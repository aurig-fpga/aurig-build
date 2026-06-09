# SPDX-License-Identifier: Apache-2.0
# Copyright 2024-2026 LogiMentor S.r.l.

# aurig_build/vivado/sim.tcl
# -----------------------------------------------------------------------------
# Vivado XSIM batch simulation, library-aware, YAML-driven (project_root aware)
#
# Usage:
#   vivado -mode tcl -source aurig_build/vivado/sim.tcl -tclargs config/project.yaml
#   vivado -mode tcl -source aurig_build/vivado/sim.tcl -tclargs config/project.yaml tb_demo_top
# -----------------------------------------------------------------------------

package require Tcl 8.5
source [file join [file dirname [file normalize [info script]]] .. common yaml.tcl]
source [file join [file dirname [file normalize [info script]]] .. common glob.tcl]

# -----------------------
# Utilities
# -----------------------
proc die {m} { puts stderr "ERROR: $m"; exit 2 }
proc log {m} { puts "INFO: $m" }

proc ensure_dir {d} {
  if {![file isdirectory $d]} { file mkdir $d }
  return [file normalize $d]
}

# Optional hooks
proc source_if_exists {abs_path} {
  if {[file exists $abs_path]} {
    puts "INFO: Hook -> sourcing [file normalize $abs_path]"
    uplevel #0 [list source $abs_path]
  }
}

# Build '-generic_top "A=1 B=2"' for 'elaborate'
proc build_generic_top_arg {Y} {
  set arg {}
  if {[dict exists $Y sim] && [dict exists [dict get $Y sim] generics]} {
    set gmap [dict get [dict get $Y sim] generics]
    set pairs {}
    dict for {k v} $gmap {
      if {$v eq "true"}  { set v true }
      if {$v eq "false"} { set v false }
      lappend pairs "${k}=$v"
    }
    if {[llength $pairs] > 0} {
      set arg [list -generic_top [join $pairs " "]]
    }
  }
  return $arg
}

# derive snapshot name from TB (strip lib prefix)
proc snapshot_from_tb {tb} {
  if {[string first "." $tb] >= 0} { return [lindex [split $tb "."] end] }
  return $tb
}

# Pattern expansion delegates to aurig_build/common/glob.tcl (single source of
# truth, Python pathlib.Path.glob semantics for `**`).
proc expand_strict {base pat} {
  return [::lm::glob::expand $base $pat]
}

# -----------------------
# Args & YAML
# -----------------------
if {$argc < 1} { die "Usage: sim.tcl <config.yaml> <tb optional>" }
set CFG [lindex $argv 0]
set TB  ""
if {$argc >= 2} { set TB [lindex $argv 1] }

set CFG_ABS [file normalize $CFG]
set CFG_DIR [file dirname  $CFG_ABS]

set Y [::lm::yaml::read_yaml $CFG_ABS]

foreach k {project_name} {
  if {![dict exists $Y $k]} { die "Missing key '$k' in $CFG_ABS" }
}
set name [dict get $Y project_name]

# Determine PROJECT_ROOT (same policy as build.tcl)
set PROJECT_ROOT [file normalize [file join $CFG_DIR ..]]
if {[info exists ::env(AURIG_BUILD_PROJECT_ROOT)] && $::env(AURIG_BUILD_PROJECT_ROOT) ne ""} {
  # env may be absolute or relative to CFG_DIR
  set pr_env $::env(AURIG_BUILD_PROJECT_ROOT)
  if {[file pathtype $pr_env] eq "absolute"} {
    set PROJECT_ROOT [file normalize $pr_env]
  } else {
    set PROJECT_ROOT [file normalize [file join $CFG_DIR $pr_env]]
  }
}
if {[dict exists $Y project_root]} {
  set pr_yaml [dict get $Y project_root]
  if {[file pathtype $pr_yaml] eq "absolute"} {
    set PROJECT_ROOT [file normalize $pr_yaml]
  } else {
    set PROJECT_ROOT [file normalize [file join $CFG_DIR $pr_yaml]]
  }
}
set BASE $PROJECT_ROOT
log "Sim BASE: $BASE"

# Determine TB
# canonical key: default_top_tb; top_tb accepted as legacy fallback
if {$TB eq "" && [dict exists $Y sim]} {
  set _sim [dict get $Y sim]
  if {[dict exists $_sim default_top_tb]} {
    set TB [dict get $_sim default_top_tb]
  } elseif {[dict exists $_sim top_tb]} {
    set TB [dict get $_sim top_tb]
  }
}
if {$TB eq ""} { die "No testbench name supplied and sim.default_top_tb not found in YAML" }

# sim options
set run_time ""
set verilog_defines {}
set vlog_flags {}
set sim_tcl ""
if {[dict exists $Y sim]} {
  set S [dict get $Y sim]
  if {[dict exists $S run_time]}         { set run_time [dict get $S run_time] }
  if {[dict exists $S verilog_defines]}  { set verilog_defines [dict get $S verilog_defines] }
  if {[dict exists $S vlog_flags]}       { set vlog_flags [dict get $S vlog_flags] }
  if {[dict exists $S tcl]}              { set sim_tcl [dict get $S tcl] }
}

# ------------- Expand file sets (patterns -> files) and includes -------------
lassign [::lm::yaml::expand_file_sets $Y] files_with_lib lib_includes

# Resolve HDL files (file_sets sources are file-only by contract; a glob
# that resolves to a directory must not enter the simulator's source set).
set files_with_lib_resolved {}
set matched_files {}
foreach trip $files_with_lib {
  lassign $trip fpat lib std
  if {$fpat eq ""} continue
  foreach f [expand_strict $BASE $fpat] {
    if {![file isfile $f]} { continue }
    lappend files_with_lib_resolved [list $f $lib $std]
    lappend matched_files $f
  }
}
if {[llength $matched_files] == 0} {
  die "No HDL files matched for simulation under: $BASE"
}

# Resolve include dirs (mainly for Verilog/SV; harmless for pure VHDL).
# Uses the directory-only API directly — these patterns never address files.
set include_dirs_global {}
if {[dict exists $Y include_dirs_global]} {
  set include_dirs_global [::lm::glob::resolve_dirs $BASE [dict get $Y include_dirs_global]]
}
set lib_includes_resolved {}
dict for {lib dirs} $lib_includes {
  dict set lib_includes_resolved $lib [::lm::glob::resolve_dirs $BASE $dirs]
}

# Build 'read_verilog' options: -define and -include_dirs
proc build_verilog_options {defines include_dirs vlog_flags} {
  set opts {}
  if {[llength $defines] > 0}   { lappend opts -define $defines }
  if {[llength $include_dirs] > 0} { lappend opts -include_dirs $include_dirs }
  foreach fl $vlog_flags { lappend opts $fl }
  return $opts
}

proc include_dirs_for_file {lib lib_includes include_global} {
  set dirs {}
  if {[dict exists $lib_includes $lib]} {
    foreach d [dict get $lib_includes $lib] { lappend dirs [file normalize $d] }
  }
  foreach d $include_global { lappend dirs [file normalize $d] }
  return [lsort -unique $dirs]
}

# ---------------- Directories & hooks ----------------
set repo_root [file normalize $PROJECT_ROOT]
set build_dir [ensure_dir [file join $repo_root sim/work xsim $name]]
set HOOK_DIR  [file join $repo_root hooks]
cd $build_dir

source_if_exists [file join $HOOK_DIR pre_sim.tcl]
source_if_exists [file join $HOOK_DIR pre_compile.tcl]

# ---------------- Compile (read_* per library) ----------------
array set seen_libs {}
foreach trip $files_with_lib_resolved {
  lassign $trip f lib std
  if {![info exists seen_libs($lib)]} { set seen_libs($lib) 1 }

  if {[string match -nocase *.vhd* $f]} {
    # VHDL
    if {[string equal -nocase $std "2008"] || $std == 2008} {
      read_vhdl -vhdl2008 -library $lib $f
    } elseif {[string equal -nocase $std "2002"] || $std == 2002} {
      read_vhdl -library $lib $f
    } else {
      read_vhdl -vhdl2008 -library $lib $f
    }
  } elseif {[string match -nocase *.sv $f] || [string match -nocase *.v $f]} {
    # Verilog / SystemVerilog
    set incs [include_dirs_for_file $lib $lib_includes_resolved $include_dirs_global]
    set opts [build_verilog_options $verilog_defines $incs $vlog_flags]
    set cmd [list read_verilog]
    eval [list lappend cmd] $opts
    if {[string match -nocase *.sv $f]} { lappend cmd -sv }
    lappend cmd $f
    if {[catch {eval $cmd} err]} { die "read_verilog failed on '[file tail $f]': $err" }
  } else {
    log "Skipping unrecognized file type: $f"
  }
}
log "Libraries compiled: [array names seen_libs]"

# ---------------- Elaborate ----------------
set generic_arg [build_generic_top_arg $Y]
set elab_cmd [list elaborate $TB]
if {[llength $generic_arg] > 0} { eval [list lappend elab_cmd] $generic_arg }
foreach lib [array names seen_libs] { lappend elab_cmd -L $lib }

log "Elaborating: $elab_cmd"
if {[catch {eval $elab_cmd} err]} { die "elaborate failed: $err" }

# Optional per-sim Tcl (waves, logging, forces)
if {$sim_tcl ne ""} {
  set p1 [file join $repo_root $sim_tcl]
  if {[file exists $p1]} { source $p1 } \
  elseif {[file exists $sim_tcl]} { source $sim_tcl } \
  else { log "WARN: sim.tcl not found: $sim_tcl" }
}

source_if_exists [file join $HOOK_DIR pre_run.tcl]

# ---------------- Run ----------------
if {$run_time eq ""} {
  simulate -runall
} else {
  # Run for a fixed time via xsim snapshot if possible, else fallback
  set snap [snapshot_from_tb $TB]
  set runfile "xsim_run.tcl"
  set fh [open $runfile w]
  puts $fh "run $run_time"
  puts $fh "quit"
  close $fh
  if {[catch {exec xsim $snap -tclbatch $runfile} xerr]} {
    log "WARN: external xsim failed ($xerr) -> simulate -runall fallback"
    simulate -runall
  }
}

source_if_exists [file join $HOOK_DIR post_run.tcl]
exit
