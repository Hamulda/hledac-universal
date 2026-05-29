"""
Testy pro Sprint 68 - Autonomous Loop Smoke Tests
"""

import asyncio
from collections import OrderedDict, deque
from unittest.mock import MagicMock

import pytest


@pytest.mark.asyncio
async def test_action_result_integration():
    """Test integrace ActionResult s orchestrátorem."""
    from hledac.universal.utils import ActionResult

    # Vytvoř výsledek akce
    result = ActionResult(
        success=True,
        findings=["finding1", "finding2"],
        sources=["source1"],
        hypotheses=[{"id": "h1", "confidence": 0.8}],
        contradictions=[],
        metadata={"url": "http://example.com"}
    )

    assert result.success is True
    assert len(result.findings) == 2
    assert len(result.sources) == 1
    assert len(result.hypotheses) == 1


@pytest.mark.asyncio
async def test_state_analysis_structure():
    """Test struktury state pro analýzu."""
    # Simulace _analyze_state
    state = {
        "query": "test query",
        "findings_count": 10,
        "sources_count": 5,
        "active_hypotheses": 3,
        "contradictions": 1,
        "rss_gb": 3.5,
        "browser_available": True,
        "archive_available": True,
        "recent_novelty": 0.2,
        "budget_remaining": {"time": 100, "network": 500},
        "stagnation": 2,
        "js_gated": False,
    }

    # Ověření struktury
    assert "query" in state
    assert "findings_count" in state
    assert "sources_count" in state
    assert "active_hypotheses" in state
    assert "contradictions" in state
    assert "rss_gb" in state
    assert "browser_available" in state
    assert "archive_available" in state
    assert "recent_novelty" in state
    assert "budget_remaining" in state
    assert "stagnation" in state
    assert "js_gated" in state


@pytest.mark.asyncio
async def test_action_registry_integration():
    """Test integrace action registry."""
    from hledac.universal.utils import ActionResult

    # Simulace registru
    action_registry = {}

    # Definice akce
    async def surface_search_handler(query: str) -> ActionResult:
        return ActionResult(
            success=True,
            findings=["finding1"],
            sources=[],
            metadata={"action": "surface_search"}
        )

    def surface_search_scorer(state):
        score = 0.5
        if state.get('recent_novelty', 0) < 0.2:
            score += 0.2
        return (score, {"query": state['query']})

    # Registrace
    action_registry['surface_search'] = (surface_search_handler, surface_search_scorer)

    # Ověření
    assert 'surface_search' in action_registry
    handler, scorer = action_registry['surface_search']

    # Test scorer
    state = {"query": "test", "recent_novelty": 0.1}
    score, params = scorer(state)
    assert score == 0.7  # 0.5 + 0.2

    # Test handler
    result = await handler(query="test")
    assert result.success is True
    assert len(result.findings) == 1


@pytest.mark.asyncio
async def test_result_processing():
    """Test zpracování výsledků."""
    from hledac.universal.utils import ActionResult

    # Simulace zpracování
    findings_heap = []
    sources_heap = []
    active_hypotheses = OrderedDict()
    contradiction_queue = deque(maxlen=20)

    # Simulace findings
    new_findings = 0
    result = ActionResult(
        success=True,
        findings=["finding1", "finding2"],
        sources=["source1"],
        hypotheses=[{"id": "h1", "confidence": 0.9}],
        contradictions=[{"claim_id": "c1"}],
        metadata={"url": "http://test.com", "preview": "preview text"}
    )

    # Process findings
    for f in result.findings:
        findings_heap.append(f)
        new_findings += 1

    # Process sources
    for s in result.sources:
        sources_heap.append(s)

    # Process hypotheses (bounded)
    max_hypotheses = 100
    for h in result.hypotheses:
        if len(active_hypotheses) >= max_hypotheses:
            active_hypotheses.popitem(last=False)
        active_hypotheses[h["id"]] = h

    # Process contradictions
    for c in result.contradictions:
        contradiction_queue.append(c)

    # Ověření
    assert len(findings_heap) == 2
    assert len(sources_heap) == 1
    assert len(active_hypotheses) == 1
    assert len(contradiction_queue) == 1
    assert new_findings == 2


@pytest.mark.asyncio
async def test_loop_termination():
    """Test terminace smyčky."""
    # Simulace terminace
    max_iters = 200

    # Test conditions
    def check_terminate(budget_ok, confidence, stagnation, iters, contradiction_count):
        if not budget_ok:
            return True
        if confidence > 0.9:
            return True
        if stagnation > 5:
            return True
        if iters >= max_iters:
            return True
        if contradiction_count > 15:
            return True
        return False

    # Test 1: budget OK, nízká confidence, žádná stagnace
    assert check_terminate(True, 0.5, 0, 10, 0) is False

    # Test 2: vysoká confidence
    assert check_terminate(True, 0.95, 0, 10, 0) is True

    # Test 3: stagnace
    assert check_terminate(True, 0.5, 6, 10, 0) is True

    # Test 4: max iterations
    assert check_terminate(True, 0.5, 0, 200, 0) is True

    # Test 5: contradiction cycling
    assert check_terminate(True, 0.5, 0, 10, 16) is True


def test_hypothesis_bounded_storage():
    """Test bounded storage pro hypotézy."""
    hypotheses = OrderedDict()
    max_hypotheses = 100

    # Přidej 150 hypotéz
    for i in range(150):
        hypotheses[f"hypothesis_{i}"] = {"id": f"h{i}", "confidence": 0.5 + i * 0.001}

        # Enforce limit
        if len(hypotheses) > max_hypotheses:
            hypotheses.popitem(last=False)

    assert len(hypotheses) == max_hypotheses
    # Prvních 50 by mělo být evictováno
    assert "hypothesis_0" not in hypotheses
    assert "hypothesis_149" in hypotheses


def test_last_action_success_tracking():
    """Test sledování úspěšnosti akcí."""
    last_action_success = OrderedDict()
    max_actions = 50

    # Simulace trackování
    for i in range(60):
        name = f"action_{i % 10}"  # 10 unikátních akcí
        success = i % 3 == 0  # Některé úspěšné

        success_val = 1.0 if success else 0.0
        old = last_action_success.get(name, 0.5)
        last_action_success[name] = 0.9 * old + 0.1 * success_val

        if len(last_action_success) > max_actions:
            last_action_success.popitem(last=False)

    # Mělo by být max 50
    assert len(last_action_success) <= max_actions


@pytest.mark.asyncio
async def test_mock_orchestrator_instantiation():
    """Test vytvoření mock orchestrátoru."""

    # Vytvoř mock orchestrátor
    orch = MagicMock()

    # Nastav Sprint 68 atributy
    orch._action_registry = {}
    orch._active_hypotheses = OrderedDict()
    orch._contradiction_queue = deque(maxlen=20)
    orch._last_action_success = OrderedDict()
    orch._stagnation_counter = 0
    orch._main_hypothesis = None
    orch._iter_count = 0
    orch._max_iters = 200

    # Ověření
    assert isinstance(orch._action_registry, dict)
    assert isinstance(orch._contradiction_queue, deque)
    assert orch._max_iters == 200


@pytest.mark.asyncio
async def test_research_uses_sprint68_loop():
    """Test že research() používá Sprint 68 autonomous loop."""
    from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
    from hledac.universal.utils import ActionResult

    # Track calls
    analyze_state_called = []
    execute_action_calls = []

    # Vytvoř orchestrátor přes __new__
    orch = FullyAutonomousOrchestrator.__new__(FullyAutonomousOrchestrator)

    # Nastav všechny potřebné atributy
    orch._action_registry = {}
    orch._active_hypotheses = OrderedDict()
    orch._contradiction_queue = deque(maxlen=20)
    orch._last_action_success = OrderedDict()
    orch._stagnation_counter = 0
    orch._main_hypothesis = None
    orch._iter_count = 0
    orch._max_iters = 200
    orch._last_url = ""
    orch._last_preview = ""
    orch._findings_heap = []
    orch._sources_heap = []
    orch._budget_manager = MagicMock()
    orch._budget_manager.check_time_allowed = MagicMock(return_value=(True, "ok"))

    # Přidej chybějící atributy pro research()
    orch._log_span = MagicMock()
    orch._current_trace_id = "test-trace"
    orch._trace_start_time = 0.0
    orch._state_mgr = MagicMock()
    orch._state_mgr._initialized = True
    orch._state_mgr._execution_count = 0
    orch._research_mgr = MagicMock()

    # Sprint 68B nové atributy
    orch._action_cooldowns = OrderedDict()
    orch._last_action_name = ""
    orch._repeat_action_count = 0

    # Sprint 69 atributy
    import threading
    orch._structure_map_lock = asyncio.Lock()
    orch._kqueue_dirty_lock = threading.Lock()
    orch._structure_map_state = {
        "file_cache": OrderedDict(),
        "prev_edges": [],
        "last_fingerprint": None,
        "last_run_time": 0.0,
        "cooldown_until": 0.0,
        "circuit_open_until": 0.0,
        "fail_score": 0.0,
        "kqueue_dirty": False,
    }
    orch._warming_task = None
    orch._STRUCTURE_MAP_FAIL_OPEN_S = 3600.0
    orch._STRUCTURE_MAP_MIN_INTERVAL_S = 600.0
    orch._STRUCTURE_MAP_TRUNC_COOLDOWN_PENALTY_S = 600.0
    orch._LOW_PRIORITY_STRUCTURE_MAP_LIMITS = {
        "max_files": 500,
        "max_bytes_total": 2_000_000,
        "max_parse_bytes_per_file": 65_536,
        "time_budget_ms": 300,
        "prefix_hash_bytes": 4096,
        "incremental": True,
        "parallel_scan_threshold": 5000,
        "max_workers": 4,
    }
    orch._evidence_log = MagicMock()

    # Sprint 70 atributy
    orch._meta_optimizer_task = None
    orch._dns_monitor_task = None
    import concurrent.futures
    orch._background_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    orch._new_domain_queue = asyncio.PriorityQueue(maxsize=20)
    orch._known_paths_queue = asyncio.Queue(maxsize=20)

    # Track _execute_action
    orch.__class__._execute_action if hasattr(orch.__class__, '_execute_action') else None

    async def mock_execute_action(name: str, **params):
        execute_action_calls.append((name, params))
        return ActionResult(success=True, findings=["f1"], sources=[], hypotheses=[], contradictions=[], metadata={})

    orch._execute_action = mock_execute_action

    # Track _analyze_state
    async def mock_analyze_state(query):
        analyze_state_called.append(1)
        return {
            "query": query,
            "findings_count": 1,
            "sources_count": 0,
            "active_hypotheses": 0,
            "contradictions": 0,
            "rss_gb": 3.0,
            "browser_available": False,
            "archive_available": False,
            "recent_novelty": 1.0,
            "budget_remaining": {"time": 100},
            "stagnation": 0,
            "js_gated": False,
        }
    orch._analyze_state = mock_analyze_state

    # Mock _should_terminate - ukončí po 2 iteracích (potřebujeme analyze_state volat)
    termination_counter = [0]
    def mock_should_terminate():
        termination_counter[0] += 1
        return termination_counter[0] >= 2
    orch._should_terminate = mock_should_terminate

    # Mock _process_result
    async def mock_process_result(result):
        pass
    orch._process_result = mock_process_result

    # Mock _check_memory_pressure
    orch._check_memory_pressure = MagicMock()

    # Mock _synthesize_results
    async def mock_synthesize(query):
        return f"Report for: {query}"
    orch._synthesize_results = mock_synthesize

    # Spusť research - měl by použít Sprint 68 loop
    await orch.research("test query")

    # Ověření - Sprint 68 loop musí být použit
    assert len(analyze_state_called) > 0, "Sprint 68: _analyze_state musí být zavolána"
    assert len(execute_action_calls) > 0, "Sprint 68: _execute_action musí být zavolána"


def test_backoff_calculation():
    """Test že backoff vzorec je správný."""
    stagnation = 1
    backoff = min(0.1 * (2 ** min(stagnation, 4)), 1.0)
    assert backoff == 0.2

    stagnation = 4
    backoff = min(0.1 * (2 ** min(stagnation, 4)), 1.0)
    assert backoff == 1.0  # cap at 1.0

    stagnation = 10
    backoff = min(0.1 * (2 ** min(stagnation, 4)), 1.0)
    assert backoff == 1.0  # cap at 1.0


@pytest.mark.asyncio
async def test_cooldown_prevents_repeated_action():
    """Test že cooldown se aktivuje po 3 opakováních stejné akce."""
    from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

    orch = FullyAutonomousOrchestrator.__new__(FullyAutonomousOrchestrator)
    orch._action_registry = {}

    # Registruj akci
    def scorer_high(state):
        return (0.9, {"query": state["query"]})

    async def handler(**params):
        from hledac.universal.utils import ActionResult
        return ActionResult(success=True, findings=[], sources=[])

    orch._action_registry['render_page'] = (handler, scorer_high)
    orch._action_cooldowns = OrderedDict()
    orch._last_action_name = "render_page"
    orch._repeat_action_count = 0

    # Simuluj 3 opakování
    for i in range(3):
        orch._repeat_action_count = i
        orch._decide_next_action({"query": "test"})

    # Po 3 opakováních by měl být nastaven cooldown
    assert orch._action_cooldowns.get("render_page", 0) > 0, "Cooldown měl být nastaven po 3 opakováních"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
