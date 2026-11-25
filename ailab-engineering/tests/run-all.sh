#!/usr/bin/env bash
# Release check: every test suite, shellcheck and plugin validation, with one summary.
#   bash tests/run-all.sh [--with-install]     (--with-install adds the slow installer and upgrade tests)
# Exit 0 only when every suite that could run passed. Suites whose tools are
# missing (node, python3, shellcheck, claude, codex) are reported as SKIPPED.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
cd "$ROOT" || exit 2
LOG="$(mktemp -d /tmp/ailab-run-all.XXXXXX)"
rc_all=0; rows=()
run() { # <name> <cmd...>
  local name="$1" t0 rc; shift
  t0=$(date +%s)
  "$@" >"$LOG/$name.log" 2>&1; rc=$?
  rows+=("$(printf '%-22s %-7s %4ss  %s' "$name" "$( ((rc==0)) && echo PASS || echo FAIL)" "$(( $(date +%s) - t0 ))" "$(grep -E 'passed|Ran |OK|FAILED|Validation|SKIPPED' "$LOG/$name.log" | tail -1 | cut -c1-90)")")
  (( rc == 0 )) || rc_all=1
}
skip() { rows+=("$(printf '%-22s %-7s        %s' "$1" SKIPPED "$2")"); }
for p in jq node python3; do
  if command -v "$p" >/dev/null 2>&1; then run "hooks-$p" env AILAB_JSON_PARSER="$p" bash tests/run-hook-tests.sh; else skip "hooks-$p" "$p not installed"; fi
done
run intel bash tests/run-intel-tests.sh
# as in agent-long under Docker's default seccomp profile: user namespaces refused (emulated with an outer
# bubblewrap), else the sandbox switched off. fbpa must fail closed; everything else must still pass.
if /usr/bin/bwrap --dev-bind / / --unshare-user --disable-userns -- true 2>/dev/null; then
  run intel-nouserns /usr/bin/bwrap --dev-bind / / --unshare-user --disable-userns -- bash tests/run-intel-tests.sh
else
  run intel-nosandbox env AILAB_SANDBOX_DISABLE=1 bash tests/run-intel-tests.sh
fi
run contract bash tests/run-contract-tests.sh
run codex bash tests/run-codex-tests.sh
if [[ "${1:-}" == --with-install ]]; then run install bash tests/run-install-tests.sh; run upgrade bash tests/run-upgrade-tests.sh
else skip install "add --with-install"; skip upgrade "add --with-install"; fi
if python3 -m pyflakes --version >/dev/null 2>&1; then
  run pyflakes python3 -m pyflakes plugins/ailab-engineering/scripts/intel.py tests/intel/test_intel.py tests/evals/summarize.py
else skip pyflakes "pyflakes not installed"; fi
if command -v shellcheck >/dev/null 2>&1; then
  run shellcheck shellcheck -S warning -x plugins/ailab-engineering/scripts/*.sh plugins/ailab-engineering/scripts/lib/common.sh install/*.sh tests/*.sh codex/*.sh
else skip shellcheck "shellcheck not installed"; fi
if command -v claude >/dev/null 2>&1; then
  run validate-marketplace claude plugin validate .
  run validate-plugin claude plugin validate plugins/ailab-engineering
else skip validate "claude CLI not installed"; fi
printf '%s\n' "${rows[@]}"
echo "logs: $LOG"
exit "$rc_all"
