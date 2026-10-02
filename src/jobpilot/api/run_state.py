"""Thread-safe state manager for pipeline background runs."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from jobpilot import services
from jobpilot.config import AppConfig
from jobpilot.services import RunSummary, _safe_serialize_summary

logger = logging.getLogger(__name__)


class RunAlreadyInProgressError(Exception):
    """Raised when a pipeline run is triggered while another is still active."""


RunConflict = RunAlreadyInProgressError


class RunStageItem(BaseModel):
    name: str
    status: str = "pending"  # pending | running | ok | error
    counts: Any = None
    error: str | None = None

    @field_validator("counts", mode="before")
    @classmethod
    def _val_counts(cls, v: Any) -> Any:
        return _safe_serialize_summary(v)



class RunStatusResponse(BaseModel):
    state: str = "idle"  # idle | running | finished | failed
    stages: list[RunStageItem] = Field(default_factory=list)
    started_at: str | None = None
    finished_at: str | None = None
    summary: RunSummary | None = None
    error: str | None = None


class RunStateManager:
    """Manages execution and status for background daily pipeline runs."""

    def __init__(self, run_fn: Callable[..., RunSummary] | None = None) -> None:
        self._lock = threading.Lock()
        self.state: str = "idle"
        self.stages: list[RunStageItem] = []
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.summary: RunSummary | None = None
        self.error: str | None = None
        self._run_fn = run_fn or services.run_daily

    def get_status(self) -> RunStatusResponse:
        """Return the current thread-safe snapshot of run state."""
        with self._lock:
            return RunStatusResponse(
                state=self.state,
                stages=list(self.stages),
                started_at=self.started_at,
                finished_at=self.finished_at,
                summary=self.summary,
                error=self.error,
            )

    def start_run(self, config: AppConfig | None = None) -> RunStatusResponse:
        """Start a daily pipeline run in a background thread if not already running."""
        with self._lock:
            if self.state == "running":
                raise RunConflict("A pipeline run is already in progress.")
            self.state = "running"
            self.started_at = datetime.now(UTC).isoformat()
            self.finished_at = None
            self.summary = None
            self.error = None
            self.stages = [
                RunStageItem(name="fetch", status="pending"),
                RunStageItem(name="ingest-alerts", status="pending"),
                RunStageItem(name="analyze", status="pending"),
                RunStageItem(name="score", status="pending"),
                RunStageItem(name="queue", status="pending"),
                RunStageItem(name="prepare", status="pending"),
            ]
            snapshot = RunStatusResponse(
                state=self.state,
                stages=list(self.stages),
                started_at=self.started_at,
            )

        thread = threading.Thread(target=self._run_worker, args=(config,), daemon=True)
        thread.start()
        return snapshot

    def _run_worker(self, config: AppConfig | None = None) -> None:
        def callback(event: str, message: str) -> None:
            with self._lock:
                for st in self.stages:
                    if st.name in event:
                        st.status = "running"

        try:
            summary = self._run_fn(
                progress_callback=callback,
                config=config,
                notify=True,
            )
            with self._lock:
                self.summary = summary
                self.stages = [
                    RunStageItem(
                        name=s.name,
                        status=s.status,
                        counts=s.summary,
                        error=s.error,
                    )
                    for s in summary.stages
                ]
                self.state = "failed" if summary.has_failures else "finished"
                self.finished_at = datetime.now(UTC).isoformat()
        except Exception as exc:
            logger.exception("Error executing daily pipeline run in background thread")
            with self._lock:
                self.state = "failed"
                self.error = str(exc)
                self.finished_at = datetime.now(UTC).isoformat()
