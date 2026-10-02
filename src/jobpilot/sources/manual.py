"""Manual input source: add-url and add-jd handlers."""

from __future__ import annotations

import logging
import sqlite3
from urllib.parse import urlparse

from jobpilot.db import update_status, upsert_job
from jobpilot.pipeline.ingest import compute_fingerprint

logger = logging.getLogger(__name__)


def add_url_job(
    conn: sqlite3.Connection,
    url: str,
    title: str | None = None,
    company: str | None = None,
    location: str | None = None,
) -> int:
    """Store a job found manually by URL.

    Sets status to 'needs_jd'.
    Returns the job ID.
    """
    url = url.strip()
    if not url:
        raise ValueError("URL cannot be empty.")

    # Infer company from hostname if not provided
    if not company:
        hostname = urlparse(url).hostname or ""
        parts = hostname.split(".")
        company = parts[-2].title() if len(parts) >= 2 else "Unknown"

    title = title or "Software Engineer"
    location = location or "Remote"
    fingerprint = compute_fingerprint(company, title, location)

    job_data = {
        "fingerprint": fingerprint,
        "source": "manual",
        "company": company,
        "title": title,
        "location": location,
        "url": url,
        "apply_url": url,
        "description": None,
        "snippet": None,
        "status": "needs_jd",
    }

    job_id = upsert_job(conn, job_data)
    # Ensure status is needs_jd
    update_status(conn, job_id, "needs_jd")
    return job_id


def add_jd_to_job(
    conn: sqlite3.Connection,
    job_target: str,
    jd_text: str,
    title: str | None = None,
    company: str | None = None,
) -> int:
    """Paste or attach JD text for a job.

    Updates the job description and moves status to 'new' so it re-enters the pipeline.
    If job_target is '--new' or 'new', creates a new job record.
    Returns the job ID.
    """
    jd_text = jd_text.strip()
    if not jd_text:
        raise ValueError("Job description text cannot be empty.")

    if job_target.strip().lower() in ("--new", "new"):
        company = company or "Unknown"
        title = title or "Software Engineer"
        location = "Remote"
        fingerprint = compute_fingerprint(company, title, location)

        job_data = {
            "fingerprint": fingerprint,
            "source": "manual",
            "company": company,
            "title": title,
            "location": location,
            "url": f"manual://{fingerprint[:10]}",
            "apply_url": None,
            "description": jd_text,
            "snippet": jd_text[:300],
            "status": "new",
        }
        return upsert_job(conn, job_data)

    try:
        job_id = int(job_target)
    except ValueError as exc:
        raise ValueError(f"Invalid job ID '{job_target}'. Must be an integer or '--new'.") from exc

    existing = conn.execute("SELECT id, status FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not existing:
        raise KeyError(f"Job {job_id} not found.")

    # Needs-JD jobs re-enter analysis. A job the user already moved along
    # keeps its status; only the description is filled in.
    if existing["status"] in ("needs_jd", "analysis_failed"):
        conn.execute(
            "UPDATE jobs SET description = ?, status = 'new', status_reason = NULL WHERE id = ?",
            (jd_text, job_id),
        )
    else:
        conn.execute(
            "UPDATE jobs SET description = ? WHERE id = ?",
            (jd_text, job_id),
        )
    conn.commit()
    return job_id
