# SPDX-License-Identifier: Apache-2.0
# Copyright 2024-2026 LogiMentor S.r.l.

from pathlib import Path
from subprocess import CalledProcessError
from unittest.mock import Mock

import pytest

from aurig_build import run


@pytest.mark.parametrize("debug", [False, True], ids=["normal", "debug"])
@pytest.mark.parametrize("returncode", [0, 7], ids=["success", "failure"])
def test_windows_env_bootstrap_keeps_values_out_of_logs(
    monkeypatch, tmp_path, capsys, debug, returncode
):
    script = tmp_path / "vendor settings.bat"
    script.touch()
    supplied = {
        "AURIG_BUILD_DEBUG": "1" if debug else "0",
        "INPUT_SECRET": "synthetic-input-marker",
        "SHARED": "synthetic-original-value",
    }
    returned = {
        "OUTPUT_SECRET": "synthetic-output-marker=with-equals",
        "SHARED": "synthetic-replacement-value",
        "Path": r"C:\synthetic-vendor\bin;C:\synthetic-system\bin",
    }
    output = "Vendor banner\n" + "\n".join(f"{key}={value}" for key, value in returned.items())
    cfg = {"tool": {"sim": {"kind": "vunit", "env_script": {"windows": str(script)}}}}

    fake_os = Mock(wraps=run.os, environ=supplied)
    monkeypatch.setattr(run, "os", fake_os)
    monkeypatch.setattr(run._env, "os", fake_os)
    monkeypatch.setattr(run, "on_windows", lambda: True)
    monkeypatch.setattr(run, "_which", lambda exe, env: None)
    monkeypatch.setattr(run.tempfile, "tempdir", str(tmp_path))

    def check_output(command, *, text, env):
        assert command[:3] == ["cmd.exe", "/v:off", "/c"]
        assert len(command) == 4
        assert text is True
        assert env == supplied
        batch = Path(command[3])
        assert batch.suffix == ".bat"
        assert batch != script
        assert batch.read_text(encoding="utf-8").splitlines() == [
            "@echo off",
            f'call "{script}"',
            "set",
        ]
        if returncode:
            raise CalledProcessError(returncode, command, output=output)
        return output

    mocked_subprocess = Mock(side_effect=check_output)
    monkeypatch.setattr(run.subprocess, "check_output", mocked_subprocess)

    merged = run.prepare_env(cfg, need_synth=False, need_sim=True)

    mocked_subprocess.assert_called_once()
    batch = Path(mocked_subprocess.call_args.args[0][3])
    assert not batch.exists()
    assert script.is_file()

    captured = capsys.readouterr()
    assert ("[DBG] load_env:" in captured.out) is debug
    if returncode:
        assert merged == supplied
        assert "[WARN] Env bootstrap failed via temp .bat:" in captured.err
        assert f"exit status {returncode}" in captured.err
    else:
        assert merged == {**supplied, **returned, "PATH": returned["Path"]}
        assert "[WARN]" not in captured.err

    synthetic_values = [supplied["INPUT_SECRET"], supplied["SHARED"], *returned.values()]
    for stream, content in zip(("stdout", "stderr"), captured):
        for value in synthetic_values:
            assert value not in content, f"synthetic value {value!r} appeared in {stream}"
