"""Tests for status tracking and transition logic (jobpilot.tracking.status)."""

from __future__ import annotations

import sqlite3

import pytest

from jobpilot.db import get_connection, init_schema
from jobpilot.tracking.status import (
    ALLOWED_TRANSITIONS,
    InvalidStatusTransitionError,
    mark_job_status,
)


@pytest.fixture
def conn() -> sqlite3.Connection:
    """In-memory database initialized with schema."""
    c = get_connection(":memory:")
    init_schema(c)
    return c


def _insert_job(conn: sqlite3.Connection, job_id: int, status: str = "new") -> None:
    """Helper to insert a minimal job row."""
    conn.execute(
        """
        INSERT INTO jobs (id, fingerprint, source, company, title, url, discovered_at, last_seen_at, status)
        VALUES (?, ?, 'greenhouse', 'TestCo', 'Engineer', 'https://example.com/job', '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z', ?)
        """,
        (job_id, f"fp-{job_id}", status),
    )
    conn.commit()


class TestStatusTransitions:
    def test_valid_transitions_lifecycle(self, conn: sqlite3.Connection) -> None:
        """Walk through the full positive lifecycle: new -> analyzed -> scored -> queued -> prepared -> applied -> interview -> offer."""
        _insert_job(conn, 1, "new")

        mark_job_status(conn, 1, "analyzed")
        assert conn.execute("SELECT status FROM jobs WHERE id = 1").fetchone()["status"] == "analyzed"

        mark_job_status(conn, 1, "scored")
        assert conn.execute("SELECT status FROM jobs WHERE id = 1").fetchone()["status"] == "scored"

        mark_job_status(conn, 1, "queued")
        assert conn.execute("SELECT status FROM jobs WHERE id = 1").fetchone()["status"] == "queued"

        mark_job_status(conn, 1, "prepared")
        assert conn.execute("SELECT status FROM jobs WHERE id = 1").fetchone()["status"] == "prepared"

        mark_job_status(conn, 1, "applied", channel="linkedin", note="Referral via Alice")
        job_row = conn.execute("SELECT status FROM jobs WHERE id = 1").fetchone()
        assert job_row["status"] == "applied"

        app_row = conn.execute("SELECT * FROM applications WHERE job_id = 1").fetchone()
        assert app_row is not None
        assert app_row["channel"] == "linkedin"
        assert app_row["notes"] == "Referral via Alice"
        assert app_row["applied_at"] is not None

        mark_job_status(conn, 1, "interview", note="Screening scheduled")
        assert conn.execute("SELECT status FROM jobs WHERE id = 1").fetchone()["status"] == "interview"
        app_row2 = conn.execute("SELECT notes FROM applications WHERE job_id = 1").fetchone()
        assert app_row2["notes"] == "Screening scheduled"

        mark_job_status(conn, 1, "offer")
        assert conn.execute("SELECT status FROM jobs WHERE id = 1").fetchone()["status"] == "offer"

    def test_invalid_transition_raises(self, conn: sqlite3.Connection) -> None:
        """Attempting to jump illegally (e.g. from new to interview) must raise InvalidStatusTransitionError."""
        _insert_job(conn, 2, "new")

        with pytest.raises(InvalidStatusTransitionError) as exc_info:
            mark_job_status(conn, 2, "interview")
        assert "Cannot transition job 2 from 'new' to 'interview'" in str(exc_info.value)

    def test_mark_applied_without_prior_application_row(self, conn: sqlite3.Connection) -> None:
        """Marking applied on an unprepared job must create an application row with applied_at set."""
        # Tier B or manually found job in 'scored'
        _insert_job(conn, 10, "scored")
        assert conn.execute("SELECT id FROM applications WHERE job_id = 10").fetchone() is None

        mark_job_status(conn, 10, "applied", channel="linkedin", note="Applied manually via portal")

        job_row = conn.execute("SELECT status FROM jobs WHERE id = 10").fetchone()
        assert job_row["status"] == "applied"

        app_row = conn.execute("SELECT * FROM applications WHERE job_id = 10").fetchone()
        assert app_row is not None
        assert app_row["job_id"] == 10
        assert app_row["applied_at"] is not None
        assert app_row["channel"] == "linkedin"
        assert app_row["notes"] == "Applied manually via portal"

    def test_mark_applied_from_needs_jd_and_analysis_failed(self, conn: sqlite3.Connection) -> None:
        """Manual application directly from needs_jd and analysis_failed is permitted."""
        # From needs_jd (e.g. applied directly from email alert link)
        _insert_job(conn, 11, "needs_jd")
        mark_job_status(conn, 11, "applied", channel="naukri")
        assert conn.execute("SELECT status FROM jobs WHERE id = 11").fetchone()["status"] == "applied"

        # From analysis_failed (e.g. LLM extraction failed but user applies anyway)
        _insert_job(conn, 12, "analysis_failed")
        mark_job_status(conn, 12, "applied", channel="referral")
        assert conn.execute("SELECT status FROM jobs WHERE id = 12").fetchone()["status"] == "applied"

        # Retrying analysis_failed moves it back to new
        _insert_job(conn, 13, "analysis_failed")
        mark_job_status(conn, 13, "new")
        assert conn.execute("SELECT status FROM jobs WHERE id = 13").fetchone()["status"] == "new"

    def test_terminal_states_cannot_transition(self, conn: sqlite3.Connection) -> None:
        """Terminal states like rejected, skipped, expired have no further transitions."""
        _insert_job(conn, 3, "rejected")
        with pytest.raises(InvalidStatusTransitionError):
            mark_job_status(conn, 3, "interview")

        _insert_job(conn, 4, "skipped")
        with pytest.raises(InvalidStatusTransitionError):
            mark_job_status(conn, 4, "queued")

    def test_same_status_update_allowed(self, conn: sqlite3.Connection) -> None:
        """Updating channel or notes on a job with the same status is allowed."""
        _insert_job(conn, 5, "applied")
        mark_job_status(conn, 5, "applied", channel="email", note="Updated note")
        app_row = conn.execute("SELECT channel, notes FROM applications WHERE job_id = 5").fetchone()
        assert app_row["channel"] == "email"
        assert app_row["notes"] == "Updated note"

    def test_unknown_job_raises_key_error(self, conn: sqlite3.Connection) -> None:
        """Marking a non-existent job ID raises KeyError."""
        with pytest.raises(KeyError) as exc_info:
            mark_job_status(conn, 9999, "applied")
        assert "Job 9999 not found" in str(exc_info.value)

    def test_all_transitions_in_table_covered(self) -> None:
        """Sanity check on ALLOWED_TRANSITIONS graph."""
        assert "new" in ALLOWED_TRANSITIONS
        assert "scored" in ALLOWED_TRANSITIONS
        assert "applied" in ALLOWED_TRANSITIONS
        assert "rejected" in ALLOWED_TRANSITIONS["applied"]
        assert "interview" in ALLOWED_TRANSITIONS["applied"]
        assert "analysis_failed" in ALLOWED_TRANSITIONS
