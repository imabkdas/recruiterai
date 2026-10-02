"""Tests for jobpilot.db — schema creation and repository functions."""

from __future__ import annotations

import json
import sqlite3

import pytest

from jobpilot.db import (
    SCHEMA_VERSION,
    get_connection,
    get_jobs_by_status,
    init_schema,
    save_analysis,
    save_score,
    update_status,
    upsert_application,
    upsert_job,
)


@pytest.fixture()
def conn() -> sqlite3.Connection:
    """In-memory database with schema initialised."""
    c = get_connection(":memory:")
    init_schema(c)
    return c


def _sample_job(**overrides) -> dict:
    defaults = {
        "fingerprint": "abc123",
        "source": "test",
        "source_job_id": "t-1",
        "company": "TestCo",
        "title": "Software Engineer",
        "location": "Remote",
        "remote_type": "remote",
        "url": "https://example.com/job/1",
        "apply_url": None,
        "description": "A test job description.",
        "snippet": None,
        "posted_at": None,
        "raw_json": None,
    }
    defaults.update(overrides)
    return defaults


# ---------------------------------------------------------------------------
# Schema init
# ---------------------------------------------------------------------------

class TestSchemaInit:
    def test_creates_tables(self, conn: sqlite3.Connection) -> None:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        expected = {"schema_version", "jobs", "job_urls", "analyses", "scores", "applications", "llm_usage", "answer_bank"}
        assert expected.issubset(tables)

    def test_idempotent(self, conn: sqlite3.Connection) -> None:
        """Calling init_schema twice should not raise or duplicate data."""
        init_schema(conn)
        init_schema(conn)
        rows = conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()
        assert rows[0] == 1

    def test_schema_version(self, conn: sqlite3.Connection) -> None:
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        assert row[0] == SCHEMA_VERSION

    def test_missing_columns_are_added_to_an_existing_database(
        self, conn: sqlite3.Connection
    ) -> None:
        """A database created before resume paths existed is upgraded in place."""
        conn.execute("ALTER TABLE applications DROP COLUMN resume_pdf_path")
        conn.execute("UPDATE schema_version SET version = 1")
        conn.commit()

        init_schema(conn)

        columns = {r["name"] for r in conn.execute("PRAGMA table_info(applications)")}
        assert "resume_pdf_path" in columns
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == (
            SCHEMA_VERSION
        )


# ---------------------------------------------------------------------------
# upsert_job
# ---------------------------------------------------------------------------

class TestUpsertJob:
    def test_insert_new(self, conn: sqlite3.Connection) -> None:
        job_id = upsert_job(conn, _sample_job())
        assert job_id is not None
        assert job_id > 0

    def test_duplicate_fingerprint_updates_last_seen(self, conn: sqlite3.Connection) -> None:
        upsert_job(conn, _sample_job())
        job_id2 = upsert_job(conn, _sample_job(url="https://other.com/job/1"))
        # Should return same job
        rows = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()
        assert rows[0] == 1
        # Extra URL recorded
        urls = conn.execute("SELECT url FROM job_urls WHERE job_id = ?", (job_id2,)).fetchall()
        assert len(urls) >= 1

    def test_keeps_richer_description(self, conn: sqlite3.Connection) -> None:
        upsert_job(conn, _sample_job(description=None))
        upsert_job(conn, _sample_job(description="Full JD text here"))
        row = conn.execute("SELECT description FROM jobs WHERE fingerprint = 'abc123'").fetchone()
        assert row[0] == "Full JD text here"

    def test_does_not_overwrite_existing_description(self, conn: sqlite3.Connection) -> None:
        upsert_job(conn, _sample_job(description="Original description"))
        upsert_job(conn, _sample_job(description=None))
        row = conn.execute("SELECT description FROM jobs WHERE fingerprint = 'abc123'").fetchone()
        assert row[0] == "Original description"


# ---------------------------------------------------------------------------
# get_jobs_by_status
# ---------------------------------------------------------------------------

class TestGetJobsByStatus:
    def test_filter_by_status(self, conn: sqlite3.Connection) -> None:
        upsert_job(conn, _sample_job(fingerprint="a1"))
        upsert_job(conn, _sample_job(fingerprint="a2"))
        update_status(conn, 1, "analyzed")
        new_jobs = get_jobs_by_status(conn, "new")
        analyzed_jobs = get_jobs_by_status(conn, "analyzed")
        assert len(new_jobs) == 1
        assert len(analyzed_jobs) == 1


# ---------------------------------------------------------------------------
# update_status
# ---------------------------------------------------------------------------

class TestUpdateStatus:
    def test_updates_status_and_reason(self, conn: sqlite3.Connection) -> None:
        upsert_job(conn, _sample_job())
        update_status(conn, 1, "filtered_out", reason="title_exclude:intern")
        row = conn.execute("SELECT status, status_reason FROM jobs WHERE id = 1").fetchone()
        assert row[0] == "filtered_out"
        assert row[1] == "title_exclude:intern"


# ---------------------------------------------------------------------------
# save_analysis
# ---------------------------------------------------------------------------

class TestSaveAnalysis:
    def test_insert_and_replace(self, conn: sqlite3.Connection) -> None:
        upsert_job(conn, _sample_job())
        save_analysis(conn, 1, "v1", "gemini-flash", "hash1", '{"key": "val"}')
        row = conn.execute("SELECT analysis_json FROM analyses WHERE job_id = 1").fetchone()
        assert json.loads(row[0]) == {"key": "val"}

        # Replace same cache key
        save_analysis(conn, 1, "v1", "gemini-flash", "hash1", '{"key": "updated"}')
        row = conn.execute("SELECT analysis_json FROM analyses WHERE job_id = 1").fetchone()
        assert json.loads(row[0]) == {"key": "updated"}


# ---------------------------------------------------------------------------
# save_score
# ---------------------------------------------------------------------------

class TestSaveScore:
    def test_insert_score(self, conn: sqlite3.Connection) -> None:
        upsert_job(conn, _sample_job())
        save_score(conn, {
            "job_id": 1,
            "total": 85.5,
            "skills_score": 35.0,
            "experience_score": 18.0,
            "seniority_score": 12.0,
            "location_score": 15.0,
            "extras_score": 5.5,
            "tier": "A",
            "matched_skills": ["Java", "Spring Boot"],
            "missing_required": ["Go"],
            "missing_preferred": [],
            "flags": ["asks_5plus_years"],
        })
        row = conn.execute("SELECT total, tier FROM scores WHERE job_id = 1").fetchone()
        assert row[0] == 85.5
        assert row[1] == "A"


# ---------------------------------------------------------------------------
# upsert_application
# ---------------------------------------------------------------------------

class TestUpsertApplication:
    def test_insert_and_update(self, conn: sqlite3.Connection) -> None:
        upsert_job(conn, _sample_job())
        app_id = upsert_application(conn, {
            "job_id": 1,
            "tailored_summary": "A summary.",
            "channel": "linkedin",
        })
        assert app_id is not None

        # Update
        app_id2 = upsert_application(conn, {
            "job_id": 1,
            "channel": "naukri",
        })
        assert app_id2 == app_id
        row = conn.execute("SELECT channel FROM applications WHERE job_id = 1").fetchone()
        assert row[0] == "naukri"

    def test_update_serializes_bullet_ids(self, conn: sqlite3.Connection) -> None:
        upsert_job(conn, _sample_job())
        upsert_application(conn, {"job_id": 1, "short_note": "first"})
        upsert_application(conn, {"job_id": 1, "bullet_ids": ["b1", "b2"], "short_note": "second"})
        row = conn.execute("SELECT bullet_ids, short_note FROM applications WHERE job_id = 1").fetchone()
        assert json.loads(row["bullet_ids"]) == ["b1", "b2"]
        assert row["short_note"] == "second"
