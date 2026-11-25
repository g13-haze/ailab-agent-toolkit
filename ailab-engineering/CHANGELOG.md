# Changelog

## 0.2.2 — 2026-11-25 (Fewer repeated approvals: task-scoped owner authorization, no docs false positives)
- guard-bash: git command names in shell comments and in the quoted arguments of read-only text
  tools (`grep`, `egrep`, `fgrep`, `rg`, `echo`, `printf`, `git log/shortlog`) are data, not
  commands. Every other quoted string stays visible as in 0.2.1, and so does everything when the
  command names a shell, interpreter, xargs, `env` or `exec`, runs `./...`, dot-sources, or uses
  `<<<`, `<(...)`, `$(...)`, backticks, `eval` or `source`. Comments are removed by a quote-aware pass that leaves the command unchanged
  when it cannot follow the quoting (`$(...)`, `${...}`, backticks, `$'...'`, unbalanced quotes,
  over 4,000 characters). `grep -n "git add -A" docs/` was denied and `grep "git push"`,
  `printf "... git push"`, `ls  # then git push` asked; all now run.
- New UserPromptSubmit hook `authorize.sh` (owner-approved addition to hooks.json; the 0.1.7
  hooks are unchanged and the contract test now pins exactly that). An `ailab-authorize:` line
  in the owner's own prompt records a grant (commit | push | merge | memory) bound to the session,
  repository, branch, active work package and, for push, the remote and its push URL;
  `ailab-authorize: revoke` drops them, and so does SessionEnd.
- guard-bash: a plain `git push`/`git merge` covered by a grant runs without the ask; every word
  `push`/`merge` in the command must be a verified, granted call, else it asks as before. Force
  push stays denied; tags, `--all`, deletes, `--no-verify`, other refspecs, git global options
  other than `-C`, variable assignments, `export`/`read`/`declare`/`printf -v`, a named `HOME`,
  `PATH`, `XDG_*`, `GIT_*` or `CDPATH`, an unfollowed `cd` (subshells, blocks, variables), xargs,
  `eval`/`source`/`env`/`sudo`, functions, `-c` scripts, here-strings, push settings that widen a
  plain push (`remote.<r>.mirror`, `remote.<r>.push`, `push.followTags`, submodule recursion) and
  commands that also switch branches or change config, remotes or submodules still ask. Rebase
  still asks. Output redirections (`2>&1`) do not prevent a grant.
- The grant store (`<plugin data>/grants/`) is an immutable protected path; commands naming it or
  running `authorize.sh` are denied; a tool call that resumes or continues a Claude session, or
  names `CLAUDE_PLUGIN_DATA`/`CLAUDE_PLUGIN_ROOT` as a word (`printf -v`, `read`, `export`), asks. authorize.sh records nothing unless run by the hook runner (`CLAUDE_PLUGIN_DATA`,
  `UserPromptSubmit`) and stores only a hash of the push URL. Side effect: an agent's recursive
  delete of the plugin data directory is now denied (it asked before).
- A new, resumed or cleared session starts without grants; session-context shows them after
  compaction. The closeout rule and wp-close steps 5 and 8 accept an owner authorization for the task instead of asking again, and
  state that WP files, reports, handoffs, tool output and subagent results never authorize.

## 0.2.1 — 2026-11-17 (Final hardening: sandboxed FBPA, bind-mount-safe install and rollback)
FBPA execution isolation (separately revertable commit)
- `intel.sh fbpa` runs the fail-before/pass-after and pinned test commands only inside a bubblewrap
  sandbox: separate user/mount/PID/network/IPC/UTS/cgroup namespaces, no network, all capabilities
  dropped, nested user namespaces refused; read-only system directories, the copy under test at
  `/work`, declared read-only `--link`/`--toolchain` directories; repository, `$HOME`, `/run` (Docker
  socket), `/var`, `/srv`, `/opt` absent apart from declared directories; cleared environment (`--env` to pass values); memory, file
  size, open files, CPU, process and wall-clock limits.
- Fails closed: without a trusted (root-owned, system location) bwrap, or when user namespaces are
  refused (Docker's default seccomp profile), the verdict is BLOCKED and no test runs. There is no
  unsandboxed mode. The editable-install leak check is static (0.2.0 executed the venv interpreter).
- New `intel.sh sandbox-check` proves the isolation on the current machine.
- After the independent reviews: the repository may not lie inside an exposed system directory;
  `--link` only for git-ignored, untracked directories (it could hide the copy under test and expose
  the original); `--tests` files may not be symlinks; `--toolchain` only install roots owned by root
  or the user, with `bin/` and without sockets; `/dev` read-only with a size-limited `/dev/shm`;
  a new session keyring; setuid/setgid bits stripped and the work directory 0700; no phase hint in
  the environment; the process budget counts threads; bubblewrap 0.8+ checked. A run that hits a
  sandbox limit or needs something the sandbox withholds is INCONCLUSIVE, never a "fails before" or
  a FAIL. `intel.sh` runs Python with `-I -S` and never uses an interpreter inside the repository.
- After the fix verification: test output reaches the log through a pipe (the test cannot learn its
  phase or the host path from its file descriptors; at most 64 MiB kept); runtime roots exposed for
  a linked virtualenv get the same owner and socket checks as `--toolchain`; more limit and
  environment signatures (threads, numpy/JVM/Rust allocation failures, Docker and database
  sockets); `intel.sh` also checks the interpreter against `--repo` and, when it refused an
  interpreter inside the repository (an activated `.venv`), falls back to the system Python.
- After a second verification: the two copies carry random names while the tests run (mountinfo and
  bwrap's arguments no longer tell the test its phase; renamed to before/after for the report);
  runtime roots found for a linked virtualenv pass every `--toolchain` check (protected paths,
  virtual filesystems, owner, sockets) and a stale one is BLOCKED instead of a crash; intel.py
  accepts no abbreviated options and intel.sh checks `--repo` in every form, also for a repository
  of another owner; Node/V8 and .NET out-of-memory messages count as limits.
- Tests that need the sandbox are skipped and reported NOT VERIFIED only where `sandbox-check` says
  BLOCKED (a sandbox that runs but fails a probe fails the suite), e.g. agent-long
  under Docker's default seccomp, so the installer works there and warns; `run-all.sh` also runs
  the intel suite with user namespaces refused. The intel tests write no bytecode into the checkout.
- wp-close, work-package, the Codex adapter and the bugfix/refactor protocols: a BLOCKED FBPA or
  PINNED check goes to the owner and is never replaced by an unsandboxed run.
Install and rollback (example host)
- `docs/INSTALL-HOST.md` rewritten. The 0.2.0 guide replaced the checkout with `mv`, which leaves a
  container that bind-mounts it on the old directory. The new procedure discovers the real
  configuration (`docker inspect`, `/proc/self/mountinfo`, inodes, a manifest diff), updates the
  mounted directory in place (`rsync --delete --checksum --exclude=/.git`), verifies every
  environment, and rolls back to 0.1.7 the same way. It also no longer uses `claude plugin uninstall`
  or `marketplace remove`, which delete the plugin data directory (guard.log, receipts).
- New `install/verify-install.sh`: source version, contents against a release manifest (with
  executable bits), the folder and version Claude Code really runs (`readFromFolder`,
  `folderVersion`), the marketplace registration; PASS / FAIL / NOT VERIFIED. Self-contained, runs
  through `docker exec -i … bash -s` and on a 0.1.7 tree.
- `install.sh --expect-version x.y.z` refuses to change anything when the tree is another version
  (a stale mount) and verifies after installing; it refuses to change a marketplace registered
  from another directory.
- New `tests/run-upgrade-tests.sh` (in `run-all.sh --with-install`): end-to-end mv / in-place /
  rollback with the real claude CLI against a read-only kernel bind mount, using the guide's own
  mountinfo line.
- After the independent reviews the guide also: tests the staged release (`install.sh --dry-run`)
  on the host and in the container before it goes live; detects a mount of a parent directory;
  guards every sync against an unset `$EGM`, re-checks the release copy right before it, lists all
  deletions, and chains each check to the command that depends on it; never nests directories on a
  re-run; uses the compose service name and restores a `~/.claude` backup after a recreate; keeps
  `TS` across shells; verifies the case-B rollback; runs the installer dry run before
  `uninstall --keep-data` on an older Claude Code.

## 0.2.0 — 2026-11-17 (Engineering upgrade: WP-A core + WP-B advanced skills)
Security (separately revertable commit)
- guard-bash 1b: `gh` is classified by an allowlist of read-only subcommands; every other `gh` call
  (pr create/merge/close/review/comment, issue/release/repo/workflow/run/secret mutations, aliases,
  extensions) asks the owner; `gh api` asks for non-GET methods, `-f/-F/--input` (implied POST) and
  GraphQL mutations; `gh auth token` / `status --show-token` are denied. Package, image and deploy
  publishing asks: npm/pnpm/yarn/bun, docker/podman push and `buildx --push`, twine/uv/poetry/hatch/
  flit/pdm, cargo/gem/nuget/maven/gradle, helm/kubectl/terraform/pulumi mutations, hosting deploy CLIs,
  `aws s3` writes, scp/rsync to a remote host. `--dry-run` stays allowed. Ordinary commands keep their cost.
- `templates/user/settings.deny.json` gains `permissions.ask` rules for the riskiest publishing commands
  (Claude Code documents that ask rules prompt even in bypassPermissions mode; not verified here);
  `install.sh --with-deny-rules` merges deny and ask.
Code and change intelligence
- `scripts/intel.sh` / `intel.py` (Python 3.9+ stdlib, `python -I`, read-only; hooks stay bash-only):
  `impact` (static, best-effort import resolution: AST for Python, patterns for JS/TS; dependents, hubs, tests reached, deleted-but-imported
  files, coverage and limits), `history` (churn, rework signals, co-changes, WP ids, commits since the
  CURRENT_STATE checkpoint), `signals` (suppressions, TODOs, skips, edited assertions, placeholders,
  manifests, large files, security-sensitive paths), `arch-check` (`ailab-boundaries` in ARCHITECTURE.md),
  `fbpa` (fail-before/pass-after in copies; `--mode refactor` for pass/pass pins), `metrics`.
Workflow
- work-package v2: engineering behaviour rules; Type and Size beside the unchanged tier; split rule;
  intake proportionate to tier; plan steps with checks; Progress line and slice checkpoints; T3 plan
  reviewed by spec-reviewer before the unchanged owner-approval stop. Template in `shared/wp-template.md`.
- review skill (new): spec-reviewer and reviewer in parallel fresh contexts (security-reviewer for T3 or
  sensitive paths), triage, in-scope P0/P1 fixes only, at most 2 rounds for T2 and 3 for T3, explicit
  stop conditions, qa-runner once. Policy in `shared/review-policy.md`; categories in
  `shared/finding-categories.md`.
- Agents: spec-reviewer (new, SPEC axis, diff and plan modes); reviewer refocused on ENGINEERING
  (regressions in dependents, test quality, capped mutation probe, design-quality judgement calls);
  categories and maxTurns on all review agents; security-reviewer report file and hand-back;
  qa-runner runs fbpa/pinned ladder stages and only proposes impact-reached tests.
- Test protocols per type: feature, bugfix (reproduce first, FBPA), refactor (characterization pins,
  unchanged assertions), infra-docs.
- wp-close: spec-review / FBPA / PINNED / codex- review evidence; WPs reviewed under 0.1.x keep 0.1.x
  gates; gap closure is not a round; `Checkpoint: <sha>` in the memory draft. Owner approvals unchanged.
- SessionStart: one line only when commits exist after the CURRENT_STATE checkpoint.
Advanced skills
- codeintel (model-invocable), change-intel (owner-invoked catch-up / range reading / area history),
  knowledge-review (owner-invoked KL-2; the pipeline WP Close -> Learning Review -> Candidate Register
  -> Owner Approval -> Reusable Skill is unchanged), ARCHITECTURE.md template
  (`init-project.sh --with-architecture`).
Codex
- `codex/`: ailab-work-package, ailab-review, ailab-wp-close, ailab-codeintel, ailab-change-intel adapters
  over the same files, a platform mapping (guards are not active in Codex) and an installer.
Context cost
- Always-on skill and agent descriptions 1,295 -> 2,075 chars (about +195 tokens); hooks.json unchanged;
  SessionStart output unchanged except the conditional checkpoint line.
Hygiene and tests
- `.bak` files removed from the tree (kept in git tag baseline-0.1.7); fail-closed comment corrected;
  two pre-existing shellcheck false positives annotated. README rewritten in English for 0.2.0.
- Tests: hook suite 489 -> 816 per parser; new intel (54), contract, Codex (with live codex-cli discovery)
  and installer suites; `tests/run-all.sh` runs everything.
Independent review
- Two fresh-context reviews (security; spec and engineering) and real review-loop runs found guard
  bypasses (line continuations, `--help` in values, options before subcommands, nested commands ...), an
  over-broad pre-approval of `intel.sh fbpa`, false fbpa results under editable installs, a drift line
  that never cleared, and policy ambiguities; all fixed with regression tests that fail on the earlier
  code. Open owner decision: Claude Code treats `.claude/` (WP files) as sensitive.
Sources (ideas re-expressed, no text copied; see CREDITS.md): global_agents (behaviour rules),
ai-agents-skills-main (bounded review loop, debugging steps), ai-skills-agent-loop (two-axis review),
project-spec (edge cases as criteria), skills-main (change intelligence, TDD and refactoring practices; MIT).

## 0.1.7 — 2026-11-14 (Knowledge Capture V1, KL-1)
- `scripts/knowledge.sh find|add|pending`: one shared, append-only inbox of evidence-backed lesson
  candidates in `<agent-logs>/_knowledge/inbox/`, where `<agent-logs>` is a tree the guard configuration
  declares `appendonly` (`/work/agent-logs` in agent containers, else `/srv/agent-logs` on the host).
  `AILAB_KNOWLEDGE_DIR` can only select another declared tree; any other location, a symlinked or
  not append-only destination is refused without falling back, and nothing is written. `add` reads a
  `Key: value` record from stdin, sets id, status `candidate` and source (project / WP / HEAD / date),
  requires every Evidence reference to be an existing path or commit outside task scratch and every
  Related id to exist, refuses a second candidate for the same project and WP, and never overwrites,
  edits or deletes a record. `find` ranks related candidates and register entries by keyword hits
  (top 8); `pending` lists candidates without a register entry.
- `knowledge/REGISTER.md`: the register format and statuses (candidate, validated, promoted, project,
  merged, rejected), no entries. Agents only read it; it changes only in an owner-approved review.
  No entry limit and no automatic expiry or rejection.
- wp-close Learning Review: files at most one candidate per WP (default none) after a duplicate
  search, names the smallest target without editing it, and reports the candidate id in the
  closeout line. No owner approval for filing; skills, agents, rules, review notes, project memory
  and global instructions still change only with explicit owner approval.
- No always-on context change: SessionStart output and skill descriptions are unchanged, and no
  skill, agent or hook is added. Review skill, promotion and Codex distribution are deferred (KL-2, KL-3).
- Tests: knowledge capture cases (filing, refusals, one per WP, concurrent filing, duplicate search,
  register precedence, append-only guards, heredoc allowed by guard-bash, wp-close contract) and inbox
  location cases (path overrides, traversal, symlinks, protected destinations, declared classes).

## 0.1.6 — 2026-10-25
- `scratch.sh clean-path [--missing-ok] <abs-path>...`: validates and deletes exact paths below this
  session's scratch (plugin scratch or Claude Code scratchpad) without registering subdirectories.
  Refuses the scratch/session roots, other sessions, paths outside the session base, protected
  paths, symlinks leading out, symlinked parents, mount points, `.`/`..`/globs, a non-private or
  repository-contained base, and an AILAB_SESSION_ID that differs from CLAUDE_CODE_SESSION_ID. All
  paths are checked first; each target is renamed from its pinned parent into `<base>/.trash/<random>`,
  re-checked (same inode, no mount) and removed with `rm -rf --one-file-system`; receipts for
  deletions and refusals.
- guard-bash: an unresolved recursive delete whose visible part (or every earlier binding of its
  leading variable) points into this session's scratch gets a recoverable `SCRATCH_CLEANUP_REDIRECT`
  denial naming `clean-path` instead of an owner prompt. Never allows; other unresolved deletes,
  protected and dangerous paths keep their decisions. Overriding AILAB_*/CLAUDE_CODE_SESSION_ID/
  CLAUDE_PLUGIN_* in a command asks the owner.
- Scratch skill, SessionStart line and project CLAUDE.md template: prefer `clean-path`; retry on the
  redirect without asking the owner.
- wp-close: three WP Log effectiveness lines (review findings/fixed/documented, post-acceptance
  rework within 21 days starting `Pending`, cost as peak context and active time with source) and a
  `Metrics:` line in the final report; never estimated, `Not measured`/`Unknown` without evidence.
- Tests: 14 own-scratch unresolved deletes move from ask to redirect; new redirect, clean-path,
  identity, race (validation vs. removal) and wp-close contract cases.

## 0.1.5 — 2026-10-24
- guard-bash: `W=$(mktemp -d "<template>")` (also quoted, `|| exit` allowed) resolves when it is the
  variable's only binding, `-d` is the only option, and the template (literal or from resolved
  variables) ends in `XXX` inside this session's own scratch (plugin scratch or Claude Code
  scratchpad). Only `"${W:?}"` resolves for such a variable, so a failed mktemp aborts the shell.
  Templates elsewhere (/tmp, other sessions, shared or protected areas, `..`, `$1`, `-u`/`-p`) stay
  unresolved and keep asking.
- guard-bash: a closed top-level `{ ...; }` group (e.g. `[[ -e $OUT ]] || { echo EXISTS; exit 2; }`)
  no longer stops variable resolution for later statements; assignments inside the group stay
  untrusted, a `}` inside quotes does not close it, and if/for/while/case still stop resolution.
- Tests: 20 new cases (5 allow, 14 ask, 1 deny), including the Example Project SSR-2 closeout command
  that asked under 0.1.3.

## 0.1.4 — 2026-10-24 (Lite)
- `skill-context.sh snapshot`: one-line content id of the work tree (tracked and untracked,
  not ignored). Only operational records are excluded: `.claude/wp/`, `.claude/review-notes.md`,
  `docs/project/CURRENT_STATE.md`, `docs/project/INDEX.md`, `docs/project/handoffs/`; specs and
  contracts under `docs/project/` count. Read-only; staging or committing the same content keeps
  the id. Shown at the top of `wp-close` context.
- Reviewer / QA split: `reviewer` finds defects (contracts, adversarial probes in its own
  `review-<wp>` scratch, targeted tests) and no longer runs the full suite or determinism /
  acceptance builds; `qa-runner` runs the ladder once on the final snapshot and marks a run
  INVALID when the snapshot changes during it. Both (and `security-reviewer`) record the snapshot.
- Compact reporting: reviewer and QA write the full report as a new file in the WP evidence
  directory (or their scratch) and hand back at most 15 lines; `work-package` step 8 adds the
  verification order, one Log line per hand-back and a ≤15-line readiness report.
- `wp-close`: classifies reviewer / QA / security evidence against the current snapshot
  (REUSE / FAILED / STALE / MISSING / INCONSISTENT) and re-runs nothing that matches; gaps get
  the smallest verification, and full-suite or real-data runs need owner approval first.
  Project Memory and Learning Review approval rules are unchanged.
- Tests: snapshot invariants, guard allow for the snapshot call, agent/skill contract checks.

## 0.1.3 — 2026-10-23
- guard-bash: `rm`/`unlink`/`shred` operands that use a variable assigned a literal path once,
  unconditionally, earlier in the same command are resolved and checked like the literal path
  (`SC=/tmp/ailab-scratch/<session>/x; W=$SC/ws; rm -rf -- "${W:?}"`). Unprovable variables
  (`$(...)`, conditional or repeated assignment, loops, `read`, `export`, `eval`/`source`,
  inherited environment) keep asking; a nonempty-looking `${W:?}` alone grants nothing.
- guard-bash: this session's Claude Code scratchpad (`/tmp/claude-<uid>/<project>/<session>/scratchpad/...`)
  counts as the session's own scratch.
- guard-bash: deleting a git repository inside a shared, owner-declared scratch area asks.
- guard-bash: commands after `then`/`do`/`else`/`elif`/`if`/`while`/`until`/`!` are checked (previously
  `for f in a; do rm -rf /srv/backups; done` passed unchecked).
- Guard config (`~/.config/ailab-engineering/protected-paths.conf`) is owner-only: agent changes ask.
- Append-only deny messages tell the agent to ask the owner to declare a working copy as scratch
  instead of retrying.
- Tests: 43 new cases (allow, deny and ask) for the rules above.

## 0.1.2 — 2026-10-23
- Smart Closeout: SessionStart context tells Claude to end a meaningful completed task with a
  short Engineering Closeout (Project Memory, Learning Review, Handoff, Owner action); proposals
  only, writes need explicit owner approval. No closeout after questions, read-only work,
  intermediate steps or trivial edits.
- `wp-close`: CURRENT_STATE/INDEX updates are drafted and written only after owner approval;
  new Handoff step; final report ends with the same Engineering Closeout block.
- Project memory reads are targeted (relevant sections plus context), not whole-file by default.
- `handoff`: English for every audience; Russian only on explicit owner request.
- Tests: session-context output within the 10,000-char hook limit; closeout rule inside the
  2,000-char preview.

## 0.1.1 — 2026-10-23

- Fail-closed behavior when no JSON parser is available.
- Regression tests for guard-bash and guard-paths.
- Learning Review added to wp-close.
- No changes to production applications.

## 0.1.0 — 2026-10-20
- Guard hooks: `guard-bash` (git/docker/systemd/rm/secrets/SQL/redirect rules) and
  `guard-paths` (immutable / appendonly / production / scratch classes).
- SessionStart context (repo state, active WP, CURRENT_STATE top) and `AILAB_*` env export.
- Task scratch lifecycle: `scratch.sh`, one-time Stop reminder, SessionEnd leftovers log, receipts.
- Subagents: `reviewer`, `security-reviewer`, `qa-runner`.
- Skills: `work-package`, `wp-close`, `host-gate`, `handoff`, `scratch`.
- Templates: AGENTS.md, CLAUDE.md, path-scoped rules, review notes, user deny rules.
- Installers: `install/install.sh`, `install/init-project.sh`. Tests: 191 cases across jq/node/python3.
- Hardened after independent review: `.git` deletion, xargs/cd/cp/mv/rsync paths into evidence,
  symlinked protected prefixes, shared scratch root, `rm -rf /*` and repo parents, combined force-push
  flags, `.env` via grep/awk/sed; no false positives on commit messages, echo, heredocs,
  `restore --staged`, `stash list`, in-tree `rm -rf build/*` and `find -delete`; `onFailure: block`.
