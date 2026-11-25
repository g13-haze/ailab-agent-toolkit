#!/usr/bin/env bash
# Knowledge capture (KL-1): evidence-backed engineering lessons from closed work
# packages, kept as append-only candidate records in one shared inbox.
#
# Usage:
#   knowledge.sh find <keyword>...   related candidates and register entries (top 8)
#   knowledge.sh add                 file one candidate; the record is read from stdin
#   knowledge.sh pending             inbox candidates that have no register entry yet
#
# Record for 'add' (one "Key: value" per line):
#   WP: <work package id>
#   Title: <the lesson in a few words>              max 100 chars
#   Lesson: <the check or instruction>              max 400 chars
#   Evidence: <path or commit> — <what it shows>    1-3 lines; must exist, not scratch
#   Scope: project | cross-project — <why>
#   Target: <smallest destination file>
#   Related: none | <id>, <id>                      ids printed by 'find'
#
# The script sets ID, Status (candidate) and Source (project / WP / HEAD / date),
# requires .claude/wp/<WP>.md in the repository, refuses a second candidate for
# the same project and WP, and never edits or deletes a record. It writes
# nothing else: register entries, skills, rules and instructions change only
# through an owner decision, never through this script.
#
# Inbox: <tree>/_knowledge/inbox of an agent-logs tree that the guard configuration
# (protected-paths.conf, owner-only) declares append-only: /work/agent-logs in agent
# containers (bind mount of the host tree), else /srv/agent-logs on the host.
# AILAB_KNOWLEDGE_DIR can only select <tree>/_knowledge of another declared tree
# (test fixtures). Any other value, a symlinked or not append-only destination is
# refused, never falls back to the default, and nothing is written.
# Register (read only): $AILAB_KNOWLEDGE_REGISTER, else <plugin>/knowledge/REGISTER.md.
#
# Exit codes: 0 ok, 2 usage or invalid record, 3 refused (already filed for this
# WP, location not approved, not a repository), 4 write failed.

set -u
shopt -s nullglob
# shellcheck source=lib/common.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

die() { printf 'knowledge: %s\n' "$2" >&2; exit "$1"; }
g() { with_timeout 5 git -c safe.directory='*' --no-optional-locks "$@" 2>/dev/null; }

REGISTER="${AILAB_KNOWLEDGE_REGISTER:-$AILAB_PLUGIN_ROOT/knowledge/REGISTER.md}"

# KD: the approved knowledge directory (real path), or empty with the reason in KD_ERR.
KD="" KD_ERR=""
resolve_kd() {
  local i p r d roots=()
  load_protected_paths
  for i in "${!PROT_PATH[@]}"; do   # declared append-only agent-logs trees, real paths
    p="${PROT_PATH[$i]}"
    [[ "${PROT_CLASS[$i]}" == appendonly && "${p##*/}" == agent-logs && -d "$p" ]] || continue
    r="$(realpath -e -- "$p" 2>/dev/null)" && roots+=("$r")
  done
  if [[ -n "${AILAB_KNOWLEDGE_DIR:-}" ]]; then
    [[ "$AILAB_KNOWLEDGE_DIR" == /* ]] && d="$(realpath -m -- "$AILAB_KNOWLEDGE_DIR" 2>/dev/null)"
    for r in "${roots[@]}"; do [[ -n "${d:-}" && "$d" == "$r/_knowledge" ]] && { KD="$d"; return 0; }; done
    KD_ERR="AILAB_KNOWLEDGE_DIR '$AILAB_KNOWLEDGE_DIR' is not <agent-logs>/_knowledge of an append-only agent-logs tree in the guard configuration"
    return 1
  fi
  for p in /work/agent-logs /srv/agent-logs; do
    d="$(realpath -e -- "$p" 2>/dev/null)" || continue
    for r in "${roots[@]}"; do
      [[ "$d" == "$r" ]] || continue
      [[ "$(realpath -m -- "$d/_knowledge")" == "$d/_knowledge" ]] && { KD="$d/_knowledge"; return 0; }
      KD_ERR="$d/_knowledge is a symlink"; return 1
    done
  done
  KD_ERR="no knowledge inbox: neither /work/agent-logs nor /srv/agent-logs is a declared append-only agent-logs tree here"
  return 1
}
resolve_kd || true
[[ -z "${AILAB_KNOWLEDGE_DIR:-}" || -n "$KD" ]] || die 3 "$KD_ERR; nothing read or written"

records() { # inbox records, then the register (register entries win on equal ids)
  local f
  if [[ -n "$KD" ]]; then for f in "$KD"/inbox/K-*.md; do printf '%s\0' "$f"; done; fi
  [[ -r "$REGISTER" ]] && printf '%s\0' "$REGISTER"
  return 0
}

known_id() { # <id>
  local f
  while IFS= read -r -d '' f; do
    LC_ALL=C awk -v id="$1" '/^## / && $2 == id { found = 1; exit } END { exit !found }' "$f" && return 0
  done < <(records)
  return 1
}

find_cmd() {
  (( $# )) || die 2 "usage: knowledge.sh find <keyword>..."
  local files=() out total
  mapfile -t -d '' files < <(records)
  (( ${#files[@]} )) || { echo "No knowledge inbox or register found."; return 0; }
  out="$(LC_ALL=C awk -v kw="$*" -v reg="$REGISTER" '
    BEGIN {
      n = split(tolower(kw), w, /[^a-z0-9_.-]+/)
      for (i = 1; i <= n; i++) if (length(w[i]) >= 3 && !(w[i] in seen)) { seen[w[i]] = 1; k[++m] = w[i] }
      if (m == 0) { bad = 1; exit 2 }
    }
    function flush(   i, s) {
      if (id == "") return
      s = 0
      for (i = 1; i <= m; i++) if (index(txt, k[i])) s++
      score[id] = s; decided[id] = src; row[id] = s "/" m " | " id " | " st " | " sc " | " ti
      id = ""
    }
    FNR == 1 { flush() }
    /^#/ {
      flush()
      if ($1 == "##" && $2 ~ /^K-[0-9]/) {
        id = $2; src = (FILENAME == reg); st = "?"; sc = "?"; txt = tolower($0)
        ti = $0; sub(/^## [^ ]+ +/, "", ti); sub(/^[^A-Za-z0-9`"(]+/, "", ti)
      }
      next
    }
    id != "" {
      txt = txt " " tolower($0)
      if ($1 == "Status:") st = $2
      if ($1 == "Scope:") sc = $2
    }
    END {
      if (bad) exit 2
      flush()
      for (x in score) if (score[x] > 0) printf "%d\t%d\t%s\t%s\n", score[x], decided[x], x, row[x]
    }' "${files[@]}")" || die 2 "find: give plain keywords of 3 or more characters"
  if [[ -z "$out" ]]; then echo "No related candidates for: $*"; return 0; fi
  total="$(printf '%s\n' "$out" | wc -l | tr -d ' ')"
  echo "Related (keyword hits | id | status | scope | title), top $(( total < 8 ? total : 8 )) of $total:"
  # most keyword hits first; on a tie register entries, then newer ids
  printf '%s\n' "$out" | LC_ALL=C sort -t $'\t' -k1,1nr -k2,2nr -k3,3r | head -n 8 | cut -f4-
}

pending_cmd() {
  local d="$KD" files=() out
  [[ -n "$d" ]] && files=("$d"/inbox/K-*.md)
  (( ${#files[@]} )) || { echo "Pending: 0 candidates (no inbox records)."; return 0; }
  out="$(LC_ALL=C awk -v reg="$REGISTER" '
    BEGIN { while ((getline l < reg) > 0) if (l ~ /^## K-/) { split(l, a, " "); decided[a[2]] = 1 } }
    function flush() { if (id != "" && !(id in decided)) print id " | " sc " | " ti; id = "" }
    FNR == 1 {
      flush()
      if (/^## K-/) { id = $2; sc = "?"; ti = $0; sub(/^## [^ ]+ +/, "", ti); sub(/^[^A-Za-z0-9`"(]+/, "", ti) }
    }
    $1 == "Scope:" { sc = $2 }
    END { flush() }' "${files[@]}" | LC_ALL=C sort)"
  if [[ -z "$out" ]]; then echo "Pending: 0 candidates in $d/inbox"; return 0; fi
  printf '%s\n' "$out" | head -n 50
  echo "Pending: $(printf '%s\n' "$out" | wc -l | tr -d ' ') candidates in $d/inbox (shown at most 50)"
}

add_cmd() {
  (( $# == 0 )) || die 2 "usage: knowledge.sh add <<'EOF' ... EOF (the record comes from stdin)"
  local top common project head inbox raw line key val e ref p rp id file body r scratch
  local -A F=()
  local ev=() rel=()
  top="$(g rev-parse --show-toplevel)" || die 3 "not inside a git repository: run it from the project repository"
  [[ -n "$KD" ]] || die 3 "$KD_ERR; nothing written"
  inbox="$KD/inbox"
  nolink() { [[ ! -L "$KD" && ! -L "$inbox" ]] || die 3 "refusing: $KD or its inbox is a symlink; nothing written"; }
  nolink
  [[ "$(classify_path "$inbox")" == appendonly ]] || die 3 "refusing: $inbox is not an append-only path in the guard configuration; nothing written"

  [[ -t 0 ]] && die 2 "the record comes from stdin (heredoc)"
  raw="$(head -c 8193)"
  (( $(printf '%s' "$raw" | wc -c) <= 8192 )) || die 2 "record larger than 8 KiB"
  local ph='^<[^>]*>$' kv='^([A-Za-z]+):[[:space:]]*(.*[^[:space:]])[[:space:]]*$'
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ -z "${line//[[:space:]]/}" ]] && continue
    [[ "$line" =~ $kv ]] || die 2 "not a 'Key: value' line: ${line:0:80}"
    key="${BASH_REMATCH[1]}"; val="${BASH_REMATCH[2]}"
    [[ "$val" == *[[:cntrl:]]* ]] && die 2 "$key contains control characters"
    [[ "$val" =~ $ph || "$val" == *'<why>'* || "$val" == *'<what it shows>'* ]] && die 2 "$key still contains a template placeholder"
    case "$key" in
      Evidence) ev+=("$val") ;;
      WP|Title|Lesson|Scope|Target|Related)
        [[ -z "${F[$key]:-}" ]] || die 2 "more than one $key line"
        F[$key]="$val" ;;
      *) die 2 "unknown key '$key' (allowed: WP Title Lesson Evidence Scope Target Related; ID, Status and Source are set by the script)" ;;
    esac
  done <<<"$raw"

  for key in WP Title Lesson Scope Target Related; do [[ -n "${F[$key]:-}" ]] || die 2 "missing $key line"; done
  (( ${#ev[@]} >= 1 && ${#ev[@]} <= 3 )) || die 2 "give 1 to 3 Evidence lines"
  [[ "${F[WP]}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]] || die 2 "invalid WP id '${F[WP]:0:70}'"
  [[ -f "$top/.claude/wp/${F[WP]}.md" ]] || die 2 "no work package file .claude/wp/${F[WP]}.md in $top"
  for key in Title:100 Lesson:400 Scope:300 Target:200 Related:300; do
    (( ${#F[${key%:*}]} <= ${key#*:} )) || die 2 "${key%:*} is longer than ${key#*:} characters"
  done
  [[ "${F[Scope]}" =~ ^(project|cross-project)([^A-Za-z0-9|-]|$) && "${F[Scope]}" != *'|'* ]] || die 2 "Scope must start with either 'project' or 'cross-project'"

  # Evidence: the first word is an existing path (absolute or repository-relative,
  # optionally :line) or a commit, outside task scratch; then what it shows.
  scratch="$(realpath -m -- "$AILAB_SCRATCH_ROOT" 2>/dev/null || printf '%s' "$AILAB_SCRATCH_ROOT")"
  for e in "${ev[@]}"; do
    (( ${#e} <= 300 )) || die 2 "an Evidence line is longer than 300 characters"
    ref="${e%%[[:space:]]*}"
    [[ "${e#"$ref"}" =~ [A-Za-z0-9] ]] || die 2 "Evidence '$ref' needs a description of what it shows"
    if [[ "$ref" =~ ^[0-9a-f]{7,40}$ ]] && g -C "$top" rev-parse -q --verify "$ref^{commit}" >/dev/null; then continue; fi
    p="$ref"; [[ "$p" == /* ]] || p="$top/$p"
    [[ ! -e "$p" && "$p" =~ ^(.+):[0-9]+(-[0-9]+)?$ ]] && p="${BASH_REMATCH[1]}"
    [[ -e "$p" ]] || die 2 "Evidence '$ref' is neither an existing path (absolute or repository-relative) nor a commit"
    rp="$(realpath -m -- "$p" 2>/dev/null || printf '%s' "$p")"
    if path_under "$rp" "$scratch" || [[ "$rp" == /tmp/claude-*/scratchpad || "$rp" == /tmp/claude-*/scratchpad/* ]]; then
      die 2 "Evidence '$ref' is task scratch, which is deleted at close; cite the WP evidence directory, a repository file or a commit"
    fi
  done

  if [[ "${F[Related]}" != none ]]; then
    IFS=', ' read -ra rel <<<"${F[Related]}"
    for r in "${rel[@]}"; do
      [[ -z "$r" ]] && continue
      [[ "$r" =~ ^K-[0-9]{8}-[A-Za-z0-9._-]+$ ]] || die 2 "Related: '${r:0:70}' is not a candidate id (write 'none' or ids from find)"
      known_id "$r" || die 2 "Related: unknown id '$r' (use ids printed by find)"
    done
  fi

  common="$(g -C "$top" rev-parse --git-common-dir)"
  [[ "$common" == /* ]] || common="$top/$common"
  common="$(cd "$common" 2>/dev/null && pwd -P)"
  if [[ "$(basename -- "${common:-x}")" == .git ]]; then project="$(basename -- "$(dirname -- "$common")")"   # worktrees share it
  else project="$(basename -- "$top")"
  fi
  project="${project//[^A-Za-z0-9._-]/-}"
  head="$(g -C "$top" rev-parse -q --verify --short HEAD || echo none)"

  # One candidate per project and WP, whether still in the inbox or already reviewed.
  while IFS= read -r -d '' p; do
    if r="$(LC_ALL=C awk -v s="Source: $project / ${F[WP]} / " '
          /^## / { id = $2 }
          index($0, s) == 1 { print id; found = 1; exit }
          END { exit !found }' "$p")"; then
      die 3 "a candidate for $project / ${F[WP]} is already filed: $r (at most one per WP); nothing written"
    fi
  done < <(records)

  id="K-$(date -u +%Y%m%d)-$project-${F[WP]}"
  file="$inbox/$id.md"
  body="$(
    printf '## %s — %s\nStatus: candidate\nSource: %s / %s / HEAD %s / %s\nLesson: %s\n' \
      "$id" "${F[Title]}" "$project" "${F[WP]}" "$head" "$(date -u +%Y-%m-%d)" "${F[Lesson]}"
    for e in "${ev[@]}"; do printf 'Evidence: %s\n' "$e"; done
    printf 'Scope: %s\nTarget: %s\nRelated: %s\n' "${F[Scope]}" "${F[Target]}" "${F[Related]}"
  )"
  umask 022
  nolink; mkdir -p -- "$inbox" 2>/dev/null || die 4 "cannot create $inbox; nothing written"; nolink
  # write relative to the pinned inbox, so a path swapped after the checks is never followed
  if ! ( cd -P -- "$inbox" 2>/dev/null && [[ "$(pwd -P)" == "$inbox" ]] && set -C && printf '%s\n' "$body" >"$id.md" ) 2>/dev/null; then
    [[ -e "$file" || -L "$file" ]] && die 3 "$file already exists; nothing written"
    die 4 "cannot write $file; nothing written"
  fi
  printf 'Filed %s (status candidate, scope %s)\n%s\n' "$id" "${F[Scope]%%[!A-Za-z-]*}" "$file"
}

case "${1:-}" in
  find) shift; find_cmd "$@" ;;
  add) shift; add_cmd "$@" ;;
  pending) shift; (( $# == 0 )) || die 2 "usage: knowledge.sh pending"; pending_cmd ;;
  *) sed -n '2,34p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2; exit 2 ;;
esac
