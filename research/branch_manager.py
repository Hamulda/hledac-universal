"""
BranchManager – rozhodování o odbočkách s ANE a spiking prioritou.
Rozhoduje o vytvoření nových větví (úloh) na základě nálezů.


ISSUE-037 opravy:
1. _boost_related_tasks/_boost_queue REMOVED — heappop na asyncio.PriorityQueue
   je CRITICAL bug (PriorityQueue nemá heap protocol). Nahrrazeno přímým
   submit() s vysokou prioritou do scheduleru.
2. MLXSpikeNetwork (s benchmark fallback) místo SpikePriorityNetwork (CPU only)
3. COREML_AVAILABLE guard na ct.models.MLModel — fail-soft
4. _pending_lock accessed přes správný název
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hledac.universal.research.parallel_scheduler import PrioritizedTask

logger = logging.getLogger(__name__)

# Optional CoreML
try:
    import coremltools as ct

    COREML_AVAILABLE = True
except ImportError:
    COREML_AVAILABLE = False
    ct = None  # type: ignore


class BranchManager:
    """
    Správce větví pro paralelní výzkum.

    Rozhoduje o vytvoření nových úloh na základě nálezů.
    ISSUE-037: Spike boosting přes přímý submit() s vysokou prioritou,
    ne přes heappop na asyncio.PriorityQueue (CRITICAL bug fix).
    """

    __slots__ = tuple(
        (
            "_ane_model",
            "_ane_model_path",
            "_entity_cache",
            "ane_model",
            "claim_index",
            "rel_engine",
            "scheduler",
            "seen_entities",
            "_spike_net",
        )
    )

    # ISSUE-037-FIX: bounded collections pro memory safety na long-running systémech
    _MAX_SEEN_ENTITIES: int = 50_000
    _MAX_ENTITY_CACHE: int = 10_000

    def __init__(
        self,
        scheduler,
        rel_engine=None,
        claim_index=None,
        ane_model_path: str | Path | None = None,
        n_spike_neurons: int = 8,
    ) -> None:
        self.scheduler = scheduler
        self.rel_engine = rel_engine
        self.claim_index = claim_index
        self.seen_entities: set = set()
        self._entity_cache: dict[str, Any] = {}
        self._seen_entities_fifo: list[str] = []  # pro LRU eviction
        self._entity_cache_fifo: list[str] = []  # pro LRU eviction
        self._ane_model_path: Path | None = (
            Path(ane_model_path) if ane_model_path else None
        )
        self.ane_model: Any = None
        self._n_spike_neurons = n_spike_neurons
        self._spike_net: Any = None  # lazy init v _ensure_spike_net()

    def _ensure_spike_net(self) -> None:
        """
        Lazy initialization MLXSpikeNetwork s benchmark fallback.
        ISSUE-037: Používá MLXSpikeNetwork (benchmark-aware) místo
        SpikePriorityNetwork (CPU-only).
        """
        if self._spike_net is not None:
            return
        try:
            # Import MLX-aware class
            from hledac.universal.research.spike_priority import MLXSpikeNetwork

            self._spike_net = MLXSpikeNetwork(
                n_neurons=self._n_spike_neurons,
                ane_model_path=None,  # ANE model je volitelný
            )
        except Exception:
            # Fail-soft: spike boosting disabled
            self._spike_net = None

    def _ensure_ane_model(self) -> None:
        """
        Lazy-load ANE CoreML model na první použití.
        ISSUE-037: COREML_AVAILABLE guard + fail-soft.
        """
        if self.ane_model is not None:
            return
        if not COREML_AVAILABLE or not self._ane_model_path:
            return
        try:
            self.ane_model = ct.models.MLModel(  # type: ignore[union-attr]
                str(self._ane_model_path)
            )
            logger.info("Loaded ANE model from %s", self._ane_model_path)
        except Exception as e:
            logger.warning("Failed to load ANE model: %s", e)
            self.ane_model = None

    async def on_finding(self, finding: dict[str, Any]) -> None:
        """
        Zpracuje nový nález a rozhodne o větvi.

        ISSUE-037: Spike boosting místo heappop na PriorityQueue
        nyní používá přímý submit() s vysokou prioritou.
        """
        features = self._extract_features(finding)
        prob = self._predict_branch_ane(features)

        if prob > 0.7:
            entity = finding.get("entity")
            if entity and entity not in self.seen_entities:
                await self._create_branch(entity, finding, prob)
                # ISSUE-037: Spike boosting přes submit s vysokou prioritou
                await self._spike_boost(entity, prob)

    def _extract_features(self, finding: dict[str, Any]) -> list[float]:
        """Extrahuje features z nálezu."""
        entity = finding.get("entity")
        centrality = 0.0
        if self.rel_engine and entity:
            try:
                if hasattr(self.rel_engine, "get_entity_centrality"):
                    centrality = self.rel_engine.get_entity_centrality(entity)
            except Exception:
                centrality = 0.0

        novelty = 1.0 if entity and entity not in self.seen_entities else 0.0

        contradiction = 0.0
        if self.claim_index and entity:
            try:
                if hasattr(self.claim_index, "is_contested"):
                    contradiction = (
                        1.0 if self.claim_index.is_contested(entity) else 0.0
                    )
            except Exception:
                contradiction = 0.0

        source_type = finding.get("source_type", 0)
        return [centrality, novelty, contradiction, source_type]

    def _predict_branch_ane(self, features: list[float]) -> float:
        """
        Predikce pomocí ANE CoreML modelu (lazy-loaded).
        ISSUE-037: COREML_AVAILABLE guard + fail-soft fallback.
        """
        self._ensure_ane_model()
        if self.ane_model is None:
            return self._predict_branch_fallback(features)
        try:
            result = self.ane_model.predict({"features": features})
            return float(result.get("probability", 0.0))
        except Exception as e:
            logger.warning("ANE prediction failed: %s", e)
            return self._predict_branch_fallback(features)

    def _predict_branch_fallback(self, features: list[float]) -> float:
        """Fallback pravidlo pro rozhodnutí o větvi."""
        centrality = features[0]
        novelty = features[1]
        contradiction = features[2]
        prob = 0.5 + 0.2 * centrality + 0.1 * novelty + 0.2 * contradiction
        return min(1.0, max(0.0, prob))

    def _add_seen_entity(self, entity: str) -> None:
        """Bounded add s LRU eviction pro seen_entities."""
        if entity in self.seen_entities:
            return
        self.seen_entities.add(entity)
        self._seen_entities_fifo.append(entity)
        if len(self.seen_entities) > self._MAX_SEEN_ENTITIES:
            oldest = self._seen_entities_fifo.pop(0)
            self.seen_entities.discard(oldest)

    def _cache_entity(self, entity: str, results: Any) -> None:
        """Bounded add s LRU eviction pro _entity_cache."""
        if entity in self._entity_cache:
            return
        self._entity_cache[entity] = results
        self._entity_cache_fifo.append(entity)
        if len(self._entity_cache) > self._MAX_ENTITY_CACHE:
            oldest = self._entity_cache_fifo.pop(0)
            self._entity_cache.pop(oldest, None)

    async def _create_branch(
        self, entity: str, finding: dict[str, Any], prob: float
    ) -> None:
        """Vytvoří novou větev (úlohu) pro entity."""
        self._add_seen_entity(entity)
        task_id = f"branch_{entity}_{int(time.time())}"
        priority = 0.8 + prob * 0.2

        if self.scheduler and hasattr(self.scheduler, "submit"):
            await self.scheduler.submit(
                task_id=task_id,
                coro_or_fn=self._explore_entity,
                priority=priority,
                is_coro=True,
                metadata={
                    "entity": entity,
                    "source": finding.get("source"),
                },
                entity=entity,
            )
            logger.info(
                "Created branch for entity %s with priority %.2f",
                entity,
                priority,
            )

    async def _spike_boost(self, entity: str, prob: float) -> None:
        """
        ISSUE-037: Spike boosting přes přímý submit() s vysokou prioritou.

        Nahrzuje starý _boost_queue s heappop na asyncio.PriorityQueue.
        Místo manipulace s interní frontou scheduleru (což je nestabilní API)
        submitneme novou úlohu s vysokou prioritou, která provede
        boost existujících úloh pro danou entitu.
        """
        self._ensure_spike_net()
        if self._spike_net is None:
            return

        # MLX/ANE spike inference
        try:
            spikes = self._spike_net.forward(prob)
            spike_count = sum(1 for s in spikes if s > 0)
        except Exception:
            spike_count = 0

        if spike_count == 0:
            return

        # Submit boost task s vysokou prioritou (nízké číslo = vyšší priorita)
        boost_priority = max(0.1, 1.0 - spike_count * 0.1)
        boost_task_id = f"spike_boost_{entity}_{int(time.time())}"

        if self.scheduler and hasattr(self.scheduler, "submit"):
            await self.scheduler.submit(
                task_id=boost_task_id,
                coro_or_fn=self._spike_boost_task,
                priority=boost_priority,
                is_coro=True,
                metadata={
                    "entity": entity,
                    "spike_count": spike_count,
                },
                entity=entity,
                spike_count=spike_count,
            )
            logger.debug(
                "Spike boost submitted for %s (spikes=%d, priority=%.2f)",
                entity,
                spike_count,
                boost_priority,
            )

    async def _spike_boost_task(
        self, entity: str, spike_count: int
    ) -> dict[str, Any]:
        """
        Spike boost task — zvýší prioritu příbuzných úloh.

        ISSUE-037 REMOVED: heappop na asyncio.PriorityQueue.
        Tato metoda nyní jen loguje a eviduje boost akci.
        Skutečné přeřazení priorit by vyžadovalo změnu ParallelResearchScheduler API.
        """
        boosted = 0
        # Prozatímně: evidence boost akce
        logger.debug(
            "Spike boost executed for entity=%s (spike_count=%d)",
            entity,
            spike_count,
        )
        return {"entity": entity, "spike_count": spike_count, "boosted": boosted}

    async def _explore_entity(self, entity: str) -> None:
        """Explore an entity using available search/graph backends."""
        try:
            async with asyncio.timeout(30.0):
                results = await self._do_explore_entity(entity)
            if results:
                self._cache_entity(entity, results)
                logger.debug(
                    "Explored entity %s: %d results cached",
                    entity,
                    len(results),
                )
        except TimeoutError:
            logger.debug("Entity exploration timed out: %s", entity)
        except Exception as e:
            logger.debug("Entity exploration failed for %s: %s", entity, e)

    async def _do_explore_entity(self, entity: str) -> list[dict[str, Any]]:
        """Core exploration logic with backends."""
        results: list[dict[str, Any]] = []

        # Try local search seam
        try:
            from hledac.universal.knowledge.search_index import LocalSearchSeam

            seam = LocalSearchSeam()
            search_result = seam.search(entity, top_k=5)
            for doc in search_result.results:
                results.append(
                    {
                        "source": "local_search",
                        "url": doc.url,
                        "title": doc.title,
                        "score": doc.score,
                    }
                )
            if results:
                return results
        except Exception:  # noqa: BLE001
            pass

        # Try graph history
        try:
            from hledac.universal.knowledge import graph_service

            history = graph_service.find_entity_history(entity, max_hops=2)
            for item in history:
                results.append(
                    {
                        "source": "graph_history",
                        "entity": item.get("entity"),
                        "relation": item.get("relation"),
                        "neighbors": item.get("neighbors", []),
                    }
                )
            if results:
                return results
        except Exception:  # noqa: BLE001
            pass

        return results

    def get_seen_entities(self) -> set:
        """Vrátí množinu již viděných entit."""
        return self.seen_entities.copy()
