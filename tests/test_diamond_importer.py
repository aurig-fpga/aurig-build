# SPDX-License-Identifier: Apache-2.0
# Copyright 2024-2026 LogiMentor S.r.l.

"""Unit tests for the Diamond importer's version helpers (#19) and the
ip_cores / board.sdc_files YAML emission (#17, #29).

Pure-function tests for detect_diamond_version_from_path,
normalize_diamond_version and ip_kind_for_ext (no I/O), plus write_yaml
emission tests that write into tmp_path and read the result back.
"""

from pathlib import Path

import pytest

from aurig_build.diamond.import_ import (
    detect_diamond_version_from_path,
    ip_kind_for_ext,
    normalize_diamond_version,
    write_yaml,
)


@pytest.mark.parametrize(
    "path, expected",
    [
        ("C:/lscc/diamond/3.14/bin/nt64", "3.14"),
        ("C:\\lscc\\diamond\\3.14\\bin\\nt64", "3.14"),
        ("/opt/lscc/diamond/3.14/bin/lin64", "3.14"),
        ("C:/lscc/diamond/3.14.0/bin/nt64", "3.14"),       # patch level normalized
        ("/opt/lscc/diamond/3.14.0/bin/lin64", "3.14"),
        ("C:/tools/mydiamond/3.14/bin", ""),               # not a 'diamond' segment
        ("C:/tools/diamonds/3.14/bin", ""),                # not a 'diamond' segment
        ("C:/some/other/path", ""),
        ("", ""),
    ],
)
def test_detect_diamond_version_from_path(path, expected):
    assert detect_diamond_version_from_path(path) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("3.14", "3.14"),
        ("3.14.0", "3.14"),
        ("3.14.0.75.2", "3.14"),
        ("  3.14.0  ", "3.14"),
        ("nonsense", "nonsense"),
        ("", ""),
    ],
)
def test_normalize_diamond_version(raw, expected):
    assert normalize_diamond_version(raw) == expected


# ---------------- ip_kind_for_ext (#17) ----------------
@pytest.mark.parametrize(
    "ext, expected",
    [
        (".ipx", "ipx"),
        (".lpc", "lpc"),
        (".edf", "edf"),
        (".edn", "edf"),
        (".edif", "edf"),
    ],
)
def test_ip_kind_for_ext_supported(ext, expected):
    assert ip_kind_for_ext(ext) == expected


@pytest.mark.parametrize("ext", [".sbx", ".mem", ".rvl", ".vhd", ".v", ""])
def test_ip_kind_for_ext_unsupported(ext):
    assert ip_kind_for_ext(ext) is None


@pytest.mark.parametrize(
    "ext, expected",
    [(".LPC", "lpc"), (".Edf", "edf"), (".IPX", "ipx"), (".EDIF", "edf")],
)
def test_ip_kind_for_ext_case_insensitive(ext, expected):
    assert ip_kind_for_ext(ext) == expected


# ---------------- write_yaml emission (#17, #29) ----------------
def _emit(tmp_path: Path, **kwargs) -> str:
    """Run write_yaml with sane defaults and return the YAML text."""
    defaults = dict(
        dest_root=str(tmp_path),
        name="proj",
        top="work.top",
        part="LFE5U-25F-6BG256I",
        pdc_relpaths=[],
        lpf_relpaths=[],
        libs_used=["work"],
    )
    defaults.update(kwargs)
    path = write_yaml(**defaults)
    return Path(path).read_text(encoding="utf-8")


def test_write_yaml_emits_ip_cores_when_present(tmp_path):
    text = _emit(
        tmp_path,
        ip_cores_emit=[
            {"kind": "ipx", "src": "ip/pll_main.ipx"},
            {"kind": "edf", "src": "ip/netlist.edf", "lib": "ces_io_lib"},
        ],
    )
    assert "ip_cores:\n" in text
    assert "  - kind: ipx" in text
    assert "    src: ip/pll_main.ipx" in text
    assert "  - kind: edf" in text
    assert "    src: ip/netlist.edf" in text
    assert "    lib: ces_io_lib" in text


@pytest.mark.parametrize("emit", [None, []])
def test_write_yaml_emits_empty_ip_cores(tmp_path, emit):
    text = _emit(tmp_path, ip_cores_emit=emit)
    assert "ip_cores: []" in text


def test_write_yaml_emits_sdc_files_when_present(tmp_path):
    text = _emit(tmp_path, sdc_relpaths=["constraints/atlas.sdc"])
    assert "  sdc_files:\n" in text
    assert "    - constraints/atlas.sdc" in text


@pytest.mark.parametrize("sdc", [None, []])
def test_write_yaml_emits_empty_sdc_files(tmp_path, sdc):
    text = _emit(tmp_path, sdc_relpaths=sdc)
    assert "  sdc_files: []" in text


def test_write_yaml_ip_src_backslashes_become_posix(tmp_path):
    text = _emit(tmp_path, ip_cores_emit=[{"kind": "lpc", "src": "ip\\mac_fir.lpc"}])
    assert "    src: ip/mac_fir.lpc" in text


def test_write_yaml_emits_edf_with_custom_lib(tmp_path):
    """write_yaml must serialize lib: when an ip_cores entry has a non-default
    library (e.g., .edf netlist targeting a specific library)."""
    text = _emit(tmp_path, ip_cores_emit=[
        {"kind": "edf", "src": "ip/netlist.edf", "lib": "mylib"}
    ])
    assert "- kind: edf" in text
    assert "    src: ip/netlist.edf" in text
    assert "    lib: mylib" in text


def test_write_yaml_omits_lib_when_default(tmp_path):
    """write_yaml must NOT emit lib: when ip_cores entry omits it
    (defaults handled downstream)."""
    text = _emit(tmp_path, ip_cores_emit=[
        {"kind": "lpc", "src": "ip/mac_fir.lpc"}
    ])
    assert "- kind: lpc" in text
    assert "    src: ip/mac_fir.lpc" in text
    assert "    lib:" not in text
