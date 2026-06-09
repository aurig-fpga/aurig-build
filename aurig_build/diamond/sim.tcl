#!/usr/bin/env tclsh
# SPDX-License-Identifier: Apache-2.0
# Copyright 2024-2026 LogiMentor S.r.l.

# -----------------------------------------------------------------------------
# aurig_build/diamond/sim.tcl
#
# Diamond has no native Tcl-driven simulator. This wrapper exists for
# users who source the per-vendor sim TCL directly from a Makefile / IDE
# (e.g. `pnmainc aurig_build/diamond/sim.tcl <config.yaml> [<tb>]`); it locates
# `vsim` on PATH and delegates to `aurig_build/questa/sim.tcl`. See
# `aurig_build/common/sim_via_questa.tcl` for the shared subprocess plumbing.
#
# Invocations from the canonical CLI (`python -m aurig_build.run sim`) bypass
# this file: they go straight to `aurig_build/questa/sim.tcl` after the Python
# dispatcher defaults `tool.sim.kind` to `questa` when synth is Diamond.
# -----------------------------------------------------------------------------

package require Tcl 8.5

if {$argc < 1} {
    puts stderr "Usage: [info script] <config.yaml> \[<tb>\]"
    exit 2
}
set CFG [lindex $argv 0]
set TB  [expr {$argc >= 2 ? [lindex $argv 1] : ""}]

set _self_dir [file dirname [file normalize [info script]]]
source [file join $_self_dir .. common sim_via_questa.tcl]
set _questa_tcl [file normalize [file join $_self_dir .. questa sim.tcl]]
exit [::lm::sim::run_via_questa $_questa_tcl $CFG $TB]
