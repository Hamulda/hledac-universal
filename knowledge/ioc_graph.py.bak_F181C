"""
IOC Graph — Kuzu-backed entity graph for OSINT IOC tracking.

GRAPH TRUTH STORE (Sprint 8F7)
===============================
IOCGraph is the GraphTruthStore — the authoritative backend for IOC entity truth.
It owns: buffer_ioc(), flush_buffers(), upsert_ioc_batch(), export_stix_bundle(), pivot().
It is NOT the analytics backend — DuckPGQGraph serves that role.

Schema:
  IOC(id STRING PK, ioc_type STRING, value STRING,
      first_seen DOUBLE, last_seen DOUBLE, confidence DOUBLE)
  OBSERVED(finding_id STRING, source_type STRING,
           first_seen DOUBLE, last_seen DOUBLE)

PIVOT:  MATCH (n:IOC)-[r*1..2]-(m:IOC) WHERE n.value=$v AND n.ioc_type=$t RETURN m, r
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import xxhash

import kuzu

# Kuzu single-thread executor — Kuzu itself is not thread-safe for concurrent queries
_DB_EXECUTOR: ThreadPoolExecutor = ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="kuzu_ioc_worker"
)

# Kuzu DB root — file path (Kuzu 0.11+ requires a file, not directory)
_KUZU_DB_ROOT: Path = Path.home() / ".hledac" / "kuzu"
_IOC_GRAPH_FILENAME: str = "ioc_graph"

# IOC type enumeration
IOC_TYPES: frozenset[str] = frozenset(
    ("cve", "ip", "hash_sha256", "hash_md5", "onion", "domain", "apt", "malware")
)

# ---------------------------------------------------------------------------
# Compiled regex constants — NEVER inside functions
# ---------------------------------------------------------------------------
_RE_IP_PUBLIC = re.compile(
    r"\b(?!10\.|127\.|169\.254\.|172\.(?:1[6-9]|2\d|3[01])\.|192\.168\.)"
    r"(?:\d{1,3}\.){3}\d{1,3}\b"
)
_RE_SHA256 = re.compile(r"\b[0-9a-fA-F]{64}\b")
_RE_ONION_V3 = re.compile(r"\b[a-z2-7]{56}\.onion\b")
_RE_ONION_V2 = re.compile(r"\b[a-z2-7]{16}\.onion\b")


def _make_ioc_id(ioc_type: str, value: str) -> str:
    """Generate a deterministic 64-bit hex ID for an IOC."""
    return f"{ioc_type}:{xxhash.xxh64(value.encode()).hexdigest()}"


def extract_iocs_from_text(
    text: str, pattern_matches: list[tuple[str, str]]
) -> list[tuple[str, str]]:
    """
    Extract IOCs from raw text and PatternMatcher hits.

    Returns list of (value, ioc_type) tuples, deduplicated.
    Private/routable IPs are filtered out.
    """
    results: list[tuple[str, str]] = []

    # From PatternMatcher labeled hits
    for match_value, label in pattern_matches:
        if label == "vulnerability_id":
            results.append((match_value, "cve"))
        elif label == "offensive_tool":
            results.append((match_value, "malware"))
        elif label == "attack_technique":
            results.append((match_value, "apt"))
        elif label == "ransomware_group":
            results.append((match_value, "malware"))

    # From regex extraction
    for m in _RE_IP_PUBLIC.finditer(text):
        results.append((m.group(), "ip"))
    for m in _RE_SHA256.finditer(text):
        results.append((m.group().lower(), "hash_sha256"))
    for m in _RE_ONION_V3.finditer(text):
        results.append((m.group(), "onion"))
    for m in _RE_ONION_V2.finditer(text):
        results.append((m.group(), "onion"))

    # Deduplicate while preserving order
    seen: set[tuple[str, str]] = set()
    unique: list[tuple[str, str]] = []
    for item in results:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


# ---------------------------------------------------------------------------
# IOCGraph
# ---------------------------------------------------------------------------


class IOCGraph:
    """
    Kuzu-backed IOC entity graph with async-safe operations.

    Single-thread Kuzu executor ensures thread safety.
    Idempotent upsert via MATCH→CREATE/SET (Kuzu has no MERGE).
    Fail-open: any Kuzu error logs a warning and returns None/empty.
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        if db_path is None:
            _KUZU_DB_ROOT.mkdir(parents=True, exist_ok=True)
            db_path = _KUZU_DB_ROOT / _IOC_GRAPH_FILENAME
        self._db_path: Path = Path(db_path)
        self._db: Optional[kuzu.Database] = None
        self._conn: Optional[kuzu.Connection] = None
        self._executor: ThreadPoolExecutor = _DB_EXECUTOR
        self._closed: bool = False

        # Sprint 8SA: Write buffers — accumulate in ACTIVE, flush in WINDUP
        # Format: (ioc_type, value, confidence)
        self._ioc_buffer: list[tuple[str, str, float]] = []
        # Format: (id_a, id_b, finding_id, ts, source_type)
        self._obs_buffer: list[tuple[str, str, str, float, str]] = []
        self._BUFFER_FLUSH_SIZE: int = 500

    # -------------------------------------------------------------------------
    # Write Buffer — Sprint 8SA
    # -------------------------------------------------------------------------

    async def buffer_ioc(
        self, ioc_type: str, value: str, confidence: float = 1.0
    ) -> None:
        """
        Add IOC to in-memory buffer — ZERO Kuzu I/O in ACTIVE phase.
        Flush automatically when buffer reaches _BUFFER_FLUSH_SIZE.

        After close() the buffer is closed: new writes are silently dropped
        so no buffered data can be lost or observed in an inconsistent state.
        """
        if self._closed:
            return
        self._ioc_buffer.append((ioc_type, value, confidence))
        if len(self._ioc_buffer) >= self._BUFFER_FLUSH_SIZE:
            await self.flush_buffers()

    async def buffer_observation(
        self,
        id_a: str,
        id_b: str,
        finding_id: str,
        ts: float,
        source_type: str,
    ) -> None:
        """
        Add observation to in-memory buffer — ZERO Kuzu I/O in ACTIVE phase.

        After close() the buffer is closed: new writes are silently dropped.
        """
        if self._closed:
            return
        self._obs_buffer.append((id_a, id_b, finding_id, ts, source_type))

    async def flush_buffers(self) -> dict[str, int]:
        """
        Bulk flush both buffers to Kuzu — call in WINDUP or at buffer limit.

        Returns:
            ioc_created: count of IOC nodes NEWLY CREATED in this flush.
                         IOCs that already existed are updated (last_seen bump)
                         but NOT counted here. Call graph_stats() for total count.
            obs_flushed: count of observation edges written to the graph.
        """
        if not self._ioc_buffer and not self._obs_buffer:
            return {"ioc_created": 0, "obs_flushed": 0}

        # Copy and clear buffers atomically.
        # _closed is NOT checked here so close()-from-WINDUP can still flush.
        # After close() is set, buffer_ioc()/buffer_observation() will refuse
        # to enqueue new items (see those methods), so no new writes can race
        # in after this point.
        ioc_copy = self._ioc_buffer[:]
        obs_copy = self._obs_buffer[:]
        self._ioc_buffer.clear()
        self._obs_buffer.clear()

        ioc_created: list[str] = []
        obs_recorded: int = 0
        try:
            if ioc_copy:
                ioc_created = await self.upsert_ioc_batch(ioc_copy)
            if obs_copy:
                await self._record_observation_batch_sync_async(obs_copy)
                obs_recorded = len(obs_copy)
        except Exception as e:
            import logging
            logging.warning(f"[IOCGraph] flush_buffers failed: {e}")

        import logging
        logging.info(
            f"[IOCGraph] Buffer flushed: {len(ioc_created)} IOCs newly created, "
            f"{obs_recorded} observations"
        )
        return {"ioc_created": len(ioc_created), "obs_flushed": obs_recorded}

    async def _record_observation_batch_sync_async(
        self, obs: list[tuple[str, str, str, float, str]]
    ) -> None:
        """Async wrapper — runs sync impl on _executor thread via run_in_executor."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._executor, self._record_observation_batch_sync, obs)

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    async def initialize(self) -> None:
        """Create schema if not exists (try/except for already-exists)."""
        if self._closed:
            return
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(self._executor, self._init_schema_sync)
        except Exception as e:
            import logging

            logging.warning(f"[IOCGraph] initialize failed: {e}")

    def _init_schema_sync(self) -> None:
        """Synchronous schema init — runs on _executor thread."""
        self._db = kuzu.Database(str(self._db_path))
        self._conn = kuzu.Connection(self._db)

        try:
            self._conn.execute(
                "CREATE NODE TABLE IOC("
                "id STRING PRIMARY KEY, ioc_type STRING, value STRING, "
                "first_seen DOUBLE, last_seen DOUBLE, confidence DOUBLE)"
            )
        except Exception:
            pass

        # Kuzu 0.11+ REL TABLE syntax: FROM node TO node WITH properties
        try:
            self._conn.execute(
                "CREATE REL TABLE OBSERVED("
                "FROM IOC TO IOC, "
                "finding_id STRING, source_type STRING, "
                "first_seen DOUBLE, last_seen DOUBLE)"
            )
        except Exception:
            pass

    async def close(self) -> None:
        """Gracefully close the Kuzu connection.

        Flushes any pending IOC and observation buffers before shutdown
        to prevent silent data loss when close() is called without
        an intervening WINDUP phase.

        close() is idempotent and data-safe: pending buffered writes are
        flushed BEFORE _closed is set to True, so no buffered IOC or
        observation data is lost on normal shutdown.
        """
        if self._closed:
            return
        loop = asyncio.get_running_loop()
        try:
            # Flush pending buffers BEFORE setting _closed.
            # This ensures buffer_ioc()/buffer_observation() calls that
            # race with close() are still honoured.
            await self.flush_buffers()
        except Exception:
            pass
        self._closed = True
        try:
            await loop.run_in_executor(self._executor, self._close_sync)
        except Exception:
            pass

    def _close_sync(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        if self._db is not None:
            self._db.close()
            self._db = None

    # -------------------------------------------------------------------------
    # IOC Operations
    # -------------------------------------------------------------------------

    async def upsert_ioc(
        self,
        ioc_type: str,
        value: str,
        confidence: float = 1.0,
    ) -> Optional[str]:
        """
        Idempotent upsert of an IOC node.

        Uses MATCH→CREATE/SET pattern (Kuzu has no MERGE).
        Returns the IOC id or None on failure.
        """
        if self._closed or self._conn is None:
            return None
        loop = asyncio.get_running_loop()
        node_id = _make_ioc_id(ioc_type, value)
        now = time.time()
        try:
            return await loop.run_in_executor(
                self._executor,
                self._upsert_ioc_sync,
                node_id,
                ioc_type,
                value,
                confidence,
                now,
            )
        except Exception as e:
            import logging

            logging.warning(f"[IOCGraph] upsert_ioc failed: {e}")
            return None

    def _upsert_ioc_sync(
        self,
        node_id: str,
        ioc_type: str,
        value: str,
        confidence: float,
        now: float,
    ) -> str:
        """Synchronous upsert — runs on _executor thread."""
        conn = self._conn
        assert conn is not None

        res = conn.execute(
            "MATCH (n:IOC) WHERE n.id = $id RETURN n.first_seen",
            {"id": node_id},
        )
        if not res.has_next():
            # No match — create new node
            conn.execute(
                "CREATE (:IOC {id: $id, ioc_type: $t, value: $v, "
                "first_seen: $ts, last_seen: $ts, confidence: $c})",
                {"id": node_id, "t": ioc_type, "v": value, "ts": now, "c": confidence},
            )
        else:
            # Node exists — update last_seen
            conn.execute(
                "MATCH (n:IOC) WHERE n.id = $id SET n.last_seen = $ts",
                {"id": node_id, "ts": now},
            )
        return node_id

    async def record_observation(
        self,
        ioc_id_a: str,
        ioc_id_b: str,
        finding_id: str,
        ts: float,
        source_type: str,
    ) -> None:
        """
        Record an OBSERVED edge between two IOC nodes.

        Idempotent: if the edge already exists, updates last_seen on the edge.
        """
        if self._closed or self._conn is None:
            return
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                self._executor,
                self._record_observation_sync,
                ioc_id_a,
                ioc_id_b,
                finding_id,
                ts,
                source_type,
            )
        except Exception as e:
            import logging

            logging.warning(f"[IOCGraph] record_observation failed: {e}")

    def _record_observation_sync(
        self,
        ioc_id_a: str,
        ioc_id_b: str,
        finding_id: str,
        ts: float,
        source_type: str,
    ) -> None:
        """Synchronous observation record — runs on _executor thread."""
        conn = self._conn
        assert conn is not None

        res = conn.execute(
            "MATCH (a:IOC)-[r:OBSERVED]->(b:IOC) "
            "WHERE a.id = $ida AND b.id = $idb "
            "RETURN r.first_seen",
            {"ida": ioc_id_a, "idb": ioc_id_b},
        )
        if not res.has_next():
            conn.execute(
                "MATCH (a:IOC), (b:IOC) "
                "WHERE a.id = $ida AND b.id = $idb "
                "CREATE (a)-[r:OBSERVED {finding_id: $fid, source_type: $st, "
                "first_seen: $ts, last_seen: $ts}]->(b)",
                {
                    "ida": ioc_id_a,
                    "idb": ioc_id_b,
                    "fid": finding_id,
                    "st": source_type,
                    "ts": ts,
                },
            )
        else:
            conn.execute(
                "MATCH (a:IOC)-[r:OBSERVED]->(b:IOC) "
                "WHERE a.id = $ida AND b.id = $idb "
                "SET r.last_seen = $ts",
                {"ida": ioc_id_a, "idb": ioc_id_b, "ts": ts},
            )

    # -------------------------------------------------------------------------
    # Batch Upsert (Sprint 8RA)
    #
    # CANONICAL SEMANTICS (Sprint 8TD):
    #   upsert_ioc_batch(iocs) -> list of NEWLY CREATED node IDs only.
    #   Running twice with same inputs: first call returns N created IDs,
    #   second call returns [] (all nodes already exist).
    #   Use graph_stats() if you need total node count.
    #
    #   flush_buffers() uses this to report 'ioc_flushed' = newly created count.
    # -------------------------------------------------------------------------

    async def upsert_ioc_batch(
        self,
        iocs: list[tuple[str, str, float]],
    ) -> list[str]:
        """
        Batch upsert of IOC nodes.

        Args:
            iocs: list of (ioc_type, value, confidence) tuples.
        Returns:
            List of node IDs newly created in this batch.
            Duplicate calls with the same inputs return [] on subsequent calls.
        """
        if self._closed or self._conn is None or not iocs:
            return []
        loop = asyncio.get_running_loop()
        node_ids = [_make_ioc_id(t, v) for t, v, _ in iocs]
        now = time.time()
        try:
            return await loop.run_in_executor(
                self._executor,
                self._upsert_ioc_batch_sync,
                node_ids,
                iocs,
                now,
            )
        except Exception as e:
            import logging
            logging.warning(f"[IOCGraph] upsert_ioc_batch failed: {e}")
            return []

    def _upsert_ioc_batch_sync(
        self,
        node_ids: list[str],
        iocs: list[tuple[str, str, float]],
        now: float,
    ) -> list[str]:
        """Synchronous batch upsert — runs on _executor thread."""
        conn = self._conn
        assert conn is not None
        created: list[str] = []
        for node_id, (ioc_type, value, confidence) in zip(node_ids, iocs):
            res = conn.execute(
                "MATCH (n:IOC) WHERE n.id = $id RETURN n.first_seen",
                {"id": node_id},
            )
            if not res.has_next():
                conn.execute(
                    "CREATE (:IOC {id: $id, ioc_type: $t, value: $v, "
                    "first_seen: $ts, last_seen: $ts, confidence: $c})",
                    {"id": node_id, "t": ioc_type, "v": value, "ts": now, "c": confidence},
                )
                created.append(node_id)
            else:
                conn.execute(
                    "MATCH (n:IOC) WHERE n.id = $id SET n.last_seen = $ts",
                    {"id": node_id, "ts": now},
                )
        return created

    # -------------------------------------------------------------------------
    # Batch Observation
    # -------------------------------------------------------------------------

    async def record_observation_batch(
        self,
        observations: list[tuple[str, str, str, float, str]],
    ) -> None:
        """
        Batch record of OBSERVED edges between IOC nodes.

        Args:
            observations: List of (ioc_id_a, ioc_id_b, finding_id, ts, source_type).
        Idempotent: duplicate edges update last_seen only.
        """
        if self._closed or self._conn is None or not observations:
            return
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            self._executor,
            self._record_observation_batch_sync,
            observations,
        )

    def _record_observation_batch_sync(
        self,
        observations: list[tuple[str, str, str, float, str]],
    ) -> None:
        """Synchronous batch observation — runs on _executor thread."""
        conn = self._conn
        assert conn is not None

        for ioc_id_a, ioc_id_b, fid, ts, src in observations:
            res = conn.execute(
                "MATCH (a:IOC)-[r:OBSERVED]->(b:IOC) "
                "WHERE a.id = $ida AND b.id = $idb "
                "RETURN r.first_seen",
                {"ida": ioc_id_a, "idb": ioc_id_b},
            )
            if not res.has_next():
                try:
                    conn.execute(
                        "MATCH (a:IOC), (b:IOC) "
                        "WHERE a.id = $ida AND b.id = $idb "
                        "CREATE (a)-[r:OBSERVED {finding_id: $fid, source_type: $st, "
                        "first_seen: $ts, last_seen: $ts}]->(b)",
                        {"ida": ioc_id_a, "idb": ioc_id_b, "fid": fid, "st": src, "ts": ts},
                    )
                except Exception:
                    # duplicate edge race — ignore
                    pass
            else:
                conn.execute(
                    "MATCH (a:IOC)-[r:OBSERVED]->(b:IOC) "
                    "WHERE a.id = $ida AND b.id = $idb "
                    "SET r.last_seen = $ts",
                    {"ida": ioc_id_a, "idb": ioc_id_b, "ts": ts},
                )

    # -------------------------------------------------------------------------
    # Pivot Query
    # -------------------------------------------------------------------------

    async def pivot(
        self,
        ioc_value: str,
        ioc_type: str,
        depth: int = 2,
    ) -> list[dict[str, Any]]:
        """
        Find IOC nodes connected to the given IOC up to *depth* hops.

        Kuzu: MATCH (n:IOC)-[r*1..2]-(m:IOC)
              WHERE n.value=$v AND n.ioc_type=$t RETURN m, r

        Returns list of dicts: id, ioc_type, value, confidence, first_seen, last_seen.
        """
        if self._closed or self._conn is None:
            return []
        loop = asyncio.get_running_loop()
        depth_clamped = max(1, min(depth, 2))
        try:
            return await loop.run_in_executor(
                self._executor,
                self._pivot_sync,
                ioc_value,
                ioc_type,
                depth_clamped,
            )
        except Exception as e:
            import logging

            logging.warning(f"[IOCGraph] pivot failed: {e}")
            return []

    def _pivot_sync(
        self,
        ioc_value: str,
        ioc_type: str,
        depth: int,
    ) -> list[dict[str, Any]]:
        """Synchronous pivot — runs on _executor thread."""
        conn = self._conn
        assert conn is not None

        results: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        query = (
            f"MATCH (n:IOC)-[r*1..{depth}]-(m:IOC) "
            "WHERE n.value = $v AND n.ioc_type = $t AND n.id <> m.id "
            "RETURN m.id AS id, m.ioc_type AS ioc_type, m.value AS value, "
            "m.confidence AS confidence, m.first_seen AS first_seen, "
            "m.last_seen AS last_seen"
        )
        res = conn.execute(query, {"v": ioc_value, "t": ioc_type})
        while res.has_next():
            row = res.get_next()
            # row is a list of column values
            col_names = res.get_column_names()
            node_data: dict[str, Any] = dict(zip(col_names, row))
            nid = node_data.get("id", "")
            if nid and nid not in seen_ids:
                seen_ids.add(nid)
                results.append(node_data)
        return results

    # -------------------------------------------------------------------------
    # Stats
    # -------------------------------------------------------------------------

    async def graph_stats(self) -> dict[str, int]:
        """Return total node and edge counts."""
        if self._closed or self._conn is None:
            return {"nodes": 0, "edges": 0}
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(
                self._executor, self._graph_stats_sync
            )
        except Exception as e:
            import logging

            logging.warning(f"[IOCGraph] graph_stats failed: {e}")
            return {"nodes": 0, "edges": 0}

    def _graph_stats_sync(self) -> dict[str, int]:
        """Synchronous stats — runs on _executor thread."""
        conn = self._conn
        assert conn is not None

        nodes = 0
        try:
            res = conn.execute("MATCH (n:IOC) RETURN count(n)")
            row = res.get_next()
            nodes = int(row[0]) if row else 0
        except Exception:
            pass

        edges = 0
        try:
            res = conn.execute("MATCH ()-[r:OBSERVED]->() RETURN count(r)")
            row = res.get_next()
            edges = int(row[0]) if row else 0
        except Exception:
            pass

        return {"nodes": nodes, "edges": edges}

    # -------------------------------------------------------------------------
    # STIX 2.1 Real Export
    # -------------------------------------------------------------------------

    async def export_stix_bundle(self) -> list[dict[str, Any]]:
        """
        Export all IOC nodes as STIX 2.1 objects.

        Validates the bundle via stix2.parse() — returns empty list on failure.
        """
        if self._closed or self._conn is None:
            return []
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(
                self._executor, self._export_stix_bundle_sync
            )
        except Exception as e:
            import logging
            logging.warning(f"[IOCGraph] export_stix_bundle failed: {e}")
            return []

    def _export_stix_bundle_sync(self) -> list[dict[str, Any]]:
        """Synchronous STIX 2.1 export — runs on _executor thread."""
        import stix2

        conn = self._conn
        assert conn is not None

        objects: list[dict[str, Any]] = []
        try:
            res = conn.execute(
                "MATCH (n:IOC) RETURN n.id, n.ioc_type, n.value, "
                "n.confidence, n.first_seen ORDER BY n.first_seen DESC"
            )
            while res.has_next():
                row = res.get_next()
                node_id, ioc_type, value, confidence, first_seen = (
                    row[0], row[1], row[2], row[3], row[4]
                )
                valid_from = datetime.fromtimestamp(first_seen or 0, tz=timezone.utc)
                conf = int((confidence or 1.0) * 100)

                try:
                    if ioc_type == "ip":
                        obj = stix2.Indicator(
                            id=f"indicator--{uuid.uuid5(uuid.NAMESPACE_URL, node_id)}",
                            name=f"IP: {value}",
                            pattern=f"[ipv4-addr:value = '{value}']",
                            pattern_type="stix",
                            valid_from=valid_from,
                            confidence=conf,
                        )
                    elif ioc_type == "domain":
                        obj = stix2.Indicator(
                            id=f"indicator--{uuid.uuid5(uuid.NAMESPACE_URL, node_id)}",
                            name=f"Domain: {value}",
                            pattern=f"[domain-name:value = '{value}']",
                            pattern_type="stix",
                            valid_from=valid_from,
                            confidence=conf,
                        )
                    elif ioc_type == "hash_sha256":
                        obj = stix2.Indicator(
                            id=f"indicator--{uuid.uuid5(uuid.NAMESPACE_URL, node_id)}",
                            name=f"SHA256: {value[:16]}...",
                            pattern=f"[file:hashes.'SHA-256' = '{value}']",
                            pattern_type="stix",
                            valid_from=valid_from,
                            confidence=conf,
                        )
                    elif ioc_type == "cve":
                        obj = stix2.Vulnerability(
                            id=f"vulnerability--{uuid.uuid5(uuid.NAMESPACE_URL, node_id)}",
                            name=value,
                            external_references=[
                                {"source_name": "cve", "external_id": value}
                            ],
                        )
                    elif ioc_type in ("onion",) or (".onion" in value):
                        obj = stix2.Indicator(
                            id=f"indicator--{uuid.uuid5(uuid.NAMESPACE_URL, node_id)}",
                            name=f"Onion: {value}",
                            pattern=f"[url:value = 'http://{value}/']",
                            pattern_type="stix",
                            valid_from=valid_from,
                            confidence=conf,
                        )
                    else:
                        continue
                    objects.append(json.loads(obj.serialize()))
                except Exception as e:
                    import logging
                    logging.warning(f"STIX build failed for {node_id}: {e}")
                    continue
        except Exception as e:
            import logging
            logging.warning(f"STIX export query failed: {e}")

        # B7: validate bundle before returning
        if objects:
            try:
                bundle = stix2.Bundle(objects=objects)
                stix2.parse(bundle.serialize())
            except Exception as e:
                import logging
                logging.warning(f"STIX bundle validation warning: {e}")
                objects = []

        return objects
