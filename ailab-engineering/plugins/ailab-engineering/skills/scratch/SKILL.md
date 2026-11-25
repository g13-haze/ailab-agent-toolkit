---
name: scratch
description: How to create, keep and clean temporary task files (fixtures, DB copies, build outputs, downloads, test databases, exports) on A.I. Lab hosts. Use whenever a task needs temporary files or when cleaning up after a task.
user-invocable: false
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/scratch.sh *)
---

# Task scratch (producer-owned cleanup)

Temporary artifacts of a task must never be scattered in `/tmp`, the repo, or
evidence directories. Use the plugin helper so every artifact is attributable
to a session and a label, measurable, and removable by exact path:

```bash
S="$(${CLAUDE_PLUGIN_ROOT}/scripts/scratch.sh new wp-<id>)"   # prints the directory
cp /path/to/source.sqlite3 "$S/copy.sqlite3"                   # work inside it
${CLAUDE_PLUGIN_ROOT}/scripts/scratch.sh list                    # size, age, status
${CLAUDE_PLUGIN_ROOT}/scripts/scratch.sh keep wp-<id> "owner review pending"
${CLAUDE_PLUGIN_ROOT}/scripts/scratch.sh clean wp-<id>           # delete + receipt
${CLAUDE_PLUGIN_ROOT}/scripts/scratch.sh clean-path --missing-ok "$S/run-a" "$S/run-b"   # exact paths inside it
```

Prefer `clean-path` over complex recursive Bash deletion (loops, `$(...)`,
variables the guard cannot resolve). When a hook answers
`SCRATCH_CLEANUP_REDIRECT`, resolve the exact absolute path and retry through
`clean-path` without asking the owner. Never use scratch cleanup for project
files, production, backups or evidence.

Rules:
- One label per purpose (`wp-<id>`, `qa-<id>`, `fixture-<name>`). Labels: `[A-Za-z0-9._-]`.
- Results that must survive (reports, accepted outputs) are copied OUT of scratch
  to the evidence location as new files before cleaning.
- Clean as soon as the work package no longer needs the data. If you keep it,
  say why with `keep`; the Stop hook reminds you about unattended scratch once.
- Never delete scratch of other sessions or agents, and never delete parent
  directories such as `/tmp/claude-*` or `/tmp` itself.
- Use exact paths. No wildcards in `rm -rf`, no `docker prune`.
- The session id comes from `$AILAB_SESSION_ID`, set by the plugin's SessionStart
  hook. If it is empty, scratch goes under `manual/`; mention this to the owner.
