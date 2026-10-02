"""Claims validation for LLM-generated tailored summaries.

Implements Section 12.2 of JOBPILOT_DESIGN.md.
Every piece of LLM output is validated against profile.yaml and resume_base.yaml
before persistence.  Never persist unvalidated text.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from jobpilot.config import AppConfig
from jobpilot.llm.redact import _EMAIL_RE, _PHONE_RE
from jobpilot.models import Bullet, EvidenceLevel, Profile, ResumeBase
from jobpilot.profile.skills import normalize_skill

# ---------------------------------------------------------------------------
# Pydantic models for LLM output
# ---------------------------------------------------------------------------


class TailoredClaim(BaseModel):
    """One skill claim made in the tailored summary."""

    bullet_id: str
    skill: str
    context: str  # text snippet from the summary mentioning this skill
    evidence_level: str  # must match or be lower than profile level


class TailoredOutput(BaseModel):
    """Structured output from the tailor-summary LLM call."""

    summary: str
    claims: list[TailoredClaim] = Field(default_factory=list)
    bullet_ids: list[str] = Field(default_factory=list)


class NoteOutput(BaseModel):
    """Structured output from the note LLM call."""

    note: str


# ---------------------------------------------------------------------------
# Evidence-level hierarchy (higher = stronger claim)
# ---------------------------------------------------------------------------

_LEVEL_RANK: dict[str, int] = {
    EvidenceLevel.VERIFIED_PROFESSIONAL.value: 6,
    EvidenceLevel.VERIFIED_PROJECT.value: 5,
    EvidenceLevel.VERIFIED_CERTIFICATION.value: 4,
    EvidenceLevel.EXPERIMENTAL.value: 3,
    EvidenceLevel.LEARNING.value: 2,
    EvidenceLevel.UNKNOWN.value: 1,
}

# Evidence levels that require a project qualifier in summary text
_QUALIFIER_REQUIRED_LEVELS = {
    EvidenceLevel.VERIFIED_PROJECT.value,
    EvidenceLevel.VERIFIED_CERTIFICATION.value,
    EvidenceLevel.EXPERIMENTAL.value,
}

# Evidence levels that must never appear in experience text
_FORBIDDEN_IN_EXPERIENCE = {
    EvidenceLevel.LEARNING,
    EvidenceLevel.UNKNOWN,
}

# Ownership / leadership verbs that must be verified against bullet text
_LEADERSHIP_VERBS = [
    "led",
    "architected",
    "spearheaded",
    "owned",
    "managed",
    "drove",
    "headed",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _collect_all_bullets(resume_base: ResumeBase) -> dict[str, Bullet]:
    """Return a dict of bullet_id -> Bullet for every bullet in the resume."""
    bullets: dict[str, Bullet] = {}
    for role in resume_base.roles:
        for b in role.bullets:
            bullets[b.id] = b
    for proj in resume_base.projects:
        for b in proj.bullets:
            bullets[b.id] = b
    for exp in resume_base.experiments:
        for b in exp.bullets:
            bullets[b.id] = b
    return bullets


def _skill_in_text(skill_name: str, text: str) -> bool:
    """Check if a skill name appears in text (case-insensitive word boundary)."""
    pattern = re.compile(rf"\b{re.escape(skill_name)}\b", re.IGNORECASE)
    return bool(pattern.search(text))


def _collect_metrics_from_bullets(
    bullets: dict[str, Bullet],
    selected_ids: list[str] | None = None,
) -> list[str]:
    """Collect all metric strings from the specified (or all) bullets."""
    ids = selected_ids if selected_ids is not None else list(bullets.keys())
    metrics: list[str] = []
    for bid in ids:
        b = bullets.get(bid)
        if b and b.metrics:
            metrics.extend(b.metrics)
    return metrics


# ---------------------------------------------------------------------------
# Main validation
# ---------------------------------------------------------------------------


def validate_claims(
    output: TailoredOutput,
    profile: Profile,
    resume_base: ResumeBase,
    config: AppConfig,
    selected_bullet_ids: list[str] | None = None,
) -> list[str]:
    """Validate an LLM-generated TailoredOutput against profile and resume.

    Returns a list of error messages.  Empty list means the output is valid.
    """
    errors: list[str] = []
    all_bullets = _collect_all_bullets(resume_base)

    # Build normalised profile skill lookup
    profile_skills: dict[str, Any] = {}
    for s in profile.skills:
        profile_skills[normalize_skill(s.name).lower()] = s

    summary_lower = output.summary.lower()

    # --- Rule 1: Bullet ID existence ---
    for claim in output.claims:
        if claim.bullet_id not in all_bullets:
            errors.append(
                f"Claim references unknown bullet ID: {claim.bullet_id}"
            )
    for bid in output.bullet_ids:
        if bid not in all_bullets:
            errors.append(f"Selected unknown bullet ID: {bid}")

    # --- Rule 2: Evidence-level matching ---
    for claim in output.claims:
        norm = normalize_skill(claim.skill).lower()
        skill_obj = profile_skills.get(norm)
        if skill_obj is not None:
            prof_rank = _LEVEL_RANK.get(skill_obj.level.value, 0)
            claim_rank = _LEVEL_RANK.get(claim.evidence_level, 0)
            if claim_rank > prof_rank:
                errors.append(
                    f"Claim level {claim.evidence_level} for '{claim.skill}' "
                    f"exceeds profile level {skill_obj.level.value}"
                )
        else:
            errors.append(f"Skill '{claim.skill}' not found in profile")

    # --- Rule 3: Project qualifier requirement ---
    qualifiers = [q.lower() for q in config.claims.project_qualifiers]
    for claim in output.claims:
        if claim.evidence_level in _QUALIFIER_REQUIRED_LEVELS and not any(
            q in summary_lower for q in qualifiers
        ):
            errors.append(
                f"Summary missing project qualifier for "
                f"{claim.evidence_level} skill: {claim.skill}"
            )

    # --- Rule 4: LEARNING / UNKNOWN skills must not appear in summary ---
    for s in profile.skills:
        if s.level in _FORBIDDEN_IN_EXPERIENCE and _skill_in_text(s.name, output.summary):
            errors.append(
                f"Summary contains {s.level.value} skill: {s.name}"
            )

    # --- Rule 5: known_gaps must not appear ---
    for gap in profile.known_gaps:
        if _skill_in_text(gap, output.summary):
            errors.append(f"Summary mentions known gap: {gap}")

    # --- Rule 6: learning.topics must not appear as experience ---
    for topic in profile.learning.topics:
        if topic.lower() in summary_lower:
            errors.append(f"Summary mentions learning topic: {topic}")

    # --- Rule 7: Years cap ---
    years_matches = re.findall(
        r"(\d+)\+?\s*(?:years|yrs)", output.summary, re.IGNORECASE
    )
    for y_str in years_matches:
        if int(y_str) > profile.experience.years_for_forms:
            errors.append(
                f"Claimed years ({y_str}) exceeds cap "
                f"({profile.experience.years_for_forms})"
            )

    # --- Rule 8: Metrics validation ---
    all_metrics = _collect_metrics_from_bullets(all_bullets, selected_bullet_ids)
    # Extract percentages and formatted counts from summary (skip the "N years" already handled)
    metric_candidates = re.findall(
        r"\b\d+(?:,\d{3})*(?:\.\d+)?\s*%", output.summary
    )  # percentages e.g. 45%, 99%
    metric_candidates += re.findall(
        r"\b\d+(?:,\d{3})*\+?\s*(?:records|users|tenants?|businesses|projects|requests)\b",
        output.summary,
        re.IGNORECASE,
    )  # counts with units
    # Also catch numbers with ~ prefix
    metric_candidates += re.findall(r"~\d+(?:,\d{3})*(?:\.\d+)?%?", output.summary)

    for num in metric_candidates:
        clean_num = num.strip()
        found = any(clean_num in m for m in all_metrics)
        if not found:
            errors.append(
                f"Metric '{clean_num}' in summary not found in selected bullets"
            )

    # --- Rule 9: Client-as-employer ---
    if (
        not profile.experience.use_client_name
        and profile.experience.current_client
    ):
        client_lower = profile.experience.current_client.lower()
        if (
            f"at {client_lower}" in summary_lower
            or f"for {client_lower}" in summary_lower
            or f"with {client_lower}" in summary_lower
        ):
            errors.append(
                "Summary mentions client as employer, which is forbidden"
            )

    # --- Rule 10: Contact details ---
    if _EMAIL_RE.search(output.summary) or _PHONE_RE.search(output.summary):
        errors.append("Summary contains unredacted contact details")

    # --- Rule 11: Ownership / leadership verbs ---
    bullets_to_check = [
        all_bullets[bid]
        for bid in (selected_bullet_ids or list(all_bullets.keys()))
        if bid in all_bullets
    ]
    bullet_texts_combined = " ".join(b.text for b in bullets_to_check)

    for verb in _LEADERSHIP_VERBS:
        if (
            re.search(rf"\b{re.escape(verb)}\b", output.summary, re.IGNORECASE)
            and not re.search(rf"\b{re.escape(verb)}\b", bullet_texts_combined, re.IGNORECASE)
        ):
            errors.append(
                f"Summary contains unverified leadership/ownership verb '{verb}' not present in selected bullets"
            )

    return errors


def validate_note(
    note: str,
    profile: Profile,
    resume_base: ResumeBase,
    config: AppConfig,
) -> list[str]:
    """Validate an LLM-generated application note.

    Applies a subset of claims rules to the note text.
    """
    errors: list[str] = []
    note_lower = note.lower()

    # LEARNING / UNKNOWN skills
    for s in profile.skills:
        if s.level in _FORBIDDEN_IN_EXPERIENCE and _skill_in_text(s.name, note):
            errors.append(
                f"Note contains {s.level.value} skill: {s.name}"
            )

    # known_gaps
    for gap in profile.known_gaps:
        if _skill_in_text(gap, note):
            errors.append(f"Note mentions known gap: {gap}")

    # learning.topics
    for topic in profile.learning.topics:
        if topic.lower() in note_lower:
            errors.append(f"Note mentions learning topic: {topic}")

    # Years cap
    years_matches = re.findall(
        r"(\d+)\+?\s*(?:years|yrs)", note, re.IGNORECASE
    )
    for y_str in years_matches:
        if int(y_str) > profile.experience.years_for_forms:
            errors.append(
                f"Claimed years ({y_str}) exceeds cap "
                f"({profile.experience.years_for_forms})"
            )

    # Client-as-employer
    if (
        not profile.experience.use_client_name
        and profile.experience.current_client
    ):
        client_lower = profile.experience.current_client.lower()
        if (
            f"at {client_lower}" in note_lower
            or f"for {client_lower}" in note_lower
            or f"with {client_lower}" in note_lower
        ):
            errors.append(
                "Note mentions client as employer, which is forbidden"
            )

    # Contact details
    if _EMAIL_RE.search(note) or _PHONE_RE.search(note):
        errors.append("Note contains unredacted contact details")

    # Leadership verbs
    all_bullets = _collect_all_bullets(resume_base)
    bullet_texts_combined = " ".join(b.text for b in all_bullets.values())
    for verb in _LEADERSHIP_VERBS:
        if (
            re.search(rf"\b{re.escape(verb)}\b", note, re.IGNORECASE)
            and not re.search(rf"\b{re.escape(verb)}\b", bullet_texts_combined, re.IGNORECASE)
        ):
            errors.append(
                f"Note contains unverified leadership/ownership verb '{verb}' not present in selected bullets"
            )

    return errors
