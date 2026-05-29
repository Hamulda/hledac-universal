"""
🔧 HELPER - MoERouter (Mixture-of-Experts)
===========================================

Toto je HELPER modul pro MoE routing.

Používá se pouze jako pomocný nástroj pro hermes3_engine.
Pro decision making použijte CANONICAL verzi:
    from hledac.universal.brain.hermes3_engine import Hermes3Engine

Tento modul implementuje MoE routing pro výběr specializovaných expertů
na základě obsahu dotazu. Optimalizováno pro M1 8GB s max 2 aktivními
experty v paměti současně.

Features:
- Lazy loading expertů
- Max 2 aktivní experti v paměti
- Sekvenční zpracování
- Agresivní cleanup
"""

from __future__ import annotations

import gc
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np

# SECURITY: Import fallback sanitizer for LLM input sanitization (failsafe)
from ..security.pii_gate import fallback_sanitize

# Hard limit for LLM prompt (no user toggles)
MAX_LLM_PROMPT_CHARS = 8192

try:
    import mlx.core as mx
    import mlx.nn as mlx_nn
    MLX_AVAILABLE = True
except ImportError:
    MLX_AVAILABLE = False
    mx = None
    mlx_nn = None

# Lazy torch.nn - only import when MLX not available (torch is heavy, ~0.8s)
_torch_nn = None

logger = logging.getLogger(__name__)


@dataclass
class MoERouterConfig:
    """Konfigurace pro MoE Router"""
    expert_names: list[str] = field(default_factory=lambda: [
        "osint", "security", "temporal", "graph", "synthesis"
    ])
    model_paths: dict[str, str] = field(default_factory=lambda: {
        "osint": "mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit",
        "security": "mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit",
        "temporal": "mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit",
        "graph": "mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit",
        "synthesis": "mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit",
    })
    max_active_experts: int = 2  # M1 8GB limit
    temperature: float = 0.3
    max_tokens_per_expert: int = 1024
    enable_mlx_quantization: bool = True


class RouterMLP:
    """
    Simple MLP pro routing mezi experty.

    Architektura: input_dim -> hidden -> num_experts

    Uses mlx_nn when available, torch_nn as fallback.
    """

    def __init__(self, input_dim: int, num_experts: int, hidden_dim: int = 128):
        global _torch_nn
        if MLX_AVAILABLE and mlx_nn is not None:
            _nn = mlx_nn
        else:
            if _torch_nn is None:
                import torch.nn as _torch_nn
            _nn = _torch_nn
        self._nn = _nn
        self.fc1 = _nn.Linear(input_dim, hidden_dim)
        self.fc2 = _nn.Linear(hidden_dim, num_experts)
        self._nn = _nn

    def __call__(self, x) -> mx.array:
        """Forward pass vrací logits pro každého experta"""
        if self._nn is None:
            raise RuntimeError("No neural network backend available (MLX and torch both unavailable)")
        x = self.fc1(x)
        x = mx.maximum(x, 0)  # ReLU
        x = self.fc2(x)
        return x

    def get_expert_weights(self, embedding: np.ndarray) -> np.ndarray:
        """Get softmax weights for experts given query embedding."""
        if not MLX_AVAILABLE:
            # Return uniform weights if MLX not available
            num_experts = self.fc2.weight.shape[0] if hasattr(self.fc2, 'weight') else 5
            return np.ones(num_experts) / num_experts

        try:
            # Convert to MLX array
            x = mx.array(embedding.reshape(1, -1))
            # Forward pass
            logits = self(x)
            # Softmax
            weights = mx.softmax(logits, axis=-1)
            return np.array(weights).flatten()
        except Exception as e:
            logger.warning(f"Failed to get expert weights: {e}")
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

    # Sprint 8TD: Known model sizes in GB for memory-aware routing
    KNOWN_MODEL_SIZES: dict[str, float] = {
        "mlx-community/Hermes-3-Llama-3.1-8B-4bit":  5.2,
        "mlx-community/Hermes-3-Llama-3.1-8B-8bit":  9.1,  # přes budget!
        "mlx-community/Phi-3.5-mini-instruct-4bit":   2.4,
        "mlx-community/Mistral-7B-Instruct-v0.3-4bit": 4.8,
        "mlx-community/gemma-2-2b-it-4bit":            1.8,  # nano expert
    }

    def __init__(
        self,
        config: MoERouterConfig = None,
        sanitize_for_llm: Callable[[str], str] | None = None
    ):
        """
        Initialize MoERouter.

        Args:
            config: MoERouter configuration
            sanitize_for_llm: Optional callback for LLM input sanitization.
                              If provided, used instead of fallback_sanitize.
                              Signature: Callable[[str], str]
        """
        self.config = config or MoERouterConfig()

        # Sanitizer injection - centralizes security in orchestrator
        self._sanitize_for_llm = sanitize_for_llm

        self._router_mlp: RouterMLP | None = None
        self._experts: dict[str, tuple[Any, Any]] = {}
        self._expert_usage: dict[str, int] = {}  # Pro LRU eviction
        self._embedding_model = None
        self._embedding_tokenizer = None
        self._prompt_cache_by_expert: dict[str, Any] = {}  # Per-expert prompt cache

        # Embedding cache
        self._embedding_cache: dict[str, np.ndarray] = {}
        self._max_cache_size = 100

    async def initialize(self) -> None:
        """Inicializovat router MLP a embedding model"""
        if not MLX_AVAILABLE:
            logger.warning("MLX not available, MoE router will not function")
            return

        try:
            # Inicializovat router MLP
            num_experts = len(self.config.expert_names)
            # Použijeme 768-dim embeddings (ModernBERT-base)
            self._router_mlp = RouterMLP(
                input_dim=768,
                num_experts=num_experts,
                hidden_dim=128
            )
            logger.info(f"✓ Router MLP initialized ({num_experts} experts)")

            # Inicializovat embedding model
            await self._init_embedding_model()

        except Exception as e:
            logger.error(f"Failed to initialize MoE router: {e}")
            raise

    async def _init_embedding_model(self) -> None:
        """Inicializovat embedding model pro router - lazy import pro avoid circular imports"""
        # Note: Embedding model disabled - uses ModernBERT via dedicated embedder
        # MoE router now uses simple hashing fallback for routing decisions
        logger.info("MoE router using hash-based routing (no embedding model)")
        self._embedding_model = None
        self._embedding_tokenizer = None

    async def _load_expert(self, expert_name: str) -> bool:
        """
        Lazy load experta přes mlx_lm.load().

        Args:
            expert_name: Jméno experta k načtení

        Returns:
            True pokud se podařilo načíst
        """
        if expert_name in self._experts:
            # Update usage pro LRU
            self._expert_usage[expert_name] = self._expert_usage.get(expert_name, 0) + 1
            return True

        # Check memory limit - pokud máme max_active_experts, unload nejméně používaného
        if len(self._experts) >= self.config.max_active_experts:
            await self._evict_lru_expert()

        try:
            from mlx_lm import load

            model_path = self.config.model_paths.get(expert_name)
            if not model_path:
                logger.error(f"No model path configured for expert: {expert_name}")
                return False

            logger.info(f"Loading expert: {expert_name} from {model_path}")
            model, tokenizer = load(model_path)

            # Initialize prompt cache for this expert (fail-safe)
            try:
                from mlx_lm.utils import make_prompt_cache
                self._prompt_cache_by_expert[expert_name] = make_prompt_cache(model)
                logger.info(f"✓ Prompt cache initialized for {expert_name}")
            except Exception as e:
                logger.warning(f"Prompt cache init failed for {expert_name}: {e}")
                self._prompt_cache_by_expert[expert_name] = None

            self._experts[expert_name] = (model, tokenizer)
            self._expert_usage[expert_name] = 1

            logger.info(f"✓ Expert '{expert_name}' loaded")
            return True

        except Exception as e:
            logger.error(f"Failed to load expert '{expert_name}': {e}")
            return False

    async def _evict_lru_expert(self) -> None:
        """Unload nejméně používaného experta (LRU eviction)"""
        if not self._experts:
            return

        # Najít experta s nejnižším usage
        lru_expert = min(self._expert_usage.keys(), key=lambda k: self._expert_usage[k])

        logger.info(f"Evicting LRU expert: {lru_expert}")
        await self._unload_expert(lru_expert)

    async def _unload_expert(self, expert_name: str) -> None:
        """
        Explicitní cleanup experta z paměti.

        Args:
            expert_name: Jméno experta k uvolnění
        """
        if expert_name not in self._experts:
            return

        logger.info(f"Unloading expert: {expert_name}")

        # Odstranit z paměti
        del self._experts[expert_name]
        if expert_name in self._expert_usage:
            del self._expert_usage[expert_name]

        # Remove only that expert's prompt cache
        self._prompt_cache_by_expert.pop(expert_name, None)

        # Agresivní cleanup
        gc.collect()
        if MLX_AVAILABLE and mx is not None:
            mx.eval([])
            mx.clear_cache()
        gc.collect()

        logger.info(f"✓ Expert '{expert_name}' unloaded")

    async def _get_query_embedding(self, query: str) -> np.ndarray:
        """
        Získat embedding dotazu pro router.

        Args:
            query: Vstupní dotaz

        Returns:
            Embedding vektor
        """
        # Check cache
        cache_key = hash(query) % (2**32)
        if str(cache_key) in self._embedding_cache:
            return self._embedding_cache[str(cache_key)]

        try:
            if self._embedding_model is None or self._embedding_tokenizer is None:
                # Fallback: simple hash-based embedding
                return self._fallback_embedding(query)

            # Tokenize
            inputs = self._embedding_tokenizer(
                query,
                return_tensors="pt",
                truncation=True,
                max_length=512,
                padding=True
            )

            # Get embeddings - lazy import torch
            try:
                from contextlib import nullcontext

                import torch

                with torch.no_grad():
                    outputs = self._embedding_model(**inputs)

                    # Mean pooling
                    embeddings = outputs.last_hidden_state.mean(dim=1)
                    embeddings = torch.nn.functional.normalize(embeddings, p=2, dim=1)

                    result = embeddings.numpy().flatten()
            except ImportError:
                # torch not available, use fallback
                logger.warning("torch not available for embedding, using fallback")
                return self._fallback_embedding(query)

            # Cache result
            if len(self._embedding_cache) >= self._max_cache_size:
                # Remove oldest entry
                oldest_key = next(iter(self._embedding_cache))
                del self._embedding_cache[oldest_key]

            self._embedding_cache[str(cache_key)] = result
            return result

        except Exception as e:
            logger.warning(f"Embedding failed, using fallback: {e}")
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
            # Simple bag-of-words embedding (384-dim)
            words = query.lower().split()
            embedding_384 = np.zeros(384, dtype=np.float32)

            for i, word in enumerate(words[:50]):  # Max 50 words
                # Simple hash-based feature
                for j, char in enumerate(word[:10]):
                    idx = (ord(char) + i * 31 + j * 17) % 384
                    embedding_384[idx] += 1.0

            # Normalize
            norm = np.linalg.norm(embedding_384)
            if norm > 0:
                embedding_384 = embedding_384 / norm

            # Expand to 768-dim by concatenating with itself
            embedding_768 = np.concatenate([embedding_384, embedding_384])
            return embedding_768
        except Exception:
            # Fail-safe: return zeros
            return np.zeros(768, dtype=np.float32)

    # ------------------------------------------------------------------
    # Sprint 8TD: Memory-aware routing
    # ------------------------------------------------------------------

    def _get_available_memory_gb(self) -> float:
        """
        Sprint 8TD: Zjistit dostupnou UMA paměť přes mlx.core nebo psutil.

        Returns:
            Dostupná paměť v GB (min 0.5GB pro bezpečný fallback).
        """
        try:
            import mlx.core as mx
            if hasattr(mx, 'metal') and hasattr(mx.metal, 'get_active_memory'):
                peak = mx.metal.get_active_memory()
                total_bytes = 8 * 1024**3  # 8GB total
                return max(0.5, (total_bytes - peak) / 1024**3)
        except Exception:
            pass
        try:
            import psutil
            return psutil.virtual_memory().available / 1024**3
        except Exception:
            return 2.0  # safe default

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
            # Fallback: vrátit všechny experty s rovným skóre
            return [(name, 1.0 / len(self.config.expert_names))
                    for name in self.config.expert_names]

        try:
            # Get query embedding
            embedding = await self._get_query_embedding(query)

            # Convert to MLX array
            x = mx.array(embedding.reshape(1, -1))

            # Forward pass through router MLP
            logits = self._router_mlp(x)

            # Softmax pro váhy
            weights = mx.softmax(logits, axis=-1)
            weights_np = np.array(weights).flatten()

            # Seřadit experty podle váhy
            expert_scores = [
                (self.config.expert_names[i], float(weights_np[i]))
                for i in range(len(self.config.expert_names))
            ]
            expert_scores.sort(key=lambda x: x[1], reverse=True)

            # Sprint 8TD: Memory-aware filtering
            avail = self._get_available_memory_gb()
            # 0.5GB reserve for safety
            feasible_experts = [
                (name, score) for name, score in expert_scores
                if self.KNOWN_MODEL_SIZES.get(self.config.model_paths.get(name, ""), 3.0)
                <= avail - 0.5
            ]
            if not feasible_experts:
                # Fallback na nejmenší expert (nano expert)
                logger.warning(f"MoE: no expert fits in {avail:.1f}GB — using nano expert")
                feasible_experts = [(expert_scores[-1][0], expert_scores[-1][1])]

            logger.debug(f"MoE: avail={avail:.1f}GB, feasible={len(feasible_experts)}/{len(expert_scores)}")

            # Vrátit top_k z feasible
            top_k = self.config.max_active_experts
            return feasible_experts[:top_k]

        except Exception as e:
            logger.error(f"Routing failed: {e}")
            # Fallback
            return [(name, 1.0 / len(self.config.expert_names))
                    for name in self.config.expert_names]

    # P16: Instance method for routing with explicit query + rag_context
    async def route(
        self,
        query_text: str,
        rag_context: list[str],
    ) -> list[str]:
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
            # Return just the expert IDs (names), not the scores
            expert_ids = [expert for expert, score in expert_scores]
            logger.debug(f"[MoE] route -> {expert_ids} for query: {query_text[:50]}")
            return expert_ids
        except Exception as e:
            logger.warning(f"[MoE] route failed: {e}, returning default experts")
            # Fallback: return top experts by config order
            return self.config.expert_names[:self.config.max_active_experts]

    async def generate(
        self,
        query: str,
        context: dict[str, Any] = None,
        system_prompt: str = None
    ) -> str:
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
            return "Error: MLX not available"

        context = context or {}

        try:
            # Krok 1: Router vybere top_k expertů
            selected_experts = await self._route_experts(query)
            logger.info(f"Selected experts: {[e[0] for e in selected_experts]}")

            # Krok 2: Sekvenčně zpracovat každého experta
            expert_outputs = []

            for expert_name, score in selected_experts:
                if expert_name == "synthesis":
                    # Synthesis expert se použije až na konci
                    continue

                # Load expert
                loaded = await self._load_expert(expert_name)
                if not loaded:
                    logger.warning(f"Failed to load expert: {expert_name}")
                    continue

                # Generate
                output = await self._generate_with_expert(
                    expert_name,
                    query,
                    context,
                    system_prompt
                )

                expert_outputs.append({
                    "expert": expert_name,
                    "score": score,
                    "output": output
                })

                # Evict pokud máme moc expertů
                if len(self._experts) >= self.config.max_active_experts:
                    await self._unload_expert(expert_name)

            # Krok 3: Syntéza výstupů
            if expert_outputs:
                final_output = await self._synthesize_outputs(
                    query, expert_outputs, context, system_prompt
                )
                return final_output
            else:
                return "Error: No experts produced output"

        except Exception as e:
            logger.error(f"MoE generation failed: {e}")
            return f"Error: {str(e)}"

    async def _generate_with_expert(
        self,
        expert_name: str,
        query: str,
        context: dict[str, Any],
        system_prompt: str = None
    ) -> str:
        """
        Generovat pomocí konkrétního experta.

        Args:
            expert_name: Jméno experta
            query: Vstupní dotaz
            context: Kontext
            system_prompt: Systémový prompt

        Returns:
            Vygenerovaný text
        """
        if expert_name not in self._experts:
            return f"Error: Expert {expert_name} not loaded"

        try:
            from mlx_lm import generate

            model, tokenizer = self._experts[expert_name]

            # Format prompt podle experta
            formatted_prompt = self._format_expert_prompt(
                expert_name, query, context, system_prompt
            )

            # SECURITY: Sanitize prompt before inference (sanitize first, then bound)
            # Priority: injected callback > fallback (failsafe)
            if self._sanitize_for_llm is not None:
                # Use injected sanitizer from orchestrator (preferred path)
                formatted_prompt = self._sanitize_for_llm(formatted_prompt)[:MAX_LLM_PROMPT_CHARS]
            else:
                # Failsafe: use fallback when no callback injected
                formatted_prompt = fallback_sanitize(formatted_prompt, max_length=MAX_LLM_PROMPT_CHARS)[:MAX_LLM_PROMPT_CHARS]

            # Generate
            response = generate(
                model,
                tokenizer,
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
            logger.error(f"Expert {expert_name} generation failed: {e}")
            return f"Error from {expert_name}: {str(e)}"

    def _format_expert_prompt(
        self,
        expert_name: str,
        query: str,
        context: dict[str, Any],
        system_prompt: str = None
    ) -> str:
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
        # Default systémové zprávy pro jednotlivé experty
        expert_system_prompts = {
            "osint": "You are an OSINT (Open Source Intelligence) expert. Focus on finding publicly available information from open sources.",
            "security": "You are a cybersecurity expert. Focus on security analysis, vulnerabilities, and protective measures.",
            "temporal": "You are a temporal analysis expert. Focus on timelines, chronology, and time-based patterns.",
            "graph": "You are a graph analysis expert. Focus on relationships, connections, and network structures.",
            "synthesis": "You are a synthesis expert. Combine multiple expert analyses into a coherent, comprehensive answer.",
        }

        system = system_prompt or expert_system_prompts.get(
            expert_name,
            "You are a helpful research assistant."
        )

        # ChatML formát
        prompt = f"""<|im_start|>system
{system}<|im_end|>
<|im_start|>user
{query}<|im_end|>
<|im_start|>assistant
"""

        return prompt

    async def _synthesize_outputs(
        self,
        query: str,
        expert_outputs: list[dict[str, Any]],
        context: dict[str, Any],
        system_prompt: str = None
    ) -> str:
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
        # Pokud máme jen jeden výstup, vrať ho přímo
        if len(expert_outputs) == 1:
            return expert_outputs[0]["output"]

        # Pokusit se použít synthesis experta
        synthesis_loaded = await self._load_expert("synthesis")

        if synthesis_loaded:
            # Připravit synthesis prompt
            synthesis_input = self._format_synthesis_input(query, expert_outputs)

            synthesis_output = await self._generate_with_expert(
                "synthesis",
                synthesis_input,
                context,
                system_prompt
            )

            return synthesis_output
        else:
            # Fallback: jednoduché spojení výstupů
            return self._fallback_synthesis(expert_outputs)

    def _format_synthesis_input(
        self,
        query: str,
        expert_outputs: list[dict[str, Any]]
    ) -> str:
        """
        Formátovat vstup pro synthesis experta.

        Args:
            query: Původní dotaz
            expert_outputs: Výstupy expertů

        Returns:
            Formátovaný synthesis prompt
        """
        parts = [f"Original Query: {query}\n\nExpert Analyses:"]

        for i, output in enumerate(expert_outputs, 1):
            parts.append(f"\n{i}. {output['expert'].upper()} (confidence: {output['score']:.2f}):")
            parts.append(output['output'][:2000])  # Limit délky

        parts.append("\n\nSynthesize a comprehensive answer combining these expert perspectives.")

        return "\n".join(parts)

    def _fallback_synthesis(self, expert_outputs: list[dict[str, Any]]) -> str:
        """
        Jednoduchá syntéza když není dostupný synthesis expert.

        Args:
            expert_outputs: Výstupy expertů

        Returns:
            Spojený text
        """
        parts = ["## Expert Analysis\n"]

        for output in expert_outputs:
            parts.append(f"\n### {output['expert'].upper()} (weight: {output['score']:.2f})")
            parts.append(output['output'])

        return "\n\n".join(parts)

    async def cleanup(self) -> None:
        """Unload všech expertů a cleanup"""
        logger.info("Cleaning up MoE router...")

        # Unload všech expertů
        expert_names = list(self._experts.keys())
        for expert_name in expert_names:
            await self._unload_expert(expert_name)

        # Clear cache
        self._embedding_cache.clear()

        # Cleanup modelů
        self._router_mlp = None
        self._embedding_model = None
        self._embedding_tokenizer = None

        # Final GC
        gc.collect()
        if MLX_AVAILABLE and mx is not None:
            mx.eval([])
            mx.clear_cache()
        gc.collect()

        logger.info("✓ MoE router cleaned up")

    def get_status(self) -> dict[str, Any]:
        """Get router status (non-async version for simple checks)."""
        return {
            "initialized": self._router_mlp is not None,
            "experts_loaded": list(self._experts.keys()),
            "expert_usage": dict(self._expert_usage),
            "max_active": self.config.max_active_experts,
            "cache_size": len(self._embedding_cache),
            "mlx_available": MLX_AVAILABLE,
        }

    async def get_expert_info(self) -> dict[str, Any]:
        """
        Získat informace o routeru a expertech.

        Returns:
            Dict s informacemi
        """
        return {
            "config": {
                "expert_names": self.config.expert_names,
                "max_active_experts": self.config.max_active_experts,
                "temperature": self.config.temperature,
                "max_tokens_per_expert": self.config.max_tokens_per_expert,
            },
            "loaded_experts": list(self._experts.keys()),
            "expert_usage": self._expert_usage.copy(),
            "embedding_cache_size": len(self._embedding_cache),
            "mlx_available": MLX_AVAILABLE,
        }


# ---------------------------------------------------------------------------
# Sprint 8VH: MoE Router Activation Functions
# ---------------------------------------------------------------------------


def route_synthesis(
    findings_count: int,
    has_gnn: bool,
    memory_pressure: str,
    sprint_query: str,
) -> str:
    """
    Vybírá synthesis engine dle aktuálních podmínek.

    Vrací jeden z: "hermes3", "inference", "heuristic".

    Strategie:
      - critical memory     → "heuristic" (nulový RAM overhead)
      - findings_count < 5  → "heuristic" (málo dat pro LLM)
      - has_gnn            → prefer "hermes3" (richer context)
      - default            → "inference"
    """
    if memory_pressure == "critical":
        return "heuristic"
    if findings_count < 5:
        return "heuristic"
    if has_gnn:
        return "hermes3"
    return "inference"


def route_embedding(memory_pressure: str) -> str:
    """
    Vybírá embedding engine.

    Vrací: "ane_minilm" | "hash_fallback"
    """
    if memory_pressure in ("warn", "critical"):
        return "hash_fallback"
    return "ane_minilm"


# ---------------------------------------------------------------------------
# Original create_moe_router
# ---------------------------------------------------------------------------

async def create_moe_router(config: MoERouterConfig | None = None) -> MoERouter | None:
    """
    Factory funkce pro vytvoření MoE routeru.

    Args:
        config: Volitelná konfigurace

    Returns:
        MoERouter instance nebo None pokud MLX není dostupné
    """
    if not MLX_AVAILABLE:
        logger.warning("MLX not available, MoE router disabled")
        return None

    router = MoERouter(config or MoERouterConfig())
    await router.initialize()
    return router


# ---------------------------------------------------------------------------
# FÁZE P14: Context Intelligence routing
# ---------------------------------------------------------------------------


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

    # Check for image content in query (e.g., <img> tags)
    img_pattern = re.compile(r'<img|image|photo|picture| screenshot', re.IGNORECASE)
    if img_pattern.search(query):
        logger.debug("[MoE] route -> vision (img tag detected)")
        return "vision"

    # Check context for image flags
    if context.get("has_images") or context.get("images"):
        logger.debug("[MoE] route -> vision (images in context)")
        return "vision"

    # Check for PDF or structured data
    content_type = context.get("content_type", "").lower()
    if content_type in ("pdf", "application/pdf", "structured"):
        logger.debug("[MoE] route -> modernbert (structured/PDF)")
        return "modernbert"

    # Check URLs for .pdf extension
    urls = context.get("urls", [])
    for url in urls if isinstance(urls, list) else []:
        if url.endswith(".pdf") or ".pdf?" in str(url):
            logger.debug("[MoE] route -> modernbert (PDF URL)")
            return "modernbert"

    # Memory pressure check: if GPU > 3GB, prefer smaller model
    try:
        import mlx.core as mx
        if hasattr(mx, 'metal') and hasattr(mx.metal, 'get_active_memory'):
            active_bytes = mx.metal.get_active_memory()
            active_gb = active_bytes / (1024**3)
            if active_gb > 3.0:
                # Under memory pressure, use smaller model
                logger.debug(f"[MoE] route -> hermes3 (memory pressure: {active_gb:.1f}GB)")
                return "hermes3"
    except Exception:
        pass

    # Default to hermes3
    logger.debug("[MoE] route -> hermes3 (default)")
    return "hermes3"
