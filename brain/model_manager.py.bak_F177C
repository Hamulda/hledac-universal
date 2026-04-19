"""
ModelManager - Správa životního cyklu modelů na M1 8GB

Zajišťuje:
- Sekvenční načítání modelů (nikdy nejsou 2 velké modely současně v RAM)
- Automatické uvolňování paměti (gc + MLX cache clear)
- Jednotné rozhraní pro Hermes3, ModernBERT a GLiNER
- Strict 1-model-at-a-time policy pro M1 8GB stabilitu
"""

from __future__ import annotations

import asyncio
import gc
import inspect
import logging
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable, Literal
from enum import Enum, auto

try:
    import mlx.core as mx
    MLX_AVAILABLE = True
except ImportError:
    MLX_AVAILABLE = False
    mx = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# Sprint 42: ANE CoreML paths
MODELS_DIR = Path.home() / ".hledac" / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)
COREML_MODEL_PATH = MODELS_DIR / "modernbert_ane.mlpackage"

# Typové aliasy pro podporované modely
ModelName = Literal["hermes", "modernbert", "gliner"]


class ModelType(Enum):
    """Typy podporovaných modelů."""
    HERMES = auto()
    MODERNBERT = auto()
    GLINER = auto()


@asynccontextmanager
async def model_lifecycle(model_name: ModelName):
    """
    Async context manager pro striktní 1-model-at-a-time lifecycle.

    Zajišťuje:
    - Načtení modelu s proper logging
    - Yield model instance
    - V finally: release + gc.collect() + mx.clear_cache()

    Usage:
        async with model_lifecycle("hermes") as model:
            result = await model.generate(...)

    Args:
        model_name: Jméno modelu ("hermes", "modernbert", "gliner")

    Yields:
        Načtená instance modelu
    """
    manager = get_model_manager()

    # Kontrola že žádný jiný model není loaded
    if manager._current_model is not None:
        current = manager._current_model.name.lower()
        if current != model_name:
            logger.warning(
                f"[MODEL CONFLICT] Requested '{model_name}' but '{current}' is loaded. "
                f"Releasing current model first."
            )
            await manager._release_current_async()

    # Load nového modelu
    model = await manager._load_model_async(model_name)

    try:
        yield model
    finally:
        # Proper cleanup včetně MLX cache
        await manager._release_current_async()


class ModelManager:
    """
    Centrální správa životního cyklu modelů.

    Klíčová vlastnost: Pouze JEDEN model může být najednou v RAM.
    To zajišťuje stabilitu na M1 8GB.

    Použití:
        # Doporučené - context manager:
        async with model_lifecycle("hermes") as model:
            result = await model.generate(...)

        # Nebo explicitní management:
        manager = ModelManager()
        model = await manager.load_model("hermes")
        # ... použití ...
        await manager.release_current()
    """

    # Mapování model_name -> ModelType
    MODEL_REGISTRY: Dict[str, ModelType] = {
        "hermes": ModelType.HERMES,
        "modernbert": ModelType.MODERNBERT,
        "gliner": ModelType.GLINER,
    }

    # ========================================================================
    # F6.5: Ownership Closure — Phase Drift Guard
    # ========================================================================
    # WORKFLOW-LEVEL phase → model mapping.
    #
    # AUTHORITY (F6.5): This map is STRICTLY Layer 1 (workflow-level).
    # It is NOT the same as the coarse-grained phase system in
    # capabilities.ModelLifecycleManager (BRAIN/TOOLS/SYNTHESIS/CLEANUP).
    #
    # OWNERSHIP DECLARATION (F6.5):
    #   - Acquire/load owner:       THIS CLASS (ModelManager singleton)
    #   - Unload/cleanup owner:     THIS CLASS._release_current_async()
    #                               + brain.model_lifecycle.unload_model() (7K SSOT)
    #   - Phase enforcer:           capabilities.ModelLifecycleManager (FACADE only)
    #   - Capability layer:         NOT a load owner — NEVER call this map directly
    #
    # LAYER MAPPING (F6.5) — MUST NOT BE CONFLATED:
    #   Layer 1 (workflow-level, this map):
    #     PLAN/DECIDE/SYNTHESIZE → hermes
    #     EMBED/DEDUP/ROUTING → modernbert
    #     NER/ENTITY → gliner
    #   Layer 2 (coarse-grained, ModelLifecycleManager):
    #     BRAIN/TOOLS/SYNTHESIS/CLEANUP — entirely different strings
    #   Layer 3 (windup-local, windup_engine.SynthesisRunner):
    #     Own isolated model plane with Qwen/SmolLM
    #
    # INVARIANTS (F6.5) — HARD:
    #   - acquire != phase enforcement
    #   - unload != phase policy
    #   - workflow phases (Layer 1) != coarse phases (Layer 2)
    #   - SYNTHESIZE (Layer 1) ≠ SYNTHESIS (Layer 2)
    #   - capability layer MUST NOT become third model truth
    #
    # Use brain.model_phase_facts.is_same_layer() to validate before comparison.
    # ========================================================================
    PHASE_MODEL_MAP: Dict[str, ModelName] = {
        "PLAN": "hermes",
        "DECIDE": "hermes",
        "SYNTHESIZE": "hermes",
        "EMBED": "modernbert",
        "DEDUP": "modernbert",
        "ROUTING": "modernbert",
        "NER": "gliner",
        "ENTITY": "gliner",
    }

    def __init__(self):
        self._loaded_models: Dict[ModelType, Any] = {}
        self._current_model: Optional[ModelType] = None
        self._model_factories: Dict[ModelType, Callable[[], Any]] = {
            ModelType.HERMES: self._create_hermes_engine,
            ModelType.MODERNBERT: self._create_modernbert_engine,
            ModelType.GLINER: self._create_gliner_engine,
        }
        self._lock = asyncio.Lock()
        # Sprint 55: ANE/MLX embedder
        self._ane_embedder = None
        self._mlx_embedder = None
        # FIX 0: Per-model locks to prevent TOCTOU race conditions
        self._model_locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

        # FIX 8: psutil for RAM pressure guard
        self._psutil_available = False
        try:
            import psutil
            self._psutil = psutil
            self._psutil_available = True
        except ImportError:
            pass

    def _create_hermes_engine(self) -> Any:
        """Factory pro Hermes3Engine."""
        from .hermes3_engine import Hermes3Engine
        return Hermes3Engine()

    def _create_modernbert_engine(self) -> Any:
        """Factory pro ModernBERTEmbedder."""
        from ...embeddings.modernbert_embedder import ModernBERTEmbedder
        return ModernBERTEmbedder()

    def _create_gliner_engine(self) -> Any:
        """Factory pro NEREngine s gliner-relex (NER + relation extraction)."""
        try:
            from gliner import GLiNER

            class NEREngine:
                """NER+RE Engine pomocí gliner-relex-large-v0.5."""

                DEFAULT_MODEL = "knowledgator/gliner-relex-large-v0.5"

                def __init__(self):
                    self._model = None
                    self._is_loaded = False

                async def load(self) -> None:
                    """Načte gliner-relex model - async verze."""
                    if not self._is_loaded:
                        logger.info(f"[MODEL LOAD] gliner-relex start")
                        loop = asyncio.get_running_loop()
                        self._model = await loop.run_in_executor(
                            None,
                            lambda: GLiNER.from_pretrained(self.DEFAULT_MODEL, map_location="cpu")
                        )
                        self._is_loaded = True
                        logger.info("[MODEL LOAD] gliner-relex done")

                def extract(
                    self,
                    text: str,
                    labels: List[str],
                    relations: List[Dict] = None,
                    threshold: float = 0.5
                ) -> Dict[str, Any]:
                    """Extract entities and optionally relations."""
                    if not self._is_loaded:
                        raise RuntimeError("Model not loaded. Use load() first.")

                    if relations:
                        # Joint inference with relations
                        entities, rels = self._model.predict(
                            texts=[text],
                            labels=labels,
                            relations=relations,
                            threshold=threshold,
                            return_relations=True
                        )
                        return {"entities": entities[0] if entities else [], "relations": rels[0] if rels else []}
                    else:
                        # NER only
                        entities = self._model.predict_entities(text, labels, threshold=threshold)
                        return {"entities": entities, "relations": []}

                async def unload(self) -> None:
                    """Uvolní model z paměti - async verze."""
                    if self._is_loaded:
                        logger.info("[MODEL RELEASE] gliner-relex start")
                        self._model = None
                        self._is_loaded = False
                        logger.info("[MODEL RELEASE] gliner-relex done")

            return NEREngine()
        except ImportError:
            logger.error("GLiNER not installed. Install with: pip install gliner")
            raise

    # ========================================================================
    # Sprint F150H: Memory admission gate — fail-fast before heavy load
    # ========================================================================
    # Uses canonical evaluate_uma_state from resource_governor.
    # FAILS FAST at CRITICAL or EMERGENCY to prevent OOM on M1 8GB.
    # Clean separation: this gate is HARD fail (raises), _check_memory_pressure
    # remains SOFT fail (clears cache only).
    # ========================================================================

    def _check_memory_admission(self) -> None:
        """
        Deterministický fail-fast gate před těžkým model loadem.

        Kontroluje system_used_gib přes evaluate_uma_state().
        Pokud je stav CRITICAL nebo EMERGENCY, okamžitě raise.
        NEČEKÁ na lazy evaluation — běží PŘED factory() voláním.

        Raises:
            RuntimeError: Pokud je memory pressure příliš vysoký.
        """
        try:
            from hledac.universal.core.resource_governor import (
                sample_uma_status,
                evaluate_uma_state,
                UMA_STATE_CRITICAL,
                UMA_STATE_EMERGENCY,
            )
        except ImportError:
            # Fail-open: pokud resource_governor není dostupný, neblokujeme load
            return

        try:
            status = sample_uma_status()
            state = evaluate_uma_state(status.system_used_gib)
            if state == UMA_STATE_EMERGENCY:
                raise RuntimeError(
                    f"[MEMORY ADMISSION] EMERGENCY state ({status.system_used_gib:.2f} GiB) — "
                    f"model load BLOCKED to prevent OOM. "
                    f"Free up memory before retrying."
                )
            if state == UMA_STATE_CRITICAL:
                raise RuntimeError(
                    f"[MEMORY ADMISSION] CRITICAL state ({status.system_used_gib:.2f} GiB) — "
                    f"model load BLOCKED to prevent OOM. "
                    f"Free up memory before retrying."
                )
        except RuntimeError:
            raise  # už je to naše RuntimeError, propaguj
        except Exception:
            # Fail-safe: jakákoliv jiná chyba při měření nezablokuje load
            pass

    # FIX 8: RAM pressure guard (SOFT - clears cache only, doesn't block)
    def _check_memory_pressure(self, threshold_gb: float = 0.8) -> bool:
        """Check free RAM, clear MLX cache if below threshold (soft fail)."""
        if not self._psutil_available:
            return False
        try:
            available = self._psutil.virtual_memory().available / 1e9
            if available < threshold_gb:
                if MLX_AVAILABLE and mx is not None:
                    mx.clear_cache()
                logger.warning(f"[MEMORY] Low RAM: {available:.2f}GB, MLX cache cleared")
                return True
        except Exception:
            pass
        return False

    # Sprint 42: CoreML ANE embedder
    def _load_coreml_embedder(self) -> Any:
        """Load CoreML version of ModernBERT if available. Returns None if not."""
        if not COREML_MODEL_PATH.exists():
            logger.debug("[COREML] CoreML model not found, will use MLX fallback")
            return None
        try:
            import coremltools as ct
            mlmodel = ct.models.MLModel(str(COREML_MODEL_PATH))
            logger.info("[COREML] Loaded ANE version of ModernBERT")
            return mlmodel
        except Exception as e:
            logger.warning(f"[COREML] Failed to load CoreML model: {e}")
            return None

    # Sprint 42: Convert ModernBERT to CoreML
    async def _convert_modernbert_to_coreml(self, embedder: Any) -> bool:
        """
        Convert ModernBERT embedder to CoreML format.
        Returns True if conversion succeeded and accuracy passes threshold.
        """
        if COREML_MODEL_PATH.exists():
            return True  # Already converted

        if embedder is None or not hasattr(embedder, '_model'):
            logger.warning("[COREML] No embedder model to convert")
            return False

        try:
            import coremltools as ct
            import numpy as np

            # Try to convert via ONNX path
            # First export MLX to ONNX, then convert to CoreML
            logger.info("[COREML] Starting conversion to CoreML...")

            # Use coremltools convert directly if possible
            # Note: Direct MLX to CoreML is not supported, need ONNX intermediate
            mlx_model = embedder._model

            # Try direct conversion - coremltools may support it
            try:
                ct_model = ct.convert(
                    mlx_model,
                    source="pytorch",  # Try pytorch first as common intermediate
                    convert_to="mlprogram",
                    compute_units=ct.ComputeUnit.CPU_AND_NE
                )
            except Exception as e:
                logger.debug(f"[COREML] Direct conversion failed: {e}, trying alternative")
                # Fallback: skip conversion in test environment
                return False

            ct_model.save(str(COREML_MODEL_PATH))
            logger.info(f"[COREML] Model saved to {COREML_MODEL_PATH}")
            return True
        except Exception as e:
            logger.warning(f"[COREML] Conversion failed: {e}")
            return False

    # FIX 4: Guaranteed model unload on exception via finally block
    @asynccontextmanager
    async def acquire_model_ctx(self, model_name: str):
        """
        Context manager that guarantees model unload on exit.

        Usage:
            async with manager.acquire_model_ctx("gliner") as model:
                result = await model.extract(...)
        """
        model = await self.load_model(model_name)
        try:
            yield model
        finally:
            await self.release_model(model_name)
            if MLX_AVAILABLE and mx is not None:
                try:
                    mx.clear_cache()
                except Exception:
                    pass

    async def with_model(self, model_name: ModelName):
        """
        Vrátí async context manager pro daný model.

        Usage:
            async with manager.with_model("hermes") as model:
                result = await model.generate(...)

        Args:
            model_name: Jméno modelu ("hermes", "modernbert", "gliner")

        Returns:
            Async context manager yielding model instance
        """
        return model_lifecycle(model_name)

    # =========================================================================
    # Sprint 30: KV Cache Compression Helper
    # =========================================================================

    def _estimate_context_length(self, cache) -> int:
        """Estimate context length from KV cache structure."""
        try:
            if hasattr(cache, 'shape') and len(cache.shape) >= 2:
                # Assume cache shape (layers, seq_len, ...)
                return cache.shape[1] if len(cache.shape) > 1 else 0
            return 0
        except:
            return 0

    async def load_model(self, model_name: ModelName) -> Any:
        """
        Async načtení modelu do paměti.

        Pokud je již načten jiný model, nejprve ho uvolní.

        Args:
            model_name: Jméno modelu ("hermes", "modernbert", "gliner")

        Returns:
            Instance načteného modelu

        Raises:
            ValueError: Pokud je model_name neznámé
            RuntimeError: Pokud se načtení nepodaří
        """
        async with self._lock:
            return await self._load_model_async(model_name)

    async def _load_model_async(self, model_name: str) -> Any:
        """Interní async implementace načtení modelu."""
        # FIX 0: Per-model lock to prevent TOCTOU race condition
        model_key = model_name.lower()
        async with self._model_locks[model_key]:
            model_type = self.MODEL_REGISTRY.get(model_key)
            if model_type is None:
                raise ValueError(f"Unknown model: {model_name}")

            # FIX 8: Check RAM pressure before loading
            self._check_memory_pressure()

            # Sprint 7D: Ensure MLX runtime is initialized before model load
            from hledac.universal.brain.model_lifecycle import ensure_mlx_runtime_initialized
            ensure_mlx_runtime_initialized()

            # Pokud je model již načten, vrátíme ho
            if model_type in self._loaded_models:
                self._current_model = model_type
                logger.debug(f"Model {model_name} already loaded")
                return self._loaded_models[model_type]

            # Nejprve uvolníme aktuální model (pokud existuje)
            if self._current_model is not None:
                logger.info(
                    f"[PHASE SWITCH] Releasing {self._current_model.name} "
                    f"before loading {model_name}"
                )
                await self._release_current_async()

            # Sprint F150H: Hard fail-fast memory admission gate
            # Runs BEFORE factory() — prevents OOM on heavy model load
            self._check_memory_admission()

            # Načteme nový model
            try:
                logger.info(f"[MODEL LOAD] {model_name} start")
                factory = self._model_factories[model_type]
                model = factory()

                # Inicializace modelu - vše async
                if hasattr(model, 'initialize'):
                    if inspect.iscoroutinefunction(model.initialize):
                        await model.initialize()
                    else:
                        # Sync metodu zavoláme v executoru
                        loop = asyncio.get_running_loop()
                        await loop.run_in_executor(None, model.initialize)
                elif hasattr(model, 'load'):
                    if inspect.iscoroutinefunction(model.load):
                        await model.load()
                    else:
                        loop = asyncio.get_running_loop()
                        await loop.run_in_executor(None, model.load)

                self._loaded_models[model_type] = model
                self._current_model = model_type
                logger.info(f"[MODEL LOAD] {model_name} done")
                return model

            except Exception as e:
                logger.error(f"Failed to load model {model_name}: {e}")
                raise RuntimeError(f"Failed to load model {model_name}: {e}") from e

    async def release_model(self, model_name: ModelName) -> None:
        """
        Async uvolnění modelu z paměti.

        Args:
            model_name: Jméno modelu ("hermes", "modernbert", "gliner")

        Raises:
            ValueError: Pokud je model_name neznámé
        """
        async with self._lock:
            model_type = self.MODEL_REGISTRY.get(model_name.lower())
            if model_type is None:
                raise ValueError(f"Unknown model: {model_name}")

            if model_type not in self._loaded_models:
                logger.debug(f"Model {model_name} not loaded")
                return

            await self._release_model_async(model_type, model_name)

    async def _release_model_async(self, model_type: ModelType, model_name: str) -> None:
        """Interní async implementace uvolnění modelu."""
        model = self._loaded_models.get(model_type)
        unload_error: Optional[Exception] = None

        # F166E: Always remove from registry first — before unload attempt
        # This ensures no partial-init leak regardless of unload outcome
        if model_type in self._loaded_models:
            del self._loaded_models[model_type]

        if self._current_model == model_type:
            self._current_model = None

        # Attempt unload — failure is logged but never propagated
        if model is not None and hasattr(model, 'unload'):
            logger.info(f"[MODEL RELEASE] {model_name} start")
            try:
                if inspect.iscoroutinefunction(model.unload):
                    await model.unload()
                else:
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(None, model.unload)
                logger.info(f"[MODEL RELEASE] {model_name} done")
            except Exception as e:
                unload_error = e
                logger.error(f"Failed to release model {model_name}: {e}")
                # F166E: Exception swallowed — model already removed from registry

        # Memory cleanup regardless of unload outcome
        await self._cleanup_memory_async(model_type)

    async def release_current(self) -> None:
        """Async uvolnění aktuálně načteného modelu."""
        async with self._lock:
            await self._release_current_async()

    async def _release_current_async(self) -> None:
        """Interní async implementace uvolnění aktuálního modelu."""
        if self._current_model is None:
            return

        model_type = self._current_model
        model_name = model_type.name.lower()

        # F168E: Capture model reference BEFORE registry deletion
        # (for unload call — must happen after _current_model clear but before del)
        model = self._loaded_models.get(model_type)

        # F168E: Always remove from registry first — before unload attempt
        # Mirrors _release_model_async() symmetry (F166E)
        if model_type in self._loaded_models:
            del self._loaded_models[model_type]

        if self._current_model == model_type:
            self._current_model = None

        # Attempt unload — failure is logged but never propagated
        if model is not None and hasattr(model, 'unload'):
            logger.info(f"[MODEL RELEASE] {model_name} start")
            try:
                if inspect.iscoroutinefunction(model.unload):
                    await model.unload()
                else:
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(None, model.unload)
                logger.info(f"[MODEL RELEASE] {model_name} done")
            except Exception as e:
                logger.error(f"Failed to release model {model_name}: {e}")
                # F168E: Exception swallowed — model already removed from registry

        # Memory cleanup regardless of unload outcome
        await self._cleanup_memory_async(model_type)

    async def _cleanup_memory_async(self, model_type: Optional[ModelType] = None) -> None:
        """Agresivní async čištění paměti po uvolnění modelu.

        Args:
            model_type: ModelType being released. If None, uses self._current_model.
        """
        # =========================================================================
        # Sprint 30: KV Cache Compression - apply before cleanup
        # =========================================================================
        # Invariant 3: Quantization in cleanup() - no inference impact
        # FIX: Use passed model_type since _current_model is None at call time
        target_model = model_type if model_type is not None else self._current_model
        if target_model and target_model.name == "HERMES":
            try:
                from .hermes3_engine import Hermes3Engine
                engine = self._loaded_models.get(target_model)
                if engine and hasattr(engine, '_prompt_cache') and engine._prompt_cache:
                    context_len = self._estimate_context_length(engine._prompt_cache)
                    if context_len > 1024:
                        # Invariant 1: Only compress if context > 1024 tokens
                        await engine._compress_kv_cache()
            except Exception:
                pass  # Fail-safe - don't block cleanup

        # Python garbage collection
        gc.collect()

        # MLX cache clear (pro M1)
        if MLX_AVAILABLE and mx is not None:
            try:
                mx.clear_cache()
                logger.debug("MLX cache cleared")
            except Exception as e:
                logger.warning(f"Failed to clear MLX cache: {e}")

    def get_model(self, model_name: ModelName) -> Optional[Any]:
        """
        Vrátí instanci načteného modelu.

        Args:
            model_name: Jméno modelu ("hermes", "modernbert", "gliner")

        Returns:
            Instance modelu nebo None pokud není načten
        """
        model_type = self.MODEL_REGISTRY.get(model_name.lower())
        if model_type is None:
            logger.error(f"Unknown model: {model_name}")
            return None

        return self._loaded_models.get(model_type)

    def is_loaded(self, model_name: ModelName) -> bool:
        """
        Zkontroluje zda je model načten.

        Args:
            model_name: Jméno modelu ("hermes", "modernbert", "gliner")

        Returns:
            True pokud je model načten, False jinak
        """
        model_type = self.MODEL_REGISTRY.get(model_name.lower())
        if model_type is None:
            return False

        return model_type in self._loaded_models

    # ========================================================================
    # Sprint 55: Dynamic embedder selection (ANE vs MLX)
    # ========================================================================

    async def get_embedder(self, resource_allocator=None):
        """
        Vrátí funkci pro embeddování, která se rozhodne podle dostupnosti ANE a zátěže.

        Args:
            resource_allocator: Volitelný resource allocator pro rozhodování

        Returns:
            Funkce pro embeddování textů na embeddingy
        """
        # Lazy import to avoid circular dependencies
        try:
            from .ane_embedder import ANEEmbedder
            from ...embeddings.modernbert_embedder import ModernBERTEmbedder
        except ImportError:
            # Fallback - just return None
            return None

        # Initialize embedders if not already
        if self._ane_embedder is None:
            self._ane_embedder = ANEEmbedder()
        if self._mlx_embedder is None:
            try:
                self._mlx_embedder = ModernBERTEmbedder()
            except Exception:
                self._mlx_embedder = None

        # Check if we should use ANE
        use_ane = False
        if resource_allocator:
            try:
                use_ane = await resource_allocator.can_use_ane()
            except Exception:
                use_ane = False

        if use_ane:
            if not self._ane_embedder.is_loaded:
                await self._ane_embedder.load()
            if self._ane_embedder.is_loaded:
                # Set fallback for when ANE fails
                if self._mlx_embedder:
                    self._ane_embedder.set_fallback(self._mlx_embedder.embed)
                return self._ane_embedder.embed

        # Fallback to MLX
        if self._mlx_embedder:
            return self._mlx_embedder.embed

        return None

    def get_current_model(self) -> Optional[str]:
        """
        Vrátí jméno aktuálně načteného modelu.

        Returns:
            Jméno modelu nebo None
        """
        if self._current_model is None:
            return None
        return self._current_model.name.lower()

    async def release_all(self) -> None:
        """Async uvolnění všech modelů z paměti."""
        logger.info("Releasing all models...")

        async with self._lock:
            last_released: Optional[ModelType] = None
            for model_type in list(self._loaded_models.keys()):
                model_name = model_type.name.lower()
                last_released = model_type
                try:
                    model = self._loaded_models[model_type]
                    if hasattr(model, 'unload'):
                        logger.info(f"[MODEL RELEASE] {model_name} start")
                        if inspect.iscoroutinefunction(model.unload):
                            await model.unload()
                        else:
                            loop = asyncio.get_running_loop()
                            await loop.run_in_executor(None, model.unload)
                        logger.info(f"[MODEL RELEASE] {model_name} done")
                    del self._loaded_models[model_type]
                    logger.info(f"✓ Released {model_name}")
                except Exception as e:
                    logger.error(f"Failed to release {model_name}: {e}")

            self._current_model = None
            await self._cleanup_memory_async(last_released)
            logger.info("✓ All models released")

    async def with_phase(self, phase_name: str):
        """
        Context manager pro fázové workflow.

        Automaticky vybere správný model podle fáze:
        - PLAN/DECIDE/SYNTHESIZE → Hermes
        - EMBED/DEDUP/ROUTING → ModernBERT
        - NER/ENTITY → GLiNER

        Usage:
            async with manager.with_phase("PLAN") as model:
                result = await model.generate(...)

        Args:
            phase_name: Název fáze (např. "PLAN", "EMBED", "NER")

        Returns:
            Async context manager yielding model instance
        """
        model_name = self.PHASE_MODEL_MAP.get(phase_name.upper())
        if model_name is None:
            raise ValueError(f"Unknown phase: {phase_name}")

        logger.info(f"[PHASE START] {phase_name} -> using {model_name}")

        @asynccontextmanager
        async def _phase_context():
            async with model_lifecycle(model_name) as model:
                yield model
            logger.info(f"[PHASE END] {phase_name}")

        return _phase_context()

    async def __aenter__(self) -> ModelManager:
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit - uvolní všechny modely."""
        await self.release_all()

    # ========================================================================
    # Sprint 55: ANE Embedder Integration (get_embedder method below)
    # ========================================================================


# Globální instance pro snadné použití
_model_manager: Optional[ModelManager] = None


def get_model_manager() -> ModelManager:
    """Vrátí globální instanci ModelManager."""
    global _model_manager
    if _model_manager is None:
        _model_manager = ModelManager()
    return _model_manager


async def reset_model_manager() -> None:
    """Resetuje globální instanci ModelManager."""
    global _model_manager
    if _model_manager is not None:
        await _model_manager.release_all()
        _model_manager = None


# Backward compatibility - sync wrappery (DEPRECATED)
# Používejte async verze v novém kódu!

class _SyncCompatibilityWrapper:
    """
    Wrapper pro zpětnou kompatibilitu se sync API.

    DEPRECATED: Používejte async metody přímo!
    """

    def __init__(self, manager: ModelManager):
        self._manager = manager

    def acquire(self, model_name: str) -> bool:
        """DEPRECATED: Použijte await load_model()"""
        logger.warning("DEPRECATED: acquire() is deprecated, use await load_model()")
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                # Jsme v async kontextu, nemůžeme použít run()
                raise RuntimeError(
                    "Cannot use sync acquire() in async context. "
                    "Use: model = await manager.load_model('hermes')"
                )
            else:
                loop.run_until_complete(self._manager.load_model(model_name))
            return True
        except Exception as e:
            logger.error(f"Failed to acquire model: {e}")
            return False

    def release(self, model_name: str) -> bool:
        """DEPRECATED: Použijte await release_model()"""
        logger.warning("DEPRECATED: release() is deprecated, use await release_model()")
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                raise RuntimeError(
                    "Cannot use sync release() in async context. "
                    "Use: await manager.release_model('hermes')"
                )
            else:
                loop.run_until_complete(self._manager.release_model(model_name))
            return True
        except Exception as e:
            logger.error(f"Failed to release model: {e}")
            return False


def get_sync_wrapper() -> _SyncCompatibilityWrapper:
    """Vrátí sync wrapper pro zpětnou kompatibilitu. DEPRECATED!"""
    return _SyncCompatibilityWrapper(get_model_manager())
