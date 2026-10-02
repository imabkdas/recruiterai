"""Tests for sources.manual — add-url and add-jd transitions."""

from __future__ import annotations

import sqlite3

import pytest

from jobpilot.db import get_connection, init_schema
from jobpilot.sources.manual import add_jd_to_job, add_url_job


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    db_path = tmp_path / "test.db"
    c = get_connection(db_path)
    init_schema(c)
    return c


def test_add_url_basic(conn: sqlite3.Connection) -> None:
    job_id = add_url_job(
        conn,
        url="https://jobs.example.com/roles/123",
        title="Backend Engineer",
        company="ExampleCorp",
    )
    assert job_id > 0

    row = conn.execute("SELECT company, title, url, status, description FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row["company"] == "ExampleCorp"
    assert row["title"] == "Backend Engineer"
    assert row["url"] == "https://jobs.example.com/roles/123"
    assert row["status"] == "needs_jd"
    assert row["description"] is None


def test_add_url_inferred_company(conn: sqlite3.Connection) -> None:
    job_id = add_url_job(conn, url="https://careers.google.com/jobs/results/456")
    row = conn.execute("SELECT company, status FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row["company"] == "Google"
    assert row["status"] == "needs_jd"


def test_add_url_empty_raises(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="URL cannot be empty"):
        add_url_job(conn, url="   ")


def test_add_jd_transitions_existing_to_new(conn: sqlite3.Connection) -> None:
    job_id = add_url_job(conn, url="https://example.com/job/1")
    # Verify initial status is needs_jd
    row = conn.execute("SELECT status, description FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row["status"] == "needs_jd"
    assert row["description"] is None

    # Add JD
    returned_id = add_jd_to_job(conn, str(job_id), "We need a Senior Java developer.")
    assert returned_id == job_id

    row = conn.execute("SELECT status, description, status_reason FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row["status"] == "new"
    assert row["description"] == "We need a Senior Java developer."
    assert row["status_reason"] is None


def test_add_jd_keeps_status_when_already_applied(conn: sqlite3.Connection) -> None:
    job_id = add_url_job(conn, url="https://example.com/job/applied")
    conn.execute("UPDATE jobs SET status = 'applied' WHERE id = ?", (job_id,))
    conn.commit()
    add_jd_to_job(conn, str(job_id), "The full posting text.")
    row = conn.execute("SELECT status, description FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row["status"] == "applied"
    assert row["description"] == "The full posting text."


def test_add_jd_new_flag(conn: sqlite3.Connection) -> None:
    job_id = add_jd_to_job(
        conn,
        "--new",
        "Full stack engineer with React and Java experience.",
        title="Full Stack Engineer",
        company="TechCo",
    )
    assert job_id > 0

    row = conn.execute("SELECT company, title, status, description FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row["company"] == "TechCo"
    assert row["title"] == "Full Stack Engineer"
    assert row["status"] == "new"
    assert "React and Java" in row["description"]


def test_add_jd_nonexistent_job_raises(conn: sqlite3.Connection) -> None:
    with pytest.raises(KeyError, match="Job 9999 not found"):
        add_jd_to_job(conn, "9999", "Some description")


def test_add_jd_invalid_id_raises(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="Invalid job ID 'abc'"):
        add_jd_to_job(conn, "abc", "Some description")


def test_add_jd_empty_description_raises(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="description text cannot be empty"):
        add_jd_to_job(conn, "--new", "   ")
