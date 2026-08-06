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
import asyncio
import gc
import logging
import os
import re
import time
from dataclasses import dataclass
import msgspec
from typing import TYPE_CHECKING, Any
from .project_types import ComplexityAnalysis, ResearchResult
if TYPE_CHECKING:
    from .brain.research_hypothesis_engine import ResearchHypothesisEngine
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
        logger.warning(f'ToT components not available: {e}')
        TOT_AVAILABLE = False
        return False

class TotResult(msgspec.Struct, gc=False):
    """Result from Tree of Thoughts reasoning."""
    solution: str | None
    confidence_score: float
    reasoning_trace: list[dict[str, Any]]
    tree_statistics: dict[str, Any]
    computation_time: float
    iterations_performed: int
    converged: bool
    backtracking_used: bool
    memory_usage_mb: float
    error: str | None = None

    def to_research_result(self, query: str) -> ResearchResult:
        """Convert ToT result to standard ResearchResult."""
        return ResearchResult(success=self.solution is not None and self.error is None, query=query, mode='tree_of_thoughts', final_answer=self.solution or 'No solution found', sources=[], knowledge_graph={}, execution_history=self.reasoning_trace, agent_results=[], statistics={'confidence': self.confidence_score, 'computation_time': self.computation_time, 'iterations': self.iterations_performed, 'converged': self.converged, 'backtracking_used': self.backtracking_used, 'memory_usage_mb': self.memory_usage_mb, 'tree_stats': self.tree_statistics}, metadata={'reasoning_mode': 'tree_of_thoughts', 'tree_depth': self.tree_statistics.get('max_depth', 0), 'exploration_rate': self.tree_statistics.get('exploration_rate', 0.0)})

class TotConfig(msgspec.Struct, frozen=True, gc=False):
    """Configuration for Tree of Thoughts integration."""
    enable_tot_autonomous: bool = True
    tot_complexity_threshold: float = 0.7
    tot_max_depth: int = 5
    tot_max_time: float = 120.0
    tot_enable_backtracking: bool = True
    tot_enable_mcts: bool = True
    hybrid_complexity_threshold: float = 0.45
    memory_limit_mb: float = 6000.0
    enable_gc_between_phases: bool = True

    @classmethod
    def from_env(cls) -> TotConfig:
        """Create TotConfig from environment variables.

        Mirrors the dominant project pattern (os.environ.get) for configuration.
        Falls back to dataclass defaults when env vars are unset.

        Returns:
            TotConfig with values from environment or defaults.
        """
        return cls(enable_tot_autonomous=_env_bool('HLEDAC_TOT_AUTONOMOUS', True), tot_complexity_threshold=_env_float('HLEDAC_TOT_COMPLEXITY_THRESHOLD', 0.7), tot_max_depth=_env_int('HLEDAC_TOT_MAX_DEPTH', 5), tot_max_time=_env_float('HLEDAC_TOT_MAX_TIME', 120.0), tot_enable_backtracking=_env_bool('HLEDAC_TOT_BACKTRACKING', True), tot_enable_mcts=_env_bool('HLEDAC_TOT_MCTS', True), hybrid_complexity_threshold=_env_float('HLEDAC_TOT_HYBRID_THRESHOLD', 0.45), memory_limit_mb=_env_float('HLEDAC_TOT_MEMORY_LIMIT_MB', 6000.0), enable_gc_between_phases=_env_bool('HLEDAC_TOT_GC_BETWEEN_PHASES', True))

def _env_bool(name: str, default: bool) -> bool:
    """Read bool from environment variable."""
    val = os.environ.get(name, '').strip().lower()
    if not val:
        return default
    return val in ('1', 'true', 'yes', 'on')

def _env_float(name: str, default: float) -> float:
    """Read float from environment variable."""
    val = os.environ.get(name, '').strip()
    if not val:
        return default
    try:
        return float(val)
    except ValueError:
        return default

def _env_int(name: str, default: int) -> int:
    """Read int from environment variable."""
    val = os.environ.get(name, '').strip()
    if not val:
        return default
    try:
        return int(val)
    except ValueError:
        return default

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
    MULTI_STEP_KEYWORDS_EN = ['\\bhow would\\b', '\\banalyze\\b', '\\bcompare\\b', '\\bevaluate\\b', '\\bassess\\b', '\\bexplain\\b', '\\bdetermine\\b', '\\binvestigate\\b', '\\bexplore\\b', '\\bexamine\\b', '\\bwhat if\\b', '\\bconsider\\b', '\\bdiscuss\\b', '\\bjustify\\b', '\\brecommend\\b', '\\bstrategize\\b', '\\bplan\\b', '\\bapproach\\b', '\\bmethodology\\b', '\\bframework\\b']
    ALTERNATIVES_KEYWORDS_EN = ['\\bwhat are the options\\b', '\\bpros and cons\\b', '\\badvantages? and disadvantages\\b', '\\balternatives\\b', '\\bdifferent approaches\\b', '\\bcompare\\b', '\\bversus\\b', '\\btrade[- ]?offs?\\b', '\\bbenefits? and risks\\b', '\\bstrengths? and weaknesses\\b']
    CONTRADICTION_KEYWORDS_EN = ['\\bbut\\b', '\\bhowever\\b', '\\balthough\\b', '\\bwhereas\\b', '\\bwhile\\b', '\\bon the other hand\\b', '\\bconversely\\b', '\\bin contrast\\b', '\\bdespite\\b', '\\bnevertheless\\b', '\\byet\\b', '\\bstill\\b']
    SUBQUESTION_PATTERNS_EN = ['\\?', '\\bwhat\\b|\\bwhy\\b|\\bwhen\\b|\\bwhere\\b|\\bwhich\\b|\\bwho\\b|\\bhow\\b']
    MULTI_STEP_KEYWORDS_CS = ['\\bjak bys?\\b', '\\bco kdyby\\b', '\\banalyz(?:uj|oval|uje|ovat)\\b', '\\bporovn(?:ej|ávej|ávat|al)\\b', '\\bzhodnoť\\b', '\\bvyhodnoť\\b', '\\bvysvětli\\b', '\\bvysvětlit\\b', '\\bsystematicky\\b', '\\bdetailně\\b', '\\bpopi(?:š|sat)\\b', '\\bnavrh(?:ni|nout|uj)\\b', '\\bzvaž\\b', '\\buvažovat\\b', '\\bprozkoumej\\b', '\\bzkoumej\\b', '\\bzkoumat\\b', '\\bposuď\\b', '\\bposuzovat\\b', '\\bstrategi(?:e|í)\\b', '\\bmetodik(?:a|y)\\b', '\\bpřístup\\b', '\\brámec\\b', '\\bjakým způsobem\\b', '\\bv jakém kontextu\\b', '\\bjaké faktory\\b']
    ALTERNATIVES_KEYWORDS_CS = ['\\bmožnost(?:i|í)\\b', '\\bpřístup(?:y|ů)\\b', '\\bmetod(?:y|a)\\b', '\\bzpůsob(?:y|ů)\\b', '\\balternativ(?:y|a)\\b', '\\bvýhod(?:y|a)\\b.*\\bnevýhod(?:y|a)\\b', '\\bklady\\b.*\\bzápory\\b', '\\bpro a proti\\b', '\\bplusy a mínusy\\b', '\\bvariant(?:y|a)\\b', '\\bmožná řešení\\b', '\\bdostupné možnosti\\b']
    CONTRADICTION_KEYWORDS_CS = ['\\bkompromis\\b', '\\bale\\b', '\\bvšak\\b', '\\bna druhé straně\\b', '\\bna jednu stranu\\b', '\\bzároveň\\b', '\\bpřesto\\b', '\\bačkoli\\b', '\\bi když\\b', '\\bnavzdory\\b', '\\boproti tomu\\b', '\\bnaproti tomu\\b', '\\bnaopak\\b', '\\bnicméně\\b', '\\bs tím, že\\b']
    SUBQUESTION_PATTERNS_CS = ['\\?', '\\bco\\b|\\bjak\\b|\\bproč\\b|\\bkdy\\b|\\bkde\\b|\\bkdo\\b|\\bčím\\b|\\bčemu\\b|\\bčí\\b', '\\bjaký\\b|\\bjaká\\b|\\bjaké\\b|\\bjací\\b|\\bjakou\\b|\\bjakého\\b|\\bjakému\\b', '\\bkolik\\b|\\bkde\\b|\\bkam\\b|\\bkudy\\b|\\bkým\\b|\\bkomu\\b|\\bkoho\\b|\\bčeho\\b|\\bčem\\b']
    CZECH_BOOST_MULTIPLIER = 1.75
    MIN_CZECH_CHARS_THRESHOLD = 1
    THRESHOLDS = {'en': (0.7, 0.45), 'cs': (0.6, 0.35)}
    __slots__ = tuple(('_hypothesis_engine', '_last_memory_check', '_memory_check_interval', '_pending_epistemic_branches', '_tot_orchestrator', 'config'))

    def __init__(self, config: TotConfig | None=None):
        """
        Initialize ToT integration layer.

        Args:
            config: ToT configuration. Reads from environment if not provided.
        """
        self.config = config or TotConfig.from_env()
        self._tot_orchestrator: Any | None = None
        self._hypothesis_engine: ResearchHypothesisEngine | None = None
        self._pending_epistemic_branches: list[str] = []
        self._last_memory_check: float = 0.0
        self._memory_check_interval: float = 5.0
        logger.info('TotIntegrationLayer initialized (v1.1.0 - Czech language support)')

    def attach_hypothesis_engine(self, engine: ResearchHypothesisEngine) -> None:
        """Store ref for use in should_activate_tot. No validation."""
        self._hypothesis_engine = engine

    def get_epistemic_branches(self) -> list[str]:
        """Return HypothesisEngine-suggested query branches (max 3, or [])."""
        return list(getattr(self, '_pending_epistemic_branches', []))

    def _detect_language(self, query: str) -> str:
        """
        Detect query language (en/cs).

        Args:
            query: The research query

        Returns:
            Language code: 'cs' for Czech, 'en' for English (default)
        """
        query_lower = query.lower()
        czech_chars = sum((1 for c in query_lower if c in 'áčďéěíňóřšťúůýž'))
        czech_words = ['jak', 'co', 'proč', 'kde', 'kdo', 'pro', 's', 'jsou', 'bude', 'tím', 'bys', 'bych', 'bychom', 'byste', 'aby', 'když', 'protože', 'takže', 'tento', 'tato', 'toto', 'tito', 'tohle', 'tomto', 'nějaký', 'nějaká', 'jestli', 'nebo', 'ano', 'ne', 'jen', 'ještě', 'už', 'taky', 'také', 'moc', 'velmi', 'trochu', 'hodně', 'málo', 'každý', 'všechny', 'nic', 'všechno', 'něco', 'někdo', 'nikdo', 'všichni', 'žádný', 'další', 'jiný', 'stejný', 'nový', 'starý', 'dobrý', 'špatný', 'velký', 'malý']
        words_lower = query_lower.split()
        czech_word_count = sum((1 for w in words_lower if w.strip('.,!?;:') in czech_words))
        czech_patterns = ['\\bjak\\s+(?:by|bys|bych|bychom|byste)\\b', '\\bco\\s+(?:je|to|to je)\\b', '\\bproč\\s+(?:je|to|to je)\\b', '\\b[áčďéěíňóřšťúůýž]']
        czech_pattern_matches = sum((1 for pattern in czech_patterns if re.search(pattern, query_lower, re.UNICODE)))
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

    def _check_memory_pressure(self) -> tuple[bool, float]:
        """
        Check if system is under memory pressure.

        Returns:
            Tuple of (is_under_pressure, current_memory_mb)
        """
        current_memory = self._get_memory_usage_mb()
        is_under_pressure = current_memory > self.config.memory_limit_mb
        if is_under_pressure:
            logger.warning(f'Memory pressure detected: {current_memory:.1f}MB > {self.config.memory_limit_mb:.1f}MB limit')
        return (is_under_pressure, current_memory)

    def _force_gc_if_needed(self):
        """Force garbage collection if memory pressure detected."""
        is_under_pressure, current_memory = self._check_memory_pressure()
        if is_under_pressure or current_memory > self.config.memory_limit_mb * 0.8:
            logger.info(f'Forcing garbage collection (memory: {current_memory:.1f}MB)')
            import gc as gc_module
            gc_module.collect()
            try:
                import mlx.core as mx
                mx.eval([])
                mx.clear_cache()
                logger.debug('MLX cache cleared')
            except ImportError:
                pass

    def _get_thresholds(self, lang: str) -> tuple[float, float]:
        """
        Get language-specific thresholds for ToT activation.

        Args:
            lang: Language code ('en' or 'cs')

        Returns:
            Tuple of (tot_threshold, hybrid_threshold)
        """
        return self.THRESHOLDS.get(lang, self.THRESHOLDS['en'])

    def should_activate_tot(self, query: str, context: dict[str, Any] | None=None) -> tuple[bool, float]:
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
        if not self.config.enable_tot_autonomous:
            logger.debug('ToT autonomous activation disabled')
            return (False, 0.0)
        is_under_pressure, memory_mb = self._check_memory_pressure()
        if is_under_pressure:
            logger.warning(f'ToT activation skipped due to memory pressure ({memory_mb:.1f}MB)')
            return (False, 0.0)
        analysis = self.analyze_complexity(query)
        score = analysis.score
        lang = self._detect_language(query)
        tot_threshold, hybrid_threshold = self._get_thresholds(lang)
        if score >= tot_threshold:
            should_use = True
            confidence = min(1.0, score)
            logger.info(f'ToT activation recommended (score: {score:.2f}, threshold: {tot_threshold}, lang: {lang})')
        elif score >= hybrid_threshold:
            should_use = True
            confidence = score
            logger.info(f'Hybrid ToT+MoE activation recommended (score: {score:.2f})')
        else:
            should_use = False
            confidence = 1.0 - score
            logger.debug(f'ToT not needed (score: {score:.2f} below threshold)')
        if getattr(self, '_hypothesis_engine', None) is not None:
            try:
                next_queries = self._hypothesis_engine.suggest_next_queries(findings=[query], context={}, max_queries=5)
                if next_queries and next_queries:
                    score = min(1.0, score + 0.2)
                    self._pending_epistemic_branches = [q['query'] for q in next_queries[:3]]
            except Exception:
                pass
        return (should_use, confidence)

    def analyze_complexity(self, query: str) -> ComplexityAnalysis:
        """
        Analyze query complexity for ToT suitability.
        Language-aware: supports English and Czech.

        Args:
            query: The research query to analyze

        Returns:
            ComplexityAnalysis with detailed metrics
        """
        lang = self._detect_language(query)
        query_lower = query.lower()
        words = query_lower.split()
        word_count = len(words)
        indicators: dict[str, float] = {}
        multi_step_patterns = self.MULTI_STEP_KEYWORDS_CS if lang == 'cs' else self.MULTI_STEP_KEYWORDS_EN
        subquestion_patterns = self.SUBQUESTION_PATTERNS_CS if lang == 'cs' else self.SUBQUESTION_PATTERNS_EN
        alternatives_patterns = self.ALTERNATIVES_KEYWORDS_CS if lang == 'cs' else self.ALTERNATIVES_KEYWORDS_EN
        contradiction_patterns = self.CONTRADICTION_KEYWORDS_CS if lang == 'cs' else self.CONTRADICTION_KEYWORDS_EN
        multi_step_matches = sum((1 for pattern in multi_step_patterns if re.search(pattern, query_lower, re.UNICODE | re.IGNORECASE)))
        multi_step_score = min(0.35, multi_step_matches * 0.1)
        indicators['multi_step_keywords'] = multi_step_score
        subquestion_count = len(re.findall('\\?', query))
        wh_word_count = sum((1 for pattern in subquestion_patterns[1:] for _ in re.finditer(pattern, query_lower, re.UNICODE | re.IGNORECASE)))
        subquestion_score = min(0.3, (subquestion_count + wh_word_count) * 0.05)
        indicators['multiple_subquestions'] = subquestion_score
        alternatives_matches = sum((1 for pattern in alternatives_patterns if re.search(pattern, query_lower, re.UNICODE | re.IGNORECASE)))
        alternatives_score = min(0.25, alternatives_matches * 0.1)
        indicators['needs_alternatives'] = alternatives_score
        length_score = 0.1 if word_count > 30 else word_count / 300
        indicators['query_length'] = length_score
        contradiction_matches = sum((1 for pattern in contradiction_patterns if re.search(pattern, query_lower, re.UNICODE | re.IGNORECASE)))
        contradiction_score = min(0.2, contradiction_matches * 0.05)
        indicators['contradictions_tradeoffs'] = contradiction_score
        if lang == 'cs':
            if indicators['multi_step_keywords'] > 0:
                indicators['multi_step_keywords'] = min(0.35, indicators['multi_step_keywords'] * self.CZECH_BOOST_MULTIPLIER)
            if indicators['needs_alternatives'] > 0:
                indicators['needs_alternatives'] = min(0.25, indicators['needs_alternatives'] * self.CZECH_BOOST_MULTIPLIER)
            if indicators['contradictions_tradeoffs'] > 0:
                indicators['contradictions_tradeoffs'] = min(0.2, indicators['contradictions_tradeoffs'] * self.CZECH_BOOST_MULTIPLIER)
            logger.debug(f'🇨🇿 Czech boost applied: {self.CZECH_BOOST_MULTIPLIER}x')
        total_score = self._calculate_complexity_score(indicators)
        indicators['detected_language'] = 1.0 if lang == 'cs' else 0.0
        requires_multi_step = indicators['multi_step_keywords'] >= 0.1 or indicators['multiple_subquestions'] >= 0.1 or indicators['needs_alternatives'] >= 0.05
        estimated_depth = self._estimate_depth(total_score, indicators)
        tot_threshold, _ = self._get_thresholds(lang)
        tot_recommended = total_score >= tot_threshold
        return ComplexityAnalysis(score=round(total_score, 3), requires_multi_step=requires_multi_step, estimated_depth=estimated_depth, tot_recommended=tot_recommended, indicators=indicators)

    def _calculate_complexity_score(self, indicators: dict[str, float]) -> float:
        """
        Calculate overall complexity score from indicators.

        Args:
            indicators: Dict of indicator names to scores

        Returns:
            Complexity score between 0.0 and 1.0
        """
        base_score = sum(indicators.values())
        if base_score > 0.7:
            base_score = min(1.0, base_score * 1.1)
        return min(1.0, max(0.0, base_score))

    def _estimate_depth(self, total_score: float, indicators: dict[str, float]) -> int:
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

    async def solve_problem(self, problem: str, context: dict[str, Any] | None=None) -> TotResult:
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
        logger.info(f'Starting ToT reasoning for problem: {problem[:100]}...')
        if not _load_tot_components():
            logger.error('ToT components not available')
            return TotResult(solution=None, confidence_score=0.0, reasoning_trace=[], tree_statistics={}, computation_time=0.0, iterations_performed=0, converged=False, backtracking_used=False, memory_usage_mb=start_memory, error='ToT components not available')
        is_under_pressure, _ = self._check_memory_pressure()
        if is_under_pressure:
            self._force_gc_if_needed()
        try:
            if self._tot_orchestrator is None:
                self._tot_orchestrator = TotOrchestrator(max_depth=self.config.tot_max_depth, branching_factor=3, use_llm=True, enable_backtracking=self.config.tot_enable_backtracking)
                logger.debug('ToT orchestrator initialized')
            timeout = min(self.config.tot_max_time, context.get('timeout', self.config.tot_max_time))
            async with asyncio.timeout(timeout):
                result = await self._tot_orchestrator.solve_problem(problem, context)
            end_memory = self._get_memory_usage_mb()
            memory_used = end_memory - start_memory
            if self.config.enable_gc_between_phases:
                self._force_gc_if_needed()
            computation_time = time.time() - start_time
            logger.info(f'ToT reasoning completed in {computation_time:.2f}s (memory: {memory_used:.1f}MB)')
            return TotResult(solution=result.get('solution'), confidence_score=result.get('confidence_score', 0.0), reasoning_trace=result.get('reasoning_trace', []), tree_statistics=result.get('tree_statistics', {}), computation_time=computation_time, iterations_performed=result.get('iterations_performed', 0), converged=result.get('converged', False), backtracking_used=result.get('backtracking_used', False), memory_usage_mb=memory_used)
        except TimeoutError:
            logger.warning(f'ToT reasoning timed out after {timeout}s')
            computation_time = time.time() - start_time
            return TotResult(solution=None, confidence_score=0.0, reasoning_trace=[], tree_statistics={}, computation_time=computation_time, iterations_performed=0, converged=False, backtracking_used=False, memory_usage_mb=self._get_memory_usage_mb() - start_memory, error=f'Timeout after {timeout}s')
        except Exception as e:
            logger.error(f'ToT reasoning failed: {e}')
            computation_time = time.time() - start_time
            return TotResult(solution=None, confidence_score=0.0, reasoning_trace=[], tree_statistics={}, computation_time=computation_time, iterations_performed=0, converged=False, backtracking_used=False, memory_usage_mb=self._get_memory_usage_mb() - start_memory, error=str(e))

    async def solve_hybrid_tot_moe(self, problem: str, context: dict[str, Any] | None=None) -> TotResult:
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
        logger.info(f'Starting Hybrid ToT+MoE reasoning for problem: {problem[:100]}...')
        if not _load_tot_components():
            logger.error('ToT components not available for hybrid mode')
            return TotResult(solution=None, confidence_score=0.0, reasoning_trace=[], tree_statistics={}, computation_time=0.0, iterations_performed=0, converged=False, backtracking_used=False, memory_usage_mb=start_memory, error='ToT components not available')
        try:
            if self._tot_orchestrator is None:
                self._tot_orchestrator = TotOrchestrator(max_depth=max(3, self.config.tot_max_depth - 2), branching_factor=2, use_llm=True, enable_backtracking=self.config.tot_enable_backtracking)
            context['hybrid_mode'] = True
            context['use_moe_pruning'] = True
            timeout = min(self.config.tot_max_time * 0.6, context.get('timeout', self.config.tot_max_time * 0.6))
            async with asyncio.timeout(timeout):
                result = await self._tot_orchestrator.solve_problem(problem, context)
            end_memory = self._get_memory_usage_mb()
            memory_used = end_memory - start_memory
            if self.config.enable_gc_between_phases:
                self._force_gc_if_needed()
            computation_time = time.time() - start_time
            logger.info(f'Hybrid ToT+MoE reasoning completed in {computation_time:.2f}s')
            return TotResult(solution=result.get('solution'), confidence_score=result.get('confidence_score', 0.0) * 0.95, reasoning_trace=result.get('reasoning_trace', []), tree_statistics=result.get('tree_statistics', {}), computation_time=computation_time, iterations_performed=result.get('iterations_performed', 0), converged=result.get('converged', False), backtracking_used=result.get('backtracking_used', False), memory_usage_mb=memory_used)
        except TimeoutError:
            logger.warning('Hybrid ToT+MoE timed out')
            return TotResult(solution=None, confidence_score=0.0, reasoning_trace=[], tree_statistics={}, computation_time=time.time() - start_time, iterations_performed=0, converged=False, backtracking_used=False, memory_usage_mb=self._get_memory_usage_mb() - start_memory, error='Hybrid mode timeout')
        except Exception as e:
            logger.error(f'Hybrid ToT+MoE failed: {e}')
            return TotResult(solution=None, confidence_score=0.0, reasoning_trace=[], tree_statistics={}, computation_time=time.time() - start_time, iterations_performed=0, converged=False, backtracking_used=False, memory_usage_mb=self._get_memory_usage_mb() - start_memory, error=str(e))

    async def solve_with_tot(self, prompt: str, timeout: float=0.0) -> str:
        """
        P12: Evaluate if prompt is complex and run ToT if needed.

        Analyzes prompt complexity using should_activate_tot(), and if
        complexity exceeds threshold, runs ToT solver. Otherwise returns
        empty string.

        Args:
            prompt: The hypothesis/prompt to evaluate
            timeout: Optional per-hypothesis timeout in seconds. If > 0, overrides
                     the config's tot_max_time for this specific call.

        Returns:
            ToT solution string, or empty string if ToT not needed/not available
        """
        if not prompt or not prompt.strip():
            return ''
        should_use, confidence = self.should_activate_tot(prompt)
        if not should_use:
            return ''
        if timeout > 0:
            try:
                async with asyncio.timeout(timeout):
                    result = await self.solve_problem(prompt)
            except TimeoutError:
                logger.warning(f'solve_with_tot timed out after {timeout}s')
                return ''
        else:
            result = await self.solve_problem(prompt)
        if result.solution:
            return result.solution
        return ''

    def get_capabilities(self) -> dict[str, Any]:
        """Get ToT integration capabilities."""
        return {'name': 'tot_integration_layer', 'version': '1.0.0', 'tot_available': _load_tot_components(), 'config': {'complexity_threshold': self.config.tot_complexity_threshold, 'hybrid_threshold': self.config.hybrid_complexity_threshold, 'max_depth': self.config.tot_max_depth, 'max_time': self.config.tot_max_time, 'enable_backtracking': self.config.tot_enable_backtracking, 'enable_mcts': self.config.tot_enable_mcts}, 'memory_limit_mb': self.config.memory_limit_mb, 'current_memory_mb': self._get_memory_usage_mb()}

    async def health_check(self) -> bool:
        """Check if ToT integration is operational."""
        try:
            if not _load_tot_components():
                return False
            test_analysis = self.analyze_complexity('What is 2+2?')
            return test_analysis.score >= 0.0
        except Exception as e:
            logger.error(f'ToT integration health check failed: {e}')
            return False

def create_tot_integration(config: dict[str, Any] | None=None) -> TotIntegrationLayer:
    """
    Create ToT integration layer with optional config override.

    Args:
        config: Optional configuration dict. If None, reads from environment.

    Returns:
        Configured TotIntegrationLayer
    """
    if config:
        tot_config = TotConfig(**config)
    else:
        tot_config = TotConfig.from_env()
    return TotIntegrationLayer(tot_config)