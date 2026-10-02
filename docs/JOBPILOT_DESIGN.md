# JobPilot — Design Document & Implementation Plan

**Version 1.1.** Changes from 1.0: skill `confidence` replaced by evidence levels (professional / project / certification / experimental / learning); candidate profile and resume bank populated (`profile.yaml`, `resume_base.yaml`); explicit candidate `settings` the tool never answers for you; new `answer` command and answer bank; deprioritized role categories; stronger claim validation (evidence phrasing, years, employer vs client).

A local, single-user tool that finds relevant Software Engineer / Senior Software Engineer jobs (India + foreign remote that accepts India-based candidates), scores them against your profile, drafts tailored application material, and gives you a ranked daily queue so you can submit **20–30 quality applications per week** in about 30 minutes a day.

**Core principle:** automate discovery, analysis and drafting. Keep the final submit on LinkedIn and Naukri manual. This protects your accounts and keeps the tool maintainable.

---

## 1. Goals and non-goals

### Goals
- G1. Surface ~80–100 relevant, still-open jobs per week from multiple sources.
- G2. Score each job against a structured profile with explainable reasons and gaps.
- G3. Draft a tailored resume summary and a short note for strong matches, using only verified experience.
- G4. Produce a ranked daily queue (4–6 jobs/day) with everything needed to apply in 5–8 minutes each.
- G5. Track application status so you can see what gets responses.
- G6. Run at $0 on free tiers, within a weekly coding-agent quota.

### Non-goals (v1)
- No scraping of LinkedIn or Naukri pages, no logging in to them with automation, no CAPTCHA handling, no auto-submit on those sites.
- No React dashboard, no cloud deployment, no multi-user support, no multi-provider fallback chain, no agent frameworks.
- No "apply to everything" mode. If only 15 good jobs exist in a week, apply to 15.

---

## 2. Weekly math and what it implies

| Stage | Weekly volume | Method |
|---|---:|---|
| Raw listings ingested | 800–2,000 | Feeds, APIs, alert emails |
| After cheap filters (title, location, seniority, keywords) | 250–400 | Plain code, no LLM |
| LLM-analyzed (structured extraction) | 150–250 | Gemini free API, cached |
| Scored above threshold (>= 65) | 80–100 | Deterministic matcher |
| Tier A (>= 80): tailored resume summary + note | ~30 | LLM |
| Tier B (65–79): base resume + one-line note | ~60 | Template/LLM-light |
| Applied | 20–30 | You, manually, ~5–8 min each |

Time budget for you: about 2–3 hours/week on applications, plus ~10 min/day reviewing the queue.

---

## 3. System overview

```text
 Sources                        Pipeline                                  You
 ───────                        ────────                                  ───
 Gmail alerts (LinkedIn,   ┐
   Naukri)                 │    ┌────────┐  ┌────────┐  ┌─────────┐
 Greenhouse / Lever boards ├───▶│ Ingest │─▶│ Filter │─▶│ Analyze │
 RemoteOK / Remotive /     │    │ + dedup│  │ (code) │  │ (LLM)   │
   Himalayas / HN hiring   │    └────────┘  └────────┘  └────┬────┘
 Manual URL / pasted JD    ┘                                  │
                                                         ┌────▼────┐
                                                         │  Score  │
                                                         │ (code)  │
                                                         └────┬────┘
                                                              │
                                              ┌───────────────▼──────────────┐
                                              │ Queue → Prepare (LLM, tiered)│
                                              └───────────────┬──────────────┘
                                                              ▼
                                                  Daily queue (CSV / terminal)
                                                              ▼
                                               You review → apply manually → mark status
```

Everything runs locally from a CLI. Data lives in one SQLite file. The only network calls are source fetches, Gmail (read-only), and the LLM API.

---

## 4. Technology choices

| Concern | Choice | Reason |
|---|---|---|
| Language | Python 3.12 | Best ecosystem for LLM, email and HTTP glue work |
| Packaging | `uv` + `pyproject.toml` | Fast, reproducible |
| CLI | Typer | Simple commands, good help output |
| Storage | SQLite via stdlib `sqlite3` + small repository layer | Zero setup, single file, no ORM overhead |
| Models/validation | Pydantic v2 | Typed models, LLM output validation |
| HTTP | `httpx` | Timeouts, retries, simple API |
| HTML parsing | `selectolax` or `beautifulsoup4` | Parsing email HTML and public feeds |
| Gmail | `google-api-python-client`, `google-auth-oauthlib` (scope: `gmail.readonly`) | Read job-alert emails |
| LLM | Gemini API free tier via `google-genai` SDK | $0; model ID kept in config, not code |
| Config | `config.yaml` + `.env` | Secrets only in `.env` |
| Tests | `pytest`, `pytest-httpx`/`respx` | Mock all network and LLM calls |
| Lint/format | `ruff` | One tool |
| Optional later | Streamlit (UI), APScheduler or cron (scheduling) | Only after v1 is in daily use |

LLM access is wrapped in one small module (`llm/client.py`) exposing `generate_json(prompt, schema_model)` and `generate_text(prompt)`. This is not a multi-provider abstraction. It is a single seam so swapping providers later is a one-file change.

> Verify before implementing: current Gemini model IDs and free-tier rate limits in Google AI Studio, and each job source's endpoint and terms (attribution and rate limits). Put model IDs and limits in `config.yaml`.

---

## 5. Repository layout

```text
jobpilot/
├── AGENTS.md                  # project context for coding agents (see Appendix A)
├── README.md
├── docs/
│   ├── JOBPILOT_DESIGN.md
│   └── Candidate_Intelligence_Profile.md   # narrative profile; profile.yaml wins on conflict
├── pyproject.toml
├── config.yaml                # sources, thresholds, model IDs, daily caps
├── .env.example               # GEMINI_API_KEY=, GOOGLE_OAUTH_CLIENT_FILE=
├── .gitignore                 # .env, data/, token files, profile/resume files
├── data/                      # git-ignored
│   ├── jobpilot.db
│   ├── profile.yaml           # your structured profile (private)
│   ├── resume_base.yaml       # bullet bank with IDs (private)
│   └── out/                   # daily queue CSVs, prepared applications
├── prompts/
│   ├── extract_job_v1.txt
│   ├── tailor_summary_v1.txt
│   ├── note_v1.txt
│   └── outreach_v1.txt
├── src/jobpilot/
│   ├── cli.py
│   ├── config.py
│   ├── db.py                  # schema, migrations (simple version table), repositories
│   ├── models.py              # Pydantic models
│   ├── profile/
│   │   ├── loader.py          # load + validate profile.yaml, resume_base.yaml
│   │   └── skills.py          # skill alias normalization
│   ├── sources/
│   │   ├── base.py            # JobSource protocol
│   │   ├── greenhouse.py
│   │   ├── lever.py
│   │   ├── remoteok.py
│   │   ├── remotive.py
│   │   ├── himalayas.py
│   │   ├── hn_hiring.py
│   │   ├── gmail_alerts.py
│   │   └── manual.py          # add-url / add-jd
│   ├── pipeline/
│   │   ├── ingest.py          # normalize, fingerprint, dedup, store
│   │   ├── prefilter.py       # cheap rule-based filters
│   │   ├── analyze.py         # LLM extraction + caching
│   │   ├── score.py           # deterministic matcher
│   │   ├── queue.py           # daily queue builder
│   │   ├── prepare.py         # tailoring + notes + validation
│   │   ├── answer.py          # application-question answers + answer bank
│   │   └── claims.py          # claim validation (evidence level, years, employer vs client)
│   ├── llm/
│   │   ├── client.py          # Gemini wrapper: retries, backoff, daily cap, usage log
│   │   └── redact.py          # strip PII before sending
│   └── tracking/
│       └── status.py
└── tests/
    ├── fixtures/              # sample JDs, alert emails, API responses
    └── ...
```

---

## 6. Data model

### 6.1 Job status (single column, not a workflow engine)

```text
new → filtered_out
new → needs_jd → analyzed        (LinkedIn/Naukri alert items awaiting JD text)
new → analyzed → scored → queued → prepared → applied → replied → interview → offer
Terminal: rejected, skipped, expired
```

### 6.2 SQLite schema (v1)

```sql
CREATE TABLE schema_version (version INTEGER NOT NULL);

CREATE TABLE jobs (
  id INTEGER PRIMARY KEY,
  fingerprint TEXT NOT NULL UNIQUE,      -- sha1(company_norm|title_norm|location_norm)
  source TEXT NOT NULL,                  -- greenhouse, lever, remoteok, gmail_linkedin, gmail_naukri, manual...
  source_job_id TEXT,
  company TEXT NOT NULL,
  title TEXT NOT NULL,
  location TEXT,
  remote_type TEXT,                      -- onsite | hybrid | remote | unknown
  url TEXT NOT NULL,
  apply_url TEXT,
  description TEXT,                      -- full JD if available
  snippet TEXT,                          -- short text from alert emails
  posted_at TEXT,
  discovered_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'new',
  status_reason TEXT,
  raw_json TEXT
);
CREATE INDEX idx_jobs_status ON jobs(status);

CREATE TABLE job_urls (                  -- same job seen on multiple sources
  job_id INTEGER NOT NULL REFERENCES jobs(id),
  source TEXT NOT NULL,
  url TEXT NOT NULL,
  PRIMARY KEY (job_id, url)
);

CREATE TABLE analyses (
  job_id INTEGER NOT NULL REFERENCES jobs(id),
  prompt_version TEXT NOT NULL,
  model TEXT NOT NULL,
  content_hash TEXT NOT NULL,            -- hash of description used
  analysis_json TEXT NOT NULL,           -- validated JobAnalysis
  created_at TEXT NOT NULL,
  PRIMARY KEY (job_id, prompt_version, content_hash)
);

CREATE TABLE scores (
  job_id INTEGER PRIMARY KEY REFERENCES jobs(id),
  total REAL NOT NULL,
  skills_score REAL, experience_score REAL, seniority_score REAL,
  location_score REAL, extras_score REAL,
  tier TEXT,                             -- A | B | none
  matched_skills TEXT,                   -- JSON list
  missing_required TEXT,                 -- JSON list
  missing_preferred TEXT,                -- JSON list
  flags TEXT,                            -- JSON list: ["asks_5plus_years", "us_only", ...]
  scored_at TEXT NOT NULL
);

CREATE TABLE applications (
  id INTEGER PRIMARY KEY,
  job_id INTEGER NOT NULL UNIQUE REFERENCES jobs(id),
  tailored_summary TEXT,
  short_note TEXT,
  outreach_draft TEXT,
  bullet_ids TEXT,                       -- JSON list of resume_base bullet IDs chosen
  prepared_at TEXT,
  applied_at TEXT,
  channel TEXT,                          -- linkedin | naukri | company_site | referral | email
  referral_contact TEXT,
  notes TEXT,
  last_status_change TEXT
);

CREATE TABLE llm_usage (
  id INTEGER PRIMARY KEY,
  task TEXT, model TEXT, input_tokens INTEGER, output_tokens INTEGER,
  cached INTEGER DEFAULT 0, created_at TEXT
);

CREATE TABLE answer_bank (
  id INTEGER PRIMARY KEY,
  question_norm TEXT NOT NULL UNIQUE,    -- normalized question text
  category TEXT NOT NULL,                -- profile_fact | setting | free_text
  answer TEXT,                           -- null until you provide/approve one
  approved INTEGER NOT NULL DEFAULT 0,   -- only approved answers are reused automatically
  source_job_id INTEGER,
  created_at TEXT, updated_at TEXT
);
```

### 6.3 Deduplication
- Normalize company (lowercase, strip "pvt ltd", "inc", punctuation), title (lowercase, strip seniority synonyms like "sr.", expand "sde" → "software engineer"), and location (city name or "remote").
- Fingerprint = sha1 of the three normalized fields. On collision, keep the richer record (the one with a full description), add the other URL to `job_urls`.
- No semantic/embedding dedup in v1.

---

## 7. Candidate profile and resume bank

The most important safety mechanism in the system. The LLM may only select and rephrase facts that exist in your files, and it may only describe a skill at the level of evidence you actually have for it.

### 7.1 Layers and files

```text
Candidate facts   →   Evidence                        →   Generated material
(profile.yaml)        (levels + bullet/project IDs)       (validated drafts)
```

| File | Used by | Purpose |
|---|---|---|
| `data/profile.yaml` | Code | Source of truth: experience, explicit settings, target roles, skills with evidence levels |
| `data/resume_base.yaml` | Code + LLM (redacted) | Bullet bank, projects, achievements, certifications, summary variants |
| `docs/Candidate_Intelligence_Profile.md` | You + coding agents | Narrative description of your background and claim rules |

If the narrative and the YAML disagree, **the YAML wins**. Populated starter versions of both YAML files ship with this document; every `TODO` in them is yours to confirm.

### 7.2 Evidence levels

| Level | Meaning | Score weight | Allowed phrasing in generated text |
|---|---|---:|---|
| `VERIFIED_PROFESSIONAL` | Used in paid work | 1.0 | "experience with", "worked with"; may appear in employment bullets |
| `VERIFIED_PROJECT` | Used in a personal project | 0.6 | "built a personal project using"; never in employment bullets |
| `VERIFIED_CERTIFICATION` | Certified, limited hands-on | 0.5 | "certified in", "familiar with"; never "hands-on production" |
| `EXPERIMENTAL` | Hackathon or prototype | 0.35 | "explored", "prototyped" |
| `LEARNING` | Currently studying | 0.2 | "currently learning"; never in experience claims |
| `UNKNOWN` | No evidence | 0 | never claimed |

Weights live in `config.yaml` (`scoring.evidence_weights`). The scorer uses them; the claim validator (Section 12.2) enforces the phrasing column.

### 7.3 `profile.yaml` structure

- `experience`: `total_years_actual` (used for scoring), `years_for_forms` (only for integer form fields), `current_employer`, `current_client`, `use_client_name`, `client_display`, `domains`.
- `settings`: work authorization, visa, relocation, onsite/hybrid, night shifts, employment type, notice period, salary expectation, cities, remote regions. **`null` means unset.** The tool never answers a question that depends on an unset setting; it asks you (Section 12.4).
- `targets`: primary and secondary titles, `deprioritized_categories` (pure frontend, pure mobile, pure DevOps/SRE, pure data science, pure ML research), `enabled_extra_categories`, `seniority_stretch`.
- `dealbreakers`: title and keyword exclusions.
- `skills[]`: `name`, `category`, `level`, `years` (null = do not state years), `evidence` (bullet/project/certification IDs), optional `notes`.
- `learning`: interview-prep topics, explicitly not claimable.
- `known_gaps`: skills the tool must never claim.

### 7.4 `resume_base.yaml` structure

- `summary_variants[]`, `roles[]` with `bullets[]`, `projects[]`, `experiments[]`, `achievements[]`, `certifications[]`.
- Each bullet: `id`, `type` (professional | project | experiment), `text`, `skills`, `metrics` (the only numbers the tool may use, with approximations kept as "~"), `tags` (used by code to rank bullets against JD themes such as batch, automation, performance, security, kafka, observability), `domain`.
- Every skill named in a bullet must exist in `profile.yaml`; every evidence ID in `profile.yaml` must exist here. `profile validate` checks both directions.

### 7.5 Integrity rules (enforced in code, not just in prompts)

1. **Years of experience.** Scoring uses `total_years_actual`. Generated text and form answers may state at most `years_for_forms`. Per-skill years are stated only when `years` is set; otherwise no per-skill years are claimed.
2. **Employer vs client.** The employer is never described as the client. If `use_client_name` is false, the client appears as `client_display`.
3. **Metrics.** Only numbers listed in a bullet's `metrics` may appear. Do not drop a "~", and never invent new percentages.
4. **Evidence phrasing.** Project, certification, experimental and learning skills are never presented as professional experience, never placed in the employment section, and are only mentioned with a qualifier (see `claims.project_qualifiers` in config).
5. **Settings.** Unset settings are never guessed. Salary, notice period, relocation, onsite, visa/work authorization and shift preferences always come from you.
6. **Awards.** Use the stated wording only. No upgrades such as "top 1%".
7. **Interview-prep topics** (`learning.topics`) never appear in application material as experience.

### 7.6 Skill alias map (`profile/skills.py`)

A YAML dictionary mapping variants to canonical names, applied to both the profile and extracted JD skills. Starter entries:

```text
spring boot, springboot, spring-boot, spring framework            → Spring Boot
k8s, kubernetes, kube                                             → Kubernetes
pcf, pivotal cloud foundry, cloud foundry, cf                     → Cloud Foundry
postgres, postgresql, psql                                        → PostgreSQL
oauth, oauth2, oauth 2, oauth 2.0                                 → OAuth 2.0
json web token, jwt                                               → JWT
azure active directory, azure ad, entra id, microsoft entra       → Azure AD
sso, single sign-on, single sign on                               → SSO
ci/cd, cicd, ci cd, continuous integration, continuous delivery   → CI/CD
rest, restful, rest api, restful apis, rest apis                  → REST APIs
event driven, event-driven, event driven architecture             → Event-driven architecture
microservice, micro-services, micro services, microservices       → Microservices
new relic, newrelic                                               → New Relic
js, javascript, es6                                               → JavaScript
ts, typescript                                                    → TypeScript
amazon ecs, aws ecs, ecs                                          → AWS ECS
aws fargate, fargate                                              → AWS Fargate
aws ecr, ecr                                                      → AWS ECR
```

This map decides most of the matching quality. Keep it in a user-editable YAML file and log unmatched JD skills so it improves over time (`jobpilot skills --unmatched`).
jobpilot answer "<question>" [--job ID]   # draft an answer to an application question (Section 12.4)

---

## 8. Sources and ingestion

All sources implement:

```python
class JobSource(Protocol):
    name: str
    def fetch(self) -> list[RawJob]: ...
```

`RawJob` is normalized by `ingest.py` into the `jobs` schema.

| Source | Mechanism | Gives full JD? | Notes |
|---|---|---|---|
| Gmail alerts: LinkedIn | Read-only Gmail API; parse alert emails | No (title, company, location, link) | Primary India source. Sender addresses configurable. Items become `needs_jd` if they pass prefilter. |
| Gmail alerts: Naukri | Same | Partial (snippet with skills/experience) | Experience range in snippets is useful for prefilter |
| Greenhouse boards | Public JSON API per company token | Yes | Config list of company board tokens that hire in India/remote |
| Lever boards | Public JSON API per company slug | Yes | Same |
| RemoteOK | Public JSON feed | Yes | Respect attribution requirement and rate limit |
| Remotive | Public API | Yes | Respect rate limit and attribution |
| Himalayas | Public feed/API if available | Yes | Check current terms |
| HN "Who is hiring" | HN Algolia API, monthly thread | Free text | Parse comments, LLM extraction handles the rest |
| Manual | `add-url`, `add-jd` | If pasted | For anything you find yourself, including LinkedIn/Naukri jobs you want analyzed |

**Handling LinkedIn/Naukri alert items (no scraping):**
1. Parse alert email → create job with title, company, location, link, snippet.
2. Run prefilter on that metadata.
3. Survivors get `needs_jd`. They appear in the daily output as "needs JD" with the link.
4. You open the link in your own browser, and for the promising ones run `jobpilot add-jd <job_id>` and paste the text (stdin or file). The job then proceeds through analysis, scoring and preparation.
5. Optionally, score on title + snippet only and mark confidence as "low".

**Company list for Greenhouse/Lever:** maintained in `config.yaml` (`greenhouse_boards: [...]`, `lever_companies: [...]`). Seed it with companies known to hire in India or remote-worldwide. Add `jobpilot discover-companies` later if needed.

**Freshness:** each fetch updates `last_seen_at`. A nightly step marks jobs not seen for 14 days as `expired` (only for API-backed sources).

---

## 9. Prefilter (plain code)

Rules applied before any LLM call. Each rejection stores `status_reason`.

- **Title:** must match a `primary_titles` or `secondary_titles` entry (fuzzy/keyword), or contain Java / Spring / Backend together with an engineer or developer term.
- **Hard exclusions:** `dealbreakers.titles_exclude` and `keywords_exclude`.
- **Deprioritized categories:** titles that look purely frontend, mobile, DevOps/SRE, data science or ML research (and whose title or snippet does not mention Java, Spring or backend) are filtered out with reason `deprioritized_category:<name>`, unless that category is listed in `targets.enabled_extra_categories`.
- **Location:** an India city in `settings.india_cities`, or remote with no hard region exclusion. Regex checks for "US only", "must reside in", "EU timezone", "UK only", "work authorization required".
- **Experience:** if the text states a range (e.g., "5–8 years"), reject when min > `total_years_actual + seniority_stretch` (rounded up). Roles above `total_years_actual` but within the stretch are kept and flagged `asks_<n>plus_years`, never silently treated as equivalent.
- **Age:** skip jobs older than 21 days when `posted_at` is known.

---

## 10. LLM job analysis

### 10.1 Extraction schema (`JobAnalysis`)

```json
{
  "normalized_title": "string",
  "seniority": "junior | mid | senior | lead | unknown",
  "experience_min_years": "number | null",
  "experience_max_years": "number | null",
  "required_skills": ["string"],
  "preferred_skills": ["string"],
  "responsibilities_summary": "string (max 3 sentences)",
  "location_type": "onsite | hybrid | remote | unknown",
  "locations": ["string"],
  "remote_eligibility": {
    "open_to_india": "yes | no | unclear",
    "restriction_text": "string | null"
  },
  "visa_or_work_auth_required": "boolean",
  "salary": { "min": "number | null", "max": "number | null", "currency": "string | null", "period": "string | null" },
  "red_flags": ["string"]
}
```

### 10.2 Rules
- Output validated by Pydantic. On validation failure, retry once with the error message, then mark `analysis_failed` and move on.
- Skills are normalized through the alias map after extraction.
- Cache key = `(job_id, prompt_version, content_hash)`. Never re-call for unchanged input.
- Truncate descriptions to a configured max (e.g., 6,000 chars) before sending.
- Respect `config.llm.daily_call_cap`. When reached, remaining jobs stay queued for the next run.
- Log every call in `llm_usage`.

### 10.3 Privacy
`llm/redact.py` removes email, phone, address and any personal identifiers from everything sent to the LLM. Only skills and experience bullets are sent for tailoring. Free-tier API terms may allow Google to use submitted content to improve its products. Check the current terms and keep sensitive details out of prompts.

---

## 11. Scoring (deterministic)

Total 0–100:

| Component | Weight | Logic |
|---|---:|---|
| Skills | 40 | Required skills carry 75% of this weight, preferred 25%. Each skill earns credit equal to the evidence-level weight from `scoring.evidence_weights` (professional 1.0, project 0.6, certification 0.5, experimental 0.35, learning 0.2, unknown 0). A required skill with no profile match earns 0. |
| Experience | 20 | Uses `total_years_actual`. Full marks if your years fall within [min, max] or min <= years + 1. Linear decay as min exceeds your years, reaching 0 at `seniority_stretch + 1` years beyond. |
| Seniority/title | 15 | Match against primary (full) and secondary (80%) titles and inferred seniority. Senior titles score fully only when the experience score is acceptable. |
| Location/remote | 15 | India city match = full; remote with `open_to_india = yes` = full; unclear = 50%; `no` = 0 and the job is flagged `not_eligible`. |
| Extras | 10 | Salary vs target if both are set (else neutral 50%), stack bonuses from config, company-list bonus. |

**Hard rules (override score):**
- `remote_eligibility.open_to_india == no` and not an India city → `not_eligible`, excluded from the queue.
- `visa_or_work_auth_required == true` and not in a country where you have authorization (`settings`) → excluded; if the setting is unset, flag `work_auth_unset` and keep for your review.

**Tiers:** A >= 80, B 65–79, below 65 dropped. Thresholds in `config.yaml`.

**Requirement ↔ evidence mapping.** For every JD skill the scorer records one of: `covered` (professional), `partial` (project, certification or experimental), `learning_only`, or `missing`. `matched_skills` stores `{skill, level, evidence_ids}`, so the review screen can show exactly why a job matches and what is uncertain.

**Flags** stored with each score: `asks_<n>plus_years`, `missing_required:<skill>`, `project_only:<skill>`, `learning_only:<skill>`, `region_unclear`, `work_auth_unset`, `salary_unknown`, `low_confidence_snippet_only`.

The scorer is pure code with unit tests. The LLM never produces the final number.

---

## 12. Queue, preparation and application answers

### 12.1 Daily queue (`jobpilot queue`)
- Pull scored jobs not yet applied/skipped, ordered by tier, then total score, then recency.
- Include `needs_jd` jobs in a separate section at the bottom with links.
- Emit a terminal table and `data/out/queue_YYYY-MM-DD.csv` with: rank, job id, company, title, location, tier, score, matched skills, gaps, flags, URL, prepared (y/n).
- Default size from `config.queue.daily_size` (6). Unfinished items roll forward.

### 12.2 Preparation (`jobpilot prepare <job_id>` or `--auto-top N`)
Tier A:
1. Code selects up to N bullets from `resume_base.yaml`, ranked by skill overlap and tag overlap with the JD (for example batch/automation/performance JDs surface the batch-processing bullet first). Professional bullets rank above project bullets; project bullets are selected only when they cover a required skill the professional bullets do not, or the role family is full-stack/AI.
2. The LLM writes a tailored 2–3 sentence summary from the selected bullets and verified skills, and returns a structured `claims` list alongside the text (`[{skill, context, bullet_id}]`).
3. The LLM writes a 3–4 sentence note for the application form or recruiter message.
4. Optional LinkedIn outreach draft (under 300 characters) to a recruiter or engineer at the company.

Tier B: base summary variant + a one-line note from a template; LLM only if needed.

**Validation (`pipeline/claims.py`, code, after the LLM returns):**
- Every bullet ID in the output exists in `resume_base.yaml`.
- Every skill mentioned in the text (found via the alias map) appears in `claims`, and its `context` does not exceed its evidence level: a "professional" claim requires `VERIFIED_PROFESSIONAL`.
- Any mention of a non-professional skill must sit in a sentence containing a qualifier from `claims.project_qualifiers` ("personal project", "certified", "explored", ...). Skills at `UNKNOWN` or in `known_gaps` may not appear at all.
- Any "N years" statement must not exceed `years_for_forms`, and per-skill years are allowed only when `years` is set for that skill.
- Numbers and percentages must come from the `metrics` of selected bullets (approximations keep their "~").
- The client name must not be phrased as the employer (for example "at JPMorgan"); with `use_client_name: false` the client appears only as `client_display`.
- Interview-prep topics never appear as experience.
- On failure: regenerate once with the validation errors in the prompt; if it still fails, fall back to the base summary variant. Never persist unvalidated text.
- Output saved to `applications` and as `data/out/<job_id>_<company>.md` for copy-paste.

### 12.3 Applying
You apply manually via the job's link. After applying: `jobpilot mark <job_id> applied --channel linkedin`. Later: `replied`, `interview`, `rejected`, `offer`.

### 12.4 Application questions (`jobpilot answer "<question>" [--job ID]`)

Application forms are the biggest time sink, so questions get their own pipeline. Each question is classified by the LLM (classification only) and handled by code:

| Category | Examples | Handling |
|---|---|---|
| `profile_fact` | "Years of Java?", "Do you have Kafka experience?", "Worked with Kubernetes?" | Answered from `profile.yaml` using the skill's evidence level and phrasing rules. A project-level skill is answered as project experience ("personal project only"), never as professional. If per-skill `years` is null, the answer says "needs your input" for numeric years, or states experience without a number. |
| `setting` | salary, notice period, relocation, visa/work authorization, onsite, shifts, employment type | **Never auto-answered.** If the setting is set, use it; if null, return `NEEDS YOUR INPUT`. Your answer is saved to the answer bank. |
| `free_text` | "Why do you want to join us?" | LLM draft from verified facts, validated by the same claim rules. |

Approved answers in `answer_bank` are reused automatically for repeated questions (normalized match). Nothing is submitted for you.

---

## 13. CLI reference

```text
jobpilot init                         # create DB, copy config/profile templates
jobpilot profile validate             # check profile.yaml, resume_base.yaml, cross-references, alias coverage, unset settings
jobpilot fetch [--source NAME]        # run API/feed sources
jobpilot ingest-alerts                # read Gmail alert emails (OAuth first run)
jobpilot add-url <url>                # store a job you found manually
jobpilot add-jd <job_id|--new>        # paste/provide JD text for a job
jobpilot analyze [--limit N]          # prefilter + LLM extraction (respects caps)
jobpilot score                        # compute scores/tiers
jobpilot queue [--size N]             # build today's ranked queue (+CSV)
jobpilot prepare <job_id>|--auto-top N
jobpilot mark <job_id> <status> [--channel X] [--note "..."]
jobpilot stats [--weeks 4]            # funnel, applications/week, response rates by source/tier
jobpilot skills --unmatched           # JD skills not in alias map (to improve matching)
jobpilot run-daily                    # fetch → ingest-alerts → analyze → score → queue → prepare top
```

---

## 14. Configuration (`config.yaml`)

```yaml
llm:
  provider: gemini
  model_extract: "<set from AI Studio>"
  model_write: "<set from AI Studio>"
  daily_call_cap: 150
  max_retries: 3
  max_description_chars: 6000
thresholds:
  tier_a: 80
  tier_b: 65
  max_job_age_days: 21
scoring:
  evidence_weights:
    VERIFIED_PROFESSIONAL: 1.0
    VERIFIED_PROJECT: 0.6
    VERIFIED_CERTIFICATION: 0.5
    EXPERIMENTAL: 0.35
    LEARNING: 0.2
    UNKNOWN: 0.0
claims:
  project_qualifiers: ["personal project", "side project", "hackathon", "certified", "certification", "explored", "prototype", "currently learning"]
queue:
  daily_size: 6
  weekly_target: 25
sources:
  greenhouse_boards: []        # e.g. ["company-token", ...]
  lever_companies: []
  remoteok: true
  remotive: true
  himalayas: true
  hn_hiring: true
  gmail:
    enabled: true
    label: "job-alerts"
    senders: []                # fill with the LinkedIn / Naukri alert sender addresses you see
stack_bonus: [Kafka, Kubernetes, Microservices, Spring Boot]
```

---

## 15. Testing strategy

- **Unit:** fingerprinting and normalization, alias mapping, prefilter rules, scoring for each component, hard rules, tier boundaries, validation of tailored output (rejects invented skills/bullets).
- **Fixture-driven:** `tests/fixtures/` with sample JDs: excellent match, remote-US-only, senior-8+-years, missing-core-skill, vague JD; sample LinkedIn and Naukri alert emails (saved HTML); sample Greenhouse/Lever/RemoteOK JSON.
- **LLM tests:** all LLM calls mocked in CI. A separate optional `pytest -m live` suite runs a handful of real extractions to catch schema drift.
- **Integration:** ingest fixtures → prefilter → (mock) analyze → score → queue produces expected ranked output.
- **Claim-validation tests (adversarial LLM outputs, all must be rejected):** a project-only skill described as professional experience; a LEARNING skill (for example MCP) in experience text; an invented percentage; "5 years" when `years_for_forms` is 4; the client named as the employer; a skill from `known_gaps`; a bullet ID that does not exist.
- **Answer tests:** every `setting` question with a null setting returns `NEEDS YOUR INPUT`; a project-level skill is never answered as professional; approved answer-bank entries are reused.
- **Rule for the coding agent:** no phase is complete until `pytest` and `ruff check` pass and the agent pastes the output.

---

## 16. Operations, security and risk

| Risk | Mitigation |
|---|---|
| Secrets leak | `.env` only, git-ignored; `.env.example` committed; OAuth token file git-ignored |
| Personal data sent to LLM | `redact.py`; send only skills/bullets; review free-tier data terms |
| Overclaiming or hallucinated experience | Evidence levels, bullet-ID and skill validation, years/metrics/client checks (Section 12.2), unset settings never answered, human review before applying |
| Source API changes | Each source isolated with its own tests; failures logged and skipped, never crash the run |
| Free-tier limits | Daily cap, caching, backoff; quota-aware design (Section 17) |
| Platform enforcement | No automation against LinkedIn/Naukri; Gmail alerts and manual JD paste only |
| Bad scoring | `stats` and `skills --unmatched` feed back into the alias map and weights; weights are config, not code |
| Over-applying low-fit jobs | Tier thresholds; weekly target is a ceiling on quality, not a quota to hit |

---

## 17. Working with a free Antigravity quota

- Quota refreshes weekly on the free tier and has been cut before, so assume it can shrink.
- Use **Opus 4.6** for: Phase 0 architecture/skeleton, the scorer, the validation logic, and any debugging that loops more than twice.
- Use **Gemini Flash** for: individual source connectors, fixtures, CLI wiring, README, repetitive tests.
- One module per prompt. Always include `AGENTS.md` plus the exact files in scope.
- Ask the agent to run tests itself and report only failures plus a short summary, not to print entire files back.
- If quota runs out mid-phase, work stays shippable: every phase ends in a runnable, tested state.

---

## 18. Implementation plan

**Start applying before the tool exists.** On day 1, create LinkedIn and Naukri job alerts (several variants: "Java Backend", "Spring Boot", "Senior Software Engineer", "Software Engineer II", by city and remote), update your Naukri profile, and apply manually to the best 3–5 a day. The tool should speed up an existing habit, not delay it.

| Phase | Deliverable | Est. effort | Preferred model |
|---|---|---|---|
| 0 | Repo skeleton, config, DB schema, models, profile/resume loaders, alias map, CLI shell, tests | 1 day | Opus 4.6 |
| 1 | API/feed sources (Greenhouse, Lever, RemoteOK, Remotive, HN), ingest, fingerprint, dedup, prefilter | 1–2 days | Gemini Flash (review with Opus) |
| 2 | Gmail alert ingestion (LinkedIn, Naukri), `needs_jd` flow, `add-jd`, `add-url` | 1–2 days | Gemini Flash / Opus for OAuth + parsing |
| 3 | LLM client, extraction, cache, caps, redaction, scoring, tiers | 2 days | Opus 4.6 for scorer; Flash for rest |
| 4 | Queue, CSV, preparation with validation, status tracking, `stats`, `run-daily` | 2 days | Opus 4.6 for validation; Flash for rest |
| 5 (optional) | Streamlit view, scheduled daily run, notification | 1–2 days | Gemini Flash |

Realistic total for v1 (Phases 0–4): about 1.5–2 weeks of evenings, depending on quota resets.

### Exit criteria for v1
- `jobpilot run-daily` completes without manual intervention on a normal day.
- At least 80 scored jobs a week with understandable reasons.
- Queue gives you 4–6 ready-to-apply jobs a day.
- Tailored output never contains a skill or bullet outside your files (validated).
- You can see applications per week and responses by source in `stats`.

---

## 19. Antigravity prompts

Use `AGENTS.md` (Appendix A) as persistent project context. Each phase prompt below goes in as a separate task. After each phase, run the tests yourself, then bring me the output if anything fails.

### Phase 0 — Foundation

```text
ROLE
You are a senior Python engineer. Build the foundation of a local CLI tool called JobPilot.

CONTEXT
Read AGENTS.md and JOBPILOT_DESIGN.md (sections 4–7, 13, 14) before changing anything.

TASK
Create the project skeleton and core infrastructure.

FILES TO CREATE
- pyproject.toml (uv, Python 3.12; deps: typer, pydantic>=2, httpx, pyyaml, python-dotenv, selectolax, pytest, respx, ruff)
- src/jobpilot/{cli.py, config.py, db.py, models.py}
- src/jobpilot/profile/{loader.py, skills.py}
- config.yaml, .env.example, .gitignore (ignore .env, data/, token files)
- tests/ for each module

REQUIREMENTS
1. db.py: create the schema from section 6.2 of the design doc using stdlib sqlite3, with a schema_version table and an idempotent init. Provide small repository functions (upsert_job, get_jobs_by_status, update_status, save_analysis, save_score, upsert_application).
2. models.py: Pydantic models for RawJob, Job, JobAnalysis (section 10.1), Profile, ResumeBase, ScoreResult.
3. profile/loader.py: load and validate data/profile.yaml and data/resume_base.yaml (schema v2, see section 7 and the provided starter files). Validate: evidence levels are in the allowed enum; every evidence ID exists in resume_base; every skill named in a bullet exists in the profile; no duplicate IDs; metrics are lists of strings; report unset (null) settings as warnings, not errors. Provide `profile validate` output that lists errors, warnings and a count of skills per evidence level.
4. profile/skills.py: alias normalization from a YAML alias file; seed it with the starter entries in section 7.6 plus at least 40 more common backend/Java/DevOps aliases; function normalize_skill(str) -> str and an unmatched-skill logger.
5. cli.py: Typer app with commands `init`, `profile validate`, `mark`, and placeholders for the others that print "not implemented yet".
6. config.py: load config.yaml and .env; typed settings object.

NON-REQUIREMENTS
No network calls, no LLM code, no Gmail, no UI.

TESTS
Schema creation is idempotent; loader catches the invalid cases above; alias normalization table-driven tests; CLI `init` and `profile validate` work against fixture files.

ACCEPTANCE
`uv run pytest` and `uv run ruff check .` pass. `uv run jobpilot init` creates data/jobpilot.db and template files without overwriting existing ones.

DO NOT
Hardcode model names or secrets. Add extra frameworks. Modify files outside this scope.

FINAL REPORT
List files created, test results, and any design decisions you had to make.
```

### Phase 1 — Feed/API sources, ingest, prefilter

```text
ROLE
Senior Python engineer.

CONTEXT
Read AGENTS.md and JOBPILOT_DESIGN.md sections 6.3, 8, 9. Phase 0 is complete.

TASK
Implement API/feed sources, ingestion with dedup, and the prefilter.

REQUIREMENTS
1. sources/base.py: JobSource protocol returning list[RawJob].
2. Implement greenhouse.py, lever.py, remoteok.py, remotive.py, hn_hiring.py. Each: timeouts, retries with backoff, polite rate limiting, user-agent from config, graceful failure (log and return []). Read endpoints and terms (attribution, rate limits) from each service's current public documentation; list what you used in the final report and put attribution notes in README.
3. pipeline/ingest.py: normalize company/title/location, compute fingerprint, upsert, merge duplicates (keep the record with the fuller description, record extra URLs in job_urls), update last_seen_at.
4. pipeline/prefilter.py: implement all rules in section 9 as small, individually tested functions; store status_reason on rejection.
5. CLI: `fetch [--source]` runs sources then ingest then prefilter and prints a funnel summary (fetched, new, duplicates, filtered_out by reason, remaining).

TESTS
Use recorded JSON fixtures for every source (no live network in tests). Table-driven tests for normalization, fingerprint collisions, merge behavior, and each prefilter rule including regex cases ("US only", "must reside in", "5-8 years").

ACCEPTANCE
Tests and ruff pass. `jobpilot fetch` against fixtures prints a correct funnel summary.

DO NOT
Call any LLM. Scrape LinkedIn or Naukri. Add sources not listed.
```

### Phase 2 — Gmail alerts and manual input

```text
ROLE
Senior Python engineer.

CONTEXT
Read AGENTS.md and JOBPILOT_DESIGN.md section 8. Phases 0–1 are complete.

TASK
Implement Gmail job-alert ingestion and manual JD input.

REQUIREMENTS
1. sources/gmail_alerts.py: OAuth installed-app flow with scope gmail.readonly only; token stored in a git-ignored file; read only messages under the configured label from configured senders; parse HTML alert emails into RawJob (title, company, location, link, snippet). Separate parsers for LinkedIn-style and Naukri-style alerts, each driven by saved fixture emails. Idempotent: never create duplicates from the same message ID. Never send, modify, or delete mail.
2. Jobs from alerts that pass prefilter get status needs_jd.
3. sources/manual.py and CLI: `add-url <url>` (stores URL with optional --title/--company) and `add-jd <job_id|--new>` which accepts JD text via stdin or --file, updates the description, and moves the job to new so it re-enters the pipeline.
4. CLI `ingest-alerts` prints counts per sender and per outcome.

TESTS
Parser tests with at least 3 fixture emails per sender (include one malformed). Idempotency test. add-jd transitions test.

ACCEPTANCE
Tests and ruff pass. Running ingest-alerts twice on the same fixtures creates no new rows.

DO NOT
Fetch or scrape the job links inside the emails. Request any Gmail scope other than readonly.
```

### Phase 3 — LLM analysis and scoring

```text
ROLE
Senior Python engineer with strong testing discipline.

CONTEXT
Read AGENTS.md and JOBPILOT_DESIGN.md sections 10 and 11. Phases 0–2 are complete.

TASK
Implement the LLM client, job analysis with caching, redaction, and the deterministic scorer.

REQUIREMENTS
1. llm/client.py: wrapper over the Gemini API (google-genai SDK) with generate_json(prompt, schema_model) and generate_text(prompt); model IDs from config; exponential backoff on rate limits; daily call cap from config; usage logging to llm_usage; one repair retry on invalid JSON; fully mockable.
2. llm/redact.py: remove emails, phone numbers, URLs containing personal identifiers, and street addresses from any outbound text; unit-tested.
3. prompts/extract_job_v1.txt and pipeline/analyze.py: for jobs with descriptions, call the extractor, validate to JobAnalysis, normalize skills through the alias map, cache by (job_id, prompt_version, content_hash), truncate long descriptions, stop gracefully when the cap is hit. CLI `analyze [--limit]`.
4. pipeline/score.py: implement section 11 exactly. Pure functions only, no I/O inside scoring logic. Weights/thresholds from config. Produce matched_skills, missing_required, missing_preferred, flags, tier. Implement hard rules (not_eligible). CLI `score`.
5. CLI `skills --unmatched` lists extracted skills not found in the alias map with counts.

TESTS
Scorer: table-driven tests for every component and boundary (experience decay, evidence-level weights, remote eligibility yes/no/unclear, tier boundaries, hard rules). Analysis: mocked LLM, cache hit avoids calls, cap respected, invalid JSON repaired then failed cleanly. Redaction cases.

ACCEPTANCE
Tests and ruff pass. With a mocked LLM and the fixture JDs, the ranking order matches the expected order defined in tests.

DO NOT
Let the LLM compute scores. Send profile contact details to the LLM. Hardcode model names.
```

### Phase 4 — Queue, preparation, tracking

```text
ROLE
Senior Python engineer.

CONTEXT
Read AGENTS.md and JOBPILOT_DESIGN.md sections 12, 13. Phases 0–3 are complete.

TASK
Implement the daily queue, application preparation with validation, status tracking, stats, and run-daily.

REQUIREMENTS
1. pipeline/queue.py and CLI `queue`: ranked queue per section 12.1, terminal table plus CSV in data/out/, needs_jd section, roll-forward of unfinished items.
2. pipeline/prepare.py and CLI `prepare <job_id>|--auto-top N`: bullet ranking by skill overlap done in code; LLM only rephrases chosen bullets and writes summary, note, and optional outreach draft; Tier B uses base summary + template note. Prompts in prompts/*.txt.
3. pipeline/claims.py: implement the validation list in section 12.2 exactly (bullet IDs, evidence-level claims and qualifiers, years, metrics, client-as-employer, known_gaps, learning topics). On failure regenerate once, then fall back to the base summary variant. Never persist unvalidated text.
3b. pipeline/answer.py and CLI `answer`: implement section 12.4 including the answer_bank table. Settings that are null must return NEEDS YOUR INPUT and never be guessed.
4. Output saved to applications table and data/out/<job_id>_<company>.md.
5. tracking/status.py and CLI `mark`: allowed status transitions, timestamps, channel and notes.
6. CLI `stats [--weeks N]`: funnel counts, applications per week vs weekly_target, responses and interviews by source and by tier.
7. CLI `run-daily`: fetch → ingest-alerts → analyze → score → queue → prepare top N; each stage's failure is logged and does not abort later stages.

TESTS
Validation tests with adversarial mocked LLM outputs (invented skill, unknown bullet ID, invented metric) must be rejected. Queue ordering and roll-forward. Status transition rules. stats on a seeded database. run-daily with all stages mocked.

ACCEPTANCE
Tests and ruff pass. On a seeded database, `run-daily` produces a CSV queue and prepared files, and no output contains a skill with level UNKNOWN or in known_gaps, nor presents a non-professional skill as professional experience.

DO NOT
Submit anything anywhere. Add UI. Add providers.
```

### Phase 5 (optional) — Streamlit + scheduling
Write this prompt only after v1 has been in daily use for a week or two, so the UI reflects what actually annoys you.

---

## 20. Weekly operating routine

**Daily (~30 min)**
1. Run `jobpilot run-daily` (or let it run on a schedule).
2. Open the queue CSV. Skim reasons and flags.
3. For `needs_jd` items that look promising, open the job link, paste the JD with `add-jd`.
4. Apply to 4–6 jobs using the prepared text, copy-pasting into the application form.
5. `jobpilot mark <id> applied --channel ...` after each.
6. For two or three top-tier jobs a week, send the outreach draft to someone at the company.

**Weekly (~20 min)**
1. `jobpilot stats --weeks 4`: check applications vs target, responses by source and tier.
2. `jobpilot skills --unmatched`: extend the alias map.
3. Adjust alerts, Greenhouse/Lever company list, or thresholds based on what's converting.

---

## Appendix A — `AGENTS.md` (project context for coding agents)

```markdown
# JobPilot — agent instructions

## What this is
A local, single-user Python CLI that ingests job listings, analyzes them with an LLM,
scores them deterministically against a structured profile, and prepares application
drafts. Full design: JOBPILOT_DESIGN.md. Follow it unless told otherwise.

## Hard rules
- Python 3.12, uv, Typer, Pydantic v2, stdlib sqlite3. No ORM, no web framework, no agent frameworks.
- Never automate, scrape, or log in to LinkedIn or Naukri. Jobs from them arrive only via Gmail alert emails (read-only) or user-pasted text.
- Never submit applications anywhere. Output is drafts for a human.
- Scoring is deterministic code. The LLM only extracts structure and writes drafts.
- LLM output must be validated (Pydantic + bullet/skill validation). Never persist unvalidated generated text.
- profile.yaml is the source of truth for claims. Describe a skill only at its evidence level; never present project, certification, experimental or learning skills as professional experience.
- Never state more than `years_for_forms` years of experience; never invent metrics; the employer is never the client.
- Candidate settings that are null (salary, visa, relocation, notice period, etc.) are never guessed; return NEEDS YOUR INPUT.
- Never send contact details to an LLM. Use llm/redact.py on all outbound text.
- Secrets only in .env. Never commit data/, tokens, resume or profile files.
- No live network or real LLM calls in tests. Use fixtures and mocks.
- Model IDs, thresholds, caps, and source lists come from config.yaml.

## Working agreement
- Inspect the existing repo before editing. Make the smallest change that satisfies the task.
- Do not modify files outside the task scope.
- Before finishing: run `uv run pytest` and `uv run ruff check .`, fix failures, and report results.
- Final report: files changed, tests run and results, assumptions made, anything left undone.
```

## Appendix B — Prompt skeletons

**`prompts/extract_job_v1.txt`**
```text
You extract structured data from a job description. Return only JSON matching the schema.
Rules:
- Use only information stated in the text. If unknown, use null/"unknown".
- List skills as they appear; do not infer skills that are not mentioned.
- remote_eligibility.open_to_india: "yes" only if the text clearly allows working from India
  or states worldwide/APAC/Asia; "no" if it requires residence or work authorization elsewhere;
  otherwise "unclear". Quote the restricting phrase in restriction_text.
- responsibilities_summary: at most 3 sentences.

JOB DESCRIPTION:
{description}
```

**`prompts/tailor_summary_v1.txt`**
```text
Write a 2–3 sentence professional summary for a resume tailored to the job below.
Use ONLY the facts and skills in CANDIDATE_FACTS. Do not add skills, employers, numbers,
or achievements that are not present. Describe each skill only at its evidence level: VERIFIED_PROFESSIONAL
may be stated as experience; project, certification and experimental skills only with a qualifier such as
"personal project" or "certified"; never mention LEARNING or UNKNOWN skills. Use only metrics listed in
the selected bullets. The employer is never the client.
Return JSON: {"summary": str, "bullet_ids": [ids chosen from CANDIDATE_FACTS]}.

JOB (structured): {job_analysis_json}
CANDIDATE_FACTS (verified): {selected_bullets_and_skills}
```

**`prompts/note_v1.txt`**
```text
Write a 3–4 sentence application note (no greeting, no sign-off) explaining why this candidate
fits this role. Use ONLY CANDIDATE_FACTS. Be specific, plain, and not exaggerated.
Return JSON: {"note": str}.
```
