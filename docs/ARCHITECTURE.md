# Architecture decisions

This document records architectural decisions that shaped aurig-build.
Each section captures the *what* and the *why*, so contributors don't
have to reverse-engineer the rationale from code archaeology.

## Schema duplication (Design B)

The canonical manifest schema (`aurig_build/schema/manifest-v1.json`)
lives **inside the `aurig-build` package**. The same file is also
shipped inside `aurig-core` (the future Tcl reference implementation).
Both copies must stay in sync.

### Why duplicate instead of central

Considered alternatives:

1. **Central repo `aurig/schema`** — Schema lives in its own repo,
   both `aurig-build` and `aurig-core` declare a dependency on it.
   Pro: single source of truth. Con: forces every consumer to add
   a transitive runtime dependency; complicates `pip install
   aurig-build` (a Python tool would depend on a Python schema
   package); adds a repo to maintain.

2. **Schema duplicated in each consumer (chosen)** — Each tool
   ships its own copy. Pro: every tool installs independently with
   no cross-dependency; users who only need `aurig-build` don't
   pull `aurig-core`. Con: explicit sync discipline required.

The decision favors **install independence over architectural
elegance**. Users adopting one tool of the AURIG suite (e.g.,
`aurig-build` only, without `aurig-lint`) should not be forced to
install other packages just to satisfy a schema dependency.

### Sync discipline

When the schema changes:

- **The change lands first in `aurig-build`** (primary edit
  location). `aurig-build/schema/manifest-v1.json` is the source
  of truth.
- **`aurig-core` copies the file verbatim** in a follow-up commit
  referencing the `aurig-build` PR.
- **Version bumps coordinate**: when bumping schema (e.g., adding
  `manifest-v2.json`), both repos add the new file in the same
  release cycle, and both keep the older file for backward
  compatibility.

Schemas are versioned explicitly via filename (`manifest-v1.json`,
`manifest-v2.json`, …) and via the `schema_version` field in the
manifest itself. Consumers that don't recognize a `schema_version`
reject the manifest with a clear error.
