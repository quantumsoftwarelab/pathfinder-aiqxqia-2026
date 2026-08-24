"""Build and check derived views of the pathfinder3 verdict ledger.

  build/pair_matrix.jsonl   gitignored cache; regenerate on demand. Rows
                            ordered by pair_id, verdicts within a row by
                            (series key, run_id), repeats listed after and
                            marked with their canonical event.
  views/instruments.json   tracked; a summary of every legacy series and
                            every registered instrument.

--check              structural invariants only; must always pass.
--check --baseline    also compares frozen per-legacy-series counts in
                       protocol/baseline.json.
--check --coverage    also requires full deployed-series coverage, named in
                       protocol/deployment.yaml; opt-in, since it is
                       legitimately false mid-sweep. Default --check only
                       warns on partial coverage.

See plans/pathfinder3-ledger-refactor-design.md.

Usage:
    python3 pathfinder3/scripts/build_views.py
    python3 pathfinder3/scripts/build_views.py --check
    python3 pathfinder3/scripts/build_views.py --check --baseline
    python3 pathfinder3/scripts/build_views.py --check --coverage
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

import ledger
from corpus_release import load_release_inputs
from _common import P3
from _ledger_common import (dump_jsonl_sorted, instrument_id,
                            instrument_identity_record, json_dumps_pretty,
                            load_schema, validate_schema)

BUILD_DIR = P3 / "build"
VIEWS_DIR = P3 / "views"
PAIR_MATRIX_CACHE = BUILD_DIR / "pair_matrix.jsonl"
INSTRUMENTS_VIEW = VIEWS_DIR / "instruments.json"
BASELINE_PATH = P3 / "protocol" / "baseline.json"
DEPLOYMENT_PATH = P3 / "protocol" / "deployment.yaml"


def build_pair_matrix_rows(led: ledger.Ledger) -> list[dict]:
    by_pair: dict[str, list[dict]] = {pid: [] for pid in led.pairs}
    for event in led.verdicts:
        entry = dict(event)
        entry["is_canonical"] = not event.get("repeat", False)
        by_pair.setdefault(event["pair_id"], []).append(entry)

    def verdict_sort_key(e: dict) -> tuple:
        rank = 0 if e["is_canonical"] else 1
        key = ("legacy", e["pair_id"], e["judge"], e["prompt_version"] or "")
        if e["instrument_id"] is not None:
            key = ("instrument", e["pair_id"], e["instrument_id"])
        return (rank, key, e.get("run_id") or "")

    rows = []
    for pid in sorted(led.pairs):
        pair = led.pairs[pid]
        verdicts = sorted(by_pair.get(pid, []), key=verdict_sort_key)
        rows.append({
            "pair_id": pid, "c1": pair["c1"], "c2": pair["c2"],
            "corpus_snapshot": pair["corpus_snapshot"], "verdicts": verdicts,
        })
    return rows


def build_instruments_summary(led: ledger.Ledger) -> dict:
    legacy: dict[tuple, dict] = {}
    for event in led.canonical_by_series().values():
        if event["instrument_id"] is not None:
            continue
        key = (event["judge"], event["prompt_version"])
        info = legacy.setdefault(key, {"judge": key[0], "prompt_version": key[1],
                                       "event_count": 0, "first_judged_at": None,
                                       "last_judged_at": None})
        info["event_count"] += 1
        dates = [d for d in (info["first_judged_at"], event["judged_at"]) if d]
        info["first_judged_at"] = min(dates) if dates else None
        dates = [d for d in (info["last_judged_at"], event["judged_at"]) if d]
        info["last_judged_at"] = max(dates) if dates else None

    instruments = []
    for iid, identity in sorted(led.instruments.items()):
        events = [e for e in led.canonical_by_series().values()
                  if e["instrument_id"] == iid]
        dates = [e["judged_at"] for e in events if e.get("judged_at")]
        instruments.append({
            "instrument_id": iid, "model_id": identity["model_id"],
            "role": identity["role"], "prompt_sha256": identity["prompt_sha256"],
            "cli_version": identity["cli_version"],
            "cli_sha256": identity["cli_sha256"],
            "event_count": len(events),
            "first_judged_at": min(dates) if dates else None,
            "last_judged_at": max(dates) if dates else None,
        })

    return {
        "legacy_series": sorted(legacy.values(), key=lambda r: (r["judge"], r["prompt_version"] or "")),
        "instruments": instruments,
    }


def structural_violations(led: ledger.Ledger, ledger_dir: Path, build_dir: Path) -> list[str]:
    violations: list[str] = []

    schemas = {
        "verdicts": load_schema("verdicts.schema.json"),
        "pairs": load_schema("pairs.schema.json"),
        "calibration_manifest": load_schema("calibration_manifest.schema.json"),
        "instruments": load_schema("instruments.schema.json"),
    }
    for i, row in enumerate(led.verdicts, 1):
        try:
            validate_schema(row, schemas["verdicts"], context=f"verdicts:{i}")
        except Exception as e:  # noqa: BLE001 - collected, not raised
            violations.append(f"[structural] {e}")
    for i, row in enumerate(led.pairs.values(), 1):
        try:
            validate_schema(row, schemas["pairs"], context=f"pairs:{i}")
        except Exception as e:  # noqa: BLE001
            violations.append(f"[structural] {e}")
    for i, row in enumerate(led.manifest.values(), 1):
        try:
            validate_schema(row, schemas["calibration_manifest"],
                            context=f"calibration_manifest:{i}")
        except Exception as e:  # noqa: BLE001
            violations.append(f"[structural] {e}")
    for i, row in enumerate(led.instruments.values(), 1):
        try:
            validate_schema(row, schemas["instruments"], context=f"instruments:{i}")
        except Exception as e:  # noqa: BLE001
            violations.append(f"[structural] {e}")

    # Canonical uniqueness + repeat validity.
    canonical_keys: set[tuple] = set()
    canonical_run_ids: dict[tuple, set[str]] = {}
    for event in led.verdicts:
        key = ledger.series_key(event)
        if event.get("repeat"):
            continue
        if key in canonical_keys:
            violations.append(f"[structural] duplicate canonical event for series {key}")
        canonical_keys.add(key)
    for event in led.repeats():
        key = ledger.series_key(event)
        if key not in canonical_keys:
            violations.append(
                f"[structural] repeat event for series {key} has no canonical event")
        seen = canonical_run_ids.setdefault(key, set())
        rid = event.get("run_id")
        if rid in seen:
            violations.append(f"[structural] duplicate run_id {rid!r} for repeat series {key}")
        seen.add(rid)

    # Required-fields bifurcation: a "new" event (non-null instrument_id)
    # must carry non-null run_id/tokens_in/tokens_out/est_cost_usd/
    # score_explanation; migrated/legacy events (null instrument_id) are
    # exempt, since their nulls are historical, not a validation failure.
    for event in led.verdicts:
        if event["instrument_id"] is None:
            continue
        missing = [f for f in ("run_id", "tokens_in", "tokens_out",
                               "est_cost_usd", "score_explanation")
                  if event.get(f) is None]
        if missing:
            violations.append(
                f"[structural] new-instrument event for {event['pair_id']} "
                f"(instrument {event['instrument_id']}) has null required "
                f"field(s) {missing}")

    # Instrument hash recomputation.
    for iid, identity in led.instruments.items():
        record = {
            key: value for key, value in identity.items()
            if key not in {"instrument_id", "registered_at", "first_run_id"}
        }
        recomputed = instrument_id(record)
        if recomputed != iid:
            violations.append(
                f"[structural] instrument {iid} does not match a "
                f"recomputation of its identity record ({recomputed})")

    # Build cache freshness.
    if build_dir.exists() and (build_dir / "pair_matrix.jsonl").exists():
        fresh_rows = build_pair_matrix_rows(led)
        fresh_bytes = "".join(
            json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in fresh_rows
        ).encode("utf-8")
        on_disk = (build_dir / "pair_matrix.jsonl").read_bytes()
        if fresh_bytes != on_disk:
            violations.append(
                "[structural] build/pair_matrix.jsonl is stale; run "
                "build_views.py to regenerate")

    return violations


def baseline_violations(led: ledger.Ledger, baseline: dict) -> list[str]:
    violations: list[str] = []
    legacy_pair_count = sum(
        row.get("schema_version") is None for row in led.pairs.values()
    )
    if legacy_pair_count != baseline["pairs"]:
        violations.append(
            f"[baseline] legacy pair count {legacy_pair_count} != frozen "
            f"{baseline['pairs']}")

    canonical = led.canonical_by_series()
    counts: dict[str, int] = {}
    for event in canonical.values():
        if event["instrument_id"] is not None:
            continue
        counts[f"{event['judge']}|{event['prompt_version']}"] = \
            counts.get(f"{event['judge']}|{event['prompt_version']}", 0) + 1
    for key, expected in baseline["legacy_series"].items():
        got = counts.get(key, 0)
        if got != expected:
            violations.append(
                f"[baseline] legacy series {key} has {got} events, frozen at {expected}")

    haiku_v2 = [e for e in canonical.values()
                if e["judge"] == "claude:claude-haiku-4-5" and e["prompt_version"] == "v2"]
    c005 = sum(1 for e in haiku_v2 if ledger.score(e) >= 0.05)
    c015 = sum(1 for e in haiku_v2 if ledger.score(e) >= 0.15)
    c025 = sum(1 for e in haiku_v2 if ledger.score(e) >= 0.25)
    if c005 != baseline["haiku_v2_survivors"]["s_ge_0.05"]:
        violations.append(f"[baseline] s>=0.05 survivors {c005} != frozen "
                          f"{baseline['haiku_v2_survivors']['s_ge_0.05']}")
    if c015 != baseline["haiku_v2_survivors"]["s_ge_0.15"]:
        violations.append(f"[baseline] s>=0.15 survivors {c015} != frozen "
                          f"{baseline['haiku_v2_survivors']['s_ge_0.15']}")
    if c025 != baseline["haiku_v2_survivors"]["s_ge_0.25"]:
        violations.append(f"[baseline] s>=0.25 survivors {c025} != frozen "
                          f"{baseline['haiku_v2_survivors']['s_ge_0.25']}")
    return violations


def coverage_violations(
    led: ledger.Ledger, deployment: dict, pair_ids: set[str] | None = None,
) -> list[str]:
    target = deployment["deployed_cheap_series"]
    covered = {
        e["pair_id"] for e in led.canonical_by_series().values()
        if e["judge"] == target["judge"]
        and e["prompt_version"] == target["prompt_version"]
        and e["instrument_id"] == target["instrument_id"]
    }
    if pair_ids is None:
        pair_ids = {
            pair_id for pair_id, row in led.pairs.items()
            if row.get("schema_version") is None
        }
    missing = pair_ids - covered
    if missing:
        sample = sorted(missing)[:5]
        return [f"[coverage] {len(missing)} pair(s) lack a canonical verdict "
               f"from the deployed series (e.g. {sample})"]
    return []


def guarded_violations(label: str, fn, *fn_args) -> list[str]:
    """Run a violations-collecting function, turning a LedgerError raised by
    ledger.canonical_by_series() (e.g. a duplicate canonical event) into a
    single violation string instead of letting it propagate.

    structural_violations() detects duplicate canonical events itself via a
    non-raising manual scan, so that check always reports cleanly. But
    baseline_violations() and coverage_violations() both call
    led.canonical_by_series() internally, which raises LedgerError on the
    same condition. Without this guard, a ledger defect that
    structural_violations() already collected as a violation would instead
    crash --check with an unhandled traceback and lose the rest of the
    violation list.
    """
    try:
        return fn(*fn_args)
    except ledger.LedgerError as e:
        return [f"[{label}] cannot evaluate: {e}"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--baseline", action="store_true")
    ap.add_argument("--coverage", action="store_true")
    ap.add_argument("--release", default=None,
                    help="scope coverage to one immutable release")
    args = ap.parse_args()

    led = ledger.load_ledger()

    if args.check:
        violations = structural_violations(led, ledger.LEDGER_DIR, BUILD_DIR)
        if args.baseline:
            baseline = json.loads(BASELINE_PATH.read_text())
            violations += guarded_violations("baseline", baseline_violations, led, baseline)
        deployment = yaml.safe_load(DEPLOYMENT_PATH.read_text())
        release_pair_ids = None
        if args.release:
            _, release_ids = load_release_inputs(args.release)
            release_pair_ids = set(release_ids)
        coverage_issues = guarded_violations(
            "coverage", coverage_violations, led, deployment, release_pair_ids
        )
        if args.coverage:
            violations += coverage_issues
        elif coverage_issues:
            print(f"WARNING: {coverage_issues[0]}")
        if violations:
            for v in violations:
                print(v)
            return 1
        print("check OK")
        return 0

    BUILD_DIR.mkdir(exist_ok=True)
    VIEWS_DIR.mkdir(exist_ok=True)
    dump_jsonl_sorted(PAIR_MATRIX_CACHE, build_pair_matrix_rows(led))
    INSTRUMENTS_VIEW.write_text(json_dumps_pretty(build_instruments_summary(led)))
    print(f"wrote {PAIR_MATRIX_CACHE} and {INSTRUMENTS_VIEW}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
