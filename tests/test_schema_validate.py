# SPDX-License-Identifier: Apache-2.0
# Copyright 2024-2026 LogiMentor S.r.l.

"""Validator behaviour: required keys, enums, unknown-key WARNs, and the
full read -> overlay -> normalize -> validate pipeline on a real manifest.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from aurig_build.schema import normalize, validate
from aurig_build.schema.errors import ManifestError
from aurig_build.run import resolve_manifest

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "tests" / "data"


def _minimal() -> dict:
    return {"schema_version": "1", "project_name": "p", "top": "t"}


# ---------------------------------------------------------------------------
# Required keys / enums
# ---------------------------------------------------------------------------

def test_minimal_canonical_passes():
    assert validate(_minimal()) == []


def test_missing_schema_version_errors():
    cfg = _minimal()
    del cfg["schema_version"]
    with pytest.raises(ManifestError) as exc:
        validate(cfg)
    assert any("schema_version" in m for m in exc.value.messages)


def test_wrong_schema_version_errors():
    cfg = _minimal()
    cfg["schema_version"] = "2"
    with pytest.raises(ManifestError):
        validate(cfg)


@pytest.mark.parametrize("missing", ["project_name", "top"])
def test_missing_required_root_key_errors(missing):
    cfg = _minimal()
    del cfg[missing]
    with pytest.raises(ManifestError):
        validate(cfg)


def test_bad_synth_kind_errors():
    cfg = _minimal()
    cfg["tool"] = {"synth": {"kind": "altium"}}
    with pytest.raises(ManifestError):
        validate(cfg)


def test_device_requires_vendor_family_part():
    cfg = _minimal()
    cfg["device"] = {"vendor": "xilinx"}
    with pytest.raises(ManifestError) as exc:
        validate(cfg)
    assert any("family" in m or "part" in m for m in exc.value.messages)


def test_sim_direct_requires_kind():
    cfg = _minimal()
    cfg["tool"] = {"sim": {"framework": "direct"}}
    with pytest.raises(ManifestError):
        validate(cfg)


def test_sim_vunit_allows_missing_kind():
    cfg = _minimal()
    cfg["tool"] = {"sim": {"framework": "vunit"}}
    assert validate(cfg) == []


# ---------------------------------------------------------------------------
# Unknown keys -> WARN (never error)
# ---------------------------------------------------------------------------

def test_unknown_top_level_key_warns():
    cfg = _minimal()
    cfg["totally_made_up"] = 123
    warns = validate(cfg)
    assert any("totally_made_up" in w and "unknown key" in w for w in warns)


def test_unknown_section_level_key_warns():
    cfg = _minimal()
    cfg["tool"] = {"synth": {"kind": "vivado"}, "mystery": {}}
    warns = validate(cfg)
    assert any("tool.mystery" in w and "unknown key" in w for w in warns)


# ---------------------------------------------------------------------------
# Real manifest + full pipeline
# ---------------------------------------------------------------------------

VENDOR_EXPECTATIONS = [
    pytest.param("vivado_min", "vivado", "xdc_files", id="vivado_min"),
    pytest.param("quartus_min", "quartus", "sdc_files", id="quartus_min"),
    pytest.param("diamond_min", "diamond", "lpf_files", id="diamond_min"),
    pytest.param("radiant_min", "radiant", "pdc_files", id="radiant_min"),
]


@pytest.mark.parametrize(
    "vendor_min,expected_kind,expected_constraint_key", VENDOR_EXPECTATIONS
)
def test_real_vendor_min_normalize_validate_green(
    vendor_min, expected_kind, expected_constraint_key
):
    """Each vendor_min fixture must normalize + validate green, declare the
    right synth kind and constraint key, emit no normalization warnings, and
    have every referenced src/ and constraint file resolvable via project_root.
    """
    fixture_root = DATA_DIR / vendor_min
    manifest = fixture_root / "config" / "project.yaml"
    raw = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    cfg, warns = normalize(raw)

    assert validate(cfg) == []
    # canonical fixtures carry no legacy aliases -> normalize is silent.
    assert warns == []
    # vhdl_std int is coerced to string permanently and silently.
    assert cfg["file_sets"]["rtl"][0]["vhdl_std"] == "2008"
    assert cfg["tool"]["synth"]["kind"] == expected_kind
    assert expected_constraint_key in cfg.get("board", {})

    project_root = (manifest.parent / cfg.get("project_root", "..")).resolve()
    for entry in cfg["file_sets"]["rtl"]:
        for src in entry["src"]:
            assert (project_root / src).exists(), f"src not found: {src}"
    for constraint in cfg["board"][expected_constraint_key]:
        assert (project_root / constraint).exists(), (
            f"constraint not found: {constraint}"
        )


def test_overlay_normalize_validate_compose(tmp_path):
    base = tmp_path / "project.yaml"
    base.write_text(yaml.dump({
        "schema_version": "1",
        "project_name": "compose",
        "top": "top",
        "tool": {"synth": {"kind": "vivado", "version": "2023.1"}},
        "device": {"vendor": "xilinx", "family": "artix7", "part": "xc7a100t"},
        "file_sets": {"rtl": [{"lib": "work", "vhdl_std": 2008, "src": ["src/top.vhd"]}]},
    }))
    overlay = tmp_path / "project.local.yaml"
    overlay.write_text(yaml.dump({"tool": {"synth": {"version": "2024.1"}}}))

    cfg, warns = resolve_manifest(base)

    # overlay merged
    assert cfg["tool"]["synth"]["version"] == "2024.1"
    # normalize coerced the int vhdl_std
    assert cfg["file_sets"]["rtl"][0]["vhdl_std"] == "2008"
    # validation produced no errors (resolve_manifest would have raised)
