"""
core.__main__ — Canonical sprint entry point (F350M-R legacy re-export).

ROLE: CANONICAL SPRINT OWNER (per sprint_entrypoint.py:2056).

This module exists for backward compatibility and test infrastructure that
patches `hledac.universal._core.__main__.run_sprint`. The actual implementation
lives in `runtime.sprint_entrypoint.run_sprint`; this module re-exports it
*lazily* via PEP 562 `__getattr__`, so importing this module never pulls in the
runtime layer (see the `_LAZY_ATTRS` comment below).

Canonical path referenced by:
- tests/test_r0_nonfeed_reality_lock.py
- runtime/sprint_entrypoint.py (canonical_sprint_owner documentation)
- Various probe/audit reports
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover — static analysis only, never imported at runtime
    from hledac.universal.runtime.sprint_entrypoint import SprintFlags as SprintFlags
    from hledac.universal.runtime.sprint_entrypoint import run_sprint as run_sprint

# F350M-R: Lazy imports to break core ↔ runtime cycle

# Lazy runtime access
_runtime_run_sprint_impl = None
_SprintFlags_impl = None


def _get_runtime_run_sprint():
    """Lazy getter for run_sprint from runtime.sprint_entrypoint."""
    global _runtime_run_sprint_impl
    if _runtime_run_sprint_impl is None:
        from hledac.universal.runtime.sprint_entrypoint import run_sprint as _impl

        _runtime_run_sprint_impl = _impl
    return _runtime_run_sprint_impl


def _get_SprintFlags():
    """Lazy getter for SprintFlags from runtime.sprint_entrypoint."""
    global _SprintFlags_impl
    if _SprintFlags_impl is None:
        from hledac.universal.runtime.sprint_entrypoint import SprintFlags as _impl

        _SprintFlags_impl = _impl
    return _SprintFlags_impl


#: L4: attribute-level lazy re-export (PEP 562).
#:
#: A module-level ``run_sprint = _get_runtime_run_sprint()`` would defeat the
#: whole F350M-R lazy pattern: merely importing this module (which every probe,
#: audit tool and ``cli/parser.py`` docstring reference does) would eagerly pull
#: in ``runtime.sprint_entrypoint`` and re-materialise the core ↔ runtime import
#: cycle this module exists to break.
#:
#: ``__getattr__`` keeps every existing access pattern working:
#:   * ``from hledac.universal._core.__main__ import run_sprint``
#:   * ``_core_main.run_sprint`` (probes, benchmarks/harness.py)
#:   * ``mock.patch("hledac.universal._core.__main__.run_sprint")`` — ``patch``
#:     resolves the original via ``getattr`` and then writes a real entry into
#:     ``__dict__``, which shadows ``__getattr__`` for the duration of the patch.
#:   * plain ``_core_main.run_sprint = stub`` (tests/test_exit_codes.py) — same
#:     ``__dict__`` shadowing, no import of the real implementation at all.
_LAZY_ATTRS: dict[str, str] = {
    "run_sprint": "_get_runtime_run_sprint",
    "SprintFlags": "_get_SprintFlags",
}


def __getattr__(name: str) -> object:
    """Resolve lazily re-exported runtime symbols on first attribute access."""
    getter_name = _LAZY_ATTRS.get(name)
    if getter_name is not None:
        return globals()[getter_name]()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """Expose lazy attributes to ``dir()``/tab-completion (PEP 562 companion)."""
    return sorted({*globals(), *_LAZY_ATTRS})


__all__ = ["SprintFlags", "_get_SprintFlags", "run_sprint"]
