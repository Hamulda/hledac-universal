"""
F11: Canonical feature flag resolvers.

Single source of truth for env-var-based feature gating. All callers
across the codebase should import from here — never re-implement
``os.environ.get("HLEDAC_…") in ("1","true","yes","on")``.

Design rules (GHOST_INVARIANTS):
- Pure functions, no I/O, no module state
- Named except clauses (``(AttributeError, TypeError)``) — never ``Exception``
- Fail-safe default: feature off
- Resolution order: explicit config flag → env var → default off
"""

from __future__ import annotations

import os
from typing import Final


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_TRUTHY_TOKENS: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on"})


def _env_truthy(name: str) -> bool:
    """Return True iff env var ``name`` is set to a truthy token.

    Truthy tokens (case-insensitive, after strip): ``1``, ``true``, ``yes``, ``on``.
    Anything else — including absent variable, empty string, ``"0"``,
    ``"false"``, ``"no"`` — returns False. Never raises.
    """
    try:
        raw = os.environ.get(name, "0")
    except (AttributeError, TypeError):
        return False
    try:
        return raw.strip().lower() in _TRUTHY_TOKENS
    except (AttributeError, TypeError):
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# Canonical env var name for F11 deep research (Sprint F11 spec).
HLEDAC_ENABLE_DEEP_RESEARCH: Final[str] = "HLEDAC_ENABLE_DEEP_RESEARCH"

# Backward-compat alias kept by historical live_public_pipeline.py usage.
# Prefer :data:`HLEDAC_ENABLE_DEEP_RESEARCH` for new code.
HLEDAC_DEEP_RESEARCH_LEGACY: Final[str] = "HLEDAC_DEEP_RESEARCH"


def is_deep_research_enabled(config_flag: bool = False) -> bool:
    """Resolve the F11 deep-research gate.

    Resolution order (first match wins):

    1. ``config_flag`` (CLI ``--deep-research`` or SprintSchedulerConfig flag)
    2. ``HLEDAC_ENABLE_DEEP_RESEARCH`` env var
    3. ``HLEDAC_DEEP_RESEARCH`` env var (legacy alias for backward compat)
    4. Default: ``False`` (fail-safe, opt-in)

    Returns a plain ``bool``. Never raises. Both env var names are honored
    so existing deployments that set the legacy name keep working.
    """
    if config_flag:
        return True
    if _env_truthy(HLEDAC_ENABLE_DEEP_RESEARCH):
        return True
    if _env_truthy(HLEDAC_DEEP_RESEARCH_LEGACY):
        return True
    return False


# ---------------------------------------------------------------------------
# Generic resolver — Phase 2 (Declarative FlagSpec registry)
# ---------------------------------------------------------------------------

#: Tokens that disable a flag when present as the env-var value.
#: ``is_enabled`` returns ``False`` for these; any other non-empty
#: value (including ``"true"``, ``"yes"``, ``"on"``) returns ``True``.
_FALSEY_TOKENS: Final[frozenset[str]] = frozenset({"0", "false", ""})


def is_enabled(flag_name: str, default: str = "0") -> bool:
    """Resolve an arbitrary HLEDAC_* env var to a boolean.

    Generic companion to :func:`is_deep_research_enabled` for callers
    that need to read a feature flag without hardcoding the resolution
    semantics at every call site. The companion declarative metadata
    lives in :mod:`utils.flag_registry`.

    Resolution:

    * Read ``os.environ[flag_name]`` (falling back to ``default``).
    * If the raw value (after strip+lower) is in :data:`_FALSEY_TOKENS`
      (``"0"``, ``"false"``, ``""``) return ``False``.
    * Otherwise return ``True``.

    ``default`` defaults to ``"0"`` (fail-safe off). Callers that ship
    a flag ON by default may pass ``default="1"``. Never raises —
    environment access is guarded by ``try/except``.
    """
    try:
        raw = os.environ.get(flag_name, default)
    except (AttributeError, TypeError):
        return False
    try:
        return raw.strip().lower() not in _FALSEY_TOKENS
    except (AttributeError, TypeError):
        return False


__all__ = [
    "HLEDAC_ENABLE_DEEP_RESEARCH",
    "HLEDAC_DEEP_RESEARCH_LEGACY",
    "is_deep_research_enabled",
    "is_enabled",
]
