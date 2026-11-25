# Review policy (shared by Claude Code and Codex)

## Independence
- Two axes, reviewed separately and never merged or re-ranked against each other:
  **SPEC** (does the change do what the work package asked, and nothing else?) and
  **ENGINEERING** (is it correct, tested, safe and no more complex than needed?).
- Each axis is reviewed in a fresh context that has not seen the implementation
  conversation or the other axis's findings. **SECURITY** is a third axis for T3
  work and for changes to security-sensitive paths.
- Reviewers are read-only. Only the main session (one writer) changes files.
- The evidence directory is outside the repository or git-ignored: report files inside
  the work tree would change the snapshot the reviewers are checking.

## Rounds and limits
- Round 1 is the first review of the implementation. Each re-review after fixes is
  another round and covers only the changes since the reviewed snapshot.
- Rounds so far = the larger of the WP Log `review round` lines and the number of
  engineering review reports (`review-*.md`) in the evidence directory, so a refused
  Log write or a new session cannot reset the count.
- Limit: **2 rounds for T2, 3 rounds for T3** (range reviews without a WP: 2 unless the
  owner says otherwise). The limit is a ceiling; one round is the normal case.
- Closing a STALE or MISSING evidence gap in wp-close is verification, not a round.
  If it finds a P0/P1 that must be fixed, the fix starts the next round; when no
  round is left, it is an owner decision.

## Triage (main session, once per round)
Merge duplicates across reports, then put each finding in exactly one group:
- **DECIDE** — needs a scope, product or architecture decision; a fix that would
  touch a file outside the WP scope; a change the implementation already made to an
  existing file outside the scope (`out-of-scope-file`: the owner chooses between
  widening the scope and reverting it — do not revert it yourself); or contradicts
  accepted project memory → stop and ask the owner.
- **FIX NOW** — P0/P1 findings inside scope, whatever the axis (`spec-missing`,
  `spec-wrong` and `unrequested-behaviour` at P1 included: implement or remove), and new
  untracked artifacts this WP created (dumps, copies, outputs: delete them).
- **RECORD** — every P2, including P2 `unrequested-behaviour`, and judgement calls;
  listed in the WP Log as documented.
- **REJECT** — false positive or contradicts project standards; one-line reason.
Never apply every suggestion blindly; fixes follow the engineering behaviour rules.

## Stop conditions (check after every round, in this order)
1. No FIX NOW and no DECIDE → the loop is done.
2. Any DECIDE → stop and ask the owner (one question per decision).
3. A finding fixed in an earlier round is back, or the same test failed twice after
   fixes → stop: the fixes do not converge.
4. A fix made in round 2 or later introduced a new P0/P1 → stop and report.
5. The round limit is reached with FIX NOW open → stop: `round limit reached`; the
   owner decides whether to allow one more round, defer, or accept.
Otherwise fix the FIX NOW group only, run the targeted tests for what changed, and
start the next round on the new snapshot.

## Records
One WP Log line per round, written by the main session:
`- <date> review round <n>/<N>: SPEC <verdict> / ENGINEERING <verdict>[ / SECURITY <verdict>] — findings: <category>×<k>, ... | none — fix now <a>, decide <b>, record <c>, rejected <d> — snapshot <id> — <report paths>`
Append `— round limit reached` or `— stopped: <condition>` when the loop stops early.
Use only the category names in `finding-categories.md`, as the reviewers tagged them.
The snapshot is always the one `skill-context.sh snapshot` prints; never compute it
another way. If a write to the WP file is refused (Claude Code treats `.claude/` as
sensitive), put the Log line in the readiness report for the owner; never work around
the refusal.

## Never part of a review loop
Staging, committing, pushing, opening or merging pull requests, publishing,
deploying, migrations, or changing files outside the WP scope. These stay owner
actions exactly as before.
