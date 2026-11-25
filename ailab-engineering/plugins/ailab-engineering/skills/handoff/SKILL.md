---
name: handoff
description: Write a SHORT handoff (max ~120 lines) for continuing this project in a new session or another agent - verified state, what changed, open decisions, the single next action and pointers to evidence. Use at the end of a session or before /clear.
argument-hint: "[audience: claude | codex | owner]"
disable-model-invocation: true
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/skill-context.sh *)
---

# Handoff

Audience: $ARGUMENTS

## Facts gathered now
!`${CLAUDE_PLUGIN_ROOT}/scripts/skill-context.sh handoff`

## Write the handoff

Save it as a NEW file `docs/project/handoffs/<UTC date>-<topic>.md` (create the
directory if needed) and show it. Hard limit: about 120 lines. History lives in
git and evidence files; link to them instead of retelling them.

Use exactly these sections:

```markdown
# <Project> — handoff <date>
Repo: <path>  Branch: <branch>  HEAD: <short sha>  Working tree: <clean | N changed>

## State (each line tagged)
- [VERIFIED <how, when>] ...
- [DOCUMENTED <file>] ...
- [HISTORICAL <source>] ...
- [UNKNOWN] ...

## Changed in this session
- ... (commit sha or file list)

## Accepted vs pending
| Item | Implemented | Tested | Reviewed | Owner-accepted | Merged | Deployed |

## Open decisions for the owner
1. ...

## Next single action
<one concrete step, with the exact command or skill to start it>

## Do not
- (at most 7 project-specific prohibitions that are easy to violate)

## Evidence
- <paths to reports, logs, gates>
```

Rules: never mark something VERIFIED unless it was checked in this session;
implemented/tested/accepted/merged/deployed are different states; no secrets,
tokens, passwords or personal data; English for every audience, including the
owner; Russian only when the owner explicitly asks for it.
