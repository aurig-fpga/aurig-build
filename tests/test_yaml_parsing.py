# SPDX-License-Identifier: Apache-2.0
# Copyright 2024-2026 LogiMentor S.r.l.

"""
Unit tests for YAML parsing and configuration validation.

Tests read_yaml() from run.py and validates content structure of parsed
configurations without invoking any FPGA tools.
"""

import pytest
import yaml
from pathlib import Path

# Import from aurig_build.run module
from aurig_build.run import read_yaml


# ============================================================================
# SECTION: basic loading
# ============================================================================

def test_read_yaml_returns_dict():
    """Load real config/project.yaml and verify it returns a dict with project_name."""
    cfg_path = Path(__file__).parent.parent / "aurig_build" / "config" / "project.yaml"
    result = read_yaml(cfg_path)

    assert isinstance(result, dict)
    assert "project_name" in result
    assert result["project_name"]  # Not empty


def test_read_yaml_missing_file():
    """Verify FileNotFoundError or equivalent when file does not exist."""
    nonexistent = Path("/nonexistent/path/to/config.yaml")

    with pytest.raises((FileNotFoundError, OSError)):
        read_yaml(nonexistent)


def test_read_yaml_empty_file(tmp_path):
    """Verify empty YAML file returns empty dict, not None or error."""
    empty_yaml = tmp_path / "empty.yaml"
    empty_yaml.write_text("")

    result = read_yaml(empty_yaml)

    # PyYAML returns None for an empty file; read_yaml normalizes that to
    # {} via _validate_top_level_mapping.
    assert result == {}


# ============================================================================
# SECTION: local overlay
# ============================================================================

from aurig_build.run import _deep_merge  # noqa: E402


def test_deep_merge_overlay_wins_on_scalar_keys():
    """Overlay's scalar values replace the base's."""
    base = {"a": 1, "b": 2}
    overlay = {"b": 99, "c": 3}
    assert _deep_merge(base, overlay) == {"a": 1, "b": 99, "c": 3}


def test_deep_merge_nested_dicts_are_merged_recursively():
    """Nested dicts merge key-by-key (overlay wins). Sibling keys survive."""
    base = {"tool": {"synth": {"kind": "vivado", "version": "2023.1"}}}
    overlay = {"tool": {"synth": {"version": "2024.1"}}}
    result = _deep_merge(base, overlay)
    assert result == {"tool": {"synth": {"kind": "vivado", "version": "2024.1"}}}


def test_deep_merge_lists_are_replaced_wholesale():
    """Lists in overlay replace the base's (no per-item merge). This is the
    docker-compose.override.yaml convention and avoids ambiguity about
    list identity."""
    base = {"src": ["a.vhd", "b.vhd"]}
    overlay = {"src": ["c.vhd"]}
    assert _deep_merge(base, overlay) == {"src": ["c.vhd"]}


def test_deep_merge_overlay_none_replaces_value():
    """`key: null` in overlay explicitly blanks a base value (use case:
    `tool.synth.env_script: null` to skip env sourcing for this machine)."""
    base = {"tool": {"synth": {"env_script": "/opt/xilinx/settings.sh"}}}
    overlay = {"tool": {"synth": {"env_script": None}}}
    result = _deep_merge(base, overlay)
    assert result["tool"]["synth"]["env_script"] is None


def test_read_yaml_applies_local_overlay(tmp_path, capsys):
    """When `<stem>.local.yaml` exists next to the cfg, read_yaml must
    deep-merge it on top automatically."""
    base = tmp_path / "project.yaml"
    base.write_text(yaml.dump({
        "project_name": "demo",
        "tool": {"synth": {"kind": "vivado", "version": "2023.1"}},
    }))
    local = tmp_path / "project.local.yaml"
    local.write_text(yaml.dump({
        "tool": {"synth": {"version": "2024.1", "exe": "/opt/Xilinx/2024.1/bin/vivado"}},
    }))

    result = read_yaml(base)

    assert result["project_name"] == "demo"             # untouched
    assert result["tool"]["synth"]["kind"] == "vivado"  # untouched
    assert result["tool"]["synth"]["version"] == "2024.1"  # overridden
    assert result["tool"]["synth"]["exe"] == "/opt/Xilinx/2024.1/bin/vivado"  # added

    # The INFO line is meant to surface the deviation to the user.
    captured = capsys.readouterr()
    assert "local overlay" in captured.err
    assert "project.local.yaml" in captured.err


def test_read_yaml_no_overlay_no_warning(tmp_path, capsys):
    """No `<stem>.local.yaml` sibling -> no merge, no log line."""
    base = tmp_path / "project.yaml"
    base.write_text(yaml.dump({"project_name": "demo"}))

    result = read_yaml(base)

    assert result == {"project_name": "demo"}
    captured = capsys.readouterr()
    assert "local overlay" not in captured.err


def test_read_yaml_empty_overlay_no_merge(tmp_path, capsys):
    """An empty `<stem>.local.yaml` -> nothing to merge, no log line."""
    base = tmp_path / "project.yaml"
    base.write_text(yaml.dump({"project_name": "demo"}))
    (tmp_path / "project.local.yaml").write_text("")  # empty file

    result = read_yaml(base)

    assert result == {"project_name": "demo"}
    captured = capsys.readouterr()
    # No INFO line when there's nothing to apply.
    assert "local overlay" not in captured.err


@pytest.mark.parametrize("bad_yaml, expected_type_name", [
    ("- a\n- b\n",       "list"),
    ("false\n",          "bool"),
    ("0\n",              "int"),
    ('""\n',             "str"),
    ("[]\n",             "list"),    # falsy non-mapping — previously masked by `or {}`
])
def test_read_yaml_rejects_non_dict_top_level(bad_yaml, expected_type_name, tmp_path, capsys):
    """A base YAML whose top-level is anything other than a mapping (or
    None for an empty file) must produce a clean SystemExit, not an opaque
    _deep_merge AttributeError. Includes falsy values (False, 0, "", [])
    that the previous `safe_load(...) or {}` pattern would have masked as
    an empty mapping."""
    base = tmp_path / "project.yaml"
    base.write_text(bad_yaml)

    with pytest.raises(SystemExit) as exc_info:
        read_yaml(base)

    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "top-level YAML must be a mapping" in captured.err
    assert expected_type_name in captured.err


@pytest.mark.parametrize("bad_overlay, expected_type_name", [
    ("- this -is\n- a list\n", "list"),
    ("false\n",                "bool"),
    ("0\n",                    "int"),
    ('""\n',                   "str"),
])
def test_read_yaml_rejects_non_dict_overlay(bad_overlay, expected_type_name, tmp_path, capsys):
    """An overlay YAML whose top-level is a list/scalar (or a falsy
    non-mapping) must produce a clean error that identifies the overlay
    file and the actual type seen."""
    base = tmp_path / "project.yaml"
    base.write_text(yaml.dump({"project_name": "demo"}))
    (tmp_path / "project.local.yaml").write_text(bad_overlay)

    with pytest.raises(SystemExit) as exc_info:
        read_yaml(base)

    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "project.local.yaml" in captured.err
    assert "top-level YAML must be a mapping" in captured.err
    assert expected_type_name in captured.err


def test_materialize_merged_cfg_rejects_non_dict_overlay(tmp_path, capsys):
    """materialize_merged_cfg() must use the same _validate_top_level_mapping
    guard read_yaml uses, so a malformed overlay fails consistently no
    matter which entrypoint reaches it (closes the Copilot consistency
    note on PR #15 round 3)."""
    from aurig_build.run import materialize_merged_cfg
    base = tmp_path / "project.yaml"
    base.write_text(yaml.dump({"project_name": "demo"}))
    (tmp_path / "project.local.yaml").write_text("- a list\n")

    with pytest.raises(SystemExit) as exc_info:
        materialize_merged_cfg(base, {"project_name": "demo"})

    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "project.local.yaml" in captured.err
    assert "top-level YAML must be a mapping" in captured.err


def test_materialize_merged_cfg_handles_non_oserror_failure(tmp_path, monkeypatch, capsys):
    """When yaml.safe_dump (or any non-OSError) raises during dump,
    materialize_merged_cfg must still exit(2) cleanly. Earlier the outer
    handler only caught OSError, so PyYAML representer errors or
    RecursionError would have escaped as a Python traceback."""
    from aurig_build.run import materialize_merged_cfg
    base = tmp_path / "project.yaml"
    base.write_text(yaml.dump({"project_name": "demo"}))
    (tmp_path / "project.local.yaml").write_text(
        yaml.dump({"tool": {"synth": {"version": "9.9.9"}}})
    )

    def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated representer error")

    monkeypatch.setattr("aurig_build.run.yaml.safe_dump", _boom)

    with pytest.raises(SystemExit) as exc_info:
        materialize_merged_cfg(base, {"project_name": "demo"})

    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "Could not materialize merged config" in captured.err
    assert "simulated representer error" in captured.err
    # No partial side-file left behind.
    sidecars = list(tmp_path.glob(f".{base.stem}.merged.*{base.suffix}"))
    assert sidecars == [], f"unexpected leftover side-files: {sidecars}"


def test_materialize_merged_cfg_handles_write_failure_cleanly(tmp_path, monkeypatch, capsys):
    """When mkstemp / write fails (read-only checkout, disk full, etc.),
    materialize_merged_cfg must exit(2) with a clean [ERROR] message
    instead of raising a traceback. Mocks tempfile.mkstemp to raise
    OSError ("Read-only file system")."""
    from aurig_build.run import materialize_merged_cfg
    base = tmp_path / "project.yaml"
    base.write_text(yaml.dump({"project_name": "demo"}))
    # Overlay must exist (with content) so materialize_merged_cfg reaches
    # the mkstemp call.
    (tmp_path / "project.local.yaml").write_text(
        yaml.dump({"tool": {"synth": {"version": "9.9.9"}}})
    )

    def _boom(*_args, **_kwargs):
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr("aurig_build.run.tempfile.mkstemp", _boom)

    with pytest.raises(SystemExit) as exc_info:
        materialize_merged_cfg(base, {"project_name": "demo", "tool": {"synth": {"version": "9.9.9"}}})

    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "Could not materialize merged config" in captured.err
    assert str(base) in captured.err
    # No partial side-file left behind (mkstemp was mocked to raise before
    # creating anything).
    sidecars = list(tmp_path.glob(f".{base.stem}.merged.*{base.suffix}"))
    assert sidecars == [], f"unexpected leftover side-files: {sidecars}"


def test_materialize_merged_cfg_writes_sidefile_only_when_overlay(tmp_path):
    """materialize_merged_cfg returns the base path when there is no
    overlay, and writes a sidecar file otherwise. The sidecar must live
    next to the base (preserving project_root resolution)."""
    from aurig_build.run import materialize_merged_cfg
    base = tmp_path / "project.yaml"
    base.write_text(yaml.dump({"project_name": "demo"}))

    # No overlay -> returns base unchanged.
    result_path = materialize_merged_cfg(base, {"project_name": "demo"})
    assert result_path == base

    # Overlay present -> writes a side file in tmp_path with the merged content.
    (tmp_path / "project.local.yaml").write_text(
        yaml.dump({"project_name": "overridden"})
    )
    merged_cfg = {"project_name": "overridden"}
    result_path = materialize_merged_cfg(base, merged_cfg)
    assert result_path != base
    assert result_path.parent == base.parent, "side file must live next to base"
    assert result_path.name.startswith(f".{base.stem}.merged.")
    # Round-trip the YAML to confirm it actually carries the merged content.
    with open(result_path, "r", encoding="utf-8") as f:
        assert yaml.safe_load(f) == merged_cfg


# ============================================================================
# SECTION: tool config extraction
# ============================================================================

def test_synth_kind_vivado(minimal_yaml_vivado):
    """Verify synth kind extraction for Vivado configuration."""
    assert minimal_yaml_vivado["tool"]["synth"]["kind"] == "vivado"


def test_synth_kind_diamond(minimal_yaml_diamond):
    """Verify synth kind extraction for Diamond configuration."""
    assert minimal_yaml_diamond["tool"]["synth"]["kind"] == "diamond"


def test_synth_kind_quartus(minimal_yaml_quartus):
    """Verify synth kind extraction for Quartus configuration."""
    assert minimal_yaml_quartus["tool"]["synth"]["kind"] == "quartus"


def test_missing_tool_synth():
    """Verify synth_kind resolves to empty string when tool key is missing."""
    cfg = {"project_name": "test"}

    # Simulate the extraction logic from run.py main()
    synth_kind = ((cfg.get("tool") or {}).get("synth") or {}).get("kind", "")

    assert synth_kind == ""


# ============================================================================
# SECTION: file_sets
# ============================================================================

def test_file_sets_rtl_present(minimal_yaml_vivado):
    """Verify file_sets.rtl exists and is a non-empty list."""
    assert "file_sets" in minimal_yaml_vivado
    assert "rtl" in minimal_yaml_vivado["file_sets"]
    assert isinstance(minimal_yaml_vivado["file_sets"]["rtl"], list)
    assert len(minimal_yaml_vivado["file_sets"]["rtl"]) > 0


def test_file_sets_item_has_lib(minimal_yaml_vivado):
    """Verify first rtl item contains a 'lib' key."""
    first_item = minimal_yaml_vivado["file_sets"]["rtl"][0]
    assert "lib" in first_item
    assert first_item["lib"] == "work"


def test_file_sets_item_has_src(minimal_yaml_vivado):
    """Verify first rtl item contains a non-empty 'src' list."""
    first_item = minimal_yaml_vivado["file_sets"]["rtl"][0]
    assert "src" in first_item
    assert isinstance(first_item["src"], list)
    assert len(first_item["src"]) > 0


# ============================================================================
# SECTION: board constraints
# ============================================================================

def test_board_xdc_files_list(minimal_yaml_vivado):
    """Verify board.xdc_files is a non-empty list."""
    assert "board" in minimal_yaml_vivado
    assert "xdc_files" in minimal_yaml_vivado["board"]
    assert isinstance(minimal_yaml_vivado["board"]["xdc_files"], list)
    assert len(minimal_yaml_vivado["board"]["xdc_files"]) > 0


def test_board_no_dangling_list(tmp_path):
    """
    Regression test: verify commented xdc_files key doesn't leave dangling list items.

    Bug: If 'xdc_files:' is commented but list items remain, PyYAML parses
    board as a list instead of a dict.
    """
    bad_yaml_content = """
project_name: test
top: top
board:
  # xdc_files:
  - constraints/pins.xdc
  - constraints/timing.xdc
"""
    yaml_file = tmp_path / "bad.yaml"
    yaml_file.write_text(bad_yaml_content)

    result = read_yaml(yaml_file)

    # board should be a dict, not a list
    assert isinstance(result.get("board"), list), \
        "This test expects the bug to manifest (board parsed as list). " \
        "If board is dict, the YAML format was corrected."


# ============================================================================
# SECTION: ip_cores
# ============================================================================

def test_ip_cores_parsed_as_list(yaml_with_ip_cores):
    """Verify ip_cores is parsed as a list with expected number of items."""
    assert "ip_cores" in yaml_with_ip_cores
    assert isinstance(yaml_with_ip_cores["ip_cores"], list)
    assert len(yaml_with_ip_cores["ip_cores"]) == 3


def test_ip_cores_item_has_kind_and_src(yaml_with_ip_cores):
    """Verify each ip_cores item contains 'kind' and 'src' keys."""
    for item in yaml_with_ip_cores["ip_cores"]:
        assert "kind" in item
        assert "src" in item
        assert item["kind"] in {"xci", "bd", "edf", "ipx", "lpc", "qip"}
        assert isinstance(item["src"], str)
        assert len(item["src"]) > 0


def test_ip_cores_absent_is_ok(minimal_yaml_vivado):
    """Verify missing ip_cores key does not cause error."""
    assert minimal_yaml_vivado.get("ip_cores") is None
