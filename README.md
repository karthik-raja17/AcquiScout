# AcquiScout

Source-backed, human-reviewed M&A research pipeline. Turns fragmented public data into verified, scored, CRM-ready acquisition prospects for title companies and property management firms in the Tampa Bay area (Hillsborough, Pinellas, Pasco).

- **What / why:** [`PROJECT.md`](./PROJECT.md)
- **Rules + stack locks:** [`AGENTS.md`](./AGENTS.md)
- **Rationale (ADRs):** [`DECISIONS.md`](./DECISIONS.md)
- **Quality gates:** [`EVALUATION.md`](./EVALUATION.md)
- **System map:** [`ARCHITECTURE.md`](./ARCHITECTURE.md)

> Not an outreach tool. No emails, DMs, or form submissions. Exports CSV/JSON only.
> EBITDA is always a proxy with a confidence band — never presented as audited fact.

## Status

Phase 0 scaffold: config, schemas, docs, tests. No live sources wired yet.

## Install

```powershell
python -m venv .venv                     # already exists in this repo
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"                  # fast: config + schemas + tests
pip install -e ".[dev,agents,api,ui,scrape,eval]"   # full stack (heavy)
Copy-Item .env.example .env              # then fill in keys locally
```

## Run

```powershell
python -m pytest -q --tb=short
ruff check .
ruff format --check .
python -m acquiscout.smoke

docker compose up -d postgres redis
```

## Layout

```
src/acquiscout/   config + Pydantic v2 schemas (pipeline/services arrive next)
tests/            unit tests now, eval gates under tests/eval later
scripts/          one-off operators
data/raw/         hand-collected golden set (committed)
data/generated/   pipeline output (gitignored)
```

## Configuration

`src/acquiscout/config.py` holds the market boundary, sectors, scoring weights, and thresholds. Answers to `PROJECT.md` §12 should change config, not code — and get recorded in `DECISIONS.md`.

Secrets come from `.env` only. Never commit them; never log them.
