---
name: ailab-work-package
description: Start or continue a bounded A.I. Lab work package (.claude/wp/<ID>.md) - tier, type, size, acceptance criteria, plan with checks, test ladder, rollback - and implement it under the A.I. Lab engineering rules. Use when the owner asks Codex to implement a work package or a non-trivial change in an A.I. Lab repository.
---

# A.I. Lab work package (Codex adapter)

First read `@AILAB_OS@/codex/platform-mapping.md`: guard hooks are not active in
Codex, so the AGENTS.md "Never" list is yours to keep.

1. **Facts and rules:** run `@AILAB_OS@/plugins/ailab-engineering/scripts/skill-context.sh wp-start`.
   It prints the repository state, existing WPs, checkpoint drift and the engineering
   behaviour rules; apply the rules to every step.
2. **Procedure:** follow steps 1-9 of
   `@AILAB_OS@/plugins/ailab-engineering/skills/work-package/SKILL.md` with the
   mapping. The WP template comes from `skill-context.sh wp-template` (replace `<ID>`);
   the test protocol for the WP type from
   `@AILAB_OS@/plugins/ailab-engineering/shared/protocols/<feature|bugfix|refactor|infra-docs>.md`.
   - Intake: run the same `intel.sh impact` and `intel.sh history` commands yourself.
   - T3: write the plan and the open questions, then STOP for the owner's approval
     (no plan-mode reviewer in Codex; the owner may run `ailab-review` axis spec on
     the plan in a fresh session).
3. **Verification:** T1 — targeted tests and `intel.sh signals --type <type>`, then the
   owner reads the diff. T2/T3 — do not certify your own work: the review axes run in
   fresh sessions (`ailab-review`, one axis each: spec, engineering, and security for
   T3) or in Claude Code (`/ailab-engineering:review <ID>`). Limits and stop conditions:
   `@AILAB_OS@/plugins/ailab-engineering/shared/review-policy.md` (2 rounds for T2,
   3 for T3). Then run the test ladder once yourself, including the `fbpa:` or
   `pinned:` line through `intel.sh fbpa` (BLOCKED goes to the owner; never run the
   test unsandboxed instead).
4. Report readiness in at most 15 lines; close with the `ailab-wp-close` skill.
