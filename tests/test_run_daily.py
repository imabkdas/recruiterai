"""Tests for the daily pipeline execution (jobpilot.pipeline.daily)."""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from jobpilot.cli import app
from jobpilot.db import get_connection, init_schema
from jobpilot.pipeline.daily import execute_daily_pipeline

runner = CliRunner()


@pytest.fixture
def conn() -> sqlite3.Connection:
    """Initialized SQLite database connection."""
    c = get_connection(":memory:")
    init_schema(c)
    return c


class TestDailyPipeline:
    def test_all_stages_run_in_sequence_on_success(self, conn: sqlite3.Connection) -> None:
        """All 5 stages (fetch -> ingest-alerts -> analyze -> score -> queue) run in order."""
        call_order: list[str] = []

        def mock_fetch(c, **kwargs):
            call_order.append("fetch")
            return MagicMock(total_fetched=5)

        def mock_ingest(c):
            call_order.append("ingest-alerts")
            return MagicMock(total_listings=2, error=None)

        def mock_analyze(c, **kwargs):
            call_order.append("analyze")
            return MagicMock(analyzed=3)

        def mock_score(c, **kwargs):
            call_order.append("score")
            return MagicMock(total_scored=3)

        def mock_queue(c, **kwargs):
            call_order.append("queue")
            return MagicMock(items=[])

        with (
            patch("jobpilot.pipeline.daily.run_fetch", side_effect=mock_fetch),
            patch("jobpilot.pipeline.daily.ingest_gmail_alerts", side_effect=mock_ingest),
            patch("jobpilot.pipeline.daily.run_analysis", side_effect=mock_analyze),
            patch("jobpilot.pipeline.daily.run_scoring", side_effect=mock_score),
            patch("jobpilot.pipeline.daily.build_queue", side_effect=mock_queue),
        ):
            summary = execute_daily_pipeline(conn)

            assert call_order == ["fetch", "ingest-alerts", "analyze", "score", "queue"]
            assert len(summary.stages) == 5
            assert not summary.has_failures
            assert [s.status for s in summary.stages] == ["ok", "ok", "ok", "ok", "ok"]

    def test_stage_failure_does_not_abort_later_stages(self, conn: sqlite3.Connection) -> None:
        """When an earlier stage raises an exception, it is logged and subsequent stages still execute."""
        executed: list[str] = []

        def mock_fetch_fail(c, **kwargs):
            executed.append("fetch")
            raise ConnectionError("Remote source unreachable")

        def mock_ingest_fail(c):
            executed.append("ingest-alerts")
            raise ValueError("Invalid alert message format")

        def mock_analyze(c, **kwargs):
            executed.append("analyze")
            return MagicMock()

        def mock_score(c, **kwargs):
            executed.append("score")
            return MagicMock()

        def mock_queue(c, **kwargs):
            executed.append("queue")
            return MagicMock()

        with (
            patch("jobpilot.pipeline.daily.run_fetch", side_effect=mock_fetch_fail),
            patch("jobpilot.pipeline.daily.ingest_gmail_alerts", side_effect=mock_ingest_fail),
            patch("jobpilot.pipeline.daily.run_analysis", side_effect=mock_analyze),
            patch("jobpilot.pipeline.daily.run_scoring", side_effect=mock_score),
            patch("jobpilot.pipeline.daily.build_queue", side_effect=mock_queue),
        ):
            summary = execute_daily_pipeline(conn)

            # All 5 stages were attempted
            assert executed == ["fetch", "ingest-alerts", "analyze", "score", "queue"]
            assert summary.has_failures

            # Stages 1 and 2 failed, 3, 4, 5 succeeded
            st_map = {s.name: s for s in summary.stages}
            assert st_map["fetch"].status == "error"
            assert "Remote source unreachable" in (st_map["fetch"].error or "")
            assert st_map["ingest-alerts"].status == "error"
            assert "Invalid alert message format" in (st_map["ingest-alerts"].error or "")
            assert st_map["analyze"].status == "ok"
            assert st_map["score"].status == "ok"
            assert st_map["queue"].status == "ok"

    def test_daily_pipeline_with_prepare_stage(self, conn: sqlite3.Connection) -> None:
        """When prepare_top is specified, prepare stage runs as Stage 6."""
        call_order: list[str] = []

        with (
            patch("jobpilot.pipeline.daily.run_fetch", side_effect=lambda c: call_order.append("fetch")),
            patch("jobpilot.pipeline.daily.ingest_gmail_alerts", side_effect=lambda c: call_order.append("ingest-alerts")),
            patch("jobpilot.pipeline.daily.run_analysis", side_effect=lambda c: call_order.append("analyze")),
            patch("jobpilot.pipeline.daily.run_scoring", side_effect=lambda c, **kw: call_order.append("score")),
            patch("jobpilot.pipeline.daily.build_queue", side_effect=lambda c, **kw: call_order.append("queue")),
            patch("jobpilot.pipeline.daily.run_prepare", side_effect=lambda c, **kw: call_order.append("prepare")),
            patch("jobpilot.pipeline.daily.load_profile", return_value=MagicMock()),
        ):
            summary = execute_daily_pipeline(conn, prepare_top=3)

            assert call_order == ["fetch", "ingest-alerts", "analyze", "score", "queue", "prepare"]
            assert len(summary.stages) == 6
            assert summary.stages[-1].name == "prepare"
            assert summary.stages[-1].status == "ok"


class TestRunDailyCLI:
    def test_cli_run_daily_invokes_pipeline(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        """CLI jobpilot run-daily runs pipeline and outputs execution summary."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
        (tmp_path / ".env.example").write_text("GEMINI_API_KEY=\n")
        runner.invoke(app, ["init"])

        with patch("jobpilot.cli.execute_daily_pipeline") as mock_pipeline:
            from jobpilot.pipeline.daily import DailyPipelineSummary, StageResult
            mock_pipeline.return_value = DailyPipelineSummary(
                stages=[
                    StageResult(name="fetch", status="ok"),
                    StageResult(name="ingest-alerts", status="ok"),
                    StageResult(name="analyze", status="ok"),
                    StageResult(name="score", status="ok"),
                    StageResult(name="queue", status="ok"),
                ]
            )

            res = runner.invoke(app, ["run-daily"])
            assert res.exit_code == 0
            assert "Running JobPilot daily pipeline..." in res.output
            assert "Daily pipeline completed successfully!" in res.output
            assert "fetch" in res.output.lower()
            assert "queue" in res.output.lower()
            assert mock_pipeline.called

    def test_gmail_auth_failure_visible_in_daily_summary(self, conn: sqlite3.Connection) -> None:
        """When Gmail token expires or fails, ingest-alerts is marked as failed with auth details."""
        mock_summary = MagicMock()
        mock_summary.error = "Auth failed (token expired)"

        with (
            patch("jobpilot.pipeline.daily.run_fetch", return_value=MagicMock()),
            patch("jobpilot.pipeline.daily.ingest_gmail_alerts", return_value=mock_summary),
            patch("jobpilot.pipeline.daily.run_analysis", return_value=MagicMock()),
            patch("jobpilot.pipeline.daily.run_scoring", return_value=MagicMock()),
            patch("jobpilot.pipeline.daily.build_queue", return_value=MagicMock()),
        ):
            summary = execute_daily_pipeline(conn)

            assert summary.has_failures
            ingest_stage = next(s for s in summary.stages if s.name == "ingest-alerts")
            assert ingest_stage.status == "error"
            assert "Auth failed (token expired)" in (ingest_stage.error or "")
