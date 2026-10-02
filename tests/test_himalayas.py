"""Tests for the Himalayas public jobs API source."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import respx

from jobpilot.sources.himalayas import HIMALAYAS_API_URL, fetch_himalayas

FIXTURE = Path(__file__).parent / "fixtures" / "himalayas_response.json"


@respx.mock
def test_himalayas_parses_public_feed() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    respx.get(HIMALAYAS_API_URL).respond(200, json=payload)
    jobs = fetch_himalayas(client=httpx.Client())
    assert len(jobs) == 2
    assert jobs[0].source == "himalayas"
    assert jobs[0].company == "Northwind"
    assert jobs[0].title == "Senior Java Engineer"
    assert jobs[0].location == "India"
    assert "Spring Boot" in (jobs[0].description or "")
    assert jobs[0].posted_at is not None
    assert jobs[0].source_job_id == payload["jobs"][0]["guid"]
    assert jobs[1].description is None
    assert jobs[1].location is None


@respx.mock
def test_himalayas_http_error_raises() -> None:
    respx.get(HIMALAYAS_API_URL).respond(503)
    try:
        fetch_himalayas(client=httpx.Client())
    except httpx.HTTPStatusError:
        return
    raise AssertionError("expected HTTPStatusError")
