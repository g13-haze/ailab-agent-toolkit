---
name: ailab-codeintel
description: Deterministic code intelligence for A.I. Lab repositories - files and tests a change reaches, history and rework of an area, diff risk signals, architecture boundary checks, with stated coverage limits. Use before changing a shared module, or when planning or debugging in an unfamiliar area.
---

# Code intelligence (Codex adapter)

Tool: `@AILAB_OS@/plugins/ailab-engineering/scripts/intel.sh` (read-only; Python 3.9+).
Follow `@AILAB_OS@/plugins/ailab-engineering/skills/codeintel/SKILL.md`, reading
`${CLAUDE_PLUGIN_ROOT}` as `@AILAB_OS@/plugins/ailab-engineering`. Impact is a lower
bound: check UNRESOLVED and DYNAMIC lines with grep before saying nothing else is
affected. Never write generated context files or indexes into the repository.
