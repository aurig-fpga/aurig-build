# Configuration Reference

aurig-build is configured via a YAML file (typically
`config/project.yaml` in your project root). This document describes
every field, organized by section. For the high-level workflow and the
supported tool matrix, see the [README](../README.md).

## Importing existing projects

If you already have a vendor project, you can generate the AURIG layout
and a canonical `config/project.yaml` from it instead of writing the
config by hand:

```
aurig-build import --from <vendor> --input <project-folder> --dest <out-dir> [--name <name>]
```

`--from` accepts `vivado` (`.xpr`), `quartus` (`.qpf`/`.qsf`) and
`diamond` (`.ldf`). The importer parses the vendor project, stages
sources and constraints under `<out-dir>`, and writes a canonical
manifest. Fields the importer cannot determine with confidence (for
example the device family of an unrecognized part) are filled with safe
placeholders such as `unknown`, so review the generated YAML before
building. The Vivado and Quartus importers additionally record a
`discovery` block reporting per-field confidence. Radiant import is not yet
available (tracked in issue #11); use the manual configuration below in
the meantime.

## Configuration model

The config has two cross-cutting concepts:

- **Tool roles**: `tool.synth` (full build flow: synthesis through
  bitstream) and `tool.sim` (simulation). Each is optional but at least
  one must be configured depending on the target you intend to run.
- **Per-machine overlay**: a sibling file `<stem>.local.<suffix>`
  (e.g. `project.local.yaml`) is deep-merged on top of the committed
  config at load time. Use it for developer-specific install paths.

## Minimal example

```yaml
project_name: demo_top
project_root: ../..
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
  sim:
    - lib: tb
      vhdl_std: 2008
      src:
        - sim/tb/**/*.vhd
```

## Configuration sections

### Project metadata

```yaml
project_name: demo_top        # Project name (used in output directories)
project_root: ../..           # Path to project root (relative to this file)
top: demo_top                 # Top-level entity/module name
require_exact_versions: true  # true: fail on version mismatch; false: warn
```

* `project_root` resolves relative to the YAML file. For a config at
  `<project>/config/project.yaml`, use `..` to reach the project root.
* `require_exact_versions: true` enforces strict version matching;
  `false` only warns. When the key is absent the code defaults to
  `true` (strict).

### Per-machine overlay (`<stem>.local.<suffix>`)

For values that should NOT be committed (developer-specific Vivado
install path, alternative version pin, local env_script, etc.), drop a
sibling file named `<stem>.local.<suffix>` next to the committed config
— where `<suffix>` matches the base config (so `project.yaml` looks for
`project.local.yaml`, and `project.yml` looks for `project.local.yml`).
aurig-build loads it automatically and deep-merges it on top:

```
config/project.yaml           # committed, shared across the team
config/project.local.yaml     # gitignored, per-machine
```

Example `project.local.yaml` overriding only the Vivado install
location:

```yaml
tool:
  synth:
    version: "2024.1"
    env_script:
      linux: /opt/Xilinx/Vivado/2024.1/settings64.sh
```

Merge semantics:
- Dicts merge recursively (overlay's keys win, sibling keys survive).
- Lists / scalars / `null` replace the base value wholesale.
- An empty or missing overlay file is silently ignored.
- The applied overlay is announced on stderr
  (`[INFO] Applying local overlay: ...`).

Add the overlay and the materialized side-file to your project
`.gitignore`. For `.yaml` configs that's `*.local.yaml` and
`.*.merged.*.yaml`; for `.yml` configs use `*.local.yml` and
`.*.merged.*.yml`. The side-file is auto-cleaned at process exit; the
gitignore is a safety net in case a crash skips the cleanup.

### Tool configuration

The `tool` section has two roles:

- **`synth`**: handles the complete build flow (synthesis,
  implementation, bitstream, hardware export). Targets: `project`,
  `synth`, `impl`, `bit`, `exporthw`.
- **`sim`**: handles simulation. Target: `sim`.

Each role is optional but at least one must be configured depending on
your target.

> **Limitation**: You cannot configure separate tools for synthesis
> and implementation (e.g., Synplify Pro for synthesis with Diamond
> for P&R). The same tool must handle the entire build flow. For
> mixed-tool flows, run synthesis externally and import the netlist
> as EDIF (see [IP Cores](#ip-cores), kind `edf`).

Common fields under any tool entry:

| Field        | Required | Type   | Description                                           |
|--------------|----------|--------|-------------------------------------------------------|
| `kind`       | Yes      | string | Tool identifier (see tables below)                    |
| `version`    | No       | string | Expected version (used for validation)                |
| `exe`        | No       | string | Executable name or absolute path (defaults to `kind`) |
| `env_script` | No       | object | OS-specific environment setup scripts                 |
| `bin_dir`    | No       | object | OS-specific binary directory paths                    |

OS keys under `env_script` and `bin_dir`: `linux` and `windows`.

#### Synth role: vendors

##### Xilinx Vivado

Supports targets: `project`, `synth`, `impl`, `bit`, `exporthw`.

```yaml
tool:
  synth:
    kind: vivado
    version: "2023.1"
    exe: vivado
    env_script:
      linux:   /opt/Xilinx/Vivado/2023.1/settings64.sh
      windows: C:/Xilinx/Vivado/2023.1/settings64.bat
    bin_dir:
      linux:   /opt/Xilinx/Vivado/2023.1/bin
      windows: C:/Xilinx/Vivado/2023.1/bin
```

Notes:
- Version format: `YYYY.N` (e.g., `2023.1`, `2021.2`).
- `env_script` sources the Vivado settings script.
- `exporthw` exports `.xsa` files for embedded projects.

##### Intel Quartus

Supports targets: `project`, `synth`, `impl`, `bit`.

```yaml
tool:
  synth:
    kind: quartus
    version: "23.1"
    exe: quartus_sh
    env_script:
      linux:   /opt/intelFPGA/23.1/quartus/adm/qenv.sh
      windows: C:/intelFPGA_lite/23.1/quartus/adm/qenv.bat
    bin_dir:
      linux:   /opt/intelFPGA/23.1/quartus/bin
      windows: C:/intelFPGA_lite/23.1/quartus/bin64
```

Notes:
- Version format: `YY.N` or `YY.N.N` (e.g., `23.1`, `22.1.0`).
- Uses non-project flow via TCL scripts.
- On Quartus, the `bit` target auto-runs `map` + `fit` + `sta` if prerequisite results are missing, producing a bitstream end-to-end like the other backends.
- `exporthw` is not applicable to Quartus.

##### Lattice Diamond

Supports targets: `project`, `synth`, `impl`, `bit`.

```yaml
tool:
  synth:
    kind: diamond
    version: "3.13"
    exe: diamondc        # or 'pnmainc' for command-line
    env_script:
      linux:   /opt/lscc/diamond/3.13/bin/lin64/diamond_env.sh
      windows: C:/lscc/diamond/3.13/bin/nt64/diamond_env.bat
    bin_dir:
      linux:   /opt/lscc/diamond/3.13/bin/lin64
      windows: C:/lscc/diamond/3.13/bin/nt64
```

Notes:
- Version format: `X.YZ` (e.g., `3.13`, `3.12`).
- Version detection attempts to read from PATH or common env vars
  (`LSC_DIAMOND`, `DIAMOND_ROOT`).
- The `project` target maps internally to Diamond's "create" phase.

##### Lattice Radiant

Supports targets: `project`, `synth`, `impl`, `bit`.

```yaml
tool:
  synth:
    kind: radiant
    version: "2024.1"
    exe: radiantc
    env_script:
      linux:   /usr/local/radiant_2024.1/bin/lin64/radiant_env.sh
      windows: C:/lscc/radiant/2024.1/bin/nt64/radiant_env.bat
    bin_dir:
      linux:   /usr/local/radiant_2024.1/bin/lin64
      windows: C:/lscc/radiant/2024.1/bin/nt64
```

Notes:
- Version format: `YYYY.N` (e.g., `2024.1`).
- Version detection parses `radiantc -v` output or the install path.
- The `project` target maps internally to Radiant's "create" phase.
- `exporthw` is not separate from `bit` on Radiant — the bitstream is
  the final artifact.

#### Sim role: tools

##### VUnit

Python-based test runner framework.

```yaml
tool:
  sim:
    kind: vunit
    version: ""                       # version not checked
    exe: python                       # interpreter for the VUnit driver
                                      # (default: sys.executable, i.e. the
                                      #  same Python that runs aurig-build)
    driver: sim/run_vunit.py          # path to the VUnit driver script
                                      # (default: sim/run_vunit.py,
                                      #  relative to invocation CWD)
```

Notes:
- Requires a Python driver script in your project. By default
  aurig-build looks for `sim/run_vunit.py`; override the path with
  `tool.sim.driver` if your layout puts it elsewhere (e.g.
  `tools/sim/vunit_driver.py` for projects with a license boundary on
  `sim/`).
- Uses `--tb` flag or `sim.default_top_tb` in YAML to specify the
  testbench (`sim.top_tb` accepted as legacy fallback).
- Extra args passed via `--` (e.g., `aurig-build sim -- --verbose`).

**Driver contract** — the consumer-side Python script
(`tool.sim.driver`, default `sim/run_vunit.py`) is what actually drives
VUnit. aurig-build invokes it as:

```
<interpreter> <driver> --cfg <abs-path-to-project.yaml> [--tb <name>] [extra args...]
```

where `<interpreter>` is `tool.sim.exe` (defaults to `sys.executable`
— the same Python that ran `aurig-build`). The driver receives the
**already-merged** YAML cfg as `--cfg`: when a per-machine
`<stem>.local.yaml` overlay exists, aurig-build writes a temporary
merged side-file and passes that path, so the driver never has to know
about overlays.

The driver is expected to:

1. Parse its argv. At minimum recognize `--cfg <path>`; ideally
   `--tb <name>` too (passed when the user runs
   `aurig-build sim --tb <name>` or when YAML has
   `sim.default_top_tb`).
2. Load the YAML and call `expand_file_sets`-style logic to assemble
   HDL sources. The simplest path is to `import yaml` and walk
   `file_sets.rtl` + `file_sets.sim` directly; glob expansion follows
   Python `pathlib.Path.glob` semantics, matching the TCL backends.
3. Create the VUnit project, add the libraries and source files, and
   add the testbench identified by `--tb` (falling back to
   `sim.default_top_tb` if the flag is absent).
4. Call `vu.main()` to run; let VUnit's own exit code propagate.
   aurig-build does not interpret stdout — it just forwards the
   driver's return code as the exit code of `aurig-build sim`.

The environment passed to the driver already has `env_script` /
`bin_dir` applied (so license servers, `LM_LICENSE_FILE`, etc. are
configured), and any vendor that VUnit will invoke under the hood
(`ghdl`, `nvc`, `vsim` simulator) needs to be reachable through the
resulting `PATH`.

Minimal driver shape (for reference; consumers usually need more):

```python
# sim/run_vunit.py
import argparse
import yaml
from pathlib import Path
from vunit import VUnit

ap = argparse.ArgumentParser()
ap.add_argument("--cfg", required=True, type=Path)
ap.add_argument("--tb", default=None)
args, vunit_argv = ap.parse_known_args()

cfg = yaml.safe_load(args.cfg.read_text())
project_root = (args.cfg.parent / cfg.get("project_root", "..")).resolve()

vu = VUnit.from_argv(argv=vunit_argv)
for entry in cfg.get("file_sets", {}).get("rtl", []) + cfg.get("file_sets", {}).get("sim", []):
    lib = vu.add_library(entry["lib"]) if entry["lib"] not in vu.libraries else vu.library(entry["lib"])
    for pat in entry.get("src", []):
        for f in sorted(project_root.glob(pat)):
            lib.add_source_file(str(f), vhdl_standard=str(entry.get("vhdl_std", "2008")))

tb_name = args.tb or (cfg.get("sim") or {}).get("default_top_tb")
if tb_name:
    lib_part, _, tb_part = tb_name.partition(".")
    vu.library(lib_part).test_bench(tb_part)

vu.main()
```

(aurig-build does not ship a template driver — each consumer's VUnit
setup tends to grow project-specific hooks for coverage, pre/post
simulation TCL, custom plusargs.)

##### Questa / ModelSim

Mentor / Siemens simulator.

```yaml
tool:
  sim:
    kind: questa
    version: "2023.2"
    exe: vsim
    env_script:
      linux:   /opt/mentor/questa/2023.2/settings.sh
      windows: C:/mentor/questa/2023.2/settings.bat
    bin_dir:
      linux:   /opt/mentor/questa/2023.2/bin
      windows: C:/questasim64_2023.2/win64
```

Notes:
- Version format: `YYYY.N` (e.g., `2023.2`, `2021.1`).
- Requires testbench top specified via `--tb` or
  `sim.default_top_tb` in YAML.
- Automatically runs TCL script at `aurig_build/questa/sim.tcl`.

##### Xilinx XSim

Vivado-integrated simulator.

```yaml
tool:
  sim:
    kind: xsim
    version: "2023.1"
    exe: vivado
    env_script:
      linux:   /opt/Xilinx/Vivado/2023.1/settings64.sh
      windows: C:/Xilinx/Vivado/2023.1/settings64.bat
```

Notes:
- Shares version and environment with Vivado.
- Runs via `vivado -mode tcl` with simulation script.
- Automatically runs TCL script at `aurig_build/vivado/sim.tcl`.

#### Multi-tool configuration

You can configure both synthesis and simulation tools:

```yaml
tool:
  synth:
    kind: vivado
    version: "2023.1"
    env_script:
      linux:   /opt/Xilinx/Vivado/2023.1/settings64.sh
      windows: C:/Xilinx/Vivado/2023.1/settings64.bat

  sim:
    kind: questa
    version: "2023.2"
    env_script:
      linux:   /opt/mentor/questa/2023.2/settings.sh
      windows: C:/mentor/questa/2023.2/settings.bat
```

Environment setup is role-specific — only the required tool
environment is loaded for each target:

```bash
aurig-build --cfg config/project.yaml synth          # uses tool.synth (Vivado)
aurig-build --cfg config/project.yaml impl           # uses tool.synth (Vivado)
aurig-build --cfg config/project.yaml bit            # uses tool.synth (Vivado)
aurig-build --cfg config/project.yaml sim --tb tb_top   # uses tool.sim (Questa)
```

#### Version checking

```yaml
require_exact_versions: true             # default: true
```

- **`true`**: fail immediately if the installed tool version doesn't
  match the `version` field.
- **`false`**: only warn on version mismatch, continue execution.

Version detection methods:

| Tool        | Method                                            |
|-------------|---------------------------------------------------|
| Vivado/XSim | Runs `vivado -mode tcl` and queries version       |
| Quartus     | Runs `quartus_sh --version`                       |
| Questa      | Runs `vsim -version`                              |
| Diamond     | Parses from executable path or env vars           |
| Radiant     | Parses from `radiantc -v` output or install path  |

### Device configuration

Specify the target FPGA device. Format varies by vendor.

#### Xilinx devices

```yaml
device:
  vendor: xilinx
  family: artix7                         # zynq, kintex7, virtex7, artix7, zynquplus, etc.
  part: xc7a100t-1csg324                 # full part number with speed grade
```

Common Xilinx families:
- `artix7`, `kintex7`, `virtex7` — 7 Series
- `zynq` — Zynq-7000 SoC
- `zynquplusRFSOC`, `zynquplus` — Zynq UltraScale+
- `kintexuplus`, `virtexuplus` — UltraScale+

Part format: `device-speed_grade-package`. Example:
`xc7a100t-1csg324` = Artix-7 100T, speed grade 1, CSG324 package.

#### Intel devices

```yaml
device:
  vendor: intel
  family: max10                          # cyclonev, max10, arriav, etc.
  part: 10M50DAF484C7G                   # full part number
```

Common Intel families:
- `max10` — MAX 10
- `cyclonev` — Cyclone V
- `cyclone10lp` — Cyclone 10 LP
- `arriav`, `arriaVgz` — Arria V

Part format varies by family:
- MAX 10: `10M50DAF484C7G`
- Cyclone V: `5CSEMA5F31C6`

#### Lattice devices

```yaml
device:
  vendor: lattice
  family: ecp5                           # ice40, ecp5, machxo2, machxo3, etc.
  part: LFE5U-85F-6BG381C                # full part number
```

Common Lattice families:
- `ice40` — iCE40 (UP, HX, LP series)
- `ecp5` — ECP5
- `machxo2`, `machxo3` — MachXO2/3

Part format varies by family:
- ECP5: `LFE5U-85F-6BG381C`
- iCE40: `iCE40HX8K-CT256`

### Constraints

Constraints define pin assignments, timing, and other physical design
rules. Use vendor-specific formats.

#### Xilinx constraints (XDC)

```yaml
board:
  xdc_files:
    - constraints/pins.xdc               # pin assignments
    - constraints/clocks.xdc             # clock definitions
    - constraints/timing.xdc             # timing constraints
```

Notes:
- Used by Vivado.
- Files processed in order — **order matters**.
- Base clocks should be defined before derived clocks.
- Paths relative to project root.

Example XDC content:

```tcl
# Clock constraint
create_clock -period 10.000 -name sys_clk [get_ports clk_in]

# Pin assignment
set_property PACKAGE_PIN E3 [get_ports clk_in]
set_property IOSTANDARD LVCMOS33 [get_ports clk_in]
```

#### Intel constraints (SDC/QSF)

```yaml
board:
  sdc_files:
    - constraints/timing.sdc             # timing constraints (SDC format)
```

Notes:
- Used by Quartus.
- Pin assignments typically in QSF extras (see Quartus-Specific
  Settings).
- SDC files for timing constraints only.

Example SDC content:

```tcl
create_clock -period 10.000 [get_ports clk_in]
derive_pll_clocks
derive_clock_uncertainty
```

Pin assignments via `quartus.qsf_extra` or `quartus.qsf_extra_files`:

```yaml
quartus:
  qsf_extra:
    - "set_location_assignment PIN_E3 -to clk_in"
    - "set_instance_assignment -name IO_STANDARD \"3.3-V LVCMOS\" -to clk_in"
```

#### Lattice constraints

For Diamond and Radiant, timing constraints in `.sdc` format:

```yaml
board:
  sdc_files:
    - constraints/timing.sdc
```

Diamond also accepts `.lpf` (Lattice Preference File) for pin
assignments, registered via vendor-specific mechanisms.

### File sets

File sets organize source files into logical groups (libraries) with
specific compilation settings.

The `file_sets` mapping has two sections — `rtl:` (synthesizable
sources, consumed by the synthesis flow) and `sim:` (sim-only sources
such as testbenches and simulation models, consumed by the simulation
flow). Each section is a list of library entries.

| Section | Consumed by                                              | Typical contents                                   |
|---------|----------------------------------------------------------|----------------------------------------------------|
| `rtl:`  | `vivado/build.tcl`, `quartus/build.tcl`, `diamond/build.tcl`, `radiant/build.tcl` (and every `sim.tcl`) | RTL the synth flow must see |
| `sim:`  | `vivado/sim.tcl`, `questa/sim.tcl`, VUnit driver only    | testbenches, behavioral models, VUnit helpers      |

Sim sources do **not** reach the synth flow — they would otherwise
pollute the Vivado `sources_1` fileset (or Quartus QSF, Diamond LDF,
Radiant RDF) and break synthesis on testbenches that use
non-synthesizable constructs (`wait`, infinite loops, `VUnit` calls).
Keep them under `sim:`.

#### Basic example

```yaml
file_sets:
  rtl:
    - lib: work                          # VHDL library name
      vhdl_std: 2008                     # VHDL standard: 1993 | 2002 | 2008
      src:
        - src/**/*.vhd                   # glob patterns for source files
        - src/**/*.v                     # can mix VHDL and Verilog
      include:                           # optional: include directories
        - src/include
```

Fields (same for `rtl:` and `sim:` entries):
- `lib` — **Required**. VHDL library name
  (e.g., `work`, `util`, `ieee_proposed`).
- `vhdl_std` — Optional. VHDL standard (`1993`, `2002`, `2008`).
  Default depends on tool.
- `src` — **Required**. List of file glob patterns.
- `include` — Optional. List of include directories for Verilog /
  SystemVerilog.

#### Multi-library example

```yaml
file_sets:
  rtl:
    # Common utilities library
    - lib: util
      vhdl_std: 2008
      src:
        - lib/util/**/*.vhd
      include:
        - lib/util/include

    # Math library
    - lib: math
      vhdl_std: 2008
      src:
        - lib/math/**/*.vhd

    # Main design (work library)
    - lib: work
      vhdl_std: 2008
      src:
        - src/common/**/*.vhd
        - src/rtl/**/*.vhd
        - src/top.vhd
      include:
        - src/include
```

Notes:
- Files compiled in order of `file_sets` definition (per-section).
- Libraries compiled before they're used.
- Glob patterns are evaluated from `project_root`. A standalone `**`
  path component matches **zero or more** intermediate directory
  components (Python `pathlib.Path.glob` / gitignore / bash
  `globstar` convention); `*` and `?` do not cross `/`. The same
  semantics apply across every synth backend (Vivado, Quartus,
  Diamond, Radiant), every sim backend (Vivado xsim, Questa), and the
  Python VUnit driver — so a pattern like `src/common/**/*.vhd`
  matches `src/common/hello.vhd` (zero subdirs) as well as
  `src/common/util/foo.vhd` (one subdir) the same way in all of them.
  **Note**: only the first standalone `**` per pattern is treated as
  recursive; any subsequent `**` collapses to a single `*` within its
  component. Prefer one recursive `**` per pattern for portable
  results.
- The `sim:` section is optional. Omit it if you have no testbenches
  in YAML and rely on a sim driver script to pick them up by
  convention.

#### Mixed-language example

```yaml
file_sets:
  rtl:
    - lib: work
      vhdl_std: 2008
      src:
        - src/**/*.vhd                   # VHDL files
        - src/**/*.v                     # Verilog files
        - src/**/*.sv                    # SystemVerilog files
      include:
        - src/include                    # for `include` directives
```

#### RTL + simulation example

```yaml
file_sets:
  rtl:
    - lib: work
      vhdl_std: 2008
      src:
        - src/**/*.vhd
  sim:
    - lib: tb
      vhdl_std: 2008
      src:
        - sim/tb/**/*.vhd                # testbenches
        - sim/models/**/*.vhd            # behavioral models
```

The `sim:` entries are visible to the simulation flow
(`vivado/sim.tcl`, `questa/sim.tcl`, VUnit driver) and invisible to
synthesis (`*/build.tcl`).

### Global include directories

```yaml
include_dirs_global:
  - src/include
  - third_party/include
```

Applied across all compiles for all libraries.

### IP cores

Optional top-level section to register vendor IP. Cores are validated
against the active synthesis tool (a Vivado-only kind under Quartus
emits a `WARN` instead of aborting) and the per-vendor `build.tcl`
adds the IP to the project — for Vivado, IPs land in the same project
alongside the HDL sources from `file_sets`.

```yaml
ip_cores:
  - kind: xci                          # Vivado XCI (IP Catalog export)
    src: ip/clk_wiz.xci
    generate: true                     # optional: synthesize OOC on add
  - kind: bd                           # Vivado block design
    src: ip/design_bd.bd
    generate: true
  - kind: edf                          # generic EDIF netlist (works in any backend)
    src: ip/netlist.edf
    lib: work                          # optional: defaults to 'work'
    module: crypto_core                # optional: top module name inside the EDIF
  - kind: qip                          # Quartus IP (not yet implemented)
    src: ip/pll.qip
  - kind: ipx                          # Diamond IPexpress (registered; generate: true is a no-op)
    src: ip/fifo.ipx
  - kind: lpc                          # Lattice Parameterized Component (registered; generate: true is a no-op)
    src: ip/transceiver.lpc
```

Fields:
- `kind` — **Required**. One of `xci`, `bd` (Vivado), `qip`
  (Quartus), `ipx`, `lpc` (Diamond), or `edf` (generic netlist,
  accepted by every backend).
- `src` — **Required**. Path to the IP source file (relative to
  `project_root` or absolute). Glob patterns are accepted
  (e.g. `ip/**/*.edf`).
- `lib` — Optional. Library for `edf` netlists; defaults to `work`.
- `generate` — Optional. When `true`, Vivado generates the IP
  out-of-context on project create. **Diamond does not honor this
  flag** — IPX/LPC/EDIF files must already exist on disk at the
  configured `src` path; generate them out-of-band via the Diamond
  GUI or `diamondc`. Default depends on `kind` (Vivado IPs default
  to `true`, others default to `false`).
- `module` — Optional. Module name for `edf` cores when it differs
  from the file stem.

Backend coverage:

| Kind   | Vivado      | Quartus                   | Diamond                                       | Radiant      |
|--------|-------------|---------------------------|-----------------------------------------------|--------------|
| `xci`  | Implemented | — (warning)               | — (warning)                                   | — (warning)  |
| `bd`   | Implemented | — (warning)               | — (warning)                                   | — (warning)  |
| `qip`  | — (warning) | Stub (not yet implemented) | — (warning)                                   | — (warning)  |
| `ipx`  | — (warning) | — (warning)               | Implemented (registered; `generate: true` no-op) | — (warning)  |
| `lpc`  | — (warning) | — (warning)               | Implemented (registered; `generate: true` no-op) | — (warning)  |
| `edf`  | Implemented | Implemented               | Implemented                                   | Implemented  |

Mixing IPs of the "wrong" kind for the active synth backend is allowed
(aurig-build only emits a `[WARN]` to stderr), so the same YAML can
describe a project that targets multiple toolchains via the `--tool`
override — non-matching IPs are skipped at the build step.

### Simulation configuration

Optional section for simulation-specific settings. Only used when
running `target: sim`.

```yaml
sim:
  default_top_tb: tb_demo_top            # default testbench entity/module name
  generics:                              # VHDL generics / Verilog parameters
    G_SEED: 42
    G_TIMEOUT_CYC: 100000
    G_CLK_PERIOD: 10.0
  waves: waves/demo.do                   # tool-specific wave config (optional)
```

Fields:
- `default_top_tb` — Testbench top-level name (can be overridden with
  `--tb` flag).
- `generics` — Dictionary of generic / parameter overrides passed to
  the simulator.
- `waves` — Path to wave configuration file (Questa: `.do`, Vivado:
  `.wcfg`).

Simulator-specific behavior:

##### VUnit
- Requires a Python driver script in your project. Default path:
  `sim/run_vunit.py`; override with `tool.sim.driver`.
- Generics passed to VUnit test runner.
- Extra args via `--`:
  `aurig-build sim -- --verbose --output-path=out`.

##### Questa
- Automatically compiles and elaborates using
  `aurig_build/questa/sim.tcl`.
- Requires `--tb` or `sim.default_top_tb` to be set.
- Generics passed as `-g` options to `vsim`.

##### XSim
- Uses `aurig_build/vivado/sim.tcl` for compilation and simulation.
- Runs via `vivado -mode tcl`.
- Generics passed as simulation parameters.

### Quartus-specific settings

Additional settings for Intel Quartus projects. Ignored by other tools.

```yaml
quartus:
  qsf_extra:                             # QSF one-liners appended to generated .qsf
    - "set_global_assignment -name OPTIMIZATION_TECHNIQUE BALANCED"
    - "set_global_assignment -name RESERVE_ALL_UNUSED_PINS \"AS INPUT TRI-STATED\""
    - "set_location_assignment PIN_E3 -to clk_in"
    - "set_instance_assignment -name IO_STANDARD \"3.3-V LVCMOS\" -to clk_in"
  qsf_extra_files:                       # external .qsf files to concatenate
    - constraints/quartus/pins.qsf
    - constraints/quartus/timing.qsf
```

Usage:
- `qsf_extra` — List of TCL assignment strings added directly to the
  project QSF.
- `qsf_extra_files` — List of external QSF files concatenated in
  order.
- Useful for pin assignments, I/O standards, optimization settings.

Common QSF assignments:

```tcl
# Pin location
set_location_assignment PIN_E3 -to clk_in

# I/O standard
set_instance_assignment -name IO_STANDARD "3.3-V LVCMOS" -to clk_in

# Optimization
set_global_assignment -name OPTIMIZATION_TECHNIQUE SPEED

# Virtual pins (for timing analysis without I/O)
set_instance_assignment -name VIRTUAL_PIN ON -to debug_out[*]
```

### Environment variables

Two scopes exist: variables you set in YAML to pass to tool processes,
and variables aurig-build itself reads to alter its behavior.

#### YAML `env:` section

Custom environment variables made available to tool scripts. Added to
the environment before tool execution. Rarely needed.

```yaml
env:
  CUSTOM_VAR: "value"
```

#### aurig-build environment variables

| Variable                   | Effect                                                                                              |
|----------------------------|-----------------------------------------------------------------------------------------------------|
| `AURIG_BUILD_DEBUG=1`      | Verbose internal output (PATH probes, exe resolution, env script results).                          |
| `AURIG_BUILD_PROJECT_ROOT` | Override the project root derivation (absolute, or relative to the cfg directory).                  |
| `FPYGA_DEBUG=1`            | **Deprecated** — legacy alias for `AURIG_BUILD_DEBUG`. Still honored with a `DeprecationWarning`; will be removed in a future release. |

## Complete configuration example

A comprehensive example showing all major sections:

```yaml
# Project metadata
project_name: camera_pipeline
project_root: ../..
top: cam_top
require_exact_versions: false            # warn on mismatch, don't fail

# Tool configuration
tool:
  synth:
    kind: vivado
    version: "2023.1"
    exe: vivado
    env_script:
      linux:   /opt/Xilinx/Vivado/2023.1/settings64.sh
      windows: C:/Xilinx/Vivado/2023.1/settings64.bat

  sim:
    kind: questa
    version: "2023.2"
    exe: vsim
    bin_dir:
      linux:   /opt/mentor/questa/2023.2/bin
      windows: C:/questasim64_2023.2/win64

# Target device
device:
  vendor: xilinx
  family: kintexu
  part: xcku040-ffva1156-2-e

# Constraints
board:
  xdc_files:
    - constraints/clocks.xdc
    - constraints/pins.xdc
    - constraints/timing.xdc

# Global includes
include_dirs_global:
  - src/common/include
  - third_party/axi/include

# Source files
file_sets:
  rtl:
    # Utility library
    - lib: util
      vhdl_std: 2008
      src:
        - lib/util/**/*.vhd

    # Video processing library
    - lib: video
      vhdl_std: 2008
      src:
        - lib/video/**/*.vhd
        - lib/video/**/*.sv
      include:
        - lib/video/include

    # Top-level design
    - lib: work
      vhdl_std: 2008
      src:
        - src/common/**/*.vhd
        - src/cam_top.vhd

  sim:
    # Testbenches (visible to vsim / xsim / VUnit, hidden from synthesis)
    - lib: tb
      vhdl_std: 2008
      src:
        - sim/tb/**/*.vhd

# Simulation settings
sim:
  default_top_tb: tb_cam_top
  generics:
    G_IMAGE_WIDTH: 1920
    G_IMAGE_HEIGHT: 1080
    G_TIMEOUT_CYC: 500000
  waves: waves/cam_top.do
```

## Configuration best practices

1. **Single source of truth** — keep all inputs and settings in YAML.
2. **Explicit globs** — list source files explicitly for
   reproducibility.
3. **Library organization** — group reusable RTL in dedicated
   libraries.
4. **Version control** — treat this file as a critical artifact;
   review all changes.
5. **Vendor separation** — keep tool-specific settings in namespaced
   sections.
6. **Order matters** — file sets compiled in order; constraints
   processed in order.
