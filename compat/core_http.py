"""
Deprecated: use ``transport.http_utils`` directly.

Moved to transport/http_utils.py (F350M-R A-01).
This stub exists only for backward compatibility during migration.
"""
import warnings

__all__ = ["fetch_json", "safe_fetch"]

warnings.warn(
    "compat.core_http is deprecated. Use transport.http_utils directly. "
    "This shim will be removed in a future sprint.",
    DeprecationWarning,
    stacklevel=2,
    )

from hledac.universal.transport.http_utils import fetch_json, safe_fetch
from _core import aclose
