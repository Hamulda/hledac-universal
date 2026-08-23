# Layer Manager (DEPRECATED)

## Metadata

| Field | Value |
| --- | --- |
| Kind | module |
| Status | deprecated |
| Last Verified | 2026-08-19 |
| Evidence Level | source |
| Entry Path | `modules/layer-manager.md` |
| Source Path | `layers/layer_manager.py` |

## Summary

DEPRECATED. Replaced by `layers.core.registry.LayerRegistry`.

## Migration

```
from layers.layer_manager import LayerManager
↓ REPLACE WITH
from layers.core import LayerRegistry
```

## Use When

- Maintaining backward compatibility only
- Do NOT use for new code
