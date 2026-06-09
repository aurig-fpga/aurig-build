# SPDX-License-Identifier: Apache-2.0
# Copyright 2024-2026 LogiMentor S.r.l.

# -----------------------------------------------------------------------------
# Quartus non-project flow (batch/CI-friendly), library-aware, YAML-driven.
#
# Actions:
#   create : prepare project (.qpf/.qsf)
#   synth  : quartus_map
#   impl   : quartus_fit + quartus_sta
#   bit    : quartus_asm
#
# Usage:
#   quartus_sh -t aurig_build/quartus/build.tcl config/project.yaml <create|synth|impl|bit>
# -----------------------------------------------------------------------------

package require Tcl 8.5
source [file join [file dirname [file normalize [info script]]] .. common yaml.tcl]
source [file join [file dirname [file normalize [info script]]] .. common glob.tcl]
source [file join [file dirname [file normalize [info script]]] ip_cores.tcl]

# -----------------------
# Utilities
# -----------------------
proc die {m} { puts stderr "ERROR: $m"; exit 2 }
proc log {m} { puts "INFO: $m" }

proc ensure_dir {d} {
    if {![file isdirectory $d]} { file mkdir $d }
    return [file normalize $d]
}

proc source_if_exists {abs_path} {
    if {[file exists $abs_path]} {
        puts "INFO: Hook -> sourcing [file normalize $abs_path]"
        uplevel #0 [list source $abs_path]
    }
}

# --- Minimal debug toggles (off by default) ---
set ::DBG_PATHS 0
proc dbg {m} { if {$::DBG_PATHS} { puts "DBG: $m" } }

# Pattern expansion delegates to aurig_build/common/glob.tcl. Local wrapper kept
# permissive: empty entries are skipped silently and the WARN is emitted
# only when the whole pattern list yields no matches. File-typed because
# every caller (file_sets sources, SDC, qsf_extra_files) feeds the result
# to QSF SOURCE entries — directories would break Quartus. Include-dir
# callers below go through `::lm::glob::resolve_dirs` directly.
proc resolve_patterns {base patterns} {
    set out {}
    foreach p $patterns {
        if {$p eq ""} continue
        foreach h [::lm::glob::expand $base $p] {
            if {[file isfile $h]} { lappend out $h }
        }
    }
    if {[llength $out] == 0} {
        puts "WARN: no files matched any of: $patterns (base: $base)"
    }
    return [lsort -unique $out]
}

# Convert any path to a QSF-safe absolute path with forward slashes.
proc _qsf_path {p} {
    # normalize to absolute, then force forward slashes (Quartus-friendly on Windows)
    return [string map {"\\" "/"} [file normalize $p]]
}

# Make a forward-slash relative path from FROM to TO.
proc _relpath {from to} {
    set f [file normalize $from]
    set t [file normalize $to]
    set fl [file split $f]; set tl [file split $t]
    set i 0; set n [expr {[llength $fl] < [llength $tl] ? [llength $fl] : [llength $tl]}]
    while {$i < $n && [string equal -nocase [lindex $fl $i] [lindex $tl $i]]} { incr i }
    set up [lrepeat [expr {[llength $fl] - $i}] ..]
    set down [lrange $tl $i end]
    set rel [eval file join $up $down]
    if {$rel eq ""} { set rel "." }
    return [string map {"\\" "/"} $rel]
}

# Relative to the current working directory (we cd into build_dir before writing QSF)
proc _qsf_rel {abs} {
    return [_relpath [pwd] $abs]
}



# -----------------------
# QSF writer  (REPLACE your write_qsf with this)
# -----------------------
proc write_qsf {name top part files_with_lib lib_includes sdc_files global_includes qsf_extra qsf_extra_files} {
    set qsf "${name}.qsf"
    set fh [open $qsf w]

    puts $fh "set_global_assignment -name TOP_LEVEL_ENTITY $top"
    puts $fh "set_global_assignment -name DEVICE $part"

    # Pick VHDL version (Quartus accepts 1993 or 2008)
    set any2008 0
    foreach trip $files_with_lib {
        lassign $trip f lib std
        if {[string equal -nocase $std "2008"] || $std == 2008} { set any2008 1; break }
    }
    puts $fh "set_global_assignment -name VHDL_INPUT_VERSION [expr {$any2008 ? {VHDL_2008} : {VHDL_1993}}]"

    # Optional: quiet parallelism warning
    puts $fh "set_global_assignment -name NUM_PARALLEL_PROCESSORS 8"

    # SEARCH_PATH (relative, forward slashes)
    set search_paths {}
    foreach lib [dict keys $lib_includes] {
        foreach d [dict get $lib_includes $lib] { lappend search_paths [_qsf_rel $d] }
    }
    foreach d $global_includes { lappend search_paths [_qsf_rel $d] }
    foreach p [lsort -unique $search_paths] {
        puts $fh "set_global_assignment -name SEARCH_PATH \"$p\""
    }

    # Source files (relative, forward slashes)
    foreach trip $files_with_lib {
        lassign $trip f lib std
        set fn [_qsf_rel $f]
        if {[string match -nocase *.vhd* $f]} {
            puts $fh "set_global_assignment -name VHDL_FILE \"$fn\" -library $lib"
        } elseif {[string match -nocase *.sv $f]} {
            puts $fh "set_global_assignment -name SYSTEMVERILOG_FILE \"$fn\""
        } elseif {[string match -nocase *.v $f]} {
            puts $fh "set_global_assignment -name VERILOG_FILE \"$fn\""
        } else {
            puts "INFO: Skipping unrecognized file type for QSF: $f"
        }
    }

    # IP cores (QIP / EDIF). Helper lives in aurig_build/quartus/ip_cores.tcl
    # so the QSF-emission logic is unit-testable against a file handle
    # without sourcing the rest of this script (which depends on
    # quartus_sh's command set).
    if {[info exists ::Y]} {
        quartus_emit_ip_cores $fh [::lm::yaml::get_ip_cores $::Y] $::BASE
    }

    # SDCs (relative, forward slashes)
    foreach s $sdc_files {
        puts $fh "set_global_assignment -name SDC_FILE \"[_qsf_rel $s]\""
    }

    # Extra lines
    foreach line $qsf_extra { puts $fh $line }

    # Inline extra QSF fragments verbatim (keep their original text)
    foreach frag $qsf_extra_files {
        if {![file exists $frag]} {
            puts "WARN: qsf_extra_files entry had no matches: $frag"
            continue
        }
        puts $fh "\n# ---- begin inline: $frag ----"
        set f2 [open $frag r]; puts -nonewline $fh [read $f2]; close $f2
        puts $fh "\n# ---- end inline: $frag ----"
    }

    close $fh
    return [file normalize $qsf]
}


# Robust prepare that tolerates noisy stderr and odd exit codes on Windows
proc _quartus_prepare {name top part} {
    set cmd [list quartus_sh --prepare -force -t $top -d $part $name]
    puts "INFO: prepare cmd: $cmd"

    # Merge stderr into stdout so we get a single log stream
    set pipeline [list | {*}$cmd 2>@1]
    set ch [open $pipeline r]
    set logtxt [read $ch]
    # close returns an error if the child exit code != 0; capture details
    set rc 0
    if {[catch {close $ch} err opts]} {
        set rc 1
        # Keep the full log for diagnostics
        append logtxt "\n" $err
    }

    # Heuristics: treat as success if Quartus says it was successful OR artifacts exist
    set qpf [file normalize "${name}.qpf"]
    set qsf [file normalize "${name}.qsf"]
    set banner_ok [expr {[string match "*Quartus Prime Shell was successful.*" $logtxt]
                         || [string match "*Evaluation of Tcl script*was successful*" $logtxt]}]
    set files_ok [expr {[file exists $qpf] && [file exists $qsf]}]

    if {$rc && !$banner_ok && !$files_ok} {
        puts $logtxt
        die "quartus_sh --prepare failed (rc!=0 and no artifacts)"
    }

    # Log and continue
    puts -nonewline $logtxt
    if {$files_ok} { puts "INFO: prepare artifacts present: $qpf, $qsf" }
}

# -----------------------
# Pipeline stages (non-project flow)
# -----------------------
# Run the Fitter. mid_impl_after_fit is intrinsic to this stage (it fires
# right after a successful fit), so it lives here and runs whether fit is
# reached via the 'impl' target or auto-chained from 'bit'.
proc _quartus_fit {name} {
    exec quartus_fit $name >@stdout 2>@stderr
    source_if_exists [file join $::HOOK_DIR mid_impl_after_fit.tcl]
}

proc _quartus_sta {name} {
    exec quartus_sta $name >@stdout 2>@stderr
}

proc _quartus_asm {name} {
    exec quartus_asm $name >@stdout 2>@stderr
}

# Analysis & Synthesis. Unlike fit, this stage has no intrinsic hook (there
# is no mid_synth_after_map), so the proc is just the exec; the target-boundary
# synth hooks stay in the 'synth' block.
proc _quartus_map {name} {
    exec quartus_map $name >@stdout 2>@stderr
}

# Quartus writes "<name>.map.summary" / "<name>.fit.summary" in the build dir
# on a successful map / fit; treat their presence as the "stage completed"
# marker. Existence-only checks, matching the shallow QSF-presence heuristic
# used before synth/impl/bit.
proc _quartus_map_results_exist {name} {
    return [file exists "${name}.map.summary"]
}

proc _quartus_fit_results_exist {name} {
    return [file exists "${name}.fit.summary"]
}

# -----------------------
# Args & YAML
# -----------------------
if {$argc < 2} { die "Usage: build.tcl <config.yaml> <action>" }
set CFG    [lindex $argv 0]
set ACTION [string tolower [lindex $argv 1]]

# Parse YAML
set Y [::lm::yaml::read_yaml $CFG]

# Optional debug
if {[dict exists $Y debug_paths] && [dict get $Y debug_paths]} { set ::DBG_PATHS 1 }

foreach k {project_name top} {
    if {![dict exists $Y $k]} { die "Missing key '$k' in YAML: $k" }
}
set name [dict get $Y project_name]
set top  [dict get $Y top]

# Device (required)
if {![dict exists $Y device] || ![dict exists [dict get $Y device] part]} {
    die "device.part is required, e.g. 10CL025YU256I7G"
}
set part [dict get [dict get $Y device] part]

# Determine BASE = project_root (relative to YAML if provided)
set CFG_ABS [file normalize $CFG]
set CFG_DIR [file dirname  $CFG_ABS]
set BASE    [file normalize [file join $CFG_DIR ..]]  ;# default: repo root = parent of config/
if {[info exists ::env(AURIG_BUILD_PROJECT_ROOT)] && $::env(AURIG_BUILD_PROJECT_ROOT) ne ""} {
    set pr_env $::env(AURIG_BUILD_PROJECT_ROOT)
    if {[file pathtype $pr_env] eq "absolute"} {
        set BASE [file normalize $pr_env]
    } else {
        set BASE [file normalize [file join $CFG_DIR $pr_env]]
    }
}
if {[dict exists $Y project_root]} {
    set pr_yaml [dict get $Y project_root]
    if {[file pathtype $pr_yaml] eq "absolute"} {
        set BASE [file normalize $pr_yaml]
    } else {
        set BASE [file normalize [file join $CFG_DIR $pr_yaml]]
    }
}
dbg "BASE: $BASE"

# Expand file sets and constraints (resolve patterns under BASE)
lassign [::lm::yaml::expand_file_sets_rtl $Y] files_with_lib lib_includes

# Resolve HDL file patterns to real files
set files_with_lib_resolved {}
foreach trip $files_with_lib {
    lassign $trip fpat lib std
    set hits [resolve_patterns $BASE [list $fpat]]
    foreach f $hits {
        lappend files_with_lib_resolved [list $f $lib $std]
    }
}

if {[llength $files_with_lib_resolved] == 0} {
    die "No HDL files matched. Base=$BASE. Check YAML file_sets (e.g. remove 'src/common/**/*.vhd' if you don't have that folder)."
}


# Resolve include dirs (global + per-lib)
set global_includes_resolved {}
if {[dict exists $Y include_dirs_global]} {
    set global_includes_resolved [::lm::glob::resolve_dirs $BASE [dict get $Y include_dirs_global]]
}

set lib_includes_resolved {}
dict for {lib dirs} $lib_includes {
    dict set lib_includes_resolved $lib [::lm::glob::resolve_dirs $BASE $dirs]
}

# Resolve SDCs
set C [::lm::yaml::get_constraints $Y]
set sdc_pats {}
if {[dict exists $C sdc_files]} { set sdc_pats [dict get $C sdc_files] }
set sdc_files_resolved [resolve_patterns $BASE $sdc_pats]

# Optional extra QSF content
set qsf_extra {}
if {[dict exists $Y quartus] && [dict exists [dict get $Y quartus] qsf_extra]} {
    set qsf_extra [dict get [dict get $Y quartus] qsf_extra]
}

set qsf_extra_files {}
if {[dict exists $Y quartus] && [dict exists [dict get $Y quartus] qsf_extra_files]} {
    # resolve these relative to BASE
    set qsf_extra_files [resolve_patterns $BASE [dict get [dict get $Y quartus] qsf_extra_files]]
}

# --- Directories ---------------------------------------------
set repo_root [file normalize $BASE]
set build_dir [ensure_dir [file join $repo_root impl/work quartus $name]]
set rpt_dir   [ensure_dir [file join $build_dir reports]]
set HOOK_DIR  [file join $repo_root hooks]

log "Quartus project: $name"
log "Top           : $top"
log "Part          : $part"
log "Build dir     : $build_dir"

# Work in the build directory
cd $build_dir

# -----------------------
# CREATE
# -----------------------
if {$ACTION eq "create"} {
    source_if_exists [file join $HOOK_DIR pre_all.tcl]
    source_if_exists [file join $HOOK_DIR pre_create.tcl]

    set qsf_path [write_qsf $name $top $part \
        $files_with_lib_resolved \
        $lib_includes_resolved \
        $sdc_files_resolved \
        $global_includes_resolved \
        $qsf_extra \
        $qsf_extra_files]
    log "Wrote QSF: $qsf_path"

    _quartus_prepare $name $top $part

    source_if_exists [file join $HOOK_DIR post_create.tcl]
    source_if_exists [file join $HOOK_DIR post_all.tcl]
    exit
}

# Auto-generate QSF if missing (before synth/impl/bit)
if {![file exists "${name}.qsf"]} {
    log "QSF not found; auto-generating from YAML before '$ACTION'..."
    set qsf_path [write_qsf $name $top $part \
        $files_with_lib_resolved \
        $lib_includes_resolved \
        $sdc_files_resolved \
        $global_includes_resolved \
        $qsf_extra \
        $qsf_extra_files]
    _quartus_prepare $name $top $part
}

# SYNTH
if {$ACTION eq "synth"} {
    source_if_exists [file join $HOOK_DIR pre_all.tcl]
    source_if_exists [file join $HOOK_DIR pre_synth.tcl]

    _quartus_map $name

    source_if_exists [file join $HOOK_DIR post_synth.tcl]
    source_if_exists [file join $HOOK_DIR post_all.tcl]
    exit
}

# IMPL
if {$ACTION eq "impl"} {
    source_if_exists [file join $HOOK_DIR pre_all.tcl]
    source_if_exists [file join $HOOK_DIR pre_impl.tcl]

    _quartus_fit $name
    _quartus_sta $name

    source_if_exists [file join $HOOK_DIR post_impl.tcl]
    source_if_exists [file join $HOOK_DIR post_all.tcl]
    exit
}

# BIT
if {$ACTION eq "bit"} {
    source_if_exists [file join $HOOK_DIR pre_all.tcl]
    source_if_exists [file join $HOOK_DIR pre_bit.tcl]

    if {![_quartus_map_results_exist $name]} {
        log "WARN: Map results not found; auto-running map before fit/sta/asm (see issue #15)"
        _quartus_map $name
    }
    if {![_quartus_fit_results_exist $name]} {
        log "WARN: Fitter results not found; auto-running fit + sta before asm (see issue #15)"
        _quartus_fit $name
        _quartus_sta $name
    }
    _quartus_asm $name

    source_if_exists [file join $HOOK_DIR post_bit.tcl]
    source_if_exists [file join $HOOK_DIR post_all.tcl]
    exit
}


die "Unknown action: $ACTION  (expected: create | synth | impl | bit)"
