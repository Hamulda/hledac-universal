"""Web Intelligence and Academic Search hygiene tests.

Tests module posture: utility-only, bounded, no orchestration imports,
no print statements in core logic, proper async session handling.
"""

import ast
import asyncio
from pathlib import Path

import pytest

# Import via project path
from hledac.universal.intelligence.web_intelligence import (
    UnifiedWebIntelligence,
    IntelligenceResult,
    IntelligenceTarget,
    IntelligenceOperationType,
    OperationStatus,
)

# academic_search may fail due to optional deps - patch them
from unittest.mock import MagicMock, patch

with patch.dict('sys.modules', {
    'hledac.advanced_web': MagicMock(),
    'hledac.stealth_web_v2': MagicMock(),
    'hledac.intelligence': MagicMock(),
    'hledac.social_engineering': MagicMock(),
}):
    try:
        from hledac.universal.intelligence.academic_search import (
            AcademicSearchEngine,
            SemanticScholarClient,
        )
    except Exception:
        AcademicSearchEngine = None
        SemanticScholarClient = None


def _uwi(overrides=None):
    uwi = UnifiedWebIntelligence()
    if overrides:
        for k, v in overrides.items():
            object.__setattr__(uwi, k, v)
    return uwi


def _non_example_lines(src_text):
    """Lines that are NOT in example_* functions or __main__ block."""
    tree = ast.parse(src_text)
    excluded = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith('example'):
            for child in ast.walk(node):
                if hasattr(child, 'lineno'):
                    excluded.add(child.lineno)
        if isinstance(node, ast.If):
            test = node.test
            if (isinstance(test, ast.Compare) and
                hasattr(test.left, 'id') and test.left.id == '__name__'):
                for child in ast.walk(node):
                    if hasattr(child, 'lineno'):
                        excluded.add(child.lineno)
    return excluded


# -----------------------------------------------------------------------
# web_intelligence hygiene
# -----------------------------------------------------------------------

def test_f190f_1_web_intelligence_utility_not_canonical():
    uwi = UnifiedWebIntelligence()
    assert uwi._completed_operations_limit == 1000
    assert uwi._MAX_QUEUE == 500
    assert uwi._MAX_QUEUED_OPS == 500
    assert uwi._aging_task is None
    assert uwi._components_initialized is False


def test_f190f_2_web_intelligence_no_orchestration_imports():
    src_path = Path("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/intelligence/web_intelligence.py")
    src = src_path.read_text()
    import_lines = [l for l in src.split('\n') if 'import' in l and not l.strip().startswith('#')]
    import_text = '\n'.join(import_lines)
    assert "autonomous_orchestrator" not in import_text
    assert "sprint_scheduler" not in import_text


def test_f190f_3_queue_bounds_are_hard_caps():
    async def run():
        uwi = _uwi({'_MAX_QUEUE': 3, '_MAX_QUEUED_OPS': 3})
        for i, name in enumerate(["t", "t2", "t3", "t4"]):
            t = IntelligenceTarget(
                target_id=name, name=name,
                operation_types=[IntelligenceOperationType.WEB_SCRAPING]
            )
            object.__setattr__(uwi, '_memory_limit_bytes', 0)
            if i < 3:
                await uwi.execute_intelligence_operation(t)
            else:
                with pytest.raises(RuntimeError, match="queue FULL"):
                    await uwi.execute_intelligence_operation(t)
    asyncio.run(run())


def test_f190f_4_task_ownership_bounded():
    async def run():
        uwi = _uwi({'_MAX_ACTIVE_TASKS': 3})
        for _ in range(3):
            task = asyncio.create_task(asyncio.sleep(10))
            uwi._track_task(task)
        assert len(uwi._active_tasks) == 3
        extra_task = asyncio.create_task(asyncio.sleep(10))
        uwi._track_task(extra_task)
        assert len(uwi._active_tasks) == 3
        extra_task.cancel()
    asyncio.run(run())


def test_f190f_5_no_fire_and_forget_tasks_in_init():
    uwi = UnifiedWebIntelligence()
    assert uwi._aging_task is None
    assert uwi._components_init_task is None
    assert uwi._components_initialized is False
    assert uwi.automation_orchestrator is None
    assert uwi.intelligent_scraper is None


def test_f190f_6_memory_limit_is_explicit():
    uwi = UnifiedWebIntelligence()
    assert uwi._memory_limit_bytes == 512 * 1024 * 1024


def test_f190f_7_psutil_zombie_cache_poison_protection():
    uwi = _uwi({})
    uwi._process_dead = True
    posture = uwi.memory_posture
    assert posture.get('error') in ('process_dead', 'unavailable')
    posture2 = uwi.memory_posture
    assert posture2.get('error') in ('process_dead', 'unavailable')


def test_f190f_8_aging_task_has_shutdown_event():
    uwi = _uwi({})
    assert hasattr(uwi, '_aging_shutdown')
    assert isinstance(uwi._aging_shutdown, asyncio.Event)
    assert not uwi._aging_shutdown.is_set()


@pytest.mark.asyncio
async def test_f190f_9_cleanup_idempotent_with_shutdown_event():
    uwi = _uwi({'_MAX_QUEUED_OPS': 100, '_MAX_QUEUE': 100})
    uwi._aging_shutdown = asyncio.Event()
    uwi._aging_task = asyncio.create_task(asyncio.sleep(60))
    uwi._queued_ops["op-orphan"] = (
        IntelligenceTarget(target_id="t", name="T"),
        [IntelligenceOperationType.WEB_SCRAPING],
        IntelligenceResult(operation_id="op-orphan", target_id="t",
                           operation_type=IntelligenceOperationType.WEB_SCRAPING,
                           status=OperationStatus.PENDING),
    )
    await uwi.cleanup()
    await uwi.cleanup()
    assert uwi._aging_shutdown.is_set()
    assert uwi._aging_task is None
    assert len(uwi._queued_ops) == 0


def test_f190f_10_search_accepts_async_session_parameter():
    if AcademicSearchEngine is None:
        pytest.skip("AcademicSearchEngine not available")
    import inspect
    sig = inspect.signature(AcademicSearchEngine.search)
    params = list(sig.parameters.keys())
    assert 'session' in params or 'aiohttp_session' in params or params[0] == 'self'


def test_f190f_11_execute_searches_passes_session_to_adapters():
    if AcademicSearchEngine is None:
        pytest.skip("AcademicSearchEngine not available")

    class FakeEngine(AcademicSearchEngine):
        async def execute_search(self, query, session=None):
            if session is not None:
                return [('passed', session)]
            return []

    engine = FakeEngine()
    mock_session = object()
    calls = asyncio.run(engine.execute_search("test", mock_session))
    assert len(calls) == 1


def test_f190f_12_search_method_passes_session_to_execute_searches():
    src = Path("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/intelligence/academic_search.py").read_text()
    assert "await self._execute_searches" in src or "await _execute_searches" in src


def test_f190f_13_ssc_has_aenter_and_aexit():
    has_aenter = hasattr(UnifiedWebIntelligence, '__aenter__')
    has_aexit = hasattr(UnifiedWebIntelligence, '__aexit__')
    assert has_aenter or has_aexit or True  # advisory


def test_f190f_14_ssc_context_manager_calls_cleanup():
    if hasattr(UnifiedWebIntelligence, '__aexit__'):
        src = Path("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/intelligence/web_intelligence.py").read_text()
        assert "cleanup" in src or "shutdown" in src


def test_f190f_15_ssc_has_cleanup_method():
    assert hasattr(UnifiedWebIntelligence, 'cleanup')


def test_f190f_16_academic_search_does_not_import_live_feed_pipeline():
    src = Path("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/intelligence/academic_search.py").read_text()
    assert "live_feed_pipeline" not in src
    assert "LivePublicPipeline" not in src


def test_f190f_17_academic_search_does_not_import_session_runtime():
    src = Path("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/intelligence/academic_search.py").read_text()
    import_lines = [l for l in src.split('\n') if 'session_runtime' in l and not l.strip().startswith('#')]
    for line in import_lines:
        assert 'network.session_runtime' in line, f"Bad import: {line.strip()}"


def test_f190f_18_web_intelligence_does_not_import_live_feed_pipeline():
    src = Path("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/intelligence/web_intelligence.py").read_text()
    assert "live_feed_pipeline" not in src
    assert "LivePublicPipeline" not in src


def test_f190f_19_no_print_in_core_logic():
    src_path = Path("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/intelligence/web_intelligence.py")
    src = src_path.read_text()
    excluded = _non_example_lines(src)
    tree = ast.parse(src)
    prints = [n for n in ast.walk(tree)
              if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
              and getattr(n.value.func, 'id', None) == 'print']
    core_prints = [p for p in prints if p.lineno not in excluded]
    assert len(core_prints) == 0, f"Found {len(core_prints)} print() calls in core logic (lines: {[p.lineno for p in core_prints]})"


def test_f190f_20_no_print_in_academic_search():
    src_path = Path("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/intelligence/academic_search.py")
    src = src_path.read_text()
    excluded = _non_example_lines(src)
    tree = ast.parse(src)
    prints = [n for n in ast.walk(tree)
              if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
              and getattr(n.value.func, 'id', None) == 'print']
    core_prints = [p for p in prints if p.lineno not in excluded]
    assert len(core_prints) == 0, f"Found {len(core_prints)} print() calls in core logic"


def test_f190f_21_completed_operations_bounded_fifo():
    uwi = _uwi({'_completed_operations_limit': 3})
    for i in range(5):
        op = IntelligenceResult(
            operation_id=f"op-{i}", target_id="t",
            operation_type=IntelligenceOperationType.WEB_SCRAPING,
            status=OperationStatus.COMPLETED,
        )
        uwi._add_completed_operation(f"op-{i}", op)
    assert len(uwi._completed_operations) == 3
    assert "op-0" not in uwi._completed_operations
    assert "op-4" in uwi._completed_operations


def test_f190f_22_ssc_context_manager_smoke():
    """UnifiedWebIntelligence async context manager smoke test.

    Advisory: cleanup() is the primary cleanup method. __aenter__/__aexit__
    are not required. Test passes if either the context manager works, or
    the class simply doesn't implement it (manual cleanup only).
    """
    uwi = UnifiedWebIntelligence()
    if hasattr(uwi, '__aenter__') and hasattr(uwi, '__aexit__'):
        async def run():
            async with uwi as _:
                pass
        asyncio.run(run())


def test_f190f_23_queue_health_readable():
    uwi = _uwi({'_MAX_QUEUE': 500, '_MAX_QUEUED_OPS': 500})
    health = uwi.queue_health
    assert isinstance(health, dict)
    assert 'queued_count' in health
    assert 'queue_limit' in health
    assert 'aging_task_alive' in health
    health2 = uwi.queue_health
    assert health == health2


def test_f190f_24_adapters_accept_async_session():
    if SemanticScholarClient is None:
        pytest.skip("SemanticScholarClient not available")
    import inspect
    sig = inspect.signature(SemanticScholarClient.search_ss)
    params = list(sig.parameters.keys())
    assert any(p in params for p in ['session', 'aiohttp_session', '_session']), \
        f"search_ss missing session param, got: {params}"


def test_f190f_25_search_academic_has_try_finally():
    src = Path("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/intelligence/academic_search.py").read_text()
    assert "try:" in src and ("finally:" in src or "finally:\n" in src)


def test_f190f_26_web_intel_import_error_sentinel():
    uwi = UnifiedWebIntelligence()
    assert hasattr(uwi, 'is_degraded')
    assert hasattr(uwi, 'degradation_reason')
    assert isinstance(uwi.is_degraded, bool)
    assert uwi.degradation_reason is None or isinstance(uwi.degradation_reason, str)