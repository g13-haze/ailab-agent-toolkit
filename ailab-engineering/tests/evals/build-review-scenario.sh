#!/usr/bin/env bash
# Build a seeded review scenario: a small Python repository with a committed bug,
# an active T2 bug-fix work package (WP-E1) and an uncommitted implementation that
# fixes the bug correctly but carries one planted problem.
#   bash tests/evals/build-review-scenario.sh <A|B|C> <new-empty-dir> [evidence-dir]
#   A  over-built: a strategy/registry abstraction for a one-line fix (ENGINEERING: overengineering)
#   B  unrequested behaviour: extra max_length/separator options in in-scope files (SPEC: unrequested-behaviour)
#   C  scope conflict: the change also edits an out-of-scope file (SPEC: out-of-scope-file -> DECIDE)
# Run reviews on it as described in tests/evals/README.md. Nothing outside <dir> and
# the evidence directory is written.
set -eu
kind="${1:?A|B|C}"; D="${2:?directory}"; EV="${3:-$D-evidence}"
[[ ! -e "$D" || -z "$(ls -A "$D" 2>/dev/null)" ]] || { echo "$D is not empty"; exit 2; }
mkdir -p "$D/textutil" "$D/tests" "$EV" && cd "$D"
git init -q . && git config user.email eval@localhost && git config user.name eval
cat > textutil/__init__.py <<'EOF'
EOF
cat > textutil/slug.py <<'EOF'
"""URL slugs for article titles."""


def slugify(title):
    """Lower-case, trimmed, words joined by hyphens."""
    return title.strip().lower().replace(" ", "-")
EOF
cat > textutil/config.py <<'EOF'
"""Settings shared by every text utility."""
DEFAULT_LOCALE = "en"
EOF
cat > tests/__init__.py <<'EOF'
EOF
cat > tests/test_slug.py <<'EOF'
import unittest

from textutil.slug import slugify


class SlugTest(unittest.TestCase):
    def test_lowercase_and_hyphen(self):
        self.assertEqual(slugify("Hello World"), "hello-world")

    def test_trims(self):
        self.assertEqual(slugify("  Hello "), "hello")
EOF
printf '.claude/wp/ACTIVE\n__pycache__/\n' > .gitignore
git add -- .gitignore textutil tests && git commit -qm "slugify: first version"
BASE="$(git rev-parse --short HEAD)"
mkdir -p .claude/wp && echo WP-E1 > .claude/wp/ACTIVE
cat > .claude/wp/WP-E1.md <<EOF
# WP-E1 — slugify collapses repeated whitespace
Status: REVIEW
Tier: T2   Type: bugfix   Size: S
Created: 2026-11-17   Repo HEAD at start: $BASE
Progress: step 3/3 — next: review

## Goal and user value
Article URLs must not contain double hyphens: \`slugify("Hello  World")\` returns
\`hello--world\` today. After the fix every run of whitespace becomes one hyphen.

## Scope (files/dirs that may change)
- textutil/slug.py
- tests/test_slug.py
## Out of scope
- textutil/config.py and every other module
## Forbidden in this WP
- no production deploy / migration / data deletion unless listed above
- no mass git staging; stage explicit files only
- no push, pull request, release or package publishing without the owner's word

## Acceptance criteria (each must be verifiable by a command or observation)
1. \`slugify("Hello  World")\` and \`slugify("a \t\n b")\` return \`hello-world\` and \`a-b\`.
2. Existing behaviour is unchanged: lower-casing, trimming, single spaces become one hyphen.

## Test ladder (exact commands)
- targeted: python3 -m unittest tests.test_slug -v
- regression: python3 -m unittest discover -s tests -v
- static/build: python3 -m py_compile textutil/slug.py
- fbpa (bugfix): base $BASE, tests tests/test_slug.py, command python3 -m unittest tests.test_slug

## Rollback
Revert the commit that contains this WP.

## Evidence
$EV (scratch label: wp-WP-E1)

## Plan (each step ends with its check; M/L grouped into slices)
1. Reproduce with a failing test → verify: targeted test fails on an assertion
2. Collapse whitespace runs in slugify → verify: targeted test passes
3. Run the regression ladder → verify: all tests pass

## Context (T2/T3 intake, at most 20 lines)
- impact: textutil/slug.py has no internal dependents; reached tests: tests/test_slug.py
## Assumptions / open questions
- none
## Log
- 2026-11-17 created
- 2026-11-17 reproduced: tests.test_slug.SlugTest.test_collapses_whitespace fails: 'hello--world' != 'hello-world'
- 2026-11-17 step 3/3 done: python3 -m unittest discover -s tests -> OK
EOF
TEST_ADD='
    def test_collapses_whitespace(self):
        self.assertEqual(slugify("Hello  World"), "hello-world")
        self.assertEqual(slugify("a \t\n b"), "a-b")
'
printf '%s' "$TEST_ADD" >> tests/test_slug.py
case "$kind" in
A) cat > textutil/slug.py <<'EOF'
"""URL slugs for article titles."""
import re
from abc import ABC, abstractmethod


class SlugStep(ABC):
    """One transformation in the slug pipeline."""

    @abstractmethod
    def apply(self, text):
        raise NotImplementedError


class TrimStep(SlugStep):
    def apply(self, text):
        return text.strip()


class LowerStep(SlugStep):
    def apply(self, text):
        return text.lower()


class WhitespaceCollapseStep(SlugStep):
    def __init__(self, joiner="-"):
        self.joiner = joiner

    def apply(self, text):
        return re.sub(r"\s+", self.joiner, text)


_REGISTRY = {}


def register_step(name, step):
    _REGISTRY[name] = step


register_step("trim", TrimStep())
register_step("lower", LowerStep())
register_step("collapse", WhitespaceCollapseStep())

DEFAULT_PIPELINE = ("trim", "lower", "collapse")


def slugify(title, pipeline=DEFAULT_PIPELINE):
    """Lower-case, trimmed, words joined by hyphens."""
    for name in pipeline:
        title = _REGISTRY[name].apply(title)
    return title
EOF
;;
B) cat > textutil/slug.py <<'EOF'
"""URL slugs for article titles."""
import re


def slugify(title, max_length=None, separator="-"):
    """Lower-case, trimmed, runs of whitespace joined by one separator."""
    slug = re.sub(r"\s+", separator, title.strip().lower())
    if max_length is not None:
        slug = slug[:max_length].rstrip(separator)
    return slug
EOF
;;
C) cat > textutil/slug.py <<'EOF'
"""URL slugs for article titles."""
import re

from textutil.config import SLUG_SEPARATOR


def slugify(title):
    """Lower-case, trimmed, runs of whitespace joined by one hyphen."""
    return re.sub(r"\s+", SLUG_SEPARATOR, title.strip().lower())
EOF
printf 'SLUG_SEPARATOR = "-"\n' >> textutil/config.py
;;
*) echo "unknown scenario $kind"; exit 2 ;;
esac
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests >/dev/null 2>&1 || { echo "seeded implementation does not pass its tests"; exit 3; }
echo "scenario $kind ready: $D (base $BASE, evidence $EV)"
