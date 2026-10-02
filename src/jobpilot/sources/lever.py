"""Lever board job source.

Fetches public postings for a given Lever company slug.
Public endpoint: https://api.lever.co/v0/postings/{company}?mode=json
"""

from __future__ import annotations

import contextlib
import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from jobpilot.models import RawJob
from jobpilot.sources.greenhouse import BoardNotFoundError

logger = logging.getLogger(__name__)

LEVER_API_URL = "https://api.lever.co/v0/postings/{company}"


def fetch_lever_board(
    company: str,
    client: httpx.Client | None = None,
) -> list[RawJob]:
    """Fetch all open jobs from a Lever company board.

    Raises BoardNotFoundError if the company returns HTTP 404.
    """
    company = company.strip().lower()
    url = LEVER_API_URL.format(company=company)
    close_client = False
    if client is None:
        client = httpx.Client(timeout=15.0)
        close_client = True

    try:
        resp = client.get(url, params={"mode": "json"})
        if resp.status_code == 404:
            raise BoardNotFoundError(f"Lever board '{company}' not found (404)")
        if resp.status_code != 200:
            logger.warning("Lever board '%s' returned HTTP %d", company, resp.status_code)
            return []

        items: list[dict[str, Any]] = resp.json()
        if not isinstance(items, list):
            return []

        raw_jobs: list[RawJob] = []
        for item in items:
            job_id = str(item.get("id", ""))
            title = item.get("text", "").strip()
            if not title:
                continue

            categories = item.get("categories") or {}
            location = categories.get("location", "").strip() or None

            hosted_url = item.get("hostedUrl", "").strip()
            apply_url = item.get("applyUrl", "").strip() or hosted_url
            description = item.get("descriptionPlain") or item.get("description")

            posted_at = None
            created_at_ms = item.get("createdAt")
            if isinstance(created_at_ms, (int, float)):
                with contextlib.suppress(Exception):
                    posted_at = datetime.fromtimestamp(created_at_ms / 1000.0, tz=UTC).isoformat()

            raw_jobs.append(
                RawJob(
                    source="lever",
                    source_job_id=job_id or None,
                    company=company,
                    title=title,
                    location=location,
                    url=hosted_url or f"https://jobs.lever.co/{company}/{job_id}",
                    apply_url=apply_url or None,
                    description=description,
                    snippet=None,
                    posted_at=posted_at,
                    raw_json=item,
                )
            )

        return raw_jobs

    except httpx.RequestError as exc:
        logger.warning("Request error fetching Lever board '%s': %s", company, exc)
        return []
    finally:
        if close_client:
            client.close()
