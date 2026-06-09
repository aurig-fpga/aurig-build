# SPDX-License-Identifier: Apache-2.0
# Copyright 2024-2026 LogiMentor S.r.l.

"""
Unit tests for version-string parsing helpers in aurig_build/run.py.

Covers:
  - _parse_diamond_version_from_text()
  - _parse_diamond_version_from_path()
  - _parse_quartus_version()
  - _parse_questa_version()
"""

import pytest

from aurig_build.run import (
    _parse_diamond_version_from_text,
    _parse_diamond_version_from_path,
    _parse_radiant_version_from_text,
    _parse_radiant_version_from_path,
    _parse_quartus_version,
    _parse_questa_version,
)


# ============================================================================
# SECTION: _parse_diamond_version_from_text
# ============================================================================

@pytest.mark.parametrize("text, expected", [
    ("Lattice Diamond Version 3.14.0.75.2", "3.14"),
    ("Lattice Diamond Version 3.12.1.454.1", "3.12"),
    ("lattice diamond version 3.11", "3.11"),          # case-insensitive
    ("Diamond Version 3.13 build 123", "3.13"),
    ("", ""),
    ("No version info here", ""),
    ("Diamond without number", ""),
])
def test_parse_diamond_version_from_text(text, expected):
    assert _parse_diamond_version_from_text(text) == expected


# ============================================================================
# SECTION: _parse_diamond_version_from_path
# ============================================================================

@pytest.mark.parametrize("path, expected", [
    # Windows-style paths
    (r"C:\lscc\diamond\3.14\bin\nt64\pnmainc.EXE", "3.14"),
    (r"C:\lscc\diamond\3.12\bin\nt64", "3.12"),
    (r"C:\Program Files\Lattice\diamond\3.13\bin\nt64\pnmainc.exe", "3.13"),
    # POSIX-style paths
    ("/usr/local/diamond/3.11/bin/lin64/pnmainc", "3.11"),
    ("/opt/lattice/diamond/3.14/bin", "3.14"),
    # No-match cases
    ("", ""),
    ("/usr/local/bin", ""),
    (r"C:\tools\quartus\23.1\bin", ""),     # different tool
])
def test_parse_diamond_version_from_path(path, expected):
    assert _parse_diamond_version_from_path(path) == expected


# ============================================================================
# SECTION: _parse_radiant_version_from_text
# ============================================================================

@pytest.mark.parametrize("text, expected", [
    ("Lattice Radiant Software Version 2024.1.0.42", "2024.1"),
    ("Radiant Software Version 2023.2", "2023.2"),
    ("lattice radiant version 2024.1", "2024.1"),     # case-insensitive
    ("Radiant 2024.1 build 99", "2024.1"),
    ("", ""),
    ("No version info here", ""),
    ("Radiant without number", ""),
])
def test_parse_radiant_version_from_text(text, expected):
    assert _parse_radiant_version_from_text(text) == expected


# ============================================================================
# SECTION: _parse_radiant_version_from_path
# ============================================================================

@pytest.mark.parametrize("path, expected", [
    # Windows-style: <root>\radiant\<version>\...
    (r"C:\lscc\radiant\2024.1\bin\nt64\radiantc.exe", "2024.1"),
    (r"C:\lscc\radiant\2023.2\bin\nt64", "2023.2"),
    (r"C:\Program Files\Lattice\radiant\2024.1\bin\nt64\radiantc.exe", "2024.1"),
    # POSIX-style with underscore: /usr/local/radiant_<version>/...
    ("/usr/local/radiant_2024.1/bin/lin64/radiantc", "2024.1"),
    ("/opt/lattice/radiant_2023.2/bin", "2023.2"),
    # POSIX-style with slash: /opt/lattice/radiant/<version>/...
    ("/opt/lattice/radiant/2024.1/bin/lin64/radiantc", "2024.1"),
    # No-match cases
    ("", ""),
    ("/usr/local/bin", ""),
    (r"C:\lscc\diamond\3.14\bin\nt64\pnmainc.exe", ""),    # different tool
    (r"C:\tools\quartus\23.1\bin", ""),
])
def test_parse_radiant_version_from_path(path, expected):
    assert _parse_radiant_version_from_path(path) == expected


# ============================================================================
# SECTION: _parse_quartus_version
# ============================================================================

@pytest.mark.parametrize("text, expected", [
    # Realistic quartus_sh --version output
    ("Quartus Prime Shell\nVersion 23.1 Build 991 11/28/2023 SC Lite Edition", "23.1"),
    ("Quartus Prime Shell\nVersion 23.1.0 Build 474 01/17/2024 SC Pro Edition", "23.1.0"),
    ("Version 22.4 Build 94", "22.4"),
    ("version 21.1.0", "21.1.0"),           # case-insensitive
    # No-match cases
    ("", ""),
    ("Quartus Prime Shell", ""),
    ("No version here", ""),
])
def test_parse_quartus_version(text, expected):
    assert _parse_quartus_version(text) == expected


# ============================================================================
# SECTION: _parse_questa_version
# ============================================================================

@pytest.mark.parametrize("text, expected", [
    # Realistic vsim -version output: version follows "vsim", not directly after "Questa"
    ("# Questa Sim-64 vsim 2023.3 Simulator 2023.07 Jul 17 2023", "2023.3"),
    ("# QuestaSim vsim 2022.4 Simulator 2022.10", "2022.4"),
    ("# Questa Advanced Simulator-64 vsim 2021.1 Compiler 2021.01 Jan  8 2021", "2021.1"),
    ("# QuestaFPGA-64 vsim 2024.1 Simulator", "2024.1"),
    # Simpler "Questa[word_chars] YYYY.N" forms (e.g. version strings embedded in configs)
    ("QuestaSim 2023.3 Simulator", "2023.3"),
    ("Questa 2022.4", "2022.4"),
    ("Questa v2021.1", "2021.1"),
    ("QUESTA 2024.1 build 123", "2024.1"),   # case-insensitive
    # No-match cases
    ("", ""),
    ("vsim 10.7c Simulator", ""),    # old ModelSim format, version not a 4-digit year
    ("No version info", ""),
])
def test_parse_questa_version(text, expected):
    assert _parse_questa_version(text) == expected
