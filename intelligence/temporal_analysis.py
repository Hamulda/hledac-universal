"""
Temporal Analysis Engine
=========================

From deep_research/temporal_analyzer.py comments:
- Historical trend analysis
- Current state analysis
- Future projections
- Causal chain analysis
- Trend detection
- Scenario planning
- Turning point detection
- Temporal pattern generation

Time-series analysis for research data with M1 optimization.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

import numpy as np

logger = logging.getLogger(__name__)


class TrendDirection(Enum):
    """Direction of temporal trend."""
    INCREASING = "increasing"
    DECREASING = "decreasing"
    STABLE = "stable"
    VOLATILE = "volatile"


class PatternType(Enum):
    """Types of temporal patterns."""
    SEASONAL = "seasonal"
    CYCLICAL = "cyclical"
    TREND = "trend"
    RANDOM = "random"
    STEP_CHANGE = "step_change"


@dataclass
class TrendAnalysis:
    """Result of trend analysis."""
    direction: TrendDirection
    strength: float  # 0-1
    slope: float
    confidence: float
    start_value: float
    end_value: float
    time_period_days: int


@dataclass
class TemporalPattern:
    """Detected temporal pattern."""
    pattern_type: PatternType
    period_days: int | None
    amplitude: float
    confidence: float
    description: str


@dataclass
class CausalEvent:
    """Event in causal chain."""
    timestamp: datetime
    event: str
    strength: float  # 0-1
    lag_days: int
    evidence: list[str] = field(default_factory=list)


@dataclass
class Scenario:
    """Future scenario projection."""
    name: str
    probability: float
    key_drivers: list[str]
    outcomes: list[str]
    implications: str
    time_horizon_days: int


@dataclass
class TurningPoint:
    """Detected turning point in time series."""
    timestamp: datetime
    significance: float
    direction_change: str
    before_trend: TrendDirection
    after_trend: TrendDirection


@dataclass
class TemporalAnalysisResult:
    """Complete temporal analysis result."""
    query: str
    timestamp: datetime

    # Analyses
    trend: TrendAnalysis | None = None
    patterns: list[TemporalPattern] = field(default_factory=list)
    causal_chain: list[CausalEvent] = field(default_factory=list)
    scenarios: list[Scenario] = field(default_factory=list)
    turning_points: list[TurningPoint] = field(default_factory=list)

    # Projections
    projections: dict[str, list[float]] = field(default_factory=dict)
    projection_confidence: float = 0.0

    # Insights
    insights: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)

    # Metadata
    overall_confidence: float = 0.0
    analysis_duration_ms: int = 0


class TemporalAnalyzer:
    """
    Temporal analysis engine for time-series research data.

    From comments in temporal_analyzer.py:
    "Step 1: Historical trend analysis"
    "Step 2: Current state analysis"
    "Step 3: Future projections"
    "Step 4: Causal chain analysis"
    "Step 5: Trend detection"
    "Step 6: Scenario planning"
    "Step 7: Turning point detection"
    "Step 8: Generate temporal patterns"
    "Step 9: Generate insights and recommendations"
    """

    def __init__(self, min_data_points: int = 5):
        """
        Initialize analyzer.

        Args:
            min_data_points: Minimum data points needed for analysis
        """
        self.min_data_points = min_data_points

    def analyze(
        self,
        query: str,
        timestamps: list[datetime],
        values: list[float],
        analysis_types: list[str] | None = None
    ) -> TemporalAnalysisResult:
        """
        Perform complete temporal analysis.

        Args:
            query: Research query
            timestamps: Time points
            values: Values at time points
            analysis_types: Types of analysis to perform (default: all)

        Returns:
            TemporalAnalysisResult with all analyses
        """
        import time
        start_time = time.time()

        result = TemporalAnalysisResult(
            query=query,
            timestamp=datetime.now()
        )

        if len(timestamps) < self.min_data_points:
            result.insights.append("Insufficient data for temporal analysis")
            return result

        analysis_types = analysis_types or [
            'trend', 'patterns', 'causal', 'scenarios', 'turning_points', 'projections'
        ]

        # Sort data chronologically
        sorted_data = sorted(zip(timestamps, values, strict=False), key=lambda x: x[0])
        timestamps = [d[0] for d in sorted_data]
        values = [d[1] for d in sorted_data]

        # Step 1: Trend analysis
        if 'trend' in analysis_types:
            result.trend = self._analyze_trend(timestamps, values)

        # Step 2: Pattern detection
        if 'patterns' in analysis_types:
            result.patterns = self._detect_patterns(timestamps, values)

        # Step 3: Causal chain
        if 'causal' in analysis_types:
            result.causal_chain = self._analyze_causal_chain(timestamps, values, query)

        # Step 4: Scenario planning
        if 'scenarios' in analysis_types:
            result.scenarios = self._generate_scenarios(timestamps, values, query)

        # Step 5: Turning points
        if 'turning_points' in analysis_types:
            result.turning_points = self._detect_turning_points(timestamps, values)

        # Step 6: Future projections
        if 'projections' in analysis_types:
            result.projections, result.projection_confidence = self._generate_projections(
                timestamps, values
            )

        # Step 7: Generate insights
        result.insights = self._generate_insights(result)
        result.recommendations = self._generate_recommendations(result)

        # Calculate overall confidence
        confidences = []
        if result.trend:
            confidences.append(result.trend.confidence)
        if result.projection_confidence:
            confidences.append(result.projection_confidence)
        confidences.extend([p.confidence for p in result.patterns])

        result.overall_confidence = float(np.mean(confidences)) if confidences else 0.0
        result.analysis_duration_ms = int((time.time() - start_time) * 1000)

        return result

    def _analyze_trend(
        self,
        timestamps: list[datetime],
        values: list[float]
    ) -> TrendAnalysis:
        """
        Analyze historical trend.

        From comments: "Step 1: Historical trend analysis"
        """
        # Linear regression for trend
        x = np.arange(len(values))
        y = np.array(values)

        # Calculate slope using least squares
        n = len(x)
        slope = (n * np.sum(x * y) - np.sum(x) * np.sum(y)) / (
            n * np.sum(x ** 2) - np.sum(x) ** 2
        )

        # Determine direction
        if abs(slope) < 0.001:
            direction = TrendDirection.STABLE
        elif slope > 0:
            direction = TrendDirection.INCREASING
        else:
            direction = TrendDirection.DECREASING

        # Calculate strength (R-squared)
        y_mean = np.mean(y)
        ss_tot = np.sum((y - y_mean) ** 2)
        y_pred = slope * x + (np.mean(y) - slope * np.mean(x))
        ss_res = np.sum((y - y_pred) ** 2)
        r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0

        # Check volatility
        if np.std(y) > abs(slope * len(y)):
            direction = TrendDirection.VOLATILE

        time_period = (timestamps[-1] - timestamps[0]).days if len(timestamps) > 1 else 1

        return TrendAnalysis(
            direction=direction,
            strength=float(r_squared),
            slope=float(slope),
            confidence=float(r_squared),
            start_value=float(values[0]),
            end_value=float(values[-1]),
            time_period_days=time_period
        )

    def _detect_patterns(
        self,
        timestamps: list[datetime],
        values: list[float]
    ) -> list[TemporalPattern]:
        """
        Detect temporal patterns.

        From comments: "Step 5: Trend detection", "Step 8: Generate temporal patterns"
        """
        patterns = []
        y = np.array(values)

        # Check for seasonal pattern (simplified)
        if len(y) >= 12:
            # Autocorrelation at lag ~period
            autocorr = np.correlate(y - np.mean(y), y - np.mean(y), mode='full')
            autocorr = autocorr[len(autocorr)//2:]

            # Find peaks in autocorrelation
            if len(autocorr) > 2:
                for i in range(2, min(len(autocorr) - 1, len(y) // 2)):
                    if autocorr[i] > autocorr[i-1] and autocorr[i] > autocorr[i+1]:
                        if autocorr[i] > autocorr[0] * 0.3:  # Significant correlation
                            patterns.append(TemporalPattern(
                                pattern_type=PatternType.SEASONAL,
                                period_days=i * 30,  # Approximate
                                amplitude=float(np.std(y)),
                                confidence=0.6,
                                description=f"Possible seasonal pattern with period ~{i} data points"
                            ))
                            break

        # Check for cyclical pattern
        if len(y) >= 6:
            # Look for wave-like behavior
            zero_crossings = np.sum(np.diff(np.sign(y - np.mean(y))) != 0)
            if zero_crossings > len(y) / 4:
                patterns.append(TemporalPattern(
                    pattern_type=PatternType.CYCLICAL,
                    period_days=None,
                    amplitude=float(np.std(y)),
                    confidence=0.5,
                    description="Cyclical behavior detected"
                ))

        # Check for step change
        if len(y) >= 4:
            mid = len(y) // 2
            before_mean = np.mean(y[:mid])
            after_mean = np.mean(y[mid:])
            if abs(after_mean - before_mean) > np.std(y):
                patterns.append(TemporalPattern(
                    pattern_type=PatternType.STEP_CHANGE,
                    period_days=None,
                    amplitude=float(abs(after_mean - before_mean)),
                    confidence=0.7,
                    description=f"Step change detected around {timestamps[mid]}"
                ))

        return patterns

    def _analyze_causal_chain(
        self,
        timestamps: list[datetime],
        values: list[float],
        query: str
    ) -> list[CausalEvent]:
        """
        Analyze causal chain.

        From comments: "Step 4: Causal chain analysis"
        """
        events = []
        y = np.array(values)

        # Detect significant changes as events
        np.mean(y)
        std_val = np.std(y)

        for i in range(1, len(y)):
            change = abs(y[i] - y[i-1])
            if change > std_val * 1.5:  # Significant change
                event = CausalEvent(
                    timestamp=timestamps[i],
                    event="Significant value change",
                    strength=min(1.0, change / (std_val * 3)),
                    lag_days=(timestamps[i] - timestamps[i-1]).days if i > 0 else 0,
                    evidence=[f"Value changed by {change:.2f}"]
                )
                events.append(event)

        return events[:5]  # Limit to top 5 events

    def _generate_scenarios(
        self,
        timestamps: list[datetime],
        values: list[float],
        query: str
    ) -> list[Scenario]:
        """
        Generate future scenarios.

        From comments: "Step 6: Scenario planning"
        """
        scenarios = []
        trend = self._analyze_trend(timestamps, values)

        # Scenario 1: Trend continuation
        scenarios.append(Scenario(
            name="Trend Continuation",
            probability=0.5,
            key_drivers=["Current momentum", "Stable conditions"],
            outcomes=[f"Continued {trend.direction.value} trend"],
            implications="Expect similar pattern to continue",
            time_horizon_days=90
        ))

        # Scenario 2: Trend reversal
        scenarios.append(Scenario(
            name="Trend Reversal",
            probability=0.25,
            key_drivers=["Market saturation", "External shocks"],
            outcomes=["Change in current direction"],
            implications="Prepare for opposite trend",
            time_horizon_days=90
        ))

        # Scenario 3: Acceleration
        if trend.direction in [TrendDirection.INCREASING, TrendDirection.DECREASING]:
            scenarios.append(Scenario(
                name="Trend Acceleration",
                probability=0.15,
                key_drivers=["Positive feedback loops", "Momentum building"],
                outcomes=["Faster rate of change"],
                implications="Monitor closely for inflection",
                time_horizon_days=60
            ))

        # Scenario 4: Stabilization
        if trend.direction != TrendDirection.STABLE:
            scenarios.append(Scenario(
                name="Stabilization",
                probability=0.1,
                key_drivers=["Equilibrium reached", "Balancing forces"],
                outcomes=["Values stabilize"],
                implications="Reduced volatility expected",
                time_horizon_days=120
            ))

        return scenarios

    def _detect_turning_points(
        self,
        timestamps: list[datetime],
        values: list[float]
    ) -> list[TurningPoint]:
        """
        Detect turning points.

        From comments: "Step 7: Turning point detection"
        """
        points = []
        y = np.array(values)

        if len(y) < 5:
            return points

        # Simple turning point detection based on slope changes
        window = min(3, len(y) // 4)

        for i in range(window, len(y) - window):
            # Calculate slope before and after
            before_slope = (y[i] - y[i-window]) / window if window > 0 else 0
            after_slope = (y[i+window] - y[i]) / window if window > 0 else 0

            # Check for significant direction change
            if before_slope * after_slope < 0:  # Sign change
                if abs(before_slope) > 0.01 and abs(after_slope) > 0.01:
                    before_trend = (
                        TrendDirection.INCREASING if before_slope > 0
                        else TrendDirection.DECREASING
                    )
                    after_trend = (
                        TrendDirection.INCREASING if after_slope > 0
                        else TrendDirection.DECREASING
                    )

                    points.append(TurningPoint(
                        timestamp=timestamps[i],
                        significance=min(1.0, abs(before_slope - after_slope) / max(abs(before_slope), abs(after_slope))),
                        direction_change=f"{before_trend.value} to {after_trend.value}",
                        before_trend=before_trend,
                        after_trend=after_trend
                    ))

        # Return top 3 most significant
        points.sort(key=lambda p: p.significance, reverse=True)
        return points[:3]

    def _generate_projections(
        self,
        timestamps: list[datetime],
        values: list[float],
        horizon_days: int = 30
    ) -> tuple[dict[str, list[float]], float]:
        """
        Generate future projections using multiple advanced methods.

        From predictive_modeler.py comments:
        - "Step 1: Generate predictions using different methods"
        - "Step 2: Generate probabilistic forecasts"
        - "Step 6: Create ensemble prediction"
        """
        if len(values) < 3:
            return {}, 0.0

        projections = {}
        confidences = []

        # Method 1: Linear trend (existing)
        trend = self._analyze_trend(timestamps, values)
        linear_proj = self._project_linear(values, trend.slope, horizon_days)
        projections['linear'] = linear_proj
        confidences.append(trend.confidence)

        # Method 2: ARIMA(1,1,1) approximation (from predictive_modeler.py comments)
        if len(values) >= 5:
            arima_proj = self._project_arima(values, horizon_days)
            projections['arima'] = arima_proj
            confidences.append(0.75)

        # Method 3: Exponential smoothing (from predictive_modeler.py comments)
        exp_smooth_proj = self._project_exponential_smoothing(values, horizon_days)
        projections['exponential_smoothing'] = exp_smooth_proj
        confidences.append(0.7)

        # Method 4: Monte Carlo simulation (from predictive_modeler.py comments)
        if len(values) >= 5:
            mc_proj = self._project_monte_carlo(values, horizon_days)
            projections['monte_carlo_mean'] = mc_proj['mean']
            projections['monte_carlo_upper'] = mc_proj['upper']
            projections['monte_carlo_lower'] = mc_proj['lower']
            confidences.append(0.8)

        # Method 5: Bayesian updating (from predictive_modeler.py comments)
        bayesian_proj = self._project_bayesian(values, horizon_days)
        projections['bayesian'] = bayesian_proj['mean']
        projections['bayesian_ci_lower'] = bayesian_proj['ci_lower']
        projections['bayesian_ci_upper'] = bayesian_proj['ci_upper']
        confidences.append(0.85)

        # Method 6: Ensemble prediction (weighted average of all methods)
        ensemble_proj = self._create_ensemble_projection(projections, confidences)
        projections['ensemble'] = ensemble_proj

        # Overall confidence is weighted average
        overall_confidence = float(np.average(confidences, weights=[1.0]*len(confidences)))

        return projections, overall_confidence

    def _project_linear(
        self,
        values: list[float],
        slope: float,
        horizon_days: int
    ) -> list[float]:
        """Simple linear projection."""
        last_value = values[-1]
        return [last_value + slope * day for day in range(1, horizon_days + 1, 7)]

    def _project_arima(
        self,
        values: list[float],
        horizon_days: int
    ) -> list[float]:
        """
        ARIMA(1,1,1) approximation for time series prediction.

        From predictive_modeler.py comments:
        "Simple ARIMA(1,1,1) approximation"
        "First difference", "AR(1) coefficient estimation", "MA(1) coefficient estimation"
        """
        y = np.array(values)

        # First difference (d=1)
        diff = np.diff(y)

        # Estimate AR(1) coefficient
        if len(diff) > 1:
            ar_coeff = np.corrcoef(diff[:-1], diff[1:])[0, 1]
            ar_coeff = np.clip(ar_coeff, -0.9, 0.9)  # Stability
        else:
            ar_coeff = 0.0

        # Estimate MA(1) coefficient (simplified)
        ma_coeff = 0.3

        # Generate predictions
        predictions = []
        last_diff = diff[-1] if len(diff) > 0 else 0
        last_level = y[-1]

        for _ in range(0, horizon_days, 7):
            # ARIMA(1,1,1) prediction
            diff_pred = ar_coeff * last_diff + ma_coeff * np.random.normal(0, np.std(diff))
            level_pred = last_level + diff_pred

            predictions.append(level_pred)

            # Update for next iteration
            last_diff = diff_pred
            last_level = level_pred

        return predictions

    def _project_exponential_smoothing(
        self,
        values: list[float],
        horizon_days: int
    ) -> list[float]:
        """
        Exponential smoothing for time series prediction.

        From predictive_modeler.py comments:
        "2. Exponential smoothing"
        """
        alpha = 0.3  # Smoothing parameter

        # Calculate smoothed values
        smoothed = [values[0]]
        for value in values[1:]:
            smoothed.append(alpha * value + (1 - alpha) * smoothed[-1])

        # Project forward
        last_smoothed = smoothed[-1]
        trend = smoothed[-1] - smoothed[-2] if len(smoothed) > 1 else 0

        predictions = []
        for day in range(1, horizon_days + 1, 7):
            prediction = last_smoothed + trend * (day / 7)
            predictions.append(prediction)

        return predictions

    def _project_monte_carlo(
        self,
        values: list[float],
        horizon_days: int,
        n_simulations: int = 100
    ) -> dict[str, list[float]]:
        """
        Monte Carlo simulation for probabilistic forecasting.

        From predictive_modeler.py comments:
        "Calculate historical statistics"
        "Monte Carlo simulation"
        "Random walk with drift"
        """
        y = np.array(values)

        # Calculate historical statistics
        np.mean(y)
        historical_std = np.std(y)

        # Estimate drift (trend)
        drift = (y[-1] - y[0]) / len(y) if len(y) > 1 else 0

        # Run simulations
        simulations = []
        n_steps = horizon_days // 7

        for _ in range(n_simulations):
            path = [y[-1]]
            current = y[-1]

            for _ in range(n_steps):
                # Random walk with drift
                random_shock = np.random.normal(0, historical_std * 0.5)
                current = current + drift + random_shock
                path.append(current)

            simulations.append(path[1:])  # Exclude starting point

        # Calculate statistics across simulations
        simulations_array = np.array(simulations)
        mean_projection = simulations_array.mean(axis=0).tolist()
        upper_projection = np.percentile(simulations_array, 95, axis=0).tolist()
        lower_projection = np.percentile(simulations_array, 5, axis=0).tolist()

        return {
            'mean': mean_projection,
            'upper': upper_projection,
            'lower': lower_projection
        }

    def _project_bayesian(
        self,
        values: list[float],
        horizon_days: int
    ) -> dict[str, list[float]]:
        """
        Bayesian updating approach for prediction.

        From predictive_modeler.py comments:
        "Simple Bayesian updating approach"
        "Prior distribution (based on historical mean and variance)"
        "Likelihood based on recent data"
        "Posterior parameters"
        """
        y = np.array(values)

        # Prior parameters (based on historical data)
        prior_mean = np.mean(y)
        prior_var = np.var(y) if len(y) > 1 else 1.0

        # Likelihood from recent data (last 3 points)
        recent = y[-3:] if len(y) >= 3 else y
        np.mean(recent)
        likelihood_var = np.var(recent) if len(recent) > 1 else prior_var

        # Bayesian updating (conjugate normal prior)
        posterior_var = 1 / (1/prior_var + len(recent)/likelihood_var)
        posterior_mean = posterior_var * (prior_mean/prior_var + np.sum(recent)/likelihood_var)

        # Generate predictions from posterior
        predictions = []
        ci_lower = []
        ci_upper = []

        n_steps = horizon_days // 7
        for step in range(n_steps):
            # Prediction with increasing uncertainty
            pred_mean = posterior_mean
            pred_var = posterior_var * (1 + step * 0.1)  # Increasing uncertainty

            predictions.append(pred_mean)
            ci_lower.append(pred_mean - 1.96 * np.sqrt(pred_var))
            ci_upper.append(pred_mean + 1.96 * np.sqrt(pred_var))

        return {
            'mean': predictions,
            'ci_lower': ci_lower,
            'ci_upper': ci_upper
        }

    def _create_ensemble_projection(
        self,
        projections: dict[str, list[float]],
        confidences: list[float]
    ) -> list[float]:
        """
        Create ensemble prediction from multiple methods.

        From predictive_modeler.py comments:
        "Step 6: Create ensemble prediction"
        "Weighted average based on confidence scores"
        """
        # Get primary projections
        available_projections = []
        available_weights = []

        for i, method in enumerate(['linear', 'bayesian', 'exponential_smoothing', 'arima']):
            if method in projections:
                available_projections.append(projections[method])
                available_weights.append(confidences[min(i, len(confidences)-1)])

        if not available_projections:
            return []

        # Ensure all projections have same length
        min_len = min(len(p) for p in available_projections)
        available_projections = [p[:min_len] for p in available_projections]

        # Weighted average
        weights = np.array(available_weights)
        weights = weights / weights.sum()  # Normalize

        ensemble = np.average(available_projections, axis=0, weights=weights)
        return ensemble.tolist()

    def _generate_insights(self, result: TemporalAnalysisResult) -> list[str]:
        """Generate insights from analysis."""
        insights = []

        if result.trend:
            insights.append(
                f"Overall trend is {result.trend.direction.value} "
                f"with {result.trend.strength:.0%} confidence"
            )

        for pattern in result.patterns:
            insights.append(f"Detected {pattern.pattern_type.value} pattern: {pattern.description}")

        if result.turning_points:
            insights.append(f"Found {len(result.turning_points)} significant turning points")

        return insights

    def _generate_recommendations(self, result: TemporalAnalysisResult) -> list[str]:
        """Generate recommendations based on analysis."""
        recommendations = []

        if result.trend and result.trend.direction == TrendDirection.VOLATILE:
            recommendations.append("High volatility detected - consider risk mitigation strategies")

        if result.scenarios:
            top_scenario = max(result.scenarios, key=lambda s: s.probability)
            recommendations.append(
                f"Most likely scenario: {top_scenario.name} ({top_scenario.probability:.0%})"
            )

        return recommendations


def create_temporal_analyzer() -> TemporalAnalyzer:
    """Factory function for TemporalAnalyzer."""
    return TemporalAnalyzer()
