"""Tests for ATS board sources (Greenhouse, Lever, Ashby) and fetch orchestration."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
import respx

from jobpilot.config import AppConfig
from jobpilot.db import get_active_boards, get_connection, init_schema, upsert_board
from jobpilot.pipeline.fetcher import run_fetch
from jobpilot.sources.ashby import fetch_ashby_board
from jobpilot.sources.greenhouse import BoardNotFoundError, fetch_greenhouse_board
from jobpilot.sources.lever import fetch_lever_board

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = get_connection(":memory:")
    init_schema(c)
    return c


@pytest.fixture()
def gh_data() -> dict:
    with open(FIXTURES_DIR / "greenhouse_response.json") as f:
        return json.load(f)


@pytest.fixture()
def lever_data() -> list:
    with open(FIXTURES_DIR / "lever_response.json") as f:
        return json.load(f)


@pytest.fixture()
def ashby_data() -> dict:
    with open(FIXTURES_DIR / "ashby_response.json") as f:
        return json.load(f)


class TestBoardSources:
    @respx.mock
    def test_greenhouse_fetch_success(self, gh_data: dict) -> None:
        respx.get("https://boards-api.greenhouse.io/v1/boards/stripe/jobs").respond(
            status_code=200,
            json=gh_data,
        )
        jobs = fetch_greenhouse_board("stripe")
        assert len(jobs) == 1
        assert jobs[0].company == "stripe"
        assert jobs[0].source == "greenhouse"
        assert jobs[0].description is not None
        assert "Java" in jobs[0].title

    @respx.mock
    def test_greenhouse_404_raises(self) -> None:
        respx.get("https://boards-api.greenhouse.io/v1/boards/bad_token/jobs").respond(status_code=404)
        with pytest.raises(BoardNotFoundError):
            fetch_greenhouse_board("bad_token")

    @respx.mock
    def test_lever_fetch_success(self, lever_data: list) -> None:
        respx.get("https://api.lever.co/v0/postings/netflix").respond(
            status_code=200,
            json=lever_data,
        )
        jobs = fetch_lever_board("netflix")
        assert len(jobs) == 1
        assert jobs[0].company == "netflix"
        assert jobs[0].source == "lever"
        assert jobs[0].description is not None

    @respx.mock
    def test_lever_404_raises(self) -> None:
        respx.get("https://api.lever.co/v0/postings/bad_company").respond(status_code=404)
        with pytest.raises(BoardNotFoundError):
            fetch_lever_board("bad_company")

    @respx.mock
    def test_ashby_fetch_success(self, ashby_data: dict) -> None:
        """Ashby fetch includes only listed jobs and parses them properly."""
        respx.get("https://api.ashbyhq.com/posting-api/job-board/ramp").respond(
            status_code=200,
            json=ashby_data,
        )
        jobs = fetch_ashby_board("ramp")
        # Only 1 of the 2 jobs in fixture isListed=true
        assert len(jobs) == 1
        assert jobs[0].company == "ramp"
        assert jobs[0].source == "ashby"
        assert jobs[0].title == "Java Backend Engineer"

    @respx.mock
    def test_ashby_404_raises(self) -> None:
        respx.get("https://api.ashbyhq.com/posting-api/job-board/bad_board").respond(status_code=404)
        with pytest.raises(BoardNotFoundError):
            fetch_ashby_board("bad_board")


class TestFetchOrchestrationAndDeactivation:
    @respx.mock
    def test_404_marks_board_inactive(self, conn: sqlite3.Connection) -> None:
        """A 404 response during fetch marks the board as active=0 in company_boards."""
        # Seed an active board
        upsert_board(conn, "greenhouse", "dead_board")
        assert len(get_active_boards(conn)) == 1

        respx.get("https://boards-api.greenhouse.io/v1/boards/dead_board/jobs").respond(status_code=404)

        config = AppConfig()
        config.adzuna.enabled = False
        config.boards.max_per_run = 10

        summary = run_fetch(conn, source="greenhouse", config=config)
        assert ("greenhouse", "dead_board") in summary.deactivated_boards

        # Verify board is now inactive in DB
        active = get_active_boards(conn)
        assert len(active) == 0

        row = conn.execute("SELECT active FROM company_boards WHERE token = 'dead_board'").fetchone()
        assert row["active"] == 0

    @respx.mock
    def test_per_run_cap_and_relevant_hits_priority(self, conn: sqlite3.Connection) -> None:
        """Fetch respects boards.max_per_run and takes highest relevant_hits first."""
        # Insert 3 boards with different relevant_hits
        upsert_board(conn, "greenhouse", "board_low")
        upsert_board(conn, "greenhouse", "board_high")
        upsert_board(conn, "greenhouse", "board_mid")

        conn.execute("UPDATE company_boards SET relevant_hits = 100 WHERE token = 'board_high'")
        conn.execute("UPDATE company_boards SET relevant_hits = 50 WHERE token = 'board_mid'")
        conn.execute("UPDATE company_boards SET relevant_hits = 10 WHERE token = 'board_low'")
        conn.commit()

        # Mock all endpoints
        r_high = respx.get("https://boards-api.greenhouse.io/v1/boards/board_high/jobs").respond(
            status_code=200, json={"jobs": []}
        )
        r_mid = respx.get("https://boards-api.greenhouse.io/v1/boards/board_mid/jobs").respond(
            status_code=200, json={"jobs": []}
        )
        r_low = respx.get("https://boards-api.greenhouse.io/v1/boards/board_low/jobs").respond(
            status_code=200, json={"jobs": []}
        )

        config = AppConfig()
        config.adzuna.enabled = False
        config.boards.max_per_run = 2  # Limit to top 2 boards

        run_fetch(conn, source="greenhouse", config=config)

        # board_high and board_mid should be called, but NOT board_low
        assert r_high.called
        assert r_mid.called
        assert not r_low.called


def test_one_source_failure_does_not_stop_others(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    """A feed that raises is recorded as failed. Later feeds still ingest."""
    from jobpilot.models import RawJob

    config = AppConfig()
    config.adzuna.enabled = False
    config.sources.hn_hiring = False
    config.sources.himalayas = False
    config.sources.remoteok = True
    config.sources.remotive = True

    def boom() -> list[RawJob]:
        raise RuntimeError("remoteok unavailable")

    def ok() -> list[RawJob]:
        return [
            RawJob(
                source="remotive",
                company="Ok Co",
                title="Software Engineer",
                location="Remote",
                url="https://remotive.com/job/1",
                description="Build Java services for a remote team.",
            )
        ]

    monkeypatch.setattr("jobpilot.pipeline.fetcher.fetch_remoteok", boom)
    monkeypatch.setattr("jobpilot.pipeline.fetcher.fetch_remotive", ok)

    summary = run_fetch(conn, config=config)
    by_name = {item.name: item for item in summary.sources}
    assert by_name["remoteok"].status == "failed"
    assert by_name["remotive"].status == "ok"
    assert by_name["remotive"].found == 1
    assert by_name["remotive"].new == 1
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    row = conn.execute("SELECT status, error FROM source_runs WHERE source = 'remoteok'").fetchone()
    assert row["status"] == "failed"
    assert "unavailable" in row["error"]
