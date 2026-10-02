"""Database layer for JobPilot.

Uses stdlib sqlite3. Provides schema initialisation (idempotent) and small
repository functions. The schema matches Section 6.2 of the design doc.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 3

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    fingerprint TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL,
    source_job_id TEXT,
    company TEXT NOT NULL,
    title TEXT NOT NULL,
    location TEXT,
    remote_type TEXT,
    url TEXT NOT NULL,
    apply_url TEXT,
    description TEXT,
    snippet TEXT,
    posted_at TEXT,
    discovered_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new',
    status_reason TEXT,
    raw_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

CREATE TABLE IF NOT EXISTS job_urls (
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    source TEXT NOT NULL,
    url TEXT NOT NULL,
    source_job_id TEXT,
    PRIMARY KEY (job_id, url)
);

CREATE TABLE IF NOT EXISTS source_runs (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL,
    found_count INTEGER NOT NULL DEFAULT 0,
    new_count INTEGER NOT NULL DEFAULT 0,
    updated_count INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

CREATE TABLE IF NOT EXISTS analyses (
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    prompt_version TEXT NOT NULL,
    model TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    analysis_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (job_id, prompt_version, content_hash)
);

CREATE TABLE IF NOT EXISTS scores (
    job_id INTEGER PRIMARY KEY REFERENCES jobs(id),
    total REAL NOT NULL,
    skills_score REAL,
    experience_score REAL,
    seniority_score REAL,
    location_score REAL,
    extras_score REAL,
    tier TEXT,
    matched_skills TEXT,
    missing_required TEXT,
    missing_preferred TEXT,
    flags TEXT,
    scored_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL UNIQUE REFERENCES jobs(id),
    tailored_summary TEXT,
    short_note TEXT,
    outreach_draft TEXT,
    bullet_ids TEXT,
    prepared_at TEXT,
    applied_at TEXT,
    channel TEXT,
    referral_contact TEXT,
    notes TEXT,
    last_status_change TEXT,
    resume_md_path TEXT,
    resume_pdf_path TEXT
);

CREATE TABLE IF NOT EXISTS llm_usage (
    id INTEGER PRIMARY KEY,
    task TEXT,
    model TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cached INTEGER DEFAULT 0,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS answer_bank (
    id INTEGER PRIMARY KEY,
    question_norm TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL,
    answer TEXT,
    approved INTEGER NOT NULL DEFAULT 0,
    source_job_id INTEGER,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS api_budget (
    api_name TEXT NOT NULL,
    period TEXT NOT NULL,
    call_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (api_name, period)
);

CREATE TABLE IF NOT EXISTS company_boards (
    ats TEXT NOT NULL,
    token TEXT NOT NULL,
    discovered_from_job_id INTEGER,
    first_seen TEXT NOT NULL,
    last_checked TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    relevant_hits INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (ats, token)
);

CREATE TABLE IF NOT EXISTS gmail_processed_messages (
    message_id TEXT PRIMARY KEY,
    processed_at TEXT NOT NULL,
    sender TEXT,
    job_count INTEGER NOT NULL DEFAULT 0
);
"""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def get_connection(db_path: str | Path) -> sqlite3.Connection:
    """Return a connection with WAL mode, busy timeout, foreign keys, and idempotent schema init."""
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    init_schema(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Create tables if they don't exist (idempotent). Set schema version."""
    conn.executescript(_SCHEMA_SQL)
    _apply_column_migrations(conn)

    row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    if row is None:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
    else:
        conn.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION,))
    conn.commit()


# Columns added after a table first shipped. CREATE TABLE IF NOT EXISTS cannot
# add these to a database that already exists, so they are applied explicitly.
_COLUMN_MIGRATIONS: dict[str, dict[str, str]] = {
    "applications": {
        "resume_md_path": "TEXT",
        "resume_pdf_path": "TEXT",
    },
    "job_urls": {
        "source_job_id": "TEXT",
    },
}


def _apply_column_migrations(conn: sqlite3.Connection) -> list[str]:
    """Add any missing columns to existing tables. Returns the columns added."""
    added: list[str] = []
    for table, columns in _COLUMN_MIGRATIONS.items():
        existing = {
            row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if not existing:
            continue  # table not created yet; the schema script handles it
        for name, decl in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")  # noqa: S608
                added.append(f"{table}.{name}")
    if added:
        conn.commit()
    return added


# ---------------------------------------------------------------------------
# Repository helpers
# ---------------------------------------------------------------------------

def _record_job_url(conn: sqlite3.Connection, job_id: int, job_data: dict[str, Any]) -> None:
    """Store a source URL on the job. source_job_id is provenance, not an identity key."""
    url = job_data.get("url")
    if not url:
        return
    conn.execute(
        """
        INSERT INTO job_urls (job_id, source, url, source_job_id)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(job_id, url) DO UPDATE SET
            source_job_id = COALESCE(job_urls.source_job_id, excluded.source_job_id)
        """,
        (
            job_id,
            job_data.get("source", ""),
            url,
            job_data.get("url_source_job_id"),
        ),
    )


def record_source_run(
    conn: sqlite3.Connection,
    source: str,
    *,
    started_at: str,
    completed_at: str,
    status: str,
    found_count: int = 0,
    new_count: int = 0,
    updated_count: int = 0,
    error: str | None = None,
) -> None:
    """Persist one source attempt. Used by the fetch and Gmail stages."""
    conn.execute(
        """
        INSERT INTO source_runs (
            source, started_at, completed_at, status,
            found_count, new_count, updated_count, error
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (source, started_at, completed_at, status, found_count, new_count, updated_count, error),
    )
    conn.commit()


def upsert_job(conn: sqlite3.Connection, job_data: dict[str, Any]) -> int:
    """Insert a new job or refresh an existing one.

    Identity is the fingerprint when present, otherwise match_job_id from a
    canonical URL lookup. discovered_at is set only on insert.

    Returns the job ID.
    """
    now = _now_iso()
    match_id = job_data.get("match_job_id")
    if match_id is not None:
        existing = conn.execute(
            "SELECT id, description, posted_at FROM jobs WHERE id = ?",
            (match_id,),
        ).fetchone()
    else:
        existing = conn.execute(
            "SELECT id, description, posted_at FROM jobs WHERE fingerprint = ?",
            (job_data["fingerprint"],),
        ).fetchone()

    if existing:
        job_id = existing["id"]
        updates: dict[str, Any] = {"last_seen_at": now}
        if not existing["description"] and job_data.get("description"):
            updates["description"] = job_data["description"]
        if not existing["posted_at"] and job_data.get("posted_at"):
            updates["posted_at"] = job_data["posted_at"]

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(
            f"UPDATE jobs SET {set_clause} WHERE id = ?",  # noqa: S608
            (*updates.values(), job_id),
        )
        _record_job_url(conn, job_id, job_data)
        conn.commit()
        return job_id

    job_data.setdefault("discovered_at", now)
    job_data.setdefault("last_seen_at", now)
    job_data.setdefault("status", "new")

    columns = [
        "fingerprint", "source", "source_job_id", "company", "title",
        "location", "remote_type", "url", "apply_url", "description",
        "snippet", "posted_at", "discovered_at", "last_seen_at",
        "status", "status_reason", "raw_json",
    ]
    values = []
    for col in columns:
        val = job_data.get(col)
        if col == "raw_json" and isinstance(val, dict):
            val = json.dumps(val)
        values.append(val)

    placeholders = ", ".join("?" for _ in columns)
    col_names = ", ".join(columns)
    cursor = conn.execute(
        f"INSERT INTO jobs ({col_names}) VALUES ({placeholders})",  # noqa: S608
        values,
    )
    job_id = cursor.lastrowid
    _record_job_url(conn, int(job_id), job_data)  # type: ignore[arg-type]
    conn.commit()
    return job_id  # type: ignore[return-value]


def get_jobs_by_status(conn: sqlite3.Connection, status: str) -> list[dict[str, Any]]:
    """Return all jobs with the given status."""
    rows = conn.execute("SELECT * FROM jobs WHERE status = ?", (status,)).fetchall()
    return [dict(row) for row in rows]


def update_status(
    conn: sqlite3.Connection,
    job_id: int,
    new_status: str,
    reason: str | None = None,
) -> None:
    """Change a job's status and optionally set a reason."""
    conn.execute(
        "UPDATE jobs SET status = ?, status_reason = ? WHERE id = ?",
        (new_status, reason, job_id),
    )
    conn.commit()


def save_analysis(
    conn: sqlite3.Connection,
    job_id: int,
    prompt_version: str,
    model: str,
    content_hash: str,
    analysis_json: str,
) -> None:
    """Store an LLM analysis result (cache key: job_id + prompt_version + content_hash)."""
    conn.execute(
        "INSERT OR REPLACE INTO analyses (job_id, prompt_version, model, content_hash, analysis_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (job_id, prompt_version, model, content_hash, analysis_json, _now_iso()),
    )
    conn.commit()


def get_analysis(
    conn: sqlite3.Connection,
    job_id: int,
    prompt_version: str,
    content_hash: str,
) -> str | None:
    """Retrieve cached analysis JSON by job_id, prompt_version, and content_hash."""
    row = conn.execute(
        "SELECT analysis_json FROM analyses WHERE job_id = ? AND prompt_version = ? AND content_hash = ?",
        (job_id, prompt_version, content_hash),
    ).fetchone()
    return row["analysis_json"] if row else None


def record_llm_usage(
    conn: sqlite3.Connection,
    task: str,
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached: int = 0,
) -> None:
    """Record token and call count usage for an LLM interaction."""
    conn.execute(
        "INSERT INTO llm_usage (task, model, input_tokens, output_tokens, cached, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (task, model, input_tokens, output_tokens, cached, _now_iso()),
    )
    conn.commit()


def get_daily_llm_call_count(
    conn: sqlite3.Connection,
    date_prefix: str | None = None,
) -> int:
    """Count non-cached LLM calls recorded today (or for given YYYY-MM-DD prefix)."""
    if date_prefix is None:
        date_prefix = datetime.now(UTC).strftime("%Y-%m-%d")
    prefix = f"{date_prefix}%"
    row = conn.execute(
        "SELECT COUNT(*) AS cnt FROM llm_usage WHERE cached = 0 AND created_at LIKE ?",
        (prefix,),
    ).fetchone()
    return int(row["cnt"]) if row else 0


def get_all_analyses(conn: sqlite3.Connection) -> list[str]:
    """Return all analysis_json strings stored in the database."""
    rows = conn.execute("SELECT analysis_json FROM analyses").fetchall()
    return [row["analysis_json"] for row in rows]


def save_score(conn: sqlite3.Connection, score_data: dict[str, Any]) -> None:
    """Upsert a scoring result for a job."""
    for list_field in ("matched_skills", "missing_required", "missing_preferred", "flags"):
        if isinstance(score_data.get(list_field), list):
            score_data[list_field] = json.dumps(score_data[list_field])

    score_data.setdefault("scored_at", _now_iso())
    columns = [
        "job_id", "total", "skills_score", "experience_score",
        "seniority_score", "location_score", "extras_score", "tier",
        "matched_skills", "missing_required", "missing_preferred",
        "flags", "scored_at",
    ]
    values = [score_data.get(col) for col in columns]
    placeholders = ", ".join("?" for _ in columns)
    col_names = ", ".join(columns)
    conn.execute(
        f"INSERT OR REPLACE INTO scores ({col_names}) VALUES ({placeholders})",  # noqa: S608
        values,
    )
    conn.commit()


def upsert_application(conn: sqlite3.Connection, app_data: dict[str, Any]) -> int:
    """Insert or update an application record. Returns the application ID."""
    existing = conn.execute(
        "SELECT id FROM applications WHERE job_id = ?",
        (app_data["job_id"],),
    ).fetchone()

    now = _now_iso()
    if isinstance(app_data.get("bullet_ids"), list):
        app_data["bullet_ids"] = json.dumps(app_data["bullet_ids"])

    if existing:
        app_id = existing["id"]
        settable = {k: v for k, v in app_data.items() if k not in ("id", "job_id") and v is not None}
        settable["last_status_change"] = now
        if settable:
            set_clause = ", ".join(f"{k} = ?" for k in settable)
            conn.execute(
                f"UPDATE applications SET {set_clause} WHERE id = ?",  # noqa: S608
                (*settable.values(), app_id),
            )
        conn.commit()
        return app_id

    app_data.setdefault("last_status_change", now)

    columns = [
        "job_id", "tailored_summary", "short_note", "outreach_draft",
        "bullet_ids", "prepared_at", "applied_at", "channel",
        "referral_contact", "notes", "last_status_change",
        "resume_md_path", "resume_pdf_path",
    ]
    values = [app_data.get(col) for col in columns]
    placeholders = ", ".join("?" for _ in columns)
    col_names = ", ".join(columns)
    cursor = conn.execute(
        f"INSERT INTO applications ({col_names}) VALUES ({placeholders})",  # noqa: S608
        values,
    )
    conn.commit()
    return cursor.lastrowid  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# API budget repository
# ---------------------------------------------------------------------------

def get_api_usage(conn: sqlite3.Connection, api_name: str, period: str) -> int:
    """Return the total call count for the given API and period (e.g. YYYY-MM)."""
    row = conn.execute(
        "SELECT call_count FROM api_budget WHERE api_name = ? AND period = ?",
        (api_name, period),
    ).fetchone()
    return row[0] if row else 0


def increment_api_usage(conn: sqlite3.Connection, api_name: str, period: str, count: int = 1) -> int:
    """Increment call count for the given API and period. Returns new call count."""
    conn.execute(
        """
        INSERT INTO api_budget (api_name, period, call_count)
        VALUES (?, ?, ?)
        ON CONFLICT(api_name, period) DO UPDATE SET call_count = call_count + excluded.call_count
        """,
        (api_name, period, count),
    )
    conn.commit()
    return get_api_usage(conn, api_name, period)


# ---------------------------------------------------------------------------
# Company boards repository
# ---------------------------------------------------------------------------

def upsert_board(
    conn: sqlite3.Connection,
    ats: str,
    token: str,
    job_id: int | None = None,
) -> bool:
    """Insert a discovered board if not exists.

    Returns True if a new row was inserted, False if it already existed.
    """
    ats = ats.strip().lower()
    token = token.strip().lower()
    now = _now_iso()
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO company_boards (ats, token, discovered_from_job_id, first_seen, active, relevant_hits)
        VALUES (?, ?, ?, ?, 1, 0)
        """,
        (ats, token, job_id, now),
    )
    conn.commit()
    return cursor.rowcount > 0


def get_active_boards(
    conn: sqlite3.Connection,
    ats: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Return active boards ordered by relevant_hits descending.

    Can be filtered by ats and limited.
    """
    sql = "SELECT * FROM company_boards WHERE active = 1"
    params: list[Any] = []
    if ats:
        sql += " AND ats = ?"
        params.append(ats.strip().lower())
    sql += " ORDER BY relevant_hits DESC, token ASC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def mark_board_inactive(conn: sqlite3.Connection, ats: str, token: str) -> None:
    """Deactivate a board (e.g. after receiving a 404)."""
    conn.execute(
        "UPDATE company_boards SET active = 0 WHERE ats = ? AND token = ?",
        (ats.strip().lower(), token.strip().lower()),
    )
    conn.commit()


def update_board_checked(
    conn: sqlite3.Connection,
    ats: str,
    token: str,
    hits: int = 0,
) -> None:
    """Update last_checked timestamp and add to relevant_hits count."""
    now = _now_iso()
    conn.execute(
        """
        UPDATE company_boards
        SET last_checked = ?, relevant_hits = relevant_hits + ?
        WHERE ats = ? AND token = ?
        """,
        (now, hits, ats.strip().lower(), token.strip().lower()),
    )
    conn.commit()


def list_boards(conn: sqlite3.Connection, active_only: bool = False) -> list[dict[str, Any]]:
    """List boards with optional active-only filter."""
    sql = "SELECT * FROM company_boards"
    if active_only:
        sql += " WHERE active = 1"
    sql += " ORDER BY relevant_hits DESC, ats ASC, token ASC"
    rows = conn.execute(sql).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Gmail processed messages repository
# ---------------------------------------------------------------------------

def is_gmail_message_processed(conn: sqlite3.Connection, message_id: str) -> bool:
    """Return True if message_id has already been processed."""
    row = conn.execute(
        "SELECT 1 FROM gmail_processed_messages WHERE message_id = ?",
        (message_id,),
    ).fetchone()
    return row is not None


def record_processed_gmail_message(
    conn: sqlite3.Connection,
    message_id: str,
    sender: str | None = None,
    job_count: int = 0,
) -> None:
    """Record a processed Gmail message for idempotency."""
    now = _now_iso()
    conn.execute(
        """
        INSERT OR IGNORE INTO gmail_processed_messages (message_id, processed_at, sender, job_count)
        VALUES (?, ?, ?, ?)
        """,
        (message_id, now, sender, job_count),
    )
    conn.commit()


