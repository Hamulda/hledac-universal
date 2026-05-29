"""
ArchiveCoordinator - Delegates archive escalation to coordinator
===========================================================

Implements the stable coordinator interface (start/step/shutdown) for:
- Archive escalation triggers
- Memento lookup and recovery
- Deep probe seed generation

This enables the orchestrator to become a thin "spine" that delegates
archive operations to this coordinator.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from typing import Any

from .base import UniversalCoordinator

logger = logging.getLogger(__name__)


# Maximum results per step (bounded output)
MAX_ARCHIVE_RESULTS = 20

# Sprint F206X: Bounded pending URLs to prevent memory exhaustion
MAX_PENDING_URLS = 2000


@dataclass
class ArchiveCoordinatorConfig:
    """Configuration for ArchiveCoordinator."""
    max_escalations_per_step: int = 2
    max_mementos_per_url: int = 5
    max_probe_urls_per_step: int = 20
    enable_memento_lookup: bool = True
    enable_deep_probe: bool = True


class ArchiveCoordinator(UniversalCoordinator):
    """
    Coordinator for archive escalation delegation.

    Responsibilities:
    - Trigger archive escalation for URLs
    - Execute memento lookups
    - Run deep probe for seed generation
    - Return bounded outputs (URLs, metrics)
    """

    def __init__(
        self,
        config: ArchiveCoordinatorConfig | None = None,
        max_concurrent: int = 2,
    ):
        super().__init__(name="ArchiveCoordinator", max_concurrent=max_concurrent)
        self._config = config or ArchiveCoordinatorConfig()

        # State
        # Sprint F206X: deque with maxlen prevents unbounded growth
        self._pending_urls: deque = deque(maxlen=MAX_PENDING_URLS)
        self._seen_urls: set[str] = set()  # O(1) membership test
        self._escalations_executed: int = 0
        self._urls_emitted: int = 0
        self._stop_reason: str | None = None

        # Orchestrator reference (set via start)
        self._orchestrator: Any | None = None
        self._ctx: dict[str, Any] = {}

    def get_supported_operations(self) -> list[Any]:
        """Return supported operation types."""
        from .base import OperationType
        return [OperationType.RESEARCH]

    async def handle_request(
        self,
        operation_ref: str,
        decision: Any
    ) -> Any:
        """
        Handle a decision request (required by UniversalCoordinator base).

        For spine pattern, we use start/step/shutdown instead.
        """
        result = await self.step({'decision': decision})
        return result

    async def _do_initialize(self) -> bool:
        """Initialize coordinator."""
        logger.info("ArchiveCoordinator initialized")
        return True

    async def _do_start(self, ctx: dict[str, Any]) -> None:
        """
        Start coordinator with context from orchestrator.

        Expected ctx keys:
        - pending_urls: List[str] - URLs to process
        - orchestrator: reference to orchestrator instance
        """
        self._ctx = ctx
        self._orchestrator = ctx.get('orchestrator')

        # Load pending URLs if provided
        if 'pending_urls' in ctx:
            # Sprint F206X: Convert to bounded deque, populate seen set for O(1) dedup
            incoming = ctx['pending_urls']
            self._pending_urls = deque(incoming, maxlen=MAX_PENDING_URLS)
            self._seen_urls = set(incoming)

        logger.info(f"ArchiveCoordinator started with {len(self._pending_urls)} pending URLs")

    async def _do_step(self, ctx: dict[str, Any]) -> dict[str, Any]:
        """
        Execute one archive escalation step.

        Process up to max_escalations_per_step from pending URLs.
        Returns bounded output with emitted URLs.
        """
        # Update context
        self._ctx.update(ctx)

        # Add new URLs from ctx
        # Sprint F206X: Use set for O(1) membership, bounded deque auto-evicts
        new_urls = ctx.get('new_urls', [])
        for url in new_urls:
            if url not in self._seen_urls:
                self._seen_urls.add(url)
                self._pending_urls.append(url)

        if not self._pending_urls:
            self._stop_reason = "no_pending_urls"
            return self._get_step_result()

        # Process URLs
        url = self._pending_urls.popleft()
        self._seen_urls.discard(url)  # Sprint F206X: remove from seen when processed

        # Execute archive escalation
        result = await self._execute_archive_escalation(url)

        return self._get_step_result(result)

    def _get_step_result(self, result: dict[str, Any] | None = None) -> dict[str, Any]:
        """Get bounded step result."""
        emitted_urls = result.get('emitted_urls', []) if result else []
        emitted_urls = emitted_urls[:self._config.max_probe_urls_per_step]

        return {
            'escalations_executed': self._escalations_executed,
            'urls_emitted': len(emitted_urls),
            'total_urls_emitted': self._urls_emitted,
            'emitted_urls': emitted_urls,
            'stop_reason': self._stop_reason,
            'pending_urls': len(self._pending_urls),
        }

    async def _execute_archive_escalation(self, url: str) -> dict[str, Any] | None:
        """
        Execute archive escalation for a URL.

        Delegates to orchestrator's archive escalation and deep probe methods.
        """
        if not self._orchestrator:
            logger.warning(f"ArchiveCoordinator: no orchestrator reference for {url}")
            return None

        try:
            emitted_urls = []
            memento_count = 0

            # Execute memento lookup if enabled
            if self._config.enable_memento_lookup:
                mementos = await self._lookup_mementos(url)
                memento_count = len(mementos)
                emitted_urls.extend([m.get('url', '') for m in mementos if m.get('url')])

            # Execute deep probe if enabled
            if self._config.enable_deep_probe:
                probe_urls = await self._run_deep_probe(url)
                emitted_urls.extend(probe_urls)

            # Bound output
            emitted_urls = list(set(emitted_urls))[:self._config.max_probe_urls_per_step]
            self._urls_emitted += len(emitted_urls)
            self._escalations_executed += 1

            return {
                'url': url,
                'emitted_urls': emitted_urls,
                'memento_count': memento_count,
                'probe_count': len(emitted_urls) - memento_count,
            }

        except Exception as e:
            logger.warning(f"ArchiveCoordinator: failed to execute escalation: {e}")
            return None

    async def _lookup_mementos(self, url: str) -> list[dict[str, Any]]:
        """Lookup mementos for URL via orchestrator."""
        try:
            if hasattr(self._orchestrator, 'trigger_archive_escalation'):
                result = await self._orchestrator.trigger_archive_escalation(url)
                return result.get('mementos', [])[:self._config.max_mementos_per_url]
            return []
        except Exception as e:
            logger.debug(f"ArchiveCoordinator: memento lookup failed: {e}")
            return []

    async def _run_deep_probe(self, url: str) -> list[str]:
        """Run deep probe for URL via orchestrator."""
        try:
            if hasattr(self._orchestrator, '_maybe_trigger_deep_probe'):
                result = await self._orchestrator._maybe_trigger_deep_probe(
                    reason="spine_delegation",
                    target_url=url
                )
                if result:
                    return result.get('discovered_urls', [])[:self._config.max_probe_urls_per_step]
            return []
        except Exception as e:
            logger.debug(f"ArchiveCoordinator: deep probe failed: {e}")
            return []

    async def _do_shutdown(self, ctx: dict[str, Any]) -> None:
        """Cleanup on shutdown."""
        logger.info(f"ArchiveCoordinator shutting down: {self._escalations_executed} escalations, {self._urls_emitted} URLs")
        self._pending_urls.clear()
        self._seen_urls.clear()  # Sprint F206X: clear seen set too
