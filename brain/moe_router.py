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
"""
import gc
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
import msgspec
from typing import Any
import numpy as np
from ..security.pii_gate import fallback_sanitize
from ..core.embeddings.cache import EmbeddingCache
MAX_LLM_PROMPT_CHARS = 8192
try:
    import mlx.core as mx
    import mlx.nn as mlx_nn
    MLX_AVAILABLE = True
except ImportError:
    MLX_AVAILABLE = False
    mx = None
    mlx_nn = None
_torch_nn = None
logger = logging.getLogger(__name__)

class MoERouterConfig(msgspec.Struct, gc=False):
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
        'config',
    ))

    def __init__(self, config: MoERouterConfig | None=None, sanitize_for_llm: Callable[[str], str] | None=None):
        """
        Initialize MoERouter.

        Args:
            config: MoERouter configuration
            sanitize_for_llm: Optional callback for LLM input sanitization.
                              If provided, used instead of fallback_sanitize.
                              Signature: Callable[[str], str]
        """
        self.config = config or MoERouterConfig()
        self._sanitize_for_llm = sanitize_for_llm
        self._router_mlp: RouterMLP | None = None
        self._experts: dict[str, tuple[Any, Any]] = {}
        self._expert_usage: dict[str, int] = {}
        self._embedding_model = None
        self._embedding_tokenizer = None
        self._prompt_cache_by_expert: dict[str, Any] = {}
        # [META]-013: Delegating to EmbeddingCache(dim=768) — two-layer LRU with
        # free-list memmap. Replaces the old circular-round-robin memmap that
        # had no real eviction. Shares the cache across sessions (meta.json persist).
        self._embed_cache: EmbeddingCache | None = None

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
        except Exception as e:
            logger.error(f'Failed to initialize MoE router: {e}')
            raise

    async def _init_embedding_model(self) -> None:
        """Inicializovat embedding model pro router - lazy import pro avoid circular imports"""
        logger.info('MoE router using hash-based routing (no embedding model)')
        self._embedding_model = None
        self._embedding_tokenizer = None

    async def _load_expert(self, expert_name: str) -> bool:
        """
        Lazy load experta přes mlx_lm.load() bez blokování event loopu.

        Issue M-04: mlx_lm.load() je synchroní blocking call (1-20s).
        Běží v MLXWorker thread, event loop zůstává volný pro jiné coroutines.

        Args:
            expert_name: Jméno experta k načtení

        Returns:
            True pokud se podařilo načíst
        """
        if expert_name in self._experts:
            self._expert_usage[expert_name] = self._expert_usage.get(expert_name, 0) + 1
            return True
        if len(self._experts) >= self.config.max_active_experts:
            await self._evict_lru_expert()
        try:
            from mlx_lm import load
            from hledac.universal.core.mlx_inference_lock import run_in_mlx_worker

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
            except Exception:
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

        [META]-013: Now delegates to EmbeddingCache(dim=768) — two-layer LRU
        with free-list persistent memmap (cross-session). Uses torch-based
        encode when available, falls back to _fallback_embedding on failure.
        """
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
        except Exception:
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
                loaded = await self._load_expert(expert_name)
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
            from mlx_lm import generate
            from hledac.universal.core.mlx_inference_lock import run_in_mlx_worker

            model, tokenizer = self._experts[expert_name]
            formatted_prompt = self._format_expert_prompt(expert_name, query, context, system_prompt)
            if self._sanitize_for_llm is not None:
                formatted_prompt = self._sanitize_for_llm(formatted_prompt)[:MAX_LLM_PROMPT_CHARS]
            else:
                formatted_prompt = fallback_sanitize(formatted_prompt, max_length=MAX_LLM_PROMPT_CHARS)[:MAX_LLM_PROMPT_CHARS]
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
            except Exception:
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
    except Exception:
        pass
    logger.debug('[MoE] route -> hermes3 (default)')
    return 'hermes3'