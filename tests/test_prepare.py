"""Tests for pipeline.prepare.py"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from jobpilot.config import AppConfig, ClaimsConfig
from jobpilot.db import get_connection, init_schema, save_analysis, save_score
from jobpilot.models import (
    Bullet,
    EvidenceLevel,
    Experience,
    Identity,
    JobAnalysis,
    Learning,
    Profile,
    ProjectEntry,
    ResumeBase,
    Role,
    Settings,
    Skill,
    SummaryVariant,
    Targets,
)
from jobpilot.pipeline.claims import NoteOutput, TailoredOutput
from jobpilot.pipeline.prepare import _find_project_root, prepare_job, rank_bullets, run_prepare


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    c = get_connection(db_path)
    init_schema(c)
    return c


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
            client_display="client",
        ),
        skills=[
            Skill(name="Java", category="backend", level=EvidenceLevel.VERIFIED_PROFESSIONAL),
            Skill(name="Kafka", category="backend", level=EvidenceLevel.VERIFIED_PROFESSIONAL),
            Skill(name="Python", category="backend", level=EvidenceLevel.VERIFIED_PROFESSIONAL),
        ],
        known_gaps=[],
        learning=Learning(topics=[]),
        settings=Settings(),
        targets=Targets(primary_titles=["Backend Engineer"], secondary_titles=[]),
    )


@pytest.fixture
def mock_resume() -> ResumeBase:
    return ResumeBase(
        schema_version=2,
        summary_variants=[SummaryVariant(id="v1", text="Base summary text")],
        roles=[
            Role(
                id="r1",
                title="Backend Engineer",
                bullets=[
                    Bullet(id="b1", type="professional", text="Used Java in production.", skills=["Java"], metrics=[]),
                    Bullet(id="b2", type="professional", text="Used Python.", skills=["Python"], metrics=[]),
                    Bullet(id="b3", type="professional", text="Used Kafka in production.", skills=["Kafka"], metrics=[]),
                ],
            )
        ],
    )


@pytest.fixture
def mock_config() -> AppConfig:
    return AppConfig(
        claims=ClaimsConfig(
            project_qualifiers=["project", "personal project"]
        )
    )


def seed_db(
    conn,
    job_id: int,
    status: str = "queued",
    score: float = 85.0,
    tier: str = "A",
    company: str = "TestCo",
    matched_skills: list | None = None,
):
    conn.execute(
        """
        INSERT INTO jobs (id, fingerprint, source, company, title, url, discovered_at, last_seen_at, status)
        VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'), ?)
        """,
        (job_id, f"fp_{job_id}", "manual", company, "Backend Engineer", f"http://example.com/{job_id}", status),
    )

    analysis = JobAnalysis(
        normalized_title="Backend Engineer",
        required_skills=["Java", "Kafka"],
        preferred_skills=[],
        locations=["Remote"],
    )
    save_analysis(
        conn,
        job_id=job_id,
        prompt_version="v1",
        model="gemini",
        content_hash=f"hash_{job_id}",
        analysis_json=json.dumps(analysis.model_dump()),
    )

    if matched_skills is None:
        matched_skills = [
            {"skill": "Java", "level": EvidenceLevel.VERIFIED_PROFESSIONAL.value},
            {"skill": "Kafka", "level": EvidenceLevel.VERIFIED_PROFESSIONAL.value},
        ]

    save_score(
        conn,
        {
            "job_id": job_id,
            "total": score,
            "tier": tier,
            "matched_skills": matched_skills,
            "missing_required": [],
            "missing_preferred": [],
            "flags": [],
        },
    )


def test_bullet_ranking_order(conn, mock_profile, mock_resume, mock_config):
    seed_db(conn, 1, status="queued")
    mock_client = MagicMock()

    res = prepare_job(conn, 1, mock_profile, mock_resume, mock_config, mock_client)

    # b1 and b3 match matched_skills (Java, Kafka), b2 does not
    top_bullets = res.bullet_ids[:2]
    assert "b1" in top_bullets
    assert "b3" in top_bullets
    assert res.bullet_ids[2] == "b2"


def test_prepare_does_not_call_the_llm(conn, mock_profile, mock_resume, mock_config):
    """Resume text is edited in the UI, so prepare never spends an LLM call."""
    seed_db(conn, 1, tier="A", status="queued")

    mock_client = MagicMock()
    mock_client.generate_json.side_effect = [
        TailoredOutput(summary="Valid summary text.", claims=[], bullet_ids=["b1", "b3"]),
        NoteOutput(note="Valid application note."),
    ]

    res = prepare_job(conn, 1, mock_profile, mock_resume, mock_config, mock_client)
    assert mock_client.generate_json.call_count == 0
    assert res.used_fallback is True
    assert res.summary == "Base summary text"
    assert "I'm interested in" in res.note

    row = conn.execute("SELECT tailored_summary, short_note FROM applications WHERE job_id = 1").fetchone()
    assert row["tailored_summary"] == "Base summary text"
    assert "I'm interested in" in row["short_note"]

    job_row = conn.execute("SELECT status FROM jobs WHERE id = 1").fetchone()
    assert job_row["status"] == "prepared"


def test_prepare_tier_a_fallback(conn, mock_profile, mock_resume, mock_config):
    seed_db(conn, 1, tier="A", status="queued")

    mock_client = MagicMock()
    mock_client.generate_json.side_effect = Exception("LLM Error")

    res = prepare_job(conn, 1, mock_profile, mock_resume, mock_config, mock_client)
    assert res.used_fallback is True
    assert res.summary == "Base summary text"  # fallback


def test_prepare_tier_b_no_llm(conn, mock_profile, mock_resume, mock_config):
    seed_db(conn, 1, tier="B", status="queued")

    mock_client = MagicMock()

    res = prepare_job(conn, 1, mock_profile, mock_resume, mock_config, mock_client)
    assert mock_client.generate_json.call_count == 0
    assert res.summary == "Base summary text"
    assert "I'm interested in" in res.note


def test_prepare_auto_top(conn, mock_profile, mock_resume, mock_config):
    seed_db(conn, 1, score=90, status="queued")
    seed_db(conn, 2, score=80, status="queued")
    seed_db(conn, 3, score=95, status="queued")

    mock_client = MagicMock()

    results = run_prepare(
        conn,
        auto_top=2,
        config=mock_config,
        profile=mock_profile,
        resume_base=mock_resume,
        llm_client=mock_client,
    )

    assert len(results) == 2
    prepared_ids = [r.job_id for r in results]
    assert 3 in prepared_ids
    assert 1 in prepared_ids
    assert 2 not in prepared_ids


def test_output_file_created(conn, mock_profile, mock_resume, mock_config):
    seed_db(conn, 101, company="Acme Corp", status="queued")
    mock_client = MagicMock()

    res = prepare_job(conn, 101, mock_profile, mock_resume, mock_config, mock_client)

    root = _find_project_root()
    out_file = root / "data" / "out" / "101_acme_corp.md"
    assert out_file.exists()
    content = out_file.read_text()
    assert "Acme Corp" in content
    assert res.summary in content


def test_rank_bullets_project_rules():
    resume = ResumeBase(
        schema_version=2,
        roles=[
            Role(
                id="r1",
                title="Backend Developer",
                bullets=[
                    Bullet(id="b1", type="professional", text="b1", skills=["Java"]),
                    Bullet(id="b2", type="professional", text="b2", skills=["Spring Boot"]),
                    Bullet(id="b3", type="professional", text="b3", skills=["Kafka"]),
                ],
            )
        ],
        projects=[
            ProjectEntry(
                id="p1",
                name="Mobile App",
                relevance="low_for_backend",
                bullets=[Bullet(id="pb1", type="project", text="pb1", skills=["Android", "Kotlin"])],
            ),
            ProjectEntry(
                id="p2",
                name="Web App",
                relevance=None,
                bullets=[Bullet(id="pb2", type="project", text="pb2", skills=["TypeScript", "React"])],
            ),
            ProjectEntry(
                id="p3",
                name="AI Search",
                relevance=None,
                bullets=[Bullet(id="pb3", type="project", text="pb3", skills=["pgvector", "Python"])],
            ),
            ProjectEntry(
                id="p4",
                name="DevOps Tool",
                relevance=None,
                bullets=[Bullet(id="pb4", type="project", text="pb4", skills=["Docker"])],
            ),
        ],
    )

    matched_skills = [
        {"skill": "Java", "level": EvidenceLevel.VERIFIED_PROFESSIONAL.value},
        {"skill": "TypeScript", "level": EvidenceLevel.VERIFIED_PROJECT.value},
        {"skill": "pgvector", "level": EvidenceLevel.VERIFIED_PROJECT.value},
        {"skill": "Docker", "level": EvidenceLevel.VERIFIED_PROJECT.value},
    ]

    # Case 1: Backend role where required skills (Java) are covered by professional bullets
    ranked_1 = rank_bullets(
        resume_base=resume,
        matched_skills=matched_skills,
        preferred_skills=[],
        job_title="Java Backend Engineer",
        top_n=8,
        required_skills=["Java"],
    )
    # Project bullets not needed, pb1 (low_for_backend) excluded
    assert "pb1" not in ranked_1
    assert "pb2" not in ranked_1
    assert ranked_1 == ["b1", "b2", "b3"]

    # Case 2: Backend role with required skills missing from professional bullets (TypeScript, pgvector, Docker)
    ranked_2 = rank_bullets(
        resume_base=resume,
        matched_skills=matched_skills,
        preferred_skills=[],
        job_title="Senior Backend Engineer",
        top_n=8,
        required_skills=["Java", "TypeScript", "pgvector", "Docker"],
    )
    # Professional bullets rank above project bullets
    assert ranked_2[:3] == ["b1", "b2", "b3"]
    # Project bullets capped at 2
    proj_in_ranked = [bid for bid in ranked_2 if bid.startswith("pb")]
    assert len(proj_in_ranked) == 2
    assert "pb1" not in ranked_2  # low_for_backend excluded

    # Case 3: Full-stack role
    ranked_3 = rank_bullets(
        resume_base=resume,
        matched_skills=matched_skills,
        preferred_skills=[],
        job_title="Full Stack Engineer",
        top_n=8,
        required_skills=["Java"],
    )
    # Professional bullets rank above project bullets
    assert ranked_3[0] == "b1"
    proj_in_fs = [bid for bid in ranked_3 if bid.startswith("pb")]
    assert len(proj_in_fs) <= 2
