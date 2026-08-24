"""Analyse Haiku, GPT, and Opus verdict agreement on their shared prefix.

The strong sweeps run in descending Haiku-score order. Until both finish, the
three-way comparison is therefore exact for the shared Haiku-selected prefix,
but it is not an unbiased estimate for the full corpus.

Outputs:

* a JSON record containing the measurements and snapshot provenance;
* a TeX fragment containing macros and table rows for the cascade note;
* a three-panel PNG showing GPT--Opus scores and Haiku-conditional summaries;
* a three-panel PNG showing tail-conditioned rank agreement and prefix recovery.

Usage:
    python3 pathfinder3/scripts/analyse_judge_agreement.py \\
        --ledger pathfinder3/ledger \\
        --gpt-instrument b2a053b3cd994b15dd9f976f8777479b6559e80391a840c1c39bd3dcdf8c7818 \\
        --opus-instrument 7f01f7e54dc02bbc59a447ea77c51c9fe8b16291b8d4e52e37ce1ed0b433b046 \\
        --json-out pathfinder3/build/judge-agreement-snapshot.json \\
        --tex-out notes/methodology/cascade-optimisation/judge-agreement-results.tex \\
        --plot-out notes/methodology/cascade-optimisation/judge-agreement-2026-07-27.png \\
        --rank-plot-out notes/methodology/cascade-optimisation/judge-tail-agreement-2026-07-27.png
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import ledger

HAIKU = "claude:claude-haiku-4-5"
GPT = "codex:gpt-5.6-sol"
OPUS = "claude:claude-opus-5"
HAIKU_PROMPT = "v2"
ROUND_DIGITS = 12


@dataclass
class TopKSelectionProfile:
    cutoff: float
    mandatory: set[str]
    tie: set[str]
    slots: int

    @property
    def band(self) -> set[str]:
        return self.mandatory | self.tie


def event_score(event: dict) -> float:
    return float(event["corr"]) * float(event["int"])


def rankdata(values: list[float]) -> list[float]:
    """Average ranks for ties, with rank one assigned to the smallest value."""
    keys = [round(value, ROUND_DIGITS) for value in values]
    order = sorted(range(len(keys)), key=keys.__getitem__)
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start + 1
        while end < len(order) and keys[order[end]] == keys[order[start]]:
            end += 1
        average_rank = (start + 1 + end) / 2
        for index in order[start:end]:
            ranks[index] = average_rank
        start = end
    return ranks


def pearson(left: list[float], right: list[float]) -> float:
    """Finite-set Pearson correlation."""
    if len(left) != len(right) or len(left) < 2:
        raise ValueError("Pearson correlation requires equally sized samples")
    mean_left = sum(left) / len(left)
    mean_right = sum(right) / len(right)
    centred_left = [value - mean_left for value in left]
    centred_right = [value - mean_right for value in right]
    numerator = sum(a * b for a, b in zip(centred_left, centred_right))
    denominator = math.sqrt(
        sum(value * value for value in centred_left)
        * sum(value * value for value in centred_right)
    )
    if denominator == 0:
        raise ValueError("correlation is undefined for a constant series")
    return numerator / denominator


def spearman(left: list[float], right: list[float]) -> float:
    """Spearman correlation as Pearson correlation of average ranks."""
    return pearson(rankdata(left), rankdata(right))


def nominal_k_grid(n: int, max_points: int = 240) -> list[int]:
    """Useful shortlist sizes, dense near the head and logarithmic thereafter."""
    if n < 2:
        return []
    dense_end = min(n, 100)
    values = set(range(2, dense_end + 1))
    if n > dense_end:
        values.update(
            int(round(value))
            for value in np.geomspace(dense_end + 1, n, max_points)
        )
    values.update(k for k in (10, 20, 40, 100, 200, 500, 1000, n)
                  if 2 <= k <= n)
    return sorted(values)


def top_k_selection_profile(
    scores: dict[str, float], k: int,
) -> TopKSelectionProfile:
    """Mandatory and cutoff-tied items for an exact size-k top selection."""
    if not scores:
        return TopKSelectionProfile(0.0, set(), set(), 0)
    if not 1 <= k <= len(scores):
        raise ValueError("k must lie between one and the number of scores")
    rounded = {
        pair_id: round(score, ROUND_DIGITS)
        for pair_id, score in scores.items()
    }
    cutoff = sorted(rounded.values(), reverse=True)[k - 1]
    mandatory = {
        pair_id for pair_id, score in rounded.items()
        if score > cutoff
    }
    tie = {
        pair_id for pair_id, score in rounded.items()
        if score == cutoff
    }
    slots = k - len(mandatory)
    if not 0 <= slots <= len(tie):
        raise ValueError("invalid cutoff-tie profile for exact size-k selection")
    return TopKSelectionProfile(cutoff, mandatory, tie, slots)


def tie_inclusive_prefix(scores: dict[str, float], k: int) -> set[str]:
    """Top nominal k, admitting every item tied at the kth score."""
    return top_k_selection_profile(scores, k).band


def tail_spearman_curve(
    reference: dict[str, float],
    left: dict[str, float],
    right: dict[str, float],
    *,
    nominal_ks: list[int] | None = None,
) -> list[dict]:
    """Spearman correlation within tie-inclusive reference-judge prefixes."""
    pair_ids = set(reference) & set(left) & set(right)
    ref = {pair_id: reference[pair_id] for pair_id in pair_ids}
    left_common = {pair_id: left[pair_id] for pair_id in pair_ids}
    right_common = {pair_id: right[pair_id] for pair_id in pair_ids}
    ks = nominal_ks if nominal_ks is not None else nominal_k_grid(len(pair_ids))
    rows = []
    previous_prefix: set[str] | None = None
    for k in ks:
        prefix = tie_inclusive_prefix(ref, k)
        if prefix == previous_prefix:
            continue
        previous_prefix = prefix
        ordered = sorted(prefix)
        try:
            rho = spearman(
                [left_common[pair_id] for pair_id in ordered],
                [right_common[pair_id] for pair_id in ordered],
            )
        except ValueError:
            # A first score tier is constant under the reference judge.
            continue
        rows.append({
            "nominal_k": k,
            "n": len(prefix),
            "cutoff": min(ref[pair_id] for pair_id in prefix),
            "spearman": rho,
        })
    return rows


def prefix_recovery_curve(
    reference: dict[str, float],
    comparison: dict[str, float],
    *,
    nominal_ks: list[int] | None = None,
) -> list[dict]:
    """Fraction of each reference prefix recovered at matched review capacity.

    The reference cutoff admits all ties.  The comparison judge is then given
    the resulting number of nominal slots and also admits all cutoff ties.
    """
    pair_ids = set(reference) & set(comparison)
    ref = {pair_id: reference[pair_id] for pair_id in pair_ids}
    other = {pair_id: comparison[pair_id] for pair_id in pair_ids}
    ks = nominal_ks if nominal_ks is not None else nominal_k_grid(len(pair_ids))
    rows = []
    previous_prefix: set[str] | None = None
    for k in ks:
        prefix = tie_inclusive_prefix(ref, k)
        if prefix == previous_prefix:
            continue
        previous_prefix = prefix
        comparison_prefix = tie_inclusive_prefix(other, len(prefix))
        overlap = len(prefix & comparison_prefix)
        rows.append({
            "nominal_k": k,
            "n": len(prefix),
            "comparison_n": len(comparison_prefix),
            "overlap": overlap,
            "recovery": overlap / len(prefix),
        })
    return rows


def threshold_agreement(
    left: dict[str, float], right: dict[str, float], threshold: float,
) -> dict:
    """Confusion counts and Cohen's kappa on the common pair IDs."""
    pair_ids = sorted(set(left) & set(right))
    both_positive = left_only = right_only = both_negative = 0
    for pair_id in pair_ids:
        a = left[pair_id] >= threshold
        b = right[pair_id] >= threshold
        if a and b:
            both_positive += 1
        elif a:
            left_only += 1
        elif b:
            right_only += 1
        else:
            both_negative += 1
    n = len(pair_ids)
    observed = (both_positive + both_negative) / n
    p_left = (both_positive + left_only) / n
    p_right = (both_positive + right_only) / n
    expected = p_left * p_right + (1 - p_left) * (1 - p_right)
    kappa = (observed - expected) / (1 - expected) if expected < 1 else 1.0
    positive_denominator = 2 * both_positive + left_only + right_only
    positive_agreement = (
        2 * both_positive / positive_denominator
        if positive_denominator else 1.0
    )
    return {
        "n": n,
        "both_positive": both_positive,
        "left_only": left_only,
        "right_only": right_only,
        "both_negative": both_negative,
        "observed_agreement": observed,
        "expected_agreement": expected,
        "kappa": kappa,
        "positive_agreement": positive_agreement,
        "left_positive_rate": p_left,
        "right_positive_rate": p_right,
    }


def top_ids(scores: dict[str, float], k: int) -> list[str]:
    """Top k IDs with pair ID as an explicit deterministic tie-break."""
    return sorted(scores, key=lambda pair_id: (-scores[pair_id], pair_id))[:k]


def _feasible_overlap_bounds(
    left: TopKSelectionProfile, right: TopKSelectionProfile,
) -> tuple[int, int]:
    fixed = len(left.mandatory & right.mandatory)
    left_with_right_mandatory = len(left.tie & right.mandatory)
    right_with_left_mandatory = len(right.tie & left.mandatory)
    shared_tie = len(left.tie & right.tie)
    left_only = len(left.tie - (right.mandatory | right.tie))
    right_only = len(right.tie - (left.mandatory | left.tie))

    left_shared_min = max(0, left.slots - left_with_right_mandatory - left_only)
    left_shared_max = min(shared_tie, left.slots)
    right_shared_min = max(
        0, right.slots - right_with_left_mandatory - right_only)
    right_shared_max = min(shared_tie, right.slots)

    feasible_min = feasible_max = None
    for left_shared in range(left_shared_min, left_shared_max + 1):
        left_mandatory_min = max(0, left.slots - left_shared - left_only)
        left_mandatory_max = min(
            left_with_right_mandatory,
            left.slots - left_shared,
        )
        for right_shared in range(right_shared_min, right_shared_max + 1):
            right_mandatory_min = max(
                0, right.slots - right_shared - right_only)
            right_mandatory_max = min(
                right_with_left_mandatory,
                right.slots - right_shared,
            )
            overlap_min = (
                fixed
                + left_mandatory_min
                + right_mandatory_min
                + max(0, left_shared + right_shared - shared_tie)
            )
            overlap_max = (
                fixed
                + left_mandatory_max
                + right_mandatory_max
                + min(left_shared, right_shared)
            )
            feasible_min = (
                overlap_min if feasible_min is None
                else min(feasible_min, overlap_min)
            )
            feasible_max = (
                overlap_max if feasible_max is None
                else max(feasible_max, overlap_max)
            )
    if feasible_min is None or feasible_max is None:
        raise ValueError("no feasible cutoff-tie resolution for exact size-k selection")
    return feasible_min, feasible_max


def feasible_top_k_overlap(
    left: dict[str, float], right: dict[str, float], k: int,
) -> tuple[int, int]:
    """Min/max overlap across exact size-k selections with arbitrary cutoff ties."""
    pair_ids = set(left) & set(right)
    if not 1 <= k <= len(pair_ids):
        raise ValueError("k must lie between one and the number of common scores")
    left_common = {pair_id: left[pair_id] for pair_id in pair_ids}
    right_common = {pair_id: right[pair_id] for pair_id in pair_ids}
    return _feasible_overlap_bounds(
        top_k_selection_profile(left_common, k),
        top_k_selection_profile(right_common, k),
    )


def top_overlap(
    left: dict[str, float], right: dict[str, float], k: int,
) -> dict:
    """Exact top-k overlap plus tie-aware feasible overlap bounds."""
    pair_ids = set(left) & set(right)
    if not 1 <= k <= len(pair_ids):
        raise ValueError("k must lie between one and the number of common scores")
    left_common = {pair_id: left[pair_id] for pair_id in pair_ids}
    right_common = {pair_id: right[pair_id] for pair_id in pair_ids}
    left_profile = top_k_selection_profile(left_common, k)
    right_profile = top_k_selection_profile(right_common, k)
    left_top = top_ids(left_common, k)
    right_top = top_ids(right_common, k)
    exact = len(set(left_top) & set(right_top))
    feasible_min, feasible_max = _feasible_overlap_bounds(
        left_profile, right_profile)
    left_cutoff = left_common[left_top[-1]]
    right_cutoff = right_common[right_top[-1]]
    left_band = left_profile.band
    right_band = right_profile.band
    return {
        "n": len(pair_ids),
        "k": k,
        "overlap": exact,
        "jaccard": exact / (2 * k - exact),
        "random_expected_overlap": k * k / len(pair_ids),
        "left_cutoff": left_cutoff,
        "right_cutoff": right_cutoff,
        "left_band_size": len(left_band),
        "right_band_size": len(right_band),
        "tie_band_overlap": len(left_band & right_band),
        "feasible_overlap_min": feasible_min,
        "feasible_overlap_max": feasible_max,
    }


def _same_event(previous: dict, current: dict) -> bool:
    keys = (
        "pair_id", "judge", "prompt_version", "instrument_id",
        "corr", "int", "score_explanation",
    )
    return all(previous.get(key) == current.get(key) for key in keys)


def resolve_instrument_prefix(
    instrument_judges: dict[str, str], prefix: str, judge: str,
) -> str:
    """Resolve one explicit instrument prefix for the named judge."""
    matches = sorted(
        instrument_id
        for instrument_id, instrument_judge in instrument_judges.items()
        if instrument_judge == judge and instrument_id.startswith(prefix)
    )
    if not matches:
        raise ValueError(f"no {judge} instrument matches {prefix!r}")
    if len(matches) > 1:
        raise ValueError(
            f"{judge} instrument prefix {prefix!r} matches {len(matches)} "
            "instruments; give a longer prefix"
        )
    return matches[0]


def load_snapshot(
    ledger_dirs: list[Path], *, gpt_instrument: str, opus_instrument: str,
) -> tuple[dict[str, dict], dict[str, dict[str, dict]], dict[str, str]]:
    """Return Haiku events plus the explicitly selected strong series."""
    haiku: dict[str, dict] = {}
    by_instrument: dict[str, dict[str, dict]] = defaultdict(dict)
    instrument_judges: dict[str, str] = {}
    for directory in ledger_dirs:
        loaded = ledger.load_ledger(directory)
        for event in loaded.canonical_by_series().values():
            pair_id = event["pair_id"]
            if (event["judge"] == HAIKU
                    and event.get("prompt_version") == HAIKU_PROMPT
                    and event.get("instrument_id") is None):
                previous = haiku.get(pair_id)
                if previous is not None and not _same_event(previous, event):
                    raise ValueError(f"conflicting Haiku verdict for {pair_id}")
                haiku[pair_id] = event
            if (event.get("role") != "strong_opinion"
                    or event.get("instrument_id") is None
                    or event.get("repeat")):
                continue
            instrument = event["instrument_id"]
            previous_judge = instrument_judges.get(instrument)
            if previous_judge is not None and previous_judge != event["judge"]:
                raise ValueError(
                    f"instrument {instrument} appears under both "
                    f"{previous_judge} and {event['judge']}"
                )
            instrument_judges[instrument] = event["judge"]
            previous = by_instrument[instrument].get(pair_id)
            if previous is not None and not _same_event(previous, event):
                raise ValueError(
                    f"conflicting verdict for {pair_id}, {instrument}")
            by_instrument[instrument][pair_id] = event

    selected_ids = {
        GPT: resolve_instrument_prefix(instrument_judges, gpt_instrument, GPT),
        OPUS: resolve_instrument_prefix(instrument_judges, opus_instrument, OPUS),
    }
    selected = {
        judge: by_instrument[instrument_id]
        for judge, instrument_id in selected_ids.items()
    }
    return haiku, selected, selected_ids


def score_map(events: dict[str, dict], field: str = "score") -> dict[str, float]:
    if field == "score":
        return {pair_id: event_score(event) for pair_id, event in events.items()}
    return {pair_id: float(event[field]) for pair_id, event in events.items()}


def score_comparison(
    left: dict[str, float], right: dict[str, float],
) -> dict:
    pair_ids = sorted(set(left) & set(right))
    a = [left[pair_id] for pair_id in pair_ids]
    b = [right[pair_id] for pair_id in pair_ids]
    differences = [x - y for x, y in zip(a, b)]
    return {
        "n": len(pair_ids),
        "pearson": pearson(a, b),
        "spearman": spearman(a, b),
        "mean_left_minus_right": sum(differences) / len(differences),
        "mean_absolute_difference": (
            sum(abs(value) for value in differences) / len(differences)
        ),
    }


def binned_summary(
    haiku: dict[str, float], gpt: dict[str, float], opus: dict[str, float],
    *, bins: int, threshold: float,
) -> list[dict]:
    """Fixed-width Haiku bins on the three-way common prefix."""
    shared = set(haiku) & set(gpt) & set(opus)
    rows = []
    for index in range(bins):
        low, high = index / bins, (index + 1) / bins
        pair_ids = [
            pair_id for pair_id in shared
            if low <= haiku[pair_id] < high
            or (index == bins - 1 and haiku[pair_id] == 1)
        ]
        if not pair_ids:
            continue
        rows.append({
            "low": low,
            "high": high,
            "mid": (low + high) / 2,
            "n": len(pair_ids),
            "haiku_mean": sum(haiku[p] for p in pair_ids) / len(pair_ids),
            "gpt_mean": sum(gpt[p] for p in pair_ids) / len(pair_ids),
            "opus_mean": sum(opus[p] for p in pair_ids) / len(pair_ids),
            "gpt_positive_rate": (
                sum(gpt[p] >= threshold for p in pair_ids) / len(pair_ids)
            ),
            "opus_positive_rate": (
                sum(opus[p] >= threshold for p in pair_ids) / len(pair_ids)
            ),
        })
    return rows


def analyse(
    haiku_events: dict[str, dict], strong: dict[str, dict[str, dict]],
    *, threshold: float, top_k: int, bins: int, provenance: dict | None = None,
) -> dict:
    event_maps = {HAIKU: haiku_events, GPT: strong[GPT], OPUS: strong[OPUS]}
    scores = {judge: score_map(events) for judge, events in event_maps.items()}
    shared = sorted(set(scores[HAIKU]) & set(scores[GPT]) & set(scores[OPUS]))
    restricted = {
        judge: {pair_id: values[pair_id] for pair_id in shared}
        for judge, values in scores.items()
    }
    consensus = {
        pair_id: (restricted[GPT][pair_id] + restricted[OPUS][pair_id]) / 2
        for pair_id in shared
    }
    labels = {HAIKU: "Haiku", GPT: "GPT-5.6", OPUS: "Opus 5"}
    pairs = ((HAIKU, GPT), (HAIKU, OPUS), (GPT, OPUS))
    score_stats = {}
    for left, right in pairs:
        key = f"{labels[left]}--{labels[right]}"
        score_stats[key] = {}
        for field, display in (
            ("score", "product"), ("corr", "corr"), ("int", "int"),
        ):
            left_values = (
                restricted[left] if field == "score"
                else {p: float(event_maps[left][p][field]) for p in shared}
            )
            right_values = (
                restricted[right] if field == "score"
                else {p: float(event_maps[right][p][field]) for p in shared}
            )
            score_stats[key][display] = score_comparison(
                left_values, right_values)

    agreement = {
        f"{labels[left]}--{labels[right]}": threshold_agreement(
            restricted[left], restricted[right], threshold)
        for left, right in pairs
    }
    overlap = {
        "Haiku--GPT-5.6": top_overlap(
            restricted[HAIKU], restricted[GPT], top_k),
        "Haiku--Opus 5": top_overlap(
            restricted[HAIKU], restricted[OPUS], top_k),
        "GPT-5.6--Opus 5": top_overlap(
            restricted[GPT], restricted[OPUS], top_k),
        "Haiku--strong mean": top_overlap(
            restricted[HAIKU], consensus, top_k),
    }
    tail_spearman = {
        "Opus-defined": tail_spearman_curve(
            restricted[OPUS], restricted[GPT], restricted[OPUS]),
        "GPT-defined": tail_spearman_curve(
            restricted[GPT], restricted[GPT], restricted[OPUS]),
    }
    prefix_recovery = {
        "Opus recovered by GPT": prefix_recovery_curve(
            restricted[OPUS], restricted[GPT]),
        "GPT recovered by Opus": prefix_recovery_curve(
            restricted[GPT], restricted[OPUS]),
    }
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "selection": "three-way shared Haiku-ranked prefix",
        "corpus_pairs": len(scores[HAIKU]),
        "judge_counts": {labels[judge]: len(values)
                         for judge, values in scores.items()},
        "shared_pairs": len(shared),
        "threshold": threshold,
        "top_k": top_k,
        "score_comparisons": score_stats,
        "threshold_agreement": agreement,
        "top_k_overlap": overlap,
        "tail_spearman": tail_spearman,
        "prefix_recovery": prefix_recovery,
        "bins": binned_summary(
            restricted[HAIKU], restricted[GPT], restricted[OPUS],
            bins=bins, threshold=threshold),
        "scores": {
            "Haiku": restricted[HAIKU],
            "GPT-5.6": restricted[GPT],
            "Opus 5": restricted[OPUS],
        },
    }
    if provenance is not None:
        result["provenance"] = provenance
    return result


def write_tex(results: dict, path: Path) -> None:
    def pct(value: float) -> str:
        return f"{100 * value:.1f}\\%"

    def feasible_interval(values: dict) -> str:
        lower = values["feasible_overlap_min"]
        upper = values["feasible_overlap_max"]
        return str(lower) if lower == upper else f"{lower}--{upper}"

    score_rows = []
    for pair, fields in results["score_comparisons"].items():
        for field, values in fields.items():
            score_rows.append(
                f"{pair} & {field} & {values['pearson']:.3f} & "
                f"{values['spearman']:.3f} & "
                f"{values['mean_absolute_difference']:.3f} \\\\")
    agreement_rows = []
    for pair, values in results["threshold_agreement"].items():
        agreement_rows.append(
            f"{pair} & {pct(values['left_positive_rate'])} & "
            f"{pct(values['right_positive_rate'])} & "
            f"{pct(values['observed_agreement'])} & "
            f"{values['kappa']:.3f} & "
            f"{pct(values['positive_agreement'])} \\\\")
    overlap_rows = []
    for pair, values in results["top_k_overlap"].items():
        overlap_rows.append(
            f"{pair} & {values['overlap']} & {values['jaccard']:.3f} & "
            f"{values['random_expected_overlap']:.2f} & "
            f"{values['tie_band_overlap']} "
            f"({values['left_band_size']}/{values['right_band_size']}; "
            f"feasible {feasible_interval(values)}) \\\\")

    generated = datetime.fromisoformat(results["generated_at"])
    body = "\n".join([
        "% Generated by pathfinder3/scripts/analyse_judge_agreement.py.",
        "% Regenerate rather than editing these measurements by hand.",
        rf"\newcommand{{\JudgeSnapshotDate}}{{{generated:%d %B %Y, %H:%M UTC}}}",
        rf"\newcommand{{\JudgeSnapshotShared}}{{{results['shared_pairs']:,}}}",
        rf"\newcommand{{\JudgeSnapshotCorpus}}{{{results['corpus_pairs']:,}}}",
        rf"\newcommand{{\JudgeSnapshotGPT}}{{{results['judge_counts']['GPT-5.6']:,}}}",
        rf"\newcommand{{\JudgeSnapshotOpus}}{{{results['judge_counts']['Opus 5']:,}}}",
        rf"\newcommand{{\JudgeThreshold}}{{{results['threshold']:.2f}}}",
        rf"\newcommand{{\JudgeTopK}}{{{results['top_k']}}}",
        "\\newcommand{\\JudgeScoreRows}{%",
        *score_rows,
        "}",
        "\\newcommand{\\JudgeAgreementRows}{%",
        *agreement_rows,
        "}",
        "\\newcommand{\\JudgeOverlapRows}{%",
        *overlap_rows,
        "}",
        "",
    ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


def write_plot(results: dict, path: Path) -> None:
    scores = results["scores"]
    pair_ids = sorted(scores["Haiku"])
    gpt = np.array([scores["GPT-5.6"][pair_id] for pair_id in pair_ids])
    opus = np.array([scores["Opus 5"][pair_id] for pair_id in pair_ids])
    bins = results["bins"]

    fig, axes = plt.subplots(1, 3, figsize=(13.2, 4.1))
    left, middle, right = axes
    left.scatter(gpt, opus, s=9, alpha=0.22, color="#495057", edgecolors="none")
    left.plot([0, 1], [0, 1], color="#c92a2a", lw=1, ls="--")
    left.set(
        title="Strong-judge score pairs",
        xlabel=r"GPT score $G$",
        ylabel=r"Opus score $O$",
        xlim=(-0.02, 1.02),
        ylim=(-0.02, 1.02),
    )

    centres = [row["mid"] for row in bins]
    widths = [0.85 * (row["high"] - row["low"]) for row in bins]
    middle.bar(
        np.array(centres) - np.array(widths) / 4,
        [row["gpt_mean"] for row in bins],
        width=np.array(widths) / 2,
        label="GPT", color="#1971c2",
    )
    middle.bar(
        np.array(centres) + np.array(widths) / 4,
        [row["opus_mean"] for row in bins],
        width=np.array(widths) / 2,
        label="Opus", color="#2b8a3e",
    )
    middle.set(
        title=r"Mean strong score conditional on $H$ bin",
        xlabel=r"Haiku score $H$",
        ylabel="mean score",
        xlim=(-0.02, 1.02),
        ylim=(0, 1),
    )
    middle.legend()

    right.bar(
        np.array(centres) - np.array(widths) / 4,
        [row["gpt_positive_rate"] for row in bins],
        width=np.array(widths) / 2,
        label="GPT", color="#1971c2",
    )
    right.bar(
        np.array(centres) + np.array(widths) / 4,
        [row["opus_positive_rate"] for row in bins],
        width=np.array(widths) / 2,
        label="Opus", color="#2b8a3e",
    )
    right.set(
        title=rf"Strong-positive rate at $s\geq{results['threshold']:.2f}$",
        xlabel=r"Haiku score $H$",
        ylabel="fraction positive",
        xlim=(-0.02, 1.02),
        ylim=(0, 1),
    )
    right.legend()
    for axis in axes:
        axis.grid(alpha=0.2)
    fig.suptitle(
        f"Pathfinder3 judge agreement on {results['shared_pairs']:,} "
        "three-way shared pairs",
        fontsize=13,
    )
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=170, bbox_inches="tight")
    plt.close(fig)


def write_rank_plot(results: dict, path: Path) -> None:
    """Plot judge-centred tail correlations and reciprocal prefix recovery."""
    global_rho = results["score_comparisons"]["GPT-5.6--Opus 5"]["product"][
        "spearman"
    ]
    fig, axes = plt.subplots(1, 3, figsize=(13.2, 4.1))
    panels = (
        ("Opus-defined", "Pairs admitted by Opus score"),
        ("GPT-defined", "Pairs admitted by GPT score"),
    )
    colours = {"Opus-defined": "#2b8a3e", "GPT-defined": "#1971c2"}
    for axis, (key, xlabel) in zip(axes[:2], panels):
        rows = results["tail_spearman"][key]
        axis.plot(
            [row["n"] for row in rows],
            [row["spearman"] for row in rows],
            color=colours[key],
            lw=2,
        )
        axis.axhline(
            global_rho, color="#868e96", lw=1, ls="--",
            label=rf"global $\rho={global_rho:.3f}$",
        )
        axis.set(
            title=rf"$\rho(G,O)$ within {key.split('-')[0]} prefix",
            xlabel=xlabel,
            ylabel=r"Spearman $\rho$",
            xscale="log",
            ylim=(-1.02, 1.02),
        )
        axis.legend(loc="lower right")

    recovery = results["prefix_recovery"]
    axes[2].plot(
        [row["n"] for row in recovery["Opus recovered by GPT"]],
        [row["recovery"] for row in recovery["Opus recovered by GPT"]],
        color="#2b8a3e",
        lw=2,
        label="Opus prefix recovered by GPT",
    )
    axes[2].plot(
        [row["n"] for row in recovery["GPT recovered by Opus"]],
        [row["recovery"] for row in recovery["GPT recovered by Opus"]],
        color="#1971c2",
        lw=2,
        label="GPT prefix recovered by Opus",
    )
    axes[2].set(
        title="Cross-judge prefix recovery",
        xlabel="Pairs in reference prefix",
        ylabel="fraction recovered",
        xscale="log",
        ylim=(-0.02, 1.02),
    )
    axes[2].legend(loc="lower right")
    for axis in axes:
        axis.grid(alpha=0.2)
    fig.suptitle(
        "GPT–Opus agreement as the admitted prefix grows "
        "(cutoff ties retained)",
        fontsize=13,
    )
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=170, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", action="append", required=True, type=Path)
    parser.add_argument(
        "--gpt-instrument", required=True, metavar="ID_PREFIX",
        help="unique GPT instrument_id prefix for the strong series to analyse",
    )
    parser.add_argument(
        "--opus-instrument", required=True, metavar="ID_PREFIX",
        help="unique Opus instrument_id prefix for the strong series to analyse",
    )
    parser.add_argument("--json-out", required=True, type=Path)
    parser.add_argument("--tex-out", required=True, type=Path)
    parser.add_argument("--plot-out", required=True, type=Path)
    parser.add_argument("--rank-plot-out", type=Path)
    parser.add_argument("--threshold", type=float, default=0.25)
    parser.add_argument("--top-k", type=int, default=40)
    parser.add_argument("--bins", type=int, default=10)
    args = parser.parse_args()
    if not 0 < args.threshold < 1:
        parser.error("--threshold must lie strictly between zero and one")
    if args.top_k < 1:
        parser.error("--top-k must be positive")
    if args.bins < 2:
        parser.error("--bins must be at least two")

    haiku, strong, selected_ids = load_snapshot(
        args.ledger,
        gpt_instrument=args.gpt_instrument,
        opus_instrument=args.opus_instrument,
    )
    results = analyse(
        haiku, strong, threshold=args.threshold,
        top_k=args.top_k, bins=args.bins,
        provenance={
            "ledger_dirs": [str(path) for path in args.ledger],
            "selected_instruments": {
                "GPT-5.6": selected_ids[GPT],
                "Opus 5": selected_ids[OPUS],
            },
        },
    )
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")
    write_tex(results, args.tex_out)
    write_plot(results, args.plot_out)
    if args.rank_plot_out is not None:
        write_rank_plot(results, args.rank_plot_out)
    outputs = [args.json_out, args.tex_out, args.plot_out]
    if args.rank_plot_out is not None:
        outputs.append(args.rank_plot_out)
    print(
        f"{results['shared_pairs']:,} shared pairs; wrote "
        + ", ".join(str(path) for path in outputs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
