"""Company ATS board discovery.

Scans stored URLs (jobs.url, apply_url, job_urls.url) using regexes for
Greenhouse, Lever, and Ashby boards. Normalizes tokens, dedupes, and stores
in the company_boards table. Makes NO HTTP requests.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from urllib.parse import parse_qs, urlparse

from jobpilot.db import upsert_board

logger = logging.getLogger(__name__)

# Patterns for extracting ATS boards
# 1. Greenhouse
# e.g., https://boards.greenhouse.io/{token}/jobs/{id}
#       https://boards.greenhouse.io/embed/job_board?for={token}
#       https://job-boards.greenhouse.io/{token}
_GH_HOST_RE = re.compile(r"^(?:[a-zA-Z0-9-]+\.)*greenhouse\.io$", re.IGNORECASE)

# 2. Lever
# e.g., https://jobs.lever.co/{company}/...
_LEVER_HOST_RE = re.compile(r"^jobs\.lever\.co$", re.IGNORECASE)

# 3. Ashby
# e.g., https://jobs.ashbyhq.com/{company}/...
_ASHBY_HOST_RE = re.compile(r"^jobs\.ashbyhq\.com$", re.IGNORECASE)


def extract_ats_board(url: str) -> tuple[str, str] | None:
    """Extract (ats, token) from a URL if it matches a known ATS pattern.

    Returns (ats_name, normalized_token) or None.
    Handles case variations, localized subdomains, trailing slashes, and query params.
    """
    if not url or not isinstance(url, str):
        return None

    url = url.strip()
    try:
        parsed = urlparse(url)
    except Exception:
        return None

    hostname = (parsed.hostname or "").lower()
    path_segments = [p for p in parsed.path.strip("/").split("/") if p]

    # --- Greenhouse ---
    if _GH_HOST_RE.match(hostname):
        # Check query params for `for=token`
        query_params = parse_qs(parsed.query)
        if "for" in query_params and query_params["for"]:
            token = query_params["for"][0].strip().lower()
            if token and token not in ("embed", "job_board"):
                return ("greenhouse", token)

        # Check path segments
        if path_segments:
            # Skip non-token prefixes
            if path_segments[0] == "embed" and len(path_segments) > 1 and path_segments[1] == "job_board":
                # Fall through to checking other segments or query
                pass
            elif path_segments[0] not in ("embed", "v1", "api", "job_board"):
                token = path_segments[0].strip().lower()
                if token:
                    return ("greenhouse", token)

    # --- Lever ---
    if _LEVER_HOST_RE.match(hostname) and path_segments:
        token = path_segments[0].strip().lower()
        if token and token not in ("v0", "apply"):
            return ("lever", token)

    # --- Ashby ---
    if _ASHBY_HOST_RE.match(hostname) and path_segments:
        token = path_segments[0].strip().lower()
        if token and token not in ("posting-api", "api"):
            return ("ashby", token)

    return None


def scan_stored_urls(conn: sqlite3.Connection) -> list[tuple[str, str, int | None]]:
    """Scan all URLs in jobs and job_urls tables for ATS boards.

    Returns deduplicated list of (ats, token, job_id).
    """
    discovered: dict[tuple[str, str], int | None] = {}

    # 1. Scan jobs table (url and apply_url)
    job_rows = conn.execute("SELECT id, url, apply_url FROM jobs").fetchall()
    for row in job_rows:
        job_id = row["id"]
        for url_field in (row["url"], row["apply_url"]):
            if url_field:
                match = extract_ats_board(url_field)
                if match:
                    ats, token = match
                    if (ats, token) not in discovered:
                        discovered[(ats, token)] = job_id

    # 2. Scan job_urls table
    url_rows = conn.execute("SELECT job_id, url FROM job_urls").fetchall()
    for row in url_rows:
        job_id = row["job_id"]
        url_field = row["url"]
        if url_field:
            match = extract_ats_board(url_field)
            if match:
                ats, token = match
                if (ats, token) not in discovered:
                    discovered[(ats, token)] = job_id

    return [(ats, token, jid) for (ats, token), jid in discovered.items()]


def discover_and_store_boards(conn: sqlite3.Connection) -> tuple[int, list[tuple[str, str]]]:
    """Scan URLs, store new boards into company_boards, and return counts.

    Returns:
        (newly_added_count, list_of_newly_added_(ats, token))
    """
    candidates = scan_stored_urls(conn)
    newly_added: list[tuple[str, str]] = []

    for ats, token, job_id in candidates:
        inserted = upsert_board(conn, ats, token, job_id=job_id)
        if inserted:
            newly_added.append((ats, token))

    return len(newly_added), newly_added
