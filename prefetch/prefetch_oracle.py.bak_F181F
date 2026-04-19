"""
PrefetchOracle – rozhoduje, které URL se mají načítat na pozadí.
Používá dvoustupňový výběr:
1. Stage A: ultralehké kandidáty (common neighbors, PQIndex, sketchy)
2. Stage B: ML reranker (SSM) pro top‑K kandidátů (jako sekvence).
Online učení pomocí contextual banditu (LinUCB) s UCB selection.
"""

import asyncio
import hashlib
import logging
import time
from collections import defaultdict, OrderedDict
from typing import Dict, List, Optional, Tuple, Any

import mlx.core as mx
import numpy as np

from hledac.universal.research.parallel_scheduler import ParallelResearchScheduler
from hledac.universal.intelligence.relationship_discovery import RelationshipDiscoveryEngine
from hledac.universal.federated.sketches import CountMinSketch, SimHashSketch
from hledac.universal.knowledge.pq_index import PQIndex
from hledac.universal.prefetch.prefetch_cache import PrefetchCache

logger = logging.getLogger(__name__)

# Konstanty pro Stage A time budget (ms)
STAGE_A_TIME_BUDGET_MS = 1.5
# Dimenze feature vektorů
BANDIT_DIM = 64 + 3 + 64                               # entity_emb(64) + quality(3) + task_emb(64)
RERANKER_DIM = 1 + 4 + 1 + BANDIT_DIM                 # rank_norm(1) + type_emb(4) + stage_score(1) + bandit context


# Priority constant for prefetch (lower than research)
PRIORITY_PREFETCH = 9


class PrefetchOracle:
    def __init__(self, scheduler: ParallelResearchScheduler, rel_engine: RelationshipDiscoveryEngine,
                 pq_index: PQIndex, cache: PrefetchCache, max_candidates: int = 50, top_k: int = 10,
                 network_budget_mb: float = 10.0, cpu_budget_ms: float = 100.0,
                 alpha: float = 0.5, bandit_weight: float = 0.2, lambda_waste: float = 0.01,
                 lambda_prior: float = 1.0):
        self.scheduler = scheduler
        self.rel_engine = rel_engine
        self.pq_index = pq_index
        self.cache = cache
        self.max_candidates = max_candidates
        self.top_k = top_k
        self.network_budget_mb = network_budget_mb
        self.cpu_budget_ms = cpu_budget_ms
        self.alpha = alpha                              # exploration bonus (standard LinUCB)
        self.bandit_weight = bandit_weight              # váha bandit skóre při kombinaci
        self.lambda_waste = lambda_waste                # penalizace za nevyužitý prefetch
        self.lambda_prior = lambda_prior                # regularizace pro LinUCB cold‑start

        # Stage A adaptivní limity
        self._neighbors_limit = 10
        self._pq_k = 5
        self._max_candidates_dynamic = max_candidates
        self._stage_a_time_accum = 0.0
        self._stage_a_count = 0

        # Stage A heuristiky a deduplikace
        self.cms = CountMinSketch()
        self.shs = SimHashSketch()
        self._seen_fingerprints = OrderedDict()         # LRU: fingerprint -> timestamp
        self._max_seen = 100000

        # Stage B ML reranker (inicializován v initialize)
        self.reranker = None

        # Contextual bandit (LinUCB) – per arm
        self.bandit_arms = {}                            # arm_id -> {'A': np.array(d,d), 'b': np.array(d,), 'A_inv': np.array(d,d)}
        self._arm_features = {}                          # (arm_id, url) -> context vector (np.array)

        # Sledování naplánovaných prefetchů pro pozdější penalizaci
        self._scheduled = OrderedDict()                  # url -> {'arm_id': arm_id, 'context': np.array, 'expires': float}
        self._max_scheduled = 100000

        # Statistiky
        self.prefetch_stats = defaultdict(lambda: {'hits': 0, 'misses': 0, 'bytes': 0})

        # Aktuální task embedding (bude nastaven orchestrátorem)
        self._current_task_embedding = mx.zeros(64)

        # Mapování node_id ↔ url (pro PQIndex)
        self._id_to_url = []                              # index -> url
        self._url_to_id = OrderedDict()                   # url -> index (LRU)
        self._max_url_map = 100000

        # Řízení běhu expire loop
        self._stop_event = asyncio.Event()
        self._expire_task = None

    async def initialize(self):
        """Načte nebo vytvoří reranker model a spustí expire loop."""
        from hledac.universal.prefetch.ssm_reranker import SSMReranker
        self.reranker = SSMReranker(feature_dim=RERANKER_DIM)
        # Zde by se načetly váhy z disku, pokud existují (např. self.reranker.load(...))
        self._expire_task = asyncio.create_task(self._expire_loop())

    async def shutdown(self):
        """Zastaví expire loop a uvolní zdroje."""
        self._stop_event.set()
        if self._expire_task:
            await self._expire_task

    def set_task_embedding(self, emb: mx.array):
        """Nastaví embedding aktuálního výzkumného úkolu (např. z query)."""
        self._current_task_embedding = emb

    async def on_new_candidates(self, url: str, entity: str, source_type: str):
        """Volá se při objevení nových URL (např. z fetch_coordinator nebo content_miner)."""
        start = time.perf_counter()

        # Stage A: generování kandidátů s adaptivními limity
        candidates = self._generate_candidates(url, entity, source_type)

        elapsed_ms = (time.perf_counter() - start) * 1000
        self._stage_a_time_accum += elapsed_ms
        self._stage_a_count += 1

        # Adaptivní úprava limitů (klouzavý průměr)
        if self._stage_a_count >= 10:
            avg_time = self._stage_a_time_accum / self._stage_a_count
            if avg_time > STAGE_A_TIME_BUDGET_MS:
                self._neighbors_limit = max(2, self._neighbors_limit // 2)
                self._pq_k = max(1, self._pq_k // 2)
                self._max_candidates_dynamic = max(10, self._max_candidates_dynamic // 2)
                logger.info(f"Stage A budget exceeded, reducing limits: neighbors={self._neighbors_limit}, pq_k={self._pq_k}, candidates={self._max_candidates_dynamic}")
            elif avg_time < STAGE_A_TIME_BUDGET_MS * 0.5:
                self._neighbors_limit = min(20, self._neighbors_limit + 1)
                self._pq_k = min(10, self._pq_k + 1)
                self._max_candidates_dynamic = min(self.max_candidates, self._max_candidates_dynamic + 5)
            self._stage_a_time_accum = 0
            self._stage_a_count = 0

        if not candidates:
            return

        # Stage B: reranking
        if self.reranker:
            features = self._extract_features_batch(candidates)   # (n, RERANKER_DIM)
            features = features[None, :, :]                       # (1, n, RERANKER_DIM)
            scores = self.reranker(features)[0]                   # (n,)  (synchronní volání)

            # Kombinace s bandit UCB
            for i, cand in enumerate(candidates):
                arm_id = self._classify_url(cand['url'])
                bandit_context = self._get_bandit_context_vector(cand)   # numpy array (BANDIT_DIM,)
                ucb = self._compute_ucb(arm_id, bandit_context)          # float
                cand['final_score'] = float(scores[i]) + self.bandit_weight * ucb

            candidates.sort(key=lambda x: x['final_score'], reverse=True)
            candidates = candidates[:self.top_k]

        # Aplikujeme budget (network)
        candidates = self._apply_budget(candidates)

        # Naplánujeme prefetch
        now = time.time()
        for cand in candidates:
            cand_url = cand['url']
            arm_id = self._classify_url(cand_url)
            bandit_context = self._get_bandit_context_vector(cand)
            self._arm_features[(arm_id, cand_url)] = bandit_context
            expires = now + 3600  # TTL pro reward 1 hodina
            self._scheduled[cand_url] = {
                'arm_id': arm_id,
                'context': bandit_context,
                'expires': expires
            }
            if len(self._scheduled) > self._max_scheduled:
                self._scheduled.popitem(last=False)

            await self.scheduler.schedule_prefetch(
                task_id=f"prefetch_{hash(cand_url)}_{int(now * 1000)}",
                coro_or_fn=self._fetch_for_prefetch,
                priority=PRIORITY_PREFETCH,
                is_coro=True,
                url=cand_url,
                deadline=now + 30,                     # deadline pro stažení 30 s
                estimated_bytes=cand.get('size', 1024 * 1024),
                metadata=cand
            )

    async def _fetch_for_prefetch(self, url: str, deadline: float, estimated_bytes: int, metadata: Dict):
        """Provede prefetch fetch – voláno z scheduleru."""
        if time.time() > deadline:
            return {'success': False, 'reason': 'deadline'}

        # Zkontrolujeme cache
        cached = await self.cache.get(url)
        if cached is not None:
            await self.on_cache_hit(url)
            return {'success': True, 'cached': True, 'data': cached}

        # Fetch by proběhl přes http_client - zde je placeholder
        # V reálné implementaci by se použil self.scheduler.http_client nebo podobný
        return {'success': False, 'reason': 'not_implemented'}

    def _fast_fingerprint(self, url: str) -> int:
        """Rychlý 64bit fingerprint URL (prvních 8 bytů SHA256)."""
        h = hashlib.sha256(url.encode()).digest()[:8]
        return int.from_bytes(h, byteorder='big')

    def _generate_candidates(self, url: str, entity: str, source_type: str) -> List[Dict]:
        """Stage A: generování kandidátů s dynamickými limity."""
        candidates = []

        # 1. Common neighbors z grafu
        neighbors = self._get_common_neighbors(entity, limit=self._neighbors_limit)
        for n in neighbors:
            candidates.append({'url': n['url'], 'type': 'graph', 'score': n['score']})

        # 2. Entity-based (PQIndex approximate nearest neighbors)
        emb = self._get_entity_embedding(entity)
        if emb is not None and self.pq_index.centroids is not None:
            if not isinstance(emb, mx.array):
                emb = mx.array(emb)
            similar = self.pq_index.search(emb, k=self._pq_k)            # vrací [(node_id, dist)]
            for node_id, dist in similar:
                cand_url = self._id_to_url[node_id] if node_id < len(self._id_to_url) else str(node_id)
                candidates.append({'url': cand_url, 'type': 'pq', 'score': 1.0 / (1.0 + dist)})

        # 3. Deduplikace přes sketchy (LRU bounded)
        filtered = []
        for c in candidates:
            fp = self._fast_fingerprint(c['url'])
            if self.cms.estimate(c['url']) > 0 or fp in self._seen_fingerprints:
                continue
            filtered.append(c)
            self.cms.add(c['url'])
            self._seen_fingerprints[fp] = time.time()
            if len(self._seen_fingerprints) > self._max_seen:
                self._seen_fingerprints.popitem(last=False)

        return filtered[:self._max_candidates_dynamic]

    def _get_common_neighbors(self, entity: str, limit: int) -> List[Dict]:
        """Placeholder pro get_common_neighbors - wrapper pro relationship_discovery."""
        # Try to call the actual method if it exists
        if hasattr(self.rel_engine, 'get_common_neighbors'):
            try:
                return self.rel_engine.get_common_neighbors(entity, limit=limit)
            except Exception:
                pass
        return []

    def _get_entity_embedding(self, entity: str) -> Optional[mx.array]:
        """Placeholder pro get_entity_embedding - wrapper pro relationship_discovery."""
        if hasattr(self.rel_engine, 'get_entity_embedding'):
            try:
                emb = self.rel_engine.get_entity_embedding(entity)
                if emb is not None:
                    return emb
            except Exception:
                pass
        # Fallback - return random embedding for testing
        return mx.random.normal(64)

    def _extract_features_batch(self, candidates: List[Dict]) -> mx.array:
        """Extrahuje feature vektory pro reranker. Vrací (n, RERANKER_DIM)."""
        features = []
        for i, c in enumerate(candidates):
            # Poziční features
            rank_norm = (i + 1) / len(candidates)
            type_id = {'graph': 0, 'pq': 1, 'pattern': 2}.get(c.get('type', 'other'), 3)
            type_emb_np = np.zeros(4, dtype=np.float32)
            type_emb_np[type_id] = 1.0

            # Stage A score
            stage_score_np = np.array([c.get('score', 0.5)], dtype=np.float32)

            # Bandit kontext
            bandit_context = self._get_bandit_context_vector(c)

            # Složíme dohromady (nejdříve numpy, pak mx.array)
            feat_np = np.concatenate([
                [rank_norm],
                type_emb_np,
                stage_score_np,
                bandit_context
            ]).astype(np.float32)
            features.append(mx.array(feat_np))

        return mx.stack(features)

    def _get_bandit_context_vector(self, candidate: Dict) -> np.ndarray:
        """Vrací feature vector pro bandit (numpy array float32, dim BANDIT_DIM)."""
        # GNN embedding entity
        entity = candidate.get('entity', '')
        entity_emb = self._get_entity_embedding(entity)
        if entity_emb is not None:
            if isinstance(entity_emb, mx.array):
                entity_emb_np = entity_emb.astype(np.float32)
            else:
                entity_emb_np = np.array(entity_emb, dtype=np.float32)
        else:
            entity_emb_np = np.zeros(64, dtype=np.float32)

        # Source quality (domain, recency, robots) – dummy
        domain = self._extract_domain(candidate['url'])
        quality_np = np.array([len(domain) / 100.0, 0.5, 0.5], dtype=np.float32)

        # Task embedding
        task_emb_np = np.array(self._current_task_embedding, dtype=np.float32)

        return np.concatenate([entity_emb_np, quality_np, task_emb_np])

    def _extract_domain(self, url: str) -> str:
        from urllib.parse import urlparse
        return urlparse(url).netloc

    def _apply_budget(self, candidates: List[Dict]) -> List[Dict]:
        """Omezí kandidáty podle aktuálních budgetů (network)."""
        total_bytes = sum(c.get('estimated_bytes', c.get('size', 1024 * 1024)) for c in candidates)
        if total_bytes > self.network_budget_mb * 1024 * 1024:
            candidates.sort(key=lambda x: x.get('final_score', x.get('score', 0)), reverse=True)
            keep = int(len(candidates) * (self.network_budget_mb * 1024 * 1024 / total_bytes))
            candidates = candidates[:max(keep, 1)]
        return candidates

    def _classify_url(self, url: str) -> str:
        """Rozhodne, do kterého ramene banditu URL patří (např. podle domény)."""
        domain = self._extract_domain(url)
        parts = domain.split('.')
        if len(parts) >= 2:
            return parts[-2]
        return 'other'

    # ========= LinUCB metody =========
    def _compute_ucb(self, arm_id: str, x: np.ndarray) -> float:
        """
        Spočítá UCB skóre pro daný kontext.
        Pokud rameno ještě neexistuje, inicializuje ho s A_inv = (1/λ)I, b=0.
        """
        x64 = x.astype(np.float64, copy=False)
        if arm_id not in self.bandit_arms:
            d = len(x64)
            self.bandit_arms[arm_id] = {
                'A': np.eye(d, dtype=np.float64) * self.lambda_prior,
                'b': np.zeros(d, dtype=np.float64),
                'A_inv': np.eye(d, dtype=np.float64) / self.lambda_prior
            }
        arm = self.bandit_arms[arm_id]
        if arm['A_inv'] is None:
            arm['A_inv'] = np.linalg.inv(arm['A'])
        A_inv = arm['A_inv']
        theta = A_inv @ arm['b']
        mean = x64 @ theta
        var = x64 @ A_inv @ x64
        return mean + self.alpha * np.sqrt(max(var, 0))

    def _update_bandit(self, arm_id: str, x: np.ndarray, reward: float):
        """LinUCB update pomocí Sherman–Morrison (levnější)."""
        x64 = x.astype(np.float64, copy=False)
        if arm_id not in self.bandit_arms:
            d = len(x64)
            self.bandit_arms[arm_id] = {
                'A': np.eye(d, dtype=np.float64) * self.lambda_prior,
                'b': np.zeros(d, dtype=np.float64),
                'A_inv': np.eye(d, dtype=np.float64) / self.lambda_prior
            }
        arm = self.bandit_arms[arm_id]
        A_inv = arm['A_inv']
        # Sherman–Morrison: A_inv = A_inv - (A_inv @ x @ x.T @ A_inv) / (1 + x.T @ A_inv @ x)
        x_np = x64.reshape(-1, 1)
        A_inv_x = A_inv @ x_np
        denominator = 1 + (x_np.T @ A_inv_x).item()
        if denominator > 1e-8:
            arm['A_inv'] = A_inv - (A_inv_x @ A_inv_x.T) / denominator
        arm['A'] += np.outer(x64, x64)                     # pro kontrolu, ale A se nemusí používat dál
        arm['b'] += reward * x64

    # ========= Rewardy =========
    async def on_cache_hit(self, url: str):
        """Volá se při cache hit – skutečný reward."""
        info = self._scheduled.pop(url, None)
        if info is None:
            return
        arm_id = info['arm_id']
        x = info['context']
        self._update_bandit(arm_id, x, 1.0)
        self.prefetch_stats[arm_id]['hits'] += 1

    async def on_prefetch_result(self, url: str, success: bool, bytes_downloaded: int, latency_ms: float):
        """Volá se po dokončení prefetch úlohy. Ukládá cost pro pozdější reward, nebo penalizuje při neúspěchu."""
        info = self._scheduled.get(url)
        if info is None:
            return
        if not success:
            # Okamžitá penalizace (negativní reward)
            cost = bytes_downloaded / (1024 * 1024) + latency_ms / 1000.0 * 0.1
            reward = -self.lambda_waste * cost
            self._update_bandit(info['arm_id'], info['context'], reward)
            self.prefetch_stats[info['arm_id']]['misses'] += 1
            if url in self._scheduled:
                del self._scheduled[url]

    async def _expire_scheduled(self):
        """Projde naplánované a penalizuje ty, co vypršely."""
        now = time.time()
        expired = [url for url, info in self._scheduled.items() if info['expires'] < now]
        for url in expired:
            info = self._scheduled.pop(url)
            # Malá penalizace za nevyužití
            reward = -self.lambda_waste * 0.1   # konstantní slabá penalizace
            self._update_bandit(info['arm_id'], info['context'], reward)
            self.prefetch_stats[info['arm_id']]['misses'] += 1

    async def _expire_loop(self):
        """Background loop pro pravidelné spouštění expirace."""
        try:
            while not self._stop_event.is_set():
                await asyncio.sleep(3600)   # každou hodinu
                await self._expire_scheduled()
        except asyncio.CancelledError:
            logger.info("Expire loop cancelled")
            # Při zručení ještě provedeme jednorázovou expiraci
            await self._expire_scheduled()

    # ========= Mapování node_id ↔ url =========
    def register_node_url(self, node_id: int, url: str):
        while len(self._id_to_url) <= node_id:
            self._id_to_url.append(None)
        self._id_to_url[node_id] = url
        self._url_to_id[url] = node_id
        if len(self._url_to_id) > self._max_url_map:
            self._url_to_id.popitem(last=False)
