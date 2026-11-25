---
name: ailab-wp-close
description: Close the active A.I. Lab work package - acceptance criteria with evidence, reuse of review and QA reports that match the final snapshot, explicit staging only if the owner asked, scratch cleanup, project-memory draft for owner approval, and the Learning Review. Use when a work package's implementation is finished.
---

# Close an A.I. Lab work package (Codex adapter)

Mapping: `@AILAB_OS@/codex/platform-mapping.md`.

1. Facts: run `@AILAB_OS@/plugins/ailab-engineering/scripts/skill-context.sh wp-close`
   (snapshot, diff, scratch).
2. Follow the steps, effectiveness lines and final report of
   `@AILAB_OS@/plugins/ailab-engineering/skills/wp-close/SKILL.md` with the mapping.
   Review evidence from Codex sessions (`codex-*` reports) counts for its axis when it
   names the current snapshot.
3. Unchanged owner boundaries: no full-suite or real-data run without approval; files
   outside scope are the owner's call; stage explicit files only and only when the
   owner asked; never push or merge; CURRENT_STATE and INDEX drafts are written only
   after explicit approval; the Learning Review files at most one candidate through
   `@AILAB_OS@/plugins/ailab-engineering/scripts/knowledge.sh` and edits nothing else.
4. Scratch: `scratch.sh clean <label>` for each label of this WP (session `manual`).
