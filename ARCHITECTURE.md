# ARCHITECTURE.md

Map of the system so agents can navigate instead of exploring. Update this file when a boundary moves.

## 1. Pipeline (LangGraph state machine)

```
discover → dedup → scrape → extract → enrich → estimate → score → verify → review(HITL) → export/report
```

| Stage | Responsibility | Output |
|---|---|---|
| discover | Query approved sources (Tavily/Serper), collect candidate company mentions | raw candidates + source URLs |
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

## 2. Module boundaries

```
src/acquiscout/
  config.py        Settings (pydantic-settings) — market, sectors, weights, thresholds
  schemas.py       Pydantic v2 domain models — the contract between every stage
  pipeline/        LangGraph nodes + graph wiring (one file per stage group)
  sources/         approved-source clients + policy.py deny-list (checked before fetch)
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

## 4. Cross-cutting concerns

- **Reliability:** `tenacity` retries with jitter; failed fetches go to a DLQ table/queue, never silently dropped.
- **Caching:** Redis cache-aside keyed by `url + extractor_version`; TTL policy per source type.
- **Observability:** per-prospect tokens, latency, cost; per-run funnel counts; weekly cost per qualified prospect.
- **Security:** bearer `ACQUISCOUT_API_KEY` on FastAPI; `llm-guard` on prompts/outputs; `sources/policy.py` denies paywalled/private domains before any request.
- **HITL:** review transitions and rejection reasons are persisted and reusable as eval signal.

## 5. Deployment

- Local: Docker Compose (`postgres`, `redis`, `api`, `dashboard`).
- Prod: three services — web service (API + dashboard), managed PostgreSQL, managed Redis. Pinned base images, non-root user, secrets via env vars.

## 6. Open boundaries (not built yet)

- `eval/` harness and `tests/eval` — see `EVALUATION.md`.
- Arkansas market pack — config-gated, deferred.
- CRM integration — export only in v1 (ADR-007).
