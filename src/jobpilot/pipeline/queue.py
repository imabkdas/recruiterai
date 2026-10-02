"""Daily queue builder for JobPilot.

Implements Sections 12.1 and 13 of JOBPILOT_DESIGN.md.
Pulls scored jobs not yet applied/skipped, ordered by tier, total score, then recency.
Includes needs_jd jobs in a separate section at the bottom.
Emits a terminal table and data/out/queue_YYYY-MM-DD.csv.
Supports roll-forward of unfinished items.
"""

from __future__ import annotations

import contextlib
import csv
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from jobpilot.config import AppConfig, load_config
from jobpilot.db import update_status

logger = logging.getLogger(__name__)


def _project_root() -> Path:
    """Find workspace root containing pyproject.toml."""
    current = Path.cwd()
    for parent in [current, *current.parents]:
        if (parent / "pyproject.toml").exists():
            return parent
    return current


def format_matched_skills(raw_json: str | None) -> str:
    """Format matched skills JSON into comma-separated list."""
    if not raw_json:
        return ""
    try:
        data = json.loads(raw_json)
        if isinstance(data, list):
            skills = []
            for item in data:
                if isinstance(item, dict):
                    skill_name = item.get("skill")
                    if skill_name:
                        skills.append(skill_name)
                elif isinstance(item, str) and item:
                    skills.append(item)
            return ", ".join(skills)
        return str(data)
    except Exception:
        return ""


def format_gaps(missing_req_json: str | None, missing_pref_json: str | None) -> str:
    """Format missing skills JSON into comma-separated list with requirement markers."""
    parts = []
    if missing_req_json:
        try:
            reqs = json.loads(missing_req_json)
            if isinstance(reqs, list):
                parts.extend(f"{r} (req)" for r in reqs if r)
        except Exception:
            pass
    if missing_pref_json:
        try:
            prefs = json.loads(missing_pref_json)
            if isinstance(prefs, list):
                parts.extend(f"{p} (pref)" for p in prefs if p)
        except Exception:
            pass
    return ", ".join(parts)


def format_flags(flags_json: str | None) -> str:
    """Format flags JSON into comma-separated string."""
    if not flags_json:
        return ""
    try:
        data = json.loads(flags_json)
        if isinstance(data, list):
            return ", ".join(str(f) for f in data if f)
        return str(data)
    except Exception:
        return ""


@dataclass
class QueueItem:
    """Single item in the daily ranked queue."""

    rank: int
    job_id: int
    company: str
    title: str
    location: str
    tier: str
    score: float
    matched_skills: str
    gaps: str
    flags: str
    url: str
    prepared: str  # 'y' | 'n'


@dataclass
class NeedsJdItem:
    """Job requiring full description input."""

    job_id: int
    company: str
    title: str
    location: str
    url: str


@dataclass
class QueueResult:
    """Complete result of queue generation."""

    items: list[QueueItem]
    needs_jd: list[NeedsJdItem]
    csv_path: Path | None
    date_str: str

    def format_table(self) -> str:
        """Format the queue and needs_jd items for terminal display."""
        lines: list[str] = []
        lines.append(f"=== Daily Ranked Queue ({self.date_str}) ===")

        if not self.items:
            lines.append("No active jobs in queue. Run 'jobpilot fetch' and 'jobpilot score' to populate.")
        else:
            header = f"{'Rank':<5} {'ID':<6} {'Tier':<5} {'Score':<6} {'Prep':<5} {'Company':<20} {'Title':<30} {'Location':<18} {'URL'}"
            lines.append(header)
            lines.append("-" * len(header))
            for item in self.items:
                company = (item.company[:18] + "..") if len(item.company) > 20 else item.company
                title = (item.title[:28] + "..") if len(item.title) > 30 else item.title
                loc = (item.location[:16] + "..") if len(item.location) > 18 else item.location
                lines.append(
                    f"{item.rank:<5} {item.job_id:<6} {item.tier:<5} {item.score:<6.1f} {item.prepared:<5} {company:<20} {title:<30} {loc:<18} {item.url}"
                )

        if self.needs_jd:
            lines.append(f"\n=== Needs Job Description ({len(self.needs_jd)}) ===")
            lines.append("Run 'jobpilot add-jd <id>' to provide the full JD text:")
            for item in self.needs_jd:
                loc_str = f" ({item.location})" if item.location else ""
                lines.append(f"  [{item.job_id}] {item.company} — {item.title}{loc_str}")
                lines.append(f"      URL: {item.url}")

        if self.csv_path:
            lines.append(f"\n📁 Saved queue to {self.csv_path}")

        return "\n".join(lines)


def expire_stale_jobs(
    conn,
    max_days: int = 21,
    reference_date: datetime | None = None,
) -> int:
    """Mark unapplied jobs older than max_days as 'expired'.

    Targets unapplied jobs in ('scored', 'queued', 'prepared', 'new', 'needs_jd', 'analyzed').
    Does not touch jobs that were applied, replied, interview, offer, or terminal.
    """
    ref = reference_date or datetime.now(UTC)
    cutoff = (ref - timedelta(days=max_days)).isoformat()

    rows = conn.execute(
        """
        SELECT id, COALESCE(posted_at, discovered_at) as age_date
        FROM jobs
        WHERE status IN ('scored', 'queued', 'prepared', 'new', 'needs_jd', 'analyzed')
          AND COALESCE(posted_at, discovered_at) < ?
        """,
        (cutoff,),
    ).fetchall()

    for r in rows:
        update_status(conn, r["id"], "expired", reason=f"age_exceeded (>{max_days}d)")

    if rows:
        logger.info("Expired %d stale jobs older than %d days.", len(rows), max_days)
    return len(rows)


def build_queue(
    conn,
    config: AppConfig | None = None,
    size: int | None = None,
    out_dir: Path | None = None,
    date_str: str | None = None,
    write_csv: bool = True,
) -> QueueResult:
    """Build the daily ranked queue, roll forward unfinished items, and export CSV."""
    cfg = config or load_config()
    daily_limit = size if (size is not None and size > 0) else cfg.queue.daily_size
    current_date = date_str or datetime.now(UTC).strftime("%Y-%m-%d")

    # 0. Expire stale unapplied jobs older than max_job_age_days
    ref_dt = None
    if date_str:
        with contextlib.suppress(Exception):
            ref_dt = datetime.fromisoformat(date_str).replace(tzinfo=UTC)
    expire_stale_jobs(conn, max_days=cfg.thresholds.max_job_age_days, reference_date=ref_dt)

    # 1. Query ranked candidates (scored, queued, prepared with Tier A or B)
    # Order: Tier A before B, total score desc, recency desc, id desc
    query = """
        SELECT j.id, j.company, j.title, j.location, j.url, j.apply_url, j.status,
               j.posted_at, j.discovered_at,
               s.total, s.tier, s.matched_skills, s.missing_required, s.missing_preferred, s.flags,
               a.prepared_at, a.tailored_summary
        FROM jobs j
        JOIN scores s ON j.id = s.job_id
        LEFT JOIN applications a ON j.id = a.job_id
        WHERE j.status IN ('scored', 'queued', 'prepared')
          AND s.tier IN ('A', 'B')
        ORDER BY
          CASE s.tier WHEN 'A' THEN 1 WHEN 'B' THEN 2 ELSE 3 END,
          s.total DESC,
          COALESCE(j.posted_at, j.discovered_at) DESC,
          j.id DESC
        LIMIT ?
    """
    rows = conn.execute(query, (daily_limit,)).fetchall()

    queue_items: list[QueueItem] = []
    for idx, row in enumerate(rows, start=1):
        job_id = row["id"]
        status = row["status"]

        # Transition newly scored jobs into 'queued'
        if status == "scored":
            update_status(conn, job_id, "queued")

        is_prepared = (
            "y"
            if (status == "prepared" or row["prepared_at"] or row["tailored_summary"])
            else "n"
        )
        url = row["apply_url"] or row["url"] or ""

        matched = format_matched_skills(row["matched_skills"])
        gaps = format_gaps(row["missing_required"], row["missing_preferred"])
        flags = format_flags(row["flags"])

        queue_items.append(
            QueueItem(
                rank=idx,
                job_id=job_id,
                company=row["company"] or "",
                title=row["title"] or "",
                location=row["location"] or "",
                tier=row["tier"] or "",
                score=float(row["total"]),
                matched_skills=matched,
                gaps=gaps,
                flags=flags,
                url=url,
                prepared=is_prepared,
            )
        )

    # 2. Query needs_jd items
    needs_jd_query = """
        SELECT id, company, title, location, url, apply_url
        FROM jobs
        WHERE status = 'needs_jd'
        ORDER BY COALESCE(posted_at, discovered_at) DESC, id DESC
    """
    needs_jd_rows = conn.execute(needs_jd_query).fetchall()
    needs_jd_items: list[NeedsJdItem] = [
        NeedsJdItem(
            job_id=r["id"],
            company=r["company"] or "",
            title=r["title"] or "",
            location=r["location"] or "",
            url=r["apply_url"] or r["url"] or "",
        )
        for r in needs_jd_rows
    ]

    # 3. Export CSV if requested
    csv_file: Path | None = None
    if write_csv:
        target_dir = out_dir or (_project_root() / "data" / "out")
        target_dir.mkdir(parents=True, exist_ok=True)
        csv_file = target_dir / f"queue_{current_date}.csv"

        fieldnames = [
            "rank",
            "job_id",
            "company",
            "title",
            "location",
            "tier",
            "score",
            "matched_skills",
            "gaps",
            "flags",
            "url",
            "prepared",
        ]
        with open(csv_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(fieldnames)
            for item in queue_items:
                writer.writerow([
                    item.rank,
                    item.job_id,
                    item.company,
                    item.title,
                    item.location,
                    item.tier,
                    f"{item.score:.1f}",
                    item.matched_skills,
                    item.gaps,
                    item.flags,
                    item.url,
                    item.prepared,
                ])

    return QueueResult(
        items=queue_items,
        needs_jd=needs_jd_items,
        csv_path=csv_file,
        date_str=current_date,
    )
