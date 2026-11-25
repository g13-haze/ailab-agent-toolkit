#!/usr/bin/env bash
# Stop hook: remind Claude ONCE about task scratch that is neither cleaned nor
# explicitly kept. It blocks the stop with a reason only when the set of
# unattended scratch labels changed since the last reminder, so it cannot loop
# and does not nag on every turn.

set -u
# shellcheck source=lib/common.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

read_hook_input
[[ "$(json_parser)" == "none" ]] && exit 0
[[ "$(json_get .stop_hook_active)" == "true" ]] && exit 0

SID="$(json_get .session_id)"
[[ -z "$SID" ]] && exit 0
BASE="${AILAB_SCRATCH_ROOT%/}/$SID"
[[ -d "$BASE" ]] || exit 0

pending=()
details=""
for d in "$BASE"/*/; do
  [[ -d "$d" ]] || continue
  [[ -f "$d/.keep" ]] && continue
  label="$(basename "$d")"
  pending+=("$label")
  size="$(du -sh -- "$d" 2>/dev/null | cut -f1)"
  details+="${label} (${size:-?}), "
done
((${#pending[@]})) || exit 0

STATE_DIR="$AILAB_DATA_DIR/state"
umask 077
mkdir -p "$STATE_DIR" 2>/dev/null || exit 0      # cannot remember -> never nag every turn
seen_file="$STATE_DIR/${SID//[^A-Za-z0-9_-]/_}.reminded"
new=0
for l in "${pending[@]}"; do
  grep -qxF -- "$l" "$seen_file" 2>/dev/null || new=1
done
(( new )) || exit 0
printf '%s\n' "${pending[@]}" >>"$seen_file" 2>/dev/null || exit 0
sig="$(printf '%s,' "${pending[@]}")"

reason="Task scratch is still present: ${details%, }. Before you finish: if the work package is done and its evidence/results are saved outside scratch, run \"${AILAB_PLUGIN_ROOT}/scripts/scratch.sh\" clean <label> for each; if it is still needed (work in progress, owner review pending), run \"${AILAB_PLUGIN_ROOT}/scripts/scratch.sh\" keep <label> \"<why>\". Then report what was cleaned or kept in one line."
log_event stop-scratch remind "unattended scratch" "$sig"
printf '{"decision":"block","reason":"%s"}\n' "$(json_escape "$reason")"
exit 0
