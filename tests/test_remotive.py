"""Tests for sources.remotive — Remotive API source."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import respx

from jobpilot.sources.remotive import fetch_remotive

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture()
def remotive_data() -> dict:
    with open(FIXTURES_DIR / "remotive_response.json") as f:
        return json.load(f)


class TestRemotive:
    @respx.mock
    def test_fetch_remotive_success(self, remotive_data: dict) -> None:
        """Remotive successfully parses jobs from jobs array."""
        respx.get("https://remotive.com/api/remote-jobs").respond(
            status_code=200,
            json=remotive_data,
        )
        jobs = fetch_remotive()
        assert len(jobs) == 1
        j = jobs[0]
        assert j.source == "remotive"
        assert j.source_job_id == "9901"
        assert j.company == "Toptal"
        assert j.title == "Senior Java Developer - Spring Boot"
        assert j.remote_type == "remote"
        assert j.description is not None

    @respx.mock
    def test_fetch_remotive_429(self) -> None:
        """Remotive handles 429 rate limit gracefully."""
        respx.get("https://remotive.com/api/remote-jobs").respond(status_code=429)
        jobs = fetch_remotive()
        assert jobs == []
