# AcquiScout

Source-backed, human-reviewed M&A research pipeline. Turns fragmented public data into verified, scored, CRM-ready acquisition prospects for title companies and property management firms in the Tampa Bay area (Hillsborough, Pinellas, Pasco).

- **What / why:** [`PROJECT.md`](./PROJECT.md)
- **Rules + stack locks:** [`AGENTS.md`](./AGENTS.md)
- **Rationale (ADRs):** [`DECISIONS.md`](./DECISIONS.md)
- **Quality gates:** [`EVALUATION.md`](./EVALUATION.md)
- **System map:** [`ARCHITECTURE.md`](./ARCHITECTURE.md)
- **Discovery run log:** [`docs/discovery-runs.md`](./docs/discovery-runs.md)

> Not an outreach tool. No emails, DMs, or form submissions. Exports CSV/JSON only.
> EBITDA is always a proxy with a confidence band — never presented as audited fact.
> Native HubSpot/CRM integration is out of scope for v1; export is CSV/JSON only
> (Week 10+ at the earliest).

## Status

- Phase 0 contract: config, schemas, policy, golden set, docs, tests.
- Discovery slice: LangGraph planner → Tavily/GovFiles search → collect → bouncer →
  resolve, with SQLite checkpoints/cache, DLQ, and dev fixture replay.
- Crawl, extraction, scoring, verification, API, and dashboard are not built yet.
- `golden_v1.json` is frozen; `golden_v2.xlsx` is a draft schema extension.

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
python -m acquiscout.pipeline       # live discovery run (needs Tavily/Groq keys)
python -m acquiscout.pipeline --export   # also write data/generated/discovery/<run_id>.json

docker compose up -d postgres redis
```

Each run appends one row to `docs/discovery-runs.md`. Export is opt-in and only
writes completed runs; aborted runs are logged but not exported.

### Dev fixtures (capture once, replay free)

```powershell
$env:ACQUISCOUT_CAPTURE_SEARCH=1; $env:ACQUISCOUT_CAPTURE_PLANNER=1
python -m acquiscout.pipeline
$env:ACQUISCOUT_CAPTURE_SEARCH=0; $env:ACQUISCOUT_CAPTURE_PLANNER=0
$env:ACQUISCOUT_USE_FIXTURES=1
python -m acquiscout.pipeline       # no external calls
```

`dev_fixtures/` is per-developer and gitignored. If capture was missed, the optional
`python -m acquiscout.dev.freeze_from_checkpoint <thread_id>` rescues a lossy copy from
a pre-bouncer checkpoint. Fixture flags are refused when `ACQUISCOUT_ENV=prod`.

## Layout

```
src/acquiscout/   config, schemas, discovery pipeline, sources
  pipeline/       LangGraph state, nodes, wiring
  sources/        policy, cache, fixtures, DLQ, Tavily/GovFiles/Groq adapters
  dev/            optional developer helpers
tests/            unit tests now, eval gates under tests/eval later
scripts/          one-off operators
data/raw/         hand-collected golden set (committed)
data/generated/   pipeline output (gitignored)
data/cache/       SQLite cache + checkpoints (gitignored)
data/dlq/         failed-search fallback log (gitignored)
dev_fixtures/     per-developer search/planner fixtures (gitignored)
```

## Configuration

`src/acquiscout/config.py` holds the market boundary, sectors, scoring weights, and thresholds. Answers to `PROJECT.md` §12 should change config, not code — and get recorded in `DECISIONS.md`.

Secrets come from `.env` only. Never commit them; never log them.
