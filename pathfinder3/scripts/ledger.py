"""Single read contract for the pathfinder3 verdict ledger.

Loads ledger/verdicts.jsonl, ledger/pairs.jsonl,
ledger/calibration_manifest.jsonl, and ledger/instruments.jsonl; resolves
the series key for each verdict event; selects canonical (non-repeat)
events; computes s = corr * int.

Every consumer (build_views.py, judge_runner.py, calibration_report.py,
compare_cheap_judges.py, plot_operating_curves.py) imports this module
rather than parsing ledger lines directly, so dedup/canonical-selection
logic exists exactly once. See plans/pathfinder3-ledger-refactor-design.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from _common import P3, load_jsonl
from _ledger_common import LedgerSchemaError, load_schema, validate_schema

LEDGER_DIR = P3 / "ledger"
VERDICTS_PATH = LEDGER_DIR / "verdicts.jsonl"
PAIRS_PATH = LEDGER_DIR / "pairs.jsonl"
MANIFEST_PATH = LEDGER_DIR / "calibration_manifest.jsonl"
INSTRUMENTS_PATH = LEDGER_DIR / "instruments.jsonl"
PAIR_SCHEMA = load_schema("pairs.schema.json")


class LedgerError(RuntimeError):
    pass


def pair_logical_ids(row: dict) -> tuple[str, str, str]:
    """Return logical pair, Q and P IDs without parsing an opaque v2 ID."""
    q_item_id = row["c1"]["item_id"]
    p_item_id = row["c2"]["item_id"]
    logical_pair_id = row.get("logical_pair_id", f"{q_item_id}::{p_item_id}")
    return logical_pair_id, q_item_id, p_item_id


def validate_pair_row(row: dict, *, context: str) -> None:
    """Validate the semantic legacy/v2 branches of the mixed pair ledger."""
    if row.get("schema_version") is None and not {"c1", "c2"} <= set(row):
        # Some analysis fixtures intentionally carry pair_id-only legacy
        # rows. New v2 rows never receive this compatibility exemption.
        return
    try:
        logical_pair_id, q_item_id, p_item_id = pair_logical_ids(row)
    except KeyError as exc:
        raise LedgerError(f"{context}: pair side lacks item_id") from exc
    expected_logical = f"{q_item_id}::{p_item_id}"
    if logical_pair_id != expected_logical:
        raise LedgerError(
            f"{context}: logical_pair_id does not match pair-side item IDs"
        )
    if row.get("schema_version") is None:
        unexpected = {"logical_pair_id", "analysis_role"} & set(row)
        side_has_representation = any(
            "representation_id" in row[side] for side in ("c1", "c2")
        )
        if unexpected or side_has_representation or row["pair_id"] != expected_logical:
            raise LedgerError(f"{context}: half-migrated legacy pair row")
        return
    try:
        validate_schema(row, PAIR_SCHEMA, context=context)
    except LedgerSchemaError as exc:
        raise LedgerError(str(exc)) from exc
    if row.get("schema_version") != 2:
        raise LedgerError(f"{context}: unsupported pair schema version")
    if "logical_pair_id" not in row or "analysis_role" not in row:
        raise LedgerError(f"{context}: v2 row lacks required semantic fields")
    try:
        q_representation_id = row["c1"]["representation_id"]
        p_representation_id = row["c2"]["representation_id"]
    except KeyError as exc:
        raise LedgerError(f"{context}: v2 row lacks representation identity") from exc
    from corpus_release import pair_id as make_pair_id

    expected_pair_id = make_pair_id(q_representation_id, p_representation_id)
    if row["pair_id"] != expected_pair_id:
        raise LedgerError(f"{context}: v2 pair_id digest mismatch")


def _unique_by(rows: list[dict], key: str, *, source: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in rows:
        value = row[key]
        if value in out:
            raise LedgerError(
                f"{source} contains duplicate {key} {value!r}")
        out[value] = row
    return out


def series_key(event: dict) -> tuple:
    if event["instrument_id"] is not None:
        return ("instrument", event["pair_id"], event["instrument_id"])
    return ("legacy", event["pair_id"], event["judge"], event["prompt_version"])


def score(event: dict) -> float:
    return event["corr"] * event["int"]


@dataclass(frozen=True)
class Ledger:
    verdicts: list[dict]
    pairs: dict[str, dict]
    manifest: dict[str, dict]
    instruments: dict[str, dict]

    def canonical_by_series(self) -> dict[tuple, dict]:
        canonical: dict[tuple, dict] = {}
        for event in self.verdicts:
            if event.get("repeat"):
                continue
            key = series_key(event)
            if key in canonical:
                raise LedgerError(f"duplicate canonical event for series {key}")
            canonical[key] = event
        return canonical

    def repeats(self) -> list[dict]:
        return [e for e in self.verdicts if e.get("repeat")]

    def canonical_for_pair(self, pair_id: str) -> list[dict]:
        return [e for e in self.canonical_by_series().values()
                if e["pair_id"] == pair_id]

    def canonical_for_series(
        self, *, judge: str | None = None, prompt_version: str | None = None,
        instrument_id: str | None = None,
    ) -> list[dict]:
        out = []
        for e in self.canonical_by_series().values():
            if judge is not None and e["judge"] != judge:
                continue
            if prompt_version is not None and e["prompt_version"] != prompt_version:
                continue
            if instrument_id is not None and e["instrument_id"] != instrument_id:
                continue
            out.append(e)
        return out


def load_ledger(ledger_dir: Path | None = None) -> Ledger:
    # ledger_dir defaults to the *current* value of the module-level
    # LEDGER_DIR, looked up at call time rather than bound at def time —
    # a `Path = LEDGER_DIR` default would freeze the value that existed at
    # import, so tests that monkeypatch ledger.LEDGER_DIR to point at a
    # fixture directory would be silently ignored.
    if ledger_dir is None:
        ledger_dir = LEDGER_DIR
    verdicts = load_jsonl(ledger_dir / "verdicts.jsonl")
    pair_rows = load_jsonl(ledger_dir / "pairs.jsonl")
    for index, row in enumerate(pair_rows):
        validate_pair_row(row, context=f"pairs.jsonl[{index}]")
    pairs = _unique_by(pair_rows, "pair_id", source="pairs.jsonl")
    manifest = _unique_by(
        load_jsonl(ledger_dir / "calibration_manifest.jsonl"), "pair_id",
        source="calibration_manifest.jsonl")
    instruments_path = ledger_dir / "instruments.jsonl"
    instruments = (_unique_by(
        load_jsonl(instruments_path), "instrument_id",
        source="instruments.jsonl")
        if instruments_path.exists() else {})
    for event in verdicts:
        if event["pair_id"] not in pairs:
            raise LedgerError(
                f"verdict references unknown pair {event['pair_id']!r}")
        iid = event["instrument_id"]
        if iid is not None and iid not in instruments:
            raise LedgerError(
                f"verdict references unregistered instrument {iid!r}")
    return Ledger(verdicts=verdicts, pairs=pairs, manifest=manifest,
                  instruments=instruments)
