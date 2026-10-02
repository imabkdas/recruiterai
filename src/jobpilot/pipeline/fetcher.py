"""Fetch orchestrator: queries sources, ingests listings, runs prefilter, and outputs funnel summary."""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from jobpilot.config import AppConfig, load_config
from jobpilot.db import (
    get_active_boards,
    mark_board_inactive,
    record_source_run,
    update_board_checked,
    upsert_board,
)
from jobpilot.models import RawJob
from jobpilot.pipeline.ingest import ingest_raw_jobs
from jobpilot.pipeline.prefilter import run_prefilter
from jobpilot.sources.adzuna import AdzunaSource
from jobpilot.sources.ashby import fetch_ashby_board
from jobpilot.sources.greenhouse import BoardNotFoundError, fetch_greenhouse_board
from jobpilot.sources.himalayas import fetch_himalayas
from jobpilot.sources.hn_hiring import fetch_hn_hiring
from jobpilot.sources.lever import fetch_lever_board
from jobpilot.sources.remoteok import fetch_remoteok
from jobpilot.sources.remotive import fetch_remotive

logger = logging.getLogger(__name__)


@dataclass
class SourceFetchStat:
    """One feed or board inside a fetch run."""

    name: str
    status: str  # ok | failed
    found: int = 0
    new: int = 0
    updated: int = 0
    error: str | None = None


@dataclass
class FetchSummary:
    """Summary of a fetch run."""

    sources_run: list[str] = field(default_factory=list)
    sources: list[SourceFetchStat] = field(default_factory=list)
    total_fetched: int = 0
    new_jobs: int = 0
    duplicates: int = 0
    updated: int = 0
    filtered_out: int = 0
    reasons: dict[str, int] = field(default_factory=dict)
    survived: int = 0
    needs_jd: int = 0
    deactivated_boards: list[tuple[str, str]] = field(default_factory=list)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _run_source(
    conn: sqlite3.Connection,
    summary: FetchSummary,
    name: str,
    fetch_fn: Callable[[], list[RawJob]],
) -> list[int]:
    """Fetch and ingest one source. A failure is recorded and does not propagate."""
    started = _now()
    summary.sources_run.append(name)
    try:
        jobs = fetch_fn()
    except Exception as exc:
        logger.warning("Source %s failed: %s", name, exc)
        summary.sources.append(SourceFetchStat(name=name, status="failed", error=str(exc)))
        record_source_run(
            conn,
            name,
            started_at=started,
            completed_at=_now(),
            status="failed",
            error=str(exc),
        )
        return []

    ingest_res = ingest_raw_jobs(conn, jobs)
    summary.total_fetched += len(jobs)
    summary.new_jobs += ingest_res.new
    summary.duplicates += ingest_res.duplicates
    summary.updated += ingest_res.updated
    summary.sources.append(
        SourceFetchStat(
            name=name,
            status="ok",
            found=len(jobs),
            new=ingest_res.new,
            updated=ingest_res.updated,
        )
    )
    record_source_run(
        conn,
        name,
        started_at=started,
        completed_at=_now(),
        status="ok",
        found_count=len(jobs),
        new_count=ingest_res.new,
        updated_count=ingest_res.updated,
    )
    return ingest_res.job_ids


def run_fetch(
    conn: sqlite3.Connection,
    source: str | None = None,
    config: AppConfig | None = None,
) -> FetchSummary:
    """Run fetch for configured sources or a specific source."""
    if config is None:
        config = load_config()

    summary = FetchSummary()
    touched_ids: list[int] = []
    src_filter = source.lower().strip() if source else None

    if (src_filter is None or src_filter == "adzuna") and config.adzuna.enabled:
        adzuna_src = AdzunaSource(config=config.adzuna, conn=conn)
        touched_ids.extend(_run_source(conn, summary, "adzuna", adzuna_src.fetch))

    board_sources = {"greenhouse", "lever", "ashby"}
    if src_filter is None or src_filter in board_sources or src_filter == "boards":
        for token in config.boards.greenhouse_boards:
            upsert_board(conn, "greenhouse", token)
        for token in config.boards.lever_companies:
            upsert_board(conn, "lever", token)
        for token in config.boards.ashby_boards:
            upsert_board(conn, "ashby", token)

        ats_filter = src_filter if src_filter in board_sources else None
        active_boards = get_active_boards(conn, ats=ats_filter, limit=config.boards.max_per_run)

        for b in active_boards:
            ats = b["ats"]
            token = b["token"]
            name = f"{ats}:{token}"
            started = _now()
            summary.sources_run.append(name)

            try:
                if ats == "greenhouse":
                    board_jobs = fetch_greenhouse_board(token)
                elif ats == "lever":
                    board_jobs = fetch_lever_board(token)
                elif ats == "ashby":
                    board_jobs = fetch_ashby_board(token)
                else:
                    board_jobs = []
                update_board_checked(conn, ats, token, hits=len(board_jobs))
                ingest_res = ingest_raw_jobs(conn, board_jobs)
                touched_ids.extend(ingest_res.job_ids)
                summary.total_fetched += len(board_jobs)
                summary.new_jobs += ingest_res.new
                summary.duplicates += ingest_res.duplicates
                summary.updated += ingest_res.updated
                summary.sources.append(
                    SourceFetchStat(
                        name=name,
                        status="ok",
                        found=len(board_jobs),
                        new=ingest_res.new,
                        updated=ingest_res.updated,
                    )
                )
                record_source_run(
                    conn,
                    name,
                    started_at=started,
                    completed_at=_now(),
                    status="ok",
                    found_count=len(board_jobs),
                    new_count=ingest_res.new,
                    updated_count=ingest_res.updated,
                )
            except BoardNotFoundError:
                logger.warning("Board %s/%s returned 404. Marking inactive.", ats, token)
                mark_board_inactive(conn, ats, token)
                summary.deactivated_boards.append((ats, token))
                summary.sources.append(
                    SourceFetchStat(name=name, status="failed", error="board not found")
                )
                record_source_run(
                    conn,
                    name,
                    started_at=started,
                    completed_at=_now(),
                    status="failed",
                    error="board not found",
                )
            except Exception as exc:
                logger.warning("Error fetching board %s/%s: %s", ats, token, exc)
                summary.sources.append(SourceFetchStat(name=name, status="failed", error=str(exc)))
                record_source_run(
                    conn,
                    name,
                    started_at=started,
                    completed_at=_now(),
                    status="failed",
                    error=str(exc),
                )

            time.sleep(0.05)

    if (src_filter is None or src_filter == "remoteok") and config.sources.remoteok:
        touched_ids.extend(_run_source(conn, summary, "remoteok", fetch_remoteok))

    if (src_filter is None or src_filter == "remotive") and config.sources.remotive:
        touched_ids.extend(_run_source(conn, summary, "remotive", fetch_remotive))

    if (src_filter is None or src_filter in ("hn_hiring", "hn", "hackernews")) and config.sources.hn_hiring:
        touched_ids.extend(_run_source(conn, summary, "hn_hiring", fetch_hn_hiring))

    if (src_filter is None or src_filter == "himalayas") and config.sources.himalayas:
        touched_ids.extend(_run_source(conn, summary, "himalayas", fetch_himalayas))

    if touched_ids:
        prefilter_res = run_prefilter(conn, job_ids=touched_ids)
        summary.filtered_out = prefilter_res.filtered_out
        summary.reasons = prefilter_res.reasons
        summary.survived = prefilter_res.passed
        summary.needs_jd = prefilter_res.needs_jd

    return summary
