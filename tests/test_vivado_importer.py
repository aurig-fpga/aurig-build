# SPDX-License-Identifier: Apache-2.0
# Copyright 2024-2026 LogiMentor S.r.l.

"""Unit tests for the Vivado importer CLI front-end (#13).

Verifies the input-folder pre-validation that emits a uniform
'Folder not found' error (matching the quartus/diamond importers)
instead of letting a raw OS error propagate. No Vivado required.
"""

import pytest

from aurig_build.vivado.import_ import main


def test_missing_input_folder_uniform_error(tmp_path, capsys):
    """A non-existent --input folder must exit 2 with 'Folder not found'
    rather than a raw OS error (regression for #13)."""
    missing = tmp_path / "does_not_exist"

    with pytest.raises(SystemExit) as exc:
        main([str(missing), "--dest", str(tmp_path / "out")])

    assert exc.value.code == 2
    err = capsys.readouterr().err.replace("\\", "/").lower()
    expected = f"Folder not found: {missing}".replace("\\", "/").lower()
    assert expected in err
