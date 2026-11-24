#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2024-2026 LogiMentor S.r.l.

"""
Vivado project importer

Policy:
- Import ONLY what the .xpr lists (no tree scan).
- Stage HDL into:          <dest>/src/<library>/
- Stage constraints into:  <dest>/constraints/
- Stage IP into:           <dest>/ip/<core_name>/... for xci/xcix (+ coe/mem nearby)
                           <dest>/ip/bd/<bd_name>/<bd_name>.bd for block designs
                           <dest>/ip/... for orphan .dcp (not tied to a core dir)
- Skip known junk (e.g., $PPRDIR/archive_project_summary.txt, *.jou, *.log).
- Copy filtered template assets:
    docs/            (entire)
    tools/vivado/    (only)
    tools/common/    (if present)

Writes canonical YAML (library-aware).
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import List, Set, Dict, Tuple

# ------------------ logging helpers ------------------
VERBOSE = False
def _ts(): return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
def info(msg): print(f"{_ts()} INFO    {msg}")
def warn(msg): print(f"{_ts()} WARNING {msg}", file=sys.stderr)
def err(msg):  print(f"{_ts()} ERROR   {msg}", file=sys.stderr)
def dbg(msg):
    if VERBOSE: print(f"{_ts()} VERBOSE {msg}")

# ------------------ small helpers ------------------
def ensure_dir(d: str) -> str:
    os.makedirs(d, exist_ok=True)
    return os.path.normpath(os.path.abspath(d))

def joinf(*parts: str) -> str:
    return os.path.normpath(os.path.abspath(os.path.join(*parts)))

def to_posix(p: str) -> str:
    return p.replace("\\", "/")

def derive_xilinx_family(part: str) -> str:
    if not part:
        return ""
    p = part.lower()
    if p.startswith("xc7a"): return "artix7"
    if p.startswith("xc7k"): return "kintex7"
    if p.startswith("xc7z"): return "zynq7000"
    if p.startswith("xcku"): return "kintex_ultrascale"
    if p.startswith("xcvu"): return "virtex_ultrascale"
    if p.startswith("xczu"): return "zynq_ultrascale+"
    if p.startswith("xcau"): return "artix_ultrascale+"
    return ""

def norm(p: str) -> str:
    return os.path.normpath(os.path.abspath(p)) if p else p

# ------------------ template helpers ------------------
def find_template_root() -> str:
    """Walk up from this file until we see a template marker (tools/ or aurig_build.py)."""
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):
        if os.path.isdir(os.path.join(d, "tools")) or os.path.exists(os.path.join(d, "aurig_build.py")):
            return os.path.normpath(d)
        d = os.path.dirname(d)
    return os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

def copy_dir_contents(src_dir: str, dst_dir: str, verbose: bool = False, exclude: Set[str] | None = None) -> int:
    """Copy only the *contents* of src_dir into dst_dir (no extra nesting)."""
    ex = set(exclude or set())
    if not os.path.isdir(src_dir):
        return 0
    os.makedirs(dst_dir, exist_ok=True)
    n = 0
    for name in os.listdir(src_dir):
        if name in ex:
            continue
        s = os.path.join(src_dir, name)
        d = os.path.join(dst_dir, name)
        if verbose:
            info(f"tpl: {s} -> {dst_dir}")
        if os.path.isdir(s):
            shutil.copytree(s, d, dirs_exist_ok=True)
        else:
            shutil.copy2(s, d)
        n += 1
    return n

# ------------------ YAML helpers ------------------
def _yaml_str(s: str) -> str:
    """Return s as a safely double-quoted YAML scalar.

    Applied to any value that originates from user input, project metadata,
    or the filesystem (project name, top module, part, paths) so that spaces,
    colons, and other YAML-special characters cannot break the output.
    """
    escaped = s.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'

# ------------------ YAML writer (canonical manifest-v1) ------------------
def write_yaml_lm(dest_root: str,
                  name: str,
                  top: str,
                  part: str,
                  libs_used: List[str],
                  xdc_relpaths: List[str],
                  xpr_path: str = "") -> str:
    cfgd = ensure_dir(joinf(dest_root, "config"))
    ypath = joinf(cfgd, "project.yaml")

    vendor = "xilinx"
    # Schema v1 requires device.family; fall back to "unknown" when the part
    # string is unrecognized (discovery.confidence.device flags this as low).
    family = derive_xilinx_family(part) or "unknown"
    generated_at = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    # Confidence assessment for discovery block. A placeholder family is as
    # uncertain as a missing part, so it lowers device confidence too.
    missing_info: List[str] = []
    top_confidence    = "high" if top else "low"
    device_confidence = "high" if (part and family != "unknown") else "low"
    if not top:
        missing_info.append("top")
    if not part or family == "unknown":
        missing_info.append("device")

    with open(ypath, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("# ============================================================================\n")
        fh.write("# AURIG Build project configuration (Vivado)\n")
        fh.write("# ============================================================================\n\n")

        # --- canonical producer field ---
        fh.write('schema_version: "1"\n\n')

        # --- project metadata ---
        fh.write(f"project_name: {_yaml_str(name)}\n")
        # project_root: .. is correct because the importer stages everything under
        # dest_root/ and writes this YAML to dest_root/config/project.yaml, so the
        # project root is always exactly one level up from the config directory.
        fh.write("project_root: ..\n")
        fh.write(f"top: {_yaml_str(top)}\n")
        fh.write("require_exact_versions: false\n")
        fh.write("debug_paths: false\n\n")

        # --- tool ---
        fh.write("tool:\n")
        fh.write("  synth:\n")
        fh.write("    kind: vivado\n")
        fh.write('    version: ""\n')
        fh.write("    exe: vivado\n")
        fh.write("    # env_script:\n")
        fh.write("    #   windows: C:/Xilinx/Vivado/2023.1/settings64.bat\n")
        fh.write("    #   linux:   /opt/Xilinx/Vivado/2023.1/settings64.sh\n\n")

        # --- device ---
        fh.write("device:\n")
        fh.write(f"  vendor: {vendor}\n")
        fh.write(f"  family: {family}\n")
        fh.write(f"  part: {_yaml_str(part)}\n\n")

        # --- board ---
        if xdc_relpaths:
            fh.write("board:\n")
            fh.write("  xdc_files:\n")
            for rp in xdc_relpaths:
                fh.write(f"    - {_yaml_str(to_posix(rp))}\n")
            fh.write("\n")
        else:
            fh.write("board: {}\n\n")

        # --- file_sets (canonical shape) ---
        fh.write("file_sets:\n")
        fh.write("  rtl:\n")
        libs_sorted = sorted(set(libs_used))
        if not libs_sorted:
            fh.write("    []\n")
        else:
            for lib in libs_sorted:
                fh.write(f"    - lib: {lib}\n")
                fh.write( '      vhdl_std: "2008"\n')
                fh.write( "      src:\n")
                fh.write(f"        - src/{lib}/**/*.vhd\n")
                fh.write(f"        - src/{lib}/**/*.v\n")
                fh.write(f"        - src/{lib}/**/*.sv\n")
                # include: is emitted for every library because the importer stages
                # all HDL types (VHDL/V/SV) into the same lib directory and we do
                # not track per-lib file types at this point.  Tools that only see
                # VHDL sources for a given lib will simply ignore the include path.
                fh.write( "      include:\n")
                fh.write(f"        - src/{lib}\n")
        fh.write("\n")

        # --- global includes and env ---
        fh.write("include_dirs_global:\n")
        fh.write("  - src\n")
        fh.write("  - sim\n\n")

        fh.write("env: {}\n\n")

        # --- origin (canonical producer metadata) ---
        xpr_posix = to_posix(xpr_path) if xpr_path else ""
        fh.write("origin:\n")
        fh.write("  generator: aurig_build_vivado_importer\n")
        fh.write('  generator_version: "1.0"\n')
        fh.write(f'  generated_at: "{generated_at}"\n')
        fh.write("  source:\n")
        fh.write("    kind: vivado_project\n")
        fh.write(f"    path: {_yaml_str(xpr_posix)}\n\n")

        # --- discovery (confidence report) ---
        fh.write("discovery:\n")
        fh.write("  confidence:\n")
        fh.write(f"    top: {top_confidence}\n")
        fh.write(f"    device: {device_confidence}\n")
        fh.write("    tool: high\n")
        if missing_info:
            fh.write("  missing_info:\n")
            for item in missing_info:
                fh.write(f"    - {item}\n")
        else:
            fh.write("  missing_info: []\n")

    return ypath

# ------------------ resolve Vivado vars ------------------
def resolve_xpr_vars(xpr_dir: str, raw: str) -> str:
    p = (raw or "").strip()
    proj_base = os.path.basename(xpr_dir.rstrip(os.sep))
    PSRCDIR = norm(os.path.join(xpr_dir, f"{proj_base}.srcs"))
    PPRDIR  = norm(xpr_dir)
    PCACHEDIR = norm(os.path.join(xpr_dir, ".Xil"))
    PRUNDIR   = norm(xpr_dir)

    varmap = {"$PSRCDIR": PSRCDIR, "$PPRDIR": PPRDIR, "$PCACHEDIR": PCACHEDIR, "$PRUNDIR": PRUNDIR}

    for var, val in varmap.items():
        if p == var:
            return val
        if p.startswith(var + "/") or p.startswith(var + "\\"):
            rest = p[len(var) + 1:]
            return norm(os.path.join(val, rest))

    if p.startswith("$"):
        cut = min([i for i in (p.find("/"), p.find("\\")) if i >= 0], default=-1)
        rest = p[cut+1:] if cut >= 0 else ""
        return norm(os.path.join(xpr_dir, rest))

    return norm(os.path.join(xpr_dir, p)) if not os.path.isabs(p) else norm(p)

# ------------------ XPR helpers ------------------
def find_xpr(src_root: str) -> str:
    root_list = [f for f in os.listdir(src_root) if f.lower().endswith(".xpr")]
    if root_list:
        base = os.path.basename(src_root.rstrip(os.sep))
        exact = f"{base}.xpr"
        for c in root_list:
            if c == exact:
                return norm(os.path.join(src_root, c))
        return norm(os.path.join(src_root, root_list[0]))
    for d in os.listdir(src_root):
        p = os.path.join(src_root, d)
        if os.path.isdir(p):
            x = os.path.join(p, f"{d}.xpr")
            if os.path.isfile(x):
                return norm(x)
    return ""

def parse_xpr(xpr_path: str):
    """
    Returns:
      top: str (prefer FileSet 'sources_1' TopModule; else global)
      part: str
      sources: list[dict(path=<str>, lib=<str>)]
      constraints: list[str]
    """
    tree = ET.parse(xpr_path)
    root = tree.getroot()

    top_global, top_sources1, part = "", "", ""
    # Prefer TopModule attached to sources_1
    for fs in root.iter("FileSet"):
        if fs.get("Name", "").lower() == "sources_1":
            for opt in fs.iter("Option"):
                if opt.get("Name") == "TopModule":
                    top_sources1 = opt.get("Val", top_sources1)

    # Fallback: global Option TopModule
    if not top_sources1:
        for parent in root.iter():
            for child in list(parent):
                if child.tag == "Option" and child.get("Name") == "TopModule":
                    top_global = child.get("Val", top_global)

    # Device
    for opt in root.iter("Option"):
        if opt.get("Name") == "Part":
            part = opt.get("Val", part)

    def files_in_fileset(fs_name: str):
        for fs in root.iter("FileSet"):
            if fs.get("Name", "").lower() == fs_name.lower():
                return list(fs.iter("File"))
        return []

    sources = []
    for f in files_in_fileset("sources_1"):
        path = f.get("Path")
        if not path:
            continue
        lib = "work"
        for attr in f.iter("Attr"):
            if attr.get("Name") == "Library":
                lib = attr.get("Val", "work") or "work"
                break
        sources.append({"path": path, "lib": lib})

    constraints = []
    for f in files_in_fileset("constrs_1"):
        path = f.get("Path")
        if path:
            constraints.append(path)

    top = top_sources1 or top_global
    return {"top": top, "part": part, "sources": sources, "constraints": constraints}

# ------------------ classification ------------------
HDL_EXT = {".vhd", ".vhdl", ".v", ".sv", ".vh", ".svh"}
IP_EXT  = {".xci", ".xcix", ".bd", ".dcp"}
IP_AUX_EXT = {".coe", ".mem"}       # route to ip/<core>/ when inside an IP dir
SKIP_BASENAMES = {"archive_project_summary.txt"}
SKIP_EXT = {".jou", ".log"}

def classify_source(abs_path: str) -> str:
    ext = os.path.splitext(abs_path)[1].lower()
    if ext in IP_EXT:
        return "ip"
    if ext in IP_AUX_EXT:
        return "ip_aux"
    if os.path.basename(abs_path).lower() in SKIP_BASENAMES or ext in SKIP_EXT:
        return "skip"
    return "hdl"

# ------------------ staging ------------------
def copy_file(src: str, dst: str, dry: bool) -> str:
    dbg(f"Copy: {src} -> {dst}")
    if dry:
        return dst
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.islink(dst) or os.path.isfile(dst):
        os.remove(dst)  # clobber
    shutil.copy2(src, dst)
    return dst

def write_default_gitignore(dest_root: str):
    """Write a safe default .gitignore (if not present)."""
    path = os.path.join(dest_root, ".gitignore")
    if os.path.exists(path):
        return
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(
            "# Auto-generated by vivado importer\n\n"
            "# Python\n__pycache__/\n*.pyc\n*.pyo\n*.swp\n\n"
            "# IDE\n.vscode/\n.idea/\n*.code-workspace\n\n"
            "# OS\n.DS_Store\nThumbs.db\n\n"
            "# Vivado build outputs\n*.jou\n*.log\n*.rpt\n*.rpx\n*.str\n*.wdb\n.Xil/\n\n"
            "# Vivado project artifacts\n*.cache/\n*.runs/\n*.hw/\nip/*/ip_user_files/\n\n"
            "# Bitstreams\n*.bit\n*.bin\n*.ltx\n"
        )

def write_default_readme(dest_root: str, name: str):
    """Write a minimal README if dest_root has none."""
    path = os.path.join(dest_root, "README.md")
    if os.path.exists(path):
        return
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(
            f"# {name}\n\n"
            "Imported with the Vivado importer.\n\n"
            "## Build\n"
            "```powershell\n"
            "aurig-build --cfg .\\config\\project.yaml project\n"
            "aurig-build --cfg .\\config\\project.yaml synth\n"
            "aurig-build --cfg .\\config\\project.yaml impl\n"
            "aurig-build --cfg .\\config\\project.yaml bit\n"
            "```\n"
        )

def write_default_root_artifacts(dest_root: str, proj_name: str):
    """Write default .gitignore and README.md into dest_root if absent."""
    if not os.path.exists(os.path.join(dest_root, ".gitignore")):
        write_default_gitignore(dest_root)
        info("Wrote default .gitignore.")
    if not os.path.exists(os.path.join(dest_root, "README.md")):
        write_default_readme(dest_root, proj_name)
        info("Wrote minimal README.md.")

def stage_project(src_root: str, dest_root: str, name: str, want_yaml: bool,
                  top_cli: str, part_cli: str, dry: bool,
                  tpl_docs: bool, tpl_tools: bool):
    xpr = find_xpr(src_root)
    if not xpr or not os.path.isfile(xpr):
        raise RuntimeError(f"No .xpr found under {src_root}")
    info(f"XPR      : {xpr}")

    meta = parse_xpr(xpr)
    top = top_cli or meta["top"]
    part = part_cli or meta["part"]
    if not top:
        warn("TOP not found in XPR and not provided.")
    if not part:
        warn("PART not found in XPR and not provided.")

    xpr_dir  = os.path.dirname(xpr)
    dst_src  = norm(os.path.join(dest_root, "src"))
    dst_con  = norm(os.path.join(dest_root, "constraints"))
    dst_ip   = norm(os.path.join(dest_root, "ip"))
    if not dry:
        os.makedirs(dst_src, exist_ok=True)
        os.makedirs(dst_con, exist_ok=True)
        os.makedirs(dst_ip, exist_ok=True)

    dbg(f"XPR parse: sources_1 entries = {len(meta['sources'])}, constrs_1 entries = {len(meta['constraints'])}")

    staged_hdl: List[str] = []
    staged_ip:  List[str] = []
    staged_con: List[str] = []
    libs_used:  Set[str]  = set()
    xdc_relpaths: List[str] = []

    # 1) Pre-index XCI roots to group .coe/.mem under the matching core folder
    #    Map: ip_dir_abs -> dst_core_dir (ip/<core_name>)
    xci_dir_to_dst: Dict[str, str] = {}   # key: abs parent dir of xci; val: dest core dir
    xci_name_from_dir: Dict[str, str] = {}  # key: abs parent dir -> core_name

    # First pass: discover all XCI/XCIX and pre-create their dest dirs
    for item in meta["sources"]:
        rel = item["path"]
        abs_src = resolve_xpr_vars(xpr_dir, rel)
        ext = os.path.splitext(abs_src)[1].lower()
        if ext in (".xci", ".xcix"):
            core_name = os.path.splitext(os.path.basename(abs_src))[0]
            src_dir   = os.path.dirname(abs_src)
            dst_core_dir = norm(os.path.join(dst_ip, core_name))
            if not dry:
                os.makedirs(dst_core_dir, exist_ok=True)
            xci_dir_to_dst[src_dir] = dst_core_dir
            xci_name_from_dir[src_dir] = core_name

    # 2) Stage all sources with grouping rules
    for item in meta["sources"]:
        rel = item["path"]
        lib = item.get("lib", "work") or "work"
        abs_src = resolve_xpr_vars(xpr_dir, rel)
        dbg(f"Resolved source: {rel} ==> {abs_src} (lib={lib})")

        if not os.path.isfile(abs_src):
            warn(f"Missing source listed in XPR, skipping: {rel} (resolved: {abs_src})")
            continue

        kind = classify_source(abs_src)
        if kind == "skip":
            dbg(f"Skip: {abs_src}")
            continue

        ext = os.path.splitext(abs_src)[1].lower()

        if kind == "ip":
            # XCI/XCIX => ip/<core_name>/<file>; BD => ip/bd/<bd_name>/<bd_name>.bd; DCP => ip/<file>
            if ext in (".xci", ".xcix"):
                core_name = os.path.splitext(os.path.basename(abs_src))[0]
                dst_dir = norm(os.path.join(dst_ip, core_name))
                if not dry:
                    os.makedirs(dst_dir, exist_ok=True)
                dst = norm(os.path.join(dst_dir, os.path.basename(abs_src)))
                final = copy_file(abs_src, dst, dry)
                staged_ip.append(final)
            elif ext == ".bd":
                bd_name = os.path.splitext(os.path.basename(abs_src))[0]
                dst_dir = norm(os.path.join(dst_ip, "bd", bd_name))
                if not dry:
                    os.makedirs(dst_dir, exist_ok=True)
                dst = norm(os.path.join(dst_dir, os.path.basename(abs_src)))
                final = copy_file(abs_src, dst, dry)
                staged_ip.append(final)
            else:
                # .dcp or other ip artifacts listed explicitly
                dst = norm(os.path.join(dst_ip, os.path.basename(abs_src)))
                final = copy_file(abs_src, dst, dry)
                staged_ip.append(final)

        elif kind == "ip_aux":
            # Route .coe/.mem to nearest XCI core if the file lives under that XCI dir (or a subdir)
            src_dir = os.path.dirname(abs_src)
            # find best matching xci root by prefix match (longest path wins)
            best_root = ""
            for root_dir in xci_dir_to_dst.keys():
                if abs_src.lower().startswith(root_dir.lower() + os.sep.lower()):
                    if len(root_dir) > len(best_root):
                        best_root = root_dir
            if best_root:
                dst_dir = xci_dir_to_dst[best_root]
                if not dry:
                    os.makedirs(dst_dir, exist_ok=True)
                dst = norm(os.path.join(dst_dir, os.path.basename(abs_src)))
                final = copy_file(abs_src, dst, dry)
                staged_ip.append(final)
            else:
                # Not under any known IP dir => keep with HDL library (as aux include)
                lib_dir = norm(os.path.join(dst_src, lib))
                dst = norm(os.path.join(lib_dir, os.path.basename(abs_src)))
                final = copy_file(abs_src, dst, dry)
                staged_hdl.append(final)
                libs_used.add(lib)

        else:
            # HDL or other design-side files
            lib_dir = norm(os.path.join(dst_src, lib))
            dst = norm(os.path.join(lib_dir, os.path.basename(abs_src)))
            final = copy_file(abs_src, dst, dry)
            staged_hdl.append(final)
            libs_used.add(lib)

    # 3) Stage constraints
    for rel in meta["constraints"]:
        abs_src = resolve_xpr_vars(xpr_dir, rel)
        dbg(f"Resolved constraint: {rel} ==> {abs_src}")
        if not os.path.isfile(abs_src):
            warn(f"Missing constraint listed in XPR, skipping: {rel} (resolved: {abs_src})")
            continue
        if os.path.basename(abs_src).lower() in SKIP_BASENAMES or os.path.splitext(abs_src)[1].lower() in SKIP_EXT:
            dbg(f"Skip constraint: {abs_src}")
            continue
        dst = norm(os.path.join(dst_con, os.path.basename(abs_src)))
        final = copy_file(abs_src, dst, dry)
        staged_con.append(final)
        try:
            xrel = os.path.relpath(final, dest_root)
        except Exception:
            xrel = final
        xdc_relpaths.append(xrel)

    # 4) Copy filtered template assets (docs/ + only tools/vivado and tools/common)
    if tpl_docs or tpl_tools:
        tpl_root = find_template_root()
        EXCL = {".git", "__pycache__", ".pytest_cache", ".idea", ".vscode"}

        if tpl_docs:
            src_docs = os.path.join(tpl_root, "docs")
            dst_docs = os.path.join(dest_root, "docs")
            n = copy_dir_contents(src_docs, dst_docs, verbose=VERBOSE, exclude=EXCL)
            if n:
                info(f"Copied {n} item(s) into docs/ from template.")
            else:
                dbg("No docs/ in template — skipping.")

        if tpl_tools:
            # copy only vivado and common subtrees
            src_tools_root = os.path.join(tpl_root, "tools")
            for sub in ("vivado", "common"):
                src_t = os.path.join(src_tools_root, sub)
                dst_t = os.path.join(dest_root, "tools", sub)
                n = copy_dir_contents(src_t, dst_t, verbose=VERBOSE, exclude=EXCL)
                if n:
                    info(f"Copied {n} item(s) into tools/{sub}/ from template.")
                else:
                    dbg(f"No tools/{sub}/ in template — skipping.")

    write_default_root_artifacts(dest_root, name)


    # 5) YAML
    if want_yaml:
        yaml_path = write_yaml_lm(
            dest_root=dest_root,
            name=name,
            top=top or "",
            part=part or "",
            libs_used=list(libs_used),
            xdc_relpaths=xdc_relpaths,
            xpr_path=xpr,
        )
        info(f"YAML written: {yaml_path}")

    # Summary
    info("Staged summary (from XPR only):")
    info(f"  HDL        : {len(staged_hdl)}")
    info(f"  IP         : {len(staged_ip)}")
    info(f"  Constraints: {len(staged_con)}")

# ------------------ programmatic entry ------------------
def import_project(input_path: str, dest: str, name: str = "", force: bool = False) -> int:
    """Run the Vivado importer programmatically (used by `aurig-build import`).

    The Vivado importer always writes into ``dest`` (no non-empty prompt), so
    ``force`` is accepted for a uniform wrapper signature but has no effect.

    Returns a process exit code (0 on success).
    """
    argv = [input_path, "--dest", dest, "--yaml"]
    if name:
        argv += ["--name", name]
    try:
        rc = main(argv)
        return 0 if rc is None else int(rc)
    except SystemExit as exc:
        return 0 if exc.code is None else int(exc.code)

# ------------------ CLI ------------------
def main(argv=None):
    global VERBOSE
    ap = argparse.ArgumentParser(description="Vivado XPR importer")
    ap.add_argument("src_root", help="Path containing the Vivado .xpr")
    ap.add_argument("--dest", required=True, help="Destination root (e.g., temp/test_vivado)")
    ap.add_argument("--name", default="", help="Project name (for manifest only; default: source folder name)")
    ap.add_argument("--top", default="", help="Override TOP module (else infer: sources_1 -> global)")
    ap.add_argument("--part", default="", help="Override PART (else take from XPR)")
    ap.add_argument("--yaml", action="store_true", help="Write config/project.yaml")
    ap.add_argument("--verbose", action="store_true", help="Verbose logging")
    ap.add_argument("--dry-run", action="store_true", help="Do not copy, just log")
    ap.add_argument("--tpl-docs",  action="store_true", help="Copy docs/ from template root into dest")
    ap.add_argument("--tpl-tools", action="store_true", help="Copy tools/vivado (+common) from template root into dest")
    args = ap.parse_args(argv)

    VERBOSE = args.verbose
    src_root = norm(args.src_root)
    if not os.path.isdir(src_root):
        err(f"Folder not found: {src_root}")
        sys.exit(2)
    dest_root = norm(args.dest)
    name = args.name or os.path.basename(src_root.rstrip("/\\")) or "project"

    info("Importing Vivado project:")
    info(f"  Source   : {src_root}")
    info(f"  Name     : {name}")
    info(f"  Top      : {args.top or '<auto>'}")
    info(f"  Part     : {args.part or '<auto>'}")

    try:
        stage_project(
            src_root=src_root,
            dest_root=dest_root,
            name=name,
            want_yaml=args.yaml,
            top_cli=args.top,
            part_cli=args.part,
            dry=args.dry_run,
            tpl_docs=args.tpl_docs,
            tpl_tools=args.tpl_tools,
        )
        info("Import completed.")
    except Exception as e:
        err(str(e))
        sys.exit(1)

if __name__ == "__main__":
    main()
