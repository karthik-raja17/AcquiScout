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
  resolve → relevance → GovFiles registry enrichment → public Sunbiz/DBPR evidence →
  company dedup → screening, with lossless archives, SQLite checkpoints/cache, DLQ,
  usage budgets, and dev fixture replay.
- Directory resolution is **off by default**. The experimental graph (directory resolver
  → high-confidence website search) remains behind `ENABLE_DIRECTORY_RESOLVER=true`.
  See `docs/resolver-experiment.md` and ADR-043.
- Discovery eval baseline: 40 samples; 100% official-company Jina fetch, 56.2%
  crawl-worthy precision.
- Registry evidence: GovFiles detail is enrichment only; public Sunbiz/DBPR detail pages
  are the evidence URLs. Dropped companies are archived with reasons.
- Source blocks are recorded explicitly (`blocked`, `rate_limited`, `http_error`,
  etc.) with source/reason/backend. A per-host circuit breaker stops repeated calls
  to a blocked host. See `docs/source-failures.md` and ADR-044.
- Registry evidence uses source adapters: Sunbiz for G3/legal identity, DBPR/DFS for
  license corroboration. `manifest.json` and export include `registry_metrics` by
  status and source. See ADR-045.
- Fixed-denominator resolver eval on the frozen 40-sample baseline: official mapping
  18/32 (56.2%) → 21/32 (65.6%), but 2 of 3 conversions were duplicates and only **one
  net-new company** was added; 8/32 unresolved. The simpler graph is the default. See
  `docs/eval-runs.md` and `docs/resolver-experiment.md`.
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
python scripts/eval_discovery.py    # crawl-worthiness eval (plain HTTP fetches)
python scripts/eval_resolver.py     # resolver/baseline phase-2/3 eval (live calls)
python scripts/measure_latency.py   # per-node discovery latency (live calls)

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

## Discovery eval and raw archive

Each search node writes its results before returning:

```text
data/generated/discovery/<run_id>/
  plan.json
  manifest.json
  usage.json
  raw/tavily_web/<task_id>.json
  raw/govfiles_registry/<task_id>.json
  directory/<hash>.json
  registry/<hash>.json
  registry_evidence/<hash>.json
  company_url/<hash>.json
  dropped.json
```

The archive includes the request, normalized results, raw provider payload, usage, and
error (if any), so an abort or crash cannot lose search data. `usage.json` records Tavily
credits, GovFiles credits, and Groq tokens; budgets are enforced before search calls.
The graph also emits `resolved_entities` and `company_candidates`, records every
relevance drop with a reason, and archives dropped companies in `dropped.json`.

Discovery is scored on crawl-worthiness, not golden-set recall:

```powershell
python scripts/eval_discovery.py
```

Only `official_company` samples are fetched, through Jina Reader; the limiter defaults to
18 RPM because the free tier allows 20. See `EVALUATION.md` §2 and
`data/raw/discovery_eval_v1.json`.

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
