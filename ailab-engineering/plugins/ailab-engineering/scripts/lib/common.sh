#!/usr/bin/env bash
# shellcheck shell=bash
# Shared helpers for ailab-engineering hooks and scripts. Sourced, not executed.
#
# Design rules:
#   * No python dependency (agent containers may not have python3).
#   * JSON parsing: jq -> node -> python3, in that order. If none exists the
#     PreToolUse guards fail CLOSED (they deny every call, because they cannot
#     read it) and SessionStart warns.
#   * Nothing here prints secrets: logged commands are truncated and redacted.

# No core dumps from anything a hook or script starts: core_pattern is a
# relative "core", so a crashing git would overwrite <repo>/core.
ulimit -c 0 2>/dev/null || true

# Hook processes get CLAUDE_PLUGIN_*; Bash commands get AILAB_* from the
# SessionStart env file. The hook-provided values win when both exist.
AILAB_PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-${AILAB_PLUGIN_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}}"
AILAB_DATA_DIR="${CLAUDE_PLUGIN_DATA:-${AILAB_DATA_DIR:-${HOME:-/tmp}/.local/share/ailab-engineering}}"
AILAB_SCRATCH_ROOT="${AILAB_SCRATCH_ROOT:-/tmp/ailab-scratch}"
AILAB_USER_CONFIG="${AILAB_GUARD_CONFIG:-${HOME:-/nonexistent}/.config/ailab-engineering/protected-paths.conf}"
# Owner authorizations (0.2.2): one file per session, written only by the
# UserPromptSubmit hook (authorize.sh) from the owner's own prompt.
AILAB_GRANTS_DIR="$AILAB_DATA_DIR/grants"

HOOK_INPUT=""

# grant_file <session-id> : the session's grant file; fails for an unusable id.
grant_file() {
  [[ "${1:-}" =~ ^[A-Za-z0-9_-]+$ ]] || return 1
  printf '%s/%s.tsv' "$AILAB_GRANTS_DIR" "$1"
}

# with_timeout <seconds> <cmd...> : use coreutils timeout when present.
with_timeout() {
  local s="$1"; shift
  if command -v timeout >/dev/null 2>&1; then timeout "$s" "$@"; else "$@"; fi
}

# Read the hook payload from stdin once.
read_hook_input() {
  HOOK_INPUT="$(cat)"
}

# Which JSON parser is available (respects AILAB_JSON_PARSER for tests).
json_parser() {
  if [[ -n "${AILAB_JSON_PARSER:-}" ]]; then
    printf '%s' "$AILAB_JSON_PARSER"
    return
  fi
  if command -v jq >/dev/null 2>&1; then printf 'jq'
  elif command -v node >/dev/null 2>&1; then printf 'node'
  elif command -v python3 >/dev/null 2>&1; then printf 'python3'
  else printf 'none'
  fi
}

# json_get <dotted.path> : prints the value at the path in $HOOK_INPUT.
# Strings print raw; objects/arrays print as JSON; null/false/missing print nothing.
json_get() {
  local path="$1"
  case "$(json_parser)" in
    jq)
      printf '%s' "$HOOK_INPUT" | jq -r "${path} // empty | if type==\"string\" then . else tojson end" 2>/dev/null
      ;;
    node)
      printf '%s' "$HOOK_INPUT" | node -e '
        let s = "";
        process.stdin.on("data", d => s += d).on("end", () => {
          try {
            let v = JSON.parse(s);
            for (const k of process.argv[1].replace(/^\./, "").split(".")) {
              if (v === null || v === undefined) break;
              v = v[k];
            }
            if (v === null || v === undefined || v === false) return;
            process.stdout.write(typeof v === "string" ? v : JSON.stringify(v));
          } catch (e) {}
        });' "$path" 2>/dev/null
      ;;
    python3)
      printf '%s' "$HOOK_INPUT" | python3 -I -c '
import json, sys
try:
    v = json.load(sys.stdin)
    for k in sys.argv[1].lstrip(".").split("."):
        if v is None: break
        v = v.get(k) if isinstance(v, dict) else None
    if v is None or v is False: sys.exit(0)
    sys.stdout.write(v if isinstance(v, str) else json.dumps(v))
except Exception:
    pass' "$path" 2>/dev/null
      ;;
    *)
      return 3
      ;;
  esac
}

# Escape a string for embedding in a JSON string literal.
json_escape() {
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  s="${s//$'\n'/\\n}"
  s="${s//$'\r'/\\r}"
  s="${s//$'\t'/\\t}"
  # Strip remaining control characters.
  s="$(printf '%s' "$s" | tr -d '\000-\010\013\014\016-\037')"
  printf '%s' "$s"
}

# Redact obvious secrets and truncate for logs.
redact() {
  printf '%s' "$1" \
    | tr '\n\t' '  ' \
    | tr -d '\000-\037\177' \
    | sed -E \
        -e 's/([A-Za-z0-9_]*([Pp][Aa][Ss][Ss]|[Tt][Oo][Kk][Ee][Nn]|[Ss][Ee][Cc][Rr][Ee][Tt]|[Kk][Ee][Yy]|[Aa][Uu][Tt][Hh][A-Za-z]*)[A-Za-z0-9_]*["'"'"']?[[:space:]]*[=:][[:space:]]*)[^[:space:]]+/\1***/g' \
        -e 's/([Bb]earer )[A-Za-z0-9._~+\/=-]+/\1***/g' \
        -e 's#(://[^/:@[:space:]]+:)[^@[:space:]]+@#\1***@#g' \
        -e 's/(gh[pousr]_)[A-Za-z0-9]+/\1***/g' \
        -e 's/(sk-[A-Za-z0-9_-]{4})[A-Za-z0-9_-]+/\1***/g' \
        -e 's/(-p)[^[:space:]]{3,}/\1***/g' \
    | cut -c1-300
}

# log_event <component> <decision> <reason> <detail>
log_event() {
  local dir="$AILAB_DATA_DIR"
  umask 077
  mkdir -p "$dir" 2>/dev/null || return 0
  local ts session
  ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  session="$(json_get .session_id 2>/dev/null || true)"
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$ts" "${session:-?}" "$1" "$2" "$(redact "$3")" "$(redact "$4")" \
    >>"$dir/guard.log" 2>/dev/null || true
}

# Resolve a path to an absolute, normalized form without requiring it to exist.
# abs_path <path> <base-dir>
abs_path() {
  local p="$1" base="${2:-$PWD}"
  # Expand a leading ~ (the agent often writes ~/...). The tilde is compared
  # literally on purpose.
  local tilde='~'
  if [[ "$p" == "$tilde" ]]; then p="${HOME:-/root}"
  elif [[ "${p:0:2}" == "$tilde/" ]]; then p="${HOME:-/root}/${p:2}"
  fi
  [[ "$p" != /* ]] && p="$base/$p"
  if command -v realpath >/dev/null 2>&1; then
    realpath -m -- "$p" 2>/dev/null || printf '%s' "$p"
  else
    printf '%s' "$p"
  fi
}

# path_under <path> <prefix> : true if path equals prefix or is inside it.
path_under() {
  local p="${1%/}" pre="${2%/}"
  [[ -z "$pre" ]] && return 1
  [[ "$p" == "$pre" || "$p" == "$pre/"* ]]
}

# checkpoint_drift <repo-top> <state-file> : one line when the top of the project
# state file names "Checkpoint: <sha>" and commits exist after it; nothing otherwise.
checkpoint_drift() {
  local top="$1" cs="$2" cp n base last
  [[ -r "$cs" ]] || return 0
  cp="$(head -n 40 -- "$cs" 2>/dev/null | grep -m1 -oE '^[[:space:]]*[-*]?[[:space:]]*Checkpoint:[[:space:]]*[0-9a-f]{7,40}' | grep -oE '[0-9a-f]{7,40}$')"
  [[ -n "$cp" ]] || return 0
  gc() { with_timeout 4 git -c safe.directory='*' --no-optional-locks -C "$top" "$@" 2>/dev/null; }
  if ! base="$(gc rev-parse -q --verify "$cp^{commit}")"; then
    printf 'Checkpoint %s in %s is not in this history (rewritten, other branch or shallow clone).\n' "$cp" "${cs#"$top"/}"
    return 0
  fi
  # The commit that recorded the checkpoint (often the WP commit itself) is not drift:
  # count from the state file's last commit when it descends from the checkpoint.
  last="$(gc log -1 --format=%H -- "${cs#"$top"/}")"
  [[ -n "$last" && "$last" != "$base" ]] && gc merge-base --is-ancestor "$base" "$last" && base="$last"
  n="$(gc rev-list --count --no-merges "$base..HEAD")" || return 0
  [[ "$n" =~ ^[0-9]+$ ]] && (( n > 0 )) && printf 'Since checkpoint %s: %s commits not yet in project memory (owner catch-up: /ailab-engineering:change-intel catch-up).\n' "$cp" "$n"
  return 0
}

# Load protected path entries into the arrays PROT_CLASS[] and PROT_PATH[].
PROT_CLASS=()
PROT_PATH=()
load_protected_paths() {
  local f cls pth
  for f in "$AILAB_PLUGIN_ROOT/config/protected-paths.conf" "$AILAB_USER_CONFIG"; do
    [[ -r "$f" ]] || continue
    while read -r cls pth _; do
      [[ -z "$cls" || "$cls" == \#* ]] && continue
      case "$cls" in immutable|appendonly|production|scratch) ;; *) continue ;; esac
      [[ "$pth" == /* ]] || continue
      pth="${pth%/}"
      PROT_CLASS+=("$cls")
      PROT_PATH+=("$pth")
      # Also register the symlink-resolved form, because tool paths are resolved.
      if command -v realpath >/dev/null 2>&1; then
        local real
        real="$(realpath -m -- "$pth" 2>/dev/null || true)"
        if [[ -n "$real" && "$real" != "$pth" ]]; then
          PROT_CLASS+=("$cls")
          PROT_PATH+=("$real")
        fi
      fi
    done <"$f"
  done
  # The owner's config is where scratch areas are declared, so changing it
  # needs the owner (handled like production: ask).
  PROT_CLASS+=("production"); PROT_PATH+=("$AILAB_USER_CONFIG")
  if command -v realpath >/dev/null 2>&1; then
    local real_cfg
    real_cfg="$(realpath -m -- "$AILAB_USER_CONFIG" 2>/dev/null || true)"
    [[ -n "$real_cfg" && "$real_cfg" != "$AILAB_USER_CONFIG" ]] && { PROT_CLASS+=("production"); PROT_PATH+=("$real_cfg"); }
  fi
  # Owner authorizations are written only by the prompt hook, never by a tool call.
  PROT_CLASS+=("immutable"); PROT_PATH+=("${AILAB_GRANTS_DIR%/}")
  if command -v realpath >/dev/null 2>&1; then
    local real_gr
    real_gr="$(realpath -m -- "$AILAB_GRANTS_DIR" 2>/dev/null || true)"
    [[ -n "$real_gr" && "$real_gr" != "${AILAB_GRANTS_DIR%/}" ]] && { PROT_CLASS+=("immutable"); PROT_PATH+=("$real_gr"); }
  fi
  return 0
}

# in_scratch <abs-path> : true if the path is inside a designated scratch area
# (the plugin scratch root or a 'scratch' entry in the config).
in_scratch() {
  local p="$1" i
  path_under "$p" "$AILAB_SCRATCH_ROOT" && return 0
  for i in "${!PROT_PATH[@]}"; do
    [[ "${PROT_CLASS[$i]}" == scratch ]] && path_under "$p" "${PROT_PATH[$i]}" && return 0
  done
  return 1
}

# contains_protected <abs-path> : prints the first protected prefix located
# INSIDE the given path (deleting the parent would delete it).
contains_protected() {
  local p="${1%/}" i
  for i in "${!PROT_PATH[@]}"; do
    [[ "${PROT_CLASS[$i]}" == scratch ]] && continue
    if [[ "${PROT_PATH[$i]}" == "$p/"* || ( -z "$p" ) ]]; then
      printf '%s' "${PROT_PATH[$i]}"
      return 0
    fi
  done
  return 1
}

# classify_path <abs-path> : prints the most restrictive class that matches,
# or nothing. Order: immutable > appendonly > production. A designated
# scratch area is never protected.
classify_path() {
  local p="$1" i best=""
  in_scratch "$p" && return 0
  for i in "${!PROT_PATH[@]}"; do
    if path_under "$p" "${PROT_PATH[$i]}"; then
      case "${PROT_CLASS[$i]}" in
        immutable) best="immutable" ;;
        appendonly) [[ "$best" != "immutable" ]] && best="appendonly" ;;
        production) [[ -z "$best" ]] && best="production" ;;
      esac
    fi
  done
  printf '%s' "$best"
}

# matched_prefix <abs-path> : prints the protected prefix that matched.
matched_prefix() {
  local p="$1" i
  for i in "${!PROT_PATH[@]}"; do
    [[ "${PROT_CLASS[$i]}" == scratch ]] && continue
    if path_under "$p" "${PROT_PATH[$i]}"; then
      printf '%s' "${PROT_PATH[$i]}"
      return
    fi
  done
}

# --- PreToolUse decisions ------------------------------------------------------
# deny: exit 2, reason on stderr (fed back to Claude).
emit_deny() {
  local component="$1" reason="$2" detail="$3"
  log_event "$component" "deny" "$reason" "$detail"
  printf '[ailab-engineering] BLOCKED: %s\n' "$reason" >&2
  printf 'If this action is genuinely required, stop and ask the owner to run it manually or to approve an exception. Do not retry with a reworded command.\n' >&2
  exit 2
}

# ask: JSON permissionDecision "ask" so the owner sees a prompt.
emit_ask() {
  local component="$1" reason="$2" detail="$3"
  log_event "$component" "ask" "$reason" "$detail"
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"%s"}}\n' \
    "$(json_escape "[ailab-engineering] $reason")"
  exit 0
}
