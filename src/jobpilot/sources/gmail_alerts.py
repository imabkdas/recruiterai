"""Gmail job alert ingestion and parsers for LinkedIn, Naukri, and Instahyre emails.

Enforces scope 'https://www.googleapis.com/auth/gmail.readonly' strictly.
Never sends, modifies, or deletes mail.
Never fetches or scrapes links inside emails.
Descriptions are left None so jobs passing prefilter move to status 'needs_jd'.
"""

from __future__ import annotations

import base64
import contextlib
import json
import logging
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
from selectolax.parser import HTMLParser

from jobpilot.config import AppConfig, load_config, load_env
from jobpilot.db import (
    is_gmail_message_processed,
    record_processed_gmail_message,
    record_source_run,
)
from jobpilot.models import RawJob
from jobpilot.pipeline.ingest import ingest_raw_jobs
from jobpilot.pipeline.prefilter import run_prefilter

logger = logging.getLogger(__name__)

GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"


# ---------------------------------------------------------------------------
# LinkedIn and Naukri alert HTML parsers
# ---------------------------------------------------------------------------

def parse_linkedin_alert(html_content: str, message_id: str) -> list[RawJob]:
    """Parse LinkedIn job alert email HTML into a list of RawJobs.

    Extracts title, company, location, URL, and snippet.
    Sets description=None so survivors get status needs_jd.
    Never fetches URLs.
    """
    if not html_content or not isinstance(html_content, str):
        return []

    parser = HTMLParser(html_content)
    raw_jobs: list[RawJob] = []

    # Find job links (LinkedIn job links typically contain /jobs/view/ or /comm/jobs/view/)
    job_links = parser.css('a[href*="/jobs/view/"], a[href*="/comm/jobs/view/"], a.job-title, a.job-title-link')
    seen_urls: set[str] = set()

    for idx, link_node in enumerate(job_links):
        url = link_node.attributes.get("href", "").strip()
        if not url or url in seen_urls:
            continue

        title = link_node.text(strip=True)
        if not title or len(title) < 3 or "view" in title.lower() and "job" in title.lower():
            # Sometimes the link is an icon or "View job" button
            continue

        seen_urls.add(url)

        # Climb to the container table or div to extract company and location
        container = link_node.parent
        for _ in range(4):
            if container is None or container.tag in ("table", "div", "body"):
                break
            container = container.parent

        company = "Unknown"
        location = None
        snippet = None

        if container:
            # Look for company
            comp_node = container.css_first(".company-name, .company, p.company, div.company")
            if comp_node:
                company = comp_node.text(strip=True) or "Unknown"

            # Look for location
            loc_node = container.css_first(".job-location, .location, span.location, div.location")
            if loc_node:
                location = loc_node.text(strip=True) or None

            # Look for snippet
            snip_node = container.css_first(".job-snippet, .snippet, p.snippet, div.snippet")
            if snip_node:
                snippet = snip_node.text(strip=True) or None

        raw_jobs.append(
            RawJob(
                source="gmail:linkedin",
                source_job_id=f"gmail:{message_id}:{idx}",
                company=company,
                title=title,
                location=location,
                url=url,
                apply_url=url,
                description=None,  # Intentionally null per Section 19
                snippet=snippet,
                raw_json={"message_id": message_id, "index": idx, "source": "linkedin_alert"},
            )
        )

    return raw_jobs


def parse_naukri_alert(html_content: str, message_id: str) -> list[RawJob]:
    """Parse Naukri job alert email HTML into a list of RawJobs.

    Extracts title, company, experience, location, URL, and keyskills snippet.
    Sets description=None so survivors get status needs_jd.
    Never fetches URLs.
    """
    if not html_content or not isinstance(html_content, str):
        return []

    parser = HTMLParser(html_content)
    raw_jobs: list[RawJob] = []

    # Find job links (Naukri job links typically contain /job-listings-)
    job_links = parser.css('a[href*="/job-listings-"], a.title, a.job-name')
    seen_urls: set[str] = set()

    for idx, link_node in enumerate(job_links):
        url = link_node.attributes.get("href", "").strip()
        if not url or url in seen_urls:
            continue

        title = link_node.text(strip=True)
        if not title or len(title) < 3:
            continue

        seen_urls.add(url)

        container = link_node.parent
        for _ in range(4):
            if container is None or container.tag in ("table", "div", "body"):
                break
            container = container.parent

        company = "Unknown"
        location = None
        snippets: list[str] = []

        if container:
            comp_node = container.css_first(".org, .company-label, .company, span.company")
            if comp_node:
                company = comp_node.text(strip=True) or "Unknown"

            exp_node = container.css_first(".exp, .experience-label, .experience")
            if exp_node:
                snippets.append(f"Experience: {exp_node.text(strip=True)}")

            loc_node = container.css_first(".loc, .location-label, .location")
            if loc_node:
                location = loc_node.text(strip=True) or None

            desc_node = container.css_first(".desc, .skills-summary, .skills")
            if desc_node:
                snippets.append(desc_node.text(strip=True))

        snippet_str = " | ".join(snippets) if snippets else None

        raw_jobs.append(
            RawJob(
                source="gmail:naukri",
                source_job_id=f"gmail:{message_id}:{idx}",
                company=company,
                title=title,
                location=location,
                url=url,
                apply_url=url,
                description=None,  # Intentionally null per Section 19
                snippet=snippet_str,
                raw_json={"message_id": message_id, "index": idx, "source": "naukri_alert"},
            )
        )

    return raw_jobs


def parse_instahyre_alert(html_content: str, message_id: str) -> list[RawJob]:
    """Parse Instahyre job-alert HTML into RawJobs.

    Job identity comes from the public URL shape instahyre.com/job-<id>-...
    Company, location, and description are taken from the mail body when present.
    Missing fields stay empty. The message is never fetched as a page.
    """
    if not html_content or not isinstance(html_content, str):
        return []

    parser = HTMLParser(html_content)
    raw_jobs: list[RawJob] = []
    job_links = parser.css('a[href*="instahyre.com/job-"]')
    seen_urls: set[str] = set()

    for idx, link_node in enumerate(job_links):
        url = link_node.attributes.get("href", "").strip()
        if not url or url in seen_urls:
            continue
        title = link_node.text(strip=True)
        if not title or len(title) < 3:
            continue
        seen_urls.add(url)

        container = link_node.parent
        for _ in range(4):
            if container is None or container.tag in ("table", "div", "body"):
                break
            container = container.parent

        company = "Unknown"
        location = None
        description = None
        if container:
            comp_node = container.css_first(".company, .org, .company-name")
            if comp_node and comp_node.text(strip=True):
                company = comp_node.text(strip=True)
            loc_node = container.css_first(".location, .loc")
            if loc_node and loc_node.text(strip=True):
                location = loc_node.text(strip=True)
            desc_node = container.css_first(".desc, .description, .job-description")
            if desc_node and desc_node.text(strip=True):
                description = desc_node.text(strip=True)

        job_id_match = re.search(r"instahyre\.com/job-(\d+)", url, re.IGNORECASE)
        raw_jobs.append(
            RawJob(
                source="gmail:instahyre",
                source_job_id=job_id_match.group(1) if job_id_match else None,
                company=company,
                title=title,
                location=location,
                url=url,
                apply_url=url,
                description=description,
                snippet=description,
                posted_at=None,
                raw_json={"message_id": message_id, "index": idx, "source": "instahyre_alert"},
            )
        )

    return raw_jobs


def parse_alert_email(sender: str, html_content: str, message_id: str) -> list[RawJob]:
    """Route an email to the LinkedIn, Naukri, or Instahyre parser."""
    sender_lower = (sender or "").lower()
    html_lower = (html_content or "").lower()

    if "linkedin" in sender_lower or "linkedin" in html_lower:
        return parse_linkedin_alert(html_content, message_id)
    if "naukri" in sender_lower or "naukri" in html_lower:
        return parse_naukri_alert(html_content, message_id)
    if "instahyre" in sender_lower or "instahyre" in html_lower:
        return parse_instahyre_alert(html_content, message_id)

    if "linkedin.com/comm/jobs" in html_lower or "linkedin.com/jobs" in html_lower:
        return parse_linkedin_alert(html_content, message_id)
    if "naukri.com/job-listings" in html_lower:
        return parse_naukri_alert(html_content, message_id)
    if "instahyre.com/job-" in html_lower:
        return parse_instahyre_alert(html_content, message_id)

    return []


# ---------------------------------------------------------------------------
# OAuth and Gmail client
# ---------------------------------------------------------------------------

class GmailOAuthClient:
    """Read-only Gmail client implementing OAuth 2.0 installed app flow.

    Strictly uses scope https://www.googleapis.com/auth/gmail.readonly.
    """

    def __init__(
        self,
        credentials_file: str | Path | None = None,
        token_file: str | Path | None = None,
    ) -> None:
        env = load_env()
        self.credentials_file = Path(credentials_file or env.google_oauth_client_file)
        self.token_file = Path(token_file or "token.json")
        self.scope = GMAIL_READONLY_SCOPE

    def get_access_token(self, force_refresh: bool = False) -> str:
        """Return a valid OAuth access token, refreshing if necessary.

        Pass force_refresh after the API rejects a token that still looked valid.
        """
        # 1. Check existing token.json
        if self.token_file.exists():
            try:
                with open(self.token_file) as f:
                    token_data: dict[str, Any] = json.load(f)
                access_token = token_data.get("access_token", "")
                refresh_token = token_data.get("refresh_token", "")
                if access_token and not force_refresh and not self._is_token_expired(token_data):
                    return access_token
                if refresh_token:
                    refreshed = self._refresh_access_token(refresh_token)
                    if refreshed:
                        return refreshed
                    logger.warning(
                        "Refreshing the Gmail token failed. Falling back to interactive consent."
                    )
            except Exception as exc:
                logger.warning("Error reading token file %s: %s", self.token_file, exc)

        # 2. Interactive installed app flow
        return self._run_installed_app_flow()

    def _is_token_expired(self, token_data: dict[str, Any]) -> bool:
        """Return True when the stored access token is expired, due, or undated.

        A token without a recorded expiry is treated as expired so it gets
        refreshed rather than used until Gmail rejects it with a 401.
        """
        expiry_raw = token_data.get("expiry")
        if not expiry_raw:
            return True
        try:
            expiry = datetime.fromisoformat(str(expiry_raw).replace("Z", "+00:00"))
        except ValueError:
            return True
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=UTC)
        # Refresh slightly early so a token cannot expire mid-run.
        return datetime.now(UTC) >= expiry - timedelta(seconds=60)

    def _save_token(self, data: dict[str, Any], refresh_token: str | None = None) -> dict[str, Any]:
        """Persist a token response, recording an absolute expiry timestamp."""
        token_data = dict(data)
        if refresh_token and not token_data.get("refresh_token"):
            token_data["refresh_token"] = refresh_token
        token_data.setdefault("scope", self.scope)

        expires_in = token_data.pop("expires_in", None)
        if expires_in is not None:
            with contextlib.suppress(TypeError, ValueError):
                token_data["expiry"] = (
                    datetime.now(UTC) + timedelta(seconds=int(expires_in))
                ).isoformat()

        with open(self.token_file, "w") as f:
            json.dump(token_data, f)
        return token_data

    def _refresh_access_token(self, refresh_token: str) -> str | None:
        """Use refresh_token to acquire a new access token."""
        if not self.credentials_file.exists():
            return None
        with open(self.credentials_file) as f:
            creds = json.load(f)
        client_info = creds.get("installed") or creds.get("web") or {}
        client_id = client_info.get("client_id")
        client_secret = client_info.get("client_secret")
        token_uri = client_info.get("token_uri", "https://oauth2.googleapis.com/token")

        if not client_id or not client_secret:
            return None

        try:
            resp = httpx.post(
                token_uri,
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
                timeout=10.0,
            )
            if resp.status_code == 200:
                saved = self._save_token(resp.json(), refresh_token=refresh_token)
                return saved.get("access_token")
            logger.warning(
                "OAuth token refresh rejected (HTTP %d): %s", resp.status_code, resp.text
            )
        except Exception as exc:
            logger.warning("Error refreshing OAuth token: %s", exc)

        return None

    def _run_installed_app_flow(self) -> str:
        """Run interactive OAuth consent in browser and capture auth code via local server."""
        if not self.credentials_file.exists():
            raise FileNotFoundError(f"OAuth credentials file not found: {self.credentials_file}")

        with open(self.credentials_file) as f:
            creds = json.load(f)
        client_info = creds.get("installed") or creds.get("web") or {}
        client_id = client_info.get("client_id")
        client_secret = client_info.get("client_secret")
        auth_uri = client_info.get("auth_uri", "https://accounts.google.com/o/oauth2/auth")
        token_uri = client_info.get("token_uri", "https://oauth2.googleapis.com/token")

        import http.server
        import urllib.parse
        import webbrowser

        auth_code_holder: dict[str, str] = {}

        class OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                query = parse_qs(urlparse(self.path).query)
                if "code" in query:
                    auth_code_holder["code"] = query["code"][0]
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(b"<h1>JobPilot Authentication Successful!</h1><p>You can close this window.</p>")
                else:
                    self.send_response(400)
                    self.end_headers()

            def log_message(self, format: str, *args: Any) -> None:
                pass  # Silence server logs

        # Bind to localhost on an available port
        ports_to_try = [8080, 8081, 8082, 8085, 8888, 0]
        server = None
        port = None
        for p in ports_to_try:
            try:
                server = http.server.HTTPServer(("localhost", p), OAuthCallbackHandler)
                port = server.server_port
                break
            except OSError:
                continue

        if server is None or port is None:
            raise RuntimeError("Could not bind to any local port for OAuth callback server.")

        redirect_uri = f"http://localhost:{port}"

        # Open browser to consent page
        auth_params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": self.scope,
            "access_type": "offline",
            "prompt": "consent",
        }
        consent_url = f"{auth_uri}?{urllib.parse.urlencode(auth_params)}"
        print(f"\n🔑 Opening browser for Gmail authorization...\nIf it does not open, visit:\n{consent_url}\n")
        webbrowser.open(consent_url)

        # Wait for callback
        while "code" not in auth_code_holder:
            server.handle_request()

        server.server_close()
        code = auth_code_holder["code"]

        # Exchange code for tokens
        resp = httpx.post(
            token_uri,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
            timeout=10.0,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Failed to exchange authorization code: {resp.text}")

        saved = self._save_token(resp.json())
        return saved.get("access_token", "")


# ---------------------------------------------------------------------------
# Ingestion Orchestration
# ---------------------------------------------------------------------------

@dataclass
class AlertIngestSummary:
    """Summary statistics of alert email ingestion."""

    messages_scanned: int = 0
    messages_processed: int = 0
    messages_skipped_duplicate: int = 0
    sender_counts: dict[str, int] = field(default_factory=dict)
    total_listings: int = 0
    new_jobs: int = 0
    duplicates: int = 0
    needs_jd: int = 0
    filtered_out: int = 0
    error: str | None = None


def ingest_gmail_alerts(
    conn: sqlite3.Connection,
    config: AppConfig | None = None,
    client: GmailOAuthClient | None = None,
    test_messages: list[dict[str, Any]] | None = None,
) -> AlertIngestSummary:
    """Read unread/unprocessed job alert emails from Gmail and ingest them into the DB.

    Idempotent: skips messages that have already been recorded in gmail_processed_messages.
    Jobs that pass prefilter receive status 'needs_jd'.
    """
    if config is None:
        config = load_config()

    summary = AlertIngestSummary()
    messages_to_process: list[dict[str, Any]] = []

    # 1. Fetch messages (from test fixture injection or Gmail API)
    if test_messages is not None:
        messages_to_process = test_messages
    else:
        if not config.sources.gmail.enabled:
            logger.info("Gmail alerts source is disabled.")
            return summary

        if client is None:
            client = GmailOAuthClient()

        try:
            token = client.get_access_token()
        except Exception as exc:
            logger.error("Could not obtain Gmail access token: %s", exc)
            summary.error = f"Auth failed ({exc})"
            return _persist_gmail_run(conn, summary)

        label = config.sources.gmail.label
        senders = config.sources.gmail.senders
        parts: list[str] = []
        if label:
            parts.append(f"label:{label}")
        if senders:
            sender_q = " OR ".join(f"from:{s}" for s in senders)
            parts.append(f"({sender_q})")
        # Either the label or a known alert sender is enough. Requiring both
        # drops LinkedIn and Naukri mail that was never given the label.
        q = " OR ".join(parts)

        with httpx.Client(timeout=15.0, headers={"Authorization": f"Bearer {token}"}) as http_client:
            resp = http_client.get(f"{GMAIL_API_BASE}/messages", params={"q": q, "maxResults": 50})

            # A 401 means the stored token is no longer accepted; refresh once and retry.
            if resp.status_code == 401:
                logger.info("Gmail rejected the stored token. Refreshing and retrying once.")
                try:
                    token = client.get_access_token(force_refresh=True)
                except Exception as exc:
                    logger.error("Could not refresh Gmail access token: %s", exc)
                    summary.error = f"Auth failed ({exc})"
                    return _persist_gmail_run(conn, summary)
                http_client.headers["Authorization"] = f"Bearer {token}"
                resp = http_client.get(f"{GMAIL_API_BASE}/messages", params={"q": q, "maxResults": 50})

            if resp.status_code != 200:
                logger.warning("Gmail API list messages failed: HTTP %d: %s", resp.status_code, resp.text)
                if resp.status_code == 401:
                    summary.error = (
                        "Gmail authentication failed (HTTP 401). Delete token.json and run "
                        "'jobpilot ingest-alerts' to re-authorize."
                    )
                else:
                    summary.error = f"Gmail API error (HTTP {resp.status_code})"
                return _persist_gmail_run(conn, summary)

            msg_list = resp.json().get("messages", [])
            for m in msg_list:
                m_id = m.get("id")
                if not m_id or is_gmail_message_processed(conn, m_id):
                    summary.messages_skipped_duplicate += 1
                    continue

                # Fetch full message
                m_resp = http_client.get(f"{GMAIL_API_BASE}/messages/{m_id}", params={"format": "full"})
                if m_resp.status_code == 200:
                    messages_to_process.append(m_resp.json())

    summary.messages_scanned = len(messages_to_process) + summary.messages_skipped_duplicate

    # 2. Process each email message
    all_raw_jobs: list[RawJob] = []

    for msg in messages_to_process:
        msg_id = msg.get("id", "")
        if not msg_id:
            continue

        if is_gmail_message_processed(conn, msg_id):
            summary.messages_skipped_duplicate += 1
            continue

        # Extract headers and body
        sender = msg.get("sender") or ""
        html_content = msg.get("html") or ""

        if not html_content and "payload" in msg:
            payload = msg["payload"]
            for header in payload.get("headers", []):
                if header.get("name", "").lower() == "from":
                    sender = header.get("value", "")

            # Extract HTML body from parts
            html_content = _extract_html_from_payload(payload)

        # Parse alerts into RawJobs
        jobs = parse_alert_email(sender, html_content, msg_id)
        sender_key = _sender_bucket(sender, jobs)
        summary.sender_counts[sender_key] = summary.sender_counts.get(sender_key, 0) + len(jobs)
        all_raw_jobs.extend(jobs)

        # Mark message as processed for idempotency
        record_processed_gmail_message(conn, msg_id, sender=sender, job_count=len(jobs))
        summary.messages_processed += 1

    summary.total_listings = len(all_raw_jobs)

    # 3. Ingest jobs and run prefilter
    if all_raw_jobs:
        ingest_res = ingest_raw_jobs(conn, all_raw_jobs)
        summary.new_jobs = ingest_res.new
        summary.duplicates = ingest_res.duplicates

        if ingest_res.job_ids:
            prefilter_res = run_prefilter(conn, job_ids=ingest_res.job_ids)
            summary.filtered_out = prefilter_res.filtered_out
            summary.needs_jd = prefilter_res.needs_jd

    return _persist_gmail_run(conn, summary)


def _persist_gmail_run(conn: sqlite3.Connection, summary: AlertIngestSummary) -> AlertIngestSummary:
    completed = datetime.now(UTC).isoformat()
    record_source_run(
        conn,
        "gmail",
        started_at=completed,
        completed_at=completed,
        status="failed" if summary.error else "ok",
        found_count=summary.total_listings,
        new_count=summary.new_jobs,
        updated_count=summary.duplicates,
        error=summary.error,
    )
    return summary


def _sender_bucket(sender: str, jobs: list[RawJob]) -> str:
    """Group alert counts by the board the parser recognized."""
    if jobs:
        source = jobs[0].source
        if source.startswith("gmail:"):
            return source.split(":", 1)[1]
    sender_lower = (sender or "").lower()
    for name in ("linkedin", "naukri", "instahyre"):
        if name in sender_lower:
            return name
    return sender or "unknown"


def _extract_html_from_payload(payload: dict[str, Any]) -> str:
    """Recursively search for text/html body in Gmail message payload."""
    mime_type = payload.get("mimeType", "")
    body_data = payload.get("body", {}).get("data", "")
    if "text/html" in mime_type and body_data:
        try:
            return base64.urlsafe_b64decode(body_data).decode("utf-8", errors="replace")
        except Exception:
            pass

    for part in payload.get("parts", []):
        html = _extract_html_from_payload(part)
        if html:
            return html

    return ""
