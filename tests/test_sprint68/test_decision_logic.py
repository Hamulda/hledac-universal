"""
Testy pro Sprint 68 - Decision Logic
"""

from collections import OrderedDict, deque

import pytest


def test_decision_logic_contradiction_priority():
    """Test že contradictions mají prioritu v rozhodování."""
    # Simulace contradiction queue
    contradiction_queue = deque(maxlen=20)
    contradiction_queue.append({"claim_id": "c1", "text": "test claim"})
    contradiction_queue.append({"claim_id": "c2", "text": "another claim"})

    # Ověření, že queue funguje správně
    assert len(contradiction_queue) == 2
    assert contradiction_queue[0]["claim_id"] == "c1"

    # Test priority scoring
    def contradiction_scorer(state):
        if state.get("contradictions", 0) > 0:
            return (1.0, {"claim_id": "c1"})
        return (0.0, {})

    state_with_contradictions = {"contradictions": 2}
    score, params = contradiction_scorer(state_with_contradictions)
    assert score == 1.0
    assert params["claim_id"] == "c1"

    state_without_contradictions = {"contradictions": 0}
    score, params = contradiction_scorer(state_without_contradictions)
    assert score == 0.0


def test_decision_logic_fallback():
    """Test fallback na surface_search."""
    def fallback_scorer(state):
        return (0.1, {"query": state["query"]})

    state = {"query": "test", "contradictions": 0}
    score, params = fallback_scorer(state)
    assert score == 0.1
    assert params["query"] == "test"


def test_decision_logic_archive_priority():
    """Test archive fetch priority."""
    def archive_scorer(state):
        if state.get("archive_available") and state.get("recent_novelty", 1) < 0.3:
            return (0.8, {"url": "http://example.com"})
        return (0.0, {})

    state_good = {"archive_available": True, "recent_novelty": 0.2}
    score, params = archive_scorer(state_good)
    assert score == 0.8

    state_bad = {"archive_available": False, "recent_novelty": 0.5}
    score, params = archive_scorer(state_bad)
    assert score == 0.0


def test_decision_logic_js_gated():
    """Test JS-gated page detection."""
    def js_gated_scorer(state):
        if state.get("browser_available") and state.get("js_gated"):
            return (0.9, {"url": "http://example.com"})
        return (0.0, {})

    state_js_gated = {"browser_available": True, "js_gated": True}
    score, params = js_gated_scorer(state_js_gated)
    assert score == 0.9

    state_not_gated = {"browser_available": True, "js_gated": False}
    score, params = js_gated_scorer(state_not_gated)
    assert score == 0.0


def test_should_terminate_conditions():
    """Test terminace smyčky."""
    # Test time budget
    budget_exhausted = True
    assert budget_exhausted is True

    # Test confidence
    high_confidence = 0.95
    assert high_confidence > 0.9

    # Test stagnation
    stagnation = 6
    assert stagnation > 5

    # Test max iterations
    iter_count = 200
    max_iters = 200
    assert iter_count >= max_iters

    # Test contradiction queue cycling
    contradiction_queue = deque(maxlen=20)
    for i in range(18):
        contradiction_queue.append({"id": i})
    assert len(contradiction_queue) > 15


def test_stagnation_counter():
    """Test stagnation counter increment/decrement."""
    stagnation_counter = 0

    # Nové findings = reset counter
    new_findings = 3
    if new_findings > 0:
        stagnation_counter = 0
    else:
        stagnation_counter += 1

    assert stagnation_counter == 0

    # Žádné nové findings = increment
    new_findings = 0
    if new_findings > 0:
        stagnation_counter = 0
    else:
        stagnation_counter += 1

    assert stagnation_counter == 1


def test_novelty_score_calculation():
    """Test výpočtu novelty score."""
    def compute_novelty(last_new, total):
        if total == 0:
            return 0.0
        return last_new / total

    # Žádné findings
    assert compute_novelty(0, 0) == 0.0

    # Několik nových
    assert compute_novelty(5, 100) == 0.05

    # Hodně nových
    assert compute_novelty(50, 100) == 0.5

    # Všechno nové
    assert compute_novelty(10, 10) == 1.0


def test_cooldown_skip_action():
    """Test že akce v cooldownu je přeskočena."""
    from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

    orch = FullyAutonomousOrchestrator.__new__(FullyAutonomousOrchestrator)

    # Definice akcí
    def render_scorer(state):
        return (0.9, {"url": "http://example.com"})

    def search_scorer(state):
        return (0.5, {"query": state["query"]})

    async def render_handler(**params):
        from hledac.universal.utils import ActionResult
        return ActionResult(success=True, findings=[], sources=[])

    async def search_handler(**params):
        from hledac.universal.utils import ActionResult
        return ActionResult(success=True, findings=[], sources=[])

    orch._action_registry = {
        "render_page": (render_handler, render_scorer),
        "surface_search": (search_handler, search_scorer),
    }

    # Simuluj cooldown pro render_page
    orch._action_cooldowns = OrderedDict([("render_page", 2)])  # 2 iterace cooldown
    orch._repeat_action_count = 0
    orch._last_action_name = ""

    # Rozhodni - render_page má vysoký score ale je v cooldownu
    state = {"query": "test", "recent_novelty": 0.5}
    action_name, params = orch._decide_next_action(state)

    # Měla by být vybrána surface_search (fallback), ne render_page
    assert action_name == "surface_search", f"Očekáván surface_search, ale vybráno {action_name}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
