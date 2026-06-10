# Contributing to aurig-build

`aurig-build` is part of the [AURIG](https://github.com/aurig-fpga)
open-source FPGA tooling suite by LogiMentor S.r.l.

This project is in early active development (v0.1.0). Public contribution
guidelines are being finalized; in the meantime, please open issues for
bugs and feature requests.

- Maintainer: LogiMentor S.r.l.
- License: Apache 2.0 (see LICENSE)

## Development setup

Install pre-commit hooks (required for contributors):

```bash
pip install pre-commit
pre-commit install
pre-commit install --hook-type commit-msg
```

Pre-commit hooks enforce:

- No AI co-author trailers in commit messages
- No staging-only files (`BRIEF.md`, `MIGRATION.md`, etc.)
- No private keys or secrets in committed content
- YAML/TOML syntax validity
- Standard hygiene (trailing whitespace, end-of-file, line endings)

Run all checks manually:

```bash
pre-commit run --all-files
```
