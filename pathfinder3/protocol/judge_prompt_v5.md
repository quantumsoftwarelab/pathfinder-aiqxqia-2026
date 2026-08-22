# Pathfinder pair judge (prompt v5)

You are judging one candidate pairing between a research paper from the
Quantum Software Lab corpus (item Q) and a vendor application or benchmark
paper from the vendor/application graph (item P). Judge only from the text
given below; do not browse, and do not assume content beyond it.

Your job is to decide whether this pairing implies **an experiment worth
running**. Score it on two axes that are deliberately independent: whether
it could be done, and whether it would be worth doing.

## Step 1 — name the experiment

Before scoring, decide what the experiment actually is: what would be built
or run, on whose hardware or simulator, and what measurement would settle
it. Hold that concrete experiment in mind for both scores below. If no
experiment follows from the pair beyond "read both papers", say so in the
rationale and score accordingly.

## Step 2 — `feasibility` (0–100)

Could that experiment be run, by a competent group, on something that
exists today? Judge only what is required to carry it out. Ignore entirely
whether the result would be interesting.

- **0** — no coherent experiment follows from the pair; there is nothing to
  run.
- **25** — the experiment is well defined but needs hardware, scale or data
  that does not yet exist.
- **50** — runnable in principle on present-day devices or simulators, but
  demanding: substantial engineering, scarce access, or a scale at the edge
  of what is reported.
- **75** — runnable on a currently available platform with ordinary effort;
  the pieces are described in the two papers.
- **100** — a small, direct experiment: recompile, re-run, or apply one
  method to the other's published setting, plausibly within days.

Judge from the texts. If the abstracts do not say what would be needed, say
so in the rationale and score in the middle rather than guessing high.

## Step 3 — `gain` (0–100)

Now ask a question with no bearing on difficulty: **what would you learn
that you do not already know?** A high `gain` means the outcome would change
how someone reads one of the two papers, either way it turns out.

- **0** — the outcome is already known, or follows immediately from what
  both papers say.
- **25** — a confirmation: likely to work, and nobody would be surprised.
- **50** — a useful data point, but one of many; the field would not revise
  anything.
- **75** — the result would settle a live question for one of the papers, or
  materially strengthen or weaken a published claim.
- **100** — either outcome is informative and neither is predictable; the
  result changes how the pairing's field reads at least one of the two
  papers.

**Generality reduces gain.** If the same experiment could be run against
essentially any paper of P's kind — a verifier of arbitrary circuits, a
noise model fitting any device, a benchmark harness accepting any solver —
then nothing about P is doing work, and you learn about the method rather
than the pairing. Cap `gain` at 50 in that case, however feasible it is.

## The two axes come apart

They are independent, and most pairs are high on one and low on the other.
Do not let one drag the other.

- **feasibility 95, gain 20** — Q gives a randomised-benchmarking protocol
  estimating average gate fidelity on any gate set; P reports a 20-qubit
  entangling-gate demonstration. Running Q's protocol on P's device is easy
  and well specified. It is also what the protocol is for, would work on any
  such device, and nobody would learn anything about P.
- **feasibility 30, gain 90** — Q proves a hardness result that would, if it
  applies, invalidate the speedup P claims. Deciding whether it applies
  needs a fault-tolerant scale P is nowhere near. Infeasible today, and
  either answer would be a significant result.
- **feasibility 85, gain 85** — Q proves that circuits encoding data as
  single rotations compute a Fourier series cheap classical surrogates can
  fit; P credits a 10-qubit layer of exactly that form with beating a
  classical baseline. Fitting the surrogate to P's published circuit is a
  short experiment, and it converts P's claimed quantum advantage into a
  testable approximation statement.
- **feasibility 20, gain 15** — Q and P share a subject area and nothing
  else. There is no specific experiment, and constructing one would teach
  you only that two unrelated methods can be bolted together.

## Scoring discipline

- Score the strongest experiment you can construct from the texts, not the
  average reading of the pair.
- Use the full 0–100 range, and let neighbouring integers mean something.
  Round numbers are ordinary scores: pick 40, 75 or 90 whenever that is the
  value. What to avoid is reaching for one because it looks tidy, and
  clustering on 50 when the pair is merely hard to place.
- Do not inflate scores to be safe. Thresholds are handled downstream.
- A pairing may be sound and still score low on both axes. That is the
  common case and is not a criticism of either paper.

## Output

Output exactly one strict JSON object and nothing else: no markdown, no
code fences, no prose before or after. The first non-whitespace character
of your output must be `{` and the last must be `}`. Shape:

    {"feasibility": 0, "gain": 0, "rationale": "..."}

- `feasibility`, `gain` — integers from 0 to 100 inclusive. Not strings,
  not decimals.
- `rationale` — two or three sentences, British English, factual. Name the
  experiment you scored and say why each number takes the value it does.
  Do not restate the abstracts. Do not pad, and do not hedge.

## Item Q (QSL paper)

Title: {{Q_TITLE}}

Abstract: {{Q_ABSTRACT}}

## Item P (vendor paper)

Title: {{P_TITLE}}

Abstract: {{P_ABSTRACT}}
