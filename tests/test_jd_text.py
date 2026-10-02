"""Tests for readable job descriptions and the skill breakdown fallback."""

from jobpilot.models import EvidenceLevel, JobAnalysis, Profile, Skill
from jobpilot.pipeline.jd_text import clean_job_description
from jobpilot.services import _derive_skill_lists

PRENOSIS = (
    "Remote - Prenosis Inc. is an artificial intelligence company pioneering precision "
    "medicine in acute care. Our Immunixâ\x84¢ precision medicine platform drives the "
    "development of precision products and enables real-time delivery of optimal therapy. "
    "We've created and validated the fi...<br/><br/>Please mention the word **STELLARLY** and tag "
    "RMjQwNToyMDE6ODAxNjo2ODVmOmRjZTI6ZGFlNDozYmU3OmYzMjc= when applying to show you read "
    "the job post completely."
)


def test_prenosis_blurb_drops_the_spam_line_and_repairs_the_trademark() -> None:
    cleaned = clean_job_description(PRENOSIS)
    assert "STELLARLY" not in cleaned
    assert "<br" not in cleaned
    assert "Immunix™" in cleaned
    assert "short preview" in cleaned


def test_plain_description_is_left_intact() -> None:
    text = "Build Java services with Kafka."
    assert clean_job_description(text) == text


def test_empty_analysis_uses_tags_and_profile_skills() -> None:
    profile = Profile(
        schema_version=2,
        identity={"name": "Anand"},
        experience={
            "total_years_actual": 4,
            "years_for_forms": 4,
            "current_employer": "Cognizant",
        },
        skills=[
            Skill(name="Java", category="languages", level=EvidenceLevel.VERIFIED_PROFESSIONAL),
            Skill(name="Go", category="languages", level=EvidenceLevel.UNKNOWN),
        ],
        targets={"primary_titles": ["Software Engineer"]},
    )
    analysis = JobAnalysis(
        normalized_title="Software Engineer",
        required_skills=[],
        preferred_skills=[],
    )
    description = "We use Java every day. Java services on the platform."
    covered, _partial, _learning, missing = _derive_skill_lists(
        description, ["golang", "dev", "engineer"], analysis, profile
    )
    assert [item.skill for item in covered] == ["Java"]
    assert covered[0].mentions == 2
    assert "Go" in missing
    assert "dev" not in missing
    assert "engineer" not in missing
