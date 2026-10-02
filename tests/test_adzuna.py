"""Tests for sources.adzuna — Adzuna API source, budget tracking, and needs_jd flow."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
import respx

from jobpilot.config import AdzunaConfig, EnvSettings
from jobpilot.db import get_api_usage, get_connection, init_schema
from jobpilot.pipeline.ingest import ingest_raw_jobs
from jobpilot.pipeline.prefilter import run_prefilter
from jobpilot.sources.adzuna import AdzunaSource

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = get_connection(":memory:")
    init_schema(c)
    return c


@pytest.fixture()
def adzuna_fixture_data() -> dict:
    with open(FIXTURES_DIR / "adzuna_response.json") as f:
        return json.load(f)


class TestAdzunaSource:
    @respx.mock
    def test_fetch_with_valid_credentials(self, conn: sqlite3.Connection, adzuna_fixture_data: dict) -> None:
        """Adzuna fetch successfully returns RawJobs with description=None and snippet populated."""
        respx.get("https://api.adzuna.com/v1/api/jobs/in/search/1").respond(
            status_code=200,
            json=adzuna_fixture_data,
        )

        config = AdzunaConfig(
            enabled=True,
            country="in",
            queries=["Software Engineer"],
            pages=1,
            max_days_old=21,
            monthly_call_budget=900,
        )
        env = EnvSettings(adzuna_app_id="test_app_id", adzuna_app_key="test_app_key")

        source = AdzunaSource(config=config, env=env, conn=conn)
        jobs = source.fetch()

        assert len(jobs) == 2
        j1 = jobs[0]
        assert j1.source == "adzuna"
        assert j1.source_job_id == "adzuna-1001"
        assert j1.title == "Senior Java Backend Engineer"
        assert j1.company == "Fintech Solutions India Pvt Ltd"
        assert j1.description is None  # Must be null per requirement
        assert "Spring Boot" in (j1.snippet or "")

        # Verify budget usage was tracked
        from datetime import UTC, datetime
        period = datetime.now(UTC).strftime("%Y-%m")
        usage = get_api_usage(conn, "adzuna", period)
        assert usage == 1

    @respx.mock
    def test_budget_limiter_stops_at_cap(self, conn: sqlite3.Connection, adzuna_fixture_data: dict) -> None:
        """When the monthly budget is reached, Adzuna stops cleanly without calling the API."""
        endpoint = respx.get("https://api.adzuna.com/v1/api/jobs/in/search/1").respond(
            status_code=200,
            json=adzuna_fixture_data,
        )

        config = AdzunaConfig(
            enabled=True,
            country="in",
            queries=["Software Engineer", "Java Backend"],
            pages=2,
            monthly_call_budget=2,  # Cap at 2 calls
        )
        env = EnvSettings(adzuna_app_id="test_id", adzuna_app_key="test_key")

        # Pre-seed the budget table to 2 calls
        from datetime import UTC, datetime

        from jobpilot.db import increment_api_usage
        period = datetime.now(UTC).strftime("%Y-%m")
        increment_api_usage(conn, "adzuna", period, count=2)

        source = AdzunaSource(config=config, env=env, conn=conn)
        jobs = source.fetch()

        # Should have stopped immediately due to budget
        assert len(jobs) == 0
        assert endpoint.call_count == 0

    def test_unsupported_country_skips(self, conn: sqlite3.Connection) -> None:
        """If an unsupported country code is configured, Adzuna skips without error."""
        config = AdzunaConfig(enabled=True, country="mars")
        env = EnvSettings(adzuna_app_id="id", adzuna_app_key="key")
        source = AdzunaSource(config=config, env=env, conn=conn)
        jobs = source.fetch()
        assert jobs == []

    def test_missing_credentials_skips(self, conn: sqlite3.Connection) -> None:
        """If app_id or app_key is empty, Adzuna skips without error."""
        config = AdzunaConfig(enabled=True, country="in")
        env = EnvSettings(adzuna_app_id="", adzuna_app_key="")
        source = AdzunaSource(config=config, env=env, conn=conn)
        jobs = source.fetch()
        assert jobs == []

    @respx.mock
    def test_truncated_adzuna_results_become_needs_jd(
        self,
        conn: sqlite3.Connection,
        adzuna_fixture_data: dict,
    ) -> None:
        """Adzuna jobs surviving prefilter must have status 'needs_jd' since description is null."""
        respx.get("https://api.adzuna.com/v1/api/jobs/in/search/1").respond(
            status_code=200,
            json=adzuna_fixture_data,
        )

        config = AdzunaConfig(enabled=True, country="in", queries=["Software Engineer"], pages=1)
        env = EnvSettings(adzuna_app_id="id", adzuna_app_key="key")

        source = AdzunaSource(config=config, env=env, conn=conn)
        raw_jobs = source.fetch()

        # Ingest
        ingest_res = ingest_raw_jobs(conn, raw_jobs)
        assert ingest_res.new == 2

        # Run prefilter
        run_prefilter(conn, job_ids=ingest_res.job_ids)

        # The Java engineer listing passes prefilter and must be 'needs_jd'
        row = conn.execute(
            "SELECT status, status_reason FROM jobs WHERE source_job_id = 'adzuna-1001'"
        ).fetchone()
        assert row["status"] == "needs_jd"
