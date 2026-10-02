"""Tests for jobpilot.models — Pydantic model validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from jobpilot.models import (
    EvidenceLevel,
    Job,
    JobAnalysis,
    Profile,
    RawJob,
    ResumeBase,
    ScoreResult,
    Skill,
)


class TestEvidenceLevel:
    def test_all_levels(self) -> None:
        levels = [e.value for e in EvidenceLevel]
        assert "VERIFIED_PROFESSIONAL" in levels
        assert "LEARNING" in levels
        assert "UNKNOWN" in levels
        assert len(levels) == 6


class TestRawJob:
    def test_minimal(self) -> None:
        job = RawJob(source="test", company="Co", title="SWE", url="https://x.com")
        assert job.source == "test"
        assert job.description is None

    def test_full(self) -> None:
        job = RawJob(
            source="greenhouse",
            source_job_id="gh-123",
            company="Acme",
            title="Backend Engineer",
            location="Bangalore",
            remote_type="hybrid",
            url="https://acme.com/jobs/123",
            apply_url="https://acme.com/apply/123",
            description="Full JD here...",
            snippet="Short snippet",
            posted_at="2024-01-15",
            raw_json={"extra": "data"},
        )
        assert job.raw_json == {"extra": "data"}


class TestJob:
    def test_defaults(self) -> None:
        job = Job(fingerprint="fp1", source="test", company="Co", title="SWE", url="https://x.com")
        assert job.status == "new"
        assert job.id is None


class TestJobAnalysis:
    def test_minimal(self) -> None:
        analysis = JobAnalysis(normalized_title="Software Engineer")
        assert analysis.seniority == "unknown"
        assert analysis.required_skills == []

    def test_full(self) -> None:
        analysis = JobAnalysis(
            normalized_title="Senior Backend Engineer",
            seniority="senior",
            experience_min_years=5,
            experience_max_years=8,
            required_skills=["Java", "Spring Boot"],
            preferred_skills=["Kafka"],
            responsibilities_summary="Build microservices.",
            location_type="remote",
            locations=["India", "US"],
            remote_eligibility={"open_to_india": "yes"},
            visa_or_work_auth_required=False,
            salary={"min": 100000, "max": 150000, "currency": "USD", "period": "yearly"},
            red_flags=["requires 10+ years"],
        )
        assert analysis.seniority == "senior"
        assert analysis.remote_eligibility.open_to_india == "yes"


class TestSkill:
    def test_valid_level(self) -> None:
        s = Skill(name="Java", category="core_backend", level=EvidenceLevel.VERIFIED_PROFESSIONAL)
        assert s.level == EvidenceLevel.VERIFIED_PROFESSIONAL

    def test_invalid_level_string(self) -> None:
        with pytest.raises(ValidationError):
            Skill(name="Java", category="core_backend", level="BOGUS_LEVEL")


class TestProfile:
    def test_minimal_profile(self) -> None:
        p = Profile(
            schema_version=2,
            identity={"name": "Test", "base_location": "India"},
            experience={"total_years_actual": 3.0, "years_for_forms": 3},
        )
        assert p.schema_version == 2
        assert p.experience.total_years_actual == 3.0

    def test_settings_defaults_to_none(self) -> None:
        p = Profile(
            schema_version=2,
            identity={"name": "Test"},
            experience={"total_years_actual": 3.0, "years_for_forms": 3},
        )
        assert p.settings.salary_expectation is None
        assert p.settings.notice_period_days is None


class TestResumeBase:
    def test_minimal(self) -> None:
        rb = ResumeBase(schema_version=2)
        assert rb.roles == []
        assert rb.projects == []


class TestScoreResult:
    def test_full_score(self) -> None:
        sr = ScoreResult(
            job_id=1,
            total=82.5,
            skills_score=35.0,
            experience_score=18.0,
            seniority_score=12.0,
            location_score=15.0,
            extras_score=2.5,
            tier="A",
            matched_skills=[{"skill": "Java", "level": "VERIFIED_PROFESSIONAL", "evidence_ids": ["b001"]}],
            missing_required=["Go"],
            flags=["asks_5plus_years"],
        )
        assert sr.tier == "A"
        assert sr.total == 82.5
