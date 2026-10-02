"""Comprehensive tests for JobPilot FastAPI application (jobpilot.api)."""

from __future__ import annotations

import json
import shutil
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient
from typer.testing import CliRunner

from jobpilot import db, services
from jobpilot.api.app import create_app
from jobpilot.api.run_state import RunStateManager
from jobpilot.cli import app as cli_app
from jobpilot.config import AppConfig, UIConfig
from jobpilot.models import JobAnalysis
from jobpilot.pipeline.prepare import PrepareResult

runner = CliRunner()


@pytest.fixture
def test_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Set up an isolated test environment with SQLite DB and sample profile."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "jobpilot.db"
    conn = db.get_connection(db_path)
    db.init_schema(conn)
    conn.close()

    real_root = Path(__file__).resolve().parent.parent
    if (real_root / "data" / "profile.yaml").exists():
        shutil.copy(real_root / "data" / "profile.yaml", data_dir / "profile.yaml")
    if (real_root / "data" / "resume_base.yaml").exists():
        shutil.copy(real_root / "data" / "resume_base.yaml", data_dir / "resume_base.yaml")

    monkeypatch.setattr(services, "_find_project_root", lambda: tmp_path)
    import jobpilot.profile.loader

    monkeypatch.setattr(jobpilot.profile.loader, "_find_project_root", lambda: tmp_path)

    return tmp_path, db_path


@pytest.fixture
def sample_data(test_env: tuple[Path, Path]):
    """Insert sample jobs, analysis, score, application, and answer bank rows."""
    _, db_path = test_env
    conn = db.get_connection(db_path)

    # 1. Job 1: queued with analysis & score
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, source, company, title, url, description, snippet, status, discovered_at, last_seen_at) "
        "VALUES (1, 'fp1', 'test_src', 'Acme Corp', 'Python Backend Lead', 'https://example.com/1', 'Full JD text here', 'Snippet text', 'queued', ?, ?)",
        (datetime.now(UTC).isoformat(), datetime.now(UTC).isoformat()),
    )
    analysis = JobAnalysis(
        normalized_title="python backend lead",
        seniority="senior",
        required_skills=["Python", "FastAPI"],
        preferred_skills=["Docker"],
    )
    conn.execute(
        "INSERT INTO analyses (job_id, prompt_version, model, content_hash, analysis_json, created_at) "
        "VALUES (1, 'v1', 'gemini-test', 'hash1', ?, ?)",
        (analysis.model_dump_json(), datetime.now(UTC).isoformat()),
    )
    matched = [{"skill": "Python", "level": "VERIFIED_PROFESSIONAL", "evidence_ids": ["b1"]}]
    conn.execute(
        "INSERT INTO scores (job_id, total, tier, skills_score, experience_score, seniority_score, location_score, extras_score, matched_skills, missing_required, missing_preferred, flags, scored_at) "
        "VALUES (1, 8.5, 'A', 4.0, 2.0, 1.0, 1.0, 0.5, ?, '[]', '[]', '[]', ?)",
        (json.dumps(matched), datetime.now(UTC).isoformat()),
    )
    conn.execute(
        "INSERT INTO applications (job_id, tailored_summary, short_note, outreach_draft, bullet_ids, prepared_at) "
        "VALUES (1, 'Tailored summary for Acme', 'Short note for Acme', 'Outreach message', '[\"b1\"]', ?)",
        (datetime.now(UTC).isoformat(),),
    )

    # 2. Job 2: needs_jd
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, source, company, title, url, snippet, status, discovered_at, last_seen_at) "
        "VALUES (2, 'fp2', 'test_src', 'Beta Inc', 'Full Stack Dev', 'https://example.com/2', 'Short snippet', 'needs_jd', ?, ?)",
        (datetime.now(UTC).isoformat(), datetime.now(UTC).isoformat()),
    )

    # 3. Job 3: applied (for follow-ups & progress)
    applied_cutoff = (datetime.now(UTC) - timedelta(days=10)).isoformat()
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, source, company, title, url, status, discovered_at, last_seen_at) "
        "VALUES (3, 'fp3', 'test_src', 'Gamma Co', 'Data Engineer', 'https://example.com/3', 'applied', ?, ?)",
        (applied_cutoff, applied_cutoff),
    )
    conn.execute(
        "INSERT INTO applications (job_id, applied_at, channel, notes) "
        "VALUES (3, ?, 'LinkedIn', 'Applied directly')",
        (applied_cutoff,),
    )

    # 4. Answer Bank item
    conn.execute(
        "INSERT INTO answer_bank (id, question_norm, category, answer, approved, created_at, updated_at) "
        "VALUES (1, 'what is your notice period', 'setting', 'NEEDS YOUR INPUT', 0, ?, ?)",
        (datetime.now(UTC).isoformat(), datetime.now(UTC).isoformat()),
    )

    conn.commit()
    conn.close()
    return db_path


TEST_TOKEN = "test-secret-token-1234567890abcdef"


@pytest.fixture
def app_prod(test_env: tuple[Path, Path]):
    """Create production mode FastAPI app."""
    cfg = AppConfig(ui=UIConfig(port=8765))
    return create_app(config=cfg, dev=False, token=TEST_TOKEN)


@pytest.fixture
def client_prod(app_prod):
    """Test client for production mode app."""
    return TestClient(app_prod, base_url="http://127.0.0.1")


@pytest.fixture
def app_dev(test_env: tuple[Path, Path]):
    """Create dev mode FastAPI app."""
    cfg = AppConfig(ui=UIConfig(port=8765))
    return create_app(config=cfg, dev=True, token=TEST_TOKEN)


@pytest.fixture
def client_dev(app_dev):
    """Test client for dev mode app."""
    return TestClient(app_dev, base_url="http://127.0.0.1")


AUTH_HEADERS = {"X-JobPilot-Token": TEST_TOKEN}


# ---------------------------------------------------------------------------
# Requirement 3: Every Endpoint Happy Path
# ---------------------------------------------------------------------------


class TestEndpointsHappyPath:
    def test_get_health(self, client_prod):
        res = client_prod.get("/api/health")
        assert res.status_code == 200
        assert res.json() == {"status": "ok"}

    def test_get_progress(self, client_prod, sample_data):
        res = client_prod.get("/api/progress")
        assert res.status_code == 200
        data = res.json()
        assert "applied_today" in data
        assert "daily_size" in data
        assert "applied_this_week" in data
        assert "weekly_target" in data
        assert "follow_ups_due_count" in data
        assert data["follow_ups_due_count"] >= 1

    def test_get_queue(self, client_prod, sample_data):
        res = client_prod.get("/api/queue?size=5")
        assert res.status_code == 200
        data = res.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        item = data[0]
        assert item["job_id"] == 1
        assert item["company"] == "Acme Corp"
        assert item["tier"] == "A"

    def test_get_needs_jd(self, client_prod, sample_data):
        res = client_prod.get("/api/needs-jd")
        assert res.status_code == 200
        data = res.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        assert any(item["job_id"] == 2 for item in data)

    def test_get_job_detail(self, client_prod, sample_data):
        res = client_prod.get("/api/jobs/1")
        assert res.status_code == 200
        data = res.json()
        assert data["id"] == 1
        assert data["company"] == "Acme Corp"
        assert data["title"] == "Python Backend Lead"
        assert data["analysis"] is not None
        assert data["score"] is not None
        assert data["score"]["tier"] == "A"
        assert data["application"] is not None
        assert data["application"]["tailored_summary"] == "Tailored summary for Acme"

    def test_prepare_job(self, client_prod, sample_data, monkeypatch: pytest.MonkeyPatch):
        mock_result = PrepareResult(
            job_id=1,
            tier="A",
            summary="Experienced engineer with strong Python background.",
            note="Excited about the role.",
            bullet_ids=["b1"],
            outreach_draft="Hi team, I applied...",
        )
        monkeypatch.setattr(services, "prepare_job", lambda **kw: mock_result)

        res = client_prod.post("/api/jobs/1/prepare", headers=AUTH_HEADERS)
        assert res.status_code == 200
        data = res.json()
        assert data["bullet_ids"] == ["b1"]
        assert data["summary"] == "Experienced engineer with strong Python background."

    def test_resume_source_compile_and_download(
        self, client_prod, sample_data, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        template_dir = tmp_path / "templates"
        template_dir.mkdir()
        template = template_dir / "createResume.tex"
        template.write_text(
            "\\documentclass{article}\n\\begin{document}Hello\\end{document}\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("jobpilot.pipeline.resume._LATEX_TEMPLATE", template)

        def fake_compile(tex_path: Path) -> None:
            tex_path.with_suffix(".pdf").write_bytes(b"%PDF-1.4\n")

        monkeypatch.setattr("jobpilot.pipeline.resume._compile_latex", fake_compile)

        source_res = client_prod.get("/api/jobs/1/resume/source")
        assert source_res.status_code == 200
        body = source_res.json()
        assert "\\documentclass" in body["source"]
        assert body["pdf_url"] is None

        edited = body["source"] + "% edited\n"
        compiled = client_prod.post(
            "/api/jobs/1/resume/compile",
            json={"source": edited},
            headers=AUTH_HEADERS,
        )
        assert compiled.status_code == 200
        pdf_url = compiled.json()["pdf_url"]
        assert pdf_url == "/api/jobs/1/resume.pdf"

        pdf = client_prod.get(pdf_url)
        assert pdf.status_code == 200
        assert pdf.content.startswith(b"%PDF")
        assert "inline" in pdf.headers["content-disposition"]

        attachment = client_prod.get(f"{pdf_url}?download=1")
        assert attachment.status_code == 200
        assert "attachment" in attachment.headers["content-disposition"]

        again = client_prod.get("/api/jobs/1/resume/source")
        assert "% edited" in again.json()["source"]
        assert again.json()["pdf_url"] == pdf_url

    def test_sidebar_resume_does_not_use_a_job(
        self, client_prod, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        template = tmp_path / "createResume.tex"
        template.write_text(
            "\\documentclass{article}\n\\begin{document}Base resume\\end{document}\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("jobpilot.pipeline.resume._LATEX_TEMPLATE", template)

        def fake_compile(tex_path: Path) -> None:
            tex_path.with_suffix(".pdf").write_bytes(b"%PDF-1.4\n")

        monkeypatch.setattr("jobpilot.pipeline.resume._compile_latex", fake_compile)

        source_res = client_prod.get("/api/resume/source")
        assert source_res.status_code == 200
        assert "Base resume" in source_res.json()["source"]

        compiled = client_prod.post(
            "/api/resume/compile",
            json={"source": template.read_text(encoding="utf-8")},
            headers=AUTH_HEADERS,
        )
        assert compiled.status_code == 200
        assert compiled.json()["pdf_url"] == "/api/resume.pdf"
        pdf = client_prod.get("/api/resume.pdf")
        assert pdf.status_code == 200
        assert pdf.content.startswith(b"%PDF")

    def test_resume_compile_reports_latex_errors(
        self, client_prod, sample_data, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        template = tmp_path / "createResume.tex"
        template.write_text("\\documentclass{article}\n", encoding="utf-8")
        monkeypatch.setattr("jobpilot.pipeline.resume._LATEX_TEMPLATE", template)

        def fail_compile(tex_path: Path) -> None:
            raise RuntimeError("! LaTeX Error: Undefined control sequence.\nl.3 \\bad")

        monkeypatch.setattr("jobpilot.pipeline.resume._compile_latex", fail_compile)
        res = client_prod.post(
            "/api/jobs/1/resume/compile",
            json={"source": "\\documentclass{article}\n\\begin{document}\\bad\\end{document}\n"},
            headers=AUTH_HEADERS,
        )
        assert res.status_code == 422
        assert "Undefined control sequence" in res.json()["detail"]

    def test_mark_job(self, client_prod, sample_data):
        # Transition Job 1 (queued) to applied
        payload = {
            "status": "applied",
            "channel": "Company Website",
            "note": "Applied through portal",
            "referral_contact": "John Doe",
        }
        res = client_prod.post("/api/jobs/1/mark", json=payload, headers=AUTH_HEADERS)
        assert res.status_code == 200
        data = res.json()
        assert data["job_id"] == 1
        assert data["previous_status"] == "queued"
        assert data["new_status"] == "applied"
        assert data["channel"] == "Company Website"
        assert data["referral_contact"] == "John Doe"

    def test_update_application_fields(self, client_prod, sample_data):
        listed = client_prod.get("/api/applications")
        queued = next(item for item in listed.json() if item["job_id"] == 1)
        assert queued["status"] == "new"

        updated = client_prod.patch(
            "/api/jobs/1/application",
            json={
                "status": "applied",
                "notes": "Sent the resume",
                "applied_at": "2026-10-02",
                "url": "https://jobs.example.com/acme",
            },
            headers=AUTH_HEADERS,
        )
        assert updated.status_code == 200
        body = updated.json()
        assert body["status"] == "applied"
        assert body["notes"] == "Sent the resume"
        assert body["applied_at"] == "2026-10-02"
        assert body["url"] == "https://jobs.example.com/acme"

        job = client_prod.get("/api/jobs/1")
        assert job.json()["url"] == "https://jobs.example.com/acme"
        assert job.json()["apply_url"] == "https://jobs.example.com/acme"

    def test_skip_job(self, client_prod, sample_data):
        res = client_prod.post("/api/jobs/2/skip", headers=AUTH_HEADERS)
        assert res.status_code == 200
        data = res.json()
        assert data["job_id"] == 2
        assert data["new_status"] == "skipped"

    def test_add_jd(self, client_prod, sample_data):
        payload = {"text": "We are seeking a Full Stack Developer with React and Python skills."}
        res = client_prod.post("/api/jobs/2/jd", json=payload, headers=AUTH_HEADERS)
        assert res.status_code == 200
        data = res.json()
        assert data["job_id"] == 2
        assert data["status"] == "new"
        assert data["char_count"] == len(payload["text"])

    def test_get_applications(self, client_prod, sample_data):
        res = client_prod.get("/api/applications?status=applied")
        assert res.status_code == 200
        data = res.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        assert any(app["job_id"] == 3 for app in data)

    def test_list_jobs_includes_postings_without_a_description(self, client_prod, sample_data):
        listed = client_prod.get("/api/jobs")
        assert listed.status_code == 200
        by_id = {item["job_id"]: item for item in listed.json()}
        assert set(by_id) == {1, 2, 3}
        assert by_id[2]["status"] == "needs_jd"
        assert by_id[2]["title"] == "Full Stack Dev"

        needs = client_prod.get("/api/jobs", params={"status": "needs_jd"})
        assert [item["job_id"] for item in needs.json()] == [2]

        apps = client_prod.get("/api/applications")
        app_by_id = {item["job_id"]: item for item in apps.json()}
        assert set(app_by_id) == {1, 2, 3}
        assert app_by_id[1]["has_description"] is True
        assert app_by_id[2]["has_description"] is False
        assert app_by_id[2]["status"] == "new"

    def test_get_follow_ups(self, client_prod, sample_data):
        res = client_prod.get("/api/follow-ups?days=7")
        assert res.status_code == 200
        data = res.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        assert data[0]["job_id"] == 3
        assert data[0]["days_since_applied"] >= 7

    def test_ask_question(self, client_prod, sample_data):
        payload = {"question": "Are you legally authorized to work in India?", "job_id": 1}
        res = client_prod.post("/api/answers/ask", json=payload, headers=AUTH_HEADERS)
        assert res.status_code == 200
        data = res.json()
        assert "question" in data
        assert "answer" in data
        assert data["category"] == "setting"
        assert data["answer"] in ("Yes", "NEEDS YOUR INPUT")

    def test_list_answers(self, client_prod, sample_data):
        res = client_prod.get("/api/answers")
        assert res.status_code == 200
        data = res.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        assert data[0]["id"] == 1
        assert data[0]["category"] == "setting"

    def test_update_answer(self, client_prod, sample_data):
        payload = {"answer_text": "2 months notice period"}
        res = client_prod.put("/api/answers/1", json=payload, headers=AUTH_HEADERS)
        assert res.status_code == 200
        assert res.json() == {"success": True}

        # Verify updated in answer bank
        res2 = client_prod.get("/api/answers")
        items = res2.json()
        matched = next(it for it in items if it["id"] == 1)
        assert matched["answer"] == "2 months notice period"
        assert matched["approved"] is True

    def test_approve_answer(self, client_prod, sample_data):
        res = client_prod.post("/api/answers/1/approve", headers=AUTH_HEADERS)
        assert res.status_code == 200
        assert res.json() == {"success": True}

    def test_get_stats(self, client_prod, sample_data):
        res = client_prod.get("/api/stats?weeks=4")
        assert res.status_code == 200
        data = res.json()
        assert "funnel" in data
        assert "applications" in data

    def test_get_profile_report(self, client_prod, sample_data):
        res = client_prod.get("/api/profile-report")
        assert res.status_code == 200
        data = res.json()
        assert "validation_errors" in data
        assert "validation_warnings" in data
        assert "unset_settings" in data
        assert "unmatched_skills" in data

    def test_post_run_and_status(self, test_env: tuple[Path, Path]):
        def dummy_run_daily(progress_callback=None, config=None, notify=True):
            if progress_callback:
                progress_callback("fetch", "fetching")
            time.sleep(0.05)
            return services.RunSummary(
                stages=[
                    services.StageSummary(name="fetch", status="ok", summary=5),
                    services.StageSummary(name="analyze", status="ok", summary=3),
                ],
                new_jobs=5,
            )

        manager = RunStateManager(run_fn=dummy_run_daily)
        app = create_app(dev=False, token=TEST_TOKEN, run_state_manager=manager)
        client = TestClient(app, base_url="http://127.0.0.1")

        # Initial status
        st0 = client.get("/api/run/status")
        assert st0.status_code == 200
        assert st0.json()["state"] == "idle"

        # Trigger run
        res = client.post("/api/run", headers=AUTH_HEADERS)
        assert res.status_code == 200
        data = res.json()
        assert data["state"] == "running"

        # Wait briefly for worker thread to finish
        time.sleep(0.15)

        st1 = client.get("/api/run/status")
        assert st1.status_code == 200
        st1_data = st1.json()
        assert st1_data["state"] == "finished"
        assert len(st1_data["stages"]) >= 2


# ---------------------------------------------------------------------------
# Requirement 5: Error Handling Mapping
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_404_nonexistent_job(self, client_prod, sample_data):
        res = client_prod.get("/api/jobs/99999")
        assert res.status_code == 404
        assert res.json()["code"] == "not_found"

    def test_404_nonexistent_answer_update(self, client_prod, sample_data):
        res = client_prod.put("/api/answers/99999", json={"answer_text": "foo"}, headers=AUTH_HEADERS)
        assert res.status_code == 404
        assert res.json()["code"] == "not_found"

    def test_404_nonexistent_answer_approve(self, client_prod, sample_data):
        res = client_prod.post("/api/answers/99999/approve", headers=AUTH_HEADERS)
        assert res.status_code == 404
        assert res.json()["code"] == "not_found"

    def test_409_invalid_status_transition(self, client_prod, sample_data):
        # Job 2 is skipped in this test
        client_prod.post("/api/jobs/2/skip", headers=AUTH_HEADERS)
        # Attempt illegal transition: skipped -> applied
        res = client_prod.post(
            "/api/jobs/2/mark",
            json={"status": "applied"},
            headers=AUTH_HEADERS,
        )
        assert res.status_code == 409
        data = res.json()
        assert data["code"] == "invalid_status_transition"
        assert "Cannot transition" in data["detail"]

    def test_409_concurrent_run(self, test_env: tuple[Path, Path]):
        evt_hold = threading.Event()

        def slow_run(progress_callback=None, config=None, notify=True):
            evt_hold.wait(timeout=2.0)
            return services.RunSummary()

        manager = RunStateManager(run_fn=slow_run)
        app = create_app(dev=False, token=TEST_TOKEN, run_state_manager=manager)
        client = TestClient(app, base_url="http://127.0.0.1")

        try:
            res1 = client.post("/api/run", headers=AUTH_HEADERS)
            assert res1.status_code == 200

            # Second POST /api/run while first is active must return 409
            res2 = client.post("/api/run", headers=AUTH_HEADERS)
            assert res2.status_code == 409
            data2 = res2.json()
            assert data2["code"] == "run_in_progress"
            assert "already in progress" in data2["detail"]
        finally:
            evt_hold.set()
            time.sleep(0.05)

    def test_run_failure_transitions_to_failed(self, test_env: tuple[Path, Path]):
        def failing_run(progress_callback=None, config=None, notify=True):
            raise RuntimeError("Database connection crashed")

        manager = RunStateManager(run_fn=failing_run)
        app = create_app(dev=False, token=TEST_TOKEN, run_state_manager=manager)
        client = TestClient(app, base_url="http://127.0.0.1")

        res = client.post("/api/run", headers=AUTH_HEADERS)
        assert res.status_code == 200
        time.sleep(0.1)

        status_res = client.get("/api/run/status")
        assert status_res.status_code == 200
        data = status_res.json()
        assert data["state"] == "failed"
        assert "Database connection crashed" in data["error"]

    def test_run_status_serializes_pipeline_summaries(self, test_env: tuple[Path, Path]):
        from jobpilot.pipeline.fetcher import FetchSummary
        from jobpilot.sources.gmail_alerts import AlertIngestSummary

        fs = FetchSummary()
        fs.sources_run.append("lever:stripe")
        fs.new_jobs = 42

        ais = AlertIngestSummary()
        ais.messages_processed = 10

        def custom_run(progress_callback=None, config=None, notify=True):
            return services.RunSummary(
                stages=[
                    services.StageSummary(name="fetch", status="ok", summary=fs),
                    services.StageSummary(name="ingest-alerts", status="ok", summary=ais),
                ],
                new_jobs=42,
            )

        manager = RunStateManager(run_fn=custom_run)
        app = create_app(dev=False, token=TEST_TOKEN, run_state_manager=manager)
        client = TestClient(app, base_url="http://127.0.0.1")

        res = client.post("/api/run", headers=AUTH_HEADERS)
        assert res.status_code == 200
        time.sleep(0.1)

        status_res = client.get("/api/run/status")
        assert status_res.status_code == 200
        data = status_res.json()
        assert data["state"] == "finished"
        assert data["summary"]["new_jobs"] == 42
        assert data["stages"][0]["counts"]["new_jobs"] == 42
        assert data["stages"][0]["counts"]["sources_run"] == ["lever:stripe"]
        assert data["stages"][1]["counts"]["messages_processed"] == 10


    def test_422_invalid_body(self, client_prod, sample_data):
        # Missing required field 'status'
        res = client_prod.post("/api/jobs/1/mark", json={"channel": "web"}, headers=AUTH_HEADERS)
        assert res.status_code == 422
        assert res.json()["code"] == "validation_error"

    def test_422_jd_text_too_long(self, client_prod, sample_data):
        huge_text = "x" * 200001
        res = client_prod.post("/api/jobs/1/jd", json={"text": huge_text}, headers=AUTH_HEADERS)
        assert res.status_code == 422
        assert res.json()["code"] == "validation_error"


# ---------------------------------------------------------------------------
# Requirement 6: Security and Privacy
# ---------------------------------------------------------------------------


class TestSecurityAndPrivacy:
    def test_403_missing_token_on_mutating_requests(self, client_prod, sample_data):
        # POST without token
        res_post = client_prod.post("/api/jobs/1/skip")
        assert res_post.status_code == 403
        assert res_post.json()["code"] == "forbidden"

        # PUT without token
        res_put = client_prod.put("/api/answers/1", json={"answer_text": "hello"})
        assert res_put.status_code == 403
        assert res_put.json()["code"] == "forbidden"

    def test_403_invalid_token(self, client_prod, sample_data):
        res = client_prod.post(
            "/api/jobs/1/skip",
            headers={"X-JobPilot-Token": "wrong-token-abc"},
        )
        assert res.status_code == 403
        assert res.json()["code"] == "forbidden"

    def test_200_valid_token_allowed(self, client_prod, sample_data):
        res = client_prod.post("/api/jobs/2/skip", headers=AUTH_HEADERS)
        assert res.status_code == 200
        assert res.json()["new_status"] == "skipped"

    def test_host_header_rejection(self, client_prod):
        # Sending non-localhost Host header must result in 400
        res = client_prod.get("/api/health", headers={"Host": "evil.example.com"})
        assert res.status_code == 400

    def test_cors_absent_in_prod(self, client_prod):
        res = client_prod.get("/api/health", headers={"Origin": "http://localhost:5173"})
        assert "access-control-allow-origin" not in res.headers

    def test_cors_present_in_dev(self, client_dev):
        res = client_dev.get("/api/health", headers={"Origin": "http://localhost:5173"})
        assert res.headers.get("access-control-allow-origin") == "http://localhost:5173"

    def test_privacy_no_contact_or_resume_leak(self, client_prod, sample_data):
        # Inspect profile.yaml for real contact info
        from jobpilot.profile.loader import load_profile

        profile = load_profile()
        contact = profile.identity.contact
        email = contact.get("email") if isinstance(contact, dict) else getattr(contact, "email", None)
        phone = contact.get("phone") if isinstance(contact, dict) else getattr(contact, "phone", None)

        # Candidate email and phone should NEVER appear in API responses
        endpoints = [
            "/api/health",
            "/api/progress",
            "/api/queue",
            "/api/needs-jd",
            "/api/jobs/1",
            "/api/applications",
            "/api/follow-ups",
            "/api/answers",
            "/api/stats",
            "/api/profile-report",
            "/api/run/status",
        ]
        for ep in endpoints:
            res = client_prod.get(ep)
            assert res.status_code == 200
            content = res.text
            if email:
                assert email not in content, f"Leaked email in {ep}"
            if phone:
                assert phone not in content, f"Leaked phone in {ep}"


# ---------------------------------------------------------------------------
# Requirement 8: Static Files and SPA Fallback
# ---------------------------------------------------------------------------


class TestStaticAndSpaFallback:
    def test_spa_fallback_injects_token(self, tmp_path: Path):
        static_dir = tmp_path / "frontend_dist"
        static_dir.mkdir(parents=True, exist_ok=True)
        index_html = (
            "<!DOCTYPE html>\n"
            "<html>\n"
            "<head>\n"
            "  <title>JobPilot Web</title>\n"
            "</head>\n"
            "<body>\n"
            "  <div id='root'></div>\n"
            "</body>\n"
            "</html>"
        )
        (static_dir / "index.html").write_text(index_html)
        (static_dir / "favicon.ico").write_text("icon-data")

        app = create_app(dev=False, token="spa-test-token", static_dir=static_dir)
        client = TestClient(app, base_url="http://127.0.0.1")

        # 1. Root path returns index.html with injected token
        res_root = client.get("/")
        assert res_root.status_code == 200
        assert '<meta name="jobpilot-token" content="spa-test-token">' in res_root.text

        # 2. Client-side route like /jobs/123 returns index.html with injected token
        res_route = client.get("/jobs/123")
        assert res_route.status_code == 200
        assert '<meta name="jobpilot-token" content="spa-test-token">' in res_route.text

        # 3. Static asset returns raw file
        res_asset = client.get("/favicon.ico")
        assert res_asset.status_code == 200
        assert res_asset.text == "icon-data"

    def test_spa_fallback_serves_built_frontend_dist(self):
        """Confirm the built app loads and serves correctly with production headers."""
        real_root = Path(__file__).resolve().parent.parent
        dist_dir = real_root / "frontend" / "dist"
        if not (dist_dir / "index.html").exists():
            pytest.skip("frontend/dist not yet built")

        app = create_app(dev=False, token="spa-prod-token", static_dir=dist_dir)
        client = TestClient(app, base_url="http://127.0.0.1")

        res_root = client.get("/")
        assert res_root.status_code == 200
        assert '<meta name="jobpilot-token" content="spa-prod-token">' in res_root.text
        assert res_root.headers["Content-Security-Policy"] == (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; frame-src 'self'; frame-ancestors 'self'; "
            "base-uri 'none'; form-action 'self'"
        )
        assert res_root.headers["X-Content-Type-Options"] == "nosniff"
        assert res_root.headers["Referrer-Policy"] == "no-referrer"


# ---------------------------------------------------------------------------
# Phase 5c-1c: Production Security Headers
# ---------------------------------------------------------------------------


class TestSecurityHeaders:
    EXPECTED_CSP = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; frame-src 'self'; frame-ancestors 'self'; "
        "base-uri 'none'; form-action 'self'"
    )

    def test_production_mode_security_headers_present(self, client_prod):
        res = client_prod.get("/api/health")
        assert res.status_code == 200
        assert res.headers["Content-Security-Policy"] == self.EXPECTED_CSP
        assert res.headers["X-Content-Type-Options"] == "nosniff"
        assert res.headers["Referrer-Policy"] == "no-referrer"

    def test_dev_mode_security_headers_absent(self, client_dev):
        res = client_dev.get("/api/health")
        assert res.status_code == 200
        assert "Content-Security-Policy" not in res.headers
        assert "X-Content-Type-Options" not in res.headers
        assert "Referrer-Policy" not in res.headers


# ---------------------------------------------------------------------------
# OpenAPI Schema Completeness
# ---------------------------------------------------------------------------


class TestOpenApiSchema:
    def test_openapi_schema_complete(self, client_prod):
        res = client_prod.get("/openapi.json")
        assert res.status_code == 200
        schema = res.json()
        assert "paths" in schema
        paths = schema["paths"]

        required_paths = [
            "/api/health",
            "/api/progress",
            "/api/queue",
            "/api/needs-jd",
            "/api/jobs",
            "/api/jobs/{job_id}",
            "/api/jobs/{job_id}/prepare",
            "/api/jobs/{job_id}/mark",
            "/api/jobs/{job_id}/application",
            "/api/jobs/{job_id}/skip",
            "/api/jobs/{job_id}/jd",
            "/api/applications",
            "/api/follow-ups",
            "/api/answers/ask",
            "/api/answers",
            "/api/answers/{answer_id}",
            "/api/answers/{answer_id}/approve",
            "/api/stats",
            "/api/profile-report",
            "/api/run",
            "/api/run/status",
        ]

        for p in required_paths:
            assert p in paths, f"Path {p} missing from openapi.json"
            # Verify each method on this path has explicit response schema
            methods = paths[p]
            for method, spec in methods.items():
                assert "responses" in spec, f"No responses spec for {method.upper()} {p}"
                assert "200" in spec["responses"], f"No 200 schema for {method.upper()} {p}"


# ---------------------------------------------------------------------------
# Requirement 7: CLI jobpilot ui
# ---------------------------------------------------------------------------


class TestCliUiCommand:
    def test_ui_help(self):
        result = runner.invoke(cli_app, ["ui", "--help"])
        assert result.exit_code == 0
        assert "--port" in result.output
        assert "--dev" in result.output
        assert "--no-browser" in result.output

    def test_ui_execution_dev_mode(self, monkeypatch: pytest.MonkeyPatch):
        mock_run = MagicMock()
        import uvicorn

        monkeypatch.setattr(uvicorn, "run", mock_run)

        result = runner.invoke(cli_app, ["ui", "--port", "9000", "--dev", "--no-browser"])
        assert result.exit_code == 0
        assert "JobPilot Dev Mode" in result.output
        assert "9000" in result.output

        mock_run.assert_called_once()
        args, kwargs = mock_run.call_args
        assert kwargs["host"] == "127.0.0.1"
        assert kwargs["port"] == 9000
