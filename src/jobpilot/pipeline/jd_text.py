"""Turn raw job-board text into something a person can read."""

from __future__ import annotations

import html
import re

# RemoteOK appends this canary to truncated listings. It is not part of the job.
_SPAM_RE = re.compile(
    r"Please mention the word\s+\*{0,2}[A-Za-z0-9]+\*{0,2}\s+and tag\s+\S+.*",
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+\n")
_BLANK_RE = re.compile(r"\n{3,}")

_TRUNCATED_NOTE = (
    "The source only published a short preview of this posting, so the rest of "
    "the description is not available here."
)


def _fix_mojibake(text: str) -> str:
    """Repair UTF-8 bytes that were decoded as Latin-1 (Immunixâ„¢ -> Immunix™)."""
    try:
        fixed = text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text
    return fixed if fixed != text else text


def clean_job_description(text: str | None) -> str:
    """Strip HTML, spam canaries, and encoding damage from a stored description."""
    if not text or not text.strip():
        return ""

    raw = text.replace("\r\n", "\n")
    truncated = bool(re.search(r"(?:\.\.\.|…)\s*(?:<|$)", raw)) or raw.rstrip().endswith(
        ("...", "…")
    )
    cleaned = re.sub(r"<br\s*/?>", "\n", raw, flags=re.IGNORECASE)
    cleaned = _TAG_RE.sub("", cleaned)
    cleaned = html.unescape(cleaned)
    cleaned = _fix_mojibake(cleaned)
    cleaned = _SPAM_RE.sub("", cleaned)
    cleaned = cleaned.replace("**", "")
    cleaned = _WS_RE.sub("\n", cleaned)
    cleaned = _BLANK_RE.sub("\n\n", cleaned).strip()
    # A cut-off word plus an ellipsis ("the fi...") is the board's truncation.
    cleaned = re.sub(r"\s+\S{1,3}\.{3}$", "...", cleaned).strip()
    if truncated and _TRUNCATED_NOTE not in cleaned:
        cleaned = f"{cleaned}\n\n{_TRUNCATED_NOTE}"
    return cleaned
