#!/usr/bin/env python3
"""
Symbol & Call Graph Generator and Navigator.
Builds a function-level, method-level, and class-level call/reference graph with full signatures.
Allows AI agents to quickly query signatures, argument types, callers, and callees across the codebase.
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    from common import PythonModuleResolver, is_python_path, load_json, save_json
    from dependency_graph import clean_import_source, normalize_import_path
except ImportError:
    from .common import PythonModuleResolver, is_python_path, load_json, save_json
    from .dependency_graph import clean_import_source, normalize_import_path

_TS_JS_LANGS = {"typescript", "tsx", "javascript"}
_TS_JS_EXTS = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")
_CALLABLE_KINDS = ("function", "class")
_MAX_DEPTH = 8


def _callee_parts(raw: str) -> Optional[List[str]]:
    """Splits a raw callee into identifiers; None when it is not a plain dotted name."""
    text, prev = raw.strip(), None
    while prev != text:  # strip generics, innermost first
        prev, text = text, re.sub(r"<[^<>]*>", "", text)
    text = text.strip()
    if not text or "(" in text or "[" in text:
        return None
    parts = text.split(".")
    if not all(p.replace("$", "_").isidentifier() for p in parts):
        return None
    return parts


def _bind(table: Dict[str, Any], name: str, binding: Any) -> None:
    """Records a binding; a name bound to different targets becomes unknown (None)."""
    if name in table and table[name] != binding:
        table[name] = None
    else:
        table[name] = binding


class _CallResolver:
    """Conservative, scope-aware static call resolution (never matches by bare name)."""

    def __init__(self, symbols: Dict[str, Dict[str, Any]], files_data: List[Dict[str, Any]],
                 class_methods: Dict[str, Dict[str, str]]):
        self.symbols = symbols
        self.class_methods = class_methods
        known_files = {f.get("path", "") for f in files_data}
        self.py = PythonModuleResolver(f.get("path", "") for f in files_data if _is_python(f))
        self.module_bindings: Dict[str, Dict[str, Any]] = {}
        self.local_bindings: Dict[str, List[Tuple[int, str, Any]]] = {}
        for f_entry in files_data:
            fpath = f_entry.get("path", "")
            imports = f_entry.get("symbols", {}).get("imports", [])
            table: Dict[str, Any] = {}
            local: List[Tuple[int, str, Any]] = []
            if _is_python(f_entry):
                for rec in imports:
                    for name, binding in self.py.resolve_import(rec, fpath)["bindings"]:
                        if rec.get("scope", "module") == "local":
                            local.append((int(rec.get("line") or 0), name, binding))
                        else:
                            _bind(table, name, binding)
            elif _is_ts_js(f_entry):
                for rec in imports:
                    spec = clean_import_source(rec.get("source") or rec.get("raw", ""))
                    target, internal = (normalize_import_path(spec, fpath, known_files)
                                        if spec.startswith(".") else ("", False))
                    for n in rec.get("names") or []:
                        bound, orig = n.get("alias") or n.get("name"), n.get("name")
                        if not bound:
                            continue
                        if not internal or orig == "default":
                            binding = None
                        elif orig == "*":
                            binding = ("module", target)
                        else:
                            binding = ("symbol", target, orig)
                        _bind(table, bound, binding)
            self.module_bindings[fpath] = table
            self.local_bindings[fpath] = local
        self._depth = 0

    def local_bindings_for(self, fpath: str, line_range: Optional[Tuple[int, int]]) -> Dict[str, Any]:
        """Function-local import bindings whose import line lies in the function's range."""
        local: Dict[str, Any] = {}
        if line_range:
            for line, name, binding in self.local_bindings.get(fpath, []):
                if line_range[0] <= line <= line_range[1]:
                    _bind(local, name, binding)
        return local

    def _callable(self, fpath: str, name: str, kinds: Tuple[str, ...] = _CALLABLE_KINDS) -> Optional[str]:
        sid = f"{fpath}::{name}"
        sym = self.symbols.get(sid)
        return sid if sym and sym["kind"] in kinds else None

    def lookup_name(self, fpath: str, name: str, depth: int = 0) -> Optional[Tuple[str, str]]:
        """What `name` is in module fpath: ("callable", symbol_id) or ("module", file)."""
        sid = self._callable(fpath, name)
        if sid:
            return ("callable", sid)
        if depth < _MAX_DEPTH:
            binding = self.module_bindings.get(fpath, {}).get(name)
            if binding:
                target = self._binding_target(binding, fpath, depth + 1)
                if target:
                    return target
        sub = self.py.submodule(fpath, name)
        return ("module", sub) if sub else None

    def _binding_target(self, binding: Any, importer: str, depth: int) -> Optional[Tuple[str, str]]:
        if binding[0] == "module":
            return ("module", binding[1])
        if binding[0] == "symbol":
            return self.lookup_name(binding[1], binding[2], depth)
        if binding[0] == "absolute":
            status, target = self.py.resolve_absolute(binding[1], importer)
            return ("module", target) if status == "internal" else None
        return None

    def find_method(self, class_id: str, name: str) -> Optional[str]:
        """Method `name` of a class, then of its resolvable bases (depth-first, in order)."""
        method = self.class_methods.get(class_id, {}).get(name)
        if method or self._depth > _MAX_DEPTH:
            return method
        self._depth += 1
        try:
            cls_path = self.symbols[class_id]["path"]
            for base in self.symbols[class_id].get("bases") or []:
                base_id = self.resolve(base, cls_path)
                if base_id and base_id != class_id and self.symbols[base_id]["kind"] == "class":
                    found = self.find_method(base_id, name)
                    if found:
                        return found
        finally:
            self._depth -= 1
        return None

    def resolve_in_module(self, fpath: str, rest: List[str]) -> Optional[str]:
        for _ in range(len(rest)):
            head = self.lookup_name(fpath, rest[0])
            if not head:
                return None
            if len(rest) == 1:
                return head[1] if head[0] == "callable" else None
            if head[0] == "callable":
                if len(rest) == 2 and self.symbols[head[1]]["kind"] == "class":
                    return self.find_method(head[1], rest[1])
                return None
            fpath, rest = head[1], rest[1:]
        return None

    def resolve(self, raw: str, fpath: str, class_id: Optional[str] = None, self_name: Optional[str] = None,
                local_names: Optional[Set[str]] = None, local: Optional[Dict[str, Any]] = None) -> Optional[str]:
        """Resolves one raw callee to a symbol id, or None when it is unknown.

        Order for the first name: the method's own self/cls parameter; a name bound in the
        calling function (parameter, assignment, loop target, nested def, ...) is unknown;
        function-local imports; same-file definitions; module-level imports.
        """
        parts = _callee_parts(raw)
        if not parts:
            return None
        head = parts[0]
        if self_name and head == self_name and class_id and len(parts) >= 2:
            return self.find_method(class_id, parts[1]) if len(parts) == 2 else None
        if local_names and head in local_names:
            return None
        if local and head in local:
            return self._resolve_via_binding(local[head], parts, fpath)
        sid = self._callable(fpath, head)
        if sid:
            if len(parts) == 1:
                return sid
            if len(parts) == 2 and self.symbols[sid]["kind"] == "class":
                return self.find_method(sid, parts[1])
            return None
        return self._resolve_via_binding(self.module_bindings.get(fpath, {}).get(head), parts, fpath)

    def _resolve_via_binding(self, binding: Any, parts: List[str], importer: str) -> Optional[str]:
        if not binding:  # not bound, external, or bound to conflicting targets
            return None
        if binding[0] == "absolute":
            dotted = [binding[1]] + parts[1:]
            for k in range(len(dotted) - 1, 0, -1):
                status, target = self.py.resolve_absolute(".".join(dotted[:k]), importer)
                if status == "internal":
                    return self.resolve_in_module(target, dotted[k:])
            return None
        target = self._binding_target(binding, importer, 0)
        if not target:
            return None
        if len(parts) == 1:
            return target[1] if target[0] == "callable" else None
        if target[0] == "module":
            return self.resolve_in_module(target[1], parts[1:])
        if len(parts) == 2 and self.symbols[target[1]]["kind"] == "class":
            return self.find_method(target[1], parts[1])
        return None


def _is_python(f_entry: Dict[str, Any]) -> bool:
    return f_entry.get("language") == "python" or is_python_path(f_entry.get("path", ""))


def _is_ts_js(f_entry: Dict[str, Any]) -> bool:
    return f_entry.get("language") in _TS_JS_LANGS or f_entry.get("path", "").endswith(_TS_JS_EXTS)


def build_symbol_graph(manifest: Dict[str, Any]) -> Dict[str, Any]:
    """
    Constructs a symbol dictionary and cross-references caller-callee relationships.
    """
    symbols: Dict[str, Dict[str, Any]] = {}
    name_to_ids: Dict[str, List[str]] = defaultdict(list)
    ranges: Dict[str, Tuple[int, int]] = {}
    local_names: Dict[str, Set[str]] = {}
    method_class: Dict[str, str] = {}
    class_methods: Dict[str, Dict[str, str]] = defaultdict(dict)

    files_data = manifest.get("files", [])

    for f_entry in files_data:
        fpath = f_entry.get("path", "")
        file_syms = f_entry.get("symbols", {})

        # 1. Functions
        for fn in file_syms.get("functions", []):
            fn_name = fn.get("name", "")
            sym_id = f"{fpath}::{fn_name}"
            sig = fn.get("signature") or f"{fn_name}()"
            symbols[sym_id] = {
                "id": sym_id,
                "name": fn_name,
                "kind": "function",
                "path": fpath,
                "line": fn.get("line_start", fn.get("line", 1)),
                "signature": sig,
                "args": fn.get("args", []),
                "return_type": fn.get("return_type"),
                "docstring": fn.get("docstring", ""),
                "callees": fn.get("callees", []),
                "callers": [],
            }
            start = fn.get("line_start", fn.get("line", 1))
            ranges[sym_id] = (start, fn.get("line_end") or start)
            local_names[sym_id] = set(fn.get("local_names") or [])
            name_to_ids[fn_name].append(sym_id)
            if "." in fn_name:
                name_to_ids[fn_name.split(".")[-1]].append(sym_id)

        # 2. Classes and their methods
        for cls in file_syms.get("classes", []):
            cls_name = cls.get("name", "")
            cls_id = f"{fpath}::{cls_name}"
            symbols[cls_id] = {
                "id": cls_id,
                "name": cls_name,
                "kind": "class",
                "path": fpath,
                "line": cls.get("line_start", cls.get("line", 1)),
                "signature": f"class {cls_name}",
                "bases": cls.get("bases", []),
                "docstring": cls.get("docstring", ""),
                "callees": [],
                "callers": [],
            }
            name_to_ids[cls_name].append(cls_id)

            for m in cls.get("methods", []):
                m_name = m.get("name", "")
                m_id = f"{fpath}::{cls_name}.{m_name}"
                sig = m.get("signature") or f"{m_name}()"
                symbols[m_id] = {
                    "id": m_id,
                    "name": f"{cls_name}.{m_name}",
                    "kind": "method",
                    "path": fpath,
                    "line": m.get("line", 1),
                    "signature": sig,
                    "args": m.get("args", []),
                    "return_type": m.get("return_type"),
                    "docstring": m.get("docstring", ""),
                    "callees": m.get("callees", []),
                    "callers": [],
                }
                ranges[m_id] = (m.get("line", 1), m.get("line_end") or m.get("line", 1))
                local_names[m_id] = set(m.get("local_names") or [])
                method_class[m_id] = cls_id
                class_methods[cls_id][m_name] = m_id
                name_to_ids[m_name].append(m_id)
                name_to_ids[f"{cls_name}.{m_name}"].append(m_id)

        # 3. Interfaces and Types
        for item in file_syms.get("interfaces_and_types", []):
            item_name = item.get("name", "")
            item_id = f"{fpath}::{item_name}"
            symbols[item_id] = {
                "id": item_id,
                "name": item_name,
                "kind": item.get("kind", "type"),
                "path": fpath,
                "line": item.get("line", 1),
                "signature": f"{item.get('kind', 'type')} {item_name}",
                "args": [],
                "docstring": "",
                "callees": [],
                "callers": [],
            }
            name_to_ids[item_name].append(item_id)

    # Resolve calls statically: same-file definitions, import bindings, self/cls methods.
    # Anything else (instance calls, builtins, parameters, dynamic chains) stays unknown;
    # the global name index is never used here.
    resolver = _CallResolver(symbols, files_data, class_methods)
    # The regex fallback has no scope data and attributes module-level code to the
    # preceding function, so none of its calls can be resolved safely.
    no_scope_files = {
        f.get("path", "") for f in files_data
        if "regex_fallback" in (f.get("parser"), f.get("symbols", {}).get("parser"))
    }
    stats = {"total_calls": 0, "resolved": 0, "unknown": 0}

    def resolve_calls(caller_id: str, fpath: str, callees: List[str], **scope: Any) -> Tuple[List[str], List[str]]:
        resolved: List[str] = []
        unknown: List[str] = []
        for callee_raw in callees:
            stats["total_calls"] += 1
            target_id = None if fpath in no_scope_files else resolver.resolve(callee_raw, fpath, **scope)
            if target_id is None:
                stats["unknown"] += 1
                unknown.append(callee_raw)
                continue
            stats["resolved"] += 1
            if target_id not in resolved:
                resolved.append(target_id)
            if target_id != caller_id and caller_id not in symbols[target_id]["callers"]:
                symbols[target_id]["callers"].append(caller_id)
        return sorted(resolved), unknown

    for sym_id, sym_data in list(symbols.items()):
        fpath = sym_data["path"]
        class_id = method_class.get(sym_id)
        args = sym_data.get("args") or []
        first_param = args[0].get("name") if args and isinstance(args[0], dict) else None
        sym_data["resolved_callees"], sym_data["unresolved_callees"] = resolve_calls(
            sym_id, fpath, sym_data.get("callees", []),
            class_id=class_id,
            self_name=first_param if class_id and first_param in ("self", "cls") and is_python_path(fpath) else None,
            local_names=local_names.get(sym_id),
            local=resolver.local_bindings_for(fpath, ranges.get(sym_id)),
        )

    # Resolve module-level / file-level calls
    module_calls: Dict[str, Dict[str, Any]] = {}
    for f_entry in files_data:
        fpath = f_entry.get("path", "")
        file_syms = f_entry.get("symbols", {})
        file_callees = file_syms.get("callees", [])
        if file_callees:
            top_id = f"{fpath}::<module>"
            resolved, unknown = resolve_calls(top_id, fpath, file_callees)
            module_calls[top_id] = {
                "path": fpath,
                "callees": list(file_callees),
                "resolved_callees": resolved,
                "unresolved_callees": unknown,
            }

    for sym_data in symbols.values():
        sym_data["callers"].sort()

    return {
        "total_symbols": len(symbols),
        "call_resolution": stats,
        "symbols": symbols,
        "module_calls": module_calls,
        "name_index": dict(name_to_ids),
    }


def export_signatures_markdown(graph: Dict[str, Any]) -> str:
    """Renders a comprehensive signature dictionary in Markdown for AI agent navigation."""
    symbols = graph.get("symbols", {})
    by_file: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    for sym in symbols.values():
        by_file[sym["path"]].append(sym)

    lines = [
        "# Codebase Signatures & Symbol Index",
        "",
        "Quick-reference catalog of function and method signatures, arguments, and return types.",
        "",
        "Callers counts statically resolved callers only. It is a lower bound: calls that static "
        "analysis cannot resolve (instance, dynamic or external calls) are unknown and not counted.",
        "",
    ]
    stats = graph.get("call_resolution")
    if stats:
        lines.append(
            f"Static call resolution: {stats['resolved']} of {stats['total_calls']} calls resolved, "
            f"{stats['unknown']} unknown."
        )
        lines.append("")

    for fpath in sorted(by_file.keys()):
        file_syms = by_file[fpath]
        lines.append(f"## [`{fpath}`](../{fpath})")
        lines.append("")

        classes = [s for s in file_syms if s["kind"] == "class"]
        methods = [s for s in file_syms if s["kind"] == "method"]
        functions = [s for s in file_syms if s["kind"] == "function"]
        types = [s for s in file_syms if s["kind"] not in ("class", "method", "function")]

        if classes:
            lines.append("### Classes")
            for c in classes:
                lines.append(f"- **`{c['signature']}`** (L{c['line']})")
                if c.get("docstring"):
                    lines.append(f"  *{c['docstring'].strip().splitlines()[0]}*")
            lines.append("")

        if functions or methods:
            lines.append("### Functions & Methods")
            lines.append("| Symbol | Signature | Line | Callers |")
            lines.append("| :--- | :--- | :--- | :--- |")
            for fn in functions + methods:
                sig = fn["signature"].replace("|", "\\|")
                callers_count = len(fn.get("callers", []))
                lines.append(f"| `{fn['name']}` | `{sig}` | L{fn['line']} | {callers_count} |")
            lines.append("")

        if types:
            lines.append("### Types & Interfaces")
            for t in types:
                lines.append(f"- `{t['signature']}` (L{t['line']})")
            lines.append("")

    return "\n".join(lines)


def query_symbol(graph: Dict[str, Any], query_str: str) -> List[Dict[str, Any]]:
    """Finds symbols matching query_str exactly or by substring."""
    symbols = graph.get("symbols", {})
    results = []

    q_lower = query_str.lower()
    for sym_id, sym in symbols.items():
        if query_str == sym["name"] or query_str == sym_id:
            # Exact match prioritized
            results.insert(0, sym)
        elif q_lower in sym["name"].lower() or q_lower in sym_id.lower():
            results.append(sym)

    return results


def main():
    parser = argparse.ArgumentParser(description="Query or export Symbol & Call Graph.")
    parser.add_argument("--manifest", "-m", type=str, default=".context_cache/ast_manifest.json", help="Path to AST manifest.")
    parser.add_argument("--save-graph", "-s", type=str, default=None, help="Save symbol graph JSON to path.")
    parser.add_argument("--export-signatures", "-e", type=str, default=None, help="Export signatures to Markdown file.")
    parser.add_argument("--find", "-f", type=str, default=None, help="Search for a symbol by name.")
    parser.add_argument("--callers", "-c", type=str, default=None, help="Find callers of a symbol.")
    parser.add_argument("--callees", type=str, default=None, help="Find callees invoked by a symbol.")

    args = parser.parse_args()
    manifest_path = Path(args.manifest).resolve()

    if not manifest_path.exists():
        print(f"Error: manifest {manifest_path} not found. Run ast_scanner.py first.")
        sys.exit(1)

    manifest_data = load_json(manifest_path)
    graph = build_symbol_graph(manifest_data)

    if args.save_graph:
        out_p = Path(args.save_graph)
        save_json(graph, out_p)
        print(f"Saved symbol graph with {graph['total_symbols']} symbols to {out_p}")

    if args.export_signatures:
        sig_p = Path(args.export_signatures)
        sig_p.parent.mkdir(parents=True, exist_ok=True)
        md_text = export_signatures_markdown(graph)
        sig_p.write_text(md_text, encoding="utf-8")
        print(f"Exported signature documentation to {sig_p}")

    if args.find:
        matches = query_symbol(graph, args.find)
        if not matches:
            print(f"No symbols matching '{args.find}' found.")
        else:
            print(f"Found {len(matches)} matching symbol(s):")
            for m in matches:
                print(f"\n[{m['kind'].upper()}] {m['id']}")
                print(f"  Signature: {m['signature']}")
                print(f"  Location:  {m['path']}:{m['line']}")
                if m.get("docstring"):
                    print(f"  Doc:       {m['docstring'].strip().splitlines()[0]}")
                print(f"  Callers:   {len(m.get('callers', []))} statically resolved caller(s)")
                if m.get("callees"):
                    print(f"  Callees:   {m['callees']}")

    if args.callers:
        matches = query_symbol(graph, args.callers)
        if not matches:
            print(f"Symbol '{args.callers}' not found.")
        else:
            for m in matches:
                print(f"\nCallers for {m['id']}:")
                if not m.get("callers"):
                    print("  (No statically resolved callers; unresolved calls are unknown)")
                for c in m["callers"]:
                    print(f"  <- {c}")

    if args.callees:
        matches = query_symbol(graph, args.callees)
        if not matches:
            print(f"Symbol '{args.callees}' not found.")
        else:
            for m in matches:
                print(f"\nCallees called by {m['id']}:")
                if not m.get("callees"):
                    print("  (No calls detected)")
                for c in m.get("resolved_callees", []):
                    print(f"  -> {c}")
                for c in m.get("unresolved_callees", []):
                    print(f"  -> {c} (unknown: not statically resolved)")


if __name__ == "__main__":
    main()
