# SPDX-License-Identifier: Apache-2.0
# Copyright 2024-2026 LogiMentor S.r.l.

# aurig_build/questa/sim.tcl
# -----------------------------------------------------------------------------
# Questa/ModelSim batch simulation, library-aware, YAML-driven.
#
# Usage:
#   vsim -c -do "do aurig_build/questa/sim.tcl config/project.yaml"          ;# TB from YAML
#   vsim -c -do "do aurig_build/questa/sim.tcl config/project.yaml tb_top"   ;# TB explicit
#
# YAML keys honored (examples):
#   project_name: demo_project
#   top: demo_top                     # (build-time; not used here)
#   file_sets:                        # library-aware sources (see vivado script docs)
#     rtl: [ {lib: work, src: [src/**/*.vhd], include: [src], vhdl_std: 2008}, ... ]
#     sim: [ {lib: tb,   src: [sim/testbenches/**/*.vhd], include: [sim/testbenches]} ]
#
#   include_dirs_global: [src, sim]   # optional; appended to vlog +incdir
#
#   sim:
#     top_tb: tb_demo_top             # default TB if not passed as argv
#     tb_lib: tb                      # optional; if TB not "lib.entity", prefix with this lib
#     run_time: 2 ms                  # else run -all
#     coverage: false                 # if true, pass -coverage and save UCDB on exit
#     ucdb: results/coverage.ucdb     # UCDB output path (under build dir)
#     do: sim/wave.do                 # optional wave/do script executed before run
#     generics: {WIDTH: 16, USE_FOO: true}   # VHDL generics to TB (via -g)
#     vlog_flags: ["-svinputport=compat"]    # extra vlog flags (optional)
#     vcom_flags: ["-93"]                     # extra vcom flags (optional)
#     plusargs: ["+TRACE=1"]                  # passed to vsim (e.g., for SV $test$plusargs)
#
# Hooks (optional; auto-sourced if they exist under ./hooks):
#   pre_sim.tcl, pre_compile.tcl, pre_run.tcl, post_run.tcl
#
# Outputs:
#   - Compiled libraries in local dir (vlib/vmap)
#   - transcript and vsim.wlf in ./questa_<project_name>/
#   - Optional UCDB at sim.ucdb path.
# -----------------------------------------------------------------------------

proc die {m} { puts stderr "ERROR: $m"; quit -code 2 }
proc log {m} { puts "INFO: $m" }

# Ensure a directory exists
proc ensure_dir {d} {
  if {![file isdirectory $d]} { file mkdir $d }
  return [file normalize $d]
}

# Source a hook if it exists (absolute path)
proc source_if_exists {abs_path} {
  if {[file exists $abs_path]} {
    puts "INFO: Hook -> sourcing [file normalize $abs_path]"
    uplevel #0 [list source $abs_path]
  }
}

# -----------------------
# Args & YAML
# -----------------------
set CFG [lindex $argv 0]
set TB  [lindex $argv 1]
if {$CFG eq ""} {
  die "Usage: sim.tcl <config.yaml> <tb optional>"
}

# Load YAML helpers (resolved relative to this script, not PWD)
source [file join [file dirname [file normalize [info script]]] .. common yaml.tcl]
source [file join [file dirname [file normalize [info script]]] .. common glob.tcl]

# Parse
set CFG_ABS [file normalize $CFG]
set CFG_DIR [file dirname  $CFG_ABS]
set Y [::lm::yaml::read_yaml $CFG_ABS]

# Determine PROJECT_ROOT (same policy as vivado/build.tcl and vivado/sim.tcl):
# CFG_DIR/.. is the default; AURIG_BUILD_PROJECT_ROOT env override wins; YAML
# project_root wins over env. All glob expansion uses this as the base
# so launching from any cwd resolves patterns identically.
set PROJECT_ROOT [file normalize [file join $CFG_DIR ..]]
if {[info exists ::env(AURIG_BUILD_PROJECT_ROOT)] && $::env(AURIG_BUILD_PROJECT_ROOT) ne ""} {
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

# Basic fields
foreach k {project_name} {
  if {![dict exists $Y $k]} { die "Missing key '$k' in $CFG" }
}
set name [dict get $Y project_name]

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
# If TB not qualified and YAML provides a tb_lib, prefix it
if {![string match *.* $TB] && [dict exists $Y sim] && [dict exists [dict get $Y sim] tb_lib]} {
  set TB "${[dict get [dict get $Y sim] tb_lib]}.${TB}"
}
if {$TB eq ""} { die "No testbench name supplied and sim.default_top_tb not found in YAML" }

# Run time
set run_time ""
if {[dict exists $Y sim] && [dict exists [dict get $Y sim] run_time]} {
  set run_time [dict get [dict get $Y sim] run_time]
}

# Coverage settings
set cov_enable 0
set ucdb_path ""
if {[dict exists $Y sim]} {
  if {[dict exists [dict get $Y sim] coverage]} {
    set cov_raw [dict get [dict get $Y sim] coverage]
    set cov_enable [expr {$cov_raw eq "true" || $cov_raw eq 1}]
  }
  if {[dict exists [dict get $Y sim] ucdb]} {
    set ucdb_path [dict get [dict get $Y sim] ucdb]
  }
}

# Do/wave script
set do_file ""
if {[dict exists $Y sim] && [dict exists [dict get $Y sim] do]} {
  set do_file [dict get [dict get $Y sim] do]
}

# Extra compile flags
set extra_vlog {}
set extra_vcom {}
if {[dict exists $Y sim] && [dict exists [dict get $Y sim] vlog_flags]} {
  set extra_vlog [dict get [dict get $Y sim] vlog_flags]
}
if {[dict exists $Y sim] && [dict exists [dict get $Y sim] vcom_flags]} {
  set extra_vcom [dict get [dict get $Y sim] vcom_flags]
}

# Plusargs (for SV $test$plusargs)
set plusargs {}
if {[dict exists $Y sim] && [dict exists [dict get $Y sim] plusargs]} {
  set plusargs [dict get [dict get $Y sim] plusargs]
}

# VHDL generics for TB
set tb_generics {}
if {[dict exists $Y sim] && [dict exists [dict get $Y sim] generics]} {
  set tb_generics [dict get [dict get $Y sim] generics]
}

# File sets (library-aware)
lassign [::lm::yaml::expand_file_sets $Y] files_with_lib lib_includes

# Global include dirs (optional; appended to vlog +incdir)
set include_global {}
if {[dict exists $Y include_dirs_global]} {
  set include_global [dict get $Y include_dirs_global]
}

# --- Directories ---------------------------------------------
# Glob base is PROJECT_ROOT, not pwd: ensures patterns resolve identically
# whether questa/sim.tcl is invoked from the project root or elsewhere.
set repo_root $PROJECT_ROOT

# Resolve YAML file_sets glob patterns into actual filesystem paths
# (Python pathlib.Path.glob semantics for `**`). Done before `cd $build_dir`
# below so relative patterns resolve against PROJECT_ROOT. File-only
# filter: a glob that resolves to a directory must not enter the compile
# loop — vcom/vlog reject directories.
set _files_resolved {}
foreach trip $files_with_lib {
  lassign $trip pat lib std
  if {$pat eq ""} continue
  set hits {}
  foreach h [::lm::glob::expand $repo_root $pat] {
    if {[file isfile $h]} { lappend hits $h }
  }
  if {[llength $hits] == 0} {
    puts "WARN: no file match for pattern '$pat' (base: $repo_root)"
    continue
  }
  foreach f $hits {
    lappend _files_resolved [list $f $lib $std]
  }
}
set files_with_lib $_files_resolved

# Expand include_dirs_global + per-lib include patterns. Dir-only API
# mirrors what vivado/sim.tcl now does — symmetric across simulators.
set include_global [::lm::glob::resolve_dirs $repo_root $include_global]

set _lib_includes_resolved [dict create]
dict for {lib dirs} $lib_includes {
  dict set _lib_includes_resolved $lib [::lm::glob::resolve_dirs $repo_root $dirs]
}
set lib_includes $_lib_includes_resolved

set build_dir [ensure_dir [file join $repo_root sim/work questa $name]]
set HOOK_DIR  [file join $repo_root hooks]

# Work in build dir so transcript/WLF/UCDB are kept cleanly per project
cd $build_dir

# -----------------------
# Hooks: pre_sim
# -----------------------
source_if_exists [file join $HOOK_DIR pre_sim.tcl]

# -----------------------
# Compile: create libraries and compile per lib
# -----------------------
source_if_exists [file join $HOOK_DIR pre_compile.tcl]

transcript on

# Create/map all libraries that will be used
array set lib_created {}
foreach trip $files_with_lib {
  lassign $trip f lib std
  if {![info exists lib_created($lib)]} {
    vlib $lib
    vmap $lib $lib
    set lib_created($lib) 1
  }
}

# Build include options for each library (vlog only)
# Merge per-lib includes and global includes, unique, +incdir+<path> tokens.
proc _build_incopts {lib lib_includes include_global} {
  set incdirs {}
  if {[dict exists $lib_includes $lib]} {
    foreach d [dict get $lib_includes $lib] { lappend incdirs [file normalize $d] }
  }
  foreach d $include_global { lappend incdirs [file normalize $d] }
  set incdirs [lsort -unique $incdirs]
  set opt ""
  foreach d $incdirs {
    append opt " +incdir+[file nativename $d]"
  }
  return $opt
}

# Compile sources
foreach trip $files_with_lib {
  lassign $trip f lib std
  if {![file exists $f]} { die "Source not found: $f" }

  if {[string match -nocase *.vhd* $f]} {
    # VHDL
    set vhdl_std_flag "-2008"
    if {$std eq "2002"} { set vhdl_std_flag "" }
    # Compose vcom command
    set cmd [list vcom]
    if {$vhdl_std_flag ne ""} { lappend cmd $vhdl_std_flag }
    foreach fl $extra_vcom { lappend cmd $fl }
    lappend cmd -work $lib -- [file nativename [file normalize $f]]
    if {[catch {eval $cmd} err]} {
      die "vcom failed on '[file tail $f]': $err"
    }
  } elseif {[string match -nocase *.sv $f] || [string match -nocase *.v $f]} {
    # Verilog/SV
    set incopt [_build_incopts $lib $lib_includes $include_global]
    set cmd "vlog $incopt"
    foreach fl $extra_vlog { append cmd " $fl" }
    append cmd " -work $lib -- \"[file nativename [file normalize $f]]\""
    if {[catch {eval $cmd} err]} {
      die "vlog failed on '[file tail $f]': $err"
    }
  } else {
    log "Skipping unrecognized file type: $f"
  }
}

# -----------------------
# Elaborate & run
# -----------------------

# Build vsim argument list
set vsim_args {}
# Add all libraries to the search path (-L) to ease cross-lib references
foreach lib [array names lib_created] {
  lappend vsim_args -L $lib
}
# Coverage
if {$cov_enable} {
  lappend vsim_args -coverage
}
# VHDL generics to TB: -gNAME=VALUE (repeat)
if {[llength $tb_generics] > 0} {
  dict for {k v} $tb_generics {
    # stringify booleans
    if {$v eq "true"} { set v true }
    if {$v eq "false"} { set v false }
    lappend vsim_args "-g${k}=$v"
  }
}
# Plusargs (SV)
foreach p $plusargs { lappend vsim_args $p }

# Elaborate TB
set vsim_cmd [list vsim]       ;# we'll eval it with appended args
eval lappend vsim_cmd $vsim_args
lappend vsim_cmd $TB

log "Elaborating: $vsim_cmd"
if {[catch {eval $vsim_cmd} err]} {
  die "vsim failed to elaborate '$TB': $err"
}

# Before run: wave.do or other setup
if {$do_file ne ""} {
  if {[file exists [file join $repo_root $do_file]]} {
    do [file join $repo_root $do_file]
  } elseif {[file exists $do_file]} {
    do $do_file
  } else {
    log "WARN: sim.do path not found: $do_file"
  }
}

# Hook before run (waves, logging, forces)
source_if_exists [file join $HOOK_DIR pre_run.tcl]

# Run
if {$run_time eq ""} {
  run -all
} else {
  run $run_time
}

# Coverage save (if enabled)
if {$cov_enable} {
  if {$ucdb_path eq ""} { set ucdb_path "coverage.ucdb" }
  set ucdb_abs [file normalize $ucdb_path]
  log "Saving coverage: $ucdb_abs"
  coverage save -onexit $ucdb_abs
}

# Hook after run (export WLF/UCDB, reports, etc.)
source_if_exists [file join $HOOK_DIR post_run.tcl]

# Quit simulator (the caller used -c; GUI users can omit -c and this will still exit)
quit -f
