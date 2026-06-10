# SPDX-License-Identifier: Apache-2.0
# Copyright 2024-2026 LogiMentor S.r.l.

"""
Quick validation script to check test suite structure.

Run this without pytest installed to verify all test files are present
and have proper structure.
"""

from pathlib import Path


def check_file_exists(path: Path, description: str) -> bool:
    """Check if a file exists and print status."""
    if path.exists():
        print(f"✓ {description}: {path}")
        return True
    else:
        print(f"✗ MISSING {description}: {path}")
        return False


def main():
    """Validate test suite structure."""
    # Root is the parent of the tests/ directory
    root = Path(__file__).parent.parent
    all_good = True

    print("aurig-build Test Suite Structure Validation")
    print("=" * 70)

    # Core test files
    test_files = [
        ("tests/conftest.py", "Shared fixtures"),
        ("tests/test_yaml_parsing.py", "YAML parsing tests"),
        ("tests/test_cli.py", "CLI tests"),
        ("tests/test_ip_warnings.py", "IP warnings tests"),
        ("tests/test_path_resolution.py", "Path resolution tests"),
        ("tests/test_integration.py", "Integration tests"),
        ("tests/smoke/test_smoke_vivado.py", "Smoke tests"),
    ]

    print("\nTest Files:")
    for file, desc in test_files:
        all_good &= check_file_exists(root / file, desc)

    # Configuration files
    print("\nConfiguration:")
    all_good &= check_file_exists(root / "pytest.ini", "Pytest config")
    all_good &= check_file_exists(root / "requirements-test.txt", "Test requirements")
    all_good &= check_file_exists(root / "tests/README.md", "Test documentation")

    # Check test function count by reading files
    print("\nTest Function Counts:")
    for file, desc in test_files:
        path = root / file
        if path.exists():
            content = path.read_text()
            count = content.count("def test_")
            print(f"  {path.name}: {count} test functions")

    print("\n" + "=" * 70)
    if all_good:
        print("✓ All test files present!")
        print("\nNext steps:")
        print("  1. pip install -r requirements-test.txt")
        print("  2. pytest -v")
        print("  3. pytest --smoke  (if Vivado is installed)")
    else:
        print("✗ Some files are missing!")
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
