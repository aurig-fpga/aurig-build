# SPDX-License-Identifier: Apache-2.0
# Copyright 2024-2026 LogiMentor S.r.l.

"""Tests for the §6 #3 closure: implicit `tool.sim.kind` default in
`aurig_build.run::main` + the three per-vendor TCL sim wrappers that hand off
to `aurig_build/questa/sim.tcl` via `vsim`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
WRAPPERS = {
    "quartus": REPO_ROOT / "aurig_build" / "quartus" / "sim.tcl",
    "diamond": REPO_ROOT / "aurig_build" / "diamond" / "sim.tcl",
    "radiant": REPO_ROOT / "aurig_build" / "radiant" / "sim.tcl",
}


# ----------------------------------------------------------------------
# Python-side default dispatcher
# ----------------------------------------------------------------------

@pytest.mark.parametrize(
    ("synth_kind", "expected_sim_kind"),
    [
        ("vivado",  "xsim"),
        ("quartus", "questa"),
        ("diamond", "questa"),
        ("radiant", "questa"),
    ],
)
def test_sim_kind_defaults_from_synth_kind(
    tmp_path: Path, monkeypatch, synth_kind: str, expected_sim_kind: str, capsys
) -> None:
    """`python -m aurig_build.run sim --cfg ...` with synth.kind set but sim.kind
    absent must default sim.kind to a sensible value per backend, route
    to the matching dispatcher, and announce the choice on stderr."""
    cfg = {
        "schema_version": "1",
        "project_name": "test_proj",
        "project_root": "..",
        "top": "test_top",
        "tool": {
            "synth": {"kind": synth_kind, "version": "1.0", "exe": synth_kind},
            # sim block intentionally missing — that's the whole point.
        },
        "device": {"vendor": "xilinx", "family": "artix7", "part": "xc7a100t"},
        "file_sets": {"rtl": []},
        "include_dirs_global": [],
    }
    cfg_path = tmp_path / "project.yaml"
    cfg_path.write_text(yaml.dump(cfg))

    # The default-dispatcher path runs before any tool resolution; we
    # mock the actual sim_* dispatchers so we only validate the dispatch
    # decision, not a real vsim/xsim launch.
    monkeypatch.setattr(sys, "argv", [
        "run.py", "sim",
        "--cfg", str(cfg_path),
        "--noenv",
    ])

    fake_dispatch = lambda *args, **kw: 0
    with patch("aurig_build.run.sim_xsim",   side_effect=fake_dispatch) as mxsim, \
         patch("aurig_build.run.sim_questa", side_effect=fake_dispatch) as mquesta, \
         patch("aurig_build.run.sim_vunit",  side_effect=fake_dispatch) as mvunit:
        from aurig_build.run import main as _main
        rc = _main()

    assert rc == 0, "main() should succeed under the default sim.kind path"

    if expected_sim_kind == "xsim":
        assert mxsim.called and not mquesta.called and not mvunit.called
    elif expected_sim_kind == "questa":
        assert mquesta.called and not mxsim.called and not mvunit.called
    else:
        pytest.fail(f"unexpected expected_sim_kind: {expected_sim_kind}")

    captured = capsys.readouterr()
    assert "defaulting to" in captured.err
    assert f"'{expected_sim_kind}'" in captured.err
    assert f"tool.synth.kind='{synth_kind}'" in captured.err


def test_explicit_sim_kind_in_yaml_wins_over_default(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """If `tool.sim.kind` is explicit in the YAML, the default-dispatcher
    must NOT override it (and must NOT print the INFO message)."""
    cfg = {
        "schema_version": "1",
        "project_name": "test_proj",
        "project_root": "..",
        "top": "test_top",
        "tool": {
            "synth": {"kind": "quartus", "version": "23.1", "exe": "quartus_sh"},
            # Explicit choice: even though synth is Quartus (default → questa),
            # the user asked for VUnit.
            "sim":   {"kind": "vunit"},
        },
        "device": {"vendor": "xilinx", "family": "artix7", "part": "xc7a100t"},
        "file_sets": {"rtl": []},
        "include_dirs_global": [],
    }
    cfg_path = tmp_path / "project.yaml"
    cfg_path.write_text(yaml.dump(cfg))

    monkeypatch.setattr(sys, "argv", [
        "run.py", "sim",
        "--cfg", str(cfg_path),
        "--noenv",
    ])

    fake_dispatch = lambda *args, **kw: 0
    with patch("aurig_build.run.sim_xsim",   side_effect=fake_dispatch) as mxsim, \
         patch("aurig_build.run.sim_questa", side_effect=fake_dispatch) as mquesta, \
         patch("aurig_build.run.sim_vunit",  side_effect=fake_dispatch) as mvunit:
        from aurig_build.run import main as _main
        rc = _main()

    assert rc == 0
    assert mvunit.called
    assert not mquesta.called
    assert not mxsim.called

    # The [INFO] line is only printed when the default fires. Explicit
    # sim.kind → default must not fire → no INFO emitted.
    captured = capsys.readouterr()
    assert "defaulting to" not in captured.err, (
        f"default INFO leaked when sim.kind was explicit: {captured.err}"
    )


@pytest.mark.parametrize(
    "sim_block",
    [
        None,         # `tool.sim: ~`
        "",           # `tool.sim: ""` — unlikely but legal YAML
    ],
)
def test_sim_block_present_but_non_dict_still_defaults_cleanly(
    tmp_path: Path, monkeypatch, sim_block
) -> None:
    """Regression for the Codex P1 / Copilot High comment on PR #27:
    when YAML has `tool.sim: ~` (or any non-dict placeholder), the
    earlier `cfg.setdefault('sim', {})['kind'] = ...` form crashed with
    `TypeError: 'NoneType' object does not support item assignment`,
    killing the default-dispatcher path for exactly the configs it's
    meant to help. The fix is the same `isinstance(..., dict)`
    normalization the --tool / --sim CLI overrides already use."""
    cfg = {
        "schema_version": "1",
        "project_name": "test_proj",
        "project_root": "..",
        "top": "test_top",
        "tool": {
            "synth": {"kind": "quartus", "version": "23.1", "exe": "quartus_sh"},
            "sim":   sim_block,
        },
        "device": {"vendor": "xilinx", "family": "artix7", "part": "xc7a100t"},
        "file_sets": {"rtl": []},
        "include_dirs_global": [],
    }
    cfg_path = tmp_path / "project.yaml"
    cfg_path.write_text(yaml.dump(cfg))

    monkeypatch.setattr(sys, "argv", [
        "run.py", "sim",
        "--cfg", str(cfg_path),
        "--noenv",
    ])

    with patch("aurig_build.run.sim_questa", return_value=0) as mquesta, \
         patch("aurig_build.run.sim_xsim",   return_value=0) as mxsim, \
         patch("aurig_build.run.sim_vunit",  return_value=0) as mvunit:
        from aurig_build.run import main as _main
        rc = _main()

    assert rc == 0, "main() must not crash on tool.sim: ~ / empty"
    assert mquesta.called, "expected Questa dispatcher for quartus synth"
    assert not mxsim.called
    assert not mvunit.called


def test_default_inherits_env_script_and_bin_dir_from_synth(
    tmp_path: Path, monkeypatch
) -> None:
    """When the default fires, tool.sim must inherit env_script and bin_dir
    from tool.synth — otherwise `prepare_env(need_sim=True)` would only see
    the (empty) tool.sim block and the implicit default would crash at vsim
    launch on the very hosts it's meant to help (Quartus IE / Diamond /
    Radiant ship their simulator co-located with the synth tool, so the
    synth env script is the one that puts `vsim` on PATH)."""
    synth_env = {"linux": "/opt/foo/qenv.sh", "windows": "C:/foo/qenv.bat"}
    synth_bin = {"linux": "/opt/foo/bin64",   "windows": "C:/foo/bin64"}
    cfg = {
        "schema_version": "1",
        "project_name": "test_proj",
        "project_root": "..",
        "top": "test_top",
        "tool": {
            "synth": {
                "kind": "quartus",
                "version": "23.1",
                "exe": "quartus_sh",
                "env_script": synth_env,
                "bin_dir":    synth_bin,
            },
            # sim block intentionally missing — the default should both
            # set sim.kind AND inherit env_script / bin_dir from synth.
        },
        "device": {"vendor": "xilinx", "family": "artix7", "part": "xc7a100t"},
        "file_sets": {"rtl": []},
        "include_dirs_global": [],
    }
    cfg_path = tmp_path / "project.yaml"
    cfg_path.write_text(yaml.dump(cfg))

    monkeypatch.setattr(sys, "argv", [
        "run.py", "sim",
        "--cfg", str(cfg_path),
        "--noenv",
    ])

    # Capture the cfg as seen by sim_questa to inspect the post-default
    # state of tool.sim.
    captured_cfg = {}
    def _capture(cfg_path, tb, env, cfg):
        captured_cfg.update(cfg)
        return 0

    with patch("aurig_build.run.sim_questa", side_effect=_capture):
        from aurig_build.run import main as _main
        rc = _main()

    assert rc == 0
    sim_block = captured_cfg["tool"]["sim"]
    assert sim_block.get("kind") == "questa"
    assert sim_block.get("env_script") == synth_env, (
        f"env_script not inherited from synth: {sim_block}"
    )
    assert sim_block.get("bin_dir") == synth_bin, (
        f"bin_dir not inherited from synth: {sim_block}"
    )


def test_default_does_not_overwrite_explicit_sim_env_script(
    tmp_path: Path, monkeypatch
) -> None:
    """If tool.sim is present but only sets env_script (no kind), the
    default still fills in kind from synth — but inheritance must NOT
    overwrite the explicit env_script. Same rule for bin_dir."""
    synth_env = {"linux": "/opt/synth/env.sh"}
    user_sim_env = {"linux": "/opt/standalone-questa/env.sh"}
    cfg = {
        "schema_version": "1",
        "project_name": "test_proj",
        "project_root": "..",
        "top": "test_top",
        "tool": {
            "synth": {
                "kind": "quartus",
                "exe": "quartus_sh",
                "env_script": synth_env,
            },
            "sim": {
                # No kind → default fires. But env_script is set, so the
                # inheritance step must leave it alone.
                "env_script": user_sim_env,
            },
        },
        "device": {"vendor": "xilinx", "family": "artix7", "part": "xc7a100t"},
        "file_sets": {"rtl": []},
        "include_dirs_global": [],
    }
    cfg_path = tmp_path / "project.yaml"
    cfg_path.write_text(yaml.dump(cfg))

    monkeypatch.setattr(sys, "argv", [
        "run.py", "sim",
        "--cfg", str(cfg_path),
        "--noenv",
    ])

    captured_cfg = {}
    def _capture(cfg_path, tb, env, cfg):
        captured_cfg.update(cfg)
        return 0

    with patch("aurig_build.run.sim_questa", side_effect=_capture):
        from aurig_build.run import main as _main
        rc = _main()

    assert rc == 0
    sim_block = captured_cfg["tool"]["sim"]
    assert sim_block.get("kind") == "questa"
    assert sim_block.get("env_script") == user_sim_env, (
        f"explicit env_script under tool.sim got overwritten by inheritance: {sim_block}"
    )


@pytest.mark.parametrize(
    "bad_synth",
    [
        "vivado",            # scalar in tool.synth slot (legal-ish YAML)
        ["vivado", "extra"], # list
        42,                  # int
    ],
)
def test_non_dict_tool_synth_does_not_crash_pre_materialize_read(
    tmp_path: Path, monkeypatch, bad_synth, capsys
) -> None:
    """A truthy non-dict at tool.synth (scalar / list / int) must not crash
    the loader. normalize() and the sim-default closure skip non-dict synth
    via isinstance(..., dict) guards, so the manifest reaches the validator
    and surfaces as a clean schema error (tool.synth must be an object),
    never an AttributeError stack trace."""
    cfg = {
        "schema_version": "1",
        "project_name": "test_proj",
        "project_root": "..",
        "top": "test_top",
        "tool": {"synth": bad_synth},
        "device": {"vendor": "xilinx", "family": "artix7", "part": "xc7a100t"},
        "file_sets": {"rtl": []},
        "include_dirs_global": [],
    }
    cfg_path = tmp_path / "project.yaml"
    cfg_path.write_text(yaml.dump(cfg))

    monkeypatch.setattr(sys, "argv", [
        "run.py", "sim",
        "--cfg", str(cfg_path),
        "--noenv",
    ])

    from aurig_build.run import main as _main
    rc = _main()

    # synth is a non-dict → validation rejects it as a clean schema error,
    # not an AttributeError traceback.
    assert rc == 2
    captured = capsys.readouterr()
    assert "[ERROR]" in captured.err
    assert "tool.synth" in captured.err
    assert "Traceback" not in captured.err


def test_default_does_not_inherit_when_sim_explicitly_opts_out_with_empty_dict(
    tmp_path: Path, monkeypatch
) -> None:
    """The opt-out form for inheritance is `env_script: {}` (an empty
    dict). Distinguishes "user explicitly disabled env sourcing" from
    "YAML placeholder with no value yet" (null) — the latter still
    inherits to keep vsim launchable on co-installed Questa setups."""
    synth_env = {"linux": "/opt/foo/qenv.sh"}
    cfg = {
        "schema_version": "1",
        "project_name": "test_proj",
        "project_root": "..",
        "top": "test_top",
        "tool": {
            "synth": {
                "kind": "quartus",
                "exe":  "quartus_sh",
                "env_script": synth_env,
            },
            # Empty-dict opt-out: user wants no env_script for sim,
            # even though synth has one. Inheritance must not overwrite.
            "sim": {"env_script": {}},
        },
        "device": {"vendor": "xilinx", "family": "artix7", "part": "xc7a100t"},
        "file_sets": {"rtl": []},
        "include_dirs_global": [],
    }
    cfg_path = tmp_path / "project.yaml"
    cfg_path.write_text(yaml.dump(cfg))

    monkeypatch.setattr(sys, "argv", [
        "run.py", "sim",
        "--cfg", str(cfg_path),
        "--noenv",
    ])

    captured_cfg = {}
    def _capture(cfg_path, tb, env, cfg):
        captured_cfg.update(cfg)
        return 0

    with patch("aurig_build.run.sim_questa", side_effect=_capture):
        from aurig_build.run import main as _main
        rc = _main()

    assert rc == 0
    sim_block = captured_cfg["tool"]["sim"]
    assert sim_block.get("kind") == "questa"
    assert sim_block.get("env_script") == {}, (
        f"explicit empty-dict opt-out got overwritten by inheritance: {sim_block}"
    )


def test_default_inherits_when_sim_keys_present_but_null(
    tmp_path: Path, monkeypatch
) -> None:
    """Regression for the second Copilot Medium on PR #27 round-N:
    inheritance was previously blocked by mere key presence in tool.sim
    (`if _inherit_key not in _sim_block`). A YAML like
        tool.sim: {env_script: ~, bin_dir: ~}
    has the keys present but with null values, so the check `not in`
    would have skipped inheritance, leaving prepare_env(need_sim=True)
    with no usable env_script and vsim missing from PATH.

    Now the guard treats anything that isn't a non-empty dict as
    absent and inherits from synth.
    """
    synth_env = {"linux": "/opt/foo/qenv.sh"}
    synth_bin = {"linux": "/opt/foo/bin64"}
    cfg = {
        "schema_version": "1",
        "project_name": "test_proj",
        "project_root": "..",
        "top": "test_top",
        "tool": {
            "synth": {
                "kind": "quartus",
                "exe":  "quartus_sh",
                "env_script": synth_env,
                "bin_dir":    synth_bin,
            },
            # Keys present but null → must still inherit.
            "sim": {"env_script": None, "bin_dir": None},
        },
        "device": {"vendor": "xilinx", "family": "artix7", "part": "xc7a100t"},
        "file_sets": {"rtl": []},
        "include_dirs_global": [],
    }
    cfg_path = tmp_path / "project.yaml"
    cfg_path.write_text(yaml.dump(cfg))

    monkeypatch.setattr(sys, "argv", [
        "run.py", "sim",
        "--cfg", str(cfg_path),
        "--noenv",
    ])

    captured_cfg = {}
    def _capture(cfg_path, tb, env, cfg):
        captured_cfg.update(cfg)
        return 0

    with patch("aurig_build.run.sim_questa", side_effect=_capture):
        from aurig_build.run import main as _main
        rc = _main()

    assert rc == 0
    sim_block = captured_cfg["tool"]["sim"]
    assert sim_block.get("kind") == "questa"
    assert sim_block.get("env_script") == synth_env, (
        f"null placeholder didn't trigger inheritance: {sim_block}"
    )
    assert sim_block.get("bin_dir") == synth_bin, (
        f"null placeholder didn't trigger inheritance: {sim_block}"
    )


def test_sim_kind_unset_with_unknown_synth_kind_still_errors(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """A synth.kind outside the supported enum is rejected by validation
    before any sim-default can fire (no canonical backend exists for it)."""
    cfg = {
        "schema_version": "1",
        "project_name": "test_proj",
        "project_root": "..",
        "top": "test_top",
        "tool": {"synth": {"kind": "weirdvendor", "version": "1.0", "exe": "wv"}},
        "device": {"vendor": "xilinx", "family": "artix7", "part": "xc7a100t"},
        "file_sets": {"rtl": []},
        "include_dirs_global": [],
    }
    cfg_path = tmp_path / "project.yaml"
    cfg_path.write_text(yaml.dump(cfg))

    monkeypatch.setattr(sys, "argv", [
        "run.py", "sim",
        "--cfg", str(cfg_path),
        "--noenv",
    ])

    from aurig_build.run import main as _main
    rc = _main()

    assert rc == 2
    captured = capsys.readouterr()
    assert "tool.synth.kind" in captured.err
    assert "weirdvendor" in captured.err
    assert "defaulting to" not in captured.err  # no INFO was emitted


# ----------------------------------------------------------------------
# TCL wrappers
# ----------------------------------------------------------------------

@pytest.mark.skipif(
    shutil.which("tclsh") is None,
    reason="tclsh not available on PATH",
)
@pytest.mark.parametrize("vendor", list(WRAPPERS.keys()))
def test_wrapper_missing_vsim_exits_cleanly(
    tmp_path: Path, vendor: str
) -> None:
    """When invoked directly with vsim NOT on PATH, the per-vendor sim
    wrapper must exit 2 and print a helpful error — never crash inside
    `auto_execok` or hang."""
    cfg_path = tmp_path / "project.yaml"
    cfg_path.write_text("project_name: foo\n")

    # Resolve tclsh to its absolute path NOW (under the host PATH) and
    # invoke it directly. That lets us strip PATH inside the child's env
    # to make `auto_execok vsim` find nothing inside the wrapper, without
    # subprocess.run() itself losing the ability to locate `tclsh` (which
    # would happen if we left argv=["tclsh", ...] and passed env=...
    # together — Python would resolve via the child env's empty PATH).
    tclsh_abs = shutil.which("tclsh")
    assert tclsh_abs is not None, "tclsh should be on PATH (pytestmark guards this)"

    # Strip PATH so `auto_execok vsim` finds nothing. Keep TCL_LIBRARY
    # and TCLLIBPATH if present so tclsh itself still finds its stdlib.
    env_min = {
        k: v for k, v in {
            "TCL_LIBRARY": os.environ.get("TCL_LIBRARY"),
            "TCLLIBPATH":  os.environ.get("TCLLIBPATH"),
            "SystemRoot":  os.environ.get("SystemRoot"),  # Win runtime
            "TEMP":        str(tmp_path),
        }.items() if v is not None
    }
    env_min["PATH"] = ""

    proc = subprocess.run(
        [tclsh_abs, str(WRAPPERS[vendor]), str(cfg_path)],
        capture_output=True, text=True, timeout=10, env=env_min,
    )
    assert proc.returncode == 2, (
        f"expected exit 2, got {proc.returncode}\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert "vsim" in proc.stderr.lower()
    assert "not found" in proc.stderr.lower() or "required" in proc.stderr.lower()


@pytest.mark.skipif(
    shutil.which("tclsh") is None,
    reason="tclsh not available on PATH",
)
@pytest.mark.parametrize("vendor", list(WRAPPERS.keys()))
def test_wrapper_usage_when_missing_args(vendor: str) -> None:
    """No args → usage line + exit 2."""
    proc = subprocess.run(
        ["tclsh", str(WRAPPERS[vendor])],
        capture_output=True, text=True, timeout=5,
    )
    assert proc.returncode == 2
    assert "Usage:" in proc.stderr
