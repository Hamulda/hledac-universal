"""
Deprecated: mlx_utils re-exported from mlx_memory._core.
See hledac.universal.utils.mlx_memory package.
"""

import warnings as _warnings

_warnings.warn(
    "hledac.universal.utils.mlx_utils is deprecated; "
    "import from hledac.universal.utils.mlx_memory instead (F330-MLX-DUP-007)",
    DeprecationWarning,
    stacklevel=2,
)

from hledac.universal.utils.mlx_memory._core import (
    get_mlx_memory_stats,
    mlx_cleanup_after,
    mlx_managed,
    reset_metal_peak,
)

__all__ = [
    "mlx_managed",
    "mlx_cleanup_after",
    "get_mlx_memory_stats",
    "reset_metal_peak",
]
