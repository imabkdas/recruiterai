"""Tests for jobpilot.profile.loader — profile and resume validation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from jobpilot.models import Profile, ResumeBase
from jobpilot.profile.loader import validate_profile_and_resume

# ---------------------------------------------------------------------------
# Helpers for building test profiles/resumes inline
# ---------------------------------------------------------------------------

def _make_profile(**overrides) -> Profile:
    """Create a minimal valid profile with optional overrides."""
    base = {
        "schema_version": 2,
        "identity": {"name": "Test", "base_location": "India"},
        "experience": {"total_years_actual": 3.0, "years_for_forms": 3},
        "settings": {},
        "targets": {},
        "dealbreakers": {},
        "positioning": "",
        "skills": [
            {"name": "Java", "category": "core_backend", "level": "VERIFIED_PROFESSIONAL", "evidence": ["b001"]},
            {"name": "React", "category": "frontend", "level": "VERIFIED_PROJECT", "evidence": ["b101"]},
        ],
        "learning": {"topics": [], "not_claimable": True},
        "known_gaps": [],
    }
    base.update(overrides)
    return Profile.model_validate(base)


def _make_resume(**overrides) -> ResumeBase:
    """Create a minimal valid resume base with optional overrides."""
    base = {
        "schema_version": 2,
        "summary_variants": [{"id": "sum1", "text": "A summary."}],
        "roles": [
            {
                "id": "r001",
                "employer": "TestCo",
                "bullets": [
                    {
                        "id": "b001",
                        "type": "professional",
                        "text": "Did Java work.",
                        "skills": ["Java"],
                        "metrics": [],
                        "tags": ["java"],
                    }
                ],
            }
        ],
        "projects": [
            {
                "id": "p001",
                "name": "Test Project",
                "type": "project",
                "bullets": [
                    {
                        "id": "b101",
                        "type": "project",
                        "text": "Built with React.",
                        "skills": ["React"],
                        "metrics": [],
                        "tags": ["frontend"],
                    }
                ],
            }
        ],
        "experiments": [],
        "achievements": [],
        "certifications": [],
    }
    base.update(overrides)
    return ResumeBase.model_validate(base)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestValidProfile:
    def test_valid_passes(self) -> None:
        result = validate_profile_and_resume(_make_profile(), _make_resume())
        assert result.ok
        assert len(result.errors) == 0

    def test_skills_per_level_info(self) -> None:
        result = validate_profile_and_resume(_make_profile(), _make_resume())
        info_text = "\n".join(result.info)
        assert "VERIFIED_PROFESSIONAL" in info_text
        assert "VERIFIED_PROJECT" in info_text


class TestInvalidEvidenceId:
    def test_missing_evidence_id(self) -> None:
        """Skill references evidence ID that doesn't exist in resume."""
        profile = _make_profile(
            skills=[
                {"name": "Java", "category": "core_backend", "level": "VERIFIED_PROFESSIONAL", "evidence": ["b999"]},
            ]
        )
        resume = _make_resume()
        result = validate_profile_and_resume(profile, resume)
        assert not result.ok
        assert any("b999" in e for e in result.errors)


class TestMissingSkillInProfile:
    def test_skill_in_bullet_not_in_profile(self) -> None:
        """A skill used in a resume bullet is not defined in the profile."""
        profile = _make_profile(
            skills=[
                {"name": "Java", "category": "core_backend", "level": "VERIFIED_PROFESSIONAL", "evidence": ["b001"]},
                # React is missing from profile but used in resume
            ]
        )
        resume = _make_resume()
        result = validate_profile_and_resume(profile, resume)
        assert not result.ok
        assert any("React" in e for e in result.errors)


class TestDuplicateIds:
    def test_duplicate_bullet_ids(self) -> None:
        resume = _make_resume()
        # Add a duplicate bullet ID
        resume.roles[0].bullets.append(
            resume.roles[0].bullets[0].model_copy()
        )
        result = validate_profile_and_resume(_make_profile(), resume)
        assert not result.ok
        assert any("Duplicate bullet ID" in e for e in result.errors)

    def test_duplicate_skill_names(self) -> None:
        profile = _make_profile(
            skills=[
                {"name": "Java", "category": "core_backend", "level": "VERIFIED_PROFESSIONAL", "evidence": ["b001"]},
                {"name": "Java", "category": "core_backend", "level": "VERIFIED_PROJECT", "evidence": ["b001"]},
                {"name": "React", "category": "frontend", "level": "VERIFIED_PROJECT", "evidence": ["b101"]},
            ]
        )
        result = validate_profile_and_resume(profile, _make_resume())
        assert not result.ok
        assert any("Duplicate skill name" in e for e in result.errors)


class TestNullSettingsAreWarnings:
    def test_null_settings_produce_warnings(self) -> None:
        """Unset (null) settings must be warnings, not errors."""
        profile = _make_profile()
        resume = _make_resume()
        result = validate_profile_and_resume(profile, resume)
        # Defaults leave many settings null
        assert result.ok  # No errors
        assert len(result.warnings) > 0
        warning_text = "\n".join(result.warnings)
        assert "salary_expectation" in warning_text
        assert "notice_period_days" in warning_text
        assert "visa_sponsorship_needed" in warning_text

    def test_set_settings_no_warnings(self) -> None:
        profile = _make_profile(
            settings={
                "work_authorization_india": True,
                "work_authorization_other_countries": "none",
                "visa_sponsorship_needed": False,
                "open_to_relocation": True,
                "onsite_ok": True,
                "hybrid_ok": True,
                "night_shifts_ok": False,
                "employment_types_ok": ["full_time"],
                "notice_period_days": 30,
                "salary_expectation": "15 LPA",
                "remote_ok": True,
                "india_cities": ["Bangalore"],
                "remote_regions_ok": ["India"],
            }
        )
        result = validate_profile_and_resume(profile, _make_resume())
        assert result.ok
        # Only structural warnings (like empty evidence) should remain
        setting_warnings = [w for w in result.warnings if "null (unset)" in w]
        assert len(setting_warnings) == 0


class TestSchemaVersionCheck:
    def test_wrong_profile_version(self) -> None:
        profile = _make_profile(schema_version=1)
        result = validate_profile_and_resume(profile, _make_resume())
        assert not result.ok
        assert any("schema_version" in e for e in result.errors)

    def test_wrong_resume_version(self) -> None:
        resume = _make_resume(schema_version=99)
        result = validate_profile_and_resume(_make_profile(), resume)
        assert not result.ok


class TestEmptyEvidenceWarning:
    def test_professional_with_no_evidence_warns(self) -> None:
        """A professional skill with no evidence IDs should warn."""
        profile = _make_profile(
            skills=[
                {"name": "Java", "category": "core_backend", "level": "VERIFIED_PROFESSIONAL", "evidence": []},
                {"name": "React", "category": "frontend", "level": "VERIFIED_PROJECT", "evidence": ["b101"]},
            ]
        )
        # Remove Java from resume bullets to avoid cross-ref error
        resume = _make_resume()
        resume.roles[0].bullets[0].skills = []  # Remove Java from bullet
        result = validate_profile_and_resume(profile, resume)
        assert any("no evidence" in w.lower() for w in result.warnings)


class TestRealProfileFiles:
    """Test against the actual data/profile.yaml and data/resume_base.yaml files."""

    def _find_data_dir(self) -> Path:
        """Find the data directory relative to the test file."""
        # Walk up to find pyproject.toml
        current = Path(__file__).parent
        for parent in [current, *current.parents]:
            if (parent / "pyproject.toml").exists():
                return parent / "data"
        pytest.skip("Could not find project root")

    def test_real_profile_loads(self) -> None:
        data_dir = self._find_data_dir()
        profile_path = data_dir / "profile.yaml"
        resume_path = data_dir / "resume_base.yaml"
        if not profile_path.exists() or not resume_path.exists():
            pytest.skip("Data files not present")

        with open(profile_path) as f:
            profile_raw = yaml.safe_load(f)
        with open(resume_path) as f:
            resume_raw = yaml.safe_load(f)

        profile = Profile.model_validate(profile_raw)
        resume = ResumeBase.model_validate(resume_raw)

        assert profile.schema_version == 2
        assert resume.schema_version == 2

    def test_real_profile_validates(self) -> None:
        data_dir = self._find_data_dir()
        profile_path = data_dir / "profile.yaml"
        resume_path = data_dir / "resume_base.yaml"
        if not profile_path.exists() or not resume_path.exists():
            pytest.skip("Data files not present")

        with open(profile_path) as f:
            profile_raw = yaml.safe_load(f)
        with open(resume_path) as f:
            resume_raw = yaml.safe_load(f)

        profile = Profile.model_validate(profile_raw)
        resume = ResumeBase.model_validate(resume_raw)

        result = validate_profile_and_resume(profile, resume)
        # Should have no errors (only warnings for null settings)
        assert result.ok, f"Validation errors: {result.errors}"
        # Should have warnings for null settings
        assert len(result.warnings) > 0
