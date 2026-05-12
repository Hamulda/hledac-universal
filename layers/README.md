# Layers Module — Active

**Status**: ACTIVE — `enable_communication_layer=True`

`layers/` is actively used by `pipeline/live_public_pipeline.py` (temporal signal 
layer) and by test suites (communication_layer, stealth_layer, memory_layer).

## Module Map

| File | Used By | Notes |
|------|---------|-------|
| `temporal_signal_layer.py` | live_public_pipeline.py | ACTIVE — temporal hints |
| `temporal_signal_runtime.py` | self + temporal_signal_store | ACTIVE |
| `temporal_signal_store.py` | temporal_signal_runtime | ACTIVE |
| `communication_layer.py` | tests, layer_manager | ACTIVE |
| `layer_manager.py` | autonomous_orchestrator | ACTIVE |
| `coordination_layer.py` | self (imports hive/smart_coordination) | BOUNDED |
| `hive_coordination.py` | coordination_layer | BOUNDED — internal only |
| `smart_coordination.py` | coordination_layer | BOUNDED — internal only |
| `stealth_layer.py` | tests | ACTIVE |
| `security_layer.py` | tests | ACTIVE |
| `memory_layer.py` | tests | ACTIVE |
| `content_layer.py` | reports/docs | ACTIVE |

## Investigation Result (Phase 4)

- `content_layer`, `communication_layer`, `hive_coordination`, `smart_coordination` 
  are ALL referenced from outside `layers/`
- `hive_coordination` and `smart_coordination` are used only by `coordination_layer` 
  internally — no external consumer
- `content_layer` is used in F214 audit docs for selectolax migration
- **Conclusion**: No archiving needed. All layers have active consumers.
