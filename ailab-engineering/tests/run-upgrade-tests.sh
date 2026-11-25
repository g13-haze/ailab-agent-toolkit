#!/usr/bin/env bash
# End-to-end upgrade and rollback test of the docs/INSTALL-HOST.md procedure with the
# REAL claude CLI, in throwaway HOMEs. No model is called.
#
#   bash tests/run-upgrade-tests.sh
#
# Emulates example host: a host checkout ($EGM) and a container that bind-mounts it read-only and
# runs its own Claude Code (a private mount namespace with `mount --bind`, the mechanism
# Docker uses for bind mounts; needs root and unshare). Container-side checks use the
# release's verify-install.sh through stdin, as the guide does with docker exec -i. Checks:
#   - replacing the checkout with mv (the 0.2.0 guide) leaves the container on the old
#     release; the manifest diff, verify-install and /proc/self/mountinfo all show it, and
#     the stale tree's installer refuses --expect-version;
#   - updating the checkout in place (rsync --delete --checksum) gives host and container the
#     new release: equal manifests, install.sh --expect-version from the read-only mount (with
#     no fbpa sandbox in the container, as under Docker's default seccomp profile), Claude
#     Code's own plugin report; the plugin data directory survives;
#   - rolling back in place returns both to the old release, verified the same way.
# Without the claude CLI, rsync or root/unshare the affected parts print NOT VERIFIED.
# Nothing outside a temporary directory is touched.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
PASS=0; FAIL=0; NV=0
check() { if [[ "$1" == "$2" ]]; then PASS=$((PASS+1)); else FAIL=$((FAIL+1)); printf 'FAIL: expected %s got %s  %s\n' "$1" "$2" "$3"; fi; }
nv() { NV=$((NV+1)); echo "NOT VERIFIED: $*"; }
for t in claude rsync python3; do command -v "$t" >/dev/null 2>&1 || { nv "$t not installed: upgrade test skipped"; echo "upgrade tests  passed: 0  failed: 0  not verified: $NV"; exit 0; }; done
T="$(mktemp -d /tmp/ailab-upgrade-test.XXXXXX)"
trap 'rm -rf -- "$T"' EXIT
REL="$T/releases"; NEW="$REL/new"; OLD="$REL/old"; EGM="$T/srv/ailab-engineering"; VIEW="$T/container/opt/ailab-engineering"
mkdir -p "$NEW" "$OLD" "$(dirname "$EGM")" "$VIEW" "$T/home" "$T/chome"
# the new release: this tree as a release contains it (git-listed files only: no caches or other local
# leftovers, which a release archive does not have either); the old one: tag baseline-0.1.7 when git
# history is here, else this tree as 0.0.1
if git -C "$ROOT" rev-parse -q --verify HEAD >/dev/null 2>&1; then
  (cd "$ROOT" && git ls-files -co --exclude-standard -z | tar --null -T - -cf -) | tar -C "$NEW" -xf -
else
  tar -C "$ROOT" --exclude=./.git --exclude=__pycache__ -cf - . | tar -C "$NEW" -xf -
fi
if git -C "$ROOT" rev-parse -q --verify baseline-0.1.7 >/dev/null 2>&1; then
  git -C "$ROOT" archive baseline-0.1.7 | tar -x -C "$OLD"; old_kind="0.1.7 (tag baseline-0.1.7)"
else
  tar -C "$NEW" -cf - . | tar -C "$OLD" -xf -
  sed -i 's/"version": *"[^"]*"/"version": "0.0.1"/' "$OLD/plugins/ailab-engineering/.claude-plugin/plugin.json"; old_kind="0.0.1 (this tree, version changed; no git history in an archive)"
fi
ver() { sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' "$1/plugins/ailab-engineering/.claude-plugin/plugin.json" | head -1; }
NEWV="$(ver "$NEW")"; OLDV="$(ver "$OLD")"
echo "old release: $old_kind; new release: $NEWV"
V="$NEW/install/verify-install.sh"
bash "$V" --print-manifest "$NEW" >"$REL/new.manifest"; bash "$V" --print-manifest "$OLD" >"$REL/old.manifest"
check 0 "$(grep -vc ' [x-]  \| l  ' "$REL/new.manifest")" "manifest lines carry the executable flag"
grep -q '^[0-9a-f]\{64\} x  plugins/ailab-engineering/scripts/guard-bash.sh$' "$REL/new.manifest" && got=ok || got=missing
check ok "$got" "manifest records guard-bash.sh as executable"
folder_version() { HOME="$1" claude plugin list --json 2>/dev/null | python3 -I -c 'import json,sys
p=[x for x in json.load(sys.stdin) if x.get("id")=="ailab-engineering@ailab"]; print(p[0].get("folderVersion","") if p else "absent")'; }

# 1. the existing installation: old release on the host
rsync -a --delete --checksum "$OLD/" "$EGM/"
inode0="$(stat -c %i "$EGM")"
HOME="$T/home" claude plugin marketplace add "$EGM" >/dev/null 2>&1 && HOME="$T/home" claude plugin install ailab-engineering@ailab --scope user >/dev/null 2>&1
check "$OLDV" "$(folder_version "$T/home")" "host: Claude Code runs the old release before the update"

# 2. host + container in one mount namespace
if [[ "$(id -u)" == 0 ]] && command -v unshare >/dev/null 2>&1 && unshare -m true 2>/dev/null; then
  # the guide's own discovery line (step 2): which directory the container's mount is attached to
  AWKP="$(sed -n "s|^docker exec \"\$C\" awk -v p=\"\$CPATH\" '\(.*\)' /proc/self/mountinfo\$|\1|p" "$NEW/docs/INSTALL-HOST.md")"
  [[ -n "$AWKP" ]] && got=ok || got=missing; check ok "$got" "the guide's mountinfo discovery line (step 2) is found"
  out="$(unshare -m --propagation private bash -s "$EGM" "$VIEW" "$T" "$NEW" "$NEWV" "$OLDV" "$V" "$OLD" "$AWKP" <<'NS'
EGM="$1"; VIEW="$2"; T="$3"; NEW="$4"; NEWV="$5"; OLDV="$6"; V="$7"; OLD="$8"; AWKP="$9"; REL="$T/releases"
# the container: a read-only bind mount (compose: - $EGM:/opt/ailab-engineering:ro) and its own HOME
{ mount --bind "$EGM" "$VIEW" && mount -o remount,bind,ro "$VIEW"; } || { echo "RESULT bind=failed"; exit 0; }
touch "$VIEW/x" 2>/dev/null && echo "RESULT ro=no" || echo "RESULT ro=yes"
HOME="$T/chome" claude plugin marketplace add "$VIEW" >/dev/null 2>&1 && HOME="$T/chome" claude plugin install ailab-engineering@ailab --scope user >/dev/null 2>&1
mkdir -p "$T/chome/.claude/plugins/data/ailab-engineering-ailab" && echo kept >"$T/chome/.claude/plugins/data/ailab-engineering-ailab/guard.log"
echo "RESULT container_before=$(HOME="$T/chome" claude plugin list --json | python3 -I -c 'import json,sys; print([x for x in json.load(sys.stdin) if x["id"]=="ailab-engineering@ailab"][0]["folderVersion"])')"
# container-side commands as the guide runs them: the release's verify-install.sh through stdin
cv() { HOME="$T/chome" bash -s -- "$@" <"$V"; }
mountroot() { awk -v p="$VIEW" "$AWKP" /proc/self/mountinfo | sed -n "s|^mount point: $VIEW  attached to: ||p"; }
echo "RESULT attached_before=$(mountroot)"

# A. the 0.2.0 guide: replace the checkout with mv
mv "$EGM" "$EGM.pre" && mkdir "$EGM" && cp -a "$NEW/." "$EGM/"
echo "RESULT mv_host=$(sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' "$EGM/plugins/ailab-engineering/.claude-plugin/plugin.json" | head -1)"
echo "RESULT mv_container=$(sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' "$VIEW/plugins/ailab-engineering/.claude-plugin/plugin.json" | head -1)"
echo "RESULT mv_attached=$(mountroot)"
cv --print-manifest "$VIEW" | diff -q "$REL/new.manifest" - >/dev/null; echo "RESULT mv_diff_rc=$?"
cv --expect-version "$NEWV" --source "$VIEW" --skip-contents >"$T/mv-verify.txt" 2>&1; echo "RESULT mv_verify_rc=$?"
HOME="$T/chome" bash "$VIEW/install/install.sh" --expect-version "$NEWV" >"$T/mv-install.txt" 2>&1; echo "RESULT mv_install_rc=$?"
echo "RESULT mv_container_after_install=$(HOME="$T/chome" claude plugin list --json | python3 -I -c 'import json,sys; print([x for x in json.load(sys.stdin) if x["id"]=="ailab-engineering@ailab"][0]["folderVersion"])')"
# undo the mv (no deletion)
mv "$EGM" "$EGM.mv-attempt" && mv "$EGM.pre" "$EGM"

# B. the 0.2.1 guide: update the mounted checkout in place
rsync -a --delete --checksum --exclude=/.git "$NEW/" "$EGM/"
cv --print-manifest "$VIEW" | diff "$REL/new.manifest" - >"$T/inplace-diff.txt"; echo "RESULT inplace_diff_rc=$?"
# as in agent-long under Docker's default seccomp profile: user namespaces refused (an outer bubblewrap
# with --disable-userns), else the sandbox switched off
if /usr/bin/bwrap --dev-bind / / --unshare-user --disable-userns -- true 2>/dev/null; then
  NOSB=(/usr/bin/bwrap --dev-bind / / --unshare-user --disable-userns --); echo "RESULT nosandbox=user namespaces refused"
else
  NOSB=(env AILAB_SANDBOX_DISABLE=1); echo "RESULT nosandbox=AILAB_SANDBOX_DISABLE"
fi
"${NOSB[@]}" env HOME="$T/chome" bash "$VIEW/install/install.sh" --expect-version "$NEWV" >"$T/inplace-install.txt" 2>&1; echo "RESULT inplace_install_rc=$?"
cv --expect-version "$NEWV" --source "$VIEW" --manifest "$REL/new.manifest" >"$T/inplace-verify.txt" 2>&1; echo "RESULT inplace_verify_rc=$?"
echo "RESULT attached_after=$(mountroot)"
[[ -f "$T/chome/.claude/plugins/data/ailab-engineering-ailab/guard.log" ]] && echo "RESULT data_kept=yes" || echo "RESULT data_kept=no"

# C. rollback in place, verified with the new release's tools
rsync -a --delete --checksum --exclude=/.git "$OLD/" "$EGM/"
cv --print-manifest "$VIEW" | diff -q "$REL/old.manifest" - >/dev/null; echo "RESULT rollback_diff_rc=$?"
cv --expect-version "$OLDV" --source "$VIEW" --manifest "$REL/old.manifest" >"$T/rollback-verify.txt" 2>&1; echo "RESULT rollback_verify_rc=$?"
NS
)"
  r() { printf '%s\n' "$out" | sed -n "s/^RESULT $1=//p"; }
  if [[ "$(r bind)" == failed ]]; then nv "bind mount failed: container emulation skipped"; else
  check yes "$(r ro)" "container: the mount is read-only"
  check "$OLDV" "$(r container_before)" "container: runs the old release before the update"
  check "$EGM" "$(r attached_before)" "mountinfo: the container is attached to \$EGM"
  check "$NEWV $OLDV" "$(r mv_host) $(r mv_container)" "mv procedure: host shows the new release, the container still the old one (stale bind mount)"
  check "$EGM.pre" "$(r mv_attached)" "mountinfo shows the container attached to the renamed directory"
  check 1 "$(r mv_diff_rc)" "manifest diff shows the stale container"
  check 1 "$(r mv_verify_rc)" "verify-install (stdin) fails on the stale container"
  grep -q "source version is '$OLDV', expected $NEWV" "$T/mv-verify.txt" && got=ok || got="$(cat "$T/mv-verify.txt")"
  check ok "$got" "verify-install names the stale version"
  [[ "$(r mv_install_rc)" != 0 && "$(r mv_container_after_install)" == "$OLDV" ]] && got=ok || got="rc=$(r mv_install_rc) $(tail -3 "$T/mv-install.txt")"
  check ok "$got" "the stale tree's installer refuses --expect-version $NEWV and changes nothing"
  if [[ "$OLDV" == 0.1.7 ]]; then want='unknown option: --expect-version'; else want="this tree is version '$OLDV', expected $NEWV"; fi
  grep -qF -- "$want" "$T/mv-install.txt" && got=ok || got="$(tail -3 "$T/mv-install.txt")"
  check ok "$got" "the stale tree's installer says what the guide tells the owner to expect"
  check 0 "$(r inplace_diff_rc)" "in place: the container's manifest equals the new release"
  check 0 "$(r inplace_install_rc)" "in place: install.sh --expect-version from the read-only mount"
  grep -q "Claude Code runs ailab-engineering $NEWV from $VIEW" "$T/inplace-install.txt" && got=ok || got="$(tail -15 "$T/inplace-install.txt")"
  check ok "$got" "in place: install.sh verified the container runs the new release"
  grep -q 'fbpa sandbox unavailable' "$T/inplace-install.txt" && got=ok || got="$(grep -E 'intel|warn' "$T/inplace-install.txt")"
  check ok "$got" "in place: without a sandbox in the container ($(r nosandbox)) the installer completes and warns that fbpa is BLOCKED there"
  check 0 "$(r inplace_verify_rc)" "in place: verify-install PASS in the container (contents and Claude Code report)"
  grep -q "version Claude Code runs: $NEWV" "$T/inplace-verify.txt" && grep -q 'contents equal the reference release' "$T/inplace-verify.txt" && got=ok || got="$(cat "$T/inplace-verify.txt")"
  check ok "$got" "in place: verified by contents and by Claude Code"
  check "$EGM" "$(r attached_after)" "mountinfo: still attached to \$EGM"
  check yes "$(r data_kept)" "the container's plugin data directory survives the update"
  check 0 "$(r rollback_diff_rc)" "rollback in place: the container's manifest equals the old release"
  check 0 "$(r rollback_verify_rc)" "rollback in place: verify-install PASS for $OLDV in the container"
  check "$inode0" "$(stat -c %i "$EGM")" "the mounted checkout kept its inode (same directory throughout)"
  fi
else
  nv "container emulation needs root and unshare (mount namespaces)"
fi

# 3. host: in-place update, then the real installer with --expect-version
rsync -a --delete --checksum --exclude=/.git "$NEW/" "$EGM/"
HOME="$T/home" bash "$EGM/install/install.sh" --expect-version "$NEWV" >"$T/install.txt" 2>&1; rc=$?
check 0 "$rc" "install.sh --expect-version $NEWV on the updated host checkout"
grep -q "Claude Code runs ailab-engineering $NEWV from $EGM" "$T/install.txt" && got=ok || got="$(tail -15 "$T/install.txt")"
check ok "$got" "install.sh verified the running release"
check "$NEWV" "$(folder_version "$T/home")" "host: Claude Code runs the new release"
[[ -z "$(find "$EGM" -name __pycache__ -not -path "$EGM/.git/*")" ]] && got=ok || got="$(find "$EGM" -name __pycache__)"
check ok "$got" "install.sh's test run leaves no __pycache__ in the checkout"
HOME="$T/home" bash "$V" --expect-version "$NEWV" --source "$EGM" --reference "$NEW" >/dev/null 2>&1; check 0 "$?" "host: verify-install PASS after the update (contents unchanged by install.sh)"

# 4. host rollback in place, verified with the new release's tools
rsync -a --delete --checksum --exclude=/.git "$OLD/" "$EGM/"
check "$OLDV" "$(folder_version "$T/home")" "host rollback: Claude Code runs the old release"
HOME="$T/home" bash "$V" --expect-version "$OLDV" --source "$EGM" --reference "$OLD" >"$T/rb.txt" 2>&1; check 0 "$?" "host rollback: verify-install PASS (contents equal the old release)"
check 1 "$(HOME="$T/home" bash "$V" --expect-version "$NEWV" --source "$EGM" --reference "$NEW" >/dev/null 2>&1; echo $?)" "after rollback, verifying the new release fails"
# a stray file, a changed file and a lost executable bit are each reported
rsync -a --delete --checksum --exclude=/.git "$NEW/" "$EGM/"
echo stray >"$EGM/stray.txt"; echo '# edited' >>"$EGM/README.md"; chmod -x "$EGM/plugins/ailab-engineering/scripts/guard-bash.sh"
HOME="$T/home" bash "$V" --expect-version "$NEWV" --source "$EGM" --reference "$NEW" >"$T/drift.txt" 2>&1; check 1 "$?" "verify-install fails on drift"
grep -q 'extra or changed:   stray.txt' "$T/drift.txt" && grep -q 'missing or changed: README.md' "$T/drift.txt" && grep -q 'missing or changed: plugins/ailab-engineering/scripts/guard-bash.sh' "$T/drift.txt" && got=ok || got="$(cat "$T/drift.txt")"
check ok "$got" "drift report names the extra file, the changed file and the lost executable bit"
# --checksum: a same-size, same-mtime change is still synced; .git is kept
rsync -a --delete --checksum --exclude=/.git "$NEW/" "$EGM/"
f="$EGM/plugins/ailab-engineering/scripts/lib/common.sh"; m="$(stat -c %y "$f")"; sed -i '1s/^#/%/' "$f"; touch -d "$m" "$f"
mkdir -p "$EGM/.git" && echo keep >"$EGM/.git/HEAD"; rsync -a --delete --checksum --exclude=/.git "$NEW/" "$EGM/"
cmp -s "$f" "$NEW/plugins/ailab-engineering/scripts/lib/common.sh" && got=ok || got=stale; check ok "$got" "rsync --checksum restores a same-size, same-mtime change"
[[ -f "$EGM/.git/HEAD" ]] && got=ok || got=deleted; check ok "$got" "in-place sync keeps the checkout's .git"
# stdin mode needs --source
check 2 "$(bash -s -- --expect-version "$NEWV" <"$V" >/dev/null 2>&1; echo $?)" "verify-install from stdin without --source is a usage error"

echo "upgrade tests  passed: $PASS  failed: $FAIL  not verified: $NV"
(( FAIL == 0 ))
