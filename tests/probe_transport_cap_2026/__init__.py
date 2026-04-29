"""
Probe Tests: Transport Capability Layer 2026 (F206K)

Tests:
  1. httpx_client lazy init and fail-soft when h2 missing
  2. httpx_transport URL classification routing
  3. transport_policy routing truth table
  4. FetchResult telemetry fields (additive, backward-compatible)
  5. aiohttp remains default hot-path
  6. Tor/I2P/Freenet never use HTTPX H2

Run:
  pytest tests/probe_transport_cap_2026/ -v
"""
