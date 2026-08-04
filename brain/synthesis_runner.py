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
import msgspec

import asyncio
import gc
import hashlib
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from hledac.universal.utils.async_helpers import safe_create_task, parallel, first_completed  # ISSUE-15
from hledac.universal.utils.cache import PyCacheDict
from hledac.universal.utils.msgspec_json import decode as _msgspec_decode
from hledac.universal.utils.msgspec_json import encode as _msgspec_encode
from hledac.universal.core.dlq_manager import dlq_catch  # DLQ-02

# Precompiled regex patterns — compile once, use repeatedly
_MML_TAG_RE = re.compile(r"<\|system\|>(.*?)<\|user\|>(.*?)<\|assistant\|>", re.DOTALL)
_BRACKET_RE = re.compile(r'\[.*?\]', re.DOTALL)
_SLUGIFY_RE = re.compile(r"[^a-z0-9]+")

# ISSUE-009: Speculative URL/IP detection for streaming token accumulation
# — scan last 512 chars of accumulated text per token, O(1) memory per token
try:
    import regex as _re_speculative

    _URL_SPEC_RE = _re_speculative.compile(r"https?://[^\s\"'<>]{10,}", _re_speculative.UNICODE)
    _IP_SPEC_RE = _re_speculative.compile(
        r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}"
        r"(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b",
    )
except ImportError:
    import re as _re_speculative  # type: ignore

    _URL_SPEC_RE = _re_speculative.compile(r"https?://[^\s\"'<>]{10,}")
    _IP_SPEC_RE = _re_speculative.compile(
        r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}"
        r"(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b",
    )
_SPEC_WINDOW = 512  # sliding window size for speculative detection


def _try_parse_json_incremental(accumulated: str) -> tuple[dict | None, bool]:
    """
    ISSUE-010: Incremental JSON parse using orjson with error position checking.

    orjson raises JSONDecodeError with exact byte position on parse failure.
    If e.pos == len(data), JSON is incomplete (keep accumulating).
    If e.pos < len(data), it's a real parse error (re-raise).

    Faster than ijson. Handles nested structures that regex cannot.

    Returns:
        (parsed_dict, is_complete) — is_complete=True means valid JSON found.
        (None, False) means incomplete, keep accumulating.
    """
    import orjson

    try:
        return orjson.loads(accumulated), True
    except orjson.JSONDecodeError as e:
        if e.pos == len(accumulated):
            return None, False  # Incomplete, keep accumulating
        raise  # Real parse error


def _mlx_cleanup() -> None:
    """
    Shared MLX Metal cleanup — Issue #20-C, Sprint 3 dedup.

    F300-MLX invariant: mx.eval([]) PŘED gc.collect().
    Reused in both _run_streaming_generation (line ~1760) and
    _run_xgrammar_generation (line ~1915) — identical 8-line block.
    """
    try:
        import mlx.core as _mx

        if _mx.metal.is_available():
            _mx.eval([])  # barrier: flush GPU queue BEFORE Python GC
            gc.collect()  # collect Python refs that held MLX objects
            if hasattr(_mx, "clear_cache"):
                _mx.clear_cache()
    except Exception:  # noqa: BLE001
        pass  # Non-fatal


try:
    import msgspec as _msgspec
    msgspec = _msgspec
except ImportError:
    msgspec = None  # type: ignore
    import logging
    _logger_msgspec = logging.getLogger(__name__)
    _logger_msgspec.warning("msgspec not installed — JSON constrained generation disabled")

if TYPE_CHECKING:
    from hledac.universal.core.model_runtime import ModelLifecycle

logger = logging.getLogger(__name__)

# L-05: Synthesis strategy — controls inference race vs cascade behavior
# "sequential_preferred" (default): xgrammar → stream → structured cascade (first success wins, no parallelism overhead)
# "race_first_wins": all 3 engines race; first successful result cancels others via asyncio.current_task().cancel()
SYNTHESIS_STRATEGY = os.getenv("SYNTHESIS_STRATEGY", "sequential_preferred").strip()
assert SYNTHESIS_STRATEGY in ("sequential_preferred", "race_first_wins"), (
    f"SYNTHESIS_STRATEGY must be 'sequential_preferred' or 'race_first_wins', got {SYNTHESIS_STRATEGY!r}"
)

# NEXUS-018-04: Collapse threshold — collapse only when findings exceed this count
# to avoid overhead on small batches. Default 30; 0 to disable, -1 to force always.
_HLEDAC_COLLAPSE_THRESHOLD = int(os.getenv("HLEDAC_COLLAPSE_THRESHOLD", "30"))


# ---------------------------------------------------------------------------
# L-05: Race task helpers — extracted from _race_inference_first_wins
# Reduces CC of _race_inference_first_wins from 22 → ~8
# ---------------------------------------------------------------------------

async def _race_try_xgrammar(
    lifecycle: Any, prompt: str,
) -> tuple[dict | None, str, list[float]]:
    """Race task: try xgrammar generation. Extracted from _race_inference_first_wins."""
    try:
        result = await lifecycle._run_xgrammar_generation(prompt)
        if result is not None:
            raw_dict, ok = result
            if ok and raw_dict is not None:
                return raw_dict, "xgrammar", []
    except Exception as e:
        logger.debug("[SYNTHESIS] xgrammar failed in race: %s", e)
    return None, "none", []


async def _race_try_streaming(
    lifecycle: Any, prompt: str,
) -> tuple[dict | None, str, list[float]]:
    """Race task: try streaming generation. Extracted from _race_inference_first_wins."""
    try:
        result = await lifecycle._run_streaming_generation(prompt, json_schema=OSINT_JSON_SCHEMA)
        if result is not None:
            raw_dict, ok, token_logprobs = result
            if ok and raw_dict is not None:
                return raw_dict, "streaming", token_logprobs
    except Exception as e:
        logger.debug("[SYNTHESIS] streaming failed in race: %s", e)
    return None, "none", []


async def _race_try_structured(
    lifecycle: Any, prompt: str,
) -> tuple[dict | None, str, list[float]]:
    """Race task: try structured generation. Extracted from _race_inference_first_wins."""
    try:
        result = await lifecycle._run_structured_generation(prompt, json_schema=OSINT_JSON_SCHEMA)
        if result is not None:
            raw_dict, ok = result
            if ok and raw_dict is not None:
                return raw_dict, "structured", []
    except Exception as e:
        logger.debug("[SYNTHESIS] structured failed in race: %s", e)
    return None, "none", []

# ---------------------------------------------------------------------------
# L-05: Sequential cascade helpers — extracted from _race_inference_sequential
# Reduces CC of _race_inference_sequential from 13 → ~6
# ---------------------------------------------------------------------------

async def _cascade_xgrammar(
    lifecycle: Any, prompt: str,
) -> tuple[dict | None, list[float]]:
    """Step 1: xgrammar cascade. Returns (dict, []) on success, (None, []) on failure."""
    try:
        result = await lifecycle._run_xgrammar_generation(prompt)
        if result is not None:
            raw_dict, ok = result
            if ok and raw_dict is not None:
                logger.debug("[SYNTHESIS] xgrammar won (confidence guarantee)")
                return raw_dict, []
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.debug("[SYNTHESIS] xgrammar failed: %s", e)
    return None, []


async def _cascade_streaming(
    lifecycle: Any, prompt: str,
) -> tuple[dict | None, list[float]]:
    """Step 2: streaming cascade. Returns (dict, token_logprobs) on success, (None, []) on failure."""
    try:
        result = await lifecycle._run_streaming_generation(prompt, json_schema=OSINT_JSON_SCHEMA)
        if result is not None:
            raw_dict, ok, token_logprobs = result
            if ok and raw_dict is not None:
                logger.debug("[SYNTHESIS] streaming won (early-exit)")
                return raw_dict, token_logprobs
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.debug("[SYNTHESIS] streaming failed: %s", e)
    return None, []


async def _cascade_structured(
    lifecycle: Any, prompt: str,
) -> tuple[dict | None, list[float]]:
    """Step 3: structured Outlines cascade. Returns (dict, []) on success, (None, []) on failure."""
    try:
        result = await lifecycle.structured_generate(prompt, OSINT_JSON_SCHEMA)
        if result is not None:
            raw_dict, ok = result
            if ok and raw_dict is not None:
                logger.debug("[SYNTHESIS] structured (Outlines) won")
                return raw_dict, []
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.debug("[SYNTHESIS] structured failed: %s", e)
    return None, []


# ---------------------------------------------------------------------------
# Sprint 8SB: Model discovery helpers — extracted from _ensure_model
# Reduces CC of _ensure_model from 19 → ~10
# ---------------------------------------------------------------------------

async def _extract_stix_nodes(
    graph: Any, graph_label: str,
) -> tuple[list[str], str, str]:
    """
    Sprint 8TH: Extract 'value' fields from graph.export_stix_bundle().

    Returns (values, backend_name, error_reason).
    On error returns ([], backend_name, error_reason).
    """
    backend_name = type(graph).__name__
    try:
        export_fn = getattr(graph, "export_stix_bundle", None)
        if export_fn is None:
            return ([], backend_name, f"'{graph_label}' lacks export_stix_bundle")
        nodes = await export_fn()
        if not nodes:
            return ([], backend_name, "empty — graph has no IOC nodes")
        values = [n.get("value", "") for n in nodes[:20] if isinstance(n, dict)]
        return (values, backend_name, "")
    except Exception as e:
        return ([], backend_name, f"raised {type(e).__name__}: {e}")


async def _check_model_size(model_id: str, max_gb: float) -> tuple[str, float] | None:
    """Check model size from HuggingFace API via HttpTransport (R4 unified).

    Returns (model_id, size_bytes) or None on any failure.
    """
    try:
        from hledac.universal.transport.http_client import HttpTransport

        api_url = f"https://huggingface.co/api/models/{model_id}"
        result = await HttpTransport.fetch_one(api_url, profile="default", timeout_s=15.0)
        if not result.ok or not result.text:
            return None
        data = _msgspec_decode(result.text.encode())
        total = sum(f.get("size", 0) for f in data.get("siblings", []))
        if total / 1e9 > max_gb:
            return None
        return (model_id, total)
    except Exception:
        return None


async def _download_model(model_id: str) -> bool:
    """Download a single model via centralized cache. Returns True on success."""
    from hledac.universal.brain.model_cache import get_or_download_model

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


# ---------------------------------------------------------------------------
# Issue #20 improvement: Adaptive KV cache for M1 8GB Metal memory
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


# ---------------------------------------------------------------------------
# G2: Streaming findings infrastructure for M1 8GB memory efficiency
# ---------------------------------------------------------------------------

from typing import AsyncIterator, Protocol, TypeAlias

# Type alias: findings can be a list or an async iterator
FindingsSource: TypeAlias = "list[dict] | AsyncIterator[dict]"


async def _collect_findings_bounded(
    source: FindingsSource,
    max_buffered: int = 50,
    max_total: int | None = None,
) -> list[dict]:
    """
    G2: Collect findings from list or async iterator with bounded buffering.

    For M1 8GB memory efficiency: prevents unbounded accumulation of findings
    by enforcing max_buffered and optional max_total limits.

    Args:
        source: Either a list of findings or an async iterator
        max_buffered: Maximum findings to hold in memory before yielding back
        max_total: Optional hard cap on total findings collected

    Returns:
        List of collected findings (up to max_total)
    """
    if isinstance(source, list):
        if max_total is not None:
            return source[:max_total]
        return source

    # Async iterator path
    collected: list[dict] = []
    async for finding in source:
        collected.append(finding)
        if len(collected) >= max_buffered:
            # Yield control back to event loop — allows other tasks to run
            await asyncio.sleep(0)
        if max_total is not None and len(collected) >= max_total:
            break
    return collected


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
            from hledac.universal.brain.dspy_optimizer import DSPyOptimizer

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
            from hledac.universal.brain.dspy_optimizer import load_optimized_prompts

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
            from hledac.universal.brain.prompt_bandit import PromptBandit

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
        from hledac.universal.brain.distillation_engine import distil
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


class SynthesisOutcome(msgspec.Struct, gc=False):
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


class UncertaintyFlags(msgspec.Struct, gc=False):
    """
    APEX-1009: Measured uncertainty metadata for hallucination detection.

    Captures token-level entropy from generation and compares with
    LLM-self-reported confidence to detect potential hallucinations.

    Fields:
        measured_entropy: Average token entropy in bits (0.0-4.0 typical range)
        entropy_stability: 1.0 - (std/mean) of per-token entropy (0.0-1.0)
        implied_confidence: Entropy-implied confidence 0.0-1.0 (1.0 = max certainty)
        confidence_divergence: |self_reported_confidence - implied_confidence| (0.0-1.0)
        hallucination_risk: True if divergence > threshold (>0.3)
        risk_level: "low" | "medium" | "high" based on divergence magnitude
        token_count: Number of tokens analyzed for entropy
    """
    measured_entropy: float = 0.0
    entropy_stability: float = 1.0
    implied_confidence: float = 1.0
    confidence_divergence: float = 0.0
    hallucination_risk: bool = False
    risk_level: str = "low"
    token_count: int = 0


def uncertainty_gate(
    self_reported_confidence: float,
    token_logprobs: list[float],
    threshold: float = 0.3,
) -> UncertaintyFlags:
    """
    APEX-1009: Compute uncertainty flags from token-level logprobs.

    Compares LLM-self-reported confidence with measured token entropy
    to detect potential hallucinations.

    Args:
        self_reported_confidence: LLM's self-reported confidence (0.0-1.0)
        token_logprobs: List of log probabilities for each generated token
        threshold: Divergence threshold for hallucination_risk flag (default 0.3)

    Returns:
        UncertaintyFlags with measured entropy, stability, divergence, and risk level

    Algorithm:
        1. Compute mean entropy from logprobs: H = -mean(logprobs) / ln(2) (convert to bits)
        2. Compute entropy stability: 1.0 - (std(logprobs) / |mean(logprobs)|)
        3. Convert entropy to implied confidence: 1.0 - (H / max_entropy) where max_entropy=4.0 bits
        4. Compute divergence: |self_reported - entropy_implied|
        5. Flag hallucination_risk if divergence > threshold
        6. Assign risk_level: "low" (<0.2), "medium" (0.2-0.4), "high" (>0.4)

    Fail-soft: Returns default UncertaintyFlags on any error.
    """
    try:
        if not token_logprobs:
            return UncertaintyFlags()

        import math

        # Convert logprobs to entropy (bits)
        # logprob is ln(p), so -logprob is -ln(p) = ln(1/p)
        # Entropy H = -Σ p*log(p), but for token-level we use -mean(logprobs) / ln(2)
        logprobs_array = [lp for lp in token_logprobs if math.isfinite(lp)]
        if not logprobs_array:
            return UncertaintyFlags()

        mean_logprob = sum(logprobs_array) / len(logprobs_array)
        measured_entropy = -mean_logprob / math.log(2)  # Convert to bits

        # Compute stability: 1.0 - coefficient_of_variation
        if len(logprobs_array) > 1:
            variance = sum((lp - mean_logprob) ** 2 for lp in logprobs_array) / (len(logprobs_array) - 1)
            std_logprob = math.sqrt(variance)
            cv = std_logprob / abs(mean_logprob) if mean_logprob != 0 else 0.0
            entropy_stability = max(0.0, min(1.0, 1.0 - cv))
        else:
            entropy_stability = 1.0

        # Convert entropy to implied confidence
        # Max entropy for typical vocab is ~4.0 bits (16-way choice)
        # Higher entropy = lower confidence
        max_entropy = 4.0
        entropy_implied_confidence = max(0.0, min(1.0, 1.0 - (measured_entropy / max_entropy)))

        # Compute divergence
        confidence_divergence = abs(self_reported_confidence - entropy_implied_confidence)

        # Flag hallucination risk
        hallucination_risk = confidence_divergence > threshold

        # Assign risk level
        if confidence_divergence < 0.2:
            risk_level = "low"
        elif confidence_divergence < 0.4:
            risk_level = "medium"
        else:
            risk_level = "high"

        return UncertaintyFlags(
            measured_entropy=round(measured_entropy, 3),
            entropy_stability=round(entropy_stability, 3),
            implied_confidence=round(entropy_implied_confidence, 3),
            confidence_divergence=round(confidence_divergence, 3),
            hallucination_risk=hallucination_risk,
            risk_level=risk_level,
            token_count=len(logprobs_array),
        )
    except Exception as e:
        logger.debug(f"uncertainty_gate failed (fail-soft): {e}")
        return UncertaintyFlags()


# ── UNIFIED-004: IoC-type → alternative protocol mapping ──────────────
# Maps IoC types to ordered list of alternative discovery protocols for
# micro-sprint re-fetching. Protocols are tried in order — first success
# with entropy improvement terminates the micro-sprint.

_IOC_PROTOCOL_MAP: dict[str, list[str]] = {
    "ip": ["shodan", "censys", "bgp", "passive_dns"],
    "domain": ["ct", "passive_dns", "doh", "wayback"],
    "hash": ["dht", "commoncrawl", "url"],
    "url": ["wayback", "commoncrawl", "gopher", "url"],
    "onion": ["url", "passive_dns"],
    "cve": [],  # CVE is canonical — no alternative source needed
    "apt": [],  # APT attribution is textual — re-fetching won't help
    "malware": ["dht", "url"],
    "btc": ["blockchain", "passive_dns"],
    "email": ["passive_dns", "url"],
}

# Always-appended fallback protocols (protocol-agnostic discovery)
_FALLBACK_PROTOCOLS: list[str] = ["url", "ct"]

# Per-IoC-type max protocols to try (M1 8GB — bounded micro-sprint)
_MAX_PROTOCOLS_PER_ENTITY: int = 4


def _resolve_alternative_protocols(
    ioc_type: str,
    entity_value: str = "",
) -> list[str]:
    """
    UNIFIED-004: Resolve ordered list of alternative protocols for an IoC type.

    Returns a deduplicated list of ≤ _MAX_PROTOCOLS_PER_ENTITY protocols,
    with type-specific protocols first, then fallback protocols.
    Empty list means no useful alternative source exists.

    Args:
        ioc_type: IoC type from IOCEntity.ioc_type (e.g., "ip", "domain", "hash")
        entity_value: The entity value, used for protocol filtering
                     (e.g., .onion URLs can't use clearnet CT logs)

    Returns:
        Ordered list of protocol names (max 4)
    """
    ioc_type_lower = ioc_type.lower().strip()
    protocols: list[str] = _IOC_PROTOCOL_MAP.get(ioc_type_lower, []).copy()

    # Filter protocols based on entity value characteristics
    entity_lower = entity_value.lower()
    if entity_lower.endswith(".onion") or entity_lower.endswith(".i2p"):
        # Darknet entities can't use clearnet protocols like shodan, censys, ct
        protocols = [p for p in protocols if p not in ("shodan", "censys", "ct", "bgp")]

    # Append fallback protocols (deduplicated)
    for fp in _FALLBACK_PROTOCOLS:
        if fp not in protocols:
            protocols.append(fp)

    # Cap at max per entity
    return protocols[:_MAX_PROTOCOLS_PER_ENTITY]


class IOCEntity(msgspec.Struct, gc=False):
    """Jedna IOC entita extrahovaná z findingu."""
    value: str
    ioc_type: str  # "cve","ip","hash","onion","domain","apt","malware","btc"
    severity: str   # "critical","high","medium","low"
    context: str    # 1 věta
    # APEX-1008: Token-level uncertainty from logprobs extraction
    confidence: float = 1.0  # 0.0-1.0, derived from token entropy
    uncertainty_flag: str = "normal"  # "normal", "elevated", "high_entropy"


class OSINTReport(msgspec.Struct, gc=False):
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
    uncertainty_flags: UncertaintyFlags | None = None  # APEX-1009


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


# ---------------------------------------------------------------------------
# Issue #A5: SynthesisSession async context manager + SynthesisContext dataclass
# Guarantees SynthesisRunner.cleanup() on all exit paths (exception, success, ImportError)
# ---------------------------------------------------------------------------

from dataclasses import dataclass, field


@dataclass
class SynthesisContext:
    """
    Input context for SynthesisSession.

    Fields:
        query: Original sprint query string.
        findings: List of finding dicts to synthesize.
        lifecycle: Optional ModelLifecycle instance (for explicit unload).
        force_synthesis: Always run synthesis even if disabled (default True).
        max_findings: Optional cap on findings passed to synthesis.
    """
    query: str
    findings: list
    lifecycle: Any = None
    force_synthesis: bool = True
    max_findings: int | None = None


class SynthesisSession:
    """
    Async context manager that wraps SynthesisRunner with guaranteed cleanup.

    Guarantees:
        - __aexit__ always calls runner.close() regardless of exit path
        - lifecycle.unload() called before close (ensures MLX cleanup)
        - Safe when runner was never created (ImportError path)

    Usage:
        synth_ctx = SynthesisContext(query="...", findings=[...])
        async with SynthesisSession(synth_ctx) as session:
            report = await session.synthesize_findings()
    """

    __slots__ = ("_ctx", "_runner", "_inited")

    def __init__(self, ctx: SynthesisContext) -> None:
        self._ctx = ctx
        self._runner: Any = None
        self._inited: bool = False

    async def __aenter__(self) -> "SynthesisSession":
        return self

    async def __aexit__(self, *_: Any) -> None:
        """Guaranteed cleanup — runs even when synthesize_findings raised."""
        # Always attempt close if runner was injected OR lazily created
        if self._runner is None:
            # ImportError path — runner was never created, nothing to close
            return
        try:
            await self._runner.close()  # type: ignore[union-attr]
        except Exception as e:  # noqa: BLE001
            pass

    async def synthesize_findings(
        self,
        findings: list[dict] | None = None,
        max_findings: int = 10,
        force_synthesis: bool = False,
    ) -> OSINTReport | None:
        """
        Lazily initialises runner and proxies to SynthesisRunner.synthesize_findings().

        Args:
            findings: Override findings list (default: use ctx.findings).
            max_findings: Max findings to pass (default: 10).
            force_synthesis: Override force_synthesis (default: False).
        """
        if not self._inited:
            runner = SynthesisRunner(
                query=self._ctx.query,
                findings=self._ctx.findings,
                force_synthesis=self._ctx.force_synthesis,
            )
            if self._ctx.lifecycle is not None:
                runner._lifecycle = self._ctx.lifecycle
            self._runner = runner
            self._inited = True

        _findings = findings if findings is not None else self._ctx.findings
        _max = self._ctx.max_findings if self._ctx.max_findings is not None else max_findings

        return await self._runner.synthesize_findings(
            self._ctx.query,
            _findings,
            max_findings=_max,
            force_synthesis=force_synthesis,
        )


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


# NEXUS-018-04: Lazy singleton for the Rust finding collapser.
# Thread-safe via parking_lot::RwLock on the Rust side.
# Returns the finding_collapser submodule or None if unavailable.
# Module-level cache — import + getattr done exactly once.
_COLLAPSER_CACHE: Any | None = None


def _get_collapser() -> Any:
    """Return the Rust finding_collapser module (lazy, cached).

    Falls back to None if Rust extension not available.
    Always returns the same module object once initialized.
    """
    global _COLLAPSER_CACHE
    if _COLLAPSER_CACHE is not None:
        return None if _COLLAPSER_CACHE is False else _COLLAPSER_CACHE

    try:
        import hledac_rust_extensions as _hre
        mod = getattr(_hre, "finding_collapser", None)
        _COLLAPSER_CACHE = mod if mod is not None else False
        return mod
    except ImportError:
        _COLLAPSER_CACHE = False
        return None


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
                 "_collapser",  # NEXUS-018-04: Rust finding collapser singleton
                 # Issue #20: KV cache params — initialized from ModelLifecycle or hardcoded defaults
                 "_kv_bits", "_max_kv_size",
                 # Cached Metal memory probe (Issue #20-A: avoid per-call Rust FFI)
                 "_metal_probe_cache",
                 # L-05: Synthesis strategy for _race_inference dispatch
                 "_synthesis_strategy")


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

        # L-05: Synthesis strategy — "sequential_preferred" or "race_first_wins"
        self._synthesis_strategy: str = SYNTHESIS_STRATEGY

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

        # NEXUS-018-04: Rust finding collapser — lazy singleton
        self._collapser: Any | None = None

        # ISSUE-009: Speculative URL/IP detection results from streaming generation
        self._speculative_urls: list[str] = []
        self._speculative_ips: list[str] = []

    # ISSUE-009: Public accessors for speculative detection results
    def get_speculative_urls(self) -> list[str]:
        """Return URLs detected during streaming generation."""
        return self._speculative_urls

    def get_speculative_ips(self) -> list[str]:
        """Return IP addresses detected during streaming generation."""
        return self._speculative_ips

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
    # Issue #20 improvement: Adaptive KV cache methods
    # G2: Now delegates to brain.kv_cache_config — single source of truth
    # ------------------------------------------------------------------

    def _probe_metal_memory(self) -> tuple[int, str, tuple[int, int, int]]:
        """
        Issue #20-A + G2: Delegates to MetalProbe in kv_cache_config.

        Caches by active_bytes bucket (rounded to 64 MiB) to handle
        repeated calls within the same synthesis batch.

        Returns:
            (kv_bits, tier_name, (emergency_bytes, critical_bytes, warn_bytes))
        """
        from brain.kv_cache_config import get_metal_probe

        probe = get_metal_probe()
        result = probe.probe()
        from brain.kv_cache_config import get_metal_tier_thresholds

        thresholds = get_metal_tier_thresholds()

        # Map MemoryTier to legacy string tier
        tier_map = {
            "normal": "normal",
            "warn": "medium",
            "critical": "high",
            "emergency": "emergency",
        }
        tier_str = tier_map.get(result.tier.value, "normal")
        kv_bits = max(4, self._kv_bits)

        # Round to 64 MiB bucket for cache stability (matches old logic)
        bucket = (result.active_bytes // (64 * 1024 * 1024)) * (64 * 1024 * 1024)
        self._metal_probe_cache[bucket] = (kv_bits, tier_str, thresholds)
        return (kv_bits, tier_str, thresholds)

    def _get_adaptive_kv_bits(self) -> int:
        """G2: Delegates to brain.kv_cache_config.get_kv_cache_config()."""
        from brain.kv_cache_config import get_kv_cache_config

        config = get_kv_cache_config(kv_bits_override=self._kv_bits)
        return config.kv_bits

    def _get_kv_cache_kwargs(
        self,
        input_tokens: int | None = None,
        max_tokens: int | None = None,
    ) -> dict:
        """
        G2: Delegates to brain.kv_cache_config.get_kv_cache_config().

        Single source of truth for KV cache sizing on M1 8GB.
        """
        from brain.kv_cache_config import get_kv_cache_config

        config = get_kv_cache_config(
            input_tokens=input_tokens,
            max_tokens=max_tokens,
            kv_bits_override=self._kv_bits,
        )
        return config.as_kwargs()

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

    # ------------------------------------------------------------------
    # Public synthesis API
    # ------------------------------------------------------------------

    # =======================================================================
    # Sub-pipeline steps — each is a focused async method.
    # Complexity per method: 1-7 (vs original 43).
    # =======================================================================

    async def _synth_phase1_guards(
        self,
        query: str,
        findings: list[dict],
        force_synthesis: bool,
    ) -> bool:
        """
        Phase 1: Pre-flight guard checks.

        Returns True to proceed, False to abort (method sets outcome and returns None).
        Guards:
          - WINDUP lifecycle gate
          - M1 8GB UMA RSS ceiling (5.5 GiB)
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
            return False

        # B.7: UMA RSS > 5.5GiB guard
        if not self._check_uma_guard():
            self._stix_status = "unavailable"
            self._stix_reason = "UMA guard blocked synthesis — RSS > 5.5GiB or EMERGENCY"
            self._stix_backend = ""
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
            return False

        return True

    # ── BLITZ-10: Fast-path triage ─────────────────────────────────────────

    async def _synth_triage_findings(
        self,
        query: str,
        findings: list[dict],
    ) -> tuple[list[dict], dict[str, int | float]]:
        """
        BLITZ-10: Pre-filter findings using FastPathTriage before Hermes-3B.

        Runs in a thread to avoid blocking the event loop. Returns filtered
        findings + triage telemetry for sprint scoreboard.

        Fail-safe: any error → returns all findings unfiltered (conservative).
        """
        from hledac.universal.brain.fast_path_triage import FastPathTriage
        import os

        if os.environ.get("HLEDAC_TRIAGE_DISABLED", "0") == "1":
            return findings, {"total_triaged": len(findings), "filtered_out": 0}

        try:
            triage = FastPathTriage(query)
            loop = asyncio.get_running_loop()

            # Extract text payloads for triage
            texts: list[str] = []
            for f in findings:
                if isinstance(f, dict):
                    # Extract text from payload_text (primary), fallback to str(f)
                    payload = f.get("payload_text", "")
                    if not payload:
                        payload = f.get("payload", "")
                    if not payload:
                        payload = str(f)
                    texts.append(payload if isinstance(payload, str) else str(payload))
                else:
                    texts.append(str(f))

            # Run triage in thread pool (involves Rust hashing + optional embeddings)
            results = await loop.run_in_executor(
                None,  # default executor
                lambda: triage.triage_batch(texts),
            )

            filtered: list[dict] = [
                f for f, keep in zip(findings, results) if keep
            ]
            stats = triage.stats
            filtered_out = stats.get("filtered_out", 0)
            noise_pct = stats.get("noise_reduction_pct", 0)

            if filtered_out > 0:
                logger.info(
                    "[BLITZ-10] Triage filtered %d/%d findings (%.1f%% noise reduction)",
                    filtered_out,
                    len(findings),
                    noise_pct,
                )

            return filtered, stats

        except Exception:
            logger.debug("[BLITZ-10] Triage failed — passing through all findings", exc_info=True)
            return findings, {"total_triaged": len(findings), "filtered_out": 0, "error": True}

    async def _synth_phase2_parallel_discovery(
        self,
        query: str,
        findings: list[dict],
    ) -> tuple[str | None, str, str, str]:
        """
        Phase 2: Parallel I/O-bound discovery — model + stix + episode + RAG.

        Returns (model_path, stix_context, episode_ctx, rag_context).
        All four tasks run concurrently via TaskGroup (eager_start=True).
        Serial cost: ~5-12s. Parallel cost: ~max of individual tasks (3-5s).
        Fail-soft: individual task failures return empty string / None.
        """
        model_path: str | None = None
        stix_context = ""
        episode_ctx = ""
        rag_context = ""

        try:
            async with asyncio.TaskGroup() as tg:
                tg_model = tg.create_task(self._ensure_model(), name="syn:model", eager_start=True)
                tg_stix = tg.create_task(self._build_stix_context(), name="syn:stix", eager_start=True)
                if self._duckdb_store is not None:
                    tg_ep = tg.create_task(
                        self._build_episode_context(self._duckdb_store, query), name="syn:ep", eager_start=True
                    )
                else:
                    tg_ep = None
                tg_rag = tg.create_task(
                    self._rag_query_safe(query, findings), name="syn:rag", eager_start=True
                )
        except ExceptionGroup as eg:
            logger.debug("[SYNTHESIS] Parallel discovery partial failure: %s", eg)

        # Extract results — re-raise cancellation as None/empty
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

        return model_path, stix_context, episode_ctx, rag_context

    async def _synth_phase3_rerank_and_graphrag(
        self,
        query: str,
        findings: list[dict],
        max_findings: int,
    ) -> tuple[list[dict], str]:
        """
        Phase 3: Rerank findings (ONNX thread) + GraphRAG IOC relationships.

        Returns (top_findings, graph_context).
        Rerank falls back to confidence-sort on error (~200-500ms).
        GraphRAG is fail-soft (returns "" on error).
        """
        # Issue #12.2: Flashrank ONNX rerank in thread — avoid blocking event loop
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

        return top, graph_context

    async def _synth_phase3_5_collapse_and_categorize(
        self,
        findings: list[dict],
    ) -> str:
        """NEXUS-018-04 Phase 3.5: Collapse findings into structured IOC tree.

        Runs in asyncio.to_thread to avoid blocking the event loop.
        Skipped entirely when len(findings) <= _HLEDAC_COLLAPSE_THRESHOLD.

        Returns pre-collapsed Markdown string, or "" if collapse fails.
        The empty string is handled gracefully by _synth_phase4_build_prompt.
        """
        if len(findings) <= _HLEDAC_COLLAPSE_THRESHOLD:
            return ""

        collapser = _get_collapser()
        if collapser is None:
            logger.debug("[SYNTHESIS] Collapser unavailable — using flat findings")
            return ""

        try:
            # Serialize findings as JSON bytes (msgspec-compatible plain JSON)
            findings_bytes = _msgspec_encode(findings)

            def _collapse_sync() -> bytes:
                # Thread-safe call into Rust collapser — uses parking_lot::RwLock internally
                return collapser.collapse_findings(findings_bytes)

            result_bytes = await asyncio.to_thread(_collapse_sync)
            if result_bytes:
                return result_bytes.decode("utf-8", errors="replace")
            return ""
        except Exception as e:
            logger.debug("[SYNTHESIS] Collapser failed: %s — falling back to flat findings", e)
            return ""

    async def _synth_phase4_build_prompt(
        self,
        query: str,
        stix_context: str,
        episode_ctx: str,
        rag_context: str,
        graph_context: str,
        top: list[dict],
        findings_count: int,
        collapsed_markdown: str = "",
    ) -> str:
        """
        Phase 4: Build synthesis prompt from findings + RAG + GraphRAG + STIX context.

        NEXUS-018-04: When collapsed_markdown is non-empty (from Phase 3.5),
        the LLM receives a structured IOC tree instead of flat text. This
        reduces inference latency from 8-15s to ~1.5s and eliminates the
        99% data loss from naive truncation.

        Zero-findings path: query-focused fallback prompt.
        Normal path: structured prompt with context layers.
        """
        if findings_count == 0:
            return (
                f"Query: {query}{stix_context}\n"
                f"Findings:\n[No findings collected during this sprint]\n"
                f"Current timestamp: {time.time()}\n"
                f"Note: Provide a threat intelligence report based on the query and general knowledge."
            )

        # NEXUS-018-04: Structured collapser output — richer, more compact
        if collapsed_markdown:
            context_parts = []
            if episode_ctx:
                context_parts.append(episode_ctx)
            if rag_context:
                context_parts.append(rag_context)
            if graph_context:
                context_parts.append(graph_context)
            header = f"Query: {query}{stix_context}\n"
            if context_parts:
                return (
                    f"{chr(10).join(context_parts)}\n\n---\n"
                    f"{header}\n"
                    f"{collapsed_markdown}\n"
                    f"Current timestamp: {time.time()}"
                )
            else:
                return (
                    f"{header}\n"
                    f"{collapsed_markdown}\n"
                    f"Current timestamp: {time.time()}"
                )

        # Legacy flat path — used when no collapser or findings below threshold
        findings_text = "\n".join(
            f"- [{f.get('source_type', '?')}] {f.get('text', '')[:200]}"
            for f in top
        )

        context_parts = []
        if episode_ctx:
            context_parts.append(episode_ctx)
        if rag_context:
            context_parts.append(rag_context)
        if graph_context:
            context_parts.append(graph_context)

        if context_parts:
            return (
                f"{chr(10).join(context_parts)}\n\n---\n"
                f"Query: {query}{stix_context}\n"
                f"Findings:\n{findings_text}\n"
                f"Current timestamp: {time.time()}"
            )
        else:
            return (
                f"Query: {query}{stix_context}\n"
                f"Findings:\n{findings_text}\n"
                f"Current timestamp: {time.time()}"
            )

    async def _synth_phase5_prompt_optimization(
        self,
        prompt: str,
    ) -> str:
        """
        Phase 5: DSPy optimized prompts + Bandit arm selection + modifier injection.

        Returns the (possibly modified) prompt.
        DSPy and Bandit are fail-soft — on error, prompt is returned unchanged.
        """
        # Sprint F234: DSPy optimized prompts — try to load from cache first
        dspy_prompts = _get_dspy_prompts()
        if dspy_prompts:
            dspy_opt = _get_dspy_optimizer(self._lifecycle)
            if dspy_opt is not None:
                try:
                    optimized = dspy_opt.get_prompt('analysis', {'complexity': 'medium'})
                    if optimized:
                        self.set_custom_prompt(optimized)
                        logger.info(f"[SYNTHESIS] DSPy optimized prompt loaded ({len(optimized)} chars)")
                except Exception:  # noqa: BLE001
                    pass
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

        return prompt

    async def _synth_phase6_inference(
        self,
        prompt: str,
    ) -> tuple[dict | None, str, list[float]]:
        """
        Phase 6: Context compression → model load → race inference → unload on fallback.

        Returns (raw_dict, used_engine, token_logprobs).
        unload() + gc.collect() is called only when ALL engines failed (raw_dict is None).
        Fail-soft: compression errors use original prompt.
        """
        # F234: Context compression — compress prompt if it exceeds threshold
        if self._compression_threshold > 0 and self._compressor is not None:
            prompt_len = len(prompt)
            if prompt_len > self._compression_threshold:
                try:
                    compressed = await self._compressor.compress_context(prompt)
                    compressed_prompt = compressed.critical_content
                    logger.info(
                        f"[SYNTHESIS] Context compressed: {prompt_len} → {len(compressed_prompt)} chars "
                        f"(ratio={compressed.compression_ratio:.2f})"
                    )
                    prompt = compressed_prompt
                except Exception as e:
                    logger.warning(f"[SYNTHESIS] Context compression failed (using original prompt): {e}")

        # Issue #12.3 + #12.4: RACE inference — parallel xgrammar + streaming + constrained.
        # Pre-load model once, then race all three engines. Take first successful result.
        raw_dict: dict | None = None
        used_engine = "none"
        token_logprobs: list[float] = []
        try:
            model, tokenizer, _model_path = await self._lifecycle._ensure_loaded()
        except RuntimeError as e:
            logger.warning("[SYNTHESIS] Model load failed for race: %s", e)
            raw_dict, used_engine, token_logprobs = None, "none", []
        else:
            raw_dict, used_engine, token_logprobs = await self._race_inference(prompt)

        # Issue #12.4: unload() only when ALL engines failed (real fallback happened)
        if raw_dict is None:
            await self._lifecycle.unload()
            gc.collect()

        return raw_dict, used_engine, token_logprobs

    async def _synth_phase7_parse_and_validate(
        self,
        raw_dict: dict,
        used_engine: str,
        findings: list[dict],
        bandit: Any,
        arm_used: str,
        query: str,
        findings_count: int,
        token_logprobs: list[float] | None = None,
    ) -> OSINTReport | None:
        """
        Phase 7: Parse raw_dict → OSINTReport → validate → confidence → bandit reward → hypothesis.

        Returns OSINTReport on success, None on parse failure.
        All validations are fail-soft — never block report production.

        APEX-1008: token_logprobs propagated to _parse_raw_to_osintreport for
        per-entity uncertainty measurement.
        """
        used_outlines = used_engine in ("streaming", "constrained")
        report = self._parse_raw_to_osintreport(raw_dict, token_logprobs=token_logprobs)
        if report is None:
            return None

        report.confidence = self._compute_confidence(report, used_outlines)

        # [FINAL]-019: Absence Mining Engine — run AFTER confidence computation
        # Detects structural absences (CT-virgin domains, orphan IPs, etc.)
        # and adjusts the computed confidence scores accordingly.
        _absence_report = None
        try:
            from .absence_mining import get_absence_engine, AbsenceReport as _AbsenceReport
            absence_enabled = os.environ.get(
                'HLEDAC_ENABLE_ABSENCE_MINING', '1',
            ).lower() in ('1', 'true', 'yes', 'on')
            if absence_enabled and self._duckdb_store is not None:
                absence_engine = await get_absence_engine(self._duckdb_store)
                _absence_report: _AbsenceReport = await absence_engine.run(
                    report, self._duckdb_store,
                )
                if _absence_report.absences:
                    logger.info(
                        "[SYNTHESIS] [FINAL]-019: Absence mining found %d absences "
                        "(checked=%d, refetch=%s)",
                        len(_absence_report.absences),
                        _absence_report.total_checked,
                        _absence_report.should_trigger_refetch,
                    )
                    # Apply absence-based confidence adjustment to computed confidence
                    adjusted_conf = absence_engine.apply_confidence_adjustment(
                        report, _absence_report,
                    )
                    if adjusted_conf != report.confidence:
                        logger.debug(
                            "[SYNTHESIS] [FINAL]-019: Confidence adjusted %.3f → %.3f "
                            "due to %d absence findings",
                            report.confidence,
                            adjusted_conf,
                            len(_absence_report.confidence_adjustments),
                        )
                        report.confidence = adjusted_conf
        except ImportError:
            logger.debug(
                "[SYNTHESIS] [FINAL]-019: AbsenceMiningEngine unavailable "
                "(dependency missing) — skipping",
            )
        except Exception as e:
            logger.debug(
                "[SYNTHESIS] [FINAL]-019: Absence mining exception (fail-soft): %s",
                e,
            )

        # APEX-1009: Run uncertainty gate — compare self-reported confidence with measured entropy
        if token_logprobs:
            uncertainty_flags = uncertainty_gate(report.confidence, token_logprobs)
            report.uncertainty_flags = uncertainty_flags

            # UNIFIED-003 / UNIFIED-004: Two-path entropy alert emission:
            #   Path A: hallucination_risk (divergence > 0.3) — confidence mismatch
            #   Path B: measured_entropy > ENTROPY_THRESHOLD_BITS (1.5) — absolute uncertainty
            # Both paths emit EntropyAlerts with alternative protocol suggestions.
            _ENTROPY_THRESHOLD_BITS = 1.5

            should_emit_alerts = (
                uncertainty_flags.hallucination_risk
                or uncertainty_flags.measured_entropy > _ENTROPY_THRESHOLD_BITS
            )

            if should_emit_alerts:
                # UNIFIED-003: Gate entropy alert emission on feature flag.
                # HLEDAC_ENABLE_ENTROPY_FEEDBACK=1 (default ON) enables the
                # closed-loop auto-remediation pipeline. Set to 0 to opt out.
                _entropy_feedback_enabled = os.environ.get(
                    'HLEDAC_ENABLE_ENTROPY_FEEDBACK', '1',
                ).lower() in ('1', 'true', 'yes', 'on')

                if _entropy_feedback_enabled:
                    if uncertainty_flags.hallucination_risk:
                        logger.warning(
                            "[SYNTHESIS] APEX-1009 hallucination_risk: "
                            "self_reported=%.3f, measured_entropy=%.3f bits, "
                            "divergence=%.3f, risk=%s",
                            report.confidence,
                            uncertainty_flags.measured_entropy,
                            uncertainty_flags.confidence_divergence,
                            uncertainty_flags.risk_level,
                        )
                    else:
                        logger.info(
                            "[SYNTHESIS] UNIFIED-003 high-entropy threshold "
                            "exceeded: measured_entropy=%.3f bits > %.1f, "
                            "confidence=%.3f",
                            uncertainty_flags.measured_entropy,
                            _ENTROPY_THRESHOLD_BITS,
                            report.confidence,
                        )

                    # UNIFIED-003: Emit EntropyAlert to trigger re-fetch
                    # via EntropyFetchBridge
                    try:
                        from .uncertainty_quant import (
                            EntropyAlert, get_entropy_bridge,
                        )
                        bridge = get_entropy_bridge()
                        if bridge is not None:
                            # Emit alert for each high-uncertainty IOC entity
                            for ioc_entity in (report.ioc_entities or [])[:5]:
                                entity_value = getattr(
                                    ioc_entity, 'value', str(ioc_entity),
                                )
                                ioc_type = getattr(
                                    ioc_entity, 'ioc_type', 'unknown',
                                )
                                # UNIFIED-004: IoC-type-aware protocol selection
                                alt_protocols = _resolve_alternative_protocols(
                                    ioc_type=ioc_type,
                                    entity_value=entity_value,
                                )
                                alert = EntropyAlert(
                                    entity_id=entity_value[:100],
                                    entropy=round(1.0 - uncertainty_flags.implied_confidence, 3),  # UNIFIED-003: normalized 0-1
                                    threshold_exceeded=_ENTROPY_THRESHOLD_BITS,
                                    confidence=report.confidence,
                                    risk_level=uncertainty_flags.risk_level,
                                    metadata={
                                        "token_count": uncertainty_flags.token_count,
                                        "stability": uncertainty_flags.entropy_stability,
                                        "divergence": uncertainty_flags.confidence_divergence,
                                        "ioc_type": ioc_type,
                                        "alternative_protocols": alt_protocols,
                                        "trigger_path": (
                                            "hallucination_risk"
                                            if uncertainty_flags.hallucination_risk
                                            else "high_entropy"
                                        ),
                                    },
                                )
                                await bridge.emit(alert)
                            logger.debug(
                                "[SYNTHESIS] UNIFIED-003: Emitted %d "
                                "EntropyAlert(s) to bridge (trigger=%s)",
                                min(len(report.ioc_entities or []), 5),
                                alert.metadata.get("trigger_path", "unknown"),
                            )
                    except Exception as e:
                        logger.debug(
                            "[SYNTHESIS] UNIFIED-003: EntropyAlert emit "
                            "failed (fail-soft): %s", e,
                        )
                else:
                    logger.debug(
                        "[SYNTHESIS] UNIFIED-003: Entropy feedback disabled "
                        "(HLEDAC_ENABLE_ENTROPY_FEEDBACK=0) — alert suppressed",
                    )
            else:
                logger.debug(
                    "[SYNTHESIS] APEX-1009 uncertainty_gate passed: "
                    "divergence=%.3f, risk=%s, tokens=%d",
                    uncertainty_flags.confidence_divergence,
                    uncertainty_flags.risk_level,
                    uncertainty_flags.token_count,
                )
        else:
            # No logprobs available (xgrammar/structured engines) — default flags
            report.uncertainty_flags = UncertaintyFlags()
            logger.debug("[SYNTHESIS] APEX-1009: no token_logprobs — uncertainty gate skipped")

        # [META]-011: ContradictionBridge — emit EntropyAlert for propositional
        # contradictions detected by AdversarialVerifier.
        # Bridges: AdversarialVerifier → EntropyAlert → EntropyFetchBridge → FetchCoordinator.
        try:
            from .contradiction_bridge import get_contradiction_bridge
            _cb_enabled = os.environ.get(
                "HLEDAC_ENABLE_CONTRADICTION_FEEDBACK", "1",
            ).lower() in ("1", "true", "yes", "on")
            if _cb_enabled and self._hypothesis_engine is not None:
                cb = get_contradiction_bridge()
                # Build Evidence objects from findings for AdversarialVerifier
                from hledac_hypothesis.types.evidence import Evidence
                from datetime import UTC as _utc, datetime as _dt
                _evidence_list: list[Evidence] = [
                    Evidence(
                        evidence_id=f"ev_{f.get('id', str(i))[:12]}",
                        source=str(f.get("source_type", "unknown")),
                        content=(f.get("payload_text", "") or "")[:500],
                        timestamp=_dt.now(_utc),
                        reliability=float(f.get("confidence", 0.5)),
                    )
                    for i, f in enumerate(findings[:100])
                    if f.get("payload_text")
                ]
                if _evidence_list:
                    from hledac_hypothesis.adversarial import AdversarialVerifier
                    _verifier = AdversarialVerifier(
                        hypothesis_engine=self._hypothesis_engine,
                    )
                    _contradictions = _verifier.detect_contradictions(_evidence_list)
                    if _contradictions:
                        _alerts = cb.build_alerts(
                            contradictions=_contradictions,
                            ioc_entities=report.ioc_entities or [],
                            findings=findings,
                            sprint_id="",
                        )
                        if _alerts:
                            _entropy_bridge = get_entropy_bridge()
                            for _alert in _alerts:
                                await _entropy_bridge.emit(_alert)
                            logger.info(
                                "[SYNTHESIS] [META]-011: Emitted %d "
                                "contradiction EntropyAlert(s) (severity > 0.7)",
                                len(_alerts),
                            )
                            # [META-008] Trigger async auto-retraction of systematic dissenters
                            await cb._auto_retract_systematic_dissenters()
        except Exception as _e:
            logger.debug(
                "[SYNTHESIS] [META]-011: ContradictionBridge failed "
                "(fail-soft): %s", _e,
            )

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
        if bandit is not None and arm_used:
            try:
                response_text = (
                    report.threat_summary + " " +
                    " ".join(str(e) for e in report.ioc_entities) +
                    " ".join(report.threat_actors)
                )
                response_len_norm = min(1.0, len(response_text) / 2000.0)
                reward = response_len_norm * report.confidence
                bandit.update_reward(arm_used, reward, reward)
                logger.info(f"[SYNTHESIS] Bandit reward: arm={arm_used} reward={reward:.3f}")
            except Exception as e:
                logger.debug(f"[SYNTHESIS] Bandit update failed: {e}")

        # F214: Extract testable hypotheses from synthesis output
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

        # ISSUE [ADVERSARY]-002: Drop findings sourced from cognitive tarpit domains
        # before they reach the LLM context window. Prevents:
        #   1. IOC poisoning (fake C2 IPs, decoy BTC addresses from honeypot forums)
        #   2. Token waste on LLM-generated content in synthesis context
        #   3. Cross-contamination of pivot operations with honeypot IOCs
        #
        # Check: any finding whose source URL domain has cognitive_tarpit_score >= 1.0
        # is excluded from the report's ioc_entities list.
        # Note: cognitive_tarpit_score is monotonically non-decreasing (once set, stays).
        _ct_filter_enabled = os.environ.get(
            'HLEDAC_ENABLE_COGNITIVE_TARPIT', '1',
        ).lower() in ('1', 'true', 'yes', 'on')

        if _ct_filter_enabled and report.ioc_entities and findings:
            try:
                from hledac.universal.knowledge.domain_reputation import (
                    get_domain_reputation_service as _get_rep_svc,
                )
                _rep_svc = _get_rep_svc()
                if _rep_svc is not None:
                    # ISSUE [ADVERSARY]-002: Batch domain reputation lookups with asyncio.gather
                    # Avoids N sequential awaits (N× latency). N=10 findings → ~50ms sequential
                    # vs ~15ms parallel with gather.
                    #
                    # Step 1: extract + deduplicate domains from findings sources
                    _domains_to_check: list[str] = []
                    _seen: set[str] = set()
                    for f in findings:
                        _src_url = f.get('url') or f.get('source_url') or ''
                        if _src_url:
                            try:
                                from urllib.parse import urlparse
                                _parsed = urlparse(_src_url)
                                _fdomain = _parsed.netloc.removeprefix('www.')
                                if _fdomain and _fdomain not in _seen:
                                    _seen.add(_fdomain)
                                    _domains_to_check.append(_fdomain)
                            except Exception:  # noqa: BLE001 — fail-soft
                                pass

                    # Step 2: parallel reputation lookup — single round-trip per domain
                    if _domains_to_check:
                        try:
                            _reps = await asyncio.gather(
                                *[_rep_svc.get(d) for d in _domains_to_check],
                                return_exceptions=True,
                            )
                            _tarpit_domains: set[str] = set()
                            for _d, _rep in zip(_domains_to_check, _reps):
                                if (
                                    isinstance(_rep, Exception)
                                    or _rep is None
                                ):
                                    continue
                                if _rep.cognitive_tarpit_score >= 1.0:
                                    _tarpit_domains.add(_d)
                        except Exception:  # noqa: BLE001 — fail-soft; gather failed
                            _tarpit_domains = set()
                    else:
                        _tarpit_domains = set()

                    if _tarpit_domains:
                        _before_count = len(report.ioc_entities)
                        # Filter ioc_entities whose source domain is in tarpit set
                        _filtered_iocs = [
                            ioc for ioc in report.ioc_entities
                            if getattr(ioc, 'source_url', None) not in _tarpit_domains
                            and getattr(ioc, 'source_domain', None) not in _tarpit_domains
                        ]
                        _after_count = len(_filtered_iocs)
                        if _after_count < _before_count:
                            _dropped = _before_count - _after_count
                            report = msgspec.replace(
                                report, ioc_entities=_filtered_iocs,
                            )
                            logger.warning(
                                "[SYNTHESIS] [ADVERSARY]-002: Dropped %d/%d IOCs "
                                "from cognitive tarpit domains: %s",
                                _dropped, _before_count,
                                sorted(_tarpit_domains),
                            )
            except Exception as e:
                logger.debug(
                    "[SYNTHESIS] [ADVERSARY]-002: tarpit domain filter failed "
                    "(fail-soft): %s", e,
                )

        return report

    # =======================================================================
    # Public synthesis API — orchestrates 8 sub-pipeline steps
    # =======================================================================

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

        Pipeline:
          Phase 1: Guard checks (windup_allowed, uma_guard)
          Phase 2: Parallel discovery (model, stix, episode, rag)
          Phase 3: Rerank + GraphRAG
          Phase 3.5: Collapse + categorize (NEXUS-018-04, skip if ≤ threshold)
          Phase 4: Prompt construction
          Phase 5: DSPy + Bandit optimization
          Phase 6: Compression + race inference
          Phase 7: Parse + validate + reward + hypothesis
          Phase 8: (inline) All-engines-failed outcome
        """
        findings_count = len(findings)

        # ── Phase 1: Guards ──────────────────────────────────────────────
        if not await self._synth_phase1_guards(query, findings, force_synthesis):
            return None

        # ── BLITZ-10: Fast-path triage ───────────────────────────────────
        # Pre-filter findings before expensive model discovery + inference.
        # Eliminates 70-90% of noise that would waste Hermes-3B cycles.
        findings, triage_stats = await self._synth_triage_findings(query, findings)
        findings_count = len(findings)
        if findings_count == 0:
            logger.info("[SYNTHESIS] All findings filtered by triage — no signal")
            self._last_synthesis_outcome = SynthesisOutcome(
                status="skipped",
                primary_reason="all_findings_filtered_by_triage",
                lifecycle_gate_source=self._lifecycle_gate_source,
                lifecycle_gate_mode=self._lifecycle_gate_mode,
                stix_status="skipped",
                stix_reason="BLITZ-10 triage: no findings passed relevance filter",
                engine_used="none",
                findings_considered=0,
                report_produced=False,
                confidence=0.0,
                operator_note=f"triage filtered all {triage_stats.get('total_triaged', 0)} findings",
            )
            return None

        # ── Phase 2: Parallel discovery ──────────────────────────────────
        model_path, stix_context, episode_ctx, rag_context = (
            await self._synth_phase2_parallel_discovery(query, findings)
        )

        if model_path is None:
            logger.warning("[SYNTHESIS] No model available — skipping")
            self._last_synthesis_outcome = SynthesisOutcome(
                status="skipped",
                primary_reason="no_model",
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

        # ── Phase 3: Rerank + GraphRAG ──────────────────────────────────
        top, graph_context = await self._synth_phase3_rerank_and_graphrag(
            query, findings, max_findings
        )

        # ── Phase 3.5: Collapse + categorize (NEXUS-018-04) ──────────────
        # Only triggers when findings exceed threshold to avoid overhead on small batches.
        # Passes pre-collapsed Markdown to Phase 4 so LLM gets structured IOC tree.
        collapsed_markdown = ""
        if findings_count > _HLEDAC_COLLAPSE_THRESHOLD:
            collapsed_markdown = await self._synth_phase3_5_collapse_and_categorize(findings)

        # ── Phase 4: Build prompt ────────────────────────────────────────
        prompt = await self._synth_phase4_build_prompt(
            query, stix_context, episode_ctx, rag_context, graph_context,
            top, findings_count,
            collapsed_markdown=collapsed_markdown,
        )

        # ── Phase 5: DSPy + Bandit ──────────────────────────────────────
        prompt = await self._synth_phase5_prompt_optimization(prompt)

        # ── Phase 6: Inference ──────────────────────────────────────────
        raw_dict: dict | None = None
        used_engine = "none"
        token_logprobs: list[float] = []
        try:
            raw_dict, used_engine, token_logprobs = await self._synth_phase6_inference(prompt)
        except Exception as e:
            logger.error("Synthesis error: %s", e)
            self._last_synthesis_outcome = SynthesisOutcome(
                status="failed",
                primary_reason="generation_failed",
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

        # ── Phase 7: Parse + validate ───────────────────────────────────
        bandit = _get_prompt_bandit()
        arm_used = getattr(self, "_last_arm", "") or ""

        if raw_dict is not None:
            report = await self._synth_phase7_parse_and_validate(
                raw_dict, used_engine, findings,
                bandit, arm_used, query, findings_count,
                token_logprobs=token_logprobs,
            )
            if report is not None:
                self._last_synthesis_outcome = SynthesisOutcome(
                    status="success",
                    primary_reason="success",
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
                return report

        # ── Phase 8 (inline): All engines failed or parse failed ────────
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

    # ------------------------------------------------------------------
    # G2: Streaming synthesis for M1 8GB memory efficiency
    # ------------------------------------------------------------------

    async def synthesize_findings_streaming(
        self,
        findings_source: FindingsSource,
        query: str,
        max_findings: int = 50,
        max_buffered: int = 50,
        force_synthesis: bool = False,
    ) -> AsyncIterator[OSINTReport | None]:
        """
        G2: Streaming synthesis — yields reports as findings are processed.

        For M1 8GB memory efficiency: uses bounded buffering instead of
        loading all findings into memory at once.

        Args:
            findings_source: Either a list[dict] or AsyncIterator[dict].
                           AsyncIterator enables backpressure when caller produces
                           findings incrementally (e.g., from a live crawl).
            query: Original sprint query string.
            max_findings: Maximum findings to pass to each synthesis call.
            max_buffered: Maximum findings to hold in memory before yielding back.
            force_synthesis: Always run synthesis even if disabled.

        Yields:
            OSINTReport on each synthesis success, None on failure.
            Caller iterates to get incremental reports.

        Usage:
            async for report in runner.synthesize_findings_streaming(findings_iter, query):
                if report:
                    process(report)
        """
        # Collect findings with bounded memory
        findings_batch: list[dict] = []
        total_collected = 0

        async for finding in _collect_findings_bounded(findings_source, max_buffered):
            findings_batch.append(finding)
            total_collected += 1

            # When batch is full, run synthesis
            if len(findings_batch) >= max_buffered:
                report = await self.synthesize_findings(
                    query=query,
                    findings=findings_batch,
                    max_findings=max_findings,
                    force_synthesis=force_synthesis,
                )
                findings_batch.clear()  # Free memory immediately
                yield report

        # Process remaining findings
        if findings_batch:
            report = await self.synthesize_findings(
                query=query,
                findings=findings_batch,
                max_findings=max_findings,
                force_synthesis=force_synthesis,
            )
            yield report

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
        # Note: self._lifecycle.unload() above already calls mlx_cleanup_sync()
        # which handles gc.collect() → mx.eval([]) → clear_cache() canonically.

    # ------------------------------------------------------------------
    # Issue #12.1 + #12.2: Helper methods for parallel discovery + async-safe rerank
    # ------------------------------------------------------------------

    async def _rag_query_safe(self, query: str, findings: list[dict]) -> str:
        """RAG retrieval — fail-soft wrapper for parallel discovery TaskGroup."""
        try:
            from hledac.universal.knowledge.rag_engine import RAGEngine
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
        """GraphRAG IOC relationships — fail-soft wrapper for parallel discovery.

        SOVEREIGN-002: Uses build_graph_chatml_context() for token-budgeted
        ChatML context injection when multi_hop_search results are available.
        """
        try:
            from hledac.universal.legacy.persistent_layer import PersistentKnowledgeLayer
            from hledac.universal.knowledge.graph_rag import GraphRAGOrchestrator
            kl = PersistentKnowledgeLayer()
            _grag = GraphRAGOrchestrator(kl)

            # SOVEREIGN-002: Try multi_hop_search for rich graph context
            try:
                graph_result = await _grag.multi_hop_search(query=query, hops=2, max_nodes=15)
                if graph_result and graph_result.get("insights"):
                    from hledac.universal.brain.graph_prompt_builder import build_graph_chatml_context
                    return build_graph_chatml_context(graph_result, query, token_budget=1500)
            except Exception:
                pass

            # Fallback: find_connections for IOC relationships
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
    # L-05: Synthesis strategy — sequential_preferred (default) or race_first_wins
    # ------------------------------------------------------------------

    async def _race_inference(
        self,
        prompt: str,
    ) -> tuple[dict | None, str, list[float]]:
        """
        L-05: Dispatch between two synthesis strategies.

        sequential_preferred (default):
            Sequential cascade — xgrammar → streaming → structured.
            Each step runs with the global MLX inference lock = strict serialization.
            Benefit: ~3× lower latency than parallel TaskGroup, stable KV cache.
            First-success wins.

        race_first_wins:
            All 3 engines race in parallel via asyncio.wait + FIRST_COMPLETED.
            First successful result wins; remaining tasks are cancelled
            via task.cancel() on the losers.
            Benefit: ~1s total wall-clock when fastest engine succeeds first.
            Note: race_first_wins uses the same MLX lock internally, so GPU
            is still serialized at the Metal level — but I/O overlap across
            engines can still reduce effective latency vs sequential cascade.

        Returns (raw_dict, engine_name, token_logprobs). On all failure: (None, "none", []).
        """
        strategy = self._synthesis_strategy
        if strategy == "race_first_wins":
            return await self._race_inference_first_wins(prompt)
        else:
            # Default: sequential_preferred cascade
            return await self._race_inference_sequential(prompt)

    async def _race_inference_sequential(
        self,
        prompt: str,
    ) -> tuple[dict | None, str, list[float]]:
        """
        L-05: Sequential cascade — xgrammar → streaming → structured.
        Each step runs with the global MLX inference lock = strict serialization.
        First-success wins. Lowest latency overhead.
        """
        # Step 1: xgrammar (highest JSON guarantee)
        result, xgram_logprobs = await _cascade_xgrammar(self._lifecycle, prompt)
        if result is not None:
            return result, "xgrammar", xgram_logprobs

        # Step 2: streaming with early-exit
        result, stream_logprobs = await _cascade_streaming(self._lifecycle, prompt)
        if result is not None:
            return result, "streaming", stream_logprobs

        # Step 3: structured (Outlines fallback)
        result, struct_logprobs = await _cascade_structured(self._lifecycle, prompt)
        if result is not None:
            return result, "constrained", struct_logprobs

        # All engines failed
        logger.debug("[SYNTHESIS] All synthesis engines failed")
        return None, "none", []

    async def _race_inference_first_wins(
        self,
        prompt: str,
    ) -> tuple[dict | None, str, list[float]]:
        """
        L-05: Race-first-wins — all 3 engines run in parallel via asyncio.wait.
        First successful result wins; remaining tasks are cancelled via
        task.cancel() at FIRST_COMPLETED boundary.

        Note: All engines share the same Metal command stream (threading.Lock
        in mlx_lm), so GPU is serialized at the Metal level regardless.
        The benefit is I/O overlap when engines have different latencies.
        """
        winner: dict | None = None
        winner_name: str = "none"
        winner_logprobs: list[float] = []

        tasks = {
            asyncio.create_task(_race_try_xgrammar(self._lifecycle, prompt), name="xgrammar"): "xgrammar",
            asyncio.create_task(_race_try_streaming(self._lifecycle, prompt), name="streaming"): "streaming",
            asyncio.create_task(_race_try_structured(self._lifecycle, prompt), name="structured"): "structured",
        }

        pending = set(tasks.keys())
        try:
            while pending:
                # ISSUE-15: asyncio.wait(FIRST_COMPLETED) → first_completed helper
                # This is a race-first-wins pattern — first successful result wins
                try:
                    result, winner_task = await first_completed(*pending)
                except asyncio.TimeoutError:
                    # No timeout configured, shouldn't happen
                    break

                # Remove winner from pending
                pending.discard(winner_task)

                try:
                    result_dict, result_name, result_logprobs = result
                    if result_dict is not None and result_name != "none":
                        winner = result_dict
                        winner_name = result_name
                        winner_logprobs = result_logprobs
                        # Cancel remaining tasks — first-success
                        for remaining_task in pending:
                            remaining_task.cancel()
                            try:
                                await remaining_task
                            except asyncio.CancelledError:
                                pass
                        logger.debug("[SYNTHESIS] race_first_wins: %s won", winner_name)
                        return winner, winner_name, winner_logprobs
                except Exception as e:
                    logger.debug("[SYNTHESIS] race task exception: %s", e)
                    # Continue waiting for other tasks
        except asyncio.CancelledError:
            # Outer cancellation — cancel all and re-raise
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            raise

        logger.debug("[SYNTHESIS] race_first_wins: all engines failed")
        return None, "none", []

    # ------------------------------------------------------------------
    # Sprint 8TC B.3: Streaming synthesis s early-exit
    # ------------------------------------------------------------------

    async def _run_streaming_generation(  # noqa: C901
        self,
        prompt: str,
        json_schema: str | None = None,  # unused — regex early-exit path
    ) -> tuple[dict | None, bool, list[float]] | None:
        """
        Sprint 8TC B.3: mlx_lm stream_generate s early-exit při kompletním JSON.

        Fallback na regex JSON extract z akumulovaného textu.
        M1: vše sync v CPU_EXECUTOR — NIKDY přímo v event loop.

        Returns:
            (dict | None, outlines_used: bool, token_logprobs: list[float]) — stejný formát jako structured_generate
            APEX-1009: token_logprobs contains per-token log probabilities for uncertainty measurement
        """
        # LLM-01: ALWAYS sanitize prompt before LLM inference (fail-safe, always-on)
        try:
            _sanitize_fn = __import__('hledac.universal.brain.prompt_injection_validator', fromlist=['sanitize_prompt_injection_patterns']).sanitize_prompt_injection_patterns
            validation_result = _sanitize_fn(prompt)
            if validation_result.suspicious:
                _high_risk = any(p in validation_result.patterns for p in (
                    'ignore_previous_instructions', 'disregard_instructions', 'forget_instructions',
                    'system_prompt_injection', 'developer_message_injection', 'you_are_chatgpt',
                    'you_are_an_ai', 'as_an_ai', 'jailbreak', 'dan',
                    'structural_repeated_delimiters', 'structural_html_comment',
                ))
                if _high_risk:
                    logger.warning(f'[LLM-01-BLOCK] High-risk prompt injection in streaming: {validation_result.patterns}')
                    return None
                logger.warning('[SYNTHESIS] streaming prompt_injection: suspicious=%s, patterns=%s', validation_result.suspicious, validation_result.patterns)
            prompt = validation_result.safe_text
        except Exception:
            # LLM-01 fail-safe: reject on any internal error
            logger.error('[LLM-01] streaming prompt injection validation failed internally')
            return None

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

        # M-03: Tokenize once — store actual tokens to avoid re-encoding in mlx_lm
        try:
            _stream_tokens_list: list[int] = tokenizer.encode(formatted)
            _stream_input_tokens: int = len(_stream_tokens_list)
        except Exception:
            _stream_tokens_list = []
            _stream_input_tokens = 0

        def _stream_sync() -> tuple[dict | None, bool]:
            import mlx_lm

            # L-01: Globální MLX Metal lock — serializuje všechny mlx_lm.stream_generate() volání
            from hledac.universal.core.mlx_inference_lock import _get_mlx_inference_lock

            _mlx_lock = _get_mlx_inference_lock()
            accumulated = ""
            # ISSUE-009: Track speculative URLs/IPs detected during streaming
            # Use sets for O(1) dedup lookup, convert to list for compatibility
            _spec_urls_set: set[str] = set()
            _spec_ips_set: set[str] = set()
            spec_urls: list[str] = []
            spec_ips: list[str] = []
            # APEX-1009: Collect token logprobs for uncertainty measurement
            token_logprobs: list[float] = []
            if hasattr(mlx_lm, "stream_generate"):
                try:
                    with _mlx_lock:
                        for chunk in mlx_lm.stream_generate(
                            model,
                            tokenizer,
                            prompt=_stream_tokens_list,  # M-03: pass tokens directly
                            max_tokens=512,
                            kv_bits=self._get_adaptive_kv_bits(),
                            **self._get_kv_cache_kwargs(_stream_input_tokens, 512),
                            verbose=False,
                        ):
                            tok = chunk.text if hasattr(chunk, "text") else str(chunk)
                            accumulated += tok
                            # APEX-1009: Extract token logprob for uncertainty measurement
                            # chunk.logprobs is a list of (token_id, logprob) tuples
                            if hasattr(chunk, "logprobs") and chunk.logprobs:
                                # Take the logprob of the generated token (last entry)
                                try:
                                    logprob_val = chunk.logprobs[-1][1] if isinstance(chunk.logprobs[-1], tuple) else chunk.logprobs[-1]
                                    token_logprobs.append(float(logprob_val))
                                except (IndexError, TypeError, ValueError):
                                    pass  # Fail-soft: skip if logprob extraction fails
                            # ISSUE-009: Speculative URL/IP detection — scan sliding window
                            # O(1) memory per token, avoids O(n²) full-string concat for detection
                            window = accumulated[-_SPEC_WINDOW:]
                            for url_match in _URL_SPEC_RE.finditer(window):
                                if url_match.group() not in _spec_urls_set:
                                    _spec_urls_set.add(url_match.group())
                                    spec_urls.append(url_match.group())
                            for ip_match in _IP_SPEC_RE.finditer(window):
                                if ip_match.group() not in _spec_ips_set:
                                    _spec_ips_set.add(ip_match.group())
                                    spec_ips.append(ip_match.group())
                            # ISSUE-010: orjson incremental parse — handles nested JSON that regex cannot
                            parsed, is_complete = _try_parse_json_incremental(accumulated)
                            if is_complete and parsed is not None:
                                try:
                                    # ISSUE-009: Attach detected URLs/IPs to self for caller access
                                    self._speculative_urls = spec_urls
                                    self._speculative_ips = spec_ips
                                    # APEX-1009: Return token_logprobs for uncertainty measurement
                                    return _msgspec_decode(_msgspec_encode(parsed)), True, token_logprobs
                                except Exception:
                                    pass  # Decode failed, keep accumulating
                except Exception as e:
                    logger.warning("[SYNTHESIS] stream_generate failed: %s — fallback", e)
                    # ISSUE-009 fix: Keep accumulated text for final parse attempt
                    # accumulated = "" would lose partial JSON that may still be parseable

            # Final attempt on accumulated text
            if accumulated:
                parsed, is_complete = _try_parse_json_incremental(accumulated)
                if is_complete and parsed is not None:
                    try:
                        # ISSUE-009: Attach detected URLs/IPs even on final fallback
                        self._speculative_urls = spec_urls
                        self._speculative_ips = spec_ips
                        # APEX-1009: Return token_logprobs for uncertainty measurement
                        return _msgspec_decode(_msgspec_encode(parsed)), True, token_logprobs
                    except Exception:  # noqa: BLE001
                        pass

            # Issue #20-C: mx.eval() + gc.collect() cleanup — Sprint 3 dedup via _mlx_cleanup()
            _mlx_cleanup()
            return (None, False, [])

        return await asyncio.to_thread(_stream_sync)

    # ------------------------------------------------------------------
    # Sprint 8UC B.1: xgrammar guaranteed-JSON synthesis
    # ------------------------------------------------------------------

    async def _run_xgrammar_generation(  # noqa: C901
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

        # LLM-01: ALWAYS sanitize prompt before LLM inference (fail-safe, always-on)
        try:
            _sanitize_fn = __import__('hledac.universal.brain.prompt_injection_validator', fromlist=['sanitize_prompt_injection_patterns']).sanitize_prompt_injection_patterns
            validation_result = _sanitize_fn(prompt)
            if validation_result.suspicious:
                _high_risk = any(p in validation_result.patterns for p in (
                    'ignore_previous_instructions', 'disregard_instructions', 'forget_instructions',
                    'system_prompt_injection', 'developer_message_injection', 'you_are_chatgpt',
                    'you_are_an_ai', 'as_an_ai', 'jailbreak', 'dan',
                    'structural_repeated_delimiters', 'structural_html_comment',
                ))
                if _high_risk:
                    logger.warning(f'[LLM-01-BLOCK] High-risk prompt injection in xgrammar: {validation_result.patterns}')
                    return None, False
                logger.warning('[SYNTHESIS] xgrammar prompt_injection: suspicious=%s, patterns=%s', validation_result.suspicious, validation_result.patterns)
            prompt = validation_result.safe_text
        except Exception:
            # LLM-01 fail-safe: reject on any internal error
            logger.error('[LLM-01] xgrammar prompt injection validation failed internally')
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

        # M-03: Tokenize once — store actual tokens to avoid re-encoding in mlx_lm
        try:
            _input_tokens_list: list[int] = tokenizer.encode(formatted)
            _input_tokens: int = len(_input_tokens_list)
        except Exception:
            _input_tokens_list = []
            _input_tokens = 0

        def _xgrammar_sync() -> tuple[dict | None, bool]:  # noqa: C901
            try:
                from contextlib import nullcontext

                import mlx_lm
                import xgrammar as xgr

                from hledac.universal.utils.mlx_memory import get_metal_stream_context

                # L-01: Globální MLX Metal lock — serializuje všechny mlx_lm.generate() volání
                from hledac.universal.core.mlx_inference_lock import _get_mlx_inference_lock

                _mlx_lock = _get_mlx_inference_lock()

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
                        with _mlx_lock:
                            try:
                                output = mlx_lm.generate(
                                    model, tokenizer,
                                    prompt=_input_tokens_list,  # M-03: pass tokens directly
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
                                    prompt=_input_tokens_list,  # M-03: pass tokens directly
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
                            with _mlx_lock:
                                try:
                                    try:
                                        output = mlx_lm.generate(
                                            model, tokenizer,
                                            prompt=_input_tokens_list,  # M-03: pass tokens directly (fixes fallback path)
                                            max_tokens=512,
                                            logits_processors=[processor],
                                            kv_bits=self._get_adaptive_kv_bits(),
                                            **self._get_kv_cache_kwargs(_input_tokens, 512),
                                            verbose=False,
                                        )
                                    except TypeError:
                                        output = mlx_lm.generate(
                                            model, tokenizer,
                                            prompt=_input_tokens_list,  # M-03: pass tokens directly (fixes fallback path)
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
                    # Sprint 8UD B.2: Clear MLX Metal cache — Sprint 3 dedup via _mlx_cleanup()
                    _mlx_cleanup()

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

    async def _ensure_model(self) -> Path | None:  # noqa: C901
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

        # Phase 1: Check all model sizes in parallel
        # F314-4: migrated asyncio.gather -> parallel_ok (fail-soft, preserves order)
        _size_result = await parallel(
            [_check_model_size(mid, mgb) for mid, mgb in model_candidates],
            policy="log",
            ctx="synthesis:check_model_sizes",
        )
        size_results = _size_result.ok

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
            await parallel(
                [_download_model(mid) for mid in eligible],
                policy="log",
                ctx="synthesis:download_models",
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

    def _parse_raw_to_osintreport(
        self,
        raw: dict,
        token_logprobs: list[float] | None = None,
    ) -> OSINTReport | None:
        """
        Sprint 8TA B.1: Safe parsing of raw dict into OSINTReport.

        Uses raw.get() for every field with defaults for missing values.
        Maps json_schema fields (title/summary/findings) to OSINTReport fields
        (threat_summary/ioc_entities/sources_count).

        APEX-1008: If token_logprobs provided, computes uncertainty flags and
        propagates per-entity confidence/uncertainty_flag to IOCEntity objects.
        """
        try:
            title = raw.get("title", "OSINT Synthesis")
            summary = raw.get("summary", "")
            threat_actors = raw.get("threat_actors") or []
            findings = raw.get("findings") or []
            confidence = raw.get("confidence", 0.0)
            timestamp = raw.get("timestamp", time.time())

            # APEX-1008: Compute uncertainty flags if token_logprobs available
            uncertainty_flags = None
            if token_logprobs:
                uncertainty_flags = uncertainty_gate(confidence, token_logprobs)

            # Map findings list to IOCEntity list
            ioc_entities: list[IOCEntity] = []
            for f in findings[:20]:  # max 20
                if isinstance(f, str):
                    # APEX-1008: Apply uncertainty to entity if available
                    entity_confidence = 1.0
                    entity_uncertainty_flag = "normal"

                    if uncertainty_flags and token_logprobs:
                        # Use measured entropy to set entity confidence
                        # Higher entropy = lower confidence
                        if uncertainty_flags.measured_entropy > 1.5:
                            entity_confidence = 0.5
                            entity_uncertainty_flag = "high_entropy"
                        elif uncertainty_flags.measured_entropy > 0.8:
                            entity_confidence = 0.7
                            entity_uncertainty_flag = "elevated"
                        else:
                            entity_confidence = 0.95
                            entity_uncertainty_flag = "normal"

                    ioc_entities.append(IOCEntity(
                        value=f[:100],
                        ioc_type=_infer_ioc_type(f),
                        severity="medium",
                        context=f[:200],
                        confidence=entity_confidence,
                        uncertainty_flag=entity_uncertainty_flag,
                    ))

            return OSINTReport(
                query=title,
                ioc_entities=ioc_entities,
                threat_summary=summary[:500] if summary else "",
                threat_actors=threat_actors[:10],
                confidence=float(confidence) if confidence else 0.0,
                sources_count=len(findings),
                timestamp=float(timestamp) if timestamp else time.time(),
                uncertainty_flags=uncertainty_flags,
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
                findings = orjson.loads(findings_raw)
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
        if self._stix_graph is not None:
            values, backend_name, error = await _extract_stix_nodes(
                self._stix_graph, f"stix_graph '{type(self._stix_graph).__name__}'"
            )
            if error:
                self._stix_status = "unavailable" if "lacks" in error else "error"
                self._stix_reason = f"stix_graph {error}"
                self._stix_backend = backend_name
                return ""
            if values:
                self._stix_status = "available"
                self._stix_reason = f"stix_graph exported {len(values)} values"
                self._stix_backend = backend_name
                return f"\nKnown IOCs from graph ({len(values)} entities): {', '.join(values)}"
            self._stix_status = "available"
            self._stix_reason = "stix_graph had no extractable IOC values"
            self._stix_backend = backend_name
            return ""

        # Sprint 8VQ: Priority 2 — analytics/donor graph (DuckPGQGraph — no STIX)
        if self._ioc_graph is None:
            self._stix_status = "unavailable"
            self._stix_reason = "no graph injected — both _stix_graph and _ioc_graph are None"
            self._stix_backend = ""
            return ""

        values, backend_name, error = await _extract_stix_nodes(
            self._ioc_graph, f"backend '{type(self._ioc_graph).__name__}'"
        )
        if error:
            self._stix_status = "unavailable" if "lacks" in error else "error"
            self._stix_reason = f"STIX {error}"
            self._stix_backend = backend_name
            return ""
        if values:
            self._stix_status = "available"
            self._stix_reason = f"exported {len(values)} values"
            self._stix_backend = backend_name
            return f"\nKnown IOCs from graph ({len(values)} entities): {', '.join(values)}"
        self._stix_status = "available"
        self._stix_reason = "graph had no extractable IOC values"
        self._stix_backend = backend_name
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
