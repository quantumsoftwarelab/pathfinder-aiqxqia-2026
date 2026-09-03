# Pathfinder AIQxQIA 2026 public supplementary repository

This repository is the public supplementary material for the Pathfinder AIQxQIA paper package. The supplementary material contains submission-time title and abstract corpora, the verdict ledger, instrument and input hashes, and provenance manifests.

The released data expose every pair identity, both judges' scores, and recorded rationales. Readers can inspect any queue entry and apply an independent evaluation protocol to the same frozen records.

Included calculation scripts live under `pathfinder3/scripts/`. Retained judge prompts live under `pathfinder3/protocol/`, and retained execution code lives under `pathfinder3/scripts/judge_runner.py` and `pathfinder3/scripts/judge_transport.py`.

Run `make verify` for offline reproduction. Offline verification is bounded to inspection, recalculation, and released snapshot consistency. Model calls are outside offline reproduction, and fresh proprietary-model calls are outside the reproduction claim.

The manuscript is distributed separately during anonymous review. This attributed repository remains private until the review embargo ends, and the README will link the manuscript only after a publisher or arXiv URL exists.

Paper full text is excluded while metadata and source links are retained.

## Public release note

Commit author email metadata is normalised for publication. William Waites commits use `ww@inf`. Vincent Danos commits use `vd@ens`.

## Scientific interpretation limits

The strong-judge sweep was ungated.

Judge agreement does not establish discovery accuracy or scientific validity.

The Haiku thresholds are retrospective.

Within-panel stability is bounded to the measured panels and shortlist depths.

Vendor composition reflects the frozen corpus as well as the selector.

## Released materials

- Submission-time corpora: [`pathfinder3/corpus/qsl_papers.jsonl`](pathfinder3/corpus/qsl_papers.jsonl) and [`pathfinder3/corpus/vendor_papers.jsonl`](pathfinder3/corpus/vendor_papers.jsonl)
- Verdict ledger and pair identities: [`pathfinder3/ledger/verdicts.jsonl`](pathfinder3/ledger/verdicts.jsonl), [`pathfinder3/ledger/pairs.jsonl`](pathfinder3/ledger/pairs.jsonl), [`pathfinder3/ledger/instruments.jsonl`](pathfinder3/ledger/instruments.jsonl), and [`pathfinder3/ledger/calibration_manifest.jsonl`](pathfinder3/ledger/calibration_manifest.jsonl)
- Frozen reported outputs: [`pathfinder3/paper/shortlist_snapshot.md`](pathfinder3/paper/shortlist_snapshot.md), [`pathfinder3/paper/shortlist_snapshot.json`](pathfinder3/paper/shortlist_snapshot.json), [`pathfinder3/paper/judge_stability_v5.json`](pathfinder3/paper/judge_stability_v5.json), [`pathfinder3/paper/phase_b_within_panel_priority_stability.md`](pathfinder3/paper/phase_b_within_panel_priority_stability.md), and [`pathfinder3/paper/phase_b_within_panel_priority_stability.json`](pathfinder3/paper/phase_b_within_panel_priority_stability.json)
- Provenance manifests: `release/manifests/*.json`, [`release/path-index.json`](release/path-index.json), and `release/publication_review.json` (`release/publication_review.json`; see [release/path-index.json](release/path-index.json))

Legacy rows without instrument hashes remain in `pathfinder3/ledger/verdicts.jsonl` for historical comparison (11,312 rows in this release). `judge_prompt_v1.md` and `judge_prompt_v2.md` are retained as historical prompts. Frozen results use instrument-pinned prompt versions v3, v4, and v5.

## Metric family mapping

| Metric family | Instrument id | Prompt version | Prompt hash | Output contract | System instruction boundary |
| --- | --- | --- | --- | --- | --- |
| legacy Haiku threshold counts | legacy rows without instrument hashes | judge_prompt_v2 historical prompt | 8984dd744c9aaa5a69a969a1cfbc7c690a892452ced68bb92090736d08165168 | legacy rows without instrument hashes | legacy rows without instrument hashes |
| Phase A shortlist snapshot and reported score-scale comparison | 7f01f7e54dc02bbc59a447ea77c51c9fe8b16291b8d4e52e37ce1ed0b433b046, b2a053b3cd994b15dd9f976f8777479b6559e80391a840c1c39bd3dcdf8c7818, e7ed3e7159a34ac3352b0d59ea64e50c11f2118858afd9df737dccbbfddc6c5c | judge_prompt_v3 | 1ac3c25f03661c5b880cc3d204eccdc2c421c22d026d61ffaa9c2c702f21c397 | 8377100f299229cdc58c57f31122c1b6ca66045142b4438e8a1a65a0fd6538cd | prompt markdown retained; system instruction boundary recorded by instrument-specific system prompt hash |
| v4 strong-judge transition rows | 425d1c378968a6d24fabf07208a7bd1a83a7b8af83d109b94e0e3a16571dc146, 60ed46389e21beb12e5be94ce08f5aa6042d4f84855544a52b9e38bdcab8e53c | judge_prompt_v4 | db111b02a6b9aac642d5c43c821da2aff9fd0188592228c635355ef75c0adc55 | 2ae297b3c6ea5ea31ba25339c390b3d93a8e126775a19fe27c0117280a192f72 | prompt markdown retained; system instruction boundary recorded by instrument-specific system prompt hash |
| v5 judge stability and within-panel priority analysis | b99996c890becb87ec59a144e419a013a8284c98bbf7e3656c830f52526ad5f0, d19bb7d063eb10e65b6e8aa0944bc2a5767494a67c743d213268a6d365caa0b0 | judge_prompt_v5 | f7ef292b682d53fdb9ac91555952f7244ff46967c6e0b653528ca672f063e9cf | 2ae297b3c6ea5ea31ba25339c390b3d93a8e126775a19fe27c0117280a192f72 | prompt markdown retained; system instruction boundary recorded by instrument-specific system prompt hash |

## Represented instrument registry

| Instrument id | Model | Prompt version | Prompt hash | Output contract | System instruction boundary |
| --- | --- | --- | --- | --- | --- |
| 34fb2deadc200869886751e8db3bbedce99a21280bc8bfc402f35cbb3e5b09ed | gpt-5.6-sol | judge_prompt_v5 | f7ef292b682d53fdb9ac91555952f7244ff46967c6e0b653528ca672f063e9cf | 2ae297b3c6ea5ea31ba25339c390b3d93a8e126775a19fe27c0117280a192f72 | ffc54f65ad277ac11b3b5e5b3ea8b20baafb43c2b9130b6694dfd04cc3977e85 |
| 425d1c378968a6d24fabf07208a7bd1a83a7b8af83d109b94e0e3a16571dc146 | claude-opus-5 | judge_prompt_v4 | db111b02a6b9aac642d5c43c821da2aff9fd0188592228c635355ef75c0adc55 | 2ae297b3c6ea5ea31ba25339c390b3d93a8e126775a19fe27c0117280a192f72 | 9af236fc23ec70a5c7d442ab2de9730132e3f61bf0e9660984ebc4cf4be22fa1 |
| 60ed46389e21beb12e5be94ce08f5aa6042d4f84855544a52b9e38bdcab8e53c | gpt-5.6-sol | judge_prompt_v4 | db111b02a6b9aac642d5c43c821da2aff9fd0188592228c635355ef75c0adc55 | 2ae297b3c6ea5ea31ba25339c390b3d93a8e126775a19fe27c0117280a192f72 | ffc54f65ad277ac11b3b5e5b3ea8b20baafb43c2b9130b6694dfd04cc3977e85 |
| 6bb8f0d8f42fc5908b7c5cd1717bb4503c27a86ecb6f9d2e2919ed8331f66396 | claude-opus-5 | historical prompt | 143196d0414f9480f90596aa4f9df17556dfebe2752efd6823c537897f37c5b5 | f0b6729a477ae5e6aaf1cdc1dacfbe906c2ee8da2c2a7398224ede13af265738 | 6deb1b429fdf418a9829a7480a153614bf96d15eeefd86597f14e9f4d515380b |
| 7f01f7e54dc02bbc59a447ea77c51c9fe8b16291b8d4e52e37ce1ed0b433b046 | claude-opus-5 | judge_prompt_v3 | 1ac3c25f03661c5b880cc3d204eccdc2c421c22d026d61ffaa9c2c702f21c397 | 8377100f299229cdc58c57f31122c1b6ca66045142b4438e8a1a65a0fd6538cd | 9af236fc23ec70a5c7d442ab2de9730132e3f61bf0e9660984ebc4cf4be22fa1 |
| b2a053b3cd994b15dd9f976f8777479b6559e80391a840c1c39bd3dcdf8c7818 | gpt-5.6-sol | judge_prompt_v3 | 1ac3c25f03661c5b880cc3d204eccdc2c421c22d026d61ffaa9c2c702f21c397 | 8377100f299229cdc58c57f31122c1b6ca66045142b4438e8a1a65a0fd6538cd | ffc54f65ad277ac11b3b5e5b3ea8b20baafb43c2b9130b6694dfd04cc3977e85 |
| b99996c890becb87ec59a144e419a013a8284c98bbf7e3656c830f52526ad5f0 | claude-opus-5 | historical prompt | 2fe11bc807ad9527610849df751474f6a5529ff79230aa93615fb19efa118455 | 2ae297b3c6ea5ea31ba25339c390b3d93a8e126775a19fe27c0117280a192f72 | 9af236fc23ec70a5c7d442ab2de9730132e3f61bf0e9660984ebc4cf4be22fa1 |
| d19bb7d063eb10e65b6e8aa0944bc2a5767494a67c743d213268a6d365caa0b0 | gpt-5.6-sol | historical prompt | 2fe11bc807ad9527610849df751474f6a5529ff79230aa93615fb19efa118455 | 2ae297b3c6ea5ea31ba25339c390b3d93a8e126775a19fe27c0117280a192f72 | ffc54f65ad277ac11b3b5e5b3ea8b20baafb43c2b9130b6694dfd04cc3977e85 |
| e7ed3e7159a34ac3352b0d59ea64e50c11f2118858afd9df737dccbbfddc6c5c | claude-opus-5 | judge_prompt_v3 | 1ac3c25f03661c5b880cc3d204eccdc2c421c22d026d61ffaa9c2c702f21c397 | 8377100f299229cdc58c57f31122c1b6ca66045142b4438e8a1a65a0fd6538cd | historical row without system instruction hash |
| e8c7663f83bece170a9bcabb833951615c378cd1d04369d055c8dec09bdbca01 | claude-opus-5 | historical prompt | 2fe11bc807ad9527610849df751474f6a5529ff79230aa93615fb19efa118455 | 2ae297b3c6ea5ea31ba25339c390b3d93a8e126775a19fe27c0117280a192f72 | 9af236fc23ec70a5c7d442ab2de9730132e3f61bf0e9660984ebc4cf4be22fa1 |
