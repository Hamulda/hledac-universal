"""
TemporalSignalStore — SQLite WAL persistence for TemporalSignalLayer snapshots.

Provides cross-run persistence for temporal signal state, enabling detection of
dormant infrastructure wake-up and long-range temporal patterns.

Design:
- WAL mode for crash-resilience without full synchronous writes
- Single JSON blob snapshot — no per-event writes
- Fail-soft throughout — store errors never crash the pipeline
- Bounded: store optional via HLEDAC_ENABLE_TEMPORAL_STORE env flag

No heavy imports at module level.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS temporal_snapshot (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    snapshot    TEXT NOT NULL,
    updated_at  REAL NOT NULL
);
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
"""

DEFAULT_STORE_PATH = Path(__file__).parent.parent / ".temporal_store" / "temporal_signal.db"


class TemporalSignalStore:
    """
    SQLite WAL store for TemporalSignalLayer snapshots.

    Parameters
    ----------
    path : str | Path
        Path to the SQLite database file. Defaults to .temporal_store/temporal_signal.db
        in the project root.

    Methods
    -------
    initialize()
        Create schema (idempotent).
    save_snapshot(snapshot: dict[str, Any]) -> None
        Overwrite the single snapshot row with the current layer state.
    load_snapshot() -> dict[str, Any] | None
        Restore the snapshot or return None if none saved yet.
    close() -> None
        Close WAL checkpoint. Idempotent.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self._path: Path = Path(path) if path is not None else DEFAULT_STORE_PATH
        self._conn: sqlite3.Connection | None = None

    # ── Public API ───────────────────────────────────────────────────────────

    def initialize(self) -> None:
        """Create schema. Idempotent. Failsoft — any error is logged and swallowed."""
        try:
            self._ensure_dir()
            self._conn = sqlite3.connect(str(self._path), timeout=10.0)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(SCHEMA_SQL)
            self._conn.commit()
        except Exception as exc:
            logger.warning("[TemporalSignalStore] initialize() failed: %s", exc)
            self._conn = None

    def save_snapshot(self, snapshot: dict[str, Any]) -> None:
        """
        Save snapshot atomically via REPLACE. No per-event writes.

        Failsoft — store errors are logged and swallowed; pipeline continues.
        """
        if self._conn is None:
            return
        try:
            payload = json.dumps(snapshot, default=str)
            updated_at = __import__("time").time()
            self._conn.execute(
                "REPLACE INTO temporal_snapshot (id, snapshot, updated_at) VALUES (1, ?, ?)",
                (payload, updated_at),
            )
            self._conn.commit()
            # checkpoint to advance WAL head without full sync
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception as exc:
            logger.warning("[TemporalSignalStore] save_snapshot() failed: %s", exc)
            try:
                self._conn.rollback()
            except Exception:
                pass

    def load_snapshot(self) -> dict[str, Any] | None:
        """
        Load the persisted snapshot.

        Returns None if no snapshot exists or on any error (corrupt DB, missing row, etc.).
        Failsoft — errors are logged and None is returned.
        """
        if self._conn is None:
            return None
        try:
            cursor = self._conn.execute(
                "SELECT snapshot FROM temporal_snapshot WHERE id = 1"
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return json.loads(row[0])
        except Exception as exc:
            logger.warning("[TemporalSignalStore] load_snapshot() failed: %s", exc)
            return None

    def close(self) -> None:
        """
        Checkpoint WAL and close connection. Idempotent.
        """
        if self._conn is None:
            return
        try:
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._conn.close()
        except Exception as exc:
            logger.warning("[TemporalSignalStore] close() failed: %s", exc)
        finally:
            self._conn = None

    # ── Private helpers ──────────────────────────────────────────────────────

    def _ensure_dir(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            logger.warning("[TemporalSignalStore] could not create store dir: %s", exc)
            # Fall back to temp path — shouldn't happen on M1 with valid home dir
            import tempfile
            self._path = Path(tempfile.gettempdir()) / "temporal_signal.db"
