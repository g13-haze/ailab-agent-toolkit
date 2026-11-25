# Installing ailab-engineering 0.2.2 on example host — discover, update, verify, roll back

Owner-run procedure; no agent runs it. Steps 1–3 only read, test and copy into a new
release directory. From step 4 on, every change has a verification command and a way
back. Commands that depend on a check are chained with `&&`, so a failed check stops
them.

## What this guide relies on, and why the 0.2.0 guide was wrong

1. **A Docker bind mount is attached to a directory, not to a path.** The container
   keeps the directory that existed when it was created. The 0.2.0 guide renamed the
   checkout (`mv "$EGM" …`) and put a new tree at the same path. The host then saw the
   new release, but a container that bind-mounts the checkout kept the renamed old
   directory until it was recreated, and `docker inspect` still showed the original
   path. This guide updates the mounted directory **in place** (`rsync --delete
   --checksum` into the same directory) and verifies what each environment really sees.
2. **Claude Code runs a directory marketplace's plugin straight from the registered
   directory** (verified on Claude Code 2.1.326: `claude plugin list --json` reports
   `readFromFolder` and `folderVersion`; `version` there is only what was recorded at
   install time). Changing the files in the registered directory therefore changes the
   code that runs at the next session start. Neither an update nor a rollback needs an
   uninstall.
3. **`claude plugin marketplace remove ailab` and `claude plugin uninstall` (without
   `--keep-data`) delete `~/.claude/plugins/data/ailab-engineering-ailab/`**, which holds
   guard.log, receipts and leftovers (verified on 2.1.326). The 0.2.0 rollback used
   `uninstall`. This guide uses neither. If you ever need them, back that directory up
   first and use `uninstall --keep-data`.

Verification tools in the 0.2.2 release (both read-only):

- `install/verify-install.sh` prints PASS / FAIL / NOT VERIFIED for:
  - the source version;
  - the source contents against a release manifest, which includes the executable bits the hook commands need;
  - which folder and version Claude Code runs;
  - where the marketplace is registered.

  It is self-contained, so it also checks a 0.1.7 tree, and inside the container it runs through stdin (`docker exec -i … bash -s -- …`).
- `install/install.sh --expect-version 0.2.2` refuses to change anything unless the tree it runs from is 0.2.2, and verifies the result afterwards.

## Variables (set them in every shell you use; confirm each in step 2)

```bash
PKG=/path/to/acceptance-0.2.2/source             # "source" folder of the unpacked acceptance package
EGM=/srv/agents/tools/ailab-engineering       # host checkout (audits); the 0.1.7 README named /srv/agents/projects/...
C=agent-long                                     # also agent-fast if it runs Claude Code
CPATH=/opt/ailab-engineering                  # path inside the container (0.1.7 README)
CUSER=<user that runs Claude Code in the container>
REL=/srv/agents/releases/ailab-engineering       # release copies, outside $EGM, writable only by root
V="$REL/0.2.2/install/verify-install.sh"
TS=$(cat "$REL/TS" 2>/dev/null || date -u +%Y%m%dT%H%M%SZ)   # the same value in every later shell (saved in step 1)
```

## 1. Stage and test the release (changes nothing that runs)

Each release lives in its own directory. Never edit these copies; check each one
against its manifest before you use it.

```bash
install -d -m 0755 -o root -g root "${REL:?}" && echo "$TS" > "$REL/TS"
cd "${PKG:?}" && sha256sum -c ailab-engineering-0.2.2.tar.gz.sha256 && mkdir "$REL/0.2.2" && \
  tar --no-same-owner -xzf ailab-engineering-0.2.2.tar.gz -C "$REL/0.2.2" --strip-components=1
bash "$V" --print-manifest "$REL/0.2.2" > "$REL/0.2.2.manifest" && \
  diff "$PKG/ailab-engineering-0.2.2.manifest" "$REL/0.2.2.manifest" && echo RELEASE-0.2.2-OK
```

Test the release where it will run before anything goes live. A dry run checks
prerequisites, runs all tests and validates the plugin; it changes nothing.

```bash
bash "$REL/0.2.2/install/install.sh" --dry-run                  # on the host, as the user that runs Claude Code
docker exec "$C" test ! -e /tmp/ailab-0.2.2-test && docker cp "$REL/0.2.2" "$C:/tmp/ailab-0.2.2-test" && \
  docker exec -it -u "$CUSER" "$C" bash -lc 'bash /tmp/ailab-0.2.2-test/install/install.sh --dry-run'
docker exec -u 0 "$C" rm -r /tmp/ailab-0.2.2-test
```

Both must end with `dry run: stopping before any change`. Where the fbpa sandbox
cannot run (expected inside agent-long), the dry run says so in a `[warn]` line. The
tests that need the sandbox are then skipped and are NOT VERIFIED there.

## 2. Discover the real configuration (read-only; decides between steps 5A and 5B)

Host: what Claude Code runs now.

```bash
claude --version
claude plugin list --json | jq '.[] | select(.id=="ailab-engineering@ailab") | {enabled, version, folderVersion, readFromFolder}'
claude plugin marketplace list --json | jq '.[] | select(.name=="ailab")'
stat -c 'host      inode %i  %n' "$EGM"
findmnt -n -o TARGET -T "$EGM"                             # host mount point of the filesystem holding $EGM
git -C "$EGM" status --short 2>/dev/null | head          # local changes in a git checkout?
command -v rsync; bwrap --version                         # rsync for step 5A; bubblewrap 0.8+ for fbpa
```

Container: how the tree reaches it, and what it is really attached to.

```bash
docker inspect "$C" --format '{{range .Mounts}}{{.Type}}  {{.Source}} -> {{.Destination}}  rw={{.RW}}{{println}}{{end}}'
docker inspect "$C" --format 'user={{.Config.User}}  project={{index .Config.Labels "com.docker.compose.project"}}  service={{index .Config.Labels "com.docker.compose.service"}}  compose={{index .Config.Labels "com.docker.compose.project.config_files"}}'
docker exec "$C" awk -v p="$CPATH" '{ m = $5; if ((p == m || index(p, m "/") == 1 || m == "/") && length(m) >= length(b)) { b = m; r = $4 } } END { if (b == "/") print "mount point: / (the container own filesystem)"; else print "mount point:", b, " attached to:", r substr(p, length(b) + 1) }' /proc/self/mountinfo
docker exec "$C" stat -c 'container inode %i  %n' "$CPATH"
docker exec -i -u "$CUSER" "$C" bash -l -s -- --expect-version 0.1.7 --source "$CPATH" --skip-contents < "$V"
diff <(bash "$V" --print-manifest "$EGM") <(docker exec -i "$C" bash -s -- --print-manifest "$CPATH" < "$V") | head
docker exec "$C" sh -c 'command -v bwrap jq python3; bwrap --version; python3 --version'
```

- `docker inspect` shows the path Docker was given when the container was created.
- The `awk` line reads `/proc/self/mountinfo` in the container. It finds the mount that holds `$CPATH`, whether that is a mount of `$CPATH` itself or of a parent directory, and prints the directory it is attached to now.
  - The path is relative to the filesystem that holds it; prefix it with the host's `findmnt` output.
  - It follows renames and shows `//deleted` for a removed directory.
- The verify line shows which version Claude Code runs in the container. The `diff` shows whether the container sees exactly the host tree (no output).

| What step 2 shows | Case | Next |
|---|---|---|
| The mount point is `$CPATH` or a parent of it, it is attached to `$EGM` (or to the parent that holds `$EGM`), the inodes are equal and the `diff` is empty | **A**: mounted and attached | step 5A |
| A mount as above, but attached to another directory (for example `…ailab-engineering.pre-0.2.0-…`) or `//deleted`, or the inodes differ | **A-stale**: the container still uses an old directory | step 4, then 5A |
| `mount point: /` (the container's own filesystem) | **B**: a `docker cp` copy | step 5B |
| Attached to a host path other than `$EGM` | the container uses another tree | set `EGM` to that path and repeat step 2, or decide whether to change the compose file (an owner decision, outside this guide) |

Also record whether `~/.claude` of `$CUSER` is on a volume in the `Mounts` output. If
it is not, recreating the container loses that user's Claude Code login, settings and
plugin data. If `claude plugin list --json` has no `readFromFolder`/`folderVersion`
(a Claude Code older than the 2.1.326 verified here), see "Older Claude Code" in
step 7.

## 3. Take the rollback point (0.1.7)

Copy what runs today: the tree whose plugin.json says 0.1.7, normally `$EGM`. If the
0.2.0 guide was already applied, it is the renamed `$EGM.pre-0.2.0-*` directory. Step 2
shows which tree the container is attached to.

```bash
OLD="$EGM"      # or the renamed directory, see above
grep '"version"' "${OLD:?}/plugins/ailab-engineering/.claude-plugin/plugin.json"     # expect 0.1.7
test -f "${OLD:?}/plugins/ailab-engineering/.claude-plugin/plugin.json" && \
  tar -C "$OLD" -czf "${REL:?}/rollback-0.1.7-$TS.tar.gz" . && chmod 600 "$REL/rollback-0.1.7-$TS.tar.gz"   # everything, including .git
test -f "${OLD:?}/plugins/ailab-engineering/.claude-plugin/plugin.json" && mkdir "${REL:?}/0.1.7" && \
  rsync -a --exclude=/.git "$OLD/" "$REL/0.1.7/" && bash "$V" --print-manifest "$REL/0.1.7" > "$REL/0.1.7.manifest"
diff "$PKG/ailab-engineering-0.1.7-supplied.manifest" "$REL/0.1.7.manifest" && echo "equals the snapshot the 0.2.x work started from"
cp -p ~/.claude/settings.json ~/.claude/settings.json.pre-0.2.2-$TS 2>/dev/null || true
```

A difference in that `diff` is information, not an error: it lists local changes on
example host that were not in the supplied snapshot. The rollback restores them, because it
uses `$REL/0.1.7`.

To keep 0.2.0 available as an intermediate rollback, stage it the same way from the
0.2.0 package.

## 4. Case A-stale only: reattach the container

The mount follows the directory it was created with, so only a recreate attaches the
container to `$EGM` again. Recreating ends the sessions running in the container and
discards everything outside volumes. Do it in a quiet moment. Take the service name and
project, service and compose file from step 2 (`project=`, `service=`, `compose=`):

```bash
PROJ=<project from step 2>; SVC=<service from step 2>; CF=<compose file from step 2>
test ! -e "$REL/container-claude-home-$TS" && docker cp "$C:/home/$CUSER/.claude" "$REL/container-claude-home-$TS"   # only if ~/.claude is not on a volume; adjust the home path
docker compose -p "$PROJ" -f "$CF" up -d --force-recreate --no-deps "$SVC"
# only if you made the backup above:
docker cp "$REL/container-claude-home-$TS/." "$C:/home/$CUSER/.claude/" && docker exec -u 0 "$C" chown -R "$CUSER": "/home/$CUSER/.claude"
```

Repeat step 2. It must now show case A. Until you recreate, the container keeps
running whatever its attached directory holds. Updating `$EGM` does not reach it.

## 5A. Update in place (case A: the container mounts `$EGM` or a parent of it)

Stop Claude Code sessions first, on the host and in the container
(`pgrep -a claude`, `docker top "$C" | grep -c claude`). Hooks run from these files, so a
session that is running during the sync could execute a half-copied script.

```bash
# every file the update would delete: local files not in the release (they are in the rollback copy)
rsync -a --delete --checksum --exclude=/.git --dry-run --itemize-changes "${REL:?}/0.2.2/" "${EGM:?}/" | grep '^\*deleting'
# update the SAME directory, keeping the checkout's owner, only if the release copy is intact and $EGM is the
# checkout. Never mv/rename $EGM: the container would keep the old directory.
grep -q '  plugins/ailab-engineering/.claude-plugin/plugin.json$' "${REL:?}/0.2.2.manifest" && \
  bash "$V" --print-manifest "$REL/0.2.2" | diff -q "$REL/0.2.2.manifest" - >/dev/null && \
  test -f "${EGM:?}/plugins/ailab-engineering/.claude-plugin/plugin.json" && rsync -a --chown="$(stat -c %u:%g "$EGM")" --delete --checksum --exclude=/.git "${REL:?}/0.2.2/" "${EGM:?}/" && echo UPDATED
```

- `--checksum` is required. Without it, rsync skips a file that has the same size and modification time but different contents.
- `--exclude=/.git` leaves a git checkout's history untouched. `git status` then shows the update as working-tree changes.
- `${EGM:?}` stops the command if `EGM` is not set (an empty `EGM` would make the destination `/`).

Then, in every environment where Claude Code runs:

```bash
# host, as the user that runs Claude Code
bash "$EGM/install/install.sh" --expect-version 0.2.2 --with-deny-rules
bash "$EGM/install/verify-install.sh" --expect-version 0.2.2 --source "$EGM" --reference "$REL/0.2.2"

# container: the contents it sees, then install and verify as $CUSER
docker exec -i "$C" bash -s -- --print-manifest "$CPATH" < "$V" | diff "$REL/0.2.2.manifest" - && echo CONTAINER-CONTENTS-0.2.2 && \
  docker exec -it -u "$CUSER" "$C" bash -lc "bash $CPATH/install/install.sh --expect-version 0.2.2 --with-deny-rules"
docker exec -i -u "$CUSER" "$C" bash -l -s -- --expect-version 0.2.2 --source "$CPATH" --skip-contents < "$V"
```

Each `verify-install.sh` must end with `VERIFY-INSTALL: PASS`.

- If the diff is not empty, the container does not see `$EGM`: go back to step 2. The same applies if the container installer refuses with `this tree is version '0.1.7', expected 0.2.2: nothing changed`, or, when the container still sees the 0.1.7 tree, with that tree's own `unknown option: --expect-version`.
- `install.sh` refuses to change a marketplace that is registered from another directory, and tells you so.
- In agent-long the installer warns that the fbpa sandbox is unavailable: fbpa then reports BLOCKED there (step 6).

The read-only mount is fine: the installer writes only to the user's `~/.claude`.

## 5B. Update a copied tree (case B: the container has its own copy)

Here no bind mount is involved, so swapping directories inside the container is
correct. The path stays the same, so the registration stays valid. The `test ! -e`
guards stop a second run from nesting directories.

```bash
docker exec "$C" test ! -e "$CPATH.0.2.2" && docker cp "$REL/0.2.2" "$C:$CPATH.0.2.2"
docker exec -i "$C" bash -s -- --print-manifest "$CPATH.0.2.2" < "$V" | diff "$REL/0.2.2.manifest" - && \
  docker exec -u 0 "$C" sh -c "test ! -e '$CPATH.pre-0.2.2' && mv '$CPATH' '$CPATH.pre-0.2.2' && mv '$CPATH.0.2.2' '$CPATH'" && echo SWAPPED
docker exec -it -u "$CUSER" "$C" bash -lc "bash $CPATH/install/install.sh --expect-version 0.2.2 --with-deny-rules"
docker exec -i -u "$CUSER" "$C" bash -l -s -- --expect-version 0.2.2 --source "$CPATH" --skip-contents < "$V"
```

The host `$EGM` is updated as in 5A. A copied tree disappears when the container is
recreated; a read-only mount (case A) avoids that, but changing the compose file is an
owner decision.

## 6. Check behaviour (10 minutes, in a throwaway repository)

| # | Do | Expect |
|---|---|---|
| 1 | Start a new Claude Code session (host, then container) | first message contains `[ailab-engineering] session context` |
| 2 | Ask Claude to run `git add -A` | `BLOCKED` |
| 3 | Ask Claude to run `gh pr view 1` | runs without a guard prompt (may fail: no PR) |
| 4 | Ask Claude to run `gh pr create --title t --body b` | owner prompt `Owner confirmation needed: gh pr create ...`; answer **no** |
| 5 | Ask Claude to run `npm publish --dry-run`, then `npm publish` | first runs, second asks; answer **no** |
| 6 | `/agents` | `spec-reviewer`, `reviewer`, `security-reviewer`, `qa-runner` |
| 7 | Type `/ailab-engineering:` | work-package, review, wp-close, change-intel, knowledge-review, host-gate, handoff |
| 8 | `"$EGM/plugins/ailab-engineering/scripts/intel.sh" impact <a shared file>` in a product repo | dependents, tests reached, a COVERAGE line and LIMITS |
| 9 | `"$EGM/plugins/ailab-engineering/scripts/intel.sh" sandbox-check`, on the host and with `docker exec -u "$CUSER" "$C" bash -lc "$CPATH/plugins/ailab-engineering/scripts/intel.sh sandbox-check"` | `RESULT: PASS`, or `RESULT: BLOCKED` with the reason; with BLOCKED, `fbpa` runs nothing there |
| 10 | In a throwaway repo: `/ailab-engineering:work-package TST-1 "<small bug fix>"` | WP file with Type/Size/Progress, a plan with `→ verify:` steps |
| 11 | Codex: `codex debug prompt-input \| grep -c 'ailab-'` | 5 or more |

`fbpa` needs bubblewrap 0.8 or newer and permission to create user namespaces.
Docker's default seccomp and AppArmor profiles usually refuse that inside a container,
so expect `BLOCKED` in agent-long. In that case:

- run `fbpa` where `sandbox-check` passes;
- or make it an explicit owner decision to give the container a profile that allows user namespaces. That is a security trade-off and not part of this release.

`fbpa` never falls back to running tests unsandboxed. A work package whose FBPA or
PINNED check is BLOCKED goes to the owner (wp-close lists it as a gap).

Read `~/.claude/plugins/data/ailab-engineering-ailab/guard.log` daily for the first week.

## 7. Roll back to 0.1.7

Use the same mechanism: put the 0.1.7 copy back and verify it with the 0.2.2 tools
(0.1.7 has none). Do not uninstall the plugin and do not remove the marketplace. Set
the variables first; `TS` comes from `$REL/TS`.

Case A (host checkout, seen by the container through the mount):

```bash
grep -q '  plugins/ailab-engineering/.claude-plugin/plugin.json$' "${REL:?}/0.1.7.manifest" && \
  bash "$V" --print-manifest "$REL/0.1.7" | diff -q "$REL/0.1.7.manifest" - >/dev/null && \
  test -f "${EGM:?}/plugins/ailab-engineering/.claude-plugin/plugin.json" && rsync -a --chown="$(stat -c %u:%g "$EGM")" --delete --checksum --exclude=/.git "${REL:?}/0.1.7/" "${EGM:?}/" && echo ROLLED-BACK
bash "$V" --expect-version 0.1.7 --source "$EGM" --reference "$REL/0.1.7"
docker exec -i "$C" bash -s -- --print-manifest "$CPATH" < "$V" | diff "$REL/0.1.7.manifest" - && echo CONTAINER-CONTENTS-0.1.7
docker exec -i -u "$CUSER" "$C" bash -l -s -- --expect-version 0.1.7 --source "$CPATH" --skip-contents < "$V"
```

Case B (container copy; the host checkout as in case A):

```bash
docker exec -u 0 "$C" sh -c "test -d '$CPATH.pre-0.2.2' && test ! -e '$CPATH.0.2.2-rejected' && mv '$CPATH' '$CPATH.0.2.2-rejected' && mv '$CPATH.pre-0.2.2' '$CPATH'" && echo SWAPPED-BACK
docker exec -i "$C" bash -s -- --print-manifest "$CPATH" < "$V" | diff "$REL/0.1.7.manifest" - && echo CONTAINER-CONTENTS-0.1.7
docker exec -i -u "$CUSER" "$C" bash -l -s -- --expect-version 0.1.7 --source "$CPATH" --skip-contents < "$V"
```

Each verify must end with `VERIFY-INSTALL: PASS`.

| Also roll back | How |
|---|---|
| Settings | `cp -p ~/.claude/settings.json.pre-0.2.2-$TS ~/.claude/settings.json` (or the `settings.json.bak-*` the installer wrote, also in the container). Optional: the 0.2.x ask rules only add owner prompts |
| Codex skills | `bash "$REL/0.2.2/codex/install-codex-skills.sh" --uninstall` (removes only the ailab-* skills it created) |
| A git checkout | the sync never touches `.git`; `git -C "$EGM" status` shows the same state as before the update |

Work package files written by 0.2.x stay readable by 0.1.7: extra header lines and
sections are ignored, and review reports keep `SNAPSHOT:` and `VERDICT:` at the top. The
knowledge inbox and register formats are unchanged.

### Older Claude Code (update and rollback)

A Claude Code without `readFromFolder` in `plugin list --json` makes verify-install
report NOT VERIFIED for the running tree. Such a version may run a cached copy of the
plugin. After the sync, refresh it in that environment without losing the plugin data.
The dry run comes first, so the plugin is never left uninstalled because of a failing
test:

```bash
bash "$EGM/install/install.sh" --dry-run && claude plugin uninstall --keep-data ailab-engineering@ailab && \
  bash "$EGM/install/install.sh" --expect-version 0.2.2     # after a rollback: 0.1.7's installer, without --expect-version
```

If the last command fails, the plugin is not installed and no guard runs. Start no
session until a re-run succeeds. The alternative is to update Claude Code first
(`claude update`; 0.2.x needs 2.1.295+).

## 8. Projects

The installer never changes existing project files. To adopt the 0.2.x rules in a
product repository (your application projects), copy the
`<!-- ailab:engineering-behaviour:start -->` block and the "Review and iteration limits"
section from `templates/project/AGENTS.md` into that repository's `AGENTS.md`, as a
separate T1 work package there. To use `arch-check`, add `docs/project/ARCHITECTURE.md`
(`init-project.sh <repo> --with-architecture` creates it only when absent).

## 9. What was verified before delivery, and what only example host can show

| Claim | Verified in the isolated environment | Check on example host |
|---|---|---|
| `mv` leaves a bind-mounted container on the old directory; mountinfo (with the `awk` line of step 2), the manifest diff and verify-install show it; the stale 0.1.7 installer refuses `--expect-version` | yes: `tests/run-upgrade-tests.sh` (kernel bind mount in a private mount namespace, read-only like `:ro`, real Claude Code 2.1.326) | step 2 on the real agent-long |
| An in-place `rsync --delete --checksum` reaches the container; install.sh runs from the read-only mount, also without an fbpa sandbox there, and leaves the tree unchanged; rollback in place works; the plugin data survives; the inode is unchanged | yes: same test | steps 5A and 7 with their verify commands |
| Claude Code runs the registered folder (`readFromFolder`, `folderVersion`) | yes, on 2.1.326 | example host's Claude Code version (step 2) |
| `marketplace remove` and `uninstall` delete plugin data; `--keep-data` keeps it | yes, on 2.1.326 | — |
| The `docker` commands themselves, the compose file, the container user, whether `~/.claude` is on a volume | NOT VERIFIED (no Docker here); the parts that do not need Docker (awk, manifests over stdin, rsync, the installer) were run | steps 1, 2, 4, 5 |
| `fbpa` sandbox | `sandbox-check` PASS here as root and as an unprivileged user; BLOCKED when user namespaces are refused; the installer completes and warns without it | step 1 dry run and step 6 row 9, host and container |
| Hook "ask" in bypassPermissions mode | NOT VERIFIED | step 6 rows 4–5 in the mode you use |
