#!/usr/bin/env bash
# PreToolUse guard for file-writing tools (Write, Edit, MultiEdit, NotebookEdit).
#
#   immutable   -> deny
#   appendonly  -> Write of a NEW file allowed; Edit, or Write over an existing file, denied
#   production  -> ask the owner
#   .git/ internals -> deny
#   secret files (.env, private keys) -> ask

set -u
# shellcheck source=lib/common.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

read_hook_input
if [[ "$(json_parser)" == "none" ]]; then
  printf '%s\n' "[ailab-engineering] BLOCKED: no JSON parser available (jq, node, python3). Fail-closed." >&2
  exit 2
fi

TOOL="$(json_get .tool_name)"
FILE="$(json_get .tool_input.file_path)"
[[ -z "$FILE" ]] && FILE="$(json_get .tool_input.notebook_path)"
[[ -z "$FILE" ]] && exit 0
CWD="$(json_get .cwd)"
CWD="${CWD:-$PWD}"
load_protected_paths

P="$(abs_path "$FILE" "$CWD")"
deny() { emit_deny guard-paths "$1" "$TOOL $P"; }
ask()  { emit_ask  guard-paths "$1" "$TOOL $P"; }

case "$P" in
  */.git/*|*/.git) deny "editing git internals ($P). Use git commands." ;;
esac

case "$(classify_path "$P")" in
  immutable)
    deny "$TOOL on immutable path $(matched_prefix "$P") (backups / frozen evidence / frozen runner)."
    ;;
  appendonly)
    if [[ "$TOOL" == "Write" && ! -e "$P" ]]; then
      exit 0   # new evidence file: allowed
    fi
    deny "$TOOL on existing evidence file '$P'. Evidence is append-only: write a NEW timestamped file next to it instead of changing this one. If this tree is a working copy, not evidence, do not retry: ask the owner to declare it as 'scratch <dir>' in $AILAB_USER_CONFIG."
    ;;
  production)
    ask "$TOOL on production path $(matched_prefix "$P"). Production changes need explicit owner approval and a rollback path."
    ;;
esac

base="$(basename -- "$P")"
case "$base" in
  .env.example|.env.sample|.env.template|.env.dist) ;;
  .env|.env.*|*.env|id_rsa|id_ed25519|id_ecdsa|*.pem|*.key)
    ask "$TOOL on a secrets file ($base). Never write real credentials from the transcript; confirm this is intended."
    ;;
esac

exit 0
