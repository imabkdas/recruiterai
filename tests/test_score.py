"""Tests for pipeline.score — deterministic scoring, boundaries, hard rules, and golden ranking."""

from __future__ import annotations

import pytest

from jobpilot.config import AppConfig, LLMConfig, ScoringConfig, ThresholdsConfig
from jobpilot.db import get_connection, init_schema, save_analysis, upsert_job
from jobpilot.models import (
    EvidenceLevel,
    Experience,
    Identity,
    Job,
    JobAnalysis,
    Profile,
    RemoteEligibility,
    Salary,
    Settings,
    Skill,
    Targets,
)
from jobpilot.pipeline.score import (
    classify_evidence_level,
    run_scoring,
    score_experience,
    score_extras,
    score_job,
    score_location,
    score_seniority_title,
    score_skills,
)


@pytest.fixture
def mock_profile() -> Profile:
    return Profile(
        schema_version=2,
        identity=Identity(name="Anand", base_location="India"),
        experience=Experience(
            total_years_actual=3.9,
            years_for_forms=4,
            current_employer="Cognizant",
            current_client="JPMorgan",
            client_display="a global financial-services client",
        ),
        settings=Settings(
            work_authorization_india=True,
            work_authorization_other_countries=None,  # unset
            salary_expectation="100000",
            india_cities=["Bangalore", "Hyderabad", "Pune"],
            remote_regions_ok=["India", "APAC", "Worldwide"],
        ),
        targets=Targets(
            primary_titles=["Software Engineer", "Senior Software Engineer", "Java Backend Engineer", "Backend Engineer"],
            secondary_titles=["Java Developer", "Spring Boot Developer", "Full Stack Software Engineer"],
            seniority_stretch=2,
        ),
        skills=[
            Skill(name="Java", category="core_backend", level=EvidenceLevel.VERIFIED_PROFESSIONAL, evidence=["b001"]),
            Skill(name="Spring Boot", category="core_backend", level=EvidenceLevel.VERIFIED_PROFESSIONAL, evidence=["b002"]),
            Skill(name="Microservices", category="core_backend", level=EvidenceLevel.VERIFIED_PROFESSIONAL, evidence=["b003"]),
            Skill(name="Kafka", category="core_backend", level=EvidenceLevel.VERIFIED_PROFESSIONAL, evidence=["b004"]),
            Skill(name="REST APIs", category="core_backend", level=EvidenceLevel.VERIFIED_PROFESSIONAL, evidence=["b005"]),
            Skill(name="React", category="frontend", level=EvidenceLevel.VERIFIED_PROFESSIONAL, evidence=["b006"]),
            Skill(name="TypeScript", category="frontend", level=EvidenceLevel.VERIFIED_PROJECT, evidence=["p001"]),
            Skill(name="Docker", category="devops", level=EvidenceLevel.VERIFIED_CERTIFICATION, evidence=["c001"]),
            Skill(name="Socket.IO", category="frontend", level=EvidenceLevel.EXPERIMENTAL, evidence=["e001"]),
            Skill(name="Java 21", category="core_backend", level=EvidenceLevel.LEARNING, evidence=[]),
        ],
    )


@pytest.fixture
def mock_config() -> AppConfig:
    return AppConfig(
        thresholds=ThresholdsConfig(tier_a=80, tier_b=65),
        scoring=ScoringConfig(
            evidence_weights={
                "VERIFIED_PROFESSIONAL": 1.0,
                "VERIFIED_PROJECT": 0.6,
                "VERIFIED_CERTIFICATION": 0.5,
                "EXPERIMENTAL": 0.35,
                "LEARNING": 0.2,
                "UNKNOWN": 0.0,
            }
        ),
        stack_bonus=["Kafka", "Kubernetes", "Microservices", "Spring Boot"],
        llm=LLMConfig(),
    )


class TestSkillScoring:
    def test_full_professional_skills(self, mock_profile: Profile, mock_config: AppConfig) -> None:
        analysis = JobAnalysis(
            normalized_title="Backend Engineer",
            required_skills=["Java", "Spring Boot", "Microservices"],
            preferred_skills=["Kafka"],
        )
        score, matched, missing_req, missing_pref, flags = score_skills(analysis, mock_profile, mock_config)
        assert score == 40.0
        assert len(missing_req) == 0
        assert len(missing_pref) == 0
        assert len(flags) == 0
        for m in matched:
            assert classify_evidence_level(m.level) == "covered"

    def test_evidence_weighting_mix(self, mock_profile: Profile, mock_config: AppConfig) -> None:
        # required: TypeScript (0.6), Docker (0.5), Java 21 (0.2), UnknownSkill (0.0) -> avg = 1.3 / 4 = 0.325 -> 30 * 0.325 = 9.75
        # preferred: Socket.IO (0.35) -> 10 * 0.35 = 3.5
        # total skills = 9.75 + 3.5 = 13.25
        analysis = JobAnalysis(
            normalized_title="Full Stack Engineer",
            required_skills=["TypeScript", "Docker", "Java 21", "UnknownSkill"],
            preferred_skills=["Socket.IO"],
        )
        score, matched, missing_req, missing_pref, flags = score_skills(analysis, mock_profile, mock_config)
        assert score == 13.25
        assert "UnknownSkill" in missing_req
        assert "missing_required:UnknownSkill" in flags
        assert "project_only:TypeScript" in flags
        assert "learning_only:Java 21" in flags


class TestExperienceScoring:
    @pytest.mark.parametrize(
        ("min_years", "max_years", "expected_score", "has_asks_flag"),
        [
            (None, None, 20.0, False),               # unstated
            (3.0, 5.0, 20.0, False),                 # within candidate's 3.9 years
            (4.0, 6.0, 20.0, True),                  # min (4.0) <= actual (3.9) + 1.0 -> 20.0, but > 3.9 so asks flag
            (4.9, 7.0, 20.0, True),                  # exactly actual + 1.0 -> 20.0
            (5.9, 8.0, 10.0, True),                  # halfway through stretch (cutoff = 3.9 + 2 + 1 = 6.9)
            (6.9, 10.0, 0.0, True),                  # at cutoff -> 0.0
            (8.0, 12.0, 0.0, True),                  # beyond cutoff -> 0.0
        ],
    )
    def test_experience_boundaries(
        self,
        mock_profile: Profile,
        min_years: float | None,
        max_years: float | None,
        expected_score: float,
        has_asks_flag: bool,
    ) -> None:
        analysis = JobAnalysis(
            normalized_title="Backend Engineer",
            experience_min_years=min_years,
            experience_max_years=max_years,
        )
        score, flags = score_experience(analysis, mock_profile)
        assert score == expected_score
        assert any("asks_" in f for f in flags) is has_asks_flag


class TestSeniorityTitleScoring:
    def test_primary_title_match(self, mock_profile: Profile) -> None:
        analysis = JobAnalysis(normalized_title="Java Backend Engineer", seniority="mid")
        score = score_seniority_title(analysis, "Java Backend Engineer", mock_profile, experience_score=20.0)
        assert score == 15.0

    def test_secondary_title_match(self, mock_profile: Profile) -> None:
        analysis = JobAnalysis(normalized_title="Spring Boot Developer", seniority="mid")
        score = score_seniority_title(analysis, "Spring Boot Developer", mock_profile, experience_score=20.0)
        assert score == 12.0  # 80% of 15

    def test_senior_title_penalized_on_low_experience(self, mock_profile: Profile) -> None:
        analysis = JobAnalysis(normalized_title="Senior Software Engineer", seniority="senior")
        # With acceptable experience score
        high_exp_score = score_seniority_title(analysis, "Senior Software Engineer", mock_profile, experience_score=15.0)
        assert high_exp_score == 15.0

        # With low experience score (< 10)
        low_exp_score = score_seniority_title(analysis, "Senior Software Engineer", mock_profile, experience_score=5.0)
        assert low_exp_score < 10.0


class TestLocationScoring:
    def test_india_city_match(self, mock_profile: Profile) -> None:
        analysis = JobAnalysis(normalized_title="Dev", locations=["Bangalore"], location_type="onsite")
        score, flags = score_location(analysis, "Bangalore, India", mock_profile)
        assert score == 15.0
        assert "not_eligible" not in flags

    def test_remote_open_to_india_yes(self, mock_profile: Profile) -> None:
        analysis = JobAnalysis(
            normalized_title="Dev",
            locations=["Remote"],
            location_type="remote",
            remote_eligibility=RemoteEligibility(open_to_india="yes"),
        )
        score, flags = score_location(analysis, "Remote", mock_profile)
        assert score == 15.0
        assert "not_eligible" not in flags

    def test_remote_open_to_india_unclear(self, mock_profile: Profile) -> None:
        analysis = JobAnalysis(
            normalized_title="Dev",
            locations=["Remote"],
            location_type="remote",
            remote_eligibility=RemoteEligibility(open_to_india="unclear"),
        )
        score, flags = score_location(analysis, "Remote", mock_profile)
        assert score == 7.5
        assert "region_unclear" in flags

    def test_remote_open_to_india_no_hard_rule(self, mock_profile: Profile) -> None:
        analysis = JobAnalysis(
            normalized_title="Dev",
            locations=["San Francisco, CA"],
            location_type="remote",
            remote_eligibility=RemoteEligibility(open_to_india="no", restriction_text="US citizens only"),
        )
        score, flags = score_location(analysis, "San Francisco, CA", mock_profile)
        assert score == 0.0
        assert "not_eligible" in flags


class TestExtrasAndHardRules:
    def test_salary_unknown_gives_neutral(self, mock_profile: Profile, mock_config: AppConfig) -> None:
        analysis = JobAnalysis(normalized_title="Dev", salary=Salary(min=None, max=None))
        score, flags = score_extras(analysis, "Acme", mock_profile, mock_config)
        assert "salary_unknown" in flags
        assert score >= 2.0

    def test_stack_bonuses(self, mock_profile: Profile, mock_config: AppConfig) -> None:
        analysis = JobAnalysis(
            normalized_title="Dev",
            required_skills=["Spring Boot", "Kafka"],
            preferred_skills=["Microservices"],
            salary=Salary(min=None, max=None),
        )
        score, _ = score_extras(analysis, "Acme", mock_profile, mock_config)
        # 2.0 salary + 3.0 stack bonuses (Spring Boot, Kafka, Microservices) = 5.0
        assert score == 5.0

    def test_work_auth_unset_flag(self, mock_profile: Profile, mock_config: AppConfig) -> None:
        # Foreign role requiring work authorization when work_authorization_other_countries is None
        analysis = JobAnalysis(
            normalized_title="Software Engineer",
            locations=["London, UK"],
            visa_or_work_auth_required=True,
            remote_eligibility=RemoteEligibility(open_to_india="unclear"),
        )
        job = Job(fingerprint="fp1", source="manual", company="UKCo", title="Software Engineer", url="https://x.com/1")
        res = score_job(job, analysis, mock_profile, mock_config)
        assert "work_auth_unset" in res.flags


# ---------------------------------------------------------------------------
# Golden Ranking Test
# ---------------------------------------------------------------------------

class TestGoldenRanking:
    """Golden ranking test: 6 fixture JobAnalysis objects must rank in strictly expected order."""

    def test_golden_ranking_order(self, mock_profile: Profile, mock_config: AppConfig) -> None:
        # 1. Excellent match
        jd_excellent = JobAnalysis(
            normalized_title="Java Backend Engineer",
            seniority="mid",
            experience_min_years=3.0,
            experience_max_years=5.0,
            required_skills=["Java", "Spring Boot", "Microservices", "REST APIs"],
            preferred_skills=["Kafka"],
            locations=["Bangalore"],
            location_type="hybrid",
            remote_eligibility=RemoteEligibility(open_to_india="yes"),
            salary=Salary(min=120000.0, max=140000.0),
        )

        # 2. Project-only skill match
        jd_project_only = JobAnalysis(
            normalized_title="Full Stack Software Engineer",
            seniority="mid",
            experience_min_years=3.0,
            experience_max_years=5.0,
            required_skills=["Java", "TypeScript"],
            preferred_skills=["Socket.IO"],
            locations=["Remote"],
            location_type="remote",
            remote_eligibility=RemoteEligibility(open_to_india="yes"),
        )

        # 3. Learning-only skill match
        jd_learning_only = JobAnalysis(
            normalized_title="Software Engineer",
            seniority="mid",
            experience_min_years=3.0,
            experience_max_years=5.0,
            required_skills=["Java 21", "Rust", "Go"],
            preferred_skills=[],
            locations=["Remote"],
            location_type="remote",
            remote_eligibility=RemoteEligibility(open_to_india="yes"),
        )

        # 4. 8+ years required
        jd_eight_plus = JobAnalysis(
            normalized_title="Java Backend Engineer",
            seniority="lead",
            experience_min_years=8.0,
            experience_max_years=12.0,
            required_skills=["Java", "Spring Boot"],
            preferred_skills=[],
            locations=["Remote"],
            location_type="remote",
            remote_eligibility=RemoteEligibility(open_to_india="yes"),
        )

        # 5. US-only remote (hard rule not_eligible)
        jd_us_only = JobAnalysis(
            normalized_title="Java Developer",
            seniority="mid",
            experience_min_years=3.0,
            experience_max_years=5.0,
            required_skills=["Java", "C++", "AWS"],
            preferred_skills=[],
            locations=["New York, NY"],
            location_type="remote",
            remote_eligibility=RemoteEligibility(open_to_india="no", restriction_text="Must reside in US"),
        )

        # 6. Vague JD
        jd_vague = JobAnalysis(
            normalized_title="Technical Analyst",
            seniority="unknown",
            experience_min_years=None,
            experience_max_years=None,
            required_skills=["Perl", "C", "Bash"],
            preferred_skills=[],
            locations=["Remote"],
            location_type="remote",
            remote_eligibility=RemoteEligibility(open_to_india="unclear"),
        )

        fixtures = [
            ("excellent_match", jd_excellent),
            ("project_only_skill", jd_project_only),
            ("learning_only_skill", jd_learning_only),
            ("eight_plus_years", jd_eight_plus),
            ("us_only_remote", jd_us_only),
            ("vague_jd", jd_vague),
        ]

        scored_results = []
        for name, jd in fixtures:
            job = Job(
                fingerprint=f"fp_{name}",
                source="test",
                company="TestCorp",
                title=jd.normalized_title,
                url="https://example.com",
            )
            res = score_job(job, jd, mock_profile, mock_config)
            scored_results.append((name, res))

        # Check total scores order
        totals = [res.total for _, res in scored_results]
        assert totals[0] > totals[1] > totals[2] > totals[3] > totals[4] > totals[5], (
            f"Expected descending order of totals: {totals}"
        )

        # Check tiers
        assert scored_results[0][1].tier == "A"
        assert scored_results[1][1].tier == "B"
        assert scored_results[2][1].tier is None
        assert scored_results[3][1].tier is None
        assert scored_results[4][1].tier is None
        assert scored_results[5][1].tier is None

        # Check hard exclusion for US only
        assert "not_eligible" in scored_results[4][1].flags


# ---------------------------------------------------------------------------
# Run Scoring DB integration test
# ---------------------------------------------------------------------------

def test_run_scoring_pipeline(tmp_path, mock_profile: Profile, mock_config: AppConfig) -> None:
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    job_id = upsert_job(
        conn,
        {
            "fingerprint": "fp1",
            "source": "manual",
            "company": "Acme",
            "title": "Java Backend Engineer",
            "url": "https://example.com/1",
            "status": "analyzed",
        },
    )

    analysis = JobAnalysis(
        normalized_title="Java Backend Engineer",
        required_skills=["Java", "Spring Boot"],
        preferred_skills=["Kafka"],
        locations=["Bangalore"],
    )

    save_analysis(
        conn,
        job_id=job_id,
        prompt_version="v1",
        model="gemini",
        content_hash="hash1",
        analysis_json=analysis.model_dump_json(),
    )

    summary = run_scoring(conn, config=mock_config, profile=mock_profile)
    assert summary.total_scored == 1
    assert summary.tier_a == 1

    # Verify score table
    row = conn.execute("SELECT * FROM scores WHERE job_id = ?", (job_id,)).fetchone()
    assert row is not None
    assert row["total"] >= 80.0
    assert row["tier"] == "A"

    # Verify job status updated to scored
    job_row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert job_row["status"] == "scored"
