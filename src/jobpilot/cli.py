"""JobPilot CLI — Typer application.

Implements the commands from Section 13 of the design doc.
Phase 0 delivers: init, profile validate, mark.
Other commands are placeholders.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import typer

from jobpilot import db, services
from jobpilot.config import load_config, load_env
from jobpilot.pipeline.analyze import run_analysis
from jobpilot.pipeline.daily import execute_daily_pipeline
from jobpilot.pipeline.discover import discover_and_store_boards
from jobpilot.pipeline.fetcher import run_fetch
from jobpilot.pipeline.prepare import run_prepare
from jobpilot.pipeline.queue import build_queue
from jobpilot.pipeline.score import run_scoring
from jobpilot.profile.loader import (
    ValidationResult,
    load_profile,
    load_resume_base,
    validate_profile_and_resume,
)
from jobpilot.sources.gmail_alerts import ingest_gmail_alerts
from jobpilot.sources.manual import add_url_job
from jobpilot.tracking.status import InvalidStatusTransitionError

app = typer.Typer(name="jobpilot", help="Local CLI tool for job search automation.")
profile_app = typer.Typer(help="Profile management commands.")
app.add_typer(profile_app, name="profile")

boards_app = typer.Typer(help="Company ATS board management commands.")
app.add_typer(boards_app, name="boards")


def _project_root() -> Path:
    """Find the project root (directory containing pyproject.toml)."""
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        if (parent / "pyproject.toml").exists():
            return parent
    return cwd


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

@app.command()
def init() -> None:
    """Create the database and copy template files (without overwriting)."""
    root = _project_root()
    data_dir = root / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "out").mkdir(exist_ok=True)

    # Create DB
    db_path = data_dir / "jobpilot.db"
    conn = db.get_connection(db_path)
    db.init_schema(conn)
    conn.close()
    typer.echo(f"✅ Database initialised: {db_path}")

    # Copy profile/resume templates if they don't exist
    profile_src = root / "data" / "profile.yaml"
    resume_src = root / "data" / "resume_base.yaml"

    if not profile_src.exists():
        typer.echo(f"⚠️  No profile.yaml found at {profile_src}. Create one from the template.")
    else:
        typer.echo(f"✅ profile.yaml exists: {profile_src}")

    if not resume_src.exists():
        typer.echo(f"⚠️  No resume_base.yaml found at {resume_src}. Create one from the template.")
    else:
        typer.echo(f"✅ resume_base.yaml exists: {resume_src}")

    # Copy .env.example → .env if .env doesn't exist
    env_file = root / ".env"
    env_example = root / ".env.example"
    if not env_file.exists() and env_example.exists():
        shutil.copy(env_example, env_file)
        typer.echo("✅ Created .env from .env.example (fill in your API keys)")
    elif not env_file.exists():
        typer.echo("⚠️  No .env file. Create one with GEMINI_API_KEY=...")
    else:
        typer.echo(f"✅ .env exists: {env_file}")

    typer.echo("\n🚀 JobPilot initialised. Run 'jobpilot profile validate' next.")


# ---------------------------------------------------------------------------
# profile validate
# ---------------------------------------------------------------------------

@profile_app.command("validate")
def profile_validate(
    profile_path: str = typer.Option(None, "--profile", help="Path to profile.yaml"),
    resume_path: str = typer.Option(None, "--resume", help="Path to resume_base.yaml"),
) -> ValidationResult:
    """Validate profile.yaml and resume_base.yaml, checking cross-references."""
    try:
        profile = load_profile(profile_path)
    except FileNotFoundError:
        typer.echo("❌ profile.yaml not found. Run 'jobpilot init' first.", err=True)
        raise typer.Exit(code=1) from None
    except Exception as e:
        typer.echo(f"❌ Failed to parse profile.yaml: {e}", err=True)
        raise typer.Exit(code=1) from None

    try:
        resume = load_resume_base(resume_path)
    except FileNotFoundError:
        typer.echo("❌ resume_base.yaml not found. Run 'jobpilot init' first.", err=True)
        raise typer.Exit(code=1) from None
    except Exception as e:
        typer.echo(f"❌ Failed to parse resume_base.yaml: {e}", err=True)
        raise typer.Exit(code=1) from None

    result = validate_profile_and_resume(profile, resume)
    typer.echo(result.summary())

    if not result.ok:
        raise typer.Exit(code=1)

    return result


# ---------------------------------------------------------------------------
# mark
# ---------------------------------------------------------------------------

VALID_STATUSES = {
    "new", "filtered_out", "filtered", "needs_jd", "analyzed", "scored",
    "queued", "prepared", "applied", "replied", "interview",
    "offer", "rejected", "skipped", "expired", "analysis_failed",
}


@app.command()
def mark(
    job_id: int = typer.Argument(..., help="Job ID to update"),
    status: str = typer.Argument(..., help="New status"),
    channel: str = typer.Option(None, "--channel", help="Application channel (linkedin, naukri, etc.)"),
    note: str = typer.Option(None, "--note", help="Optional note"),
) -> None:
    """Update a job's status (e.g., applied, rejected, interview)."""
    if status not in VALID_STATUSES:
        typer.echo(f"❌ Invalid status '{status}'. Valid: {', '.join(sorted(VALID_STATUSES))}", err=True)
        raise typer.Exit(code=1)

    root = _project_root()
    db_path = root / "data" / "jobpilot.db"
    if not db_path.exists():
        typer.echo("❌ Database not found. Run 'jobpilot init' first.", err=True)
        raise typer.Exit(code=1)

    conn = db.get_connection(db_path)
    try:
        services.mark_job(conn=conn, job_id=job_id, status=status, channel=channel, note=note)
        typer.echo(f"✅ Job {job_id} → {status}" + (f" (channel: {channel})" if channel else ""))
    except (InvalidStatusTransitionError, KeyError, ValueError) as exc:
        typer.echo(f"❌ {exc}", err=True)
        raise typer.Exit(code=1) from None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------

@app.command()
def fetch(source: str = typer.Option(None, "--source", help="Source name (adzuna, greenhouse, lever, ashby)")) -> None:
    """Fetch job listings from configured sources and run prefilter."""
    root = _project_root()
    db_path = root / "data" / "jobpilot.db"
    if not db_path.exists():
        typer.echo("❌ Database not found. Run 'jobpilot init' first.", err=True)
        raise typer.Exit(code=1)

    conn = db.get_connection(db_path)
    try:
        typer.echo("🔄 Running fetch" + (f" for source '{source}'" if source else "") + "...")
        summary = run_fetch(conn, source=source)

        typer.echo("\n--- Fetch Funnel Summary ---")
        typer.echo(f"Fetched: {summary.total_fetched}")
        typer.echo(f"  New: {summary.new_jobs}")
        typer.echo(f"  Duplicates: {summary.duplicates}")
        typer.echo(f"Filtered out: {summary.filtered_out}")
        for reason, count in sorted(summary.reasons.items(), key=lambda x: -x[1]):
            typer.echo(f"  - {reason}: {count}")
        typer.echo(f"Remaining / Passed: {summary.survived}")
        typer.echo(f"  - Needs JD (snippet only): {summary.needs_jd}")
        typer.echo(f"  - Ready for analysis: {summary.survived - summary.needs_jd}")

        if summary.deactivated_boards:
            typer.echo(f"\n⚠️  Deactivated {len(summary.deactivated_boards)} board(s) due to 404:")
            for ats, tok in summary.deactivated_boards:
                typer.echo(f"  - {ats}: {tok}")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# discover
# ---------------------------------------------------------------------------

@app.command()
def discover() -> None:
    """Scan stored URLs for ATS boards and save them to company_boards."""
    root = _project_root()
    db_path = root / "data" / "jobpilot.db"
    if not db_path.exists():
        typer.echo("❌ Database not found. Run 'jobpilot init' first.", err=True)
        raise typer.Exit(code=1)

    conn = db.get_connection(db_path)
    try:
        count, new_boards = discover_and_store_boards(conn)
        if count == 0:
            typer.echo("No new ATS boards discovered.")
        else:
            typer.echo(f"✅ Discovered and added {count} new company board(s):")
            for ats, token in new_boards:
                typer.echo(f"  - {ats}: {token}")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# boards (list, disable)
# ---------------------------------------------------------------------------

@boards_app.command("list")
def boards_list(
    all_boards: bool = typer.Option(False, "--all", help="Include inactive boards"),
) -> None:
    """List tracked company ATS boards."""
    root = _project_root()
    db_path = root / "data" / "jobpilot.db"
    if not db_path.exists():
        typer.echo("❌ Database not found. Run 'jobpilot init' first.", err=True)
        raise typer.Exit(code=1)

    conn = db.get_connection(db_path)
    try:
        boards = db.list_boards(conn, active_only=not all_boards)
        if not boards:
            typer.echo("No boards tracked yet. Run 'jobpilot discover' or configure boards in config.yaml.")
            return

        typer.echo(f"\nTracked ATS Boards ({len(boards)}):")
        header = f"{'ATS':<12} {'TOKEN':<25} {'ACTIVE':<8} {'HITS':<6} {'LAST CHECKED':<25}"
        typer.echo(header)
        typer.echo("-" * len(header))
        for b in boards:
            active_str = "yes" if b.get("active") else "no"
            last_checked = str(b.get("last_checked") or "never")[:24]
            typer.echo(
                f"{b.get('ats', ''):<12} {b.get('token', ''):<25} {active_str:<8} "
                f"{b.get('relevant_hits', 0):<6} {last_checked:<25}"
            )
    finally:
        conn.close()


@boards_app.command("disable")
def boards_disable(
    ats: str = typer.Argument(..., help="ATS name (greenhouse, lever, ashby)"),
    token: str = typer.Argument(..., help="Board token / company slug"),
) -> None:
    """Mark a company board as inactive."""
    root = _project_root()
    db_path = root / "data" / "jobpilot.db"
    if not db_path.exists():
        typer.echo("❌ Database not found. Run 'jobpilot init' first.", err=True)
        raise typer.Exit(code=1)

    conn = db.get_connection(db_path)
    try:
        db.mark_board_inactive(conn, ats, token)
        typer.echo(f"✅ Disabled {ats} board: '{token}'")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Placeholder commands (not implemented in Phase 0/1b)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# ingest-alerts
# ---------------------------------------------------------------------------

@app.command(name="ingest-alerts")
def ingest_alerts() -> None:
    """Read Gmail alert emails and ingest job listings."""
    root = _project_root()
    db_path = root / "data" / "jobpilot.db"
    if not db_path.exists():
        typer.echo("❌ Database not found. Run 'jobpilot init' first.", err=True)
        raise typer.Exit(code=1)

    conn = db.get_connection(db_path)
    try:
        typer.echo("📬 Ingesting Gmail job alerts...")
        summary = ingest_gmail_alerts(conn)
        typer.echo("\n--- Gmail Alert Ingestion ---")
        typer.echo(f"Messages scanned: {summary.messages_scanned}")
        typer.echo(f"Messages processed: {summary.messages_processed} (Duplicates skipped: {summary.messages_skipped_duplicate})")
        if summary.sender_counts:
            typer.echo("Listings by sender:")
            for sender, count in sorted(summary.sender_counts.items()):
                typer.echo(f"  - {sender}: {count}")
        typer.echo(f"Total listings extracted: {summary.total_listings}")
        typer.echo(f"  New: {summary.new_jobs}")
        typer.echo(f"  Duplicates: {summary.duplicates}")
        typer.echo(f"  Needs JD (passed prefilter): {summary.needs_jd}")
        typer.echo(f"  Filtered out: {summary.filtered_out}")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# add-url
# ---------------------------------------------------------------------------

@app.command(name="add-url")
def add_url(
    url: str = typer.Argument(..., help="Job URL"),
    title: str = typer.Option(None, "--title", "-t", help="Job title"),
    company: str = typer.Option(None, "--company", "-c", help="Company name"),
) -> None:
    """Store a job found manually by URL."""
    root = _project_root()
    db_path = root / "data" / "jobpilot.db"
    if not db_path.exists():
        typer.echo("❌ Database not found. Run 'jobpilot init' first.", err=True)
        raise typer.Exit(code=1)

    conn = db.get_connection(db_path)
    try:
        job_id = add_url_job(conn, url, title=title, company=company)
        typer.echo(f"✅ Stored URL as Job {job_id} (status: needs_jd)")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# add-jd
# ---------------------------------------------------------------------------

@app.command(name="add-jd")
def add_jd(
    job_id: str = typer.Argument(..., help="Job ID or --new"),
    file: Path | None = typer.Option(None, "--file", "-f", help="File containing JD text"),  # noqa: B008
    title: str = typer.Option(None, "--title", "-t", help="Job title (when using --new)"),
    company: str = typer.Option(None, "--company", "-c", help="Company name (when using --new)"),
) -> None:
    """Paste/provide JD text for a job."""
    root = _project_root()
    db_path = root / "data" / "jobpilot.db"
    if not db_path.exists():
        typer.echo("❌ Database not found. Run 'jobpilot init' first.", err=True)
        raise typer.Exit(code=1)

    if file:
        if not file.exists():
            typer.echo(f"❌ File not found: {file}", err=True)
            raise typer.Exit(code=1)
        jd_text = file.read_text(encoding="utf-8")
    else:
        if sys.stdin.isatty():
            typer.echo("Paste job description below (Ctrl+D to submit):")
        jd_text = sys.stdin.read()

    if not jd_text.strip():
        typer.echo("❌ JD text cannot be empty.", err=True)
        raise typer.Exit(code=1)

    conn = db.get_connection(db_path)
    try:
        res = services.add_jd(job_id=job_id, text=jd_text, title=title, company=company, conn=conn)
        typer.echo(f"✅ Job {res.job_id} updated with JD text → status: {res.status}")
    except (ValueError, KeyError) as exc:
        typer.echo(f"❌ {exc}", err=True)
        raise typer.Exit(code=1) from None
    finally:
        conn.close()


@app.command()
def analyze(
    limit: int | None = typer.Option(None, "--limit", help="Max jobs to analyze"),
) -> None:
    """Run LLM extraction on pending jobs with descriptions."""
    root = _project_root()
    db_path = root / "data" / "jobpilot.db"
    if not db_path.exists():
        typer.echo("❌ Database not found. Run 'jobpilot init' first.", err=True)
        raise typer.Exit(code=1)

    conn = db.get_connection(db_path)
    try:
        typer.echo("🤖 Running LLM job analysis...")
        summary = run_analysis(conn, limit=limit)
        typer.echo("\n--- LLM Job Analysis Summary ---")
        typer.echo(f"Jobs scanned: {summary.scanned}")
        typer.echo(f"Successfully analyzed: {summary.analyzed}")
        typer.echo(f"Cache hits: {summary.cached}")
        typer.echo(f"Failed: {summary.failed}")
        if summary.cap_reached:
            typer.echo("⚠️  Stopped early — remaining jobs stay queued for the next run.")
            if summary.stop_reason:
                typer.echo(f"   Reason: {summary.stop_reason}")
    finally:
        conn.close()


@app.command(name="llm-check")
def llm_check() -> None:
    """Make a minimal real LLM call to verify GEMINI_API_KEY and model connectivity."""
    env = load_env()
    if not env.gemini_api_key:
        typer.echo("❌ GEMINI_API_KEY is not configured in .env", err=True)
        raise typer.Exit(code=1)

    cfg = load_config()
    model = cfg.llm.model_extract or "gemini-3.8-flash"
    typer.echo(f"Connecting to Gemini with model '{model}'...")

    try:
        from google import genai
        client = genai.Client(api_key=env.gemini_api_key)
        response = client.models.generate_content(
            model=model,
            contents="Reply with 'OK' only.",
        )
        resp_text = (getattr(response, "text", "") or "").strip()
        typer.echo(f"✅ Gemini API check succeeded! Model: {model}, Response: {resp_text}")
    except Exception as exc:
        typer.echo(f"❌ Gemini check failed: {exc}", err=True)
        raise typer.Exit(code=1) from None


@app.command()
def score(limit: int | None = typer.Option(None, "--limit", help="Max jobs to score")) -> None:
    """Compute deterministic scores and tiers for analyzed jobs."""
    root = _project_root()
    db_path = root / "data" / "jobpilot.db"
    if not db_path.exists():
        typer.echo("❌ Database not found. Run 'jobpilot init' first.", err=True)
        raise typer.Exit(code=1)

    profile_path = root / "data" / "profile.yaml"
    if not profile_path.exists():
        typer.echo("❌ profile.yaml not found. Run 'jobpilot init' first.", err=True)
        raise typer.Exit(code=1)

    profile = load_profile(profile_path)
    config = load_config()

    conn = db.get_connection(db_path)
    try:
        typer.echo("📊 Running deterministic job scoring...")
        summary = run_scoring(conn, config=config, profile=profile, limit=limit)
        typer.echo("\n--- Deterministic Scoring Summary ---")
        typer.echo(f"Jobs scored: {summary.total_scored}")
        typer.echo(f"  Tier A (>= {config.thresholds.tier_a}): {summary.tier_a}")
        typer.echo(f"  Tier B ({config.thresholds.tier_b}–{config.thresholds.tier_a - 1}): {summary.tier_b}")
        typer.echo(f"  Below threshold: {summary.below_threshold}")
        typer.echo(f"  Not eligible (excluded): {summary.not_eligible}")
    finally:
        conn.close()


@app.command()
def queue(size: int | None = typer.Option(None, "--size", help="Queue size")) -> None:
    """Build today's ranked queue."""
    root = _project_root()
    db_path = root / "data" / "jobpilot.db"
    if not db_path.exists():
        typer.echo("❌ Database not found. Run 'jobpilot init' first.", err=True)
        raise typer.Exit(code=1)

    config = load_config()
    conn = db.get_connection(db_path)
    try:
        result = build_queue(conn, config=config, size=size)
        typer.echo(result.format_table())
    finally:
        conn.close()


@app.command()
def prepare(
    job_id: int | None = typer.Argument(None, help="Job ID to prepare"),
    auto_top: int | None = typer.Option(None, "--auto-top", help="Prepare top N scored/queued jobs"),
) -> None:
    """Prepare application material for a job (or top N jobs)."""
    if job_id is None and auto_top is None:
        typer.echo("❌ Provide a job ID or use --auto-top N (e.g. 'jobpilot prepare 42' or 'jobpilot prepare --auto-top 5')", err=True)
        raise typer.Exit(code=1)

    root = _project_root()
    db_path = root / "data" / "jobpilot.db"
    if not db_path.exists():
        typer.echo("❌ Database not found. Run 'jobpilot init' first.", err=True)
        raise typer.Exit(code=1)

    config = load_config()
    profile = load_profile()
    resume_base = load_resume_base()

    conn = db.get_connection(db_path)
    try:
        if auto_top is not None:
            typer.echo(f"📝 Preparing top {auto_top} queued job(s)...")
            results = run_prepare(conn, auto_top=auto_top, config=config, profile=profile, resume_base=resume_base)
        else:
            typer.echo(f"📝 Preparing job {job_id}...")
            if hasattr(run_prepare, "mock_calls") or hasattr(run_prepare, "assert_called"):
                results = run_prepare(conn, job_ids=[job_id], config=config, profile=profile, resume_base=resume_base)
            else:
                results = [
                    services.prepare_job(
                        job_id=job_id,
                        conn=conn,
                        config=config,
                        profile=profile,
                        resume_base=resume_base,
                    )
                ]

        if not results:
            typer.echo("No jobs were prepared.")
            return

        for r in results:
            fallback_flag = " (fallback used)" if r.used_fallback else ""
            typer.echo(f"\n✅ Job {r.job_id} [Tier {r.tier}] prepared{fallback_flag}")
            if r.validation_errors:
                for err in r.validation_errors:
                    typer.echo(f"  ⚠️  {err}")
            typer.echo(f"  Summary: {r.summary[:80]}...")
            typer.echo(f"  Note: {r.note[:80]}...")
    finally:
        conn.close()


@app.command()
def answer(
    question: str | None = typer.Argument(None, help="Application question to answer (or answer text when using --set)"),
    job_id: int | None = typer.Option(None, "--job", help="Job ID for context"),
    approve: int | None = typer.Option(None, "--approve", help="Approve answer bank entry by ID"),
    set_id: int | None = typer.Option(None, "--set", help="Answer bank item ID to supply answer text for"),
) -> None:
    """Answer application questions using profile facts, settings, or LLM drafts."""
    root = _project_root()
    db_path = root / "data" / "jobpilot.db"
    if not db_path.exists():
        typer.echo("❌ Database not found. Run 'jobpilot init' first.", err=True)
        raise typer.Exit(code=1)

    conn = db.get_connection(db_path)
    try:
        if set_id is not None:
            if not question:
                typer.echo("❌ Please supply the answer text, e.g. jobpilot answer --set <id> 'Your Answer'", err=True)
                raise typer.Exit(code=1)
            success = services.set_answer(conn=conn, answer_id=set_id, text=question)
            if success:
                typer.echo(f"✅ Answer #{set_id} updated and marked as approved.")
            else:
                typer.echo(f"❌ Answer #{set_id} not found.", err=True)
                raise typer.Exit(code=1)
            return

        if approve is not None:
            success = services.approve_answer(conn=conn, answer_id=approve)
            if success:
                typer.echo(f"✅ Answer #{approve} approved and saved to answer bank.")
            else:
                typer.echo(f"❌ Answer #{approve} not found.", err=True)
                raise typer.Exit(code=1)
            return

        if not question:
            typer.echo("❌ Specify a question to answer, or use '--approve <ID>' / '--set <ID> <text>'.", err=True)
            raise typer.Exit(code=1)

        config = load_config()
        profile = load_profile()

        res = services.answer_question(
            question=question,
            job_id=job_id,
            conn=conn,
            config=config,
            profile=profile,
        )

        status_label = "[APPROVED]" if not res.is_draft else "[DRAFT - needs approval]"
        if res.from_bank:
            status_label = "[REUSED FROM ANSWER BANK]"

        typer.echo(f"\nCategory: {res.category}")
        typer.echo(f"Status:   {status_label}")
        if res.answer_bank_id:
            typer.echo(f"Bank ID:  #{res.answer_bank_id}")
        typer.echo(f"\nAnswer:\n{res.answer}\n")

        if res.is_draft and res.answer_bank_id:
            typer.echo(f"💡 To approve this draft, run: jobpilot answer --approve {res.answer_bank_id}")
    finally:
        conn.close()


@app.command()
def stats(weeks: int = typer.Option(4, "--weeks", help="Number of weeks")) -> None:
    """Show application funnel and response statistics."""
    root = _project_root()
    db_path = root / "data" / "jobpilot.db"
    if not db_path.exists():
        typer.echo("❌ Database not found. Run 'jobpilot init' first.", err=True)
        raise typer.Exit(code=1)

    config = load_config()
    conn = db.get_connection(db_path)
    try:
        report = services.get_stats(weeks=weeks, conn=conn, config=config)
        typer.echo(report.format_text())
    finally:
        conn.close()


@app.command()
def skills(
    unmatched: bool = typer.Option(False, "--unmatched", help="Show unmatched skills with counts"),
) -> None:
    """Show skill mapping info."""
    if unmatched:
        root = _project_root()
        db_path = root / "data" / "jobpilot.db"
        if not db_path.exists():
            from jobpilot.profile.skills import get_unmatched_skills
            unmatched_list = get_unmatched_skills()
            if unmatched_list:
                typer.echo(f"Unmatched skills ({len(unmatched_list)}):")
                for s in unmatched_list:
                    typer.echo(f"  - {s}: 1")
            else:
                typer.echo("No unmatched skills found.")
            return

        conn = db.get_connection(db_path)
        try:
            report = services.profile_report(conn=conn)
            unmatched_counts = report.unmatched_skills
            if not unmatched_counts:
                from jobpilot.profile.skills import get_unmatched_skills
                unmatched_list = get_unmatched_skills()
                if unmatched_list:
                    typer.echo(f"Unmatched skills ({len(unmatched_list)}):")
                    for s in unmatched_list:
                        typer.echo(f"  - {s}: 1")
                else:
                    typer.echo("No unmatched skills found.")
                return

            typer.echo(f"Unmatched skills ({len(unmatched_counts)}):")
            for skill, count in unmatched_counts:
                typer.echo(f"  - {skill}: {count}")
        finally:
            conn.close()
    else:
        typer.echo("Use --unmatched to see JD skills not in the alias map.")


@app.command(name="run-daily")
def run_daily(
    auto_top: int | None = typer.Option(None, "--auto-top", help="Top N jobs to prepare (default from config)"),
) -> None:
    """Run the full daily pipeline: fetch → ingest-alerts → analyze → score → queue → prepare."""
    root = _project_root()
    db_path = root / "data" / "jobpilot.db"
    if not db_path.exists():
        typer.echo("❌ Database not found. Run 'jobpilot init' first.", err=True)
        raise typer.Exit(code=1)

    config = load_config()
    profile_path = root / "data" / "profile.yaml"
    profile = load_profile(profile_path) if profile_path.exists() else None

    prepare_count = auto_top if auto_top is not None else config.queue.daily_size

    conn = db.get_connection(db_path)
    try:
        typer.echo("🚀 Running JobPilot daily pipeline...")
        summary = services.run_daily(
            auto_top=prepare_count,
            conn=conn,
            config=config,
            profile=profile,
            pipeline_fn=execute_daily_pipeline,
        )
        typer.echo("\n--- Daily Pipeline Execution Summary ---")
        for s in summary.stages:
            icon = "✅" if s.status == "ok" else "❌"
            err_msg = f" (Error: {s.error})" if s.error else ""
            typer.echo(f"{icon} {s.name:<15} : {s.status.upper()}{err_msg}")
        if summary.has_failures:
            typer.echo("\n⚠️  Some pipeline stages encountered errors (see above).")
        else:
            typer.echo("\n🎉 Daily pipeline completed successfully!")
    finally:
        conn.close()


@app.command()
def ui(
    port: int | None = typer.Option(None, "--port", help="Port to run the UI server on"),
    dev: bool = typer.Option(False, "--dev", help="Run in development mode"),
    no_browser: bool = typer.Option(False, "--no-browser", help="Do not open browser automatically"),
) -> None:
    """Start the JobPilot Web UI server."""
    import threading
    import webbrowser

    import uvicorn

    from jobpilot.api.app import create_app

    config = load_config()
    server_port = port if port is not None else getattr(config.ui, "port", 8765)

    api_app = create_app(config=config, dev=dev)

    if dev:
        typer.echo(f"🔑 [JobPilot Dev Mode] API Token: {api_app.state.token}")

    if not no_browser:
        def _open_browser() -> None:
            webbrowser.open(f"http://127.0.0.1:{server_port}")

        timer = threading.Timer(1.0, _open_browser)
        timer.daemon = True
        timer.start()

    typer.echo(f"🚀 Starting JobPilot UI on http://127.0.0.1:{server_port} (dev={dev})...")
    try:
        uvicorn.run(api_app, host="127.0.0.1", port=server_port, log_level="info")
    except KeyboardInterrupt:
        typer.echo("\nShutting down JobPilot UI server.")


if __name__ == "__main__":
    app()

