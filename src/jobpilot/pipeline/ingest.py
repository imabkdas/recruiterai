"""Ingestion pipeline: normalization, fingerprinting, deduplication, and database storage."""

from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from jobpilot.db import upsert_job
from jobpilot.models import RawJob

logger = logging.getLogger(__name__)

# Legal entity suffixes to strip from company names
_LEGAL_SUFFIXES = [
    r"\bpvt\b", r"\bltd\b", r"\binc\b", r"\bllc\b", r"\bcorp\b",
    r"\bcorporation\b", r"\bco\b", r"\bprivate limited\b", r"\blimited\b",
    r"\bgmbh\b", r"\btechnologies\b", r"\bservices\b",
]
_LEGAL_RE = re.compile(r"|".join(_LEGAL_SUFFIXES), re.IGNORECASE)

# Seniority prefixes to strip from titles for normalization
_SENIORITY_PREFIXES = [
    r"\bsr\.\b", r"\bsr\b", r"\bsenior\b", r"\bjr\.\b", r"\bjr\b",
    r"\bjunior\b", r"\blead\b", r"\bstaff\b", r"\bprincipal\b",
    r"\bassociate\b", r"\bmid-level\b", r"\bmid\b", r"\bentry-level\b",
    r"\bi+\b", r"\b1\b", r"\b2\b", r"\b3\b",
]
_SENIORITY_RE = re.compile(r"|".join(_SENIORITY_PREFIXES), re.IGNORECASE)

# Abbreviations to expand
_ABBREVIATIONS = {
    r"\bsde\b": "software engineer",
    r"\bswe\b": "software engineer",
    r"\bdev\b": "developer",
}


def normalize_company(company: str) -> str:
    """Normalize company name: lowercase, strip legal entity suffixes and punctuation."""
    if not company:
        return "unknown"
    c = company.lower()
    c = _LEGAL_RE.sub("", c)
    # Remove non-alphanumeric except spaces
    c = re.sub(r"[^a-z0-9\s]", " ", c)
    return " ".join(c.split()) or "unknown"


def normalize_title(title: str) -> str:
    """Normalize title: lowercase, strip seniority terms, expand common abbreviations.

    TODO: stripping seniority (senior, lead, staff, …) can merge distinct roles
    such as "Java Engineer" and "Senior Java Engineer" at the same company.
    Leave this as-is until fingerprints can be migrated deliberately.
    """
    if not title:
        return "unknown"
    t = title.lower()
    for abbr, expanded in _ABBREVIATIONS.items():
        t = re.sub(abbr, expanded, t)
    t = _SENIORITY_RE.sub("", t)
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    return " ".join(t.split()) or "unknown"


def normalize_location(location: str | None) -> str:
    """Normalize location: lowercase, simplify to city or remote."""
    if not location:
        return "unknown"
    loc = location.lower().strip()
    if any(k in loc for k in ("remote", "work from home", "wfh", "anywhere")):
        return "remote"
    # Take first city segment if comma separated
    segments = [s.strip() for s in loc.split(",") if s.strip()]
    cleaned = re.sub(r"[^a-z0-9\s]", " ", segments[0] if segments else loc)
    return " ".join(cleaned.split()) or "unknown"


# Query keys that identify a campaign or click, not the job itself.
_TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "gclid",
    "fbclid",
    "mc_cid",
    "mc_eid",
    "igshid",
    "ref",
    "refid",
    "tracking",
    "trackingid",
    "trk",
    "trkinfo",
}


def _is_tracking_param(name: str) -> bool:
    key = name.lower()
    return key.startswith("utm_") or key in _TRACKING_PARAMS


def canonicalize_url(url: str | None) -> str | None:
    """Drop tracking query parameters. Keep parameters that may identify the job.

    None and blank input are returned unchanged (None or ""). A string that is
    not an absolute URL is returned unchanged.
    """
    if url is None:
        return None
    raw = url.strip()
    if not raw:
        return ""
    parsed = urlsplit(raw)
    if not parsed.scheme or not parsed.netloc:
        return raw
    kept = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not _is_tracking_param(key)
    ]
    query = urlencode(kept, doseq=True)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, parsed.fragment))


def _lookup_urls(url: str | None) -> list[str]:
    if not url:
        return []
    canon = canonicalize_url(url)
    values = []
    for candidate in (url, canon):
        if candidate and candidate not in values:
            values.append(candidate)
    return values


def find_job_id_by_url(conn: sqlite3.Connection, url: str | None) -> int | None:
    """Return a job already stored under this URL or its canonical form."""
    candidates = _lookup_urls(url)
    if not candidates:
        return None
    placeholders = ", ".join("?" for _ in candidates)
    row = conn.execute(
        f"SELECT id FROM jobs WHERE url IN ({placeholders}) OR apply_url IN ({placeholders}) LIMIT 1",  # noqa: S608
        (*candidates, *candidates),
    ).fetchone()
    if row:
        return int(row["id"])
    row = conn.execute(
        f"SELECT job_id FROM job_urls WHERE url IN ({placeholders}) LIMIT 1",  # noqa: S608
        candidates,
    ).fetchone()
    if row:
        return int(row["job_id"])
    return None


def compute_fingerprint(company: str, title: str, location: str | None) -> str:
    """Compute sha1 fingerprint of normalized company, title, and location."""
    c_norm = normalize_company(company)
    t_norm = normalize_title(title)
    l_norm = normalize_location(location)
    key = f"{c_norm}|{t_norm}|{l_norm}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


class IngestResult:
    """Funnel statistics for an ingestion run."""

    def __init__(self) -> None:
        self.fetched: int = 0
        self.new: int = 0
        self.updated: int = 0
        self.duplicates: int = 0
        self.job_ids: list[int] = []


def ingest_raw_jobs(conn: sqlite3.Connection, raw_jobs: list[RawJob]) -> IngestResult:
    """Ingest a list of RawJobs into the database with deduplication.

    Updates IngestResult stats.
    """
    result = IngestResult()
    result.fetched = len(raw_jobs)

    for rj in raw_jobs:
        fingerprint = compute_fingerprint(rj.company, rj.title, rj.location)
        existing = conn.execute(
            "SELECT id FROM jobs WHERE fingerprint = ?",
            (fingerprint,),
        ).fetchone()
        url = canonicalize_url(rj.url) or rj.url
        apply_url = canonicalize_url(rj.apply_url) if rj.apply_url else rj.apply_url
        match_id = int(existing["id"]) if existing else find_job_id_by_url(conn, url)

        job_data: dict[str, Any] = {
            "fingerprint": fingerprint,
            "source": rj.source,
            "source_job_id": rj.source_job_id,
            "url_source_job_id": _url_provenance_id(rj, url),
            "company": rj.company,
            "title": rj.title,
            "location": rj.location,
            "remote_type": rj.remote_type,
            "url": url,
            "apply_url": apply_url,
            "description": rj.description,
            "snippet": rj.snippet,
            "posted_at": rj.posted_at,
            "raw_json": rj.raw_json,
            "match_job_id": match_id,
        }

        job_id = upsert_job(conn, job_data)
        result.job_ids.append(job_id)

        if match_id is not None:
            result.updated += 1
            result.duplicates += 1
        else:
            result.new += 1

    return result


_LINKEDIN_JOB_ID = re.compile(r"/jobs/view/(\d+)")
_INSTAHYRE_JOB_ID = re.compile(r"instahyre\.com/job-(\d+)", re.IGNORECASE)
_NAUKRI_JOB_ID = re.compile(r"naukri\.com/job-listings-.+-(\d+)(?:$|\?)", re.IGNORECASE)


def _url_provenance_id(raw: RawJob, url: str | None) -> str | None:
    """Stable id the source actually published. Synthetic Gmail indexes stay off job_urls."""
    if raw.source_job_id and not raw.source_job_id.startswith("gmail:"):
        return raw.source_job_id
    if not url:
        return None
    for pattern in (_LINKEDIN_JOB_ID, _INSTAHYRE_JOB_ID, _NAUKRI_JOB_ID):
        match = pattern.search(url)
        if match:
            return match.group(1)
    return None
