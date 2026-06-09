# SPDX-License-Identifier: Apache-2.0
# Copyright 2024-2026 LogiMentor S.r.l.

# aurig_build/diamond/ip_cores.tcl
# -----------------------------------------------------------------------------
# IP-core handling for the Diamond non-project flow.
#
# Diamond supports three native IP formats:
#   IPX  — IPexpress-generated cores (PLLs, FIFOs, transceivers, ...)
#   LPC  — Lattice Parameterized Components (legacy)
#   EDIF — generic netlist (cross-vendor; `-work <lib>` for non-`work`)
#
# Any other `kind` from `ip_cores:` is meant for another backend
# (xci/bd for Vivado, qip for Quartus) and is silently skipped here.
# `aurig_build/run.py::validate_ip_cores` already emits a `[WARN]` to stderr
# before this script runs, so the YAML-level mismatch is loud enough.
#
# Sourced by aurig_build/diamond/build.tcl. Pulled out into its own file so
# the test suite can exercise the dispatch via a `prj_src` mock without
# sourcing the parent build.tcl (which exits if Diamond's `prj` package
# is not loaded).
#
# Requires the following to already be sourced / defined in the caller:
#   - aurig_build/common/yaml.tcl  (::lm::yaml::get_ip_cores)
#   - aurig_build/common/glob.tcl  (::lm::glob::resolve_files)
#   - proc npath {p}         (path normalizer)
#   - proc log  {m}          (info logger)
#   - proc warn {m}          (warning logger)
#   - command prj_src        (real Diamond command or test mock)
# -----------------------------------------------------------------------------

proc diamond_add_ip_cores {impl base Y} {
    set ip_cores [::lm::yaml::get_ip_cores $Y]
    foreach core $ip_cores {
        set kind    [string tolower [dict get $core kind]]
        set src_raw [dict get $core src]
        set ip_lib  [dict get $core lib]

        # Tcl 8.5 portable membership check (the `ni` / `in` operators
        # are Tcl 8.6+; Diamond 3.x ships an 8.5-minimum tclsh).
        if {[lsearch -exact {ipx lpc edf} $kind] < 0} { continue }

        # Resolve src against the project root. Glob patterns are accepted
        # (e.g. `ip/**/*.edf`); a literal file path falls through to a
        # single match. Empty match = WARN + skip, never abort.
        set src_resolved [::lm::glob::resolve_files $base [list $src_raw]]
        if {[llength $src_resolved] == 0} {
            warn "IP core src '$src_raw' resolved to no files; skipping"
            continue
        }

        foreach src_file $src_resolved {
            set src_file [npath $src_file]
            if {$kind eq "ipx"} {
                if {[catch {prj_src add -impl $impl -format IPX $src_file} e]} {
                    warn "prj_src add IPX failed for $src_file: $e"
                    continue
                }
            } elseif {$kind eq "lpc"} {
                if {[catch {prj_src add -impl $impl -format LPC $src_file} e]} {
                    warn "prj_src add LPC failed for $src_file: $e"
                    continue
                }
            } else {
                # edf: pass -work only when the YAML asked for a non-default lib.
                if {$ip_lib ne "" && $ip_lib ne "work"} {
                    set rc [catch {prj_src add -impl $impl -format EDIF -work $ip_lib $src_file} e]
                } else {
                    set rc [catch {prj_src add -impl $impl -format EDIF $src_file} e]
                }
                if {$rc} {
                    warn "prj_src add EDIF failed for $src_file: $e"
                    continue
                }
            }
            log "IP core added (kind=$kind, src=$src_file)"
        }
    }
}
