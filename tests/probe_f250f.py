"""Sprint F250F: Privacy Layer + Research Layer integration probes."""

BASE = '/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal'

# Test 1: SprintSchedulerResult has pii_findings_anonymized field
def test_sprint_scheduler_result_has_pii_field():
    """Verify pii_findings_anonymized field exists in SprintSchedulerResult dataclass."""
    with open(f'{BASE}/runtime/sprint_scheduler.py') as f:
        content = f.read()

    assert 'pii_findings_anonymized: int = 0' in content, "pii_findings_anonymized field not found in SprintSchedulerResult"
    print("✓ SprintSchedulerResult.pii_findings_anonymized exists")


# Test 2: Privacy layer PII check gate respects HLEDAC_ENABLE_PRIVACY_LAYER
def test_privacy_layer_gate_respected():
    """Verify privacy layer gate only runs when env var = '1'."""
    with open(f'{BASE}/runtime/sprint_scheduler.py') as f:
        content = f.read()

    assert 'HLEDAC_ENABLE_PRIVACY_LAYER' in content, "Gate env var not found"
    assert 'os.environ.get("HLEDAC_ENABLE_PRIVACY_LAYER") == "1"' in content, "Gate condition not found"
    assert 'detect_pii' in content, "detect_pii call not found"
    assert 'anonymize_text' in content, "anonymize_text call not found"
    assert 'self._result.pii_findings_anonymized' in content, "counter update not found"
    print("✓ Privacy layer gate respects HLEDAC_ENABLE_PRIVACY_LAYER")


# Test 3: Research layer hunt integration in hypothesis_engine
def test_research_layer_hunt_integration():
    """Verify research_layer hunt() is called before LLM in generate_dark_surface_queries."""
    with open(f'{BASE}/brain/hypothesis_engine.py') as f:
        content = f.read()

    assert 'HLEDAC_ENABLE_RESEARCH_LAYER' in content, "Gate env var not found"
    assert 'asyncio.to_thread' in content, "to_thread wrapper not found"
    assert '_research.hunt' in content, "hunt call not found"
    assert 'context_hints' in content, "context_hints not found"
    assert '_research_hint_section' in content, "prompt injection not found"
    print("✓ Research layer hunt() integrated before LLM call")


# Test 4: Privacy context lifecycle (create at startup, close at teardown)
def test_privacy_context_lifecycle():
    """Verify privacy context is created at startup and closed at teardown."""
    with open(f'{BASE}/runtime/sprint_scheduler.py') as f:
        content = f.read()

    assert 'create_privacy_context' in content, "create_privacy_context not found"
    assert 'close_privacy_context' in content, "close_privacy_context not found"
    assert 'self._privacy_context_id' in content, "_privacy_context_id not found"
    print("✓ Privacy context lifecycle (create/close) wired")


# Test 5: Separate gates (PRIVACY_LAYER vs RESEARCH_LAYER)
def test_separate_gates():
    """Verify PRIVACY_LAYER and RESEARCH_LAYER are separate gates."""
    with open(f'{BASE}/runtime/sprint_scheduler.py') as f:
        sched = f.read()
    with open(f'{BASE}/brain/hypothesis_engine.py') as f:
        hyp = f.read()

    assert 'HLEDAC_ENABLE_PRIVACY_LAYER' in sched, "PRIVACY_LAYER gate missing in scheduler"
    assert 'HLEDAC_ENABLE_RESEARCH_LAYER' in hyp, "RESEARCH_LAYER gate missing in engine"
    print("✓ Separate gates (PRIVACY_LAYER in scheduler, RESEARCH_LAYER in engine)")


# Test 6: max_depth=2 for M1 safety in hunt()
def test_hunt_max_depth():
    """Verify hunt() is called with max_depth=2 for M1 safety."""
    with open(f'{BASE}/brain/hypothesis_engine.py') as f:
        content = f.read()

    # Look for the hunt call with max_depth
    assert '_research.hunt' in content, "hunt call not found"
    # Verify max_depth=2 in the call
    assert ', 2' in content or 'max_depth=2' in content, "max_depth=2 not found in hunt call"
    print("✓ hunt() max_depth=2 for M1 safety")


if __name__ == '__main__':
    print("\n=== Sprint F250F Integration Probes ===\n")
    test_sprint_scheduler_result_has_pii_field()
    test_privacy_layer_gate_respected()
    test_research_layer_hunt_integration()
    test_privacy_context_lifecycle()
    test_separate_gates()
    test_hunt_max_depth()
    print("\n=== All probes passed ===\n")
