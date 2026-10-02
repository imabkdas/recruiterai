"""Service layer for JobPilot.

Exposes typed, pure Python functions returning Pydantic models for all JobPilot
features without terminal printing or sys.exit calls.
Serves as the single API layer for both the Typer CLI and future UI (e.g. Streamlit).
"""

from __future__ import annotations

import dataclasses
import json
import re
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

from jobpilot import db
from jobpilot.config import AppConfig, load_config
from jobpilot.llm.client import GeminiClient
from jobpilot.models import (
    EvidenceLevel,
    JobAnalysis,
    Profile,
    ResumeBase,
)
from jobpilot.notify import Notifier
from jobpilot.pipeline.analyze import get_unmatched_skills_with_counts
from jobpilot.pipeline.answer import (
    AnswerResult,
)
from jobpilot.pipeline.answer import (
    answer_question as core_answer_question,
)
from jobpilot.pipeline.answer import (
    approve_answer as core_approve_answer,
)
from jobpilot.pipeline.answer import (
    set_answer as core_set_answer,
)
from jobpilot.pipeline.daily import execute_daily_pipeline
from jobpilot.pipeline.jd_text import clean_job_description
from jobpilot.pipeline.labels import listing_labels
from jobpilot.pipeline.prepare import PrepareResult
from jobpilot.pipeline.prepare import prepare_job as core_prepare_job
from jobpilot.pipeline.queue import NeedsJdItem, QueueItem, build_queue
from jobpilot.pipeline.resume import (
    base_resume_tex_path,
    compile_resume_source,
    read_resume_source,
    resume_tex_path,
)
from jobpilot.profile.loader import (
    load_profile,
    load_resume_base,
    validate_profile_and_resume,
)
from jobpilot.profile.skills import normalize_skill
from jobpilot.sources.manual import add_jd_to_job
from jobpilot.tracking.stats import FullStatsReport, get_stats_report
from jobpilot.tracking.status import mark_job_status

NeedsJDItem = NeedsJdItem

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class SkillEvidenceItem(BaseModel):
    skill: str
    level: str
    evidence_ids: list[str] = Field(default_factory=list)
    mentions: int = 0


class ScoreDetail(BaseModel):
    total: float
    tier: str
    skills_score: float | None = None
    experience_score: float | None = None
    seniority_score: float | None = None
    location_score: float | None = None
    extras_score: float | None = None
    covered_skills: list[SkillEvidenceItem] = Field(default_factory=list)
    partial_skills: list[SkillEvidenceItem] = Field(default_factory=list)
    learning_only_skills: list[SkillEvidenceItem] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    scored_at: str


class ApplicationDraft(BaseModel):
    id: int | None = None
    job_id: int
    tailored_summary: str | None = None
    short_note: str | None = None
    outreach_draft: str | None = None
    bullet_ids: list[str] = Field(default_factory=list)
    prepared_at: str | None = None
    applied_at: str | None = None
    channel: str | None = None
    referral_contact: str | None = None
    notes: str | None = None
    resume_md_path: str | None = None
    resume_pdf_path: str | None = None


class JobDetail(BaseModel):
    id: int
    fingerprint: str
    source: str
    company: str
    title: str
    location: str | None = None
    remote_type: str | None = None
    url: str
    apply_url: str | None = None
    description: str | None = None
    snippet: str | None = None
    posted_at: str | None = None
    discovered_at: str
    last_seen_at: str
    status: str
    status_reason: str | None = None
    analysis: JobAnalysis | None = None
    score: ScoreDetail | None = None
    application: ApplicationDraft | None = None


class JobStatusUpdate(BaseModel):
    job_id: int
    previous_status: str
    new_status: str
    channel: str | None = None
    note: str | None = None
    referral_contact: str | None = None
    timestamp: str


class AddJDResult(BaseModel):
    job_id: int
    status: str
    char_count: int


APPLICATION_TRACKER_STATUSES = (
    "new",
    "applied",
    "replied",
    "interview",
    "offer",
    "rejected",
)

# Pipeline states that mean the user has not applied yet.
_UNAPPLIED_STATUSES = frozenset(
    {
        "new",
        "queued",
        "prepared",
        "scored",
        "analyzed",
        "needs_jd",
        "analysis_failed",
    }
)


def tracker_status(status: str) -> str:
    """Status shown on the Applications page. Unapplied jobs read as new."""
    if status in _UNAPPLIED_STATUSES:
        return "new"
    return status


class ApplicationItem(BaseModel):
    job_id: int
    company: str
    title: str
    status: str
    url: str = ""
    location: str | None = None
    posted_at: str | None = None
    has_description: bool = False
    applied_at: str | None = None
    channel: str | None = None
    notes: str | None = None
    referral_contact: str | None = None
    prepared_at: str | None = None
    tailored_summary: str | None = None
    short_note: str | None = None
    resume_md_path: str | None = None
    resume_pdf_path: str | None = None


class JobListItem(BaseModel):
    """One searched job for the All jobs screen."""

    job_id: int
    company: str
    title: str
    status: str
    url: str = ""
    location: str | None = None
    posted_at: str | None = None


class ResumeSource(BaseModel):
    source: str
    pdf_url: str | None = None


class CompileResumeResult(BaseModel):
    pdf_url: str


class FollowUpItem(BaseModel):
    job_id: int
    company: str
    title: str
    status: str
    applied_at: str
    days_since_applied: int
    channel: str | None = None
    referral_contact: str | None = None
    notes: str | None = None


class AnswerBankItem(BaseModel):
    id: int
    question_norm: str
    category: str
    answer: str
    approved: bool
    source_job_id: int | None = None
    created_at: str | None = None
    updated_at: str | None = None


def _safe_serialize_summary(val: Any, seen: set[int] | None = None) -> Any:
    """Recursively convert custom objects/summaries into JSON-serializable primitives."""
    if seen is None:
        seen = set()
    if val is None or isinstance(val, (int, float, str, bool)):
        return val
    obj_id = id(val)
    if obj_id in seen:
        return str(val)
    seen.add(obj_id)
    if isinstance(val, (list, tuple, set)):
        return [_safe_serialize_summary(x, seen) for x in val]
    if isinstance(val, dict):
        return {str(k): _safe_serialize_summary(v, seen) for k, v in val.items()}
    if hasattr(val, "model_dump"):
        return val.model_dump()
    if dataclasses.is_dataclass(val):
        return dataclasses.asdict(val)
    if hasattr(val, "__dict__"):
        return {
            str(k): _safe_serialize_summary(v, seen)
            for k, v in val.__dict__.items()
            if not str(k).startswith("_")
        }
    return str(val)


class StageSummary(BaseModel):
    name: str
    status: str  # ok | error
    error: str | None = None
    summary: Any = None

    @field_validator("summary", mode="before")
    @classmethod
    def _val_summary(cls, v: Any) -> Any:
        return _safe_serialize_summary(v)



class RunSummary(BaseModel):
    stages: list[StageSummary] = Field(default_factory=list)
    new_jobs: int = 0
    queued_count: int = 0
    tier_a_count: int = 0
    needs_jd_count: int = 0
    prepared_count: int = 0
    follow_ups_due_count: int = 0
    has_failures: bool = False


class ProfileReport(BaseModel):
    validation_errors: list[str] = Field(default_factory=list)
    validation_warnings: list[str] = Field(default_factory=list)
    unset_settings: list[str] = Field(default_factory=list)
    unmatched_skills: list[tuple[str, int]] = Field(default_factory=list)


class ProgressSummary(BaseModel):
    applied_today: int
    daily_size: int
    applied_this_week: int
    weekly_target: int
    follow_ups_due_count: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_project_root() -> Path:
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        if (parent / "pyproject.toml").exists():
            return parent
    return cwd


def _get_db_conn(conn: sqlite3.Connection | None = None) -> tuple[sqlite3.Connection, bool]:
    """Return an active connection and a boolean indicating if it must be closed."""
    if conn is not None:
        return conn, False
    root = _find_project_root()
    db_path = root / "data" / "jobpilot.db"
    return db.get_connection(db_path), True


# ---------------------------------------------------------------------------
# Service Functions
# ---------------------------------------------------------------------------


def get_queue(
    size: int | None = None,
    conn: sqlite3.Connection | None = None,
    config: AppConfig | None = None,
    write_csv: bool = False,
) -> list[QueueItem]:
    """Return today's ranked queue items."""
    c, close = _get_db_conn(conn)
    try:
        cfg = config or load_config()
        result = build_queue(c, config=cfg, size=size, write_csv=write_csv)
        return result.items
    finally:
        if close:
            c.close()


def get_needs_jd(
    conn: sqlite3.Connection | None = None,
    config: AppConfig | None = None,
) -> list[NeedsJDItem]:
    """Return jobs awaiting full JD paste."""
    c, close = _get_db_conn(conn)
    try:
        cfg = config or load_config()
        result = build_queue(c, config=cfg, write_csv=False)
        return result.needs_jd
    finally:
        if close:
            c.close()


_TAG_SKIP = {
    "dev", "engineer", "developer", "digital nomad", "remote", "senior", "junior",
    "software", "hiring", "job", "full time", "contract", "intern", "manager",
    "backend", "frontend", "fullstack", "full stack", "full-stack", "tech",
}


def _mention_count(description: str, skill: str) -> int:
    if not description or not skill:
        return 0
    return len(re.findall(rf"\b{re.escape(skill)}\b", description, flags=re.IGNORECASE))


def _derive_skill_lists(
    description: str,
    tags: list[str],
    analysis: JobAnalysis | None,
    profile: Profile,
) -> tuple[list[SkillEvidenceItem], list[SkillEvidenceItem], list[SkillEvidenceItem], list[str]]:
    """Build the four skill groups when the scorer stored none.

    Uses the extracted requirements when the analysis has them, otherwise the
    posting's tags and any profile skill that actually appears in the text.
    """
    names: list[str] = []
    if analysis and (analysis.required_skills or analysis.preferred_skills):
        names.extend(analysis.required_skills)
        names.extend(analysis.preferred_skills)
    else:
        for tag in tags:
            label = str(tag).strip()
            if not label or label.lower() in _TAG_SKIP:
                continue
            names.append(normalize_skill(label))
        lowered = description.lower()
        for skill in profile.skills:
            if re.search(rf"\b{re.escape(skill.name)}\b", lowered, flags=re.IGNORECASE):
                names.append(skill.name)

    by_name = {s.name.lower(): s for s in profile.skills}
    covered: list[SkillEvidenceItem] = []
    partial: list[SkillEvidenceItem] = []
    learning: list[SkillEvidenceItem] = []
    missing: list[str] = []
    seen: set[str] = set()
    for raw_name in names:
        canonical = normalize_skill(raw_name)
        key = canonical.lower()
        if not key or key in seen or key in _TAG_SKIP:
            continue
        seen.add(key)
        mentions = _mention_count(description, canonical)
        skill = by_name.get(key)
        if skill is None:
            missing.append(canonical)
            continue
        item = SkillEvidenceItem(
            skill=skill.name,
            level=skill.level.value,
            evidence_ids=list(skill.evidence or []),
            mentions=mentions,
        )
        level = skill.level
        if level == EvidenceLevel.VERIFIED_PROFESSIONAL:
            covered.append(item)
        elif level in (
            EvidenceLevel.VERIFIED_PROJECT,
            EvidenceLevel.VERIFIED_CERTIFICATION,
            EvidenceLevel.EXPERIMENTAL,
        ):
            partial.append(item)
        elif level == EvidenceLevel.LEARNING:
            learning.append(item)
        else:
            missing.append(skill.name)
    return covered, partial, learning, missing


def get_job_detail(
    job_id: int,
    conn: sqlite3.Connection | None = None,
) -> JobDetail:
    """Fetch complete details for a job including analysis, score breakdown, and application drafts."""
    c, close = _get_db_conn(conn)
    try:
        job_row = c.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not job_row:
            raise KeyError(f"Job {job_id} not found.")

        # Analysis
        analysis_obj: JobAnalysis | None = None
        a_row = c.execute(
            "SELECT analysis_json FROM analyses WHERE job_id = ? ORDER BY created_at DESC LIMIT 1",
            (job_id,),
        ).fetchone()
        if a_row:
            analysis_obj = JobAnalysis.model_validate_json(a_row["analysis_json"])

        # Score
        score_detail: ScoreDetail | None = None
        s_row = c.execute("SELECT * FROM scores WHERE job_id = ?", (job_id,)).fetchone()
        if s_row:
            matched_raw = json.loads(s_row["matched_skills"] or "[]")
            missing_req = json.loads(s_row["missing_required"] or "[]")
            missing_pref = json.loads(s_row["missing_preferred"] or "[]")
            flags = json.loads(s_row["flags"] or "[]")

            covered: list[SkillEvidenceItem] = []
            partial: list[SkillEvidenceItem] = []
            learning_only: list[SkillEvidenceItem] = []

            for ms in matched_raw:
                lvl = ms.get("level", "")
                item = SkillEvidenceItem(
                    skill=ms.get("skill", ""),
                    level=lvl,
                    evidence_ids=ms.get("evidence_ids", []),
                )
                if lvl == EvidenceLevel.VERIFIED_PROFESSIONAL.value:
                    covered.append(item)
                elif lvl in (
                    EvidenceLevel.VERIFIED_PROJECT.value,
                    EvidenceLevel.VERIFIED_CERTIFICATION.value,
                    EvidenceLevel.EXPERIMENTAL.value,
                ):
                    partial.append(item)
                elif lvl == EvidenceLevel.LEARNING.value:
                    learning_only.append(item)

            missing = list(dict.fromkeys(missing_req + missing_pref))

            score_detail = ScoreDetail(
                total=s_row["total"],
                tier=s_row["tier"] or "B",
                skills_score=s_row["skills_score"],
                experience_score=s_row["experience_score"],
                seniority_score=s_row["seniority_score"],
                location_score=s_row["location_score"],
                extras_score=s_row["extras_score"],
                covered_skills=covered,
                partial_skills=partial,
                learning_only_skills=learning_only,
                missing_skills=missing,
                flags=flags,
                scored_at=s_row["scored_at"],
            )

        description = clean_job_description(job_row["description"])
        if score_detail and not (
            score_detail.covered_skills
            or score_detail.partial_skills
            or score_detail.learning_only_skills
            or score_detail.missing_skills
        ):
            tags: list[str] = []
            if job_row["raw_json"]:
                try:
                    tags = list(json.loads(job_row["raw_json"]).get("tags") or [])
                except (TypeError, json.JSONDecodeError):
                    tags = []
            try:
                profile = load_profile()
            except Exception:
                profile = None
            if profile is not None:
                covered, partial, learning, missing = _derive_skill_lists(
                    description, tags, analysis_obj, profile
                )
                score_detail.covered_skills = covered
                score_detail.partial_skills = partial
                score_detail.learning_only_skills = learning
                score_detail.missing_skills = missing

        # Application
        app_draft: ApplicationDraft | None = None
        app_row = c.execute("SELECT * FROM applications WHERE job_id = ?", (job_id,)).fetchone()
        if app_row:
            b_ids = json.loads(app_row["bullet_ids"] or "[]") if app_row["bullet_ids"] else []
            app_draft = ApplicationDraft(
                id=app_row["id"],
                job_id=job_id,
                tailored_summary=app_row["tailored_summary"],
                short_note=app_row["short_note"],
                outreach_draft=app_row["outreach_draft"],
                bullet_ids=b_ids,
                prepared_at=app_row["prepared_at"],
                applied_at=app_row["applied_at"],
                channel=app_row["channel"],
                referral_contact=app_row["referral_contact"],
                notes=app_row["notes"],
                resume_md_path=app_row["resume_md_path"],
                resume_pdf_path=app_row["resume_pdf_path"],
            )

        return JobDetail(
            id=job_row["id"],
            fingerprint=job_row["fingerprint"],
            source=job_row["source"],
            company=job_row["company"],
            title=job_row["title"],
            location=job_row["location"],
            remote_type=job_row["remote_type"],
            url=job_row["url"],
            apply_url=job_row["apply_url"],
            description=description,
            snippet=job_row["snippet"],
            posted_at=job_row["posted_at"],
            discovered_at=job_row["discovered_at"],
            last_seen_at=job_row["last_seen_at"],
            status=job_row["status"],
            status_reason=job_row["status_reason"],
            analysis=analysis_obj,
            score=score_detail,
            application=app_draft,
        )
    finally:
        if close:
            c.close()


def prepare_job(
    job_id: int,
    conn: sqlite3.Connection | None = None,
    config: AppConfig | None = None,
    profile: Profile | None = None,
    resume_base: ResumeBase | None = None,
    llm_client: GeminiClient | None = None,
) -> PrepareResult:
    """Prepare application materials for a single job."""
    c, close = _get_db_conn(conn)
    try:
        cfg = config or load_config()
        prof = profile or load_profile()
        res_base = resume_base or load_resume_base()
        client = llm_client or GeminiClient(config=cfg, conn=c)
        return core_prepare_job(c, job_id, prof, res_base, cfg, client)
    finally:
        if close:
            c.close()


def _job_resume_tex(conn: sqlite3.Connection, job_id: int) -> Path:
    """Return the LaTeX path for a job, preferring one already saved."""
    job = conn.execute(
        "SELECT id, company, title FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    if job is None:
        raise KeyError(f"Job {job_id} not found.")
    app = conn.execute(
        "SELECT resume_pdf_path FROM applications WHERE job_id = ?", (job_id,)
    ).fetchone()
    if app and app["resume_pdf_path"]:
        existing = Path(app["resume_pdf_path"]).with_suffix(".tex")
        if existing.is_file():
            return existing
    return resume_tex_path(job["id"], job["company"] or "", job["title"] or "")


def get_base_resume_source() -> ResumeSource:
    """Return the sidebar resume editor source. No job is involved."""
    tex_path = base_resume_tex_path()
    pdf_path = tex_path.with_suffix(".pdf")
    return ResumeSource(
        source=read_resume_source(tex_path if tex_path.is_file() else None),
        pdf_url="/api/resume.pdf" if pdf_path.is_file() else None,
    )


def compile_base_resume(source: str) -> CompileResumeResult:
    """Compile the sidebar resume and store the PDF beside the template."""
    tex_path = base_resume_tex_path()
    compile_resume_source(source, tex_path)
    return CompileResumeResult(pdf_url="/api/resume.pdf")


def base_resume_pdf_path() -> Path:
    """Return the compiled base resume PDF."""
    pdf_path = base_resume_tex_path().with_suffix(".pdf")
    if not pdf_path.is_file():
        raise KeyError("Resume PDF has not been compiled yet.")
    return pdf_path


def get_resume_source(
    job_id: int,
    conn: sqlite3.Connection | None = None,
) -> ResumeSource:
    """Return the LaTeX the editor should show for this job."""
    c, close = _get_db_conn(conn)
    try:
        tex_path = _job_resume_tex(c, job_id)
        pdf_path = tex_path.with_suffix(".pdf")
        return ResumeSource(
            source=read_resume_source(tex_path if tex_path.is_file() else None),
            pdf_url=f"/api/jobs/{job_id}/resume.pdf" if pdf_path.is_file() else None,
        )
    finally:
        if close:
            c.close()


def compile_job_resume(
    job_id: int,
    source: str,
    conn: sqlite3.Connection | None = None,
) -> CompileResumeResult:
    """Compile edited LaTeX and store the PDF on the job's application row."""
    c, close = _get_db_conn(conn)
    try:
        tex_path = _job_resume_tex(c, job_id)
        pdf_path = compile_resume_source(source, tex_path)
        db.upsert_application(c, {
            "job_id": job_id,
            "resume_pdf_path": str(pdf_path),
            "resume_md_path": str(tex_path),
        })
        return CompileResumeResult(pdf_url=f"/api/jobs/{job_id}/resume.pdf")
    finally:
        if close:
            c.close()


def job_resume_pdf_path(
    job_id: int,
    conn: sqlite3.Connection | None = None,
) -> Path:
    """Return the compiled PDF path. Raises KeyError when it does not exist."""
    c, close = _get_db_conn(conn)
    try:
        pdf_path = _job_resume_tex(c, job_id).with_suffix(".pdf")
        if not pdf_path.is_file():
            raise KeyError("Resume PDF has not been compiled yet.")
        return pdf_path
    finally:
        if close:
            c.close()


def mark_job(
    job_id: int,
    status: str,
    channel: str | None = None,
    note: str | None = None,
    referral_contact: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> JobStatusUpdate:
    """Transition a job's status and record application metadata."""
    c, close = _get_db_conn(conn)
    try:
        row = c.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            raise KeyError(f"Job {job_id} not found.")
        prev_status = row["status"]

        mark_job_status(
            c,
            job_id,
            status,
            channel=channel,
            note=note,
            referral_contact=referral_contact,
        )
        return JobStatusUpdate(
            job_id=job_id,
            previous_status=prev_status,
            new_status=status,
            channel=channel,
            note=note,
            referral_contact=referral_contact,
            timestamp=datetime.now(UTC).isoformat(),
        )
    finally:
        if close:
            c.close()


def add_jd(
    job_id: int | str,
    text: str,
    title: str | None = None,
    company: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> AddJDResult:
    """Attach full job description text to a job."""
    c, close = _get_db_conn(conn)
    try:
        updated_id = add_jd_to_job(c, str(job_id), text, title=title, company=company)
        status_row = c.execute("SELECT status FROM jobs WHERE id = ?", (updated_id,)).fetchone()
        return AddJDResult(
            job_id=updated_id,
            status=status_row["status"] if status_row else "new",
            char_count=len(text.strip()),
        )
    finally:
        if close:
            c.close()


def skip_job(
    job_id: int,
    reason: str = "user_skipped",
    conn: sqlite3.Connection | None = None,
) -> JobStatusUpdate:
    """Mark a job as skipped."""
    return mark_job(job_id, status="skipped", note=reason, conn=conn)


_JOB_SELECT = (
    "SELECT j.id AS job_id, j.company, j.title, j.status, j.url, j.location, "
    "j.posted_at, j.discovered_at, substr(j.description, 1, 400) AS description, "
    "CASE WHEN j.description IS NOT NULL AND length(trim(j.description)) > 0 "
    "THEN 1 ELSE 0 END AS has_description, "
    "a.applied_at, a.channel, a.notes, a.referral_contact, a.prepared_at, "
    "a.tailored_summary, a.short_note, a.resume_md_path, a.resume_pdf_path "
    "FROM jobs j LEFT JOIN applications a ON a.job_id = j.id "
)


def list_applications(
    status: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> list[ApplicationItem]:
    """List every searched job, optionally filtered by tracker status."""
    c, close = _get_db_conn(conn)
    try:
        query = _JOB_SELECT
        params: list[Any] = []
        if status == "new":
            placeholders = ", ".join("?" for _ in _UNAPPLIED_STATUSES)
            query += f"WHERE j.status IN ({placeholders}) "
            params.extend(sorted(_UNAPPLIED_STATUSES))
        elif status:
            query += "WHERE j.status = ? "
            params.append(status)
        query += "ORDER BY COALESCE(j.posted_at, j.discovered_at) DESC, j.id DESC"

        rows = c.execute(query, params).fetchall()
        return [_application_item(r) for r in rows]
    finally:
        if close:
            c.close()


def list_searched_jobs(
    *,
    location: str | None = None,
    status: str | None = None,
    posted_from: str | None = None,
    posted_to: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> list[JobListItem]:
    """List searched jobs for the archive screen, with optional filters.

    Location matches the text shown in the Location column, including a city
    parsed out of a LinkedIn title. A typed city or country matches when that
    column contains it.
    """
    c, close = _get_db_conn(conn)
    try:
        clauses: list[str] = []
        params: list[Any] = []
        needle = location.strip().lower() if location and location.strip() else ""
        if status and status.strip():
            clauses.append("j.status = ?")
            params.append(status.strip())
        if posted_from:
            clauses.append("substr(COALESCE(j.posted_at, j.discovered_at), 1, 10) >= ?")
            params.append(_date_only(posted_from))
        if posted_to:
            clauses.append("substr(COALESCE(j.posted_at, j.discovered_at), 1, 10) <= ?")
            params.append(_date_only(posted_to))

        query = _JOB_SELECT
        if clauses:
            query += "WHERE " + " AND ".join(clauses) + " "
        query += "ORDER BY COALESCE(j.posted_at, j.discovered_at) DESC, j.id DESC"
        rows = c.execute(query, params).fetchall()
        items = [_job_list_item(r) for r in rows]
        if needle:
            items = [item for item in items if needle in (item.location or "").lower()]
        return items
    finally:
        if close:
            c.close()


def _application_item(row: sqlite3.Row) -> ApplicationItem:
    company, title, location = listing_labels(
        row["company"] or "",
        row["title"] or "",
        row["location"],
        row["description"],
    )
    return ApplicationItem(
        job_id=row["job_id"],
        company=company,
        title=title,
        status=tracker_status(row["status"]),
        url=row["url"] or "",
        location=location,
        posted_at=row["posted_at"],
        has_description=bool(row["has_description"]),
        applied_at=row["applied_at"],
        channel=row["channel"],
        notes=row["notes"],
        referral_contact=row["referral_contact"],
        prepared_at=row["prepared_at"],
        tailored_summary=row["tailored_summary"],
        short_note=row["short_note"],
        resume_md_path=row["resume_md_path"],
        resume_pdf_path=row["resume_pdf_path"],
    )


def _job_list_item(row: sqlite3.Row) -> JobListItem:
    company, title, location = listing_labels(
        row["company"] or "",
        row["title"] or "",
        row["location"],
        row["description"],
    )
    return JobListItem(
        job_id=row["job_id"],
        company=company,
        title=title,
        status=row["status"],
        url=row["url"] or "",
        location=location,
        posted_at=row["posted_at"] or row["discovered_at"],
    )


_DATE_ONLY = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def _date_only(value: str) -> str:
    match = _DATE_ONLY.match(value.strip())
    if not match:
        raise ValueError("Applied date must be YYYY-MM-DD.")
    try:
        datetime.strptime(match.group(1), "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("Applied date must be YYYY-MM-DD.") from exc
    return match.group(1)


def _http_url(value: str) -> str:
    cleaned = value.strip()
    parsed = urlparse(cleaned)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Job link must be an http or https URL.")
    return cleaned


def update_application(
    job_id: int,
    *,
    status: str | None = None,
    notes: str | None = None,
    applied_at: str | None = None,
    url: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> ApplicationItem:
    """Update tracker fields for one application.

    Status changes on this page are the user's record of where the application
    stands. They do not stamp an applied date; that date is set on its own.
    """
    if status is None and notes is None and applied_at is None and url is None:
        raise ValueError("Nothing to update.")

    c, close = _get_db_conn(conn)
    try:
        job = c.execute("SELECT id FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not job:
            raise KeyError(f"Job {job_id} not found.")
        if status is not None and status not in APPLICATION_TRACKER_STATUSES:
            allowed = ", ".join(APPLICATION_TRACKER_STATUSES)
            raise ValueError(f"Status must be one of: {allowed}.")
        cleaned_url = _http_url(url) if url is not None else None
        cleaned_date = _date_only(applied_at) if applied_at is not None else None

        if status is not None:
            db.update_status(c, job_id, status)

        if cleaned_url is not None:
            c.execute(
                "UPDATE jobs SET url = ?, apply_url = ? WHERE id = ?",
                (cleaned_url, cleaned_url, job_id),
            )
            c.commit()

        app_fields: dict[str, Any] = {}
        if cleaned_date is not None:
            app_fields["applied_at"] = cleaned_date
        if notes is not None and notes.strip():
            app_fields["notes"] = notes.strip()
        if status is not None or app_fields or notes is not None:
            app_fields["job_id"] = job_id
            db.upsert_application(c, app_fields)
        if notes is not None and not notes.strip():
            c.execute("UPDATE applications SET notes = NULL WHERE job_id = ?", (job_id,))
            c.commit()

        row = c.execute(_JOB_SELECT + "WHERE j.id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(f"Job {job_id} not found.")
        return _application_item(row)
    finally:
        if close:
            c.close()


def follow_ups_due(
    days: int | None = None,
    conn: sqlite3.Connection | None = None,
    config: AppConfig | None = None,
) -> list[FollowUpItem]:
    """List applied jobs where applied_at is older than days with no response."""
    c, close = _get_db_conn(conn)
    try:
        cfg = config or load_config()
        follow_up_days = days if days is not None else cfg.notifications.follow_up_days
        now = datetime.now(UTC)
        cutoff = (now - timedelta(days=follow_up_days)).isoformat()

        query = (
            "SELECT a.job_id, j.company, j.title, j.status, a.applied_at, "
            "a.channel, a.referral_contact, a.notes "
            "FROM applications a JOIN jobs j ON a.job_id = j.id "
            "WHERE j.status = 'applied' AND a.applied_at IS NOT NULL AND a.applied_at <= ? "
            "ORDER BY a.applied_at ASC"
        )
        rows = c.execute(query, (cutoff,)).fetchall()

        items: list[FollowUpItem] = []
        for r in rows:
            app_at = r["applied_at"]
            try:
                app_dt = datetime.fromisoformat(app_at)
                if app_dt.tzinfo is None:
                    app_dt = app_dt.replace(tzinfo=UTC)
                delta_days = (now - app_dt).days
            except Exception:
                delta_days = follow_up_days

            items.append(
                FollowUpItem(
                    job_id=r["job_id"],
                    company=r["company"],
                    title=r["title"],
                    status=r["status"],
                    applied_at=app_at,
                    days_since_applied=delta_days,
                    channel=r["channel"],
                    referral_contact=r["referral_contact"],
                    notes=r["notes"],
                )
            )
        return items
    finally:
        if close:
            c.close()


def answer_question(
    question: str,
    job_id: int | None = None,
    conn: sqlite3.Connection | None = None,
    config: AppConfig | None = None,
    profile: Profile | None = None,
) -> AnswerResult:
    """Answer an application question using profile facts, settings, or LLM drafts."""
    c, close = _get_db_conn(conn)
    try:
        cfg = config or load_config()
        prof = profile or load_profile()
        client = GeminiClient(config=cfg, conn=c)
        return core_answer_question(
            question=question,
            profile=prof,
            config=cfg,
            conn=c,
            llm_client=client,
            job_id=job_id,
        )
    finally:
        if close:
            c.close()


def list_answers(conn: sqlite3.Connection | None = None) -> list[AnswerBankItem]:
    """List all questions and approved/draft answers in the answer bank."""
    c, close = _get_db_conn(conn)
    try:
        rows = c.execute(
            "SELECT id, question_norm, category, answer, approved, source_job_id, created_at, updated_at "
            "FROM answer_bank ORDER BY id ASC"
        ).fetchall()
        return [
            AnswerBankItem(
                id=r["id"],
                question_norm=r["question_norm"],
                category=r["category"],
                answer=r["answer"],
                approved=bool(r["approved"]),
                source_job_id=r["source_job_id"],
                created_at=r["created_at"],
                updated_at=r["updated_at"],
            )
            for r in rows
        ]
    finally:
        if close:
            c.close()


def set_answer(
    answer_id: int,
    text: str,
    conn: sqlite3.Connection | None = None,
) -> bool:
    """Set an answer directly on an answer bank item and mark as approved."""
    c, close = _get_db_conn(conn)
    try:
        return core_set_answer(c, answer_id, text)
    finally:
        if close:
            c.close()


def approve_answer(
    answer_id: int,
    conn: sqlite3.Connection | None = None,
) -> bool:
    """Mark an answer bank entry as approved."""
    c, close = _get_db_conn(conn)
    try:
        return core_approve_answer(c, answer_id)
    finally:
        if close:
            c.close()


def get_stats(
    weeks: int = 4,
    conn: sqlite3.Connection | None = None,
    config: AppConfig | None = None,
) -> FullStatsReport:
    """Compute application funnel and conversion statistics."""
    c, close = _get_db_conn(conn)
    try:
        cfg = config or load_config()
        return get_stats_report(c, weeks=weeks, weekly_target=cfg.queue.weekly_target)
    finally:
        if close:
            c.close()


def run_daily(
    progress_callback: Callable[[str, str], None] | None = None,
    auto_top: int | None = None,
    conn: sqlite3.Connection | None = None,
    config: AppConfig | None = None,
    profile: Profile | None = None,
    notify: bool = True,
    pipeline_fn: Callable[..., Any] = execute_daily_pipeline,
) -> RunSummary:
    """Execute the full daily pipeline and trigger notifications."""
    c, close = _get_db_conn(conn)
    try:
        cfg = config or load_config()
        prof = profile
        if prof is None:
            try:
                prof = load_profile()
            except Exception:
                prof = None
        top_n = auto_top if auto_top is not None else cfg.queue.daily_size

        if progress_callback:
            progress_callback("start", "Starting daily pipeline execution...")

        # Count new jobs before run
        row_before = c.execute("SELECT COUNT(*) FROM jobs").fetchone()
        count_before = row_before[0] if row_before else 0

        # Execute daily pipeline
        pipeline_summary = pipeline_fn(c, config=cfg, profile=prof, prepare_top=top_n)

        # Count new jobs after run
        row_after = c.execute("SELECT COUNT(*) FROM jobs").fetchone()
        count_after = row_after[0] if row_after else 0
        new_jobs = max(0, count_after - count_before)

        # Queue counts
        try:
            queue_items = get_queue(conn=c, config=cfg)
            queued_count = len(queue_items)
            tier_a_count = sum(1 for it in queue_items if it.tier == "A")
            needs_jd_count = len(get_needs_jd(conn=c, config=cfg))
        except Exception:
            queue_items = []
            queued_count = 0
            tier_a_count = 0
            needs_jd_count = 0

        # Prepared count
        prep_stage = next((s for s in pipeline_summary.stages if s.name == "prepare"), None)
        prepared_count = len(prep_stage.summary) if prep_stage and isinstance(prep_stage.summary, list) else 0

        # Follow-ups due count
        try:
            follow_up_items = follow_ups_due(conn=c, config=cfg)
            follow_ups_due_count = len(follow_up_items)
        except Exception:
            follow_up_items = []
            follow_ups_due_count = 0

        # Stage failures
        stage_summaries: list[StageSummary] = []
        failures: list[tuple[str, str]] = []
        for s in pipeline_summary.stages:
            stage_summaries.append(
                StageSummary(
                    name=s.name,
                    status=s.status,
                    error=s.error,
                    summary=s.summary,
                )
            )
            if s.status == "error":
                failures.append((s.name, s.error or "Unknown failure"))

        summary = RunSummary(
            stages=stage_summaries,
            new_jobs=new_jobs,
            queued_count=queued_count,
            tier_a_count=tier_a_count,
            needs_jd_count=needs_jd_count,
            prepared_count=prepared_count,
            follow_ups_due_count=follow_ups_due_count,
            has_failures=pipeline_summary.has_failures,
        )

        if notify:
            notifier = Notifier(config=cfg)
            notifier.send_run_daily_summary(
                new_jobs=new_jobs,
                queued_count=queued_count,
                tier_a_count=tier_a_count,
                needs_jd_count=needs_jd_count,
                stage_failures=failures if failures else None,
            )
            if follow_ups_due_count > 0:
                notifier.send_follow_ups_due(follow_ups_due_count)

        if progress_callback:
            progress_callback("complete", "Daily pipeline completed.")

        return summary
    finally:
        if close:
            c.close()


def profile_report(conn: sqlite3.Connection | None = None) -> ProfileReport:
    """Generate a validation and gap analysis report for profile.yaml and resume_base.yaml."""
    c, close = _get_db_conn(conn)
    try:
        try:
            prof = load_profile()
        except Exception:
            prof = None
        try:
            res_base = load_resume_base()
        except Exception:
            res_base = None

        val_result = validate_profile_and_resume(prof, res_base) if (prof and res_base) else None

        # Unset settings
        unset: list[str] = []
        if prof:
            for k, v in prof.settings.model_dump().items():
                if v is None:
                    unset.append(k)

        # Unmatched skills
        unmatched = get_unmatched_skills_with_counts(c)

        errors = val_result.errors if val_result else ([] if (prof and res_base) else ["profile.yaml or resume_base.yaml not found"])
        warnings = val_result.warnings if val_result else []

        return ProfileReport(
            validation_errors=errors,
            validation_warnings=warnings,
            unset_settings=unset,
            unmatched_skills=unmatched,
        )
    finally:
        if close:
            c.close()


def get_progress(
    conn: sqlite3.Connection | None = None,
    config: AppConfig | None = None,
) -> ProgressSummary:
    """Return daily and weekly application progress metrics and follow-ups due count.

    Metrics are computed strictly from applications.applied_at, never from the status column.
    """
    c, close = _get_db_conn(conn)
    try:
        cfg = config or load_config()
        now = datetime.now(UTC)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        rows = c.execute(
            "SELECT applied_at FROM applications WHERE applied_at IS NOT NULL"
        ).fetchall()

        applied_today = 0
        applied_this_week = 0
        for r in rows:
            ts = r["applied_at"]
            if not ts:
                continue
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                if dt >= today_start:
                    applied_today += 1
                if dt >= week_start:
                    applied_this_week += 1
            except Exception:
                continue

        follow_up_items = follow_ups_due(conn=c, config=cfg)
        follow_ups_due_count = len(follow_up_items)

        return ProgressSummary(
            applied_today=applied_today,
            daily_size=cfg.queue.daily_size,
            applied_this_week=applied_this_week,
            weekly_target=cfg.queue.weekly_target,
            follow_ups_due_count=follow_ups_due_count,
        )
    finally:
        if close:
            c.close()
