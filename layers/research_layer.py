"""
Research Layer - GhostDirector and Deep Research Integration
============================================================

Integrates:
- GhostDirector: 18+ actions, OODA loop, autonomous investigation
- ResearchDepthMaximizer: 10-level deep research, citation following
- Hunter: DuckDuckGo search, Trafilatura extraction

This is a thin wrapper that imports existing research modules
and adds integration logic for the universal orchestrator.
"""

from __future__ import annotations

import logging
from typing import Any

from hledac.universal.project_types import (
    DeepResearchConfig,
    ExplorationNode,
    ExplorationStrategy,
    GhostMission,
)

logger = logging.getLogger(__name__)


class ResearchLayer:
    """
    Research layer for deep investigation with GhostDirector and depth maximization.

    This layer:
    1. Manages GhostDirector for autonomous actions (can be shared from LayerManager)
    2. Performs 10-level deep research with citation following
    3. Hunts for URLs and extracts content
    4. Explores tangential topics

    Example:
        research = ResearchLayer(config)
        await research.initialize()

        # Start Ghost mission
        mission = await research.create_mission("Investigate quantum computing")
        result = await research.execute_mission(mission)

        # Deep research
        exploration = await research.deep_explore(
            "https://example.com/paper",
            strategy=ExplorationStrategy.CITATION_FOLLOWING
        )
    """

    def __init__(self, config: DeepResearchConfig | None = None, ghost_director: Any | None = None):
        """
        Initialize ResearchLayer.

        Args:
            config: Deep research configuration (uses defaults if None)
            ghost_director: Optional shared GhostDirector instance from LayerManager
                           (prevents duplicate initialization on M1 8GB)
        """
        self.config = config or DeepResearchConfig()

        # Core components (lazy loaded)
        # GhostDirector can be shared from LayerManager to prevent duplicate init
        self._ghost_director = ghost_director
        self._ghost_director_shared = ghost_director is not None
        self._depth_maximizer = None
        self._hunter = None

        # Mission tracking
        self._missions: dict[str, GhostMission] = {}
        self._explorations: dict[str, list[ExplorationNode]] = {}

        # Statistics
        self._missions_completed = 0
        self._actions_executed = 0
        self._depth_levels_reached = 0

        logger.info(f"ResearchLayer initialized (GhostDirector: {'shared' if self._ghost_director_shared else 'lazy'})")

    async def initialize(self) -> bool:
        """
        Initialize ResearchLayer components.

        Returns:
            True if initialization successful
        """
        try:
            logger.info("🚀 Initializing ResearchLayer...")

            # Initialize Hunter (lightweight, do first)
            await self._init_hunter()

            # Initialize GhostDirector (heavy, lazy)
            # Will be initialized on first use

            # Initialize DepthMaximizer (medium)
            await self._init_depth_maximizer()

            logger.info("✅ ResearchLayer initialized successfully")
            return True

        except Exception as e:
            logger.error(f"❌ ResearchLayer initialization failed: {e}")
            return False

    async def _init_ghost_director(self) -> None:
        """Lazy initialization of GhostDirector (only if not shared)"""
        # Skip if GhostDirector was provided by LayerManager (shared instance)
        if self._ghost_director_shared and self._ghost_director is not None:
            logger.debug("Using shared GhostDirector from LayerManager")
            return

        if self._ghost_director is None:
            try:
                from hledac.cortex.director import GhostDirector

                self._ghost_director = GhostDirector(
                    max_steps=20,
                    # ctx and vault will be passed during execution
                )
                await self._ghost_director.initialize_drivers()
                logger.info("✅ GhostDirector initialized (local)")

            except ImportError as e:
                logger.warning(f"⚠️ GhostDirector not available: {e}")
                self._ghost_director = None

    async def _init_depth_maximizer(self) -> None:
        """Lazy initialization of ResearchDepthMaximizer"""
        if self._depth_maximizer is None:
            try:
                from hledac.research.depth_maximizer import ResearchDepthMaximizer

                self._depth_maximizer = ResearchDepthMaximizer(
                    max_depth=self.config.max_depth,
                    strategy=self.config.strategy
                )
                await self._depth_maximizer.start()
                logger.info("✅ ResearchDepthMaximizer initialized")

            except ImportError as e:
                logger.warning(f"⚠️ ResearchDepthMaximizer not available: {e}")
                self._depth_maximizer = None

    async def _init_hunter(self) -> None:
        """Lazy initialization of Hunter"""
        if self._hunter is None:
            try:
                from hledac.cortex.hunter import Hunter

                self._hunter = Hunter()
                await self._hunter.initialize()
                logger.info("✅ Hunter initialized")

            except ImportError as e:
                logger.warning(f"⚠️ Hunter not available: {e}")
                self._hunter = None

    def create_mission(self, goal: str) -> GhostMission:
        """
        Create a new GhostDirector mission.

        Args:
            goal: Mission goal/description

        Returns:
            GhostMission
        """
        import uuid
        mission_id = str(uuid.uuid4())[:8]

        mission = GhostMission(
            mission_id=mission_id,
            goal=goal,
            actions=[],  # Will be populated by GhostDirector
            current_step=0,
            acquired_loot=[],
            anti_loop_counter=0
        )

        self._missions[mission_id] = mission
        logger.info(f"🎯 Mission created: {mission_id} - {goal[:50]}...")
        return mission

    async def execute_mission(
        self,
        mission: GhostMission,
        max_steps: int | None = None
    ) -> dict[str, Any]:
        """
        Execute a GhostDirector mission.

        Args:
            mission: GhostMission to execute
            max_steps: Maximum steps (uses config default if None)

        Returns:
            Mission results
        """
        if self._ghost_director is None:
            await self._init_ghost_director()

        if self._ghost_director is None:
            logger.error("❌ GhostDirector not available")
            return {"success": False, "error": "GhostDirector not available"}

        max_steps = max_steps or 20

        logger.info(f"🚀 Executing mission: {mission.mission_id}")

        try:
            # Start investigation via GhostDirector
            result = await self._ghost_director.start_investigation(mission.goal)

            self._missions_completed += 1
            self._actions_executed += result.get("actions_count", 0)

            # Update mission with results
            mission.acquired_loot = result.get("loot", [])

            return {
                "success": True,
                "mission_id": mission.mission_id,
                "goal": mission.goal,
                "actions_executed": result.get("actions_count", 0),
                "loot_count": len(mission.acquired_loot),
                "findings": result.get("findings", []),
                "duration": result.get("duration", 0),
            }

        except Exception as e:
            logger.error(f"❌ Mission execution failed: {e}")
            return {
                "success": False,
                "mission_id": mission.mission_id,
                "error": str(e)
            }

    async def deep_explore(
        self,
        start_url: str,
        strategy: ExplorationStrategy | None = None,
        max_depth: int | None = None
    ) -> list[ExplorationNode]:
        """
        Perform deep research exploration.

        Args:
            start_url: Starting URL
            strategy: Exploration strategy (uses config default if None)
            max_depth: Maximum depth (uses config default if None)

        Returns:
            List of ExplorationNodes
        """
        if self._depth_maximizer is None:
            await self._init_depth_maximizer()

        if self._depth_maximizer is None:
            logger.warning("⚠️ ResearchDepthMaximizer not available, using fallback")
            return await self._fallback_exploration(start_url, max_depth)

        strategy = strategy or ExplorationStrategy(self.config.strategy)
        max_depth = max_depth or self.config.max_depth

        logger.info(f"🔍 Deep exploration: {start_url} (strategy: {strategy.value}, max_depth: {max_depth})")

        try:
            # Start deep research thread
            result = await self._depth_maximizer.start_deep_research(
                query=start_url,
                strategy=strategy.value
            )

            # Convert to ExplorationNodes
            nodes = []
            for item in result.get("explored", []):
                node = ExplorationNode(
                    node_id=item.get("id", ""),
                    url=item.get("url", ""),
                    title=item.get("title", ""),
                    depth=item.get("depth", 0),
                    parent_id=item.get("parent"),
                    children=item.get("children", []),
                    citations=item.get("citations", []),
                    quality_score=item.get("quality", 0.0)
                )
                nodes.append(node)

            self._depth_levels_reached = max(n.depth for n in nodes) if nodes else 0

            logger.info(f"✅ Deep exploration complete: {len(nodes)} nodes, depth {self._depth_levels_reached}")
            return nodes

        except Exception as e:
            logger.error(f"❌ Deep exploration failed: {e}")
            return await self._fallback_exploration(start_url, max_depth)

    async def _fallback_exploration(
        self,
        start_url: str,
        max_depth: int | None
    ) -> list[ExplorationNode]:
        """Fallback exploration without DepthMaximizer"""
        logger.debug(f"Using fallback exploration for {start_url}")

        # Return single node
        return [ExplorationNode(
            node_id="root",
            url=start_url,
            title="Root",
            depth=0,
            parent_id=None,
            children=[],
            citations=[],
            quality_score=1.0
        )]

    async def hunt(
        self,
        query: str,
        dorks: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """
        Hunt for URLs using DuckDuckGo.

        Args:
            query: Search query
            dorks: Optional search dorks

        Returns:
            List of found URLs with metadata
        """
        if self._hunter is None:
            await self._init_hunter()

        if self._hunter is None:
            logger.warning("⚠️ Hunter not available")
            return []

        try:
            results = []
            async for artifact in self._hunter.search(query, dorks=dorks):
                results.append({
                    "url": artifact.url if hasattr(artifact, 'url') else str(artifact),
                    "title": artifact.title if hasattr(artifact, 'title') else "",
                    "source": "hunter"
                })

            logger.info(f"🔍 Hunt complete: {len(results)} results")
            return results

        except Exception as e:
            logger.error(f"❌ Hunt failed: {e}")
            return []

    async def harvest(
        self,
        url: str,
        depth: int = 0
    ) -> dict[str, Any]:
        """
        Harvest content from URL using Trafilatura.

        Args:
            url: URL to harvest
            depth: Harvesting depth

        Returns:
            Harvested content
        """
        if self._hunter is None:
            await self._init_hunter()

        if self._hunter is None:
            logger.warning("⚠️ Hunter not available")
            return {"url": url, "content": "", "success": False}

        try:
            result = await self._hunter.harvest(url, depth=depth)
            return {
                "url": url,
                "content": result.content if hasattr(result, 'content') else str(result),
                "title": result.title if hasattr(result, 'title') else "",
                "success": True
            }

        except Exception as e:
            logger.error(f"❌ Harvest failed: {e}")
            return {"url": url, "content": "", "success": False, "error": str(e)}

    def get_statistics(self) -> dict[str, Any]:
        """Get research layer statistics"""
        return {
            "missions_completed": self._missions_completed,
            "actions_executed": self._actions_executed,
            "depth_levels_reached": self._depth_levels_reached,
            "active_missions": len(self._missions),
            "ghost_director_available": self._ghost_director is not None,
            "depth_maximizer_available": self._depth_maximizer is not None,
            "hunter_available": self._hunter is not None,
            "config": {
                "max_depth": self.config.max_depth,
                "strategy": self.config.strategy,
                "follow_citations": self.config.follow_citations,
                "explore_tangents": self.config.explore_tangents,
            }
        }

    async def cleanup(self) -> None:
        """Cleanup resources"""
        logger.info("🧹 Cleaning up ResearchLayer...")

        # Cleanup GhostDirector
        if self._ghost_director and hasattr(self._ghost_director, 'cleanup'):
            try:
                await self._ghost_director.cleanup()
            except Exception as e:
                logger.warning(f"⚠️ GhostDirector cleanup error: {e}")

        # Cleanup DepthMaximizer
        if self._depth_maximizer and hasattr(self._depth_maximizer, 'stop'):
            try:
                await self._depth_maximizer.stop()
            except Exception as e:
                logger.warning(f"⚠️ DepthMaximizer cleanup error: {e}")

        # Cleanup Hunter
        if self._hunter and hasattr(self._hunter, 'cleanup'):
            try:
                await self._hunter.cleanup()
            except Exception as e:
                logger.warning(f"⚠️ Hunter cleanup error: {e}")

        self._missions.clear()
        self._explorations.clear()

        logger.info("✅ ResearchLayer cleanup complete")
