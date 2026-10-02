"""Application funnel, weekly metrics, and response statistics for JobPilot.

Implements Section 13 and 20 of JOBPILOT_DESIGN.md.
Tracks pipeline funnel counts, weekly application volume against weekly target,
and response/interview conversion rates broken down by source and by tier.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass
class FunnelStats:
    """Funnel counts across the full job lifecycle."""

    total_discovered: int
    filtered: int
    needs_jd: int
    analyzed: int
    scored: int
    queued: int
    prepared: int
    applied: int
    replied: int
    interview: int
    offer: int
    rejected: int
    skipped: int
    expired: int
    status_counts: dict[str, int]
    total_applied_all_time: int = 0


@dataclass
class WeeklyAppStats:
    """Application volume for a single calendar week."""

    iso_week: str
    week_start: str
    week_end: str
    count: int
    target: int
    pct_of_target: float


@dataclass
class ApplicationStats:
    """Weekly application performance over the evaluation window."""

    weeks: list[WeeklyAppStats]
    total_applied: int
    weekly_target: int
    avg_per_week: float


@dataclass
class ConversionStats:
    """Response and interview metrics for a category (source or tier)."""

    name: str
    applied: int
    responses: int
    interviews: int
    offers: int
    response_rate: float
    rejected: int = 0


@dataclass
class FullStatsReport:
    """Combined report returned by get_stats_report."""

    funnel: FunnelStats
    applications: ApplicationStats
    sources: list[ConversionStats]
    tiers: list[ConversionStats]

    def format_text(self) -> str:
        """Render report for terminal display."""
        lines: list[str] = []

        # 1. Funnel
        lines.append("=== Pipeline Funnel Summary ===")
        lines.append(f"Total Discovered: {self.funnel.total_discovered}")
        lines.append(f"  Pre-filtered:   {self.funnel.filtered}")
        lines.append(f"  Needs JD:       {self.funnel.needs_jd}")
        lines.append(f"  Analyzed:       {self.funnel.analyzed}")
        lines.append(f"  Scored:         {self.funnel.scored}")
        lines.append(f"  Queued:         {self.funnel.queued}")
        lines.append(f"  Prepared:       {self.funnel.prepared}")
        lines.append(f"  Applied:        {self.funnel.applied}")
        lines.append(f"  Replied:        {self.funnel.replied}")
        lines.append(f"  Interview:      {self.funnel.interview}")
        lines.append(f"  Offer:          {self.funnel.offer}")
        lines.append(f"  Rejected:       {self.funnel.rejected}")
        lines.append(f"  Skipped:        {self.funnel.skipped}")
        lines.append(f"  Expired:        {self.funnel.expired}")
        if self.funnel.total_applied_all_time:
            lines.append(f"Total Applications (all-time): {self.funnel.total_applied_all_time}")

        # 2. Weekly Applications
        lines.append(f"\n=== Weekly Applications (Target: {self.applications.weekly_target}/wk) ===")
        if not self.applications.weeks:
            lines.append("No weekly application history found.")
        else:
            for w in self.applications.weeks:
                bar_len = min(20, int(w.pct_of_target / 5))
                bar = "█" * bar_len + "░" * (20 - bar_len)
                lines.append(
                    f"Week {w.iso_week} ({w.week_start} to {w.week_end}): "
                    f"{w.count:>2} / {w.target} [{bar}] {w.pct_of_target:>5.1f}%"
                )
            lines.append(f"Average / week: {self.applications.avg_per_week:.1f} applications")

        # 3. Performance by Source
        lines.append("\n=== Performance by Source ===")
        if not self.sources:
            lines.append("No applications recorded yet.")
        else:
            header = f"{'Source':<24} {'Applied':<8} {'Responses':<10} {'Interviews':<11} {'Offers':<7} {'Resp Rate'}"
            lines.append(header)
            lines.append("-" * len(header))
            for s in self.sources:
                lines.append(
                    f"{s.name:<24} {s.applied:<8} {s.responses:<10} {s.interviews:<11} {s.offers:<7} {s.response_rate:>5.1f}%"
                )

        # 4. Performance by Tier
        lines.append("\n=== Performance by Tier ===")
        if not self.tiers:
            lines.append("No applications recorded yet.")
        else:
            header = f"{'Tier':<12} {'Applied':<8} {'Responses':<10} {'Interviews':<11} {'Offers':<7} {'Resp Rate'}"
            lines.append(header)
            lines.append("-" * len(header))
            for t in self.tiers:
                lines.append(
                    f"{t.name:<12} {t.applied:<8} {t.responses:<10} {t.interviews:<11} {t.offers:<7} {t.response_rate:>5.1f}%"
                )

        return "\n".join(lines)


def get_funnel_stats(conn: sqlite3.Connection) -> FunnelStats:
    """Calculate funnel counts across all jobs."""
    rows = conn.execute("SELECT status, COUNT(*) as cnt FROM jobs GROUP BY status").fetchall()
    status_counts = {r["status"]: r["cnt"] for r in rows}

    filtered = status_counts.get("filtered_out", 0) + status_counts.get("filtered", 0)
    total_discovered = sum(status_counts.values())

    app_row = conn.execute(
        "SELECT COUNT(*) as cnt FROM applications WHERE applied_at IS NOT NULL"
    ).fetchone()
    total_applied_all_time = app_row["cnt"] if app_row else 0

    return FunnelStats(
        total_discovered=total_discovered,
        filtered=filtered,
        needs_jd=status_counts.get("needs_jd", 0),
        analyzed=status_counts.get("analyzed", 0),
        scored=status_counts.get("scored", 0),
        queued=status_counts.get("queued", 0),
        prepared=status_counts.get("prepared", 0),
        applied=status_counts.get("applied", 0),
        replied=status_counts.get("replied", 0),
        interview=status_counts.get("interview", 0),
        offer=status_counts.get("offer", 0),
        rejected=status_counts.get("rejected", 0),
        skipped=status_counts.get("skipped", 0),
        expired=status_counts.get("expired", 0),
        status_counts=status_counts,
        total_applied_all_time=total_applied_all_time,
    )


def get_weekly_application_stats(
    conn: sqlite3.Connection,
    weeks: int = 4,
    weekly_target: int = 25,
    reference_date: datetime | None = None,
) -> ApplicationStats:
    """Calculate weekly application counts against weekly_target for the last N weeks."""
    ref = reference_date or datetime.now(UTC)

    # Align to current Monday
    current_monday = (ref - timedelta(days=ref.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    week_ranges: list[tuple[datetime, datetime, str, str]] = []
    # Build list of weeks from oldest to newest
    for i in range(weeks - 1, -1, -1):
        w_start = current_monday - timedelta(weeks=i)
        w_end = w_start + timedelta(days=6, hours=23, minutes=59, seconds=59)
        iso_year, iso_week_num, _ = w_start.isocalendar()
        iso_label = f"{iso_year}-W{iso_week_num:02d}"
        week_ranges.append((w_start, w_end, iso_label, w_start.strftime("%Y-%m-%d")))

    # Retrieve applied timestamps
    query = """
        SELECT a.applied_at
        FROM applications a
        JOIN jobs j ON a.job_id = j.id
        WHERE a.applied_at IS NOT NULL
           OR j.status IN ('applied', 'replied', 'interview', 'offer', 'rejected')
    """
    rows = conn.execute(query).fetchall()

    applied_dates: list[datetime] = []
    for r in rows:
        ts = r["applied_at"]
        if ts:
            try:
                # Handle standard ISO strings
                cleaned = ts.replace("Z", "+00:00")
                dt = datetime.fromisoformat(cleaned)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                applied_dates.append(dt)
            except Exception:
                continue

    weekly_items: list[WeeklyAppStats] = []
    total_in_period = 0

    for w_start, w_end, iso_label, start_str in week_ranges:
        count = sum(1 for dt in applied_dates if w_start <= dt <= w_end)
        pct = round(count / weekly_target * 100, 1) if weekly_target > 0 else 0.0
        weekly_items.append(
            WeeklyAppStats(
                iso_week=iso_label,
                week_start=start_str,
                week_end=(w_start + timedelta(days=6)).strftime("%Y-%m-%d"),
                count=count,
                target=weekly_target,
                pct_of_target=pct,
            )
        )
        total_in_period += count

    avg = round(total_in_period / weeks, 1) if weeks > 0 else 0.0

    return ApplicationStats(
        weeks=weekly_items,
        total_applied=total_in_period,
        weekly_target=weekly_target,
        avg_per_week=avg,
    )


def get_source_stats(conn: sqlite3.Connection) -> list[ConversionStats]:
    """Calculate conversion rates (responses, interviews, offers) grouped by job source."""
    query = """
        SELECT j.source, j.status, COUNT(*) as cnt
        FROM jobs j
        LEFT JOIN applications a ON j.id = a.job_id
        WHERE a.applied_at IS NOT NULL
           OR j.status IN ('applied', 'replied', 'interview', 'offer')
        GROUP BY j.source, j.status
    """
    rows = conn.execute(query).fetchall()

    grouped: dict[str, dict[str, int]] = {}
    for r in rows:
        src = r["source"] or "unknown"
        grouped.setdefault(src, {})[r["status"]] = r["cnt"]

    results: list[ConversionStats] = []
    for src, st_map in grouped.items():
        applied = sum(st_map.values())
        responses = (
            st_map.get("replied", 0)
            + st_map.get("interview", 0)
            + st_map.get("offer", 0)
            + st_map.get("rejected", 0)
        )
        interviews = st_map.get("interview", 0) + st_map.get("offer", 0)
        offers = st_map.get("offer", 0)
        rejected = st_map.get("rejected", 0)
        rate = round(responses / applied * 100.0, 1) if applied > 0 else 0.0

        results.append(
            ConversionStats(
                name=src,
                applied=applied,
                responses=responses,
                interviews=interviews,
                offers=offers,
                rejected=rejected,
                response_rate=rate,
            )
        )

    # Sort by applied desc, then responses desc
    results.sort(key=lambda s: (-s.applied, -s.responses, s.name))
    return results


def get_tier_stats(conn: sqlite3.Connection) -> list[ConversionStats]:
    """Calculate conversion rates grouped by tier (Tier A, Tier B, Unranked)."""
    query = """
        SELECT COALESCE(s.tier, 'Unranked') as tier, j.status, COUNT(*) as cnt
        FROM jobs j
        LEFT JOIN applications a ON j.id = a.job_id
        LEFT JOIN scores s ON j.id = s.job_id
        WHERE a.applied_at IS NOT NULL
           OR j.status IN ('applied', 'replied', 'interview', 'offer')
        GROUP BY s.tier, j.status
    """
    rows = conn.execute(query).fetchall()

    grouped: dict[str, dict[str, int]] = {}
    for r in rows:
        raw_tier = r["tier"]
        tier_label = f"Tier {raw_tier}" if raw_tier in ("A", "B") else "Unranked"
        grouped.setdefault(tier_label, {})[r["status"]] = (
            grouped.get(tier_label, {}).get(r["status"], 0) + r["cnt"]
        )

    results: list[ConversionStats] = []
    tier_order = {"Tier A": 1, "Tier B": 2, "Unranked": 3}

    for tier_label, st_map in grouped.items():
        applied = sum(st_map.values())
        responses = (
            st_map.get("replied", 0)
            + st_map.get("interview", 0)
            + st_map.get("offer", 0)
            + st_map.get("rejected", 0)
        )
        interviews = st_map.get("interview", 0) + st_map.get("offer", 0)
        offers = st_map.get("offer", 0)
        rejected = st_map.get("rejected", 0)
        rate = round(responses / applied * 100.0, 1) if applied > 0 else 0.0

        results.append(
            ConversionStats(
                name=tier_label,
                applied=applied,
                responses=responses,
                interviews=interviews,
                offers=offers,
                rejected=rejected,
                response_rate=rate,
            )
        )

    results.sort(key=lambda t: (tier_order.get(t.name, 99), -t.applied))
    return results


def get_stats_report(
    conn: sqlite3.Connection,
    weeks: int = 4,
    weekly_target: int = 25,
    reference_date: datetime | None = None,
) -> FullStatsReport:
    """Aggregate all metrics into a complete stats report."""
    return FullStatsReport(
        funnel=get_funnel_stats(conn),
        applications=get_weekly_application_stats(
            conn, weeks=weeks, weekly_target=weekly_target, reference_date=reference_date
        ),
        sources=get_source_stats(conn),
        tiers=get_tier_stats(conn),
    )
