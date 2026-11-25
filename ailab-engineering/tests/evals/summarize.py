#!/usr/bin/env python3
"""Summarize a `claude -p --output-format stream-json --verbose` review run.

  python3 -I tests/evals/summarize.py <run.jsonl> [<repo-dir>]

Prints: subagents launched (type, model, message id: same id = launched in one
message), whether any reviewer prompt quotes another reviewer's hand-back
(independence), the hand-back first lines, WP Log round lines, commits made, the
work-tree status, turns and cost. Facts only; no scoring.
"""
import json
import os
import re
import subprocess
import sys


def main():
    path = sys.argv[1]
    repo = sys.argv[2] if len(sys.argv) > 2 else None
    launches, results, final = [], {}, None
    for line in open(path, encoding="utf-8", errors="replace"):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        msg = d.get("message") if isinstance(d.get("message"), dict) else {}
        parent = d.get("parent_tool_use_id")
        if parent:                        # inside a subagent: keep its last text as the hand-back
            if d.get("type") == "assistant" and isinstance(msg.get("content"), list):
                text = "\n".join(c.get("text", "") for c in msg["content"] if c.get("type") == "text").strip()
                if text:
                    results[parent] = text
            continue
        content = msg.get("content") if isinstance(msg.get("content"), list) else []
        for c in content:
            if c.get("type") == "tool_use" and c.get("name") in ("Agent", "Task"):
                inp = c.get("input") or {}
                launches.append({"id": c["id"], "msg": msg.get("id", "?"), "type": inp.get("subagent_type", "?"),
                                 "prompt": inp.get("prompt", "")})
            if c.get("type") == "tool_result" and c.get("tool_use_id") in {l["id"] for l in launches}:
                body = c.get("content")
                if isinstance(body, list):
                    body = "\n".join(x.get("text", "") for x in body if isinstance(x, dict))
                if body and "Async agent launched" not in body:
                    results[c["tool_use_id"]] = body
        if d.get("type") == "result":
            final = d
    print("SUBAGENTS LAUNCHED: %d" % len(launches))
    for l in launches:
        same = sum(1 for x in launches if x["msg"] == l["msg"])
        first = next((ln for ln in results.get(l["id"], "").splitlines()
                      if re.match(r"^\W*(SPEC REVIEW|REVIEW|SECURITY VERDICT|QA|PLAN REVIEW)\b", ln.strip())), "(no hand-back line)")
        print("- %-18s message %s (%s)  -> %s" % (l["type"], l["msg"][-8:], "parallel x%d" % same if same > 1 else "alone", first.strip()[:150]))
    # independence: does any reviewer prompt quote another reviewer's finding lines?
    leaks = []
    for l in launches:
        for o in launches:
            if o is l:
                continue
            for ln in results.get(o["id"], "").splitlines():
                ln = ln.strip()
                if re.match(r"^-? ?\[(P[012]|CRITICAL|HIGH|MEDIUM|LOW)\]", ln) and ln[:60] in l["prompt"]:
                    leaks.append((l["type"], o["type"], ln[:80]))
    print("INDEPENDENCE: %s" % ("no reviewer prompt contains another reviewer's finding lines" if not leaks else "LEAKS: %s" % leaks))
    if repo:
        wp = os.path.join(repo, ".claude", "wp", "WP-E1.md")
        if os.path.exists(wp):
            rounds = [l.rstrip() for l in open(wp) if "review round" in l]
            print("WP LOG ROUND LINES (%d):" % len(rounds))
            for r in rounds:
                print("  " + r[:260])
        log = subprocess.run(["git", "-C", repo, "log", "--oneline"], capture_output=True, text=True).stdout.strip().splitlines()
        print("COMMITS IN REPO: %d (%s)" % (len(log), "; ".join(log)))
        st = subprocess.run(["git", "-C", repo, "status", "--short"], capture_output=True, text=True).stdout.strip()
        print("WORK TREE: " + (st.replace("\n", " | ") or "clean"))
    if final:
        print("TURNS: %s  COST USD: %s  DURATION: %ss" % (final.get("num_turns"), final.get("total_cost_usd"),
                                                         round((final.get("duration_ms") or 0) / 1000)))
        print("FINAL ANSWER (first 1500 chars):\n" + (final.get("result") or "")[:1500])


if __name__ == "__main__":
    main()
