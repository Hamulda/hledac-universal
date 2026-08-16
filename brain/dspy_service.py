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
from typing import TYPE_CHECKING, Any
from hledac.universal.brain.mlx_worker_thread import MLXWorkerThread
from hledac.universal.utils.asyncx import safe_wait_for
from _core import aclose
try:
    import orjson
except ImportError:
    orjson = None
try:
    import dspy
except ImportError:
    dspy = None
logger = logging.getLogger('dspy_service')
ENABLED = os.getenv('HLEDAC_ENABLE_DSPY', '0') == '1'
CACHE_PATH = Path.home() / '.hledac' / 'dspy_cache.json'
TIMEOUT_SECONDS = 30
MAX_OUTPUT_TOKENS = 50
# Batch scoring concurrency (M1 8GB bounded — DSPy is CPU-light, memory-light)
_SCORING_CONCURRENCY = 5  # max concurrent DSPy scoring calls
_SCORING_BATCH_SIZE = 20  # findings per DSPy call (prompt token budget)
_programs: dict = {}
_programs_loaded: bool = False

def _load_programs() -> dict:
    """Lazy-load compiled DSPy programs from cache. Call once per process."""
    global _programs, _programs_loaded
    if _programs_loaded:
        return _programs
    _programs_loaded = True
    if not CACHE_PATH.exists():
        logger.warning('dspy_service: cache not found at %s', CACHE_PATH)
        return {}
    try:
        if orjson is not None:
            with open(CACHE_PATH, 'rb') as f:
                data = orjson.loads(f.read())
        else:
            import json as _stdlib_json

            with open(CACHE_PATH) as f:
                data = _stdlib_json.load(f)
        prompts = data.get('prompts', {})
        _programs = {k: v for k, v in prompts.items() if v and isinstance(v, str)}
        logger.info('dspy_service: loaded %d compiled programs from cache', len(_programs))
    except Exception as e:
        logger.warning('dspy_service: failed to load cache: %s', e)
        _programs = {}
    return _programs


def _get_dspy_signature(task_key: str) -> tuple[str, Any] | None:
    """
    Shared helper: resolve prompt_template + LM for a DSPy task key.

    Returns (prompt_template, lm) tuple or None if DSPy unavailable/not configured.
    Eliminates 4-line boilerplate repeated in expand_query / score_findings /
    suggest_pivots (Sprint 3 dedup).
    """
    programs = _load_programs()
    prompt_template = programs.get(task_key)
    if not prompt_template:
        logger.warning('dspy_service: no compiled prompt for %s', task_key)
        return None
    lm = _get_dspy_lm()
    if lm is None:
        return None
    return (prompt_template, lm)


Hermes3LM_ENABLED = os.getenv('HLEDAC_ENABLE_LLM', '1') == '1'
_HERMES_LM_INSTANCE: "Hermes3DSPyLM | None" = None

if dspy is not None:
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
        __slots__ = tuple(('_engine', '_loaded', '_model_path'))

        def __init__(self, model_path: str | None=None):
            super().__init__(model=model_path or 'hermes-3-llama', model_type='chat')
            self._model_path = model_path
            self._engine = None
            self._loaded = False

        @classmethod
        def _get_worker(cls) -> MLXWorkerThread:
            """Get or create the shared MLXWorkerThread (singleton per process)."""
            if cls._worker is None:
                cls._worker = MLXWorkerThread(name='dspy-hermes-worker')
                cls._worker.start()
            return cls._worker

        def _ensure_engine(self) -> None:
            """Lazy-load Hermes3Engine or MlxcelHermesAdapter with ANE mutex protection.

            F4XX: If mlxcel binary is available, uses MlxcelHermesAdapter (out-of-process
            Rust inference) instead of DeepHermes3Engine (in-process mlx-lm). This saves
            ~300MB RSS in the Python process.

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
                mutex.acquire_llm(model_size_mb=2000.0)

                # F4XX: Check for mlxcel first — prefer out-of-process inference
                from hledac.universal.brain.model_manager import _mlxcel_is_available
                if _mlxcel_is_available():
                    from hledac.universal.brain.model_manager import MlxcelHermesAdapter
                    self._engine = MlxcelHermesAdapter()
                    logger.info('[DSPy] Using MlxcelHermesAdapter (mlxcel out-of-process)')
                else:
                    from hledac.universal.brain.deephermes3_engine import DeepHermes3Engine
                    self._engine = DeepHermes3Engine(model_path=self._model_path, sanitize_for_llm=None)
                    logger.info('[DSPy] Using DeepHermes3Engine (in-process mlx-lm)')

                worker = self._get_worker()
                assert worker._loop is not None, 'mlx_worker loop not ready'
                asyncio.run_coroutine_threadsafe(self._engine.initialize(), worker._loop).result(timeout=120.0)
                self._loaded = True
            except Exception as e:
                logger.warning('Hermes3DSPyLM engine load failed: %s', e)
                self._loaded = False
                self._engine = None
                raise

        async def _async_generate(self, prompt: str, **kwargs) -> str:
            """Async generation via Hermes3Engine.generate()."""
            self._ensure_engine()
            assert self._engine is not None, 'Hermes3Engine not loaded'
            return await self._engine.generate(prompt, **kwargs)

        @staticmethod
        def _format_chat_to_prompt(messages: list[dict[str, str]] | None, prompt: str | None) -> tuple[str | None, str]:
            """
            Reconstruct a single prompt from ChatAdapter message list.

            Returns:
                tuple of (system_msg, full_prompt)
            """
            system_msg: str | None = None
            prompt_parts: list[str] = []
            if messages:
                for msg in messages:
                    role = msg.get('role', 'user')
                    content = msg.get('content', '')
                    if role == 'system':
                        system_msg = content
                    elif role == 'user':
                        prompt_parts.append(f'User: {content}')
                    elif role == 'assistant':
                        prompt_parts.append(f'Assistant: {content}')
                if prompt:
                    prompt_parts.append(f'User: {prompt}')
                full_prompt = '\n\n'.join(prompt_parts)
            else:
                full_prompt = prompt or ''
            return system_msg, full_prompt

        def forward(self, prompt: str | None=None, messages: list[dict[str, str]] | None=None, **kwargs: Any) -> dict:
            """
            Synchronous forward pass — wraps asyncio call for DSPy compatibility.

            Called by BaseLM.__call__ which expects a dict response matching the
            OpenAI chat completion format (response.choices[0].message.content).
            """
            self._ensure_engine()
            system_msg, full_prompt = self._format_chat_to_prompt(messages, prompt)
            worker = self._get_worker()
            assert worker._loop is not None, 'mlx_worker loop not ready'
            text: str = asyncio.run_coroutine_threadsafe(self._async_generate(full_prompt, system_msg=system_msg, **kwargs), worker._loop).result(timeout=60.0)
            return _HermesChatResponse(text if text else '', model=self.model)

        async def aforward(self, prompt: str | None=None, messages: list[dict[str, str]] | None=None, **kwargs: Any) -> dict:
            """
            Async forward pass — called by BaseLM.acall.

            ChatAdapter formats messages as:
              [{"role": "system"|"user"|"assistant", "content": str}, ...]

            We reconstruct a single prompt by concatenating role-prefixed content.
            """
            self._ensure_engine()
            system_msg, full_prompt = self._format_chat_to_prompt(messages, prompt)
            worker = self._get_worker()
            assert worker._loop is not None, 'mlx_worker loop not ready'
            text: str = await worker.submit(self._async_generate(full_prompt, system_msg=system_msg, **kwargs), timeout=60.0)
            return _HermesChatResponse(text if text else '', model=self.model)

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
                worker = self._get_worker()
                assert worker._loop is not None, 'mlx_worker loop not ready'
                asyncio.run_coroutine_threadsafe(self._engine.unload(), worker._loop).result(timeout=60.0)
            except Exception:  # noqa: BLE001
                pass
            finally:
                try:
                    import mlx.core as _mx
                    _mx.eval([])
                    if _mx.metal.is_available():
                        import gc
                        gc.collect()
                        if hasattr(_mx, 'clear_cache'):
                            _mx.clear_cache()
                except Exception:  # noqa: BLE001
                    pass
                try:
                    from hledac.universal.brain.ane_embedder import get_ane_mlx_mutex
                    get_ane_mlx_mutex().release('llm')
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
        logger.info('dspy_service: DSPy configured with Hermes3Engine')
        return True
    except Exception as e:
        logger.warning('dspy_service: DSPy configure with Hermes failed: %s', e)
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
        logger.warning('dspy_service: failed to create DSPy LM: %s', e)
        return None

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
    sig = _get_dspy_signature('analysis:medium')
    if sig is None:
        return None
    prompt_template, lm = sig
    t0 = time.monotonic()
    try:
        import dspy

        class QueryExpandSignature(dspy.Signature):
            """Expand OSINT query into diverse search variants."""
            query: str = dspy.InputField()
            answer: str = dspy.OutputField()
        program = dspy.Predict(QueryExpandSignature)
        program.signature.instructions = prompt_template

        async def _run():
            try:
                with dspy.context(lm=lm):
                    pred = program(query=query.strip())
                    if asyncio.iscoroutine(pred):
                        pred = await pred
                    return str(pred.answer) if hasattr(pred, 'answer') else None
            except (AttributeError, TypeError):
                program.lm = lm
                pred = program(query=query.strip())
                if asyncio.iscoroutine(pred):
                    pred = await pred
                return str(pred.answer) if hasattr(pred, 'answer') else None
        async with asyncio.timeout(TIMEOUT_SECONDS):
            answer = await _run()
        if answer is None:
            return None
        variants = [line.strip() for line in answer.split('\n') if line.strip() and len(line.strip()) < 120]
        seen = set()
        unique = []
        for v in variants:
            if v not in seen:
                seen.add(v)
                unique.append(v)
        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info('dspy_service: expand_query dspy_call=query_expansion latency_ms=%.0f tokens_in=%d tokens_out=%d variants=%d', elapsed_ms, len(query), len(answer), len(unique))
        return unique[:5] if unique else None
    except TimeoutError:
        logger.warning('dspy_service: expand_query timed out after %ds', TIMEOUT_SECONDS)
        return None
    except Exception as e:
        logger.warning('dspy_service: expand_query failed: %s', e)
        return None

async def score_findings(findings: list, min_score: float=4.0) -> list | None:
    """
    Phase B: DSPy-powered finding relevance scoring — batch-parallel.

    Takes raw findings from discovery → returns scored+filtered list.
    Filters out findings with DSPy relevance score < min_score.

    Returns None if DSPy unavailable — caller accepts all findings.
    Each finding dict must have at least 'content' or 'title' field.

    Batching: findings are split into batches of _SCORING_BATCH_SIZE,
    processed concurrently with a semaphore cap of _SCORING_CONCURRENCY.
    """
    if not ENABLED:
        return None
    if not findings:
        return None
    sig = _get_dspy_signature('extraction:medium')
    if sig is None:
        return None
    prompt_template, lm = sig
    t0 = time.monotonic()

    # Split findings into batches
    batches: list[list[tuple[int, dict]]] = []
    for batch_start in range(0, len(findings), _SCORING_BATCH_SIZE):
        batch_items = [
            (i, findings[i])
            for i in range(batch_start, min(batch_start + _SCORING_BATCH_SIZE, len(findings)))
        ]
        batches.append(batch_items)

    sem = asyncio.Semaphore(_SCORING_CONCURRENCY)
    scored: list[tuple[dict, float]] = []

    async def _score_batch(batch_items: list[tuple[int, dict]]) -> list[tuple[dict, float]]:
        """Score a single batch of findings via DSPy."""
        try:
            import dspy
            batch_findings = [f for _, f in batch_items]
            if orjson is not None:
                findings_json = orjson.dumps(
                    [{'i': i, 't': (f.get('content') or f.get('title') or '')[:60]}
                     for i, f in batch_items]
                ).decode()
            else:
                from hledac.universal.utils.msgspec_json import encode as _msgspec_encode
                findings_json = _msgspec_encode(
                    [{'i': i, 't': (f.get('content') or f.get('title') or '')[:60]}
                     for i, f in batch_items]
                ).decode()

            class RelevanceScoreSignature(dspy.Signature):
                """Score OSINT findings for relevance 0-10."""
                query: str = dspy.InputField()
                answer: str = dspy.OutputField()

            program = dspy.Predict(RelevanceScoreSignature)
            program.signature.instructions = prompt_template

            async def _run():
                try:
                    with dspy.context(lm=lm):
                        pred = program(query=findings_json[:500])
                        return str(pred.answer) if hasattr(pred, 'answer') else None
                except (AttributeError, TypeError):
                    program.lm = lm
                    pred = program(query=findings_json[:500])
                    return str(pred.answer) if hasattr(pred, 'answer') else None

            answer = await safe_wait_for(_run(), timeout=TIMEOUT_SECONDS, label='dspy_score_batch')
            if answer is None:
                return []

            batch_scored: list[tuple[dict, float]] = []
            for line in answer.split('\n'):
                line = line.strip()
                if ':' not in line:
                    continue
                parts = line.rsplit(':', 1)
                try:
                    idx = int(parts[0].strip('[]-: '))
                    score = float(parts[1].strip())
                    if 0 <= score <= 10 and idx < len(batch_findings):
                        batch_scored.append((batch_findings[idx], score))
                except (ValueError, IndexError):  # noqa: BLE001
                    pass
            return batch_scored
        except TimeoutError:
            logger.warning('dspy_service: score_batch timed out after %ds', TIMEOUT_SECONDS)
            return []
        except Exception as e:
            logger.warning('dspy_service: score_batch failed: %s', e)
            return []

    async def _score_batch_sem(batch_items: list[tuple[int, dict]]) -> list[tuple[dict, float]]:
        async with sem:
            return await _score_batch(batch_items)

    try:
        # Run all batches concurrently, bounded by semaphore
        from hledac.universal.utils.asyncx import parallel
        result = await parallel(
            [_score_batch_sem(b) for b in batches],
            policy="log",
            ctx="dspy_score",
    )
        for item in result.ok:
            if isinstance(item, list):
                scored.extend(item)

        scored.sort(key=lambda x: x[1], reverse=True)
        filtered = [(f, s) for f, s in scored if s >= min_score]
        elapsed_ms = (time.monotonic() - t0) * 1000
        total_batches = len(batches)
        logger.info(
            'dspy_service: score_findings batches=%d concurrency=%d batch_size=%d '
            'latency_ms=%.0f scored=%d filtered=%d',
            total_batches, _SCORING_CONCURRENCY, _SCORING_BATCH_SIZE,
            elapsed_ms, len(scored), len(filtered)
    )
        return filtered if filtered else None
    except Exception as e:
        logger.warning('dspy_service: score_findings failed: %s', e)
        return None

async def suggest_pivots(findings: list, context: dict | None=None) -> list | None:
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
    sig = _get_dspy_signature('summarization:medium')
    if sig is None:
        return None
    prompt_template, lm = sig
    t0 = time.monotonic()
    try:
        import dspy
        finding_texts = [(f.get('content') or f.get('title') or str(f))[:80] for f in findings[:10]]
        findings_str = '\n'.join((f'  {i}. {t}' for i, t in enumerate(finding_texts)))

        class PivotSuggestSignature(dspy.Signature):
            """Suggest OSINT pivot seeds from findings."""
            query: str = dspy.InputField()
            answer: str = dspy.OutputField()
        program = dspy.Predict(PivotSuggestSignature)
        program.signature.instructions = prompt_template

        async def _run():
            if hasattr(dspy, 'context'):
                with dspy.context(lm=lm):
                    pred = program(query=findings_str[:400])
                    return str(pred.answer) if hasattr(pred, 'answer') else None
            else:
                program.lm = lm
                pred = program(query=findings_str[:400])
                return str(pred.answer) if hasattr(pred, 'answer') else None
        async with asyncio.timeout(TIMEOUT_SECONDS):
            answer = await _run()
        if answer is None:
            return None
        pivots = []
        for line in answer.split('\n'):
            line = line.strip()
            if '|' in line:
                parts = line.split('|')
                if len(parts) == 3:
                    try:
                        ioc_value = parts[0].strip()
                        ioc_type = parts[1].strip().lower()
                        confidence = float(parts[2].strip())
                        if ioc_value and ioc_type in ('domain', 'ip', 'url', 'hash', 'email'):
                            pivots.append({'ioc_value': ioc_value, 'ioc_type': ioc_type, 'confidence': min(1.0, max(0.0, confidence))})
                    except ValueError:  # noqa: BLE001
                        pass
        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info('dspy_service: suggest_pivots dspy_call=pivot_suggestion latency_ms=%.0f tokens_in=%d tokens_out=%d pivots=%d', elapsed_ms, len(findings_str), len(answer), len(pivots))
        return pivots[:5] if pivots else None
    except TimeoutError:
        logger.warning('dspy_service: suggest_pivots timed out after %ds', TIMEOUT_SECONDS)
        return None
    except Exception as e:
        logger.warning('dspy_service: suggest_pivots failed: %s', e)
        return None

async def check_health() -> dict:
    """
    Returns dict with DSPy service health status.
    Used by preflight_check.py — WARN (not FAIL) if unavailable.
    """
    health = {'dspy_enabled': ENABLED, 'cache_exists': CACHE_PATH.exists(), 'programs_loaded': 0, 'lm_available': False, 'status': 'ok'}
    if not ENABLED:
        health['status'] = 'disabled'
        return health
    programs = _load_programs()
    health['programs_loaded'] = len(programs)
    if not programs:
        health['status'] = 'warn'
        return health
    health['lm_available'] = Hermes3LM_ENABLED and _HERMES_LM_INSTANCE is not None and _HERMES_LM_INSTANCE._loaded
    if not health['lm_available']:
        health['status'] = 'warn'
    return health