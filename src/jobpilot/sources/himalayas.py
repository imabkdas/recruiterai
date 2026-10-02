"""Himalayas job source.

Fetches one page of the public Remote Jobs API.
Endpoint: https://himalayas.app/jobs/api
Terms: free public JSON, no API key. One page per run (limit 20).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from jobpilot.models import RawJob
from jobpilot.pipeline.jd_text import clean_job_description

logger = logging.getLogger(__name__)

HIMALAYAS_API_URL = "https://himalayas.app/jobs/api"
DEFAULT_USER_AGENT = "JobPilot/0.1.0 (job search CLI; https://github.com/imabkdas/recruiterai)"


def fetch_himalayas(
    client: httpx.Client | None = None,
    limit: int = 20,
    user_agent: str = DEFAULT_USER_AGENT,
) -> list[RawJob]:
    """Fetch one page of remote listings from the Himalayas public API."""
    close_client = False
    if client is None:
        client = httpx.Client(timeout=15.0, headers={"User-Agent": user_agent})
        close_client = True

    try:
        resp = client.get(
            HIMALAYAS_API_URL,
            params={"limit": limit},
            headers={"User-Agent": user_agent},
        )
        if resp.status_code == 429:
            logger.warning("Himalayas rate limit (429) hit.")
            raise RuntimeError("Himalayas rate limit (429)")
        resp.raise_for_status()
        payload = resp.json()
    finally:
        if close_client:
            client.close()

    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(jobs, list):
        return []

    raw_jobs: list[RawJob] = []
    for item in jobs:
        if not isinstance(item, dict):
            continue
        parsed = _to_raw_job(item)
        if parsed is not None:
            raw_jobs.append(parsed)
    return raw_jobs


def _to_raw_job(item: dict[str, Any]) -> RawJob | None:
    title = str(item.get("title") or "").strip()
    company = str(item.get("companyName") or "").strip()
    url = str(item.get("applicationLink") or item.get("guid") or "").strip()
    if not title or not company or not url:
        return None

    restrictions = item.get("locationRestrictions") or []
    if isinstance(restrictions, list):
        location = ", ".join(str(part) for part in restrictions if part) or None
    else:
        location = str(restrictions) or None

    description = clean_job_description(item.get("description") or item.get("excerpt") or "")
    posted_at = _posted_at(item.get("pubDate"))
    source_job_id = str(item.get("guid") or "").strip() or None

    return RawJob(
        source="himalayas",
        source_job_id=source_job_id,
        company=company,
        title=title,
        location=location,
        remote_type="remote",
        url=url,
        apply_url=url,
        description=description or None,
        snippet=(item.get("excerpt") or None),
        posted_at=posted_at,
        raw_json={"guid": item.get("guid"), "companySlug": item.get("companySlug")},
    )


def _posted_at(value: Any) -> str | None:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=UTC).isoformat()
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
