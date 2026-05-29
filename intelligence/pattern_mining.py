"""
Pattern Mining Engine
=====================

Advanced pattern detection and analysis system for:
- Behavioral pattern detection (user behavior analysis)
- Transaction flow analysis (financial patterns)
- Temporal pattern mining (seasonality, cycles, periodicity)
- Communication pattern extraction (who talks to whom, when)
- Structural pattern recognition (organizational hierarchies)
- Sequential pattern mining (order of events)
- Anomaly detection within patterns

STATUS: DORMANT
  - Zero production call sites (grep audit: legacy autonomous_orchestrator.py only)
  - Re-exported via intelligence/__init__.py (lazy try/except)
  - NOT on canonical sprint/autonomous_orchestrator.py hot path
  - No call sites in prefetch_oracle.py or knowledge/ cluster
  - Retention: pattern-matching algorithms may be useful later

M1 8GB CEILING (ADVISORY):
  - max_memory_mb=512 recommended for M1 8GB UMA
  - _top_patterns bounded to MAX_TOP_PATTERNS=200 entries
  - SlidingWindowCounter has max_unique=10000 hard limit
  - FFT binned to 256 max bins
  - MLX FFT: limited to 16+ element series before using it
  - optimize_memory() clears caches on demand

PROMOTION GATE: requires production call site evidence before activating.
"""

from __future__ import annotations

import heapq
import logging
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# Optional MLX import for M1 acceleration
try:
    import mlx.core as mx
    MLX_AVAILABLE = True
except ImportError:
    MLX_AVAILABLE = False
    mx = None

# Sprint 67: Mamba2 forecasting state
_MAMBA_AVAILABLE = False
_MAMBA_MODEL = None
_MAMBA_TOKENIZER = None
_MAMBA_FAILURES = 0
_MAMBA_DISABLED_UNTIL = 0.0


def _get_pywt():
    """Lazy import pywt."""
    try:
        import pywt
        return pywt
    except ImportError:
        return None


async def _get_mamba_model():
    """Get or load Mamba2 model (lazy)."""
    global _MAMBA_AVAILABLE, _MAMBA_MODEL, _MAMBA_TOKENIZER

    if _MAMBA_AVAILABLE and _MAMBA_MODEL is not None:
        return _MAMBA_MODEL, _MAMBA_TOKENIZER

    try:
        from hledac.universal.utils.mlx_cache import get_mlx_model
        model, tokenizer = await get_mlx_model("mlx-community/mamba2-370m-4bit")
        if model is not None:
            _MAMBA_MODEL = model
            _MAMBA_TOKENIZER = tokenizer
            _MAMBA_AVAILABLE = True
            logger.info("Mamba2 model loaded successfully")
        return model, tokenizer
    except Exception as e:
        logger.debug(f"Mamba2 model not available: {e}")
        return None, None


async def forecast_mamba2(series: list[float], horizon: int = 5) -> list[float] | None:
    """
    Forecast using Mamba2 model with best-effort timeout and circuit breaker.

    Args:
        series: Time series data
        horizon: Number of steps to forecast

    Returns:
        List of forecasted values or None on failure
    """
    import asyncio
    import re
    import time

    global _MAMBA_FAILURES, _MAMBA_DISABLED_UNTIL

    # Circuit breaker check
    if time.time() < _MAMBA_DISABLED_UNTIL:
        return None

    if not _MAMBA_AVAILABLE:
        model, _ = await _get_mamba_model()
        if model is None:
            return None

    model, tokenizer = await _get_mamba_model()
    if model is None or tokenizer is None:
        return None

    # Prepare prompt
    series_str = " ".join([f"{x:.2f}" for x in series[-50:]])
    prompt = f"""You are a time series forecaster. Given past values, predict the next {horizon} values as numbers only, separated by spaces.

Example:
Past: 1.0 2.0 3.0 4.0
Next: 5.0 6.0 7.0

Now:
Past: {series_str}
Next:"""

    try:
        from mlx_lm import generate
        loop = asyncio.get_running_loop()

        from hledac.universal.utils.mlx_cache import get_mlx_semaphore
        async with get_mlx_semaphore():
            try:
                output = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        lambda: generate(model, tokenizer, prompt, max_tokens=horizon * 5, temp=0.0)
                    ),
                    timeout=0.5
                )
            except TypeError:
                # Fallback if temp not supported
                output = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        lambda: generate(model, tokenizer, prompt, max_tokens=horizon * 5)
                    ),
                    timeout=0.5
                )

        # Parse numbers with correct regex
        numbers = re.findall(r"[-+]?\d*\.?\d+", output)
        if len(numbers) >= horizon:
            _MAMBA_FAILURES = 0  # Reset on success
            return [float(n) for n in numbers[:horizon]]

    except TimeoutError:
        _MAMBA_FAILURES += 1
        if _MAMBA_FAILURES >= 3:
            _MAMBA_DISABLED_UNTIL = time.time() + 60
            logger.warning("Mamba2 circuit breaker triggered (3 timeouts)")
        return None
    except Exception as e:
        _MAMBA_FAILURES += 1
        if _MAMBA_FAILURES >= 3:
            _MAMBA_DISABLED_UNTIL = time.time() + 60
        logger.debug(f"Mamba2 forecast failed: {e}")
        return None

    return None


def _ewma_drift(series: list[float], alpha: float = 0.3, threshold: float = 0.5) -> bool:
    """EWMA-based drift detection."""
    if len(series) < 10:
        return False

    ewma = series[0]
    for x in series[1:]:
        ewma = alpha * x + (1 - alpha) * ewma

    std = max(series) - min(series)
    return abs(series[-1] - ewma) > threshold * (std + 1e-6)


def _cusum_change(series: list[float], threshold: float = 2.0) -> bool:
    """CUSUM change detection."""
    if len(series) < 10:
        return False

    mean = sum(series) / len(series)
    std = max(series) - min(series) + 1e-6
    cusum = 0.0

    for x in series:
        cusum += (x - mean)
        if abs(cusum) > threshold * std:
            return True

    return False


async def detect_change_points_wavelet(series: list[float]) -> list[int]:
    """
    Detect change points using wavelet decomposition.

    Args:
        series: Time series data

    Returns:
        List of change point indices
    """
    import gc

    pywt = _get_pywt()
    if pywt is None or len(series) < 10:
        return []

    # Limit series length
    if len(series) > 1024:
        series = series[-1024:]

    data = np.array(series, dtype=np.float32)

    try:
        coeffs = pywt.wavedec(data, 'db4', level=3)
        changes = []

        for _i, c in enumerate(coeffs[1:]):  # Skip approximation coefficients
            threshold = np.std(c) * 3
            if threshold == 0:
                continue
            peaks = np.where(np.abs(c) > threshold)[0]
            step = max(1, len(data) // (len(c) * 2))

            for p in peaks:
                idx = p * step
                if idx < len(series):
                    changes.append(idx)

        gc.collect()
        return sorted(set(changes))[:10]

    except Exception as e:
        logger.debug(f"Wavelet change point detection failed: {e}")
        return []


# =============================================================================
# ENUMS
# =============================================================================

class PatternType(Enum):
    """Types of patterns that can be detected."""
    TEMPORAL = "temporal"
    BEHAVIORAL = "behavioral"
    COMMUNICATION = "communication"
    TRANSACTION = "transaction"
    STRUCTURAL = "structural"
    SEQUENTIAL = "sequential"
    ANOMALY = "anomaly"


class SeasonalityType(Enum):
    """Types of seasonality patterns."""
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"
    NONE = "none"


class TrendDirection(Enum):
    """Direction of trend in temporal patterns."""
    INCREASING = "increasing"
    DECREASING = "decreasing"
    STABLE = "stable"
    VOLATILE = "volatile"


class AnomalyType(Enum):
    """Types of anomalies that can be detected."""
    POINT = "point"  # Single anomalous data point
    CONTEXTUAL = "contextual"  # Anomalous in context
    COLLECTIVE = "collective"  # Group of related anomalies
    SEASONAL = "seasonal"  # Anomalous for specific season


# =============================================================================
# DATACLASSES - Input Data
# =============================================================================

@dataclass
class Event:
    """Generic event for pattern mining."""
    timestamp: datetime
    entity_id: str
    event_type: str
    value: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Action:
    """User action for behavioral pattern mining."""
    timestamp: datetime
    user_id: str
    action_type: str
    target: str | None = None
    duration_ms: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Communication:
    """Communication event for pattern mining."""
    timestamp: datetime
    sender: str
    recipient: str
    channel: str  # email, sms, call, etc.
    size_bytes: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Transaction:
    """Financial transaction for flow analysis."""
    timestamp: datetime
    sender: str
    recipient: str
    amount: float
    currency: str = "USD"
    transaction_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


# =============================================================================
# DATACLASSES - Pattern Results
# =============================================================================

@dataclass
class Pattern:
    """Base pattern class."""
    pattern_type: PatternType
    description: str
    confidence: float  # 0-1
    support: float  # 0-1, how often it occurs
    entities: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TemporalPattern(Pattern):
    """Temporal pattern with time-based characteristics."""
    period: timedelta | None = None
    seasonality: SeasonalityType | None = None
    burst_times: list[datetime] = field(default_factory=list)
    trend: TrendDirection = TrendDirection.STABLE
    start_time: datetime | None = None
    end_time: datetime | None = None

    def __post_init__(self):
        if self.pattern_type is None:
            self.pattern_type = PatternType.TEMPORAL


@dataclass
class BehavioralPattern(Pattern):
    """Behavioral pattern from user actions."""
    user_id: str | None = None
    action_sequence: list[str] = field(default_factory=list)
    frequency_per_day: float = 0.0
    preferred_times: list[int] = field(default_factory=list)  # Hours of day (0-23)
    pattern_duration_ms: int | None = None  # Typical duration

    def __post_init__(self):
        if self.pattern_type is None:
            self.pattern_type = PatternType.BEHAVIORAL


@dataclass
class CommunicationPattern(Pattern):
    """Communication pattern between entities."""
    response_time_avg: timedelta | None = None
    response_time_std: timedelta | None = None
    frequency: float = 0.0  # Messages per day
    network_centrality: float = 0.0  # 0-1, how central in network
    cluster_id: str | None = None

    def __post_init__(self):
        if self.pattern_type is None:
            self.pattern_type = PatternType.COMMUNICATION


@dataclass
class FlowPattern(Pattern):
    """Transaction or data flow pattern."""
    source_clusters: list[str] = field(default_factory=list)
    destination_clusters: list[str] = field(default_factory=list)
    flow_volume: dict[tuple[str, str], float] = field(default_factory=dict)
    intermediaries: list[str] = field(default_factory=list)
    cycle_detected: bool = False
    concentration_index: float = 0.0  # Gini coefficient for flow distribution

    def __post_init__(self):
        if self.pattern_type is None:
            self.pattern_type = PatternType.TRANSACTION


@dataclass
class StructuralPattern(Pattern):
    """Structural/organizational pattern."""
    hierarchy_levels: int = 0
    hierarchy_edges: list[tuple[str, str]] = field(default_factory=list)
    cluster_sizes: dict[str, int] = field(default_factory=dict)
    centralization: float = 0.0  # 0-1
    density: float = 0.0  # Network density

    def __post_init__(self):
        if self.pattern_type is None:
            self.pattern_type = PatternType.STRUCTURAL


@dataclass
class SequentialPattern(Pattern):
    """Sequential pattern from ordered events."""
    sequence: list[str] = field(default_factory=list)
    sequence_length: int = 0
    occurrence_count: int = 0
    is_cyclic: bool = False

    def __post_init__(self):
        if self.pattern_type is None:
            self.pattern_type = PatternType.SEQUENTIAL
        self.sequence_length = len(self.sequence)


@dataclass
class Anomaly:
    """Detected anomaly in data."""
    anomaly_type: AnomalyType
    timestamp: datetime
    entity_id: str
    description: str
    severity: float  # 0-1
    expected_value: float | None = None
    actual_value: float | None = None
    related_pattern: str | None = None


@dataclass
class CorrelationMatrix:
    """Cross-pattern correlation results."""
    pattern_ids: list[str] = field(default_factory=list)
    correlation_matrix: np.ndarray = field(default_factory=lambda: np.array([]))
    p_values: np.ndarray = field(default_factory=lambda: np.array([]))
    significant_pairs: list[tuple[str, str, float]] = field(default_factory=list)


# =============================================================================
# SLIDING WINDOW IMPLEMENTATIONS (M1 Optimized)
# =============================================================================

class SlidingWindowCounter:
    """Memory-efficient sliding window frequency counter."""

    def __init__(self, window_size: int, max_unique: int = 10000):
        self.window_size = window_size
        self.max_unique = max_unique
        # Use deque for O(1) popleft instead of O(n) list shifting
        self.window: deque = deque()
        self.counter: Counter = Counter()

    def add(self, item: Any, timestamp: datetime) -> None:
        """Add item to window."""
        self.window.append((item, timestamp))
        self.counter[item] += 1

        # Remove old items - O(1) with deque.popleft
        cutoff = timestamp - timedelta(seconds=self.window_size)
        while self.window and self.window[0][1] < cutoff:
            old_item, _ = self.window.popleft()
            self.counter[old_item] -= 1
            if self.counter[old_item] <= 0:
                del self.counter[old_item]

        # Limit memory for high cardinality
        if len(self.counter) > self.max_unique:
            # Remove least frequent items
            least_common = self.counter.most_common()[:-self.max_unique//10]
            for item, _ in least_common:
                del self.counter[item]

    def get_frequency(self, item: Any) -> int:
        """Get frequency of item in current window."""
        return self.counter.get(item, 0)

    def get_top_k(self, k: int = 10) -> list[tuple[Any, int]]:
        """Get top k most frequent items using heapq for O(n log k) performance (Sprint 26)."""
        if not self.counter:
            return []
        return heapq.nlargest(k, self.counter.items(), key=lambda x: x[1])


class StreamingStatistics:
    """Streaming mean and variance calculation (Welford's algorithm)."""

    def __init__(self):
        self.n = 0
        self.mean = 0.0
        self.m2 = 0.0  # Sum of squares of differences

    def update(self, x: float) -> None:
        """Update statistics with new value."""
        self.n += 1
        delta = x - self.mean
        self.mean += delta / self.n
        delta2 = x - self.mean
        self.m2 += delta * delta2

    def get_mean(self) -> float:
        return self.mean

    def get_variance(self) -> float:
        return self.m2 / self.n if self.n > 0 else 0.0

    def get_std(self) -> float:
        return np.sqrt(self.get_variance())


# =============================================================================
# MAIN ENGINE
# =============================================================================

class PatternMiningEngine:
    """
    Advanced pattern mining engine with M1 8GB optimization.

    Capabilities:
    - Behavioral pattern detection
    - Transaction flow analysis
    - Temporal pattern mining
    - Communication pattern extraction
    - Structural pattern recognition
    - Sequential pattern mining
    - Anomaly detection

    M1 Optimizations:
    - Streaming algorithms for large datasets
    - Efficient sliding windows
    - Memory-efficient frequency counting
    - MLX-accelerated correlation and FFT
    """

    def __init__(
        self,
        max_memory_mb: float = 512.0,
        use_mlx: bool = True,
        min_support: float = 0.1,
        min_confidence: float = 0.5
    ):
        """
        Initialize pattern mining engine.

        Args:
            max_memory_mb: ADVISORY ceiling in MB for M1 8GB UMA (512 recommended).
                           Not hard-enforced — rely on specific bounded structures.
            use_mlx: Whether to use MLX acceleration on M1
            min_support: Minimum support threshold for patterns (0-1)
            min_confidence: Minimum confidence threshold for patterns (0-1)
        """
        self.max_memory_mb = max_memory_mb
        self.use_mlx = use_mlx and MLX_AVAILABLE
        self.min_support = min_support
        self.min_confidence = min_confidence

        # Streaming statistics for each entity type
        self._streaming_stats: dict[str, StreamingStatistics] = defaultdict(StreamingStatistics)

        # Heavy hitters: top-K patterns (bounded to 200)
        self._top_patterns: dict[str, int] = {}

        logger.info(f"PatternMiningEngine initialized (MLX: {self.use_mlx})")

    async def detect_change_points(self, series: list[float]) -> list[int]:
        """
        Detect change points in time series using wavelet + Mamba2 (with fallbacks).

        Uses:
        1. Wavelet decomposition for change detection
        2. Mamba2 forecasting for anomaly detection (best-effort)
        3. EWMA/CUSUM fallbacks if MLX unavailable

        Args:
            series: Time series data

        Returns:
            List of change point indices
        """
        import gc

        changes = await detect_change_points_wavelet(series)

        # Try Mamba2 if available
        await _get_mamba_model()
        if _MAMBA_AVAILABLE:
            forecast = await forecast_mamba2(series)
            if forecast and len(forecast) > 0:
                last = series[-1] if series else 0
                std = (max(series) - min(series)) / 2 if len(series) > 1 else 1.0
                if abs(forecast[0] - last) > 0.5 * std:
                    changes.append(len(series) - 1)
        else:
            # Fallback to EWMA/CUSUM
            if len(series) > 20 and (_ewma_drift(series) or _cusum_change(series)):
                changes.append(len(series) - 1)

        gc.collect()
        return sorted(set(changes))[:10]

    def _ingest_pattern(self, pattern_id: str) -> None:
        """
        Ingest a pattern for heavy hitters tracking.

        Args:
            pattern_id: Unique identifier for the pattern
        """
        MAX_TOP_PATTERNS = 200

        # Update count
        if pattern_id in self._top_patterns:
            self._top_patterns[pattern_id] += 1
        else:
            self._top_patterns[pattern_id] = 1

        # Evict if over limit - keep top K by count
        if len(self._top_patterns) > MAX_TOP_PATTERNS:
            # Sort by count and keep top K
            sorted_patterns = sorted(
                self._top_patterns.items(),
                key=lambda x: x[1],
                reverse=True
            )
            self._top_patterns = dict(sorted_patterns[:MAX_TOP_PATTERNS])

    # ========================================================================
    # TEMPORAL PATTERN MINING
    # ========================================================================

    def mine_temporal_patterns(
        self,
        events: list[Event],
        min_events: int = 10
    ) -> list[TemporalPattern]:
        """
        Mine temporal patterns from events.

        Args:
            events: List of events with timestamps
            min_events: Minimum number of events required

        Returns:
            List of detected temporal patterns
        """
        if len(events) < min_events:
            logger.warning(f"Insufficient events for temporal mining: {len(events)} < {min_events}")
            return []

        patterns = []

        # Sort events by timestamp
        sorted_events = sorted(events, key=lambda e: e.timestamp)
        timestamps = [e.timestamp for e in sorted_events]
        values = [e.value for e in sorted_events if e.value is not None]

        # Detect periodicity
        period_patterns = self._detect_periodicity(timestamps, values)
        patterns.extend(period_patterns)

        # Detect bursts
        burst_pattern = self._detect_bursts(sorted_events)
        if burst_pattern:
            patterns.append(burst_pattern)

        # Detect trends
        trend_pattern = self._detect_trend(sorted_events)
        if trend_pattern:
            patterns.append(trend_pattern)

        # Detect seasonality
        seasonality_pattern = self._detect_seasonality(timestamps)
        if seasonality_pattern:
            patterns.append(seasonality_pattern)

        return patterns

    def _detect_periodicity(
        self,
        timestamps: list[datetime],
        values: list[float] | None = None
    ) -> list[TemporalPattern]:
        """Detect periodic patterns using FFT."""
        patterns = []

        if len(timestamps) < 10:
            return patterns

        # Convert timestamps to seconds since first event
        base_time = timestamps[0]
        time_diffs = [(t - base_time).total_seconds() for t in timestamps]

        if self.use_mlx and len(time_diffs) >= 16:
            # Use MLX FFT for periodicity detection
            patterns = self._detect_periodicity_mlx(time_diffs, timestamps)
        else:
            # Use numpy FFT (Fix 2: replaced autocorrelation with FFT)
            patterns = self._compute_fft_periodicity(time_diffs, timestamps)

        return patterns

    def _detect_periodicity_mlx(
        self,
        time_diffs: list[float],
        timestamps: list[datetime]
    ) -> list[TemporalPattern]:
        """Detect periodicity using MLX FFT (M1 optimized)."""
        patterns = []

        try:
            # Create uniform time series
            max_time = max(time_diffs)
            n_bins = min(len(time_diffs), 256)  # Limit for memory
            bin_size = max_time / n_bins

            # Bin events
            binned = np.zeros(n_bins)
            for t in time_diffs:
                bin_idx = min(int(t / bin_size), n_bins - 1)
                binned[bin_idx] += 1

            # MLX FFT
            mx_array = mx.array(binned)
            fft_result = mx.fft.fft(mx_array)
            power_spectrum = mx.abs(fft_result) ** 2
            power_np = np.array(power_spectrum)

            # Find peaks in power spectrum (excluding DC component)
            freqs = np.fft.fftfreq(n_bins, d=bin_size)
            positive_freqs = freqs[:n_bins//2]
            positive_power = power_np[:n_bins//2]

            # Find peaks
            peaks = []
            for i in range(1, len(positive_power) - 1):
                if positive_power[i] > positive_power[i-1] and positive_power[i] > positive_power[i+1]:
                    if positive_power[i] > np.mean(positive_power) * 2:  # Significant peak
                        period = 1 / positive_freqs[i] if positive_freqs[i] > 0 else None
                        if period and period > bin_size * 2:  # At least 2 bins
                            peaks.append((period, positive_power[i]))

            # Create patterns for top peaks
            peaks.sort(key=lambda x: x[1], reverse=True)
            for period, power in peaks[:3]:
                period_td = timedelta(seconds=period)
                confidence = min(0.95, power / (np.max(positive_power) + 1e-10))

                patterns.append(TemporalPattern(
                    pattern_type=PatternType.TEMPORAL,
                    description=f"Periodic pattern with period {period_td}",
                    confidence=confidence,
                    support=len(timestamps) / (max(time_diffs) / period) if period > 0 else 0,
                    entities=[],
                    evidence=[f"FFT peak at frequency {1/period:.4f} Hz"],
                    period=period_td,
                    trend=TrendDirection.STABLE,
                    start_time=timestamps[0],
                    end_time=timestamps[-1]
                ))

        except Exception as e:
            logger.warning(f"MLX FFT failed, falling back: {e}")
            return self._detect_periodicity_autocorr(time_diffs, timestamps)

        return patterns

    def _compute_fft_periodicity(
        self,
        time_diffs: list[float],
        timestamps: list[datetime]
    ) -> list[TemporalPattern]:
        """Detect periodicity using FFT (O(n log n) instead of O(n²) autocorrelation)."""
        patterns = []

        try:
            # Create uniform time series
            max_time = max(time_diffs)
            if max_time <= 0:
                return patterns

            n_bins = min(len(time_diffs), 256)  # Limit for memory
            bin_size = max_time / n_bins

            if bin_size <= 0:
                return patterns

            # Bin events
            binned = np.zeros(n_bins)
            for t in time_diffs:
                bin_idx = min(int(t / bin_size), n_bins - 1)
                binned[bin_idx] += 1

            # Compute FFT (use MLX if available, else NumPy)
            if MLX_AVAILABLE:
                mx_array = mx.array(binned)
                fft_result = mx.fft.fft(mx_array)
                power_spectrum = mx.abs(fft_result) ** 2
                power_np = np.array(power_spectrum)
            else:
                fft_result = np.fft.fft(binned)
                power_spectrum = np.abs(fft_result) ** 2
                power_np = power_spectrum

            # Find peaks in power spectrum (excluding DC component)
            freqs = np.fft.fftfreq(n_bins, d=bin_size)
            positive_freqs = freqs[:n_bins//2]
            positive_power = power_np[:n_bins//2]

            # Find peaks
            peaks = []
            for i in range(1, len(positive_power) - 1):
                if positive_power[i] > positive_power[i-1] and positive_power[i] > positive_power[i+1]:
                    if positive_power[i] > np.mean(positive_power) * 2:  # Significant peak
                        period = 1 / positive_freqs[i] if positive_freqs[i] > 0 else None
                        if period and period > bin_size * 2:  # At least 2 bins
                            peaks.append((period, positive_power[i]))

            # Create patterns for top peaks
            peaks.sort(key=lambda x: x[1], reverse=True)
            for period, power in peaks[:3]:
                period_td = timedelta(seconds=period)
                confidence = min(0.95, power / (np.max(positive_power) + 1e-10))

                patterns.append(TemporalPattern(
                    pattern_type=PatternType.TEMPORAL,
                    description=f"Periodic pattern with period {period_td}",
                    confidence=confidence,
                    support=len(timestamps) / (max(time_diffs) / period) if period > 0 else 0,
                    entities=[],
                    evidence=[f"FFT peak at frequency {1/period:.4f} Hz"],
                    period=period_td,
                    trend=TrendDirection.STABLE,
                    start_time=timestamps[0],
                    end_time=timestamps[-1]
                ))

        except Exception as e:
            logger.warning(f"FFT periodicity detection failed: {e}")

        return patterns

    def _detect_periodicity_autocorr(
        self,
        time_diffs: list[float],
        timestamps: list[datetime]
    ) -> list[TemporalPattern]:
        """Detect periodicity using autocorrelation."""
        patterns = []

        # Create uniform time series
        max_time = max(time_diffs)
        if max_time <= 0:
            return patterns

        n_bins = min(len(time_diffs), 128)
        bin_size = max_time / n_bins

        if bin_size <= 0:
            return patterns

        binned = np.zeros(n_bins)
        for t in time_diffs:
            bin_idx = min(int(t / bin_size), n_bins - 1)
            binned[bin_idx] += 1

        # Autocorrelation
        if len(binned) < 4:
            return patterns

        autocorr = np.correlate(binned - np.mean(binned), binned - np.mean(binned), mode='full')
        autocorr = autocorr[len(autocorr)//2:]
        autocorr = autocorr / (autocorr[0] + 1e-10)  # Normalize

        # Find peaks
        for i in range(2, min(len(autocorr) - 1, n_bins // 2)):
            if autocorr[i] > autocorr[i-1] and autocorr[i] > autocorr[i+1]:
                if autocorr[i] > 0.3:  # Significant correlation
                    period = i * bin_size
                    period_td = timedelta(seconds=period)

                    patterns.append(TemporalPattern(
                        pattern_type=PatternType.TEMPORAL,
                        description=f"Periodic pattern with period ~{period_td}",
                        confidence=min(0.9, autocorr[i]),
                        support=0.5,
                        entities=[],
                        evidence=[f"Autocorrelation peak at lag {i}"],
                        period=period_td,
                        trend=TrendDirection.STABLE,
                        start_time=timestamps[0],
                        end_time=timestamps[-1]
                    ))
                    break  # Only strongest pattern

        return patterns

    def _detect_bursts(self, events: list[Event]) -> TemporalPattern | None:
        """Detect burst patterns in event timing."""
        if len(events) < 10:
            return None

        # Calculate inter-event times
        inter_times = []
        for i in range(1, len(events)):
            delta = (events[i].timestamp - events[i-1].timestamp).total_seconds()
            inter_times.append(delta)

        if not inter_times:
            return None

        # Detect bursts using threshold
        mean_time = np.mean(inter_times)
        std_time = np.std(inter_times)
        threshold = max(mean_time - 2 * std_time, mean_time * 0.1)

        bursts = []
        burst_start = None

        for i, t in enumerate(inter_times):
            if t < threshold:
                if burst_start is None:
                    burst_start = events[i].timestamp
            else:
                if burst_start is not None:
                    bursts.append(burst_start)
                    burst_start = None

        if burst_start is not None:
            bursts.append(burst_start)

        if len(bursts) >= 2:
            return TemporalPattern(
                pattern_type=PatternType.TEMPORAL,
                description=f"Detected {len(bursts)} burst periods",
                confidence=min(0.9, len(bursts) / 10),
                support=len(bursts) / len(events),
                entities=list({e.entity_id for e in events}),
                evidence=[f"Burst threshold: {threshold:.2f}s"],
                burst_times=bursts,
                trend=TrendDirection.VOLATILE,
                start_time=events[0].timestamp,
                end_time=events[-1].timestamp
            )

        return None

    def _detect_trend(self, events: list[Event]) -> TemporalPattern | None:
        """Detect trend in event values or frequency."""
        if len(events) < 5:
            return None

        # Use event values if available, otherwise use cumulative count
        values = [e.value for e in events if e.value is not None]

        if len(values) >= 5:
            y = np.array(values)
        else:
            # Use event frequency trend
            y = np.arange(1, len(events) + 1)

        x = np.arange(len(y))

        # Linear regression
        n = len(x)
        slope = (n * np.sum(x * y) - np.sum(x) * np.sum(y)) / (n * np.sum(x**2) - np.sum(x)**2 + 1e-10)

        # Determine trend direction
        if abs(slope) < 0.001:
            direction = TrendDirection.STABLE
        elif slope > 0:
            direction = TrendDirection.INCREASING
        else:
            direction = TrendDirection.DECREASING

        # Check volatility
        if len(y) > 3 and np.std(y) > abs(slope * len(y)):
            direction = TrendDirection.VOLATILE

        # Calculate R-squared
        y_mean = np.mean(y)
        ss_tot = np.sum((y - y_mean)**2)
        y_pred = slope * x + (np.mean(y) - slope * np.mean(x))
        ss_res = np.sum((y - y_pred)**2)
        r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0

        if r_squared > 0.3:  # Significant trend
            return TemporalPattern(
                pattern_type=PatternType.TEMPORAL,
                description=f"Trend: {direction.value} (slope={slope:.4f})",
                confidence=min(0.95, r_squared),
                support=0.7,
                entities=list({e.entity_id for e in events}),
                evidence=[f"R² = {r_squared:.3f}"],
                trend=direction,
                start_time=events[0].timestamp,
                end_time=events[-1].timestamp
            )

        return None

    def _detect_seasonality(
        self,
        timestamps: list[datetime]
    ) -> TemporalPattern | None:
        """Detect daily/weekly seasonality patterns."""
        if len(timestamps) < 24:
            return None

        # Hour of day distribution
        hours = [t.hour for t in timestamps]
        hour_counts = Counter(hours)

        # Check for daily pattern (concentration in specific hours)
        total = len(hours)
        max_hour_count = max(hour_counts.values())
        concentration = max_hour_count / total

        if concentration > 0.3:  # Events concentrated in specific hours
            peak_hours = [h for h, c in hour_counts.items() if c > total * 0.15]

            return TemporalPattern(
                pattern_type=PatternType.TEMPORAL,
                description=f"Daily seasonality: peak hours {peak_hours}",
                confidence=min(0.9, concentration),
                support=sum(hour_counts[h] for h in peak_hours) / total,
                entities=[],
                evidence=[f"Peak hours: {peak_hours}"],
                seasonality=SeasonalityType.DAILY,
                trend=TrendDirection.STABLE,
                start_time=timestamps[0],
                end_time=timestamps[-1]
            )

        # Check for weekly pattern
        if len(timestamps) >= 7 * 3:  # At least 3 weeks of data
            weekdays = [t.weekday() for t in timestamps]
            weekday_counts = Counter(weekdays)
            max_weekday_count = max(weekday_counts.values())
            weekday_concentration = max_weekday_count / total

            if weekday_concentration > 0.25:
                peak_days = [d for d, c in weekday_counts.items() if c > total * 0.12]
                day_names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

                return TemporalPattern(
                    pattern_type=PatternType.TEMPORAL,
                    description=f"Weekly seasonality: peak days {[day_names[d] for d in peak_days]}",
                    confidence=min(0.85, weekday_concentration),
                    support=sum(weekday_counts[d] for d in peak_days) / total,
                    entities=[],
                    evidence=[f"Peak days: {[day_names[d] for d in peak_days]}"],
                    seasonality=SeasonalityType.WEEKLY,
                    trend=TrendDirection.STABLE,
                    start_time=timestamps[0],
                    end_time=timestamps[-1]
                )

        return None

    # ========================================================================
    # BEHAVIORAL PATTERN MINING
    # ========================================================================

    def mine_behavioral_patterns(
        self,
        actions: list[Action],
        min_actions: int = 5
    ) -> list[BehavioralPattern]:
        """
        Mine behavioral patterns from user actions.

        Args:
            actions: List of user actions
            min_actions: Minimum actions per user required

        Returns:
            List of detected behavioral patterns
        """
        if len(actions) < min_actions:
            return []

        patterns = []

        # Group actions by user
        user_actions: dict[str, list[Action]] = defaultdict(list)
        for action in actions:
            user_actions[action.user_id].append(action)

        for user_id, user_acts in user_actions.items():
            if len(user_acts) < min_actions:
                continue

            # Sort by timestamp
            user_acts.sort(key=lambda a: a.timestamp)

            # Extract action sequence pattern
            sequence_pattern = self._extract_action_sequence(user_id, user_acts)
            if sequence_pattern:
                patterns.append(sequence_pattern)

            # Extract temporal preference pattern
            temporal_pattern = self._extract_temporal_preferences(user_id, user_acts)
            if temporal_pattern:
                patterns.append(temporal_pattern)

            # Extract frequency pattern
            frequency_pattern = self._extract_frequency_pattern(user_id, user_acts)
            if frequency_pattern:
                patterns.append(frequency_pattern)

        return patterns

    def _extract_action_sequence(
        self,
        user_id: str,
        actions: list[Action]
    ) -> BehavioralPattern | None:
        """Extract common action sequences using sequential pattern mining."""
        if len(actions) < 3:
            return None

        # Extract action types
        action_types = [a.action_type for a in actions]

        # Find frequent 2-grams and 3-grams
        sequences_2 = [tuple(action_types[i:i+2]) for i in range(len(action_types)-1)]
        sequences_3 = [tuple(action_types[i:i+3]) for i in range(len(action_types)-2)]

        freq_2 = Counter(sequences_2)
        freq_3 = Counter(sequences_3)

        # Find most common sequence
        all_freq = list(freq_2.items()) + list(freq_3.items())
        if not all_freq:
            return None

        most_common = max(all_freq, key=lambda x: x[1])
        sequence, count = most_common

        support = count / len(actions)

        if support >= self.min_support and count >= 2:
            return BehavioralPattern(
                pattern_type=PatternType.BEHAVIORAL,
                description=f"Common action sequence: {' -> '.join(sequence)}",
                confidence=min(0.9, support * 2),
                support=support,
                entities=[user_id],
                evidence=[f"Sequence occurs {count} times"],
                user_id=user_id,
                action_sequence=list(sequence),
                frequency_per_day=len(actions) / max(1, (actions[-1].timestamp - actions[0].timestamp).days)
            )

        return None

    def _extract_temporal_preferences(
        self,
        user_id: str,
        actions: list[Action]
    ) -> BehavioralPattern | None:
        """Extract temporal preferences (preferred hours of activity)."""
        if len(actions) < 5:
            return None

        hours = [a.timestamp.hour for a in actions]
        hour_counts = Counter(hours)

        # Find preferred hours (>20% of activity)
        threshold = len(actions) * 0.15
        preferred_hours = [h for h, c in hour_counts.items() if c >= threshold]

        if len(preferred_hours) >= 1 and len(preferred_hours) <= 8:
            return BehavioralPattern(
                pattern_type=PatternType.BEHAVIORAL,
                description=f"Activity concentrated in hours: {preferred_hours}",
                confidence=min(0.9, len(preferred_hours) * 0.1 + 0.3),
                support=sum(hour_counts[h] for h in preferred_hours) / len(actions),
                entities=[user_id],
                evidence=[f"Preferred hours: {preferred_hours}"],
                user_id=user_id,
                preferred_times=preferred_hours,
                frequency_per_day=len(actions) / max(1, (actions[-1].timestamp - actions[0].timestamp).days)
            )

        return None

    def _extract_frequency_pattern(
        self,
        user_id: str,
        actions: list[Action]
    ) -> BehavioralPattern | None:
        """Extract frequency-based behavioral pattern."""
        if len(actions) < 5:
            return None

        time_span = (actions[-1].timestamp - actions[0].timestamp).total_seconds()
        days = max(1, time_span / 86400)

        frequency = len(actions) / days

        # Check for consistent frequency (low variance in daily counts)
        daily_counts = defaultdict(int)
        for a in actions:
            day_key = a.timestamp.strftime("%Y-%m-%d")
            daily_counts[day_key] += 1

        daily_values = list(daily_counts.values())
        if len(daily_values) >= 3:
            cv = np.std(daily_values) / (np.mean(daily_values) + 1e-10)  # Coefficient of variation
            consistency = max(0, 1 - cv)
        else:
            consistency = 0.5

        if frequency >= 0.5:  # At least once every 2 days
            return BehavioralPattern(
                pattern_type=PatternType.BEHAVIORAL,
                description=f"Regular activity: {frequency:.1f} actions/day",
                confidence=min(0.9, consistency + 0.3),
                support=0.7,
                entities=[user_id],
                evidence=[f"Frequency: {frequency:.2f}/day, Consistency: {consistency:.2f}"],
                user_id=user_id,
                frequency_per_day=frequency
            )

        return None

    # ========================================================================
    # COMMUNICATION PATTERN MINING
    # ========================================================================

    def mine_communication_patterns(
        self,
        communications: list[Communication],
        min_communications: int = 5
    ) -> list[CommunicationPattern]:
        """
        Mine communication patterns.

        Args:
            communications: List of communication events
            min_communications: Minimum communications required

        Returns:
            List of detected communication patterns
        """
        if len(communications) < min_communications:
            return []

        patterns = []

        # Build communication graph
        edges: dict[tuple[str, str], list[Communication]] = defaultdict(list)
        for comm in communications:
            key = (comm.sender, comm.recipient)
            edges[key].append(comm)

        # Analyze each communication pair
        for (sender, recipient), comms in edges.items():
            if len(comms) < 2:
                continue

            pattern = self._analyze_communication_pair(sender, recipient, comms)
            if pattern:
                patterns.append(pattern)

        # Analyze network structure
        network_pattern = self._analyze_network_structure(communications)
        if network_pattern:
            patterns.append(network_pattern)

        return patterns

    def _analyze_communication_pair(
        self,
        sender: str,
        recipient: str,
        comms: list[Communication]
    ) -> CommunicationPattern | None:
        """Analyze communication pattern between a specific pair."""
        if len(comms) < 2:
            return None

        # Sort by timestamp
        comms.sort(key=lambda c: c.timestamp)

        # Calculate response times (if bidirectional)
        response_times = []
        for i in range(1, len(comms)):
            delta = (comms[i].timestamp - comms[i-1].timestamp).total_seconds()
            if delta > 0 and delta < 86400 * 7:  # Max 7 days
                response_times.append(delta)

        # Calculate frequency
        time_span = (comms[-1].timestamp - comms[0].timestamp).total_seconds()
        days = max(1, time_span / 86400)
        frequency = len(comms) / days

        # Build pattern
        avg_response = np.mean(response_times) if response_times else None
        std_response = np.std(response_times) if len(response_times) > 1 else None

        return CommunicationPattern(
            pattern_type=PatternType.COMMUNICATION,
            description=f"Communication: {sender} -> {recipient} ({frequency:.1f}/day)",
            confidence=min(0.9, len(comms) / 20),
            support=len(comms) / max(1, int(days)),
            entities=[sender, recipient],
            evidence=[f"{len(comms)} communications over {days:.1f} days"],
            response_time_avg=timedelta(seconds=avg_response) if avg_response else None,
            response_time_std=timedelta(seconds=std_response) if std_response else None,
            frequency=frequency
        )

    def _analyze_network_structure(
        self,
        communications: list[Communication]
    ) -> CommunicationPattern | None:
        """Analyze overall network structure."""
        if len(communications) < 10:
            return None

        # Build adjacency list
        adjacency: dict[str, set[str]] = defaultdict(set)
        all_nodes: set[str] = set()

        for comm in communications:
            adjacency[comm.sender].add(comm.recipient)
            all_nodes.add(comm.sender)
            all_nodes.add(comm.recipient)

        # Calculate centrality metrics
        degrees = {node: len(adjacency[node]) for node in all_nodes}
        max_degree = max(degrees.values()) if degrees else 0

        # Find central nodes
        central_nodes = [n for n, d in degrees.items() if d == max_degree]

        # Calculate network density
        n_nodes = len(all_nodes)
        n_edges = sum(len(neighbors) for neighbors in adjacency.values())
        max_edges = n_nodes * (n_nodes - 1) if n_nodes > 1 else 1
        density = n_edges / max_edges if max_edges > 0 else 0

        return CommunicationPattern(
            pattern_type=PatternType.COMMUNICATION,
            description=f"Network: {n_nodes} nodes, density={density:.2f}",
            confidence=min(0.85, density + 0.3),
            support=len(communications) / max(1, n_nodes),
            entities=list(all_nodes),
            evidence=[f"Central nodes: {central_nodes}", f"Density: {density:.3f}"],
            frequency=len(communications) / max(1, (communications[-1].timestamp - communications[0].timestamp).days),
            network_centrality=max_degree / max(1, n_nodes - 1)
        )

    # ========================================================================
    # TRANSACTION FLOW ANALYSIS
    # ========================================================================

    def analyze_transaction_flows(
        self,
        transactions: list[Transaction],
        min_transactions: int = 5
    ) -> FlowPattern | None:
        """
        Analyze transaction flows for patterns.

        Args:
            transactions: List of financial transactions
            min_transactions: Minimum transactions required

        Returns:
            FlowPattern with transaction flow analysis
        """
        if len(transactions) < min_transactions:
            return None

        # Build flow graph
        flows: dict[tuple[str, str], list[Transaction]] = defaultdict(list)
        for tx in transactions:
            key = (tx.sender, tx.recipient)
            flows[key].append(tx)

        # Calculate flow volumes
        flow_volume: dict[tuple[str, str], float] = {}
        for key, txs in flows.items():
            total = sum(tx.amount for tx in txs)
            flow_volume[key] = total

        # Identify clusters using simple heuristic (high mutual flow)
        all_entities = set()
        for sender, recipient in flows.keys():
            all_entities.add(sender)
            all_entities.add(recipient)

        # Simple clustering: entities with multiple mutual connections
        clusters: dict[str, set[str]] = {}
        entity_cluster: dict[str, str] = {}

        for entity in all_entities:
            if entity not in entity_cluster:
                cluster_id = f"cluster_{len(clusters)}"
                clusters[cluster_id] = {entity}
                entity_cluster[entity] = cluster_id

                # Find connected entities
                for (s, r), txs in flows.items():
                    if s == entity or r == entity:
                        other = r if s == entity else s
                        if other not in entity_cluster:
                            clusters[cluster_id].add(other)
                            entity_cluster[other] = cluster_id

        # Identify intermediaries (entities with high in/out ratio)
        in_flows: dict[str, float] = defaultdict(float)
        out_flows: dict[str, float] = defaultdict(float)

        for (sender, recipient), volume in flow_volume.items():
            out_flows[sender] += volume
            in_flows[recipient] += volume

        intermediaries = []
        for entity in all_entities:
            total = in_flows[entity] + out_flows[entity]
            if total > 0:
                ratio = min(in_flows[entity], out_flows[entity]) / total
                if ratio > 0.4:  # Balanced in/out suggests intermediary
                    intermediaries.append(entity)

        # Detect cycles (simplified)
        cycle_detected = self._detect_cycles(flows)

        # Calculate concentration index (Gini coefficient)
        volumes = list(flow_volume.values())
        concentration = self._gini_coefficient(volumes) if volumes else 0.0

        return FlowPattern(
            pattern_type=PatternType.TRANSACTION,
            description=f"Transaction flow: {len(all_entities)} entities, {len(flows)} flows",
            confidence=min(0.9, len(transactions) / 100),
            support=len(transactions) / max(1, len(all_entities)),
            entities=list(all_entities),
            evidence=[f"{len(flows)} unique flows", f"Concentration: {concentration:.2f}"],
            source_clusters=list(clusters.keys()),
            destination_clusters=list(clusters.keys()),
            flow_volume=flow_volume,
            intermediaries=intermediaries,
            cycle_detected=cycle_detected,
            concentration_index=concentration
        )

    def _detect_cycles(
        self,
        flows: dict[tuple[str, str], list[Transaction]]
    ) -> bool:
        """Detect cycles in flow graph (simplified)."""
        # Build adjacency
        adjacency: dict[str, set[str]] = defaultdict(set)
        for sender, recipient in flows.keys():
            adjacency[sender].add(recipient)

        # Simple cycle detection: check for 2-cycles and 3-cycles
        for sender, recipients in adjacency.items():
            for recipient in recipients:
                # 2-cycle
                if sender in adjacency.get(recipient, set()):
                    return True
                # 3-cycle
                for r2 in adjacency.get(recipient, set()):
                    if sender in adjacency.get(r2, set()):
                        return True

        return False

    def _gini_coefficient(self, values: list[float]) -> float:
        """Calculate Gini coefficient for concentration."""
        if not values or len(values) < 2:
            return 0.0

        sorted_values = sorted(values)
        n = len(sorted_values)
        cumsum = np.cumsum(sorted_values)
        return (n + 1 - 2 * np.sum(cumsum) / cumsum[-1]) / n if cumsum[-1] > 0 else 0.0

    # ========================================================================
    # SEQUENTIAL PATTERN MINING
    # ========================================================================

    def find_sequential_patterns(
        self,
        sequences: list[list[str]],
        min_support: float | None = None,
        max_pattern_length: int = 5
    ) -> list[SequentialPattern]:
        """
        Find frequent sequential patterns using SPADE-like algorithm.

        Args:
            sequences: List of sequences (each sequence is a list of items)
            min_support: Minimum support threshold (default: self.min_support)
            max_pattern_length: Maximum length of patterns to find

        Returns:
            List of sequential patterns
        """
        min_support = min_support or self.min_support

        if not sequences or len(sequences) < 2:
            return []

        patterns = []

        # Find frequent 1-sequences
        item_counts: Counter = Counter()
        for seq in sequences:
            unique_items = set(seq)
            for item in unique_items:
                item_counts[item] += 1

        min_count = max(1, int(min_support * len(sequences)))
        frequent_items = {item for item, count in item_counts.items() if count >= min_count}

        # Find frequent 2-sequences
        seq2_counts: Counter = Counter()
        for seq in sequences:
            for i in range(len(seq) - 1):
                if seq[i] in frequent_items and seq[i+1] in frequent_items:
                    seq2_counts[(seq[i], seq[i+1])] += 1

        for seq, count in seq2_counts.items():
            if count >= min_count:
                support = count / len(sequences)
                patterns.append(SequentialPattern(
                    pattern_type=PatternType.SEQUENTIAL,
                    description=f"Sequence: {' -> '.join(seq)}",
                    confidence=min(0.9, support * 1.5),
                    support=support,
                    entities=[],
                    evidence=[f"Occurs in {count} sequences"],
                    sequence=list(seq),
                    occurrence_count=count
                ))

        # Find frequent 3-sequences (if enough data)
        if max_pattern_length >= 3 and len(sequences) >= 10:
            seq3_counts: Counter = Counter()
            for seq in sequences:
                for i in range(len(seq) - 2):
                    triple = (seq[i], seq[i+1], seq[i+2])
                    if all(item in frequent_items for item in triple):
                        seq3_counts[triple] += 1

            for seq, count in seq3_counts.items():
                if count >= max(2, min_count // 2):
                    support = count / len(sequences)
                    patterns.append(SequentialPattern(
                        pattern_type=PatternType.SEQUENTIAL,
                        description=f"Sequence: {' -> '.join(seq)}",
                        confidence=min(0.85, support * 2),
                        support=support,
                        entities=[],
                        evidence=[f"Occurs in {count} sequences"],
                        sequence=list(seq),
                        occurrence_count=count
                    ))

        return patterns

    # ========================================================================
    # ANOMALY DETECTION
    # ========================================================================

    def detect_anomalies_in_pattern(
        self,
        pattern: Pattern,
        new_data: list[Any],
        threshold: float = 2.0
    ) -> list[Anomaly]:
        """
        Detect anomalies relative to an established pattern.

        Args:
            pattern: Established pattern to compare against
            new_data: New data points to check
            threshold: Standard deviation threshold for anomaly detection

        Returns:
            List of detected anomalies
        """
        anomalies = []

        if isinstance(pattern, TemporalPattern):
            anomalies = self._detect_temporal_anomalies(pattern, new_data, threshold)
        elif isinstance(pattern, BehavioralPattern):
            anomalies = self._detect_behavioral_anomalies(pattern, new_data, threshold)
        elif isinstance(pattern, FlowPattern):
            anomalies = self._detect_flow_anomalies(pattern, new_data, threshold)

        return anomalies

    def _detect_temporal_anomalies(
        self,
        pattern: TemporalPattern,
        new_data: list[Event],
        threshold: float
    ) -> list[Anomaly]:
        """Detect anomalies in temporal pattern."""
        anomalies = []

        for event in new_data:
            if not isinstance(event, Event):
                continue

            # Check if event fits temporal pattern
            is_anomaly = False
            description = ""

            if pattern.seasonality == SeasonalityType.DAILY:
                hour = event.timestamp.hour
                if pattern.preferred_times and hour not in pattern.preferred_times:
                    is_anomaly = True
                    description = f"Event at unusual hour: {hour}"

            if pattern.period:
                # Check if event fits expected period
                if pattern.start_time:
                    elapsed = (event.timestamp - pattern.start_time).total_seconds()
                    period_secs = pattern.period.total_seconds()
                    phase = elapsed % period_secs
                    if phase > period_secs * 0.8 or phase < period_secs * 0.1:
                        is_anomaly = True
                        description = "Event at unexpected phase of period"

            if is_anomaly:
                anomalies.append(Anomaly(
                    anomaly_type=AnomalyType.CONTEXTUAL,
                    timestamp=event.timestamp,
                    entity_id=event.entity_id,
                    description=description,
                    severity=0.7,
                    related_pattern=pattern.description
                ))

        return anomalies

    def _detect_behavioral_anomalies(
        self,
        pattern: BehavioralPattern,
        new_data: list[Action],
        threshold: float
    ) -> list[Anomaly]:
        """Detect anomalies in behavioral pattern."""
        anomalies = []

        for action in new_data:
            if not isinstance(action, Action):
                continue

            # Check if action fits behavioral pattern
            is_anomaly = False
            description = ""

            if pattern.action_sequence:
                # Check if action continues the sequence
                if action.action_type not in pattern.action_sequence:
                    is_anomaly = True
                    description = f"Unusual action type: {action.action_type}"

            if pattern.preferred_times:
                hour = action.timestamp.hour
                if hour not in pattern.preferred_times:
                    is_anomaly = True
                    description = f"Activity at unusual time: {hour}:00"

            if is_anomaly:
                anomalies.append(Anomaly(
                    anomaly_type=AnomalyType.BEHAVIORAL,
                    timestamp=action.timestamp,
                    entity_id=action.user_id,
                    description=description,
                    severity=0.6,
                    related_pattern=pattern.description
                ))

        return anomalies

    def _detect_flow_anomalies(
        self,
        pattern: FlowPattern,
        new_data: list[Transaction],
        threshold: float
    ) -> list[Anomaly]:
        """Detect anomalies in flow pattern."""
        anomalies = []

        # Calculate expected flow statistics
        volumes = list(pattern.flow_volume.values())
        if not volumes:
            return anomalies

        mean_volume = np.mean(volumes)
        std_volume = np.std(volumes)

        for tx in new_data:
            if not isinstance(tx, Transaction):
                continue

            # Check for anomalous transaction
            key = (tx.sender, tx.recipient)

            # New flow
            if key not in pattern.flow_volume:
                anomalies.append(Anomaly(
                    anomaly_type=AnomalyType.COLLECTIVE,
                    timestamp=tx.timestamp,
                    entity_id=tx.sender,
                    description=f"New transaction flow: {tx.sender} -> {tx.recipient}",
                    severity=0.5,
                    related_pattern=pattern.description
                ))
            else:
                # Unusual amount
                if std_volume > 0:
                    z_score = abs(tx.amount - mean_volume) / std_volume
                    if z_score > threshold:
                        anomalies.append(Anomaly(
                            anomaly_type=AnomalyType.POINT,
                            timestamp=tx.timestamp,
                            entity_id=tx.sender,
                            description=f"Unusual transaction amount: {tx.amount}",
                            severity=min(0.95, z_score / 5),
                            expected_value=mean_volume,
                            actual_value=tx.amount,
                            related_pattern=pattern.description
                        ))

        return anomalies

    # ========================================================================
    # CROSS-PATTERN CORRELATION
    # ========================================================================

    def cross_pattern_correlation(
        self,
        patterns: list[Pattern],
        use_mlx: bool = True
    ) -> CorrelationMatrix:
        """
        Calculate correlations between patterns.

        Args:
            patterns: List of patterns to correlate
            use_mlx: Whether to use MLX acceleration

        Returns:
            CorrelationMatrix with pairwise correlations
        """
        if len(patterns) < 2:
            return CorrelationMatrix()

        n = len(patterns)
        pattern_ids = [f"pattern_{i}" for i in range(n)]

        # Create feature vectors for each pattern
        features = self._extract_pattern_features(patterns)

        if use_mlx and self.use_mlx and len(patterns) >= 3:
            return self._correlation_mlx(features, pattern_ids)
        else:
            return self._correlation_numpy(features, pattern_ids)

    def _extract_pattern_features(self, patterns: list[Pattern]) -> np.ndarray:
        """Extract numerical features from patterns for correlation."""
        features = []

        for pattern in patterns:
            feat = [
                pattern.confidence,
                pattern.support,
                len(pattern.entities) / 100,  # Normalize
            ]

            # Add type-specific features
            if isinstance(pattern, TemporalPattern):
                feat.extend([
                    1.0, 0.0, 0.0, 0.0, 0.0,  # Type encoding
                    len(pattern.burst_times) / 10,
                    1.0 if pattern.period else 0.0,
                ])
            elif isinstance(pattern, BehavioralPattern):
                feat.extend([
                    0.0, 1.0, 0.0, 0.0, 0.0,
                    pattern.frequency_per_day / 100,
                    len(pattern.preferred_times) / 24,
                ])
            elif isinstance(pattern, CommunicationPattern):
                feat.extend([
                    0.0, 0.0, 1.0, 0.0, 0.0,
                    pattern.frequency / 100,
                    pattern.network_centrality,
                ])
            elif isinstance(pattern, FlowPattern):
                feat.extend([
                    0.0, 0.0, 0.0, 1.0, 0.0,
                    pattern.concentration_index,
                    1.0 if pattern.cycle_detected else 0.0,
                ])
            elif isinstance(pattern, StructuralPattern):
                feat.extend([
                    0.0, 0.0, 0.0, 0.0, 1.0,
                    pattern.centralization,
                    pattern.density,
                ])
            else:
                feat.extend([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])

            features.append(feat)

        return np.array(features)

    def _correlation_mlx(
        self,
        features: np.ndarray,
        pattern_ids: list[str]
    ) -> CorrelationMatrix:
        """Calculate correlation using MLX (M1 optimized)."""
        try:
            # Convert to MLX array
            mx_features = mx.array(features)

            # Standardize
            mean = mx.mean(mx_features, axis=0)
            std = mx.std(mx_features, axis=0)
            standardized = (mx_features - mean) / (std + 1e-10)

            # Correlation matrix
            mx_features.shape[0]
            corr_matrix = mx.matmul(standardized, standardized.T) / standardized.shape[1]

            # Convert to numpy
            corr_np = np.array(corr_matrix)

            # Find significant pairs
            significant = []
            for i in range(len(pattern_ids)):
                for j in range(i + 1, len(pattern_ids)):
                    if abs(corr_np[i, j]) > 0.5:
                        significant.append((pattern_ids[i], pattern_ids[j], float(corr_np[i, j])))

            return CorrelationMatrix(
                pattern_ids=pattern_ids,
                correlation_matrix=corr_np,
                significant_pairs=significant
            )

        except Exception as e:
            logger.warning(f"MLX correlation failed, falling back: {e}")
            return self._correlation_numpy(features, pattern_ids)

    def _correlation_numpy(
        self,
        features: np.ndarray,
        pattern_ids: list[str]
    ) -> CorrelationMatrix:
        """Calculate correlation using NumPy."""
        # Standardize
        mean = np.mean(features, axis=0)
        std = np.std(features, axis=0)
        standardized = (features - mean) / (std + 1e-10)

        # Correlation matrix
        corr_matrix = np.corrcoef(standardized)

        # Find significant pairs
        significant = []
        for i in range(len(pattern_ids)):
            for j in range(i + 1, len(pattern_ids)):
                if abs(corr_matrix[i, j]) > 0.5:
                    significant.append((pattern_ids[i], pattern_ids[j], float(corr_matrix[i, j])))

        return CorrelationMatrix(
            pattern_ids=pattern_ids,
            correlation_matrix=corr_matrix,
            significant_pairs=significant
        )

    # ========================================================================
    # MLX-ACCELERATED METHODS
    # ========================================================================

    def detect_periodicity_mlx(
        self,
        timestamps: list[datetime],
        values: list[float] | None = None
    ) -> list[TemporalPattern]:
        """
        Detect periodicity using MLX FFT (public API).

        Args:
            timestamps: List of timestamps
            values: Optional values associated with timestamps

        Returns:
            List of detected temporal patterns with periodicity
        """
        if not self.use_mlx or len(timestamps) < 16:
            # Fall back to standard method
            return self._detect_periodicity(timestamps, values)

        base_time = timestamps[0]
        time_diffs = [(t - base_time).total_seconds() for t in timestamps]

        return self._detect_periodicity_mlx(time_diffs, timestamps)

    def batch_pattern_matching(
        self,
        patterns: list[Pattern],
        data_batch: list[Any],
        batch_size: int = 100
    ) -> dict[int, list[Pattern]]:
        """
        Match patterns against data in batches (M1 memory optimized).

        Args:
            patterns: Patterns to match
            data_batch: Data to match against
            batch_size: Size of processing batches

        Returns:
            Dictionary mapping data index to matched patterns
        """
        results: dict[int, list[Pattern]] = {}

        for i in range(0, len(data_batch), batch_size):
            batch = data_batch[i:i+batch_size]

            for j, item in enumerate(batch):
                matched = []
                idx = i + j

                for pattern in patterns:
                    if self._matches_pattern(item, pattern):
                        matched.append(pattern)

                if matched:
                    results[idx] = matched

            # Force garbage collection between batches
            if i + batch_size < len(data_batch):
                import gc
                gc.collect()

        return results

    def _matches_pattern(self, item: Any, pattern: Pattern) -> bool:
        """Check if item matches pattern (simplified)."""
        if isinstance(pattern, TemporalPattern) and isinstance(item, Event):
            # Check if event fits temporal constraints
            if pattern.start_time and pattern.end_time:
                if not (pattern.start_time <= item.timestamp <= pattern.end_time):
                    return False
            return True

        if isinstance(pattern, BehavioralPattern) and isinstance(item, Action):
            # Check if action matches behavioral pattern
            if pattern.user_id and item.user_id != pattern.user_id:
                return False
            if pattern.action_sequence and item.action_type not in pattern.action_sequence:
                return False
            return True

        return False


# =============================================================================
# FACTORY FUNCTION
# =============================================================================

def create_pattern_mining_engine(
    max_memory_mb: float = 512.0,
    use_mlx: bool = True,
    min_support: float = 0.1,
    min_confidence: float = 0.5
) -> PatternMiningEngine:
    """
    Factory function for creating PatternMiningEngine.

    Args:
        max_memory_mb: Maximum memory usage in MB
        use_mlx: Whether to use MLX acceleration on M1
        min_support: Minimum support threshold for patterns
        min_confidence: Minimum confidence threshold for patterns

    Returns:
        Configured PatternMiningEngine instance
    """
    return PatternMiningEngine(
        max_memory_mb=max_memory_mb,
        use_mlx=use_mlx,
        min_support=min_support,
        min_confidence=min_confidence
    )
