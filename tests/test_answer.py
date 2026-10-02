"""Tests for pipeline.answer.py"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from jobpilot.config import AppConfig
from jobpilot.db import get_connection, init_schema
from jobpilot.models import (
    EvidenceLevel,
    Experience,
    Identity,
    Profile,
    Settings,
    Skill,
    Targets,
)
from jobpilot.pipeline.answer import (
    answer_profile_fact,
    answer_question,
    answer_setting,
    approve_answer,
    classify_question,
    normalize_question,
    set_answer,
)


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
            client_display="a global financial-services client",
        ),
        skills=[
            Skill(name="Java", category="backend", level=EvidenceLevel.VERIFIED_PROFESSIONAL),
            Skill(name="Spring Boot", category="backend", level=EvidenceLevel.VERIFIED_PROFESSIONAL),
            Skill(name="TypeScript", category="frontend", level=EvidenceLevel.VERIFIED_PROJECT),
            Skill(name="AWS", category="cloud", level=EvidenceLevel.VERIFIED_PROJECT),
            Skill(name="MCP", category="ai", level=EvidenceLevel.LEARNING),
        ],
        settings=Settings(
            salary_expectation=None,
            notice_period_days=30,
            open_to_relocation=None,
            visa_sponsorship_needed=False,
            work_authorization_india=True,
            work_authorization_other_countries=None,
            remote_ok=True,
        ),
        targets=Targets(primary_titles=["Software Engineer"]),
    )


@pytest.fixture
def mock_config() -> AppConfig:
    return AppConfig()


def test_question_normalization():
    assert normalize_question("  What is your EXPECTED salary?? ") == "what is your expected salary"
    assert normalize_question("Years of experience???") == "years of experience"


def test_classify_question_basic(mock_profile):
    assert classify_question("What is your expected salary?", mock_profile) == "setting"
    assert classify_question("How many years of experience do you have?", mock_profile) == "profile_fact"
    assert classify_question("Do you have experience with Java?", mock_profile) == "profile_fact"
    assert classify_question("Tell me about a challenging bug you fixed", mock_profile) == "free_text"


# Table-driven tests with at least 30 real-style Indian and international application questions
SETTING_QUESTIONS = [
    # Indian market: CTC / Pay
    "What is your current CTC and expected CTC (in LPA)?",
    "What is your expected CTC?",
    "Please share your Current Fixed CTC + Variable component.",
    "What is your remuneration expectation for this role?",
    "What is your expected monthly stipend?",
    "What is your target base pay?",
    # Indian market: Notice / LWD / Buyout / Joining
    "What is your official notice period in days?",
    "Are you currently serving notice period?",
    "What is your Last Working Day (LWD)?",
    "Can your notice period be bought out by the employer?",
    "How soon can you join if an offer is rolled out?",
    "What is your earliest joining date?",
    "What is your joining time / availability?",
    # Indian market: Relocation / Onsite / Shifts / Authorization
    "Are you open to relocation to Bangalore, Pune or Hyderabad?",
    "Are you comfortable working 3 days onsite from our Gurgaon office?",
    "Are you willing to work in rotational shifts?",
    "Are you comfortable with night shifts (US working hours)?",
    "Are you authorized to work in India without requiring sponsorship?",
    "What is your current employment type (Full-time / Contract)?",
    # International: US / UK / Canada Pay
    "What are your salary expectations for this position (USD)?",
    "What is your desired annual base compensation?",
    "What is your expected hourly pay rate?",
    "What is your target total compensation package?",
    # International: US / UK / Global Visa & Work Authorization
    "Will you now or in the future require visa sponsorship to work in the United States?",
    "Are you legally authorized to work in the US?",
    "Do you possess unrestricted work authorization in the UK?",
    "Do you have a valid work permit or require visa sponsorship?",
    "What is your citizenship or right to work status?",
    "Are you legally authorized in the United States?",
    # International: Remote / Relocation / Onsite / Shifts / Contract
    "Are you open to relocation for this role?",
    "This position is hybrid (2 days per week in London). Can you commit to onsite attendance?",
    "Can you work remote full time from anywhere in APAC?",
    "Are you available for 24x7 weekend on-call shifts?",
    "Are you looking for W2 or C2C contract employment?",
    "What is your earliest possible start date?",
]


@pytest.mark.parametrize("question", SETTING_QUESTIONS)
def test_classify_question_table_driven_settings(question: str, mock_profile: Profile):
    assert classify_question(question, mock_profile) == "setting"


def test_profile_fact_years_of_experience(mock_profile):
    ans = answer_profile_fact("How many years of experience do you have?", mock_profile)
    assert ans == "4"


def test_profile_fact_skill_professional(mock_profile):
    ans = answer_profile_fact("Do you have experience with Java?", mock_profile)
    assert "professional experience with Java" in ans
    assert "personal project" not in ans


def test_profile_fact_skill_project_only(mock_profile):
    ans = answer_profile_fact("Do you have experience with TypeScript?", mock_profile)
    assert "personal project" in ans
    assert "professional experience" not in ans


def test_profile_fact_skill_learning(mock_profile):
    ans = answer_profile_fact("Do you know MCP?", mock_profile)
    assert "currently learning MCP" in ans
    assert "professional experience" not in ans


def test_setting_null_returns_needs_input(mock_profile):
    ans = answer_setting("What is your expected salary?", mock_profile)
    assert ans == "NEEDS YOUR INPUT"

    ans_reloc = answer_setting("Are you willing to relocate?", mock_profile)
    assert ans_reloc == "NEEDS YOUR INPUT"


def test_setting_with_value(mock_profile):
    ans = answer_setting("What is your notice period?", mock_profile)
    assert ans == "30 days"

    ans_visa = answer_setting("Do you need visa sponsorship?", mock_profile)
    assert ans_visa == "False"


def test_setting_never_reused_from_bank(conn, mock_profile, mock_config):
    """Setting answers must NEVER be reused from the answer bank; recompute from profile."""
    q_norm = normalize_question("What is your expected salary?")
    conn.execute(
        """
        INSERT INTO answer_bank (question_norm, category, answer, approved, created_at, updated_at)
        VALUES (?, 'setting', '1000000 INR (Cached)', 1, datetime('now'), datetime('now'))
        """,
        (q_norm,),
    )
    conn.commit()

    # In mock_profile, salary_expectation is None -> MUST return NEEDS YOUR INPUT, not the cached bank value!
    res = answer_question("What is your expected salary?", mock_profile, mock_config, conn=conn)
    assert res.from_bank is False
    assert res.answer == "NEEDS YOUR INPUT"

    # Now set a salary on profile
    mock_profile.settings.salary_expectation = "25 LPA"
    res2 = answer_question("What is your expected salary?", mock_profile, mock_config, conn=conn)
    assert res2.from_bank is False
    assert res2.answer == "25 LPA"


def test_profile_fact_never_reused_from_bank(conn, mock_profile, mock_config):
    """Profile fact answers must NEVER be reused from the answer bank; recompute from profile."""
    q_norm = normalize_question("How many years of experience do you have?")
    conn.execute(
        """
        INSERT INTO answer_bank (question_norm, category, answer, approved, created_at, updated_at)
        VALUES (?, 'profile_fact', '10 years (Cached)', 1, datetime('now'), datetime('now'))
        """,
        (q_norm,),
    )
    conn.commit()

    # In mock_profile, years_for_forms is 4 -> MUST return 4, not 10!
    res = answer_question("How many years of experience do you have?", mock_profile, mock_config, conn=conn)
    assert res.from_bank is False
    assert res.answer == "4"


def test_free_text_generic_reused_from_bank(conn, mock_profile, mock_config):
    """Only approved free_text answers not mentioning company or job are reused."""
    q = "Describe a challenging technical architecture you designed."
    q_norm = normalize_question(q)
    conn.execute(
        """
        INSERT INTO answer_bank (question_norm, category, answer, approved, created_at, updated_at)
        VALUES (?, 'free_text', 'I architected an event-driven system.', 1, datetime('now'), datetime('now'))
        """,
        (q_norm,),
    )
    conn.commit()

    mock_client = MagicMock()
    res = answer_question(q, mock_profile, mock_config, conn=conn, llm_client=mock_client)
    assert res.from_bank is True
    assert res.is_draft is False
    assert res.answer == "I architected an event-driven system."
    assert mock_client.generate_text.call_count == 0


def test_free_text_mentioning_company_or_job_not_reused(conn, mock_profile, mock_config):
    """Questions mentioning company or job must NOT be reused across jobs."""
    q = "Why do you want to work at this company for this role?"
    q_norm = normalize_question(q)
    conn.execute(
        """
        INSERT INTO answer_bank (question_norm, category, answer, approved, created_at, updated_at)
        VALUES (?, 'free_text', 'I love Acme Corp.', 1, datetime('now'), datetime('now'))
        """,
        (q_norm,),
    )
    conn.commit()

    mock_client = MagicMock()
    mock_client.generate_text.return_value = "Fresh answer generated for this specific role."

    res = answer_question(q, mock_profile, mock_config, conn=conn, llm_client=mock_client)
    # Must NOT reuse the cached answer
    assert res.from_bank is False
    assert res.answer == "Fresh answer generated for this specific role."
    assert mock_client.generate_text.call_count == 1


def test_free_text_stored_as_draft(conn, mock_profile, mock_config):
    mock_client = MagicMock()
    mock_client.generate_text.return_value = "I have extensive experience building scalable web applications."

    res = answer_question(
        "Describe your proudest engineering project.",
        mock_profile,
        mock_config,
        conn=conn,
        llm_client=mock_client,
    )

    assert res.is_draft is True
    assert res.from_bank is False
    assert res.answer == "I have extensive experience building scalable web applications."

    row = conn.execute("SELECT answer, approved FROM answer_bank WHERE id = ?", (res.answer_bank_id,)).fetchone()
    assert row["approved"] == 0
    assert row["answer"] == res.answer


def test_approve_answer(conn):
    conn.execute(
        """
        INSERT INTO answer_bank (question_norm, category, answer, approved, created_at, updated_at)
        VALUES ('test question', 'free_text', 'Draft answer', 0, datetime('now'), datetime('now'))
        """
    )
    conn.commit()
    row = conn.execute("SELECT id FROM answer_bank WHERE question_norm = 'test question'").fetchone()
    ans_id = row[0]

    success = approve_answer(conn, ans_id)
    assert success is True

    row_after = conn.execute("SELECT approved FROM answer_bank WHERE id = ?", (ans_id,)).fetchone()
    assert row_after["approved"] == 1


def test_set_answer(conn):
    """answer --set <id> 'text' updates the answer and sets approved = 1."""
    conn.execute(
        """
        INSERT INTO answer_bank (question_norm, category, answer, approved, created_at, updated_at)
        VALUES ('expected salary', 'setting', 'NEEDS YOUR INPUT', 0, datetime('now'), datetime('now'))
        """
    )
    conn.commit()
    row = conn.execute("SELECT id FROM answer_bank WHERE question_norm = 'expected salary'").fetchone()
    ans_id = row[0]

    success = set_answer(conn, ans_id, "25 LPA, 30 days notice")
    assert success is True

    row_after = conn.execute("SELECT answer, approved FROM answer_bank WHERE id = ?", (ans_id,)).fetchone()
    assert row_after["answer"] == "25 LPA, 30 days notice"
    assert row_after["approved"] == 1
