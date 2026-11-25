@AGENTS.md

# Claude Code specifics

- The `ailab-engineering` plugin is active: guard hooks block forbidden
  operations deterministically. If a hook blocks you, do not reword the command
  to get around it; stop and ask the owner. Exception: `SCRATCH_CLEANUP_REDIRECT`
  means retry through `scratch.sh clean-path` as the message says.
- Start implementation with `/ailab-engineering:work-package <ID> <goal>`,
  finish with `/ailab-engineering:wp-close`, end long sessions with
  `/ailab-engineering:handoff`.
- Review T2/T3 work with `/ailab-engineering:review` (the work-package skill does
  this): `spec-reviewer` and `reviewer` in parallel, `security-reviewer` for T3,
  bounded rounds, then `qa-runner` once. `qa-runner` also for test suites longer
  than a minute.
- Code intelligence: the `codeintel` skill and `${AILAB_PLUGIN_ROOT}/scripts/intel.sh`
  (impact, history, signals, arch-check); owner catch-up with
  `/ailab-engineering:change-intel`.
- Host or production verification: `/ailab-engineering:host-gate` writes a script
  for the owner to run; never run it against production yourself.
- Temporary files: use `${AILAB_PLUGIN_ROOT}/scripts/scratch.sh` for task-owned
  temporary files (`new <label>`). Prefer `clean-path <abs-path>...` over complex
  recursive Bash deletion. On `SCRATCH_CLEANUP_REDIRECT`, resolve the exact
  absolute path and retry through the helper without asking the owner. Never
  use scratch cleanup for project files, production, backups or evidence.
- Prefer CLI tools (`git`, `gh`, `sqlite3 -readonly`, `docker ... --format`)
  over adding MCP servers. Read-only `gh` commands run freely; `gh` commands that
  publish or change remote state ask the owner.
