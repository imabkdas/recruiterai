"""Progress metrics endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Request

from jobpilot import services
from jobpilot.services import ProgressSummary

router = APIRouter(prefix="/api", tags=["progress"])


@router.get("/progress", response_model=ProgressSummary)
def get_progress(request: Request) -> ProgressSummary:
    """Return daily and weekly application progress metrics."""
    config = getattr(request.app.state, "config", None)
    return services.get_progress(config=config)
