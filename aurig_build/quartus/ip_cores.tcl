# SPDX-License-Identifier: Apache-2.0
# Copyright 2024-2026 LogiMentor S.r.l.

# aurig_build/quartus/ip_cores.tcl
# -----------------------------------------------------------------------------
# IP-core registration for the Quartus non-project flow.
#
# Quartus IPs are wired into the project via QSF lines emitted by
# aurig_build/quartus/build.tcl::write_qsf. This helper writes the right
# `set_global_assignment` directives for each `ip_cores:` entry.
#
# Supported `kind` values (all others are silently skipped — the
# Python `validate_ip_cores` upstream emits a `[WARN]` for vendor
# mismatch before this script runs):
#
#   qip — Quartus IP archive (PLLs, FIFOs, transceivers, ...);
#         emitted as `set_global_assignment -name QIP_FILE <file>`.
#   edf — Generic EDIF netlist; emitted as
#         `set_global_assignment -name EDIF_FILE <file>`,
#         plus `-library <lib>` when the YAML asks for a non-`work`
#         library.
#
# Tcl 8.5 portable: uses lsearch -exact instead of the 8.6+ `in`
# membership operator (Diamond 3.x shells declare 8.5).
#
# Required helpers (already sourced / defined by the caller):
#   - ::lm::glob::resolve_files (aurig_build/common/glob.tcl)
#   - proc _qsf_rel {abs}        (path-to-QSF-relative converter
#                                  defined at the top of build.tcl)
# -----------------------------------------------------------------------------

proc quartus_emit_ip_cores {fh ip_cores base} {
    foreach core $ip_cores {
        set kind    [string tolower [dict get $core kind]]
        set src_raw [dict get $core src]

        if {[lsearch -exact {qip edf} $kind] < 0} { continue }

        # Resolve src against the project root. Glob patterns are
        # accepted (e.g. `ip/**/*.qip`); literal paths fall through as
        # a single match. Empty match = WARN + skip, never abort.
        set src_resolved [::lm::glob::resolve_files $base [list $src_raw]]
        if {[llength $src_resolved] == 0} {
            puts "WARN: IP core src '$src_raw' resolved to no files; skipping"
            continue
        }

        foreach src_file $src_resolved {
            set rel [_qsf_rel $src_file]
            if {$kind eq "qip"} {
                puts $fh "set_global_assignment -name QIP_FILE \"$rel\""
            } else {
                # edf — pass -library only when the YAML asked for a
                # non-default library.
                set ip_lib [dict get $core lib]
                if {$ip_lib ne "" && $ip_lib ne "work"} {
                    puts $fh "set_global_assignment -name EDIF_FILE \"$rel\" -library $ip_lib"
                } else {
                    puts $fh "set_global_assignment -name EDIF_FILE \"$rel\""
                }
            }
            puts "INFO: IP core added (kind=$kind, src=$rel)"
        }
    }
}
