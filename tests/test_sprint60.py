"""
Testy pro Sprint 60 – HTN plánování, cost model, explainer a hypothesis.
"""

import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

# Testuje se pouze pokud je MLX dostupný
try:
    import mlx.core as mx
    MLX_AVAILABLE = True
except ImportError:
    MLX_AVAILABLE = False

pytestmark = pytest.mark.skipif(not MLX_AVAILABLE, reason="MLX not available")


class TestResourceGovernor:
    """Testy pro ResourceGovernor."""

    def test_priority_enum(self):
        """Test priority enum hodnot."""
        from hledac.universal.core.resource_governor import Priority
        assert Priority.CRITICAL.value == "CRITICAL"
        assert Priority.HIGH.value == "HIGH"
        assert Priority.NORMAL.value == "NORMAL"
        assert Priority.LOW.value == "LOW"

    def test_governor_init(self):
        """Test inicializace ResourceGovernor."""
        from hledac.universal.core.resource_governor import Priority, ResourceGovernor
        gov = ResourceGovernor(memory_high_water_mb=5000, thermal_threshold=80.0)
        assert gov.high_water == 5000
        assert gov.thermal_threshold == 80.0
        assert gov._priority_factor[Priority.CRITICAL] == 1.2
        assert gov._priority_factor[Priority.LOW] == 0.7

    def test_can_afford_sync_no_resources(self):
        """Test can_afford_sync když nejsou dostupné zdroje."""
        from hledac.universal.core.resource_governor import Priority, ResourceGovernor

        with patch('hledac.universal.core.resource_governor.psutil.virtual_memory') as mock_mem:
            mock_mem.return_value = MagicMock(used=7000 * 1024 * 1024)  # 7GB used

            gov = ResourceGovernor(memory_high_water_mb=6000)
            result = gov.can_afford_sync({'ram_mb': 500}, Priority.NORMAL)
            assert result is False

    def test_can_afford_sync_with_resources(self):
        """Test can_afford_sync když jsou dostupné zdroje."""
        from hledac.universal.core.resource_governor import Priority, ResourceGovernor

        with patch('hledac.universal.core.resource_governor.psutil.virtual_memory') as mock_mem:
            mock_mem.return_value = MagicMock(used=2000 * 1024 * 1024)  # 2GB used

            gov = ResourceGovernor(memory_high_water_mb=6000)
            result = gov.can_afford_sync({'ram_mb': 100}, Priority.NORMAL)
            assert result is True

    @pytest.mark.asyncio
    async def test_reserve_context_manager(self):
        """Test async context manager pro rezervaci."""
        from hledac.universal.core.resource_governor import Priority, ResourceGovernor

        with patch('hledac.universal.core.resource_governor.psutil.virtual_memory') as mock_mem:
            mock_mem.return_value = MagicMock(used=2000 * 1024 * 1024)

            gov = ResourceGovernor(memory_high_water_mb=6000)

            async with gov.reserve({'ram_mb': 100}, Priority.NORMAL) as res:
                assert res is not None
                assert gov._active_tasks == 1

            assert gov._active_tasks == 0


class TestCostModel:
    """Testy pro AdaptiveCostModel."""

    def test_online_ridge(self):
        """Test online ridge regression."""
        from hledac.universal.planning.cost_model import OnlineRidge

        ridge = OnlineRidge(n_features=10, alpha=1.0)

        # Update s jedním vzorkem
        x = np.random.randn(10)
        y = 5.0
        ridge.update(x, y)

        # Predikce
        pred = ridge.predict(x)
        assert isinstance(pred, float)

    def test_running_normalizer(self):
        """Test running normalizer."""
        from hledac.universal.planning.cost_model import RunningNormalizer

        norm = RunningNormalizer(dim=10)

        x = np.random.randn(10)
        norm.update(x)

        normalized = norm.normalize(x)
        assert normalized.shape == (10,)

    def test_adaptive_cost_model_init(self):
        """Test inicializace AdaptiveCostModel."""
        from hledac.universal.planning.cost_model import AdaptiveCostModel

        model = AdaptiveCostModel(
            governor=None,
            evidence_log=None,
            feature_dim=64,
            hidden_dim=32
        )

        assert model.feature_dim == 64
        assert model.hidden_dim == 32
        assert len(model.baseline) == 4
        assert model.ssm_ready is False

    def test_build_features(self):
        """Test build features."""
        from hledac.universal.planning.cost_model import AdaptiveCostModel

        model = AdaptiveCostModel(None, None, feature_dim=64)

        feat = model._build_features('fetch', {'url': 'http://test.com'}, {'active_tasks': 2, 'rss_gb': 4.0, 'avg_latency': 0.5})

        assert feat.shape == (64,)
        assert feat[0] == 1.0  # fetch = 0

    def test_predict(self):
        """Test predikce."""
        from hledac.universal.planning.cost_model import AdaptiveCostModel

        model = AdaptiveCostModel(None, None, feature_dim=16)

        result = model.predict('fetch', {'url': 'http://test.com'}, {'active_tasks': 1, 'rss_gb': 3.0, 'avg_latency': 0.2})

        assert len(result) == 5  # 4 outputs + uncertainty
        assert isinstance(result[0], float)

    @pytest.mark.asyncio
    async def test_update(self):
        """Test update cost model."""
        from hledac.universal.planning.cost_model import AdaptiveCostModel

        model = AdaptiveCostModel(None, None, feature_dim=16)

        await model.update(
            'fetch',
            {'url': 'http://test.com'},
            {'active_tasks': 1, 'rss_gb': 3.0, 'avg_latency': 0.2},
            (1.0, 50.0, 0.5, 1.0)
        )

        assert model.baseline_ready is True


class TestTaskCache:
    """Testy pro TaskCache."""

    @pytest.mark.asyncio
    async def test_cache_put_get(self):
        """Test put a get."""
        from hledac.universal.planning.task_cache import TaskCache

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = TaskCache(f"{tmpdir}/test_cache.lmdb", max_size_mb=10)

            # Put
            await cache.put("test_key", {"result": "data"}, model_version=1)

            # Get
            result = await cache.get("test_key", model_version=1)
            assert result == {"result": "data"}

            # Wrong version
            result = await cache.get("test_key", model_version=2)
            assert result is None

            await cache.close()

    @pytest.mark.asyncio
    async def test_cache_miss(self):
        """Test cache miss."""
        from hledac.universal.planning.task_cache import TaskCache

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = TaskCache(f"{tmpdir}/test_cache.lmdb", max_size_mb=10)

            result = await cache.get("nonexistent", model_version=1)
            assert result is None

            await cache.close()


class TestSearch:
    """Testy pro anytime beam search."""

    def test_search_node(self):
        """Test SearchNode."""
        from hledac.universal.planning.search import SearchNode

        state = {'pending': [], 'done': []}
        node = SearchNode(state, cost=1.0, value=2.0)

        assert node.cost == 1.0
        assert node.value == 2.0
        assert node.score == 0.0

    def test_anytime_beam_search_simple(self):
        """Test simple beam search."""
        from hledac.universal.core.resource_governor import ResourceGovernor
        from hledac.universal.planning.search import anytime_beam_search

        gov = ResourceGovernor(memory_high_water_mb=6000)

        # Jednoduchý problém: reach goal
        initial_state = {'pending': [1, 2, 3], 'done': [], 'cost': 0.0, 'value': 0.0}

        def goal_check(state):
            return len(state['pending']) == 0

        def expand(state):
            if not state['pending']:
                return []
            item = state['pending'][0]
            new_state = {
                'pending': state['pending'][1:],
                'done': state['done'] + [item],
                'cost': state['cost'] + 1.0,
                'value': state['value'] + 1.0
            }
            return [(item, new_state, 1.0, 10.0, 1.0, 1.0)]

        def heuristic(state):
            # Vracíme zbývající hodnotu - čím méně úkolů, tím menší hodnota zbývá
            return 0.0, float(len(state['pending'])), float(len(state['pending']))

        plan = anytime_beam_search(
            initial_state=initial_state,
            goal_check=goal_check,
            expand=expand,
            heuristic=heuristic,
            governor=gov,
            time_budget=10.0,
            ram_budget_mb=1000.0,
            net_budget_mb=100.0,
            beam_width=5
        )

        assert plan is not None


class TestHTNPlanner:
    """Testy pro HTNPlanner."""

    @pytest.fixture
    def mock_components(self):
        """Vytvoří mock komponenty."""
        from hledac.universal.core.resource_governor import ResourceGovernor
        from hledac.universal.planning.cost_model import AdaptiveCostModel
        from hledac.universal.planning.slm_decomposer import SLMDecomposer
        from hledac.universal.planning.task_cache import TaskCache

        gov = ResourceGovernor(memory_high_water_mb=6000)
        cost_model = AdaptiveCostModel(gov, None, feature_dim=16)

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = TaskCache(f"{tmpdir}/test_cache.lmdb")
            decomposer = SLMDecomposer(gov, cache)

            return {
                'governor': gov,
                'cost_model': cost_model,
                'decomposer': decomposer,
                'cache': cache,
                'scheduler': MagicMock(),
                'evidence_log': MagicMock()
            }

    def test_planner_init(self, mock_components):
        """Test inicializace HTNPlanner."""
        from hledac.universal.planning.htn_planner import HTNPlanner

        planner = HTNPlanner(
            governor=mock_components['governor'],
            cost_model=mock_components['cost_model'],
            decomposer=mock_components['decomposer'],
            scheduler=mock_components['scheduler'],
            evidence_log=mock_components['evidence_log']
        )

        assert planner.governor is not None
        assert len(planner._task_types) == 0

    def test_register_task_type(self, mock_components):
        """Test registrace typu úkolu."""
        from hledac.universal.planning.htn_planner import HTNPlanner

        planner = HTNPlanner(
            governor=mock_components['governor'],
            cost_model=mock_components['cost_model'],
            decomposer=mock_components['decomposer'],
            scheduler=mock_components['scheduler'],
            evidence_log=mock_components['evidence_log']
        )

        def dummy_expander(task, context):
            return []

        planner.register_task_type('fetch', dummy_expander, is_primitive=True)

        assert 'fetch' in planner._task_types
        assert planner._task_types['fetch']['primitive'] is True


class TestHypothesis:
    """Testy pro hypothesis moduly."""

    def test_dempster_shafer_init(self):
        """Test inicializace DempsterShafer."""
        from hledac.universal.hypothesis.dempster_shafer import DempsterShafer

        ds = DempsterShafer({'h1', 'h2', 'h3'})

        assert ds.unknown == 1.0
        assert ds.conflict == 0.0
        assert len(ds.masses) == 3

    def test_dempster_shafer_add_evidence(self):
        """Test přidávání evidence."""
        from hledac.universal.hypothesis.dempster_shafer import DempsterShafer

        ds = DempsterShafer({'h1', 'h2'})

        ds.add_evidence('h1', 0.6)

        assert ds.masses['h1'] > 0

    def test_dempster_shafer_belief(self):
        """Test belief výpočet."""
        from hledac.universal.hypothesis.dempster_shafer import DempsterShafer

        ds = DempsterShafer({'h1', 'h2'})

        ds.add_evidence('h1', 0.5)

        assert ds.belief('h1') > 0
        assert ds.belief() > 0

    def test_eig_calculator(self):
        """Test EIG calculator."""
        from hledac.universal.hypothesis.dempster_shafer import DempsterShafer
        from hledac.universal.hypothesis.eig import EIGCalculator

        calc = EIGCalculator()

        hypotheses = [DempsterShafer({'h1', 'h2'}), DempsterShafer({'h1', 'h2'})]
        hypotheses[0].add_evidence('h1', 0.6)
        hypotheses[1].add_evidence('h2', 0.4)

        eig = calc.compute_eig(hypotheses, {'action': 'test'})

        assert isinstance(eig, float)


class TestExplainer:
    """Testy pro explainer moduly."""

    @pytest.fixture
    def mock_graph_rag(self):
        """Vytvoří mock GraphRAG."""
        graph = MagicMock()
        graph.multi_hop_search = AsyncMock(return_value={
            'nodes': ['A', 'B', 'C'],
            'edges': [('A', 'B'), ('B', 'C')]
        })
        return graph

    def test_fast_explainer_init(self, mock_graph_rag):
        """Test inicializace FastExplainer."""
        from hledac.universal.core.resource_governor import ResourceGovernor
        from hledac.universal.knowledge.explainer.fast import FastExplainer

        gov = ResourceGovernor(memory_high_water_mb=6000)
        explainer = FastExplainer(mock_graph_rag, gov)

        assert explainer.graph_rag is not None
        assert explainer.governor is not None

    @pytest.mark.asyncio
    async def test_fast_explainer_explain_path(self, mock_graph_rag):
        """Test explain_path."""
        from hledac.universal.core.resource_governor import ResourceGovernor
        from hledac.universal.knowledge.explainer.fast import FastExplainer

        gov = ResourceGovernor(memory_high_water_mb=6000)
        explainer = FastExplainer(mock_graph_rag, gov)

        result = await explainer.explain_path('A', 'C', max_hops=3)

        assert isinstance(result, list)

    def test_deep_explainer_init(self):
        """Test inicializace DeepExplainer."""
        from hledac.universal.core.resource_governor import ResourceGovernor
        from hledac.universal.knowledge.explainer.deep import DeepExplainer

        gov = ResourceGovernor(memory_high_water_mb=6000)
        gnn = MagicMock()

        explainer = DeepExplainer(gnn, gov)

        assert explainer.gnn is not None
        assert explainer.governor is not None


class TestSLMDecomposer:
    """Testy pro SLMDecomposer."""

    @pytest.fixture
    def decomposer(self):
        """Vytvoří SLMDecomposer."""
        from hledac.universal.core.resource_governor import ResourceGovernor
        from hledac.universal.planning.slm_decomposer import SLMDecomposer
        from hledac.universal.planning.task_cache import TaskCache

        gov = ResourceGovernor(memory_high_water_mb=6000)

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = TaskCache(f"{tmpdir}/test_cache.lmdb")
            decomposer = SLMDecomposer(gov, cache, model_name="test")

            yield decomposer

            import asyncio
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    pass
            except Exception:
                pass

    def test_decomposer_init(self, decomposer):
        """Test inicializace SLMDecomposer."""
        assert decomposer.model_name == "test"
        assert decomposer._model is None

    def test_rule_based_fallback(self, decomposer):
        """Test rule-based fallback."""
        result = decomposer._rule_based_fallback("test task", {"context": "data"})

        assert isinstance(result, list)
        assert len(result) > 0
        assert result[0]['type'] == 'fetch'

    def test_cache_key(self, decomposer):
        """Test generování cache key."""
        key1 = decomposer._cache_key("task1", {"a": 1})
        key2 = decomposer._cache_key("task1", {"a": 1})
        key3 = decomposer._cache_key("task2", {"a": 1})

        assert key1 == key2
        assert key1 != key3
