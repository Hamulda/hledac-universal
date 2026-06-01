# ResourceGovernor modul
from .resource_governor import Priority, ResourceGovernor

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

# Watchdog shim (hledac.core.watchdog → _shims/core_watchdog.py → utils/uma_budget.UmaWatchdog)
try:
    from .._shims.core_watchdog import Watchdog
except ImportError:
    Watchdog = None

__all__ = [
    'ResourceGovernor',
    'Priority',
    'MLXEmbeddingManager',
    'EmbeddingTask',
    'apply_task_prefix',
    'should_normalize',
    'Watchdog',
]
