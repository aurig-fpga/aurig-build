# SPDX-License-Identifier: Apache-2.0
# Copyright 2024-2026 LogiMentor S.r.l.

# -----------------------------------------------------------------------------
# Vivado project-mode (on-disk), library-aware, YAML-driven.
# Actions: list | project | synth | impl | bit | exporthw
#
# Usage examples:
#   vivado -mode batch -source aurig_build/vivado/build.tcl -tclargs config/project.yaml list
#   vivado -mode batch -source aurig_build/vivado/build.tcl -tclargs config/project.yaml synth
#
# Resolution policy (STRICT):
#   - PROJECT_ROOT is the ONLY base used to resolve your YAML patterns.
#   - PROJECT_ROOT default = parent of YAML folder; if YAML sets project_root:
#       * absolute path -> use as-is
#       * relative path -> resolve RELATIVE TO YAML FOLDER
#   - We DO NOT fallback-scan anywhere else.
# -----------------------------------------------------------------------------

# ==== Tcl 8.5 compatibility (Vivado sometimes embeds Tcl 8.5) ===============
if {![llength [info commands lassign]]} {
    proc lassign {list args} {
        upvar 1 {*}$args
        set i 0
        foreach v $args {
            if {$i < [llength $list]} { set $v [lindex $list $i] } else { set $v {} }
            incr i
        }
        return [lrange $list $i end]
    }
}

# ==== Utils ==================================================================
set ::BUILD_START_TS [clock seconds]

proc die {m} { puts stderr "ERROR: $m"; exit 2 }
proc log {m} { puts "INFO: $m" }

proc ensure_dir {d} {
    if {![file isdirectory $d]} { file mkdir $d }
    return [file normalize $d]
}

# Debug switches (OFF by default). Enable with YAML key debug_paths: true
set ::DBG_PATHS 0
set ::DBG_TRACE_DIRS 0
proc dbg {m} { if {$::DBG_PATHS} { puts "DBG: $m" } }
proc dbg_dir {m} { if {$::DBG_PATHS && $::DBG_TRACE_DIRS} { puts "DBG: $m" } }

# Pattern expansion delegates to aurig_build/common/glob.tcl (single source of
# truth, Python pathlib.Path.glob semantics for `**`). The `_strict` name
# is kept for callers that expect files (XDC, file_sets sources, IP,
# block-design TCL): directories are filtered out so `add_files` and the
# Vivado source manager never see a directory. Include-dir callers below
# go through `::lm::glob::resolve_dirs` directly instead.
proc resolve_patterns_strict {base patterns} {
    return [::lm::glob::resolve_files $base $patterns]
}

# Move session vivado*.jou/log from launch dir to impl/work/vivado/logs/
proc _relocate_vivado_logs {launch_dir target_dir start_ts} {
    set target [ensure_dir $target_dir]
    foreach pat {vivado*.jou vivado*.log} {
        foreach f [glob -nocomplain -directory $launch_dir -- $pat] {
            if {![file isfile $f]} { continue }
            if {[catch {set mt [file mtime $f]}] || $mt < ($start_ts - 5)} { continue }
            set dst [file join $target [file tail $f]]
            if {[catch {file rename -force $f $dst} err]} {
                puts "WARN: could not move [file tail $f] -> $target: $err"
            } else {
                puts "INFO: moved [file tail $f] -> $target"
            }
        }
    }
}

# ==== YAML helpers ===========================================================
# Expect: ../common/yaml.tcl to expose:
#   ::lm::yaml::read_yaml
#   ::lm::yaml::get_constraints      -> dict {xdc_files <list-of-patterns> sdc_files <list-of-patterns>}
#   ::lm::yaml::expand_file_sets_rtl -> files_with_lib (rtl only, list of {pattern lib std}), lib_includes (dict lib -> dir patterns)
#     NB: sim sources are intentionally NOT included here — sources_1 is for
#         synthesis. Testbenches and other sim-only files belong in
#         file_sets.sim and are consumed by sim.tcl (Questa/xsim/VUnit).
source [file join [file dirname [file normalize [info script]]] .. common yaml.tcl]
source [file join [file dirname [file normalize [info script]]] .. common glob.tcl]

# ==== Args ===================================================================
if {$argc < 2} { die "Usage: build.tcl <config.yaml> <action>" }
set CFG    [lindex $argv 0]
set ACTION [string tolower [lindex $argv 1]]

set CFG_ABS [file normalize $CFG]
set CFG_DIR [file dirname  $CFG_ABS]
set PWD_NOW [pwd]
log "Config:  $CFG_ABS"

# Load YAML
set Y [::lm::yaml::read_yaml $CFG_ABS]

# Optional debug from YAML too
if {[dict exists $Y debug_paths] && [dict get $Y debug_paths]} { set ::DBG_PATHS 1 }

# Determine PROJECT_ROOT (strict)
# default: parent of YAML folder
set PROJECT_ROOT [file dirname $CFG_DIR]
# env override (absolute recommended)
if {[info exists ::env(AURIG_BUILD_PROJECT_ROOT)] && $::env(AURIG_BUILD_PROJECT_ROOT) ne ""} {
    set pr_env $::env(AURIG_BUILD_PROJECT_ROOT)
    if {[file pathtype $pr_env] eq "absolute"} {
        set PROJECT_ROOT [file normalize $pr_env]
    } else {
        # resolve env relative to YAML folder (safer)
        set PROJECT_ROOT [file normalize [file join $CFG_DIR $pr_env]]
    }
}
# YAML override
if {[dict exists $Y project_root]} {
    set pr_yaml [dict get $Y project_root]
    # If relative, resolve RELATIVE TO YAML FOLDER
    if {[file pathtype $pr_yaml] eq "absolute"} {
        set PROJECT_ROOT [file normalize $pr_yaml]
    } else {
        set PROJECT_ROOT [file normalize [file join $CFG_DIR $pr_yaml]]
    }
}
log "Project root: $PROJECT_ROOT"

# STRICT base list: only PROJECT_ROOT
set BASE $PROJECT_ROOT

# ==== Required YAML fields ===================================================
foreach k {project_name top} {
    if {![dict exists $Y $k]} { die "Missing key '$k' in $CFG_ABS" }
}
set name [dict get $Y project_name]
set top  [dict get $Y top]

# Device part
if {![dict exists $Y device] || ![dict exists [dict get $Y device] part]} {
    die "device.part missing in YAML"
}
set part [dict get [dict get $Y device] part]

# ==== Collect patterns from YAML (NO fallback) ===============================
# Constraints
set C [::lm::yaml::get_constraints $Y]
set xdc_pats {}; if {[dict exists $C xdc_files]} { set xdc_pats [dict get $C xdc_files] }

# File sets (library-aware)
lassign [::lm::yaml::expand_file_sets_rtl $Y] files_with_lib lib_includes

# Resolve exactly what YAML says (under PROJECT_ROOT only)
set xdc_files [resolve_patterns_strict $BASE $xdc_pats]

set files_with_lib_resolved {}
set matched_files {}
foreach trip $files_with_lib {
    lassign $trip fpat lib std
    set got [resolve_patterns_strict $BASE [list $fpat]]
    foreach f $got {
        lappend files_with_lib_resolved [list $f $lib $std]
        lappend matched_files $f
    }
}

set include_dirs_global {}
if {[dict exists $Y include_dirs_global]} {
    set include_dirs_global [::lm::glob::resolve_dirs $BASE [dict get $Y include_dirs_global]]
}

set lib_includes_resolved {}
dict for {lib dirs} $lib_includes {
    dict set lib_includes_resolved $lib [::lm::glob::resolve_dirs $BASE $dirs]
}

# ==== LIST (dry run) =========================================================
if {$ACTION eq "list"} {
    puts "BASE: $BASE"
    puts "== RESOLVED MATCHES =="
    if {[llength $matched_files] == 0} {
        puts "HDL files: (none)"
    } else {
        puts "HDL files:"; foreach f [lsort -unique $matched_files] { puts "  $f" }
    }
    set inc_all {}
    foreach d $include_dirs_global { lappend inc_all $d }
    dict for {lib dirs} $lib_includes_resolved { foreach d $dirs { lappend inc_all $d } }
    if {[llength $inc_all] == 0} {
        puts "Include dirs (all): (none)"
    } else {
        puts "Include dirs (all):"; foreach d [lsort -unique $inc_all] { puts "  $d" }
    }
    if {[llength $xdc_files] == 0} {
        puts "XDC files: (none)"
    } else {
        puts "XDC files:"; foreach x $xdc_files { puts "  $x" }
    }
    if {[llength $matched_files] == 0} {
        die "No HDL files matched. Check your YAML patterns and that they live under: $BASE"
    }
    _relocate_vivado_logs $PWD_NOW [file join $BASE impl work vivado logs] $::BUILD_START_TS
    exit
}

# ==== Create/Open project ====================================================
set proj_root [ensure_dir [file join $BASE impl work vivado]]
set proj_dir  [file join $proj_root $name]
set proj_xpr  [file join $proj_dir "${name}.xpr"]

# Track whether we created a fresh project in this invocation
set CREATED_NEW 0

if {[file exists $proj_xpr]} {
    log "Opening existing project: $proj_xpr"
    open_project $proj_xpr
    # Keep YAML part authoritative
    set_property part $part [current_project]
} else {
    log "Creating project: $name @ $proj_dir (part: $part)"
    ensure_dir $proj_dir
    create_project $name $proj_dir -part $part -force
    set_property target_language VHDL [current_project]
    set CREATED_NEW 1
    # First save establishes the .xpr on disk
    catch { save_project_as $name $proj_dir }
}

# Manual compile order (prevents auto top switching)
set_property source_mgmt_mode None [current_project]

# Filesets
set src_fs [get_filesets sources_1]
set cst_fs [get_filesets constrs_1]

# Constraints
foreach xf $xdc_files {
    if {[file exists $xf]} {
        log "Add XDC: $xf"
        add_files -fileset $cst_fs $xf
    } else {
        puts "WARN: XDC not found: $xf"
    }
}

# HDL sources (strictly what YAML matched)
array set seen_libs {}
set added_files 0
foreach trip $files_with_lib_resolved {
    lassign $trip f lib std
    if {![file exists $f]} {
        puts "WARN: source not found (skipped): $f"
        continue
    }
    if {![info exists seen_libs($lib)]} { set seen_libs($lib) 1 }

    log "Add HDL: $f  (lib=$lib, std=$std)"
    add_files -fileset $src_fs $f
    incr added_files

    if {[string match -nocase *.vhd* $f]} {
        if {[string equal -nocase $std "2008"] || $std == 2008} {
            set_property file_type {VHDL 2008} [get_files [list $f]]
        } else {
            set_property file_type {VHDL} [get_files [list $f]]
        }
    }
    set_property library $lib [get_files [list $f]]
}
log "Libraries detected: [array names seen_libs]"
log "Files added to sources_1: $added_files"

# ==== IP cores (if any) ======================================================
set ip_cores [::lm::yaml::get_ip_cores $Y]
set added_ips 0
foreach core $ip_cores {
    set kind [dict get $core kind]
    set src  [dict get $core src]
    set lib  [dict get $core lib]
    set gen  [dict get $core generate]
    set mod  [dict get $core module]

    # Resolve path under PROJECT_ROOT
    set resolved [resolve_patterns_strict $BASE [list $src]]
    if {[llength $resolved] == 0} {
        puts "WARN: IP core not found: $src"
        continue
    }
    set ip_path [lindex $resolved 0]

    if {$kind eq "xci" || $kind eq "bd"} {
        # Vivado IP catalog core or block design
        if {![file exists $ip_path]} {
            puts "WARN: IP file not found, skipping: $ip_path"
            continue
        }
        log "Add IP ($kind): $ip_path"
        add_files -fileset $src_fs $ip_path
        incr added_ips

        # Generate targets if requested (default: true for xci/bd)
        if {$gen eq "true" || $gen == 1} {
            log "Generating IP targets: $ip_path"
            if {[catch {
                generate_target all [get_files $ip_path]
            } err]} {
                puts "WARN: generate_target failed for $ip_path: $err"
            }
        }
    } elseif {$kind eq "edf"} {
        # EDIF netlist (vendor-agnostic black box)
        if {![file exists $ip_path]} {
            puts "WARN: EDIF file not found, skipping: $ip_path"
            continue
        }
        log "Add EDIF netlist: $ip_path (lib=$lib)"
        read_edif $ip_path
        incr added_ips
        # Note: Vivado doesn't have direct library assignment for EDIF
        # The module name must match the entity in your VHDL wrapper
    } else {
        # Unsupported kind for Vivado (ipx, lpc, qip, etc.)
        puts "WARN: IP kind '$kind' not supported by Vivado, skipping: $src"
    }
}
if {$added_ips > 0} {
    log "IP cores added: $added_ips"
}

# Include dirs (mainly for Verilog `include)
set incs {}
foreach d $include_dirs_global { lappend incs [file normalize $d] }
dict for {lib dirs} $lib_includes_resolved { foreach d $dirs { lappend incs [file normalize $d] } }
if {[llength $incs] > 0} {
    set incs [lsort -unique $incs]
    log "Setting include_dirs on sources_1: $incs"
    set_property include_dirs $incs $src_fs
}

# (Optional) Verilog defines (if present)
set define_list {}
if {[dict exists $Y synth] && [dict exists [dict get $Y synth] verilog_defines]} {
    set define_list [dict get [dict get $Y synth] verilog_defines]
}
if {[llength $define_list] > 0} {
    log "Setting verilog_define on sources_1: [join $define_list { }]"
    set_property verilog_define [join $define_list " "] $src_fs
}

# Compile order & top
update_compile_order -fileset $src_fs

if {[llength [get_files -of_objects $src_fs]] == 0} {
    die "No source files were added. Expected matches ONLY under: $BASE"
}
set_property top $top $src_fs
log "Top set to: $top"

# ==== Optional BD (left unchanged; resolves relative to PROJECT_ROOT only) ===
set bd_enabled 0
set bd_tcl ""
if {[dict exists $Y features] && [dict exists [dict get $Y features] block_design]} {
    set b [dict get [dict get $Y features] block_design]
    if {[dict exists $b enabled]} {
        set v [dict get $b enabled]
        set bd_enabled [expr {$v eq "true" || $v eq 1}]
    }
    if {[dict exists $b tcl]} {
        set cand [resolve_patterns_strict $BASE [list [dict get $b tcl]]]
        if {[llength $cand] > 0} { set bd_tcl [lindex $cand 0] }
    }
}
if {$bd_enabled && $bd_tcl ne ""} {
    if {[file exists $bd_tcl]} {
        log "Rebuilding BD from $bd_tcl"
        source $bd_tcl
        update_compile_order -fileset $src_fs
    } else {
        puts "WARN: BD script not found: $bd_tcl"
    }
}

# Save the project ONLY if we created it now (avoid flaky save after open_run)
if {$CREATED_NEW} {
    if {[catch {save_project} err]} {
        puts "WARN: initial save_project failed ($err) — continuing."
    } else {
        puts "INFO: Project saved: $proj_xpr"
    }
}

# Threads
set max_threads 8
if {[dict exists $Y vivado] && [dict exists [dict get $Y vivado] max_threads]} {
    set max_threads [dict get $Y vivado] ; set max_threads [dict get $max_threads max_threads]
}
set_param general.maxThreads $max_threads

# Helper to wait for runs
# Robust wait that accepts Vivado's "X Complete!" statuses
proc _wait_run_or_die {run_name} {
    if {[catch {wait_on_run $run_name} err]} {
        puts stderr "ERROR: wait_on_run $run_name failed: $err"
        exit 2
    }
    # Normalize and decide
    set st_raw [get_property STATUS [get_runs $run_name]]
    set st [string tolower $st_raw]

    # Immediate failure cases
    if {[regexp {(error|fail|failed|cancel)} $st]} {
        puts stderr "ERROR: run $run_name failed (STATUS=$st_raw)"
        exit 2
    }

    # Typical success variants: "synth_design Complete!", "write_bitstream Complete!"...
    if {[regexp {complete|completed} $st]} {
        puts "INFO: run $run_name done (STATUS=$st_raw)"
        return
    }

    # Some builds momentarily return an intermediate string; re-check once
    after 1000
    set st_raw2 [get_property STATUS [get_runs $run_name]]
    set st2 [string tolower $st_raw2]
    if {[regexp {complete|completed} $st2]} {
        puts "INFO: run $run_name done (STATUS=$st_raw2)"
        return
    }

    puts stderr "ERROR: run $run_name did not reach 'Complete' (STATUS=$st_raw2)"
    exit 2
}

# ==== Actions ================================================================
if {$ACTION eq "project"} {
    puts "INFO: Project ready: [file normalize [current_project -quiet]]"
    _relocate_vivado_logs $PWD_NOW [file join $BASE impl work vivado logs] $::BUILD_START_TS
    exit
}

if {$ACTION eq "synth"} {
    log "Launching synth_1 (threads=$::max_threads)"
    reset_run synth_1
    launch_runs synth_1 -jobs $max_threads
    _wait_run_or_die synth_1

    open_run synth_1 -name netlist_1
    set outdir $proj_dir
    report_timing_summary -file [file join $outdir "${::name}_synth_timing.rpt"]
    report_utilization    -file [file join $outdir "${::name}_synth_util.rpt"]
    _relocate_vivado_logs $PWD_NOW [file join $BASE impl work vivado logs] $::BUILD_START_TS
    exit
}

if {$ACTION eq "impl"} {
    log "Launching impl_1 up to route (threads=$::max_threads)"
    reset_run impl_1
    launch_runs impl_1 -to_step route_design -jobs $max_threads
    _wait_run_or_die impl_1

    open_run impl_1
    set outdir $proj_dir
    write_checkpoint -force [file join $outdir "${::name}_post_route.dcp"]
    report_timing_summary -file [file join $outdir "${::name}_impl_timing.rpt"]
    report_route_status   -file [file join $outdir "${::name}_route_status.rpt"]
    report_utilization    -file [file join $outdir "${::name}_impl_util.rpt"]
    _relocate_vivado_logs $PWD_NOW [file join $BASE impl work vivado logs] $::BUILD_START_TS
    exit
}

if {$ACTION eq "bit"} {
    log "Launching impl_1 through write_bitstream (threads=$::max_threads)"
    reset_run impl_1
    launch_runs impl_1 -to_step write_bitstream -jobs $max_threads
    _wait_run_or_die impl_1

    set outdir $proj_dir
    set run_dir [get_property DIRECTORY [get_runs impl_1]]
    set bpath  [glob -nocomplain -directory $run_dir "*/${::top}.bit"]
    if {[llength $bpath] > 0} {
        file copy -force [lindex $bpath 0] [file join $outdir "${::name}.bit"]
        puts "INFO: Bitstream -> [file normalize [file join $outdir "${::name}.bit"]]"
    } else {
        puts "WARN: Could not locate ${::top}.bit under $run_dir."
    }

    open_run impl_1
    report_timing_summary -file [file join $outdir "${::name}_final_timing.rpt"]
    _relocate_vivado_logs $PWD_NOW [file join $BASE impl work vivado logs] $::BUILD_START_TS
    exit
}

if {$ACTION eq "exporthw"} {
    log "Launching impl_1 through write_bitstream (threads=$::max_threads)"
    reset_run impl_1
    launch_runs impl_1 -to_step write_bitstream -jobs $max_threads
    _wait_run_or_die impl_1

    set export_dir [ensure_dir "export"]
    set xsa [file join $export_dir "${::name}.xsa"]
    if {[catch {write_hw_platform -fixed -include_bit -force -file $xsa} err]} {
        die "write_hw_platform failed: $err"
    }
    puts "INFO: XSA -> [file normalize $xsa]"
    _relocate_vivado_logs $PWD_NOW [file join $BASE impl work vivado logs] $::BUILD_START_TS
    exit
}

die "Unknown action: $ACTION"
