"""
DSPy production service — wired into sprint pipeline.

HLEDAC_ENABLE_DSPY=1 gates all calls.
Lazy-loads compiled programs from ~/.hledac/dspy_cache.json on first call.
Fails soft: returns None/empty on any error.

3 integration points (sprint phases):
  A) query_expansion  — before duckduckgo_adapter._build_query_variants
  B) finding_relevance — after raw findings arrive, filter score < 4
  C) pivot_suggestion  — in hypothesis_engine._model_assisted_query_suggestion
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path

try:
    import orjson
except ImportError:
    orjson = None

try:
    import aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = None

logger = logging.getLogger("dspy_service")

ENABLED = os.getenv("HLEDAC_ENABLE_DSPY", "0") == "1"
CACHE_PATH = Path.home() / ".hledac" / "dspy_cache.json"
TIMEOUT_SECONDS = 30
MAX_OUTPUT_TOKENS = 50

# Lazy-loaded state
_programs: dict = {}
_programs_loaded: bool = False


def _load_programs() -> dict:
    """Lazy-load compiled DSPy programs from cache. Call once per process."""
    global _programs, _programs_loaded
    if _programs_loaded:
        return _programs

    _programs_loaded = True
    if not CACHE_PATH.exists():
        logger.warning("dspy_service: cache not found at %s", CACHE_PATH)
        return {}

    try:
        if orjson is not None:
            with open(CACHE_PATH, "rb") as f:
                data = orjson.loads(f.read())
        else:
            import json as _json
            with open(CACHE_PATH) as f:
                data = _json.load(f)
        prompts = data.get("prompts", {})
        _programs = {k: v for k, v in prompts.items() if v and isinstance(v, str)}
        logger.info("dspy_service: loaded %d compiled programs from cache", len(_programs))
    except Exception as e:
        logger.warning("dspy_service: failed to load cache: %s", e)
        _programs = {}

    return _programs


def _get_dspy_lm():
    """Build DSPy LM instance using mlx_lm.server (same config as MIPROv2 setup)."""
    model_id = os.getenv(
        "HLEDAC_LLM_MODEL",
        "/Users/" + os.getenv("USER", "root") + "/.hledac/models/DeepHermes-3-Llama-3-3B-Preview-4bit",
    )
    try:
        import dspy
        lm = dspy.LM(
            model=model_id,
            base_url="http://localhost:8080/v1",
            api_key="none",
            custom_llm_provider="openai",
            max_tokens=MAX_OUTPUT_TOKENS,
        )
        return lm
    except Exception as e:
        logger.warning("dspy_service: failed to create DSPy LM: %s", e)
        return None


# ---------------------------------------------------------------------------
# Phase A: Query Expansion
# ---------------------------------------------------------------------------
# Before: seed query string
# After: list of expanded query strings (max 5)
# Cache key: "analysis:medium" — query expansion task


async def expand_query(query: str) -> list | None:
    """
    Phase A: DSPy-powered query expansion.

    Takes seed query → returns 3-5 semantically diverse query variants.
    Used before duckduckgo_adapter._build_query_variants (which handles
    domain-specific variants; DSPy handles semantic expansion).

    Returns None if DSPy unavailable or fails — caller falls back to default.
    """
    if not ENABLED:
        return None

    if not query or len(query.strip()) < 2:
        return None

    t0 = time.monotonic()
    programs = _load_programs()
    task_key = "analysis:medium"
    prompt_template = programs.get(task_key)
    if not prompt_template:
        logger.warning("dspy_service: no compiled prompt for %s", task_key)
        return None

    lm = _get_dspy_lm()
    if lm is None:
        return None

    try:
        import dspy

        class QueryExpandSignature(dspy.Signature):
            """Expand OSINT query into diverse search variants."""
            query: str = dspy.InputField()
            answer: str = dspy.OutputField()

        program = dspy.Predict(QueryExpandSignature)
        # Inject the compiled prompt as instructions
        program._predictor.instructions = prompt_template

        async def _run():
            with dspy.ctx(lm=lm):
                pred = program(query=query.strip())
                return str(pred.answer) if hasattr(pred, "answer") else None

        answer = await asyncio.wait_for(_run(), timeout=TIMEOUT_SECONDS)
        if answer is None:
            return None

        # Parse: each line is a variant
        variants = [
            line.strip()
            for line in answer.split("\n")
            if line.strip() and len(line.strip()) < 120
        ]
        # Remove duplicates while preserving order
        seen = set()
        unique = []
        for v in variants:
            if v not in seen:
                seen.add(v)
                unique.append(v)

        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info(
            "dspy_service: expand_query dspy_call=query_expansion latency_ms=%.0f "
            "tokens_in=%d tokens_out=%d variants=%d",
            elapsed_ms,
            len(query),
            len(answer),
            len(unique),
        )
        return unique[:5] if unique else None

    except TimeoutError:
        logger.warning("dspy_service: expand_query timed out after %ds", TIMEOUT_SECONDS)
        return None
    except Exception as e:
        logger.warning("dspy_service: expand_query failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Phase B: Finding Relevance Scoring
# ---------------------------------------------------------------------------
# Before: raw finding dicts (each with 'content' or 'title' field)
# After: list of (finding, score) tuples, filtered to score >= 4
# Cache key: "extraction:medium" — relevance scoring task


async def score_findings(findings: list, min_score: float = 4.0) -> list | None:
    """
    Phase B: DSPy-powered finding relevance scoring.

    Takes raw findings from discovery → returns scored+filtered list.
    Filters out findings with DSPy relevance score < min_score.

    Returns None if DSPy unavailable — caller accepts all findings.
    Each finding dict must have at least 'content' or 'title' field.
    """
    if not ENABLED:
        return None

    if not findings:
        return None

    t0 = time.monotonic()
    programs = _load_programs()
    task_key = "extraction:medium"
    prompt_template = programs.get(task_key)
    if not prompt_template:
        logger.warning("dspy_service: no compiled prompt for %s", task_key)
        return None

    lm = _get_dspy_lm()
    if lm is None:
        return None

    try:
        import dspy

        # Build compact finding strings (max 20 findings, 60 chars each)
        finding_lines = []
        for i, f in enumerate(findings[:20]):
            text = f.get("content") or f.get("title") or f.get("url", "")[:80]
            finding_lines.append(f"{i}:{text[:60]}")

        # Serialize compactly
        if orjson is not None:
            findings_json = orjson.dumps(
                [{"i": i, "t": (f.get("content") or f.get("title") or "")[:60]}
                 for i, f in enumerate(findings[:20])]
            ).decode()
        else:
            import json
            findings_json = json.dumps(
                [{"i": i, "t": (f.get("content") or f.get("title") or "")[:60]}
                 for i, f in enumerate(findings[:20])]
            )

        class RelevanceScoreSignature(dspy.Signature):
            """Score OSINT findings for relevance 0-10."""
            query: str = dspy.InputField()
            answer: str = dspy.OutputField()

        program = dspy.Predict(RelevanceScoreSignature)
        program._predictor.instructions = prompt_template

        async def _run():
            with dspy.ctx(lm=lm):
                pred = program(query=findings_json[:500])
                return str(pred.answer) if hasattr(pred, "answer") else None

        answer = await asyncio.wait_for(_run(), timeout=TIMEOUT_SECONDS)
        if answer is None:
            return None

        # Parse: look for "INDEX:SCORE" patterns
        scored = []
        for line in answer.split("\n"):
            line = line.strip()
            if ":" in line:
                parts = line.rsplit(":", 1)
                try:
                    idx = int(parts[0].strip("[]-: "))
                    score = float(parts[1].strip())
                    if 0 <= score <= 10 and idx < len(findings):
                        scored.append((findings[idx], score))
                except (ValueError, IndexError):
                    pass

        scored.sort(key=lambda x: x[1], reverse=True)
        filtered = [(f, s) for f, s in scored if s >= min_score]

        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info(
            "dspy_service: score_findings dspy_call=finding_relevance latency_ms=%.0f "
            "tokens_in=%d tokens_out=%d scored=%d filtered=%d",
            elapsed_ms,
            len(findings_json),
            len(answer),
            len(scored),
            len(filtered),
        )
        return filtered if filtered else None

    except TimeoutError:
        logger.warning("dspy_service: score_findings timed out after %ds", TIMEOUT_SECONDS)
        return None
    except Exception as e:
        logger.warning("dspy_service: score_findings failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Phase C: Hypothesis Pivot Suggestion
# ---------------------------------------------------------------------------
# Before: list of finding strings + context dict
# After: list of pivot seed dicts {ioc_value, ioc_type, confidence}
# Cache key: "summarization:medium" — pivot seed generation


async def suggest_pivots(findings: list, context: dict | None = None) -> list | None:
    """
    Phase C: DSPy-powered hypothesis pivot seed suggestion.

    Takes current sprint findings → returns pivot seed candidates.
    Used in hypothesis_engine._model_assisted_query_suggestion which is
    currently aspirational (returns []).

    Returns None if DSPy unavailable — caller uses existing fallback.
    """
    if not ENABLED:
        return None

    if not findings:
        return None

    t0 = time.monotonic()
    programs = _load_programs()
    task_key = "summarization:medium"
    prompt_template = programs.get(task_key)
    if not prompt_template:
        logger.warning("dspy_service: no compiled prompt for %s", task_key)
        return None

    lm = _get_dspy_lm()
    if lm is None:
        return None

    try:
        import dspy

        # Compact representation of findings
        finding_texts = [
            (f.get("content") or f.get("title") or str(f))[:80]
            for f in findings[:10]
        ]
        findings_str = "\n".join(f"  {i}. {t}" for i, t in enumerate(finding_texts))

        class PivotSuggestSignature(dspy.Signature):
            """Suggest OSINT pivot seeds from findings."""
            query: str = dspy.InputField()
            answer: str = dspy.OutputField()

        program = dspy.Predict(PivotSuggestSignature)
        program._predictor.instructions = prompt_template

        async def _run():
            with dspy.ctx(lm=lm):
                pred = program(query=findings_str[:400])
                return str(pred.answer) if hasattr(pred, "answer") else None

        answer = await asyncio.wait_for(_run(), timeout=TIMEOUT_SECONDS)
        if answer is None:
            return None

        # Parse: IOC_VALUE|IOC_TYPE|CONFIDENCE
        pivots = []
        for line in answer.split("\n"):
            line = line.strip()
            if "|" in line:
                parts = line.split("|")
                if len(parts) == 3:
                    try:
                        ioc_value = parts[0].strip()
                        ioc_type = parts[1].strip().lower()
                        confidence = float(parts[2].strip())
                        if ioc_value and ioc_type in ("domain", "ip", "url", "hash", "email"):
                            pivots.append({
                                "ioc_value": ioc_value,
                                "ioc_type": ioc_type,
                                "confidence": min(1.0, max(0.0, confidence)),
                            })
                    except ValueError:
                        pass

        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info(
            "dspy_service: suggest_pivots dspy_call=pivot_suggestion latency_ms=%.0f "
            "tokens_in=%d tokens_out=%d pivots=%d",
            elapsed_ms,
            len(findings_str),
            len(answer),
            len(pivots),
        )
        return pivots[:5] if pivots else None

    except TimeoutError:
        logger.warning("dspy_service: suggest_pivots timed out after %ds", TIMEOUT_SECONDS)
        return None
    except Exception as e:
        logger.warning("dspy_service: suggest_pivots failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Health check (for preflight)
# ---------------------------------------------------------------------------
async def check_health() -> dict:
    """
    Returns dict with DSPy service health status.
    Used by preflight_check.py — WARN (not FAIL) if unavailable.
    """
    health = {
        "dspy_enabled": ENABLED,
        "cache_exists": CACHE_PATH.exists(),
        "programs_loaded": 0,
        "lm_available": False,
        "status": "ok",
    }

    if not ENABLED:
        health["status"] = "disabled"
        return health

    programs = _load_programs()
    health["programs_loaded"] = len(programs)

    if not programs:
        health["status"] = "warn"
        return health

    # Check if mlx_lm.server is reachable
    if AIOHTTP_AVAILABLE:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "http://localhost:8080/health",
                    timeout=aiohttp.ClientTimeout(total=3)
                ) as resp:
                    health["lm_available"] = resp.status == 200
        except Exception:
            pass

    if not health["lm_available"]:
        health["status"] = "warn"

    return health
