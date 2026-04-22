# agQSL

`agQSL` is the working repository for the Quantum Software Lab vendor evaluation and debate project. It holds the local artefacts used to compare quantum hardware vendors, curate vendor/application evidence, run portability dossiers from recent hardware papers, and support the Ezratty-backed debate/search bots.

The repository is deliberately filesystem-first: generated outputs should be rebuilt from source data, and agent handoffs should be visible in versioned Markdown rather than hidden in local prompt memory.

## Main Workflows

| Workflow | Source of truth | Main outputs |
|---|---|---|
| Vendor/application graph | `vendor-app/edges.json` | `vendor-app/quantum_algorithm_portfolio.md`, `vendor-app/vendor_application_matrix.html` |
| Portability dossiers | `dossiers/_queue.md`, `dossiers/<slug>/dossier.md`, `dossiers/<slug>/extraction.md` | Per-paper `dossier.md`; local ignored `dossier.pdf` |
| Ezratty index/search | `ezratty/`, `ezratty2/` | `ezratty/build/seed.jsonl`, local search answers, Slack/debate bot runtime files |
| Plans and agent comments | `plans/`, `comments/` | Reviewed implementation plans, inter-agent review notes |

## Repository Structure

```text
.
├── comments/                 # Inter-agent review and coordination notes
├── debates/                  # Debate outputs and experiments
├── dossiers/                 # Portability scanner queue and per-paper dossiers
├── ezratty/                  # Ezratty indexer, Nanograph assets, Slack/debate bot runtime
├── ezratty2/                 # Key-less search wrapper over the Ezratty seed/index data
├── plans/                    # Active implementation and design plans
├── prompts/portability/      # Scanner, Romain, Julien, Brillant, and Critic prompts
├── quantum-wars/             # Source notes and presentations for the broader evaluation work
├── scripts/portability/      # Scanner, bootstrap, stage runner, publisher
├── slides/                   # Presentation material
├── surveys/                  # Academic survey and reference inputs
├── tests/                    # Pytest coverage for portability and Ezratty tooling
├── vendor-app/               # Canonical vendor/application graph and generated matrix outputs
└── vq-slides/                # Brainstorm presentation material
```

## Vendor/Application Graph

[`vendor-app/edges.json`](vendor-app/edges.json) is the canonical local data source for vendor/application edges. It currently tracks 79 edges across 27 vendors and 13 application groups.

Each edge records the vendor, application group, communication evidence, technical papers, match type, partner organisations, local reference IDs, verification status, and notes. The generated matrix and portfolio files are derived artefacts and should not be hand-edited.

Regenerate the graph outputs after changing `edges.json`:

```bash
python3 vendor-app/build.py
```

Key generated outputs:

- [`vendor-app/quantum_algorithm_portfolio.md`](vendor-app/quantum_algorithm_portfolio.md)
- [`vendor-app/vendor_application_matrix.html`](vendor-app/vendor_application_matrix.html)
- [`vendor-app/vendor_application_bipartite.png`](vendor-app/vendor_application_bipartite.png)

Reference evidence lives under [`vendor-app/refs/`](vendor-app/refs/), with 59 local files covering vendor strategy documents, technical excerpts, benchmark papers, and external pointers.

## Portability Pipeline

The portability pipeline starts from recent papers or announcements with actual hardware execution, then asks: given this application ran on vendor X, which other vendors could support it, and how?

The current app path is:

```text
scan queue -> human triage -> bootstrap -> Romain -> Julien -> Brillant -> Critic -> publish
```

The queue lives at [`dossiers/_queue.md`](dossiers/_queue.md). Approved rows are marked `[x]`; bootstrapped rows become `[x] (bootstrapped)` and get a folder under `dossiers/<slug>/`.

Per-paper dossier folders contain:

- `dossier.md`: metadata, Romain sourcing, Brillant matrix, Critic verdicts.
- `extraction.md`: Julien's standalone technical extraction.
- `paper.html`, `paper.pdf`, or `paper.url`: fetched source paper or fetch fallback.
- `dossier.pdf`: generated presentation PDF, ignored by git.

Useful commands:

```bash
uv run python -m scripts.portability.scan --since YYYY-MM-DD
uv run python -m scripts.portability.bootstrap <slug> --triager <name>
uv run python -m scripts.portability.port_paper <slug> --all
uv run python -m scripts.portability.publish <slug>
scripts/portability/port_next.sh --run
```

The scanner appends to the queue and must not overwrite prior scan sections. The parser in [`scripts/portability/_queue.py`](scripts/portability/_queue.py) handles escaped table pipes, so use the provided scripts rather than ad hoc `awk` parsing.

The current worked dossiers include:

- [`dossiers/2026-arxiv-2604.14921-ethylene-se-qpe/`](dossiers/2026-arxiv-2604.14921-ethylene-se-qpe/)
- [`dossiers/2026-arxiv-2604.16164-nonlinear-spectroscopy/`](dossiers/2026-arxiv-2604.16164-nonlinear-spectroscopy/)

## Ezratty Index And Bots

[`ezratty/`](ezratty/) contains the indexed Olivier Ezratty source workflow, Nanograph configuration, and Slack/debate bot support files. [`ezratty2/search.py`](ezratty2/search.py) is the key-less local search wrapper used by agents when they need vendor/modality background without requiring external credentials.

Typical search commands:

```bash
python3 ezratty2/search.py search "mid-circuit measurement"
python3 ezratty2/search.py section 8-3-7-vendors Quantinuum
python3 ezratty2/search.py references <section-slug>
```

Container and shared-home setup for the bots is documented in [`ezratty/README.md`](ezratty/README.md). The optional `nanograph` binary is not bundled; if it is missing, use `ezratty2/search.py` or set the documented runtime path before relying on direct Nanograph commands.

## Planning And Coordination

Implementation plans live under [`plans/`](plans/). Use them for non-trivial repo-scoped work so the plan travels with the code.

Inter-agent comments live under [`comments/`](comments/). This is the preferred local home for Codex/Claude review notes and cross-agent handoff material that should be visible in-tree.

Durable organisation-level memory belongs in QSL documents, not in local hidden agent memory. Local files should cite QSL documents with full document URLs when needed.

## Debate Architecture

The original vendor debate system is described in [`quantum_vendor_debate_architecture.md`](quantum_vendor_debate_architecture.md). It scores vendor-specific agents on:

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
