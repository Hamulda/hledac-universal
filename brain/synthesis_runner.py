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
import hashlib
import logging
import os
import re
import threading
import time
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any

from hledac.universal.utils.async_helpers import safe_create_task, safe_gather_fire_and_forget, safe_gather_ok
from hledac.universal.utils.cache import PyCacheDict
from hledac.universal.utils.msgspec_json import decode as _msgspec_decode
from hledac.universal.utils.msgspec_json import encode as _msgspec_encode

# Precompiled regex patterns — compile once, use repeatedly
_MML_TAG_RE = re.compile(r"<\|system\|>(.*?)<\|user\|>(.*?)<\|assistant\|>", re.DOTALL)
_JSON_OBJ_RE = re.compile(r'\{[^{}]{20,}"title"[^{}]*\}', re.DOTALL)
_JSON_FINAL_RE = re.compile(r'\{.*\}', re.DOTALL)
_BRACKET_RE = re.compile(r'\[.*?\]', re.DOTALL)
_SLUGIFY_RE = re.compile(r"[^a-z0-9]+")

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
# Issue #20 improvement: Adaptive KV cache for M1 8GB Metal memory
# Mirrors DeepHermes3Engine._get_kv_cache_kwargs() + _get_adaptive_kv_bits()
# ---------------------------------------------------------------------------

def _synthesis_get_metal_tier_thresholds() -> tuple[int, int, int]:
    """
    Probes Rust FFI get_metal_limit_bytes_py() for dynamic M1 Metal cache ceiling.
    Fallback: static M1 8GB values.
    """
    try:
        from hledac.universal import rust_extensions as _rust
        limit_bytes = _rust.get_metal_limit_bytes_py()
        if limit_bytes > 0:
            return (
                int(limit_bytes * 1.75),  # emergency
                int(limit_bytes * 1.05),  # critical
                int(limit_bytes * 0.70),  # warn
            )
    except Exception:
        pass
    return (
        2_684_354_560,  # emergency = 2.5 GiB
        1_610_612_736,  # critical = 1.5 GiB
        1_073_741_824,  # warn = 1.0 GiB
    )


# ---------------------------------------------------------------------------
# Sprint 8UF B.1: xgrammar grammar cache — compile ONCE per schema lifetime
# ---------------------------------------------------------------------------
import re as _re_synth  # noqa: E402

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
    report: OSINTReport,
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


def validate_report_semantics(report: OSINTReport) -> tuple[bool, list[str]]:
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
        if not ioc_entities and sc is not None and int(sc) > 0:
            errors.append(
                f"ioc_entities empty but sources_count={sc} — possible generation failure")

        threat_summary = getattr(report, 'threat_summary', None)
        if (not threat_summary or not isinstance(threat_summary, str)
                or not threat_summary.strip()):
            errors.append("threat_summary is empty or whitespace-only")

    except Exception as e:
        logger.debug(f"validate_report_semantics exception (fail-soft): {e}")
        return (True, [])  # fail-soft on introspection error

    return (not errors, errors)


# F3.2: PyCacheDict replaces manual dict+RLock — bounded + TTL + thread-safe
_GRAMMAR_CACHE: PyCacheDict[str, object] = PyCacheDict(256, 600.0)


# Issue #12.6: Thread-safe grammar compilation lock
_GRAMMAR_BUILD_LOCK = threading.Lock()


def _get_cached_grammar(schema_json_str: str, tokenizer) -> object:
    """Compile JSON Schema grammar ONLY on first call per schema (idempotent).

    Thread-safe via PyCacheDict internal lock + explicit threading.Lock around
    xgr.TokenizerInfo.from_huggingface() (not thread-safe on M1 Metal).
    Cache key = SHA-256 of first 256 schema chars.
    """
    import xgrammar as xgr

    key = hashlib.sha256(schema_json_str[:256].encode()).hexdigest()[:16]
    cached = _GRAMMAR_CACHE.get(key)
    if cached is not None:
        return cached
    # Issue #12.6: Serialize xgrammar TokenizerInfo compilation — not thread-safe
    with _GRAMMAR_BUILD_LOCK:
        # Double-check after acquiring lock
        cached = _GRAMMAR_CACHE.get(key)
        if cached is not None:
            return cached
        tokenizer_info = xgr.TokenizerInfo.from_huggingface(tokenizer)
        compiler = xgr.GrammarCompiler(tokenizer_info)
        grammar = compiler.compile_json_schema(schema_json_str)
        _GRAMMAR_CACHE.set(key, grammar)
    return grammar


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
# F3.2: PyCacheDict replaces lru_cache — bounded + TTL + thread-safe
# (PyCacheDict already imported at L36)
# Thread-safe singleton init with double-check locking
_optimizer_init_lock = threading.Lock()
_dspy_optimizer_cache: PyCacheDict[None, object] = PyCacheDict(1, 300.0)
_prompt_bandit_cache: PyCacheDict[None, object] = PyCacheDict(1, 300.0)


def _get_dspy_optimizer(lifecycle=None):
    """Lazy init DSPyOptimizer — starts background optimization loop on first call."""
    # F3.2: PyCacheDict with double-check locking (singleton per process)
    cached = _dspy_optimizer_cache.get(None)
    if cached is not None:
        return cached
    with _optimizer_init_lock:
        # Double-check after acquiring lock
        cached = _dspy_optimizer_cache.get(None)
        if cached is not None:
            return cached
        try:
            from brain.dspy_optimizer import DSPyOptimizer

            # F234: Pass lifecycle for memory_mgr access (battery/thermal guards)
            instance = DSPyOptimizer(brain_manager=lifecycle)
            # Sprint F234: Start background optimization loop (non-blocking)
            safe_create_task(instance.start(), name="dspy_optimizer")
            _dspy_optimizer_cache.set(None, instance)
            return instance
        except Exception:
            _dspy_optimizer_cache.set(None, None)
            return None


def _get_dspy_prompts() -> dict:
    """
    Lazy load DSPy optimalizované prompty from optimizer cache.
    Fallback: prázdný dict (synthesis použije hardcoded templates).
    """
    prompts: dict = {}
    try:
        # Sprint F234: Try optimizer first, then fallback to load_optimized_prompts
        # Use cached optimizer which already has lifecycle attached
        dspy_opt = _get_dspy_optimizer()
        if dspy_opt is not None and dspy_opt._optimized_prompts:
            prompts = dspy_opt._optimized_prompts
        else:
            from brain.dspy_optimizer import load_optimized_prompts

            prompts = load_optimized_prompts()
    except Exception:
        prompts = {}
    return prompts


def _get_prompt_bandit():
    """Lazy init PromptBandit."""
    cached = _prompt_bandit_cache.get(None)
    if cached is not None:
        return cached
    with _optimizer_init_lock:
        cached = _prompt_bandit_cache.get(None)
        if cached is not None:
            return cached
        try:
            from brain.prompt_bandit import PromptBandit

            instance = PromptBandit(
                brain_manager=None,
                alpha=1.0,
                lambda_reg=0.01,
                context_dim=9,
                persist_path=str(Path.home() / '.hledac' / 'prompt_bandit.json'),
            )
            _prompt_bandit_cache.set(None, instance)
            return instance
        except Exception:
            _prompt_bandit_cache.set(None, None)
            return None


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

OSINT_JSON_SCHEMA: str = _msgspec_encode({
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
}).decode()


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
                 "_hypothesis_engine",
                 "_hermes_engine",  # P2-1: cached Hermes3Engine for continuous batching
                 "_inference_pipeliner",  # P2-1b: InferencePipeliner for non-blocking submit + prompt overlap
                 # Issue #20: KV cache params — initialized from ModelLifecycle or hardcoded defaults
                 "_kv_bits", "_max_kv_size",
                 # Cached Metal memory probe (Issue #20-A: avoid per-call Rust FFI)
                 "_metal_probe_cache")

    def __init__(self, lifecycle: ModelLifecycle) -> None:
        self._lifecycle = lifecycle
        # Issue #20: KV cache params — expose from ModelLifecycle or hardcoded defaults
        # ModelLifecycle does NOT carry _kv_bits/_max_kv_size (it's a Qwen/SmolLM windup sidecar),
        # so we hardcode the same defaults as DeepHermes3Engine for consistency.
        self._kv_bits: int = int(os.getenv("GHOST_KV_BITS", "4"))
        self._max_kv_size: int = 8192
        # Issue #20-A: cache for Metal probe to avoid repeated Rust FFI calls
        # Structure: {active_bytes: (kv_bits, (emergency, critical, warn))}
        self._metal_probe_cache: dict = {}
        self._ioc_graph: Any | None = None
        self._cached_model_path: Path | None = None
        self._last_outlines_used: bool = False
        # Sprint 8TD: Custom prompt support
        self._custom_synthesis_prompt: str | None = None
        self._prompt_modifier: str = ""
        # Sprint 8UC B.2: DuckDB store for episode recall
        self._duckdb_store: Any | None = None
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
        self._compressor: Any | None = None

        # F214: HypothesisEngine — optional synthesis step
        self._hypothesis_engine: Any | None = None

        # P2-1: Hermes3Engine for continuous batching via MLXBatchedExecutor
        self._hermes_engine: Any | None = None

        # P2-1b: Optional InferencePipeliner for non-blocking submit + prompt overlap
        self._inference_pipeliner: Any | None = None

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
    # P2-1: Hermes3Engine lazy init for continuous batching
    # ------------------------------------------------------------------

    def _get_hermes_engine(self) -> Any:
        """
        P2-1: Get or create Hermes3Engine instance for continuous batching.

        Uses MLXBatchedExecutor (P0-2) for adaptive batching + MLXWorkerThread (P0-3)
        for non-blocking inference. Lazy init — first call triggers model load.

        Returns:
            DeepHermes3Engine instance (always-on, fail-soft on errors)
        """
        if self._hermes_engine is not None:
            return self._hermes_engine
        try:
            from .deephermes3_engine import DeepHermes3Engine
            self._hermes_engine = DeepHermes3Engine()
            logger.debug("[P2-1] Hermes3Engine created for continuous batching")
        except Exception as e:
            logger.warning("[P2-1] Hermes3Engine init failed: %s", e)
        return self._hermes_engine

    def _get_inference_pipeliner(self) -> Any:
        """
        P2-1b: Get or create InferencePipeliner for non-blocking submit + prompt overlap.

        Wraps DeepHermes3Engine with non-blocking submit() API that overlaps
        prompt preprocessing with current inference. Lazy init.

        Returns:
            InferencePipeliner instance with generate() method (always-on, fail-soft)
        """
        if self._inference_pipeliner is not None:
            return self._inference_pipeliner
        try:
            from .inference_pipeliner import InferencePipeliner
            from .mlx_worker_thread import MLXWorkerThread

            # Create worker thread for non-blocking dispatch
            worker = MLXWorkerThread(name="mlx-pipeliner-worker")
            worker.start()

            # Create engine and pipeliner
            engine = self._get_hermes_engine()
            if engine is None:
                return None

            self._inference_pipeliner = InferencePipeliner(
                engine=engine,
                worker_thread=worker,
            )
            logger.debug("[P2-1b] InferencePipeliner created with MLXWorkerThread")
        except Exception as e:
            logger.warning("[P2-1b] InferencePipeliner init failed: %s", e)
        return self._inference_pipeliner

    # ------------------------------------------------------------------
    # Issue #20 improvement: Adaptive KV cache methods (mirrors DeepHermes3Engine)
    # ------------------------------------------------------------------
    # Issue #20: Combined Metal memory probe — called ONCE per synthesis call
    # Caches result in _metal_probe_cache to avoid repeated Rust FFI calls
    # Returns: (kv_bits: int, tier: str, thresholds: tuple[int,int,int])
    # ------------------------------------------------------------------

    def _probe_metal_memory(self) -> tuple[int, str, tuple[int, int, int]]:
        """
        Issue #20-A: Combined Metal memory probe with result caching.

        Probes active memory ONCE and returns kv_bits + tier + thresholds.
        Caches by active_bytes bucket (rounded to 64 MiB) to handle
        repeated calls within the same synthesis batch.

        Returns:
            (kv_bits, tier_name, (emergency_bytes, critical_bytes, warn_bytes))
        """
        try:
            import mlx.core as mx

            active = 0
            if hasattr(mx, "get_active_memory"):
                active = int(mx.get_active_memory())
            elif hasattr(mx.metal, "get_active_memory"):
                active = int(mx.metal.get_active_memory())

            # Round to 64 MiB bucket for cache stability
            bucket = (active // (64 * 1024 * 1024)) * (64 * 1024 * 1024)

            if bucket in self._metal_probe_cache:
                return self._metal_probe_cache[bucket]

            thresholds = _synthesis_get_metal_tier_thresholds()
            emergency_bytes, critical_bytes, warn_bytes = thresholds

            active_gib = active / (1024**3)
            if active_gib > 2.0:
                tier = "high"
                kv_bits = 8
            elif active_gib > 1.5:
                tier = "medium"
                kv_bits = 6
            else:
                tier = "normal"
                kv_bits = max(4, self._kv_bits)

            result = (kv_bits, tier, thresholds)
            self._metal_probe_cache[bucket] = result
            return result
        except Exception:
            pass

        return (max(4, self._kv_bits), "normal", _synthesis_get_metal_tier_thresholds())

    def _get_adaptive_kv_bits(self) -> int:
        """Issue #20: Adaptive KV quantization bits based on Metal memory pressure."""
        kv_bits, _, _ = self._probe_metal_memory()
        return kv_bits

    def _get_kv_cache_kwargs(
        self,
        input_tokens: int | None = None,
        max_tokens: int | None = None,
    ) -> dict:
        """
        Issue #20-A: Adaptive KV cache sizing using cached Metal probe.

        O1 optimization: min(input_tokens + headroom, memory_tier_cap).
        Uses _probe_metal_memory() cache to avoid repeated Rust FFI calls.

        Memory-pressure tiers:
        - normal → max_kv_size = full (8192 or adaptive)
        - warn → 50% reduction
        - critical → 75% reduction
        - emergency → KV cache off {}

        Returns:
            dict: kwargs pro mlx_lm.generate() — {} nebo {"max_kv_size": N}
        """
        # Issue #20-A: use cached probe instead of redundant Metal FFI calls
        _, tier, thresholds = self._probe_metal_memory()
        emergency_bytes, critical_bytes, warn_bytes = thresholds

        # Determine tier from thresholds (high/medium/normal from probe → emergency/warn/critical)
        if tier == "high":
            tier = "critical"
        elif tier == "medium":
            tier = "warn"
        else:
            tier = "normal"

        # O1: input-length-aware cache sizing
        _in_tokens = input_tokens if input_tokens is not None else 0
        _max_tok = max_tokens if max_tokens is not None else 512
        _headroom = min(_max_tok, 1024)
        _min_cache = _in_tokens + _headroom

        if tier == "emergency":
            base_size = 0
        elif tier == "critical":
            base_size = max(256, self._max_kv_size // 4)
        elif tier == "warn":
            base_size = max(1024, self._max_kv_size // 2)
        else:
            base_size = self._max_kv_size

        final_size = 0 if base_size == 0 else max(_min_cache, base_size)
        return {"max_kv_size": final_size} if final_size > 0 else {}

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
        # F285: Wire Hermes3Engine into HypothesisEngine so generate_hypotheses_async
        # can route through MLXBatchedExecutor (P0-2 wiring). Without this, the
        # getattr() call in synthesize_findings returns None and hermes goes direct.
        hermes = self._get_hermes_engine()
        if hermes is not None and hasattr(engine, "_inference_engine"):
            engine._inference_engine = hermes
        # P2-1b: Also inject InferencePipeliner for overlapping hypothesis generation
        pipeliner = self._get_inference_pipeliner()
        if pipeliner is not None and hasattr(engine, "_inference_pipeliner"):
            engine._inference_pipeliner = pipeliner

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
        # Issue #12.5: __slots__ attrs always initialized in __init__ — direct access (5-10× faster)
        return {
            "synthesis_engine": self._last_synthesis_engine,
            "dspy_prompt_version": len(_get_dspy_prompts()),
            "bandit_arm_used": self._last_arm,
            "bandit_arm_rewards": self._bandit_rewards,
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
            # Issue #12.5: __slots__ attrs always initialized — direct access
            self._lifecycle_gate_source = self._lifecycle_gate_source
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

        # Issue #12.1: PARALLEL DISCOVERY — model + stix + episode + RAG
        # All independent I/O-bound tasks run concurrently via TaskGroup.
        # Serial cost: ~5-12s. Parallel cost: ~max of individual tasks (3-5s).
        model_path = None
        stix_context = ""
        episode_ctx = ""
        rag_context = ""

        try:
            async with asyncio.TaskGroup() as tg:
                # Task 1: Model discovery (I/O — file scan or HTTP download)
                tg_model = tg.create_task(self._ensure_model(), name="syn:model")
                # Task 2: STIX context (async DB export)
                tg_stix = tg.create_task(self._build_stix_context(), name="syn:stix")
                # Task 3: Episode context (DB query, conditional)
                if self._duckdb_store is not None:
                    tg_ep = tg.create_task(
                        self._build_episode_context(self._duckdb_store, query), name="syn:ep"
                    )
                else:
                    tg_ep = None
                # Task 4: RAG retrieval (I/O — vector search)
                tg_rag = tg.create_task(
                    self._rag_query_safe(query, findings), name="syn:rag"
                )
        except ExceptionGroup as eg:
            logger.debug("[SYNTHESIS] Parallel discovery partial failure: %s", eg)

        # Extract results (re-raise cancellation if any task was cancelled)
        try:
            model_path = tg_model.result()
        except asyncio.CancelledError:
            model_path = None

        try:
            stix_context = tg_stix.result()
        except asyncio.CancelledError:
            stix_context = ""

        if tg_ep is not None:
            try:
                episode_ctx = tg_ep.result()
            except asyncio.CancelledError:
                episode_ctx = ""

        try:
            rag_context = tg_rag.result()
        except asyncio.CancelledError:
            rag_context = ""

        if model_path is None:
            logger.warning("[SYNTHESIS] No model available — skipping")
            self._last_synthesis_outcome = SynthesisOutcome(
                status="skipped",
                primary_reason="no_model",
                # Issue #12.5: __slots__ attrs always initialized — direct access
                lifecycle_gate_source=self._lifecycle_gate_source,
                lifecycle_gate_mode=self._lifecycle_gate_mode,
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

        # Issue #12.2: Rerank in thread — ONNX sync inference must not block event loop.
        # Cost: ~200-500ms. Falls back to confidence-sort on error.
        top = findings
        try:
            top = await self._rerank_findings(query, findings, max_findings)
        except Exception:
            top = sorted(findings, key=lambda f: f.get("confidence", 0.0), reverse=True)[:max_findings]

        # Issue #12.1 continued: GraphRAG — I/O-bound IOC relationship query
        graph_context = ""
        top_iocs = [
            f.get("ioc") or f.get("indicator") or f.get("value")
            for f in top[:5]
            if f.get("ioc") or f.get("indicator") or f.get("value")
        ]
        if top_iocs:
            graph_context = await self._graphrag_safe(query, top_iocs)

        # [P0-1] Zero-findings path: build query-focused prompt instead of findings-focused
        if findings_count == 0:
            findings_text = "[No findings collected during this sprint]"
            prompt = (
                f"Query: {query}{stix_context}\n"
                f"Findings:\n{findings_text}\n"
                f"Current timestamp: {time.time()}\n"
                f"Note: Provide a threat intelligence report based on the query and general knowledge."
            )
        else:
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
            dspy_opt = _get_dspy_optimizer(self._lifecycle)
            if dspy_opt is not None:
                try:
                    # Check for optimized prompt for analysis task
                    optimized = dspy_opt.get_prompt('analysis', {'complexity': 'medium'})
                    if optimized:
                        self.set_custom_prompt(optimized)
                        logger.info(f"[SYNTHESIS] DSPy optimized prompt loaded ({len(optimized)} chars)")
                except Exception:  # noqa: BLE001
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

            # Issue #12.3 + #12.4: RACE inference — parallel xgrammar + streaming + constrained.
            # Pre-load model once, then race all three engines. Take first successful result.
            # Benefits: ~3s sequential cascade → ~1s first-success (37-67% speedup).
            # Issue #12.4: unload() only on real fallback (raw_dict is None), not on success path.
            try:
                model, tokenizer, _model_path = await self._lifecycle._ensure_loaded()
            except RuntimeError as e:
                logger.warning("[SYNTHESIS] Model load failed for race: %s", e)
                raw_dict, used_engine = None, "none"
            else:
                raw_dict, used_engine = await self._race_inference(prompt)

            # Issue #12.4: unload() only when ALL engines failed (real fallback happened)
            if raw_dict is None:
                await self._lifecycle.unload()
                gc.collect()
        except Exception as e:
            logger.error("Synthesis error: %s", e)
            self._last_synthesis_outcome = SynthesisOutcome(
                status="failed",
                primary_reason="generation_failed",
                # Issue #12.5: __slots__ attrs always initialized — direct access
                lifecycle_gate_source=self._lifecycle_gate_source,
                lifecycle_gate_mode=self._lifecycle_gate_mode,
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
                    # Issue #12.5: __slots__ attrs always initialized — direct access
                    lifecycle_gate_source=self._lifecycle_gate_source,
                    lifecycle_gate_mode=self._lifecycle_gate_mode,
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
            lifecycle_gate_source=self._lifecycle_gate_source,
            lifecycle_gate_mode=self._lifecycle_gate_mode,
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
        # P2-1b: Shutdown InferencePipeliner first
        if self._inference_pipeliner is not None:
            try:
                await self._inference_pipeliner.shutdown()
            except Exception:  # noqa: BLE001
                pass
        # Ensure any pending lifecycle resources are released
        try:
            await self._lifecycle.unload()
        except Exception:  # noqa: BLE001
            pass
        # Sprint F234: Persist bandit state on shutdown
        bandit = _get_prompt_bandit()
        if bandit is not None:
            try:
                await bandit.final_save()
            except Exception:  # noqa: BLE001
                pass
        gc.collect()

    # ------------------------------------------------------------------
    # Issue #12.1 + #12.2: Helper methods for parallel discovery + async-safe rerank
    # ------------------------------------------------------------------

    async def _rag_query_safe(self, query: str, findings: list[dict]) -> str:
        """RAG retrieval — fail-soft wrapper for parallel discovery TaskGroup."""
        try:
            from knowledge.rag_engine import RAGEngine
            _rag = RAGEngine()
            rag_result = await _rag.query(
                query=query,
                context_chunks=[f.get("text", "")[:500] for f in findings[:20]],
                use_compression=False,
            )
            if rag_result and rag_result.get("context"):
                raw_ctx = rag_result["context"]
                max_chars = 7200
                if len(raw_ctx) > max_chars:
                    raw_ctx = raw_ctx[:max_chars] + "...[truncated]"
                return f"\n\n## Semantically Retrieved Findings\n{raw_ctx}"
        except Exception as e:
            logger.debug(f"[SYNTHESIS] RAG retrieve skipped: {e}")
        return ""

    async def _graphrag_safe(self, query: str, top_iocs: list) -> str:
        """GraphRAG IOC relationships — fail-soft wrapper for parallel discovery."""
        try:
            from hledac.universal.legacy.persistent_layer import PersistentKnowledgeLayer
            from knowledge.graph_rag import GraphRAGOrchestrator
            kl = PersistentKnowledgeLayer()
            _grag = GraphRAGOrchestrator(kl)
            if not hasattr(_grag, "find_connections"):
                return ""
            conn_texts = []
            for ioc in top_iocs[:3]:
                try:
                    # Issue #12.5: safe None guard on IOC value
                    ioc_str = str(ioc) if ioc else ""
                    if not ioc_str:
                        continue
                    conns = await _grag.find_connections(ioc_str, ioc_str, max_hops=2)
                    if conns:
                        conn_texts.append(
                            f"IOC {ioc_str}: {'; '.join(str(c)[:80] for c in conns[:3])}"
                        )
                except Exception:  # noqa: BLE001
                    pass
            if conn_texts:
                return "\n\n## IOC Relationship Graph\n" + "\n".join(conn_texts)[:1500]
        except Exception as e:
            logger.debug(f"[SYNTHESIS] GraphRAG skipped: {e}")
        return ""

    async def _rerank_findings(
        self,
        query: str,
        findings: list[dict],
        max_findings: int,
    ) -> list[dict]:
        """
        Issue #12.2: Flashrank ONNX rerank — MUST run in thread to avoid blocking event loop.

        Returns reranked findings (top max_findings).
        Raises on error so caller falls back to confidence sort.
        """
        from flashrank import RerankRequest

        _ranker = _get_flashrank_ranker()
        passages = [
            {"id": i, "text": f"{f.get('title', '')} {f.get('snippet', f.get('text', ''))}"}
            for i, f in enumerate(findings[:200])
        ]
        rerank_request = RerankRequest(query=query, passages=passages)

        def _rerank_sync() -> list[dict]:
            results = _ranker.rerank(rerank_request)
            ranked_idxs = [r["id"] for r in results[:max_findings]]
            return [findings[i] for i in ranked_idxs]

        return await asyncio.to_thread(_rerank_sync)

    # ------------------------------------------------------------------
    # Issue #12.3: Race inference — parallel xgrammar + streaming + constrained
    # ------------------------------------------------------------------

    async def _race_inference(
        self,
        prompt: str,
    ) -> tuple[dict | None, str]:
        """
        Issue #12.3: Race xgrammar vs streaming vs constrained — take first success.

        All three engines run in parallel. First to return valid dict wins.
        The winner cancels the other two tasks via TaskGroup cancellation.

        Returns (raw_dict, engine_name). On all failure: (None, "none").
        """
        try:
            async with asyncio.TaskGroup() as tg:
                tg_xgr = tg.create_task(
                    self._run_xgrammar_generation(prompt), name="race:xgrammar"
                )
                tg_stream = tg.create_task(
                    self._run_streaming_generation(prompt, json_schema=OSINT_JSON_SCHEMA),
                    name="race:streaming"
                )
                tg_constrained = tg.create_task(
                    self._lifecycle.structured_generate(prompt, OSINT_JSON_SCHEMA),
                    name="race:constrained"
                )
        except ExceptionGroup as eg:
            # All three failed
            logger.debug("[SYNTHESIS] All race engines failed: %s", eg)
            return None, "none"

        # Determine winner — TaskGroup succeeded means at least one task completed
        # We need to check which task has a valid result
        winner_result = None
        winner_name = "none"

        # Inspect completed tasks (they all completed since TaskGroup didn't raise)
        for tg_task, name in [
            (tg_xgr, "xgrammar"),
            (tg_stream, "streaming"),
            (tg_constrained, "constrained"),
        ]:
            try:
                result = tg_task.result()
                if result is None:
                    continue
                if name == "constrained":
                    # structured_generate returns (raw_dict, outlines_ok)
                    raw_dict, ok = result
                    if ok and raw_dict is not None:
                        winner_result = raw_dict
                        winner_name = name
                        break
                else:
                    # xgrammar/streaming return (raw_dict, ok)
                    raw_dict, ok = result
                    if ok and raw_dict is not None:
                        winner_result = raw_dict
                        winner_name = name
                        break
            except asyncio.CancelledError:
                continue
            except Exception:
                continue

        return winner_result, winner_name

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
                m = _MML_TAG_RE.search(full_prompt)
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

        # Count tokens for adaptive KV cache (Issue #20 improvement)
        try:
            _stream_input_tokens = len(tokenizer.encode(formatted))
        except Exception:
            _stream_input_tokens = 0

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
                        kv_bits=self._get_adaptive_kv_bits(),
                        **self._get_kv_cache_kwargs(_stream_input_tokens, 512),
                        verbose=False,
                    ):
                        tok = chunk.text if hasattr(chunk, "text") else str(chunk)
                        accumulated += tok
                        # Early-exit: hledáme kompletní JSON objekt s "title"
                        m_match = _JSON_OBJ_RE.search(accumulated)
                        if m_match:
                            try:
                                return _msgspec_decode(m_match.group()), True
                            except Exception:
                                pass  # neúplný — pokračuj
                except Exception as e:
                    logger.warning("[SYNTHESIS] stream_generate failed: %s — fallback", e)
                    accumulated = ""

            # Fallback: regex JSON extract z akumulovaného textu
            if accumulated:
                m_final = _JSON_FINAL_RE.search(accumulated)
                if m_final:
                    try:
                        return _msgspec_decode(m_final.group()), True
                    except Exception:  # noqa: BLE001
                        pass

            # Issue #20-C: mx.eval() + gc.collect() cleanup (matches xgrammar pattern)
            try:
                import mlx.core as _mx
                if _mx.metal.is_available():
                    _mx.eval([])  # barrier: flush GPU queue BEFORE Python GC
                    import gc
                    gc.collect()  # collect Python refs that held MLX objects
                    if hasattr(_mx, "clear_cache"):
                        _mx.clear_cache()
            except Exception:  # noqa: BLE001
                pass  # Non-fatal

            return (None, False)

        return await asyncio.to_thread(_stream_sync)

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

        # Load model BEFORE executor (same pattern as _run_streaming_generation)
        try:
            model, tokenizer, _model_path = await self._lifecycle._ensure_loaded()
        except RuntimeError as e:
            logger.warning("[SYNTHESIS] xgrammar model load failed: %s", e)
            return None, False

        # Format prompt OUTSIDE _xgrammar_sync so tokenizer.count_tokens() is accessible
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

        # Count tokens for adaptive KV cache
        try:
            _input_tokens = len(tokenizer.encode(formatted))
        except Exception:
            _input_tokens = 0

        def _xgrammar_sync() -> tuple[dict | None, bool]:
            try:
                from contextlib import nullcontext

                import mlx_lm
                import xgrammar as xgr

                from hledac.universal.utils.mlx_memory import get_metal_stream_context

                # Use cached grammar compilation (Sprint 8UF B.1)
                schema = _build_osint_json_schema()
                schema_str = _msgspec_encode(schema).decode()
                grammar = _get_cached_grammar(schema_str, tokenizer)

                # Build logits processor via contrib.hf
                try:
                    processor = xgr.contrib.hf.LogitsProcessor(grammar, tokenizer)
                except (AttributeError, TypeError):
                    # Fallback: use grammar directly if LogitsProcessor unavailable
                    return None, False

                # P0-1: mx.eval([]) barrier BEFORE mlx_lm.generate() — canonical F266 order
                try:
                    import mlx.core as _mx
                    if _mx.metal.is_available():
                        _mx.eval([])
                except Exception:  # noqa: BLE001
                    pass

                # P0-1: Primary path — try with Metal stream context
                stream_ctx = get_metal_stream_context()
                output = None
                _stream_err = None
                try:
                    with stream_ctx:
                        try:
                            output = mlx_lm.generate(
                                model, tokenizer,
                                prompt=formatted,
                                max_tokens=512,
                                logits_processors=[processor],
                                kv_bits=self._get_adaptive_kv_bits(),
                                **self._get_kv_cache_kwargs(_input_tokens, 512),
                                verbose=False,
                            )
                        except TypeError:
                            # Old mlx_lm without logits_processors
                            output = mlx_lm.generate(
                                model, tokenizer,
                                prompt=formatted,
                                max_tokens=512,
                                kv_bits=self._get_adaptive_kv_bits(),
                                **self._get_kv_cache_kwargs(_input_tokens, 512),
                                verbose=False,
                            )
                except RuntimeError as _e:
                    if "Stream(gpu" in str(_e):
                        _stream_err = _e
                        logger.debug("[P0-1] [SYNTHESIS] Metal stream error, retrying direct: %s", _e)
                        with nullcontext():
                            try:
                                try:
                                    output = mlx_lm.generate(
                                        model, tokenizer,
                                        prompt=formatted,
                                        max_tokens=512,
                                        logits_processors=[processor],
                                        kv_bits=self._get_adaptive_kv_bits(),
                                        **self._get_kv_cache_kwargs(_input_tokens, 512),
                                        verbose=False,
                                    )
                                except TypeError:
                                    output = mlx_lm.generate(
                                        model, tokenizer,
                                        prompt=formatted,
                                        max_tokens=512,
                                        kv_bits=self._get_adaptive_kv_bits(),
                                        **self._get_kv_cache_kwargs(_input_tokens, 512),
                                        verbose=False,
                                    )
                            except Exception as _direct_err:
                                logger.warning("[P0-1] [SYNTHESIS] Direct retry also failed: %s", _direct_err)
                    else:
                        raise
                finally:
                    # Sprint 8UD B.2: Clear MLX Metal cache after inference
                    # F300-MLX invariant: mx.eval([]) PŘED gc.collect()
                    try:
                        import mlx.core as _mx
                        if _mx.metal.is_available():
                            _mx.eval([])  # barrier: flush GPU queue BEFORE Python GC
                            import gc
                            gc.collect()  # collect Python refs that held MLX objects
                            if hasattr(_mx, "clear_cache"):
                                _mx.clear_cache()
                    except Exception:  # noqa: BLE001
                        pass  # noqa: BLE001  # Non-fatal

                if output is None:
                    return None, False

                result = _msgspec_decode(output)
                if "title" in result and "summary" in result:
                    return result, True
                return None, False

            except ImportError:
                return None, False
            except Exception as e:
                logger.warning(f"[SYNTHESIS] xgrammar generation: {e}")
                return None, False

        return await asyncio.to_thread(_xgrammar_sync)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _ensure_model(self) -> Path | None:
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
                hits = await asyncio.to_thread(lambda: list(d.glob(pat)))  # noqa: B023
                if hits:
                    self._cached_model_path = hits[0].parent
                    logger.info("[SYNTHESIS] Model found: %s", self._cached_model_path.name)
                    return self._cached_model_path

        # Tier 3: parallel size check + parallel download for pre-warming
        model_candidates = [
            ("mlx-community/Qwen2.5-0.5B-Instruct-4bit", 1.0),
            ("mlx-community/SmolLM2-135M-Instruct-4bit", 0.2),
        ]

        async def _check_model_size(model_id: str, max_gb: float) -> tuple[str, float] | None:
            """Check model size from HuggingFace API. Returns (model_id, size_bytes) or None."""
            try:
                api_url = f"https://huggingface.co/api/models/{model_id}"
                r = await asyncio.to_thread(urllib.request.urlopen, api_url, timeout=15)
                with r:
                    data = _msgspec_decode(r.read())
                    total = sum(f.get("size", 0) for f in data.get("siblings", []))
                if total / 1e9 > max_gb:
                    return None
                return (model_id, total)
            except Exception:
                return None

        async def _download_model(model_id: str) -> bool:
            """Download a single model via centralized cache. Returns True on success."""
            from brain.model_cache import get_or_download_model

            try:
                logger.info("[SYNTHESIS] Downloading %s ...", model_id)
                result = await get_or_download_model(model_id)
                if result is not None:
                    logger.info("[SYNTHESIS] Download complete: %s", model_id)
                    return True
                logger.warning("[SYNTHESIS] Model download failed for %s", model_id)
                return False
            except Exception as e:
                logger.warning("[SYNTHESIS] Model download failed for %s: %s", model_id, e)
                return False

        # Phase 1: Check all model sizes in parallel
        # F314-4: migrated asyncio.gather -> safe_gather_ok (fail-soft, preserves order)
        size_results = await safe_gather_ok(
            *[_check_model_size(mid, mgb) for mid, mgb in model_candidates],
            label="synthesis:check_model_sizes",
        )

        # Filter eligible models (fit within budget)
        eligible: list[str] = []
        for (model_id, max_gb), result in zip(model_candidates, size_results):
            if isinstance(result, Exception) or result is None:
                continue
            _, size_bytes = result
            eligible.append(model_id)
            logger.info("[SYNTHESIS] Model %s fits budget (%.0fMB)", model_id, size_bytes / 1e6)

        # Phase 2: Parallel download of all eligible models for pre-warming
        if eligible:
            # F314-4: migrated asyncio.gather -> safe_gather_fire_and_forget
            # Download in parallel - best effort, don't fail all if one fails.
            # Result is unused (disk rescan below finds downloads).
            await safe_gather_fire_and_forget(
                *[_download_model(mid) for mid in eligible],
                label="synthesis:download_models",
            )
            # Re-scan disk for any successfully downloaded model
            for d in search:
                for pat in ["**/config.json"]:
                    hits = await asyncio.to_thread(lambda: list(d.glob(pat)))  # noqa: B023
                    if hits:
                        self._cached_model_path = hits[0].parent
                        logger.info("[SYNTHESIS] Model ready: %s", self._cached_model_path.name)
                        return self._cached_model_path

        return None

    def _compute_confidence(
        self,
        report: OSINTReport,
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
            except Exception:  # noqa: BLE001
                pass  # noqa: BLE001  # Fall through to Path 2

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
        except Exception:  # noqa: BLE001
            pass  # noqa: BLE001  # Fall through to Path 3

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
        P2-1: Decompose query into 3-5 sub-queries. Max 80 tokens.

        Routes through InferencePipeliner (P2-1b) for non-blocking submit
        + prompt preprocessing overlap. Falls back to identity if unavailable.
        """
        # P2-1b: Try InferencePipeliner for overlapping inference
        pipeliner = self._get_inference_pipeliner()
        if pipeliner is not None:
            try:
                prompt = (
                    "You are a security OSINT assistant. "
                    f"Generate 3-5 specific search queries for: {query}\n"
                    "Output ONLY a JSON array of strings, no explanation.\n"
                    'Example: ["LockBit IOCs 2026","LockBit C2 infra","LockBit victims list"]'
                )
                out = await pipeliner.generate(prompt, max_tokens=80, thinking=False)
                m = _BRACKET_RE.search(out)
                if m:
                    parsed = _msgspec_decode(m.group())
                    if isinstance(parsed, list) and parsed:
                        result = [str(s) for s in parsed[:5]]
                        logger.info(f"decompose_query '{query[:40]}' → {len(result)} sub-queries [pipelined]")
                        return result
            except Exception as e:
                logger.warning(f"decompose_query pipeliner failed: {e} — fallback to identity")

        # Fallback: identity (no model available)
        logger.debug("decompose_query: no pipeliner → identity fallback")
        return [query]

    # ── Sprint 8TB: Ghost Global Context ─────────────────────────────────

    async def _load_global_context(self) -> str:
        """
        Load top-10 recurring entities from ghost_global.duckdb as context.

        Returns empty string if DB doesn't exist or on any error.
        """
        try:
            import duckdb

            from ..paths import RAMDISK_ROOT

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
            if findings and isinstance(findings, list) and findings:
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
                    self._stix_reason = "stix_graph export_stix_bundle returned nodes but none had extractable 'value' field"  # noqa: E501
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
                self._stix_reason = f"backend '{backend_name}' lacks export_stix_bundle — DuckPGQGraph donor cannot serve STIX"  # noqa: E501
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
    return _SLUGIFY_RE.sub("-", s.lower()).strip("-")


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

    # F265B-FIX: guard against reports being a FILE (not directory).
    # Can happen from interrupted prior runs or manual creation.
    if reports_dir.exists() and not reports_dir.is_dir():
        reports_dir.rename(reports_dir.with_suffix(".bak.reports"))
    reports_dir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    filename = f"{ts}_{slugify(query)[:40]}_report.json"
    out_path = reports_dir / filename

    # msgspec → JSON bytes → decode string → write
    content = msgspec.json.encode(report).decode("utf-8")
    out_path.write_text(content, encoding="utf-8")
    logger.info("Sprint report saved: %s", out_path)
    return out_path
