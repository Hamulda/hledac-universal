"""
runtime/protocols/brain_protocol.py — F270: Brain Interface
=========================================================

Protocol for MLX/LLM inference (Hermes3, synthesis, hypothesis).
Extracted from SprintScheduler's BRAIN group (~9 attributes).

GHOST_INVARIANTS:
- Fail-safe: generate returns "" on error
- Bounded: kv_cache_size, max_tokens limits enforced
"""



from typing import Any, Protocol, runtime_checkable
from core import aclose


@runtime_checkable
class BrainProtocol(Protocol):
    """
    LLM inference and synthesis protocol.

    Implementations:
        - Hermes3EngineAdapter: wraps Hermes3Engine
        - SynthesisRunnerAdapter: wraps SynthesisRunner

    Key methods:
        - generate: LLM inference
        - synthesize: multi-finding synthesis
    """

    async def generate(
        self,
        prompt: str,
        max_tokens: int = 512,
        **kwargs: Any,
    ) -> str:
        """Generate LLM response."""
        ...

    async def synthesize(
        self, findings: list[Any], query: str
    ) -> dict[str, Any] | None:
        """Synthesize findings into structured report."""
        ...

    def score_ioc(self, ioc_value: str, ioc_type: str) -> float:
        """Return IOC novelty score (0.0-1.0)."""
        ...
