#!/usr/bin/env bash
# Install the ailab-* Codex skills (adapters over the ailab-engineering plugin files)
# for the CURRENT user. Skills are rendered with the absolute path of this checkout,
# so they keep working while the checkout stays where it is; re-run after moving it
# or after pulling a new version.
#
#   bash codex/install-codex-skills.sh [--dest DIR] [--dry-run] [--uninstall]
#
#   --dest DIR   skills directory (default: ${CODEX_HOME:-~/.codex}/skills)
#   --dry-run    print what would change, change nothing
#   --uninstall  remove the ailab-* skills this installer created (marker file), nothing else
#
# Writes only <dest>/ailab-<name>/ directories that carry the marker file
# .ailab-managed; refuses to overwrite a directory without it. Never edits Codex
# config, AGENTS.md files or project repositories.

set -u
shopt -u patsub_replacement 2>/dev/null || true   # bash 5.2: '&' in ${x//a/b} must stay literal
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
SRC="$REPO/codex/skills"
DEST="${CODEX_HOME:-${HOME:-/nonexistent}/.codex}/skills"
DRY=0; UNINSTALL=0
while (($#)); do
  case "$1" in
    --dest) DEST="${2:?--dest needs a directory}"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help) sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1"; exit 2 ;;
  esac
done
[[ "$DEST" == /* ]] || { echo "--dest must be an absolute path"; exit 2; }
MARK=".ailab-managed"
act() { if (( DRY )); then echo "would: $*"; else "$@"; fi; }

echo "== ailab Codex skills: $( ((UNINSTALL)) && echo uninstall || echo install ) — source $REPO — dest $DEST"
if (( UNINSTALL )); then
  for d in "$DEST"/ailab-*/; do
    [[ -d "$d" ]] || continue
    if [[ -f "$d/$MARK" && ! -L "${d%/}" ]]; then act rm -r -- "${d%/}"; echo "removed: ${d%/}"
    else echo "kept (not created by this installer): ${d%/}"; fi
  done
  exit 0
fi
command -v python3 >/dev/null 2>&1 || echo "  [warn] python3 not on PATH: intel.sh needs Python 3.9+ (or AILAB_PYTHON)"
act mkdir -p -- "$DEST"
n=0
for f in "$SRC"/ailab-*/SKILL.md; do
  name="$(basename "$(dirname "$f")")"
  d="$DEST/$name"
  if [[ -L "$d" || ( -e "$d" && ! -f "$d/$MARK" ) ]]; then
    echo "  [FAIL] $d exists and was not created by this installer; not touching it"; exit 3
  fi
  body="$(cat "$f")"; body="${body//@AILAB_OS@/$REPO}"
  if (( DRY )); then echo "would write: $d/SKILL.md"
  else
    mkdir -p -- "$d" && printf '%s\n' "$body" >"$d/SKILL.md" && printf 'source %s\n' "$REPO" >"$d/$MARK" || { echo "  [FAIL] cannot write $d"; exit 4; }
    echo "  [ok] $d"
  fi
  n=$((n + 1))
done
echo "$n skills $( ((DRY)) && echo 'would be ' )installed. Codex loads them in new sessions; check with: codex debug prompt-input | grep ailab-"
