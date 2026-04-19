"""
Tree of Thoughts (ToT) Integration Layer for Hledac Universal Orchestrator
==========================================================================

Unified ToT interface for autonomous integration into the Hledac research platform.
Provides intelligent complexity analysis and automatic ToT activation decisions.

M1 8GB RAM Optimizations:
- Memory monitoring with 6GB hard limit
- Aggressive garbage collection between phases
- Context swap architecture (no parallel models)
- Graceful fallbacks when memory is constrained

Author: Hledac AI Research Platform
Version: 1.0.0
"""

from __future__ import annotations

import asyncio
import gc
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

# Import types
from .types import ComplexityAnalysis, ResearchResult

# Lazy import ToT components to avoid heavy loading
TOT_AVAILABLE = False
TotOrchestrator = None

logger = logging.getLogger(__name__)


def _load_tot_components():
    """Lazy load ToT components."""
    global TOT_AVAILABLE, TotOrchestrator
    if TOT_AVAILABLE:
        return True
    try:
        from ..tree_of_thoughts.tot_orchestrator import TotOrchestrator as _TotOrchestrator
        TotOrchestrator = _TotOrchestrator
        TOT_AVAILABLE = True
        return True
    except ImportError as e:
        logger.warning(f"ToT components not available: {e}")
        TOT_AVAILABLE = False
        return False


@dataclass
class TotResult:
    """Result from Tree of Thoughts reasoning."""
    solution: Optional[str]
    confidence_score: float
    reasoning_trace: List[Dict[str, Any]]
    tree_statistics: Dict[str, Any]
    computation_time: float
    iterations_performed: int
    converged: bool
    backtracking_used: bool
    memory_usage_mb: float
    error: Optional[str] = None

    def to_research_result(self, query: str) -> ResearchResult:
        """Convert ToT result to standard ResearchResult."""
        return ResearchResult(
            success=self.solution is not None and self.error is None,
            query=query,
            mode="tree_of_thoughts",
            final_answer=self.solution or "No solution found",
            sources=[],
            knowledge_graph={},
            execution_history=self.reasoning_trace,
            agent_results=[],
            statistics={
                "confidence": self.confidence_score,
                "computation_time": self.computation_time,
                "iterations": self.iterations_performed,
                "converged": self.converged,
                "backtracking_used": self.backtracking_used,
                "memory_usage_mb": self.memory_usage_mb,
                "tree_stats": self.tree_statistics,
            },
            metadata={
                "reasoning_mode": "tree_of_thoughts",
                "tree_depth": self.tree_statistics.get("max_depth", 0),
                "exploration_rate": self.tree_statistics.get("exploration_rate", 0.0),
            }
        )


@dataclass
class TotConfig:
    """Configuration for Tree of Thoughts integration."""
    enable_tot_autonomous: bool = True
    tot_complexity_threshold: float = 0.70  # Lowered from 0.75
    tot_max_depth: int = 5
    tot_max_time: float = 120.0
    tot_enable_backtracking: bool = True
    tot_enable_mcts: bool = True
    hybrid_complexity_threshold: float = 0.45  # Lowered from 0.50
    memory_limit_mb: float = 6000.0  # M1 8GB hard limit
    enable_gc_between_phases: bool = True


class TotIntegrationLayer:
    """
    Unified Tree of Thoughts integration layer for Hledac.

    Provides intelligent complexity analysis and autonomous ToT activation
    with M1 8GB RAM optimizations.

    Usage:
        >>> tot_layer = TotIntegrationLayer()
        >>> should_use, confidence = tot_layer.should_activate_tot(query, context)
        >>> if should_use:
        ...     result = await tot_layer.solve_problem(problem, context)
    """

    # Complexity indicator patterns - English
    MULTI_STEP_KEYWORDS_EN = [
        r"\bhow would\b", r"\banalyze\b", r"\bcompare\b", r"\bevaluate\b", r"\bassess\b",
        r"\bexplain\b", r"\bdetermine\b", r"\binvestigate\b", r"\bexplore\b", r"\bexamine\b",
        r"\bwhat if\b", r"\bconsider\b", r"\bdiscuss\b", r"\bjustify\b", r"\brecommend\b",
        r"\bstrategize\b", r"\bplan\b", r"\bapproach\b", r"\bmethodology\b", r"\bframework\b"
    ]

    ALTERNATIVES_KEYWORDS_EN = [
        r"\bwhat are the options\b", r"\bpros and cons\b", r"\badvantages? and disadvantages\b",
        r"\balternatives\b", r"\bdifferent approaches\b", r"\bcompare\b", r"\bversus\b",
        r"\btrade[- ]?offs?\b", r"\bbenefits? and risks\b", r"\bstrengths? and weaknesses\b"
    ]

    CONTRADICTION_KEYWORDS_EN = [
        r"\bbut\b", r"\bhowever\b", r"\balthough\b", r"\bwhereas\b", r"\bwhile\b",
        r"\bon the other hand\b", r"\bconversely\b", r"\bin contrast\b",
        r"\bdespite\b", r"\bnevertheless\b", r"\byet\b", r"\bstill\b"
    ]

    SUBQUESTION_PATTERNS_EN = [
        r"\?",  # Question marks
        r"\bwhat\b|\bwhy\b|\bwhen\b|\bwhere\b|\bwhich\b|\bwho\b|\bhow\b",
    ]

    # Complexity indicator patterns - Czech
    # Note: Using word stems with optional suffixes for better matching
    MULTI_STEP_KEYWORDS_CS = [
        r"\bjak bys?\b", r"\bco kdyby\b",
        r"\banalyz(?:uj|oval|uje|ovat)\b",  # analyzuj, analyzoval, analyzuje, analyzovat
        r"\bporovn(?:ej|ávej|ávat|al)\b",   # porovnej, porovnávej, porovnávat, porovnal
        r"\bzhodnoť\b", r"\bvyhodnoť\b",
        r"\bvysvětli\b", r"\bvysvětlit\b",
        r"\bsystematicky\b", r"\bdetailně\b",
        r"\bpopi(?:š|sat)\b",  # popiš, popsat
        r"\bnavrh(?:ni|nout|uj)\b",  # navrhni, navrhnout, navrhuj
        r"\bzvaž\b", r"\buvažovat\b",
        r"\bprozkoumej\b", r"\bzkoumej\b", r"\bzkoumat\b",
        r"\bposuď\b", r"\bposuzovat\b",
        r"\bstrategi(?:e|í)\b", r"\bmetodik(?:a|y)\b",
        r"\bpřístup\b", r"\brámec\b",
        r"\bjakým způsobem\b", r"\bv jakém kontextu\b", r"\bjaké faktory\b"
    ]

    ALTERNATIVES_KEYWORDS_CS = [
        r"\bmožnost(?:i|í)\b", r"\bpřístup(?:y|ů)\b", r"\bmetod(?:y|a)\b", r"\bzpůsob(?:y|ů)\b",
        r"\balternativ(?:y|a)\b",
        r"\bvýhod(?:y|a)\b.*\bnevýhod(?:y|a)\b",  # výhody a nevýhody (order independent)
        r"\bklady\b.*\bzápory\b",
        r"\bpro a proti\b", r"\bplusy a mínusy\b", r"\bvariant(?:y|a)\b",
        r"\bmožná řešení\b", r"\bdostupné možnosti\b"
    ]

    CONTRADICTION_KEYWORDS_CS = [
        r"\bkompromis\b", r"\bale\b", r"\bvšak\b", r"\bna druhé straně\b",
        r"\bna jednu stranu\b", r"\bzároveň\b", r"\bpřesto\b", r"\bačkoli\b",
        r"\bi když\b", r"\bnavzdory\b", r"\boproti tomu\b", r"\bnaproti tomu\b",
        r"\bnaopak\b", r"\bnicméně\b", r"\bs tím, že\b"
    ]

    SUBQUESTION_PATTERNS_CS = [
        r"\?",  # Question marks
        r"\bco\b|\bjak\b|\bproč\b|\bkdy\b|\bkde\b|\bkdo\b|\bčím\b|\bčemu\b|\bčí\b",
        r"\bjaký\b|\bjaká\b|\bjaké\b|\bjací\b|\bjakou\b|\bjakého\b|\bjakému\b",
        r"\bkolik\b|\bkde\b|\bkam\b|\bkudy\b|\bkým\b|\bkomu\b|\bkoho\b|\bčeho\b|\bčem\b"
    ]

    # Czech language boost settings (ToT activation enhancement)
    CZECH_BOOST_MULTIPLIER = 1.75  # +75% boost for Czech pattern matches
    MIN_CZECH_CHARS_THRESHOLD = 1  # Detect Czech by diacritics

    # Language-specific thresholds (tot_threshold, hybrid_threshold)
    THRESHOLDS = {
        'en': (0.70, 0.45),  # Standard English thresholds
        'cs': (0.60, 0.35),  # Lower thresholds for Czech
    }

    def __init__(self, config: Optional[TotConfig] = None):
        """
        Initialize ToT integration layer.

        Args:
            config: ToT configuration. Uses defaults if not provided.
        """
        self.config = config or TotConfig()
        self._tot_orchestrator: Optional[Any] = None
        self._last_memory_check: float = 0.0
        self._memory_check_interval: float = 5.0  # seconds

        logger.info("TotIntegrationLayer initialized (v1.1.0 - Czech language support)")

    def _detect_language(self, query: str) -> str:
        """
        Detect query language (en/cs).

        Args:
            query: The research query

        Returns:
            Language code: 'cs' for Czech, 'en' for English (default)
        """
        query_lower = query.lower()

        # Count Czech-specific characters
        czech_chars = sum(1 for c in query_lower if c in 'áčďéěíňóřšťúůýž')

        # Count common Czech words (expanded list)
        czech_words = [
            'jak', 'co', 'proč', 'kde', 'kdo', 'pro', 's', 'jsou', 'bude', 'tím',
            'bys', 'bych', 'bychom', 'byste', 'aby', 'když', 'protože', 'takže',
            'tento', 'tato', 'toto', 'tito', 'tohle', 'tomto', 'nějaký', 'nějaká',
            'jestli', 'nebo', 'ano', 'ne', 'jen', 'ještě', 'už', 'taky', 'také',
            'moc', 'velmi', 'trochu', 'hodně', 'málo', 'každý', 'všechny', 'nic',
            'všechno', 'něco', 'někdo', 'nikdo', 'všichni', 'žádný', 'další',
            'jiný', 'stejný', 'nový', 'starý', 'dobrý', 'špatný', 'velký', 'malý'
        ]
        words_lower = query_lower.split()
        czech_word_count = sum(1 for w in words_lower if w.strip('.,!?;:') in czech_words)

        # Check for Czech-specific patterns
        czech_patterns = [
            r'\bjak\s+(?:by|bys|bych|bychom|byste)\b',  # jak by, jak bys
            r'\bco\s+(?:je|to|to je)\b',  # co je, co to
            r'\bproč\s+(?:je|to|to je)\b',  # proč je
            r'\b[áčďéěíňóřšťúůýž]',  # any word starting with Czech char
        ]
        czech_pattern_matches = sum(
            1 for pattern in czech_patterns
            if re.search(pattern, query_lower, re.UNICODE)
        )

        # If we have Czech chars, Czech words, or Czech patterns, it's Czech
        if czech_chars >= 1 or czech_word_count >= 1 or czech_pattern_matches >= 1:
            return 'cs'
        return 'en'

    def _get_memory_usage_mb(self) -> float:
        """Get current memory usage in MB."""
        try:
            import psutil
            process = psutil.Process(os.getpid())
            return process.memory_info().rss / (1024 * 1024)
        except ImportError:
            return 0.0

    def _check_memory_pressure(self) -> Tuple[bool, float]:
        """
        Check if system is under memory pressure.

        Returns:
            Tuple of (is_under_pressure, current_memory_mb)
        """
        current_memory = self._get_memory_usage_mb()
        is_under_pressure = current_memory > self.config.memory_limit_mb

        if is_under_pressure:
            logger.warning(f"Memory pressure detected: {current_memory:.1f}MB > "
                          f"{self.config.memory_limit_mb:.1f}MB limit")

        return is_under_pressure, current_memory

    def _force_gc_if_needed(self):
        """Force garbage collection if memory pressure detected."""
        is_under_pressure, current_memory = self._check_memory_pressure()

        if is_under_pressure or current_memory > self.config.memory_limit_mb * 0.8:
            logger.info(f"Forcing garbage collection (memory: {current_memory:.1f}MB)")
            gc.collect()

            # Clear MLX cache if available
            try:
                import mlx.core as mx
                mx.eval([])
                mx.clear_cache()
                logger.debug("MLX cache cleared")
            except ImportError:
                pass

    def _get_thresholds(self, lang: str) -> Tuple[float, float]:
        """
        Get language-specific thresholds for ToT activation.

        Args:
            lang: Language code ('en' or 'cs')

        Returns:
            Tuple of (tot_threshold, hybrid_threshold)
        """
        return self.THRESHOLDS.get(lang, self.THRESHOLDS['en'])

    def should_activate_tot(self, query: str, context: Optional[Dict[str, Any]] = None
                           ) -> Tuple[bool, float]:
        """
        Determine if ToT should be activated for this query.
        Uses language-specific thresholds for better Czech support.

        Args:
            query: The research query to analyze
            context: Optional additional context

        Returns:
            Tuple of (should_use_tot, confidence_score)
        """
        context = context or {}

        # Check if ToT is enabled
        if not self.config.enable_tot_autonomous:
            logger.debug("ToT autonomous activation disabled")
            return False, 0.0

        # Check memory pressure
        is_under_pressure, memory_mb = self._check_memory_pressure()
        if is_under_pressure:
            logger.warning(f"ToT activation skipped due to memory pressure "
                          f"({memory_mb:.1f}MB)")
            return False, 0.0

        # Analyze complexity
        analysis = self.analyze_complexity(query)
        score = analysis.score

        # Get language-specific thresholds
        lang = self._detect_language(query)
        tot_threshold, hybrid_threshold = self._get_thresholds(lang)

        # Decision logic with language-specific thresholds
        if score >= tot_threshold:
            should_use = True
            confidence = min(1.0, score)
            logger.info(f"ToT activation recommended (score: {score:.2f}, "
                       f"threshold: {tot_threshold}, lang: {lang})")
        elif score >= hybrid_threshold:
            # Hybrid mode - use ToT with MoE
            should_use = True
            confidence = score
            logger.info(f"Hybrid ToT+MoE activation recommended (score: {score:.2f})")
        else:
            should_use = False
            confidence = 1.0 - score
            logger.debug(f"ToT not needed (score: {score:.2f} below threshold)")

        return should_use, confidence

    def analyze_complexity(self, query: str) -> ComplexityAnalysis:
        """
        Analyze query complexity for ToT suitability.
        Language-aware: supports English and Czech.

        Args:
            query: The research query to analyze

        Returns:
            ComplexityAnalysis with detailed metrics
        """
        # Detect language for language-aware pattern matching
        lang = self._detect_language(query)
        query_lower = query.lower()
        words = query_lower.split()
        word_count = len(words)

        indicators: Dict[str, float] = {}

        # Select patterns based on detected language
        multi_step_patterns = (
            self.MULTI_STEP_KEYWORDS_CS if lang == 'cs' else self.MULTI_STEP_KEYWORDS_EN
        )
        subquestion_patterns = (
            self.SUBQUESTION_PATTERNS_CS if lang == 'cs' else self.SUBQUESTION_PATTERNS_EN
        )
        alternatives_patterns = (
            self.ALTERNATIVES_KEYWORDS_CS if lang == 'cs' else self.ALTERNATIVES_KEYWORDS_EN
        )
        contradiction_patterns = (
            self.CONTRADICTION_KEYWORDS_CS if lang == 'cs' else self.CONTRADICTION_KEYWORDS_EN
        )

        # Multi-step keywords detection (+0.35)
        multi_step_matches = sum(
            1 for pattern in multi_step_patterns
            if re.search(pattern, query_lower, re.UNICODE | re.IGNORECASE)
        )
        multi_step_score = min(0.35, multi_step_matches * 0.1)
        indicators["multi_step_keywords"] = multi_step_score

        # Multiple subquestions detection (+0.30)
        subquestion_count = len(re.findall(r'\?', query))
        wh_word_count = sum(
            1 for pattern in subquestion_patterns[1:]
            for _ in re.finditer(pattern, query_lower, re.UNICODE | re.IGNORECASE)
        )
        subquestion_score = min(0.30, (subquestion_count + wh_word_count) * 0.05)
        indicators["multiple_subquestions"] = subquestion_score

        # Alternatives detection (+0.25)
        alternatives_matches = sum(
            1 for pattern in alternatives_patterns
            if re.search(pattern, query_lower, re.UNICODE | re.IGNORECASE)
        )
        alternatives_score = min(0.25, alternatives_matches * 0.1)
        indicators["needs_alternatives"] = alternatives_score

        # Long query detection (+0.10)
        length_score = 0.10 if word_count > 30 else (word_count / 300)
        indicators["query_length"] = length_score

        # Contradictions/tradeoffs detection (+0.20)
        contradiction_matches = sum(
            1 for pattern in contradiction_patterns
            if re.search(pattern, query_lower, re.UNICODE | re.IGNORECASE)
        )
        contradiction_score = min(0.20, contradiction_matches * 0.05)
        indicators["contradictions_tradeoffs"] = contradiction_score

        # Apply Czech boost multiplier for Czech language queries
        if lang == 'cs':
            # Boost pattern scores for better ToT activation on Czech text
            if indicators["multi_step_keywords"] > 0:
                indicators["multi_step_keywords"] = min(0.35, indicators["multi_step_keywords"] * self.CZECH_BOOST_MULTIPLIER)
            if indicators["needs_alternatives"] > 0:
                indicators["needs_alternatives"] = min(0.25, indicators["needs_alternatives"] * self.CZECH_BOOST_MULTIPLIER)
            if indicators["contradictions_tradeoffs"] > 0:
                indicators["contradictions_tradeoffs"] = min(0.20, indicators["contradictions_tradeoffs"] * self.CZECH_BOOST_MULTIPLIER)
            logger.debug(f"🇨🇿 Czech boost applied: {self.CZECH_BOOST_MULTIPLIER}x")

        # Calculate total complexity score BEFORE adding metadata indicators
        total_score = self._calculate_complexity_score(indicators)

        # Add language indicator for debugging (not included in score calculation)
        indicators["detected_language"] = 1.0 if lang == 'cs' else 0.0

        # Determine if multi-step reasoning required (lowered thresholds for aggressive ToT)
        requires_multi_step = (
            indicators["multi_step_keywords"] >= 0.10 or
            indicators["multiple_subquestions"] >= 0.10 or
            indicators["needs_alternatives"] >= 0.05
        )

        # Estimate required depth (1-5)
        estimated_depth = self._estimate_depth(total_score, indicators)

        # ToT recommended if score exceeds language-specific threshold
        tot_threshold, _ = self._get_thresholds(lang)
        tot_recommended = total_score >= tot_threshold

        return ComplexityAnalysis(
            score=round(total_score, 3),
            requires_multi_step=requires_multi_step,
            estimated_depth=estimated_depth,
            tot_recommended=tot_recommended,
            indicators=indicators
        )

    def _calculate_complexity_score(self, indicators: Dict[str, float]) -> float:
        """
        Calculate overall complexity score from indicators.

        Args:
            indicators: Dict of indicator names to scores

        Returns:
            Complexity score between 0.0 and 1.0
        """
        # Base score from indicators
        base_score = sum(indicators.values())

        # Apply non-linear scaling for high complexity
        if base_score > 0.7:
            # Boost high complexity queries
            base_score = min(1.0, base_score * 1.1)

        # Cap at 1.0
        return min(1.0, max(0.0, base_score))

    def _estimate_depth(self, total_score: float, indicators: Dict[str, float]) -> int:
        """
        Estimate required ToT depth based on complexity.

        Args:
            total_score: Overall complexity score
            indicators: Individual indicator scores

        Returns:
            Estimated depth (1-5)
        """
        if total_score >= 0.9:
            return 5
        elif total_score >= 0.75:
            return 4
        elif total_score >= 0.6:
            return 3
        elif total_score >= 0.4:
            return 2
        else:
            return 1

    async def solve_problem(self, problem: str,
                           context: Optional[Dict[str, Any]] = None) -> TotResult:
        """
        Execute Tree of Thoughts reasoning on a problem.

        Args:
            problem: Problem description to solve
            context: Additional context for reasoning

        Returns:
            TotResult with solution and metadata
        """
        context = context or {}
        start_time = time.time()
        start_memory = self._get_memory_usage_mb()

        logger.info(f"Starting ToT reasoning for problem: {problem[:100]}...")

        # Check ToT availability
        if not _load_tot_components():
            logger.error("ToT components not available")
            return TotResult(
                solution=None,
                confidence_score=0.0,
                reasoning_trace=[],
                tree_statistics={},
                computation_time=0.0,
                iterations_performed=0,
                converged=False,
                backtracking_used=False,
                memory_usage_mb=start_memory,
                error="ToT components not available"
            )

        # Check memory before starting
        is_under_pressure, _ = self._check_memory_pressure()
        if is_under_pressure:
            self._force_gc_if_needed()

        try:
            # Initialize ToT orchestrator if needed
            if self._tot_orchestrator is None:
                self._tot_orchestrator = TotOrchestrator(
                    max_depth=self.config.tot_max_depth,
                    branching_factor=3,
                    use_llm=True,
                    enable_backtracking=self.config.tot_enable_backtracking
                )
                logger.debug("ToT orchestrator initialized")

            # Set timeout for ToT execution
            timeout = min(self.config.tot_max_time,
                         context.get('timeout', self.config.tot_max_time))

            # Execute ToT with timeout
            result = await asyncio.wait_for(
                self._tot_orchestrator.solve_problem(problem, context),
                timeout=timeout
            )

            # Calculate memory usage
            end_memory = self._get_memory_usage_mb()
            memory_used = end_memory - start_memory

            # Force GC after execution
            if self.config.enable_gc_between_phases:
                self._force_gc_if_needed()

            computation_time = time.time() - start_time

            logger.info(f"ToT reasoning completed in {computation_time:.2f}s "
                       f"(memory: {memory_used:.1f}MB)")

            return TotResult(
                solution=result.get('solution'),
                confidence_score=result.get('confidence_score', 0.0),
                reasoning_trace=result.get('reasoning_trace', []),
                tree_statistics=result.get('tree_statistics', {}),
                computation_time=computation_time,
                iterations_performed=result.get('iterations_performed', 0),
                converged=result.get('converged', False),
                backtracking_used=result.get('backtracking_used', False),
                memory_usage_mb=memory_used
            )

        except asyncio.TimeoutError:
            logger.warning(f"ToT reasoning timed out after {timeout}s")
            computation_time = time.time() - start_time
            return TotResult(
                solution=None,
                confidence_score=0.0,
                reasoning_trace=[],
                tree_statistics={},
                computation_time=computation_time,
                iterations_performed=0,
                converged=False,
                backtracking_used=False,
                memory_usage_mb=self._get_memory_usage_mb() - start_memory,
                error=f"Timeout after {timeout}s"
            )

        except Exception as e:
            logger.error(f"ToT reasoning failed: {e}")
            computation_time = time.time() - start_time
            return TotResult(
                solution=None,
                confidence_score=0.0,
                reasoning_trace=[],
                tree_statistics={},
                computation_time=computation_time,
                iterations_performed=0,
                converged=False,
                backtracking_used=False,
                memory_usage_mb=self._get_memory_usage_mb() - start_memory,
                error=str(e)
            )

    async def solve_hybrid_tot_moe(self, problem: str,
                                    context: Optional[Dict[str, Any]] = None) -> TotResult:
        """
        Execute hybrid ToT + MoE reasoning for medium complexity problems.

        Uses MoE router for initial path selection, then ToT for deep exploration.

        Args:
            problem: Problem description to solve
            context: Additional context for reasoning

        Returns:
            TotResult with solution and metadata
        """
        context = context or {}
        start_time = time.time()
        start_memory = self._get_memory_usage_mb()

        logger.info(f"Starting Hybrid ToT+MoE reasoning for problem: {problem[:100]}...")

        # Check ToT availability
        if not _load_tot_components():
            logger.error("ToT components not available for hybrid mode")
            return TotResult(
                solution=None,
                confidence_score=0.0,
                reasoning_trace=[],
                tree_statistics={},
                computation_time=0.0,
                iterations_performed=0,
                converged=False,
                backtracking_used=False,
                memory_usage_mb=start_memory,
                error="ToT components not available"
            )

        try:
            # Initialize ToT with reduced depth for hybrid mode
            if self._tot_orchestrator is None:
                self._tot_orchestrator = TotOrchestrator(
                    max_depth=max(3, self.config.tot_max_depth - 2),  # Reduced depth
                    branching_factor=2,  # Reduced branching
                    use_llm=True,
                    enable_backtracking=self.config.tot_enable_backtracking
                )

            # Add hybrid mode flag to context
            context['hybrid_mode'] = True
            context['use_moe_pruning'] = True

            # Execute with shorter timeout for hybrid mode
            timeout = min(self.config.tot_max_time * 0.6,  # 60% of max time
                         context.get('timeout', self.config.tot_max_time * 0.6))

            result = await asyncio.wait_for(
                self._tot_orchestrator.solve_problem(problem, context),
                timeout=timeout
            )

            end_memory = self._get_memory_usage_mb()
            memory_used = end_memory - start_memory

            if self.config.enable_gc_between_phases:
                self._force_gc_if_needed()

            computation_time = time.time() - start_time

            logger.info(f"Hybrid ToT+MoE reasoning completed in {computation_time:.2f}s")

            return TotResult(
                solution=result.get('solution'),
                confidence_score=result.get('confidence_score', 0.0) * 0.95,  # Slight penalty
                reasoning_trace=result.get('reasoning_trace', []),
                tree_statistics=result.get('tree_statistics', {}),
                computation_time=computation_time,
                iterations_performed=result.get('iterations_performed', 0),
                converged=result.get('converged', False),
                backtracking_used=result.get('backtracking_used', False),
                memory_usage_mb=memory_used
            )

        except asyncio.TimeoutError:
            logger.warning(f"Hybrid ToT+MoE timed out")
            return TotResult(
                solution=None,
                confidence_score=0.0,
                reasoning_trace=[],
                tree_statistics={},
                computation_time=time.time() - start_time,
                iterations_performed=0,
                converged=False,
                backtracking_used=False,
                memory_usage_mb=self._get_memory_usage_mb() - start_memory,
                error="Hybrid mode timeout"
            )

        except Exception as e:
            logger.error(f"Hybrid ToT+MoE failed: {e}")
            return TotResult(
                solution=None,
                confidence_score=0.0,
                reasoning_trace=[],
                tree_statistics={},
                computation_time=time.time() - start_time,
                iterations_performed=0,
                converged=False,
                backtracking_used=False,
                memory_usage_mb=self._get_memory_usage_mb() - start_memory,
                error=str(e)
            )

    def get_capabilities(self) -> Dict[str, Any]:
        """Get ToT integration capabilities."""
        return {
            "name": "tot_integration_layer",
            "version": "1.0.0",
            "tot_available": _load_tot_components(),
            "config": {
                "complexity_threshold": self.config.tot_complexity_threshold,
                "hybrid_threshold": self.config.hybrid_complexity_threshold,
                "max_depth": self.config.tot_max_depth,
                "max_time": self.config.tot_max_time,
                "enable_backtracking": self.config.tot_enable_backtracking,
                "enable_mcts": self.config.tot_enable_mcts,
            },
            "memory_limit_mb": self.config.memory_limit_mb,
            "current_memory_mb": self._get_memory_usage_mb(),
        }

    async def health_check(self) -> bool:
        """Check if ToT integration is operational."""
        try:
            if not _load_tot_components():
                return False

            # Quick complexity analysis test
            test_analysis = self.analyze_complexity("What is 2+2?")
            return test_analysis.score >= 0.0

        except Exception as e:
            logger.error(f"ToT integration health check failed: {e}")
            return False


# Factory function
def create_tot_integration(config: Optional[Dict[str, Any]] = None) -> TotIntegrationLayer:
    """
    Create ToT integration layer with optional config override.

    Args:
        config: Optional configuration dict

    Returns:
        Configured TotIntegrationLayer
    """
    if config:
        tot_config = TotConfig(**config)
    else:
        tot_config = TotConfig()

    return TotIntegrationLayer(tot_config)
