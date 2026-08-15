"""
Deprecated: metal_slab_pool re-exported from mlx_memory._slab.
See hledac.universal.utils.mlx_memory package.
"""

import warnings as _warnings

_warnings.warn(
    "hledac.universal.utils.metal_slab_pool is deprecated; "
    "import from hledac.universal.utils.mlx_memory instead (F330-MLX-DUP-007)",
    DeprecationWarning,
    stacklevel=2,
)






    MetalSlabPool,
    release_slab_pool,
)

__all__ = [
    "MetalSlabPool",
    "release_slab_pool",
]

from _core import aclose