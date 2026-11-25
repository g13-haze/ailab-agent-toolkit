#!/usr/bin/env python3
"""Unit tests for verify_context."""

import shutil
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from verify_context import (
    check_agents_md,
    check_context_dir,
    check_markdown_links,
    verify_context,
)


class TestVerifyContext(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp(prefix="ctx_test_")).resolve()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_missing_files(self):
        errors, _ = check_agents_md(self.test_dir / "AGENTS.md")
        self.assertTrue(len(errors) > 0)

        errors, _ = check_context_dir(self.test_dir / "context")
        self.assertTrue(len(errors) > 0)

    def test_valid_setup(self):
        agents_content = """# Agent Guidelines
## Project Overview
This is a sample project for testing.

## Build and Test Commands
```bash
npm run build
npm test
```

## Architecture and Context
See [context/index.md](context/index.md).

## Rules and Conventions
Do not introduce unnecessary abstractions.
"""
        (self.test_dir / "AGENTS.md").write_text(agents_content)

        ctx_dir = self.test_dir / "context"
        ctx_dir.mkdir()
        (ctx_dir / "index.md").write_text("# Index\nSee [architecture.md](architecture.md)")
        (ctx_dir / "architecture.md").write_text("# Architecture")
        (ctx_dir / "modules.md").write_text("# Modules")
        (ctx_dir / "signatures.md").write_text("# Signatures")
        (ctx_dir / "dependencies.md").write_text("# Dependencies")
        (ctx_dir / "conventions.md").write_text("# Conventions")

        self.assertTrue(verify_context(self.test_dir))

    def test_broken_markdown_link(self):
        agents_content = """# Agent Guidelines
## Project Overview
Test

## Commands
```bash
test
```

## Architecture
See [missing file](context/does_not_exist.md).

## Rules
Test
"""
        agents_file = self.test_dir / "AGENTS.md"
        agents_file.write_text(agents_content)

        errors, _ = check_markdown_links(self.test_dir, [agents_file])
        self.assertTrue(any("does_not_exist.md" in e for e in errors))

    def test_link_syntax_inside_code_is_not_a_link(self):
        # Regression: a docstring copied into signatures.md as ``answers[n](request)`` was reported as a broken link.
        md = self.test_dir / "signatures.md"
        (self.test_dir / "real.md").write_text("# Real")
        md.write_text(
            "*An offline provider answering each attempt with ``answers[n](request) -> (text, stop_reason)``.*\n"
            "Inline `items[0](call)` and a fenced block:\n"
            "```python\n"
            "x = data[i](arg)  # [not](a-link.md)\n"
            "```\n"
            "~~~\n"
            "[also not](a-link.md)\n"
            "~~~\n"
            "A real link: [ok](real.md)\n"
        )
        errors, _ = check_markdown_links(self.test_dir, [md])
        self.assertEqual(errors, [])

    def test_real_broken_links_outside_code_are_still_reported(self):
        md = self.test_dir / "index.md"
        md.write_text(
            "```bash\necho skipped\n```\n"
            "See [`missing.py`](../missing.py) and [plain](gone.md), with `code` before it.\n"
            "An unclosed `backtick does not hide [this](absent.md)\n"
        )
        errors, _ = check_markdown_links(self.test_dir, [md])
        joined = " ".join(errors)
        self.assertIn("../missing.py", joined)  # link text is a code span: still validated
        self.assertIn("gone.md", joined)
        self.assertIn("absent.md", joined)

    def test_inline_code_at_line_start_is_not_a_fence(self):
        # Review round 1: "```ls -la``` lists files." opened a fence and hid every later link.
        md = self.test_dir / "index.md"
        md.write_text("```ls -la``` lists files.\nSee [broken](missing2.md).\n")
        errors, warnings = check_markdown_links(self.test_dir, [md])
        self.assertTrue(any("missing2.md" in e for e in errors), errors)
        self.assertEqual(warnings, [])

    def test_indented_closing_fence_in_a_list_closes_the_block(self):
        md = self.test_dir / "index.md"
        md.write_text("- step\n  ```bash\n  [inside](inside.md)\n     ```\n[after](missing3.md)\n")
        errors, warnings = check_markdown_links(self.test_dir, [md])
        joined = " ".join(errors)
        self.assertIn("missing3.md", joined)
        self.assertNotIn("inside.md", joined)
        self.assertEqual(warnings, [])

    def test_deeply_indented_backticks_inside_a_block_do_not_close_it(self):
        # Review round 2: a backtick-only line indented 4+ spaces inside a top-level block closed it early.
        md = self.test_dir / "index.md"
        md.write_text("```markdown\n    ```\n[inside](inside.md)\n```\n[after](missing6.md)\n")
        errors, warnings = check_markdown_links(self.test_dir, [md])
        joined = " ".join(errors)
        self.assertNotIn("inside.md", joined)
        self.assertIn("missing6.md", joined)
        self.assertEqual(warnings, [])

    def test_unclosed_fence_is_reported(self):
        md = self.test_dir / "index.md"
        md.write_text("Intro [ok](missing4.md)\n```python\nx = 1\n[hidden](missing5.md)\n")
        errors, warnings = check_markdown_links(self.test_dir, [md])
        self.assertTrue(any("missing4.md" in e for e in errors), errors)
        self.assertTrue(any("Unclosed code fence" in w and "line 2" in w for w in warnings), warnings)

    def test_overview_accepted_under_a_product_heading(self):
        # Regression: an AGENTS.md describing the project under "## Product" was reported as missing an overview.
        agents = self.test_dir / "AGENTS.md"
        agents.write_text("# Agents\n## Product\nWhat it does.\n## Commands\n```bash\nmake test\n```\n"
                          "## Architecture\nLayers.\n## Rules\nBe careful.\n")
        errors, _ = check_agents_md(agents)
        self.assertFalse(any("Project Overview" in e for e in errors), errors)

    def test_overview_still_required_without_heading_or_keyword(self):
        agents = self.test_dir / "AGENTS.md"
        agents.write_text("# Agents\nThe production host runs the product.\n## Commands\n```bash\nmake test\n"
                          "## Product\n```\n## Architecture\nLayers.\n## Rules\nBe careful.\n")
        errors, _ = check_agents_md(agents)
        self.assertTrue(any("Project Overview" in e for e in errors), errors)


if __name__ == "__main__":
    unittest.main()
