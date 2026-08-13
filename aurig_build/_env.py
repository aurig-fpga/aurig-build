# SPDX-License-Identifier: Apache-2.0
# Copyright 2024-2026 LogiMentor S.r.l.
"""Internal env-var helpers.

Provides a backward-compatibility shim for the fpyga -> aurig_build
rename: reads AURIG_BUILD_DEBUG, falls back to the legacy FPYGA_DEBUG
with a DeprecationWarning.
"""

from __future__ import annotations

import os
import warnings


def debug_enabled() -> bool:
    """Return True if AURIG_BUILD_DEBUG=1 (or legacy FPYGA_DEBUG=1)."""
    if os.environ.get("AURIG_BUILD_DEBUG") == "1":
        return True
    if os.environ.get("FPYGA_DEBUG") == "1":
        with warnings.catch_warnings():
            # DeprecationWarning is ignored by default outside of __main__,
            # so a normal CLI run would otherwise never show this. Force it
            # to display here without permanently altering global filters.
            warnings.simplefilter("always", DeprecationWarning)
            warnings.warn(
                "FPYGA_DEBUG is deprecated; use AURIG_BUILD_DEBUG=1 instead.",
                DeprecationWarning,
                stacklevel=2,
            )
        return True
    return False
