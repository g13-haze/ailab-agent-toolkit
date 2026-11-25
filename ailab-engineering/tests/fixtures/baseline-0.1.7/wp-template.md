# $0 — <short title>
Status: PLANNED | IN PROGRESS | REVIEW | ACCEPTED | ABANDONED
Tier: T1 | T2 | T3   Created: <date>   Repo HEAD at start: <sha>

## Goal and user value
<1-3 sentences: what changes for the user/owner and how we will know>

## Scope (files/dirs that may change)
- ...
## Out of scope
- ...
## Forbidden in this WP
- no production deploy / migration / data deletion unless listed above
- no mass git staging; stage explicit files only
- <product-specific prohibitions>

## Acceptance criteria (each must be verifiable by a command or observation)
1. ...
## Test ladder (exact commands)
- targeted: ...
- regression: ...
- static/build: ...
- real-data / browser smoke (T2+ when relevant): ...

## Rollback
<how to undo: revert commit X / feature flag / restore backup Y>

## Evidence
<where results, logs and reports will be written; scratch label: wp-$0>

## Log
- <date> created
