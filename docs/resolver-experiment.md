# Directory resolver experiment

Status: closed for v1; code retained behind `ENABLE_DIRECTORY_RESOLVER`.

## Question

Do directory pages (LeadIQ, MapQuest, RocketReach, property-management
directories) add unique companies, or do they mostly duplicate companies already
found through official websites and GovFiles?

## Baseline

Frozen sample: `data/raw/discovery_eval_v1.json` (40 records: 32 web inputs,
8 registry inputs). Labels were not changed.

| Metric | Baseline |
|---|---:|
| Fetchable official URLs | 18/18 (100%) |
| Crawl-worthy official URLs | 18/32 (56.2%) |
| Non-target inputs | 14/32 (43.8%) |
| Domain diversity | 25/32 (78.1%) |

## Final fixed-denominator run

Artifact: `data/generated/discovery_eval/resolver_eval_20261112-165800.json`.

Protocol: every one of the 32 web inputs maps to exactly one outcome;
merged inputs count as `official`; denominator stays 32.

| Metric | Baseline | Resolver |
|---|---:|---:|
| Official input mappings | 18/32 (56.2%) | 21/32 (65.6%) |
| Non-target inputs | 14/32 (43.8%) | 3/32 (9.4%) |
| Unresolved inputs | 0/32 (0%) | 8/32 (25.0%) |
| Non-target conversion | — | 3/14 (21.4%) |
| Final unique web URLs | 18 | 18 |
| Final fetch | 18/18 | 18/18 |
| Conditional precision | 18/18 | 18/18 |
| Conditional diversity | — | 17/18 (94.4%) |

## What the three conversions were

| Directory input | Official URL | Result |
|---|---|---|
| MapQuest, Tampa Title | `tampatitle.com` | Duplicate: already an original official input |
| LeadIQ, Old Tampa Bay Title | `oldtampabaytitle.com` | New company |
| MapQuest, Hillsborough Rental | `dennisrealtyrentals.com/hillsborough-property-management` | Duplicate domain: same root as existing Dennis company |

Only one of the three conversions was a genuinely new company. The
Hillsborough Rental record was not merged because company dedup runs before URL
search; a post-URL dedup would reduce the final unique count from 18 to 17.
Net new companies: **+1**.

The apparent "non-target 14 -> 3" is misleading: three inputs became official,
but eight became unresolved, not usable. Directory conversion was 3/12 (25%),
and net-new conversion was 1/12 (8.3%). The two government inputs were correctly
never converted.

## Registry enrichment

Registry enrichment was not the problem. In the earlier live run
`20261112-132829`, GovFiles + public evidence produced 8/8 public Sunbiz detail
records, 5/8 with at least two officers, and 8 GovFiles credits. In the final
fixed run, Jina returned empty for Sunbiz (`jina_low_quality`), so registry
evidence was 0/8 and registry-only records were dropped. That is a fetch
reliability issue to fix separately; it is not a reason to remove registry
enrichment.

## Decision

Use the simpler graph by default:

```text
relevance -> enrich_registry -> resolve_registry_evidence -> company_dedup
  -> screen_companies -> END
```

Directory candidates are dropped at relevance with reason `directory_skipped`.
The resolver, company URL search, and URL judge remain available behind:

```powershell
$env:ENABLE_DIRECTORY_RESOLVER='true'
```

The full experimental graph is:

```text
relevance -> resolve_directories -> enrich_registry
  -> resolve_registry_evidence -> company_dedup -> resolve_company_urls
  -> screen_companies -> END
```

## Why

- One net new company per 12 directory inputs.
- Two of three conversions duplicated existing companies.
- Eight of twelve directories remained unresolved.
- Extra fetches, Tavily searches, GPT calls, and duplicate cleanup for marginal
  gain.
- Registry enrichment is independently valuable and stays on.

## Revisit criteria

Retest the resolver only if all of these hold:

1. Blocked directory pages are handled (Jina or a browser-backed fetch).
2. Company dedup runs after URL search, so duplicate domains merge.
3. Net-new companies are at least 25% of directory inputs.
4. Cost per net-new company is recorded and acceptable.

## Reproduce

```powershell
python scripts/eval_resolver.py
python -m pytest -q --tb=short
ruff check .
```

The eval script makes live Tavily/GovFiles/Jina calls; it is not offline.
