---
name: review
description: Bounded A.I. Lab review loop: spec-reviewer and reviewer in parallel fresh contexts (security-reviewer for T3), triage, in-scope P0/P1 fixes, at most 2 rounds for T2 or 3 for T3, then qa-runner once. Use when a T2/T3 work package implementation is done or the owner asks for a review.
argument-hint: "[WP-ID | <base>..<head> [requirements file]]"
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/skill-context.sh *) Bash(${CLAUDE_PLUGIN_ROOT}/scripts/intel.sh signals *) Bash(${CLAUDE_PLUGIN_ROOT}/scripts/intel.sh impact *) Bash(${CLAUDE_PLUGIN_ROOT}/scripts/intel.sh arch-check *) Bash(${CLAUDE_PLUGIN_ROOT}/scripts/scratch.sh *)
---

# Review loop

Requested: $ARGUMENTS

## Current state (snapshot, work package, rounds so far)
!`${CLAUDE_PLUGIN_ROOT}/scripts/skill-context.sh review`

Policy (rounds, triage groups, stop conditions, Log line format):
`${CLAUDE_PLUGIN_ROOT}/shared/review-policy.md` — read it once before round 1.
Finding categories: `${CLAUDE_PLUGIN_ROOT}/shared/finding-categories.md`.

## What is reviewed
- **Work package** (default: the active WP, or the WP id given): requirements are
  the WP goal, scope and acceptance criteria; the base is `Repo HEAD at start`; the
  limit is 2 rounds for T2 and 3 for T3. A T1 WP is not reviewed here unless the
  owner asks.
- **Range** (`<base>..<head>`, e.g. Codex output or a branch): the owner names the
  requirements (a file, an issue text, or the PR description). `<head>` must be the
  checked-out HEAD, because the tools read the work tree: check it out first, or review
  in a `git worktree add` copy in scratch. Without requirements,
  run only `reviewer` and report `SPEC: not reviewed (no requirements given)`.
  A range review is review-only: report findings, change nothing unless the owner asks.
  Limit 2 rounds.

## Steps

1. **Prepare once per snapshot.** Create the evidence directory named in the WP (or
   use `${CLAUDE_PLUGIN_ROOT}/scripts/scratch.sh new review-<id>`); it must be outside the
   repository or git-ignored. Save each output as a NEW file there and pass the paths
   to the reviewers; read only the summary lines yourself:
   - `${CLAUDE_PLUGIN_ROOT}/scripts/skill-context.sh change-patch <base>` → `change-<snapshot>.patch`
     (the reviewed state; round 2+ reviewers compare it with the previous round's patch)
   - `${CLAUDE_PLUGIN_ROOT}/scripts/intel.sh signals --base <base> --type <type>` → `signals-<snapshot>.txt`
   - `${CLAUDE_PLUGIN_ROOT}/scripts/intel.sh impact --base <base>` → `impact-<snapshot>.txt`
   - `${CLAUDE_PLUGIN_ROOT}/scripts/intel.sh arch-check --changed --base <base>` → `arch-<snapshot>.txt`
     (only when the repository has an `ailab-boundaries` block)

2. **Round n: launch the reviewers in parallel**, in one message, each in its own
   fresh context. Give each the same facts and nothing more: WP id or range, round
   `n/N`, base, snapshot id, the tool-output paths, and for rounds 2+ "review only the
   changes between change-<previous>.patch and change-<current>.patch; your previous
   report is <path>".
   - `spec-reviewer` (mode: diff) — the SPEC axis.
   - `reviewer` — the ENGINEERING axis.
   - `security-reviewer` — when the tier is T3, or signals report security-sensitive
     paths, or the change touches auth, sessions, tenancy, billing, webhooks,
     user-supplied URLs or migrations.
   Never paste one reviewer's findings into another reviewer's prompt.

3. **Triage** the hand-backs into DECIDE / FIX NOW / RECORD / REJECT as the policy
   says (merge duplicates first). Append the round Log line from the policy to the WP.

4. **Stop or continue** by the policy's stop conditions, checked in order. When the
   loop continues: fix only the FIX NOW group, following the engineering behaviour
   rules and the type's protocol, run the targeted tests for what changed, take the
   new snapshot with `${CLAUDE_PLUGIN_ROOT}/scripts/skill-context.sh snapshot` (never
   another hash), and start round n+1 with a delta review.
   When it stops without PASS: report the condition and the open findings, and ask
   the owner one question per decision. Do not start another round on your own.

5. **QA once.** When the loop ends in PASS (or PASS WITH NOTES), run `qa-runner` once
   on the final snapshot: the full ladder, plus the `fbpa:` (bug fix) or `pinned:`
   (refactor) line when the ladder has one. A QA failure that needs a code fix makes
   the review evidence stale: the fix starts the next round if one is left;
   otherwise it is an owner decision. qa-runner re-runs the full ladder at most once
   after a fix; a further full run needs owner approval.

6. **Readiness report** (at most 15 lines):
   ```
   REVIEW LOOP: PASS | STOPPED (<condition>) — rounds <n>/<N> — snapshot <id>
   SPEC: <verdict> — <report path>
   ENGINEERING: <verdict> — <report path>
   SECURITY: <verdict> — <report path> | not required
   QA: <verdict> — <report path> | not run (<why>)
   Fixed: <n> (<categories>) · Recorded: <n> · Rejected: <n>
   Open decisions: none | <one line each>
   Next: /ailab-engineering:wp-close | <owner question>
   ```

Never stage, commit, push, open or merge pull requests, publish, deploy or run
migrations as part of this loop; those stay owner actions.
