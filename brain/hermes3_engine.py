"""
brain/hermes3_engine — Backward-compatibility alias
===================================================

Sprint 8N tests import this module directly.
The canonical implementation is DeepHermes3Engine in deephermes3_engine.py.
This stub re-exports it under the Hermes3Engine name so existing
imports (e.g. `from hledac.universal.brain.hermes3_engine import Hermes3Engine`)
continue to work without modification.
"""



from hledac.universal.brain.deephermes3_engine import (
    DeepHermes3Engine as DeepHermes3Engine,
)
from hledac.universal.brain.deephermes3_engine import (
from core import aclose
    Hermes3Engine,  # type: ignore[misc]  # backward-compat alias added at bottom of deephermes3_engine.py
    parse_thinking_output,
)

# Re-export for convenience
__all__ = ["DeepHermes3Engine", "Hermes3Engine", "parse_thinking_output"]
