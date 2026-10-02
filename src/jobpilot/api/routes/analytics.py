"""Analytics, stats, and profile report endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from jobpilot import services
from jobpilot.services import ProfileReport
from jobpilot.tracking.stats import FullStatsReport

router = APIRouter(prefix="/api", tags=["analytics"])


@router.get("/stats", response_model=FullStatsReport)
def get_stats(request: Request, weeks: int = Query(4, ge=1)) -> FullStatsReport:
    """Return application funnel and conversion statistics."""
    config = getattr(request.app.state, "config", None)
    return services.get_stats(weeks=weeks, config=config)


@router.get("/profile-report", response_model=ProfileReport)
def get_profile_report() -> ProfileReport:
    """Return validation report and gap analysis for profile.yaml and resume."""
    return services.profile_report()
