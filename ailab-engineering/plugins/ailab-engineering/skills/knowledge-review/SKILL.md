---
name: knowledge-review
description: Owner-run review of pending knowledge candidates (KL-2) - checks each candidate's evidence and recurrence, proposes validate / promote / project / merge / reject with the smallest target, drafts register entries and target changes, and writes them only after the owner approves each one.
argument-hint: "[candidate id ...]"
disable-model-invocation: true
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/skill-context.sh *) Bash(${CLAUDE_PLUGIN_ROOT}/scripts/knowledge.sh *) Bash(${CLAUDE_PLUGIN_ROOT}/scripts/intel.sh metrics *)
---

# Knowledge review (KL-2)

Requested: $ARGUMENTS

## Pending candidates and register
!`${CLAUDE_PLUGIN_ROOT}/scripts/skill-context.sh knowledge`

The pipeline is unchanged: WP Close → Learning Review → Candidate Register (inbox) →
**this review** → Owner Approval → reusable target. This skill prepares decisions;
the owner makes them. Inbox records are append-only: never edit, move or delete one.

## Steps
1. **Read** each pending record (or only the ids requested) from the inbox directory
   printed above.
2. **Verify the evidence.** Each Evidence path or commit must still exist
   (`git cat-file -e <sha>`, `test -e <path>`) and must show what the record claims;
   open it. Missing or unsupporting evidence → propose `rejected` with the reason.
3. **Check recurrence.** `${CLAUDE_PLUGIN_ROOT}/scripts/knowledge.sh find <3-6 keywords>`
   for related candidates and entries; `${CLAUDE_PLUGIN_ROOT}/scripts/intel.sh metrics <repos the owner names>`
   for finding categories across WPs. `validated` needs a second project, a read-only
   check in another repository, or a lesson about this plugin itself. One occurrence
   stays `candidate` (no decision) or becomes `project`.
4. **Pick the smallest target** that would have prevented the problem, in this order,
   and check the target does not already cover it: a guard rule (forbidden operation)
   → an `intel.sh signals` rule (deterministic, advisory) → a reviewer or
   spec-reviewer checklist line → the project's `.claude/review-notes.md` or
   `.claude/rules/` → a skill step → a line in the `AGENTS.md` template → a new skill
   (only for a repeatable multi-step procedure).
5. **Decision table**, one row per candidate:
   `id | lesson | evidence ok? | recurrence | proposed status | target | draft change (at most 3 lines)`.
   Duplicates → `merged` with the surviving id in Related.
6. **Ask the owner** to approve, change or reject each row. Only for approved rows:
   - add the register entry (format in the register's header) to
     `plugins/ailab-engineering/knowledge/REGISTER.md` of the ailab-engineering
     source repository, never to an installed plugin copy;
   - draft the target change; a change to plugin behaviour ships with a version bump,
     a CHANGELOG line and a contract test (repository AGENTS.md rules);
   - a project-scoped target (a product repository's review notes) is a separate T1
     work package in that repository.
   If this session is not in the ailab-engineering repository, show the drafts and
   stop.

Never promote from a single occurrence unless the lesson is about this plugin or the
owner says so; never change the register, a skill, an agent, a rule or an
instruction file without that row's explicit approval.
