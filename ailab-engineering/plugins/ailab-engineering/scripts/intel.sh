#!/usr/bin/env bash
# Runs scripts/intel.py (code and change intelligence) with a Python 3.9+
# interpreter in isolated mode without site (-I -S), so no repository code is
# imported and no .pth file of an activated virtualenv runs. An interpreter that
# lives inside the current repository is never used.
#
#   intel.sh impact | history | signals | arch-check | fbpa | metrics  [args]   (intel.sh <cmd> -h)
#
# Interpreter: $AILAB_PYTHON, else python3 on PATH, else the A.I. Lab managed
# CPython under ~/.local/share/ailab-python. Without one, advisory commands
# print an UNAVAILABLE line and exit 0 (callers fall back to git and grep and
# report impact as unknown); fbpa prints BLOCKED and exits 4.
# Hooks never call this script: hooks stay bash-only.

set -u
src="${BASH_SOURCE[0]}"; [[ "$src" == */* ]] || src="./$src"
here="$(cd "${src%/*}" && pwd)"   # builtins only: works with a minimal PATH
suitable() { "$1" -I -S -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; }
toplevel() { local t; t="$(git -c safe.directory='*' -C "$1" rev-parse --show-toplevel 2>/dev/null)" && (cd "$t" 2>/dev/null && pwd -P); }
tops=()                         # the repositories this call is about: the current one and --repo
t="$(toplevel .)" && tops+=("$t")
prev=""
for a in "$@"; do                # also the abbreviations argparse would once have accepted (--r ... --repo)
  case "$prev" in --r|--re|--rep|--repo) t="$(toplevel "$a")" && tops+=("$t") ;; esac
  case "$a" in --r=*|--re=*|--rep=*|--repo=*) t="$(toplevel "${a#*=}")" && tops+=("$t") ;; esac
  prev="$a"
done
in_repo() { # is the interpreter (or what it resolves to) inside one of those repositories? checked before it runs
  local p r top
  p="$(command -v -- "$1")" || return 1
  [[ "$p" == /* ]] || p="$PWD/$p"
  r="$(readlink -f -- "$p" 2>/dev/null)" || r="$p"
  for top in ${tops[@]+"${tops[@]}"}; do
    if [[ "$r" == "$top"/* || "$p" == "$top"/* ]]; then return 0; fi
  done
  return 1
}
py=""; refused=0
for c in "${AILAB_PYTHON:-}" python3 "${HOME:-/nonexistent}"/.local/share/ailab-python/cpython-*/bin/python3.[0-9]*; do
  [[ -n "$c" ]] || continue
  command -v -- "$c" >/dev/null 2>&1 || continue
  if in_repo "$c"; then echo "intel: ignoring the interpreter $c: it is inside the repository" >&2; refused=1; continue; fi
  if suitable "$c"; then py="$c"; break; fi
done
if [[ -z "$py" && "$refused" == 1 ]]; then      # e.g. an activated .venv of the repository: use the system's
  for c in /usr/bin/python3 /usr/local/bin/python3; do
    if [[ -x "$c" ]] && ! in_repo "$c" && suitable "$c"; then py="$c"; break; fi
  done
fi
if [[ -z "$py" ]]; then
  if [[ "${1:-}" == fbpa ]]; then
    echo "FBPA: BLOCKED — no Python 3.9+ interpreter for the intel tool (set AILAB_PYTHON)"
    exit 4
  fi
  echo "INTEL ${1:-}: UNAVAILABLE — no Python 3.9+ interpreter (set AILAB_PYTHON). Fall back to git and grep, and report impact as unknown."
  exit 0
fi
exec "$py" -I -S "$here/intel.py" "$@"
