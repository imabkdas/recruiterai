"""Tests for pipeline.resume.py

The emphasis is on the claim rules from AGENTS.md: a skill may only ever appear
at its evidence level, project work must never read as professional experience,
and the client is never presented as the employer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jobpilot.models import (
    Achievement,
    Bullet,
    Certification,
    EvidenceLevel,
    Experience,
    Identity,
    JobAnalysis,
    Learning,
    Profile,
    ProjectEntry,
    ResumeBase,
    Role,
    Settings,
    Skill,
    SummaryVariant,
    Targets,
)
from jobpilot.pipeline.resume import (
    build_resume,
    choose_base_summary,
    compile_resume_source,
    escape_latex,
    prepare_latex_wrapping,
    render_markdown,
    score_bullet_relevance,
    select_content,
    select_skills,
    substitute_professional_summary,
)


@pytest.fixture
def profile() -> Profile:
    return Profile(
        schema_version=2,
        identity=Identity(
            name="Anand",
            base_location="India",
            contact={"email": "a@example.com", "phone": ""},
        ),
        experience=Experience(
            total_years_actual=3.9,
            years_for_forms=4,
            current_employer="Cognizant",
            current_client="JPMorgan",
            use_client_name=False,
            client_display="a global financial-services client",
        ),
        skills=[
            Skill(name="Java", category="core_backend", level=EvidenceLevel.VERIFIED_PROFESSIONAL),
            Skill(name="Kafka", category="core_backend", level=EvidenceLevel.VERIFIED_PROFESSIONAL),
            Skill(name="Azure", category="cloud_infra", level=EvidenceLevel.VERIFIED_CERTIFICATION),
            Skill(name="Kotlin", category="mobile", level=EvidenceLevel.VERIFIED_PROJECT),
            Skill(name="pgvector", category="ai", level=EvidenceLevel.EXPERIMENTAL),
            Skill(name="Rust", category="core_backend", level=EvidenceLevel.LEARNING),
            Skill(name="Haskell", category="core_backend", level=EvidenceLevel.UNKNOWN),
            Skill(name="Go", category="core_backend", level=EvidenceLevel.VERIFIED_PROFESSIONAL),
        ],
        known_gaps=["Go"],
        learning=Learning(topics=["Rust"]),
        settings=Settings(),
        targets=Targets(primary_titles=["Backend Engineer"], secondary_titles=[]),
    )


@pytest.fixture
def resume_base() -> ResumeBase:
    return ResumeBase(
        schema_version=2,
        summary_variants=[
            SummaryVariant(id="backend", text="Backend-focused summary."),
            SummaryVariant(id="fullstack", text="Full-stack summary."),
        ],
        roles=[
            Role(
                id="r1",
                title="Programmer Analyst",
                employer="Cognizant",
                client="JPMorgan",
                start="2021",
                end=None,
                bullets=[
                    Bullet(
                        id="b_kafka",
                        type="professional",
                        text="Built Kafka consumers in production.",
                        skills=["Kafka"],
                        metrics=["10%"],
                    ),
                    Bullet(
                        id="b_misc",
                        type="professional",
                        text="Participated in version upgrades.",
                        skills=[],
                        metrics=[],
                    ),
                ],
            )
        ],
        projects=[
            ProjectEntry(
                id="proj_chat",
                name="Chat App",
                relevance="low_for_backend",
                bullets=[
                    Bullet(
                        id="p_chat",
                        type="project",
                        text="Personal project: chat app using Kotlin.",
                        skills=["Kotlin"],
                        metrics=[],
                    )
                ],
            ),
            ProjectEntry(
                id="proj_vector",
                name="Vector Search",
                relevance="high",
                bullets=[
                    Bullet(
                        id="p_vector",
                        type="project",
                        text="Personal project: semantic search with pgvector.",
                        skills=["pgvector"],
                        metrics=[],
                    )
                ],
            ),
        ],
        achievements=[Achievement(id="a1", text="Star Performer award")],
        certifications=[Certification(id="c1", name="Microsoft Azure Fundamentals")],
    )


@pytest.fixture
def analysis() -> JobAnalysis:
    return JobAnalysis(
        normalized_title="Backend Engineer",
        required_skills=["Java", "Kafka"],
        preferred_skills=["Azure"],
        locations=["Remote"],
    )


def _markdown(profile, resume_base, analysis, job_title="Backend Engineer") -> str:
    content = select_content(
        profile=profile,
        resume_base=resume_base,
        analysis=analysis,
        job_title=job_title,
        summary="Summary.",
    )
    return render_markdown(content)


class TestSkillClaims:
    def test_professional_skills_are_unqualified(self, profile, analysis) -> None:
        professional, _ = select_skills(profile, analysis)
        flat = [s for skills in professional.values() for s in skills]
        assert "Java" in flat
        assert "Kafka" in flat

    def test_lower_evidence_skills_are_labelled_not_promoted(self, profile, analysis) -> None:
        professional, qualified = select_skills(profile, analysis)
        flat = [s for skills in professional.values() for s in skills]

        assert "Azure" not in flat
        assert "Kotlin" not in flat
        assert "pgvector" not in flat
        assert "Azure" in qualified["certified"]
        assert "Kotlin" in qualified["personal projects"]
        assert "pgvector" in qualified["explored in prototypes"]

    def test_learning_and_unknown_skills_never_appear(
        self, profile, resume_base, analysis
    ) -> None:
        """A skill being learned is not a claim and must not reach the page."""
        md = _markdown(profile, resume_base, analysis)
        assert "Rust" not in md
        assert "Haskell" not in md

    def test_known_gaps_are_excluded_even_when_professional(
        self, profile, resume_base, analysis
    ) -> None:
        md = _markdown(profile, resume_base, analysis)
        assert "Go" not in md.split("## Technical Skills")[1].split("##")[0]


class TestExperienceSection:
    def test_project_bullets_stay_out_of_experience(
        self, profile, resume_base, analysis
    ) -> None:
        content = select_content(
            profile=profile,
            resume_base=resume_base,
            analysis=analysis,
            job_title="Backend Engineer",
            summary="Summary.",
        )
        experience_ids = {b.id for role in content.roles for b in role.bullets}
        assert experience_ids == {"b_kafka", "b_misc"}

    def test_all_professional_bullets_are_kept(self, profile, resume_base, analysis) -> None:
        """Reordering emphasis is fine; dropping employment history is not."""
        content = select_content(
            profile=profile,
            resume_base=resume_base,
            analysis=analysis,
            job_title="Backend Engineer",
            summary="Summary.",
        )
        assert len(content.roles[0].bullets) == 2
        assert content.roles[0].bullets[0].id == "b_kafka"

    def test_client_is_not_presented_as_employer(
        self, profile, resume_base, analysis
    ) -> None:
        md = _markdown(profile, resume_base, analysis)
        assert "Cognizant" in md
        assert "JPMorgan" not in md
        assert "a global financial-services client" in md

    def test_client_name_shown_only_when_permitted(
        self, profile, resume_base, analysis
    ) -> None:
        profile.experience.use_client_name = True
        md = _markdown(profile, resume_base, analysis)
        assert "JPMorgan" in md

    def test_role_without_start_date_omits_period(
        self, profile, resume_base, analysis
    ) -> None:
        resume_base.roles[0].start = ""
        content = select_content(
            profile=profile,
            resume_base=resume_base,
            analysis=analysis,
            job_title="Backend Engineer",
            summary="Summary.",
        )
        assert content.roles[0].period == ""


class TestProjectSelection:
    def test_mobile_project_dropped_for_backend_role(
        self, profile, resume_base, analysis
    ) -> None:
        content = select_content(
            profile=profile,
            resume_base=resume_base,
            analysis=analysis,
            job_title="Senior Backend Developer",
            summary="Summary.",
        )
        names = [p.name for p in content.projects]
        assert "Chat App" not in names

    def test_mobile_project_kept_for_non_backend_role(
        self, profile, resume_base, analysis
    ) -> None:
        content = select_content(
            profile=profile,
            resume_base=resume_base,
            analysis=analysis,
            job_title="Android Engineer",
            summary="Summary.",
        )
        assert "Chat App" in [p.name for p in content.projects]


class TestBulletRelevance:
    def test_required_skill_outranks_preferred(self, analysis) -> None:
        required = Bullet(id="x", type="professional", text="Kafka work", skills=["Kafka"])
        preferred = Bullet(id="y", type="professional", text="Azure work", skills=["Azure"])
        req_set, pref_set = {"kafka"}, {"azure"}
        assert score_bullet_relevance(required, req_set, pref_set, analysis) > (
            score_bullet_relevance(preferred, req_set, pref_set, analysis)
        )

    def test_unrelated_bullet_scores_zero(self, analysis) -> None:
        bullet = Bullet(id="z", type="professional", text="Attended standups", skills=[])
        assert score_bullet_relevance(bullet, {"kafka"}, set(), analysis) == 0


class TestLatexSummary:
    def test_blank_summary_leaves_the_template_untouched(self) -> None:
        template = _TINY_TEMPLATE
        assert substitute_professional_summary(template, None) == template
        assert substitute_professional_summary(template, "   ") == template

    def test_escape_covers_latex_specials(self) -> None:
        assert escape_latex(r"a & b % c $ d # e _ f {g} ~h ^i \j") == (
            r"a \& b \% c \$ d \# e \_ f \{g\} \textasciitilde{}h "
            r"\textasciicircum{}i \textbackslash{}j"
        )


class TestSummaryChoice:
    def test_backend_role_picks_backend_variant(self, resume_base) -> None:
        assert choose_base_summary(resume_base, "Senior Backend Engineer") == (
            "Backend-focused summary."
        )

    def test_fullstack_role_picks_fullstack_variant(self, resume_base) -> None:
        assert choose_base_summary(resume_base, "Java Full Stack Developer") == (
            "Full-stack summary."
        )


_TINY_TEMPLATE = """\\section*{Professional Summary}

STOCK SUMMARY

\\section*{Technical Skills}

Cognizant
anand.abhishek269@gmail.com
Microsoft Certified: Azure Fundamentals (AZ-900)
"""


class TestLatexWrapping:
    def test_long_summary_and_skill_runs_can_break(self) -> None:
        source = (
            "\\documentclass{article}\n"
            "\\begin{document}\n"
            "Tailored summary for this role."
            + ("k" * 40)
            + "\n\n"
            "\\textbf{Backend:} Kafka,jkfdnjfextra\n"
            "\\end{document}\n"
        )
        wrapped = prepare_latex_wrapping(source)
        assert "\\emergencystretch" in wrapped
        assert r"\hspace{0pt}" in wrapped
        assert "k" * 40 not in wrapped
        assert r"Kafka,\hspace{0pt}jkfdnjfextra" in wrapped
        assert "\\textbf" in wrapped


class TestCompileResumeSource:
    def test_rejects_source_without_documentclass(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="documentclass"):
            compile_resume_source("just some text", tmp_path / "resume.tex")

    def test_writes_source_and_compiles(self, tmp_path: Path, monkeypatch) -> None:
        def fake_compile(tex_path: Path) -> None:
            tex_path.with_suffix(".pdf").write_bytes(b"%PDF-1.4\n")

        monkeypatch.setattr("jobpilot.pipeline.resume._compile_latex", fake_compile)
        tex = tmp_path / "resume.tex"
        pdf = compile_resume_source(
            "\\documentclass{article}\n\\begin{document}Hi\\end{document}\n",
            tex,
        )
        assert "Hi" in tex.read_text(encoding="utf-8")
        assert pdf.read_bytes().startswith(b"%PDF")


class TestBuildResume:
    def test_writes_markdown_and_pdf(
        self, profile, resume_base, analysis, tmp_path, monkeypatch
    ) -> None:
        template = tmp_path / "createResume.tex"
        template.write_text(_TINY_TEMPLATE, encoding="utf-8")
        monkeypatch.setattr("jobpilot.pipeline.resume._LATEX_TEMPLATE", template)

        def fake_compile(tex_path) -> None:
            tex_path.with_suffix(".pdf").write_bytes(b"%PDF-1.4\n")

        monkeypatch.setattr("jobpilot.pipeline.resume._compile_latex", fake_compile)
        out = tmp_path / "out"
        doc = build_resume(
            job_id=42,
            job={"company": "Acme Corp", "title": "Backend Engineer"},
            profile=profile,
            resume_base=resume_base,
            analysis=analysis,
            out_dir=out,
        )
        assert doc.markdown_path.exists()
        assert doc.pdf_path.exists()
        assert doc.pdf_error is None
        assert doc.pdf_path.read_bytes().startswith(b"%PDF")
        assert doc.markdown_path.name == "42_acme_corp_backend_engineer.md"
        # No tailored summary was passed, so the template paragraph stays.
        tex = doc.pdf_path.with_suffix(".tex").read_text(encoding="utf-8")
        assert "STOCK SUMMARY" in tex
        assert "Cognizant" in tex

    def test_tailored_summary_replaces_only_that_section(
        self, profile, resume_base, analysis, tmp_path, monkeypatch
    ) -> None:
        template = tmp_path / "createResume.tex"
        template.write_text(_TINY_TEMPLATE, encoding="utf-8")
        monkeypatch.setattr("jobpilot.pipeline.resume._LATEX_TEMPLATE", template)
        monkeypatch.setattr(
            "jobpilot.pipeline.resume._compile_latex",
            lambda tex_path: tex_path.with_suffix(".pdf").write_bytes(b"%PDF-1.4\n"),
        )
        doc = build_resume(
            job_id=7,
            job={"company": "Prenosis", "title": "Software Engineer"},
            profile=profile,
            resume_base=resume_base,
            analysis=analysis,
            summary="Tailored summary for this role. Cost is $5 & 100%_fit.",
            out_dir=tmp_path / "out",
        )
        tex = doc.pdf_path.with_suffix(".tex").read_text(encoding="utf-8")
        assert "STOCK SUMMARY" not in tex
        assert r"Tailored summary for this role. Cost is \$5 \& 100\%\_fit." in tex
        assert "Cognizant" in tex
        assert "anand.abhishek269@gmail.com" in tex
        assert "Microsoft Certified: Azure Fundamentals (AZ-900)" in tex

    def test_missing_pdflatex_is_reported(
        self, profile, resume_base, analysis, tmp_path, monkeypatch
    ) -> None:
        template = tmp_path / "createResume.tex"
        template.write_text(_TINY_TEMPLATE, encoding="utf-8")
        monkeypatch.setattr("jobpilot.pipeline.resume._LATEX_TEMPLATE", template)
        monkeypatch.setattr("jobpilot.pipeline.resume._find_pdflatex", lambda: None)
        doc = build_resume(
            job_id=1,
            job={"company": "Acme", "title": "Backend Engineer"},
            profile=profile,
            resume_base=resume_base,
            analysis=analysis,
            out_dir=tmp_path / "out",
        )
        assert doc.markdown_path.exists()
        assert doc.pdf_path is None
        assert doc.pdf_error is not None
        assert "pdflatex is not installed" in doc.pdf_error

    def test_no_llm_summary_means_not_tailored(
        self, profile, resume_base, analysis
    ) -> None:
        doc = build_resume(
            job_id=1,
            job={"company": "Acme", "title": "Backend Engineer"},
            profile=profile,
            resume_base=resume_base,
            analysis=analysis,
        )
        assert doc.tailored is False
        assert doc.summary == "Backend-focused summary."
        assert doc.markdown_path is None

    def test_llm_summary_is_used_when_supplied(
        self, profile, resume_base, analysis
    ) -> None:
        doc = build_resume(
            job_id=1,
            job={"company": "Acme", "title": "Backend Engineer"},
            profile=profile,
            resume_base=resume_base,
            analysis=analysis,
            summary="  Tailored for Acme.  ",
        )
        assert doc.tailored is True
        assert doc.summary == "Tailored for Acme."

    def test_blank_contact_fields_are_skipped(
        self, profile, resume_base, analysis
    ) -> None:
        content = select_content(
            profile=profile,
            resume_base=resume_base,
            analysis=analysis,
            job_title="Backend Engineer",
            summary="Summary.",
        )
        assert content.contact_lines == ["a@example.com"]

    def test_bullet_ids_cover_every_rendered_bullet(
        self, profile, resume_base, analysis
    ) -> None:
        doc = build_resume(
            job_id=1,
            job={"company": "Acme", "title": "Backend Engineer"},
            profile=profile,
            resume_base=resume_base,
            analysis=analysis,
        )
        assert set(doc.bullet_ids) == {"b_kafka", "b_misc", "p_vector"}
