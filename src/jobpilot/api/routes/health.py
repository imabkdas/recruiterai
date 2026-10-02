"""Health check endpoint."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api", tags=["health"])


class HealthResponse(BaseModel):
    status: str = "ok"


@router.get("/health", response_model=HealthResponse)
def get_health() -> HealthResponse:
    """Return health status of the API server."""
    return HealthResponse(status="ok")
