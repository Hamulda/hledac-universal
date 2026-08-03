"""
[NEXUS]-018-03: Mach vm_remap zero-copy — Hermetic Probe Tests

Tests the MachRemap Python bridge WITHOUT requiring:
  - macOS (mocked platform)
  - Real Rust extension (mocked)
  - Real fork/mmap syscalls (patched)
  - Actual sandbox subprocess

Coverage:
  (a) Handshake protocol: feature gate, size guard, platform check
  (b) Fallback to tempfile when MachRemapError is raised
  (c) Memory guard: available < 1.5 GiB → skipped
  (d) Zero-copy assert: can_remap() gate, remap_for_sandbox() return shape
  (e) Lazy import: no module loaded until first use
  (f) Env var configuration: HLEDAC_ENABLE_MACH_REMAP, HLEDAC_MACH_REMAP_MIN_SIZE
  (g) Fail-soft: any exception → None returned, no exception propagated
"""
