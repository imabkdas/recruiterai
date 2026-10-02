"""Tests for application statistics and funnel tracking (jobpilot.tracking.stats)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from jobpilot.db import get_connection, init_schema
from jobpilot.tracking.stats import (
    get_funnel_stats,
    get_source_stats,
    get_stats_report,
    get_tier_stats,
    get_weekly_application_stats,
)


@pytest.fixture
def seeded_conn(tmp_path: Path) -> sqlite3.Connection:
    """Database populated with jobs across various statuses, sources, and tiers."""
    db_file = tmp_path / "test_stats.db"
    conn = get_connection(db_file)
    init_schema(conn)

    # 1. Insert jobs for funnel coverage
    # Total jobs = 14
    statuses = [
        ("new", "greenhouse"),
        ("filtered_out", "greenhouse"),
        ("needs_jd", "gmail_alerts_linkedin"),
        ("analyzed", "lever"),
        ("scored", "remoteok"),
        ("queued", "greenhouse"),
        ("prepared", "lever"),
        ("applied", "greenhouse"),
        ("replied", "greenhouse"),
        ("interview", "lever"),
        ("offer", "greenhouse"),
        ("rejected", "remoteok"),
        ("skipped", "lever"),
        ("expired", "greenhouse"),
    ]
    for idx, (st, src) in enumerate(statuses, start=1):
        conn.execute(
            """
            INSERT INTO jobs (id, fingerprint, source, company, title, url, discovered_at, last_seen_at, status)
            VALUES (?, ?, ?, 'Company', 'Title', 'https://example.com', '2026-09-01T00:00:00Z', '2026-09-01T00:00:00Z', ?)
            """,
            (idx, f"fp-{idx}", src, st),
        )

    # 2. Insert application records for applied/responded jobs
    # Jobs 8, 9, 10, 11, 12 were applied
    now = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)
    app_data = [
        # Job 8: applied this week (greenhouse)
        (8, (now - timedelta(days=1)).isoformat(), "linkedin"),
        # Job 9: replied this week (greenhouse)
        (9, (now - timedelta(days=2)).isoformat(), "referral"),
        # Job 10: interview last week (lever)
        (10, (now - timedelta(days=8)).isoformat(), "direct"),
        # Job 11: offer 2 weeks ago (greenhouse)
        (11, (now - timedelta(days=16)).isoformat(), "direct"),
        # Job 12: rejected 3 weeks ago (remoteok)
        (12, (now - timedelta(days=23)).isoformat(), "email"),
    ]
    for jid, applied_at, channel in app_data:
        conn.execute(
            """
            INSERT INTO applications (job_id, applied_at, channel, last_status_change)
            VALUES (?, ?, ?, ?)
            """,
            (jid, applied_at, channel, applied_at),
        )

    # 3. Insert scores for applied jobs to test tier metrics
    # Job 8: Tier A (applied)
    # Job 9: Tier A (replied)
    # Job 10: Tier B (interview)
    # Job 11: Tier A (offer)
    # Job 12: No tier / Unranked (rejected)
    score_data = [
        (8, 85.0, "A"),
        (9, 90.0, "A"),
        (10, 72.0, "B"),
        (11, 88.0, "A"),
        (12, 50.0, None),
    ]
    for jid, total, tier in score_data:
        conn.execute(
            """
            INSERT INTO scores (job_id, total, tier, scored_at)
            VALUES (?, ?, ?, '2026-09-01T00:00:00Z')
            """,
            (jid, total, tier),
        )

    conn.commit()
    return conn


class TestStatsReport:
    def test_funnel_counts(self, seeded_conn: sqlite3.Connection) -> None:
        """Funnel counts accurately aggregate each status in the pipeline and total applications."""
        funnel = get_funnel_stats(seeded_conn)

        assert funnel.total_discovered == 14
        assert funnel.filtered == 1
        assert funnel.needs_jd == 1
        assert funnel.analyzed == 1
        assert funnel.scored == 1
        assert funnel.queued == 1
        assert funnel.prepared == 1
        assert funnel.applied == 1
        assert funnel.replied == 1
        assert funnel.interview == 1
        assert funnel.offer == 1
        assert funnel.rejected == 1
        assert funnel.skipped == 1
        assert funnel.expired == 1
        assert funnel.total_applied_all_time == 5

    def test_weekly_applications_against_target(self, seeded_conn: sqlite3.Connection) -> None:
        """Weekly applications bucket timestamps correctly across weeks."""
        ref_date = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)  # Thursday of W40
        app_stats = get_weekly_application_stats(
            seeded_conn, weeks=4, weekly_target=10, reference_date=ref_date
        )

        assert app_stats.total_applied == 5
        assert app_stats.weekly_target == 10
        assert len(app_stats.weeks) == 4
        assert app_stats.avg_per_week == 1.2  # 5 / 4 = 1.25 -> 1.2

        # Check latest week has Job 8 and Job 9 (2 applications)
        latest_week = app_stats.weeks[-1]
        assert latest_week.count == 2
        assert latest_week.pct_of_target == 20.0

    def test_source_conversion_rates(self, seeded_conn: sqlite3.Connection) -> None:
        """Source stats compute applied, responses (including rejections), interviews, and response rates."""
        # Insert a job rejected before applying; should NOT be counted in applied or responses
        seeded_conn.execute(
            "INSERT INTO jobs (id, fingerprint, source, company, title, url, discovered_at, last_seen_at, status) "
            "VALUES (99, 'fp-99', 'remoteok', 'NoApplyCo', 'Dev', 'https://example.com', '2026-09-01', '2026-09-01', 'rejected')"
        )
        seeded_conn.commit()

        sources = get_source_stats(seeded_conn)
        src_map = {s.name: s for s in sources}

        # Greenhouse: Jobs 8 (applied), 9 (replied), 11 (offer) -> 3 applied, 2 responses (replied, offer), 1 interview/offer
        gh = src_map["greenhouse"]
        assert gh.applied == 3
        assert gh.responses == 2
        assert gh.interviews == 1
        assert gh.offers == 1
        assert gh.response_rate == 66.7

        # Lever: Job 10 (interview) -> 1 applied, 1 response, 1 interview
        lev = src_map["lever"]
        assert lev.applied == 1
        assert lev.responses == 1
        assert lev.interviews == 1
        assert lev.offers == 0
        assert lev.response_rate == 100.0

        # RemoteOK: Job 12 (applied then rejected) -> 1 applied, 1 response (rejection is a response!)
        # Job 99 was rejected WITHOUT applied_at, so remoteok applied is still 1!
        rok = src_map["remoteok"]
        assert rok.applied == 1
        assert rok.responses == 1
        assert rok.rejected == 1
        assert rok.response_rate == 100.0

    def test_tier_conversion_rates(self, seeded_conn: sqlite3.Connection) -> None:
        """Tier stats calculate conversions for Tier A, Tier B, and Unranked."""
        tiers = get_tier_stats(seeded_conn)
        tier_map = {t.name: t for t in tiers}

        # Tier A: Jobs 8, 9, 11 -> 3 applied, 2 responses (9, 11)
        tier_a = tier_map["Tier A"]
        assert tier_a.applied == 3
        assert tier_a.responses == 2
        assert tier_a.interviews == 1
        assert tier_a.offers == 1
        assert tier_a.response_rate == 66.7

        # Tier B: Job 10 -> 1 applied, 1 response (interview)
        tier_b = tier_map["Tier B"]
        assert tier_b.applied == 1
        assert tier_b.responses == 1
        assert tier_b.interviews == 1
        assert tier_b.response_rate == 100.0

        # Unranked: Job 12 -> 1 applied, 1 response (rejection)
        unranked = tier_map["Unranked"]
        assert unranked.applied == 1
        assert unranked.responses == 1
        assert unranked.rejected == 1
        assert unranked.response_rate == 100.0

    def test_full_report_text_formatting(self, seeded_conn: sqlite3.Connection) -> None:
        """Full stats report renders all sections into human-readable terminal text."""
        ref_date = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)
        report = get_stats_report(seeded_conn, weeks=4, weekly_target=10, reference_date=ref_date)
        text = report.format_text()

        assert "=== Pipeline Funnel Summary ===" in text
        assert "Total Discovered: 14" in text
        assert "=== Weekly Applications (Target: 10/wk) ===" in text
        assert "=== Performance by Source ===" in text
        assert "greenhouse" in text
        assert "=== Performance by Tier ===" in text
        assert "Tier A" in text
