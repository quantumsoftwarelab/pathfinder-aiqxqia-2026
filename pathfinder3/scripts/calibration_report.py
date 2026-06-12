"""Regenerate pathfinder3/calibration/report.md from verdicts.

Reads ``calibration_pairs.jsonl`` and turns cheap and strong judge verdicts
into a model-referenced Phase 0 operating report. Curator labels are optional:
when ``labels_curator.jsonl`` is filled in, the report adds a sanity-check
judge-vs-curator section, but Phase 0 no longer waits for those labels.

The primary calibration target is strong-model recall: choose a cheap
threshold that preserves pairs the strong judge would keep, then state the
candidate volume that threshold implies. This is discovery calibration, not
human-ground-truth classifier measurement.

The report is generated, never hand-edited.

Usage: python3 pathfinder3/scripts/calibration_report.py
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import date

from _common import P3, PROMPT_VERSION, load_jsonl

PAIRS_PATH = P3 / "calibration" / "calibration_pairs.jsonl"
LABELS_PATH = P3 / "calibration" / "labels_curator.jsonl"
REPORT_PATH = P3 / "calibration" / "report.md"
RECALL_TARGET = 0.95
FLAG_SHARE_CAP = 0.10
STRONG_REFERENCE_SCORE = 0.25
AXIS_REFERENCE_SCORE = 0.50
STRATA = ("likely_good", "surface_similar", "hard_positive", "random")
THRESHOLDS = [round(0.05 * k, 2) for k in range(0, 20)]

# Pairs whose pairing is described as a worked example inside the v2 judge
# prompt (sourced from pathfinder/revealing_examples.md). The judges have
# effectively been told the answer for these, so they are excluded from
# model-reference metrics and the threshold sweep. Paper-level halo (other
# pairs sharing a paper with an example) is reported but not excluded.
EXAMPLE_PAIR_IDS = {
    "qsl:2501.14380::e100.001",
    "qsl:2011.10005::e118.001",
}
EXAMPLE_PAPERS = {"qsl:2508.05523", "qsl:2210.13200", "qsl:2501.14380",
                  "qsl:2011.10005"}


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def kappa(pairs: list[tuple[int, int]]) -> float | None:
    n = len(pairs)
    if n == 0:
        return None
    po = sum(1 for a, b in pairs if a == b) / n
    pa1 = sum(a for a, _ in pairs) / n
    pb1 = sum(b for _, b in pairs) / n
    pe = pa1 * pb1 + (1 - pa1) * (1 - pb1)
    if pe == 1:
        return None
    return (po - pe) / (1 - pe)


def auc(scored: list[tuple[float, int]]) -> float | None:
    pos = [s for s, y in scored if y == 1]
    neg = [s for s, y in scored if y == 0]
    if not pos or not neg:
        return None
    wins = sum(1 for sp in pos for sn in neg if sp > sn)
    ties = sum(0.5 for sp in pos for sn in neg if sp == sn)
    return (wins + ties) / (len(pos) * len(neg))


def fmt_ci(successes: int, n: int) -> str:
    if n == 0:
        return "n/a"
    lo, hi = wilson(successes, n)
    return f"{successes / n:.3f} [{lo:.3f}, {hi:.3f}] ({successes}/{n})"


def pair_score(v: dict) -> float:
    return v["corr"] * v["int"]


def mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def first_score(row: dict, verdicts_by_tier: dict[tuple[str, str], list[dict]],
                tier: str) -> float | None:
    vs = verdicts_by_tier.get((row["pair_id"], tier), [])
    if not vs:
        return None
    return mean([pair_score(v) for v in vs])


def first_axis(row: dict, verdicts_by_tier: dict[tuple[str, str], list[dict]],
               tier: str, axis: str) -> float | None:
    vs = verdicts_by_tier.get((row["pair_id"], tier), [])
    if not vs:
        return None
    return mean([v[axis] for v in vs])


def strong_reference(row: dict,
                     verdicts_by_tier: dict[tuple[str, str], list[dict]]) -> bool:
    """Union rule: any strong judge can make a pair a reference positive."""
    return any(pair_score(v) >= STRONG_REFERENCE_SCORE
               for v in verdicts_by_tier.get((row["pair_id"], "strong"), []))


def strong_axis_reference(
        row: dict, verdicts_by_tier: dict[tuple[str, str], list[dict]]) -> bool:
    return any(v["corr"] >= AXIS_REFERENCE_SCORE and v["int"] >= AXIS_REFERENCE_SCORE
               for v in verdicts_by_tier.get((row["pair_id"], "strong"), []))


def main() -> None:
    pairs = load_jsonl(PAIRS_PATH)
    verdicts_by_tier = defaultdict(list)
    judge_ids_by_tier = defaultdict(set)
    for row in pairs:
        for v in row["verdicts"]:
            if v["prompt_version"] == PROMPT_VERSION:
                verdicts_by_tier[(row["pair_id"], v["tier"])].append(v)
                judge_ids_by_tier[v["tier"]].add(v["judge"])

    labels = {}
    if LABELS_PATH.exists():
        for row in load_jsonl(LABELS_PATH):
            if row.get("corr_label") is not None and row.get("int_label") is not None:
                labels[row["pair_id"]] = row

    lines = [
        "# Pathfinder3 calibration report",
        "",
        f"Generated by `calibration_report.py` on {date.today().isoformat()}; "
        "do not hand-edit.",
        "",
        f"Pairs: {len(pairs)}. Curator labels filled: {len(labels)} of {len(pairs)}.",
        "",
        "Mode: strong-model-calibrated discovery cascade. Curator labels are "
        "optional sanity checks, not a Phase 0 dependency.",
        "",
        "Judges present: "
        + ", ".join(f"{tier}: {', '.join(sorted(judges))}"
                    for tier, judges in sorted(judge_ids_by_tier.items())),
        "",
    ]

    lines += ["## Stratum counts", "", "| stratum | pairs | cheap judged | strong judged |",
              "|---|---|---|---|"]
    by_stratum = defaultdict(list)
    for row in pairs:
        by_stratum[row["stratum"]].append(row)
    for s in STRATA:
        rows = by_stratum.get(s, [])
        c = sum(1 for r in rows if (r["pair_id"], "cheap") in verdicts_by_tier)
        g = sum(1 for r in rows if (r["pair_id"], "strong") in verdicts_by_tier)
        lines.append(f"| {s} | {len(rows)} | {c} | {g} |")
    lines.append("")

    lines += ["## Score distributions (mean corr / mean int / mean s per stratum)", "",
              "| stratum | cheap | strong |", "|---|---|---|"]
    for s in STRATA:
        cells = []
        for tier in ("cheap", "strong"):
            vs = [v for r in by_stratum.get(s, [])
                  for v in verdicts_by_tier.get((r["pair_id"], tier), [])]
            if vs:
                mc = sum(v["corr"] for v in vs) / len(vs)
                mi = sum(v["int"] for v in vs) / len(vs)
                ms = sum(pair_score(v) for v in vs) / len(vs)
                cells.append(f"{mc:.2f} / {mi:.2f} / {ms:.2f}")
            else:
                cells.append("n/a")
        lines.append(f"| {s} | {cells[0]} | {cells[1]} |")
    lines.append("")

    eval_pairs = [r for r in pairs if r["pair_id"] not in EXAMPLE_PAIR_IDS]
    excluded_examples = len(pairs) - len(eval_pairs)
    both = [(r,
             first_axis(r, verdicts_by_tier, "cheap", "corr"),
             first_axis(r, verdicts_by_tier, "cheap", "int"),
             first_axis(r, verdicts_by_tier, "strong", "corr"),
             first_axis(r, verdicts_by_tier, "strong", "int"))
            for r in eval_pairs]
    both = [(r, cc, ci, sc, si) for r, cc, ci, sc, si in both
            if cc is not None and ci is not None and sc is not None and si is not None]
    if both:
        lines += ["## Cheap/strong agreement", "",
                  f"Excluded from model-reference metrics: {excluded_examples} "
                  "pair(s) described as worked examples inside the judge prompt.",
                  ""]
        axis_values = {
            "corr": [(int(cc >= 0.5), int(sc >= 0.5)) for _, cc, _, sc, _ in both],
            "int": [(int(ci >= 0.5), int(si >= 0.5)) for _, _, ci, _, si in both],
        }
        for axis, values in axis_values.items():
            kp = kappa(values)
            lines.append(f"- {axis}: kappa (both binarised at 0.5) = "
                         f"{kp:.3f} on {len(values)} pairs" if kp is not None else
                         f"- {axis}: kappa undefined")
        lines.append("")

    strong_ref = [r for r in eval_pairs if strong_reference(r, verdicts_by_tier)
                  and first_score(r, verdicts_by_tier, "cheap") is not None]
    strong_axis_ref = [
        r for r in eval_pairs if strong_axis_reference(r, verdicts_by_tier)
        and first_score(r, verdicts_by_tier, "cheap") is not None
    ]
    cheap_judged_eval = [
        r for r in eval_pairs if first_score(r, verdicts_by_tier, "cheap") is not None
    ]
    random_eval = [
        r for r in eval_pairs if r["stratum"] == "random"
        and first_score(r, verdicts_by_tier, "cheap") is not None
    ]

    if strong_ref and cheap_judged_eval:
        lines += [
            "## Model-referenced threshold sweep",
            "",
            f"Primary reference positive: any strong judge with "
            f"`s = corr * int >= {STRONG_REFERENCE_SCORE:.2f}`. This is a "
            "model reference, not human ground truth.",
            "",
            f"Paper-level halo (non-excluded pairs sharing a paper with a "
            f"worked example): "
            f"{sum(1 for r in cheap_judged_eval if r['pair_id'].split('::')[0] in EXAMPLE_PAPERS)} "
            "pair(s), reported but kept, per plan item 4.",
            "",
            "| threshold | recall on strong reference | all calibration flagged | random flagged |",
            "|---|---|---|---|",
        ]
        recall_choice = None
        budget_choice = None
        for t in THRESHOLDS:
            flagged_ref = sum(
                1 for r in strong_ref
                if (first_score(r, verdicts_by_tier, "cheap") or 0.0) >= t)
            flagged_all = sum(
                1 for r in cheap_judged_eval
                if (first_score(r, verdicts_by_tier, "cheap") or 0.0) >= t)
            flagged_rand = sum(
                1 for r in random_eval
                if (first_score(r, verdicts_by_tier, "cheap") or 0.0) >= t)
            recall = flagged_ref / len(strong_ref)
            random_share = flagged_rand / len(random_eval) if random_eval else 1.0
            lines.append(f"| {t:.2f} | {fmt_ci(flagged_ref, len(strong_ref))} | "
                         f"{fmt_ci(flagged_all, len(cheap_judged_eval))} | "
                         f"{fmt_ci(flagged_rand, len(random_eval))} |")
            if recall >= RECALL_TARGET:
                recall_choice = t if recall_choice is None or t > recall_choice else recall_choice
            if random_share <= FLAG_SHARE_CAP and budget_choice is None:
                budget_choice = t
        lines.append("")
        if recall_choice is not None:
            kept_all = sum(
                1 for r in cheap_judged_eval
                if (first_score(r, verdicts_by_tier, "cheap") or 0.0) >= recall_choice)
            kept_rand = sum(
                1 for r in random_eval
                if (first_score(r, verdicts_by_tier, "cheap") or 0.0) >= recall_choice)
            lines.append(
                f"Recall-first operating point: threshold {recall_choice:.2f}. "
                f"It keeps at least {RECALL_TARGET:.0%} of strong-reference "
                f"positives and forwards {kept_all}/{len(cheap_judged_eval)} "
                f"calibration pairs; random-stratum forwarding is "
                f"{fmt_ci(kept_rand, len(random_eval))}.")
        else:
            lines.append(
                f"No non-zero threshold reaches model-referenced recall >= "
                f"{RECALL_TARGET:.0%}; forwarding all cheap-judged pairs is "
                "the only recall-first option under the current reference "
                "definition.")
        if budget_choice is not None:
            kept_ref = sum(
                1 for r in strong_ref
                if (first_score(r, verdicts_by_tier, "cheap") or 0.0) >= budget_choice)
            lines.append(
                f"Budget checkpoint: threshold {budget_choice:.2f} is the "
                f"lowest threshold with random-stratum forwarding <= "
                f"{FLAG_SHARE_CAP:.0%}; its model-referenced recall is "
                f"{fmt_ci(kept_ref, len(strong_ref))}.")
        lines.append("")

        lines += ["### Reference sensitivity", "",
                  "| reference rule | positives | recall at recall-first threshold |",
                  "|---|---|---|"]
        sensitivity = [
            (f"strong s >= {STRONG_REFERENCE_SCORE:.2f}", strong_ref),
            (f"strong corr >= {AXIS_REFERENCE_SCORE:.2f} and int >= "
             f"{AXIS_REFERENCE_SCORE:.2f}", strong_axis_ref),
        ]
        for name, refs in sensitivity:
            if not refs:
                lines.append(f"| {name} | 0 | n/a |")
                continue
            threshold = recall_choice if recall_choice is not None else 0.0
            kept = sum(
                1 for r in refs
                if (first_score(r, verdicts_by_tier, "cheap") or 0.0) >= threshold)
            lines.append(f"| {name} | {len(refs)} | {fmt_ci(kept, len(refs))} |")
        lines.append("")

    if labels:
        eval_labels = {pid: l for pid, l in labels.items()
                       if pid not in EXAMPLE_PAIR_IDS}
        excluded = len(labels) - len(eval_labels)
        halo = sum(1 for pid in eval_labels
                   if pid.split("::")[0] in EXAMPLE_PAPERS)
        lines += ["## Optional curator sanity check", "",
                  f"Excluded from all metrics below: {excluded} pair(s) "
                  "described as worked examples inside the judge prompt. "
                  f"Paper-level halo (non-excluded pairs sharing a paper "
                  f"with an example): {halo} pair(s), reported but kept.", ""]
        for tier in ("cheap", "strong"):
            lines.append(f"### {tier} judge")
            lines.append("")
            for axis, lab in (("corr", "corr_label"), ("int", "int_label")):
                obs = []
                for pid in eval_labels:
                    fake_row = {"pair_id": pid}
                    score = first_axis(fake_row, verdicts_by_tier, tier, axis)
                    if score is not None:
                        obs.append((score, eval_labels[pid][lab]))
                kp = kappa([(int(s >= 0.5), y) for s, y in obs])
                a = auc(obs)
                kp_s = f"{kp:.3f}" if kp is not None else "undefined"
                a_s = f"{a:.3f}" if a is not None else "undefined"
                lines.append(f"- {axis}: kappa = {kp_s}, AUC = {a_s}, n = {len(obs)}")
            lines.append("")
        lines.append("")
    else:
        lines += ["## Optional curator labels", "",
                  "No curator labels are filled. Under the current protocol "
                  "this does not block Phase 0; labels would be used only as "
                  "a sanity check against the model-referenced operating "
                  "point.", ""]

    failures = []
    for log in sorted((P3 / "logs").glob("judge-*.jsonl")):
        for row in load_jsonl(log):
            if row.get("error"):
                failures.append(row)
    lines += ["## Contract failures (from raw logs)", "",
              f"- {len(failures)} failed judge calls recorded in raw logs "
              "(retries included; final-state failures only appear if a pair "
              "still lacks a verdict above).", ""]

    lines += ["## Known limitations", "",
              "- Strata `surface_similar` and `hard_positive` are below plan "
              "targets (audit-derived seeds only); the current calibration "
              "set is useful for an operating-point check, not for a final "
              "claim about recall.",
              "- The threshold target is strong-model recall, not human "
              "ground truth. Add a second strong judge before treating model "
              "disagreement as measured rather than assumed.",
              "- 7 vendor papers have no abstract (non-arXiv or API miss); "
              "their pairs are judged from titles, flagged via the "
              "`(not available; judge from the title)` placeholder.",
              "- Production leakage still needs the Phase 1 random audit; "
              "this report only selects the Phase 1 cheap threshold.",
              ""]

    REPORT_PATH.write_text("\n".join(lines))
    print(f"wrote {REPORT_PATH}")


if __name__ == "__main__":
    main()
