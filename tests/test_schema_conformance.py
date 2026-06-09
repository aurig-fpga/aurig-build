# SPDX-License-Identifier: Apache-2.0
# Copyright 2024-2026 LogiMentor S.r.l.

"""Conformance: the hand-rolled validator must agree with ``jsonschema``.

The corpus under ``tests/data/manifests/`` is CANONICAL-ONLY (no legacy /
alias forms): both validators are canonical-only, so alias inputs would
sail through ``jsonschema`` as harmless unknown keys and make the
comparison meaningless. Alias-normalization correctness is covered
separately in ``test_schema_normalize.py``.

For every document, ``validate()`` (in-tree, zero extra runtime deps) and
``jsonschema`` validating against the same bundled ``manifest-v1.json``
must return the same pass/fail verdict. The directory the file lives in
(``valid`` / ``invalid``) pins the expected verdict.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from aurig_build.schema import validate, load_schema
from aurig_build.schema.errors import ManifestError

jsonschema = pytest.importorskip(
    "jsonschema", reason="jsonschema is the conformance oracle (requirements-test.txt)"
)

CORPUS = Path(__file__).resolve().parent / "data" / "manifests"


def _cases(kind: str):
    return sorted((CORPUS / kind).glob("*.yaml"))


def _hand_rolled_ok(doc) -> bool:
    try:
        validate(doc)
        return True
    except ManifestError:
        return False


def _jsonschema_ok(doc) -> bool:
    validator = jsonschema.Draft202012Validator(load_schema())
    return not list(validator.iter_errors(doc))


@pytest.mark.parametrize(
    "path",
    _cases("valid") + _cases("invalid"),
    ids=lambda p: f"{p.parent.name}/{p.name}",
)
def test_hand_rolled_agrees_with_jsonschema(path: Path):
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    expected_valid = path.parent.name == "valid"

    hand = _hand_rolled_ok(doc)
    oracle = _jsonschema_ok(doc)

    assert hand == oracle, (
        f"{path.name}: hand-rolled={'valid' if hand else 'invalid'} but "
        f"jsonschema={'valid' if oracle else 'invalid'}"
    )
    assert hand == expected_valid, (
        f"{path.name}: expected {'valid' if expected_valid else 'invalid'}, "
        f"got {'valid' if hand else 'invalid'}"
    )
