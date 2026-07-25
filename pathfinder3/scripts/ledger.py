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

LEDGER_DIR = P3 / "ledger"
VERDICTS_PATH = LEDGER_DIR / "verdicts.jsonl"
PAIRS_PATH = LEDGER_DIR / "pairs.jsonl"
MANIFEST_PATH = LEDGER_DIR / "calibration_manifest.jsonl"
INSTRUMENTS_PATH = LEDGER_DIR / "instruments.jsonl"


class LedgerError(RuntimeError):
    pass


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
    pairs = {r["pair_id"]: r for r in load_jsonl(ledger_dir / "pairs.jsonl")}
    manifest = {r["pair_id"]: r
                for r in load_jsonl(ledger_dir / "calibration_manifest.jsonl")}
    instruments_path = ledger_dir / "instruments.jsonl"
    instruments = ({r["instrument_id"]: r for r in load_jsonl(instruments_path)}
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
