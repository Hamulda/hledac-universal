"""
PrefetchOracle – rozhoduje, které URL se mají načítat na pozadí.

PROMOTION GATE — EXPERIMENTAL / HEAVY / NOT PROMOTED
=====================================================
Používá dvoustupňový výběr:
1. Stage A: ultralehké kandidáty (common neighbors, PQIndex, sketchy)
2. Stage B: ML reranker (SSM) pro top‑K kandidátů (jako sekvence).
Online učení pomocí contextual banditu (LinUCB) s UCB selection.

STATUS: EXPERIMENTAL
  - SSMReranker (řádek 99): placeholder impl, žádné reálné trained weights
  - on_new_candidates() je voláno? GREP: žádné production call site
  - scheduler.schedule_prefetch() — existuje v ParallelResearchScheduler? ANO, ale unused
  - Bandit arms: unbounded dict (bandit_arms arm_id → {A, b, A_inv}), nikdy nemazáno
  - LRU caches: _seen_fingerprints (100k), _scheduled (100k), _url_to_id (100k) — bounded
  - _id_to_url list: unbounded (roste bez limitu při register_node_url)

M1 8GB MEMORY CEILING:
  - SSMReranker: mlx.nn.Sequential + linear layers (neznámá velikost bez váhy)
  - BANDIT_DIM = 131 (64+3+64) — 131-dim vectors pro každý arm
  - bandit_arms: pokud 10k unique domains → 10k * 131 * 8 bytes * 3 arrays ≈ 30MB+
  - entity embeddings: mx.random.normal(64) fallback, žádný real embedding store
  - _neighbors_limit adaptivní (2-20), _pq_k adaptivní (1-10)
  - Celkový memory footprint: těžko odhadnutelný bez reálných váh

ALLOWED PURPOSE: Spekulativní prefetch pro URL discovery
  - NENÍ součástí canonical fetch_coordinator path
  - Nemá žádnou proof-of-value v produkčním OSINT kontextu
  - LinUCB cold-start: lambda_prior=1.0, alpha=0.5 — empiricky neurčené

PROMOTION ELIGIBILITY: NO
  - Žádné production call sites
  - Reranker je pure placeholder — "Zde by se načetly váhy z disku" (komentář v kódu)
  - _fetch_for_prefetch vrací {'success': False, 'reason': 'not_implemented'}
  - Bandit arms BOUNDED na MAX_BANDIT_ARMS=512 — F184F fix (původně unbounded)
  - Adaptivní limity Stage A (1.5ms budget) jsou příliš agresivní pro M1 MLX overhead

CONTAINMENT HARDENING (F184F):
  - MAX_BANDIT_ARMS = 512 — hard upper bound na bandit arm count
  - při překročení: nejstarší arm (FIFO) odstraněn, nový přidán
  - _id_to_url list BOUNDED na MAX_URL_MAP (původně rostl bez limitu)
  - žádné unbounded memory growth na M1 8GB

SECURITY: Žádná.
STEALTH: Prefetch generuje síťový traffic — žádná stealth vrstva.
"""
import asyncio
from hledac.universal.utils.async_helpers import safe_create_task
import hashlib
import logging
import time
from collections import OrderedDict, defaultdict
try:
    import mlx.core as mx
    MLX_AVAILABLE = True
except ImportError:
    MLX_AVAILABLE = False
    mx = None
import numpy as np
from hledac.universal.federated.sketches import CountMinSketch, SimHashSketch
from hledac.universal.intelligence.relationship_discovery import RelationshipDiscoveryEngine
from hledac.universal.knowledge.pq_index import PQIndex
from hledac.universal.prefetch.prefetch_cache import PrefetchCache
from hledac.universal.research.parallel_scheduler import ParallelResearchScheduler
logger = logging.getLogger(__name__)
STAGE_A_TIME_BUDGET_MS = 1.5
BANDIT_DIM = 64 + 3 + 64
RERANKER_DIM = 1 + 4 + 1 + BANDIT_DIM
PRIORITY_PREFETCH = 9
MAX_BANDIT_ARMS = 512

class PrefetchOracle:
    __slots__ = tuple(('_arm_features', '_current_task_embedding', '_expire_task', '_id_to_url', '_last_stage_a_time', '_max_candidates_dynamic', '_max_scheduled', '_max_seen', '_max_url_map', '_neighbors_limit', '_pq_k', '_scheduled', '_seen_fingerprints', '_stage_a_count', '_stage_a_throttle_s', '_stage_a_time_accum', '_stop_event', '_url_to_id', 'alpha', 'bandit_arms', 'bandit_weight', 'cache', 'cms', 'cpu_budget_ms', 'lambda_prior', 'lambda_waste', 'max_candidates', 'network_budget_mb', 'pq_index', 'prefetch_stats', 'rel_engine', 'reranker', 'scheduler', 'shs', 'top_k'))

    def __init__(self, scheduler: ParallelResearchScheduler, rel_engine: RelationshipDiscoveryEngine, pq_index: PQIndex, cache: PrefetchCache, max_candidates: int=50, top_k: int=10, network_budget_mb: float=10.0, cpu_budget_ms: float=100.0, alpha: float=0.5, bandit_weight: float=0.2, lambda_waste: float=0.01, lambda_prior: float=1.0):
        self.scheduler = scheduler
        self.rel_engine = rel_engine
        self.pq_index = pq_index
        self.cache = cache
        self.max_candidates = max_candidates
        self.top_k = top_k
        self.network_budget_mb = network_budget_mb
        self.cpu_budget_ms = cpu_budget_ms
        self.alpha = alpha
        self.bandit_weight = bandit_weight
        self.lambda_waste = lambda_waste
        self.lambda_prior = lambda_prior
        self._neighbors_limit = 10
        self._pq_k = 5
        self._max_candidates_dynamic = max_candidates
        self._stage_a_time_accum = 0.0
        self._stage_a_count = 0
        self.cms = CountMinSketch()
        self.shs = SimHashSketch()
        self._seen_fingerprints = OrderedDict()
        self._max_seen = 100000
        self.reranker = None
        self.bandit_arms = {}
        self._arm_features = {}
        self._scheduled = OrderedDict()
        self._max_scheduled = 100000
        self.prefetch_stats = defaultdict(lambda: {'hits': 0, 'misses': 0, 'bytes': 0})
        self._current_task_embedding = np.zeros(64, dtype=np.float32)
        self._id_to_url = []
        self._url_to_id = OrderedDict()
        self._max_url_map = 100000
        self._last_stage_a_time = 0.0
        self._stage_a_throttle_s = 1.0
        self._stop_event = asyncio.Event()
        self._expire_task = None

    async def initialize(self):
        """Načte nebo vytvoří reranker model a spustí expire loop."""
        from hledac.universal.prefetch.ssm_reranker import SSMReranker
        self.reranker = SSMReranker(feature_dim=RERANKER_DIM)
        self._expire_task = safe_create_task(self._expire_loop())

    async def shutdown(self):
        """Zastaví expire loop a uvolní zdroje."""
        self._stop_event.set()
        if self._expire_task:
            await self._expire_task

    def set_task_embedding(self, emb):
        """Nastaví embedding aktuálního výzkumného úkolu (např. z query)."""
        self._current_task_embedding = emb

    async def on_new_candidates(self, url: str, entity: str, source_type: str):
        """Volá se při objevení nových URL (např. z fetch_coordinator nebo content_miner)."""
        now = time.monotonic()
        do_profile = now - self._last_stage_a_time >= self._stage_a_throttle_s
        if do_profile:
            start = time.perf_counter()
        candidates = self._generate_candidates(url, entity, source_type)
        if do_profile:
            elapsed_ms = (time.perf_counter() - start) * 1000
            self._stage_a_time_accum += elapsed_ms
            self._stage_a_count += 1
            self._last_stage_a_time = now
        if self._stage_a_count >= 10:
            avg_time = self._stage_a_time_accum / self._stage_a_count
            if avg_time > STAGE_A_TIME_BUDGET_MS:
                self._neighbors_limit = max(2, self._neighbors_limit // 2)
                self._pq_k = max(1, self._pq_k // 2)
                self._max_candidates_dynamic = max(10, self._max_candidates_dynamic // 2)
                logger.info(f'Stage A budget exceeded, reducing limits: neighbors={self._neighbors_limit}, pq_k={self._pq_k}, candidates={self._max_candidates_dynamic}')
            elif avg_time < STAGE_A_TIME_BUDGET_MS * 0.5:
                self._neighbors_limit = min(20, self._neighbors_limit + 1)
                self._pq_k = min(10, self._pq_k + 1)
                self._max_candidates_dynamic = min(self.max_candidates, self._max_candidates_dynamic + 5)
            self._stage_a_time_accum = 0
            self._stage_a_count = 0
        if not candidates:
            return
        if self.reranker:
            features = self._extract_features_batch(candidates)
            features = features[None, :, :]
            scores = self.reranker(features)[0]
            for i, cand in enumerate(candidates):
                arm_id = self._classify_url(cand['url'])
                bandit_context = self._get_bandit_context_vector(cand)
                ucb = self._compute_ucb(arm_id, bandit_context)
                cand['final_score'] = float(scores[i]) + self.bandit_weight * ucb
            candidates.sort(key=lambda x: x['final_score'], reverse=True)
            candidates = candidates[:self.top_k]
        candidates = self._apply_budget(candidates)
        now = time.time()
        for cand in candidates:
            cand_url = cand['url']
            arm_id = self._classify_url(cand_url)
            bandit_context = self._get_bandit_context_vector(cand)
            self._arm_features[arm_id, cand_url] = bandit_context
            expires = now + 3600
            self._scheduled[cand_url] = {'arm_id': arm_id, 'context': bandit_context, 'expires': expires}
            if len(self._scheduled) > self._max_scheduled:
                self._scheduled.popitem(last=False)
            await self.scheduler.schedule_prefetch(task_id=f'prefetch_{hash(cand_url)}_{int(now * 1000)}', coro_or_fn=self._fetch_for_prefetch, priority=PRIORITY_PREFETCH, is_coro=True, url=cand_url, deadline=now + 30, estimated_bytes=cand.get('size', 1024 * 1024), metadata=cand)

    async def _fetch_for_prefetch(self, url: str, deadline: float, estimated_bytes: int, metadata: dict):
        """Provede prefetch fetch – voláno z scheduleru."""
        if time.time() > deadline:
            return {'success': False, 'reason': 'deadline'}
        cached = await self.cache.get(url)
        if cached is not None:
            await self.on_cache_hit(url)
            return {'success': True, 'cached': True, 'data': cached}
        return {'success': False, 'reason': 'not_implemented'}

    def _fast_fingerprint(self, url: str) -> int:
        """Rychlý 64bit fingerprint URL (prvních 8 bytů SHA256)."""
        h = hashlib.sha256(url.encode()).digest()[:8]
        return int.from_bytes(h, byteorder='big')

    def _generate_candidates(self, url: str, entity: str, source_type: str) -> list[dict]:
        """Stage A: generování kandidátů s dynamickými limity."""
        candidates = []
        neighbors = self._get_common_neighbors(entity, limit=self._neighbors_limit)
        for n in neighbors:
            candidates.append({'url': n['url'], 'type': 'graph', 'score': n['score']})
        emb = self._get_entity_embedding(entity)
        if emb is not None and self.pq_index.centroids is not None:
            if MLX_AVAILABLE and (not isinstance(emb, mx.array)):
                emb = mx.array(emb)
            similar = self.pq_index.search(emb, k=self._pq_k)
            for node_id, dist in similar:
                cand_url = self._id_to_url[node_id] if node_id < len(self._id_to_url) else str(node_id)
                candidates.append({'url': cand_url, 'type': 'pq', 'score': 1.0 / (1.0 + dist)})
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

    def _get_common_neighbors(self, entity: str, limit: int) -> list[dict]:
        """Placeholder pro get_common_neighbors - wrapper pro relationship_discovery."""
        if hasattr(self.rel_engine, 'get_common_neighbors'):
            try:
                return self.rel_engine.get_common_neighbors(entity, limit=limit)
            except Exception:
                pass
        return []

    def _get_entity_embedding(self, entity: str):
        """Placeholder pro get_entity_embedding - wrapper pro relationship_discovery."""
        if hasattr(self.rel_engine, 'get_entity_embedding'):
            try:
                emb = self.rel_engine.get_entity_embedding(entity)
                if emb is not None:
                    return emb
            except Exception:
                pass
        if MLX_AVAILABLE:
            return mx.random.normal(64)
        return np.random.normal(size=64).astype(np.float32)

    def _extract_features_batch(self, candidates: list[dict]):
        """Extrahuje feature vektory pro reranker. Vrací (n, RERANKER_DIM)."""
        features = []
        for i, c in enumerate(candidates):
            rank_norm = (i + 1) / len(candidates)
            type_id = {'graph': 0, 'pq': 1, 'pattern': 2}.get(c.get('type', 'other'), 3)
            type_emb_np = np.zeros(4, dtype=np.float32)
            type_emb_np[type_id] = 1.0
            stage_score_np = np.array([c.get('score', 0.5)], dtype=np.float32)
            bandit_context = self._get_bandit_context_vector(c)
            feat_np = np.concatenate([[rank_norm], type_emb_np, stage_score_np, bandit_context]).astype(np.float32)
            if MLX_AVAILABLE:
                features.append(mx.array(feat_np))
            else:
                features.append(feat_np)
        if MLX_AVAILABLE:
            return mx.stack(features)
        return np.stack(features)

    def _get_bandit_context_vector(self, candidate: dict) -> np.ndarray:
        """Vrací feature vector pro bandit (numpy array float32, dim BANDIT_DIM)."""
        entity = candidate.get('entity', '')
        entity_emb = self._get_entity_embedding(entity)
        if entity_emb is not None:
            if MLX_AVAILABLE and isinstance(entity_emb, mx.array):
                entity_emb_np = entity_emb.astype(np.float32)
            else:
                entity_emb_np = np.array(entity_emb, dtype=np.float32)
        else:
            entity_emb_np = np.zeros(64, dtype=np.float32)
        domain = self._extract_domain(candidate['url'])
        quality_np = np.array([len(domain) / 100.0, 0.5, 0.5], dtype=np.float32)
        task_emb_np = np.array(self._current_task_embedding, dtype=np.float32)
        return np.concatenate([entity_emb_np, quality_np, task_emb_np])

    def _extract_domain(self, url: str) -> str:
        from urllib.parse import urlparse
        return urlparse(url).netloc

    def _apply_budget(self, candidates: list[dict]) -> list[dict]:
        """Omezí kandidáty podle aktuálních budgetů (network)."""
        total_bytes = sum((c.get('estimated_bytes', c.get('size', 1024 * 1024)) for c in candidates))
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

    def _compute_ucb(self, arm_id: str, x: np.ndarray) -> float:
        """
        Spočítá UCB skóre pro daný kontext.
        Pokud rameno ještě neexistuje, inicializuje ho s A_inv = (1/λ)I, b=0.

        F184F: bounded arm creation — při překročení MAX_BANDIT_ARMS
        je nejstarší arm (FIFO) odstraněn před vytvořením nového.
        """
        x64 = x.astype(np.float64, copy=False)
        if arm_id not in self.bandit_arms:
            if len(self.bandit_arms) >= MAX_BANDIT_ARMS:
                oldest_arm = next(iter(self.bandit_arms))
                del self.bandit_arms[oldest_arm]
                logger.debug(f'[F184F] bandit_arms evicting oldest arm: {oldest_arm} (MAX={MAX_BANDIT_ARMS})')
            d = len(x64)
            self.bandit_arms[arm_id] = {'A': np.eye(d, dtype=np.float64) * self.lambda_prior, 'b': np.zeros(d, dtype=np.float64), 'A_inv': np.eye(d, dtype=np.float64) / self.lambda_prior}
        arm = self.bandit_arms[arm_id]
        if arm['A_inv'] is None:
            arm['A_inv'] = np.linalg.inv(arm['A'])
        A_inv = arm['A_inv']
        theta = A_inv @ arm['b']
        mean = x64 @ theta
        var = x64 @ A_inv @ x64
        return mean + self.alpha * np.sqrt(max(var, 0))

    def _update_bandit(self, arm_id: str, x: np.ndarray, reward: float):
        """
        LinUCB update pomocí Sherman–Morrison (levnější).

        F184F: bounded arm creation — konzistentní s _compute_ucb.
        Pokud arm_id neexistuje a jsme na limitu, evict oldest first.
        """
        x64 = x.astype(np.float64, copy=False)
        if arm_id not in self.bandit_arms:
            if len(self.bandit_arms) >= MAX_BANDIT_ARMS:
                oldest_arm = next(iter(self.bandit_arms))
                del self.bandit_arms[oldest_arm]
                logger.debug(f'[F184F] bandit_arms evicting oldest arm: {oldest_arm} (MAX={MAX_BANDIT_ARMS})')
            d = len(x64)
            self.bandit_arms[arm_id] = {'A': np.eye(d, dtype=np.float64) * self.lambda_prior, 'b': np.zeros(d, dtype=np.float64), 'A_inv': np.eye(d, dtype=np.float64) / self.lambda_prior}
        arm = self.bandit_arms[arm_id]
        A_inv = arm['A_inv']
        x_np = x64.reshape(-1, 1)
        A_inv_x = A_inv @ x_np
        denominator = 1 + (x_np.T @ A_inv_x).item()
        if denominator > 1e-08:
            arm['A_inv'] = A_inv - A_inv_x @ A_inv_x.T / denominator
        arm['A'] += np.outer(x64, x64)
        arm['b'] += reward * x64

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
            reward = -self.lambda_waste * 0.1
            self._update_bandit(info['arm_id'], info['context'], reward)
            self.prefetch_stats[info['arm_id']]['misses'] += 1

    async def _expire_loop(self):
        """Background loop pro pravidelné spouštění expirace."""
        try:
            while not self._stop_event.is_set():
                await asyncio.sleep(3600)
                await self._expire_scheduled()
        except asyncio.CancelledError:
            logger.info('Expire loop cancelled')
            await self._expire_scheduled()

    def register_node_url(self, node_id: int, url: str):
        """
        Registruje node_id → url mapping.

        F184F: _id_to_url list BOUNDED na MAX_URL_MAP.
        Pokud node_id přesáhne limit, registrace je no-op (fail-safe).
        """
        if len(self._url_to_id) >= self._max_url_map:
            logger.debug(f'[F184F] register_node_url: at max ({self._max_url_map}), skipping {url}')
            return
        while len(self._id_to_url) <= node_id:
            if len(self._id_to_url) >= self._max_url_map:
                logger.debug(f'[F285] _id_to_url at max ({self._max_url_map}), skipping {url}')
                return
            self._id_to_url.append(None)
        self._id_to_url[node_id] = url
        self._url_to_id[url] = node_id
        if len(self._url_to_id) > self._max_url_map:
            self._url_to_id.popitem(last=False)