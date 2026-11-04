# Research Notes — Golden Set

Caveats and unresolved threads that don't fit the `GoldenRecord` schema but matter for audit.

## golden-006 — PHP Management Services LLC (dba Condominium Associates)

**Yellow flag: Property Management Holdings Sub Corp.** The top of the entity chain is `Property Management Holdings Sub Corp` (member of Esplanade Equity, LLC, which is the member of PHP Management Services LLC). This entity does not appear in Florida Sunbiz results and may be a Delaware or out-of-state holding vehicle.

**Evidence for holding co (not PE):**
- Same four individuals are officers of both PHP Management Services and Esplanade Equity: Andy Cook (VP), Manjola Kavaric (Treasurer), Brandon Pinderhughes (Secretary), Antonio J. Busquets (VP)
- Manjola Kavaric appears on condominiumassociates.com/about as Executive Vice President of Finance — operator continuity confirms operators still run the business
- Both entities share operational address 570 Carillon Parkway, Suite #210
- Company site: "locally owned and operated since 1982"

**Verification still needed (not blocking for qualified, but should be checked before any acquisition consideration):**
- Search opencorporates.com or Delaware Division of Corporations for `Property Management Holdings Sub Corp` — check incorporation date and registered agent. A very recent incorporation date would shift the assessment toward PE; a founding-era date would confirm founder holding.

**Status:** qualified as of 2026-11-04, PMHS caveat noted.

## golden-019 — Milestone Title Services, LLC (dba Milestone Title Services)

**Yellow flag: revenue is a headcount benchmark, not published volume.** No source states annual closings or annual transaction volume. The website only says "Our experienced staff has handled thousands of closings" — cumulative, not annual. The revenue/EBITDA bands are derived from roughly 30 named team members × $120K–$192K per employee (headcount benchmark) with a 15–25% EBITDA margin, marked `low` confidence.

**Employee count conflicts:** LinkedIn says 14, SignalHire says 10–50, and the team page lists ~30 named staff. The 30-person basis is the most generous reading; a 14-person count would put revenue below the gate.

**Offices:** Tampa, Riverview, Apollo Beach (Hillsborough); Largo (Pinellas); Spring Hill (Hernando); Jacksonville (Duval). One Florida license (`W201143`) under Anthony Fowkes; no acquisition announcement and no parent/roll-up language found, so G5 passes as a single multi-branch firm.

**Verification still needed before any acquisition consideration:**
- Quantify annual closings or annual transaction volume (Hillsborough Clerk settlement-agent search, FLOFR production data, or TBBJ/ALTA listings).
- Reconcile the employee count (14 vs 30 vs 10–50).

**Status:** qualified as of 2026-11-04, headcount caveat noted.

## golden-007 — Hillsborough Title, Inc. (semi_qualified at 57)

**Ambiguous ownership — FAN network relationship, not confirmed roll-up.**

Sources conflict:
- `hillsboroughtitle.com/about-us/` states: "Hillsborough Title is owned and operated by Aaron M. Davis" and describes FAN as "a conglomerate of independent title agencies" and "a strategic alliance amongst members and vendors."
- `sourceoftitle.com/article.aspx?uniq=11637` states: "Hillsborough Title is the first member of the Florida Agency Network's statewide network" and describes FAN as "a strategic alliance of members."
- A 2020 Respanews article (truncated URL, not fetchable) states "FAN consists of two wholly owned brands — Hillsborough Title, Inc. and Total Title Solutions, LLC."

**Resolution:** The company's own current About page (primary source) says Davis owns it and FAN is a member network. The 2020 Respanews claim is not fetchable and contradicts the primary source. The record is `semi_qualified` because of the missing team page (G3), NOT because of parent-company reasons. If future evidence establishes FAN ownership, this becomes a rejected record per ADR-025 (independence gate).

## golden-018 — Leland Management, Inc. (rejected at 54)

**Out of geographic scope per ADR-024 (observation-county convention).**

- Legal entity: LELAND MANAGEMENT, INC., Orange County HQ (6972 Lake Gloria Blvd, Orlando)
- Tampa presence: regional branch at 12630 Race Track Rd — not a local independent
- `primary_county = hillsborough` (observation location) with `also_serves_counties = orange`
- `score_market = 0` reflects the geographic mismatch
- Recorded as rejected to teach the pipeline that statewide firms with a Tampa branch are not the target profile
