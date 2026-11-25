---
name: host-gate
description: Write an owner-run host verification script for A.I. Lab (read-only by default) with bounded commands, KEY=VALUE results and a final verdict line, saved as a new evidence file. Use when something must be verified on the real host or production runtime that the agent cannot or must not touch itself.
argument-hint: <gate-name> <what must be verified>
disable-model-invocation: true
---

# Host gate: $0

What must be verified: $ARGUMENTS

You do not run this gate on production yourself. You write a script the owner
runs on the host, then you interpret the output they paste back.

## Rules for the script
- Bash, `set -u`, no `set -e` (every check must report, not abort).
- READ-ONLY unless the owner explicitly approved a write in this conversation;
  if a write is approved, put it in a clearly separated section with its own
  confirmation prompt (`read -r -p "Apply? [y/N] "`) and a backup step first.
- Every external or slow command wrapped in `timeout <s>`.
- Never print secrets: no `env`, no `cat` of `.env` files, no `docker inspect`
  without `--format`, no DB rows with personal data. Counts, hashes, timestamps,
  exit codes and sizes only.
- Output machine-checkable lines: `CHECK_NAME=PASS|FAIL|UNKNOWN detail`.
  End with `GATE_<NAME>=PASS|FAIL|INCOMPLETE checks=<passed>/<total>`.
- UNKNOWN is never counted as PASS. A timer that ran is not a successful job;
  HTTP 200 is not fresh data. Check the actual outcome (exit status, result
  timestamps, row counts, checksums).
- Bounded scans only: no `find /`, no full-tree `du` on large roots without
  `--max-depth` and `timeout`.
- Header comment: purpose, date, what it reads, what it never touches, how to run:
  `bash <file> 2>&1 | tee ~/gate-<name>-$(date -u +%Y%m%dT%H%M%SZ).txt`

## Where to save
Write the script as a NEW file in the project's evidence location (for example host
agents: `/work/agent-logs/` in the container, `/srv/agent-logs/` on the host),
named `<UTC timestamp>__host-gate__<gate-name>.sh`, and print its SHA-256.

## After the owner pastes the output
Produce a short verdict table: check, result, evidence line. Separate VERIFIED
from UNKNOWN. State exactly which follow-up (if any) needs owner approval.
