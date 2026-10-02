"""Adversarial tests for pipeline.claims.py"""

from __future__ import annotations

import pytest

from jobpilot.config import AppConfig, ClaimsConfig
from jobpilot.models import (
    Bullet,
    EvidenceLevel,
    Experience,
    Identity,
    Learning,
    Profile,
    ResumeBase,
    Role,
    Settings,
    Skill,
    Targets,
)
from jobpilot.pipeline.claims import TailoredClaim, TailoredOutput, validate_claims


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
            use_client_name=False,
            client_display="a global financial-services client",
        ),
        skills=[
            Skill(name="Java", category="backend", level=EvidenceLevel.VERIFIED_PROFESSIONAL),
            Skill(name="Spring Boot", category="backend", level=EvidenceLevel.VERIFIED_PROFESSIONAL),
            Skill(name="Kafka", category="backend", level=EvidenceLevel.VERIFIED_PROFESSIONAL),
            Skill(name="React", category="frontend", level=EvidenceLevel.VERIFIED_PROFESSIONAL),
            Skill(name="TypeScript", category="frontend", level=EvidenceLevel.VERIFIED_PROJECT),
            Skill(name="AWS", category="cloud", level=EvidenceLevel.VERIFIED_PROJECT),
            Skill(name="MCP", category="ai", level=EvidenceLevel.LEARNING),
            Skill(name="Kubernetes", category="devops", level=EvidenceLevel.VERIFIED_PROFESSIONAL),
        ],
        known_gaps=["Terraform"],
        learning=Learning(topics=["MCP", "System design"]),
        settings=Settings(),
        targets=Targets(primary_titles=["Backend Engineer"], secondary_titles=[]),
    )


@pytest.fixture
def mock_resume() -> ResumeBase:
    return ResumeBase(
        schema_version=2,
        roles=[
            Role(
                id="r1",
                bullets=[
                    Bullet(id="b001", type="professional", text="b1", metrics=["4", "10"]),
                    Bullet(id="b002", type="professional", text="b2", metrics=[]),
                    Bullet(id="b003", type="professional", text="b3", metrics=["45%"]),
                    Bullet(id="b101", type="professional", text="b101", metrics=[]),
                    Bullet(id="b102", type="professional", text="b102", metrics=[]),
                    Bullet(id="b201", type="professional", text="b201", metrics=[]),
                ],
            )
        ],
    )


@pytest.fixture
def mock_config() -> AppConfig:
    return AppConfig(
        claims=ClaimsConfig(
            project_qualifiers=["personal project", "side project", "hackathon", "certified", "explored", "prototype"]
        )
    )


def test_rejects_nonexistent_bullet_id(mock_profile, mock_resume, mock_config):
    output = TailoredOutput(
        summary="A summary without numbers.",
        claims=[TailoredClaim(bullet_id="b999", skill="Java", context="backend", evidence_level="VERIFIED_PROFESSIONAL")],
        bullet_ids=["b999"],
    )
    errors = validate_claims(output, mock_profile, mock_resume, mock_config)
    assert any("b999" in e for e in errors)


def test_rejects_project_skill_as_professional(mock_profile, mock_resume, mock_config):
    output = TailoredOutput(
        summary="A summary.",
        claims=[TailoredClaim(bullet_id="b001", skill="TypeScript", context="frontend", evidence_level="VERIFIED_PROFESSIONAL")],
        bullet_ids=["b001"],
    )
    errors = validate_claims(output, mock_profile, mock_resume, mock_config)
    assert any("exceeds profile level" in e for e in errors)


def test_rejects_learning_skill_in_summary(mock_profile, mock_resume, mock_config):
    output = TailoredOutput(
        summary="I have MCP experience.",
        claims=[],
        bullet_ids=["b001"],
    )
    errors = validate_claims(output, mock_profile, mock_resume, mock_config)
    assert any("LEARNING skill: MCP" in e for e in errors)


def test_rejects_invented_percentage(mock_profile, mock_resume, mock_config):
    output = TailoredOutput(
        summary="reduced latency by 45%. Wait, 99% is invented.",
        claims=[],
        bullet_ids=["b003"],
    )
    errors = validate_claims(output, mock_profile, mock_resume, mock_config)
    assert any("99%" in e for e in errors)


def test_rejects_years_exceeding_cap(mock_profile, mock_resume, mock_config):
    output = TailoredOutput(
        summary="5 years of experience",
        claims=[],
        bullet_ids=["b001"],
    )
    errors = validate_claims(output, mock_profile, mock_resume, mock_config)
    assert any("exceeds cap" in e for e in errors)


def test_rejects_client_as_employer(mock_profile, mock_resume, mock_config):
    output = TailoredOutput(
        summary="worked at JPMorgan on backend",
        claims=[],
        bullet_ids=["b001"],
    )
    errors = validate_claims(output, mock_profile, mock_resume, mock_config)
    assert any("client as employer" in e for e in errors)


def test_rejects_known_gaps_skill(mock_profile, mock_resume, mock_config):
    output = TailoredOutput(
        summary="I used Terraform",
        claims=[],
        bullet_ids=["b001"],
    )
    errors = validate_claims(output, mock_profile, mock_resume, mock_config)
    assert any("known gap: Terraform" in e for e in errors)


def test_rejects_learning_topic_as_experience(mock_profile, mock_resume, mock_config):
    output = TailoredOutput(
        summary="System design experience",
        claims=[],
        bullet_ids=["b001"],
    )
    errors = validate_claims(output, mock_profile, mock_resume, mock_config)
    assert any("learning topic: System design" in e for e in errors)


def test_accepts_valid_output(mock_profile, mock_resume, mock_config):
    output = TailoredOutput(
        summary="Backend developer with 4 years experience.",
        claims=[TailoredClaim(bullet_id="b001", skill="Java", context="backend", evidence_level="VERIFIED_PROFESSIONAL")],
        bullet_ids=["b001"],
    )
    errors = validate_claims(output, mock_profile, mock_resume, mock_config)
    assert not errors


def test_accepts_project_skill_with_qualifier(mock_profile, mock_resume, mock_config):
    output = TailoredOutput(
        summary="Used TypeScript in a personal project.",
        claims=[TailoredClaim(bullet_id="b001", skill="TypeScript", context="frontend", evidence_level="VERIFIED_PROJECT")],
        bullet_ids=["b001"],
    )
    errors = validate_claims(output, mock_profile, mock_resume, mock_config)
    assert not errors


def test_rejects_unverified_leadership_verb(mock_profile, mock_resume, mock_config):
    output = TailoredOutput(
        summary="Spearheaded microservices design and architected backend.",
        claims=[TailoredClaim(bullet_id="b001", skill="Java", context="backend", evidence_level="VERIFIED_PROFESSIONAL")],
        bullet_ids=["b001"],
    )
    errors = validate_claims(output, mock_profile, mock_resume, mock_config)
    assert any("spearheaded" in e for e in errors)
    assert any("architected" in e for e in errors)


def test_accepts_verified_leadership_verb(mock_profile, mock_resume, mock_config):
    mock_resume.roles[0].bullets.append(
        Bullet(id="b999", type="professional", text="Led migration of core services.", metrics=["4"])
    )
    output = TailoredOutput(
        summary="Led migration of services with 4 years experience.",
        claims=[TailoredClaim(bullet_id="b999", skill="Java", context="backend", evidence_level="VERIFIED_PROFESSIONAL")],
        bullet_ids=["b999"],
    )
    errors = validate_claims(output, mock_profile, mock_resume, mock_config)
    assert not any("led" in e for e in errors)
