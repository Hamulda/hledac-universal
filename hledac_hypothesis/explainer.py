"""
Hypothesis Engine — Simple Node Ablation Explainer (C4 Sprint Refactoring)
==========================================================================


Extracted from :mod:`brain.hypothesis_engine_engine` to break the 5 373 LOC monolith
into focused modules. This module hosts:

- :class:`SimpleNodeAblationExplainer` — the leave-one-node-out path
  importance explainer used by the AdversarialVerifier.
- :func:`explain_with_mlx` — module-level MLX-LM helper for generating
  textual explanations (Sprint 67, **moved here in C4 Tier-5**).

GHOST_INVARIANTS:
- The extraction is **byte-for-byte identical** to the original — no
  behaviour change, no field rename, no default mutation. Existing tests
  must pass unchanged.
- ``brain.hypothesis_engine_engine`` re-exports both symbols
  (:class:`SimpleNodeAblationExplainer` and ``explain_with_mlx``) for
  backward compat.
- New code should
  ``from brain.hypothesis_engine.explainer import SimpleNodeAblationExplainer, explain_with_mlx``.
- Zero dependency on :mod:`brain.hypothesis_engine_engine` types — the class
  is graph-RAG-agnostic and operates on duck-typed ``graph_rag.score_path``
  / ``graph_rag._get_embedder`` interfaces.
- ``explain_with_mlx`` is M1-safe: it loads the MLX model lazily, uses
  ``asyncio.wait_for`` with a 10s timeout, and returns fail-soft tuples
  on any error. The helper never raises.
"""

import asyncio
import hashlib
import logging

logger = logging.getLogger(__name__)


class SimpleNodeAblationExplainer:
    """
    Explains path importance using leave-one-node-out ablation.

    Computes importance scores by removing each node and measuring
    the change in path score from graph_rag.
    """

    __slots__ = ("graph_rag",)

    def __init__(self, graph_rag) -> None:
        """
        Initialize explainer.

        Args:
            graph_rag: GraphRAGOrchestrator instance with score_path method
        """
        self.graph_rag = graph_rag

    async def explain_path(self, path: list[str], hypothesis: str, max_nodes: int = 5) -> dict[str, float]:
        """
        Explain path importance using node ablation.

        Args:
            path: List of node IDs forming the path
            hypothesis: The hypothesis to score against
            max_nodes: Maximum nodes to ablate

        Returns:
            Dict mapping node index to importance score
        """
        if len(path) < 2:
            return {}
        embedder = await self.graph_rag._get_embedder()
        if embedder is None:
            return {}
        try:
            hypothesis_emb = await embedder._embed_text(hypothesis)
            if hypothesis_emb is None:
                hypothesis_emb = [0.0] * 384
        except Exception:
            hypothesis_emb = [0.0] * 384
        n_nodes = min(len(path), max_nodes)
        try:
            original_score = await self.graph_rag.score_path(path, hypothesis, hypothesis_emb=hypothesis_emb)
        except Exception:
            return {}
        ablation_paths = []
        ablation_indices = []
        for i in range(n_nodes):
            if i == 0 or i >= len(path) - 1:
                continue
            new_path = path[:i] + path[i + 1 :]
            ablation_paths.append(new_path)
            ablation_indices.append(i)
        if not ablation_paths:
            return {}
        try:
            ablation_scores = await self.graph_rag.score_paths_parallel(ablation_paths, hypothesis, max_nodes=max_nodes)
        except Exception:
            from hledac.universal.utils.asyncx import parallel_ok

            ablation_tasks = [
                self.graph_rag.score_path(p, hypothesis, hypothesis_emb=hypothesis_emb) for p in ablation_paths
            ]
            ablation_scores = await parallel_ok(*ablation_tasks, label="explain_path:fallback")
        importances = {}
        for idx, score_result in zip(ablation_indices, ablation_scores, strict=False):
            if isinstance(score_result, Exception):
                continue
            try:
                importances[str(idx)] = original_score - float(score_result)
            except TypeError, ValueError:
                continue
        if all(v <= 0.0 for v in importances.values()):
            return {}
        return importances


async def explain_with_mlx(
    hypothesis: str, path: list[str], model_name: str = "mlx-community/Qwen2.5-0.5B-Instruct-4bit"
) -> tuple[str, str]:
    """
    Generate textual explanation using MLX-LM.

    Args:
        hypothesis: The hypothesis
        path: Graph path
        model_name: Model identifier

    Returns:
        Tuple of (explanation, prompt_hash)
    """
    try:
        from hledac.universal.utils.mlx_cache import get_mlx_model, get_mlx_semaphore

        model, tokenizer = await get_mlx_model(model_name)
        if model is None or tokenizer is None:
            return ("MLX model unavailable", "")
        prompt = f"Explain why this path in a knowledge graph is important for the hypothesis: '{hypothesis}'. Path: {' -> '.join(path)}"
        from mlx_lm import generate

        loop = asyncio.get_running_loop()
        async with get_mlx_semaphore():
            try:
                async with asyncio.timeout(10.0):
                    explanation = await loop.run_in_executor(
                        None, lambda: generate(model, tokenizer, prompt, max_tokens=80, temp=0.0)
                    )
            except TypeError:
                async with asyncio.timeout(10.0):
                    explanation = await loop.run_in_executor(
                        None, lambda: generate(model, tokenizer, prompt, max_tokens=80)
                    )
        prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()[:8]
        return (explanation.strip(), prompt_hash)
    except TimeoutError:
        return ("Explanation generation timed out", "")
    except Exception as e:
        logger.debug(f"MLX explanation failed: {e}")
        return (f"Generation failed: {e}", "")
