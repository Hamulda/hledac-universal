"""
core/env_config.py — Canonical env-var registry with functools.cache

Sprint F280: Centralizes ALL os.environ.get("HLEDAC_*") lookups.
P4-11 fix: Replaced _LazyEnvConfig DCLP race → functools.cache (thread-safe,
zero-overhead, eliminates 2× _EnvConfig init race entirely).

Problem solved:
    - _env_flag existed only in sprint_scheduler.py (L176-187)
    - Every other module called os.environ.get() directly at runtime
    - No caching = ~3.5us overhead per call (measured M1 Air)
    - Mixing cached vs raw lookups = incoherent configuration state

Design (P4-11: matches core/constants.py singleton pattern):
    - @functools.cache on module-level _get_cached — thread-safe, GIL-protected
    - _CacheAccessor provides dot-access compat + typed getters
    - Typed getters: get_bool, get_int, get_float, get_str — single conversion point
    - env vars read ONCE per name, cached for process lifetime
    - Backward compat: ENV.get() preserves raw os.environ.get semantics

Usage:
    from hledac.universal._core.env_config import ENV

    if ENV.get_bool("HLEDAC_ENABLE_DARK_PIVOTS"):
        ...
    pool_size: int = ENV.get_int("HLEDAC_CURL_CFFI_POOL_SIZE", default=4)
    threshold: float = ENV.get_float("HLEDAC_ANE_DEDUP_THRESHOLD", default=0.92)

Invariant tests (TestSprintF280):
    INV: env_config_singleton — only one _CacheAccessor instance
    INV: env_config_cached — same name returns same value (cache hit)
    INV: env_config_lazy — no env lookups at import time
    INV: env_config_fail_safe — Exception → default returned
    INV: env_config_bool_interprets_truthy — "1"/"true"/"yes" → True; "0"/""/miss → False
    INV: env_config_int_casts — valid int string → int; invalid → default
    INV: env_config_float_casts — valid float string → float; invalid → default
    INV: env_config_no_shared_mutable_state — mutable defaults are copied
    INV: env_config_module_level_constant_replaced — all existing module-level
           HLEDAC_* constants migrated to ENV.get_* getters

Anti-patterns prevented:
    - Raw os.environ.get("HLEDAC_...") in runtime modules → must use ENV.*
    - Module-level _HLEDAC_* constants that duplicate ENV lookups
    - os.getenv without default (miss returns None → subtle bugs)
"""

__all__ = [
    "API_KEY_ALIASES",
    "ENV",
    "_get_cached",
    "credential_names",
    "get_api_key",
]

import os
from functools import cache
from typing import Any

#: L2: canonical ``HLEDAC_*`` credential name → legacy/vendor aliases.
#:
#: The canonical name always wins. Aliases stay supported because they are the
#: de-facto names emitted by the vendor tooling (``shodan init``, ``censys
#: config``) and already live in existing CI secret stores — silently dropping
#: them would break every deployed key.
#:
#: NOTE: ``HLEDAC_CENSYS_API_SECRET`` intentionally lists both ``CENSYS_API_SECRET``
#: and ``CENSYS_SECRET``; the codebase historically read one in ``recon/censys_lane.py``
#: and the other in ``recon/exposure_clients.py``, so a key set for one lane was
#: invisible to the other.
API_KEY_ALIASES: dict[str, tuple[str, ...]] = {
    "HLEDAC_SHODAN_API_KEY": ("SHODAN_API_KEY",),
    "HLEDAC_CENSYS_API_ID": ("CENSYS_API_ID",),
    "HLEDAC_CENSYS_API_SECRET": ("CENSYS_API_SECRET", "CENSYS_SECRET"),
    "HLEDAC_GREYNOISE_API_KEY": ("GREYNOISE_API_KEY",),
    "HLEDAC_IPINFO_API_KEY": ("IPINFO_API_KEY",),
    "HLEDAC_HIBP_API_KEY": ("HIBP_API_KEY",),
}


def credential_names(canonical: str) -> tuple[str, ...]:
    """Return the full resolution order ``(canonical, *aliases)`` for a credential.

    Used both by :func:`get_api_key` (read path) and by
    ``security.secrets_scrubber`` (redaction path) so a key can never be
    readable under a name the scrubber does not know about.
    """
    return (canonical, *API_KEY_ALIASES.get(canonical, ()))


def get_api_key(canonical: str, default: str = "") -> str:
    """Resolve an API credential: canonical ``HLEDAC_*`` name first, then aliases.

    Deliberately **not** memoized via :func:`_get_cached`, unlike every other
    getter in this module:

    * secrets must stay rotatable within a process lifetime,
    * a process-wide ``functools.cache`` would retain secret material for the
      whole run (and expose it via ``_get_cached.cache_info``/introspection),
    * credentials are read O(lanes) times per sprint, not in a hot path, so the
      ~3.5 µs ``os.environ`` lookup is irrelevant.

    Returns the stripped value, or ``default`` when nothing is configured.
    """
    if not isinstance(canonical, str):
        return default
    for name in credential_names(canonical):
        val = os.environ.get(name)
        if val:
            stripped = val.strip()
            if stripped:
                return stripped
    return default


@cache  # thread-safe, bounded by Python's cache implementation
def _get_cached(name: str) -> str:
    """Cached raw env lookup — thread-safe via GIL, no DCLP race.

    Rejects non-str names to prevent accidental cache poisoning
    via loose type coercion (e.g. int keys from config files).

    Note: os.environ.get() never raises in CPython — try/except removed
    to satisfy type checkers (unreachable code warning).

    M9: canonical HLEDAC_* names transparently fall back to the legacy
    GHOST_* prefix, preserving existing M1 deployments without breakage.
    """
    if not isinstance(name, str):
        return ""
    # M9: HLEDAC_* is canonical; GHOST_* is the deprecated legacy alias.
    if name.startswith("HLEDAC_"):
        val = os.environ.get(name)
        if val is None:
            val = os.environ.get("GHOST_" + name[len("HLEDAC_"):])
        if val is None:
            return ""
        return val.strip()
    return (os.environ.get(name) or "").strip()


class _CacheAccessor:
    """Dot-access + typed-getter wrapper around _get_cached.

    Replaces _LazyEnvConfig + _EnvConfig — stateless, no DCLP race,
    no threading.Lock needed (functools.cache is GIL-protected).
    """

    __slots__ = ()

    def get_bool(self, name: str, default: bool = False) -> bool:
        """Return HLEDAC flag as bool.

        Truthy: "1", "true", "yes" (case-insensitive)
        Falsy:  "0", "false", "no", "" (including missing key → default)
        """
        val = _get_cached(name)
        if not val:
            return default
        return val.lower() in ("1", "true", "yes")

    def get_int(self, name: str, default: int = 0) -> int:
        """Return env var as int; invalid/missing → default."""
        val = _get_cached(name)
        if not val:
            return default
        try:
            return int(val)
        except (ValueError, TypeError):
            return default

    def get_float(self, name: str, default: float = 0.0) -> float:
        """Return env var as float; invalid/missing → default."""
        val = _get_cached(name)
        if not val:
            return default
        try:
            return float(val)
        except (ValueError, TypeError):
            return default

    def get_str(self, name: str, default: str = "") -> str:
        """Return env var as str; missing/empty → default."""
        val = _get_cached(name)
        return val if val else default

    def get_memory_bytes(self, name: str, default: str = "3GB") -> int:
        """Return env var as memory bytes (e.g. '3GB', '512MB', '1TB').

        Falls back to default string if env var not set.
        Returns 0 for invalid/unparseable strings.
        """
        val = _get_cached(name)
        if not val:
            val = default
        return _parse_memory_string(val)

    def get(self, name: str, default: str = "") -> str:
        """[DEPRECATED] Alias for get_str(). Use get_str() for new code."""
        return self.get_str(name, default)

    def get_api_key(self, canonical: str, default: str = "") -> str:
        """Resolve an API credential by canonical ``HLEDAC_*`` name (uncached).

        See module-level :func:`get_api_key` for the rationale behind skipping
        the cache for secret material.
        """
        return get_api_key(canonical, default)

    @property
    def UVLOOP_ENABLED(self) -> bool:
        """Whether uvloop is enabled (HLEDAC_UVLOOP_ENABLED, default: True).

        uvloop provides ~2× I/O speedup on M1 vs native asyncio kqueue.
        """
        return self.get_bool("HLEDAC_UVLOOP_ENABLED", default=True)

    @property
    def EAGER_START_ENABLED(self) -> bool:
        """Whether eager_start=True is used for asyncio tasks (HLEDAC_EAGER_START_ENABLED, default: True).

        eager_start runs coroutines synchronously up to first await, eliminating
        ~15-30μs scheduling overhead per task (Python 3.12+).

        Note: When uvloop is installed, eager_start is applied to TaskGroup.create_task()
        (handled at stdlib level) but NOT to safe_create_task() unless OTel
        instrumentation with proper eager_start support is available.

        Set to False for debugging or when library compatibility issues arise.
        """
        return self.get_bool("HLEDAC_EAGER_START_ENABLED", default=True)

    def __getattr__(self, name: str) -> Any:
        """Route HLEDAC_* → _get_cached(name) for dot-access compatibility."""
        return _get_cached(name)


def _parse_memory_string(s: str) -> int:
    """Parse memory string like '3GB', '512MB', '1TB' to bytes.

    IMPORTANT: Must check longest suffixes first (TB→GB→MB→KB→B) to avoid
    'B' matching before 'GB' when parsing '3GB'.endswith('B') = True.
    """
    s = s.strip().upper()
    if not s:
        return 0
    multipliers = [
        ("TB", 1024**4),
        ("GB", 1024**3),
        ("MB", 1024**2),
        ("KB", 1024),
        ("B", 1),
    ]
    for suffix, mult in multipliers:
        if s.endswith(suffix):
            num_str = s[: -len(suffix)].strip()
            try:
                return int(float(num_str) * mult)
            except (ValueError, TypeError):
                return 0
    try:
        return int(s)
    except (ValueError, TypeError):
        return 0


#: Process-wide singleton — imported eagerly, resolved lazily
ENV: _CacheAccessor = _CacheAccessor()
