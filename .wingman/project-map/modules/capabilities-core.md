# Capabilities (Core)

## Metadata

| Field | Value |
| --- | --- |
| Kind | module |
| Status | current |
| Last Verified | 2026-08-19 |
| Evidence Level | source |
| Entry Path | `modules/capabilities-core.md` |
| Source Path | `_core/capabilities.py` |

## Summary

Centralized optional dependency registry replacing scattered try/except ImportError chains. Uses Cap dataclass with `require()` and `dump()`.

## Evidence

- Single facade: `CAPS.require(ZSTD)`, `CAPS.dump()`
- One-line addition: `MY_DEP = Cap("my_dep", "my_package.module")`
- Exports: CAPS, ZSTD, AIOHTTP, LIGHTPANDA
- Telemetry: logs unavailable capabilities at debug level

## Use When

- Adding a new optional dependency
- Checking if a library is available
- Replacing scattered import guards

## Do Not Use When

- The dependency is always required (use normal import)
