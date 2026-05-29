"""Legacy modules — kept for reference only.
These are NOT imported by the active sprint pipeline.
Import paths from legacy/ will raise DeprecationWarning.

Note: This package intentionally does NOT auto-import its modules.
To use a legacy module, import it directly:
    from legacy.atomic_storage import AtomicJSONKnowledgeGraph
"""

import os as _os
import sys as _sys

# Ensure this package exists in sys.modules
import types as _types

if "legacy" not in _sys.modules:
    _pkg = _types.ModuleType("legacy")
    _pkg.__path__ = [_os.path.dirname(__file__)]
    _pkg.__package__ = "legacy"
    _sys.modules["legacy"] = _pkg
