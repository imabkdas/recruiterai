"""Profile and resume-base loader and validator for JobPilot.

Loads data/profile.yaml and data/resume_base.yaml, validates them against
the schema, and cross-references evidence IDs, skill names, and settings.

Unset (null) settings are reported as warnings, not errors.
"""

from __future__ import annotations

import logging
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from jobpilot.models import (
    EvidenceLevel,
    Profile,
    ResumeBase,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------

class ValidationResult:
    """Collects errors, warnings, and info from validation."""

    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.info: list[str] = []

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def add_info(self, msg: str) -> None:
        self.info.append(msg)

    def summary(self) -> str:
        lines = []
        if self.errors:
            lines.append(f"\n❌ {len(self.errors)} error(s):")
            for e in self.errors:
                lines.append(f"  ERROR: {e}")
        if self.warnings:
            lines.append(f"\n⚠️  {len(self.warnings)} warning(s):")
            for w in self.warnings:
                lines.append(f"  WARN: {w}")
        if self.info:
            lines.append("\nℹ️  Info:")
            for i in self.info:
                lines.append(f"  {i}")
        if not self.errors and not self.warnings:
            lines.append("\n✅ Profile validation passed with no issues.")
        elif not self.errors:
            lines.append("\n✅ Profile validation passed (warnings only, no errors).")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def _find_project_root() -> Path:
    """Walk up from CWD to find the directory containing pyproject.toml."""
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        if (parent / "pyproject.toml").exists():
            return parent
    return cwd


def load_profile(path: str | Path | None = None) -> Profile:
    """Load and parse profile.yaml into a Profile model."""
    if path is None:
        path = _find_project_root() / "data" / "profile.yaml"
    path = Path(path)

    with open(path) as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}

    return Profile.model_validate(raw)


def load_resume_base(path: str | Path | None = None) -> ResumeBase:
    """Load and parse resume_base.yaml into a ResumeBase model."""
    if path is None:
        path = _find_project_root() / "data" / "resume_base.yaml"
    path = Path(path)

    with open(path) as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}

    return ResumeBase.model_validate(raw)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _collect_all_bullet_ids(resume: ResumeBase) -> set[str]:
    """Return the set of all bullet IDs in the resume base."""
    ids: set[str] = set()
    for role in resume.roles:
        for b in role.bullets:
            ids.add(b.id)
    for proj in resume.projects:
        for b in proj.bullets:
            ids.add(b.id)
    for exp in resume.experiments:
        for b in exp.bullets:
            ids.add(b.id)
    return ids


def _collect_all_valid_evidence_ids(resume: ResumeBase) -> set[str]:
    """Return all valid evidence IDs: bullet IDs + project IDs + experiment IDs + certification IDs."""
    ids = _collect_all_bullet_ids(resume)
    # Project container IDs (p001, p002, ...)
    for proj in resume.projects:
        ids.add(proj.id)
    # Experiment container IDs (e001, ...)
    for exp in resume.experiments:
        ids.add(exp.id)
    # Certification IDs (c001, ...)
    for cert in resume.certifications:
        ids.add(cert.id)
    return ids


def _collect_all_resume_skills(resume: ResumeBase) -> set[str]:
    """Return all skill names mentioned in any bullet."""
    skills: set[str] = set()
    for role in resume.roles:
        for b in role.bullets:
            skills.update(b.skills)
    for proj in resume.projects:
        for b in proj.bullets:
            skills.update(b.skills)
    for exp in resume.experiments:
        for b in exp.bullets:
            skills.update(b.skills)
    return skills


def validate_profile_and_resume(
    profile: Profile,
    resume: ResumeBase,
) -> ValidationResult:
    """Run all validation checks on the profile and resume base.

    Returns a ValidationResult with errors, warnings, and info.
    """
    result = ValidationResult()

    # ----- Schema version check -----
    if profile.schema_version != 2:
        result.error(f"profile.yaml schema_version is {profile.schema_version}, expected 2")
    if resume.schema_version != 2:
        result.error(f"resume_base.yaml schema_version is {resume.schema_version}, expected 2")

    # ----- Evidence level enum check -----
    valid_levels = set(EvidenceLevel)
    for skill in profile.skills:
        if skill.level not in valid_levels:
            result.error(f"Skill '{skill.name}' has invalid evidence level: {skill.level}")

    # ----- Duplicate skill names in profile -----
    skill_names = [s.name for s in profile.skills]
    name_counts = Counter(skill_names)
    for name, count in name_counts.items():
        if count > 1:
            result.error(f"Duplicate skill name in profile: '{name}' appears {count} times")

    # ----- Collect IDs from resume -----
    valid_evidence_ids = _collect_all_valid_evidence_ids(resume)
    all_bullet_ids_list: list[str] = []
    for role in resume.roles:
        for b in role.bullets:
            all_bullet_ids_list.append(b.id)
    for proj in resume.projects:
        for b in proj.bullets:
            all_bullet_ids_list.append(b.id)
    for exp in resume.experiments:
        for b in exp.bullets:
            all_bullet_ids_list.append(b.id)

    # ----- Duplicate bullet IDs -----
    bullet_id_counts = Counter(all_bullet_ids_list)
    for bid, count in bullet_id_counts.items():
        if count > 1:
            result.error(f"Duplicate bullet ID in resume_base: '{bid}' appears {count} times")

    # ----- Evidence IDs in profile → must exist in resume -----
    for skill in profile.skills:
        for eid in skill.evidence:
            if eid not in valid_evidence_ids:
                result.error(
                    f"Skill '{skill.name}' references evidence ID '{eid}' "
                    f"which does not exist in resume_base.yaml"
                )

    # ----- Skills in resume bullets → must exist in profile -----
    profile_skill_names = {s.name for s in profile.skills}
    resume_skills = _collect_all_resume_skills(resume)
    for rs in sorted(resume_skills):
        if rs not in profile_skill_names:
            result.error(
                f"Skill '{rs}' is used in a resume bullet but not defined in profile.yaml"
            )

    # ----- Metrics must be lists of strings -----
    for role in resume.roles:
        for b in role.bullets:
            if not isinstance(b.metrics, list):
                result.error(f"Bullet '{b.id}' metrics must be a list, got {type(b.metrics).__name__}")
            elif not all(isinstance(m, str) for m in b.metrics):
                result.error(f"Bullet '{b.id}' metrics must all be strings")
    for proj in resume.projects:
        for b in proj.bullets:
            if not isinstance(b.metrics, list):
                result.error(f"Bullet '{b.id}' metrics must be a list, got {type(b.metrics).__name__}")
            elif not all(isinstance(m, str) for m in b.metrics):
                result.error(f"Bullet '{b.id}' metrics must all be strings")

    # ----- Unset settings → warnings -----
    settings = profile.settings
    nullable_settings = [
        "work_authorization_india",
        "work_authorization_other_countries",
        "visa_sponsorship_needed",
        "open_to_relocation",
        "onsite_ok",
        "hybrid_ok",
        "night_shifts_ok",
        "employment_types_ok",
        "notice_period_days",
        "salary_expectation",
    ]
    for setting_name in nullable_settings:
        value = getattr(settings, setting_name, "NOT_FOUND")
        if value is None:
            result.warn(f"Setting '{setting_name}' is null (unset). The tool will ask for your input when needed.")

    # ----- Empty evidence lists → warnings -----
    for skill in profile.skills:
        if skill.level != EvidenceLevel.LEARNING and not skill.evidence:
            result.warn(f"Skill '{skill.name}' has level {skill.level.value} but no evidence IDs linked")

    # ----- Empty india_cities -----
    if not settings.india_cities:
        result.warn("settings.india_cities is empty. Location matching for Indian cities will be limited.")

    # ----- Skills per evidence level (info) -----
    level_counts: Counter[str] = Counter()
    for skill in profile.skills:
        level_counts[skill.level.value] += 1

    result.add_info(f"Total skills: {len(profile.skills)}")
    for level in EvidenceLevel:
        count = level_counts.get(level.value, 0)
        if count > 0:
            result.add_info(f"  {level.value}: {count}")

    result.add_info(f"Total bullets in resume: {len(all_bullet_ids_list)}")
    result.add_info(f"Projects: {len(resume.projects)}, Experiments: {len(resume.experiments)}")
    result.add_info(f"Certifications: {len(resume.certifications)}, Achievements: {len(resume.achievements)}")

    return result
