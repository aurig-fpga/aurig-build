# SPDX-License-Identifier: Apache-2.0
# Copyright 2024-2026 LogiMentor S.r.l.

"""
Unit tests for prepare_env() version enforcement behavior.

Covers:
  - Strict mode (require_exact_versions: true) with version mismatch -> exit(2)
  - Permissive mode (require_exact_versions: false) with version mismatch -> warning
  - Matching version -> no exit, no mismatch warning
  - No configured version -> no version enforcement at all

All tests mock _which (so the fake executable is "found") and
_vivado_version_via_tcl (so no real tool is invoked).  Vivado is used
as the simplest single-mock path; Diamond/Questa/Quartus each need
different version-getter mocks.
"""

import pytest
from unittest.mock import patch, MagicMock
from aurig_build.run import prepare_env


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_EXE = "/fake/vivado"


def _vivado_synth_cfg(version: str = "2023.1", require_exact: bool = True) -> dict:
    """Minimal cfg dict for a Vivado synth role."""
    return {
        "require_exact_versions": require_exact,
        "tool": {
            "synth": {
                "kind": "vivado",
                "version": version,
            }
        },
    }


def _run_prepare_env(cfg, *, detected_version: str, need_synth=True, need_sim=False):
    """
    Call prepare_env with _which always returning FAKE_EXE and
    _vivado_version_via_tcl returning detected_version.
    """
    with patch("aurig_build.run._which", return_value=FAKE_EXE), \
         patch("aurig_build.run._vivado_version_via_tcl", return_value=detected_version):
        return prepare_env(cfg, need_synth=need_synth, need_sim=need_sim)


# ---------------------------------------------------------------------------
# 1. Strict mode: version mismatch -> SystemExit(2)
# ---------------------------------------------------------------------------

def test_strict_mismatch_exits_with_code_2():
    """strict=True, detected != configured -> sys.exit(2)."""
    cfg = _vivado_synth_cfg(version="2023.1", require_exact=True)

    with pytest.raises(SystemExit) as exc_info:
        _run_prepare_env(cfg, detected_version="2024.2")

    assert exc_info.value.code == 2


def test_strict_mismatch_prints_error_to_stderr(capsys):
    """strict=True, mismatch -> [ERROR] message on stderr before exit."""
    cfg = _vivado_synth_cfg(version="2023.1", require_exact=True)

    with pytest.raises(SystemExit):
        _run_prepare_env(cfg, detected_version="2024.2")

    captured = capsys.readouterr()
    assert "[ERROR]" in captured.err
    assert "vivado" in captured.err.lower()
    assert "2023.1" in captured.err
    assert "2024.2" in captured.err


# ---------------------------------------------------------------------------
# 2. Permissive mode: version mismatch -> warning, execution continues
# ---------------------------------------------------------------------------

def test_permissive_mismatch_does_not_exit(capsys):
    """strict=False, detected != configured -> returns env dict, no SystemExit."""
    cfg = _vivado_synth_cfg(version="2023.1", require_exact=False)

    result = _run_prepare_env(cfg, detected_version="2024.2")

    assert isinstance(result, dict)


def test_permissive_mismatch_emits_warn_to_stdout(capsys):
    """strict=False, mismatch -> [WARN] message on stdout."""
    cfg = _vivado_synth_cfg(version="2023.1", require_exact=False)

    _run_prepare_env(cfg, detected_version="2024.2")

    captured = capsys.readouterr()
    assert "[WARN]" in captured.out
    assert "vivado" in captured.out.lower()
    assert "2023.1" in captured.out
    assert "2024.2" in captured.out


def test_permissive_mismatch_no_error_on_stderr(capsys):
    """strict=False, mismatch -> nothing on stderr (no [ERROR] line)."""
    cfg = _vivado_synth_cfg(version="2023.1", require_exact=False)

    _run_prepare_env(cfg, detected_version="2024.2")

    captured = capsys.readouterr()
    assert "[ERROR]" not in captured.err


# ---------------------------------------------------------------------------
# 3. Matching version: no exit, no mismatch warning
# ---------------------------------------------------------------------------

def test_matching_version_returns_env(capsys):
    """Configured == detected -> returns env dict without exiting."""
    cfg = _vivado_synth_cfg(version="2023.1", require_exact=True)

    result = _run_prepare_env(cfg, detected_version="2023.1")

    assert isinstance(result, dict)


def test_matching_version_no_mismatch_output(capsys):
    """Configured == detected -> no WARN or ERROR about version mismatch."""
    cfg = _vivado_synth_cfg(version="2023.1", require_exact=True)

    _run_prepare_env(cfg, detected_version="2023.1")

    captured = capsys.readouterr()
    assert "mismatch" not in captured.out.lower()
    assert "mismatch" not in captured.err.lower()


def test_matching_version_no_version_getter_after_match(capsys):
    """Sanity: version getter is called exactly once when version is pinned."""
    cfg = _vivado_synth_cfg(version="2023.1", require_exact=True)

    with patch("aurig_build.run._which", return_value=FAKE_EXE), \
         patch("aurig_build.run._vivado_version_via_tcl", return_value="2023.1") as mock_ver:
        prepare_env(cfg, need_synth=True, need_sim=False)

    mock_ver.assert_called_once()


# ---------------------------------------------------------------------------
# 4. No configured version: no mismatch enforcement
# ---------------------------------------------------------------------------

def test_no_version_field_no_exit(capsys):
    """Empty version string -> version getter never called, no exit."""
    cfg = {
        "require_exact_versions": True,
        "tool": {
            "synth": {
                "kind": "vivado",
                "version": "",
            }
        },
    }

    with patch("aurig_build.run._which", return_value=FAKE_EXE), \
         patch("aurig_build.run._vivado_version_via_tcl") as mock_ver:
        result = prepare_env(cfg, need_synth=True, need_sim=False)

    assert isinstance(result, dict)
    mock_ver.assert_not_called()


def test_missing_version_key_no_exit():
    """Absent version key -> treated as empty, no enforcement."""
    cfg = {
        "require_exact_versions": True,
        "tool": {
            "synth": {
                "kind": "vivado",
                # no 'version' key at all
            }
        },
    }

    with patch("aurig_build.run._which", return_value=FAKE_EXE), \
         patch("aurig_build.run._vivado_version_via_tcl") as mock_ver:
        result = prepare_env(cfg, need_synth=True, need_sim=False)

    assert isinstance(result, dict)
    mock_ver.assert_not_called()


def test_no_version_no_warning_output(capsys):
    """Empty version -> no WARN or ERROR lines in any output."""
    cfg = {
        "require_exact_versions": True,
        "tool": {
            "synth": {
                "kind": "vivado",
                "version": "",
            }
        },
    }

    with patch("aurig_build.run._which", return_value=FAKE_EXE), \
         patch("aurig_build.run._vivado_version_via_tcl", return_value="2024.2"):
        prepare_env(cfg, need_synth=True, need_sim=False)

    captured = capsys.readouterr()
    assert "mismatch" not in captured.out.lower()
    assert "mismatch" not in captured.err.lower()


# ---------------------------------------------------------------------------
# 5. Radiant version-getter is wired into prepare_env.get_version()
# ---------------------------------------------------------------------------

def _radiant_synth_cfg(version: str = "2024.1", exe: str = "radiantc", require_exact: bool = True) -> dict:
    """Minimal cfg dict for a Radiant synth role.

    Defaults still set `tool.synth.exe` explicitly even though ensure()
    no longer requires it (since the _DEFAULT_EXE_FOR_KIND map covers
    radiant -> radiantc). Keeping it explicit:
      - matches what users typically write in YAML;
      - lets individual tests parametrise the exe (e.g. absolute path,
        custom name) without touching the cfg builder;
      - keeps the assertions decoupled from the default-exe map, so a
        regression in the map is caught by a separate, dedicated test.
    """
    return {
        "require_exact_versions": require_exact,
        "tool": {
            "synth": {
                "kind": "radiant",
                "exe": exe,
                "version": version,
            }
        },
    }


def _which_only(target_name: str, resolved: str):
    """Return a _which mock that resolves only the expected exe basename.

    Other lookups return None so the test fails loudly if ensure() asks
    for a different name than expected — e.g. a regression in the
    _DEFAULT_EXE_FOR_KIND map (radiant -> radiantc), or if the
    configured `tool.synth.exe` ever stops winning over the default.
    """
    def _mock(name, *_args, **_kwargs):
        return resolved if name == target_name else None
    return _mock


def test_radiant_strict_matching_version_does_not_exit():
    """
    Regression for PR #11 review: previously get_version() had no `radiant`
    branch, so prepare_env() hard-failed under the default strict mode when
    `tool.synth.kind: radiant` + `tool.synth.version` were set. With the
    branch in place, a matching detected version must NOT exit.
    """
    cfg = _radiant_synth_cfg(version="2024.1")
    resolved = "/fake/bin/radiantc"

    with patch("aurig_build.run._which", side_effect=_which_only("radiantc", resolved)), \
         patch("aurig_build.run._radiant_version_guess", return_value="2024.1"):
        # Should return without raising; if get_version() were missing the
        # radiant branch this would sys.exit(2) on the version mismatch.
        prepare_env(cfg, need_synth=True, need_sim=False)


def test_radiant_strict_mismatch_exits_with_code_2():
    """Same as the Vivado strict-mismatch case, but for radiant."""
    cfg = _radiant_synth_cfg(version="2024.1")
    resolved = "/fake/bin/radiantc"

    with patch("aurig_build.run._which", side_effect=_which_only("radiantc", resolved)), \
         patch("aurig_build.run._radiant_version_guess", return_value="2023.2"):
        with pytest.raises(SystemExit) as exc_info:
            prepare_env(cfg, need_synth=True, need_sim=False)

    assert exc_info.value.code == 2


def test_radiant_version_guess_receives_resolved_exe_path():
    """
    Regression for PR #11 review (Copilot, medium): get_version() must
    pass the resolved `cur_exe_path` down to _radiant_version_guess so the
    version probe runs against the actual configured tool.synth.exe, not
    against a hardcoded "radiantc" basename. Verifies the propagation.
    """
    cfg = _radiant_synth_cfg(version="2024.1", exe="radiantc")
    resolved = "/opt/lattice/radiant_2024.1/bin/lin64/radiantc"

    with patch("aurig_build.run._which", side_effect=_which_only("radiantc", resolved)), \
         patch("aurig_build.run._radiant_version_guess", return_value="2024.1") as mock_vg:
        prepare_env(cfg, need_synth=True, need_sim=False)

    mock_vg.assert_called_once()
    # _radiant_version_guess(env, exe_override) — second arg must be the
    # resolved exe path returned by _which, not the kind or basename.
    args, kwargs = mock_vg.call_args
    exe_override = args[1] if len(args) > 1 else kwargs.get("exe_override", "")
    assert exe_override == resolved, \
        f"Expected resolved exe path '{resolved}' propagated to _radiant_version_guess, got '{exe_override}'"


@pytest.mark.parametrize("quote_style", ['"', "'"])
def test_ensure_sanitizes_quoted_tool_exe(quote_style):
    """
    PR #16 round 8 follow-up: tool.<role>.exe goes through
    _sanitize_exe_value in role_cfg (mirrors what bin_dir/env_script
    already get and what _resolve_vendor_exe does in the dispatchers).
    Without this, a quoted absolute path landed verbatim as cur_exe,
    `_which('"/path/to/vivado"', env)` failed, and the basename fallback
    saw `vivado"` (with a trailing quote) — both useless.
    """
    import os
    # Use a portable absolute path the OS understands natively, so we can
    # compare via os.path.normpath without baking in / vs \ differences.
    raw_abs = os.path.normpath("/opt/Xilinx/Vivado/2023.1/bin/vivado")
    quoted_exe = f"{quote_style}{raw_abs}{quote_style}"

    cfg = {
        "require_exact_versions": False,
        "tool": {
            "synth": {
                "kind": "vivado",
                "exe": quoted_exe,
            }
        },
    }

    queried = []
    def _which_mock(name, *_args, **_kwargs):
        queried.append(name)
        # Pretend the unquoted-and-normalized path is found, the raw quoted
        # one is not.
        return raw_abs if name == raw_abs else None

    with patch("aurig_build.run._which", side_effect=_which_mock), \
         patch("aurig_build.run._vivado_version_via_tcl", return_value=""):
        prepare_env(cfg, need_synth=True, need_sim=False)

    # The lookup must have used the UNQUOTED+normpath'd path (sanitized),
    # never the raw quoted string.
    assert raw_abs in queried, \
        f"prepare_env did not sanitize quoted exe; saw queries: {queried}"
    assert quoted_exe not in queried, \
        f"prepare_env used the raw quoted value; saw queries: {queried}"


@pytest.mark.parametrize("nonsense_exe", ['""', "''", '"   "'])
def test_ensure_quotes_only_exe_falls_back_to_default(nonsense_exe):
    """
    _sanitize_exe_value collapses quotes-only / whitespace-only values to
    "". role_cfg returns that "" so ensure() falls back to
    _DEFAULT_EXE_FOR_KIND, instead of trying to _which(".") or _which('""').
    """
    cfg = {
        "require_exact_versions": False,
        "tool": {
            "synth": {
                "kind": "vivado",
                "exe": nonsense_exe,
            }
        },
    }

    queried = []
    def _which_mock(name, *_args, **_kwargs):
        queried.append(name)
        return None

    with patch("aurig_build.run._which", side_effect=_which_mock), \
         patch("aurig_build.run._vivado_version_via_tcl", return_value=""):
        # No version pin and _which returns None -> the final exit branch
        # in ensure() will fire, but only AFTER fallback to the kind's
        # default exe. Catch the SystemExit so we can inspect the queries.
        with pytest.raises(SystemExit):
            prepare_env(cfg, need_synth=True, need_sim=False)

    # The "." path / quotes-only literal must NEVER have reached _which.
    assert "." not in queried, \
        f"ensure() looked up '.' from sanitize-collapsed exe; saw: {queried}"
    assert nonsense_exe not in queried, \
        f"ensure() looked up the raw nonsense exe; saw: {queried}"
    # And it must have queried the vendor default.
    assert "vivado" in queried, \
        f"ensure() did not fall back to the vivado default; saw: {queried}"


@pytest.mark.parametrize("kind, expected_exe", [
    ("vivado",  "vivado"),
    ("quartus", "quartus_sh"),
    ("questa",  "vsim"),
    ("diamond", "pnmainc"),
    ("radiant", "radiantc"),
])
def test_ensure_uses_default_exe_when_tool_exe_omitted(kind, expected_exe):
    """
    Regression for PR #11 review (Copilot, medium): when tool.<role>.exe is
    omitted, ensure() previously fell back to the kind string itself
    (`cur_exe = exe or kind`). That works for Vivado (binary IS 'vivado') but
    is wrong for every other supported backend (quartus_sh / vsim / pnmainc /
    radiantc). Now it goes through the _DEFAULT_EXE_FOR_KIND map so omitting
    `exe` in YAML matches what the per-vendor build dispatchers default to.
    """
    cfg = {
        "require_exact_versions": False,    # avoid version-getter complications
        "tool": {
            "synth": {
                "kind": kind,
                # exe deliberately omitted
            }
        },
    }

    queried = []
    def _which_mock(name, *_args, **_kwargs):
        queried.append(name)
        return f"/fake/bin/{name}" if name == expected_exe else None

    # Stub every version-getter the kind might reach; we only care about the
    # exe lookup here, not version semantics.
    with patch("aurig_build.run._which", side_effect=_which_mock), \
         patch("aurig_build.run._vivado_version_via_tcl", return_value=""), \
         patch("aurig_build.run._radiant_version_guess", return_value=""), \
         patch("aurig_build.run._diamond_version_guess", return_value=""), \
         patch("aurig_build.run._running_version", return_value=""):
        prepare_env(cfg, need_synth=True, need_sim=False)

    assert expected_exe in queried, (
        f"ensure() did not look up the expected default exe for kind={kind}: "
        f"got queries {queried!r}, wanted to see {expected_exe!r}"
    )


def test_vunit_sim_kind_short_circuits_ensure():
    """
    §6 #1 / §5 #13: sim_vunit runs via sys.executable and never needs a vendor
    exe. ensure() must short-circuit for `kind: vunit` so the user can run
    `python -m aurig_build.run sim --sim vunit` on a python3-only system without
    hitting "_which('vunit') -> None -> sys.exit(2)".
    """
    cfg = {
        "require_exact_versions": True,
        "tool": {
            "sim": {
                "kind": "vunit",
                # Deliberately no exe; previously this would have crashed.
            }
        },
    }

    # _which returns None for everything: if ensure() did NOT short-circuit
    # for vunit, it would fail to resolve any binary and exit(2).
    with patch("aurig_build.run._which", return_value=None):
        # Must return normally, not raise SystemExit.
        prepare_env(cfg, need_synth=False, need_sim=True)


def test_vunit_sim_kind_still_loads_env_script_and_bin_dir():
    """
    Codex P2 (PR #12): the VUnit short-circuit must NOT skip env bootstrap.
    sim_vunit runs via sys.executable but may still depend on
    tool.sim.env_script / tool.sim.bin_dir to source license variables or
    expose simulator binaries VUnit itself shells out to. Only the exe /
    version check is skipped.
    """
    cfg = {
        "require_exact_versions": True,
        "tool": {
            "sim": {
                "kind": "vunit",
                # Use OS-keyed dicts so the role_cfg() helper picks the right one.
                "env_script": {
                    "windows": r"C:\fake\setup.bat",
                    "linux": "/fake/setup.sh",
                },
                "bin_dir": {
                    "windows": r"C:\fake\sim\bin",
                    "linux": "/fake/sim/bin",
                },
            }
        },
    }

    with patch("aurig_build.run._which", return_value=None), \
         patch("aurig_build.run._source_script_into_env",
               side_effect=lambda env, _script: env) as mock_source:
        prepare_env(cfg, need_synth=False, need_sim=True)

    # load_env() must have been invoked; it calls _source_script_into_env
    # for env_script and then prepends bin_dir to PATH.
    assert mock_source.call_count == 1, \
        "VUnit short-circuit skipped load_env() — env_script not sourced"


def test_ensure_writes_basename_fallback_back_into_cfg():
    """
    §6 #12 / §5 #12: when ensure()'s basename fallback recovers from a bogus
    absolute tool.synth.exe, the resolved path must be written back into
    cfg['tool']['synth']['exe'] so every downstream dispatcher sees the
    corrected value (instead of re-running its own resolution against the
    bogus path).
    """
    cfg = _radiant_synth_cfg(version="2024.1", exe="/nonexistent/dir/radiantc")
    discovered = "/usr/local/bin/radiantc"

    def _mock_which(name, *_args, **_kwargs):
        if name == "/nonexistent/dir/radiantc":
            return None
        if name == "radiantc":
            return discovered
        return None

    with patch("aurig_build.run._which", side_effect=_mock_which), \
         patch("aurig_build.run._radiant_version_guess", return_value="2024.1"):
        prepare_env(cfg, need_synth=True, need_sim=False)

    # cfg must have been mutated to point at the resolved exe.
    assert cfg["tool"]["synth"]["exe"] == discovered, \
        f"Expected cfg.tool.synth.exe to be rewritten to {discovered!r}, got {cfg['tool']['synth']['exe']!r}"


def test_ensure_falls_back_to_basename_when_configured_abs_exe_missing(capsys):
    """
    Regression for PR #11 review (Copilot, medium): when tool.synth.exe is
    an absolute path that does not resolve, prepare_env().ensure() must fall
    back to a PATH lookup by basename — otherwise the user hits a fatal
    "not found" before the per-vendor build dispatcher (which already does
    this split) ever gets a chance to recover.
    """
    import os
    # role_cfg now sanitizes exe (PR #16): os.path.normpath turns `/` into
    # `\` on Windows. Build the expected sanitized form so the assertion is
    # cross-platform.
    bogus_abs = os.path.normpath("/nonexistent/dir/radiantc")
    cfg = _radiant_synth_cfg(version="2024.1", exe=bogus_abs)
    discovered = "/usr/local/bin/radiantc"

    def _mock_which(name, *_args, **_kwargs):
        if name == bogus_abs:
            return None
        if name == "radiantc":
            return discovered
        return None

    with patch("aurig_build.run._which", side_effect=_mock_which), \
         patch("aurig_build.run._radiant_version_guess", return_value="2024.1"):
        # Must NOT raise: the fallback recovers from the bad abs path.
        prepare_env(cfg, need_synth=True, need_sim=False)

    captured = capsys.readouterr()
    # User-visible warning that we deviated from the configured exe.
    assert bogus_abs in captured.err
    assert discovered in captured.err
