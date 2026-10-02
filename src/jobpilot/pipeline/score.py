"""Deterministic scoring engine for JobPilot.

Implements Section 11 of JOBPILOT_DESIGN.md.
Pure functions only for scoring logic — zero database or network I/O.

Component Weights (Total 0–100):
- Skills (40): Required skills 75%, Preferred skills 25%, weighted by evidence level.
- Experience (20): Uses total_years_actual with linear decay past reach limit.
- Seniority/Title (15): Matches primary and secondary titles, conditioned on experience.
- Location/Remote (15): India city match or remote open to India.
- Extras (10): Salary vs target, stack bonuses, company board list bonus.

Hard rules:
- remote_eligibility.open_to_india == 'no' and not India city → not_eligible.
- visa_or_work_auth_required is True and unauthorized → not_eligible (or work_auth_unset).
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass

from jobpilot.config import AppConfig, load_config
from jobpilot.db import save_score, update_status
from jobpilot.models import (
    EvidenceLevel,
    Job,
    JobAnalysis,
    MatchedSkill,
    Profile,
    ScoreResult,
)
from jobpilot.profile.loader import load_profile
from jobpilot.profile.skills import normalize_skill

logger = logging.getLogger(__name__)

COMMON_INDIA_CITIES = {
    "bangalore",
    "bengaluru",
    "hyderabad",
    "pune",
    "mumbai",
    "delhi",
    "new delhi",
    "ncr",
    "noida",
    "gurgaon",
    "gurugram",
    "chennai",
    "kolkata",
    "ahmedabad",
    "kochi",
    "cochin",
    "trivandrum",
    "thiruvananthapuram",
    "india",
}


@dataclass
class ScoringSummary:
    """Summary of a scoring run."""

    total_scored: int = 0
    tier_a: int = 0
    tier_b: int = 0
    below_threshold: int = 0
    not_eligible: int = 0


# ---------------------------------------------------------------------------
# Requirement ↔ Evidence Mapping helper
# ---------------------------------------------------------------------------

def classify_evidence_level(level: EvidenceLevel | str) -> str:
    """Map evidence level to category: covered | partial | learning_only | missing."""
    lvl_str = level.value if isinstance(level, EvidenceLevel) else str(level)
    if lvl_str == EvidenceLevel.VERIFIED_PROFESSIONAL.value:
        return "covered"
    if lvl_str in (
        EvidenceLevel.VERIFIED_PROJECT.value,
        EvidenceLevel.VERIFIED_CERTIFICATION.value,
        EvidenceLevel.EXPERIMENTAL.value,
    ):
        return "partial"
    if lvl_str == EvidenceLevel.LEARNING.value:
        return "learning_only"
    return "missing"


# ---------------------------------------------------------------------------
# Pure Scoring Components
# ---------------------------------------------------------------------------

def score_skills(
    analysis: JobAnalysis,
    profile: Profile,
    config: AppConfig,
) -> tuple[float, list[MatchedSkill], list[str], list[str], list[str]]:
    """Compute skills score (max 40) and requirement-evidence mapping.

    Returns:
        (skills_score, matched_skills, missing_required, missing_preferred, flags)
    """
    weights = config.scoring.evidence_weights

    # Map candidate profile skills by normalized lower name
    profile_skills_map = {
        normalize_skill(s.name).strip().lower(): s
        for s in profile.skills
    }

    matched_skills: list[MatchedSkill] = []
    missing_required: list[str] = []
    missing_preferred: list[str] = []
    flags: list[str] = []

    seen_skills: set[str] = set()

    # Score required skills (75% of 40 = 30 points)
    req_weights_sum = 0.0
    for s in analysis.required_skills:
        clean_name = normalize_skill(s).strip()
        key = clean_name.lower()
        if not key:
            continue

        skill_obj = profile_skills_map.get(key)
        if skill_obj is not None:
            level = skill_obj.level
            evidence_ids = skill_obj.evidence
            w = weights.get(level.value, 0.0)
            if level in (EvidenceLevel.VERIFIED_PROJECT, EvidenceLevel.VERIFIED_CERTIFICATION, EvidenceLevel.EXPERIMENTAL):
                flags.append(f"project_only:{clean_name}")
            elif level == EvidenceLevel.LEARNING:
                flags.append(f"learning_only:{clean_name}")
        else:
            level = EvidenceLevel.UNKNOWN
            evidence_ids = []
            w = 0.0
            missing_required.append(clean_name)
            flags.append(f"missing_required:{clean_name}")

        req_weights_sum += w
        if key not in seen_skills:
            seen_skills.add(key)
            matched_skills.append(MatchedSkill(skill=clean_name, level=level, evidence_ids=evidence_ids))

    req_score = 30.0 * (req_weights_sum / len(analysis.required_skills)) if analysis.required_skills else 30.0

    # Score preferred skills (25% of 40 = 10 points)
    pref_weights_sum = 0.0
    for s in analysis.preferred_skills:
        clean_name = normalize_skill(s).strip()
        key = clean_name.lower()
        if not key:
            continue

        skill_obj = profile_skills_map.get(key)
        if skill_obj is not None:
            level = skill_obj.level
            evidence_ids = skill_obj.evidence
            w = weights.get(level.value, 0.0)
            if level in (EvidenceLevel.VERIFIED_PROJECT, EvidenceLevel.VERIFIED_CERTIFICATION, EvidenceLevel.EXPERIMENTAL):
                flag = f"project_only:{clean_name}"
                if flag not in flags:
                    flags.append(flag)
            elif level == EvidenceLevel.LEARNING:
                flag = f"learning_only:{clean_name}"
                if flag not in flags:
                    flags.append(flag)
        else:
            level = EvidenceLevel.UNKNOWN
            evidence_ids = []
            w = 0.0
            missing_preferred.append(clean_name)

        pref_weights_sum += w
        if key not in seen_skills:
            seen_skills.add(key)
            matched_skills.append(MatchedSkill(skill=clean_name, level=level, evidence_ids=evidence_ids))

    pref_score = 10.0 * (pref_weights_sum / len(analysis.preferred_skills)) if analysis.preferred_skills else 10.0

    total_skills = round(min(40.0, max(0.0, req_score + pref_score)), 2)
    return total_skills, matched_skills, missing_required, missing_preferred, flags


def score_experience(
    analysis: JobAnalysis,
    profile: Profile,
) -> tuple[float, list[str]]:
    """Compute experience score (max 20) with linear decay past candidate years.

    Returns:
        (experience_score, flags)
    """
    flags: list[str] = []
    actual_years = profile.experience.total_years_actual
    stretch = profile.targets.seniority_stretch  # default 2

    min_y = analysis.experience_min_years
    max_y = analysis.experience_max_years

    # If no experience requirement specified in JD, award full marks
    if min_y is None:
        return 20.0, flags

    if min_y > actual_years:
        flags.append(f"asks_{int(round(min_y))}plus_years")

    # Full marks if within [min, max] or min <= actual_years + 1
    if (max_y is not None and min_y <= actual_years <= max_y) or (min_y <= actual_years + 1.0):
        return 20.0, flags

    # Linear decay reaching 0 at seniority_stretch + 1 years beyond actual_years
    cutoff = actual_years + stretch + 1.0
    if min_y >= cutoff:
        return 0.0, flags

    # Linear interpolation between (actual_years + 1) and cutoff
    threshold = actual_years + 1.0
    fraction = (cutoff - min_y) / (cutoff - threshold)
    score = 20.0 * max(0.0, min(1.0, fraction))
    return round(score, 2), flags


def _title_matches(target: str, text: str) -> bool:
    """Case-insensitive substring or word boundary match."""
    pattern = rf"\b{re.escape(target.lower())}\b"
    return bool(re.search(pattern, text.lower()))


def score_seniority_title(
    analysis: JobAnalysis,
    job_title: str,
    profile: Profile,
    experience_score: float,
) -> float:
    """Compute seniority and title score (max 15).

    Senior titles score fully only when experience score is acceptable (>= 10.0).
    """
    combined_titles = f"{job_title} {analysis.normalized_title}".strip()

    title_fraction = 0.0

    # Match primary titles (full 1.0 fraction)
    for pt in profile.targets.primary_titles:
        if _title_matches(pt, combined_titles) or pt.lower() in combined_titles.lower():
            title_fraction = 1.0
            break

    # Match secondary titles (80% 0.8 fraction)
    if title_fraction == 0.0:
        for st in profile.targets.secondary_titles:
            if _title_matches(st, combined_titles) or st.lower() in combined_titles.lower():
                title_fraction = 0.8
                break

    # Generic engineering terms
    if title_fraction == 0.0:
        if any(term in combined_titles.lower() for term in ("backend", "software engineer", "developer", "java", "spring")):
            title_fraction = 0.5
        else:
            title_fraction = 0.2

    base_score = 15.0 * title_fraction

    # Condition senior titles on acceptable experience score (>= 10.0 out of 20)
    sen = analysis.seniority.lower()
    is_senior = sen in ("senior", "lead", "staff", "principal") or any(
        s in job_title.lower() for s in ("senior", "sr.", "sr ", "lead", "staff", "principal")
    )

    if is_senior and experience_score < 10.0:
        # Scaled down when experience score is not acceptable
        penalty_ratio = max(0.25, experience_score / 20.0)
        base_score *= penalty_ratio

    return round(min(15.0, max(0.0, base_score)), 2)


def is_india_location(analysis: JobAnalysis, job_location: str | None, profile: Profile) -> bool:
    """Determine if a job is in an India city."""
    configured_cities = {c.lower().strip() for c in (profile.settings.india_cities or []) if c.strip()}
    allowed_cities = configured_cities | COMMON_INDIA_CITIES

    loc_texts = []
    if job_location:
        loc_texts.append(job_location.lower())
    if analysis.locations:
        loc_texts.extend(loc.lower() for loc in analysis.locations if loc)

    for text in loc_texts:
        if not text:
            continue
        for city in allowed_cities:
            if re.search(rf"\b{re.escape(city)}\b", text):
                return True
    return False


def score_location(
    analysis: JobAnalysis,
    job_location: str | None,
    profile: Profile,
) -> tuple[float, list[str]]:
    """Compute location score (max 15) and eligibility flags."""
    flags: list[str] = []

    # 1. India city match = full marks
    if is_india_location(analysis, job_location, profile):
        return 15.0, flags

    # 2. Remote check via remote_eligibility
    open_to_india = analysis.remote_eligibility.open_to_india.lower() if analysis.remote_eligibility else "unclear"

    if open_to_india == "yes":
        return 15.0, flags
    if open_to_india == "unclear":
        flags.append("region_unclear")
        return 7.5, flags
    if open_to_india == "no":
        flags.append("not_eligible")
        return 0.0, flags

    # Fallback when open_to_india is unstated
    if analysis.location_type.lower() == "remote":
        flags.append("region_unclear")
        return 7.5, flags

    flags.append("region_unclear")
    return 5.0, flags


def _parse_salary_number(raw: str | None) -> float | None:
    """Extract numeric salary from candidate expectation string."""
    if not raw:
        return None
    cleaned = re.sub(r"[^\d.]", "", raw)
    try:
        return float(cleaned)
    except ValueError:
        return None


def score_extras(
    analysis: JobAnalysis,
    job_company: str,
    profile: Profile,
    config: AppConfig,
) -> tuple[float, list[str]]:
    """Compute extras score (max 10) covering salary, stack bonuses, and company list."""
    flags: list[str] = []

    # 1. Salary component (max 4.0 points)
    salary_target = _parse_salary_number(profile.settings.salary_expectation)
    job_salary_max = analysis.salary.max or analysis.salary.min

    if job_salary_max is None:
        salary_score = 2.0  # neutral 50%
        flags.append("salary_unknown")
    elif salary_target is None:
        salary_score = 2.0  # neutral 50%
    else:
        if job_salary_max >= salary_target:
            salary_score = 4.0
        else:
            salary_score = round(4.0 * max(0.0, job_salary_max / salary_target), 2)

    # 2. Stack bonuses from config (max 4.0 points)
    all_job_skills = {
        normalize_skill(s).lower()
        for s in (analysis.required_skills + analysis.preferred_skills)
    }
    bonus_matches = 0
    for bonus in config.stack_bonus:
        if normalize_skill(bonus).lower() in all_job_skills:
            bonus_matches += 1
    stack_score = min(4.0, float(bonus_matches))

    # 3. Company-list bonus (max 2.0 points)
    company_norm = job_company.strip().lower()
    known_companies = set()
    for board in (
        config.boards.greenhouse_boards
        + config.boards.lever_companies
        + config.boards.ashby_boards
        + config.sources.greenhouse_boards
        + config.sources.lever_companies
    ):
        known_companies.add(board.strip().lower())

    company_score = 2.0 if company_norm in known_companies else 0.0

    total_extras = round(min(10.0, salary_score + stack_score + company_score), 2)
    return total_extras, flags


# ---------------------------------------------------------------------------
# Main Pure Scoring Function
# ---------------------------------------------------------------------------

def score_job(
    job: Job | dict,
    analysis: JobAnalysis,
    profile: Profile,
    config: AppConfig,
) -> ScoreResult:
    """Pure scoring function. Computes deterministic score (0–100) and assigns tier."""
    job_id = job.id if isinstance(job, Job) else job.get("id", 0)
    job_title = (job.title or "") if isinstance(job, Job) else (job.get("title") or "")
    job_company = (job.company or "") if isinstance(job, Job) else (job.get("company") or "")
    job_location = (job.location or "") if isinstance(job, Job) else (job.get("location") or "")
    is_snippet_only = bool(job.snippet and not job.description) if isinstance(job, Job) else bool(job.get("snippet") and not job.get("description"))

    all_flags: list[str] = []

    # 1. Skills (40)
    skills_pts, matched_skills, missing_req, missing_pref, skill_flags = score_skills(
        analysis, profile, config
    )
    all_flags.extend(skill_flags)

    # 2. Experience (20)
    exp_pts, exp_flags = score_experience(analysis, profile)
    all_flags.extend(exp_flags)

    # 3. Seniority/Title (15)
    sen_pts = score_seniority_title(analysis, job_title, profile, exp_pts)

    # 4. Location/Remote (15)
    loc_pts, loc_flags = score_location(analysis, job_location, profile)
    all_flags.extend(loc_flags)

    # 5. Extras (10)
    extras_pts, extra_flags = score_extras(analysis, job_company, profile, config)
    all_flags.extend(extra_flags)

    # Hard rules: Work authorization
    if analysis.visa_or_work_auth_required:
        in_india = is_india_location(analysis, job_location, profile)
        if in_india:
            auth_india = profile.settings.work_authorization_india
            if auth_india is None:
                all_flags.append("work_auth_unset")
            elif not auth_india:
                all_flags.append("not_eligible")
        else:
            auth_other = profile.settings.work_authorization_other_countries
            if auth_other is None:
                all_flags.append("work_auth_unset")
            elif not auth_other:
                all_flags.append("not_eligible")

    if is_snippet_only:
        all_flags.append("low_confidence_snippet_only")

    # Deduplicate flags preserving order
    unique_flags = list(dict.fromkeys(all_flags))

    # Compute total
    total = round(skills_pts + exp_pts + sen_pts + loc_pts + extras_pts, 2)
    total = min(100.0, max(0.0, total))

    # Assign Tier
    if "not_eligible" in unique_flags:
        tier = None
    elif total >= config.thresholds.tier_a:
        tier = "A"
    elif total >= config.thresholds.tier_b:
        tier = "B"
    else:
        tier = None

    return ScoreResult(
        job_id=job_id,
        total=total,
        skills_score=skills_pts,
        experience_score=exp_pts,
        seniority_score=sen_pts,
        location_score=loc_pts,
        extras_score=extras_pts,
        tier=tier,
        matched_skills=matched_skills,
        missing_required=missing_req,
        missing_preferred=missing_pref,
        flags=unique_flags,
    )


# ---------------------------------------------------------------------------
# Pipeline Execution (I/O)
# ---------------------------------------------------------------------------

def run_scoring(
    conn: sqlite3.Connection,
    config: AppConfig | None = None,
    profile: Profile | None = None,
    limit: int | None = None,
) -> ScoringSummary:
    """Score all jobs in status 'analyzed' and save results to scores table."""
    cfg = config or load_config()
    prof = profile or load_profile()

    summary = ScoringSummary()

    query = (
        "SELECT j.id, j.fingerprint, j.source, j.company, j.title, j.location, "
        "       j.remote_type, j.url, j.apply_url, j.description, j.snippet, j.status, "
        "       a.analysis_json "
        "FROM jobs j "
        "JOIN ( "
        "    SELECT job_id, analysis_json, MAX(created_at) "
        "    FROM analyses "
        "    GROUP BY job_id "
        ") a ON j.id = a.job_id "
        "WHERE j.status = 'analyzed' "
        "ORDER BY j.id ASC"
    )
    if limit is not None and limit > 0:
        query += f" LIMIT {int(limit)}"

    rows = conn.execute(query).fetchall()

    for row in rows:
        job = Job(
            id=row["id"],
            fingerprint=row["fingerprint"],
            source=row["source"],
            company=row["company"],
            title=row["title"],
            location=row["location"],
            remote_type=row["remote_type"],
            url=row["url"],
            apply_url=row["apply_url"],
            description=row["description"],
            snippet=row["snippet"],
            status=row["status"],
        )

        try:
            analysis = JobAnalysis.model_validate_json(row["analysis_json"])
        except Exception as exc:
            logger.error("Failed to parse analysis_json for Job %d: %s", job.id, exc)
            continue

        score_res = score_job(job, analysis, prof, cfg)
        save_score(conn, score_res.model_dump())

        if "not_eligible" in score_res.flags:
            update_status(conn, job.id, "filtered", reason="not_eligible")
            summary.not_eligible += 1
        else:
            update_status(conn, job.id, "scored")
            if score_res.tier == "A":
                summary.tier_a += 1
            elif score_res.tier == "B":
                summary.tier_b += 1
            else:
                summary.below_threshold += 1

        summary.total_scored += 1

    return summary
