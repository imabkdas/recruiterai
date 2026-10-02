"""Daily pipeline execution for JobPilot.

Implements Sections 13 and 19 of JOBPILOT_DESIGN.md.
Executes the sequence:
  fetch -> ingest-alerts -> analyze -> score -> queue
Each stage's failure is logged and does not abort subsequent stages.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from jobpilot.config import AppConfig, load_config
from jobpilot.models import Profile
from jobpilot.pipeline.analyze import run_analysis
from jobpilot.pipeline.fetcher import run_fetch
from jobpilot.pipeline.prepare import run_prepare
from jobpilot.pipeline.queue import build_queue
from jobpilot.pipeline.score import run_scoring
from jobpilot.profile.loader import load_profile
from jobpilot.sources.gmail_alerts import ingest_gmail_alerts

logger = logging.getLogger(__name__)


@dataclass
class StageResult:
    """Outcome of a single daily pipeline stage."""

    name: str
    status: str  # "ok" | "error"
    summary: Any = None
    error: str | None = None


@dataclass
class DailyPipelineSummary:
    """Full execution summary of the daily pipeline."""

    stages: list[StageResult] = field(default_factory=list)

    @property
    def has_failures(self) -> bool:
        return any(s.status == "error" for s in self.stages)


def execute_daily_pipeline(
    conn: sqlite3.Connection,
    config: AppConfig | None = None,
    profile: Profile | None = None,
    prepare_top: int | None = None,
) -> DailyPipelineSummary:
    """Execute fetch -> ingest-alerts -> analyze -> score -> queue -> prepare with fault tolerance."""
    cfg = config or load_config()
    summary = DailyPipelineSummary()

    # Stage 1: fetch
    try:
        logger.info("Daily pipeline: running fetch...")
        res = run_fetch(conn)
        summary.stages.append(StageResult(name="fetch", status="ok", summary=res))
    except Exception as exc:
        logger.error("Daily pipeline stage 'fetch' failed: %s", exc, exc_info=True)
        summary.stages.append(StageResult(name="fetch", status="error", error=str(exc)))

    # Stage 2: ingest-alerts
    try:
        logger.info("Daily pipeline: running ingest-alerts...")
        res = ingest_gmail_alerts(conn)
        err = getattr(res, "error", None)
        if isinstance(err, str) and err.strip():
            logger.warning("Daily pipeline stage 'ingest-alerts' had error: %s", err)
            summary.stages.append(
                StageResult(name="ingest-alerts", status="error", error=err, summary=res)
            )
        else:
            summary.stages.append(StageResult(name="ingest-alerts", status="ok", summary=res))
    except Exception as exc:
        logger.error("Daily pipeline stage 'ingest-alerts' failed: %s", exc, exc_info=True)
        summary.stages.append(StageResult(name="ingest-alerts", status="error", error=str(exc)))

    # Stage 3: analyze
    try:
        logger.info("Daily pipeline: running analyze...")
        res = run_analysis(conn)
        summary.stages.append(StageResult(name="analyze", status="ok", summary=res))
    except Exception as exc:
        logger.error("Daily pipeline stage 'analyze' failed: %s", exc, exc_info=True)
        summary.stages.append(StageResult(name="analyze", status="error", error=str(exc)))

    # Stage 4: score
    try:
        logger.info("Daily pipeline: running score...")
        prof = profile or load_profile()
        res = run_scoring(conn, config=cfg, profile=prof)
        summary.stages.append(StageResult(name="score", status="ok", summary=res))
    except Exception as exc:
        logger.error("Daily pipeline stage 'score' failed: %s", exc, exc_info=True)
        summary.stages.append(StageResult(name="score", status="error", error=str(exc)))

    # Stage 5: queue
    try:
        logger.info("Daily pipeline: building queue...")
        res = build_queue(conn, config=cfg)
        summary.stages.append(StageResult(name="queue", status="ok", summary=res))
    except Exception as exc:
        logger.error("Daily pipeline stage 'queue' failed: %s", exc, exc_info=True)
        summary.stages.append(StageResult(name="queue", status="error", error=str(exc)))

    # Stage 6: prepare (if requested)
    if prepare_top is not None and prepare_top > 0:
        try:
            logger.info("Daily pipeline: preparing top %d jobs...", prepare_top)
            prof = profile or load_profile()
            res = run_prepare(conn, auto_top=prepare_top, config=cfg, profile=prof)
            summary.stages.append(StageResult(name="prepare", status="ok", summary=res))
        except Exception as exc:
            logger.error("Daily pipeline stage 'prepare' failed: %s", exc, exc_info=True)
            summary.stages.append(StageResult(name="prepare", status="error", error=str(exc)))

    return summary
