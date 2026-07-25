"""Merge new (QSL paper, vendor paper) pairs into ledger/pairs.jsonl.

For future corpus growth only — the 10,912 pairs from the initial migration
already live in ledger/pairs.jsonl (see migrate_to_ledger.py and
plans/pathfinder3-ledger-refactor-design.md). This script computes the full
corpus product, adds any pair_id not already present, and asserts every
existing pair's recorded input_sha256 is unchanged (a changed corpus text
under an unchanged pair_id would silently invalidate every verdict that
scored the old text — that must be a loud provenance error, not a merge).

Usage: python3 pathfinder3/scripts/build_phase1_pairs.py
"""
from __future__ import annotations

import subprocess

from _common import P3, REPO, item_block, load_jsonl, sha256_text
from _ledger_common import dump_jsonl_sorted
import ledger


class ProvenanceError(RuntimeError):
    pass


def corpus_snapshot() -> str:
    return subprocess.run(
        ["git", "log", "-1", "--format=%h", "--", "pathfinder3/corpus/"],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout.strip()


def merge_pairs(existing: list[dict], qsl: list[dict], vendor: list[dict],
                snapshot: str) -> tuple[list[dict], int, int]:
    by_id = {r["pair_id"]: r for r in existing}
    new_count = unchanged_count = 0
    for q in qsl:
        for p in vendor:
            pair_id = f"{q['item_id']}::{p['item_id']}"
            c1_hash = sha256_text(item_block(q))
            c2_hash = sha256_text(item_block(p))
            if pair_id in by_id:
                row = by_id[pair_id]
                if (row["c1"]["input_sha256"] != c1_hash
                        or row["c2"]["input_sha256"] != c2_hash):
                    raise ProvenanceError(
                        f"{pair_id}: corpus text changed under an existing "
                        f"pair_id (old hash vs recomputed hash mismatch); "
                        f"every verdict scoring the old text is now "
                        f"orphaned from its input — investigate before "
                        f"merging")
                unchanged_count += 1
                continue
            by_id[pair_id] = {
                "pair_id": pair_id,
                "c1": {"corpus": "QSL_Papers", "item_id": q["item_id"],
                      "title": q["title"], "input_sha256": c1_hash},
                "c2": {"corpus": "Vendor_papers", "item_id": p["item_id"],
                      "title": p["title"], "input_sha256": c2_hash},
                "corpus_snapshot": snapshot,
            }
            new_count += 1
    merged = sorted(by_id.values(), key=lambda r: r["pair_id"])
    return merged, new_count, unchanged_count


def main() -> None:
    qsl = load_jsonl(P3 / "corpus" / "qsl_papers.jsonl")
    vendor = load_jsonl(P3 / "corpus" / "vendor_papers.jsonl")
    snapshot = corpus_snapshot()
    existing = load_jsonl(ledger.PAIRS_PATH) if ledger.PAIRS_PATH.exists() else []

    merged, new_count, unchanged_count = merge_pairs(existing, qsl, vendor, snapshot)
    dump_jsonl_sorted(ledger.PAIRS_PATH, merged)
    print(f"ledger/pairs.jsonl: {len(merged)} total "
         f"({new_count} new, {unchanged_count} unchanged, snapshot {snapshot})")


if __name__ == "__main__":
    main()
