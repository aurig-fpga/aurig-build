# SPDX-License-Identifier: Apache-2.0
# Copyright 2024-2026 LogiMentor S.r.l.

"""Shared exception for manifest normalization and validation failures."""

from __future__ import annotations


class ManifestError(Exception):
    """A manifest could not be normalized or validated to canonical v1.

    Carries one or more human-readable lines. The CLI front-door catches
    this, prints each line as an ``[ERROR]`` to stderr, and exits 2.
    """

    def __init__(self, messages):
        if isinstance(messages, str):
            messages = [messages]
        self.messages = list(messages)
        super().__init__("\n".join(self.messages))
