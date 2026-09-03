from __future__ import annotations

import hashlib
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pathfinder3" / "scripts"))

import ledger
from build_shortlist_snapshot import edge_vendor_map, spearman
from calibration_report import confusion, eval_pair_ids

PROTOCOL = ROOT / "pathfinder3" / "protocol"
PAPER = ROOT / "pathfinder3" / "paper"
LEDGER = ROOT / "pathfinder3" / "ledger"
VENDOR_EDGES = ROOT / "pathfinder3" / "data" / "vendor-edges.json"
SHORTLIST_LEFT = "7f01f7e54dc02bbc59a447ea77c51c9fe8b16291b8d4e52e37ce1ed0b433b046"
SHORTLIST_RIGHT = "b2a053b3cd994b15dd9f976f8777479b6559e80391a840c1c39bd3dcdf8c7818"
PRIMARY_VENDORS = ("ibm", "quantinuum", "dwave", "ionq", "quera", "pasqal")
PRIMARY_VENDOR_LABELS = {
    "ibm": "IBM",
    "quantinuum": "Quantinuum",
    "dwave": "D-Wave",
    "ionq": "IonQ",
    "quera": "QuEra",
    "pasqal": "Pasqal",
}
EXPECTED_RESULTS = {
    "corpus and pair counts": "88 QSL records, 124 vendor records, and 10,912 pairs",
    "Haiku survivor counts": "6,051 at 0.05, 1,763 at 0.15, and 688 at 0.25",
    "calibration at 0.05": "TP 21, FP 45, TN 31, FN 1, sensitivity 0.955, and specificity 0.408",
    "calibration at 0.15": "sensitivity 0.682 and specificity 0.789",
    "strong-judge score scale": "Opus median 0.022 and count 982 at 0.25; GPT median 0.091 and count 3,306; shared 838",
    "strong-judge rank agreement": "Opus/GPT Spearman 0.740; tie-aware top-100 overlap 12 to 36; random expectation 0.92",
    "shortlist sensitivity": "mean-rank and worst-rank top-100 overlap 92 of 100",
    "mean-rank top-100 composition": "43 QSL papers, 49 vendor artefacts, 13 vendors, and 48 IBM pair entries",
    "corpus/top-100 vendor entries": "IBM 53/48; Quantinuum 13/5; D-Wave 12/1; IonQ 10/11; QuEra 6/8; Pasqal 5/7; Other 25/20",
    "top-10/20/50 panel retention": "Opus 90/95/94; GPT 45/50/73; combined 67/61/73",
}


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    if path.is_dir():
        for child in sorted(item for item in path.rglob("*") if item.is_file()):
            digest.update(str(child.relative_to(path)).encode("utf-8"))
            digest.update(b"\0")
            digest.update(sha256_path(child).encode("ascii"))
            digest.update(b"\0")
        return digest.hexdigest()

    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest() -> dict[str, object]:
    manifests = sorted((ROOT / "release" / "manifests").glob("*.json"))
    if not manifests:
        raise SystemExit("release manifest is absent")
    return json.loads(manifests[-1].read_text(encoding="utf-8"))


def verify_manifest_imports(manifest: dict[str, object]) -> None:
    print("manifest import hashes:")
    for entry in manifest["imports"]:
        public_path = ROOT / entry["public_path"]
        if not public_path.exists():
            raise SystemExit(f"release path is absent: {entry['public_path']}")
        print(f"- {entry['public_path']}: {entry['sha256']}")


def verify_input_hashes() -> None:
    replay_inputs = {
        "qsl_corpus": ROOT / "pathfinder3" / "corpus" / "qsl_papers.jsonl",
        "vendor_corpus": ROOT / "pathfinder3" / "corpus" / "vendor_papers.jsonl",
        "pairs": ROOT / "pathfinder3" / "ledger" / "pairs.jsonl",
        "verdicts": ROOT / "pathfinder3" / "ledger" / "verdicts.jsonl",
        "instruments": ROOT / "pathfinder3" / "ledger" / "instruments.jsonl",
        "calibration_manifest": ROOT / "pathfinder3" / "ledger" / "calibration_manifest.jsonl",
        "vendor_projection": ROOT / "pathfinder3" / "data" / "vendor-edges.json",
    }
    print("offline replay input hashes:")
    for name, path in replay_inputs.items():
        print(f"- {name}: {sha256_path(path)}")


def verify_instrument_hashes() -> None:
    instruments = [
        json.loads(line)
        for line in (LEDGER / "instruments.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    represented = {
        row["instrument_id"]
        for row in [json.loads(line) for line in (LEDGER / "verdicts.jsonl").read_text(encoding="utf-8").splitlines() if line]
        if row.get("instrument_id")
    }
    print("offline replay instrument hashes:")
    for row in instruments:
        if row["instrument_id"] not in represented:
            continue
        print(
            f"- {row['instrument_id']}: prompt {row['prompt_sha256']} | "
            f"output {row['output_contract_sha256']} | system {row.get('system_prompt_sha256')}"
        )


def corpus_and_pair_counts(led: ledger.Ledger) -> str:
    qsl_count = sum(1 for line in (ROOT / "pathfinder3" / "corpus" / "qsl_papers.jsonl").read_text(encoding="utf-8").splitlines() if line)
    vendor_count = sum(1 for line in (ROOT / "pathfinder3" / "corpus" / "vendor_papers.jsonl").read_text(encoding="utf-8").splitlines() if line)
    return f"{qsl_count} QSL records, {vendor_count} vendor records, and {len(led.pairs):,} pairs"


def haiku_survivor_counts() -> str:
    baseline = json.loads((PROTOCOL / "baseline.json").read_text(encoding="utf-8"))
    counts = baseline["haiku_v2_survivors"]
    return (
        f"{counts['s_ge_0.05']:,} at 0.05, {counts['s_ge_0.15']:,} at 0.15, "
        f"and {counts['s_ge_0.25']:,} at 0.25"
    )


def calibration_summary(led: ledger.Ledger, threshold: float) -> str:
    tp, fp, tn, fn = confusion(led, eval_pair_ids(led), threshold)
    sensitivity = tp / (tp + fn)
    specificity = tn / (tn + fp)
    if threshold == 0.05:
        return (
            f"TP {tp}, FP {fp}, TN {tn}, FN {fn}, sensitivity {sensitivity:.3f}, "
            f"and specificity {specificity:.3f}"
        )
    return f"sensitivity {sensitivity:.3f} and specificity {specificity:.3f}"


def shortlist_scores(led: ledger.Ledger) -> tuple[dict[str, float], dict[str, float]]:
    left_scores = {
        row["pair_id"]: ledger.score(row)
        for row in led.canonical_for_series(instrument_id=SHORTLIST_LEFT)
    }
    right_scores = {
        row["pair_id"]: ledger.score(row)
        for row in led.canonical_for_series(instrument_id=SHORTLIST_RIGHT)
    }
    return left_scores, right_scores


def strong_judge_score_scale(led: ledger.Ledger) -> str:
    left_scores, right_scores = shortlist_scores(led)
    shared = len(set(left_scores) & set(right_scores) & {pair_id for pair_id, score in left_scores.items() if score >= 0.25} & {pair_id for pair_id, score in right_scores.items() if score >= 0.25})
    return (
        f"Opus median {statistics.median(left_scores.values()):.3f} and count {sum(score >= 0.25 for score in left_scores.values()):,} at 0.25; "
        f"GPT median {statistics.median(right_scores.values()):.3f} and count {sum(score >= 0.25 for score in right_scores.values()):,}; "
        f"shared {shared}"
    )


def tie_aware_overlap_range(left_scores: dict[str, float], right_scores: dict[str, float], k: int) -> tuple[int, int]:
    def cutoff_sets(scores: dict[str, float]) -> tuple[set[str], set[str], int]:
        ordered = sorted(scores, key=lambda pair_id: (-scores[pair_id], pair_id))
        cutoff = scores[ordered[k - 1]]
        above = {pair_id for pair_id, value in scores.items() if value > cutoff}
        tied = {pair_id for pair_id, value in scores.items() if value == cutoff}
        return above, tied, k - len(above)

    left_above, left_tied, left_need = cutoff_sets(left_scores)
    right_above, right_tied, _right_need = cutoff_sets(right_scores)
    base = len(left_above & right_above)
    minima = []
    maxima = []
    for candidate in right_tied:
        right_selected = right_above | {candidate}
        overlap_candidates = len(left_tied & right_selected)
        avoidable = len(left_tied - right_selected)
        minima.append(base + max(0, left_need - avoidable))
        maxima.append(base + min(left_need, overlap_candidates))
    return min(minima), max(maxima)


def strong_judge_rank_agreement(led: ledger.Ledger) -> str:
    left_scores, right_scores = shortlist_scores(led)
    shared = sorted(set(left_scores) & set(right_scores))
    rho = spearman([left_scores[pair_id] for pair_id in shared], [right_scores[pair_id] for pair_id in shared])
    low, high = tie_aware_overlap_range(left_scores, right_scores, 100)
    return (
        f"Opus/GPT Spearman {rho:.3f}; tie-aware top-100 overlap {low} to {high}; "
        f"random expectation {100 * 100 / len(shared):.2f}"
    )


def shortlist_snapshot() -> dict[str, object]:
    return json.loads((PAPER / "shortlist_snapshot.json").read_text(encoding="utf-8"))


def shortlist_sensitivity() -> str:
    overlap = next(row for row in shortlist_snapshot()["selector_overlap"] if row["k"] == 100)
    return f"mean-rank and worst-rank top-100 overlap {overlap['overlap']} of 100"


def mean_rank_top_100_composition() -> str:
    row = next(entry for entry in shortlist_snapshot()["selectors"]["mean_rank"] if entry["k"] == 100)
    return (
        f"{row['distinct_qsl_papers']} QSL papers, {row['distinct_vendor_papers']} vendor artefacts, "
        f"{row['distinct_vendors']} vendors, and {row['largest_vendor_count']} IBM pair entries"
    )


def corpus_top_100_vendor_entries(led: ledger.Ledger) -> str:
    top_pairs = next(entry for entry in shortlist_snapshot()["selectors"]["mean_rank"] if entry["k"] == 100)["pair_ids"]
    vendor_by_edge = edge_vendor_map(VENDOR_EDGES)
    corpus_counts: dict[str, set[str]] = defaultdict(set)
    top_counts: Counter[str] = Counter()
    for pair_id, row in led.pairs.items():
        vendor = vendor_by_edge[row["c2"]["item_id"].split(".", 1)[0]]
        corpus_counts[vendor].add(row["c2"]["item_id"])
    for pair_id in top_pairs:
        vendor = vendor_by_edge[
            led.pairs[pair_id]["c2"]["item_id"].split(".", 1)[0]
        ]
        top_counts[vendor] += 1
    parts = [
        f"{PRIMARY_VENDOR_LABELS[vendor]} {len(corpus_counts[vendor])}/{top_counts[vendor]}"
        for vendor in PRIMARY_VENDORS
    ]
    other_corpus = sum(len(records) for vendor, records in corpus_counts.items() if vendor not in PRIMARY_VENDORS)
    other_top = sum(count for vendor, count in top_counts.items() if vendor not in PRIMARY_VENDORS)
    parts.append(f"Other {other_corpus}/{other_top}")
    return "; ".join(parts)


def panel_retention() -> str:
    report = json.loads((PAPER / "phase_b_within_panel_priority_stability.json").read_text(encoding="utf-8"))
    policies = report["comparison"]["one_read_policies"]["policies"]
    opus = policies["opus_alone"]
    gpt = policies["gpt_alone"]
    combined = policies["equal_mean_rank"]
    def pct_int(value: float) -> int:
        return round(100 * value)
    return (
        f"Opus {pct_int(opus['top_10']['mean'])}/{pct_int(opus['top_20']['mean'])}/{pct_int(opus['top_50']['mean'])}; "
        f"GPT {pct_int(gpt['top_10']['mean'])}/{pct_int(gpt['top_20']['mean'])}/{pct_int(gpt['top_50']['mean'])}; "
        f"combined {pct_int(combined['top_10']['mean'])}/{pct_int(combined['top_20']['mean'])}/{pct_int(combined['top_50']['mean'])}"
    )


def check_result(name: str, actual: str) -> None:
    expected = EXPECTED_RESULTS[name]
    if actual != expected:
        raise SystemExit(f"{name} mismatch: expected {expected!r}, got {actual!r}")
    print(f"- {name}: {actual}")


def main() -> None:
    manifest = load_manifest()
    verify_manifest_imports(manifest)
    verify_input_hashes()
    verify_instrument_hashes()
    led = ledger.load_ledger(LEDGER)
    print("reported analysis:")
    check_result("corpus and pair counts", corpus_and_pair_counts(led))
    check_result("Haiku survivor counts", haiku_survivor_counts())
    check_result("calibration at 0.05", calibration_summary(led, 0.05))
    check_result("calibration at 0.15", calibration_summary(led, 0.15))
    check_result("strong-judge score scale", strong_judge_score_scale(led))
    check_result("strong-judge rank agreement", strong_judge_rank_agreement(led))
    check_result("shortlist sensitivity", shortlist_sensitivity())
    check_result("mean-rank top-100 composition", mean_rank_top_100_composition())
    check_result("corpus/top-100 vendor entries", corpus_top_100_vendor_entries(led))
    check_result("top-10/20/50 panel retention", panel_retention())
    print("analysis replay checks pass.")
    print("reported tables and statistics match the released paper.")


if __name__ == "__main__":
    main()
