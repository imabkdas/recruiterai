"""Tests for sources.remoteok — RemoteOK API source."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import respx

from jobpilot.sources.remoteok import fetch_remoteok

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture()
def remoteok_data() -> list:
    with open(FIXTURES_DIR / "remoteok_response.json") as f:
        return json.load(f)


class TestRemoteOK:
    @respx.mock
    def test_fetch_remoteok_success(self, remoteok_data: list) -> None:
        """RemoteOK successfully parses listings and discards metadata item."""
        respx.get("https://remoteok.com/api").respond(
            status_code=200,
            json=remoteok_data,
        )
        jobs = fetch_remoteok()
        assert len(jobs) == 1
        j = jobs[0]
        assert j.source == "remoteok"
        assert j.source_job_id == "rok-101"
        assert j.company == "Automattic"
        assert j.title == "Senior Backend Engineer (Java)"
        assert j.remote_type == "remote"
        assert j.description is not None
        assert "Java" in j.description

    @respx.mock
    def test_fetch_remoteok_429(self) -> None:
        """RemoteOK handles 429 rate limit gracefully."""
        respx.get("https://remoteok.com/api").respond(status_code=429)
        jobs = fetch_remoteok()
        assert jobs == []
