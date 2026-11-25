#!/usr/bin/env bash
# Task-owned scratch lifecycle (producer-owned self-cleanup).
#
# Every temporary artifact of a task (fixtures, DB copies, build outputs,
# downloaded data, test databases) lives in a labelled directory under
#   $AILAB_SCRATCH_ROOT/<session-id>/<label>
# so it is always attributable, measurable and removable by exact path.
#
# Usage:
#   scratch.sh new <label>             create (or reuse) and print the directory
#   scratch.sh path <label>            print the directory without creating it
#   scratch.sh list                    this session's scratch: size, age, keep status
#   scratch.sh keep <label> <reason>   mark as intentionally kept (silences the Stop reminder)
#   scratch.sh clean <label>           delete one label and write a receipt
#   scratch.sh clean-all [--include-kept]   delete this session's scratch
#   scratch.sh clean-path [--missing-ok] <abs-path>...
#                                      delete exact paths inside this session's scratch + receipt
#   scratch.sh report                  all sessions on this host (for Ops)
#
# Labels: 1-64 chars of [A-Za-z0-9._-]. Deletion is refused for anything that
# does not resolve exactly to <root>/<this-session>/<label>, or for clean-path
# to a path below it (or below this session's Claude Code scratchpad).

set -u
# shellcheck source=lib/common.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

die() { printf 'scratch: %s\n' "$*" >&2; exit 1; }

SID="${AILAB_SESSION_ID:-manual}"
[[ "$SID" =~ ^[A-Za-z0-9_-]{1,128}$ ]] || die "invalid session id '$SID'"
ROOT="${AILAB_SCRATCH_ROOT%/}"
[[ "$ROOT" == /* && "$ROOT" != "/" && "$ROOT" != "/tmp" ]] || die "unsafe scratch root '$ROOT'"
BASE="$ROOT/$SID"
RECEIPTS="$AILAB_DATA_DIR/receipts"
umask 077

# The session directory must be a real directory directly under the real root.
check_base() {
  [[ -e "$BASE" ]] || return 0
  [[ -L "$BASE" || -L "$ROOT" ]] && die "refusing: scratch root or session dir is a symlink"
  local rr rb
  rr="$(realpath -m -- "$ROOT")"; rb="$(realpath -m -- "$BASE")"
  [[ "$rb" == "$rr/$SID" ]] || die "refusing: '$BASE' resolves to '$rb'"
}
check_base

valid_label() {
  [[ "$1" =~ ^[A-Za-z0-9_-][A-Za-z0-9._-]{0,63}$ ]] || die "invalid label '$1' (allowed: A-Za-z0-9._- , must not start with '.', max 64)"
}

size_bytes() { du -sb -- "$1" 2>/dev/null | cut -f1 || echo 0; }
human() {
  local b="${1:-0}"
  if command -v numfmt >/dev/null 2>&1; then numfmt --to=iec --suffix=B "$b"; else printf '%sB' "$b"; fi
}

safe_dir() {
  # Resolve and verify the label directory is exactly BASE/<label>.
  local label="$1" d real
  d="$BASE/$label"
  [[ -e "$d" ]] || return 1
  real="$(realpath -- "$d" 2>/dev/null || printf '%s' "$d")"
  [[ "$real" == "$(realpath -m -- "$BASE" 2>/dev/null || printf '%s' "$BASE")/$label" ]] || die "refusing: '$d' resolves to '$real' (symlink?)"
  [[ -L "$d" ]] && die "refusing: '$d' is a symlink"
  printf '%s' "$d"
}

write_receipt() {
  local label="$1" bytes="$2" path="$3" action="$4"
  mkdir -p "$RECEIPTS" 2>/dev/null || return 0
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$SID" "$action" "$label" "$bytes" "$path" \
    >>"$RECEIPTS/$(date -u +%Y%m).tsv"
}

# --- clean-path ------------------------------------------------------------------
# Deletes exact paths below this session's scratch base: the plugin scratch
# <root>/<session> or this session's Claude Code scratchpad
# /tmp/claude-<uid>/<project>/<session>/scratchpad. Subdirectories need no
# registration. The session comes from AILAB_SESSION_ID (SessionStart hook)
# and must equal Claude Code's CLAUDE_CODE_SESSION_ID when both are set.
# Every path is validated before anything is deleted. Each target is then
# renamed from its pinned parent into a private <base>/.trash/<random> entry,
# re-checked there (same device and inode, no mount below it) and removed with
# rm --one-file-system, which never follows symlinks.
# Exit: 0 done | 2 usage | 3 refused, nothing deleted | 4 missing, nothing
#       deleted | 5 removal failed or the target changed (left in .trash)

cp_refuse() { # <path> <reason>
  local p="${1//[[:cntrl:]]/?}"
  printf 'scratch clean-path: REFUSED %s: %s. Nothing was deleted.\n' "$p" "$2" >&2
  printf 'This is not routine task scratch cleanup. Do not work around it; if the deletion is really needed, use rm with the literal absolute path so the guard applies its normal rules.\n' >&2
  write_receipt "-" 0 "$p" "clean-path-refused"
  exit 3
}

# mount_conflict <base> <path> : prints a mount point that is the path, lies
# below it, is the base, or lies inside base above it. An unreadable mount
# table counts as a conflict. AILAB_SCRATCH_EXTRA_MOUNTS (tests) can only add
# mount points.
mount_conflict() {
  local base="$1" p="$2" m
  [[ -r /proc/self/mountinfo ]] || { echo "(mount table unreadable)"; return 0; }
  while IFS= read -r m; do
    if [[ "$m" == "$p" || "$m" == "$p/"* || "$m" == "$base" ]] || [[ "$m" == "$base/"* && "$p" == "$m/"* ]]; then
      printf '%s' "$m"; return 0
    fi
  done < <(cut -d' ' -f5 /proc/self/mountinfo; [[ -n "${AILAB_SCRATCH_EXTRA_MOUNTS:-}" ]] && cat -- "$AILAB_SCRATCH_EXTRA_MOUNTS" 2>/dev/null)
  return 1
}

# cp_check_base <base> <path> : the base is a private, symlink-free directory
# of this user, deep enough, not containing HOME and not inside a repository.
cp_check_base() {
  local b="$1" p="$2" mode d
  [[ "$(realpath -e -- "$b" 2>/dev/null)" == "$b" ]] || cp_refuse "$p" "session scratch '$b' contains a symlink"
  [[ "$(stat -c %u -- "$b")" == "$(id -u)" ]] || cp_refuse "$p" "session scratch '$b' is not owned by this user"
  mode="$(stat -c %a -- "$b")"
  (( (8#$mode & 8#077) == 0 )) || cp_refuse "$p" "session scratch '$b' is not private (mode $mode; scratch.sh new <label> creates it with 700)"
  d="${b//[^\/]/}"; (( ${#d} >= 3 )) || cp_refuse "$p" "session scratch '$b' is too shallow"
  [[ -n "${HOME:-}" ]] && path_under "$HOME" "$b" && cp_refuse "$p" "session scratch '$b' contains the home directory"
  d="$b"
  while [[ -n "$d" ]]; do
    [[ -e "$d/.git" ]] && cp_refuse "$p" "session scratch '$b' lies inside a git repository ($d)"
    d="${d%/*}"
  done
  [[ -e /.git ]] && cp_refuse "$p" "session scratch '$b' lies inside a git repository (/)"
  return 0
}

clean_path() {
  local missing_ok=0 a c p q pb base rel parent name trash tdir t m i j rc=0 uid cwd
  local -a args=() tp=() tb=() tid=() tbytes=()
  while (( $# )); do
    case "$1" in
      --missing-ok) missing_ok=1 ;;
      --) shift; args+=("$@"); break ;;
      -*) printf 'scratch clean-path: unknown option %s\n' "$1" >&2; exit 2 ;;
      *) args+=("$1") ;;
    esac
    shift
  done
  ((${#args[@]})) || { echo "usage: scratch.sh clean-path [--missing-ok] <absolute-path>..." >&2; exit 2; }

  a="${AILAB_SESSION_ID:-}"; c="${CLAUDE_CODE_SESSION_ID:-}"
  [[ -n "$a" && -n "$c" && "$a" != "$c" ]] && cp_refuse "${args[0]}" "session identity mismatch (AILAB_SESSION_ID '$a', CLAUDE_CODE_SESSION_ID '$c')"
  SID="${a:-$c}"
  [[ "$SID" =~ ^[A-Za-z0-9_-]{1,128}$ && "$SID" != manual ]] || cp_refuse "${args[0]}" "no trusted session identity (AILAB_SESSION_ID or CLAUDE_CODE_SESSION_ID)"
  pb="$ROOT/$SID"; uid="$(id -u)"; cwd="$(pwd -P)"
  # The plugin's own config, wherever AILAB_PLUGIN_ROOT points (read by load_protected_paths).
  # shellcheck disable=SC2034
  AILAB_PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
  load_protected_paths

  for p in "${args[@]}"; do
    q="$p"
    [[ "$q" == /* ]] || cp_refuse "$p" "not an absolute path"
    [[ "$q" == *[[:space:][:cntrl:]\\*?[]* ]] && cp_refuse "$p" "ambiguous path (whitespace, control, backslash or glob characters)"
    while [[ "$q" == */ && "$q" != / ]]; do q="${q%/}"; done
    [[ "$q" == *//* || "$q/" == */./* || "$q/" == */../* ]] && cp_refuse "$p" "ambiguous path ('//', '.' or '..' component)"
    if [[ "$q" == "$ROOT" || "$q" == "$pb" ]]; then
      cp_refuse "$q" "the scratch root and the session root are never removed; name paths inside them"
    elif [[ "$q" == "$pb/"* ]]; then
      base="$pb"
    elif [[ "$q" == "$ROOT/"* ]]; then
      cp_refuse "$q" "belongs to another session's scratch"
    elif [[ "$q" =~ ^(/tmp/claude-$uid/[^/]+/$SID/scratchpad)/. ]]; then
      base="${BASH_REMATCH[1]}"
    elif [[ "$q" =~ ^/tmp/claude-[0-9]+/[^/]+/[^/]+/scratchpad(/|$) ]]; then
      cp_refuse "$q" "the scratchpad root, or another session's or user's scratchpad"
    else
      cp_refuse "$q" "outside this session's scratch ($pb/... or its Claude Code scratchpad)"
    fi
    rel="${q#"$base"/}"
    [[ "$rel" == .trash || "$rel" == .trash/* ]] && cp_refuse "$q" "clean-path's own trash (leftovers need a manual look)"
    for i in "${!PROT_PATH[@]}"; do
      [[ "${PROT_CLASS[$i]}" == scratch ]] && continue
      if path_under "$q" "${PROT_PATH[$i]}" || path_under "${PROT_PATH[$i]}" "$q"; then
        cp_refuse "$q" "${PROT_CLASS[$i]} path ${PROT_PATH[$i]}"
      fi
    done
    path_under "$cwd" "$q" && cp_refuse "$q" "contains the current working directory"
    for i in "${!tp[@]}"; do
      if path_under "$q" "${tp[$i]}" || path_under "${tp[$i]}" "$q"; then cp_refuse "$q" "overlaps another argument (${tp[$i]})"; fi
    done
    parent="${q%/*}"
    if [[ ! -e "$q" && ! -L "$q" ]]; then
      [[ -d "$parent" && "$(realpath -e -- "$parent" 2>/dev/null)" != "$parent" ]] && cp_refuse "$q" "a parent directory is a symlink"
      (( missing_ok )) || { printf 'scratch clean-path: MISSING %s. Nothing was deleted (use --missing-ok for idempotent cleanup).\n' "$q" >&2; exit 4; }
      echo "absent: $q"
      continue
    fi
    [[ -d "$base" && ! -L "$base" ]] || cp_refuse "$q" "session scratch '$base' is not a directory"
    cp_check_base "$base" "$q"
    [[ "$(realpath -e -- "$parent" 2>/dev/null)" == "$parent" ]] || cp_refuse "$q" "a parent directory is a symlink"
    if [[ -L "$q" ]]; then
      t="$(realpath -m -- "$q" 2>/dev/null)"
      path_under "$t" "$base" || cp_refuse "$q" "symlink pointing outside this session's scratch (${t:-unresolvable})"
    fi
    [[ "$(stat -c %d -- "$q")" == "$(stat -c %d -- "$base")" ]] || cp_refuse "$q" "on a different filesystem than the session scratch"
    m="$(mount_conflict "$base" "$q")" && cp_refuse "$q" "mount point $m at, below or above the target inside scratch"
    tp+=("$q"); tb+=("$base"); tid+=("$(stat -c %d:%i -- "$q")"); tbytes+=("$(size_bytes "$q")")
  done

  # Test seam (tests/run-hook-tests.sh): pause between validation and removal.
  if [[ -n "${AILAB_SCRATCH_TEST_PAUSE:-}" && -p "$AILAB_SCRATCH_TEST_PAUSE.ready" && -p "$AILAB_SCRATCH_TEST_PAUSE.go" ]]; then
    echo validated >"$AILAB_SCRATCH_TEST_PAUSE.ready"; read -r _ <"$AILAB_SCRATCH_TEST_PAUSE.go"
  fi

  for i in "${!tp[@]}"; do
    q="${tp[$i]}"; base="${tb[$i]}"; parent="${q%/*}"; name="${q##*/}"; trash="$base/.trash"
    mkdir -p -- "$trash" 2>/dev/null
    tdir=""
    if [[ -d "$trash" && ! -L "$trash" && "$(realpath -e -- "$trash" 2>/dev/null)" == "$trash" ]] \
       && ! mount_conflict "$base" "$trash" >/dev/null; then
      tdir="$(mktemp -d -- "$trash/XXXXXXXXXX" 2>/dev/null)" || tdir=""
    fi
    # Same filesystem as the target, so mv renames and never falls back to copy + delete.
    if [[ -n "$tdir" && "$(stat -c %d -- "$tdir" 2>/dev/null)" != "${tid[$i]%%:*}" ]]; then
      rmdir -- "$tdir" 2>/dev/null; tdir=""
    fi
    if [[ -z "$tdir" ]]; then
      rmdir -- "$trash" 2>/dev/null
      printf 'scratch clean-path: FAILED %s: no private trash entry in %s on the same filesystem (symlink, mount point or other device); nothing deleted.\n' "$q" "$trash" >&2
      rc=5; continue
    fi
    # Rename relative to the pinned parent, so a parent swapped for a symlink
    # after validation is noticed instead of followed.
    if ! ( cd -P -- "$parent" 2>/dev/null && [[ "$(pwd -P)" == "$parent" ]] && mv -fT -- "./$name" "$tdir/x" ) 2>/dev/null; then
      rmdir -- "$tdir" "$trash" 2>/dev/null
      printf 'scratch clean-path: FAILED %s: the path changed after validation or could not be moved; nothing deleted.\n' "$q" >&2
      rc=5; continue
    fi
    j=0
    # Remove only the entry that was validated: same device and inode, no
    # mount below it; from inside the pinned trash entry.
    ( cd -P -- "$tdir" 2>/dev/null && [[ "$(pwd -P)" == "$tdir" ]] \
        && [[ "$(stat -c %d:%i -- ./x 2>/dev/null)" == "${tid[$i]}" ]] \
        && ! mount_conflict "$base" "$tdir/x" >/dev/null || exit 9
      rm -rf --one-file-system -- ./x 2>/dev/null && exit 0
      # Read-only directories (module caches): make only directories writable
      # (files may be hard links to data outside scratch), retry once.
      find ./x -xdev -type d ! -perm -u=rwx -exec chmod u+rwx -- {} + 2>/dev/null
      rm -rf --one-file-system -- ./x ) || j=$?
    if (( j == 9 )); then
      printf 'scratch clean-path: FAILED %s: the target changed between validation and removal; not deleted, left in %s/x for inspection.\n' "$q" "$tdir" >&2
      rc=5; continue
    fi
    if (( j != 0 )) || [[ -e "$tdir/x" || -L "$tdir/x" ]]; then
      printf 'scratch clean-path: FAILED %s: removal incomplete; the rest is in %s/x.\n' "$q" "$tdir" >&2
      rc=5; continue
    fi
    rmdir -- "$tdir" "$trash" 2>/dev/null
    write_receipt "${q#"$base"/}" "${tbytes[$i]}" "$q" "clean-path"
    echo "cleaned: $q ($(human "${tbytes[$i]}"))"
  done
  ((${#tp[@]})) && echo "receipt: $RECEIPTS/$(date -u +%Y%m).tsv"
  return "$rc"
}

cmd="${1:-}"; shift || true
case "$cmd" in
  new)
    label="${1:-}"; [[ -n "$label" ]] || die "usage: scratch.sh new <label>"
    valid_label "$label"
    d="$BASE/$label"
    mkdir -p -- "$d" || die "cannot create $d"
    chmod 700 "$ROOT" "$BASE" 2>/dev/null || true
    if [[ ! -f "$d/.meta" ]]; then
      printf 'created=%s\nsession=%s\nlabel=%s\ncwd=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$SID" "$label" "$PWD" >"$d/.meta"
    fi
    printf '%s\n' "$d"
    ;;

  path)
    label="${1:-}"; valid_label "$label"
    printf '%s\n' "$BASE/$label"
    ;;

  list)
    [[ -d "$BASE" ]] || { echo "no scratch for session $SID"; exit 0; }
    printf '%-28s %10s  %-20s %s\n' LABEL SIZE CREATED STATUS
    for d in "$BASE"/*/; do
      [[ -d "$d" ]] || continue
      label="$(basename "$d")"
      created="$(grep -m1 '^created=' "$d/.meta" 2>/dev/null | cut -d= -f2)"
      status="temporary"
      [[ -f "$d/.keep" ]] && status="KEPT: $(head -1 "$d/.keep" | cut -c1-60)"
      printf '%-28s %10s  %-20s %s\n' "$label" "$(human "$(size_bytes "$d")")" "${created:-?}" "$status"
    done
    ;;

  keep)
    label="${1:-}"; shift || true
    reason="$*"
    valid_label "$label"
    [[ -n "$reason" ]] || die "usage: scratch.sh keep <label> <reason>"
    d="$(safe_dir "$label")" || die "no scratch '$label' in session $SID"
    printf '%s (%s)\n' "$reason" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"$d/.keep"
    write_receipt "$label" "$(size_bytes "$d")" "$d" "keep"
    echo "kept: $d — $reason"
    ;;

  clean)
    label="${1:-}"; valid_label "$label"
    d="$(safe_dir "$label")" || die "no scratch '$label' in session $SID"
    bytes="$(size_bytes "$d")"
    rm -rf -- "$d" || die "delete failed for $d"
    write_receipt "$label" "$bytes" "$d" "clean"
    echo "cleaned: $d ($(human "$bytes")). Receipt: $RECEIPTS/$(date -u +%Y%m).tsv"
    rmdir -- "$BASE" 2>/dev/null || true
    ;;

  clean-all)
    include_kept=0; [[ "${1:-}" == "--include-kept" ]] && include_kept=1
    [[ -d "$BASE" ]] || { echo "no scratch for session $SID"; exit 0; }
    total=0
    for d in "$BASE"/*/; do
      [[ -d "$d" ]] || continue
      label="$(basename "$d")"
      if [[ -f "$d/.keep" ]] && (( ! include_kept )); then echo "skipped (kept): $label"; continue; fi
      d="$(safe_dir "$label")" || continue
      bytes="$(size_bytes "$d")"; total=$((total + bytes))
      rm -rf -- "$d" && write_receipt "$label" "$bytes" "$d" "clean"
      echo "cleaned: $label ($(human "$bytes"))"
    done
    rmdir -- "$BASE" 2>/dev/null || true
    echo "total freed: $(human "$total")"
    ;;

  clean-path)
    clean_path "$@"; exit $?
    ;;

  report)
    [[ -d "$ROOT" ]] || { echo "scratch root $ROOT does not exist"; exit 0; }
    echo "scratch root: $ROOT  total: $(human "$(size_bytes "$ROOT")")"
    printf '%-40s %-28s %10s %s\n' SESSION LABEL SIZE STATUS
    for s in "$ROOT"/*/; do
      [[ -d "$s" ]] || continue
      for d in "$s"*/; do
        [[ -d "$d" ]] || continue
        st="temporary"; [[ -f "$d/.keep" ]] && st="kept"
        printf '%-40s %-28s %10s %s\n' "$(basename "$s")" "$(basename "$d")" "$(human "$(size_bytes "$d")")" "$st"
      done
    done
    ;;

  *)
    sed -n '2,23p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 2
    ;;
esac
