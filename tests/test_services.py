"""Tests for JobPilot service layer (jobpilot.services)."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jobpilot import services
from jobpilot.config import AppConfig
from jobpilot.db import get_connection, init_schema
from jobpilot.models import (
    Bullet,
    EvidenceLevel,
    Experience,
    Identity,
    JobAnalysis,
    Profile,
    ResumeBase,
    Role,
    Settings,
    Skill,
)
from jobpilot.pipeline.daily import DailyPipelineSummary, StageResult
from jobpilot.pipeline.prepare import PrepareResult


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    """In-memory or temp SQLite connection with schema initialized."""
    db_file = tmp_path / "test_services.db"
    c = get_connection(db_file)
    init_schema(c)
    return c


@pytest.fixture
def minimal_profile() -> Profile:
    """Minimal test profile."""
    return Profile(
        schema_version=2,
        identity=Identity(name="Test Candidate", base_location="Bangalore, India"),
        experience=Experience(total_years_actual=4.0, years_for_forms=4),
        settings=Settings(work_authorization_india=True),
        skills=[
            Skill(name="Python", category="core_backend", level=EvidenceLevel.VERIFIED_PROFESSIONAL, evidence=["b01"]),
            Skill(name="Docker", category="devops", level=EvidenceLevel.VERIFIED_PROJECT, evidence=["p01"]),
            Skill(name="Kubernetes", category="devops", level=EvidenceLevel.LEARNING),
        ],
    )


@pytest.fixture
def minimal_resume() -> ResumeBase:
    """Minimal test resume base."""
    return ResumeBase(
        schema_version=2,
        summary_variants=[{"id": "sum1", "text": "Experienced Python Backend Engineer."}],
        roles=[
            Role(
                id="r01",
                employer="Acme Corp",
                bullets=[
                    Bullet(
                        id="b01",
                        type="professional",
                        text="Built scalable Python microservices.",
                        skills=["Python"],
                    )
                ],
            )
        ],
    )


class TestServicesQueueAndNeedsJD:
    def test_get_queue_returns_ranked_items(self, conn: sqlite3.Connection) -> None:
        """get_queue returns ranked QueueItem objects."""
        now = datetime.now(UTC).isoformat()
        conn.execute(
            """
            INSERT INTO jobs (id, fingerprint, source, company, title, url, discovered_at, last_seen_at, status)
            VALUES (1, 'fp-1', 'greenhouse', 'Acme', 'Python Eng', 'https://example.com/1', ?, ?, 'scored')
            """,
            (now, now),
        )
        conn.execute(
            """
            INSERT INTO scores (job_id, total, tier, skills_score, experience_score, scored_at)
            VALUES (1, 85.0, 'A', 35.0, 18.0, ?)
            """,
            (now,),
        )
        conn.commit()

        items = services.get_queue(size=5, conn=conn)
        assert len(items) == 1
        assert items[0].job_id == 1
        assert items[0].tier == "A"
        assert items[0].company == "Acme"

    def test_get_needs_jd_returns_items(self, conn: sqlite3.Connection) -> None:
        """get_needs_jd returns jobs awaiting full JD paste."""
        now = datetime.now(UTC).isoformat()
        conn.execute(
            """
            INSERT INTO jobs (id, fingerprint, source, company, title, url, discovered_at, last_seen_at, status)
            VALUES (2, 'fp-2', 'manual', 'Beta Corp', 'Staff Eng', 'https://example.com/2', ?, ?, 'needs_jd')
            """,
            (now, now),
        )
        conn.commit()

        items = services.get_needs_jd(conn=conn)
        assert len(items) == 1
        assert items[0].job_id == 2
        assert items[0].company == "Beta Corp"


class TestServicesJobDetail:
    def test_get_job_detail_comprehensive(self, conn: sqlite3.Connection) -> None:
        """get_job_detail returns parsed analysis, categorized score breakdown, and draft."""
        now = datetime.now(UTC).isoformat()
        conn.execute(
            """
            INSERT INTO jobs (id, fingerprint, source, company, title, url, discovered_at, last_seen_at, status)
            VALUES (10, 'fp-10', 'greenhouse', 'Gamma', 'Backend Lead', 'https://example.com/10', ?, ?, 'prepared')
            """,
            (now, now),
        )

        analysis = JobAnalysis(
            normalized_title="Backend Lead",
            required_skills=["Python", "PostgreSQL", "Kafka"],
            preferred_skills=["Docker", "Kubernetes", "AWS"],
            summary="Leading backend development team.",
        )
        conn.execute(
            """
            INSERT INTO analyses (job_id, prompt_version, model, content_hash, analysis_json, created_at)
            VALUES (10, 'v1', 'gemini-2.0-flash', 'hash10', ?, ?)
            """,
            (analysis.model_dump_json(), now),
        )

        matched_skills = [
            {"skill": "Python", "level": EvidenceLevel.VERIFIED_PROFESSIONAL.value, "evidence_ids": ["b01"]},
            {"skill": "Docker", "level": EvidenceLevel.VERIFIED_PROJECT.value, "evidence_ids": ["p01"]},
            {"skill": "Kubernetes", "level": EvidenceLevel.LEARNING.value, "evidence_ids": []},
        ]
        conn.execute(
            """
            INSERT INTO scores (
                job_id, total, tier, skills_score, experience_score, seniority_score, location_score, extras_score,
                matched_skills, missing_required, missing_preferred, flags, scored_at
            )
            VALUES (10, 88.0, 'A', 36.0, 18.0, 14.0, 12.0, 8.0, ?, ?, ?, ?, ?)
            """,
            (
                json.dumps(matched_skills),
                json.dumps(["Kafka"]),
                json.dumps(["AWS"]),
                json.dumps(["high_experience"]),
                now,
            ),
        )

        conn.execute(
            """
            INSERT INTO applications (job_id, tailored_summary, short_note, bullet_ids, prepared_at)
            VALUES (10, 'Custom backend summary', 'Custom short note', '["b01"]', ?)
            """,
            (now,),
        )
        conn.commit()

        detail = services.get_job_detail(10, conn=conn)

        assert detail.id == 10
        assert detail.company == "Gamma"
        assert detail.analysis is not None
        assert detail.analysis.normalized_title == "Backend Lead"

        assert detail.score is not None
        assert detail.score.total == 88.0
        assert detail.score.tier == "A"
        assert len(detail.score.covered_skills) == 1
        assert detail.score.covered_skills[0].skill == "Python"
        assert len(detail.score.partial_skills) == 1
        assert detail.score.partial_skills[0].skill == "Docker"
        assert len(detail.score.learning_only_skills) == 1
        assert detail.score.learning_only_skills[0].skill == "Kubernetes"
        assert set(detail.score.missing_skills) == {"Kafka", "AWS"}
        assert detail.score.flags == ["high_experience"]

        assert detail.application is not None
        assert detail.application.tailored_summary == "Custom backend summary"
        assert detail.application.bullet_ids == ["b01"]

    def test_get_job_detail_not_found(self, conn: sqlite3.Connection) -> None:
        """get_job_detail raises KeyError for unknown job."""
        with pytest.raises(KeyError):
            services.get_job_detail(999, conn=conn)


class TestServicesStatusTransitions:
    def test_mark_job_success(self, conn: sqlite3.Connection) -> None:
        """mark_job transitions status and records channel/referral_contact."""
        now = datetime.now(UTC).isoformat()
        conn.execute(
            """
            INSERT INTO jobs (id, fingerprint, source, company, title, url, discovered_at, last_seen_at, status)
            VALUES (20, 'fp-20', 'greenhouse', 'Zeta', 'Engineer', 'https://example.com/20', ?, ?, 'queued')
            """,
            (now, now),
        )
        conn.commit()

        update = services.mark_job(
            job_id=20,
            status="applied",
            channel="referral",
            note="Referral via Alex",
            referral_contact="Alex Smith",
            conn=conn,
        )

        assert update.job_id == 20
        assert update.previous_status == "queued"
        assert update.new_status == "applied"
        assert update.channel == "referral"
        assert update.referral_contact == "Alex Smith"

        row = conn.execute("SELECT status FROM jobs WHERE id = 20").fetchone()
        assert row["status"] == "applied"
        app_row = conn.execute("SELECT referral_contact, channel, notes FROM applications WHERE job_id = 20").fetchone()
        assert app_row["referral_contact"] == "Alex Smith"
        assert app_row["channel"] == "referral"
        assert app_row["notes"] == "Referral via Alex"

    def test_skip_job(self, conn: sqlite3.Connection) -> None:
        """skip_job transitions to skipped."""
        now = datetime.now(UTC).isoformat()
        conn.execute(
            """
            INSERT INTO jobs (id, fingerprint, source, company, title, url, discovered_at, last_seen_at, status)
            VALUES (21, 'fp-21', 'greenhouse', 'Zeta', 'Engineer', 'https://example.com/21', ?, ?, 'queued')
            """,
            (now, now),
        )
        conn.commit()

        update = services.skip_job(job_id=21, reason="Location unsuitable", conn=conn)
        assert update.new_status == "skipped"
        assert update.note == "Location unsuitable"

    def test_add_jd(self, conn: sqlite3.Connection) -> None:
        """add_jd updates JD text and transitions status to new."""
        now = datetime.now(UTC).isoformat()
        conn.execute(
            """
            INSERT INTO jobs (id, fingerprint, source, company, title, url, discovered_at, last_seen_at, status)
            VALUES (22, 'fp-22', 'manual', 'Zeta', 'Engineer', 'https://example.com/22', ?, ?, 'needs_jd')
            """,
            (now, now),
        )
        conn.commit()

        res = services.add_jd(22, "Detailed description for the job here.", conn=conn)
        assert res.job_id == 22
        assert res.status == "new"
        assert res.char_count > 0

        row = conn.execute("SELECT status, description FROM jobs WHERE id = 22").fetchone()
        assert row["status"] == "new"
        assert "Detailed description" in row["description"]


class TestServicesApplicationsAndFollowUps:
    def test_list_applications_filter(self, conn: sqlite3.Connection) -> None:
        """list_applications returns application items optionally filtered by status."""
        now = datetime.now(UTC).isoformat()
        for jid, st in [(31, "applied"), (32, "interview"), (33, "rejected")]:
            conn.execute(
                """
                INSERT INTO jobs (id, fingerprint, source, company, title, url, discovered_at, last_seen_at, status)
                VALUES (?, ?, 'greenhouse', 'Co', 'Title', 'https://example.com', ?, ?, ?)
                """,
                (jid, f"fp-{jid}", now, now, st),
            )
            conn.execute(
                """
                INSERT INTO applications (job_id, applied_at, channel)
                VALUES (?, ?, 'portal')
                """,
                (jid, now),
            )
        conn.commit()

        all_apps = services.list_applications(conn=conn)
        assert len(all_apps) == 3

        applied_apps = services.list_applications(status="applied", conn=conn)
        assert len(applied_apps) == 1
        assert applied_apps[0].job_id == 31

    def test_follow_ups_due_filters_past_cutoff(self, conn: sqlite3.Connection) -> None:
        """follow_ups_due returns only jobs applied before cutoff with status 'applied'."""
        now = datetime.now(UTC)
        old_date = (now - timedelta(days=10)).isoformat()
        recent_date = (now - timedelta(days=2)).isoformat()

        # Job 41: applied 10 days ago (DUE)
        conn.execute(
            """
            INSERT INTO jobs (id, fingerprint, source, company, title, url, discovered_at, last_seen_at, status)
            VALUES (41, 'fp-41', 'lever', 'OldCo', 'Senior Dev', 'https://example.com/41', ?, ?, 'applied')
            """,
            (old_date, old_date),
        )
        conn.execute("INSERT INTO applications (job_id, applied_at) VALUES (41, ?)", (old_date,))

        # Job 42: applied 2 days ago (NOT DUE)
        conn.execute(
            """
            INSERT INTO jobs (id, fingerprint, source, company, title, url, discovered_at, last_seen_at, status)
            VALUES (42, 'fp-42', 'lever', 'RecentCo', 'Junior Dev', 'https://example.com/42', ?, ?, 'applied')
            """,
            (recent_date, recent_date),
        )
        conn.execute("INSERT INTO applications (job_id, applied_at) VALUES (42, ?)", (recent_date,))

        # Job 43: applied 10 days ago but moved to interview (NOT DUE)
        conn.execute(
            """
            INSERT INTO jobs (id, fingerprint, source, company, title, url, discovered_at, last_seen_at, status)
            VALUES (43, 'fp-43', 'lever', 'InterviewCo', 'Lead Dev', 'https://example.com/43', ?, ?, 'interview')
            """,
            (old_date, old_date),
        )
        conn.execute("INSERT INTO applications (job_id, applied_at) VALUES (43, ?)", (old_date,))
        conn.commit()

        due = services.follow_ups_due(days=7, conn=conn)
        assert len(due) == 1
        assert due[0].job_id == 41
        assert due[0].company == "OldCo"
        assert due[0].days_since_applied >= 10


class TestServicesAnswersAndStats:
    def test_answer_question_setting(self, conn: sqlite3.Connection, minimal_profile: Profile) -> None:
        """answer_question classifies setting question and returns NEEDS YOUR INPUT if unset."""
        res = services.answer_question(
            question="What is your expected CTC in INR?",
            conn=conn,
            profile=minimal_profile,
        )
        assert res.category == "setting"
        assert "NEEDS YOUR INPUT" in res.answer

    def test_answer_bank_lifecycle(self, conn: sqlite3.Connection) -> None:
        """list_answers, set_answer, and approve_answer manage the answer bank."""
        now = datetime.now(UTC).isoformat()
        conn.execute(
            """
            INSERT INTO answer_bank (id, question_norm, category, answer, approved, created_at, updated_at)
            VALUES (50, 'why do you want to join us', 'free_text', 'Draft reason', 0, ?, ?)
            """,
            (now, now),
        )
        conn.commit()

        items = services.list_answers(conn=conn)
        assert len(items) == 1
        assert items[0].id == 50
        assert not items[0].approved

        # Approve answer
        ok = services.approve_answer(50, conn=conn)
        assert ok
        row = conn.execute("SELECT approved FROM answer_bank WHERE id = 50").fetchone()
        assert row["approved"] == 1

        # Set answer text
        ok_set = services.set_answer(50, "Updated reason to join.", conn=conn)
        assert ok_set
        row_set = conn.execute("SELECT answer FROM answer_bank WHERE id = 50").fetchone()
        assert row_set["answer"] == "Updated reason to join."

    def test_get_stats_report(self, conn: sqlite3.Connection) -> None:
        """get_stats returns FullStatsReport."""
        report = services.get_stats(weeks=4, conn=conn)
        assert report.funnel is not None
        assert report.applications is not None


class TestServicesPrepareAndRunDaily:
    def test_prepare_job_service(
        self,
        conn: sqlite3.Connection,
        minimal_profile: Profile,
        minimal_resume: ResumeBase,
    ) -> None:
        """prepare_job delegates to prepare logic with mocked LLM."""
        now = datetime.now(UTC).isoformat()
        conn.execute(
            """
            INSERT INTO jobs (id, fingerprint, source, company, title, url, discovered_at, last_seen_at, status)
            VALUES (60, 'fp-60', 'greenhouse', 'Delta', 'Python Dev', 'https://example.com/60', ?, ?, 'scored')
            """,
            (now, now),
        )
        conn.execute(
            """
            INSERT INTO scores (job_id, total, tier, skills_score, experience_score, matched_skills, scored_at)
            VALUES (60, 85.0, 'A', 35.0, 18.0, ?, ?)
            """,
            (json.dumps([{"skill": "Python", "level": "VERIFIED_PROFESSIONAL"}]), now),
        )
        conn.execute(
            """
            INSERT INTO analyses (job_id, prompt_version, model, content_hash, analysis_json, created_at)
            VALUES (60, 'v1', 'gemini-2.0-flash', 'hash60', '{"normalized_title": "Python Dev", "required_skills": ["Python"], "preferred_skills": []}', ?)
            """,
            (now,),
        )
        conn.commit()

        mock_client = MagicMock()
        mock_client.generate_json.side_effect = [
            {"summary": "Experienced in Python."},
            {"note": "Strong Python candidate."},
        ]

        cfg = AppConfig()
        result = services.prepare_job(
            job_id=60,
            conn=conn,
            config=cfg,
            profile=minimal_profile,
            resume_base=minimal_resume,
            llm_client=mock_client,
        )
        assert isinstance(result, PrepareResult)
        assert result.job_id == 60
        assert result.tier == "A"

    def test_run_daily_summary_and_callback(self, conn: sqlite3.Connection) -> None:
        """run_daily triggers stages, logs progress callback, and returns RunSummary."""
        progress_events: list[tuple[str, str]] = []

        def callback(event: str, message: str) -> None:
            progress_events.append((event, message))

        mock_pipeline_summary = DailyPipelineSummary(
            stages=[
                StageResult(name="fetch", status="ok"),
                StageResult(name="ingest-alerts", status="error", error="Auth failed"),
                StageResult(name="analyze", status="ok"),
                StageResult(name="score", status="ok"),
                StageResult(name="queue", status="ok"),
            ]
        )

        with patch("jobpilot.services.Notifier") as mock_notifier_cls:
            mock_notifier = MagicMock()
            mock_notifier_cls.return_value = mock_notifier

            summary = services.run_daily(
                progress_callback=callback,
                conn=conn,
                notify=True,
                pipeline_fn=lambda *args, **kwargs: mock_pipeline_summary,
            )

            assert summary.has_failures
            assert len(summary.stages) == 5
            st_map = {s.name: s for s in summary.stages}
            assert st_map["ingest-alerts"].status == "error"
            assert st_map["ingest-alerts"].error == "Auth failed"

            # Check notifier called with stage failures
            mock_notifier.send_run_daily_summary.assert_called_once()
            _, kwargs = mock_notifier.send_run_daily_summary.call_args
            assert kwargs["stage_failures"] == [("ingest-alerts", "Auth failed")]

            # Check progress callback was triggered
            assert any(ev[0] == "start" for ev in progress_events)
            assert any(ev[0] == "complete" for ev in progress_events)

    def test_profile_report_summary(self, conn: sqlite3.Connection) -> None:
        """profile_report returns validation results, unset settings, and unmatched skills."""
        report = services.profile_report(conn=conn)
        assert isinstance(report.validation_errors, list)
        assert isinstance(report.unset_settings, list)
        assert isinstance(report.unmatched_skills, list)

    def test_get_progress_summary(self, conn: sqlite3.Connection) -> None:
        """get_progress computes metrics strictly from applications.applied_at and never jobs.status."""
        now = datetime.now(UTC)
        today_iso = now.isoformat()
        # Ensure a timestamp that is guaranteed to be earlier this week if weekday > 0, or today if Monday
        days_back = min(1, now.weekday())
        this_week_iso = (now - timedelta(days=days_back, hours=1)).isoformat()
        ten_days_ago_iso = (now - timedelta(days=10)).isoformat()

        # Job 1: applied today
        conn.execute(
            """
            INSERT INTO jobs (id, fingerprint, source, company, title, url, discovered_at, last_seen_at, status)
            VALUES (101, 'fp-101', 'greenhouse', 'TodayCo', 'Dev', 'https://example.com/101', ?, ?, 'applied')
            """,
            (today_iso, today_iso),
        )
        conn.execute("INSERT INTO applications (job_id, applied_at) VALUES (101, ?)", (today_iso,))

        # Job 2: applied earlier this week (or today if Monday), status is 'rejected' (proving status is ignored)
        conn.execute(
            """
            INSERT INTO jobs (id, fingerprint, source, company, title, url, discovered_at, last_seen_at, status)
            VALUES (102, 'fp-102', 'greenhouse', 'WeekCo', 'Dev', 'https://example.com/102', ?, ?, 'rejected')
            """,
            (this_week_iso, this_week_iso),
        )
        conn.execute("INSERT INTO applications (job_id, applied_at) VALUES (102, ?)", (this_week_iso,))

        # Job 3: status is 'applied', but applied_at is NULL (must NOT be counted)
        conn.execute(
            """
            INSERT INTO jobs (id, fingerprint, source, company, title, url, discovered_at, last_seen_at, status)
            VALUES (103, 'fp-103', 'greenhouse', 'NullCo', 'Dev', 'https://example.com/103', ?, ?, 'applied')
            """,
            (today_iso, today_iso),
        )
        conn.execute("INSERT INTO applications (job_id, applied_at) VALUES (103, NULL)")

        # Job 4: applied 10 days ago, status='applied' -> should count in follow-ups due
        conn.execute(
            """
            INSERT INTO jobs (id, fingerprint, source, company, title, url, discovered_at, last_seen_at, status)
            VALUES (104, 'fp-104', 'greenhouse', 'OldCo', 'Dev', 'https://example.com/104', ?, ?, 'applied')
            """,
            (ten_days_ago_iso, ten_days_ago_iso),
        )
        conn.execute("INSERT INTO applications (job_id, applied_at) VALUES (104, ?)", (ten_days_ago_iso,))
        conn.commit()

        cfg = AppConfig()
        progress = services.get_progress(conn=conn, config=cfg)

        assert progress.daily_size == cfg.queue.daily_size
        assert progress.weekly_target == cfg.queue.weekly_target
        assert progress.applied_today >= 1
        assert progress.applied_this_week >= 1
        assert progress.follow_ups_due_count >= 1


def test_location_filter_matches_the_city_shown_in_the_column(conn: sqlite3.Connection) -> None:
    now = datetime.now(UTC).isoformat()
    conn.execute(
        """
        INSERT INTO jobs (
            id, fingerprint, source, company, title, location, url, status,
            discovered_at, last_seen_at
        ) VALUES (1, 'fp1', 'gmail:linkedin', 'Unknown', ?, NULL, 'https://example.com/1', 'needs_jd', ?, ?)
        """,
        ("Full Stack EngineerDeloitte · Bengaluru (On-site)Easy Apply", now, now),
    )
    conn.execute(
        """
        INSERT INTO jobs (
            id, fingerprint, source, company, title, location, url, status,
            discovered_at, last_seen_at
        ) VALUES (2, 'fp2', 'remoteok', 'Acme', 'Backend Engineer', 'Remote', 'https://example.com/2', 'new', ?, ?)
        """,
        (now, now),
    )
    conn.execute(
        """
        INSERT INTO jobs (
            id, fingerprint, source, company, title, location, url, status,
            discovered_at, last_seen_at
        ) VALUES (
            3, 'fp3', 'adzuna', 'Northwind', 'Java Engineer',
            'Chennai, Tamil Nadu, India', 'https://example.com/3', 'new', ?, ?
        )
        """,
        (now, now),
    )
    conn.commit()

    bengaluru = services.list_searched_jobs(location="bengaluru", conn=conn)
    assert [item.job_id for item in bengaluru] == [1]
    assert bengaluru[0].location == "Bengaluru (On-site)"

    india = services.list_searched_jobs(location="India", conn=conn)
    assert [item.job_id for item in india] == [3]

    remote = services.list_searched_jobs(location="remote", conn=conn)
    assert [item.job_id for item in remote] == [2]
