"""
Insight Generation Engine
==========================

From deep_research/insight_generator.py comments:
- Pattern recognition insights
- Anomaly detection insights
- Contradiction-based insights
- Gap identification insights
- Hypothesis generation insights
- Serendipity engineering insights

Advanced insight discovery for research synthesis.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class Insight:
    """Generated insight."""
    insight_id: str
    type: str
    content: str
    confidence: float
    novelty_score: float
    importance_score: float
    evidence: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    related_insights: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Pattern:
    """Discovered pattern."""
    pattern_type: str
    description: str
    occurrences: int
    confidence: float
    examples: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Anomaly:
    """Detected anomaly."""
    anomaly_type: str
    description: str
    severity: float
    expected_behavior: str
    actual_behavior: str
    implications: str


@dataclass(slots=True)
class Contradiction:
    """Identified contradiction."""
    contradiction_id: str
    statement_a: str
    statement_b: str
    severity: float
    resolution_options: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Gap:
    """Identified knowledge gap."""
    area: str
    description: str
    importance: float
    research_opportunities: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Hypothesis:
    """Generated hypothesis."""
    hypothesis: str
    confidence: float
    supporting_evidence: list[str] = field(default_factory=list)
    counter_evidence: list[str] = field(default_factory=list)
    test_methods: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CausalRelationship:
    """
    Causal relationship between variables.

    From predictive_modeler.py comments:
    "Step 3: Build causal models"
    "Extract causal model components"
    """
    cause: str
    effect: str
    strength: float  # 0-1
    confidence: float
    lag: int | None = None  # Time lag in periods
    evidence: list[str] = field(default_factory=list)
    alternative_explanations: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SynthesisLevel:
    """
    Multi-level synthesis result.

    From multi_level_synthesis.py comments:
    "Level 1: Surface Synthesis Processor"
    "Level 2: Deep Synthesis Processor"
    "Level 3: Meta Synthesis Processor"
    "Level 4: Conceptual Synthesis Processor"
    "Level 5: Paradigm Synthesis Processor"
    """
    level: int  # 1-5
    level_name: str
    synthesis: str
    confidence: float
    quality_score: float
    key_insights: list[str] = field(default_factory=list)


@dataclass(slots=True)
class InsightAnalysisResult:
    """Complete insight analysis result."""
    query: str
    insights: list[Insight] = field(default_factory=list)
    patterns: list[Pattern] = field(default_factory=list)
    anomalies: list[Anomaly] = field(default_factory=list)
    contradictions: list[Contradiction] = field(default_factory=list)
    gaps: list[Gap] = field(default_factory=list)
    hypotheses: list[Hypothesis] = field(default_factory=list)
    serendipity_opportunities: list[str] = field(default_factory=list)

    # Metrics
    total_discovered: int = 0
    high_confidence_count: int = 0
    novelty_distribution: dict[str, int] = field(default_factory=dict)

    def __post_init__(self):
        self.total_discovered = (
            len(self.insights) + len(self.patterns) + len(self.anomalies) +
            len(self.contradictions) + len(self.gaps) + len(self.hypotheses)
        )
        self.high_confidence_count = sum(
            1 for i in self.insights if i.confidence > 0.8
        )


class InsightEngine:
    """
    Advanced insight generation engine.

    From comments in insight_generator.py:
    "Step 2: Discover insights through multiple methods"
    "- Pattern recognition insights"
    "- Anomaly detection insights"
    "- Contradiction-based insights"
    "- Gap identification insights"
    "- Hypothesis generation insights"
    "- Serendipity engineering insights"
    """

    def __init__(self, min_confidence: float = 0.6):
        """
        Initialize insight engine.

        Args:
            min_confidence: Minimum confidence threshold for insights
        """
        self.min_confidence = min_confidence
        self.insight_counter = 0

    def analyze(
        self,
        query: str,
        data: list[dict[str, Any]],
        analysis_types: list[str] | None = None
    ) -> InsightAnalysisResult:
        """
        Perform comprehensive insight analysis.

        Args:
            query: Research query
            data: Research data to analyze
            analysis_types: Types of analysis (default: all)

        Returns:
            InsightAnalysisResult with all findings
        """
        result = InsightAnalysisResult(query=query)

        if not data:
            return result

        analysis_types = analysis_types or [
            'patterns', 'anomalies', 'contradictions', 'gaps',
            'hypotheses', 'serendipity'
        ]

        # Pattern recognition
        if 'patterns' in analysis_types:
            result.patterns = self._recognize_patterns(data)
            result.insights.extend(self._patterns_to_insights(result.patterns))

        # Anomaly detection
        if 'anomalies' in analysis_types:
            result.anomalies = self._detect_anomalies(data)
            result.insights.extend(self._anomalies_to_insights(result.anomalies))

        # Contradiction detection
        if 'contradictions' in analysis_types:
            result.contradictions = self._find_contradictions(data)
            result.insights.extend(self._contradictions_to_insights(result.contradictions))

        # Gap identification
        if 'gaps' in analysis_types:
            result.gaps = self._identify_gaps(data, query)
            result.insights.extend(self._gaps_to_insights(result.gaps))

        # Hypothesis generation
        if 'hypotheses' in analysis_types:
            result.hypotheses = self._generate_hypotheses(data, query)
            result.insights.extend(self._hypotheses_to_insights(result.hypotheses))

        # Serendipity engineering
        if 'serendipity' in analysis_types:
            result.serendipity_opportunities = self._engineer_serendipity(data, query)

        # Causal modeling (from predictive_modeler.py comments)
        if 'causal' in analysis_types:
            causal_relationships = self._build_causal_model(data, query)
            result.insights.extend(self._causal_to_insights(causal_relationships))

        # Multi-level synthesis (from multi_level_synthesis.py comments)
        if 'synthesis' in analysis_types:
            synthesis_levels = self._perform_multi_level_synthesis(data, query)
            result.insights.extend(self._synthesis_to_insights(synthesis_levels))

        # Score and rank insights
        result.insights = self._rank_insights(result.insights)

        return result

    def _recognize_patterns(self, data: list[dict[str, Any]]) -> list[Pattern]:
        """
        Recognize patterns in data.

        From comments: "Pattern recognition insights"
        """
        patterns = []

        if not data:
            return patterns

        # Extract all string values
        texts = []
        for item in data:
            for _key, value in item.items():
                if isinstance(value, str):
                    texts.append(value)
                elif isinstance(value, list):
                    texts.extend([str(v) for v in value if isinstance(v, str)])

        if not texts:
            return patterns

        # Common phrase detection
        phrases = self._extract_common_phrases(texts)
        for phrase, count in phrases.items():
            if count >= 2:
                patterns.append(Pattern(
                    pattern_type="semantic",
                    description=f"Recurring phrase: '{phrase}'",
                    occurrences=count,
                    confidence=min(1.0, count / len(texts) + 0.5),
                    examples=[t for t in texts if phrase in t][:3]
                ))

        # Keyword co-occurrence patterns
        keywords = self._extract_keywords(texts)
        if len(keywords) >= 2:
            patterns.append(Pattern(
                pattern_type="co_occurrence",
                description=f"Keywords often appear together: {', '.join(keywords[:5])}",
                occurrences=len(texts),
                confidence=0.7,
                examples=[]
            ))

        return patterns

    def _detect_anomalies(self, data: list[dict[str, Any]]) -> list[Anomaly]:
        """
        Detect anomalies in data.

        From comments: "Anomaly detection insights"
        """
        anomalies = []

        if len(data) < 3:
            return anomalies

        # Check for outliers in numeric fields
        numeric_fields = {}
        for item in data:
            for key, value in item.items():
                if isinstance(value, (int, float)):
                    if key not in numeric_fields:
                        numeric_fields[key] = []
                    numeric_fields[key].append(value)

        for field, values in numeric_fields.items():
            if len(values) >= 3:
                mean = np.mean(values)
                std = np.std(values)

                for _i, val in enumerate(values):
                    if std > 0 and abs(val - mean) > 2 * std:
                        anomalies.append(Anomaly(
                            anomaly_type="statistical_outlier",
                            description=f"Unusual value in '{field}': {val}",
                            severity=min(1.0, abs(val - mean) / (3 * std)),
                            expected_behavior=f"Typical range: {mean-std:.2f} to {mean+std:.2f}",
                            actual_behavior=f"Observed: {val}",
                            implications="May indicate special case or data error"
                        ))

        # Check for missing fields (structural anomalies)
        all_keys = set()
        for item in data:
            all_keys.update(item.keys())

        for item in data:
            missing = all_keys - set(item.keys())
            if missing and len(missing) > len(all_keys) * 0.3:
                anomalies.append(Anomaly(
                    anomaly_type="incomplete_data",
                    description=f"Item missing {len(missing)} expected fields",
                    severity=len(missing) / len(all_keys),
                    expected_behavior=f"All items should have: {', '.join(all_keys)}",
                    actual_behavior=f"Missing: {', '.join(missing)}",
                    implications="Data collection may be incomplete"
                ))

        return anomalies

    def _find_contradictions(self, data: list[dict[str, Any]]) -> list[Contradiction]:
        """
        Find contradictions in data.

        From comments: "Contradiction-based insights"
        """
        contradictions = []

        # Simple contradiction: same field with different values in related items
        for i, item1 in enumerate(data):
            for j, item2 in enumerate(data[i+1:], i+1):
                for key in set(item1.keys()) & set(item2.keys()):
                    val1 = item1[key]
                    val2 = item2[key]

                    # Check for direct contradiction in strings
                    if isinstance(val1, str) and isinstance(val2, str):
                        # Simple negation detection
                        negators = ['not ', 'no ', 'never ', "doesn't ", "isn't "]
                        val1_neg = any(val1.lower().startswith(n) for n in negators)
                        val2_neg = any(val2.lower().startswith(n) for n in negators)

                        if val1_neg != val2_neg and len(val1) > 10 and len(val2) > 10:
                            contradictions.append(Contradiction(
                                contradiction_id=f"cont_{i}_{j}_{key}",
                                statement_a=f"{key}: {val1}",
                                statement_b=f"{key}: {val2}",
                                severity=0.7,
                                resolution_options=[
                                    "Verify source reliability",
                                    "Check temporal context",
                                    "Consider different interpretations"
                                ]
                            ))

        return contradictions

    def _identify_gaps(
        self,
        data: list[dict[str, Any]],
        query: str
    ) -> list[Gap]:
        """
        Identify knowledge gaps.

        From comments: "Gap identification insights"
        """
        gaps = []

        # Common research gap patterns
        query.lower()

        # Check for temporal gaps
        dates = []
        for item in data:
            for key in ['date', 'timestamp', 'year', 'created']:
                if key in item:
                    dates.append(item[key])

        if len(dates) >= 2:
            gaps.append(Gap(
                area="temporal_coverage",
                description="Limited temporal range in available data",
                importance=0.6,
                research_opportunities=[
                    "Extend data collection to earlier periods",
                    "Include more recent data points"
                ]
            ))

        # Check for geographic gaps
        locations = []
        for item in data:
            for key in ['location', 'country', 'region', 'place']:
                if key in item:
                    locations.append(item[key])

        if len(set(locations)) < 3 and len(data) > 5:
            gaps.append(Gap(
                area="geographic_coverage",
                description="Limited geographic diversity in data",
                importance=0.7,
                research_opportunities=[
                    "Collect data from additional regions",
                    "Compare findings across different locations"
                ]
            ))

        # Check for source diversity
        sources = set()
        for item in data:
            if 'source' in item:
                sources.add(item['source'])

        if len(sources) < 2 and len(data) > 5:
            gaps.append(Gap(
                area="source_diversity",
                description="Limited source diversity may bias results",
                importance=0.8,
                research_opportunities=[
                    "Incorporate additional data sources",
                    "Cross-validate with independent datasets"
                ]
            ))

        return gaps

    def _generate_hypotheses(
        self,
        data: list[dict[str, Any]],
        query: str
    ) -> list[Hypothesis]:
        """
        Generate hypotheses from data.

        From comments: "Hypothesis generation insights"
        """
        hypotheses = []

        if not data:
            return hypotheses

        # Extract key themes
        themes = self._extract_themes(data)

        if len(themes) >= 2:
            hypotheses.append(Hypothesis(
                hypothesis=f"There is a causal relationship between {themes[0]} and {themes[1]}",
                confidence=0.6,
                supporting_evidence=["Co-occurrence in data"],
                counter_evidence=["Correlation does not imply causation"],
                test_methods=["Controlled experiment", "Longitudinal study"]
            ))

        # Pattern-based hypothesis
        patterns = self._recognize_patterns(data)
        if patterns:
            top_pattern = max(patterns, key=lambda p: p.confidence)
            hypotheses.append(Hypothesis(
                hypothesis=f"The observed pattern '{top_pattern.description}' will continue in future data",
                confidence=top_pattern.confidence * 0.8,
                supporting_evidence=[f"Observed {top_pattern.occurrences} times"],
                counter_evidence=["Past patterns may not predict future behavior"],
                test_methods=["Predictive validation", "Out-of-sample testing"]
            ))

        return hypotheses

    def _engineer_serendipity(
        self,
        data: list[dict[str, Any]],
        query: str
    ) -> list[str]:
        """
        Engineer serendipitous discoveries.

        From comments: "Serendipity engineering insights"
        """
        opportunities = []

        # Look for unexpected connections
        ' '.join([
            str(v) for item in data for v in item.values()
            if isinstance(v, str)
        ]).lower()

        # Suggest related but unexpected areas
        serendipity_triggers = [
            "Consider looking at historical parallels",
            "Investigate similar patterns in unrelated fields",
            "Check for inverse relationships",
            "Explore edge cases and outliers",
            "Look for seasonal or cyclic variations",
            "Examine the absence of expected patterns"
        ]

        # Select based on query characteristics
        if 'technology' in query.lower():
            opportunities.append(serendipity_triggers[0])
            opportunities.append("Research how other industries solved similar problems")

        if 'social' in query.lower() or 'human' in query.lower():
            opportunities.append(serendipity_triggers[1])
            opportunities.append("Study animal behavior analogs")

        if 'economic' in query.lower() or 'market' in query.lower():
            opportunities.append(serendipity_triggers[2])
            opportunities.append("Look for contrarian indicators")

        if not opportunities:
            opportunities = serendipity_triggers[:3]

        return opportunities

    def _patterns_to_insights(self, patterns: list[Pattern]) -> list[Insight]:
        """Convert patterns to insights."""
        return [
            Insight(
                insight_id=self._next_insight_id(),
                type="pattern",
                content=f"Pattern detected: {p.description}",
                confidence=p.confidence,
                novelty_score=0.5,
                importance_score=0.6,
                tags=["pattern", p.pattern_type]
            )
            for p in patterns if p.confidence >= self.min_confidence
        ]

    def _anomalies_to_insights(self, anomalies: list[Anomaly]) -> list[Insight]:
        """Convert anomalies to insights."""
        return [
            Insight(
                insight_id=self._next_insight_id(),
                type="anomaly",
                content=f"Anomaly: {a.description}. Implications: {a.implications}",
                confidence=0.7,
                novelty_score=min(1.0, a.severity + 0.3),
                importance_score=a.severity,
                tags=["anomaly", a.anomaly_type]
            )
            for a in anomalies if a.severity > 0.5
        ]

    def _contradictions_to_insights(self, contradictions: list[Contradiction]) -> list[Insight]:
        """Convert contradictions to insights."""
        return [
            Insight(
                insight_id=self._next_insight_id(),
                type="contradiction",
                content=f"Contradiction found: '{c.statement_a}' vs '{c.statement_b}'",
                confidence=c.severity,
                novelty_score=0.8,
                importance_score=c.severity,
                tags=["contradiction"]
            )
            for c in contradictions
        ]

    def _gaps_to_insights(self, gaps: list[Gap]) -> list[Insight]:
        """Convert gaps to insights."""
        return [
            Insight(
                insight_id=self._next_insight_id(),
                type="gap",
                content=f"Knowledge gap in {g.area}: {g.description}",
                confidence=0.75,
                novelty_score=0.6,
                importance_score=g.importance,
                tags=["gap", g.area]
            )
            for g in gaps
        ]

    def _hypotheses_to_insights(self, hypotheses: list[Hypothesis]) -> list[Insight]:
        """Convert hypotheses to insights."""
        return [
            Insight(
                insight_id=self._next_insight_id(),
                type="hypothesis",
                content=f"Hypothesis: {h.hypothesis}",
                confidence=h.confidence,
                novelty_score=0.7,
                importance_score=0.75,
                evidence=h.supporting_evidence,
                tags=["hypothesis"]
            )
            for h in hypotheses
        ]

    def _rank_insights(self, insights: list[Insight]) -> list[Insight]:
        """Rank insights by composite score."""
        for insight in insights:
            # Composite score combining multiple factors
            insight.importance_score = (
                insight.confidence * 0.4 +
                insight.novelty_score * 0.35 +
                (1.0 if insight.evidence else 0.5) * 0.25
            )

        # Sort by importance
        return sorted(insights, key=lambda i: i.importance_score, reverse=True)

    def _next_insight_id(self) -> str:
        """Generate next insight ID."""
        self.insight_counter += 1
        return f"insight_{self.insight_counter}"

    def _extract_common_phrases(self, texts: list[str]) -> dict[str, int]:
        """Extract common phrases from texts."""
        phrases = {}
        for text in texts:
            words = text.lower().split()
            for i in range(len(words) - 2):
                phrase = ' '.join(words[i:i+3])
                phrases[phrase] = phrases.get(phrase, 0) + 1
        return {p: c for p, c in phrases.items() if c >= 2}

    def _extract_keywords(self, texts: list[str]) -> list[str]:
        """Extract keywords from texts."""
        # Simple keyword extraction
        word_freq = {}
        stop_words = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with'}

        for text in texts:
            words = re.findall(r'\b[a-zA-Z]{4,}\b', text.lower())
            for word in words:
                if word not in stop_words:
                    word_freq[word] = word_freq.get(word, 0) + 1

        # Return most frequent
        sorted_words = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)
        return [w for w, _ in sorted_words[:10]]

    def _extract_themes(self, data: list[dict[str, Any]]) -> list[str]:
        """Extract main themes from data."""
        texts = []
        for item in data:
            for key in ['topic', 'category', 'theme', 'subject']:
                if key in item:
                    texts.append(str(item[key]))

        if texts:
            return self._extract_keywords(texts)[:3]
        return []

    # =============================================================================
    # CAUSAL MODELING (from predictive_modeler.py comments)
    # =============================================================================

    def _build_causal_model(
        self,
        data: list[dict[str, Any]],
        query: str
    ) -> list[CausalRelationship]:
        """
        Build causal model from data.

        From predictive_modeler.py comments:
        "Step 3: Build causal models"
        "Extract causal model components"
        "Extract potential variable names"
        """
        causal_relationships = []

        if len(data) < 3:
            return causal_relationships

        # Extract numeric variables
        variables = {}
        for item in data:
            for key, value in item.items():
                if isinstance(value, (int, float)) and key not in ['id', 'timestamp']:
                    if key not in variables:
                        variables[key] = []
                    variables[key].append(float(value))

        if len(variables) < 2:
            return causal_relationships

        # Look for potential causal relationships using simple correlation
        var_names = list(variables.keys())
        for i, var1 in enumerate(var_names):
            for var2 in var_names[i+1:]:
                values1 = variables[var1]
                values2 = variables[var2]

                if len(values1) == len(values2) and len(values1) > 2:
                    # Calculate correlation
                    corr = np.corrcoef(values1, values2)[0, 1]

                    if abs(corr) > 0.5:  # Significant correlation
                        # Determine direction based on time if available
                        cause, effect = (var1, var2) if corr > 0 else (var2, var1)

                        # Check for lag (simplified)
                        lag = self._estimate_lag(values1, values2)

                        causal_relationships.append(CausalRelationship(
                            cause=cause,
                            effect=effect,
                            strength=abs(corr),
                            confidence=min(1.0, abs(corr) + 0.1),
                            lag=lag,
                            evidence=[f"Correlation coefficient: {corr:.3f}"],
                            alternative_explanations=[
                                "Third variable influence",
                                "Coincidental correlation"
                            ]
                        ))

        return causal_relationships

    def _estimate_lag(
        self,
        values1: list[float],
        values2: list[float],
        max_lag: int = 3
    ) -> int | None:
        """Estimate time lag between two variables."""
        if len(values1) < max_lag + 2:
            return None

        best_lag = 0
        best_corr = abs(np.corrcoef(values1, values2)[0, 1])

        for lag in range(1, max_lag + 1):
            if len(values1) > lag:
                corr = abs(np.corrcoef(values1[:-lag], values2[lag:])[0, 1])
                if corr > best_corr:
                    best_corr = corr
                    best_lag = lag

        return best_lag if best_lag > 0 else None

    def _causal_to_insights(
        self,
        causal_relationships: list[CausalRelationship]
    ) -> list[Insight]:
        """Convert causal relationships to insights."""
        return [
            Insight(
                insight_id=self._next_insight_id(),
                type="causal_relationship",
                content=f"Causal relationship: {c.cause} → {c.effect} (strength: {c.strength:.2f})",
                confidence=c.confidence,
                novelty_score=0.7,
                importance_score=c.strength,
                evidence=c.evidence,
                tags=["causal", "relationship"]
            )
            for c in causal_relationships if c.confidence >= self.min_confidence
        ]

    # =============================================================================
    # MULTI-LEVEL SYNTHESIS (from multi_level_synthesis.py comments)
    # =============================================================================

    def _perform_multi_level_synthesis(
        self,
        data: list[dict[str, Any]],
        query: str
    ) -> list[SynthesisLevel]:
        """
        Perform multi-level synthesis.

        From multi_level_synthesis.py comments:
        "Level 1: Surface Synthesis Processor" - Basic aggregation
        "Level 2: Deep Synthesis Processor" - Pattern extraction
        "Level 3: Meta Synthesis Processor" - Cross-pattern analysis
        "Level 4: Conceptual Synthesis Processor" - Theory building
        "Level 5: Paradigm Synthesis Processor" - Paradigm shifts
        """
        levels = []

        if not data:
            return levels

        # Level 1: Surface Synthesis
        level1 = self._synthesis_level_1(data, query)
        levels.append(level1)

        # Level 2: Deep Synthesis
        level2 = self._synthesis_level_2(data, query, level1)
        levels.append(level2)

        # Level 3: Meta Synthesis
        level3 = self._synthesis_level_3(data, query, level2)
        levels.append(level3)

        # Level 4: Conceptual Synthesis
        level4 = self._synthesis_level_4(data, query, level3)
        levels.append(level4)

        # Level 5: Paradigm Synthesis
        level5 = self._synthesis_level_5(data, query, level4)
        levels.append(level5)

        return levels

    def _synthesis_level_1(
        self,
        data: list[dict[str, Any]],
        query: str
    ) -> SynthesisLevel:
        """Level 1: Surface Synthesis - Basic aggregation."""
        # Count and summarize basic facts
        facts = []
        for item in data:
            if 'fact' in item:
                facts.append(item['fact'])
            elif 'content' in item:
                facts.append(item['content'])

        summary = f"Surface analysis: {len(data)} data points, {len(facts)} explicit facts."

        return SynthesisLevel(
            level=1,
            level_name="Surface Synthesis",
            synthesis=summary,
            confidence=0.9,
            quality_score=0.7,
            key_insights=[f"Found {len(facts)} facts"] if facts else []
        )

    def _synthesis_level_2(
        self,
        data: list[dict[str, Any]],
        query: str,
        prev_level: SynthesisLevel
    ) -> SynthesisLevel:
        """Level 2: Deep Synthesis - Pattern extraction."""
        patterns = self._recognize_patterns(data)

        synthesis = f"Deep synthesis identified {len(patterns)} patterns."
        if patterns:
            top_pattern = max(patterns, key=lambda p: p.confidence)
            synthesis += f" Strongest: {top_pattern.description}"

        return SynthesisLevel(
            level=2,
            level_name="Deep Synthesis",
            synthesis=synthesis,
            confidence=0.8,
            quality_score=0.75,
            key_insights=[p.description for p in patterns[:3]]
        )

    def _synthesis_level_3(
        self,
        data: list[dict[str, Any]],
        query: str,
        prev_level: SynthesisLevel
    ) -> SynthesisLevel:
        """Level 3: Meta Synthesis - Cross-pattern analysis."""
        # Analyze relationships between patterns
        contradictions = self._find_contradictions(data)
        gaps = self._identify_gaps(data, query)

        synthesis = f"Meta synthesis: {len(contradictions)} contradictions, {len(gaps)} gaps identified."

        insights = []
        if contradictions:
            insights.append(f"Key contradiction: {contradictions[0].statement_a[:50]}...")
        if gaps:
            insights.append(f"Critical gap: {gaps[0].description[:50]}...")

        return SynthesisLevel(
            level=3,
            level_name="Meta Synthesis",
            synthesis=synthesis,
            confidence=0.75,
            quality_score=0.8,
            key_insights=insights
        )

    def _synthesis_level_4(
        self,
        data: list[dict[str, Any]],
        query: str,
        prev_level: SynthesisLevel
    ) -> SynthesisLevel:
        """Level 4: Conceptual Synthesis - Theory building."""
        hypotheses = self._generate_hypotheses(data, query)
        causal = self._build_causal_model(data, query)

        synthesis = f"Conceptual synthesis generated {len(hypotheses)} hypotheses."
        if causal:
            synthesis += f" Found {len(causal)} causal relationships."

        insights = [h.hypothesis for h in hypotheses[:2]]
        if causal:
            insights.append(f"Causal: {causal[0].cause} → {causal[0].effect}")

        return SynthesisLevel(
            level=4,
            level_name="Conceptual Synthesis",
            synthesis=synthesis,
            confidence=0.7,
            quality_score=0.85,
            key_insights=insights
        )

    def _synthesis_level_5(
        self,
        data: list[dict[str, Any]],
        query: str,
        prev_level: SynthesisLevel
    ) -> SynthesisLevel:
        """Level 5: Paradigm Synthesis - Paradigm shifts."""
        # Look for paradigm-shifting insights
        anomalies = self._detect_anomalies(data)

        synthesis = "Paradigm synthesis: Exploring potential paradigm shifts."

        paradigm_insights = []
        if anomalies:
            severe_anomalies = [a for a in anomalies if a.severity > 0.8]
            if severe_anomalies:
                paradigm_insights.append(
                    f"Severe anomaly suggests paradigm shift: {severe_anomalies[0].description[:50]}..."
                )

        # Check for serendipitous discoveries
        serendipity = self._engineer_serendipity(data, query)
        if serendipity:
            paradigm_insights.append(f"Unexpected opportunity: {serendipity[0]}")

        if not paradigm_insights:
            paradigm_insights.append("No immediate paradigm shift detected")

        return SynthesisLevel(
            level=5,
            level_name="Paradigm Synthesis",
            synthesis=synthesis,
            confidence=0.6,
            quality_score=0.9,
            key_insights=paradigm_insights
        )

    def _synthesis_to_insights(
        self,
        synthesis_levels: list[SynthesisLevel]
    ) -> list[Insight]:
        """Convert synthesis levels to insights."""
        insights = []

        for level in synthesis_levels:
            insights.append(Insight(
                insight_id=self._next_insight_id(),
                type=f"synthesis_level_{level.level}",
                content=f"{level.level_name}: {level.synthesis}",
                confidence=level.confidence,
                novelty_score=0.5 + (level.level * 0.1),
                importance_score=level.quality_score,
                evidence=level.key_insights,
                tags=["synthesis", level.level_name.lower().replace(" ", "_")]
            ))

        return insights


def create_insight_engine() -> InsightEngine:
    """Factory function for InsightEngine."""
    return InsightEngine()
