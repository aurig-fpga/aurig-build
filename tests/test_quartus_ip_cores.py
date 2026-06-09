# SPDX-License-Identifier: Apache-2.0
# Copyright 2024-2026 LogiMentor S.r.l.

"""Unit tests for aurig_build/quartus/ip_cores.tcl::quartus_emit_ip_cores.

The QSF-line emission for QIP / EDIF IP cores is exercised under
tclsh against an in-test output file — no Intel Quartus installation
is required. Validates that:

* `kind: qip` emits `set_global_assignment -name QIP_FILE "<rel>"`
* `kind: edf` with default `work` library emits `EDIF_FILE` without `-library`
* `kind: edf` with explicit library emits `EDIF_FILE` with `-library <lib>`
* xci / bd / ipx / lpc entries (other backends) are silently skipped
* `src` patterns that resolve to no file emit a WARN and skip
* glob in `src` resolves to multiple files (one QSF line each)
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
IP_TCL   = REPO_ROOT / "aurig_build" / "quartus" / "ip_cores.tcl"


pytestmark = pytest.mark.skipif(
    shutil.which("tclsh") is None,
    reason="tclsh not available on PATH",
)


def _run(tmp_dir: Path, yaml_text: str, expected_files: list[str]) -> tuple[str, str]:
    """Materialize the YAML + the placeholder IP files; run
    `quartus_emit_ip_cores <fh> <ip_cores> <tmp>` and return
    (qsf_contents, stdout_stderr_combined)."""
    yaml_path = tmp_dir / "project.yaml"
    yaml_path.write_text(yaml_text, encoding="utf-8")
    for rel in expected_files:
        p = tmp_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# placeholder\n", encoding="utf-8")

    qsf_path = tmp_dir / "out.qsf"

    script = textwrap.dedent(f"""
        # Minimal _qsf_rel for the SUT: build.tcl's real version cd's
        # into the build dir before writing; here we just emit a forward-
        # slash relative-to-tmp_dir representation so assertions are
        # platform-stable.
        proc _qsf_rel {{abs}} {{
            set abs [string map {{"\\\\" "/"}} [file normalize $abs]]
            set base [string map {{"\\\\" "/"}} [file normalize "{tmp_dir.as_posix()}"]]
            if {{[string first $base $abs] == 0}} {{
                set rel [string range $abs [expr {{[string length $base]+1}}] end]
                if {{$rel ne ""}} {{ return $rel }}
            }}
            return $abs
        }}

        source [file normalize "{YAML_TCL.as_posix()}"]
        source [file normalize "{GLOB_TCL.as_posix()}"]
        source [file normalize "{IP_TCL.as_posix()}"]

        set fh_y [open "{yaml_path.as_posix()}" r]
        set ydata [read $fh_y]
        close $fh_y
        set Y [::lm::yaml::read_yaml_lite $ydata]

        set fh [open "{qsf_path.as_posix()}" w]
        quartus_emit_ip_cores $fh [::lm::yaml::get_ip_cores $Y] "{tmp_dir.as_posix()}"
        close $fh
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
    qsf = qsf_path.read_text(encoding="utf-8") if qsf_path.exists() else ""
    return qsf, proc.stdout + proc.stderr


def test_qip_kind_emits_qip_file_assignment(tmp_path: Path) -> None:
    yaml_text = textwrap.dedent("""
        ip_cores:
          - kind: qip
            src: ip/pll.qip
    """).strip() + "\n"
    qsf, _ = _run(tmp_path, yaml_text, ["ip/pll.qip"])
    lines = [l for l in qsf.splitlines() if l.strip()]
    assert len(lines) == 1, qsf
    assert lines[0] == 'set_global_assignment -name QIP_FILE "ip/pll.qip"'


def test_edif_kind_default_work_library_omits_library_flag(tmp_path: Path) -> None:
    yaml_text = textwrap.dedent("""
        ip_cores:
          - kind: edf
            src: ip/netlist.edf
    """).strip() + "\n"
    qsf, _ = _run(tmp_path, yaml_text, ["ip/netlist.edf"])
    lines = [l for l in qsf.splitlines() if l.strip()]
    assert len(lines) == 1, qsf
    assert "EDIF_FILE" in lines[0]
    assert "netlist.edf" in lines[0]
    # Default `work` library → no `-library` flag (don't double-bind).
    assert "-library" not in lines[0], lines[0]


def test_edif_kind_explicit_library_adds_library_flag(tmp_path: Path) -> None:
    yaml_text = textwrap.dedent("""
        ip_cores:
          - kind: edf
            src: ip/netlist.edf
            lib: crypto
    """).strip() + "\n"
    qsf, _ = _run(tmp_path, yaml_text, ["ip/netlist.edf"])
    lines = [l for l in qsf.splitlines() if l.strip()]
    assert len(lines) == 1, qsf
    assert lines[0] == 'set_global_assignment -name EDIF_FILE "ip/netlist.edf" -library crypto'


def test_other_backend_kinds_are_silently_skipped(tmp_path: Path) -> None:
    """xci/bd (Vivado) and ipx/lpc (Diamond) entries belong to other
    backends. The Python validate_ip_cores has already emitted a [WARN]
    upstream, so the TCL side just produces no QSF lines for them."""
    yaml_text = textwrap.dedent("""
        ip_cores:
          - kind: xci
            src: ip/clk_wiz.xci
          - kind: bd
            src: ip/design.bd
          - kind: ipx
            src: ip/fifo.ipx
          - kind: lpc
            src: ip/transceiver.lpc
    """).strip() + "\n"
    qsf, _ = _run(tmp_path, yaml_text, [
        "ip/clk_wiz.xci", "ip/design.bd", "ip/fifo.ipx", "ip/transceiver.lpc",
    ])
    lines = [l for l in qsf.splitlines() if l.strip()]
    assert lines == [], f"unexpected QSF lines for non-Quartus kinds: {lines}"


def test_unmatched_src_pattern_warns_and_skips(tmp_path: Path) -> None:
    yaml_text = textwrap.dedent("""
        ip_cores:
          - kind: qip
            src: ip/nope/**/*.qip
    """).strip() + "\n"
    qsf, out = _run(tmp_path, yaml_text, [])
    lines = [l for l in qsf.splitlines() if l.strip()]
    assert lines == [], f"expected no QSF line for unmatched src: {lines}"
    assert "WARN: IP core src" in out


def test_glob_pattern_in_src_resolves_to_multiple_files(tmp_path: Path) -> None:
    """A `src` like `ip/**/*.qip` must emit one QSF line per match."""
    yaml_text = textwrap.dedent("""
        ip_cores:
          - kind: qip
            src: ip/**/*.qip
    """).strip() + "\n"
    qsf, _ = _run(tmp_path, yaml_text, [
        "ip/a.qip",
        "ip/sub/b.qip",
        "ip/sub/deep/c.qip",
    ])
    lines = [l for l in qsf.splitlines() if l.strip()]
    assert len(lines) == 3, f"expected 3 QIP_FILE lines, got: {lines}"
    for l in lines:
        assert "-name QIP_FILE" in l
