# SPDX-License-Identifier: Apache-2.0
# Copyright 2024-2026 LogiMentor S.r.l.

"""Smoke tests for Vivado integration.

End-to-end: actually invokes Vivado. Gated by `pytest -m smoke` and skips
cleanly when the `vivado` entry is missing from `tests/smoke/local_config.yaml`
(or when the configured install paths don't exist on disk). See
`tests/smoke/local_config.example.yaml` for the schema.

WARNING: these tests are slow (real `vivado -mode batch` invocation).
"""

import sys
from pathlib import Path

import pytest
import yaml

from aurig_build.run import main
from tests.smoke.conftest import inject_vendor_env


pytestmark = pytest.mark.smoke


@pytest.mark.slow
def test_aurig_build_project_target_with_vivado(tmp_project, vivado_env, monkeypatch):
    """End-to-end: invoke Vivado through `python -m aurig_build.run project`
    against a minimal real tree, and assert the .xpr project file is
    written under `$PROJECT_ROOT/impl/work/vivado/`.

    Exercises the full toolchain: env_script sourcing, exe resolution via
    `bin_dir`, YAML parsing (lite parser fallback if tcllib is broken in
    the host Vivado), `**` glob expansion through `::lm::glob::*`, and
    the `vivado_build` dispatcher in `aurig_build/run.py`.
    """
    yaml_path = tmp_project / "config" / "project.yaml"
    with open(yaml_path, "r") as f:
        cfg = yaml.safe_load(f)
    inject_vendor_env(cfg, vivado_env)
    with open(yaml_path, "w") as f:
        yaml.dump(cfg, f)

    monkeypatch.setattr(sys, "argv", [
        "run.py", "project",
        "--cfg", str(yaml_path),
    ])

    assert main() == 0

    impl_dir = tmp_project / "impl" / "work" / "vivado"
    assert impl_dir.exists(), f"expected {impl_dir} to exist"

    xpr_files = list(impl_dir.rglob("*.xpr"))
    assert len(xpr_files) > 0, "expected at least one .xpr project file"
