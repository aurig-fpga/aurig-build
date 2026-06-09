# SPDX-License-Identifier: Apache-2.0
# Copyright 2024-2026 LogiMentor S.r.l.

"""Legacy-alias normalization for the AURIG manifest (handoff table 2).

``normalize(cfg)`` rewrites every legacy form to its canonical v1 shape and
returns ``(canonical_cfg, warnings)``. Three modes:

* ``warn``   - accepted, rewritten to canonical, one deprecation WARN logged.
* ``error``  - removed with no canonical target: raises ``ManifestError``.
* ``coerce`` - accepted permanently, silently normalized (no deprecation).

The function is **idempotent** (canonical input returns unchanged with zero
warnings) and **OS-independent / deterministic** (it never branches on the
host OS), so re-running it on the materialized side-file is a no-op and a
cross-tool fixture corpus is reproducible.
"""

from __future__ import annotations

import copy
import re

from .errors import ManifestError

_SCHEMA_VERSION_V1 = re.compile(r"^1(\.0){1,2}$")

_HDL_EXTS = (".vhd", ".vhdl", ".v", ".sv", ".vh", ".svh")
_LEGACY_SIM_KIND_REMAP = {"questasim": "questa", "vivado": "xsim"}
_DEAD_SYNTH_KINDS = {"ise", "precision", "libero"}
_CONSTRAINT_EXT_TO_BOARD = {
    ".xdc": "xdc_files",
    ".sdc": "sdc_files",
    ".lpf": "lpf_files",
    ".pdc": "pdc_files",
}


def normalize(cfg: dict) -> tuple[dict, list[str]]:
    cfg = copy.deepcopy(cfg)
    warns: list[str] = []

    _canonicalize_schema_version(cfg)
    _normalize_top(cfg, warns)
    _normalize_tool(cfg, warns)
    _normalize_device(cfg, warns)
    _normalize_board_and_file_sets(cfg, warns)
    _normalize_libraries(cfg, warns)
    _normalize_env_generics(cfg, warns)
    _normalize_sim(cfg, warns)
    _normalize_quartus_and_features(cfg, warns)
    _coerce_vhdl_std(cfg)

    return cfg, warns


def _warn(warns: list[str], msg: str) -> None:
    warns.append(f"[WARN] {msg}")


def _is_path_form(top: str) -> bool:
    return "/" in top or "\\" in top or top.lower().endswith(_HDL_EXTS)


def _normalize_top(cfg: dict, warns: list[str]) -> None:
    top = cfg.get("top")
    if not isinstance(top, str) or not _is_path_form(top):
        return
    base = top.replace("\\", "/").rsplit("/", 1)[-1]
    stem = base.rsplit(".", 1)[0] if "." in base else base
    cfg.setdefault("top_file", top)
    cfg["top"] = stem
    _warn(
        warns,
        f"top: path form '{top}' is deprecated; set 'top' to the real "
        f"entity/module name (guessed '{stem}' from the file name) and keep "
        f"the path in 'top_file'.",
    )


def _os_keyify(parent: dict, key: str, ctx: str, warns: list[str]) -> None:
    val = parent.get(key)
    if isinstance(val, str):
        parent[key] = {"linux": val, "windows": val}
        _warn(
            warns,
            f"{ctx}.{key}: bare string is deprecated; use an OS-keyed object "
            f"{{linux, windows}}. Applied to BOTH keys — set them explicitly "
            f"to resolve the OS ambiguity.",
        )


def _normalize_tool(cfg: dict, warns: list[str]) -> None:
    tool = cfg.get("tool")
    if not isinstance(tool, dict):
        return

    synth = tool.get("synth")
    if isinstance(synth, dict):
        kind = synth.get("kind")
        if kind in _DEAD_SYNTH_KINDS:
            raise ManifestError(
                f"tool.synth.kind: '{kind}' is removed and has no canonical "
                f"backend. Supported: vivado, quartus, diamond, radiant."
            )
        _os_keyify(synth, "env_script", "tool.synth", warns)
        _os_keyify(synth, "bin_dir", "tool.synth", warns)

    sim = tool.get("sim")
    if isinstance(sim, dict):
        _normalize_sim_engine(sim, warns)
        _os_keyify(sim, "env_script", "tool.sim", warns)
        _os_keyify(sim, "bin_dir", "tool.sim", warns)


def _normalize_sim_engine(sim: dict, warns: list[str]) -> None:
    kind = sim.get("kind")
    if kind is None:
        return
    if kind == "isim":
        raise ManifestError(
            "tool.sim.kind: 'isim' is removed and has no canonical target. "
            "Use a supported engine (ghdl, nvc, modelsim, questa, active-hdl, xsim)."
        )
    if kind == "vunit":
        sim.pop("kind", None)
        sim["framework"] = "vunit"
        _warn(
            warns,
            "tool.sim.kind: 'vunit' is deprecated; set 'tool.sim.framework: "
            "vunit' instead (engine left unset).",
        )
        return
    if kind in _LEGACY_SIM_KIND_REMAP:
        canonical = _LEGACY_SIM_KIND_REMAP[kind]
        sim["kind"] = canonical
        sim.setdefault("framework", "direct")
        _warn(
            warns,
            f"tool.sim.kind: '{kind}' is deprecated; use framework: direct "
            f"with kind: {canonical}.",
        )
        return
    if "framework" not in sim:
        sim["framework"] = "direct"
        _warn(
            warns,
            f"tool.sim.kind: '{kind}' without a framework is deprecated; "
            f"set 'tool.sim.framework: direct' explicitly.",
        )


def _normalize_device(cfg: dict, warns: list[str]) -> None:
    device = cfg.get("device")
    if not isinstance(device, dict):
        return
    vendor = device.get("vendor")
    vendor_remap = {"altera": "intel", "microsemi": "microchip"}
    if vendor in vendor_remap:
        canonical = vendor_remap[vendor]
        device["vendor"] = canonical
        _warn(
            warns,
            f"device.vendor: '{vendor}' is deprecated; use '{canonical}'.",
        )
        vendor = canonical

    if "speed" in device:
        raise ManifestError(
            "device.speed is deprecated and the speed-grade position inside a "
            f"part string is vendor-specific and not safely derivable "
            f"(vendor='{vendor}'). Embed the speed grade directly into "
            "device.part (e.g. xilinx 'xc7a100t-1csg324') and remove device.speed."
        )


def _normalize_board_and_file_sets(cfg: dict, warns: list[str]) -> None:
    file_sets = cfg.get("file_sets")
    if isinstance(file_sets, dict):
        if "ip" in file_sets:
            ip = file_sets.pop("ip")
            rtl = file_sets.setdefault("rtl", [])
            if isinstance(ip, list):
                rtl.extend(ip)
            _warn(
                warns,
                "file_sets.ip is deprecated; pre-generated IP HDL is plain "
                "RTL — moved into file_sets.rtl.",
            )
        if "constraints" in file_sets:
            _route_constraints(cfg, file_sets.pop("constraints"), warns)


def _route_constraints(cfg: dict, constraints, warns: list[str]) -> None:
    if not isinstance(constraints, list):
        raise ManifestError(
            "file_sets.constraints must be a list of constraint files to "
            "migrate to board.{xdc,sdc,lpf,pdc}_files."
        )
    board = cfg.setdefault("board", {})
    for path in constraints:
        ext = ("." + path.rsplit(".", 1)[-1].lower()) if "." in str(path) else ""
        if ext == ".ucf":
            raise ManifestError(
                f"file_sets.constraints entry '{path}': UCF constraints are "
                "removed (no canonical target)."
            )
        target = _CONSTRAINT_EXT_TO_BOARD.get(ext)
        if target is None:
            raise ManifestError(
                f"file_sets.constraints entry '{path}': cannot route by "
                "extension; place it under the matching board.<type>_files."
            )
        board.setdefault(target, []).append(path)
    _warn(
        warns,
        "file_sets.constraints is deprecated; routed to board.{xdc,sdc,lpf,pdc}_files "
        "by file extension.",
    )


def _normalize_libraries(cfg: dict, warns: list[str]) -> None:
    if "libraries" not in cfg:
        return
    legacy = cfg.pop("libraries")
    ext = cfg.setdefault("external_libraries", {})
    if isinstance(legacy, dict):
        for k, v in legacy.items():
            ext.setdefault(k, v)
    _warn(
        warns,
        "libraries is deprecated; renamed to external_libraries.",
    )


def _normalize_env_generics(cfg: dict, warns: list[str]) -> None:
    env = cfg.get("env")
    if not isinstance(env, dict) or "generics" not in env:
        return
    legacy = env.pop("generics")
    if isinstance(legacy, dict):
        generics = cfg.setdefault("generics", {})
        for k, v in legacy.items():
            generics.setdefault(k, v)
    _warn(
        warns,
        "env.generics is deprecated; moved to top-level generics.",
    )


def _normalize_sim(cfg: dict, warns: list[str]) -> None:
    sim = cfg.get("sim")
    if not isinstance(sim, dict):
        return

    if "top_tb" in sim:
        sim.setdefault("default_top_tb", sim.pop("top_tb"))
        _warn(warns, "sim.top_tb is deprecated; renamed to sim.default_top_tb.")

    if "sim_options" in sim:
        sim.setdefault("options", sim.pop("sim_options"))
        _warn(warns, "sim.sim_options is deprecated; renamed to sim.options.")

    tb_lib = sim.get("tb_lib")

    if "tb_folder" in sim:
        folder = str(sim.pop("tb_folder")).replace("\\", "/").rstrip("/")
        lib = tb_lib if isinstance(tb_lib, str) and tb_lib else "tb"
        sim_sets = cfg.setdefault("file_sets", {}).setdefault("sim", [])
        sim_sets.append({
            "lib": lib,
            "src": [f"{folder}/**/*{e}" for e in (".vhd", ".vhdl", ".v", ".sv")],
        })
        _warn(
            warns,
            f"sim.tb_folder is deprecated; converted to a file_sets.sim entry "
            f"(lib='{lib}') with HDL globs under '{folder}'.",
        )

    if "tb_lib" in sim:
        sim.pop("tb_lib")
        dtb = sim.get("default_top_tb")
        if isinstance(dtb, str) and dtb and "." not in dtb and isinstance(tb_lib, str) and tb_lib:
            sim["default_top_tb"] = f"{tb_lib}.{dtb}"
        _warn(
            warns,
            "sim.tb_lib is deprecated; fold the library into a qualified "
            "sim.default_top_tb (<lib>.<tb>) or file_sets.sim[].lib.",
        )


def _normalize_quartus_and_features(cfg: dict, warns: list[str]) -> None:
    quartus = cfg.get("quartus")
    if isinstance(quartus, dict) and "qip_files" in quartus:
        qips = quartus.pop("qip_files")
        ip_cores = cfg.setdefault("ip_cores", [])
        if isinstance(qips, list):
            for src in qips:
                ip_cores.append({"kind": "qip", "src": src})
        _warn(
            warns,
            "quartus.qip_files is deprecated; converted to ip_cores entries "
            "with kind: qip.",
        )

    features = cfg.get("features")
    if isinstance(features, dict) and "block_design" in features:
        bd = features.pop("block_design")
        if isinstance(bd, dict) and bd.get("enabled") is True:
            src = bd.get("tcl") or bd.get("src")
            if not src:
                raise ManifestError(
                    "features.block_design is enabled but has no 'tcl'/'src' "
                    "generator script to migrate to ip_cores."
                )
            cfg.setdefault("ip_cores", []).append({"kind": "bd", "src": src})
            _warn(
                warns,
                "features.block_design is deprecated; converted to an "
                "ip_cores entry with kind: bd.",
            )
        else:
            _warn(
                warns,
                "features.block_design is deprecated and was disabled "
                "(enabled not true); dropped.",
            )
        if not features:
            cfg.pop("features", None)


def _canonicalize_schema_version(cfg: dict) -> None:
    # Producers may emit "1.0"/"1.0.0"; the canonical value is the bare major
    # "1". Silent coerce (no deprecation warn): this is a producer-format
    # tolerance, not a deprecated user-facing form. Only zero-valued
    # minor/patch are coerced — a real future minor like "1.2" is left intact
    # so the schema pattern rejects it rather than it being read as v1.
    # Idempotent: "1" is left untouched.
    sv = cfg.get("schema_version")
    if isinstance(sv, str) and _SCHEMA_VERSION_V1.match(sv):
        cfg["schema_version"] = "1"


def _coerce_vhdl_std(cfg: dict) -> None:
    file_sets = cfg.get("file_sets")
    if not isinstance(file_sets, dict):
        return
    for group in ("rtl", "sim"):
        entries = file_sets.get(group)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and isinstance(entry.get("vhdl_std"), int) \
                    and not isinstance(entry.get("vhdl_std"), bool):
                entry["vhdl_std"] = str(entry["vhdl_std"])
