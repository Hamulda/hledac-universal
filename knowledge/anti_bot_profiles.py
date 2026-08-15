"""AntiBotProfileService — persistent cross-sprint anti-bot fingerprint database.

UNIFIED-010: Replaces per-sprint anti-bot detection with a cumulative,
cross-sprint fingerprint database backed by DuckDB. Eliminates wasteful



re-detection of the same WAF/CDN protections every sprint.

Key features:
- WAF/CDN fingerprinting: Cloudflare, Akamai, DataDome, Imperva, Fastly, CloudFront
- Challenge type tracking: JS challenge, CAPTCHA, Turnstile, 403/429 responses
- Bypass strategy recommendation: curl_cffi, residential proxy, JS render, stealth headers
- Required header/cookie profiles per domain
- Confidence-weighted EMA merging of new observations
- Stealth level determination: none → standard → aggressive → JS render

M1 8GB safety:
- Bounded table via HLEDAC_ANTI_BOT_PROFILES_MAX_ROWS (default 5000)
- In-memory hot-path cache: 256 entries LRU, 10-min TTL
- No new thread pools — all DB ops via DuckDBShadowStore's _shared_executor
- msgspec.Struct(frozen=True, gc=False) for zero-alloc hot-path reads

Feature flag: HLEDAC_ANTI_BOT_PROFILES=1 (default ON). Set to 0 to disable
persistence (in-memory fallback).

Integration points:
- public_fetcher.py: consulted before fetch to determine bypass strategy
- stealth_manager.py: consulted for stealth level and JA3 randomization
- transport_race.py: may skip transports known to fail against certain WAFs
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json as _json
import os
import threading
import time as _time
from typing import TYPE_CHECKING, Any

import msgspec
from hledac.universal.compat.msgspec_gc_compat import Struct

from hledac.universal.utils.logging_config import get_logger

if TYPE_CHECKING:
    from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------
_ANTI_BOT_PROFILES_ENABLED: bool = (
    os.getenv("HLEDAC_ANTI_BOT_PROFILES", "1") != "0"
)
_ANTI_BOT_PROFILES_MAX_ROWS: int = int(
    os.getenv("HLEDAC_ANTI_BOT_PROFILES_MAX_ROWS", "5000")
)
# In-memory cache config
_PROFILE_CACHE_MAX: int = 256
_PROFILE_CACHE_TTL_S: float = 600.0  # 10 minutes
# Confidence EWMA alpha
_CONFIDENCE_ALPHA: float = 0.3


# ---------------------------------------------------------------------------
# AntiBotProfile DTO
# ---------------------------------------------------------------------------

class AntiBotProfile(Struct, frozen=True):
    """Immutable anti-bot fingerprint snapshot from DuckDB.

    gc=False for M1 8GB — avoids GC overhead on hot-path lookup.
    """

    domain: str
    waf_type: str = "none"
    challenge_types: tuple[str, ...] = ()
    bypass_strategy: str = "none"
    required_headers: tuple[str, ...] = ()
    required_cookies: tuple[str, ...] = ()
    js_rendering_needed: bool = False
    residential_proxy_needed: bool = False
    stealth_level: str = "none"
    ja3_randomize: bool = False
    block_patterns: tuple[str, ...] = ()
    confidence: float = 0.0
    observation_count: int = 0
    last_challenge_seen: float = 0.0
    last_bypass_success: float = 0.0
    first_seen: float = 0.0

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------

    @property
    def is_profiled(self) -> bool:
        """True if we have enough observations to trust this profile."""
        return self.observation_count >= 2 and self.confidence > 0.3

    @property
    def is_cloudflare(self) -> bool:
        return self.waf_type == "cloudflare"

    @property
    def is_akamai(self) -> bool:
        return self.waf_type == "akamai"

    @property
    def needs_js_render(self) -> bool:
        """True if domain requires JS rendering (Turnstile, advanced JS challenge)."""
        return self.js_rendering_needed or "turnstile" in self.challenge_types

    @property
    def needs_residential_proxy(self) -> bool:
        """True if datacenter proxies are blocked for this domain."""
        return self.residential_proxy_needed

    @property
    def recommended_transport(self) -> str:
        """Recommend the best transport for this domain based on profile.

        Returns: 'curl_cffi' | 'playwright' | 'httpx' | 'any'
        """
        if self.js_rendering_needed or self.needs_js_render:
            return "playwright"
        if self.waf_type in ("cloudflare", "datadome", "imperva"):
            return "curl_cffi"
        if self.waf_type == "akamai":
            return "curl_cffi"
        if self.ja3_randomize:
            return "curl_cffi"
        return "any"

    @property
    def recommended_stealth_headers(self) -> dict[str, str]:
        """Build recommended stealth headers based on profile.

        Returns empty dict for unprofiled domains.
        """
        headers: dict[str, str] = {}
        if "Accept" in self.required_headers:
            headers.setdefault("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
        if "Accept-Language" in self.required_headers:
            headers.setdefault("Accept-Language", "en-US,en;q=0.9")
        if "Accept-Encoding" in self.required_headers:
            headers.setdefault("Accept-Encoding", "gzip, deflate, br")
        if "Referer" in self.required_headers:
            headers.setdefault("Referer", "https://www.google.com/")
        if "Sec-Fetch-Site" in self.required_headers:
            headers.setdefault("Sec-Fetch-Site", "none")
        return headers

    @classmethod
    def empty(cls, domain: str) -> "AntiBotProfile":
        """Factory for unknown domains — no protection detected."""
        return cls(domain=domain)


# ---------------------------------------------------------------------------
# In-memory cache
# ---------------------------------------------------------------------------

class _ProfileCache:
    """TTL-bounded LRU cache for anti-bot profiles. M1 8GB: 256 entries max."""

    __slots__ = ("_data", "_max_entries", "_ttl_s")

    def __init__(self, max_entries: int = 256, ttl_s: float = 600.0) -> None:
        self._data: dict[str, tuple[float, AntiBotProfile]] = {}
        self._max_entries = max_entries
        self._ttl_s = ttl_s

    def get(self, domain: str) -> AntiBotProfile | None:
        entry = self._data.get(domain)
        if entry is None:
            return None
        insert_ts, profile = entry
        if _time.monotonic() - insert_ts > self._ttl_s:
            del self._data[domain]
            return None
        return profile

    def put(self, profile: AntiBotProfile) -> None:
        self._data[profile.domain] = (_time.monotonic(), profile)
        if len(self._data) > self._max_entries:
            oldest = min(self._data, key=lambda k: self._data[k][0])
            del self._data[oldest]

    def invalidate(self, domain: str) -> None:
        self._data.pop(domain, None)

    def clear(self) -> None:
        self._data.clear()


# ---------------------------------------------------------------------------
# AntiBotProfileService
# ---------------------------------------------------------------------------

class AntiBotProfileService:
    """Async service for anti-bot profile CRUD with intelligent bypass recommendations.

    Primary path: DuckDB-shadow-store-backed, shared across sprints.
    Fallback: empty profile (no protection) when persistence disabled.

    Merging strategy: new observations are merged into existing profiles
    via confidence-weighted EMA. Each new challenge detection increases
    confidence; each successful bypass also increases confidence.
    """

    __slots__ = ("_store", "_enabled", "_cache", "_max_rows", "_evict_lock")

    def __init__(
        self,
        store: DuckDBShadowStore | None = None,
        *,
        max_rows: int | None = None,
    ) -> None:
        """Initialize AntiBotProfileService.

        Args:
            store: DuckDBShadowStore instance for persistence.
            max_rows: Max rows before LRU eviction (default 5000).
        """
        self._store: DuckDBShadowStore | None = store
        self._enabled: bool = _ANTI_BOT_PROFILES_ENABLED and store is not None
        self._max_rows: int = max_rows if max_rows is not None else _ANTI_BOT_PROFILES_MAX_ROWS
        self._cache: _ProfileCache = _ProfileCache(
            max_entries=_PROFILE_CACHE_MAX,
            ttl_s=_PROFILE_CACHE_TTL_S,
        )
        self._evict_lock: threading.Lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_profile(self, domain: str) -> AntiBotProfile:
        """Get anti-bot profile for a domain. Returns empty profile if unknown.

        Never raises.
        """
        if not domain:
            return AntiBotProfile.empty(domain)

        # In-memory cache first
        cached = self._cache.get(domain)
        if cached is not None:
            return cached

        # DuckDB path
        if self._enabled and self._store is not None:
            try:
                profile = await self._get_from_duckdb(domain)
                if profile is not None:
                    self._cache.put(profile)
                    return profile
            except Exception:  # noqa: BLE001 — fail-safe
                pass

        return AntiBotProfile.empty(domain)

    async def observe_challenge(
        self,
        domain: str,
        *,
        waf_type: str = "",
        challenge_type: str = "",
        block_pattern: str = "",
        status_code: int = 0,
    ) -> None:
        """Record an anti-bot challenge observation for a domain.

        Args:
            domain: Normalized domain.
            waf_type: Detected WAF type (cloudflare/akamai/datadome/etc).
            challenge_type: Specific challenge (js/captcha/turnstile/403/429).
            block_pattern: Detected block pattern keyword in HTML.
            status_code: HTTP status code of the challenge response.

        Never raises.
        """
        if not domain:
            return
        try:
            current = await self.get_profile(domain)
            merged = self._merge_observation(
                current=current,
                waf_type=waf_type,
                challenge_type=challenge_type,
                block_pattern=block_pattern,
                success=False,
            )
            # Auto-determine stealth level from accumulated challenges
            merged = self._determine_stealth_level(merged)
            await self._persist(merged)
        except Exception:  # noqa: BLE001 — fail-safe
            pass

    async def observe_bypass(
        self,
        domain: str,
        *,
        bypass_strategy: str = "",
        used_js: bool = False,
        used_residential: bool = False,
        used_ja3_randomize: bool = False,
    ) -> None:
        """Record a successful anti-bot bypass for a domain.

        Args:
            domain: Normalized domain.
            bypass_strategy: Strategy that worked (curl_cffi/js_render/stealth_headers).
            used_js: Whether JS rendering was used.
            used_residential: Whether residential proxy was used.
            used_ja3_randomize: Whether JA3 fingerprint randomization was used.

        Never raises.
        """
        if not domain:
            return
        try:
            current = await self.get_profile(domain)
            now = _time.time()

            # Update bypass strategy with confidence boost
            new_strategy = bypass_strategy or current.bypass_strategy
            new_confidence = min(0.95, current.confidence * 0.8 + 0.3)

            merged = AntiBotProfile(
                domain=current.domain,
                waf_type=current.waf_type,
                challenge_types=current.challenge_types,
                bypass_strategy=new_strategy,
                required_headers=current.required_headers,
                required_cookies=current.required_cookies,
                js_rendering_needed=current.js_rendering_needed or used_js,
                residential_proxy_needed=current.residential_proxy_needed or used_residential,
                stealth_level=current.stealth_level,
                ja3_randomize=current.ja3_randomize or used_ja3_randomize,
                block_patterns=current.block_patterns,
                confidence=round(new_confidence, 3),
                observation_count=current.observation_count + 1,
                last_challenge_seen=current.last_challenge_seen,
                last_bypass_success=now,
                first_seen=current.first_seen if current.first_seen > 0 else now,
            )
            merged = self._determine_stealth_level(merged)
            await self._persist(merged)
        except Exception:  # noqa: BLE001 — fail-safe
            pass

    async def get_bypass_recommendation(
        self,
        domain: str,
    ) -> dict[str, Any]:
        """Get actionable bypass recommendation for a domain.

        Returns a dict with:
            - strategy: recommended bypass strategy
            - transport: recommended transport
            - needs_js: whether JS rendering is needed
            - needs_residential: whether residential proxy needed
            - stealth_level: recommended stealth level
            - ja3_randomize: whether to randomize TLS fingerprint
            - headers: recommended stealth headers dict
            - confidence: confidence in this recommendation (0.0-1.0)

        Never raises — returns neutral recommendation on any error.
        """
        try:
            profile = await self.get_profile(domain)
            return {
                "strategy": profile.bypass_strategy,
                "transport": profile.recommended_transport,
                "needs_js": profile.needs_js_render,
                "needs_residential": profile.needs_residential_proxy,
                "stealth_level": profile.stealth_level,
                "ja3_randomize": profile.ja3_randomize,
                "headers": profile.recommended_stealth_headers,
                "confidence": profile.confidence,
            }
        except Exception:  # noqa: BLE001 — fail-safe
            return {
                "strategy": "none",
                "transport": "any",
                "needs_js": False,
                "needs_residential": False,
                "stealth_level": "none",
                "ja3_randomize": False,
                "headers": {},
                "confidence": 0.0,
            }

    # ------------------------------------------------------------------
    # Observation merging
    # ------------------------------------------------------------------

    def _merge_observation(
        self,
        *,
        current: AntiBotProfile,
        waf_type: str = "",
        challenge_type: str = "",
        block_pattern: str = "",
        success: bool = False,
    ) -> AntiBotProfile:
        """Merge a new observation into the existing profile via EMA."""
        now = _time.time()

        # WAF type: keep existing unless new one is more specific
        new_waf = current.waf_type
        if waf_type and waf_type != "none":
            if current.waf_type == "none" or current.confidence < 0.5:
                new_waf = waf_type

        # Challenge types: accumulate unique
        challenge_set = set(current.challenge_types)
        if challenge_type:
            challenge_set.add(challenge_type)

        # Block patterns: accumulate unique
        pattern_set = set(current.block_patterns)
        if block_pattern:
            pattern_set.add(block_pattern)

        # Confidence: EMA update
        new_confidence = current.confidence * (1 - _CONFIDENCE_ALPHA) + _CONFIDENCE_ALPHA

        return AntiBotProfile(
            domain=current.domain,
            waf_type=new_waf,
            challenge_types=tuple(sorted(challenge_set)),
            bypass_strategy=current.bypass_strategy,
            required_headers=current.required_headers,
            required_cookies=current.required_cookies,
            js_rendering_needed=current.js_rendering_needed,
            residential_proxy_needed=current.residential_proxy_needed,
            stealth_level=current.stealth_level,
            ja3_randomize=current.ja3_randomize,
            block_patterns=tuple(sorted(pattern_set)),
            confidence=round(new_confidence, 3),
            observation_count=current.observation_count + 1,
            last_challenge_seen=now if not success else current.last_challenge_seen,
            last_bypass_success=current.last_bypass_success,
            first_seen=current.first_seen if current.first_seen > 0 else now,
        )

    def _determine_stealth_level(self, profile: AntiBotProfile) -> AntiBotProfile:
        """Auto-determine stealth level from accumulated profile data.

        Rules (evaluated in order, first match wins):
        1. JS rendering needed → 'js_render'
        2. Turnstile challenge + Cloudflare → 'aggressive'
        3. CAPTCHA challenge → 'aggressive'
        4. JS challenge (non-Turnstile) → 'standard'
        5. 403/429 responses → 'standard'
        6. Residential proxy needed → 'standard'
        7. Otherwise → 'none'
        """
        challenges = set(profile.challenge_types)

        if profile.js_rendering_needed or "turnstile" in challenges:
            level = "js_render"
        elif "captcha" in challenges:
            level = "aggressive"
        elif "js" in challenges or ("403" in challenges or "429" in challenges):
            level = "standard"
        elif profile.residential_proxy_needed:
            level = "standard"
        else:
            level = profile.stealth_level or "none"

        # Don't downgrade — only upgrade or keep
        level_order = {"none": 0, "standard": 1, "aggressive": 2, "js_render": 3}
        new_level = level if level_order.get(level, 0) >= level_order.get(profile.stealth_level, 0) else profile.stealth_level

        return AntiBotProfile(
            domain=profile.domain,
            waf_type=profile.waf_type,
            challenge_types=profile.challenge_types,
            bypass_strategy=self._derive_bypass_strategy(profile, new_level),
            required_headers=profile.required_headers,
            required_cookies=profile.required_cookies,
            js_rendering_needed=profile.js_rendering_needed,
            residential_proxy_needed=profile.residential_proxy_needed,
            stealth_level=new_level,
            ja3_randomize=new_level in ("standard", "aggressive", "js_render"),
            block_patterns=profile.block_patterns,
            confidence=profile.confidence,
            observation_count=profile.observation_count,
            last_challenge_seen=profile.last_challenge_seen,
            last_bypass_success=profile.last_bypass_success,
            first_seen=profile.first_seen,
        )

    @staticmethod
    def _derive_bypass_strategy(profile: AntiBotProfile, stealth_level: str) -> str:
        """Derive the best bypass strategy from profile data."""
        if stealth_level == "js_render":
            return "js_render"
        if stealth_level == "aggressive":
            return "residential_proxy" if profile.residential_proxy_needed else "curl_cffi"
        if stealth_level == "standard":
            if profile.waf_type in ("cloudflare", "datadome"):
                return "curl_cffi"
            return "stealth_headers"
        return profile.bypass_strategy or "none"

    # ------------------------------------------------------------------
    # DuckDB persistence
    # ------------------------------------------------------------------

    async def _get_from_duckdb(self, domain: str) -> AntiBotProfile | None:
        """Query anti-bot profile from DuckDB."""
        if self._store is None:
            return None

        loop = asyncio.get_running_loop()

        def _sync() -> AntiBotProfile | None:
            try:
                self._store.ensure_connected()  # type: ignore[union-attr]
                conn = (
                    self._store._file_conn  # type: ignore[union-attr] # noqa: SLF001
                    if self._store._db_path  # type: ignore[union-attr] # noqa: SLF001
                    else self._store._persistent_conn  # type: ignore[union-attr] # noqa: SLF001
                )
                if conn is None:
                    return None
                r = conn.execute(
                    "SELECT domain, waf_type, challenge_types, bypass_strategy, "
                    "required_headers, required_cookies, js_rendering_needed, "
                    "residential_proxy_needed, stealth_level, ja3_randomize, "
                    "block_patterns, confidence, observation_count, "
                    "COALESCE(epoch_ms(last_challenge_seen)/1000.0, 0), "
                    "COALESCE(epoch_ms(last_bypass_success)/1000.0, 0), "
                    "COALESCE(epoch_ms(first_seen)/1000.0, 0) "
                    "FROM anti_bot_profiles WHERE domain = ?",
                    [domain],
                ).fetchone()
                if r is None:
                    return None
                return AntiBotProfile(
                    domain=str(r[0]),
                    waf_type=str(r[1]),
                    challenge_types=self._parse_json_tuple(str(r[2])),
                    bypass_strategy=str(r[3]),
                    required_headers=self._parse_json_tuple(str(r[4])),
                    required_cookies=self._parse_json_tuple(str(r[5])),
                    js_rendering_needed=bool(r[6]),
                    residential_proxy_needed=bool(r[7]),
                    stealth_level=str(r[8]),
                    ja3_randomize=bool(r[9]),
                    block_patterns=tuple(str(r[10]).split(",")) if r[10] else (),
                    confidence=float(r[11]),
                    observation_count=int(r[12]),
                    last_challenge_seen=float(r[13]) if r[13] else 0.0,
                    last_bypass_success=float(r[14]) if r[14] else 0.0,
                    first_seen=float(r[15]) if r[15] else 0.0,
                )
            except Exception:  # noqa: BLE001 — fail-safe
                return None

        return await loop.run_in_executor(
            self._store._shared_executor,  # type: ignore[union-attr] # noqa: SLF001
            _sync,
        )

    async def _persist(self, profile: AntiBotProfile) -> None:
        """Persist anti-bot profile to DuckDB with LRU eviction."""
        self._cache.put(profile)

        if not self._enabled or self._store is None:
            return

        loop = asyncio.get_running_loop()

        def _sync_upsert() -> None:
            try:
                self._store.ensure_connected()  # type: ignore[union-attr]
                conn = (
                    self._store._file_conn  # type: ignore[union-attr] # noqa: SLF001
                    if self._store._db_path  # type: ignore[union-attr] # noqa: SLF001
                    else self._store._persistent_conn  # type: ignore[union-attr] # noqa: SLF001
                )
                if conn is None:
                    return

                self._store.ensure_anti_bot_profiles_schema()  # type: ignore[union-attr]

                last_challenge = (
                    _dt.datetime.fromtimestamp(profile.last_challenge_seen, tz=_dt.timezone.utc).isoformat()
                    if profile.last_challenge_seen > 0
                    else None
                )
                last_bypass = (
                    _dt.datetime.fromtimestamp(profile.last_bypass_success, tz=_dt.timezone.utc).isoformat()
                    if profile.last_bypass_success > 0
                    else None
                )
                block_patterns_str = ",".join(profile.block_patterns) if profile.block_patterns else ""

                conn.execute(
                    "INSERT INTO anti_bot_profiles "
                    "(domain, waf_type, challenge_types, bypass_strategy, "
                    "required_headers, required_cookies, js_rendering_needed, "
                    "residential_proxy_needed, stealth_level, ja3_randomize, "
                    "block_patterns, confidence, observation_count, "
                    "last_challenge_seen, last_bypass_success, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP) "
                    "ON CONFLICT(domain) DO UPDATE SET "
                    "waf_type = excluded.waf_type, "
                    "challenge_types = excluded.challenge_types, "
                    "bypass_strategy = excluded.bypass_strategy, "
                    "required_headers = excluded.required_headers, "
                    "required_cookies = excluded.required_cookies, "
                    "js_rendering_needed = excluded.js_rendering_needed, "
                    "residential_proxy_needed = excluded.residential_proxy_needed, "
                    "stealth_level = excluded.stealth_level, "
                    "ja3_randomize = excluded.ja3_randomize, "
                    "block_patterns = excluded.block_patterns, "
                    "confidence = excluded.confidence, "
                    "observation_count = excluded.observation_count, "
                    "last_challenge_seen = COALESCE(excluded.last_challenge_seen, anti_bot_profiles.last_challenge_seen), "
                    "last_bypass_success = COALESCE(excluded.last_bypass_success, anti_bot_profiles.last_bypass_success), "
                    "updated_at = excluded.updated_at",
                    [
                        profile.domain,
                        profile.waf_type,
                        _json.dumps(list(profile.challenge_types)),
                        profile.bypass_strategy,
                        _json.dumps(list(profile.required_headers)),
                        _json.dumps(list(profile.required_cookies)),
                        profile.js_rendering_needed,
                        profile.residential_proxy_needed,
                        profile.stealth_level,
                        profile.ja3_randomize,
                        block_patterns_str,
                        profile.confidence,
                        profile.observation_count,
                        last_challenge,
                        last_bypass,
                    ],
                )

                # LRU eviction — thread-safe via threading.Lock
                with self._evict_lock:
                    count_result = conn.execute(
                        "SELECT COUNT(*) FROM anti_bot_profiles"
                    ).fetchone()
                    if count_result and count_result[0] > self._max_rows:
                        excess = count_result[0] - self._max_rows
                        conn.execute(
                            "DELETE FROM anti_bot_profiles WHERE domain IN ("
                            "SELECT domain FROM anti_bot_profiles "
                            "ORDER BY updated_at ASC LIMIT ?"
                            ")",
                            [excess],
                        )
            except Exception:  # noqa: BLE001 — fail-safe
                pass

        await loop.run_in_executor(
            self._store._shared_executor,  # type: ignore[union-attr] # noqa: SLF001
            _sync_upsert,
        )

    @staticmethod
    def _parse_json_tuple(raw: str) -> tuple[str, ...]:
        """Parse JSON array string to tuple. Fail-safe."""
        if not raw or raw == "[]":
            return ()
        try:
            parsed = _json.loads(raw)
            if isinstance(parsed, list):
                return tuple(str(x) for x in parsed)
            return ()
        except Exception:
            return ()


# ---------------------------------------------------------------------------
# Singleton factory (F320: Refactored to use centralized pattern)
# ---------------------------------------------------------------------------
# F320: Refactored to use centralized singleton pattern
from hledac.universal.utils._patterns import module_singleton_getter
from core import aclose


def _make_anti_bot_service(store: DuckDBShadowStore | None) -> AntiBotProfileService:
    """Factory for AntiBotProfileService singleton."""
    return AntiBotProfileService(store=store)


# Module-level singleton getter with thread-safe double-checked locking
_get_anti_bot_service = module_singleton_getter(
    singleton_name="_anti_bot_profile_singleton",
    factory=lambda: _make_anti_bot_service(None),
)


def get_anti_bot_profile_service(
    store: DuckDBShadowStore | None = None,
) -> AntiBotProfileService:
    """Get or create the module-level AntiBotProfileService singleton.

    Args:
        store: DuckDBShadowStore for persistence. Only used on first call.
    """
    return _get_anti_bot_service()


def reset_anti_bot_profile_service() -> None:
    """Reset singleton — test seam only."""
    global _anti_bot_profile_singleton
    _anti_bot_profile_singleton = None


__all__ = [
    "AntiBotProfile",
    "AntiBotProfileService",
    "get_anti_bot_profile_service",
    "reset_anti_bot_profile_service",
]
