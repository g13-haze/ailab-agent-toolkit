"""Black-box tests for scripts/intel.sh (impact, history, signals, arch-check, fbpa, metrics).

Run: bash tests/run-intel-tests.sh   (or python3 -m unittest discover -s tests/intel)
Every test works in a temporary git repository built from tests/fixtures/intel or
scripted commits; nothing outside the temporary directory is touched.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
INTEL = os.path.join(ROOT, "plugins", "ailab-engineering", "scripts", "intel.sh")
FIX = os.path.join(ROOT, "tests", "fixtures", "intel")
PY = sys.executable
# The fbpa sandbox exposes the system directories; an interpreter installed elsewhere
# (a virtualenv, a uv- or pyenv-managed CPython) is declared as a read-only toolchain.
TOOLCHAIN = []
for _p in sorted({os.path.realpath(sys.prefix), os.path.realpath(sys.base_prefix)}):
    if not _p.startswith("/usr/"):
        TOOLCHAIN += ["--toolchain", _p]


def _sandbox_works():
    """Can fbpa's sandbox run here? Only where sandbox-check says BLOCKED (Docker's default
    seccomp profile, no bubblewrap, AILAB_SANDBOX_DISABLE) are the tests that need it skipped
    and reported as NOT VERIFIED; the fail-closed tests still run. A sandbox that runs but
    fails an isolation probe is not skipped: those tests then fail, as they must."""
    p = subprocess.run(["bash", INTEL, "sandbox-check"], cwd=tempfile.gettempdir(), stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, env=dict(os.environ, AILAB_PYTHON=PY))
    last = p.stdout.decode("utf-8", "replace").strip().splitlines()[-1:]
    return not (last and last[0].startswith("RESULT: BLOCKED")), last


SANDBOX, SANDBOX_RESULT = _sandbox_works()
if not SANDBOX:
    sys.stderr.write("NOT VERIFIED: the fbpa sandbox cannot run here (%s); the tests that need it are skipped\n"
                     % " ".join(SANDBOX_RESULT))


def sh(cmd, cwd, env=None, check=True):
    p = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    if check and p.returncode != 0:
        raise AssertionError("command failed %s: %s" % (cmd, p.stderr.decode()))
    return p


def git(cwd, *args, date=None):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@t")
    if date:
        env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    return sh(["git", "-c", "init.defaultBranch=main"] + list(args), cwd, env=env).stdout.decode().strip()


def write(root, files):
    for path, text in files.items():
        p = os.path.join(root, path)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            fh.write(textwrap.dedent(text))


def intel(cwd, *args, env=None):
    e = dict(os.environ, AILAB_PYTHON=PY)
    if env:
        e.update(env)
    if args and args[0] == "fbpa":
        args = ("fbpa",) + tuple(TOOLCHAIN) + tuple(args[1:])
    p = subprocess.run(["bash", INTEL] + list(args), cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=e)
    return p.returncode, p.stdout.decode(), p.stderr.decode()


def jintel(cwd, *args):
    rc, out, err = intel(cwd, *args, "--json") if "fbpa" not in args else intel(cwd, *args)
    if rc not in (0, 1):
        raise AssertionError("intel %s exited %d: %s %s" % (args, rc, out, err))
    return json.loads(out)


def snapshot(repo):
    """Paths and contents of the work tree plus git status: to prove read-only behaviour."""
    out = {}
    for d, dirs, files in os.walk(repo):
        if ".git" in dirs:
            dirs.remove(".git")
        for f in files:
            p = os.path.join(d, f)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, repo)] = fh.read()
    st = sh(["git", "status", "--porcelain", "--untracked-files=all"], repo).stdout
    return out, st


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ailab-intel-test.")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fixture_repo(self, name):
        dst = os.path.join(self.tmp, name)
        shutil.copytree(os.path.join(FIX, name), dst)
        git(dst, "init", "-q")
        git(dst, "add", "--", ".")
        git(dst, "commit", "-qm", "base")
        return dst

    def new_repo(self, name="repo"):
        d = os.path.join(self.tmp, name)
        os.makedirs(d)
        git(d, "init", "-q")
        return d


class ImpactPython(Base):
    def setUp(self):
        super().setUp()
        self.repo = self.fixture_repo("pyproj")

    def impact(self, *paths, depth=2):
        return jintel(self.repo, "impact", "--depth", str(depth), *paths)

    def test_direct_dependents_of_a_hub_module_are_exact(self):
        d = self.impact("src/shop/util/money.py")["per_file"]["src/shop/util/money.py"]
        self.assertEqual(d["direct"], ["src/shop/cart.py", "src/shop/pricing.py", "src/shop/util/__init__.py",
                                       "tests/conftest.py"])
        self.assertIn("src/shop/orders.py", d["indirect"])

    def test_stdlib_name_collision_and_substring_paths_get_no_false_edges(self):
        r = self.impact("src/shop/util/os.py", "src/shop/costs.py", depth=1)
        self.assertEqual(r["per_file"]["src/shop/util/os.py"]["direct"], [])
        self.assertEqual(r["per_file"]["src/shop/costs.py"]["direct"], [])

    def test_relative_imports_resolve_inside_the_package(self):
        d = self.impact("src/shop/cart.py", "src/shop/pricing.py", depth=1)["per_file"]
        self.assertIn("src/shop/orders.py", d["src/shop/cart.py"]["direct"])       # from .cart import Cart
        self.assertIn("src/shop/orders.py", d["src/shop/pricing.py"]["direct"])    # from . import pricing
        self.assertIn("src/shop/api/views.py", self.impact("src/shop/orders.py", depth=1)["per_file"]["src/shop/orders.py"]["direct"])

    def test_from_package_import_submodule_links_the_submodule(self):
        self.assertIn("src/shop/pricing.py", self.impact("src/shop/util/money.py", depth=1)["per_file"]["src/shop/util/money.py"]["direct"])

    def test_dynamic_literal_followed_non_literal_counted(self):
        r = self.impact("src/shop/plugins/extra.py", depth=1)
        self.assertEqual(r["per_file"]["src/shop/plugins/extra.py"]["direct"], ["src/shop/api/views.py"])
        self.assertEqual(r["coverage"]["imports_dynamic_unresolved"], 1)

    def test_sibling_imports_script_dir_and_pytest_prepend(self):
        self.assertEqual(self.impact("scripts/siblingmod.py", depth=1)["per_file"]["scripts/siblingmod.py"]["direct"], ["scripts/tool.py"])
        self.assertIn("tests/test_orders.py", self.impact("tests/helpers.py", depth=1)["per_file"]["tests/helpers.py"]["direct"])

    def test_tests_reached_include_conftest_users(self):
        tests = {t["path"] for t in self.impact("src/shop/util/money.py")["tests"]}
        self.assertTrue({"tests/test_cart.py", "tests/test_orders.py", "tests/conftest.py"} <= tests)
        tests = {t["path"] for t in self.impact("src/shop/cart.py")["tests"]}
        self.assertIn("tests/test_cart.py", tests)
        self.assertNotIn("tests/test_unrelated.py", tests)

    def test_coverage_discloses_parse_errors_unresolved_and_externals(self):
        r = self.impact("src/shop/report.py")
        c = r["coverage"]
        self.assertEqual(c["parse_errors"], 1)
        self.assertEqual(c["imports_unresolved"], 1)
        self.assertGreaterEqual(c["imports_external"], 1)        # requests
        self.assertIn(["src/shop/report.py", 1, "shop.missing_mod"], r["unresolved_in_scope"])
        self.assertIn("lower bound", r["limits"])
        rc, out, _ = intel(self.repo, "impact", "src/shop/report.py")
        self.assertIn("UNRESOLVED (in scope): src/shop/report.py:1 shop.missing_mod", out)
        self.assertIn("PARSE ERRORS: src/shop/legacy.py", out)
        self.assertIn("LIMITS: static imports only", out)

    def test_changed_set_from_work_tree_and_deleted_files_still_imported(self):
        os.remove(os.path.join(self.repo, "src/shop/pricing.py"))
        with open(os.path.join(self.repo, "src/shop/cart.py"), "a") as fh:
            fh.write("# touched\n")
        r = jintel(self.repo, "impact")
        self.assertEqual(r["changed"], ["src/shop/cart.py"])
        self.assertEqual(r["deleted"], ["src/shop/pricing.py"])
        self.assertEqual(r["deleted_still_imported"], {"src/shop/pricing.py": ["src/shop/orders.py"]})
        rc, out, _ = intel(self.repo, "impact")
        self.assertIn("BREAKAGE LIKELY: deleted src/shop/pricing.py is still imported by src/shop/orders.py", out)

    def test_base_ref_and_untracked_files(self):
        base = git(self.repo, "rev-parse", "HEAD")
        write(self.repo, {"src/shop/newmod.py": "from shop.cart import Cart\n"})
        git(self.repo, "add", "--", "src/shop/newmod.py")
        git(self.repo, "commit", "-qm", "add newmod")
        write(self.repo, {"src/shop/untracked.py": "from shop import newmod\n"})
        r = jintel(self.repo, "impact", "--base", base)
        self.assertEqual(r["changed"], ["src/shop/newmod.py", "src/shop/untracked.py"])
        self.assertEqual(r["per_file"]["src/shop/newmod.py"]["direct"], ["src/shop/untracked.py"])

    def test_tests_only_output_and_hub_flag(self):
        rc, out, _ = intel(self.repo, "impact", "--tests-only", "src/shop/cart.py")
        self.assertEqual(out.split(), ["tests/test_cart.py", "tests/test_orders.py"])
        rc, out, _ = intel(self.repo, "impact", "--hub", "3", "src/shop/util/money.py")
        self.assertIn("HUB: 4 direct dependents", out)

    def test_read_only(self):
        before = snapshot(self.repo)
        for args in (["impact"], ["impact", "src/shop"], ["history"], ["signals"], ["arch-check"]):
            intel(self.repo, *args)
        self.assertEqual(before, snapshot(self.repo))

    def test_directory_argument_and_outside_path(self):
        r = self.impact("src/shop/util", depth=1)
        self.assertIn("src/shop/util/money.py", r["per_file"])
        rc, out, err = intel(self.repo, "impact", "/etc/passwd")
        self.assertEqual(rc, 2)
        self.assertIn("outside the repository", err)

    def test_not_a_repository(self):
        rc, out, err = intel(self.tmp, "impact")
        self.assertEqual(rc, 2)
        self.assertIn("not inside a git repository", err)

    def test_repository_code_is_never_executed(self):
        write(self.repo, {"src/shop/evil.py": "import os\nopen(%r, 'w').write('ran')\n" % os.path.join(self.tmp, "PWNED"),
                          "src/shop/json.py": "raise SystemExit('shadowed stdlib imported')\n"})
        rc, out, _ = intel(self.repo, "impact", "src/shop/evil.py")
        self.assertEqual(rc, 0, out)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "PWNED")))


class ReviewRegressions(Base):
    """Scenarios reported by the independent review of 0.2.0."""

    def setUp(self):
        super().setUp()
        self.repo = self.fixture_repo("pyproj")

    def test_globs_planned_files_and_subdirectory_paths(self):
        r = jintel(self.repo, "impact", "--depth", "1", "src/shop/util/*.py", "src/shop/new_feature.py")
        self.assertIn("src/shop/util/money.py", r["per_file"])
        self.assertEqual(r["deleted"], [])
        self.assertEqual(r["not_found"], ["src/shop/new_feature.py"])
        rc, out, _ = intel(self.repo, "impact", "src/shop/new_feature.py", "src/shop/util/money.py")
        self.assertIn("NOT FOUND", out)
        self.assertNotIn("DELETED", out)
        sub = os.path.join(self.repo, "src")
        rc, out, err = intel(sub, "impact", "--json", "--depth", "1", "shop/cart.py")
        r = json.loads(out)
        self.assertEqual(list(r["per_file"]), ["src/shop/cart.py"])
        self.assertEqual(r["deleted"], [])
        rc, out, err = intel(self.repo, "impact", "nothing/here.py")
        self.assertEqual(rc, 2)
        self.assertIn("no file matches", err)

    def test_package_modules_do_not_import_their_siblings_by_name(self):
        write(self.repo, {"app/__init__.py": "", "app/api/__init__.py": "", "app/api/json.py": "X = 1\n",
                          "app/api/requests.py": "Y = 1\n", "app/api/views.py": "import json\nimport requests\n"})
        r = jintel(self.repo, "impact", "--depth", "1", "app/api/json.py", "app/api/requests.py")
        self.assertEqual(r["per_file"]["app/api/json.py"]["direct"], [])
        self.assertEqual(r["per_file"]["app/api/requests.py"]["direct"], [])

    def test_signals_ignore_external_diff_tools_and_noprefix(self):
        write(self.repo, {"src/shop/cart.py": open(os.path.join(self.repo, "src/shop/cart.py")).read() + "x = 1  # noqa\n"})
        git(self.repo, "config", "diff.external", "/bin/false")
        git(self.repo, "config", "diff.noprefix", "true")
        s = jintel(self.repo, "signals")["signals"]
        self.assertTrue(any("noqa" in x for x in s["suppressions"]), s)

    def test_checkpoint_committed_with_the_code_is_not_drift(self):
        r = self.new_repo("ck")
        write(r, {"a.py": "A = 1\n"})
        git(r, "add", "--", "a.py")
        git(r, "commit", "-qm", "one")
        c1 = git(r, "rev-parse", "--short", "HEAD")
        write(r, {"a.py": "A = 2\n", "docs/project/CURRENT_STATE.md": "# State\nCheckpoint: %s\n" % c1})
        git(r, "add", "--", "a.py", "docs/project/CURRENT_STATE.md")
        git(r, "commit", "-qm", "WP-1 work and its checkpoint")
        self.assertEqual(jintel(r, "history")["checkpoint"]["commits_since"], 0)
        write(r, {"b.py": "B = 1\n"})
        git(r, "add", "--", "b.py")
        git(r, "commit", "-qm", "later work")
        h = jintel(r, "history", "--since", "checkpoint")
        self.assertEqual(h["checkpoint"]["commits_since"], 1)
        self.assertEqual([c["subject"] for c in h["commits"]], ["later work"])


class ImpactTypeScript(Base):
    def setUp(self):
        super().setUp()
        self.repo = self.fixture_repo("tsproj")

    def test_alias_extends_esm_reexport_and_comments(self):
        r = jintel(self.repo, "impact", "--depth", "3", "src/lib/format.ts")
        d = r["per_file"]["src/lib/format.ts"]
        self.assertEqual(d["direct"], ["src/__tests__/format.test.ts", "src/components/Price.tsx", "src/esm.ts", "src/lib/index.ts"])
        self.assertEqual(d["indirect"], ["src/components/Cart.tsx", "src/pages/home.ts"])
        self.assertNotIn("src/commented.ts", d["direct"] + d["indirect"])
        self.assertEqual([t["path"] for t in r["tests"]], ["src/__tests__/format.test.ts"])

    def test_dynamic_import_workspace_package_and_unresolved(self):
        r = jintel(self.repo, "impact", "--depth", "1", "src/pages/lazy.ts", "packages/ui/src/index.ts")
        self.assertEqual(r["per_file"]["src/pages/lazy.ts"]["direct"], ["src/pages/home.ts"])
        self.assertEqual(r["per_file"]["packages/ui/src/index.ts"]["direct"], ["src/usesui.ts"])
        r = jintel(self.repo, "impact", "src/broken.ts")
        self.assertIn(["src/broken.ts", 1, "./does-not-exist"], r["unresolved_in_scope"])
        self.assertEqual(r["coverage"]["imports_dynamic_unresolved"], 1)

    def test_vendored_and_minified_files_are_skipped_and_counted(self):
        write(self.repo, {"node_modules/x/index.js": "require('./a')\n", "public/app.min.js": "import a from './b'\n"})
        git(self.repo, "add", "-f", "--", "node_modules/x/index.js", "public/app.min.js")
        r = jintel(self.repo, "impact", "src/lib/format.ts")
        self.assertEqual(r["coverage"]["files_skipped_vendored"], 2)


class ArchCheck(Base):
    def setUp(self):
        super().setUp()
        self.repo = self.fixture_repo("pyproj")

    def test_planted_violation_is_found_and_implicit_edges_are_ignored(self):
        rc, out, _ = intel(self.repo, "arch-check")
        self.assertEqual(rc, 1)
        r = jintel(self.repo, "arch-check")
        self.assertEqual([(v["file"], v["imports"], v["rule"]) for v in r["violations"]],
                         [("src/shop/util/audit.py", "src/shop/orders.py", "forbid util -> domain")])
        self.assertIn("docs/project/ARCHITECTURE.md:4 src/shop/old_engine.py", r["stale_references"])

    def test_only_rule_and_changed_scope(self):
        write(self.repo, {"src/shop/cart.py": "import os\nfrom shop.api import views\nfrom shop.util.money import fmt\n"})
        r = jintel(self.repo, "arch-check", "--changed")
        self.assertEqual([(v["file"], v["imports"], v["rule"]) for v in r["violations"]],
                         [("src/shop/cart.py", "src/shop/api/views.py", "only domain -> util")])

    def test_clean_repository_exits_zero_and_strict_flags_stale_references(self):
        os.remove(os.path.join(self.repo, "src/shop/util/audit.py"))
        rc, out, _ = intel(self.repo, "arch-check")
        self.assertEqual(rc, 0, out)
        self.assertIn("VIOLATIONS: none", out)
        rc, out, _ = intel(self.repo, "arch-check", "--strict")
        self.assertEqual(rc, 1)

    def test_config_errors_and_missing_block(self):
        doc = os.path.join(self.repo, "docs/project/ARCHITECTURE.md")
        with open(doc, "w") as fh:
            fh.write("```ailab-boundaries\nlayer a: x/**\nforbid a -> b\nbogus line\n```\n")
        rc, out, _ = intel(self.repo, "arch-check")
        self.assertEqual(rc, 2)
        self.assertIn("unknown layer 'b'", out)
        self.assertIn("cannot parse 'bogus line'", out)
        with open(doc, "w") as fh:
            fh.write("# no rules\n")
        rc, out, _ = intel(self.repo, "arch-check")
        self.assertEqual(rc, 0)
        self.assertIn("no ```ailab-boundaries block", out)

    def test_empty_layer_warning(self):
        doc = os.path.join(self.repo, "docs/project/ARCHITECTURE.md")
        with open(doc, "a") as fh:
            fh.write("\n")
        text = open(doc).read().replace("layer domain: src/shop/*.py", "layer domain: src/shop/*.py\nlayer ghost: src/nowhere/**")
        open(doc, "w").write(text)
        rc, out, _ = intel(self.repo, "arch-check")
        self.assertIn("layers that match no analyzed file (stale globs?): ghost", out)


class History(Base):
    def setUp(self):
        super().setUp()
        r = self.repo = self.new_repo()
        write(r, {".claude/wp/WP-1.md": "# WP-1\n", ".claude/wp/WP-2.md": "# WP-2\n",
                  "app/pricing.py": "A = 1\n", "app/cart.py": "B = 1\n"})
        git(r, "add", "--", ".")
        git(r, "commit", "-qm", "WP-1: initial cart and pricing", date="2026-01-01T10:00:00")
        self.c1 = git(r, "rev-parse", "HEAD")
        write(r, {"app/pricing.py": "A = 2\n", "app/cart.py": "B = 2\n"})
        git(r, "commit", "-qam", "feat: pricing rules (WP-2)", date="2026-01-02T10:00:00")
        self.c2 = git(r, "rev-parse", "HEAD")
        write(r, {"app/pricing.py": "A = 3\n"})
        git(r, "commit", "-qam", "fix: rounding in pricing", date="2026-01-03T10:00:00")
        write(r, {"app/other.py": "C = 1\n"})
        git(r, "add", "--", "app/other.py")
        git(r, "commit", "-qm", "docs: unrelated", date="2026-01-04T10:00:00")
        write(r, {"docs/project/CURRENT_STATE.md": "# State\nCheckpoint: %s\n" % self.c2[:10]})

    def test_scope_churn_rework_cochange_and_wp_refs(self):
        h = jintel(self.repo, "history", "app/pricing.py")
        self.assertEqual([c["subject"] for c in h["commits"]],
                         ["fix: rounding in pricing", "feat: pricing rules (WP-2)", "WP-1: initial cart and pricing"])
        self.assertEqual(h["churn"], {"app/pricing.py": 3})
        self.assertEqual(h["rework"], [["app/pricing.py", 1, 3]])
        self.assertEqual(h["cochange"], {"app/cart.py": 2, ".claude/wp/WP-1.md": 1, ".claude/wp/WP-2.md": 1})
        self.assertEqual(h["wp_refs"], {"WP-1": 1, "WP-2": 1})

    def test_checkpoint_commits_since(self):
        h = jintel(self.repo, "history", "--since", "checkpoint")
        self.assertEqual(h["checkpoint"]["commits_since"], 2)
        self.assertEqual(len(h["commits"]), 2)
        write(self.repo, {"docs/project/CURRENT_STATE.md": "Checkpoint: deadbeefdeadbeef\n"})
        h = jintel(self.repo, "history")
        self.assertIsNone(h["checkpoint"]["commits_since"])
        rc, out, _ = intel(self.repo, "history")
        self.assertIn("checkpoint commit not in this history", out)

    def test_shallow_clone_is_disclosed(self):
        clone = os.path.join(self.tmp, "shallow")
        sh(["git", "clone", "-q", "--depth", "1", "file://" + self.repo, clone], self.tmp)
        rc, out, _ = intel(clone, "history")
        self.assertIn("SHALLOW CLONE: history truncated", out)

    def test_changed_scope_and_limit(self):
        write(self.repo, {"app/cart.py": "B = 3\n"})
        h = jintel(self.repo, "history", "--changed", "--limit", "1")
        self.assertEqual(h["scope"], ["app/cart.py", "docs/project/CURRENT_STATE.md"])
        self.assertEqual(len(h["commits"]), 1)


class Signals(Base):
    def setUp(self):
        super().setUp()
        r = self.repo = self.new_repo()
        shutil.copytree(os.path.join(ROOT, "templates", "project", ".claude"), os.path.join(r, ".claude"))
        write(r, {"app/core.py": "def f():\n    return 1\n",
                  "tests/test_core.py": "from app.core import f\n\ndef test_f():\n    assert f() == 1\n    assert f() != 2\n",
                  "requirements.txt": "requests==2.0\n"})
        git(r, "add", "--", ".")
        git(r, "commit", "-qm", "base")

    def test_signals_on_a_risky_diff(self):
        write(self.repo, {"app/core.py": "def f():  # noqa: E501\n    # TODO: real implementation\n    raise NotImplementedError\n",
                          "tests/test_core.py": "import pytest\nfrom app.core import f\n\n@pytest.mark.skip\ndef test_f():\n    assert f() != 2\n",
                          "requirements.txt": "requests==2.1\n",
                          "app/auth/session.py": "SECRET = 1\n",
                          "app/big.py": "x = 1\n" * 1200})
        s = jintel(self.repo, "signals", "--type", "refactor")["signals"]
        self.assertTrue(any("noqa" in x for x in s["suppressions"]))
        self.assertTrue(any("TODO" in x for x in s["todos"]))
        self.assertTrue(any("pytest.mark.skip" in x for x in s["skips"]))
        self.assertTrue(any("assert f() == 1" in x for x in s["assertions_removed_or_edited"]))
        self.assertTrue(any("NotImplementedError" in x for x in s["placeholders"]))
        self.assertEqual(s["manifests"], ["requirements.txt"])
        self.assertTrue(any(x.startswith("app/auth/session.py") for x in s["security_sensitive"]))
        self.assertTrue(any(x.startswith("app/big.py") for x in s["large_files"]))
        rc, out, _ = intel(self.repo, "signals", "--type", "refactor")
        self.assertIn("REFACTOR WITH EDITED ASSERTIONS", out)
        self.assertIn("SECURITY-SENSITIVE PATHS TOUCHED", out)

    def test_bugfix_without_test_and_clean_diff(self):
        rc, out, _ = intel(self.repo, "signals")
        self.assertIn("No signals.", out)
        write(self.repo, {"app/core.py": "def f():\n    return 2\n"})
        rc, out, _ = intel(self.repo, "signals", "--type", "bugfix")
        self.assertIn("BUG FIX WITHOUT TEST CHANGES", out)

    def test_deleted_test_file_assertions_are_reported(self):
        os.remove(os.path.join(self.repo, "tests/test_core.py"))
        s = jintel(self.repo, "signals")["signals"]
        self.assertEqual(s["tests_deleted"], ["tests/test_core.py"])
        self.assertTrue(any("assert f() == 1" in x for x in s["assertions_removed_or_edited"]))


class Fbpa(Base):
    BUGGY = "def add(a, b):\n    return a - b\n"
    FIXED = "def add(a, b):\n    return a + b\n"
    # these hold where the sandbox cannot run: argument checks and the fail-closed paths
    WITHOUT_SANDBOX = {"test_unsafe_workdirs_and_bad_arguments_are_refused", "test_no_sandbox_means_blocked_and_nothing_runs",
                       "test_refused_user_namespaces_mean_blocked_and_nothing_runs", "test_link_must_be_an_ignored_untracked_directory",
                       "test_linked_directory_with_a_socket_is_refused", "test_carried_test_file_cannot_be_a_symlink_to_a_host_file",
                       "test_an_interpreter_inside_the_repository_is_never_run",
                       "test_site_packages_of_an_activated_virtualenv_do_not_run",
                       "test_toolchain_must_be_an_install_root_without_sockets", "test_unsafe_toolchains_are_refused",
                       "test_repository_inside_an_exposed_system_directory_is_blocked",
                       "test_fbpa_refuses_a_repository_under_usr", "test_link_to_git_metadata_is_refused",
                       "test_workspace_symlinks_in_linked_node_modules_are_blocked", "test_sandbox_unavailable_reports_blocked",
                       "test_activated_repository_virtualenv_falls_back_to_the_system_python",
                       "test_repository_interpreter_refused_also_for_repo_given_by_option",
                       "test_limit_and_environment_signatures", "test_abbreviated_repo_option_cannot_bypass_the_interpreter_check",
                       "test_repository_of_another_owner_is_still_checked"}

    def setUp(self):
        if not SANDBOX and self._testMethodName not in self.WITHOUT_SANDBOX:
            self.skipTest("NOT VERIFIED: the fbpa sandbox cannot run here")
        super().setUp()
        r = self.repo = self.new_repo()
        write(r, {"calc.py": self.BUGGY, "tests/__init__.py": ""})
        git(r, "add", "--", ".")
        git(r, "commit", "-qm", "base with bug")
        self.base = git(r, "rev-parse", "HEAD")
        self.wd = os.path.join(self.tmp, "scratch", "fbpa")

    def run_fbpa(self, test_body, fixed=True, extra=(), cmd=None, timeout=None):
        write(self.repo, {"calc.py": self.FIXED if fixed else self.BUGGY, "tests/test_calc.py": test_body})
        self.pre = snapshot(self.repo)
        args = ["fbpa", "--base", self.base, "--tests", "tests/test_calc.py", "--workdir", self.wd, "--json"] + list(extra)
        if timeout:
            args += ["--timeout", str(timeout)]
        args += ["--"] + (cmd or [PY, "-m", "unittest", "-v", "tests.test_calc"])
        rc, out, err = intel(self.repo, *args)
        self.post = snapshot(self.repo)
        return rc, (json.loads(out) if out.strip().startswith("{") else {"raw": out, "err": err})

    def test_reproducing_test_passes_the_check(self):
        rc, r = self.run_fbpa("import unittest\nfrom calc import add\n\nclass T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n")
        self.assertEqual((rc, r["verdict"]), (0, "PASS"), r)
        self.assertEqual(r["before"]["class"], "assert-fail")
        self.assertEqual(r["after"]["class"], "pass")
        self.assertEqual(self.pre, self.post)
        self.assertEqual(r["isolation"]["repository_snapshot"][0], r["isolation"]["repository_snapshot"][1])

    def test_non_reproducing_test_fails_the_check(self):
        rc, r = self.run_fbpa("import unittest\nfrom calc import add\n\nclass T(unittest.TestCase):\n    def test_zero(self):\n        self.assertEqual(add(0, 0), 0)\n")
        self.assertEqual((rc, r["verdict"]), (1, "FAIL"), r)
        self.assertIn("does not reproduce the bug", r["reason"])

    def test_test_needing_new_code_is_inconclusive_not_pass(self):
        write(self.repo, {"calc.py": self.FIXED + "\ndef mul(a, b):\n    return a * b\n",
                          "tests/test_calc.py": "import unittest\nfrom calc import add, mul\n\nclass T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n"})
        rc, out, _ = intel(self.repo, "fbpa", "--base", self.base, "--tests", "tests/test_calc.py", "--workdir", self.wd,
                           "--json", "--", PY, "-m", "unittest", "tests.test_calc")
        r = json.loads(out)
        self.assertEqual((rc, r["verdict"]), (3, "INCONCLUSIVE"), r)
        self.assertEqual(r["before"]["detail"], "import error")

    def test_wrong_fix_fails_after(self):
        rc, r = self.run_fbpa("import unittest\nfrom calc import add\n\nclass T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n", fixed=False)
        self.assertEqual((rc, r["verdict"]), (1, "FAIL"), r)
        self.assertIn("does not pass on the final tree", r["reason"])

    def test_writes_land_in_the_copies_not_the_repository(self):
        body = ("import unittest\nfrom calc import add\n\nclass T(unittest.TestCase):\n    def test_add(self):\n"
                "        open('written-by-test.txt', 'w').write('x')\n        self.assertEqual(add(2, 3), 5)\n")
        rc, r = self.run_fbpa(body)
        self.assertEqual(r["verdict"], "PASS", r)
        self.assertFalse(os.path.exists(os.path.join(self.repo, "written-by-test.txt")))
        self.assertTrue(os.path.exists(os.path.join(self.wd, "before", "written-by-test.txt")))
        self.assertTrue(os.path.exists(os.path.join(self.wd, "after", "written-by-test.txt")))

    def test_test_cannot_reach_the_repository(self):
        target = os.path.join(self.repo, "calc.py")
        body = ("import unittest\nfrom calc import add\n\nclass T(unittest.TestCase):\n    def test_add(self):\n"
                "        open(%r, 'a').write('# touched\\n')\n        self.assertEqual(add(2, 3), 5)\n" % target)
        rc, r = self.run_fbpa(body)
        self.assertNotEqual(r["verdict"], "PASS", r)
        self.assertEqual(self.pre, self.post)                      # the repository was never written
        self.assertIn("No such file or directory", open(r["after"]["log"]).read())

    def test_timeout_blocked_and_no_tests(self):
        rc, r = self.run_fbpa("x = 1\n", cmd=[PY, "-c", "import time; time.sleep(5)"], timeout=1)
        self.assertEqual((rc, r["verdict"]), (3, "INCONCLUSIVE"), r)
        shutil.rmtree(self.wd)
        rc, r = self.run_fbpa("x = 1\n", cmd=["definitely-not-a-command-xyz"])
        self.assertEqual((rc, r["verdict"]), (4, "BLOCKED"), r)
        shutil.rmtree(self.wd)
        rc, r = self.run_fbpa("import unittest\n", cmd=[PY, "-m", "unittest", "-v", "tests.test_calc"])
        self.assertNotEqual(r["verdict"], "PASS", r)

    def test_unsafe_workdirs_and_bad_arguments_are_refused(self):
        write(self.repo, {"tests/test_calc.py": "x = 1\n"})
        base = ["fbpa", "--base", self.base, "--tests", "tests/test_calc.py"]
        for wd in (os.path.join(self.repo, "w"), self.repo, "/", os.path.dirname(self.repo)):
            rc, out, err = intel(self.repo, *(base + ["--workdir", wd, "--", "true"]))
            self.assertEqual(rc, 2, (wd, out, err))
        os.makedirs(os.path.join(self.tmp, "full"))
        open(os.path.join(self.tmp, "full", "f"), "w").close()
        rc, out, err = intel(self.repo, *(base + ["--workdir", os.path.join(self.tmp, "full"), "--", "true"]))
        self.assertEqual(rc, 2)
        self.assertIn("must be empty", err)
        rc, out, err = intel(self.repo, "fbpa", "--base", self.base, "--tests", "../x.py", "--workdir", self.wd, "--", "true")
        self.assertEqual(rc, 2)
        rc, out, err = intel(self.repo, "fbpa", "--base", "nope", "--tests", "tests/test_calc.py", "--workdir", self.wd, "--", "true")
        self.assertEqual(rc, 2)
        self.assertIn("unknown git ref", err)

    def test_linked_dependency_directory(self):
        with open(os.path.join(self.repo, ".gitignore"), "w") as fh:
            fh.write("deps/\n")
        write(self.repo, {"deps/helperlib.py": "VALUE = 5\n"})
        body = ("import sys, unittest\nsys.path.insert(0, 'deps')\nimport helperlib\nfrom calc import add\n\n"
                "class T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), helperlib.VALUE)\n")
        rc, r = self.run_fbpa(body, extra=["--link", "deps"])
        self.assertEqual(r["verdict"], "PASS", r)
        mount_point = os.path.join(self.wd, "before", "deps")      # read-only bind mount, not a copy or a symlink
        self.assertFalse(os.path.islink(mount_point))
        self.assertEqual(os.listdir(mount_point), [])

    def test_refactor_mode_pins_must_pass_before_and_after(self):
        body = ("import unittest\nfrom calc import add\n\nclass T(unittest.TestCase):\n    def test_pin(self):\n"
                "        self.assertEqual(add(5, 3), %d)\n")
        rc, r = self.run_fbpa(body % 2, fixed=False, extra=["--mode", "refactor"])     # pins current (buggy) behaviour
        self.assertEqual((rc, r["check"], r["verdict"]), (0, "PINNED", "PASS"), r)
        shutil.rmtree(self.wd)
        rc, r = self.run_fbpa(body % 2, fixed=True, extra=["--mode", "refactor"])      # "refactor" changed behaviour
        self.assertEqual((rc, r["verdict"]), (1, "FAIL"), r)
        self.assertIn("behaviour changed", r["reason"])
        shutil.rmtree(self.wd)
        rc, r = self.run_fbpa(body % 8, fixed=True, extra=["--mode", "refactor"])      # pin does not describe the base
        self.assertEqual((rc, r["verdict"]), (1, "FAIL"), r)
        self.assertIn("do not describe current behaviour", r["reason"])

    def test_refactor_mode_wrong_reason_failure_is_inconclusive(self):
        write(self.repo, {"calc.py": self.BUGGY + "\ndef mul(a, b):\n    return a * b\n"})
        body = "import unittest\nfrom calc import mul\n\nclass T(unittest.TestCase):\n    def test_pin(self):\n        self.assertEqual(mul(2, 3), 6)\n"
        write(self.repo, {"tests/test_calc.py": body})
        rc, out, _ = intel(self.repo, "fbpa", "--mode", "refactor", "--base", self.base, "--tests", "tests/test_calc.py",
                           "--workdir", self.wd, "--json", "--", PY, "-m", "unittest", "tests.test_calc")
        r = json.loads(out)
        self.assertEqual((rc, r["verdict"]), (3, "INCONCLUSIVE"), r)

    def _src_layout(self):
        r = self.new_repo("srcrepo")
        write(r, {"src/pkg/__init__.py": "", "src/pkg/calc.py": self.BUGGY, "tests/__init__.py": ""})
        git(r, "add", "--", ".")
        git(r, "commit", "-qm", "base")
        write(r, {"src/pkg/calc.py": self.FIXED,
                  "tests/test_calc.py": "import unittest\nfrom pkg.calc import add\n\nclass T(unittest.TestCase):\n"
                                        "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n"})
        return r, git(r, "rev-parse", "HEAD")

    def test_editable_install_is_blocked_not_a_false_result(self):
        r, base = self._src_layout()
        with open(os.path.join(r, ".gitignore"), "w") as fh:
            fh.write(".venv/\n")
        sh([PY, "-m", "venv", "--without-pip", os.path.join(r, ".venv")], r)
        site = sh([os.path.join(r, ".venv", "bin", "python"), "-c", "import site; print(site.getsitepackages()[0])"], r).stdout.decode().strip()
        with open(os.path.join(site, "__editable__.pkg.pth"), "w") as fh:
            fh.write(os.path.join(r, "src") + "\n")             # what pip -e / uv sync / poetry install write
        rc, out, _ = intel(r, "fbpa", "--base", base, "--tests", "tests/test_calc.py", "--workdir", self.wd, "--link", ".venv",
                           "--json", "--", ".venv/bin/python", "-m", "unittest", "tests.test_calc")
        res = json.loads(out)
        self.assertEqual((rc, res["verdict"], res["tests_run"]), (4, "BLOCKED", False), res)
        self.assertIn("editable install", res["reason"])
        shutil.rmtree(self.wd, ignore_errors=True)
        # the caller's PYTHONPATH (pointing at the real source) never reaches the sandbox
        rc, out, _ = intel(r, "fbpa", "--base", base, "--tests", "tests/test_calc.py", "--workdir", self.wd, "--json", "--",
                           PY, "-m", "unittest", "tests.test_calc", env={"PYTHONPATH": os.path.join(r, "src")})
        res = json.loads(out)
        self.assertNotEqual(res["verdict"], "PASS", res)
        self.assertIn("No module named 'pkg'", open(res["before"]["log"]).read())
        shutil.rmtree(self.wd)
        rc, out, _ = intel(r, "fbpa", "--base", base, "--tests", "tests/test_calc.py", "--workdir", self.wd, "--json", "--",
                           "env", "PYTHONPATH=src", PY, "-m", "unittest", "tests.test_calc")
        self.assertEqual(json.loads(out)["verdict"], "PASS", out)          # the documented remedy works

    def test_workspace_symlinks_in_linked_node_modules_are_blocked(self):
        write(self.repo, {"packages/ui/index.js": "module.exports = 1\n", ".gitignore": "node_modules/\n",
                          "tests/test_calc.py": "x = 1\n"})
        os.makedirs(os.path.join(self.repo, "node_modules", "@org"))
        os.symlink(os.path.join(self.repo, "packages", "ui"), os.path.join(self.repo, "node_modules", "@org", "ui"))
        rc, out, _ = intel(self.repo, "fbpa", "--base", self.base, "--tests", "tests/test_calc.py", "--workdir", self.wd,
                           "--link", "node_modules", "--json", "--", PY, "-c", "pass")
        self.assertEqual((rc, json.loads(out)["verdict"]), (4, "BLOCKED"))

    # ---- sandbox (0.2.1) ----
    def test_sandbox_check_proves_isolation(self):
        rc, out, _ = intel(self.repo, "sandbox-check")
        self.assertEqual(rc, 0, out)
        self.assertIn("RESULT: PASS", out)
        self.assertNotIn("FAIL", out)

    # ---- independent security review of 0.2.1 ----
    ADD_TEST = ("import unittest\nfrom calc import add\n\nclass T(unittest.TestCase):\n    def test_add(self):\n"
                "        self.assertEqual(add(2, 3), 5)\n")

    def _fbpa_args(self, *extra):
        return ["fbpa", "--base", self.base, "--tests", "tests/test_calc.py", "--workdir", self.wd, "--json"] + list(extra)

    def test_link_must_be_an_ignored_untracked_directory(self):
        write(self.repo, {"calc.py": self.FIXED, "tests/test_calc.py": self.ADD_TEST, ".env": "SECRET=1\n",
                          ".gitignore": ".env\n.venv/\n"})
        for link in (".", "tests", "./"):                 # the repository itself, a tracked directory
            rc, out, err = intel(self.repo, *self._fbpa_args("--link", link), "--", PY, "-m", "unittest", "tests.test_calc")
            self.assertEqual(rc, 2, out + err)
            self.assertIn("--link", err)
            self.assertFalse(os.path.exists(self.wd))
        os.makedirs(os.path.join(self.repo, "deps"))      # untracked but not ignored: it is copied, so it would hide the copy
        rc, out, err = intel(self.repo, *self._fbpa_args("--link", "deps"), "--", PY, "-m", "unittest", "tests.test_calc")
        self.assertEqual(rc, 2, out + err)
        os.makedirs(os.path.join(self.repo, ".venv"))
        os.symlink(os.path.join(self.repo, "tests"), os.path.join(self.repo, ".venv", "t"))
        rc, out, err = intel(self.repo, *self._fbpa_args("--link", ".venv/t"), "--", PY, "-m", "unittest", "tests.test_calc")
        self.assertEqual(rc, 2, out + err)                # a symlink into the repository
        self.assertIn("symbolic link", err)

    def test_linked_directory_with_a_socket_is_refused(self):
        import socket
        write(self.repo, {"calc.py": self.FIXED, "tests/test_calc.py": self.ADD_TEST, ".gitignore": ".venv/\n"})
        os.makedirs(os.path.join(self.repo, ".venv"))
        srv = socket.socket(socket.AF_UNIX)
        srv.bind(os.path.join(self.repo, ".venv", "agent.sock"))
        try:
            rc, out, err = intel(self.repo, *self._fbpa_args("--link", ".venv"), "--", PY, "-m", "unittest", "tests.test_calc")
        finally:
            srv.close()
        self.assertEqual(rc, 2, out + err)
        self.assertIn("socket", err)

    def test_carried_test_file_cannot_be_a_symlink_to_a_host_file(self):
        secret = os.path.join(self.tmp, "host-secret.txt")
        open(secret, "w").write("host secret\n")
        write(self.repo, {"calc.py": self.FIXED, "tests/test_calc.py": self.ADD_TEST})
        os.symlink(secret, os.path.join(self.repo, "tests", "fixture.txt"))
        rc, out, err = intel(self.repo, "fbpa", "--base", self.base, "--tests", "tests/test_calc.py,tests/fixture.txt",
                             "--workdir", self.wd, "--json", "--", PY, "-m", "unittest", "tests.test_calc")
        self.assertEqual(rc, 2, out + err)
        self.assertIn("symbolic link", err)

    def test_an_interpreter_inside_the_repository_is_never_run(self):
        marker = os.path.join(self.tmp, "repo-interpreter-ran")
        write(self.repo, {"tools/py": "#!/bin/sh\ntouch %s\nexec %s \"$@\"\n" % (marker, PY)})
        os.chmod(os.path.join(self.repo, "tools", "py"), 0o755)
        rc, out, err = intel(self.repo, "history", env={"AILAB_PYTHON": os.path.join(self.repo, "tools", "py")})
        self.assertFalse(os.path.exists(marker), err)
        self.assertIn("inside the repository", err)

    def test_site_packages_of_an_activated_virtualenv_do_not_run(self):
        venv = os.path.join(self.tmp, "venv-outside")
        sh([PY, "-m", "venv", "--without-pip", venv], self.tmp)
        site = sh([os.path.join(venv, "bin", "python"), "-c", "import site; print(site.getsitepackages()[0])"], self.tmp).stdout.decode().strip()
        marker = os.path.join(self.tmp, "pth-ran")
        with open(os.path.join(site, "evil.pth"), "w") as fh:
            fh.write("import os; open(%r, 'w').write('x')\n" % marker)
        env = {"PATH": os.path.join(venv, "bin") + ":" + os.environ["PATH"], "AILAB_PYTHON": ""}
        rc, out, err = intel(self.repo, "history", env=env)
        self.assertEqual(rc, 0, out + err)
        self.assertFalse(os.path.exists(marker))

    def test_a_memory_limit_failure_is_not_a_fail_before(self):
        # the base is correct but needs 1 GiB; under --mem-mb 512 it dies with MemoryError
        hungry = "def add(a, b):\n    x = bytearray(1024 * 1024 * 1024)\n    return a + b\n"
        write(self.repo, {"calc.py": hungry})
        git(self.repo, "commit", "-qam", "hungry base")
        self.base = git(self.repo, "rev-parse", "HEAD")
        rc, r = self.run_fbpa(self.ADD_TEST, extra=["--mem-mb", "512"])
        self.assertEqual((rc, r["verdict"]), (3, "INCONCLUSIVE"), r)
        self.assertEqual(r["before"]["class"], "limit")

    def test_dev_shm_is_capped_and_the_overflow_is_a_limit(self):
        write(self.repo, {"calc.py": self.FIXED, "tests/test_calc.py": "x = 1\n"})
        rc, out, _ = intel(self.repo, *self._fbpa_args("--tmp-mb", "16"), "--", PY, "-c",
                           "open('/dev/shm/fill', 'wb').write(b'0' * (64 << 20))")
        r = json.loads(out)
        self.assertEqual((rc, r["verdict"], r["before"]["class"]), (3, "INCONCLUSIVE", "limit"), r)
        self.assertIn("No space left on device", open(r["before"]["log"]).read())

    def test_setuid_files_made_by_the_test_are_neutralized(self):
        body = ("import os, unittest\nfrom calc import add\n\nclass T(unittest.TestCase):\n    def test_add(self):\n"
                "        open('suid', 'w').close()\n        os.chmod('suid', 0o6755)\n        self.assertEqual(add(2, 3), 5)\n")
        rc, r = self.run_fbpa(body)
        self.assertEqual(r["verdict"], "PASS", r)
        self.assertEqual(os.stat(self.wd).st_mode & 0o777, 0o700)
        for phase in ("before", "after"):
            self.assertEqual(os.stat(os.path.join(self.wd, phase, "suid")).st_mode & 0o6000, 0, phase)
        self.assertNotIn("AILAB_FBPA_PHASE", r["isolation"]["env"])

    def test_toolchain_must_be_an_install_root_without_sockets(self):
        import socket
        write(self.repo, {"calc.py": self.FIXED, "tests/test_calc.py": self.ADD_TEST})
        plain = os.path.join(self.tmp, "plain")
        os.makedirs(plain)
        rc, out, err = intel(self.repo, *self._fbpa_args("--toolchain", plain), "--", PY, "-m", "unittest", "tests.test_calc")
        self.assertEqual(rc, 2, out + err)
        self.assertIn("no bin/", err)
        os.makedirs(os.path.join(plain, "bin"))
        srv = socket.socket(socket.AF_UNIX)
        srv.bind(os.path.join(plain, "bin", "docker.sock"))
        try:
            rc, out, err = intel(self.repo, *self._fbpa_args("--toolchain", plain), "--", PY, "-m", "unittest", "tests.test_calc")
        finally:
            srv.close()
        self.assertEqual(rc, 2, out + err)
        self.assertIn("socket", err)
        other = os.path.join(self.tmp, "other-user")
        os.makedirs(os.path.join(other, "bin"))
        try:
            os.chown(other, 65534, 65534)                 # needs root and a mapped uid 65534
        except OSError:
            other = None
        if other:
            rc, out, err = intel(self.repo, *self._fbpa_args("--toolchain", other), "--", PY, "-m", "unittest", "tests.test_calc")
            self.assertEqual(rc, 2, out + err)
            self.assertIn("another user", err)

    @unittest.skipUnless(os.uname().machine == "x86_64", "add_key syscall number known for x86_64 only")
    def test_host_session_keyring_is_not_reachable(self):
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        libc.syscall.restype = ctypes.c_long
        key = libc.syscall(248, b"user", b"ailab-test-key", b"secret", 6, ctypes.c_long(-3))   # add_key -> session keyring
        if key < 0:
            self.skipTest("cannot add a key here (errno %d)" % ctypes.get_errno())
        try:
            probe = ("import ctypes, sys\nlibc = ctypes.CDLL(None, use_errno=True)\nlibc.syscall.restype = ctypes.c_long\n"
                     "k = libc.syscall(250, 10, ctypes.c_long(-3), b'user', b'ailab-test-key', 0)\n"
                     "print('FOUND' if k > 0 else 'ABSENT'); sys.exit(1 if k > 0 else 0)\n")
            write(self.repo, {"calc.py": self.FIXED, "tests/test_calc.py": "x = 1\n", "probe.py": probe})
            rc, out, _ = intel(self.repo, *self._fbpa_args(), "--", PY, "probe.py")
            r = json.loads(out)
            self.assertIn("ABSENT", open(r["after"]["log"]).read(), r)
        finally:
            libc.syscall(250, 21, ctypes.c_long(key), ctypes.c_long(-3))                    # KEYCTL_UNLINK

    def test_sandbox_unavailable_reports_blocked(self):
        rc, out, _ = intel(self.repo, "sandbox-check", env={"AILAB_SANDBOX_DISABLE": "1"})
        self.assertEqual(rc, 4, out)
        self.assertIn("RESULT: BLOCKED", out)

    def test_linked_directories_are_read_only(self):
        write(self.repo, {"calc.py": self.FIXED, "tests/test_calc.py": "x = 1\n", ".gitignore": ".venv/\n",
                          ".venv/keep.txt": "dependency\n"})
        probe = "import sys\ntry:\n    open('.venv/x', 'w')\nexcept OSError:\n    sys.exit(0)\nsys.exit(1)\n"
        rc, out, _ = intel(self.repo, *self._fbpa_args("--mode", "refactor", "--link", ".venv"), "--", PY, "-c", probe)
        r = json.loads(out)
        self.assertEqual((rc, r["verdict"]), (0, "PASS"), r)
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".venv", "x")))

    def test_a_failure_caused_by_the_sandbox_is_not_a_verdict(self):
        # needs a host service: passes on the host, cannot inside the sandbox -> INCONCLUSIVE, not FAIL
        body = ("import socket, unittest\nfrom calc import add\n\nclass T(unittest.TestCase):\n    def test_add(self):\n"
                "        socket.create_connection(('127.0.0.1', 9), 2)\n        self.assertEqual(add(2, 3), 5)\n")
        rc, r = self.run_fbpa(body)
        self.assertEqual((rc, r["verdict"]), (3, "INCONCLUSIVE"), r)
        self.assertEqual(r["after"]["class"], "environment")

    def test_env_wrapper_options_with_arguments(self):
        write(self.repo, {"calc.py": self.FIXED, "tests/test_calc.py": self.ADD_TEST})
        rc, out, _ = intel(self.repo, *self._fbpa_args(), "--", "env", "-u", "HOME", PY, "-m", "unittest", "tests.test_calc")
        self.assertEqual(json.loads(out)["verdict"], "PASS", out)

    def test_activated_repository_virtualenv_falls_back_to_the_system_python(self):
        venv = os.path.join(self.repo, ".venv")
        sh([PY, "-m", "venv", "--without-pip", venv], self.repo)
        marker = os.path.join(self.tmp, "venv-site-ran")
        site = sh([os.path.join(venv, "bin", "python"), "-c", "import site; print(site.getsitepackages()[0])"], self.repo).stdout.decode().strip()
        with open(os.path.join(site, "evil.pth"), "w") as fh:
            fh.write("import os; open(%r, 'w').write('x')\n" % marker)
        env = {"PATH": os.path.join(venv, "bin") + ":" + os.environ["PATH"], "AILAB_PYTHON": ""}
        rc, out, err = intel(self.repo, "history", env=env)
        if not any(os.access(c, os.X_OK) for c in ("/usr/bin/python3", "/usr/local/bin/python3")):
            self.skipTest("no system python3 to fall back to")
        self.assertEqual(rc, 0, out + err)
        self.assertNotIn("UNAVAILABLE", out)
        self.assertIn("inside the repository", err)
        self.assertFalse(os.path.exists(marker))

    def test_repository_interpreter_refused_also_for_repo_given_by_option(self):
        marker = os.path.join(self.tmp, "repo-interpreter-ran")
        write(self.repo, {"tools/py": "#!/bin/sh\ntouch %s\nexec %s \"$@\"\n" % (marker, PY)})
        os.chmod(os.path.join(self.repo, "tools", "py"), 0o755)
        rc, out, err = intel(self.tmp, "history", "--repo", self.repo, env={"AILAB_PYTHON": os.path.join(self.repo, "tools", "py")})
        self.assertFalse(os.path.exists(marker), err)

    def test_the_test_cannot_learn_its_phase_or_the_log_path(self):
        probe = ("import os\nprint('FD1=' + os.readlink('/proc/self/fd/1'))\n"
                 "print('PHASE=' + str(sorted(k for k in os.environ if 'PHASE' in k)))\n"
                 "print('MOUNT=' + ' '.join(l.split()[3] for l in open('/proc/self/mountinfo') if l.split()[4] == '/work'))\n"
                 "print('CMD=' + open('/proc/1/cmdline').read().replace(chr(0), ' '))\n")
        write(self.repo, {"calc.py": self.FIXED, "tests/test_calc.py": "x = 1\n"})
        rc, out, _ = intel(self.repo, *self._fbpa_args("--mode", "refactor"), "--", PY, "-c", probe)
        r = json.loads(out)
        for phase in ("before", "after"):
            log = open(r[phase]["log"]).read()
            self.assertIn("FD1=pipe:", log)
            self.assertNotIn(".log", log)
            self.assertIn("PHASE=[]", log)
            # mountinfo and bwrap's own arguments show the copy's host path: it must not name the phase
            self.assertIn("MOUNT=", log)
            self.assertIn("CMD=", log)
            for line in log.splitlines():
                if line.startswith(("MOUNT=", "CMD=")):
                    self.assertNotRegex(line, r"/(before|after)\b", line)
        self.assertTrue(os.path.isdir(os.path.join(self.wd, "before")) and os.path.isdir(os.path.join(self.wd, "after")))

    def test_auto_exposed_runtime_gets_the_toolchain_checks(self):
        import socket
        root = os.path.join(self.tmp, "fakehome", ".local", "share", "uv", "python", "cpython-9.9")
        os.makedirs(os.path.join(root, "bin"))
        write(self.repo, {"calc.py": self.FIXED, "tests/test_calc.py": self.ADD_TEST, ".gitignore": ".venv/\n",
                          ".venv/pyvenv.cfg": "home = %s\n" % os.path.join(root, "bin")})
        srv = socket.socket(socket.AF_UNIX)
        srv.bind(os.path.join(root, "agent.sock"))
        try:
            rc, out, _ = intel(self.repo, *self._fbpa_args("--link", ".venv"), "--", PY, "-m", "unittest", "tests.test_calc")
        finally:
            srv.close()
        r = json.loads(out)
        self.assertEqual((rc, r["verdict"], r["tests_run"]), (4, "BLOCKED", False), r)
        self.assertIn("socket", r["reason"])

    def test_stale_virtualenv_is_blocked_not_a_crash(self):
        gone = os.path.join(self.tmp, "fakehome", ".pyenv", "versions", "3.7.0", "bin")   # an uninstalled runtime
        write(self.repo, {"calc.py": self.FIXED, "tests/test_calc.py": self.ADD_TEST, ".gitignore": ".venv/\n",
                          ".venv/pyvenv.cfg": "home = %s\n" % gone})
        rc, out, _ = intel(self.repo, *self._fbpa_args("--link", ".venv"), "--", PY, "-m", "unittest", "tests.test_calc")
        r = json.loads(out)
        self.assertEqual((rc, r["verdict"], r["tests_run"]), (4, "BLOCKED", False), r)

    def test_auto_exposed_runtime_inside_a_protected_path_is_blocked(self):
        root = os.path.join(self.tmp, "fakehome", ".local", "share", "uv", "python", "cpython-9.8")
        os.makedirs(os.path.join(root, "bin"))
        cfg = os.path.join(self.tmp, "guard.conf")
        with open(cfg, "w") as fh:
            fh.write("production %s\n" % root)
        write(self.repo, {"calc.py": self.FIXED, "tests/test_calc.py": self.ADD_TEST, ".gitignore": ".venv/\n",
                          ".venv/pyvenv.cfg": "home = %s\n" % os.path.join(root, "bin")})
        rc, out, _ = intel(self.repo, *self._fbpa_args("--link", ".venv"), "--", PY, "-m", "unittest", "tests.test_calc",
                           env={"AILAB_GUARD_CONFIG": cfg})
        r = json.loads(out)
        self.assertEqual((rc, r["verdict"]), (4, "BLOCKED"), r)
        self.assertIn("protected path", r["reason"])

    def test_abbreviated_repo_option_cannot_bypass_the_interpreter_check(self):
        marker = os.path.join(self.tmp, "repo-interpreter-ran")
        write(self.repo, {"tools/py": "#!/bin/sh\ntouch %s\nexec %s \"$@\"\n" % (marker, PY)})
        os.chmod(os.path.join(self.repo, "tools", "py"), 0o755)
        for args in (["--rep", self.repo], ["--re=" + self.repo]):
            rc, out, err = intel(self.tmp, "history", *args, env={"AILAB_PYTHON": os.path.join(self.repo, "tools", "py")})
            self.assertFalse(os.path.exists(marker), (args, err))
        rc, out, err = intel(self.repo, "history", "--rep", self.repo)
        self.assertEqual(rc, 2, out + err)                 # intel.py itself accepts no abbreviations

    def test_repository_of_another_owner_is_still_checked(self):
        marker = os.path.join(self.tmp, "repo-interpreter-ran")
        write(self.repo, {"tools/py": "#!/bin/sh\ntouch %s\nexec %s \"$@\"\n" % (marker, PY)})
        os.chmod(os.path.join(self.repo, "tools", "py"), 0o755)
        try:
            os.chown(self.repo, 65534, 65534)               # git then reports "dubious ownership"
        except OSError:
            self.skipTest("needs root and a mapped uid 65534")
        intel(self.repo, "history", env={"AILAB_PYTHON": os.path.join(self.repo, "tools", "py")})
        self.assertFalse(os.path.exists(marker))

    def test_limit_and_environment_signatures(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("intel_mod", os.path.join(os.path.dirname(INTEL), "intel.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        limit = ["RuntimeError: can't start new thread", "numpy.core._exceptions._ArrayMemoryError: Unable to allocate 8 GiB",
                 "java.lang.OutOfMemoryError: Java heap space", "memory allocation of 1048576 bytes failed",
                 "OSError: [Errno 28] No space left on device", "BlockingIOError: [Errno 11] Resource temporarily unavailable",
                 "RangeError: WebAssembly.Memory(): could not allocate memory", "MemoryError",
                 "RangeError: Array buffer allocation failed", "Cannot allocate Wasm memory for new instance",
                 "Unhandled exception. System.OutOfMemoryException", "# Fatal process out of memory: Zone"]
        env = ["ConnectionRefusedError: [Errno 111] Connection refused", "Cannot connect to the Docker daemon at unix:///var/run/docker.sock",
               "psycopg2.OperationalError: connection to server at \"localhost\" (127.0.0.1), port 5432 failed",
               "socket.gaierror: [Errno -3] Temporary failure in name resolution", "OSError: [Errno 30] Read-only file system"]
        for text in limit:
            self.assertEqual(m.classify_run(1, "Traceback\n" + text + "\nFAILED (errors=1)\n", False)[0], "limit", text)
        for text in env:
            self.assertEqual(m.classify_run(1, "Traceback\n" + text + "\nFAILED (errors=1)\n", False)[0], "environment", text)
        self.assertEqual(m.classify_run(152, "", False)[0], "limit")       # SIGXCPU through bwrap
        self.assertEqual(m.classify_run(1, "AssertionError: 4 != 5\nFAILED (failures=1)\n", False)[0], "assert-fail")

    def test_sandbox_check_compares_identity_not_path(self):
        # run outside a repository from /tmp: inside the sandbox /tmp is a private tmpfs, not the host's /tmp
        rc, out, _ = intel(tempfile.gettempdir(), "sandbox-check")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("FAIL", out)

    def test_repository_inside_an_exposed_system_directory_is_blocked(self):
        # /usr is mounted read-only in the sandbox; a "repository" there would be readable by the test
        rc, out, _ = intel("/usr/lib", "sandbox-check")
        self.assertEqual(rc, 4, out)
        self.assertIn("RESULT: BLOCKED", out)
        self.assertIn("inside a directory the sandbox exposes read-only", out)

    @unittest.skipUnless(os.geteuid() == 0 and os.path.isdir("/usr/local/src") and os.access("/usr/local/src", os.W_OK),
                         "needs a writable /usr/local/src (root)")
    def test_fbpa_refuses_a_repository_under_usr(self):
        top = tempfile.mkdtemp(prefix="ailab-intel-usr.", dir="/usr/local/src")
        try:
            r = os.path.join(top, "repo")
            os.makedirs(r)
            git(r, "init", "-q")
            write(r, {"calc.py": self.BUGGY, "tests/__init__.py": ""})
            git(r, "add", "--", ".")
            git(r, "commit", "-qm", "base")
            base = git(r, "rev-parse", "HEAD")
            marker = os.path.join(self.tmp, "ran")
            write(r, {"tests/test_calc.py": "x = 1\n"})
            rc, out, _ = intel(r, "fbpa", "--base", base, "--tests", "tests/test_calc.py", "--workdir", self.wd, "--json",
                               "--", PY, "-c", "open(%r, 'w').write('x')" % marker)
            res = json.loads(out)
            self.assertEqual((rc, res["verdict"], res["tests_run"]), (4, "BLOCKED", False), res)
            self.assertIn("exposes read-only", res["reason"])
            self.assertFalse(os.path.exists(marker))
        finally:
            shutil.rmtree(top)

    def test_no_sandbox_means_blocked_and_nothing_runs(self):
        marker = os.path.join(self.tmp, "ran-on-host")
        write(self.repo, {"tests/test_calc.py": "x = 1\n"})
        rc, out, _ = intel(self.repo, "fbpa", "--base", self.base, "--tests", "tests/test_calc.py", "--workdir", self.wd,
                           "--json", "--", PY, "-c", "open(%r, 'w').write('x')" % marker, env={"AILAB_SANDBOX_DISABLE": "1"})
        r = json.loads(out)
        self.assertEqual((rc, r["verdict"], r["tests_run"]), (4, "BLOCKED", False), r)
        self.assertIn("no secure execution environment", r["reason"])
        self.assertFalse(os.path.exists(marker))
        rc, out, _ = intel(self.repo, "sandbox-check", env={"AILAB_SANDBOX_DISABLE": "1"})
        self.assertEqual(rc, 4)
        self.assertIn("RESULT: BLOCKED", out)

    def test_text_report(self):
        write(self.repo, {"calc.py": self.FIXED, "tests/test_calc.py": "import unittest\nfrom calc import add\n\nclass T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n"})
        rc, out, err = intel(self.repo, "fbpa", "--base", self.base, "--tests", "tests/test_calc.py", "--workdir", self.wd,
                             "--", PY, "-m", "unittest", "tests.test_calc")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("FBPA: PASS", out)
        self.assertIn("ISOLATION: bubblewrap sandbox, no network", out)
        self.assertIn("  | ", out)

    def test_refused_user_namespaces_mean_blocked_and_nothing_runs(self):
        # The outer sandbox forbids new user namespaces, as Docker's default seccomp profile does.
        # Where this machine already refuses them (agent-long), fbpa is checked directly.
        marker = os.path.join(self.tmp, "ran-without-sandbox")
        write(self.repo, {"tests/test_calc.py": "x = 1\n"})
        fbpa = ["bash", INTEL, "fbpa", *TOOLCHAIN, "--base", self.base, "--tests", "tests/test_calc.py",
                "--workdir", self.wd, "--json", "--", PY, "-c", "open(%r, 'w').write('x')" % marker]
        outer = ["/usr/bin/bwrap", "--bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--unshare-user", "--disable-userns", "--"]
        if SANDBOX:
            probe = subprocess.run(outer + ["true"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if probe.returncode != 0:
                self.skipTest("cannot emulate refused user namespaces here")
            fbpa = outer + fbpa
        p = subprocess.run(fbpa, cwd=self.repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           env=dict(os.environ, AILAB_PYTHON=PY))
        r = json.loads(p.stdout.decode())
        self.assertEqual((p.returncode, r["verdict"], r["tests_run"]), (4, "BLOCKED", False), r)
        self.assertFalse(os.path.exists(marker))

    def test_a_bwrap_on_path_cannot_replace_the_sandbox(self):
        fake = os.path.join(self.tmp, "fakebin")
        os.makedirs(fake)
        marker = os.path.join(self.tmp, "fake-bwrap-used")
        with open(os.path.join(fake, "bwrap"), "w") as fh:
            fh.write("#!/bin/sh\ntouch %s\nwhile [ \"$1\" != -- ]; do shift; done; shift; exec \"$@\"\n" % marker)
        os.chmod(os.path.join(fake, "bwrap"), 0o755)
        rc, r = self.run_fbpa("import unittest\nfrom calc import add\n\nclass T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n")
        shutil.rmtree(self.wd)
        env = {"PATH": fake + ":" + os.environ["PATH"]}
        rc, out, _ = intel(self.repo, "fbpa", "--base", self.base, "--tests", "tests/test_calc.py", "--workdir", self.wd,
                           "--json", "--", PY, "-m", "unittest", "tests.test_calc", env=env)
        self.assertEqual(json.loads(out)["verdict"], "PASS", out)
        self.assertFalse(os.path.exists(marker))

    def test_isolation_holds_inside_a_real_check(self):
        listener = __import__("socket").socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        home = os.path.realpath(os.path.expanduser("~"))
        home_secret = home if home not in ("/", "/tmp") and not home.startswith("/usr/") else "/nonexistent-home"
        try:
            probe = textwrap.dedent("""\
                import os, socket, sys, unittest
                def reachable(host, port):
                    try:
                        socket.create_connection((host, port), 2).close()
                        return True
                    except OSError:
                        return False
                class T(unittest.TestCase):
                    def test_isolated(self):
                        self.assertFalse(os.path.exists(%r))           # the repository
                        self.assertFalse(os.path.exists(%r))           # the real home directory
                        self.assertFalse(os.path.exists("/run"))       # the Docker socket lives there
                        self.assertNotIn("AILAB_TEST_CANARY", os.environ)
                        self.assertEqual(os.environ.get("DECLARED"), "yes")
                        self.assertFalse(reachable("1.1.1.1", 53))
                        self.assertFalse(reachable("127.0.0.1", %d))  # a service on the host's loopback
                        open("written.txt", "w").write("ok")             # the copy is writable
                """) % (self.repo, home_secret, port)
            write(self.repo, {"tests/test_probe.py": probe})
            rc, out, _ = intel(self.repo, "fbpa", "--mode", "refactor", "--base", self.base, "--tests", "tests/test_probe.py",
                               "--workdir", self.wd, "--env", "DECLARED=yes", "--json", "--", PY, "-m", "unittest", "tests.test_probe",
                               env={"AILAB_TEST_CANARY": "leak"})
            r = json.loads(out)
            self.assertEqual(r["verdict"], "PASS", open(r["before"]["log"]).read())
            self.assertEqual(r["isolation"]["network"], "none")
            self.assertTrue(os.path.exists(os.path.join(self.wd, "before", "written.txt")))
        finally:
            listener.close()

    def test_timeout_kills_the_whole_process_tree(self):
        tag = "ailab-orphan-%d" % os.getpid()
        write(self.repo, {"tests/test_calc.py": "x = 1\n"})
        cmd = [PY, "-c", "import subprocess, time; subprocess.Popen(['sh', '-c', 'exec sleep 300 # %s']); time.sleep(300)" % tag]
        rc, out, _ = intel(self.repo, "fbpa", "--base", self.base, "--tests", "tests/test_calc.py", "--workdir", self.wd,
                           "--timeout", "2", "--json", "--", *cmd)
        self.assertEqual(json.loads(out)["verdict"], "INCONCLUSIVE", out)
        time.sleep(0.5)
        left = []
        for pid in os.listdir("/proc"):
            if pid.isdigit():
                try:
                    if tag.encode() in open("/proc/%s/cmdline" % pid, "rb").read():
                        left.append(pid)
                except OSError:
                    pass
        self.assertEqual(left, [])

    def test_memory_limit_applies(self):
        write(self.repo, {"tests/test_calc.py": "x = 1\n"})
        rc, out, _ = intel(self.repo, "fbpa", "--mode", "refactor", "--base", self.base, "--tests", "tests/test_calc.py",
                           "--workdir", self.wd, "--mem-mb", "300", "--json", "--", PY, "-c", "b = bytearray(1024 * 1024 * 1024)")
        r = json.loads(out)
        self.assertNotEqual(r["verdict"], "PASS", r)
        self.assertIn("MemoryError", open(r["before"]["log"]).read())

    def test_unsafe_toolchains_are_refused(self):
        write(self.repo, {"tests/test_calc.py": "x = 1\n"})
        home = os.path.join(self.tmp, "home")
        os.makedirs(os.path.join(home, ".ssh"))
        prod = os.path.join(self.tmp, "prod")
        os.makedirs(prod)
        cfg = os.path.join(self.tmp, "guard.conf")
        open(cfg, "w").write("production %s\n" % prod)
        env = {"HOME": home, "AILAB_GUARD_CONFIG": cfg}
        for t in ("/", home, os.path.join(home, ".ssh"), self.repo, os.path.dirname(self.repo), prod, "relative/dir"):
            rc, out, err = intel(self.repo, "fbpa", "--base", self.base, "--tests", "tests/test_calc.py", "--workdir", self.wd,
                                 "--toolchain", t, "--", "true", env=env)
            self.assertEqual(rc, 2, (t, out, err))
            self.assertFalse(os.path.exists(self.wd))

    def test_commands_outside_the_sandbox_paths_are_blocked(self):
        write(self.repo, {"tests/test_calc.py": "x = 1\n"})
        script = os.path.join(self.tmp, "outside.sh")
        open(script, "w").write("#!/bin/sh\necho hi\n")
        os.chmod(script, 0o755)
        for cmd, why in (([script], "outside the paths the sandbox exposes"), (["no-such-command-xyz"], "not on the sandbox PATH")):
            rc, out, _ = intel(self.repo, "fbpa", "--base", self.base, "--tests", "tests/test_calc.py", "--workdir", self.wd,
                               "--json", "--", *cmd)
            r = json.loads(out)
            self.assertEqual((rc, r["verdict"]), (4, "BLOCKED"), r)
            self.assertIn(why, r["reason"])
            shutil.rmtree(self.wd, ignore_errors=True)

    def test_link_to_git_metadata_is_refused(self):
        write(self.repo, {"tests/test_calc.py": "x = 1\n"})
        rc, out, err = intel(self.repo, "fbpa", "--base", self.base, "--tests", "tests/test_calc.py", "--workdir", self.wd,
                             "--link", ".git", "--", "true")
        self.assertEqual(rc, 2)
        self.assertIn("git metadata", err)

    def test_git_in_the_copies_does_not_reach_a_parent_repository(self):
        outer = os.path.join(self.tmp, "outer")
        os.makedirs(outer)
        git(outer, "init", "-q")
        wd = os.path.join(outer, "scratch", "fbpa")
        write(self.repo, {"tests/test_calc.py": "x = 1\n"})
        probe = "import subprocess, sys; sys.exit(0 if subprocess.run(['git', 'rev-parse'], capture_output=True).returncode else 1)"
        rc, out, _ = intel(self.repo, "fbpa", "--mode", "refactor", "--base", self.base, "--tests", "tests/test_calc.py",
                           "--workdir", wd, "--json", "--", PY, "-c", probe)
        self.assertEqual(json.loads(out)["verdict"], "PASS", out)

    def test_missing_test_runner_is_blocked_not_fail(self):
        rc, r = self.run_fbpa("x = 1\n", cmd=[PY, "-m", "no_such_runner_xyz", "tests/test_calc.py"])
        self.assertEqual((rc, r["verdict"]), (4, "BLOCKED"), r)

    @unittest.skipUnless(subprocess.run([PY, "-c", "import pytest"], stderr=subprocess.DEVNULL).returncode == 0,
                         "pytest not importable by this interpreter")
    def test_pytest_collection_error_is_inconclusive(self):
        write(self.repo, {"calc.py": self.FIXED + "\ndef mul(a, b):\n    return a * b\n",
                          "tests/test_calc.py": "from calc import add, mul\n\ndef test_add():\n    assert add(2, 3) == 5\n"})
        rc, out, _ = intel(self.repo, "fbpa", "--base", self.base, "--tests", "tests/test_calc.py", "--workdir", self.wd,
                           "--json", "--", PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_calc.py")
        r = json.loads(out)
        self.assertEqual(r["verdict"], "INCONCLUSIVE", r)


class Metrics(Base):
    def test_metrics_reads_wp_files_and_known_categories_only(self):
        r = self.new_repo()
        write(r, {".claude/wp/A-1.md": """\
            # A-1 — legacy WP (0.1.x format)
            Status: ACCEPTED
            Tier: T2   Created: 2026-10-01   Repo HEAD at start: abc
            ## Log
            - Review: 2 findings / 2 fixed / 0 documented
            - Post-acceptance rework (21d): No
            """,
                  ".claude/wp/B-2.md": """\
            # B-2 — v2 WP
            Status: REVIEW
            Tier: T3   Type: bugfix   Size: M   Created: 2026-11-01   Repo HEAD at start: def
            ## Log
            - 2026-11-02 review round 1/3: SPEC FAIL / ENGINEERING PASS WITH NOTES — findings: unrequested-behaviour×1, test-weak×2, maxx9 — snapshot aaa
            - 2026-11-02 review round 2/3: SPEC PASS / ENGINEERING PASS — findings: none — snapshot bbb
            - Review: 3 findings / 3 fixed / 0 documented
            """})
        m = jintel(self.tmp, "metrics", r)
        rows = {w["wp"]: w for w in m["work_packages"]}
        self.assertEqual(rows["A-1"]["tier"], "T2")
        self.assertEqual(rows["A-1"]["rounds"], 0)
        self.assertEqual((rows["B-2"]["type"], rows["B-2"]["size"], rows["B-2"]["rounds"]), ("bugfix", "M", 2))
        self.assertEqual(m["finding_categories"], {"unrequested-behaviour": 1, "test-weak": 2})


class Wrapper(Base):
    def test_unavailable_python_degrades_explicitly(self):
        fake = os.path.join(self.tmp, "bin")          # a PATH with no python at all
        os.makedirs(fake)
        bash = shutil.which("bash")
        env = {"PATH": fake, "HOME": self.tmp, "AILAB_PYTHON": "/nonexistent"}
        p = subprocess.run([bash, INTEL, "impact"], cwd=self.tmp, stdout=subprocess.PIPE, env=env)
        self.assertEqual(p.returncode, 0)
        self.assertIn(b"UNAVAILABLE", p.stdout)
        p = subprocess.run([bash, INTEL, "fbpa"], cwd=self.tmp, stdout=subprocess.PIPE, env=env)
        self.assertEqual(p.returncode, 4)
        self.assertIn(b"BLOCKED", p.stdout)

    def test_performance_on_a_generated_repository(self):
        r = self.new_repo()
        files = {}
        for i in range(600):
            files["pkg/m%03d.py" % i] = "from pkg import m%03d\nimport os\n" % max(i - 1, 0)
        files["pkg/__init__.py"] = ""
        write(r, files)
        git(r, "add", "--", ".")
        git(r, "commit", "-qm", "base")
        t0 = time.time()
        rc, out, _ = intel(r, "impact", "--depth", "3", "pkg/m000.py")
        self.assertEqual(rc, 0)
        self.assertLess(time.time() - t0, 20)
        self.assertIn("direct (1): pkg/m001.py", out)


if __name__ == "__main__":
    unittest.main()
