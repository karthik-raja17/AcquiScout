# PROJECT.md

> **AcquiScout — Business Context, Problem Statement, and Success Contract**
>
> This document explains **what** AcquiScout is, **why** it exists, **for whom**, and **what success looks like**. It is written for humans — the client, hiring reviewers, and future contributors.
>
> For operational rules, agent behavior constraints, tech-stack locks, code conventions, and eval discipline, see [`AGENTS.md`](./AGENTS.md).
> For design rationale, see [`DECISIONS.md`](./DECISIONS.md).
> For how to install and run, see [`README.md`](./README.md).

---

## 1. Executive Problem Statement

**The client needs a repeatable, auditable system that produces 20+ qualified acquisition prospects per week in title companies and property management firms across the Tampa Bay area.** Each prospect must meet the financial and management profile — **$1.5M+ annual revenue, $500K+ EBITDA proxy, existing management team** — and must include verified contact information, ownership structure, revenue/EBITDA estimates with confidence bands, source URLs, an acquisition-potential score, and CRM-ready pipeline data.

Today, this work is manual, fragmented across public records and web sources, inconsistent in qualification, difficult to audit, and too slow to sustain a weekly 20-prospect pipeline at **>95% verified contact/financial information**.

The core problem is **not** "build a scraper" or "build an LLM agent." The core problem is:

> **Produce trustworthy, source-backed, prioritized M&A pipeline decisions under data scarcity, compliance constraints, and weekly cadence.**

AcquiScout must automate discovery, deduplication, scraping, structured extraction, entity resolution, scoring, verification, human review, reporting, and CRM/dashboard delivery — while never presenting unverified estimates as facts.

---

## 2. Client and Business Context

| Field | Value |
|---|---|
| **Client** | Private equity / M&A advisory (name withheld) |
| **Engagement shape** | $7k/mo, 6 months, competitive applicant pool |
| **Markets (v1)** | Tampa Bay area, Florida |
| **Sectors (v1)** | Title companies, property management firms |
| **Target profile** | $1.5M+ annual revenue, $500K+ EBITDA proxy, existing management team |
| **Required cadence** | 20+ qualified prospects/week, weekly pipeline reports, sample verification audits |
| **Business decisions supported** | Which companies to prioritize for outreach, which to investigate further, which to reject, and how the pipeline trends week over week |

The agent is a **decision-support and pipeline-generation system** — not an autonomous outreach system, and not a replacement for financial due diligence.

### Why Tampa Bay (and not strict Tampa city)

The client's operating footprint spans the Tampa–St. Petersburg–Clearwater metro (Hillsborough, Pinellas, Pasco counties), not just the city limits. The client's business address sits in a ZIP that straddles Tampa city and unincorporated Hillsborough — so strict city-limits targeting would exclude relevant targets. M&A sourcing operates on functional markets, not legal boundaries.

**v1 boundary (fixed):** Tampa Bay = **Hillsborough + Pinellas + Pasco** counties. Hernando, Manatee, and Sarasota are out of scope for v1 and can be enabled later via config.

---

## 3. Current-State Pain

Without AcquiScout, the client/analyst must manually:

1. Search for title and property management companies across the region.
2. Check whether each company plausibly meets revenue and EBITDA thresholds.
3. Find ownership structure, principals, and management-team evidence.
4. Find and verify contact information.
5. Deduplicate companies with similar names, rebrands, DBAs, and multi-location offices.
6. Score acquisition potential consistently.
7. Maintain CRM/outreach status.
8. Produce weekly reports and market intelligence.
9. Audit claims after the fact.

This creates five problems:

| Problem | Consequence |
|---|---|
| **Coverage** | Analysts miss target companies because discovery is manual and local. |
| **Consistency** | Qualification and scoring vary by analyst and day. |
| **Trust** | Revenue/EBITDA estimates and contacts are often unsourced or stale. |
| **Cadence** | 20+ verified prospects/week is not sustainable manually. |
| **Auditability** | Hard to prove why a company was included, rejected, or ranked highly. |

---

## 4. Desired Future State

AcquiScout runs a weekly pipeline cycle:

**Discover → deduplicate → scrape → extract → enrich → estimate financials → score → verify → human review → CRM/dashboard → weekly report.**

Intended throughput:

**~500 raw discovered → ~150 deduplicated → ~60 scraped/extracted → ~25 qualified → 20 verified prospects per week.**

Every prospect record must answer:

- Who is this company?
- Where is it located and what does it do?
- Does it plausibly meet $1.5M+ revenue and $500K+ EBITDA?
- How was that estimate derived, and with what confidence?
- Who owns/manages it?
- How do we contact them?
- Why does it score as a high-priority acquisition target?
- What sources support each claim?
- Has a human verified it?
- What is the outreach/CRM status?

---

## 5. Users and Stakeholders

| User | Need |
|---|---|
| **Client / PE analyst** | Weekly prioritized prospect list, rationale, confidence, sources, CRM status. |
| **Research operator / reviewer** | Human-in-the-loop review, rejection reasons, verification audits. |
| **Engineer / maintainer** | Reproducible evals, observability, retries, caching, deployment, security. |
| **Compliance / security reviewer** | Approved-source enforcement, no prohibited scraping, API auth, prompt-injection defense. |

---

## 6. Scope

### In Scope (v1)

- Discovery from approved web sources and public registries.
- Search/scraping via Tavily/Serper and Firecrawl/Jina AI Reader, plus a GovFiles
  registry adapter (personal-project exception, ADR-028).
- Structured company profiles using Pydantic v2.
- Entity resolution and deduplication.
- Revenue and EBITDA proxy estimation with explicit confidence bands and methods.
- Ownership/management extraction.
- Contact discovery and verification.
- Acquisition-potential scoring using explicit criteria and LLM-as-a-Judge alignment.
- Human-in-the-loop review and rejection feedback.
- Weekly target lists, pipeline reports, and market-intelligence summaries.
- CRM-ready output via dashboard/export.
- Eval harness, golden dataset, CI regression gates.
- Observability: tokens, cost, latency, cost per qualified prospect.
- Security: API key auth, prompt-injection guard, approved-source policy, Docker security basics.
- Local Docker Compose; production deployment as three services (Web Service, Managed PostgreSQL, Managed Redis).

### Out of Scope (v1)

- Sending outreach (emails, LinkedIn messages, contact forms).
- Legal, accounting, or full financial due diligence.
- Guaranteeing actual EBITDA — only proxy estimates with confidence bands.
- Scraping prohibited, paywalled, logged-in, or private sources.
- Native HubSpot/CRM integration (deferred to Week 10+; CSV/JSON export only).
- Automated acquisition decisions without human review.
- Horizontal scaling on local GPU (local 8GB RTX 3070 Ti handles Llama-3-8B in 4-bit; scale requires Groq/Gemini API).
- Arkansas market (second market pack after Tampa Bay pilot works).
- Hernando, Manatee, Sarasota counties (config toggle later).

---

## 7. Target Definition and Qualification

A **qualified prospect** is a title company or property management firm that:

1. Operates in the Tampa Bay area (Hillsborough, Pinellas, or Pasco county).
2. Plausibly has **$1.5M+ annual revenue**.
3. Plausibly has **$500K+ EBITDA**, estimated via proxy indicators with confidence bands.
4. Has an existing management team.
5. Has sufficient ownership/contact/company data to support outreach.
6. Meets the client's acquisition-potential scoring threshold.
7. Has every critical claim tied to a source URL and method.
8. Has passed verification or is clearly marked "needs human review."

Financial proxies may include office count, headcount, transaction volume, units under management, fee income signals, website claims, registry filings, and public listings. The system must **never** present proxy EBITDA as audited fact.

---

## 8. Required Outputs

Each prospect profile includes:

- Legal/trade name, DBA, website, location(s), sector.
- Revenue estimate, EBITDA estimate, confidence band, estimation method.
- Ownership structure and management-team evidence.
- Contact info and verification status.
- Source URLs for key claims.
- Acquisition score, rationale, and risk flags.
- Entity-resolution/dedup status.
- Human-review status and rejection reason if applicable.
- Outreach/CRM status.
- Last-updated timestamp.

Weekly deliverables:

- 20+ verified, prioritized prospects.
- Weekly pipeline report.
- Sample verification audit.
- Market-intelligence summary on industry trends and competitive landscape.
- Funnel metrics and cost per qualified prospect.

---

## 9. Success Criteria and Acceptance

| Area | Target |
|---|---|
| Qualified leads | 20+ verified prospects/week |
| Contact/financial verification | >95% on sample audits |
| EBITDA band alignment | 70–85% alignment on golden/eval set |
| Scoring alignment | ±10 points on 80% of golden set |
| Funnel tracking | 500 raw → 150 dedup → 60 scraped → 25 qualified → 20 verified |
| Cost efficiency | <$5 cost per qualified prospect |
| Auditability | Every critical claim has source/method/confidence |
| Reliability | Retries, caching, DLQ for failed scrapes |
| Security | API key auth, prompt-injection guard, approved-source policy |
| CI/CD | Eval-gated regression tests in GitHub Actions |
| Deployment | Render/Railway three-service pattern; Docker Compose local dev |

**"Verified" means** checked against a source and/or human-audited — **not** guaranteed audited financial truth.

---

## 10. Constraints and Assumptions

- **Timeline:** 8-week full-time sprint; 12–16 weeks if alongside work/studies. Week 1's golden dataset is 3 days of manual research and must not be compressed.
- **Hardware:** Local 8GB RTX 3070 Ti; Llama-3-8B via 4-bit quantization for fast/clean extraction; Groq/Gemini for complex reasoning.
- **Architecture:** LangGraph state machine, subgraphs, checkpointing, async scraping; MCP server; FastAPI backend; Streamlit dashboard.
- **Infrastructure:** Docker Compose local; production as Web Service + Managed PostgreSQL + Managed Redis on Render/Railway.
- **Data:** Financials are private/fragmented; contacts go stale; registries vary by state/county; dedup is non-trivial.
- **Compliance:** Only approved sources; no prohibited scraping; no bypassing access controls.
- **Human review:** Required for HITL state transitions and rejection feedback into evals.

### Client Assumptions — To Be Confirmed

Because this project originated from a build-first engagement, most client questions do not have answers upfront. The following defaults are **explicit assumptions**, not final truth, and are configurable:

| Question | v1 Default Assumption |
|---|---|
| Arkansas scope | Statewide; second market pack after Tampa Bay pilot works |
| Tampa scope | Tampa Bay = Hillsborough + Pinellas + Pasco |
| Scoring weights | Financial fit 30, management 20, ownership 15, contactability 15, market/strategic 10, data confidence 10 — configurable |
| "Existing management team" evidence | Named principal/owner + at least one of: website team page, LinkedIn, registry officer listing |
| CRM target | CSV/JSON export now; HubSpot deferred |
| Verification audit | Random 20% weekly sample; critical-field error >5% fails audit |
| Approved sources | Company websites, public registries, public directories, state business filings; no paywalled/logged-in/private sources |
| Contact verification | One authoritative source + one corroborating source, or two independent sources |
| Weekly report deadline | Friday CSV + dashboard + Markdown market summary |
| "Verified" definition | Source-backed and/or human-audited — not audited financial truth |

Discovery note: GovFiles.dev is used as a **personal-project exception** behind a
config-gated adapter while its ToS, endpoint, and coverage are unconfirmed (ADR-028). It
is not yet an approved client source. Headcount and founding year in the draft v2 labels
are context only; they are not qualification gates (ADR-033).

---

## 11. Key Risks

| Risk | Mitigation |
|---|---|
| Weak golden dataset | Build 20-company `golden_v1.json` first; treat as contract. |
| LLM hallucinated contacts/financials | Pydantic schema, source URLs, confidence, human audit, `llm-guard`. |
| Duplicate companies | `rapidfuzz` entity resolution + manual review. |
| Stale contacts | Verification status, weekly refresh, audit samples. |
| Source blocking/rate limits | `tenacity` retries, caching, DLQ, approved-source policy. |
| Cost overrun | Redis cache-aside, model routing, cost-per-prospect metric. |
| Prompt injection | `llm-guard`, approved sources, guard nodes, memory write policy. |
| GPU limits | Local 8B for extraction; API models for reasoning/scale. |
| Overclaiming | Gate resume/demo claims to actual eval results in `DECISIONS.md`. |

---

## 12. Open Questions (Deferred, Non-Blocking)

These do not block the v1 build. Defaults above apply until the client confirms:

1. Is "Arkansas" statewide, or Jonesboro/NEA?
2. What exact scoring weights does the client prefer?
3. What counts as sufficient "existing management team" evidence?
4. Which CRM is the eventual target, and what fields are mandatory before HubSpot?
5. What sample size and failure threshold define the weekly verification audit?
6. Which public registries and directories are explicitly approved/prohibited for each county?
7. Should contact verification require two independent sources or one authoritative + one corroborating?
8. What is the weekly deadline and delivery format for the pipeline report?

**Rule:** When the client answers, update this section and log the resolution in `DECISIONS.md`. No code changes should be required for answers that only touch config.

---

## 13. One-Line Summary

> **AcquiScout is a source-backed, human-reviewed, multi-agent M&A research pipeline that turns fragmented public data into 20+ verified, scored, CRM-ready title and property management acquisition prospects per week in the Tampa Bay area — with explicit confidence, auditability, and cost control.**
