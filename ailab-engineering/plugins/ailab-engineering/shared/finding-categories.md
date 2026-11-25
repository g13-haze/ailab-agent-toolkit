# Review finding categories

Every finding of `spec-reviewer`, `reviewer` and `security-reviewer` carries exactly
one category from this list, written `[P1][<category>]`. The review skill records
category counts in the WP Log round line (`findings: <category>×<n>, ...`);
`intel.sh metrics` counts only the names below, and the knowledge review uses the
counts to see what recurs. Categories describe findings; they are never targets.

## Spec axis (spec-reviewer)
- `spec-missing` — a requirement or acceptance criterion is not implemented, or only partly.
- `spec-wrong` — implemented, but differently from what the WP asked.
- `unrequested-behaviour` — behaviour nobody asked for (flags, options, endpoints, side effects), even inside scope files.
- `out-of-scope-file` — a changed file outside the WP scope, unrelated reformatting, or a stray artifact.
- `criteria-unverifiable` — (plan mode) a criterion that no command or observation can verify.
- `memory-conflict` — contradicts an accepted decision in CURRENT_STATE, an ADR or ARCHITECTURE.md.

## Engineering axis (reviewer)
- `correctness` — logic error, wrong default, off-by-one, broken invariant.
- `error-handling` — unhandled failure path, swallowed error, a guard that hides the root cause.
- `data-semantics` — missing treated as zero, wrong denominator, double counting, timezone.
- `regression` — breaks existing behaviour or a dependent the change did not consider.
- `test-weak` — a test that would pass without the change (tautology, passes if the code returns nothing, asserts on mocks only).
- `test-missing` — behaviour or a failure path without a test where an honest test is practical.
- `overengineering` — abstraction, configurability or generality the task does not need.
- `duplication` — re-implements an existing helper or copies logic instead of reusing it.
- `dead-code` — code the change made unused, or added without a caller.
- `boundary` — crosses a documented architecture boundary.
- `performance` — measurable cost on a hot path, or unbounded work.

## Security axis (security-reviewer)
- `security` — any security finding; name the kind in the title (authz, injection, ssrf, secrets, session, webhook, migration).

## Process
- `process` — a skipped gate, approval or host gate, or work that belongs in another WP.
