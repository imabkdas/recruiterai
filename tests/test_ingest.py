"""Tests for pipeline.ingest — normalization, fingerprinting, and deduplication."""

from __future__ import annotations

import sqlite3

import pytest

from jobpilot.db import get_connection, init_schema
from jobpilot.models import RawJob
from jobpilot.pipeline.ingest import (
    canonicalize_url,
    compute_fingerprint,
    ingest_raw_jobs,
    normalize_company,
    normalize_location,
    normalize_title,
)


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = get_connection(":memory:")
    init_schema(c)
    return c


# ---------------------------------------------------------------------------
# Normalization tests
# ---------------------------------------------------------------------------

class TestNormalization:
    @pytest.mark.parametrize("raw,expected", [
        ("Acme Technologies Pvt Ltd", "acme"),
        ("Google Inc.", "google"),
        ("Meta Platforms, LLC", "meta platforms"),
        ("Stripe, Inc", "stripe"),
        ("Example Corp.", "example"),
        ("TCS Private Limited", "tcs"),
        ("Startup Gmbh", "startup"),
    ])
    def test_normalize_company(self, raw: str, expected: str) -> None:
        assert normalize_company(raw) == expected

    @pytest.mark.parametrize("raw,expected", [
        ("Senior Software Engineer", "software engineer"),
        ("Sr. Software Engineer", "software engineer"),
        ("Lead Java Developer", "java developer"),
        ("SDE 2", "software engineer"),
        ("SWE III", "software engineer"),
        ("Junior Backend Engineer", "backend engineer"),
        ("Staff Platform Engineer", "platform engineer"),
    ])
    def test_normalize_title(self, raw: str, expected: str) -> None:
        assert normalize_title(raw) == expected

    @pytest.mark.parametrize("raw,expected", [
        ("Remote - India", "remote"),
        ("Work From Home", "remote"),
        ("Bengaluru, Karnataka, India", "bengaluru"),
        ("Pune, India", "pune"),
        ("San Francisco, CA", "san francisco"),
        (None, "unknown"),
        ("", "unknown"),
    ])
    def test_normalize_location(self, raw: str | None, expected: str) -> None:
        assert normalize_location(raw) == expected

    def test_compute_fingerprint_deterministic(self) -> None:
        fp1 = compute_fingerprint("Stripe Inc", "Sr. Software Engineer", "Remote")
        fp2 = compute_fingerprint("stripe", "Software Engineer", "work from home")
        assert fp1 == fp2


# ---------------------------------------------------------------------------
# Ingestion and deduplication tests
# ---------------------------------------------------------------------------

class TestIngestPipeline:
    def test_dedup_and_collision_handling(self, conn: sqlite3.Connection) -> None:
        """First listing has no description, second has full description. Richer description must be kept."""
        job1 = RawJob(
            source="adzuna",
            company="Acme Corp",
            title="Software Engineer",
            location="Remote",
            url="https://adzuna.in/job1",
            snippet="Short snippet",
            description=None,
        )
        res1 = ingest_raw_jobs(conn, [job1])
        assert res1.new == 1
        assert res1.duplicates == 0

        # Second job from a different source with full description
        job2 = RawJob(
            source="greenhouse",
            company="Acme",
            title="Sr Software Engineer",  # Normalizes to software engineer
            location="Remote",
            url="https://boards.greenhouse.io/acme/1",
            description="Full long description of the job...",
        )
        res2 = ingest_raw_jobs(conn, [job2])
        assert res2.new == 0
        assert res2.duplicates == 1

        # Check DB has kept the description from job2
        row = conn.execute("SELECT company, description FROM jobs").fetchone()
        assert row["description"] == "Full long description of the job..."

        urls = {row["url"] for row in conn.execute("SELECT url FROM job_urls")}
        assert "https://boards.greenhouse.io/acme/1" in urls
        assert "https://adzuna.in/job1" in urls


class TestCanonicalUrl:
    def test_strips_tracking_params(self) -> None:
        assert canonicalize_url("https://example.com/jobs/123?utm_source=linkedin") == (
            "https://example.com/jobs/123"
        )

    def test_keeps_identity_query_params(self) -> None:
        url = "https://example.com/jobs?gh_jid=99&utm_medium=email"
        assert canonicalize_url(url) == "https://example.com/jobs?gh_jid=99"

    def test_blank_and_malformed(self) -> None:
        assert canonicalize_url(None) is None
        assert canonicalize_url("") == ""
        assert canonicalize_url("   ") == ""
        assert canonicalize_url("not a url") == "not a url"
        assert canonicalize_url("https://example.com/jobs/123") == "https://example.com/jobs/123"


class TestSecondaryDedupe:
    def _job(self, **kwargs: object) -> RawJob:
        data = {
            "source": "adzuna",
            "company": "ABC",
            "title": "Senior Java Engineer",
            "location": "Bangalore",
            "url": "https://example.com/jobs/10",
        }
        data.update(kwargs)
        return RawJob(**data)  # type: ignore[arg-type]

    def test_same_fingerprint_is_one_job(self, conn: sqlite3.Connection) -> None:
        ingest_raw_jobs(conn, [self._job()])
        res = ingest_raw_jobs(conn, [self._job(url="https://other.example/jobs/9")])
        assert res.updated == 1
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1

    def test_tracking_params_collapse_to_one_job(self, conn: sqlite3.Connection) -> None:
        ingest_raw_jobs(
            conn,
            [self._job(company="Other Co", title="Analyst", url="https://example.com/jobs/123?utm_source=linkedin")],
        )
        res = ingest_raw_jobs(
            conn,
            [self._job(company="Different Co", title="Designer", url="https://example.com/jobs/123")],
        )
        assert res.updated == 1
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1

    def test_same_canonical_url_different_fingerprint_is_one_job(self, conn: sqlite3.Connection) -> None:
        ingest_raw_jobs(conn, [self._job(url="https://boards.example/jobs/55?ref=mail")])
        res = ingest_raw_jobs(
            conn,
            [self._job(company="Not ABC", title="Support", location="Delhi", url="https://boards.example/jobs/55")],
        )
        assert res.updated == 1
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1

    def test_different_fingerprint_and_url_stay_separate(self, conn: sqlite3.Connection) -> None:
        ingest_raw_jobs(conn, [self._job()])
        res = ingest_raw_jobs(
            conn,
            [self._job(company="Other", title="Designer", location="Pune", url="https://example.com/jobs/99")],
        )
        assert res.new == 1
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 2

    def test_same_role_from_three_sources_is_one_job(self, conn: sqlite3.Connection) -> None:
        listings = [
            self._job(source="gmail:linkedin", url="https://www.linkedin.com/jobs/view/12345?trackingId=abc"),
            self._job(source="gmail:naukri", url="https://www.naukri.com/job-listings-java-abc-98765"),
            self._job(source="adzuna", source_job_id="54321", url="https://www.adzuna.in/details/54321"),
        ]
        ingest_raw_jobs(conn, listings)
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
        urls = {row["url"] for row in conn.execute("SELECT url, source_job_id FROM job_urls")}
        assert len(urls) == 3
        ids = {
            row["source_job_id"]
            for row in conn.execute("SELECT source_job_id FROM job_urls")
        }
        assert "12345" in ids
        assert "98765" in ids
        assert "54321" in ids

    def test_freshness_fields(self, conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
        stamps = iter(["2026-01-01T00:00:00+00:00", "2026-01-02T00:00:00+00:00", "2026-01-03T00:00:00+00:00"])
        monkeypatch.setattr("jobpilot.db._now_iso", lambda: next(stamps))

        ingest_raw_jobs(conn, [self._job(posted_at=None)])
        row = conn.execute("SELECT discovered_at, last_seen_at, posted_at FROM jobs").fetchone()
        assert row["discovered_at"] == "2026-01-01T00:00:00+00:00"
        assert row["last_seen_at"] == "2026-01-01T00:00:00+00:00"
        assert row["posted_at"] is None

        ingest_raw_jobs(conn, [self._job(posted_at="2026-01-02")])
        row = conn.execute("SELECT discovered_at, last_seen_at, posted_at FROM jobs").fetchone()
        assert row["discovered_at"] == "2026-01-01T00:00:00+00:00"
        assert row["last_seen_at"] == "2026-01-02T00:00:00+00:00"
        assert row["posted_at"] == "2026-01-02"

        ingest_raw_jobs(conn, [self._job(posted_at="2026-09-09")])
        row = conn.execute("SELECT posted_at, discovered_at FROM jobs").fetchone()
        assert row["posted_at"] == "2026-01-02"
        assert row["discovered_at"] == "2026-01-01T00:00:00+00:00"
