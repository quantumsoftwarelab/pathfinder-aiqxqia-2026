"""Run a registered judge over a set of pairs, appending to the ledger.

Every completed call appends one event to ledger/verdicts.jsonl; there is
no whole-file rewrite. Safety contract:

  Locking: an advisory fcntl.flock on verdicts.jsonl, held for the whole
  run; a second writer fails fast (LockHeldError).
  Atomic writes: worker threads hand completed events to a single
  mutex-guarded in-process writer; each event is one line, one write(),
  fsync'd immediately.
  Recovery: on startup, a torn trailing line halts the run and reports its
  byte offset for operator truncation. The runner never auto-rewrites
  history.
  Resume: the worklist is every requested pair minus pairs whose exact
  series key (instrument_id, not just judge+prompt_version) already has a
  canonical event. --repeat bypasses the skip set and stamps repeat: true.
  Registration: on first use, an instrument's full identity record is
  appended to ledger/instruments.jsonl.

Token/cost capture is deliberately NOT wired here (Phase 2 Step 6, gated
behind a live probe and a fake-executable test — this refactor makes no
live calls). capture_usage() therefore refuses by default; tests
monkeypatch it to exercise the rest of the append path. See
plans/pathfinder3-ledger-refactor-design.md.

Usage:
    python3 pathfinder3/scripts/judge_runner.py --judge cheap [--workers 4]
    python3 pathfinder3/scripts/judge_runner.py --judge cheap --pair-ids qsl:A::e001.001 qsl:A::e002.001
    python3 pathfinder3/scripts/judge_runner.py --judge cheap --repeat --run-id probe-1
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import tempfile
import threading
from datetime import date, datetime
from pathlib import Path

import yaml

import ledger
from _common import P3, PROMPT_PATH, corpus_by_item_id, item_block, render_prompt, sha256_text
from _ledger_common import (dump_jsonl_sorted, instrument_id,
                            instrument_identity_record, sha256_hex)

OUTPUT_CONTRACT_V2 = json.dumps(
    {"keys": sorted(["corr", "int", "rationale"])}, sort_keys=True
).encode("utf-8")
OUTPUT_CONTRACT_SHA256 = sha256_hex(OUTPUT_CONTRACT_V2)


def prompt_path_for(judge_cfg: dict) -> Path:
    """The judge prompt named by this registry entry.

    Replaces the module-level PROMPT_PATH constant: the cheap judge runs
    v2 and the strong judges run v3, so the prompt is a property of the
    judge, not of the runner.
    """
    return P3 / "protocol" / judge_cfg["prompt_file"]


class LockHeldError(RuntimeError):
    pass


class TornTailError(RuntimeError):
    pass


class AppendLock:
    def __init__(self, path: Path, blocking: bool = True):
        self.path = path
        self.blocking = blocking
        self._fh = None

    def __enter__(self) -> "AppendLock":
        self._fh = self.path.open("a+")
        flags = fcntl.LOCK_EX if self.blocking else (fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            fcntl.flock(self._fh.fileno(), flags)
        except BlockingIOError as e:
            self._fh.close()
            raise LockHeldError(f"another process holds the lock on {self.path}") from e
        return self

    def __exit__(self, *exc) -> None:
        fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        self._fh.close()

    def append_line(self, obj: dict) -> None:
        line = json.dumps(obj, ensure_ascii=False, sort_keys=True) + "\n"
        self._fh.write(line)
        self._fh.flush()
        os.fsync(self._fh.fileno())


def find_torn_tail(path: Path) -> int | None:
    if not path.exists():
        return None
    data = path.read_bytes()
    if not data:
        return None
    if not data.endswith(b"\n"):
        return data.rfind(b"\n") + 1
    offset = 0
    for line in data.split(b"\n"):
        if line.strip():
            try:
                json.loads(line)
            except json.JSONDecodeError:
                return offset
        offset += len(line) + 1
    return None


def resolve_cli_identity() -> tuple[str, str]:
    version = subprocess.run(["claude", "--version"], capture_output=True,
                             text=True, check=True).stdout.strip()
    import shutil
    binary = shutil.which("claude")
    if binary is None:
        raise RuntimeError("claude executable not found on PATH")
    # sha256 the raw binary bytes directly — do not decode/re-encode
    # through a text codec first, which would corrupt any byte >= 0x80.
    cli_sha256 = sha256_hex(Path(binary).read_bytes())
    return version, cli_sha256


def resolve_instrument_identity(judge_cfg: dict, model_id: str,
                                prompt_path: Path = PROMPT_PATH) -> dict:
    cli_version, cli_sha256 = resolve_cli_identity()
    prompt_sha256 = sha256_text(prompt_path.read_text())
    tier_to_role = {"cheap": "cheap_selector", "strong": "strong_opinion",
                    "cheap_candidate": "cheap_candidate"}
    return instrument_identity_record(
        model_id=model_id, role=tier_to_role[judge_cfg.get("tier", "cheap")],
        transport=judge_cfg.get("transport", "claude-cli"), effort=None,
        prompt_sha256=prompt_sha256, output_contract_sha256=OUTPUT_CONTRACT_SHA256,
        cli_version=cli_version, cli_sha256=cli_sha256,
        system_prompt_sha256=None,
    )


def register_instrument_if_new(identity: dict, led: ledger.Ledger,
                               instruments_path: Path, run_id: str) -> str:
    iid = instrument_id(identity)
    if iid in led.instruments:
        return iid
    row = dict(identity)
    row["instrument_id"] = iid
    row["registered_at"] = date.today().isoformat()
    row["first_run_id"] = run_id
    # Safe without its own lock only because the sole caller (main()) always
    # holds the verdicts.jsonl AppendLock before calling this; a future
    # standalone caller of register_instrument_if_new would need to take
    # that lock itself to keep this append race-free.
    with instruments_path.open("a") as f:
        f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())
    return iid


def skip_pair_ids(led: ledger.Ledger, *, judge: str, prompt_version: str | None,
                  instrument_id: str) -> set[str]:
    return {e["pair_id"] for e in
            led.canonical_for_series(judge=judge, instrument_id=instrument_id)}


def check_provenance(pid: str, q: dict, p: dict, led: ledger.Ledger) -> None:
    """Verify the corpus text for both sides of pair ``pid`` still matches
    the SHA-256 recorded on the pair row before a judge call is spent on it.

    Ported from the pre-refactor judge_runner.py (see pathfinder3-ledger-
    refactor-design.md), whose equivalent check silently disappeared in the
    ledger rewrite. A mismatch means the corpus snapshot has drifted since
    the pair was minted; raises SystemExit naming the pair and the
    mismatched side rather than spending a judge call on stale text.
    """
    pair = led.pairs[pid]
    for side, item in (("c1", q), ("c2", p)):
        got = sha256_text(item_block(item))
        want = pair[side]["input_sha256"]
        if got != want:
            raise SystemExit(
                f"provenance mismatch on {pid} {side}: corpus text no "
                "longer matches the pair's input_sha256; rebuild the "
                "ledger pairs or restore the corpus snapshot")


def _unwired_capture_usage(raw_output: str) -> dict:
    raise NotImplementedError(
        "token/cost capture not wired (Phase 2 Step 6); this refactor "
        "intentionally cannot append new-instrument verdicts yet — see "
        "plans/pathfinder3-ledger-refactor-design.md Non-goals. Tests "
        "monkeypatch judge_runner.capture_usage to exercise the append path.")


capture_usage = _unwired_capture_usage


def _call_judge(model_id: str, prompt: str, timeout: int = 300) -> str:
    proc = subprocess.run(
        ["claude", "--model", model_id, "--disallowedTools", "*",
         "--print", "--output-format", "text", "-p", prompt],
        capture_output=True, text=True, timeout=timeout,
        cwd=tempfile.gettempdir(),
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude rc={proc.returncode}: {proc.stderr[:300]}")
    return proc.stdout.strip()


def _parse_verdict(raw: str) -> dict:
    if raw.startswith("```"):
        raw = raw.strip("`\n")
        raw = raw[4:].lstrip() if raw.startswith("json") else raw
    if not (raw.startswith("{") and raw.endswith("}")):
        raise ValueError(f"not a bare JSON object: {raw[:120]!r}")
    obj = json.loads(raw)
    if set(obj) != {"corr", "int", "rationale"}:
        raise ValueError(f"wrong keys: {sorted(obj)}")
    for k in ("corr", "int"):
        value = obj[k]
        # bool is a subclass of int, so an unguarded isinstance check
        # would accept {"corr": true} as a valid score.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{k} not numeric: {value!r}")
        if not 0 <= value <= 1:
            raise ValueError(f"{k} out of range: {value!r}")
    if not isinstance(obj["rationale"], str):
        raise ValueError("rationale missing")
    # Deliberately not truncated. v2 capped the rationale at 140
    # characters for the cheap tier, and the old [:140] here silently
    # mangled anything longer; v3 asks for two or three sentences.
    return obj


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--judge", required=True)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--pair-ids", nargs="*", default=None,
                    help="explicit pair ids to judge (default: full pairs.jsonl)")
    ap.add_argument("--repeat", action="store_true",
                    help="bypass the skip set; stamp repeat: true")
    ap.add_argument("--run-id", default=None)
    args = ap.parse_args()

    if capture_usage is _unwired_capture_usage:
        raise SystemExit(
            "judge_runner cannot append verdicts yet: token/cost capture is "
            "deferred to Phase 2 Step 6 (see plans/pathfinder3-ledger-"
            "refactor-design.md). This refusal is by design in this "
            "refactor; inject judge_runner.capture_usage for testing.")

    registry = yaml.safe_load((P3 / "protocol" / "judges.yaml").read_text())
    if args.judge not in registry["judges"]:
        raise SystemExit(f"unknown judge {args.judge!r}")
    judge_cfg = registry["judges"][args.judge]
    model_id = judge_cfg["model_id"]
    judge_full_id = f"claude:{model_id}"
    run_id = args.run_id or datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{os.getpid():04x}"

    torn = find_torn_tail(ledger.VERDICTS_PATH)
    if torn is not None:
        raise SystemExit(
            f"torn trailing line in {ledger.VERDICTS_PATH} at byte offset "
            f"{torn}; back up the file, then truncate to that offset before "
            f"resuming")

    identity = resolve_instrument_identity(judge_cfg, model_id)

    # blocking=False is load-bearing: the safety contract (see module
    # docstring and plans/pathfinder3-ledger-refactor-design.md) requires a
    # second writer to fail fast with LockHeldError, not queue up and wait
    # for the first run to finish. A blocking acquire here would silently
    # violate that contract (and, worse, let a second process act on a
    # `led` snapshot taken before the first process's run committed its
    # instrument registration, risking a duplicate instruments.jsonl row).
    with AppendLock(ledger.VERDICTS_PATH, blocking=False) as lock:
        # led is loaded only once the lock is held, not before: reading it
        # earlier would risk a snapshot that is already stale relative to
        # another process's writes that land between the read and this
        # process's (successful) lock acquisition.
        led = ledger.load_ledger()
        iid = register_instrument_if_new(identity, led, ledger.INSTRUMENTS_PATH, run_id)
        skip = set() if args.repeat else skip_pair_ids(
            led, judge=judge_full_id, prompt_version=None, instrument_id=iid)
        candidates = args.pair_ids if args.pair_ids else sorted(led.pairs)
        todo = [pid for pid in candidates if pid not in skip]
        if args.limit:
            todo = todo[:args.limit]
        print(f"{args.judge} ({judge_full_id}, instrument {iid[:12]}...): "
             f"{len(todo)} pairs to judge")

        items = corpus_by_item_id()
        template = PROMPT_PATH.read_text()
        write_mutex = threading.Lock()
        today = date.today().isoformat()
        failures = []

        def work(pid: str) -> None:
            q, p = items[led.pairs[pid]["c1"]["item_id"]], items[led.pairs[pid]["c2"]["item_id"]]
            check_provenance(pid, q, p, led)
            prompt = render_prompt(template, q, p)
            raw, verdict, error = None, None, None
            for attempt in (1, 2, 3):
                try:
                    raw = _call_judge(model_id, prompt)
                    verdict = _parse_verdict(raw)
                    break
                except Exception as e:  # noqa: BLE001
                    error = f"attempt {attempt}: {e}"
            if verdict is None:
                with write_mutex:
                    failures.append((pid, error))
                return
            usage = capture_usage(raw)
            event = {
                "schema_version": 2, "pair_id": pid, "judge": judge_full_id,
                "prompt_version": None, "instrument_id": iid,
                "role": identity["role"], "corr": float(verdict["corr"]),
                "int": float(verdict["int"]), "score_explanation": verdict["rationale"],
                "derived_idea": None, "transport": identity["transport"],
                "effort": identity["effort"], "run_id": run_id, "judged_at": today,
                "tokens_in": usage["tokens_in"], "tokens_out": usage["tokens_out"],
                "est_cost_usd": usage["est_cost_usd"],
            }
            if args.repeat:
                event["repeat"] = True
            with write_mutex:
                lock.append_line(event)

        import concurrent.futures as cf
        with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
            list(ex.map(work, todo))

        print(f"judged {len(todo) - len(failures)}/{len(todo)}; "
             f"failures: {len(failures)}")
        for pid, error in failures:
            print(f"  FAILED {pid}: {error}")
        return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
