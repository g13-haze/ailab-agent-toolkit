---
name: ailab-review
description: One axis (spec, engineering or security) of an A.I. Lab review of a work package or git range, written as an evidence report that wp-close accepts. Use for cross-model review of Claude Code work, for reviewing Codex output in a fresh session, or when the owner asks Codex to review an A.I. Lab change.
---

# A.I. Lab review — one axis per session (Codex adapter)

Mapping: `@AILAB_OS@/codex/platform-mapping.md`.

**Independence comes from a fresh context.** Review exactly one axis in this session
and never an implementation you wrote in this session. If the owner did not name the
axis, ask: `spec` | `engineering` | `security`.

1. Read the axis instructions in full and follow them with the mapping:
   - spec → `@AILAB_OS@/plugins/ailab-engineering/agents/spec-reviewer.md`
   - engineering → `@AILAB_OS@/plugins/ailab-engineering/agents/reviewer.md`
   - security → `@AILAB_OS@/plugins/ailab-engineering/agents/security-reviewer.md`
2. Inputs: the WP id (or the range and the requirements the owner names), the round
   `n/N`, and for rounds 2+ the previous report and the previous and current
   `change-<snapshot>.patch` files (`skill-context.sh change-patch <base>`): review only
   what changed between them. Record the snapshot with
   `@AILAB_OS@/plugins/ailab-engineering/scripts/skill-context.sh snapshot`. Run
   `intel.sh impact`, `signals` and `arch-check` yourself when the instructions use
   their output.
3. Read-only: never edit repository files, stage, commit, push or comment on pull
   requests. Probes and targeted tests run in a scratch copy
   (`scratch.sh new review-codex-<id>`); the full suite is not yours to run.
4. Write the report as the instructions say, as a NEW file whose name starts with
   `codex-` (for example `codex-review-<snapshot>-<UTC>.md`) in the WP evidence
   directory, and end the first hand-back line with ` — model: codex`.
5. Show the hand-back (at most 15 lines). Triage and fixes belong to the session that
   owns the implementation, under the review policy
   (`@AILAB_OS@/plugins/ailab-engineering/shared/review-policy.md`).
