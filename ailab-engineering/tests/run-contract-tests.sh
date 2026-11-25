#!/usr/bin/env bash
# Contract tests for skills, agents, shared policy, protocols, templates and the
# Codex adapter: the prose rules the workflow depends on, frontmatter, budgets and
# cross-references. Parser-independent, so they run once (not per JSON parser).
#   bash tests/run-contract-tests.sh
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
P="$ROOT/plugins/ailab-engineering"
SK="$P/skills"; AG="$P/agents"; SH="$P/shared"; TP="$ROOT/templates/project"
T="$(mktemp -d /tmp/ailab-contract-test.XXXXXX)"
trap 'rm -rf -- "$T"' EXIT
PASS=0; FAIL=0
check() { if [[ "$1" == "$2" ]]; then PASS=$((PASS+1)); else FAIL=$((FAIL+1)); printf 'FAIL: expected %s got %s  %s\n' "$1" "$2" "$3"; fi; }
has()  { grep -qF -- "$2" "$1"; }                   # <file> <fixed text>
all()  { local f="$1" t; shift; for t in "$@"; do has "$f" "$t" || { echo "missing in ${f#"$ROOT"/}: $t"; return 1; }; done; echo ok; }
fm()   { awk 'NR==1 && /^---$/ {on=1; next} on && /^---$/ {exit} on {print}' "$1"; }   # frontmatter
fmv()  { fm "$1" | sed -n "s/^$2: *//p" | head -1; }

# ---------------- frontmatter and invocation ----------------
for s in work-package wp-close host-gate handoff change-intel knowledge-review; do
  check true "$(fmv "$SK/$s/SKILL.md" disable-model-invocation)" "$s is owner-invoked only (no always-on context)"
done
for s in review codeintel; do
  check "" "$(fmv "$SK/$s/SKILL.md" disable-model-invocation)" "$s is model-invocable"
  check "" "$(fmv "$SK/$s/SKILL.md" user-invocable)" "$s is also user-invocable"
done
check false "$(fmv "$SK/scratch/SKILL.md" user-invocable)" "scratch stays model-invoked only"
for a in reviewer spec-reviewer security-reviewer qa-runner; do
  check "Read, Grep, Glob, Bash" "$(fmv "$AG/$a.md" tools)" "$a tools are read-only plus Bash"
  check "Write, Edit, MultiEdit, NotebookEdit" "$(fmv "$AG/$a.md" disallowedTools)" "$a cannot write files"
  [[ "$(fmv "$AG/$a.md" maxTurns)" =~ ^[0-9]+$ ]] && got=ok || got=missing; check ok "$got" "$a has a maxTurns ceiling"
  has "$AG/$a.md" 'scripts/skill-context.sh" snapshot' && got=ok || got=missing; check ok "$got" "$a records the snapshot"
done
check sonnet "$(fmv "$AG/spec-reviewer.md" model)" "spec-reviewer model"
# every skill's Bash commands are pre-approved by its own allowed-tools
got=ok
for f in "$SK"/*/SKILL.md; do
  allow="$(fmv "$f" allowed-tools)"
  while IFS= read -r sc; do
    [[ "$sc" == intel.sh ]] && continue
    [[ "$allow" == *"Bash(\${CLAUDE_PLUGIN_ROOT}/scripts/$sc *)"* ]] || got="${f#"$ROOT"/} uses $sc without allowed-tools"
  done < <(grep -oE '\$\{CLAUDE_PLUGIN_ROOT\}/scripts/[a-z-]+\.sh' "$f" | sed 's#.*/##' | sort -u)
  while IFS= read -r sub; do      # intel.sh is granted per read-only subcommand
    [[ "$sub" == fbpa ]] && continue
    [[ "$allow" == *"Bash(\${CLAUDE_PLUGIN_ROOT}/scripts/intel.sh $sub *)"* ]] || got="${f#"$ROOT"/} uses intel.sh $sub without allowed-tools"
  done < <(grep -oE '\$\{CLAUDE_PLUGIN_ROOT\}/scripts/intel\.sh [a-z-]+' "$f" | sed 's#.* ##' | sort -u)
done
check ok "$got" "skills pre-approve every plugin script and intel subcommand they call"
grep -lE 'scripts/intel\.sh (\*|fbpa)' "$SK"/*/SKILL.md >/dev/null && got="intel.sh * or fbpa pre-approved" || got=ok
check ok "$got" "no skill pre-approves intel.sh fbpa (it runs arbitrary test commands)"
# every referenced plugin file exists
got=ok
while IFS= read -r ref; do
  rel="${ref#*\}/}"; rel="${rel#*\$AILAB_PLUGIN_ROOT/}"; rel="${rel%\"}"
  [[ "$rel" == *'<'* ]] && continue
  [[ -e "$P/$rel" ]] || got="missing referenced file: $rel"
done < <(grep -ohE '(\$\{CLAUDE_PLUGIN_ROOT\}|\$AILAB_PLUGIN_ROOT)/(shared|scripts|knowledge)/[A-Za-z0-9_./<>|-]+' "$SK"/*/SKILL.md "$AG"/*.md | sed 's/[.,`)]*$//' | sort -u)
check ok "$got" "referenced plugin files exist"
for t in feature bugfix refactor infra-docs; do [[ -s "$SH/protocols/$t.md" ]] && got=ok || got=missing; check ok "$got" "protocol $t exists"; done
has "$SK/work-package/SKILL.md" 'shared/protocols/<feature|bugfix|refactor|infra-docs>.md' && got=ok || got=missing
check ok "$got" "work-package loads only the protocol for the WP type"

# ---------------- always-on context budget ----------------
total=0
for f in "$SK"/*/SKILL.md "$AG"/*.md; do
  [[ "$(fmv "$f" disable-model-invocation)" == true ]] && continue
  d="$(fmv "$f" description)"; total=$((total + ${#d}))
done
(( total <= 2200 )) && got=ok || got="$total chars"
check ok "$got" "always-on skill and agent descriptions stay within 2,200 chars (0.1.7: 1,295)"
# Baseline facts are frozen in tests/fixtures/baseline-0.1.7 so the suite also runs from a release
# archive without git history (install.sh runs it before installing).
BL="$HERE/fixtures/baseline-0.1.7"
# 0.2.2 (owner-approved): the only addition is the UserPromptSubmit owner-authorization hook.
UPS='/^    "UserPromptSubmit": \[$/,/^    \],$/'
check "$(cat "$BL/hooks.json.sha256")" "$(sed "${UPS}d" "$P/hooks/hooks.json" | sha256sum | cut -d' ' -f1)" "hooks.json: the 0.1.7 hooks are unchanged"
check '"command": "\"${CLAUDE_PLUGIN_ROOT}/scripts/authorize.sh\"",' "$(sed -n "${UPS}p" "$P/hooks/hooks.json" | grep -F '"command":' | sed 's/^ *//')" "hooks.json: the one new hook (0.2.2) runs only authorize.sh"

# ---------------- shared behaviour rules: one text, two homes ----------------
blk() { sed -n '/<!-- ailab:engineering-behaviour:start -->/,/<!-- ailab:engineering-behaviour:end -->/p' "$1"; }
[[ -n "$(blk "$SH/engineering-behaviour.md")" && "$(blk "$SH/engineering-behaviour.md")" == "$(blk "$TP/AGENTS.md")" ]] && got=ok || got=differs
check ok "$got" "AGENTS.md template carries the shared behaviour block verbatim"
check ok "$(all "$SH/engineering-behaviour.md" 'Think before coding' 'Simplicity first' 'Surgical changes' 'Goal-driven execution' 'verify:')" "behaviour block: four rules and step checks"
(( $(blk "$SH/engineering-behaviour.md" | wc -c) <= 900 )) && got=ok || got=long; check ok "$got" "behaviour block stays under 900 bytes"

# ---------------- work-package v2 ----------------
WP="$SK/work-package/SKILL.md"
check ok "$(all "$SH/wp-template.md" 'Type: feature | bugfix | refactor | infra | docs' 'Size: S | M | L' 'Progress:' 'Repo HEAD at start' '→ verify:' '## Context' 'fbpa (bugfix) | pinned (refactor)')" "shared WP template v2 fields"
check ok "$(all "$WP" '**Type**' '**Size**' 'Split rule' '→ verify:' 'Progress:' 'intel.sh impact' 'intel.sh history' \
  'never' 'STOP and ask the' 'Do not edit code.' 'spec-reviewer' 'mode: plan' '`review` skill' \
  'at most 2 rounds for T2 and 3 for T3' 'Do not inflate the tier' 'T1: targeted tests' 'No subagents.' \
  'round limit or owner decision')" "work-package: classification, intake, plan checks, gates"
has "$SH/wp-template.md" 'no push, pull request, release or package publishing' && got=ok || got=missing
check ok "$got" "WP template forbids publishing without the owner"
(( $(wc -l <"$WP") <= 170 )) && got=ok || got="$(wc -l <"$WP") lines"; check ok "$got" "work-package stays under 170 lines"
tpl="$T/wp.md"; sed -n '/^```markdown$/,/^```$/p' "$SH/wp-template.md" | sed '1d;$d' >"$tpl"
has "$WP" 'skill-context.sh wp-template' && [[ "$("$P/scripts/skill-context.sh" wp-template | head -1)" == '```markdown' ]] && got=ok || got=missing
check ok "$got" "work-package injects the shared WP template"
n_acc="$(grep -n '^## Acceptance criteria' "$tpl" | cut -d: -f1)"; n_ctx="$(grep -n '^## Context' "$tpl" | cut -d: -f1)"
(( n_acc > 0 && n_acc <= 26 )) && got=ok || got="line $n_acc"; check ok "$got" "WP template: acceptance criteria start inside the 30 lines re-injected after compaction"
(( n_ctx > n_acc )) && grep -n '^## ' "$tpl" | tail -3 | grep -q '## Log' && got=ok || got=order; check ok "$got" "WP template: Context and Log after the criteria, Log last"
sed -n 5p "$tpl" | grep -q '^Progress:' && got=ok || got=missing; check ok "$got" "WP template: Progress line in the header"

# ---------------- review loop ----------------
RV="$SK/review/SKILL.md"; RP="$SH/review-policy.md"
check ok "$(all "$RP" '**2 rounds for T2, 3 rounds for T3**' 'covers only the changes since the reviewed snapshot' 'is verification, not a round' \
  'DECIDE' 'FIX NOW' 'RECORD' 'REJECT' 'round limit reached' 'same test failed twice' 'new P0/P1' 'Never apply every suggestion blindly' \
  'fresh context' 'never merged or re-ranked' 'Staging, committing, pushing')" "review policy: limits, triage, stop conditions, owner actions"
check ok "$(all "$RP" 'do not revert it yourself' 'a fix that would' 'Use only the category names' 'never compute it' 'never work around')" "review policy: out-of-scope edits are owner decisions; fixed snapshot and category sources"
check ok "$(all "$RP" 'every P2, including P2 `unrequested-behaviour`' 'Rounds so far = the larger' 'outside the repository or git-ignored')" "review policy: P2 recorded, rounds survive refused writes, evidence outside the tree"
check ok "$(all "$RV" 'change-patch <base>' 'checked-out HEAD')" "review skill: patches for delta reviews; ranges read the checked-out head"
check ok "$(all "$RV" 'spec-reviewer' 'reviewer' 'security-reviewer' 'qa-runner' 'in one message' 'Never paste one reviewer' \
  'Do not start another round on your own' 'review-policy.md' 'A range review is review-only' 'Never stage, commit, push' 'skill-context.sh snapshot` (never')" "review skill: parallel independent axes, bounded, review-only ranges"
grep -c 'qa-runner` once' "$RV" | grep -q '^1$' && got=ok || got=bad; check ok "$got" "review skill: qa-runner once"
(( $(wc -l <"$RV") <= 100 )) && got=ok || got=long; check ok "$got" "review skill stays under 100 lines"

# ---------------- agents ----------------
SR="$AG/spec-reviewer.md"; RW="$AG/reviewer.md"; QA="$AG/qa-runner.md"; SC="$AG/security-reviewer.md"
check ok "$(all "$SR" 'unrequested-behaviour' 'even inside an in-scope file' 'Plan mode' 'You never approve' 'MET' 'PARTIAL' \
  '## Hand-back (at most 15 lines' 'REPORT: <path>' '— snapshot <id>' 'do not report them' 'Never overwrite a file')" "spec-reviewer contract"
check ok "$(all "$RW" 'ENGINEERING' 'test-weak' 'no more than three' 'overengineering' 'duplication' 'dead-code' 'Regressions' \
  'documented standards win' 'Do not run the full regression suite' '## Hand-back (at most 15 lines' 'REPORT: <path>' '— snapshot <id>')" "reviewer contract (engineering axis)"
check ok "$(all "$SR" 'without' '`## Plan` section (created before 0.2.0)')" "spec-reviewer traces 0.1.x WPs to goal and criteria"
! grep -q '^ACCEPTANCE' "$RW" && got=ok || got=present; check ok "$got" "reviewer no longer maps acceptance criteria (spec-reviewer does)"
check ok "$(all "$QA" 'intel.sh" fbpa' '--mode refactor' 'impact-suggested (not authorized)' 'Never run them yourself' 'INVALID')" "qa-runner: fbpa/pinned stage, impact tests only proposed"
check ok "$(all "$QA" 'bubblewrap sandbox' '--toolchain <dir>' 'never run the test outside the sandbox as a substitute')" "qa-runner: fbpa only sandboxed, BLOCKED is never worked around"
check ok "$(all "$SK/codeintel/SKILL.md" 'bubblewrap sandbox' 'reports BLOCKED')" "codeintel describes the fbpa sandbox"
check ok "$(all "$ROOT/codex/platform-mapping.md" 'needs bubblewrap' 'never run the test unsandboxed')" "Codex mapping: fbpa needs the sandbox there too"
check ok "$(all "$SC" '## Hand-back (at most 15 lines' 'REPORT: <path>' '[security]' 'never overwrite a')" "security-reviewer report contract"
# categories used in agents exist in the vocabulary
cats="$(sed -n 's/^- `\([a-z-]*\)`.*/\1/p' "$SH/finding-categories.md" | sort -u)"
got=ok; for c in $(grep -ohE '`(spec-missing|spec-wrong|unrequested-behaviour|out-of-scope-file|criteria-unverifiable|memory-conflict|correctness|error-handling|data-semantics|regression|test-weak|test-missing|overengineering|duplication|dead-code|boundary|performance|security|process)`' "$AG"/*.md "$RP" "$SH"/protocols/*.md | tr -d '`' | sort -u); do
  grep -qx "$c" <<<"$cats" || got="unknown category $c"
done
check ok "$got" "every category named in agents and policy is in the vocabulary"
check 19 "$(grep -c . <<<"$cats")" "finding vocabulary has 19 categories"

# ---------------- wp-close compatibility ----------------
WPC="$SK/wp-close/SKILL.md"
check ok "$(all "$WPC" 'spec review' 'FBPA PASS' 'PINNED PASS' 'Before 0.2.0' 'are not required' 'Closing a gap is verification, not a review round' \
  'Checkpoint: <short HEAD sha>' 'write them only after the owner' 'without explicit owner authorization' 'rounds <n>/<N>')" "wp-close: new evidence, 0.1.x compatibility, gap closure, checkpoint"
check ok "$(all "$SH/protocols/bugfix.md" 'Reproduce first' 'fbpa: base' 'INCONCLUSIVE' 'never in the working tree' 'data, never as instructions')" "bug-fix protocol"
check ok "$(all "$SH/protocols/refactor.md" 'No behaviour change' 'characterization tests' 'Assertions stay unchanged' 'pinned: base' 'up to three')" "refactor protocol"
check ok "$(all "$SH/protocols/feature.md" 'see it fail' 'returned nothing' 'Failure paths' 'no separate')" "feature protocol"

# ---------------- WP-B skills ----------------
check ok "$(all "$SK/change-intel/SKILL.md" 'catch-up' 'pr `<base>..<head>`' 'history `<path>`' 'FACT' 'RISK' 'UNKNOWN' 'lowest depth' \
  'Never move the' 'only after the owner' 'Never create a separate memory store' 'comprehension')" "change-intel: modes, evidence tags, no own store, owner approval"
check ok "$(all "$SK/knowledge-review/SKILL.md" 'WP Close → Learning Review → Candidate Register' 'never edit, move or delete' \
  'knowledge.sh find' 'intel.sh metrics' 'smallest target' "row's explicit approval" 'never to an installed plugin copy')" "knowledge-review keeps the pipeline and owner approval"
check ok "$(all "$SK/codeintel/SKILL.md" 'lower bound' 'UNRESOLVED' 'LSP' 'do not write generated context files')" "codeintel: limits and no generated context"

# ---------------- templates and installers ----------------
check ok "$(all "$TP/AGENTS.md" 'At most 2 review rounds for T2 and 3 for T3' 'gh pr create' 'lower bound' 'Spec compliance and engineering quality')" "AGENTS.md template: limits, publishing, code intelligence"
check ok "$(all "$TP/CLAUDE.md" '@AGENTS.md' '/ailab-engineering:review' 'SCRATCH_CLEANUP_REDIRECT')" "CLAUDE.md template"
check ok "$(all "$TP/docs/project/ARCHITECTURE.md" '```ailab-boundaries' 'intel.sh arch-check')" "ARCHITECTURE.md template"
D="$T/proj"; mkdir -p "$D" && git -C "$D" init -q
bash "$ROOT/install/init-project.sh" "$D" --dry-run >"$T/o" 2>&1; [[ ! -e "$D/AGENTS.md" ]] && got=ok || got=wrote; check ok "$got" "init-project --dry-run writes nothing"
bash "$ROOT/install/init-project.sh" "$D" >"$T/o" 2>&1; [[ -f "$D/AGENTS.md" && ! -e "$D/docs/project/ARCHITECTURE.md" ]] && got=ok || got=bad; check ok "$got" "init-project: ARCHITECTURE.md only on request"
bash "$ROOT/install/init-project.sh" "$D" --with-architecture >"$T/o" 2>&1; [[ -f "$D/docs/project/ARCHITECTURE.md" ]] && got=ok || got=missing; check ok "$got" "init-project --with-architecture"
echo custom >"$D/AGENTS.md"; bash "$ROOT/install/init-project.sh" "$D" --with-architecture >"$T/o" 2>&1
check custom "$(cat "$D/AGENTS.md")" "init-project never overwrites"
check 2 "$(bash "$ROOT/install/init-project.sh" "$D" --bogus >/dev/null 2>&1; echo $?)" "init-project rejects unknown options"
(cd "$D" && git status --porcelain | grep -q '^A') && got=staged || got=ok; check ok "$got" "init-project stages nothing"

# ---------------- backward compatibility: a WP written by the 0.1.7 template ----------------
V1="$T/v1"; mkdir -p "$V1/.claude/wp" && git -C "$V1" init -q && echo x >"$V1/f.py" && git -C "$V1" add -- f.py && git -C "$V1" -c user.email=t@t -c user.name=t commit -qm base
v1base="$(git -C "$V1" rev-parse --short HEAD)"
sed -e "s/\$0/OLD-1/g" -e 's/^Status: .*/Status: IN PROGRESS/' -e 's/^Tier: .*/Tier: T2   Created: 2026-11-01   Repo HEAD at start: '"$v1base"'/' \
  -e 's#^<where results.*#/tmp/ev/OLD-1#' "$BL/wp-template.md" >"$V1/.claude/wp/OLD-1.md"
[[ -s "$BL/wp-template.md" ]] && grep -q '^Tier: T2' "$V1/.claude/wp/OLD-1.md" && got=ok || got=missing
check ok "$got" "the frozen 0.1.7 WP template is present"
printf '%s\n' '- 2026-11-02 reviewer: PASS WITH NOTES — snapshot abc123 — /tmp/ev/OLD-1/review-abc.md' >>"$V1/.claude/wp/OLD-1.md"
echo OLD-1 >"$V1/.claude/wp/ACTIVE"; echo y >>"$V1/f.py"
out="$(cd "$V1" && "$P/scripts/skill-context.sh" review)"
[[ "$out" == *"WP base (Repo HEAD at start): $v1base"* && "$out" == *"Tier: T2"* && "$out" == *"review rounds so far ---"*none* && "$out" == *"f.py"* ]] && got=ok || got="$out"
check ok "$got" "0.1.7-format WP: review context finds tier, base and diff, no rounds"
out="$(cd "$V1" && "$P/scripts/skill-context.sh" wp-close)"; [[ "$out" == *"Active WP: OLD-1"* && "$out" == *"Snapshot: "* ]] && got=ok || got=bad
check ok "$got" "0.1.7-format WP: wp-close context unchanged"
m="$(AILAB_PYTHON="${AILAB_PYTHON:-python3}" "$P/scripts/intel.sh" metrics --json "$V1" 2>/dev/null)"
[[ "$m" == *'"tier": "T2"'* && "$m" == *'"rounds": 0'* ]] && got=ok || got="$m"
check ok "$got" "0.1.7-format WP: metrics reads tier, no rounds"
has "$WPC" 'header has no `Type:` line was created under 0.1.x' && got=ok || got=missing; check ok "$got" "wp-close keeps 0.1.x gates for WPs created before 0.2.0 (by header)"

# ---------------- install and rollback procedure (0.2.1) ----------------
IJ="$ROOT/docs/INSTALL-HOST.md"; VI="$ROOT/install/verify-install.sh"
instr() { awk '/^```/ {on = !on; next} on || /^\|/' "$1"; }   # what the owner runs: fenced code and table rows
instr "$IJ" >"$T/ij.code"
! grep -En 'mv +"?\$\{?EGM' "$T/ij.code" && got=ok || got=renames; check ok "$got" "INSTALL-HOST never renames the mounted checkout"
! grep -En 'plugin +marketplace +remove|plugin +(uninstall|remove) ' "$T/ij.code" | grep -v -- '--keep-data' | grep -q . && got=ok || got=deletes-data
check ok "$got" "INSTALL-HOST never removes the marketplace or uninstalls without --keep-data (both delete plugin data)"
sync_lines() { grep -E 'rsync .*"\$(\{EGM:\?\}|EGM)/"( |$)' "$T/ij.code" | grep -v -- '--dry-run'; }
bad="$(sync_lines | grep -vE -- '--delete --checksum --exclude=/\.git ' ; sync_lines | grep -vF 'test -f "${EGM:?}/plugins/ailab-engineering/.claude-plugin/plugin.json" && rsync' ; true)"
[[ -z "$bad" && "$(sync_lines | wc -l)" -ge 2 ]] && got=ok || got="$bad"
check ok "$got" "every in-place sync of \$EGM uses --delete --checksum --exclude=/.git and refuses an unset or wrong \$EGM"
! grep -E 'rsync .*"\$EGM/"( |$)' "$T/ij.code" | grep -q . && got=ok || got=unguarded
check ok "$got" "no rsync writes to an unguarded \$EGM"
grep -qE "^docker exec \"\\\$C\" awk -v p=\"\\\$CPATH\" '.*' /proc/self/mountinfo$" "$T/ij.code" && got=ok || got=missing
check ok "$got" "INSTALL-HOST has the mountinfo discovery line the upgrade test runs"
all "$P/skills/wp-close/SKILL.md" 'the test outside the sandbox never satisfies it' >/dev/null && \
  all "$P/skills/work-package/SKILL.md" 'never run the test unsandboxed instead' >/dev/null && \
  all "$ROOT/codex/skills/ailab-work-package/SKILL.md" 'test unsandboxed instead' >/dev/null && \
  all "$P/shared/protocols/bugfix.md" 'never run the test outside the sandbox instead' >/dev/null && \
  all "$P/shared/protocols/refactor.md" 'outside the sandbox instead' >/dev/null && got=ok || got=missing
check ok "$got" "a BLOCKED fbpa goes to the owner, never to an unsandboxed run (wp-close, work-package, Codex, protocols)"
! grep -E 'install\.sh.*--with-deny-rules' "$T/ij.code" | grep -qv -- "--expect-version $(sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' "$P/.claude-plugin/plugin.json")" && got=ok || got=missing
check ok "$got" "every documented install.sh run checks --expect-version"
all "$IJ" '/proc/self/mountinfo' 'docker inspect' '--print-manifest' 'VERIFY-INSTALL: PASS' 'Roll back to 0.1.7' 'NOT VERIFIED' >/dev/null && got=ok || got=missing
check ok "$got" "INSTALL-HOST discovers mounts, verifies contents and documents rollback"
grep -q "ailab-engineering $(sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' "$P/.claude-plugin/plugin.json") on example host" "$IJ" && got=ok || got=stale
check ok "$got" "INSTALL-HOST title names the plugin version"
got=ok; for h in $(grep -o 'scripts/[a-z-]*\.sh' "$P/hooks/hooks.json"); do [[ -x "$P/$h" ]] || got="$h not executable"; done
check ok "$got" "every hook script is executable (hooks.json runs them directly)"
bash "$VI" --print-manifest "$ROOT" >"$T/m1"; bash -s -- --print-manifest "$ROOT" <"$VI" >"$T/m2"
cmp -s "$T/m1" "$T/m2" && [[ -s "$T/m1" ]] && ! grep -q '\.git/' "$T/m1" && got=ok || got=differs
check ok "$got" "verify-install manifest: same from a file and from stdin, .git excluded"
grep -q '^[0-9a-f]\{64\} x  plugins/ailab-engineering/scripts/guard-bash.sh$' "$T/m1" && got=ok || got=missing
check ok "$got" "manifest records the executable bit"
check 2 "$(bash -s -- --expect-version 1 <"$VI" >/dev/null 2>&1; echo $?)" "verify-install from stdin needs --source"
mkdir -p "$T/vbin"; ver0="$(sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' "$P/.claude-plugin/plugin.json")"
cat >"$T/vbin/claude" <<'EOF2'
#!/usr/bin/env bash
case "$* " in
  "plugin list --json "*) cat "$VSTUB" ;;
  "plugin marketplace list --json "*) printf '[{"name":"ailab","source":"directory","path":"%s"}]\n' "$VROOT" ;;
  "--version "*) echo "2.0.0 (Claude Code)" ;;
esac
EOF2
chmod +x "$T/vbin/claude"
vrun() { VSTUB="$T/pl.json" VROOT="$ROOT" PATH="$T/vbin:$PATH" bash "$VI" "$@" >"$T/vo" 2>&1; echo $?; }
printf '[{"id":"ailab-engineering@ailab","version":"0.1.7","enabled":true,"readFromFolder":"%s/plugins/ailab-engineering","folderVersion":"%s"}]\n' "$ROOT" "$ver0" >"$T/pl.json"
check 0 "$(vrun --expect-version "$ver0" --manifest "$T/m1")" "verify-install PASS: contents, folder, version, marketplace"
grep -q "version Claude Code runs: $ver0 (recorded at install: 0.1.7)" "$T/vo" && got=ok || got="$(cat "$T/vo")"; check ok "$got" "verify-install reports the running version, not the recorded one"
check 1 "$(vrun --expect-version 9.9.9 --skip-contents)" "verify-install FAIL on another version"
sed 's/^\([0-9a-f]*\) x  plugins\/ailab-engineering\/scripts\/guard-bash.sh$/\1 -  plugins\/ailab-engineering\/scripts\/guard-bash.sh/' "$T/m1" >"$T/m3"
check 1 "$(vrun --expect-version "$ver0" --manifest "$T/m3")" "verify-install FAIL when an executable bit differs"
grep -q 'guard-bash.sh' "$T/vo" && got=ok || got="$(cat "$T/vo")"; check ok "$got" "drift report names the file"
printf '[{"id":"ailab-engineering@ailab","version":"%s","enabled":true}]\n' "$ver0" >"$T/pl.json"
check 3 "$(vrun --expect-version "$ver0" --skip-contents)" "older Claude Code without readFromFolder: NOT VERIFIED, not PASS"
printf '[{"id":"ailab-engineering@ailab","version":"%s","enabled":true,"readFromFolder":"/elsewhere/plugins/ailab-engineering","folderVersion":"%s"}]\n' "$ver0" "$ver0" >"$T/pl.json"
check 1 "$(vrun --expect-version "$ver0" --skip-contents)" "verify-install FAIL when Claude Code reads another folder"

# ---------------- release bookkeeping ----------------
ver="$(sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' "$P/.claude-plugin/plugin.json")"
head -5 "$ROOT/CHANGELOG.md" | grep -q "^## $ver " && got=ok || got="plugin $ver not at the top of CHANGELOG"; check ok "$got" "version and CHANGELOG agree"
! find "$ROOT" -path "$ROOT/.git" -prune -o \( -name '*.bak' -o -name '*.bak.*' -o -name '*.bak-*' \) -print | grep -q . && got=ok || got=present
check ok "$got" "no backup files in the source tree"

echo "contract tests  passed: $PASS  failed: $FAIL"
(( FAIL == 0 ))
