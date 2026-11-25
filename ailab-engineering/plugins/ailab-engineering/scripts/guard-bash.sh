#!/usr/bin/env bash
# PreToolUse guard for the Bash tool.
#
# Decisions:
#   deny (exit 2)  - irreversible or explicitly forbidden by A.I. Lab rules
#   ask  (JSON)    - legitimate but needs the owner's eyes (push, restarts, migrations ...)
#   allow (exit 0) - everything else; the normal permission flow still applies
#
# This is a guardrail, not a sandbox: a deliberately obfuscated command can get
# past it. It exists to stop the common, accidental, expensive mistakes, while
# staying quiet on normal development commands (false positives teach agents to
# work around hooks).

set -u
umask 077
# shellcheck source=lib/common.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

read_hook_input
if [[ "$(json_parser)" == "none" ]]; then
  printf '%s\n' "[ailab-engineering] BLOCKED: no JSON parser available (jq, node, python3). Fail-closed." >&2
  exit 2
fi

CMD="$(json_get .tool_input.command)"
[[ -z "$CMD" ]] && exit 0
SID="$(json_get .session_id)"
CWD="$(json_get .cwd)"
CWD="${CWD:-$PWD}"
[[ -d "$CWD" ]] && CWD="$(cd "$CWD" 2>/dev/null && pwd -P || printf '%s' "$CWD")"
load_protected_paths

ASKS=() REDIRECTS=()
ask_later() { ASKS+=("$1"); }
deny()      { emit_deny guard-bash "$1" "$CMD"; }

# ---------------------------------------------------------------------------
# 0. Normalize
#    - drop heredoc bodies (file contents and commit messages are data, not commands)
#    - blank quoted commit messages and echo/printf strings
#    - flatten to one line for the regex rules
# ---------------------------------------------------------------------------
strip_heredocs() {
  local line delim="" tabs=0 out="" cmp
  local re='(^|[^<])<<(-?)[[:space:]]*["'"'"']?([A-Za-z_][A-Za-z0-9_]*)["'"'"']?'
  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ -n "$delim" ]]; then
      cmp="$line"
      (( tabs )) && cmp="${cmp#"${cmp%%[!$'\t']*}"}"
      [[ "$cmp" == "$delim" ]] && delim=""
      continue
    fi
    if [[ "$line" =~ $re ]]; then
      delim="${BASH_REMATCH[3]}"
      [[ -n "${BASH_REMATCH[2]}" ]] && tabs=1 || tabs=0
    fi
    out+="$line"$'\n'
  done <<<"$1"
  printf '%s' "$out"
}

# join_continuations : a line ending in an odd number of backslashes continues on
# the next line (an even number is a literal backslash and ends the line).
join_continuations() {
  local line out="" pend="" bs
  while IFS= read -r line || [[ -n "$line" ]]; do
    bs="${line##*[!\\]}"; [[ "$line" =~ ^\\+$ ]] && bs="$line"
    if (( ${#bs} % 2 == 1 )); then pend+="${line%\\} "; continue; fi
    out+="$pend$line"$'\n'; pend=""
  done <<<"$1"
  [[ -n "$pend" ]] && out+="$pend"$'\n'
  printf '%s' "$out"
}

# strip_comments (0.2.2) : drop shell comments ("ls  # then git push") so a
# command named in a comment is not a command. Quote state is tracked across
# lines; a comment ends at the newline even after a backslash, so this runs
# before join_continuations. Whenever the quoting cannot be followed reliably
# (command substitution, ${...}, backticks, $'...', unbalanced quotes, more than 4,000 characters)
# the text is returned unchanged and the strict view applies as before.
strip_comments() {
  local s="$1" out="" c q="" prev=$'\n' i n LC_ALL=C
  n=${#s}
  if [[ "$s" != *'#'* || "$s" == *'`'* || "$s" == *'$('* || "$s" == *'${'* || "$s" == *"\$'"* ]] || (( n > 4000 )); then
    printf '%s' "$s"; return
  fi
  for (( i = 0; i < n; i++ )); do
    c="${s:i:1}"
    if [[ -n "$q" ]]; then
      if [[ "$c" == "$q" ]]; then q=""
      elif [[ "$q" == '"' && "$c" == '\' ]]; then out+="$c"; i=$((i + 1)); c="${s:i:1}"
      fi
    else
      case "$c" in
        '\') out+="$c"; i=$((i + 1)); out+="${s:i:1}"; prev=x; continue ;;
        "'"|'"') q="$c" ;;
        '#')
          if [[ "$prev" == [[:space:]\;\&\|\(\)] ]]; then
            while (( i + 1 < n )) && [[ "${s:i+1:1}" != $'\n' ]]; do i=$((i + 1)); done
            continue
          fi ;;
      esac
    fi
    out+="$c"; prev="$c"
  done
  [[ -z "$q" ]] || { printf '%s' "$s"; return; }
  printf '%s' "$out"
}

SAN="$(join_continuations "$(strip_comments "$(strip_heredocs "$CMD")")")"
FLAT="$(printf '%s' "$SAN" | tr '\n' ';' | tr -s ' \t' '  ' | sed -E \
  -e "s/(^|[[:space:]])(-m|--message|-F|--file)(=|[[:space:]]+)\"([^\"\$\`]|\\\$[^(\"\`])*\"/\1\2 \"MSG\"/g" \
  -e "s/(^|[[:space:]])(-m|--message|-F|--file)(=|[[:space:]]+)'[^']*'/\1\2 'MSG'/g" \
  -e "s/((^|[;&|(])[[:space:]]*(echo|printf)([[:space:]]+-[a-zA-Z]+)*[[:space:]]+)(\"([^\"\$\`]|\\\$[^(\"\`])*\"|'[^']*')/\1STR/g")"

B='(^|[[:space:];&|(`!{"'"'"'])'          # start-of-command boundary (also inside bash -c "...")
E='([[:space:];&|)`"'"'"']|$)'            # end-of-token boundary
E_BARE='[[:space:]]*([;&|)`"'"'"']|$)'    # nothing follows
GIT="${B}([^[:space:];&|]*/)?git([[:space:]]+(-C|-c|--git-dir|--work-tree)([[:space:]]+|=)[^[:space:];&|]+|[[:space:]]+--no-pager)*[[:space:]]+"
DOCKER="${B}(sudo[[:space:]]+)?([^[:space:];&|]*/)?docker"
Q='["'"'"']?'                             # optional quote around a pathspec

has()  { printf '%s' "$FLAT" | grep -Eq -- "$1"; }
hasi() { printf '%s' "$FLAT" | grep -Eiq -- "$1"; }

# PUB (0.2.0): a view of the command in which quoted multi-word strings are data
# (grep patterns, PR titles and bodies, commit messages) and are blanked, unless
# they are the script of `sh -c` / `eval` or contain a command substitution,
# which executes. Single quoted words are unquoted, so `"gh" pr merge` is still seen.
PUB="$(printf '%s' "$FLAT" | sed -E \
  -e "s/\"([^\"[:space:];&|\`]+)\"/\1/g" \
  -e "s/'([^'[:space:];&|]+)'/\1/g" \
  -e "s/((^|[[:space:]])(-[a-zA-Z]*c|eval)[[:space:]]+)\"([^\"]*)\"/\1\x01\4\x01/g" \
  -e "s/((^|[[:space:]])(-[a-zA-Z]*c|eval)[[:space:]]+)'([^']*)'/\1\x02\4\x02/g" \
  -e "s/\"([^\"\$\`]|\\\$[^(\"\`])*\"/STR/g" \
  -e "s/'[^']*'/STR/g" \
  -e "s/\x01/\"/g" -e "s/\x02/'/g")"
hasp() { printf '%s' "$PUB" | grep -Eq -- "$1"; }

# GV (0.2.2): the view for the git rules. Only the quoted arguments of read-only
# text tools are data, so `grep -n "git add -A" docs/` is not mass staging; every
# other quoted string stays visible exactly as in 0.2.1 (bash <<<"...",
# perl -e '...', ssh host "...", git -c alias.x='!...'). When the command could
# run such text (a shell, interpreter, xargs, env or exec anywhere in it, a
# ./script, dot-sourcing, eval, source, <<<, <(...), $(...), backticks: also
# `printf ... > r.sh; sh r.sh`), GV is the strict FLAT.
TEXT_TOOLS='(grep|egrep|fgrep|rg|echo|printf|git[[:space:]]+(log|shortlog))'
GV="$FLAT"
if ! has '<<<|<\(|\$\(|`' && ! has "(^|[;&|(])[[:space:]]*(\.[[:space:]]|\.{0,2}/)" \
   && ! has "${B}([^[:space:];&|]*/)?(sh|bash|zsh|dash|ksh|fish|xargs|env|exec|eval|source|python[0-9.]*|perl|ruby|node|php)([[:space:]]|$)"; then
  GV="$(printf '%s' "$FLAT" | sed -E -e ':a' \
    -e "s/((^|[;&|(])[[:space:]]*([^[:space:];&|]*\/)?${TEXT_TOOLS}([[:space:]][^;&|\"']*)?[[:space:]=])(\"[^\"\$\`\\\\]*\"|'[^']*')/\1STR/" -e 'ta')"
fi
hasg() { printf '%s' "$GV" | grep -Eq -- "$1"; }
# count_calls <push|merge> : every word "push" / "merge" in FLAT (quoted text and
# any git global option included), at least 1. Each must be a verified, granted
# call; an over-count only makes the guard ask.
count_calls() {
  local n
  n="$(printf '%s' "$FLAT" | tr -cs 'A-Za-z0-9_-' '\n' | grep -cx -- "$1")"
  (( n > 0 )) || n=1
  printf '%s' "$n"
}

# ---------------------------------------------------------------------------
# 1. Git: mass staging, discarding work, rewriting history
# ---------------------------------------------------------------------------
if hasg "${GIT}(add|stage)([[:space:]][^;&|]*)?[[:space:]]${Q}(-A|--all|-u|--update|\.|\./|:/|\*|-[a-zA-Z]*A[a-zA-Z]*)${Q}${E}"; then
  deny "mass staging (git add -A / . / -u / --all). Stage an explicit file list: git add -- <file1> <file2>, then show git diff --cached --stat."
fi
hasg "${B}xargs[^;&|]*[[:space:]]git[[:space:]]+(add|stage)${E}" && ask_later "staging files from a generated list (xargs git add)."
if hasg "${GIT}commit([[:space:]][^;&|]*)?[[:space:]](-[b-zA-Z]*a[a-zA-Z]*|--all)${E}"; then
  deny "git commit -a stages every tracked change. Stage explicit files first, then commit."
fi
if hasg "${GIT}clean(${E}|[[:space:]])" && ! hasg "${GIT}clean[^;&|]*[[:space:]](-n|--dry-run|-[a-zA-Z]*n[a-zA-Z]*)${E}"; then
  deny "git clean deletes untracked files (often owner work, evidence or untracked source). Only 'git clean -n' (dry run) is allowed."
fi
if hasg "${GIT}reset[^;&|]*[[:space:]]--hard${E}"; then
  deny "git reset --hard discards uncommitted work irreversibly."
fi
if hasg "${GIT}(checkout|restore)([[:space:]][^;&|]*)?[[:space:]](--[[:space:]]+)?${Q}(\.|:/|\*)${Q}${E}"; then
  # 'git restore --staged .' only unstages; allowed unless it also touches the worktree.
  if ! { hasg "${GIT}restore[^;&|]*[[:space:]](--staged|-S)${E}" && ! hasg "${GIT}restore[^;&|]*[[:space:]](--worktree|-W)${E}"; }; then
    deny "discarding all working-tree changes (checkout/restore .). Restore explicit files only, after confirming they are yours."
  fi
fi
if hasg "${GIT}push[^;&|]*[[:space:]](-[a-zA-Z]*f[a-zA-Z]*|--force|--mirror|\+[^[:space:];&|]+)${E}"; then
  deny "force push rewrites remote history. Use a new commit; --force-with-lease needs explicit owner approval."
fi
if hasg "${GIT}(filter-branch|filter-repo|replace)${E}" \
   || hasg "${GIT}reflog[[:space:]]+(expire|delete)" \
   || hasg "${GIT}gc[^;&|]*--prune" \
   || hasg "${GIT}update-ref[[:space:]]+-d" \
   || hasg "${GIT}stash[[:space:]]+clear${E}"; then
  deny "history-destroying git operation."
fi
if hasg "${GIT}show[^;&|]*:[^[:space:];&|]*\.env${E}"; then
  deny "printing an env/secret file from git history."
fi
# push and merge ask unless an owner grant covers every one of them (decided
# after the per-segment pass, which verifies each call; see git_granted).
PUSH_N=0 MERGE_N=0 PUSH_OK=0 MERGE_OK=0
if hasg "${GIT}push(${E}|[[:space:]])" && ! hasg "${GIT}push[^;&|]*[[:space:]](--dry-run|-n)${E}"; then
  PUSH_N="$(count_calls push)"
fi
if hasg "${GIT}(rebase|merge)(${E}|[[:space:]])" && ! hasg "${GIT}(rebase|merge)[[:space:]]+--(abort|quit)${E}"; then
  if hasg "${GIT}rebase(${E}|[[:space:]])"; then
    ask_later "git rebase/merge changes branch history; confirm this is the accepted merge step."
  else
    MERGE_N="$(count_calls merge)"
  fi
fi
hasg "${GIT}branch[^;&|]*[[:space:]](-D|--delete[[:space:]]+--force)${E}" && ask_later "force-deleting a branch."
hasg "${GIT}stash(${E_BARE}|[[:space:]]+(push|save|drop|pop|-u|--include-untracked|-a|--all)${E})" && ask_later "git stash moves or drops uncommitted work."
hasg "${GIT}(checkout|switch)[^;&|]*[[:space:]](-f|--force|--discard-changes)${E}" && ask_later "checkout --force discards local changes."
hasg "${GIT}worktree[[:space:]]+remove" && ask_later "removing a git worktree."
# Owner authorizations are written only by the prompt hook (authorize.sh).
if has "(AILAB_DATA_DIR|CLAUDE_PLUGIN_DATA)[}\"']*/+grants" || [[ "$FLAT" == *"${AILAB_GRANTS_DIR%/}"* ]] \
   || has "plugins/data/ailab-engineering[^/[:space:]]*/grants"; then
  deny "the owner-authorization store is written only from the owner's own prompt (a line 'ailab-authorize: ...'). Ask the owner instead."
fi
if has "(^|[;&|(]|then|do)[[:space:]]*([A-Za-z_][A-Za-z0-9_]*=[^[:space:];&|]*[[:space:]]+)*[\"']?[^[:space:];&|]*authorize\.sh" \
   || has "${B}(sudo[[:space:]]+)?([^[:space:];&|]*/)?(ba|da|z|k)?sh[[:space:]][^;&|]*authorize\.sh"; then
  deny "authorize.sh is the owner's prompt hook; a tool call never records owner authorization. Ask the owner instead."
fi
has "${B}([^[:space:];&|]*/)?claude[[:space:]]([^;&|]*[[:space:]])?(-r|-c|--resume|--continue)([[:space:]=]|$)" \
  && ask_later "resuming or continuing a Claude session from a tool call (its prompt would reach the owner-authorization hook)."

# ---------------------------------------------------------------------------
# 1b. Publishing and remote state (0.2.0): GitHub CLI, package and container
#     registries, deploy tools. Reads stay allowed; anything that can publish,
#     merge, deploy or change remote state asks the owner. One prefilter keeps
#     ordinary commands at the old cost. These rules read PUB (above).
# ---------------------------------------------------------------------------

# gh_decision <token>... : classify one gh invocation (tokens after "gh").
# Sets GH_DEC (allow | ask | deny) and GH_WHY. Allowlist: only subcommands
# known to read are allowed; unknown groups (user aliases, extensions) ask.
gh_decision() {
  local t=() a i=0 n g s j m="" fields=0 input=0 graphql=0 rest
  for a in "$@"; do a="${a#[\"\'\`(\\]}"; a="${a%[\"\'\`)]}"; [[ -n "$a" ]] && t+=("$a"); done
  n=${#t[@]}; GH_DEC=allow; GH_WHY=""
  (( n == 0 )) && return 0                          # bare "gh" prints help
  # auth first: there "-h" means --hostname, not help
  for (( j = 0; j < n; j++ )); do [[ "${t[j]}" == -* ]] || break; done
  if [[ "${t[j]:-}" == auth ]]; then
    s="${t[j+1]:-}"; rest=" ${t[*]:j+2} "
    if [[ "$s" == token ]] || { [[ "$s" == status ]] && [[ "$rest" =~ [[:space:]](-[a-zA-Z]*t[a-zA-Z]*|--show-token(=true)?)[[:space:]] ]]; }; then
      GH_DEC=deny; GH_WHY="gh auth $s prints a GitHub credential into the transcript. Check login state with 'gh auth status' (without --show-token)."
      return 0
    fi
    [[ "$s" == status ]] && return 0
    GH_DEC=ask; GH_WHY="gh auth $s changes GitHub credentials or scopes."; return 0
  fi
  # help only as the last of at most three tokens (gh -h, gh pr -h, gh pr merge --help)
  [[ "${t[n-1]}" == -h || "${t[n-1]}" == --help ]] && (( n <= 3 )) && return 0
  [[ "${t[0]}" == --version && n -eq 1 ]] && return 0
  while (( i < n )) && [[ "${t[i]}" == -* ]]; do
    case "${t[i]}" in -R|--repo|--hostname) i=$((i + 2)) ;; *) i=$((i + 1)) ;; esac
  done
  (( i < n )) || return 0
  g="${t[i]}"; s="${t[i+1]:-}"
  case "$g" in
    help|version|status|search|browse|completion) return 0 ;;
    pr)       case "$s" in view|list|ls|diff|checks|status|checkout|co) return 0 ;; esac ;;
    issue)    case "$s" in view|list|ls|status) return 0 ;; esac ;;
    release)  case "$s" in view|list|ls|download|verify|verify-asset) return 0 ;; esac ;;
    run)      case "$s" in view|list|ls|watch|download) return 0 ;; esac ;;
    workflow) case "$s" in view|list|ls) return 0 ;; esac ;;
    repo)
      case "$s" in view|list|ls|clone|set-default|gitignore|license) return 0 ;; esac
      [[ "$s" == deploy-key || "$s" == autolink ]] && [[ "${t[i+2]:-}" == list || "${t[i+2]:-}" == view ]] && return 0 ;;
    label|gist|secret|variable|cache|ruleset|org|extension|alias|gpg-key|ssh-key|project|attestation|config|codespace)
      case "$s" in list|ls|view|get|check|verify|field-list|item-list) return 0 ;; esac ;;
    api)
      for (( j = i + 1; j < n; j++ )); do
        case "${t[j]}" in
          --method) m="${t[j+1]:-}"; j=$((j + 1)) ;;
          --method=*) m="${t[j]#--method=}" ;;
          --input|--input=*) input=1 ;;
          --field|--field=*|--raw-field|--raw-field=*) fields=1 ;;
          graphql) graphql=1 ;;
          --*) ;;
          -*X) m="${t[j+1]:-}"; j=$((j + 1)); [[ "${t[j-1]}" == -*[fF]*X ]] && fields=1 ;;
          -*X?*) m="${t[j]#*X}"; [[ "${t[j]%%X*}" == *[fF]* ]] && fields=1 ;;
          -*[fF]*) fields=1 ;;
        esac
      done
      m="${m^^}"
      if (( graphql )); then
        # -F values are blanked by normalization and a query can come from a file or a
        # variable: only an inline -f query without "mutation", $ or backticks is a read.
        local raw="${CMD#*graphql}"
        if [[ "${CMD,,}" != *mutation* && "$raw" != *'$'* && "$raw" != *'`'* ]] && (( ! input )) \
           && [[ " ${t[*]} " != *" -F"* && " ${t[*]} " != *" --field"* ]]; then return 0; fi
        GH_DEC=ask; GH_WHY="gh api graphql with a mutation or an uninspectable query may change remote state."
        return 0
      fi
      if [[ -n "$m" ]]; then [[ "$m" == GET || "$m" == HEAD ]] && return 0
      elif (( ! fields && ! input )); then return 0
      fi
      GH_DEC=ask; GH_WHY="gh api ${m:-POST (implied by -f/-F/--input)} changes remote state."
      return 0 ;;
  esac
  GH_DEC=ask; GH_WHY="gh $g${s:+ $s} may publish, merge or change remote GitHub state (only known read-only gh subcommands run without the owner)."
}

# real_dry_run <segment> : the segment (comments removed) carries a real dry-run flag.
real_dry_run() {
  local seg="${1%%[[:space:]]#*}" tok
  for tok in $seg; do
    case "$tok" in --dry-run|--dry-run=true|--dry-run=client|--dry-run=server) return 0 ;; esac
  done
  return 1
}

# ask_publish <regex> <reason> : ask once if a matching command (per segment) is
# not a real dry run.
ask_publish() {
  local m
  while IFS= read -r m; do
    [[ -z "$m" ]] && continue
    real_dry_run "$m" && continue
    ask_later "$2"; return 0
  done < <(printf '%s' "$PUB" | grep -Eo -- "$1[^;&|]*" || true)
}

# first_word <skip-with-value-flags> <tokens...> : the first token that is not an
# option (or the value of one of the listed options).
first_word() {
  local vf=" $1 " t skip=0; shift
  for t in "$@"; do
    (( skip )) && { skip=0; continue; }
    if [[ "$t" == -* ]]; then [[ "$vf" == *" ${t%%=*} "* && "$t" != *=* ]] && skip=1; continue; fi
    printf '%s' "$t"; return 0
  done
  return 1
}

PUB_TOOLS='gh|npm|npx|pnpm|yarn|bun|bunx|docker|docker-compose|podman|buildah|skopeo|twine|poetry|uv|hatch|flit|pdm|maturin|cargo|gem|rake|dotnet|nuget|mvn|mvnw|gradle|gradlew|helm|kubectl|terraform|terragrunt|tofu|pulumi|cdk|sam|serverless|sls|vercel|netlify|fly|flyctl|firebase|wrangler|gcloud|gsutil|aws|lerna|changeset|semantic-release|setup\.py'
if hasp "${B}([^[:space:];&|]*/|\\\\)?(${PUB_TOOLS})([[:space:]]|$)"; then
  while IFS= read -r occ; do
    [[ "$occ" =~ (^|[^A-Za-z0-9_.-])gh([[:space:]]+(.*))?$ ]] || continue
    read -ra GT <<<"${BASH_REMATCH[3]:-}" || true
    gh_decision "${GT[@]}"
    case "$GH_DEC" in
      deny) deny "$GH_WHY" ;;
      ask)  ask_later "$GH_WHY" ;;
    esac
  done < <(printf '%s' "$PUB" | grep -Eo "${B}([^[:space:];&|]*/|\\\\)?gh([[:space:]][^;&|)\`(]*)?" || true)
  R="${B}([^[:space:];&|]*/|\\\\)?"
  G='([^;&|]*[[:space:]])?'                    # options between a tool and its subcommand
  ask_publish "${R}(npm|pnpm|bun|yarn)[[:space:]]${G}(publish|unpublish|deprecate)${E}" "publishing or retracting an npm package."
  ask_publish "${R}npm[[:space:]]${G}(dist-tag[[:space:]]+(add|rm)|owner[[:space:]]+(add|rm)|access[[:space:]]+(set|grant|revoke))${E}" "changing npm registry state."
  ask_publish "${R}(npx|bunx|pnpm|yarn)[[:space:]]${G}(changeset[[:space:]]+publish|lerna[[:space:]]+publish|semantic-release|np|gh-pages)${E}" "publishing a release with a release tool."
  ask_publish "${R}(changeset|lerna)[[:space:]]+publish${E}" "publishing a release with a release tool."
  ask_publish "${R}semantic-release${E}" "publishing a release with semantic-release."
  ask_publish "${R}(sudo[[:space:]]+)?(docker|podman|buildah)[[:space:]]${G}push${E}" "pushing a container image to a registry."
  ask_publish "${R}(sudo[[:space:]]+)?(docker|podman)[[:space:]]${G}(--push|(-o|--output)[= ][^;&|[:space:]]*(push=true|type=registry)|imagetools[[:space:]]+create)" "building and pushing a container image to a registry."
  ask_publish "${R}docker-compose[[:space:]]${G}push${E}" "pushing container images to a registry."
  ask_publish "${R}skopeo[[:space:]]${G}(copy|sync|delete)${E}" "copying or deleting images in a registry."
  ask_publish "${B}([^[:space:];&|]*/)?twine[[:space:]]${G}upload${E}" "uploading a Python package (twine)."
  ask_publish "${R}(poetry|uv|hatch|flit|pdm|maturin)[[:space:]]${G}publish${E}" "publishing a Python package."
  ask_publish "${R}setup\\.py[[:space:]]${G}upload${E}" "uploading a Python package (setup.py)."
  ask_publish "${R}cargo[[:space:]]${G}(publish|yank|owner|release)${E}" "publishing or changing a crate."
  ask_publish "${R}gem[[:space:]]${G}(push|yank|owner)${E}" "publishing a Ruby gem."
  ask_publish "${R}rake[[:space:]]${G}release${E}" "releasing a Ruby gem (rake release)."
  ask_publish "${R}(dotnet[[:space:]]+)?nuget[[:space:]]${G}(push|delete)${E}" "publishing a NuGet package."
  ask_publish "${R}(mvn|mvnw)[[:space:]]${G}(deploy|deploy:[a-z-]+|release:perform|jib:build)${E}" "deploying Maven artifacts or images."
  ask_publish "${R}(gradle|gradlew)[[:space:]]${G}[^;&|[:space:]]*publish[A-Za-z]*${E}" "running a Gradle publish task."
  ask_publish "${R}helm[[:space:]]${G}(install|upgrade|uninstall|delete|rollback|push)${E}" "Helm release or chart change."
  ask_publish "${R}(terraform|terragrunt|tofu)[[:space:]]${G}(apply|destroy|import|taint|untaint|state[[:space:]]+(rm|mv|push)|run-all[[:space:]]+(apply|destroy))${E}" "changing infrastructure (terraform)."
  ask_publish "${R}pulumi[[:space:]]${G}(up|update|destroy|import)${E}" "changing infrastructure (pulumi)."
  ask_publish "${R}(cdk|sam|serverless|sls)[[:space:]]${G}deploy${E}" "deploying infrastructure or functions."
  ask_publish "${R}aws[[:space:]]${G}cloudformation[[:space:]]+(deploy|create-stack|update-stack|delete-stack)${E}" "changing a CloudFormation stack."
  ask_publish "${R}(netlify|fly|flyctl|wrangler|vercel)[[:space:]]${G}(deploy|publish|--prod|promote|rollback)${E}" "deploying to a hosting provider."
  ask_publish "${R}firebase[[:space:]]${G}[a-z:]*deploy${E}" "deploying to Firebase."
  ask_publish "${R}vercel${E_BARE}" "deploying to Vercel (bare 'vercel' deploys)."
  ask_publish "${R}gcloud[[:space:]]${G}(builds[[:space:]]+submit)${E}" "building and pushing with gcloud."
  # gcloud ... deploy, except Cloud Deploy reads (gcloud deploy releases list)
  while IFS= read -r m; do
    [[ -z "$m" ]] && continue
    [[ " $m " =~ [[:space:]](list|describe|get)[[:space:]] ]] || real_dry_run "$m" || { ask_later "deploying with gcloud."; break; }
  done < <(printf '%s' "$PUB" | grep -Eo -- "${R}gcloud[[:space:]]${G}deploy${E}[^;&|]*" || true)
  # object storage: writes go to an s3:// or gs:// destination (the last operand); rm/rb/mb always ask
  while IFS= read -r m; do
    [[ -z "$m" ]] && continue
    read -ra W <<<"$m" || true
    last=""; for w in "${W[@]}"; do [[ "$w" == -* ]] || last="$w"; done
    [[ " $m " =~ [[:space:]](ls|presign|cat|du|stat|hash|head-object|get-object|list-objects|list-objects-v2)[[:space:]] ]] && continue
    if [[ " $m " =~ [[:space:]](rm|rb|mb|delete-object|delete-bucket|put-object|put-bucket-policy)[[:space:]] || "$last" == s3://* || "$last" == gs://* ]]; then
      real_dry_run "$m" || { ask_later "writing or deleting objects in cloud storage."; break; }
    fi
  done < <(printf '%s' "$PUB" | grep -Eo -- "${R}(aws[[:space:]]${G}(s3|s3api)[[:space:]]|gsutil[[:space:]]|gcloud[[:space:]]${G}storage[[:space:]])[^;&|]*" || true)
  # kubectl: the verb is the first word that is not an option or an option value
  while IFS= read -r m; do
    read -ra W <<<"${m#*kubectl}" || true
    v="$(first_word "-n --namespace --context --kubeconfig --cluster --user -s --server --token --as --as-group --request-timeout -v -l --selector -f --filename" "${W[@]}")" || continue
    case "$v" in
      apply|create|delete|replace|patch|edit|scale|set|label|annotate|drain|cordon|uncordon|taint|run|expose|autoscale)
        real_dry_run "$m" || { ask_later "changing Kubernetes cluster state (kubectl $v)."; break; } ;;
      rollout)
        [[ " $m " =~ [[:space:]](restart|undo|pause|resume)[[:space:]] ]] && { ask_later "changing Kubernetes cluster state (kubectl rollout)."; break; } ;;
    esac
  done < <(printf '%s' "$PUB" | grep -Eo -- "${R}kubectl([[:space:]][^;&|]*)?" || true)
fi

# ---------------------------------------------------------------------------
# 2. Docker / systemd / host
# ---------------------------------------------------------------------------
if has "${DOCKER}([[:space:]]+(system|volume|image|container|network|builder|buildx))?[[:space:]]+prune"; then
  deny "docker prune is global and can delete images/volumes of other A.I. Lab products. Remove specific objects by exact name after verifying ownership."
fi
if has "${DOCKER}([[:space:]]+compose|-compose)[^;&|]*[[:space:]]down[^;&|]*[[:space:]](-v|--volumes)${E}"; then
  deny "compose down -v deletes persistent volumes (databases)."
fi
if has "${DOCKER}[[:space:]]+volume[[:space:]]+rm"; then
  deny "docker volume rm deletes persistent state."
fi
has "${DOCKER}[[:space:]]+(stop|restart|kill|rm|rmi|pause|update|rename)(${E}|[[:space:]])" && ask_later "changing container state on a shared host."
has "${DOCKER}[[:space:]]+image[[:space:]]+rm" && ask_later "removing a docker image."
has "${DOCKER}([[:space:]]+compose|-compose)[^;&|]*[[:space:]](up|down|restart|stop|start|rm|create|kill)(${E}|[[:space:]])" && ask_later "docker compose lifecycle change on a shared host."
has "${DOCKER}([[:space:]]+compose|-compose)[^;&|]*[[:space:]]config(${E}|[[:space:]])" && ask_later "docker compose config prints interpolated secrets."
if has "${DOCKER}[[:space:]]+(container[[:space:]]+)?inspect(${E}|[[:space:]])" && ! has "${DOCKER}[[:space:]]+(container[[:space:]]+)?inspect[^;&|]*[[:space:]](-f|--format)([[:space:]=])"; then
  ask_later "docker inspect without --format prints container environment (secrets). Prefer: docker inspect --format '{{json .State.Health}}' <name>."
fi
has "${B}(sudo[[:space:]]+)?systemctl([[:space:]]+--[a-z-]+)*[[:space:]]+(reboot|poweroff|halt|kexec)${E}" && deny "host power operation."
has "${B}(sudo[[:space:]]+)?systemctl([[:space:]]+--[a-z-]+)*[[:space:]]+(start|stop|restart|reload|reload-or-restart|try-restart|enable|disable|mask|unmask|kill|daemon-reload|isolate|edit|set-property|revert)(${E}|[[:space:]])" \
  && ask_later "systemd change on a host that runs production services."
if has "(curl|wget)[^;&]*\|[[:space:]]*(sudo[[:space:]]+)?(ba|z|da)?sh${E}"; then
  deny "piping a download into a shell. Download, review, verify checksum, then run."
fi

# ---------------------------------------------------------------------------
# 3. Databases and migrations
# ---------------------------------------------------------------------------
if has "${B}(psql|sqlite3|mysql|mariadb|duckdb|pg_restore|redis-cli)(${E}|[[:space:]])"; then
  hasi "(drop[[:space:]]+(table|database|schema|index)|truncate[[:space:]]+(table[[:space:]]+)?[a-z_\"]|delete[[:space:]]+from[[:space:]]|flushall|flushdb)" \
    && ask_later "destructive SQL/database statement."
  has "${B}pg_restore[^;&|]*[[:space:]](-c|--clean)${E}" && ask_later "pg_restore --clean drops objects in the target database."
fi
has "${B}(alembic|flask[[:space:]]+db|python[0-9.]*[[:space:]]+-m[[:space:]]+alembic)[^;&|]*[[:space:]](upgrade|downgrade|stamp)${E}" \
  && ask_later "database migration: confirm target DB, fresh verified backup, and owner approval."
if has "${B}(prisma[[:space:]]+migrate[[:space:]]+reset|manage\.py[[:space:]]+(flush|reset_db))"; then
  deny "database reset."
fi
has "${B}sudo[[:space:]]" && ask_later "sudo command."
# scratch.sh trusts these for the session and scratch boundary; only the hooks set them.
if has "(^|[^A-Za-z0-9_])(AILAB_(SESSION_ID|SCRATCH_ROOT|GUARD_CONFIG|DATA_DIR|PLUGIN_ROOT)|CLAUDE_CODE_SESSION_ID|CLAUDE_PLUGIN_(ROOT|DATA))=" \
   || has "${B}unset[[:space:]][^;&|]*(AILAB_SESSION_ID|CLAUDE_CODE_SESSION_ID)" \
   || has '(^|[^A-Za-z0-9_${])CLAUDE_PLUGIN_(ROOT|DATA)([^A-Za-z0-9_]|$)'; then   # printf -v, read, export, declare ...
  ask_later "overriding the plugin's session or scratch identity (AILAB_*/CLAUDE_CODE_SESSION_ID/CLAUDE_PLUGIN_*)."
fi

# ---------------------------------------------------------------------------
# 4. Per-segment parsing (file-level rules)
# ---------------------------------------------------------------------------
EFF_CWD="$CWD"
GIT_TOP="$(cd "$CWD" 2>/dev/null && with_timeout 3 git -c safe.directory='*' rev-parse --show-toplevel 2>/dev/null || true)"
SCRATCH_ROOT="${AILAB_SCRATCH_ROOT%/}"

# Variables that get a literal path once, unconditionally, earlier in this
# command (W=/tmp/ailab-scratch/<session>/x; rm -rf -- "${W:?}") are resolved
# so the path itself can be checked. Anything the guard cannot prove stays
# unresolved and keeps the old decision; inherited variables are never trusted.
# W=$(mktemp -d <template>) counts only when the template lies in this
# session's own scratch (RTMP), and then only "${W:?}" resolves: a failed
# mktemp aborts the shell instead of deleting "/...".
declare -A RVAR=() RTMP=()
RESOLVE=1
has "(^|[;&|(])[[:space:]]*(eval|source|\.)[[:space:]]" && RESOLVE=0
has "${B}(declare|local|typeset)[[:space:]]+-[a-zA-Z]*n" && RESOLVE=0
TOPLEVEL=1; DQ=0; SQ=0; BQ=0; PD=0; BR=0
SAFE_VALUE='^[A-Za-z0-9_./@%+,:=-]*$'
SAFE_WORD='^[]A-Za-z0-9_./@%+,:=*?[-]*$'

# var_trusted <name> : exactly one binding in the whole command, not after
# && / || / | / &, never bound by for/read/export/... and not an array.
var_trusted() {
  local n="$1"
  (( $(printf '%s' "$FLAT" | grep -Eo "(^|[^A-Za-z0-9_])${n}(\+|:)?=" | wc -l) == 1 )) || return 1
  has "(&&|\|\||[|&])[[:space:]]*${n}=" && return 1
  has "${B}(for|select|read|readarray|mapfile|getopts|unset|local|declare|typeset|export|readonly|let)([[:space:]][^;&|]*)?[[:space:]]${n}([^A-Za-z0-9_]|$)" && return 1
  has "${B}printf[[:space:]][^;&|]*-v[[:space:]]*${n}([^A-Za-z0-9_]|$)" && return 1
  has "(^|[^A-Za-z0-9_])${n}\[" && return 1
  has "\(\([^)]*(^|[^A-Za-z0-9_])${n}([^A-Za-z0-9_]|$)" && return 1
  return 0
}

# resolve_word <word> : prints the word with $NAME, ${NAME} and ${NAME:?...}
# replaced by resolved values; fails on any other expansion.
resolve_word() {
  local w="$1" out="" lit
  local re1='^\$\{([A-Za-z_][A-Za-z0-9_]*)(:\?[^}]*)?\}' re2='^\$([A-Za-z_][A-Za-z0-9_]*)'
  while [[ -n "$w" ]]; do
    if [[ "$w" == \$* ]]; then
      [[ "$w" =~ $re1 || "$w" =~ $re2 ]] || return 1
      [[ -n "${RVAR[${BASH_REMATCH[1]}]+set}" ]] || return 1
      [[ -n "${RTMP[${BASH_REMATCH[1]}]+set}" && -z "${BASH_REMATCH[2]:-}" ]] && return 1
      out+="${RVAR[${BASH_REMATCH[1]}]}"; w="${w:${#BASH_REMATCH[0]}}"
    else
      lit="${w%%\$*}"; out+="$lit"; w="${w:${#lit}}"
    fi
  done
  out="${out//\"/}"
  [[ "$out" =~ $SAFE_WORD ]] || return 1
  printf '%s' "$out"
}

# note_assignments <segment> : record plain top-level assignment statements,
# then track quotes, backticks, parentheses and { } groups so assignments inside
# strings, $(...), subshells or groups are never trusted. if/for/while/case
# ends trust for the rest of the command; a closed { ...; } group does not.
note_assignments() {
  local seg="$1" tok n v r s all=1 toks=() clean=0 mk p
  local re_mk='^([A-Za-z_][A-Za-z0-9_]*)=("?)\$\(mktemp[[:space:]]+-d[[:space:]]+([^[:space:]()`;&|]+)\)("?)$'
  read -ra toks <<<"$seg" || true
  (( DQ % 2 == 0 && SQ % 2 == 0 && BQ % 2 == 0 && PD == 0 )) && clean=1
  # shellcheck disable=SC1010  # "esac" here is a literal token being matched, not syntax
  case "${toks[0]:-}" in
    if|then|elif|else|fi|for|while|until|do|done|case|esac|select|function) TOPLEVEL=0 ;;
    \{|\}) (( clean )) || TOPLEVEL=0 ;;      # group start / end at command position
    \{*) TOPLEVEL=0 ;;
  esac
  for tok in "${toks[@]:1}"; do
    case "$tok" in \{|*'()'*) TOPLEVEL=0 ;; esac
  done
  [[ "${toks[0]:-}" == *'()'* ]] && TOPLEVEL=0
  s="${seg#"${seg%%[![:space:]]*}"}"; s="${s%"${s##*[![:space:]]}"}"
  if (( RESOLVE && TOPLEVEL && clean && BR == 0 )) \
     && [[ "$s" =~ $re_mk && "${BASH_REMATCH[2]}" == "${BASH_REMATCH[4]}" ]]; then
    n="${BASH_REMATCH[1]}"; mk="${BASH_REMATCH[3]}"
    if var_trusted "$n"; then
      if [[ "$mk" == \'*\' && ${#mk} -ge 2 ]]; then r="${mk:1:${#mk}-2}"; else r="$(resolve_word "$mk")" || r=""; fi
      if [[ "$r" == /* && "$r" =~ $SAFE_VALUE ]]; then
        p="$(abs_path "$r" /)"
        [[ "${p##*/}" == *XXX ]] && own_scratch "$p" && { RVAR[$n]="$p"; RTMP[$n]=1; }
      fi
    fi
  fi
  if (( RESOLVE && TOPLEVEL && ${#toks[@]} && clean && BR == 0 )); then
    for tok in "${toks[@]}"; do [[ "$tok" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]] || all=0; done
    if (( all )); then
      for tok in "${toks[@]}"; do
        n="${tok%%=*}"; v="${tok#*=}"
        var_trusted "$n" || continue
        if [[ "$v" == \'*\' && ${#v} -ge 2 ]]; then r="${v:1:${#v}-2}"
        else r="$(resolve_word "$v")" || continue
        fi
        [[ "$r" =~ $SAFE_VALUE ]] && RVAR[$n]="$r"
      done
    fi
  fi
  s="${seg//[^\"]/}"; DQ=$((DQ + ${#s}))
  s="${seg//[^\']/}"; SQ=$((SQ + ${#s}))
  s="${seg//[^\`]/}"; BQ=$((BQ + ${#s}))
  s="${seg//[^(]/}"; PD=$((PD + ${#s}))
  s="${seg//[^)]/}"; PD=$((PD - ${#s}))
  (( PD < 0 )) && TOPLEVEL=0
  if (( clean )); then
    [[ "${toks[0]:-}" == "{" ]] && BR=$((BR + 1))
    [[ "${toks[0]:-}" == "}" ]] && BR=$((BR - 1))
  fi
  (( BR < 0 )) && TOPLEVEL=0
  return 0
}

# Scratch cleanup redirect (0.1.6). An unresolved recursive delete whose
# visible part points into this session's own scratch is denied with a
# recoverable message that names "scratch.sh clean-path", which validates and
# deletes the exact path at run time. A hint only: it never allows anything.
#
# hint_own <word> : the literal text and resolved variables before the first
# expansion the guard cannot resolve name a directory in this session's
# scratch ("${SCR:?}/applytest-$p" -> $SCR).
hint_own() {
  local w="$1" out="" lit d
  [[ "$w" == *\`* ]] && return 1
  local re1='^\$\{([A-Za-z_][A-Za-z0-9_]*)(:\?[^}]*)?\}' re2='^\$([A-Za-z_][A-Za-z0-9_]*)'
  while [[ -n "$w" ]]; do
    if [[ "$w" == \$* ]]; then
      [[ "$w" =~ $re1 || "$w" =~ $re2 ]] || break
      [[ -n "${RVAR[${BASH_REMATCH[1]}]+set}" ]] || break
      out+="${RVAR[${BASH_REMATCH[1]}]}"; w="${w:${#BASH_REMATCH[0]}}"
    else
      lit="${w%%\$*}"; out+="$lit"; w="${w:${#lit}}"
    fi
  done
  [[ "$w" == *..* || "$w" == *'$('* ]] && return 1   # the unresolved rest could leave
  out="${out//[\"\']/}"
  [[ "$out" == /*/* ]] || return 1
  d="$(abs_path "${out%/*}" /)"
  own_scratch "${d%/}/_"
}

# var_hint_own <word> : the word starts with an unresolved $NAME / ${NAME} /
# ${NAME:?}, NAME is bound before the use, and every binding of NAME in the
# command (loops rebind) is a hint into this session's scratch: NAME=<word>,
# NAME=$(mktemp -d <template>XXX), NAME=$(.../scratch.sh new|path <label>),
# for NAME in <words>.
var_hint_own() {
  local w="${1#[\"\']}" n b v x words=()
  local re1='^\$\{([A-Za-z_][A-Za-z0-9_]*)(:\?[^}]*)?\}' re2='^\$([A-Za-z_][A-Za-z0-9_]*)'
  local re_mk='^"?\$\(mktemp[[:space:]]+-d[[:space:]]+([^[:space:]()]+XXX["'"'"']?)\)"?$'
  local re_sh='^"?\$\([^)]*/scratch\.sh"?[[:space:]]+(new|path)[[:space:]]+["'"'"']?[A-Za-z0-9_][A-Za-z0-9._-]*["'"'"']?\)"?$'
  [[ "$w" =~ $re1 || "$w" =~ $re2 ]] || return 1
  n="${BASH_REMATCH[1]}"
  [[ -z "${RVAR[$n]+set}" && "${w:${#BASH_REMATCH[0]}}" != *..* ]] || return 1
  has "${B}(read|readarray|mapfile|getopts|select|let)([[:space:]][^;&|]*)?[[:space:]]${n}([^A-Za-z0-9_]|$)" && return 1
  has "${B}printf[[:space:]][^;&|]*-v[[:space:]]*${n}([^A-Za-z0-9_]|$)" && return 1
  has "(^|[^A-Za-z0-9_])${n}(\[|\+=)" && return 1
  has "\(\([^)]*(^|[^A-Za-z0-9_])${n}([^A-Za-z0-9_]|$)" && return 1
  has "${B}for[[:space:]]+${n}[[:space:]]*(;|do([[:space:]]|$))" && return 1
  while IFS= read -r b; do
    [[ -n "$b" ]] || continue
    v="${b#*"$n"=}"
    if [[ "$v" =~ $re_mk ]]; then hint_own "${BASH_REMATCH[1]}" || return 1
    elif [[ "$v" =~ $re_sh ]]; then :     # prints <scratch root>/<session>/<label>
    elif [[ "$v" != *'$('* ]]; then hint_own "$v" || return 1
    else return 1
    fi
  done < <(printf '%s' "$FLAT" | grep -Eo "(^|[^A-Za-z0-9_])${n}=(\"?\\\$\([^)]*\)\"?|[^[:space:];&|()]*)" || true)
  while IFS= read -r b; do
    [[ -n "$b" ]] || continue
    read -ra words <<<"${b#*[[:space:]]in[[:space:]]}" || true
    ((${#words[@]})) || return 1
    for x in "${words[@]}"; do hint_own "$x" || return 1; done
  done < <(printf '%s' "$FLAT" | grep -Eo "${B}for[[:space:]]+${n}[[:space:]]+in[[:space:]][^;&|]*" || true)
  printf '%s' "$SEEN" | grep -Eq "(^|[^A-Za-z0-9_])${n}=|${B}for[[:space:]]+${n}[[:space:]]+in[[:space:]]"
}

depth_of() { local p="${1%/}" s; [[ -z "$p" ]] && { echo 0; return; }; s="${p//[^\/]/}"; echo "${#s}"; }

is_git_internal() { [[ "$1" == */.git || "$1" == */.git/* ]]; }

# own_scratch <abs-path> : inside this session's plugin scratch, or strictly
# inside this session's Claude Code scratchpad
# (/tmp/claude-<uid>/<project>/<session-id>/scratchpad/...).
own_scratch() {
  local p="${1%/}"
  [[ "$SID" =~ ^[A-Za-z0-9_-]+$ ]] || return 1
  path_under "$p" "$SCRATCH_ROOT/$SID" && return 0
  [[ "$p" =~ ^/tmp/claude-[0-9]+/[^/]+/([^/]+)/scratchpad/. && "${BASH_REMATCH[1]}" == "$SID" ]]
}

# scratch_decision <abs-path> : allow | ask | deny | "" (not a scratch area)
scratch_decision() {
  local p="${1%/}" i
  own_scratch "$p" && { echo allow; return; }
  if path_under "$p" "$SCRATCH_ROOT"; then
    [[ "$p" == "$SCRATCH_ROOT" ]] && { echo deny; return; }
    echo ask
    return
  fi
  for i in "${!PROT_PATH[@]}"; do
    [[ "${PROT_CLASS[$i]}" == scratch ]] || continue
    if [[ "$p" == "${PROT_PATH[$i]}" ]]; then echo ask; return; fi
    path_under "$p" "${PROT_PATH[$i]}" && { echo allow; return; }
  done
  echo ""
}

# check_target <abs-path> <verb> : class policy for any mutating verb.
check_target() {
  local p="$1" verb="$2" cls inner sd
  sd="$(scratch_decision "$p")"
  case "$sd" in
    allow) return 0 ;;
    deny)  deny "$verb on the shared scratch root '$p' (other sessions' work lives there)." ;;
    ask)   ask_later "$verb on scratch that belongs to another session or on a scratch area root: '$p'."; return 0 ;;
  esac
  is_git_internal "$p" && deny "$verb on git internals '$p'. Use git commands."
  if inner="$(contains_protected "$p")"; then
    deny "$verb on '$p', which contains protected path $inner."
  fi
  cls="$(classify_path "$p")"
  case "$cls" in
    immutable)  deny "$verb on immutable path $(matched_prefix "$p") (backups / frozen evidence / frozen runner)." ;;
    appendonly) deny "$verb on append-only evidence path $(matched_prefix "$p"). Evidence is never edited, moved or deleted; write a new timestamped file instead. If this tree is a working copy, not evidence, ask the owner to declare it as scratch; do not retry." ;;
    production) ask_later "$verb on production path $(matched_prefix "$p")." ;;
  esac
}

# check_write_dest <abs-dest> <verb> <recursive 0|1> <sources...>
# For copy/move-like verbs: new files into append-only dirs are fine,
# overwriting existing evidence is not.
check_write_dest() {
  local dp="$1" verb="$2" rec="$3"; shift 3
  local cls s
  cls="$(classify_path "$dp")"
  if [[ "$cls" == appendonly ]]; then
    if [[ -d "$dp" ]]; then
      for s in "$@"; do
        [[ -e "$dp/$(basename -- "$s")" ]] && deny "$verb would overwrite existing evidence '$dp/$(basename -- "$s")'. Write a new timestamped file."
      done
      (( rec )) && ask_later "recursive $verb into evidence directory '$dp' (may overwrite existing files)."
    elif [[ -e "$dp" ]]; then
      deny "$verb would overwrite existing evidence file '$dp'. Write a new timestamped file."
    fi
  elif [[ -n "$cls" || -n "$(scratch_decision "$dp")" ]] || is_git_internal "$dp"; then
    check_target "$dp" "$verb into"
  fi
}

check_recursive_delete() {
  local p="$1" raw="$2" d sd
  sd="$(scratch_decision "$p")"
  case "$sd" in
    allow)
      # A git repository in a shared, owner-declared scratch area may hold
      # unpushed work; this session's own scratch stays free to recreate.
      if [[ -e "$p/.git" ]] && ! own_scratch "$p"; then
        ask_later "recursive delete of git repository '$p' inside a shared scratch area."
      fi
      return 0 ;;
    deny)  deny "recursive delete of the shared scratch root '$p'. Clean your own label with scratch.sh clean <label>." ;;
    ask)   ask_later "recursive delete of scratch that belongs to another session or of a scratch area root: '$p'."; return 0 ;;
  esac
  if [[ "$(basename -- "$p")" == ".git" ]] || is_git_internal "$p"; then
    deny "deleting git metadata '$p' destroys the repository history."
  fi
  check_target "$p" "recursive delete"
  if [[ -n "${HOME:-}" && "${p%/}" == "${HOME%/}" ]]; then
    deny "recursive delete of the home directory '$p'."
  fi
  d="$(depth_of "$p")"
  if (( d <= 1 )); then
    deny "recursive delete of top-level path '$p'."
  fi
  if (( d == 2 )); then
    if path_under "$p" /tmp; then
      [[ "$(basename "$p")" == claude-* ]] && deny "recursive delete of the shared agent scratch parent '$p'. Delete only your own task directory by exact path."
      ask_later "recursive delete of '$p'."
      return 0
    fi
    deny "recursive delete of shallow path '$p' (depth 2)."
  fi
  if path_under "$EFF_CWD" "$p"; then
    deny "recursive delete of the working directory or one of its parents '$p'."
  fi
  if [[ -n "$GIT_TOP" ]] && path_under "$GIT_TOP" "$p"; then
    deny "recursive delete of the repository root or one of its parents '$p'."
  fi
  if [[ -e "$p/.git" ]]; then
    deny "recursive delete of a git repository '$p'."
  fi
  path_under "$p" "$EFF_CWD" && return 0   # inside the working tree: build outputs, node_modules ...
  ask_later "recursive delete outside the current working tree: '$p' (raw: $raw)."
}

# Wildcard base directory of a glob argument.
glob_base() {
  local a="$1" base
  base="${a%%[\*\?\[]*}"
  if [[ "$base" == */* ]]; then base="${base%/*}"; [[ -z "$base" ]] && base="/"; else base="."; fi
  printf '%s' "$base"
}

check_redirects() { # <segment>
  local seg="$1" redir op target p cls
  [[ "$seg" == *'>'* ]] || return 0
  while IFS= read -r redir; do
    [[ -z "$redir" ]] && continue
    op=">"; [[ "$redir" == *'>>'* ]] && op=">>"
    target="${redir#*>}"; target="${target#>}"; target="${target#|}"
    target="${target#"${target%%[![:space:]]*}"}"
    target="${target#[\"\']}"; target="${target%[\"\']}"
    [[ -z "$target" || "$target" == /dev/* || "$target" == \&* || "$target" == *'$'* ]] && continue
    p="$(abs_path "$target" "$EFF_CWD")"
    is_git_internal "$p" && deny "redirecting output into git internals '$p'."
    cls="$(classify_path "$p")"
    case "$cls" in
      immutable)  deny "redirecting output into immutable path $(matched_prefix "$p")." ;;
      appendonly) [[ "$op" == ">" && -f "$p" ]] && deny "overwriting existing evidence file '$p' (use >> or a new file)." ;;
      production) ask_later "writing into production path $(matched_prefix "$p")." ;;
    esac
  done < <(printf '%s' "$seg" | grep -Eo '(^|[^0-9&>])(&?>>?\|?|[0-9]>>?\|?)[[:space:]]*[^[:space:];&|)<>]+' || true)
}

# check_env_read <verb> : deny printing env/secret files named in ARGS.
check_env_read() {
  local v="$1" a
  for a in "${ARGS[@]:-}"; do
    [[ -z "$a" || "$a" == *'$'* ]] && continue
    is_env_file "$a" || continue
    case "$v" in
      grep|egrep|rg)
        [[ "$flagstr" =~ [[:space:]]-[a-zA-Z]*[clLq] || "$flagstr" == *" --count "* || "$flagstr" == *" --files-with-matches "* || "$flagstr" == *" --quiet "* ]] && continue ;;
      cut)
        [[ "$flagstr" == *" -d="* && ( "$flagstr" == *" -f1 "* || "$seg" =~ -f[[:space:]]+1([[:space:]]|$) ) ]] && continue ;;
    esac
    deny "printing an env/secret file ('$a') exposes credentials in the transcript. List keys only: cut -d= -f1 '$a'  (or grep -c / grep -q to test presence)."
  done
}

is_env_file() { # <path>
  local b; b="$(basename -- "$1")"
  case "$1" in */tests/*|*/test/*|*/fixtures/*) return 1 ;; esac
  case "$b" in
    .env.example|.env.sample|.env.template|.env.dist|.env.test|.env.ci) return 1 ;;
    .env|.env.*|*.env) return 0 ;;
    id_rsa|id_ed25519|id_ecdsa|id_dsa) return 0 ;;
  esac
  return 1
}

# ---------------------------------------------------------------------------
# Owner authorization (0.2.2). A plain `git push` / `git merge` runs without the
# ask when an owner grant of this session (authorize.sh, from the owner's own
# prompt) covers it: same repository, branch, active work package and, for a
# push, the same remote and push URL (hash). Whatever the guard cannot verify
# keeps the ask: options outside a short list, expansions, git global options
# other than -C, any variable assignment, export/read/declare/printf -v, a named
# HOME/PATH/XDG_*/GIT_*/CDPATH, a cd it cannot follow, xargs, eval,
# source, env, sudo, functions, -c scripts, here-strings, push configuration
# that widens a plain push (mirror, followTags, submodules, remote.<r>.push), or
# a command that also changes branches, refs, config, remotes or submodules.
# ---------------------------------------------------------------------------
GRANTS=() CWD_SURE=1 GOP=""
if (( PUSH_N + MERGE_N )) && GF="$(grant_file "$SID")" && [[ -f "$GF" && ! -L "$GF" && -O "$GF" ]]; then
  mapfile -t GRANTS <"$GF"
  if has "git[[:space:]]([^;&|]*[[:space:]])?(checkout|switch|branch|config|remote|symbolic-ref|update-ref|worktree|submodule)([[:space:]]|$)" \
     || has "(^|[;&|(]|[[:space:]])[A-Za-z_][A-Za-z0-9_]*\+?=" \
     || has '(^|[^A-Za-z0-9_${])(HOME|PATH|CDPATH|XDG_[A-Z_]+|GIT_[A-Z_]+)([^A-Za-z0-9_]|$)' \
     || has "${B}(pushd|popd|eval|source|env|sudo|doas|exec|claude|export|read|readarray|mapfile|declare|typeset|local|unset|printf[[:space:]]+-v)(${E}|[[:space:]])" \
     || has "(^|[;&|(])[[:space:]]*\.[[:space:]]" || has '\(\)|<<<' \
     || has "(^|[[:space:]])-[a-zA-Z]*c[[:space:]]+[\"']"; then
    GRANTS=()
  fi
fi

# grant_covers <op> <top> <branch> <wp> [<remote> <url>]
grant_covers() {
  local ln ts gt gb gw go gr gu
  for ln in "${GRANTS[@]}"; do
    IFS=$'\t' read -r ts gt gb gw go gr gu <<<"$ln"
    [[ "$gt" == "$2" && "$gb" == "$3" && "$gw" == "$4" && ",$go," == *",$1,"* ]] || continue
    [[ "$1" != push || ( -n "$gu" && "$gr" == "${5:-}" && "$gu" == "${6:-}" ) ]] || continue
    return 0
  done
  return 1
}

# git_granted <git> <tokens...> : this segment is a push or merge that a grant
# covers. Sets GOP to push | merge.
git_granted() {
  local dir="$EFF_CWD" t sub="" top br wp rem url pd src dst skip=0 redir=0 pos=() args=() gg q
  GOP=""
  (( CWD_SURE && ${#GRANTS[@]} && ! XARGS )) || return 1
  for t in "$@"; do                         # a token split out of a quoted string
    q="${t//[^\"]/}"; (( ${#q} % 2 )) && return 1
    q="${t//[^\']/}"; (( ${#q} % 2 )) && return 1
  done
  shift
  for t in "$@"; do                         # output redirections are not arguments
    (( redir )) && { redir=0; continue; }
    [[ "$t" =~ ^[0-9]*(\>\>?|\>\&|\&\>\>?)$ ]] && { redir=1; continue; }
    [[ "$t" =~ ^([0-9]*\>\>?|[0-9]*\>\&|\&\>\>?)[^\<\>]+$ ]] && continue
    args+=("$t")
  done
  set -- "${args[@]}"
  unq() { local w="${1%[)\}]}"; w="${w#[\"\']}"; printf '%s' "${w%[\"\']}"; }
  while (( $# )); do
    t="$(unq "$1")"
    case "$t" in
      -C) (( $# >= 2 )) || return 1; t="$(unq "$2")"
          [[ -n "$t" && "$t" != *[\$\`\"\'\\]* ]] || return 1
          dir="$(abs_path "$t" "$dir")"; shift 2 ;;
      --no-pager) shift ;;
      -*) return 1 ;;                       # -c, --git-dir, --work-tree ...: not verifiable
      *) sub="$t"; shift; break ;;
    esac
  done
  case "$sub" in push|merge) ;; *) return 1 ;; esac
  for t in "$@"; do
    (( skip )) && { skip=0; continue; }     # the (blanked) merge message
    t="$(unq "$t")"
    [[ -n "$t" && "$t" != *[\$\`\\\"\'\(\)\{\}\<\>\*\?\[]* ]] || return 1
    case "$sub:$t" in
      push:-u|push:--set-upstream|push:-q|push:--quiet|push:-v|push:--verbose|push:--progress|push:--no-progress|push:--porcelain) ;;
      merge:--no-ff|merge:--ff|merge:--ff-only|merge:--no-edit|merge:--squash|merge:-q|merge:--quiet|merge:-v|merge:--verbose|merge:--stat|merge:--no-stat|merge:--log|merge:--no-log|merge:--continue|merge:--signoff) ;;
      merge:-m|merge:--message|merge:-F|merge:--file) skip=1 ;;
      *:-*) return 1 ;;
      *) pos+=("$t") ;;
    esac
  done
  (( skip )) && return 1
  top="$(cd "$dir" 2>/dev/null && with_timeout 3 git -c safe.directory='*' rev-parse --show-toplevel 2>/dev/null)" || return 1
  top="$(abs_path "$top" /)"
  gg() { with_timeout 3 git -c safe.directory='*' --no-optional-locks -C "$top" "$@" 2>/dev/null; }
  br="$(gg symbolic-ref --short -q HEAD)" || return 1
  wp="-"; [[ -r "$top/.claude/wp/ACTIVE" ]] && wp="$(head -1 "$top/.claude/wp/ACTIVE" | tr -d '[:space:]')"; [[ -n "$wp" ]] || wp="-"
  if [[ "$sub" == merge ]]; then              # merges into the current branch
    grant_covers merge "$top" "$br" "$wp" && GOP=merge && return 0
    return 1
  fi
  if (( ${#pos[@]} <= 1 )); then              # the current branch, as push.default says
    pd="$(gg config --get push.default)"
    case "$pd" in ""|simple|current) ;; *) return 1 ;; esac
    rem="${pos[0]:-$(gg for-each-ref --format='%(push:remotename)' "refs/heads/$br")}"; rem="${rem:-origin}"
    pos=("$rem" "$br")
  fi
  rem="${pos[0]}"
  [[ "$rem" =~ ^[A-Za-z0-9._/-]+$ ]] || return 1
  # configuration that makes a plain push publish more than the named branch
  [[ -z "$(gg config --get-all "remote.$rem.push")" ]] || return 1
  [[ "$(gg config --bool --get "remote.$rem.mirror")" != true ]] || return 1
  [[ "$(gg config --bool --get push.followTags)" != true ]] || return 1
  case "$(gg config --get push.recurseSubmodules)" in ""|no|false|check) ;; *) return 1 ;; esac
  [[ "$(gg config --bool --get submodule.recurse)" != true ]] || return 1
  url="$(gg remote get-url --push -- "$rem")" && [[ -n "$url" ]] || return 1
  url="$(printf '%s' "$url" | gg hash-object --stdin)" && [[ -n "$url" ]] || return 1
  for t in "${pos[@]:1}"; do                  # every refspec: <branch>[:<same branch>]
    src="${t%%:*}"; src="${src#refs/heads/}"; [[ "$src" == HEAD ]] && src="$br"
    if [[ "$t" == *:* ]]; then dst="${t#*:}"; dst="${dst#refs/heads/}"; else dst="$src"; fi
    [[ -n "$src" && "$src" == "$dst" ]] || return 1
    grant_covers push "$top" "$src" "$wp" "$rem" "$url" || return 1
  done
  GOP=push
}

SEGMENTS="$(printf '%s' "$FLAT" | sed -E -e 's/>\|/>/g' -e 's/&&/\n/g' -e 's/\|\|/\n/g' -e 's/;/\n/g' -e 's/\|/\n/g' -e 's/([^<>&0-9])&([^<>&]|$)/\1\n\2/g')"
nseg="$(printf '%s\n' "$SEGMENTS" | grep -c . || true)"
if (( nseg > 300 )); then
  emit_ask guard-bash "Owner confirmation needed: command has $nseg segments, too large to inspect reliably." "$CMD"
fi

SEEN=""   # segments up to the current one (bindings before a use)
while IFS= read -r seg; do
  SEEN+="$seg;"
  note_assignments "$seg"
  read -ra T <<<"$seg" || true
  ((${#T[@]})) || continue
  T[0]="${T[0]#[({]}"
  [[ -z "${T[0]}" ]] && T=("${T[@]:1}")
  ((${#T[@]})) || continue

  # Bare environment dumps (checked before wrapper stripping removes 'env').
  t0="${T[0]}"; (( ${#T[@]} > 1 )) && [[ "$t0" == sudo || "$t0" == doas ]] && T2=("${T[@]:1}") || T2=("${T[@]}")
  if [[ ${#T2[@]} -eq 1 && ( "${T2[0]}" == env || "${T2[0]}" == printenv ) ]] \
     || [[ ${#T2[@]} -eq 2 && ( "${T2[0]} ${T2[1]}" == "export -p" || "${T2[0]} ${T2[1]}" == "declare -x" || "${T2[0]} ${T2[1]}" == "declare -p" ) ]]; then
    deny "dumping the whole environment exposes secrets. Check one variable: [ -n \"\${VAR:-}\" ] && echo set"
  fi

  # Strip wrappers: sudo, env assignments, timeout, nice, nohup, xargs ...
  XARGS=0
  changed=1
  while ((${#T[@]})) && ((changed)); do
    changed=0
    case "${T[0]}" in
      then|do|else|elif|if|while|until|'!')   # 'for f in x; do rm -rf /srv; done' is still checked
        T=("${T[@]:1}"); changed=1 ;;
      sudo|doas|nohup|command|exec|time|builtin|stdbuf|env)
        T=("${T[@]:1}"); changed=1
        while ((${#T[@]})) && [[ "${T[0]}" == -* ]]; do T=("${T[@]:1}"); done ;;
      timeout)
        T=("${T[@]:1}"); changed=1
        while ((${#T[@]})) && [[ "${T[0]}" == -* ]]; do T=("${T[@]:1}"); done
        ((${#T[@]})) && T=("${T[@]:1}") ;;
      nice|ionice)
        T=("${T[@]:1}"); changed=1
        while ((${#T[@]})) && [[ "${T[0]}" == -* ]]; do
          if [[ "${T[0]}" == -n || "${T[0]}" == -c ]]; then T=("${T[@]:2}"); else T=("${T[@]:1}"); fi
        done ;;
      xargs)
        XARGS=1; T=("${T[@]:1}"); changed=1
        while ((${#T[@]})) && [[ "${T[0]}" == -* ]]; do T=("${T[@]:1}"); done ;;
      *intel.sh|*intel.sh\"|*intel.sh\'|*intel.py|*intel.py\"|*intel.py\')
        # intel.sh fbpa runs the command after "--" in copies under --workdir: check both.
        [[ "${T[1]:-}" == fbpa ]] || break
        fb_cmd=(); fb_seen=0; fb_prev=""
        for tok in "${T[@]:2}"; do
          if (( fb_seen )); then fb_cmd+=("$tok"); continue; fi
          [[ "$tok" == -- ]] && { fb_seen=1; continue; }
          if [[ "$fb_prev" == --workdir && "$tok" != *'$'* ]]; then
            tok="${tok#[\"\']}"; tok="${tok%[\"\']}"; check_target "$(abs_path "$tok" "$EFF_CWD")" "fbpa workdir"
          fi
          fb_prev="$tok"
        done
        T=("${fb_cmd[@]}"); changed=1 ;;
      *)
        if [[ "${T[0]}" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then T=("${T[@]:1}"); changed=1; fi ;;
    esac
  done
  ((${#T[@]})) || { check_redirects "$seg"; continue; }
  verb="${T[0]#[\"\']}"; verb="${verb%[\"\']}"; verb="${verb##*/}"

  # Flags and operands (quotes stripped per token, no subshells).
  FLAGS=(); ARGS=(); ddash=0
  for tok in "${T[@]:1}"; do
    tok="${tok%[)\}]}"; tok="${tok#[\"\']}"; tok="${tok%[\"\']}"
    [[ -z "$tok" ]] && continue
    if (( ! ddash )) && [[ "$tok" == "--" ]]; then ddash=1; continue; fi
    if (( ! ddash )) && [[ "$tok" == -* ]]; then FLAGS+=("$tok"); else ARGS+=("$tok"); fi
  done
  flagstr=" ${FLAGS[*]:-} "

  case "$verb" in
    cd)
      if ((${#ARGS[@]})) && [[ "${ARGS[0]}" != *'$'* ]]; then EFF_CWD="$(abs_path "${ARGS[0]}" "$EFF_CWD")"; else CWD_SURE=0; fi
      (( TOPLEVEL && PD == 0 && BR == 0 )) && [[ "$seg" != *[\(\)\{\}]* ]] || CWD_SURE=0 ;;   # in a block or subshell: not followed

    git)
      if (( PUSH_N + MERGE_N )) && git_granted "${T[@]}"; then
        [[ "$GOP" == push ]] && PUSH_OK=$((PUSH_OK + 1))
        [[ "$GOP" == merge ]] && MERGE_OK=$((MERGE_OK + 1))
      fi ;;

    shutdown|reboot|poweroff|halt|mkfs|mkfs.*|wipefs|fdisk|sfdisk|parted)
      deny "host power or disk-level destructive command ($verb)." ;;

    rm|unlink|shred)
      recursive=0
      [[ "$flagstr" =~ [[:space:]]-[a-zA-Z]*[rR] || "$flagstr" == *" --recursive "* ]] && recursive=1
      if (( XARGS )); then
        for i in "${!PROT_PATH[@]}"; do
          [[ "${PROT_CLASS[$i]}" == immutable || "${PROT_CLASS[$i]}" == appendonly ]] || continue
          [[ "$FLAT" == *"${PROT_PATH[$i]}"* ]] && deny "deleting files from a generated list that involves protected path ${PROT_PATH[$i]}."
        done
        case "$(classify_path "$EFF_CWD")" in immutable|appendonly) deny "deleting files from a generated list inside a protected directory." ;; esac
        ask_later "delete with targets from xargs (not inspectable)."
      fi
      for a in "${ARGS[@]:-}"; do
        [[ -z "$a" ]] && continue
        if [[ "$a" == *'$'* || "$a" == *'`'* ]]; then
          if (( RESOLVE )) && r="$(resolve_word "$a")"; then
            a="$r"; [[ -z "$a" ]] && continue
          else
            if (( recursive )); then
              if (( RESOLVE )) && { hint_own "$a" || var_hint_own "$a"; }; then
                REDIRECTS+=("$a")
              else
                ask_later "recursive delete of an unresolved path '$a' (assign it once earlier in the same command, outside if/for/while blocks: W=/abs/path, or W=\$(mktemp -d <your scratch>/name.XXXXXXXX) used as \"\${W:?}\"; then rm -rf -- \"\${W:?}\" can be checked)."
              fi
            fi
            continue
          fi
        fi
        if [[ "$a" == *[\*\?\[]* ]]; then
          p="$(abs_path "$(glob_base "$a")" "$EFF_CWD")"
          if (( recursive )); then
            sd="$(scratch_decision "$p")"
            case "$sd" in
              allow) continue ;;
              deny)  deny "recursive wildcard delete in the shared scratch root ('$a')." ;;
              ask)   ask_later "recursive wildcard delete in another session's scratch ('$a')."; continue ;;
            esac
            check_target "$p" "wildcard delete"
            d="$(depth_of "$p")"
            if (( d <= 1 )); then
              if [[ "$p" == /tmp ]] && [[ "$(basename -- "$a")" != claude-* ]]; then ask_later "recursive wildcard delete in /tmp ('$a')."; continue; fi
              deny "recursive wildcard delete near the filesystem root ('$a')."
            fi
            (( d == 2 )) && { path_under "$p" /tmp && { ask_later "recursive wildcard delete '$a'."; continue; }; deny "recursive wildcard delete in shallow path '$a'."; }
            if path_under "$p" "$EFF_CWD" && [[ "${p%/}" != "${EFF_CWD%/}" ]] && { [[ -z "$GIT_TOP" ]] || [[ "${p%/}" != "${GIT_TOP%/}" ]]; }; then
              continue   # e.g. rm -rf build/* inside the working tree
            fi
            ask_later "recursive wildcard delete '$a'."
          else
            check_target "$p" "wildcard delete"
          fi
          continue
        fi
        p="$(abs_path "$a" "$EFF_CWD")"
        if (( recursive )); then check_recursive_delete "$p" "$a"; else check_target "$p" "delete"; fi
      done ;;

    find)
      if [[ "$seg" =~ -delete || "$seg" =~ -exec(dir)?[[:space:]]+(sudo[[:space:]]+)?rm ]]; then
        roots=()
        for tok in "${T[@]:1}"; do
          tok="${tok#[\"\']}"; tok="${tok%[\"\']}"
          [[ "$tok" == -* || "$tok" == '(' || "$tok" == '!' ]] && break
          roots+=("$tok")
        done
        ((${#roots[@]})) || roots=(".")
        filtered=0; [[ "$seg" =~ [[:space:]]-(name|iname|path|ipath|regex|iregex)[[:space:]] ]] && filtered=1
        for r in "${roots[@]}"; do
          p="$(abs_path "$r" "$EFF_CWD")"
          [[ "$(scratch_decision "$p")" == allow ]] && continue
          check_target "$p" "find -delete"
          if (( filtered )) && path_under "$p" "$EFF_CWD"; then continue; fi
          ask_later "bulk delete via find in '$p'."
        done
      fi ;;

    mv)
      n=${#ARGS[@]}
      if (( n >= 2 )); then
        for a in "${ARGS[@]:0:n-1}"; do
          [[ -z "$a" || "$a" == *'$'* ]] && continue
          p="$(abs_path "$a" "$EFF_CWD")"
          [[ "$(basename -- "$p")" == ".git" ]] && deny "moving git metadata '$p'."
          check_target "$p" "move"
        done
        dest="${ARGS[n-1]}"
        [[ "$dest" != *'$'* ]] && check_write_dest "$(abs_path "$dest" "$EFF_CWD")" "mv" 0 "${ARGS[@]:0:n-1}"
      fi ;;

    cp|install|ln)
      n=${#ARGS[@]}
      if (( n >= 2 )); then
        dest="${ARGS[n-1]}"
        rec=0; [[ "$flagstr" =~ [[:space:]]-[a-zA-Z]*[rRa] || "$flagstr" == *" --recursive "* || "$flagstr" == *" --archive "* ]] && rec=1
        [[ "$dest" != *'$'* && "$dest" != *:* ]] && check_write_dest "$(abs_path "$dest" "$EFF_CWD")" "$verb" "$rec" "${ARGS[@]:0:n-1}"
      fi ;;

    rsync|scp)
      OPS=(); skipnext=0
      for a in "${ARGS[@]:-}"; do         # redirections are not operands
        (( skipnext )) && { skipnext=0; continue; }
        [[ "$a" =~ ^[0-9]*\>+\|?$|^\&\>+$ ]] && { skipnext=1; continue; }
        [[ "$a" =~ ^[0-9]*\>|^\&\> ]] && continue
        [[ -n "$a" ]] && OPS+=("$a")
      done
      ARGS=("${OPS[@]}")
      n=${#ARGS[@]}
      if (( n >= 2 )); then
        dest="${ARGS[n-1]}"
        # user@host:path, host:path ($HOST:path too) or rsync:// as the destination copies to another machine.
        [[ "$dest" == rsync://* || ( "$dest" =~ ^[^/]*: && "$dest" != .* ) ]] \
          && ask_later "$verb to a remote host ('$dest') publishes files outside this machine."
        if [[ "$dest" != *'$'* && "$dest" != *:* ]]; then
          dp="$(abs_path "$dest" "$EFF_CWD")"; cls="$(classify_path "$dp")"
          if [[ "$flagstr" == *"--delete"* ]] && [[ -n "$cls" ]]; then
            deny "rsync --delete into protected path $(matched_prefix "$dp")."
          fi
          if [[ "$cls" == appendonly ]]; then
            [[ "$flagstr" == *"--ignore-existing"* ]] || ask_later "rsync into evidence directory '$dp' may overwrite existing files (use --ignore-existing)."
          else
            check_write_dest "$dp" "$verb" 1 "${ARGS[@]:0:n-1}"
          fi
        fi
      fi ;;

    touch|truncate|gzip|gunzip|bzip2|xz|unxz|zstd)
      for a in "${ARGS[@]:-}"; do
        [[ -z "$a" || "$a" == *'$'* ]] && continue
        p="$(abs_path "$a" "$EFF_CWD")"
        if [[ "$verb" == touch && "$(classify_path "$p")" == appendonly && ! -e "$p" ]]; then continue; fi
        check_target "$p" "$verb"
      done ;;

    dd)
      for a in "${ARGS[@]:-}"; do
        [[ "$a" == of=* ]] || continue
        p="${a#of=}"; [[ "$p" == /dev/* ]] && deny "dd onto a device."
        check_target "$(abs_path "$p" "$EFF_CWD")" "dd write"
      done ;;

    tar)
      mode="${FLAGS[*]:-} ${ARGS[0]:-}"
      prev=""
      for tok in "${T[@]:1}"; do
        tok="${tok#[\"\']}"; tok="${tok%[\"\']}"
        if [[ "$prev" == "-C" || "$prev" == "--directory" ]] && [[ "$mode" =~ (^|[[:space:]])-?[a-zA-Z]*x|--extract ]]; then
          check_target "$(abs_path "$tok" "$EFF_CWD")" "tar extract into"
        fi
        if [[ "$prev" =~ ^-[a-zA-Z]*f$ || "$prev" == "--file" ]] && [[ "$mode" =~ (^|[[:space:]])-?[a-zA-Z]*[cruA]|--create|--append|--update ]]; then
          check_write_dest "$(abs_path "$tok" "$EFF_CWD")" "tar create" 0 "$tok"
        fi
        prev="$tok"
      done ;;

    chmod|chown|chgrp|setfacl)
      rec=0; [[ "$flagstr" =~ [[:space:]]-[a-zA-Z]*R || "$flagstr" == *" --recursive "* ]] && rec=1
      for a in "${ARGS[@]:-}"; do
        [[ -z "$a" || "$a" == *'$'* ]] && continue
        [[ "$a" != */* && "$a" != .* && ! -e "$(abs_path "$a" "$EFF_CWD")" ]] && continue  # mode/owner operand
        p="$(abs_path "$a" "$EFF_CWD")"
        check_target "$p" "$verb"
        if (( rec )); then
          d="$(depth_of "$p")"
          (( d <= 1 )) && deny "recursive $verb on top-level path '$p'. No mass chown/chmod."
          (( d == 2 )) && ask_later "recursive $verb on '$p'."
        fi
      done
      [[ "$seg" =~ (^|[[:space:]])0?777([[:space:]]|$) ]] && ask_later "chmod 777." ;;

    sed|perl)
      if [[ "$flagstr" =~ [[:space:]]-[a-zA-Z]*i || "$flagstr" == *" --in-place"* ]]; then
        for a in "${ARGS[@]:1}"; do
          [[ -z "$a" || "$a" == *'$'* ]] && continue
          check_target "$(abs_path "$a" "$EFF_CWD")" "in-place edit"
        done
      else
        check_env_read "$verb"
      fi ;;

    tee)
      append=0; [[ "$flagstr" == *" -a "* || "$flagstr" == *" --append "* ]] && append=1
      for a in "${ARGS[@]:-}"; do
        [[ -z "$a" || "$a" == *'$'* ]] && continue
        p="$(abs_path "$a" "$EFF_CWD")"; cls="$(classify_path "$p")"
        if [[ "$cls" == appendonly ]]; then
          (( append )) || { [[ -f "$p" ]] && deny "overwriting existing evidence file '$p' with tee (use tee -a or a new file)."; }
        elif [[ -n "$cls" ]] || is_git_internal "$p"; then
          check_target "$p" "write"
        fi
      done ;;

    sqlite3)
      for a in "${ARGS[@]:-}"; do
        [[ "$a" == *.db || "$a" == *.sqlite || "$a" == *.sqlite3 ]] || continue
        p="$(abs_path "$a" "$EFF_CWD")"; cls="$(classify_path "$p")"
        [[ -z "$cls" ]] && continue
        if [[ "$flagstr" != *" -readonly "* ]]; then
          if [[ "$cls" == production ]]; then
            ask_later "sqlite3 on production database '$p' without -readonly."
          else
            deny "sqlite3 on protected database '$p' without -readonly (even reads can write journals)."
          fi
        fi
      done ;;

    cat|less|more|head|tail|bat|nl|xxd|strings|od|tac|base64|awk|sort|uniq|diff|cut|tr|jq|grep|egrep|rg)
      check_env_read "$verb" ;;
  esac

  check_redirects "$seg"
done <<<"$SEGMENTS"

# Push and merge: ask unless every call was verified against an owner grant.
if (( PUSH_N )); then
  if (( PUSH_OK >= PUSH_N )); then log_event guard-bash grant "git push covered by an owner authorization" "$CMD"
  else ask_later "git push: publishing requires explicit owner authorization (the owner can authorize it for this task with a prompt line 'ailab-authorize: push [branch=<name>]')."
  fi
fi
if (( MERGE_N )); then
  if (( MERGE_OK >= MERGE_N )); then log_event guard-bash grant "git merge covered by an owner authorization" "$CMD"
  else ask_later "git rebase/merge changes branch history; confirm this is the accepted merge step (or the owner authorizes merges into a branch for this task: 'ailab-authorize: merge [branch=<target>]')."
  fi
fi

# ---------------------------------------------------------------------------
# Recoverable denial (exit 2, stderr goes to Claude, no owner prompt). It comes
# before the asks: the rest of the command is checked again on the retry.
if ((${#REDIRECTS[@]})); then
  joined="$(printf "'%s' " "${REDIRECTS[@]}")"
  log_event guard-bash redirect "scratch cleanup: ${joined% }" "$CMD"
  printf '[ailab-engineering] SCRATCH_CLEANUP_REDIRECT: Unresolvable recursive deletion of %s. If this is task-owned scratch, resolve the absolute target and use "%s/scripts/scratch.sh" clean-path [--missing-ok] <path>... instead (it verifies each path is inside this session'"'"'s scratch, deletes it and writes a receipt). Do not request owner approval for routine scratch cleanup. If the target is not task-owned scratch, write its literal absolute path so the normal rules apply.\n' \
    "${joined% }" "$AILAB_PLUGIN_ROOT" >&2
  exit 2
fi
if ((${#ASKS[@]})); then
  joined="$(printf '%s ' "${ASKS[@]}")"
  emit_ask guard-bash "Owner confirmation needed: ${joined% }" "$CMD"
fi
exit 0
