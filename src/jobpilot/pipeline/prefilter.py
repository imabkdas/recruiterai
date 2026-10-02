"""Prefilter rules applied to jobs before LLM analysis.

Plain code filtering on title, hard exclusions, deprioritized categories,
locations, experience requirements, and posting age.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import UTC, datetime

from jobpilot.config import load_config
from jobpilot.db import update_status
from jobpilot.models import Profile
from jobpilot.profile.loader import load_profile

logger = logging.getLogger(__name__)

# Regional exclusion regexes
_REGION_EXCLUSION_PATTERNS = [
    (r"\bus\s+citizens?\s+only\b", "region:us_citizen_only"),
    (r"\bsecurity\s+clearance\b", "dealbreaker:security_clearance"),
    (r"\bmust\s+be\s+located\s+in\s+the\s+us\b", "region:us_only"),
    (r"\bmust\s+reside\s+in\s+(?:the\s+)?(?:us|usa|united\s+states)\b", "region:us_only"),
    (r"\bus\s+only\b", "region:us_only"),
    (r"\buk\s+only\b", "region:uk_only"),
    (r"\beu\s+timezone\b", "region:eu_timezone_only"),
]

# Location specific regex checks from section 9
_LOCATION_REGEX_CHECKS = [
    (re.compile(r"\bus\s+only\b", re.IGNORECASE), "region:us_only"),
    (re.compile(r"\bmust\s+reside\s+in\b", re.IGNORECASE), "region:must_reside_in"),
    (re.compile(r"\beu\s+timezone\b", re.IGNORECASE), "region:eu_timezone"),
    (re.compile(r"\buk\s+only\b", re.IGNORECASE), "region:uk_only"),
    (re.compile(r"\bwork\s+authorization\s+required\b", re.IGNORECASE), "region:work_authorization_required"),
    (re.compile(r"\bus\s+citizens?\s+only\b", re.IGNORECASE), "region:us_citizen_only"),
    (re.compile(r"\bmust\s+be\s+located\s+in\s+(?:the\s+)?us\b", re.IGNORECASE), "region:must_be_located_in_the_us"),
]

_CITY_SYNONYMS = {
    "bangalore": "bengaluru",
    "bengaluru": "bengaluru",
    "bombay": "mumbai",
    "mumbai": "mumbai",
    "calcutta": "kolkata",
    "kolkata": "kolkata",
    "madras": "chennai",
    "chennai": "chennai",
    "gurgaon": "gurugram",
    "gurugram": "gurugram",
    "cochin": "kochi",
    "kochi": "kochi",
    "trivandrum": "thiruvananthapuram",
    "thiruvananthapuram": "thiruvananthapuram",
}

_INDIAN_CITIES = {
    "bengaluru", "bangalore", "hyderabad", "pune", "chennai", "mumbai",
    "delhi", "new delhi", "noida", "gurgaon", "gurugram", "kolkata", "ahmedabad",
    "kochi", "cochin", "thiruvananthapuram", "trivandrum", "chandigarh",
    "jaipur", "indore", "bhubaneswar", "coimbatore", "mysore", "mysuru",
    "nagpur", "lucknow", "surat", "vadodara",
}

# Deprioritized category patterns: (category_name, regex)
_DEPRIORITIZED_PATTERNS = [
    ("pure_frontend", re.compile(r"\b(?:front[\s-]?end|ui|react|angular|vue)\b", re.IGNORECASE)),
    ("pure_mobile", re.compile(r"\b(?:mobile|android|ios|flutter|react\s+native)\b", re.IGNORECASE)),
    ("pure_devops_sre", re.compile(r"\b(?:devops|sre|site\s+reliability|infrastructure\s+engineer)\b", re.IGNORECASE)),
    ("pure_data_science", re.compile(r"\b(?:data\s+scientist|data\s+science)\b", re.IGNORECASE)),
    ("pure_ml_research", re.compile(r"\b(?:ml\s+research|machine\s+learning\s+research|ai\s+research)\b", re.IGNORECASE)),
]

_JAVA_SPRING_BACKEND_RE = re.compile(r"\b(?:java|spring|spring\s+boot|backend|back[\s-]?end)\b", re.IGNORECASE)
_ENG_DEV_RE = re.compile(r"\b(?:engineer|developer|architect|programmer)\b", re.IGNORECASE)

# Experience regex: e.g. "5-8 years", "5+ years", "5 to 8 years of experience"
_EXP_RANGE_RE = re.compile(
    r"\b(\d+)\s*(?:-|–|to|\+)\s*(?:\d+)?\s*(?:years?|yrs?)(?:\s+of)?\s+experience\b",
    re.IGNORECASE,
)


def filter_title(title: str, profile: Profile) -> tuple[bool, str | None]:
    """Check if title matches targets or is a valid Java/Spring/Backend engineer title."""
    t_lower = title.lower()

    # Check dealbreaker title exclusions
    for exc in profile.dealbreakers.titles_exclude:
        if exc.lower() in t_lower:
            return False, f"title_exclude:{exc}"

    # Target titles match
    for pt in profile.targets.primary_titles + profile.targets.secondary_titles:
        if pt.lower() in t_lower or t_lower in pt.lower():
            return True, None

    # General Java / Spring / Backend engineer match
    has_tech = bool(_JAVA_SPRING_BACKEND_RE.search(t_lower))
    has_role = bool(_ENG_DEV_RE.search(t_lower))
    if has_tech and has_role:
        return True, None

    return False, "title_mismatch"


def filter_dealbreakers(text: str, profile: Profile) -> tuple[bool, str | None]:
    """Check text for keywords_exclude and regional hard exclusions."""
    t_lower = text.lower()
    for kw in profile.dealbreakers.keywords_exclude:
        if kw.lower() in t_lower:
            return False, f"keyword_exclude:{kw}"

    for pattern, reason in _REGION_EXCLUSION_PATTERNS:
        if re.search(pattern, t_lower):
            return False, reason

    return True, None


def filter_deprioritized(title: str, text: str, profile: Profile) -> tuple[bool, str | None]:
    """Filter out purely deprioritized categories unless enabled or mentions Java/Spring/backend."""
    t_lower = title.lower()
    full_lower = f"{title} {text}".lower()

    # If it explicitly mentions Java, Spring, or backend, do NOT deprioritize
    if _JAVA_SPRING_BACKEND_RE.search(full_lower):
        return True, None

    enabled = set(profile.targets.enabled_extra_categories)
    for cat_name, pattern in _DEPRIORITIZED_PATTERNS:
        if (
            cat_name in profile.targets.deprioritized_categories
            and cat_name not in enabled
            and pattern.search(t_lower)
        ):
            return False, f"deprioritized_category:{cat_name}"

    return True, None


def filter_experience(text: str, profile: Profile) -> tuple[bool, str | None]:
    """Check if stated experience exceeds total_years_actual + seniority_stretch."""
    match = _EXP_RANGE_RE.search(text)
    if not match:
        return True, None

    min_years = int(match.group(1))
    max_allowed = profile.experience.total_years_actual + profile.targets.seniority_stretch
    if min_years > max_allowed:
        return False, f"experience_too_high:{min_years}_years_asked"

    return True, None


def filter_age(posted_at: str | None, max_days: int = 21) -> tuple[bool, str | None]:
    """Reject listings older than max_days."""
    if not posted_at:
        return True, None

    try:
        # Support various ISO format dates
        clean_date = posted_at.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean_date)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        age_days = (datetime.now(UTC) - dt).days
        if age_days > max_days:
            return False, f"job_too_old:{age_days}_days"
    except Exception:
        pass

    return True, None


def filter_location(location: str | None, text: str, profile: Profile) -> tuple[bool, str | None]:
    """Check location requirements per section 9:
    - Regex checks for 'US only', 'must reside in', 'EU timezone', 'UK only',
      'work authorization required'.
    - Accepts India cities from settings.india_cities and remote with remote_regions_ok.
    - A null/empty india_cities list must NOT reject on city alone.
    """
    combined = f"{location or ''}\n{text}".lower()

    # 1. Regex checks
    for pattern, reason in _LOCATION_REGEX_CHECKS:
        if pattern.search(combined):
            return False, reason

    if not location:
        return True, None

    loc_lower = location.lower().strip()

    # 2. Remote check
    is_remote = any(term in loc_lower for term in ("remote", "wfh", "work from home", "anywhere"))
    if is_remote:
        # Check if remote specifies an incompatible region
        allowed_regions = [r.lower().strip() for r in (profile.settings.remote_regions_ok or [])]
        restricted = ("us only", "usa only", "us", "usa", "emea", "europe", "uk", "canada", "latam")
        for reg in restricted:
            if re.search(rf"\b{reg}\b", loc_lower) and not any(reg in r for r in allowed_regions) and not any(
                al in loc_lower for al in ("worldwide", "anywhere", "india", "apac", "asia")
            ):
                return False, f"region_not_allowed:{location}"
        return True, None

    # 3. Physical location (India or foreign)
    is_indian = "india" in loc_lower or any(city in loc_lower for city in _INDIAN_CITIES)
    if is_indian:
        target_cities = [c.lower().strip() for c in (profile.settings.india_cities or [])]
        if target_cities:
            canonical_targets = {_CITY_SYNONYMS.get(tc, tc) for tc in target_cities}
            canonical_loc = loc_lower
            for raw_c, canon_c in _CITY_SYNONYMS.items():
                if raw_c in canonical_loc:
                    canonical_loc = canonical_loc.replace(raw_c, canon_c)

            if "india" in loc_lower or any(tc in canonical_loc for tc in canonical_targets):
                return True, None
            return False, f"city_not_in_targets:{location}"
        # A null or empty india_cities list must NOT reject on city alone
        return True, None

    # Foreign physical onsite location (not remote and not India)
    return False, f"location_not_supported:{location}"


def evaluate_job(job: dict, profile: Profile, max_job_age_days: int = 21) -> tuple[bool, str | None]:
    """Run all prefilter checks against a job dict.

    Returns (passed, reason_if_failed).
    """
    title = job.get("title", "")
    description = job.get("description", "") or ""
    snippet = job.get("snippet", "") or ""
    location = job.get("location")
    content = f"{title}\n{description}\n{snippet}"

    # 1. Title filter
    ok, reason = filter_title(title, profile)
    if not ok:
        return False, reason

    # 2. Location filter (regex checks + city/remote rules)
    ok, reason = filter_location(location, content, profile)
    if not ok:
        return False, reason

    # 3. Dealbreakers (keywords_exclude)
    ok, reason = filter_dealbreakers(content, profile)
    if not ok:
        return False, reason

    # 4. Deprioritized categories
    ok, reason = filter_deprioritized(title, content, profile)
    if not ok:
        return False, reason

    # 5. Experience check
    ok, reason = filter_experience(content, profile)
    if not ok:
        return False, reason

    # 6. Age check
    ok, reason = filter_age(job.get("posted_at"), max_days=max_job_age_days)
    if not ok:
        return False, reason

    return True, None


class PrefilterResult:
    """Funnel statistics for a prefilter run."""

    def __init__(self) -> None:
        self.evaluated: int = 0
        self.passed: int = 0
        self.filtered_out: int = 0
        self.needs_jd: int = 0
        self.reasons: dict[str, int] = {}


def run_prefilter(
    conn: sqlite3.Connection,
    profile: Profile | None = None,
    job_ids: list[int] | None = None,
) -> PrefilterResult:
    """Run prefilter on newly ingested jobs.

    Jobs that survive become:
    - 'needs_jd' if description is empty/null (alert emails, Adzuna snippets)
    - 'new' if full description is available
    """
    if profile is None:
        profile = load_profile()

    cfg = load_config()
    max_days = cfg.thresholds.max_job_age_days

    res = PrefilterResult()
    if job_ids is not None:
        placeholders = ",".join("?" for _ in job_ids)
        rows = conn.execute(f"SELECT * FROM jobs WHERE id IN ({placeholders})", job_ids).fetchall()  # noqa: S608
    else:
        rows = conn.execute("SELECT * FROM jobs WHERE status = 'new'").fetchall()

    for row in rows:
        job = dict(row)
        res.evaluated += 1

        passed, reason = evaluate_job(job, profile, max_job_age_days=max_days)
        if not passed:
            res.filtered_out += 1
            res.reasons[reason or "unspecified"] = res.reasons.get(reason or "unspecified", 0) + 1
            update_status(conn, job["id"], "filtered_out", reason=reason)
        else:
            res.passed += 1
            # If description is missing/null, job moves to needs_jd
            if not job.get("description"):
                update_status(conn, job["id"], "needs_jd")
                res.needs_jd += 1
            else:
                update_status(conn, job["id"], "new")

    return res
