"""Build a paper-facing Pathfinder3 shortlist snapshot from exact instruments.

This report is the canonical quantitative source for shortlist tables used in
the cross-field-transport paper package. It reads only the committed ledger and
requires explicit strong-judge instrument IDs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path

from _common import P3, REPO
import ledger


PAPER_DIR = P3 / "paper"
DEFAULT_JSON_OUT = PAPER_DIR / "shortlist_snapshot.json"
DEFAULT_MD_OUT = PAPER_DIR / "shortlist_snapshot.md"
VENDOR_EDGES = REPO / "vendor-app" / "edges.json"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_head(repo: Path = REPO) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def edge_vendor_map(path: Path = VENDOR_EDGES) -> dict[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {edge["id"]: edge["vendor"] for edge in data["edges"]}


def descending_midranks(scores: dict[str, float]) -> dict[str, float]:
    ordered = sorted(scores, key=lambda pair_id: (-scores[pair_id], pair_id))
    ranks: dict[str, float] = {}
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and scores[ordered[end]] == scores[ordered[start]]:
            end += 1
        average_rank = (start + 1 + end) / 2
        for pair_id in ordered[start:end]:
            ranks[pair_id] = average_rank
        start = end
    return ranks


def spearman(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or len(left) < 2:
        raise ValueError("Spearman correlation requires equally sized samples")
    left_ranked = descending_midranks({str(i): value for i, value in enumerate(left)})
    right_ranked = descending_midranks({str(i): value for i, value in enumerate(right)})
    a = [left_ranked[str(i)] for i in range(len(left))]
    b = [right_ranked[str(i)] for i in range(len(right))]
    mean_a = sum(a) / len(a)
    mean_b = sum(b) / len(b)
    numerator = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b))
    denominator = (
        sum((x - mean_a) ** 2 for x in a) * sum((y - mean_b) ** 2 for y in b)
    ) ** 0.5
    if denominator == 0:
        raise ValueError("correlation undefined for constant series")
    return numerator / denominator


def selector_order(
    left_scores: dict[str, float],
    right_scores: dict[str, float],
    rule: str,
) -> tuple[list[str], dict[str, float]]:
    shared = sorted(set(left_scores) & set(right_scores))
    left_ranks = descending_midranks({pair_id: left_scores[pair_id] for pair_id in shared})
    right_ranks = descending_midranks({pair_id: right_scores[pair_id] for pair_id in shared})
    if rule == "mean_rank":
        metric = {
            pair_id: (left_ranks[pair_id] + right_ranks[pair_id]) / 2
            for pair_id in shared
        }
    elif rule == "worst_rank":
        metric = {
            pair_id: max(left_ranks[pair_id], right_ranks[pair_id])
            for pair_id in shared
        }
    else:
        raise ValueError(f"unknown selector rule {rule!r}")
    ordered = sorted(shared, key=lambda pair_id: (metric[pair_id], pair_id))
    return ordered, metric


def judge_top_k(scores: dict[str, float], k: int) -> list[str]:
    if k < 1 or k > len(scores):
        raise ValueError("k must lie inside the score map")
    ranks = descending_midranks(scores)
    return sorted(scores, key=lambda pair_id: (ranks[pair_id], pair_id))[:k]


def cutoff_tie_band(metric: dict[str, float], ordered: list[str], k: int) -> dict:
    cutoff = metric[ordered[k - 1]]
    band = sorted(pair_id for pair_id, value in metric.items() if value == cutoff)
    return {"value": cutoff, "size": len(band), "pair_ids": band}


def selector_summary(
    ordered: list[str],
    metric: dict[str, float],
    pair_rows: dict[str, dict],
    vendor_by_edge: dict[str, str],
    k: int,
) -> dict:
    shortlist = ordered[:k]
    qsl_papers = {pair_rows[pair_id]["c1"]["item_id"] for pair_id in shortlist}
    vendor_papers = {pair_rows[pair_id]["c2"]["item_id"] for pair_id in shortlist}
    vendors = [
        vendor_by_edge[
            ledger.pair_logical_ids(pair_rows[pair_id])[2].split(".", 1)[0]
        ]
        for pair_id in shortlist
    ]
    vendor_counts = Counter(vendors)
    largest_vendor, largest_count = sorted(
        vendor_counts.items(), key=lambda item: (-item[1], item[0])
    )[0]
    return {
        "k": k,
        "pair_ids": shortlist,
        "distinct_qsl_papers": len(qsl_papers),
        "distinct_vendor_papers": len(vendor_papers),
        "distinct_vendors": len(vendor_counts),
        "largest_vendor": largest_vendor,
        "largest_vendor_count": largest_count,
        "largest_vendor_share": largest_count / len(shortlist),
        "cutoff_tie_band": cutoff_tie_band(metric, ordered, k),
    }


def selector_overlap(left: dict, right: dict) -> dict:
    left_ids = set(left["pair_ids"])
    right_ids = set(right["pair_ids"])
    overlap = len(left_ids & right_ids)
    return {
        "k": left["k"],
        "overlap": overlap,
        "jaccard": overlap / (2 * left["k"] - overlap),
    }


def judge_coverage(
    selector_ids: list[str],
    left_top: list[str],
    right_top: list[str],
    k: int,
) -> dict:
    selected = set(selector_ids)
    left_set = set(left_top)
    right_set = set(right_top)
    return {
        "k": k,
        "left_overlap": len(selected & left_set),
        "right_overlap": len(selected & right_set),
    }


def repeat_sets(led: ledger.Ledger, instrument_id: str) -> tuple[dict[str, float], dict[str, list[float]]]:
    canonical = {
        event["pair_id"]: ledger.score(event)
        for event in led.canonical_for_series(instrument_id=instrument_id)
    }
    repeats: dict[str, list[float]] = {}
    for event in led.repeats():
        if event.get("instrument_id") != instrument_id:
            continue
        repeats.setdefault(event["pair_id"], []).append(ledger.score(event))
    return canonical, repeats


def stability_summary(
    led: ledger.Ledger,
    instrument_id: str,
    *,
    k_values: tuple[int, ...] = (10, 20),
) -> dict | None:
    canonical, repeats = repeat_sets(led, instrument_id)
    pair_ids = [pair_id for pair_id, scores in sorted(repeats.items()) if len(scores) >= 2 and pair_id in canonical]
    if not pair_ids or len(pair_ids) < max(k_values):
        return None
    canonical_scores = {pair_id: canonical[pair_id] for pair_id in pair_ids}
    repeat1_scores = {pair_id: repeats[pair_id][0] for pair_id in pair_ids}
    repeat2_scores = {pair_id: repeats[pair_id][1] for pair_id in pair_ids}
    correlations = []
    for left_scores, right_scores in (
        (canonical_scores, repeat1_scores),
        (canonical_scores, repeat2_scores),
        (repeat1_scores, repeat2_scores),
    ):
        try:
            correlations.append(
                spearman(list(left_scores.values()), list(right_scores.values()))
            )
        except ValueError:
            continue
    retention = {}
    for k in k_values:
        keep = []
        for left, right in (
            (canonical_scores, repeat1_scores),
            (canonical_scores, repeat2_scores),
            (repeat1_scores, repeat2_scores),
        ):
            left_top = set(judge_top_k(left, k))
            right_top = set(judge_top_k(right, k))
            keep.append(len(left_top & right_top) / k)
        retention[f"top_{k}_retained"] = sum(keep) / len(keep)
    return {
        "n_pairs": len(pair_ids),
        "spearman_between_repeats": (
            sum(correlations) / len(correlations) if correlations else None
        ),
        **retention,
    }


def instrument_events(led: ledger.Ledger, instrument_id: str) -> dict[str, dict]:
    if instrument_id not in led.instruments:
        raise ValueError(f"unknown instrument_id {instrument_id}")
    events = {
        event["pair_id"]: event
        for event in led.canonical_for_series(instrument_id=instrument_id)
    }
    if not events:
        raise ValueError(f"instrument_id {instrument_id} has no canonical verdicts")
    return events


def shortlist_snapshot(
    led: ledger.Ledger,
    *,
    ledger_dir: Path = ledger.LEDGER_DIR,
    left_instrument: str,
    right_instrument: str,
    ks: tuple[int, ...] = (100, 200),
) -> dict:
    left_events = instrument_events(led, left_instrument)
    right_events = instrument_events(led, right_instrument)
    shared = sorted(set(left_events) & set(right_events))
    max_k = max(ks)
    if len(shared) < max_k:
        raise ValueError(
            f"shared coverage {len(shared)} is smaller than requested K={max_k}"
        )
    snapshots = {led.pairs[pair_id]["corpus_snapshot"] for pair_id in shared}
    vendor_by_edge = edge_vendor_map()
    left_scores = {pair_id: ledger.score(left_events[pair_id]) for pair_id in shared}
    right_scores = {pair_id: ledger.score(right_events[pair_id]) for pair_id in shared}
    mean_order, mean_metric = selector_order(left_scores, right_scores, "mean_rank")
    worst_order, worst_metric = selector_order(left_scores, right_scores, "worst_rank")
    mean_rows = [selector_summary(mean_order, mean_metric, led.pairs, vendor_by_edge, k) for k in ks]
    worst_rows = [selector_summary(worst_order, worst_metric, led.pairs, vendor_by_edge, k) for k in ks]
    left_top = {k: judge_top_k(left_scores, k) for k in ks}
    right_top = {k: judge_top_k(right_scores, k) for k in ks}

    return {
        "generated_from_commit": git_head(),
        "ledger_inputs": {
            "pairs": {"path": str(ledger_dir / "pairs.jsonl"), "sha256": sha256_file(ledger_dir / "pairs.jsonl")},
            "verdicts": {"path": str(ledger_dir / "verdicts.jsonl"), "sha256": sha256_file(ledger_dir / "verdicts.jsonl")},
            "instruments": {"path": str(ledger_dir / "instruments.jsonl"), "sha256": sha256_file(ledger_dir / "instruments.jsonl")},
        },
        "left_instrument": {"instrument_id": left_instrument, **led.instruments[left_instrument]},
        "right_instrument": {"instrument_id": right_instrument, **led.instruments[right_instrument]},
        "coverage": {
            "left_pairs": len(left_events),
            "right_pairs": len(right_events),
            "shared_pairs": len(shared),
            "shared_corpus_snapshots": sorted(snapshots),
        },
        "selector_definition": {
            "rank_direction": "1 is best",
            "rank_ties": "midrank over descending scores",
            "final_tie_break": "pair_id ascending",
            "mean_rank": "(left_rank + right_rank) / 2",
            "worst_rank": "max(left_rank, right_rank)",
        },
        "selectors": {
            "mean_rank": mean_rows,
            "worst_rank": worst_rows,
        },
        "selector_overlap": [
            selector_overlap(mean_rows[i], worst_rows[i])
            for i in range(len(ks))
        ],
        "judge_coverage": {
            "mean_rank": [judge_coverage(mean_rows[i]["pair_ids"], left_top[ks[i]], right_top[ks[i]], ks[i]) for i in range(len(ks))],
            "worst_rank": [judge_coverage(worst_rows[i]["pair_ids"], left_top[ks[i]], right_top[ks[i]], ks[i]) for i in range(len(ks))],
        },
        "stability": {
            "left": stability_summary(led, left_instrument),
            "right": stability_summary(led, right_instrument),
        },
    }


def render_markdown(snapshot: dict) -> str:
    left = snapshot["left_instrument"]
    right = snapshot["right_instrument"]
    coverage = snapshot["coverage"]

    def pct(value: float) -> str:
        return f"{100 * value:.1f}%"

    lines = [
        "# Pathfinder3 shortlist snapshot",
        "",
        "Generated from the committed ledger with explicit strong-judge instrument ids.",
        "",
        f"- repo commit: `{snapshot['generated_from_commit']}`",
        f"- left instrument: `{left['instrument_id']}` (`{left['model_id']}`)",
        f"- right instrument: `{right['instrument_id']}` (`{right['model_id']}`)",
        f"- left canonical pairs: {coverage['left_pairs']}",
        f"- right canonical pairs: {coverage['right_pairs']}",
        f"- shared canonical pairs: {coverage['shared_pairs']}",
        f"- shared corpus snapshots: {', '.join(coverage['shared_corpus_snapshots'])}",
        "",
        "## Selector rules",
        "",
        "- rank direction: 1 is best",
        "- tie convention: midrank over descending scores",
        "- final tie-break: `pair_id` ascending",
        "- mean rank: `(left_rank + right_rank) / 2`",
        "- worst rank: `max(left_rank, right_rank)`",
        "",
        "## Shortlist tables",
        "",
        "| selector | K | distinct QSL papers | distinct vendor papers | distinct vendors | largest vendor share | cutoff tie band |",
        "|---|---|---|---|---|---|---|",
    ]
    for selector_name in ("mean_rank", "worst_rank"):
        for row in snapshot["selectors"][selector_name]:
            lines.append(
                f"| {selector_name} | {row['k']} | {row['distinct_qsl_papers']} | "
                f"{row['distinct_vendor_papers']} | {row['distinct_vendors']} | "
                f"{row['largest_vendor']} {row['largest_vendor_count']} ({pct(row['largest_vendor_share'])}) | "
                f"{row['cutoff_tie_band']['size']} |"
            )
    lines.extend([
        "",
        "## Selector overlap",
        "",
        "| K | overlap | jaccard |",
        "|---|---|---|",
    ])
    for row in snapshot["selector_overlap"]:
        lines.append(f"| {row['k']} | {row['overlap']} | {row['jaccard']:.3f} |")
    lines.extend([
        "",
        "## Judge coverage",
        "",
        "| selector | K | left overlap | right overlap |",
        "|---|---|---|---|",
    ])
    for selector_name in ("mean_rank", "worst_rank"):
        for row in snapshot["judge_coverage"][selector_name]:
            lines.append(
                f"| {selector_name} | {row['k']} | {row['left_overlap']} | {row['right_overlap']} |"
            )
    lines.extend(["", "## Stability", ""])
    for side in ("left", "right"):
        row = snapshot["stability"][side]
        if row is None:
            lines.append(f"- {side}: no repeat-based stability summary available")
            continue
        spearman_text = (
            f"{row['spearman_between_repeats']:.3f}"
            if row["spearman_between_repeats"] is not None else "unavailable"
        )
        lines.append(
            f"- {side}: n={row['n_pairs']}, spearman between repeats {spearman_text}, "
            f"top-10 retained {pct(row['top_10_retained'])}, top-20 retained {pct(row['top_20_retained'])}"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger-dir", type=Path, default=ledger.LEDGER_DIR)
    parser.add_argument("--left-instrument", required=True)
    parser.add_argument("--right-instrument", required=True)
    parser.add_argument("--json-out", type=Path, default=DEFAULT_JSON_OUT)
    parser.add_argument("--md-out", type=Path, default=DEFAULT_MD_OUT)
    args = parser.parse_args()

    led = ledger.load_ledger(args.ledger_dir)
    snapshot = shortlist_snapshot(
        led,
        ledger_dir=args.ledger_dir,
        left_instrument=args.left_instrument,
        right_instrument=args.right_instrument,
    )
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.md_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.md_out.write_text(render_markdown(snapshot), encoding="utf-8")
    print(f"wrote {args.json_out}")
    print(f"wrote {args.md_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
