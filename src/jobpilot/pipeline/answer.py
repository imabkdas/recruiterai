"""Application question answering for JobPilot.

Implements Section 12.4 of JOBPILOT_DESIGN.md.
Classifies questions, looks up the answer bank, and generates answers
from profile facts, settings, or LLM (free-text).
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from jobpilot.config import AppConfig
from jobpilot.llm.client import GeminiClient
from jobpilot.llm.redact import redact_text
from jobpilot.models import EvidenceLevel, Profile
from jobpilot.profile.skills import normalize_skill


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass
class AnswerResult:
    """Outcome of answering one application question."""

    question: str
    question_norm: str
    category: str  # profile_fact | setting | free_text
    answer: str
    is_draft: bool  # True for unapproved free_text answers
    answer_bank_id: int | None = None
    from_bank: bool = False  # True if reused from approved answer


# ---------------------------------------------------------------------------
# Question normalisation
# ---------------------------------------------------------------------------


def normalize_question(question: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    normalised = re.sub(r"[^\w\s]", "", question.lower())
    return re.sub(r"\s+", " ", normalised).strip()


# ---------------------------------------------------------------------------
# Classification (deterministic, no LLM)
# ---------------------------------------------------------------------------

_SETTING_PATTERNS = [
    # Pay: salary, CTC, ECTC, expected, current compensation, pay, package, etc.
    r"\bsalary\b",
    r"\bctc\b",
    r"\bectc\b",
    r"\bcompensation\b",
    r"\bcurrent compensation\b",
    r"\bexpected\b",
    r"\bpay\b",
    r"\bremuneration\b",
    r"\bpackage\b",
    r"\bbase pay\b",
    r"\bfixed pay\b",
    r"\bstipend\b",
    # Notice: notice, last working day, buyout, joining time
    r"\bnotice\b",
    r"\blast working day\b",
    r"\blwd\b",
    r"\bbuyout\b",
    r"\bjoining time\b",
    r"\bjoining date\b",
    r"\bhow soon can you join\b",
    r"\bearliest start date\b",
    r"\bstart date\b",
    r"\bavailability\b",
    # Relocation, onsite/hybrid, remote
    r"\brelocation\b",
    r"\brelocate\b",
    r"\bonsite\b",
    r"\bon-site\b",
    r"\bhybrid\b",
    r"\bremote\b",
    r"\bwork from home\b",
    r"\bwfh\b",
    # Visa, sponsorship, work authorization
    r"\bvisa\b",
    r"\bsponsorship\b",
    r"\bsponsor\b",
    r"\bwork authorization\b",
    r"\bwork auth\b",
    r"\bauthorized to work\b",
    r"\bauthorized in\b",
    r"\bauthorized\b",
    r"\bwork permit\b",
    r"\blegally authorized\b",
    r"\bright to work\b",
    r"\bcitizenship\b",
    # Shifts
    r"\bshift\b",
    r"\bshifts\b",
    r"\bnight shift\b",
    r"\brotational shift\b",
    r"\bworking hours\b",
    # Employment type
    r"\bemployment type\b",
    r"\bcontract\b",
    r"\bfull time\b",
    r"\bfull-time\b",
    r"\bpart time\b",
    r"\bpart-time\b",
    r"\bc2c\b",
    r"\bw2\b",
    r"\bfreelance\b",
    r"\binternship\b",
]

_FACT_KEYWORDS = [
    "years of experience",
    "how many years",
    "education",
    "degree",
    "current title",
    "current role",
    "where are you",
    "your location",
]


def _is_setting_question(q_norm: str) -> bool:
    return any(re.search(pattern, q_norm, re.IGNORECASE) for pattern in _SETTING_PATTERNS)


def classify_question(question: str, profile: Profile) -> str:
    """Classify a question into: setting | profile_fact | free_text.

    Defaults to 'setting' when the question mentions pay, notice, last working day,
    buyout, joining time, relocation, onsite/hybrid, visa, sponsorship, work auth,
    shifts, or employment type.
    """
    q_norm = normalize_question(question)

    # Settings keywords take absolute priority
    if _is_setting_question(q_norm):
        return "setting"

    # Profile fact keywords
    if any(k in q_norm for k in _FACT_KEYWORDS):
        return "profile_fact"

    # Specific skill mention
    for skill in profile.skills:
        skill_lower = skill.name.lower()
        norm_lower = normalize_skill(skill.name).lower()
        if (
            re.search(rf"\b{re.escape(skill_lower)}\b", q_norm)
            or re.search(rf"\b{re.escape(norm_lower)}\b", q_norm)
        ):
            return "profile_fact"

    # Keywords that suggest a skill question
    if any(
        p in q_norm
        for p in [
            "do you have", "experience with", "worked with",
            "familiar with", "hands on", "hands-on"
        ]
    ):
        return "profile_fact"

    return "free_text"


# ---------------------------------------------------------------------------
# Answerers
# ---------------------------------------------------------------------------


def answer_profile_fact(question: str, profile: Profile) -> str:
    """Answer from profile fields directly.

    Rules:
    - VERIFIED_PROFESSIONAL → "professional experience with"
    - VERIFIED_PROJECT → "personal project" (never professional)
    - LEARNING → "currently learning"
    - Per-skill years only stated when skill.years is set
    """
    q_norm = normalize_question(question)

    # Years of experience
    if "years of experience" in q_norm or "how many years" in q_norm:
        return str(profile.experience.years_for_forms)

    # Current title / role
    if "current title" in q_norm or "current role" in q_norm:
        title = ""
        if profile.targets.primary_titles:
            title = profile.targets.primary_titles[0]
        return f"{profile.experience.current_employer} - {title}".strip(" -")

    # Location
    if "location" in q_norm or "where are you" in q_norm:
        return str(profile.identity.base_location)

    # Education / degree
    if "education" in q_norm or "degree" in q_norm or "university" in q_norm:
        return "I have a relevant educational background."

    # Specific skill lookup
    for skill in profile.skills:
        skill_lower = skill.name.lower()
        norm_lower = normalize_skill(skill.name).lower()
        if (
            re.search(rf"\b{re.escape(skill_lower)}\b", q_norm)
            or re.search(rf"\b{re.escape(norm_lower)}\b", q_norm)
        ):
            years_str = (
                f" for {int(skill.years)} years"
                if skill.years is not None
                else ""
            )

            if skill.level == EvidenceLevel.VERIFIED_PROFESSIONAL:
                return f"Yes, I have professional experience with {skill.name}{years_str}."
            if skill.level == EvidenceLevel.VERIFIED_PROJECT:
                return f"I have used {skill.name} in a personal project."
            if skill.level == EvidenceLevel.VERIFIED_CERTIFICATION:
                return f"I am certified in {skill.name}."
            if skill.level == EvidenceLevel.EXPERIMENTAL:
                return f"I have explored {skill.name} in a prototype/hackathon."
            if skill.level == EvidenceLevel.LEARNING:
                return f"I am currently learning {skill.name}."
            return f"I do not have experience with {skill.name}."

    return "I do not have experience with that specific requirement."


def answer_setting(question: str, profile: Profile) -> str:
    """Answer from profile.settings.  Null values return 'NEEDS YOUR INPUT'."""
    q_norm = normalize_question(question)
    settings = profile.settings

    # Pay (salary, CTC, ECTC, expected, current compensation, pay, package, etc.)
    if any(
        w in q_norm
        for w in [
            "salary", "ctc", "ectc", "compensation", "expected", "pay",
            "package", "remuneration", "fixed pay", "base pay", "stipend"
        ]
    ):
        return (
            str(settings.salary_expectation)
            if settings.salary_expectation is not None
            else "NEEDS YOUR INPUT"
        )

    # Notice, last working day, buyout, joining time, availability
    if any(
        w in q_norm
        for w in [
            "notice", "last working day", "lwd", "buyout", "joining",
            "start date", "availability"
        ]
    ):
        return (
            f"{settings.notice_period_days} days"
            if settings.notice_period_days is not None
            else "NEEDS YOUR INPUT"
        )

    # Relocation
    if "relocation" in q_norm or "relocate" in q_norm:
        return (
            str(settings.open_to_relocation)
            if settings.open_to_relocation is not None
            else "NEEDS YOUR INPUT"
        )

    # Visa / sponsorship
    if "visa" in q_norm or "sponsorship" in q_norm or "sponsor" in q_norm:
        return (
            str(settings.visa_sponsorship_needed)
            if settings.visa_sponsorship_needed is not None
            else "NEEDS YOUR INPUT"
        )

    # Work authorization
    if (
        "work authorization" in q_norm
        or "authorized" in q_norm
        or "work permit" in q_norm
        or "right to work" in q_norm
        or "citizenship" in q_norm
    ):
        if "india" in q_norm:
            return (
                str(settings.work_authorization_india)
                if settings.work_authorization_india is not None
                else "NEEDS YOUR INPUT"
            )
        return (
            str(settings.work_authorization_other_countries)
            if settings.work_authorization_other_countries is not None
            else "NEEDS YOUR INPUT"
        )

    # Onsite
    if "onsite" in q_norm or "on-site" in q_norm:
        return (
            str(settings.onsite_ok)
            if settings.onsite_ok is not None
            else "NEEDS YOUR INPUT"
        )

    # Hybrid
    if "hybrid" in q_norm:
        return (
            str(settings.hybrid_ok)
            if settings.hybrid_ok is not None
            else "NEEDS YOUR INPUT"
        )

    # Remote
    if "remote" in q_norm or "work from home" in q_norm or "wfh" in q_norm:
        return (
            str(settings.remote_ok)
            if settings.remote_ok is not None
            else "NEEDS YOUR INPUT"
        )

    # Shifts
    if "shift" in q_norm or "working hours" in q_norm:
        return (
            str(settings.night_shifts_ok)
            if settings.night_shifts_ok is not None
            else "NEEDS YOUR INPUT"
        )

    # Employment type
    if any(
        w in q_norm
        for w in [
            "employment type", "contract", "full time", "full-time",
            "part time", "part-time", "c2c", "w2", "freelance", "internship"
        ]
    ):
        return (
            str(settings.employment_types_ok)
            if settings.employment_types_ok is not None
            else "NEEDS YOUR INPUT"
        )

    return "NEEDS YOUR INPUT"


def answer_free_text(
    question: str,
    profile: Profile,
    config: AppConfig,
    llm_client: GeminiClient | None,
    job_id: int | None = None,
    conn: sqlite3.Connection | None = None,
) -> str:
    """Generate a free-text answer using the LLM.

    Falls back to a safe generic answer if the LLM is unavailable or
    if the answer fails basic validation.
    """
    if not llm_client:
        return "I am a strong fit for this role based on my background."

    # Build prompt from verified facts only
    positioning = redact_text(profile.positioning or "")
    verified = [
        s.name
        for s in profile.skills
        if s.level
        in (
            EvidenceLevel.VERIFIED_PROFESSIONAL,
            EvidenceLevel.VERIFIED_PROJECT,
            EvidenceLevel.VERIFIED_CERTIFICATION,
        )
    ]
    skills_text = redact_text(", ".join(verified))

    prompt = (
        f"Answer this application question concisely and honestly.\n"
        f"Question: {question}\n\n"
        f"Candidate positioning: {positioning}\n"
        f"Verified skills: {skills_text}\n"
    )

    if job_id and conn:
        row = conn.execute(
            "SELECT analysis_json FROM analyses WHERE job_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (job_id,),
        ).fetchone()
        if row:
            prompt += f"\nJob context: {redact_text(row['analysis_json'])}\n"

    prompt += (
        "\nRules: Do not claim skills beyond their evidence level. "
        "Do not mention any LEARNING skills as experience. "
        "Be specific and plain."
    )

    try:
        answer = llm_client.generate_text(prompt, task="answer_question")
        # Basic validation: no learning topics in answer
        answer_lower = answer.lower()
        for topic in profile.learning.topics:
            if topic.lower() in answer_lower:
                return "I have a solid foundation to contribute effectively."
        return answer
    except Exception:
        return "I have a strong background relevant to this position."


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def mentions_company_or_job(question: str, company: str | None = None) -> bool:
    """Check if question mentions specific company or job context."""
    q_lower = question.lower()
    markers = [
        "company", "role", "position", "job", "organization", "organisation",
        "firm", "team", "us", "this role", "this job", "this company",
        "our company", "our team", "why work with us", "why join us",
        "why do you want to join", "why do you want to work",
    ]
    if any(re.search(rf"\b{re.escape(m)}\b", q_lower) for m in markers):
        return True
    return bool(company and re.search(rf"\b{re.escape(company.lower())}\b", q_lower))


def answer_question(
    question: str,
    profile: Profile,
    config: AppConfig,
    conn: sqlite3.Connection | None = None,
    llm_client: GeminiClient | None = None,
    job_id: int | None = None,
) -> AnswerResult:
    """Answer an application question.

    1. Normalise and classify the question.
    2. Setting and profile_fact answers are NEVER cached or reused; recomputed from profile each time.
    3. Only approved free_text answers are reused, and only when the question does not mention the company or job.
    4. Store in answer_bank.
    """
    q_norm = normalize_question(question)
    category = classify_question(question, profile)

    company_name = None
    if job_id and conn:
        job_row = conn.execute("SELECT company FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if job_row:
            company_name = job_row["company"]

    # Only approved free_text answers are reused, and only when question does not mention company or job
    if category == "free_text" and not mentions_company_or_job(question, company_name) and conn:
        row = conn.execute(
            "SELECT id, answer FROM answer_bank "
            "WHERE question_norm = ? AND category = 'free_text' AND approved = 1",
            (q_norm,),
        ).fetchone()
        if row:
            return AnswerResult(
                question=question,
                question_norm=q_norm,
                category=category,
                answer=row["answer"],
                is_draft=False,
                answer_bank_id=row["id"],
                from_bank=True,
            )

    # Generate answer
    if category == "profile_fact":
        ans_text = answer_profile_fact(question, profile)
        is_draft = False
    elif category == "setting":
        ans_text = answer_setting(question, profile)
        is_draft = (ans_text == "NEEDS YOUR INPUT")
    else:
        ans_text = answer_free_text(
            question, profile, config, llm_client, job_id, conn
        )
        is_draft = True

    # Store in answer bank
    ans_id = None
    if conn:
        approved_val = 0 if is_draft else 1
        now = _now_iso()

        existing = conn.execute(
            "SELECT id FROM answer_bank WHERE question_norm = ?",
            (q_norm,),
        ).fetchone()

        if existing:
            conn.execute(
                "UPDATE answer_bank "
                "SET answer = ?, category = ?, approved = ?, updated_at = ? "
                "WHERE id = ?",
                (ans_text, category, approved_val, now, existing["id"]),
            )
            ans_id = existing["id"]
        else:
            cursor = conn.execute(
                "INSERT INTO answer_bank "
                "(question_norm, category, answer, approved, source_job_id, "
                "created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (q_norm, category, ans_text, approved_val, job_id, now, now),
            )
            ans_id = cursor.lastrowid
        conn.commit()

    return AnswerResult(
        question=question,
        question_norm=q_norm,
        category=category,
        answer=ans_text,
        is_draft=is_draft,
        answer_bank_id=ans_id,
        from_bank=False,
    )


def set_answer(conn: sqlite3.Connection, answer_id: int, answer_text: str) -> bool:
    """Supply an answer for a NEEDS YOUR INPUT item in the answer bank."""
    cursor = conn.execute(
        "UPDATE answer_bank SET answer = ?, approved = 1, updated_at = ? WHERE id = ?",
        (answer_text, _now_iso(), answer_id),
    )
    conn.commit()
    return cursor.rowcount > 0


# ---------------------------------------------------------------------------
# Approval
# ---------------------------------------------------------------------------


def approve_answer(conn: sqlite3.Connection, answer_id: int) -> bool:
    """Set approved=1 for an answer bank entry. Returns True if updated."""
    cursor = conn.execute(
        "UPDATE answer_bank SET approved = 1, updated_at = ? WHERE id = ?",
        (_now_iso(), answer_id),
    )
    conn.commit()
    return cursor.rowcount > 0
