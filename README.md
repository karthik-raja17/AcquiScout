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

## Observability (LangSmith)

Tracing is opt-in. Add these to `.env`:

```dotenv
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=lsv2_...
LANGSMITH_PROJECT=acquiscout
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
ACQUISCOUT_AGENT_ID=research-agent
ACQUISCOUT_AGENT_ENVIRONMENT=local
```

`python -m acquiscout.pipeline` then sends the graph run to LangSmith, where the planner,
search fan-out, collect, router, bouncer, abort, and resolve steps are visible as a trace.
Traces carry `agent_id` and `agent_environment` metadata and use `discovery-<run_id>` as
the run name. The `ACQUISCOUT_AGENT_*` names keep that metadata separate from LangSmith's
own agent-routing variables.

## Graph shape (LangGraph Studio)

`langgraph.json` exposes the compiled discovery graph. Start the local server:

```powershell
$env:PYTHONIOENCODING='utf-8'
langgraph dev
```

Open the Studio URL it prints:

```text
https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024
```

The graph shows `planner`, `tavily_web`, `govfiles_registry`, `collect`,
`route_after_search`, `bouncer`, `abort_node`, and `resolve`. Use `--no-browser` to skip
auto-opening the browser. On Windows, setting `PYTHONIOENCODING=utf-8` avoids the CLI's
emoji help/startup crash under the default cp1252 console encoding. If port 2024 is
already taken by a stale server, use `--port 2025` and point Studio at
`http://127.0.0.1:2025`.

The Studio factory intentionally does not create the SQLite search cache; doing blocking
file I/O inside the async graph factory triggers LangGraph's `BlockingError`. The normal
CLI path still uses the cache.
Tracing is explicitly disabled when `LANGSMITH_TRACING` is false; a missing key with
tracing enabled fails at `Settings` construction.

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
