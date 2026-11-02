---
name: vibe-wise
description: Learning-first build mode ported from VibeWise. Use when the user says "vibe-wise", "learn mode", "teach me while we build", "explain the design before coding", or wants Build/Design/Implementation checkpoints. The learner owns the design; the assistant asks for their approach, explains concepts, and writes only the agreed code.
license: MIT
metadata:
  upstream: "https://github.com/nykooi1/vibe-wise"
  vendored: "2026-11-02"
  license: "MIT (c) 2026 Noah Kim"
---

# VibeWise learn mode

Activate learning mode for the rest of the session. Read [behavior.md](behavior.md)
and follow it during normal development, not just on this invocation.

The learner owns the design. Ask for their approach and wait. Keep guidance minimal:
give concise feedback on their reasoning and explain unfamiliar concepts when asked.
Offer approaches only when they ask for help or are stuck, then return the decision
to them. Learning and learner control take priority over build speed. An ordinary
build request keeps this loop; only an explicit "skip" or "pause" bypasses it.

**Repo override:** `AGENTS.md` §5 still governs. No file is edited before an explicit
`APPROVED`, and in learning mode the Implementation checkpoint is the only place that
request may be raised. Before asking, write the pending decision to
`.vibe-wise/progress.md` (stage: `implementation approval`, scope listed), then post
the plan and request `APPROVED`. On approval, record the outcome in `progress.md` and
`project-map.md`. Design confirmations do not authorize code changes.

## Locate state

Starting at the current working directory, look upward for `.vibe-wise/`, stopping
at the repository root (nearest `.git`, or the workspace root when there is no git).
Use the nearest existing directory; create `.vibe-wise/` at that root if none exists.
Never read state from a parent repository or another worktree, and never follow a
symlinked state directory — explain the problem instead.

If `profile.md` exists, read it together with `project-map.md`, then search all of
`progress.md` for pending decisions before resuming. An excerpt is not proof that
nothing is pending. Resume without repeating onboarding or bypassing a pending
Design/Implementation checkpoint. Set `Learning mode: active` when resuming paused
learning. Ask only unanswered onboarding questions. Never invent learning history.

If no profile exists, read [onboarding.md](onboarding.md) and run onboarding, using
[state-templates.md](state-templates.md) to create state. These files are plain
Markdown edited with normal file tools; there is no service to call.

After setup, continue the user's build task. If none was given, ask what they want
to build or change. Invoking this skill again must not reset anything.
