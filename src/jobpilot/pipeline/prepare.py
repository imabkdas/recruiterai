"""Application preparation pipeline for JobPilot.

Implements Section 12.2 of JOBPILOT_DESIGN.md.
Ranks bullets deterministically, generates tailored summary and note via LLM,
validates with claims.py, and persists only validated output.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jobpilot.config import AppConfig, load_config
from jobpilot.db import update_status, upsert_application
from jobpilot.llm.client import GeminiClient, LLMError
from jobpilot.llm.redact import redact_text
from jobpilot.models import (
    Bullet,
    EvidenceLevel,
    JobAnalysis,
    Profile,
    ResumeBase,
)
from jobpilot.pipeline.claims import (
    NoteOutput,
    TailoredOutput,
    validate_claims,
    validate_note,
)
from jobpilot.profile.loader import load_profile, load_resume_base
from jobpilot.profile.skills import normalize_skill

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _find_project_root() -> Path:
    """Walk up from CWD to find pyproject.toml."""
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        if (parent / "pyproject.toml").exists():
            return parent
    return cwd


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass
class PrepareResult:
    """Outcome of preparing one job."""

    job_id: int
    tier: str
    summary: str
    note: str
    bullet_ids: list[str] = field(default_factory=list)
    outreach_draft: str | None = None
    used_fallback: bool = False
    validation_errors: list[str] | None = None
    resume_md_path: str | None = None
    resume_pdf_path: str | None = None
    resume_tailored: bool = False


# ---------------------------------------------------------------------------
# Bullet ranking (deterministic, no LLM)
# ---------------------------------------------------------------------------


def _is_fullstack_or_ai(title: str) -> bool:
    t = title.lower()
    return any(
        kw in t
        for kw in [
            "full stack",
            "fullstack",
            "full-stack",
            "ai",
            "ml",
            "machine learning",
            "genai",
            "llm",
            "prompt",
            "artificial intelligence",
        ]
    )


def _is_backend(title: str) -> bool:
    t = title.lower()
    if any(
        kw in t
        for kw in [
            "backend",
            "back-end",
            "back end",
            "java",
            "spring",
            "api",
            "microservice",
            "platform",
            "server",
        ]
    ):
        return True
    return not _is_fullstack_or_ai(title) and not any(
        kw in t for kw in ["frontend", "front-end", "mobile", "android", "ios"]
    )


def rank_bullets(
    resume_base: ResumeBase,
    matched_skills: list[dict[str, Any]],
    preferred_skills: list[str],
    job_title: str,
    top_n: int = 8,
    required_skills: list[str] | None = None,
) -> list[str]:
    """Rank resume bullets by overlap with job requirements.

    Professional bullets rank above project bullets.
    A project bullet is included only if:
    - it covers a required JD skill that no selected professional bullet covers, OR
    - the role is full-stack/AI.
    Project bullets are capped at 2.
    For backend roles, bullets from projects with relevance 'low_for_backend' are excluded.
    """
    # Build skill sets by evidence level
    prof_skills: set[str] = set()
    proj_skills: set[str] = set()
    exp_skills: set[str] = set()

    for ms in matched_skills:
        level = ms.get("level", "")
        norm = normalize_skill(ms.get("skill", "")).lower()
        if level == EvidenceLevel.VERIFIED_PROFESSIONAL.value:
            prof_skills.add(norm)
        elif level in (
            EvidenceLevel.VERIFIED_PROJECT.value,
            EvidenceLevel.VERIFIED_CERTIFICATION.value,
        ):
            proj_skills.add(norm)
        elif level == EvidenceLevel.EXPERIMENTAL.value:
            exp_skills.add(norm)

    pref_set = {normalize_skill(s).lower() for s in preferred_skills}
    req_set = {normalize_skill(s).lower() for s in (required_skills or [])}
    title_lower = job_title.lower()

    # 1. Score professional bullets
    scored_prof: list[tuple[str, float, Bullet]] = []
    for role in resume_base.roles:
        role_title = (role.title or "").lower()
        for b in role.bullets:
            score = _score_bullet(
                b_skills=[normalize_skill(s).lower() for s in b.skills],
                prof_skills=prof_skills,
                proj_skills=proj_skills,
                exp_skills=exp_skills,
                pref_set=pref_set,
                parent_title=role_title,
                job_title=title_lower,
            )
            scored_prof.append((b.id, score, b))
    scored_prof.sort(key=lambda x: x[1], reverse=True)

    # 2. Score project bullets (filtering out low_for_backend for backend roles)
    is_back = _is_backend(job_title)
    is_fs_ai = _is_fullstack_or_ai(job_title)

    scored_proj: list[tuple[str, float, Bullet]] = []
    for proj in resume_base.projects:
        if is_back and (proj.relevance or "").lower() == "low_for_backend":
            continue
        for b in proj.bullets:
            score = _score_bullet(
                b_skills=[normalize_skill(s).lower() for s in b.skills],
                prof_skills=prof_skills,
                proj_skills=proj_skills,
                exp_skills=exp_skills,
                pref_set=pref_set,
                parent_title="",
                job_title=title_lower,
            )
            scored_proj.append((b.id, score, b))
    scored_proj.sort(key=lambda x: x[1], reverse=True)

    # 3. Determine covered required skills from top professional bullets
    base_prof_count = min(len(scored_prof), top_n)
    selected_prof = scored_prof[:base_prof_count]

    covered_by_prof = set()
    for _, _, b in selected_prof:
        for s in b.skills:
            covered_by_prof.add(normalize_skill(s).lower())

    uncovered_req = req_set - covered_by_prof

    # 4. Select qualifying project bullets (capped at 2)
    selected_proj: list[tuple[str, float, Bullet]] = []
    for p_id, p_score, p_b in scored_proj:
        if len(selected_proj) >= 2:
            break
        p_b_skills = {normalize_skill(s).lower() for s in p_b.skills}
        if is_fs_ai:
            if p_score > 0 or len(selected_prof) < top_n:
                selected_proj.append((p_id, p_score, p_b))
        else:
            if p_b_skills & uncovered_req:
                selected_proj.append((p_id, p_score, p_b))
                uncovered_req -= p_b_skills

    # 5. Professional bullets rank above project bullets
    num_proj = len(selected_proj)
    num_prof = max(0, min(len(scored_prof), top_n - num_proj))
    final_prof = scored_prof[:num_prof]

    result_ids = [bid for bid, _, _ in final_prof] + [bid for bid, _, _ in selected_proj]
    return result_ids[:top_n]


def _score_bullet(
    *,
    b_skills: list[str],
    prof_skills: set[str],
    proj_skills: set[str],
    exp_skills: set[str],
    pref_set: set[str],
    parent_title: str,
    job_title: str,
) -> float:
    """Compute overlap score for a single bullet."""
    score = 0.0
    for s in b_skills:
        if s in prof_skills:
            score += 3
        elif s in proj_skills:
            score += 2
        elif s in exp_skills:
            score += 1
        if s in pref_set:
            score += 0.5

    # Title overlap (fuzzy)
    if parent_title and parent_title in job_title:
        score += 1

    return score


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------


def _build_summary_prompt(
    prompt_template: str,
    analysis: JobAnalysis,
    selected_bullets: list[dict[str, Any]],
    base_summary: str,
) -> str:
    """Build the tailored-summary prompt from template and data."""
    bullets_text = "\n".join(
        f"- [{b['id']}] ({b.get('type', 'professional')}): {b['text']} "
        f"[skills: {', '.join(b.get('skills', []))}] "
        f"[metrics: {', '.join(b.get('metrics', []))}]"
        for b in selected_bullets
    )

    filled = prompt_template.replace("{job_title}", analysis.normalized_title)
    filled = filled.replace(
        "{required_skills}", ", ".join(analysis.required_skills)
    )
    filled = filled.replace(
        "{preferred_skills}", ", ".join(analysis.preferred_skills)
    )
    filled = filled.replace("{seniority}", analysis.seniority)
    filled = filled.replace("{selected_bullets}", bullets_text)
    filled = filled.replace("{base_summary}", base_summary)

    return redact_text(filled)


def _build_note_prompt(
    prompt_template: str,
    analysis: JobAnalysis,
    company: str,
    selected_bullets: list[dict[str, Any]],
    positioning: str,
) -> str:
    """Build the note prompt from template and data."""
    bullets_text = "\n".join(
        f"- [{b['id']}]: {b['text']}" for b in selected_bullets
    )

    filled = prompt_template.replace("{job_title}", analysis.normalized_title)
    filled = filled.replace("{company}", company)
    filled = filled.replace(
        "{required_skills}", ", ".join(analysis.required_skills)
    )
    filled = filled.replace("{selected_bullets}", bullets_text)
    filled = filled.replace("{positioning}", positioning)

    return redact_text(filled)


# ---------------------------------------------------------------------------
# Core preparation
# ---------------------------------------------------------------------------


def prepare_job(
    conn: sqlite3.Connection,
    job_id: int,
    profile: Profile,
    resume_base: ResumeBase,
    config: AppConfig,
    llm_client: GeminiClient,
    allow_llm: bool = True,
) -> PrepareResult:
    """Prepare a short application note for a single job.

    The resume itself is edited as LaTeX in the UI and compiled there.
    This function does not call the LLM and does not write a resume file.
    `allow_llm` is accepted so existing callers keep working, and ignored.
    """
    # 1. Load job data
    job_row = conn.execute(
        "SELECT * FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    if not job_row:
        raise ValueError(f"Job {job_id} not found")
    job_data = dict(job_row)

    analysis_row = conn.execute(
        "SELECT analysis_json FROM analyses WHERE job_id = ? "
        "ORDER BY created_at DESC LIMIT 1",
        (job_id,),
    ).fetchone()
    if not analysis_row:
        raise ValueError(f"No analysis found for job {job_id}")

    analysis = JobAnalysis.model_validate_json(analysis_row["analysis_json"])

    score_row = conn.execute(
        "SELECT * FROM scores WHERE job_id = ?", (job_id,)
    ).fetchone()
    score_data = dict(score_row) if score_row else {}
    tier = score_data.get("tier") or "B"

    matched_skills_raw = score_data.get("matched_skills", "[]")
    matched_skills: list[dict[str, Any]] = json.loads(matched_skills_raw) if isinstance(matched_skills_raw, str) else matched_skills_raw

    # 2. Bullet ranking (deterministic)
    ranked_ids = rank_bullets(
        resume_base=resume_base,
        matched_skills=matched_skills,
        preferred_skills=analysis.preferred_skills,
        job_title=job_data.get("title") or "",
        required_skills=analysis.required_skills,
    )

    # Base summary fallback
    base_summary = ""
    if resume_base.summary_variants:
        base_summary = resume_base.summary_variants[0].text

    result = PrepareResult(
        job_id=job_id,
        tier=tier,
        summary=base_summary,
        note="",
        bullet_ids=ranked_ids,
        used_fallback=True,
    )

    # Fallback note if not set
    if not result.note:
        company = job_data.get("company", "your company")
        title = job_data.get("title", "the role")
        top_skills = ", ".join(
            ms.get("skill", "") for ms in matched_skills[:3] if ms.get("skill")
        )
        if top_skills:
            result.note = (
                f"I'm interested in the {title} role at {company}. "
                f"My background in {top_skills} aligns well with your requirements."
            )
        else:
            result.note = f"I'm interested in the {title} role at {company}."

    # 5. Persist. Resume paths are written by the compile endpoint, not here,
    # so a later prepare does not wipe a resume the user edited.
    upsert_application(conn, {
        "job_id": job_id,
        "tailored_summary": result.summary,
        "short_note": result.note,
        "bullet_ids": result.bullet_ids,
        "prepared_at": _now_iso(),
    })
    update_status(conn, job_id, "prepared")

    # 7. Application notes file, alongside the resume
    _write_output_file(job_id, job_data, result)

    return result


def _prepare_tier_a(
    *,
    result: PrepareResult,
    analysis: JobAnalysis,
    profile: Profile,
    resume_base: ResumeBase,
    config: AppConfig,
    llm_client: GeminiClient,
    selected_bullet_dicts: list[dict[str, Any]],
    ranked_ids: list[str],
    base_summary: str,
    job_data: dict[str, Any],
) -> PrepareResult:
    """Handle Tier A preparation with LLM and validation."""
    project_root = _find_project_root()

    # Load prompts
    summary_prompt_path = project_root / "prompts" / "tailor_summary_v1.txt"
    note_prompt_path = project_root / "prompts" / "note_v1.txt"

    summary_template = (
        summary_prompt_path.read_text()
        if summary_prompt_path.exists()
        else "Write a tailored summary. Return JSON: {\"summary\": ..., \"claims\": [...], \"bullet_ids\": [...]}"
    )
    note_template = (
        note_prompt_path.read_text()
        if note_prompt_path.exists()
        else "Write a note. Return JSON: {\"note\": ...}"
    )

    try:
        # --- Summary ---
        prompt = _build_summary_prompt(
            summary_template, analysis, selected_bullet_dicts, base_summary
        )
        output_obj = llm_client.generate_json(
            prompt, TailoredOutput, task="prepare_summary"
        )
        errors = validate_claims(
            output_obj, profile, resume_base, config, ranked_ids
        )

        if errors:
            # Retry once with errors
            retry_prompt = (
                prompt
                + "\n\nPREVIOUS ERRORS (FIX THESE):\n"
                + "\n".join(errors)
            )
            output_obj = llm_client.generate_json(
                retry_prompt, TailoredOutput, task="prepare_summary"
            )
            errors = validate_claims(
                output_obj, profile, resume_base, config, ranked_ids
            )

        if not errors:
            result.summary = output_obj.summary
            result.bullet_ids = output_obj.bullet_ids or ranked_ids
            result.used_fallback = False
        else:
            logger.warning(
                "Falling back to base summary for job %d: %s",
                result.job_id,
                errors,
            )
            result.validation_errors = errors
            result.used_fallback = True

        # --- Note ---
        note_prompt = _build_note_prompt(
            note_template,
            analysis,
            job_data.get("company", ""),
            selected_bullet_dicts,
            profile.positioning,
        )
        note_obj = llm_client.generate_json(
            note_prompt, NoteOutput, task="prepare_note"
        )
        note_errors = validate_note(
            note_obj.note, profile, resume_base, config
        )

        if note_errors:
            retry_note_prompt = (
                note_prompt
                + "\n\nPREVIOUS ERRORS:\n"
                + "\n".join(note_errors)
            )
            note_obj = llm_client.generate_json(
                retry_note_prompt, NoteOutput, task="prepare_note"
            )
            note_errors = validate_note(
                note_obj.note, profile, resume_base, config
            )

        if not note_errors:
            result.note = note_obj.note

    except LLMError as exc:
        logger.error("LLM error during prepare for job %d: %s", result.job_id, exc)
        result.used_fallback = True
        result.validation_errors = [str(exc)]
    except Exception as exc:
        logger.error("Prepare failed for job %d: %s", result.job_id, exc)
        result.used_fallback = True
        result.validation_errors = [str(exc)]

    return result


def _write_output_file(
    job_id: int,
    job_data: dict[str, Any],
    result: PrepareResult,
) -> None:
    """Write prepared material to data/out/{job_id}_{company}.md."""
    project_root = _find_project_root()
    out_dir = project_root / "data" / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    company_slug = (
        (job_data.get("company") or "unknown")
        .lower()
        .replace(" ", "_")
        .replace("/", "_")
    )
    out_file = out_dir / f"{job_id}_{company_slug}.md"

    content = (
        f"# Application: {job_data.get('title')} at {job_data.get('company')}\n\n"
        f"## Summary\n{result.summary}\n\n"
        f"## Note\n{result.note}\n\n"
        f"## Selected Bullets\n"
        + "\n".join(f"- {bid}" for bid in result.bullet_ids)
        + "\n"
    )
    if result.used_fallback:
        content += "\n> ⚠️ Used fallback (base summary).\n"

    out_file.write_text(content)


# ---------------------------------------------------------------------------
# Pipeline entry point
# ---------------------------------------------------------------------------


def run_prepare(
    conn: sqlite3.Connection,
    job_ids: list[int] | None = None,
    auto_top: int | None = None,
    config: AppConfig | None = None,
    profile: Profile | None = None,
    resume_base: ResumeBase | None = None,
    llm_client: GeminiClient | None = None,
) -> list[PrepareResult]:
    """Prepare one or more jobs.

    If auto_top is set, pick the top N scored/queued jobs by score.
    """
    cfg = config or load_config()
    prof = profile or load_profile()
    rb = resume_base or load_resume_base()

    if llm_client is None:
        llm_client = GeminiClient(config=cfg, conn=conn)

    if auto_top is not None:
        rows = conn.execute(
            "SELECT j.id "
            "FROM jobs j "
            "JOIN scores s ON j.id = s.job_id "
            "WHERE j.status IN ('scored', 'queued') "
            "ORDER BY s.total DESC "
            "LIMIT ?",
            (auto_top,),
        ).fetchall()
        job_ids = [r["id"] for r in rows]

    results: list[PrepareResult] = []
    if job_ids:
        for jid in job_ids:
            try:
                res = prepare_job(conn, jid, prof, rb, cfg, llm_client, allow_llm=False)
                results.append(res)
            except Exception as exc:
                logger.error("Failed to prepare job %d: %s", jid, exc)
                results.append(
                    PrepareResult(
                        job_id=jid,
                        tier="?",
                        summary="",
                        note="",
                        used_fallback=True,
                        validation_errors=[str(exc)],
                    )
                )

    return results
