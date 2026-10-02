"""Configuration loader for JobPilot.

Loads config.yaml and .env, exposing a typed settings object.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Typed config models
# ---------------------------------------------------------------------------

class LLMConfig(BaseModel):
    provider: str = "gemini"
    model_extract: str = ""
    model_write: str = ""
    daily_call_cap: int = 150
    max_retries: int = 3
    max_description_chars: int = 6000
    max_backoff_seconds: float = 60.0
    min_request_interval_seconds: float = 0.0


class ThresholdsConfig(BaseModel):
    tier_a: int = 80
    tier_b: int = 65
    max_job_age_days: int = 21


class ScoringConfig(BaseModel):
    evidence_weights: dict[str, float] = Field(default_factory=lambda: {
        "VERIFIED_PROFESSIONAL": 1.0,
        "VERIFIED_PROJECT": 0.6,
        "VERIFIED_CERTIFICATION": 0.5,
        "EXPERIMENTAL": 0.35,
        "LEARNING": 0.2,
        "UNKNOWN": 0.0,
    })


class ClaimsConfig(BaseModel):
    project_qualifiers: list[str] = Field(default_factory=list)


class QueueConfig(BaseModel):
    daily_size: int = 6
    weekly_target: int = 25


class PrepareConfig(BaseModel):
    """How much of the LLM budget to spend on tailoring resumes."""

    # Top-ranked jobs whose summary is LLM-tailored. Everything below this gets a
    # resume assembled entirely from resume_base.yaml with no API call.
    llm_tailor_top_n: int = 3
    write_pdf: bool = True


class GmailSourceConfig(BaseModel):
    enabled: bool = True
    label: str = "job-alerts"
    senders: list[str] = Field(default_factory=list)


class SourcesConfig(BaseModel):
    greenhouse_boards: list[str] = Field(default_factory=list)
    lever_companies: list[str] = Field(default_factory=list)
    remoteok: bool = True
    remotive: bool = True
    himalayas: bool = True
    hn_hiring: bool = True
    gmail: GmailSourceConfig = Field(default_factory=GmailSourceConfig)


class AdzunaConfig(BaseModel):
    enabled: bool = True
    country: str = "in"
    queries: list[str] = Field(default_factory=lambda: [
        "Software Engineer",
        "Java Backend Engineer",
        "Spring Boot Developer",
    ])
    pages: int = 2
    max_days_old: int = 21
    monthly_call_budget: int = 900


class BoardsConfig(BaseModel):
    max_per_run: int = 20
    greenhouse_boards: list[str] = Field(default_factory=list)
    lever_companies: list[str] = Field(default_factory=list)
    ashby_boards: list[str] = Field(default_factory=list)


class NotificationsConfig(BaseModel):
    desktop: bool = False
    telegram: bool = False
    follow_up_days: int = 7


class UIConfig(BaseModel):
    port: int = 8765


class AppConfig(BaseModel):
    """Top-level application configuration."""

    llm: LLMConfig = Field(default_factory=LLMConfig)
    thresholds: ThresholdsConfig = Field(default_factory=ThresholdsConfig)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    claims: ClaimsConfig = Field(default_factory=ClaimsConfig)
    queue: QueueConfig = Field(default_factory=QueueConfig)
    prepare: PrepareConfig = Field(default_factory=PrepareConfig)
    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    adzuna: AdzunaConfig = Field(default_factory=AdzunaConfig)
    boards: BoardsConfig = Field(default_factory=BoardsConfig)
    stack_bonus: list[str] = Field(default_factory=list)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
    ui: UIConfig = Field(default_factory=UIConfig)


# ---------------------------------------------------------------------------
# Env settings (secrets)
# ---------------------------------------------------------------------------

class EnvSettings(BaseModel):
    gemini_api_key: str = ""
    google_oauth_client_file: str = "credentials.json"
    adzuna_app_id: str = ""
    adzuna_app_key: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def _find_project_root() -> Path:
    """Walk up from CWD to find the directory containing pyproject.toml."""
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        if (parent / "pyproject.toml").exists():
            return parent
    return cwd


def load_config(config_path: str | Path | None = None) -> AppConfig:
    """Load config.yaml and return a typed AppConfig."""
    if config_path is None:
        config_path = _find_project_root() / "config.yaml"
    config_path = Path(config_path)

    if not config_path.exists():
        return AppConfig()

    with open(config_path) as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}

    return AppConfig.model_validate(raw)


def load_env(env_path: str | Path | None = None) -> EnvSettings:
    """Load .env file and return typed env settings."""
    if env_path is None:
        env_path = _find_project_root() / ".env"
    env_path = Path(env_path)

    values: dict[str, str | None] = {}
    if env_path.exists():
        values = dotenv_values(str(env_path))

    return EnvSettings(
        gemini_api_key=values.get("GEMINI_API_KEY", "") or "",
        google_oauth_client_file=values.get("GOOGLE_OAUTH_CLIENT_FILE", "credentials.json") or "credentials.json",
        adzuna_app_id=values.get("ADZUNA_APP_ID", "") or "",
        adzuna_app_key=values.get("ADZUNA_APP_KEY", "") or "",
        telegram_bot_token=values.get("TELEGRAM_BOT_TOKEN", "") or "",
        telegram_chat_id=values.get("TELEGRAM_CHAT_ID", "") or "",
    )
