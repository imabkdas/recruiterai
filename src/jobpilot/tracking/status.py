"""Status transition and tracking logic for JobPilot.

Implements Sections 6.1, 12.3, and 13 of JOBPILOT_DESIGN.md.
Enforces valid status transitions and updates applications metadata (applied_at, channel, notes).
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from jobpilot.db import _now_iso, update_status, upsert_application

logger = logging.getLogger(__name__)


class InvalidStatusTransitionError(ValueError):
    """Raised when an illegal status transition is attempted."""


# Allowed transition graph per Section 6.1
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "new": {"filtered_out", "filtered", "needs_jd", "analyzed", "applied", "skipped", "expired"},
    "needs_jd": {"new", "analyzed", "applied", "filtered_out", "filtered", "skipped", "expired"},
    "analyzed": {"scored", "applied", "filtered_out", "filtered", "skipped", "expired"},
    "scored": {"queued", "prepared", "applied", "skipped", "rejected", "expired", "filtered_out", "filtered"},
    "queued": {"prepared", "applied", "skipped", "rejected", "expired"},
    "prepared": {"applied", "skipped", "rejected", "expired"},
    "applied": {"replied", "interview", "offer", "rejected", "expired"},
    "replied": {"interview", "offer", "rejected", "expired"},
    "interview": {"offer", "rejected", "expired"},
    "offer": {"rejected"},
    "rejected": set(),
    "skipped": set(),
    "expired": set(),
    "filtered_out": set(),
    "filtered": set(),
    "analysis_failed": {"new", "skipped", "applied", "expired"},
}


def mark_job_status(
    conn: sqlite3.Connection,
    job_id: int,
    new_status: str,
    channel: str | None = None,
    note: str | None = None,
    referral_contact: str | None = None,
) -> None:
    """Transition a job to a new status while enforcing valid workflow transitions."""
    row = conn.execute("SELECT id, status FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        raise KeyError(f"Job {job_id} not found.")

    current_status = row["status"]

    # Canonicalize filtered alias
    target_status = "filtered_out" if new_status == "filtered" else new_status

    # Validate transition
    if target_status != current_status:
        allowed = ALLOWED_TRANSITIONS.get(current_status, set())
        if target_status not in allowed and new_status not in allowed:
            raise InvalidStatusTransitionError(
                f"Cannot transition job {job_id} from '{current_status}' to '{new_status}'."
            )

    update_status(conn, job_id, target_status, reason=note)

    # If status relates to applying or channels/notes/referral were supplied, record application entry
    if (
        target_status in ("applied", "replied", "interview", "offer", "rejected")
        or channel
        or note
        or referral_contact
    ):
        app_data: dict[str, Any] = {"job_id": job_id}
        if target_status == "applied":
            existing_app = conn.execute(
                "SELECT applied_at FROM applications WHERE job_id = ?", (job_id,)
            ).fetchone()
            if not existing_app or not existing_app["applied_at"]:
                app_data["applied_at"] = _now_iso()
        if channel:
            app_data["channel"] = channel
        if note:
            app_data["notes"] = note
        if referral_contact:
            app_data["referral_contact"] = referral_contact
        upsert_application(conn, app_data)
