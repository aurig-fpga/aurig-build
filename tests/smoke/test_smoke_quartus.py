# SPDX-License-Identifier: Apache-2.0
# Copyright 2024-2026 LogiMentor S.r.l.

"""Smoke tests for Intel Quartus integration.

End-to-end: actually invokes quartus_sh. Gated by `pytest -m smoke` and
skips cleanly when the `quartus` entry is missing from
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
def test_aurig_build_project_target_with_quartus(tmp_project_quartus, quartus_env, monkeypatch):
    """End-to-end: `python -m aurig_build.run project` translates to Quartus
    `create` via the dispatcher's phase_map and produces `${name}.qsf`
    (and `${name}.qpf`) under `$PROJECT_ROOT/impl/work/quartus/$name/`.
    """
    yaml_path = tmp_project_quartus / "config" / "project.yaml"
    with open(yaml_path, "r") as f:
        cfg = yaml.safe_load(f)
    inject_vendor_env(cfg, quartus_env)
    with open(yaml_path, "w") as f:
        yaml.dump(cfg, f)

    monkeypatch.setattr(sys, "argv", [
        "run.py", "project",
        "--cfg", str(yaml_path),
    ])

    assert main() == 0

    build_dir = tmp_project_quartus / "impl" / "work" / "quartus" / "test_proj"
    assert build_dir.exists(), f"expected {build_dir} to exist"

    qsf_files = list(build_dir.rglob("*.qsf"))
    assert len(qsf_files) > 0, "expected at least one .qsf settings file"


@pytest.mark.slow
def test_quartus_bit_autochains_fit(tmp_project_quartus, quartus_env, monkeypatch):
    """Regression for #15 (fit-only auto-chain path): `synth` then `bit`.

    Quartus uses non-project flow, so each target only runs its own stage
    (`impl` itself does not re-run map). After `synth`, map results are present
    but fit results are absent; pre-fix `bit` ran only quartus_asm and failed
    with 'Run Fitter before Assembler'. Here `bit` must auto-chain fit + sta
    (but not map, which already ran) and produce a `.sof`.
    """
    yaml_path = tmp_project_quartus / "config" / "project.yaml"
    with open(yaml_path, "r") as f:
        cfg = yaml.safe_load(f)
    inject_vendor_env(cfg, quartus_env)
    with open(yaml_path, "w") as f:
        yaml.dump(cfg, f)

    build_dir = tmp_project_quartus / "impl" / "work" / "quartus" / "test_proj"

    monkeypatch.setattr(sys, "argv", [
        "run.py", "synth",
        "--cfg", str(yaml_path),
    ])
    assert main() == 0
    assert (build_dir / "test_proj.map.summary").exists(), (
        "synth must produce Map results (precondition: fit-only auto-chain)"
    )
    assert not (build_dir / "test_proj.fit.summary").exists(), (
        "synth alone must not produce Fitter results (precondition for #15)"
    )

    monkeypatch.setattr(sys, "argv", [
        "run.py", "bit",
        "--cfg", str(yaml_path),
    ])
    assert main() == 0

    sof_files = list(build_dir.rglob("*.sof"))
    assert len(sof_files) > 0, "expected bit to auto-chain fit+sta and emit a .sof"


@pytest.mark.slow
def test_quartus_bit_autochains_full(tmp_project_quartus, quartus_env, monkeypatch):
    """#15 (full auto-chain path): `bit` on a clean tree with no prior stage.

    With neither map nor fit results present, `bit` must auto-chain the whole
    pipeline (map -> fit -> sta -> asm) and produce a `.sof`, matching the
    end-to-end behavior of the project-managed backends.
    """
    yaml_path = tmp_project_quartus / "config" / "project.yaml"
    with open(yaml_path, "r") as f:
        cfg = yaml.safe_load(f)
    inject_vendor_env(cfg, quartus_env)
    with open(yaml_path, "w") as f:
        yaml.dump(cfg, f)

    build_dir = tmp_project_quartus / "impl" / "work" / "quartus" / "test_proj"

    monkeypatch.setattr(sys, "argv", [
        "run.py", "bit",
        "--cfg", str(yaml_path),
    ])
    assert main() == 0

    sof_files = list(build_dir.rglob("*.sof"))
    assert len(sof_files) > 0, "expected bit to auto-chain map+fit+sta and emit a .sof"
