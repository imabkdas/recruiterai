"""Adzuna API job source.

Fetches job listings from the Adzuna search API for supported countries (including India).
Enforces monthly API call budget tracked in the api_budget SQLite table.
Descriptions are kept as snippets (description stays None) so survivors get status needs_jd.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from jobpilot.config import AdzunaConfig, EnvSettings, load_config, load_env
from jobpilot.db import get_api_usage, increment_api_usage
from jobpilot.models import RawJob

logger = logging.getLogger(__name__)

SUPPORTED_COUNTRIES = {
    "at", "au", "be", "br", "ca", "ch", "de", "es", "fr", "gb",
    "in", "it", "mx", "nl", "nz", "pl", "ru", "sg", "us", "za",
}

API_BASE_URL = "https://api.adzuna.com/v1/api/jobs"


def _current_month_period() -> str:
    """Return period in YYYY-MM format."""
    return datetime.now(UTC).strftime("%Y-%m")


class AdzunaSource:
    """Source implementation for Adzuna job search API."""

    name: str = "adzuna"

    def __init__(
        self,
        config: AdzunaConfig | None = None,
        env: EnvSettings | None = None,
        conn: sqlite3.Connection | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.config = config or load_config().adzuna
        self.env = env or load_env()
        self.conn = conn
        self._client = client

    def fetch(self) -> list[RawJob]:
        """Fetch jobs from Adzuna for configured queries and pages.

        Stops cleanly if the monthly budget is exceeded or credentials are missing.
        """
        if not self.config.enabled:
            logger.info("Adzuna source is disabled in configuration.")
            return []

        app_id = self.env.adzuna_app_id.strip()
        app_key = self.env.adzuna_app_key.strip()
        if not app_id or not app_key:
            logger.warning("ADZUNA_APP_ID or ADZUNA_APP_KEY not set. Skipping Adzuna source.")
            return []

        country = self.config.country.lower().strip()
        if country not in SUPPORTED_COUNTRIES:
            logger.warning(
                "Adzuna does not support country '%s'. Supported countries: %s. Skipping.",
                country,
                ", ".join(sorted(SUPPORTED_COUNTRIES)),
            )
            return []

        period = _current_month_period()
        raw_jobs: list[RawJob] = []
        client = self._client or httpx.Client(timeout=15.0)

        try:
            for query in self.config.queries:
                for page in range(1, self.config.pages + 1):
                    # Check monthly budget
                    if self.conn:
                        current_usage = get_api_usage(self.conn, self.name, period)
                        if current_usage >= self.config.monthly_call_budget:
                            logger.warning(
                                "Adzuna monthly budget reached (%d/%d calls in %s). Stopping cleanly.",
                                current_usage,
                                self.config.monthly_call_budget,
                                period,
                            )
                            return raw_jobs

                    # Make request
                    url = f"{API_BASE_URL}/{country}/search/{page}"
                    params: dict[str, Any] = {
                        "app_id": app_id,
                        "app_key": app_key,
                        "what": query,
                        "max_days_old": self.config.max_days_old,
                        "results_per_page": 20,
                        "content-type": "application/json",
                    }

                    try:
                        resp = client.get(url, params=params)
                        if self.conn:
                            increment_api_usage(self.conn, self.name, period, 1)

                        if resp.status_code == 429:
                            logger.warning("Adzuna rate limit hit (429). Stopping query.")
                            break
                        if resp.status_code != 200:
                            logger.warning(
                                "Adzuna API error for query '%s' page %d: HTTP %d: %s",
                                query,
                                page,
                                resp.status_code,
                                resp.text[:200],
                            )
                            continue

                        data = resp.json()
                        results = data.get("results", [])
                        if not results:
                            break

                        for item in results:
                            raw_job = self._parse_adzuna_item(item)
                            if raw_job:
                                raw_jobs.append(raw_job)

                    except httpx.RequestError as exc:
                        logger.warning("Adzuna request error for query '%s' page %d: %s", query, page, exc)
                        continue

                    # Polite rate limit between pages
                    time.sleep(0.1)

        finally:
            if not self._client:
                client.close()

        logger.info("Adzuna fetched %d raw jobs.", len(raw_jobs))
        return raw_jobs

    def _parse_adzuna_item(self, item: dict[str, Any]) -> RawJob | None:
        """Convert a single Adzuna result item into a RawJob."""
        job_id = str(item.get("id", ""))
        title = item.get("title", "").strip()
        if not title:
            return None

        company_dict = item.get("company") or {}
        company = company_dict.get("display_name", "").strip() or "Unknown"

        location_dict = item.get("location") or {}
        location = location_dict.get("display_name", "").strip() or None

        redirect_url = item.get("redirect_url", "").strip()
        posted_at = item.get("created")

        snippet = item.get("description", "").strip() or None

        return RawJob(
            source=self.name,
            source_job_id=job_id or None,
            company=company,
            title=title,
            location=location,
            url=redirect_url,
            apply_url=redirect_url,
            description=None,  # Intentionally null so survivors get needs_jd
            snippet=snippet,
            posted_at=posted_at,
            raw_json=item,
        )
