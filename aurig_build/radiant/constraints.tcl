#!/usr/bin/env tclsh
# SPDX-License-Identifier: Apache-2.0
# Copyright 2024-2026 LogiMentor S.r.l.
#
# Board-level constraint consumption for the Radiant backend (#32).
# Radiant never read the board: section, so PDC/SDC files declared there were
# silently dropped (same class of bug as #18 on Diamond). PDC (physical) / SDC
# (timing) / XDC (cross-vendor convenience) are added via prj_add_source, which
# Radiant classifies by extension. LPF is Diamond-only; not applicable here.

proc radiant_add_board_constraints {impl Y base} {
    set added {}
    set C [::lm::yaml::get_constraints $Y]
    foreach key {xdc_files pdc_files sdc_files} {
        foreach pat [dict get $C $key] {
            if {$pat eq ""} continue
            foreach h [::lm::glob::expand $base $pat] {
                if {![file isfile $h]} continue
                set f [npath $h]
                if {[lsearch -exact $added $f] >= 0} continue
                lappend added $f
                if {[catch { prj_add_source $f } e]} {
                    puts "WARN: prj_add_source (constraint) failed for $f: $e"
                }
            }
        }
    }
    return $added
}
