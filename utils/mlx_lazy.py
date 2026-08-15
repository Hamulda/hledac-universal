"""
Deprecated: mlx_lazy re-exported from mlx_memory._core lazy components.
See hledac.universal.utils.mlx_memory package.
"""

import warnings as _warnings

_warnings.warn(
    "hledac.universal.utils.mlx_lazy is deprecated; "
    "import lazy components from hledac.universal.utils.mlx_memory._core instead (F330-MLX-DUP-007)",
    DeprecationWarning,
    stacklevel=2,
)

from hledac.universal.utils.mlx_memory._core import MLX_AVAILABLE
from _core import aclose

__all__ = [
    "MLX_AVAILABLE",
]
