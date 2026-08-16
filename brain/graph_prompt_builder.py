"""
SOVEREIGN-002: Graph-to-ChatML prompt conversion with token budgeting.

Converts graph RAG results into ChatML-formatted context suitable for
injection into DeepHermes3 prompts. Uses tiktoken for accurate token
counting and budget management.

M1 8GB optimized:
- Lazy tokenizer loading (no model load at import time)
- Token budgeting prevents context window overflow
- Progressive truncation: facts -> paths -> metadata
"""
from __future__ import annotations

import logging
from typing import Any
from _core import aclose

logger = logging.getLogger(__name__)

# Token budget constants
DEFAULT_TOKEN_BUDGET = 2048  # Conservative budget for graph context
MAX_FACTS = 20  # Hard limit on facts to include
MAX_PATHS = 10  # Hard limit on paths to include

# Lazy-loaded tokenizer
_tokenizer = None


def _get_tokenizer():
    """Lazy-load tiktoken encoder for token counting."""
    global _tokenizer
    if _tokenizer is None:
        try:
            import tiktoken
            # Use cl100k_base (GPT-4/Claude compatible)
            _tokenizer = tiktoken.get_encoding("cl100k_base")
        except ImportError:
            logger.warning("tiktoken not available, using char-based estimation")
            _tokenizer = None
    return _tokenizer


def count_tokens(text: str) -> int:
    """Count tokens in text using tiktoken or char-based fallback."""
    tokenizer = _get_tokenizer()
    if tokenizer is not None:
        return len(tokenizer.encode(text))
    # Fallback: ~4 chars per token (conservative estimate)
    return len(text) // 4


def truncate_to_budget(text: str, max_tokens: int) -> str:
    """Truncate text to fit within token budget."""
    tokenizer = _get_tokenizer()
    if tokenizer is not None:
        tokens = tokenizer.encode(text)
        if len(tokens) > max_tokens:
            return tokenizer.decode(tokens[:max_tokens])
    else:
        # Fallback: char-based truncation
        max_chars = max_tokens * 4
        if len(text) > max_chars:
            return text[:max_chars]
    return text


def build_graph_chatml_context(
    graph_result: dict[str, Any],
    query: str,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
) -> str:
    """
    Convert graph RAG result into ChatML-formatted context string.

    Progressive truncation strategy:
    1. Start with top facts (highest similarity)
    2. Add path evidence if budget allows
    3. Add metadata (contested, narratives) last
    4. Truncate individual items if still over budget

    Args:
        graph_result: Output from graph_rag.multi_hop_search()
        query: Original query string
        token_budget: Maximum tokens for graph context (default: 2048)

    Returns:
        ChatML-formatted string ready for injection into system prompt
    """
    if not graph_result:
        return ""

    # Extract components with priority ordering
    insights = graph_result.get("insights", [])[:MAX_FACTS]
    paths = graph_result.get("paths", [])[:MAX_PATHS]
    summary_text = graph_result.get("summary_text", "")
    contested = graph_result.get("contested", False)
    counter_paths = graph_result.get("counter_paths", [])[:5]
    narratives = graph_result.get("narratives", [])[:3]

    # Build context sections with token tracking
    sections = []
    used_tokens = 0

    # 1. Summary (highest priority, always include if exists)
    summary_section, summary_tokens = _build_summary_section(summary_text, used_tokens, token_budget)
    if summary_section:
        sections.append(summary_section)
        used_tokens += summary_tokens

    # 2. Key insights (high priority, truncate if needed)
    insights_section, insights_tokens = _build_insights_section(insights, used_tokens, token_budget)
    if insights_section:
        sections.append(insights_section)
        used_tokens += insights_tokens

    # 3. Path evidence (medium priority)
    if paths and used_tokens < token_budget * 0.8:  # Only if we have 20% budget left
        paths_section, paths_tokens = _build_paths_section(paths, used_tokens, token_budget)
        if paths_section:
            sections.append(paths_section)
            used_tokens += paths_tokens

    # 4. Contested information (low priority, only if budget allows)
    if contested and used_tokens < token_budget * 0.9:
        contested_section, contested_tokens = _build_contested_section(
            counter_paths, narratives, used_tokens, token_budget
    )
        if contested_section:
            sections.append(contested_section)
            used_tokens += contested_tokens

    # Combine all sections
    if not sections:
        return ""

    full_context = "\n\n".join(sections)

    # Final token check
    final_tokens = count_tokens(full_context)
    if final_tokens > token_budget:
        full_context = truncate_to_budget(full_context, token_budget)
        logger.debug(f"Graph context truncated from {final_tokens} to {token_budget} tokens")

    # Wrap in ChatML system context format
    chatml_context = f"<|im_start|>system\n{full_context}<|im_end|>"

    logger.debug(f"Built graph ChatML context: {count_tokens(chatml_context)} tokens, {len(sections)} sections")
    return chatml_context


# ------------------------------------------------------------------
# Complexity-reduced section builders (complexity: 25 → ~10)
# ------------------------------------------------------------------

def _build_summary_section(
    summary_text: str, used_tokens: int, token_budget: int
) -> tuple[str, int]:
    """Build summary section if within budget."""
    if not summary_text:
        return "", 0
    summary_section = f"## Graph Summary\n{summary_text}"
    summary_tokens = count_tokens(summary_section)
    if used_tokens + summary_tokens <= token_budget:
        return summary_section, summary_tokens
    return "", 0


def _build_insights_section(
    insights: list[Any], used_tokens: int, token_budget: int
) -> tuple[str, int]:
    """Build key insights section with progressive truncation."""
    if not insights:
        return "", 0
    insight_lines = []
    for insight in insights:
        content = insight.get("content", "") if isinstance(insight, dict) else str(insight)
        if content:
            insight_lines.append(f"- {content[:200]}")

    if not insight_lines:
        return "", 0

    insights_section = "## Key Findings\n" + "\n".join(insight_lines)
    insights_tokens = count_tokens(insights_section)

    remaining_budget = token_budget - used_tokens
    if insights_tokens > remaining_budget:
        max_insight_tokens = max(100, remaining_budget // 2)
        insights_section = truncate_to_budget(insights_section, max_insight_tokens)
        insights_tokens = count_tokens(insights_section)

    if used_tokens + insights_tokens <= token_budget:
        return insights_section, insights_tokens
    return "", 0


def _build_paths_section(
    paths: list[Any], used_tokens: int, token_budget: int
) -> tuple[str, int]:
    """Build evidence paths section."""
    if not paths:
        return "", 0
    path_lines = []
    for path in paths[:5]:
        nodes = path.get("nodes", []) if isinstance(path, dict) else []
        if nodes:
            path_str = " -> ".join(str(n)[:30] for n in nodes[:5])
            path_lines.append(f"- {path_str}")

    if not path_lines:
        return "", 0

    paths_section = "## Evidence Paths\n" + "\n".join(path_lines)
    paths_tokens = count_tokens(paths_section)

    remaining_budget = token_budget - used_tokens
    if paths_tokens <= remaining_budget:
        return paths_section, paths_tokens
    # Truncate paths to fit
    paths_section = truncate_to_budget(paths_section, remaining_budget)
    return paths_section, count_tokens(paths_section)


def _build_contested_section(
    counter_paths: list[Any], narratives: list[Any], used_tokens: int, token_budget: int
) -> tuple[str, int]:
    """Build contested information section with counter-paths and narratives."""
    contested_parts = ["## Contested Information"]
    if counter_paths:
        contested_parts.append("Alternative perspectives found:")
        for cp in counter_paths[:3]:
            content = cp.get("content", "") if isinstance(cp, dict) else str(cp)
            if content:
                contested_parts.append(f"- {content[:150]}")

    if narratives:
        contested_parts.append("\nCompeting narratives:")
        for narrative in narratives[:2]:
            content = narrative.get("content", "") if isinstance(narrative, dict) else str(narrative)
            if content:
                contested_parts.append(f"- {content[:150]}")

    contested_section = "\n".join(contested_parts)
    contested_tokens = count_tokens(contested_section)

    remaining_budget = token_budget - used_tokens
    if contested_tokens <= remaining_budget:
        return contested_section, contested_tokens
    return "", 0


def inject_graph_context(
    system_prompt: str,
    graph_result: dict[str, Any],
    query: str,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
) -> str:
    """
    Inject graph RAG context into existing system prompt.

    Args:
        system_prompt: Base system prompt
        graph_result: Output from graph_rag.multi_hop_search()
        query: Original query string
        token_budget: Maximum tokens for graph context

    Returns:
        Enhanced system prompt with graph context injected
    """
    graph_context = build_graph_chatml_context(graph_result, query, token_budget)
    if not graph_context:
        return system_prompt

    # Insert graph context after system prompt header
    # Format: system_prompt + \n\n + graph_context
    enhanced_prompt = f"{system_prompt}\n\n{graph_context}"

    total_tokens = count_tokens(enhanced_prompt)
    logger.debug(f"Enhanced system prompt with graph context: {total_tokens} tokens total")

    return enhanced_prompt
