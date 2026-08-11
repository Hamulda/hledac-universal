"""
security/clearance_cookie_jar.py

Cloudflare / DataDome clearance cookie persistence layer.


Stores cf_clearance and datadome cookies in LMDB with bounded TTL.
On subsequent requests, cookies are injected into the curl_cffi session
so protected endpoints are bypassed without solving the challenge again.

GHOST_INVARIANTS:
- I1: Always bounded — LMDB map_size capped, entries TTL-expired
- I2: Fail-soft — any error returns empty dict, never raises
- I3: Zero-copy reads — cursor.get() returns value without bytes() conversion
- I4: Lazy import — no LMDB / orjson at module load time

Cloudflare Turnstile cookies:
  cf_clearance — set after challenge solved, typically 30min-8h TTL
  cf_challenge_bypass — sometimes present alongside

DataDome cookies:
  datadome — long-lived fingerprint cookie, typically 1 year TTL

M1 8GB: 2 MB LMDB map (500 entries), FIFO eviction.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any

logger = logging.getLogger(__name__)

# --- Lazy imports (defer until first use) ---


def _open_lmdb_env() -> Any | None:
    """Open LMDB env lazily. Returns None if unavailable."""
    try:
        from hledac.universal.paths import LMDB_ROOT
        from hledac.universal.knowledge.lmdb_boot_guard import open_lmdb_with_guard

        lmdb_path = str(LMDB_ROOT / "clearance.lmdb")
        # POTENTIAL-1 Fix: critical=True for auth cookies (session data)
        # According to lmdb_boot_guard.py docs: "cookies, auth tokens MUST use critical=True
        # to avoid losing authentication state on crash (up to 5s of re-auth on every crash)."
        env = open_lmdb_with_guard(
            lmdb_path,
            map_size=2 * 1024 * 1024,  # 2 MB — 500 entries max
            readahead=False,
            critical=True,  # POTENTIAL-1 Fix: auth cookies need durability
        )
        return env
    except Exception:  # noqa: BLE001 — fail-soft
        return None


def _loads_cookie(value: bytes) -> dict[str, Any] | None:
    """Parse cookie value from LMDB. Returns None on error."""
    try:
        import orjson

        return orjson.loads(value)
    except Exception:  # noqa: BLE001
        return None


def _dumps_cookie(data: dict[str, Any]) -> bytes | None:
    """Serialize cookie data to bytes. Returns None on error."""
    try:
        import orjson

        return orjson.dumps(data)
    except Exception:  # noqa: BLE001
        return None


# --- Cookie Jar ---


class ClearanceCookieJar:
    """
    Bounded LMDB-backed cookie jar for Cloudflare/DataDome clearance.

    Stores per-domain clearance cookies with TTL. On lookup, returns
    valid (non-expired) cookies for injection into curl_cffi sessions.

    M1 8GB: 2 MB LMDB map, max 500 entries, FIFO eviction.
    """

    __slots__ = ("_env", "_max_entries", "_default_ttl_s")

    # Default TTLs by cookie type
    CF_CLEARANCE_TTL_S: int = 30 * 60  # 30 minutes (conservative)
    DATADOME_TTL_S: int = 365 * 24 * 60 * 60  # 1 year

    def __init__(
        self,
        max_entries: int = 500,
        default_ttl_s: int | None = None,
    ) -> None:
        self._max_entries = max_entries
        self._default_ttl_s = default_ttl_s or self.CF_CLEARANCE_TTL_S
        self._env = _open_lmdb_env()

    def _domain_key(self, domain: str) -> str:
        """Normalize domain to cache key."""
        return domain.lower().strip()

    def _is_expired(self, entry: dict[str, Any]) -> bool:
        """Check if cookie entry has expired based on stored expiry."""
        expires_at = entry.get("expires_at", 0)
        return time.time() > expires_at

    def get(self, domain: str) -> dict[str, str]:
        """
        Get valid clearance cookies for domain.

        Returns:
            Dict of {cookie_name: cookie_value} for unexpired cookies.
            Empty dict if no valid cookies found or on any error.
        """
        if self._env is None:
            return {}

        try:
            domain_key = self._domain_key(domain)
            with self._env.begin() as txn:
                raw = txn.get(domain_key.encode("utf-8"))
            if not raw:
                return {}

            entry = _loads_cookie(raw)
            if entry is None:
                return {}

            if self._is_expired(entry):
                # Evict expired entry
                try:
                    with self._env.begin(write=True) as txn:
                        txn.delete(domain_key.encode("utf-8"))
                except Exception:  # noqa: BLE001
                    pass
                return {}

            cookies = entry.get("cookies", {})
            if not isinstance(cookies, dict):
                return {}

            # Validate cookie values are non-empty
            return {k: v for k, v in cookies.items() if v}

        except Exception:  # noqa: BLE001 — fail-soft
            return {}

    def put(
        self,
        domain: str,
        cookies: dict[str, str],
        ttl_s: int | None = None,
    ) -> bool:
        """
        Store clearance cookies for domain with TTL.

        Args:
            domain: Domain these cookies apply to
            cookies: Dict of {cookie_name: cookie_value}
            ttl_s: TTL in seconds (default: CF_CLEARANCE_TTL_S)

        Returns:
            True if stored successfully, False on any error.
        """
        if self._env is None:
            return False
        if not cookies or not domain:
            return False

        try:
            domain_key = self._domain_key(domain)
            ttl = ttl_s or self._default_ttl_s
            expires_at = time.time() + ttl

            entry = {
                "cookies": cookies,
                "expires_at": expires_at,
                "stored_at": time.time(),
            }

            raw_value = _dumps_cookie(entry)
            if raw_value is None:
                return False

            # FIFO eviction if at capacity
            with self._env.begin(write=True) as txn:
                # Check count using a cursor
                cursor = txn.cursor()
                count = 0
                for _ in cursor.iternext(keys=False, values=False):
                    count += 1
                cursor.close()

                # If at capacity, evict oldest by stored_at BEFORE inserting.
                # Also delete the domain entry itself if it already exists
                # (avoids duplicate domain entries that break the count invariant).
                if count >= self._max_entries:
                    # First, try to delete the domain_key if it already exists
                    # so we replace rather than accumulate
                    existing_raw = txn.get(domain_key.encode("utf-8"))
                    if existing_raw:
                        txn.delete(domain_key.encode("utf-8"))
                        count -= 1  # account for the deletion

                    # If still at capacity after potential domain replacement,
                    # evict the oldest entry by stored_at
                    if count >= self._max_entries:
                        evict_cursor = txn.cursor()
                        oldest_key: bytes | None = None
                        oldest_time = float("inf")
                        # iterprev walks from last item backwards
                        for k, v in evict_cursor.iterprev(keys=True, values=True):
                            # Skip the domain_key we just deleted (if present)
                            if k == domain_key.encode("utf-8"):
                                continue
                            parsed = _loads_cookie(v)
                            if parsed:
                                stored = parsed.get("stored_at", 0)
                                if stored < oldest_time:
                                    oldest_time = stored
                                    oldest_key = k
                        evict_cursor.close()
                        if oldest_key is not None:
                            txn.delete(oldest_key)

                txn.put(domain_key.encode("utf-8"), raw_value)

            logger.debug(
                "[CLEARANCE] stored %d cookies for %s (ttl=%ds)",
                len(cookies),
                domain,
                ttl,
            )
            return True

        except Exception:  # noqa: BLE001 — fail-soft
            return False

    def put_cf_clearance(self, domain: str, clearance_token: str) -> bool:
        """Convenience method for Cloudflare cf_clearance cookie."""
        return self.put(domain, {"cf_clearance": clearance_token})

    def put_datadome(self, domain: str, datadome_cookie: str) -> bool:
        """Convenience method for DataDome cookie."""
        return self.put(
            domain,
            {"datadome": datadome_cookie},
            ttl_s=self.DATADOME_TTL_S,
        )

    def delete(self, domain: str) -> bool:
        """Delete cookies for domain."""
        if self._env is None:
            return False
        try:
            domain_key = self._domain_key(domain)
            with self._env.begin(write=True) as txn:
                txn.delete(domain_key.encode("utf-8"))
            return True
        except Exception:  # noqa: BLE001 — fail-soft
            return False

    def clear_expired(self) -> int:
        """
        Remove all expired entries.

        Returns:
            Number of entries removed.
        """
        if self._env is None:
            return 0

        removed = 0
        try:
            with self._env.begin(write=True) as txn:
                cursor = txn.cursor()
                expired_keys: list[bytes] = []
                for k, v in cursor.iterprev(keys=True, values=True):
                    parsed = _loads_cookie(v)
                    if parsed and self._is_expired(parsed):
                        expired_keys.append(k)
                cursor.close()

                for key in expired_keys:
                    txn.delete(key)
                    removed += 1

        except Exception:  # noqa: BLE001 — fail-soft
            pass

        if removed:
            logger.debug("[CLEARANCE] cleared %d expired entries", removed)
        return removed

    def stats(self) -> dict[str, Any]:
        """Return jar statistics for telemetry."""
        if self._env is None:
            return {"available": False}

        try:
            with self._env.begin() as txn:
                cursor = txn.cursor()
                count = sum(1 for _ in cursor.iternext(keys=False, values=False))
                cursor.close()

            return {
                "available": True,
                "entry_count": count,
                "max_entries": self._max_entries,
            }
        except Exception:  # noqa: BLE001 — fail-soft
            return {"available": False, "error": True}


# --- Global singleton (lazy) ---


_jar: ClearanceCookieJar | None = None


def get_clearance_jar() -> ClearanceCookieJar:
    """Get the global ClearanceCookieJar singleton."""
    global _jar
    if _jar is None:
        _jar = ClearanceCookieJar()
    return _jar
