"""Tests for pipeline.analyze — LLM job extraction, caching, truncation, redaction, and capping."""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import MagicMock

import pytest

from jobpilot.config import AppConfig, LLMConfig
from jobpilot.db import get_connection, init_schema, upsert_job
from jobpilot.llm.client import (
    GeminiClient,
    LLMQuotaExhaustedError,
    LLMValidationError,
)
from jobpilot.models import JobAnalysis
from jobpilot.pipeline.analyze import (
    get_unmatched_skills_with_counts,
    run_analysis,
)


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    db_path = tmp_path / "test.db"
    c = get_connection(db_path)
    init_schema(c)
    return c


@pytest.fixture
def config() -> AppConfig:
    return AppConfig(
        llm=LLMConfig(
            model_extract="gemini-2.0-flash",
            daily_call_cap=5,
            max_description_chars=500,
        )
    )


def sample_job_analysis() -> JobAnalysis:
    return JobAnalysis(
        normalized_title="Backend Engineer",
        seniority="mid",
        experience_min_years=3.0,
        experience_max_years=5.0,
        required_skills=["springboot", "java", "k8s", "custom_unknown_lib"],
        preferred_skills=["kafka", "another_unknown_tool"],
        responsibilities_summary="Build high throughput backend services.",
        location_type="remote",
        locations=["Bangalore"],
    )


def test_successful_analysis_and_normalization(conn: sqlite3.Connection, config: AppConfig) -> None:
    job_id = upsert_job(
        conn,
        {
            "fingerprint": "fp1",
            "source": "manual",
            "company": "Acme Corp",
            "title": "Senior Java Developer",
            "url": "https://example.com/job/1",
            "description": "Looking for a developer with Spring Boot, Java, and Kubernetes.",
            "status": "new",
        },
    )

    mock_client = MagicMock(spec=GeminiClient)
    mock_client.is_cap_reached.return_value = False
    mock_client.generate_json.return_value = sample_job_analysis()

    summary = run_analysis(conn, config=config, client=mock_client)
    assert summary.scanned == 1
    assert summary.analyzed == 1
    assert summary.cached == 0
    assert summary.failed == 0

    # Job status updated to analyzed
    row = conn.execute("SELECT status, status_reason FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row["status"] == "analyzed"
    assert row["status_reason"] is None

    # Analysis saved in DB with normalized skills
    analysis_row = conn.execute("SELECT * FROM analyses WHERE job_id = ?", (job_id,)).fetchone()
    assert analysis_row is not None
    data = json.loads(analysis_row["analysis_json"])
    assert "Spring Boot" in data["required_skills"]  # normalized from 'springboot'
    assert "Kubernetes" in data["required_skills"]   # normalized from 'k8s'
    assert "custom_unknown_lib" in data["required_skills"]


def test_cache_hit_avoids_llm_call(conn: sqlite3.Connection, config: AppConfig) -> None:
    upsert_job(
        conn,
        {
            "fingerprint": "fp1",
            "source": "manual",
            "company": "Acme Corp",
            "title": "Senior Java Developer",
            "url": "https://example.com/job/1",
            "description": "Looking for a developer with Spring Boot and Java.",
            "status": "new",
        },
    )

    mock_client = MagicMock(spec=GeminiClient)
    mock_client.is_cap_reached.return_value = False
    mock_client.generate_json.return_value = sample_job_analysis()

    # Run 1: LLM call
    summary1 = run_analysis(conn, config=config, client=mock_client)
    assert summary1.analyzed == 1
    assert mock_client.generate_json.call_count == 1

    # Reset job back to new (simulate re-running analysis)
    conn.execute("UPDATE jobs SET status = 'new'")
    conn.commit()

    # Run 2: Cache hit
    summary2 = run_analysis(conn, config=config, client=mock_client)
    assert summary2.cached == 1
    assert summary2.analyzed == 0
    # No new call made
    assert mock_client.generate_json.call_count == 1

    # Usage table records cache hit
    cache_usage = conn.execute("SELECT * FROM llm_usage WHERE cached = 1").fetchone()
    assert cache_usage is not None


def test_needs_jd_and_empty_desc_skipped(conn: sqlite3.Connection, config: AppConfig) -> None:
    upsert_job(
        conn,
        {
            "fingerprint": "fp_alert",
            "source": "gmail_linkedin",
            "company": "AlertCo",
            "title": "Engineer",
            "url": "https://example.com/1",
            "description": None,
            "status": "needs_jd",
        },
    )
    upsert_job(
        conn,
        {
            "fingerprint": "fp_empty",
            "source": "manual",
            "company": "EmptyCo",
            "title": "Engineer",
            "url": "https://example.com/2",
            "description": "   ",
            "status": "new",
        },
    )

    mock_client = MagicMock(spec=GeminiClient)
    mock_client.is_cap_reached.return_value = False

    summary = run_analysis(conn, config=config, client=mock_client)
    assert summary.scanned == 0
    assert summary.analyzed == 0
    assert mock_client.generate_json.call_count == 0


def test_daily_cap_stops_cleanly(conn: sqlite3.Connection, config: AppConfig) -> None:
    job_id = upsert_job(
        conn,
        {
            "fingerprint": "fp1",
            "source": "manual",
            "company": "Acme Corp",
            "title": "Developer",
            "url": "https://example.com/1",
            "description": "Enterprise Java role.",
            "status": "new",
        },
    )

    mock_client = MagicMock(spec=GeminiClient)
    mock_client.is_cap_reached.return_value = True

    summary = run_analysis(conn, config=config, client=mock_client)
    assert summary.cap_reached is True
    assert summary.analyzed == 0
    assert mock_client.generate_json.call_count == 0

    # Job remains new for next run
    row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row["status"] == "new"


def test_truncation_and_redaction(conn: sqlite3.Connection, config: AppConfig) -> None:
    long_desc = (
        "Contact recruiter at recruiter@secret.com or phone +91 98765 43210. "
        + ("A" * 1000)
    )

    upsert_job(
        conn,
        {
            "fingerprint": "fp_long",
            "source": "manual",
            "company": "BigCo",
            "title": "Dev",
            "url": "https://example.com/1",
            "description": long_desc,
            "status": "new",
        },
    )

    mock_client = MagicMock(spec=GeminiClient)
    mock_client.is_cap_reached.return_value = False
    mock_client.generate_json.return_value = sample_job_analysis()

    run_analysis(conn, config=config, client=mock_client)

    # Inspect prompt passed to LLM
    call_args = mock_client.generate_json.call_args
    prompt_sent = call_args.kwargs["prompt"]

    assert "recruiter@secret.com" not in prompt_sent
    assert "[REDACTED_EMAIL]" in prompt_sent
    assert "98765 43210" not in prompt_sent
    assert "[REDACTED_PHONE]" in prompt_sent
    # Verified description was truncated (max 500 chars from config)
    assert len(prompt_sent) < 1500


def test_analysis_failed_status_on_error(conn: sqlite3.Connection, config: AppConfig) -> None:
    job_id = upsert_job(
        conn,
        {
            "fingerprint": "fp_err",
            "source": "manual",
            "company": "ErrCo",
            "title": "Dev",
            "url": "https://example.com/1",
            "description": "Valid JD description.",
            "status": "new",
        },
    )

    mock_client = MagicMock(spec=GeminiClient)
    mock_client.is_cap_reached.return_value = False
    mock_client.generate_json.side_effect = LLMValidationError("JSON schema error")

    summary = run_analysis(conn, config=config, client=mock_client)
    assert summary.failed == 1
    assert summary.analyzed == 0

    row = conn.execute("SELECT status, status_reason FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row["status"] == "analysis_failed"
    assert "JSON schema error" in row["status_reason"]


def test_rate_limit_leaves_jobs_retryable_and_halts(
    conn: sqlite3.Connection, config: AppConfig
) -> None:
    """A provider limit must stop the run and leave jobs in 'new', not 'analysis_failed'."""
    job_ids = [
        upsert_job(
            conn,
            {
                "fingerprint": f"fp_rl_{i}",
                "source": "manual",
                "company": f"Co{i}",
                "title": "Backend Engineer",
                "url": f"https://example.com/{i}",
                "description": "Valid JD description.",
                "status": "new",
            },
        )
        for i in range(3)
    ]

    mock_client = MagicMock(spec=GeminiClient)
    mock_client.is_cap_reached.return_value = False
    mock_client.generate_json.side_effect = LLMQuotaExhaustedError("quota exhausted")

    summary = run_analysis(conn, config=config, client=mock_client)

    assert summary.cap_reached is True
    assert summary.failed == 0
    assert summary.analyzed == 0
    # Stopped at the first job rather than burning the limit on every one.
    assert mock_client.generate_json.call_count == 1
    assert summary.stop_reason is not None

    for jid in job_ids:
        row = conn.execute("SELECT status FROM jobs WHERE id = ?", (jid,)).fetchone()
        assert row["status"] == "new"


def test_unmatched_skills_summary(conn: sqlite3.Connection, config: AppConfig) -> None:
    upsert_job(
        conn,
        {
            "fingerprint": "fp1",
            "source": "manual",
            "company": "Acme",
            "title": "Dev",
            "url": "https://example.com/1",
            "description": "Description text.",
            "status": "new",
        },
    )

    mock_client = MagicMock(spec=GeminiClient)
    mock_client.is_cap_reached.return_value = False
    mock_client.generate_json.return_value = sample_job_analysis()

    run_analysis(conn, config=config, client=mock_client)

    unmatched = get_unmatched_skills_with_counts(conn)
    unmatched_names = [name for name, _ in unmatched]
    assert "custom_unknown_lib" in unmatched_names
    assert "another_unknown_tool" in unmatched_names
    # Normalized canonical skills should NOT be in unmatched
    assert "Spring Boot" not in unmatched_names
    assert "Java" not in unmatched_names
    assert "Kubernetes" not in unmatched_names
