"""
Shim for hledac.core.resilience — bypasses hledac.core.__init__.py chain
which has cross-dependencies that fail at top-level import time.
"""
import sys
from importlib import util as importlib_util
from importlib.machinery import ModuleSpec
from pathlib import Path

# Path to the actual sibling module file
# _shims/ -> universal/ -> hledac/ -> Hledac/ -> parent = Hledac root
# Then hledac/core/resilience.py
_SIBLING_ROOT = Path(__file__).parent.parent.parent.parent / "hledac"
_RESILIENCE_PATH = _SIBLING_ROOT / "core" / "resilience.py"

# Set up hledac.core namespace so relative imports in sibling resolve
if "hledac.core" not in sys.modules:
    core_pkg = ModuleSpec("hledac.core", None)
    sys.modules["hledac.core"] = core_pkg

if not _RESILIENCE_PATH.exists():
    raise ImportError(f"hledac.core.resilience not found at {_RESILIENCE_PATH}")

# Use importlib to load the module directly without triggering __init__.py
spec = importlib_util.spec_from_file_location("hledac.core.resilience", _RESILIENCE_PATH)
assert spec and spec.loader
module = importlib_util.module_from_spec(spec)
sys.modules["hledac.core.resilience"] = module
spec.loader.exec_module(module)

# Re-export
AgentExecutionError = module.AgentExecutionError

# CircuitBreakerOpen may not exist in the actual file
CircuitBreakerOpen = getattr(module, "CircuitBreakerOpen", None)
if CircuitBreakerOpen is None:
    class CircuitBreakerOpen(Exception):
        """Raised when circuit breaker is open."""
        pass

__all__ = ["AgentExecutionError", "CircuitBreakerOpen"]
