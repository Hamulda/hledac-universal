"""
Shim for hledac.core.mlx_embeddings — proxies to universal/core/mlx_embeddings.py.
Bypasses hledac.core.__init__.py chain which fails due to cross-dependencies in hledac/.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SELF_DIR = Path(__file__).parent.resolve()
_MLX_PATH = _SELF_DIR.parent / "core" / "mlx_embeddings.py"

# Set up hledac.core namespace so relative imports in sibling resolve
if "hledac.core" not in sys.modules:
    core_pkg = type(sys)("hledac.core")
    sys.modules["hledac.core"] = core_pkg

spec = importlib.util.spec_from_file_location("hledac.core.mlx_embeddings", _MLX_PATH)
mod = importlib.util.module_from_spec(spec)
sys.modules["hledac.core.mlx_embeddings"] = mod
spec.loader.exec_module(mod)

# Re-export public API using absolute import from the target module
_MLX_MOD = sys.modules["hledac.core.mlx_embeddings"]
MLXEmbeddingManager = _MLX_MOD.MLXEmbeddingManager
get_embedding_manager = _MLX_MOD.get_embedding_manager
EmbeddingTask = _MLX_MOD.EmbeddingTask
EmbeddingDimensionError = _MLX_MOD.EmbeddingDimensionError
assert_embedding_dimension = _MLX_MOD.assert_embedding_dimension
should_normalize = _MLX_MOD.should_normalize
apply_task_prefix = _MLX_MOD.apply_task_prefix

__all__ = [
    "MLXEmbeddingManager",
    "get_embedding_manager",
    "EmbeddingTask",
    "EmbeddingDimensionError",
    "assert_embedding_dimension",
    "should_normalize",
    "apply_task_prefix",
]
