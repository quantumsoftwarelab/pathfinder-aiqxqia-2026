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
import judge_transport
from _common import P3, corpus_by_item_id, item_block, render_prompt, sha256_text
from _ledger_common import (dump_jsonl_sorted, instrument_id,
                            instrument_identity_record, sha256_hex)
from judge_transport import (JUDGE_SYSTEM_PROMPT, RateLimited, RateLimitGate,
                             TransportError, build_claude_command,
                             build_pi_command, claude_system_prompt_digest,
                             parse_claude_json, parse_pi_stream,
                             rate_limit_wait_seconds)

PROVIDER_JUDGE_PREFIX = {"anthropic": "claude", "openai-codex": "codex"}

# Output contracts, by name. A judge's registry entry names the one it uses;
# the hash enters the instrument identity, so changing a contract yields a new
# series rather than silently extending an old one.
#
# v4's axes are feasibility and gain, but they are stored in the corr and int
# fields: verdicts.schema.json fixes those two names, bounds them to [0,1] and
# forbids additional properties. The mapping is feasibility -> corr,
# gain -> int, each divided by 100. It is recorded in judges.yaml beside the
# judge, and the prompt hash makes the series unmistakable.
OUTPUT_CONTRACTS = {
    "v2": ("corr", "int", "rationale"),
    "v4": ("feasibility", "gain", "rationale"),
}


def contract_sha256(name: str) -> str:
    return sha256_hex(json.dumps({"keys": sorted(OUTPUT_CONTRACTS[name])},
                                 sort_keys=True).encode("utf-8"))


# preserved so existing v2 instruments keep their identity
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


def resolve_cli_identity(binary: str) -> tuple[str, str]:
    version = subprocess.run([binary, "--version"], capture_output=True,
                             text=True, check=True).stdout.strip()
    import shutil
    path = shutil.which(binary)
    if path is None:
        raise RuntimeError(f"{binary} executable not found on PATH")
    # sha256 the raw binary bytes directly — do not decode/re-encode
    # through a text codec first, which would corrupt any byte >= 0x80.
    return version, sha256_hex(Path(path).read_bytes())


def resolve_instrument_identity(judge_cfg: dict, model_id: str,
                                prompt_path: Path | None = None) -> dict:
    transport = judge_cfg.get("transport", "claude-cli")
    binary = "pi" if transport == "pi" else "claude"
    cli_version, cli_sha256 = resolve_cli_identity(binary)
    if prompt_path is None:
        prompt_path = prompt_path_for(judge_cfg)
    tier_to_role = {"cheap": "cheap_selector", "strong": "strong_opinion",
                    "cheap_candidate": "cheap_candidate"}
    # Both transports now send an explicit system prompt. The claude
    # digest additionally covers the ambient CLAUDE.md the CLI injects and
    # that no flag suppresses while keeping subscription auth — without
    # that, editing ~/.claude/CLAUDE.md would change what the judge sees
    # while leaving instrument_id untouched.
    system_prompt_sha256 = (
        sha256_text(JUDGE_SYSTEM_PROMPT) if transport == "pi"
        else claude_system_prompt_digest())
    return instrument_identity_record(
        model_id=model_id, role=tier_to_role[judge_cfg.get("tier", "cheap")],
        transport=transport, effort=judge_cfg.get("settings", {}).get("effort"),
        prompt_sha256=sha256_text(prompt_path.read_text()),
        output_contract_sha256=contract_sha256(
            judge_cfg.get("output_contract", "v2")),
        cli_version=cli_version, cli_sha256=cli_sha256,
        system_prompt_sha256=system_prompt_sha256,
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


def series_tag(event: dict) -> str:
    """A series' identity with the pair dropped: an instrument_id for events
    minted by this runner, and judge+prompt_version for migrated legacy rows,
    which predate instruments and carry instrument_id null."""
    return (event["instrument_id"]
            or f"legacy:{event['judge']}:{event['prompt_version']}")


def ordered_pair_ids(led: ledger.Ledger, candidates: list[str],
                     deployment_path: Path | None = None,
                     order_by: str | None = None) -> list[str]:
    """Worklist ordered by the deployed cheap judge's score, descending.

    The cheap judge is a good sort key and a bad filter: measured on the
    100 pairs carrying both a Haiku-v2 and an Opus-4.8-v2 verdict, its
    top 10 is 90% strong-positive against a 24% base rate, but no
    threshold cuts volume without dropping positives. So order by it,
    never exclude by it — pairs with no cheap verdict sort last rather
    than being dropped. See plans/pathfinder3-strong-sweep-design.md.

    Ordering never affects correctness: resume matches on series key.
    """
    if order_by:
        # Order by an arbitrary completed series rather than the deployed
        # cheap selector -- e.g. rank by a strong judge once its sweep has
        # finished. `order_by` is a judge name, or a series tag prefix when
        # one judge has run more than one instrument: judge names are not
        # unique across instruments (v3 and v4 both write judge
        # "codex:gpt-5.6-sol"), and collapsing two scales into one pair_id
        # map would silently rank on whichever event happened to land last.
        events = [e for e in led.canonical_by_series().values()
                  if e["judge"] == order_by
                  or (len(order_by) >= 8 and series_tag(e).startswith(order_by))]
        if not events:
            raise SystemExit(f"--order-by {order_by!r} has no canonical verdicts")
        tags = {series_tag(e) for e in events}
        if len(tags) > 1:
            raise SystemExit(
                f"--order-by {order_by!r} is ambiguous: it spans "
                f"{len(tags)} series ({', '.join(sorted(t[:16] for t in tags))}). "
                "Pass a series tag prefix instead.")
        cheap = {e["pair_id"]: ledger.score(e) for e in events}
    else:
        if deployment_path is None:
            deployment_path = P3 / "protocol" / "deployment.yaml"
        dep = yaml.safe_load(deployment_path.read_text())["deployed_cheap_series"]
        cheap = {e["pair_id"]: ledger.score(e) for e in led.canonical_for_series(
            judge=dep["judge"], prompt_version=dep["prompt_version"],
            instrument_id=dep["instrument_id"])}
    # -1.0 default sorts unscored pairs last; the pair_id tie-break keeps
    # the order deterministic across runs.
    return sorted(candidates, key=lambda pid: (-cheap.get(pid, -1.0), pid))


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


def capture_usage(usage: dict) -> dict:
    """Ledger token/cost fields from one pi usage block.

    Cache reads and writes are input tokens that were billed at a
    different rate; the ledger records total tokens consumed, so they
    belong in tokens_in. est_cost_usd is pi's own computed total — do
    not recompute it from a price table, and in particular do not read
    ~/.pi/agent/models-store.json, which is a refresh cache that need
    not contain built-in models.

    On subscription auth this figure is notional, not a bill. It is
    recorded because it is the only consumption measure comparable
    across two vendors with different, unpublished plan accounting.
    """
    for key in ("input", "output", "cacheRead", "cacheWrite"):
        value = usage.get(key)
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"pi usage block missing integer {key!r}: {usage!r}")
    cost = usage.get("cost")
    if not isinstance(cost, dict) or not isinstance(cost.get("total"), (int, float)):
        raise ValueError(f"pi usage block missing cost.total: {usage!r}")
    return {
        "tokens_in": usage["input"] + usage["cacheRead"] + usage["cacheWrite"],
        "tokens_out": usage["output"],
        "est_cost_usd": float(cost["total"]),
    }


def sum_usage(records: list[dict]) -> dict:
    """Total usage across every attempt that was actually paid for.

    A call that returned malformed JSON still consumed tokens. Summing
    them into the accepted verdict keeps the recorded consumption
    honest; without it a retried pair understates what it cost.
    """
    return {
        "tokens_in": sum(r["tokens_in"] for r in records),
        "tokens_out": sum(r["tokens_out"] for r in records),
        "est_cost_usd": sum(r["est_cost_usd"] for r in records),
    }


def _call_judge(judge_cfg: dict, model_id: str, prompt: str,
                timeout: int = 900) -> judge_transport.JudgeResult:
    """One judge call. Raises RateLimited when the vendor closes the tap."""
    provider = judge_cfg["provider"]
    transport = judge_cfg.get("transport", "claude-cli")
    if transport == "pi":
        cmd = build_pi_command(
            provider=provider, model_id=model_id,
            effort=judge_cfg.get("settings", {}).get("effort"), prompt=prompt)
    else:
        cmd = build_claude_command(
            model_id=model_id,
            effort=judge_cfg.get("settings", {}).get("effort"),
            prompt=prompt)
    # stdin=DEVNULL is load-bearing, not tidiness: the child inherits the
    # parent's stdin otherwise and blocks indefinitely when that is a
    # pipe nobody closes — which is exactly the case under nohup. Found
    # by a live probe that hung until stdin was redirected.
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          timeout=timeout, cwd=tempfile.gettempdir(),
                          stdin=subprocess.DEVNULL)
    try:
        if transport == "pi":
            return parse_pi_stream(proc.stdout, want_provider=provider,
                                   want_model=model_id)
        return parse_claude_json(proc.stdout, want_model=model_id)
    except TransportError as exc:
        # Classify against everything the CLI said, not just the parse
        # failure. A rate limit routinely arrives on stderr with empty
        # stdout, in which case the parse error is merely "no output" and
        # carries no rate-limit vocabulary at all. Checking it alone made
        # a 429 look like a hard failure, burn all three retries, and drop
        # the pair (found in review of 7c5a882).
        evidence = f"{exc} | rc={proc.returncode} | {proc.stderr[:1000]}"
        wait = rate_limit_wait_seconds(evidence)
        if wait is not None:
            raise RateLimited(wait, evidence) from exc
        if proc.returncode != 0:
            raise TransportError(
                f"{cmd[0]} rc={proc.returncode}: {proc.stderr[:300]}") from exc
        raise


def _parse_verdict(raw: str, contract: str = "v2") -> dict:
    """Parse one verdict into the ledger's normalised shape.

    Returns corr, int and rationale whatever the contract. v4 reports
    feasibility and gain as integers 0-100; they are divided by 100 and
    stored in corr and int respectively, because verdicts.schema.json fixes
    those field names and bounds them to [0,1].
    """
    if raw.startswith("```"):
        raw = raw.strip("`\n")
        raw = raw[4:].lstrip() if raw.startswith("json") else raw
    if not (raw.startswith("{") and raw.endswith("}")):
        raise ValueError(f"not a bare JSON object: {raw[:120]!r}")
    obj = json.loads(raw)
    expected = set(OUTPUT_CONTRACTS[contract])
    if set(obj) != expected:
        raise ValueError(f"wrong keys for contract {contract}: {sorted(obj)}")
    if not isinstance(obj["rationale"], str):
        raise ValueError("rationale missing")

    if contract == "v2":
        for k in ("corr", "int"):
            value = obj[k]
            # bool is a subclass of int, so an unguarded isinstance check
            # would accept {"corr": true} as a valid score.
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{k} not numeric: {value!r}")
            if not 0 <= value <= 1:
                raise ValueError(f"{k} out of range: {value!r}")
        return obj

    out = {"rationale": obj["rationale"]}
    for src, dst in (("feasibility", "corr"), ("gain", "int")):
        value = obj[src]
        # integers only: the 0-100 grid is the point of this contract, and a
        # float would mean the judge ignored it.
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{src} must be an integer 0-100: {value!r}")
        if not 0 <= value <= 100:
            raise ValueError(f"{src} out of range: {value!r}")
        out[dst] = value / 100.0
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--judge", required=True)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0,
                    help="judge at most this many pairs THIS run. The cap is "
                         "applied after the resume skip, so it bounds the "
                         "work remaining, not the series total: a smoke run "
                         "of 3 followed by --limit 1000 leaves 1,003 judged")
    ap.add_argument("--pair-ids", nargs="*", default=None,
                    help="explicit pair ids to judge (default: full pairs.jsonl)")
    ap.add_argument("--repeat", action="store_true",
                    help="bypass the skip set; stamp repeat: true")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--order-by", default=None, metavar="SERIES",
                    help="rank the worklist by this series' scores instead of "
                         "the deployed cheap selector: a judge name "
                         "(codex:gpt-5.6-sol) when that judge has run one "
                         "instrument, else an instrument_id prefix")
    args = ap.parse_args()

    registry = yaml.safe_load((P3 / "protocol" / "judges.yaml").read_text())
    if args.judge not in registry["judges"]:
        raise SystemExit(f"unknown judge {args.judge!r}")
    judge_cfg = registry["judges"][args.judge]
    model_id = judge_cfg["model_id"]
    judge_full_id = f"{PROVIDER_JUDGE_PREFIX[judge_cfg['provider']]}:{model_id}"
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
        todo = ordered_pair_ids(
            led, [pid for pid in candidates if pid not in skip],
            order_by=args.order_by)
        if args.limit:
            todo = todo[:args.limit]
        print(f"{args.judge} ({judge_full_id}, instrument {iid[:12]}...): "
             f"{len(todo)} pairs to judge")

        items = corpus_by_item_id()
        template = prompt_path_for(judge_cfg).read_text()
        write_mutex = threading.Lock()
        today = date.today().isoformat()
        failures = []
        gate = RateLimitGate()

        def work(pid: str) -> None:
            q, p = items[led.pairs[pid]["c1"]["item_id"]], items[led.pairs[pid]["c2"]["item_id"]]
            check_provenance(pid, q, p, led)
            prompt = render_prompt(template, q, p)
            spent, verdict, error = [], None, None
            attempt = 0
            # Rate-limit pauses are deliberately unbounded: pausing until
            # the tap reopens IS the drip feed, and a weekly limit can
            # close for hours. This cannot spin, because every pause is
            # at least DEFAULT_BACKOFF_SECONDS. A run that looks hung
            # during a long closure is behaving correctly; the printed
            # pause line is how an operator tells that from a crash.
            while attempt < 3:
                gate.wait()
                try:
                    result = _call_judge(judge_cfg, model_id, prompt)
                except RateLimited as e:
                    # Not an attempt: the vendor never looked at the
                    # prompt, so this must not consume a retry.
                    gate.close_for(e.wait_seconds)
                    print(f"  rate limited on {pid}; pausing "
                          f"{e.wait_seconds:.0f}s", flush=True)
                    continue
                except Exception as e:  # noqa: BLE001
                    attempt += 1
                    error = f"attempt {attempt}: {e}"
                    continue
                attempt += 1
                try:
                    spent.append(capture_usage(result.usage))
                    verdict = _parse_verdict(
                        result.text, judge_cfg.get("output_contract", "v2"))
                    break
                except Exception as e:  # noqa: BLE001
                    error = f"attempt {attempt}: {e}"
            if verdict is None:
                with write_mutex:
                    failures.append((pid, error))
                return
            usage = sum_usage(spent)
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
