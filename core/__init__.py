"""
core package — central governance, MLX embeddings, watchdog shim.

Lazy module loading (PEP 562) pro M1 8GB UMA cold start:
- `Priority` a `ResourceGovernor` se importují z `resource_governor.py` až při
  prvním přístupu (ne při importu `core` samotného).
- Tím se import `hledac.universal.knowledge.explainer.deep` (který referencuje
  `core.resource_governor` jen pro type hint) neprodražuje o 688 řádků + psutil.
- Canonical dep: psutil zůstává v `requirements.txt`; runtime contract beze změny.

Vzor: Python 3.7+ PEP 562 (module-level __getattr__/__dir__).
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Stub pro statickou analýzu (pyright) — runtime využívá __getattr__ níže.
    from hledac.universal.core.resource_governor import Priority, ResourceGovernor

# Lazy attrs map: name → plná import cesta k modulu, který jej poskytuje.
# Při prvním `core.Priority` / `core.ResourceGovernor` se teprve načte resource_governor.
_LAZY_ATTRS: dict[str, str] = {
    "Priority": "hledac.universal.core.resource_governor",
    "ResourceGovernor": "hledac.universal.core.resource_governor",
}


def __getattr__(name: str):
    """
    PEP 562 lazy module attribute access.

    Pokud `name` je v `_LAZY_ATTRS`, importujeme příslušný modul a vrátíme atribut.
    Jinak AttributeError (default chování Pythonu).
    """
    if name in _LAZY_ATTRS:
        module = importlib.import_module(_LAZY_ATTRS[name])
        attr = getattr(module, name)
        # Cache pro další přístup (PEP 562 best practice)
        globals()[name] = attr
        return attr
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """PEP 562: REPL/IDE autocomplete podpora — zahrň i lazy attrs."""
    return sorted(set(globals().keys()) | set(_LAZY_ATTRS.keys()))


# Eager imports s try/except (fail-soft, vzor již použit v projektu).
# mlx_embeddings: optional canonical feature, ne blokuje core.
try:
    from .mlx_embeddings import (
        EmbeddingTask,
        MLXEmbeddingManager,
        apply_task_prefix,
        should_normalize,
    )
except ImportError:
    MLXEmbeddingManager = None
    EmbeddingTask = None
    apply_task_prefix = None
    should_normalize = None

# Watchdog shim (hledac.core.watchdog → _shims/core_watchdog.py → utils/uma_budget.UmaWatchdog)
try:
    from .._shims.core_watchdog import Watchdog
except ImportError:
    Watchdog = None

__all__ = [
    'ResourceGovernor',
    'Priority',
    'MLXEmbeddingManager',
    'EmbeddingTask',
    'apply_task_prefix',
    'should_normalize',
    'Watchdog',
]
