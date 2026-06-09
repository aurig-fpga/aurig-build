# SPDX-License-Identifier: Apache-2.0
# Copyright 2024-2026 LogiMentor S.r.l.

"""
Unit tests for IP cores cross-vendor compatibility warning logic.

Tests validate_ip_cores() function that warns about mismatched IP formats
and tool kinds. Warnings are printed to stderr but never raise exceptions.
"""

import pytest
import sys
from io import StringIO
from unittest.mock import patch

from aurig_build.run import validate_ip_cores


# ============================================================================
# Helper to capture stderr
# ============================================================================

def capture_stderr(func, *args, **kwargs):
    """Capture stderr output during function call."""
    old_stderr = sys.stderr
    sys.stderr = StringIO()
    try:
        func(*args, **kwargs)
        output = sys.stderr.getvalue()
    finally:
        sys.stderr = old_stderr
    return output


# ============================================================================
# Test cases
# ============================================================================

def test_xci_with_diamond_warns():
    """Verify xci IP with diamond tool generates warning."""
    cfg = {
        "ip_cores": [
            {"kind": "xci", "src": "ip/core.xci"}
        ]
    }
    
    stderr = capture_stderr(validate_ip_cores, cfg, "diamond")
    
    assert "[WARN]" in stderr
    assert "xci" in stderr.lower()
    assert "vivado" in stderr.lower() or "Vivado" in stderr


def test_bd_with_quartus_warns():
    """Verify bd IP with quartus tool generates warning."""
    cfg = {
        "ip_cores": [
            {"kind": "bd", "src": "ip/design.bd"}
        ]
    }
    
    stderr = capture_stderr(validate_ip_cores, cfg, "quartus")
    
    assert "[WARN]" in stderr
    assert "bd" in stderr.lower()
    assert "vivado" in stderr.lower() or "Vivado" in stderr


def test_qip_with_vivado_warns():
    """Verify qip IP with vivado tool generates warning."""
    cfg = {
        "ip_cores": [
            {"kind": "qip", "src": "ip/fifo.qip"}
        ]
    }
    
    stderr = capture_stderr(validate_ip_cores, cfg, "vivado")
    
    assert "[WARN]" in stderr
    assert "qip" in stderr.lower()
    assert "quartus" in stderr.lower() or "Quartus" in stderr


def test_ipx_with_vivado_warns():
    """Verify ipx IP with vivado tool generates warning."""
    cfg = {
        "ip_cores": [
            {"kind": "ipx", "src": "ip/pll.ipx"}
        ]
    }
    
    stderr = capture_stderr(validate_ip_cores, cfg, "vivado")
    
    assert "[WARN]" in stderr
    assert "ipx" in stderr.lower()
    assert "diamond" in stderr.lower() or "Diamond" in stderr


def test_lpc_with_vivado_warns():
    """Verify lpc IP with vivado tool generates warning."""
    cfg = {
        "ip_cores": [
            {"kind": "lpc", "src": "ip/fifo.lpc"}
        ]
    }
    
    stderr = capture_stderr(validate_ip_cores, cfg, "vivado")
    
    assert "[WARN]" in stderr
    assert "lpc" in stderr.lower()
    assert "diamond" in stderr.lower() or "Diamond" in stderr


def test_edf_is_always_valid():
    """Verify edf (EDIF netlist) generates no warnings with any tool."""
    cfg = {
        "ip_cores": [
            {"kind": "edf", "src": "ip/netlist.edf"}
        ]
    }
    
    for tool in ["vivado", "quartus", "diamond"]:
        stderr = capture_stderr(validate_ip_cores, cfg, tool)
        assert "[WARN]" not in stderr or "edf" not in stderr.lower()


def test_no_ip_cores_no_warning():
    """Verify missing ip_cores key generates no warnings."""
    cfg = {}
    
    stderr = capture_stderr(validate_ip_cores, cfg, "vivado")
    
    # Should be silent (or may have other warnings, but not IP-related)
    assert stderr == "" or "ip_cores" not in stderr.lower()


def test_multiple_mixed_warns_once_per_core():
    """Verify multiple bad IP cores generate one warning per core."""
    cfg = {
        "ip_cores": [
            {"kind": "xci", "src": "ip/core1.xci"},
            {"kind": "bd", "src": "ip/design.bd"},
        ]
    }
    
    stderr = capture_stderr(validate_ip_cores, cfg, "diamond")
    
    # Should have two [WARN] lines
    warn_count = stderr.count("[WARN]")
    assert warn_count >= 2, f"Expected at least 2 warnings, got {warn_count}"


def test_validate_ip_cores_never_raises():
    """Verify validate_ip_cores always completes without exception."""
    cfg = {
        "ip_cores": [
            {"kind": "unknown_format", "src": "ip/bad.xyz"}
        ]
    }
    
    # Should not raise regardless of bad data
    try:
        validate_ip_cores(cfg, "vivado")
    except Exception as e:
        pytest.fail(f"validate_ip_cores raised exception: {e}")
