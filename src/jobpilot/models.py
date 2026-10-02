"""Pydantic models for JobPilot.

Covers raw/processed jobs, LLM analysis output, profile/resume schemas,
and scoring results. All models use strict validation.
"""

from __future__ import annotations

import enum
from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Evidence levels
# ---------------------------------------------------------------------------

class EvidenceLevel(enum.StrEnum):
    """How strongly a skill claim is supported."""

    VERIFIED_PROFESSIONAL = "VERIFIED_PROFESSIONAL"
    VERIFIED_PROJECT = "VERIFIED_PROJECT"
    VERIFIED_CERTIFICATION = "VERIFIED_CERTIFICATION"
    EXPERIMENTAL = "EXPERIMENTAL"
    LEARNING = "LEARNING"
    UNKNOWN = "UNKNOWN"


# ---------------------------------------------------------------------------
# Job models
# ---------------------------------------------------------------------------

class RawJob(BaseModel):
    """A job listing as fetched from a source, before normalisation."""

    source: str
    source_job_id: str | None = None
    company: str
    title: str
    location: str | None = None
    remote_type: str | None = None
    url: str
    apply_url: str | None = None
    description: str | None = None
    snippet: str | None = None
    posted_at: str | None = None
    raw_json: dict[str, Any] | None = None


class Job(BaseModel):
    """A normalised, stored job listing."""

    id: int | None = None
    fingerprint: str
    source: str
    source_job_id: str | None = None
    company: str
    title: str
    location: str | None = None
    remote_type: str | None = None
    url: str
    apply_url: str | None = None
    description: str | None = None
    snippet: str | None = None
    posted_at: str | None = None
    discovered_at: str = ""
    last_seen_at: str = ""
    status: str = "new"
    status_reason: str | None = None


# ---------------------------------------------------------------------------
# LLM extraction (Section 10.1)
# ---------------------------------------------------------------------------

class RemoteEligibility(BaseModel):
    open_to_india: str = "unclear"  # yes | no | unclear
    restriction_text: str | None = None


class Salary(BaseModel):
    min: float | None = None
    max: float | None = None
    currency: str | None = None
    period: str | None = None


class JobAnalysis(BaseModel):
    """Structured data extracted from a job description by the LLM."""

    normalized_title: str
    seniority: str = "unknown"  # junior | mid | senior | lead | unknown
    experience_min_years: float | None = None
    experience_max_years: float | None = None
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    responsibilities_summary: str = ""
    location_type: str = "unknown"  # onsite | hybrid | remote | unknown
    locations: list[str] = Field(default_factory=list)
    remote_eligibility: RemoteEligibility = Field(default_factory=RemoteEligibility)
    visa_or_work_auth_required: bool = False
    salary: Salary = Field(default_factory=Salary)
    red_flags: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Profile models (Section 7)
# ---------------------------------------------------------------------------

class Skill(BaseModel):
    name: str
    category: str
    level: EvidenceLevel
    years: float | None = None
    evidence: list[str] = Field(default_factory=list)
    notes: str | None = None


class Identity(BaseModel):
    name: str
    contact: dict[str, str] = Field(default_factory=dict)
    base_location: str = ""


class Experience(BaseModel):
    total_years_actual: float
    years_for_forms: int
    never_exceed_actual: bool = True
    current_employer: str = ""
    current_client: str = ""
    use_client_name: bool = False
    client_display: str = ""
    domains: list[str] = Field(default_factory=list)


class Settings(BaseModel):
    """Explicit settings. null/None means unset — tool must never guess."""

    work_authorization_india: bool | None = None
    work_authorization_other_countries: str | None = None
    visa_sponsorship_needed: bool | None = None
    open_to_relocation: bool | None = None
    onsite_ok: bool | None = None
    hybrid_ok: bool | None = None
    remote_ok: bool | None = None
    night_shifts_ok: bool | None = None
    employment_types_ok: list[str] | None = None
    notice_period_days: int | None = None
    salary_expectation: str | None = None
    india_cities: list[str] = Field(default_factory=list)
    remote_regions_ok: list[str] = Field(default_factory=list)


class Targets(BaseModel):
    primary_titles: list[str] = Field(default_factory=list)
    secondary_titles: list[str] = Field(default_factory=list)
    deprioritized_categories: list[str] = Field(default_factory=list)
    enabled_extra_categories: list[str] = Field(default_factory=list)
    seniority_stretch: int = 2


class Dealbreakers(BaseModel):
    titles_exclude: list[str] = Field(default_factory=list)
    keywords_exclude: list[str] = Field(default_factory=list)


class Learning(BaseModel):
    topics: list[str] = Field(default_factory=list)
    not_claimable: bool = True


class Profile(BaseModel):
    """Complete candidate profile from profile.yaml."""

    schema_version: int
    identity: Identity
    experience: Experience
    settings: Settings = Field(default_factory=Settings)
    targets: Targets = Field(default_factory=Targets)
    dealbreakers: Dealbreakers = Field(default_factory=Dealbreakers)
    positioning: str = ""
    skills: list[Skill] = Field(default_factory=list)
    learning: Learning = Field(default_factory=Learning)
    known_gaps: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Resume-base models (Section 7.4)
# ---------------------------------------------------------------------------

class Bullet(BaseModel):
    id: str
    type: str  # professional | project | experiment
    text: str
    skills: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    domain: str | None = None


class SummaryVariant(BaseModel):
    id: str
    text: str


class Role(BaseModel):
    id: str
    employer: str = ""
    client: str = ""
    title: str = ""
    start: str = ""
    end: str | None = None
    domains: list[str] = Field(default_factory=list)
    bullets: list[Bullet] = Field(default_factory=list)


class ProjectEntry(BaseModel):
    id: str
    name: str
    type: str = "project"
    relevance: str | None = None
    # Technology names shown beside the project title, e.g. Spring Boot, React.
    stack: list[str] = Field(default_factory=list)
    bullets: list[Bullet] = Field(default_factory=list)


class ExperimentEntry(BaseModel):
    id: str
    name: str
    type: str = "experiment"
    bullets: list[Bullet] = Field(default_factory=list)


class Achievement(BaseModel):
    id: str
    text: str


class Certification(BaseModel):
    id: str
    name: str


class EducationEntry(BaseModel):
    id: str
    institution: str
    degree: str = ""
    # Shown right-aligned beside the degree, e.g. "CGPA: 8.5" or a graduation year.
    detail: str = ""


class ResumeBase(BaseModel):
    """Bullet bank from resume_base.yaml."""

    schema_version: int
    summary_variants: list[SummaryVariant] = Field(default_factory=list)
    roles: list[Role] = Field(default_factory=list)
    projects: list[ProjectEntry] = Field(default_factory=list)
    experiments: list[ExperimentEntry] = Field(default_factory=list)
    education: list[EducationEntry] = Field(default_factory=list)
    achievements: list[Achievement] = Field(default_factory=list)
    certifications: list[Certification] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Scoring (Section 11)
# ---------------------------------------------------------------------------

class MatchedSkill(BaseModel):
    skill: str
    level: EvidenceLevel | str
    evidence_ids: list[str] = Field(default_factory=list)


class ScoreResult(BaseModel):
    """Deterministic scoring output for a job."""

    job_id: int | None = None
    total: float
    skills_score: float = 0.0
    experience_score: float = 0.0
    seniority_score: float = 0.0
    location_score: float = 0.0
    extras_score: float = 0.0
    tier: str | None = None  # A | B | None
    matched_skills: list[MatchedSkill] = Field(default_factory=list)
    missing_required: list[str] = Field(default_factory=list)
    missing_preferred: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
