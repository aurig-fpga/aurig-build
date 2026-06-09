# SPDX-License-Identifier: Apache-2.0
# Copyright 2024-2026 LogiMentor S.r.l.

"""Tcl-level checks for aurig_build/common/glob.tcl ::lm::glob::expand.

Asserts the acceptance table from docs/aurig-build-issues/D14 (the upstream bug
record): `**` must match zero or more intermediate directory components,
matching Python pathlib.Path.glob / gitignore / bash globstar semantics.

Skipped when tclsh is not on PATH.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
GLOB_TCL = REPO_ROOT / "aurig_build" / "common" / "glob.tcl"


pytestmark = pytest.mark.skipif(
    shutil.which("tclsh") is None,
    reason="tclsh not available on PATH",
)


def _run_tcl(script: str, tmp_dir: Path) -> subprocess.CompletedProcess:
    script_path = tmp_dir / "_run.tcl"
    script_path.write_text(script, encoding="utf-8")
    return subprocess.run(
        ["tclsh", str(script_path)],
        capture_output=True,
        text=True,
        timeout=10,
    )


def _make_tree(root: Path) -> None:
    """Build the fixture tree from the D14 acceptance section."""
    for rel in (
        "a/x.vhd",
        "a/sub/y.vhd",
        "a/sub/deep/z.vhd",
        "other/w.vhd",
        "other/note.txt",  # non-vhd, must never match `*.vhd` patterns
    ):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("-- placeholder\n", encoding="utf-8")


def _run_glob_proc(base: Path, pattern: str, tmp_dir: Path, proc_name: str) -> list[str]:
    """Common harness: invoke proc_name (expand / resolve_files / resolve_dirs)
    and return matches as posix paths relative to base.

    The pattern is wrapped in Tcl braces `{...}` rather than double quotes
    so glob bracket classes (`[xy]`, `[a-z]`, `[!x]`) are NOT interpreted
    by Tcl as command substitution. Patterns that contain literal `{` or
    `}` would not survive this scheme — none of the tests need it.
    """
    assert "{" not in pattern and "}" not in pattern, (
        "test harness can't encode literal braces in patterns"
    )
    # resolve_* take a list of patterns; expand takes a single pattern.
    if proc_name == "expand":
        call = f'::lm::glob::expand "{base.as_posix()}" {{{pattern}}}'
    else:
        call = f'::lm::glob::{proc_name} "{base.as_posix()}" [list {{{pattern}}}]'
    script = textwrap.dedent(f"""
        source [file normalize "{GLOB_TCL.as_posix()}"]
        foreach h [{call}] {{ puts $h }}
    """).strip()
    proc = _run_tcl(script, tmp_dir)
    assert proc.returncode == 0, (
        f"tclsh failed for {proc_name}({pattern!r}):\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    base_norm = base.resolve().as_posix().rstrip("/") + "/"
    out: list[str] = []
    for line in proc.stdout.splitlines():
        line = line.strip().replace("\\", "/")
        if not line or line.startswith("WARN:"):
            continue
        # Tcl `file normalize` may capitalize Windows drive letters differently.
        if line.lower().startswith(base_norm.lower()):
            out.append(line[len(base_norm):])
        else:
            out.append(line)
    return sorted(out)


def _expand(base: Path, pattern: str, tmp_dir: Path) -> list[str]:
    """Run ::lm::glob::expand (all types) — files + dirs + links."""
    return _run_glob_proc(base, pattern, tmp_dir, "expand")


def _resolve_files(base: Path, pattern: str, tmp_dir: Path) -> list[str]:
    return _run_glob_proc(base, pattern, tmp_dir, "resolve_files")


def _resolve_dirs(base: Path, pattern: str, tmp_dir: Path) -> list[str]:
    return _run_glob_proc(base, pattern, tmp_dir, "resolve_dirs")


# D14 acceptance table — each row must produce exactly the listed matches
# against the fixture tree (other/note.txt is filler that must never appear
# in `*.vhd` patterns).
@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        ("a/*.vhd",        ["a/x.vhd"]),
        ("a/**/*.vhd",     ["a/sub/deep/z.vhd", "a/sub/y.vhd", "a/x.vhd"]),
        ("a/sub/**/*.vhd", ["a/sub/deep/z.vhd", "a/sub/y.vhd"]),
        ("**/*.vhd",       ["a/sub/deep/z.vhd", "a/sub/y.vhd", "a/x.vhd", "other/w.vhd"]),
        # `a/**` matches every entry under a/ — both files AND directories,
        # so include-dir resolution (include: [src/**/include]) works.
        ("a/**", [
            "a/sub",
            "a/sub/deep",
            "a/sub/deep/z.vhd",
            "a/sub/y.vhd",
            "a/x.vhd",
        ]),
        ("a/x.vhd",        ["a/x.vhd"]),
    ],
)
def test_d14_acceptance_table(tmp_path: Path, pattern: str, expected: list[str]) -> None:
    _make_tree(tmp_path)
    assert _expand(tmp_path, pattern, tmp_path) == sorted(expected)


def test_literal_directory_pattern_is_matched(tmp_path: Path) -> None:
    """A literal directory path (no glob metachars) must resolve to that
    directory. Otherwise `include: [src/include]` in YAML — fed through the
    same resolver as file_sets — silently produces an empty list and the
    include path is lost. Regression coverage for the codex P1 review
    comment on PR #17."""
    _make_tree(tmp_path)
    (tmp_path / "src" / "include").mkdir(parents=True)

    assert _expand(tmp_path, "src/include", tmp_path) == ["src/include"]
    # Non-`**` glob on directories must also work.
    assert _expand(tmp_path, "a/sub", tmp_path) == ["a/sub"]


def test_double_star_yields_intermediate_directories(tmp_path: Path) -> None:
    """`<root>/**` and `**/include`-style patterns are expected to enumerate
    intermediate directories, not just leaf files. This is what makes the
    shared resolver usable for both file_sets[*].src and include patterns."""
    _make_tree(tmp_path)
    (tmp_path / "a" / "sub" / "include").mkdir()

    hits = _expand(tmp_path, "a/**", tmp_path)
    assert "a/sub" in hits
    assert "a/sub/deep" in hits
    assert "a/sub/include" in hits

    # Include-dir use-case: `**/include` must match every nested `include`
    # directory (and nothing that isn't named `include`).
    inc_hits = _expand(tmp_path, "**/include", tmp_path)
    assert "a/sub/include" in inc_hits
    # `a/sub/deep` is not named "include", must not be in the set.
    assert "a/sub/deep" not in inc_hits


def test_split_static_prefix_root_anchored(tmp_path: Path) -> None:
    """Whitebox: _split_static_prefix must keep the trailing `/` in the
    walk-root prefix so root-anchored absolute patterns (`/**/foo`,
    `C:/**/foo`) don't fall back to `base`. Regression coverage for the
    Copilot review comment on PR #17."""
    script = textwrap.dedent(f"""
        source [file normalize "{GLOB_TCL.as_posix()}"]
        # Probe the helper directly with a few edge-case inputs.
        foreach p {{/abs/path/**/foo.vhd /**/foo.vhd C:/**/foo.vhd rel/**/foo.vhd **/foo.vhd}} {{
            lassign [::lm::glob::_split_static_prefix $p] root tail
            puts "$p :: walk=$root :: tail=$tail"
        }}
    """).strip()
    proc = _run_tcl(script, tmp_path)
    assert proc.returncode == 0, f"tclsh failed: {proc.stderr}"
    lines = proc.stdout.strip().splitlines()
    assert "/abs/path/**/foo.vhd :: walk=/abs/path/ :: tail=**/foo.vhd" in lines
    # Unix root: the leading `/` MUST be preserved as the walk root, not
    # stripped to an empty string (which would route through the base
    # fallback).
    assert "/**/foo.vhd :: walk=/ :: tail=**/foo.vhd" in lines
    # Windows drive root: same idea — keep `C:/`.
    assert "C:/**/foo.vhd :: walk=C:/ :: tail=**/foo.vhd" in lines
    # Relative patterns with a static prefix retain the trailing slash.
    assert "rel/**/foo.vhd :: walk=rel/ :: tail=**/foo.vhd" in lines
    # Pattern that starts with a glob: empty walk root, full tail.
    assert "**/foo.vhd :: walk= :: tail=**/foo.vhd" in lines


def test_folletto_zero_subdir_repro(tmp_path: Path) -> None:
    """Repro from D14: `a/**/*.vhd` must match `a/x.vhd` even though there
    are zero intermediate directories. This is the case that broke
    Folletto Radar M0 — sim (Python Path.glob) compiled it, synth (TCL)
    silently dropped it."""
    (tmp_path / "fw" / "common").mkdir(parents=True)
    (tmp_path / "fw" / "common" / "hello_world.vhd").write_text("-- hi\n")

    hits = _expand(tmp_path, "fw/common/**/*.vhd", tmp_path)
    assert hits == ["fw/common/hello_world.vhd"], (
        f"zero-subdir case regressed: {hits}"
    )


def test_no_double_star_falls_back_to_simple_glob(tmp_path: Path) -> None:
    """A pattern with no `**` must still work (degenerate path that does
    not exercise the recursive walk)."""
    _make_tree(tmp_path)
    # File that doesn't exist must yield nothing without erroring.
    assert _expand(tmp_path, "a/does-not-exist.vhd", tmp_path) == []
    # Single-component glob still resolves.
    assert _expand(tmp_path, "a/*.vhd", tmp_path) == ["a/x.vhd"]


def test_double_star_walk_root_missing_returns_empty(tmp_path: Path) -> None:
    """Walk root that doesn't exist must return [] cleanly, not error."""
    # `a/` does not exist under tmp_path here (empty tree).
    assert _expand(tmp_path, "a/**/*.vhd", tmp_path) == []


def test_absolute_pattern_used_as_is(tmp_path: Path) -> None:
    """An absolute pattern bypasses `base` and resolves directly."""
    _make_tree(tmp_path)
    abs_pat = (tmp_path / "a" / "**" / "*.vhd").as_posix()
    hits = _expand(tmp_path, abs_pat, tmp_path)
    assert hits == sorted([
        "a/sub/deep/z.vhd",
        "a/sub/y.vhd",
        "a/x.vhd",
    ])


# Regression coverage for Copilot High #1 on PR #17: the `/**/` middle-token
# replacement in _tail_to_regex used `expr {... [string length ...]}`, which
# errors at runtime because braced `expr` disables command substitution.
# Patterns that exercise the middle branch (anything with a literal
# component AFTER the standalone `**`) used to crash; now they must work.
@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        # Literal leaf after `**`: matches at every depth.
        ("a/**/y.vhd",         ["a/sub/y.vhd"]),
        ("a/**/z.vhd",         ["a/sub/deep/z.vhd"]),
        # `**` with a non-leaf static suffix.
        ("a/**/deep/z.vhd",    ["a/sub/deep/z.vhd"]),
        # Multi-`**` per pattern: only the first `**` is recursive, the
        # second collapses to `*` (documented limitation). So this matches
        # `a/<any depth>/sub/<exactly one component>/<*.vhd>` — i.e. only
        # `a/sub/deep/z.vhd` (one component "deep" between `sub/` and the
        # `.vhd` leaf). `a/sub/y.vhd` is intentionally not matched.
        ("a/**/sub/**/*.vhd",  ["a/sub/deep/z.vhd"]),
    ],
)
def test_middle_double_star(tmp_path: Path, pattern: str, expected: list[str]) -> None:
    _make_tree(tmp_path)
    assert _expand(tmp_path, pattern, tmp_path) == sorted(expected)


def test_resolve_files_filters_directory_hits(tmp_path: Path) -> None:
    """::lm::glob::resolve_files is what every file-typed caller in the
    build dispatchers now uses (file_sets, XDC/SDC, IP, block-design TCL).
    A pattern that would also match directories must come back file-only,
    so a downstream `add_files` / vcom / vlog / QSF source list never sees
    a directory."""
    _make_tree(tmp_path)
    (tmp_path / "a" / "sub" / "include").mkdir()

    # `a/**` would yield dirs + files via ::lm::glob::expand; resolve_files
    # drops the dirs.
    hits = _resolve_files(tmp_path, "a/**", tmp_path)
    assert hits == sorted([
        "a/sub/deep/z.vhd",
        "a/sub/y.vhd",
        "a/x.vhd",
    ]), f"directory hits leaked into file resolver: {hits}"


def test_resolve_dirs_filters_file_hits(tmp_path: Path) -> None:
    """::lm::glob::resolve_dirs is what include_dirs_global / per-lib
    include callers now use. Must drop file hits so +incdir+ / SEARCH_PATH
    never see a regular file."""
    _make_tree(tmp_path)
    (tmp_path / "a" / "sub" / "include").mkdir()

    # Literal directory pattern.
    assert _resolve_dirs(tmp_path, "a/sub", tmp_path) == ["a/sub"]
    # `**/include` (real include-dir use-case) returns only the include
    # directory, not anything inside it.
    assert _resolve_dirs(tmp_path, "**/include", tmp_path) == ["a/sub/include"]


def test_resolve_files_and_dirs_disjoint_for_same_pattern(tmp_path: Path) -> None:
    """Sanity: for the same pattern that yields a mix of file and dir hits,
    the union of resolve_files + resolve_dirs equals the all-types expand,
    and their intersection is empty."""
    _make_tree(tmp_path)
    (tmp_path / "a" / "sub" / "include").mkdir()

    all_hits   = set(_expand(tmp_path, "a/**", tmp_path))
    file_hits  = set(_resolve_files(tmp_path, "a/**", tmp_path))
    dir_hits   = set(_resolve_dirs(tmp_path, "a/**", tmp_path))

    assert file_hits & dir_hits == set()
    assert file_hits | dir_hits == all_hits


def test_non_standalone_double_star_does_not_trigger_recursive_walk(tmp_path: Path) -> None:
    """`foo**bar` inside a single path component is NOT standalone `**` —
    it must fall through to plain `glob` (no recursive walk). Verifies the
    optimization for Copilot's Medium comment on PR #17: scanning a large
    tree for a pattern that doesn't actually need recursion is wasted work.

    Behavior check (whitebox would be brittle): `a/foo**bar/x.vhd` with no
    such directory under `a/` must return nothing — same as plain glob —
    and must NOT silently return `a/x.vhd` (which would happen if `**`
    were over-eagerly treated as recursive)."""
    _make_tree(tmp_path)
    (tmp_path / "a" / "foosomebar").mkdir()
    (tmp_path / "a" / "foosomebar" / "x.vhd").write_text("-- placeholder\n")

    # `foo*bar` matches `foosomebar` per plain glob; `foo**bar` should be
    # equivalent (the two `*` collapse). Both must return the file under
    # `a/foosomebar/`.
    star_hits  = _expand(tmp_path, "a/foo*bar/x.vhd", tmp_path)
    dstar_hits = _expand(tmp_path, "a/foo**bar/x.vhd", tmp_path)
    assert star_hits == ["a/foosomebar/x.vhd"]
    assert dstar_hits == star_hits, (
        f"foo**bar (non-standalone) diverged from foo*bar: "
        f"star={star_hits} dstar={dstar_hits}"
    )

    # Cross-check the helper directly: only a `**` standalone component
    # returns 1.
    script = textwrap.dedent(f"""
        source [file normalize "{GLOB_TCL.as_posix()}"]
        foreach p {{a/**/x.vhd a/foo**bar/x.vhd **/x.vhd a/**}} {{
            puts "$p :: [::lm::glob::_has_standalone_double_star $p]"
        }}
    """).strip()
    proc = _run_tcl(script, tmp_path)
    assert proc.returncode == 0, f"tclsh failed: {proc.stderr}"
    lines = proc.stdout.strip().splitlines()
    assert "a/**/x.vhd :: 1"       in lines
    assert "a/foo**bar/x.vhd :: 0" in lines
    assert "**/x.vhd :: 1"         in lines
    assert "a/** :: 1"             in lines


def _can_make_symlinks(tmp: Path) -> bool:
    try:
        target = tmp / "_sl_probe_t"
        link = tmp / "_sl_probe_l"
        target.mkdir()
        link.symlink_to(target, target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        return False
    finally:
        # Best-effort cleanup; don't fail the probe if removal errors.
        try:
            link.unlink()
        except OSError:
            pass
        try:
            target.rmdir()
        except OSError:
            pass


@pytest.mark.xfail(
    strict=False,
    reason="glob walker symlink bugs, see #1",
)
def test_walk_follows_symlink_to_directory(tmp_path: Path) -> None:
    """`_walk_files` previously iterated only over `glob -types d`, which
    excludes symlinks pointing to directories (they come back as `l`). The
    pre-PR per-dispatcher resolvers used `file isdirectory` (transparent
    over symlinks), so `**` patterns descended into linked dirs. This test
    locks that behavior in. Skipped where the filesystem can't create
    symlinks (e.g. Windows without developer mode)."""
    if not _can_make_symlinks(tmp_path):
        pytest.skip("filesystem cannot create symlinks here")

    (tmp_path / "real" / "deep").mkdir(parents=True)
    (tmp_path / "real" / "deep" / "z.vhd").write_text("-- placeholder\n")
    (tmp_path / "linked").symlink_to(tmp_path / "real", target_is_directory=True)

    hits = _expand(tmp_path, "linked/**/*.vhd", tmp_path)
    assert "linked/deep/z.vhd" in hits, (
        f"recursive walk did not follow symlink to dir: {hits}"
    )


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="`*` is not a legal character in Windows filenames",
)
def test_literal_double_star_in_base_does_not_trigger_recursion(tmp_path: Path) -> None:
    """If `base` itself contains a literal `**` *path component* (legal
    on POSIX, where `*` is a valid filename char), a relative pattern
    with no standalone `**` must NOT trigger the recursive walk — the
    recursive branch is the user's opt-in, expressed in `pat`, not
    something the layout of `base` should impose.

    Regression coverage for the Copilot Medium comment on PR #17 about
    checking `_has_standalone_double_star` on `absPat` (which folds in
    the base prefix) instead of on the user-supplied pattern."""
    # Directory literally named "**" — i.e. a `**` whole-component, not
    # a substring like `weird**dir`. This is the case the docstring
    # describes and the check exists to handle.
    weird = tmp_path / "**"
    weird.mkdir()
    (weird / "x.vhd").write_text("-- placeholder\n")
    # Populate a sub-tree below the `**`-named dir. If the recursive walk
    # over-fired (because base contains `**`), these would surface too.
    (weird / "sub").mkdir()
    (weird / "sub" / "should_not_match.vhd").write_text("-- placeholder\n")

    hits = _expand(weird, "x.vhd", tmp_path)
    assert hits == ["x.vhd"], (
        f"recursive walk over-fired for base with literal `**`: {hits}"
    )


@pytest.mark.xfail(
    strict=False,
    reason="glob walker symlink bugs, see #1",
)
def test_walk_loop_guard_handles_circular_symlinks(tmp_path: Path) -> None:
    """Circular symlink (`a/loop -> a/`) must not cause infinite recursion.
    The descent-chain guard in `_walk_files` keeps the walk finite. Skipped
    where symlinks cannot be created."""
    if not _can_make_symlinks(tmp_path):
        pytest.skip("filesystem cannot create symlinks here")

    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "x.vhd").write_text("-- placeholder\n")
    (tmp_path / "a" / "loop").symlink_to(tmp_path / "a", target_is_directory=True)

    # If the loop guard fails this test will hang and time out via the
    # _run_tcl 10s timeout — the assertion just confirms we got back the
    # expected file at least once.
    hits = _expand(tmp_path, "a/**/*.vhd", tmp_path)
    assert "a/x.vhd" in hits


@pytest.mark.xfail(
    strict=False,
    reason="glob walker symlink bugs, see #1",
)
def test_walk_does_not_prune_sibling_symlink_aliases(tmp_path: Path) -> None:
    """Two distinct symlink entries that point to the same real directory
    must BOTH yield their files — they are aliases, not a cycle. The
    earlier flat-`seen` guard collapsed them to one visited node, so
    only the first alias's contents came back and the second silently
    matched zero files. Regression coverage for the Codex P2 review
    comment on PR #23. Skipped where symlinks cannot be created."""
    if not _can_make_symlinks(tmp_path):
        pytest.skip("filesystem cannot create symlinks here")

    (tmp_path / "real").mkdir()
    (tmp_path / "real" / "x.vhd").write_text("-- placeholder\n")
    (tmp_path / "a_link").symlink_to(tmp_path / "real", target_is_directory=True)
    (tmp_path / "b_link").symlink_to(tmp_path / "real", target_is_directory=True)

    # A pattern anchored on each alias must reach the shared target's
    # contents. Before the fix, whichever alias the walk saw second
    # came back empty.
    a_hits = _expand(tmp_path, "a_link/**/*.vhd", tmp_path)
    b_hits = _expand(tmp_path, "b_link/**/*.vhd", tmp_path)
    assert "a_link/x.vhd" in a_hits, f"a_link side missed its file: {a_hits}"
    assert "b_link/x.vhd" in b_hits, f"b_link side missed its file: {b_hits}"

    # Combined `**` over the whole tree must include all three paths
    # to x.vhd (real, a_link, b_link).
    all_hits = _expand(tmp_path, "**/*.vhd", tmp_path)
    assert "real/x.vhd"   in all_hits, all_hits
    assert "a_link/x.vhd" in all_hits, all_hits
    assert "b_link/x.vhd" in all_hits, all_hits


# Glob character classes ([abc], [a-z], [!abc]) must work under the
# recursive walk path too, matching `string match` / Path.glob behavior.
# Regression coverage for the Copilot Medium comment on PR #17 about
# `_component_to_regex` previously escaping `[` and `]`.
def test_character_class_under_recursive_walk(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    # Both `a/x.vhd` (zero subdirs from a/) and `a/sub/y.vhd` (one subdir
    # from a/) match `[xy].vhd`; `a/sub/deep/z.vhd` does not.
    hits = _expand(tmp_path, "a/**/[xy].vhd", tmp_path)
    assert hits == sorted(["a/sub/y.vhd", "a/x.vhd"])


def test_character_class_range_under_recursive_walk(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    # `[w-y]` matches w, x, y (not z). Against the fixture: a/x.vhd and
    # a/sub/y.vhd qualify; a/sub/deep/z.vhd does not; other/w.vhd does too
    # via the leading `(?:[^/]+/)*` produced by **.
    hits = _expand(tmp_path, "**/[w-y].vhd", tmp_path)
    assert hits == sorted(["a/sub/y.vhd", "a/x.vhd", "other/w.vhd"])


def test_character_class_negation_under_recursive_walk(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    # `[!x]` excludes single-char names that are 'x'. Single-char .vhd
    # files: x.vhd (excluded), y.vhd, z.vhd, w.vhd → keep y/z/w.
    hits = _expand(tmp_path, "**/[!x].vhd", tmp_path)
    assert "a/x.vhd" not in hits
    assert sorted(hits) == sorted([
        "a/sub/deep/z.vhd",
        "a/sub/y.vhd",
        "other/w.vhd",
    ])


def test_character_class_literal_caret_under_recursive_walk(tmp_path: Path) -> None:
    """In glob / `string match` / Python pathlib.Path.glob, a leading
    `^` inside a character class is a *literal* caret — `[^ab]` matches
    one of `^`, `a`, `b`. In regex the same `^` means class negation,
    so without escaping the recursive `**` branch would silently negate
    the class and diverge from the non-`**` branch. Regression coverage
    for the Copilot Medium comment on PR #17."""
    (tmp_path / "a.vhd").write_text("-- placeholder\n")
    (tmp_path / "b.vhd").write_text("-- placeholder\n")
    (tmp_path / "^.vhd").write_text("-- placeholder\n")
    # `c` must NOT match: it's not in the {^, a, b} class.
    (tmp_path / "c.vhd").write_text("-- placeholder\n")

    hits = _expand(tmp_path, "**/[^ab].vhd", tmp_path)
    assert sorted(hits) == sorted(["^.vhd", "a.vhd", "b.vhd"]), (
        f"`[^ab]` did not behave as a literal-caret class: {hits}"
    )


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows-only: aligns recursive walk with the case-insensitive NTFS filesystem",
)
def test_recursive_walk_is_case_insensitive_on_windows(tmp_path: Path) -> None:
    """On Windows NTFS is case-insensitive (and Tcl's built-in `glob`
    reflects that). The recursive `**` branch uses `regexp`, which is
    case-sensitive by default — without `-nocase` it would silently
    diverge from the non-`**` branch on the same filesystem and miss
    files like `src/x.vhd` when the YAML pattern reads `Src/**/*.vhd`.
    Regression coverage for the Copilot Medium comment on PR #17."""
    (tmp_path / "src" / "sub").mkdir(parents=True)
    (tmp_path / "src" / "sub" / "x.vhd").write_text("-- placeholder\n")

    # Upper-case `SRC/` in the pattern; lower-case `src/` on disk.
    hits = _expand(tmp_path, "SRC/**/*.vhd", tmp_path)
    assert any(h.lower().endswith("x.vhd") for h in hits), (
        f"recursive walk lost case-insensitive match on Windows: {hits}"
    )


def test_resolvers_skip_empty_patterns(tmp_path: Path) -> None:
    """An empty pattern fed to ::lm::glob::expand resolves to `$base`
    itself (because [file join $base ""] == $base), so a stray empty
    entry in YAML include_dirs_global would silently inject the entire
    project root as an include. The shared resolvers must skip empty
    patterns. Regression coverage for the Copilot Medium comment on
    PR #17."""
    _make_tree(tmp_path)
    (tmp_path / "src" / "include").mkdir(parents=True)

    # Baseline via the wrapper: a single non-empty dir pattern resolves
    # to that directory (no surprise, but locks in the happy path).
    hits = _run_glob_proc(tmp_path, "src/include", tmp_path, "resolve_dirs")
    assert hits == ["src/include"]

    # Actual empty-pattern regression coverage: pass an explicit Tcl list
    # containing empty strings alongside the real entry, and verify the
    # base never leaks into the result set.
    script = textwrap.dedent(f"""
        source [file normalize "{GLOB_TCL.as_posix()}"]
        foreach h [::lm::glob::resolve_dirs "{tmp_path.as_posix()}" \
                       [list "" "src/include" ""]] {{
            puts $h
        }}
    """).strip()
    proc = _run_tcl(script, tmp_path)
    assert proc.returncode == 0, f"tclsh failed: {proc.stderr}"
    lines = [l.strip() for l in proc.stdout.splitlines() if l.strip()]
    base_norm = tmp_path.resolve().as_posix().rstrip("/")
    assert base_norm.lower() not in [l.lower() for l in lines], (
        f"empty pattern leaked the base directory: {lines}"
    )
    # And of course the real entry must still resolve.
    assert any(l.lower().endswith("src/include") for l in lines), lines
