"""Tests for pipeline.prefilter — plain code filtering rules."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from jobpilot.db import get_connection, init_schema, upsert_job
from jobpilot.models import Experience, Identity, Profile, Targets
from jobpilot.pipeline.prefilter import (
    filter_age,
    filter_dealbreakers,
    filter_deprioritized,
    filter_experience,
    filter_location,
    filter_title,
    run_prefilter,
)


@pytest.fixture()
def profile() -> Profile:
    return Profile(
        schema_version=2,
        identity=Identity(name="Anand", base_location="India"),
        experience=Experience(total_years_actual=3.9, years_for_forms=4),
        targets=Targets(
            primary_titles=["Software Engineer", "Senior Software Engineer", "Java Backend Engineer"],
            secondary_titles=["Java Developer", "Spring Boot Developer"],
            deprioritized_categories=["pure_frontend", "pure_mobile", "pure_devops_sre", "pure_data_science"],
            enabled_extra_categories=[],
            seniority_stretch=2,
        ),
        dealbreakers={
            "titles_exclude": ["intern", "trainee", "manager", "director", ".net", "php"],
            "keywords_exclude": ["us citizens only", "security clearance", "must be located in the us"],
        },
    )


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = get_connection(":memory:")
    init_schema(c)
    return c


# ---------------------------------------------------------------------------
# Title filter tests
# ---------------------------------------------------------------------------

class TestTitleFilter:
    @pytest.mark.parametrize("title,ok", [
        ("Software Engineer", True),
        ("Senior Software Engineer", True),
        ("Java Backend Engineer", True),
        ("Spring Boot Developer", True),
        ("Backend Engineer", True),
        ("Java Developer", True),
        ("Software Developer - Java & Spring", True),
        # Exclusions
        ("Software Engineer Intern", False),
        ("Engineering Manager", False),
        ("PHP Developer", False),
        (".NET Core Developer", False),
        ("Director of Engineering", False),
    ])
    def test_filter_title(self, title: str, ok: bool, profile: Profile) -> None:
        passed, reason = filter_title(title, profile)
        assert passed == ok
        if not ok:
            assert reason is not None


# ---------------------------------------------------------------------------
# Dealbreakers and regional exclusion tests
# ---------------------------------------------------------------------------

class TestDealbreakersFilter:
    @pytest.mark.parametrize("text,ok", [
        ("Looking for a backend engineer. Work from home in India.", True),
        ("Role requires US citizens only due to federal contracts.", False),
        ("Must possess an active Top Secret security clearance.", False),
        ("Candidate must be located in the US.", False),
        ("Must reside in the USA.", False),
        ("Remote - US only.", False),
        ("Working hours in EU timezone only.", False),
        ("UK only remote position.", False),
    ])
    def test_filter_dealbreakers(self, text: str, ok: bool, profile: Profile) -> None:
        passed, reason = filter_dealbreakers(text, profile)
        assert passed == ok
        if not ok:
            assert reason is not None


# ---------------------------------------------------------------------------
# Location filter tests (Section 9 rules)
# ---------------------------------------------------------------------------

class TestLocationFilter:
    @pytest.mark.parametrize("location,text,ok,expected_reason", [
        # Regex checks
        ("Remote", "Position is US only.", False, "region:us_only"),
        ("Remote", "Candidate must reside in the United States.", False, "region:must_reside_in"),
        ("Remote", "Working hours in EU timezone.", False, "region:eu_timezone"),
        ("Remote", "UK only applicants.", False, "region:uk_only"),
        ("Remote", "Valid US work authorization required.", False, "region:work_authorization_required"),

        # Remote with remote_regions_ok
        ("Remote", "Global remote company.", True, None),
        ("Remote - Worldwide", "Anywhere in the world.", True, None),
        ("Remote - India", "Work from anywhere in India.", True, None),
        ("Remote - APAC", "APAC hours.", True, None),
        ("Remote - US only", "US timezone.", False, "region:us_only"),
        ("Remote - EMEA", "EMEA region only.", False, "region_not_allowed:Remote - EMEA"),

        # Null / empty location
        (None, "Standard software engineer JD.", True, None),
        ("", "Standard software engineer JD.", True, None),

        # Foreign onsite location
        ("Austin, TX", "Onsite at our Austin headquarters.", False, "location_not_supported:Austin, TX"),
        ("London, UK", "Onsite in our London office.", False, "location_not_supported:London, UK"),
        ("Berlin, Germany", "Onsite in Berlin.", False, "location_not_supported:Berlin, Germany"),
    ])
    def test_location_rules(
        self,
        location: str | None,
        text: str,
        ok: bool,
        expected_reason: str | None,
        profile: Profile,
    ) -> None:
        passed, reason = filter_location(location, text, profile)
        assert passed == ok
        if not ok:
            assert reason == expected_reason

    def test_null_india_cities_does_not_reject_on_city_alone(self, profile: Profile) -> None:
        """When settings.india_cities is empty or null, Indian cities must NOT be rejected."""
        profile.settings.india_cities = []
        for city in ["Bengaluru, Karnataka", "Pune, Maharashtra", "Hyderabad", "Chennai", "Noida, UP", "Mumbai"]:
            passed, reason = filter_location(city, "Great backend opportunity.", profile)
            assert passed is True, f"Failed for {city}: {reason}"

    def test_specific_india_cities_filter(self, profile: Profile) -> None:
        """When settings.india_cities has specific cities, other Indian cities are rejected."""
        profile.settings.india_cities = ["Bangalore", "Hyderabad"]

        # Bangalore / Bengaluru matches
        p1, _ = filter_location("Bengaluru, Karnataka", "Backend job", profile)
        assert p1 is True

        p2, _ = filter_location("Hyderabad, Telangana", "Backend job", profile)
        assert p2 is True

        # Pune is not in targets
        p3, reason = filter_location("Pune, Maharashtra", "Backend job", profile)
        assert p3 is False
        assert "city_not_in_targets" in (reason or "")


# ---------------------------------------------------------------------------
# Deprioritized category tests
# ---------------------------------------------------------------------------

class TestDeprioritizedFilter:
    def test_pure_frontend_rejected(self, profile: Profile) -> None:
        passed, reason = filter_deprioritized("React Frontend Developer", "Build web UIs with CSS", profile)
        assert not passed
        assert reason == "deprioritized_category:pure_frontend"

    def test_frontend_with_java_allowed(self, profile: Profile) -> None:
        """If Java or Spring is mentioned, do not deprioritize."""
        passed, _ = filter_deprioritized("Full Stack Engineer (React)", "Backend is in Java Spring Boot", profile)
        assert passed

    def test_pure_mobile_rejected(self, profile: Profile) -> None:
        passed, reason = filter_deprioritized("Android App Developer", "Build Kotlin native apps", profile)
        assert not passed
        assert reason == "deprioritized_category:pure_mobile"


# ---------------------------------------------------------------------------
# Experience filter tests
# ---------------------------------------------------------------------------

class TestExperienceFilter:
    def test_acceptable_experience(self, profile: Profile) -> None:
        # 3.9 actual + 2 stretch = 5.9
        passed, _ = filter_experience("Requires 3-5 years of experience in Java.", profile)
        assert passed

    def test_excessive_experience_rejected(self, profile: Profile) -> None:
        passed, reason = filter_experience("Requires 8-10 years of experience.", profile)
        assert not passed
        assert "experience_too_high" in (reason or "")


# ---------------------------------------------------------------------------
# Age filter tests
# ---------------------------------------------------------------------------

class TestAgeFilter:
    def test_recent_job_passes(self) -> None:
        recent = (datetime.now(UTC) - timedelta(days=5)).isoformat()
        passed, _ = filter_age(recent, max_days=21)
        assert passed

    def test_old_job_rejected(self) -> None:
        old = (datetime.now(UTC) - timedelta(days=30)).isoformat()
        passed, reason = filter_age(old, max_days=21)
        assert not passed
        assert "job_too_old" in (reason or "")


# ---------------------------------------------------------------------------
# Integration with database and needs_jd status
# ---------------------------------------------------------------------------

class TestPrefilterRun:
    def test_run_prefilter_transitions(self, conn: sqlite3.Connection, profile: Profile) -> None:
        # Job 1: Good Java job with NO description -> should become needs_jd
        upsert_job(conn, {
            "fingerprint": "fp1",
            "source": "adzuna",
            "company": "GoodCo",
            "title": "Java Backend Engineer",
            "url": "https://example.com/job1",
            "snippet": "Java Spring Boot microservices",
            "description": None,
        })
        # Job 2: Good Java job with full description -> should become new
        upsert_job(conn, {
            "fingerprint": "fp2",
            "source": "greenhouse",
            "company": "TechCo",
            "title": "Software Engineer",
            "url": "https://example.com/job2",
            "description": "Full description with Java",
        })
        # Job 3: Bad job (US only) -> should become filtered_out
        upsert_job(conn, {
            "fingerprint": "fp3",
            "source": "manual",
            "company": "BadCo",
            "title": "Software Engineer",
            "url": "https://example.com/job3",
            "description": "Must be located in the US.",
        })

        res = run_prefilter(conn, profile=profile)
        assert res.evaluated == 3
        assert res.passed == 2
        assert res.filtered_out == 1
        assert res.needs_jd == 1

        j1 = conn.execute("SELECT status FROM jobs WHERE fingerprint = 'fp1'").fetchone()
        assert j1["status"] == "needs_jd"

        j2 = conn.execute("SELECT status FROM jobs WHERE fingerprint = 'fp2'").fetchone()
        assert j2["status"] == "new"

        j3 = conn.execute("SELECT status, status_reason FROM jobs WHERE fingerprint = 'fp3'").fetchone()
        assert j3["status"] == "filtered_out"
        assert any(term in j3["status_reason"] for term in ("must_be_located_in_the_us", "must be located in the us", "us_only"))
