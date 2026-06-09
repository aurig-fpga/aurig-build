# SPDX-License-Identifier: Apache-2.0
# Copyright 2024-2026 LogiMentor S.r.l.

"""One test per legacy-alias row (handoff migration table): legacy form in,
canonical form out, plus the right mode (warn / error / silent coerce).

normalize() also must be idempotent (canonical in -> unchanged, no warns)
and OS-independent.
"""

from __future__ import annotations

import copy

import pytest

from aurig_build.schema import normalize
from aurig_build.schema.errors import ManifestError


def _base() -> dict:
    return {"schema_version": "1", "project_name": "p", "top": "top_entity"}


def _warned(warns, needle: str) -> bool:
    return any(needle in w for w in warns)


# ---------------------------------------------------------------------------
# warn rows
# ---------------------------------------------------------------------------

def test_top_path_form_splits_to_name_and_file():
    cfg = _base()
    cfg["top"] = "src/rtl/my_top.vhd"
    out, warns = normalize(cfg)
    assert out["top"] == "my_top"
    assert out["top_file"] == "src/rtl/my_top.vhd"
    assert _warned(warns, "entity/module name")


@pytest.mark.parametrize("key", ["env_script", "bin_dir"])
def test_synth_path_string_becomes_os_keyed(key):
    cfg = _base()
    cfg["tool"] = {"synth": {"kind": "vivado", key: "/opt/tool/settings.sh"}}
    out, warns = normalize(cfg)
    assert out["tool"]["synth"][key] == {
        "linux": "/opt/tool/settings.sh",
        "windows": "/opt/tool/settings.sh",
    }
    assert _warned(warns, f"tool.synth.{key}")


def test_sim_path_string_becomes_os_keyed():
    cfg = _base()
    cfg["tool"] = {"sim": {"kind": "questa", "env_script": "/opt/q/env.sh"}}
    out, warns = normalize(cfg)
    assert out["tool"]["sim"]["env_script"] == {
        "linux": "/opt/q/env.sh",
        "windows": "/opt/q/env.sh",
    }


def test_sim_kind_vunit_becomes_framework():
    cfg = _base()
    cfg["tool"] = {"sim": {"kind": "vunit"}}
    out, warns = normalize(cfg)
    assert "kind" not in out["tool"]["sim"]
    assert out["tool"]["sim"]["framework"] == "vunit"
    assert _warned(warns, "framework: ")


def test_sim_kind_questasim_remaps_to_questa():
    cfg = _base()
    cfg["tool"] = {"sim": {"kind": "questasim"}}
    out, warns = normalize(cfg)
    assert out["tool"]["sim"]["kind"] == "questa"
    assert out["tool"]["sim"]["framework"] == "direct"


def test_sim_kind_vivado_remaps_to_xsim():
    cfg = _base()
    cfg["tool"] = {"sim": {"kind": "vivado"}}
    out, warns = normalize(cfg)
    assert out["tool"]["sim"]["kind"] == "xsim"
    assert out["tool"]["sim"]["framework"] == "direct"


def test_sim_kind_without_framework_gets_direct():
    cfg = _base()
    cfg["tool"] = {"sim": {"kind": "ghdl"}}
    out, warns = normalize(cfg)
    assert out["tool"]["sim"]["kind"] == "ghdl"
    assert out["tool"]["sim"]["framework"] == "direct"
    assert _warned(warns, "without a framework")


@pytest.mark.parametrize("legacy,canonical", [("altera", "intel"), ("microsemi", "microchip")])
def test_device_vendor_remap(legacy, canonical):
    cfg = _base()
    cfg["device"] = {"vendor": legacy, "family": "f", "part": "p"}
    out, warns = normalize(cfg)
    assert out["device"]["vendor"] == canonical
    assert _warned(warns, f"'{legacy}'")


def test_file_sets_ip_folds_into_rtl():
    cfg = _base()
    cfg["file_sets"] = {
        "rtl": [{"lib": "work", "src": ["a.vhd"]}],
        "ip": [{"lib": "work", "src": ["gen/ip.vhd"]}],
    }
    out, warns = normalize(cfg)
    assert "ip" not in out["file_sets"]
    assert {"lib": "work", "src": ["gen/ip.vhd"]} in out["file_sets"]["rtl"]
    assert _warned(warns, "file_sets.ip")


def test_file_sets_constraints_route_by_extension():
    cfg = _base()
    cfg["file_sets"] = {"constraints": ["pins.xdc", "timing.sdc"]}
    out, warns = normalize(cfg)
    assert "constraints" not in out["file_sets"]
    assert out["board"]["xdc_files"] == ["pins.xdc"]
    assert out["board"]["sdc_files"] == ["timing.sdc"]


def test_libraries_renamed_to_external_libraries():
    cfg = _base()
    cfg["libraries"] = {"unisim": "ignore"}
    out, warns = normalize(cfg)
    assert "libraries" not in out
    assert out["external_libraries"] == {"unisim": "ignore"}


def test_env_generics_moved_to_top_level():
    cfg = _base()
    cfg["env"] = {"PATH_HINT": "x", "generics": {"WIDTH": 8}}
    out, warns = normalize(cfg)
    assert "generics" not in out["env"]
    assert out["generics"] == {"WIDTH": 8}


def test_sim_top_tb_renamed():
    cfg = _base()
    cfg["sim"] = {"top_tb": "tb_top"}
    out, warns = normalize(cfg)
    assert out["sim"]["default_top_tb"] == "tb_top"
    assert "top_tb" not in out["sim"]


def test_sim_options_renamed():
    cfg = _base()
    cfg["sim"] = {"sim_options": "-foo"}
    out, warns = normalize(cfg)
    assert out["sim"]["options"] == "-foo"
    assert "sim_options" not in out["sim"]


def test_sim_tb_folder_becomes_file_set():
    cfg = _base()
    cfg["sim"] = {"tb_folder": "tb/"}
    out, warns = normalize(cfg)
    assert "tb_folder" not in out["sim"]
    sim_sets = out["file_sets"]["sim"]
    assert len(sim_sets) == 1
    assert sim_sets[0]["lib"] == "tb"
    assert any(s.startswith("tb/**/") for s in sim_sets[0]["src"])


def test_sim_tb_lib_qualifies_default_top_tb():
    cfg = _base()
    cfg["sim"] = {"default_top_tb": "dut_tb", "tb_lib": "mytb"}
    out, warns = normalize(cfg)
    assert "tb_lib" not in out["sim"]
    assert out["sim"]["default_top_tb"] == "mytb.dut_tb"


def test_quartus_qip_files_become_ip_cores():
    cfg = _base()
    cfg["quartus"] = {"qip_files": ["ip/a.qip", "ip/b.qip"]}
    out, warns = normalize(cfg)
    assert "qip_files" not in out["quartus"]
    assert {"kind": "qip", "src": "ip/a.qip"} in out["ip_cores"]
    assert {"kind": "qip", "src": "ip/b.qip"} in out["ip_cores"]


def test_block_design_enabled_becomes_ip_core():
    cfg = _base()
    cfg["features"] = {"block_design": {"enabled": True, "tcl": "bd/design.tcl"}}
    out, warns = normalize(cfg)
    assert {"kind": "bd", "src": "bd/design.tcl"} in out["ip_cores"]
    assert "features" not in out


def test_block_design_disabled_is_dropped():
    cfg = _base()
    cfg["features"] = {"block_design": {"enabled": False, "tcl": "bd/design.tcl"}}
    out, warns = normalize(cfg)
    assert "ip_cores" not in out
    assert _warned(warns, "disabled")


# ---------------------------------------------------------------------------
# error rows
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("dead", ["ise", "precision", "libero"])
def test_dead_synth_kind_errors(dead):
    cfg = _base()
    cfg["tool"] = {"synth": {"kind": dead}}
    with pytest.raises(ManifestError):
        normalize(cfg)


def test_sim_kind_isim_errors():
    cfg = _base()
    cfg["tool"] = {"sim": {"kind": "isim"}}
    with pytest.raises(ManifestError):
        normalize(cfg)


def test_ucf_constraints_error():
    cfg = _base()
    cfg["file_sets"] = {"constraints": ["legacy.ucf"]}
    with pytest.raises(ManifestError):
        normalize(cfg)


def test_device_speed_errors_with_guidance():
    cfg = _base()
    cfg["device"] = {"vendor": "xilinx", "family": "artix7", "part": "xc7a100t", "speed": "-1"}
    with pytest.raises(ManifestError) as exc:
        normalize(cfg)
    assert any("device.part" in m for m in exc.value.messages)


# ---------------------------------------------------------------------------
# coerce + idempotency
# ---------------------------------------------------------------------------

def test_vhdl_std_int_coerced_silently():
    cfg = _base()
    cfg["file_sets"] = {"rtl": [{"lib": "work", "vhdl_std": 2008, "src": ["a.vhd"]}]}
    out, warns = normalize(cfg)
    assert out["file_sets"]["rtl"][0]["vhdl_std"] == "2008"
    assert warns == []  # coercion is silent


def test_normalize_is_idempotent():
    canonical = {
        "schema_version": "1",
        "project_name": "p",
        "top": "top_entity",
        "tool": {
            "synth": {"kind": "vivado", "env_script": {"linux": "a", "windows": "b"}},
            "sim": {"framework": "direct", "kind": "questa"},
        },
        "device": {"vendor": "intel", "family": "max10", "part": "10M50"},
        "board": {"xdc_files": ["pins.xdc"]},
        "external_libraries": {"unisim": "ignore"},
        "generics": {"WIDTH": 8},
        "file_sets": {"rtl": [{"lib": "work", "vhdl_std": "2008", "src": ["a.vhd"]}]},
    }
    once, w1 = normalize(canonical)
    assert w1 == []
    assert once == canonical
    twice, w2 = normalize(once)
    assert w2 == []
    assert twice == once
