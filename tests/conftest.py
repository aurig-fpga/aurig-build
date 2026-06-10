# SPDX-License-Identifier: Apache-2.0
# Copyright 2024-2026 LogiMentor S.r.l.

"""
Shared pytest fixtures for aurig-build test suite.

Provides minimal YAML configurations for each vendor tool, real directory
structures under tmp_path, and a subprocess guard to prevent accidental
invocation of real FPGA tools in CI.
"""

import pytest
import yaml
from pathlib import Path
from unittest.mock import patch


@pytest.fixture
def minimal_yaml_vivado():
    """Return minimal valid Vivado project configuration as dict."""
    return {
        "schema_version": "1",
        "project_name": "test_proj",
        "project_root": "..",
        "top": "test_top",
        "tool": {
            "synth": {
                "kind": "vivado",
                "version": "2023.1",
                "exe": "vivado",
            }
        },
        "device": {
            "vendor": "xilinx",
            "family": "artix7",
            "part": "xc7a100t-1csg324",
        },
        "board": {
            "xdc_files": ["constraints/pins.xdc"],
        },
        "file_sets": {
            "rtl": [
                {
                    "lib": "work",
                    "vhdl_std": "2008",
                    "src": ["src/**/*.vhd"],
                }
            ],
        },
        "include_dirs_global": [],
    }


@pytest.fixture
def minimal_yaml_quartus():
    """Return minimal valid Quartus project configuration as dict."""
    return {
        "schema_version": "1",
        "project_name": "test_proj",
        "project_root": "..",
        "top": "test_top",
        "tool": {
            "synth": {
                "kind": "quartus",
                "version": "23.1",
                "exe": "quartus_sh",
            }
        },
        "device": {
            "vendor": "intel",
            "family": "max10",
            "part": "10M50DAF484C7G",
        },
        "board": {
            "sdc_files": ["constraints/timing.sdc"],
        },
        "file_sets": {
            "rtl": [
                {
                    "lib": "work",
                    "vhdl_std": "2008",
                    "src": ["src/**/*.vhd"],
                }
            ],
        },
        "include_dirs_global": [],
    }


@pytest.fixture
def minimal_yaml_diamond():
    """Return minimal valid Diamond project configuration as dict."""
    return {
        "schema_version": "1",
        "project_name": "test_proj",
        "project_root": "..",
        "top": "test_top",
        "tool": {
            "synth": {
                "kind": "diamond",
                "version": "3.13",
                "exe": "pnmainc",
            }
        },
        "device": {
            "vendor": "lattice",
            "family": "ecp5",
            "part": "LFE5U-85F-6BG381C",
        },
        "board": {
            "lpf_files": ["constraints/pins.lpf"],
        },
        "file_sets": {
            "rtl": [
                {
                    "lib": "work",
                    "vhdl_std": "2008",
                    "src": ["src/**/*.vhd"],
                }
            ],
        },
        "include_dirs_global": [],
    }


@pytest.fixture
def minimal_yaml_radiant():
    """Return minimal valid Radiant project configuration as dict."""
    return {
        "schema_version": "1",
        "project_name": "test_proj",
        "project_root": "..",
        "top": "test_top",
        "tool": {
            "synth": {
                "kind": "radiant",
                "version": "2024.1",
                "exe": "radiantc",
            }
        },
        "device": {
            "vendor": "lattice",
            "family": "nexus",
            "part": "LIFCL-40-9BG400C",
        },
        "board": {
            "pdc_files": ["constraints/pins.pdc"],
        },
        "file_sets": {
            "rtl": [
                {
                    "lib": "work",
                    "vhdl_std": "2008",
                    "src": ["src/**/*.vhd"],
                }
            ],
        },
        "include_dirs_global": [],
    }


@pytest.fixture
def yaml_with_ip_cores(minimal_yaml_vivado):
    """Return Vivado config extended with IP cores list."""
    cfg = minimal_yaml_vivado.copy()
    cfg["ip_cores"] = [
        {"kind": "xci", "src": "ip/clk_wiz.xci", "generate": True},
        {"kind": "bd", "src": "ip/design_bd.bd", "generate": True},
        {"kind": "edf", "src": "ip/netlist.edf", "lib": "work", "module": "crypto_core"},
    ]
    return cfg


_TOP_VHDL = (
    "library ieee;\n"
    "use ieee.std_logic_1164.all;\n"
    "\n"
    "entity test_top is\n"
    "  port (\n"
    "    clk : in std_logic\n"
    "  );\n"
    "end entity test_top;\n"
    "\n"
    "architecture rtl of test_top is\n"
    "begin\n"
    "end architecture rtl;\n"
)


def _make_tmp_project(tmp_path, cfg_dict, constraint_files):
    """Internal helper used by all per-vendor tmp_project fixtures.

    Builds the standard minimal layout (config/, src/, constraints/),
    writes the YAML, the VHDL top entity, and every placeholder
    constraint file in `constraint_files` (relative-to-project paths).
    """
    (tmp_path / "config").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "constraints").mkdir()

    with open(tmp_path / "config" / "project.yaml", "w") as f:
        yaml.dump(cfg_dict, f, default_flow_style=False)

    (tmp_path / "src" / "top.vhd").write_text(_TOP_VHDL)

    for rel in constraint_files:
        cf = tmp_path / rel
        cf.parent.mkdir(parents=True, exist_ok=True)
        cf.write_text("# Placeholder constraint file\n")

    return tmp_path


@pytest.fixture
def tmp_project(tmp_path, minimal_yaml_vivado):
    """Minimal Vivado project tree under tmp_path. Vivado-shaped YAML
    + constraints/pins.xdc."""
    return _make_tmp_project(
        tmp_path, minimal_yaml_vivado, ["constraints/pins.xdc"]
    )


@pytest.fixture
def tmp_project_quartus(tmp_path, minimal_yaml_quartus):
    """Minimal Quartus project tree. SDC instead of XDC."""
    return _make_tmp_project(
        tmp_path, minimal_yaml_quartus, ["constraints/timing.sdc"]
    )


@pytest.fixture
def tmp_project_diamond(tmp_path, minimal_yaml_diamond):
    """Minimal Diamond project tree. LPF instead of XDC."""
    return _make_tmp_project(
        tmp_path, minimal_yaml_diamond, ["constraints/pins.lpf"]
    )


@pytest.fixture
def patch_subprocess():
    """
    Patch subprocess.call and subprocess.check_output to prevent real tool invocation.

    Not autouse to allow explicit override in integration tests.
    Raises AssertionError if actually called.
    """
    def _call_guard(*args, **kwargs):
        raise AssertionError(
            f"subprocess.call invoked with args={args}, kwargs={kwargs}. "
            "Real FPGA tools should not be called in unit tests."
        )

    def _check_output_guard(*args, **kwargs):
        raise AssertionError(
            f"subprocess.check_output invoked with args={args}, kwargs={kwargs}. "
            "Real FPGA tools should not be called in unit tests."
        )

    with patch("subprocess.call", side_effect=_call_guard), \
         patch("subprocess.check_output", side_effect=_check_output_guard):
        yield
