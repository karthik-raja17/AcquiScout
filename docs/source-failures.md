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

## Future sources

The resolver is still Sunbiz-shaped:

- `DETAIL_LINK` only matches Sunbiz `SearchResultDetail` links.
- `_parse_detail` expects Sunbiz page structure.
- `myfloridalicense.com` (DBPR) and `dos.fl.gov` (DFS) are approved by policy but
  do not have parsers yet.

Source roles:

| Source | Role |
|---|---|
| Sunbiz | G3 / legal identity / officers / status |
| DBPR | Property-management / real-estate license evidence |
| DFS | Title agency / agent license evidence |
| GovFiles | Discovery and enrichment only, never evidence |
