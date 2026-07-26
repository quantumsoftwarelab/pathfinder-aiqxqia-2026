"""Canonical serialisation, hashing, and schema validation shared by every
pathfinder3 ledger script (migration, view builder, judge runner, loader).

Kept independent of vendor-db/_lib.py deliberately: pathfinder3 is a
self-contained subsystem (see pathfinder3/scripts/_common.py) and should not
take a runtime dependency on a different subsystem's internals. The
validate_schema/json_type_matches pair below is adapted from vendor-db's
implementation, extended with const and $ref/$defs support (needed because
pathfinder3's schemas use those, vendor-db's don't) and without the
empty-string check (pathfinder3 uses null for absent optional fields, not
empty strings).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from datetime import date
from pathlib import Path
from typing import Any

from _common import P3


class LedgerSchemaError(RuntimeError):
    pass


def canonical_json_bytes(obj: Any) -> bytes:
    return json.dumps(
        obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def dump_jsonl_sorted(path: Path, rows: list[dict]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(tmp, path)


def json_dumps_pretty(data: Any) -> str:
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def instrument_identity_record(
    *, model_id: str, role: str, transport: str, effort: str | None,
    prompt_sha256: str, output_contract_sha256: str, cli_version: str,
    cli_sha256: str,
) -> dict:
    return {
        "model_id": model_id,
        "role": role,
        "transport": transport,
        "effort": effort,
        "prompt_sha256": prompt_sha256,
        "output_contract_sha256": output_contract_sha256,
        "cli_version": cli_version,
        "cli_sha256": cli_sha256,
    }


def instrument_id(identity: dict) -> str:
    return sha256_hex(canonical_json_bytes(identity))


def load_schema(name: str) -> dict:
    return json.loads((P3 / "protocol" / name).read_text())


def _json_type_matches(value: Any, expected_type: str) -> bool:
    if expected_type == "null":
        return value is None
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "object":
        return isinstance(value, dict)
    raise LedgerSchemaError(f"unsupported schema type {expected_type!r}")


def validate_schema(value: Any, schema: dict, *, context: str) -> None:
    expected_types = schema.get("type")
    if isinstance(expected_types, str):
        expected_types = [expected_types]
    if expected_types is not None and not any(
        _json_type_matches(value, t) for t in expected_types
    ):
        raise LedgerSchemaError(
            f"{context}: expected {expected_types}, got {type(value).__name__}")

    if "const" in schema and value != schema["const"]:
        raise LedgerSchemaError(f"{context}: expected const {schema['const']!r}, got {value!r}")

    if "enum" in schema and value not in schema["enum"]:
        raise LedgerSchemaError(f"{context}: {value!r} not in enum {schema['enum']}")

    if "pattern" in schema and isinstance(value, str) and not re.search(schema["pattern"], value):
        raise LedgerSchemaError(f"{context}: {value!r} does not match {schema['pattern']!r}")

    if "minLength" in schema and isinstance(value, str):
        if len(value) < schema["minLength"]:
            raise LedgerSchemaError(
                f"{context}: length {len(value)} is below minimum "
                f"{schema['minLength']}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and not math.isfinite(value):
            raise LedgerSchemaError(f"{context}: number must be finite")
        if "minimum" in schema and value < schema["minimum"]:
            raise LedgerSchemaError(
                f"{context}: {value!r} is below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            raise LedgerSchemaError(
                f"{context}: {value!r} is above maximum {schema['maximum']}")

    if schema.get("format") == "date" and isinstance(value, str):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise LedgerSchemaError(
                f"{context}: {value!r} is not an ISO date (YYYY-MM-DD)")
        try:
            date.fromisoformat(value)
        except ValueError as e:
            raise LedgerSchemaError(
                f"{context}: {value!r} is not a valid calendar date") from e

    if isinstance(value, dict):
        required = schema.get("required", [])
        missing = [f for f in required if f not in value]
        if missing:
            raise LedgerSchemaError(f"{context}: missing required fields {missing}")
        properties = schema.get("properties", {})
        defs = schema.get("$defs", {})
        if schema.get("additionalProperties") is False:
            extra = sorted(set(value) - set(properties))
            if extra:
                raise LedgerSchemaError(f"{context}: unexpected fields {extra}")
        for key, item in value.items():
            if key in properties:
                sub = properties[key]
                if "$ref" in sub:
                    ref_name = sub["$ref"].rsplit("/", 1)[-1]
                    sub = defs[ref_name]
                validate_schema(item, sub, context=f"{context}.{key}")

    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            validate_schema(item, schema["items"], context=f"{context}[{i}]")
