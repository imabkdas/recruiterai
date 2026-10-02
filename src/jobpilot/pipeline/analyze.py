"""Job analysis pipeline: extracts structured JobAnalysis using LLM.

Features:
- Reads prompts/extract_job_v1.txt.
- Truncates descriptions to config.llm.max_description_chars.
- Redacts contact details before sending via llm.redact.
- Caches by (job_id, prompt_version, content_hash) in SQLite analyses table.
- Normalizes extracted skills through profile/skills alias map.
- Respects daily call cap, stopping cleanly when reached.
- Marks status 'analyzed' on success, 'analysis_failed' on failure.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from jobpilot.config import AppConfig, EnvSettings, load_config, load_env
from jobpilot.db import (
    get_all_analyses,
    get_analysis,
    record_llm_usage,
    save_analysis,
    update_status,
)
from jobpilot.llm.client import (
    DailyCallCapExceededError,
    GeminiClient,
    LLMQuotaExhaustedError,
    LLMRateLimitError,
)
from jobpilot.llm.redact import redact_text
from jobpilot.models import JobAnalysis
from jobpilot.profile.skills import get_alias_map, normalize_skill

logger = logging.getLogger(__name__)

PROMPT_VERSION = "v1"
DEFAULT_PROMPT_PATH = Path(__file__).resolve().parents[3] / "prompts" / "extract_job_v1.txt"


@dataclass
class AnalysisSummary:
    """Summary counts from an analysis run."""

    scanned: int = 0
    analyzed: int = 0
    cached: int = 0
    failed: int = 0
    cap_reached: bool = False
    stop_reason: str | None = None


def _load_prompt_template(prompt_path: Path | None = None) -> str:
    """Load the job extraction prompt template."""
    path = prompt_path or DEFAULT_PROMPT_PATH
    if not path.exists():
        # Fallback search from cwd
        cwd_path = Path.cwd() / "prompts" / "extract_job_v1.txt"
        if cwd_path.exists():
            path = cwd_path
        else:
            raise FileNotFoundError(f"Extraction prompt file not found at {path}")
    return path.read_text(encoding="utf-8")


def run_analysis(
    conn: sqlite3.Connection,
    config: AppConfig | None = None,
    env: EnvSettings | None = None,
    client: GeminiClient | None = None,
    limit: int | None = None,
    prompt_path: Path | None = None,
) -> AnalysisSummary:
    """Analyze unanalyzed jobs that possess a job description.

    Ignores jobs in 'needs_jd' or jobs without descriptions.
    """
    cfg = config or load_config()
    settings = env or load_env()
    llm_client = client or GeminiClient(config=cfg, env=settings, conn=conn)

    summary = AnalysisSummary()

    # Check daily cap upfront
    if llm_client.is_cap_reached():
        logger.warning("Daily LLM call cap already reached. Skipping analysis.")
        summary.cap_reached = True
        return summary

    prompt_template = _load_prompt_template(prompt_path)
    model_name = cfg.llm.model_extract or "gemini-3.8-flash"
    max_chars = cfg.llm.max_description_chars or 6000

    query = (
        "SELECT id, company, title, location, description "
        "FROM jobs "
        "WHERE status = 'new' AND description IS NOT NULL AND trim(description) != '' "
        "ORDER BY id ASC"
    )
    if limit is not None and limit > 0:
        query += f" LIMIT {int(limit)}"

    rows = conn.execute(query).fetchall()

    for row in rows:
        job_id = row["id"]
        raw_description = row["description"]

        # Check cap before processing each job
        if llm_client.is_cap_reached():
            logger.info("Daily LLM call cap reached during run. Halting analysis cleanly.")
            summary.cap_reached = True
            break

        summary.scanned += 1

        # Truncate and redact description
        truncated_desc = raw_description[:max_chars]
        redacted_desc = redact_text(truncated_desc)
        content_hash = hashlib.sha256(redacted_desc.encode("utf-8")).hexdigest()

        # Check cache
        cached_analysis_json = get_analysis(conn, job_id, PROMPT_VERSION, content_hash)
        if cached_analysis_json:
            logger.debug("Cache hit for Job %d (hash: %s)", job_id, content_hash[:8])
            record_llm_usage(conn, task="extract_job", model=model_name, input_tokens=0, output_tokens=0, cached=1)
            update_status(conn, job_id, "analyzed")
            summary.cached += 1
            continue

        # Format prompt
        prompt = prompt_template.format(description=redacted_desc)

        try:
            analysis = llm_client.generate_json(
                prompt=prompt,
                schema_model=JobAnalysis,
                model=model_name,
                task="extract_job",
            )

            # Normalize extracted skills via alias map
            analysis.required_skills = [normalize_skill(s) for s in analysis.required_skills]
            analysis.preferred_skills = [normalize_skill(s) for s in analysis.preferred_skills]

            # Save analysis and update status
            save_analysis(
                conn,
                job_id=job_id,
                prompt_version=PROMPT_VERSION,
                model=model_name,
                content_hash=content_hash,
                analysis_json=analysis.model_dump_json(),
            )
            update_status(conn, job_id, "analyzed")
            summary.analyzed += 1

        except DailyCallCapExceededError as exc:
            logger.info("Daily LLM call cap reached while calling LLM. Stopping.")
            summary.cap_reached = True
            summary.stop_reason = str(exc)
            break
        except (LLMQuotaExhaustedError, LLMRateLimitError) as exc:
            # Provider-side limit, not a problem with this job. Leave it in 'new'
            # so the next run retries it instead of burning quota on the rest.
            logger.warning("Halting analysis at Job %d: %s", job_id, exc)
            summary.cap_reached = True
            summary.stop_reason = str(exc)
            break
        except Exception as exc:
            logger.error("Analysis failed for Job %d: %s", job_id, exc)
            update_status(conn, job_id, "analysis_failed", reason=str(exc)[:200])
            summary.failed += 1

    return summary


def get_unmatched_skills_with_counts(
    conn: sqlite3.Connection,
    alias_path: Path | None = None,
) -> list[tuple[str, int]]:
    """Return list of (skill_name, count) for extracted skills not present in alias map."""
    alias_map = get_alias_map(alias_path)
    analyses_json = get_all_analyses(conn)

    counter: Counter[str] = Counter()
    for raw_json in analyses_json:
        try:
            data = json.loads(raw_json)
        except Exception:
            continue

        skills: list[str] = []
        skills.extend(data.get("required_skills") or [])
        skills.extend(data.get("preferred_skills") or [])

        for s in skills:
            if not isinstance(s, str):
                continue
            clean = s.strip()
            if not clean:
                continue
            if clean.lower() not in alias_map:
                counter[clean] += 1

    return counter.most_common()
