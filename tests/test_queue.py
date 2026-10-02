"""Tests for daily queue builder (jobpilot.pipeline.queue)."""

from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

import pytest

from jobpilot.config import AppConfig, QueueConfig
from jobpilot.db import get_connection, init_schema
from jobpilot.pipeline.queue import build_queue


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    """Initialized SQLite database connection."""
    db_file = tmp_path / "test.db"
    c = get_connection(db_file)
    init_schema(c)
    return c


def _insert_job(
    conn: sqlite3.Connection,
    job_id: int,
    company: str,
    title: str,
    status: str,
    posted_at: str = "2026-10-01T00:00:00Z",
    location: str = "Remote",
    url: str = "https://example.com/job",
) -> None:
    conn.execute(
        """
        INSERT INTO jobs (id, fingerprint, source, company, title, location, url, discovered_at, posted_at, last_seen_at, status)
        VALUES (?, ?, 'greenhouse', ?, ?, ?, ?, '2026-10-01T00:00:00Z', ?, '2026-10-01T00:00:00Z', ?)
        """,
        (job_id, f"fp-{job_id}", company, title, location, url, posted_at, status),
    )
    conn.commit()


def _insert_score(
    conn: sqlite3.Connection,
    job_id: int,
    total: float,
    tier: str | None,
    matched_skills: list[dict] | None = None,
    missing_required: list[str] | None = None,
    missing_preferred: list[str] | None = None,
    flags: list[str] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO scores (job_id, total, tier, matched_skills, missing_required, missing_preferred, flags, scored_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, '2026-10-01T01:00:00Z')
        """,
        (
            job_id,
            total,
            tier,
            json.dumps(matched_skills or []),
            json.dumps(missing_required or []),
            json.dumps(missing_preferred or []),
            json.dumps(flags or []),
        ),
    )
    conn.commit()


class TestQueueBuilder:
    def test_queue_ordering_and_tier_priority(self, conn: sqlite3.Connection, tmp_path: Path) -> None:
        """Tier A jobs must always rank before Tier B jobs, followed by total score desc, then recency."""
        # Job 1: Tier B, score 78.0
        _insert_job(conn, 1, "BetaCo", "Backend Dev", "scored", posted_at="2026-10-01T10:00:00Z")
        _insert_score(conn, 1, 78.0, "B")

        # Job 2: Tier A, score 82.0
        _insert_job(conn, 2, "AlphaCo", "Senior Dev", "scored", posted_at="2026-10-01T08:00:00Z")
        _insert_score(conn, 2, 82.0, "A")

        # Job 3: Tier A, score 90.0
        _insert_job(conn, 3, "AcmeCorp", "Staff Dev", "scored", posted_at="2026-10-01T07:00:00Z")
        _insert_score(conn, 3, 90.0, "A")

        # Job 4: Tier B, score 78.0, more recent than Job 1
        _insert_job(conn, 4, "GammaCo", "Python Dev", "scored", posted_at="2026-10-01T12:00:00Z")
        _insert_score(conn, 4, 78.0, "B")

        res = build_queue(conn, size=10, out_dir=tmp_path, date_str="2026-10-01")

        # Expected order:
        # 1. Job 3 (Tier A, 90.0)
        # 2. Job 2 (Tier A, 82.0)
        # 3. Job 4 (Tier B, 78.0, posted 12:00)
        # 4. Job 1 (Tier B, 78.0, posted 10:00)
        job_ids = [item.job_id for item in res.items]
        assert job_ids == [3, 2, 4, 1]
        assert [item.rank for item in res.items] == [1, 2, 3, 4]

    def test_roll_forward_of_unfinished_items(self, conn: sqlite3.Connection, tmp_path: Path) -> None:
        """Unfinished items with status 'queued' or 'prepared' roll forward and compete with new 'scored' items."""
        # Unfinished item from yesterday: status 'queued', Tier A, score 85
        _insert_job(conn, 10, "OldCo", "Java Dev", "queued", posted_at="2026-09-30T10:00:00Z")
        _insert_score(conn, 10, 85.0, "A")

        # Unfinished item from yesterday: status 'prepared', Tier A, score 88
        _insert_job(conn, 11, "PreparedCo", "Lead Dev", "prepared", posted_at="2026-09-30T11:00:00Z")
        _insert_score(conn, 11, 88.0, "A")

        # New item scored today: status 'scored', Tier A, score 86
        _insert_job(conn, 12, "NewCo", "Senior Java Dev", "scored", posted_at="2026-10-01T09:00:00Z")
        _insert_score(conn, 12, 86.0, "A")

        res = build_queue(conn, size=5, out_dir=tmp_path, date_str="2026-10-01")

        # Expected order: 11 (88.0, prepared), 12 (86.0, scored), 10 (85.0, queued)
        job_ids = [item.job_id for item in res.items]
        assert job_ids == [11, 12, 10]

        # Check prepared flag
        prep_map = {item.job_id: item.prepared for item in res.items}
        assert prep_map[11] == "y"
        assert prep_map[12] == "n"
        assert prep_map[10] == "n"

        # Check status update: newly scored job 12 should now be 'queued'
        assert conn.execute("SELECT status FROM jobs WHERE id = 12").fetchone()["status"] == "queued"
        # Prepared job 11 remains 'prepared'
        assert conn.execute("SELECT status FROM jobs WHERE id = 11").fetchone()["status"] == "prepared"
        # Already queued job 10 remains 'queued'
        assert conn.execute("SELECT status FROM jobs WHERE id = 10").fetchone()["status"] == "queued"

    def test_exclusion_of_applied_skipped_and_unranked_jobs(
        self, conn: sqlite3.Connection, tmp_path: Path
    ) -> None:
        """Jobs that are applied, skipped, or have no tier (below threshold) must not appear in queue."""
        # Applied job
        _insert_job(conn, 20, "AppCo", "Dev", "applied")
        _insert_score(conn, 20, 95.0, "A")

        # Skipped job
        _insert_job(conn, 21, "SkipCo", "Dev", "skipped")
        _insert_score(conn, 21, 92.0, "A")

        # Below threshold (tier None)
        _insert_job(conn, 22, "LowFitCo", "Dev", "scored")
        _insert_score(conn, 22, 55.0, None)

        # Eligible job
        _insert_job(conn, 23, "EligibleCo", "Dev", "scored")
        _insert_score(conn, 23, 80.0, "A")

        res = build_queue(conn, size=10, out_dir=tmp_path, date_str="2026-10-01")
        assert len(res.items) == 1
        assert res.items[0].job_id == 23

    def test_needs_jd_section_separated(self, conn: sqlite3.Connection, tmp_path: Path) -> None:
        """Jobs in status 'needs_jd' appear in needs_jd list and table, not in ranked queue items."""
        _insert_job(conn, 30, "AlertCo", "Alert Title", "needs_jd", url="https://linkedin.com/alert/1")
        _insert_job(conn, 31, "RegularCo", "Dev", "scored")
        _insert_score(conn, 31, 85.0, "A")

        res = build_queue(conn, size=5, out_dir=tmp_path, date_str="2026-10-01")

        assert len(res.items) == 1
        assert res.items[0].job_id == 31

        assert len(res.needs_jd) == 1
        assert res.needs_jd[0].job_id == 30
        assert res.needs_jd[0].company == "AlertCo"

        output = res.format_table()
        assert "=== Needs Job Description (1) ===" in output
        assert "[30] AlertCo — Alert Title" in output
        assert "jobpilot add-jd" in output

    def test_csv_export_format_and_columns(self, conn: sqlite3.Connection, tmp_path: Path) -> None:
        """CSV exported to data/out/queue_YYYY-MM-DD.csv matches required 12 columns exactly."""
        _insert_job(conn, 40, "CsvCo", "Python Lead", "scored", location="Bengaluru")
        _insert_score(
            conn,
            40,
            87.5,
            "A",
            matched_skills=[{"skill": "Python"}, {"skill": "FastAPI"}],
            missing_required=["Kubernetes"],
            missing_preferred=["GraphQL"],
            flags=["salary_unknown"],
        )

        res = build_queue(conn, size=5, out_dir=tmp_path, date_str="2026-10-01", write_csv=True)
        assert res.csv_path is not None
        assert res.csv_path.exists()
        assert res.csv_path.name == "queue_2026-10-01.csv"

        with open(res.csv_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            assert len(rows) == 1
            r = rows[0]
            assert r["rank"] == "1"
            assert r["job_id"] == "40"
            assert r["company"] == "CsvCo"
            assert r["title"] == "Python Lead"
            assert r["location"] == "Bengaluru"
            assert r["tier"] == "A"
            assert r["score"] == "87.5"
            assert r["matched_skills"] == "Python, FastAPI"
            assert "Kubernetes (req)" in r["gaps"]
            assert "GraphQL (pref)" in r["gaps"]
            assert r["flags"] == "salary_unknown"
            assert r["url"] == "https://example.com/job"
            assert r["prepared"] == "n"

    def test_daily_size_limit_respected(self, conn: sqlite3.Connection, tmp_path: Path) -> None:
        """Queue size respects the limit argument or config queue.daily_size."""
        for i in range(1, 10):
            _insert_job(conn, i, f"Co{i}", f"Title{i}", "scored")
            _insert_score(conn, i, 80.0 + i, "A")

        cfg = AppConfig(queue=QueueConfig(daily_size=3))
        res = build_queue(conn, config=cfg, out_dir=tmp_path, date_str="2026-10-01")
        assert len(res.items) == 3

        # Explicit size override
        res_custom = build_queue(conn, config=cfg, size=5, out_dir=tmp_path, date_str="2026-10-01")
        assert len(res_custom.items) == 5

    def test_empty_queue_handled_cleanly(self, conn: sqlite3.Connection, tmp_path: Path) -> None:
        """When no jobs are eligible, empty queue is returned with informative table."""
        res = build_queue(conn, size=5, out_dir=tmp_path, date_str="2026-10-01")
        assert len(res.items) == 0
        assert len(res.needs_jd) == 0
        output = res.format_table()
        assert "No active jobs in queue" in output

    def test_expire_stale_jobs_in_queue(self, conn: sqlite3.Connection, tmp_path: Path) -> None:
        """Jobs older than max_job_age_days are marked expired and excluded from the queue."""
        # Job 50: Posted 25 days ago (stale)
        _insert_job(conn, 50, "StaleCo", "Old Dev", "queued", posted_at="2026-09-01T00:00:00Z")
        _insert_score(conn, 50, 92.0, "A")

        # Job 51: Posted 5 days ago (fresh)
        _insert_job(conn, 51, "FreshCo", "New Dev", "queued", posted_at="2026-09-26T00:00:00Z")
        _insert_score(conn, 51, 85.0, "A")

        # Run queue as of 2026-10-01 (default max_job_age_days is 21)
        res = build_queue(conn, size=10, out_dir=tmp_path, date_str="2026-10-01")

        # Stale job 50 was expired
        assert [item.job_id for item in res.items] == [51]
        stale_status = conn.execute("SELECT status, status_reason FROM jobs WHERE id = 50").fetchone()
        assert stale_status["status"] == "expired"
        assert "age_exceeded" in (stale_status["status_reason"] or "")
