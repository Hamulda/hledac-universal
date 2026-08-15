"""
IOC Graph — Kuzu-backed entity graph for OSINT IOC tracking.

GRAPH TRUTH STORE (Sprint 8F7)

===============================
IOCGraph is the GraphTruthStore — the authoritative backend for IOC entity truth.
It owns: buffer_ioc(), flush_buffers(), upsert_ioc_batch(), export_stix_bundle(), pivot().
It is NOT the analytics backend — DuckPGQGraph serves that role.

[META]-006 + [SWARM]-003 Schema:
  IOC(id STRING PK, ioc_type STRING, value STRING,
      first_seen DOUBLE, last_seen DOUBLE, confidence DOUBLE,
      earliest_observed DOUBLE, latest_observed DOUBLE, observation_count INTEGER)
  OBSERVED(finding_id STRING, source_type STRING,
           first_seen DOUBLE, last_seen DOUBLE)
  PREDICTED(confidence DOUBLE, adamic_adar DOUBLE, jaccard DOUBLE,
            pref_attach DOUBLE, common_neighbors INTEGER, method STRING,
            created_at DOUBLE, verified BOOLEAN)

PIVOT:  MATCH (n:IOC)-[r*1..2]-(m:IOC) WHERE n.value=$v AND n.ioc_type=$t RETURN m, r
"""
import asyncio
import logging
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

logger = logging.getLogger(__name__)

from hledac.universal.brain.jtms import JTMS, Justification, apply_temporal_decay

# [FINAL]-019-07: Capability cost registration for QoS ladder triage.
# IOCGraph: rss_mb=150, peak_mb=400 (Kuzu DB + in-memory buffers)
from hledac.universal.core.capability_cost import register_capability_cost
from core import aclose
register_capability_cost("iocgraph", rss_mb=150, peak_mb=400, tier="heavy", tags=("graph", "kuzu"))
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
# 3.1 FIX: Added email and ssh_key types for git forensics pivot
IOC_TYPES: frozenset[str] = frozenset((
    'cve', 'ip', 'hash_sha256', 'hash_md5', 'hash_sha1',
    'onion', 'i2p', 'domain', 'apt', 'malware',
    'info_hash', 'magnet_uri', 'threat_actor', 'malware_family',
    'email', 'ssh_key',  # 3.1 FIX: git forensics IOCs
))
_RE_IP_PUBLIC = re.compile('\\b(?!10\\.|127\\.|169\\.254\\.|172\\.(?:1[6-9]|2\\d|3[01])\\.|192\\.168\\.)(?:\\d{1,3}\\.){3}\\d{1,3}\\b')
_RE_SHA256 = re.compile('\\b[0-9a-fA-F]{64}\\b')
_RE_SHA1 = re.compile('\\b[0-9a-fA-F]{40}\\b')
_RE_MD5 = re.compile('\\b[0-9a-fA-F]{32}\\b')
_RE_ONION_V3 = re.compile('\\b[a-z2-7]{56}\\.onion\\b')
_RE_ONION_V2 = re.compile('\\b[a-z2-7]{16}\\.onion\\b')
# 3.1 FIX: Email and SSH key patterns
_RE_EMAIL = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b')
# SSH key patterns: various formats (ssh-rsa, ecdsa-sha2-nistp256, ssh-ed25519, sk-ssh-ed25519@openssh.com)
# FIX: Use [\s\S] instead of \s+ for multiline text compatibility
# SSH keys in git configs may span multiple lines or have embedded newlines
_RE_SSH_KEY = re.compile(
    r'(?:ssh-rsa|ecdsa-sha2-nistp\d+|ssh-ed25519|sk-ssh-ed25519@openssh\.com)[\s\S]{10,1024}?(?=\n|$)'
)

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
        elif label == 'email':
            results.append((match_value, 'email'))
        elif label == 'ssh_key':
            results.append((match_value, 'ssh_key'))
    for m in _RE_IP_PUBLIC.finditer(text):
        results.append((m.group(), 'ip'))
    for m in _RE_SHA256.finditer(text):
        results.append((m.group().lower(), 'hash_sha256'))
    for m in _RE_SHA1.finditer(text):
        results.append((m.group().lower(), 'hash_sha1'))
    for m in _RE_MD5.finditer(text):
        results.append((m.group().lower(), 'hash_md5'))
    for m in _RE_ONION_V3.finditer(text):
        results.append((m.group(), 'onion'))
    for m in _RE_ONION_V2.finditer(text):
        results.append((m.group(), 'onion'))
    # 3.1 FIX: Extract emails directly from text (git forensics pivot)
    for m in _RE_EMAIL.finditer(text):
        email = m.group()
        # Filter out common noise patterns
        if not any(n in email.lower() for n in ('example.com', 'test.com', 'localhost')):
            results.append((email, 'email'))
    # 3.1 FIX: Extract SSH public keys (git forensics pivot)
    for m in _RE_SSH_KEY.finditer(text):
        ssh_key = m.group()
        # Store first 256 chars to avoid huge keys
        results.append((ssh_key[:256], 'ssh_key'))
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
    # 3.2 FIX: Separate flush sizes for IOCs and observations
    _OBS_BUFFER_FLUSH_SIZE: int = 500  # Match IOC buffer flush size

    __slots__ = tuple(('_BUFFER_FLUSH_SIZE', '_buffer_lock', '_closed', '_conn', '_db', '_db_path',
                       '_executor', '_flush_lock', '_ioc_buffer', '_is_memory_mode',
                       '_obs_buffer', '_jtms', '_decay_lambda', '_schema_has_temporal',
                       '_schema_has_embeddings', '_schema_has_gnn_scores', '_OBS_BUFFER_FLUSH_SIZE',
                       '_schema_has_multimedia', '_face_buffer', '_voice_buffer',
                       '_FACE_BUFFER_FLUSH_SIZE', '_VOICE_BUFFER_FLUSH_SIZE'))

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
        self._ioc_buffer: list[tuple[str, str, float, float | None]] = []
        self._obs_buffer: list[tuple[str, str, str, float, str]] = []
        self._BUFFER_FLUSH_SIZE: int = 500
        self._OBS_BUFFER_FLUSH_SIZE: int = 500  # 3.2 FIX: Auto-flush cap
        self._jtms: JTMS = JTMS()
        self._decay_lambda: float = decay_lambda
        self._schema_has_temporal: bool = False
        # SAFE-4: Flush lock to prevent concurrent flushes from corrupting buffers
        self._flush_lock = asyncio.Lock()
        # FIX: Buffer write lock to prevent race condition on concurrent buffer_ioc calls
        # Without this, concurrent writes can overflow the buffer before flush triggers
        self._buffer_lock = asyncio.Lock()
        # SAFE-4: Schema flags for GNN embedding support
        self._schema_has_embeddings: bool = False
        self._schema_has_gnn_scores: bool = False
        # NEXTGEN-03: Multimedia schema flags and buffers
        self._schema_has_multimedia: bool = False
        self._face_buffer: list[tuple[str, list[float], str, float]] = []  # (id, embedding, source_hash, confidence)
        self._voice_buffer: list[tuple[str, list[float], str, float]] = []  # (id, embedding, source_hash, confidence)
        self._FACE_BUFFER_FLUSH_SIZE: int = 100
        self._VOICE_BUFFER_FLUSH_SIZE: int = 100

    async def buffer_ioc(
        self,
        ioc_type: str,
        value: str,
        confidence: float = 1.0,
        observed_at: float | None = None,
    ) -> None:
        """
        Add IOC to in-memory buffer — ZERO Kuzu I/O in ACTIVE phase.
        Flush automatically when buffer reaches _BUFFER_FLUSH_SIZE.

        [META]-006: observed_at captures the original event timestamp (e.g., CT
        certificate not_before, Telegram message date) instead of flush time.
        When None, defaults to time.time() at flush for backward compatibility.

        After close() the buffer is closed: new writes are silently dropped
        so no buffered data can be lost or observed in an inconsistent state.

        FIX: Uses _buffer_lock to prevent race condition on concurrent writes.
        Without locking, concurrent buffer_ioc() calls can overflow the buffer
        before the flush threshold is checked.
        """
        if self._closed:
            return
        async with self._buffer_lock:
            self._ioc_buffer.append((ioc_type, value, confidence, observed_at))
            if len(self._ioc_buffer) >= self._BUFFER_FLUSH_SIZE:
                await self.flush_buffers()

    async def buffer_observation(self, id_a: str, id_b: str, finding_id: str, ts: float, source_type: str) -> None:
        """
        Add observation to in-memory buffer — ZERO Kuzu I/O in ACTIVE phase.

        After close() the buffer is closed: new writes are silently dropped.

        3.2 FIX: Added auto-flush limit like buffer_ioc().
        Previous implementation had unbounded _obs_buffer growth → RAM exhaustion.
        Now flushes when _OBS_BUFFER_FLUSH_SIZE (500) entries accumulated.

        FIX: Uses _buffer_lock to prevent race condition on concurrent writes.
        """
        if self._closed:
            return
        async with self._buffer_lock:
            self._obs_buffer.append((id_a, id_b, finding_id, ts, source_type))
            # 3.2 FIX: Auto-flush to prevent unbounded buffer growth
            if len(self._obs_buffer) >= self._OBS_BUFFER_FLUSH_SIZE:
                await self.flush_buffers()

    async def buffer_face(
        self,
        face_id: str,
        embedding: list[float],
        source_image_hash: str,
        confidence: float = 0.9,
    ) -> None:
        """
        NEXTGEN-03: Buffer face embedding for cross-modal identity fusion.

        Face embeddings are stored in FACE nodes and linked to IOC identities
        via HAS_FACE relationships. Uses LSH indexing for fast similarity search.

        Args:
            face_id: Unique identifier for this face (e.g., "face_{sha256}")
            embedding: 512-dim face embedding vector
            source_image_hash: SHA256 hash of source image for provenance
            confidence: Face detection confidence (0-1)
        """
        if self._closed:
            return
        async with self._buffer_lock:
            self._face_buffer.append((face_id, embedding, source_image_hash, confidence))
            if len(self._face_buffer) >= self._FACE_BUFFER_FLUSH_SIZE:
                await self.flush_buffers()

    async def buffer_voiceprint(
        self,
        voice_id: str,
        embedding: list[float],
        source_audio_hash: str,
        confidence: float = 0.85,
        duration_s: float = 0.0,
    ) -> None:
        """
        NEXTGEN-03: Buffer voiceprint embedding for cross-modal identity fusion.

        Voiceprint embeddings are stored in VOICEPRINT nodes and linked to IOC
        identities via HAS_VOICEPRINT relationships.

        Args:
            voice_id: Unique identifier for this voiceprint (e.g., "voice_{sha256}")
            embedding: 256-dim speaker embedding vector
            source_audio_hash: SHA256 hash of source audio for provenance
            confidence: Voice quality confidence (0-1)
            duration_s: Duration of voice sample in seconds
        """
        if self._closed:
            return
        async with self._buffer_lock:
            self._voice_buffer.append((voice_id, embedding, source_audio_hash, confidence, duration_s))
            if len(self._voice_buffer) >= self._VOICE_BUFFER_FLUSH_SIZE:
                await self.flush_buffers()

    async def link_identity_face(
        self,
        ioc_id: str,
        face_id: str,
        confidence: float = 0.9,
        source_type: str = "multimedia",
    ) -> None:
        """
        NEXTGEN-03: Link an IOC identity to a FACE node via HAS_FACE relationship.

        Args:
            ioc_id: IOC node ID (identity)
            face_id: FACE node ID
            confidence: Attribution confidence (0-1)
            source_type: Source of the linkage (e.g., "media", "social")
        """
        if self._closed or self._conn is None:
            return
        ts = time.time()
        try:
            conn = self._conn
            conn.execute(
                'MATCH (i:IOC), (f:FACE) WHERE i.id = $ioc AND f.id = $face '
                'CREATE (i)-[r:HAS_FACE {confidence: $conf, source_type: $src, first_seen: $ts, last_seen: $ts}]->(f)',
                {'ioc': ioc_id, 'face': face_id, 'conf': confidence, 'src': source_type, 'ts': ts}
            )
        except Exception as e:
            logger.debug('[IOCGraph] link_identity_face failed: %s', e)

    async def link_identity_voice(
        self,
        ioc_id: str,
        voice_id: str,
        confidence: float = 0.85,
        source_type: str = "multimedia",
    ) -> None:
        """
        NEXTGEN-03: Link an IOC identity to a VOICEPRINT node via HAS_VOICEPRINT relationship.

        Args:
            ioc_id: IOC node ID (identity)
            voice_id: VOICEPRINT node ID
            confidence: Attribution confidence (0-1)
            source_type: Source of the linkage
        """
        if self._closed or self._conn is None:
            return
        ts = time.time()
        try:
            conn = self._conn
            conn.execute(
                'MATCH (i:IOC), (v:VOICEPRINT) WHERE i.id = $ioc AND v.id = $voice '
                'CREATE (i)-[r:HAS_VOICEPRINT {confidence: $conf, source_type: $src, first_seen: $ts, last_seen: $ts}]->(v)',
                {'ioc': ioc_id, 'voice': voice_id, 'conf': confidence, 'src': source_type, 'ts': ts}
            )
        except Exception as e:
            logger.debug('[IOCGraph] link_identity_voice failed: %s', e)

    async def link_face_to_voice(
        self,
        face_id: str,
        voice_id: str,
        confidence: float = 0.75,
        face_weight: float = 0.5,
        voice_weight: float = 0.5,
    ) -> None:
        """
        NEXTGEN-03: Link a FACE to a VOICEPRINT as the same person via CROSS_MODAL.

        Args:
            face_id: FACE node ID
            voice_id: VOICEPRINT node ID
            confidence: Combined match confidence
            face_weight: Weight of face signal (0-1)
            voice_weight: Weight of voice signal (0-1)
        """
        if self._closed or self._conn is None:
            return
        ts = time.time()
        try:
            conn = self._conn
            conn.execute(
                'MATCH (f:FACE), (v:VOICEPRINT) WHERE f.id = $face AND v.id = $voice '
                'CREATE (f)-[r:CROSS_MODAL {confidence: $conf, face_weight: $fw, voice_weight: $vw, first_seen: $ts, last_seen: $ts}]->(v)',
                {'face': face_id, 'voice': voice_id, 'conf': confidence, 'fw': face_weight, 'vw': voice_weight, 'ts': ts}
            )
        except Exception as e:
            logger.debug('[IOCGraph] link_face_to_voice failed: %s', e)

    async def buffer_ioc_with_justification(
        self,
        ioc_type: str,
        value: str,
        confidence: float,
        source_ids: list[str] | tuple[str, ...],
        inference_rule: str = "manual",
        source_reliability: float = 1.0,
        observed_at: float | None = None,
    ) -> str | None:
        """
        Buffer IOC with JTMS justification tracking.

        Creates a justification in the in-memory JTMS before buffering.
        When flush_buffers() runs, temporal decay is applied to confidence.

        [META]-006: observed_at captures the original event timestamp.

        Args:
            ioc_type: IOC type (ip, domain, hash_sha256, etc.)
            value: IOC value
            confidence: Base confidence (0..1) before temporal decay
            source_ids: List of source identifiers supporting this fact
            inference_rule: Algorithm that derived this fact (default: "manual")
            source_reliability: Aggregate source reliability (0..1)
            observed_at: Original event timestamp (Unix epoch seconds).
                         When None, defaults to time.time() at flush.

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
        # FIX: Use _buffer_lock to prevent race condition on concurrent writes
        async with self._buffer_lock:
            self._ioc_buffer.append((ioc_type, value, confidence, observed_at))
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

    async def auto_retract_unreliable_sources(self) -> list[str]:
        """
        [META-008] Auto-retract sources flagged as unreliable by SourceReliabilityTracker.

        Checks all tracked sources and retracts any with ratio > AUTO_RETRACT_RATIO
        that haven't already been auto-retracted. Should be called during SYNTHESIS
        phase to clean up systematic dissenters.

        Returns:
            List of source_ids that were retracted.

        Fail-soft: any error returns empty list.
        """
        try:
            from hledac.universal.knowledge.source_reliability import (
                get_source_reliability_tracker,
            )
        except ImportError:
            return []

        tracker = get_source_reliability_tracker()
        unreliable = tracker.get_unreliable_sources()

        retracted: list[str] = []
        for source in unreliable:
            try:
                result = await self.retract_source(source.source_id)
                if result.get('facts_retracted', 0) > 0:
                    await tracker.mark_auto_retracted(source.source_id)
                    retracted.append(source.source_id)
                    import logging
                    logging.info(
                        '[IOCGraph] auto_retract_unreliable_sources: '
                        'retracted %s (ratio=%.3f, %d/%d contradictory claims)',
                        source.source_id,
                        source.ratio,
                        source.contradiction_count,
                        source.total_claims,
                    )
            except Exception as e:
                import logging
                logging.warning(
                    '[IOCGraph] auto_retract_unreliable_sources '
                    'failed for %s: %s',
                    source.source_id, e,
                )

        return retracted

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
            'MATCH (n:IOC) WHERE n.id = $id '
            'SET n.confidence = $c, n.last_seen = $ts',
            {'id': ioc_id, 'c': confidence, 'ts': now}
        )

    async def flush_buffers(self) -> dict[str, int]:
        """
        Bulk flush both buffers to Kuzu — call in WINDUP or at buffer limit.

        JTMS INTEGRATION: Applies temporal decay to confidence scores before
        flushing to Kuzu. Decay formula: conf * exp(-λ * Δt_hours)

        SAFE-4: Uses _flush_lock to prevent concurrent flushes from corrupting
        buffer state. Only one flush can run at a time.

        Returns:
            ioc_created: count of IOC nodes NEWLY CREATED in this flush.
                         IOCs that already existed are updated (last_seen bump)
                         but NOT counted here. Call graph_stats() for total count.
            obs_flushed: count of observation edges written to the graph.
        """
        # SAFE-4: Acquire flush lock to prevent concurrent flushes
        if not self._flush_lock.locked():
            async with self._flush_lock:
                return await self._flush_buffers_impl()
        else:
            # Another flush is in progress, skip this one
            import logging
            logging.debug('[IOCGraph] flush_buffers: another flush in progress, skipping')
            return {'ioc_created': 0, 'obs_flushed': 0}

    async def _flush_buffers_impl(self) -> dict[str, int]:
        """
        Internal flush implementation — caller must hold _flush_lock.
        
        CRITICAL FIX: Clear buffers ONLY after successful write to prevent data loss.
        If write fails, data remains in buffer for retry.
        """
        if not self._ioc_buffer and (not self._obs_buffer):
            return {'ioc_created': 0, 'obs_flushed': 0}
        ioc_copy = self._ioc_buffer[:]
        obs_copy = self._obs_buffer[:]

        # Apply temporal decay to IOC confidences if JTMS has facts
        # Also resolve observed_at: None → time.time()
        now = time.time()
        if self._jtms._facts and self._decay_lambda > 0:
            decayed_ioc_copy = []
            for ioc_type, value, base_conf, obs_at in ioc_copy:
                # Resolve observed_at
                ts = obs_at if obs_at is not None else now
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
                    decayed_ioc_copy.append((ioc_type, value, decayed_conf, ts))
                else:
                    # No JTMS fact, use base confidence
                    decayed_ioc_copy.append((ioc_type, value, base_conf, ts))
            ioc_copy = decayed_ioc_copy
        else:
            # No JTMS: just resolve observed_at
            ioc_copy = [
                (ioc_type, value, conf, (obs_at if obs_at is not None else now))
                for ioc_type, value, conf, obs_at in ioc_copy
            ]

        ioc_created: list[str] = []
        obs_recorded: int = 0
        face_created: int = 0
        voice_created: int = 0
        failed: bool = False

        # NEXTGEN-03: Copy multimedia buffers (always initialized in __init__)
        face_copy = self._face_buffer[:]
        voice_copy = self._voice_buffer[:]

        try:
            if ioc_copy:
                ioc_created = await self.upsert_ioc_batch(ioc_copy)
            if obs_copy:
                await self._record_observation_batch_sync_async(obs_copy)
                obs_recorded = len(obs_copy)
            # NEXTGEN-03: Flush face embeddings
            if face_copy and self._schema_has_multimedia:
                face_created = await self._flush_face_buffer(face_copy)
            # NEXTGEN-03: Flush voiceprint embeddings
            if voice_copy and self._schema_has_multimedia:
                voice_created = await self._flush_voice_buffer(voice_copy)
        except Exception as e:
            import logging
            logging.warning(f'[IOCGraph] flush_buffers failed: {e}')
            failed = True
            # Restore buffers on failure so data is not lost
            self._ioc_buffer.extend(ioc_copy)
            self._obs_buffer.extend(obs_copy)
            # NEXTGEN-03: Restore multimedia buffers on failure
            self._face_buffer.extend(face_copy)
            self._voice_buffer.extend(voice_copy)
        
        # Only clear buffers on success
        if not failed:
            self._ioc_buffer.clear()
            self._obs_buffer.clear()
            # NEXTGEN-03: Clear multimedia buffers on success
            self._face_buffer.clear()
            self._voice_buffer.clear()
            import logging
            logging.info(
                f'[IOCGraph] Buffer flushed: {len(ioc_created)} IOCs, '
                f'{obs_recorded} obs, {face_created} faces, {voice_created} voices'
            )
        
        return {
            'ioc_created': len(ioc_created),
            'obs_flushed': obs_recorded,
            'faces_created': face_created,
            'voices_created': voice_created,
        }

    async def _flush_face_buffer(self, face_copy: list[tuple[str, list[float], str, float]]) -> int:
        """Flush face embeddings to Kuzu FACE nodes. Returns count of created nodes."""
        if not self._conn or not face_copy:
            return 0

        def _flush_sync():
            count = 0
            ts = time.time()
            for face_id, embedding, source_hash, confidence in face_copy:
                try:
                    # Store embedding in cross-modal index (Rust)
                    self._store_crossmodal_face(face_id, embedding)
                    # Create FACE node in Kuzu
                    self._conn.execute(
                        'CREATE (:FACE {id: $id, embedding_dim: $dim, source_image_hash: $hash, confidence: $conf, first_seen: $ts, last_seen: $ts})',
                        {'id': face_id, 'dim': len(embedding), 'hash': source_hash, 'conf': confidence, 'ts': ts}
                    )
                    count += 1
                except Exception as e:
                    logger.debug('[IOCGraph] _flush_face_buffer: %s', e)
            return count

        return await asyncio.to_thread(_flush_sync)

    async def _flush_voice_buffer(self, voice_copy: list[tuple[str, list[float], str, float, float]]) -> int:
        """Flush voiceprint embeddings to Kuzu VOICEPRINT nodes. Returns count of created nodes."""
        if not self._conn or not voice_copy:
            return 0

        def _flush_sync():
            count = 0
            ts = time.time()
            for voice_id, embedding, source_hash, confidence, duration in voice_copy:
                try:
                    # Store embedding in cross-modal index (Rust)
                    self._store_crossmodal_voice(voice_id, embedding)
                    # Create VOICEPRINT node in Kuzu
                    self._conn.execute(
                        'CREATE (:VOICEPRINT {id: $id, embedding_dim: $dim, source_audio_hash: $hash, confidence: $conf, duration_s: $dur, first_seen: $ts, last_seen: $ts})',
                        {'id': voice_id, 'dim': len(embedding), 'hash': source_hash, 'conf': confidence, 'dur': duration, 'ts': ts}
                    )
                    count += 1
                except Exception as e:
                    logger.debug('[IOCGraph] _flush_voice_buffer: %s', e)
            return count

        return await asyncio.to_thread(_flush_sync)

    def _store_crossmodal_face(self, face_id: str, embedding: list[float]) -> None:
        """Store face embedding in Rust cross-modal index."""
        try:
            from hledac.universal.core.rust_backend import rust
            rust.ane.crossmodal_store_face(face_id, embedding)
        except Exception:
            pass  # Non-critical: Rust index is optional

    def _store_crossmodal_voice(self, voice_id: str, embedding: list[float]) -> None:
        """Store voiceprint embedding in Rust cross-modal index."""
        try:
            from hledac.universal.core.rust_backend import rust
            rust.ane.crossmodal_store_voice(voice_id, embedding)
        except Exception:
            pass  # Non-critical: Rust index is optional

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
        """Synchronous schema init — runs on _executor thread.

        [META]-006 Schema:
        - IOC: id, ioc_type, value, first_seen, last_seen, confidence,
               earliest_observed, latest_observed, observation_count
        - OBSERVED: FROM IOC TO IOC, finding_id, source_type, first_seen, last_seen

        [GNN-3]: Extended schema with per-node embedding references for GNN:
        - embedding_table: STRING — LanceDB table name for embedding lookup
        - embedding_row_id: INTEGER — LanceDB row ID for this node's embedding
        - embedding_dim: INTEGER — Dimension of embedding vector
        - embedding_updated_at: DOUBLE — Unix timestamp of last embedding update
        """
        self._db = _kuzu.Database(str(self._db_path))
        self._conn = _kuzu.Connection(self._db)

        # IOC node table with [META]-006 temporal fields + [GNN-3] embedding refs
        try:
            self._conn.execute(
                'CREATE NODE TABLE IOC('
                'id STRING PRIMARY KEY, '
                'ioc_type STRING, '
                'value STRING, '
                'first_seen DOUBLE, '
                'last_seen DOUBLE, '
                'confidence DOUBLE, '
                'earliest_observed DOUBLE, '
                'latest_observed DOUBLE, '
                'observation_count INTEGER, '
                'embedding_table STRING, '
                'embedding_row_id INTEGER, '
                'embedding_dim INTEGER, '
                'embedding_updated_at DOUBLE'
                ')'
            )
            self._schema_has_temporal = True
        except Exception:  # noqa: BLE001
            pass

        try:
            self._conn.execute('CREATE REL TABLE OBSERVED(FROM IOC TO IOC, finding_id STRING, source_type STRING, first_seen DOUBLE, last_seen DOUBLE)')
        except Exception:  # noqa: BLE001
            pass

        # SWARM-003: PREDICTED edge type for link prediction results
        # Extended with [GNN-3] fields
        try:
            self._conn.execute(
                'CREATE REL TABLE PREDICTED('
                'FROM IOC TO IOC, '
                'confidence DOUBLE, '
                'adamic_adar DOUBLE, '
                'jaccard DOUBLE, '
                'pref_attach DOUBLE, '
                'common_neighbors INTEGER, '
                'method STRING, '
                'created_at DOUBLE, '
                'verified BOOLEAN DEFAULT false, '
                'gnn_score DOUBLE DEFAULT 0.0, '
                'combined_score DOUBLE DEFAULT 0.0'
                ')'
            )
        except Exception:  # noqa: BLE001
            pass

        # [GNN-3]: Probe whether existing DB has embedding fields
        self._schema_has_embeddings = False
        try:
            self._conn.execute(
                "MATCH (n:IOC) RETURN n.embedding_table, n.embedding_row_id LIMIT 1"
            )
            self._schema_has_embeddings = True
        except Exception:
            self._schema_has_embeddings = False

        # [META]-006: Probe whether existing DB has temporal fields
        if not self._schema_has_temporal:
            try:
                self._conn.execute(
                    'MATCH (n:IOC) RETURN n.earliest_observed, n.latest_observed, n.observation_count LIMIT 1'
                )
                self._schema_has_temporal = True
            except Exception:
                self._schema_has_temporal = False

        # [GNN-3]: Migrate PREDICTED edge schema if missing GNN fields
        self._schema_has_gnn_scores = False
        try:
            self._conn.execute(
                "MATCH ()-[r:PREDICTED]->() RETURN r.gnn_score, r.combined_score LIMIT 1"
            )
            self._schema_has_gnn_scores = True
        except Exception:
            self._schema_has_gnn_scores = False
        
        if not self._schema_has_gnn_scores:
            # Kuzu supports ALTER to add properties to existing tables
            try:
                self._conn.execute(
                    "ALTER (FROM)->(TO) ADD gnn_score DOUBLE DEFAULT 0.0"
                )
                self._conn.execute(
                    "ALTER (FROM)->(TO) ADD combined_score DOUBLE DEFAULT 0.0"
                )
                self._schema_has_gnn_scores = True
                logger.info('[IOCGraph] Migrated PREDICTED edge schema with GNN fields')
            except Exception:
                # ALTER failed - might be syntax issue or already added
                # Try to verify again
                try:
                    self._conn.execute(
                        "MATCH ()-[r:PREDICTED]->() RETURN r.gnn_score LIMIT 1"
                    )
                    self._schema_has_gnn_scores = True
                except Exception:
                    self._schema_has_gnn_scores = False

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
        except Exception:  # noqa: BLE001
            pass
        self._closed = True
        try:
            await asyncio.to_thread(self._close_sync)
        except Exception:  # noqa: BLE001
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

    def _create_schema(self, conn):
        """Create IOC, OBSERVED, PREDICTED tables in target DB."""
        try: conn.execute('CREATE NODE TABLE IOC(id STRING PRIMARY KEY, ioc_type STRING, value STRING, first_seen DOUBLE, last_seen DOUBLE, confidence DOUBLE, earliest_observed DOUBLE, latest_observed DOUBLE, observation_count INTEGER)')
        except Exception: pass
        try: conn.execute('CREATE REL TABLE OBSERVED(FROM IOC TO IOC, finding_id STRING, source_type STRING, first_seen DOUBLE, last_seen DOUBLE)')
        except Exception: pass
        try: conn.execute('CREATE REL TABLE PREDICTED(FROM IOC TO IOC, confidence DOUBLE, adamic_adar DOUBLE, jaccard DOUBLE, pref_attach DOUBLE, common_neighbors INTEGER, method STRING, created_at DOUBLE, verified BOOLEAN DEFAULT false)')
        except Exception: pass
        # NEXTGEN-03: Multimedia schema - FACE and VOICEPRINT nodes with relationships
        self._create_multimedia_schema(conn)

    def _create_multimedia_schema(self, conn):
        """
        Create multimedia schema for cross-modal identity fusion (NEXTGEN-03).
        
        Uses CREATE TABLE IF NOT EXISTS to be idempotent - safe for concurrent
        initialization or when schema already exists.
        """
        import logging
        logger = logging.getLogger(__name__)
        
        # FACE node: stores face embedding for identity matching
        try:
            conn.execute(
                'CREATE NODE TABLE IF NOT EXISTS FACE('
                'id STRING PRIMARY KEY, '
                'embedding_dim INTEGER, '  # 512 for FaceNet
                'source_image_hash STRING, '  # SHA256 of source image
                'confidence DOUBLE, '  # Face detection confidence (0-1)
                'first_seen DOUBLE, '
                'last_seen DOUBLE)'
            )
            logger.debug('[IOCGraph] FACE node table created or already exists')
        except Exception as e:
            logger.warning(f'[IOCGraph] Failed to create FACE table (may already exist): {e}')
        
        # VOICEPRINT node: stores speaker embedding for voice identity
        try:
            conn.execute(
                'CREATE NODE TABLE IF NOT EXISTS VOICEPRINT('
                'id STRING PRIMARY KEY, '
                'embedding_dim INTEGER, '  # 256 for speaker embedding
                'source_audio_hash STRING, '  # SHA256 of source audio
                'confidence DOUBLE, '  # Voice quality confidence (0-1)
                'duration_s DOUBLE, '  # Duration of voice sample
                'first_seen DOUBLE, '
                'last_seen DOUBLE)'
            )
            logger.debug('[IOCGraph] VOICEPRINT node table created or already exists')
        except Exception as e:
            logger.warning(f'[IOCGraph] Failed to create VOICEPRINT table (may already exist): {e}')
        
        # HAS_FACE: links IOC (identity) to FACE node
        try:
            conn.execute(
                'CREATE REL TABLE IF NOT EXISTS HAS_FACE('
                'FROM IOC TO FACE, '
                'confidence DOUBLE, '  # Attribution confidence
                'source_type STRING, '  # media, social, etc.
                'first_seen DOUBLE, '
                'last_seen DOUBLE)'
            )
            logger.debug('[IOCGraph] HAS_FACE relationship table created or already exists')
        except Exception as e:
            logger.warning(f'[IOCGraph] Failed to create HAS_FACE table (may already exist): {e}')
        
        # HAS_VOICEPRINT: links IOC (identity) to VOICEPRINT node
        try:
            conn.execute(
                'CREATE REL TABLE IF NOT EXISTS HAS_VOICEPRINT('
                'FROM IOC TO VOICEPRINT, '
                'confidence DOUBLE, '
                'source_type STRING, '
                'first_seen DOUBLE, '
                'last_seen DOUBLE)'
            )
            logger.debug('[IOCGraph] HAS_VOICEPRINT relationship table created or already exists')
        except Exception as e:
            logger.warning(f'[IOCGraph] Failed to create HAS_VOICEPRINT table (may already exist): {e}')
        
        # SAME_IDENTITY: links two FACE nodes as same person
        try:
            conn.execute(
                'CREATE REL TABLE IF NOT EXISTS SAME_IDENTITY('
                'FROM FACE TO FACE, '
                'confidence DOUBLE, '  # Face match confidence
                'method STRING, '  # facenet, arcface, etc.
                'first_seen DOUBLE, '
                'last_seen DOUBLE)'
            )
            logger.debug('[IOCGraph] SAME_IDENTITY relationship table created or already exists')
        except Exception as e:
            logger.warning(f'[IOCGraph] Failed to create SAME_IDENTITY table (may already exist): {e}')
        
        # SAME_VOICE: links two VOICEPRINT nodes as same speaker
        try:
            conn.execute(
                'CREATE REL TABLE IF NOT EXISTS SAME_VOICE('
                'FROM VOICEPRINT TO VOICEPRINT, '
                'confidence DOUBLE, '  # Voice match confidence
                'method STRING, '  # xvectornet, etc.
                'first_seen DOUBLE, '
                'last_seen DOUBLE)'
            )
            logger.debug('[IOCGraph] SAME_VOICE relationship table created or already exists')
        except Exception as e:
            logger.warning(f'[IOCGraph] Failed to create SAME_VOICE table (may already exist): {e}')
        
        # CROSS_MODAL: links FACE to VOICEPRINT as same identity
        try:
            conn.execute(
                'CREATE REL TABLE IF NOT EXISTS CROSS_MODAL('
                'FROM FACE TO VOICEPRINT, '
                'confidence DOUBLE, '  # Combined face+voice confidence
                'face_weight DOUBLE, '  # Weight of face signal
                'voice_weight DOUBLE, '  # Weight of voice signal
                'first_seen DOUBLE, '
                'last_seen DOUBLE)'
            )
            logger.debug('[IOCGraph] CROSS_MODAL relationship table created or already exists')
        except Exception as e:
            logger.warning(f'[IOCGraph] Failed to create CROSS_MODAL table (may already exist): {e}')
        
        self._schema_has_multimedia = True

    def _copy_nodes(self, target_conn):
        """Copy IOC nodes to target DB."""
        ioc_count = 0
        if self._schema_has_temporal:
            res = self._conn.execute('MATCH (n:IOC) RETURN n.id, n.ioc_type, n.value, n.first_seen, n.last_seen, n.confidence, n.earliest_observed, n.latest_observed, n.observation_count')
            while res.has_next():
                row = res.get_next()
                target_conn.execute('CREATE (:IOC {id: $id, ioc_type: $t, value: $v, first_seen: $fs, last_seen: $ls, confidence: $c, earliest_observed: $eo, latest_observed: $lo, observation_count: $oc})', {'id': row[0], 't': row[1], 'v': row[2], 'fs': row[3], 'ls': row[4], 'c': row[5], 'eo': row[6], 'lo': row[7], 'oc': row[8]})
                ioc_count += 1
        else:
            res = self._conn.execute('MATCH (n:IOC) RETURN n.id, n.ioc_type, n.value, n.first_seen, n.last_seen, n.confidence')
            while res.has_next():
                row = res.get_next()
                target_conn.execute('CREATE (:IOC {id: $id, ioc_type: $t, value: $v, first_seen: $fs, last_seen: $ls, confidence: $c, earliest_observed: $fs, latest_observed: $ls, observation_count: 1})', {'id': row[0], 't': row[1], 'v': row[2], 'fs': row[3], 'ls': row[4], 'c': row[5]})
                ioc_count += 1
        logger.info('[IOCGraph] persist_to_disk: %d IOC nodes copied', ioc_count)
        return ioc_count

    def _copy_observed_edges(self, target_conn):
        """Copy OBSERVED edges to target DB."""
        obs_count = 0
        res = self._conn.execute('MATCH (a:IOC)-[r:OBSERVED]->(b:IOC) RETURN a.id, b.id, r.finding_id, r.source_type, r.first_seen, r.last_seen')
        while res.has_next():
            row = res.get_next()
            target_conn.execute('MATCH (a:IOC {id: $aid}), (b:IOC {id: $bid}) CREATE (a)-[:OBSERVED {finding_id: $fid, source_type: $st, first_seen: $fs, last_seen: $ls}]->(b)', {'aid': row[0], 'bid': row[1], 'fid': row[2], 'st': row[3], 'fs': row[4], 'ls': row[5]})
            obs_count += 1
        logger.info('[IOCGraph] persist_to_disk: %d OBSERVED edges copied', obs_count)
        return obs_count

    def _copy_predicted_edges(self, target_conn):
        """Copy PREDICTED edges to target DB."""
        pred_count = 0
        try:
            res = self._conn.execute('MATCH (a:IOC)-[r:PREDICTED]->(b:IOC) RETURN a.id, b.id, r.confidence, r.adamic_adar, r.jaccard, r.pref_attach, r.common_neighbors, r.method, r.created_at, r.verified')
            while res.has_next():
                row = res.get_next()
                target_conn.execute('MATCH (a:IOC {id: $aid}), (b:IOC {id: $bid}) CREATE (a)-[:PREDICTED {confidence: $conf, adamic_adar: $aa, jaccard: $jac, pref_attach: $pa, common_neighbors: $cn, method: $method, created_at: $ts, verified: $ver}]->(b)', {'aid': row[0], 'bid': row[1], 'conf': row[2], 'aa': row[3], 'jac': row[4], 'pa': row[5], 'cn': row[6], 'method': row[7], 'ts': row[8], 'ver': row[9]})
                pred_count += 1
            logger.info('[IOCGraph] persist_to_disk: %d PREDICTED edges copied', pred_count)
        except Exception: pass
        return pred_count

    def _prepare_target_directory(self, target_path: Path) -> None:
        """Prepare target directory for persistence."""
        target_path = Path(target_path)
        target_path.parent.mkdir(parents=True, exist_errors=True)
        if target_path.exists():
            import shutil
            shutil.rmtree(str(target_path), ignore_errors=True)

    def _persist_to_target_db(self, target_path: Path) -> tuple[_kuzu.Database, _kuzu.Connection]:
        """Create target database and connection."""
        target_db = _kuzu.Database(str(target_path))
        target_conn = _kuzu.Connection(target_db)
        self._create_schema(target_conn)
        return target_db, target_conn

    async def persist_to_disk(self, target_path: Path) -> int:
        """BLITZ-08: Export in-memory graph to a file-backed Kuzu database.

        SAFE-4: Clears buffers after successful persistence to prevent
        duplicate data when transitioning from memory mode to disk mode.
        """
        if self._closed or self._conn is None:
            logger.warning('[IOCGraph] persist_to_disk: graph is closed')
            return 0
        if not self._is_memory_mode:
            logger.debug('[IOCGraph] persist_to_disk: already file-backed, no-op')
            return 0

        self._prepare_target_directory(target_path)
        target_db, target_conn = None, None

        try:
            target_db, target_conn = self._persist_to_target_db(target_path)
            total = (self._copy_nodes(target_conn) + self._copy_observed_edges(target_conn)
                     + self._copy_predicted_edges(target_conn))
            logger.info('[IOCGraph] persist_to_disk: %d total entities written to %s', total, target_path)

            # SAFE-4: Clear buffers after successful persistence
            # This prevents duplicate data when transitioning from memory to disk mode
            self._ioc_buffer.clear()
            self._obs_buffer.clear()
            logger.info('[IOCGraph] persist_to_disk: buffers cleared after successful export')

            return total
        except Exception as exc:
            logger.error('[IOCGraph] persist_to_disk failed: %s', exc)
            return 0
        finally:
            if target_conn:
                target_conn.close()
            if target_db:
                target_db.close()

    async def upsert_ioc(
        self,
        ioc_type: str,
        value: str,
        confidence: float = 1.0,
        observed_at: float | None = None,
    ) -> str | None:
        """
        Idempotent upsert of an IOC node.

        [META]-006: observed_at for protocol provenance. None → time.time().

        Uses MATCH→CREATE/SET pattern (Kuzu has no MERGE).
        Returns the IOC id or None on failure.
        """
        if self._closed or self._conn is None:
            return None

        node_id = _make_ioc_id(ioc_type, value)
        ts = observed_at if observed_at is not None else time.time()
        try:
            return await asyncio.to_thread(self._upsert_ioc_sync, node_id, ioc_type, value, confidence, ts)
        except Exception as e:
            import logging
            logging.warning(f'[IOCGraph] upsert_ioc failed: {e}')
            return None

    def _upsert_ioc_sync(self, node_id: str, ioc_type: str, value: str, confidence: float, observed_at: float) -> str:
        """Synchronous upsert — runs on _executor thread.

        [META]-006: Uses observed_at for earliest/latest tracking.
        Falls back to legacy when schema lacks temporal fields.
        """
        conn = self._conn
        assert conn is not None

        # Legacy fallback
        if not self._schema_has_temporal:
            res = conn.execute('MATCH (n:IOC) WHERE n.id = $id RETURN n.first_seen', {'id': node_id})
            if not res.has_next():
                conn.execute(
                    'CREATE (:IOC {id: $id, ioc_type: $t, value: $v, '
                    'first_seen: $ts, last_seen: $ts, confidence: $c})',
                    {'id': node_id, 't': ioc_type, 'v': value, 'ts': observed_at, 'c': confidence}
                )
            else:
                conn.execute(
                    'MATCH (n:IOC) WHERE n.id = $id SET n.last_seen = $ts',
                    {'id': node_id, 'ts': observed_at}
                )
            return node_id

        # Temporal schema
        res = conn.execute('MATCH (n:IOC) WHERE n.id = $id RETURN n.earliest_observed, n.latest_observed', {'id': node_id})
        if not res.has_next():
            conn.execute(
                'CREATE (:IOC {id: $id, ioc_type: $t, value: $v, '
                'first_seen: $ts, last_seen: $ts, confidence: $c, '
                'earliest_observed: $eo, latest_observed: $lo, observation_count: 1})',
                {'id': node_id, 't': ioc_type, 'v': value, 'ts': observed_at, 'c': confidence,
                 'eo': observed_at, 'lo': observed_at}
            )
        else:
            conn.execute(
                'MATCH (n:IOC) WHERE n.id = $id '
                'SET n.last_seen = $ts, '
                'n.latest_observed = CASE WHEN $ts > COALESCE(n.latest_observed, $ts) THEN $ts ELSE n.latest_observed END, '
                'n.observation_count = n.observation_count + 1',
                {'id': node_id, 'ts': observed_at}
            )
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

    # SWARM-003: Link Prediction Methods
    async def add_predicted_edge(
        self,
        src_id: str,
        dst_id: str,
        confidence: float,
        method: str,
        adamic_adar: float = 0.0,
        jaccard: float = 0.0,
        pref_attach: float = 0.0,
        common_neighbors: int = 0,
    ) -> bool:
        """
        Add a PREDICTED edge between two IOC nodes.

        SWARM-003: Stores link prediction results in Kuzu for later verification.
        Predicted edges can be promoted to OBSERVED edges once verified.

        Args:
            src_id: Source IOC node ID
            dst_id: Destination IOC node ID
            confidence: Combined confidence score (0-1)
            method: Prediction method used (adamic_adar, jaccard, pref_attach)
            adamic_adar: Adamic-Adar index score
            jaccard: Jaccard coefficient
            pref_attach: Preferential attachment score
            common_neighbors: Number of common neighbors

        Returns:
            True if edge was added, False otherwise
        """
        if self._closed or self._conn is None:
            return False

        try:
            await asyncio.to_thread(
                self._add_predicted_edge_sync,
                src_id, dst_id, confidence, method,
                adamic_adar, jaccard, pref_attach, common_neighbors
            )
            return True
        except Exception as e:
            import logging
            logging.warning(f'[IOCGraph] add_predicted_edge failed: {e}')
            return False

    def _add_predicted_edge_sync(
        self,
        src_id: str,
        dst_id: str,
        confidence: float,
        method: str,
        adamic_adar: float,
        jaccard: float,
        pref_attach: float,
        common_neighbors: int,
    ) -> None:
        """Synchronous predicted edge addition — runs on _executor thread."""
        conn = self._conn
        assert conn is not None

        # Check if PREDICTED edge already exists
        res = conn.execute(
            'MATCH (a:IOC)-[r:PREDICTED]->(b:IOC) WHERE a.id = $ida AND b.id = $idb RETURN r.confidence',
            {'ida': src_id, 'idb': dst_id}
        )

        ts = time.time()

        if not res.has_next():
            # Create new PREDICTED edge
            conn.execute(
                'MATCH (a:IOC), (b:IOC) WHERE a.id = $ida AND b.id = $idb '
                'CREATE (a)-[r:PREDICTED {'
                'confidence: $conf, '
                'adamic_adar: $aa, '
                'jaccard: $jac, '
                'pref_attach: $pa, '
                'common_neighbors: $cn, '
                'method: $method, '
                'created_at: $ts, '
                'verified: false'
                '}]->(b)',
                {
                    'ida': src_id,
                    'idb': dst_id,
                    'conf': confidence,
                    'aa': adamic_adar,
                    'jac': jaccard,
                    'pa': pref_attach,
                    'cn': common_neighbors,
                    'method': method,
                    'ts': ts,
                }
            )
        else:
            # Update existing PREDICTED edge if new one has higher confidence
            existing_conf = res.get_next()[0]
            if confidence > existing_conf:
                conn.execute(
                    'MATCH (a:IOC)-[r:PREDICTED]->(b:IOC) WHERE a.id = $ida AND b.id = $idb '
                    'SET r.confidence = $conf, r.adamic_adar = $aa, r.jaccard = $jac, '
                    'r.pref_attach = $pa, r.common_neighbors = $cn, r.method = $method',
                    {
                        'ida': src_id,
                        'idb': dst_id,
                        'conf': confidence,
                        'aa': adamic_adar,
                        'jac': jaccard,
                        'pa': pref_attach,
                        'cn': common_neighbors,
                        'method': method,
                    }
                )

    async def verify_predicted_edge(self, src_id: str, dst_id: str) -> bool:
        """
        Mark a PREDICTED edge as verified.

        SWARM-003: Called by EntropyFetchBridge micro-sprint when a predicted
        edge is confirmed. Upgrades PREDICTED → OBSERVED relationship.

        Args:
            src_id: Source IOC node ID
            dst_id: Destination IOC node ID

        Returns:
            True if edge was verified, False otherwise
        """
        if self._closed or self._conn is None:
            return False

        try:
            conn = self._conn
            assert conn is not None

            # Mark as verified
            conn.execute(
                'MATCH (a:IOC)-[r:PREDICTED]->(b:IOC) WHERE a.id = $ida AND b.id = $idb SET r.verified = true',
                {'ida': src_id, 'idb': dst_id}
            )

            # Create OBSERVED edge if it doesn't exist
            res = conn.execute(
                'MATCH (a:IOC)-[r:OBSERVED]->(b:IOC) WHERE a.id = $ida AND b.id = $idb RETURN r',
                {'ida': src_id, 'idb': dst_id}
            )
            if not res.has_next():
                conn.execute(
                    'MATCH (a:IOC), (b:IOC) WHERE a.id = $ida AND b.id = $idb '
                    'CREATE (a)-[o:OBSERVED {finding_id: $fid, source_type: $st, first_seen: $ts, last_seen: $ts}]->(b)',
                    {
                        'ida': src_id,
                        'idb': dst_id,
                        'fid': 'link_prediction_verified',
                        'st': 'swarm_003',
                        'ts': time.time(),
                    }
                )

            return True
        except Exception as e:
            import logging
            logging.warning(f'[IOCGraph] verify_predicted_edge failed: {e}')
            return False

    async def get_predicted_edges(
        self,
        min_confidence: float = 0.3,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Get PREDICTED edges above confidence threshold.

        SWARM-003: Returns edges ready for EntropyFetchBridge verification.

        Args:
            min_confidence: Minimum confidence threshold (default: 0.3)
            limit: Maximum number of edges to return (default: 100)

        Returns:
            List of predicted edge dicts with all properties
        """
        if self._closed or self._conn is None:
            return []

        try:
            conn = self._conn
            assert conn is not None

            result = conn.execute(
                'MATCH (a:IOC)-[r:PREDICTED]->(b:IOC) '
                'WHERE r.confidence >= $min_conf AND r.verified = false '
                'RETURN a.id, a.value, a.ioc_type, b.id, b.value, b.ioc_type, '
                'r.confidence, r.adamic_adar, r.jaccard, r.method, r.common_neighbors '
                'ORDER BY r.confidence DESC LIMIT $lim',
                {'min_conf': min_confidence, 'lim': limit}
            )

            edges = []
            while result.has_next():
                row = result.get_next()
                edges.append({
                    'src_id': row[0],
                    'src_value': row[1],
                    'src_type': row[2],
                    'dst_id': row[3],
                    'dst_value': row[4],
                    'dst_type': row[5],
                    'confidence': row[6],
                    'adamic_adar': row[7],
                    'jaccard': row[8],
                    'method': row[9],
                    'common_neighbors': row[10],
                })

            return edges
        except Exception as e:
            import logging
            logging.warning(f'[IOCGraph] get_predicted_edges failed: {e}')
            return []

    async def upsert_ioc_batch(
        self,
        iocs: list[tuple[str, str, float]],
        observed_at: float | None = None,
    ) -> list[str]:
        """
        Batch upsert of IOC nodes.

        [META]-006: observed_at is the original event timestamp.
        When provided, sets earliest_observed / latest_observed on IOC nodes.

        Args:
            iocs: list of (ioc_type, value, confidence) tuples.
            observed_at: Original event timestamp (Unix epoch seconds).
                        When provided, stored as earliest_observed/latest_observed.
                        For backward compat, also accepts 4-tuples:
                        (ioc_type, value, confidence, observed_at).
        Returns:
            List of node IDs newly created in this batch.
            Duplicate calls with the same inputs return [] on subsequent calls.
        """
        if self._closed or self._conn is None or (not iocs):
            return []

        # Normalize: support both 3-tuple and 4-tuple formats
        normalized: list[tuple[str, str, float, float]] = []
        for item in iocs:
            if len(item) == 4:
                ioc_type, value, confidence, obs = item
                normalized.append((ioc_type, value, confidence, obs if obs is not None else (observed_at or time.time())))
            else:
                ioc_type, value, confidence = item
                normalized.append((ioc_type, value, confidence, observed_at or time.time()))

        node_ids = [_make_ioc_id(t, v) for t, v, _, _ in normalized]
        now = time.time()
        try:
            return await asyncio.to_thread(self._upsert_ioc_batch_sync, node_ids, normalized, now)
        except Exception as e:
            import logging
            logging.warning(f'[IOCGraph] upsert_ioc_batch failed: {e}')
            return []

    def _get_existing_ids(self, conn, node_ids: list[str]) -> set[str]:
        """Get existing IOC IDs using UNWIND batch query."""
        res = conn.execute(
            'UNWIND $ids AS nid MATCH (n:IOC) WHERE n.id = nid RETURN n.id',
            {'ids': node_ids}
        )
        existing_ids: set[str] = set()
        try:
            while res.has_next():
                existing_ids.add(res.get_next()[0])
        except Exception:
            existing_ids = set()
        return existing_ids

    def _upsert_legacy(self, conn, node_ids: list[str], iocs: list[tuple], now: float) -> list[str]:
        """Legacy schema upsert without temporal fields."""
        existing_ids = self._get_existing_ids(conn, node_ids)
        new_iocs = [(nid, t, v, c) for nid, (t, v, c, _) in zip(node_ids, iocs, strict=False) if nid not in existing_ids]
        existing = [nid for nid in node_ids if nid in existing_ids]
        created: list[str] = []

        if new_iocs:
            try:
                data = [{'id': nid, 't': t, 'v': v, 'c': c, 'ts': now} for nid, t, v, c in new_iocs]
                conn.execute(
                    'UNWIND $data AS row CREATE (:IOC {id: row.id, ioc_type: row.t, value: row.v, first_seen: row.ts, last_seen: row.ts, confidence: row.c})',
                    {'data': data}
                )
                created = [nid for nid, _, _, _ in new_iocs]
            except Exception:
                for nid, t, v, c in new_iocs:
                    try:
                        conn.execute('CREATE (:IOC {id: $id, ioc_type: $t, value: $v, first_seen: $ts, last_seen: $ts, confidence: $c})',
                                   {'id': nid, 't': t, 'v': v, 'ts': now, 'c': c})
                        created.append(nid)
                    except Exception:  # noqa: BLE001
                        pass

        if existing:
            try:
                conn.execute('UNWIND $ids AS nid MATCH (n:IOC) WHERE n.id = nid SET n.last_seen = $ts', {'ids': existing, 'ts': now})
            except Exception:
                for nid in existing:
                    try:
                        conn.execute('MATCH (n:IOC) WHERE n.id = $id SET n.last_seen = $ts', {'id': nid, 'ts': now})
                    except Exception:  # noqa: BLE001
                        pass
        return created

    def _load_existing_info(self, conn, node_ids) -> dict:
        """Load existing node temporal info."""
        try:
            res = conn.execute('UNWIND $ids AS nid MATCH (n:IOC) WHERE n.id = nid RETURN n.id, n.earliest_observed, n.latest_observed', {'ids': node_ids})
            info = {}
            while res.has_next():
                row = res.get_next(); info[row[0]] = (row[1], row[2])
            return info
        except Exception: return {}

    def _create_new_nodes(self, conn, new_nodes) -> list:
        """Create new nodes with UNWIND or fallback."""
        if not new_nodes: return []
        created = []
        try:
            data = [{'id': nid, 't': t, 'v': val, 'c': c, 'eo': ts, 'lo': ts} for nid, t, val, c, ts in new_nodes]
            conn.execute('UNWIND $data AS row CREATE (:IOC {id: row.id, ioc_type: row.t, value: row.v, first_seen: row.eo, last_seen: row.lo, confidence: row.c, earliest_observed: row.eo, latest_observed: row.lo, observation_count: 1})', {'data': data})
            return [nid for nid, _, _, _, _ in new_nodes]
        except Exception:
            for nid, (t, val, c, ts) in new_nodes:
                try:
                    conn.execute('CREATE (:IOC {id: $id, ioc_type: $t, value: $v, first_seen: $eo, last_seen: $lo, confidence: $c, earliest_observed: $eo, latest_observed: $lo, observation_count: 1})', {'id': nid, 't': t, 'v': val, 'c': c, 'eo': ts, 'lo': ts})
                    created.append(nid)
                except Exception: pass
        return created

    def _update_existing_nodes(self, conn, existing_to_update) -> None:
        """Update existing nodes with UNWIND or fallback."""
        if not existing_to_update: return
        try:
            data = [{'id': nid, 'ts': obs_at} for nid, obs_at, _ in existing_to_update]
            conn.execute('UNWIND $data AS row MATCH (n:IOC) WHERE n.id = row.id SET n.last_seen = row.ts, n.latest_observed = CASE WHEN row.ts > n.latest_observed THEN row.ts ELSE n.latest_observed END, n.observation_count = n.observation_count + 1', {'data': data})
        except Exception:
            for nid, obs_at, _ in existing_to_update:
                try: conn.execute('MATCH (n:IOC) WHERE n.id = $id SET n.last_seen = $ts, n.latest_observed = CASE WHEN $ts > n.latest_observed THEN $ts ELSE n.latest_observed END, n.observation_count = n.observation_count + 1', {'id': nid, 'ts': obs_at})
                except Exception: pass

    def _upsert_temporal(self, conn, node_ids: list[str], iocs: list[tuple], now: float) -> list[str]:
        """Temporal schema upsert with provenance tracking."""
        existing_info = self._load_existing_info(conn, node_ids)
        new_nodes, existing_to_update = [], []
        for nid, (ioc_type, value, confidence, obs_at) in zip(node_ids, iocs, strict=False):
            if nid in existing_info:
                existing_to_update.append((nid, obs_at, existing_info[nid][1] or obs_at))
            else:
                new_nodes.append((nid, ioc_type, value, confidence, obs_at))
        return self._create_new_nodes(conn, new_nodes)

    def _upsert_ioc_batch_sync(
        self,
        node_ids: list[str],
        iocs: list[tuple[str, str, float, float]],
        now: float,
    ) -> list[str]:
        """Synchronous batch upsert — runs on _executor thread.

        [META]-006: Resolves earliest_observed / latest_observed per IOC.
        Falls back to legacy queries when schema lacks temporal fields.
        N+1 elimination via UNWIND batch queries.
        """
        conn = self._conn
        assert conn is not None
        if not node_ids:
            return []
        return self._upsert_legacy(conn, node_ids, iocs, now) if not self._schema_has_temporal else self._upsert_temporal(conn, node_ids, iocs, now)

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

    def _check_existing_edges(self, conn, obs_pairs) -> set:
        """Check which edges already exist."""
        try:
            res = conn.execute('UNWIND $obs AS pair MATCH (a:IOC)-[r:OBSERVED]->(b:IOC) WHERE a.id = pair[0] AND b.id = pair[1] RETURN pair[0], pair[1]', {'obs': obs_pairs})
            existing = set()
            while res.has_next():
                row = res.get_next(); existing.add((row[0], row[1]))
            return existing
        except Exception: return set()

    def _create_missing_edges(self, conn, missing) -> None:
        """Create missing edges with UNWIND or fallback."""
        if not missing: return
        try:
            data = [{'ida': a, 'idb': b, 'fid': f, 'st': s, 'ts': t} for a, b, f, t, s in missing]
            conn.execute('UNWIND $data AS row MATCH (a:IOC), (b:IOC) WHERE a.id = row.ida AND b.id = row.idb CREATE (a)-[r:OBSERVED {finding_id: row.fid, source_type: row.st, first_seen: row.ts, last_seen: row.ts}]->(b)', {'data': data})
        except Exception:
            for a, b, f, t, s in missing:
                try: conn.execute('MATCH (a:IOC), (b:IOC) WHERE a.id = $ida AND b.id = $idb CREATE (a)-[r:OBSERVED {finding_id: $fid, source_type: $st, first_seen: $ts, last_seen: $ts}]->(b)', {'ida': a, 'idb': b, 'fid': f, 'st': s, 'ts': t})
                except Exception: pass

    def _update_existing_edges(self, conn, existing_obs) -> None:
        """Update last_seen for existing edges."""
        if not existing_obs: return
        try:
            data = [{'ida': a, 'idb': b, 'ts': t} for a, b, t in existing_obs]
            conn.execute('UNWIND $data AS row MATCH (a:IOC)-[r:OBSERVED]->(b:IOC) WHERE a.id = row.ida AND b.id = row.idb SET r.last_seen = row.ts', {'data': data})
        except Exception:
            for a, b, t in existing_obs:
                try: conn.execute('MATCH (a:IOC)-[r:OBSERVED]->(b:IOC) WHERE a.id = $ida AND b.id = $idb SET r.last_seen = $ts', {'ida': a, 'idb': b, 'ts': t})
                except Exception: pass

    def _record_observation_batch_sync(self, observations: list[tuple[str, str, str, float, str]]) -> None:
        """Synchronous batch observation — runs on _executor thread. Uses UNWIND batch queries."""
        conn = self._conn; assert conn is not None
        if not observations: return
        obs_pairs = [[a, b] for a, b, _, _, _ in observations]
        existing = self._check_existing_edges(conn, obs_pairs)
        missing = [(a, b, f, t, s) for a, b, f, t, s in observations if (a, b) not in existing]
        existing_obs = [(a, b, t) for a, b, _, t, _ in observations if (a, b) in existing]
        self._create_missing_edges(conn, missing)
        self._update_existing_edges(conn, existing_obs)

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

    def _collect_nodes_arrow(self, conn, ioc_value, ioc_type, k, neighbor_limit):
        """Collect nodes using Arrow export."""
        node_set = {}
        try:
            query = f'MATCH (n:IOC)-[r*1..{k}]-(m:IOC) WHERE n.value = $v AND n.ioc_type = $t AND n.id <> m.id RETURN DISTINCT m.id AS id, m.ioc_type AS ioc_type, m.value AS value, m.confidence AS confidence, m.first_seen AS first_seen, m.last_seen AS last_seen LIMIT {neighbor_limit + 1}'
            arrow_table = conn.execute(query, {'v': ioc_value, 't': ioc_type}).getAsArrow(0)
            for node_data in arrow_table.to_pylist():
                if len(node_set) >= neighbor_limit: return node_set, True
                if (nid := node_data.get('id', '')) and nid not in node_set:
                    node_set[nid] = {'id': nid, 'ioc_type': node_data.get('ioc_type', 'unknown'), 'value': node_data.get('value', ''), 'confidence': float(node_data.get('confidence', 1.0)), 'first_seen': float(node_data.get('first_seen', 0.0)), 'last_seen': float(node_data.get('last_seen', 0.0))}
        except Exception: pass
        return node_set, False

    def _add_seed_node(self, conn, seed_id, ioc_type, ioc_value, node_set):
        """Add seed node to node set."""
        try:
            if (seed_res := conn.execute('MATCH (n:IOC) WHERE n.id = $id RETURN n.ioc_type, n.value, n.confidence, n.first_seen, n.last_seen', {'id': seed_id})).has_next():
                row = seed_res.get_next()
                if seed_id not in node_set:
                    node_set[seed_id] = {'id': seed_id, 'ioc_type': str(row[0]) if row[0] else ioc_type, 'value': str(row[1]) if row[1] else ioc_value, 'confidence': float(row[2]) if row[2] is not None else 1.0, 'first_seen': float(row[3]) if row[3] is not None else 0.0, 'last_seen': float(row[4]) if row[4] is not None else 0.0}
        except Exception: pass

    def _collect_edges_arrow(self, conn, node_ids, node_set, max_edges):
        """Collect edges using Arrow export."""
        edges, truncated, degree_map = [], False, {nid: 0 for nid in node_ids}
        if len(node_ids) < 2: return edges, truncated, degree_map
        edge_set = set()
        try:
            arrow_table = conn.execute('UNWIND $ids AS nid MATCH (a:IOC)-[r:OBSERVED]->(b:IOC) WHERE a.id = nid AND b.id IN $ids RETURN a.id AS src, b.id AS dst, r.finding_id AS finding_id, r.source_type AS source_type, r.last_seen AS last_seen LIMIT $limit', {'ids': node_ids, 'limit': max_edges * 2}).getAsArrow(0)
            for rec in arrow_table.to_pylist():
                if len(edge_set) >= max_edges: truncated = True; break
                if (src := str(rec.get('src', ''))) in node_set and (dst := str(rec.get('dst', ''))) in node_set and (pair := (src, dst)) not in edge_set:
                    edge_set.add(pair); edges.append({'source_id': src, 'target_id': dst, 'finding_id': str(rec.get('finding_id', '')), 'source_type': str(rec.get('source_type', 'unknown')), 'confidence': 1.0, 'last_seen': float(rec.get('last_seen', 0.0) or 0.0)}); degree_map[src] += 1; degree_map[dst] += 1
        except Exception: pass
        return edges, truncated, degree_map

    def _extract_k_hop_subgraph_arrow_sync(self, ioc_value: str, ioc_type: str, k: int, max_nodes: int, max_edges: int) -> dict:
        """Arrow-accelerated subgraph extraction using Kuzu getAsArrow()."""
        if not _PYARROW_AVAILABLE or _pa is None: return self._extract_k_hop_subgraph_sync(ioc_value, ioc_type, k, max_nodes, max_edges)
        k, max_nodes, max_edges = max(1, min(k, 5)), max(1, min(max_nodes, 500)), max(0, min(max_edges, 2000))
        conn = self._conn; assert conn is not None
        seed_id = _make_ioc_id(ioc_type, ioc_value)
        node_set, truncated = self._collect_nodes_arrow(conn, ioc_value, ioc_type, k, max_nodes - 1)
        self._add_seed_node(conn, seed_id, ioc_type, ioc_value, node_set)
        edges, _, degree_map = self._collect_edges_arrow(conn, list(node_set.keys()), node_set, max_edges)
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

    def _collect_nodes_sync(self, conn, ioc_value, ioc_type, k, neighbor_limit):
        """Collect k-hop nodes."""
        node_set, truncated = {}, False
        try:
            res = conn.execute(f'MATCH (n:IOC)-[r*1..{k}]-(m:IOC) WHERE n.value = $v AND n.ioc_type = $t AND n.id <> m.id RETURN DISTINCT m.id AS id, m.ioc_type AS ioc_type, m.value AS value, m.confidence AS confidence, m.first_seen AS first_seen, m.last_seen AS last_seen LIMIT {neighbor_limit + 1}', {'v': ioc_value, 't': ioc_type})
            col_names = res.get_column_names()
            while res.has_next():
                row = res.get_next()
                if len(node_set) >= neighbor_limit: truncated = True; break
                node_data = dict(zip(col_names, row, strict=False))
                if (nid := node_data.get('id', '')) and nid not in node_set:
                    node_set[nid] = {'id': nid, 'ioc_type': node_data.get('ioc_type', 'unknown'), 'value': node_data.get('value', ''), 'confidence': float(node_data.get('confidence', 1.0)), 'first_seen': float(node_data.get('first_seen', 0.0)), 'last_seen': float(node_data.get('last_seen', 0.0))}
        except Exception: return {}, True
        return node_set, truncated

    def _add_seed_node_sync(self, conn, seed_id, ioc_type, ioc_value, node_set):
        """Add seed node."""
        try:
            if (seed_res := conn.execute('MATCH (n:IOC) WHERE n.id = $id RETURN n.ioc_type, n.value, n.confidence, n.first_seen, n.last_seen', {'id': seed_id})).has_next():
                row = seed_res.get_next()
                if seed_id not in node_set:
                    node_set[seed_id] = {'id': seed_id, 'ioc_type': str(row[0]) if row[0] else ioc_type, 'value': str(row[1]) if row[1] else ioc_value, 'confidence': float(row[2]) if row[2] is not None else 1.0, 'first_seen': float(row[3]) if row[3] is not None else 0.0, 'last_seen': float(row[4]) if row[4] is not None else 0.0}
        except Exception: pass

    def _collect_edges_sync(self, conn, node_ids, node_set, max_edges):
        """Collect induced edges."""
        edges, degree_map, truncated = [], {nid: 0 for nid in node_ids}, False
        if len(node_ids) < 2: return edges, degree_map, truncated
        edge_set = set()
        try:
            edge_res = conn.execute('UNWIND $ids AS nid MATCH (a:IOC)-[r:OBSERVED]->(b:IOC) WHERE a.id = nid AND b.id IN $ids RETURN a.id, b.id, r.finding_id, r.source_type, r.last_seen LIMIT $limit', {'ids': node_ids, 'limit': max_edges * 2})
            while edge_res.has_next():
                if len(edge_set) >= max_edges: truncated = True; break
                row = edge_res.get_next()
                if (src := str(row[0]) if row[0] else '') in node_set and (dst := str(row[1]) if row[1] else '') in node_set:
                    if (pair := (src, dst)) not in edge_set:
                        edge_set.add(pair); edges.append({'source_id': src, 'target_id': dst, 'finding_id': str(row[2]) if row[2] else '', 'source_type': str(row[3]) if row[3] else 'unknown', 'confidence': 1.0, 'last_seen': float(row[4]) if row[4] is not None else 0.0}); degree_map[src] += 1; degree_map[dst] += 1
        except Exception: pass
        return edges, degree_map, truncated

    def _extract_k_hop_subgraph_sync(self, ioc_value: str, ioc_type: str, k: int, max_nodes: int, max_edges: int) -> dict:
        """Synchronous subgraph extraction — runs on executor thread."""
        k, max_nodes, max_edges = max(1, min(k, 5)), max(1, min(max_nodes, 500)), max(0, min(max_edges, 2000))
        conn = self._conn; assert conn is not None
        seed_id = _make_ioc_id(ioc_type, ioc_value)
        node_set, truncated = self._collect_nodes_sync(conn, ioc_value, ioc_type, k, max_nodes - 1)
        if not node_set: return self._empty_subgraph_result(ioc_value, ioc_type, k)
        self._add_seed_node_sync(conn, seed_id, ioc_type, ioc_value, node_set)
        edges, degree_map, _ = self._collect_edges_sync(conn, list(node_set.keys()), node_set, max_edges)
        total_nodes, total_edges = len(node_set), len(edges)
        return {'seed_id': seed_id, 'seed_value': ioc_value, 'seed_type': ioc_type, 'k': k, 'nodes': list(node_set.values()), 'edges': edges, 'stats': {'total_nodes': total_nodes, 'total_edges': total_edges, 'density': round(total_edges / (total_nodes * (total_nodes - 1) // 2) if total_nodes > 1 else 0.0, 4), 'max_degree': max(degree_map.values()) if degree_map else 0}, 'truncated': truncated}

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
        except Exception:  # noqa: BLE001
            pass
        edges = 0
        try:
            res = conn.execute('MATCH ()-[r:OBSERVED]->() RETURN count(r)')
            row = res.get_next()
            edges = int(row[0]) if row else 0
        except Exception:  # noqa: BLE001
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
                    logger.warning(f'STIX build failed for {node_id}: {e}')
                    continue
        except Exception as e:
            logger.warning(f'STIX export query failed: {e}')
        if objects:
            try:
                bundle = stix2.Bundle(objects=objects)
                stix2.parse(bundle.serialize())
            except Exception as e:
                logger.warning(f'STIX bundle validation warning: {e}')
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

    def _load_nodes_for_communities(self, conn):
        """Load nodes from Kuzu."""
        nodes, value_to_id = [], {}
        try:
            res = conn.execute('MATCH (n:IOC) RETURN n.value, n.ioc_type')
            node_id = 1
            while res.has_next():
                row = res.get_next()
                value, ioc_type = str(row[0]) if row[0] else '', str(row[1]) if row[1] else 'unknown'
                if value and value not in value_to_id:
                    value_to_id[value] = node_id
                    nodes.append((node_id, value, ioc_type))
                    node_id += 1
        except Exception as e:
            import logging; logging.warning(f'[IOCGraph] Failed to load nodes: {e}')
        return nodes, value_to_id

    def _load_edges_for_communities(self, conn, value_to_id):
        """Load edges from Kuzu."""
        edges = []
        try:
            res = conn.execute('MATCH (a:IOC)-[r:OBSERVED]->(b:IOC) RETURN a.value, b.value, r.confidence')
            while res.has_next():
                row = res.get_next()
                if (src_id := value_to_id.get(str(row[0]) if row[0] else '')) is not None:
                    if (dst_id := value_to_id.get(str(row[1]) if row[1] else '')) is not None:
                        edges.append((src_id, dst_id, float(row[2]) if row[2] is not None else 1.0))
        except Exception as e:
            import logging; logging.warning(f'[IOCGraph] Failed to load edges: {e}')
        return edges

    def _get_communities_rust(self, nodes, edges):
        """Try Rust Louvain community detection."""
        try:
            from hledac.universal.core.rust_backend import rust
            result = rust.raw.module.rust_graph_analytics_all(nodes, edges, 0.85, 1.0)
            if result and isinstance(result, dict):
                communities = result.get('communities')
                if communities and isinstance(communities, dict):
                    return {str(k): int(v) for k, v in communities.items()}
        except Exception:  # noqa: BLE001
            pass
        return None

    def _get_communities_igraph(self, nodes, edges):
        """Fallback: igraph label propagation."""
        try:
            import igraph as ig
            id_to_idx = {nid: i for i, (nid, _, _) in enumerate(nodes)}
            edge_list = [(id_to_idx[s], id_to_idx[d]) for s, d, _ in edges if s in id_to_idx and d in id_to_idx]
            if not edge_list: return {value: i for i, (_, value, _) in enumerate(nodes)}
            g = ig.Graph(n=len(nodes), edges=edge_list, directed=False)
            membership = g.community_label_propagation()
            return {value: membership.membership[i] for i, (_, value, _) in enumerate(nodes)}
        except Exception as e:
            import logging; logging.debug(f'igraph fallback failed: {e}')
        return None

    def _get_communities_sync(self) -> dict[str, int]:
        """Synchronous community detection — runs on _executor thread."""
        conn = self._conn; assert conn is not None
        nodes, value_to_id = self._load_nodes_for_communities(conn)
        if not nodes: return {}
        edges = self._load_edges_for_communities(conn, value_to_id)
        if not edges: return {value: i for i, (_, value, _) in enumerate(nodes)}
        if rust_result := self._get_communities_rust(nodes, edges): return rust_result
        return self._get_communities_igraph(nodes, edges) or {value: 0 for _, value, _ in nodes}

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

        # Try Rust petgraph first (GRAPH-01)
        try:
            from hledac.universal.core.rust_backend import rust
            _rust_ext = rust.raw.module
            result = _rust_ext.rust_graph_analytics_all(nodes, edges, 0.85, 1.0)
            if result and isinstance(result, dict):
                pagerank = result.get('pagerank')
                if pagerank and isinstance(pagerank, dict):
                    score = pagerank.get(target_id, 0.0)
                    return float(score)
        except Exception:  # noqa: BLE001
            pass

        # Fallback: igraph PageRank
        try:
            import igraph as ig

            id_to_idx: dict[int, int] = {}
            for i, (nid, _, _) in enumerate(nodes):
                id_to_idx[nid] = i

            edge_list = [
                (id_to_idx[s], id_to_idx[d])
                for s, d, _ in edges
                if s in id_to_idx and d in id_to_idx
            ]
            if not edge_list:
                return 0.0

            g = ig.Graph(n=len(nodes), edges=edge_list, directed=True)
            pr_scores = g.pagerank(damping=0.85, directed=True)
            target_idx = id_to_idx.get(target_id)
            if target_idx is not None and target_idx < len(pr_scores):
                return float(pr_scores[target_idx])
        except Exception:  # noqa: BLE001
            pass

        return 0.0

    # ------------------------------------------------------------------
    # [META]-010: Graph Visualization Export
    # ------------------------------------------------------------------

    async def export_graph_topology(
        self,
        *,
        max_nodes: int = 1000,
        max_community_size: int = 200,
        include_centrality: bool = True,
    ) -> dict[str, Any]:
        """
        [META]-010: Export graph topology as a Canvas-ready JSON structure.

        Returns a single JSON-serializable dict containing:
          - nodes: list of IOC node dicts (id, value, ioc_type, confidence,
                   first_seen, last_seen, community_id, degree)
          - edges: list of OBSERVED relationships (source, target, finding_id,
                   source_type, confidence, last_seen)
          - communities: dict mapping community_id → {size, ioc_types, cohesion,
                       nodes, truncated}
          - centrality: dict mapping node_id → {degree, pagerank, betweenness,
                       eigenvector, closeness}
          - stats: {total_nodes, total_edges, total_communities, density,
                    max_degree}

        Uses rust_graph_analytics_all (Rust petgraph) when GRAPH-01 is available,
        falls back to igraph for centrality + label propagation for communities.
        M1 8GB safe: bounded by max_nodes/max_community_size.

        Args:
            max_nodes: Cap on exported nodes (default 1000). 0 = unlimited.
            max_community_size: Cap per community (default 200). Truncated
                communities are flagged with 'truncated: true'.
            include_centrality: Compute centrality metrics (default True).
                Set False to skip for very large graphs.

        Returns:
            {"nodes": [...], "edges": [...], "communities": {...},
             "centrality": {...}, "stats": {...}}
        """
        if self._closed or self._conn is None:
            return self._empty_topology_result()

        try:
            return await asyncio.to_thread(
                self._export_graph_topology_sync,
                max_nodes=max_nodes,
                max_community_size=max_community_size,
                include_centrality=include_centrality,
            )
        except Exception as e:
            import logging
            logging.warning(f'[IOCGraph] export_graph_topology failed: {e}')
            return self._empty_topology_result()

    def _empty_topology_result(self) -> dict[str, Any]:
        """Return an empty topology result matching the expected schema."""
        return {
            "nodes": [],
            "edges": [],
            "communities": {},
            "centrality": {},
            "stats": {
                "total_nodes": 0,
                "total_edges": 0,
                "total_communities": 0,
                "density": 0.0,
                "max_degree": 0,
            },
        }

    def _extract_nodes(self, conn, max_nodes: int) -> dict:
        """Phase 1: Extract all nodes from graph."""
        node_map = {}
        try:
            res = conn.execute(
                'MATCH (n:IOC) RETURN n.id, n.value, n.ioc_type, n.confidence, n.first_seen, n.last_seen '
                'ORDER BY n.confidence DESC NULLS LAST')
            col_names = res.get_column_names()
            while res.has_next():
                row = res.get_next()
                data = dict(zip(col_names, row, strict=False))
                nid = str(data.get('id', ''))
                if nid:
                    node_map[nid] = {'id': nid, 'value': str(data.get('value', '')),
                        'ioc_type': str(data.get('ioc_type', 'unknown')),
                        'confidence': float(data.get('confidence', 1.0)),
                        'first_seen': float(data.get('first_seen', 0.0) or 0.0),
                        'last_seen': float(data.get('last_seen', 0.0) or 0.0)}
        except Exception as e:
            import logging
            logging.warning(f'[IOCGraph] topology: node extraction failed: {e}')
            return {}
        if max_nodes > 0 and len(node_map) > max_nodes:
            node_map = {nid: node_map[nid] for nid in list(node_map.keys())[:max_nodes]}
        return node_map

    def _extract_edges(self, conn, node_map) -> tuple:
        """Phase 2: Extract edges where both endpoints are in node_map."""
        edges, degree_map, edge_set = [], {nid: 0 for nid in node_map}, set()
        try:
            res = conn.execute(
                'MATCH (a:IOC)-[r:OBSERVED]->(b:IOC) RETURN a.id, b.id, r.finding_id, r.source_type, r.confidence, r.last_seen')
            col_names = res.get_column_names()
            while res.has_next():
                row = res.get_next()
                data = dict(zip(col_names, row, strict=False))
                src, dst = str(data.get('a.id', '')), str(data.get('b.id', ''))
                if src in node_map and dst in node_map and (src, dst) not in edge_set:
                    edge_set.add((src, dst))
                    edges.append({'source': src, 'target': dst,
                        'finding_id': str(data.get('finding_id', '') or ''),
                        'source_type': str(data.get('source_type', 'unknown') or 'unknown'),
                        'confidence': float(data.get('confidence', 1.0)),
                        'last_seen': float(data.get('last_seen', 0.0) or 0.0)})
                    degree_map[src] = degree_map.get(src, 0) + 1
                    degree_map[dst] = degree_map.get(dst, 0) + 1
        except Exception as e:
            import logging
            logging.warning(f'[IOCGraph] topology: edge extraction failed: {e}')
        return edges, degree_map

    def _compute_igraph_communities(self, node_map, edges) -> tuple:
        """Compute communities using igraph."""
        import igraph as ig
        id_to_idx, idx_to_nid = {nid: i for i, nid in enumerate(node_map)}, {i: nid for i, nid in enumerate(node_map)}
        edge_list = [(id_to_idx[e['source']], id_to_idx[e['target']]) for e in edges if e['source'] in id_to_idx and e['target'] in id_to_idx]
        if edge_list:
            g = ig.Graph(n=len(node_map), edges=edge_list, directed=False)
            membership = g.community_label_propagation()
            return {nid: int(membership.membership[i] if hasattr(membership, 'membership') else membership[i]) for i, nid in enumerate(node_map)}, id_to_idx, idx_to_nid
        return {nid: i for i, nid in enumerate(node_map)}, id_to_idx, idx_to_nid

    def _try_rust_communities(self, communities, node_map, edges, id_to_idx, idx_to_nid) -> bool:
        """Try Rust enrichment for communities."""
        if len(set(communities.values())) <= 1 and edges:
            try:
                from hledac.universal.core.rust_backend import rust
                nodes_compact = [(i + 1, node_map[nid]['value'], node_map[nid]['ioc_type']) for i, nid in enumerate(node_map)]
                edges_compact = [(id_to_idx[e['source']] + 1, id_to_idx[e['target']] + 1, e['confidence']) for e in edges
                                if e['source'] in id_to_idx and e['target'] in id_to_idx]
                if nodes_compact and edges_compact:
                    rust_result = rust.raw.module.rust_graph_analytics_all(nodes_compact, edges_compact, 0.85, 1.0)
                    if rust_result and (rust_comm := rust_result.get('communities', {})):
                        communities.clear()
                        communities.update({idx_to_nid[int(k) - 1]: int(v) for k, v in rust_comm.items() if int(k) - 1 in idx_to_nid})
                        return True
            except Exception:
                pass
        return False

    def _build_community_info(self, communities, node_map, edges, max_community_size) -> dict:
        """Build community info dict."""
        community_groups, community_info = {}, {}
        for nid, cid in communities.items():
            community_groups.setdefault(cid, []).append(nid)
        for cid, members in community_groups.items():
            node_list = [node_map[nid] for nid in members if nid in node_map]
            size, member_set = len(members), set(members)
            internal = sum(1 for e in edges if e['source'] in member_set and e['target'] in member_set)
            community_info[str(cid)] = {'size': size, 'truncated': size > max_community_size,
                'ioc_types': list({n['ioc_type'] for n in node_list}),
                'cohesion': round(internal / (size * (size - 1) / 2) if size > 1 else 1.0, 4),
                'nodes': [nid for nid in members if nid in node_map][:max_community_size]}
        return community_info

    def _detect_communities(self, node_map, edges, max_community_size) -> tuple:
        """Phase 3: Detect communities using igraph with Rust fallback."""
        try:
            communities, id_to_idx, idx_to_nid = self._compute_igraph_communities(node_map, edges)
            self._try_rust_communities(communities, node_map, edges, id_to_idx, idx_to_nid)
        except Exception as e:
            import logging
            logging.debug(f'[IOCGraph] topology: community detection failed: {e}')
            communities = {nid: 0 for nid in node_map}
        community_info = self._build_community_info(communities, node_map, edges, max_community_size)
        return communities, {}, community_info

    def _assemble_topology_nodes_with_sort(
        self,
        node_map: dict,
        community_groups: dict,
        degree_map: dict,
        centrality: dict,
    ) -> list:
        """Build sorted node entries for topology export."""
        nodes_result = []
        comm_size = {cid: len(members) for cid, members in community_groups.items()}
        for nid, node in node_map.items():
            node_entry = {**node, 'id': nid, 'community_id': node.get('community_id', -1)}
            if centrality and nid in centrality:
                node_entry['centrality'] = centrality[nid]
            nodes_result.append(node_entry)
        # Sort by community size (desc), then by degree (desc)
        nodes_result.sort(
            key=lambda n: (-comm_size.get(n.get('community_id', -1), 0), -n.get('degree', 0))
        )
        return nodes_result

    def _compute_topology_stats(
        self,
        node_map: dict,
        edges: list,
        community_groups: dict,
        degree_map: dict,
    ) -> dict:
        """Compute graph topology statistics."""
        total_nodes = len(node_map)
        total_edges = len(edges)
        total_communities = len(community_groups)
        # Density: edges / (n * (n-1) / 2)
        max_possible = total_nodes * (total_nodes - 1) / 2 if total_nodes > 1 else 0.0
        density = round(total_edges / max_possible, 4) if max_possible > 0 else 0.0
        max_degree = max(degree_map.values()) if degree_map else 0
        return {
            'total_nodes': total_nodes,
            'total_edges': total_edges,
            'total_communities': total_communities,
            'density': density,
            'max_degree': max_degree,
        }

    def _export_graph_topology_sync(self, *, max_nodes: int, max_community_size: int, include_centrality: bool) -> dict:
        """Synchronous graph topology export — runs on _executor thread."""
        conn = self._conn
        assert conn is not None
        
        # Phase 1: Extract nodes
        node_map = self._extract_topology_nodes(conn, max_nodes)
        if not node_map:
            return self._empty_topology_result()

        # Initialize degree tracking
        for nid in node_map:
            node_map[nid]['degree'] = 0

        # Phase 2: Extract edges (SAFE-5: bounded to prevent unbounded memory growth)
        edges, degree_map = self._extract_topology_edges(conn, node_map, max_edges=max_nodes * 10)
        for nid, deg in degree_map.items():
            if nid in node_map:
                node_map[nid]['degree'] = deg

        # Phase 3: Detect communities
        communities, community_groups, community_info = self._detect_topology_communities(
            node_map, edges, max_community_size)

        # Assign community IDs
        for nid in node_map:
            node_map[nid]['community_id'] = communities.get(nid, -1)

        # Phase 4: Compute centrality
        centrality = self._compute_topology_for_export(
            node_map, edges, degree_map, include_centrality)

        # Phase 5: Assemble result
        return self._assemble_topology_result(
            node_map, edges, community_info, centrality, community_groups, degree_map)

    # -------------------------------------------------------------------------
    # Topology Export Helpers (extracted to reduce complexity)
    # -------------------------------------------------------------------------

    def _extract_topology_nodes(self, conn, max_nodes: int) -> dict:
        """Phase 1: Extract top-scored nodes from graph.

        SAFE-5: Uses SQL LIMIT to bound memory at database level.
        Previously loaded ALL nodes then truncated — now limits at query time.
        """
        node_map = {}
        try:
            # SAFE-5: LIMIT pushed to database to avoid unbounded memory growth
            # max_nodes=0 means no limit (used for small graphs)
            limit_clause = f'LIMIT {max_nodes}' if max_nodes > 0 else ''
            res = conn.execute(
                f'MATCH (n:IOC) RETURN n.id, n.value, n.ioc_type, n.confidence, n.first_seen, n.last_seen '
                f'ORDER BY n.confidence DESC NULLS LAST {limit_clause}')
            col_names = res.get_column_names()
            while res.has_next():
                row = res.get_next()
                data = dict(zip(col_names, row, strict=False))
                nid = str(data.get('id', ''))
                if nid:
                    node_map[nid] = {
                        'id': nid,
                        'value': str(data.get('value', '')),
                        'ioc_type': str(data.get('ioc_type', 'unknown')),
                        'confidence': float(data.get('confidence', 1.0)),
                        'first_seen': float(data.get('first_seen', 0.0) or 0.0),
                        'last_seen': float(data.get('last_seen', 0.0) or 0.0),
                    }
        except Exception as e:
            import logging
            logging.warning(f'[IOCGraph] topology: node extraction failed: {e}')
            return {}
        # Post-load truncate only as safety net (shouldn't be needed with LIMIT)
        if max_nodes > 0 and len(node_map) > max_nodes:
            node_map = {nid: node_map[nid] for nid in list(node_map.keys())[:max_nodes]}
        return node_map

    def _extract_topology_edges(self, conn, node_map, max_edges: int = 50000) -> tuple[list, dict]:
        """Phase 2: Extract edges where both endpoints are in node_map.

        SAFE-5: Bounded edge extraction to prevent unbounded memory growth.
        - Uses IN clause with node IDs to filter at database level
        - Limits total edges via LIMIT clause
        - Truncates edge_set to prevent O(edges) memory growth
        """
        edges, degree_map, edge_set = [], {nid: 0 for nid in node_map}, set()
        node_ids = list(node_map.keys())
        max_edges_cap = min(max_edges, len(node_ids) * 10)  # Cap at 10 edges per node average
        
        try:
            # SAFE-5: Filter edges at database level using IN clause
            # This prevents loading all graph edges when we only need subgraph edges
            if node_ids:
                # Build parameterized IN clause for source nodes
                placeholders = ', '.join(['?' for _ in node_ids[:1000]])  # Limit IN clause size
                sql = f'''
                    MATCH (a:IOC)-[r:OBSERVED]->(b:IOC)
                    WHERE a.id IN ({placeholders}) AND b.id IN ({placeholders})
                    RETURN a.id, b.id, r.finding_id, r.source_type, r.confidence, r.last_seen
                    LIMIT {max_edges_cap}
                '''
                # Use first 1000 node IDs for IN clause (covers most use cases)
                params = node_ids[:1000]
                
                try:
                    res = conn.execute(sql, params)
                except Exception:
                    # Fallback: simpler query if parameterized fails
                    res = conn.execute(
                        f'MATCH (a:IOC)-[r:OBSERVED]->(b:IOC) RETURN a.id, b.id, r.finding_id, r.source_type, r.confidence, r.last_seen LIMIT {max_edges_cap}')
            else:
                res = None
            
            if res:
                col_names = res.get_column_names()
                while res.has_next():
                    row = res.get_next()
                    data = dict(zip(col_names, row, strict=False))
                    src, dst = str(data.get('a.id', '')), str(data.get('b.id', ''))
                    if src in node_map and dst in node_map and (src, dst) not in edge_set:
                        edge_set.add((src, dst))
                        edges.append({
                            'source': src,
                            'target': dst,
                            'finding_id': str(data.get('finding_id', '') or ''),
                            'source_type': str(data.get('source_type', 'unknown') or 'unknown'),
                            'confidence': float(data.get('confidence', 1.0)),
                            'last_seen': float(data.get('last_seen', 0.0) or 0.0),
                        })
                        degree_map[src] = degree_map.get(src, 0) + 1
                        degree_map[dst] = degree_map.get(dst, 0) + 1
                        
                        # SAFE-5: Early exit when reaching edge limit
                        if len(edges) >= max_edges_cap:
                            break
        except Exception as e:
            import logging
            logging.warning(f'[IOCGraph] topology: edge extraction failed: {e}')
        
        # SAFE-5: Clear edge_set to free memory (edges list already bounded)
        edge_set.clear()
        return edges, degree_map

    def _detect_topology_communities(self, node_map, edges, max_community_size: int) -> tuple:
        """Phase 3: Detect communities using label propagation."""
        communities = {nid: i for i, nid in enumerate(node_map)}
        community_groups: dict = {}
        community_info: dict = {}

        try:
            import igraph as ig
            id_to_idx = {nid: i for i, nid in enumerate(node_map)}
            idx_to_nid = {i: nid for i, nid in enumerate(node_map)}
            edge_list = [(id_to_idx[e['source']], id_to_idx[e['target']]) for e in edges
                        if e['source'] in id_to_idx and e['target'] in id_to_idx]

            if edge_list:
                g = ig.Graph(n=len(node_map), edges=edge_list, directed=False)
                membership = g.community_label_propagation()
                communities = {
                    idx_to_nid[i]: int(membership.membership[i] if hasattr(membership, 'membership') else membership[i])
                    for i, nid in enumerate(node_map)
                }

                # Build community groups with truncation
                for nid, cid in communities.items():
                    if cid not in community_groups:
                        community_groups[cid] = []
                    if len(community_groups[cid]) < max_community_size:
                        community_groups[cid].append(nid)
                    if cid not in community_info:
                        community_info[cid] = {'size': 0, 'ioc_types': set(), 'truncated': False}
                    community_info[cid]['size'] += 1
                    community_info[cid]['ioc_types'].add(node_map[nid]['ioc_type'])
                    if len(community_groups[cid]) >= max_community_size:
                        community_info[cid]['truncated'] = True
        except Exception as e:
            import logging
            logging.debug(f'[IOCGraph] topology: community detection failed: {e}')

        # Convert sets to lists for JSON serialization
        for cid in community_info:
            community_info[cid]['ioc_types'] = list(community_info[cid]['ioc_types'])

        return communities, community_groups, community_info

    def _compute_topology_for_export(self, node_map, edges, degree_map, include_centrality: bool) -> dict:
        """Phase 4: Compute centrality metrics."""
        if not include_centrality:
            return {}
        try:
            return self._compute_topology_centrality(node_map=node_map, edges=edges, degree_map=degree_map)
        except Exception as e:
            import logging
            logger.warning(f'[IOCGraph] topology: centrality failed: {e}')
            return {}

    def _assemble_topology_result(self, node_map, edges, community_info, centrality, community_groups, degree_map) -> dict:
        """Phase 5: Assemble final topology result."""
        nodes_result = self._assemble_topology_nodes_with_sort(
            node_map, community_groups, degree_map, centrality)
        stats = self._compute_topology_stats(
            node_map, edges, community_groups, degree_map)

        return {
            'nodes': nodes_result,
            'edges': edges,
            'communities': community_info,
            'centrality': centrality,
            'stats': stats,
        }

    def _init_centrality_map(self, node_ids, degree_map) -> dict:
        """Initialize centrality map with degree scores."""
        return {nid: {'degree': float(degree_map.get(nid, 0)), 'pagerank': 0.0,
            'betweenness': 0.0, 'eigenvector': 0.0, 'closeness': 0.0} for nid in node_ids}

    def _try_rust_centrality(self, centrality, node_ids, node_map, edges, value_to_id):
        """Try Rust petgraph for centrality (GRAPH-01)."""
        try:
            # B8-fix: Use batch_centrality_all with adjacency-list builder (matches graph_rag.py:945)
            from hledac.universal.core.rust_backend import rust
            _rust_ext = rust.raw.module
            # Build adjacency list: {node_id: [neighbor_ids]}
            adjacency: dict[int, list[int]] = {value_to_id[nid]: [] for nid in node_ids}
            for e in edges:
                src = value_to_id.get(e['source'])
                dst = value_to_id.get(e['target'])
                if src is not None and dst is not None:
                    adjacency.setdefault(src, [])
                    adjacency.setdefault(dst, [])
                    adjacency[src].append(dst)
                    adjacency[dst].append(src)
            adj_list: list[tuple[int, list[int]]] = [(nid, neighbors) for nid, neighbors in adjacency.items()]
            if adj_list:
                rust_result = _rust_ext.batch_centrality_all(adj_list)
                if rust_result:
                    for nid, metrics in rust_result:
                        if isinstance(metrics, dict):
                            centrality[nid]['pagerank'] = float(metrics.get('pagerank', 0.0))
                            centrality[nid]['betweenness'] = float(metrics.get('betweenness', 0.0))
                            centrality[nid]['closeness'] = float(metrics.get('closeness', 0.0))
                            centrality[nid]['eigenvector'] = float(metrics.get('eigenvector', 0.0))
                    return True
        except Exception:
            pass
        return False

    def _try_igraph_centrality(self, centrality, node_ids, edges, node_map, n):
        """Try igraph for centrality (fallback)."""
        try:
            import igraph as ig
            id_to_idx = {i + 1: i for i, nid in enumerate(node_ids)}
            edge_list = [(id_to_idx[e['source']], id_to_idx[e['target']]) for e in edges
                        if e['source'] in node_map and e['target'] in node_map]
            if not edge_list:
                return False
            g = ig.Graph(n=n, edges=edge_list, directed=False)
            g.vs['name'] = node_ids
            for metric, func in [('pagerank', g.pagerank), ('betweenness', g.betweenness if n <= 2000 else None),
                                 ('eigenvector', g.eigenvector_centrality), ('closeness', g.closeness)]:
                if func:
                    try:
                        scores = func(directed=False) if metric != 'pagerank' else func(weights='weight' if g.is_weighted() else None)
                        for i, nid in enumerate(node_ids):
                            centrality[nid][metric] = float(scores[i])
                    except Exception:
                        pass
            return True
        except Exception as e:
            import logging
            logging.debug(f'[IOCGraph] topology: igraph centrality failed: {e}')
            return False

    def _compute_topology_centrality(self, node_map, edges, degree_map) -> dict:
        """Compute PageRank, betweenness, eigenvector, and closeness centrality."""
        node_ids = list(node_map.keys())
        n = len(node_ids)
        centrality = {}
        if n == 0:
            return centrality
        centrality = self._init_centrality_map(node_ids, degree_map)
        if n > 5000:
            import logging
            logging.debug(f'[IOCGraph] topology centrality: {n} nodes > 5000 limit')
            return centrality
        value_to_id = {nid: i + 1 for i, nid in enumerate(node_ids)}
        if self._try_rust_centrality(centrality, node_ids, node_map, edges, value_to_id):
            return centrality
        self._try_igraph_centrality(centrality, node_ids, edges, node_map, n)
        return centrality

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

    def _load_graph_for_centrality(self, conn) -> tuple:
        """Load nodes and edges from Kuzu."""
        nodes, value_to_id = [], {}
        try:
            res = conn.execute('MATCH (n:IOC) RETURN n.value, n.ioc_type')
            nid = 1
            while res.has_next():
                row = res.get_next()
                if (value := str(row[0]) if row[0] else '') and value not in value_to_id:
                    value_to_id[value] = nid
                    nodes.append((nid, value, str(row[1]) if row[1] else 'unknown'))
                    nid += 1
        except Exception: return [], {}
        edges = []
        try:
            res = conn.execute('MATCH (a:IOC)-[r:OBSERVED]->(b:IOC) RETURN a.value, b.value, r.confidence')
            while res.has_next():
                row = res.get_next()
                if (src_id := value_to_id.get(str(row[0]) if row[0] else '')) is not None:
                    if (dst_id := value_to_id.get(str(row[1]) if row[1] else '')) is not None:
                        edges.append((src_id, dst_id, float(row[2]) if row[2] is not None else 1.0))
        except Exception: pass
        return nodes, value_to_id, edges

    def _get_rust_pagerank(self, nodes, edges, target_id) -> float:
        """Try Rust PageRank."""
        try:
            from hledac.universal.core.rust_backend import rust
            result = rust.raw.module.rust_graph_analytics_all(nodes, edges, 0.85, 1.0)
            if result and isinstance(result, dict):
                pagerank = result.get('pagerank')
                if pagerank and isinstance(pagerank, dict):
                    return float(pagerank.get(target_id, 0.0))
        except Exception: pass
        return 0.0

    def _get_igraph_pagerank(self, nodes, edges, value_to_id, target_id) -> float:
        """Fallback: igraph PageRank."""
        try:
            import igraph as ig
            id_to_idx = {nid: i for i, (nid, _, _) in enumerate(nodes)}
            edge_list = [(id_to_idx[s], id_to_idx[d]) for s, d, _ in edges if s in id_to_idx and d in id_to_idx]
            if not edge_list: return 0.0
            target_idx = id_to_idx.get(target_id)
            if target_idx is None: return 0.0
            return float(ig.Graph(n=len(nodes), edges=edge_list, directed=True).pagerank(damping=0.85, directed=True)[target_idx])
        except Exception: return 0.0

    def _get_centrality_sync(self, ioc_value: str) -> float:
        """Synchronous PageRank computation — runs on _executor thread."""
        conn = self._conn; assert conn is not None
        nodes, value_to_id, edges = self._load_graph_for_centrality(conn)
        if not nodes: return 0.0
        if (target_id := value_to_id.get(ioc_value)) is None: return 0.0
        if score := self._get_rust_pagerank(nodes, edges, target_id): return score
        return self._get_igraph_pagerank(nodes, edges, value_to_id, target_id)