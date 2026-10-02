"""Tests for SQLite WAL concurrency and multi-threaded service isolation."""

from __future__ import annotations

import concurrent.futures
from pathlib import Path

import pytest

from jobpilot import services
from jobpilot.db import get_connection


class TestWALConcurrency:
    def test_pragmas_wal_and_busy_timeout_configured(self, tmp_path: Path) -> None:
        """Every connection has WAL mode and busy_timeout=5000 set."""
        db_file = tmp_path / "pragmas.db"
        conn = get_connection(db_file)
        try:
            journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0].lower()
            assert journal_mode == "wal"

            busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
            assert int(busy_timeout) == 5000
        finally:
            conn.close()

    def test_concurrent_read_while_write_transaction_open_does_not_raise(self, tmp_path: Path) -> None:
        """In WAL mode, concurrent reads while a write transaction is open do not raise database locked."""
        db_file = tmp_path / "wal_concurrency.db"

        # Connection 1: set up initial data
        writer = get_connection(db_file)
        writer.execute(
            """
            INSERT INTO jobs (fingerprint, source, company, title, url, discovered_at, last_seen_at, status)
            VALUES ('fp-wal', 'greenhouse', 'WalCo', 'WalDev', 'https://example.com', '2026-10-01', '2026-10-01', 'new')
            """
        )
        writer.commit()

        # Connection 2: separate reader connection
        reader = get_connection(db_file)

        try:
            # Writer begins an active immediate write transaction
            writer.execute("BEGIN IMMEDIATE")
            writer.execute("UPDATE jobs SET status = 'queued' WHERE fingerprint = 'fp-wal'")

            # While write transaction is open and uncommitted, reader reads concurrently
            # In WAL mode, this must not raise sqlite3.OperationalError: database is locked
            row = reader.execute("SELECT status FROM jobs WHERE fingerprint = 'fp-wal'").fetchone()
            assert row is not None
            # WAL snapshot isolation guarantees reader sees pre-transaction state
            assert row["status"] == "new"

            # Writer commits changes
            writer.commit()

            # Reader now sees the committed state
            row_committed = reader.execute("SELECT status FROM jobs WHERE fingerprint = 'fp-wal'").fetchone()
            assert row_committed is not None
            assert row_committed["status"] == "queued"
        finally:
            writer.close()
            reader.close()

    def test_services_opens_new_connection_per_call_across_threads(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """services.py opens a new connection per call when conn=None, safe across threads."""
        db_file = tmp_path / "thread_services.db"
        init_conn = get_connection(db_file)
        init_conn.execute(
            """
            INSERT INTO jobs (fingerprint, source, company, title, url, discovered_at, last_seen_at, status)
            VALUES ('fp-thread', 'greenhouse', 'ThreadCo', 'ThreadDev', 'https://example.com', '2026-10-01', '2026-10-01', 'scored')
            """
        )
        init_conn.execute(
            """
            INSERT INTO scores (job_id, total, tier, skills_score, experience_score, scored_at)
            VALUES (1, 85.0, 'A', 35.0, 18.0, '2026-10-01')
            """
        )
        init_conn.commit()
        init_conn.close()

        # Point services._get_db_conn to test db_file
        def mock_get_conn(conn=None):
            if conn is not None:
                return conn, False
            return get_connection(db_file), True

        monkeypatch.setattr(services, "_get_db_conn", mock_get_conn)

        def worker_task(i: int) -> int:
            items = services.get_queue(size=5, conn=None)
            return len(items)

        # Run 8 concurrent calls across 4 threads
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(worker_task, i) for i in range(8)]
            results = [f.result() for f in futures]

        assert all(r == 1 for r in results)
