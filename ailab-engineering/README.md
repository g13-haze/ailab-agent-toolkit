# ailab-engineering

Engineering harness for A.I. Lab coding agents on example host: rules that used to live
as text in handoff files, turned into mechanisms that act on their own. Claude Code
is the primary platform; Codex gets the shared rules and adapter skills.

Plugin `ailab-engineering` **0.2.1** in the marketplace `ailab`. Needs Claude Code
**2.1.295+** (`onFailure: block` on the guard hooks). `bash tests/run-all.sh` runs
every suite (hook tests under jq, node and python3; intel, contract, Codex and
installer tests; shellcheck; `claude plugin validate`).

## What is inside

| Component | What it does |
|---|---|
| **guard-bash** (PreToolUse) | Denies: `git add -A/./-u`, `commit -a`, `git clean`, `reset --hard`, force push, `docker * prune`, `compose down -v`, deleting `.git`, `rm -rf` of root/HOME/the repository and its parents/`/tmp/claude-*`/shared scratch, deleting or overwriting backups and evidence (including `cp`/`mv`/`>`/`xargs rm`), printing `.env` and keys, `env`/`printenv`, `curl \| bash`, `gh auth token`. Asks the owner: push, rebase/merge, stash, docker/systemd restarts, migrations, destructive SQL, `sudo`, `rm -rf` outside the work tree, production paths, and (0.2.0) every `gh` command that is not a known read (PR create/merge/review/comment, issues, releases, workflows, `gh api` writes) plus package, image and deploy publishing. Commit messages, `echo` and heredocs are not treated as commands; from 0.2.2 neither are git commands named in the quoted arguments of read-only text tools (`grep "git push" docs/`, `rg`, `git log --grep`, `echo`, `printf`) or in shell comments. A plain `git push`/`git merge` the owner authorized for the task runs without asking (see [Owner authorization](#owner-authorization-for-a-task)) |
| **authorize** (UserPromptSubmit, 0.2.2) | Records a task-scoped owner authorization from an `ailab-authorize:` line in the owner's own prompt; does nothing for any other prompt |
| **guard-paths** (PreToolUse) | Write/Edit: backups and frozen evidence denied; evidence logs new files only; production asks; `.git/` denied; `.env` and keys ask |
| **session-context** (SessionStart, also after compaction) | Branch, HEAD, change counts, the active WP header and the top of `CURRENT_STATE.md`; one line when commits exist after the memory checkpoint. Exports `AILAB_*` for Bash |
| **scratch + stop-scratch + session-end** | Task files live in `/tmp/ailab-scratch/<session>/<label>`; one Stop reminder to clean or keep them; receipts and `leftovers.tsv` |
| **intel** (`scripts/intel.sh`) | Read-only code and change intelligence: `impact` (dependents and tests a change reaches), `history`, `signals`, `arch-check`, `fbpa` (fail-before/pass-after in copies inside a bubblewrap sandbox; `sandbox-check` proves the isolation), `metrics`. Python 3.9+; every report states its coverage limits |
| **Agents** | `spec-reviewer` (SPEC axis: requirements, unrequested behaviour, criteria vs evidence), `reviewer` (ENGINEERING axis: defects, regressions, test quality, complexity), `security-reviewer`, `qa-runner` (test ladder, FBPA). All read-only |
| **Skills (owner-invoked)** | `/ailab-engineering:work-package`, `:review`, `:wp-close`, `:change-intel`, `:knowledge-review`, `:host-gate`, `:handoff` |
| **Skills (automatic)** | `review` (also runs from a T2/T3 work package), `codeintel`, `scratch` |
| **Knowledge capture** | `wp-close` files at most one evidence-backed lesson per WP into the append-only inbox (`scripts/knowledge.sh`); `knowledge-review` prepares owner decisions; the register is `plugins/ailab-engineering/knowledge/REGISTER.md` |
| **Templates** | `AGENTS.md` (shared by Claude and Codex: behaviour rules, review limits, "Never" list), `CLAUDE.md` importing it, path-scoped `.claude/rules/`, `review-notes.md`, optional `docs/project/ARCHITECTURE.md`, user deny/ask rules |
| **Codex** (`codex/`) | `ailab-*` adapter skills over the same files and an installer; see [Codex](#codex) |

Protected paths: `plugins/ailab-engineering/config/protected-paths.conf` (classes
`immutable`, `appendonly`, `production`, `scratch`; host and container paths). Add
your own in `~/.config/ailab-engineering/protected-paths.conf` (owner-only).

## How work flows

| | T1 small | T2 normal | T3 high risk |
|---|---|---|---|
| Intake | none | `intel impact` + `history` (≤10 lines) | + entry points, contradictions with memory (≤20 lines) |
| Plan | steps with checks | same | same, spec-reviewer plan check, **owner approves before code** |
| Tests | targeted | by type: feature / bugfix (reproduce first, FBPA) / refactor (pins) | same + real-data smoke |
| Review | owner reads the diff + signals | spec-reviewer ∥ reviewer, **≤ 2 rounds** | + security-reviewer, **≤ 3 rounds** |
| QA | — | qa-runner once on the final snapshot | same, then the owner's host gate |
| Close | none for trivial edits | wp-close: evidence reuse, memory draft → owner, Learning Review | same, explicit owner acceptance |

The review loop stops and asks the owner on any scope, product or architecture
decision, a fix outside scope, a finding that comes back, the same test failing
twice, a new P0/P1 introduced by a fix, or the round limit. Pushing, PRs,
publishing, merging and deploying are never part of it.

```text
/ailab-engineering:work-package DFL-B1A "Customer UI login + workspace shell"
   -> .claude/wp/DFL-B1A.md: tier, type, size, criteria, plan with checks, ladder, rollback; T3 stops for plan approval
/goal ... (the skill suggests a bounded goal condition)
   -> implementation with checkpoints; then the review loop runs automatically for T2/T3
/ailab-engineering:review DFL-B1A      -> or run the loop yourself, also for a range: main..feature
/ailab-engineering:wp-close            -> criteria with evidence, explicit staging, scratch cleanup, checkpoint draft
/ailab-engineering:change-intel catch-up  -> what changed since the memory checkpoint, what deserves your attention
/ailab-engineering:knowledge-review    -> decide on pending lessons
/ailab-engineering:handoff             -> a handoff of at most ~120 lines
/ailab-engineering:host-gate           -> a script for the owner to run on the host
```

## Install on example host

Follow `docs/INSTALL-HOST.md`. It discovers how agent-long sees this tree (bind mount
or copy), updates the mounted checkout **in place** (a container keeps the directory it
was created with, so renaming the checkout does not update it), verifies contents and
the version Claude Code really runs in every environment, and rolls back to 0.1.7 the
same way. In short:

```bash
bash install/install.sh --dry-run                                   # prerequisites, all tests, plugin validation; changes nothing
bash install/install.sh --expect-version 0.2.1 --with-deny-rules    # in every environment where Claude Code runs (host user, agent-long)
bash install/verify-install.sh --expect-version 0.2.1 --reference <pristine release dir>   # PASS / FAIL / NOT VERIFIED
bash install/init-project.sh /srv/agents/projects/<repo> --dry-run  # then without --dry-run; never overwrites
bash codex/install-codex-skills.sh --dry-run                        # optional, for the user that runs Codex
```

`jq` is recommended (hooks fall back to node, then python3; with none of them the
guards fail closed and SessionStart warns). `intel` needs Python 3.9+ (`python3` on
PATH, `AILAB_PYTHON`, or `~/.local/share/ailab-python`); without it the analysis
skills say "unavailable" and nothing else breaks.

Recommended official plugins (`claude plugin install <name>@claude-plugins-official`):
`pyright-lsp` and `typescript-lsp` for symbol-level navigation (intel is file-level),
`playwright` only in projects with a UI.

## Codex

Codex reads the project `AGENTS.md` (behaviour rules, review limits, the "Never"
list). `bash codex/install-codex-skills.sh` installs five adapter skills into
`${CODEX_HOME:-~/.codex}/skills`: `ailab-work-package`, `ailab-review` (one review
axis per fresh session; reports count as cross-model evidence in wp-close),
`ailab-wp-close`, `ailab-codeintel`, `ailab-change-intel`. They reuse the plugin's
files through `codex/platform-mapping.md`.

**Guard hooks, SessionStart context and subagents are Claude Code features and are
not active in Codex.** In Codex the AGENTS.md "Never" list and Codex's own sandbox
and approval settings are the only protection.

## If a hook blocked something you need
- Run the command yourself in your own terminal: the guards act only on the agent.
- If the path is a legitimate temporary area, declare it `scratch` in
  `~/.config/ailab-engineering/protected-paths.conf`.
- Last resort: `claude plugin disable ailab-engineering@ailab` while you work.
- Every decision is logged in `~/.claude/plugins/data/ailab-engineering-ailab/guard.log`;
  read it during the first week to find false positives.

## Owner authorization for a task
To stop repeated prompts for routine steps you have already approved, put a line in
your own prompt (not inside a fenced code block):

```
ailab-authorize: <op>[,<op>...] [branch=<name>] [remote=<name>] [repo=<path>]
ailab-authorize: revoke
```

`<op>` is `commit`, `push`, `merge` or `memory`. Each line is one grant for the current
session, bound to the repository (default: the prompt's working directory), the branch
(default: its current branch; for `merge` the branch merged into), the active work package,
and for `push` the remote (default `origin`) and its push URL at that moment. The hook
confirms or rejects each line in the conversation.
- `push`, `merge`: the guard lets a plain `git push`/`git merge` that matches a grant run
  without asking. Force push stays denied. Tags, `--all`, deletes, `--no-verify`, other
  branches, remotes or repositories, rebase, `gh`, a changed remote URL or work package,
  a `cd` it cannot follow, variable assignments or `export`/`read`/`declare` (and a named
  `HOME`, `PATH`, `XDG_*`, `GIT_*`), `eval`/`env`/`sudo`/`-c` scripts/functions, push settings that
  widen a plain push (mirror, `push.followTags`, submodule recursion, `remote.<r>.push`),
  and commands that also switch branches or change config or remotes still ask.
- `commit`, `memory`: the agent is told not to ask again (commits are never hook-gated;
  `wp-close` writes the project-memory checkpoint without a second approval).
- Grants end with the session (`SessionEnd`, a new or resumed session, `/clear`) and
  with `revoke`; compaction keeps them. Only this hook writes them, from your prompt
  (it records nothing when run from a tool call); WP files, reports, tool output and
  subagents cannot. The guards deny commands that run `authorize.sh` or name the grant
  store, deny Write/Edit there, and ask before a tool call resumes a Claude session or
  names `CLAUDE_PLUGIN_DATA`/`CLAUDE_PLUGIN_ROOT` (which the hook runner alone sets).
  Only a hash of the push URL is stored.

## Honest limitations
- The guard parses commands; it is not a sandbox. It stops the common, accidental,
  expensive mistakes; a deliberately obfuscated command (`G=gh; $G pr merge`, an
  existing `gh` alias) can pass. The user settings deny and ask rules are a second
  layer.
- Only the quoted arguments of `grep`, `egrep`, `fgrep`, `rg`, `echo`, `printf` and
  `git log/shortlog` count as data, and not when the command also names a shell,
  interpreter, xargs, `env` or `exec`, runs `./...` or dot-sources, or uses `<<<`,
  `<(...)`, `$(...)`, backticks, `eval` or `source`. Elsewhere (for example `sed -i 's/git push/.../' docs.md`) the strict
  0.2.1 view stays and may still ask. Comments are not stripped from commands with
  `$(...)`, `${...}`, backticks or `$'...'`, or longer than 4,000 characters (after
  heredoc bodies are removed). A granted push asks again when the same command also
  names `push` in other text.
- Grants and hooks run as the same user as the agent: a deliberately obfuscated write
  to the grant store (a path assembled from pieces) is not caught, like any other
  obfuscated command. The guards stop the direct and accidental routes.
- Each Bash call costs about 0.1-0.25 s (jq) or 0.45 s (node) for the guard.
- The guards use `onFailure: block`: if a guard crashes or hangs, the command is
  blocked. If everything is blocked, run `bash tests/run-hook-tests.sh` and read
  `guard.log`.
- "Ask" prompts the owner even in auto mode, on purpose. Hook behaviour in
  bypassPermissions mode is not documented by Claude Code; the publishing ask rules
  in the user settings are the documented layer for that mode.
- Plugin subagents ignore `permissionMode`. Reviewers have Write/Edit disabled but
  keep Bash (for `git diff` and tests), so read-only rests on their instructions and
  on the guards, not on a hard sandbox. `maxTurns` caps each run.
- Review independence comes from separate fresh contexts, not from different models;
  the same model can share blind spots. For T3, a cross-model review in Codex
  (`ailab-review`) is the owner's option.
- `intel impact` sees static imports only (Python, JS/TS); dependency injection,
  string routing, framework autodiscovery and other languages are invisible to it.
  It is a lower bound and says so in every report.
- `fbpa` runs the WP's targeted test command in a bubblewrap sandbox (0.2.1): no
  network, cleared environment, a new session keyring, only system directories, the
  copy and declared read-only directories visible, memory/file/process/time limits.
  It needs `bubblewrap` 0.8+ and user namespaces; inside a Docker container the default
  seccomp profile refuses them, and then fbpa reports BLOCKED and runs nothing
  (`intel.sh sandbox-check` shows which). A BLOCKED check goes to the owner; the test is
  never run unsandboxed instead. Limits of the isolation:
  - there is no seccomp filter of its own and the kernel is shared: this is process
    isolation, not a VM;
  - the memory limit is an address-space limit: runtimes that reserve large virtual
    memory (Node WebAssembly, the JVM, Go) need a higher `--mem-mb`; hitting any limit
    makes the result INCONCLUSIVE, never a "fails before";
  - run as root, the process limit is not enforced by the kernel (the JSON says
    `process_limit_enforced: false`); the time limit still ends the run;
  - disk use in the copy is bounded per file (`--max-file-mb`) and by time, not in total;
  - a failure caused by what the sandbox withholds (network, host services, host files)
    is INCONCLUSIVE; such a test needs `--env`, `--link`, `--toolchain` or an owner decision;
  - `--link` accepts only git-ignored, untracked directories inside the repository and
    `--toolchain` only install roots (with `bin/`) owned by root or the user, without
    sockets; a virtualenv kept outside the repository in a hidden home directory
    (Poetry, pipenv) cannot be exposed.
- Claude Code (verified on 2.1.326) treats every file under `.claude/` as sensitive:
  writing a WP file (`.claude/wp/<ID>.md`) prompts the owner in an interactive
  session and is refused in headless `claude -p` runs, even with an explicit
  `Edit(.claude/wp/**)` allow rule. This affects WP files since 0.1.0; 0.2.0 writes
  a few more Log lines (one per review round). When a write is refused the review
  loop reports the Log line instead of working around the refusal. Moving WP files
  out of `.claude/` is an open owner decision.
- `qa-runner` works on the current tree (preferring a scratch copy for tests that
  write files), not on a worktree: a worktree starts from the default branch and
  would miss uncommitted changes.

## Plugin data
`~/.claude/plugins/data/ailab-engineering-ailab/`: `guard.log` (decisions, commands
truncated and redacted), `receipts/YYYYMM.tsv` (scratch cleaned or kept, with sizes),
`leftovers.tsv` (what sessions left behind), `grants/<session>.tsv` (owner authorizations
of a running session; immutable to the agent). Knowledge inbox:
`<agent-logs>/_knowledge/inbox/` (append-only).

## Update and removal
- Update: bump `version` in `plugins/ailab-engineering/.claude-plugin/plugin.json`,
  add a CHANGELOG line, `bash tests/run-all.sh --with-install`, then update the installed
  tree in place and verify it as in `docs/INSTALL-HOST.md`.
- Rollback: put the previous release back in place and verify it (`docs/INSTALL-HOST.md`,
  step 7). No uninstall is needed: Claude Code runs the plugin from the registered directory.
- Removal: back up `~/.claude/plugins/data/ailab-engineering-ailab/` first (both commands
  below delete it), then
  `claude plugin uninstall ailab-engineering@ailab && claude plugin marketplace remove ailab`;
  deny/ask rules are restored from `~/.claude/settings.json.bak-*`;
  Codex skills: `bash codex/install-codex-skills.sh --uninstall`.

## Public distribution note

Host names, application names, and site-specific protected paths have been replaced with illustrative examples. Configure the guard for your own infrastructure before enabling it. The hooks are operational guardrails, not an OS-level security boundary.
