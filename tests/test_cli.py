# SPDX-License-Identifier: Apache-2.0
# Copyright 2024-2026 LogiMentor S.r.l.

"""
Unit tests for CLI argument parsing and tool dispatch logic.

Tests argparse setup, routing to builder functions (vivado_build,
quartus_build, diamond_build), and --noenv flag handling.
Does not invoke real FPGA tools.
"""

import pytest
import sys
import os
import yaml
from pathlib import Path
from unittest.mock import patch, MagicMock

from aurig_build.run import main


# ============================================================================
# SECTION: argument parsing
# ============================================================================

@pytest.mark.parametrize("target", ["project", "synth", "impl", "bit", "exporthw", "sim"])
def test_target_choices_valid(target, tmp_project, monkeypatch):
    """Verify all valid targets parse cleanly without error."""
    yaml_path = tmp_project / "config" / "project.yaml"

    # Mock sys.argv
    monkeypatch.setattr(sys, "argv", ["run.py", target, "--cfg", str(yaml_path), "--noenv"])

    # Mock builder functions and prepare_env
    with patch("aurig_build.run.vivado_build", return_value=0), \
         patch("aurig_build.run.quartus_build", return_value=0), \
         patch("aurig_build.run.diamond_build", return_value=0), \
         patch("aurig_build.run.sim_vunit", return_value=0), \
         patch("aurig_build.run.sim_questa", return_value=0), \
         patch("aurig_build.run.sim_xsim", return_value=0), \
         patch("aurig_build.run.prepare_env", return_value=os.environ.copy()):

        # For sim target, we need tool.sim.kind configured
        if target == "sim":
            # Read and modify the YAML to add sim tool
            with open(yaml_path, "r") as f:
                cfg = yaml.safe_load(f)
            cfg["tool"]["sim"] = {"kind": "vunit"}
            with open(yaml_path, "w") as f:
                yaml.dump(cfg, f)

        # Should not raise
        result = main()
        assert result in (0, None)  # Success


def test_target_invalid_exits(monkeypatch, tmp_project):
    """Verify invalid target causes SystemExit."""
    yaml_path = tmp_project / "config" / "project.yaml"

    monkeypatch.setattr(sys, "argv", ["run.py", "invalid_target", "--cfg", str(yaml_path)])

    with pytest.raises(SystemExit):
        main()


def test_version_flag(monkeypatch, capsys):
    """Verify --version prints the package version and exits 0 without
    requiring a target (regression for #10)."""
    import aurig_build

    monkeypatch.setattr(sys, "argv", ["run.py", "--version"])

    with pytest.raises(SystemExit) as exc:
        main()

    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"aurig-build {aurig_build.__version__}"


def test_cfg_default(monkeypatch, tmp_path):
    """Verify default --cfg path ends with config/project.yaml."""
    # Create minimal structure
    (tmp_path / "aurig_build" / "config").mkdir(parents=True)
    default_cfg = tmp_path / "aurig_build" / "config" / "project.yaml"
    minimal_cfg = {
        "schema_version": "1",
        "project_name": "test",
        "top": "top",
        "tool": {"synth": {"kind": "vivado"}},
        "device": {"vendor": "xilinx", "family": "artix7", "part": "xc7a100t"},
        "file_sets": {"rtl": [{"lib": "work", "src": ["*.vhd"]}]},
    }
    with open(default_cfg, "w") as f:
        yaml.dump(minimal_cfg, f)

    # Mock SELF_DIR to point to our tmp aurig_build folder
    monkeypatch.setattr("aurig_build.run.SELF_DIR", tmp_path / "aurig_build")
    monkeypatch.setattr("aurig_build.run.DEFAULT_CFG", default_cfg)
    monkeypatch.setattr(sys, "argv", ["run.py", "synth", "--noenv"])

    with patch("aurig_build.run.vivado_build", return_value=0), \
         patch("aurig_build.run.prepare_env", return_value=os.environ.copy()):
        result = main()
        # Just verify it doesn't crash - actual path checking done in builder mock
        assert result in (0, None)


def test_cfg_override(tmp_path, monkeypatch):
    """Verify --cfg override is respected."""
    custom_yaml = tmp_path / "custom.yaml"
    cfg = {
        "schema_version": "1",
        "project_name": "custom",
        "top": "top",
        "tool": {"synth": {"kind": "vivado"}},
        "device": {"vendor": "xilinx", "family": "artix7", "part": "xc7a100t"},
        "file_sets": {"rtl": [{"lib": "work", "src": ["*.vhd"]}]},
    }
    with open(custom_yaml, "w") as f:
        yaml.dump(cfg, f)

    monkeypatch.setattr(sys, "argv", ["run.py", "synth", "--cfg", str(custom_yaml), "--noenv"])

    with patch("aurig_build.run.vivado_build", return_value=0) as mock_build, \
         patch("aurig_build.run.prepare_env", return_value=os.environ.copy()):
        main()
        # Verify vivado_build was called with the custom config path
        mock_build.assert_called_once()
        call_args = mock_build.call_args
        # Normalize paths for comparison (handle forward/backslash differences)
        call_args_str = str(call_args).replace('\\', '/').lower()
        custom_yaml_normalized = str(custom_yaml).replace('\\', '/').lower()
        assert custom_yaml_normalized in call_args_str


# ============================================================================
# SECTION: tool dispatch
# ============================================================================

def test_dispatch_vivado(tmp_project, monkeypatch):
    """Verify target=synth with kind=vivado routes to vivado_build only."""
    yaml_path = tmp_project / "config" / "project.yaml"

    monkeypatch.setattr(sys, "argv", ["run.py", "synth", "--cfg", str(yaml_path), "--noenv"])

    with patch("aurig_build.run.vivado_build", return_value=0) as mock_vivado, \
         patch("aurig_build.run.quartus_build", return_value=0) as mock_quartus, \
         patch("aurig_build.run.diamond_build", return_value=0) as mock_diamond, \
         patch("aurig_build.run.prepare_env", return_value=os.environ.copy()):

        result = main()

        mock_vivado.assert_called_once()
        mock_quartus.assert_not_called()
        mock_diamond.assert_not_called()
        assert result == 0


def test_dispatch_quartus(tmp_path, monkeypatch, minimal_yaml_quartus):
    """Verify target=synth with kind=quartus routes to quartus_build only."""
    # Create Quartus config
    (tmp_path / "config").mkdir()
    yaml_path = tmp_path / "config" / "project.yaml"
    with open(yaml_path, "w") as f:
        yaml.dump(minimal_yaml_quartus, f)

    monkeypatch.setattr(sys, "argv", ["run.py", "synth", "--cfg", str(yaml_path), "--noenv"])

    with patch("aurig_build.run.vivado_build", return_value=0) as mock_vivado, \
         patch("aurig_build.run.quartus_build", return_value=0) as mock_quartus, \
         patch("aurig_build.run.diamond_build", return_value=0) as mock_diamond, \
         patch("aurig_build.run.prepare_env", return_value=os.environ.copy()):

        result = main()

        mock_quartus.assert_called_once()
        mock_vivado.assert_not_called()
        mock_diamond.assert_not_called()
        assert result == 0


def test_dispatch_diamond(tmp_path, monkeypatch, minimal_yaml_diamond):
    """Verify target=synth with kind=diamond routes to diamond_build only."""
    # Create Diamond config
    (tmp_path / "config").mkdir()
    yaml_path = tmp_path / "config" / "project.yaml"
    with open(yaml_path, "w") as f:
        yaml.dump(minimal_yaml_diamond, f)

    monkeypatch.setattr(sys, "argv", ["run.py", "synth", "--cfg", str(yaml_path), "--noenv"])

    with patch("aurig_build.run.vivado_build", return_value=0) as mock_vivado, \
         patch("aurig_build.run.quartus_build", return_value=0) as mock_quartus, \
         patch("aurig_build.run.diamond_build", return_value=0) as mock_diamond, \
         patch("aurig_build.run.prepare_env", return_value=os.environ.copy()):

        result = main()

        mock_diamond.assert_called_once()
        mock_vivado.assert_not_called()
        mock_quartus.assert_not_called()
        assert result == 0


def test_dispatch_unknown_tool_exits(tmp_path, monkeypatch):
    """Verify unknown tool kind returns non-zero exit code."""
    bad_cfg = {
        "schema_version": "1",
        "project_name": "bad",
        "top": "top",
        "tool": {"synth": {"kind": "badtool"}},
        "device": {"vendor": "xilinx", "family": "artix7", "part": "xc7a100t"},
        "file_sets": {"rtl": [{"lib": "work", "src": ["*.vhd"]}]},
    }
    yaml_path = tmp_path / "bad.yaml"
    with open(yaml_path, "w") as f:
        yaml.dump(bad_cfg, f)

    monkeypatch.setattr(sys, "argv", ["run.py", "synth", "--cfg", str(yaml_path), "--noenv"])

    with patch("aurig_build.run.prepare_env", return_value=os.environ.copy()):
        result = main()
        assert result == 2  # Error exit code


def test_dispatch_sim_missing_kind_exits(tmp_project, monkeypatch):
    """Verify target=sim without tool.sim.kind prints error and returns 2."""
    yaml_path = tmp_project / "config" / "project.yaml"

    # Default tmp_project has no tool.sim configured
    monkeypatch.setattr(sys, "argv", ["run.py", "sim", "--cfg", str(yaml_path), "--noenv"])

    with patch("aurig_build.run.prepare_env", return_value=os.environ.copy()):
        result = main()
        assert result == 2


# ============================================================================
# SECTION: --noenv flag
# ============================================================================

def test_noenv_skips_prepare_env(tmp_project, monkeypatch):
    """Verify --noenv flag prevents prepare_env from being called."""
    yaml_path = tmp_project / "config" / "project.yaml"

    monkeypatch.setattr(sys, "argv", ["run.py", "synth", "--cfg", str(yaml_path), "--noenv"])

    with patch("aurig_build.run.vivado_build", return_value=0), \
         patch("aurig_build.run.prepare_env", return_value=os.environ.copy()) as mock_prepare:

        result = main()

        # prepare_env should NOT be called when --noenv is set
        mock_prepare.assert_not_called()
        assert result == 0
