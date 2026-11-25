<!-- Work package template v2, shared by the Claude Code work-package skill and the Codex ailab-work-package skill. <ID> is the WP id. -->
```markdown
# <ID> — <short title>
Status: PLANNED | IN PROGRESS | REVIEW | ACCEPTED | ABANDONED
Tier: T1 | T2 | T3   Type: feature | bugfix | refactor | infra | docs   Size: S | M | L
Created: <date>   Repo HEAD at start: <sha>
Progress: <step or slice n/m> — next: <one concrete action>

## Goal and user value
<1-3 sentences: what changes for the user/owner and how we will know>

## Scope (files/dirs that may change)
- ...
## Out of scope
- ...
## Forbidden in this WP
- no production deploy / migration / data deletion unless listed above
- no mass git staging; stage explicit files only
- no push, pull request, release or package publishing without the owner's word
- <product-specific prohibitions>

## Acceptance criteria (each must be verifiable by a command or observation)
1. ...
(known edge cases and traps are criteria too)

## Test ladder (exact commands)
- targeted: ...
- regression: ...
- static/build: ...
- real-data / browser smoke (T2+ when relevant): ...
- fbpa (bugfix) | pinned (refactor): base <sha>, tests <files>, command <targeted command>

## Rollback
<how to undo: revert commit X / feature flag / restore backup Y>

## Evidence
<where results, logs and reports will be written: outside the repository or git-ignored; scratch label: wp-<ID>>

## Plan (each step ends with its check; M/L grouped into slices)
1. <step> → verify: <command or observation>

## Context (T2/T3 intake, at most 20 lines)
## Assumptions / open questions
## Log
- <date> created
```
