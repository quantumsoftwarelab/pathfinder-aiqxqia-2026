# Quantum Vendor Debate System (wwQSL)

An evidence-driven knowledge base and multi-agent debate framework for comparing quantum hardware vendors on algorithm implementation readiness.

## Repository structure

```
.
├── quantum_vendor_debate_architecture.md   # System design: scoring rubric, debate protocol, agent roles
├── quantum_vendor_debate_slides.html       # Presentation deck (HTML)
├── ww_slides_Mar_4_2026.pdf                # Presentation deck (PDF)
├── surveys/                                # Academic reference papers (Dalzell et al., resource estimation, etc.)
├── vendor-app/                             # Core data and generated outputs
│   ├── edges.json                          # Canonical vendor–application graph (77 edges, 27 vendors)
│   ├── build.py                            # Generates portfolio markdown + interactive HTML matrix
│   ├── quantum_algorithm_portfolio.md      # Generated: per-substrate vendor portfolio
│   ├── vendor_application_matrix.html      # Generated: interactive colour-coded matrix
│   ├── vendor_application_bipartite.png    # Bipartite graph visualisation
│   └── refs/                               # 59 evidence files (vendor strategy docs, paper excerpts, benchmarks)
└── vq-slides/                              # Brainstorm presentation (retro CRT aesthetic)
```

## Vendor–application matrix

`vendor-app/edges.json` is the single source of truth. Each edge connects a vendor to an application with:

- Communication evidence (press releases, blog posts)
- Technical papers (arxiv, journals)
- Match type (deployed / published result / strategic partnership / thematic)
- Partner organisations
- Paper summary and justification
- URL verification status

**27 vendors** across 8 substrates: superconducting, trapped ion, neutral atom, photonic, annealing, quantum-inspired, NV center, cloud platform.

**13 application groups**: optimisation, chemistry & materials, drug discovery, finance, logistics & scheduling, quantum ML, simulation & physics, error correction, crypto & security, energy & climate, networking & edge, boson sampling, platforms & tooling.

Run `python3 vendor-app/build.py` to regenerate the markdown portfolio and HTML matrix from `edges.json`.

## Reference files

`vendor-app/refs/` contains 59 files in several categories:

- **Vendor strategy docs** (`sp01`–`sp05`, `ti01`–`ti08`, `na01`–`na04`, `ph01`–`ph04`, `an01`): per-vendor portfolio evidence organised by substrate
- **Technical excerpts** (`ref01`–`ref31`): paper summaries, algorithm details, and partnership documentation linked from edges
- **Benchmarking papers** (`ref07`, `ref23`, `ref24`): general background (QAOA MaxkSAT, SupermarQ, application-oriented benchmarks) not tied to specific edges
- **External links** (`ref25`–`ref28`): pointers to Wikipedia/GitHub for vendors without edges (1QBit, QCWare, QMill, QEDC)

49 ref files are linked from edges in `edges.json`; 10 are standalone background reference material.

## Debate architecture

The system is designed around structured technical debates between vendor-specific agents, scored on:

| Metric | Weight |
|--------|--------|
| Expected output fidelity | 30% |
| Native circuit depth + 2Q gates | 25% |
| Error mitigation overhead | 15% |
| Wall-clock time | 15% |
| Qubit sufficiency | 10% |
| Engineering complexity | 5% |

See `quantum_vendor_debate_architecture.md` for the full protocol, anti-gaming rules, and MVP scope.

## Surveys

Academic papers seeding the algorithm definitions and resource estimates:

- Dalzell et al. quantum algorithms survey (2310.03011)
- Requirements for practical quantum advantage (2211.07629)
- QC applications in health and medicine (2301.09106)
- Quantum computing vision and challenges (2403.02240)
- Resource estimation techniques (2402.12434)


### Addendum

- need a framework to evaluate debate structures
- testing agents expertise in vendor, in general quantum applications