"""
FÁZE P14: Active Learning Loop for OSINT Discovery

This module implements an active learning step that uses Hermes 3 to suggest
new search queries/dorks for deeper research, filters duplicates, and runs
discovery to find new URLs.

Features:
- Hermes 3 query suggestion via model_lifecycle() memory guard
- Duplicate filtering via set-based dedup
- Bounded queue (max 50 items)
- Max 3 learning steps per session

Anti-patterns enforced:
- Uses model_lifecycle() for memory guard before LLM calls
- async-only, never sync in async pipeline
- Limited iterations and queue size
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["active_learning_step"]

# Bounded: max steps and queue size as per spec
_MAX_LEARNING_STEPS: int = 3
_MAX_QUEUE_SIZE: int = 50


async def active_learning_step(
    query: str,
    current_results: list[str],
) -> list[str]:
    """
    FÁZE P14: Active learning step that suggests new dorks and runs discovery.

    Uses Hermes 3 to generate new search queries based on current results,
    filters duplicates against existing URLs, and runs discovery to find new URLs.

    Args:
        query: Original research query
        current_results: List of current result URLs to avoid duplicating

    Returns:
        List of new URLs discovered through active learning

    Raises:
        No exceptions - fail-soft design, returns empty list on any failure
    """
    if not query or not current_results:
        return []

    # Bounded: limit to _MAX_LEARNING_STEPS
    step_count = 0
    new_urls: list[str] = []
    seen_urls: set[str] = set(current_results[:_MAX_QUEUE_SIZE])  # Limit initial set

    try:
        # Import here to avoid circular dependencies
        from hledac.universal.brain.hermes3_engine import Hermes3Engine
        from hledac.universal.brain.model_lifecycle import model_lifecycle
        from hledac.universal.discovery.duckduckgo_adapter import async_search_public_web

        # Create Hermes engine with memory guard via model_lifecycle
        async with model_lifecycle("hermes3"):
            hermes = Hermes3Engine()

            while step_count < _MAX_LEARNING_STEPS:
                step_count += 1
                logger.info(f"[P14] Active learning step {step_count}/{_MAX_LEARNING_STEPS}")

                # Build prompt for query suggestion
                context_for_prompt = "\n".join(current_results[:10])  # First 10 for context
                prompt = f"""Navrhni nové vyhledávací dotazy pro hlubší výzkum tématu: {query}

Kontext aktuálních výsledků:
{context_for_prompt}

Navrhni 3-5 specifických vyhledávacích dotazů (dorks) které by odhalily více informací.
Formát: jeden dotaz na řádek, bez čísel nebo odrážek."""

                try:
                    # Generate new dorks using Hermes
                    response = await hermes.generate(prompt, max_tokens=512)

                    if not response:
                        logger.warning(f"[P14] Step {step_count}: empty response from Hermes")
                        break

                    # Parse suggested queries (one per line)
                    suggested_queries = [
                        line.strip()
                        for line in response.strip().split("\n")
                        if line.strip() and not line.strip().startswith("#")
                    ]

                    logger.info(f"[P14] Suggested queries: {suggested_queries[:3]}")

                    # Run discovery for each suggested query (bounded)
                    for sq in suggested_queries[:3]:  # Max 3 queries per step
                        if len(new_urls) >= _MAX_QUEUE_SIZE:
                            break

                        try:
                            discovery_result = await async_search_public_web(sq, max_results=5)
                            if hasattr(discovery_result, "hits"):
                                hits = discovery_result.hits
                            elif isinstance(discovery_result, dict):
                                hits = discovery_result.get("hits", [])
                            else:
                                hits = []

                            # Extract URLs, filter duplicates
                            for hit in hits:
                                url = getattr(hit, 'url', '') or str(getattr(hit, 'url', ''))
                                if url and url not in seen_urls:
                                    seen_urls.add(url)
                                    new_urls.append(url)

                        except Exception as e:
                            logger.warning(f"[P14] Discovery failed for query '{sq}': {e}")
                            continue

                except Exception as e:
                    logger.warning(f"[P14] Step {step_count}: Hermes generation failed: {e}")
                    continue

                # Check if we've reached queue limit
                if len(new_urls) >= _MAX_QUEUE_SIZE:
                    break

        logger.info(f"[P14] Active learning complete: {len(new_urls)} new URLs")
        return new_urls[:_MAX_QUEUE_SIZE]

    except Exception as e:
        logger.warning(f"[P14] Active learning failed: {e}")
        return new_urls[:_MAX_QUEUE_SIZE]


async def _ASYNC_DISCOVERY_SEARCH(query: str, max_results: int) -> Any:
    """
    Internal async discovery search wrapper.

    Used by active_learning_step to run discovery searches.
    """
    from hledac.universal.discovery.duckduckgo_adapter import async_search_public_web

    try:
        result = await async_search_public_web(query, max_results=max_results)
        return result
    except Exception as e:
        logger.warning(f"[_ASYNC_DISCOVERY_SEARCH] Failed: {e}")
        return type('obj', (object,), {'hits': []})()
