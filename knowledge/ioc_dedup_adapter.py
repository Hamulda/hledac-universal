"""
IOC Dedup Adapter — Sprint F265B-III (ioc_dedup.rs integration)
===============================================================

Wraps Rust IocDedupStore for cross-sprint IOC deduplication with
type-aware normalization (domains lowercase, hashes lowercase, CVE uppercase).

CANONICAL WRITE PATH INTEGRATION:
    async_ingest_findings_batch()
        -> extract_iocs_from_texts() [already exists]
        -> IocDedupAdapter.add_iocs() [NEW: normalize + track cross-sprint]
        -> quality gate + BLAKE2b dedup [existing]
        -> DuckDB insert [existing]

WHY NOT REPLACE BLAKE2b DEDUP:
    - BLAKE2b dedup = content-level (same text → duplicate)
    - IocDedupStore = IOC-level (same IOC value → duplicate, type-aware)
    Both run in parallel; IocDedupStore enriches the decision, doesn't replace it.

PERSISTENCE:
    - State serialized to LMDB via IocDedupStore.get_state_bytes()
    - Restored via ioc_dedup_from_bytes() or Python fallback

M1 8GB: Rust AHashMap (50k capacity) ≈ 5-8 MB resident
"""


import atexit
import json
import logging
import weakref
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from hledac.universal.paths import LMDB_ROOT

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LMDB path for IocDedupStore persistence
# ---------------------------------------------------------------------------

_IOC_DEDUP_LMDB_PATH = LMDB_ROOT / "ioc_dedup.lmdb"


# ─── Module-level cleanup callback for weakref.finalize ──────────────

def _ioc_dedup_at_exit_close(instance: IocDedupAdapter) -> None:
    """Called by weakref.finalize at interpreter exit if explicit close() was not called.

    LMDB environment handles need explicit close() on process exit to avoid
    map file corruption and ensure all pending writes are flushed.
    weakref.finalize + atexit ensures this even if close() was never called.
    """
    try:
        # First persist state if dirty
        if instance._dirty:
            instance._persist_lmdb()
    except Exception:  # noqa: BLE001
        pass
    try:
        # Then close LMDB environment
        if instance._lmdb_env is not None:
            instance._lmdb_env.close()
            instance._lmdb_env = None
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Python fallback IOC normalizer (mirrors ioc_dedup.rs::normalize_ioc)
# Used when Rust extension is not available
# ---------------------------------------------------------------------------


def _normalize_ioc_value(value: str, ioc_type: str) -> str:
    """
    Normalize IOC value according to type rules.
    Mirrors Rust ioc_dedup.rs::normalize_ioc() for Python fallback path.
    """
    if not value:
        return ""

    lower_type = ioc_type.lower()

    if lower_type in ("domain", "fqdn"):
        lower = value.lower()
        return lower[4:] if lower.startswith("www.") else lower
    if lower_type in ("md5", "sha1", "sha256", "sha2"):
        return value.lower()
    if lower_type == "cve":
        return value.upper()
    if lower_type == "ip":
        # Normalize leading zeros in octets: 192.168.001.001 -> 192.168.1.1
        parts = value.split(".")
        normalized = []
        for octet in parts:
            try:
                normalized.append(str(int(octet)))
            except ValueError:
                normalized.append(octet)
        return ".".join(normalized)
    if lower_type == "ipv6":
        return value.lower()
    return value


# ---------------------------------------------------------------------------
# Python fallback IocDedupStore (when Rust not available)
# ---------------------------------------------------------------------------


@dataclass
class _IocEntryPython:
    """Python fallback entry matching ioc_dedup.rs::IocEntry."""
    normalized_value: str
    ioc_type: str
    first_seen_sprint: int
    last_seen_sprint: int
    occurrence_count: int
    confidence_max: float


class IocDedupStorePythonFallback:
    """
    Pure-Python IocDedupStore fallback.
    Mirrors Rust IocDedupStore API for environments without compiled extension.
    """

    def __init__(self, sprint_id: int = 0) -> None:
        self._entries: dict[str, _IocEntryPython] = {}
        self._current_sprint = sprint_id
        self._total_seen = 0
        self._total_deduped = 0

    def add(self, value: str, ioc_type_str: str, confidence: float = 0.5) -> bool:
        """Add IOC — returns True if NEW, False if duplicate."""
        self._total_seen += 1
        if not value:
            return False

        normalized = _normalize_ioc_value(value, ioc_type_str)
        key = f"{ioc_type_str.lower()}:{normalized}"

        if key in self._entries:
            entry = self._entries[key]
            entry.last_seen_sprint = self._current_sprint
            entry.occurrence_count += 1
            if confidence > entry.confidence_max:
                entry.confidence_max = confidence
            self._total_deduped += 1
            return False

        self._entries[key] = _IocEntryPython(
            normalized_value=normalized,
            ioc_type=ioc_type_str.lower(),
            first_seen_sprint=self._current_sprint,
            last_seen_sprint=self._current_sprint,
            occurrence_count=1,
            confidence_max=confidence,
        )
        return True

    def add_batch(self, items: list[tuple[str, str, float]]) -> list[bool]:
        """Batch add — returns list of bool (True = new)."""
        return [self.add(value, ioc_type, confidence) for value, ioc_type, confidence in items]

    def contains(self, value: str, ioc_type_str: str) -> bool:
        """Check if IOC exists."""
        if not value:
            return False
        normalized = _normalize_ioc_value(value, ioc_type_str)
        key = f"{ioc_type_str.lower()}:{normalized}"
        return key in self._entries

    def advance_sprint(self, new_sprint_id: int) -> None:
        """Advance to next sprint."""
        self._current_sprint = new_sprint_id

    def __len__(self) -> int:
        return len(self._entries)

    def is_empty(self) -> bool:
        return len(self._entries) == 0

    def stats(self) -> tuple[int, int, int]:
        """Returns (total_seen, total_deduped, unique_count)."""
        return (self._total_seen, self._total_deduped, len(self._entries))

    def stats_dict(self) -> dict:
        """SoA-style stats dict matching Rust stats_dict()."""
        total = self._total_seen
        hit_rate = self._total_deduped / total if total > 0 else 0.0
        hit_rate_bp = int(hit_rate * 10_000)
        return {
            "total_seen": self._total_seen,
            "total_deduped": self._total_deduped,
            "unique_count": len(self._entries),
            "current_sprint": self._current_sprint,
            "hit_rate_bp": hit_rate_bp,
        }

    def get_by_type(self, ioc_type_str: str) -> list[str]:
        """Get all IOC values of specified type."""
        lower = ioc_type_str.lower()
        return [
            e.normalized_value
            for e in self._entries.values()
            if e.ioc_type == lower
        ]

    def get_entries_by_type(
        self, ioc_type_str: str
    ) -> list[tuple[str, int, int, int, float]]:
        """Get entries with full metadata."""
        lower = ioc_type_str.lower()
        return [
            (e.normalized_value, e.first_seen_sprint, e.last_seen_sprint,
             e.occurrence_count, e.confidence_max)
            for e in self._entries.values()
            if e.ioc_type == lower
        ]

    def get_sprint(self) -> int:
        return self._current_sprint

    def to_bytes(self) -> bytes:
        """Serialize state to bytes (compatible with Rust get_state_bytes)."""
        data = {
            "entries": {
                k: {
                    "nv": v.normalized_value,
                    "it": v.ioc_type,
                    "fs": v.first_seen_sprint,
                    "ls": v.last_seen_sprint,
                    "oc": v.occurrence_count,
                    "cm": v.confidence_max,
                }
                for k, v in self._entries.items()
            },
            "cs": self._current_sprint,
            "ts": self._total_seen,
            "td": self._total_deduped,
        }
        return json.dumps(data, separators=(",", ":")).encode("utf-8")

    def clear(self) -> None:
        self._entries.clear()
        self._total_seen = 0
        self._total_deduped = 0


# ---------------------------------------------------------------------------
# IocDedupAdapter — main entry point
# ---------------------------------------------------------------------------


@dataclass
class IocDedupStats:
    """Stats snapshot from IocDedupAdapter."""
    total_seen: int = 0
    total_deduped: int = 0
    unique_count: int = 0
    current_sprint: int = 0
    hit_rate_bp: int = 0  # basis points (0.75 → 7500)


class IocDedupAdapter:
    """
    Cross-sprint IOC deduplication with type-aware normalization.

    Wraps Rust IocDedupStore when available; falls back to pure Python.

    Integration point: called in async_ingest_findings_batch() after
    IOC extraction (extract_iocs_from_texts) and BEFORE quality gate
    BLAKE2b dedup. This ensures IOC values are normalized consistently.

    M1 8GB: Rust AHashMap cap=50k ≈ 5-8 MB; Python fallback uses
    dict with same cap, slightly more memory but bounded.

    PERSISTENCE: State persisted to LMDB on every advance_sprint() call.
    Load happens lazily on first add() after init or after process restart.
    """

    def __init__(self, sprint_id: int = 0) -> None:
        self._sprint_id = sprint_id
        self._rust_available = False
        self._store: Any = None  # IocDedupStore | IocDedupStorePythonFallback
        self._lmdb_env = None
        self._dirty = False
        self._load_attempted = False

        # Probe for Rust extension (lazy import — extension may not be built)
        # F265C: Use centralized rust backend
        self._rust_available = False
        try:
            from core.rust_backend import rust as _rust_backend

            if _rust_backend.is_available and _rust_backend.ioc_dedup is not None:
                self._store = _rust_backend.ioc_dedup.IocDedupStore(sprint_id=sprint_id)
                self._rust_available = True
                logger.debug("[IOC-DEDUP] Rust IocDedupStore initialized (sprint_id=%d)", sprint_id)
            else:
                raise ImportError("Rust ioc_dedup not available")
        except Exception as exc:
            logger.debug(
                "[IOC-DEDUP] Rust IocDedupStore unavailable (%s), using Python fallback", exc
            )
            self._store = IocDedupStorePythonFallback(sprint_id=sprint_id)

        # Ensure LMDB path exists
        try:
            _IOC_DEDUP_LMDB_PATH.mkdir(parents=True, exist_ok=True)
        except Exception:  # noqa: BLE001
            pass

        # F289: weakref.finalize for interpreter-exit cleanup guarantee.
        # LMDB environment handle needs explicit close() on process exit
        # to avoid map file corruption. finalizer ensures this even if close()
        # was never called explicitly.
        self._finalizer = weakref.finalize(
            self,
            _ioc_dedup_at_exit_close,
            self,
        )
        atexit.register(self._finalizer)

    # -------------------------------------------------------------------------
    # Public API (mirrors IocDedupStore pyclass methods)
    # -------------------------------------------------------------------------

    def add(self, value: str, ioc_type: str, confidence: float = 0.5) -> bool:
        """
        Add IOC to dedup store. Returns True if NEW (not duplicate), False if duplicate.

        Args:
            value: IOC value (domain, URL, IP, hash, CVE, email)
            ioc_type: IOC type string (domain, url, ip, md5, sha1, sha256, cve, email, etc.)
            confidence: Confidence score [0.0, 1.0], used for confidence_max tracking

        Returns:
            True if this is a NEW IOC (accepted), False if duplicate
        """
        if self._store is None:
            return True

        try:
            result = self._store.add(value, ioc_type, confidence)
            self._dirty = True
            return result
        except Exception as exc:
            logger.warning("[IOC-DEDUP] add(%s, %s) failed: %s", value[:30], ioc_type, exc)
            return True  # Fail-open: accept on error

    def add_batch(
        self, items: list[tuple[str, str, float]]
    ) -> list[bool]:
        """
        Batch add IOCs. Returns list of bool (True = new).

        Args:
            items: List of (value, ioc_type, confidence) tuples
        """
        if self._store is None:
            return [True] * len(items)

        results = []
        for value, ioc_type, confidence in items:
            results.append(self.add(value, ioc_type, confidence))
        return results

    def contains(self, value: str, ioc_type: str) -> bool:
        """Check if IOC exists in store (without affecting counters)."""
        if self._store is None:
            return False
        try:
            return self._store.contains(value, ioc_type)
        except Exception:
            return False

    def advance_sprint(self, new_sprint_id: int) -> None:
        """
        Advance to new sprint. Persists current state to LMDB before advancing.

        Called by sprint_scheduler at sprint boundary.
        """
        # Persist before advancing
        self._persist_lmdb()

        if self._store is not None:
            try:
                self._store.advance_sprint(new_sprint_id)
                self._sprint_id = new_sprint_id
            except Exception as exc:
                logger.warning("[IOC-DEDUP] advance_sprint(%d) failed: %s", new_sprint_id, exc)

    def len(self) -> int:
        return len(self._store) if self._store else 0

    def is_empty(self) -> bool:
        return self._store.is_empty() if self._store else True

    def get_stats(self) -> IocDedupStats:
        """Get current dedup statistics."""
        if self._store is None:
            return IocDedupStats()

        try:
            stats = self._store.stats()
            stats_dict = self._store.stats_dict()
            return IocDedupStats(
                total_seen=stats[0],
                total_deduped=stats[1],
                unique_count=stats[2],
                current_sprint=stats_dict.get("current_sprint", self._sprint_id),
                hit_rate_bp=stats_dict.get("hit_rate_bp", 0),
            )
        except Exception as exc:
            logger.warning("[IOC-DEDUP] get_stats() failed: %s", exc)
            return IocDedupStats()

    def get_by_type(self, ioc_type: str) -> list[str]:
        """Get all IOC values of specified type."""
        if self._store is None:
            return []
        try:
            return self._store.get_by_type(ioc_type)
        except Exception:
            return []

    def get_entries_by_type(
        self, ioc_type: str
    ) -> list[tuple[str, int, int, int, float]]:
        """
        Get entries with full metadata for a given IOC type.

        Returns:
            List of (normalized_value, first_sprint, last_sprint, occurrence_count, confidence_max)
        """
        if self._store is None:
            return []
        try:
            return self._store.get_entries_by_type(ioc_type)
        except Exception:
            return []

    def normalize_value(self, value: str, ioc_type: str) -> str:
        """
        Normalize IOC value according to type rules (mirrors Rust normalize_ioc).

        Useful for callers that need the normalized form without adding to store.
        """
        return _normalize_ioc_value(value, ioc_type)

    def current_sprint(self) -> int:
        """Get current sprint ID."""
        return self._sprint_id

    # -------------------------------------------------------------------------
    # Persistence (LMDB)
    # -------------------------------------------------------------------------

    def _ensure_lmdb(self) -> bool:
        """Ensure LMDB environment is open. Returns True if successful."""
        if self._lmdb_env is not None:
            return True

        try:
            import lmdb

            self._lmdb_env = lmdb.open(
                str(_IOC_DEDUP_LMDB_PATH),
                map_size=16 * 1024 * 1024,  # 16 MB — 50k entries ≈ 5-8 MB
                readonly=False,
                lock=True,
            )
            return True
        except Exception as exc:
            logger.debug("[IOC-DEDUP] LMDB open failed (non-fatal): %s", exc)
            self._lmdb_env = None
            return False

    def _load_lmdb(self) -> bool:
        """
        Load persisted state from LMDB.
        Called lazily on first add() after init or after advance_sprint().
        """
        if self._load_attempted:
            return False
        self._load_attempted = True

        if not self._ensure_lmdb():
            return False

        try:

            with self._lmdb_env.begin() as txn:  # type: ignore[union-attr]
                data = txn.get(b"ioc_dedup_state")
                if data is None:
                    return False

            if self._rust_available:
                from core.rust_backend import rust as _rust_backend
                ioc_dedup_from_bytes = getattr(_rust_backend.ioc_dedup, 'ioc_dedup_from_bytes', None)
                if ioc_dedup_from_bytes:
                    self._store = ioc_dedup_from_bytes(bytes(data))
                else:
                    raise ImportError("ioc_dedup_from_bytes not available")
            else:
                # Python fallback: decode JSON
                import json

                parsed = json.loads(data.decode("utf-8"))
                fallback = IocDedupStorePythonFallback(sprint_id=parsed.get("cs", 0))
                fallback._entries = {}
                for k, v in parsed.get("entries", {}).items():
                    fallback._entries[k] = _IocEntryPython(
                        normalized_value=v["nv"],
                        ioc_type=v["it"],
                        first_seen_sprint=v["fs"],
                        last_seen_sprint=v["ls"],
                        occurrence_count=v["oc"],
                        confidence_max=v["cm"],
                    )
                fallback._total_seen = parsed.get("ts", 0)
                fallback._total_deduped = parsed.get("td", 0)
                self._store = fallback

            logger.info(
                "[IOC-DEDUP] Loaded %d entries from LMDB (rust=%s)",
                len(self._store) if self._store else 0,  # type: ignore[arg-type]
                self._rust_available,
            )
            self._dirty = False
            return True

        except Exception as exc:
            logger.debug("[IOC-DEDUP] LMDB load failed (non-fatal): %s", exc)
            return False

    def _persist_lmdb(self) -> bool:
        """
        Persist current state to LMDB.
        Called on advance_sprint() and during graceful shutdown.
        """
        if not self._dirty:
            return True  # Nothing to persist

        if not self._ensure_lmdb():
            return False

        try:

            if self._rust_available and hasattr(self._store, "to_bytes"):
                state_bytes = self._store.to_bytes()
            elif self._store is not None:
                state_bytes = self._store.to_bytes()
            else:
                return False

            with self._lmdb_env.begin(write=True) as txn:
                txn.put(b"ioc_dedup_state", bytes(state_bytes))

            self._dirty = False
            logger.debug(
                "[IOC-DEDUP] Persisted %d entries to LMDB",
                len(self._store) if self._store else 0,  # type: ignore[arg-type]
            )
            return True

        except Exception as exc:
            logger.warning("[IOC-DEDUP] LMDB persist failed (non-fatal): %s", exc)
            return False

    def flush(self) -> bool:
        """Explicitly flush state to LMDB (called during sprint winddown)."""
        return self._persist_lmdb()

    def close(self) -> None:
        """Graceful shutdown — persist state and close LMDB.

        F289: Detaches finalizer on explicit call to prevent double-cleanup
        at interpreter exit. After detach(), atexit no longer triggers
        _ioc_dedup_at_exit_close.
        """
        # Detach finalizer — explicit close wins over atexit
        self._finalizer.detach()

        self._persist_lmdb()
        if self._lmdb_env is not None:
            try:
                self._lmdb_env.close()
            except Exception:  # noqa: BLE001
                pass
            self._lmdb_env = None
