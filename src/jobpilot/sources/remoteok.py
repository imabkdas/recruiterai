"""RemoteOK job source.

Fetches public listings from RemoteOK API.
Endpoint: https://remoteok.com/api
Terms: Public API, requires User-Agent, attribution to RemoteOK.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from jobpilot.models import RawJob

logger = logging.getLogger(__name__)

REMOTEOK_API_URL = "https://remoteok.com/api"
DEFAULT_USER_AGENT = "JobPilot/0.1.0 (job search CLI; https://github.com/imabkdas/recruiterai)"


def fetch_remoteok(
    client: httpx.Client | None = None,
    user_agent: str = DEFAULT_USER_AGENT,
) -> list[RawJob]:
    """Fetch remote listings from RemoteOK public API.

    Skips the first metadata/legal item in the array.
    """
    close_client = False
    if client is None:
        client = httpx.Client(timeout=15.0, headers={"User-Agent": user_agent})
        close_client = True

    try:
        resp = client.get(REMOTEOK_API_URL, headers={"User-Agent": user_agent})
        if resp.status_code == 429:
            logger.warning("RemoteOK rate limit (429) hit. Returning empty.")
            return []
        if resp.status_code != 200:
            logger.warning("RemoteOK returned HTTP %d", resp.status_code)
            return []

        items: list[dict[str, Any]] = resp.json()
        if not isinstance(items, list):
            return []

        raw_jobs: list[RawJob] = []
        for item in items:
            # RemoteOK's first item is a legal notice without an 'id' or 'position'
            if not isinstance(item, dict):
                continue
            if not item.get("id") or not item.get("position"):
                continue

            job_id = str(item.get("id", "")).strip()
            title = str(item.get("position", "")).strip()
            company = str(item.get("company", "")).strip() or "Unknown"

            location = str(item.get("location", "")).strip() or "Remote"
            url = str(item.get("url", "")).strip()
            apply_url = str(item.get("apply_url", "")).strip() or url
            description = item.get("description")
            posted_at = item.get("date")

            raw_jobs.append(
                RawJob(
                    source="remoteok",
                    source_job_id=job_id or None,
                    company=company,
                    title=title,
                    location=location,
                    remote_type="remote",
                    url=url or f"https://remoteok.com/remote-jobs/{job_id}",
                    apply_url=apply_url or None,
                    description=description,
                    snippet=None,
                    posted_at=posted_at,
                    raw_json=item,
                )
            )

        logger.info("RemoteOK fetched %d listings.", len(raw_jobs))
        return raw_jobs

    except httpx.RequestError as exc:
        logger.warning("Request error fetching RemoteOK: %s", exc)
        return []
    finally:
        if close_client:
            client.close()
