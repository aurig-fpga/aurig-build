#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2024-2026 LogiMentor S.r.l.

"""
aurig-build - FPGA Project Automation (Python-only front door)
- Unified targets: project | synth | impl | bit | exporthw | sim
- Defaults to config/project.yaml unless --cfg is given
- Auto-selects vendor tools from YAML (tool.synth / tool.sim)
- Version-aware env bootstrap (settings scripts / bin dirs) per OS
"""

import argparse, atexit, os, sys, subprocess, yaml, shlex, platform, re, shutil
import tempfile
from pathlib import Path

from . import _env, __version__
from .schema import normalize, validate
from .schema.errors import ManifestError

# Self-location for portable paths
SELF_DIR = Path(__file__).resolve().parent
DEFAULT_CFG = SELF_DIR / "config" / "project.yaml"

# Default executable name per tool kind, used when tool.synth.exe / tool.sim.exe
# is omitted. The per-vendor build dispatchers have matching inline defaults;
# keep this map in sync if either side changes.
_DEFAULT_EXE_FOR_KIND = {
    "vivado":  "vivado",
    "xsim":    "vivado",
    "quartus": "quartus_sh",
    "questa":  "vsim",
    "diamond": "pnmainc",
    "radiant": "radiantc",
}

# -------------------------
# Small utilities
# -------------------------

def on_windows() -> bool:
    return platform.system().lower().startswith("win")

def _which(exe: str, env=None):
    if env is None:
        return shutil.which(exe)
    # Windows sometimes exposes 'Path' instead of 'PATH'
    path_var = env.get("PATH") or env.get("Path") or ""
    return shutil.which(exe, path=path_var)

def _deep_merge(base: dict, overlay: dict) -> dict:
    """Recursive dict merge. overlay wins on conflicts. Lists / scalars are
    replaced wholesale (docker-compose.override.yaml convention). Returns a
    new dict; base and overlay are not mutated.
    """
    out = dict(base)
    for k, ov in overlay.items():
        if isinstance(out.get(k), dict) and isinstance(ov, dict):
            out[k] = _deep_merge(out[k], ov)
        else:
            out[k] = ov
    return out


def _overlay_path_for(base: Path) -> Path:
    """Return the conventional `<stem>.local<suffix>` sibling for a base cfg.

    The overlay's extension follows the base's, so `project.yaml` looks for
    `project.local.yaml` and `project.yml` looks for `project.local.yml`.
    """
    return base.with_name(f"{base.stem}.local{base.suffix}")


def _validate_top_level_mapping(parsed, source: Path) -> dict:
    """Reject YAML files whose top-level value is not a mapping.

    PyYAML's safe_load returns None for empty files (treated as {}) and
    Python types (dict / list / int / str / bool / None) for any other
    document. We accept None and dict; everything else gets a clean
    SystemExit(2) instead of failing later with an opaque AttributeError
    inside _deep_merge.
    """
    if parsed is None:
        return {}
    if isinstance(parsed, dict):
        return parsed
    print(
        f"[ERROR] {source}: top-level YAML must be a mapping (got {type(parsed).__name__}).",
        file=sys.stderr,
    )
    sys.exit(2)


def read_yaml(path: Path) -> dict:
    """Load a YAML config and, if a sibling `<stem>.local<suffix>` exists
    next to it (e.g. `project.local.yaml` for a `.yaml` base, or
    `project.local.yml` for a `.yml` base), deep-merge that overlay on top.

    The overlay is the per-machine / per-developer escape hatch: commit
    `project.yaml` with the team's defaults, and
    each machine can drop a gitignored `project.local.yaml` next to it to
    override `tool.synth.version`, `tool.synth.env_script`, etc. without
    touching the committed file.

    Merge semantics:
      - Dict values are merged recursively (overlay's keys win).
      - List / scalar / None values from the overlay replace the base
        value wholesale (so e.g. overlay can blank out `tool.synth.exe`).

    Note: this returns the merged Python view only. Downstream TCL
    backends (build.tcl / sim.tcl) reparse the YAML themselves and would
    miss the overlay unless they read the same path. `main()` writes the
    merged content to a side-file via `materialize_merged_cfg` and routes
    the dispatchers there.
    """
    path = Path(path)
    # Use _validate_top_level_mapping rather than `safe_load(f) or {}`: the
    # latter coerces any falsy YAML value (False, 0, "", []) into {} and
    # would silently bypass the type check we want here.
    with open(path, "r", encoding="utf-8") as f:
        cfg = _validate_top_level_mapping(yaml.safe_load(f), path)
    overlay_path = _overlay_path_for(path)
    if overlay_path.is_file():
        with open(overlay_path, "r", encoding="utf-8") as f:
            overlay = _validate_top_level_mapping(yaml.safe_load(f), overlay_path)
        if overlay:
            print(f"[INFO] Applying local overlay: {overlay_path}", file=sys.stderr)
            cfg = _deep_merge(cfg, overlay)
    return cfg


def resolve_manifest(base_path, pre_normalize=None, post_normalize=None):
    """Run the mandatory load pipeline and return ``(cfg, warnings)``.

    Order (handoff section 1): read base + ``.local`` overlay -> optional
    ``pre_normalize`` (canonical CLI injection) -> alias normalization ->
    optional ``post_normalize`` (e.g. framework-aware sim default) ->
    validate against the bundled ``manifest-v1.json``. Validation is the
    last step and runs on the fully-resolved canonical document.

    ``pre_normalize`` / ``post_normalize`` are callables that take the cfg
    dict and return it (mutated). They let the CLI front-door inject
    canonical overrides before normalization and apply build-engine defaults
    after it without scattering the pipeline. Raises ``ManifestError`` for
    dead aliases or schema violations.
    """
    cfg = read_yaml(Path(base_path))
    if pre_normalize is not None:
        cfg = pre_normalize(cfg)
    cfg, norm_warnings = normalize(cfg)
    if post_normalize is not None:
        cfg = post_normalize(cfg)
    val_warnings = validate(cfg)
    return cfg, norm_warnings + val_warnings


def materialize_merged_cfg(base_path: Path, merged_cfg: dict) -> Path:
    """Write merged_cfg to a side-file next to base_path and return that path
    when the effective config differs from the raw base file — either because
    a `<stem>.local<suffix>` overlay contributed content, or because
    normalization / CLI overrides changed it. Otherwise return base_path
    unchanged.

    The second trigger keeps the TCL backends canonical: a no-overlay manifest
    carrying legacy aliases (or an int `vhdl_std`) would otherwise reach the
    backends un-normalized from the raw base while Python dispatched on the
    canonical cfg — a split-brain.

    Writing the merged YAML next to the base (not in /tmp) preserves the
    `project_root` and `aurig_build/<x>/` adjacency that TCL's
    `compute_project_root` relies on. The temp file is named
    `.<stem>.merged.<random><suffix>` (a dotfile, with the mkstemp random
    component, and the same suffix as the base — `.yaml` or `.yml`),
    auto-deleted at process exit. The matching `.gitignore` patterns are
    `.*.merged.*.yaml` / `.*.merged.*.yml` (alongside `*.local.yaml` /
    `*.local.yml` for the overlay itself) in case a crash skips the
    atexit callback.
    """
    overlay_path = _overlay_path_for(base_path)
    overlay: dict = {}
    if overlay_path.is_file():
        # Reuse the same validator read_yaml uses so non-mapping overlays fail
        # loudly here too (instead of silently returning base_path and creating
        # a contract mismatch with read_yaml). An empty overlay returns {}.
        with open(overlay_path, "r", encoding="utf-8") as f:
            overlay = _validate_top_level_mapping(yaml.safe_load(f), overlay_path)
    if not overlay:
        # No overlay content: only materialize when normalization / overrides
        # actually changed the document relative to the raw base file.
        with open(base_path, "r", encoding="utf-8") as f:
            raw_base = yaml.safe_load(f)
        if merged_cfg == raw_base:
            return base_path
    merged_path: Path | None = None
    try:
        fd, merged_str = tempfile.mkstemp(
            prefix=f".{base_path.stem}.merged.",
            suffix=base_path.suffix,
            dir=str(base_path.parent),
            text=True,
        )
        merged_path = Path(merged_str)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                yaml.safe_dump(merged_cfg, f, sort_keys=False)
        except Exception:
            # mkstemp created the file; the write or fdopen failed. If fdopen
            # raised before taking ownership of fd, fd is still open — close
            # it best-effort. Then unlink the (empty/partial) side-file so we
            # don't leak descriptors or stale files. Both are best-effort and
            # silenced; we re-raise so the outer OSError handler still gets
            # to print the clean [ERROR] message.
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                merged_path.unlink()
            except OSError:
                pass
            raise
    except Exception as e:
        # OSError covers the common cases (read-only checkout, no permission,
        # disk full); the broader Exception net also catches PyYAML
        # representer errors / RecursionError on cyclic refs / etc. so the
        # consumer sees the [ERROR] + sys.exit(2) pattern used elsewhere in
        # run.py instead of a Python traceback. SystemExit (raised by inner
        # validators) and KeyboardInterrupt are BaseException, not caught.
        print(
            f"[ERROR] Could not materialize merged config next to {base_path}: {e}",
            file=sys.stderr,
        )
        sys.exit(2)
    def _cleanup(p: Path) -> None:
        # Best-effort: the try/except OSError already covers
        # FileNotFoundError (TOCTOU race) and permission/IO errors, so
        # atexit doesn't print a noisy traceback during interpreter shutdown.
        try:
            p.unlink()
        except OSError:
            pass
    # `merged_path` is `Path | None` only because of the try-block init;
    # the only ways out of the try are (a) success, where merged_path is set,
    # or (b) sys.exit, which never reaches here. Narrow the type for clarity.
    assert merged_path is not None
    atexit.register(_cleanup, merged_path)
    print(f"[INFO] Materialized merged config: {merged_path}", file=sys.stderr)
    return merged_path

def run(cmd, env=None):
    """Run a command (list or string) with pretty echo."""
    if isinstance(cmd, str):
        print(">>", cmd)
        return subprocess.call(cmd, shell=True, env=env)
    print(">>", " ".join(shlex.quote(c) for c in cmd))
    return subprocess.call(cmd, env=env)

# -------------------------
# Env/bootstrap helpers
# -------------------------

def _parse_diamond_version_from_text(s: str) -> str:
    # e.g. "Lattice Diamond Version 3.14.0.75.2" -> "3.14"
    m = re.search(r"Diamond\s+Version\s+(\d+\.\d+)", s, re.IGNORECASE)
    return m.group(1) if m else ""

def _parse_diamond_version_from_path(p: str) -> str:
    # e.g. C:\lscc\diamond\3.14\bin\nt64\pnmainc.EXE -> "3.14"
    m = re.search(r"[\\/](diamond)[\\/](\d+\.\d+)(?:[\\/]|$)", p, re.IGNORECASE)
    return m.group(2) if m else ""

def _diamond_version_guess(env: dict, exe_override: str = "") -> str:
    exe = exe_override or _which("pnmainc", env) or _which("pnmainc.exe", env)
    # 1) try to read from exe path
    if exe:
        v = _parse_diamond_version_from_path(exe)
        if v:
            return v
        # 2) try common flags / bare run
        for args in (["-v"], ["-version"], []):
            try:
                out = subprocess.check_output([exe] + args, text=True, stderr=subprocess.STDOUT, env=env, timeout=5)
                v = _parse_diamond_version_from_text(out)
                if v:
                    return v
            except Exception:
                pass
    # 3) scan PATH chunks for ...\diamond\X.Y\...
    for chunk in (env.get("PATH") or env.get("Path") or "").split(os.pathsep):
        v = _parse_diamond_version_from_path(chunk)
        if v:
            return v
    # 4) common env vars from Diamond installs
    for key in ("LSC_DIAMOND", "DIAMOND_ROOT", "DIAMOND_HOME"):
        v = _parse_diamond_version_from_path(env.get(key, ""))
        if v:
            return v
    return ""


def _parse_radiant_version_from_text(s: str) -> str:
    # e.g. "Lattice Radiant Software Version 2024.1.0.42" -> "2024.1"
    m = re.search(r"Radiant(?:\s+Software)?(?:\s+Version)?\s+v?(\d+\.\d+)", s, re.IGNORECASE)
    return m.group(1) if m else ""

def _parse_radiant_version_from_path(p: str) -> str:
    # e.g. C:\lscc\radiant\2024.1\bin\nt64\radiantc.EXE -> "2024.1"
    #      /usr/local/radiant_2024.1/bin/lin64/radiantc -> "2024.1"
    m = re.search(r"[\\/]radiant[_\-]?[\\/]?(\d+\.\d+)(?:[\\/]|$)", p, re.IGNORECASE)
    return m.group(1) if m else ""

def _radiant_version_guess(env: dict, exe_override: str = "") -> str:
    # exe_override is the already-resolved tool path from prepare_env (so the
    # configured tool.synth.exe is honored). Fall back to looking up radiantc
    # by name only when no override is provided.
    exe = exe_override or _which("radiantc", env) or _which("radiantc.exe", env)
    if exe:
        v = _parse_radiant_version_from_path(exe)
        if v:
            return v
        for args in (["-v"], ["-version"], []):
            try:
                out = subprocess.check_output([exe] + args, text=True, stderr=subprocess.STDOUT, env=env, timeout=5)
                v = _parse_radiant_version_from_text(out)
                if v:
                    return v
            except Exception:
                pass
    for chunk in (env.get("PATH") or env.get("Path") or "").split(os.pathsep):
        v = _parse_radiant_version_from_path(chunk)
        if v:
            return v
    for key in ("FOUNDRY", "LSC_RADIANT", "RADIANTDIR"):
        v = _parse_radiant_version_from_path(env.get(key, ""))
        if v:
            return v
    return ""


def _parse_quartus_version(s: str) -> str:
    m = re.search(r"Version\s+(\d+\.\d+(?:\.\d+)?)", s, re.IGNORECASE)
    return m.group(1) if m else ""

def _parse_questa_version(s: str) -> str:
    # Match either "Questa[word_chars] YYYY.N" (simple invocation) or
    # "vsim YYYY.N" (real vsim -version output: "# Questa Sim-64 vsim 2023.3 …")
    # Use a single search so the earliest match in the string is preserved.
    m = re.search(r"(?:Questa\w*|\bvsim)\s+v?(\d{4}\.\d+)", s, re.IGNORECASE)
    return m.group(1) if m else ""

def _running_version(exe: str, args, parse_fn, env=None) -> str:
    try:
        out = subprocess.check_output([exe] + args, text=True, env=env, stderr=subprocess.STDOUT)
        return parse_fn(out) or ""
    except Exception:
        return ""

def _sanitize_script_path(p: str) -> str:
    """
    Make a Windows/Linux script path safe for subprocess:
    - trim spaces, remove wrapping quotes (even escaped), expand env/~
    - normalize separators
    """
    if not p:
        return p
    s = str(p).strip()

    # Strip escaped wrapping quotes: \"...\" or \'...\'
    if (s.startswith(r'\"') and s.endswith(r'\"')) or (s.startswith(r"\'") and s.endswith(r"\'")):
        s = s[2:-2].strip()

    # Strip normal wrapping quotes: "..." or '...'
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        s = s[1:-1].strip()

    # Unescape any remaining \" or \'
    s = s.replace(r'\"', '"').replace(r"\'", "'")

    # Expand ~ and %VARS%/$VARS
    s = os.path.expandvars(os.path.expanduser(s))

    # Normalize path (forward slashes are OK on Windows too)
    s = os.path.normpath(s)
    return s

def _normalize_windows_path_key(e: dict) -> dict:
    """Ensure PATH key exists on Windows even if cmd.exe returned 'Path'."""
    if on_windows():
        if "PATH" not in e and "Path" in e:
            e["PATH"] = e["Path"]
    return e

def _source_script_into_env(env: dict, script_path: str) -> dict:
    """
    Call a vendor settings script and merge its environment into 'env'.
    Windows:
      - Write a temp .bat that does:
            call "<script>"
            set
        Then capture the output and merge.
    Linux/macOS:
      - source '<script>'; env
    """
    if not script_path:
        return env
    script_path = _sanitize_script_path(script_path)
    if not os.path.exists(script_path):
        print(f"[WARN] Env script not found: {script_path}", file=sys.stderr)
        return env

    if on_windows():
        # Use a temp .bat to avoid quoting/&& parsing issues
        with tempfile.NamedTemporaryFile(delete=False, suffix=".bat", mode="w", encoding="utf-8") as bat:
            bat.write("@echo off\r\n")
            bat.write(f'call "{script_path}"\r\n')
            bat.write("set\r\n")
            bat_path = bat.name

        try:
            # print debug
            print(f"[DEBUG] Running env script: {bat_path} with env {env}")
            out = subprocess.check_output(["cmd.exe", "/v:off", "/c", bat_path], text=True, env=env)
        except subprocess.CalledProcessError as e:
            print(f"[WARN] Env bootstrap failed via temp .bat: {bat_path}\n{e}", file=sys.stderr)
            try: os.remove(bat_path)
            except Exception: pass
            return env
        finally:
            try: os.remove(bat_path)
            except Exception: pass

        merged = env.copy()
        for line in out.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                merged[k.strip()] = v
        return _normalize_windows_path_key(merged)

    # Linux/macOS
    try:
        out = subprocess.check_output(
            ["bash", "-lc", f"source '{script_path}' >/dev/null 2>&1; env"],
            text=True,
            env=env,
        )
    except subprocess.CalledProcessError as e:
        print(f"[WARN] Env bootstrap failed: source '{script_path}'\n{e}", file=sys.stderr)
        return env

    merged = env.copy()
    for line in out.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            merged[k.strip()] = v
    return _normalize_windows_path_key(merged)

def _role_cfg(cfg: dict, role: str) -> dict:
    t = (cfg.get("tool") or {}).get(role, {}) or {}
    oskey = "windows" if on_windows() else "linux"
    env_script = _sanitize_script_path((t.get("env_script") or {}).get(oskey, "") or "")
    bin_dir    = _sanitize_script_path((t.get("bin_dir")    or {}).get(oskey, "") or "")
    return {
        "kind":       str(t.get("kind") or ""),
        "version":    str(t.get("version") or ""),
        "exe":        str(t.get("exe") or ""),
        "env_script": env_script,
        "bin_dir":    bin_dir,
    }

def _vivado_version_via_tcl(env: dict) -> str:
    """
    Get Vivado version using Tcl mode. Resolve the actual exe path first.
    """
    names = ["vivado.bat", "vivado"] if on_windows() else ["vivado"]
    exe_path = None
    for n in names:
        p = _which(n, env)
        if p:
            exe_path = p
            break
    if not exe_path:
        return ""
    try:
        proc = subprocess.run(
            [exe_path, "-mode", "tcl", "-nolog", "-nojournal", "-notrace"],
            input="puts [version -short]\nexit\n",
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )
        out = (proc.stdout or "") + "\n" + (proc.stderr or "")
        m = re.search(r"\b(\d{4}\.\d+)\b", out)
        return m.group(1) if m else ""
    except Exception:
        return ""

def prepare_env(cfg: dict, need_synth: bool, need_sim: bool) -> dict:
    """
    Prepare environment only for the requested roles (synth and/or sim),
    and enforce versions only for those roles.
    """
    env = os.environ.copy()
    strict = bool(cfg.get("require_exact_versions", True))

    def role_cfg(role: str) -> dict:
        t = (cfg.get("tool") or {}).get(role, {}) or {}
        oskey = "windows" if on_windows() else "linux"
        return {
            "kind":       str(t.get("kind") or ""),
            "version":    str(t.get("version") or ""),
            # Apply the same sanitize pipeline _resolve_vendor_exe uses (trim
            # wrapping quotes, expanduser/expandvars, normalize separators,
            # collapse "." to "") so that quoted / ~-prefixed exe values
            # resolve consistently in prepare_env's _which lookup, not only
            # in the dispatchers downstream.
            "exe":        _sanitize_exe_value(str(t.get("exe") or "")),
            "env_script": _sanitize_script_path((t.get("env_script") or {}).get(oskey, "") or ""),
            "bin_dir":    _sanitize_script_path((t.get("bin_dir")    or {}).get(oskey, "") or ""),
        }

    def get_version(kind: str, cur_exe_path: str = "") -> str:
        # cur_exe_path is the already-resolved tool path (from ensure() below);
        # passing it down lets every version probe honor tool.synth.exe instead
        # of re-resolving a hardcoded basename.
        if kind in ("vivado", "xsim"):
            return _vivado_version_via_tcl(env)
        if kind == "quartus":
            return _running_version(cur_exe_path or "quartus_sh", ["--version"], _parse_quartus_version, env)
        if kind == "questa":
            return _running_version(cur_exe_path or "vsim", ["-version"], _parse_questa_version, env)
        if kind == "diamond":
            return _diamond_version_guess(env, cur_exe_path)
        if kind == "radiant":
            return _radiant_version_guess(env, cur_exe_path)
        return ""

    def load_env(kind: str, env_script: str, bin_dir: str):
        nonlocal env
        if env_script:
            env = _source_script_into_env(env, env_script)
        if bin_dir:
            env = env.copy()
            env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")
        env = _normalize_windows_path_key(env)
        if _env.debug_enabled():
            print(f"[DBG] load_env: kind={kind}, bin_dir={bin_dir}")
            print(f"[DBG] which(quartus_sh) -> {_which('quartus_sh', env)}", file=sys.stderr)
            print(f"[DBG] which(pnmainc)    -> {_which('pnmainc', env)}", file=sys.stderr)



    def ensure(role: str):
        r = role_cfg(role)
        kind, want_ver, exe, env_script, bin_dir = r["kind"], r["version"], r["exe"], r["env_script"], r["bin_dir"]
        if not kind:
            return
        # VUnit runs the simulation via sys.executable (see sim_vunit) and
        # does not need a resolvable vendor exe — but it may still need
        # tool.sim.env_script / tool.sim.bin_dir to source license vars or
        # expose simulator binaries that VUnit itself shells out to. Apply
        # those env hints if provided, then skip the exe / version check.
        if role == "sim" and kind == "vunit":
            if env_script or bin_dir:
                load_env(kind, env_script, bin_dir)
            return
        # If tool.<role>.exe is omitted, fall back to the well-known binary
        # for this kind (e.g. radiant -> radiantc, questa -> vsim). Using the
        # kind verbatim would only ever work for Vivado.
        cur_exe  = exe or _DEFAULT_EXE_FOR_KIND.get(kind, kind)
        cur_path = _which(cur_exe, env)

        # load env if missing or any env hints are provided
        if cur_path is None or env_script or bin_dir:
            load_env(kind, env_script, bin_dir)
            cur_path = _which(cur_exe, env)

        # Basename fallback: if tool.<role>.exe is a path (absolute or with
        # a directory component) that does not resolve, try the bare basename
        # so a wrong directory in the configured exe still degrades to a PATH
        # lookup. Mirrors what the per-vendor build / sim dispatchers do via
        # _resolve_vendor_exe. On success we write the resolved path back
        # into cfg['tool'][role]['exe']; every consumer that reads exe
        # (vivado_build / quartus_build / diamond_build / radiant_build,
        # plus sim_questa / sim_xsim) will then use the corrected value.
        # VUnit is NOT affected by this path — the `kind == "vunit"` early
        # return above bypasses both the exe lookup and this write-back.
        if cur_path is None:
            base = os.path.basename(cur_exe)
            if base and base != cur_exe:
                fallback = _which(base, env)
                if fallback is not None:
                    print(
                        f"[WARN]  Configured {role} exe '{cur_exe}' not found; using '{fallback}' from PATH.",
                        file=sys.stderr,
                    )
                    cur_exe = base
                    cur_path = fallback
                    # Defensive normalize: YAML may have `tool: ~` or
                    # `tool.<role>: ~` (None nodes). setdefault() would not
                    # replace those, and the assignment would AttributeError.
                    if not isinstance(cfg.get("tool"), dict):
                        cfg["tool"] = {}
                    if not isinstance(cfg["tool"].get(role), dict):
                        cfg["tool"][role] = {}
                    cfg["tool"][role]["exe"] = fallback

        # version check only if reachable and version pinned
        cur_ver = ""
        if cur_path and want_ver:
            cur_ver = get_version(kind, cur_path)

        if want_ver and (cur_ver != want_ver):
            msg = f"{kind} version mismatch: want {want_ver}, found '{cur_ver or 'N/A'}'."
            if strict:
                print(f"[ERROR] {msg}", file=sys.stderr); sys.exit(2)
            else:
                print(f"[WARN]  {msg}")

        if _which(cur_exe, env) is None:
            print(f"[ERROR] '{cur_exe}' not found after env setup (role={role}).", file=sys.stderr)
            if env_script: print(f"        Tried env_script: {env_script}", file=sys.stderr)
            if bin_dir:    print(f"        Tried bin_dir:    {bin_dir}", file=sys.stderr)
            sys.exit(2)

    if need_synth: ensure("synth")
    if need_sim:   ensure("sim")
    return env

# -------------------------
# Project-root & tool exec
# -------------------------

def compute_project_root(cfg_path: Path, cfg: dict) -> Path:
    """
    Mirror build.tcl resolution:
      - default: cfg_dir/.. (when YAML is under aurig_build/config/)
        Since we're now inside aurig_build/, the default should be cfg_dir/../.. to get to project root
      - if YAML has project_root:
          * absolute -> use it
          * relative -> resolve relative to cfg_dir
      - AURIG_BUILD_PROJECT_ROOT env overrides (absolute or relative to cfg_dir)
    """
    cfg_dir = cfg_path.resolve().parent
    # Default: when config is under aurig_build/config/, go back two levels to reach project root
    root = cfg_dir.parent.parent if cfg_dir.parent.name == "aurig_build" else cfg_dir.parent

    # env override
    env_pr = os.environ.get("AURIG_BUILD_PROJECT_ROOT", "").strip()
    if env_pr:
        candidate = Path(env_pr)
        root = (candidate if candidate.is_absolute() else (cfg_dir / candidate)).resolve()

    # YAML
    pr_yaml = str((cfg or {}).get("project_root") or "").strip()
    if pr_yaml:
        candidate = Path(pr_yaml)
        root = (candidate if candidate.is_absolute() else (cfg_dir / candidate)).resolve()

    return root

def _resolve_tool_exe(names: list[str], env: dict) -> str:
    for n in names:
        p = _which(n, env)
        if p:
            return p
    return ""


def _sanitize_exe_value(raw: str) -> str:
    """Sanitize a user-supplied executable path and collapse nonsense input
    to "". Same pipeline that `_sanitize_script_path` already applies to
    `tool.synth.bin_dir` and `tool.synth.env_script` — trim wrapping quotes,
    expanduser (`~/...`), expandvars (`$VAR` / `${VAR}` on POSIX; those
    plus `%VAR%` on Windows — see os.path.expandvars), normalize path
    separators — plus a final step that turns the post-sanitize "." (which
    `os.path.normpath("")` produces from a quotes-only or whitespace-only
    value like `'""'` or `'"   "'`) into "" so callers can treat it as
    "unset" instead of looking for a tool literally named `.`.
    """
    if not raw:
        return ""
    s = _sanitize_script_path(raw)
    return "" if s in ("", ".") else s


def _resolve_vendor_exe(tool_cfg: dict, env: dict, default_name: str) -> tuple[str, str]:
    """Three-step resolution used by every per-vendor synth dispatcher
    (vivado_build / quartus_build / diamond_build / radiant_build) and the
    Questa / xsim sim dispatchers. sim_vunit bypasses this — it always runs
    a Python interpreter (sys.executable by default, or whatever
    `tool.sim.exe` configures) — but its driver and interpreter values
    still go through `_sanitize_exe_value` so quote / ~ / $VAR / %VAR%
    handling is uniform across all dispatch paths.

    Returns (resolved_abs_path, exe_field). resolved_abs_path is "" on
    failure; exe_field is the configured name (or default_name) for use
    in error messages. Steps:
      1) tool_cfg["exe"] as an absolute path that exists.
      2) PATH lookup by basename (PATHEXT-aware on Windows via shutil.which).
      3) tool_cfg["bin_dir"][os] searched by basename (also PATHEXT-aware),
         after running _sanitize_script_path on bin_dir and gating with
         isabs/isdir (prevents empty/relative bin_dir from collapsing to CWD).
    """
    # Strip first, fall back to default if the configured value is empty,
    # whitespace-only, quotes-only, or otherwise collapses to "." after
    # _sanitize_script_path. The shared helper applies the same pipeline
    # already used for bin_dir / env_script so a user who copies a quoted
    # Windows path from a shell or uses `~` / `$VAR` in YAML resolves
    # correctly. See _sanitize_exe_value for the "." collapse rationale.
    exe_field = _sanitize_exe_value(str(tool_cfg.get("exe") or "")) or default_name
    exe_basename = os.path.basename(exe_field) or default_name

    def _exists(p: str) -> bool:
        try:
            if not p or not os.path.isfile(p):
                return False
            if on_windows():
                return True
            return os.access(p, os.X_OK)
        except Exception:
            return False

    # 1) Absolute path in YAML. Use os.path.isabs only — the old `":" in
    # exe_field` belt-and-suspenders matched drive-relative forms like
    # `C:vivado` (a CWD-on-C: relative path), and any unrelated string
    # containing a colon. isabs handles real Windows abs paths (C:\..., \\..., /).
    if os.path.isabs(exe_field) and _exists(exe_field):
        return exe_field, exe_field

    # 2) PATH (merged env, by basename). shutil.which (via _which) honors
    # PATHEXT on Windows so vivado.bat / quartus_sh.cmd are picked up.
    p = _which(exe_basename, env) or ""
    if p:
        return p, exe_field

    # 3) YAML bin_dir (sanitized; guard against empty/relative to avoid CWD
    # pickup). shutil.which with path=bin_dir is PATHEXT-aware — important for
    # Vivado on Windows where the only runnable is vivado.bat.
    oskey = "windows" if on_windows() else "linux"
    bin_dir = _sanitize_script_path(((tool_cfg.get("bin_dir") or {}).get(oskey, "") or ""))
    if bin_dir and os.path.isabs(bin_dir) and os.path.isdir(bin_dir):
        p = shutil.which(exe_basename, path=bin_dir) or ""
        if p:
            return p, exe_field

    return "", exe_field


# -------------------------
# Dispatchers
# -------------------------

def vivado_build(action: str, cfg_path: Path, env: dict, cfg: dict) -> int:
    """
    Launch Vivado in batch mode.

    Resolution order (via _resolve_vendor_exe):
      1) Absolute exe path in YAML: tool.synth.exe
      2) PATH lookup by basename (PATHEXT-aware on Windows so vivado.bat is found)
      3) YAML tool.synth.bin_dir (sanitized)

    On Windows, .bat/.cmd execs are invoked via `cmd.exe /c` because
    subprocess.call cannot run them directly. Log/journal go straight
    into impl/work/vivado/logs (no post-move needed).
    """
    # tiny debug to verify PATH actually contains Vivado
    if _env.debug_enabled() and on_windows():
        p = subprocess.run(["cmd.exe", "/c", "where vivado"], capture_output=True, text=True, env=env)
        print("[DBG] where vivado ->\n" + (p.stdout or p.stderr), file=sys.stderr)

    synth = (cfg.get("tool") or {}).get("synth", {}) or {}
    # On Windows Xilinx ships vivado.bat (a wrapper), not vivado.exe. With the
    # default PATHEXT (.COM;.EXE;.BAT;...), `shutil.which("vivado")` would
    # prefer a stray vivado.exe from a foreign install over the .bat wrapper.
    # Pin the default to vivado.bat so the resolver matches the
    # pre-_resolve_vendor_exe behavior `["vivado.bat", "vivado"]`. The user
    # can still override with tool.synth.exe (their value wins over the default).
    default_exe = "vivado.bat" if on_windows() else "vivado"
    exe_path, exe_field = _resolve_vendor_exe(synth, env, default_exe)
    # Historical fallback: when the user did NOT set tool.synth.exe explicitly,
    # and the .bat-preferred default lookup failed, fall through to plain
    # `vivado` (PATHEXT order — matches `.exe`/`.com`/etc.). This preserves
    # `_resolve_tool_exe(["vivado.bat", "vivado"], env)` semantics from before
    # the refactor: installs that ship `vivado.exe` instead of `vivado.bat`
    # (containerized, portable, non-Xilinx) still resolve. Gate on the
    # SANITIZED value so quotes-only / whitespace-in-quotes inputs like
    # `exe: '""'` (which _resolve_vendor_exe already treats as unset) are
    # treated the same here.
    if not exe_path and on_windows() and not _sanitize_exe_value(str(synth.get("exe") or "")):
        exe_path, exe_field = _resolve_vendor_exe(synth, env, "vivado")
    if not exe_path:
        print(f"[ERROR] '{exe_field}' not found after env setup.", file=sys.stderr)
        if _env.debug_enabled():
            # Windows tends to expose `Path`; _which checks both. Mirror that
            # for the diagnostic.
            path_val = env.get("PATH") or env.get("Path") or ""
            print(f"[DBG] PATH={path_val}", file=sys.stderr)
        return 2

    # compute logs directory under project root (same convention as build.tcl)
    root = compute_project_root(cfg_path, cfg)
    logs_dir = root / "impl" / "work" / "vivado" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    # pick stable names; if you prefer per-action, use f"vivado_{action}.log"
    log_path = logs_dir / "vivado.log"
    jou_path = logs_dir / "vivado.jou"

    # Build the argument list - use SELF_DIR for portable paths
    args = [
        "-mode", "batch",
        "-log", str(log_path),
        "-journal", str(jou_path),
        "-source", str(SELF_DIR / "vivado" / "build.tcl"),
        "-tclargs", str(cfg_path), action,
    ]

    # If it's a batch file, run via cmd.exe /c; otherwise call directly
    if on_windows() and exe_path.lower().endswith((".bat", ".cmd")):
        cmd = ["cmd.exe", "/c", exe_path] + args
    else:
        cmd = [exe_path] + args

    return run(cmd, env=env)

# 
def quartus_build(action: str, cfg_path: Path, env: dict, cfg: dict) -> int:
    """
    Launch Quartus non-project flow using an absolute path to quartus_sh.
    Resolution order:
      1) Absolute exe path in YAML: tool.synth.exe
      2) PATH (using our merged env)
      3) YAML tool.synth.bin_dir
      4) QUARTUS_ROOTDIR/bin64 (if present)
    """
    synth = (cfg.get("tool") or {}).get("synth", {}) or {}
    exe_path, exe_field = _resolve_vendor_exe(synth, env, "quartus_sh")

    # 4) Try QUARTUS_ROOTDIR/bin[64] as a vendor-specific extra search dir.
    if not exe_path and env.get("QUARTUS_ROOTDIR"):
        exe_basename = os.path.basename(exe_field) or "quartus_sh"
        qbin = os.path.join(env["QUARTUS_ROOTDIR"], "bin64" if on_windows() else "bin")
        if os.path.isdir(qbin):
            exe_path = shutil.which(exe_basename, path=qbin) or ""

    if not exe_path:
        print(f"[ERROR] '{exe_field}' not found after env setup.", file=sys.stderr)
        if _env.debug_enabled():
            print(f"[DBG] PATH={env.get('PATH','')}", file=sys.stderr)
        return 2

    # Map aurig-build target name to Quartus argument (mirrors diamond_build).
    phase_map = {
        "project":  "create",
        "synth":    "synth",
        "impl":     "impl",
        "bit":      "bit",
        "exporthw": "bit",   # Quartus has no separate hw handoff; .sof is the artifact
    }
    qphase = phase_map.get(action, action)

    # Build the command using the absolute exe path we resolved and SELF_DIR for portable paths
    cmd = [exe_path, "-t", str(SELF_DIR / "quartus" / "build.tcl"), str(cfg_path), qphase]
    return run(cmd, env=env)

def diamond_build(action: str, cfg_path: Path, env: dict, cfg: dict) -> int:
    """
    Launch Lattice Diamond through our unified TCL:
      pnmainc aurig_build/diamond/build.tcl "<yaml>" <phase>
    Resolution order (mirrors quartus_build / radiant_build):
      1) Absolute exe path in YAML: tool.synth.exe
      2) PATH (using our merged env, by basename)
      3) YAML tool.synth.bin_dir (by basename, sanitized)
    Note: aurig-build target 'project' maps to Diamond 'create'.
    """
    synth = (cfg.get("tool") or {}).get("synth", {}) or {}
    exe_path, exe_field = _resolve_vendor_exe(synth, env, "pnmainc")

    if not exe_path:
        print(f"[ERROR] '{exe_field}' not found after env setup.", file=sys.stderr)
        if _env.debug_enabled():
            print(f"[DBG] PATH={env.get('PATH','')}", file=sys.stderr)
        return 2

    # Use SELF_DIR for portable path to build script
    build_tcl = SELF_DIR / "diamond" / "build.tcl"
    if not build_tcl.exists():
        print(f"[ERROR] Diamond build script not found: {build_tcl}", file=sys.stderr)
        return 2

    # Map aurig-build target name to Diamond argument
    phase_map = {
        "project":  "create",
        "synth":    "synth",
        "impl":     "impl",
        "bit":      "bit",
        "exporthw": "bit",   # Diamond doesn't have exporthw; reuse bit if asked
    }
    dphase = phase_map.get(action, action)

    cmd = [exe_path, str(build_tcl), str(cfg_path), dphase]
    return run(cmd, env=env)

def radiant_build(action: str, cfg_path: Path, env: dict, cfg: dict) -> int:
    """
    Launch Lattice Radiant through our unified TCL:
      radiantc aurig_build/radiant/build.tcl "<yaml>" <phase>
    Resolution order (mirrors quartus_build):
      1) Absolute exe path in YAML: tool.synth.exe
      2) PATH (using our merged env)
      3) YAML tool.synth.bin_dir
    Note: aurig-build target 'project' maps to Radiant 'create'.
    """
    synth = (cfg.get("tool") or {}).get("synth", {}) or {}
    exe_path, exe_field = _resolve_vendor_exe(synth, env, "radiantc")

    if not exe_path:
        print(f"[ERROR] '{exe_field}' not found after env setup.", file=sys.stderr)
        if _env.debug_enabled():
            print(f"[DBG] PATH={env.get('PATH','')}", file=sys.stderr)
        return 2

    build_tcl = SELF_DIR / "radiant" / "build.tcl"
    if not build_tcl.exists():
        print(f"[ERROR] Radiant build script not found: {build_tcl}", file=sys.stderr)
        return 2

    # Map aurig-build target name to Radiant argument (mirrors diamond_build).
    phase_map = {
        "project":  "create",
        "synth":    "synth",
        "impl":     "impl",
        "bit":      "bit",
        "exporthw": "bit",   # Radiant has no separate hw handoff; bitstream is the artifact
    }
    rphase = phase_map.get(action, action)

    cmd = [exe_path, str(build_tcl), str(cfg_path), rphase]
    return run(cmd, env=env)

def _yaml_default_tb(cfg: dict) -> str:
    """
    Return the default simulation testbench name from YAML config.

    The canonical key is ``sim.default_top_tb``. For backward
    compatibility, the legacy key ``sim.top_tb`` is also accepted as a
    fallback. If both are present, ``default_top_tb`` takes precedence.
    """
    sim = cfg.get("sim") or {}
    # canonical key is default_top_tb; top_tb is accepted as a legacy fallback
    return str(sim.get("default_top_tb") or sim.get("top_tb") or "")

def sim_vunit(cfg_path: Path, tb: str, extra: list, env: dict, cfg: dict | None = None) -> int:
    # tool.sim.driver lets the consumer place its VUnit driver wherever the
    # project layout dictates (e.g. tools/sim/vunit_driver.py for projects
    # with a license boundary on sim/). Falls back to "sim/run_vunit.py"
    # (resolved relative to the invocation CWD — typically the project root
    # when invoked via the standard Makefile, but aurig-build does not set cwd
    # itself). Both driver and interpreter go through _sanitize_exe_value
    # so quoted / ~-prefixed / $VAR / %VAR% YAML values resolve consistently
    # with the synth dispatchers; nonsense values (quotes-only / whitespace)
    # collapse to "" and the call site applies the default.
    sim_cfg = ((cfg or {}).get("tool") or {}).get("sim") or {}
    driver = _sanitize_exe_value(str(sim_cfg.get("driver") or "")) or "sim/run_vunit.py"
    # tool.sim.exe lets the consumer pick a specific Python interpreter (e.g.
    # "python3" on a system where "python" is python2, or an absolute path to
    # a venv). Defaults to sys.executable so a missing key Just Works.
    interpreter = _sanitize_exe_value(str(sim_cfg.get("exe") or "")) or sys.executable
    cmd = [interpreter, driver, "--cfg", str(cfg_path)]
    if tb: cmd += ["--tb", tb]
    if extra: cmd += extra
    return run(cmd, env=env)

def sim_questa(cfg_path: Path, tb: str, env: dict, cfg: dict) -> int:
    tb_eff = tb or _yaml_default_tb(cfg)
    if not tb_eff:
        print("[ERROR] Provide --tb or set sim.default_top_tb in YAML for Questa.", file=sys.stderr)
        return 2
    sim_cfg = (cfg.get("tool") or {}).get("sim", {}) or {}
    exe_path, exe_field = _resolve_vendor_exe(sim_cfg, env, "vsim")
    if not exe_path:
        print(f"[ERROR] '{exe_field}' not found after env setup.", file=sys.stderr)
        if _env.debug_enabled():
            print(f"[DBG] PATH={env.get('PATH','')}", file=sys.stderr)
        return 2
    sim_tcl = SELF_DIR / "questa" / "sim.tcl"
    return run([exe_path, "-c", "-do", f"do {sim_tcl} {cfg_path} {tb_eff}"], env=env)

def sim_xsim(cfg_path: Path, tb: str, env: dict, cfg: dict) -> int:
    tb_eff = tb or _yaml_default_tb(cfg)
    sim_cfg = (cfg.get("tool") or {}).get("sim", {}) or {}
    exe_path, exe_field = _resolve_vendor_exe(sim_cfg, env, "vivado")
    if not exe_path:
        print(f"[ERROR] '{exe_field}' not found after env setup.", file=sys.stderr)
        if _env.debug_enabled():
            print(f"[DBG] PATH={env.get('PATH','')}", file=sys.stderr)
        return 2
    args = ["-mode", "tcl",
            "-source", str(SELF_DIR / "vivado" / "sim.tcl"),
            "-tclargs", str(cfg_path), tb_eff or ""]
    # Vivado on Windows is typically vivado.bat — subprocess can't execute
    # .bat directly (no shell), so wrap via cmd.exe /c. Mirrors vivado_build.
    if on_windows() and exe_path.lower().endswith((".bat", ".cmd")):
        cmd = ["cmd.exe", "/c", exe_path] + args
    else:
        cmd = [exe_path] + args
    return run(cmd, env=env)

# -------------------------
# Main CLI
# -------------------------

# -------------------------
# IP cores validation
# -------------------------

def validate_ip_cores(cfg: dict, synth_kind: str) -> None:
    """
    Check IP cores against tool compatibility.
    Prints warnings to stderr but does not abort.
    """
    ip_cores = cfg.get("ip_cores", [])
    if not ip_cores:
        return
    
    # Vendor-specific IP kinds
    vivado_kinds = {"xci", "bd"}
    diamond_kinds = {"ipx", "lpc"}
    quartus_kinds = {"qip"}
    generic_kinds = {"edf"}  # works everywhere
    
    for core in ip_cores:
        kind = core.get("kind", "")
        src = core.get("src", "")
        
        # Check compatibility
        if kind in vivado_kinds and synth_kind != "vivado":
            print(f"[WARN] IP core {src} uses Vivado-specific format '{kind}' but tool is '{synth_kind}'", file=sys.stderr)
        elif kind in diamond_kinds and synth_kind != "diamond":
            print(f"[WARN] IP core {src} uses Diamond-specific format '{kind}' but tool is '{synth_kind}'", file=sys.stderr)
        elif kind in quartus_kinds and synth_kind != "quartus":
            print(f"[WARN] IP core {src} uses Quartus-specific format '{kind}' but tool is '{synth_kind}'", file=sys.stderr)
        elif kind not in (vivado_kinds | diamond_kinds | quartus_kinds | generic_kinds):
            print(f"[WARN] IP core {src} has unknown kind '{kind}'", file=sys.stderr)

# -------------------------
# Main dispatch
# -------------------------

def _import_main(argv) -> int:
    ap = argparse.ArgumentParser(
        prog="aurig-build import",
        description="Import an existing vendor project into the AURIG layout",
    )
    ap.add_argument("--from", dest="vendor", required=True,
                    choices=["vivado", "quartus", "diamond", "radiant"],
                    help="Source vendor toolchain")
    ap.add_argument("--input", required=True,
                    help="Path to the vendor project folder (scanned for "
                         ".xpr / .qpf+.qsf / .ldf)")
    ap.add_argument("--dest", required=True,
                    help="Destination root for the generated AURIG project")
    ap.add_argument("--name", default="",
                    help="Project name (default: derived from the vendor project)")
    ap.add_argument("--force", action="store_true",
                    help="Overwrite/merge without prompting when --dest is non-empty")
    args = ap.parse_args(argv)

    if args.vendor == "radiant":
        print("[ERROR] Radiant import is not yet implemented (see issue #11).",
              file=sys.stderr)
        return 2

    if args.vendor == "vivado":
        from aurig_build.vivado.import_ import import_project
    elif args.vendor == "quartus":
        from aurig_build.quartus.import_ import import_project
    else:
        from aurig_build.diamond.import_ import import_project

    return import_project(args.input, args.dest, args.name, force=args.force)


def main():
    # `import` is dispatched before the main argparse: `target` is a positional
    # with a fixed `choices` set, so the importer subcommand and its own flags
    # (--from/--input) must be routed here rather than fought into that parser.
    argv = sys.argv[1:]
    if argv and argv[0] == "import":
        return _import_main(argv[1:])

    ap = argparse.ArgumentParser(prog="aurig-build", description="FPGA Project Automation")
    ap.add_argument("--version", action="version", version=f"aurig-build {__version__}")
    ap.add_argument("target", choices=["project", "synth", "impl", "bit", "exporthw", "sim"])
    ap.add_argument("--cfg", default=str(DEFAULT_CFG), help=f"Config file (default: aurig_build/config/project.yaml)")
    ap.add_argument("--tool", help="Override synthesis tool kind (vivado|quartus|...)")
    ap.add_argument("--sim",  help="Override simulation tool kind (vunit|questa|xsim)")
    ap.add_argument("--tb",   help="Testbench top (for sim)")
    ap.add_argument("--noenv", action="store_true", help="Do not auto-load vendor environment")
    ap.add_argument("--", dest="extra", nargs=argparse.REMAINDER, help="Extra args for VUnit")
    args = ap.parse_args()

    cfg_path = Path(args.cfg)
    if not cfg_path.exists():
        print(f"[ERROR] Config not found: {cfg_path}", file=sys.stderr)
        return 2

    def _apply_cli_overrides(c):
        # Inject the CLI --tool / --sim kind overrides as canonical shapes
        # BEFORE normalization, so prepare_env() and the per-vendor
        # dispatchers see the effective kind (not the YAML's original, which
        # would bootstrap the wrong vendor env / probe the wrong exe). Only
        # `kind` is overridden; tool.{synth,sim}.{exe,version,env_script,
        # bin_dir} must come from the YAML. Each level is normalized
        # defensively: YAML allows `tool: ~` / `tool.synth: ~` (None), which
        # setdefault() would not replace before the subsequent mutation.
        if not isinstance(c.get("tool"), dict):
            c["tool"] = {}
        tool_cfg = c["tool"]
        if args.tool:
            if not isinstance(tool_cfg.get("synth"), dict):
                tool_cfg["synth"] = {}
            tool_cfg["synth"]["kind"] = args.tool
        if args.sim:
            if not isinstance(tool_cfg.get("sim"), dict):
                tool_cfg["sim"] = {}
            tool_cfg["sim"]["kind"] = args.sim
        return c

    def _apply_sim_default(c):
        # Framework-aware implicit sim engine default. Runs AFTER
        # normalization (so `framework` is already resolved) and only for the
        # `sim` target. Fires only when no engine is set AND the framework is
        # `direct` (explicit or default) — never under `framework: vunit`,
        # where the engine is intentionally unset, and never overwriting an
        # existing kind. This is what stops a legacy `tool.sim.kind: vunit`
        # (which normalization has already turned into `framework: vunit`)
        # from being misread as `direct` and wrongly assigned an engine.
        if args.target != "sim":
            return c
        tool = c.get("tool") if isinstance(c.get("tool"), dict) else {}
        sim = tool.get("sim") if isinstance(tool.get("sim"), dict) else {}
        synth = tool.get("synth") if isinstance(tool.get("synth"), dict) else {}
        if sim.get("kind") or sim.get("framework") == "vunit":
            return c
        synth_kind = synth.get("kind", "") if isinstance(synth, dict) else ""
        default_sim = {
            "vivado":  "xsim",
            "quartus": "questa",
            "diamond": "questa",
            "radiant": "questa",
        }.get(synth_kind)
        if not default_sim:
            return c
        if not isinstance(c.get("tool"), dict):
            c["tool"] = {}
        if not isinstance(c["tool"].get("sim"), dict):
            c["tool"]["sim"] = {}
        sim_block = c["tool"]["sim"]
        sim_block["kind"] = default_sim
        sim_block.setdefault("framework", "direct")
        # `env_script` and `bin_dir` are inherited from tool.synth when the
        # default fires and the key is missing under tool.sim — otherwise
        # prepare_env(need_sim=True) sources only tool.sim.env_script and the
        # implicit default would crash at vsim launch on the very hosts it's
        # meant to help (Quartus / Diamond / Radiant ship their simulator
        # co-located with the synth tool). A key truly missing OR present-but-
        # null inherits; any other value (dict — even empty — or scalar/list)
        # is an explicit opt-out and blocks inheritance.
        synth_block = c["tool"].get("synth") or {}
        if isinstance(synth_block, dict):
            for inherit_key in ("env_script", "bin_dir"):
                if inherit_key in sim_block and sim_block[inherit_key] is not None:
                    continue
                if inherit_key in synth_block:
                    sim_block[inherit_key] = synth_block[inherit_key]
        print(
            f"[INFO] tool.sim.kind not set; defaulting to '{default_sim}' "
            f"from tool.synth.kind='{synth_kind}'.",
            file=sys.stderr,
        )
        return c

    try:
        cfg, warnings = resolve_manifest(
            cfg_path,
            pre_normalize=_apply_cli_overrides,
            post_normalize=_apply_sim_default,
        )
    except ManifestError as exc:
        for line in exc.messages:
            print(f"[ERROR] {line}", file=sys.stderr)
        return 2
    for warning in warnings:
        print(warning, file=sys.stderr)

    # Materialize the merged YAML AFTER all in-Python cfg mutations so the
    # side-file downstream TCL backends read reflects the same effective
    # configuration Python has — including CLI overrides AND the implicit
    # sim.kind default applied above. Without this, Python could dispatch
    # on the effective kind while TCL parses the un-mutated value from the
    # base/overlay merge.
    cfg_path = materialize_merged_cfg(cfg_path, cfg)

    # Tool kinds (now reflected in cfg AND in the materialized side-file).
    # Same isinstance(..., dict) guard as the pre-materialize read above —
    # YAML can put a non-dict scalar / list at tool.synth or tool.sim and
    # the chained .get(...) would raise AttributeError otherwise.
    _tool_eff  = cfg.get("tool") if isinstance(cfg.get("tool"), dict) else {}
    _synth_eff = _tool_eff.get("synth") if isinstance(_tool_eff.get("synth"), dict) else {}
    _sim_eff   = _tool_eff.get("sim")   if isinstance(_tool_eff.get("sim"),   dict) else {}
    synth_kind    = _synth_eff.get("kind", "") if isinstance(_synth_eff, dict) else ""
    sim_kind      = _sim_eff.get("kind", "")   if isinstance(_sim_eff,   dict) else ""
    sim_framework = _sim_eff.get("framework", "") if isinstance(_sim_eff, dict) else ""

    if args.target != "sim" and not synth_kind:
        print("[ERROR] Synthesis tool kind not set (tool.synth.kind). Use --tool to override.", file=sys.stderr)
        return 2
    # Under VUnit the engine (kind) is intentionally unset; only require a
    # kind for the direct framework.
    if args.target == "sim" and not sim_kind and sim_framework != "vunit":
        print("[ERROR] Simulation tool kind not set (tool.sim.kind). Use --sim to override.", file=sys.stderr)
        return 2

    # Validate IP cores compatibility
    if args.target != "sim" and synth_kind:
        validate_ip_cores(cfg, synth_kind)

    # Prepare env (only for the needed role) unless disabled
    env = os.environ.copy()
    if not args.noenv:
        if args.target == "sim":
            env = prepare_env(cfg, need_synth=False, need_sim=True)
        else:
            env = prepare_env(cfg, need_synth=True, need_sim=False)

    # Build / Implementation
    if args.target in ("project", "synth", "impl", "bit", "exporthw"):
        action = args.target  # <— keep exact; your build.tcl expects 'project', not 'create'
        if synth_kind == "vivado":
            return vivado_build(action, cfg_path, env, cfg)
        elif synth_kind == "quartus":
            return quartus_build(action, cfg_path, env, cfg)
        elif synth_kind == "diamond":
            return diamond_build(action, cfg_path, env, cfg)
        elif synth_kind == "radiant":
            return radiant_build(action, cfg_path, env, cfg)
        else:
            print(f"[ERROR] Unsupported synthesis tool kind: {synth_kind}", file=sys.stderr)
            return 2

    # Simulation
    if args.target == "sim":
        if sim_framework == "vunit":
            return sim_vunit(cfg_path, args.tb or "", args.extra or [], env, cfg)
        if sim_kind == "questa":
            return sim_questa(cfg_path, args.tb or "", env, cfg)
        if sim_kind == "xsim":
            return sim_xsim(cfg_path, args.tb or "", env, cfg)
        print(f"[ERROR] Unsupported simulation tool kind: {sim_kind}", file=sys.stderr)
        return 2

    return 0

if __name__ == "__main__":
    sys.exit(main())
