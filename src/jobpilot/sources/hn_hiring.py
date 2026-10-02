"""Hacker News 'Who is hiring?' job source.

Fetches the monthly 'Ask HN: Who is hiring?' thread via the official Algolia HN API
and parses top-level comments into RawJobs.
Endpoint 1: https://hn.algolia.com/api/v1/search_by_date?query=who%20is%20hiring&tags=story,author_whoishiring
Endpoint 2: https://hn.algolia.com/api/v1/items/{story_id}
"""

from __future__ import annotations

import html
import logging
import re
from typing import Any

import httpx

from jobpilot.models import RawJob
from jobpilot.pipeline.labels import readable_company_title

logger = logging.getLogger(__name__)

HN_SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date"
HN_ITEM_URL = "https://hn.algolia.com/api/v1/items/{story_id}"
DEFAULT_USER_AGENT = "JobPilot/0.1.0 (job search CLI; https://github.com/imabkdas/recruiterai)"


def _parse_first_line(text: str) -> tuple[str, str, str | None]:
    """Parse typical HN hiring header: 'Company | Title | Location | Remote ...'

    Returns (company, title, location).
    """
    decoded = html.unescape(text)
    decoded = re.sub(r"<[^>]+>", "\n", decoded)
    decoded = html.unescape(decoded)
    lines = [line.strip() for line in decoded.splitlines() if line.strip()]
    if not lines:
        return "Unknown", "Software Engineer", None

    first_line = re.sub(r"\s+", " ", lines[0]).strip()
    parts = [p.strip() for p in first_line.split("|") if p.strip()]
    remote = "Remote" if "remote" in decoded.lower() else None

    if len(parts) >= 3:
        location = " | ".join(parts[2:])
        return parts[0][:60], parts[1][:80], location[:80]
    if len(parts) == 2:
        location = "Remote" if "remote" in first_line.lower() else None
        return parts[0][:60], parts[1][:80], location

    company, title = readable_company_title(first_line, "Software Engineer", first_line)
    return company, title, remote


def fetch_hn_hiring(
    client: httpx.Client | None = None,
    user_agent: str = DEFAULT_USER_AGENT,
    max_comments: int = 150,
) -> list[RawJob]:
    """Fetch job postings from the latest HN 'Who is hiring?' thread."""
    close_client = False
    if client is None:
        client = httpx.Client(timeout=15.0, headers={"User-Agent": user_agent})
        close_client = True

    try:
        # Step 1: Find the latest "Who is hiring" story
        search_resp = client.get(
            HN_SEARCH_URL,
            params={
                "query": "who is hiring",
                "tags": "story,author_whoishiring",
            },
            headers={"User-Agent": user_agent},
        )
        if search_resp.status_code != 200:
            logger.warning("HN search returned HTTP %d", search_resp.status_code)
            return []

        search_data = search_resp.json()
        hits = search_data.get("hits", [])
        if not hits:
            logger.warning("No HN 'Who is hiring' threads found.")
            return []

        story_id = str(hits[0].get("objectID", "")).strip()
        if not story_id:
            return []

        # Step 2: Fetch comment tree for the story
        item_url = HN_ITEM_URL.format(story_id=story_id)
        item_resp = client.get(item_url, headers={"User-Agent": user_agent})
        if item_resp.status_code != 200:
            logger.warning("HN item fetch for %s returned HTTP %d", story_id, item_resp.status_code)
            return []

        item_data: dict[str, Any] = item_resp.json()
        children = item_data.get("children", [])
        if not isinstance(children, list):
            return []

        raw_jobs: list[RawJob] = []
        for child in children[:max_comments]:
            if not isinstance(child, dict):
                continue
            comment_id = str(child.get("id", "")).strip()
            text = child.get("text")
            if not comment_id or not text:
                continue

            company, title, location = _parse_first_line(text)
            posted_at = child.get("created_at")

            raw_jobs.append(
                RawJob(
                    source="hn_hiring",
                    source_job_id=comment_id,
                    company=company,
                    title=title,
                    location=location,
                    remote_type="remote" if (location and "remote" in location.lower()) else None,
                    url=f"https://news.ycombinator.com/item?id={comment_id}",
                    apply_url=f"https://news.ycombinator.com/item?id={comment_id}",
                    description=text,
                    snippet=None,
                    posted_at=posted_at,
                    raw_json=child,
                )
            )

        logger.info("HN hiring fetched %d job listings from thread %s.", len(raw_jobs), story_id)
        return raw_jobs

    except httpx.RequestError as exc:
        logger.warning("Request error fetching HN hiring: %s", exc)
        return []
    finally:
        if close_client:
            client.close()
