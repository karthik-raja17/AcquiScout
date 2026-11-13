# Source blocks and host circuit breaker

## Observed failure

On 2026-11-12, Sunbiz returned a Cloudflare challenge:

```text
Direct Sunbiz request: 403, title "Just a moment..."
Jina request:          200, body:
                       Warning: Target URL returned error 403: Forbidden
                       Markdown Content:
                       Enable JavaScript and cookies to continue
```

This is a target-side block, not a Jina outage. Jina called Sunbiz
successfully; Sunbiz/Cloudflare rejected the request.

Earlier the same URLs returned 1,088–10,542 characters and produced
`public_detail` evidence. The later run produced 0 characters.

## Evidence statuses

`RegistryProfile.evidence_status` now distinguishes:

| Status | Meaning |
|---|---|
| `public_detail` | Public registry detail page fetched and parsed |
| `search_page_only` | Search page fetched successfully, no matching detail link |
| `blocked` | 401/403, Cloudflare, captcha, or access-denied page |
| `rate_limited` | 429 |
| `http_error` | Other HTTP failure |
| `network_error` | Timeout, DNS, or transport failure |
| `low_quality` | Fetched, but text was too short or a block marker was present |
| `unsupported_source` | URL is not an approved registry host |
| `parse_failed` | Detail fetched but fields could not be parsed |
| `failed` | Resolver unavailable or unclassified failure |
| `none` | Not attempted |

Each profile also carries:

```text
evidence_reason
evidence_source      # sunbiz / dbpr / dfs
evidence_http_status
evidence_backend     # jina / firecrawl / circuit
evidence_checked_at
```

`registry_evidence/<hash>.json` and `dropped.json` record the same diagnostic
fields. A blocked fetch is never reported as `search_page_only`.

## Host circuit breaker

The breaker is keyed by **target host**, not by Jina:

```text
search.sunbiz.org
www.myfloridalicense.com
dos.fl.gov
```

States:

```text
CLOSED     requests pass through
OPEN       later requests short-circuit with host_circuit_open
HALF-OPEN  after cooldown, exactly one probe is allowed
```

On a blocked or rate-limited result, the circuit opens for the cooldown. Later
URLs on that host return `blocked` with `evidence_reason="host_circuit_open"`
without a network call. After the cooldown, one probe runs:

- success → close
- blocked again → reopen
- another request during the probe → short-circuit

Configuration:

```powershell
SCRAPE_CIRCUIT_ENABLED=true
SCRAPE_CIRCUIT_FAILURE_THRESHOLD=1
SCRAPE_CIRCUIT_COOLDOWN_SECONDS=600
```

The breaker is in-memory for the current process. If multiple API workers scrape
concurrently, move the state to Redis with a TTL.

## Measured effect

| Run | Evidence node | Total run |
|---|---:|---:|
| Before breaker (`20261112-173436`) | 5075 ms | 7145 ms |
| After breaker (`20261112-181339`) | 617 ms | 2553 ms |

The after-run made one Jina call, recorded `cloudflare_challenge` with HTTP 403,
and short-circuited seven records with `host_circuit_open`. `enrich_registry`
(1863 ms) is now the largest remaining node.

## What it does not do

- It does not bypass Cloudflare, captchas, logins, or access controls.
- It does not treat a `404` or a parse failure as a host outage.
- It does not change the frozen discovery baseline.

## Source adapters

Evidence dispatch is host-based through `RegistrySource`:

| Source | Host | Parser |
|---|---|---|
| `SunbizSource` | `sunbiz.org` | Follows `SearchResultDetail`, parses officers/status |
| `DBPREvidenceSource` | `myfloridalicense.com` | Parses license fields from the supplied page |
| `DFSEvidenceSource` | `dos.fl.gov` | Parses license fields from the supplied page |

License adapters store:

```text
license_number
license_type
license_status
license_expiration
licensee_name
registered_address
```

If a license page has no recognizable fields, the result is
`search_page_only` (`no_license_detail`) when it looks like a search page, or
`parse_failed` (`no_license_fields`) otherwise. They do not infer a license from
missing data.

Source roles:

| Source | Role |
|---|---|
| Sunbiz | G3 / legal identity / officers / status |
| DBPR | Property-management / real-estate license corroboration |
| DFS | Title agency / agent license corroboration |
| GovFiles | Discovery and enrichment only, never evidence |

Registry-only companies with only DBPR/DFS evidence are dropped as
`missing_sunbiz_identity_evidence`; a license is not a substitute for corporate
identity evidence.

## Metrics by source

`registry_metrics` is written to `manifest.json` and completed-run exports:

```json
{
  "profiles": 8,
  "status_counts": {"blocked": 7, "public_detail": 1},
  "source_counts": {"sunbiz": 8},
  "public_detail_by_source": {"sunbiz": 1},
  "dropped_by_reason": {"registry_evidence_blocked": 8}
}
```

This makes blocked/partial coverage visible separately for Sunbiz, DBPR, and DFS.
