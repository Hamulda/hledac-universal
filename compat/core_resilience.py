"""
Deprecated: use ``hledac.core.resilience`` directly.

The canonical module is at ``hledac/core/resilience.py`` (parent hledac/ package).
This stub uses manual import because hledac.core is a namespace package
and does not support direct sibling imports from the parent directory.

Moved to hledac/core/resilience.py (F350M-R A-01).
This stub exists only for backward compatibility during migration.
"""
from __future__ import annotations

import warnings

__all__ = ["AgentExecutionError", "CircuitBreakerOpenError", "CircuitBreakerOpen"]

warnings.warn(
    "compat.core_resilience is deprecated. Use hledac.core.resilience directly. "
    "This shim will be removed in a future sprint.",
    DeprecationWarning,
    stacklevel=2,
)

# Load from parent hledac/core/resilience.py using explicit path
# (hledac.core is a namespace package — direct import would fail)
import sys
from importlib import util as importlib_util
from importlib.machinery import ModuleSpec
from pathlib import Path

_SIBLING_ROOT = Path(__file__).parent.parent.parent.parent / "hledac"
_RESILIENCE_PATH = _SIBLING_ROOT / "core" / "resilience.py"

if "hledac.core" not in sys.modules:
    core_pkg = ModuleSpec("hledac.core", None)
    sys.modules["hledac.core"] = core_pkg  # type: ignore[index]

if not _RESILIENCE_PATH.exists():
    raise ImportError(f"hledac.core.resilience not found at {_RESILIENCE_PATH}")

spec = importlib_util.spec_from_file_location("hledac.core.resilience", str(_RESILIENCE_PATH))
assert spec and spec.loader
module = importlib_util.module_from_spec(spec)
sys.modules["hledac.core.resilience"] = module
spec.loader.exec_module(module)

AgentExecutionError = module.AgentExecutionError
CircuitBreakerOpenError = module.CircuitBreakerOpenError
CircuitBreakerOpen = CircuitBreakerOpenError
