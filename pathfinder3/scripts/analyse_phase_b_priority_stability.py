"""Freeze and measure the initial Phase B tranche priority policy.

The six v5 repeat runs cover the already selected 100-pair tranche, not the
other 903 pairs in the frozen canonical population. This script therefore
does two deliberately separate things:

1. reproduce the audited equal-mean-rank top-100 membership deterministically;
2. measure only within-panel top-K priority retention under repeat reads.

The resulting policy uses both judges for frozen membership, orders the
tranche by the more repeatable observed Opus ranking, and carries GPT rank and
the observed inter-judge gap as descriptive metadata. It makes no claim that
K=100 is optimal or that a new full-population sweep would select the same
members.

Usage:
    python3 pathfinder3/scripts/analyse_phase_b_priority_stability.py
    python3 pathfinder3/scripts/analyse_phase_b_priority_stability.py --check
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import statistics
import sys
from pathlib import Path

import ledger
from _common import P3, REPO
from analyse_judge_stability import pair_set_sha256, percentile_map
from build_shortlist_snapshot import judge_top_k


OPUS_ID = "b99996c890becb87ec59a144e419a013a8284c98bbf7e3656c830f52526ad5f0"
GPT_ID = "d19bb7d063eb10e65b6e8aa0944bc2a5767494a67c743d213268a6d365caa0b0"
OUTPUT_CONTRACT_SHA256 = (
    "2ae297b3c6ea5ea31ba25339c390b3d93a8e126775a19fe27c0117280a192f72"
)
EXPECTED_SHARED_PAIR_SET_SHA256 = (
    "2b505ee3d5a1e443be8e19a808fc2d3984e96d28b798f858cf788482dd23a7ff"
)
EXPECTED_TRANCHE_PAIR_SET_SHA256 = (
    "86326cc29b3e11003d4d92ee43ef03a515608ba7c3f5e15b54b71369b85f1d7b"
)
PERCENTILE_FORMULA = "(below + (1 + equal) / 2) / N"
KS = (10, 20, 50)

INSTRUMENTS = {
    "opus": {
        "instrument_id": OPUS_ID,
        "model_id": "claude-opus-5",
        "canonical_runs": (
            ("fg2-smoke-opus5_fg", 3),
            ("fg2-opus5_fg", 1000),
        ),
        "repeat_runs": (
            ("selfcons-v5-opus5-r1", 100),
            ("selfcons-v5-opus5-r2", 100),
        ),
    },
    "gpt": {
        "instrument_id": GPT_ID,
        "model_id": "gpt-5.6-sol",
        "canonical_runs": (
            ("fg2-smoke-gpt56_fg", 3),
            ("fg2-gpt56_fg", 912),
            ("fg2-gpt56_fg-resume", 88),
        ),
        "repeat_runs": (
            ("selfcons-v5-gpt56-r1", 100),
            ("selfcons-v5-gpt56-r2", 100),
            ("selfcons-v5-gpt56-r3", 100),
            ("selfcons-v5-gpt56-r4", 100),
        ),
    },
}

FROZEN_REPEAT_MANIFEST = P3 / "protocol" / "judge_stability_gpt_v5_manifest.json"
DEFAULT_JSON_OUT = P3 / "paper" / "phase_b_within_panel_priority_stability.json"
DEFAULT_MD_OUT = P3 / "paper" / "phase_b_within_panel_priority_stability.md"
DEFAULT_MANIFEST_DIR = P3 / "manifests"


class AnalysisError(RuntimeError):
    """The frozen evidence no longer satisfies the analysis contract."""


def canonical_json_bytes(value: object) -> bytes:
    """Canonical UTF-8 JSON used for semantic manifest digests."""
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def pretty_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def ordered_pair_list_sha256(pair_ids: list[str]) -> str:
    payload = "".join(f"{pair_id}\n" for pair_id in pair_ids)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _exact_run_events(
    led: ledger.Ledger,
    *,
    instrument_id: str,
    run_id: str,
    repeat: bool,
    expected_count: int,
) -> list[dict]:
    events = [
        event
        for event in led.verdicts
        if event.get("instrument_id") == instrument_id
        and event.get("run_id") == run_id
        and bool(event.get("repeat")) is repeat
    ]
    if len(events) != expected_count:
        raise AnalysisError(
            f"run {run_id!r} has {len(events)} matching rows; "
            f"expected {expected_count}"
        )
    pair_ids = [event["pair_id"] for event in events]
    if len(pair_ids) != len(set(pair_ids)):
        raise AnalysisError(f"run {run_id!r} contains duplicate pair IDs")
    return sorted(events, key=lambda event: event["pair_id"])


def semantic_run_sha256(events: list[dict]) -> str:
    rows = [
        {
            "corr": event["corr"],
            "instrument_id": event["instrument_id"],
            "int": event["int"],
            "pair_id": event["pair_id"],
            "repeat": bool(event.get("repeat")),
            "run_id": event["run_id"],
        }
        for event in sorted(events, key=lambda event: event["pair_id"])
    ]
    return sha256_bytes(canonical_json_bytes(rows))


def _load_instrument_evidence(led: ledger.Ledger, name: str) -> dict:
    config = INSTRUMENTS[name]
    instrument_id = str(config["instrument_id"])
    registered = led.instruments.get(instrument_id)
    if registered is None:
        raise AnalysisError(f"instrument {instrument_id} is not registered")
    if registered.get("model_id") != config["model_id"]:
        raise AnalysisError(
            f"instrument {name} model is {registered.get('model_id')!r}; "
            f"expected {config['model_id']!r}"
        )
    if registered.get("output_contract_sha256") != OUTPUT_CONTRACT_SHA256:
        raise AnalysisError(f"instrument {name} output contract changed")

    canonical_by_run: dict[str, list[dict]] = {}
    canonical: dict[str, dict] = {}
    for run_id, expected_count in config["canonical_runs"]:
        events = _exact_run_events(
            led,
            instrument_id=instrument_id,
            run_id=run_id,
            repeat=False,
            expected_count=expected_count,
        )
        canonical_by_run[run_id] = events
        for event in events:
            if event["pair_id"] in canonical:
                raise AnalysisError(
                    f"canonical runs for {name} duplicate {event['pair_id']!r}"
                )
            canonical[event["pair_id"]] = event

    repeats_by_run: dict[str, list[dict]] = {}
    for run_id, expected_count in config["repeat_runs"]:
        repeats_by_run[run_id] = _exact_run_events(
            led,
            instrument_id=instrument_id,
            run_id=run_id,
            repeat=True,
            expected_count=expected_count,
        )

    canonical_rank = percentile_map(
        [ledger.score(event) for event in canonical.values()]
    )
    canonical_percentiles = {
        pair_id: canonical_rank(ledger.score(event))
        for pair_id, event in canonical.items()
    }
    repeat_percentiles = {
        run_id: {
            event["pair_id"]: canonical_rank(ledger.score(event))
            for event in events
        }
        for run_id, events in repeats_by_run.items()
    }
    return {
        "config": config,
        "registered": registered,
        "canonical": canonical,
        "canonical_by_run": canonical_by_run,
        "canonical_percentiles": canonical_percentiles,
        "repeats_by_run": repeats_by_run,
        "repeat_percentiles": repeat_percentiles,
    }


def _assert_exact_pair_sets(opus: dict, gpt: dict, frozen_manifest: dict) -> list[str]:
    opus_canonical = set(opus["canonical"])
    gpt_canonical = set(gpt["canonical"])
    if len(opus_canonical) != 1003 or len(gpt_canonical) != 1003:
        raise AnalysisError("both frozen canonical references must contain 1,003 pairs")
    if opus_canonical != gpt_canonical:
        raise AnalysisError("the two frozen canonical references differ in membership")
    shared = sorted(opus_canonical)
    if pair_set_sha256(shared) != EXPECTED_SHARED_PAIR_SET_SHA256:
        raise AnalysisError("the frozen 1,003-pair population digest changed")

    repeat_sets: list[set[str]] = []
    for evidence in (opus, gpt):
        repeat_sets.extend(
            {event["pair_id"] for event in events}
            for events in evidence["repeats_by_run"].values()
        )
    first = repeat_sets[0]
    if any(pair_ids != first for pair_ids in repeat_sets[1:]):
        raise AnalysisError("the six repeat runs do not share one exact pair set")
    repeat_pair_ids = sorted(first)
    if len(repeat_pair_ids) != 100:
        raise AnalysisError("the repeat panel must contain exactly 100 pairs")
    if pair_set_sha256(repeat_pair_ids) != EXPECTED_TRANCHE_PAIR_SET_SHA256:
        raise AnalysisError("the repeat-panel pair-set digest changed")
    if frozen_manifest.get("pair_set_sha256") != EXPECTED_TRANCHE_PAIR_SET_SHA256:
        raise AnalysisError("the supporting repeat manifest digest changed")
    if sorted(frozen_manifest.get("pair_ids", [])) != repeat_pair_ids:
        raise AnalysisError("the supporting repeat manifest pair IDs changed")
    return shared


def load_frozen_evidence(led: ledger.Ledger) -> dict:
    frozen_manifest = json.loads(FROZEN_REPEAT_MANIFEST.read_text(encoding="utf-8"))
    opus = _load_instrument_evidence(led, "opus")
    gpt = _load_instrument_evidence(led, "gpt")
    shared = _assert_exact_pair_sets(opus, gpt, frozen_manifest)

    mean_percentiles = {
        pair_id: (
            opus["canonical_percentiles"][pair_id]
            + gpt["canonical_percentiles"][pair_id]
        )
        / 2.0
        for pair_id in shared
    }
    membership_order = judge_top_k(mean_percentiles, len(mean_percentiles))
    tranche = membership_order[:100]
    if pair_set_sha256(tranche) != EXPECTED_TRANCHE_PAIR_SET_SHA256:
        raise AnalysisError(
            "canonical equal-mean-rank top 100 no longer matches the repeat panel"
        )
    priority_scores = {
        pair_id: opus["canonical_percentiles"][pair_id] for pair_id in tranche
    }
    priority_order = judge_top_k(priority_scores, len(priority_scores))
    return {
        "opus": opus,
        "gpt": gpt,
        "pair_rows": led.pairs,
        "shared_pair_ids": shared,
        "membership_order": membership_order,
        "membership_percentiles": mean_percentiles,
        "tranche_pair_ids": sorted(tranche),
        "priority_order": priority_order,
    }


def measurement_noise_variance(
    reads: dict[str, dict[str, float]], pair_ids: list[str]
) -> float:
    if len(reads) < 2:
        raise AnalysisError("measurement variance needs at least two repeat reads")
    run_ids = sorted(reads)
    expected = set(pair_ids)
    for run_id in run_ids:
        if set(reads[run_id]) != expected:
            raise AnalysisError(f"repeat read {run_id!r} has a different pair set")
    variance = statistics.fmean(
        statistics.variance([reads[run_id][pair_id] for run_id in run_ids])
        for pair_id in pair_ids
    )
    if not math.isfinite(variance) or variance <= 0:
        raise AnalysisError("measurement variance must be positive and finite")
    return variance


def inverse_variance_weights(opus_variance: float, gpt_variance: float) -> dict[str, float]:
    if any(
        not math.isfinite(value) or value <= 0
        for value in (opus_variance, gpt_variance)
    ):
        raise AnalysisError("inverse-variance weights require positive finite variances")
    opus_precision = 1.0 / opus_variance
    gpt_precision = 1.0 / gpt_variance
    total = opus_precision + gpt_precision
    return {"opus": opus_precision / total, "gpt": gpt_precision / total}


def _top_set(scores: dict[str, float], k: int) -> set[str]:
    if not scores or k < 1 or k > len(scores):
        raise AnalysisError("K must lie inside a non-empty score map")
    if any(not math.isfinite(value) for value in scores.values()):
        raise AnalysisError("selector scores must be finite")
    return set(judge_top_k(scores, k))


def _retention(left: dict[str, float], right: dict[str, float], k: int) -> float:
    if set(left) != set(right):
        raise AnalysisError("retention score maps must contain the same pairs")
    return len(_top_set(left, k) & _top_set(right, k)) / k


def _weighted_scores(
    opus_scores: dict[str, float],
    gpt_scores: dict[str, float],
    opus_weight: float,
    gpt_weight: float,
) -> dict[str, float]:
    if set(opus_scores) != set(gpt_scores):
        raise AnalysisError("judge score maps must contain the same pairs")
    return {
        pair_id: opus_weight * opus_scores[pair_id] + gpt_weight * gpt_scores[pair_id]
        for pair_id in sorted(opus_scores)
    }


def _summary(values: dict[str, float]) -> dict:
    observed = list(values.values())
    return {
        "mean": statistics.fmean(observed),
        "min": min(observed),
        "max": max(observed),
        "by_ordered_assignment": values,
    }


def compare_one_read_policies(
    opus_reads: dict[str, dict[str, float]],
    gpt_reads: dict[str, dict[str, float]],
    weights: dict[str, float],
    ks: tuple[int, ...] = KS,
) -> dict:
    opus_run_ids = sorted(opus_reads)
    gpt_run_ids = sorted(gpt_reads)
    if len(opus_run_ids) != 2 or len(gpt_run_ids) != 4:
        raise AnalysisError("one-read comparison requires two Opus and four GPT reads")
    pair_ids = sorted(opus_reads[opus_run_ids[0]])
    if len(set(ks)) != len(ks) or any(k < 1 or k > len(pair_ids) for k in ks):
        raise AnalysisError("K values must be unique and valid for the repeat panel")

    policy_values = {
        "opus_alone": {k: {} for k in ks},
        "gpt_alone": {k: {} for k in ks},
        "equal_mean_rank": {k: {} for k in ks},
        "inverse_variance_rank": {k: {} for k in ks},
    }
    assignments = list(itertools.permutations(gpt_run_ids, 2))
    for gpt_left, gpt_right in assignments:
        label = f"{gpt_left}__{gpt_right}"
        left_opus = opus_reads[opus_run_ids[0]]
        right_opus = opus_reads[opus_run_ids[1]]
        left_gpt = gpt_reads[gpt_left]
        right_gpt = gpt_reads[gpt_right]
        score_pairs = {
            "opus_alone": (left_opus, right_opus),
            "gpt_alone": (left_gpt, right_gpt),
            "equal_mean_rank": (
                _weighted_scores(left_opus, left_gpt, 0.5, 0.5),
                _weighted_scores(right_opus, right_gpt, 0.5, 0.5),
            ),
            "inverse_variance_rank": (
                _weighted_scores(left_opus, left_gpt, weights["opus"], weights["gpt"]),
                _weighted_scores(right_opus, right_gpt, weights["opus"], weights["gpt"]),
            ),
        }
        for policy, (left, right) in score_pairs.items():
            for k in ks:
                policy_values[policy][k][label] = _retention(left, right, k)

    return {
        "ordered_assignments": len(assignments),
        "unique_opus_contrasts": 1,
        "unique_gpt_contrasts": math.comb(len(gpt_run_ids), 2),
        "policies": {
            policy: {f"top_{k}": _summary(values[k]) for k in ks}
            for policy, values in policy_values.items()
        },
    }


def compare_two_gpt_average(
    opus_reads: dict[str, dict[str, float]],
    gpt_reads: dict[str, dict[str, float]],
    ks: tuple[int, ...] = KS,
) -> dict:
    opus_run_ids = sorted(opus_reads)
    gpt_run_ids = sorted(gpt_reads)
    if len(opus_run_ids) != 2 or len(gpt_run_ids) != 4:
        raise AnalysisError("two-GPT comparison requires two Opus and four GPT reads")
    pair_ids = sorted(opus_reads[opus_run_ids[0]])
    if len(set(ks)) != len(ks) or any(k < 1 or k > len(pair_ids) for k in ks):
        raise AnalysisError("K values must be unique and valid for the repeat panel")

    by_k: dict[int, dict[str, float]] = {k: {} for k in ks}
    for left_runs in itertools.combinations(gpt_run_ids, 2):
        right_runs = tuple(run_id for run_id in gpt_run_ids if run_id not in left_runs)
        label = f"{'+'.join(left_runs)}__{'+'.join(right_runs)}"
        left_gpt = {
            pair_id: statistics.fmean(gpt_reads[run_id][pair_id] for run_id in left_runs)
            for pair_id in pair_ids
        }
        right_gpt = {
            pair_id: statistics.fmean(gpt_reads[run_id][pair_id] for run_id in right_runs)
            for pair_id in pair_ids
        }
        left = _weighted_scores(opus_reads[opus_run_ids[0]], left_gpt, 0.5, 0.5)
        right = _weighted_scores(opus_reads[opus_run_ids[1]], right_gpt, 0.5, 0.5)
        for k in ks:
            by_k[k][label] = _retention(left, right, k)
    return {
        "policy": "equal_mean_rank_with_two_gpt_reads",
        "ordered_assignments": math.comb(len(gpt_run_ids), 2),
        "unique_gpt_partitions": math.comb(len(gpt_run_ids), 2) // 2,
        "gpt_averaging": "average two mapped frozen-canonical percentiles",
        "retention": {f"top_{k}": _summary(by_k[k]) for k in ks},
    }


def pooled_d_within(reads: dict[str, dict[str, float]], pair_ids: list[str]) -> float:
    run_pairs = list(itertools.combinations(sorted(reads), 2))
    return statistics.fmean(
        statistics.fmean(
            abs(reads[left][pair_id] - reads[right][pair_id])
            for left, right in run_pairs
        )
        for pair_id in pair_ids
    )


def _run_provenance(evidence: dict) -> dict:
    out: dict[str, dict] = {}
    for kind, runs in (
        ("canonical", evidence["canonical_by_run"]),
        ("repeat", evidence["repeats_by_run"]),
    ):
        out[kind] = {
            run_id: {
                "count": len(events),
                "pair_set_sha256": pair_set_sha256(
                    [event["pair_id"] for event in events]
                ),
                "semantic_evidence_sha256": semantic_run_sha256(events),
            }
            for run_id, events in sorted(runs.items())
        }
    return out


def build_manifest_body(evidence: dict) -> dict:
    opus = evidence["opus"]
    gpt = evidence["gpt"]
    membership_position = {
        pair_id: index for index, pair_id in enumerate(evidence["membership_order"], 1)
    }
    priority_position = {
        pair_id: index for index, pair_id in enumerate(evidence["priority_order"], 1)
    }
    records = []
    for pair_id in evidence["priority_order"]:
        pair = evidence["pair_rows"][pair_id]
        opus_event = opus["canonical"][pair_id]
        gpt_event = gpt["canonical"][pair_id]
        opus_percentile = opus["canonical_percentiles"][pair_id]
        gpt_percentile = gpt["canonical_percentiles"][pair_id]
        records.append(
            {
                "corpus_snapshot": pair["corpus_snapshot"],
                "gpt": {
                    "canonical_run_id": gpt_event["run_id"],
                    "composite_score": ledger.score(gpt_event),
                    "feasibility": gpt_event["corr"],
                    "gain": gpt_event["int"],
                    "midrank_percentile": gpt_percentile,
                },
                "mean_membership_percentile": (
                    opus_percentile + gpt_percentile
                )
                / 2.0,
                "membership_rank": membership_position[pair_id],
                "observed_inter_judge_percentile_gap": abs(
                    opus_percentile - gpt_percentile
                ),
                "opus": {
                    "canonical_run_id": opus_event["run_id"],
                    "composite_score": ledger.score(opus_event),
                    "feasibility": opus_event["corr"],
                    "gain": opus_event["int"],
                    "midrank_percentile": opus_percentile,
                },
                "pair_id": pair_id,
                "priority_rank": priority_position[pair_id],
                "qsl_input_sha256": pair["c1"]["input_sha256"],
                "qsl_item_id": pair["c1"]["item_id"],
                "vendor_input_sha256": pair["c2"]["input_sha256"],
                "vendor_item_id": pair["c2"]["item_id"],
            }
        )

    selected = evidence["tranche_pair_ids"]
    ordered = evidence["priority_order"]
    return {
        "description": "Initial Phase B tranche with within-panel priority stability evidence",
        "evidence": {
            "gpt": {
                "instrument_id": GPT_ID,
                "model_id": INSTRUMENTS["gpt"]["model_id"],
                "output_contract_sha256": OUTPUT_CONTRACT_SHA256,
                "runs": _run_provenance(gpt),
            },
            "opus": {
                "instrument_id": OPUS_ID,
                "model_id": INSTRUMENTS["opus"]["model_id"],
                "output_contract_sha256": OUTPUT_CONTRACT_SHA256,
                "runs": _run_provenance(opus),
            },
            "supporting_repeat_manifest": {
                "path": "pathfinder3/protocol/judge_stability_gpt_v5_manifest.json",
                "sha256": sha256_bytes(FROZEN_REPEAT_MANIFEST.read_bytes()),
            },
        },
        "limitations": {
            "membership_stability": "unevaluated outside the selected 100-pair panel",
            "pair_level_gap": "descriptive only; not confirmed pair-level disagreement",
            "priority_stability": "observed on one Opus repeat contrast in this panel",
            "shortlist_size": "K=100 is an operational initial tranche, not an optimum",
        },
        "manifest_name": "phase_b_initial_tranche_v1_manifest",
        "membership": {
            "k": 100,
            "population_count": len(evidence["shared_pair_ids"]),
            "population_pair_set_sha256": pair_set_sha256(
                evidence["shared_pair_ids"]
            ),
            "rule": "descending equal mean of Opus and GPT midrank percentiles",
            "status": "deterministic operational reproduction of the audited selection",
            "tie_break": "ascending pair_id",
        },
        "pair_digests": {
            "ordered_pair_list_serialisation": "UTF-8 pair_id plus LF, in priority order",
            "ordered_pair_list_sha256": ordered_pair_list_sha256(ordered),
            "selected_pair_set_serialisation": "UTF-8 pair_id plus LF, sorted ascending",
            "selected_pair_set_sha256": pair_set_sha256(selected),
        },
        "pairs": records,
        "percentile": {
            "formula": PERCENTILE_FORMULA,
            "reference": "each judge's exact frozen 1,003-row canonical run union",
        },
        "policy_version": 1,
        "priority": {
            "rule": "descending Opus canonical midrank percentile within frozen membership",
            "status": "provisional, supported by within-panel repeat sensitivity",
            "tie_break": "ascending pair_id",
        },
    }


def manifest_envelope(body: dict) -> dict:
    body_sha256 = sha256_bytes(canonical_json_bytes(body))
    return {
        "body": body,
        "body_digest_contract": (
            "SHA-256 of UTF-8 JSON(body) with sorted keys, no insignificant "
            "whitespace, ensure_ascii=false, and no non-finite numbers"
        ),
        "body_sha256": body_sha256,
        "manifest_type": "phase_b_initial_tranche_v1_manifest",
        "schema_version": 1,
    }


def build_report(evidence: dict, manifest: dict, manifest_path: Path) -> dict:
    opus_reads = evidence["opus"]["repeat_percentiles"]
    gpt_reads = evidence["gpt"]["repeat_percentiles"]
    pair_ids = evidence["tranche_pair_ids"]
    opus_variance = measurement_noise_variance(opus_reads, pair_ids)
    gpt_variance = measurement_noise_variance(gpt_reads, pair_ids)
    weights = inverse_variance_weights(opus_variance, gpt_variance)
    one_read = compare_one_read_policies(opus_reads, gpt_reads, weights)
    two_gpt = compare_two_gpt_average(opus_reads, gpt_reads)
    return {
        "comparison": {
            "ks": list(KS),
            "one_read_policies": one_read,
            "two_gpt_average_policy": two_gpt,
        },
        "decision": {
            "membership": (
                "reproduce the audited canonical v5 equal-mean-rank top 100"
            ),
            "population_wide_membership_stability": "unevaluated",
            "priority": (
                "order the frozen tranche by Opus canonical midrank percentile; "
                "tie-break by ascending pair_id"
            ),
            "status": "provisional versioned initial-tranche policy",
            "use_of_gpt": (
                "membership input plus descriptive percentile and observed gap metadata; "
                "no automatic within-tranche reprioritisation"
            ),
        },
        "estimator": {
            "assignment_interpretation": (
                "exact sensitivity summaries, not independent trials or confidence intervals"
            ),
            "measurement_noise_variance": (
                "mean pair-level unbiased sample variance across repeat reads in "
                "frozen-percentile units"
            ),
            "percentile_formula": PERCENTILE_FORMULA,
            "two_gpt_average": (
                "map each read to its frozen percentile, average two GPT reads, "
                "then combine equally with Opus"
            ),
        },
        "evidence": {
            "canonical_population": len(evidence["shared_pair_ids"]),
            "canonical_pair_set_sha256": pair_set_sha256(
                evidence["shared_pair_ids"]
            ),
            "gpt_repeat_runs": sorted(gpt_reads),
            "opus_repeat_runs": sorted(opus_reads),
            "repeat_panel_pairs": len(pair_ids),
            "repeat_panel_pair_set_sha256": pair_set_sha256(pair_ids),
        },
        "limitations": [
            "The repeat panel contains no candidates below the original top-100 cutoff.",
            "Opus priority repeatability is observed from one repeat contrast.",
            "The observed inter-judge percentile gap is noisy pair-level metadata.",
            "Inverse-variance weighting is a panel diagnostic, not an optimal truth estimator.",
            "The analysis does not establish that K=100 is optimal.",
        ],
        "manifest": {
            "body_sha256": manifest["body_sha256"],
            "ordered_pair_list_sha256": manifest["body"]["pair_digests"]
            ["ordered_pair_list_sha256"],
            "path": manifest_path.relative_to(REPO).as_posix(),
            "selected_pair_set_sha256": manifest["body"]["pair_digests"]
            ["selected_pair_set_sha256"],
        },
        "noise_diagnostic": {
            "gpt_d_within": pooled_d_within(gpt_reads, pair_ids),
            "gpt_measurement_variance": gpt_variance,
            "inverse_variance_weights": weights,
            "opus_d_within": pooled_d_within(opus_reads, pair_ids),
            "opus_measurement_variance": opus_variance,
        },
        "report_type": "phase_b_within_panel_priority_stability",
        "schema_version": 1,
    }


def render_markdown(report: dict) -> str:
    policies = report["comparison"]["one_read_policies"]["policies"]
    two_gpt = report["comparison"]["two_gpt_average_policy"]["retention"]
    labels = {
        "opus_alone": "Opus alone",
        "gpt_alone": "GPT alone",
        "equal_mean_rank": "Equal mean rank",
        "inverse_variance_rank": "Inverse-variance rank (diagnostic)",
    }

    def pct(value: float) -> str:
        return f"{100 * value:.1f}%"

    rows = []
    for policy, label in labels.items():
        cells = []
        for k in KS:
            summary = policies[policy][f"top_{k}"]
            cells.append(
                f"{pct(summary['mean'])} "
                f"({pct(summary['min'])}--{pct(summary['max'])})"
            )
        rows.append(f"| {label} | {' | '.join(cells)} |")
    two_cells = []
    for k in KS:
        summary = two_gpt[f"top_{k}"]
        two_cells.append(
            f"{pct(summary['mean'])} ({pct(summary['min'])}--{pct(summary['max'])})"
        )
    rows.append(f"| Equal mean, two GPT reads | {' | '.join(two_cells)} |")

    weights = report["noise_diagnostic"]["inverse_variance_weights"]
    manifest_path = Path(report["manifest"]["path"])
    manifest_link = "../manifests/" + manifest_path.name
    lines = [
        "# Phase B within-panel priority stability",
        "",
        "## Decision",
        "",
        "Freeze a provisional initial tranche of 100 pairs by reproducing the",
        "audited canonical v5 equal-mean-rank membership. Within that fixed",
        "tranche, prioritise by Opus canonical midrank percentile and break ties",
        "by ascending `pair_id`. Keep GPT percentile and the observed inter-judge",
        "percentile gap as descriptive metadata only.",
        "",
        "This reduces observed within-tranche priority volatility while preserving",
        "both judges in the frozen membership decision. Population-wide membership",
        "stability remains **unevaluated**: the repeat panel contains only the",
        "already selected 100 pairs.",
        "",
        "## Observed priority retention",
        "",
        "Each cell is mean within-panel top-K retention, with the minimum and",
        "maximum across available ordered read assignments in parentheses.",
        "Assignments reuse reads and are exact sensitivity summaries, not",
        "independent trials or confidence intervals.",
        "",
        "| Policy | Top 10 | Top 20 | Top 50 |",
        "|---|---:|---:|---:|",
        *rows,
        "",
        "Opus alone retained 90.0%, 95.0%, and 94.0% at K=10, 20, and 50.",
        "It was more repeatable on this observed panel than every combination",
        "that allowed GPT to affect priority order. Averaging two GPT reads",
        "improved the equal-mean policy but did not close the gap.",
        "",
        "## Noise diagnostic",
        "",
        "The mean pair-level unbiased within-pair variance was",
        f"`{report['noise_diagnostic']['opus_measurement_variance']:.9f}` for Opus",
        f"and `{report['noise_diagnostic']['gpt_measurement_variance']:.9f}` for GPT",
        "in frozen-percentile units. A mechanical inverse-variance calculation",
        f"therefore assigns {pct(weights['opus'])} to Opus and {pct(weights['gpt'])}",
        "to GPT. This is diagnostic only: the judges have systematic differences,",
        "so the weights are not an optimal estimator of a shared truth.",
        "",
        "The corresponding pooled `D_within` values are",
        f"`{report['noise_diagnostic']['opus_d_within']:.9f}` for Opus and",
        f"`{report['noise_diagnostic']['gpt_d_within']:.9f}` for GPT.",
        "",
        "## Frozen artefact",
        "",
        f"The [content-addressed tranche manifest](../manifests/phase-b-initial-tranche-v1-8d42b2b961859358.json) records the",
        "exact reference and repeat runs, semantic evidence digests, selection",
        "formulae, set and order digests, input hashes, canonical scores,",
        "percentiles, ranks, and descriptive gap metadata.",
        "",
        f"- Manifest body: `{report['manifest']['body_sha256']}`",
        f"- Selected pair set: `{report['manifest']['selected_pair_set_sha256']}`",
        f"- Ordered pair list: `{report['manifest']['ordered_pair_list_sha256']}`",
        "",
        "## Limits",
        "",
        "- The analysis does not measure selection stability against the 903 pairs",
        "  below the original cutoff.",
        "- Opus repeatability is based on one repeat contrast in this panel.",
        "- A large pair-level GPT--Opus gap is not confirmed disagreement and does",
        "  not trigger automatic reprioritisation.",
        "- K=100 is a bounded initial tranche, not an empirically optimal size.",
        "",
        "## Regeneration",
        "",
        "```sh",
        "uv run python3 pathfinder3/scripts/analyse_phase_b_priority_stability.py",
        "uv run python3 pathfinder3/scripts/analyse_phase_b_priority_stability.py --check",
        "```",
        "",
    ]
    return "\n".join(lines)


def build_outputs(led: ledger.Ledger, manifest_dir: Path) -> tuple[dict, dict, Path, str]:
    evidence = load_frozen_evidence(led)
    body = build_manifest_body(evidence)
    manifest = manifest_envelope(body)
    manifest_path = manifest_dir / (
        f"phase-b-initial-tranche-v1-{manifest['body_sha256'][:16]}.json"
    )
    report = build_report(evidence, manifest, manifest_path)
    markdown = render_markdown(report)
    return report, manifest, manifest_path, markdown


def _write_or_check(path: Path, payload: bytes, *, check: bool) -> None:
    if check:
        if not path.exists():
            raise AnalysisError(f"missing generated output {path.relative_to(REPO)}")
        if path.read_bytes() != payload:
            raise AnalysisError(f"stale generated output {path.relative_to(REPO)}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if outputs are stale")
    parser.add_argument("--json-out", type=Path, default=DEFAULT_JSON_OUT)
    parser.add_argument("--markdown-out", type=Path, default=DEFAULT_MD_OUT)
    parser.add_argument("--manifest-dir", type=Path, default=DEFAULT_MANIFEST_DIR)
    args = parser.parse_args()

    report, manifest, manifest_path, markdown = build_outputs(
        ledger.load_ledger(), args.manifest_dir
    )
    _write_or_check(args.json_out, pretty_json_bytes(report), check=args.check)
    _write_or_check(manifest_path, pretty_json_bytes(manifest), check=args.check)
    _write_or_check(args.markdown_out, markdown.encode("utf-8"), check=args.check)
    action = "verified" if args.check else "wrote"
    print(f"{action} {args.json_out.relative_to(REPO)}")
    print(f"{action} {manifest_path.relative_to(REPO)}")
    print(f"{action} {args.markdown_out.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(main())
