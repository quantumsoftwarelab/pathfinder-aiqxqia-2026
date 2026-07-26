"""CLI transports for pathfinder3 judges: pi and claude.

Owns everything specific to invoking a judge CLI and reading its
output: command construction, response parsing, rate-limit detection,
and the shared pause gate. Two transports live here because they share
that error taxonomy and gate, and differ only in how a call is built
and how its response is shaped.

Deliberately knows nothing about ledgers, verdicts, or pairs, so it can
be tested without any ledger fixture. See
plans/pathfinder3-strong-sweep-design.md.
"""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass

JUDGE_SYSTEM_PROMPT = (
    "You are a strict evaluator. Follow the output contract in the user "
    "message exactly. Emit exactly one JSON object and nothing else: no "
    "prose, no explanation, no markdown, no code fences."
)


def build_pi_command(*, provider: str, model_id: str, effort: str | None,
                     prompt: str,
                     system_prompt: str = JUDGE_SYSTEM_PROMPT) -> list[str]:
    """Argv for one judge call.

    The isolation flags are load-bearing, not defensive: -nt -ns -ne -np
    -nc disable tools, skills, extensions, prompt templates and
    AGENTS.md/CLAUDE.md discovery, so the call is a bare model
    invocation that nothing in the working directory can influence.
    --no-session stops a 10,912-pair sweep writing a session file per
    call. --system-prompt pins the system text instead of inheriting
    Pi's default coding-assistant prompt, so the instrument identity
    covers everything the model actually saw.
    """
    cmd = ["pi", "--provider", provider, "--model", model_id]
    if effort:
        cmd += ["--thinking", effort]
    cmd += [
        "--mode", "json", "-p", "--no-session", "--no-approve",
        "-nt", "-ns", "-ne", "-np", "-nc",
        "--system-prompt", system_prompt,
        prompt,
    ]
    return cmd


class TransportError(RuntimeError):
    """The transport produced no usable verdict for this call."""


class ModelMismatchError(TransportError):
    """Pi answered with a different provider or model than was requested."""


@dataclass(frozen=True)
class JudgeResult:
    text: str
    usage: dict
    provider: str
    model: str
    response_model: str | None
    stop_reason: str


def parse_pi_stream(stdout: str, *, want_provider: str,
                    want_model: str) -> JudgeResult:
    """Extract the one terminal assistant message from a pi JSON stream.

    Non-JSON and non-terminal lines are skipped rather than fatal: the
    stream carries a session header and several lifecycle events before
    the answer, and only the last assistant ``message_end`` is the
    verdict.
    """
    terminal = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "message_end":
            continue
        message = event.get("message")
        if isinstance(message, dict) and message.get("role") == "assistant":
            terminal = message
    if terminal is None:
        raise TransportError("no terminal assistant message_end event in pi output")

    stop = terminal.get("stopReason")
    if stop == "error":
        raise TransportError(
            f"pi reported stopReason=error: "
            f"{str(terminal.get('errorMessage') or '')[:400]}")
    if stop != "stop":
        # "length" means the answer was cut off mid-JSON; "aborted" and
        # "toolUse" should be unreachable with -nt. None is usable.
        raise TransportError(f"unusable stopReason {stop!r}")

    provider = terminal.get("provider")
    model = terminal.get("model")
    response_model = terminal.get("responseModel")
    if provider != want_provider or model != want_model:
        raise ModelMismatchError(
            f"requested {want_provider}/{want_model}, got {provider}/{model}")
    # responseModel is the model the server actually ran and may carry a
    # date suffix on the requested alias; anything not prefixed by the
    # requested id is a genuine substitution.
    if response_model is not None and not response_model.startswith(want_model):
        raise ModelMismatchError(
            f"requested {want_model}, server ran {response_model}")

    usage = terminal.get("usage")
    if not isinstance(usage, dict):
        raise TransportError("terminal assistant event carries no usage block")

    text = "".join(
        block.get("text", "") for block in terminal.get("content", [])
        if isinstance(block, dict) and block.get("type") == "text"
    ).strip()
    return JudgeResult(text=text, usage=usage, provider=provider, model=model,
                    response_model=response_model, stop_reason=stop)


DEFAULT_BACKOFF_SECONDS = 900.0

_RATE_LIMIT_WORDS = re.compile(
    r"rate[ _-]?limit|usage limit|quota|too many requests|\b429\b", re.I)
_MINUTES_HINT = re.compile(r"try again in ~?\s*(\d+)\s*min", re.I)
_SECONDS_HINT = re.compile(r"retry[- ]after[\"'\s:=]+(\d+)", re.I)
_RESETS_AT = re.compile(r"resets?_at[\"'\s:=]+(\d{9,11})")


class RateLimited(TransportError):
    """The vendor closed the tap. Not a transient error: never retried."""

    def __init__(self, wait_seconds: float, message: str = ""):
        super().__init__(message or f"rate limited, wait {wait_seconds:.0f}s")
        self.wait_seconds = wait_seconds


def rate_limit_wait_seconds(error_message: str) -> float | None:
    """Seconds to wait, or None if this is not a rate limit.

    The single place that knows what a rate-limit message looks like.
    When a real one is observed, add it verbatim to the tests rather
    than widening these patterns speculatively.
    """
    if not error_message or not _RATE_LIMIT_WORDS.search(error_message):
        return None
    match = _RESETS_AT.search(error_message)
    if match:
        return max(0.0, float(match.group(1)) - time.time())
    match = _MINUTES_HINT.search(error_message)
    if match:
        return float(match.group(1)) * 60.0
    match = _SECONDS_HINT.search(error_message)
    if match:
        return float(match.group(1))
    return DEFAULT_BACKOFF_SECONDS


class RateLimitGate:
    """A shared pause every worker checks before spending a call.

    Deliberately not a threading.Event: with all workers blocked in
    Event.wait() none is left to set it again, so the run deadlocks.
    Here each waiter waits with a timeout and the first to see the
    deadline pass reopens the gate for everyone.
    """

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._cond = threading.Condition()
        self._deadline = 0.0

    def close_for(self, seconds: float) -> None:
        with self._cond:
            # max(): a second worker reporting a shorter wait must not
            # shorten a longer pause already in force.
            self._deadline = max(self._deadline,
                                 self._clock() + max(0.0, seconds))

    def remaining(self) -> float:
        with self._cond:
            return max(0.0, self._deadline - self._clock())

    def wait(self) -> None:
        with self._cond:
            while True:
                remaining = self._deadline - self._clock()
                if remaining <= 0:
                    self._deadline = 0.0
                    self._cond.notify_all()
                    return
                self._cond.wait(timeout=remaining)


# --- claude CLI transport -------------------------------------------------
#
# Used for the Anthropic side because Pi's anthropic provider is refused
# outright on a subscription: the API returns 400 "Third-party apps now
# draw from your extra usage, not your plan limits". The claude CLI is
# first-party, so it draws on the plan, and it is the transport that
# produced every historical verdict in the ledger.
#
# Its --output-format json emits ONE JSON object, not an event stream, so
# it needs its own parser rather than parse_pi_stream.


def build_claude_command(*, model_id: str, effort: str | None,
                         prompt: str) -> list[str]:
    """Argv for one judge call through the first-party claude CLI.

    --disallowedTools "*" is the claude-side equivalent of pi's -nt: the
    judge must not be able to touch the filesystem or the network.

    --effort must be passed whenever the registry declares one, or the
    instrument identity would record an effort the call never requested.
    """
    cmd = ["claude", "--model", model_id, "--disallowedTools", "*"]
    if effort:
        cmd += ["--effort", effort]
    cmd += ["--print", "--output-format", "json", "-p", prompt]
    return cmd


def parse_claude_json(stdout: str, *, want_model: str) -> JudgeResult:
    """Parse one claude --output-format json response.

    The usage block is shaped differently from pi's: cache tokens are
    named cache_read_input_tokens / cache_creation_input_tokens, and the
    cost is total_cost_usd at the top level. It is normalised here to
    pi's field names so capture_usage has a single shape to consume.
    """
    text = stdout.strip()
    if not text:
        raise TransportError("claude produced no output")
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise TransportError(
            f"claude output is not JSON: {text[:200]!r}") from exc
    if not isinstance(obj, dict):
        raise TransportError(f"claude output is not a JSON object: {text[:120]!r}")

    # is_error and api_error_status can both be set on a clean exit, so
    # exit status alone is not a sufficient success check here either.
    if obj.get("is_error") or obj.get("api_error_status"):
        raise TransportError(
            f"claude reported an error: {str(obj.get('api_error_status') or obj.get('result'))[:400]}")
    if obj.get("subtype") not in (None, "success"):
        raise TransportError(f"claude subtype {obj.get('subtype')!r}")

    usage = obj.get("usage")
    if not isinstance(usage, dict):
        raise TransportError("claude response carries no usage block")
    cost = obj.get("total_cost_usd")
    if not isinstance(cost, (int, float)):
        raise TransportError("claude response carries no total_cost_usd")

    models = obj.get("modelUsage") or {}
    served = next(iter(models), None)
    if served is not None and not served.startswith(want_model):
        raise ModelMismatchError(
            f"requested {want_model}, claude ran {served}")

    result = obj.get("result")
    if not isinstance(result, str):
        raise TransportError("claude response carries no result string")

    normalised = {
        "input": int(usage.get("input_tokens", 0)),
        "output": int(usage.get("output_tokens", 0)),
        "cacheRead": int(usage.get("cache_read_input_tokens", 0)),
        "cacheWrite": int(usage.get("cache_creation_input_tokens", 0)),
        "cost": {"total": float(cost)},
    }
    return JudgeResult(text=result.strip(), usage=normalised,
                       provider="anthropic", model=want_model,
                       response_model=served,
                       stop_reason=str(obj.get("stop_reason") or "end_turn"))
