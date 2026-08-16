"""
🔧 HELPER - MoERouter (Mixture-of-Experts)
===========================================




Toto je HELPER modul pro MoE routing.

Používá se pouze jako pomocný nástroj pro deephermes3_engine.
Pro decision making použijte CANONICAL verzi:
    from hledac.universal.brain.deephermes3_engine import DeepHermes3Engine

Tento modul implementuje MoE routing pro výběr specializovaných expertů
na základě obsahu dotazu. Optimalizováno pro M1 8GB s max 2 aktivními
experty v paměti současně.

Features:
- Lazy loading expertů
- Max 2 aktivní experti v paměti
- Sekvenční zpracování
- Agresivní cleanup
- Memory-aware routing (Sprint 8TD)
- SWARM-001: Micro-model routing s <1ms TRUE ZERO-COPY hot-swap
- SWARM-002: Multilingual embedding routing (BGE-M3 for non-English)

SWARM-001 Integration:
    from hledac.universal.brain.moe_swarm_integration import MoERouterSwarmMixin

SWARM-002 Integration:
    Multilingual embedding support for cross-lingual threat intelligence.
    Non-English queries are routed to BGE-M3 multilingual embedder.
"""
import asyncio
import gc
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
import msgspec
from compat.msgspec_gc_compat import Struct
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from mlx_lm import Model as MLXModel
    from mlx_lm import TokenizerWrapper as MLXTokenizer
import numpy as np
from ..security.pii_gate import fallback_sanitize
from ..core.embeddings.cache import EmbeddingCache
MAX_LLM_PROMPT_CHARS = 8192

# C1-X FIX: Import MLX_AVAILABLE from SSOT (zero-import detection)
from hledac.universal.utils.mlx_memory import MLX_AVAILABLE
from _core import aclose

# Lazy accessor for mlx modules - uses centralized get_mx() from SSOT
def _get_mlx():
    """Lazy accessor for mlx.core — uses centralized get_mx() from SSOT."""
    from hledac.universal.utils.mlx_memory._core import get_mx as _get_mx_from_core
    return _get_mx_from_core()

def _get_mlx_nn():
    """Lazy accessor for mlx.nn — returns None if unavailable."""
    if MLX_AVAILABLE:
        try:
            import mlx.nn as _nn
            return _nn
        except ImportError:
            pass
    return None
_torch_nn = None
logger = logging.getLogger(__name__)

# SWARM-001: Optional micro-model swarm integration
try:
    from .micro_model_swarm import (
        MicroModelSwarmRouter,
        create_swarm_router,
        TaskType,
        MICRO_MODELS,
    )
    SWARM_AVAILABLE = True
except ImportError:
    SWARM_AVAILABLE = False
    MicroModelSwarmRouter = None
    create_swarm_router = None
    TaskType = None
    MICRO_MODELS = {}
    logger.warning("MicroModelSwarm not available (SWARM-001 disabled)")

# SWARM-002: Optional multilingual embedding support
_MULTILINGUAL_AVAILABLE = False
try:
    from hledac.universal._core.multilingual import (
        detect_language,
        get_lang_detector,
        get_bge_m3_embedder,
    )
    _MULTILINGUAL_AVAILABLE = True
except ImportError:
    logger.debug("[MoE] Multilingual modules not available (SWARM-002 disabled)")

class MoERouterConfig(Struct):
    """Konfigurace pro MoE Router"""
    expert_names: list[str] = field(default_factory=lambda: ['osint', 'security', 'temporal', 'graph', 'synthesis'])
    model_paths: dict[str, str] = field(default_factory=lambda: {'osint': 'mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit', 'security': 'mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit', 'temporal': 'mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit', 'graph': 'mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit', 'synthesis': 'mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit'})
    max_active_experts: int = 2
    temperature: float = 0.3
    max_tokens_per_expert: int = 1024
    enable_mlx_quantization: bool = True

class RouterMLP:
    """
    Simple MLP pro routing mezi experty.

    Architektura: input_dim -> hidden -> num_experts

    Uses mlx_nn when available, torch_nn as fallback.
    """
    __slots__ = ('_nn', 'fc1', 'fc2')

    def __init__(self, input_dim: int, num_experts: int, hidden_dim: int=128):
        # ISSUE #5.5: Removed redundant global _torch_nn + double self._nn assignment.
        # self._nn is instance-only; torch.nn is module-level cache for lazy import.
        if MLX_AVAILABLE and mlx_nn is not None:
            _nn = mlx_nn
        else:
            global _torch_nn
            if _torch_nn is None:
                import torch.nn as _torch_nn
            _nn = _torch_nn
        self._nn = _nn
        self.fc1 = _nn.Linear(input_dim, hidden_dim)
        self.fc2 = _nn.Linear(hidden_dim, num_experts)

    def __call__(self, x) -> mx.array:
        """Forward pass vrací logits pro každého experta"""
        if self._nn is None:
            raise RuntimeError('No neural network backend available (MLX and torch both unavailable)')
        x = self.fc1(x)
        x = mx.maximum(x, 0)
        x = self.fc2(x)
        return x

    def get_expert_weights(self, embedding: np.ndarray) -> np.ndarray:
        """Get softmax weights for experts given query embedding."""
        if not MLX_AVAILABLE:
            num_experts = self.fc2.weight.shape[0] if hasattr(self.fc2, 'weight') else 5
            return np.ones(num_experts) / num_experts
        try:
            x = mx.array(embedding.reshape(1, -1))
            logits = self(x)
            weights = mx.softmax(logits, axis=-1)
            return np.array(weights).flatten()
        except Exception as e:
            logger.warning(f'Failed to get expert weights: {e}')
            num_experts = self.fc2.weight.shape[0] if hasattr(self.fc2, 'weight') else 5
            return np.ones(num_experts) / num_experts

class MoERouter:
    """
    Mixture-of-Experts Router pro M1 8GB.

    Features:
    - Lazy loading expertů
    - Max 2 aktivní experti v paměti
    - Sekvenční zpracování
    - Agresivní cleanup
    - Memory-aware routing (Sprint 8TD)
    - SWARM-001: Micro-model routing s <100ms hot-swap
    """
    KNOWN_MODEL_SIZES: dict[str, float] = {'mlx-community/Hermes-3-Llama-3.1-8B-4bit': 5.2, 'mlx-community/Hermes-3-Llama-3.1-8B-8bit': 9.1, 'mlx-community/Phi-3.5-mini-instruct-4bit': 2.4, 'mlx-community/Mistral-7B-Instruct-v0.3-4bit': 4.8, 'mlx-community/gemma-2-2b-it-4bit': 1.8}
    __slots__ = tuple((
        '_embed_cache',
        '_embedding_model',
        '_embedding_tokenizer',
        '_expert_usage',
        '_experts',
        '_prompt_cache_by_expert',
        '_router_mlp',
        '_sanitize_for_llm',
        '_swarm_router',
        '_swarm_enabled',
        # MoERouterSwarmMixin slots (must be declared in concrete class)
        '_swarm_lock',
        '_swarm_initialized',
        'config',
    ))

    def __init__(self, config: MoERouterConfig | None=None, sanitize_for_llm: Callable[[str], str] | None=None, enable_swarm: bool = True):
        """
        Initialize MoERouter.

        Args:
            config: MoERouter configuration
            sanitize_for_llm: Optional callback for LLM input sanitization.
                              If provided, used instead of fallback_sanitize.
                              Signature: Callable[[str], str]
            enable_swarm: Enable SWARM-001 micro-model routing (default: True)
        """
        self.config = config or MoERouterConfig()
        self._sanitize_for_llm = sanitize_for_llm
        self._router_mlp: RouterMLP | None = None
        self._experts: dict[str, tuple[MLXModel, MLXTokenizer]] = {}
        self._expert_usage: dict[str, int] = {}
        self._embedding_model = None
        self._embedding_tokenizer = None
        self._prompt_cache_by_expert: dict[str, Any] = {}  # type: ignore[assignment]
        # [META]-013: Delegating to EmbeddingCache(dim=768) — two-layer LRU with
        # free-list memmap. Replaces the old circular-round-robin memmap that
        # had no real eviction. Shares the cache across sessions (meta.json persist).
        self._embed_cache: EmbeddingCache | None = None
        
        # SWARM-001: Micro-model swarm router
        self._swarm_enabled = enable_swarm and SWARM_AVAILABLE
        self._swarm_router: MicroModelSwarmRouter | None = None

    async def initialize(self) -> None:
        """Inicializovat router MLP a embedding model"""
        if not MLX_AVAILABLE:
            logger.warning('MLX not available, MoE router will not function')
            return
        try:
            num_experts = len(self.config.expert_names)
            self._router_mlp = RouterMLP(input_dim=768, num_experts=num_experts, hidden_dim=128)
            logger.info(f'✓ Router MLP initialized ({num_experts} experts)')
            await self._init_embedding_model()
            # SWARM-001: Initialize micro-model swarm router
            if self._swarm_enabled:
                await self._init_swarm_router()
        except Exception as e:
            logger.error(f'Failed to initialize MoE router: {e}')
            raise

    async def _init_embedding_model(self) -> None:
        """Inicializovat embedding model pro router - lazy import pro avoid circular imports"""
        logger.info('MoE router using hash-based routing (no embedding model)')
        self._embedding_model = None
        self._embedding_tokenizer = None

    # SWARM-001: Micro-model swarm methods
    async def _init_swarm_router(self) -> None:
        """
        Initialize the MicroModelSwarmRouter for task-specialized routing.
        
        This enables:
        - Content-based routing (regex patterns, <1ms)
        - Micro-model pool with <100ms hot-swap
        - UMA-resident micro-models with LRU eviction
        """
        if not self._swarm_enabled:
            return
        
        try:
            # Create swarm router with adaptive budget for micro-models
            # (ResourceGovernor calculates optimal: ~3.2GB for M1 8GB)
            self._swarm_router = create_swarm_router(
                memory_budget_mb=None,  # Use adaptive budget
                preload_models=True,
                use_adaptive_budget=True,
    )
            logger.info('[SWARM-001] MicroModelSwarmRouter initialized')
            
            # Preload priority models in background
            import asyncio
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._swarm_router.preload_priority_models)
            logger.info('[SWARM-001] Priority micro-models preloading...')
            
        except Exception as e:
            logger.error(f'[SWARM-001] Failed to initialize swarm router: {e}')
            self._swarm_enabled = False

    async def _classify_for_swarm(self, text: str) -> tuple[str | None, TaskType]:
        """
        Classify text and route to appropriate micro-model.
        
        Uses regex-based content classification (<1ms latency).
        Falls back to main model if no micro-model is suitable.
        
        Args:
            text: Input text to classify
            
        Returns:
            Tuple of (micro_model_id, task_type)
        """
        if not self._swarm_router or not self._swarm_enabled:
            return (None, TaskType.GENERAL)
        
        try:
            model_id, task_type = self._swarm_router.route(text)
            # Ensure we always return a valid TaskType
            if task_type is None:
                task_type = TaskType.GENERAL
            return (model_id, task_type)
        except Exception as e:
            logger.warning(f'[SWARM-001] Classification failed: {e}')
            return (None, TaskType.GENERAL)

    async def _load_micro_model(self, model_id: str) -> bool:
        """
        Load a micro-model via pointer swap (<100ms).
        
        Instead of full mlx_lm.load() (1-20s), we use pre-loaded
        model pool and swap pointers.
        
        Args:
            model_id: ID of micro-model (e.g., 'qwen_coder')
            
        Returns:
            True if model loaded/available
        """
        if not self._swarm_router or not self._swarm_enabled:
            return False
        
        try:
            loaded = self._swarm_router._pool.get_model(model_id)
            return loaded is not None
        except Exception as e:
            logger.warning(f'[SWARM-001] Failed to load micro-model {model_id}: {e}')
            return False

    def get_swarm_stats(self) -> dict[str, Any]:
        """Get SWARM-001 statistics."""
        if not self._swarm_router:
            return {"enabled": False}
        return self._swarm_router.get_stats()
    
    @property
    def swarm_memory_pressure(self) -> float:
        """Current micro-model pool memory pressure."""
        if self._swarm_router:
            return self._swarm_router.memory_pressure
        return 0.0
    
    @property
    def swarm_loaded_models(self) -> list[str]:
        """List of currently loaded micro-models."""
        if self._swarm_router:
            return self._swarm_router.loaded_models
        return []
    
    @property
    def swarm_enabled(self) -> bool:
        """Whether SWARM-001 micro-model routing is enabled."""
        return self._swarm_enabled

    async def _load_expert(
        self,
        expert_name: str,
        query: str = "",
    ) -> bool:
        """
        Lazy load experta přes mlx_lm.load() bez blokování event loopu.

        Issue M-04: mlx_lm.load() je synchroní blocking call (1-20s).
        Běží v MLXWorker thread, event loop zůstává volný pro jiné coroutines.

        SWARM-001: If query is provided and SWARM is enabled, tries micro-model
        routing first for task-specialized inference (<100ms hot-swap).

        Args:
            expert_name: Jméno experta k načtení
            query: Optional query text for SWARM-001 micro-model routing

        Returns:
            True pokud se podařilo načíst
        """
        if expert_name in self._experts:
            self._expert_usage[expert_name] = self._expert_usage.get(expert_name, 0) + 1
            return True
        
        # SWARM-001: Try micro-model routing for compatible tasks
        if self._swarm_enabled and query:
            micro_model_id, task_type = await self._classify_for_swarm(query)
            if micro_model_id and task_type:
                # Expert is compatible with micro-model task
                expert_task_map = {
                    'osint': 'CLASSIFICATION',
                    'security': 'CODE',
                    'temporal': 'SYNTHESIS',
                    'graph': 'GENERAL',
                    'synthesis': 'SYNTHESIS',
                }
                expected_task = expert_task_map.get(expert_name, '')
                # Compare task types (both are TaskType enum)
                from .micro_model_swarm import TaskType as SWTaskType
                if expected_task == 'CLASSIFICATION' and task_type == SWTaskType.CLASSIFICATION:
                    pass  # Match
                elif expected_task == 'CODE' and task_type == SWTaskType.CODE:
                    pass  # Match
                elif expected_task == 'SYNTHESIS' and task_type in (SWTaskType.SYNTHESIS, SWTaskType.TRANSLATION):
                    pass  # Match
                elif expected_task == 'GENERAL':
                    pass  # Always allow general fallback
                else:
                    # Task mismatch - don't use micro-model
                    micro_model_id = None
                
                if micro_model_id:
                    logger.info(f'[SWARM-001] Routing {expert_name} to micro-model: {micro_model_id}')
                    if await self._load_micro_model(micro_model_id):
                        # Store micro-model reference - use same key as full model
                        # The _generate_with_expert will detect micro-model and use swarm router
                        self._experts[expert_name] = (f"_swarm:{micro_model_id}", task_type)
                        self._expert_usage[expert_name] = 1
                        return True
                    # Fall through to regular loading if micro-model load failed
        
        if len(self._experts) >= self.config.max_active_experts:
            await self._evict_lru_expert()
        try:
            from mlx_lm import load
            from hledac.universal._core.mlx_inference_lock import run_in_mlx_worker

            model_path = self.config.model_paths.get(expert_name)
            if not model_path:
                logger.error(f'No model path configured for expert: {expert_name}')
                return False
            logger.info(f'Loading expert: {expert_name} from {model_path} (non-blocking)')
            # Issue M-04: run mlx_lm.load() in worker thread — event loop stays FREE
            model, tokenizer = await run_in_mlx_worker(load, model_path)
            try:
                from mlx_lm.utils import make_prompt_cache
                self._prompt_cache_by_expert[expert_name] = make_prompt_cache(model)
                logger.info(f'✓ Prompt cache initialized for {expert_name}')
            except Exception as e:
                logger.warning(f'Prompt cache init failed for {expert_name}: {e}')
                self._prompt_cache_by_expert[expert_name] = None
            self._experts[expert_name] = (model, tokenizer)
            self._expert_usage[expert_name] = 1
            logger.info(f"✓ Expert '{expert_name}' loaded (non-blocking)")
            return True
        except Exception as e:
            logger.error(f"Failed to load expert '{expert_name}': {e}")
            return False

    async def _evict_lru_expert(self) -> None:
        """Unload nejméně používaného experta (LRU eviction)"""
        if not self._experts:
            return
        lru_expert = min(self._expert_usage.keys(), key=lambda k: self._expert_usage[k])
        logger.info(f'Evicting LRU expert: {lru_expert}')
        await self._unload_expert(lru_expert)

    async def _unload_expert(self, expert_name: str) -> None:
        """
        Explicitní cleanup experta z paměti.

        Args:
            expert_name: Jméno experta k uvolnění
        """
        if expert_name not in self._experts:
            return
        logger.info(f'Unloading expert: {expert_name}')
        del self._experts[expert_name]
        if expert_name in self._expert_usage:
            del self._expert_usage[expert_name]
        self._prompt_cache_by_expert.pop(expert_name, None)
        if MLX_AVAILABLE and mx is not None:
            try:
                mx.eval([])
            except Exception:  # noqa: BLE001
                pass
            gc.collect()
            if hasattr(mx, 'clear_cache'):
                mx.clear_cache()
        logger.info(f"✓ Expert '{expert_name}' unloaded")

    async def _embed_with_torch(self, text: str) -> np.ndarray | None:
        """
        Encode text using the torch embedding model (the original approach).
        
        Returns 768-dim normalized float32 embedding, or None on failure.
        """
        try:
            if self._embedding_model is None or self._embedding_tokenizer is None:
                return None
            inputs = self._embedding_tokenizer(text, return_tensors='pt', truncation=True, max_length=512, padding=True)
            try:
                import torch
                import torch.nn.functional as F
                with torch.no_grad():
                    outputs = self._embedding_model(**inputs)
                    embeddings = outputs.last_hidden_state.mean(dim=1)
                    embeddings = F.normalize(embeddings, p=2, dim=1)
                    return embeddings.numpy().flatten().astype(np.float32)
            except ImportError:
                return None
        except Exception:
            return None

    async def _get_query_embedding(self, query: str) -> np.ndarray:
        """
        Získat embedding dotazu pro router.

        SWARM-002: Language-aware embedding routing:
        - English queries → torch embed (768d)
        - Non-English queries → BGE-M3 embed (MRL truncated to 768d)

        [META]-013: Now delegates to EmbeddingCache(dim=768) — two-layer LRU
        with free-list persistent memmap (cross-session). Uses torch-based
        encode when available, falls back to _fallback_embedding on failure.
        """
        # SWARM-002: Language detection for multilingual routing
        lang_result = None
        if _MULTILINGUAL_AVAILABLE:
            try:
                lang_result = detect_language(query)
            except Exception:  # noqa: BLE001
                pass

        # SWARM-002: Route to appropriate embedder based on language
        if lang_result is not None and not lang_result.is_english:
            return await self._get_multilingual_embedding(query)

        # English path: torch embed (original behavior)
        # [META]-013: lazily create the facade wrapping EmbeddingCache(dim=768)
        if self._embed_cache is None:
            self._embed_cache = EmbeddingCache(dim=768)
        # EmbeddingCache.get_or_encode with torch embed fn for correct 768-dim output
        result: np.ndarray | None = await self._embed_cache.get_or_encode(
            query, encode_fn=self._embed_with_torch
    )
        if result is not None:
            return result
        # Fallback: stateless hash embedding when nothing works
        return self._fallback_embedding(query)

    async def _get_multilingual_embedding(self, query: str) -> np.ndarray:
        """
        SWARM-002: Get multilingual embedding via BGE-M3.

        BGE-M3 provides cross-lingual semantic alignment for 100+ languages.
        Embeddings are MRL-truncated to 768d for MoE router compatibility.

        Args:
            query: Non-English query text

        Returns:
            768-dim normalized float32 embedding
        """
        if not _MULTILINGUAL_AVAILABLE:
            # Fallback to hash if multilingual not available
            return self._fallback_embedding(query)

        try:
            bge_embedder = get_bge_m3_embedder(mrl_target_dim=768, lazy_load=True)

            # Load model if not already loaded
            if not bge_embedder.is_loaded:
                bge_embedder.load()

            # Get embedding (BGE-M3 1024d → MRL truncated to 768d)
            # BGE-M3 embed is async, so we can await it directly
            embedding = await bge_embedder.embed(query, truncate_to=768)
            return embedding

        except Exception as e:
            logger.warning(f'[MoE] BGE-M3 embedding failed: {e}')
            return self._fallback_embedding(query)

    def _fallback_embedding(self, query: str) -> np.ndarray:
        """
        Fallback embedding když není dostupný model.

        Args:
            query: Vstupní dotaz

        Returns:
            768-dim embedding vektor (RouterMLP expects 768-dim input)
        """
        try:
            words = query.lower().split()
            embedding_384 = np.zeros(384, dtype=np.float32)
            for i, word in enumerate(words[:50]):
                for j, char in enumerate(word[:10]):
                    idx = (ord(char) + i * 31 + j * 17) % 384
                    embedding_384[idx] += 1.0
            norm = np.linalg.norm(embedding_384)
            if norm > 0:
                embedding_384 = embedding_384 / norm
            embedding_768 = np.concatenate([embedding_384, embedding_384])
            return embedding_768
        except Exception:
            return np.zeros(768, dtype=np.float32)

    def _get_available_memory_gb(self) -> float:
        """
        Sprint 8TD: Zjistit dostupnou UMA paměť přes mlx.core nebo psutil.

        Returns:
            Dostupná paměť v GB (min 0.5GB pro bezpečný fallback).
        """
        try:
            import mlx.core as mx
            if hasattr(mx, 'metal') and hasattr(mx.metal, 'get_active_memory'):
                peak = mx.get_active_memory()
                total_bytes = 8 * 1024 ** 3
                return max(0.5, (total_bytes - peak) / 1024 ** 3)
        except Exception:  # noqa: BLE001
            pass
        try:
            import psutil
            return psutil.virtual_memory().available / 1024 ** 3
        except Exception:
            return 2.0

    async def _route_experts(self, query: str) -> list[tuple[str, float]]:
        """
        Vybrat top_k experty na základě dotazu.

        Sprint 8TD: Memory-aware routing — filtruje experty podle dostupné paměti.

        Args:
            query: Vstupní dotaz

        Returns:
            Seznam (expert_name, score) tuples, seřazené podle skóre
        """
        if not MLX_AVAILABLE or self._router_mlp is None:
            return [(name, 1.0 / len(self.config.expert_names)) for name in self.config.expert_names]
        try:
            embedding = await self._get_query_embedding(query)
            x = mx.array(embedding.reshape(1, -1))
            logits = self._router_mlp(x)
            weights = mx.softmax(logits, axis=-1)
            weights_np = np.array(weights).flatten()
            expert_scores = [(name, float(weights_np[i])) for i, name in enumerate(self.config.expert_names)]
            expert_scores.sort(key=lambda x: x[1], reverse=True)
            avail = self._get_available_memory_gb()
            feasible_experts = [(name, score) for name, score in expert_scores if self.KNOWN_MODEL_SIZES.get(self.config.model_paths.get(name, ''), 3.0) <= avail - 0.5]
            if not feasible_experts:
                logger.warning(f'MoE: no expert fits in {avail:.1f}GB — using nano expert')
                feasible_experts = [(expert_scores[-1][0], expert_scores[-1][1])]
            logger.debug(f'MoE: avail={avail:.1f}GB, feasible={len(feasible_experts)}/{len(expert_scores)}')
            top_k = self.config.max_active_experts
            return feasible_experts[:top_k]
        except Exception as e:
            logger.error(f'Routing failed: {e}')
            return [(name, 1.0 / len(self.config.expert_names)) for name in self.config.expert_names]

    async def route(self, query_text: str, rag_context: list[str]) -> list[str]:
        """
        P16: Route query to experts based on content analysis.

        Uses query embedding and memory-aware routing to select top experts.

        Args:
            query_text: Input query string.
            rag_context: List of context strings from RAG (unused but part of contract).

        Returns:
            List of expert IDs (e.g., ['osint', 'security']).
            Returns up to max_active_experts based on memory availability.
        """
        try:
            expert_scores = await self._route_experts(query_text)
            expert_ids = [expert for expert, score in expert_scores]
            logger.debug(f'[MoE] route -> {expert_ids} for query: {query_text[:50]}')
            return expert_ids
        except Exception as e:
            logger.warning(f'[MoE] route failed: {e}, returning default experts')
            return self.config.expert_names[:self.config.max_active_experts]

    async def generate(self, query: str, context: dict[str, Any] | None=None, system_prompt: str | None=None) -> str:
        """
        Hlavní metoda pro generování pomocí MoE.

        Flow:
        1. Router vybere top_k expertů
        2. Sekvenčně zpracuje každého experta
        3. Sloučí výstupy přes synthesis experta

        Args:
            query: Vstupní dotaz
            context: Kontext pro generování
            system_prompt: Systémový prompt

        Returns:
            Finální odpověď
        """
        if not MLX_AVAILABLE:
            return 'Error: MLX not available'
        context = context or {}
        try:
            selected_experts = await self._route_experts(query)
            logger.info(f'Selected experts: {[e[0] for e in selected_experts]}')
            expert_outputs = []
            for expert_name, score in selected_experts:
                if expert_name == 'synthesis':
                    continue
                # SWARM-001: Pass query for micro-model routing
                loaded = await self._load_expert(expert_name, query=query)
                if not loaded:
                    logger.warning(f'Failed to load expert: {expert_name}')
                    continue
                output = await self._generate_with_expert(expert_name, query, context, system_prompt)
                expert_outputs.append({'expert': expert_name, 'score': score, 'output': output})
                if len(self._experts) >= self.config.max_active_experts:
                    await self._unload_expert(expert_name)
            if expert_outputs:
                final_output = await self._synthesize_outputs(query, expert_outputs, context, system_prompt)
                return final_output
            else:
                return 'Error: No experts produced output'
        except Exception as e:
            logger.error(f'MoE generation failed: {e}')
            return f'Error: {str(e)}'

    async def _generate_with_expert(self, expert_name: str, query: str, context: dict[str, Any], system_prompt: str | None=None) -> str:
        """
        Generovat pomocí konkrétního experta bez blokování event loopu.

        Issue M-04: mlx_lm.generate() je synchroní blocking call (1-60s).
        Běží v MLXWorker thread, event loop zůstává volný pro jiné coroutines.

        SWARM-001: If expert has micro-model assigned (stored as "_swarm:model_id"),
        uses the micro-model pool for generation instead.

        Args:
            expert_name: Jméno experta
            query: Vstupní dotaz
            context: Kontext
            system_prompt: Systémový prompt

        Returns:
            Vygenerovaný text
        """
        if expert_name not in self._experts:
            return f'Error: Expert {expert_name} not loaded'
        
        try:
            model_or_ref, tokenizer_or_task = self._experts[expert_name]
            formatted_prompt = self._format_expert_prompt(expert_name, query, context, system_prompt)
            if self._sanitize_for_llm is not None:
                formatted_prompt = self._sanitize_for_llm(formatted_prompt)[:MAX_LLM_PROMPT_CHARS]
            else:
                formatted_prompt = fallback_sanitize(formatted_prompt, max_length=MAX_LLM_PROMPT_CHARS)[:MAX_LLM_PROMPT_CHARS]
            
            # SWARM-001: Check if this is a micro-model reference
            if isinstance(model_or_ref, str) and model_or_ref.startswith('_swarm:'):
                micro_model_id = model_or_ref.replace('_swarm:', '')
                logger.info(f'[SWARM-001] Using micro-model: {micro_model_id}')
                
                if self._swarm_router:
                    result = self._swarm_router._pool.generate(
                        micro_model_id,
                        formatted_prompt,
                        max_tokens=self.config.max_tokens_per_expert,
                        temp=self.config.temperature,
    )
                    return result.strip()
                else:
                    logger.warning('[SWARM-001] Swarm router not available, falling back to main model')
            
            # Standard path: use loaded model
            from mlx_lm import generate
            from hledac.universal._core.mlx_inference_lock import run_in_mlx_worker

            model, tokenizer = model_or_ref, tokenizer_or_task
            
            # Issue M-04: run mlx_lm.generate() in worker thread — event loop stays FREE
            response = await run_in_mlx_worker(
                generate,
                model, tokenizer,
                prompt=formatted_prompt,
                temp=self.config.temperature,
                max_tokens=self.config.max_tokens_per_expert,
                max_kv_size=8192,
                kv_bits=4,
                prompt_cache=self._prompt_cache_by_expert.get(expert_name),
                verbose=False,
    )
            return response.strip()
        except Exception as e:
            logger.error(f'Expert {expert_name} generation failed: {e}')
            return f'Error from {expert_name}: {str(e)}'

    def _format_expert_prompt(self, expert_name: str, query: str, context: dict[str, Any], system_prompt: str | None=None) -> str:
        """
        Formátovat prompt pro konkrétního experta.

        Args:
            expert_name: Jméno experta
            query: Vstupní dotaz
            context: Kontext
            system_prompt: Volitelný systémový prompt

        Returns:
            Formátovaný prompt
        """
        expert_system_prompts = {'osint': 'You are an OSINT (Open Source Intelligence) expert. Focus on finding publicly available information from open sources.', 'security': 'You are a cybersecurity expert. Focus on security analysis, vulnerabilities, and protective measures.', 'temporal': 'You are a temporal analysis expert. Focus on timelines, chronology, and time-based patterns.', 'graph': 'You are a graph analysis expert. Focus on relationships, connections, and network structures.', 'synthesis': 'You are a synthesis expert. Combine multiple expert analyses into a coherent, comprehensive answer.'}
        system = system_prompt or expert_system_prompts.get(expert_name, 'You are a helpful research assistant.')
        prompt = f'<|im_start|>system\n{system}<|im_end|>\n<|im_start|>user\n{query}<|im_end|>\n<|im_start|>assistant\n'
        return prompt

    async def _synthesize_outputs(self, query: str, expert_outputs: list[dict[str, Any]], context: dict[str, Any], system_prompt: str | None=None) -> str:
        """
        Sloučit výstupy expertů do finální odpovědi.

        Args:
            query: Původní dotaz
            expert_outputs: Výstupy od jednotlivých expertů
            context: Kontext
            system_prompt: Systémový prompt

        Returns:
            Syntetizovaná odpověď
        """
        if len(expert_outputs) == 1:
            return expert_outputs[0]['output']
        synthesis_loaded = await self._load_expert('synthesis')
        if synthesis_loaded:
            synthesis_input = self._format_synthesis_input(query, expert_outputs)
            synthesis_output = await self._generate_with_expert('synthesis', synthesis_input, context, system_prompt)
            return synthesis_output
        else:
            return self._fallback_synthesis(expert_outputs)

    # S6-REFACTOR: Shared formatter — eliminates 96.1% clone between
    # _format_synthesis_input (numbered) and _fallback_synthesis (markdown).
    def _format_expert_block(
        self,
        output: dict[str, Any],
        index: int | None = None,
        prefix: str = "###",
        max_chars: int | None = None,
    ) -> str:
        """
        Format a single expert output block for synthesis.

        Args:
            output: Expert output dict with 'expert', 'score', 'output' keys.
            index: Optional number prefix (None = no number, e.g. "1. Expert: ...").
            prefix: Markdown header prefix (e.g. "###" or "##").
            max_chars: Optional truncation of output text (None = no truncation).

        Returns:
            Formatted block string.
        """
        label = output["expert"].upper()
        score_label = "confidence" if prefix == "##" else "weight"
        header = (
            f"{prefix} {label} ({score_label}: {output['score']:.2f})"
            if index is None
            else f"\n{index}. {label} (confidence: {output['score']:.2f}):"
    )
        text = output["output"]
        if max_chars is not None and len(text) > max_chars:
            text = text[:max_chars]
        return f"{header}\n{text}" if index is None else f"{header}\n{text}"

    def _format_synthesis_input(self, query: str, expert_outputs: list[dict[str, Any]]) -> str:
        """
        Formátovat vstup pro synthesis experta.

        Args:
            query: Původní dotaz
            expert_outputs: Výstupy expertů

        Returns:
            Formátovaný synthesis prompt
        """
        blocks = [f"Original Query: {query}\n\nExpert Analyses:"]
        for i, output in enumerate(expert_outputs, 1):
            blocks.append(self._format_expert_block(output, index=i, prefix="##", max_chars=2000))
        blocks.append("\nSynthesize a comprehensive answer combining these expert perspectives.")
        return "\n".join(blocks)

    def _fallback_synthesis(self, expert_outputs: list[dict[str, Any]]) -> str:
        """
        Jednoduchá syntéza když není dostupný synthesis expert.

        Args:
            expert_outputs: Výstupy expertů

        Returns:
            Spojený text
        """
        blocks = ["## Expert Analysis"]
        for output in expert_outputs:
            blocks.append(self._format_expert_block(output, index=None, prefix="###"))
        return "\n\n".join(blocks)

    async def cleanup(self) -> None:
        """Unload všech expertů a cleanup"""
        logger.info('Cleaning up MoE router...')
        expert_names = list(self._experts.keys())
        for expert_name in expert_names:
            await self._unload_expert(expert_name)
        if self._embed_cache is not None:
            await self._embed_cache.close()
            self._embed_cache = None
        self._router_mlp = None
        self._embedding_model = None
        self._embedding_tokenizer = None
        if MLX_AVAILABLE and mx is not None:
            try:
                mx.eval([])
            except Exception:  # noqa: BLE001
                pass
            gc.collect()
            if hasattr(mx, 'clear_cache'):
                mx.clear_cache()
        logger.info('✓ MoE router cleaned up')

    def get_status(self) -> dict[str, Any]:
        """Get router status (non-async version for simple checks)."""
        cache_stats = self._embed_cache.get_stats() if self._embed_cache else {}
        return {
            'initialized': self._router_mlp is not None,
            'experts_loaded': list(self._experts.keys()),
            'expert_usage': dict(self._expert_usage),
            'max_active': self.config.max_active_experts,
            'embed_cache_stats': cache_stats,
            'mlx_available': MLX_AVAILABLE,
        }

    async def get_expert_info(self) -> dict[str, Any]:
        """
        Získat informace o routeru a expertech.

        Returns:
            Dict s informacemi
        """
        return {
            'config': {
                'expert_names': self.config.expert_names,
                'max_active_experts': self.config.max_active_experts,
                'temperature': self.config.temperature,
                'max_tokens_per_expert': self.config.max_tokens_per_expert,
            },
            'loaded_experts': list(self._experts.keys()),
            'expert_usage': self._expert_usage.copy(),
            'embed_cache_stats': self._embed_cache.get_stats() if self._embed_cache else {},
            'mlx_available': MLX_AVAILABLE,
        }

def route_synthesis(findings_count: int, has_gnn: bool, memory_pressure: str, sprint_query: str) -> str:
    """
    Vybírá synthesis engine dle aktuálních podmínek.

    Vrací jeden z: "hermes3", "inference", "heuristic".

    Strategie:
      - critical memory     → "heuristic" (nulový RAM overhead)
      - findings_count < 5  → "heuristic" (málo dat pro LLM)
      - has_gnn            → prefer "hermes3" (richer context)
      - default            → "inference"
    """
    if memory_pressure == 'critical':
        return 'heuristic'
    if findings_count < 5:
        return 'heuristic'
    if has_gnn:
        return 'hermes3'
    return 'inference'

def route_embedding(memory_pressure: str) -> str:
    """
    Vybírá embedding engine.

    Vrací: "ane_minilm" | "hash_fallback"
    """
    if memory_pressure in ('warn', 'critical'):
        return 'hash_fallback'
    return 'ane_minilm'

async def create_moe_router(config: MoERouterConfig | None=None) -> MoERouter | None:
    """
    Factory funkce pro vytvoření MoE routeru.

    Args:
        config: Volitelná konfigurace

    Returns:
        MoERouter instance nebo None pokud MLX není dostupné
    """
    if not MLX_AVAILABLE:
        logger.warning('MLX not available, MoE router disabled')
        return None
    router = MoERouter(config or MoERouterConfig())
    await router.initialize()
    return router

def route(query: str, context: dict) -> str:
    """
    FÁZE P14: Route query to appropriate model based on content analysis.

    Analyzes query and context to select the best model:
    - 'vision': context contains images or <img> tags
    - 'modernbert': PDF/structured data detected
    - 'hermes3': default text routing

    Uses heuristics (regex) and memory pressure check (GPU > 3GB → smaller model).

    Args:
        query: Input query string
        context: Dict that may contain:
            - 'has_images': bool flag
            - 'content_type': 'pdf', 'html', 'text', etc.
            - 'urls': list of URLs to check for .pdf

    Returns:
        str in {'hermes3', 'modernbert', 'vision'}
    """
    import re
    logger = logging.getLogger(__name__)
    img_pattern = re.compile('<img|image|photo|picture| screenshot', re.IGNORECASE)
    if img_pattern.search(query):
        logger.debug('[MoE] route -> vision (img tag detected)')
        return 'vision'
    if context.get('has_images') or context.get('images'):
        logger.debug('[MoE] route -> vision (images in context)')
        return 'vision'
    content_type = context.get('content_type', '').lower()
    if content_type in ('pdf', 'application/pdf', 'structured'):
        logger.debug('[MoE] route -> modernbert (structured/PDF)')
        return 'modernbert'
    urls = context.get('urls', [])
    for url in urls if isinstance(urls, list) else []:
        if url.endswith('.pdf') or '.pdf?' in str(url):
            logger.debug('[MoE] route -> modernbert (PDF URL)')
            return 'modernbert'
    try:
        import mlx.core as mx
        if hasattr(mx, 'metal') and hasattr(mx.metal, 'get_active_memory'):
            active_bytes = mx.get_active_memory()
            active_gb = active_bytes / 1024 ** 3
            if active_gb > 3.0:
                logger.debug(f'[MoE] route -> hermes3 (memory pressure: {active_gb:.1f}GB)')
                return 'hermes3'
    except Exception:  # noqa: BLE001
        pass
    logger.debug('[MoE] route -> hermes3 (default)')
    return 'hermes3'