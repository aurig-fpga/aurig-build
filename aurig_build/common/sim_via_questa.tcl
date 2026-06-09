# SPDX-License-Identifier: Apache-2.0
# Copyright 2024-2026 LogiMentor S.r.l.

# aurig_build/common/sim_via_questa.tcl
# -----------------------------------------------------------------------------
# Shared helper for the per-vendor sim.tcl wrappers in
# aurig_build/{quartus,diamond,radiant}/sim.tcl.
#
# None of those three vendors ship a native Tcl-driven simulator, so each
# of their sim.tcl entrypoints just locates `vsim` on PATH and hands off
# to `aurig_build/questa/sim.tcl` via `vsim -c -do "do ... ; quit -f"`.
#
# This helper exists so the wrappers are three near-empty files instead
# of three copies of the same 30-line subprocess invocation.
#
# Invocations from the canonical CLI (`python -m aurig_build.run sim` with
# `tool.sim.kind: questa`) bypass these wrappers entirely — they call
# `aurig_build/questa/sim.tcl` directly. The wrappers exist for Makefile / IDE
# setups that source the per-vendor TCL script directly from the vendor
# shell (`quartus_sh -t aurig_build/quartus/sim.tcl ...`, etc.).
# -----------------------------------------------------------------------------

namespace eval ::lm::sim {}

# Locate vsim on PATH. Returns the auto_execok command list (which may be
# multi-element on Windows when vsim is shipped as a .bat / .cmd shim and
# auto_execok prepends `cmd.exe /c ...`). Returns {} when nothing matches.
# Callers must expand the list at exec time, e.g. `exec {*}$vsim -c -do ...`.
proc ::lm::sim::find_vsim {} {
    foreach exe {vsim vsim.exe} {
        set hit [auto_execok $exe]
        if {[llength $hit] > 0} {
            return $hit
        }
    }
    return {}
}

# Delegate to Questa. `questa_tcl` is the absolute path to
# aurig_build/questa/sim.tcl; `cfg` is the YAML path; `tb` is the testbench
# name (may be empty — questa/sim.tcl will fall back to
# sim.default_top_tb in the YAML).
#
# Returns 0 on success or 2 on any error (vsim not found, exec failed).
proc ::lm::sim::run_via_questa {questa_tcl cfg tb} {
    set vsim [::lm::sim::find_vsim]
    if {[llength $vsim] == 0} {
        puts stderr "ERROR: 'vsim' not found on PATH. Questa is required by this wrapper."
        puts stderr "       Either add 'vsim' to PATH (or your vendor env script), or use"
        puts stderr "       'python -m aurig_build.run sim' which honors tool.sim.bin_dir / env_script."
        return 2
    }
    if {![file exists $questa_tcl]} {
        puts stderr "ERROR: Questa sim helper not found: $questa_tcl"
        return 2
    }
    # vsim -do takes a single string that is a Tcl script. Build the
    # script via `[list do ...]` so each argument is Tcl-quoted (handles
    # spaces, `{`, `}`, etc. that hand-rolled `{$cfg}` interpolation
    # would mangle or — worse — re-interpret as a Tcl group delimiter).
    set do_script "[list do $questa_tcl $cfg $tb]; quit -f"
    # Print the joined command for the log; expand $vsim via {*} on the
    # actual exec so a multi-element auto_execok return (e.g.
    # `cmd.exe /c vsim.bat`) keeps all of its parts.
    puts "INFO: delegating to Questa: [join $vsim { }] -c -do \"$do_script\""
    if {[catch {exec {*}$vsim -c -do $do_script >@stdout 2>@stderr} err]} {
        puts stderr "ERROR: Questa invocation failed: $err"
        return 2
    }
    return 0
}
