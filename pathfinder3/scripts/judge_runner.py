"""Run a registered judge over the calibration pairs.

For every pair lacking a verdict from the requested judge (matched on
``(judge, prompt_version)``), renders ``judge_prompt_v1.md`` with the two
title+abstract blocks (verifying each block's SHA-256 against the pair's
recorded ``input_sha256`` first), calls the claude CLI with the judge's
pinned model id, validates the strict-JSON verdict (ranges, rationale
length), and appends it to the pair's ``verdicts`` array. Raw outputs go to
``pathfinder3/logs/judge-<tier>-<date>.jsonl``.

Out-of-contract output gets one retry; a second failure is recorded in the
log and the pair is left for a later run. The pairs file is rewritten
atomically at the end.

Usage:
    python3 pathfinder3/scripts/judge_runner.py --judge cheap [--workers 4]
    python3 pathfinder3/scripts/judge_runner.py --judge strong
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import subprocess
import threading
from datetime import date
from pathlib import Path

import yaml

from _common import (P3, PROMPT_PATH, PROMPT_VERSION, corpus_by_item_id,
                     dump_jsonl, item_block, load_jsonl, render_prompt,
                     sha256_text)

PAIRS_PATH = P3 / "calibration" / "calibration_pairs.jsonl"
LOG_DIR = P3 / "logs"


def _call_judge(model_id: str, prompt: str, timeout: int = 300) -> str:
    # No permission bypass and no tools: the judge is a pure text call.
    proc = subprocess.run(
        ["claude", "--model", model_id, "--disallowedTools", "*",
         "--print", "--output-format", "text", "-p", prompt],
        capture_output=True, text=True, timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude rc={proc.returncode}: {proc.stderr[:300]}")
    return proc.stdout.strip()


def _parse_verdict(raw: str) -> dict:
    # Documented normalisation: strip a markdown fence if the model wrapped
    # the object in one. Anything beyond that stays out-of-contract.
    if raw.startswith("```"):
        raw = raw.strip("`\n")
        raw = raw[4:].lstrip() if raw.startswith("json") else raw
    if not (raw.startswith("{") and raw.endswith("}")):
        raise ValueError(f"not a bare JSON object: {raw[:120]!r}")
    obj = json.loads(raw)
    if set(obj) != {"corr", "int", "rationale"}:
        raise ValueError(f"wrong keys: {sorted(obj)}")
    for k in ("corr", "int"):
        if not isinstance(obj[k], (int, float)) or not 0 <= obj[k] <= 1:
            raise ValueError(f"{k} out of range: {obj[k]!r}")
    if not isinstance(obj["rationale"], str):
        raise ValueError("rationale missing")
    # Documented normalisation: rationale is advisory metadata, so an
    # over-long one is truncated rather than rejected. Scores are never
    # coerced.
    obj["rationale"] = obj["rationale"][:140]
    return obj


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--judge", required=True, choices=("cheap", "strong"))
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0,
                    help="judge at most N pairs (0 = all)")
    args = ap.parse_args()

    registry = yaml.safe_load((P3 / "protocol" / "judges.yaml").read_text())
    judge_cfg = registry["judges"][args.judge]
    model_id = judge_cfg["model_id"]
    judge_id = f"claude:{model_id}"
    template = PROMPT_PATH.read_text()
    items = corpus_by_item_id()
    pairs = load_jsonl(PAIRS_PATH)
    today = date.today().isoformat()

    todo = []
    for row in pairs:
        if any(v["judge"] == judge_id and v["prompt_version"] == PROMPT_VERSION
               for v in row["verdicts"]):
            continue
        q, p = items[row["c1"]["item_id"]], items[row["c2"]["item_id"]]
        for side, item in (("c1", q), ("c2", p)):
            got = sha256_text(item_block(item))
            if got != row[side]["input_sha256"]:
                raise SystemExit(
                    f"provenance mismatch on {row['pair_id']} {side}: corpus "
                    "text no longer matches the pair's input_sha256; rebuild "
                    "the calibration set or restore the corpus snapshot")
        todo.append((row, q, p))
    if args.limit:
        todo = todo[:args.limit]
    print(f"{args.judge} judge ({judge_id}): {len(todo)} pairs to judge")

    LOG_DIR.mkdir(exist_ok=True)
    log_path = LOG_DIR / f"judge-{args.judge}-{today}.jsonl"
    log_lock = threading.Lock()
    failures = []

    def work(entry):
        row, q, p = entry
        prompt = render_prompt(template, q, p)
        raw, verdict, error = None, None, None
        for attempt in (1, 2):
            try:
                raw = _call_judge(model_id, prompt)
                verdict = _parse_verdict(raw)
                break
            except Exception as e:  # noqa: BLE001 - log and retry once
                error = f"attempt {attempt}: {e}"
        with log_lock, log_path.open("a") as f:
            f.write(json.dumps({"pair_id": row["pair_id"], "judge": judge_id,
                                "raw": raw, "error": None if verdict else error,
                                "date": today}, ensure_ascii=False) + "\n")
        return row["pair_id"], verdict, error

    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        results = list(ex.map(work, todo))

    by_id = {r["pair_id"]: r for r in pairs}
    done = 0
    for pair_id, verdict, error in results:
        if verdict is None:
            failures.append((pair_id, error))
            continue
        by_id[pair_id]["verdicts"].append({
            "judge": judge_id,
            "tier": args.judge,
            "prompt_version": PROMPT_VERSION,
            "corr": float(verdict["corr"]),
            "int": float(verdict["int"]),
            "rationale": verdict["rationale"],
            "settings": {"transport": judge_cfg.get("transport", "claude-cli")},
            "judged_at": today,
        })
        done += 1

    dump_jsonl(PAIRS_PATH, pairs)
    print(f"judged {done}/{len(todo)}; failures: {len(failures)}")
    for pair_id, error in failures:
        print(f"  FAILED {pair_id}: {error}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
