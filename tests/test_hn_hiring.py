"""Tests for sources.hn_hiring — Hacker News 'Who is hiring?' source."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import respx

from jobpilot.sources.hn_hiring import _parse_first_line, fetch_hn_hiring

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture()
def hn_search_data() -> dict:
    with open(FIXTURES_DIR / "hn_search_response.json") as f:
        return json.load(f)


@pytest.fixture()
def hn_item_data() -> dict:
    with open(FIXTURES_DIR / "hn_item_response.json") as f:
        return json.load(f)


class TestHNHiring:
    def test_parse_first_line(self) -> None:
        raw = "Stripe | Senior Backend Engineer | San Francisco, CA | REMOTE\nHere is what we do..."
        company, title, loc = _parse_first_line(raw)
        assert company == "Stripe"
        assert title == "Senior Backend Engineer"
        assert "San Francisco" in (loc or "")

    def test_parse_html_entities_and_domain_header(self) -> None:
        raw = (
            "whitecircle.com&#x2F;careers, we&#x27;re building the most advanced "
            "AI safety infra, raised roughly $70m in total, join us"
        )
        company, title, _loc = _parse_first_line(raw)
        assert company == "Whitecircle"
        assert title == "Software Engineer"

    def test_parse_pipe_header_with_html_link(self) -> None:
        raw = (
            "Enveritas (YC S18, non-profit) | Backend Software Engineer | Remote (Global) | "
            '<a href="https:&#x2F;&#x2F;enveritas.org&#x2F;jobs&#x2F;">link</a>'
        )
        company, title, loc = _parse_first_line(raw)
        assert company == "Enveritas (YC S18, non-profit)"
        assert title == "Backend Software Engineer"
        assert loc is not None
        assert "Remote" in loc

    def test_parse_sentence_without_company_is_unknown(self) -> None:
        raw = "It&#x27;s my duty again to point out the popular request"
        company, title, _loc = _parse_first_line(raw)
        assert company == "Unknown"
        assert title == "Software Engineer"

    @respx.mock
    def test_fetch_hn_hiring_success(self, hn_search_data: dict, hn_item_data: dict) -> None:
        """HN hiring fetches latest thread and parses active comments."""
        respx.get("https://hn.algolia.com/api/v1/search_by_date").respond(
            status_code=200,
            json=hn_search_data,
        )
        respx.get("https://hn.algolia.com/api/v1/items/41000000").respond(
            status_code=200,
            json=hn_item_data,
        )

        jobs = fetch_hn_hiring()
        assert len(jobs) == 1
        j = jobs[0]
        assert j.source == "hn_hiring"
        assert j.source_job_id == "41000101"
        assert j.company == "Acme Corp"
        assert "Java" in j.title
        assert j.url == "https://news.ycombinator.com/item?id=41000101"
        assert "Spring Boot" in (j.description or "")
