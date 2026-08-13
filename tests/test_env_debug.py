# SPDX-License-Identifier: Apache-2.0
# Copyright 2024-2026 LogiMentor S.r.l.

"""
Regression tests for _env.debug_enabled() (Week 2 item B5).

Covers:
  - AURIG_BUILD_DEBUG=1 -> True, no warning.
  - Neither var set -> False.
  - Legacy FPYGA_DEBUG=1 -> True, with a DeprecationWarning.
  - B5: the FPYGA_DEBUG DeprecationWarning must stay visible even when
    the caller's warning filters are set to "ignore::DeprecationWarning"
    (i.e. the default state outside __main__ under a normal CLI run,
    where DeprecationWarning is otherwise silently swallowed).
"""

import warnings

import pytest

from aurig_build import _env


def test_aurig_build_debug_true_no_warning(monkeypatch):
    monkeypatch.setenv("AURIG_BUILD_DEBUG", "1")
    monkeypatch.delenv("FPYGA_DEBUG", raising=False)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert _env.debug_enabled() is True


def test_neither_set_returns_false(monkeypatch):
    monkeypatch.delenv("AURIG_BUILD_DEBUG", raising=False)
    monkeypatch.delenv("FPYGA_DEBUG", raising=False)
    assert _env.debug_enabled() is False


def test_fpyga_debug_warns_and_returns_true(monkeypatch):
    monkeypatch.delenv("AURIG_BUILD_DEBUG", raising=False)
    monkeypatch.setenv("FPYGA_DEBUG", "1")
    with pytest.warns(DeprecationWarning, match="FPYGA_DEBUG is deprecated"):
        assert _env.debug_enabled() is True


def test_fpyga_debug_warning_visible_under_default_ignore_filter(monkeypatch):
    """B5: normal CLI invocation runs with 'ignore::DeprecationWarning'
    active for any module other than __main__, which used to swallow
    this warning silently. debug_enabled() must force it visible
    regardless of the caller's active filters."""
    monkeypatch.delenv("AURIG_BUILD_DEBUG", raising=False)
    monkeypatch.setenv("FPYGA_DEBUG", "1")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("ignore", DeprecationWarning)
        result = _env.debug_enabled()

    assert result is True
    assert any(
        issubclass(w.category, DeprecationWarning)
        and "FPYGA_DEBUG is deprecated" in str(w.message)
        for w in caught
    )
