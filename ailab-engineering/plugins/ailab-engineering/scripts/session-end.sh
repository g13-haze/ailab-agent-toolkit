#!/usr/bin/env bash
# SessionEnd hook: record leftover scratch of the ending session so A.I. Lab Ops
# (or a human) can see what a session left behind. Never deletes scratch; drops
# the session's owner authorizations (0.2.2), which end with the session.

set -u
# shellcheck source=lib/common.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

read_hook_input
[[ "$(json_parser)" == "none" ]] && exit 0
SID="$(json_get .session_id)"
[[ -z "$SID" ]] && exit 0
GF="$(grant_file "$SID")" && rm -f -- "$GF"
BASE="${AILAB_SCRATCH_ROOT%/}/$SID"
[[ -d "$BASE" ]] || exit 0

mkdir -p "$AILAB_DATA_DIR" 2>/dev/null || exit 0
reason="$(json_get .reason)"
for d in "$BASE"/*/; do
  [[ -d "$d" ]] || continue
  st="temporary"; [[ -f "$d/.keep" ]] && st="kept"
  bytes="$(du -sb -- "$d" 2>/dev/null | cut -f1)"
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$SID" "${reason:-?}" "$(basename "$d")" "${bytes:-0}" "$st" \
    >>"$AILAB_DATA_DIR/leftovers.tsv" 2>/dev/null || true
done
exit 0
