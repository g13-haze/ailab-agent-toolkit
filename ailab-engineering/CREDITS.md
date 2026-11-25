# Credits — ideas adapted in ailab-engineering 0.2.0

The 0.2.0 upgrade adapts ideas from five external collections that the owner
supplied for review. No text or code was copied: every adopted idea was
re-expressed in this repository's own words and mechanisms. Material that was not
adopted is listed in the 0.2.0 acceptance report with the reason.

| Source | Idea adapted | Where |
|---|---|---|
| `global_agents` (no licence file) | Four engineering behaviour rules (think before coding, simplicity, surgical changes, goal-driven steps with checks) | `shared/engineering-behaviour.md`, AGENTS.md template |
| `ai-agents-skills-main` (no licence file) | Parent-controlled review loop with a round limit, finding triage and stop conditions; debugging sequence (reproduce, localize, root cause, guard) | `shared/review-policy.md`, review skill, `shared/protocols/bugfix.md` |
| `ai-skills-agent-loop` (no licence file) | Spec and standards reviewed as separate axes in separate contexts, never merged; smells as judgement calls; repository standards win | spec-reviewer, reviewer |
| `project-spec` (no licence file) | Known edge cases and traps written as verifiable acceptance criteria | WP template, feature protocol |
| `skills-main` (MIT, Copyright (c) 2026 Mikhail Larchanka) | Change intelligence (progressive depth, fact/risk/unknown, attention map, checkpoint discipline); "would the test pass if the code returned nothing"; reproduce-first; characterization before refactoring; planning in verifiable slices | change-intel skill, protocols, reviewer test-quality lens, WP plan |

No source code from any collection is included, executed by, or required by this
plugin. The `intel` tool is an independent implementation; the codebase context
generator in `skills-main` was not used.

## skills-main licence notice

The adapted `skills-main` ideas were rewritten rather than copied; the notice is
included here as a courtesy:

    MIT License

    Copyright (c) 2026 Mikhail Larchanka

    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction, including without limitation the rights
    to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in all
    copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
    AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
    OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
    SOFTWARE.
