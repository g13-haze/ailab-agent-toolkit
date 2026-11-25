#!/usr/bin/env bash
# Verify that this environment runs the intended ailab-engineering release. Read-only.
#
#   bash install/verify-install.sh --expect-version <x.y.z> [--source <dir>]
#                                  [--reference <release dir> | --manifest <file> | --skip-contents]
#   bash install/verify-install.sh --print-manifest <dir>
#
#   --source          the ailab-engineering tree Claude Code should use here (default: the
#                     tree this script is in). Inside agent-long: the mounted path.
#   --reference       a pristine release directory to compare the source contents with
#   --manifest        a manifest written by --print-manifest, instead of --reference
#   --skip-contents   do not compare contents (install.sh, or when compared separately)
#   --print-manifest  print "<sha256> <x|->  <path>" for every file and
#                     "symlink:<target> l  <path>" for every link, sorted, without .git.
#                     Two trees hold the same release exactly when their manifests are equal.
#
# Self-contained: works on any tree, also a 0.1.7 one, and inside a container through
# stdin (bash -s does not know where the script lives, so give --source):
#   docker exec -i agent-long bash -s -- --print-manifest /opt/ailab-engineering < verify-install.sh
#
# Checks, each PASS / FAIL / NOT VERIFIED:
#   1. the source tree's plugin version is the expected one;
#   2. its contents equal the reference release (no stale, missing, changed or extra files);
#   3. Claude Code reads ailab-engineering from <source>/plugins/ailab-engineering and the
#      version in that folder is the expected one (`claude plugin list --json`: readFromFolder,
#      folderVersion; "version" there is only what was recorded at install time);
#   4. the marketplace "ailab" is registered from <source>.
# Exit: 0 all PASS; 1 any FAIL; 3 no FAIL but something NOT VERIFIED; 2 usage.
set -u
manifest() { # <dir>
  [[ -d "$1" ]] || { echo "not a directory: $1" >&2; return 2; }
  ( cd "$1" || exit 2
    export LC_ALL=C
    find . -path ./.git -prune -o \( -type f -o -type l \) -print0 | sort -z | while IFS= read -r -d '' f; do
      if [[ -L "$f" ]]; then printf 'symlink:%s l  %s\n' "$(readlink -- "$f")" "${f#./}"
      else
        h="$(sha256sum -- "$f")" || exit 2
        if [[ -x "$f" ]]; then x=x; else x=-; fi
        printf '%s %s  %s\n' "${h%% *}" "$x" "${f#./}"
      fi
    done )
}
SRC=""; EXP=""; REF=""; MAN=""; SKIPC=0
if [[ -n "${BASH_SOURCE[0]:-}" && -f "${BASH_SOURCE[0]}" ]]; then SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; fi
while (($#)); do
  case "$1" in
    --print-manifest) manifest "${2:?--print-manifest needs a directory}"; exit $? ;;
    --expect-version) EXP="${2:?}"; shift 2 ;;
    --source) SRC="${2:?}"; shift 2 ;;
    --reference) REF="${2:?}"; shift 2 ;;
    --manifest) MAN="${2:?}"; shift 2 ;;
    --skip-contents) SKIPC=1; shift ;;
    -h|--help) if [[ -f "${BASH_SOURCE[0]:-}" ]]; then sed -n '2,29p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
               else echo "usage: verify-install.sh --expect-version X --source DIR [--reference DIR | --manifest FILE | --skip-contents] | --print-manifest DIR"; fi
               exit 0 ;;
    *) echo "unknown option: $1"; exit 2 ;;
  esac
done
[[ -n "$EXP" ]] || { echo "--expect-version is required"; exit 2; }
[[ -n "$SRC" ]] || { echo "--source is required when the script is read from stdin"; exit 2; }
[[ -d "$SRC" ]] || { echo "source is not a directory: $SRC"; exit 2; }
SRC="$(cd "$SRC" && pwd -P)"
fails=0; unver=0
pass() { printf '  PASS          %s\n' "$*"; }
fail() { printf '  FAIL          %s\n' "$*"; fails=$((fails + 1)); }
nv()   { printf '  NOT VERIFIED  %s\n' "$*"; unver=$((unver + 1)); }
json() { command -v python3 >/dev/null 2>&1 || return 9; python3 -I -c "import json,sys; d=json.load(sys.stdin); $1"; }
echo "== verify ailab-engineering $EXP in $SRC ($(id -un)@$(hostname 2>/dev/null || uname -n))"

v="$(sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' "$SRC/plugins/ailab-engineering/.claude-plugin/plugin.json" 2>/dev/null | head -1)"
[[ "$v" == "$EXP" ]] && pass "source version $v" || fail "source version is '${v:-missing}', expected $EXP (a stale mount or an incomplete update?)"

if [[ -n "$REF" || -n "$MAN" ]]; then
  tmp="$(mktemp -d "${TMPDIR:-/tmp}/ailab-verify.XXXXXX")" || exit 2
  trap 'rm -rf -- "$tmp"' EXIT
  ok=1
  if [[ -n "$REF" ]]; then manifest "$REF" >"$tmp/ref" || { fail "cannot read reference $REF"; ok=0; }
  else cp -- "$MAN" "$tmp/ref" 2>/dev/null && [[ -s "$tmp/ref" ]] || { fail "cannot read manifest $MAN"; ok=0; }; fi
  if (( ok )); then
    manifest "$SRC" >"$tmp/src" || fail "cannot read $SRC"
    if cmp -s "$tmp/ref" "$tmp/src"; then
      pass "contents equal the reference release ($(wc -l <"$tmp/ref") files)"
    else
      fail "contents differ from the reference release ($(diff "$tmp/ref" "$tmp/src" | grep -c '^[<>]') manifest lines):"
      diff "$tmp/ref" "$tmp/src" | grep -E '^[<>]' | sed -e 's/^< [^ ]* [^ ]*  /    missing or changed: /' -e 's/^> [^ ]* [^ ]*  /    extra or changed:   /' | sort -u | head -12
    fi
  fi
elif (( SKIPC )); then
  printf '  SKIPPED       %s\n' "contents not compared here (compare with --reference <release dir>)"
else
  nv "contents (give --reference <release dir> or --manifest <file>)"
fi

if ! command -v claude >/dev/null 2>&1; then
  nv "Claude Code installation (no claude CLI for $(id -un) here; inside a container use a login shell: bash -l)"
else
  r="$(claude plugin list --json 2>/dev/null | json '
p=[x for x in d if x.get("id")=="ailab-engineering@ailab"]
print("|".join(str(p[0].get(k,"")) for k in ("enabled","readFromFolder","folderVersion","version")) if p else "absent")' 2>/dev/null)" || r=""
  if [[ -z "$r" ]]; then nv "Claude Code plugin list (cannot parse; python3 missing?)"
  elif [[ "$r" == absent ]]; then fail "ailab-engineering@ailab is not installed for $(id -un)"
  else
    IFS='|' read -r en folder fver rec <<<"$r"
    [[ "$en" == True ]] && pass "plugin enabled" || fail "plugin installed but not enabled"
    if [[ -z "$folder" || -z "$fver" ]]; then
      nv "which tree Claude Code runs: this Claude Code ($(claude --version 2>/dev/null | head -1)) does not report readFromFolder/folderVersion (verified on 2.1.326); recorded version: ${rec:-?}"
    else
      if [[ "$(cd "$folder" 2>/dev/null && pwd -P)" == "$SRC/plugins/ailab-engineering" ]]; then pass "Claude Code reads the plugin from $folder"
      else fail "Claude Code reads the plugin from '$folder', expected $SRC/plugins/ailab-engineering (marketplace registered elsewhere?)"; fi
      [[ "$fver" == "$EXP" ]] && pass "version Claude Code runs: $fver (recorded at install: ${rec:-?})" \
        || fail "Claude Code runs version '$fver', expected $EXP"
    fi
  fi
  mp="$(claude plugin marketplace list --json 2>/dev/null | json '
m=[x for x in d if x.get("name")=="ailab"]
print((m[0].get("path") or m[0].get("installLocation") or "?") if m else "absent")' 2>/dev/null)" || mp=""
  if [[ -z "$mp" || "$mp" == "?" ]]; then nv "marketplace registration (cannot parse)"
  elif [[ "$mp" == absent ]]; then fail "marketplace 'ailab' is not registered for $(id -un)"
  elif [[ "$(cd "$mp" 2>/dev/null && pwd -P)" == "$SRC" ]]; then pass "marketplace 'ailab' registered from $mp"
  else fail "marketplace 'ailab' registered from '$mp', expected $SRC (see docs/INSTALL-HOST.md; removing the marketplace deletes the plugin data)"; fi
fi

if (( fails )); then echo "VERIFY-INSTALL: FAIL ($fails failed, $unver not verified)"; exit 1; fi
if (( unver )); then echo "VERIFY-INSTALL: INCOMPLETE ($unver not verified)"; exit 3; fi
echo "VERIFY-INSTALL: PASS"
