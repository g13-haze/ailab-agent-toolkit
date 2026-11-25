#!/usr/bin/env bash
# Prints facts for skill dynamic-context injection. Always exits 0 so a skill
# never fails to load because of a missing repo, file or permission.
# Usage: skill-context.sh <wp-start|wp-template|wp-close|handoff|snapshot|review|knowledge|change-intel>

set -u
# shellcheck source=lib/common.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

mode="${1:-}"
here="$(dirname "${BASH_SOURCE[0]}")"
g() { with_timeout 5 git -c safe.directory='*' --no-optional-locks "$@" 2>/dev/null; }

top="$(g rev-parse --show-toplevel)"

# Operational records that never change code or acceptance semantics: WP records,
# review notes, and the Project Memory files wp-close and handoff write. Every
# other file, including specs and contracts under docs/project/, is in the snapshot.
bookkeeping() {
  case "$1" in
    .claude/wp/*|.claude/review-notes.md) return 0 ;;
    docs/project/CURRENT_STATE.md|docs/project/INDEX.md|docs/project/handoffs/*) return 0 ;;
  esac
  return 1
}

# Content identity of the work tree, so reviewer/QA evidence can be matched to
# the final code: "<blob> <path>" of every file git would track (tracked, or
# untracked and not ignored), minus bookkeeping. Unchanged files reuse their
# index blob; only paths `git status` reports are hashed and no objects are
# written. Staging or committing the same content keeps the id.
snapshot() (
  [[ -n "$top" ]] || { echo "Snapshot: none (not a git repository)"; exit 0; }
  cd "$top" || { echo "Snapshot: unavailable (cannot enter $top)"; exit 0; }
  declare -A blob=()
  local idx=() st=() files=() hashes=() rec path i changed=0 id
  mapfile -t -d '' idx < <(g ls-files -s -z; printf 'rc=%s\0' "$?")
  mapfile -t -d '' st < <(g status --porcelain=v1 -z --untracked-files=all --no-renames; printf 'rc=%s\0' "$?")
  [[ "${idx[-1]}" == rc=0 && "${st[-1]}" == rc=0 ]] || { echo "Snapshot: unavailable (git ls-files/status failed)"; exit 0; }
  unset 'idx[-1]' 'st[-1]'
  for rec in "${idx[@]}"; do      # "<mode> <blob> <stage>\t<path>"
    path="${rec#*$'\t'}"; rec="${rec#* }"; blob["$path"]="${rec%% *}"
  done
  for rec in "${st[@]}"; do       # "XY <path>"
    path="${rec:3}"
    bookkeeping "$path" && continue
    changed=$((changed + 1))
    if [[ -L "$path" ]]; then blob["$path"]="$(printf '%s' "$(readlink -- "$path")" | g hash-object --stdin)"
    elif [[ -f "$path" && -r "$path" && "$path" != *$'\n'* ]]; then files+=("$path")
    elif [[ -e "$path" ]]; then blob["$path"]=unhashed   # directory, unreadable, or newline in name
    else blob["$path"]=deleted
    fi
  done
  if (( ${#files[@]} )); then
    mapfile -t hashes < <(printf '%s\n' "${files[@]}" | with_timeout 30 git -c safe.directory='*' hash-object --stdin-paths 2>/dev/null)
    (( ${#hashes[@]} == ${#files[@]} )) || { echo "Snapshot: unavailable (could not hash ${#files[@]} changed files)"; exit 0; }
    for i in "${!files[@]}"; do blob["${files[$i]}"]="${hashes[$i]}"; done
  fi
  id="$(for path in "${!blob[@]}"; do
          bookkeeping "$path" || [[ "${blob[$path]}" == deleted ]] || printf '%s %s\0' "${blob[$path]}" "$path"
        done | LC_ALL=C sort -z | g hash-object --stdin)"
  [[ -n "$id" ]] || { echo "Snapshot: unavailable (hash failed)"; exit 0; }
  echo "Snapshot: ${id:0:12} (HEAD $(g rev-parse -q --verify --short HEAD || echo none) + $changed changed paths)"
)

if [[ "$mode" == snapshot ]]; then snapshot; exit 0; fi
# change-patch <base>: the reviewed state as one patch - tracked changes against
# <base> plus untracked, not ignored files - minus bookkeeping. Read-only.
if [[ "$mode" == change-patch ]]; then
  base="${2:-}"
  [[ -n "$top" && -n "$base" ]] && cd "$top" && g rev-parse -q --verify "$base^{commit}" >/dev/null \
    || { echo "# change-patch: give a valid base commit inside a repository"; exit 0; }
  ex=(':(exclude).claude/wp' ':(exclude).claude/review-notes.md' ':(exclude)docs/project/CURRENT_STATE.md'
      ':(exclude)docs/project/INDEX.md' ':(exclude)docs/project/handoffs')
  g diff --binary --no-ext-diff --no-textconv --src-prefix=a/ --dst-prefix=b/ "$base" -- . "${ex[@]}"
  while IFS= read -r -d '' f; do
    bookkeeping "$f" && continue
    with_timeout 10 git -c safe.directory='*' --no-optional-locks diff --no-index --binary --no-ext-diff \
      --src-prefix=a/ --dst-prefix=b/ /dev/null "$f" 2>/dev/null
  done < <(g ls-files -o --exclude-standard -z)
  exit 0
fi
if [[ "$mode" == wp-template ]]; then
  grep -v '^<!--' "$here/../shared/wp-template.md" 2>/dev/null || echo "(WP template unavailable: $here/../shared/wp-template.md)"
  exit 0
fi

if [[ -z "$top" ]]; then
  echo "Repository: NOT A GIT REPOSITORY (cwd: $PWD)"
else
  branch="$(g rev-parse --abbrev-ref HEAD)"; [[ "$branch" == "HEAD" ]] && branch="DETACHED"
  echo "Repository: $top"
  echo "Branch: ${branch:-?}"
  echo "HEAD: $(g log -1 --format='%H %cs %s' | cut -c1-140)"
  status="$(g status --short)"
  echo "Working tree: $(printf '%s' "$status" | grep -c . || true) changed paths"
fi

active="$(head -1 .claude/wp/ACTIVE 2>/dev/null | tr -d '[:space:]')"
echo "Active WP: ${active:-none}"
wpf=""; [[ -n "$active" && -r ".claude/wp/$active.md" ]] && wpf=".claude/wp/$active.md"
state=""; for cs in docs/project/CURRENT_STATE.md CURRENT_STATE.md; do [[ -r "$cs" ]] && { state="$cs"; break; }; done
drift() { [[ -n "$top" && -n "$state" ]] && checkpoint_drift "$top" "$PWD/$state"; return 0; }
wp_base() { [[ -n "$wpf" ]] && grep -m1 -oE 'Repo HEAD at start:[[:space:]]*[0-9a-f]{7,40}' "$wpf" | grep -oE '[0-9a-f]{7,40}$'; }
intel_status() {
  local out
  out="$("$here/intel.sh" --version 2>/dev/null | head -1)"
  [[ "$out" == intel* ]] && echo "Code intelligence: ${CLAUDE_PLUGIN_ROOT:-$AILAB_PLUGIN_ROOT}/scripts/intel.sh available" \
    || echo "Code intelligence: UNAVAILABLE (no Python 3.9+); use git and grep and say impact is unknown"
}

case "$mode" in
  wp-start)
    [[ -n "$top" ]] && printf '%s\n' "$status" | head -25
    wps=""; for w in .claude/wp/*.md; do [[ -e "$w" ]] && wps+="$(basename "$w" .md) "; done
    echo "Existing work packages: ${wps:-none}"
    drift
    intel_status
    echo
    grep -v '^<!--' "$here/../shared/engineering-behaviour.md" 2>/dev/null || echo "(engineering behaviour rules unavailable)"
    ;;
  wp-close)
    snapshot
    if [[ -n "$top" ]]; then
      printf '%s\n' "$status" | head -40
      echo "--- diff stat ---"
      g diff --stat | tail -15
      echo "--- staged ---"
      g diff --cached --stat | tail -15
    fi
    echo "--- scratch (session ${AILAB_SESSION_ID:-unknown}) ---"
    "$(dirname "${BASH_SOURCE[0]}")/scratch.sh" list 2>/dev/null || echo "scratch helper unavailable"
    ;;
  handoff)
    [[ -n "$wpf" ]] && { echo "--- active WP header ---"; head -n 5 -- "$wpf"; }
    drift
    [[ -n "$top" ]] && { echo "--- recent commits ---"; g log --oneline -8; }
    ;;
  review)
    snapshot
    if [[ -n "$wpf" ]]; then
      echo "--- active WP header ---"; head -n 5 -- "$wpf"
      base="$(wp_base)"; echo "WP base (Repo HEAD at start): ${base:-unknown}"
      echo "Evidence: $(sed -n '/^## Evidence/{n;p;q}' "$wpf" | cut -c1-200)"
      echo "--- review rounds so far ---"
      grep -E 'review round [0-9]+/[0-9]+' "$wpf" | tail -n 6 | cut -c1-300 || true
      grep -qE 'review round [0-9]+/[0-9]+' "$wpf" || echo "none"
      evd="$(sed -n '/^## Evidence/{n;p;q}' "$wpf" | grep -oE '(^|[[:space:]])/[^[:space:]()<>]+' | head -1 | tr -d '[:space:]')"
      if [[ -n "$evd" && -d "$evd" ]]; then
        nrep=0; for r in "$evd"/review-*.md; do [[ -e "$r" ]] && nrep=$((nrep + 1)); done
        echo "Engineering review reports in $evd: $nrep (rounds so far = the larger of this and the Log lines)"
      fi
      if [[ -n "$top" && -n "$base" ]]; then echo "--- diff stat vs WP base ---"; g diff --stat "$base" | tail -15; fi
    else
      echo "No active work package: review a range or ask the owner for the requirements."
    fi
    [[ -n "$top" ]] && { echo "--- working tree ---"; printf '%s\n' "$status" | head -25; }
    intel_status
    ;;
  knowledge)
    "$here/knowledge.sh" pending 2>&1 | tail -n 60 || true
    reg="${AILAB_KNOWLEDGE_REGISTER:-$here/../knowledge/REGISTER.md}"
    n="$(grep -c '^## K-' "$reg" 2>/dev/null)" || true
    echo "Register entries: ${n:-0} ($reg)"
    ;;
  change-intel)
    drift
    [[ -n "$state" ]] && echo "Project memory: $state" || echo "Project memory: none (no CURRENT_STATE.md)"
    [[ -n "$top" ]] && { echo "--- recent commits ---"; g log --oneline -10; }
    intel_status
    ;;
  *)
    echo "skill-context: unknown mode '$mode'"
    ;;
esac
exit 0
