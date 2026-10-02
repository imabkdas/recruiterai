"""Greenhouse board job source.

Fetches public postings for a given Greenhouse board token.
Public endpoint: https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from jobpilot.models import RawJob

logger = logging.getLogger(__name__)

GREENHOUSE_API_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"


class BoardNotFoundError(Exception):
    """Raised when a board returns HTTP 404."""


def fetch_greenhouse_board(
    token: str,
    client: httpx.Client | None = None,
) -> list[RawJob]:
    """Fetch all open jobs from a Greenhouse board.

    Raises BoardNotFoundError if the board returns HTTP 404.
    """
    token = token.strip().lower()
    url = GREENHOUSE_API_URL.format(token=token)
    close_client = False
    if client is None:
        client = httpx.Client(timeout=15.0)
        close_client = True

    try:
        resp = client.get(url, params={"content": "true"})
        if resp.status_code == 404:
            raise BoardNotFoundError(f"Greenhouse board '{token}' not found (404)")
        if resp.status_code != 200:
            logger.warning("Greenhouse board '%s' returned HTTP %d", token, resp.status_code)
            return []

        data: dict[str, Any] = resp.json()
        raw_jobs: list[RawJob] = []
        for item in data.get("jobs", []):
            job_id = str(item.get("id", ""))
            title = item.get("title", "").strip()
            if not title:
                continue

            loc_dict = item.get("location") or {}
            location = loc_dict.get("name", "").strip() or None

            url_val = item.get("absolute_url", "").strip()
            content = item.get("content", "").strip() or None

            raw_jobs.append(
                RawJob(
                    source="greenhouse",
                    source_job_id=job_id or None,
                    company=token,
                    title=title,
                    location=location,
                    url=url_val or f"https://boards.greenhouse.io/{token}/jobs/{job_id}",
                    apply_url=url_val,
                    description=content,
                    snippet=None,
                    posted_at=item.get("updated_at"),
                    raw_json=item,
                )
            )

        return raw_jobs

    except httpx.RequestError as exc:
        logger.warning("Request error fetching Greenhouse board '%s': %s", token, exc)
        return []
    finally:
        if close_client:
            client.close()
