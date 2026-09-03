# Pathfinder3 shortlist snapshot

Generated from the committed ledger with explicit strong-judge instrument ids.

- repo commit: `80db5f000e31436627b8be2b979c534b8b0edcb9`
- left instrument: `7f01f7e54dc02bbc59a447ea77c51c9fe8b16291b8d4e52e37ce1ed0b433b046` (`claude-opus-5`)
- right instrument: `b2a053b3cd994b15dd9f976f8777479b6559e80391a840c1c39bd3dcdf8c7818` (`gpt-5.6-sol`)
- left canonical pairs: 10912
- right canonical pairs: 10912
- shared canonical pairs: 10912
- shared corpus snapshots: d8deb3c

## Selector rules

- rank direction: 1 is best
- tie convention: midrank over descending scores
- final tie-break: `pair_id` ascending
- mean rank: `(left_rank + right_rank) / 2`
- worst rank: `max(left_rank, right_rank)`

## Shortlist tables

| selector | K | distinct QSL papers | distinct vendor papers | distinct vendors | largest vendor share | cutoff tie band |
|---|---|---|---|---|---|---|
| mean_rank | 100 | 43 | 49 | 13 | ibm 48 (48.0%) | 3 |
| mean_rank | 200 | 54 | 74 | 13 | ibm 103 (51.5%) | 2 |
| worst_rank | 100 | 43 | 47 | 13 | ibm 48 (48.0%) | 1 |
| worst_rank | 200 | 54 | 76 | 13 | ibm 107 (53.5%) | 5 |

## Selector overlap

| K | overlap | jaccard |
|---|---|---|
| 100 | 92 | 0.852 |
| 200 | 183 | 0.843 |

## Judge coverage

| selector | K | left overlap | right overlap |
|---|---|---|---|
| mean_rank | 100 | 51 | 61 |
| mean_rank | 200 | 110 | 131 |
| worst_rank | 100 | 50 | 55 |
| worst_rank | 200 | 103 | 122 |

## Stability

- left: n=103, spearman between repeats 0.594, top-10 retained 53.3%, top-20 retained 60.0%
- right: n=103, spearman between repeats 0.682, top-10 retained 43.3%, top-20 retained 63.3%
