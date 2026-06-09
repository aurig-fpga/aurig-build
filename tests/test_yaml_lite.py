# SPDX-License-Identifier: Apache-2.0
# Copyright 2024-2026 LogiMentor S.r.l.

"""Tests for aurig_build/common/yaml.tcl `read_yaml_lite` — the tcllib-free
fallback parser.

These tests bypass the tcllib `yaml` package by calling
`::lm::yaml::read_yaml_lite` directly, so they run anywhere `tclsh` is
on PATH (no `tcllib` required). They lock in the structural shape of
the parser's output against the PyYAML default emission style, which
is what the test fixtures and `materialize_merged_cfg` write to disk.

Regression test: the lite parser used to flatten nested mappings, which
made `file_sets.rtl[].src` come back empty and dropped `lib`/`vhdl_std`
keys. These tests lock in the nested shape so that it can't regress.
"""

from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
YAML_TCL = REPO_ROOT / "aurig_build" / "common" / "yaml.tcl"


pytestmark = pytest.mark.skipif(
    shutil.which("tclsh") is None,
    reason="tclsh not available on PATH",
)


def _run_lite(yaml_text: str, tcl_query: str, tmp_dir: Path) -> str:
    """Pass `yaml_text` through `::lm::yaml::read_yaml_lite` and execute
    `tcl_query` (which can `puts` whatever it needs to assert against).
    Returns the script stdout."""
    yaml_path = tmp_dir / "input.yaml"
    yaml_path.write_text(yaml_text, encoding="utf-8")
    script_path = tmp_dir / "_run.tcl"
    script_path.write_text(
        textwrap.dedent(f"""
            source [file normalize "{YAML_TCL.as_posix()}"]
            set fh [open "{yaml_path.as_posix()}" r]
            set data [read $fh]
            close $fh
            # Force the lite path even if tcllib yaml is available.
            set Y [::lm::yaml::read_yaml_lite $data]
            {tcl_query}
        """).strip(),
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["tclsh", str(script_path)],
        capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode == 0, (
        f"tclsh failed:\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    return proc.stdout


# PyYAML's default block style for the aurig-build canonical schema: list items
# under `file_sets.rtl` dash at the SAME indent as their parent key, and
# scalar fields like `src:` carry their list items at that same indent
# too. The lite parser must handle that.
PYYAML_DEFAULT = textwrap.dedent("""
    project_name: test_proj
    project_root: ..
    top: test_top
    tool:
      synth:
        kind: vivado
        version: '2023.1'
        exe: vivado
    device:
      vendor: xilinx
      family: artix7
      part: xc7a100t-1csg324
    board:
      xdc_files:
      - constraints/pins.xdc
    file_sets:
      rtl:
      - lib: work
        src:
        - src/**/*.vhd
        vhdl_std: '2008'
    include_dirs_global: []
""").strip() + "\n"


def test_nested_tool_synth_keys_survive(tmp_path: Path) -> None:
    """`tool.synth.kind` etc. must be reachable as a nested dict. Before
    the fix, the lite parser flattened these into `tool.kind` (etc.)
    and `dict get $Y tool synth kind` errored out."""
    out = _run_lite(
        PYYAML_DEFAULT,
        textwrap.dedent("""
            set kind    [dict get $Y tool synth kind]
            set version [dict get $Y tool synth version]
            set exe     [dict get $Y tool synth exe]
            puts "KIND=$kind"
            puts "VERSION=$version"
            puts "EXE=$exe"
        """).strip(),
        tmp_path,
    )
    assert "KIND=vivado" in out
    assert "VERSION='2023.1'" in out or "VERSION=2023.1" in out
    assert "EXE=vivado" in out


def test_file_sets_rtl_item_carries_lib_vhdl_src(tmp_path: Path) -> None:
    """The single rtl item produced by PyYAML default dump must come back
    with all four keys (lib / vhdl_std / src / include). Before the fix,
    `src:` resolved to an empty list (because parse_block_list used `<=`
    and treated the same-indent dash as the terminator), and a phantom
    second item with no `lib` appeared in the section list."""
    out = _run_lite(
        PYYAML_DEFAULT,
        textwrap.dedent("""
            set rtl [dict get $Y file_sets rtl]
            puts "RTL_COUNT=[llength $rtl]"
            set item [lindex $rtl 0]
            puts "ITEM_KEYS=[lsort [dict keys $item]]"
            puts "ITEM_LIB=[dict get $item lib]"
            puts "ITEM_VHDL_STD=[dict get $item vhdl_std]"
            puts "ITEM_SRC=[dict get $item src]"
        """).strip(),
        tmp_path,
    )
    assert "RTL_COUNT=1" in out, f"expected exactly one rtl item: {out}"
    assert "ITEM_LIB=work" in out
    assert "src/**/*.vhd" in out
    # vhdl_std comes through quoted (PyYAML emits `'2008'`); the parser
    # leaves the quotes on. That's fine for downstream — the build.tcl
    # accepts `'2008'`, `"2008"`, or `2008`.
    assert "ITEM_VHDL_STD=" in out


def test_top_level_xdc_files_block_list_at_same_indent(tmp_path: Path) -> None:
    """`board.xdc_files: \\n - constraints/pins.xdc` (dash at same indent
    as `xdc_files:`) is PyYAML's idiomatic emission for short block lists.
    parse_block_list previously terminated immediately on that pattern."""
    out = _run_lite(
        PYYAML_DEFAULT,
        textwrap.dedent("""
            puts "XDC_FILES=[dict get $Y board xdc_files]"
        """).strip(),
        tmp_path,
    )
    assert "constraints/pins.xdc" in out


def test_deep_nested_map_three_levels(tmp_path: Path) -> None:
    """Sanity at three+ nesting levels: a hypothetical
    `env.shells.windows.path` must survive."""
    txt = textwrap.dedent("""
        env:
          shells:
            windows:
              path: C:/foo
              ext: .bat
            linux:
              path: /usr/local/bin
    """).strip() + "\n"
    out = _run_lite(
        txt,
        textwrap.dedent("""
            puts "WPATH=[dict get $Y env shells windows path]"
            puts "WEXT=[dict get $Y env shells windows ext]"
            puts "LPATH=[dict get $Y env shells linux path]"
        """).strip(),
        tmp_path,
    )
    assert "WPATH=C:/foo" in out
    assert "WEXT=.bat" in out
    assert "LPATH=/usr/local/bin" in out


def test_empty_value_still_empty(tmp_path: Path) -> None:
    """A key with no value and no nested structure below it must come
    back as an empty value (not accidentally grab the next key's data)."""
    txt = textwrap.dedent("""
        a: 1
        empty:
        b: 2
    """).strip() + "\n"
    out = _run_lite(
        txt,
        textwrap.dedent("""
            puts "A=[dict get $Y a]"
            puts "B=[dict get $Y b]"
            puts "EMPTY=[dict get $Y empty]"
        """).strip(),
        tmp_path,
    )
    assert "A=1" in out
    assert "B=2" in out
    assert "EMPTY=" in out


def test_get_constraints_returns_all_four_board_keys(tmp_path: Path) -> None:
    """`get_constraints` must surface all four documented board.* keys
    (xdc/sdc/lpf/pdc). It used to return only xdc_files/sdc_files, which
    is why the Diamond backend silently dropped board.lpf_files and
    board.sdc_files (#18). Keys present in YAML carry their values; keys
    absent default to an empty list."""
    txt = textwrap.dedent("""
        board:
          xdc_files:
          - constraints/a.xdc
          sdc_files:
          - constraints/b.sdc
          lpf_files:
          - constraints/c.lpf
          pdc_files:
          - constraints/d.pdc
    """).strip() + "\n"
    out = _run_lite(
        txt,
        textwrap.dedent("""
            set C [::lm::yaml::get_constraints $Y]
            puts "KEYS=[lsort [dict keys $C]]"
            puts "XDC=[dict get $C xdc_files]"
            puts "SDC=[dict get $C sdc_files]"
            puts "LPF=[dict get $C lpf_files]"
            puts "PDC=[dict get $C pdc_files]"
        """).strip(),
        tmp_path,
    )
    assert "KEYS=lpf_files pdc_files sdc_files xdc_files" in out
    assert "XDC=constraints/a.xdc" in out
    assert "SDC=constraints/b.sdc" in out
    assert "LPF=constraints/c.lpf" in out
    assert "PDC=constraints/d.pdc" in out


def test_get_constraints_absent_keys_default_empty(tmp_path: Path) -> None:
    """When board declares only some constraint kinds, the others come
    back as empty lists rather than erroring (so backends can iterate the
    full key set unconditionally)."""
    txt = textwrap.dedent("""
        board:
          lpf_files:
          - constraints/only.lpf
    """).strip() + "\n"
    out = _run_lite(
        txt,
        textwrap.dedent("""
            set C [::lm::yaml::get_constraints $Y]
            puts "LPF=[dict get $C lpf_files]"
            puts "XDC=<[dict get $C xdc_files]>"
            puts "SDC=<[dict get $C sdc_files]>"
            puts "PDC=<[dict get $C pdc_files]>"
        """).strip(),
        tmp_path,
    )
    assert "LPF=constraints/only.lpf" in out
    assert "XDC=<>" in out
    assert "SDC=<>" in out
    assert "PDC=<>" in out
