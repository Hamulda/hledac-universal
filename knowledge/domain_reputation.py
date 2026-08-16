"""DomainReputationService — persistent cross-sprint domain reputation store.

UNIFIED-007 + UNIFIED-008: Replaces per-sprint relearning of domain hostility
with a cumulative, cross-sprint reputation store backed by DuckDB.




Key features:
- tarpit_score: cumulative 0.0→1.0 score from TarpitDetector across sprints
- proxy_affinity: JSON arrays of successful/failed proxy strings per domain
- anti_bot_type: detected WAF/protection type (cloudflare/akamai/datadome/none)
- challenge_type: specific challenge (js/captcha/turnstile/none)
- success_rate: cumulative success/total ratio for pre-fetch routing decisions

M1 8GB safety:
- Bounded table via HLEDAC_DOMAIN_REPUTATION_MAX_ROWS (default 5000)
- LRU eviction (oldest last_seen) on insert when threshold exceeded
- No new thread pools — all DB ops via DuckDBShadowStore's _write_executor
- msgspec.Struct(frozen=True, gc=False) for zero-alloc hot-path reads

Feature flag: HLEDAC_DOMAIN_REPUTATION=1 (default ON). Set to 0 to disable
persistence (in-memory fallback with TTL).
"""
from __future__ import annotations

import asyncio
import os
import threading
import time as _time
from typing import TYPE_CHECKING, Any

import msgspec
from compat.msgspec_gc_compat import Struct
from hledac.universal.compat.msgspec_gc_compat import Struct

from hledac.universal.utils.logging_config import get_logger

if TYPE_CHECKING:
    from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------
_DOMAIN_REPUTATION_ENABLED: bool = os.getenv("HLEDAC_DOMAIN_REPUTATION", "1") != "0"
_DOMAIN_REPUTATION_MAX_ROWS: int = int(
    os.getenv("HLEDAC_DOMAIN_REPUTATION_MAX_ROWS", "5000")
    )
# In-memory fallback TTL when persistence disabled (seconds)
_DOMAIN_REPUTATION_MEMORY_TTL_S: float = 3600.0  # 1 hour
# Max in-memory entries when persistence disabled
_DOMAIN_REPUTATION_MEMORY_MAX: int = 512


# ---------------------------------------------------------------------------
# DTO
# ---------------------------------------------------------------------------

class DomainReputation(Struct, frozen=True):
    """Immutable domain reputation snapshot from DuckDB.

    gc=False for M1 8GB — avoids GC overhead on hot-path lookup.
    """

    domain: str
    tarpit_score: float = 0.0
    successful_proxies: tuple[str, ...] = ()
    failed_proxies: tuple[str, ...] = ()
    anti_bot_type: str = "none"
    challenge_type: str = "none"
    success_rate: float = 1.0
    total_attempts: int = 0
    successful_attempts: int = 0
    last_seen: float = 0.0  # epoch seconds
    # ISSUE [ADVERSARY]-002: Cognitive tarpit score from LLM-honeypot detection
    cognitive_tarpit_score: float = 0.0
    cognitive_tarpit_reasons: str = ""

    @classmethod
    def empty(cls, domain: str) -> DomainReputation:
        """Factory for unknown domains — neutral reputation."""
        return cls(domain=domain)

    @property
    def is_cognitive_tarpit(self) -> bool:
        """True if cognitive tarpit score indicates LLM-generated honeypot."""
        return self.cognitive_tarpit_score >= 0.7

    @property
    def is_tarpit(self) -> bool:
        """True if cumulative tarpit_score exceeds abort threshold."""
        return self.tarpit_score > 0.7

    @property
    def is_hostile(self) -> bool:
        """True if domain is known tarpit or has very low success_rate."""
        return self.is_tarpit or (
            self.total_attempts >= 5 and self.success_rate < 0.3
    )

    @property
    def preferred_proxy(self) -> str | None:
        """First successful proxy, or None if none recorded."""
        return self.successful_proxies[0] if self.successful_proxies else None

    @property
    def anti_bot_proxy_hint(self) -> str:
        """Return proxy strategy hint based on anti_bot_type.

        Returns: 'residential' | 'cloudflare_bypass' | 'none'
        """
        if self.anti_bot_type in ("cloudflare", "akamai"):
            return "cloudflare_bypass"
        if self.anti_bot_type in ("datadome", "imperva"):
            return "residential"
        return "none"


# ---------------------------------------------------------------------------
# In-memory fallback (when HLEDAC_DOMAIN_REPUTATION=0)
# ---------------------------------------------------------------------------

class _MemoryReputationStore:
    """TTL-bounded LRU in-memory fallback when persistence is disabled."""

    __slots__ = ("_data", "_max_entries", "_ttl_s")
    _data: dict[str, tuple[float, DomainReputation]]  # domain -> (insert_ts, rep)

    def __init__(self, max_entries: int = 512, ttl_s: float = 3600.0) -> None:
        self._data = {}
        self._max_entries = max_entries
        self._ttl_s = ttl_s

    def get(self, domain: str) -> DomainReputation | None:
        entry = self._data.get(domain)
        if entry is None:
            return None
        insert_ts, rep = entry
        if _time.monotonic() - insert_ts > self._ttl_s:
            del self._data[domain]
            return None
        return rep

    def put(self, rep: DomainReputation) -> None:
        self._data[rep.domain] = (_time.monotonic(), rep)
        if len(self._data) > self._max_entries:
            oldest = min(self._data, key=lambda k: self._data[k][0])
            del self._data[oldest]

    def clear(self) -> None:
        self._data.clear()


# ---------------------------------------------------------------------------
# DomainReputationService
# ---------------------------------------------------------------------------

class DomainReputationService:
    """Async service for domain reputation CRUD with DuckDB persistence.

    Primary path: DuckDB-shadow-store-backed, shared across sprints.
    Fallback: TTL-bounded in-memory dict when persistence is disabled.

    Fail-safe: all errors return empty/neutral reputation, never raise.
    """

    __slots__ = ("_store", "_enabled", "_memory_fallback", "_max_rows", "_evict_lock")

    def __init__(
        self,
        store: DuckDBShadowStore | None = None,
        *,
        max_rows: int | None = None,
    ) -> None:
        """Initialize DomainReputationService.

        Args:
            store: DuckDBShadowStore instance for persistence.
                   None = in-memory fallback only.
            max_rows: Max rows in DuckDB table before LRU eviction.
                      Default: HLEDAC_DOMAIN_REPUTATION_MAX_ROWS (5000).
        """
        self._store: DuckDBShadowStore | None = store
        self._enabled: bool = _DOMAIN_REPUTATION_ENABLED and store is not None
        self._max_rows: int = max_rows if max_rows is not None else _DOMAIN_REPUTATION_MAX_ROWS
        self._memory_fallback: _MemoryReputationStore = _MemoryReputationStore(
            max_entries=_DOMAIN_REPUTATION_MEMORY_MAX,
            ttl_s=_DOMAIN_REPUTATION_MEMORY_TTL_S,
    )
        self._evict_lock: threading.Lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get(self, domain: str) -> DomainReputation:
        """Get domain reputation, or neutral default if unknown.

        Never raises — returns DomainReputation.empty(domain) on any error.
        """
        if not domain:
            return DomainReputation.empty(domain)

        # In-memory check (always hit first for speed)
        mem_rep = self._memory_fallback.get(domain)
        if mem_rep is not None:
            return mem_rep

        # DuckDB path
        if self._enabled and self._store is not None:
            try:
                rep = await self._get_from_duckdb(domain)
                if rep is not None:
                    # Cache in memory for hot-path reuse
                    self._memory_fallback.put(rep)
                    return rep
            except Exception:  # noqa: BLE001 — fail-safe; DB operation failure; non-critical
                pass

        return DomainReputation.empty(domain)

    async def record_success(
        self,
        domain: str,
        proxy: str = "",
        *,
        anti_bot_type: str = "none",
        challenge_type: str = "none",
    ) -> None:
        """Record a successful fetch for the domain.

        Args:
            domain: Normalized domain (e.g. "example.com").
            proxy: Proxy:port string used for the successful fetch.
            anti_bot_type: Detected WAF type (optional).
            challenge_type: Detected challenge type (optional).

        Never raises.
        """
        if not domain:
            return

        try:
            current = await self.get(domain)
            new_rep = self._compute_updated_reputation(
                current=current,
                success=True,
                proxy=proxy,
                tarpit_score=current.tarpit_score,
                anti_bot_type=anti_bot_type or current.anti_bot_type,
                challenge_type=challenge_type or current.challenge_type,
                # ISSUE [ADVERSARY]-002: preserve cognitive_tarpit_score on success
                cognitive_tarpit_score=current.cognitive_tarpit_score,
                cognitive_tarpit_reasons=current.cognitive_tarpit_reasons,
    )
            await self._persist(new_rep)
        except Exception:  # noqa: BLE001 — fail-safe; non-critical
            pass

    async def record_failure(
        self,
        domain: str,
        proxy: str = "",
        *,
        tarpit_score: float = 0.0,
        anti_bot_type: str = "",
        challenge_type: str = "",
    ) -> None:
        """Record a failed fetch for the domain.

        Args:
            domain: Normalized domain.
            proxy: Proxy:port string used (that failed).
            tarpit_score: Tarpit score from TarpitDetector (0.0-1.0).
            anti_bot_type: Detected WAF type from headers/HTML.
            challenge_type: Detected challenge type.

        Never raises.
        """
        if not domain:
            return

        try:
            current = await self.get(domain)
            # Exponential moving average for tarpit_score
            cumulative_tarpit = current.tarpit_score * 0.7 + tarpit_score * 0.3
            # ISSUE [ADVERSARY]-002: Handle cognitive_tarpit_score
            # When public_fetcher detects cognitive tarpit, it calls record_failure
            # with tarpit_score=1.0. We propagate this to cognitive_tarpit_score.
            new_ct_score: float | None = None
            new_ct_reasons: str | None = None
            if tarpit_score >= 1.0:
                new_ct_score = tarpit_score
                new_ct_reasons = f"cognitive_tarpit_score={tarpit_score:.3f}"
            new_rep = self._compute_updated_reputation(
                current=current,
                success=False,
                proxy=proxy,
                tarpit_score=max(current.tarpit_score, cumulative_tarpit),
                anti_bot_type=anti_bot_type or current.anti_bot_type,
                challenge_type=challenge_type or current.challenge_type,
                cognitive_tarpit_score=new_ct_score,
                cognitive_tarpit_reasons=new_ct_reasons,
    )
            await self._persist(new_rep)
        except Exception:  # noqa: BLE001 — fail-safe; non-critical
            pass

    async def get_proxy_hint(self, domain: str) -> str:
        """Get proxy strategy recommendation for a domain.

        Returns one of: 'cloudflare_bypass' | 'residential' | 'none'
        Never raises — returns 'none' on any error.
        """
        try:
            rep = await self.get(domain)
            if rep.is_hostile and rep.preferred_proxy:
                return rep.anti_bot_proxy_hint
            if rep.anti_bot_type != "none":
                return rep.anti_bot_proxy_hint
            return "none"
        except Exception:  # noqa: BLE001 — fail-safe
            return "none"

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _compute_updated_reputation(
        self,
        *,
        current: DomainReputation,
        success: bool,
        proxy: str = "",
        tarpit_score: float = 0.0,
        anti_bot_type: str = "none",
        challenge_type: str = "none",
        cognitive_tarpit_score: float | None = None,
        cognitive_tarpit_reasons: str | None = None,
    ) -> DomainReputation:
        """Compute updated reputation record from current state + new event."""
        total = current.total_attempts + 1
        successful = current.successful_attempts + (1 if success else 0)
        new_rate = successful / total if total > 0 else 1.0

        # Proxy affinity: maintain successful and failed lists
        succ_proxies = list(current.successful_proxies)
        fail_proxies = list(current.failed_proxies)
        if proxy:
            if success:
                if proxy not in succ_proxies:
                    succ_proxies.insert(0, proxy)  # prepend for recency
                    succ_proxies = succ_proxies[:5]  # cap at 5
                if proxy in fail_proxies:
                    fail_proxies.remove(proxy)
            else:
                if proxy not in fail_proxies:
                    fail_proxies.insert(0, proxy)
                    fail_proxies = fail_proxies[:5]
                if proxy in succ_proxies:
                    succ_proxies.remove(proxy)

        # ISSUE [ADVERSARY]-002: Cognitive tarpit score is monotonically increasing
        # (once detected as LLM honeypot, stays marked)
        new_ct_score = current.cognitive_tarpit_score
        new_ct_reasons = current.cognitive_tarpit_reasons
        if cognitive_tarpit_score is not None and cognitive_tarpit_score > new_ct_score:
            new_ct_score = cognitive_tarpit_score
            new_ct_reasons = cognitive_tarpit_reasons or ""

        now = _time.time()

        return DomainReputation(
            domain=current.domain,
            tarpit_score=round(tarpit_score, 3),
            successful_proxies=tuple(succ_proxies),
            failed_proxies=tuple(fail_proxies),
            anti_bot_type=anti_bot_type or current.anti_bot_type,
            challenge_type=challenge_type or current.challenge_type,
            success_rate=round(new_rate, 4),
            total_attempts=total,
            successful_attempts=successful,
            last_seen=now,
            cognitive_tarpit_score=round(new_ct_score, 3),
            cognitive_tarpit_reasons=new_ct_reasons,
    )

    async def _get_from_duckdb(self, domain: str) -> DomainReputation | None:
        """Synchronous DuckDB query run on write executor."""
        if self._store is None:
            return None

        loop = asyncio.get_running_loop()

        def _sync_query() -> DomainReputation | None:
            try:
                self._store.ensure_connected()
                conn = self._store._file_conn if self._store._db_path else self._store._persistent_conn  # noqa: SLF001
                if conn is None:
                    return None
                result = conn.execute(
                    "SELECT domain, tarpit_score, successful_proxies, failed_proxies, "
                    "anti_bot_type, challenge_type, success_rate, total_attempts, "
                    "successful_attempts, "
                    "epoch_ms(last_seen) / 1000.0, "
                    "COALESCE(cognitive_tarpit_score, 0.0), "
                    "COALESCE(cognitive_tarpit_reasons, '')"
                    "FROM domain_reputation WHERE domain = ?",
                    [domain],
                ).fetchone()
                if result is None:
                    return None
                return DomainReputation(
                    domain=str(result[0]),
                    tarpit_score=float(result[1]),
                    successful_proxies=self._parse_json_list(result[2]),
                    failed_proxies=self._parse_json_list(result[3]),
                    anti_bot_type=str(result[4]),
                    challenge_type=str(result[5]),
                    success_rate=float(result[6]),
                    total_attempts=int(result[7]),
                    successful_attempts=int(result[8]),
                    last_seen=float(result[9]) if result[9] is not None else 0.0,
                    cognitive_tarpit_score=float(result[10]) if result[10] is not None else 0.0,
                    cognitive_tarpit_reasons=str(result[11]) if result[11] is not None else "",
    )
            except Exception:  # noqa: BLE001 — fail-safe; DB query failure; non-critical
                return None

        return await loop.run_in_executor(
            self._store._shared_executor,  # noqa: SLF001
            _sync_query,
    )

    async def _persist(self, rep: DomainReputation) -> None:
        """Persist domain reputation to DuckDB, with LRU eviction if needed."""
        # Always update in-memory cache
        self._memory_fallback.put(rep)

        if not self._enabled or self._store is None:
            return

        loop = asyncio.get_running_loop()

        def _sync_upsert() -> None:
            try:
                self._store.ensure_connected()
                conn = self._store._file_conn if self._store._db_path else self._store._persistent_conn  # noqa: SLF001
                if conn is None:
                    return

                # Ensure table exists (idempotent)
                self._store.ensure_domain_reputation_schema()

                conn.execute(
                    "INSERT INTO domain_reputation "
                    "(domain, tarpit_score, successful_proxies, failed_proxies, "
                    "anti_bot_type, challenge_type, success_rate, total_attempts, "
                    "successful_attempts, last_seen, updated_at, "
                    "cognitive_tarpit_score, cognitive_tarpit_reasons) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?, ?) "
                    "ON CONFLICT(domain) DO UPDATE SET "
                    "tarpit_score = excluded.tarpit_score, "
                    "successful_proxies = excluded.successful_proxies, "
                    "failed_proxies = excluded.failed_proxies, "
                    "anti_bot_type = excluded.anti_bot_type, "
                    "challenge_type = excluded.challenge_type, "
                    "success_rate = excluded.success_rate, "
                    "total_attempts = excluded.total_attempts, "
                    "successful_attempts = excluded.successful_attempts, "
                    "last_seen = excluded.last_seen, "
                    "updated_at = excluded.updated_at, "
                    "cognitive_tarpit_score = excluded.cognitive_tarpit_score, "
                    "cognitive_tarpit_reasons = excluded.cognitive_tarpit_reasons",
                    [
                        rep.domain,
                        rep.tarpit_score,
                        self._serialize_json_list(rep.successful_proxies),
                        self._serialize_json_list(rep.failed_proxies),
                        rep.anti_bot_type,
                        rep.challenge_type,
                        rep.success_rate,
                        rep.total_attempts,
                        rep.successful_attempts,
                        rep.cognitive_tarpit_score,
                        rep.cognitive_tarpit_reasons,
                    ],
    )

                # LRU eviction: remove oldest entries if over max_rows
                with self._evict_lock:
                    count_result = conn.execute(
                        "SELECT COUNT(*) FROM domain_reputation"
                    ).fetchone()
                    if count_result and count_result[0] > self._max_rows:
                        excess = count_result[0] - self._max_rows
                        conn.execute(
                            "DELETE FROM domain_reputation WHERE domain IN ("
                            "SELECT domain FROM domain_reputation "
                            "ORDER BY last_seen ASC LIMIT ?"
                            ")",
                            [excess],
    )
            except Exception:  # noqa: BLE001 — fail-safe; DB write failure; non-critical
                pass

        await loop.run_in_executor(
            self._store._shared_executor,  # noqa: SLF001
            _sync_upsert,
    )

    @staticmethod
    def _parse_json_list(raw: str | None) -> tuple[str, ...]:
        """Parse JSON array string to tuple of strings. Fail-safe."""
        if not raw:
            return ()
        try:
            import json as _json
            parsed = _json.loads(raw)
            if isinstance(parsed, list):
                return tuple(str(x) for x in parsed)
            return ()
        except Exception:
            return ()

    @staticmethod
    def _serialize_json_list(items: tuple[str, ...]) -> str:
        """Serialize tuple of strings to JSON array string."""
        if not items:
            return "[]"
        try:
            import json as _json
            return _json.dumps(list(items))
        except Exception:
            return "[]"


# ---------------------------------------------------------------------------
# Singleton factory (F320: Refactored to use centralized pattern)
# ---------------------------------------------------------------------------
from hledac.universal.utils._patterns import module_singleton_getter
from _core import aclose


def _make_reputation_service(store: DuckDBShadowStore | None) -> DomainReputationService:
    """Factory for DomainReputationService singleton."""
    return DomainReputationService(store=store)


# Module-level singleton getter with thread-safe double-checked locking
_get_reputation_service = module_singleton_getter(
    singleton_name="_reputation_service_singleton",
    factory=lambda: _make_reputation_service(None),
    )


def get_domain_reputation_service(
    store: DuckDBShadowStore | None = None,
) -> DomainReputationService:
    """Get or create the module-level DomainReputationService singleton.

    Args:
        store: DuckDBShadowStore for persistence. Only used on first call.
               Subsequent calls ignore this arg.
    """
    return _get_reputation_service()


def reset_domain_reputation_service() -> None:
    """Reset singleton — test seam only."""
    global _reputation_service_singleton
    _reputation_service_singleton = None


__all__ = [
    "DomainReputation",
    "DomainReputationService",
    "get_domain_reputation_service",
    "reset_domain_reputation_service",
]
