"""
SynthesisRunner — Sprint 8QC
============================
Orchestrates MLX-based structured synthesis of OSINT findings into STIX-ready reports.
Works in WINDUP phase only (or with explicit force_synthesis=True).

OSINTReport schema (msgspec.Struct):
  - query: str
  - ioc_entities: list[IOCEntity]
  - threat_summary: str (max 3 věty)
  - threat_actors: list[str] (APT skupiny, ransomware gangy)
  - confidence: float (0.0-1.0)
  - sources_count: int
  - timestamp: float (Unix epoch)

E2E flow:
  sprint lifecycle WINDUP → SynthesisRunner.synthesize_findings()
  → structured_generate() (Outlines MLX constrained JSON)
  → unload + gc → JSON export do ~/.hledac/reports/
"""

from __future__ import annotations

import asyncio
import gc
import json as _json
import logging
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

try:
    import msgspec as _msgspec
    msgspec = _msgspec
except ImportError:
    msgspec = None  # type: ignore
    import logging
    _logger_msgspec = logging.getLogger(__name__)
    _logger_msgspec.warning("msgspec not installed — JSON constrained generation disabled")

if TYPE_CHECKING:
    from .model_lifecycle import ModelLifecycle

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sprint 8UF B.1: xgrammar grammar cache — compile ONCE per schema lifetime
# ---------------------------------------------------------------------------
import hashlib
import threading as _threading
import re as _re_synth

_MAX_VALIDATION_FINDINGS = 100  # bounded — M1 8GB guard

def _extract_text_iocs_from_finding(finding: dict) -> set[str]:
    """Extract IOC-like strings from a single finding dict.
    Scans structured IOC fields AND raw content via regex.
    Fail-soft: returns empty set on any error.
    """
    iocs: set[str] = set()
    try:
        for field in ('ioc_val', 'val', 'value', 'indicator', 'ioc', 'hash', 'ip', 'domain'):
            v = finding.get(field)
            if v and isinstance(v, str):
                iocs.add(v.strip())
        content = (finding.get('content') or finding.get('raw_content')
                   or finding.get('text') or finding.get('snippet') or '')
        if content:
            iocs.update(_re_synth.findall(r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b', content))
            iocs.update(_re_synth.findall(
                r'\b[a-zA-Z0-9][a-zA-Z0-9\-]{1,61}\.[a-zA-Z]{2,}\b', content))
            iocs.update(_re_synth.findall(r'\b[a-fA-F0-9]{32,64}\b', content))
            iocs.update(_re_synth.findall(r'CVE-\d{4}-\d{4,7}', content, _re_synth.I))
    except Exception as e:
        logger.debug(f"_extract_text_iocs_from_finding failed: {e}")
    return iocs


def validate_evidence_grounding(
    report: "OSINTReport",
    findings: list[dict],
) -> tuple[bool, list[str]]:
    """GAP-8: Validate that IOCEntity values in report appear in source findings.

    Returns (True, []) on clean pass.
    Returns (True, [list of unmatched IOC values]) on mismatch — FAIL-SOFT.
    Never raises. Never returns False (fail-soft per M1 GHOST_INVARIANTS).
    """
    if not findings:
        return (True, ["no findings to validate against"])
    try:
        evidence_set: set[str] = set()
        for f in findings[:_MAX_VALIDATION_FINDINGS]:
            evidence_set.update(_extract_text_iocs_from_finding(f))
        ioc_entities = getattr(report, 'ioc_entities', None) or []
        unmatched = [
            str(ioc.value)
            for ioc in ioc_entities
            if hasattr(ioc, 'value') and str(ioc.value) not in evidence_set
        ]
        if unmatched:
            logger.warning(
                f"GAP-8 grounding: {len(unmatched)}/{len(ioc_entities)} IOCs unverified "
                f"in findings — values: {unmatched[:5]}"
            )
        return (True, unmatched)
    except Exception as e:
        logger.debug(f"validate_evidence_grounding exception (fail-soft): {e}")
        return (True, [])


def validate_report_semantics(report: "OSINTReport") -> tuple[bool, list[str]]:
    """GAP-7: Semantic constraint validation for OSINTReport fields.

    Validates value ranges that msgspec.Struct cannot enforce.
    Returns (True, []) on pass.
    Returns (False, [error list]) on violation — CALLER decides whether to log or block.
    Never raises.
    """
    errors: list[str] = []
    try:
        conf = getattr(report, 'confidence', None)
        if conf is not None and not (0.0 <= float(conf) <= 1.0):
            errors.append(f"confidence {conf} out of range [0.0, 1.0]")

        sc = getattr(report, 'sources_count', None)
        if sc is not None and int(sc) < 0:
            errors.append(f"sources_count {sc} is negative")

        ts = getattr(report, 'timestamp', None)
        if ts is not None and float(ts) <= 0:
            errors.append(f"timestamp {ts} invalid (must be positive unix epoch)")

        ioc_entities = getattr(report, 'ioc_entities', None) or []
        if len(ioc_entities) == 0 and sc is not None and int(sc) > 0:
            errors.append(
                f"ioc_entities empty but sources_count={sc} — possible generation failure")

        threat_summary = getattr(report, 'threat_summary', None)
        if (not threat_summary or not isinstance(threat_summary, str)
                or not threat_summary.strip()):
            errors.append("threat_summary is empty or whitespace-only")

    except Exception as e:
        logger.debug(f"validate_report_semantics exception (fail-soft): {e}")
        return (True, [])  # fail-soft on introspection error

    return (len(errors) == 0, errors)


_GRAMMAR_CACHE: dict[str, object] = {}
_GRAMMAR_CACHE_LOCK = _threading.RLock()


def _get_cached_grammar(schema_json_str: str, tokenizer):
    """Compile JSON Schema grammar ONLY on first call per schema.
    Key = SHA-256 of first 256 chars of schema (schema is constant)."""
    key = hashlib.sha256(schema_json_str[:256].encode()).hexdigest()[:16]
    with _GRAMMAR_CACHE_LOCK:
        if key not in _GRAMMAR_CACHE:
            import xgrammar as xgr
            tokenizer_info = xgr.tokenizer_info.TokenizerInfo.from_tokenizer(tokenizer)
            compiler = xgr.GrammarCompiler(tokenizer_info)
            _GRAMMAR_CACHE[key] = compiler.compile_json_schema(schema_json_str)
        return _GRAMMAR_CACHE[key]


# ---------------------------------------------------------------------------
# Sprint 8UC B.1: JSON Schema for OSINTReport — xgrammar + Outlines compatible
# ---------------------------------------------------------------------------


def _build_osint_json_schema() -> dict:
    """JSON Schema for OSINTReport — compatible with xgrammar GrammarCompiler and Outlines."""
    return {
        "type": "object",
        "properties": {
            "title":           {"type": "string"},
            "summary":         {"type": "string"},
            "confidence":      {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "findings":        {"type": "array", "items": {"type": "string"}, "maxItems": 20},
            "threat_actors":   {"type": "array", "items": {"type": "string"}, "maxItems": 10},
            "iocs":            {"type": "array", "items": {"type": "string"}, "maxItems": 50},
            "ttps":            {"type": "array", "items": {"type": "string"}, "maxItems": 15},
            "recommendations": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
        },
        "required": ["title", "summary", "confidence"],
        "additionalProperties": False,
    }


def _infer_ioc_type(text: str) -> str:
    """Infer IOC type from text content."""
    t = text.lower()
    if any(x in t for x in ["cve-", "cve_", "vulnerability"]):
        return "cve"
    if "." not in t and len(text) > 20:
        return "hash"
    if t.startswith("http") or ".onion" in t or ".i2p" in t:
        return "onion"
    if "/" in t and "." not in t.split("/")[0]:
        return "hash"
    if t.startswith("1") and len(t) > 30:
        return "btc"
    if "@" in t:
        return "email"
    return "domain"

# ---------------------------------------------------------------------------
# Sprint 8VH: Brain Intelligence Layer Integration State
# ---------------------------------------------------------------------------

_DSPY_PROMPTS: dict | None = None
_PROMPT_BANDIT = None
_DSPY_OPTIMIZER = None


def _get_dspy_optimizer():
    """Lazy init DSPyOptimizer — starts background optimization loop on first call."""
    global _DSPY_OPTIMIZER
    if _DSPY_OPTIMIZER is not None:
        return _DSPY_OPTIMIZER
    try:
        from brain.dspy_optimizer import DSPyOptimizer
        _DSPY_OPTIMIZER = DSPyOptimizer(brain_manager=None)
        # Sprint F234: Start background optimization loop (non-blocking)
        import asyncio
        asyncio.create_task(_DSPY_OPTIMIZER.start(), name="dspy_optimizer")
    except Exception:
        _DSPY_OPTIMIZER = None
    return _DSPY_OPTIMIZER


def _get_dspy_prompts() -> dict:
    """
    Lazy load DSPy optimalizované prompty from optimizer cache.
    Fallback: prázdný dict (synthesis použije hardcoded templates).
    """
    global _DSPY_PROMPTS
    if _DSPY_PROMPTS is not None:
        return _DSPY_PROMPTS
    prompts: dict = {}
    try:
        # Sprint F234: Try optimizer first, then fallback to load_optimized_prompts
        dspy_opt = _get_dspy_optimizer()
        if dspy_opt is not None and dspy_opt._optimized_prompts:
            prompts = dspy_opt._optimized_prompts
        else:
            from brain.dspy_optimizer import load_optimized_prompts
            prompts = load_optimized_prompts()
    except Exception:
        prompts = {}
    _DSPY_PROMPTS = prompts
    return prompts


def _get_prompt_bandit():
    """Lazy init PromptBandit."""
    global _PROMPT_BANDIT
    if _PROMPT_BANDIT is not None:
        return _PROMPT_BANDIT
    try:
        from brain.prompt_bandit import PromptBandit
        _PROMPT_BANDIT = PromptBandit(
            brain_manager=None,
            alpha=1.0,
            lambda_reg=0.01,
            context_dim=9,
            persist_path=str(Path.home() / '.hledac' / 'prompt_bandit.json'),
        )
    except Exception:
        _PROMPT_BANDIT = None
    return _PROMPT_BANDIT


async def _distill_findings(
    findings: list[dict],
    max_tokens: int = 2000,
) -> str:
    """
    Předprocesuje findings přes DistillationEngine před synthesis.
    Fallback: serialize top findings jako plaintext.
    """
    try:
        from brain.distillation_engine import distil
        return await distil(findings, max_tokens=max_tokens)
    except Exception:
        # Fallback: serialize top findings jako text
        lines = []
        for f in findings[:20]:
            lines.append(
                f"[{f.get('source', '?')}] {f.get('title', '')} "
                f"— {f.get('snippet', f.get('text', ''))[:200]}"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# OSINTReport Schema — msgspec.Struct for JSON constrained generation
# ---------------------------------------------------------------------------


class SynthesisOutcome(msgspec.Struct):
    """
    Sprint F151A: Fail-soft synthesis outcome seam.

    Carries structured truth about every exit path in synthesize_findings()
    so callers never have to guess why synthesis returned None.
    """
    # execution status
    status: str            # "executed" | "skipped" | "failed" | "success"
    primary_reason: str    # "lifecycle_blocked" | "uma_blocked" | "no_model"
                          # | "no_findings" | "generation_failed" | "parse_failed"
                          # | "success" | "unknown"
    # lifecycle gate truth (Sprint 8VL)
    lifecycle_gate_source: str  # "runtime" | "compat" | "unavailable" | "forced" | "unknown"
    lifecycle_gate_mode: str   # "windup" | "forced" | "blocked" | "unknown"
    # STIX degradation state (Sprint 8TH)
    stix_status: str       # "available" | "unavailable" | "error" | "unknown"
    stix_reason: str       # concrete reason string
    stix_backend: str      # backend class name or ""
    # engine + findings
    engine_used: str        # "xgrammar" | "streaming" | "constrained" | "none"
    findings_considered: int # count of findings passed to synthesis
    report_produced: bool   # True if OSINTReport was returned
    confidence: float      # 0.0-1.0, valid only if report_produced=True
    operator_note: str     # short human-readable note


def synthesis_outcome_to_dict(outcome: SynthesisOutcome | None) -> dict:
    """
    Sprint F151A: Lightweight export seam over SynthesisOutcome.

    Maps to preferred export-friendly keys:
      status, primary_reason, engine, backend,
      lifecycle_gate_source, lifecycle_gate_mode,
      report_present, degraded, operator_note

    Fail-soft: returns a minimal dict even on AttributeError or None.
    """
    if outcome is None:
        return {"status": "unknown", "primary_reason": "no_outcome", "operator_note": ""}
    try:
        return {
            "status": outcome.status,
            "primary_reason": outcome.primary_reason,
            "engine": outcome.engine_used,
            "backend": outcome.stix_backend,
            "lifecycle_gate_source": outcome.lifecycle_gate_source,
            "lifecycle_gate_mode": outcome.lifecycle_gate_mode,
            "report_present": outcome.report_produced,
            "degraded": (
                outcome.primary_reason in ("generation_failed", "parse_failed")
            ),
            "operator_note": outcome.operator_note,
        }
    except AttributeError:
        return {"status": "unknown", "primary_reason": "attr_error", "operator_note": ""}


class IOCEntity(msgspec.Struct):
    """Jedna IOC entita extrahovaná z findingu."""
    value: str
    ioc_type: str  # "cve","ip","hash","onion","domain","apt","malware","btc"
    severity: str   # "critical","high","medium","low"
    context: str    # 1 věta


class OSINTReport(msgspec.Struct):
    """
    STIX-ready OSINT synthesis report.

    Vrací se z structured_generate() při úspěchu.
    Timestamp je Unix epoch (float), threat_actors jsou APT/ransomware gangy.
    """
    query: str
    ioc_entities: list[IOCEntity]
    threat_summary: str          # max 3 věty
    threat_actors: list[str]     # APT skupiny, ransomware gangy
    confidence: float            # 0.0-1.0
    sources_count: int
    timestamp: float            # Unix epoch


# ---------------------------------------------------------------------------
# Sprint 8TA: Outlines json_schema dict — not msgspec.Struct
# ---------------------------------------------------------------------------

OSINT_JSON_SCHEMA: str = _json.dumps({
    "type": "object",
    "properties": {
        "title":          {"type": "string"},
        "summary":        {"type": "string"},
        "threat_actors":  {"type": "array", "items": {"type": "string"}},
        "findings":       {"type": "array", "items": {"type": "string"}},
        "confidence":     {"type": "number", "minimum": 0, "maximum": 1},
        "timestamp":      {"type": "number"},
    },
    "required": ["title", "summary", "threat_actors", "findings", "confidence", "timestamp"],
    "additionalProperties": False,
})


# Sprint 8VF: flashrank singleton — loaded once, reused across sprint cycles
# NOTE: This is a COMPATIBILITY WRAPPER for the synthesis rerank path.
# Canonical reranker owner is tools/reranker.py (LightweightReranker).
# This instance exists for historical reasons and serves the synthesis context.
_FLASHRANK_RANKER = None

def _get_flashrank_ranker():
    """Get FlashRank reranker for synthesis path.

    Canonical owner: tools/reranker.py
    This is a compatibility wrapper serving the synthesis context only.
    Uses ms-marco-MiniLM-L-12-v2 model (same as canonical).
    """
    global _FLASHRANK_RANKER
    if _FLASHRANK_RANKER is None:
        from flashrank import Ranker
        _FLASHRANK_RANKER = Ranker(
            model_name="ms-marco-MiniLM-L-12-v2",
            cache_dir="/tmp",
        )
    return _FLASHRANK_RANKER


# ---------------------------------------------------------------------------
# SynthesisRunner
# ---------------------------------------------------------------------------


class SynthesisRunner:
    """
    WINDUP-only synthesis orchestrator.

    Usage:
        runner = SynthesisRunner(model_lifecycle)
        runner.inject_graph(ioc_graph)
        report = await runner.synthesize_findings(query, findings, force_synthesis=True)
        await runner.close()
    """

    __slots__ = ("_lifecycle", "_ioc_graph", "_cached_model_path", "_last_outlines_used",
                 "_custom_synthesis_prompt", "_prompt_modifier", "_duckdb_store",
                 "_last_synthesis_engine", "_last_arm", "_bandit_rewards",
                 "_stix_status", "_stix_reason", "_stix_backend",
                 "_lifecycle_gate_source", "_lifecycle_gate_mode", "_lifecycle_adapter",
                 "_stix_graph", "_last_synthesis_outcome",
                 "_compression_threshold", "_compressor",
                 "_hypothesis_engine")

    def __init__(self, lifecycle: "ModelLifecycle") -> None:
        self._lifecycle = lifecycle
        self._ioc_graph: Optional[Any] = None
        self._cached_model_path: Optional[Path] = None
        self._last_outlines_used: bool = False
        # Sprint 8TD: Custom prompt support
        self._custom_synthesis_prompt: Optional[str] = None
        self._prompt_modifier: str = ""
        # Sprint 8UC B.2: DuckDB store for episode recall
        self._duckdb_store: Optional[Any] = None
        # Sprint 8UC B.3: Last synthesis engine used
        self._last_synthesis_engine: str = "none"
        # Sprint 8VH: Bandit tracking
        self._last_arm: str | None = None
        self._bandit_rewards: dict = {}
        # Sprint 8TH: Structured STIX degradation state
        self._stix_status: str = "unknown"
        self._stix_reason: str = ""
        self._stix_backend: str = ""
        # Sprint 8VL: Lifecycle gate truth — structured degradation state
        # _lifecycle_gate_source: "runtime" | "compat" | "unavailable"
        # _lifecycle_gate_mode: "windup" | "forced" | "blocked"
        # _lifecycle_adapter: _LifecycleAdapter | None (for runtime path)
        self._lifecycle_gate_source: str = "unknown"
        self._lifecycle_gate_mode: str = "unknown"
        self._lifecycle_adapter: Any = None
        # Sprint 8VQ: Dedicated STIX truth-store graph (IOCGraph/Kuzu only)
        self._stix_graph: Any = None
        # Sprint F151A: Last synthesis outcome — structured seam for all exit paths
        self._last_synthesis_outcome: SynthesisOutcome | None = None

        # F234: Context compression — opt-in threshold (0 = disabled)
        # Default 0 means compression is disabled unless explicitly enabled
        self._compression_threshold: int = 0
        self._compressor: Optional[Any] = None

        # F214: HypothesisEngine — optional synthesis step
        self._hypothesis_engine: Optional[Any] = None

    def inject_graph(self, graph: Any) -> None:
        """Inject IOCGraph instance from 8QA for STIX context injection."""
        self._ioc_graph = graph

    def inject_stix_graph(self, graph: Any) -> None:
        """
        Sprint 8VQ: Inject dedicated truth-store STIX graph.

        TRUTH-STORE ONLY: only IOCGraph (Kuzu) has export_stix_bundle().
        This is a CONSUMER-SPECIFIC seam — not a generic graph abstraction.

        Priority in _build_stix_context:
          1. _stix_graph (injected here) — PREFERRED truth path
          2. _ioc_graph (injected via inject_graph) — fallback/analytics path

        Args:
            graph: IOCGraph (Kuzu) instance with export_stix_bundle(), or None.
        """
        self._stix_graph = graph

    def inject_lifecycle_adapter(self, adapter: Any) -> None:
        """
        SPRINT 8VL: Inject runtime lifecycle adapter for windup gate.

        windup_engine passes scheduler._lc_adapter (runtime _LifecycleAdapter wrapping
        the canonical SprintLifecycleManager). This is the PREFERRED truth path —
        it bypasses the need to find a global singleton.

        Also accepts direct runtime SprintLifecycleManager instances.
        """
        self._lifecycle_adapter = adapter

    # ------------------------------------------------------------------
    # F214: HypothesisEngine injection
    # ------------------------------------------------------------------

    def inject_hypothesis_engine(self, engine: Any) -> None:
        """
        F214: Inject HypothesisEngine for optional post-synthesis
        hypothesis extraction from OSINTReport.

        The engine uses the already-loaded Hermes3 via dependency injection
        (not a separate MLX model load). Max 10 active hypotheses per call.
        Fail-soft: hypothesis extraction failure does not affect synthesis result.
        """
        self._hypothesis_engine = engine

    # ------------------------------------------------------------------
    # Sprint 8TD: Custom prompt injection
    # ------------------------------------------------------------------

    def set_custom_prompt(self, prompt: str) -> None:
        """Sprint 8TD: Set custom synthesis prompt from DSPy optimizer."""
        self._custom_synthesis_prompt = prompt
        logger.info(f"SynthesisRunner: custom prompt set ({len(prompt)} chars)")

    def set_prompt_modifier(self, modifier: str) -> None:
        """Sprint 8TD: Set prompt modifier from bandit arm selection."""
        self._prompt_modifier = modifier
        logger.info(f"SynthesisRunner: prompt modifier set ({len(modifier)} chars)")

    # ------------------------------------------------------------------
    # F234: Context compression threshold (opt-in)
    # ------------------------------------------------------------------

    def set_compression_threshold(self, token_threshold: int) -> None:
        """
        F234: Enable context compression when prompt exceeds token_threshold.

        Args:
            token_threshold: Min prompt length (in chars, ~4x tokens) to trigger
                           compression. 0 = disabled (default).
        """
        self._compression_threshold = token_threshold
        if token_threshold > 0 and self._compressor is None:
            try:
                from context_optimization.context_compressor import ContextCompressor
                self._compressor = ContextCompressor()
                logger.info(f"SynthesisRunner: compression enabled, threshold={token_threshold}")
            except Exception as e:
                logger.warning(f"SynthesisRunner: compressor init failed: {e}")

    # ------------------------------------------------------------------
    # Sprint F151A: Synthesis outcome seam
    # ------------------------------------------------------------------

    def get_last_synthesis_outcome(self) -> SynthesisOutcome | None:
        """Sprint F151A: Vrátí structured outcome posledního synthesis volání."""
        return self._last_synthesis_outcome

    # ------------------------------------------------------------------
    # Sprint 8TD: Custom prompt injection
    # ------------------------------------------------------------------

    @property
    def last_synthesis_meta(self) -> dict:
        """Vrátí metadata posledního synthesis volání pro scorecard."""
        return {
            "synthesis_engine": getattr(self, "_last_synthesis_engine", "unknown"),
            "dspy_prompt_version": len(_get_dspy_prompts()),
            "bandit_arm_used": getattr(self, "_last_arm", None),
            "bandit_arm_rewards": self._get_bandit_rewards(),
        }

    def _get_bandit_rewards(self) -> dict:
        bandit = _get_prompt_bandit()
        if bandit is None:
            return {}
        try:
            return getattr(bandit, "arm_rewards", {})
        except Exception:
            return {}

    # ------------------------------------------------------------------
    # Public synthesis API
    # ------------------------------------------------------------------

    async def synthesize_findings(
        self,
        query: str,
        findings: list[dict],
        max_findings: int = 10,
        force_synthesis: bool = False,
    ) -> OSINTReport | None:
        """
        Synthesize top findings into OSINTReport.

        WINDUP-only (B.7): skip pokud není WINDUP fáze a force_synthesis=False.
        B.7: skip pokud RSS > 5.5GiB (M1 8GB UMA safety).
        STIX context (B.6): injektuje se z ioc_graph.export_stix_bundle().
        """
        findings_count = len(findings)

        # B.7: WINDUP guard
        if not self._is_windup_allowed(force_synthesis):
            logger.debug("Synthesis skipped: not in WINDUP phase (force=%s)", force_synthesis)
            self._last_synthesis_outcome = SynthesisOutcome(
                status="skipped",
                primary_reason="lifecycle_blocked",
                lifecycle_gate_source=self._lifecycle_gate_source,
                lifecycle_gate_mode=self._lifecycle_gate_mode,
                stix_status=self._stix_status,
                stix_reason=self._stix_reason,
                stix_backend=self._stix_backend,
                engine_used="none",
                findings_considered=findings_count,
                report_produced=False,
                confidence=0.0,
                operator_note="windup guard blocked — not in WINDUP phase",
            )
            return None

        # B.7: UMA RSS > 5.5GiB guard
        if not self._check_uma_guard():
            self._stix_status = "unavailable"
            self._stix_reason = "UMA guard blocked synthesis — RSS > 5.5GiB or EMERGENCY"
            self._stix_backend = ""
            self._lifecycle_gate_source = getattr(self, "_lifecycle_gate_source", "unknown")
            self._lifecycle_gate_mode = "blocked"
            self._last_synthesis_outcome = SynthesisOutcome(
                status="skipped",
                primary_reason="uma_blocked",
                lifecycle_gate_source=self._lifecycle_gate_source,
                lifecycle_gate_mode=self._lifecycle_gate_mode,
                stix_status=self._stix_status,
                stix_reason=self._stix_reason,
                stix_backend=self._stix_backend,
                engine_used="none",
                findings_considered=findings_count,
                report_produced=False,
                confidence=0.0,
                operator_note="UMA RSS > 5.5GiB or EMERGENCY state",
            )
            return None

        # Sprint 8SB: ensure model is available (discovery + optional download)
        model_path = await self._ensure_model()
        if model_path is None:
            logger.warning("[SYNTHESIS] No model available — skipping")
            self._last_synthesis_outcome = SynthesisOutcome(
                status="skipped",
                primary_reason="no_model",
                lifecycle_gate_source=getattr(self, "_lifecycle_gate_source", "unknown"),
                lifecycle_gate_mode=getattr(self, "_lifecycle_gate_mode", "unknown"),
                stix_status=self._stix_status,
                stix_reason="model discovery and download failed — no usable model",
                stix_backend=self._stix_backend,
                engine_used="none",
                findings_considered=findings_count,
                report_produced=False,
                confidence=0.0,
                operator_note="no model available after discovery and download attempt",
            )
            return None

        # Update lifecycle model path for structured_generate
        self._lifecycle._model_path = model_path
        self._lifecycle._loaded = False  # force reload with new path

        # STIX context z 8QA grafu
        stix_context = await self._build_stix_context()

        # Sprint 8UC B.2.3: Inject episode context from research memory
        episode_ctx = ""
        if self._duckdb_store is not None:
            episode_ctx = await self._build_episode_context(self._duckdb_store, query)

        # Sprint 8VA B.2: RAG retrieval — semantically relevant findings
        # Token budget guard pro M1 8GB (~1800 tokens rezerva)
        rag_context = ""
        try:
            from knowledge.rag_engine import RAGEngine
            _rag = RAGEngine()  # lazy singleton
            # Sprint 8VA: RAGEngine.query() — adaptuj dle skutečné API
            rag_result = await _rag.query(
                query=query,
                context_chunks=[f.get("text", "")[:500] for f in findings[:20]],
                use_compression=False,
            )
            if rag_result and rag_result.get("context"):
                raw_ctx = rag_result["context"]
                # Token budget: max ~1800 tokens RAG → ~7200 znaků
                max_chars = 7200
                if len(raw_ctx) > max_chars:
                    raw_ctx = raw_ctx[:max_chars] + "...[truncated]"
                rag_context = f"\n\n## Semantically Retrieved Findings\n{raw_ctx}"
        except Exception as e:
            logger.debug(f"Sprint 8VA RAG retrieve skipped: {e}")

        # Sprint 8VF AREA-A: flashrank ms-marco cross-encoder rerank before LLM synthesis.
        # Replaces confidence-sort ceiling. Cap input at 200 (flashrank RAM limit).
        # Singleton loader — model loaded once per process, ~22MB ONNX.
        try:
            from flashrank import RerankRequest
            _ranker = _get_flashrank_ranker()
            passages = [
                {"id": i, "text": f"{f.get('title', '')} {f.get('snippet', f.get('text', ''))}"}
                for i, f in enumerate(findings[:200])
            ]
            rerank_request = RerankRequest(query=query, passages=passages)
            results = _ranker.rerank(rerank_request)
            ranked_idxs = [r["id"] for r in results[:max_findings]]
            top = [findings[i] for i in ranked_idxs]
        except Exception:
            top = sorted(findings, key=lambda f: f.get("confidence", 0.0), reverse=True)[:max_findings]

        # Sprint 8VA C.2: GraphRAG — IOC relationship context (WINDUP phase)
        graph_context = ""
        top_iocs = [
            f.get("ioc") or f.get("indicator") or f.get("value")
            for f in top[:5]
            if f.get("ioc") or f.get("indicator") or f.get("value")
        ]
        if top_iocs:
            try:
                from knowledge.graph_rag import GraphRAGOrchestrator
                # GraphRAGOrchestrator vyžaduje knowledge_layer — zkusíme najít
                from hledac.universal.legacy.persistent_layer import PersistentKnowledgeLayer
                kl = PersistentKnowledgeLayer()
                _grag = GraphRAGOrchestrator(kl)
                # Sprint 8VA: GraphRAGOrchestrator.find_connections() — ne extract_subgraph/verbalize
                if hasattr(_grag, "find_connections"):
                    conn_texts = []
                    for ioc in top_iocs[:3]:
                        try:
                            conns = _grag.find_connections(ioc, ioc, max_hops=2)
                            if conns:
                                conn_texts.append(f"IOC {ioc}: {'; '.join(str(c)[:80] for c in conns[:3])}")
                        except Exception:
                            pass
                    if conn_texts:
                        graph_context = "\n\n## IOC Relationship Graph\n" + "\n".join(conn_texts)[:1500]
            except Exception as e:
                logger.debug(f"Sprint 8VA GraphRAG skipped: {e}")

        # Sestavit prompt z top findings
        findings_text = "\n".join(
            f"- [{f.get('source_type', '?')}] {f.get('text', '')[:200]}"
            for f in top
        )

        # Sprint 8VA B.2 + C.2: Sestavit synthesis prompt s RAG + GraphRAG context
        context_parts = []
        if episode_ctx:
            context_parts.append(episode_ctx)
        if rag_context:
            context_parts.append(rag_context)
        if graph_context:
            context_parts.append(graph_context)

        if context_parts:
            prompt = (
                f"{chr(10).join(context_parts)}\n\n---\n"
                f"Query: {query}{stix_context}\n"
                f"Findings:\n{findings_text}\n"
                f"Current timestamp: {time.time()}"
            )
        else:
            prompt = (
                f"Query: {query}{stix_context}\n"
                f"Findings:\n{findings_text}\n"
                f"Current timestamp: {time.time()}"
            )

        # Sprint F234: DSPy optimized prompts — try to load from cache first
        dspy_prompts = _get_dspy_prompts()
        if dspy_prompts:
            dspy_opt = _get_dspy_optimizer()
            if dspy_opt is not None:
                try:
                    # Check for optimized prompt for analysis task
                    optimized = dspy_opt.get_prompt('analysis', {'complexity': 'medium'})
                    if optimized:
                        self.set_custom_prompt(optimized)
                        logger.info(f"[SYNTHESIS] DSPy optimized prompt loaded ({len(optimized)} chars)")
                except Exception:
                    pass
            # Fallback: use cached prompts directly
            elif dspy_prompts.get('analysis:medium'):
                self.set_custom_prompt(dspy_prompts['analysis:medium'])

        # Sprint F234: Bandit arm selection — select before generation, apply modifier to prompt
        bandit = _get_prompt_bandit()
        arm_used = ""
        if bandit is not None:
            try:
                arm_used = bandit.select_arm()
                modifier = bandit.get_prompt_modifier(arm_used)
                self.set_prompt_modifier(modifier)
                self._last_arm = arm_used
                logger.info(f"[SYNTHESIS] Bandit selected arm: {arm_used}")
            except Exception as e:
                logger.debug(f"[SYNTHESIS] Bandit select failed: {e}")
                arm_used = ""

        # Sprint F234: Append bandit modifier to prompt if set
        if self._prompt_modifier:
            prompt = prompt.rstrip() + self._prompt_modifier + "\n"

        raw_dict = None
        used_engine = "none"
        try:
            # F234: Context compression — compress prompt if it exceeds threshold
            if self._compression_threshold > 0 and self._compressor is not None:
                prompt_len = len(prompt)
                if prompt_len > self._compression_threshold:
                    try:
                        compressed = await self._compressor.compress_context(prompt)
                        # Use critical content tier (most concise)
                        compressed_prompt = compressed.critical_content
                        logger.info(
                            f"[SYNTHESIS] Context compressed: {prompt_len} → {len(compressed_prompt)} chars "
                            f"(ratio={compressed.compression_ratio:.2f})"
                        )
                        prompt = compressed_prompt
                    except Exception as e:
                        # F234: fail-soft — synthesis continues with original prompt
                        logger.warning(f"[SYNTHESIS] Context compression failed (using original prompt): {e}")

            # Sprint 8UC B.1 + B.3: Cascade: xgrammar → streaming → constrained
            result_tuple = await self._run_xgrammar_generation(prompt)
            if result_tuple is not None:
                raw_dict, xgr_ok = result_tuple
                if xgr_ok:
                    used_engine = "xgrammar"

            # Fallback 1: streaming
            if raw_dict is None:
                result_tuple = await self._run_streaming_generation(
                    prompt, json_schema=OSINT_JSON_SCHEMA
                )
                if result_tuple is not None:
                    raw_dict, str_ok = result_tuple
                    if str_ok:
                        used_engine = "streaming"

            # Fallback 2: constrained via lifecycle's structured_generate
            if raw_dict is None:
                raw_dict, outlines_ok = await self._lifecycle.structured_generate(
                    prompt, OSINT_JSON_SCHEMA
                )
                if raw_dict is not None:
                    used_engine = "constrained"
        except Exception as e:
            logger.error("Synthesis error: %s", e)
            self._last_synthesis_outcome = SynthesisOutcome(
                status="failed",
                primary_reason="generation_failed",
                lifecycle_gate_source=getattr(self, "_lifecycle_gate_source", "unknown"),
                lifecycle_gate_mode=getattr(self, "_lifecycle_gate_mode", "unknown"),
                stix_status=self._stix_status,
                stix_reason=f"synthesis engine raised {type(e).__name__}: {e}",
                stix_backend=self._stix_backend,
                engine_used=used_engine,
                findings_considered=findings_count,
                report_produced=False,
                confidence=0.0,
                operator_note=f"exception during generation: {e}",
            )
            return None
        finally:
            # B.4: unload + cleanup v přesném pořadí
            await self._lifecycle.unload()
            gc.collect()

        # Log engine used
        logger.info(f"[SYNTHESIS] Engine used: {used_engine}")
        self._last_synthesis_engine = used_engine

        if raw_dict is not None:
            # Sprint 8TA B.1: _parse_raw_to_osintreport s defaulty
            used_outlines = used_engine in ("streaming", "constrained")
            report = self._parse_raw_to_osintreport(raw_dict)
            if report is not None:
                report.confidence = self._compute_confidence(report, used_outlines)

                # GAP-8: Evidence grounding validation (fail-soft)
                _, grounding_warnings = validate_evidence_grounding(report, findings)
                if grounding_warnings:
                    logger.warning(
                        f"[SYNTHESIS] GAP-8 grounding warnings: "
                        f"{len(grounding_warnings)} unverified IOCs"
                    )

                # GAP-7: Semantic constraint validation (fail-soft — log only, never block)
                sem_ok, sem_errors = validate_report_semantics(report)
                if not sem_ok:
                    logger.warning(f"[SYNTHESIS] GAP-7 semantic errors: {sem_errors}")

                # Sprint F234: Update bandit UCB1 reward — reward = response_length_normalized × confidence
                # Note: LinUCB update() is NOT called — select_arm() uses UCB1 algorithm.
                # UCB1 state (arm_counts, arm_rewards) requires persistence fix — see prompt_bandit.py.
                if bandit is not None and arm_used:
                    try:
                        response_text = (
                            report.threat_summary + " " +
                            " ".join(str(e) for e in report.ioc_entities) +
                            " ".join(report.threat_actors)
                        )
                        response_len_norm = min(1.0, len(response_text) / 2000.0)  # 2k chars = 1.0
                        reward = response_len_norm * report.confidence
                        bandit.update_reward(arm_used, reward, reward)
                        logger.info(f"[SYNTHESIS] Bandit reward: arm={arm_used} reward={reward:.3f}")
                    except Exception as e:
                        logger.debug(f"[SYNTHESIS] Bandit update failed: {e}")

                self._last_synthesis_outcome = SynthesisOutcome(
                    status="success",
                    primary_reason="success",
                    lifecycle_gate_source=getattr(self, "_lifecycle_gate_source", "unknown"),
                    lifecycle_gate_mode=getattr(self, "_lifecycle_gate_mode", "unknown"),
                    stix_status=self._stix_status,
                    stix_reason=self._stix_reason,
                    stix_backend=self._stix_backend,
                    engine_used=used_engine,
                    findings_considered=findings_count,
                    report_produced=True,
                    confidence=report.confidence,
                    operator_note=f"report produced with confidence {report.confidence:.3f}",
                )
                # F214: Extract testable hypotheses from synthesis output
                # Fail-soft: hypothesis pipeline error must not affect canonical report
                if self._hypothesis_engine is not None:
                    try:
                        ctx = {
                            "query": query,
                            "report_summary": report.threat_summary[:500] if report.threat_summary else "",
                            "iocs": [i.ioc_value for i in (report.ioc_entities or [])[:10]],
                            "source": "synthesis_runner",
                        }
                        hyp_strings = await self._hypothesis_engine.generate_hypotheses_async(
                            context=ctx,
                            hermes_engine=getattr(self._hypothesis_engine, "_inference_engine", None),
                        )
                        if hyp_strings:
                            logger.debug(
                                f"[SYNTHESIS] Extracted {len(hyp_strings[:10])} hypotheses from report"
                            )
                    except Exception as e:
                        logger.debug(f"[SYNTHESIS] Hypothesis extraction skipped: {e}")

                return report

        # All engines failed or parse failed
        self._last_synthesis_outcome = SynthesisOutcome(
            status="failed",
            primary_reason="generation_failed" if raw_dict is None else "parse_failed",
            lifecycle_gate_source=getattr(self, "_lifecycle_gate_source", "unknown"),
            lifecycle_gate_mode=getattr(self, "_lifecycle_gate_mode", "unknown"),
            stix_status=self._stix_status,
            stix_reason="all engines exhausted" if raw_dict is None else "raw dict parse returned None",
            stix_backend=self._stix_backend,
            engine_used=used_engine,
            findings_considered=findings_count,
            report_produced=False,
            confidence=0.0,
            operator_note=f"engines={used_engine}, raw_dict={'set' if raw_dict is not None else 'None'}",
        )
        return None

    async def close(self) -> None:
        """Clean close — volá se po syntéze."""
        # Ensure any pending lifecycle resources are released
        try:
            await self._lifecycle.unload()
        except Exception:
            pass
        # Sprint F234: Persist bandit state on shutdown
        bandit = _get_prompt_bandit()
        if bandit is not None:
            try:
                await bandit.final_save()
            except Exception:
                pass
        gc.collect()

    # ------------------------------------------------------------------
    # Sprint 8TC B.3: Streaming synthesis s early-exit
    # ------------------------------------------------------------------

    async def _run_streaming_generation(
        self,
        prompt: str,
        json_schema: str | None = None,  # unused — regex early-exit path
    ) -> tuple[dict | None, bool] | None:
        """
        Sprint 8TC B.3: mlx_lm stream_generate s early-exit při kompletním JSON.

        Fallback na regex JSON extract z akumulovaného textu.
        M1: vše sync v CPU_EXECUTOR — NIKDY přímo v event loop.

        Returns:
            (dict | None, outlines_used: bool) — stejný formát jako structured_generate
        """
        import re as _re
        import json as _json

        try:
            model, tokenizer, _model_path = await self._lifecycle._ensure_loaded()
        except RuntimeError as e:
            logger.warning("[SYNTHESIS] Model load failed: %s", e)
            return None

        if self._custom_synthesis_prompt:
            # DSPy MIPROv2 optimized prompt takes precedence over default
            system_prompt = self._custom_synthesis_prompt
        else:
            system_prompt = (
                "You are a cybersecurity analyst. "
                "Extract IOC entities from findings. "
                "Respond with valid JSON matching the schema exactly."
            )
        full_prompt = f"<|system|>{system_prompt}<|user|>{prompt}<|assistant|>"

        # Pokus o chat template
        try:
            if hasattr(tokenizer, "apply_chat_template"):
                m = _re.search(r"<\|system\|>(.*?)<\|user\|>(.*?)<\|assistant\|>", full_prompt, _re.DOTALL)
                if m:
                    system_text = m.group(1).strip()
                    user_text = m.group(2).strip()
                else:
                    system_text = "You are a cybersecurity analyst. Respond with JSON only."
                    user_text = full_prompt
                messages = [
                    {"role": "system", "content": system_text},
                    {"role": "user", "content": user_text},
                ]
                formatted = tokenizer.apply_chat_template(
                    messages, tokenize=False, add_generation_prompt=True
                )
            else:
                formatted = full_prompt
        except Exception:
            formatted = full_prompt

        def _stream_sync() -> tuple[dict | None, bool]:
            import mlx_lm

            accumulated = ""
            if hasattr(mlx_lm, "stream_generate"):
                try:
                    for chunk in mlx_lm.stream_generate(
                        model,
                        tokenizer,
                        prompt=formatted,
                        max_tokens=512,
                        verbose=False,
                    ):
                        tok = chunk.text if hasattr(chunk, "text") else str(chunk)
                        accumulated += tok
                        # Early-exit: hledáme kompletní JSON objekt s "title"
                        m_match = _re.search(r'\{[^{}]{20,}"title"[^{}]*\}', accumulated, _re.DOTALL)
                        if m_match:
                            try:
                                return _json.loads(m_match.group()), True
                            except _json.JSONDecodeError:
                                pass  # neúplný — pokračuj
                except Exception as e:
                    logger.warning("[SYNTHESIS] stream_generate failed: %s — fallback", e)
                    accumulated = ""

            # Fallback: regex JSON extract z akumulovaného textu
            if accumulated:
                m_final = _re.search(r'\{.*\}', accumulated, _re.DOTALL)
                if m_final:
                    try:
                        return _json.loads(m_final.group()), True
                    except Exception:
                        pass

            return (None, False)

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _stream_sync)

    # ------------------------------------------------------------------
    # Sprint 8UC B.1: xgrammar guaranteed-JSON synthesis
    # ------------------------------------------------------------------

    async def _run_xgrammar_generation(
        self,
        prompt: str,
    ) -> tuple[dict | None, bool]:
        """
        Sprint 8UC B.1: xgrammar guaranteed-JSON synthesis.

        Uses XGrammarLogitsProcessor for 100% valid JSON guarantee.
        Falls back to (None, False) on any error — caller handles cascade.
        """
        import json as _json

        # Load model BEFORE executor (same pattern as _run_streaming_generation)
        try:
            model, tokenizer, _model_path = await self._lifecycle._ensure_loaded()
        except RuntimeError as e:
            logger.warning("[SYNTHESIS] xgrammar model load failed: %s", e)
            return None, False

        def _xgrammar_sync() -> tuple[dict | None, bool]:
            try:
                import xgrammar as xgr
                import mlx_lm

                # Use cached grammar compilation (Sprint 8UF B.1)
                schema = _build_osint_json_schema()
                schema_str = _json.dumps(schema, sort_keys=True)
                grammar = _get_cached_grammar(schema_str, tokenizer)

                # Build logits processor via contrib.hf
                try:
                    processor = xgr.contrib.hf.LogitsProcessor(grammar, tokenizer)
                except (AttributeError, TypeError):
                    # Fallback: use grammar directly if LogitsProcessor unavailable
                    return None, False

                # Format prompt
                system_prompt = "You are a cybersecurity analyst. Respond with valid JSON only."
                try:
                    if hasattr(tokenizer, "apply_chat_template"):
                        messages = [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": prompt},
                        ]
                        formatted = tokenizer.apply_chat_template(
                            messages, tokenize=False, add_generation_prompt=True
                        )
                    else:
                        formatted = f"<|system|>{system_prompt}<|user|>{prompt}<|assistant|>"
                except Exception:
                    formatted = prompt

                # Generate with xgrammar logits processor
                output = None
                try:
                    try:
                        output = mlx_lm.generate(
                            model, tokenizer,
                            prompt=formatted,
                            max_tokens=512,
                            logits_processors=[processor],
                            verbose=False,
                        )
                    except TypeError:
                        # Old mlx_lm without logits_processors
                        output = mlx_lm.generate(
                            model, tokenizer,
                            prompt=formatted,
                            max_tokens=512,
                            verbose=False,
                        )
                finally:
                    # Sprint 8UD B.2: Clear MLX Metal cache after inference
                    try:
                        import mlx.core as _mx
                        if _mx.metal.is_available():
                            _mx.metal.clear_cache()
                    except Exception:
                        pass  # Non-fatal

                result = _json.loads(output)
                if "title" in result and "summary" in result:
                    return result, True
                return None, False

            except ImportError:
                return None, False
            except Exception as e:
                logger.warning(f"[SYNTHESIS] xgrammar generation: {e}")
                return None, False

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _xgrammar_sync)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _ensure_model(self) -> Optional[Path]:
        """
        Sprint 8SB: 3-tier model discovery with conditional download.

        Tier 1: cached path from previous call
        Tier 2: scan ~/.cache/huggingface/hub and ~/.mlx for existing models
        Tier 3: download Qwen2.5-0.5B-Instruct-4bit (~400MB) then SmolLM2-135M fallback (~70MB)

        Returns Path to model or None if unavailable.
        """
        # Tier 1: reuse cached path
        if self._cached_model_path is not None:
            if self._cached_model_path.exists():
                return self._cached_model_path
            self._cached_model_path = None

        # Tier 2: scan disk
        search = [Path.home() / ".cache" / "huggingface" / "hub", Path.home() / ".mlx"]
        for d in search:
            if not d.exists():
                continue
            for pat in [
                "**/Qwen2.5*0.5B*/config.json",
                "**/*0.5B*/config.json",
                "**/*135M*/config.json",
                "**/SmolLM2*135M*/config.json",
            ]:
                hits = await asyncio.to_thread(lambda: list(d.glob(pat)))
                if hits:
                    self._cached_model_path = hits[0].parent
                    logger.info("[SYNTHESIS] Model found: %s", self._cached_model_path.name)
                    return self._cached_model_path

        # Tier 3: download with fallback
        for model_id, max_gb in [
            ("mlx-community/Qwen2.5-0.5B-Instruct-4bit", 1.0),
            ("mlx-community/SmolLM2-135M-Instruct-4bit", 0.2),
        ]:
            try:
                api_url = f"https://huggingface.co/api/models/{model_id}"
                r = await asyncio.to_thread(urllib.request.urlopen, api_url, timeout=15)
                with r:
                    data = _json.loads(r.read())
                    total = sum(f.get("size", 0) for f in data.get("siblings", []))
                if total / 1e9 > max_gb:
                    continue
                logger.info(
                    "[SYNTHESIS] Downloading %s (%.0fMB) ...",
                    model_id,
                    total / 1e6,
                )
                import mlx_lm

                await asyncio.to_thread(mlx_lm.utils.snapshot_download, model_id)
                logger.info("[SYNTHESIS] Download complete: %s", model_id)
                # Re-scan disk
                for d in search:
                    for pat in ["**/config.json"]:
                        hits = await asyncio.to_thread(lambda: list(d.glob(pat)))
                        if hits:
                            self._cached_model_path = hits[0].parent
                            return self._cached_model_path
            except Exception as e:
                logger.warning("[SYNTHESIS] Model download failed for %s: %s", model_id, e)
        return None

    def _compute_confidence(
        self,
        report: "OSINTReport",
        used_outlines: bool,
    ) -> float:
        """
        Sprint 8SB: Synthesis quality confidence score 0.0–1.0.

        B.8 scoring:
          base = 0.3 (any output)
          +0.20 if threat_actors non-empty
          +0.20 if any CVE mention in ioc_entities
          +0.15 if all required OSINTReport fields non-empty
          +0.15 if Outlines constrained (not free-text fallback)
        """
        score = 0.30
        actors = getattr(report, "threat_actors", None)
        if actors:
            score += 0.20
        # Check for CVE mentions in IOC entities
        iocs = getattr(report, "ioc_entities", None) or []
        if any("CVE" in str(e.value) for e in iocs if hasattr(e, "value")):
            score += 0.20
        # Track if we got any content bonus (for Outlines bonus gate)
        has_content = bool(actors) or any("CVE" in str(e.value) for e in iocs if hasattr(e, "value"))
        # All required fields: query/threat_summary non-empty strings,
        # ioc_entities non-None, sources_count >= 1, timestamp > 0
        q = getattr(report, "query", None)
        ts = getattr(report, "threat_summary", None)
        ie = getattr(report, "ioc_entities", None)
        sc = getattr(report, "sources_count", None)
        tm = getattr(report, "timestamp", None)
        if (
            q is not None and isinstance(q, str) and q
            and ts is not None and isinstance(ts, str) and ts
            and ie is not None
            and sc is not None and sc >= 1
            and tm is not None and tm > 0
        ):
            score += 0.15
        if used_outlines and has_content:
            # Outlines bonus only when report has real content (threat_actors or CVE)
            score += 0.15
        return min(1.0, round(score, 3))

    def _is_windup_allowed(self, force: bool) -> bool:
        """
        B.7: Check windup phase or force flag.

        SPRINT 8VL: Lifecycle gate truth — prefer runtime lifecycle, compat fallback.

        Truth priority:
          1. Injected runtime lifecycle adapter (_lifecycle_adapter) — SET by windup_engine
          2. Runtime sprint_lifecycle.SprintLifecycleManager.get_instance() — preferred
          3. utils.sprint_lifecycle.SprintLifecycleManager.get_instance() — COMPAT fallback

        Sets structured state BEFORE returning:
          _lifecycle_gate_source: "runtime" | "compat" | "unavailable"
          _lifecycle_gate_mode: "windup" | "forced" | "blocked"

        Force flag: always returns True, sets mode="forced", source="n/a".
        """
        # Force path — always allowed, no lifecycle truth needed
        if force:
            self._lifecycle_gate_source = "forced"
            self._lifecycle_gate_mode = "forced"
            return True

        # Path 1: injected runtime lifecycle adapter (windup_engine path)
        if self._lifecycle_adapter is not None:
            try:
                should_windup = self._lifecycle_adapter.should_enter_windup()
                self._lifecycle_gate_source = "runtime"
                self._lifecycle_gate_mode = "windup" if should_windup else "blocked"
                return should_windup
            except Exception:
                pass  # Fall through to Path 2

        # Path 2: runtime sprint_lifecycle (canonical) — no singleton, it's a dataclass
        # Runtime manager is created by __main__ and passed to scheduler; we check if it
        # was injected as _runtime_lifecycle attribute on self (set by windup_engine)
        try:
            from ..runtime.sprint_lifecycle import SprintLifecycleManager as RuntimeLC
            for _name in ("_runtime_lifecycle", "_lc"):
                if hasattr(self, _name):
                    lc = getattr(self, _name)
                    if isinstance(lc, RuntimeLC):
                        should_windup = lc.should_enter_windup()
                        self._lifecycle_gate_source = "runtime"
                        self._lifecycle_gate_mode = "windup" if should_windup else "blocked"
                        return should_windup
        except Exception:
            pass  # Fall through to Path 3

        # Path 3: utils.sprint_lifecycle (COMPAT fallback — labeled as such)
        try:
            from ..utils.sprint_lifecycle import SprintLifecycleManager
            manager = SprintLifecycleManager.get_instance()
            should_windup = manager.is_windup_phase()
            self._lifecycle_gate_source = "compat"
            self._lifecycle_gate_mode = "windup" if should_windup else "blocked"
            return should_windup
        except Exception:
            self._lifecycle_gate_source = "unavailable"
            self._lifecycle_gate_mode = "blocked"
            self._stix_status = "unavailable"
            self._stix_reason = "lifecycle unavailable — all lookup paths failed"
            self._stix_backend = ""
            return False

    def _check_uma_guard(self) -> bool:
        """
        B.7: RSS > 5.5GiB → skip synthesis (M1 8GB UMA safety).
        Also checks EMERGENCY state via evaluate_uma_state.
        """
        try:
            from ..core.resource_governor import evaluate_uma_state, sample_uma_status
            status = sample_uma_status()
            if status.rss_gib > 5.5:
                logger.warning("[SYNTHESIS] Skipped: RSS %.1fGiB > 5.5GiB", status.rss_gib)
                return False
            state = evaluate_uma_state(status.system_used_gib)
            if state == "emergency":
                logger.warning("[SYNTHESIS] Skipped: UMA EMERGENCY")
                return False
            return True
        except Exception:
            return True  # fail-open

    def _parse_raw_to_osintreport(self, raw: dict) -> OSINTReport | None:
        """
        Sprint 8TA B.1: Safe parsing of raw dict into OSINTReport.

        Uses raw.get() for every field with defaults for missing values.
        Maps json_schema fields (title/summary/findings) to OSINTReport fields
        (threat_summary/ioc_entities/sources_count).
        """
        try:
            title = raw.get("title", "OSINT Synthesis")
            summary = raw.get("summary", "")
            threat_actors = raw.get("threat_actors") or []
            findings = raw.get("findings") or []
            confidence = raw.get("confidence", 0.0)
            timestamp = raw.get("timestamp", time.time())

            # Map findings list to IOCEntity list
            ioc_entities: list[IOCEntity] = []
            for f in findings[:20]:  # max 20
                if isinstance(f, str):
                    ioc_entities.append(IOCEntity(
                        value=f[:100],
                        ioc_type=_infer_ioc_type(f),
                        severity="medium",
                        context=f[:200],
                    ))

            return OSINTReport(
                query=title,
                ioc_entities=ioc_entities,
                threat_summary=summary[:500] if summary else "",
                threat_actors=threat_actors[:10],
                confidence=float(confidence) if confidence else 0.0,
                sources_count=len(findings),
                timestamp=float(timestamp) if timestamp else time.time(),
            )
        except Exception as e:
            logger.warning("[SYNTHESIS] _parse_raw_to_osintreport failed: %s", e)
            return None

    # ── Sprint 8TB: Query Decomposer ────────────────────────────────────

    async def decompose_query(
        self,
        query: str,
        model=None,
        tokenizer=None,
    ) -> list[str]:
        """
        Decompose query into 3-5 sub-queries. Max 80 tokens.

        Identity fallback if model is None.
        Uses CPU_EXECUTOR for sync MLX inference.
        """
        if model is None or tokenizer is None:
            logger.debug("decompose_query: no model → identity fallback")
            return [query]

        PROMPT = (
            "You are a security OSINT assistant. "
            "Generate 3-5 specific search queries for: {q}\n"
            "Output ONLY a JSON array of strings, no explanation.\n"
            'Example: ["LockBit IOCs 2026","LockBit C2 infra","LockBit victims list"]'
        ).format(q=query)

        def _gen() -> list[str]:
            try:
                import re, json, mlx_lm
                msgs = [{"role": "user", "content": PROMPT}]
                prompt_str = tokenizer.apply_chat_template(
                    msgs, tokenize=False, add_generation_prompt=True,
                )
                out = mlx_lm.generate(
                    model, tokenizer,
                    prompt=prompt_str,
                    max_tokens=80,
                    verbose=False,
                )
                m = re.search(r'\[.*?\]', out, re.DOTALL)
                if m:
                    parsed = json.loads(m.group())
                    if isinstance(parsed, list) and parsed:
                        return [str(s) for s in parsed[:5]]
            except Exception as e:
                logger.warning(f"decompose_query generate: {e}")
            finally:
                # Sprint 8UD B.2: Clear MLX Metal cache after inference
                try:
                    import mlx.core as _mx
                    if _mx.metal.is_available():
                        _mx.metal.clear_cache()
                except Exception:
                    pass  # Non-fatal
            return [query]

        loop = asyncio.get_running_loop()
        from concurrent.futures import ThreadPoolExecutor
        _CPU_EXECUTOR = ThreadPoolExecutor(max_workers=1)
        try:
            result = await loop.run_in_executor(_CPU_EXECUTOR, _gen)
        finally:
            _CPU_EXECUTOR.shutdown(wait=False)
        logger.info(f"decompose_query '{query[:40]}' → {len(result)} sub-queries")
        return result

    # ── Sprint 8TB: Ghost Global Context ─────────────────────────────────

    async def _load_global_context(self) -> str:
        """
        Load top-10 recurring entities from ghost_global.duckdb as context.

        Returns empty string if DB doesn't exist or on any error.
        """
        try:
            from ..paths import RAMDISK_ROOT
            import duckdb

            ghost_path = RAMDISK_ROOT / "db" / "ghost_global.duckdb"
            if not ghost_path.exists():
                return ""
            conn = duckdb.connect(str(ghost_path), read_only=True)
            rows = conn.execute("""
                SELECT entity_value, entity_type, sprint_count, confidence_cumulative
                FROM global_entities
                ORDER BY sprint_count DESC, confidence_cumulative DESC
                LIMIT 10
            """).fetchall()
            conn.close()
            if not rows:
                return ""
            lines = ["Recurring entities from prior sprints:"]
            for val, typ, cnt, conf in rows:
                lines.append(f"  [{typ}] {val} (seen {cnt}x, conf={conf:.2f})")
            return "\n".join(lines)
        except Exception as e:
            logger.debug(f"global_context load: {e}")
            return ""

    # ── Sprint 8UC B.2.3: Episode Context ─────────────────────────────────

    async def _build_episode_context(self, store, query: str) -> str:
        """Sprint 8UC B.2.3: Načíst relevantní epizody a sestavit context string."""
        if store is None or not hasattr(store, "recall_episodes"):
            return ""
        try:
            episodes = await store.recall_episodes(None, limit=5)
        except Exception:
            return ""
        if not episodes:
            return ""
        import orjson
        lines = ["Past research context (most recent first):"]
        for ep in episodes[:3]:
            findings_raw = ep.get("top_findings", "")
            try:
                findings = orjson.loads(findings_raw) if isinstance(findings_raw, str) else findings_raw
            except Exception:
                findings = []
            ep_query = ep.get("query", "")[:60]
            lines.append(f"  Sprint {ep.get('sprint_id','')}: query='{ep_query}'")
            if findings and isinstance(findings, list) and len(findings) > 0:
                lines.append(f"    Key finding: {findings[0][:120]}")
        return "\n".join(lines)

    # ── Sprint 8TA: STIX Context ───────────────────────────────────────────
    # Sprint 8TH: STRUCTURED DEGRADATION — stix_status/stix_reason replaces silent "" return

    # _stix_status, _stix_reason, _stix_backend declared in __slots__
    # Initialized in __init__ — see there

    async def _build_stix_context(self) -> str:
        """
        B.6: STIX context z ioc_graph.export_stix_bundle() pokud dostupný.

        SPRINT 8VQ: Truth-store priority path via _stix_graph (inject_stix_graph).
        SPRINT 8TH: Returns empty string on degradation, BUT sets structured
        instance attributes FIRST so caller can audit why:

          _stix_status  = "available" | "unavailable" | "error"
          _stix_reason  = concrete reason string (not a generic message)
          _stix_backend = backend class name if safe to extract

        Graph priority (Sprint 8VQ):
          1. _stix_graph — dedicated truth-store STIX slot (IOCGraph/Kuzu only)
          2. _ioc_graph — analytics/donor fallback (DuckPGQGraph — no STIX)

        Truth store (IOCGraph/Kuzu) HAS export_stix_bundle (async).
        Donor backend (DuckPGQGraph/DuckDB) DOES NOT.
        """
        # Sprint 8VQ: Priority 1 — dedicated truth-store STIX graph
        stix_graph = self._stix_graph
        if stix_graph is not None:
            try:
                export_fn = getattr(stix_graph, "export_stix_bundle", None)
                if export_fn is None:
                    backend_name = type(stix_graph).__name__
                    self._stix_status = "unavailable"
                    self._stix_reason = f"stix_graph '{backend_name}' lacks export_stix_bundle"
                    self._stix_backend = backend_name
                    return ""
                nodes = await export_fn()
                if not nodes:
                    self._stix_status = "available"
                    self._stix_reason = "stix_graph export_stix_bundle returned empty — graph has no IOC nodes"
                    self._stix_backend = type(stix_graph).__name__
                    return ""
                values = [n.get("value", "") for n in nodes[:20] if isinstance(n, dict)]
                if values:
                    self._stix_status = "available"
                    self._stix_reason = f"stix_graph exported {len(nodes)} nodes, truncated to {len(values)} for prompt"
                    self._stix_backend = type(stix_graph).__name__
                    return f"\nKnown IOCs from graph ({len(values)} entities): {', '.join(values)}"
                else:
                    self._stix_status = "available"
                    self._stix_reason = "stix_graph export_stix_bundle returned nodes but none had extractable 'value' field"
                    self._stix_backend = type(stix_graph).__name__
                    return ""
            except Exception as e:
                self._stix_status = "error"
                self._stix_reason = f"stix_graph STIX export raised {type(e).__name__}: {e}"
                self._stix_backend = type(stix_graph).__name__
                return ""

        # Sprint 8VQ: Priority 2 — analytics/donor graph (DuckPGQGraph — no STIX)
        if self._ioc_graph is None:
            self._stix_status = "unavailable"
            self._stix_reason = "no graph injected — both _stix_graph and _ioc_graph are None"
            self._stix_backend = ""
            return ""
        try:
            export_fn = getattr(self._ioc_graph, "export_stix_bundle", None)
            if export_fn is None:
                backend_name = type(self._ioc_graph).__name__
                self._stix_status = "unavailable"
                self._stix_reason = f"backend '{backend_name}' lacks export_stix_bundle — DuckPGQGraph donor cannot serve STIX"
                self._stix_backend = backend_name
                return ""
            # IOCGraph.export_stix_bundle is async; DuckPGQGraph lacks it entirely
            nodes = await export_fn()
            if not nodes:
                self._stix_status = "available"
                self._stix_reason = "export_stix_bundle returned empty — graph has no IOC nodes"
                self._stix_backend = type(self._ioc_graph).__name__
                return ""
            values = [n.get("value", "") for n in nodes[:20] if isinstance(n, dict)]
            if values:
                self._stix_status = "available"
                self._stix_reason = f"exported {len(nodes)} nodes, truncated to {len(values)} for prompt"
                self._stix_backend = type(self._ioc_graph).__name__
                return f"\nKnown IOCs from graph ({len(values)} entities): {', '.join(values)}"
            else:
                self._stix_status = "available"
                self._stix_reason = "export_stix_bundle returned nodes but none had extractable 'value' field"
                self._stix_backend = type(self._ioc_graph).__name__
                return ""
        except Exception as e:
            self._stix_status = "error"
            self._stix_reason = f"STIX export raised {type(e).__name__}: {e}"
            self._stix_backend = type(self._ioc_graph).__name__
            return ""


# ---------------------------------------------------------------------------
# E2E export helper (volá se z __main__.py)
# ---------------------------------------------------------------------------


def slugify(s: str) -> str:
    """Bez-dependency slugify pro export filename."""
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


async def export_report(
    report: OSINTReport,
    query: str,
    reports_dir: Path | None = None,
) -> Path:
    """
    Export OSINTReport do JSON souboru.

    B.10: E2E export path = ~/.hledac/reports/{timestamp}_{slug(query)}_report.json
    Vytvoří adresář pokud neexistuje (parents=True, exist_ok=True).
    """
    if reports_dir is None:
        reports_dir = Path.home() / ".hledac" / "reports"

    reports_dir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    filename = f"{ts}_{slugify(query)[:40]}_report.json"
    out_path = reports_dir / filename

    # msgspec → JSON bytes → decode string → write
    content = msgspec.json.encode(report).decode("utf-8")
    out_path.write_text(content, encoding="utf-8")
    logger.info("Sprint report saved: %s", out_path)
    return out_path
