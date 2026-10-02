"""Daily pipeline background run endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request

from jobpilot.api.run_state import RunStatusResponse

router = APIRouter(prefix="/api", tags=["run"])


@router.post("/run", response_model=RunStatusResponse)
def trigger_run(request: Request) -> RunStatusResponse:
    """Trigger the daily pipeline in a background thread."""
    manager = request.app.state.run_state
    config = getattr(request.app.state, "config", None)
    return manager.start_run(config=config)


@router.get("/run/status", response_model=RunStatusResponse)
def get_run_status(request: Request) -> RunStatusResponse:
    """Return status of the active or most recent pipeline run."""
    manager = request.app.state.run_state
    return manager.get_status()
