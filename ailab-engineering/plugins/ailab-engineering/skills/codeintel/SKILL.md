---
name: codeintel
description: Deterministic code intelligence for A.I. Lab repositories: files and tests a change reaches, area history and rework, diff risk signals, boundary checks, with stated coverage limits. Use before changing a shared module, or when planning or debugging in an unfamiliar area.
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/intel.sh impact *) Bash(${CLAUDE_PLUGIN_ROOT}/scripts/intel.sh history *) Bash(${CLAUDE_PLUGIN_ROOT}/scripts/intel.sh signals *) Bash(${CLAUDE_PLUGIN_ROOT}/scripts/intel.sh arch-check *)
---

# Code intelligence

Tool: `${CLAUDE_PLUGIN_ROOT}/scripts/intel.sh <command> [--json]` — read-only, writes
nothing into the repository, never runs repository code (except `fbpa`, which runs the
test command you give it in copies inside a bubblewrap sandbox, and reports BLOCKED
when no sandbox works here). If it prints UNAVAILABLE, use git and grep and say impact
is unknown.

| Question | Use |
|---|---|
| Which files and tests does this change (or these paths) reach? | `intel.sh impact [--base <ref>] [paths]` (`--tests-only` for a test list) |
| Who calls this exact function or symbol? | an LSP plugin (pyright, typescript) if installed, else `grep -rn` — impact is file-level |
| What happened in this area; was it reworked? | `intel.sh history <paths>` (`--since checkpoint` for project memory) |
| Is this diff risky (suppressions, edited assertions, manifests, sensitive paths)? | `intel.sh signals [--base <ref>] [--type <type>]` |
| Does it cross a documented boundary? | `intel.sh arch-check [--changed --base <ref>]` (needs an `ailab-boundaries` block in ARCHITECTURE.md) |
| Does a bug-fix test really reproduce the bug? | `intel.sh fbpa` (normally run by qa-runner from the WP ladder) |
| Open-ended "how does X work"? | the built-in Explore subagent, or targeted reads |

Reading the output:
- Impact is a **lower bound**. Read the COVERAGE and UNRESOLVED lines: unresolved and
  dynamic imports, parse errors and unanalyzed languages are places the graph cannot
  see. Check them with grep before saying "nothing else is affected".
- A HUB (many direct dependents) or `BREAKAGE LIKELY` (a deleted file still imported)
  deserves a targeted read and a test from the dependent side.
- Signals are reasons to look, not defects.

Report what you used in at most 10 lines with `file:line` references. Do not paste
whole tool outputs into the conversation, and do not write generated context files,
indexes or summaries into the repository.
