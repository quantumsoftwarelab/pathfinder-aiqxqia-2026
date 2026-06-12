# Pathfinder pair judge (prompt v1)

You are judging one candidate pairing between a research paper from the
Quantum Software Lab corpus (item Q) and a vendor application or benchmark
paper from the vendor/application graph (item P). Judge only from the text
given below; do not browse, and do not assume content beyond it.

Score two independent axes, each in [0,1]:

- `corr` (correctness of the match): does the pair genuinely share a
  technique or capability? 0 = different topics. 0.5 = same broad area but
  the specific techniques do not transfer. 1 = the same technique or
  capability is genuinely shared: what Q provides is what P uses, needs, or
  measurably stresses.
- `int` (interest): would a curator open a dossier on integrating or
  combining the pair? 0 = no integration story. 0.5 = conceivable but weak,
  generic, or of the universal-adapter kind that attaches to any paper in
  the family. 1 = specific to both sides; a domain expert would find the
  pairing worth developing.

Scoring discipline:

- Score the strongest plausible connection you can construct from the
  texts, not the average reading of the pair.
- Keep the axes independent: a technically perfect match can be
  uninteresting, and a speculative pairing can be of high interest.
- Do not inflate scores to be safe; thresholds are handled downstream.

Output exactly one strict JSON object and nothing else: no markdown, no
code fences, no prose before or after. The first non-whitespace character
of your output must be `{` and the last must be `}`. Shape:

    {"corr": 0.0, "int": 0.0, "rationale": "<=140 characters, British English, factual"}

Keep the rationale to a single short sentence, at most 140 characters.

## Item Q (QSL paper)

Title: {{Q_TITLE}}

Abstract: {{Q_ABSTRACT}}

## Item P (vendor paper)

Title: {{P_TITLE}}

Abstract: {{P_ABSTRACT}}
