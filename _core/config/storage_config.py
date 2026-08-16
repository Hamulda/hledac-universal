"""
core/config/storage_config.py — StorageConfig: msgspec.Struct typed configuration for paths.

ISSUE-033: SSD-aware paths + LIBC_PERF_OPT support.


Design:
    - msgspec.Struct (frozen=True) — zero-copy decode, ~3× faster than dataclass
    - Defaults tuned for M1 8GB MacBook Air
    - LIBC_PERF_OPT hint: when set, paths.py uses /tmp-based storage hints
      (macOS /tmp is APFS/SSD-backed — suitable for testing without RAM disk)
    - storage_mode: "auto" | "ramdisk" | "ssd" | "tmp"

Invariant tests (TestSprintISSUE033):
  INV: storage_config_frozen       — instance is immutable (frozen=True)
  INV: storage_config_slots        — __slots__ defined (no __dict__ overhead)
  INV: storage_config_default_mode  — default storage_mode == "auto"
  INV: storage_config_libc_perf_opt — LIBC_PERF_OPT=1 sets ssd_hint=True
  INV: storage_config_env_override  — ENV vars override defaults
  INV: storage_config_msgspec_encodes — encodes/decodes via msgspec without error
"""


from __future__ import annotations

import os
from typing import ClassVar

import msgspec
from _core._util import aclose


class StorageConfig(msgspec.Struct, frozen=True, kw_only=True, gc=False):
    """
    Immutable typed storage configuration.

    Attributes:
        storage_mode: "auto" (detect) | "ramdisk" | "ssd" | "tmp"
            - "auto": detect from HLEDAC_RAMDISK / GHOST_RAMDISK env vars
            - "ramdisk": force RAM disk usage
            - "ssd": force SSD fallback
            - "tmp": use /tmp (APFS/SSD-backed on macOS — LIBC_PERF_OPT hint)
        ramdisk_size_mb: RAM disk size in MB (hdiutil ram:// sectors)
            Default: 512 MB (suitable for M1 8GB)
        lmdb_map_size_mb: LMDB map_size per environment
            Default: 256 MB (M1 8GB ceiling — Phase4)
        duckdb_threads: DuckDB thread count
            Default: 2 (optimal for M1 8GB thread-local conn bottleneck)
        duckdb_inprocess: DuckDB in-process mode (saves ~200MB RAM vs IPC)
            Default: True
        libc_perf_opt: SSD optimization hint
            When True: paths.py enables /tmp-based temp hints for DuckDB/LMDB
            On macOS: /tmp is APFS/SSD-backed (cached), suitable for testing
            Default: False
        ramdisk_auto_create: Auto-create RAM disk if not found at runtime
            Default: True (F500I: deferred creation, not on import)
        hledac_ramdisk_path: Override RAM disk mount point
            Default: /Volumes/ghost_tmp or /tmp/hledac_ramdisk
        sprint_store_path: Override sprint artifact store path
        duckdb_store_path: Override DuckDB store path
        lmdb_store_path: Override LMDB store path
        lancedb_store_path: Override LanceDB store path
    """

    # ── Storage mode ────────────────────────────────────────────────────────

    storage_mode: str = "auto"
    """Detection mode: 'auto' | 'ramdisk' | 'ssd' | 'tmp'."""

    # ── Size limits ─────────────────────────────────────────────────────────

    ramdisk_size_mb: int = 512
    """RAM disk size in MB. 512 MB is safe for M1 8GB (leaves ~7.5GB for system)."""

    lmdb_map_size_mb: int = 256
    """LMDB map_size in MB per environment. M1 8GB ceiling is 256 MB."""

    # ── DuckDB ─────────────────────────────────────────────────────────────

    duckdb_threads: int = 2
    """DuckDB thread count. 2 is optimal for M1 8GB thread-local conn bottleneck."""

    duckdb_inprocess: bool = True
    """DuckDB in-process mode. Saves ~200 MB RAM vs out-of-process IPC."""

    # ── SSD optimization ───────────────────────────────────────────────────

    libc_perf_opt: bool = False
    """
    SSD optimization hint (LIBC_PERF_OPT env var).

    When True: paths.py treats /tmp as a viable fast-path storage location.
    On macOS /tmp is APFS/SSD-backed (page cache), fast enough for testing
    without RAM disk overhead. LMDB and DuckDB use /tmp for temp files.

    Use cases:
        - CI/CD on SSD runners (no RAM disk available)
        - Local testing where RAM disk is impractical
        - M1 8GB testing with limited RAM headroom
    """

    # ── RAM disk ───────────────────────────────────────────────────────────

    ramdisk_auto_create: bool = True
    """Auto-create RAM disk at runtime if not found (F500I: deferred, not on import)."""

    # ── Path overrides ────────────────────────────────────────────────────

    hledac_ramdisk_path: str = ""
    """Override RAM disk mount point. Empty = auto-detect."""

    sprint_store_path: str = ""
    """Override sprint artifact store path."""

    duckdb_store_path: str = ""
    """Override DuckDB store path."""

    lmdb_store_path: str = ""
    """Override LMDB store path."""

    lancedb_store_path: str = ""
    """Override LanceDB store path."""

    # ── Defaults ───────────────────────────────────────────────────────────

    _defaults: ClassVar[dict[str, str]] = {
        "HLEDAC_RAMDISK": "",
        "GHOST_RAMDISK": "",
        "HLEDAC_SPRINT_STORE": "",
        "HLEDAC_DUCKDB_STORE": "",
        "HLEDAC_LMDB_STORE": "",
        "HLEDAC_LANCEDB_STORE": "",
        "GHOST_LMDB_MAX_SIZE_MB": "256",
        "LIBC_PERF_OPT": "0",
    }

    @classmethod
    def from_env(cls) -> StorageConfig:
        """
        Construct StorageConfig from environment variables.

        Environment variables override default values:
            LIBC_PERF_OPT=1          → libc_perf_opt=True
            HLEDAC_RAMDISK=/path     → hledac_ramdisk_path=/path
            GHOST_LMDB_MAX_SIZE_MB   → lmdb_map_size_mb=int
            HLEDAC_SPRINT_STORE=     → sprint_store_path= (if non-empty)
            ...
        """
        libc_perf_opt = os.environ.get("LIBC_PERF_OPT", "0").lower() in ("1", "true", "yes")
        lmdb_mb = cls._env_int("GHOST_LMDB_MAX_SIZE_MB", 256)
        duckdb_threads = cls._env_int("HLEDAC_DUCKDB_THREADS", 2)
        duckdb_inprocess = os.environ.get("HLEDAC_DUCKDB_INPROCESS", "1").lower() in ("1", "true", "yes")

        return cls(
            storage_mode=cls._detect_storage_mode(),
            ramdisk_size_mb=cls._env_int("HLEDAC_RAMDISK_SIZE_MB", 512),
            lmdb_map_size_mb=lmdb_mb,
            duckdb_threads=duckdb_threads,
            duckdb_inprocess=duckdb_inprocess,
            libc_perf_opt=libc_perf_opt,
            ramdisk_auto_create=os.environ.get("HLEDAC_RAMDISK_AUTO_CREATE", "1").lower() in ("1", "true", "yes"),
            hledac_ramdisk_path=os.environ.get("HLEDAC_RAMDISK", "") or os.environ.get("GHOST_RAMDISK", ""),
            sprint_store_path=os.environ.get("HLEDAC_SPRINT_STORE", ""),
            duckdb_store_path=os.environ.get("HLEDAC_DUCKDB_STORE", ""),
            lmdb_store_path=os.environ.get("HLEDAC_LMDB_STORE", ""),
            lancedb_store_path=os.environ.get("HLEDAC_LANCEDB_STORE", ""),
    )

    @classmethod
    def _detect_storage_mode(cls) -> str:
        """Detect storage mode from environment."""
        if os.environ.get("HLEDAC_RAMDISK") or os.environ.get("GHOST_RAMDISK"):
            return "ramdisk"
        if os.environ.get("LIBC_PERF_OPT", "0").lower() in ("1", "true", "yes"):
            return "ssd"
        return "auto"

    @staticmethod
    def _env_int(name: str, default: int) -> int:
        """Parse env var as int, return default on failure."""
        try:
            return int(os.environ.get(name, str(default)))
        except (ValueError, TypeError):
            return default

    def is_tmp_acceptable(self) -> bool:
        """
        Return True if /tmp-based storage is acceptable.

        True when:
            - libc_perf_opt=True (SSD optimization)
            - storage_mode="tmp" (explicit)
            - storage_mode="ssd" (SSD fallback)
        """
        return self.libc_perf_opt or self.storage_mode in ("tmp", "ssd")

    def is_ramdisk_acceptable(self) -> bool:
        """Return True if RAM disk usage is acceptable."""
        return self.storage_mode in ("auto", "ramdisk")


# ─────────────────────────────────────────────────────────────────────────────
# Module-level singleton (lazy — constructed on first access)
# ─────────────────────────────────────────────────────────────────────────────

_storage_config: StorageConfig | None = None


def get_storage_config() -> StorageConfig:
    """Get the module-level StorageConfig singleton (from env)."""
    global _storage_config
    if _storage_config is None:
        _storage_config = StorageConfig.from_env()
    return _storage_config
