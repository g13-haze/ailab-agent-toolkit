---
paths:
  - "**/migrations/**"
  - "**/alembic/**"
  - "**/*migration*.py"
  - "**/schema*.sql"
---

# Database migrations (T3 by default)

- Name the exact target database (container, file path, database name) in the
  work package. Two databases with similar names exist on example host
  (e.g. `example-db-1` vs `example-db-test`); never guess.
- Before any migration: fresh backup (`pg_dump -Fc` / `sqlite3 .backup`), restore
  it into an isolated location, compare table and row counts, record SHA-256.
- Migrations must be forward-only safe: additive first, data backfill second,
  destructive changes only in a later, separately approved step.
- Provide a downgrade or a documented restore path; test it on the restored copy.
- Migration commands are run by the owner (or with explicit approval in this
  session), never as a side effect of tests or app startup in production.
- Never interpret a missing row as zero, and never "fix" data in place without
  an approved, logged script.
