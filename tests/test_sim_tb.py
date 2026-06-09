# SPDX-License-Identifier: Apache-2.0
# Copyright 2024-2026 LogiMentor S.r.l.

"""
Regression tests for the YAML testbench key resolution in sim dispatch.

Covers:
  - sim.default_top_tb  (canonical key)
  - sim.top_tb          (legacy fallback key)
  - explicit --tb       overrides YAML
  - missing testbench   produces the expected error for Questa
"""

import sys
import io
import pytest
from unittest.mock import patch

from aurig_build.run import _yaml_default_tb, sim_questa, sim_xsim


# ---------------------------------------------------------------------------
# _yaml_default_tb unit tests
# ---------------------------------------------------------------------------

def test_yaml_default_tb_canonical_key():
    """sim.default_top_tb is returned as the canonical key."""
    cfg = {"sim": {"default_top_tb": "tb_top_canonical"}}
    assert _yaml_default_tb(cfg) == "tb_top_canonical"


def test_yaml_default_tb_legacy_key():
    """sim.top_tb is accepted when default_top_tb is absent (legacy fallback)."""
    cfg = {"sim": {"top_tb": "tb_top_legacy"}}
    assert _yaml_default_tb(cfg) == "tb_top_legacy"


def test_yaml_default_tb_canonical_wins_over_legacy():
    """When both keys present, default_top_tb takes precedence."""
    cfg = {"sim": {"default_top_tb": "tb_canonical", "top_tb": "tb_legacy"}}
    assert _yaml_default_tb(cfg) == "tb_canonical"


def test_yaml_default_tb_missing_sim_key():
    """Returns empty string when sim section is absent."""
    assert _yaml_default_tb({}) == ""


def test_yaml_default_tb_empty_sim_section():
    """Returns empty string when sim section has neither tb key."""
    assert _yaml_default_tb({"sim": {"generics": {"G_SEED": 1}}}) == ""


def test_yaml_default_tb_none_sim_value():
    """Returns empty string when sim is explicitly None."""
    assert _yaml_default_tb({"sim": None}) == ""


# ---------------------------------------------------------------------------
# sim_questa integration: --tb override and error behaviour
# ---------------------------------------------------------------------------

def _questa_cfg_with_tb(tb_value):
    """Build a minimal cfg dict with the canonical default_top_tb key."""
    return {"sim": {"default_top_tb": tb_value}}


def test_sim_questa_explicit_tb_overrides_yaml(tmp_path):
    """Explicit --tb argument takes precedence over sim.default_top_tb in YAML."""
    cfg = _questa_cfg_with_tb("tb_from_yaml")
    cfg_path = tmp_path / "project.yaml"
    cfg_path.write_text("")

    with patch("aurig_build.run.run", return_value=0) as mock_run, \
         patch("aurig_build.run._resolve_vendor_exe", return_value=("/fake/sim_exe", "vsim")):
        result = sim_questa(cfg_path, tb="tb_explicit", env={}, cfg=cfg)

    assert result == 0
    call_args = mock_run.call_args[0][0]
    # The final element of the vsim -do command embeds the tb name
    assert "tb_explicit" in call_args[-1]
    assert "tb_from_yaml" not in call_args[-1]


def test_sim_questa_uses_default_top_tb_from_yaml(tmp_path):
    """sim.default_top_tb is passed to vsim when no --tb flag is given."""
    cfg = _questa_cfg_with_tb("tb_from_yaml")
    cfg_path = tmp_path / "project.yaml"
    cfg_path.write_text("")

    with patch("aurig_build.run.run", return_value=0) as mock_run, \
         patch("aurig_build.run._resolve_vendor_exe", return_value=("/fake/sim_exe", "vsim")):
        result = sim_questa(cfg_path, tb="", env={}, cfg=cfg)

    assert result == 0
    call_args = mock_run.call_args[0][0]
    assert "tb_from_yaml" in call_args[-1]


def test_sim_questa_uses_top_tb_legacy_fallback(tmp_path):
    """sim.top_tb (legacy key) is still accepted when default_top_tb is absent."""
    cfg = {"sim": {"top_tb": "tb_legacy_questa"}}
    cfg_path = tmp_path / "project.yaml"
    cfg_path.write_text("")

    with patch("aurig_build.run.run", return_value=0) as mock_run, \
         patch("aurig_build.run._resolve_vendor_exe", return_value=("/fake/sim_exe", "vsim")):
        result = sim_questa(cfg_path, tb="", env={}, cfg=cfg)

    assert result == 0
    call_args = mock_run.call_args[0][0]
    assert "tb_legacy_questa" in call_args[-1]


def test_sim_questa_missing_tb_returns_error_code(tmp_path, capsys):
    """sim_questa returns exit code 2 and prints an error when no tb is provided."""
    cfg = {}  # no sim section at all
    cfg_path = tmp_path / "project.yaml"
    cfg_path.write_text("")

    result = sim_questa(cfg_path, tb="", env={}, cfg=cfg)

    assert result == 2
    captured = capsys.readouterr()
    assert "sim.default_top_tb" in captured.err


def test_sim_vunit_default_driver_path(tmp_path):
    """Backward-compat half:
    without `tool.sim.driver`, sim_vunit still invokes the historical
    "sim/run_vunit.py" relative to CWD."""
    from aurig_build.run import sim_vunit
    cfg = {}      # no tool.sim.driver
    cfg_path = tmp_path / "project.yaml"
    cfg_path.write_text("")

    with patch("aurig_build.run.run", return_value=0) as mock_run:
        result = sim_vunit(cfg_path, tb="", extra=[], env={}, cfg=cfg)

    assert result == 0
    cmd = mock_run.call_args[0][0]
    # cmd is [sys.executable, driver, "--cfg", ...]
    assert cmd[1] == "sim/run_vunit.py", f"expected default driver path, got cmd[1]={cmd[1]!r}"


def test_sim_vunit_honors_tool_sim_driver(tmp_path):
    """`tool.sim.driver` overrides the
    hardcoded "sim/run_vunit.py" so projects with a different layout
    (e.g. license boundary) can configure their own path. After PR #16
    the driver path goes through _sanitize_exe_value (os.path.normpath),
    so the assertion uses the platform-native form."""
    import os
    from aurig_build.run import sim_vunit
    driver_path = os.path.normpath("tools/sim/vunit_driver.py")
    cfg = {"tool": {"sim": {"driver": driver_path}}}
    cfg_path = tmp_path / "project.yaml"
    cfg_path.write_text("")

    with patch("aurig_build.run.run", return_value=0) as mock_run:
        result = sim_vunit(cfg_path, tb="", extra=[], env={}, cfg=cfg)

    assert result == 0
    cmd = mock_run.call_args[0][0]
    assert cmd[1] == driver_path, \
        f"expected configured driver path, got cmd[1]={cmd[1]!r}"


def test_sim_vunit_empty_driver_falls_back_to_default(tmp_path):
    """Whitespace-only tool.sim.driver must collapse to the default
    "sim/run_vunit.py" instead of producing [python, "", ...]."""
    from aurig_build.run import sim_vunit
    cfg = {"tool": {"sim": {"driver": "   "}}}
    cfg_path = tmp_path / "project.yaml"
    cfg_path.write_text("")

    with patch("aurig_build.run.run", return_value=0) as mock_run:
        result = sim_vunit(cfg_path, tb="", extra=[], env={}, cfg=cfg)

    assert result == 0
    cmd = mock_run.call_args[0][0]
    assert cmd[1] == "sim/run_vunit.py", \
        f"empty driver should default; got cmd[1]={cmd[1]!r}"


def test_sim_vunit_honors_tool_sim_exe_as_interpreter(tmp_path):
    """Closes README/code mismatch: `tool.sim.exe` is now actually used
    by sim_vunit as the Python interpreter (previously sys.executable
    was hardcoded). Default behavior is preserved when exe is unset.
    """
    from aurig_build.run import sim_vunit
    cfg = {"tool": {"sim": {"exe": "python3"}}}
    cfg_path = tmp_path / "project.yaml"
    cfg_path.write_text("")

    with patch("aurig_build.run.run", return_value=0) as mock_run:
        result = sim_vunit(cfg_path, tb="", extra=[], env={}, cfg=cfg)

    assert result == 0
    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "python3", \
        f"expected configured interpreter, got cmd[0]={cmd[0]!r}"


@pytest.mark.parametrize("quote_style", ['"', "'"])
def test_sim_vunit_sanitizes_quoted_interpreter_and_driver(quote_style, tmp_path):
    """
    PR #16 round 10: sim_vunit's interpreter (tool.sim.exe) and driver
    (tool.sim.driver) go through _sanitize_exe_value, same pipeline the
    synth dispatchers' exe values get. A user-quoted absolute path must
    resolve to its unquoted+normpath form, not be passed verbatim to
    subprocess (which would fail).
    """
    import os
    from aurig_build.run import sim_vunit
    raw_interp = os.path.normpath("/opt/python3.11/bin/python")
    raw_driver = os.path.normpath("/proj/tools/sim/vunit_driver.py")
    cfg = {
        "tool": {
            "sim": {
                "exe":    f"{quote_style}{raw_interp}{quote_style}",
                "driver": f"{quote_style}{raw_driver}{quote_style}",
            }
        }
    }
    cfg_path = tmp_path / "project.yaml"
    cfg_path.write_text("")

    with patch("aurig_build.run.run", return_value=0) as mock_run:
        result = sim_vunit(cfg_path, tb="", extra=[], env={}, cfg=cfg)

    assert result == 0
    cmd = mock_run.call_args[0][0]
    # cmd = [interpreter, driver, "--cfg", ...]. Both must be the sanitized
    # (unquoted, normpath'd) forms — never the raw quoted strings.
    assert cmd[0] == raw_interp, \
        f"interpreter not sanitized: expected {raw_interp!r}, got cmd[0]={cmd[0]!r}"
    assert cmd[1] == raw_driver, \
        f"driver not sanitized: expected {raw_driver!r}, got cmd[1]={cmd[1]!r}"


@pytest.mark.parametrize("nonsense", ['""', "''", '"   "'])
def test_sim_vunit_quotes_only_interpreter_falls_back_to_default(nonsense, tmp_path):
    """A quotes-only / whitespace-only tool.sim.exe must collapse to ""
    and fall back to sys.executable. Driver behaves the same against its
    sim/run_vunit.py default."""
    import sys as _sys
    from aurig_build.run import sim_vunit
    cfg = {"tool": {"sim": {"exe": nonsense, "driver": nonsense}}}
    cfg_path = tmp_path / "project.yaml"
    cfg_path.write_text("")

    with patch("aurig_build.run.run", return_value=0) as mock_run:
        result = sim_vunit(cfg_path, tb="", extra=[], env={}, cfg=cfg)

    assert result == 0
    cmd = mock_run.call_args[0][0]
    assert cmd[0] == _sys.executable, \
        f"interpreter did not fall back to sys.executable: cmd[0]={cmd[0]!r}"
    assert cmd[1] == "sim/run_vunit.py", \
        f"driver did not fall back to the default: cmd[1]={cmd[1]!r}"


def test_sim_vunit_default_interpreter_is_sys_executable(tmp_path):
    """Without tool.sim.exe, sim_vunit must still use sys.executable
    (preserving the historical behavior)."""
    import sys
    from aurig_build.run import sim_vunit
    cfg = {}
    cfg_path = tmp_path / "project.yaml"
    cfg_path.write_text("")

    with patch("aurig_build.run.run", return_value=0) as mock_run:
        result = sim_vunit(cfg_path, tb="", extra=[], env={}, cfg=cfg)

    assert result == 0
    cmd = mock_run.call_args[0][0]
    assert cmd[0] == sys.executable, \
        f"expected sys.executable, got cmd[0]={cmd[0]!r}"


def test_sim_questa_missing_tb_error_message_mentions_canonical_key(tmp_path, capsys):
    """Error message references the canonical sim.default_top_tb key, not the legacy one."""
    result = sim_questa(tmp_path / "p.yaml", tb="", env={}, cfg={})
    captured = capsys.readouterr()
    assert "default_top_tb" in captured.err
    assert result == 2


# ---------------------------------------------------------------------------
# sim_xsim integration: tb key resolution (no error-on-missing, just passthrough)
# ---------------------------------------------------------------------------

def test_sim_xsim_uses_default_top_tb_from_yaml(tmp_path):
    """sim.default_top_tb is forwarded to vivado tclargs when no --tb flag given."""
    cfg = {"sim": {"default_top_tb": "tb_xsim_yaml"}}
    cfg_path = tmp_path / "project.yaml"
    cfg_path.write_text("")

    with patch("aurig_build.run.run", return_value=0) as mock_run, \
         patch("aurig_build.run._resolve_vendor_exe", return_value=("/fake/sim_exe", "vsim")):
        result = sim_xsim(cfg_path, tb="", env={}, cfg=cfg)

    assert result == 0
    call_args = mock_run.call_args[0][0]
    assert "tb_xsim_yaml" in call_args


def test_sim_xsim_uses_top_tb_legacy_fallback(tmp_path):
    """sim.top_tb (legacy key) still works for XSim when default_top_tb absent."""
    cfg = {"sim": {"top_tb": "tb_xsim_legacy"}}
    cfg_path = tmp_path / "project.yaml"
    cfg_path.write_text("")

    with patch("aurig_build.run.run", return_value=0) as mock_run, \
         patch("aurig_build.run._resolve_vendor_exe", return_value=("/fake/sim_exe", "vsim")):
        result = sim_xsim(cfg_path, tb="", env={}, cfg=cfg)

    assert result == 0
    call_args = mock_run.call_args[0][0]
    assert "tb_xsim_legacy" in call_args


def test_sim_xsim_explicit_tb_overrides_yaml(tmp_path):
    """Explicit --tb takes precedence over sim.default_top_tb for XSim."""
    cfg = {"sim": {"default_top_tb": "tb_from_yaml"}}
    cfg_path = tmp_path / "project.yaml"
    cfg_path.write_text("")

    with patch("aurig_build.run.run", return_value=0) as mock_run, \
         patch("aurig_build.run._resolve_vendor_exe", return_value=("/fake/sim_exe", "vsim")):
        result = sim_xsim(cfg_path, tb="tb_explicit_xsim", env={}, cfg=cfg)

    assert result == 0
    call_args = mock_run.call_args[0][0]
    assert "tb_explicit_xsim" in call_args
    assert "tb_from_yaml" not in call_args
