#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2024-2026 LogiMentor S.r.l.

"""
Diamond importer (Python port of tools/diamond/import.tcl)

Behavior:
  - Read an existing Diamond project folder (expects *.ldf)
  - Parse .ldf to get: device, impl name, top, file list
  - Stage files into the project structure:
        <dest>/src/hdl/            (VHDL/Verilog/SystemVerilog)
        <dest>/constraints/        (.xdc/.lpf/.pdc)
        <dest>/ip/                 (everything else)
        <dest>/impl/work/diamond/<name>/
  - Optionally copy docs/ and tools/ from the template root (repo), not from the legacy project
  - Write default .gitignore and README.md into the imported folder if absent
  - Optionally write config/project.yaml (same stub as the Tcl script)

Usage:
  aurig-build import --from diamond --input "C:\\path\\to\\diamond_project" --dest temp/mytest --name my_port

Or directly:
  py -3 -m aurig_build.diamond.import_ "C:\\path\\to\\diamond_project" --dest temp/mytest --name my_port \
     --mode copy --verbose --force --tpl-docs --tpl-tools
"""

import argparse
import os
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Dict, List, Optional, Tuple

# ---------------- Logging ----------------
VERBOSE = False
def _ts() -> str: return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
def info(m: str): print(f"{_ts()} INFO    {m}")
def warn(m: str): print(f"{_ts()} WARNING {m}", file=sys.stderr)
def err(m: str):  print(f"{_ts()} ERROR   {m}", file=sys.stderr)
def dbg(m: str):
    if VERBOSE: print(f"{_ts()} VERBOSE {m}")

# ---------------- Path helpers ----------------
def npath(p: str) -> str:
    """Normalize to absolute and use native separators."""
    return os.path.normpath(os.path.abspath(p))

def ensure_dir(d: str) -> str:
    os.makedirs(d, exist_ok=True)
    return npath(d)

def joinf(*parts: str) -> str:
    return npath(os.path.join(*parts))

def is_rel_or_volume_rel(p: str) -> bool:
    # Tcl had 'volumerelative'; in Python treat non-absolute as relative
    return not os.path.isabs(p)

def tail_parent(p: str) -> str:
    """Return the last directory name (tail of dirname)."""
    return os.path.basename(os.path.dirname(p))

def copy_or_link(src: str, dst: str, mode: str):
    """Try to link if mode==link, else copy. On link failure, copy."""
    dbg(f"{'Link' if mode=='link' else 'Copy'}: {src} -> {dst}")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if mode == "link":
        try:
            # On Windows this may require admin/Developer Mode; fallback if it fails
            if os.path.exists(dst):
                os.remove(dst)
            os.symlink(src, dst)
            return
        except Exception as e:
            warn(f"symlink failed ({e}); copying instead: {src}")
    shutil.copy2(src, dst)

def unique_path(dst: str) -> str:
    """Avoid overwriting: append _1/_2/... before extension if needed."""
    base, ext = os.path.splitext(dst)
    i = 1
    out = dst
    while os.path.exists(out):
        out = f"{base}_{i}{ext}"
        i += 1
    return out

def detect_pnmainc_bin_on_windows() -> str:
    """If pnmainc is on PATH, return its bin dir (…/diamond/3.xx/bin/nt64). Else ''."""
    exe = shutil.which("pnmainc") or shutil.which("pnmainc.exe")
    if not exe:
        return ""
    return os.path.normpath(os.path.dirname(exe))

def normalize_diamond_version(v: str) -> str:
    """Normalize a Diamond version string to major.minor (e.g. '3.14.0' ->
    '3.14'), matching what the runtime version checker derives
    (aurig_build/run.py:262-270). Returns the stripped input if it has no
    recognizable major.minor prefix."""
    m = re.match(r"\s*(\d+)\.(\d+)", v)
    return f"{m.group(1)}.{m.group(2)}" if m else v.strip()

def detect_diamond_version_from_path(bin_path: str) -> str:
    """Extract the Diamond version (e.g. '3.14') from a bin path like
    …/diamond/3.14/bin/nt64 or …/diamond/3.14.0/bin/lin64. Returns
    '' if no version segment is found.

    The result is normalized to major.minor to match what the runtime
    version checker extracts (aurig_build/run.py:262-270), so the emitted
    YAML's version field round-trips through require_exact_versions check.
    The 'diamond' segment is anchored to path-segment boundaries (mirroring
    run.py) so strings like '.../mydiamond/3.14/...' are not false matches.
    """
    if not bin_path:
        return ""
    m = re.search(r"[\\/]diamond[\\/](\d+)\.(\d+)(?:\.\d+)?(?:[\\/]|$)", bin_path, re.IGNORECASE)
    return f"{m.group(1)}.{m.group(2)}" if m else ""

# ---------------- LDF parsing ----------------
def _tag_name(elem) -> str:
    """Strip namespaces so <ns:BaliProject> and <BaliProject> both appear as 'BaliProject'."""
    if '}' in elem.tag:
        return elem.tag.rsplit('}', 1)[1]
    return elem.tag

def parse_ldf_xml(ldf_path: str) -> Dict:
    """
    Return dict:
      impl: str
      top: str
      device: str
      default_lib: str (implementation-level default library)
      src: list[dict(path=<str>, lib=<str>, kind=<str>)]  # kind ~ 'vhdl'|'verilog'|'sv'|'constr'|'other'
    """
    R = {"impl": "impl1", "top": "", "device": "", "default_lib": "", "src": []}
    try:
        tree = ET.parse(ldf_path)
        root = tree.getroot()
    except Exception as e:
        warn(f"XML parse failed, falling back to regex ({e})")
        return parse_ldf_regex(ldf_path)

    def tn(x):  # tag name w/o ns
        return x.tag.rsplit('}', 1)[-1] if '}' in x.tag else x.tag

    # Device
    for e in root.iter():
        if tn(e) == "BaliProject":
            R["device"] = e.attrib.get("device", "") or R["device"]

    # Implementation + default lib + top
    for e in root.iter():
        if tn(e) == "Implementation":
            impl = e.attrib.get("title") or e.attrib.get("name")
            if impl: R["impl"] = impl
            # Implementation-level <Options ...>
            for c in e:
                if tn(c) == "Options":
                    # Default library for sources that omit lib=
                    dlib = c.attrib.get("lib", "")
                    if dlib: R["default_lib"] = dlib
                    # Prefer explicit top here; some LDFs set top/def_top only here
                    if not R["top"]:
                        R["top"] = c.attrib.get("top") or c.attrib.get("def_top") or ""

    # Top fallback (project-level <Options>)
    if not R["top"]:
        for e in root.iter():
            if tn(e) == "Options":
                R["top"] = e.attrib.get("top") or R["top"]

    # Sources
    for e in root.iter():
        if tn(e) != "Source":
            continue
        name  = e.attrib.get("name", "")
        tshort = (e.attrib.get("type_short", "") or "").lower()
        lib = ""
        for c in e:
            if tn(c) == "Options":
                lib = c.attrib.get("lib", "") or ""
                break

        if not name:
            continue

        # classify rudimentarily from type_short + extension
        ext = os.path.splitext(name)[1].lower()
        if tshort in ("vhdl",) or ext in (".vhd", ".vhdl"):
            kind = "vhdl"
        elif tshort in ("verilog",) or ext == ".v":
            kind = "verilog"
        elif tshort in ("systemverilog",) or ext == ".sv":
            kind = "sv"
        elif tshort in ("lpf", "pdc", "xdc", "sdc") or ext in (".lpf", ".pdc", ".xdc", ".sdc"):
            kind = "constr"
        else:
            kind = "other"

        R["src"].append({"path": name, "lib": lib, "kind": kind})

    return R


def parse_ldf_regex(ldf_path: str) -> Dict:
    """Fallback line-based regex (mirrors your Tcl)."""
    R = {"impl": "impl1", "top": "", "device": "", "src": []}
    re_dev  = re.compile(r'<BaliProject[^>]*\sdevice="([^"]+)"')
    re_impl = re.compile(r'<Implementation[^>]*\s(title|name)="([^"]+)"')
    re_top1 = re.compile(r'<Options[^>]*\stop="([^"]+)"')
    re_top2 = re.compile(r'<Options[^>]*\stop_module="([^"]+)"')
    re_src  = re.compile(r'<Source[^>]*\sname="([^"]+)"')

    with open(ldf_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            s = line.strip()
            if not R["device"]:
                m = re_dev.search(s)
                if m: R["device"] = m.group(1)
            m = re_impl.search(s)
            if m: R["impl"] = m.group(2)
            if not R["top"]:
                m = re_top1.search(s)
                if m: R["top"] = m.group(1)
            if not R["top"]:
                m = re_top2.search(s)
                if m: R["top"] = m.group(1)
            m = re_src.search(s)
            if m: R["src"].append(m.group(1))
    return R

def split_lib_top(top: str) -> tuple[str, str]:
    """Return (lib, mod) from 'lib.mod' or ('', top) if no dot."""
    if top and "." in top:
        a, b = top.split(".", 1)
        return a, b
    return "", top or ""

def detect_lib(abs_src: str, top_lib_hint: str, top_mod_hint: str) -> str:
    """
    Heuristic library detection for Diamond:
      - if filename == <top_mod>.*  -> top_lib_hint (or 'work')
      - if any path segment endswith '_lib' -> that segment
      - if filename starts with <name>_ and '<name>_lib' appears in path -> that
      - else 'work'
    """
    p_norm = abs_src.replace("\\", "/").lower()
    base = os.path.basename(p_norm)
    stem, _ = os.path.splitext(base)

    # match top module file
    if top_mod_hint and stem == top_mod_hint.lower():
        return (top_lib_hint or "work")

    # folder …/<xxx>_lib/…
    for seg in p_norm.split("/"):
        if seg.endswith("_lib"):
            return seg

    # filename prefix implying lib present in path
    if "_" in stem:
        cand = stem.split("_", 1)[0] + "_lib"
        if cand in p_norm:
            return cand

    return "work"

# ---------------- Template root detection ----------------
def find_template_root() -> str:
    """
    Start at .../tools/diamond/import.py and go up until we see 'tools/' or 'aurig_build.py'.
    Fallback: three levels up.
    """
    d = os.path.dirname(npath(__file__))
    for _ in range(6):
        if os.path.isdir(os.path.join(d, "tools")) or os.path.exists(os.path.join(d, "aurig_build.py")):
            return npath(d)
        d = os.path.dirname(d)
    return npath(os.path.join(os.path.dirname(npath(__file__)), "..", "..", ".."))

def copy_dir_contents(src_dir: str, dst_dir: str, verbose: bool = False) -> int:
    """Copy only the *contents* of src_dir into dst_dir (no extra nesting)."""
    if not os.path.isdir(src_dir):
        return 0
    os.makedirs(dst_dir, exist_ok=True)
    n = 0
    for item in os.listdir(src_dir):
        s = os.path.join(src_dir, item)
        d = os.path.join(dst_dir, item)
        if verbose:
            print(f"  tpl: {s} -> {dst_dir}")
        if os.path.isdir(s):
            shutil.copytree(s, d, dirs_exist_ok=True)
        else:
            shutil.copy2(s, d)
        n += 1
    return n

# -------- root artifacts from template (NEW) --------
def write_default_gitignore(dest_root: str):
    """Write a safe default .gitignore (if not present)."""
    path = os.path.join(dest_root, ".gitignore")
    if os.path.exists(path):
        return
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(
            "# Auto-generated by diamond importer\n"
            "__pycache__/\n*.pyc\n*.pyo\n*.swp\n\n"
            "# Diamond/Lattice artifacts\nimpl/\n*.log\n*.rpt\n*.twr\n\n"
            "# Vivado/General FPGA noise\n*.jou\n*.str\n*.rpx\n*.ltx\n*.wdb\n.Xil/\n*.cache/\n*.runs/\n*.hw/\n"
            "ip/*/ip_user_files/\n\n"
            "# Project scratch\n.temp/\ntemp/\n"
        )

def write_default_readme(dest_root: str, name: str):
    """Write a minimal README if dest_root has none."""
    path = os.path.join(dest_root, "README.md")
    if os.path.exists(path):
        return
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(
            f"# {name}\n\n"
            "Imported with the Diamond importer.\n\n"
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

def derive_lattice_family(part: str) -> str:
    """Best-effort Lattice family from the part name."""
    if not part: return ""
    p = part.upper()
    if p.startswith("LFE5U") or p.startswith("LFE5"):   return "ecp5"
    if p.startswith("LFE3"):                              return "ecp3"
    if p.startswith("LFXP"):                              return "xp2"
    if p.startswith("LCMXO3"):                            return "machxo3"
    if p.startswith("LCMXO2"):                            return "machxo2"
    if p.startswith("ICE40"):                             return "ice40"
    return ""

# ---------------- YAML stub ----------------
def to_posix(p: str) -> str:
    return p.replace("\\", "/")

def yaml_quote(s: str) -> str:
    """Quote a string safely for YAML single-line values."""
    s = str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f"\"{s}\""

def write_yaml(dest_root: str,
               name: str,
               top: str,
               part: str,
               pdc_relpaths: List[str],
               lpf_relpaths: List[str],
               libs_used: List[str],
               tool_version: str = "",
               bin_win: str = "",
               bin_linux: str = "",
               sdc_relpaths: Optional[List[str]] = None,
               ip_cores_emit: Optional[List[Dict]] = None) -> str:

    """
    Emit canonical YAML for Diamond:
      - tool.synth.kind: diamond
      - tool.synth.exe: pnmainc
      - tool.synth.bin_dir.{windows,linux}
      - device.vendor: lattice (+family guess)
      - board: pdc_files / lpf_files / sdc_files
      - ip_cores: ipx/lpc/edf entries staged from the .ldf
      - file_sets: single 'work' bucket (safe default)
    """
    sdc_relpaths = sdc_relpaths or []
    ip_cores_emit = ip_cores_emit or []
    cfgd = ensure_dir(joinf(dest_root, "config"))
    y = joinf(cfgd, "project.yaml")

    vendor = "lattice"
    # Schema v1 requires device.family; fall back to "unknown" when the part
    # string is unrecognized.
    family = derive_lattice_family(part) or "unknown"

    with open(y, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("# ============================================================================\n")
        fh.write("# AURIG Build project configuration (Diamond)\n")
        fh.write("# ============================================================================\n\n")
        fh.write('schema_version: "1"\n\n')
        fh.write(f"project_name: {name}\n")
        fh.write( "project_root: ..\n")
        fh.write( "debug_paths: true\n")
        fh.write(f"top: {top}\n\n")

        fh.write("tool:\n")
        fh.write("  synth:\n")
        fh.write("    kind: diamond\n")
        fh.write(f"    version: \"{tool_version}\"\n")
        fh.write("    exe: pnmainc\n")
        fh.write("    # If env_script is available, you can set it instead of bin_dir:\n")
        fh.write("    # env_script:\n")
        fh.write("    #   windows: C:/lscc/diamond/3.14/bin/nt64/diamond_env.bat\n")
        fh.write("    #   linux:   /opt/lscc/diamond/3.14/diamond_env.sh\n")
        fh.write("    bin_dir:\n")
        win_val = yaml_quote(bin_win) if bin_win else '""'
        lin_val = yaml_quote(bin_linux) if bin_linux else '""'
        fh.write(f"      windows: {win_val}\n")
        fh.write(f"      linux:   {lin_val}\n\n")

        fh.write("require_exact_versions: true\n\n")

        fh.write("device:\n")
        fh.write(f"  vendor: {vendor}\n")
        fh.write(f"  family: {family}\n")
        fh.write(f"  part:   {part}\n\n")

        fh.write("board:\n")
        if pdc_relpaths:
            fh.write("  pdc_files:\n")
            for rp in pdc_relpaths:
                fh.write(f"    - {to_posix(rp)}\n")
        else:
            fh.write("  pdc_files: []\n")
        if lpf_relpaths:
            fh.write("  lpf_files:\n")
            for rp in lpf_relpaths:
                fh.write(f"    - {to_posix(rp)}\n")
        else:
            fh.write("  lpf_files: []\n")
        if sdc_relpaths:
            fh.write("  sdc_files:\n")
            for rp in sdc_relpaths:
                fh.write(f"    - {to_posix(rp)}\n")
        else:
            fh.write("  sdc_files: []\n")
        fh.write("\n")

        fh.write("# ----------------------------------------------------------------------------\n")
        fh.write("# Library-aware file sets (Diamond import: src/<lib>/**)\n")
        fh.write("# ----------------------------------------------------------------------------\n")
        fh.write("file_sets:\n")
        fh.write("  rtl:\n")

        libs_sorted = sorted({lib for lib in (libs_used or []) if lib})
        if not libs_sorted:
            # fallback single work bucket
            fh.write("    - lib: work\n")
            fh.write("      src:\n")
            for ext in ("vhd", "v", "sv"):
                fh.write(f"        - src/work/*.{ext}\n")
                fh.write(f"        - src/work/**/*.{ext}\n")
            fh.write("      include: [src/work]\n")
            fh.write("      vhdl_std: 1993\n")
        else:
            for lib in libs_sorted:
                fh.write(f"    - lib: {lib}\n")
                fh.write( "      src:\n")
                for ext in ("vhd", "v", "sv"):
                    fh.write(f"        - src/{lib}/*.{ext}\n")
                    fh.write(f"        - src/{lib}/**/*.{ext}\n")
                fh.write(f"      include: [src/{lib}]\n")
                fh.write( "      vhdl_std: 1993\n")
        fh.write("\n")

        if ip_cores_emit:
            fh.write("ip_cores:\n")
            for core in ip_cores_emit:
                fh.write(f"  - kind: {core['kind']}\n")
                fh.write(f"    src: {to_posix(core['src'])}\n")
                if core.get("lib"):
                    fh.write(f"    lib: {core['lib']}\n")
        else:
            fh.write("ip_cores: []\n")
        fh.write("\n")

        fh.write("include_dirs_global:\n")
        fh.write("  - src\n")
        fh.write("  - sim\n\n")

        fh.write("env: {}\n")

    return y


# ---------------- Staging ----------------
HDL_EXT = {".vhd", ".vhdl", ".v", ".sv"}
CONSTR_EXT = {".xdc", ".lpf", ".pdc", ".sdc"}

# Diamond IP container extensions that the backend can consume. Anything not
# here (e.g. .sbx, .mem) is copied to ip/ but not emitted as an ip_cores entry.
_IP_KIND_BY_EXT = {
    ".ipx": "ipx",
    ".lpc": "lpc",
    ".edf": "edf",
    ".edn": "edf",
    ".edif": "edf",
}

def ip_kind_for_ext(ext: str) -> Optional[str]:
    """Map a file extension to the aurig-build ip_cores kind.
    Returns None if the extension is not a supported IP container."""
    return _IP_KIND_BY_EXT.get(ext.lower())

def stage_one(src_root: str, dst_root: str, p: str, mode: str) -> str:
    """
    Stage a single path p (relative to src_root if not absolute) into
    dst_root/<parent_tail>/<file>. Returns the final absolute dest path or "".
    """
    src = p
    if is_rel_or_volume_rel(src):
        src = joinf(src_root, src)
    src = npath(src)
    if not os.path.exists(src):
        warn(f"missing: {p}")
        return ""
    dst_dir = ensure_dir(joinf(dst_root, tail_parent(src)))
    dst = unique_path(joinf(dst_dir, os.path.basename(src)))
    copy_or_link(src, dst, mode)
    return dst

# ---------------- programmatic entry ----------------
def import_project(input_path: str, dest: str, name: str = "", force: bool = False) -> int:
    """Run the Diamond importer programmatically (used by `aurig-build import`).

    Returns a process exit code (0 on success).
    """
    argv = [input_path, "--dest", dest]
    if force:
        argv.append("--force")
    if name:
        argv += ["--name", name]
    try:
        rc = main(argv)
        return 0 if rc is None else int(rc)
    except SystemExit as exc:
        return 0 if exc.code is None else int(exc.code)

# ---------------- Main ----------------
def main(argv=None):
    global VERBOSE
    ap = argparse.ArgumentParser(description="Diamond importer (Python port)")
    ap.add_argument("diamond_project_folder", help="Folder containing .ldf")
    ap.add_argument("--dest", default=".", help="Destination root (e.g., ./temp/test)")
    ap.add_argument("--name", default="", help="Project name (defaults to .ldf basename)")
    ap.add_argument("--mode", choices=["copy", "link"], default="copy", help="copy (default) or link")
    ap.add_argument("--verbose", action="store_true", help="Verbose logging")
    ap.add_argument("--force", action="store_true", help="Proceed if --dest exists and not empty")
    ap.add_argument("--tpl-docs", action="store_true", help="Copy docs/ from template root into dest")
    ap.add_argument("--tpl-tools", action="store_true", help="Copy tools/ from template root into dest")
    ap.add_argument("--no-yaml", action="store_true", help="Do not write config/project.yaml")
    # accept --yaml for parity with Tcl, though default is to write it
    ap.add_argument("--yaml", action="store_true", help="(no-op; YAML is written by default unless --no-yaml)")
    ap.add_argument("--tool-version", default="", help="Write tool.synth.version into YAML (e.g. 3.14)")
    ap.add_argument("--bin-win", default="", help="Write tool.synth.bin_dir.windows into YAML")
    ap.add_argument("--bin-linux", default="", help="Write tool.synth.bin_dir.linux into YAML")

    args = ap.parse_args(argv)

    VERBOSE = args.verbose

    src = npath(args.diamond_project_folder)
    if not os.path.isdir(src):
        err(f"Folder not found: {src}")
        sys.exit(2)

    # Locate LDF
    ldfs = [f for f in os.listdir(src) if f.lower().endswith(".ldf")]
    if not ldfs:
        err(f"No .ldf found under {src}")
        sys.exit(2)
    ldf = npath(os.path.join(src, ldfs[0]))

    # Parse LDF
    M = parse_ldf_xml(ldf)
    impl  = M.get("impl") or "impl1"
    top   = M.get("top") or ""
    part  = M.get("device") or ""
    default_lib = M.get("default_lib") or ""   # <-- NEW
    srcs  = M.get("src") or []

    if not top:
        err("Top not found in LDF")
        sys.exit(2)
    if not part:
        err("Device not found in LDF")
        sys.exit(2)
    name = args.name or os.path.splitext(os.path.basename(ldf))[0]

    # Prepare destination
    dest = npath(args.dest)
    if os.path.exists(dest) and not os.path.isdir(dest):
        err("--dest exists and is not a directory")
        sys.exit(2)
    if os.path.isdir(dest) and not args.force:
        entries = [e for e in os.listdir(dest) if e not in (".gitignore",)]
        if entries:
            # interactive prompt (parity with Tcl)
            print(f"Destination '{dest}' exists and is not empty. Overwrite/merge? [y/N]: ", end="", flush=True)
            try:
                ans = sys.stdin.readline().strip().lower()
            except Exception:
                ans = "n"
            if not ans.startswith("y"):
                err("Aborted by user.")
                sys.exit(2)

    dst_src_root = ensure_dir(joinf(dest, "src"))       # we will do src/<lib>/...
    d_con        = ensure_dir(joinf(dest, "constraints"))
    d_ip         = ensure_dir(joinf(dest, "ip"))
    d_wrk        = ensure_dir(joinf(dest, "impl", "work", "diamond", name))


    if VERBOSE:
        info(f"Found {len(srcs)} sources in LDF:")
        for p in srcs:
            print(f"  - {p}")

    libs_used: set[str] = set()
    staged_pdc_rel: List[str] = []
    staged_lpf_rel: List[str] = []
    staged_sdc_rel: List[str] = []
    ip_cores_emit: List[Dict] = []

    d_src = ensure_dir(joinf(dest, "src"))          # we’ll do src/<lib>/...
    d_con = ensure_dir(joinf(dest, "constraints"))
    d_ip  = ensure_dir(joinf(dest, "ip"))

    for s in srcs:
        rel = s["path"]
        kind = s.get("kind", "")
        lib  = (s.get("lib") or default_lib or "work")

        # Resolve to abs path (relative -> relative to src project folder)
        src_abs = rel if os.path.isabs(rel) else joinf(src, rel)
        src_abs = npath(src_abs)

        if not os.path.exists(src_abs):
            warn(f"missing: {rel}")
            continue

        ext = os.path.splitext(src_abs)[1].lower()

        if kind in ("vhdl","verilog","sv"):
            dst_dir = ensure_dir(joinf(d_src, lib))
            dst = unique_path(joinf(dst_dir, os.path.basename(src_abs)))
            copy_or_link(src_abs, dst, args.mode)
            libs_used.add(lib)

        elif kind == "constr" or ext in CONSTR_EXT:
            dst = unique_path(joinf(d_con, os.path.basename(src_abs)))
            copy_or_link(src_abs, dst, args.mode)
            try:
                relp = os.path.relpath(dst, dest)
            except Exception:
                relp = dst
            if ext == ".pdc": staged_pdc_rel.append(relp)
            elif ext == ".lpf": staged_lpf_rel.append(relp)
            elif ext == ".sdc": staged_sdc_rel.append(relp)

        else:
            # IP cores / other -> ip/
            dst = unique_path(joinf(d_ip, os.path.basename(src_abs)))
            copy_or_link(src_abs, dst, args.mode)
            try:
                relp = os.path.relpath(dst, dest)
            except Exception:
                relp = dst
            ip_kind = ip_kind_for_ext(ext)
            if ip_kind:
                core = {"kind": ip_kind, "src": relp}
                # EDIF can target a non-default library; pass it through.
                if ip_kind == "edf" and lib and lib != "work":
                    core["lib"] = lib
                ip_cores_emit.append(core)
            elif ext == ".sbx":
                warn(f"IP container '{os.path.basename(src_abs)}' (.sbx) is not a "
                     "supported ip_cores kind; copied to ip/ but not emitted. "
                     "Provide the generated .ipx/.lpc/.edf instead.")


    # Copy docs/tools from template root (repo), not from the legacy project
    if args.tpl_docs or args.tpl_tools:
        tpl_root = find_template_root()
        if VERBOSE:
            info(f"Template root detected: {tpl_root}")
        if args.tpl_docs:
            src_docs = os.path.join(tpl_root, "docs")
            dst_docs = os.path.join(dest, "docs")
            n = copy_dir_contents(src_docs, dst_docs, verbose=VERBOSE)
            if VERBOSE:
                print(f"Copied {n} item(s) into docs/ from template.")
        if args.tpl_tools:
            src_tools = os.path.join(tpl_root, "tools")
            dst_tools = os.path.join(dest, "tools")
            n = copy_dir_contents(src_tools, dst_tools, verbose=VERBOSE)
            if VERBOSE:
                print(f"Copied {n} item(s) into tools/ from template.")

    write_default_root_artifacts(dest, name)

    # YAML
    if not args.no_yaml:
        info("Staging complete. Generating YAML...")
        # Prefer CLI-supplied values; else auto-detect pnmainc on PATH (Windows).
        bin_win = args.bin_win
        bin_linux = args.bin_linux
        tool_version = normalize_diamond_version(args.tool_version) if args.tool_version else ""
        if not bin_win and os.name == "nt":
            auto = detect_pnmainc_bin_on_windows()
            if auto:
                info(f"Detected pnmainc in PATH -> bin_dir.windows={auto}")
                bin_win = auto
        if not tool_version:
            detected_ver = (
                detect_diamond_version_from_path(bin_win)
                or detect_diamond_version_from_path(bin_linux)
            )
            if detected_ver:
                info(f"Detected Diamond version from path -> version={detected_ver}")
                tool_version = detected_ver

        y = write_yaml(
            dest_root=dest,
            name=name,
            top=top,
            part=part,
            pdc_relpaths=staged_pdc_rel,
            lpf_relpaths=staged_lpf_rel,
            libs_used=sorted(libs_used),
            tool_version=tool_version,
            bin_win=bin_win,
            bin_linux=bin_linux,
            sdc_relpaths=staged_sdc_rel,
            ip_cores_emit=ip_cores_emit,
        )


        info(f"YAML written: {y}")
        print("")
        print("Next steps:")
        print(f"  pnmainc tools/diamond/build.tcl \"{y}\" create")
        print(f"  pnmainc tools/diamond/build.tcl \"{y}\" synth")
        print(f"  pnmainc tools/diamond/build.tcl \"{y}\" impl")
        print(f"  pnmainc tools/diamond/build.tcl \"{y}\" bit")

    sys.exit(0)

if __name__ == "__main__":
    main()
