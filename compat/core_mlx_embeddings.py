"""
Shim for hledac.core.mlx_embeddings — proxies to universal/core/mlx_embeddings.py.
Bypasses hledac.core.__init__.py chain which fails due to cross-dependencies in hledac/.

F265-5.2: Re-exports actual functions/classes from the target module.
Supports multiple import paths (direct, via hledac.universal, etc.) by checking
all possible sys.modules keys where the target might be registered.
"""
from __future__ import annotations


import sys
from pathlib import Path

_SELF_DIR = Path(__file__).parent.resolve()
_SRC_PATH = _SELF_DIR.parent / "core" / "mlx_embeddings.py"

# F265-5.2: Check all possible keys where the original module might be registered.
# Order matters: most specific first.
_POSSIBLE_KEYS = [
    "hledac.universal.core.mlx_embeddings",  # via hledac.universal path
    "hledac.core.mlx_embeddings",  # via hledac.core path
    "core.mlx_embeddings",  # direct import
]

_target = None
for key in _POSSIBLE_KEYS:
    _target = sys.modules.get(key)
    if _target is not None:
        break

if _target is None:
    import importlib.util

    # Ensure hledac.core namespace exists for any internal imports in the target
    if "hledac.core" not in sys.modules:
        core_pkg = type(sys)("hledac.core")
        sys.modules["hledac.core"] = core_pkg

    spec = importlib.util.spec_from_file_location(
        "hledac.core.mlx_embeddings", str(_SRC_PATH)
    )
    assert spec is not None and spec.loader is not None
    _target = importlib.util.module_from_spec(spec)
    # Store under all the keys so future shim users find this instance
    for key in _POSSIBLE_KEYS:
        sys.modules[key] = _target
    spec.loader.exec_module(_target)

# Re-export actual objects from target — NOT wrappers
MLXEmbeddingManager = _target.MLXEmbeddingManager
get_mlx_embedder = _target.get_mlx_embedder
# F350M-R: get_embedding_manager → get_mlx_embedder (deprecated alias removed)
get_embedding_manager = _target.get_mlx_embedder
EmbeddingTask = _target.EmbeddingTask
EmbeddingDimensionError = _target.EmbeddingDimensionError
assert_embedding_dimension = _target.assert_embedding_dimension
should_normalize = _target.should_normalize
apply_task_prefix = _target.apply_task_prefix
# F275-5: persistent prewarm
prewarm_embedding_model = _target.prewarm_embedding_model
is_embedding_model_prewarmed = _target.is_embedding_model_prewarmed

__all__ = [
    "MLXEmbeddingManager",
    "get_mlx_embedder",
    "get_embedding_manager",  # deprecated alias
    "EmbeddingTask",
    "EmbeddingDimensionError",
    "assert_embedding_dimension",
    "should_normalize",
    "apply_task_prefix",
    # F275-5: persistent prewarm
    "prewarm_embedding_model",
    "is_embedding_model_prewarmed",
]
