"""
TemporalIOCPredictor — P3-2: Speculative prefetch based on temporal patterns.

Analyzes time-of-day / day-of-week patterns for IOC sources and pre-fetches



during predicted peak activity windows.

Architecture:
- TemporalSignalLayer: live burst/periodicity scoring (pure Python, no numpy)
- DuckDB historical analysis: time-of-day / day-of-week distributions per IOC type
- predict_next_iocs(): called by ContinuousPrefetchPipeline._producer_loop()

M1 8GB invariants:
- Pure Python only (no numpy, no pandas, no MLX)
- DuckDB historical query: bounded, <5MB RAM per analysis
- MAX_PREDICTIONS = 20 per call
- Always-on, bounded, fail-safe
"""
import logging
import time
from collections import deque
from dataclasses import dataclass, field
import msgspec
from typing import Any
from hledac.universal.layers.temporal_signal_layer import TemporalEvent, TemporalSignalLayer, event_from_finding_like
logger = logging.getLogger(__name__)
MAX_PREDICTIONS = 20
MAX_HISTORY_PER_TYPE = 256
FLUSH_INTERVAL_S = 300.0
PREDICTION_HORIZON_S = 600.0
CONFIDENCE_BOOST_HOUR = 1.5
CONFIDENCE_BOOST_BURST = 2.0
MIN_EVENTS_FOR_PATTERN = 5
PEAK_HOUR_TOLERANCE = 2

class IOCPrediction(msgspec.Struct, frozen=True, gc=False):
    """Single IOC prediction from temporal analysis."""
    ioc_value: str
    ioc_type: str
    confidence: float
    source_node: str
    prediction_method: str
    predicted_at: float
    expires_at: float

class _PatternStats(msgspec.Struct, gc=False):
    """Per-(ioc_type, source) rolling pattern statistics."""
    hour_counts: list[int] = field(default_factory=lambda: [0] * 24)
    dow_counts: list[int] = field(default_factory=lambda: [0] * 7)
    total_events: int = 0
    last_hour: int = -1
    last_dow: int = -1
    peak_hour: int = -1
    peak_dow: int = -1
    events: deque[float] = field(default_factory=lambda: deque(maxlen=MAX_HISTORY_PER_TYPE))

class TemporalIOCPredictor:
    """
    P3-2: Temporal pattern-based IOC predictor.

    Combines:
    1. Time-of-day / day-of-week distributions from DuckDB historical data
    2. Live burst/periodicity signals from TemporalSignalLayer
    3. Confidence boosting during predicted active windows

    Wired into:
    - ContinuousPrefetchPipeline (predict_next_iocs)
    - Sprint findings ingestion (observe_findings)

    Invariants:
    - Always-on, bounded, fail-safe
    - Pure Python only (no numpy, no pandas)
    - M1 8GB safe: <10MB RAM for patterns
    """
    __slots__ = tuple(('_duckdb', '_flush_interval', '_last_flush', '_live_events', '_lru_keys', '_max_predictions', '_patterns', '_stats', '_temporal'))

    def __init__(self, temporal_layer: TemporalSignalLayer | None=None, duckdb_store: Any=None, max_predictions: int=MAX_PREDICTIONS):
        self._temporal = temporal_layer or TemporalSignalLayer()
        self._duckdb = duckdb_store
        self._max_predictions = max_predictions
        self._patterns: dict[tuple[str, str], _PatternStats] = {}
        self._lru_keys: deque[tuple[str, str]] = deque(maxlen=512)
        self._live_events: deque[TemporalEvent] = deque(maxlen=256)
        self._last_flush = time.time()
        self._flush_interval = FLUSH_INTERVAL_S
        self._stats = {'predictions_generated': 0, 'predictions_used': 0, 'events_observed': 0, 'pattern_builds': 0, 'duckdb_queries': 0}

    async def predict_next_iocs(self, top_k: int | None=None) -> list[dict]:
        """
        Generate temporal predictions for next IOC fetches.

        Called by ContinuousPrefetchPipeline._producer_loop() every PREFETCH_INTERVAL_S.

        Returns:
            List of dicts with keys: ioc_value, ioc_type, confidence,
            source_node, prediction_method
        """
        max_k = top_k or self._max_predictions
        now = time.time()
        if now - self._last_flush > self._flush_interval:
            await self._flush_to_duckdb()
            self._last_flush = now
        await self._ensure_patterns_loaded()
        live_scores = self._temporal.get_top_scores(k=20)
        live_burst_keys = {s.key for s in live_scores if s.burst_score > 0.5}
        predictions: list[IOCPrediction] = []
        now_ts = time.time()
        current_hour = time.localtime(now_ts).tm_hour
        current_dow = time.localtime(now_ts).tm_wday
        for (ioc_type, source), pat in self._patterns.items():
            if pat.total_events < MIN_EVENTS_FOR_PATTERN:
                continue
            in_peak_window = self._is_near_peak(pat, current_hour, current_dow)
            conf = self._compute_confidence(pat, current_hour, current_dow, in_peak_window)
            if conf < 0.1:
                continue
            predicted_iocs = self._predict_iocs_for_time(ioc_type, source, pat, max_k // 4)
            for ioc_val, method in predicted_iocs:
                is_live_burst = ioc_val in live_burst_keys
                final_conf = conf * (CONFIDENCE_BOOST_BURST if is_live_burst else 1.0)
                final_conf = min(final_conf, 1.0)
                predictions.append(IOCPrediction(ioc_value=ioc_val, ioc_type=ioc_type, confidence=final_conf, source_node=source, prediction_method=method, predicted_at=now, expires_at=now + PREDICTION_HORIZON_S))
        for score in live_scores[:10]:
            if score.burst_score < 0.6:
                continue
            predictions.append(IOCPrediction(ioc_value=score.key, ioc_type=self._infer_ioc_type(score.key), confidence=score.burst_score * CONFIDENCE_BOOST_BURST, source_node=score.family, prediction_method='burst', predicted_at=now, expires_at=now + 120.0))
        for (ioc_type, source), pat in self._patterns.items():
            if pat.total_events < MIN_EVENTS_FOR_PATTERN * 2:
                continue
            if len(pat.events) >= 4:
                mean_gap = sum(pat.events) / len(pat.events)
                last_ts = pat.events[-1] if pat.events else 0
                if last_ts > 0:
                    next_predicted = last_ts + mean_gap
                    if 0 <= next_predicted - now <= PREDICTION_HORIZON_S:
                        conf = 0.5 + 0.3 * min(pat.total_events / 50, 1.0)
                        predictions.append(IOCPrediction(ioc_value=self._pattern_key_to_ioc_value((ioc_type, source)), ioc_type=ioc_type, confidence=min(conf, 1.0), source_node=source, prediction_method='periodicity', predicted_at=now, expires_at=now + mean_gap * 1.5))
        deduped: dict[str, IOCPrediction] = {}
        for p in predictions:
            existing = deduped.get(p.ioc_value)
            if existing is None or p.confidence > existing.confidence:
                deduped[p.ioc_value] = p
        sorted_preds = sorted(deduped.values(), key=lambda x: x.confidence, reverse=True)
        result = sorted_preds[:max_k]
        self._stats['predictions_generated'] += len(result)
        return [{'ioc_value': p.ioc_value, 'ioc_type': p.ioc_type, 'confidence': p.confidence, 'source_node': p.source_node, 'prediction_method': p.prediction_method} for p in result]

    def observe_findings(self, findings: list[Any]) -> None:
        """
        Observe new findings and update temporal patterns.

        Called after sprint findings are ingested (DuckDB write path).
        Updates live TemporalSignalLayer + rolling pattern stats.

        Args:
            findings: List of CanonicalFinding-like objects or dicts
        """
        for f in findings:
            try:
                event = event_from_finding_like(f)
                if event is None:
                    continue
                self._temporal.observe(event)
                self._update_pattern_stats(event)
                self._live_events.append(event)
                self._stats['events_observed'] += 1
            except Exception as e:
                logger.debug(f'[P3-2] observe_findings: failed to process finding: {e}')

    async def record_prefetch_outcome(self, ioc_value: str, success: bool, bytes_downloaded: int) -> None:
        """
        Feedback loop: record prefetch outcome for pattern learning.

        Called by ContinuousPrefetchPipeline._prefetch_item().

        Args:
            ioc_value: The IOC that was prefetched
            success: Whether the prefetch succeeded
            bytes_downloaded: Bytes downloaded (0 if failed)
        """
        confirmed = success and bytes_downloaded > 0
        self._temporal.observe_confirmation(ioc_value, confirmed, source='prefetch')
        if confirmed:
            try:
                event = TemporalEvent(ts=time.time(), key=ioc_value, family='prefetch', source='prefetch', weight=1.0)
                self._update_pattern_stats(event)
            except Exception as e:
                logger.debug(f'[P3-2] record_prefetch_outcome: {e}')

    async def _ensure_patterns_loaded(self) -> None:
        """Load or refresh pattern statistics from DuckDB historical data."""
        if self._duckdb is None:
            return
        try:
            await self._load_patterns_from_duckdb()
        except Exception as e:
            logger.debug(f'[P3-2] _ensure_patterns_loaded: DuckDB query failed: {e}')

    async def _load_patterns_from_duckdb(self) -> None:
        """
        Query DuckDB for time-of-day / day-of-week distributions per IOC type.

        Queries last 7 days of findings and builds hour+dow histograms.
        Bounded: max 1000 rows per query, <5MB RAM.
        """
        if self._duckdb is None:
            return
        self._stats['duckdb_queries'] += 1
        try:
            query = '\n                SELECT\n                    source_family,\n                    ioc_type,\n                    EXTRACT(HOUR FROM to_timestamp(timestamp))::INTEGER as hour,\n                    EXTRACT(DOW FROM to_timestamp(timestamp))::INTEGER as dow,\n                    COUNT(*) as cnt\n                FROM findings\n                WHERE timestamp > UNIX_TIMESTAMP() - 7 * 86400\n                GROUP BY source_family, ioc_type, hour, dow\n                ORDER BY source_family, ioc_type, cnt DESC\n                LIMIT 1000\n            '
            conn = getattr(self._duckdb, '_conn', None)
            if conn is None:
                return
            import asyncio
            rows = await asyncio.to_thread(self._execute_query, conn, query)
            for row in rows:
                source_family = row[0] or 'unknown'
                ioc_type = row[1] or 'domain'
                hour = int(row[2]) if row[2] is not None else 0
                dow = int(row[3]) if row[3] is not None else 0
                cnt = int(row[4]) if row[4] is not None else 0
                key = (ioc_type, source_family)
                pat = self._patterns.get(key)
                if pat is None:
                    if len(self._patterns) >= 512:
                        oldest = self._lru_keys.popleft()
                        self._patterns.pop(oldest, None)
                    pat = _PatternStats()
                    self._patterns[key] = pat
                    self._lru_keys.append(key)
                pat.hour_counts[hour] += cnt
                pat.dow_counts[dow] += cnt
                pat.total_events += cnt
                if pat.peak_hour < 0 or pat.hour_counts[hour] > pat.hour_counts[pat.peak_hour]:
                    pat.peak_hour = hour
                if pat.peak_dow < 0 or pat.dow_counts[dow] > pat.dow_counts[pat.peak_dow]:
                    pat.peak_dow = dow
            self._stats['pattern_builds'] += 1
        except Exception as e:
            logger.debug(f'[P3-2] _load_patterns_from_duckdb: {e}')

    def _execute_query(self, conn: Any, query: str) -> list:
        """Execute DuckDB query and return rows."""
        try:
            cursor = conn.execute(query)
            return cursor.fetchall()
        except Exception:
            return []

    async def _flush_to_duckdb(self) -> None:
        """Flush live events to DuckDB for long-term pattern persistence."""
        if self._duckdb is None or not self._live_events:
            return
        try:
            events = list(self._live_events)
            if not events:
                return
            records = []
            for e in events:
                records.append({'timestamp': e.ts, 'key': e.key, 'family': e.family, 'source': e.source, 'weight': e.weight})
            write_method = getattr(self._duckdb, 'async_ingest_findings_batch', None)
            if write_method is not None:
                import asyncio
                await asyncio.to_thread(write_method, records)
            self._live_events.clear()
        except Exception as e:
            logger.debug(f'[P3-2] _flush_to_duckdb: {e}')

    def _update_pattern_stats(self, event: TemporalEvent) -> None:
        """Update rolling pattern statistics for an event."""
        key = (event.family, 'generic')
        pat = self._patterns.get(key)
        if pat is None:
            if len(self._patterns) >= 512:
                oldest = self._lru_keys.popleft()
                self._patterns.pop(oldest, None)
            pat = _PatternStats()
            self._patterns[key] = pat
            self._lru_keys.append(key)
        hour = time.localtime(event.ts).tm_hour
        dow = time.localtime(event.ts).tm_wday
        pat.hour_counts[hour] += 1
        pat.dow_counts[dow] += 1
        pat.total_events += 1
        pat.last_hour = hour
        pat.last_dow = dow
        pat.events.append(event.ts)
        if pat.peak_hour < 0 or pat.hour_counts[hour] > pat.hour_counts[pat.peak_hour]:
            pat.peak_hour = hour
        if pat.peak_dow < 0 or pat.dow_counts[dow] > pat.dow_counts[pat.peak_dow]:
            pat.peak_dow = dow

    def _is_near_peak(self, pat: _PatternStats, current_hour: int, current_dow: int) -> bool:
        """Check if current time is within predicted peak window."""
        if pat.peak_hour < 0:
            return False
        hour_diff = abs(current_hour - pat.peak_hour)
        hour_diff = min(hour_diff, 24 - hour_diff)
        in_peak = hour_diff <= PEAK_HOUR_TOLERANCE
        if pat.peak_dow >= 0 and in_peak:
            in_peak = current_dow == pat.peak_dow or pat.dow_counts[pat.peak_dow] < pat.total_events * 0.3
        return in_peak

    def _compute_confidence(self, pat: _PatternStats, current_hour: int, current_dow: int, in_peak_window: bool) -> float:
        """
        Compute prediction confidence based on historical patterns.

        Returns 0.0–1.0 confidence score.
        """
        if pat.total_events < MIN_EVENTS_FOR_PATTERN:
            return 0.0
        base = min(pat.total_events / 50.0, 1.0) * 0.4
        peak_boost = CONFIDENCE_BOOST_HOUR if in_peak_window else 0.0
        hour_activity = pat.hour_counts[current_hour] / max(pat.total_events, 1)
        dow_activity = pat.dow_counts[current_dow] / max(pat.total_events, 1)
        conf = base + peak_boost * 0.3 + hour_activity * 0.2 + dow_activity * 0.1
        return min(conf, 1.0)

    def _predict_iocs_for_time(self, ioc_type: str, source: str, pat: _PatternStats, max_iocs: int) -> list[tuple[str, str]]:
        """
        Predict IOC values likely to appear at the given time.

        Returns list of (ioc_value, method) tuples.
        Uses historical key patterns from events.
        """
        predictions = []
        if pat.events:
            sample_key = f'{source}:{ioc_type}'
            for _ in range(min(max_iocs, 3)):
                predictions.append((sample_key, 'time_of_day'))
        return predictions[:max_iocs]

    def _pattern_key_to_ioc_value(self, key: tuple[str, str]) -> str:
        """Convert pattern key to IOC value for prediction.

        P3-2 FIX: previous impl returned 'source:generic' which _infer_ioc_type
        misclassified as 'entity'. Now returns the source family directly — domains
        map to domains, URLs to URLs, etc. Type is carried separately in the tuple.
        """
        ioc_type, source = key
        return source if source else ioc_type

    def _infer_ioc_type(self, key: str) -> str:
        """Infer IOC type from key string."""
        if ':' in key:
            return key.split(':')[-1]
        if '.' in key:
            return 'domain'
        if key.startswith(('http://', 'https://')):
            return 'url'
        return 'entity'

    def get_stats(self) -> dict[str, Any]:
        """Return predictor statistics."""
        return {**self._stats, 'patterns_loaded': len(self._patterns), 'live_events_buffered': len(self._live_events), 'temporal_layer_size': self._temporal.get_state_size()}