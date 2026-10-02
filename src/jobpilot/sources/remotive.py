"""Remotive job source.

Fetches public software development remote jobs from Remotive API.
Endpoint: https://remotive.com/api/remote-jobs?category=software-dev
Terms: Attribution to Remotive with direct job links, max ~4 requests/day.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from jobpilot.models import RawJob

logger = logging.getLogger(__name__)

REMOTIVE_API_URL = "https://remotive.com/api/remote-jobs"
DEFAULT_USER_AGENT = "JobPilot/0.1.0 (job search CLI; https://github.com/imabkdas/recruiterai)"


def fetch_remotive(
    client: httpx.Client | None = None,
    category: str = "software-dev",
    user_agent: str = DEFAULT_USER_AGENT,
) -> list[RawJob]:
    """Fetch remote listings from Remotive public API."""
    close_client = False
    if client is None:
        client = httpx.Client(timeout=15.0, headers={"User-Agent": user_agent})
        close_client = True

    try:
        resp = client.get(
            REMOTIVE_API_URL,
            params={"category": category},
            headers={"User-Agent": user_agent},
        )
        if resp.status_code == 429:
            logger.warning("Remotive rate limit (429) hit. Returning empty.")
            return []
        if resp.status_code != 200:
            logger.warning("Remotive returned HTTP %d", resp.status_code)
            return []

        data: dict[str, Any] = resp.json()
        jobs_list = data.get("jobs", [])
        if not isinstance(jobs_list, list):
            return []

        raw_jobs: list[RawJob] = []
        for item in jobs_list:
            if not isinstance(item, dict):
                continue

            job_id = str(item.get("id", "")).strip()
            title = str(item.get("title", "")).strip()
            if not title:
                continue

            company = str(item.get("company_name", "")).strip() or "Unknown"
            location = str(item.get("candidate_required_location", "")).strip() or "Remote"
            url = str(item.get("url", "")).strip()
            description = item.get("description")
            posted_at = item.get("publication_date")

            raw_jobs.append(
                RawJob(
                    source="remotive",
                    source_job_id=job_id or None,
                    company=company,
                    title=title,
                    location=location,
                    remote_type="remote",
                    url=url or f"https://remotive.com/remote-jobs/{job_id}",
                    apply_url=url or None,
                    description=description,
                    snippet=None,
                    posted_at=posted_at,
                    raw_json=item,
                )
            )

        logger.info("Remotive fetched %d listings.", len(raw_jobs))
        return raw_jobs

    except httpx.RequestError as exc:
        logger.warning("Request error fetching Remotive: %s", exc)
        return []
    finally:
        if close_client:
            client.close()
