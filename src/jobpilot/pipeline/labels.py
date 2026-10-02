"""Turn stored company and title strings into readable labels.

Hacker News comments often arrive as HTML, and posts that skip the
``Company | Title`` header leave a sentence fragment in the company field.
"""

from __future__ import annotations

import html
import re

_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
_DOMAIN = re.compile(
    r"^([a-z0-9](?:[a-z0-9-]{0,40}[a-z0-9])?)\.(?:com|io|org|ai|dev|co|net|app)\b",
    re.IGNORECASE,
)
_SENTENCE = re.compile(
    r"^(it['’]s|we['’]re|i['’]m|this is|please|join us|here['’]s)\b",
    re.IGNORECASE,
)


def plain_text(value: str | None) -> str:
    """Decode HTML entities, drop tags, and collapse whitespace."""
    if not value:
        return ""
    text = html.unescape(value)
    text = _TAG.sub(" ", text)
    text = html.unescape(text)
    return _SPACE.sub(" ", text).strip()


def _first_line(value: str | None) -> str:
    if not value:
        return ""
    text = html.unescape(value)
    text = _TAG.sub("\n", text)
    text = html.unescape(text)
    for line in text.splitlines():
        cleaned = _SPACE.sub(" ", line).strip()
        if cleaned:
            return cleaned
    return ""


def looks_like_company(name: str) -> bool:
    """True when a string reads as an organization name, not a sentence."""
    if not name or len(name) > 80:
        return False
    if _SENTENCE.search(name):
        return False
    if _DOMAIN.match(name):
        return False
    if len(name.split()) > 8:
        return False
    lowered = name.lower()
    return "we're" not in lowered and "we’re" not in lowered and "http" not in lowered


def brand_from_domain(text: str) -> str | None:
    """``whitecircle.com/careers`` becomes ``Whitecircle``."""
    match = _DOMAIN.match(text.strip())
    if not match:
        return None
    return match.group(1).replace("-", " ").title()


_MIDDOT = re.compile(r"\s*·\s*")
_LINKEDIN_NOISE = re.compile(
    r"(?:"
    r"actively recruiting|"
    r"easy apply|"
    r"\d[\d,]*\s*company alumni|"
    r"\d[\d,]*\s*school alumni|"
    r"\d[\d,]*\s*connections?"
    r")+\s*$",
    re.IGNORECASE,
)
# LinkedIn alert text glues the company onto the title: "EngineerAccenture".
_GLUE_BOUNDARY = re.compile(r"(?<=[a-z0-9)])(?=[A-Z])")
_ROMAN_GLUE = re.compile(
    r"^(.*(?:^|[\s-])(?:I{1,3}|IV|VI{0,3}|IX))([A-Z][a-z].+)$"
)


def _strip_linkedin_noise(value: str) -> str:
    return _LINKEDIN_NOISE.sub("", value).strip(" -–—")


def _inside_acronym_hump(text: str, index: int) -> bool:
    """True for the Chase hump inside ``JPMorganChase``."""
    j = index - 1
    while j >= 0 and text[j].islower():
        j -= 1
    if j < 0 or not text[j].isupper():
        return False
    run = 0
    while j >= 0 and text[j].isupper():
        run += 1
        j -= 1
    return run >= 2


def _split_glued_company(text: str) -> tuple[str, str | None]:
    """Split ``EngineerAccenture services`` into title and company."""
    match = next(
        (
            candidate
            for candidate in _GLUE_BOUNDARY.finditer(text)
            if not _inside_acronym_hump(text, candidate.start())
        ),
        None,
    )
    if match:
        title, company = text[: match.start()], text[match.start() :]
    else:
        roman = _ROMAN_GLUE.match(text)
        if not roman:
            return text, None
        title, company = roman.group(1), roman.group(2)
    title = title.strip(" -–—")
    company = company.strip()
    if title and looks_like_company(company) and any(ch.islower() for ch in company):
        return title, company
    return text, None


def _strip_trailing_location(title: str, location: str | None) -> str:
    """Drop a city that LinkedIn repeated at the end of the role name."""
    if not title or not location:
        return title
    city = re.split(r"\s*[(/|]", location, maxsplit=1)[0].strip()
    if len(city) < 3:
        return title
    stripped = re.sub(
        rf"[\s\-–—,]*{re.escape(city)}\s*$",
        "",
        title,
        count=1,
        flags=re.IGNORECASE,
    ).strip(" -–—,")
    return stripped or title


def listing_labels(
    company: str,
    title: str,
    location: str | None = None,
    description: str | None = None,
) -> tuple[str, str, str | None]:
    """Return company, role title, and location for a job list row.

    LinkedIn alerts store ``RoleCompany · City (Remote)Easy Apply`` in the
    title and leave location empty. The city belongs in the location column.
    """
    raw_title = plain_text(title)
    raw_company = plain_text(company)
    raw_location = plain_text(location) or None

    if "·" in raw_title:
        left, right = _MIDDOT.split(raw_title, maxsplit=1)
        extracted = _strip_linkedin_noise(right) or None
        raw_title = left.strip()
        if extracted and not raw_location:
            raw_location = extracted
        if raw_company.lower() in {"", "unknown"}:
            peeled_title, peeled_company = _split_glued_company(raw_title)
            if peeled_company:
                raw_title = peeled_title
                raw_company = peeled_company
        raw_title = _strip_trailing_location(raw_title, raw_location)

    company_out, title_out = readable_company_title(raw_company, raw_title, description)
    if raw_location:
        raw_location = raw_location[:80]
    return company_out, title_out, raw_location


def readable_company_title(
    company: str,
    title: str,
    description: str | None = None,
) -> tuple[str, str]:
    """Return a short company and title safe to show in a list."""
    stored_company = plain_text(company)
    stored_title = plain_text(title) or "Software Engineer"

    if looks_like_company(stored_company):
        return stored_company[:60], stored_title[:80]

    header = _first_line(description) or stored_company
    parts = [part.strip(" -") for part in header.split("|") if part.strip(" -")]
    if len(parts) >= 2 and looks_like_company(parts[0]):
        return parts[0][:60], parts[1][:80]

    brand = brand_from_domain(stored_company) or brand_from_domain(header)
    if brand:
        return brand[:60], stored_title[:80]

    return "Unknown", stored_title[:80]
