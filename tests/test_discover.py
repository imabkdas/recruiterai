"""Tests for pipeline.discover — ATS board extraction, normalization, and deduplication."""

from __future__ import annotations

import sqlite3

import pytest

from jobpilot.db import get_connection, init_schema
from jobpilot.pipeline.discover import (
    discover_and_store_boards,
    extract_ats_board,
    scan_stored_urls,
)


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = get_connection(":memory:")
    init_schema(c)
    return c


# ---------------------------------------------------------------------------
# Table-driven URL pattern tests
# ---------------------------------------------------------------------------

_URL_PATTERN_CASES = [
    # Greenhouse
    ("https://boards.greenhouse.io/stripe", ("greenhouse", "stripe")),
    ("https://boards.greenhouse.io/stripe/", ("greenhouse", "stripe")),
    ("https://boards.greenhouse.io/STRIPE/jobs/12345", ("greenhouse", "stripe")),
    ("https://boards.greenhouse.io/stripe/jobs/12345/", ("greenhouse", "stripe")),
    ("https://boards.greenhouse.io/embed/job_board?for=stripe", ("greenhouse", "stripe")),
    ("https://boards.greenhouse.io/embed/job_board?for=STRIPE&another=param", ("greenhouse", "stripe")),
    ("https://job-boards.greenhouse.io/stripe", ("greenhouse", "stripe")),
    ("https://job-boards.greenhouse.io/stripe/jobs/9999", ("greenhouse", "stripe")),
    ("http://boards.greenhouse.io/stripe", ("greenhouse", "stripe")),
    ("https://custom.subdomain.greenhouse.io/stripe", ("greenhouse", "stripe")),

    # Lever
    ("https://jobs.lever.co/netflix", ("lever", "netflix")),
    ("https://jobs.lever.co/netflix/", ("lever", "netflix")),
    ("https://jobs.lever.co/NETFLIX/abc-123", ("lever", "netflix")),
    ("https://jobs.lever.co/netflix/abc-123/", ("lever", "netflix")),
    ("https://jobs.lever.co/netflix/abc-123/apply", ("lever", "netflix")),
    ("http://jobs.lever.co/netflix", ("lever", "netflix")),

    # Ashby
    ("https://jobs.ashbyhq.com/ramp", ("ashby", "ramp")),
    ("https://jobs.ashbyhq.com/ramp/", ("ashby", "ramp")),
    ("https://jobs.ashbyhq.com/RAMP/xyz-789", ("ashby", "ramp")),
    ("https://jobs.ashbyhq.com/ramp/xyz-789/", ("ashby", "ramp")),
    ("http://jobs.ashbyhq.com/ramp", ("ashby", "ramp")),

    # Non-ATS / Invalid URLs
    ("https://linkedin.com/jobs/view/12345", None),
    ("https://naukri.com/job-listings-12345", None),
    ("https://google.com/careers", None),
    ("https://example.com/greenhouse/not_ats", None),
    ("https://example.com/lever/not_ats", None),
    ("https://example.com/ashby/not_ats", None),
    ("", None),
    ("   ", None),
    ("not-a-valid-url", None),
]


@pytest.mark.parametrize("url,expected", _URL_PATTERN_CASES)
def test_extract_ats_board(url: str, expected: tuple[str, str] | None) -> None:
    assert extract_ats_board(url) == expected


# ---------------------------------------------------------------------------
# Stored URL scanning and deduplication
# ---------------------------------------------------------------------------

class TestDiscoverAndStore:
    def test_deduplication_across_urls(self, conn: sqlite3.Connection) -> None:
        """Multiple jobs pointing to the same board should deduplicate to a single entry."""
        # Insert 3 jobs with the same Greenhouse board in different forms
        conn.execute(
            """
            INSERT INTO jobs (id, fingerprint, source, company, title, url, apply_url, discovered_at, last_seen_at)
            VALUES (1, 'fp1', 'manual', 'Stripe', 'SWE', 'https://boards.greenhouse.io/stripe/jobs/1', 'https://boards.greenhouse.io/stripe', '2026-09-01', '2026-09-01'),
                   (2, 'fp2', 'manual', 'Stripe', 'Sr SWE', 'https://job-boards.greenhouse.io/stripe/jobs/2', NULL, '2026-09-01', '2026-09-01'),
                   (3, 'fp3', 'manual', 'Netflix', 'SWE', 'https://jobs.lever.co/netflix/abc', NULL, '2026-09-01', '2026-09-01')
            """
        )
        # Also add a duplicate in job_urls
        conn.execute(
            "INSERT INTO job_urls (job_id, source, url) VALUES (1, 'feed', 'https://boards.greenhouse.io/stripe')"
        )
        conn.commit()

        candidates = scan_stored_urls(conn)
        # Should have found stripe (greenhouse) and netflix (lever)
        boards = {(ats, token) for ats, token, _ in candidates}
        assert boards == {("greenhouse", "stripe"), ("lever", "netflix")}

        # Store boards
        new_count, new_boards = discover_and_store_boards(conn)
        assert new_count == 2
        assert set(new_boards) == {("greenhouse", "stripe"), ("lever", "netflix")}

        # Second discovery should add 0 new boards (idempotent)
        count_again, new_again = discover_and_store_boards(conn)
        assert count_again == 0
        assert new_again == []

        # Check DB rows
        rows = conn.execute("SELECT ats, token, active FROM company_boards ORDER BY ats").fetchall()
        assert len(rows) == 2
        assert rows[0]["ats"] == "greenhouse"
        assert rows[0]["token"] == "stripe"
        assert rows[0]["active"] == 1
        assert rows[1]["ats"] == "lever"
        assert rows[1]["token"] == "netflix"
        assert rows[1]["active"] == 1
