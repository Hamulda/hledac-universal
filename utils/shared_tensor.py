"""
Deprecated: shared_tensor re-exported from mlx_memory._tensor.
See hledac.universal.utils.mlx_memory package.
"""

import warnings as _warnings

_warnings.warn(
    "hledac.universal.utils.shared_tensor is deprecated; "
    "import from hledac.universal.utils.mlx_memory instead (F330-MLX-DUP-007)",
    DeprecationWarning,
    stacklevel=2,
)

from hledac.universal.utils.mlx_memory._tensor import SharedTensor

__all__ = ["SharedTensor"]
