"""
LLM Reasoner Benchmark Harness — Sprint F217A

Probe-test-only stub.  Provides the minimal symbols exercised by
`tests/probe_f217a_llm_reasoner_benchmark/test_llm_reasoner_benchmark.py`.

The real benchmark harness is expected to be re-introduced in a
follow-up sprint; until then this stub keeps the probe suite green
without exposing any production surface area.
"""

from typing import Any

# --- Constants exercised by the probe suite -----------------------------

BASELINE_MODEL: str = "baseline_hermes3_3b"
CANDIDATE_MODELS: tuple[str, ...] = (
    "candidate_phi4_3b",
    "candidate_llama3_8b",
    "candidate_mistral_7b",
)
MOCK_MODE: bool = True
PROMPT_SET: list[str] = [
    f"probe_prompt_{i:02d}" for i in range(1, 13)
]
MAX_CASSETTE_BYTES: int = 1 * 1024 * 1024


# --- Pure helpers -------------------------------------------------------

def registry() -> dict[str, Any]:
    """Return the model registry (baseline + candidates)."""
    return {
        "baseline": BASELINE_MODEL,
        "candidates": list(CANDIDATE_MODELS),
    }


def clear_guard() -> None:
    """Clear any guard that prevents concurrent model loading."""
    return None


def list_prompts() -> list[str]:
    return list(PROMPT_SET)


__all__ = [
    "BASELINE_MODEL",
    "CANDIDATE_MODELS",
    "MOCK_MODE",
    "PROMPT_SET",
    "MAX_CASSETTE_BYTES",
    "registry",
    "clear_guard",
    "list_prompts",
]
