# ARCHITECTURE.md

Map of the system so agents can navigate instead of exploring. Update this file when a boundary moves.

## 1. Pipeline (LangGraph state machine)

```
discover → dedup → scrape → extract → enrich → estimate → score → verify → review(HITL) → export/report
```

| Stage | Responsibility | Output |
|---|---|---|
| discover | Query approved sources (Tavily/GovFiles; Serper planned), collect candidate mentions | raw candidates + provenance |
| dedup | `rapidfuzz` entity resolution, cluster merges/rebrands/DBAs | canonical company records |
| scrape | Firecrawl/Jina fetch of approved URLs, cache-aside in Redis | page text + provenance |
| extract | Local Llama-3-8B 4-bit + Pydantic schema → structured fields | `Prospect` fields with sources |
| enrich | Ownership/management/contact evidence from registries + directory data | ownership + contacts |
| estimate | Deterministic proxy models produce revenue/EBITDA bands | `FinancialEstimate` objects |
| score | Deterministic weighted score; LLM judge is alignment-only | `ScoreBreakdown` 0–100 |
| verify | Source + corroboration checks, audit sampling | verification status |
| review | HITL approve/reject with reason; reason feeds evals | review state |
| export | CSV/JSON + weekly report + funnel metrics | artifacts in `data/generated/` |

Funnel target per week: 500 raw → 150 deduped → 60 scraped → 25 qualified → 20 verified.
The implemented discovery `bouncer` performs URL-level dedup; the `dedup` stage
(company entity resolution) is still a target, not built.

### Implemented discovery slice

```
planner → Send(tavily_web | govfiles_registry) → collect → route
  → bouncer → resolve → END
  → abort_node → END          (> failure_abort_ratio, or an empty plan)
```

- `pipeline/state.py` — `DiscoveryState`; `merge_search_results` uses `None` as the
  explicit clear signal; `search_failure_count` uses `operator.add`.
- `pipeline/nodes.py` — planner/search/collect/router/bouncer/abort/resolve.
  The bouncer owns canonicalization, dedup, policy, and the per-domain cap; the router
  owns the failure-ratio decision so `abort_node` can write a clean checkpoint.
- `pipeline/discovery_graph.py` — wiring plus SQLite checkpointer setup (async saver,
  WAL + busy timeout) and serializer allow-list for `Candidate` / `QueryHit` /
  `SearchTask`.
- Checkpoints are per super-step in `data/cache/checkpoints.db`; the raw search list is
  cleared after the bouncer, but earlier checkpoint history still retains it.
- `sources/cache.py` — SQLite search cache keyed by provider + query + params + version.
- `sources/fixtures.py` — dev capture/replay. Capture at the provider wrapper is the
  source of truth; `dev/freeze_from_checkpoint.py` is an optional lossy rescue tool.
- `sources/dlq.py` — Redis `dlq:search` plus a JSONL fallback; never raises.
- `sources/planner.py` — Groq planner with Pydantic `SearchTask` validation and a
  deterministic fallback plan.
- `sources/tavily.py`, `sources/govfiles.py` — provider adapters. GovFiles is
  config-gated and fixture-backed until its live contract is confirmed.
- `pipeline/runlog.py` — appends one idempotent row per run to
  `docs/discovery-runs.md`.
- `pipeline/export.py` — opt-in JSON export for completed runs to
  `data/generated/discovery/`; aborted runs are not exported.

## 2. Module boundaries

```
src/acquiscout/
  config.py        Settings (pydantic-settings) — market, sectors, weights, thresholds,
                   discovery limits, fixture flags, prod guard
  schemas.py       Pydantic v2 domain models — the contract between every stage
                   (including discovery `Candidate`, `QueryHit`, `SearchTask`)
  pipeline/        discovery LangGraph state, nodes, and wiring
  sources/         approved-source clients, policy, cache, fixtures, DLQ, providers
  dev/             developer-only helpers (optional checkpoint fixture rescue)
  services/        dedup, scoring, estimation, verification, export (pure, testable)
  smoke.py         offline wiring check (no network)
api/               FastAPI app: auth, run control, prospect reads
dashboard/         Streamlit: pipeline table, review queue, weekly report
scripts/           one-off operators (golden-set ingest, backfills)
```

Rules:
- Nodes orchestrate; `services/` holds logic. Logic must be testable without a graph.
- Every external call goes through `sources/` so policy + cache + retries are applied once.
- No stage may invent a field outside `schemas.py`. Extend the schema first.

## 3. Data model (v1 core)

- `SourceRef` — url, title, retrieved_at, extractor version.
- `FinancialEstimate` — value, low/high band, confidence, method, `sources[]`. Value without method + source is invalid.
- `Contact` — kind, value, `verification_status`, `sources[]`.
- `OwnershipProfile` — legal name, DBA, entity type, principals, management evidence.
- `ScoreBreakdown` — financial 30 / management 20 / ownership 15 / contactability 15 / market 10 / data confidence 10; components bounded, total must equal the sum.
- `Prospect` — identity, county, sector, estimates, ownership, contacts, score, risk flags, dedup cluster, review state, CRM state, timestamps.
- `ReviewState` — pending / verified / rejected (+ reason). Only a human may move to verified.
- `Candidate` / `QueryHit` / `SearchTask` — discovery provenance: identifier kind
  (`url` or `registry_id`), provider, per-query hits, optional resolved `crawl_url`,
  and the validated planner task.

## 4. Cross-cutting concerns

- **Reliability:** `tenacity` retries with jitter; failed searches go to Redis
  `dlq:search` with a JSONL fallback (`data/dlq/`), never silently dropped. A run
  continues when failures are at or below the abort ratio and routes to `abort_node`
  above it.
- **Caching:** Redis cache-aside for scrapes keyed by `url + extractor_version`; SQLite
  search cache for discovery keyed by provider + query + params + version. Dev
  fixtures replay before either cache and before any network call.
- **Observability:** per-prospect tokens, latency, cost; per-run funnel counts; weekly cost per qualified prospect.
- **Security:** bearer `ACQUISCOUT_API_KEY` on FastAPI; `llm-guard` on prompts/outputs; `sources/policy.py` denies paywalled/private domains before any request.
- **HITL:** review transitions and rejection reasons are persisted and reusable as eval signal.

## 5. Deployment

- Local: Docker Compose currently provides `postgres`, `redis`, and `jaeger`; the API
  and dashboard containers are not built yet.
- Prod: target is three services — web service (API + dashboard), managed PostgreSQL,
  managed Redis. Pinned base images, non-root user, secrets via env vars.

## 6. Open boundaries (not built yet)

- Crawl onward: extraction, enrichment, estimation, scoring, verification, review, export.
- `eval/` harness and `tests/eval` — see `EVALUATION.md`.
- Registry URL resolution is best-effort; the Sunbiz/GovFiles live contracts are unverified.
- Arkansas market pack — config-gated, deferred.
- CRM integration — export only in v1 (ADR-007); native HubSpot is Week 10+.
