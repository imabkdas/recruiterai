"""Per-job resume assembly for JobPilot.

Builds a job-tailored resume from data/resume_base.yaml and data/profile.yaml.
Markdown is rendered in code. The PDF is the Overleaf source in
data/templates/createResume.tex, compiled with pdflatex. A tailored summary,
when one was passed in, replaces only the Professional Summary section.

Selection is deterministic code: bullets are ordered by overlap with the job's
required and preferred skills. The LLM is optional and only ever supplies the
summary paragraph, so a resume can always be produced with zero API calls.

The evidence rules from Section 7.5 of the design doc are enforced structurally
rather than by prompting:
- professional bullets are the only content under Experience
- project and experiment bullets sit under their own headings
- project / certification / experimental skills are labelled, never listed as
  plain professional skills
- LEARNING and UNKNOWN skills and anything in known_gaps never appear
- the employer is never rendered as the client
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from jobpilot.models import (
    Bullet,
    EducationEntry,
    EvidenceLevel,
    JobAnalysis,
    Profile,
    ResumeBase,
    Skill,
)
from jobpilot.profile.skills import normalize_skill

logger = logging.getLogger(__name__)

MAX_PROJECTS = 3

# profile.yaml category keys are snake_case; these read badly when title-cased.
# The first block mirrors the "Technical Skills" groups on the candidate's own
# resume; the rest are kept so an older profile.yaml still renders sensibly.
_CATEGORY_LABELS = {
    "languages": "Languages",
    "backend": "Backend",
    "frontend": "Frontend",
    "databases": "Databases",
    "cloud_devops": "Cloud & DevOps",
    "testing_monitoring": "Testing & Monitoring",
    "tools": "Tools",
    "security": "Security & Identity",
    "process": "Process",
    "ai": "AI & GenAI",
    "mobile": "Mobile",
    "cicd": "CI/CD",
    "ci_cd": "CI/CD",
    "cloud_infra": "Cloud & Infrastructure",
    "core_backend": "Core Backend",
    "monitoring": "Monitoring",
    "testing": "Testing",
}

# Order the skills lines appear in, matching the resume template. Categories not
# listed here follow, alphabetically.
_CATEGORY_ORDER = (
    "languages", "core_backend", "backend", "frontend", "databases",
    "cloud_devops", "cloud_infra", "cicd", "ci_cd", "testing_monitoring",
    "testing", "monitoring", "tools", "security", "process", "ai", "mobile",
)

# Evidence levels that may be named in a resume, and how each must be qualified.
_SKILL_LEVEL_LABELS: dict[EvidenceLevel, str] = {
    EvidenceLevel.VERIFIED_PROFESSIONAL: "",
    EvidenceLevel.VERIFIED_PROJECT: "personal projects",
    EvidenceLevel.VERIFIED_CERTIFICATION: "certified",
    EvidenceLevel.EXPERIMENTAL: "explored in prototypes",
}

# Printed form of each qualifier, and the order the lines appear in.
_QUALIFIED_DISPLAY = {
    "certified": "Certified",
    "personal projects": "Personal Projects",
    "explored in prototypes": "Explored in Prototypes",
}

# Overleaf source. Contact details live only in this file, which is gitignored.
_LATEX_TEMPLATE = Path(__file__).resolve().parents[3] / "data" / "templates" / "createResume.tex"

# Inserted text is plain prose. Each character is replaced on its own so the
# backslash we emit is not escaped a second time.
_LATEX_SPECIAL = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}

_SUMMARY_SECTION = re.compile(
    r"(\\section\*\{Professional Summary\}\n)(.*?)(\n\\section\*\{)",
    re.DOTALL,
)

# Phrases the candidate's own summary sets in bold. Applied only when the
# text actually contains them, so an LLM rewrite is left untouched otherwise.
_EMPHASIS_PATTERNS = (
    re.compile(r"\d+ years of experience"),
    re.compile(r"Banking & Financial Services \(BFS\)"),
    re.compile(r"\bRetail\b"),
)


@dataclass
class RoleSection:
    """One employment entry with its selected bullets.

    title / employer / client_note are kept apart because the template prints
    the title and period on one row and the employer on an italic row beneath.
    """

    title: str
    employer: str
    period: str
    client_note: str = ""
    domains: list[str] = field(default_factory=list)
    bullets: list[Bullet] = field(default_factory=list)

    @property
    def heading(self) -> str:
        """Single-line form, used by the Markdown rendering."""
        parts = [p for p in (self.title, self.employer) if p]
        head = " — ".join(parts) if parts else "Experience"
        return f"{head} (client: {self.client_note})" if self.client_note else head

    @property
    def employer_line(self) -> str:
        """Italic line beneath the title. Never names the client as the employer."""
        if self.client_note:
            return f"{self.employer} (client: {self.client_note})"
        return self.employer


@dataclass
class ProjectSection:
    """One project or experiment entry with its bullets."""

    name: str
    stack: list[str] = field(default_factory=list)
    bullets: list[Bullet] = field(default_factory=list)


@dataclass
class ResumeContent:
    """Everything chosen for one job's resume, before rendering."""

    name: str
    location: str
    contact_lines: list[str]
    summary: str
    professional_skills: dict[str, list[str]]
    qualified_skills: dict[str, list[str]]
    roles: list[RoleSection]
    projects: list[ProjectSection]
    achievements: list[str]
    certifications: list[str]
    education: list[EducationEntry] = field(default_factory=list)

    def bullet_ids(self) -> list[str]:
        ids = [b.id for r in self.roles for b in r.bullets]
        ids += [b.id for p in self.projects for b in p.bullets]
        return ids


@dataclass
class ResumeDocument:
    """Result of building and writing one job's resume."""

    job_id: int
    summary: str
    markdown: str
    bullet_ids: list[str]
    markdown_path: Path | None = None
    pdf_path: Path | None = None
    tailored: bool = False
    pdf_error: str | None = None


# ---------------------------------------------------------------------------
# Relevance scoring (deterministic)
# ---------------------------------------------------------------------------


def _jd_skill_sets(analysis: JobAnalysis) -> tuple[set[str], set[str]]:
    """Return normalised (required, preferred) skill name sets from the JD."""
    required = {normalize_skill(s).lower() for s in analysis.required_skills if s.strip()}
    preferred = {normalize_skill(s).lower() for s in analysis.preferred_skills if s.strip()}
    return required, preferred


def score_bullet_relevance(
    bullet: Bullet,
    required: set[str],
    preferred: set[str],
    analysis: JobAnalysis,
) -> float:
    """Score one bullet against a job. Required skills weigh most, then preferred, then tags."""
    bullet_skills = {normalize_skill(s).lower() for s in bullet.skills}
    score = 3.0 * len(bullet_skills & required) + 1.5 * len(bullet_skills & preferred)

    # Tags catch JD themes the skill list misses, e.g. "batch" or "observability".
    jd_text = " ".join(
        [analysis.normalized_title, analysis.responsibilities_summary]
        + analysis.required_skills
        + analysis.preferred_skills
    ).lower()
    for tag in bullet.tags:
        if tag and re.search(rf"\b{re.escape(tag.lower())}\b", jd_text):
            score += 0.5

    # Quantified bullets read better; use only as a tiebreaker.
    if bullet.metrics:
        score += 0.1

    return score


# ---------------------------------------------------------------------------
# Content selection
# ---------------------------------------------------------------------------


def _is_claimable(skill: Skill, known_gaps: set[str]) -> bool:
    """A skill may appear only with real evidence and only if not a declared gap."""
    if normalize_skill(skill.name).lower() in known_gaps:
        return False
    return skill.level in _SKILL_LEVEL_LABELS


def select_skills(
    profile: Profile,
    analysis: JobAnalysis,
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Split claimable skills into professional and qualified groups, JD matches first.

    Returns (professional_by_category, qualified_by_label).
    """
    required, preferred = _jd_skill_sets(analysis)
    relevant = required | preferred
    known_gaps = {normalize_skill(g).lower() for g in profile.known_gaps}

    professional: dict[str, list[tuple[int, str]]] = {}
    qualified: dict[str, list[tuple[int, str]]] = {}

    for skill in profile.skills:
        if not _is_claimable(skill, known_gaps):
            continue
        # 0 sorts before 1, so JD-relevant skills lead each line.
        rank = 0 if normalize_skill(skill.name).lower() in relevant else 1
        label = _SKILL_LEVEL_LABELS[skill.level]
        if label:
            qualified.setdefault(label, []).append((rank, skill.name))
        else:
            category = skill.category or "Other"
            professional.setdefault(category, []).append((rank, skill.name))

    def _flatten(
        groups: dict[str, list[tuple[int, str]]], order: tuple[str, ...]
    ) -> dict[str, list[str]]:
        # Dicts keep insertion order, so inserting by `order` fixes the line
        # order the renderers print without them needing to sort.
        ranked = sorted(groups, key=lambda k: (order.index(k) if k in order else len(order), k))
        return {
            key: [name for _, name in sorted(groups[key], key=lambda v: (v[0], v[1].lower()))]
            for key in ranked
        }

    return (
        _flatten(professional, _CATEGORY_ORDER),
        _flatten(qualified, tuple(_QUALIFIED_DISPLAY)),
    )


def qualified_label(key: str) -> str:
    """Printed form of an evidence qualifier, e.g. 'certified' -> 'Certified'."""
    return _QUALIFIED_DISPLAY.get(key, key.capitalize())


def category_label(category: str) -> str:
    """Turn a profile.yaml category key into a resume heading."""
    key = (category or "").strip().lower()
    if key in _CATEGORY_LABELS:
        return _CATEGORY_LABELS[key]
    return key.replace("_", " ").title() if key else "Other"


def _is_backend_role(job_title: str) -> bool:
    """True when the title reads as backend rather than full-stack, frontend or mobile."""
    title = job_title.lower()
    non_backend = (
        "full stack", "fullstack", "full-stack",
        "frontend", "front-end", "mobile", "android", "ios",
    )
    return not any(kw in title for kw in non_backend)


def _client_note(role_client: str, profile: Profile) -> str:
    """How the client may be referred to, if at all. Never used as the employer."""
    if not role_client:
        return ""
    if profile.experience.use_client_name:
        return role_client
    return profile.experience.client_display or ""


def select_content(
    profile: Profile,
    resume_base: ResumeBase,
    analysis: JobAnalysis,
    job_title: str,
    summary: str,
) -> ResumeContent:
    """Choose and order every section of a resume for one job."""
    required, preferred = _jd_skill_sets(analysis)

    # Experience: keep every professional bullet, most relevant first. A resume
    # should never hide employment history, only reorder its emphasis.
    roles: list[RoleSection] = []
    for role in resume_base.roles:
        ranked = sorted(
            (b for b in role.bullets if b.type == "professional"),
            key=lambda b: score_bullet_relevance(b, required, preferred, analysis),
            reverse=True,
        )
        if not ranked:
            continue
        # Without a start date there is no period worth printing; "Present" alone reads as an error.
        period = f"{role.start} – {role.end or 'Present'}" if role.start else ""
        roles.append(
            RoleSection(
                title=role.title or "Software Engineer",
                employer=role.employer or profile.experience.current_employer,
                period=period,
                client_note=_client_note(role.client, profile),
                domains=list(role.domains),
                bullets=ranked,
            )
        )

    # Projects: only those that speak to this job, capped so the resume stays tight.
    backend_role = _is_backend_role(job_title)
    scored_projects: list[tuple[float, ProjectSection]] = []
    for proj in resume_base.projects:
        if not proj.bullets:
            continue
        # A mobile side project does nothing for a backend application.
        if backend_role and (proj.relevance or "").lower() == "low_for_backend":
            continue
        best = max(
            score_bullet_relevance(b, required, preferred, analysis) for b in proj.bullets
        )
        scored_projects.append((
            best,
            ProjectSection(
                name=proj.name, stack=list(proj.stack), bullets=list(proj.bullets)
            ),
        ))

    scored_projects.sort(key=lambda t: t[0], reverse=True)
    projects = [sec for score, sec in scored_projects[:MAX_PROJECTS] if score > 0]
    # With no skill overlap at all, still show the strongest few for context.
    if not projects:
        projects = [sec for _, sec in scored_projects[:1]]

    contact_lines = [v for v in (profile.identity.contact or {}).values() if v and v.strip()]
    professional_skills, qualified_skills = select_skills(profile, analysis)

    return ResumeContent(
        name=profile.identity.name,
        location=profile.identity.base_location,
        contact_lines=contact_lines,
        summary=summary,
        professional_skills=professional_skills,
        qualified_skills=qualified_skills,
        roles=roles,
        projects=projects,
        achievements=[a.text for a in resume_base.achievements],
        certifications=[c.name for c in resume_base.certifications],
        education=list(resume_base.education),
    )


def choose_base_summary(resume_base: ResumeBase, job_title: str) -> str:
    """Pick the stock summary variant that best fits the role family."""
    if not resume_base.summary_variants:
        return ""
    title = job_title.lower()
    wants_fullstack = any(
        kw in title for kw in ("full stack", "fullstack", "full-stack", "frontend", "react")
    )
    for variant in resume_base.summary_variants:
        is_fullstack_variant = "fullstack" in variant.id.lower() or "full" in variant.id.lower()
        if wants_fullstack == is_fullstack_variant:
            return variant.text
    return resume_base.summary_variants[0].text


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_markdown(content: ResumeContent) -> str:
    """Render resume content as Markdown, in the same section order as the PDF."""
    lines: list[str] = [f"# {content.name}"]

    # The template header is contact only; location stays in the profile.
    if content.contact_lines:
        lines.append(" | ".join(content.contact_lines))

    if content.summary:
        lines += ["", "## Professional Summary", "", _emphasize_markdown(content.summary.strip())]

    if content.professional_skills or content.qualified_skills:
        lines += ["", "## Technical Skills", ""]
        for category, names in content.professional_skills.items():
            lines.append(f"- **{category_label(category)}:** {', '.join(names)}")
        for label, names in content.qualified_skills.items():
            lines.append(f"- **{qualified_label(label)}:** {', '.join(names)}")

    if content.roles:
        lines += ["", "## Professional Experience", ""]
        for role in content.roles:
            lines.append(f"### {role.heading}")
            if role.period:
                lines.append(f"*{role.period}*")
            if role.domains:
                lines.append(f"**Domains:** {' — '.join(role.domains)}")
            lines.append("")
            lines += [f"- {b.text}" for b in role.bullets]
            lines.append("")

    if content.projects:
        lines += ["## Projects", ""]
        for proj in content.projects:
            stack = f" — *{' — '.join(proj.stack)}*" if proj.stack else ""
            lines.append(f"### {proj.name}{stack}")
            lines += [f"- {b.text}" for b in proj.bullets]
            lines.append("")

    if content.education:
        lines += ["## Education", ""]
        for edu in content.education:
            lines.append(f"### {edu.institution}")
            detail = " — ".join(p for p in (edu.degree, edu.detail) if p)
            if detail:
                lines.append(detail)
            lines.append("")

    if content.certifications:
        lines += ["## Certifications", ""]
        lines += [f"- {c}" for c in content.certifications]
        lines.append("")

    if content.achievements:
        lines += ["## Achievements", ""]
        lines += [f"- {a}" for a in content.achievements]
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _emphasis_spans(text: str) -> list[tuple[int, int]]:
    """Non-overlapping ranges of `text` that the template sets in bold."""
    spans = [m.span() for pat in _EMPHASIS_PATTERNS for m in pat.finditer(text)]
    if not spans:
        return []
    spans.sort()
    merged = [spans[0]]
    for start, end in spans[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def _emphasize_markdown(text: str) -> str:
    """Wrap the template's bold summary phrases in Markdown `**`."""
    spans = _emphasis_spans(text)
    if not spans:
        return text
    parts: list[str] = []
    cursor = 0
    for start, end in spans:
        parts.append(text[cursor:start])
        parts.append(f"**{text[start:end]}**")
        cursor = end
    parts.append(text[cursor:])
    return "".join(parts)


def escape_latex(text: str) -> str:
    """Escape LaTeX specials in prose inserted into the template."""
    return "".join(_LATEX_SPECIAL.get(ch, ch) for ch in text)


def substitute_professional_summary(template: str, summary: str | None) -> str:
    """Replace the Professional Summary body, or return the template unchanged.

    Experience, skills, projects, education, certifications, and the contact
    header stay exactly as they are in createResume.tex. A missing or blank
    summary leaves the Overleaf paragraph in place.
    """
    if summary is None or not summary.strip():
        return template
    match = _SUMMARY_SECTION.search(template)
    if match is None:
        raise ValueError("Professional Summary section not found in createResume.tex")
    body = escape_latex(summary.strip())
    return (
        template[: match.start()]
        + match.group(1)
        + "\n"
        + body
        + "\n"
        + match.group(3)
        + template[match.end() :]
    )


def render_pdf(out_path: Path, summary: str | None = None) -> None:
    """Write a per-job .tex from the Overleaf template and compile it.

    `summary` replaces only the Professional Summary section. Pass None to
    compile the template unchanged.
    """
    if not _LATEX_TEMPLATE.is_file():
        raise FileNotFoundError(f"Resume template not found: {_LATEX_TEMPLATE}")
    source = _LATEX_TEMPLATE.read_text(encoding="utf-8")
    rendered = substitute_professional_summary(source, summary)
    tex_path = out_path.with_suffix(".tex")
    tex_path.parent.mkdir(parents=True, exist_ok=True)
    tex_path.write_text(rendered, encoding="utf-8")
    _compile_latex(tex_path)


# BasicTeX installs here and does not add itself to PATH for GUI launches.
_PDFLATEX_FALLBACKS = ("/Library/TeX/texbin/pdflatex",)


def _find_pdflatex() -> str | None:
    """Return a pdflatex binary, including the MacTeX location off PATH."""
    found = shutil.which("pdflatex")
    if found:
        return found
    for candidate in _PDFLATEX_FALLBACKS:
        if Path(candidate).is_file():
            return candidate
    return None


def _compile_latex(tex_path: Path) -> None:
    """Run pdflatex on `tex_path`. The PDF is written beside the source."""
    binary = _find_pdflatex()
    if binary is None:
        raise RuntimeError(
            "pdflatex is not installed, so the resume cannot be compiled. "
            "Install a TeX distribution (MacTeX or TeX Live) and retry."
        )
    result = subprocess.run(
        [
            binary,
            "-interaction=nonstopmode",
            "-halt-on-error",
            "-no-shell-escape",
            tex_path.name,
        ],
        cwd=tex_path.parent,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    pdf_path = tex_path.with_suffix(".pdf")
    if result.returncode != 0 or not pdf_path.is_file():
        log_path = tex_path.with_suffix(".log")
        if log_path.is_file():
            detail = log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
        else:
            detail = (result.stdout or result.stderr or "")[-2000:]
        raise RuntimeError(f"pdflatex failed for {tex_path.name}: {detail}")
    for suffix in (".aux", ".log", ".out"):
        side = tex_path.with_suffix(suffix)
        if side.is_file():
            side.unlink()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def resume_tex_path(job_id: int, company: str, title: str) -> Path:
    """Path of the per-job LaTeX file, next to its compiled PDF."""
    out_dir = _LATEX_TEMPLATE.parent.parent / "out" / "resumes"
    stem = f"{job_id}_{_slug(company)}_{_slug(title, 30)}"
    return out_dir / f"{stem}.tex"


def base_resume_tex_path() -> Path:
    """The Overleaf source edited from the sidebar, not tied to a job."""
    return _LATEX_TEMPLATE


def read_resume_source(job_tex: Path | None = None) -> str:
    """Return the job's saved source, or the Overleaf template if none exists."""
    if job_tex is not None and job_tex.is_file():
        return job_tex.read_text(encoding="utf-8")
    if not _LATEX_TEMPLATE.is_file():
        raise FileNotFoundError(f"Resume template not found: {_LATEX_TEMPLATE}")
    return _LATEX_TEMPLATE.read_text(encoding="utf-8")


def _short_latex_error(detail: str) -> str:
    """Keep the pdflatex lines that say what to fix."""
    lines = detail.splitlines()
    useful = [ln for ln in lines if ln.startswith("!") or ln.startswith("l.")]
    chosen = useful[-8:] if useful else lines[-12:]
    text = "\n".join(chosen).strip()
    return text[:800] or "pdflatex failed."


_WRAP_PREAMBLE = (
    "\\sloppy\n"
    "\\setlength{\\emergencystretch}{3em}\n"
    "\\hyphenpenalty=0\n"
    "\\exhyphenpenalty=0\n"
)
_LONG_RUN = re.compile(r"(?<!\\)[A-Za-z0-9]{18,}")
_LETTER_COMMA = re.compile(r"(?<=[A-Za-z]),(?=[A-Za-z0-9])")


def _break_token(token: str, every: int = 12) -> str:
    """Let TeX wrap a long unbroken word instead of running it off the page."""
    pieces = [token[i : i + every] for i in range(0, len(token), every)]
    return r"\hspace{0pt}".join(pieces)


def _break_long_runs(text: str) -> str:
    text = _LETTER_COMMA.sub(r",\\hspace{0pt}", text)
    return _LONG_RUN.sub(lambda match: _break_token(match.group(0)), text)


def prepare_latex_wrapping(source: str) -> str:
    """Return compile-only LaTeX that wraps long summary and skill lines.

    The editor keeps the source the user typed. Break points are added only
    in the file sent to pdflatex.
    """
    text = source
    if "\\emergencystretch" not in text:
        text = re.sub(
            r"(\\documentclass(?:\[[^\]]*\])?\{[^}]+\}\n)",
            lambda match: match.group(1) + _WRAP_PREAMBLE,
            text,
            count=1,
        )
    marker = r"\begin{document}"
    begin = text.find(marker)
    if begin == -1:
        return _break_long_runs(text)
    cut = begin + len(marker)
    return text[:cut] + _break_long_runs(text[cut:])


def compile_resume_source(source: str, tex_path: Path) -> Path:
    """Write `source` to `tex_path` and compile it. Returns the PDF path."""
    text = source.replace("\r\n", "\n")
    if not text.strip():
        raise ValueError("Resume source is empty.")
    if "\\documentclass" not in text:
        raise ValueError("Resume source must include \\documentclass.")
    tex_path.parent.mkdir(parents=True, exist_ok=True)
    tex_path.write_text(text, encoding="utf-8")
    compile_path = tex_path.with_name(f"{tex_path.stem}.compile.tex")
    compile_path.write_text(prepare_latex_wrapping(text), encoding="utf-8")
    try:
        _compile_latex(compile_path)
    except RuntimeError as exc:
        raise ValueError(_short_latex_error(str(exc))) from exc
    finally:
        for suffix in (".aux", ".log", ".out", ".tex"):
            side = compile_path.with_suffix(suffix)
            if suffix == ".tex" and side == tex_path:
                continue
            if side.is_file() and side != tex_path.with_suffix(".pdf"):
                side.unlink()
    compiled_pdf = compile_path.with_suffix(".pdf")
    pdf_path = tex_path.with_suffix(".pdf")
    if compiled_pdf.is_file() and compiled_pdf != pdf_path:
        compiled_pdf.replace(pdf_path)
    return pdf_path


def _slug(text: str, limit: int = 40) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", (text or "unknown").lower()).strip("_")
    return (cleaned or "unknown")[:limit]


def build_resume(
    job_id: int,
    job: dict,
    profile: Profile,
    resume_base: ResumeBase,
    analysis: JobAnalysis,
    summary: str | None = None,
    out_dir: Path | None = None,
    write_pdf: bool = True,
) -> ResumeDocument:
    """Assemble and write a job-tailored resume.

    Pass summary to use an LLM-tailored paragraph in both the Markdown and the
    PDF. The PDF is the Overleaf template: that paragraph replaces only the
    Professional Summary section. With no summary, the template text is
    compiled unchanged and Markdown uses the closest stock variant. No API
    call is made either way.
    """
    job_title = job.get("title") or analysis.normalized_title
    tailored = bool(summary and summary.strip())
    resolved_summary = (
        summary.strip() if tailored else choose_base_summary(resume_base, job_title)
    )

    content = select_content(
        profile=profile,
        resume_base=resume_base,
        analysis=analysis,
        job_title=job_title,
        summary=resolved_summary,
    )
    markdown = render_markdown(content)

    doc = ResumeDocument(
        job_id=job_id,
        summary=resolved_summary,
        markdown=markdown,
        bullet_ids=content.bullet_ids(),
        tailored=tailored,
    )

    if out_dir is None:
        return doc

    stem = f"{job_id}_{_slug(job.get('company', ''))}_{_slug(job_title, 30)}"
    out_dir.mkdir(parents=True, exist_ok=True)

    md_path = out_dir / f"{stem}.md"
    md_path.write_text(markdown, encoding="utf-8")
    doc.markdown_path = md_path

    if write_pdf:
        pdf_path = out_dir / f"{stem}.pdf"
        try:
            # Only an explicitly passed summary is written into the template.
            render_pdf(pdf_path, summary=resolved_summary if tailored else None)
            doc.pdf_path = pdf_path
        except Exception as exc:
            # A missing TeX install must not cost us the Markdown resume.
            logger.warning("PDF rendering failed for job %d: %s", job_id, exc)
            doc.pdf_error = str(exc)

    return doc
