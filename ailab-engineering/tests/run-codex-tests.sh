#!/usr/bin/env bash
# Codex adapter tests: adapter contracts, installer behaviour, the shared scripts
# without any Claude Code environment, and (when the codex CLI is installed) live
# skill and AGENTS.md discovery through `codex debug prompt-input`, which renders
# the model-visible prompt offline (no model call, no credentials needed).
#   bash tests/run-codex-tests.sh
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd -P)"
CX="$ROOT/codex"
T="$(mktemp -d /tmp/ailab-codex-test.XXXXXX)"
trap 'rm -rf -- "$T"' EXIT
PASS=0; FAIL=0; SKIP=0
check() { if [[ "$1" == "$2" ]]; then PASS=$((PASS+1)); else FAIL=$((FAIL+1)); printf 'FAIL: expected %s got %s  %s\n' "$1" "$2" "$3"; fi; }
SKILLS=(ailab-work-package ailab-review ailab-wp-close ailab-codeintel ailab-change-intel)

# ---------------- adapter contracts ----------------
for s in "${SKILLS[@]}"; do
  f="$CX/skills/$s/SKILL.md"
  [[ "$(sed -n 's/^name: //p' "$f" | head -1)" == "$s" ]] && got=ok || got=bad; check ok "$got" "$s: name matches its directory"
  d="$(sed -n 's/^description: //p' "$f" | head -1)"; (( ${#d} > 40 && ${#d} <= 500 )) && got=ok || got="${#d} chars"; check ok "$got" "$s: description length"
  grep -qE '^(allowed-tools|disable-model-invocation|user-invocable|argument-hint):' "$f" && got=claude-only || got=ok
  check ok "$got" "$s: no Claude-only frontmatter"
  grep -qE '^!`|\$\{CLAUDE_PLUGIN_ROOT\}/scripts' "$f" && got=claude-only || got=ok
  check ok "$got" "$s: no Claude dynamic-context lines or CLAUDE_PLUGIN_ROOT commands"
  grep -qF '@AILAB_OS@/codex/platform-mapping.md' "$f" || [[ "$s" == ailab-codeintel ]] && got=ok || got=missing
  check ok "$got" "$s: points to the platform mapping"
done
got=ok
while IFS= read -r ref; do
  rel="${ref#@AILAB_OS@/}"; rel="${rel%%[\`\'\" ),]*}"; rel="${rel%.}"
  [[ "$rel" == *'<'* ]] && rel="${rel%%<*}"
  [[ -e "$ROOT/$rel" ]] || got="missing: $rel"
done < <(grep -ohE '@AILAB_OS@/[A-Za-z0-9_./<>|-]+' "$CX"/skills/*/SKILL.md "$CX/platform-mapping.md" | sort -u)
check ok "$got" "every file an adapter references exists"
grep -qF 'not active in Codex' "$CX/platform-mapping.md" && grep -qF 'Owner approval boundaries are identical' "$CX/platform-mapping.md" && got=ok || got=missing
check ok "$got" "platform mapping states that guards are not active and approvals are unchanged"
grep -qF 'Review exactly one axis' "$CX/skills/ailab-review/SKILL.md" && grep -qF 'never an implementation you wrote in this session' "$CX/skills/ailab-review/SKILL.md" && got=ok || got=missing
check ok "$got" "ailab-review keeps review independence (one axis, fresh session)"
grep -qF 'do not certify your own work' "$CX/skills/ailab-work-package/SKILL.md" && grep -qF 'STOP for the owner' "$CX/skills/ailab-work-package/SKILL.md" && got=ok || got=missing
check ok "$got" "ailab-work-package: no self-certification, T3 stop"

# ---------------- installer ----------------
I="$CX/install-codex-skills.sh"
H="$T/codex-home"
check 2 "$(bash "$I" --bogus >/dev/null 2>&1; echo $?)" "installer: unknown option"
check 2 "$(bash "$I" --dest rel/path >/dev/null 2>&1; echo $?)" "installer: relative --dest refused"
CODEX_HOME="$H" bash "$I" --dry-run >"$T/o" 2>&1; check 0 "$?" "installer: dry run"
[[ ! -e "$H" ]] && got=ok || got=wrote; check ok "$got" "installer: dry run writes nothing"
CODEX_HOME="$H" bash "$I" >"$T/o" 2>&1; check 0 "$?" "installer: install"
got=ok; for s in "${SKILLS[@]}"; do
  f="$H/skills/$s/SKILL.md"
  [[ -f "$f" && -f "$H/skills/$s/.ailab-managed" ]] || { got="missing $s"; continue; }
  grep -q '@AILAB_OS@' "$f" && got="placeholder left in $s"
  grep -qF "$ROOT/plugins/ailab-engineering" "$f" || [[ "$s" == ailab-codeintel && "$(grep -c "$ROOT" "$f")" -gt 0 ]] || got="no absolute path in $s"
done
check ok "$got" "installer: rendered skills with the checkout path and markers"
s1="$(cat "$H"/skills/*/SKILL.md | sha256sum)"; CODEX_HOME="$H" bash "$I" >/dev/null 2>&1
check "$s1" "$(cat "$H"/skills/*/SKILL.md | sha256sum)" "installer: idempotent"
mkdir -p "$H/skills/other-skill" "$T/h2/skills/ailab-review"; echo mine >"$H/skills/other-skill/SKILL.md"; echo mine >"$T/h2/skills/ailab-review/SKILL.md"
check 3 "$(bash "$I" --dest "$T/h2/skills" >/dev/null 2>&1; echo $?)" "installer: refuses a directory it did not create"
check mine "$(cat "$T/h2/skills/ailab-review/SKILL.md")" "installer: the foreign directory is untouched"
mkdir -p "$H/skills/ailab-foreign"; echo mine >"$H/skills/ailab-foreign/SKILL.md"
CODEX_HOME="$H" bash "$I" --uninstall --dry-run >/dev/null 2>&1
[[ -d "$H/skills/ailab-review" ]] && got=ok || got=removed; check ok "$got" "installer: uninstall dry run removes nothing"
CODEX_HOME="$H" bash "$I" --uninstall >"$T/o" 2>&1
got=ok; for s in "${SKILLS[@]}"; do [[ -e "$H/skills/$s" ]] && got="left $s"; done
[[ -f "$H/skills/ailab-foreign/SKILL.md" && -f "$H/skills/other-skill/SKILL.md" ]] || got="removed a foreign skill"
check ok "$got" "installer: uninstall removes only its own skills"
# a checkout path with characters that are special in sed and bash replacement
AMP="$T/a&b|c"; mkdir -p "$AMP" && cp -R "$ROOT/codex" "$ROOT/plugins" "$AMP/"
bash "$AMP/codex/install-codex-skills.sh" --dest "$T/h3/skills" >/dev/null 2>&1
grep -qF "$AMP/plugins/ailab-engineering/scripts/skill-context.sh" "$T/h3/skills/ailab-work-package/SKILL.md" && got=ok || got=mangled
check ok "$got" "installer: a checkout path with & and | is rendered literally"

# ---------------- shared scripts with no Claude Code environment ----------------
CODEX_HOME="$H" bash "$I" >/dev/null 2>&1
PR="$T/proj"; mkdir -p "$PR/src" "$PR/.claude/wp" && git -C "$PR" init -q
cp "$ROOT/templates/project/AGENTS.md" "$PR/AGENTS.md"
printf 'def f():\n    return 1\n' >"$PR/src/a.py"; printf 'from src.a import f\n' >"$PR/src/b.py"
git -C "$PR" add -- . && git -C "$PR" -c user.email=t@t -c user.name=t commit -qm base
noclaude() { (cd "$PR" && env -u CLAUDE_PLUGIN_ROOT -u CLAUDE_PLUGIN_DATA -u AILAB_PLUGIN_ROOT -u AILAB_SESSION_ID -u CLAUDE_CODE_SESSION_ID -u CLAUDE_ENV_FILE \
  AILAB_SCRATCH_ROOT="$T/scratch" HOME="$T/home" "$@"); }
SC="$ROOT/plugins/ailab-engineering/scripts"
for m in wp-start wp-template review wp-close change-intel handoff; do
  noclaude "$SC/skill-context.sh" "$m" >"$T/o" 2>&1; check 0 "$?" "Codex env: skill-context $m exits 0"
done
noclaude "$SC/skill-context.sh" wp-start | grep -q '^## Engineering behaviour' && got=ok || got=missing
check ok "$got" "Codex env: wp-start prints the behaviour rules"
noclaude "$SC/intel.sh" impact src/a.py | grep -q 'direct (1): src/b.py' && got=ok || got=bad
check ok "$got" "Codex env: intel impact works"
d="$(noclaude "$SC/scratch.sh" new wp-X1)"; [[ "$d" == "$T/scratch/manual/wp-X1" && -d "$d" ]] && got=ok || got="$d"
check ok "$got" "Codex env: scratch new uses the manual session"
noclaude "$SC/scratch.sh" clean wp-X1 >/dev/null 2>&1; [[ ! -e "$d" ]] && got=ok || got=left
check ok "$got" "Codex env: scratch clean <label> works"
check 3 "$(noclaude "$SC/scratch.sh" clean-path "$T/scratch/manual/x" >/dev/null 2>&1; echo $?)" "Codex env: clean-path refuses without a trusted session (as documented)"

# ---------------- live discovery by the codex CLI ----------------
if command -v codex >/dev/null 2>&1; then
  if (cd "$PR" && CODEX_HOME="$H" HOME="$T/home" timeout 120 codex debug prompt-input >"$T/prompt.json" 2>"$T/prompt.err"); then
    got=ok; for s in "${SKILLS[@]}"; do grep -q -- "- $s: " "$T/prompt.json" || got="codex did not list $s"; done
    check ok "$got" "codex CLI discovers all five ailab skills ($(codex --version 2>/dev/null))"
    grep -qF "$H/skills/ailab-review/SKILL.md" "$T/prompt.json" || grep -qF 'ailab-review/SKILL.md' "$T/prompt.json" && got=ok || got=missing
    check ok "$got" "codex CLI points at the installed skill files"
    grep -qF 'Goal-driven execution' "$T/prompt.json" && grep -qF 'At most 2 review rounds for T2 and 3 for T3' "$T/prompt.json" && got=ok || got=missing
    check ok "$got" "codex CLI loads the AGENTS.md behaviour rules and review limits"
  else
    SKIP=$((SKIP+1)); echo "SKIP: codex debug prompt-input failed: $(head -c 200 "$T/prompt.err")"
  fi
else
  SKIP=$((SKIP+1)); echo "SKIP: codex CLI not installed (live discovery not verified)"
fi

echo "codex tests  passed: $PASS  failed: $FAIL  skipped: $SKIP"
(( FAIL == 0 ))
