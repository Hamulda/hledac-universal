# Capabilities Registry

## Metadata

| Field | Value |
| --- | --- |
| Kind | module |
| Status | current |
| Last Verified | 2026-08-19 |
| Evidence Level | source |
| Entry Path | `modules/capabilities-registry.md` |
| Source Path | `capabilities_registry.py` |

## Summary

Lightweight plugin registry for capability adapters. Validates module presence via `importlib.util.find_spec()` — no network I/O, no full module imports at registration. Declarative: `register_capability()` records metadata only.

## Evidence

- `CapabilityPluginRegistry.register_capability()` — registers with module_spec, env_gate, api_key
- Actual capability loading handled by `CapabilityRegistry.load()` in `_core/capabilities.py`
- Examples: "graph_rag", "bgp" as capability identifiers

## Use When

- Registering a new capability adapter
- Understanding which plugins are available
- Debugging capability loading failures

## Do Not Use When

- Changing the capability implementation (see the specific capability module)
- Changing MLX model lifecycle (see `capabilities.py`)
