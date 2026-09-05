#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2024-2026 LogiMentor S.r.l.

"""
Quartus Project Importer

Behavior:
  - Parse an existing Quartus project (.qpf/.qsf)
  - Copy/symlink sources into standardized layout:
        <dest>/src/hdl/                 (VHDL/Verilog/SV, headers)
        <dest>/constraints/             (.sdc)
        <dest>/ip/                      (.qsys/.ip/.sopcinfo)
        <dest>/impl/work/quartus/<name> (generated minimal .qsf)
        <dest>/config/                  (optional YAML stub)
  - Recreate a minimal, clean .qsf (keeps extra assignments except
    TOP/DEVICE/VHDL_INPUT_VERSION that we re-emit cleanly).

Usage:
  aurig-build import --from quartus --input "C:\\path\\to\\old_quartus_proj" --dest temp/test_foo --name my_project

Or directly:
  py -3 -m aurig_build.quartus.import_ "C:\\path\\to\\old_quartus_proj" ^
     --dest temp/test_foo ^
     --name my_project ^
     --mode copy|link ^
     --vhdl 1993|2008 ^
     --yaml ^
     --verbose ^
     --dry-run ^
     --force
"""

import argparse
import os
import shlex
import shutil
import sys
from datetime import datetime
from typing import Dict, List, Tuple

# ------------- logging -------------
VERBOSE = False
def _ts(): return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
def info(m):  print(f"{_ts()} INFO    {m}")
def warn(m):  print(f"{_ts()} WARNING {m}", file=sys.stderr)
def err(m):   print(f"{_ts()} ERROR   {m}", file=sys.stderr)
def dbg(m):
    if VERBOSE:
        print(f"{_ts()} VERBOSE {m}")

# ------------- paths -------------
def npath(p: str) -> str:
    return os.path.normpath(os.path.abspath(p))

def ensure_dir(d: str) -> str:
    os.makedirs(d, exist_ok=True)
    return npath(d)

def joinf(*parts: str) -> str:
    return npath(os.path.join(*parts))

def relpath(from_dir: str, to_path: str) -> str:
    """Return to_path relative to from_dir, with native separators."""
    try:
        return os.path.relpath(to_path, from_dir)
    except Exception:
        return to_path

def unique_path(dst: str) -> str:
    base, ext = os.path.splitext(dst)
    i, out = 1, dst
    while os.path.exists(out):
        out = f"{base}_{i}{ext}"
        i += 1
    return out

def copy_or_link(src: str, dst: str, mode: str, dry: bool):
    action = "Link" if mode == "link" else "Copy"
    dbg(f"{action}: {src} -> {dst}")
    if dry:
        return
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if mode == "link":
        try:
            if os.path.exists(dst):
                os.remove(dst)
            os.symlink(src, dst)
            return
        except Exception as e:
            warn(f"symlink failed ({e}); copying instead")
    shutil.copy2(src, dst)

# ------------- YAML helpers -------------

def to_posix(p: str) -> str:
    return p.replace("\\", "/")

def _yaml_str(s: str) -> str:
    """Return s as a safely double-quoted YAML scalar.

    Applied to values that come from user input, project metadata, or the
    filesystem (project name, top, part, paths) so that spaces, colons, and
    other YAML-special characters cannot break the output.
    """
    escaped = s.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'

def derive_intel_family(part: str) -> str:
    """Map a Quartus part string to a canonical Intel/Altera device family name.

    Returns an empty string when the part prefix is unrecognised; callers
    substitute a "unknown" placeholder so the required device.family stays set.
    """
    if not part:
        return ""
    p = part.upper()
    # MAX 10
    if p.startswith("10M"):    return "max10"
    # Arria 10
    if p.startswith("10AX") or p.startswith("10AT"):  return "arria10"
    # Cyclone V / V SoC
    if p.startswith("5CSE") or p.startswith("5CSXFC"): return "cyclone5_soc"
    if p.startswith("5CEA") or p.startswith("5CEBA") or p.startswith("5CEFA"):
        return "cyclone5"
    if p.startswith("5CGX"):   return "cyclone5_gx"
    # Cyclone IV
    if p.startswith("EP4CGX"): return "cyclone4_gx"
    if p.startswith("EP4CE"):  return "cyclone4e"
    # Cyclone III
    if p.startswith("EP3C"):   return "cyclone3"
    # Cyclone II
    if p.startswith("EP2C"):   return "cyclone2"
    # Cyclone I
    if p.startswith("EP1C"):   return "cyclone"
    # Stratix V
    if p.startswith("5SGX"):   return "stratix5"
    # Stratix IV
    if p.startswith("EP4SGX"): return "stratix4"
    # Stratix III
    if p.startswith("EP3SL") or p.startswith("EP3SE"): return "stratix3"
    # Stratix II GX
    if p.startswith("EP2SGX"): return "stratix2_gx"
    # Arria GX (Stratix II GX variant)
    if p.startswith("EP2AGX"): return "arria_gx"
    # MAX II/IIZ
    if p.startswith("EPM"):    return "max2"
    return ""

# ------------- QPF -------------
def get_project_name_from_qpf(qpf_file: str) -> str:
    """
    Read PROJECT_REVISION from .qpf or return ''.
    """
    try:
        with open(qpf_file, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if "PROJECT_REVISION" in line:
                    # e.g. PROJECT_REVISION = my_rev
                    parts = line.split("=", 1)
                    if len(parts) == 2:
                        return parts[1].strip().strip('"')
    except FileNotFoundError:
        pass
    return ""

# ------------- QSF parsing -------------
def tokenize_qsf_line(line: str) -> List[str]:
    """
    Tokenize a QSF command line. Use shlex to respect quoted paths.
    """
    try:
        return shlex.split(line, posix=True)
    except Exception:
        # crude fallback: whitespace split
        return line.split()

def parse_qsf(qsf_file: str) -> Dict:
    """
    Parse QSF into buckets and metadata.

    Returns dict with:
      device: str
      top: str
      vhdl: list[(path, lib)]
      verilog: list[str]
      sv: list[str]
      headers: list[str]   (rare; kept for parity)
      sdc: list[str]
      ipfiles: list[str]   (.qsys/.ip/.sopcinfo/.qip-sip via IP_FILE/QSYS_FILE/SOPCINFO_FILE)
      assignments: list[str] (all set_*assignment lines, for carryover)
      any_2008: bool
    """
    R = {
        "device": "",
        "top": "",
        "vhdl": [],
        "verilog": [],
        "sv": [],
        "headers": [],
        "sdc": [],
        "ipfiles": [],
        "assignments": [],
        "any_2008": False,
    }
    with open(qsf_file, "r", encoding="utf-8", errors="ignore") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            if line.lower().startswith("set_"):
                toks = tokenize_qsf_line(line)
                if not toks:
                    continue
                cmd = toks[0]
                name = ""
                value = ""
                lib = ""
                # scan args
                i = 1
                while i < len(toks):
                    t = toks[i]
                    if t == "-name" and i + 1 < len(toks):
                        name = toks[i + 1]
                        i += 2
                        continue
                    if t == "-library" and i + 1 < len(toks):
                        lib = toks[i + 1]
                        i += 2
                        continue
                    # first positional token after flags is the value
                    value = toks[i]
                    i += 1

                # normalize name case
                nlow = name.upper()

                if cmd == "set_global_assignment" and name:
                    if nlow == "DEVICE":
                        if not R["device"]:
                            R["device"] = value
                        R["assignments"].append(line)
                    elif nlow == "TOP_LEVEL_ENTITY":
                        if not R["top"]:
                            R["top"] = value
                        R["assignments"].append(line)
                    elif nlow == "VHDL_FILE":
                        R["vhdl"].append((value, lib or "work"))
                    elif nlow == "SYSTEMVERILOG_FILE":
                        R["sv"].append(value)
                    elif nlow == "VERILOG_FILE":
                        R["verilog"].append(value)
                    elif nlow == "SDC_FILE":
                        R["sdc"].append(value)
                    elif nlow in ("IP_FILE", "QSYS_FILE", "SOPCINFO_FILE", "QIP_FILE"):
                        R["ipfiles"].append(value)
                    elif nlow == "VHDL_INPUT_VERSION":
                        if "2008" in value.upper():
                            R["any_2008"] = True
                        R["assignments"].append(line)
                    else:
                        # carry everything else
                        R["assignments"].append(line)
                else:
                    # set_instance_assignment / others — carry over
                    R["assignments"].append(line)
            else:
                # comments or other lines — carry over to assignments to be safe
                R["assignments"].append(line)
    return R

# ------------- QSF writer (clean minimal) -------------
def write_qsf(dest_dir: str, name: str, top: str, part: str,
              vhdl_list: List[Tuple[str, str]],
              verilog_list: List[str],
              sv_list: List[str],
              sdc_list: List[str],
              extra_lines: List[str],
              vhdl_std: str) -> str:
    """
    Create a minimal .qsf, re-emitting our clean core assignments first,
    then appending all other lines except duplicates for TOP/DEVICE/VHDL_INPUT_VERSION.
    """
    ensure_dir(dest_dir)
    qsf_path = joinf(dest_dir, f"{name}.qsf")
    with open(qsf_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(f"set_global_assignment -name TOP_LEVEL_ENTITY {top}\n")
        fh.write(f"set_global_assignment -name DEVICE {part}\n")
        fh.write(f"set_global_assignment -name VHDL_INPUT_VERSION VHDL_{vhdl_std.upper()}\n")
        fh.write("set_global_assignment -name NUM_PARALLEL_PROCESSORS 8\n")

        for rel, lib in vhdl_list:
            fh.write(f"set_global_assignment -name VHDL_FILE \"{rel}\" -library {lib}\n")
        for rel in sv_list:
            fh.write(f"set_global_assignment -name SYSTEMVERILOG_FILE \"{rel}\"\n")
        for rel in verilog_list:
            fh.write(f"set_global_assignment -name VERILOG_FILE \"{rel}\"\n")
        for rel in sdc_list:
            fh.write(f"set_global_assignment -name SDC_FILE \"{rel}\"\n")

        skip_prefix = (
            "set_global_assignment -name DEVICE ",
            "set_global_assignment -name TOP_LEVEL_ENTITY ",
            "set_global_assignment -name VHDL_INPUT_VERSION ",
        )
        for ln in extra_lines:
            # Filter out duplicates of the 3 we just wrote
            lns = ln.strip()
            if any(lns.upper().startswith(p.upper()) for p in skip_prefix):
                continue
            fh.write(ln.rstrip() + "\n")
    return qsf_path

# ------------- YAML writer (canonical manifest-v1) -------------
def write_yaml_canonical(
    dest_root: str,
    name: str,
    top: str,
    part: str,
    vhdl_std: str,
    vhdl_libs: List[str],
    has_verilog: bool,
    has_sv: bool,
    sdc_yaml_paths: List[str],
    has_ip: bool,
    qpf_path: str = "",
) -> str:
    """Write a canonical aurig-build project YAML aligned with the manifest-v1 schema."""
    cfgdir = ensure_dir(joinf(dest_root, "config"))
    ypath = joinf(cfgdir, "project.yaml")

    vendor = "intel"
    # Schema v1 requires device.family; fall back to "unknown" when the part
    # string is unrecognized (discovery.confidence.device flags this as low).
    family = derive_intel_family(part) or "unknown"
    generated_at = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    src_path = to_posix(qpf_path) if qpf_path else ""

    # Confidence assessment. A placeholder family is as uncertain as a missing
    # part, so it lowers device confidence too.
    missing_info: List[str] = []
    top_confidence    = "high" if top else "low"
    device_confidence = "high" if (part and family != "unknown") else "low"
    if not top:
        missing_info.append("top")
    if not part or family == "unknown":
        missing_info.append("device")
    # Multiple VHDL libs staged flat → runtime cannot distinguish them by glob
    multi_lib = len(set(vhdl_libs)) > 1

    with open(ypath, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("# ============================================================================\n")
        fh.write("# AURIG Build project configuration (Quartus)\n")
        fh.write("# ============================================================================\n\n")

        # --- canonical producer field ---
        fh.write('schema_version: "1"\n\n')

        # --- project metadata ---
        fh.write(f"project_name: {_yaml_str(name)}\n")
        # project_root: .. is correct — the importer stages everything under dest_root/
        # and writes this YAML to dest_root/config/project.yaml, so the project root
        # is always exactly one level up from the config directory.
        fh.write("project_root: ..\n")
        fh.write(f"top: {_yaml_str(top)}\n")
        fh.write("require_exact_versions: false\n")
        fh.write("debug_paths: false\n\n")

        # --- tool ---
        fh.write("tool:\n")
        fh.write("  synth:\n")
        fh.write("    kind: quartus\n")
        fh.write('    version: ""\n')
        fh.write("    exe: quartus_sh\n")
        fh.write("    # env_script:\n")
        fh.write("    #   windows: C:/intelFPGA_lite/23.1/quartus/adm/qenv.bat\n")
        fh.write("    #   linux:   /opt/intelFPGA/23.1/quartus/adm/qenv.sh\n\n")

        # --- device ---
        fh.write("device:\n")
        fh.write(f"  vendor: {vendor}\n")
        fh.write(f"  family: {family}\n")
        fh.write(f"  part: {_yaml_str(part)}\n\n")

        # --- board ---
        if sdc_yaml_paths:
            fh.write("board:\n")
            fh.write("  sdc_files:\n")
            for p in sdc_yaml_paths:
                fh.write(f"    - {_yaml_str(p)}\n")
            fh.write("\n")
        else:
            fh.write("board: {}\n\n")

        # --- file_sets (canonical shape) ---
        fh.write("file_sets:\n")
        fh.write("  rtl:\n")
        if not vhdl_libs:
            fh.write("    []\n")
        else:
            if multi_lib:
                # All HDL was staged flat into src/hdl/ without per-library
                # subdirectories; glob patterns below cannot isolate individual
                # libraries at runtime.  Consider reorganising into src/<lib>/.
                fh.write("    # NOTE: multiple VHDL libraries detected in the original QSF.\n")
                fh.write("    # All files were staged to src/hdl/ (flat layout). These glob\n")
                fh.write("    # patterns cannot distinguish libraries at runtime; reorganise\n")
                fh.write("    # into src/<lib>/ subdirectories if library isolation is needed.\n")
            for lib in vhdl_libs:
                fh.write(f"    - lib: {lib}\n")
                fh.write(f'      vhdl_std: "{vhdl_std}"\n')
                fh.write( "      src:\n")
                fh.write( "        - src/hdl/**/*.vhd\n")
                # V/SV globs only on the 'work' entry (or the sole entry when
                # there are no separate libs) to avoid duplicate compilation.
                if lib == "work" or not multi_lib:
                    if has_verilog:
                        fh.write( "        - src/hdl/**/*.v\n")
                    if has_sv:
                        fh.write( "        - src/hdl/**/*.sv\n")
                # include: emitted for every lib because the importer stages all
                # HDL types (VHDL/V/SV) into the same src/hdl/ directory.  Tools
                # that encounter VHDL-only libs will simply ignore it.
                fh.write( "      include:\n")
                fh.write( "        - src/hdl\n")
        fh.write("\n")

        # --- global includes and env ---
        fh.write("include_dirs_global:\n")
        fh.write("  - src\n\n")

        fh.write("env: {}\n\n")

        # --- quartus section (emitted only when IP files were staged) ---
        if has_ip:
            fh.write("quartus:\n")
            # IP files were staged to ip/; exact names depend on unique_path
            # deconfliction so we cannot emit accurate paths here.
            fh.write("  # IP files were staged to ip/; review and fill in actual paths.\n")
            fh.write("  # qip_files:\n")
            fh.write("  #   - ip/**/*.qip\n")
            fh.write("  # qsys_files:\n")
            fh.write("  #   - ip/**/*.qsys\n\n")

        # --- origin (canonical producer metadata) ---
        fh.write("origin:\n")
        fh.write("  generator: aurig_build_quartus_importer\n")
        fh.write('  generator_version: "1.0"\n')
        fh.write(f'  generated_at: "{generated_at}"\n')
        fh.write("  source:\n")
        fh.write("    kind: quartus_project\n")
        fh.write(f"    path: {_yaml_str(src_path)}\n\n")

        # --- discovery ---
        if multi_lib:
            missing_info.append("library_isolation")
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

# ------------- staging -------------
def stage_one(src_root: str, dst_root: str, qsf_dir: str, from_path: str,
              mode: str, dry: bool) -> str:
    """
    Stage a single file referenced by QSF:
      - Resolve relative to src_root if not absolute
      - Copy/link into dst_root/<parent_dir_name>/<filename>
      - Return path relative to qsf_dir (for writing into QSF)
    """
    src = from_path
    if not os.path.isabs(src):
        src = joinf(src_root, src)
    src = npath(src)
    if not os.path.exists(src):
        warn(f"missing file referenced by QSF: {from_path}")
        return ""
    parent_tail = os.path.basename(os.path.dirname(src))
    dst_dir = ensure_dir(joinf(dst_root, parent_tail))
    dst = unique_path(joinf(dst_dir, os.path.basename(src)))
    copy_or_link(src, dst, mode, dry)
    return relpath(qsf_dir, dst)

# ------------- programmatic entry -------------
def import_project(input_path: str, dest: str, name: str = "", force: bool = False) -> int:
    """Run the Quartus importer programmatically (used by `aurig-build import`).

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

# ------------- main -------------
def main(argv=None):
    global VERBOSE
    ap = argparse.ArgumentParser(description="Quartus Project Importer (Python)")
    ap.add_argument("quartus_project_folder", help="Path to folder containing .qpf/.qsf")
    ap.add_argument("--dest", default=".", help="Destination root (e.g., ./temp/test_foo)")
    ap.add_argument("--name", default="", help="Project name (defaults to .qpf/.qsf basename)")
    ap.add_argument("--mode", choices=["copy", "link"], default="copy", help="copy (default) or link")
    ap.add_argument("--vhdl", choices=["1993", "2008"], default="", help="VHDL standard override")
    ap.add_argument("--yaml", action="store_true", help="Write config/project.yaml (default on unless --no-yaml)")
    ap.add_argument("--no-yaml", action="store_true", help="Do not write YAML")
    ap.add_argument("--verbose", action="store_true", help="Verbose logging")
    ap.add_argument("--dry-run", action="store_true", help="Only log actions")
    ap.add_argument("--force", action="store_true", help="Do not prompt when --dest is non-empty")
    args = ap.parse_args(argv)

    VERBOSE = args.verbose

    src_folder = npath(args.quartus_project_folder)
    if not os.path.isdir(src_folder):
        err(f"Folder not found: {src_folder}")
        sys.exit(2)

    # Find QPF/QSF
    qpf = ""
    qsf = ""
    for f in os.listdir(src_folder):
        if f.lower().endswith(".qpf"):
            qpf = joinf(src_folder, f)
        if f.lower().endswith(".qsf"):
            qsf = joinf(src_folder, f)
    if not qsf:
        err(f"No .qsf found in {src_folder}")
        sys.exit(2)

    base = os.path.splitext(os.path.basename(qsf))[0]
    proj_name = get_project_name_from_qpf(qpf) if qpf else base
    if not proj_name:
        proj_name = base
    if args.name:
        proj_name = args.name

    Q = parse_qsf(qsf)
    part = Q["device"]
    if not part:
        err(f"DEVICE not found in {qsf}")
        sys.exit(2)
    top = Q["top"] or proj_name

    dest_root = npath(args.dest)
    if os.path.exists(dest_root) and not os.path.isdir(dest_root):
        err("--dest exists and is not a directory")
        sys.exit(2)
    if os.path.isdir(dest_root) and not args.force and not args.dry_run:
        entries = [e for e in os.listdir(dest_root) if e not in (".gitignore",)]
        if entries:
            print(f"Destination '{dest_root}' exists and is not empty. Overwrite/merge? [y/N]: ", end="", flush=True)
            ans = sys.stdin.readline().strip().lower()
            if not ans.startswith("y"):
                err("Aborted by user.")
                sys.exit(2)

    # Layout
    dst_hdl  = joinf(dest_root, "src", "hdl")
    dst_sdc  = joinf(dest_root, "constraints")
    dst_ip   = joinf(dest_root, "ip")
    dst_qdir = joinf(dest_root, "impl", "work", "quartus", proj_name)

    if args.dry_run:
        info("Would create dirs:")
        for d in (dst_hdl, dst_sdc, dst_ip, dst_qdir):
            print(f"  {d}")
    else:
        ensure_dir(dst_hdl)
        ensure_dir(dst_sdc)
        ensure_dir(dst_ip)
        ensure_dir(dst_qdir)

    if VERBOSE:
        info("Importing Quartus project:")
        info(f"  Source   : {src_folder}")
        info(f"  Name     : {proj_name}")
        info(f"  Top      : {top}")
        info(f"  Part     : {part}")
        info(f"  Mode     : {args.mode}")
        info(f"  Dest     : {dest_root}")
        if args.dry_run: info("  (dry-run)")

    # Decide VHDL std
    vhdl_std = args.vhdl or ("2008" if Q["any_2008"] else "1993")

    # Stage files and collect paths relative to qsf_dir
    rel_vhdl: List[Tuple[str, str]] = []
    rel_v:    List[str] = []
    rel_sv:   List[str] = []
    rel_sdc:  List[str] = []
    abs_sdc:  List[str] = []   # absolute staged paths, used for YAML relpath computation

    for path, lib in Q["vhdl"]:
        rel = stage_one(src_root=src_folder, dst_root=dst_hdl, qsf_dir=dst_qdir,
                        from_path=path, mode=args.mode, dry=args.dry_run)
        if rel:
            rel_vhdl.append((rel, lib))

    for path in Q["verilog"]:
        rel = stage_one(src_root=src_folder, dst_root=dst_hdl, qsf_dir=dst_qdir,
                        from_path=path, mode=args.mode, dry=args.dry_run)
        if rel:
            rel_v.append(rel)

    for path in Q["sv"]:
        rel = stage_one(src_root=src_folder, dst_root=dst_hdl, qsf_dir=dst_qdir,
                        from_path=path, mode=args.mode, dry=args.dry_run)
        if rel:
            rel_sv.append(rel)

    for path in Q["sdc"]:
        rel = stage_one(src_root=src_folder, dst_root=dst_sdc, qsf_dir=dst_qdir,
                        from_path=path, mode=args.mode, dry=args.dry_run)
        if rel:
            rel_sdc.append(rel)
            # Reconstruct absolute staged path so the YAML writer can express
            # it relative to dest_root rather than relative to the QSF directory.
            abs_sdc.append(npath(os.path.join(dst_qdir, rel)))

    for path in Q["ipfiles"]:
        _ = stage_one(src_root=src_folder, dst_root=dst_ip, qsf_dir=dst_qdir,
                      from_path=path, mode=args.mode, dry=args.dry_run)

    # Write QSF
    if args.dry_run:
        info(f"Would write QSF to: {joinf(dst_qdir, proj_name + '.qsf')}")
    else:
        qsf_out = write_qsf(
            dest_dir=dst_qdir, name=proj_name, top=top, part=part,
            vhdl_list=rel_vhdl, verilog_list=rel_v, sv_list=rel_sv,
            sdc_list=rel_sdc, extra_lines=Q["assignments"], vhdl_std=vhdl_std
        )
        info(f"QSF written: {qsf_out}")

    # YAML (canonical schema)
    want_yaml = args.yaml or (not args.no_yaml)
    if want_yaml:
        if args.dry_run:
            info(f"Would write YAML to: {joinf(dest_root, 'config', 'project.yaml')}")
        else:
            # Ordered-unique VHDL library names (preserves declaration order from QSF)
            vhdl_libs = list(dict.fromkeys(lib for _, lib in Q["vhdl"]))
            if not vhdl_libs:
                # No VHDL at all — seed work for any Verilog/SV
                if Q["verilog"] or Q["sv"]:
                    vhdl_libs = ["work"]
            elif (Q["verilog"] or Q["sv"]) and "work" not in vhdl_libs:
                # Verilog/SV is conventionally work; add it so the YAML entry exists
                vhdl_libs.append("work")

            # SDC paths expressed relative to dest_root for the YAML consumer
            sdc_yaml_paths = [
                to_posix(os.path.relpath(a, dest_root))
                for a in abs_sdc
            ]

            y = write_yaml_canonical(
                dest_root=dest_root,
                name=proj_name,
                top=top,
                part=part,
                vhdl_std=vhdl_std,
                vhdl_libs=vhdl_libs,
                has_verilog=bool(Q["verilog"]),
                has_sv=bool(Q["sv"]),
                sdc_yaml_paths=sdc_yaml_paths,
                has_ip=bool(Q["ipfiles"]),
                qpf_path=qpf or qsf,
            )
            info(f"YAML written: {y}")

    info("Import completed.")
    sys.exit(0)

if __name__ == "__main__":
    main()
