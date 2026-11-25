# AGENTS.md — <PROJECT NAME>

Shared instructions for every coding agent in this repository (Claude Code,
Codex). Keep this file short; details live in `docs/project/` and `.claude/rules/`.

## Product
<One paragraph: what this product is, who uses it, what "working" means for the user.>

## Sources of truth (in this order)
1. Actual git state and runtime on the host (verify, don't assume).
2. `docs/project/CURRENT_STATE.md` — top checkpoint is authoritative.
3. `docs/project/INDEX.md` — map of contracts, ADRs, evidence.
4. Owner-accepted reports and host-gate evidence.
Handoffs and chat history are context, not proof.

## Status vocabulary
Write states precisely; they are different things:
`implemented` · `tested` · `reviewed` · `owner-accepted` · `merged` · `deployed` · `used`.
Tag claims as VERIFIED (checked now, say how), DOCUMENTED, HISTORICAL, or UNKNOWN.

## How work is done
- One bounded work package at a time (`.claude/wp/<ID>.md`): goal, scope,
  forbidden operations, acceptance criteria, test ladder, rollback.
- Risk tier decides the gates: T1 tests only; T2 + independent review + one
  real-data/browser smoke; T3 (auth, tenancy, billing, migrations, production,
  deletion, external crawling) + approved plan, security review, host gate,
  owner acceptance.
- Never treat "code exists" or "tests pass" as permission to merge or deploy.
- Every work package has a type (feature, bugfix, refactor, infra, docs) and a size
  (S, M, L). A bug fix starts with a test that fails on the bug; a refactor keeps
  existing assertions unchanged; an L-size task is split or worked in slices.

<!-- ailab:engineering-behaviour:start -->
## Engineering behaviour
- Think before coding: state assumptions and open questions first. When two readings of the task differ in cost or risk, name both and ask instead of choosing silently.
- Simplicity first: write the least code that meets the acceptance criteria. No unrequested features, options, abstractions, or handling for cases that cannot happen.
- Surgical changes: change only what the task needs and match the existing style. Remove only what your own change made unused; report unrelated problems instead of fixing them.
- Goal-driven execution: give every step a check that can fail (`step → verify: <command or observation>`) and run it before the next step. A bug fix starts with a test that reproduces the bug.
<!-- ailab:engineering-behaviour:end -->

## Review and iteration limits
- Spec compliance and engineering quality are reviewed separately, each by a
  reviewer that has not seen the implementation conversation; security as a third
  review for T3 and security-sensitive paths.
- At most 2 review rounds for T2 and 3 for T3. Only in-scope P0/P1 findings are fixed
  without asking. Stop and ask the owner when a fix needs a scope, product or
  architecture decision or a file outside scope, when the same finding comes back or
  the same test fails twice, or when the round limit is reached.

## Never (without explicit owner approval in the current session)
- Mass staging (`git add -A`, `git add .`), `git clean`, `reset --hard`, force push.
- Push, open, merge or close pull requests, create releases or issues, publish
  packages or images (`gh pr create`, `npm publish`, `docker push`, ...).
- Deploy, restart services, run migrations, touch production data or config.
- Delete or edit backups, frozen evidence, or files you did not create.
- Global cleanup (`docker * prune`, wildcard `rm -rf`, `chown -R`).
- Print secrets (`.env`, tokens, `env`/`printenv`, `docker inspect` without `--format`).

## Temporary files
All temporary artifacts go into a labelled task scratch directory and are
cleaned (with a receipt) when the work package closes. Results that must
survive are copied to the evidence location first.

## Code intelligence
Before changing a shared module, check what it reaches: the A.I. Lab `intel` tool
(`intel.sh impact`, `history`, `signals`, `arch-check`) gives file-level dependents,
tests reached, history and boundary checks, with stated coverage limits. Its result
is a lower bound, never proof that nothing else is affected.

## Commands
- Tests (targeted): `<command>`
- Tests (full): `<command>`
- Lint / types: `<command>`
- Build: `<command>`
- Run locally: `<command>`

## Language
All engineering instructions, prompts, documentation, code comments, reports, handoffs, and owner-facing communication MUST be in English by default.
Russian is permitted ONLY when explicitly requested by the owner for a specific output.
