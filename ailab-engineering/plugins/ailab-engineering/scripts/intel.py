#!/usr/bin/env python3
"""ailab-engineering code and change intelligence (read-only, advisory).

Run through scripts/intel.sh, which picks a Python 3.9+ interpreter and runs
this file in isolated mode (-I): no repository code is ever imported; Python
files are parsed with `ast`, JS/TS files with regular expressions.

Subcommands
  impact      dependents and tests reached by a change (static imports)
  history     commits, churn, rework signals and co-changes for a scope
  signals     diff heuristics: size, suppressions, edited assertions, ...
  arch-check  boundary rules from the ailab-boundaries block in ARCHITECTURE.md
  fbpa        fail-before/pass-after check of a bug-fix test, in copies inside a sandbox
  sandbox-check  prove that the fbpa sandbox isolates tests on this machine
  metrics     review rounds, finding categories and effectiveness lines of WPs

Nothing is written into the repository. `fbpa` writes only inside --workdir and runs
the test command only inside a bubblewrap sandbox (no network, no host secrets, no
repository); without a working sandbox it reports BLOCKED and runs nothing.
Every report states what it could not see; results are a lower bound.

Exit codes: 0 ok (advisory commands always 0), 1 arch-check violations or
fbpa FAIL, 2 usage or configuration error, 3 fbpa INCONCLUSIVE, 4 fbpa BLOCKED
or INVALID.
"""

import argparse
import ast
import glob
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import threading
import time
from collections import defaultdict, deque

VERSION = "1.0"

PY_EXT = (".py", ".pyi")
JS_EXT = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")
JS_PROBE = (".ts", ".tsx", ".mts", ".cts", ".d.ts", ".js", ".jsx", ".mjs", ".cjs")
CODE_EXT_OTHER = (".go", ".rs", ".java", ".kt", ".rb", ".php", ".cs", ".c", ".cc", ".cpp", ".h",
                  ".hpp", ".swift", ".scala", ".vue", ".svelte", ".sh", ".sql")

LIMITS = ("static imports only. Not seen: dependency injection, string-based routing or registries, "
          "framework autodiscovery (pytest plugins, Django apps, Celery tasks, entry points), "
          "sys.path or bundler tricks, and languages that are not analyzed. Treat the result as a "
          "lower bound on impact, never as proof that something is unaffected.")


class UsageError(Exception):
    pass


# --------------------------------------------------------------------------- git

def git(repo, *args, check=True, binary=False, timeout=60):
    cmd = ["git", "-c", "safe.directory=*", "-c", "core.quotepath=off", "--no-optional-locks", "-C", repo]
    cmd += list(args)
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if check and p.returncode != 0:
        raise UsageError("git %s failed: %s" % (" ".join(args[:3]), p.stderr.decode("utf-8", "replace").strip()[:300]))
    return p.stdout if binary else p.stdout.decode("utf-8", "replace")


def repo_root(start):
    p = subprocess.run(["git", "-c", "safe.directory=*", "-C", start, "rev-parse", "--show-toplevel"],
                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    if p.returncode != 0:
        raise UsageError("not inside a git repository: %s" % start)
    return p.stdout.decode().strip()


def resolve_ref(repo, ref):
    out = git(repo, "rev-parse", "-q", "--verify", ref + "^{commit}", check=False).strip()
    if not out:
        raise UsageError("unknown git ref '%s'" % ref)
    return out


def tree_files(repo):
    """Tracked plus untracked-not-ignored files that exist in the work tree."""
    out = git(repo, "ls-files", "-co", "--exclude-standard", "-z")
    files = []
    for f in out.split("\0"):
        if f and os.path.isfile(os.path.join(repo, f)):
            files.append(f)
    return sorted(set(files))


def changed_files(repo, base=None):
    """(changed, deleted): paths changed in the work tree against base (default HEAD)."""
    ref = base or "HEAD"
    has_head = bool(git(repo, "rev-parse", "-q", "--verify", "HEAD", check=False).strip())
    changed, deleted = set(), set()
    if has_head or base:
        out = git(repo, "diff", "--no-ext-diff", "--name-status", "--no-renames", "-z", ref)
        parts = [p for p in out.split("\0") if p != ""]
        for i in range(0, len(parts) - 1, 2):
            st, path = parts[i], parts[i + 1]
            (deleted if st.startswith("D") else changed).add(path)
    else:
        changed.update(git(repo, "ls-files", "-z").split("\0"))
    for f in git(repo, "ls-files", "-o", "--exclude-standard", "-z").split("\0"):
        if f:
            changed.add(f)
    changed.discard("")
    return sorted(changed), sorted(deleted)


def is_shallow(repo):
    return git(repo, "rev-parse", "--is-shallow-repository", check=False).strip() == "true"


# --------------------------------------------------------------------------- helpers

def read_text(path, limit=2_000_000):
    try:
        with open(path, "rb") as fh:
            data = fh.read(limit + 1)
    except OSError:
        return None
    if len(data) > limit or b"\0" in data[:8192]:
        return None
    return data.decode("utf-8", "replace")


def glob_to_regex(pattern):
    """Glob with ** (any directories), * (within a segment) and ? -> anchored regex."""
    pat = pattern.strip()
    if pat.startswith("./"):
        pat = pat[2:]
    i, out = 0, []
    while i < len(pat):
        c = pat[i]
        if pat.startswith("**/", i):
            out.append("(?:.*/)?"); i += 3
        elif pat.startswith("**", i):
            out.append(".*"); i += 2
        elif c == "*":
            out.append("[^/]*"); i += 1
        elif c == "?":
            out.append("[^/]"); i += 1
        else:
            out.append(re.escape(c)); i += 1
    rx = "".join(out)
    if pat.endswith("/"):
        rx += ".*"
    return re.compile("^" + rx + "$")


def is_test_path(path):
    base = os.path.basename(path)
    if re.search(r"(^|/)(tests?|__tests__|spec|specs)/", path):
        return True
    if re.match(r"^(test_.*\.py|.*_test\.py|conftest\.py)$", base):
        return True
    return bool(re.search(r"\.(test|spec)\.(ts|tsx|js|jsx|mjs|cjs|mts|cts)$", base))


def lang_of(path):
    if path.endswith(PY_EXT):
        return "python"
    if path.endswith(JS_EXT) and not path.endswith(".d.ts"):
        return "js"
    if path.endswith(".d.ts"):
        return "js"
    return None


def limited(items, n=12):
    items = list(items)
    if len(items) <= n:
        return ", ".join(items)
    return ", ".join(items[:n]) + ", +%d more" % (len(items) - n)


def stdlib_names():
    names = set(getattr(sys, "stdlib_module_names", ()))
    names.update(sys.builtin_module_names)
    if not getattr(sys, "stdlib_module_names", None):   # Python 3.9
        import sysconfig
        std = sysconfig.get_paths().get("stdlib", "")
        try:
            for entry in os.listdir(std):
                names.add(entry[:-3] if entry.endswith(".py") else entry)
        except OSError:
            pass
    names.add("__future__")
    return names


def iter_statements(tree):
    """Every statement node, including nested blocks; imports are always statements,
    so expressions need not be visited (a full ast.walk is ~10x slower)."""
    stack = list(getattr(tree, "body", ()))
    while stack:
        node = stack.pop()
        yield node
        for field in ("body", "orelse", "finalbody"):
            stack.extend(x for x in getattr(node, field, ()) if isinstance(x, ast.stmt))
        for h in getattr(node, "handlers", ()):
            stack.extend(h.body)
        for c in getattr(node, "cases", ()):
            stack.extend(c.body)


# --------------------------------------------------------------------------- JSONC (tsconfig)

def strip_jsonc(text):
    """Remove // and /* */ comments and trailing commas outside strings."""
    out, i, n, in_str = [], 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1]); i += 2; continue
            if c == '"':
                in_str = False
            i += 1
        elif c == '"':
            in_str = True; out.append(c); i += 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
        else:
            out.append(c); i += 1
    s = "".join(out)
    # trailing commas (outside strings: strings no longer contain comments, so a
    # second string-aware pass is enough)
    res, in_str, i = [], False, 0
    while i < len(s):
        c = s[i]
        if in_str:
            res.append(c)
            if c == "\\" and i + 1 < len(s):
                res.append(s[i + 1]); i += 2; continue
            if c == '"':
                in_str = False
            i += 1; continue
        if c == '"':
            in_str = True; res.append(c); i += 1; continue
        if c == ",":
            j = i + 1
            while j < len(s) and s[j] in " \t\r\n":
                j += 1
            if j < len(s) and s[j] in "}]":
                i += 1; continue
        res.append(c); i += 1
    return "".join(res)


# --------------------------------------------------------------------------- import graph

class Graph:
    """File-level import graph of one repository (current work tree)."""

    def __init__(self, repo, files, extra_known=()):
        self.repo = repo
        self.skipped = [f for f in files if lang_of(f) and ("node_modules/" in f or f.endswith((".min.js", ".bundle.js")))]
        skip = set(self.skipped)
        self.files = [f for f in files if lang_of(f) and f not in skip]
        self.known = set(files) | set(extra_known)      # extra: deleted paths, so importers of them are found
        self.edges = defaultdict(set)                    # importer -> {target}
        self.edge_line = {}                              # (importer, target) -> line
        self.edge_kind = {}                              # (importer, target) -> static | dynamic | implicit
        self.rev = defaultdict(set)
        self.stats = defaultdict(int)
        self.unresolved = []                             # (file, line, spec)
        self.parse_errors = []
        self.dynamic = []                                # (file, line, kind)
        self.not_analyzed = defaultdict(int)
        for f in files:
            if not lang_of(f):
                ext = os.path.splitext(f)[1].lower()
                if ext in CODE_EXT_OTHER:
                    self.not_analyzed[ext] += 1
        self._stdlib = stdlib_names()
        self._py_roots = self._python_roots()
        self._py_top = self._python_top_names()
        self._ts_cfg_cache = {}
        self._pkg_names = self._workspace_packages()
        for f in self.files:
            if lang_of(f) == "python":
                self.stats["files_python"] += 1
                self._scan_python(f)
            else:
                self.stats["files_js"] += 1
                self._scan_js(f)
        self._pytest_conftest_edges()
        for a, ts in self.edges.items():
            for b in ts:
                self.rev[b].add(a)

    # ---- edges
    def _add(self, src, dst, line, kind):
        if dst == src:
            return
        self.edges[src].add(dst)
        self.edge_line.setdefault((src, dst), line)
        rank = {"implicit": 0, "dynamic": 1, "static": 2}
        if rank[kind] > rank.get(self.edge_kind.get((src, dst)), -1):
            self.edge_kind[(src, dst)] = kind
            if kind != "implicit":
                self.edge_line[(src, dst)] = line
        if kind == "static":
            self.stats["imports_internal"] += 1
        elif kind == "dynamic":
            self.stats["imports_internal"] += 1
            self.stats["imports_dynamic_literal"] += 1

    # ---- python
    def _python_roots(self):
        roots = {""}
        for d in ("src", "lib"):
            if os.path.isdir(os.path.join(self.repo, d)):
                roots.add(d)
        for f in self.known:
            if f.endswith(("pyproject.toml", "setup.py", "setup.cfg")):
                d = os.path.dirname(f)
                roots.add(d)
                if os.path.isdir(os.path.join(self.repo, d, "src")):
                    roots.add((d + "/src") if d else "src")
            if f.endswith("/__init__.py") or f == "__init__.py":
                d = os.path.dirname(f)
                while d and (d + "/__init__.py") in self.known:
                    d = os.path.dirname(d)
                roots.add(d)
        return sorted(roots, key=lambda r: (r.count("/"), r))

    def _python_top_names(self):
        tops = set()
        for f in self.known:
            if not f.endswith(PY_EXT):
                continue
            for r in self._py_roots:
                pre = (r + "/") if r else ""
                if f.startswith(pre):
                    rest = f[len(pre):]
                    first = rest.split("/")[0]
                    tops.add(os.path.splitext(first)[0] if "/" not in rest else first)
        return tops

    def _py_module_files(self, dotted):
        """All files that module `dotted` resolves to (one per matching root)."""
        parts = dotted.split(".")
        res = []
        for r in self._py_roots:
            base = "/".join(([r] if r else []) + parts)
            for cand in (base + ".py", base + "/__init__.py", base + ".pyi", base + "/__init__.pyi"):
                if cand in self.known:
                    res.append(cand)
                    break
        return res

    def _py_parents(self, target):
        """Package __init__ files executed when importing the module at `target`."""
        out = []
        d = os.path.dirname(target)
        if target.endswith("__init__.py"):
            d = os.path.dirname(d)
        while d:
            init = d + "/__init__.py"
            if init in self.known:
                out.append(init)
            else:
                break
            d = os.path.dirname(d)
        return out

    def _py_resolve(self, f, line, dotted, names=None, level=0):
        if level:
            pkg = os.path.dirname(f)
            for _ in range(level - 1):
                pkg = os.path.dirname(pkg)
            base_parts = [p for p in pkg.split("/") if p]
            mod_parts = dotted.split(".") if dotted else []
            # relative imports resolve inside the file's own package tree, not via roots
            base = "/".join(base_parts + mod_parts)
            targets = []
            for cand in ((base + ".py") if mod_parts else None, (base + "/__init__.py") if base else "__init__.py"):
                if cand and cand in self.known:
                    targets.append(cand)
                    break
            sub_hits = []
            for nm in names or ():
                sb = "/".join([p for p in (base,) if p] + [nm])
                for cand in (sb + ".py", sb + "/__init__.py"):
                    if cand in self.known:
                        sub_hits.append(cand)
                        break
            if not targets and not sub_hits:
                self.stats["imports_unresolved"] += 1
                self.unresolved.append((f, line, "." * level + (dotted or "")))
                return
            for t in targets + sub_hits:
                self._add(f, t, line, "static")
                for p in self._py_parents(t):
                    self._add(f, p, line, "implicit")
            return
        top = dotted.split(".")[0]
        hits = self._py_module_files(dotted)
        sub_hits = []
        for nm in names or ():
            sub_hits += self._py_module_files(dotted + "." + nm)
        fdir = os.path.dirname(f)
        if not hits and not sub_hits and dotted and ((fdir + "/__init__.py") if fdir else "__init__.py") not in self.known:
            # script directory / pytest "prepend" import mode: a module next to the importing
            # file, only when that directory is not a package (Python 3 has no implicit
            # relative imports inside packages)
            sib = "/".join([p for p in (fdir,) if p] + dotted.split("."))
            for cand in (sib + ".py", sib + "/__init__.py"):
                if cand in self.known:
                    hits = [cand]
                    self.stats["imports_sibling"] += 1
                    break
        if hits or sub_hits:
            if len(set(hits)) > 1:
                self.stats["imports_ambiguous"] += 1
            # "from pkg import sub1, sub2" where every name is a submodule: the package
            # itself is only executed (implicit), the submodules are the dependency.
            pkg_kind = "implicit" if names and len(sub_hits) >= len(names) else "static"
            for t in set(hits):
                self._add(f, t, line, pkg_kind)
            for t in set(sub_hits):
                self._add(f, t, line, "static")
            for t in set(hits + sub_hits):
                for p in self._py_parents(t):
                    self._add(f, p, line, "implicit")
            return
        if top in self._stdlib:
            self.stats["imports_stdlib"] += 1
        elif top in self._py_top:
            # an internal package whose submodule or attribute is not a file here
            self.stats["imports_unresolved"] += 1
            self.unresolved.append((f, line, dotted))
        else:
            self.stats["imports_external"] += 1

    def _scan_python(self, f):
        text = read_text(os.path.join(self.repo, f))
        if text is None:
            self.parse_errors.append((f, "unreadable or binary"))
            return
        try:
            tree = ast.parse(text, filename=f)
        except (SyntaxError, ValueError) as e:
            self.parse_errors.append((f, "SyntaxError line %s" % getattr(e, "lineno", "?")))
            return
        dyn_possible = "import_module" in text or "__import__" in text
        for node in (ast.walk(tree) if dyn_possible else iter_statements(tree)):
            if isinstance(node, ast.Import):
                for a in node.names:
                    self._py_resolve(f, node.lineno, a.name)
            elif isinstance(node, ast.ImportFrom):
                names = [a.name for a in node.names if a.name != "*"]
                self._py_resolve(f, node.lineno, node.module or "", names, node.level or 0)
            elif isinstance(node, ast.Call):
                fn = node.func
                name = fn.attr if isinstance(fn, ast.Attribute) else (fn.id if isinstance(fn, ast.Name) else "")
                if name in ("import_module", "__import__") and node.args:
                    arg = node.args[0]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and not arg.value.startswith("."):
                        self._py_resolve(f, node.lineno, arg.value)
                        self.dynamic.append((f, node.lineno, "literal"))
                        self.stats["imports_dynamic_literal"] += 1
                    else:
                        self.stats["imports_dynamic_unresolved"] += 1
                        self.dynamic.append((f, node.lineno, "non-literal"))

    def _pytest_conftest_edges(self):
        confs = [f for f in self.known if os.path.basename(f) == "conftest.py"]
        if not confs:
            return
        for f in self.files:
            if not f.endswith(".py") or not is_test_path(f) or os.path.basename(f) == "conftest.py":
                continue
            for c in confs:
                d = os.path.dirname(c)
                if not d or f.startswith(d + "/"):
                    self._add(f, c, 0, "implicit")

    # ---- js / ts
    RX_IMPORT = re.compile(r"""(?m)^\s*import\s+(?:type\s+)?(?:[^'";]*?\s+from\s+)?['"]([^'"\n]+)['"]""")
    RX_EXPORT = re.compile(r"""(?m)^\s*export\s+(?:type\s+)?(?:\*(?:\s+as\s+\w+)?|\{[^}]*\})\s+from\s+['"]([^'"\n]+)['"]""")
    RX_REQUIRE = re.compile(r"""\brequire\(\s*(['"]?)([^'")\n]*)\1\s*\)""")
    RX_DYN = re.compile(r"""\bimport\(\s*(['"`]?)([^'"`)\n]*)\1\s*[,)]""")

    def _scan_js(self, f):
        text = read_text(os.path.join(self.repo, f))
        if text is None:
            self.parse_errors.append((f, "unreadable or binary"))
            return
        text = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
        text = re.sub(r"(?m)^\s*//.*$", "", text)

        def lineno(pos):
            return text.count("\n", 0, pos) + 1

        for rx in (self.RX_IMPORT, self.RX_EXPORT):
            for m in rx.finditer(text):
                self._js_resolve(f, lineno(m.start(1)), m.group(1))
        for rx in (self.RX_REQUIRE, self.RX_DYN):
            for m in rx.finditer(text):
                q, spec = m.group(1), m.group(2)
                if q in ("'", '"') and spec:
                    self._js_resolve(f, lineno(m.start(2)), spec, dynamic=(rx is self.RX_DYN))
                else:
                    self.stats["imports_dynamic_unresolved"] += 1
                    self.dynamic.append((f, lineno(m.start()), "non-literal"))

    def _js_probe(self, base):
        base = os.path.normpath(base).replace("\\", "/")
        if base.startswith("../") or base == "..":
            return None
        if base == ".":
            base = ""
        cands = [base] if base else []
        stem, ext = os.path.splitext(base)
        if ext in (".js", ".mjs", ".cjs", ".jsx"):
            cands += [stem + e for e in (".ts", ".tsx", ".mts", ".cts")]
        cands += [base + e for e in JS_PROBE]
        cands += [(base + "/index" if base else "index") + e for e in JS_PROBE]
        for c in cands:
            if c in self.known:
                return c
        return None

    def _tsconfig_for(self, f):
        d = os.path.dirname(f)
        while True:
            for name in ("tsconfig.json", "jsconfig.json"):
                p = (d + "/" + name) if d else name
                if p in self.known:
                    return self._load_tsconfig(p)
            if not d:
                return None
            d = os.path.dirname(d)

    def _load_tsconfig(self, p, depth=0):
        if p in self._ts_cfg_cache:
            return self._ts_cfg_cache[p]
        self._ts_cfg_cache[p] = None
        text = read_text(os.path.join(self.repo, p))
        try:
            data = json.loads(strip_jsonc(text or "{}"))
        except ValueError:
            self.parse_errors.append((p, "tsconfig is not valid JSON(C)"))
            return None
        cfg = {"dir": os.path.dirname(p), "baseUrl": None, "paths": {}}
        ext = data.get("extends") if isinstance(data, dict) else None
        if isinstance(ext, str) and ext.startswith(".") and depth < 5:
            ep = os.path.normpath(os.path.join(os.path.dirname(p), ext)).replace("\\", "/")
            if not ep.endswith(".json"):
                ep += ".json"
            parent = self._load_tsconfig(ep, depth + 1) if ep in self.known else None
            if parent:
                cfg.update({k: parent[k] for k in ("baseUrl", "paths")})
                cfg["paths_dir"] = parent.get("paths_dir", parent["dir"])
        co = (data or {}).get("compilerOptions") or {}
        if isinstance(co.get("baseUrl"), str):
            cfg["baseUrl"] = os.path.normpath(os.path.join(cfg["dir"], co["baseUrl"])).replace("\\", "/")
            if cfg["baseUrl"] == ".":
                cfg["baseUrl"] = ""
        if isinstance(co.get("paths"), dict):
            cfg["paths"] = co["paths"]
            cfg["paths_dir"] = cfg["dir"]
        self._ts_cfg_cache[p] = cfg
        return cfg

    def _workspace_packages(self):
        pk = {}
        for f in self.known:
            if os.path.basename(f) == "package.json" and "/" in f and "node_modules/" not in f:
                try:
                    data = json.loads(read_text(os.path.join(self.repo, f)) or "{}")
                except ValueError:
                    continue
                if isinstance(data, dict) and isinstance(data.get("name"), str):
                    pk[data["name"]] = (os.path.dirname(f), data)
        return pk

    def _js_resolve(self, f, line, spec, dynamic=False):
        target = None
        if spec.startswith("./") or spec.startswith("../") or spec in (".", ".."):
            target = self._js_probe(os.path.join(os.path.dirname(f), spec))
            if target is None:
                self.stats["imports_unresolved"] += 1
                self.unresolved.append((f, line, spec))
                return
        else:
            cfg = self._tsconfig_for(f)
            if cfg and cfg.get("paths"):
                pdir = cfg.get("paths_dir", cfg["dir"])
                base = cfg["baseUrl"] if cfg["baseUrl"] is not None else pdir
                for pat, subs in cfg["paths"].items():
                    if not isinstance(subs, list):
                        continue
                    if "*" in pat:
                        pre, post = pat.split("*", 1)
                        if spec.startswith(pre) and spec.endswith(post) and len(spec) >= len(pre) + len(post):
                            star = spec[len(pre):len(spec) - len(post)] if post else spec[len(pre):]
                            for s in subs:
                                t = self._js_probe(os.path.join(base, s.replace("*", star, 1)))
                                if t:
                                    target = t; break
                    elif spec == pat:
                        for s in subs:
                            t = self._js_probe(os.path.join(base, s))
                            if t:
                                target = t; break
                    if target:
                        break
            if target is None and cfg and cfg.get("baseUrl") is not None:
                target = self._js_probe(os.path.join(cfg["baseUrl"], spec))
            if target is None:
                parts = spec.split("/")
                name = "/".join(parts[:2]) if spec.startswith("@") else parts[0]
                if name in self._pkg_names:
                    pdir, data = self._pkg_names[name]
                    sub = spec[len(name):].lstrip("/")
                    if sub:
                        target = self._js_probe(os.path.join(pdir, sub)) or self._js_probe(os.path.join(pdir, "src", sub))
                    else:
                        for entry in (data.get("source"), data.get("types"), data.get("main"), "src/index", "index"):
                            if isinstance(entry, str):
                                target = self._js_probe(os.path.join(pdir, entry))
                                if target:
                                    break
            if target is None:
                if spec.startswith("node:"):
                    self.stats["imports_stdlib"] += 1
                else:
                    self.stats["imports_external"] += 1
                return
        self._add(f, target, line, "dynamic" if dynamic else "static")

    # ---- queries
    def dependents(self, targets, depth):
        """{file: distance} of files that import targets, up to depth (BFS on reverse edges)."""
        seen = {}
        q = deque((t, 0) for t in targets)
        start = set(targets)
        while q:
            node, d = q.popleft()
            if d >= depth:
                continue
            for imp in self.rev.get(node, ()):
                if imp in start or (imp in seen and seen[imp] <= d + 1):
                    continue
                seen[imp] = d + 1
                q.append((imp, d + 1))
        return seen

    def coverage(self):
        s = self.stats
        return {
            "files_python": s["files_python"], "files_js_ts": s["files_js"],
            "files_not_analyzed": dict(sorted(self.not_analyzed.items())),
            "files_skipped_vendored": len(self.skipped),
            "parse_errors": len(self.parse_errors),
            "imports_internal": s["imports_internal"], "imports_external": s["imports_external"],
            "imports_stdlib": s["imports_stdlib"], "imports_unresolved": s["imports_unresolved"],
            "imports_dynamic_literal": s["imports_dynamic_literal"],
            "imports_dynamic_unresolved": s["imports_dynamic_unresolved"],
            "imports_ambiguous": s["imports_ambiguous"], "imports_sibling": s["imports_sibling"],
        }


def coverage_lines(g, scope=None):
    c = g.coverage()
    na = ", ".join("%s %d" % kv for kv in c["files_not_analyzed"].items()) or "none"
    lines = [
        "COVERAGE: python %d, js/ts %d files parsed; not analyzed: %s; vendored/minified skipped: %d; parse errors: %d"
        % (c["files_python"], c["files_js_ts"], na, c["files_skipped_vendored"], c["parse_errors"]),
        "IMPORTS: internal %d (sibling-directory %d), external %d, stdlib %d, unresolved %d, dynamic literal %d, dynamic non-literal %d"
        % (c["imports_internal"], c["imports_sibling"], c["imports_external"], c["imports_stdlib"], c["imports_unresolved"],
           c["imports_dynamic_literal"], c["imports_dynamic_unresolved"]),
    ]
    unresolved = [u for u in g.unresolved if scope is None or u[0] in scope]
    if unresolved:
        lines.append("UNRESOLVED%s: %s" % (" (in scope)" if scope is not None else "",
                                          limited(["%s:%s %s" % u for u in unresolved], 6)))
    dyn = [d for d in g.dynamic if d[2] == "non-literal" and (scope is None or d[0] in scope)]
    if dyn:
        lines.append("DYNAMIC (not followed): %s" % limited(["%s:%s" % d[:2] for d in dyn], 6))
    if g.parse_errors:
        lines.append("PARSE ERRORS: %s" % limited(["%s (%s)" % e for e in g.parse_errors], 6))
    lines.append("LIMITS: " + LIMITS)
    return lines


# --------------------------------------------------------------------------- impact

def cmd_impact(a):
    repo = repo_root(a.repo)
    base = resolve_ref(repo, a.base) if a.base else None
    files = tree_files(repo)
    not_found = []
    if a.paths:
        changed, deleted = [], []
        head_files = set(git(repo, "ls-tree", "-r", "-z", "--name-only", base or "HEAD", check=False).split("\0"))
        real_repo = os.path.realpath(repo)
        cwd = os.path.realpath(os.getcwd())
        for p in a.paths:
            # relative to the current directory, like any command-line path; symlinks in the
            # directory part are resolved, the file itself is not (it may be a link)
            ap = os.path.normpath(os.path.join(cwd, p))
            if not (ap == real_repo or ap.startswith(real_repo + os.sep)):
                ap = os.path.join(os.path.realpath(os.path.dirname(ap)), os.path.basename(ap))
            rp = os.path.relpath(ap, real_repo).replace(os.sep, "/")
            if rp == ".." or rp.startswith("../"):
                raise UsageError("path outside the repository: %s" % p)
            if any(c in p for c in "*?["):
                rx = glob_to_regex(rp)
                hit = [f for f in files if rx.match(f)]
                changed += hit
                if not hit:
                    not_found.append(p)
            elif rp == "." or os.path.isdir(os.path.join(repo, rp)):
                pre = "" if rp == "." else rp.rstrip("/") + "/"
                changed += [f for f in files if f.startswith(pre)]
            elif rp in files:
                changed.append(rp)
            elif rp in head_files:
                deleted.append(rp)
            else:
                not_found.append(p)
        changed = sorted(set(changed))
        if not changed and not deleted:
            raise UsageError("no file matches %s (not in the work tree or in %s)" % (" ".join(a.paths), base or "HEAD"))
    else:
        changed, deleted = changed_files(repo, base)
    g = Graph(repo, files, extra_known=deleted)
    targets = [c for c in changed if lang_of(c)] + [d for d in deleted if lang_of(d)]
    other = [c for c in changed if not lang_of(c)]
    per = {}
    all_dep = {}
    for t in targets:
        dep = g.dependents([t], a.depth)
        per[t] = dep
        for k, v in dep.items():
            if k not in all_dep or v < all_dep[k]:
                all_dep[k] = v
    closure = g.dependents(targets, max(a.depth, a.test_depth))
    tests = sorted({k: v for k, v in closure.items() if is_test_path(k)}.items(), key=lambda kv: (kv[1], kv[0]))
    tests_changed = sorted(t for t in targets if is_test_path(t))
    deleted_imported = {d: sorted(g.rev.get(d, ())) for d in deleted if g.rev.get(d)}
    hubs = {t: len(g.rev.get(t, ())) for t in targets if len(g.rev.get(t, ())) >= a.hub}
    scope = set(targets) | set(all_dep)

    if a.json:
        print(json.dumps({
            "repo": repo, "base": base or "HEAD (work tree)", "changed": changed, "deleted": deleted,
            "not_analyzed_changed": other,
            "per_file": {t: {"direct": sorted(k for k, v in per[t].items() if v == 1),
                             "indirect": sorted(k for k, v in per[t].items() if v > 1),
                             "dependents_total": len(g.rev.get(t, ()))} for t in targets},
            "tests": [{"path": k, "distance": v} for k, v in tests], "tests_changed": tests_changed,
            "deleted_still_imported": deleted_imported, "hubs": hubs, "not_found": not_found,
            "coverage": g.coverage(), "unresolved_in_scope": [list(u) for u in g.unresolved if u[0] in scope],
            "limits": LIMITS, "version": VERSION}, indent=2))
        return 0
    if a.tests_only:
        for k, _ in tests:
            print(k)
        for t in tests_changed:
            if t not in dict(tests):
                print(t)
        return 0
    print("IMPACT (static imports, advisory) — base %s, depth %d" % (base[:12] if base else "HEAD + work tree", a.depth))
    print("CHANGED: %d files (%d analyzable%s%s)" % (
        len(changed) + len(deleted), len(targets),
        ", deleted %d" % len(deleted) if deleted else "",
        "; not analyzed: %s" % limited(other, 6) if other else ""))
    if not_found:
        print("NOT FOUND (not in the work tree or %s; planned new files have no dependents yet): %s" % (base or "HEAD", limited(not_found, 8)))
    if not targets:
        print("No analyzable changed files.")
    for t in targets:
        dep = per[t]
        direct = sorted(k for k, v in dep.items() if v == 1)
        indirect = sorted(k for k, v in dep.items() if v > 1)
        flag = []
        if t in hubs:
            flag.append("HUB: %d direct dependents" % hubs[t])
        if t in deleted:
            flag.append("DELETED")
        print("- %s%s" % (t, "  [" + "; ".join(flag) + "]" if flag else ""))
        print("    direct (%d): %s" % (len(direct), limited(direct) if direct else "none"))
        if a.depth > 1:
            print("    indirect (%d): %s" % (len(indirect), limited(indirect) if indirect else "none"))
    if deleted_imported:
        for d, imps in deleted_imported.items():
            print("BREAKAGE LIKELY: deleted %s is still imported by %s" % (d, limited(imps, 8)))
    print("TESTS REACHING THE CHANGE (%d, within %d import steps): %s" % (
        len(tests), max(a.depth, a.test_depth), limited(["%s (%d)" % kv for kv in tests], 15) if tests else "none found"))
    if tests_changed:
        print("TESTS CHANGED: %s" % limited(tests_changed, 10))
    for line in coverage_lines(g, scope):
        print(line)
    return 0


# --------------------------------------------------------------------------- history

FIX_RX = re.compile(r"(^|\b)(fix(e[sd])?|hotfix|bugfix|regression|revert(s|ed)?|rollback)\b", re.I)
REVERT_RX = re.compile(r'^Revert "|\brevert(s|ed)?\b', re.I)


def checkpoint_sha(repo):
    for rel in ("docs/project/CURRENT_STATE.md", "CURRENT_STATE.md"):
        text = read_text(os.path.join(repo, rel))
        if text is None:
            continue
        for line in text.splitlines()[:40]:
            m = re.match(r"^\s*[-*]?\s*Checkpoint:\s*([0-9a-f]{7,40})\b", line)
            if m:
                return rel, m.group(1)
        return rel, None
    return None, None


def checkpoint_base(repo, rel_cs, cp):
    """The commit project memory is current with: the checkpoint commit, or the later
    commit that recorded the checkpoint (the state file's last commit), whichever is
    newer. Returns (full sha or None, how)."""
    full = git(repo, "rev-parse", "-q", "--verify", cp + "^{commit}", check=False).strip()
    if not full:
        return None, "unknown"
    last = git(repo, "log", "-1", "--format=%H", "--", rel_cs, check=False).strip()
    if last and last != full and subprocess.run(
            ["git", "-c", "safe.directory=*", "-C", repo, "merge-base", "--is-ancestor", full, last]).returncode == 0:
        return last, "recorded in " + last[:9]
    return full, "checkpoint"


def wp_ids(repo):
    d = os.path.join(repo, ".claude", "wp")
    try:
        return sorted(f[:-3] for f in os.listdir(d) if f.endswith(".md"))
    except OSError:
        return []


def cmd_history(a):
    repo = repo_root(a.repo)
    has_head = bool(git(repo, "rev-parse", "-q", "--verify", "HEAD", check=False).strip())
    if not has_head:
        print("HISTORY: no commits yet")
        return 0
    rel_cs, cp = checkpoint_sha(repo)
    since = a.since
    if since == "checkpoint":
        if not cp:
            raise UsageError("--since checkpoint: no 'Checkpoint: <sha>' line in the top of CURRENT_STATE.md")
        since = checkpoint_base(repo, rel_cs, cp)[0] or cp
    rng = []
    if since:
        rng = [resolve_ref(repo, since) + "..HEAD"]
    paths = list(a.paths)
    if not paths and a.changed:
        ch, de = changed_files(repo)
        paths = ch + de
        if not paths:
            print("HISTORY: no changed files in the work tree; pass paths or --since")
            return 0
    args = ["log", "--no-merges", "--format=%x1e%H%x1f%cs%x1f%s", "--name-only", "--full-diff",
            "-n", str(a.limit)] + rng
    if paths:
        args += ["--"] + paths
    out = git(repo, *args)
    commits = []
    for rec in out.split("\x1e"):
        rec = rec.strip("\n")
        if not rec:
            continue
        head, _, names = rec.partition("\n")
        sha, date, subj = (head.split("\x1f") + ["", ""])[:3]
        commits.append({"sha": sha, "date": date, "subject": subj,
                        "files": [n for n in names.split("\n") if n.strip()]})
    ids = wp_ids(repo)
    id_rx = re.compile(r"(?<![A-Za-z0-9_-])(%s)(?![A-Za-z0-9_-])" % "|".join(re.escape(i) for i in ids)) if ids else None
    churn = defaultdict(int)
    fixes = defaultdict(int)
    cochange = defaultdict(int)
    wp_refs = defaultdict(int)
    scope_set = set(paths)

    def in_scope(fn):
        return not paths or fn in scope_set or any(fn.startswith(p.rstrip("/") + "/") for p in paths)

    for c in commits:
        c["fix"] = bool(FIX_RX.search(c["subject"]))
        c["revert"] = bool(REVERT_RX.search(c["subject"]))
        c["wps"] = sorted(set(id_rx.findall(c["subject"]))) if id_rx else []
        for w in c["wps"]:
            wp_refs[w] += 1
        for fn in c["files"]:
            if in_scope(fn):
                churn[fn] += 1
                if c["fix"] or c["revert"]:
                    fixes[fn] += 1
            elif paths:
                cochange[fn] += 1
    # rework: a file changed by a fix/revert commit that also has an earlier non-fix commit in the window
    rework = []
    for fn, nf in fixes.items():
        if churn[fn] > nf or nf > 1:
            rework.append((fn, nf, churn[fn]))
    rework.sort(key=lambda x: (-x[1], x[0]))
    shallow = is_shallow(repo)
    cp_info = None
    if cp:
        full, how = checkpoint_base(repo, rel_cs, cp)
        if full:
            n = git(repo, "rev-list", "--count", "--no-merges", full + "..HEAD").strip()
            cp_info = {"file": rel_cs, "sha": cp, "commits_since": int(n or 0), "counted_from": how}
        else:
            cp_info = {"file": rel_cs, "sha": cp, "commits_since": None,
                       "problem": "checkpoint commit not in this history (rewritten, other branch or shallow clone)"}
    if a.json:
        print(json.dumps({"repo": repo, "scope": paths or "whole repository", "since": since,
                          "commits": commits, "churn": dict(churn), "rework": rework,
                          "cochange": dict(cochange), "wp_refs": dict(wp_refs), "checkpoint": cp_info,
                          "shallow": shallow, "limit": a.limit, "version": VERSION}, indent=2))
        return 0
    print("HISTORY (git log, advisory) — scope: %s; window: %s, at most %d commits%s" % (
        limited(paths, 6) if paths else "whole repository", ("since " + since) if since else "all history",
        a.limit, "; SHALLOW CLONE: history truncated" if shallow else ""))
    print("COMMITS: %d%s" % (len(commits), " (limit reached; older commits not shown)" if len(commits) >= a.limit else ""))
    for c in commits[:a.show]:
        tags = [t for t, on in (("fix", c["fix"]), ("revert", c["revert"])) if on] + c["wps"]
        print("  %s %s %s%s" % (c["sha"][:9], c["date"], c["subject"][:100], ("  [" + ", ".join(tags) + "]") if tags else ""))
    if len(commits) > a.show:
        print("  ... %d more" % (len(commits) - a.show))
    top = sorted(churn.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
    print("CHURN (commits per file in scope): %s" % (limited(["%s %d" % kv for kv in top], 10) if top else "none"))
    print("REWORK SIGNALS (files touched by fix/revert commits, heuristic by subject): %s" % (
        limited(["%s fixes %d of %d" % r for r in rework], 8) if rework else "none"))
    if paths:
        co = sorted(cochange.items(), key=lambda kv: (-kv[1], kv[0]))[:8]
        print("CO-CHANGED OUTSIDE SCOPE: %s" % (limited(["%s %d" % kv for kv in co], 8) if co else "none"))
    if ids:
        print("WP REFERENCES: %s" % (limited(["%s %d" % kv for kv in sorted(wp_refs.items())], 10) if wp_refs else "none in commit subjects"))
    if cp_info:
        if cp_info["commits_since"] is None:
            print("CHECKPOINT: %s in %s — %s" % (cp, rel_cs, cp_info["problem"]))
        else:
            print("CHECKPOINT: %s in %s — %d commits since" % (cp, rel_cs, cp_info["commits_since"]))
    elif rel_cs:
        print("CHECKPOINT: none (no 'Checkpoint: <sha>' line in the top of %s)" % rel_cs)
    print("LIMITS: fix/revert detection reads commit subjects only; merges are excluded; renames are not followed%s."
          % ("; shallow clone" if shallow else ""))
    return 0


# --------------------------------------------------------------------------- diff parsing and signals

SUPPRESS_RX = re.compile(r"(#\s*noqa|#\s*type:\s*ignore|#\s*pragma:\s*no\s*cover|#\s*pylint:\s*disable|"
                         r"@ts-ignore|@ts-expect-error|@ts-nocheck|eslint-disable|# nosec|//\s*nolint)")
TODO_RX = re.compile(r"\b(TODO|FIXME|XXX|HACK)\b")
SKIP_RX = re.compile(r"(@pytest\.mark\.(skip|xfail)|@unittest\.skip|\bpytest\.skip\(|\b(it|test|describe)\.(skip|only)\(|\bx(it|describe|test)\(|\.todo\()")
ASSERT_RX = re.compile(r"(\bassert\b|\bself\.assert\w*\(|\bexpect\(|\.to(Be|Equal|StrictEqual|Match|Throw|HaveBeenCalled)\w*\(|\bassert_\w+\(|\.should\b)")
PLACEHOLDER_RX = re.compile(r"(raise\s+NotImplementedError|\bnot implemented\b|lorem ipsum|\bplaceholder\b|\bdummy data\b)", re.I)
MANIFESTS = ("requirements.txt", "requirements-dev.txt", "pyproject.toml", "setup.py", "setup.cfg", "Pipfile",
             "poetry.lock", "uv.lock", "package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock",
             "go.mod", "Cargo.toml", "Gemfile", "pom.xml", "build.gradle")


def parse_diff(text):
    """{path: {"added": [(line, text)], "removed": [(oldline, text)], "binary": bool}}"""
    files = {}
    cur = old = None
    old_ln = new_ln = 0
    for raw in text.split("\n"):
        if raw.startswith("diff --git "):
            cur = None
            continue
        if raw.startswith("--- "):
            path = raw[4:]
            old = None if path == "/dev/null" else (path[2:] if path.startswith("a/") else path)
            continue
        if raw.startswith("+++ "):
            path = raw[4:]
            new = None if path == "/dev/null" else (path[2:] if path.startswith("b/") else path)
            key = new or old
            cur = files.setdefault(key, {"added": [], "removed": [], "binary": False}) if key else None
            continue
        if raw.startswith("Binary files"):
            continue
        m = re.match(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", raw)
        if m:
            old_ln, new_ln = int(m.group(1)), int(m.group(2))
            continue
        if cur is None:
            continue
        if raw.startswith("+"):
            cur["added"].append((new_ln, raw[1:])); new_ln += 1
        elif raw.startswith("-"):
            cur["removed"].append((old_ln, raw[1:])); old_ln += 1
        elif raw.startswith(" "):
            old_ln += 1; new_ln += 1
    return files


def diff_against(repo, base):
    ref = base or "HEAD"
    has_head = bool(git(repo, "rev-parse", "-q", "--verify", "HEAD", check=False).strip())
    text = git(repo, "diff", "--no-color", "--no-ext-diff", "--no-textconv", "--src-prefix=a/", "--dst-prefix=b/",
               "--no-renames", "-U0", ref) if (has_head or base) else ""
    files = parse_diff(text)
    for f in git(repo, "ls-files", "-o", "--exclude-standard", "-z").split("\0"):
        if not f:
            continue
        t = read_text(os.path.join(repo, f))
        files[f] = {"added": [(i + 1, l) for i, l in enumerate(t.split("\n"))] if t is not None else [],
                    "removed": [], "binary": t is None, "untracked": True}
    return files


def sensitive_globs(repo):
    """Globs from .claude/rules/*.md whose title says '(T3 by default)'."""
    out = []
    d = os.path.join(repo, ".claude", "rules")
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return out
    for n in names:
        if not n.endswith(".md"):
            continue
        text = read_text(os.path.join(d, n)) or ""
        if "T3 by default" not in text:
            continue
        m = re.match(r"^---\n(.*?)\n---", text, re.S)
        if not m:
            continue
        for line in m.group(1).splitlines():
            mm = re.match(r'^\s*-\s*["\']?([^"\']+)["\']?\s*$', line)
            if mm:
                out.append((n, mm.group(1)))
    return out


def cmd_signals(a):
    repo = repo_root(a.repo)
    base = resolve_ref(repo, a.base) if a.base else None
    files = diff_against(repo, base)
    sig = defaultdict(list)
    add_total = sum(len(v["added"]) for v in files.values())
    rem_total = sum(len(v["removed"]) for v in files.values())
    tests = [f for f in files if is_test_path(f)]
    code = [f for f in files if lang_of(f) and not is_test_path(f)]
    for f, v in sorted(files.items()):
        full = os.path.join(repo, f)
        if os.path.isfile(full) and lang_of(f):
            t = read_text(full)
            if t is not None:
                n = t.count("\n")
                if n > a.large:
                    sig["large_files"].append("%s (%d lines)" % (f, n))
        if os.path.basename(f) in MANIFESTS:
            sig["manifests"].append(f)
        for ln, text in v["added"]:
            if SUPPRESS_RX.search(text):
                sig["suppressions"].append("%s:%d %s" % (f, ln, text.strip()[:80]))
            if TODO_RX.search(text):
                sig["todos"].append("%s:%d %s" % (f, ln, text.strip()[:80]))
            if is_test_path(f) and SKIP_RX.search(text):
                sig["skips"].append("%s:%d %s" % (f, ln, text.strip()[:80]))
            if lang_of(f) and not is_test_path(f) and PLACEHOLDER_RX.search(text):
                sig["placeholders"].append("%s:%d %s" % (f, ln, text.strip()[:80]))
        if is_test_path(f) and not v.get("untracked"):
            for ln, text in v["removed"]:
                if ASSERT_RX.search(text):
                    sig["assertions_removed_or_edited"].append("%s:%d %s" % (f, ln, text.strip()[:80]))
    sens = sensitive_globs(repo)
    for f in files:
        for rule, gl in sens:
            if glob_to_regex(gl).match(f):
                sig["security_sensitive"].append("%s (%s: %s)" % (f, rule, gl))
                break
    deleted_tests = [f for f in tests if not os.path.exists(os.path.join(repo, f))]
    if deleted_tests:
        sig["tests_deleted"] = deleted_tests
    notes = []
    if a.type == "refactor" and sig.get("assertions_removed_or_edited"):
        notes.append("REFACTOR WITH EDITED ASSERTIONS: behaviour pins changed; the characterization protocol requires unchanged assertions or an explicit owner decision.")
    if a.type == "bugfix" and not tests:
        notes.append("BUG FIX WITHOUT TEST CHANGES: the bug-fix protocol expects a reproducing test (or a recorded reason why none is practical).")
    if a.type in ("feature", "bugfix") and code and not tests:
        notes.append("CODE CHANGED, NO TEST CHANGED.")
    if sig.get("security_sensitive"):
        notes.append("SECURITY-SENSITIVE PATHS TOUCHED: run security-reviewer (rules marked T3 by default).")
    if a.json:
        print(json.dumps({"repo": repo, "base": base or "HEAD (work tree)", "files": len(files),
                          "added": add_total, "removed": rem_total, "tests_changed": tests, "code_changed": code,
                          "signals": sig, "notes": notes, "version": VERSION}, indent=2))
        return 0
    print("SIGNALS (diff heuristics, advisory) — base %s%s" % (base[:12] if base else "HEAD + work tree",
                                                              ", type " + a.type if a.type else ""))
    print("SIZE: %d files, +%d/-%d lines; code files %d, test files %d" % (len(files), add_total, rem_total, len(code), len(tests)))
    labels = (("large_files", "LARGE FILES TOUCHED (> %d lines)" % a.large), ("manifests", "DEPENDENCY MANIFESTS CHANGED"),
              ("suppressions", "NEW SUPPRESSIONS"), ("todos", "NEW TODO/FIXME"), ("skips", "NEW SKIPPED/FOCUSED TESTS"),
              ("assertions_removed_or_edited", "ASSERTIONS REMOVED OR EDITED IN TESTS"),
              ("tests_deleted", "TEST FILES DELETED"), ("placeholders", "PLACEHOLDERS IN CODE"),
              ("security_sensitive", "SECURITY-SENSITIVE PATHS"))
    any_sig = False
    for key, label in labels:
        if sig.get(key):
            any_sig = True
            print("%s (%d): %s" % (label, len(sig[key]), limited(sig[key], 6)))
    if not any_sig:
        print("No signals.")
    for n in notes:
        print("NOTE: " + n)
    print("LIMITS: line-pattern heuristics on the diff; a signal is a reason to look, not a defect.")
    return 0


# --------------------------------------------------------------------------- arch-check

def find_arch_doc(repo, doc):
    if doc:
        p = doc if os.path.isabs(doc) else os.path.join(repo, doc)
        if not os.path.isfile(p):
            raise UsageError("architecture document not found: %s" % doc)
        return p
    for rel in ("docs/project/ARCHITECTURE.md", "ARCHITECTURE.md", "docs/ARCHITECTURE.md"):
        p = os.path.join(repo, rel)
        if os.path.isfile(p):
            return p
    return None


def parse_boundaries(text):
    m = re.search(r"^```ailab-boundaries[ \t]*\n(.*?)^```", text, re.S | re.M)
    if not m:
        return None
    start_line = text.count("\n", 0, m.start(1)) + 1
    layers, rules, errors = [], [], []
    for i, raw in enumerate(m.group(1).split("\n")):
        line = raw.split("#", 1)[0].strip()
        ln = start_line + i
        if not line:
            continue
        mm = re.match(r"^layer\s+([A-Za-z0-9_-]+)\s*:\s*(.+)$", line)
        if mm:
            if any(l[0] == mm.group(1) for l in layers):
                errors.append("line %d: layer '%s' defined twice" % (ln, mm.group(1)))
            layers.append((mm.group(1), [g for g in re.split(r"[\s,]+", mm.group(2)) if g]))
            continue
        mm = re.match(r"^(forbid|only)\s+([A-Za-z0-9_-]+)\s*->\s*(.+)$", line)
        if mm:
            rules.append((mm.group(1), mm.group(2), [x for x in re.split(r"[\s,]+", mm.group(3)) if x], ln))
            continue
        errors.append("line %d: cannot parse '%s' (expected 'layer <name>: <glob>...', 'forbid <a> -> <b>[, <c>]' or 'only <a> -> <b>[, <c>]')" % (ln, line))
    names = {l[0] for l in layers}
    for kind, src, dsts, ln in rules:
        for x in [src] + dsts:
            if x not in names:
                errors.append("line %d: unknown layer '%s'" % (ln, x))
    return {"layers": layers, "rules": rules, "errors": errors}


def cmd_arch_check(a):
    repo = repo_root(a.repo)
    docp = find_arch_doc(repo, a.doc)
    if not docp:
        print("ARCH-CHECK: no ARCHITECTURE.md (docs/project/, repository root or docs/); nothing to check")
        return 0
    text = read_text(docp) or ""
    rel_doc = os.path.relpath(docp, repo)
    spec = parse_boundaries(text)
    if spec is None:
        print("ARCH-CHECK: %s has no ```ailab-boundaries block; nothing to check" % rel_doc)
        return 0
    if spec["errors"]:
        for e in spec["errors"]:
            print("CONFIG ERROR: %s %s" % (rel_doc, e))
        return 2
    files = tree_files(repo)
    g = Graph(repo, files)
    rx = [(name, [glob_to_regex(x) for x in globs]) for name, globs in spec["layers"]]

    def layer_of(f):
        for name, rs in rx:
            if any(r.match(f) for r in rs):
                return name
        return None

    layer = {f: layer_of(f) for f in g.files}
    empty = [name for name, _ in rx if not any(v == name for v in layer.values())]
    forbid = defaultdict(set)
    only = {}
    for kind, src, dsts, _ in spec["rules"]:
        if kind == "forbid":
            forbid[src].update(dsts)
        else:
            only.setdefault(src, set()).update(dsts)
    scope = None
    if a.changed:
        ch, _ = changed_files(repo, resolve_ref(repo, a.base) if a.base else None)
        scope = set(ch)
    violations = []
    for src, dsts in g.edges.items():
        if scope is not None and src not in scope:
            continue
        ls = layer.get(src)
        if not ls:
            continue
        for dst in dsts:
            if g.edge_kind.get((src, dst)) == "implicit":
                continue     # package __init__ execution and pytest conftest loading are not design dependencies
            ld = layer.get(dst) or layer_of(dst)
            if not ld or ld == ls:
                continue
            bad = None
            if ld in forbid.get(ls, ()):
                bad = "forbid %s -> %s" % (ls, ld)
            elif ls in only and ld not in only[ls]:
                bad = "only %s -> %s" % (ls, ", ".join(sorted(only[ls])))
            if bad:
                violations.append((src, g.edge_line.get((src, dst), 0), dst, ls, ld, bad))
    violations.sort()
    stale = []
    for m in re.finditer(r"`([^`\s]+)`", text):
        tok = m.group(1).rstrip(".,:;")
        if tok.startswith(("http", "$", "-")) or any(c in tok for c in "*?<>{}()=") or "/" not in tok:
            continue
        if re.match(r"^[A-Za-z0-9_.@-]+(/[A-Za-z0-9_.@-]+)+/?$", tok) and not os.path.exists(os.path.join(repo, tok)):
            stale.append("%s:%d %s" % (rel_doc, text.count("\n", 0, m.start()) + 1, tok))
    layered_unresolved = [u for u in g.unresolved if layer.get(u[0])]
    if a.json:
        print(json.dumps({"doc": rel_doc, "layers": {n: gl for n, gl in spec["layers"]},
                          "violations": [dict(zip(("file", "line", "imports", "from_layer", "to_layer", "rule"), v)) for v in violations],
                          "empty_layers": empty, "stale_references": stale,
                          "unresolved_in_layers": [list(u) for u in layered_unresolved],
                          "scope": "changed files" if scope is not None else "whole repository",
                          "coverage": g.coverage(), "version": VERSION}, indent=2))
    else:
        print("ARCH-CHECK (static imports) — %s; %d layers, %d rules; scope: %s" % (
            rel_doc, len(spec["layers"]), len(spec["rules"]), "changed files" if scope is not None else "whole repository"))
        if violations:
            print("VIOLATIONS (%d):" % len(violations))
            for v in violations[:a.show]:
                print("  %s:%d imports %s  (%s -> %s; rule: %s)" % v)
            if len(violations) > a.show:
                print("  ... %d more" % (len(violations) - a.show))
        else:
            print("VIOLATIONS: none")
        if empty:
            print("WARNING: layers that match no analyzed file (stale globs?): %s" % ", ".join(empty))
        if stale:
            print("WARNING: paths named in %s that do not exist: %s" % (rel_doc, limited(stale, 8)))
        if layered_unresolved:
            print("UNRESOLVED IMPORTS IN LAYERED FILES (may hide violations): %d — %s" % (
                len(layered_unresolved), limited(["%s:%s %s" % u for u in layered_unresolved], 5)))
        print("LIMITS: " + LIMITS)
    if violations:
        return 1
    if a.strict and (empty or stale):
        return 1
    return 0


# --------------------------------------------------------------------------- fbpa

WRONG_REASON = [
    (re.compile(r"\b(ModuleNotFoundError|ImportError)\b"), "import error"),
    (re.compile(r"\bERROR collecting\b|\berror(s)? during collection\b|Interrupted: \d+ errors? during collection", re.I), "collection error"),
    (re.compile(r"\b(SyntaxError|IndentationError|TabError)\b"), "syntax error"),
    (re.compile(r"fixture '[^']+' not found"), "missing fixture"),
    (re.compile(r"Cannot find module|ERR_MODULE_NOT_FOUND|error TS2307|Module not found"), "module not found"),
    (re.compile(r"\bAttributeError: module '[^']+' has no attribute\b"), "missing symbol in module"),
    (re.compile(r"cannot import name"), "import error"),
]
ASSERT_EVIDENCE = re.compile(r"(AssertionError|\bassert\b|^E\s+|FAILED|^FAIL:|AssertionFailedError|Expected|expect\(|✕|✗|✕)", re.M)
BWRAP_ERR = re.compile(r"(?m)^bwrap: (.+)$")
RUNNER_MISSING = re.compile(r"(?m)^\S*python[0-9.]*: (No module named \S+)\s*$")
# a sandbox limit, not the code under test: never evidence that a test "fails before"
LIMIT_HIT = re.compile(r"\bMemoryError\b|_ArrayMemoryError|Unable to allocate|OutOfMemoryError|[Cc]annot allocate memory|"
                       r"could not allocate memory|failed to allocate|memory allocation of \d+ bytes failed|"
                       r"can't start new thread|Cannot fork|fork failed|pthread_create|Too many processes|"
                       r"Array buffer allocation failed|Cannot allocate Wasm memory|OutOfMemoryException|"
                       r"Fatal process out of memory|\bENOMEM\b|std::bad_alloc|heap out of memory|"
                       r"Resource temporarily unavailable|\bEAGAIN\b|fork: retry|No space left on device|\bENOSPC\b|"
                       r"File too large|\bEFBIG\b|Too many open files|\bEMFILE\b")
# the sandbox withholds network, host services and host files: such a failure says nothing about the code
ENV_HIT = re.compile(r"ConnectionRefusedError|Connection refused|\bECONNREFUSED\b|Network is unreachable|\bENETUNREACH\b|"
                     r"Name or service not known|Temporary failure in name resolution|\bEAI_AGAIN\b|getaddrinfo ENOTFOUND|"
                     r"Read-only file system|\bEROFS\b|PermissionError|Permission denied|\bEACCES\b|"
                     r"Cannot connect to the Docker daemon|docker\.sock|could not connect to server|"
                     r"connection to server at .{0,80} failed|Connection reset by peer|\bECONNRESET\b")
LIMIT_SIGNALS = {24: "CPU time limit (SIGXCPU)", 25: "file size limit (SIGXFSZ)"}
NO_TESTS = re.compile(r"(no tests ran|collected 0 items|\bRan 0 tests|No tests found|(?<![0-9])0 passing|no test files)", re.I)


def classify_run(rc, out, timed_out):
    if timed_out:
        return "timeout", "timed out"
    if rc in (126, 127):
        return "blocked", "command could not be executed (exit %d)" % rc
    m = BWRAP_ERR.search(out)
    if rc != 0 and m:
        return "blocked", "sandbox: %s" % m.group(1)[:160]
    m = RUNNER_MISSING.search(out)
    if rc != 0 and m:
        return "blocked", "test runner not installed (%s)" % m.group(1)
    if rc == 0:
        return ("no-tests", "exit 0 but no tests ran") if NO_TESTS.search(out) else ("pass", "passed")
    sig = -rc if rc < 0 else rc - 128 if rc > 128 else 0
    if sig in LIMIT_SIGNALS:
        return "limit", "hit the sandbox %s" % LIMIT_SIGNALS[sig]
    m = LIMIT_HIT.search(out)
    if m:
        return "limit", "hit a sandbox resource limit (%s)" % m.group(0)
    m = ENV_HIT.search(out)
    if m:
        return "environment", "needs something the sandbox withholds (%s): network, host services or host files" % m.group(0)
    if "test session starts" in out and rc in (2, 3, 4, 5):
        return "wrong-reason", {2: "pytest interrupted (collection error)", 3: "pytest internal error",
                                4: "pytest usage error", 5: "no tests collected"}[rc]
    for rx, why in WRONG_REASON:
        if rx.search(out):
            return "wrong-reason", why
    if NO_TESTS.search(out):
        return "no-tests", "no tests ran"
    if ASSERT_EVIDENCE.search(out):
        return "assert-fail", "failed inside the test (assertion or exception)"
    return "fail-unclassified", "failed (exit %d) without recognizable test-failure output" % rc


LIMIT_ADVICE = ("a sandbox limit was hit (before: %s; after: %s), so the result says nothing about the code; raise "
                "--mem-mb / --max-procs / --max-file-mb / --tmp-mb / --timeout or narrow the command")


ENV_ADVICE = ("the run failed on something the sandbox withholds, or on a genuine failure that mentions such an error "
              "(before: %s; after: %s); read the logs. fbpa cannot judge such a test: pass values with --env, dependencies "
              "with --link/--toolchain, or record the check as an owner decision")


def safe_extract(tar_bytes, dest):
    import io
    with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tf:
        if hasattr(tarfile, "data_filter"):
            tf.extractall(dest, filter="data")
            return
        real = os.path.realpath(dest)
        for m in tf.getmembers():
            target = os.path.realpath(os.path.join(dest, m.name))
            if not (target == real or target.startswith(real + os.sep)) or m.isdev():
                raise UsageError("unsafe path in git archive: %s" % m.name)
            if (m.issym() or m.islnk()) and (os.path.isabs(m.linkname) or ".." in m.linkname.split("/")):
                raise UsageError("unsafe link in git archive: %s" % m.name)
        tf.extractall(dest)


def copy_worktree(repo, files, dest):
    for f in files:
        src = os.path.join(repo, f)
        dst = os.path.join(dest, f)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.islink(src):
            os.symlink(os.readlink(src), dst)
        elif os.path.isfile(src):
            shutil.copy2(src, dst)


def snapshot_id(repo):
    sc = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skill-context.sh")
    if os.path.isfile(sc):
        p = subprocess.run(["bash", sc, "snapshot"], cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=120)
        return p.stdout.decode("utf-8", "replace").strip()
    st = git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all", binary=True)
    df = git(repo, "diff", "HEAD", "--binary", binary=True, check=False)
    return "status-hash " + hashlib.sha256(st + df).hexdigest()[:12]


# --------------------------------------------------------------------------- sandbox
#
# The fbpa/pinned test command is repository code. It runs only inside a bubblewrap
# sandbox: new user, mount, PID, network, IPC, UTS and cgroup namespaces; no network
# (an isolated loopback only); all capabilities dropped; a cleared environment; a
# root filesystem that holds only the read-only system directories, a few /etc
# files, a private /tmp, the copy under test at /work and explicitly declared
# read-only dependency (--link) and toolchain (--toolchain) directories. The
# repository, $HOME, /run (the Docker socket), /srv, /var, /opt and every other host
# path are absent. Resource limits are applied to the sandbox's processes. When no
# trustworthy sandbox works here, the check is BLOCKED: tests never run on the host.

SYSTEM_RO = ("/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64", "/libx32")
ETC_RO = ("/etc/ld.so.cache", "/etc/ld.so.conf", "/etc/ld.so.conf.d", "/etc/alternatives", "/etc/localtime",
          "/etc/passwd", "/etc/group", "/etc/nsswitch.conf", "/etc/hosts", "/etc/mime.types", "/etc/os-release")
TRUSTED_BWRAP = ("/usr/bin/bwrap", "/usr/local/bin/bwrap", "/bin/bwrap")
MANAGED_RUNTIMES = ("/.local/share/ailab-python/", "/.local/share/uv/python/", "/.pyenv/versions/",
                    "/.nvm/versions/node/", "/.volta/tools/image/")
SANDBOX_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
WORK = "/work"
REFUSED_TOOLCHAIN = ("/", "/etc", "/root", "/home", "/var", "/run", "/srv", "/proc", "/sys", "/dev", "/tmp", "/mnt", "/media", "/boot")


class SandboxUnavailable(Exception):
    pass


def under(path, root):
    path, root = path.rstrip("/") or "/", root.rstrip("/") or "/"
    return root == "/" or path == root or path.startswith(root + "/")


BWRAP_MIN = (0, 8)


def trusted_bwrap():
    """A bubblewrap binary that only root can replace: a root-owned file in a system
    location whose directories are root-owned and not writable by others. PATH and
    environment variables cannot point the tool at another binary."""
    if os.environ.get("AILAB_SANDBOX_DISABLE"):          # can only take the sandbox away, never replace it
        raise SandboxUnavailable("the sandbox is disabled by AILAB_SANDBOX_DISABLE")
    for cand in TRUSTED_BWRAP:
        real = os.path.realpath(cand)
        if not os.path.isfile(real):
            continue
        chain, d = [real], os.path.dirname(real)
        while True:
            chain.append(d)
            if d == "/":
                break
            d = os.path.dirname(d)
        ok = True
        for p in chain:
            st = os.stat(p)
            if st.st_uid != 0 or st.st_mode & 0o022:
                ok = False
                break
        if ok and os.access(real, os.X_OK):
            try:
                v = subprocess.run([real, "--version"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   timeout=10).stdout.decode("utf-8", "replace")
            except (OSError, subprocess.TimeoutExpired):
                v = ""
            m = re.search(r"(\d+)\.(\d+)", v)
            if not m or (int(m.group(1)), int(m.group(2))) < BWRAP_MIN:
                raise SandboxUnavailable("bubblewrap at %s is %s; fbpa needs %d.%d or newer (--disable-userns, --size)"
                                         % (real, v.strip() or "of unknown version", BWRAP_MIN[0], BWRAP_MIN[1]))
            return real
    raise SandboxUnavailable("bubblewrap is not installed in a root-owned system location (%s); install the "
                             "'bubblewrap' package" % ", ".join(TRUSTED_BWRAP))


def session_keyring_call():
    """A callable that makes the calling process join a new anonymous session keyring
    (keyctl(KEYCTL_JOIN_SESSION_KEYRING, NULL)), so the sandbox cannot read keys in the
    host session keyring; None where the syscall number is unknown. Failures are ignored:
    where keyctl is refused (Docker's default seccomp), the sandbox cannot use it either."""
    import ctypes
    import platform
    nr = {"x86_64": 250, "aarch64": 219}.get(platform.machine())
    if nr is None:
        return None
    try:
        libc = ctypes.CDLL(None, use_errno=True)
    except OSError:
        return None
    return lambda: libc.syscall(nr, 1, None)


def strip_setid(root):
    """Clear setuid/setgid bits in a work copy after a run (the test may have set them)."""
    for d, dirs, files in os.walk(root):
        for n in dirs + files:
            p = os.path.join(d, n)
            try:
                st = os.lstat(p)
                if not stat.S_ISLNK(st.st_mode) and st.st_mode & 0o6000:
                    os.chmod(p, stat.S_IMODE(st.st_mode) & ~0o6000)
            except OSError:
                pass


def find_socket(root, cap=1000000):
    """The first Unix socket under root (a read-only mount does not stop connect()), or a
    message when the tree is too large to check."""
    n = 0
    for d, dirs, files in os.walk(root):
        for x in dirs + files:
            n += 1
            if n > cap:
                return "more than %d entries, too large to check for sockets" % cap
            p = os.path.join(d, x)
            try:
                if stat.S_ISSOCK(os.lstat(p).st_mode):
                    return "contains the socket %s" % p
            except OSError:
                pass
    return None


LOG_CAP = 64 * 1024 * 1024                                # bytes of test output kept per phase


class Sandbox:
    def __init__(self, repo, links=(), toolchains=(), env=(), mem_mb=8192, max_procs=256, file_mb=2048, tmp_mb=1024):
        self.repo = os.path.realpath(repo)
        self.links = list(links)
        self.toolchains = [os.path.realpath(t) for t in toolchains]
        self.env = dict(env)
        self.mem_mb, self.max_procs, self.file_mb, self.tmp_mb = mem_mb, max_procs, file_mb, tmp_mb
        self.auto = []                                    # managed runtime roots found for linked venvs
        self.symlinks = []                                # (target, path): symlinks recreated inside the sandbox
        uid = os.getuid()
        self.existing_procs = 0                          # RLIMIT_NPROC counts all of this user's threads
        for d in os.listdir("/proc"):
            try:
                if d.isdigit() and os.stat("/proc/" + d).st_uid == uid:
                    self.existing_procs += len(os.listdir("/proc/%s/task" % d))
            except OSError:
                pass
        self.keyctl = session_keyring_call()
        if self.visible(self.repo):                       # e.g. a repository under /usr: tests could read the original
            raise SandboxUnavailable("the repository %s is inside a directory the sandbox exposes read-only (%s), so a "
                                     "test could read the original; run fbpa on a clone outside those directories"
                                     % (self.repo, ", ".join(SYSTEM_RO)))
        self.bwrap = trusted_bwrap()
        self._selftest()

    def _base(self):
        a = [self.bwrap, "--unshare-all", "--unshare-user", "--disable-userns", "--die-with-parent", "--new-session",
             "--cap-drop", "ALL", "--hostname", "ailab-sandbox", "--clearenv"]
        for d in SYSTEM_RO:
            if os.path.islink(d):
                a += ["--symlink", os.readlink(d), d]
            elif os.path.isdir(d):
                a += ["--ro-bind", d, d]
        for e in ETC_RO:
            a += ["--ro-bind-try", e, e]
        size = str(self.tmp_mb * 1024 * 1024)            # /tmp and /dev/shm are RAM: both are capped
        a += ["--proc", "/proc", "--dev", "/dev", "--size", size, "--tmpfs", "/dev/shm", "--remount-ro", "/dev",
              "--size", size, "--tmpfs", "/tmp", "--dir", "/tmp/home"]
        return a

    def _selftest(self):
        try:
            p = subprocess.run(self._base() + ["--", "/usr/bin/true"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               timeout=30, preexec_fn=self._limits(30))
        except (OSError, subprocess.TimeoutExpired) as e:
            raise SandboxUnavailable("bubblewrap could not start: %s" % e)
        if p.returncode != 0:
            msg = p.stdout.decode("utf-8", "replace").strip().splitlines()
            raise SandboxUnavailable("bubblewrap cannot create the sandbox here (%s). Usual causes: user namespaces are "
                                     "refused (inside Docker the default seccomp profile, on the host a sysctl or "
                                     "AppArmor restriction) or a resource limit was reached"
                                     % (msg[-1] if msg else "exit %d" % p.returncode))

    def _limits(self, cpu_s):
        mem, fsize, nproc = self.mem_mb * 1024 * 1024, self.file_mb * 1024 * 1024, self.max_procs
        existing, keyctl = self.existing_procs, self.keyctl

        def apply():
            import resource
            os.setsid()
            if keyctl:
                keyctl()                                  # a new, empty session keyring: host keys stay outside
            for lim, val in ((resource.RLIMIT_AS, mem), (resource.RLIMIT_FSIZE, fsize), (resource.RLIMIT_NOFILE, 1024),
                             (resource.RLIMIT_CORE, 0), (resource.RLIMIT_CPU, int(cpu_s)),
                             (resource.RLIMIT_NPROC, existing + nproc)):
                soft, hard = resource.getrlimit(lim)
                v = val if hard == resource.RLIM_INFINITY else min(val, hard)
                resource.setrlimit(lim, (v, v))
        return apply

    def visible(self, path):
        """Is a host path visible inside the sandbox (read-only), by its real path?"""
        real = os.path.realpath(path)
        roots = [os.path.realpath(d) for d in SYSTEM_RO if os.path.exists(d)] + self.toolchains + self.auto
        roots += [os.path.realpath(os.path.join(self.repo, l)) for l in self.links]
        return any(under(real, r) for r in roots)

    def preflight(self, cmd, copy_dir):
        """Make the interpreters the command needs visible, or say why it cannot run."""
        for l in self.links:                              # a linked virtualenv needs its base interpreter
            cfg = read_text(os.path.join(self.repo, l, "pyvenv.cfg")) or ""
            m = re.search(r"(?m)^home\s*=\s*(.+?)\s*$", cfg)
            if m:
                self._need(m.group(1), "the base interpreter of linked %s" % l)
        for tok in cmd[1:]:                                # absolute paths used as arguments (e.g. after env)
            if tok.startswith("/") and os.path.lexists(tok) and self.visible(tok):
                self._keep_path(tok)
        if os.path.basename(cmd[0]) == "env":              # env [-i] [-u NAME] [-C DIR] [VAR=value ...] command ...
            rest = list(cmd[1:])
            while rest and (rest[0].startswith("-") or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", rest[0])):
                opt = rest.pop(0)
                if opt in ("-u", "--unset", "-C", "--chdir") and rest:
                    rest.pop(0)                          # the option's argument
                elif opt in ("-S", "--split-string") or opt.startswith("--split-string="):
                    raise SandboxUnavailable("env -S is not supported in the test command; write the words out")
                elif opt == "--":
                    break
            if rest:
                self.preflight_command(rest[0], copy_dir)
        self.preflight_command(cmd[0], copy_dir)

    def preflight_command(self, first, copy_dir):
        if "/" not in first:
            host = shutil.which(first, path=":".join([SANDBOX_PATH] + [t + "/bin" for t in self.toolchains + self.auto]))
            if not host:
                raise SandboxUnavailable("'%s' is not on the sandbox PATH (system directories%s); use a path inside the "
                                         "work tree, an absolute path, or --toolchain <dir>"
                                         % (first, " and the toolchain bin directories" if self.toolchains else ""))
            return
        if os.path.isabs(first):
            self._need(first, "the command")
            return
        rel = os.path.normpath(first)
        for l in self.links:
            if rel == l or rel.startswith(l.rstrip("/") + "/"):
                target = os.path.join(self.repo, rel)
                if os.path.islink(target):
                    self._need(os.path.realpath(target), "the command %s" % first)
                return
        if not os.path.lexists(os.path.join(copy_dir, rel)):
            raise SandboxUnavailable("the command %s does not exist in the work tree" % first)

    def _mounted_literally(self, path):
        roots = [d for d in SYSTEM_RO if os.path.exists(d)] + self.toolchains + self.auto
        roots += [os.path.join(self.repo, l) for l in self.links]
        return any(under(path, r) for r in roots)

    def _keep_path(self, path):
        """Recreate the symlinks along an absolute path inside the sandbox, so the path
        works as invoked (resolving it instead would lose a virtualenv's identity)."""
        cur = "/"
        for comp in [c for c in os.path.normpath(path).split("/") if c]:
            nxt = os.path.join(cur, comp)
            if self._mounted_literally(nxt):
                return
            if os.path.islink(nxt):
                tgt = os.readlink(nxt)
                if (tgt, nxt) not in self.symlinks:
                    self.symlinks.append((tgt, nxt))
                cur = os.path.realpath(nxt)
                continue
            cur = nxt

    def _need(self, path, what):
        self._keep_path(path)
        if self.visible(path):
            return
        real = os.path.realpath(path)
        for marker in MANAGED_RUNTIMES:               # a managed runtime install: expose its root read-only
            i = real.find(marker)
            if i >= 0:
                rest = real[i + len(marker):].split("/")
                root = real[:i + len(marker)] + rest[0]
                if rest[0] and not under(self.repo, root) and not under(root, self.repo):
                    problem = install_root_problem(root)
                    if problem:
                        raise SandboxUnavailable("%s needs %s, which %s" % (what, root, problem))
                    self.auto.append(root)
                    return
        raise SandboxUnavailable("%s (%s) is outside the paths the sandbox exposes; pass --toolchain <dir> to "
                                 "expose its installation directory read-only" % (what, real))

    def argv(self, copy_dir, cmd, phase):
        a = self._base() + ["--bind", copy_dir, WORK]
        for t in self.toolchains + self.auto:
            a += ["--ro-bind", t, t]
        for l in self.links:
            src = os.path.join(self.repo, l)
            a += ["--ro-bind", src, WORK + "/" + l.strip("/"), "--ro-bind", src, src]   # also at its own path (shebangs, venv)
        for tgt, path in self.symlinks:
            a += ["--symlink", tgt, path]
        env = {"PATH": ":".join([t + "/bin" for t in self.toolchains + self.auto] + [SANDBOX_PATH]), "HOME": "/tmp/home",
               "TMPDIR": "/tmp", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
               "GIT_CEILING_DIRECTORIES": "/", "CI": "1"}
        env.update(self.env)
        for k in sorted(env):
            a += ["--setenv", k, env[k]]
        return a + ["--chdir", WORK, "--"] + list(cmd)

    def run(self, cmd, copy_dir, log, timeout, phase):
        """Run in the sandbox. Output goes through a pipe (the test cannot learn the log path or
        the phase from its file descriptors) into the log, at most LOG_CAP bytes."""
        t0 = time.time()
        timed_out = False
        with open(log, "wb") as fh:
            try:
                p = subprocess.Popen(self.argv(copy_dir, cmd, phase), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, preexec_fn=self._limits(timeout + 30))
            except OSError as e:
                fh.write(("cannot start the sandbox: %s\n" % e).encode())
                return 127, read_text(log) or "", False, 0.0

            def pump():
                written = 0
                for chunk in iter(lambda: p.stdout.read(65536), b""):
                    if written < LOG_CAP:
                        fh.write(chunk[:LOG_CAP - written])
                    written += len(chunk)
                if written > LOG_CAP:
                    fh.write(("\n[ailab] output truncated: %d of %d bytes kept\n" % (LOG_CAP, written)).encode())
            reader = threading.Thread(target=pump, daemon=True)
            reader.start()
            try:
                rc = p.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                try:
                    os.killpg(p.pid, 9)                   # bwrap and its PID namespace die together
                except OSError:
                    pass
                rc = p.wait()
            reader.join(30)
            p.stdout.close()
        return rc, read_text(log, limit=20_000_000) or "", timed_out, time.time() - t0


def leak_check(repo, links):
    """Static check (nothing is executed): would the command import code from the real
    repository through a linked dependency directory? Editable installs (.pth files,
    __editable__ finders, .egg-link) and workspace symlinks in node_modules point there.
    Inside the sandbox the repository is absent, so such imports would fail confusingly."""
    real_repo = os.path.realpath(repo)
    keys = {real_repo, os.path.abspath(repo)}
    found = []
    for l in links:
        d = os.path.join(repo, l)
        sites = []
        for pat in ("lib/python*/site-packages", "lib64/python*/site-packages", "Lib/site-packages"):
            sites += glob.glob(os.path.join(d, pat))
        for sp in sites:
            try:
                names = os.listdir(sp)
            except OSError:
                continue
            for n in names:
                if n.endswith((".pth", ".egg-link")) or (n.startswith("__editable__") and n.endswith(".py")):
                    text = read_text(os.path.join(sp, n), limit=1_000_000) or ""
                    if any(k + "/" in text or text.strip().endswith(k) or ("'%s'" % k) in text or ('"%s"' % k) in text for k in keys):
                        found.append("%s refers to the repository (editable install)" % os.path.relpath(os.path.join(sp, n), repo))
        if os.path.basename(l.rstrip("/")) == "node_modules" and os.path.isdir(d):
            real_nm = os.path.realpath(d)
            for e in os.listdir(d):
                paths = [os.path.join(d, e)]
                if e.startswith("@") and os.path.isdir(paths[0]):
                    paths = [os.path.join(paths[0], x) for x in os.listdir(paths[0])]
                for q in paths:
                    target = os.path.realpath(q)
                    if os.path.islink(q) and under(target, real_repo) and not under(target, real_nm):
                        found.append("node_modules entry %s links into the repository (workspace package)" % os.path.relpath(q, repo))
    return found[:5]


def check_toolchain(t, repo):
    real = os.path.realpath(t)
    home = os.path.realpath(os.path.expanduser("~"))
    if not os.path.isabs(t) or not os.path.isdir(real):
        raise UsageError("--toolchain: '%s' is not an existing absolute directory" % t)
    if real in REFUSED_TOOLCHAIN or under(home, real) or real == home or under(os.path.realpath(repo), real) \
            or under(real, os.path.realpath(repo)):
        raise UsageError("--toolchain: '%s' is too broad or contains the repository or the home directory" % t)
    return real


def protected_prefixes():
    """immutable / appendonly / production prefixes from the guard configuration."""
    out = []
    here = os.path.dirname(os.path.abspath(__file__))
    user = os.environ.get("AILAB_GUARD_CONFIG") or os.path.expanduser("~/.config/ailab-engineering/protected-paths.conf")
    for f in (os.path.join(here, "..", "config", "protected-paths.conf"), user):
        for line in (read_text(f) or "").splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0] in ("immutable", "appendonly", "production") and parts[1].startswith("/"):
                out.append(os.path.realpath(parts[1]))
    return out


def check_toolchain_safe(t, repo):
    real = check_toolchain(t, repo)
    home = os.path.realpath(os.path.expanduser("~"))
    if under(real, home) and not any(m in real + "/" for m in MANAGED_RUNTIMES) \
            and any(c.startswith(".") for c in os.path.relpath(real, home).split("/")):
        raise UsageError("--toolchain: '%s' is a hidden directory under the home directory (credentials live there); "
                         "only managed runtime installs (%s) are allowed there" % (t, ", ".join(m.strip("/") for m in MANAGED_RUNTIMES)))
    problem = install_root_problem(real)
    if problem:
        raise UsageError("--toolchain: '%s' %s" % (t, problem))
    return real


def install_root_problem(real):
    """Why a directory may not be exposed read-only as an interpreter or runtime install
    root (--toolchain, or a runtime found for a linked virtualenv), or None."""
    for p in protected_prefixes():
        if under(real, p) or under(p, real):
            return "overlaps the protected path %s" % p
    for v in ("/proc", "/sys", "/dev", "/run", "/var/run"):
        if under(real, v):
            return "is under %s" % v
    try:
        owner = os.stat(real).st_uid
    except OSError as e:
        return "cannot be read (%s)" % e.strerror
    if owner not in (0, os.geteuid()):
        return "belongs to another user"
    if not os.path.isdir(os.path.join(real, "bin")):
        return "is not an installation directory (no bin/); give the interpreter's or runtime's install root"
    return find_socket(real)


def tail(text, n=8):
    lines = [l for l in text.rstrip("\n").split("\n") if l.strip()]
    return lines[-n:]


def fbpa_blocked(a, label, reason, extra=None):
    res = {"check": label, "mode": a.mode, "verdict": "BLOCKED", "reason": reason, "tests_run": False, "version": VERSION}
    res.update(extra or {})
    if a.json:
        print(json.dumps(res, indent=2))
    else:
        print("%s: BLOCKED — %s" % (label, reason))
        print("No test was run.")
    return 4


def cmd_fbpa(a):
    repo = repo_root(a.repo)
    cmd = a.test_command[1:] if a.test_command and a.test_command[0] == "--" else a.test_command
    if not cmd:
        raise UsageError("give the targeted test command after --, e.g. -- .venv/bin/python -m pytest tests/test_x.py")
    base = resolve_ref(repo, a.base)
    tests = [t for x in a.tests for t in x.split(",") if t]
    if not tests:
        raise UsageError("--tests: name the new or changed test files (and fixtures) to carry over to the base")
    real_repo = os.path.realpath(repo)
    for t in tests:
        if os.path.isabs(t) or ".." in t.split("/") or not os.path.isfile(os.path.join(repo, t)):
            raise UsageError("--tests: '%s' is not an existing repository-relative file" % t)
        if os.path.realpath(os.path.join(repo, t)) != os.path.join(real_repo, os.path.normpath(t)):
            raise UsageError("--tests: '%s' is or passes through a symbolic link; carry the real file" % t)
    wd = os.path.abspath(a.workdir)
    real_wd = os.path.realpath(wd)
    if real_wd == real_repo or real_wd.startswith(real_repo + os.sep) or real_repo.startswith(real_wd + os.sep):
        raise UsageError("--workdir must be outside the repository (use a task scratch directory)")
    if real_wd in ("/", os.path.realpath(os.path.expanduser("~"))) or real_wd.count(os.sep) < 2:
        raise UsageError("--workdir is too broad: %s" % wd)
    if os.path.exists(wd) and os.listdir(wd):
        raise UsageError("--workdir must be empty or absent: %s" % wd)
    for l in a.link:
        if os.path.isabs(l) or ".." in l.split("/") or not os.path.isdir(os.path.join(repo, l)):
            raise UsageError("--link: '%s' is not an existing repository-relative directory" % l)
        if ".git" in l.strip("/").split("/"):
            raise UsageError("--link: '%s' would share git metadata with the copies" % l)
        norm = os.path.normpath(l)
        if norm in (".", "") or os.path.realpath(os.path.join(repo, l)) != os.path.join(real_repo, norm):
            raise UsageError("--link: '%s' is the repository itself or passes through a symbolic link" % l)
        if git(repo, "ls-files", "-z", "--", norm, check=False).strip("\0") or \
                git(repo, "ls-tree", "-r", "--name-only", base, "--", norm, check=False).strip() or \
                subprocess.run(["git", "-c", "safe.directory=*", "-C", repo, "check-ignore", "-q", "--", norm],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0:
            raise UsageError("--link: '%s' must be a git-ignored, untracked directory that is not in the base commit "
                             "(an installed dependency such as .venv or node_modules); it would hide the copy under test" % l)
        problem = find_socket(os.path.join(real_repo, norm))
        if problem:
            raise UsageError("--link: '%s' %s" % (l, problem))
    toolchains = [check_toolchain_safe(t, repo) for t in a.toolchain]
    env = {}
    for kv in a.env:
        k, sep, v = kv.partition("=")
        if not sep or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", k):
            raise UsageError("--env: '%s' is not KEY=VALUE" % kv)
        env[k] = v
    label = "PINNED" if a.mode == "refactor" else "FBPA"
    leaks = leak_check(repo, a.link)
    if leaks:
        return fbpa_blocked(a, label, "the command would import code from the real repository: %s. Make it import the "
                            "copy (for Python put the source directory first, e.g. `env PYTHONPATH=src ...`) or use a "
                            "non-editable environment" % "; ".join(leaks), {"leaks": leaks})
    try:
        sb = Sandbox(repo, a.link, toolchains, env, a.mem_mb, a.max_procs, a.max_file_mb, a.tmp_mb)
    except SandboxUnavailable as e:
        return fbpa_blocked(a, label, "no secure execution environment: %s. Tests never run on the host; run "
                            "`intel.sh sandbox-check` for details" % e)
    os.makedirs(wd, exist_ok=True)
    os.chmod(wd, 0o700)                                   # copies may hold files the test created
    snap0 = snapshot_id(repo)
    # the copies get random names while the test runs (they are visible inside through mountinfo and
    # bwrap's arguments): nothing tells the test which phase it is in; renamed afterwards for the report
    import secrets
    before = os.path.join(wd, "copy-" + secrets.token_hex(8))
    after = os.path.join(wd, "copy-" + secrets.token_hex(8))
    os.makedirs(before); os.makedirs(after)
    safe_extract(git(repo, "archive", "--format=tar", base, binary=True, timeout=300), before)
    carried = []
    for t in tests:
        dst = os.path.join(before, t)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(os.path.join(repo, t), dst)
        carried.append(t)
    copy_worktree(repo, tree_files(repo), after)
    for l in a.link:
        for c in (before, after):
            if os.path.lexists(os.path.join(c, os.path.normpath(l))):
                return fbpa_blocked(a, label, "--link %s exists in the copy under test; linking it would hide the copy" % l)
    try:
        sb.preflight(cmd, after)
    except SandboxUnavailable as e:
        return fbpa_blocked(a, label, str(e))
    rc_b, out_b, to_b, dur_b = sb.run(cmd, before, os.path.join(wd, "before.log"), a.timeout, "before")
    rc_a, out_a, to_a, dur_a = sb.run(cmd, after, os.path.join(wd, "after.log"), a.timeout, "after")
    strip_setid(before)
    strip_setid(after)
    os.rename(before, os.path.join(wd, "before"))
    os.rename(after, os.path.join(wd, "after"))
    snap1 = snapshot_id(repo)
    cb, why_b = classify_run(rc_b, out_b, to_b)
    ca, why_a = classify_run(rc_a, out_a, to_a)
    if snap0 != snap1:
        verdict, reason, code = "INVALID", "the repository changed during the check (%s -> %s)" % (snap0, snap1), 4
    elif a.mode == "refactor":
        if cb == "timeout" or ca == "timeout":
            verdict, reason, code = "INCONCLUSIVE", "timed out after %ds" % a.timeout, 3
        elif cb == "blocked" or ca == "blocked":
            verdict, reason, code = "BLOCKED", "test command could not run: before %s, after %s" % (why_b, why_a), 4
        elif cb == "limit" or ca == "limit":
            verdict, reason, code = "INCONCLUSIVE", LIMIT_ADVICE % (why_b, why_a), 3
        elif cb == "environment" or ca == "environment":
            verdict, reason, code = "INCONCLUSIVE", ENV_ADVICE % (why_b, why_a), 3
        elif cb in ("wrong-reason", "no-tests", "fail-unclassified"):
            verdict, reason, code = "INCONCLUSIVE", "the pinning tests did not run on the base (%s): they may need code that exists only after the change" % why_b, 3
        elif cb != "pass":
            verdict, reason, code = "FAIL", "the pinning tests do not pass on the base (%s): they do not describe current behaviour" % why_b, 1
        elif ca != "pass":
            verdict, reason, code = "FAIL", "behaviour changed: the pinning tests fail on the final tree (%s)" % why_a, 1
        else:
            verdict, reason, code = "PASS", "the pinning tests pass on the base and on the final tree", 0
    elif cb == "timeout" or ca == "timeout":
        verdict, reason, code = "INCONCLUSIVE", "timed out after %ds (before %s, after %s); raise --timeout or narrow the command" % (a.timeout, why_b, why_a), 3
    elif cb == "blocked" or ca == "blocked":
        verdict, reason, code = "BLOCKED", "test command could not run: before %s, after %s" % (why_b, why_a), 4
    elif cb == "limit" or ca == "limit":
        verdict, reason, code = "INCONCLUSIVE", LIMIT_ADVICE % (why_b, why_a), 3
    elif cb == "environment" or ca == "environment":
        verdict, reason, code = "INCONCLUSIVE", ENV_ADVICE % (why_b, why_a), 3
    elif cb == "pass":
        verdict, reason, code = "FAIL", "the test passes on the base: it does not reproduce the bug", 1
    elif ca != "pass":
        verdict, reason, code = "FAIL", "the test does not pass on the final tree (%s)" % why_a, 1
    elif cb == "assert-fail":
        verdict, reason, code = "PASS", "fails before on an assertion, passes after", 0
    elif cb == "fail-unclassified":
        verdict, reason, code = "INCONCLUSIVE", "fails before but no assertion output was recognized; read before.log", 3
    else:
        verdict, reason, code = "INCONCLUSIVE", "fails before for the wrong reason (%s): the test may need code that exists only after the fix; test through an interface the base already has, or record the reason" % why_b, 3
    limits = {"memory_mb": a.mem_mb, "extra_processes": a.max_procs, "file_mb": a.max_file_mb, "tmp_mb": a.tmp_mb,
              "timeout_s": a.timeout, "process_limit_enforced": os.geteuid() != 0}
    res = {"check": label, "mode": a.mode, "verdict": verdict, "reason": reason, "base": base, "tests_carried": carried, "command": cmd,
           "before": {"exit": rc_b, "class": cb, "detail": why_b, "seconds": round(dur_b, 1), "log": os.path.join(wd, "before.log")},
           "after": {"exit": rc_a, "class": ca, "detail": why_a, "seconds": round(dur_a, 1), "log": os.path.join(wd, "after.log")},
           "isolation": {"workdir": wd, "sandbox": "bubblewrap " + sb.bwrap, "network": "none",
                         "read_only": [os.path.join(real_repo, l) for l in a.link] + toolchains + sb.auto,
                         "env": sorted(env), "limits": limits, "repository_snapshot": [snap0, snap1]},
           "tests_run": True, "version": VERSION}
    if a.json:
        print(json.dumps(res, indent=2))
        return code
    print("%s: %s — %s" % (label, verdict, reason))
    print("BASE: %s; carried to base: %s" % (base[:12], limited(carried, 8)))
    print("COMMAND: %s" % " ".join(cmd))
    print("BEFORE: exit %s, %s, %.1fs — log %s" % (rc_b, why_b, dur_b, res["before"]["log"]))
    for l in tail(out_b, a.tail):
        print("  | " + l[:160])
    print("AFTER: exit %s, %s, %.1fs — log %s" % (rc_a, why_a, dur_a, res["after"]["log"]))
    for l in tail(out_a, a.tail):
        print("  | " + l[:160])
    ro = res["isolation"]["read_only"]
    print("ISOLATION: bubblewrap sandbox, no network, cleared environment; visible: system directories, the copy at %s%s; "
          "limits: memory %d MiB, +%d processes%s, files %d MiB, timeout %ds; repository snapshot %s"
          % (WORK, ("; read-only: " + ", ".join(ro)) if ro else "", a.mem_mb, a.max_procs,
             "" if os.geteuid() != 0 else " (not enforced for root)", a.max_file_mb, a.timeout,
             "unchanged" if snap0 == snap1 else "CHANGED"))
    print("LIMITS: classification of the failure reason uses exit codes (pytest) and output patterns (other runners).")
    return code


PROBE = r'''
import json, os, resource, socket, subprocess, sys
r = {}
def blocked(host, port):
    try:
        socket.create_connection((host, port), 2).close()
        return False
    except OSError:
        return True
r["net_external_blocked"] = blocked("1.1.1.1", 53)
r["net_host_loopback_blocked"] = blocked("127.0.0.1", int(sys.argv[1]))
def ident(p):
    try:
        st = os.stat(p)
        return [st.st_dev, st.st_ino]
    except OSError:
        return None
r["seen"] = {p: ident(p) for p in sys.argv[2:]}   # what is at each host path inside the sandbox, if anything
r["canary_env_absent"] = "AILAB_SANDBOX_CANARY" not in os.environ
r["env_keys"] = sorted(os.environ)
r["rlimit_as_mb"] = resource.getrlimit(resource.RLIMIT_AS)[0] // (1024 * 1024)
try:
    open("/usr/.ailab-probe", "w").close(); r["system_read_only"] = False
except OSError:
    r["system_read_only"] = True
try:
    open("/work/.ailab-probe", "w").write("x"); r["work_writable"] = True
except OSError:
    r["work_writable"] = False
try:
    r["nested_userns_blocked"] = subprocess.run(["unshare", "-U", "true"], capture_output=True).returncode != 0
except OSError:
    r["nested_userns_blocked"] = True
r["pid"] = os.getpid()
print(json.dumps(r))
'''


def cmd_sandbox_check(a):
    """Prove the isolation on this machine: run a probe inside the sandbox and check it."""
    import tempfile
    print("SANDBOX-CHECK (fbpa / pinned test isolation)")
    try:
        repo = repo_root(a.repo)
    except UsageError:
        repo = os.getcwd()
    try:
        sb = Sandbox(repo, (), [], {}, a.mem_mb)
    except SandboxUnavailable as e:
        print("RESULT: BLOCKED — %s. fbpa and pinned checks will report BLOCKED and run no tests." % e)
        return 4
    py = shutil.which("python3", path=SANDBOX_PATH)
    if not py:
        print("RESULT: BLOCKED — no python3 in the system directories to run the probe")
        return 4
    listener = socket_listener()
    port = listener.getsockname()[1]
    work = tempfile.mkdtemp(prefix="ailab-sandbox-check.")
    try:
        open(os.path.join(work, "probe.py"), "w").write(PROBE)
        home = os.path.realpath(os.path.expanduser("~"))
        secret_paths = [os.path.realpath(repo), home, "/run", "/var/run/docker.sock", "/var", "/srv", "/root",
                        "/etc/shadow", "/opt", "/home", "/mnt"]
        os.environ["AILAB_SANDBOX_CANARY"] = "must-not-leak"
        log = os.path.join(work, "probe.log")
        rc, out, to, _ = sb.run([py, "-I", "probe.py", str(port)] + secret_paths, work, log, 60, "check")
        os.environ.pop("AILAB_SANDBOX_CANARY", None)
        try:
            r = json.loads(out.strip().splitlines()[-1])
        except (ValueError, IndexError):
            print("RESULT: BLOCKED — the probe did not run in the sandbox (exit %s): %s" % (rc, out.strip()[-300:]))
            return 4
        checks = [("network: external hosts unreachable", r["net_external_blocked"]),
                  ("network: host loopback services unreachable", r["net_host_loopback_blocked"]),
                  ("environment: host variables cleared", r["canary_env_absent"]),
                  ("filesystem: system directories read-only", r["system_read_only"]),
                  ("filesystem: the work copy is writable", r["work_writable"]),
                  ("namespaces: nested user namespaces refused", r["nested_userns_blocked"]),
                  ("limits: address space %d MiB" % r["rlimit_as_mb"], r["rlimit_as_mb"] == a.mem_mb)]
        # invisible: absent inside, or something else there (the private /tmp is not the host's /tmp)
        checks += [("filesystem: %s invisible" % p, seen is None or seen != host_ident(p))
                   for p, seen in r["seen"].items() if os.path.lexists(p)]
        bad = 0
        for name, ok in checks:
            print("  %-4s %s" % ("OK" if ok else "FAIL", name))
            bad += 0 if ok else 1
        print("  info: sandbox %s; environment inside: %s" % (sb.bwrap, ", ".join(r["env_keys"])))
        print("RESULT: %s" % ("PASS — fbpa and pinned checks run isolated here" if not bad else "FAIL — %d checks failed" % bad))
        return 0 if not bad else 4
    finally:
        listener.close()
        shutil.rmtree(work, ignore_errors=True)


def host_ident(p):
    try:
        st = os.stat(p)
        return [st.st_dev, st.st_ino]
    except OSError:
        return None


def socket_listener():
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    return s


# --------------------------------------------------------------------------- metrics

def known_categories():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shared", "finding-categories.md")
    text = read_text(p) or ""
    return set(re.findall(r"^- `([a-z][a-z-]+)`", text, re.M))


def cmd_metrics(a):
    known = known_categories()
    rows = []
    cats = defaultdict(int)
    limit_hits = 0
    for r in a.repos:
        d = os.path.join(r, ".claude", "wp")
        try:
            names = sorted(n for n in os.listdir(d) if n.endswith(".md"))
        except OSError:
            print("metrics: no .claude/wp in %s" % r, file=sys.stderr)
            continue
        for n in names:
            text = read_text(os.path.join(d, n)) or ""
            row = {"repo": os.path.basename(os.path.abspath(r)), "wp": n[:-3]}
            for key, rx in (("tier", r"^Tier:\s*(T[123])"), ("type", r"\bType:\s*([a-z-]+)"), ("size", r"\bSize:\s*([SML])\b"),
                            ("status", r"^Status:\s*([A-Z ]+?)\s*$")):
                m = re.search(rx, text, re.M)
                row[key] = m.group(1) if m else ""
            rounds = re.findall(r"review round (\d+)/(\d+)", text)
            row["rounds"] = max((int(x) for x, _ in rounds), default=0)
            if re.search(r"review round \d+/\d+:.*limit reached", text) or "round limit reached" in text:
                row["limit_reached"] = True
                limit_hits += 1
            m = re.search(r"^- Review: (.*)$", text, re.M)
            row["review"] = m.group(1).strip() if m else ""
            m = re.search(r"^- Post-acceptance rework \(21d\): (.*)$", text, re.M)
            row["rework"] = m.group(1).strip() if m else ""
            for c, k in re.findall(r"\b([a-z][a-z-]+)[x×](\d+)\b", " ".join(re.findall(r"review round[^\n]*", text))):
                if c in known:
                    cats[c] += int(k)
            rows.append(row)
    if a.json:
        print(json.dumps({"work_packages": rows, "finding_categories": dict(cats), "round_limit_reached": limit_hits}, indent=2))
        return 0
    print("METRICS — %d work packages in %d repositories (from WP files; nothing estimated)" % (len(rows), len(a.repos)))
    for r in rows:
        print("  %s/%s %s %s%s status=%s rounds=%s review=[%s] rework=[%s]%s" % (
            r["repo"], r["wp"], r["tier"] or "T?", r["type"] or "-", ("/" + r["size"]) if r["size"] else "",
            r["status"] or "?", r["rounds"], r["review"] or "none", r["rework"] or "none",
            " LIMIT REACHED" if r.get("limit_reached") else ""))
    print("FINDING CATEGORIES: %s" % (", ".join("%s %d" % kv for kv in sorted(cats.items(), key=lambda kv: (-kv[1], kv[0])))
                                      or "none recorded"))
    print("ROUND LIMIT REACHED: %d" % limit_hits)
    return 0


# --------------------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(prog="intel", description=__doc__.split("\n\n")[0], allow_abbrev=False)
    ap.add_argument("--version", action="version", version="intel " + VERSION)
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--repo", default=".", help="repository (default: current directory)")
        p.add_argument("--json", action="store_true")
        return p

    p = common(sub.add_parser("impact", allow_abbrev=False, help="dependents and tests reached by a change"))
    p.add_argument("--base", help="compare the work tree with this ref (default HEAD)")
    p.add_argument("--depth", type=int, default=2, help="dependent levels to list (default 2)")
    p.add_argument("--test-depth", type=int, default=6, help="import steps to search for tests (default 6)")
    p.add_argument("--hub", type=int, default=5, help="direct dependents that make a file a hub (default 5)")
    p.add_argument("--tests-only", action="store_true", help="print only test paths, one per line")
    p.add_argument("paths", nargs="*", help="files or directories instead of the changed set")

    p = common(sub.add_parser("history", allow_abbrev=False, help="commits, churn and rework signals for a scope"))
    p.add_argument("--since", help="ref, or 'checkpoint' for the CURRENT_STATE checkpoint")
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--show", type=int, default=15)
    p.add_argument("--changed", action="store_true", help="use the changed files of the work tree as the scope")
    p.add_argument("paths", nargs="*")

    p = common(sub.add_parser("signals", allow_abbrev=False, help="diff heuristics"))
    p.add_argument("--base")
    p.add_argument("--type", choices=("feature", "bugfix", "refactor", "infra", "docs"))
    p.add_argument("--large", type=int, default=1000)

    p = common(sub.add_parser("arch-check", allow_abbrev=False, help="boundary rules from ARCHITECTURE.md"))
    p.add_argument("--doc")
    p.add_argument("--changed", action="store_true", help="only imports made by changed files")
    p.add_argument("--base")
    p.add_argument("--strict", action="store_true", help="exit 1 on stale references or empty layers too")
    p.add_argument("--show", type=int, default=30)

    p = common(sub.add_parser("fbpa", allow_abbrev=False, help="fail-before/pass-after (bug fix) or pass/pass (refactor) check, in copies"))
    p.add_argument("--base", required=True, help="the pre-fix commit (WP 'Repo HEAD at start')")
    p.add_argument("--tests", action="append", default=[], required=True, help="files to carry to the base (comma-separated or repeated)")
    p.add_argument("--workdir", required=True, help="empty directory outside the repository (task scratch)")
    p.add_argument("--link", action="append", default=[], help="ignored dependency directory exposed read-only, e.g. .venv")
    p.add_argument("--toolchain", action="append", default=[], help="interpreter or SDK installation directory exposed read-only")
    p.add_argument("--env", action="append", default=[], help="KEY=VALUE set inside the sandbox (the host environment is not passed)")
    p.add_argument("--mem-mb", type=int, default=8192, help="address-space limit per process (default 8192)")
    p.add_argument("--max-procs", type=int, default=256, help="processes the test may add (default 256)")
    p.add_argument("--max-file-mb", type=int, default=2048, help="largest file the test may write (default 2048)")
    p.add_argument("--tmp-mb", type=int, default=1024, help="size of the private /tmp (default 1024)")
    p.add_argument("--mode", choices=("bugfix", "refactor"), default="bugfix",
                   help="bugfix: must fail before and pass after (default); refactor: must pass before and after")
    p.add_argument("--timeout", type=int, default=600)
    p.add_argument("--tail", type=int, default=6)
    p.add_argument("test_command", nargs=argparse.REMAINDER, help="-- <targeted test command and arguments>")

    p = common(sub.add_parser("sandbox-check", allow_abbrev=False, help="prove that fbpa/pinned test isolation works on this machine"))
    p.add_argument("--mem-mb", type=int, default=8192)

    p = sub.add_parser("metrics", allow_abbrev=False, help="review rounds and categories from WP files")
    p.add_argument("--json", action="store_true")
    p.add_argument("repos", nargs="+")

    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 2
    try:
        return {"impact": cmd_impact, "history": cmd_history, "signals": cmd_signals, "arch-check": cmd_arch_check,
                "fbpa": cmd_fbpa, "metrics": cmd_metrics, "sandbox-check": cmd_sandbox_check}[a.cmd](a)
    except UsageError as e:
        print("intel %s: %s" % (a.cmd, e), file=sys.stderr)
        return 2
    except subprocess.TimeoutExpired as e:
        print("intel %s: timed out: %s" % (a.cmd, e), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
