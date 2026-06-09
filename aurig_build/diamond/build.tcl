#!/usr/bin/env tclsh
# SPDX-License-Identifier: Apache-2.0
# Copyright 2024-2026 LogiMentor S.r.l.

# -----------------------------------------------------------------------------
# Lattice Diamond non-project flow (batch/CI-friendly), YAML-driven.
# Actions: create | synth | impl | bit
# Usage  : pnmainc aurig_build/diamond/build.tcl config/project.yaml <action>
# -----------------------------------------------------------------------------

package require Tcl 8.5

# Ensure Diamond project commands are available
if {[catch {package require prj}]} {
    if {[llength [info commands load_package]]} { catch {load_package project} }
}
if {![llength [info commands prj_project]]} {
    puts stderr "ERROR: Diamond 'prj' Tcl package not loaded (no prj_* commands)."
    puts stderr "       Run via 'pnmainc' and ensure Diamond is on PATH."
    exit 2
}

# YAML helpers
source [file join [file dirname [file normalize [info script]]] .. common yaml.tcl]
source [file join [file dirname [file normalize [info script]]] .. common glob.tcl]

proc die {m}  { puts stderr "ERROR: $m"; exit 2 }
proc log {m}  { puts "INFO: $m" }
proc warn {m} { puts "WARN: $m" }

# normalize
proc npath {p} { return [string map {"\\" "/"} [file normalize $p]] }
proc ensure_dir {d} { if {![file isdirectory $d]} { file mkdir $d }; return [file normalize $d] }

# Pattern expansion delegates to aurig_build/common/glob.tcl (zero-or-more `**`).
# Permissive wrapper kept locally: the WARN is emitted only when the whole
# pattern list yields no matches. File-typed because the only caller
# (file_sets resolution) feeds the result to `prj_add_source`, which
# would reject a directory.
proc resolve_patterns {base patterns} {
    set out {}
    foreach p $patterns {
        if {$p eq ""} continue
        foreach h [::lm::glob::expand $base $p] {
            if {[file isfile $h]} { lappend out $h }
        }
    }
    if {[llength $out] == 0} {
        warn "no files matched any of: $patterns (base: $base)"
    }
    return [lsort -unique $out]
}

# ---------- project create/open & populate ----------
proc diamond_create_or_open {build_dir name impl part vhdl_std} {
    file mkdir $build_dir
    # close any project that might be left open from a previous run
    puts "closing project"
    puts "pwd: [pwd]"
    cd $build_dir
    puts "pwd: [pwd]"
    catch { prj_project close }
    if {[file exists "${name}.ldf"]} {
        puts "existing ldf, trying to open project $name"
        prj_project open "${name}.ldf"
    } else {
        puts "no project found, creating new one"
        # NOTE: no '-force' flag in Diamond
        prj_project new -name $name -impl $impl -dev $part
        # default synthesis: prefer Synplify, else LSE
        set synTool ""
        foreach t {"Synplify Pro" "Synplify" "LSE"} {
            if {![catch {prj_impl option -impl $impl "Synthesis Tool" $t}]} { set synTool $t; break }
            if {![catch {prj_impl option -impl $impl "SynthesisTool" $t}]} { set synTool $t; break }
        }
        if {$synTool eq ""} {
            catch { prj_set_option -impl $impl -option "Synthesis Tool" -value "LSE" }
        }
        catch { prj_impl option -impl $impl "VHDL Standard" "VHDL$vhdl_std" }
        catch { prj_impl option -impl $impl "VHDLStandard"  "VHDL$vhdl_std" }
        prj_project save
    }
}

# Add many sources for one library
proc prj_add_files {impl lib lst} {
    foreach f $lst {
        if {![file exists $f]} { continue }
        set ext [string tolower [file extension $f]]
        if {$ext eq ".vhd" || $ext eq ".vhdl"} {
            if {[catch {prj_src add -impl $impl -format VHDL $f -work $lib} e]} {
                puts "WARN: prj_src add failed (VHDL): $f ($e)"
            }
        } elseif {$ext eq ".sv"} {
            if {[catch {prj_src add -impl $impl -format SYSTEMVERILOG $f} e]} {
                puts "WARN: prj_src add failed (SV): $f ($e)"
            }
        } elseif {$ext eq ".v"} {
            if {[catch {prj_src add -impl $impl -format VERILOG $f} e]} {
                puts "WARN: prj_src add failed (V): $f ($e)"
            }
        }
    }
}

# Set implementation top in a robust way (works across Diamond variants)
proc prj_set_top_all {impl qualifiedTop} {
    # qualifiedTop can be "lib.mod" or just "mod"
    set topLib "work"
    set topMod $qualifiedTop
    if {[regexp {^([^\.]+)\.([^\.]+)$} $qualifiedTop -> L M]} {
        set topLib $L
        set topMod $M
    }
    set topFull "${topLib}.${topMod}"
    puts "Setting TOP to $topFull (lib=$topLib, mod=$topMod)"

    # Set every known knob; ignore failures (older releases vary)
    catch { prj_impl option -impl $impl lib $topLib }
    catch { prj_impl option -impl $impl def_top $topFull }
    catch { prj_impl option -impl $impl top $topFull }
}

# Diamond IP-core handling (IPX / LPC / EDIF) lives in a sibling file so
# the dispatch logic is unit-testable under tclsh with a `prj_src` mock,
# without sourcing the rest of this script (which would error out on the
# `package require prj` gate at top).
source [file join [file dirname [file normalize [info script]]] ip_cores.tcl]

# Populate from grouped lists
proc diamond_populate {build_dir name impl top vhdl_by_lib v_by_lib sv_by_lib constr_files} {
    puts "populating ldf with files from $build_dir"
    cd $build_dir
    puts "current working directory: [pwd]"
    foreach {lib lst} $vhdl_by_lib {
        puts "adding VHDL file $lst in library $lib"
        prj_add_files $impl $lib $lst 
    }
    foreach {lib lst} $v_by_lib   { prj_add_files $impl $lib $lst }
    foreach {lib lst} $sv_by_lib  { prj_add_files $impl $lib $lst }

    # Diamond IP cores (IPX / LPC / EDIF).
    diamond_add_ip_cores $impl $::BASE $::Y

    foreach c $constr_files {
        if {![file exists $c]} { continue }
        if {[llength [info commands prj_add_constraint]]} {
            if {[catch { prj_add_constraint $c } e]} {
                puts "WARN: prj_add_constraint failed for $c: $e"
            }
        } else {
            # fallback by extension
            set fmt ""
            switch -- [string tolower [file extension $c]] {
                .pdc { set fmt PDC }
                .lpf { set fmt LPF }
                .xdc { set fmt XDC }
                .sdc { set fmt SDC }
            }
            if {$fmt eq ""} {
                puts "WARN: unknown constraint extension, skipping: $c"
            } elseif {[catch { prj_src add -impl $impl -format $fmt $c } e]} {
                puts "WARN: prj_src add ($fmt) failed for $c: $e"
            }
        }
    }

    # ---------------- Set TOP (lib.top or bare) ----------------
    prj_set_top_all $impl $top

    prj_project save
}

# Run a Diamond milestone and propagate failure to the process exit code.
# prj_run raises a Tcl error when a milestone fails; the synth block used to
# catch that error, log it, and then fall through to a bare `exit` (status 0),
# so CI treated a failed synthesis as success (#21).
proc run_milestone {args} {
    if {[catch {prj_run {*}$args} e]} {
        puts stderr "ERROR: prj_run $args failed: $e"
        catch { prj_project save }
        exit 1
    }
}

# LDF is considered "bare" if it exists but has no HDL sources
proc ldf_is_bare {ldf_path} {
    if {![file exists $ldf_path]} { return 1 }
    set f [open $ldf_path r]; set s [read $f]; close $f
    # Look for any HDL source entries
    return [expr {![regexp -nocase {type_short="(VHDL|VERILOG|SYSTEMVERILOG)"} $s]}]
}

# ---------- main ----------
if {$argc < 2} { die "Usage: build.tcl <config.yaml> <create|synth|impl|bit>" }
set CFG    [lindex $argv 0]
set ACTION [string tolower [lindex $argv 1]]

set Y [::lm::yaml::read_yaml $CFG]
foreach k {project_name top} { if {![dict exists $Y $k]} { die "Missing key '$k' in YAML" } }
set name [dict get $Y project_name]
set top  [dict get $Y top]
if {![dict exists $Y device] || ![dict exists [dict get $Y device] part]} {
    die "device.part is required (e.g. LFE5U-85F-8BG381I)"
}
set part [dict get [dict get $Y device] part]

# Base directory (repo root)
set CFG_ABS [file normalize $CFG]
set CFG_DIR [file dirname  $CFG_ABS]
set BASE    [file normalize [file join $CFG_DIR ..]]
if {[dict exists $Y project_root]} {
    set project_root [dict get $Y project_root]
    if {[file pathtype $project_root] eq "absolute"} {
        set BASE [file normalize $project_root]
    } else {
        set BASE [file normalize [file join $CFG_DIR $project_root]]
    }
}

# Expand file_sets (library-aware)
lassign [::lm::yaml::expand_file_sets_rtl $Y] files_with_lib lib_includes

# Split by lib & ext, and collect constraints
array set vhdl_by_lib {}
array set v_by_lib   {}
array set sv_by_lib  {}
set constr_files {}
foreach trip $files_with_lib {
    lassign $trip fpat lib std
    set hits [resolve_patterns $BASE [list $fpat]]
    foreach f $hits {
        set lf [string tolower $f]
        if {[string match *.vhd $lf] || [string match *.vhdl $lf]} {
            lappend vhdl_by_lib($lib) [npath $f]
        } elseif {[string match *.sv $lf]} {
            lappend sv_by_lib($lib)   [npath $f]
        } elseif {[string match *.v $lf]} {
            lappend v_by_lib($lib)    [npath $f]
        } elseif {[string match *.pdc $lf] || [string match *.lpf $lf] || \
                  [string match *.xdc $lf] || [string match *.sdc $lf]} {
            lappend constr_files [npath $f]
        }
    }
}

# Board-level constraints (board.{xdc,sdc,lpf,pdc}_files): the documented
# route, independent of file_sets.rtl. The Diamond backend never read board:,
# so LPF pin files / SDC timing declared there were silently dropped (#18).
set C [::lm::yaml::get_constraints $Y]
foreach key {xdc_files sdc_files lpf_files pdc_files} {
    set pats [dict get $C $key]
    if {[llength $pats] == 0} { continue }   ;# skip empty: resolve_patterns would WARN
    foreach f [resolve_patterns $BASE $pats] {
        lappend constr_files [npath $f]
    }
}
set constr_files [lsort -unique $constr_files]   ;# dedup if also in file_sets

# sanity
set total_hdl 0
foreach lib [array names vhdl_by_lib] { incr total_hdl [llength $vhdl_by_lib($lib)] }
foreach lib [array names v_by_lib]   { incr total_hdl [llength $v_by_lib($lib)] }
foreach lib [array names sv_by_lib]  { incr total_hdl [llength $sv_by_lib($lib)] }
if {$total_hdl == 0} { die "No HDL files matched. Base=$BASE. Check YAML file_sets." }

# Build dir
set repo_root [file normalize $BASE]
set build_dir [ensure_dir [file join $repo_root impl/work diamond $name]]
log "Diamond project: $name"
log "Top           : $top"
log "Part          : $part"
log "Build dir     : $build_dir"

# Always create/open before any action
puts "Creating/opening diamond project: $name"
diamond_create_or_open $build_dir $name $name $part 2008

# (Re)populate from YAML every time; project files are idempotent
diamond_populate $build_dir $name $name $top [array get vhdl_by_lib] [array get v_by_lib] [array get sv_by_lib] $constr_files
prj_project save
catch { prj_project close }
catch { prj_project open "${name}.ldf" }


set ldf [file join $build_dir "${name}.ldf"]

# create -> populate + exit
if {$ACTION eq "create"} {
    diamond_populate $build_dir $name $name $top \
        [array get vhdl_by_lib] [array get v_by_lib] [array get sv_by_lib] $constr_files
    log "Project created/updated."
    exit
}

# For any other action, (re)populate if the LDF has no HDL
if {[ldf_is_bare $ldf]} {
    puts "INFO: LDF has no HDL sources; repopulating from YAML..."
    diamond_populate $build_dir $name $name $top \
        [array get vhdl_by_lib] [array get v_by_lib] [array get sv_by_lib] $constr_files
}

# Open the project and run
catch { prj_project open $ldf }

# Ensure TOP is set before synth (some versions forget)
prj_set_top_all $name $top
prj_project save

if {$ACTION eq "synth"} {
    if {[llength [info commands prj_report]]} {
        puts "reporting project"
        catch { prj_report processes -impl $name }
    }
    run_milestone Synthesis -impl $name
    prj_project save
    exit
}

if {$ACTION eq "impl"} {
    run_milestone Map -impl $name
    run_milestone PAR -impl $name
    prj_project save
    exit
}
if {$ACTION eq "bit"} {
    run_milestone Export -impl $name -task Bitgen
    prj_project save
    exit
}
die "Unknown action: $ACTION (expected: create | synth | impl | bit)"
