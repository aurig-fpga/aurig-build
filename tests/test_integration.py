# SPDX-License-Identifier: Apache-2.0
# Copyright 2024-2026 LogiMentor S.r.l.

"""
Integration tests using real directory structures and mocked subprocess calls.

Tests verify the complete command shape that would be passed to vendor tools
without actually invoking them. Uses tmp_project fixture for realistic setup.
"""

import os
import pytest
import sys
import yaml
from pathlib import Path
from unittest.mock import patch, call

from aurig_build.run import main


# ============================================================================
# Vivado command shape tests
# ============================================================================

def test_vivado_project_cmd_shape(tmp_project, monkeypatch):
    """Verify vivado project target generates correct command shape."""
    yaml_path = tmp_project / "config" / "project.yaml"

    monkeypatch.setattr(sys, "argv", [
        "run.py", "project",
        "--cfg", str(yaml_path),
        "--noenv"
    ])

    # Mock shutil.which to pretend vivado exists
    with patch("shutil.which", return_value="/usr/bin/vivado"), \
         patch("subprocess.call", return_value=0) as mock_call:
        result = main()

        # Should have been called once
        assert mock_call.call_count == 1

        # Get the command that was called
        cmd = mock_call.call_args[0][0]

        # Verify command shape
        assert any("vivado" in str(arg).lower() for arg in cmd), \
            f"Expected 'vivado' in command, got: {cmd}"
        assert any("build.tcl" in str(arg) for arg in cmd), \
            f"Expected 'build.tcl' in command, got: {cmd}"
        assert "project" in cmd, \
            f"Expected 'project' action in command, got: {cmd}"

        assert result == 0


def test_vivado_synth_cmd_shape(tmp_project, monkeypatch):
    """Verify vivado synth target generates correct command shape."""
    yaml_path = tmp_project / "config" / "project.yaml"

    monkeypatch.setattr(sys, "argv", [
        "run.py", "synth",
        "--cfg", str(yaml_path),
        "--noenv"
    ])

    with patch("shutil.which", return_value="/usr/bin/vivado"), \
         patch("subprocess.call", return_value=0) as mock_call:
        result = main()

        assert mock_call.call_count == 1
        cmd = mock_call.call_args[0][0]

        assert any("vivado" in str(arg).lower() for arg in cmd)
        assert any("build.tcl" in str(arg) for arg in cmd)
        assert "synth" in cmd
        assert result == 0


def test_vivado_bit_cmd_shape(tmp_project, monkeypatch):
    """Verify vivado bit target generates correct command shape."""
    yaml_path = tmp_project / "config" / "project.yaml"

    monkeypatch.setattr(sys, "argv", [
        "run.py", "bit",
        "--cfg", str(yaml_path),
        "--noenv"
    ])

    with patch("shutil.which", return_value="/usr/bin/vivado"), \
         patch("subprocess.call", return_value=0) as mock_call:
        result = main()

        assert mock_call.call_count == 1
        cmd = mock_call.call_args[0][0]

        assert any("vivado" in str(arg).lower() for arg in cmd)
        assert any("build.tcl" in str(arg) for arg in cmd)
        assert "bit" in cmd
        assert result == 0


# ============================================================================
# Diamond command shape tests
# ============================================================================

def test_diamond_project_cmd_shape(tmp_path, monkeypatch, minimal_yaml_diamond):
    """Verify diamond project target generates correct command shape."""
    # Create Diamond config
    (tmp_path / "config").mkdir()
    yaml_path = tmp_path / "config" / "project.yaml"
    with open(yaml_path, "w") as f:
        yaml.dump(minimal_yaml_diamond, f)

    # Create minimal source structure
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "top.vhd").write_text("entity test_top is end entity;")
    (tmp_path / "constraints").mkdir()
    (tmp_path / "constraints" / "pins.lpf").write_text("# LPF placeholder")

    monkeypatch.setattr(sys, "argv", [
        "run.py", "project",
        "--cfg", str(yaml_path),
        "--noenv"
    ])

    with patch("shutil.which", return_value="/usr/bin/pnmainc"), \
         patch("subprocess.call", return_value=0) as mock_call:
        result = main()

        assert mock_call.call_count == 1
        cmd = mock_call.call_args[0][0]

        # Diamond uses pnmainc executable
        assert any("pnmainc" in str(arg).lower() for arg in cmd), \
            f"Expected 'pnmainc' in command, got: {cmd}"
        assert any("build.tcl" in str(arg) for arg in cmd)
        assert result == 0


# ============================================================================
# Vivado tool.synth.exe / bin_dir resolution (§6 #8)
# ============================================================================

def test_vivado_honors_tool_synth_exe(tmp_project, monkeypatch):
    """
    §6 #8: vivado_build must honor `tool.synth.exe` (absolute path) the
    same way the other synth dispatchers do, instead of doing a
    hardcoded PATH-only lookup of "vivado".
    """
    import os
    custom_exe = tmp_project / "custom_vivado"
    custom_exe.write_text("# placeholder\n")
    os.chmod(custom_exe, 0o755)

    yaml_path = tmp_project / "config" / "project.yaml"
    cfg = yaml.safe_load(yaml_path.read_text())
    cfg["tool"]["synth"]["exe"] = str(custom_exe)
    yaml_path.write_text(yaml.dump(cfg))

    monkeypatch.setattr(sys, "argv", [
        "run.py", "project",
        "--cfg", str(yaml_path),
        "--noenv",
    ])

    with patch("subprocess.call", return_value=0) as mock_call:
        result = main()
        assert mock_call.call_count == 1
        cmd = mock_call.call_args[0][0]
        # On Windows, .bat/.cmd is wrapped via cmd.exe /c (so the configured
        # path appears at cmd[2] in [cmd.exe, /c, <exe>, ...]); on POSIX
        # without an extension it appears at cmd[0]. The custom_vivado fixture
        # has no extension so cmd[0] should be the absolute path verbatim.
        assert cmd[0] == str(custom_exe), \
            f"Expected configured exe '{custom_exe}', got cmd[0]={cmd[0]} (full cmd: {cmd})"
        assert result == 0


@pytest.mark.parametrize("bin_dir_template", [
    "{abs}",        # already absolute, baseline
    '"{abs}"',      # double-quoted (Windows-style YAML quoting)
    "'{abs}'",      # single-quoted
])
def test_vivado_bin_dir_is_sanitized(bin_dir_template, tmp_project, monkeypatch):
    """
    §6 #8: vivado_build now resolves via _resolve_vendor_exe which honors
    tool.synth.bin_dir (sanitized: trim quotes, expanduser, expandvars,
    normalize separators). With no PATH match, bin_dir is the only viable
    fallback and must be honored across common YAML quoting shapes —
    mirror of test_radiant_bin_dir_is_sanitized.
    """
    import os
    bin_dir = tmp_project / "vendor_bin"
    bin_dir.mkdir()
    exe_name = "vivado.exe" if sys.platform.startswith("win") else "vivado"
    tool_exe = bin_dir / exe_name
    tool_exe.write_text("# placeholder\n")
    os.chmod(tool_exe, 0o755)

    rendered_bin_dir = bin_dir_template.format(abs=str(bin_dir))
    yaml_path = tmp_project / "config" / "project.yaml"
    cfg = yaml.safe_load(yaml_path.read_text())
    oskey = "windows" if sys.platform.startswith("win") else "linux"
    cfg["tool"]["synth"]["bin_dir"] = {oskey: rendered_bin_dir}
    # Force a basename that won't match anything on the real PATH, so the
    # test fails loudly if the bin_dir branch is broken instead of silently
    # passing via the system Vivado.
    cfg["tool"]["synth"]["exe"] = "vivado"
    yaml_path.write_text(yaml.dump(cfg))

    monkeypatch.setattr(sys, "argv", [
        "run.py", "project",
        "--cfg", str(yaml_path),
        "--noenv",
    ])

    # Patch aurig_build.run._which only — leave shutil.which itself alone so the
    # path-aware shutil.which(name, path=bin_dir) call inside step 3 still
    # works against the planted file.
    with patch("aurig_build.run._which", return_value=None), \
         patch("subprocess.call", return_value=0) as mock_call:
        result = main()
        assert mock_call.call_count == 1, \
            f"bin_dir={rendered_bin_dir!r} not honored; subprocess.call never ran"
        cmd = mock_call.call_args[0][0]
        # On Windows shutil.which returns the path with the PATHEXT case
        # (e.g. vivado.EXE) regardless of the on-disk filename case, and the
        # .bat/.cmd wrap goes through cmd.exe /c so the exe is at cmd[2].
        exe_at = cmd[2] if (sys.platform.startswith("win") and cmd[0].lower() == "cmd.exe") else cmd[0]
        assert os.path.normcase(exe_at) == os.path.normcase(str(tool_exe)), \
            f"Expected exe under sanitized bin_dir '{bin_dir}', got exe={exe_at} (full cmd: {cmd})"
        assert result == 0


@pytest.mark.parametrize("nonsense_exe", [
    '""',     # double-quoted empty
    "''",     # single-quoted empty
    '"   "',  # quoted whitespace
])
def test_vivado_quotes_only_exe_falls_back_to_default(nonsense_exe, tmp_project, monkeypatch):
    """
    _sanitize_script_path collapses a quotes-only / whitespace-only value
    to "." via os.path.normpath(""). _resolve_vendor_exe must treat that
    as "no exe configured" and fall back to the vendor default — not look
    for an executable literally named ".".
    """
    yaml_path = tmp_project / "config" / "project.yaml"
    cfg = yaml.safe_load(yaml_path.read_text())
    cfg["tool"]["synth"]["exe"] = nonsense_exe
    yaml_path.write_text(yaml.dump(cfg))

    monkeypatch.setattr(sys, "argv", [
        "run.py", "project",
        "--cfg", str(yaml_path),
        "--noenv",
    ])

    fake_vivado = "/fake/bin/vivado.bat" if sys.platform.startswith("win") else "/fake/bin/vivado"
    queried = []

    def _which(name, *_args, **_kwargs):
        queried.append(name)
        # Match whichever default the dispatcher passes (vivado.bat on Windows,
        # vivado on POSIX).
        return fake_vivado if name in ("vivado.bat", "vivado") else None

    with patch("aurig_build.run._which", side_effect=_which), \
         patch("subprocess.call", return_value=0) as mock_call:
        result = main()
        assert result == 0
        # The resolver must NEVER have asked for "." — that's the
        # regression we are guarding against.
        assert "." not in queried, \
            f"resolver looked up the dot path; saw queries: {queried}"
        # And it must have asked for one of the vendor defaults.
        assert any(name in queried for name in ("vivado.bat", "vivado")), \
            f"expected fallback to vivado default; saw queries: {queried}"


@pytest.mark.parametrize("exe_template", [
    '"{abs}"',     # double-quoted (Windows-style YAML quoting)
    "'{abs}'",     # single-quoted
])
def test_vivado_sanitizes_quoted_exe(exe_template, tmp_project, monkeypatch):
    """
    _resolve_vendor_exe sanitizes `tool.synth.exe` the same way it
    sanitizes `tool.synth.bin_dir` — trim wrapping quotes, expanduser,
    expandvars, normalize separators. Without this a user who copies a
    quoted absolute path from a shell or docs would silently fail step
    1 (isabs/exists) and degrade to a basename PATH lookup.
    """
    import os
    custom_exe = tmp_project / "custom_vivado"
    custom_exe.write_text("# placeholder\n")
    os.chmod(custom_exe, 0o755)

    yaml_path = tmp_project / "config" / "project.yaml"
    cfg = yaml.safe_load(yaml_path.read_text())
    cfg["tool"]["synth"]["exe"] = exe_template.format(abs=str(custom_exe))
    yaml_path.write_text(yaml.dump(cfg))

    monkeypatch.setattr(sys, "argv", [
        "run.py", "project",
        "--cfg", str(yaml_path),
        "--noenv",
    ])

    with patch("subprocess.call", return_value=0) as mock_call:
        result = main()
        assert mock_call.call_count == 1
        cmd = mock_call.call_args[0][0]
        # The path inside the quotes must be honored after sanitize.
        # Path equality is platform-aware (os.path.normpath in sanitize
        # normalizes separators on Windows).
        assert os.path.normcase(cmd[0]) == os.path.normcase(str(custom_exe)), \
            f"Expected sanitized exe '{custom_exe}', got cmd[0]={cmd[0]} (full cmd: {cmd})"
        assert result == 0


@pytest.mark.skipif(
    not sys.platform.startswith("win"),
    reason="Windows-only path: vivado.bat default vs vivado.exe via PATHEXT",
)
def test_vivado_default_lookup_prefers_bat_on_windows(tmp_project, monkeypatch):
    """
    Before PR #16 vivado_build did `_resolve_tool_exe(["vivado.bat",
    "vivado"], env)`, explicitly preferring `.bat` over `.exe`. After the
    refactor to `_resolve_vendor_exe`, the default basename matters
    because `shutil.which` follows PATHEXT order (`.COM;.EXE;.BAT;...`),
    which would prefer a stray `vivado.exe` over the Xilinx-shipped
    `vivado.bat`. vivado_build now passes `vivado.bat` as the default
    on Windows; this test pins that.

    Mocks aurig_build.run._which so that `vivado` resolves to a stray .exe
    while `vivado.bat` resolves to the wrapper. The dispatcher must pick
    the .bat.
    """
    yaml_path = tmp_project / "config" / "project.yaml"
    # The minimal_yaml_vivado fixture pre-sets exe: "vivado". Remove it so
    # we exercise the DEFAULT branch where vivado_build picks "vivado.bat"
    # on Windows. (Explicit user-set exe is the user's choice; the
    # preference being pinned here is the OUT-OF-THE-BOX default.)
    cfg = yaml.safe_load(yaml_path.read_text())
    cfg["tool"]["synth"].pop("exe", None)
    yaml_path.write_text(yaml.dump(cfg))

    monkeypatch.setattr(sys, "argv", [
        "run.py", "project",
        "--cfg", str(yaml_path),
        "--noenv",
    ])

    bat_path = r"C:\Xilinx\Vivado\2023.1\bin\vivado.bat"
    foreign_exe = r"C:\foreign\vivado.exe"

    def _which(name, *_args, **_kwargs):
        if name == "vivado.bat":
            return bat_path
        if name == "vivado":
            return foreign_exe
        return None

    with patch("aurig_build.run._which", side_effect=_which), \
         patch("subprocess.call", return_value=0) as mock_call:
        result = main()
        assert mock_call.call_count == 1
        cmd = mock_call.call_args[0][0]
        # .bat is wrapped via cmd.exe /c: cmd looks like
        #   [cmd.exe, /c, <bat>, ...args]
        assert cmd[0].lower() == "cmd.exe"
        assert cmd[1] == "/c"
        assert cmd[2] == bat_path, \
            f"Expected vivado.bat preference, got cmd[2]={cmd[2]} (foreign exe = {foreign_exe})"
        assert result == 0


@pytest.mark.skipif(
    not sys.platform.startswith("win"),
    reason="Windows-only path: .bat-then-vivado fallback",
)
def test_vivado_default_lookup_falls_back_to_plain_vivado_on_windows(tmp_project, monkeypatch):
    """
    Historical compat: the pre-refactor `_resolve_tool_exe(["vivado.bat",
    "vivado"], env)` would try .bat first AND fall through to `vivado` if
    .bat was missing. Containerized / non-Xilinx Windows installs ship
    `vivado.exe` (PATHEXT-resolvable as `vivado`) without a `.bat` wrapper;
    those must still resolve when the user doesn't override
    `tool.synth.exe`. Pins the fallback restored after Copilot review.
    """
    yaml_path = tmp_project / "config" / "project.yaml"
    cfg = yaml.safe_load(yaml_path.read_text())
    cfg["tool"]["synth"].pop("exe", None)   # exercise the default branch
    yaml_path.write_text(yaml.dump(cfg))

    monkeypatch.setattr(sys, "argv", [
        "run.py", "project",
        "--cfg", str(yaml_path),
        "--noenv",
    ])

    discovered_exe = r"C:\PortableVivado\bin\vivado.exe"

    def _which(name, *_args, **_kwargs):
        # No vivado.bat anywhere on this hypothetical PATH.
        if name == "vivado.bat":
            return None
        if name == "vivado":
            return discovered_exe
        return None

    with patch("aurig_build.run._which", side_effect=_which), \
         patch("subprocess.call", return_value=0) as mock_call:
        result = main()
        assert mock_call.call_count == 1
        cmd = mock_call.call_args[0][0]
        # .exe is invoked directly (no cmd.exe /c wrap), so cmd[0] is the exe.
        assert cmd[0] == discovered_exe, \
            f"Expected fallback to '{discovered_exe}', got cmd[0]={cmd[0]}"
        assert result == 0


def test_vivado_falls_back_to_basename_when_abs_exe_missing(tmp_project, monkeypatch):
    """
    §6 #8 (basename fallback parity with the other synth dispatchers):
    a bogus absolute tool.synth.exe must degrade to a PATH lookup by
    basename, not give up.
    """
    yaml_path = tmp_project / "config" / "project.yaml"
    cfg = yaml.safe_load(yaml_path.read_text())
    cfg["tool"]["synth"]["exe"] = "/nonexistent/dir/custom_vivado"
    yaml_path.write_text(yaml.dump(cfg))

    monkeypatch.setattr(sys, "argv", [
        "run.py", "project",
        "--cfg", str(yaml_path),
        "--noenv",
    ])

    discovered = "/usr/local/bin/custom_vivado"
    def _which(name, *_args, **_kwargs):
        return discovered if name == "custom_vivado" else None

    with patch("shutil.which", side_effect=_which), \
         patch("subprocess.call", return_value=0) as mock_call:
        result = main()
        assert mock_call.call_count == 1
        cmd = mock_call.call_args[0][0]
        assert cmd[0] == discovered, \
            f"Expected fallback to PATH-resolved '{discovered}', got cmd[0]={cmd[0]}"
        assert result == 0


# ============================================================================
# Quartus command shape tests
# ============================================================================

def _write_quartus_project(tmp_path, cfg):
    """Helper: write minimal Quartus project tree and return YAML path."""
    (tmp_path / "config").mkdir()
    yaml_path = tmp_path / "config" / "project.yaml"
    with open(yaml_path, "w") as f:
        yaml.dump(cfg, f)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "top.vhd").write_text("entity test_top is end entity;")
    (tmp_path / "constraints").mkdir()
    (tmp_path / "constraints" / "timing.sdc").write_text("# SDC placeholder")
    return yaml_path


@pytest.mark.parametrize("target,expected_action", [
    ("project",  "create"),
    ("synth",    "synth"),
    ("impl",     "impl"),
    ("bit",      "bit"),
    ("exporthw", "bit"),
])
def test_quartus_phase_map(target, expected_action, tmp_path, monkeypatch, minimal_yaml_quartus):
    """
    aurig_build/run.py — quartus_build phase_map translates aurig-build CLI targets to the
    action names that aurig_build/quartus/build.tcl expects (create|synth|impl|bit).
    Without this map, `project` and `exporthw` would die TCL-side.
    """
    yaml_path = _write_quartus_project(tmp_path, minimal_yaml_quartus)

    monkeypatch.setattr(sys, "argv", [
        "run.py", target,
        "--cfg", str(yaml_path),
        "--noenv",
    ])

    with patch("shutil.which", return_value="/usr/bin/quartus_sh"), \
         patch("subprocess.call", return_value=0) as mock_call:
        result = main()

        assert mock_call.call_count == 1
        cmd = mock_call.call_args[0][0]

        # Quartus command shape: [exe, "-t", build.tcl, cfg_path, action]
        assert any("quartus_sh" in str(arg).lower() for arg in cmd), \
            f"Expected 'quartus_sh' in command, got: {cmd}"
        assert any("build.tcl" in str(arg) for arg in cmd)
        assert cmd[-1] == expected_action, \
            f"Expected action '{expected_action}' for target '{target}', got: {cmd[-1]} (full cmd: {cmd})"
        assert result == 0


# ============================================================================
# Radiant command shape tests
# ============================================================================

def _write_radiant_project(tmp_path, cfg):
    """Helper: write minimal Radiant project tree and return YAML path."""
    (tmp_path / "config").mkdir()
    yaml_path = tmp_path / "config" / "project.yaml"
    with open(yaml_path, "w") as f:
        yaml.dump(cfg, f)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "top.vhd").write_text("entity test_top is end entity;")
    (tmp_path / "constraints").mkdir()
    (tmp_path / "constraints" / "pins.pdc").write_text("# PDC placeholder")
    return yaml_path


@pytest.mark.parametrize("target,expected_action", [
    ("project",  "create"),
    ("synth",    "synth"),
    ("impl",     "impl"),
    ("bit",      "bit"),
    ("exporthw", "bit"),
])
def test_radiant_phase_map(target, expected_action, tmp_path, monkeypatch, minimal_yaml_radiant):
    """
    aurig_build/run.py — radiant_build phase_map translates aurig-build CLI targets to the
    action names that aurig_build/radiant/build.tcl expects (create|synth|impl|bit).
    Mirrors test_quartus_phase_map / diamond_build behavior.
    """
    yaml_path = _write_radiant_project(tmp_path, minimal_yaml_radiant)

    monkeypatch.setattr(sys, "argv", [
        "run.py", target,
        "--cfg", str(yaml_path),
        "--noenv",
    ])

    with patch("shutil.which", return_value="/usr/bin/radiantc"), \
         patch("subprocess.call", return_value=0) as mock_call:
        result = main()

        assert mock_call.call_count == 1
        cmd = mock_call.call_args[0][0]

        # Radiant command shape: [exe, build.tcl, cfg_path, action]
        assert any("radiantc" in str(arg).lower() for arg in cmd), \
            f"Expected 'radiantc' in command, got: {cmd}"
        assert any("build.tcl" in str(arg) for arg in cmd)
        assert cmd[-1] == expected_action, \
            f"Expected action '{expected_action}' for target '{target}', got: {cmd[-1]} (full cmd: {cmd})"
        assert result == 0


def test_radiant_honors_tool_synth_exe(tmp_path, monkeypatch, minimal_yaml_radiant):
    """
    aurig_build/run.py — radiant_build must honor tool.synth.exe from YAML
    (absolute path or custom name), not just the hardcoded radiantc default.
    Mirrors the resolution order used by quartus_build.
    """
    import os
    custom_exe = tmp_path / "custom_radiantc"
    custom_exe.write_text("# placeholder so os.path.isfile returns True\n")
    os.chmod(custom_exe, 0o755)  # POSIX: needs +x for _exists() to accept it
    cfg = dict(minimal_yaml_radiant)
    cfg["tool"] = dict(cfg["tool"])
    cfg["tool"]["synth"] = dict(cfg["tool"]["synth"])
    cfg["tool"]["synth"]["exe"] = str(custom_exe)
    yaml_path = _write_radiant_project(tmp_path, cfg)

    monkeypatch.setattr(sys, "argv", [
        "run.py", "project",
        "--cfg", str(yaml_path),
        "--noenv",
    ])

    # No which/PATH mock: resolution must take the absolute-path branch.
    with patch("subprocess.call", return_value=0) as mock_call:
        result = main()
        assert mock_call.call_count == 1
        cmd = mock_call.call_args[0][0]
        assert cmd[0] == str(custom_exe), \
            f"Expected exe '{custom_exe}', got cmd[0]={cmd[0]} (full cmd: {cmd})"
        assert result == 0


def test_quartus_falls_back_to_basename_when_abs_exe_missing(tmp_path, monkeypatch, minimal_yaml_quartus):
    """
    §6 #4: quartus_build must derive cand_names from the basename so a
    misconfigured absolute tool.synth.exe still falls back to PATH/bin_dir
    lookups by name. Mirrors test_radiant_falls_back_to_basename_when_abs_exe_missing.
    """
    bogus_abs = "/nonexistent/dir/custom_quartus_sh"
    cfg = dict(minimal_yaml_quartus)
    cfg["tool"] = dict(cfg["tool"])
    cfg["tool"]["synth"] = dict(cfg["tool"]["synth"])
    cfg["tool"]["synth"]["exe"] = bogus_abs
    yaml_path = _write_quartus_project(tmp_path, cfg)

    monkeypatch.setattr(sys, "argv", [
        "run.py", "project",
        "--cfg", str(yaml_path),
        "--noenv",
    ])

    discovered = "/usr/local/bin/custom_quartus_sh"
    def _which(name, *_args, **_kwargs):
        return discovered if name == "custom_quartus_sh" else None

    with patch("shutil.which", side_effect=_which), \
         patch("subprocess.call", return_value=0) as mock_call:
        result = main()
        assert mock_call.call_count == 1
        cmd = mock_call.call_args[0][0]
        assert cmd[0] == discovered, \
            f"Expected fallback to PATH-resolved '{discovered}', got cmd[0]={cmd[0]} (full cmd: {cmd})"
        assert result == 0


@pytest.mark.parametrize("bin_dir_template", [
    "{abs}",          # already absolute, baseline
    '"{abs}"',        # double-quoted (Windows-style YAML quoting)
    "'{abs}'",        # single-quoted
])
def test_radiant_bin_dir_is_sanitized(bin_dir_template, tmp_path, monkeypatch, minimal_yaml_radiant):
    """
    §6 #11 / §5 #11: radiant_build (and quartus_build / diamond_build) must
    run tool.synth.bin_dir through _sanitize_script_path before the
    isabs/isdir guard. Without it, quoted paths fail the guard and the
    probe is silently skipped.
    """
    import os
    bin_dir = tmp_path / "vendor_bin"
    bin_dir.mkdir()
    # On Windows `shutil.which("radiantc", path=bin_dir)` only matches names
    # that satisfy PATHEXT (.exe/.bat/.cmd/...). On POSIX an extensionless
    # +x file is fine. Pick the right shape for the host so the fixture
    # mirrors a real Lattice install on that OS.
    exe_name = "radiantc.exe" if sys.platform.startswith("win") else "radiantc"
    tool_exe = bin_dir / exe_name
    tool_exe.write_text("# placeholder\n")
    os.chmod(tool_exe, 0o755)

    rendered_bin_dir = bin_dir_template.format(abs=str(bin_dir))

    cfg = dict(minimal_yaml_radiant)
    cfg["tool"] = dict(cfg["tool"])
    cfg["tool"]["synth"] = dict(cfg["tool"]["synth"])
    oskey = "windows" if sys.platform.startswith("win") else "linux"
    cfg["tool"]["synth"]["bin_dir"] = {oskey: rendered_bin_dir}
    yaml_path = _write_radiant_project(tmp_path, cfg)

    monkeypatch.setattr(sys, "argv", [
        "run.py", "project",
        "--cfg", str(yaml_path),
        "--noenv",
    ])

    # PATH lookup fails, so bin_dir is the only viable fallback.
    # NB: patch aurig_build.run._which (the PATH lookup) only — leave shutil.which
    # itself intact so _resolve_vendor_exe's path-aware `shutil.which(..., path=bin_dir)`
    # can still discover the planted tool.
    with patch("aurig_build.run._which", return_value=None), \
         patch("subprocess.call", return_value=0) as mock_call:
        result = main()
        assert mock_call.call_count == 1, \
            f"bin_dir={rendered_bin_dir!r} not honored; subprocess.call never ran"
        cmd = mock_call.call_args[0][0]
        # On Windows shutil.which returns the path with the PATHEXT case
        # (e.g. radiantc.EXE) regardless of the on-disk filename case.
        # Compare via normcase so the test passes on both OSes.
        assert os.path.normcase(cmd[0]) == os.path.normcase(str(tool_exe)), \
            f"Expected exe under sanitized bin_dir '{bin_dir}', got cmd[0]={cmd[0]}"
        assert result == 0


def test_diamond_honors_tool_synth_exe(tmp_path, monkeypatch, minimal_yaml_diamond):
    """
    §6 #3 / §5 #7: diamond_build now honors `tool.synth.exe` (absolute path)
    the same way quartus_build / radiant_build do.
    """
    import os
    custom_exe = tmp_path / "custom_pnmainc"
    custom_exe.write_text("# placeholder\n")
    os.chmod(custom_exe, 0o755)
    cfg = dict(minimal_yaml_diamond)
    cfg["tool"] = dict(cfg["tool"])
    cfg["tool"]["synth"] = dict(cfg["tool"]["synth"])
    cfg["tool"]["synth"]["exe"] = str(custom_exe)
    # Write the diamond project tree manually (no _write_diamond_project helper today)
    (tmp_path / "config").mkdir()
    yaml_path = tmp_path / "config" / "project.yaml"
    with open(yaml_path, "w") as f:
        yaml.dump(cfg, f)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "top.vhd").write_text("entity test_top is end entity;")
    (tmp_path / "constraints").mkdir()
    (tmp_path / "constraints" / "pins.lpf").write_text("# LPF placeholder")

    monkeypatch.setattr(sys, "argv", [
        "run.py", "project",
        "--cfg", str(yaml_path),
        "--noenv",
    ])

    with patch("subprocess.call", return_value=0) as mock_call:
        result = main()
        assert mock_call.call_count == 1
        cmd = mock_call.call_args[0][0]
        assert cmd[0] == str(custom_exe), \
            f"Expected exe '{custom_exe}', got cmd[0]={cmd[0]} (full cmd: {cmd})"
        assert result == 0


def test_radiant_bin_dir_probe_does_not_fall_back_to_cwd(tmp_path, monkeypatch, minimal_yaml_radiant):
    """
    Regression for PR #11 review (Copilot, medium): radiant_build's bin_dir
    probe runs `os.path.join(bin_dir, name)` + `os.path.exists()`. When
    `bin_dir` is empty (the default), the join collapses to just `name`,
    and a `radiantc` planted in CWD would silently get picked up and
    executed. Same code path exists in quartus_build.
    """
    import os
    # Plant a decoy "radiantc" in CWD. Make it executable so the POSIX
    # X_OK check in _exists() can't save us.
    decoy = tmp_path / "radiantc"
    decoy.write_text("# decoy that should NEVER be invoked\n")
    os.chmod(decoy, 0o755)

    # Default minimal cfg: exe=radiantc, no bin_dir set.
    yaml_path = _write_radiant_project(tmp_path, minimal_yaml_radiant)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", [
        "run.py", "project",
        "--cfg", str(yaml_path),
        "--noenv",
    ])

    # PATH lookup fails (no radiantc on PATH).
    with patch("shutil.which", return_value=None), \
         patch("subprocess.call", return_value=0) as mock_call:
        result = main()

    # Must NOT have invoked the decoy. radiant_build should have returned
    # 2 ("not found after env setup") instead.
    assert mock_call.call_count == 0, (
        "subprocess.call was invoked; bin_dir probe likely picked up "
        f"the decoy at {decoy} via CWD"
    )
    assert result == 2


def test_radiant_falls_back_to_basename_when_abs_exe_missing(tmp_path, monkeypatch, minimal_yaml_radiant):
    """
    aurig_build/run.py — when tool.synth.exe points to an absolute path that does
    NOT exist, radiant_build must probe PATH using the basename of that path
    (not the full bogus path). This validates the cand_names = basename split.
    """
    bogus_abs = "/nonexistent/dir/custom_radiantc"
    cfg = dict(minimal_yaml_radiant)
    cfg["tool"] = dict(cfg["tool"])
    cfg["tool"]["synth"] = dict(cfg["tool"]["synth"])
    cfg["tool"]["synth"]["exe"] = bogus_abs
    yaml_path = _write_radiant_project(tmp_path, cfg)

    monkeypatch.setattr(sys, "argv", [
        "run.py", "project",
        "--cfg", str(yaml_path),
        "--noenv",
    ])

    # Have shutil.which return a discovered path only when asked for the basename.
    discovered = "/usr/local/bin/custom_radiantc"
    def _which(name, *_args, **_kwargs):
        return discovered if name == "custom_radiantc" else None

    with patch("shutil.which", side_effect=_which), \
         patch("subprocess.call", return_value=0) as mock_call:
        result = main()
        assert mock_call.call_count == 1
        cmd = mock_call.call_args[0][0]
        assert cmd[0] == discovered, \
            f"Expected fallback to PATH-resolved '{discovered}', got cmd[0]={cmd[0]} (full cmd: {cmd})"
        assert result == 0


# ============================================================================
# CLI --tool / --sim override propagation
# ============================================================================

@pytest.mark.parametrize("yaml_body, expected_kind_path", [
    # tool: null (whole tool section absent or explicitly null)
    ("schema_version: '1'\nproject_name: t\nproject_root: ..\ntop: t\ntool: ~\n",
     ("tool", "synth", "kind")),
    # tool.synth: null (sibling sim survives)
    ("schema_version: '1'\nproject_name: t\nproject_root: ..\ntop: t\ntool:\n  synth: ~\n  sim: {kind: questa}\n",
     ("tool", "synth", "kind")),
    # tool present but empty mapping
    ("schema_version: '1'\nproject_name: t\nproject_root: ..\ntop: t\ntool: {}\n",
     ("tool", "synth", "kind")),
])
def test_cli_tool_override_handles_malformed_tool_section(yaml_body, expected_kind_path, tmp_path, monkeypatch):
    """
    Regression for PR #11 review (Copilot, medium): the override block must
    tolerate the same malformed inputs the previous defensive `or {}` pattern
    did. YAML allows `tool: ~` or `tool.synth: ~`, which leave those nodes as
    None; setdefault() does not replace them and the subsequent mutation
    would raise AttributeError on None.setdefault.
    """
    (tmp_path / "config").mkdir()
    yaml_path = tmp_path / "config" / "project.yaml"
    yaml_path.write_text(yaml_body)

    monkeypatch.setattr(sys, "argv", [
        "run.py", "project",
        "--cfg", str(yaml_path),
        "--tool", "radiant",
        "--noenv",
    ])

    with patch("aurig_build.run.radiant_build", return_value=0) as mock_radiant:
        # Must not raise AttributeError. Must dispatch to radiant_build with
        # the override propagated into cfg.
        result = main()

    assert mock_radiant.call_count == 1
    cfg_arg = mock_radiant.call_args[0][3]
    node = cfg_arg
    for key in expected_kind_path:
        node = node[key]
    assert node == "radiant", \
        f"Expected override propagated to cfg{list(expected_kind_path)}='radiant', got {node!r}"
    assert result == 0


def test_cli_overrides_reach_tcl_via_materialized_cfg(tmp_project, monkeypatch):
    """
    Copilot follow-up (PR #15 round 8): the materialized side-file must
    reflect the CLI overrides too, not just the YAML + overlay merge.
    Otherwise a future `--<tool-field>` flag that mutates a TCL-consumed
    key would silently split-brain (Python uses the override, TCL reads
    the pre-override merge from the side-file).

    Today only `kind` is overridden and TCL doesn't read it, so the bug
    is invisible. This test pins the ordering so it stays correct.
    """
    import yaml as _yaml
    yaml_path = tmp_project / "config" / "project.yaml"
    # Drop an overlay so materialize_merged_cfg actually creates a side-file.
    overlay_path = yaml_path.with_name(f"{yaml_path.stem}.local{yaml_path.suffix}")
    overlay_path.write_text(_yaml.dump({"tool": {"synth": {"version": "9.9.9"}}}))

    monkeypatch.setattr(sys, "argv", [
        "run.py", "project",
        "--cfg", str(yaml_path),
        "--tool", "radiant",
        "--noenv",
    ])

    with patch("aurig_build.run.radiant_build", return_value=0) as mock_radiant:
        main()

    assert mock_radiant.call_count == 1
    # signature: (action, cfg_path, env, cfg)
    materialized_path = Path(mock_radiant.call_args[0][1])
    assert materialized_path != yaml_path, "expected materialized side-file"
    assert materialized_path.name.startswith(f".{yaml_path.stem}.merged.")

    with open(materialized_path, "r", encoding="utf-8") as f:
        materialized_cfg = _yaml.safe_load(f)

    # Overlay survived AND CLI override is in the side-file:
    assert materialized_cfg["tool"]["synth"]["version"] == "9.9.9", \
        "overlay value missing from materialized side-file"
    assert materialized_cfg["tool"]["synth"]["kind"] == "radiant", \
        "CLI --tool override not reflected in the materialized side-file (split-brain risk)"


def test_overlay_propagates_to_tcl_via_materialized_cfg(tmp_project, monkeypatch):
    """
    Codex P1 follow-up (PR #15 round 2): the overlay must reach TCL
    backends too. With <stem>.local.yaml present, main() must write a
    merged side-file and pass its path to the build dispatcher — not the
    base YAML path. Without this fix, Python sees the overlay while TCL
    re-reads the base and we get split-brain.
    """
    import yaml as _yaml
    yaml_path = tmp_project / "config" / "project.yaml"
    overlay_path = yaml_path.with_name(f"{yaml_path.stem}.local{yaml_path.suffix}")
    overlay_path.write_text(_yaml.dump({"tool": {"synth": {"version": "9.9.9"}}}))

    monkeypatch.setattr(sys, "argv", [
        "run.py", "project",
        "--cfg", str(yaml_path),
        "--noenv",
    ])

    with patch("aurig_build.run.vivado_build", return_value=0) as mock_vivado:
        result = main()

    assert result == 0
    assert mock_vivado.call_count == 1
    # vivado_build signature: (action, cfg_path, env, cfg)
    cfg_path_arg = mock_vivado.call_args[0][1]
    # cfg_path passed to the dispatcher must be the materialized merged
    # file (next to the base), NOT the base path itself.
    assert Path(cfg_path_arg) != yaml_path, (
        "dispatcher received the base YAML path; overlay would be invisible to TCL"
    )
    assert Path(cfg_path_arg).parent == yaml_path.parent, (
        "merged file must live next to base (project_root resolution)"
    )
    assert Path(cfg_path_arg).name.startswith(f".{yaml_path.stem}.merged.")


def test_cli_tool_override_propagates_to_cfg(tmp_project, monkeypatch):
    """
    Regression for PR #11 review (Copilot, high): `--tool <kind>` must
    rewrite cfg['tool']['synth']['kind'] before prepare_env() and the
    dispatcher read it. Without the override, the dispatcher branch would
    flip (e.g. to radiant_build) while prepare_env / radiant_build still
    saw the YAML's original kind (vivado in the tmp_project fixture) and
    bootstrap / probe the wrong vendor.
    """
    yaml_path = tmp_project / "config" / "project.yaml"
    # tmp_project is Vivado-flavored; --tool radiant overrides.

    monkeypatch.setattr(sys, "argv", [
        "run.py", "project",
        "--cfg", str(yaml_path),
        "--tool", "radiant",
        "--noenv",
    ])

    with patch("aurig_build.run.radiant_build", return_value=0) as mock_radiant, \
         patch("aurig_build.run.vivado_build", return_value=0) as mock_vivado:
        result = main()

        assert mock_radiant.call_count == 1, \
            "--tool radiant should dispatch to radiant_build"
        assert mock_vivado.call_count == 0, \
            "--tool radiant must NOT dispatch to vivado_build"

        # radiant_build signature: (action, cfg_path, env, cfg). The cfg it
        # receives must reflect the CLI override, not the YAML's "vivado".
        cfg_arg = mock_radiant.call_args[0][3]
        assert cfg_arg["tool"]["synth"]["kind"] == "radiant", (
            "CLI --tool override did not propagate into cfg; "
            f"got kind={cfg_arg['tool']['synth']['kind']!r}"
        )
        assert result == 0


# ============================================================================
# Sim dispatchers honor tool.sim.exe (§6 #8)
# ============================================================================

def _vivado_xsim_yaml(tmp_path, exe="vivado"):
    """Vivado YAML with `sim.kind = xsim` and a default TB so sim_xsim runs."""
    cfg = {
        "schema_version": "1",
        "project_name": "test_proj",
        "project_root": "..",
        "top": "test_top",
        "tool": {
            "synth": {"kind": "vivado", "version": "2023.1", "exe": "vivado"},
            "sim":   {"kind": "xsim",   "exe": exe},
        },
        "device": {"vendor": "xilinx", "family": "artix7", "part": "xc7a100t-1csg324"},
        "board":  {"xdc_files": ["constraints/pins.xdc"]},
        "file_sets": {"rtl": [{"lib": "work", "vhdl_std": "2008", "src": ["src/**/*.vhd"]}]},
        "include_dirs_global": [],
        "sim": {"default_top_tb": "tb_top"},
    }
    (tmp_path / "config").mkdir()
    yaml_path = tmp_path / "config" / "project.yaml"
    with open(yaml_path, "w") as f:
        yaml.dump(cfg, f)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "top.vhd").write_text("entity tb_top is end entity;")
    (tmp_path / "constraints").mkdir()
    (tmp_path / "constraints" / "pins.xdc").write_text("# placeholder\n")
    return yaml_path


def _questa_yaml(tmp_path, exe="vsim"):
    """Vivado-synth + Questa-sim YAML with a default TB."""
    cfg = {
        "schema_version": "1",
        "project_name": "test_proj",
        "project_root": "..",
        "top": "test_top",
        "tool": {
            "synth": {"kind": "vivado", "version": "2023.1", "exe": "vivado"},
            "sim":   {"kind": "questa", "exe": exe},
        },
        "device": {"vendor": "xilinx", "family": "artix7", "part": "xc7a100t-1csg324"},
        "board":  {"xdc_files": ["constraints/pins.xdc"]},
        "file_sets": {"rtl": [{"lib": "work", "vhdl_std": "2008", "src": ["src/**/*.vhd"]}]},
        "include_dirs_global": [],
        "sim": {"default_top_tb": "tb_top"},
    }
    (tmp_path / "config").mkdir()
    yaml_path = tmp_path / "config" / "project.yaml"
    with open(yaml_path, "w") as f:
        yaml.dump(cfg, f)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "top.vhd").write_text("entity tb_top is end entity;")
    (tmp_path / "constraints").mkdir()
    (tmp_path / "constraints" / "pins.xdc").write_text("# placeholder\n")
    return yaml_path


def test_sim_questa_honors_tool_sim_exe(tmp_path, monkeypatch):
    """
    §6 #8: sim_questa must invoke the exe configured in tool.sim.exe, not the
    hardcoded "vsim" literal. Verifies via absolute-path resolution.
    """
    import os
    custom_exe = tmp_path / "custom_vsim"
    custom_exe.write_text("# placeholder\n")
    os.chmod(custom_exe, 0o755)
    yaml_path = _questa_yaml(tmp_path, exe=str(custom_exe))

    monkeypatch.setattr(sys, "argv", [
        "run.py", "sim",
        "--cfg", str(yaml_path),
        "--noenv",
    ])

    with patch("subprocess.call", return_value=0) as mock_call:
        result = main()
        assert mock_call.call_count == 1
        cmd = mock_call.call_args[0][0]
        assert cmd[0] == str(custom_exe), \
            f"Expected configured vsim exe '{custom_exe}', got cmd[0]={cmd[0]}"
        assert result == 0


def test_sim_xsim_honors_tool_sim_exe(tmp_path, monkeypatch):
    """
    §6 #8: sim_xsim must invoke the exe configured in tool.sim.exe, not the
    hardcoded "vivado" literal.
    """
    import os
    custom_exe = tmp_path / "custom_vivado"
    custom_exe.write_text("# placeholder\n")
    os.chmod(custom_exe, 0o755)
    yaml_path = _vivado_xsim_yaml(tmp_path, exe=str(custom_exe))

    monkeypatch.setattr(sys, "argv", [
        "run.py", "sim",
        "--cfg", str(yaml_path),
        "--noenv",
    ])

    with patch("subprocess.call", return_value=0) as mock_call:
        result = main()
        assert mock_call.call_count == 1
        cmd = mock_call.call_args[0][0]
        assert cmd[0] == str(custom_exe), \
            f"Expected configured vivado(xsim) exe '{custom_exe}', got cmd[0]={cmd[0]}"
        assert result == 0


def test_sim_questa_falls_back_to_basename_when_abs_exe_missing(tmp_path, monkeypatch):
    """
    §6 #8 (basename fallback parity with synth side): a bogus absolute
    tool.sim.exe must degrade to a PATH lookup by basename.
    """
    bogus_abs = "/nonexistent/dir/custom_vsim"
    yaml_path = _questa_yaml(tmp_path, exe=bogus_abs)

    monkeypatch.setattr(sys, "argv", [
        "run.py", "sim",
        "--cfg", str(yaml_path),
        "--noenv",
    ])

    discovered = "/usr/local/bin/custom_vsim"
    def _which(name, *_args, **_kwargs):
        return discovered if name == "custom_vsim" else None

    with patch("shutil.which", side_effect=_which), \
         patch("subprocess.call", return_value=0) as mock_call:
        result = main()
        assert mock_call.call_count == 1
        cmd = mock_call.call_args[0][0]
        assert cmd[0] == discovered, \
            f"Expected fallback to PATH-resolved '{discovered}', got cmd[0]={cmd[0]}"
        assert result == 0


# ============================================================================
# Subprocess invocation guard
# ============================================================================

def test_no_real_subprocess_called_without_noenv(tmp_project, monkeypatch):
    """
    Verify subprocess.call is invoked exactly once for the build when
    --noenv is NOT specified, even though prepare_env tries to source scripts.
    """
    yaml_path = tmp_project / "config" / "project.yaml"

    monkeypatch.setattr(sys, "argv", [
        "run.py", "synth",
        "--cfg", str(yaml_path),
        # NO --noenv flag
    ])

    # Mock _source_script_into_env to avoid actual script sourcing
    # Also mock shutil.which and version checking (Vivado uses _vivado_version_via_tcl)
    import os
    with patch("aurig_build.run._source_script_into_env", return_value=os.environ.copy()), \
         patch("aurig_build.run._vivado_version_via_tcl", return_value="2023.1"), \
         patch("shutil.which", return_value="/usr/bin/vivado"), \
         patch("subprocess.call", return_value=0) as mock_call:

        result = main()

        # Should call subprocess.call exactly once (for the build command)
        assert mock_call.call_count == 1
        assert result == 0


# ============================================================================
# Multi-target integration
# ============================================================================

@pytest.mark.parametrize("target", ["project", "synth", "impl"])
def test_vivado_multi_target_integration(target, tmp_project, monkeypatch):
    """Verify multiple targets work end-to-end with mocked subprocess."""
    yaml_path = tmp_project / "config" / "project.yaml"

    monkeypatch.setattr(sys, "argv", [
        "run.py", target,
        "--cfg", str(yaml_path),
        "--noenv"
    ])

    with patch("shutil.which", return_value="/usr/bin/vivado"), \
         patch("subprocess.call", return_value=0) as mock_call:
        result = main()

        assert mock_call.call_count == 1
        cmd = mock_call.call_args[0][0]

        # Verify target is passed correctly
        assert target in cmd
        assert result == 0
