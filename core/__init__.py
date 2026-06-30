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


import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Stub pro statickou analýzu (pyright) — runtime využívá __getattr__ níže.
    from hledac.universal.core.resource_governor import Priority

# Lazy attrs map: name → plná import cesta k modulu, který jej poskytuje.
# Při prvním `core.Priority` se teprve načte resource_governor.
# Sprint F500I: mlx_embeddings added here to eliminate 20s import bottleneck
_LAZY_ATTRS: dict[str, str] = {
    "Priority": "hledac.universal.core.resource_governor",
    "MLXEmbeddingManager": "hledac.universal.core.mlx_embeddings",
    "EmbeddingTask": "hledac.universal.core.mlx_embeddings",
    "apply_task_prefix": "hledac.universal.core.mlx_embeddings",
    "should_normalize": "hledac.universal.core.mlx_embeddings",
}


def __getattr__(name: str):
    """
    PEP 562 lazy module attribute access.

    Pokud `name` je v `_LAZY_ATTRS`, importujeme příslušný modul a vrátíme atribut.
    Jinak AttributeError (default chování Pythonu).
    """
    if name in _LAZY_ATTRS:
        # noaudit[python.lang.security.audit.non-literal-import.non-literal-import]
        # _LAZY_ATTRS is a static module-level constant dict; values are
        # hardcoded module paths — no user input reaches this call site.
        module = importlib.import_module(_LAZY_ATTRS[name])
        attr = getattr(module, name)
        # Cache pro další přístup (PEP 562 best practice)
        globals()[name] = attr
        return attr
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """PEP 562: REPL/IDE autocomplete podpora — zahrň i lazy attrs."""
    return sorted(set(globals().keys()) | set(_LAZY_ATTRS.keys()))


# Sprint F500I: All imports now lazy via __getattr__
# - MLXEmbeddingManager, EmbeddingTask, apply_task_prefix, should_normalize: lazy via _LAZY_ATTRS
# - Watchdog: lazy via _LAZY_ATTRS (see entry below)

# Watchdog shim (hledac.core.watchdog → _shims/core_watchdog.py → utils/uma_budget.UmaWatchdog)
# Sprint F500I: Moved to lazy import via __getattr__
_LAZY_ATTRS["Watchdog"] = "_shims.core_watchdog"

__all__ = [
    'Priority',
    'MLXEmbeddingManager',
    'EmbeddingTask',
    'apply_task_prefix',
    'should_normalize',
    'Watchdog',
]
