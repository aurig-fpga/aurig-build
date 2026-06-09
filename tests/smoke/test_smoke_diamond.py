# SPDX-License-Identifier: Apache-2.0
# Copyright 2024-2026 LogiMentor S.r.l.

"""Smoke tests for Lattice Diamond integration.

End-to-end: actually invokes pnmainc. Gated by `pytest -m smoke` and
skips cleanly when the `diamond` entry is missing from
`tests/smoke/local_config.yaml`.
"""

import sys
from pathlib import Path

import pytest
import yaml

from aurig_build.run import main
from tests.smoke.conftest import inject_vendor_env


pytestmark = pytest.mark.smoke


@pytest.mark.slow
def test_aurig_build_project_target_with_diamond(tmp_project_diamond, diamond_env, monkeypatch):
    """End-to-end: `python -m aurig_build.run project` translates to Diamond
    `create` via the dispatcher's phase_map and produces `${name}.ldf`
    under `$PROJECT_ROOT/impl/work/diamond/$name/`.
    """
    yaml_path = tmp_project_diamond / "config" / "project.yaml"
    with open(yaml_path, "r") as f:
        cfg = yaml.safe_load(f)
    inject_vendor_env(cfg, diamond_env)
    with open(yaml_path, "w") as f:
        yaml.dump(cfg, f)

    monkeypatch.setattr(sys, "argv", [
        "run.py", "project",
        "--cfg", str(yaml_path),
    ])

    assert main() == 0

    build_dir = tmp_project_diamond / "impl" / "work" / "diamond" / "test_proj"
    assert build_dir.exists(), f"expected {build_dir} to exist"

    ldf_files = list(build_dir.rglob("*.ldf"))
    assert len(ldf_files) > 0, "expected at least one .ldf project file"
