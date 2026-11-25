# Claude Code → Codex mapping for the ailab-* skills

The Codex `ailab-*` skills reuse the files of the Claude Code plugin
`ailab-engineering` (skills, agents, shared policy, scripts). Read those files with
this mapping. `@AILAB_OS@` is the ailab-engineering checkout this skill was
installed from.

| The Claude Code files say | In Codex |
|---|---|
| `${CLAUDE_PLUGIN_ROOT}` or `$AILAB_PLUGIN_ROOT` | `@AILAB_OS@/plugins/ailab-engineering` |
| a line `` !`<command>` `` (dynamic context) | run that command yourself first and read its output |
| `allowed-tools`, `disable-model-invocation`, `maxTurns` | ignore |
| `/ailab-engineering:<name>` | the Codex skill `ailab-<name>` when installed; otherwise follow `@AILAB_OS@/plugins/ailab-engineering/skills/<name>/SKILL.md` |
| `qa-runner` or the built-in Explore subagent | do that work yourself in this session, with the same rules (run only the ladder's commands; report failures, not logs) |
| `spec-reviewer`, `reviewer`, `security-reviewer` | never review your own implementation in the same session. Use the `ailab-review` skill in a FRESH Codex session, one axis per session, or ask the owner to run `/ailab-engineering:review` in Claude Code |
| guard hooks (guard-bash, guard-paths), SessionStart context, Stop reminder | **not active in Codex.** Nothing blocks a forbidden command for you. The "Never" list in the project's AGENTS.md and Codex's own sandbox and approval settings are the only protection: no mass staging, destructive cleanup, push, pull request, release, package publishing, deploy, migration or production change without the owner's explicit word in this session |
| `AILAB_SESSION_ID` (task scratch) | unset: `scratch.sh new|keep|clean <label>` use the session `manual`; `scratch.sh clean-path` refuses without a trusted session id, so clean whole labels with `scratch.sh clean <label>` |
| `knowledge.sh`, `intel.sh`, `skill-context.sh` | work unchanged (bash; `intel.sh` needs Python 3.9+; `intel.sh fbpa` needs bubblewrap and reports BLOCKED without it — never run the test unsandboxed instead) |

Owner approval boundaries are identical on both platforms: plans for T3, project
memory writes, Learning Review changes, out-of-scope files, full-suite or real-data
runs during close, and every commit, push, merge, publish and deploy.
