# SPDX-License-Identifier: Apache-2.0
# Copyright 2024-2026 LogiMentor S.r.l.

"""Unit tests for aurig_build/radiant/constraints.tcl::radiant_add_board_constraints.

The board-level constraint consumption is exercised under tclsh against a
`prj_add_source` mock — no Lattice Radiant installation is required. Closes
the #18-class bug for Radiant (#32): board.{xdc,pdc,sdc}_files were declared
in the manifest but never handed to the tool. Validates that:

* board.pdc_files entries reach prj_add_source
* board.sdc_files entries reach prj_add_source
* board.xdc_files entries reach prj_add_source (cross-vendor convenience)
* no board: section -> zero calls
* a pattern matching no file on disk is silently skipped (no call)
* the same file declared under multiple keys is added exactly once
"""

from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
YAML_TCL         = REPO_ROOT / "aurig_build" / "common" / "yaml.tcl"
GLOB_TCL         = REPO_ROOT / "aurig_build" / "common" / "glob.tcl"
CONSTRAINTS_TCL  = REPO_ROOT / "aurig_build" / "radiant" / "constraints.tcl"


pytestmark = pytest.mark.skipif(
    shutil.which("tclsh") is None,
    reason="tclsh not available on PATH",
)


def _run(tmp_dir: Path, yaml_text: str, expected_files: list[str]) -> str:
    """Materialize the YAML + the placeholder constraint files; run
    `radiant_add_board_constraints my_impl <tmp> <Y>` under a
    `prj_add_source` mock and return its stdout (one `CALL:` line per add)."""
    yaml_path = tmp_dir / "project.yaml"
    yaml_path.write_text(yaml_text, encoding="utf-8")
    for rel in expected_files:
        p = tmp_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# placeholder\n", encoding="utf-8")

    script = textwrap.dedent(f"""
        # The SUT only depends on `prj_add_source` + `npath`; mock/define both.
        set ::ADD_CALLS {{}}
        proc prj_add_source {{args}} {{ lappend ::ADD_CALLS $args; return 0 }}
        proc npath {{p}} {{ return [string map {{"\\\\" "/"}} [file normalize $p]] }}

        source [file normalize "{YAML_TCL.as_posix()}"]
        source [file normalize "{GLOB_TCL.as_posix()}"]
        source [file normalize "{CONSTRAINTS_TCL.as_posix()}"]

        set fh [open "{yaml_path.as_posix()}" r]
        set data [read $fh]
        close $fh
        set Y [::lm::yaml::read_yaml_lite $data]

        radiant_add_board_constraints my_impl $Y "{tmp_dir.as_posix()}"

        foreach call $::ADD_CALLS {{
            puts "CALL: $call"
        }}
    """).strip()

    script_path = tmp_dir / "_run.tcl"
    script_path.write_text(script, encoding="utf-8")
    proc = subprocess.run(
        ["tclsh", str(script_path)],
        capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode == 0, (
        f"tclsh failed:\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    return proc.stdout


def _calls(out: str) -> list[str]:
    return [l for l in out.splitlines() if l.startswith("CALL:")]


def test_pdc_files_are_added(tmp_path: Path) -> None:
    yaml_text = textwrap.dedent("""
        board:
          pdc_files:
            - constraints/pins.pdc
    """).strip() + "\n"
    out = _run(tmp_path, yaml_text, ["constraints/pins.pdc"])
    calls = _calls(out)
    assert len(calls) == 1, out
    assert "pins.pdc" in calls[0]


def test_sdc_files_are_added(tmp_path: Path) -> None:
    yaml_text = textwrap.dedent("""
        board:
          sdc_files:
            - constraints/timing.sdc
    """).strip() + "\n"
    out = _run(tmp_path, yaml_text, ["constraints/timing.sdc"])
    calls = _calls(out)
    assert len(calls) == 1, out
    assert "timing.sdc" in calls[0]


def test_xdc_files_are_added_cross_vendor(tmp_path: Path) -> None:
    yaml_text = textwrap.dedent("""
        board:
          xdc_files:
            - constraints/shared.xdc
    """).strip() + "\n"
    out = _run(tmp_path, yaml_text, ["constraints/shared.xdc"])
    calls = _calls(out)
    assert len(calls) == 1, out
    assert "shared.xdc" in calls[0]


def test_no_board_section_makes_no_calls(tmp_path: Path) -> None:
    yaml_text = textwrap.dedent("""
        project_name: p
        top: t
    """).strip() + "\n"
    out = _run(tmp_path, yaml_text, [])
    assert _calls(out) == [], out


def test_unmatched_pattern_is_silently_skipped(tmp_path: Path) -> None:
    yaml_text = textwrap.dedent("""
        board:
          pdc_files:
            - constraints/nope/**/*.pdc
    """).strip() + "\n"
    # No matching file on disk.
    out = _run(tmp_path, yaml_text, [])
    assert _calls(out) == [], out


def test_same_file_in_multiple_keys_added_once(tmp_path: Path) -> None:
    """A file reachable via two board keys must be added exactly once
    (the proc dedups on the normalized path)."""
    yaml_text = textwrap.dedent("""
        board:
          pdc_files:
            - constraints/dup.pdc
          sdc_files:
            - constraints/dup.pdc
    """).strip() + "\n"
    out = _run(tmp_path, yaml_text, ["constraints/dup.pdc"])
    calls = _calls(out)
    assert len(calls) == 1, f"expected dedup to a single add, got: {calls}"
    assert "dup.pdc" in calls[0]
