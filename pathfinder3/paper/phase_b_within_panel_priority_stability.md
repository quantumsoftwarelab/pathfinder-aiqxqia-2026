# Phase B within-panel priority stability

## Decision

Freeze a provisional initial tranche of 100 pairs by reproducing the
audited canonical v5 equal-mean-rank membership. Within that fixed
tranche, prioritise by Opus canonical midrank percentile and break ties
by ascending `pair_id`. Keep GPT percentile and the observed inter-judge
percentile gap as descriptive metadata only.

This reduces observed within-tranche priority volatility while preserving
both judges in the frozen membership decision. Population-wide membership
stability remains **unevaluated**: the repeat panel contains only the
already selected 100 pairs.

## Observed priority retention

Each cell is mean within-panel top-K retention, with the minimum and
maximum across available ordered read assignments in parentheses.
Assignments reuse reads and are exact sensitivity summaries, not
independent trials or confidence intervals.

| Policy | Top 10 | Top 20 | Top 50 |
|---|---:|---:|---:|
| Opus alone | 90.0% (90.0%--90.0%) | 95.0% (95.0%--95.0%) | 94.0% (94.0%--94.0%) |
| GPT alone | 45.0% (40.0%--60.0%) | 50.0% (40.0%--65.0%) | 73.3% (70.0%--78.0%) |
| Equal mean rank | 66.7% (60.0%--80.0%) | 60.8% (45.0%--75.0%) | 72.8% (68.0%--78.0%) |
| Inverse-variance rank (diagnostic) | 75.0% (60.0%--90.0%) | 81.7% (75.0%--90.0%) | 86.8% (84.0%--90.0%) |
| Equal mean, two GPT reads | 73.3% (70.0%--80.0%) | 68.3% (65.0%--70.0%) | 79.7% (78.0%--82.0%) |

Opus alone retained 90.0%, 95.0%, and 94.0% at K=10, 20, and 50.
It was more repeatable on this observed panel than every combination
that allowed GPT to affect priority order. Averaging two GPT reads
improved the equal-mean policy but did not close the gap.

## Noise diagnostic

The mean pair-level unbiased within-pair variance was
`0.001337906` for Opus
and `0.018333410` for GPT
in frozen-percentile units. A mechanical inverse-variance calculation
therefore assigns 93.2% to Opus and 6.8%
to GPT. This is diagnostic only: the judges have systematic differences,
so the weights are not an optimal estimator of a shared truth.

The corresponding pooled `D_within` values are
`0.025902293` for Opus and
`0.121189764` for GPT.

## Frozen artefact

The [content-addressed tranche manifest](../manifests/phase-b-initial-tranche-v1-8d42b2b961859358.json) records the
exact reference and repeat runs, semantic evidence digests, selection
formulae, set and order digests, input hashes, canonical scores,
percentiles, ranks, and descriptive gap metadata.

- Manifest body: `8d42b2b961859358e70e2f2b76bebdf31b5e64625e0a2051bcafe4d3f429208c`
- Selected pair set: `86326cc29b3e11003d4d92ee43ef03a515608ba7c3f5e15b54b71369b85f1d7b`
- Ordered pair list: `b0e05974e00e4633c1fd2a4217db0b47d0fee073dac19de6143f19fb02fee198`

## Limits

- The analysis does not measure selection stability against the 903 pairs
  below the original cutoff.
- Opus repeatability is based on one repeat contrast in this panel.
- A large pair-level GPT--Opus gap is not confirmed disagreement and does
  not trigger automatic reprioritisation.
- K=100 is a bounded initial tranche, not an empirically optimal size.

## Regeneration

```sh
uv run python3 pathfinder3/scripts/analyse_phase_b_priority_stability.py
uv run python3 pathfinder3/scripts/analyse_phase_b_priority_stability.py --check
```
