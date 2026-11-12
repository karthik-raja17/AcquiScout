# Eval Runs Log

Per EVALUATION.md §1, each re-baseline or freeze requires an entry here.

## 2026-11-04 — golden_v1 label baseline

- **Status:** frozen, no eval run yet
- **File:** `data/raw/golden_v1.json`
- **SHA-256:** `1c33e7c85d931ddba6c5694da2818b59449eb197d0b05dd2ac0f743284b655a1`
- **Records:** 19 mentions / 16 unique entities
- **Verdicts:** 9 qualified / 5 semi_qualified / 5 rejected
- **Pipeline status:** discovery slice built (ADR-027…035); extraction/scoring/eval
  harness not built — no eval scores available
- **Baseline type:** label baseline only (measurement targets defined, no scores recorded)
- **ADR:** ADR-026

Discovery runs are operational records, not eval runs; they live in
`docs/discovery-runs.md`. The latest completed run has no eval score attached.

## 2026-11-11 — discovery crawl-worthiness baseline

- **Status:** baseline, gates not met; Phase 2 resolver/company dedup is the next step
- **Sample:** `data/raw/discovery_eval_v1.json` (17 samples: 9 web, 8 registry)
- **Runner:** `python scripts/eval_discovery.py`
- **Artifact:** `data/generated/discovery_eval/discovery_eval_20261111-105531.json`
  (gitignored)
- **Metrics:**
  - Fetch success (official-company sample): 4/5 = 80.0% (gate ≥90%: fail; Two Rivers
    returned HTTP 403 to plain `httpx`)
  - Crawl-worthy precision: 5/9 = 55.6% (gate ≥80%: fail)
  - Non-target share: 4/9 = 44.4%
  - Root-domain diversity: 8/9 = 88.9%
- **Not measured:** golden-set recall (deliberately not a discovery metric)
- **Interpretation:** discovery recall is broad but directory/government URLs still
  dominate the web sample; the directory resolver and relevance gate are required before
  crawl. No quality improvement is claimed.
- **Superseded:** the fetch metric here used plain `httpx`; v2 below uses Jina and a
  larger sample. Keep this entry for history.

## 2026-11-11 — discovery crawl-worthiness baseline v2 (Jina, 40 samples)

- **Status:** corrected baseline; fetch gate passes, precision gate fails; Phase 2
  resolver/relevance gate is the next step
- **Sample:** `data/raw/discovery_eval_v1.json` v1.1 (40 samples: 32 web, 8 registry)
- **Runner:** `python scripts/eval_discovery.py` (official URLs through `Scraper`/Jina,
  rate-limited to 18 RPM; directory rows are labeled, not fetched)
- **Artifact:** `data/generated/discovery_eval/discovery_eval_20261111-111507.json`
  (gitignored)
- **Metrics:**
  - Official fetch success: 18/18 = 100.0% (gate ≥90%: pass)
  - Crawl-worthy precision: 18/32 = 56.2% (gate ≥80%: fail)
  - Non-target share: 14/32 = 43.8%
  - Root-domain diversity: 25/32 = 78.1%
- **Not measured:** golden-set recall
- **Interpretation:** Jina removes the plain-`httpx` anti-bot false negatives; the
  remaining problem is relevance, not fetchability. Directory/government URLs are still
  43.8% of the web sample.

**Phase 2 note (2026-11-11):** the relevance gate, directory resolver, and company dedup
are implemented. A live smoke run (`20261111-172610`) produced 17 candidates → 13
company candidates, with 4 dropped (1 government, 2 resolved-government directories, 1
unresolved directory). This is operational evidence, **not** a post-resolver eval; the
next eval must run after Phase 2 stabilization.

**Phase 3 note (2026-11-12):** registry enrichment and public evidence resolution are
implemented. Live run (`20261112-132829`) produced 13 company candidates; all 8 registry
records were enriched and resolved to public Sunbiz detail pages, with 0 companies
dropped for missing evidence. GovFiles credits = 10 (2 search + 8 detail). A second
live run (`20261112-133755`) added high-confidence website search: 2 eligible registry
companies were searched, 0 official URLs resolved; Tavily credits = 4.

## 2026-11-12 — resolver/enrichment eval on the baseline batch

- **Sample:** `data/raw/discovery_eval_v1.json` (40 samples: 32 web, 8 registry)
- **Runner:** `python scripts/eval_resolver.py`
- **Artifact:** `data/generated/discovery_eval/resolver_eval_20261112-134514.json`
  (gitignored)
- **Input web:** 32; official 18; crawl-worthy precision 56.2%; non-target 43.8%;
  domain diversity 78.1%
- **Directory resolver:** 12 directory inputs; 3 yielded a company name (25%); 1 yielded
  an official URL (8.3%); 12 directory candidates were dropped
- **Registry:** 8 profiles; 8 public Sunbiz detail pages; 5 with ≥2 officers; 2 active;
  2 eligible for website search; 0 official URLs resolved
- **Output:** 24 companies; 16 web companies with URLs; 8 structured registry records;
  12 drops; actionable rate 66.7%; post-resolver web coverage 50%; output domain
  diversity 100%; post-validation non-target URLs 0
- **Interpretation:** the resolver is the bottleneck (25% name yield, 8.3% URL yield on
  this batch). Registry evidence is strong (8/8 public detail), but only 2 of 8 are
  active and eligible for website search, and those 2 did not resolve. The 16 web
  companies are the surviving official URLs; the output is clean by construction after
  validation, so 100% output precision is not an independent quality claim.

## 2026-11-12 — resolver eval v2 (title fallback + directory URL search)

- **Sample:** same 40-sample baseline batch
- **Runner:** `python scripts/eval_resolver.py`
- **Artifact:** `data/generated/discovery_eval/resolver_eval_20261112-135102.json`
- **Input web:** 32; official 18; crawl-worthy precision 56.2%; non-target 43.8%;
  domain diversity 78.1%
- **Directory resolver:** 12 inputs; 7 yielded a name (58.3%); 1 yielded an official URL
  directly (8.3%); 6 dropped
- **URL search:** 5 eligible; 3 directory-derived companies resolved to official URLs;
  0 registry-derived URLs; Tavily searches = 5
- **Registry:** 8 profiles; 8 public detail; 5 with ≥2 officers; 2 active
- **Output:** 27 companies; 19 web companies with URLs; 8 structured registry records;
  7 drops; actionable 70.4%; web coverage 59.4%; output domain diversity 89.5%;
  post-validation non-target URLs 0
- **Delta vs v1:** directory name yield 25% → 58.3%; web coverage 50% → 59.4%;
  actionable 66.7% → 70.4%; web companies with URLs 16 → 19.
- **Interpretation:** title fallback recovered directory pages that block plain HTTP; URL
  search converted 3 of those names into official sites. The remaining gap is unresolved
  anti-bot/list pages and inactive registry records. Output precision remains clean by
  construction.

## 2026-11-12 — final four-metric eval on the baseline batch

- **Sample:** the fixed 40-sample baseline batch
- **Runner:** `python scripts/eval_resolver.py`
- **Artifact:** `data/generated/discovery_eval/resolver_eval_20261112-163942.json`
- **Baseline (input):** fetch 18/18 = 100%; crawl-worthy 18/32 = 56.2%;
  non-target 14/32 = 43.8%; diversity 25/32 = 78.1%
- **Final (18 web URLs):** fetch 18/18 = 100%; crawl-worthy 18/18 = 100%;
  non-target 0/18 = 0%; diversity 17/18 = 94.4%
- **Coverage:** 18/32 = 56.2%
- **Labeling:** 16 original official URLs reused manual labels; 2 new directory-derived
  URLs judged by `openai/gpt-oss-20b`; judge failures = 0
- **Note:** output precision is high because validation removed non-company URLs; the
  comparison must be read with coverage. One duplicate domain remains
  (`dennisrealtyrentals.com`), hence 17/18 diversity.
- **Registry:** this live run resolved 0 public Sunbiz detail pages (Jina returned empty
  for Sunbiz), so registry-only records were dropped and are excluded from the four web
  metrics. This is a live-source limitation, not a web metric regression.

## 2026-11-12 — fixed-denominator impact eval

- **Sample:** fixed 40-sample baseline, unchanged (32 web, 8 registry)
- **Runner:** `python scripts/eval_resolver.py`
- **Artifact:** `data/generated/discovery_eval/resolver_eval_20261112-165800.json`
- **Fixed denominator:** 32 input web URLs; merged inputs count as `official` when
  they map to an official final company.
- **Baseline outcome:** official 18/32 = 56.2%; non-target 14/32 = 43.8%;
  unresolved 0/32 = 0%
- **Test outcome:** official **21/32 = 65.6%**; non-target **3/32 = 9.4%**;
  unresolved **8/32 = 25.0%**
- **Non-target conversion:** 3/14 = **21.4%**
- **Final unique web URLs:** 18; fetch success 18/18 = 100%; conditional precision
  18/18 = 100%; conditional diversity 17/18 = 94.4%
- **Baseline official-only diversity:** 17/18 = 94.4% (the recorded 25/32 is all-web
  input diversity)
- **Registry:** live Sunbiz evidence returned empty from Jina again (0/8 public detail),
  so registry-only records were dropped and excluded from the web metrics.
- **Impact:** +3 input URLs became official; non-target inputs fell 14 → 3; 8 remain
  unresolved. Unique final URL count stayed 18 because dedup merged 3 original official
  inputs into existing companies.
- **Conversion composition:** of the 3 converted directory inputs, 2 mapped to existing
  companies (`tampatitle.com` was an original official input;
  `dennisrealtyrentals.com/hillsborough-property-management` shares Dennis's domain) and
  1 was new (`oldtampabaytitle.com`). Net-new companies: **+1**. Directory conversion
  was 3/12 = 25%; net-new conversion was 1/12 = 8.3%.
- **Decision:** directory resolution is off by default (`ENABLE_DIRECTORY_RESOLVER=false`);
  the simple graph keeps registry enrichment/evidence. See ADR-043 and
  `docs/resolver-experiment.md`.

When the pipeline produces its first eval run, add a new entry below with:

- Run date, commit SHA, judge model + version, prompt hash
- Metrics from EVALUATION.md §2 (each with actual value vs threshold)
- Cost per qualified prospect
- Pass/fail on blocking metrics

Do not edit existing entries. Append new ones.
