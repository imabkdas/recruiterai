"""Tests for jobpilot.cli — CLI invocation tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from jobpilot.cli import app
from jobpilot.models import JobAnalysis

runner = CliRunner()


class TestInit:
    def test_init_creates_db(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """init should create the database and data directory."""
        monkeypatch.chdir(tmp_path)

        # Create minimal pyproject.toml so _project_root() finds tmp_path
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
        (tmp_path / ".env.example").write_text("GEMINI_API_KEY=\n")

        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0
        assert "Database initialised" in result.output
        assert (tmp_path / "data" / "jobpilot.db").exists()
        assert (tmp_path / "data" / "out").exists()

    def test_init_idempotent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Running init twice should not error."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
        (tmp_path / ".env.example").write_text("GEMINI_API_KEY=\n")

        result1 = runner.invoke(app, ["init"])
        assert result1.exit_code == 0

        result2 = runner.invoke(app, ["init"])
        assert result2.exit_code == 0

    def test_init_does_not_overwrite_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """init should not overwrite an existing .env file."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
        (tmp_path / ".env.example").write_text("GEMINI_API_KEY=\n")
        (tmp_path / ".env").write_text("GEMINI_API_KEY=secret123\n")

        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0
        assert (tmp_path / ".env").read_text() == "GEMINI_API_KEY=secret123\n"


class TestProfileValidate:
    def test_validate_with_valid_files(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """profile validate should pass on valid profile and resume files."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')

        data_dir = tmp_path / "data"
        data_dir.mkdir()

        # Minimal valid profile
        profile_content = {
            "schema_version": 2,
            "identity": {"name": "Test", "base_location": "India"},
            "experience": {"total_years_actual": 3.0, "years_for_forms": 3},
            "settings": {},
            "targets": {},
            "dealbreakers": {},
            "positioning": "",
            "skills": [
                {"name": "Java", "category": "core_backend", "level": "VERIFIED_PROFESSIONAL", "evidence": ["b001"]},
            ],
            "learning": {"topics": [], "not_claimable": True},
            "known_gaps": [],
        }

        resume_content = {
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
            "projects": [],
            "experiments": [],
            "achievements": [],
            "certifications": [],
        }

        import yaml
        (data_dir / "profile.yaml").write_text(yaml.dump(profile_content))
        (data_dir / "resume_base.yaml").write_text(yaml.dump(resume_content))

        result = runner.invoke(app, ["profile", "validate"])
        assert result.exit_code == 0
        assert "passed" in result.output.lower()

    def test_validate_missing_files(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """profile validate should fail gracefully if files are missing."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')

        result = runner.invoke(app, ["profile", "validate"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()


class TestMark:
    def test_mark_invalid_status(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')

        result = runner.invoke(app, ["mark", "1", "bogus_status"])
        assert result.exit_code == 1
        assert "Invalid status" in result.output

    def test_mark_no_db(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')

        result = runner.invoke(app, ["mark", "1", "applied"])
        assert result.exit_code == 1
        assert "Database not found" in result.output


class TestBoardsAndDiscovery:
    def test_discover_no_db(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
        result = runner.invoke(app, ["discover"])
        assert result.exit_code == 1
        assert "Database not found" in result.output

    def test_boards_list_no_db(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
        result = runner.invoke(app, ["boards", "list"])
        assert result.exit_code == 1
        assert "Database not found" in result.output

    def test_boards_disable_no_db(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
        result = runner.invoke(app, ["boards", "disable", "greenhouse", "stripe"])
        assert result.exit_code == 1
        assert "Database not found" in result.output

    def test_discover_and_boards_commands(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
        (tmp_path / ".env.example").write_text("GEMINI_API_KEY=\n")
        runner.invoke(app, ["init"])

        # Insert a job with a Greenhouse URL
        from jobpilot import db
        conn = db.get_connection(tmp_path / "data" / "jobpilot.db")
        db.upsert_job(conn, {
            "fingerprint": "test_fp",
            "source": "manual",
            "company": "Stripe",
            "title": "Software Engineer",
            "url": "https://boards.greenhouse.io/stripe/jobs/123",
        })
        conn.close()

        # Run discover
        disc_result = runner.invoke(app, ["discover"])
        assert disc_result.exit_code == 0
        assert "stripe" in disc_result.output

        # Run boards list
        list_result = runner.invoke(app, ["boards", "list"])
        assert list_result.exit_code == 0
        assert "stripe" in list_result.output
        assert "yes" in list_result.output

        # Disable board
        dis_result = runner.invoke(app, ["boards", "disable", "greenhouse", "stripe"])
        assert dis_result.exit_code == 0
        assert "Disabled greenhouse board: 'stripe'" in dis_result.output

        # List should now show 0 active (or inactive with --all)
        list_after = runner.invoke(app, ["boards", "list"])
        assert "No boards tracked yet" in list_after.output or "0" in list_after.output

        list_all = runner.invoke(app, ["boards", "list", "--all"])
        assert "stripe" in list_all.output
        assert "no" in list_all.output


class TestFetchCLI:
    def test_fetch_no_db(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
        result = runner.invoke(app, ["fetch"])
        assert result.exit_code == 1
        assert "Database not found" in result.output

    def test_fetch_specific_source(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import respx
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
        (tmp_path / ".env.example").write_text("GEMINI_API_KEY=\n")
        runner.invoke(app, ["init"])

        with respx.mock:
            respx.get("https://remoteok.com/api").respond(status_code=200, json=[])
            result = runner.invoke(app, ["fetch", "--source", "remoteok"])
            assert result.exit_code == 0
            assert "Fetch Funnel Summary" in result.output


class TestManualAndAlertsCLI:
    def test_add_url_cli(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
        (tmp_path / ".env.example").write_text("GEMINI_API_KEY=\n")
        runner.invoke(app, ["init"])

        res = runner.invoke(app, ["add-url", "https://example.com/jobs/1", "--title", "Software Engineer", "--company", "Acme"])
        assert res.exit_code == 0
        assert "Stored URL as Job 1" in res.output

    def test_add_jd_cli(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
        (tmp_path / ".env.example").write_text("GEMINI_API_KEY=\n")
        runner.invoke(app, ["init"])

        # Add URL first
        runner.invoke(app, ["add-url", "https://example.com/jobs/1", "--title", "Software Engineer", "--company", "Acme"])

        # Add JD text via file
        jd_file = tmp_path / "jd.txt"
        jd_file.write_text("Looking for an enterprise Java developer with Spring Boot experience.", encoding="utf-8")
        res = runner.invoke(app, ["add-jd", "1", "--file", str(jd_file)])
        assert res.exit_code == 0
        assert "Job 1 updated with JD text → status: new" in res.output


class TestPhase3CLI:
    def test_analyze_cli(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
        (tmp_path / ".env.example").write_text("GEMINI_API_KEY=\n")
        runner.invoke(app, ["init"])

        # Add URL and JD
        runner.invoke(app, ["add-url", "https://example.com/jobs/1", "--title", "Backend Engineer", "--company", "Acme"])
        jd_file = tmp_path / "jd.txt"
        jd_file.write_text("Need Java and Spring Boot engineer.", encoding="utf-8")
        runner.invoke(app, ["add-jd", "1", "--file", str(jd_file)])

        analysis = JobAnalysis(
            normalized_title="Backend Engineer",
            required_skills=["Java", "Spring Boot"],
            preferred_skills=[],
        )

        with patch("jobpilot.llm.client.GeminiClient.generate_json", return_value=analysis):
            res = runner.invoke(app, ["analyze"])
            assert res.exit_code == 0
            assert "LLM Job Analysis Summary" in res.output
            assert "Successfully analyzed: 1" in res.output

    def test_skills_unmatched_cli(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
        (tmp_path / ".env.example").write_text("GEMINI_API_KEY=\n")
        runner.invoke(app, ["init"])

        res = runner.invoke(app, ["skills", "--unmatched"])
        assert res.exit_code == 0

    def test_llm_check_no_key_fails(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
        (tmp_path / ".env.example").write_text("GEMINI_API_KEY=\n")
        runner.invoke(app, ["init"])
        # Ensure .env has empty key
        (tmp_path / ".env").write_text("GEMINI_API_KEY=\n")

        res = runner.invoke(app, ["llm-check"])
        assert res.exit_code == 1
        assert "GEMINI_API_KEY is not configured" in res.output

    def test_llm_check_mock_success(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
        (tmp_path / ".env.example").write_text("GEMINI_API_KEY=mock-key\n")
        runner.invoke(app, ["init"])
        (tmp_path / ".env").write_text("GEMINI_API_KEY=mock-key\n")

        mock_response = MagicMock()
        mock_response.text = "OK"
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        with patch("google.genai.Client", return_value=mock_client):
            res = runner.invoke(app, ["llm-check"])
            assert res.exit_code == 0
            assert "Gemini API check succeeded" in res.output

    def test_score_cli(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import shutil
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
        (tmp_path / ".env.example").write_text("GEMINI_API_KEY=\n")
        runner.invoke(app, ["init"])

        # Copy data/profile.yaml from workspace into tmp_path/data/
        proj_root = Path(__file__).resolve().parents[1]
        shutil.copy(proj_root / "data" / "profile.yaml", tmp_path / "data" / "profile.yaml")

        res = runner.invoke(app, ["score"])
        assert res.exit_code == 0
        assert "Deterministic Scoring Summary" in res.output

    def test_queue_cli(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
        (tmp_path / ".env.example").write_text("GEMINI_API_KEY=\n")
        runner.invoke(app, ["init"])

        res = runner.invoke(app, ["queue"])
        assert res.exit_code == 0
        assert "Daily Ranked Queue" in res.output

    def test_stats_cli(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
        (tmp_path / ".env.example").write_text("GEMINI_API_KEY=\n")
        runner.invoke(app, ["init"])

        res = runner.invoke(app, ["stats", "--weeks", "2"])
        assert res.exit_code == 0
        assert "Pipeline Funnel Summary" in res.output
        assert "Weekly Applications" in res.output

    def test_mark_cli_valid_and_invalid(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import sqlite3
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
        (tmp_path / ".env.example").write_text("GEMINI_API_KEY=\n")
        runner.invoke(app, ["init"])

        db_path = tmp_path / "data" / "jobpilot.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO jobs (id, fingerprint, source, company, title, url, discovered_at, last_seen_at, status) "
            "VALUES (101, 'fp-101', 'greenhouse', 'MarkCo', 'Engineer', 'https://example.com', '2026-10-01', '2026-10-01', 'scored')"
        )
        conn.commit()
        conn.close()

        # Valid transition: scored -> applied
        res1 = runner.invoke(app, ["mark", "101", "applied", "--channel", "linkedin", "--note", "applied via portal"])
        assert res1.exit_code == 0
        assert "Job 101 → applied (channel: linkedin)" in res1.output

        # Invalid transition: applied -> queued (not allowed)
        res2 = runner.invoke(app, ["mark", "101", "queued"])
        assert res2.exit_code == 1
        assert "Cannot transition job 101" in res2.output


class TestPrepareCLI:
    def test_prepare_cli_no_args_fails(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
        (tmp_path / ".env.example").write_text("GEMINI_API_KEY=\n")
        runner.invoke(app, ["init"])

        res = runner.invoke(app, ["prepare"])
        assert res.exit_code == 1
        assert "Provide a job ID or use --auto-top" in res.output

    def test_prepare_cli_job_id(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import shutil
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
        (tmp_path / ".env.example").write_text("GEMINI_API_KEY=\n")
        runner.invoke(app, ["init"])

        proj_root = Path(__file__).resolve().parents[1]
        shutil.copy(proj_root / "data" / "profile.yaml", tmp_path / "data" / "profile.yaml")
        shutil.copy(proj_root / "data" / "resume_base.yaml", tmp_path / "data" / "resume_base.yaml")

        with patch("jobpilot.cli.run_prepare") as mock_prep:
            from jobpilot.pipeline.prepare import PrepareResult
            mock_prep.return_value = [
                PrepareResult(job_id=42, tier="A", summary="Tailored summary.", note="Note text.", bullet_ids=["b1"])
            ]
            res = runner.invoke(app, ["prepare", "42"])
            assert res.exit_code == 0
            assert "Job 42 [Tier A] prepared" in res.output


class TestAnswerCLI:
    def test_answer_cli_setting(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import shutil
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
        (tmp_path / ".env.example").write_text("GEMINI_API_KEY=\n")
        runner.invoke(app, ["init"])

        proj_root = Path(__file__).resolve().parents[1]
        shutil.copy(proj_root / "data" / "profile.yaml", tmp_path / "data" / "profile.yaml")

        res = runner.invoke(app, ["answer", "What is your expected salary?"])
        assert res.exit_code == 0
        assert "Category: setting" in res.output
        assert "NEEDS YOUR INPUT" in res.output

    def test_answer_cli_approve(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import sqlite3
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
        (tmp_path / ".env.example").write_text("GEMINI_API_KEY=\n")
        runner.invoke(app, ["init"])

        db_path = tmp_path / "data" / "jobpilot.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO answer_bank (id, question_norm, category, answer, approved, created_at, updated_at) "
            "VALUES (99, 'test question', 'free_text', 'Draft answer', 0, '2026-10-01', '2026-10-01')"
        )
        conn.commit()
        conn.close()

        res = runner.invoke(app, ["answer", "--approve", "99"])
        assert res.exit_code == 0
        assert "Answer #99 approved" in res.output

    def test_answer_cli_set(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import sqlite3
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
        (tmp_path / ".env.example").write_text("GEMINI_API_KEY=\n")
        runner.invoke(app, ["init"])

        db_path = tmp_path / "data" / "jobpilot.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO answer_bank (id, question_norm, category, answer, approved, created_at, updated_at) "
            "VALUES (105, 'salary expectation', 'setting', 'NEEDS YOUR INPUT', 0, '2026-10-01', '2026-10-01')"
        )
        conn.commit()
        conn.close()

        res = runner.invoke(app, ["answer", "--set", "105", "25 LPA, 30 days notice"])
        assert res.exit_code == 0
        assert "Answer #105 updated and marked as approved" in res.output

        conn = sqlite3.connect(str(db_path))
        row = conn.execute("SELECT answer, approved FROM answer_bank WHERE id = 105").fetchone()
        conn.close()
        assert row[0] == "25 LPA, 30 days notice"
        assert row[1] == 1
