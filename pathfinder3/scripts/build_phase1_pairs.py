"""Build the Phase 1 production pair file: the full corpus product.

Emits one row per (QSL paper, vendor paper) pair to
``pathfinder3/phase1/pair_matrix.jsonl``, schema-identical to the
calibration file but with ``stratum: "production"``. Verdicts already
recorded in ``calibration_pairs.jsonl`` are copied across by pair_id so the
sweep does not re-judge the calibration subset; the runner's idempotency on
``(judge, prompt_version)`` then skips those rows.

Refuses to overwrite an existing output file, because judge verdicts
accumulate in it; delete it deliberately if a rebuild is intended.

Usage: python3 pathfinder3/scripts/build_phase1_pairs.py
"""
from __future__ import annotations

import subprocess

from _common import P3, REPO, dump_jsonl, item_block, load_jsonl, sha256_text

CALIBRATION_PATH = P3 / "calibration" / "calibration_pairs.jsonl"
PHASE1_DIR = P3 / "phase1"
OUT_PATH = PHASE1_DIR / "pair_matrix.jsonl"


def corpus_snapshot() -> str:
    return subprocess.run(
        ["git", "log", "-1", "--format=%h", "--", "pathfinder3/corpus/"],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout.strip()


def main() -> None:
    if OUT_PATH.exists():
        raise SystemExit(f"{OUT_PATH} already exists; refusing to overwrite")

    qsl = load_jsonl(P3 / "corpus" / "qsl_papers.jsonl")
    vendor = load_jsonl(P3 / "corpus" / "vendor_papers.jsonl")
    snapshot = corpus_snapshot()
    calibration_verdicts = {
        r["pair_id"]: r["verdicts"] for r in load_jsonl(CALIBRATION_PATH)
        if r["verdicts"]
    }

    rows = []
    copied = 0
    for q in qsl:
        for p in vendor:
            pair_id = f"{q['item_id']}::{p['item_id']}"
            verdicts = calibration_verdicts.get(pair_id, [])
            copied += bool(verdicts)
            rows.append({
                "schema_version": 1,
                "pair_id": pair_id,
                "c1": {"corpus": "QSL_Papers", "item_id": q["item_id"],
                       "title": q["title"],
                       "input_sha256": sha256_text(item_block(q))},
                "c2": {"corpus": "Vendor_papers", "item_id": p["item_id"],
                       "title": p["title"],
                       "input_sha256": sha256_text(item_block(p))},
                "corpus_snapshot": snapshot,
                "stratum": "production",
                "sampling_seed": None,
                "verdicts": verdicts,
                "decision": None,
            })

    PHASE1_DIR.mkdir(exist_ok=True)
    dump_jsonl(OUT_PATH, rows)
    print(f"wrote {len(rows)} pairs ({len(qsl)} x {len(vendor)}) to {OUT_PATH}; "
          f"verdicts copied from calibration for {copied} pairs")


if __name__ == "__main__":
    main()
