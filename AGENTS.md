# JobPilot — agent instructions

## What this is
A local, single-user Python CLI that ingests job listings, analyzes them with an LLM,
scores them deterministically against a structured profile, and prepares application
drafts. Full design: JOBPILOT_DESIGN.md. Current behavior: docs/FUNCTIONALITY.md. Where they disagree, follow FUNCTIONALITY.md.

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
- When a change alters what the app does, update docs/FUNCTIONALITY.md in the same change.
- Do not modify files outside the task scope.
- Before finishing: run `uv run pytest` and `uv run ruff check .`, fix failures, and report results.
- Final report: files changed, tests run and results, assumptions made, anything left undone.
