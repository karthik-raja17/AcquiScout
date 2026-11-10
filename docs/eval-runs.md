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

When the pipeline produces its first eval run, add a new entry below with:

- Run date, commit SHA, judge model + version, prompt hash
- Metrics from EVALUATION.md §2 (each with actual value vs threshold)
- Cost per qualified prospect
- Pass/fail on blocking metrics

Do not edit existing entries. Append new ones.
