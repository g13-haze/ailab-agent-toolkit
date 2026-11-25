#!/usr/bin/env bash
# Installer tests: run install/install.sh against a stub `claude` CLI and a
# throwaway HOME. Checks the dry run, the permission-rule merge (deny + ask),
# preservation of existing settings, the backup, and idempotence.
#   bash tests/run-install-tests.sh
# Nothing outside a temporary directory is touched.

set -u
command -v jq >/dev/null 2>&1 || { echo "install tests need jq"; exit 1; }
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
T="$(mktemp -d /tmp/ailab-install-test.XXXXXX)"
trap 'rm -rf -- "$T"' EXIT

PASS=0; FAIL=0
check() { if [[ "$1" == "$2" ]]; then PASS=$((PASS+1)); else FAIL=$((FAIL+1)); printf 'FAIL: expected %s got %s  %s\n' "$1" "$2" "$3"; fi; }

mkdir -p "$T/bin" "$T/state" "$T/home/.claude"
cat >"$T/bin/claude" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$STUB_STATE/log"
mkt="$(cat "$STUB_STATE/mkt_path" 2>/dev/null)"
case "${1:-} ${2:-}" in
  "--version "*) echo "2.1.326 (Claude Code)" ;;
  "plugin validate") exit 0 ;;
  "plugin marketplace")
    case "${3:-} ${4:-}" in
      "list --json") if [[ -n "$mkt" ]]; then printf '[{"name":"ailab","source":"directory","path":"%s"}]\n' "$mkt"; else echo '[]'; fi ;;
      list*) [[ -n "$mkt" ]] && echo "  > ailab" ;;
      add*) printf '%s' "$4" >"$STUB_STATE/mkt_path" ;;
    esac ;;
  "plugin list")
    if [[ "${3:-}" == --json ]]; then
      if [[ -s "$STUB_STATE/plugins" && -n "$mkt" ]]; then
        fv="$(sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' "$mkt/plugins/ailab-engineering/.claude-plugin/plugin.json" | head -1)"
        printf '[{"id":"ailab-engineering@ailab","version":"%s","enabled":true,"readFromFolder":"%s/plugins/ailab-engineering","folderVersion":"%s"}]\n' "$fv" "$mkt" "$fv"
      else echo '[]'; fi
    else cat "$STUB_STATE/plugins" 2>/dev/null; fi ;;
  "plugin install") echo "ailab-engineering@ailab" >"$STUB_STATE/plugins" ;;
esac
exit 0
EOF
chmod +x "$T/bin/claude"
export STUB_STATE="$T/state" PATH="$T/bin:$PATH" HOME="$T/home"
SET="$HOME/.claude/settings.json"
run() { bash "$REPO/install/install.sh" "$@" >"$T/out" 2>&1; echo $?; }

check 2 "$(run --bogus)" "unknown option is a usage error"
VER="$(sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' "$REPO/plugins/ailab-engineering/.claude-plugin/plugin.json" | head -1)"
check 1 "$(run --expect-version 9.9.9 --with-deny-rules)" "a different expected version is refused"
grep -q "this tree is version '$VER', expected 9.9.9: nothing changed" "$T/out" && [[ ! -e "$SET" && ! -e "$STUB_STATE/log" ]] && got=ok || got=changed
check ok "$got" "refused before any change (no settings, no claude call)"
check 0 "$(run --dry-run --with-deny-rules)" "dry run succeeds"
[[ ! -e "$SET" ]] && ! grep -q 'plugin install' "$STUB_STATE/log" && got=ok || got=changed
check ok "$got" "dry run changes nothing and installs nothing"

printf '%s\n' '{"model":"opus","permissions":{"deny":["Bash(rm -rf /data)"],"ask":["Bash(make release)"]}}' >"$SET"
check 0 "$(run --expect-version "$VER" --with-deny-rules)" "install with permission rules and the expected version"
grep -q "Claude Code runs ailab-engineering $VER from $REPO" "$T/out" && grep -q 'VERIFY-INSTALL: PASS' "$T/out" && got=ok || got="$(grep -E 'verify|VERIFY|FAIL' "$T/out")"
check ok "$got" "post-install verification passes"
got="$(jq -r '.model' "$SET")"; check opus "$got" "existing unrelated settings are preserved"
jq -e '.permissions.deny | index("Bash(rm -rf /data)")' "$SET" >/dev/null && got=ok || got=missing
check ok "$got" "existing deny rule preserved"
jq -e '.permissions.ask | index("Bash(make release)")' "$SET" >/dev/null && got=ok || got=missing
check ok "$got" "existing ask rule preserved"
jq -e '(.permissions.deny | index("Bash(git add -A)")) and (.permissions.ask | index("Bash(gh pr merge *)")) and (.permissions.ask | index("Bash(npm publish)"))' "$SET" >/dev/null && got=ok || got=missing
check ok "$got" "template deny and publishing ask rules merged"
compgen -G "$SET.bak-*" >/dev/null && got=ok || got=missing
check ok "$got" "settings backup written before the merge"
n1="$(jq '.permissions.deny + .permissions.ask | length' "$SET")"
check 0 "$(run --with-deny-rules)" "second install run"
check "$n1" "$(jq '.permissions.deny + .permissions.ask | length' "$SET")" "merge is idempotent (no duplicates)"
grep -q 'plugin install ailab-engineering@ailab --scope user' "$STUB_STATE/log" && got=ok || got=missing
check ok "$got" "plugin installed through the claude CLI"
tpl="$REPO/templates/user/settings.deny.json"
jq -e '.permissions.ask | all(startswith("Bash("))' "$tpl" >/dev/null && got=ok || got=bad
check ok "$got" "template ask rules are Bash rules only"

# a marketplace registered from another tree is not changed silently
OTHER="$T/other-tree"; mkdir -p "$OTHER"; printf '%s' "$OTHER" >"$STUB_STATE/mkt_path"; : >"$STUB_STATE/log"
check 1 "$(run)" "a marketplace registered from another tree is refused"
grep -q "marketplace 'ailab' is registered from $OTHER" "$T/out" && ! grep -q 'plugin install\|plugin update\|marketplace update' "$STUB_STATE/log" && got=ok || got=changed
check ok "$got" "refused before any marketplace or plugin change"

echo "install tests  passed: $PASS  failed: $FAIL"
(( FAIL == 0 ))
