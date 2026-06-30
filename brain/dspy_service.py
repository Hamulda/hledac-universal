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


import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Any

from hledac.universal.brain.mlx_worker_thread import MLXWorkerThread

try:
    import orjson
except ImportError:
    orjson = None

# DSPy — lazy-loaded to avoid hard dependency at module import time.
# The actual check (HLEDAC_ENABLE_DSPY) is at instantiation in get_hermes_dspy_lm().
try:
    import dspy
except ImportError:
    dspy = None  # type: ignore[assignment]

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


# ---------------------------------------------------------------------------
# Part A: Hermes3 ↔ DSPy Bridge (Sprint HERMES3_WIRING)
# ---------------------------------------------------------------------------
# Hermes3DSPyLM wraps Hermes3Engine as a proper dspy.BaseLM subclass.
# Gate: HLEDAC_ENABLE_LLM=1 (default OFF to save RAM when not needed)
# M1 constraint: lazy load, unload after synthesis, mx.metal.clear_cache() on finish
#
# DSPy 3.2.1 BaseLM contract:
#   - Extend dspy.BaseLM and call super().__init__(model=..., model_type='chat')
#   - Implement aforward(prompt, messages, **kwargs) -> OpenAI chat completion dict
#   - ChatAdapter.format() produces list[{"role": "system"|"user"|"assistant", "content": str}]
#   - We concatenate messages into prompt+system_msg and call Hermes3Engine.generate()
#   - Return {"choices": [{"message": {"content": <str>}}]} — OpenAI chat format

Hermes3LM_ENABLED = os.getenv("HLEDAC_ENABLE_LLM", "1") == "1"
_HERMES_LM_INSTANCE: Hermes3DSPyLM | None = None


class _HermesChatResponse:
    """
    Mock OpenAI chat completion response for DSPy _process_completion.

    _process_completion accesses response.choices[0].message.content.
    _process_lm_response logs response.model.
    The usage attribute is optional (None on cache hit).
    """

    def __init__(self, content: str, model: str = "hermes-3-llama"):
        self.choices = [_HermesChoice(content)]
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self.model = model  # type: ignore[attr-defined]  # accessed by BaseLM logging


class _HermesChoice:
    """Single choice in OpenAI chat completion response."""

    def __init__(self, content: str):
        self.message = _HermesMessage(content)
        self.finish_reason = "stop"
        self.index = 0


class _HermesMessage:
    """OpenAI chat message object."""

    def __init__(self, content: str):
        self.content = content
        self.role = "assistant"
        self.audio = None
        self.function_call = None
        self.tool_calls = None


class Hermes3DSPyLM(dspy.BaseLM):
    """
    DSPy BaseLM wrapper around Hermes3Engine.

    Properly extends dspy.BaseLM so DSPy 3.2.1 Predict._forward_preprocess
    passes the isinstance(lm, BaseLM) check.

    The call chain is:
      Predict.__call__ → _forward_preprocess (isinstance check) →
      Adapter.__call__ → lm(messages=[...], **lm_kwargs) →
      BaseLM.__call__(messages=..., **kwargs) →
      _process_lm_response(forward(...), ...) →
      _process_completion(response.choices[0].message.content)

    M1 8GB constraints:
    - Lazy load: Hermes3Engine only initialized on first inference
    - Unload after synthesis: mx.metal.clear_cache() called in unload()
    - ANE/MLX mutex: acquire before loading, release after
    - MLXWorkerThread.submit() for thread-safe async execution
    """

    _loaded: bool = False
    _worker: MLXWorkerThread | None = None

    def __init__(self, model_path: str | None = None):
        super().__init__(model=model_path or "hermes-3-llama", model_type="chat")
        self._model_path = model_path
        self._engine = None
        self._loaded = False

    @classmethod
    def _get_worker(cls) -> MLXWorkerThread:
        """Get or create the shared MLXWorkerThread (singleton per process)."""
        if cls._worker is None:
            cls._worker = MLXWorkerThread(name="dspy-hermes-worker")
            cls._worker.start()
        return cls._worker

    def _ensure_engine(self) -> None:
        """Lazy-load Hermes3Engine with ANE mutex protection.

        Initialization runs IN the MLXWorkerThread via submit() — never
        on the main thread's event loop. This avoids the nested-loop M1 crash
        (asyncio.run_coroutine_threadsafe().result() already uses the worker
        loop; we must not create a second loop via new_event_loop()).
        """
        if self._loaded:
            return
        try:
            from hledac.universal.brain.ane_embedder import get_ane_mlx_mutex
            mutex = get_ane_mlx_mutex()
            mutex.acquire_mlx(model_size_mb=2000.0)

            from hledac.universal.brain.deephermes3_engine import DeepHermes3Engine
            self._engine = DeepHermes3Engine(
                model_path=self._model_path,
                sanitize_for_llm=None,
            )

            # P0-3 fix: run initialization IN the MLXWorkerThread.
            # _get_worker() creates + starts the daemon thread on first call.
            # submit() schedules the coroutine on the worker's loop and awaits
            # it from the main thread — no new_event_loop(), no nested loop.
            worker = self._get_worker()
            assert worker._loop is not None, "mlx_worker loop not ready"
            asyncio.run_coroutine_threadsafe(
                self._engine.initialize(),
                worker._loop,
            ).result(timeout=120.0)


            self._loaded = True
        except Exception as e:
            logger.warning("Hermes3DSPyLM engine load failed: %s", e)
            self._loaded = False
            self._engine = None
            raise

    async def _async_generate(self, prompt: str, **kwargs) -> str:
        """Async generation via Hermes3Engine.generate()."""
        self._ensure_engine()
        assert self._engine is not None, "Hermes3Engine not loaded"
        return await self._engine.generate(prompt, **kwargs)

    def forward(
        self,
        prompt: str | None = None,
        messages: list[dict[str, str]] | None = None,
        **kwargs: Any,
    ) -> dict:
        """
        Synchronous forward pass — wraps asyncio call for DSPy compatibility.

        Called by BaseLM.__call__ which expects a dict response matching the
        OpenAI chat completion format (response.choices[0].message.content).
        """
        self._ensure_engine()

        # Build prompt + system_msg from messages
        system_msg: str | None = None
        prompt_parts: list[str] = []

        if messages:
            for msg in messages:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role == "system":
                    system_msg = content
                elif role == "user":
                    prompt_parts.append(f"User: {content}")
                elif role == "assistant":
                    prompt_parts.append(f"Assistant: {content}")
            if prompt:
                prompt_parts.append(f"User: {prompt}")
            full_prompt = "\n\n".join(prompt_parts)
        else:
            full_prompt = prompt or ""

        # Run via worker thread — synchronous result
        worker = self._get_worker()
        assert worker._loop is not None, "mlx_worker loop not ready"
        text: str = asyncio.run_coroutine_threadsafe(
            self._async_generate(full_prompt, system_msg=system_msg, **kwargs),
            worker._loop,
        ).result(timeout=60.0)

        return _HermesChatResponse(text if text else "", model=self.model)  # type: ignore[return-value]

    async def aforward(
        self,
        prompt: str | None = None,
        messages: list[dict[str, str]] | None = None,
        **kwargs: Any,
    ) -> dict:
        """
        Async forward pass — called by BaseLM.acall.

        ChatAdapter formats messages as:
          [{"role": "system"|"user"|"assistant", "content": str}, ...]

        We reconstruct a single prompt by concatenating role-prefixed content.
        """
        self._ensure_engine()

        system_msg: str | None = None
        prompt_parts: list[str] = []

        if messages:
            for msg in messages:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role == "system":
                    system_msg = content
                elif role == "user":
                    prompt_parts.append(f"User: {content}")
                elif role == "assistant":
                    prompt_parts.append(f"Assistant: {content}")
            if prompt:
                prompt_parts.append(f"User: {prompt}")
            full_prompt = "\n\n".join(prompt_parts)
        else:
            full_prompt = prompt or ""

        worker = self._get_worker()
        assert worker._loop is not None, "mlx_worker loop not ready"
        text: str = await worker.submit(
            self._async_generate(full_prompt, system_msg=system_msg, **kwargs),
            timeout=60.0,
        )

        return _HermesChatResponse(text if text else "", model=self.model)  # type: ignore[return-value]

    def unload(self) -> None:
        """Unload model and clear Metal cache (M1 RAM recovery).
        unload() runs IN the MLXWorkerThread via submit() — same fix as
        _ensure_engine(). The worker loop is still running at this point;
        creating a second loop with new_event_loop() causes nested-loop
        crash on M1.
        """
        if not self._loaded or self._engine is None:
            return
        try:
            # P0-3 fix: run unload() IN the MLXWorkerThread.
            worker = self._get_worker()
            assert worker._loop is not None, "mlx_worker loop not ready"
            asyncio.run_coroutine_threadsafe(
                self._engine.unload(),
                worker._loop,
            ).result(timeout=60.0)
        except Exception:  # noqa: BLE001
            pass
        finally:
            try:
                import mlx.core as _mx
                _mx.eval([])
                if _mx.metal.is_available():
                    import gc
                    gc.collect()  # F266: Python GC BEFORE Metal release
                    # Modern-first: mx.clear_cache() — mlx >= 0.20, no fallback
                    if hasattr(_mx, "clear_cache"):
                        _mx.clear_cache()
                    gc.collect()  # F266: second GC pass
            except Exception:  # noqa: BLE001
                pass
            try:
                from hledac.universal.brain.ane_embedder import get_ane_mlx_mutex
                get_ane_mlx_mutex().release("mlx")
            except Exception:  # noqa: BLE001
                pass
            self._loaded = False


def get_hermes_dspy_lm() -> Hermes3DSPyLM | None:
    """
    Get singleton Hermes3DSPyLM instance.

    Returns None if HLEDAC_ENABLE_LLM != "1".
    """
    global _HERMES_LM_INSTANCE
    if not Hermes3LM_ENABLED:
        return None
    if _HERMES_LM_INSTANCE is None:
        _HERMES_LM_INSTANCE = Hermes3DSPyLM()
    return _HERMES_LM_INSTANCE


def configure_dspy_with_hermes() -> bool:
    """
    Configure DSPy to use Hermes3Engine as the language model.

    Call once at startup if HLEDAC_ENABLE_DSPY=1.
    Returns True if configured, False if skipped/failed.
    """
    if not Hermes3LM_ENABLED:
        logger.info("dspy_service: Hermes3 LM disabled (HLEDAC_ENABLE_LLM != '1')")
        return False

    try:
        import dspy
        hermes_lm = get_hermes_dspy_lm()
        if hermes_lm is None:
            return False
        dspy.settings.configure(lm=hermes_lm)
        logger.info("dspy_service: DSPy configured with Hermes3Engine")
        return True
    except Exception as e:
        logger.warning("dspy_service: DSPy configure with Hermes failed: %s", e)
        return False


def _get_dspy_lm():
    """
    Build DSPy LM instance using Hermes3DSPyLM (direct MLX, no HTTP server).

    Replaces mlx_lm.server HTTP proxy — ConnectionError was caused by
    missing mlx_lm.server process on localhost:8080.
    Uses MLXWorkerThread for thread-safe async execution (M1 crash-safe).
    """
    try:
        hermes_lm = get_hermes_dspy_lm()
        if hermes_lm is None:
            logger.warning("dspy_service: Hermes3DSPyLM unavailable (HLEDAC_ENABLE_LLM != '1')")
            return None
        return hermes_lm
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
        # Inject the compiled prompt as instructions (DSPy 3.2+ uses signature.instructions)
        program.signature.instructions = prompt_template

        async def _run():
            # F288: fail-soft if dspy.context not available (older DSPy versions)
            # F289: ALSO catch AttributeError when dspy.context exists but is broken
            # (e.g. Python 3.14 where dspy.context raises AttributeError internally)
            # F290 FIX: program(query=...) may return a coroutine in DSPy 3.2+ or
            # Python 3.14 where async predict signatures exist — guard with
            # inspect.iscoroutinefunction / asyncio.iscoroutine to prevent
            # "coroutine was never awaited" RuntimeWarning and memory leak.
            try:
                with dspy.context(lm=lm):
                    pred = program(query=query.strip())
                    # Guard: coroutine leak if DSPy/Python version changed behavior
                    if asyncio.iscoroutine(pred):
                        pred = await pred
                    return str(pred.answer) if hasattr(pred, "answer") else None
            except (AttributeError, TypeError):
                # Fallback: set lm directly on program
                program.lm = lm
                pred = program(query=query.strip())
                if asyncio.iscoroutine(pred):
                    pred = await pred
                return str(pred.answer) if hasattr(pred, "answer") else None

        async with asyncio.timeout(TIMEOUT_SECONDS):
            answer = await _run()
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
        program.signature.instructions = prompt_template

        async def _run():
            # F288: fail-soft if dspy.context not available (older DSPy versions)
            # F289: ALSO catch AttributeError when dspy.context exists but is broken
            try:
                with dspy.context(lm=lm):
                    pred = program(query=findings_json[:500])
                    return str(pred.answer) if hasattr(pred, "answer") else None
            except (AttributeError, TypeError):
                program.lm = lm
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
        program.signature.instructions = prompt_template

        async def _run():
            # F288: fail-soft if dspy.context not available (older DSPy versions)
            if hasattr(dspy, "context"):
                with dspy.context(lm=lm):
                    pred = program(query=findings_str[:400])
                    return str(pred.answer) if hasattr(pred, "answer") else None
            else:
                program.lm = lm
                pred = program(query=findings_str[:400])
                return str(pred.answer) if hasattr(pred, "answer") else None

        async with asyncio.timeout(TIMEOUT_SECONDS):
            answer = await _run()
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

    # Check Hermes3 LM availability via singleton state
    health["lm_available"] = (
        Hermes3LM_ENABLED
        and _HERMES_LM_INSTANCE is not None
        and _HERMES_LM_INSTANCE._loaded
    )

    if not health["lm_available"]:
        health["status"] = "warn"

    return health

