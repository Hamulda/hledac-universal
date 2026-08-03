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
import asyncio
import math
import orjson
import re
import time
import uuid
from concurrent.futures import as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
import xxhash

from hledac.universal.brain.jtms import JTMS, Justification, apply_temporal_decay
_KUZU_AVAILABLE: bool = False
_kuzu = None
try:
    import kuzu as _kuzu
    _KUZU_AVAILABLE = True
except ImportError:
    _kuzu = None

_PYARROW_AVAILABLE: bool = False
_pa = None
try:
    import pyarrow as _pa
    _PYARROW_AVAILABLE = True
except ImportError:
    _pa = None

class GraphBackendUnavailableError(Exception):
    """Raised when a required graph backend (kuzu) is not installed."""
    pass
GraphBackendUnavailable = GraphBackendUnavailableError
_KUZU_DB_ROOT: Path = Path.home() / '.hledac' / 'kuzu'
_IOC_GRAPH_FILENAME: str = 'ioc_graph'
IOC_TYPES: frozenset[str] = frozenset(('cve', 'ip', 'hash_sha256', 'hash_md5', 'onion', 'i2p', 'domain', 'apt', 'malware', 'info_hash', 'magnet_uri', 'threat_actor', 'malware_family'))
_RE_IP_PUBLIC = re.compile('\\b(?!10\\.|127\\.|169\\.254\\.|172\\.(?:1[6-9]|2\\d|3[01])\\.|192\\.168\\.)(?:\\d{1,3}\\.){3}\\d{1,3}\\b')
_RE_SHA256 = re.compile('\\b[0-9a-fA-F]{64}\\b')
_RE_ONION_V3 = re.compile('\\b[a-z2-7]{56}\\.onion\\b')
_RE_ONION_V2 = re.compile('\\b[a-z2-7]{16}\\.onion\\b')

def _make_ioc_id(ioc_type: str, value: str) -> str:
    """Generate a deterministic 64-bit hex ID for an IOC."""
    return f'{ioc_type}:{xxhash.xxh64(value.encode()).hexdigest()}'

def extract_iocs_from_text(text: str, pattern_matches: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """
    Extract IOCs from raw text and PatternMatcher hits.

    Returns list of (value, ioc_type) tuples, deduplicated.
    Private/routable IPs are filtered out.
    """
    results: list[tuple[str, str]] = []
    for match_value, label in pattern_matches:
        if label == 'vulnerability_id':
            results.append((match_value, 'cve'))
        elif label == 'offensive_tool':
            results.append((match_value, 'malware'))
        elif label == 'attack_technique':
            results.append((match_value, 'apt'))
        elif label == 'ransomware_group':
            results.append((match_value, 'malware'))
    for m in _RE_IP_PUBLIC.finditer(text):
        results.append((m.group(), 'ip'))
    for m in _RE_SHA256.finditer(text):
        results.append((m.group().lower(), 'hash_sha256'))
    for m in _RE_ONION_V3.finditer(text):
        results.append((m.group(), 'onion'))
    for m in _RE_ONION_V2.finditer(text):
        results.append((m.group(), 'onion'))
    seen: set[tuple[str, str]] = set()
    unique: list[tuple[str, str]] = []
    for item in results:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


class IOCGraph:
    """
    Kuzu-backed IOC entity graph with async-safe operations.

    GRAPH TRUTH STORE — owns authoritative IOC entity storage.
    - buffer_ioc(), flush_buffers(), upsert_ioc_batch(), export_stix_bundle(), pivot()
    - NOT analytics backend — DuckPGQGraph serves that role.

    BLITZ-08: Supports in-memory mode (memory_mode=True) for zero-disk-I/O
    sprint ACTIVE phase. Use persist_to_disk() during TEARDOWN to export
    the in-memory graph to a file-backed Kuzu database.

    JTMS INTEGRATION (APEX-1003):
    - Maintains in-memory JTMS for justification tracking
    - Supports retract_source() to remove all facts from a source
    - Applies temporal decay to confidence at flush_buffers()
    """
    __slots__ = tuple(('_BUFFER_FLUSH_SIZE', '_closed', '_conn', '_db', '_db_path',
                       '_executor', '_ioc_buffer', '_is_memory_mode', '_obs_buffer',
                       '_jtms', '_decay_lambda'))

    def __init__(self, db_path: Path | None=None, decay_lambda: float = 0.01,
                 *, memory_mode: bool = False) -> None:
        """Initialize IOCGraph with optional in-memory mode (BLITZ-08).

        Args:
            db_path: Path to Kuzu database directory. If None, defaults to
                     ~/.hledac/kuzu/ioc_graph. Ignored when memory_mode=True.
            decay_lambda: Temporal decay rate for JTMS confidence scores.
            memory_mode: When True, Kuzu database lives entirely in RAM
                         (":memory:" path). No disk I/O on flush_buffers().
                         At TEARDOWN, call persist_to_disk() to export.
                         M1 8GB safe: Kuzu :memory: is page-backed, not
                         fully resident — ~10-50 MB for typical sprint IOCs.
        """
        if not _KUZU_AVAILABLE:
            raise GraphBackendUnavailable('kuzu is not installed. Install via: pip install hledac-universal[kuzu-graph]')
        self._is_memory_mode: bool = memory_mode
        if memory_mode:
            self._db_path = Path(':memory:')
        elif db_path is None:
            _KUZU_DB_ROOT.mkdir(parents=True, exist_ok=True)
            db_path = _KUZU_DB_ROOT / _IOC_GRAPH_FILENAME
            self._db_path = Path(db_path)
        else:
            self._db_path = Path(db_path)
        self._db: Any | None = None
        self._conn: Any | None = None
        self._closed: bool = False
        self._ioc_buffer: list[tuple[str, str, float]] = []
        self._obs_buffer: list[tuple[str, str, str, float, str]] = []
        self._BUFFER_FLUSH_SIZE: int = 500
        self._jtms: JTMS = JTMS()
        self._decay_lambda: float = decay_lambda

    async def buffer_ioc(self, ioc_type: str, value: str, confidence: float=1.0) -> None:
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

    async def buffer_observation(self, id_a: str, id_b: str, finding_id: str, ts: float, source_type: str) -> None:
        """
        Add observation to in-memory buffer — ZERO Kuzu I/O in ACTIVE phase.

        After close() the buffer is closed: new writes are silently dropped.
        """
        if self._closed:
            return
        self._obs_buffer.append((id_a, id_b, finding_id, ts, source_type))

    async def buffer_ioc_with_justification(
        self,
        ioc_type: str,
        value: str,
        confidence: float,
        source_ids: list[str] | tuple[str, ...],
        inference_rule: str = "manual",
        source_reliability: float = 1.0,
    ) -> str | None:
        """
        Buffer IOC with JTMS justification tracking.

        Creates a justification in the in-memory JTMS before buffering.
        When flush_buffers() runs, temporal decay is applied to confidence.

        Args:
            ioc_type: IOC type (ip, domain, hash_sha256, etc.)
            value: IOC value
            confidence: Base confidence (0..1) before temporal decay
            source_ids: List of source identifiers supporting this fact
            inference_rule: Algorithm that derived this fact (default: "manual")
            source_reliability: Aggregate source reliability (0..1)

        Returns:
            fact_id: JTMS fact identifier for later retraction, or None if closed
        """
        if self._closed:
            return None

        # Generate IOC ID for JTMS tracking
        ioc_id = _make_ioc_id(ioc_type, value)

        # Add fact to JTMS with justification
        fact_id = self._jtms.add_fact(
            ioc_id=ioc_id,
            source_ids=source_ids,
            inference_rule=inference_rule,
            confidence=confidence,
            source_reliability=source_reliability,
        )

        # Buffer for flush (temporal decay applied at flush time)
        self._ioc_buffer.append((ioc_type, value, confidence))
        if len(self._ioc_buffer) >= self._BUFFER_FLUSH_SIZE:
            await self.flush_buffers()

        return fact_id

    async def retract_source(self, source_id: str) -> dict[str, int]:
        """
        Retract all facts justified by a specific source.

        Finds all facts where source_id ∈ justification.source_ids,
        marks them inactive in JTMS, and updates confidence in Kuzu.

        Args:
            source_id: Source identifier to retract

        Returns:
            dict with:
                - facts_retracted: Number of JTMS facts retracted
                - iocs_updated: Number of IOC nodes updated in Kuzu
        """
        if self._closed or self._conn is None:
            return {'facts_retracted': 0, 'iocs_updated': 0}

        # Retract from JTMS
        facts_retracted = self._jtms.retract_source(source_id)

        if facts_retracted == 0:
            return {'facts_retracted': 0, 'iocs_updated': 0}

        # Recompute confidence for affected IOCs
        iocs_updated = await self._recompute_ioc_confidences()

        import logging
        logging.info(f'[IOCGraph] retract_source({source_id}): {facts_retracted} facts retracted, {iocs_updated} IOCs updated')

        return {'facts_retracted': facts_retracted, 'iocs_updated': iocs_updated}

    async def _recompute_ioc_confidences(self) -> int:
        """
        Recompute IOC confidences from active JTMS facts.

        For each IOC with multiple facts, aggregates confidence from
        all active justifications. Updates Kuzu nodes.

        Returns:
            iocs_updated: Number of IOC nodes updated
        """
        if self._conn is None:
            return 0

        # Collect all active facts grouped by IOC
        ioc_confidences: dict[str, list[float]] = {}
        for fact_data in self._jtms._facts.values():
            if not fact_data['active']:
                continue
            ioc_id = fact_data['ioc_id']
            confidence = fact_data['confidence']
            if ioc_id not in ioc_confidences:
                ioc_confidences[ioc_id] = []
            ioc_confidences[ioc_id].append(confidence)

        # Update Kuzu nodes with aggregated confidence
        iocs_updated = 0
        now = time.time()
        for ioc_id, confidences in ioc_confidences.items():
            # Aggregate: max confidence (simple strategy)
            # Could be enhanced with weighted average, DST fusion, etc.
            aggregated_conf = max(confidences) if confidences else 0.0

            try:
                await asyncio.to_thread(
                    self._update_ioc_confidence_sync,
                    ioc_id, aggregated_conf, now
                )
                iocs_updated += 1
            except Exception as e:
                import logging
                logging.warning(f'[IOCGraph] Failed to update confidence for {ioc_id}: {e}')

        return iocs_updated

    def _update_ioc_confidence_sync(self, ioc_id: str, confidence: float, now: float) -> None:
        """Synchronous IOC confidence update — runs on executor thread."""
        conn = self._conn
        assert conn is not None
        conn.execute(
            'MATCH (n:IOC) WHERE n.id = $id SET n.confidence = $c, n.last_seen = $ts',
            {'id': ioc_id, 'c': confidence, 'ts': now}
        )

    async def flush_buffers(self) -> dict[str, int]:
        """
        Bulk flush both buffers to Kuzu — call in WINDUP or at buffer limit.

        JTMS INTEGRATION: Applies temporal decay to confidence scores before
        flushing to Kuzu. Decay formula: conf * exp(-λ * Δt_hours)

        Returns:
            ioc_created: count of IOC nodes NEWLY CREATED in this flush.
                         IOCs that already existed are updated (last_seen bump)
                         but NOT counted here. Call graph_stats() for total count.
            obs_flushed: count of observation edges written to the graph.
        """
        if not self._ioc_buffer and (not self._obs_buffer):
            return {'ioc_created': 0, 'obs_flushed': 0}
        ioc_copy = self._ioc_buffer[:]
        obs_copy = self._obs_buffer[:]
        self._ioc_buffer.clear()
        self._obs_buffer.clear()

        # Apply temporal decay to IOC confidences if JTMS has facts
        if self._jtms._facts and self._decay_lambda > 0:
            now = time.time()
            decayed_ioc_copy = []
            for ioc_type, value, base_conf in ioc_copy:
                ioc_id = _make_ioc_id(ioc_type, value)
                # Find corresponding JTMS fact
                fact_data = None
                for fact in self._jtms._facts.values():
                    if fact['ioc_id'] == ioc_id and fact['active']:
                        fact_data = fact
                        break

                if fact_data:
                    # Apply temporal decay
                    justification = fact_data['justification']
                    decayed_conf = apply_temporal_decay(
                        base_conf,
                        justification.timestamp,
                        self._decay_lambda,
                        now
                    )
                    decayed_ioc_copy.append((ioc_type, value, decayed_conf))
                else:
                    # No JTMS fact, use base confidence
                    decayed_ioc_copy.append((ioc_type, value, base_conf))
            ioc_copy = decayed_ioc_copy

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
            logging.warning(f'[IOCGraph] flush_buffers failed: {e}')
        import logging
        logging.info(f'[IOCGraph] Buffer flushed: {len(ioc_created)} IOCs newly created, {obs_recorded} observations')
        return {'ioc_created': len(ioc_created), 'obs_flushed': obs_recorded}

    async def _record_observation_batch_sync_async(self, obs: list[tuple[str, str, str, float, str]]) -> None:
        """Async wrapper — runs sync impl on background thread via asyncio.to_thread."""
        await asyncio.to_thread(self._record_observation_batch_sync, obs)

    async def initialize(self) -> None:
        """Create schema if not exists (try/except for already-exists)."""
        if self._closed:
            return
        try:
            await asyncio.to_thread(self._init_schema_sync)
        except Exception as e:
            import logging
            logging.warning(f'[IOCGraph] initialize failed: {e}')

    def _init_schema_sync(self) -> None:
        """Synchronous schema init — runs on _executor thread."""
        self._db = _kuzu.Database(str(self._db_path))
        self._conn = _kuzu.Connection(self._db)
        try:
            self._conn.execute('CREATE NODE TABLE IOC(id STRING PRIMARY KEY, ioc_type STRING, value STRING, first_seen DOUBLE, last_seen DOUBLE, confidence DOUBLE)')
        except Exception:
            pass
        try:
            self._conn.execute('CREATE REL TABLE OBSERVED(FROM IOC TO IOC, finding_id STRING, source_type STRING, first_seen DOUBLE, last_seen DOUBLE)')
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

        try:
            await self.flush_buffers()
        except Exception:
            pass
        self._closed = True
        try:
            await asyncio.to_thread(self._close_sync)
        except Exception:
            pass

    def _close_sync(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        if self._db is not None:
            self._db.close()
            self._db = None

    @property
    def is_memory_mode(self) -> bool:
        """BLITZ-08: True if this graph operates in :memory: mode (no disk I/O)."""
        return self._is_memory_mode

    async def persist_to_disk(self, target_path: Path) -> int:
        """BLITZ-08: Export in-memory graph to a file-backed Kuzu database.

        Creates a new Kuzu database at target_path, copies the schema
        (IOC node table + OBSERVED rel table), then bulk-copies all nodes
        and edges from the in-memory graph.

        Call this during sprint TEARDOWN to persist the in-memory graph
        before the :memory: database is destroyed on close().

        Args:
            target_path: Directory path for the new file-backed Kuzu DB.
                        Parent directories are created if needed.

        Returns:
            Total number of nodes + edges copied, or 0 on failure.

        M1 8GB safe: uses sequential COPY via Kuzu Cypher — no buffered
        bulk transfer, O(n) memory for the largest single row.
        """
        if self._closed or self._conn is None:
            logger.warning('[IOCGraph] persist_to_disk: graph is closed, nothing to persist')
            return 0
        if not self._is_memory_mode:
            logger.debug('[IOCGraph] persist_to_disk: already file-backed, no-op')
            return 0

        target_path = Path(target_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if target_path.exists():
            import shutil
            logger.warning(
                '[IOCGraph] persist_to_disk: target %s exists, removing',
                target_path,
            )
            shutil.rmtree(str(target_path), ignore_errors=True)

        total_copied = 0
        target_db: Any = None
        target_conn: Any = None
        try:
            # Create file-backed Kuzu database with identical schema
            target_db = _kuzu.Database(str(target_path))
            target_conn = _kuzu.Connection(target_db)

            # Create schema (idempotent — first time on a fresh DB)
            target_conn.execute(
                'CREATE NODE TABLE IOC('
                'id STRING PRIMARY KEY, '
                'ioc_type STRING, '
                'value STRING, '
                'first_seen DOUBLE, '
                'last_seen DOUBLE, '
                'confidence DOUBLE'
                ')'
            )
        except Exception:
            # Schema already exists (Kuzu raises on duplicate CREATE)
            pass

        try:
            target_conn.execute(
                'CREATE REL TABLE OBSERVED('
                'FROM IOC TO IOC, '
                'finding_id STRING, '
                'source_type STRING, '
                'first_seen DOUBLE, '
                'last_seen DOUBLE'
                ')'
            )
        except Exception:
            pass

        try:
            # Copy IOC nodes — MATCH all, CREATE in target
            ioc_res = self._conn.execute(
                'MATCH (n:IOC) RETURN n.id, n.ioc_type, n.value, '
                'n.first_seen, n.last_seen, n.confidence'
            )
            ioc_count = 0
            while ioc_res.has_next():
                row = ioc_res.get_next()
                target_conn.execute(
                    'CREATE (:IOC {'
                    'id: $id, ioc_type: $t, value: $v, '
                    'first_seen: $fs, last_seen: $ls, confidence: $c'
                    '})',
                    {
                        'id': row[0], 't': row[1], 'v': row[2],
                        'fs': row[3], 'ls': row[4], 'c': row[5],
                    },
                )
                ioc_count += 1
            total_copied += ioc_count
            logger.info('[IOCGraph] persist_to_disk: %d IOC nodes copied', ioc_count)

            # Copy OBSERVED edges — MATCH with endpoints, CREATE in target
            obs_res = self._conn.execute(
                'MATCH (a:IOC)-[r:OBSERVED]->(b:IOC) '
                'RETURN a.id, b.id, r.finding_id, r.source_type, '
                'r.first_seen, r.last_seen'
            )
            obs_count = 0
            while obs_res.has_next():
                row = obs_res.get_next()
                target_conn.execute(
                    'MATCH (a:IOC {id: $aid}), (b:IOC {id: $bid}) '
                    'CREATE (a)-[:OBSERVED {'
                    'finding_id: $fid, source_type: $st, '
                    'first_seen: $fs, last_seen: $ls'
                    '}]->(b)',
                    {
                        'aid': row[0], 'bid': row[1],
                        'fid': row[2], 'st': row[3],
                        'fs': row[4], 'ls': row[5],
                    },
                )
                obs_count += 1
            total_copied += obs_count
            logger.info('[IOCGraph] persist_to_disk: %d OBSERVED edges copied', obs_count)

        except Exception as exc:
            logger.error('[IOCGraph] persist_to_disk failed: %s', exc)
            return 0
        finally:
            if target_conn is not None:
                try:
                    target_conn.close()
                except Exception:
                    pass
            if target_db is not None:
                try:
                    target_db.close()
                except Exception:
                    pass

        logger.info(
            '[IOCGraph] persist_to_disk: %d total entities written to %s',
            total_copied, target_path,
        )
        return total_copied

    async def upsert_ioc(self, ioc_type: str, value: str, confidence: float=1.0) -> str | None:
        """
        Idempotent upsert of an IOC node.

        Uses MATCH→CREATE/SET pattern (Kuzu has no MERGE).
        Returns the IOC id or None on failure.
        """
        if self._closed or self._conn is None:
            return None

        node_id = _make_ioc_id(ioc_type, value)
        now = time.time()
        try:
            return await asyncio.to_thread(self._upsert_ioc_sync, node_id, ioc_type, value, confidence, now)
        except Exception as e:
            import logging
            logging.warning(f'[IOCGraph] upsert_ioc failed: {e}')
            return None

    def _upsert_ioc_sync(self, node_id: str, ioc_type: str, value: str, confidence: float, now: float) -> str:
        """Synchronous upsert — runs on _executor thread."""
        conn = self._conn
        assert conn is not None
        res = conn.execute('MATCH (n:IOC) WHERE n.id = $id RETURN n.first_seen', {'id': node_id})
        if not res.has_next():
            conn.execute('CREATE (:IOC {id: $id, ioc_type: $t, value: $v, first_seen: $ts, last_seen: $ts, confidence: $c})', {'id': node_id, 't': ioc_type, 'v': value, 'ts': now, 'c': confidence})
        else:
            conn.execute('MATCH (n:IOC) WHERE n.id = $id SET n.last_seen = $ts', {'id': node_id, 'ts': now})
        return node_id

    async def record_observation(self, ioc_id_a: str, ioc_id_b: str, finding_id: str, ts: float, source_type: str) -> None:
        """
        Record an OBSERVED edge between two IOC nodes.

        Idempotent: if the edge already exists, updates last_seen on the edge.
        """
        if self._closed or self._conn is None:
            return

        try:
            await asyncio.to_thread(self._record_observation_sync, ioc_id_a, ioc_id_b, finding_id, ts, source_type)
        except Exception as e:
            import logging
            logging.warning(f'[IOCGraph] record_observation failed: {e}')

    def _record_observation_sync(self, ioc_id_a: str, ioc_id_b: str, finding_id: str, ts: float, source_type: str) -> None:
        """Synchronous observation record — runs on _executor thread."""
        conn = self._conn
        assert conn is not None
        res = conn.execute('MATCH (a:IOC)-[r:OBSERVED]->(b:IOC) WHERE a.id = $ida AND b.id = $idb RETURN r.first_seen', {'ida': ioc_id_a, 'idb': ioc_id_b})
        if not res.has_next():
            conn.execute('MATCH (a:IOC), (b:IOC) WHERE a.id = $ida AND b.id = $idb CREATE (a)-[r:OBSERVED {finding_id: $fid, source_type: $st, first_seen: $ts, last_seen: $ts}]->(b)', {'ida': ioc_id_a, 'idb': ioc_id_b, 'fid': finding_id, 'st': source_type, 'ts': ts})
        else:
            conn.execute('MATCH (a:IOC)-[r:OBSERVED]->(b:IOC) WHERE a.id = $ida AND b.id = $idb SET r.last_seen = $ts', {'ida': ioc_id_a, 'idb': ioc_id_b, 'ts': ts})

    async def upsert_ioc_batch(self, iocs: list[tuple[str, str, float]]) -> list[str]:
        """
        Batch upsert of IOC nodes.

        Args:
            iocs: list of (ioc_type, value, confidence) tuples.
        Returns:
            List of node IDs newly created in this batch.
            Duplicate calls with the same inputs return [] on subsequent calls.
        """
        if self._closed or self._conn is None or (not iocs):
            return []

        node_ids = [_make_ioc_id(t, v) for t, v, _ in iocs]
        now = time.time()
        try:
            return await asyncio.to_thread(self._upsert_ioc_batch_sync, node_ids, iocs, now)
        except Exception as e:
            import logging
            logging.warning(f'[IOCGraph] upsert_ioc_batch failed: {e}')
            return []

    def _upsert_ioc_batch_sync(self, node_ids: list[str], iocs: list[tuple[str, str, float]], now: float) -> list[str]:
        """Synchronous batch upsert — runs on _executor thread.

        N+1 elimination via UNWIND batch queries:
          Phase 1: 1 query — UNWIND batch existence check
          Phase 3: 1 query — UNWIND batch CREATE for new nodes
          Phase 4: 1 query — UNWIND batch SET last_seen for existing nodes
        Total: 3 queries regardless of batch size (was 2N+1).
        """
        conn = self._conn
        assert conn is not None
        if not node_ids:
            return []
        res = conn.execute('UNWIND $ids AS nid MATCH (n:IOC) WHERE n.id = nid RETURN n.id', {'ids': node_ids})
        existing_ids: set[str] = set()
        try:
            while res.has_next():
                row = res.get_next()
                existing_ids.add(row[0])
        except Exception:
            existing_ids = set()
        new_nodes: list[tuple[str, tuple[str, str, float]]] = [(node_id, ioc) for node_id, ioc in zip(node_ids, iocs, strict=False) if node_id not in existing_ids]
        existing_to_update: list[str] = [node_id for node_id, _ in zip(node_ids, iocs, strict=False) if node_id in existing_ids]
        created: list[str] = []
        if new_nodes:
            try:
                data = [{'id': nid, 't': t, 'v': v, 'c': c, 'ts': now} for nid, (t, v, c) in new_nodes]
                conn.execute('UNWIND $data AS row CREATE (:IOC {id: row.id, ioc_type: row.t, value: row.v, first_seen: row.ts, last_seen: row.ts, confidence: row.c})', {'data': data})
                created = [nid for nid, _ in new_nodes]
            except Exception:
                for node_id, (ioc_type, value, confidence) in new_nodes:
                    try:
                        conn.execute('CREATE (:IOC {id: $id, ioc_type: $t, value: $v, first_seen: $ts, last_seen: $ts, confidence: $c})', {'id': node_id, 't': ioc_type, 'v': value, 'ts': now, 'c': confidence})
                        created.append(node_id)
                    except Exception:
                        pass
        if existing_to_update:
            try:
                conn.execute('UNWIND $ids AS nid MATCH (n:IOC) WHERE n.id = nid SET n.last_seen = $ts', {'ids': existing_to_update, 'ts': now})
            except Exception:
                for node_id in existing_to_update:
                    try:
                        conn.execute('MATCH (n:IOC) WHERE n.id = $id SET n.last_seen = $ts', {'id': node_id, 'ts': now})
                    except Exception:
                        pass
        return created

    async def record_observation_batch(self, observations: list[tuple[str, str, str, float, str]]) -> None:
        """
        Batch record of OBSERVED edges between IOC nodes.

        Args:
            observations: List of (ioc_id_a, ioc_id_b, finding_id, ts, source_type).
        Idempotent: duplicate edges update last_seen only.
        """
        if self._closed or self._conn is None or (not observations):
            return

        await asyncio.to_thread(self._record_observation_batch_sync, observations)

    def _record_observation_batch_sync(self, observations: list[tuple[str, str, str, float, str]]) -> None:
        """Synchronous batch observation — runs on _executor thread.

        N+1 elimination via UNWIND batch queries:
          Phase 1: 1 query — UNWIND batch existence check for all edges
          Phase 3: 1 query — UNWIND batch CREATE for missing edges
          Phase 4: 1 query — UNWIND batch SET last_seen for existing edges
        Total: 3 queries regardless of batch size (was 2N+1).
        """
        conn = self._conn
        assert conn is not None
        if not observations:
            return
        obs_pairs: list[list[str]] = [[ioc_id_a, ioc_id_b] for ioc_id_a, ioc_id_b, _, _, _ in observations]
        res = conn.execute('UNWIND $obs AS pair MATCH (a:IOC)-[r:OBSERVED]->(b:IOC) WHERE a.id = pair[0] AND b.id = pair[1] RETURN pair[0], pair[1]', {'obs': obs_pairs})
        existing: set[tuple[str, str]] = set()
        try:
            while res.has_next():
                row = res.get_next()
                existing.add((row[0], row[1]))
        except Exception:
            existing = set()
        missing: list[tuple[str, str, str, float, str]] = [(a, b, f, t, s) for a, b, f, t, s in observations if (a, b) not in existing]
        existing_obs: list[tuple[str, str, float]] = [(a, b, t) for a, b, _, t, _ in observations if (a, b) in existing]
        if missing:
            try:
                data = [{'ida': a, 'idb': b, 'fid': f, 'st': s, 'ts': t} for a, b, f, t, s in missing]
                conn.execute('UNWIND $data AS row MATCH (a:IOC), (b:IOC) WHERE a.id = row.ida AND b.id = row.idb CREATE (a)-[r:OBSERVED {finding_id: row.fid, source_type: row.st, first_seen: row.ts, last_seen: row.ts}]->(b)', {'data': data})
            except Exception:
                for ioc_id_a, ioc_id_b, fid, ts, src in missing:
                    try:
                        conn.execute('MATCH (a:IOC), (b:IOC) WHERE a.id = $ida AND b.id = $idb CREATE (a)-[r:OBSERVED {finding_id: $fid, source_type: $st, first_seen: $ts, last_seen: $ts}]->(b)', {'ida': ioc_id_a, 'idb': ioc_id_b, 'fid': fid, 'st': src, 'ts': ts})
                    except Exception:
                        pass
        if existing_obs:
            try:
                data = [{'ida': a, 'idb': b, 'ts': t} for a, b, t in existing_obs]
                conn.execute('UNWIND $data AS row MATCH (a:IOC)-[r:OBSERVED]->(b:IOC) WHERE a.id = row.ida AND b.id = row.idb SET r.last_seen = row.ts', {'data': data})
            except Exception:
                for ioc_id_a, ioc_id_b, ts in existing_obs:
                    try:
                        conn.execute('MATCH (a:IOC)-[r:OBSERVED]->(b:IOC) WHERE a.id = $ida AND b.id = $idb SET r.last_seen = $ts', {'ida': ioc_id_a, 'idb': ioc_id_b, 'ts': ts})
                    except Exception:
                        pass

    async def pivot(self, ioc_value: str, ioc_type: str, depth: int=2) -> list[dict[str, Any]]:
        """
        Find IOC nodes connected to the given IOC up to *depth* hops.

        Uses Kuzu getAsArrow() → PyArrow zero-copy path when pyarrow is
        available (Kuzu 0.11.3+).  Falls back to row-by-row iteration
        otherwise.  Both paths run on the executor thread.

        Returns list of dicts: id, ioc_type, value, confidence, first_seen, last_seen.
        """
        if self._closed or self._conn is None:
            return []

        depth_clamped = max(1, min(depth, 2))
        try:
            return await asyncio.to_thread(self._pivot_arrow_sync, ioc_value, ioc_type, depth_clamped)
        except Exception as e:
            import logging
            logging.warning(f'[IOCGraph] pivot failed: {e}')
            return []

    def _pivot_arrow_sync(self, ioc_value: str, ioc_type: str, depth: int) -> list[dict[str, Any]]:
        """Arrow-accelerated pivot — Kuzu getAsArrow() → PyArrow zero-copy → to_pylist().

        Kuzu 0.11.3+ exports results via the C Data Interface directly into a
        PyArrow Table.  The per-column Arrow arrays are shared with Kuzu's
        internal buffers — no per-row Python calls, no dict(zip(...)) overhead.
        to_pylist() constructs dicts in C++ inside the Arrow compute layer,
        which is ~10× faster than Python-level row iteration.

        Falls back to _pivot_sync() when pyarrow is not importable at runtime.
        """
        if not _PYARROW_AVAILABLE or _pa is None:
            return self._pivot_sync(ioc_value, ioc_type, depth)

        conn = self._conn
        assert conn is not None
        query = (
            f'MATCH (n:IOC)-[r*1..{depth}]-(m:IOC) '
            'WHERE n.value = $v AND n.ioc_type = $t AND n.id <> m.id '
            'RETURN m.id AS id, m.ioc_type AS ioc_type, m.value AS value, '
            'm.confidence AS confidence, m.first_seen AS first_seen, m.last_seen AS last_seen'
        )
        try:
            res = conn.execute(query, {'v': ioc_value, 't': ioc_type})
            # Kuzu → Arrow zero-copy via C Data Interface (Kuzu 0.11.3+).
            # chunkSize=0 means all rows in one batch — safe for ≤500 rows.
            arrow_table = res.getAsArrow(0)
            # to_pylist() constructs list[dict] in C++ (Arrow compute layer).
            # This is the only Python heap allocation — no per-row overhead.
            records: list[dict[str, Any]] = arrow_table.to_pylist()
        except Exception:
            return self._pivot_sync(ioc_value, ioc_type, depth)

        # Dedup by id (Kuzu variable-length paths may return duplicates)
        results: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for node_data in records:
            nid = node_data.get('id', '')
            if nid and nid not in seen_ids:
                seen_ids.add(nid)
                results.append(node_data)
        return results

    def _pivot_sync(self, ioc_value: str, ioc_type: str, depth: int) -> list[dict[str, Any]]:
        """Synchronous pivot — runs on _executor thread.

        Fallback path when pyarrow is unavailable.  Uses row-by-row iteration
        with get_column_names() hoisted outside the loop (was inside in v1).
        """
        conn = self._conn
        assert conn is not None
        results: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        query = (
            f'MATCH (n:IOC)-[r*1..{depth}]-(m:IOC) '
            'WHERE n.value = $v AND n.ioc_type = $t AND n.id <> m.id '
            'RETURN m.id AS id, m.ioc_type AS ioc_type, m.value AS value, '
            'm.confidence AS confidence, m.first_seen AS first_seen, m.last_seen AS last_seen'
        )
        res = conn.execute(query, {'v': ioc_value, 't': ioc_type})
        # Hoisted: get_column_names() is O(columns), was previously called
        # inside the loop — O(N × columns) → O(columns).
        col_names = res.get_column_names()
        while res.has_next():
            row = res.get_next()
            node_data: dict[str, Any] = dict(zip(col_names, row, strict=False))
            nid = node_data.get('id', '')
            if nid and nid not in seen_ids:
                seen_ids.add(nid)
                results.append(node_data)
        return results

    async def extract_k_hop_subgraph(
        self,
        ioc_value: str,
        ioc_type: str,
        k: int = 2,
        max_nodes: int = 500,
        max_edges: int = 2000,
    ) -> dict[str, Any]:
        """
        Extract the full induced subgraph within k hops of a seed IOC.

        Unlike pivot() which returns a flat neighbor list, this returns
        the COMPLETE induced subgraph: all nodes within k hops AND all
        edges where both endpoints are in the node set. Single Kuzu
        variable-length path query collects the neighbourhood in one pass.

        M1 8GB: bounded to max_nodes / max_edges with early cutoff.

        Args:
            ioc_value: IOC value (e.g. IP, domain, hash)
            ioc_type: IOC type key (e.g. 'ip', 'domain', 'hash_sha256')
            k: Number of hops (1-5, default 2)
            max_nodes: Hard limit on nodes collected (default 500)
            max_edges: Hard limit on edges collected (default 2000)

        Returns:
            Dict with:
                - seed_id: str (IOC id of the seed node)
                - seed_value: str
                - seed_type: str
                - k: int (actual hop radius used)
                - nodes: list[dict] (id, ioc_type, value, confidence,
                          first_seen, last_seen)
                - edges: list[dict] (source_id, target_id, finding_id,
                          source_type, confidence)
                - stats: dict (total_nodes, total_edges, density, max_degree)
                - truncated: bool (True if limits were hit)
        """
        if self._closed or self._conn is None:
            return self._empty_subgraph_result(ioc_value, ioc_type, k)

        k_clamped = max(1, min(k, 5))
        try:
            return await asyncio.to_thread(
                self._extract_k_hop_subgraph_arrow_sync,
                ioc_value, ioc_type, k_clamped, max_nodes, max_edges,
            )
        except Exception as e:
            import logging
            logging.warning(f'[IOCGraph] extract_k_hop_subgraph failed: {e}')
            return self._empty_subgraph_result(ioc_value, ioc_type, k)

    def _empty_subgraph_result(self, value: str, ioc_type: str, k: int) -> dict[str, Any]:
        """Return empty subgraph structure when extraction is not possible."""
        return {
            'seed_id': _make_ioc_id(ioc_type, value),
            'seed_value': value,
            'seed_type': ioc_type,
            'k': k,
            'nodes': [],
            'edges': [],
            'stats': {'total_nodes': 0, 'total_edges': 0, 'density': 0.0, 'max_degree': 0},
            'truncated': False,
        }

    def _extract_k_hop_subgraph_arrow_sync(
        self,
        ioc_value: str,
        ioc_type: str,
        k: int,
        max_nodes: int,
        max_edges: int,
    ) -> dict[str, Any]:
        """Arrow-accelerated subgraph extraction — Kuzu getAsArrow() for both phases.

        Phase 1 (node collection) and Phase 2 (edge collection) both use
        Kuzu's native Arrow export via the C Data Interface.  Falls back to
        _extract_k_hop_subgraph_sync() when pyarrow is not available or any
        Arrow operation fails.
        """
        if not _PYARROW_AVAILABLE or _pa is None:
            return self._extract_k_hop_subgraph_sync(
                ioc_value, ioc_type, k, max_nodes, max_edges,
            )

        k = max(1, min(k, 5))
        if max_nodes < 1:
            max_nodes = 1
        max_nodes = min(max_nodes, 500)
        max_edges = max(0, min(max_edges, 2000))
        neighbor_limit = max(0, max_nodes - 1)

        conn = self._conn
        assert conn is not None
        seed_id = _make_ioc_id(ioc_type, ioc_value)
        truncated = False
        node_set: dict[str, dict[str, Any]] = {}

        # --- Phase 1: Arrow-accelerated node collection ---
        try:
            query = (
                f'MATCH (n:IOC)-[r*1..{k}]-(m:IOC) '
                'WHERE n.value = $v AND n.ioc_type = $t AND n.id <> m.id '
                'RETURN DISTINCT m.id AS id, m.ioc_type AS ioc_type, '
                'm.value AS value, m.confidence AS confidence, '
                'm.first_seen AS first_seen, m.last_seen AS last_seen '
                f'LIMIT {neighbor_limit + 1}'
            )
            res = conn.execute(query, {'v': ioc_value, 't': ioc_type})
            arrow_table = res.getAsArrow(0)  # zero-copy C Data Interface
            records: list[dict[str, Any]] = arrow_table.to_pylist()

            for node_data in records:
                if len(node_set) >= neighbor_limit:
                    truncated = True
                    break
                nid = node_data.get('id', '')
                if nid and nid not in node_set:
                    node_set[nid] = {
                        'id': nid,
                        'ioc_type': node_data.get('ioc_type', 'unknown'),
                        'value': node_data.get('value', ''),
                        'confidence': float(node_data.get('confidence', 1.0)),
                        'first_seen': float(node_data.get('first_seen', 0.0)),
                        'last_seen': float(node_data.get('last_seen', 0.0)),
                    }
        except Exception:
            return self._extract_k_hop_subgraph_sync(
                ioc_value, ioc_type, k, max_nodes, max_edges,
            )

        # --- Seed node lookup (single row — keep as-is) ---
        try:
            seed_res = conn.execute(
                'MATCH (n:IOC) WHERE n.id = $id '
                'RETURN n.ioc_type, n.value, n.confidence, '
                'n.first_seen, n.last_seen',
                {'id': seed_id},
            )
            if seed_res.has_next():
                row = seed_res.get_next()
                if seed_id not in node_set:
                    node_set[seed_id] = {
                        'id': seed_id,
                        'ioc_type': str(row[0]) if row[0] else ioc_type,
                        'value': str(row[1]) if row[1] else ioc_value,
                        'confidence': float(row[2]) if row[2] is not None else 1.0,
                        'first_seen': float(row[3]) if row[3] is not None else 0.0,
                        'last_seen': float(row[4]) if row[4] is not None else 0.0,
                    }
        except Exception:
            pass

        # --- Phase 2: Arrow-accelerated edge collection ---
        node_ids = list(node_set.keys())
        edges: list[dict[str, Any]] = []
        degree_map: dict[str, int] = {nid: 0 for nid in node_ids}

        if len(node_ids) >= 2:
            edge_set: set[tuple[str, str]] = set()
            try:
                res = conn.execute(
                    'UNWIND $ids AS nid '
                    'MATCH (a:IOC)-[r:OBSERVED]->(b:IOC) '
                    'WHERE a.id = nid AND b.id IN $ids '
                    'RETURN a.id AS src, b.id AS dst, '
                    'r.finding_id AS finding_id, '
                    'r.source_type AS source_type, '
                    'r.last_seen AS last_seen '
                    'LIMIT $limit',
                    {'ids': node_ids, 'limit': max_edges * 2},
                )
                arrow_table = res.getAsArrow(0)
                edge_records: list[dict[str, Any]] = arrow_table.to_pylist()

                for rec in edge_records:
                    if len(edge_set) >= max_edges:
                        truncated = True
                        break
                    src = str(rec.get('src', ''))
                    dst = str(rec.get('dst', ''))
                    if src in node_set and dst in node_set:
                        pair = (src, dst)
                        if pair not in edge_set:
                            edge_set.add(pair)
                            edges.append({
                                'source_id': src,
                                'target_id': dst,
                                'finding_id': str(rec.get('finding_id', '')),
                                'source_type': str(rec.get('source_type', 'unknown')),
                                'confidence': 1.0,
                                'last_seen': float(rec.get('last_seen', 0.0) or 0.0),
                            })
                            degree_map[src] = degree_map.get(src, 0) + 1
                            degree_map[dst] = degree_map.get(dst, 0) + 1
            except Exception:
                pass

        total_nodes = len(node_set)
        total_edges = len(edges)
        max_possible = total_nodes * (total_nodes - 1) // 2
        density = total_edges / max_possible if max_possible > 0 else 0.0
        max_degree = max(degree_map.values()) if degree_map else 0

        return {
            'seed_id': seed_id,
            'seed_value': ioc_value,
            'seed_type': ioc_type,
            'k': k,
            'nodes': list(node_set.values()),
            'edges': edges,
            'stats': {
                'total_nodes': total_nodes,
                'total_edges': total_edges,
                'density': round(density, 4),
                'max_degree': max_degree,
            },
            'truncated': truncated,
        }

    def _extract_k_hop_subgraph_sync(
        self,
        ioc_value: str,
        ioc_type: str,
        k: int,
        max_nodes: int,
        max_edges: int,
    ) -> dict[str, Any]:
        """Synchronous subgraph extraction — runs on executor thread.

        Phase 1: Kuzu variable-length path MATCH collects all unique
                 nodes within k hops in a single query.
        Phase 2: Per-node OBSERVED edge queries collect only edges
                 whose both endpoints are in the node set (induced).
        """
        # Normalize bounds for M1 8GB safety (preserve caller intent)
        k = max(1, min(k, 5))
        if max_nodes < 1:
            max_nodes = 1
        max_nodes = min(max_nodes, 500)
        max_edges = max(0, min(max_edges, 2000))
        # Reserve 1 slot for the seed — Phase 1 fills at most max_nodes-1 neighbours
        neighbor_limit = max(0, max_nodes - 1)
        conn = self._conn
        assert conn is not None
        seed_id = _make_ioc_id(ioc_type, ioc_value)
        truncated = False
        node_set: dict[str, dict[str, Any]] = {}

        # Phase 1: Collect all unique nodes within k hops
        try:
            query = (
                f'MATCH (n:IOC)-[r*1..{k}]-(m:IOC) '
                'WHERE n.value = $v AND n.ioc_type = $t AND n.id <> m.id '
                'RETURN DISTINCT m.id AS id, m.ioc_type AS ioc_type, '
                'm.value AS value, m.confidence AS confidence, '
                'm.first_seen AS first_seen, m.last_seen AS last_seen '
                f'LIMIT {neighbor_limit + 1}'
            )
            res = conn.execute(query, {'v': ioc_value, 't': ioc_type})
            col_names = res.get_column_names()
            while res.has_next():
                row = res.get_next()
                if len(node_set) >= neighbor_limit:
                    truncated = True
                    break
                node_data: dict[str, Any] = dict(
                    zip(col_names, row, strict=False)
                )
                nid = node_data.get('id', '')
                if nid and nid not in node_set:
                    node_set[nid] = {
                        'id': nid,
                        'ioc_type': node_data.get('ioc_type', 'unknown'),
                        'value': node_data.get('value', ''),
                        'confidence': float(
                            node_data.get('confidence', 1.0)
                        ),
                        'first_seen': float(
                            node_data.get('first_seen', 0.0)
                        ),
                        'last_seen': float(
                            node_data.get('last_seen', 0.0)
                        ),
                    }
        except Exception:
            return self._empty_subgraph_result(ioc_value, ioc_type, k)

        # Include the seed node itself (may exist with no neighbours)
        try:
            seed_res = conn.execute(
                'MATCH (n:IOC) WHERE n.id = $id '
                'RETURN n.ioc_type, n.value, n.confidence, '
                'n.first_seen, n.last_seen',
                {'id': seed_id},
            )
            if seed_res.has_next():
                row = seed_res.get_next()
                if seed_id not in node_set:
                    node_set[seed_id] = {
                        'id': seed_id,
                        'ioc_type': str(row[0]) if row[0] else ioc_type,
                        'value': str(row[1]) if row[1] else ioc_value,
                        'confidence': float(row[2]) if row[2] is not None else 1.0,
                        'first_seen': float(row[3]) if row[3] is not None else 0.0,
                        'last_seen': float(row[4]) if row[4] is not None else 0.0,
                    }
        except Exception:
            pass  # seed node missing — still valid, report what we have
        # Phase 2: Extract induced edges (both endpoints in node_set)
        node_ids = list(node_set.keys())
        edges: list[dict[str, Any]] = []
        degree_map: dict[str, int] = {nid: 0 for nid in node_ids}

        if len(node_ids) >= 2:
            edge_set: set[tuple[str, str]] = set()
            try:
                # Single UNWIND query — avoids N+1 per-node edge fetches.
                # OBSERVED schema: finding_id, source_type, first_seen, last_seen
                # (no confidence column). Use last_seen as a recency proxy,
                # default edge confidence to 1.0.
                edge_res = conn.execute(
                    'UNWIND $ids AS nid '
                    'MATCH (a:IOC)-[r:OBSERVED]->(b:IOC) '
                    'WHERE a.id = nid AND b.id IN $ids '
                    'RETURN a.id, b.id, r.finding_id, '
                    'r.source_type, r.last_seen '
                    'LIMIT $limit',
                    {'ids': node_ids, 'limit': max_edges * 2},
                )
                while edge_res.has_next():
                    if len(edge_set) >= max_edges:
                        truncated = True
                        break
                    row = edge_res.get_next()
                    src = str(row[0]) if row[0] else ''
                    dst = str(row[1]) if row[1] else ''
                    if src in node_set and dst in node_set:
                        pair = (src, dst)
                        if pair not in edge_set:
                            edge_set.add(pair)
                            edges.append({
                                'source_id': src,
                                'target_id': dst,
                                'finding_id': (
                                    str(row[2]) if row[2] else ''
                                ),
                                'source_type': (
                                    str(row[3])
                                    if row[3]
                                    else 'unknown'
                                ),
                                'confidence': 1.0,  # OBSERVED has no conf
                                'last_seen': (
                                    float(row[4])
                                    if row[4] is not None
                                    else 0.0
                                ),
                            })
                            degree_map[src] = (
                                degree_map.get(src, 0) + 1
                            )
                            degree_map[dst] = (
                                degree_map.get(dst, 0) + 1
                            )
            except Exception:
                pass  # edge extraction failed — return nodes only

        total_nodes = len(node_set)
        total_edges = len(edges)
        max_possible = total_nodes * (total_nodes - 1) // 2
        density = total_edges / max_possible if max_possible > 0 else 0.0
        max_degree = max(degree_map.values()) if degree_map else 0

        return {
            'seed_id': seed_id,
            'seed_value': ioc_value,
            'seed_type': ioc_type,
            'k': k,
            'nodes': list(node_set.values()),
            'edges': edges,
            'stats': {
                'total_nodes': total_nodes,
                'total_edges': total_edges,
                'density': round(density, 4),
                'max_degree': max_degree,
            },
            'truncated': truncated,
        }

    async def graph_stats(self) -> dict[str, int]:
        """Return total node and edge counts."""
        if self._closed or self._conn is None:
            return {'nodes': 0, 'edges': 0}

        try:
            return await asyncio.to_thread(self._graph_stats_sync)
        except Exception as e:
            import logging
            logging.warning(f'[IOCGraph] graph_stats failed: {e}')
            return {'nodes': 0, 'edges': 0}

    def _graph_stats_sync(self) -> dict[str, int]:
        """Synchronous stats — runs on _executor thread."""
        conn = self._conn
        assert conn is not None
        nodes = 0
        try:
            res = conn.execute('MATCH (n:IOC) RETURN count(n)')
            row = res.get_next()
            nodes = int(row[0]) if row else 0
        except Exception:
            pass
        edges = 0
        try:
            res = conn.execute('MATCH ()-[r:OBSERVED]->() RETURN count(r)')
            row = res.get_next()
            edges = int(row[0]) if row else 0
        except Exception:
            pass
        return {'nodes': nodes, 'edges': edges}

    async def export_stix_bundle(self) -> list[dict[str, Any]]:
        """
        Export all IOC nodes as STIX 2.1 objects.

        Validates the bundle via stix2.parse() — returns empty list on failure.
        """
        if self._closed or self._conn is None:
            return []

        try:
            return await asyncio.to_thread(self._export_stix_bundle_sync)
        except Exception as e:
            import logging
            logging.warning(f'[IOCGraph] export_stix_bundle failed: {e}')
            return []

    def _export_stix_bundle_sync(self) -> list[dict[str, Any]]:
        """Synchronous STIX 2.1 export — runs on _executor thread."""
        import stix2
        conn = self._conn
        assert conn is not None
        objects: list[dict[str, Any]] = []
        try:
            res = conn.execute('MATCH (n:IOC) RETURN n.id, n.ioc_type, n.value, n.confidence, n.first_seen ORDER BY n.first_seen DESC')
            while res.has_next():
                row = res.get_next()
                node_id, ioc_type, value, confidence, first_seen = (row[0], row[1], row[2], row[3], row[4])
                valid_from = datetime.fromtimestamp(first_seen or 0, tz=UTC)
                conf = int((confidence or 1.0) * 100)
                try:
                    if ioc_type in ('ip', 'ipv4'):
                        obj = stix2.Indicator(id=f'indicator--{uuid.uuid5(uuid.NAMESPACE_URL, node_id)}', name=f'IP: {value}', pattern=f"[ipv4-addr:value = '{value}']", pattern_type='stix', valid_from=valid_from, confidence=conf)
                    elif ioc_type == 'domain':
                        obj = stix2.Indicator(id=f'indicator--{uuid.uuid5(uuid.NAMESPACE_URL, node_id)}', name=f'Domain: {value}', pattern=f"[domain-name:value = '{value}']", pattern_type='stix', valid_from=valid_from, confidence=conf)
                    elif ioc_type == 'hash_sha256':
                        obj = stix2.Indicator(id=f'indicator--{uuid.uuid5(uuid.NAMESPACE_URL, node_id)}', name=f'SHA256: {value[:16]}...', pattern=f"[file:hashes.'SHA-256' = '{value}']", pattern_type='stix', valid_from=valid_from, confidence=conf)
                    elif ioc_type == 'cve':
                        obj = stix2.Vulnerability(id=f'vulnerability--{uuid.uuid5(uuid.NAMESPACE_URL, node_id)}', name=value, external_references=[{'source_name': 'cve', 'external_id': value}])
                    elif ioc_type in ('onion',) or '.onion' in value:
                        obj = stix2.Indicator(id=f'indicator--{uuid.uuid5(uuid.NAMESPACE_URL, node_id)}', name=f'Onion: {value}', pattern=f"[url:value = 'http://{value}/']", pattern_type='stix', valid_from=valid_from, confidence=conf)
                    else:
                        continue
                    objects.append(orjson.loads(obj.serialize()))
                except Exception as e:
                    import logging
                    logging.warning(f'STIX build failed for {node_id}: {e}')
                    continue
        except Exception as e:
            import logging
            logging.warning(f'STIX export query failed: {e}')
        if objects:
            try:
                bundle = stix2.Bundle(objects=objects)
                stix2.parse(bundle.serialize())
            except Exception as e:
                import logging
                logging.warning(f'STIX bundle validation warning: {e}')
                objects = []
        return objects

    # ------------------------------------------------------------------
    # ISSUE-010: Community Detection & Centrality Metrics
    # ------------------------------------------------------------------

    async def get_communities(self) -> dict[str, int]:
        """
        Compute community detection on the IOC graph.

        Uses Louvain community detection via Rust petgraph (GRAPH-01 feature)
        when available, with igraph C-core label propagation as fallback.

        Returns dict mapping IOC value to community ID (0-indexed).
        Returns empty dict if the graph is empty or computation fails.
        """
        if self._closed or self._conn is None:
            return {}

        try:
            return await asyncio.to_thread(self._get_communities_sync)
        except Exception as e:
            import logging
            logging.warning(f'[IOCGraph] get_communities failed: {e}')
            return {}

    def _get_communities_sync(self) -> dict[str, int]:
        """Synchronous community detection — runs on _executor thread."""
        conn = self._conn
        assert conn is not None

        # Extract nodes and edges from Kuzu
        nodes: list[tuple[int, str, str]] = []
        value_to_id: dict[str, int] = {}

        try:
            res = conn.execute('MATCH (n:IOC) RETURN n.value, n.ioc_type')
            node_id = 1
            while res.has_next():
                row = res.get_next()
                value = str(row[0]) if row[0] else ''
                ioc_type = str(row[1]) if row[1] else 'unknown'
                if value and value not in value_to_id:
                    value_to_id[value] = node_id
                    nodes.append((node_id, value, ioc_type))
                    node_id += 1
        except Exception as e:
            import logging
            logging.warning(f'[IOCGraph] Failed to load nodes for communities: {e}')
            return {}

        if len(nodes) == 0:
            return {}

        # Extract edges (OBSERVED relationships)
        edges: list[tuple[int, int, float]] = []
        try:
            res = conn.execute('MATCH (a:IOC)-[r:OBSERVED]->(b:IOC) RETURN a.value, b.value, r.confidence')
            while res.has_next():
                row = res.get_next()
                src_value = str(row[0]) if row[0] else ''
                dst_value = str(row[1]) if row[1] else ''
                confidence = float(row[2]) if row[2] is not None else 1.0
                src_id = value_to_id.get(src_value)
                dst_id = value_to_id.get(dst_value)
                if src_id is not None and dst_id is not None:
                    edges.append((src_id, dst_id, confidence))
        except Exception as e:
            import logging
            logging.warning(f'[IOCGraph] Failed to load edges for communities: {e}')
            return {}

        if not edges:
            # No edges — every node is its own community
            return {value: i for i, (_, value, _) in enumerate(nodes)}

        # Try Rust Louvain first (petgraph, GRAPH-01 feature)
        try:
            import hledac_rust_extensions as _rust_ext
            result = _rust_ext.rust_graph_analytics_all(nodes, edges, 0.85, 1.0)
            if result and isinstance(result, dict):
                communities = result.get('communities')
                if communities and isinstance(communities, dict):
                    return {str(k): int(v) for k, v in communities.items()}
        except Exception:
            pass

        # Fallback: igraph label propagation
        try:
            import igraph as ig

            id_to_idx: dict[int, int] = {}
            idx_to_value: dict[int, str] = {}
            for i, (nid, value, _) in enumerate(nodes):
                id_to_idx[nid] = i
                idx_to_value[i] = value

            edge_list = [(id_to_idx[s], id_to_idx[d]) for s, d, _ in edges if s in id_to_idx and d in id_to_idx]
            if not edge_list:
                return {value: i for i, (_, value, _) in enumerate(nodes)}

            g = ig.Graph(n=len(nodes), edges=edge_list, directed=False)
            membership = g.community_label_propagation()
            result: dict[str, int] = {}
            for i, (_, value, _) in enumerate(nodes):
                result[value] = membership.membership[i]
            return result
        except Exception as e:
            import logging
            logging.debug(f'[IOCGraph] igraph community detection fallback failed: {e}')

        return {value: 0 for _, value, _ in nodes}

    async def get_centrality(self, ioc_value: str) -> float:
        """
        Compute PageRank centrality for a specific IOC value.

        Uses petgraph PageRank via Rust (GRAPH-01 feature) when available,
        with igraph C-core power iteration as fallback.

        Returns PageRank score (0.0-1.0) or 0.0 on failure.
        """
        if self._closed or self._conn is None:
            return 0.0

        try:
            return await asyncio.to_thread(self._get_centrality_sync, ioc_value)
        except Exception as e:
            import logging
            logging.warning(f'[IOCGraph] get_centrality({ioc_value}) failed: {e}')
            return 0.0

    def _get_centrality_sync(self, ioc_value: str) -> float:
        """Synchronous PageRank computation — runs on _executor thread."""
        conn = self._conn
        assert conn is not None

        # Extract graph
        nodes: list[tuple[int, str, str]] = []
        value_to_id: dict[str, int] = {}

        try:
            res = conn.execute('MATCH (n:IOC) RETURN n.value, n.ioc_type')
            node_id = 1
            while res.has_next():
                row = res.get_next()
                value = str(row[0]) if row[0] else ''
                ioc_type = str(row[1]) if row[1] else 'unknown'
                if value and value not in value_to_id:
                    value_to_id[value] = node_id
                    nodes.append((node_id, value, ioc_type))
                    node_id += 1
        except Exception:
            return 0.0

        if not nodes:
            return 0.0

        edges: list[tuple[int, int, float]] = []
        try:
            res = conn.execute('MATCH (a:IOC)-[r:OBSERVED]->(b:IOC) RETURN a.value, b.value, r.confidence')
            while res.has_next():
                row = res.get_next()
                src_value = str(row[0]) if row[0] else ''
                dst_value = str(row[1]) if row[1] else ''
                confidence = float(row[2]) if row[2] is not None else 1.0
                src_id = value_to_id.get(src_value)
                dst_id = value_to_id.get(dst_value)
                if src_id is not None and dst_id is not None:
                    edges.append((src_id, dst_id, confidence))
        except Exception:
            return 0.0

        target_id = value_to_id.get(ioc_value)
        if target_id is None:
            return 0.0

        # Try Rust PageRank first
        try:
            import hledac_rust_extensions as _rust_ext
            result = _rust_ext.rust_graph_analytics_all(nodes, edges, 0.85, 1.0)
            if result and isinstance(result, dict):
                pagerank = result.get('pagerank')
                if pagerank and isinstance(pagerank, dict):
                    score = pagerank.get(target_id, 0.0)
                    return float(score)
        except Exception:
            pass

        # Fallback: igraph PageRank
        try:
            import igraph as ig

            id_to_idx: dict[int, int] = {}
            for i, (nid, _, _) in enumerate(nodes):
                id_to_idx[nid] = i

            edge_list = [(id_to_idx[s], id_to_idx[d]) for s, d, _ in edges if s in id_to_idx and d in id_to_idx]
            if not edge_list:
                return 0.0

            g = ig.Graph(n=len(nodes), edges=edge_list, directed=True)
            pr_scores = g.pagerank(damping=0.85, directed=True)
            target_idx = id_to_idx.get(target_id)
            if target_idx is not None and target_idx < len(pr_scores):
                return float(pr_scores[target_idx])
        except Exception:
            pass

        return 0.0