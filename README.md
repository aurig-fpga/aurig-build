# aurig-build

FPGA project automation: one YAML drives synthesis and simulation across
Vivado, Quartus, Diamond, and Radiant.

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org)
<!-- TODO: CI badge once .github/workflows/test.yml lands (Commit 10) -->
<!-- TODO: PyPI badge once published (Commit 11) -->

## Features

- **Unified targets**: `project`, `synth`, `impl`, `bit`, `exporthw`, `sim`
  from a single command.
- **Multi-vendor support**: Xilinx Vivado, Intel Quartus, Lattice Diamond,
  Lattice Radiant.
- **Automatic environment setup**: sources vendor scripts, validates
  installed tool versions.
- **YAML-driven configuration**: project metadata, file sets, devices, and
  constraints in one file.
- **Per-machine overlay**: a gitignored `<config>.local.<suffix>` deep-merges
  on top of the committed config for developer-specific paths.
- **Simulation integration**: built-in support for VUnit, Questa, and XSim.

## Installation

From PyPI:

```bash
pip install aurig-build
```

From source (development):

```bash
git clone https://github.com/aurig-fpga/aurig-build.git
cd aurig-build
pip install -e .
```

Verify the install:

```bash
aurig-build --help
```

## Quick Start

Importing from an existing project? Run
`aurig-build import --from <vendor> --input <path> --dest <out-dir>`
(Vivado, Quartus, Diamond) to generate the layout and config
automatically — see [docs/configuration.md](docs/configuration.md).
Otherwise, write `config/project.yaml` by hand.

Drop a `config/project.yaml` in your project root. A minimal Vivado
configuration:

```yaml
project_name: demo_top
project_root: ..
top: demo_top

tool:
  synth:
    kind: vivado
    version: "2023.1"
    env_script:
      linux:   /opt/Xilinx/Vivado/2023.1/settings64.sh
      windows: C:/Xilinx/Vivado/2023.1/settings64.bat

device:
  vendor: xilinx
  family: artix7
  part: xc7a100t-1csg324

board:
  xdc_files:
    - constraints/pins.xdc

file_sets:
  rtl:
    - lib: work
      vhdl_std: 2008
      src:
        - src/**/*.vhd
```

Run synthesis:

```bash
aurig-build --cfg config/project.yaml synth
```

For simulation, add a `tool.sim` block and a testbench file set — see
[docs/configuration.md](docs/configuration.md) for the full schema.

## Supported Tools

| Tool    | Vendor  | Role    | `kind`    | Targets                              |
|---------|---------|---------|-----------|--------------------------------------|
| Vivado  | Xilinx  | `synth` | `vivado`  | project, synth, impl, bit, exporthw  |
| Quartus | Intel   | `synth` | `quartus` | project, synth, impl, bit            |
| Diamond | Lattice | `synth` | `diamond` | project, synth, impl, bit            |
| Radiant | Lattice | `synth` | `radiant` | project, synth, impl, bit            |
| VUnit   | —       | `sim`   | `vunit`   | sim                                  |
| Questa  | Mentor  | `sim`   | `questa`  | sim                                  |
| XSim    | Xilinx  | `sim`   | `xsim`    | sim                                  |

The `synth` role handles the entire build flow (synthesis through
bitstream) for a single tool. You cannot mix tools across phases (e.g.
Synplify Pro synthesis + Diamond P&R). For mixed-tool flows, run
synthesis externally and import the netlist as EDIF — see the
[IP Cores section](docs/configuration.md#ip-cores) for `kind: edf`.

## Documentation

- **[Configuration reference](docs/configuration.md)** — full YAML schema:
  project metadata, tool roles, devices, constraints, file sets, IP cores,
  simulation, vendor-specific settings.

For per-vendor quick references, see the Supported Tools table above.

## Project Structure

aurig-build is installed as a Python package and invoked as a console
script. Your project lives outside the install location:

```
my_fpga_project/
├── config/
│   ├── project.yaml           # tracked, shared with team
│   └── project.local.yaml     # gitignored, per-machine overlay
├── src/                       # synthesizable HDL (referenced by file_sets.rtl)
├── constraints/               # XDC / SDC / QSF / LPF files
├── sim/
│   └── tb/                    # testbenches (referenced by file_sets.sim)
└── impl/                      # build outputs (auto-generated)
    └── work/<vendor>/
```

The layout above is a convention; only `config/project.yaml` is required
for the tool to run.

## Workflow

Typical sequence:

```bash
aurig-build --cfg config/project.yaml project   # create vendor project (optional, for GUI work)
aurig-build --cfg config/project.yaml synth     # synthesis
aurig-build --cfg config/project.yaml impl      # place & route
aurig-build --cfg config/project.yaml bit       # bitstream
aurig-build --cfg config/project.yaml sim --tb tb_demo_top
```

Outputs land in `impl/work/<vendor>/` under the project root resolved
from the config file.

## Debugging

`AURIG_BUILD_DEBUG=1` enables verbose internal output (PATH probing, exe
resolution, env script results):

```bash
# Linux / macOS
AURIG_BUILD_DEBUG=1 aurig-build --cfg config/project.yaml synth

# Windows PowerShell
$env:AURIG_BUILD_DEBUG = "1"; aurig-build --cfg config/project.yaml synth
```

`AURIG_BUILD_PROJECT_ROOT=<path>` overrides the project root derivation
(useful for invocations from outside the standard project layout).

## Requirements

- Python 3.10 or later
- PyYAML (installed automatically)
- One or more vendor toolchains (Vivado, Quartus, Diamond, Radiant) for
  the target backends you intend to use

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Apache License 2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).

---

Maintained by LogiMentor S.r.l.
