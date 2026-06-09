# SPDX-License-Identifier: Apache-2.0
# Copyright 2024-2026 LogiMentor S.r.l.

"""Tcl-level checks for aurig_build/common/yaml.tcl expand_file_sets / _rtl.

Skipped when tclsh is not on PATH. Part of building out Tcl-level test
coverage by exercising the split without requiring any vendor tool.
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


def _run_tcl(script: str, tmp_dir: Path) -> subprocess.CompletedProcess:
    # `tclsh -` (read from stdin) is not portable across Tcl distributions:
    # stock Unix Tcl treats `-` as a literal filename and errors out. Write
    # the script to a temp file and pass that path instead.
    script_path = tmp_dir / "_run.tcl"
    script_path.write_text(script, encoding="utf-8")
    return subprocess.run(
        ["tclsh", str(script_path)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=10,
    )


def test_expand_file_sets_rtl_excludes_sim(tmp_path: Path) -> None:
    """expand_file_sets_rtl must omit file_sets.sim, while expand_file_sets
    (combined) keeps both rtl and sim, giving
    the synth dispatchers an rtl-only view of file_sets."""
    script = textwrap.dedent(f"""
        source [file normalize "{YAML_TCL.as_posix()}"]

        # Hand-built dict shaped like what parse_file_sets emits for:
        #   file_sets:
        #     rtl:
        #       - {{lib: work, src: [src/a.vhd, src/b.vhd]}}
        #     sim:
        #       - {{lib: tb, src: [tb/tb_top.vhd]}}
        set Y [dict create file_sets [dict create \\
            rtl [list \\
                [dict create lib work src [list src/a.vhd src/b.vhd]]] \\
            sim [list \\
                [dict create lib tb src [list tb/tb_top.vhd]]] \\
        ]]

        lassign [::lm::yaml::expand_file_sets $Y] all_files _all_inc
        lassign [::lm::yaml::expand_file_sets_rtl $Y] rtl_files _rtl_inc

        puts "ALL=[llength $all_files] RTL=[llength $rtl_files]"
        # Synthesis must NOT see the tb entry.
        set tb_in_rtl 0
        foreach t $rtl_files {{
            if {{[lindex $t 0] eq "tb/tb_top.vhd"}} {{ set tb_in_rtl 1 }}
        }}
        puts "TB_IN_RTL=$tb_in_rtl"
        # And combined must include it.
        set tb_in_all 0
        foreach t $all_files {{
            if {{[lindex $t 0] eq "tb/tb_top.vhd"}} {{ set tb_in_all 1 }}
        }}
        puts "TB_IN_ALL=$tb_in_all"
    """).strip()

    proc = _run_tcl(script, tmp_path)
    assert proc.returncode == 0, f"tclsh failed: {proc.stderr}"
    out = proc.stdout
    assert "ALL=3 RTL=2" in out, f"unexpected counts: {out}"
    assert "TB_IN_RTL=0" in out, f"sim leaked into rtl-only view: {out}"
    assert "TB_IN_ALL=1" in out, f"sim missing from combined view: {out}"


def test_materialized_yaml_is_readable_by_tcl_loader(tmp_path: Path) -> None:
    """End-to-end round-trip: materialize_merged_cfg writes a YAML that
    ::lm::yaml::read_yaml in aurig_build/common/yaml.tcl must be able to parse,
    and nested keys (tool.synth.kind, tool.synth.version) must survive
    intact. Without this check, an incompatibility between PyYAML's
    emitter and yaml.tcl's parser (tcllib-backed OR the lite fallback)
    could silently break every TCL backend on overlay use even with
    Python-side tests green.

    Used to be skipped when tcllib was missing because the lite fallback
    flattened nested mappings. After the lite parser fix, the lite path
    produces the same nested shape, so this test now
    runs unconditionally — whichever parser is in effect must agree.
    """
    import yaml as _yaml
    from aurig_build.run import materialize_merged_cfg

    base = tmp_path / "project.yaml"
    base.write_text(_yaml.dump({
        "project_name": "demo",
        "tool": {"synth": {"kind": "vivado", "version": "2023.1"}},
    }))
    # Overlay required for materialize to produce a side-file.
    (tmp_path / "project.local.yaml").write_text(
        _yaml.dump({"tool": {"synth": {"kind": "radiant", "version": "9.9.9"}}})
    )

    merged_cfg = {
        "project_name": "demo",
        "tool": {"synth": {"kind": "radiant", "version": "9.9.9"}},
    }
    materialized = materialize_merged_cfg(base, merged_cfg)
    assert materialized != base, "expected materialize to produce a side-file"

    script = textwrap.dedent(f"""
        source [file normalize "{YAML_TCL.as_posix()}"]
        set Y [::lm::yaml::read_yaml "{materialized.as_posix()}"]
        # Walk the nested structure the way build.tcl / sim.tcl do.
        set kind    [dict get $Y tool synth kind]
        set version [dict get $Y tool synth version]
        set name    [dict get $Y project_name]
        puts "PROJECT_NAME=$name"
        puts "KIND=$kind"
        puts "VERSION=$version"
    """).strip()

    proc = _run_tcl(script, tmp_path)
    assert proc.returncode == 0, (
        f"TCL loader failed on materialized YAML.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert "PROJECT_NAME=demo" in proc.stdout
    assert "KIND=radiant" in proc.stdout, \
        f"overlay's CLI-shaped kind did not round-trip through PyYAML -> TCL parser:\n{proc.stdout}"
    assert "VERSION=9.9.9" in proc.stdout


def test_expand_file_sets_rtl_handles_missing_file_sets(tmp_path: Path) -> None:
    """A YAML without file_sets must return empty lists, not error."""
    script = textwrap.dedent(f"""
        source [file normalize "{YAML_TCL.as_posix()}"]
        set Y [dict create]
        lassign [::lm::yaml::expand_file_sets_rtl $Y] files inc
        puts "FILES=[llength $files] INC_KEYS=[llength [dict keys $inc]]"
    """).strip()
    proc = _run_tcl(script, tmp_path)
    assert proc.returncode == 0, f"tclsh failed: {proc.stderr}"
    assert "FILES=0 INC_KEYS=0" in proc.stdout
