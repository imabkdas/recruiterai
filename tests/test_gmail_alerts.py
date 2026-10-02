"""Tests for sources.gmail_alerts — parsers, idempotency, and needs_jd status."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from jobpilot.db import get_connection, init_schema
from jobpilot.models import RawJob
from jobpilot.pipeline.ingest import ingest_raw_jobs
from jobpilot.sources.gmail_alerts import (
    GMAIL_READONLY_SCOPE,
    GmailOAuthClient,
    ingest_gmail_alerts,
    parse_alert_email,
    parse_instahyre_alert,
    parse_linkedin_alert,
    parse_naukri_alert,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "alerts"


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = get_connection(":memory:")
    init_schema(c)
    return c


@pytest.fixture()
def linkedin_1_html() -> str:
    return (FIXTURES_DIR / "linkedin_1.html").read_text(encoding="utf-8")


@pytest.fixture()
def linkedin_2_html() -> str:
    return (FIXTURES_DIR / "linkedin_2.html").read_text(encoding="utf-8")


@pytest.fixture()
def linkedin_malformed_html() -> str:
    return (FIXTURES_DIR / "linkedin_malformed.html").read_text(encoding="utf-8")


@pytest.fixture()
def naukri_1_html() -> str:
    return (FIXTURES_DIR / "naukri_1.html").read_text(encoding="utf-8")


@pytest.fixture()
def naukri_2_html() -> str:
    return (FIXTURES_DIR / "naukri_2.html").read_text(encoding="utf-8")


@pytest.fixture()
def naukri_malformed_html() -> str:
    return (FIXTURES_DIR / "naukri_malformed.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Scope restriction test
# ---------------------------------------------------------------------------

def test_gmail_readonly_scope_enforced() -> None:
    """OAuth client must request ONLY gmail.readonly scope."""
    client = GmailOAuthClient()
    assert client.scope == GMAIL_READONLY_SCOPE
    assert "readonly" in client.scope
    assert "modify" not in client.scope
    assert "send" not in client.scope
    assert "delete" not in client.scope


# ---------------------------------------------------------------------------
# Token expiry handling
# ---------------------------------------------------------------------------

class TestTokenExpiry:
    def test_token_without_expiry_is_treated_as_expired(self) -> None:
        """An undated token must be refreshed, not used until Gmail returns 401."""
        client = GmailOAuthClient()
        assert client._is_token_expired({"access_token": "abc"}) is True

    def test_future_expiry_is_valid(self) -> None:
        client = GmailOAuthClient()
        future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        assert client._is_token_expired({"access_token": "abc", "expiry": future}) is False

    def test_past_and_imminent_expiry_are_expired(self) -> None:
        client = GmailOAuthClient()
        past = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        assert client._is_token_expired({"access_token": "abc", "expiry": past}) is True
        # Inside the 60s refresh margin.
        imminent = (datetime.now(UTC) + timedelta(seconds=30)).isoformat()
        assert client._is_token_expired({"access_token": "abc", "expiry": imminent}) is True

    def test_unparseable_expiry_is_expired(self) -> None:
        client = GmailOAuthClient()
        assert client._is_token_expired({"access_token": "abc", "expiry": "not-a-date"}) is True

    def test_save_token_records_absolute_expiry(self, tmp_path: Path) -> None:
        """expires_in from Google is converted to an absolute timestamp on disk."""
        token_file = tmp_path / "token.json"
        client = GmailOAuthClient(token_file=token_file)

        saved = client._save_token(
            {"access_token": "fresh", "expires_in": 3600}, refresh_token="refresh-me"
        )

        assert saved["access_token"] == "fresh"
        assert saved["refresh_token"] == "refresh-me"
        assert "expires_in" not in saved
        assert client._is_token_expired(saved) is False
        assert json.loads(token_file.read_text())["expiry"] == saved["expiry"]


# ---------------------------------------------------------------------------
# LinkedIn Parser Tests (3 fixtures)
# ---------------------------------------------------------------------------

class TestLinkedInAlertParser:
    def test_fixture_1_two_jobs(self, linkedin_1_html: str) -> None:
        jobs = parse_linkedin_alert(linkedin_1_html, message_id="msg_li_1")
        assert len(jobs) == 2

        j1 = jobs[0]
        assert j1.company == "Oracle"
        assert j1.title == "Senior Java Backend Engineer"
        assert "Bengaluru" in (j1.location or "")
        assert "linkedin.com" in j1.url
        assert j1.description is None  # Truncated per Section 19
        assert "microservices in Java" in (j1.snippet or "")

        j2 = jobs[1]
        assert j2.company == "Infosys"
        assert "Spring Boot" in j2.title
        assert "Hyderabad" in (j2.location or "")
        assert j2.description is None

    def test_fixture_2_one_job(self, linkedin_2_html: str) -> None:
        jobs = parse_linkedin_alert(linkedin_2_html, message_id="msg_li_2")
        assert len(jobs) == 1
        j = jobs[0]
        assert j.company == "Razorpay"
        assert j.title == "Backend Software Engineer"
        assert "Bengaluru" in (j.location or "")
        assert j.description is None
        assert "MySQL, Kafka" in (j.snippet or "")

    def test_fixture_malformed_zero_jobs(self, linkedin_malformed_html: str) -> None:
        jobs = parse_linkedin_alert(linkedin_malformed_html, message_id="msg_li_bad")
        assert jobs == []


# ---------------------------------------------------------------------------
# Naukri Parser Tests (3 fixtures)
# ---------------------------------------------------------------------------

class TestNaukriAlertParser:
    def test_fixture_1_two_jobs(self, naukri_1_html: str) -> None:
        jobs = parse_naukri_alert(naukri_1_html, message_id="msg_nk_1")
        assert len(jobs) == 2

        j1 = jobs[0]
        assert j1.company == "Wipro Limited"
        assert "Java Developer" in j1.title
        assert "Bengaluru" in (j1.location or "")
        assert "naukri.com" in j1.url
        assert j1.description is None  # Truncated per Section 19
        assert "Experience: 3 - 6 Yrs" in (j1.snippet or "")
        assert "Keyskills: Java, Spring Boot" in (j1.snippet or "")

        j2 = jobs[1]
        assert j2.company == "Cognizant Technology Solutions"
        assert "Senior Software Engineer - Java" in j2.title
        assert "Pune" in (j2.location or "")
        assert j2.description is None

    def test_fixture_2_one_job(self, naukri_2_html: str) -> None:
        jobs = parse_naukri_alert(naukri_2_html, message_id="msg_nk_2")
        assert len(jobs) == 1
        j = jobs[0]
        assert j.company == "Swiggy"
        assert "Backend Engineer" in j.title
        assert "Bengaluru" in (j.location or "")
        assert j.description is None
        assert "Skills: Java" in (j.snippet or "")

    def test_fixture_malformed_zero_jobs(self, naukri_malformed_html: str) -> None:
        jobs = parse_naukri_alert(naukri_malformed_html, message_id="msg_nk_bad")
        assert jobs == []


# ---------------------------------------------------------------------------
# Routing by sender
# ---------------------------------------------------------------------------

def test_parse_alert_email_routing(linkedin_1_html: str, naukri_1_html: str) -> None:
    li_jobs = parse_alert_email("jobalerts-noreply@linkedin.com", linkedin_1_html, "msg1")
    assert len(li_jobs) == 2
    assert li_jobs[0].source == "gmail:linkedin"

    nk_jobs = parse_alert_email("jobalert@naukri.com", naukri_1_html, "msg2")
    assert len(nk_jobs) == 2
    assert nk_jobs[0].source == "gmail:naukri"


# ---------------------------------------------------------------------------
# Idempotency and needs_jd Transition Tests
# ---------------------------------------------------------------------------

class TestAlertIngestionIntegration:
    def test_idempotent_ingestion_and_needs_jd(
        self,
        conn: sqlite3.Connection,
        linkedin_1_html: str,
        naukri_1_html: str,
    ) -> None:
        test_messages = [
            {"id": "msg_001", "sender": "jobalerts-noreply@linkedin.com", "html": linkedin_1_html},
            {"id": "msg_002", "sender": "jobalert@naukri.com", "html": naukri_1_html},
        ]

        # First run: should process 2 messages and extract 4 jobs
        s1 = ingest_gmail_alerts(conn, test_messages=test_messages)
        assert s1.messages_processed == 2
        assert s1.total_listings == 4
        assert s1.new_jobs == 4

        # Jobs surviving prefilter must have status 'needs_jd'
        rows = conn.execute("SELECT id, status, description FROM jobs").fetchall()
        assert len(rows) == 4
        for r in rows:
            assert r["status"] == "needs_jd"
            assert r["description"] is None

        # Second run on the same messages: MUST create 0 new rows (idempotency)
        s2 = ingest_gmail_alerts(conn, test_messages=test_messages)
        assert s2.messages_processed == 0
        assert s2.messages_skipped_duplicate == 2
        assert s2.new_jobs == 0

        # Total rows in DB remains 4
        count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        assert count == 4


@pytest.fixture()
def instahyre_1_html() -> str:
    return (FIXTURES_DIR / "instahyre_1.html").read_text(encoding="utf-8")


@pytest.fixture()
def instahyre_2_html() -> str:
    return (FIXTURES_DIR / "instahyre_2.html").read_text(encoding="utf-8")


@pytest.fixture()
def instahyre_malformed_html() -> str:
    return (FIXTURES_DIR / "instahyre_malformed.html").read_text(encoding="utf-8")


class TestInstahyreAlerts:
    def test_instahyre_alert_parsing(self, instahyre_1_html: str) -> None:
        jobs = parse_alert_email("Instahyre Jobs <mail@instahyre.test>", instahyre_1_html, "ih1")
        assert len(jobs) == 1
        assert jobs[0].source == "gmail:instahyre"

    def test_instahyre_title_extraction(self, instahyre_1_html: str) -> None:
        job = parse_instahyre_alert(instahyre_1_html, "ih1")[0]
        assert job.title == "Senior Software Engineer"

    def test_instahyre_company_extraction(self, instahyre_1_html: str) -> None:
        job = parse_instahyre_alert(instahyre_1_html, "ih1")[0]
        assert job.company == "Toast"

    def test_instahyre_location_extraction(self, instahyre_1_html: str) -> None:
        job = parse_instahyre_alert(instahyre_1_html, "ih1")[0]
        assert job.location == "Bangalore"

    def test_instahyre_url_extraction(self, instahyre_1_html: str) -> None:
        job = parse_instahyre_alert(instahyre_1_html, "ih1")[0]
        assert "instahyre.com/job-430341" in job.url
        assert job.source_job_id == "430341"

    def test_instahyre_description_extraction(self, instahyre_1_html: str) -> None:
        job = parse_instahyre_alert(instahyre_1_html, "ih1")[0]
        assert job.description == "Build onboarding services in Java and Spring Boot."

    def test_instahyre_missing_description(self, instahyre_2_html: str) -> None:
        job = parse_instahyre_alert(instahyre_2_html, "ih2")[0]
        assert job.description is None
        assert job.posted_at is None

    def test_instahyre_malformed_alert(self, instahyre_malformed_html: str) -> None:
        assert parse_instahyre_alert(instahyre_malformed_html, "ihx") == []

    def test_instahyre_duplicate(self, conn: sqlite3.Connection, instahyre_1_html: str) -> None:
        messages = [{"id": "ih_msg", "sender": "mail@instahyre.test", "html": instahyre_1_html}]
        first = ingest_gmail_alerts(conn, test_messages=messages)
        second = ingest_gmail_alerts(conn, test_messages=messages)
        assert first.new_jobs == 1
        assert second.new_jobs == 0
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1

    def test_instahyre_matches_same_job_from_another_source(self, conn: sqlite3.Connection) -> None:
        alert = parse_instahyre_alert(
            (FIXTURES_DIR / "instahyre_2.html").read_text(encoding="utf-8"),
            "ih2",
        )[0]
        other = RawJob(
            source="adzuna",
            source_job_id="adz-1",
            company=alert.company,
            title=alert.title,
            location=alert.location,
            url="https://www.adzuna.in/details/1",
            description="Full description from Adzuna.",
        )
        ingest_raw_jobs(conn, [alert, other])
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
        row = conn.execute("SELECT description FROM jobs").fetchone()
        assert row["description"] == "Full description from Adzuna."
