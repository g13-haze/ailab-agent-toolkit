#!/usr/bin/env python3
"""Regression tests for import resolution, determinism, signatures, callers and discovery.

Offline; every fixture lives in a temporary directory.
"""

import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from ast_scanner import extract_python_ast, extract_regex_symbols, extract_tree_sitter_symbols, scan_codebase
from common import discover_source_files, get_parser_for_language
from dependency_graph import build_dependency_graph, normalize_import_path
from symbol_graph import build_symbol_graph, export_signatures_markdown


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


IMPORT_FIXTURE = {
    "src/pkg/__init__.py": "",
    "src/pkg/core.py": (
        "import re\n"
        "import time\n"
        "import os\n"
        "from pkg.analysis import link_evidence as le\n"
        "from . import helpers\n"
        "from .sub.mod import thing\n"
        "import pkg.missing_mod\n"
        "try:\n"
        "    from pkg import optional_mod\n"
        "except ImportError:\n"
        "    optional_mod = None\n"
        "\n"
        "def work():\n"
        "    from pkg.local_only import value\n"
        "    return value\n"
    ),
    "src/pkg/analysis/__init__.py": "",
    "src/pkg/analysis/link_evidence.py": "def build():\n    return 1\n",
    "src/pkg/helpers.py": "",
    "src/pkg/sub/__init__.py": "",
    "src/pkg/sub/mod.py": "thing = 1\n",
    "src/pkg/optional_mod.py": "",
    "src/pkg/local_only.py": "value = 2\n",
    "tests/test_regressions.py": "import re\n",
    "scripts/jupiter/ksi_run_timeline.py": "import time\n",
    "scripts/tool.py": "import analysis.link_evidence\n",
    "frontend/src/pages/hosts.ts": "export const hosts = [];\n",
}


class ImportFixtureMixin:
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ccg_reg_")).resolve()
        for rel, text in IMPORT_FIXTURE.items():
            write(self.root, rel, text)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)


class TestPythonImportResolution(ImportFixtureMixin, unittest.TestCase):
    def test_import_records_are_complete(self):
        code = (self.root / "src/pkg/core.py").read_bytes()
        imports = extract_python_ast(code, Path("core.py"))["imports"]
        by_raw = {i["raw"]: i for i in imports}
        le = by_raw["from pkg.analysis import link_evidence as le"]
        self.assertEqual(le["module"], "pkg.analysis")
        self.assertEqual(le["level"], 0)
        self.assertEqual(le["names"], [{"name": "link_evidence", "alias": "le"}])
        self.assertEqual(le["symbols"], ["le"])
        rel = by_raw["from .sub.mod import thing"]
        self.assertEqual((rel["source"], rel["module"], rel["level"]), (".sub.mod", "sub.mod", 1))
        self.assertEqual(by_raw["from pkg import optional_mod"]["scope"], "module")
        local = by_raw["from pkg.local_only import value"]
        self.assertEqual(local["scope"], "local")
        self.assertEqual(local["line"], 14)

    def test_edges_are_exact(self):
        graph = build_dependency_graph(scan_codebase(self.root))
        self.assertEqual(
            graph["internal_graph"]["src/pkg/core.py"],
            [
                "src/pkg/analysis/link_evidence.py",
                "src/pkg/helpers.py",
                "src/pkg/local_only.py",
                "src/pkg/optional_mod.py",
                "src/pkg/sub/mod.py",
            ],
        )
        # Standard-library imports never resolve to look-alike project files
        for path in ("tests/test_regressions.py", "scripts/jupiter/ksi_run_timeline.py"):
            self.assertEqual(graph["internal_graph"][path], [])
        self.assertEqual(graph["reverse_graph"]["frontend/src/pages/hosts.ts"], [])
        self.assertEqual(graph["reverse_graph"]["tests/test_regressions.py"], [])
        # A partial suffix inside a package is not importable and is not matched
        self.assertEqual(graph["internal_graph"]["scripts/tool.py"], [])
        # An unknown module under an internal package is unresolved, not external
        self.assertEqual(graph["unresolved_imports"]["src/pkg/core.py"], ["pkg.missing_mod"])
        self.assertNotIn("pkg", graph["external_dependencies"])

    def test_non_python_specifiers_never_match_by_substring(self):
        known = {"frontend/src/pages/hosts.ts", "tests/test_regressions.py", "src/react/index.ts",
                 "src/main/java/com/example/Foo.java"}
        for spec in ("os", "re", "time", "react", "hosts"):
            target, internal = normalize_import_path(spec, "frontend/src/main.ts", known)
            self.assertFalse(internal, spec)
        target, internal = normalize_import_path("com.example.Foo", "src/main/java/App.java", known)
        self.assertEqual((target, internal), ("src/main/java/com/example/Foo.java", True))


class TestImportRoots(unittest.TestCase):
    """Absolute imports resolve only from the repo root, `src`, or a non-package script dir."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ccg_roots_")).resolve()
        files = {
            # Python 3 has no implicit relative import: proj/redis.py is not `redis`
            "proj/__init__.py": "",
            "proj/redis.py": "def from_url(u):\n    return u\n",
            "proj/cache.py": "import redis\n\ndef get():\n    return redis.from_url('x')\n",
            # A non-package example directory is not a source root
            "examples/flask.py": "def Flask():\n    return 1\n",
            "src/app/__init__.py": "",
            "src/app/main.py": "import flask\n\ndef serve():\n    return flask.Flask()\n",
            # Script-style sibling import in a non-package directory still resolves
            "scripts/run_tool.py": "import tool_helpers\n\ndef main():\n    return tool_helpers.assist()\n",
            "scripts/tool_helpers.py": "def assist():\n    return 1\n",
            # A root-level file named like a stdlib module never captures the stdlib import
            "json.py": "def loads(x):\n    return x\n",
            "src/app/codec.py": "import json\n\ndef parse(x):\n    return json.loads(x)\n",
            # A file stem containing a dot is not an importable module
            "celery.schedules.py": "def crontab():\n    return 1\n",
            "src/app/beat.py": "import celery.schedules\n\ndef plan():\n    return celery.schedules.crontab()\n",
            # A package outside `src` resolves from its import root (the first non-package ancestor)
            "backend/app/__init__.py": "",
            "backend/app/db.py": "def get():\n    return 1\n",
            "backend/app/main.py": "from app.db import get\nimport app.db\n\ndef serve():\n    get()\n    app.db.get()\n",
            "backend/manage.py": "from app.main import serve\n\ndef main():\n    serve()\n",
            # A project module reachable only through a sys.path change is unresolved, not external
            "tests/__init__.py": "",
            "tests/fixtures/c0/build_fx.py": "def build():\n    return 1\n",
            "tests/test_fx.py": "def test_build():\n    import build_fx\n    build_fx.build()\n",
        }
        for rel, text in files.items():
            write(self.root, rel, text)
        manifest = scan_codebase(self.root)
        self.deps = build_dependency_graph(manifest)
        self.syms = build_symbol_graph(manifest)["symbols"]

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_package_sibling_named_like_a_library_is_not_imported(self):
        self.assertEqual(self.deps["internal_graph"]["proj/cache.py"], [])
        self.assertIn("redis", self.deps["external_dependencies"])
        self.assertEqual(self.syms["proj/redis.py::from_url"]["callers"], [])

    def test_example_directory_is_not_a_source_root(self):
        self.assertEqual(self.deps["internal_graph"]["src/app/main.py"], [])
        self.assertIn("flask", self.deps["external_dependencies"])
        self.assertEqual(self.syms["examples/flask.py::Flask"]["callers"], [])

    def test_script_directory_sibling_import_resolves(self):
        self.assertEqual(self.deps["internal_graph"]["scripts/run_tool.py"], ["scripts/tool_helpers.py"])
        self.assertEqual(self.syms["scripts/tool_helpers.py::assist"]["callers"], ["scripts/run_tool.py::main"])

    def test_stdlib_import_never_resolves_to_project_file(self):
        self.assertEqual(self.deps["internal_graph"]["src/app/codec.py"], [])
        self.assertEqual(self.deps["reverse_graph"]["json.py"], [])
        self.assertEqual(self.syms["json.py::loads"]["callers"], [])

    def test_dotted_file_stem_is_not_a_module(self):
        self.assertEqual(self.deps["internal_graph"]["src/app/beat.py"], [])
        self.assertIn("celery", self.deps["external_dependencies"])
        self.assertEqual(self.syms["celery.schedules.py::crontab"]["callers"], [])

    def test_package_outside_src_resolves_from_its_import_root(self):
        self.assertEqual(self.deps["internal_graph"]["backend/app/main.py"], ["backend/app/db.py"])
        self.assertEqual(self.deps["internal_graph"]["backend/manage.py"], ["backend/app/main.py"])
        self.assertEqual(self.syms["backend/app/db.py::get"]["callers"], ["backend/app/main.py::serve"])
        self.assertEqual(self.syms["backend/app/main.py::serve"]["callers"], ["backend/manage.py::main"])
        self.assertNotIn("app", self.deps["external_dependencies"])

    def test_unreachable_project_module_is_unresolved_not_external(self):
        self.assertEqual(self.deps["internal_graph"]["tests/test_fx.py"], [])
        self.assertEqual(self.deps["unresolved_imports"]["tests/test_fx.py"], ["build_fx"])
        self.assertNotIn("build_fx", self.deps["external_dependencies"])
        self.assertEqual(self.syms["tests/fixtures/c0/build_fx.py::build"]["callers"], [])


class TestDeterminism(ImportFixtureMixin, unittest.TestCase):
    def test_dependency_graph_is_identical_across_hash_seeds(self):
        manifest = self.root / "manifest.json"
        manifest.write_text(json.dumps(scan_codebase(self.root)), encoding="utf-8")
        outputs = []
        for seed in ("1", "2", "3"):
            out = self.root / f"graph_{seed}.json"
            env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONDONTWRITEBYTECODE="1")
            subprocess.run(
                [sys.executable, "-B", str(SCRIPTS / "dependency_graph.py"), "--manifest", str(manifest),
                 "--out", str(out)],
                check=True, capture_output=True, env=env,
            )
            outputs.append(out.read_bytes())
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(outputs[0], outputs[2])


class TestPythonSignatures(unittest.TestCase):
    def test_complete_signature(self):
        code = b"def f(a, /, b: int = 1, *args, c, d=2, **kw) -> None:\n    pass\n"
        fn = extract_python_ast(code, Path("m.py"))["functions"][0]
        self.assertEqual(fn["signature"], "f(a, /, b: int = 1, *args, c, d=2, **kw) -> None")
        kinds = [(a["name"], a["kind"], a["default"]) for a in fn["args"]]
        self.assertEqual(kinds, [
            ("a", "positional_only", None),
            ("b", "positional_or_keyword", "1"),
            ("args", "var_positional", None),
            ("c", "keyword_only", None),
            ("d", "keyword_only", "2"),
            ("kw", "var_keyword", None),
        ])

    def test_method_bare_star_redaction_and_truncation(self):
        code = (
            "class Runner:\n"
            "    def execute(self, run_id: int, *, api_token: str = 'abc', resubmit: bool = False,\n"
            "                note='" + "x" * 40 + "', table=[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]):\n"
            "        pass\n"
        ).encode()
        method = extract_python_ast(code, Path("m.py"))["classes"][0]["methods"][0]
        sig = method["signature"]
        self.assertTrue(sig.startswith("execute(self, run_id: int, *, api_token: str = '<redacted>', "
                                       "resubmit: bool = False, note='<redacted>', table=[1, 2, 3"), sig)
        self.assertNotIn("abc", sig)
        table_default = method["args"][-1]["default"]
        self.assertTrue(table_default.endswith("...") and len(table_default) <= 40, table_default)

    def test_scanner_runs_without_match_statement_nodes(self):
        """Python 3.9 has no ast.MatchAs/MatchStar/MatchMapping; the scanner must still work."""
        import ast
        import importlib
        import ast_scanner
        missing = ("MatchAs", "MatchStar", "MatchMapping")
        saved = {name: getattr(ast, name) for name in missing if hasattr(ast, name)}
        try:
            for name in saved:
                delattr(ast, name)
            module = importlib.reload(ast_scanner)
            fn = module.extract_python_ast(b"def f(x):\n    y = x\n    return y\n", Path("m.py"))["functions"][0]
            self.assertEqual(fn["local_names"], ["x", "y"])
        finally:
            for name, value in saved.items():
                setattr(ast, name, value)
            importlib.reload(ast_scanner)

    def test_secret_defaults_are_redacted(self):
        code = (
            "def connect(access_key='AKIAABCDEFGHIJKLMNOP', private_key='k3y', pwd='hunter2',\n"
            "            dsn='postgres://u:pw@h/db', token=f'sk-{1}abc', author='Bob', api_key=None,\n"
            "            retries=3, url='postgres://u:pw@h/db', password=get_pw(), session_key=-1,\n"
            "            client_secret=DEFAULT_SECRET):\n"
            "    pass\n"
        ).encode()
        sig = extract_python_ast(code, Path("m.py"))["functions"][0]["signature"]
        self.assertEqual(
            sig,
            "connect(access_key='<redacted>', private_key='<redacted>', pwd='<redacted>', "
            "dsn='<redacted>', token='<redacted>', author='Bob', api_key=None, retries=3, "
            "url='<redacted>', password='<redacted>', session_key=-1, client_secret='<redacted>')",
        )
        for leaked in ("AKIA", "k3y", "hunter2", "pw@", "sk-", "get_pw", "DEFAULT_SECRET"):
            self.assertNotIn(leaked, sig)


class TestCallerResolution(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ccg_calls_")).resolve()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def build(self, files):
        for rel, text in files.items():
            write(self.root, rel, text)
        return build_symbol_graph(scan_codebase(self.root))

    def test_python_callers_are_scope_aware(self):
        graph = self.build({
            "a.py": "def execute():\n    return 1\n",
            "b.py": "def execute():\n    return 2\n",
            "c.py": (
                "import a as mod\n"
                "from a import execute\n"
                "\n"
                "def run(obj, cur):\n"
                "    execute()\n"
                "    obj.execute()\n"
                "    cur.execute('SELECT 1')\n"
                "\n"
                "def via_alias():\n"
                "    mod.execute()\n"
                "\n"
                "class Base:\n"
                "    def save(self):\n"
                "        return 0\n"
                "\n"
                "class Svc(Base):\n"
                "    def go(self):\n"
                "        self.helper()\n"
                "        self.save()\n"
                "        self.db.get()\n"
                "    def helper(self):\n"
                "        return 1\n"
            ),
            "d.py": (
                "def uses_local():\n"
                "    from b import execute\n"
                "    execute()\n"
                "\n"
                "def no_import():\n"
                "    execute()\n"
            ),
            # A function-local import wins over a same-file definition of the same name
            "e.py": (
                "def execute():\n"
                "    return 0\n"
                "\n"
                "def run_local():\n"
                "    from b import execute\n"
                "    execute()\n"
            ),
            # One name bound to two different targets is unknown, never either target
            "f.py": (
                "try:\n"
                "    from a import execute\n"
                "except ImportError:\n"
                "    from b import execute\n"
                "\n"
                "def ambiguous():\n"
                "    execute()\n"
            ),
        })
        syms = graph["symbols"]
        self.assertEqual(syms["a.py::execute"]["callers"], ["c.py::run", "c.py::via_alias"])
        self.assertEqual(syms["b.py::execute"]["callers"], ["d.py::uses_local", "e.py::run_local"])
        self.assertEqual(syms["e.py::execute"]["callers"], [])
        self.assertEqual(syms["f.py::ambiguous"]["unresolved_callees"], ["execute"])
        run = syms["c.py::run"]
        self.assertEqual(run["resolved_callees"], ["a.py::execute"])
        self.assertEqual(sorted(run["unresolved_callees"]), ["cur.execute", "obj.execute"])
        self.assertEqual(syms["d.py::no_import"]["resolved_callees"], [])
        self.assertEqual(syms["d.py::no_import"]["unresolved_callees"], ["execute"])
        self.assertEqual(syms["c.py::Svc.helper"]["callers"], ["c.py::Svc.go"])
        self.assertEqual(syms["c.py::Base.save"]["callers"], ["c.py::Svc.go"])
        self.assertIn("self.db.get", syms["c.py::Svc.go"]["unresolved_callees"])
        stats = graph["call_resolution"]
        self.assertEqual(stats["total_calls"], stats["resolved"] + stats["unknown"])
        md = export_signatures_markdown(graph)
        self.assertIn("statically resolved callers only", md)
        self.assertIn("lower bound", md)

    def test_names_bound_in_the_calling_function_are_unknown(self):
        graph = self.build({
            "a.py": (
                "def execute():\n    return 1\n\n"
                "def check():\n    return 2\n\n"
                "def helper():\n    return 3\n\n"
                "def send():\n    return 4\n\n"
                "def by_param(execute):\n    execute()\n\n"
                "def by_loop(checks):\n    for check in checks:\n        check()\n\n"
                "def by_nested():\n    def helper():\n        return 0\n    helper()\n\n"
                "def by_assignment(conn):\n    send = conn.send\n    send()\n\n"
                "def by_comprehension(items):\n    return [check() for check in items]\n\n"
                "def by_except():\n    try:\n        pass\n    except Exception as helper:\n        helper()\n\n"
                "def plain():\n    execute()\n    check()\n"
            ),
            "b.py": (
                "from a import send\n\n"
                "def rebinds(conn):\n    send = conn.send\n    send()\n\n"
                "def by_walrus(conn):\n    if (send := conn.send):\n        send()\n"
            ),
        })
        syms = graph["symbols"]
        self.assertEqual(syms["a.py::execute"]["callers"], ["a.py::plain"])
        self.assertEqual(syms["a.py::check"]["callers"], ["a.py::plain"])
        self.assertEqual(syms["a.py::helper"]["callers"], [])
        self.assertEqual(syms["a.py::send"]["callers"], [])
        self.assertEqual(syms["a.py::by_param"]["unresolved_callees"], ["execute"])
        self.assertIn("send", syms["b.py::rebinds"]["unresolved_callees"])
        self.assertIn("send", syms["b.py::by_walrus"]["unresolved_callees"])
        self.assertIn("helper", syms["a.py::by_except"]["unresolved_callees"])

    def test_regex_fallback_calls_are_unknown(self):
        code = b"function a() { return 1; }\nfunction run(a) { return a(); }\n"
        manifest = {"files": [{
            "path": "web/u.js",
            "language": "javascript",
            "parser": "regex_fallback",
            "symbols": dict(extract_regex_symbols(code, "javascript"), parser="regex_fallback"),
        }]}
        graph = build_symbol_graph(manifest)
        syms = graph["symbols"]
        self.assertEqual(syms["web/u.js::a"]["callers"], [])
        self.assertEqual(syms["web/u.js::run"]["resolved_callees"], [])
        self.assertIn("a", syms["web/u.js::run"]["unresolved_callees"])
        self.assertEqual(graph["call_resolution"]["resolved"], 0)

    def test_typescript_parameters_and_locals_shadow_imports(self):
        if not get_parser_for_language("typescript"):
            self.skipTest("tree-sitter-typescript not available")
        graph = self.build({
            "web/x.ts": "export function a(): number { return 1; }\n",
            "web/shadow.ts": (
                'import { a } from "./x";\n'
                "export function helperLocal(): number { return 0; }\n"
                "export function viaParam(a: () => number, { helperLocal }: { helperLocal: () => number }): void {\n"
                "  a();\n"
                "  helperLocal();\n"
                "}\n"
                "export function viaLoop(fs: Array<() => number>): void {\n"
                "  for (const a of fs) { a(); }\n"
                "}\n"
                "export const viaArrow = (helperLocal: () => number): number => helperLocal();\n"
                "export function direct(): void {\n"
                "  a();\n"
                "  helperLocal();\n"
                "}\n"
            ),
        })
        syms = graph["symbols"]
        self.assertEqual(syms["web/x.ts::a"]["callers"], ["web/shadow.ts::direct"])
        self.assertEqual(syms["web/shadow.ts::helperLocal"]["callers"], ["web/shadow.ts::direct"])
        self.assertEqual(sorted(syms["web/shadow.ts::viaParam"]["unresolved_callees"]), ["a", "helperLocal"])

    def test_typescript_import_bindings(self):
        if not get_parser_for_language("typescript"):
            self.skipTest("tree-sitter-typescript not available")
        code = (b'import Def, { a as b, c } from "./x";\n'
                b'import * as ns from "./y";\n')
        imports = extract_tree_sitter_symbols(get_parser_for_language("typescript"), code, "typescript",
                                              Path("m.ts"))["imports"]
        self.assertEqual(imports[0]["names"], [
            {"name": "default", "alias": "Def"},
            {"name": "a", "alias": "b"},
            {"name": "c", "alias": None},
        ])
        self.assertEqual(imports[1]["names"], [{"name": "*", "alias": "ns"}])

        graph = self.build({
            "web/x.ts": "export function a(): number { return 1; }\n",
            "web/y.ts": "export function helper(): number { return 2; }\n",
            "web/z.ts": "export function a(): number { return 3; }\n",
            "web/main.ts": (
                'import { a as b } from "./x";\n'
                'import * as ns from "./y";\n'
                "export function run(other: any): void {\n"
                "  b();\n"
                "  ns.helper();\n"
                "  other.thing();\n"
                "}\n"
            ),
        })
        syms = graph["symbols"]
        self.assertEqual(syms["web/x.ts::a"]["callers"], ["web/main.ts::run"])
        self.assertEqual(syms["web/y.ts::helper"]["callers"], ["web/main.ts::run"])
        self.assertEqual(syms["web/z.ts::a"]["callers"], [])
        self.assertEqual(syms["web/main.ts::run"]["unresolved_callees"], ["other.thing"])


class TestDiscovery(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ccg_disc_")).resolve()
        write(self.root, ".gitignore", "data/\n")
        write(self.root, "src/app.py", "def main():\n    return 0\n")
        write(self.root, "frontend/src/main.ts", "export const x = 1;\n")
        write(self.root, "data/fixture.py", "X = 1\n")
        # Generated output: dist.*/dist-*/dist_* and build.* directories, bundles, minified JS
        write(self.root, "frontend/dist.old/app.js", "var a=1;\n")
        write(self.root, "build.out/gen.py", "X = 1\n")
        write(self.root, "frontend/src/vendor.min.js", "var b=2;\n")
        write(self.root, "frontend/src/bundle.js", "var c=" + "1+" * 3000 + "1;\n")
        # Real sources that must stay: build tooling dirs, long-line Python, a `secrets` package
        write(self.root, "build_tools/gen.py", "X = 1\n")
        write(self.root, "build-tools/make.py", "X = 1\n")
        write(self.root, "src/table.py", "TABLE = [" + "1, " * 3000 + "]\n")
        write(self.root, "src/vault/secrets/__init__.py", "")
        write(self.root, "src/vault/secrets/store.py", "def get():\n    return 1\n")
        write(self.root, "secrets/keys.py", "KEY = 'x'\n")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    @contextlib.contextmanager
    def isolated_git(self):
        """Git runs without the user's global or system configuration."""
        with mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}):
            for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
                os.environ.pop(key, None)
            yield

    def git(self, *args):
        subprocess.run(["git", *args], check=True, capture_output=True)

    def test_git_discovery_respects_gitignore_and_skips_generated(self):
        if not shutil.which("git"):
            self.skipTest("git not available")
        with self.isolated_git():
            self.git("init", "-q", str(self.root))
            write(self.root, "nested_repo/lib.py", "X = 1\n")
            self.git("init", "-q", str(self.root / "nested_repo"))
            manifest = scan_codebase(self.root)
        self.assertEqual([f["path"] for f in manifest["files"]], [
            "build-tools/make.py",
            "build_tools/gen.py",
            "frontend/src/main.ts",
            "src/app.py",
            "src/table.py",
            "src/vault/secrets/__init__.py",
            "src/vault/secrets/store.py",
        ])
        self.assertEqual(manifest["discovery"]["method"], "git")
        self.assertEqual(manifest["discovery"]["excluded"], {
            "build_artifact_dir": 2,
            "minified_content": 1,
            "minified_name": 1,
            "nested_repository": 1,
            "secrets_dir": 1,
        })
        self.assertEqual(manifest["discovery"]["skipped_minified"], ["frontend/src/bundle.js"])

    def test_valid_empty_git_listing_scans_nothing(self):
        if not shutil.which("git"):
            self.skipTest("git not available")
        with self.isolated_git():
            self.git("init", "-q", str(self.root))
            (self.root / ".git" / "info").mkdir(parents=True, exist_ok=True)
            (self.root / ".git" / "info" / "exclude").write_text("*\n", encoding="utf-8")
            manifest = scan_codebase(self.root)
        self.assertEqual(manifest["discovery"]["method"], "git")
        self.assertEqual(manifest["files"], [])

    def test_walk_fallback_without_git(self):
        with self.isolated_git():
            probe = subprocess.run(["git", "-C", str(self.root), "rev-parse", "--is-inside-work-tree"],
                                   capture_output=True, text=True) if shutil.which("git") else None
            if probe is not None and probe.returncode == 0:
                self.skipTest("temporary directory is inside a Git work tree")
            manifest = scan_codebase(self.root)
        self.assertEqual(manifest["discovery"]["method"], "walk")
        # Without Git, .gitignore is not applied; every other rule still holds
        self.assertEqual([f["path"] for f in manifest["files"]], [
            "build-tools/make.py",
            "build_tools/gen.py",
            "data/fixture.py",
            "frontend/src/main.ts",
            "src/app.py",
            "src/table.py",
            "src/vault/secrets/__init__.py",
            "src/vault/secrets/store.py",
        ])
        stats = {}
        files = discover_source_files(self.root, use_git=False, stats=stats)
        self.assertEqual(stats["method"], "walk")
        self.assertNotIn(self.root / "frontend/src/vendor.min.js", files)


if __name__ == "__main__":
    unittest.main()
