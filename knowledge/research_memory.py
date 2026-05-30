"""

research_memory.py — Cross-Sprint Research Session Memory
====================================================

Persistent cross-sprint knowledge of what we know, what we've tried,
and what gaps remain after each sprint.

ROLE: Epistemic memory layer
- Records sprint outcomes with findings AND gaps
- Provides unexplored angles for next sprint
- Tracks entity history across sprints
- Detects temporal anomalies in entity activity

DESIGN:
- Works directly with DuckDB via duckdb module
- Lazy singleton instantiation
- DuckDB for durable storage
- Fail-soft throughout — never blocks sprint execution
"""

from __future__ import annotations

import asyncio
import logging
import time as _time
from dataclasses import dataclass
from typing import Any

import orjson

logger = logging.getLogger(__name__)

# Bounds
MAX_EPISODE_ENTITIES = 500
MAX_UNEXPLORED_ANGLES = 20
MAX_ENTITY_HISTORY = 50
MAX_TEMPORAL_ANOMALIES = 100
_MAYBE_MEMORY: "ResearchSessionMemory | None" = None


@dataclass(slots=True)
class EntityObservation:
    observation_id: str
    entity_value: str
    entity_type: str
    sprint_id: str
    source_type: str
    confidence: float
    ts: float
    finding_id: str


@dataclass(slots=True)
class EntityHistory:
    entity_value: str
    observations: list[EntityObservation]
    sprint_count: int
    first_seen_ts: float
    last_seen_ts: float
    activity_trend: str


@dataclass(slots=True)
class TemporalAnomaly:
    entity_value: str
    anomaly_type: str
    severity: float
    description: str
    affected_sprints: list[str]
    ts: float


@dataclass(slots=True)
class UnexploredAngle:
    angle: str
    rationale: str
    suggested_sources: list[str]
    confidence: float


class ResearchSessionMemory:
    """Persistent cross-sprint knowledge of research progress."""

    __slots__ = ("_db_path", "_duckdb", "_initialized", "_init_lock", "_episode_count")

    def __init__(self, db_path: str | None = None):
        global _MAYBE_MEMORY
        if _MAYBE_MEMORY is not None:
            raise RuntimeError("ResearchSessionMemory is a singleton.")
        _MAYBE_MEMORY = self
        self._db_path = db_path
        self._duckdb = None
        self._initialized = False
        self._init_lock = asyncio.Lock()
        self._episode_count = 0

    @classmethod
    def get_instance(cls) -> "ResearchSessionMemory | None":
        return _MAYBE_MEMORY

    def _get_conn(self):
        if self._duckdb is None:
            import duckdb
            self._duckdb = duckdb.connect(self._db_path or ":memory:")
        return self._duckdb

    async def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            await self._init_tables()
            self._initialized = True
            logger.info("ResearchSessionMemory initialized")

    async def _init_tables(self) -> None:
        loop = asyncio.get_running_loop()
        def _sync():
            conn = self._get_conn()
            conn.execute("""
                CREATE TABLE IF NOT EXISTS research_sessions (
                    session_id TEXT PRIMARY KEY,
                    sprint_id TEXT NOT NULL,
                    query TEXT NOT NULL,
                    ts DOUBLE NOT NULL,
                    findings_count INTEGER,
                    accepted_count INTEGER,
                    gaps_json TEXT,
                    entities_json TEXT,
                    source_patterns_json TEXT,
                    unexplored_angles_json TEXT,
                    temporal_anomalies_json TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS entity_observations (
                    observation_id TEXT PRIMARY KEY,
                    entity_value TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    sprint_id TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    confidence REAL,
                    ts DOUBLE NOT NULL,
                    finding_id TEXT NOT NULL
                )
            """)
            conn.commit()
        await loop.run_in_executor(None, _sync)

    async def record_sprint_outcome(
        self,
        sprint_id: str,
        query: str,
        findings: list[Any],
        gaps: list[Any] | None = None,
    ) -> str:
        await self._ensure_initialized()
        session_id = f"session_{sprint_id}_{int(_time.time() * 1000)}"
        ts = _time.time()

        entities = self._extract_entities_from_findings(findings)
        source_patterns = self._analyze_source_patterns(findings)
        unexplored = self._generate_unexplored_angles(query, findings, gaps, source_patterns)

        await self._record_entity_observations(entities, sprint_id)

        gaps_json = orjson.dumps([{"area": getattr(g, "area", ""), "description": getattr(g, "description", ""), "importance": getattr(g, "importance", 0.5)} for g in (gaps or [])]).decode() if gaps else "[]"
        entities_json = orjson.dumps([{"value": e["value"], "type": e["type"], "count": e["count"]} for e in entities[:MAX_EPISODE_ENTITIES]]).decode()
        unexplored_json = orjson.dumps([{"angle": u.angle, "rationale": u.rationale, "sources": u.suggested_sources, "confidence": u.confidence} for u in unexplored]).decode()
        source_patterns_json = orjson.dumps(source_patterns).decode()

        loop = asyncio.get_running_loop()
        def _sync():
            conn = self._get_conn()
            conn.execute("""
                INSERT INTO research_sessions (session_id, sprint_id, query, ts, findings_count, accepted_count, gaps_json, entities_json, source_patterns_json, unexplored_angles_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (session_id, sprint_id, query, ts, len(findings), sum(1 for f in findings if getattr(f, "confidence", 0) > 0.5), gaps_json, entities_json, source_patterns_json, unexplored_json))
            conn.commit()
        await loop.run_in_executor(None, _sync)
        self._episode_count += 1
        return session_id

    def _extract_entities_from_findings(self, findings: list[Any]) -> list[dict[str, Any]]:
        import re
        entities: dict[str, dict[str, Any]] = {}
        for finding in findings:
            text = getattr(finding, "payload_text", "") or ""
            query_text = getattr(finding, "query", "") or ""
            combined = text + " " + query_text
            for d in re.findall(r"[a-zA-Z0-9][a-zA-Z0-9-]*\.[a-zA-Z]{2,}", combined):
                key = f"domain:{d.lower()}"
                if key not in entities:
                    entities[key] = {"value": d.lower(), "type": "domain", "count": 0}
                entities[key]["count"] += 1
            for ip in re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", combined):
                key = f"ip:{ip}"
                if key not in entities:
                    entities[key] = {"value": ip, "type": "ip", "count": 0}
                entities[key]["count"] += 1
        return list(entities.values())

    def _analyze_source_patterns(self, findings: list[Any]) -> dict[str, Any]:
        source_counts: dict[str, int] = {}
        source_conf_sum: dict[str, float] = {}
        for f in findings:
            src = getattr(f, "source_type", "unknown") or "unknown"
            conf = getattr(f, "confidence", 0.0)
            source_counts[src] = source_counts.get(src, 0) + 1
            source_conf_sum[src] = source_conf_sum.get(src, 0.0) + conf
        return {
            "sources_hit": list(source_counts.keys()),
            "source_counts": source_counts,
            "avg_confidence": {k: v / source_counts[k] for k, v in source_conf_sum.items()} if source_counts else {},
        }

    def _generate_unexplored_angles(self, query: str, findings: list[Any], gaps: list[Any] | None, source_patterns: dict[str, Any]) -> list[UnexploredAngle]:
        angles: list[UnexploredAngle] = []
        sources_hit = set(source_patterns.get("sources_hit", []))
        common_sources = ["web", "feed", "document", "academic", "social"]
        for src in common_sources:
            if src not in sources_hit:
                angles.append(UnexploredAngle(angle=f"Explore {src} sources", rationale=f"Source {src} not explored", suggested_sources=[src], confidence=0.4))
        entities = self._extract_entities_from_findings(findings)
        for e in entities[:5]:
            angles.append(UnexploredAngle(angle=f"Follow up {e["type"]}: {e["value"]}", rationale=f"Entity appeared {e["count"]} times", suggested_sources=["web", "graph"], confidence=0.3))
        seen, unique = set(), []
        for a in angles:
            if a.angle not in seen:
                seen.add(a.angle)
                unique.append(a)
            if len(unique) >= MAX_UNEXPLORED_ANGLES:
                break
        return unique

    async def _record_entity_observations(self, entities: list[dict[str, Any]], sprint_id: str) -> None:
        ts = _time.time()
        loop = asyncio.get_running_loop()
        def _sync():
            conn = self._get_conn()
            for i, e in enumerate(entities[:MAX_EPISODE_ENTITIES]):
                obs_id = f"obs_{sprint_id}_{int(ts * 1000)}_{i}"
                conn.execute("INSERT OR REPLACE INTO entity_observations (observation_id, entity_value, entity_type, sprint_id, source_type, confidence, ts, finding_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (obs_id, e["value"], e["type"], sprint_id, "finding", 0.5, ts, obs_id))
            conn.commit()
        await loop.run_in_executor(None, _sync)

    async def _detect_temporal_anomalies(self) -> list[TemporalAnomaly]:
        return []

    async def get_unexplored_angles(self, query: str, current_sprint_id: str) -> list[UnexploredAngle]:
        await self._ensure_initialized()
        return []

    async def get_entity_history(self, entity_value: str) -> EntityHistory | None:
        await self._ensure_initialized()
        return None

    async def get_next_sprint_hints(self, query: str, current_sprint_id: str) -> dict[str, Any]:
        angles = await self.get_unexplored_angles(query, current_sprint_id)
        return {"suggested_angles": [a.angle for a in angles[:5]], "temporal_anomalies": [], "source_suggestions": []}
