#!/usr/bin/env tclsh
# SPDX-License-Identifier: Apache-2.0
# Copyright 2024-2026 LogiMentor S.r.l.

# -----------------------------------------------------------------------------
# Lattice Radiant non-project flow (batch/CI-friendly), YAML-driven.
#
# Actions:
#   create : create .rdf project and register sources
#   synth  : prj_run_synthesis
#   impl   : prj_run_map + prj_run_par
#   bit    : prj_run_bitstream
#
# Usage:
#   radiantc aurig_build/radiant/build.tcl config/project.yaml <create|synth|impl|bit>
#   (radiantc = Radiant Tcl console launcher)
# -----------------------------------------------------------------------------

package require Tcl 8.5
source [file join [file dirname [file normalize [info script]]] .. common yaml.tcl]
source [file join [file dirname [file normalize [info script]]] .. common glob.tcl]
source [file join [file dirname [file normalize [info script]]] constraints.tcl]

proc die {m}  { puts stderr "ERROR: $m"; exit 2 }
proc log {m}  { puts "INFO: $m" }
proc warn {m} { puts "WARN: $m" }
proc npath {p} { return [string map {"\\" "/"} [file normalize $p]] }
proc ensure_dir {d} { if {![file isdirectory $d]} { file mkdir $d }; return [file normalize $d] }

# Pattern expansion delegates to aurig_build/common/glob.tcl (zero-or-more `**`).
# File-typed because the only caller (file_sets resolution) feeds the
# result to `prj_add_source`.
proc resolve_patterns {base patterns} {
    set out {}
    foreach p $patterns {
        if {$p eq ""} continue
        foreach h [::lm::glob::expand $base $p] {
            if {[file isfile $h]} { lappend out $h }
        }
    }
    if {[llength $out] == 0} { warn "no files matched any of: $patterns (base: $base)" }
    return [lsort -unique $out]
}

# --- create/open project and add sources ---
proc radiant_create_project {proj_dir name impl top device vhdl_files v_files sv_files constr_files} {
    set rdf [file normalize [file join $proj_dir "${name}.rdf"]]
    prj_create -force $rdf                           ;# create new project file
    prj_add_impl $impl
    prj_set_impl_opt -impl $impl top $top            ;# set top (Radiant)
    prj_set_device -impl $impl $device

    foreach f $vhdl_files   { prj_add_source $f -lib work }
    foreach f $v_files      { prj_add_source $f }
    foreach f $sv_files     { prj_add_source $f }
    foreach f $constr_files { prj_add_source $f }

    # board.{xdc,pdc,sdc}_files: documented route, independent of file_sets.rtl.
    # Radiant never read board:, so PDC/SDC declared there were dropped (#32).
    radiant_add_board_constraints $impl $::Y $::BASE

    prj_save
    return $rdf
}

# Run a Radiant flow step and propagate failure to the process exit code, so a
# failed synth/map/par/bitstream surfaces as a non-zero CLI exit instead of a
# bare Tcl stack trace or a swallowed error (#21).
proc run_step {cmd args} {
    if {[catch {$cmd {*}$args} e]} {
        puts stderr "ERROR: $cmd failed: $e"
        catch { prj_save }
        exit 1
    }
}

# Args & YAML
if {$argc < 2} { die "Usage: build.tcl <config.yaml> <action>" }
set CFG    [lindex $argv 0]
set ACTION [string tolower [lindex $argv 1]]

set Y [::lm::yaml::read_yaml $CFG]
foreach k {project_name top} { if {![dict exists $Y $k]} { die "Missing key '$k' in YAML" } }
set name [dict get $Y project_name]
set top  [dict get $Y top]
if {![dict exists $Y device] || ![dict exists [dict get $Y device] part]} {
    die "device.part is required (e.g. LIFCL-40-9BG400C)"
}
set part [dict get [dict get $Y device] part]

set BASE [file normalize [file join [file dirname [file normalize $CFG]] ..]]

# Expand files
lassign [::lm::yaml::expand_file_sets_rtl $Y] files_with_lib lib_includes
set vhdl_files {}; set v_files {}; set sv_files {}; set constr_files {}
foreach trip $files_with_lib {
    lassign $trip fpat lib std
    set hits [resolve_patterns $BASE [list $fpat]]
    foreach f $hits {
        if {[string match -nocase *.vhd* $f]} {
            lappend vhdl_files [npath $f]
        } elseif {[string match -nocase *.sv $f]} {
            lappend sv_files [npath $f]
        } elseif {[string match -nocase *.v $f]} {
            lappend v_files [npath $f]
        } elseif {[string match -nocase *.xdc $f] || [string match -nocase *.pdc $f] || \
                  [string match -nocase *.sdc $f]} {
            lappend constr_files [npath $f]
        }
    }
}
if {[llength $vhdl_files] + [llength $v_files] + [llength $sv_files] == 0} { die "No HDL files matched." }

# Build dirs
set build_dir [ensure_dir [file join $BASE impl/work radiant $name]]
log "Radiant project: $name"
log "Top           : $top"
log "Part          : $part"
log "Build dir     : $build_dir"
cd $build_dir
set impl "${name}"

# CREATE
if {$ACTION eq "create"} {
    set rdf [radiant_create_project $build_dir $name $impl $top $part \
        $vhdl_files $v_files $sv_files $constr_files]
    log "Wrote RDF: $rdf"
    exit
}
if {![file exists "${name}.rdf"]} {
    log "RDF not found; creating from YAML before '$ACTION'..."
    radiant_create_project $build_dir $name $impl $top $part \
        $vhdl_files $v_files $sv_files $constr_files
}

# SYNTH
if {$ACTION eq "synth"} {
    prj_open "${name}.rdf"
    run_step prj_run_synthesis                ;# Radiant synthesis step
    prj_save
    exit
}
# IMPL
if {$ACTION eq "impl"} {
    prj_open "${name}.rdf"
    run_step prj_run_map
    run_step prj_run_par
    prj_save
    exit
}
# BIT
if {$ACTION eq "bit"} {
    prj_open "${name}.rdf"
    run_step prj_run_bitstream                ;# generate programming file
    prj_save
    exit
}
die "Unknown action: $ACTION (expected: create | synth | impl | bit)"
