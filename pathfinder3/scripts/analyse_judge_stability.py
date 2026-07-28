"""Test-retest stability of one judge instrument, from its --repeat rows.

This is the measurement half of the experiment written up in
notes/judge-self-consistency-2026-07-27.tex. That note derives

    D_within_J = E|U^{J,1} - U^{J,2}|,

the mean absolute gap between two readings of the same pair by the same
instrument, in midrank percentile units. Because the deterministic part of
a judge's score cancels between two readings of one pair, D_within is a
measurement of that judge's noise alone, uncontaminated by what it thinks
of the pair. Everything here is computed from --repeat rows only, so the
canonical series is never used as one arm of a comparison and selection
into the tested set cannot pull the two readings together.

Percentile units, not raw scores: a replicate value is placed in the
instrument's OWN canonical score distribution by the midrank rule

    U(x) = (#{c < x} + (1 + #{c == x}) / 2) / N,   c over the N canonical scores,

so the figure is invariant under any monotone rescaling of that judge and
two differently calibrated judges become comparable. Midranking rather
than the plain empirical CDF matters because these judges tie heavily;
the CDF would place every member of a tie band at its top.

With --counterpart naming a second instrument that has replicates over the
same pairs, the script also reports the note's test statistic

    R = D_cross_observed / sqrt( (D_within_A^2 + D_within_B^2) / 2 ),

whose denominator is what the cross-judge gap would be if the two judges
shared a ranking and differed only by independent noise. R ~ 1 leaves that
null standing; R > 1 rejects it, and 1 - 1/R^2 is the systematic share of
the variance in their difference.

Usage:
    python3 pathfinder3/scripts/analyse_judge_stability.py \\
        --instrument d19bb7d063eb --run-prefix selfcons-v5-gpt56
    python3 pathfinder3/scripts/analyse_judge_stability.py \\
        --instrument d19bb7d063eb --counterpart b99996c890be --json-out out.json
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import ledger
from build_shortlist_snapshot import spearman
from judge_runner import OUTPUT_CONTRACTS, contract_sha256

# The two axes always land in the corr and int ledger fields, but what they
# MEAN depends on the instrument's output contract: v2 scored correlation and
# interest, v4/v5 score feasibility and gain. Label by contract hash so a
# mixed-instrument report cannot mislabel either series.
AXIS_NAMES = {contract_sha256(name): keys[:2]
              for name, keys in OUTPUT_CONTRACTS.items()}


def resolve_instrument(led: ledger.Ledger, prefix: str) -> str:
    """Accept a unique instrument_id prefix, as the sibling scripts do."""
    matches = [iid for iid in led.instruments if iid.startswith(prefix)]
    if not matches:
        raise SystemExit(f"no instrument matches {prefix!r}")
    if len(matches) > 1:
        raise SystemExit(f"{prefix!r} matches {len(matches)} instruments")
    return matches[0]


def percentile_map(canonical_scores: list[float]):
    """Midrank percentile of an arbitrary value within a fixed population."""
    ordered = sorted(canonical_scores)
    n = len(ordered)
    if n == 0:
        raise SystemExit("instrument has no canonical verdicts to rank against")

    def rank(value: float) -> float:
        below = sum(1 for c in ordered if c < value)
        equal = sum(1 for c in ordered if c == value)
        return (below + (1 + equal) / 2.0) / n

    return rank


def replicates(led: ledger.Ledger, instrument_id: str,
               run_prefix: str | None) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for event in led.repeats():
        if event.get("instrument_id") != instrument_id:
            continue
        if run_prefix and not str(event.get("run_id") or "").startswith(run_prefix):
            continue
        out[event["pair_id"]].append(event)
    # Replicate order is ledger append order, i.e. run order. Two runs over
    # one pair list therefore give reading 1 and reading 2 consistently.
    return {pid: evs for pid, evs in out.items() if len(evs) >= 2}


def mean_abs(values: list[float]) -> float:
    return sum(values) / len(values)


def within_report(led: ledger.Ledger, instrument_id: str,
                  run_prefix: str | None, ks: list[int]) -> dict:
    canonical = {e["pair_id"]: e
                 for e in led.canonical_for_series(instrument_id=instrument_id)}
    rank = percentile_map([ledger.score(e) for e in canonical.values()])
    reps = replicates(led, instrument_id, run_prefix)
    if not reps:
        raise SystemExit(
            f"instrument {instrument_id[:12]} has no pair with two repeat rows"
            + (f" under run prefix {run_prefix!r}" if run_prefix else ""))

    pair_ids = sorted(reps)
    first = {p: ledger.score(reps[p][0]) for p in pair_ids}
    second = {p: ledger.score(reps[p][1]) for p in pair_ids}
    u1 = {p: rank(first[p]) for p in pair_ids}
    u2 = {p: rank(second[p]) for p in pair_ids}

    gaps = [abs(u1[p] - u2[p]) for p in pair_ids]
    raw_gaps = [abs(first[p] - second[p]) for p in pair_ids]
    contract = led.instruments[instrument_id]["output_contract_sha256"]
    axis_names = AXIS_NAMES.get(contract, ("corr axis", "int axis"))
    axis = {}
    for name, field in zip(axis_names, ("corr", "int")):
        axis[name] = {
            "mean_abs_delta": mean_abs(
                [abs(reps[p][0][field] - reps[p][1][field]) for p in pair_ids]),
            "identical": sum(1 for p in pair_ids
                             if reps[p][0][field] == reps[p][1][field]),
        }

    retention = {}
    for k in ks:
        if k > len(pair_ids):
            continue
        top1 = set(sorted(pair_ids, key=lambda p: (-u1[p], p))[:k])
        top2 = set(sorted(pair_ids, key=lambda p: (-u2[p], p))[:k])
        retention[f"top_{k}_retained"] = len(top1 & top2) / k

    out = {
        "instrument_id": instrument_id,
        "judge": next(iter(canonical.values()))["judge"],
        "model_id": led.instruments[instrument_id]["model_id"],
        "effort": led.instruments[instrument_id]["effort"],
        "run_prefix": run_prefix,
        "canonical_population": len(canonical),
        "n_pairs": len(pair_ids),
        "d_within": mean_abs(gaps),
        "d_within_median": sorted(gaps)[len(gaps) // 2],
        "d_within_max": max(gaps),
        "mean_abs_raw_delta": mean_abs(raw_gaps),
        "identical_scores": sum(1 for g in raw_gaps if g == 0),
        "spearman_repeat1_repeat2": spearman(
            [first[p] for p in pair_ids], [second[p] for p in pair_ids]),
        "axes": axis,
        "retention": retention,
        "runs": sorted({str(e.get("run_id")) for evs in reps.values() for e in evs}),
        "cost_usd": sum((e.get("est_cost_usd") or 0.0)
                        for evs in reps.values() for e in evs),
    }
    # Canonical-vs-replicate correlation is reported for continuity with the
    # sweep, but it is NOT the stability figure: if the tested pairs were
    # selected on canonical scores, that comparison is conditioned and the
    # replicate-only D_within above is the unconditioned estimate.
    shared = [p for p in pair_ids if p in canonical]
    if len(shared) >= 2:
        out["spearman_canonical_repeat1"] = spearman(
            [ledger.score(canonical[p]) for p in shared],
            [first[p] for p in shared])
    return out


def cross_report(led: ledger.Ledger, left: dict, right: dict,
                 left_id: str, right_id: str,
                 run_prefix: str | None) -> dict | None:
    """The note's R statistic, when both instruments have replicates."""
    maps, reads = {}, {}
    for iid in (left_id, right_id):
        canonical = {e["pair_id"]: e
                     for e in led.canonical_for_series(instrument_id=iid)}
        rank = percentile_map([ledger.score(e) for e in canonical.values()])
        reps = replicates(led, iid, run_prefix)
        maps[iid] = rank
        reads[iid] = {p: [rank(ledger.score(e)) for e in evs[:2]]
                      for p, evs in reps.items()}
    shared = sorted(set(reads[left_id]) & set(reads[right_id]))
    if len(shared) < 2:
        return None
    # r = 1, 2: replicate against replicate, so neither arm is the score the
    # tested pairs were selected on.
    cross = [abs(reads[left_id][p][r] - reads[right_id][p][r])
             for p in shared for r in (0, 1)]
    observed = mean_abs(cross)
    predicted = ((left["d_within"] ** 2 + right["d_within"] ** 2) / 2.0) ** 0.5
    ratio = observed / predicted if predicted else None
    return {
        "n_pairs": len(shared),
        "d_cross_observed": observed,
        "d_cross_predicted_under_h0": predicted,
        "ratio_R": ratio,
        "systematic_variance_share": (1 - 1 / ratio ** 2) if ratio else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--instrument", required=True,
                    help="instrument_id or unique prefix")
    ap.add_argument("--counterpart", default=None,
                    help="second instrument, to report the cross-judge R")
    ap.add_argument("--run-prefix", default=None,
                    help="only count repeat rows whose run_id starts with this")
    ap.add_argument("--k", action="append", type=int, default=None,
                    help="shortlist size for retention (repeatable; default 10 20)")
    ap.add_argument("--json-out", type=Path, default=None)
    args = ap.parse_args()
    ks = args.k or [10, 20]

    led = ledger.load_ledger()
    iid = resolve_instrument(led, args.instrument)
    report = within_report(led, iid, args.run_prefix, ks)

    print(f"\ntest-retest stability, {report['model_id']} "
          f"effort={report['effort']} (instrument {iid[:12]}...)")
    print(f"  runs: {', '.join(report['runs'])}")
    print(f"  {report['n_pairs']} pairs read twice, "
          f"ranked within {report['canonical_population']:,} canonical scores")
    print(f"  D_within            {report['d_within']:.4f} "
          f"(median {report['d_within_median']:.4f}, max {report['d_within_max']:.4f})")
    print(f"  mean |delta s|      {report['mean_abs_raw_delta']:.4f}   "
          f"identical both times: {report['identical_scores']}/{report['n_pairs']}")
    print(f"  Spearman r1 vs r2   {report['spearman_repeat1_repeat2']:.3f}")
    for name, row in report["axes"].items():
        print(f"  {name:12s} mean |delta| {row['mean_abs_delta']*100:5.2f} pts, "
              f"unchanged {row['identical']}/{report['n_pairs']}")
    for key, value in report["retention"].items():
        print(f"  {key:20s} {value:.2f}")
    print(f"  replicate cost      ${report['cost_usd']:.2f}")

    out = {"within": report}
    if args.counterpart:
        cid = resolve_instrument(led, args.counterpart)
        other = within_report(led, cid, args.run_prefix, ks)
        cross = cross_report(led, report, other, iid, cid, args.run_prefix)
        out["counterpart"] = other
        out["cross"] = cross
        if cross is None:
            print(f"\n  counterpart {cid[:12]} shares no twice-read pair; "
                  "R not computed")
        else:
            print(f"\ncross-judge test, vs {other['model_id']} "
                  f"({cid[:12]}...), n={cross['n_pairs']}")
            print(f"  D_within other      {other['d_within']:.4f}")
            print(f"  D_cross observed    {cross['d_cross_observed']:.4f}")
            print(f"  D_cross under H0    {cross['d_cross_predicted_under_h0']:.4f}")
            print(f"  R                   {cross['ratio_R']:.2f}  "
                  f"({100*cross['systematic_variance_share']:.0f}% systematic)")
    else:
        print("\n  (pass --counterpart to compute the cross-judge ratio R)")

    if args.json_out:
        args.json_out.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
        print(f"\nwrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
