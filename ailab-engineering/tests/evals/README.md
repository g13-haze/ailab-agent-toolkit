# Review evaluations (manual, they use model tokens)

Seeded scenarios for comparing the 0.1.7 reviewer with the 0.2.0 two-axis review
and for exercising the bounded loop in a real headless Claude Code session. They
are not part of `run-all.sh` because they call models and their output varies; run
each configuration more than once before drawing conclusions.

| Scenario | Planted problem | Expected with 0.2.0 |
|---|---|---|
| A | correct fix built as a strategy/registry abstraction | SPEC pass; ENGINEERING `overengineering` finding |
| B | correct fix plus `max_length` and `separator` options nobody asked for, in in-scope files | SPEC fail (`unrequested-behaviour`); fixed in round 1, PASS in round 2 |
| C | correct fix that also edits out-of-scope `textutil/config.py` | `out-of-scope-file` → DECIDE → the loop stops and asks the owner |

```bash
bash tests/evals/build-review-scenario.sh B /tmp/eval-B            # A | B | C
cd /tmp/eval-B
# 0.1.7 reviewer (plugin dir from: git archive baseline-0.1.7 | tar -x -C /tmp/egm-0.1.7)
claude -p --plugin-dir /tmp/egm-0.1.7/plugins/ailab-engineering --allowedTools "Agent,Read,Grep,Glob,Bash" \
  --output-format stream-json --verbose "Use the reviewer subagent to review the active work package WP-E1 ..." > run.jsonl
# 0.2.0 full loop
claude -p --plugin-dir <repo>/plugins/ailab-engineering --allowedTools "Agent,Skill,Read,Edit,Write,Grep,Glob,Bash" \
  --output-format stream-json --verbose "/ailab-engineering:review WP-E1" > run.jsonl
python3 -I <repo>/tests/evals/summarize.py run.jsonl /tmp/eval-B
```

`summarize.py` reports which subagents ran and whether they were launched in one
message, whether a reviewer's prompt quoted another reviewer's findings, the WP
round lines, commits (there must be none) and cost. The FBPA check of each scenario
is deterministic: `intel.sh fbpa --base <WP base> --tests tests/test_slug.py --workdir <scratch> -- python3 -m unittest tests.test_slug` → PASS.
