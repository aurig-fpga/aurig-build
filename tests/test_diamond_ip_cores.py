# SPDX-License-Identifier: Apache-2.0
# Copyright 2024-2026 LogiMentor S.r.l.

"""Unit tests for aurig_build/diamond/ip_cores.tcl::diamond_add_ip_cores.

The dispatch logic for IPX / LPC / EDIF IP cores is exercised under
tclsh against a `prj_src` mock — no Lattice Diamond installation is
required. Validates that:

* IPX entries call `prj_src add -impl ... -format IPX <file>`
* LPC entries call `prj_src add -impl ... -format LPC <file>`
* EDIF entries with the default `work` library omit `-work`
* EDIF entries with a non-`work` library pass `-work <lib>`
* xci / bd / qip entries (other backends) are silently skipped
* `src` patterns that resolve to no file emit a WARN and skip
"""

from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
YAML_TCL = REPO_ROOT / "aurig_build" / "common" / "yaml.tcl"
GLOB_TCL = REPO_ROOT / "aurig_build" / "common" / "glob.tcl"
IP_TCL   = REPO_ROOT / "aurig_build" / "diamond" / "ip_cores.tcl"


pytestmark = pytest.mark.skipif(
    shutil.which("tclsh") is None,
    reason="tclsh not available on PATH",
)


def _run(tmp_dir: Path, yaml_text: str, expected_files: list[str]) -> str:
    """Materialize the YAML + the placeholder IP files; run
    `diamond_add_ip_cores impl <tmp> <Y>` under a `prj_src` mock and
    return its stdout (one line per mock call)."""
    yaml_path = tmp_dir / "project.yaml"
    yaml_path.write_text(yaml_text, encoding="utf-8")
    for rel in expected_files:
        p = tmp_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# placeholder\n", encoding="utf-8")

    script = textwrap.dedent(f"""
        # Mock the Diamond + project plumbing first; the SUT only depends
        # on `prj_src`, the rest is just to satisfy the env.
        set ::PRJ_SRC_CALLS {{}}
        proc prj_src {{args}} {{ lappend ::PRJ_SRC_CALLS $args; return 0 }}
        proc npath {{p}} {{ return [string map {{"\\\\" "/"}} [file normalize $p]] }}
        proc log  {{m}} {{ puts "LOG: $m" }}
        proc warn {{m}} {{ puts "WARN: $m" }}

        source [file normalize "{YAML_TCL.as_posix()}"]
        source [file normalize "{GLOB_TCL.as_posix()}"]
        source [file normalize "{IP_TCL.as_posix()}"]

        set fh [open "{yaml_path.as_posix()}" r]
        set data [read $fh]
        close $fh
        set Y [::lm::yaml::read_yaml_lite $data]

        diamond_add_ip_cores my_impl "{tmp_dir.as_posix()}" $Y

        foreach call $::PRJ_SRC_CALLS {{
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


def test_ipx_kind_routes_to_format_ipx(tmp_path: Path) -> None:
    yaml_text = textwrap.dedent("""
        ip_cores:
          - kind: ipx
            src: ip/fifo.ipx
    """).strip() + "\n"
    out = _run(tmp_path, yaml_text, ["ip/fifo.ipx"])
    calls = [l for l in out.splitlines() if l.startswith("CALL:")]
    assert len(calls) == 1, out
    assert "-format IPX" in calls[0]
    assert "fifo.ipx" in calls[0]
    assert "-impl my_impl" in calls[0]


def test_lpc_kind_routes_to_format_lpc(tmp_path: Path) -> None:
    yaml_text = textwrap.dedent("""
        ip_cores:
          - kind: lpc
            src: ip/transceiver.lpc
    """).strip() + "\n"
    out = _run(tmp_path, yaml_text, ["ip/transceiver.lpc"])
    calls = [l for l in out.splitlines() if l.startswith("CALL:")]
    assert len(calls) == 1, out
    assert "-format LPC" in calls[0]
    assert "transceiver.lpc" in calls[0]


def test_edif_kind_with_default_work_lib_omits_work_flag(tmp_path: Path) -> None:
    yaml_text = textwrap.dedent("""
        ip_cores:
          - kind: edf
            src: ip/netlist.edf
    """).strip() + "\n"
    out = _run(tmp_path, yaml_text, ["ip/netlist.edf"])
    calls = [l for l in out.splitlines() if l.startswith("CALL:")]
    assert len(calls) == 1, out
    assert "-format EDIF" in calls[0]
    # default lib == work → do NOT pass -work to avoid double-binding to
    # the implicit default library.
    assert "-work" not in calls[0], (
        f"-work should not appear when lib is the default 'work': {calls[0]}"
    )


def test_edif_kind_with_explicit_lib_passes_work_flag(tmp_path: Path) -> None:
    yaml_text = textwrap.dedent("""
        ip_cores:
          - kind: edf
            src: ip/netlist.edf
            lib: crypto
    """).strip() + "\n"
    out = _run(tmp_path, yaml_text, ["ip/netlist.edf"])
    calls = [l for l in out.splitlines() if l.startswith("CALL:")]
    assert len(calls) == 1, out
    assert "-format EDIF" in calls[0]
    assert "-work crypto" in calls[0]


def test_vivado_and_quartus_kinds_are_silently_skipped(tmp_path: Path) -> None:
    """xci/bd/qip belong to other backends. The Python `validate_ip_cores`
    has already emitted its WARN before this script runs, so the TCL side
    just skips."""
    yaml_text = textwrap.dedent("""
        ip_cores:
          - kind: xci
            src: ip/clk_wiz.xci
          - kind: bd
            src: ip/design.bd
          - kind: qip
            src: ip/pll.qip
    """).strip() + "\n"
    out = _run(tmp_path, yaml_text, [
        "ip/clk_wiz.xci", "ip/design.bd", "ip/pll.qip",
    ])
    calls = [l for l in out.splitlines() if l.startswith("CALL:")]
    assert calls == [], f"unexpected prj_src calls for non-Diamond kinds: {calls}"


def test_unmatched_src_pattern_warns_and_skips(tmp_path: Path) -> None:
    yaml_text = textwrap.dedent("""
        ip_cores:
          - kind: edf
            src: ip/nope/**/*.edf
    """).strip() + "\n"
    # No matching file on disk.
    out = _run(tmp_path, yaml_text, [])
    calls = [l for l in out.splitlines() if l.startswith("CALL:")]
    assert calls == [], f"expected no call for unmatched src: {calls}"
    assert "WARN: IP core src" in out


def test_glob_pattern_in_src_resolves_to_multiple_files(tmp_path: Path) -> None:
    """A `src` like `ip/**/*.edf` must add every matching file."""
    yaml_text = textwrap.dedent("""
        ip_cores:
          - kind: edf
            src: ip/**/*.edf
    """).strip() + "\n"
    out = _run(tmp_path, yaml_text, [
        "ip/a.edf",
        "ip/sub/b.edf",
        "ip/sub/deep/c.edf",
    ])
    calls = [l for l in out.splitlines() if l.startswith("CALL:")]
    assert len(calls) == 3, f"expected 3 EDIF adds, got: {calls}"
    for c in calls:
        assert "-format EDIF" in c
