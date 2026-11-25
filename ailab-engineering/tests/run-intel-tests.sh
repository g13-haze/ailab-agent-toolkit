#!/usr/bin/env bash
# Tests for scripts/intel.py (impact, history, signals, arch-check, fbpa, metrics).
#   bash tests/run-intel-tests.sh                 # python3 on PATH
#   AILAB_PYTHON=/path/to/python3.9 bash tests/run-intel-tests.sh
# The tool needs Python 3.9+; hooks never depend on it. Work happens in temporary
# git repositories built from tests/fixtures/intel.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${AILAB_PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || { echo "intel tests: SKIPPED (no $PY)"; exit 0; }
"$PY" -I -B -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || { echo "intel tests: SKIPPED ($PY is older than 3.9)"; exit 0; }
# -B: no __pycache__ in the checkout (a release tree is verified file by file against its manifest)
out="$(PYTHONDONTWRITEBYTECODE=1 "$PY" -I -B -m unittest discover -s "$HERE/intel" 2>&1)"; rc=$?
printf '%s\n' "$out" | grep -E '^(FAIL|ERROR):' || true
nv="$(printf '%s\n' "$out" | grep -m1 '^NOT VERIFIED:' || true)"
printf '%s\n' "$out" | tail -3 | grep -E '^(Ran|OK|FAILED)' | tr '\n' ' '
echo "($("$PY" -I -B -c 'import platform; print("python", platform.python_version())'))${nv:+ $nv}"
exit "$rc"
