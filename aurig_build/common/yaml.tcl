# SPDX-License-Identifier: Apache-2.0
# Copyright 2024-2026 LogiMentor S.r.l.

# aurig_build/common/yaml.tcl
# -----------------------------------------------------------------------------
# YAML loader + helpers for aurig-build template (Tcl 8.5+ compatible)
#
# - Prefers tcllib 'yaml'; otherwise uses YAML-lite that supports:
#     * nested simple maps (device, board, tool, sim, env, features)
#     * block lists under file_sets (src/include) and board.{xdc_files,sdc_files}
# - Strips inline '#' comments ONLY when OUTSIDE quotes, while PRESERVING
#   LEADING INDENTATION (critical for block structure).
# - Does NOT expand globs; returns RAW patterns. build.tcl resolves them.
# -----------------------------------------------------------------------------

namespace eval ::lm::yaml {
    variable has_yaml_pkg 0
    variable tried_auto_path 0
}

# Add vendored tcllib to auto_path if present (no error if missing)
# Resolved relative to this script, not PWD.
set _vend [file normalize [file join [file dirname [file normalize [info script]]] .. vendor tcllib]]
if {[file isdirectory $_vend]} {
    lappend auto_path $_vend
    set ::lm::yaml::tried_auto_path 1
}

# Try to require tcllib yaml
if {[catch {package require yaml}]} {
    set ::lm::yaml::has_yaml_pkg 0
} else {
    set ::lm::yaml::has_yaml_pkg 1
}

# -----------------------------------------------------------------------------
# Helpers (comment stripping, indentation, list parsing)
# -----------------------------------------------------------------------------

# Strip an inline '#' comment only when it appears OUTSIDE quotes.
# IMPORTANT: DO NOT trim-left; keep leading spaces so indentation is preserved.
proc ::lm::yaml::strip_inline_comment {s} {
    # Keep leading whitespace exactly as-is
    set n [string length $s]
    set in_single 0
    set in_double 0
    for {set i 0} {$i < $n} {incr i} {
        set ch [string index $s $i]
        if {$ch eq "\"" && !$in_single} { set in_double [expr {!$in_double}]; continue }
        if {$ch eq "'"  && !$in_double} { set in_single [expr {!$in_single}]; continue }
        if {$ch eq "#"  && !$in_single && !$in_double} {
            # Cut off the comment (keep text before '#')
            set s [string range $s 0 [expr {$i-1}]]
            break
        }
    }
    # Trim RIGHT side only (keep left indentation)
    return [string trimright $s]
}

# Count leading indentation (spaces or tabs)
proc ::lm::yaml::indent_of {line} {
    set i 0
    set n [string length $line]
    while {$i < $n} {
        set ch [string index $line $i]
        if {$ch eq " " || $ch eq "\t"} {
            incr i
        } else {
            break
        }
    }
    return $i
}

# Parse an inline list like "[a, b, c]" -> {a b c}
proc ::lm::yaml::parse_inline_list {txt} {
    set txt [string trim $txt]
    set out {}
    if {$txt eq ""} { return $out }
    if {![regexp {^\[(.*)\]$} $txt -> inner]} {
        lappend out [string trim $txt]
        return $out
    }
    foreach t [split $inner ,] {
        set t [string trim $t]
        if {$t ne ""} { lappend out $t }
    }
    return $out
}

# Parse a BLOCK list. Items start with "- " and live at indent >= parent_indent
# (PyYAML's default emitter dumps block-list items at the SAME indent as the
# parent key, which is idiomatic YAML; older versions of this parser used
# `<=` and lost every such list). A non-dashed line at any indent, or a
# line less indented than `parent_indent`, terminates the list.
# Returns {list new_index}
proc ::lm::yaml::parse_block_list {lines start_i parent_indent} {
    set out {}
    set i $start_i
    set n [llength $lines]
    while {$i < $n} {
        set raw [lindex $lines $i]
        if {[string trim $raw] eq ""} { incr i; continue }
        set ind [::lm::yaml::indent_of $raw]
        if {$ind < $parent_indent} { break }
        set trimmed [string trimleft $raw]
        if {[string match "- *" $trimmed]} {
            set val [string trim [string range $trimmed 1 end]]
            if {$val ne ""} { lappend out $val }
            incr i
            continue
        } else {
            break
        }
    }
    return [list $out $i]
}

# Parse a simple nested map (key: value) starting at lines[start_i], with
# indent > parent_indent. Supports:
#   - scalar / quoted scalar    `k: value`
#   - inline list               `k: [a, b]`
#   - block list (RHS empty)    `k:\n  - a\n  - b`  or `k:\n- a` (PyYAML)
#   - NESTED MAP (RHS empty)    `k:\n  k2: v2`  — recurses
# Tracks a `key_indent` for each top-level entry so deeper indented lines
# are routed to a recursive parse_simple_map call instead of being read as
# siblings (the lite parser used to flatten `tool.synth.kind` into
# `tool.kind` here).
proc ::lm::yaml::parse_simple_map {lines start_i parent_indent} {
    set out {}
    set i $start_i
    set n [llength $lines]
    set key_indent -1
    while {$i < $n} {
        set line [lindex $lines $i]
        if {[string trim $line] eq ""} { incr i; continue }
        set ind [::lm::yaml::indent_of $line]
        if {$ind <= $parent_indent} { break }
        # Lock in the per-entry indent on the first key so any deeper-
        # indented line is treated as a sub-mapping, not a sibling.
        if {$key_indent < 0} { set key_indent $ind }
        if {$ind != $key_indent} { break }
        set trimmed [string trimleft $line]
        if {[regexp {^([A-Za-z0-9_]+):\s*(.*)$} $trimmed -> k v]} {
            set v [string trim $v]
            if {$v eq ""} {
                # Peek the next non-empty line to decide between
                # nested map vs block list vs empty.
                set peek_i [expr {$i+1}]
                while {$peek_i < $n && [string trim [lindex $lines $peek_i]] eq ""} {
                    incr peek_i
                }
                if {$peek_i < $n} {
                    set pline [lindex $lines $peek_i]
                    set pind  [::lm::yaml::indent_of $pline]
                    set ptrim [string trimleft $pline]
                    if {$pind > $ind && [string match "- *" $ptrim]} {
                        # Block list whose items live deeper than the key.
                        lassign [::lm::yaml::parse_block_list $lines [expr {$i+1}] $ind] bl ni
                        dict set out $k $bl
                        set i $ni
                        continue
                    } elseif {$pind == $ind && [string match "- *" $ptrim]} {
                        # Block list whose items live at the SAME indent as
                        # the key (PyYAML's default emission style).
                        lassign [::lm::yaml::parse_block_list $lines [expr {$i+1}] $ind] bl ni
                        dict set out $k $bl
                        set i $ni
                        continue
                    } elseif {$pind > $ind && [regexp {^[A-Za-z0-9_]+:} $ptrim]} {
                        # Nested mapping — recurse.
                        lassign [::lm::yaml::parse_simple_map $lines [expr {$i+1}] $ind] mp ni
                        dict set out $k $mp
                        set i $ni
                        continue
                    }
                }
                # Default: empty value.
                dict set out $k {}
                incr i
                continue
            } else {
                if {[string match {\[*\]} $v]} {
                    dict set out $k [::lm::yaml::parse_inline_list $v]
                } else {
                    dict set out $k $v
                }
                incr i
                continue
            }
        } else {
            break
        }
    }
    return [list $out $i]
}

# Parse ip_cores section: a block list where each item is a map with:
#   kind (required), src (required), lib (opt), generate (opt), module (opt)
proc ::lm::yaml::parse_ip_cores {lines start_i parent_indent} {
    set cores {}
    set i $start_i
    set n [llength $lines]

    while {$i < $n} {
        set line [lindex $lines $i]
        if {[string trim $line] eq ""} { incr i; continue }
        set ind [::lm::yaml::indent_of $line]
        if {$ind <= $parent_indent} { break }
        set trimmed [string trimleft $line]

        # Item: "- ..."
        if {[regexp {^- } $trimmed]} {
            set item {}
            set afterDash [string trim [string range $trimmed 1 end]]

            # Case: single "key: value" on dash line
            if {$afterDash ne "" && [regexp {^([A-Za-z0-9_]+):\s*(.*)$} $afterDash -> k v]} {
                dict set item $k [string trim $v]
            }

            # Now consume indented fields of this item
            set item_parent_indent [::lm::yaml::indent_of $line]
            incr i
            while {$i < $n} {
                set l [lindex $lines $i]
                if {[string trim $l] eq ""} { incr i; continue }
                set lind [::lm::yaml::indent_of $l]
                if {$lind <= $item_parent_indent} { break }
                set t [string trimleft $l]

                if {[regexp {^([A-Za-z0-9_]+):\s*(.*)$} $t -> k v]} {
                    dict set item $k [string trim $v]
                    incr i
                    continue
                } else {
                    break
                }
            }

            lappend cores $item
            continue
        }

        break
    }
    return [list $cores $i]
}

# Parse file_sets section with items; supports:
#  - "- lib: work" (inline k:v on dash line)
#  - "- {lib: work, vhdl_std: 2002}" (inline map on dash line)
#  - block lists for src/include
proc ::lm::yaml::parse_file_sets {lines start_i parent_indent} {
    set fs {}
    set i $start_i
    set n [llength $lines]
    set cur_sect ""

    # helper: parse "{k:v, k2:v2}" into a dict
    proc _parse_inline_map {txt} {
        set txt [string trim $txt]
        set out {}
        if {[regexp {^\{(.*)\}$} $txt -> inner]} {
            foreach pair [split $inner ,] {
                set pair [string trim $pair]
                if {$pair eq ""} { continue }
                if {[regexp {^([^:]+):\s*(.*)$} $pair -> k v]} {
                    dict set out [string trim $k] [string trim $v]
                }
            }
        }
        return $out
    }

    while {$i < $n} {
        set line [lindex $lines $i]
        if {[string trim $line] eq ""} { incr i; continue }
        set ind [::lm::yaml::indent_of $line]
        if {$ind <= $parent_indent} { break }
        set trimmed [string trimleft $line]

        # Section: "rtl:" or "sim:"
        if {[regexp {^(rtl|sim):\s*$} $trimmed -> sect]} {
            set cur_sect $sect
            dict set fs $cur_sect {}
            incr i
            continue
        }

        # Item under section: "- ..."
        if {$cur_sect ne "" && [regexp {^- } $trimmed]} {
            set item {}
            set afterDash [string trim [string range $trimmed 1 end]]

            # Case A: inline map on the dash line
            if {[string match {\{*} $afterDash]} {
                set m [_parse_inline_map $afterDash]
                dict for {k v} $m {
                    if {[lsearch -exact {src include} $k] >= 0} {
                        if {[string match {\[*\]} $v]} {
                            dict set item $k [::lm::yaml::parse_inline_list $v]
                        } else {
                            dict set item $k [list $v]
                        }
                    } else {
                        dict set item $k $v
                    }
                }
            } elseif {$afterDash ne ""} {
                # Case B: single "key: value" on dash line
                if {[regexp {^([A-Za-z0-9_]+):\s*(.*)$} $afterDash -> k v]} {
                    if {[lsearch -exact {src include} $k] >= 0} {
                        if {$v eq ""} {
                            # will be filled from block list below
                            dict set item $k {}
                        } elseif {[string match {\[*\]} $v]} {
                            dict set item $k [::lm::yaml::parse_inline_list $v]
                        } else {
                            dict set item $k [list [string trim $v]]
                        }
                    } else {
                        dict set item $k [string trim $v]
                    }
                } else {
                    # Unknown inline content; ignore it (item continues below)
                }
            }

            # Now consume the indented fields of this item
            set item_parent_indent [::lm::yaml::indent_of $line]
            incr i
            while {$i < $n} {
                set l [lindex $lines $i]
                if {[string trim $l] eq ""} { incr i; continue }
                set lind [::lm::yaml::indent_of $l]
                if {$lind <= $item_parent_indent} { break }
                set t [string trimleft $l]

                if {[regexp {^([A-Za-z0-9_]+):\s*(.*)$} $t -> k v]} {
                    set v [string trim $v]
                    if {[lsearch -exact {src include} $k] >= 0} {
                        if {$v eq ""} {
                            set next_i [expr {$i+1}]
                            lassign [::lm::yaml::parse_block_list $lines $next_i $lind] bl ni
                            dict set item $k $bl
                            set i $ni
                            continue
                        } else {
                            if {[string match {\[*\]} $v]} {
                                dict set item $k [::lm::yaml::parse_inline_list $v]
                            } else {
                                dict set item $k [list $v]
                            }
                            incr i
                            continue
                        }
                    } else {
                        dict set item $k $v
                        incr i
                        continue
                    }
                } else {
                    break
                }
            }

            # Append item to section list
            set cur [dict get $fs $cur_sect]
            lappend cur $item
            dict set fs $cur_sect $cur
            continue
        }

        break
    }
    return [list $fs $i]
}


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------

# read_yaml <path> -> Tcl dict (uses tcllib yaml or YAML-lite)
proc ::lm::yaml::read_yaml {cfgfile} {
    if {![file exists $cfgfile]} {
        error "YAML config not found: $cfgfile"
    }
    set fh [open $cfgfile r]
    set data [read $fh]
    close $fh

    if {$::lm::yaml::has_yaml_pkg} {
        if {[catch {::yaml::yaml2dict $data} Y]} {
            puts stderr "WARN: tcllib yaml failed, falling back to lite parser: $Y"
            set Y [::lm::yaml::read_yaml_lite $data]
        }
    } else {
        set Y [::lm::yaml::read_yaml_lite $data]
    }
    return $Y
}

# get_constraints <Y> -> dict with raw patterns
proc ::lm::yaml::get_constraints {Y} {
    set out [dict create xdc_files {} sdc_files {} lpf_files {} pdc_files {}]
    if {[dict exists $Y board]} {
        set bd [dict get $Y board]
        foreach k {xdc_files sdc_files lpf_files pdc_files} {
            if {[dict exists $bd $k]} { dict set out $k [dict get $bd $k] }
        }
    }
    return $out
}

# Internal helper used by both expand_file_sets and expand_file_sets_rtl.
# Walks file_sets.<section> items and appends each {pattern lib std} into
# files_with_lib, and each include dir into lib_includes (dict lib -> list).
proc ::lm::yaml::_proc_file_sets_section {fs section filesVar incVar default_std} {
    upvar 1 $filesVar files_with_lib $incVar lib_includes
    if {![dict exists $fs $section]} { return }
    set items [dict get $fs $section]
    foreach item $items {
        if {[catch {dict size $item}]} {
            puts "WARN: file_sets.$section item is not a map; skipping: $item"
            continue
        }
        if {![dict exists $item lib]} {
            puts "WARN: file_sets.$section item missing 'lib'; skipping"
            continue
        }
        if {![dict exists $item src]} {
            puts "WARN: file_sets.$section item missing 'src'; skipping"
            continue
        }

        set lib [dict get $item lib]
        set std $default_std
        if {[dict exists $item vhdl_std]} { set std [dict get $item vhdl_std] }

        foreach p [dict get $item src] {
            lappend files_with_lib [list $p $lib $std]
        }

        if {![dict exists $lib_includes $lib]} {
            dict set lib_includes $lib {}
        }
        if {[dict exists $item include]} {
            foreach d [dict get $item include] {
                dict lappend lib_includes $lib $d
            }
        }
    }
}

# Combined rtl + sim. sim.tcl callers (vivado/sim.tcl, questa/sim.tcl) use this.
proc ::lm::yaml::expand_file_sets {Y} {
    set files_with_lib {}
    set lib_includes   [dict create]
    set default_std "2008"

    if {![dict exists $Y file_sets]} {
        return [list $files_with_lib $lib_includes]
    }
    set fs [dict get $Y file_sets]

    ::lm::yaml::_proc_file_sets_section $fs rtl files_with_lib lib_includes $default_std
    ::lm::yaml::_proc_file_sets_section $fs sim files_with_lib lib_includes $default_std

    foreach lib [dict keys $lib_includes] {
        dict set lib_includes $lib [lsort -unique [dict get $lib_includes $lib]]
    }
    return [list $files_with_lib $lib_includes]
}

# Only the file_sets.rtl section — for per-vendor synthesis dispatchers
# (build.tcl). Keeps simulation-only sources (testbenches, VUnit helpers,
# etc.) out of the synth flow.
proc ::lm::yaml::expand_file_sets_rtl {Y} {
    set files_with_lib {}
    set lib_includes   [dict create]
    set default_std "2008"

    if {![dict exists $Y file_sets]} {
        return [list $files_with_lib $lib_includes]
    }
    set fs [dict get $Y file_sets]

    ::lm::yaml::_proc_file_sets_section $fs rtl files_with_lib lib_includes $default_std

    foreach lib [dict keys $lib_includes] {
        dict set lib_includes $lib [lsort -unique [dict get $lib_includes $lib]]
    }
    return [list $files_with_lib $lib_includes]
}

# get_ip_cores <Y> -> list of dicts with defaults applied
proc ::lm::yaml::get_ip_cores {Y} {
    set cores {}
    if {![dict exists $Y ip_cores]} {
        return $cores
    }
    set raw_cores [dict get $Y ip_cores]

    foreach item $raw_cores {
        # Validate dict-ness
        if {[catch {dict size $item}]} {
            puts "WARN: ip_cores item is not a map; skipping: $item"
            continue
        }
        if {![dict exists $item kind]} {
            puts "WARN: ip_cores item missing 'kind'; skipping"
            continue
        }
        if {![dict exists $item src]} {
            puts "WARN: ip_cores item missing 'src'; skipping"
            continue
        }

        # Apply defaults
        set core $item
        if {![dict exists $core lib]} {
            dict set core lib "work"
        }
        if {![dict exists $core generate]} {
            set kind [dict get $core kind]
            if {$kind eq "xci" || $kind eq "bd"} {
                dict set core generate "true"
            } else {
                dict set core generate "false"
            }
        }
        if {![dict exists $core module]} {
            dict set core module ""
        }

        lappend cores $core
    }

    return $cores
}

# -----------------------------------------------------------------------------
# YAML-lite fallback: supports our schema with block lists; preserves indentation
# -----------------------------------------------------------------------------
proc ::lm::yaml::read_yaml_lite {text} {
    # Pre-strip inline comments but KEEP LEADING WHITESPACE (indentation)
    set lines_raw [split $text \n]
    set lines {}
    foreach raw $lines_raw {
        lappend lines [::lm::yaml::strip_inline_comment $raw]
    }

    set Y {}
    set i 0
    set n [llength $lines]

    while {$i < $n} {
        set line [lindex $lines $i]
        incr i
        set trimmed_all [string trim $line]
        if {$trimmed_all eq ""} { continue }
        if {[string match "#*" $trimmed_all]} { continue }

        # Accept top-level keys regardless of small leading whitespace
        set ind [::lm::yaml::indent_of $line]
        if {$ind > 0} {
            # if accidental leading spaces/tabs before a top key, normalize to top-level
            set trimmed_all [string trimleft $line]
        }

        if {[regexp {^([A-Za-z0-9_]+):\s*(.*)$} $trimmed_all -> k v]} {
            set v [string trim $v]
            if {$v eq ""} {
                # nested block: choose parser per section
                if {$k eq "file_sets"} {
                    lassign [::lm::yaml::parse_file_sets $lines $i 0] fs ni
                    dict set Y file_sets $fs
                    set i $ni
                    continue
                } elseif {$k eq "ip_cores"} {
                    lassign [::lm::yaml::parse_ip_cores $lines $i 0] cores ni
                    dict set Y ip_cores $cores
                    set i $ni
                    continue
                } elseif {$k eq "device" || $k eq "board" || $k eq "tool" || $k eq "sim" || $k eq "env" || $k eq "features"} {
                    lassign [::lm::yaml::parse_simple_map $lines $i 0] mp ni
                    dict set Y $k $mp
                    set i $ni
                    continue
                } else {
                    # Unknown nested map; parse simple map anyway
                    lassign [::lm::yaml::parse_simple_map $lines $i 0] mp ni
                    dict set Y $k $mp
                    set i $ni
                    continue
                }
            } else {
                # scalar or inline list
                if {[string match {\[*\]} $v]} {
                    dict set Y $k [::lm::yaml::parse_inline_list $v]
                } else {
                    dict set Y $k $v
                }
                continue
            }
        }
    }

    return $Y
}
