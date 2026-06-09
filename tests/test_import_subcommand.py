# SPDX-License-Identifier: Apache-2.0
# Copyright 2024-2026 LogiMentor S.r.l.

"""The vendor importers must emit canonical manifests that pass schema v1.

Historically the importers wrote ``schema_version: "1.0"`` (Vivado, Quartus)
or omitted the field entirely (Diamond), so every imported project failed the
schema-v1 loader. These tests pin the producer output to the canonical ``"1"``
and exercise the ``aurig-build import`` subcommand dispatch.
"""

from __future__ import annotations

import yaml

from aurig_build.run import _import_main
from aurig_build.schema import validate
from aurig_build.schema.normalize import normalize


def _load_and_validate(yaml_path):
    with open(yaml_path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    canonical, _ = normalize(cfg)
    validate(canonical)  # raises ManifestError on any violation
    return canonical


def test_vivado_writer_emits_canonical_manifest(tmp_path):
    from aurig_build.vivado.import_ import write_yaml_lm

    ypath = write_yaml_lm(
        dest_root=str(tmp_path),
        name="demo",
        top="top_entity",
        part="xc7a100t-1csg324",
        libs_used=["work"],
        xdc_relpaths=[],
    )
    cfg = _load_and_validate(ypath)
    assert cfg["schema_version"] == "1"
    assert cfg["tool"]["synth"]["kind"] == "vivado"


def test_diamond_writer_emits_canonical_manifest(tmp_path):
    from aurig_build.diamond.import_ import write_yaml

    ypath = write_yaml(
        dest_root=str(tmp_path),
        name="demo",
        top="top_entity",
        part="LFE5U-25F-6BG256C",
        pdc_relpaths=[],
        lpf_relpaths=[],
        libs_used=["work"],
    )
    cfg = _load_and_validate(ypath)
    assert cfg["schema_version"] == "1"
    assert cfg["device"]["vendor"] == "lattice"


def _make_quartus_project(root):
    src = root / "src"
    src.mkdir(parents=True)
    (src / "top.vhd").write_text(
        "entity top_entity is end entity;\n", encoding="utf-8"
    )
    (root / "demo.qpf").write_text(
        'PROJECT_REVISION = "demo"\n', encoding="utf-8"
    )
    (root / "demo.qsf").write_text(
        "set_global_assignment -name TOP_LEVEL_ENTITY top_entity\n"
        "set_global_assignment -name DEVICE 10CL025YU256C8G\n"
        "set_global_assignment -name VHDL_INPUT_VERSION VHDL_2008\n"
        'set_global_assignment -name VHDL_FILE "src/top.vhd" -library work\n',
        encoding="utf-8",
    )
    return root


def test_quartus_import_roundtrip(tmp_path):
    from aurig_build.quartus.import_ import import_project

    proj = _make_quartus_project(tmp_path / "legacy")
    dest = tmp_path / "imported"

    rc = import_project(str(proj), str(dest), name="demo")
    assert rc == 0

    cfg = _load_and_validate(dest / "config" / "project.yaml")
    assert cfg["schema_version"] == "1"
    assert cfg["project_name"] == "demo"
    assert cfg["tool"]["synth"]["kind"] == "quartus"


def test_import_subcommand_roundtrip(tmp_path):
    proj = _make_quartus_project(tmp_path / "legacy")
    dest = tmp_path / "imported"

    rc = _import_main(
        ["--from", "quartus", "--input", str(proj), "--dest", str(dest), "--name", "demo"]
    )
    assert rc == 0
    _load_and_validate(dest / "config" / "project.yaml")


def test_import_radiant_not_implemented(tmp_path, capsys):
    rc = _import_main(
        ["--from", "radiant", "--input", str(tmp_path), "--dest", str(tmp_path / "out")]
    )
    assert rc == 2
    assert "not yet implemented" in capsys.readouterr().err
