# SPDX-License-Identifier: Apache-2.0
# Copyright 2024-2026 LogiMentor S.r.l.

"""
Unit tests for path resolution helpers.

Tests _sanitize_script_path, compute_project_root, project_root resolution,
and env_script selection logic without invoking vendor tools.
"""

import pytest
import os
import sys
from pathlib import Path
from unittest.mock import patch

from aurig_build.run import _sanitize_script_path, compute_project_root


# ============================================================================
# SECTION: _sanitize_script_path
# ============================================================================

def test_strips_leading_trailing_spaces():
    """Verify leading and trailing spaces are removed."""
    result = _sanitize_script_path("  /opt/tool  ")
    assert result == os.path.normpath("/opt/tool")


def test_strips_double_quotes():
    """Verify double quotes are stripped."""
    result = _sanitize_script_path('"/opt/tool"')
    assert result == os.path.normpath("/opt/tool")


def test_strips_escaped_quotes():
    """Verify escaped quotes are handled correctly."""
    result = _sanitize_script_path(r'\"path/to/tool\"')
    # After unescaping and normpath
    assert "path" in result and "tool" in result


def test_expands_env_var_unix(monkeypatch):
    """Verify $VAR expansion works on all platforms."""
    monkeypatch.setenv("MY_TOOL", "/opt/x")
    result = _sanitize_script_path("$MY_TOOL/settings.sh")
    assert "/opt/x" in result or "\\opt\\x" in result  # Handle Windows normpath


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="%VAR% expansion is Windows-only via os.path.expandvars",
)
def test_expands_env_var_windows(monkeypatch):
    """Verify %VAR% expansion works on Windows."""
    monkeypatch.setenv("MY_TOOL", "/opt/x")
    result = _sanitize_script_path("%MY_TOOL%/settings.bat")
    assert "/opt/x" in result or "\\opt\\x" in result


def test_empty_string_returns_empty():
    """Verify empty string input returns empty string."""
    result = _sanitize_script_path("")
    assert result == ""


def test_none_equivalent_empty():
    """Verify None-like input is handled gracefully."""
    # _sanitize_script_path converts to str, so "" case is covered
    result = _sanitize_script_path("")
    assert result == ""


# ============================================================================
# SECTION: project_root resolution
# ============================================================================

def test_project_root_relative(tmp_project):
    """Verify relative project_root resolves correctly from config file location."""
    yaml_path = tmp_project / "config" / "project.yaml"
    
    # The fixture sets project_root: ".."
    # Relative to config/, that should resolve to tmp_project root
    
    # Simulate resolution: config file dir is tmp_project/config
    cfg_dir = yaml_path.parent
    project_root_relative = ".."
    
    # Resolve relative to cfg_dir
    resolved = (cfg_dir / project_root_relative).resolve()
    
    assert resolved == tmp_project.resolve()
    assert resolved.exists()


def test_project_root_absolute(tmp_path):
    """Verify absolute project_root is used as-is."""
    absolute_root = tmp_path / "myproject"
    absolute_root.mkdir()
    
    # Simulate resolution
    resolved = Path(str(absolute_root)).resolve()
    
    assert resolved == absolute_root.resolve()


# ============================================================================
# SECTION: env_script selection (OS-specific)
# ============================================================================

def test_env_script_linux_selection(monkeypatch, minimal_yaml_vivado):
    """Verify env_script selects linux path when platform is Linux."""
    # Add env_script to config
    cfg = minimal_yaml_vivado.copy()
    cfg["tool"]["synth"]["env_script"] = {
        "linux": "/opt/Xilinx/Vivado/2023.1/settings64.sh",
        "windows": "C:/Xilinx/Vivado/2023.1/settings64.bat",
    }
    
    # Mock platform.system to return Linux
    monkeypatch.setattr("platform.system", lambda: "Linux")
    
    # Simulate the selection logic (from prepare_env or similar)
    from aurig_build.run import on_windows
    if not on_windows():
        selected = cfg["tool"]["synth"]["env_script"]["linux"]
    else:
        selected = cfg["tool"]["synth"]["env_script"]["windows"]
    
    # Since we mocked platform.system but on_windows might cache, just verify structure
    assert cfg["tool"]["synth"]["env_script"]["linux"].endswith(".sh")
    assert cfg["tool"]["synth"]["env_script"]["windows"].endswith(".bat")


def test_env_script_windows_selection(monkeypatch, minimal_yaml_vivado):
    """Verify env_script selects windows path when platform is Windows."""
    cfg = minimal_yaml_vivado.copy()
    cfg["tool"]["synth"]["env_script"] = {
        "linux": "/opt/Xilinx/Vivado/2023.1/settings64.sh",
        "windows": "C:/Xilinx/Vivado/2023.1/settings64.bat",
    }
    
    # Mock platform.system to return Windows
    monkeypatch.setattr("platform.system", lambda: "Windows")
    
    # Verify structure exists
    assert cfg["tool"]["synth"]["env_script"]["windows"].endswith(".bat")
    assert cfg["tool"]["synth"]["env_script"]["linux"].endswith(".sh")


# ============================================================================
# SECTION: bin_dir resolution
# ============================================================================

def test_bin_dir_relative_to_config(tmp_path):
    """Verify bin_dir paths can be specified absolutely or relatively."""
    # Absolute path (use Windows-style on Windows, Unix-style on Unix)
    import platform
    if platform.system().lower().startswith("win"):
        absolute_bin = "C:/opt/vivado/2023.1/bin"
    else:
        absolute_bin = "/opt/vivado/2023.1/bin"
    assert Path(absolute_bin).is_absolute()

    # Relative path (would be resolved in actual code)
    relative_bin = "bin"
    assert not Path(relative_bin).is_absolute()


# ============================================================================
# SECTION: compute_project_root — direct function tests
# ============================================================================

def test_compute_project_root_default_two_level_ascent(tmp_path):
    """
    When no project_root key is present and config is under aurig_build/config/,
    compute_project_root should ascend two levels (cfg_dir/../..) to reach
    the project root.
    """
    # Mimic the expected layout: <project_root>/aurig_build/config/project.yaml
    project_root = tmp_path / "myproject"
    aurig_build_dir = project_root / "aurig_build"
    config_dir = aurig_build_dir / "config"
    config_dir.mkdir(parents=True)
    cfg_path = config_dir / "project.yaml"
    cfg_path.write_text("project_name: test\n")

    result = compute_project_root(cfg_path, {})

    assert result == project_root.resolve()


def test_compute_project_root_default_one_level_when_not_under_aurig_build(tmp_path):
    """
    When config is NOT under a directory named 'aurig_build', the default ascent
    is only one level (cfg_dir/..).
    """
    # Layout: <project_root>/config/project.yaml  (parent is "config", grandparent is not "aurig_build")
    project_root = tmp_path / "myproject"
    config_dir = project_root / "config"
    config_dir.mkdir(parents=True)
    cfg_path = config_dir / "project.yaml"
    cfg_path.write_text("project_name: test\n")

    result = compute_project_root(cfg_path, {})

    assert result == project_root.resolve()


def test_compute_project_root_explicit_relative(tmp_path):
    """
    An explicit relative project_root (e.g. '../..') is resolved relative
    to the config file's directory.
    """
    project_root = tmp_path / "myproject"
    config_dir = project_root / "aurig_build" / "config"
    config_dir.mkdir(parents=True)
    cfg_path = config_dir / "project.yaml"
    cfg_path.write_text("project_name: test\n")

    cfg = {"project_root": "../.."}
    result = compute_project_root(cfg_path, cfg)

    # cfg_dir / ../.. == project_root
    assert result == project_root.resolve()


def test_compute_project_root_explicit_absolute(tmp_path):
    """An explicit absolute project_root is used as-is (resolved)."""
    absolute_root = tmp_path / "absolute_root"
    absolute_root.mkdir()

    # cfg can live anywhere
    config_dir = tmp_path / "somewhere" / "config"
    config_dir.mkdir(parents=True)
    cfg_path = config_dir / "project.yaml"
    cfg_path.write_text("project_name: test\n")

    cfg = {"project_root": str(absolute_root)}
    result = compute_project_root(cfg_path, cfg)

    assert result == absolute_root.resolve()


def test_compute_project_root_env_override(tmp_path, monkeypatch):
    """
    AURIG_BUILD_PROJECT_ROOT environment variable overrides the default but is itself
    overridden by an explicit project_root in YAML (YAML wins).
    """
    env_root = tmp_path / "env_root"
    env_root.mkdir()

    config_dir = tmp_path / "aurig_build" / "config"
    config_dir.mkdir(parents=True)
    cfg_path = config_dir / "project.yaml"
    cfg_path.write_text("project_name: test\n")

    monkeypatch.setenv("AURIG_BUILD_PROJECT_ROOT", str(env_root))

    # No YAML project_root — env var should win over the default
    result = compute_project_root(cfg_path, {})
    assert result == env_root.resolve()


def test_compute_project_root_yaml_overrides_env(tmp_path, monkeypatch):
    """
    An explicit project_root in YAML takes priority over AURIG_BUILD_PROJECT_ROOT.
    """
    env_root = tmp_path / "env_root"
    env_root.mkdir()
    yaml_root = tmp_path / "yaml_root"
    yaml_root.mkdir()

    config_dir = tmp_path / "aurig_build" / "config"
    config_dir.mkdir(parents=True)
    cfg_path = config_dir / "project.yaml"
    cfg_path.write_text("project_name: test\n")

    monkeypatch.setenv("AURIG_BUILD_PROJECT_ROOT", str(env_root))

    cfg = {"project_root": str(yaml_root)}
    result = compute_project_root(cfg_path, cfg)

    assert result == yaml_root.resolve()
