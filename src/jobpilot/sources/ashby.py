"""Ashby board job source.

Fetches public postings for a given Ashby job board name.
Public endpoint: https://api.ashbyhq.com/posting-api/job-board/{board_name}?includeCompensation=true
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from jobpilot.models import RawJob
from jobpilot.sources.greenhouse import BoardNotFoundError

logger = logging.getLogger(__name__)

ASHBY_API_URL = "https://api.ashbyhq.com/posting-api/job-board/{board_name}"


def fetch_ashby_board(
    board_name: str,
    client: httpx.Client | None = None,
) -> list[RawJob]:
    """Fetch listed jobs from an Ashby job board.

    Raises BoardNotFoundError if the board returns HTTP 404.
    """
    board_name = board_name.strip().lower()
    url = ASHBY_API_URL.format(board_name=board_name)
    close_client = False
    if client is None:
        client = httpx.Client(timeout=15.0)
        close_client = True

    try:
        resp = client.get(url, params={"includeCompensation": "true"})
        if resp.status_code == 404:
            raise BoardNotFoundError(f"Ashby board '{board_name}' not found (404)")
        if resp.status_code != 200:
            logger.warning("Ashby board '%s' returned HTTP %d", board_name, resp.status_code)
            return []

        data: dict[str, Any] = resp.json()
        raw_jobs: list[RawJob] = []
        for item in data.get("jobs", []):
            # Only listed jobs
            if item.get("isListed") is False:
                continue

            job_id = str(item.get("id", ""))
            title = item.get("title", "").strip()
            if not title:
                continue

            location = item.get("location", "")
            if isinstance(location, dict):
                location = location.get("name", "")
            location = str(location).strip() or None

            job_url = item.get("jobUrl", "").strip()
            apply_url = item.get("applyUrl", "").strip() or job_url
            description = item.get("descriptionPlain") or item.get("descriptionHtml")

            raw_jobs.append(
                RawJob(
                    source="ashby",
                    source_job_id=job_id or None,
                    company=board_name,
                    title=title,
                    location=location,
                    url=job_url or f"https://jobs.ashbyhq.com/{board_name}/{job_id}",
                    apply_url=apply_url or None,
                    description=description,
                    snippet=None,
                    posted_at=item.get("publishedAt"),
                    raw_json=item,
                )
            )

        return raw_jobs

    except httpx.RequestError as exc:
        logger.warning("Request error fetching Ashby board '%s': %s", board_name, exc)
        return []
    finally:
        if close_client:
            client.close()
