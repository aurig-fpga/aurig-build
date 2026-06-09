# SPDX-License-Identifier: Apache-2.0
# Copyright 2024-2026 LogiMentor S.r.l.

"""Hand-rolled validator for the AURIG manifest v1 contract.

Validates a NORMALIZED (canonical) manifest against the bundled
``manifest-v1.json``. The implementation deliberately covers only the
JSON Schema constructs that contract uses, so the build engine needs no
``jsonschema`` runtime dependency. A test-only conformance suite proves
this validator agrees with ``jsonschema`` on a canonical corpus.

Unknown keys are NOT errors (forward-compat): the contract leaves
``additionalProperties`` unconstrained, so any key absent from a schema's
``properties`` is reported as a WARN and otherwise ignored.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .errors import ManifestError

MANIFEST_SCHEMA_PATH = Path(__file__).resolve().parent / "manifest-v1.json"

_schema_cache: dict | None = None


def load_schema() -> dict:
    """Load and cache the bundled canonical contract."""
    global _schema_cache
    if _schema_cache is None:
        with MANIFEST_SCHEMA_PATH.open(encoding="utf-8") as fh:
            _schema_cache = json.load(fh)
    return _schema_cache


def validate(cfg: dict) -> list[str]:
    """Validate a canonical manifest dict.

    Returns a list of WARN strings (unknown keys). Raises ``ManifestError``
    with one line per violation if the manifest is invalid.
    """
    schema = load_schema()
    defs = schema.get("$defs", {})
    errors: list[str] = []
    warnings: list[str] = []
    _validate(cfg, schema, defs, "<root>", errors, warnings)
    if errors:
        raise ManifestError(errors)
    return warnings


def _resolve(schema: dict, defs: dict) -> dict:
    ref = schema.get("$ref")
    if ref and ref.startswith("#/$defs/"):
        return defs[ref[len("#/$defs/"):]]
    return schema


_TYPE_CHECKS = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "null": lambda v: v is None,
}


def _type_ok(value: Any, type_spec) -> bool:
    types = type_spec if isinstance(type_spec, list) else [type_spec]
    return any(_TYPE_CHECKS.get(t, lambda v: True)(value) for t in types)


def _known_props(schema: dict, defs: dict) -> set[str]:
    """Property names allowed at this object level, unioned across allOf /
    if / then / else branches so conditional subschemas don't trigger
    spurious unknown-key warnings."""
    schema = _resolve(schema, defs)
    props: set[str] = set(schema.get("properties", {}))
    for branch in schema.get("allOf", []):
        props |= _known_props(branch, defs)
    for key in ("if", "then", "else"):
        if key in schema:
            props |= _known_props(schema[key], defs)
    return props


def _matches(value: Any, schema: dict, defs: dict) -> bool:
    """True if value validates against schema with no errors (warnings are
    ignored). Used to evaluate if / not / oneOf branches."""
    errs: list[str] = []
    _validate(value, schema, defs, "<cond>", errs, [], warn_unknown=False)
    return not errs


def _validate(
    value: Any,
    schema: dict,
    defs: dict,
    path: str,
    errors: list[str],
    warnings: list[str],
    warn_unknown: bool = True,
) -> None:
    schema = _resolve(schema, defs)

    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected const {schema['const']!r}, got {value!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} is not one of {schema['enum']}")

    if "type" in schema and not _type_ok(value, schema["type"]):
        errors.append(f"{path}: expected type {schema['type']}, got {type(value).__name__}")
        # Type mismatch makes deeper structural checks meaningless.
        return

    if isinstance(value, str) and "minLength" in schema and len(value) < schema["minLength"]:
        errors.append(f"{path}: string shorter than minLength {schema['minLength']}")

    if isinstance(value, str) and "pattern" in schema and re.search(schema["pattern"], value) is None:
        errors.append(f"{path}: {value!r} does not match pattern {schema['pattern']!r}")

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: fewer than minItems {schema['minItems']} items")
        item_schema = schema.get("items")
        if item_schema is not None:
            for i, item in enumerate(value):
                _validate(item, item_schema, defs, f"{path}[{i}]", errors, warnings, warn_unknown)

    if isinstance(value, dict):
        for req in schema.get("required", []):
            if req not in value:
                errors.append(f"{path}: missing required key '{req}'")
        props = schema.get("properties", {})
        for key, sub in props.items():
            if key in value:
                _validate(value[key], sub, defs, f"{path}.{key}", errors, warnings, warn_unknown)
        addl = schema.get("additionalProperties")
        if isinstance(addl, dict):
            for key, val in value.items():
                if key not in props:
                    _validate(val, addl, defs, f"{path}.{key}", errors, warnings, warn_unknown)
        elif warn_unknown and props:
            known = _known_props(schema, defs)
            for key in value:
                if key not in known:
                    warnings.append(
                        f"[WARN] {path}.{key}: unknown key (ignored; forward-compat)"
                    )

    for branch in schema.get("allOf", []):
        _validate(value, branch, defs, path, errors, warnings, warn_unknown)

    if "if" in schema:
        if _matches(value, schema["if"], defs):
            if "then" in schema:
                _validate(value, schema["then"], defs, path, errors, warnings, warn_unknown)
        elif "else" in schema:
            _validate(value, schema["else"], defs, path, errors, warnings, warn_unknown)

    if "not" in schema and _matches(value, schema["not"], defs):
        errors.append(f"{path}: value must not match the 'not' subschema")

    if "oneOf" in schema:
        n = sum(1 for branch in schema["oneOf"] if _matches(value, branch, defs))
        if n != 1:
            errors.append(f"{path}: must match exactly one of oneOf ({n} matched)")
