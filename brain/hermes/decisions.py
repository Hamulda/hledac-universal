"""
brain/hermes/decisions.py — Decision Engine
==========================================

PEP 698: Extracted from brain/deephermes3_engine.py.

Handles:
- Research flow decision making
- Triage mode for relevance scoring
- Action selection based on context

M1 8GB: Uses structured output for deterministic decisions.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import msgspec

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class DecisionOutput(msgspec.Struct, frozen=True):
    """
    Structured decision output from LLM-based decision engine.

    PEP 698: Extracted from _DecisionOutput in deephermes3_engine.py.
    """

    action: str
    params: dict[str, Any]
    reasoning: str
    complete: bool = False


# System prompts for decision making
_RESEARCH_ORCHESTRATOR_PROMPT = """You are a research orchestrator. Decide the next action to progress the research.

Available actions:
- search: Search for information
- google: Google search
- download: Download a file
- deep_read: Read content from URL (secure)
- research_paper: Search academic papers
- osint_discovery: Discover hidden sources
- archive_fallback: Check Wayback Machine
- fact_check: Verify a claim
- synthesize: Complete research and synthesize findings

Respond in JSON format:
{{
  "action": "action_name",
  "params": {{"key": "value"}},
  "reasoning": "why this action",
  "complete": false
}}

Set "complete": true when research is sufficiently comprehensive."""


async def decide_next_action(
    engine,
    context: dict[str, Any],
) -> dict[str, Any]:
    """
    Decide the next action in research flow.

    Args:
        engine: DeepHermes3Engine instance with generate_structured
        context: Research context with query, step, history, etc.

    Returns:
        Decision dictionary with action, params, reasoning, complete
    """
    query = context.get("query", "")
    step = context.get("step", 0)
    max_steps = context.get("max_steps", 20)
    history = context.get("history", [])

    # Prepare history string
    from hledac.universal.utils.msgspec_json import encode_fast as _msgspec_encode_fast

    history_str = _msgspec_encode_fast(history[-3:]).decode() if history else "No previous actions"

    prompt = f"""Research query: {query}
Step: {step}/{max_steps}

History:
{history_str}

What should be the next action?"""

    decision_model = await engine.generate_structured(
        prompt,
        DecisionOutput,
        system_msg=_RESEARCH_ORCHESTRATOR_PROMPT,
        temperature=0.2,
    )

    return msgspec.to_dict(decision_model)


class TriageMode:
    """
    Triage mode for relevance scoring.

    PEP 698: Extracted from triage_mode and related methods.
    """

    # Thresholds for M1 8GB constraints
    DEFAULT_THRESHOLD = 0.3
    HIGH_PRIORITY_THRESHOLD = 0.7

    @staticmethod
    def is_enabled(engine) -> bool:
        """Check if triage mode is enabled."""
        return getattr(engine, "_triage_mode", False)

    @staticmethod
    def enable(engine) -> None:
        """Enable triage mode."""
        engine._triage_mode = True
        logger.debug("[TRIAGE] Mode enabled")

    @staticmethod
    def disable(engine) -> None:
        """Disable triage mode."""
        engine._triage_mode = False
        logger.debug("[TRIAGE] Mode disabled")

    @staticmethod
    async def score_relevance(
        engine,
        item: dict[str, Any],
        threshold: float = DEFAULT_THRESHOLD,
    ) -> bool:
        """
        Score and filter items by relevance.

        Args:
            engine: DeepHermes3Engine instance
            item: Item to score (dict with 'content', 'url', etc.)
            threshold: Minimum relevance threshold

        Returns:
            True if item is relevant enough
        """
        content = item.get("content", item.get("snippet", ""))
        url = item.get("url", "")

        if not content:
            return False

        # Quick heuristic for very short content
        if len(content) < 50:
            return False

        # For now, use simple length-based scoring
        # In production, could use embeddings or LLM scoring
        base_score = min(1.0, len(content) / 500)

        # URL quality signals
        if url:
            quality_signals = sum(
                [
                    "github" in url,  # Quality source
                    "arxiv" in url,
                    "wikipedia" in url,
                    not url.endswith(".pdf"),  # Harder to process
                ]
            )
            base_score += quality_signals * 0.1

        return base_score >= threshold


# Python 3.14+ match statement support for action routing
def route_action(action: str, params: dict[str, Any]) -> str:
    """
    Route decision action to appropriate handler.

    PEP 698: Uses Python 3.14+ match statement for clean action routing.

    Args:
        action: Action name
        params: Action parameters

    Returns:
        Handler identifier
    """
    match action:
        case "search":
            return f"search:{params.get('query', '')}"
        case "google":
            return f"google:{params.get('query', '')}"
        case "deep_read":
            return f"deep_read:{params.get('url', '')}"
        case "synthesize":
            return "synthesize"
        case "fact_check":
            return f"fact_check:{params.get('claim', '')}"
        case "archive_fallback":
            return f"archive:{params.get('url', '')}"
        case _:
            logger.warning(f"[DECISION] Unknown action: {action}")
            return f"unknown:{action}"


def create_default_context() -> dict[str, Any]:
    """
    Create default context for decision engine.

    Returns:
        Default context dictionary
    """
    return {
        "query": "",
        "step": 0,
        "max_steps": 20,
        "history": [],
        "findings": [],
        "hypotheses": [],
    }
