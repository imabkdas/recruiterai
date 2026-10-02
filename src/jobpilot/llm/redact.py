"""Redaction utilities for outbound LLM prompts.

Removes emails, phone numbers, personal profile URLs, and street addresses
to ensure candidate contact details and personal identifiers never reach
external LLM endpoints.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Compiled regex patterns
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
)

# Phone numbers:
# 1) International format with +: +91 98765 43210, +1-555-123-4567, +44 20 7946 0991
# 2) Standard US/Canada: (555) 123-4567, 555-123-4567, 555.123.4567
# 3) Indian mobile format: 10 digits starting with 6, 7, 8, 9, optionally spaced or preceded by 0 or +91
# 4) Labeled phone numbers: "phone: 1234567" or "tel: 1234567"
_PHONE_RE = re.compile(
    r"(?:"
    r"(?:\+?\d{1,3}[-.\s]*)?(?:\(\d{2,4}\)[-.\s]*)?\d{3,4}[-.\s]+\d{3,4}(?:[-.\s]+\d{3,4})?"  # +1-555-123-4567, 555-123-4567
    r"|"
    r"(?:\+91[-.\s]*)?[6-9]\d{4}[-.\s]*\d{5}\b"  # Indian mobile numbers e.g. 98765 43210 or 9876543210
    r")",
    re.IGNORECASE,
)

_LABELED_PHONE_RE = re.compile(
    r"\b((?:phone|tel|mobile|cell|call)[\s:#-]+)(\+?\d[\d\s().-]{4,14}\d)\b",
    re.IGNORECASE,
)

# URLs with personal identifiers (LinkedIn profiles, GitHub usernames, X/Twitter handles, portfolio URLs)
_PERSONAL_URL_RE = re.compile(
    r"(?:https?://(?:[a-zA-Z0-9-]+\.)?linkedin\.com/in/[a-zA-Z0-9_%-]+/?)"
    r"|"
    r"(?:https?://(?:[a-zA-Z0-9-]+\.)?github\.com/(?!(?:features|pricing|security|enterprise|topics|collections|trending|about|contact|login|signup|join)\b)[a-zA-Z0-9_%-]+(?:/[a-zA-Z0-9_%.-]+)?/?)"
    r"|"
    r"(?:https?://(?:[a-zA-Z0-9-]+\.)?(?:twitter|x)\.com/[a-zA-Z0-9_%-]+/?)"
    r"|"
    r"(?:https?://[a-zA-Z0-9_%-]+\.(?:github\.io|gitlab\.io)/?)",
    re.IGNORECASE,
)

# Street addresses:
# e.g., "123 Main St", "456 Elm Avenue, Apt 4B", "Flat 201, Green Garden Layout", "PIN: 560001"
_ADDRESS_RE = re.compile(
    r"(?:"
    r"\b\d{1,5}\s+[A-Za-z0-9\s.,]{1,35}\b(?:Street|St|Road|Rd|Avenue|Ave|Boulevard|Blvd|Drive|Dr|Lane|Ln|Way|Court|Ct|Circle|Cir)\b(?:[,\s]+(?:Apt|Apartment|Suite|Ste|Floor|Fl|Unit)\s*[A-Za-z0-9#-]+)?"
    r"|"
    r"\b(?:Flat|Apt|Apartment|Plot|House)\s+(?:No\.?\s*)?[A-Za-z0-9/-]+[,\s]+[A-Za-z0-9\s.,]{1,35}\b(?:Street|St|Road|Rd|Avenue|Ave|Lane|Ln|Nagar|Colony|Layout|Sector|Block)\b"
    r"|"
    r"\b(?:Pincode|PIN|Zip(?:\s+Code)?|Postal\s+Code)[\s:#-]+\d{5,6}\b"
    r")",
    re.IGNORECASE,
)


def redact_email(text: str) -> str:
    """Replace email addresses with [REDACTED_EMAIL]."""
    if not text:
        return text
    return _EMAIL_RE.sub("[REDACTED_EMAIL]", text)


def redact_phone(text: str) -> str:
    """Replace phone numbers with [REDACTED_PHONE]."""
    if not text:
        return text
    t = _LABELED_PHONE_RE.sub(r"\1[REDACTED_PHONE]", text)
    return _PHONE_RE.sub("[REDACTED_PHONE]", t)


def redact_url(text: str) -> str:
    """Replace personal profile URLs with [REDACTED_URL]."""
    if not text:
        return text
    return _PERSONAL_URL_RE.sub("[REDACTED_URL]", text)


def redact_address(text: str) -> str:
    """Replace street addresses and postal codes with [REDACTED_ADDRESS]."""
    if not text:
        return text
    return _ADDRESS_RE.sub("[REDACTED_ADDRESS]", text)


def redact_text(text: str) -> str:
    """Run all personal identifier redactions in order.

    Order:
    1. URLs (avoids partial URL pieces matching other rules)
    2. Emails
    3. Addresses
    4. Phone numbers
    """
    if not text:
        return text

    t = redact_url(text)
    t = redact_email(t)
    t = redact_address(t)
    t = redact_phone(t)
    return t
