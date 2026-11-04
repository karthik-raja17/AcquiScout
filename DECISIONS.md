# DECISIONS.md

Architecture Decision Records (ADRs). Short, dated, reversible-by-ADR. Status values: `accepted`, `superseded`, `deferred`.

**Recording rule (automatic):** an ADR is written whenever (a) a non-obvious design choice is made in code or docs, (b) the client answers a deferred question from `PROJECT.md` §12, or (c) a prior decision is replaced. Superseded ADRs are never deleted — they are marked `superseded by ADR-NNN` and left in place.

---

## ADR-001 — v1 market boundary is county-based

- **Status:** accepted
- **Context:** The client's footprint spans Tampa–St. Petersburg–Clearwater; the client's business ZIP straddles Tampa city and unincorporated Hillsborough. City-limits targeting would drop valid targets.
- **Decision:** Tampa Bay = Hillsborough + Pinellas + Pasco. Counties live in config (`Settings.counties`), not code.
- **Consequence:** Hernando/Manatee/Sarasota can be enabled later by config change only.

## ADR-002 — Sectors limited to title and property management

- **Status:** accepted
- **Decision:** `Sector` enum has exactly `title_company` and `property_management` in v1.
- **Consequence:** Adding a sector is a schema + prompt change → requires an eval run.

## ADR-003 — Scoring is deterministic, LLM-as-Judge is alignment-only

- **Status:** accepted
- **Decision:** The score is computed from explicit weighted components (financial 30, management 20, ownership 15, contactability 15, market 10, data confidence 10). The judge model is used to compare against the rubric, not to produce the pipeline score.
- **Consequence:** Scores are reproducible and auditable; judge drift cannot silently reorder the pipeline. Weights are config (must sum to 100 — enforced by schema).

## ADR-004 — Estimates are typed, never free text

- **Status:** accepted
- **Decision:** Revenue/EBITDA are `FinancialEstimate` objects with value, low/high band, confidence, method, and source URLs. A bare number without method + source is rejected at validation.
- **Consequence:** "Proxy is not truth" is enforced by the type system, not by reviewer discipline.

## ADR-005 — Contacts require explicit verification state

- **Status:** accepted
- **Decision:** Every contact carries `verification_status` (`unverified`, `single_source`, `corroborated`, `human_audited`) and ≥1 source. A prospect may only be `verified` if at least one contact is `corroborated`/`human_audited` and all estimates carry sources.
- **Consequence:** Stale or unsourced contacts cannot reach the "verified" funnel stage.

## ADR-006 — Audit sample: random 20% weekly, >5% critical-field error fails

- **Status:** accepted (default assumption, configurable)
- **Decision:** Weekly verification audit draws a random 20% of verified prospects; a critical-field error rate above 5% fails the week and blocks the report.
- **Consequence:** Sample metadata must be persisted with each weekly run.

## ADR-007 — CRM output is CSV/JSON only in v1

- **Status:** accepted
- **Decision:** No HubSpot/native CRM integration. Export is a stable column contract.
- **Consequence:** Column changes are breaking changes → bump export schema version.

## ADR-008 — Packaging is pip + pyproject (no uv)

- **Status:** accepted
- **Context:** `uv` was not present in the environment at project start; the existing Python 3.13 `.venv` is used.
- **Decision:** `pip install -e ".[dev]"`. Dependencies are split into extras (`agents`, `api`, `ui`, `scrape`, `eval`, `dev`) so the scaffold stays light and install cost is explicit.
- **Consequence:** Reproduce environments from `pyproject.toml`; no lockfile in v1.

## ADR-009 — Evaluations gate merges on thresholds, not on absolute quality

- **Status:** accepted
- **Decision:** Thresholds live in `EVALUATION.md` and are copied into CI config. A run outside thresholds blocks merge; improvements inside thresholds do not require re-baselining.
- **Consequence:** Metrics must be machine-readable and stored per run.

## ADR-010 — Cost target is $5 per qualified prospect

- **Status:** accepted — **measurement clause superseded by ADR-018**
- **Decision:** Every prospect carries token/cost/latency telemetry aggregated per run; the weekly report exposes cost per qualified prospect.
- **Consequence:** Model routing and cache-aside are required, not optional optimizations.

## ADR-011 — A score is self-describing

- **Status:** accepted
- **Context:** If weights live only in config, a stored score cannot be re-interpreted later; if the total is stored independently, it can drift from its parts.
- **Decision:** `ScoreBreakdown` embeds the `ScoreWeights` used, validates each component against its own cap, and derives `total` as a computed field. `total` is never accepted as input.
- **Consequence:** Re-scoring with new client weights produces a different object, not a mutated number. Stored scores remain auditable by construction.

## ADR-012 — Market and funnel are configuration, validated at load

- **Status:** accepted
- **Context:** PROJECT.md §12 answers must be config changes, not code changes. Silent bad config would corrupt every downstream metric.
- **Decision:** counties, sectors, weights, thresholds, funnel targets, audit rate, and cost target live in `Settings` (`pydantic-settings`). Validation rejects an empty market, an audit rate outside `(0, 1]`, and a non-monotonic weekly funnel.
- **Consequence:** Enabling Hernando/Manatee/Sarasota or Arkansas is an env/config edit. A funnel like `500 → 150 → 600` fails fast instead of producing nonsense reports.

## ADR-013 — Provenance is immutable; trust is one explicit set

- **Status:** accepted
- **Context:** Sources are the audit trail. If a `SourceRef` can be mutated after the fact, provenance proves nothing. Trust decisions were otherwise scattered across ad-hoc status comparisons.
- **Decision:** `SourceRef` is a frozen model. "Trusted contact" is defined once as `TRUSTED_CONTACT_STATUSES = {corroborated, human_audited}` and surfaced as `Contact.is_trusted`; the `Prospect` verified-gate consumes that property.
- **Consequence:** Adding a future trust level (e.g. `registry_confirmed`) is a one-line change with schema-level effect, not a codebase-wide search.

## ADR-014 — Scrape order is Jina first, Firecrawl only on explicit fallback

- **Status:** accepted
- **Context:** Jina AI Reader is free for basic `r.jina.ai/<url>` reads; Firecrawl credits are a one-time allowance (500). Scraping is the highest-volume paid action in the pipeline.
- **Decision:** `sources/scrape.py` always attempts Jina first. Firecrawl is called only when Jina's text fails the quality gate (length + block markers) **and** `scrape_fallback_to_firecrawl` is true. The flag defaults to false so dev runs cannot silently burn credits.
- **Consequence:** URLs that fail Jina are visible in run logs (`reason`) and become the evidence for deciding when Firecrawl is genuinely needed. Policy is enforced before the cache lookup, so blocked URLs never touch the network or cache.

## ADR-015 — Dev model routing: local extract, free-tier reason, free-tier judge

- **Status:** accepted
- **Decision:** Extraction runs on local Ollama Llama-3-8B Q4_K_M (RTX 3070 Ti, ~5.5 GB VRAM). Judgment/reasoning uses the Groq free tier. LLM-as-a-Judge runs on Gemini 2.5 Flash free tier.
- **Consequence:** No paid inference is required to reach a demoable v1. The judge model and prompt hash stay pinned in every run record (`EVALUATION.md` §3), so switching to a paid model is a deliberate, recorded re-baseline.

## ADR-016 — Local Docker Compose is the primary dev loop; cloud is demo-only

- **Status:** accepted
- **Context:** Render's free PostgreSQL expires after 90 days and its free web service spins down after 15 minutes; neither suits a weekly cadence.
- **Decision:** Day-to-day development uses `docker compose` (postgres, redis, jaeger). Cloud dev/demo uses Neon (Postgres) + Upstash (Redis) + a free-tier app host. No paid infrastructure before the engagement converts.
- **Consequence:** Compose images are pinned by tag, not `:latest`. Cloud manifests are not committed until the demo target is chosen; scheduled runs use GitHub Actions rather than platform cron.

## ADR-017 — Observability is local Jaeger during dev; Langfuse deferred

- **Status:** accepted
- **Decision:** Traces go to the Jaeger all-in-one container (OTLP 4318, UI 16686); structured JSON logs go to local files. Langfuse is introduced only when demo traces are needed.
- **Consequence:** No observability vendor is required for the free build; trace data never leaves the machine during development.

## ADR-018 — Cost is blocking only on a paid-tier reference run

- **Status:** accepted — supersedes the measurement clause of ADR-010
- **Context:** On the free stack, run cost is $0, so a "<$5" check passes trivially and proves nothing.
- **Decision:** Dev runs report token counts and free-tier budget consumption (Tavily/Firecrawl credits, Groq tokens, Gemini requests) against `Settings` budgets. The $5 per qualified prospect threshold is measured once on a paid-tier reference run and recorded in `docs/eval-runs.md` before any demo claim.
- **Consequence:** CI may gate on free-tier budget exhaustion and token counts; it must not claim the cost target. `EVALUATION.md` §2 and §6 encode this distinction.

## ADR-019 — VibeWise is vendored as a project skill, not installed as a plugin

- **Status:** accepted
- **Context:** VibeWise ships as a Claude Code / Codex plugin. Neither CLI is installed in this environment, and the plugin's `hooks/session_start.py` targets a hook surface opencode does not expose.
- **Decision:** Port the four guidance documents (`SKILL.md`, `behavior.md`, `onboarding.md`, `state-templates.md`) into `.opencode/skills/vibe-wise/`, register the path in `opencode.json`, and keep the upstream MIT notice alongside. Learning state lives in `.vibe-wise/` and is gitignored.
- **Adaptations made:** Claude/Codex-specific instructions removed; `AskUserQuestion` mapped to opencode's `question` tool; the Implementation checkpoint is explicitly tied to the repo `APPROVED` gate (ADR-019 does not weaken ADR-003 or the §5 workflow); no session-start hook — state restoration is triggered by loading the skill.
- **Consequence:** Learning mode works in opencode without adding a runtime dependency. Upstream updates must be re-vendored manually; provenance is recorded in the skill frontmatter.

## ADR-020 — One authorization token, two records (amends ADR-019)

- **Status:** accepted — amends the implementation note in ADR-019
- **Context:** Mapping the VibeWise Implementation checkpoint directly onto the repo `APPROVED` rule made the checkpoint look like a label rather than the technical gatekeeper, so `.vibe-wise/` would not show who authorized what. Splitting it into two independent gates would deadlock skips, docs-only fixes, and non-learning sessions.
- **Decision:** Keep `APPROVED` as the single authorization token. In learning mode it may only be requested at the Implementation checkpoint, which must first persist a pending decision (stage: `implementation approval`, with scope) to `.vibe-wise/progress.md`. Approval records the outcome in `progress.md` and `project-map.md`. Outside learning mode, `AGENTS.md` §5 alone governs.
- **Consequence:** One interaction per step, no dual-gate deadlock, and an auditable record of the gatekeeper and the exact scope it authorized. A `skip` waives the reasoning loop, never the token.

## ADR-021 — Golden-set source compromises (pilot)

- **Status:** accepted
- **Context:** During pilot labeling, some preferred sources were unavailable and some mentions share evidence. The golden set has to record those compromises instead of hiding them.
- **Decision:** Record four compromises. (1) BisProfiles returned 404, so `golden-016` uses the Sunbiz detail page as its legal-name-only primary source, while `golden-017` uses the company facts page as its DBA-only primary source. (2) A mention's primary source must display the name that mention represents: the registry for legal-name mentions, the DBA site for DBA mentions. (3) `golden-016` and `golden-017` share a principal source; that is a merge signal, not a collapse, because their primary displayed names differ. (4) `golden-016`'s contact evidence is DBA-branded because the phone appears only on the DBA site; that is an auxiliary-source impurity, not a primary one.
- **Consequence:** Dedup must not merge on shared principal evidence alone, and scoring must key on the primary displayed name rather than contact branding.

## ADR-022 — Management evidence (G3) registry interpretation

- **Status:** accepted
- **Context:** Many founder-owned title and property-management firms have no marketing team page, so requiring one would reject firms whose management team is documented in state filings.
- **Decision:** A state registry page (Sunbiz or DBPR) that lists at least two named officers with roles satisfies `management_evidence_url` for G3. A marketing team page is preferred but not required. A single named officer does not satisfy the rule.
- **Consequence:** G3 evidence can come from the registry even when `management_evidence_url` equals `principal_source_url`, provided the page shows at least two named people. Management still scores 20 on that evidence.

## ADR-023 — Risk flags for due diligence

- **Status:** accepted
- **Context:** `GoldenRecord` v1 has no `risk_flags` field, but acquisition screening can surface material due-diligence concerns that are independent of financial qualification. A free-text label would be unauditable, and an unsourced allegation would violate the no-fabrication rule.
- **Decision:** v1 does not add `risk_flags` to the label schema. Material risks are tracked outside the schema, keyed by `company_id`, with `source_url`, `method`, `retrieved_at`, and `confidence` for every entry. Unsourced reports are not recorded.
- **Consequence:** Risk evidence stays auditable and separate from qualification scores; the golden JSON artifacts remain unchanged. Adding structured risk fields requires a later schema version and a new ADR.

## ADR-024 — Out-of-geography mentions

- **Status:** accepted
- **Context:** A target can surface through a local branch while its headquarters sits outside the three target counties. `primary_county` is defined per mention, so recording the observed county does not mean the business is market-based.
- **Decision:** For an out-of-geography entity, `primary_county` records the county where the mention was observed; the HQ county goes in `also_serves_counties`; `score_market` is 0; `human_verdict` is `rejected` with a rejection reason naming the HQ county. Geography is a qualification gate (G4), not only a score component.
- **Consequence:** `also_serves_counties` carries the HQ county for these rows, so "serves" also means "based in" there; a dedicated `hq_county` field would remove the ambiguity. Market full or partial credit applies only to entities headquartered in Hillsborough, Pinellas, or Pasco.

## ADR-025 — Independence gate (G5)

- **Status:** accepted
- **Context:** A locally branded firm can be owned by a regional or national roll-up. Financial qualification alone would treat it as an acquisition target when it is not independently acquirable.
- **Decision:** Add G5 as a qualification gate. A brand of a multi-brand roll-up, or a subsidiary of a larger operator, is `rejected` regardless of G1-G4, and `rejection_reason` names the parent or roll-up. The relationship should be evidenced by a source on the row.
- **Consequence:** Roll-up brands cannot be `qualified` or `semi_qualified`. Scores still follow the anchors; only the verdict changes.

## ADR-026 — golden_v1.json frozen at 19 records / 16 unique entities (contract deviation documented)

- **Status:** accepted
- **Date:** 2026-11-04
- **Context:** `EVALUATION.md` §1 targets ~20 unique entities across 25-30 mentions. After two full screening rounds (17 candidates across PM and title verticals), the golden set reached 19 mentions / 16 unique entities. Screening yield was ~18%: 3 of 17 passed screening; of those, Milestone Title Services was full-researched and qualified at 72, Eaton Realty was full-researched and rejected on size (375 doors, $540K-$900K derived revenue), and Assurity Title passed screening but was not researched. The 500+ door / $1.5M+ revenue independent segment is genuinely rare in Tampa Bay. Pre-freeze verification (Checks A-I) confirmed all 19 records pass schema, score math, band sanity, verdict logic, contact rules, URL format, duplicate consistency, and known caveats. Zero blocking failures.
- **Decision:** Freeze `data/raw/golden_v1.json` at 19 records / 16 unique entities. Amend `EVALUATION.md` §1 to reflect the actual contents. This is a **label baseline**, not a scored baseline — no eval run exists yet (pipeline not built).
- **Coverage achieved:**
  - 9 qualified (5 title: Turner, Tampa Title, T&A, ARETSI, Milestone; 4 PM: Greenacre, Resource, Wise, Condo Associates)
  - 5 semi_qualified (Rent Solutions x3 with G2 miss; Hillsborough Title, Integrity First with no team page)
  - 5 rejected (2 too small: RentWorks, Hoffman; 1 national subsidiary pair: Secured Land x2; 1 out-of-geography: Leland)
  - 3 duplicate patterns: TYPE A (Secured Land: two DBAs), TYPE B (MYM Solutions: legal vs DBA), TYPE C (MYM Solutions: same name two sources)
- **Rationale:** 19 well-curated records with this diversity are sufficient to measure pipeline qualification accuracy, rejection accuracy, and borderline sensitivity. Reaching 20 unique would require ~5+ more candidate screens at the observed yield. Version bumping (`golden_v2.json`) is the designed mechanism for expansion per `EVALUATION.md` §1.
- **Alternatives considered:**
  1. Expand to 20/25-30 before freeze — estimated 15-20 more screens; not economically viable at this stage
  2. Relax the 500-door threshold — would compromise the brief
  3. Include Arkansas in v1 — `PROJECT.md` §6 explicitly defers it
- **Consequences:**
  - `golden_v1.json` is frozen and immutable
  - `scripts/freeze_golden.py` writes it once; refuses to overwrite
  - Future expansions happen via `golden_v2.json` with a corresponding ADR
  - Future eval runs reference this file by SHA-256
- **SHA-256:** `1c33e7c85d931ddba6c5694da2818b59449eb197d0b05dd2ac0f743284b655a1`

---

## Resolved client questions

When PROJECT.md §12 questions are answered, record: date, question, answer, and which config key changed.

| Date | Question | Answer | Config key |
|---|---|---|---|
| _(none yet)_ | | | |
