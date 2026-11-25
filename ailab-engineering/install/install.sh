#!/usr/bin/env bash
# Install the ailab-engineering plugin for the CURRENT user on THIS machine or
# container. Run it once per environment where Claude Code runs as an agent
# (example host host user, agent-long, agent-fast if it runs Claude Code).
#
#   bash install/install.sh [--expect-version x.y.z] [--scope user|project|local] [--with-deny-rules] [--dry-run]
#
#   --expect-version   refuse to do anything unless this tree is that release (catches a stale
#                      container mount or an incomplete update); after installing, verify that
#                      Claude Code runs exactly this tree (install/verify-install.sh)
#   --scope            plugin install scope (default: user = every project for this user)
#   --with-deny-rules  also merge templates/user/settings.deny.json (deny rules, plus ask rules for
#                      publishing commands) into ~/.claude/settings.json (backup written next to it first)
#   --dry-run          only check prerequisites and run the hook tests
#
# Idempotent: safe to run again after pulling a new version.

set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCOPE="user"
DENY=0
DRY=0
EXPECT=""
while (($#)); do
  case "$1" in
    --expect-version) EXPECT="${2:?--expect-version needs a version}"; shift 2 ;;
    --scope) SCOPE="${2:-user}"; shift 2 ;;
    --with-deny-rules) DENY=1; shift ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1"; exit 2 ;;
  esac
done
case "$SCOPE" in user|project|local) ;; *) echo "invalid --scope $SCOPE"; exit 2 ;; esac

ok()   { printf '  [ok]   %s\n' "$*"; }
warn() { printf '  [warn] %s\n' "$*"; }
fail() { printf '  [FAIL] %s\n' "$*"; exit 1; }

SRCV="$(sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' "$REPO/plugins/ailab-engineering/.claude-plugin/plugin.json" | head -1)"
echo "== ailab-engineering installer =="
echo "repo: $REPO   version: ${SRCV:-?}   user: $(id -un)   home: ${HOME:-?}   scope: $SCOPE"
if [[ -n "$EXPECT" && "$SRCV" != "$EXPECT" ]]; then
  fail "this tree is version '${SRCV:-unknown}', expected $EXPECT: nothing changed. Inside a container this usually means the bind mount still shows the old directory (see docs/INSTALL-HOST.md)."
fi

echo "-- prerequisites"
command -v claude >/dev/null 2>&1 || fail "claude CLI not found in PATH"
CV="$(claude --version 2>/dev/null | head -1 | grep -Eo '[0-9]+\.[0-9]+\.[0-9]+' | head -1)"
ok "claude ${CV:-unknown}"
if [[ -n "$CV" ]] && [[ "$(printf '%s\n2.1.295\n' "$CV" | sort -V | head -1)" != "2.1.295" ]]; then
  warn "Claude Code $CV < 2.1.295: hooks.json 'onFailure: block' is not supported, so a crashed or timed-out guard would let the command through. Run: claude update"
fi
command -v git >/dev/null 2>&1 && ok "git" || warn "git missing: session context and repo guards degrade"
if command -v jq >/dev/null 2>&1; then ok "jq"
elif command -v node >/dev/null 2>&1; then warn "jq missing, hooks will use node (slower). Recommended: apt-get install -y jq"
elif command -v python3 >/dev/null 2>&1; then warn "jq missing, hooks will use python3. Recommended: apt-get install -y jq"
else fail "no jq, node or python3: guard hooks could not read tool calls. Install jq first."
fi
command -v realpath >/dev/null 2>&1 && ok "realpath" || warn "realpath missing: path normalization is weaker"
command -v timeout  >/dev/null 2>&1 && ok "timeout"  || warn "timeout missing: slow git repos may delay session start"

echo "-- hook tests"
TLOG="$(mktemp -d "${TMPDIR:-/tmp}/ailab-install-tests.XXXXXX")" || fail "cannot create a private temporary directory"
trap 'rm -rf -- "$TLOG"' EXIT
if command -v jq >/dev/null 2>&1; then
  if bash "$REPO/tests/run-hook-tests.sh" >"$TLOG/hook.log" 2>&1; then
    ok "$(tail -1 "$TLOG/hook.log")"
  else
    cat "$TLOG/hook.log"
    fail "hook tests failed; not installing"
  fi
else
  warn "skipping hook tests (they need jq)"
fi
if bash "$REPO/tests/run-contract-tests.sh" >"$TLOG/contract.log" 2>&1; then
  ok "$(tail -1 "$TLOG/contract.log")"
else
  cat "$TLOG/contract.log"
  fail "contract tests failed; not installing"
fi
# intel.sh (code intelligence) is optional: without Python 3.9+ it reports UNAVAILABLE.
if out="$(bash "$REPO/tests/run-intel-tests.sh" 2>&1)"; then
  ok "intel tests: $(printf '%s' "$out" | tail -1 | sed 's/ NOT VERIFIED:.*//')"
  if printf '%s' "$out" | grep -q 'NOT VERIFIED:'; then
    warn "fbpa sandbox unavailable for $(id -un) here: fbpa and pinned checks will report BLOCKED and run nothing ($REPO/plugins/ailab-engineering/scripts/intel.sh sandbox-check shows why)"
  fi
else
  printf '%s\n' "$out" | tail -20
  fail "intel tests failed; not installing"
fi

echo "-- plugin validation"
claude plugin validate "$REPO" >/dev/null 2>&1 && ok "marketplace manifest valid" || fail "marketplace manifest invalid: run claude plugin validate $REPO"
claude plugin validate "$REPO/plugins/ailab-engineering" >/dev/null 2>&1 && ok "plugin manifest valid" || fail "plugin invalid: run claude plugin validate $REPO/plugins/ailab-engineering"

(( DRY )) && { echo "dry run: stopping before any change"; exit 0; }

echo "-- marketplace"
# Claude Code runs a directory marketplace's plugin straight from that directory, so
# the registered path decides which code runs. A registration elsewhere is not changed silently.
MKT_PATH=""
if command -v jq >/dev/null 2>&1; then
  MKT_PATH="$(claude plugin marketplace list --json 2>/dev/null | jq -r '.[]? | select(.name=="ailab") | (.path // .installLocation // "")' 2>/dev/null | head -1)"
elif command -v python3 >/dev/null 2>&1; then
  MKT_PATH="$(claude plugin marketplace list --json 2>/dev/null | python3 -I -c 'import json,sys
d=json.load(sys.stdin); m=[x for x in d if x.get("name")=="ailab"]; print((m[0].get("path") or m[0].get("installLocation") or "") if m else "")' 2>/dev/null)"
fi
if [[ -n "$MKT_PATH" && "$(cd "$MKT_PATH" 2>/dev/null && pwd -P)" != "$(cd "$REPO" && pwd -P)" ]]; then
  fail "marketplace 'ailab' is registered from $MKT_PATH, not $REPO: Claude Code keeps running that tree. Nothing changed. Update that tree in place instead (docs/INSTALL-HOST.md). Re-registering needs 'claude plugin marketplace remove ailab', which also deletes ~/.claude/plugins/data/ailab-engineering-ailab (guard.log, receipts): back that directory up first."
fi
if claude plugin marketplace list 2>/dev/null | grep -qE '(^|[[:space:]])ailab([[:space:]]|$)'; then
  claude plugin marketplace update ailab >/dev/null 2>&1 && ok "marketplace 'ailab' updated" || warn "marketplace update failed (continuing)"
else
  claude plugin marketplace add "$REPO" || fail "could not add marketplace from $REPO"
  ok "marketplace 'ailab' added"
fi

echo "-- plugin"
if claude plugin list 2>/dev/null | grep -q 'ailab-engineering@ailab'; then
  claude plugin update ailab-engineering@ailab >/dev/null 2>&1 && ok "plugin updated" || ok "plugin already installed"
else
  claude plugin install ailab-engineering@ailab --scope "$SCOPE" || fail "plugin install failed"
  ok "plugin installed (scope: $SCOPE)"
fi

echo "-- verify"
vrc=0; vout="$(bash "$REPO/install/verify-install.sh" --expect-version "$SRCV" --source "$REPO" --skip-contents 2>&1)" || vrc=$?
printf '%s\n' "$vout" | sed 's/^/  /'
case "$vrc" in
  0) ok "Claude Code runs ailab-engineering $SRCV from $REPO" ;;
  3) warn "installation partly verified (see NOT VERIFIED above)" ;;
  *) fail "Claude Code does not run this tree; see the FAIL lines above" ;;
esac

if (( DENY )); then
  echo "-- deny rules"
  command -v jq >/dev/null 2>&1 || fail "--with-deny-rules needs jq"
  SETTINGS="${HOME}/.claude/settings.json"
  mkdir -p "$(dirname "$SETTINGS")"
  [[ -f "$SETTINGS" ]] || echo '{}' >"$SETTINGS"
  backup="$SETTINGS.bak-$(date -u +%Y%m%dT%H%M%SZ)"
  cp -p "$SETTINGS" "$backup" && ok "backup: $backup"
  tmp="$(mktemp)"
  # deny rules block in every mode; ask rules (publishing) prompt the owner (documented to apply in
  # bypassPermissions mode too; not verified here).
  if jq -s '.[0] as $cur | .[1] as $add
            | $cur * {permissions: (($cur.permissions // {}) + {
                deny: ((($cur.permissions.deny // []) + ($add.permissions.deny // [])) | unique),
                ask:  ((($cur.permissions.ask // []) + ($add.permissions.ask // [])) | unique)})}' \
        "$SETTINGS" "$REPO/templates/user/settings.deny.json" >"$tmp" && jq empty "$tmp"; then
    mv "$tmp" "$SETTINGS" && ok "permission rules merged into $SETTINGS ($(jq '.permissions.deny | length' "$SETTINGS") deny, $(jq '.permissions.ask | length' "$SETTINGS") ask)"
  else
    rm -f "$tmp"; fail "could not merge deny rules; $SETTINGS unchanged"
  fi
fi

echo "-- result"
claude plugin details ailab-engineering@ailab 2>/dev/null | sed -n '1,16p'
cat <<EOF

Next:
  1. Start a NEW Claude Code session in a project and check the first message
     contains "[ailab-engineering] session context".
  2. Ask Claude to run:  git add -A     -> must be BLOCKED by ailab-engineering.
  3. Type /ailab-engineering:  -> work-package, review, wp-close, change-intel,
     knowledge-review, host-gate, handoff appear; /agents shows spec-reviewer.
  4. Ask Claude to run:  gh pr create --title x --body y   -> must ASK the owner.
  5. Guard log:  ~/.claude/plugins/data/ailab-engineering-*/guard.log
  Codex (optional, per user):  bash codex/install-codex-skills.sh
EOF
