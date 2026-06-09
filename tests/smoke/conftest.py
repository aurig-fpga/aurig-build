# SPDX-License-Identifier: Apache-2.0
# Copyright 2024-2026 LogiMentor S.r.l.

"""Shared fixtures for aurig-build smoke tests.

Smoke tests actually call vendor tools (Vivado, Quartus, Diamond) end-to-end.
Each developer keeps a per-machine `tests/smoke/local_config.yaml`
(gitignored) that points at the vendor installations on their workstation.
See `tests/smoke/local_config.example.yaml` for the schema.

The `vendor_env(vendor)` fixture below loads that file and returns the
{env_script, bin_dir, version} dict for the requested vendor, or skips
the test cleanly if the file or the vendor entry is missing.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml


SMOKE_DIR = Path(__file__).resolve().parent
LOCAL_CONFIG_PATH = SMOKE_DIR / "local_config.yaml"


def _load_local_config() -> dict:
    if not LOCAL_CONFIG_PATH.is_file():
        return {}
    with open(LOCAL_CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def vendor_env_or_skip(vendor: str) -> dict:
    """Return the local config entry for `vendor`, or pytest.skip.

    Validates the install paths actually exist on disk so a stale entry
    in local_config.yaml gives a clear skip reason instead of a
    runtime tool failure halfway through the run.
    """
    cfg = _load_local_config()
    if vendor not in cfg:
        pytest.skip(
            f"smoke vendor '{vendor}' not configured in "
            f"{LOCAL_CONFIG_PATH.relative_to(SMOKE_DIR.parent.parent)} "
            f"(see local_config.example.yaml)"
        )
    entry = cfg[vendor]
    if not isinstance(entry, dict):
        pytest.skip(f"local_config.yaml '{vendor}' entry is not a mapping")
    bin_dir = entry.get("bin_dir")
    if not bin_dir or not Path(bin_dir).is_dir():
        pytest.skip(
            f"smoke vendor '{vendor}': bin_dir not found on disk: {bin_dir}"
        )
    env_script = entry.get("env_script")
    if env_script is not None and not Path(env_script).is_file():
        pytest.skip(
            f"smoke vendor '{vendor}': env_script not found on disk: {env_script}"
        )
    return entry


@pytest.fixture
def vivado_env():
    return vendor_env_or_skip("vivado")


@pytest.fixture
def quartus_env():
    return vendor_env_or_skip("quartus")


@pytest.fixture
def diamond_env():
    return vendor_env_or_skip("diamond")


def inject_vendor_env(cfg: dict, vendor_cfg: dict) -> None:
    """Mutate `cfg` in-place so its `tool.synth` block points at the
    installation described by `vendor_cfg`. Mirrors the schema in
    `aurig_build/config/project.yaml` (env_script/bin_dir as per-OS mappings).
    """
    synth = cfg.setdefault("tool", {}).setdefault("synth", {})
    if "version" in vendor_cfg:
        synth["version"] = str(vendor_cfg["version"])
    bin_dir = vendor_cfg["bin_dir"].replace("\\", "/")
    synth["bin_dir"] = {"windows": bin_dir, "linux": bin_dir}
    if "env_script" in vendor_cfg:
        env_script = vendor_cfg["env_script"].replace("\\", "/")
        synth["env_script"] = {"windows": env_script, "linux": env_script}
    else:
        # Some toolchains (Diamond on Windows) have no env_script bundled
        # with the installer; bin_dir alone is enough.
        synth.pop("env_script", None)
