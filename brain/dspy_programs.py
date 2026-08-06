"""
brain/dspy_programs.py
======================
DSPy-style prompt optimization for hypothesis engine.







Gate: HLEDAC_ENABLE_DSPY=1 (runtime inference-only, compiled programs)
Compilation: scripts/dspy_compile.py (offline, never during sprint)

DSPy optimization candidates from DSPY_OPTIMIZATION_MAP.md:
  - hypothesis:medium  (generate_hypotheses_async)
  - dark_query:medium  (generate_dark_surface_queries)
  - hypothesis_ranker   (future: rank_hypotheses_by_value)

Metric: _osint_metric — rewards JSON with 3+ fields, penalizes non-JSON.
Persistence: ~/.hledac/dspy/{name}.json (max 10 versions per task).
"""
import logging
from hledac.universal.utils.msgspec_json import encode as _msgspec_encode, decode as _msgspec_decode
import os
from pathlib import Path
from typing import Any
logger = logging.getLogger(__name__)
_DSPY_AVAILABLE = False
try:
    import dspy
    _DSPY_AVAILABLE = True
except ImportError:
    dspy = None
HLEDAC_ENABLE_DSPY = os.environ.get('HLEDAC_ENABLE_DSPY', '0') == '1'
_DSPY_DIR = Path.home() / '.hledac' / 'dspy'
_DSPY_DIR.mkdir(parents=True, exist_ok=True)

class DarkQuerySignature:
    """Signature for dark surface query generation."""
    if _DSPY_AVAILABLE:
        ioc_brief = dspy.InputField(desc='Brief summary of IOC findings from current sprint')
        available_transports = dspy.InputField(desc='Available transports: clearnet, tor, i2p, nym, stealth')
        max_queries = dspy.InputField(desc='Maximum number of dark queries to generate')
        dark_queries = dspy.OutputField(desc='JSON list of {type, query, priority, reasoning}. type ∈ {onion,ipfs,paste,i2p}. priority 0-1.')

class HypothesisGeneratorSignature:
    """Signature for hypothesis generation from OSINT context."""
    if _DSPY_AVAILABLE:
        research_query = dspy.InputField(desc='The core research query')
        rag_context = dspy.InputField(desc='RAG context from accumulated findings')
        graph_summary = dspy.InputField(desc='Cross-sprint graph summary (may be empty)')
        reward_context = dspy.InputField(desc='Reward context from bandit selection')
        existing_hypotheses = dspy.InputField(desc='Already explored hypotheses to avoid duplication')
        hypotheses = dspy.OutputField(desc='Numbered list of 5-10 hypotheses in Czech, each starting with digit')

class HypothesisRankerSignature:
    """Signature for ranking hypotheses by investigative value."""
    if _DSPY_AVAILABLE:
        hypotheses = dspy.InputField(desc='List of hypothesis strings to rank')
        sprint_context = dspy.InputField(desc='Current sprint context and goals')
        ranked = dspy.OutputField(desc='Hypotheses ranked by investigative value, best first')

class DarkQueryProgram:
    """Wraps DarkQuerySignature with ChainOfThought reasoning."""
    __slots__ = ('program',)

    def __init__(self):
        if not _DSPY_AVAILABLE or not HLEDAC_ENABLE_DSPY:
            raise RuntimeError('DSPy not available or not enabled')
        self.program = dspy.ChainOfThought(DarkQuerySignature)

    def forward(self, ioc_brief: str, available_transports: str='tor+stealth', max_queries: int=10) -> dspy.Prediction:
        return self.program(ioc_brief=ioc_brief, available_transports=available_transports, max_queries=max_queries)

class HypothesisGeneratorProgram:
    """Wraps HypothesisGeneratorSignature with ChainOfThought."""
    __slots__ = ('program',)

    def __init__(self):
        if not _DSPY_AVAILABLE or not HLEDAC_ENABLE_DSPY:
            raise RuntimeError('DSPy not available or not enabled')
        self.program = dspy.ChainOfThought(HypothesisGeneratorSignature)

    def forward(self, research_query: str, rag_context: str='', graph_summary: str='', reward_context: str='', existing_hypotheses: list[str] | None=None) -> dspy.Prediction:
        return self.program(research_query=research_query, rag_context=rag_context, graph_summary=graph_summary, reward_context=reward_context, existing_hypotheses=existing_hypotheses or [])

class HypothesisRankProgram:
    """Wraps HypothesisRankerSignature with ChainOfThought."""
    __slots__ = ('program',)

    def __init__(self):
        if not _DSPY_AVAILABLE or not HLEDAC_ENABLE_DSPY:
            raise RuntimeError('DSPy not available or not enabled')
        self.program = dspy.ChainOfThought(HypothesisRankerSignature)

    def forward(self, hypotheses: list[str], sprint_context: str='') -> dspy.Prediction:
        return self.program(hypotheses=hypotheses, sprint_context=sprint_context)

def load_compiled_program(name: str) -> Any | None:
    """
    Load a compiled DSPy program from ~/.hledac/dspy/{name}.json.

    Returns None if:
      - DSPy not available
      - HLEDAC_ENABLE_DSPY != "1"
      - File does not exist
      - JSON invalid

    M1 constraint: this is read-only at runtime. Compilation is offline.
    """
    if not _DSPY_AVAILABLE or not HLEDAC_ENABLE_DSPY:
        return None
    path = _DSPY_DIR / f'{name}.json'
    if not path.exists():
        logger.info("DSPy: No compiled program for '%s' — running zero-shot. Compile with: python scripts/dspy_compile.py --program %s --train gold_data/dark_queries.jsonl", name, name)
        return None
    try:
        state = _msgspec_decode(path.read_text())
        program_cls = {'dark_query': DarkQueryProgram, 'hypothesis_generator': HypothesisGeneratorProgram, 'hypothesis_ranker': HypothesisRankProgram}.get(name)
        if program_cls is None:
            logger.warning(f'Unknown DSPy program name: {name}')
            return None
        program = program_cls()
        if 'parameters' in state:
            logger.info(f'Loaded compiled DSPy program: {name}')
        return program
    except (json.JSONDecodeError, Exception) as e:
        logger.warning(f'Failed to load DSPy program {name}: {e}')
        return None

def save_compiled_program(name: str, state: dict[str, Any]) -> None:
    """Save compiled program state to ~/.hledac/dspy/{name}.json."""
    path = _DSPY_DIR / f'{name}.json'
    path.write_text(_msgspec_encode(state).decode())
    logger.info(f'Saved compiled DSPy program to {path}')
MAX_EPISTEMIC_FINDINGS = 30

class EpistemicGapSignature:
    """Signature for identifying unknown gaps from sprint findings."""
    if _DSPY_AVAILABLE:
        findings = dspy.InputField(desc='Current sprint findings as text (max 30 findings)')
        known_gaps = dspy.InputField(desc='Previously identified knowledge gaps')
        query = dspy.InputField(desc='Research query')
        gaps = dspy.OutputField(desc='Prioritized list of unanswered questions')
        evidence_needed = dspy.OutputField(desc='Specific evidence types needed to fill gaps')
        confidence = dspy.OutputField(desc='Confidence that these gaps are real (0-1)')

class ContradictionResolverSignature:
    """Signature for resolving contradictory findings."""
    if _DSPY_AVAILABLE:
        contradictory_findings = dspy.InputField(desc='Findings with high DS conflict (conflict_mass > 0.3)')
        context = dspy.InputField(desc='Sprint context and goal')
        resolution = dspy.OutputField(desc='Resolution strategy for the contradiction')
        adjusted_evidence = dspy.OutputField(desc='Confidence-adjusted evidence after resolution')
        confidence = dspy.OutputField(desc='Confidence in resolution (0-1)')

class EpistemicGapProgram:
    """
    DSPy program for identifying epistemic gaps in OSINT findings.

    Inputs:
        - findings: Current sprint findings (max 30 due to M1 RAM)
        - known_gaps: Previously identified gaps from ResearchSessionMemory
        - query: Research query

    Outputs:
        - gaps: Prioritized unanswered questions
        - evidence_needed: Specific evidence types to fill gaps
        - confidence: Confidence that gaps are real

    Wire: Called after WINDUP synthesis in sprint_scheduler
    """
    MAX_FINDINGS = MAX_EPISTEMIC_FINDINGS
    __slots__ = ('program',)

    def __init__(self):
        if not _DSPY_AVAILABLE or not HLEDAC_ENABLE_DSPY:
            raise RuntimeError('DSPy not available or not enabled')
        self.program = dspy.ChainOfThought(EpistemicGapSignature)

    def forward(self, findings: list[str], known_gaps: list[str] | None=None, query: str='') -> dspy.Prediction:
        """
        Identify epistemic gaps from findings.

        Args:
            findings: List of finding strings (max 30)
            known_gaps: Previously identified gaps
            query: Research query

        Returns:
            DSPy Prediction with gaps, evidence_needed, confidence
        """
        limited_findings = findings[:self.MAX_FINDINGS]
        findings_text = '\n'.join((f'- {f[:200]}' for f in limited_findings)) if limited_findings else 'No findings available'
        known_gaps_text = '\n'.join((f'- {g}' for g in known_gaps or [])) if known_gaps else 'No known gaps'
        return self.program(findings=findings_text, known_gaps=known_gaps_text, query=query)

class ContradictionResolverProgram:
    """
    DSPy program for resolving contradictory OSINT findings.

    Uses DS conflict_mass > 0.3 threshold to identify contradictions.
    Applies ChainOfThought reasoning to resolve and adjust evidence.

    Inputs:
        - contradictory_findings: Findings with high DS conflict
        - context: Sprint context and goal

    Outputs:
        - resolution: Resolution strategy
        - adjusted_evidence: Confidence-adjusted evidence
        - confidence: Confidence in resolution

    Wire: Called when DS conflict_mass > 0.3 in hypothesis_engine
    """
    MAX_CONTRADICTIONS = 5
    __slots__ = ('program',)

    def __init__(self):
        if not _DSPY_AVAILABLE or not HLEDAC_ENABLE_DSPY:
            raise RuntimeError('DSPy not available or not enabled')
        self.program = dspy.ChainOfThought(ContradictionResolverSignature)

    def forward(self, contradictory_findings: list[dict], context: str='') -> dspy.Prediction:
        """
        Resolve contradictory findings.

        Args:
            contradictory_findings: List of {finding, conflict_mass, source} dicts
            context: Sprint context

        Returns:
            DSPy Prediction with resolution, adjusted_evidence, confidence
        """
        limited = contradictory_findings[:self.MAX_CONTRADICTIONS]
        findings_text = '\n'.join((f"- [{f.get('conflict_mass', 0):.2f}] {f.get('finding', '')[:150]}" for f in limited)) if limited else 'No contradictory findings'
        return self.program(contradictory_findings=findings_text, context=context)
try:
    from hledac.universal.brain.dspy_signatures import DeepResearchChain, DeepResearchHopSignature
    _DEEP_RESEARCH_SIGNATURE_AVAILABLE = True
except ImportError:
    DeepResearchHopSignature = None
    DeepResearchChain = None
    _DEEP_RESEARCH_SIGNATURE_AVAILABLE = False

class MultiHopDeepResearchChain:
    """
    F260: DSPy-powered multi-hop deep research chain.

    Unifies InferenceEngine.MultiHopPath reasoning with GraphRAGOrchestrator
    multi-hop traversal into a single coherent DSPy module.

    M1 Constraints:
        - max_hops adapts based on RAM: 3 when RAM > 5GB, 5 when RAM < 4.5GB
        - Each hop bounded to 2 GraphRAG hops and 30 nodes max
        - Total chain timeout: 120 seconds

    Wire: hypothesis_engine.generate_hypotheses_async() before generating hypotheses
    """
    DEFAULT_MAX_HOPS = 5
    CONFIDENCE_THRESHOLD = 0.3
    MAX_EVIDENCE_PER_HOP = 20
    MAX_NODES_PER_HOP = 30
    MAX_HOPS_PER_SEARCH = 2
    TIMEOUT_SECONDS = 120
    __slots__ = ('graph_rag', 'hop_reasoner', 'max_hops')

    def __init__(self, max_hops: int | None=None, graph_rag: Any=None):
        """
        Initialize multi-hop research chain.

        Args:
            max_hops: Override default max hops (RAM-adaptive)
            graph_rag: GraphRAGOrchestrator instance for evidence retrieval
        """
        if not _DSPY_AVAILABLE or not HLEDAC_ENABLE_DSPY:
            raise RuntimeError('DSPy not available or not enabled')
        self.max_hops = max_hops or self.DEFAULT_MAX_HOPS
        self.graph_rag = graph_rag
        self.hop_reasoner = dspy.ChainOfThought(DeepResearchHopSignature)

    def forward(self, query: str, initial_findings: list[str], graph_rag: Any | None=None) -> list[str]:
        """
        Execute multi-hop deep research chain.

        Args:
            query: Research query
            initial_findings: Starting evidence pool
            graph_rag: Optional GraphRAGOrchestrator (overrides instance attr)

        Returns:
            Extended evidence list with multi-hop findings
        """
        from hledac.universal.brain.dspy_signatures import is_dspy_available
        if not is_dspy_available():
            logger.warning('DSPy not available, returning initial findings')
            return list(initial_findings)
        effective_max_hops = self._get_ram_adaptive_hops()
        evidence = list(initial_findings[:self.MAX_EVIDENCE_PER_HOP])
        grag = graph_rag or self.graph_rag
        if grag is None:
            logger.warning('No GraphRAG available, returning initial findings')
            return evidence
        for hop_n in range(1, effective_max_hops + 1):
            evidence_context = evidence[-self.MAX_EVIDENCE_PER_HOP:]
            try:
                result = self.hop_reasoner(query=query, current_evidence=evidence_context, hop_number=hop_n)
                confidence = getattr(result, 'confidence', 0.0) or 0.0
                if confidence < self.CONFIDENCE_THRESHOLD:
                    logger.debug(f'MultiHop: stopping at hop {hop_n} (confidence={confidence:.2f} < {self.CONFIDENCE_THRESHOLD})')
                    break
                next_query = getattr(result, 'next_query', '') or ''
                if not next_query:
                    break
                new_findings = self._fetch_graph_evidence(grag, next_query, hop_n)
                for finding in new_findings:
                    if len(finding) > 200:
                        finding = finding[:200] + '...'
                    evidence.append(finding)
            except Exception as e:
                logger.warning(f'MultiHop hop {hop_n} failed: {e}')
                continue
        return evidence

    def _get_ram_adaptive_hops(self) -> int:
        """Get hop count based on available RAM."""
        try:
            from hledac.universal.utils.uma_budget import get_uma_snapshot
            snapshot = get_uma_snapshot()
            if snapshot.is_emergency or snapshot.is_critical:
                return min(3, self.max_hops)
            if snapshot.is_warn:
                return min(4, self.max_hops)
            return self.max_hops
        except Exception:
            return self.max_hops

    def _fetch_graph_evidence(self, graph_rag: Any, query: str, hop_number: int) -> list[str]:
        """
        Fetch evidence from GraphRAG for a given query.

        Args:
            graph_rag: GraphRAGOrchestrator instance
            query: Search query
            hop_number: Current hop number (for logging)

        Returns:
            List of finding strings
        """
        try:
            import time
            start = time.monotonic()
            if time.monotonic() - start > self.TIMEOUT_SECONDS:
                logger.debug(f'MultiHop: timeout exceeded at hop {hop_number}')
                return []
            result = graph_rag.multi_hop_search(query=query, hops=self.MAX_HOPS_PER_SEARCH, max_nodes=self.MAX_NODES_PER_HOP)
            findings = []
            nodes = result.get('nodes', [])
            for node in nodes:
                content = node.get('content', '') or node.get('name', '')
                if content:
                    findings.append(content)
            logger.debug(f"MultiHop hop {hop_number}: fetched {len(findings)} findings for query '{query[:50]}...'")
            return findings
        except Exception as e:
            logger.warning(f'MultiHop: graph_rag search failed: {e}')
            return []

def get_multi_hop_chain(graph_rag: Any=None, max_hops: int | None=None) -> MultiHopDeepResearchChain | None:
    """
    Factory: get or create MultiHopDeepResearchChain.

    Args:
        graph_rag: GraphRAGOrchestrator instance
        max_hops: RAM-adaptive hop override

    Returns:
        MultiHopDeepResearchChain instance or None if DSPy not available
    """
    if not _DSPY_AVAILABLE or not HLEDAC_ENABLE_DSPY:
        return None
    try:
        return MultiHopDeepResearchChain(max_hops=max_hops, graph_rag=graph_rag)
    except Exception as e:
        logger.warning(f'MultiHop chain creation failed: {e}')
        return None
_PROGRAMS: dict[str, Any | None] = {'dark_query': None, 'hypothesis_generator': None, 'hypothesis_ranker': None}

def get_program(name: str) -> Any | None:
    """Get (or lazy-load) a compiled DSPy program."""
    if name not in _PROGRAMS:
        return None
    if _PROGRAMS[name] is None:
        _PROGRAMS[name] = load_compiled_program(name)
    return _PROGRAMS[name]
DARK_QUERY_ZERO_SHOT = 'Z techto IOC z aktualniho sprintu: {ioc_brief}\n\nNavrhuj {max_queries} specificke dotazy pro dark surface (neindexovane zdroje).\nPro kazdy dotaz uved:\n1. typ: onion | ipfs | paste | i2p\n2. samotny dotaz (co hledat)\n3. priorita: 0-1 (vyssi = dulezitejsi)\n4. odovodneni (proc by to mohlo mit relevantni data)\n\nVystup formatuj jako JSON list s objekty: type, query, priority, reasoning\n\nZajimave patterny k hledani:\n- .onion domeny korelovane s IP/domain z IOC\n- IPFS CID z intelligence findings\n- Paste site leak korelace\n- Darknet forum IOC patterns'
HYPOTHESIS_ZERO_SHOT = 'Research query: {query}\n\nRAG ctx:\n{rag_context}\n\n{graph_summary}\n{reward_context}\n\nNavrhni možné cesty, jak získat více informací o "{query}".\n生成 5-10 konkrétních hypotéz v češtině, kde každá začíná číslem.\n\nFormát (pouze seznam, žádný další text):\n1. [hypotéza 1]\n2. [hypotéza 2]\n...'
try:
    from hledac.universal.brain.evidence_fusion import DempsterShafer
    from hledac.universal.utils.eig import EIGCalculator
    _DS_EIG_AVAILABLE = True
except ImportError:
    DempsterShafer = None
    EIGCalculator = None
    _DS_EIG_AVAILABLE = False

def _compute_conflict_from_evidence(evidence_list: list[dict]) -> float:
    """
    Compute DS conflict mass from evidence list.

    Args:
        evidence_list: List of {hypothesis, mass, source_weight} dicts

    Returns:
        Conflict mass (0.0-1.0, higher = more contradictory)
    """
    if not _DS_EIG_AVAILABLE or not evidence_list:
        return 0.0
    hypotheses = {e.get('hypothesis', 'present') for e in evidence_list}
    hypotheses.add('absent')
    ds = DempsterShafer(hypotheses=hypotheses)
    for e in evidence_list:
        ds.add_evidence(hypothesis=e.get('hypothesis', 'present'), mass=e.get('mass', 0.5), source_weight=e.get('source_weight', 1.0))
    return ds.conflict_mass()

def _compute_eig_bonus(hypothesis_set: list, action: dict) -> float:
    """
    Compute EIG bonus for action that reduces entropy.

    Returns:
        EIG bonus (0.0-0.1) if action reduces entropy, else 0.0
    """
    if not _DS_EIG_AVAILABLE or not hypothesis_set:
        return 0.0
    try:
        calc = EIGCalculator()
        eig = calc.compute_eig(hypothesis_set, action)
        return min(0.1, max(0.0, eig))
    except Exception:
        return 0.0

def osint_metric(example, pred, trace=None) -> float:
    """
    MIPROv2 training metric with DS penalty and EIG bonus.

    Base score: semantic similarity (cosine) between predicted and gold findings.
    DS penalty: if conflict_mass > 0.4 → multiply by (1 - conflict_mass).
    EIG bonus: +0.1 if prediction reduces entropy.

    Args:
        example: Gold standard example with 'evidence' field
        pred: Predicted answer
        trace: Optional trace dict with 'evidence' and 'action' keys

    Returns:
        Score0.0-1.0
    """
    try:
        answer = str(pred.answer)
        if len(answer) < 50:
            return 0.0
        try:
            data = _msgspec_decode(answer)
        except Exception:
            return 0.3 if len(answer) > 100 else 0.0
        fields = data.keys() if isinstance(data, dict) else []
        field_bonus = min(1.0, len(fields) / 3)
        base_score = 0.7 + 0.3 * field_bonus
        evidence_list = []
        if trace and isinstance(trace, dict):
            evidence_list = trace.get('evidence', [])
        elif hasattr(example, 'evidence'):
            evidence_list = example.evidence or []
        elif isinstance(data, dict) and 'evidence' in data:
            evidence_list = data.get('evidence', [])
        conflict = _compute_conflict_from_evidence(evidence_list)
        if conflict > 0.4:
            penalty = conflict * 0.5
            base_score = base_score * (1.0 - penalty)
        action = {}
        if trace and isinstance(trace, dict):
            action = trace.get('action', {})
        elif hasattr(example, 'action'):
            action = example.action or {}
        if action:
            eig_bonus = _compute_eig_bonus([], action)
            base_score = min(1.0, base_score + eig_bonus)
        return max(0.0, min(1.0, base_score))
    except Exception:
        return 0.5