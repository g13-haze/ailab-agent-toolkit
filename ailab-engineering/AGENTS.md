# AGENTS.md — ailab-engineering

This repository is the Claude Code marketplace `ailab` with one plugin,
`plugins/ailab-engineering`, plus project templates and installers.

- Hook scripts are bash and must not depend on python3; JSON parsing goes
  through `scripts/lib/common.sh` (jq -> node -> python3).
- After ANY change to `plugins/ailab-engineering/scripts/` or `config/`:
  `bash tests/run-hook-tests.sh` for each parser
  (`AILAB_JSON_PARSER=node`, `AILAB_JSON_PARSER=python3`), `shellcheck -S warning -x`,
  and `claude plugin validate .` plus `claude plugin validate plugins/ailab-engineering`.
- Every new guard rule gets at least one allow test and one deny/ask test.
- Prefer `ask` over `deny` unless the action is irreversible or explicitly
  forbidden by A.I. Lab rules; false positives teach agents to work around hooks.
- Bump `version` in `plugins/ailab-engineering/.claude-plugin/plugin.json` for
  every behaviour change and add a line to `CHANGELOG.md`.
- Skill `!` injections call `scripts/skill-context.sh` only (pre-approved via
  `allowed-tools`, always exits 0).
