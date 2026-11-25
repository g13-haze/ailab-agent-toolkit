---
name: change-intel
description: Owner catch-up and change intelligence - what changed since the last project-memory checkpoint or in a range, why it matters, what is risky or unknown, and which code deserves the owner's attention, from git history and the code itself with every claim tied to evidence. Drafts project-memory updates for approval; writes nothing on its own.
argument-hint: "catch-up [since <ref>] | pr <base>..<head> | history <path>"
disable-model-invocation: true
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/skill-context.sh *) Bash(${CLAUDE_PLUGIN_ROOT}/scripts/intel.sh history *) Bash(${CLAUDE_PLUGIN_ROOT}/scripts/intel.sh impact *)
---

# Change intelligence

Request: $ARGUMENTS

## Facts gathered now
!`${CLAUDE_PLUGIN_ROOT}/scripts/skill-context.sh change-intel`

## Modes
- **catch-up** (default): what happened since the `Checkpoint:` in project memory
  (`intel.sh history --since checkpoint`), else since the ref given, else the last 30
  commits. For an owner returning after time away.
- **pr `<base>..<head>`**: guided reading of a range (a branch, Codex output, an
  external PR): what it does, in reading order, with an attention map.
- **history `<path>`**: how one area evolved: changes, reworks, reverts, the WPs and
  decisions behind them. For planning and debugging.

## Method
1. **Deterministic facts first:** `${CLAUDE_PLUGIN_ROOT}/scripts/intel.sh history ...`
   for the window or paths, `git log --stat` / `git show --stat <sha>` for the commits
   that matter, `${CLAUDE_PLUGIN_ROOT}/scripts/intel.sh impact --base <base>` for a
   range. These are facts; your synthesis is not.
2. **Choose the lowest depth that answers the question** and say which you used:
   1 quick scan (subjects, stats, history summary) · 2 focused (read the diffs of the
   important commits, their callers and tests) · 3 deep (contract or cross-module
   changes; at most two Explore subagents for independent areas). Stop as soon as the
   question is answered; list what you did not examine.
3. **Tag every statement:** `FACT` (commit sha or `file:line`), `RISK` (why, with
   evidence), `UNKNOWN` (what would settle it). Implemented, tested, reviewed,
   owner-accepted, merged and deployed are different states; git shows at most the
   first three.
4. **Check project memory against git:** commits after the checkpoint that
   CURRENT_STATE does not mention; WP files with `Status: ACCEPTED` that it does not
   mention; claims it makes that the repository cannot confirm (mark UNKNOWN, do not
   correct them yourself).

## Report (at most 60 lines)
```
CHANGE INTELLIGENCE — <mode>, <window or range>, depth <n> — HEAD <sha>
Summary: <3-5 lines for the owner>
What changed (grouped by feature or area): FACT lines with shas
Risks and open questions: RISK / UNKNOWN lines
Code worth your attention (at most 7, most important first):
- High|Medium — <file:line> — <why to read it: contract, data semantics, security, transaction boundary> — <what to check> — comprehension | possible defect
Project memory: in sync | drift: <commits / WPs / claims>
Not examined: <commits, areas or depth skipped>
```
A comprehension priority is not a defect; label which each item is.

## Afterwards
- If project memory drifted, draft a CURRENT_STATE checkpoint (at most 15 lines,
  ending with `Checkpoint: <sha of the last commit you examined>`). Never move the
  checkpoint past commits you did not examine. Write it only after the owner
  explicitly approves, following the same rules as `/ailab-engineering:wp-close`.
- Save the report only if the owner asks, as a NEW file in the evidence location.
- Never create a separate memory store, a generated docs folder, or summaries
  inside the repository.
