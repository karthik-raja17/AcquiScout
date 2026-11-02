# Learning mode (VibeWise)

This repo vendors [VibeWise](https://github.com/nykooi1/vibe-wise) (MIT) as a
project skill so the build is also a learning exercise. It runs in opencode, not
Claude Code.

## Start

Say **vibe-wise** (or "learn mode", "teach me while we build"). The assistant then:

1. Finds or creates `.vibe-wise/` at the repo root.
2. Runs onboarding once (project, experience, preferences) if `profile.md` is missing.
3. Builds with you through three checkpoints:

| Checkpoint | What happens | What it authorizes |
|---|---|---|
| **Build** | You explain how you would approach the problem. | Nothing — reasoning only. |
| **Design** | You review the summarized design and tradeoffs. | Nothing — records the design. |
| **Implementation** | You review the exact code changes proposed. | Code changes — the recorded technical gatekeeper. |

Say "skip" for a single step, "pause learning" to stop the loop, or run **vibe-wise**
again to resume. Notes persist across sessions and survive compaction.

The Implementation checkpoint is the technical gatekeeper: before it asks, it writes
the pending decision (stage: implementation approval, with scope) to
`.vibe-wise/progress.md`. **`APPROVED` is the token**, not a second gate — it
authorizes exactly the scope that checkpoint recorded, and the outcome is written back
to `progress.md` and `project-map.md`.

## State

```
.vibe-wise/profile.md       preferences + experience (what you know)
.vibe-wise/progress.md      per-topic learning evidence + pending decisions
.vibe-wise/project-map.md   components, flow, trust boundaries, unknowns
```

`.vibe-wise/` is gitignored, so notes stay local. To start over, delete it — the next
run re-onboards. Nothing in `src/`, `tests/`, or the docs is touched by reset.

## Relationship to repo rules

`AGENTS.md` §5 still governs: plan first, explicit `APPROVED`, verify with pytest and
ruff, summarize with `file:line`. VibeWise adds the reasoning steps **before** that
gate; it does not loosen the gate or the no-fabrication rules.

## Attribution

Skill files adapted from VibeWise © 2026 Noah Kim, MIT. Full text in
`.opencode/skills/vibe-wise/LICENSE`.
