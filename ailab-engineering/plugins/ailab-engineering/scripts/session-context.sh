#!/usr/bin/env bash
# SessionStart hook (startup | resume | clear | compact).
#
# 1. Exports AILAB_* variables into CLAUDE_ENV_FILE so later Bash commands can
#    call the plugin's scratch helper with the right session id, and disables
#    core dumps for those commands (including agents' git calls).
# 2. Prints a SHORT, bounded state summary that Claude Code adds to context:
#    the engineering closeout rule, repository state, the active work package
#    and the top of CURRENT_STATE.md. After compaction this restores the facts
#    most likely to be lost.

set -u
# shellcheck source=lib/common.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

read_hook_input
PARSER="$(json_parser)"
SID="$(json_get .session_id 2>/dev/null || true)"
SOURCE="$(json_get .source 2>/dev/null || true)"
CWD="$(json_get .cwd 2>/dev/null || true)"
CWD="${CWD:-$PWD}"

# --- 1. Environment for Bash commands -------------------------------------------
if [[ -n "${CLAUDE_ENV_FILE:-}" ]]; then
  {
    printf 'export AILAB_SESSION_ID=%q\n' "${SID:-manual}"
    printf 'export AILAB_PLUGIN_ROOT=%q\n' "$AILAB_PLUGIN_ROOT"
    printf 'export AILAB_DATA_DIR=%q\n' "$AILAB_DATA_DIR"
    printf 'export AILAB_SCRATCH_ROOT=%q\n' "$AILAB_SCRATCH_ROOT"
    printf 'ulimit -c 0 2>/dev/null || true\n'
  } >>"$CLAUDE_ENV_FILE" 2>/dev/null || true
fi

# --- 2. Context summary -----------------------------------------------------------
# Each section is capped separately (by lines, then by characters without
# splitting multi-byte UTF-8), so the important lines are never cut off.
cap() {
  if command -v iconv >/dev/null 2>&1; then
    head -n "$1" | cut -c1-"$2" | iconv -c -f UTF-8 -t UTF-8 2>/dev/null
  else
    head -n "$1" | cut -c1-"$2"
  fi
}
gq() { with_timeout 4 git -c safe.directory='*' --no-optional-locks -C "$top" "$@" 2>/dev/null; }

echo "[ailab-engineering] session context (source: ${SOURCE:-unknown})"
echo "Guardrails active: no mass git staging/clean/reset, no docker prune, no edits to backups or frozen evidence, production changes ask the owner. Temporary files: \"$AILAB_PLUGIN_ROOT/scripts/scratch.sh\" new <label>; delete them with its clean-path <abs-path>. On SCRATCH_CLEANUP_REDIRECT retry that way without asking the owner (never for project files, production, backups or evidence)."
if [[ "$PARSER" == "none" ]]; then
  echo "WARNING: no jq/node/python3 found. ailab-engineering guard hooks CANNOT read tool calls and are INACTIVE. Tell the owner; install jq."
fi
# Printed before the repo section so it stays inside the 2,000-char preview
# Claude Code shows when hook output exceeds its 10,000-char limit.
cat <<'EOF'
Engineering closeout: only right after completing a meaningful engineering task the owner asked for (code, config, docs or a decision changed and verified), end that reply once with the block below. Never after questions, read-only investigations, intermediate steps or trivial edits.
Engineering Closeout
- Project Memory: Update recommended — <why> | Not needed  (CURRENT_STATE.md; INDEX.md only for new docs/contracts/evidence)
- Learning Review: <one evidence-backed reusable lesson -> target file> | No reusable lesson
- Handoff: Needed — <why> | Not needed  (only if work continues elsewhere and its state is not in project memory)
- Owner action: <one approval question> | none
Propose only: write memory, review notes or skills after explicit owner approval (an owner authorization for this task counts: do not ask twice). Passing tests are not owner acceptance. Never stage, commit, push, deploy or restart as part of closeout. Active WP: suggest /ailab-engineering:wp-close.
EOF

# Owner authorizations (authorize.sh) belong to one task: a new, cleared or
# resumed session starts without them; after compaction they are shown again.
if GF="$(grant_file "$SID")" && [[ -f "$GF" ]]; then
  if [[ "$SOURCE" != compact ]]; then
    rm -f -- "$GF"
  else
    echo "Owner authorizations for this session (from the owner's own prompt; do not ask again for these, everything else still needs the owner):"
    while IFS=$'\t' read -r _ gt gb gw go gr _; do
      echo "- $go in $gt on branch $gb, task ${gw}${gr:+, push to remote $gr}"
    done < <(head -n 10 -- "$GF") | cap 10 300
  fi
fi

top="$(cd "$CWD" 2>/dev/null && with_timeout 4 git -c safe.directory='*' rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -n "$top" ]]; then
  branch="$(gq rev-parse --abbrev-ref HEAD || echo '?')"
  [[ "$branch" == "HEAD" ]] && branch="DETACHED"
  head="$(gq log -1 --format='%h %cs %s' | cut -c1-100)"
  if status="$(gq status --porcelain=v1)"; then
    tracked="$(printf '%s\n' "$status" | grep -c '^[ MADRCU][MADRCU ]' || true)"
    untracked="$(printf '%s\n' "$status" | grep -c '^??' || true)"
    staged="$(printf '%s\n' "$status" | grep -c '^[MADRC]' || true)"
    changes="tracked changes ${tracked}, staged ${staged}, untracked ${untracked}"
  else
    changes="git status UNKNOWN (timeout or permission error)"
  fi
  echo "Repo: $top | branch: ${branch:-?} | HEAD: ${head:-?} | $changes"

  active=""
  [[ -r "$top/.claude/wp/ACTIVE" ]] && active="$(head -1 "$top/.claude/wp/ACTIVE" | tr -d '[:space:]')"
  if [[ -n "$active" && -r "$top/.claude/wp/$active.md" ]]; then
    echo
    echo "Active work package: .claude/wp/$active.md (first lines):"
    cap 30 160 <"$top/.claude/wp/$active.md"
  else
    echo "Active work package: none (start one with /ailab-engineering:work-package)."
  fi

  for cs in "$top/docs/project/CURRENT_STATE.md" "$top/CURRENT_STATE.md"; do
    if [[ -r "$cs" ]]; then
      echo
      echo "Top of ${cs#"$top"/} (authoritative project memory; before changing code or this file, read the sections that matter and enough surrounding context; the whole file only if a targeted read is not enough):"
      cap 25 160 <"$cs"
      checkpoint_drift "$top" "$cs" | cap 1 200
      break
    fi
  done
else
  echo "Working directory $CWD is not inside a git repository (or git is unavailable)."
fi

if [[ -d "$AILAB_SCRATCH_ROOT/${SID:-manual}" ]] && [[ "$SOURCE" != "startup" ]]; then
  echo
  echo "Task scratch for this session: $AILAB_SCRATCH_ROOT/${SID:-manual} (manage with \"$AILAB_PLUGIN_ROOT/scripts/scratch.sh\" list)"
fi

exit 0
