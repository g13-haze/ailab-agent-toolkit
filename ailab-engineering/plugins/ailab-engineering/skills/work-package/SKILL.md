---
name: work-package
description: Start a bounded A.I. Lab work package - writes .claude/wp/<ID>.md with goal, scope, forbidden operations, risk tier, type, size, acceptance criteria, plan with checks, test ladder and rollback, marks it active, gathers proportionate context, then plans. Use at the start of any implementation task.
argument-hint: <WP-ID> <goal in one sentence>
disable-model-invocation: true
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/skill-context.sh *) Bash(${CLAUDE_PLUGIN_ROOT}/scripts/scratch.sh *) Bash(${CLAUDE_PLUGIN_ROOT}/scripts/intel.sh impact *) Bash(${CLAUDE_PLUGIN_ROOT}/scripts/intel.sh history *) Bash(${CLAUDE_PLUGIN_ROOT}/scripts/intel.sh signals *)
---

# Start work package `$0`

Full request: $ARGUMENTS

## Current repository state and engineering rules
!`${CLAUDE_PLUGIN_ROOT}/scripts/skill-context.sh wp-start`

The engineering behaviour rules above apply to every step of this work package.

## Steps

1. **Read project memory first.** Read the top checkpoint of
   `docs/project/CURRENT_STATE.md` and `docs/project/INDEX.md` if they exist.
   If the request contradicts an accepted decision there, stop and say so.

2. **Classify: tier, type, size.** Pick the LOWEST tier that honestly fits:

   | Tier | When | Required gates |
   |---|---|---|
   | T1 | Docs, UI polish, local refactor with full test coverage, no data/infra effect | targeted tests; owner looks at the diff |
   | T2 | Normal feature or bug fix inside one product | targeted + regression tests; `reviewer` subagent; one real-data or real-browser smoke if the feature touches data or UI |
   | T3 | Auth, sessions, tenancy, billing, migrations, production deploy, deleting data, external network to third-party sites, anything irreversible | written plan approved by owner BEFORE implementation; `reviewer` + `security-reviewer`; host gate (owner-run commands via `/ailab-engineering:host-gate`); explicit owner acceptance |

   Do not inflate the tier. Extra ceremony on T1/T2 work is a cost, not safety.
   From 0.2.0 the review gate for T2/T3 is the review loop (step 9): `spec-reviewer`
   and `reviewer` together, plus `security-reviewer` for T3.

   **Type** selects the test protocol: `feature` | `bugfix` | `refactor` | `infra` | `docs`.
   **Size** selects how the work runs: `S` one area, about 5 steps or fewer, single
   pass · `M` 6-10 steps or two areas, work in slices · `L` more than ~10 steps or
   independent subsystems. **Split rule:** for L, or work that cannot be verified
   step by step, propose separate WPs first; the owner decides. An L that stays one
   WP works in slices with a handoff at a slice boundary when context runs high.

3. **Write `.claude/wp/$0.md`** using exactly this template, with `<ID>` = `$0`
   (keep it under ~100 lines; goal, scope and criteria must stay in the first 30
   lines, which are re-injected after compaction):

!`${CLAUDE_PLUGIN_ROOT}/scripts/skill-context.sh wp-template`

4. **Activate it:** write the id to `.claude/wp/ACTIVE` (single line: `$0`).
   Ensure `.claude/wp/ACTIVE` is listed in `.gitignore` (add the line only if
   missing; do not stage anything).

5. **Scratch:** create the task scratch directory with
   `${CLAUDE_PLUGIN_ROOT}/scripts/scratch.sh new wp-$0` and use it for every
   temporary artifact of this work package.

6. **Intake, proportionate to the tier** (record facts, not essays; list unknowns
   instead of researching them):
   - **T1:** none.
   - **T2:** `${CLAUDE_PLUGIN_ROOT}/scripts/intel.sh impact --depth 2 <scope paths>` and
     `${CLAUDE_PLUGIN_ROOT}/scripts/intel.sh history <scope paths>`. Write at most 10
     lines into `## Context`: dependents and hubs, tests reached, rework signals,
     coverage gaps the tool reported. Dependents that must change go into Scope;
     reached tests that the ladder misses go into `regression:`.
   - **T3:** the same, plus entry points with `path:line`, helpers to reuse, and every
     contradiction between code and project memory (at most 20 lines). Use the built-in
     Explore subagent for an unfamiliar area; read directly when the area is small.
   - If the tool reports UNAVAILABLE, write `impact: unknown (tool unavailable)`; never
     guess dependents. Impact is a lower bound: dynamic wiring is not visible to it.

7. **Plan.** Fill `## Plan`: each step `N. <step> → verify: <check>`; contracts and
   data shapes first, the riskiest step early, the tree working after every step.
   Read and apply only the protocol for this type:
   `${CLAUDE_PLUGIN_ROOT}/shared/protocols/<feature|bugfix|refactor|infra-docs>.md`.
   - **T3:** run the `spec-reviewer` subagent once in plan mode (WP id, "mode: plan"),
     add its questions to `## Assumptions / open questions`, then STOP and ask the
     owner to approve the plan. Do not edit code.
   - **T1/T2:** proceed unless something is ambiguous enough that a wrong guess
     would be expensive; then ask one question.

8. **Suggest a goal condition** the owner can paste to run the WP to completion,
   for example:
   `/goal every acceptance criterion in .claude/wp/$0.md is shown met by command output in this conversation, the targeted and regression tests pass, git status shows changes only inside the WP scope; stop and report when the review loop stops without PASS (round limit or owner decision), or stop after 25 turns`

9. **Implementation and checkpoints.** Follow the plan; run each step's check before
   the next step. After each step or slice update the `Progress:` line and append one
   Log line (`- <date> step/slice n/m done: <check result>`); a bug fix logs the
   failing reproduction before the fix. Size M/L: finish and verify one slice before
   starting the next. When context runs high on an L WP, finish the current slice and
   suggest `/ailab-engineering:handoff`.

10. **Verification (when implementation is done).** Each check runs once, on a
    snapshot the agents record (`${CLAUDE_PLUGIN_ROOT}/scripts/skill-context.sh snapshot`):
    - T1: targeted tests, `${CLAUDE_PLUGIN_ROOT}/scripts/intel.sh signals --base <Repo HEAD at start> --type <type>`,
      and the ladder's `fbpa:` / `pinned:` line yourself (`intel.sh fbpa`, Log the
      verdict; BLOCKED goes to the owner, never run the test unsandboxed instead);
      then the owner looks at the diff. No subagents.
    - T2/T3: follow the `review` skill (`/ailab-engineering:review $0`): spec-reviewer
      and reviewer in parallel fresh contexts (security-reviewer for T3), triage, fixes
      only for in-scope P0/P1, at most 2 rounds for T2 and 3 for T3, then `qa-runner`
      once on the final snapshot — do not produce full-ladder or acceptance-build
      evidence yourself before the review fixes are in. Create the evidence directory
      first so their reports land there.
    - Do not change repository files (other than this WP file) while any reviewer or
      QA runs.
    - After each hand-back append one Log line (agent, verdict, snapshot, report
      path); open a report only for the finding you are fixing.
    - Report readiness to the owner in at most 15 lines (status, snapshot,
      acceptance n/m, verdicts with report paths, review rounds n/N, decision needed);
      the detail stays in the evidence files, and `/ailab-engineering:wp-close` reuses it.
