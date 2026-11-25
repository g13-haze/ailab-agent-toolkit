#!/usr/bin/env python3
"""
Tree-sitter and AST-based codebase scanner.
Extracts symbols (classes, functions, interfaces, structs, imports, exports)
into a structured JSON manifest to generate AI context without exceeding context windows.
"""

import argparse
import ast
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    from common import (
        DEFAULT_IGNORE_DIRS,
        EXTENSION_TO_LANG,
        discover_source_files,
        get_parser_for_language,
        save_json,
    )
except ImportError:
    from .common import (
        DEFAULT_IGNORE_DIRS,
        EXTENSION_TO_LANG,
        discover_source_files,
        get_parser_for_language,
        save_json,
    )


_SECRET_PARAM_RE = re.compile(
    r"(?:^|_)pass(?:$|_)|pass(?:word|wd|phrase)|pwd|secret|token|api_?key"
    r"|(?:access|private|signing|session)_?key|credential|auth(?!or(?:s|ship|ed|ing)?(?:$|_))"
    r"|dsn|conn(?:ection)?_?str(?:ing)?|cookie|salt",
    re.IGNORECASE,
)
# scheme://user:password@host credentials embedded in a URL
_URL_CREDENTIALS_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://[^\s/:@]+:[^\s/@]+@")
_MAX_DEFAULT_LEN = 40
_MAX_PLAIN_LITERAL_LEN = 32
_REDACTED = "'<redacted>'"


def _is_plain_scalar(node: ast.expr) -> bool:
    """None, True, False or a (signed) number literal."""
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        node = node.operand
    return isinstance(node, ast.Constant) and (
        node.value is None or isinstance(node.value, (bool, int, float, complex))
    )


def _render_default(param_name: str, node: ast.expr) -> str:
    """Renders a default value without leaking secrets.

    Under a secret-looking parameter name only None/True/False/numbers are shown; any
    default containing URL credentials and any str/bytes literal longer than 32
    characters is redacted; other values are truncated beyond 40 characters.
    """
    if _SECRET_PARAM_RE.search(param_name) and not _is_plain_scalar(node):
        return _REDACTED
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
        if len(node.value) > _MAX_PLAIN_LITERAL_LEN:
            return _REDACTED
    try:
        text = ast.unparse(node)
    except Exception:
        return "..."
    if _URL_CREDENTIALS_RE.search(text):
        return _REDACTED
    if len(text) > _MAX_DEFAULT_LEN:
        text = text[:_MAX_DEFAULT_LEN - 3] + "..."
    return text


# Match statement nodes exist only on Python 3.10+
_MATCH_CAPTURE_TYPES = tuple(t for t in (getattr(ast, "MatchAs", None), getattr(ast, "MatchStar", None)) if t)
_MATCH_MAPPING_TYPES = tuple(t for t in (getattr(ast, "MatchMapping", None),) if t)


def python_local_names(fn: ast.AST) -> List[str]:
    """Names bound inside a function (parameters, assignment/for/with/except/walrus and
    comprehension targets, nested def/class names, global/nonlocal and match captures),
    conservatively including nested scopes. Import-bound names are excluded: local
    imports are resolved separately."""
    names: Set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node is not fn:
            names.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names.update(node.names)
        elif isinstance(node, _MATCH_CAPTURE_TYPES) and node.name:
            names.add(node.name)
        elif isinstance(node, _MATCH_MAPPING_TYPES) and node.rest:
            names.add(node.rest)
    return sorted(names)


def python_signature(name: str, fn_args: ast.arguments, returns: Optional[ast.expr]) -> Tuple[str, List[Dict[str, Any]], Optional[str]]:
    """Builds a complete signature: positional-only and /, regular, *args or bare *,
    keyword-only, **kwargs, with annotations and (redacted, truncated) defaults.
    Returns (signature, args, return_annotation)."""
    def anno(a: ast.arg) -> Optional[str]:
        return ast.unparse(a.annotation) if getattr(a, "annotation", None) else None

    args: List[Dict[str, Any]] = []
    parts: List[str] = []

    def add(a: ast.arg, kind: str, default: Optional[ast.expr], prefix: str = "") -> None:
        ann = anno(a)
        dflt = _render_default(a.arg, default) if default is not None else None
        args.append({"name": a.arg, "type": ann, "kind": kind, "default": dflt})
        text = prefix + a.arg + (f": {ann}" if ann else "")
        if dflt is not None:
            text += f" = {dflt}" if ann else f"={dflt}"
        parts.append(text)

    positional = list(fn_args.posonlyargs) + list(fn_args.args)
    defaults: List[Optional[ast.expr]] = [None] * (len(positional) - len(fn_args.defaults)) + list(fn_args.defaults)
    for idx, a in enumerate(positional):
        is_posonly = idx < len(fn_args.posonlyargs)
        add(a, "positional_only" if is_posonly else "positional_or_keyword", defaults[idx])
        if is_posonly and idx == len(fn_args.posonlyargs) - 1:
            parts.append("/")
    if fn_args.vararg:
        add(fn_args.vararg, "var_positional", None, "*")
    elif fn_args.kwonlyargs:
        parts.append("*")
    for a, d in zip(fn_args.kwonlyargs, fn_args.kw_defaults):
        add(a, "keyword_only", d)
    if fn_args.kwarg:
        add(fn_args.kwarg, "var_keyword", None, "**")

    ret_anno = ast.unparse(returns) if returns is not None else None
    sig = f"{name}({', '.join(parts)})" + (f" -> {ret_anno}" if ret_anno else "")
    return sig, args, ret_anno


def _python_import_records(node: ast.stmt, scope: str) -> List[Dict[str, Any]]:
    """One record per `import` alias, or one per `from ... import ...` statement."""
    if isinstance(node, ast.Import):
        return [
            {
                "source": alias.name,
                "module": "",
                "level": 0,
                "names": [{"name": alias.name, "alias": alias.asname}],
                "symbols": [alias.asname or alias.name],
                "raw": ast.unparse(ast.Import(names=[alias])),
                "line": node.lineno,
                "scope": scope,
            }
            for alias in node.names
        ]
    level = node.level or 0
    module = node.module or ""
    return [{
        "source": "." * level + module,
        "module": module,
        "level": level,
        "names": [{"name": a.name, "alias": a.asname} for a in node.names],
        "symbols": [a.asname or a.name for a in node.names],
        "raw": ast.unparse(node),
        "line": node.lineno,
        "scope": scope,
    }]


def collect_python_imports(tree: ast.AST) -> List[Dict[str, Any]]:
    """Every Import/ImportFrom in the module; scope is "local" inside a function or class body."""
    records: List[Tuple[int, int, Dict[str, Any]]] = []
    stack: List[Tuple[ast.AST, bool]] = [(tree, False)]
    while stack:
        node, local = stack.pop()
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.Import, ast.ImportFrom)):
                for rec in _python_import_records(child, "local" if local else "module"):
                    records.append((child.lineno, child.col_offset, rec))
            elif isinstance(child, ast.stmt) or isinstance(child, ast.excepthandler) or hasattr(child, "body"):
                child_local = local or isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                stack.append((child, child_local))
    records.sort(key=lambda r: (r[0], r[1]))
    return [r[2] for r in records]


def extract_python_ast(code_bytes: bytes, file_path: Path) -> Dict[str, Any]:
    """Extracts symbols from Python code using the standard library ast module."""
    symbols: Dict[str, Any] = {
        "classes": [],
        "functions": [],
        "interfaces_and_types": [],
        "imports": [],
        "exports": [],
        "callees": [],
    }
    try:
        source_text = code_bytes.decode("utf-8")
        parsed = ast.parse(source_text, filename=str(file_path))
    except Exception as e:
        symbols["parse_error"] = str(e)
        return symbols

    # Check for __all__
    for node in parsed.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    if isinstance(node.value, (ast.List, ast.Tuple)):
                        for elt in node.value.elts:
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                symbols["exports"].append(elt.value)

    # Imports anywhere in the module (function-local, try/if blocks included)
    symbols["imports"] = collect_python_imports(parsed)

    for node in parsed.body:
        # Classes
        if isinstance(node, ast.ClassDef):
            bases = []
            for b in node.bases:
                if isinstance(b, ast.Name):
                    bases.append(b.id)
                elif isinstance(b, ast.Attribute):
                    bases.append(ast.unparse(b))
                else:
                    bases.append("...")

            methods = []
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    sig, m_args, ret_anno = python_signature(item.name, item.args, getattr(item, "returns", None))

                    calls = []
                    for c_node in ast.walk(item):
                        if isinstance(c_node, ast.Call):
                            try:
                                calls.append(ast.unparse(c_node.func))
                            except Exception:
                                pass

                    methods.append({
                        "name": item.name,
                        "signature": sig,
                        "args": m_args,
                        "return_type": ret_anno,
                        "callees": sorted(list(set(calls))),
                        "local_names": python_local_names(item),
                        "is_async": isinstance(item, ast.AsyncFunctionDef),
                        "docstring": ast.get_docstring(item) or "",
                        "line": item.lineno,
                        "line_end": getattr(item, "end_lineno", item.lineno),
                    })

            symbols["classes"].append({
                "name": node.name,
                "bases": bases,
                "docstring": ast.get_docstring(node) or "",
                "line_start": node.lineno,
                "line_end": getattr(node, "end_lineno", node.lineno),
                "methods": methods,
            })

        # Functions
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            sig, f_args, ret_anno = python_signature(node.name, node.args, getattr(node, "returns", None))

            calls = []
            for c_node in ast.walk(node):
                if isinstance(c_node, ast.Call):
                    try:
                        calls.append(ast.unparse(c_node.func))
                    except Exception:
                        pass

            symbols["functions"].append({
                "name": node.name,
                "signature": sig,
                "args": f_args,
                "return_type": ret_anno,
                "callees": sorted(list(set(calls))),
                "local_names": python_local_names(node),
                "is_async": isinstance(node, ast.AsyncFunctionDef),
                "docstring": ast.get_docstring(node) or "",
                "line_start": node.lineno,
                "line_end": getattr(node, "end_lineno", node.lineno),
            })

        # Type aliases (TypeVar, NewType, etc.)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    symbols["interfaces_and_types"].append({
                        "name": target.id,
                        "kind": "type_alias",
                        "line": node.lineno,
                    })

    # Module-level calls (calls outside functions/classes)
    mod_calls = []
    for node in parsed.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for c_node in ast.walk(node):
                if isinstance(c_node, ast.Call):
                    try:
                        mod_calls.append(ast.unparse(c_node.func))
                    except Exception:
                        pass
    symbols["callees"] = sorted(list(set(mod_calls)))

    return symbols


def extract_node_text(node: Any, code_bytes: bytes) -> str:
    """Safely extracts UTF-8 string for a Tree-sitter node."""
    return code_bytes[node.start_byte:node.end_byte].decode("utf-8", "replace").strip()


def _ts_pattern_names(node: Any, code_bytes: bytes, out: Set[str]) -> None:
    """Identifiers bound by a TS/JS binding pattern (identifier or destructuring)."""
    ntype = node.type
    if ntype in ("identifier", "shorthand_property_identifier_pattern"):
        out.add(extract_node_text(node, code_bytes))
    elif ntype == "pair_pattern":
        value = node.child_by_field_name("value")
        if value is not None:
            _ts_pattern_names(value, code_bytes, out)
    elif ntype in ("assignment_pattern", "object_assignment_pattern"):
        left = node.child_by_field_name("left")
        if left is not None:
            _ts_pattern_names(left, code_bytes, out)
    elif ntype in ("object_pattern", "array_pattern", "rest_pattern"):
        for child in node.named_children:
            _ts_pattern_names(child, code_bytes, out)


_TS_PATTERN_FIELDS = {
    "required_parameter": "pattern",
    "optional_parameter": "pattern",
    "arrow_function": "parameter",
    "variable_declarator": "name",
    "for_in_statement": "left",
    "catch_clause": "parameter",
}
_TS_NAMED_SCOPES = {
    "function_declaration", "generator_function_declaration", "function_expression",
    "class_declaration", "abstract_class_declaration", "class",
}


def ts_local_names(fn_node: Any, code_bytes: bytes) -> List[str]:
    """Names bound inside a TS/JS function: parameters (incl. destructuring), declarators,
    for-in/of targets, catch parameters and nested function/class names, conservatively
    including nested scopes."""
    names: Set[str] = set()
    stack = [fn_node]
    while stack:
        node = stack.pop()
        field = _TS_PATTERN_FIELDS.get(node.type)
        if field:
            target = node.child_by_field_name(field)
            if target is not None:
                _ts_pattern_names(target, code_bytes, names)
        elif node.type in _TS_NAMED_SCOPES and node is not fn_node:
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                names.add(extract_node_text(name_node, code_bytes))
        stack.extend(node.children)
    return sorted(names)


def _ts_import_names(node: Any, code_bytes: bytes) -> List[Dict[str, Optional[str]]]:
    """Bound names of a TS/JS import_statement: default ("default" -> local), named
    imports with aliases, and namespace imports ("*" -> local)."""
    names: List[Dict[str, Optional[str]]] = []
    for clause in node.children:
        if clause.type != "import_clause":
            continue
        for child in clause.children:
            if child.type == "identifier":
                names.append({"name": "default", "alias": extract_node_text(child, code_bytes)})
            elif child.type == "namespace_import":
                ids = [c for c in child.children if c.type == "identifier"]
                if ids:
                    names.append({"name": "*", "alias": extract_node_text(ids[-1], code_bytes)})
            elif child.type == "named_imports":
                for spec in child.children:
                    if spec.type != "import_specifier":
                        continue
                    name_node = spec.child_by_field_name("name")
                    alias_node = spec.child_by_field_name("alias")
                    if name_node is None:
                        continue
                    names.append({
                        "name": extract_node_text(name_node, code_bytes),
                        "alias": extract_node_text(alias_node, code_bytes) if alias_node else None,
                    })
    return names


def extract_tree_sitter_symbols(
    parser: Any,
    code_bytes: bytes,
    lang: str,
    file_path: Path
) -> Dict[str, Any]:
    """Extracts symbols using a Tree-sitter AST parser."""
    symbols: Dict[str, Any] = {
        "classes": [],
        "functions": [],
        "interfaces_and_types": [],
        "imports": [],
        "exports": [],
        "callees": [],
    }

    try:
        tree = parser.parse(code_bytes)
    except Exception as e:
        symbols["parse_error"] = str(e)
        return symbols

    root = tree.root_node

    def find_callees(node: Any) -> List[str]:
        calls = []
        def _rec(n):
            if n.type in ("call_expression", "method_invocation", "object_creation_expression"):
                fn_node = (
                    n.child_by_field_name("function")
                    or n.child_by_field_name("name")
                    or n.child_by_field_name("type")
                )
                if fn_node:
                    calls.append(extract_node_text(fn_node, code_bytes))
            for c in n.children:
                _rec(c)
        _rec(node)
        return sorted(list(set(calls)))

    def walk(node: Any):
        ntype = node.type

        # TypeScript / JavaScript / TSX
        if lang in ("typescript", "tsx", "javascript"):
            if ntype in ("class_declaration", "abstract_class_declaration"):
                name_node = node.child_by_field_name("name")
                name = extract_node_text(name_node, code_bytes) if name_node else "Anonymous"
                symbols["classes"].append({
                    "name": name,
                    "line_start": node.start_point.row + 1,
                    "line_end": node.end_point.row + 1,
                })
            elif ntype in ("interface_declaration", "type_alias_declaration", "enum_declaration"):
                name_node = node.child_by_field_name("name")
                name = extract_node_text(name_node, code_bytes) if name_node else "Anonymous"
                symbols["interfaces_and_types"].append({
                    "name": name,
                    "kind": ntype.replace("_declaration", ""),
                    "line": node.start_point.row + 1,
                })
            elif ntype in ("function_declaration", "generator_function_declaration"):
                name_node = node.child_by_field_name("name")
                name = extract_node_text(name_node, code_bytes) if name_node else "Anonymous"
                params_node = node.child_by_field_name("parameters")
                params = extract_node_text(params_node, code_bytes) if params_node else "()"
                ret_node = node.child_by_field_name("return_type")
                ret = extract_node_text(ret_node, code_bytes) if ret_node else ""
                sig = f"{name}{params}" + (f" {ret}" if ret else "")
                callees = find_callees(node)

                symbols["functions"].append({
                    "name": name,
                    "signature": sig,
                    "callees": callees,
                    "local_names": ts_local_names(node, code_bytes),
                    "line_start": node.start_point.row + 1,
                    "line_end": node.end_point.row + 1,
                })
            elif ntype == "method_definition":
                name_node = node.child_by_field_name("name")
                name = extract_node_text(name_node, code_bytes) if name_node else "Anonymous"
                params_node = node.child_by_field_name("parameters")
                params = extract_node_text(params_node, code_bytes) if params_node else "()"
                ret_node = node.child_by_field_name("return_type")
                ret = extract_node_text(ret_node, code_bytes) if ret_node else ""
                sig = f"{name}{params}" + (f" {ret}" if ret else "")
                callees = find_callees(node)

                symbols["functions"].append({
                    "name": name,
                    "signature": sig,
                    "callees": callees,
                    "local_names": ts_local_names(node, code_bytes),
                    "line_start": node.start_point.row + 1,
                    "line_end": node.end_point.row + 1,
                })
            elif ntype == "variable_declarator":
                val_node = node.child_by_field_name("value")
                if val_node and val_node.type in ("arrow_function", "function_expression"):
                    name_node = node.child_by_field_name("name")
                    name = extract_node_text(name_node, code_bytes) if name_node else "Anonymous"
                    params_node = val_node.child_by_field_name("parameters")
                    params = extract_node_text(params_node, code_bytes) if params_node else "()"
                    ret_node = val_node.child_by_field_name("return_type")
                    ret = extract_node_text(ret_node, code_bytes) if ret_node else ""
                    sig = f"{name}{params}" + (f" {ret}" if ret else "")
                    callees = find_callees(val_node)

                    symbols["functions"].append({
                        "name": name,
                        "signature": sig,
                        "callees": callees,
                        "local_names": ts_local_names(val_node, code_bytes),
                        "line_start": node.start_point.row + 1,
                        "line_end": node.end_point.row + 1,
                    })
            elif ntype == "import_statement":
                raw = extract_node_text(node, code_bytes)
                source_node = node.child_by_field_name("source")
                source = extract_node_text(source_node, code_bytes).strip("\"'") if source_node else ""
                names = _ts_import_names(node, code_bytes)
                symbols["imports"].append({
                    "source": source,
                    "raw": raw,
                    "names": names,
                    "symbols": [n["alias"] or n["name"] for n in names],
                    "line": node.start_point.row + 1,
                })
            elif ntype == "export_statement":
                decl = node.child_by_field_name("declaration")
                if decl:
                    walk(decl)
                else:
                    symbols["exports"].append(extract_node_text(node, code_bytes))
                return

        # Go
        elif lang == "go":
            if ntype == "type_declaration":
                raw = extract_node_text(node, code_bytes)
                symbols["interfaces_and_types"].append({
                    "name": raw.split("\n")[0],
                    "kind": "go_type",
                    "line": node.start_point.row + 1,
                })
            elif ntype in ("function_declaration", "method_declaration"):
                name_node = node.child_by_field_name("name")
                name = extract_node_text(name_node, code_bytes) if name_node else "Anonymous"
                params_node = node.child_by_field_name("parameters")
                params = extract_node_text(params_node, code_bytes) if params_node else "()"
                result_node = node.child_by_field_name("result")
                ret = extract_node_text(result_node, code_bytes) if result_node else ""
                sig = f"func {name}{params}" + (f" {ret}" if ret else "")
                callees = find_callees(node)

                symbols["functions"].append({
                    "name": name,
                    "signature": sig,
                    "callees": callees,
                    "line_start": node.start_point.row + 1,
                    "line_end": node.end_point.row + 1,
                })
            elif ntype == "import_declaration":
                symbols["imports"].append({
                    "raw": extract_node_text(node, code_bytes),
                    "source": extract_node_text(node, code_bytes),
                })

        # Rust
        elif lang == "rust":
            if ntype in ("struct_item", "enum_item", "union_item"):
                name_node = node.child_by_field_name("name")
                name = extract_node_text(name_node, code_bytes) if name_node else "Anonymous"
                symbols["classes"].append({
                    "name": name,
                    "kind": ntype.replace("_item", ""),
                    "line_start": node.start_point.row + 1,
                    "line_end": node.end_point.row + 1,
                })
            elif ntype == "trait_item":
                name_node = node.child_by_field_name("name")
                name = extract_node_text(name_node, code_bytes) if name_node else "Anonymous"
                symbols["interfaces_and_types"].append({
                    "name": name,
                    "kind": "trait",
                    "line": node.start_point.row + 1,
                })
            elif ntype == "function_item":
                name_node = node.child_by_field_name("name")
                name = extract_node_text(name_node, code_bytes) if name_node else "Anonymous"
                params_node = node.child_by_field_name("parameters")
                params = extract_node_text(params_node, code_bytes) if params_node else "()"
                ret_node = node.child_by_field_name("return_type")
                ret = extract_node_text(ret_node, code_bytes) if ret_node else ""
                sig = f"fn {name}{params}" + (f" {ret}" if ret else "")
                callees = find_callees(node)

                symbols["functions"].append({
                    "name": name,
                    "signature": sig,
                    "callees": callees,
                    "line_start": node.start_point.row + 1,
                    "line_end": node.end_point.row + 1,
                })
            elif ntype == "use_declaration":
                symbols["imports"].append({
                    "raw": extract_node_text(node, code_bytes),
                    "source": extract_node_text(node, code_bytes),
                })

        # Java
        elif lang == "java":
            if ntype in ("class_declaration", "record_declaration"):
                name_node = node.child_by_field_name("name")
                name = extract_node_text(name_node, code_bytes) if name_node else "Anonymous"
                symbols["classes"].append({
                    "name": name,
                    "line_start": node.start_point.row + 1,
                    "line_end": node.end_point.row + 1,
                })
            elif ntype in ("interface_declaration", "enum_declaration", "annotation_type_declaration"):
                name_node = node.child_by_field_name("name")
                name = extract_node_text(name_node, code_bytes) if name_node else "Anonymous"
                symbols["interfaces_and_types"].append({
                    "name": name,
                    "kind": ntype.replace("_declaration", ""),
                    "line": node.start_point.row + 1,
                })
            elif ntype == "method_declaration":
                name_node = node.child_by_field_name("name")
                name = extract_node_text(name_node, code_bytes) if name_node else "Anonymous"
                params_node = node.child_by_field_name("parameters")
                params = extract_node_text(params_node, code_bytes) if params_node else "()"
                type_node = node.child_by_field_name("type")
                ret = extract_node_text(type_node, code_bytes) if type_node else ""
                sig = f"{name}{params}" + (f" -> {ret}" if ret else "")
                callees = find_callees(node)
                symbols["functions"].append({
                    "name": name,
                    "signature": sig,
                    "callees": callees,
                    "line_start": node.start_point.row + 1,
                    "line_end": node.end_point.row + 1,
                })
            elif ntype == "import_declaration":
                symbols["imports"].append({
                    "raw": extract_node_text(node, code_bytes),
                    "source": extract_node_text(node, code_bytes),
                })

        # Recurse child nodes
        for child in node.children:
            walk(child)

    walk(root)
    return symbols


def extract_regex_symbols(code_bytes: bytes, lang: str) -> Dict[str, Any]:
    """Lightweight fallback symbol extraction using regex heuristics."""
    symbols: Dict[str, Any] = {
        "classes": [],
        "functions": [],
        "interfaces_and_types": [],
        "imports": [],
        "exports": [],
        "callees": [],
    }
    text = code_bytes.decode("utf-8", "replace")
    lines = text.split("\n")

    call_keywords = {
        "if", "for", "while", "switch", "catch", "function", "class", "constructor",
        "import", "export", "return", "throw", "typeof", "super", "new", "require",
        "else", "try", "finally", "with", "yield", "await", "delete", "void", "in",
        "instanceof", "type", "interface", "get", "set", "from", "as", "default", "case",
        "let", "const", "var"
    }
    call_re = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")

    def extract_calls(code_text: str, current_name: Optional[str] = None) -> List[str]:
        cleaned = re.sub(r"\"[^\"]*\"|\'[^\']*\'|\`[^\`]*\`|//.*|/\*[\s\S]*?\*/|#.*", "", code_text)
        raw = call_re.findall(cleaned)
        return sorted(list(set(c for c in raw if c not in call_keywords and c != current_name)))

    class_re = re.compile(r"^\s*(?:export\s+)?(?:(?:public|private|protected|abstract|sealed|final|declare|static)\s+)*(class|struct|trait)\s+([A-Za-z0-9_]+)")
    type_re = re.compile(r"^\s*(?:export\s+)?(?:(?:public|declare)\s+)*(interface|type|enum)\s+([A-Za-z0-9_]+)")
    func_re = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function|def|fn|func|sub)\s+([A-Za-z0-9_]+)\s*(\([^)]*\))?(?:\s*(?:->|:)\s*([^{:\n]+))?")
    arrow_re = re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z0-9_]+)\s*=\s*(?:async\s*)?(?:(\([^)]*\)|[A-Za-z0-9_]+))\s*(?::\s*([^{=\n]+))?\s*=>")
    method_re = re.compile(r"^\s*(?:(?:public|private|protected|static|async|override|readonly|abstract)\s+)+([A-Za-z0-9_]+)\s*(\([^)]*\))(?:\s*(?:->|:)\s*([^{:\n]+))?")
    bare_method_re = re.compile(r"^\s*(?:async\s+)?([A-Za-z0-9_]+)\s*(\([^)]*\))(?:\s*(?:->|:)\s*([^{:\n]+))?\s*\{")
    import_re = re.compile(r"^\s*(?:import\s+|from\s+|use\s+|require\()([^;\n]+)")

    # 1. Track class scopes via brace tracking
    active_class: Optional[Dict[str, Any]] = None
    brace_depth = 0

    for idx, line in enumerate(lines, 1):
        cleaned = re.sub(r"\"[^\"]*\"|\'[^\']*\'|\`[^\`]*\`|//.*|/\*[\s\S]*?\*/|#.*", "", line)
        if not active_class:
            cm = class_re.search(line)
            if cm:
                c_name = cm.group(2)
                c_kind = cm.group(1)
                active_class = {
                    "name": c_name,
                    "kind": c_kind,
                    "line_start": idx,
                    "methods": [],
                }
                brace_depth = cleaned.count("{") - cleaned.count("}")
                symbols["classes"].append(active_class)
        else:
            brace_depth += cleaned.count("{") - cleaned.count("}")
            if brace_depth <= 0:
                active_class["line_end"] = idx
                active_class = None

    if active_class:
        active_class["line_end"] = len(lines)

    # 2. Extract types, imports, and collect all callable declarations
    callable_decls = []

    for idx, line in enumerate(lines, 1):
        i_match = import_re.search(line)
        if i_match:
            symbols["imports"].append({"raw": line.strip(), "source": i_match.group(1).strip()})

        t_match = type_re.search(line)
        if t_match:
            symbols["interfaces_and_types"].append({
                "name": t_match.group(2),
                "kind": t_match.group(1),
                "line": idx,
            })
            continue

        if any(c["line_start"] == idx for c in symbols["classes"]):
            continue

        in_class = any(c["line_start"] <= idx <= c.get("line_end", len(lines)) for c in symbols["classes"])

        m_match = method_re.search(line)
        if m_match and m_match.group(1) not in call_keywords:
            m_name = m_match.group(1)
            args = m_match.group(2) or "()"
            ret = f" -> {m_match.group(3).strip()}" if m_match.group(3) else ""
            sig = f"{m_name}{args}{ret}"
            callable_decls.append((idx, "method" if in_class else "func", m_name, sig))
            continue

        if in_class:
            bm_match = bare_method_re.search(line)
            if bm_match and bm_match.group(1) not in call_keywords:
                m_name = bm_match.group(1)
                args = bm_match.group(2) or "()"
                ret = f" -> {bm_match.group(3).strip()}" if bm_match.group(3) else ""
                sig = f"{m_name}{args}{ret}"
                callable_decls.append((idx, "method", m_name, sig))
                continue

        f_match = func_re.search(line)
        if f_match and f_match.group(1) not in call_keywords:
            f_name = f_match.group(1)
            args = f_match.group(2) or "()"
            ret = f" -> {f_match.group(3).strip()}" if f_match.group(3) else ""
            sig = f"{f_name}{args}{ret}"
            callable_decls.append((idx, "method" if in_class else "func", f_name, sig))
            continue

        a_match = arrow_re.search(line)
        if a_match and a_match.group(1) not in call_keywords:
            a_name = a_match.group(1)
            args = a_match.group(2) if a_match.group(2).startswith("(") else f"({a_match.group(2)})"
            ret = f" -> {a_match.group(3).strip()}" if a_match.group(3) else ""
            sig = f"{a_name}{args}{ret}"
            callable_decls.append((idx, "func", a_name, sig))
            continue

    # 3. Compute function/method bodies and callees
    callable_decls.sort(key=lambda d: d[0])
    covered_lines = set()

    for i, (line_num, kind, name, sig) in enumerate(callable_decls):
        next_line = callable_decls[i + 1][0] if i + 1 < len(callable_decls) else len(lines) + 1
        body_lines = lines[line_num - 1:next_line - 1]
        for l_idx in range(line_num, next_line):
            covered_lines.add(l_idx)
        body_text = "\n".join(body_lines)
        callees = extract_calls(body_text, current_name=name)

        parent_class = None
        for c in symbols["classes"]:
            if c["line_start"] <= line_num <= c.get("line_end", len(lines)):
                parent_class = c
                break

        if kind == "method" and parent_class:
            parent_class["methods"].append({
                "name": name,
                "signature": sig,
                "line": line_num,
                "callees": callees,
            })
        else:
            symbols["functions"].append({
                "name": name,
                "signature": sig,
                "line_start": line_num,
                "callees": callees,
            })

    uncovered_lines = [line for idx, line in enumerate(lines, 1) if idx not in covered_lines]
    if uncovered_lines:
        symbols["callees"] = extract_calls("\n".join(uncovered_lines))

    return symbols


def scan_file(file_path: Path, root_path: Path, raw_bytes: Optional[bytes] = None) -> Dict[str, Any]:
    """Analyzes a single source file, extracting AST symbols and metadata."""
    rel_path = file_path.relative_to(root_path).as_posix()
    ext = file_path.suffix.lower()
    lang = EXTENSION_TO_LANG.get(ext, "unknown")

    try:
        if raw_bytes is None:
            raw_bytes = file_path.read_bytes()
    except Exception as e:
        return {
            "path": rel_path,
            "language": lang,
            "error": f"Failed to read file: {e}",
            "lines": 0,
            "size_bytes": 0,
        }

    line_count = raw_bytes.count(b"\n") + (1 if raw_bytes else 0)

    # 1. Native Python AST parsing
    if lang == "python":
        symbols = extract_python_ast(raw_bytes, file_path)
        symbols["parser"] = "python_ast"

    # 2. Tree-sitter parsing
    else:
        parser = get_parser_for_language(lang)
        if parser:
            symbols = extract_tree_sitter_symbols(parser, raw_bytes, lang, file_path)
            symbols["parser"] = f"tree_sitter_{lang}"
        else:
            symbols = extract_regex_symbols(raw_bytes, lang)
            symbols["parser"] = "regex_fallback"

    return {
        "path": rel_path,
        "language": lang,
        "extension": ext,
        "lines": line_count,
        "size_bytes": len(raw_bytes),
        "parser": symbols.get("parser"),
        "symbols": symbols,
    }


MINIFIED_MAX_LINE = 5000
MINIFIED_AVG_LINE = 300
MINIFIED_MIN_BYTES = 1024
# Only JavaScript-family files are checked; Python and TypeScript sources are never skipped
MINIFIED_CHECK_EXTS = {".js", ".mjs", ".cjs", ".jsx"}


def looks_minified(raw_bytes: bytes) -> bool:
    """Heuristic for generated/minified sources: one very long line, or long lines on average."""
    if not raw_bytes:
        return False
    lines = raw_bytes.split(b"\n")
    if max(len(line) for line in lines) > MINIFIED_MAX_LINE:
        return True
    return len(raw_bytes) >= MINIFIED_MIN_BYTES and len(raw_bytes) / len(lines) > MINIFIED_AVG_LINE


def scan_codebase(
    root_path: Path,
    extra_ignores: Optional[Set[str]] = None,
    allowed_extensions: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    """Scans the entire repository and returns a structured AST manifest."""
    root = root_path.resolve()
    discovery: Dict[str, Any] = {}
    files = discover_source_files(
        root, extra_ignores=extra_ignores, allowed_extensions=allowed_extensions, stats=discovery
    )
    discovery.setdefault("method", "walk")
    discovery.setdefault("excluded", {})
    skipped_minified: List[str] = []

    file_results: List[Dict[str, Any]] = []
    lang_stats: Dict[str, int] = {}
    total_lines = 0
    total_symbols = 0

    for f in files:
        raw: Optional[bytes] = None
        if f.suffix.lower() in MINIFIED_CHECK_EXTS:
            try:
                raw = f.read_bytes()
            except OSError:
                raw = None
            if raw is not None and looks_minified(raw):
                skipped_minified.append(f.relative_to(root).as_posix())
                continue
        res = scan_file(f, root, raw)
        file_results.append(res)

        lang = res.get("language", "unknown")
        lang_stats[lang] = lang_stats.get(lang, 0) + 1
        total_lines += res.get("lines", 0)

        syms = res.get("symbols", {})
        s_count = (
            len(syms.get("classes", []))
            + len(syms.get("functions", []))
            + len(syms.get("interfaces_and_types", []))
        )
        total_symbols += s_count

    if skipped_minified:
        discovery["excluded"]["minified_content"] = len(skipped_minified)
        discovery["excluded"] = dict(sorted(discovery["excluded"].items()))
    discovery["skipped_minified"] = skipped_minified

    return {
        "root": str(root),
        "total_files": len(file_results),
        "total_lines": total_lines,
        "total_symbols": total_symbols,
        "languages": lang_stats,
        "discovery": discovery,
        "files": file_results,
    }


def main():
    parser = argparse.ArgumentParser(description="Tree-sitter and AST symbol extractor for codebase context.")
    parser.add_argument("--root", "-r", type=str, default=".", help="Root path of target codebase.")
    parser.add_argument("--out", "-o", type=str, default=None, help="Output JSON path (default: .context_cache/ast_manifest.json).")
    parser.add_argument("--summary", action="store_true", help="Print summary to stdout instead of full JSON.")

    args = parser.parse_args()
    root_path = Path(args.root).resolve()

    manifest = scan_codebase(root_path)

    out_file = Path(args.out) if args.out else (root_path / ".context_cache" / "ast_manifest.json")
    save_json(manifest, out_file)

    if args.summary:
        print(f"Codebase Scan Summary for: {root_path}")
        print(f"Total Files Scanned: {manifest['total_files']}")
        print(f"Total Lines of Code: {manifest['total_lines']}")
        print(f"Total Symbols Extracted: {manifest['total_symbols']}")
        print(f"Languages: {manifest['languages']}")
        disc = manifest.get("discovery", {})
        print(f"Discovery: method={disc.get('method')} excluded={disc.get('excluded', {})}")
        print(f"Manifest saved to: {out_file}")
    else:
        print(f"Wrote AST manifest with {manifest['total_files']} files to {out_file}")


if __name__ == "__main__":
    main()
