"""JobSource protocol definition."""

from __future__ import annotations

from typing import Protocol

from jobpilot.models import RawJob


class JobSource(Protocol):
    """Protocol that all job listing sources implement."""

    name: str

    def fetch(self) -> list[RawJob]:
        """Fetch raw job listings from the source."""
        ...
