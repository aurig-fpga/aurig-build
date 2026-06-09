# SPDX-License-Identifier: Apache-2.0
# Copyright 2024-2026 LogiMentor S.r.l.

"""AURIG manifest schema v1: alias normalization and validation.

The load pipeline is: read base -> .local overlay merge -> normalize
(alias rewrite) -> validate (against the bundled manifest-v1.json) ->
consume. The JSON contract is canonical-only; normalization rewrites
legacy forms to canonical before validation ever runs.
"""

from .normalize import normalize
from .validate import MANIFEST_SCHEMA_PATH, load_schema, validate

__all__ = ["normalize", "validate", "load_schema", "MANIFEST_SCHEMA_PATH"]
