# SPDX-License-Identifier: Apache-2.0
# Copyright 2024-2026 LogiMentor S.r.l.

# aurig_build/common/glob.tcl
# -----------------------------------------------------------------------------
# Path-glob expansion with Python pathlib.Path.glob semantics.
#
#   ::lm::glob::expand         {base pat}        -> sorted unique abs paths
#                                                  (all types: files + dirs + links)
#   ::lm::glob::resolve_files  {base patterns}   -> file-only, WARN per pattern
#   ::lm::glob::resolve_dirs   {base patterns}   -> dir-only,  WARN per pattern
#   ::lm::glob::resolve_strict {base patterns}   -> all-types, WARN per pattern
#                                                  (kept for backward compat)
#
# Rules (gitignore / Python Path.glob / bash globstar):
#   * `**`  as a standalone path component matches zero or more intermediate
#           directory components. Only the FIRST `**` per pattern is treated
#           recursively; further `**` collapse to `*` within their component.
#   * `*`   matches any sequence of characters except `/`.
#   * `?`   matches a single character except `/`.
#
# Relative `pat` is resolved against `base`; absolute `pat` is used as-is.
# Results are absolute, normalized, and lsorted unique.
# -----------------------------------------------------------------------------

namespace eval ::lm::glob {}

proc ::lm::glob::_walk_files {dir outVar {chainVar ""}} {
    upvar 1 $outVar out
    # Loop guard tracks the *current descent chain* of canonical paths.
    # A symlink that resolves to a directory already on the chain (e.g.
    # `a/loop -> a/`) is a true cycle and must be skipped. But two
    # *different* entry paths that happen to resolve to the same target
    # (e.g. `a_link -> /real`, `b_link -> /real`) are NOT a cycle —
    # both should descend, and both should yield their distinct entry
    # paths. The old implementation keyed a flat `seen` set on the
    # canonical path and pruned the second alias entirely.
    if {$chainVar eq ""} {
        set _chain {}
        ::lm::glob::_walk_files $dir out _chain
        return
    }
    upvar 1 $chainVar chain
    if {![file isdirectory $dir]} { return }
    set canon [file normalize $dir]
    # Tcl 8.5 portability: the `in` membership operator is Tcl 8.6+,
    # but Diamond 3.x's bundled tclsh declares 8.5 minimum (see
    # `aurig_build/diamond/build.tcl`'s `package require Tcl 8.5`). Use
    # lsearch -exact instead so the helper works inside every vendor
    # shell that sources it.
    if {[lsearch -exact $chain $canon] >= 0} { return }
    lappend chain $canon

    foreach f [glob -nocomplain -types {f l} -directory $dir -- *] {
        lappend out $f
    }
    # Append AND descend into directories. Both real dirs (`d`) and
    # symlinks that resolve to a directory must be followed — the
    # previous per-dispatcher implementations relied on `file isdirectory`
    # which transparently follows symlinks. Iterate over {d l} and filter
    # with `file isdirectory` to keep that behavior.
    foreach entry [glob -nocomplain -types {d l} -directory $dir -- *] {
        if {![file isdirectory $entry]} { continue }
        lappend out $entry
        ::lm::glob::_walk_files $entry out chain
    }

    # Pop our entry off the chain so sibling subtrees can still descend
    # into the same target through a different alias path.
    set chain [lreplace $chain end end]
}

proc ::lm::glob::_component_to_regex {comp} {
    # Convert a single path component (no `/`) glob into a regex fragment.
    # Supported glob constructs:
    #   *      -> [^/]*       (runs of `*` collapse; stray `**` inside a
    #                          component degenerates to `*`)
    #   ?      -> [^/]
    #   [abc]  -> [abc]       (character class; range syntax preserved)
    #   [!abc] -> [^abc]      (negation; gitignore / `string match` style)
    # Regex meta characters outside a class are escaped.
    set re ""
    set i 0
    set n [string length $comp]
    while {$i < $n} {
        set c [string index $comp $i]
        if {$c eq "*"} {
            append re {[^/]*}
            while {($i + 1) < $n && [string index $comp [expr {$i+1}]] eq "*"} {
                incr i
            }
        } elseif {$c eq "?"} {
            append re {[^/]}
        } elseif {$c eq "\["} {
            # Locate the matching `]`. Glob syntax allows `]` as the first
            # body character; we honor that by starting the search one
            # position past it.
            set body_start [expr {$i + 1}]
            set search_from $body_start
            if {$search_from < $n && [string index $comp $search_from] eq "!"} {
                incr search_from
            }
            if {$search_from < $n && [string index $comp $search_from] eq "\]"} {
                incr search_from
            }
            set end_idx [string first "\]" $comp $search_from]
            if {$end_idx < 0} {
                # Unmatched `[`: degrade to a literal bracket.
                append re "\\\["
            } else {
                set body [string range $comp $body_start [expr {$end_idx-1}]]
                set negated 0
                if {[string length $body] > 0 && [string index $body 0] eq "!"} {
                    set body [string range $body 1 end]
                    set negated 1
                }
                # In glob / `string match` / Path.glob a leading `^` in
                # the class body is literal (the class `[^ab]` matches
                # `^`, `a`, or `b`). In regex the same `^` means
                # negation. Escape it so the recursive `**` branch
                # stays aligned with the non-`**` branch and the rest
                # of the glob ecosystem.
                if {!$negated && [string length $body] > 0 && \
                        [string index $body 0] eq "^"} {
                    set body "\\^[string range $body 1 end]"
                }
                if {$negated} {
                    set body "^$body"
                }
                append re "\[${body}\]"
                set i $end_idx
            }
        } elseif {[string match {[.\\+()\]{}^$|]} $c]} {
            append re "\\$c"
        } else {
            append re $c
        }
        incr i
    }
    return $re
}

proc ::lm::glob::_tail_to_regex {tail} {
    # Convert the `/`-joined glob tail under the walk root to an anchored
    # regex. The first standalone `**` component is the recursive one.
    set DS "\x00DS\x00"
    set parts {}
    set seen 0
    foreach comp [split $tail "/"] {
        if {$comp eq "**" && !$seen} {
            lappend parts $DS
            set seen 1
        } else {
            lappend parts [::lm::glob::_component_to_regex $comp]
        }
    }
    set joined [join $parts "/"]

    if {$joined eq $DS} {
        return {^.*$}
    }
    # Leading "DS/": zero or more leading dir components.
    set lead "${DS}/"
    set ll [string length $lead]
    if {[string length $joined] >= $ll && [string range $joined 0 [expr {$ll-1}]] eq $lead} {
        set joined "(?:\[^/\]+/)*[string range $joined $ll end]"
    }
    # Trailing "/DS": optional any-depth suffix.
    set trail "/${DS}"
    set tl [string length $trail]
    set jl [string length $joined]
    if {$jl >= $tl && [string range $joined [expr {$jl-$tl}] end] eq $trail} {
        set joined "[string range $joined 0 [expr {$jl-$tl-1}]](?:/.*)?"
    }
    # Middle "/DS/": zero or more interior dir components.
    # The token length is precomputed because braced `expr` disables
    # command substitution, so [string length ...] inside `expr {...}`
    # would error at runtime as soon as a middle `**` is encountered
    # (e.g. `src/**/include/**/foo.vhd` or `a/**/b/leaf.vhd`).
    set middle "/${DS}/"
    set ml [string length $middle]
    while {[set p [string first $middle $joined]] >= 0} {
        set head [string range $joined 0 [expr {$p-1}]]
        set rest [string range $joined [expr {$p + $ml}] end]
        set joined "${head}/(?:\[^/\]+/)*${rest}"
    }
    return "^${joined}\$"
}

proc ::lm::glob::_split_static_prefix {pat} {
    # Longest leading prefix without glob metachars, split at the last `/`
    # so the prefix names a directory and the tail carries the glob. The
    # returned prefix INCLUDES the trailing `/` (so Unix-root patterns
    # like `/**/foo` keep `/` as the walk root, and Windows-drive patterns
    # like `C:/**/foo` keep `C:/`).
    set i 0
    set n [string length $pat]
    set last_slash -1
    while {$i < $n} {
        set c [string index $pat $i]
        if {$c eq "*" || $c eq "?" || $c eq "\["} { break }
        if {$c eq "/"} { set last_slash $i }
        incr i
    }
    if {$last_slash >= 0} {
        return [list [string range $pat 0 $last_slash] \
                     [string range $pat [expr {$last_slash+1}] end]]
    }
    return [list "" $pat]
}

proc ::lm::glob::_has_standalone_double_star {pat} {
    # Recursive semantics only fire for `**` that occupies an entire path
    # component (gitignore / Path.glob rule). `foo**bar` inside a single
    # component is just `*` + `*`, so it must NOT trigger the recursive
    # walk — that would scan large trees for nothing.
    foreach comp [split $pat "/"] {
        if {$comp eq "**"} { return 1 }
    }
    return 0
}

proc ::lm::glob::expand {base pat} {
    set pat [string map {"\\" "/"} $pat]

    if {[file pathtype $pat] eq "absolute"} {
        set absPat $pat
    } else {
        set absPat [string map {"\\" "/"} [file join $base $pat]]
    }

    # No standalone `**` component -> Tcl's built-in glob is sufficient.
    # `*` and `?` are handled per-component by `glob` itself, so the
    # semantics already match "does not cross `/`". A bare-`**` substring
    # inside a single component (e.g. `foo**bar`) falls through to plain
    # `glob` too and collapses to `*` per the docs. Directories are
    # included alongside files/links so the helper also serves include-dir
    # resolution (include_dirs_global, per-lib include:).
    #
    # The check runs on `pat` (the user-supplied pattern, post-slash-normalize)
    # rather than `absPat`. If `base` itself happens to contain a literal
    # `**` path component — legal on POSIX, where `*` is a valid filename
    # character — checking `absPat` would over-fire and trigger the
    # recursive walk for relative patterns that don't ask for it.
    if {![::lm::glob::_has_standalone_double_star $pat]} {
        set out {}
        foreach f [glob -nocomplain -types {f l d} -- $absPat] {
            lappend out [file normalize $f]
        }
        return [lsort -unique $out]
    }

    # `**` present: walk the static root and regex-match the glob tail.
    lassign [::lm::glob::_split_static_prefix $absPat] walk_root tail
    if {$walk_root eq ""} {
        set walk_root [file normalize $base]
    }
    if {![file isdirectory $walk_root]} {
        return {}
    }

    set re [::lm::glob::_tail_to_regex $tail]

    set files {}
    ::lm::glob::_walk_files $walk_root files

    set wr_norm [string map {"\\" "/"} [file normalize $walk_root]]
    set wr_len  [string length $wr_norm]
    # Unix root `/` and Windows drive root `C:/` both end in `/`; in every
    # other normalized path the trailing `/` is gone. Special-case the
    # `ends-with-/` form so the relpath separator check is consistent.
    set wr_root_form [expr {[string index $wr_norm end] eq "/"}]

    # Case sensitivity is aligned with Tcl's built-in `glob` so the
    # recursive `**` branch and the simple-glob branch agree on the same
    # filesystem. On Windows NTFS is case-insensitive (and Tcl's glob
    # reflects that), so we apply `-nocase` to keep `Src/**/*.vhd`
    # matching `src/foo.vhd`. On POSIX glob is case-sensitive (per the
    # default ext4 / btrfs / etc. behavior), so we stay sensitive too.
    set nocase [expr {$::tcl_platform(platform) eq "windows"}]

    set out {}
    foreach f $files {
        set fn [string map {"\\" "/"} [file normalize $f]]
        if {$wr_root_form} {
            if {[string range $fn 0 [expr {$wr_len-1}]] ne $wr_norm} { continue }
            set rel [string range $fn $wr_len end]
        } else {
            if {[string length $fn] <= $wr_len} { continue }
            if {[string index $fn $wr_len] ne "/"} { continue }
            set rel [string range $fn [expr {$wr_len+1}] end]
        }
        if {$nocase} {
            set matched [regexp -nocase -- $re $rel]
        } else {
            set matched [regexp -- $re $rel]
        }
        if {$matched} {
            lappend out [file normalize $f]
        }
    }
    return [lsort -unique $out]
}

# Resolve a list of patterns under one base. WARN-and-skip on no-match,
# matching the historical behavior of the per-vendor resolve_patterns_strict.
# Empty patterns are skipped silently: `expand $base ""` would otherwise
# resolve to `$base` itself (because [file join $base ""] == $base), so a
# stray empty entry in YAML would silently inject the project root.
proc ::lm::glob::resolve_strict {base patterns} {
    set out {}
    foreach p $patterns {
        if {$p eq ""} continue
        set got [::lm::glob::expand $base $p]
        if {[llength $got] == 0} {
            puts "WARN: no match for pattern '$p' (base: $base)"
        } else {
            lappend out {*}$got
        }
    }
    return [lsort -unique $out]
}

# File-only resolution. Use this for `file_sets[*].src`, XDC / SDC lists,
# IP cores, block-design TCL — anything that downstream feeds to
# `add_files`, vcom/vlog, or QSF SOURCE entries (none of those accept a
# directory).
proc ::lm::glob::resolve_files {base patterns} {
    set out {}
    foreach p $patterns {
        if {$p eq ""} continue
        set files {}
        foreach h [::lm::glob::expand $base $p] {
            if {[file isfile $h]} { lappend files $h }
        }
        if {[llength $files] == 0} {
            puts "WARN: no file match for pattern '$p' (base: $base)"
        } else {
            lappend out {*}$files
        }
    }
    return [lsort -unique $out]
}

# Directory-only resolution. Use this for `include_dirs_global` and the
# per-lib `include:` lists — anything that needs to land in `+incdir+`,
# `-include_dirs`, Quartus `SEARCH_PATH`, etc.
proc ::lm::glob::resolve_dirs {base patterns} {
    set out {}
    foreach p $patterns {
        if {$p eq ""} continue
        set dirs {}
        foreach h [::lm::glob::expand $base $p] {
            if {[file isdirectory $h]} { lappend dirs $h }
        }
        if {[llength $dirs] == 0} {
            puts "WARN: no directory match for pattern '$p' (base: $base)"
        } else {
            lappend out {*}$dirs
        }
    }
    return [lsort -unique $out]
}
