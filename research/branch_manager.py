"""
BranchManager – rozhodování o odbočkách s ANE a spiking prioritou.
Rozhoduje o vytvoření nových větví (úloh) na základě nálezů.
"""
import asyncio
import logging
import time
from heapq import heappop, heappush
from pathlib import Path
from typing import TYPE_CHECKING, Any
if TYPE_CHECKING:
    from hledac.universal.research.parallel_scheduler import PrioritizedTask
logger = logging.getLogger(__name__)
try:
    import coremltools as ct
    ANE_AVAILABLE = True
except ImportError:
    ct = None
    ANE_AVAILABLE = False

class BranchManager:
    """
    Správce větví pro paralelní výzkum.
    Rozhoduje o vytvoření nových úloh na základě nálezů.
    """
    __slots__ = tuple(('_ane_model_path', '_entity_cache', 'ane_model', 'claim_index', 'rel_engine', 'scheduler', 'seen_entities', 'spike_net'))

    def __init__(self, scheduler, rel_engine=None, claim_index=None, ane_model_path: str | None=None):
        self.scheduler = scheduler
        self.rel_engine = rel_engine
        self.claim_index = claim_index
        self.seen_entities: set = set()
        self._entity_cache: dict[str, Any] = {}
        self._ane_model_path = Path(ane_model_path) if ane_model_path else None
        self.ane_model = None
        try:
            from hledac.universal.research.spike_priority import SpikePriorityNetwork
            self.spike_net = SpikePriorityNetwork(n_neurons=8)
        except ImportError:
            self.spike_net = None

    def _ensure_ane_model(self):
        """CoreML→MLX migration: lazy-load ANE model on first use (was eager in __init__)."""
        if self.ane_model is not None:
            return
        if not self._ane_model_path or not self._ane_model_path.exists():
            return
        try:
            self.ane_model = ct.models.MLModel(str(self._ane_model_path))
            logger.info(f'Loaded ANE model from {self._ane_model_path}')
        except Exception as e:
            logger.warning(f'Failed to load ANE model: {e}')
            self.ane_model = None

    async def on_finding(self, finding: dict[str, Any]):
        """
        Zpracuje nový nález a rozhodne o větvi.
        """
        features = self._extract_features(finding)
        prob = self._predict_branch_ane(features)
        if prob > 0.7:
            entity = finding.get('entity')
            if entity and entity not in self.seen_entities:
                await self._create_branch(entity, finding, prob)
                if self.spike_net:
                    spikes = self.spike_net.forward(prob)
                    if any(spikes):
                        await self._boost_related_tasks(entity, spikes)

    def _extract_features(self, finding: dict[str, Any]) -> list[float]:
        """Extrahuje features z nálezu."""
        entity = finding.get('entity')
        centrality = 0.0
        if self.rel_engine and entity:
            try:
                centrality = self.rel_engine.get_entity_centrality(entity) if hasattr(self.rel_engine, 'get_entity_centrality') else 0.0
            except Exception:
                centrality = 0.0
        novelty = 1.0 if entity and entity not in self.seen_entities else 0.0
        contradiction = 0.0
        if self.claim_index and entity:
            try:
                if hasattr(self.claim_index, 'is_contested'):
                    contradiction = 1.0 if self.claim_index.is_contested(entity) else 0.0
            except Exception:
                contradiction = 0.0
        source_type = finding.get('source_type', 0)
        return [centrality, novelty, contradiction, source_type]

    def _predict_branch_ane(self, features: list[float]) -> float:
        """Predikce pomocí ANE CoreML modelu (lazy-loaded)."""
        self._ensure_ane_model()
        if self.ane_model is None:
            return self._predict_branch_fallback(features)
        try:
            result = self.ane_model.predict({'features': features})
            return float(result.get('probability', 0.0))
        except Exception as e:
            logger.warning(f'ANE prediction failed: {e}')
            return self._predict_branch_fallback(features)

    def _predict_branch_fallback(self, features: list[float]) -> float:
        """Fallback pravidlo pro rozhodnutí o větvi."""
        centrality = features[0]
        novelty = features[1]
        contradiction = features[2]
        prob = 0.5 + 0.2 * centrality + 0.1 * novelty + 0.2 * contradiction
        return min(1.0, max(0.0, prob))

    async def _create_branch(self, entity: str, finding: dict[str, Any], prob: float):
        """Vytvoří novou větev (úlohu) pro entity."""
        self.seen_entities.add(entity)
        task_id = f'branch_{entity}_{int(time.time())}'
        priority = 0.8 + prob * 0.2
        if self.scheduler and hasattr(self.scheduler, 'submit'):
            await self.scheduler.submit(task_id=task_id, coro_or_fn=self._explore_entity, priority=priority, is_coro=True, metadata={'entity': entity, 'source': finding.get('source')}, entity=entity)
            logger.info(f'Created branch for entity {entity} with priority {priority:.2f}')

    async def _boost_related_tasks(self, entity: str, spikes: list[float]):
        """Zvýší prioritu úloh souvisejících s entity."""
        if not self.scheduler or not hasattr(self.scheduler, '_lock'):
            return
        async with self.scheduler._lock:
            await self._boost_queue(self.scheduler.io_queue, entity)
            await self._boost_queue(self.scheduler.cpu_queue, entity)

    async def _boost_queue(self, queue: list, entity: str):
        """Zvýší prioritu úloh v dané frontě."""
        if not queue:
            return
        new_queue = []
        while queue:
            try:
                task = heappop(queue)
                entities = task.metadata.get('entities', [])
                if entity in entities:
                    task = PrioritizedTask(priority=task.priority - 0.1, task_id=task.task_id, coro_or_fn=task.coro_or_fn, args=task.args, kwargs=task.kwargs, created_at=task.created_at, metadata=task.metadata, is_coro=task.is_coro, timeout=task.timeout)
                new_queue.append(task)
            except Exception:
                break
        for task in new_queue:
            heappush(queue, task)

    async def _explore_entity(self, entity: str):
        """Explore an entity using available search/graph backends."""
        try:
            async with asyncio.timeout(30.0):
                results = await self._do_explore_entity(entity)
            if results:
                self._entity_cache[entity] = results
                logger.debug(f'Explored entity {entity}: {len(results)} results cached')
        except TimeoutError:
            logger.debug(f'Entity exploration timed out: {entity}')
        except Exception as e:
            logger.debug(f'Entity exploration failed for {entity}: {e}')

    async def _do_explore_entity(self, entity: str) -> list[dict[str, Any]]:
        """Core exploration logic with backends."""
        results: list[dict[str, Any]] = []
        try:
            from hledac.universal.knowledge.search_index import LocalSearchSeam
            seam = LocalSearchSeam()
            search_result = seam.search(entity, top_k=5)
            for doc in search_result.results:
                results.append({'source': 'local_search', 'url': doc.url, 'title': doc.title, 'score': doc.score})
            if results:
                return results
        except Exception:
            pass
        try:
            from hledac.universal.knowledge import graph_service
            history = graph_service.find_entity_history(entity, max_hops=2)
            for item in history:
                results.append({'source': 'graph_history', 'entity': item.get('entity'), 'relation': item.get('relation'), 'neighbors': item.get('neighbors', [])})
            if results:
                return results
        except Exception:
            pass
        return results

    def get_seen_entities(self) -> set:
        """Vrátí množinu již viděných entit."""
        return self.seen_entities.copy()