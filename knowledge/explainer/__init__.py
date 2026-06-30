"""
explainer package — GNN/path explanations (Deep, Fast).

Lazy module loading (PEP 562) pro M1 8GB UMA cold start:
- `DeepExplainer` (knowledge.explainer.deep) a `FastExplainer`
  (knowledge.explainer.fast) se importují až při prvním přístupu.
- Důsledek: `import hledac.universal.knowledge.explainer.deep` NEtriggeruje
  load `core.resource_governor` + `psutil` C-ext, pokud deep.py nečte
  Priority/ResourceGovernor name při importu (deep.py je používá jen jako
  type hint, takže se resource_governor nenačte vůbec — Python resolvne
  jméno přes core.__getattr__ jen pokud by deep.py reálně přistoupil k atributu).
- Vzor: core/__init__.py (stejný pattern), Python 3.7+ PEP 562.
"""


import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Stub pro pyright — runtime využívá __getattr__ níže.
    from hledac.universal.knowledge.explainer.deep import DeepExplainer
    from hledac.universal.knowledge.explainer.fast import FastExplainer

# Lazy attrs map.
_LAZY_ATTRS: dict[str, str] = {
    "DeepExplainer": "hledac.universal.knowledge.explainer.deep",
    "FastExplainer": "hledac.universal.knowledge.explainer.fast",
}


_LAZY_WHITELIST: frozenset[str] = frozenset(_LAZY_ATTRS.keys())


def __getattr__(name: str):
    """
    PEP 562 lazy module attribute access.
    Whitelist-only: only names in _LAZY_WHITELIST can be imported.
    """
    if name in _LAZY_WHITELIST:
        module = importlib.import_module(_LAZY_ATTRS[name])
        attr = getattr(module, name)
        globals()[name] = attr
        return attr
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals().keys()) | set(_LAZY_ATTRS.keys()))


__all__ = ['FastExplainer', 'DeepExplainer']
