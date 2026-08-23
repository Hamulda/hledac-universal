# Layer Registry

## Metadata

- **Entry Path:** components/layer-registry
- **Status:** current
- **Evidence Level:** source
- **Last Verified:** 2026-08-20
- **Category:** component

## Summary

Plugin-like layer system with priority-based execution pipeline.

## Source Paths

- layers/core/registry.py

## Layer Lifecycle

1. Register with name, instance, priority
2. Optional dependencies declared
3. Pipeline rebuilt on register/unregister
4. Mount in priority order
5. Execute through all mounted layers

## Key Methods

| Method | Purpose |
|--------|---------|
| register() | Add layer to pipeline |
| unregister() | Remove layer |
| enable_layer() | Toggle layer active |
| disable_layer() | Toggle layer inactive |
| mount() | Initialize all layers |

## Layers

| Layer | Purpose |
|-------|---------|
| MemoryLayer | Context management |
| GhostLayer | Ghost CTI integration |
| SecurityLayer | Deprecated |
