#!/usr/bin/env bash
# UserPromptSubmit hook (0.2.2): task-scoped owner authorization.
#
# Only the owner's own prompt reaches this hook, so only the owner can grant;
# text the agent writes (WP files, reports, tool output) never does. A prompt
# line outside fenced code blocks:
#
#   ailab-authorize: <op>[,<op>...] [branch=<name>] [remote=<name>] [repo=<path>]
#   ailab-authorize: revoke
#
# <op>: commit | push | merge | memory. One grant per line, for this session
# only, bound to the repository (default: the prompt's working directory), the
# branch (default: its current branch), the active work package, and for push
# the remote (default: origin) and its push URL. guard-bash lets a plain
# `git push` / `git merge` covered by a grant run without asking; commit and
# memory grants tell the agent not to ask again. Never blocks a prompt.

set -u
umask 077
# shellcheck source=lib/common.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

read_hook_input
[[ "$HOOK_INPUT" == *ailab-authorize* ]] || exit 0     # fast path: almost every prompt
# Only Claude Code's hook runner sets CLAUDE_PLUGIN_DATA (Bash tool calls get
# AILAB_* instead, and guard-bash asks before a command sets CLAUDE_PLUGIN_*):
# run from a tool call, this script records nothing.
say() { printf '[ailab-engineering] %s\n' "$1"; }
[[ -n "${CLAUDE_PLUGIN_DATA:-}" ]] || { say "Owner authorization NOT recorded: not run by the Claude Code hook runner."; exit 0; }
[[ "$(json_parser)" == none ]] && { say "Owner authorization NOT recorded: no JSON parser (jq, node, python3)."; exit 0; }
[[ "$(json_get .hook_event_name)" == UserPromptSubmit ]] || exit 0

SID="$(json_get .session_id)"
CWD="$(json_get .cwd)"; CWD="${CWD:-$PWD}"
PROMPT="$(json_get .prompt)"
GF="$(grant_file "$SID")" || { say "Owner authorization NOT recorded: unusable session id."; exit 0; }
g() { with_timeout 4 git -c safe.directory='*' --no-optional-locks -C "$top" "$@" 2>/dev/null; }

# grant <directive text> : validate one directive and append it to the session's grants.
grant() {
  local w ops=() repo="" branch="" remote="" top wp url="" o words=() list=()
  read -ra words <<<"$1"
  for w in "${words[@]}"; do
    case "$w" in
      repo=?*)   repo="${w#repo=}" ;;
      branch=?*) branch="${w#branch=}" ;;
      remote=?*) remote="${w#remote=}" ;;
      *=*)       say "Owner authorization NOT recorded: unknown setting '$w' (repo=, branch=, remote=)."; return ;;
      *)
        IFS=, read -ra list <<<"$w"
        for o in "${list[@]}"; do
          case "$o" in
            "") ;;
            commit|push|merge|memory) [[ " ${ops[*]:-} " == *" $o "* ]] || ops+=("$o") ;;
            *) say "Owner authorization NOT recorded: unknown operation '$o' (commit, push, merge, memory)."; return ;;
          esac
        done ;;
    esac
  done
  ((${#ops[@]})) || { say "Owner authorization NOT recorded: name the operations (commit, push, merge, memory)."; return; }
  top="$(abs_path "${repo:-.}" "$CWD")"
  top="$(cd "$top" 2>/dev/null && with_timeout 4 git -c safe.directory='*' rev-parse --show-toplevel 2>/dev/null)" \
    || { say "Owner authorization NOT recorded: '${repo:-$CWD}' is not inside a git repository."; return; }
  top="$(abs_path "$top" /)"
  [[ "$top" != *[$'\t\n']* ]] || { say "Owner authorization NOT recorded: unsupported repository path."; return; }
  [[ -n "$branch" ]] || branch="$(g symbolic-ref --short -q HEAD)" \
    || { say "Owner authorization NOT recorded: $top has no current branch (detached HEAD); name it with branch=<name>."; return; }
  g check-ref-format --branch "$branch" >/dev/null && [[ "$branch" != *[[:space:]]* ]] \
    || { say "Owner authorization NOT recorded: '$branch' is not a valid branch name."; return; }
  if [[ " ${ops[*]} " == *" push "* ]]; then
    remote="${remote:-origin}"
    [[ "$remote" =~ ^[A-Za-z0-9._/-]+$ ]] && url="$(g remote get-url --push -- "$remote")" && [[ -n "$url" ]] \
      && url="$(printf '%s' "$url" | g hash-object --stdin)" && [[ -n "$url" ]] \
      || { say "Owner authorization NOT recorded: remote '$remote' is not configured in $top."; return; }
    # Only the hash of the push URL is kept: a URL can carry a token.
  else
    remote=""
  fi
  wp="-"
  [[ -r "$top/.claude/wp/ACTIVE" ]] && wp="$(head -1 "$top/.claude/wp/ACTIVE" | tr -d '[:space:]')"
  [[ -n "$wp" ]] || wp="-"
  mkdir -p "$AILAB_GRANTS_DIR" 2>/dev/null || { say "Owner authorization NOT recorded: cannot create $AILAB_GRANTS_DIR."; return; }
  o="$(IFS=,; printf '%s' "${ops[*]}")"
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$top" "$branch" "$wp" "$o" "$remote" "$url" >>"$GF" \
    || { say "Owner authorization NOT recorded: cannot write $GF."; return; }
  log_event authorize grant "$o $top $branch${remote:+ $remote} wp=$wp" ""
  say "Owner authorization recorded for this session and task ${wp/#-/(no active WP)}: $o in $top on branch $branch${remote:+ (push to remote $remote at its current URL)}. Do not ask the owner again for these. Anything else (force push, tags, other branches, remotes or repositories, rebase, gh, releases, deploys) still needs the owner."
}

fence=0 n=0
while IFS= read -r line || [[ -n "$line" ]]; do
  line="${line%$'\r'}"
  if [[ "$line" =~ ^[[:space:]]*(\`\`\`|~~~) ]]; then fence=$((1 - fence)); continue; fi
  (( fence )) && continue
  [[ "$line" =~ ^[[:space:]]*ailab-authorize:[[:space:]]*(.*)$ ]] || continue
  n=$((n + 1)); (( n > 10 )) && { say "Owner authorization: more than 10 directives; the rest were ignored."; break; }
  d="${BASH_REMATCH[1]}"; d="${d%"${d##*[![:space:]]}"}"
  if [[ "$d" == revoke ]]; then
    rm -f -- "$GF"
    log_event authorize revoke "all grants of this session" ""
    say "Owner authorizations of this session revoked: push and merge ask again; commit and memory need the owner's word again."
  else
    grant "$d"
  fi
done <<<"$PROMPT"
exit 0
