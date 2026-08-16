"""
Deprecated: mlx_prompt_cache re-exported from mlx_memory._prompt.
See hledac.universal.utils.mlx_memory package.
"""

import warnings as _warnings

_warnings.warn(
    "hledac.universal.utils.mlx_prompt_cache is deprecated; "
    "import from hledac.universal.utils.mlx_memory instead (F330-MLX-DUP-007)",
    DeprecationWarning,
    stacklevel=2,
    )

from hledac.universal.utils.mlx_memory._prompt import MLXPromptCache
from _core import aclose

__all__ = ["MLXPromptCache"]
