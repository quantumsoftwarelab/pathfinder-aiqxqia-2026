"""Shared helpers for the pathfinder3 calibration pipeline."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
P3 = REPO / "pathfinder3"
SCHEMA_PATH = P3 / "protocol" / "pair_matrix.schema.json"
PROMPT_PATH = P3 / "protocol" / "judge_prompt_v1.md"
PROMPT_VERSION = "v1"


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def dump_jsonl(path: Path, rows: list[dict]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


NO_ABSTRACT = "(not available; judge from the title)"


def item_block(item: dict) -> str:
    """The exact per-item text block rendered into the judge prompt.

    ``input_sha256`` in pair rows is the SHA-256 of this block, byte for
    byte, including the placeholder used when no abstract is available.
    """
    abstract = item["abstract"] or NO_ABSTRACT
    return f"Title: {item['title']}\n\nAbstract: {abstract}"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def render_prompt(template: str, q: dict, p: dict) -> str:
    return (template
            .replace("Title: {{Q_TITLE}}\n\nAbstract: {{Q_ABSTRACT}}", item_block(q))
            .replace("Title: {{P_TITLE}}\n\nAbstract: {{P_ABSTRACT}}", item_block(p)))


def corpus_by_item_id() -> dict[str, dict]:
    items = {}
    for name in ("qsl_papers.jsonl", "vendor_papers.jsonl"):
        for row in load_jsonl(P3 / "corpus" / name):
            items[row["item_id"]] = row
    return items
