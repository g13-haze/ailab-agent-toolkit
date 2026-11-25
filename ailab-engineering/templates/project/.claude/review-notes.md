# Review notes — <PROJECT NAME>

Recurring, project-specific pitfalls the `reviewer` subagent must check. Add a
line whenever a review or an incident finds something that could happen again.
Keep it under ~40 lines; delete entries that no longer apply.

## Data and analytics
- Missing observations are not zero (GSC rows, inspection results, provider responses).
- Selected/sampled populations are never reported as site-wide rates.
- Every percentage states its denominator.

## This repository
- <example: frontend in apps/web is bind-mounted into the live dogfood container — edit only in an isolated checkout>
- <example: the scheduler must not materialize duplicates after retries — check idempotency keys>
