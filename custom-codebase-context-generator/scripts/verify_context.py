#!/usr/bin/env python3
"""
Context & AGENTS.md Quality and Integrity Verifier.
Verifies that generated AI context:
1. Conforms to required sections and structural standards
2. Has no broken local markdown links or missing target files
3. Accurately covers key modules and entry points identified during AST scanning
"""

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    from common import load_json
except ImportError:
    from .common import load_json

REQUIRED_AGENTS_SECTIONS: List[Tuple[str, List[str]]] = [
    ("Project Overview", ["overview", "project summary", "about this project", "purpose"]),
    ("Commands & Workflow", ["build", "test", "verification", "run", "commands", "setup"]),
    ("Architecture & Context", ["architecture", "context", "structure", "navigation"]),
    ("Agent Rules & Conventions", ["rules", "conventions", "guardrails", "guidelines", "do's and don'ts"]),
]

# Sections that may also be satisfied by a dedicated Markdown heading with this exact word (for example the overview
# of a project written under "## Product"). A heading is required: the word in body text does not count.
HEADING_SYNONYMS: Dict[str, List[str]] = {
    "Project Overview": ["product", "introduction"],
}

_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_INLINE_CODE_RE = re.compile(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)")


def strip_code_blocks(content: str) -> Tuple[str, Optional[int]]:
    """Blanks fenced code blocks and single-line inline code spans, where Markdown renders no links or headings.

    Returns the blanked text and the 1-based line of a fence still open at the end of the file (None if every fence
    closes). Text is replaced by spaces of the same length, so a link whose text is a code span
    (``[`path`](../path)``) keeps its brackets and is still checked. As in CommonMark, a backtick run followed by
    text containing another backtick is inline code, not a fence; a closing fence may be indented (list items).
    """
    out: List[str] = []
    fence: Optional[str] = None
    opened_at: Optional[int] = None
    fence_indent = 0
    for number, line in enumerate(content.split("\n"), 1):
        indent = len(line) - len(line.lstrip(" "))
        if fence is None:
            m = _FENCE_RE.match(line)
            if m and not (m.group(1)[0] == "`" and "`" in line.strip()[len(m.group(1)):]):
                fence, opened_at, fence_indent = m.group(1), number, indent
                out.append("")
                continue
            out.append(_INLINE_CODE_RE.sub(lambda s: " " * len(s.group(0)), line))
            continue
        # A closing fence may be indented up to 3 spaces beyond its opening fence (fences inside list items).
        run = re.match(r"(`+|~+)\s*$", line.strip())
        if run and indent <= fence_indent + 3 and run.group(1)[0] == fence[0] and len(run.group(1)) >= len(fence):
            fence, opened_at = None, None
        out.append("")
    return "\n".join(out), opened_at


def strip_code(content: str) -> str:
    """``strip_code_blocks`` without the unclosed-fence position."""
    return strip_code_blocks(content)[0]


EXPECTED_CONTEXT_FILES: List[str] = [
    "index.md",
    "architecture.md",
    "modules.md",
    "signatures.md",
    "dependencies.md",
    "conventions.md",
]


def check_agents_md(agents_path: Path) -> Tuple[List[str], List[str]]:
    """Validates AGENTS.md file exists and contains essential sections."""
    errors: List[str] = []
    warnings: List[str] = []

    if not agents_path.exists():
        errors.append(f"Missing {agents_path.name} at root.")
        return errors, warnings

    content = agents_path.read_text(encoding="utf-8")
    lines = content.split("\n")

    # Check for placeholder text that indicates incomplete generation
    placeholders = re.findall(r"(<insert[^>]*>|TODO:|\[TBD\]|<replace[^>]*>)", content, re.IGNORECASE)
    if placeholders:
        warnings.append(f"AGENTS.md contains unresolved placeholders: {placeholders[:3]}")

    lower_content = content.lower()
    prose = strip_code(content)
    for section_name, synonyms in REQUIRED_AGENTS_SECTIONS:
        found = any(syn in lower_content for syn in synonyms)
        if not found:
            found = any(re.search(rf"^#{{1,6}}[ \t]+{re.escape(word)}[ \t]*#*[ \t]*$", prose, re.IGNORECASE | re.MULTILINE)
                        for word in HEADING_SYNONYMS.get(section_name, []))
        if not found:
            errors.append(f"AGENTS.md missing essential section: '{section_name}' (expected keywords: {synonyms})")

    # Check for code blocks with executable commands
    code_blocks = re.findall(r"```(?:bash|sh|zsh)?\n(.*?)\n```", content, re.DOTALL)
    if not code_blocks:
        warnings.append("AGENTS.md has no bash/sh code blocks with build/test commands.")

    return errors, warnings


def check_context_dir(context_dir: Path) -> Tuple[List[str], List[str]]:
    """Validates context directory exists and contains expected files."""
    errors: List[str] = []
    warnings: List[str] = []

    if not context_dir.exists() or not context_dir.is_dir():
        errors.append(f"Context directory '{context_dir}' does not exist.")
        return errors, warnings

    existing_files = {p.name for p in context_dir.glob("*.md")}

    for expected in EXPECTED_CONTEXT_FILES:
        if expected not in existing_files:
            # overview.md is acceptable in place of index.md
            if expected == "index.md" and "overview.md" in existing_files:
                continue
            warnings.append(f"Expected file 'context/{expected}' not found.")

    return errors, warnings


def check_markdown_links(base_dir: Path, md_files: List[Path]) -> Tuple[List[str], List[str]]:
    """Validates that all local relative links inside markdown files resolve to real files."""
    errors: List[str] = []
    warnings: List[str] = []

    link_pattern = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")

    for md_file in md_files:
        content, unclosed_at = strip_code_blocks(md_file.read_text(encoding="utf-8"))
        if unclosed_at is not None:
            warnings.append(f"Unclosed code fence in '{md_file.name}' at line {unclosed_at}: links after it are not "
                            "checked.")
        for match in link_pattern.finditer(content):
            _, raw_link = match.groups()
            raw_link = raw_link.strip()

            # Skip remote URLs or anchors
            if raw_link.startswith(("http://", "https://", "mailto:", "ftp://")):
                continue
            if raw_link.startswith("#"):
                continue

            # Strip anchor fragment
            path_part = raw_link.split("#")[0]
            if not path_part:
                continue

            # Resolve relative to the markdown file's directory
            target_path = (md_file.parent / path_part).resolve()
            if not target_path.exists():
                # Also try relative to project root
                root_target = (base_dir / path_part).resolve()
                if not root_target.exists():
                    errors.append(f"Broken link in '{md_file.name}': target '{raw_link}' not found on disk.")

    return errors, warnings


def check_coverage(manifest_path: Path, context_dir: Path) -> List[str]:
    """Checks that key modules from manifest are mentioned in context documentation."""
    warnings: List[str] = []
    if not manifest_path.exists():
        return warnings

    try:
        manifest = load_json(manifest_path)
    except Exception:
        return warnings

    # Gather text across all context markdown files
    all_context_text = ""
    for md_file in context_dir.glob("*.md"):
        all_context_text += md_file.read_text(encoding="utf-8") + "\n"

    # Check top files / modules
    files = manifest.get("files", [])
    for f in files:
        fpath = f.get("path", "")
        stem = Path(fpath).stem
        # If file has significant code (>100 lines) and isn't mentioned by name or stem
        if f.get("lines", 0) > 100:
            if fpath not in all_context_text and stem not in all_context_text:
                warnings.append(f"Key source file '{fpath}' ({f.get('lines')} lines) not referenced in context docs.")

    return warnings


def verify_context(root_path: Path, manifest_path: Optional[Path] = None) -> bool:
    """Runs all verifications and prints structured results. Returns True if passed."""
    root = root_path.resolve()
    agents_path = root / "AGENTS.md"
    context_dir = root / "context"

    print(f"=== Verifying Codebase Context for: {root} ===")

    all_errors: List[str] = []
    all_warnings: List[str] = []

    # 1. Check AGENTS.md
    ag_errors, ag_warn = check_agents_md(agents_path)
    all_errors.extend(ag_errors)
    all_warnings.extend(ag_warn)

    # 2. Check context/ directory
    ctx_errors, ctx_warn = check_context_dir(context_dir)
    all_errors.extend(ctx_errors)
    all_warnings.extend(ctx_warn)

    # 3. Check link integrity
    md_files = []
    if agents_path.exists():
        md_files.append(agents_path)
    if context_dir.exists():
        md_files.extend(list(context_dir.glob("*.md")))

    link_errors, link_warn = check_markdown_links(root, md_files)
    all_errors.extend(link_errors)
    all_warnings.extend(link_warn)

    # 4. Coverage check
    m_path = manifest_path or (root / ".context_cache" / "ast_manifest.json")
    cov_warn = check_coverage(m_path, context_dir)
    all_warnings.extend(cov_warn)

    # Report
    print("\n--- Summary ---")
    if all_errors:
        print(f"❌ FAIL: {len(all_errors)} Errors found:")
        for e in all_errors:
            print(f"  • {e}")
    else:
        print("✅ PASS: All critical requirements met.")

    if all_warnings:
        print(f"\n⚠️  {len(all_warnings)} Warnings / Suggestions:")
        for w in all_warnings:
            print(f"  • {w}")

    return len(all_errors) == 0


def main():
    parser = argparse.ArgumentParser(description="Verify AGENTS.md and context/ documentation.")
    parser.add_argument("--root", "-r", type=str, default=".", help="Root of codebase to verify.")
    parser.add_argument("--manifest", "-m", type=str, default=None, help="Optional path to ast_manifest.json.")

    args = parser.parse_args()
    root_path = Path(args.root)
    manifest_path = Path(args.manifest) if args.manifest else None

    passed = verify_context(root_path, manifest_path)
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
