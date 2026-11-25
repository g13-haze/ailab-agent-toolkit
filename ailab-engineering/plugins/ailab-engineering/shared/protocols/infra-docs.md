# Test protocol — infra and docs

Tier gates apply unchanged; there is no extra test ceremony.
- **Infra** (CI, containers, scripts, config): evidence is a dry run or plan output
  (`--dry-run`, `terraform plan`, `docker compose config --quiet`), a rollback that was
  rehearsed or is a revert of one commit, and the host gate for anything that touches
  the real host. Never apply to production from the WP.
- **Docs**: every command in the changed text was run or is marked untested; every path
  and link exists (`intel.sh arch-check --strict` checks ARCHITECTURE.md references);
  status words follow the vocabulary in AGENTS.md (implemented / tested / reviewed /
  owner-accepted / merged / deployed).
