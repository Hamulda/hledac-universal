"""Probe Q1: Arch Rules as CI — Hermetic Tests

Tests for architecture rules enforcement:
  - BLE001: bare except ban (via ble_audit.py)
  - ASYNC461: raw asyncio.gather ban (via ban_raw_gather.py)
  - E911: asyncio.run() outside allowed entry points
  - F911: asyncio.wait_for ban (use safe_wait_for)
  - TPL001: threading.Lock() must be registered
  - RUFF022: banned bare imports (from runtime, brain, etc.)
  - networkx ban
  - aiohttp runtime ban (deps removed)
  - stdlib json in hot paths ban
  - direct rust import ban (use hledac_rust_extensions wrapper)

Each test is hermetic — no network, no scheduler, no MLX.
M1 timeout budget: 30s per test.
"""

from __future__ import annotations

from _core import aclose
