"""
Deprecated: metal_embedder_buffers re-exported from mlx_memory._embedder.
See hledac.universal.utils.mlx_memory package.
"""
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "hledac.universal.utils.metal_embedder_buffers is deprecated; "
    "import from hledac.universal.utils.mlx_memory instead (F330-MLX-DUP-007)",
    DeprecationWarning,
    stacklevel=2,
)

from hledac.universal.utils.mlx_memory._embedder import (
    MetalBufferPool,
    get_buffer_pool,
    init_metal_embedder_buffers,
    release_metal_embedder_buffers,
)

__all__ = [
    "MetalBufferPool",
    "get_buffer_pool",
    "init_metal_embedder_buffers",
    "release_metal_embedder_buffers",
]
