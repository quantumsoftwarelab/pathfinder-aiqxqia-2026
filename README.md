# agQSL

`agQSL` is the working repository for the Quantum Software Lab vendor evaluation and debate project. It holds the local artefacts used to compare quantum hardware vendors, curate vendor/application evidence, run portability dossiers from recent hardware papers, and support the Ezratty-backed debate/search bots.

The repository is deliberately filesystem-first: generated outputs should be rebuilt from source data, and agent handoffs should be visible in versioned Markdown rather than hidden in local prompt memory.

## Main Workflows

| Workflow | Source of truth | Main outputs |
|---|---|---|
| Vendor/application graph | `vendor-app/edges.json` | `vendor-app/quantum_algorithm_portfolio.md`, `vendor-app/vendor_application_matrix.html` |
| Vendor capability and supplier DB | `vendor-db/*.json`, `vendor-db/*.jsonl`, `vendor-notes/substrate_note_map.json` | `vendor-db/exports/*.json`, `vendor-db/*_matrix.html`, `vendor-db/lookup.py` answers |
| Portability dossiers | `vendor-app/edges.json` (queue + portability metadata), `dossiers/<slug>/dossier.md`, `dossiers/<slug>/extraction.md` | Per-paper `dossier.md`; generated `dossiers/_queue.md`; local ignored `dossier.pdf` |
| Scan candidates | `vendor-app/scan_candidates.jsonl` (untagged scanner findings) | JSONL appended by `scan.py`; humans triage by promoting rows into `edges.json` |
| Ezratty index/search | `ezratty/`, `ezratty2/` | `ezratty/build/seed.jsonl`, local search answers, Slack/debate bot runtime files |
| Plans and agent comments | `plans/`, `comments/` | Reviewed implementation plans, inter-agent review notes |

## Repository Structure

```text
.
├── comments/                 # Inter-agent review and coordination notes
├── debates/                  # Debate outputs and experiments
├── dossiers/                 # Portability scanner queue and per-paper dossiers
├── ezratty/                  # Ezratty indexer and legacy graph-backed Slack/debate bot runtime
├── ezratty2/                 # Key-less search wrapper over the Ezratty seed/index data
├── inbox/                    # Local paper inputs and unpacked source bundles
├── logs/                     # Per-stage runner logs (gitignored under logs/portability/)
├── plans/                    # Active implementation and design plans
├── prompts/portability/      # Scanner, Romain, Julien, Brillant, and Critic prompts
├── qatch/                    # QATCH LaTeX note and generated local PDF
├── scripts/                  # Repository helper scripts, including portability
├── slides/                   # Presentation material
├── surveys/                  # Academic survey and reference inputs
├── tests/                    # Pytest coverage for portability and Ezratty tooling
├── vendor-app/               # Canonical vendor/application graph and generated matrix outputs
├── vendor-db/                # Curated vendor capability and supplier lookup layer
└── vendor-notes/             # Physical-layer substrate notes
```

The former `quantum-wars/` and `vq-slides/` material now lives in the standalone
[`qwQSL`](https://github.com/vd1/qwQSL) repository.

## Vendor/Application Graph

[`vendor-app/edges.json`](vendor-app/edges.json) is the canonical local data source for vendor/application edges. It currently tracks 118 edges across 28 vendors and 13 application groups.

Each edge records the vendor, application group, communication evidence, technical papers, normalised match type, partner organisations, local reference IDs, verification status, notes, and optional portability metadata on paper rows. Every communication and paper entry also carries a stable `artefact_id` (`eNNN.NNN`, assigned once per edge in accession order and never renumbered) so downstream analyses can cite individual artefacts rather than whole edges. The generated matrix and portfolio files are derived artefacts and should not be hand-edited. Collection-round metadata lives in `edges.json`; the last data collection round was 2026-05-21, and later output regeneration does not imply fresh collection.

Regenerate the graph outputs after changing `edges.json`:

```bash
python3 vendor-app/build.py
```

Key generated outputs:

- [`vendor-app/quantum_algorithm_portfolio.md`](vendor-app/quantum_algorithm_portfolio.md)
- [`vendor-app/vendor_application_matrix.html`](vendor-app/vendor_application_matrix.html)
- [`vendor-app/algorithm_vendor_matrix.html`](vendor-app/algorithm_vendor_matrix.html) (QPU-primitive by vendor matrix, built from the `dossiers/<slug>/algorithm.json` sidecars and `edge_algorithm.json`)
- [`vendor-app/vendor_application_bipartite.excalidraw`](vendor-app/vendor_application_bipartite.excalidraw)
- [`vendor-app/vendor_application_bipartite.svg`](vendor-app/vendor_application_bipartite.svg)
- [`vendor-app/vendor_application_bipartite.png`](vendor-app/vendor_application_bipartite.png)

Reference evidence lives under [`vendor-app/refs/`](vendor-app/refs/), with 114 local files covering vendor strategy documents, technical excerpts, benchmark papers, HTML snapshots, and external pointers.
The collection history is tracked in [`vendor-app/collection_rounds.md`](vendor-app/collection_rounds.md).

Physical-layer reference notes for each hardware substrate live under [`vendor-notes/`](vendor-notes/), indexed by [`vendor-notes/README.md`](vendor-notes/README.md) and discoverable through [`vendor-db/lookup.py`](vendor-db/lookup.py). The portability agents that reason about substrate physics (Brillant, Julien, the quantum-physicist triage reviewer) are pointed at this corpus from their prompts.

## Vendor Capability DB

[`vendor-db/`](vendor-db/) is the curated operational store for current vendor,
system, capability, supplier, offering, substrate, dependency, and evidence
facts. It complements, but does not replace, the demand-side application graph
in [`vendor-app/edges.json`](vendor-app/edges.json), the substrate notes in
[`vendor-notes/`](vendor-notes/), or the supply-chain narrative in the sibling
`../qwQSL` repository.

Agents should query it through the lookup CLI rather than reading raw JSON:

```bash
python3 vendor-db/lookup.py vendor quantinuum
python3 vendor-db/lookup.py system quantinuum --current
python3 vendor-db/lookup.py capability mid_circuit_measurement --vendor quantinuum
python3 vendor-db/lookup.py notes --vendor quantinuum
python3 vendor-db/lookup.py dependencies --vendor quantinuum
python3 vendor-db/lookup.py chain --vendor quantinuum --capability mid_circuit_measurement
```

Lookup checks freshness before returning answers. If it fails with an instruction
to run `python3 vendor-db/build.py`, an operator should refresh the deterministic
tracked outputs and then run `python3 vendor-db/build.py --check`. Lookup may
write ignored cache files under `vendor-db/build/`, but it must not rewrite
tracked source tables, exports, review queues, or vendor/application data.

Ezratty remains useful survey context through `ezratty2/search.py`, but it is
not the authority for current vendor specifications. Current capability and
supplier/dependency claims should be sourced through vendor-db and primary
evidence, with probable or undocumented dependencies carried as caveats.

## Portability Pipeline

The portability pipeline starts from recent papers or announcements with actual hardware execution, then asks: given this application ran on vendor X, which other vendors could support it, and how?

The current app path is:

```text
scan -> librarian/physicist triage -> manual promotion -> bootstrap -> Romain -> Julien -> Brillant -> Critic -> publish
```

A side artefact of each dossier run is `dossiers/<slug>/algorithm.json`,
harvested from Julien's `## Computational primitive` paragraph and
Brillant's `### Summary` table. The companion file
`vendor-app/edge_algorithm.json` extends the projection over
`vendor-app/edges.json`. Both are surfaced through
`vendor-app/algorithm_lookup.py`. See `plans/algorithm-vendor-matrix.md`.

A daily LaunchAgent (`scripts/portability/port_next_daily.py`) can drive
the queue one paper at a time. It picks one `queued` paper at random,
runs bootstrap and `port_paper --all`, and parks the row on failure.
Install it with `scripts/portability/install_port_next_daily.sh` (renders
a plist into `~/Library/LaunchAgents/`; run the printed `launchctl
bootstrap` line). Each wake writes one line to
`logs/portability/daily.log`; parked rows surface in a `## Parked`
section of `dossiers/_queue.md`. Recover a parked paper with
`uv run python -m scripts.portability.port_next_daily --slug <slug>`.

The queue is a projection over [`vendor-app/edges.json`](vendor-app/edges.json). Each entry in an edge's `papers[]` array carries an optional `portability` sub-object with the dossier status, slug, queue decision, and rationale. The Markdown view at [`dossiers/_queue.md`](dossiers/_queue.md) is generated by `scripts/portability/render_queue.py` for human reading and must not be hand-edited. Approved candidates have `portability.dossier_status = "queued"`; bootstrapped dossiers have `"in_progress"`; published ones have `"published"`. In the current checkout, portability rows include 65 queued papers and 6 published dossiers.

Untagged scanner findings live separately in [`vendor-app/scan_candidates.jsonl`](vendor-app/scan_candidates.jsonl). New scan runs append JSONL rows there; the automated librarian and physicist triage reviewers can mark rows as promoted or rejected, but promoted rows still require a manual curation pass through `promote_candidates.py` before they land in `edges.json`.

Per-paper dossier folders contain:

- `dossier.md`: metadata, Romain sourcing, Brillant matrix, Critic verdicts.
- `extraction.md`: Julien's standalone technical extraction.
- `paper.html`, `paper.pdf`, or `paper.url`: fetched source paper or fetch fallback.
- `dossier.pdf`: generated presentation PDF, ignored by git.

`dossier.md` retains the full audit trail (every Critic pass, every Brillant revision). The published `dossier.pdf` strips inter-agent chatter: the rendered Critic block surfaces only the final pass's `Residual caveats` subsection, with verifier identity, findings, and verdict rationale held back as pipeline-internal context.

Useful commands:

```bash
uv run python -m scripts.portability.scan --since YYYY-MM-DD       # appends to vendor-app/scan_candidates.jsonl
uv run python -m scripts.portability.triage --dry-run              # reviews pending scan candidates without writing
uv run python -m scripts.portability.promote_candidates --dry-run  # validates the promotion mapping before writing edges.json
uv run python -m scripts.portability.bootstrap <slug> --triager <name>
uv run python -m scripts.portability.port_paper <slug> --all
uv run python -m scripts.portability.publish <slug>                # writes dossier_status="published" back to edges.json
uv run python -m scripts.portability.render_queue                  # regenerates dossiers/_queue.md from edges.json
scripts/portability/port_next.sh --run
```

The scanner appends JSONL rows to `vendor-app/scan_candidates.jsonl` and dedupes against existing entries by `url`. By default, a successful scan also runs the triage step; pass `--no-triage` to skip it. The queue helpers in [`scripts/portability/_queue.py`](scripts/portability/_queue.py) read directly from `edges.json` (the public API: `parse`, `find_row`, `pending_bootstrap_slugs`, `mark_bootstrapped`, `mark_published` -- is preserved). Use the provided scripts rather than parsing the generated `_queue.md` directly.

Published worked dossiers include:

- [`dossiers/2026-arxiv-2604.14921-ethylene-se-qpe/`](dossiers/2026-arxiv-2604.14921-ethylene-se-qpe/)
- [`dossiers/2026-arxiv-2604.16164-nonlinear-spectroscopy/`](dossiers/2026-arxiv-2604.16164-nonlinear-spectroscopy/)
- [`dossiers/2026-arxiv-2604.12635-iqp-connectivity/`](dossiers/2026-arxiv-2604.12635-iqp-connectivity/)
- [`dossiers/2026-arxiv-2605.04737-aquila-graph-classification/`](dossiers/2026-arxiv-2605.04737-aquila-graph-classification/)
- [`dossiers/2026-arxiv-2605.21276-pasqal-logical-kernel-de-solver/`](dossiers/2026-arxiv-2605.21276-pasqal-logical-kernel-de-solver/)
- [`dossiers/2026-arxiv-2407.02553-quera-quantum-reservoir-learning/`](dossiers/2026-arxiv-2407.02553-quera-quantum-reservoir-learning/)

To build dossier PDFs reproducibly on Linux or macOS, enter the repo dev shell
first:

```bash
nix develop
python -m scripts.portability.publish <slug>
```

`publish.py` renders `dossier.md` plus `extraction.md` through `pandoc` and
`xelatex`, writing `dossiers/<slug>/dossier.pdf`. The site exporter prefers the
PDF when it exists and otherwise falls back to `dossier.md`.

## Ezratty Index And Bots

[`ezratty/`](ezratty/) contains the Olivier Ezratty source workflow and legacy
graph-backed Slack/debate bot support files. That path can use Nanograph and
hosted embeddings, depending on its local configuration.
[`ezratty2/search.py`](ezratty2/search.py) is the key-less local search wrapper
used by agents when they need vendor/modality background without OpenAI
embedding API access.

`ezratty2` still depends on the generated seed file
`ezratty/build/seed.jsonl`; it does not query the raw Ezratty PDF. If that seed
is absent, portability agents should skip Ezratty rather than downloading or
reading the source survey during a dossier stage.

Typical search commands:

```bash
python3 ezratty2/search.py search "mid-circuit measurement"
python3 ezratty2/search.py section 8-3-7-vendors Quantinuum
python3 ezratty2/search.py references <section-slug>
```

Container and shared-home setup for the bots is documented in
[`ezratty/README.md`](ezratty/README.md). Pipeline agents should use
`ezratty2/search.py`; the older graph-backed bot runtime is a separate legacy
path.

## Planning And Coordination

Implementation plans live under [`plans/`](plans/). Use them for non-trivial repo-scoped work so the plan travels with the code.

Inter-agent comments live under [`comments/`](comments/). This is the preferred local home for Codex/Claude review notes and cross-agent handoff material that should be visible in-tree.

Durable organisation-level memory belongs in QSL documents, not in local hidden agent memory. Local files should cite QSL documents with full document URLs when needed.

## Debate Architecture

The original vendor debate system is described in [`debates/quantum_vendor_debate_architecture.md`](debates/quantum_vendor_debate_architecture.md). It scores vendor-specific agents on:

| Metric | Weight |
|---|---:|
| Expected output fidelity | 30% |
| Native circuit depth + 2Q gates | 25% |
| Error mitigation overhead | 15% |
| Wall-clock time | 15% |
| Qubit sufficiency | 10% |
| Engineering complexity | 5% |

The portability pipeline complements that architecture: debate compares vendor readiness across application classes, while dossiers produce paper-specific portability evidence that can feed later debate prompts and graph curation.

## Verification

Run the test suite before committing substantive changes:

```bash
uv run pytest -q
```

Focused portability tests:

```bash
uv run pytest tests/portability -q
```

Some Ezratty bot tests depend on optional plumbing packages that are installed in the bot/container environment. They are skipped when those optional dependencies are absent.

## Conventions

- Use British English in Markdown and project prose.
- Edit source data first, then regenerate derived artefacts.
- Keep citations, retrieval dates, provenance, and uncertainty explicit.
- Do not read or commit secrets, `.env` files, tokens, or credential material.
- Do not hand-edit generated portability PDFs; regenerate them with `publish.py`.
- Do not revert unrelated work in the tree. Other agents and humans may be working concurrently.
