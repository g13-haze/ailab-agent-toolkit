#!/usr/bin/env bash
# Add the A.I. Lab agent templates to one project repository WITHOUT
# overwriting anything that already exists, and without staging or committing.
#
#   bash install/init-project.sh /srv/agents/projects/<repo> [--dry-run] [--with-architecture]
#
# Creates only missing files:
#   AGENTS.md, CLAUDE.md (imports AGENTS.md), .claude/review-notes.md,
#   .claude/rules/migrations.md, .claude/rules/security-surface.md, .claude/wp/
#   docs/project/ARCHITECTURE.md only with --with-architecture (boundaries for intel.sh arch-check)
# Appends missing lines to .gitignore. Prints what it did; review with git status.
#
# If the repo already has a CLAUDE.md, it is left untouched and the script tells
# you to add the line "@AGENTS.md" at its top yourself (or merge by hand).

set -u
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/../templates/project" && pwd)"
DEST="${1:-}"; shift || true
DRY=0; ARCH=0
for opt in "$@"; do
  case "$opt" in
    --dry-run) DRY=1 ;;
    --with-architecture) ARCH=1 ;;
    *) echo "unknown option: $opt"; exit 2 ;;
  esac
done
[[ -n "$DEST" && -d "$DEST" ]] || { echo "usage: init-project.sh <repo-dir> [--dry-run] [--with-architecture]"; exit 2; }
DEST="$(cd "$DEST" && pwd)"
git -C "$DEST" rev-parse --show-toplevel >/dev/null 2>&1 || echo "note: $DEST is not a git repository"

act() { if (( DRY )); then echo "would: $*"; else "$@"; fi; }

copy_if_missing() { # <relative path>
  local rel="$1"
  if [[ -e "$DEST/$rel" ]]; then
    echo "kept existing: $rel"
  else
    act mkdir -p "$(dirname "$DEST/$rel")"
    act cp "$SRC/$rel" "$DEST/$rel"
    echo "created: $rel"
  fi
}

copy_if_missing AGENTS.md
if [[ -e "$DEST/CLAUDE.md" || -e "$DEST/.claude/CLAUDE.md" ]]; then
  if grep -qs '^@AGENTS.md' "$DEST/CLAUDE.md" "$DEST/.claude/CLAUDE.md"; then
    echo "kept existing: CLAUDE.md (already imports AGENTS.md)"
  else
    echo "kept existing: CLAUDE.md  -> ACTION: add '@AGENTS.md' as its first line, then move duplicated rules into AGENTS.md"
  fi
else
  copy_if_missing CLAUDE.md
fi
copy_if_missing .claude/review-notes.md
copy_if_missing .claude/rules/migrations.md
copy_if_missing .claude/rules/security-surface.md
(( ARCH )) && copy_if_missing docs/project/ARCHITECTURE.md
[[ -d "$DEST/.claude/wp" ]] || { act mkdir -p "$DEST/.claude/wp"; echo "created: .claude/wp/"; }

while IFS= read -r line; do
  [[ -z "$line" ]] && continue
  if ! grep -qxF -- "$line" "$DEST/.gitignore" 2>/dev/null; then
    if (( DRY )); then echo "would append to .gitignore: $line"; else printf '%s\n' "$line" >>"$DEST/.gitignore"; echo "appended to .gitignore: $line"; fi
  fi
done <"$SRC/gitignore-additions.txt"

echo
echo "Done. Nothing was staged. Next: fill the <placeholders> in AGENTS.md (product, commands), review 'git status', commit explicitly."
