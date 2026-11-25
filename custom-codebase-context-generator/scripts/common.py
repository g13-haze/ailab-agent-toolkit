#!/usr/bin/env python3
"""Shared helper utilities and Tree-sitter loader for codebase-context-generator."""

import importlib
import json
import os
import posixpath
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

# Standard directories and files to exclude from codebase scanning
DEFAULT_IGNORE_DIRS: Set[str] = {
    ".git",
    ".hg",
    ".svn",
    ".idea",
    ".vscode",
    "node_modules",
    "vendor",
    "venv",
    ".venv",
    "env",
    ".env",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".tox",
    "target",
    "build",
    "dist",
    "out",
    "bin",
    "obj",
    ".next",
    ".nuxt",
    ".cache",
    "coverage",
    ".turbo",
    "Pods",
}

# Mapping file extensions to language identifiers
EXTENSION_TO_LANG: Dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".php": "php",
    ".dart": "dart",
    ".rb": "ruby",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cc": "cpp",
    ".cs": "c_sharp",
    ".swift": "swift",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".scala": "scala",
    ".pl": "perl",
    ".pm": "perl",
    ".sh": "bash",
    ".command": "bash",
}

# Mapping language name to tree-sitter python module and function candidates
GRAMMAR_MODULE_MAP: Dict[str, Tuple[str, List[str]]] = {
    "python": ("tree_sitter_python", ["language", "language_python"]),
    "typescript": ("tree_sitter_typescript", ["language_typescript", "language"]),
    "tsx": ("tree_sitter_typescript", ["language_tsx", "language"]),
    "javascript": ("tree_sitter_javascript", ["language", "language_javascript"]),
    "go": ("tree_sitter_go", ["language", "language_go"]),
    "rust": ("tree_sitter_rust", ["language", "language_rust"]),
    "java": ("tree_sitter_java", ["language", "language_java"]),
    "php": ("tree_sitter_php", ["language_php", "language"]),
    "dart": ("tree_sitter_dart_orchard", ["language", "language_dart"]),
    "c": ("tree_sitter_c", ["language", "language_c"]),
    "cpp": ("tree_sitter_cpp", ["language", "language_cpp"]),
    "c_sharp": ("tree_sitter_c_sharp", ["language", "language_c_sharp"]),
    "ruby": ("tree_sitter_ruby", ["language", "language_ruby"]),
}

_LOADED_LANGUAGES: Dict[str, Any] = {}
_PARSER_CACHE: Dict[str, Any] = {}


def load_tree_sitter_language(lang_name: str) -> Optional[Any]:
    """Dynamically loads and returns a tree-sitter Language object for lang_name."""
    if lang_name in _LOADED_LANGUAGES:
        return _LOADED_LANGUAGES[lang_name]

    if lang_name not in GRAMMAR_MODULE_MAP:
        return None

    try:
        from tree_sitter import Language
    except ImportError:
        return None

    mod_name, func_names = GRAMMAR_MODULE_MAP[lang_name]
    try:
        mod = importlib.import_module(mod_name)
        for fn_name in func_names:
            if hasattr(mod, fn_name):
                fn = getattr(mod, fn_name)
                ptr = fn()
                # Depending on tree-sitter version, Language() takes pointer or is created directly
                try:
                    lang = Language(ptr)
                except Exception:
                    lang = ptr
                _LOADED_LANGUAGES[lang_name] = lang
                return lang
    except Exception:
        pass

    return None


def get_parser_for_language(lang_name: str) -> Optional[Any]:
    """Returns a cached tree_sitter.Parser instance for lang_name."""
    if lang_name in _PARSER_CACHE:
        return _PARSER_CACHE[lang_name]

    lang = load_tree_sitter_language(lang_name)
    if not lang:
        return None

    try:
        from tree_sitter import Parser
        try:
            # Modern tree_sitter 0.22+
            parser = Parser(lang)
        except TypeError:
            # Older tree_sitter
            parser = Parser()
            parser.set_language(lang)
        _PARSER_CACHE[lang_name] = parser
        return parser
    except Exception:
        return None


# Generated build-output directory variants: dist.*, dist-*, dist_*, build.*
_BUILD_VARIANT_DIR_RE = re.compile(r"^(?:dist[.\-_]|build\.)", re.IGNORECASE)


def dir_exclusion_reason(
    dir_name: str, extra_ignores: Optional[Set[str]] = None, dir_path: Optional[Path] = None
) -> Optional[str]:
    """Returns the reason a directory is skipped, or None if it is scanned.

    A directory named `secrets` is skipped unless dir_path shows it is a Python package.
    """
    if dir_name.startswith(".") and dir_name not in {".github"}:
        return "ignored_dir"
    if dir_name in DEFAULT_IGNORE_DIRS:
        return "ignored_dir"
    if extra_ignores and dir_name in extra_ignores:
        return "ignored_dir"
    if dir_name == "secrets" and not (dir_path is not None and (dir_path / "__init__.py").is_file()):
        return "secrets_dir"
    if _BUILD_VARIANT_DIR_RE.match(dir_name):
        return "build_artifact_dir"
    return None


def should_ignore_dir(
    dir_name: str, extra_ignores: Optional[Set[str]] = None, dir_path: Optional[Path] = None
) -> bool:
    """Returns True if the directory should be skipped."""
    return dir_exclusion_reason(dir_name, extra_ignores, dir_path) is not None


def is_minified_name(file_name: str) -> bool:
    """True for generated bundle file names such as app.min.js or vendor.bundle.js."""
    lower = file_name.lower()
    return ".min." in lower or ".bundle." in lower


def _git_file_list(root: Path) -> Optional[List[str]]:
    """Lists tracked plus untracked, non-ignored files under root (read-only Git calls).

    Returns None when root is not inside a Git work tree or Git fails (caller walks the
    tree instead); a valid empty listing returns []. fsmonitor is disabled so no
    repository-configured hook runs.
    """
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    git = ["git", "-c", "core.fsmonitor=false", "-C", str(root)]
    try:
        probe = subprocess.run(
            git + ["rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, env=env, timeout=60,
        )
        if probe.returncode != 0 or probe.stdout.strip() != "true":
            return None
        listing = subprocess.run(
            git + ["ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            capture_output=True, env=env, timeout=300,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if listing.returncode != 0:
        return None
    return [e for e in listing.stdout.decode("utf-8", "surrogateescape").split("\0") if e]


def discover_source_files(
    root_path: Path,
    extra_ignores: Optional[Set[str]] = None,
    allowed_extensions: Optional[Set[str]] = None,
    *,
    use_git: bool = True,
    stats: Optional[Dict[str, Any]] = None,
) -> List[Path]:
    """Collects source files within root_path, respecting exclusions.

    Inside a Git work tree the file list comes from `git ls-files --cached --others
    --exclude-standard` (honours .gitignore); otherwise the tree is walked. The directory
    rules, minified-name rule and extension filter apply in both modes. If `stats` is a
    dict, it receives {"method": "git" | "walk", "excluded": {reason: count}}; in git
    mode counts are source files, in walk mode pruned directories count once each.
    """
    source_files: List[Path] = []
    root = root_path.resolve()
    excluded: Dict[str, int] = {}

    def has_source_ext(name: str) -> bool:
        ext = os.path.splitext(name)[1].lower()
        return ext in allowed_extensions if allowed_extensions else ext in EXTENSION_TO_LANG

    def count(reason: str) -> None:
        excluded[reason] = excluded.get(reason, 0) + 1

    git_entries = _git_file_list(root) if use_git else None
    method = "git" if git_entries is not None else "walk"

    if git_entries is not None:
        for rel in sorted(set(git_entries)):
            parts = rel.rstrip("/").split("/")
            fname = parts[-1]
            # Nested repositories and submodules are listed as directory entries
            if rel.endswith("/") or (not has_source_ext(fname) and root.joinpath(*parts).is_dir()):
                count("nested_repository")
                continue
            if fname.startswith(".") or not has_source_ext(fname):
                continue
            reason = None
            for k, d in enumerate(parts[:-1]):
                reason = dir_exclusion_reason(d, extra_ignores, root.joinpath(*parts[:k + 1]))
                if reason:
                    break
            if reason is None and is_minified_name(fname):
                reason = "minified_name"
            if reason:
                count(reason)
                continue
            fpath = root.joinpath(*parts)
            if fpath.is_file():
                source_files.append(fpath)
    else:
        for dirpath, dirnames, filenames in os.walk(root):
            # Prune ignored directories in-place
            kept = []
            for d in sorted(dirnames):
                reason = dir_exclusion_reason(d, extra_ignores, Path(dirpath) / d)
                if reason:
                    count(reason)
                else:
                    kept.append(d)
            dirnames[:] = kept

            for fname in filenames:
                if fname.startswith(".") or not has_source_ext(fname):
                    continue
                if is_minified_name(fname):
                    count("minified_name")
                    continue
                source_files.append(Path(dirpath) / fname)

    if stats is not None:
        stats["method"] = method
        stats["excluded"] = dict(sorted(excluded.items()))
    return sorted(source_files)


# ---------------------------------------------------------------------------
# Python module resolution (shared by dependency_graph.py and symbol_graph.py)
# ---------------------------------------------------------------------------

# Fallback for interpreters without sys.stdlib_module_names (Python < 3.10)
_STDLIB_FALLBACK = frozenset({
    "__future__", "abc", "argparse", "ast", "asyncio", "base64", "binascii", "bisect", "builtins",
    "calendar", "collections", "concurrent", "configparser", "contextlib", "contextvars", "copy", "csv",
    "ctypes", "dataclasses", "datetime", "decimal", "difflib", "email", "enum", "errno", "fcntl",
    "fnmatch", "fractions", "functools", "gc", "getpass", "glob", "gzip", "hashlib", "heapq", "hmac",
    "html", "http", "importlib", "inspect", "io", "ipaddress", "itertools", "json", "logging", "math",
    "mimetypes", "multiprocessing", "numbers", "operator", "os", "pathlib", "pickle", "platform",
    "pprint", "queue", "random", "re", "secrets", "select", "selectors", "shlex", "shutil", "signal",
    "socket", "sqlite3", "ssl", "stat", "statistics", "string", "struct", "subprocess", "sys",
    "tempfile", "textwrap", "threading", "time", "timeit", "tokenize", "traceback", "types", "typing",
    "unicodedata", "unittest", "urllib", "uuid", "warnings", "weakref", "xml", "zipfile", "zlib",
})
STDLIB_MODULE_NAMES: frozenset = frozenset(getattr(sys, "stdlib_module_names", None) or _STDLIB_FALLBACK)
_PY_EXTS = (".py", ".pyi")
def _py_file_rank(path: str) -> int:
    """Preference when several files define one dotted path: __init__.py, .py, __init__.pyi, .pyi."""
    name = posixpath.basename(path)
    if name == "__init__.py":
        return 0
    if name == "__init__.pyi":
        return 2
    return 1 if path.endswith(".py") else 3


def is_python_path(path: str) -> bool:
    return path.endswith(_PY_EXTS)


class PythonModuleResolver:
    """Resolves Python import records to project files without guessing.

    Built from the manifest's Python file paths (posix, relative to the scan root).
    Every query iterates sorted data, so results never depend on hash order.
    """

    def __init__(self, paths: Iterable[str]):
        files = sorted({p for p in paths if is_python_path(p)}, key=lambda p: (_py_file_rank(p), p))
        self.files: Set[str] = set(files)
        self.packages: Set[str] = {
            posixpath.dirname(p) for p in files if posixpath.basename(p) in ("__init__.py", "__init__.pyi")
        }
        # dotted suffix -> sorted [(prefix_dir, file)]
        suffixes: Dict[str, Dict[str, str]] = {}
        # top-level name -> prefix dirs where a module path starting with it begins
        tops: Dict[str, Set[str]] = {}
        for f in files:
            parts = self._module_parts(f)
            for i in range(len(parts)):
                # Only identifier components are importable (excludes `celery.schedules.py`)
                if not all(part.isidentifier() for part in parts[i:]):
                    continue
                prefix = "/".join(parts[:i])
                suffixes.setdefault(".".join(parts[i:]), {}).setdefault(prefix, f)
                tops.setdefault(parts[i], set()).add(prefix)
        self._suffixes: Dict[str, List[Tuple[str, str]]] = {
            k: sorted(v.items()) for k, v in suffixes.items()
        }
        self._tops: Dict[str, List[str]] = {k: sorted(v) for k, v in tops.items()}

    @staticmethod
    def _module_parts(path: str) -> List[str]:
        stem = os.path.splitext(path)[0]
        parts = [p for p in stem.split("/") if p]
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        return parts

    def import_root(self, importer_dir: str) -> str:
        """The importer's first ancestor directory (itself included) that is not a package:
        its own directory for a script, the directory above the top package otherwise."""
        d = importer_dir
        while d and d in self.packages:
            d = posixpath.dirname(d)
        return d

    def _valid_prefix(self, prefix: str, importer_dir: str) -> bool:
        """A source root is the repo root, `src`, or the importer's import root. Python 3 has
        no implicit relative imports, so a package directory itself is never a root."""
        return prefix in ("", "src") or prefix == self.import_root(importer_dir)

    def _is_project_top(self, top: str, importer_dir: str) -> bool:
        """True when `top` names a project module that this importer could only reach through
        a sys.path change: a module in a non-package directory under its import root."""
        root = self.import_root(importer_dir)
        for prefix in self._tops.get(top, []):
            if self._valid_prefix(prefix, importer_dir):
                return True
            if prefix not in self.packages and (not root or prefix == root or prefix.startswith(root + "/")):
                return True
        return False

    def module_file(self, dir_path: str) -> Optional[str]:
        """File for a module at dir_path (no extension): package __init__ first, then module file."""
        base = dir_path.strip("/")
        cands = (
            [posixpath.join(base, "__init__.py"), base + ".py", posixpath.join(base, "__init__.pyi"), base + ".pyi"]
            if base else ["__init__.py", "__init__.pyi"]
        )
        for c in cands:
            if c in self.files:
                return c
        return None

    def submodule(self, module_file: str, name: str) -> Optional[str]:
        """Submodule `name` of a package whose __init__ file is module_file, else None."""
        if posixpath.basename(module_file) not in ("__init__.py", "__init__.pyi") or not name:
            return None
        return self.module_file(posixpath.join(posixpath.dirname(module_file), name))

    def resolve_absolute(self, module: str, importer: str) -> Tuple[str, Optional[str]]:
        """Returns ("internal", file) | ("external", None) | ("unresolved", None)."""
        if not module or not all(p.isidentifier() for p in module.split(".")):
            return "unresolved", None
        top = module.split(".")[0]
        if top in STDLIB_MODULE_NAMES:
            return "external", None
        importer_dir = posixpath.dirname(importer)
        cands = [(pre, f) for pre, f in self._suffixes.get(module, []) if self._valid_prefix(pre, importer_dir)]
        if len(cands) == 1:
            return "internal", cands[0][1]
        if len(cands) > 1:
            root = self.import_root(importer_dir)
            for preferred in (
                [c for c in cands if c[0] == root],
                [c for c in cands if c[0] in ("", "src")],
            ):
                if len(preferred) == 1:
                    return "internal", preferred[0][1]
            return "unresolved", None
        if self._is_project_top(top, importer_dir):
            return "unresolved", None
        return "external", None

    def _relative_base(self, importer: str, level: int) -> Optional[str]:
        d = posixpath.dirname(importer)
        for _ in range(level - 1):
            if not d:
                return None
            d = posixpath.dirname(d)
        return d

    @staticmethod
    def normalize_record(rec: Dict[str, Any]) -> Optional[Tuple[str, int, str, List[Tuple[str, Optional[str]]]]]:
        """Returns (kind, level, module, [(name, alias)]) with kind "import" or "from"."""
        source = (rec.get("source") or "").strip()
        raw = (rec.get("raw") or "").strip()
        if any(k in rec for k in ("level", "module", "names")):
            level = int(rec.get("level") or 0)
            module = rec.get("module") or ""
            names = [(n.get("name") or "", n.get("alias")) for n in rec.get("names") or [] if n.get("name")]
            if level == 0 and not module:
                if not names and source:
                    names = [(source, None)]
                return ("import", 0, "", names) if names else None
            return "from", level, module, names
        # Legacy record: only source/symbols/raw
        level = len(source) - len(source.lstrip("."))
        module = source.lstrip(".")
        if raw.startswith("from ") or level:
            return "from", level, module, [(s, None) for s in rec.get("symbols") or [] if s]
        return ("import", 0, "", [(module, None)]) if module else None

    def resolve_import(self, rec: Dict[str, Any], importer: str) -> Dict[str, Any]:
        """Resolves one import record of `importer`.

        Returns {"targets": [file], "external": [top-level name], "unresolved": [source],
        "bindings": [(bound_name, binding)]} where binding is ("module", file),
        ("symbol", file, name), ("absolute", top_name) for `import a.b` binding `a`, or None
        when the bound name cannot be resolved.
        """
        out: Dict[str, Any] = {"targets": [], "external": [], "unresolved": [], "bindings": []}
        norm = self.normalize_record(rec)
        if norm is None:
            return out
        kind, level, module, names = norm

        def add(key: str, value: Any) -> None:
            if value not in out[key]:
                out[key].append(value)

        if kind == "import":
            for name, alias in names:
                status, target = self.resolve_absolute(name, importer)
                if status == "internal":
                    add("targets", target)
                elif status == "external":
                    add("external", name.split(".")[0])
                else:
                    add("unresolved", name)
                if alias:
                    out["bindings"].append((alias, ("module", target) if status == "internal" else None))
                else:
                    top = name.split(".")[0]
                    out["bindings"].append((top, ("absolute", top) if status == "internal" else None))
            return out

        source = "." * level + module
        if level > 0:
            base = self._relative_base(importer, level)
            if base is None:
                add("unresolved", source)
                for name, alias in names:
                    out["bindings"].append((alias or name, None))
                return out
            mod_dir = posixpath.join(base, *module.split(".")) if module else base
            mod_file = self.module_file(mod_dir)
            for name, alias in names:
                bound = alias or name
                sub = self.module_file(posixpath.join(mod_dir, name)) if name != "*" else None
                if sub:
                    add("targets", sub)
                    out["bindings"].append((bound, ("module", sub)))
                elif mod_file:
                    add("targets", mod_file)
                    if name != "*":
                        out["bindings"].append((bound, ("symbol", mod_file, name)))
                else:
                    add("unresolved", source if module or name == "*" else source + name)
                    if name != "*":
                        out["bindings"].append((bound, None))
            return out

        # Absolute `from module import names`
        mod_status: Optional[Tuple[str, Optional[str]]] = None
        for name, alias in names:
            bound = alias or name
            if name != "*":
                sub_status, sub = self.resolve_absolute(f"{module}.{name}", importer)
                if sub_status == "internal":
                    add("targets", sub)
                    out["bindings"].append((bound, ("module", sub)))
                    continue
            if mod_status is None:
                mod_status = self.resolve_absolute(module, importer)
            status, target = mod_status
            if status == "internal":
                add("targets", target)
                if name != "*":
                    out["bindings"].append((bound, ("symbol", target, name)))
            else:
                if status == "external":
                    add("external", module.split(".")[0])
                else:
                    add("unresolved", module)
                if name != "*":
                    out["bindings"].append((bound, None))
        if not names:
            status, target = self.resolve_absolute(module, importer)
            if status == "internal":
                add("targets", target)
            elif status == "external":
                add("external", module.split(".")[0])
            else:
                add("unresolved", module)
        return out


def save_json(data: Any, out_path: Path) -> None:
    """Writes formatted JSON data safely."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_json(in_path: Path) -> Any:
    """Reads JSON data from file."""
    with open(in_path, "r", encoding="utf-8") as f:
        return json.load(f)
