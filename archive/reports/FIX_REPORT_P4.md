# FIX REPORT P4 — Dead Code Cleanup + hledac.core.* Redirects

**Date:** 2026-05-31
**Status:** COMPLETE

---

## TASK A: Delete Confirmed Dead Code in ghost_executor.py

### Deleted References

| Component | Lines | Status |
|-----------|-------|--------|
| `_get_stealth_manager` method | 549-577 | DELETED |
| `stealth_mgr = await self._get_stealth_manager()` + `if stealth_mgr: pass` | 663-668 | DELETED |
| `self._stealth_manager = None` initialization | 487 | DELETED |
| `self._stealth_manager = None` cleanup | 985 | DELETED |

### git diff (ghost_executor.py)

```diff
-        self._network_driver = None
-        self._stealth_manager = None
+        self._network_driver = None
         self._bloom_filter = None
```

```diff
-            logger.info("✓ GhostNetworkDriver loaded")
-        return self._network_driver
-
-    async def _get_stealth_manager(self):
-        """
-        Lazy load stealth manager.
-
-        NOTE (Sprint F900G): This import path was stale — hledac.stealth_toolkit
-        does not exist as a top-level package. The canonical stealth system lives at:
-        - hledac.universal.stealth.stealth_manager (active, canonical)
-        - hledac.outdated.stealth_toolkit (deprecated, donor compat)
-
-        This lazy-load path is kept for backward compat with existing call-sites
-        that pass enable_stealth=True. If module resolution fails, returns None
-        (degraded stub) rather than crashing.
-        """
-        if self._stealth_manager is None and self.enable_stealth:
-            try:
-                from hledac.outdated.stealth_toolkit.stealth_orchestrator import StealthOrchestrator as _SO
-                logger.info("Loading StealthOrchestrator (outaged path)...")
-                self._stealth_manager = _SO()
-                logger.info("✓ StealthOrchestrator loaded (degraded/stub)")
-            except ModuleNotFoundError:
-                logger.warning(
-                    "StealthOrchestrator not available — "
-                    "hledac.outdated.stealth_toolkit not in path. "
-                    "Stealth features will be degraded/stub."
-                )
-                self._stealth_manager = None
-        return self._stealth_manager
-
-    async def execute(
+            logger.info("✓ GhostNetworkDriver loaded")
+        return self._network_driver
+
+    async def execute(
```

```diff
         try:
-            if self.enable_stealth:
-                stealth_mgr = await self._get_stealth_manager()
-                if stealth_mgr:
-                    # Stealth režim: použít stealth Google search přes GhostNetworkDriver
-                    # Pokud driver není dostupný, fallback na ddgs
-                    pass
-
             # Fallback: DuckDuckGo s Google backend emulací
+            # DuckDuckGo s Google backend emulací
```

```diff
         if self._network_driver:
             await self._network_driver.close()
             self._network_driver = None

-        self._stealth_manager = None
         self._bloom_filter = None
```

### Result
- File reduced from 992 lines to 953 lines
- No NameError possible — `_get_stealth_manager` no longer exists
- Canonical stealth implementation remains in `layers/stealth_layer.py`

---

## TASK B: Fix hledac.core.mlx_embeddings Redirect

### Change (core/__init__.py)

```python
# MLX embeddings redirect (hledac.core.mlx_embeddings → core/mlx_embeddings.py)
try:
    from .mlx_embeddings import (
        MLXEmbeddingManager,
        EmbeddingTask,
        apply_task_prefix,
        should_normalize,
    )
except ImportError:
    MLXEmbeddingManager = None
    EmbeddingTask = None
    apply_task_prefix = None
    should_normalize = None
```

### Verified
- `from core import MLXEmbeddingManager` works
- `from core import EmbeddingTask` works
- `from core import apply_task_prefix` works
- `from core import should_normalize` works

---

## TASK C: Fix Watchdog Redirect

### Change (core/__init__.py)

```python
# Watchdog shim (hledac.core.watchdog → _shims/core_watchdog.py → utils/uma_budget.UmaWatchdog)
try:
    from .._shims.core_watchdog import Watchdog
except ImportError:
    Watchdog = None
```

### Verified
- `from core import Watchdog` works
- Watchdog wraps `utils.uma_budget.UmaWatchdog` correctly

---

## Smoke Tests

```
uv run python -c "
from execution.ghost_executor import GhostExecutor, GhostBridge
from core import MLXEmbeddingManager, EmbeddingTask, apply_task_prefix, should_normalize
from core import Watchdog

import inspect
source = inspect.getsource(GhostExecutor)
assert '_stealth_manager' not in source
assert '_get_stealth_manager' not in source

assert MLXEmbeddingManager is not None
assert EmbeddingTask is not None
assert apply_task_prefix is not None
assert should_normalize is not None
assert Watchdog is not None

print('ALL SMOKE TESTS PASSED')
"
```

**Result:** ALL PASSED

---

## Summary

| Task | Description | Status |
|------|-------------|--------|
| TASK A | Delete dead code from ghost_executor.py | COMPLETE |
| TASK B | Fix hledac.core.mlx_embeddings export | COMPLETE |
| TASK C | Fix Watchdog redirect | COMPLETE |
