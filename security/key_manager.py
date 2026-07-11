"""
KeyManager — Stub implementation.

Provides key management for bucket operations and LMDB storage.
Used by legacy/autonomous_orchestrator.py, intelligence/data_leak_hunter.py, deep_research/probe_runner.py.

Real implementation wraps LMDB for key storage.
Stub provides interface compatibility with callers.
"""


import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class KeyManager:
    """
    Stub key manager for bucket operations.

    Real implementation manages master keys and bucket keys via LMDB.
    Stub provides interface compatibility with callers expecting:
    - db_path: Path to LMDB database
    - get_master_key() -> bytes
    - get_bucket_key(bucket_id) -> tuple[bytes, int]
    - _current_version: int
    """

    def __init__(self, db_path: str | None = None) -> None:
        """
        Initialize key manager.

        Args:
            db_path: Optional path to LMDB database directory
        """
        self._db_path = Path(db_path) if db_path else Path.home() / ".hledac" / "keys"
        self._current_version = 0
        self._master_key: bytes | None = None
        logger.debug(f"KeyManager: db_path={self._db_path}")

    @property
    def db_path(self) -> Path:
        """Return path to LMDB database."""
        return self._db_path

    async def get_master_key(self) -> bytes:
        """
        Get or create master key.

        Returns:
            bytes: Master key (stub: returns zero-filled key)
        """
        if self._master_key is None:
            # Stub: return deterministic key for testing
            self._master_key = b"stub_master_key_32_bytes_xxxxx"
        return self._master_key

    async def get_bucket_key(self, bucket_id: str) -> tuple[bytes, int]:
        """
        Get key for bucket and return version.

        Args:
            bucket_id: Bucket identifier

        Returns:
            tuple[bytes, int]: (key, version)
        """
        # Stub: derive key from bucket_id
        key = f"key_for_{bucket_id}".encode()[:32].ljust(32, b"\x00")
        return key, self._current_version


__all__ = ["KeyManager"]
