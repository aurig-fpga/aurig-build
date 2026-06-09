# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-06-08

Initial public release of aurig-build, the FPGA project automation tool
from the AURIG stack. One YAML manifest drives synthesis, implementation,
bitstream generation, and simulation across Vivado, Quartus, Diamond, and
Radiant.

### Added

Core capabilities:
- Multi-vendor build engine: Vivado, Quartus, Diamond, Radiant backends
- Schema v1 canonical pipeline (AURIG Project Manifest v1, `manifest-v1.json`)
  with hand-rolled validator (PyYAML-only runtime dependency)
- `aurig-build` CLI with targets `project`, `synth`, `impl`, `bit`,
  `exporthw`, `sim`
- `aurig-build import` subcommand for the Vivado, Quartus, and Diamond
  importers

CLI and UX:
- `--version` flag (#10)
- Vivado importer: uniform "folder not found" error message (#13)
- Diamond importer: persist auto-detected `bin_dir` and `version` (#19)
- Diamond importer: emit `ip_cores` and `board.sdc_files` sections (#17, #29)

Cross-vendor consistency:
- All four backends now consume the `board:` section uniformly within their
  vendor-applicable extensions (#18 Diamond, #32 Radiant; Vivado and Quartus
  already supported)
- Multi-vendor smoke fixtures (`quartus_min`, `diamond_min`, `radiant_min`)
  with parametrized schema validation in CI (#4)
- Quartus: `bit` target auto-chains map+fit+sta when prerequisite results are
  missing (#15)

Reliability:
- Diamond + Radiant: propagate `prj_run` failures to exit code (#21) —
  previously synthesis failures were silently logged with exit 0
- Diamond + Radiant: fixed Tcl `elseif` syntax crash on constraint files (#20)

Documentation and infrastructure:
- `CHANGELOG.md` following Keep-a-Changelog
- `LICENSE` (Apache 2.0)
- `NOTICE` (LogiMentor S.r.l. attribution)
- Test suite with 409 passing tests covering schema, importers, backends,
  and CLI behaviors

### Known limitations

- **Diamond support is experimental**: synthesis engine selection
  (Synplify Pro vs LSE) is not yet configurable via YAML; LSE is used by
  default which may differ from manual Diamond GUI projects (see issue #22)
- **Radiant importer is Tcl-only**: planned port to Python (#11)
- **Glob walker has known symlink edge cases** on circular symlinks and
  directory aliases (issue #1)
- **Multi-variant manifests not yet supported** (#14)
- **Architectural refactor planned for v0.2.0**: backend reads canonical
  Tcl data instead of YAML side-files (#8, #9)

### Acknowledgments

Developed by Andrea Campera, lead developer, on behalf of LogiMentor S.r.l.

Part of the AURIG open-source FPGA tooling stack:
https://github.com/aurig-fpga

---

[Unreleased]: https://github.com/aurig-fpga/aurig-build/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/aurig-fpga/aurig-build/releases/tag/v0.1.0
