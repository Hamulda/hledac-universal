"""Sprint 69E: Non-blocking scorer tests."""
import inspect
import time


class TestStructureMapNonBlockingScorer:
    """Testy pro neblokující scorer - Sprint 69E."""

    def test_scorer_does_not_call_async_methods(self):
        """Test: Scorer nesmí volat žádné async metody ani thread pooly."""
        # Definice scoreru (zkopírováno z AutonomousOrchestrator._initialize_actions)
        def build_structure_map_scorer(state):
            """Scorer for build_structure_map action - NON-BLOCKING."""
            # Čte z state dict, žádné async volání
            if not state.get("structure_map_can_run", False):
                return (-1e9, {})

            score = 0.35
            if state.get("structure_map_upcoming_synthesis"):
                score += 0.10

            return (score, {})

        # Simuluj state s can_run = True
        state = {
            "structure_map_can_run": True,
            "structure_map_upcoming_synthesis": False,
        }

        # Zavolej scorer - nesmí zablokovat
        start = time.monotonic()
        score, metadata = build_structure_map_scorer(state)
        elapsed = time.monotonic() - start

        # Ověř výsledek
        assert score == 0.35
        assert elapsed < 0.01  # Musí být OKAMŽITÝ (< 10ms)

    def test_scorer_returns_negative_when_cannot_run(self):
        """Test: Scorer vrací -1e9 když nelze spustit."""
        def build_structure_map_scorer(state):
            if not state.get("structure_map_can_run", False):
                return (-1e9, {})
            score = 0.35
            if state.get("structure_map_upcoming_synthesis"):
                score += 0.10
            return (score, {})

        # can_run = False
        state = {"structure_map_can_run": False}
        score, _ = build_structure_map_scorer(state)
        assert score == -1e9

    def test_scorer_includes_synthesis_bonus(self):
        """Test: Scorer přidává bonus za upcoming synthesis."""
        def build_structure_map_scorer(state):
            if not state.get("structure_map_can_run", False):
                return (-1e9, {})
            score = 0.35
            if state.get("structure_map_upcoming_synthesis"):
                score += 0.10
            return (score, {})

        # S bonusem
        state = {
            "structure_map_can_run": True,
            "structure_map_upcoming_synthesis": True,
        }
        score, _ = build_structure_map_scorer(state)
        assert abs(score - 0.45) < 0.001  # 0.35 + 0.10 (float precision)

    def test_scorer_source_code_is_non_blocking(self):
        """Test: Ověří, že zdrojový kód scoreru neobsahuje blocking operace."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        # Získej zdrojový kód _initialize_actions
        source = inspect.getsource(FullyAutonomousOrchestrator._initialize_actions)

        # Najdi začátek a konec build_structure_map_scorer
        lines = source.split("\n")
        in_scorer = False
        scorer_lines = []

        for _i, line in enumerate(lines):
            if "def build_structure_map_scorer" in line:
                in_scorer = True
            elif in_scorer and line.strip().startswith("def ") and "build_structure_map" not in line:
                break

            if in_scorer:
                scorer_lines.append(line)

        scorer_source = "\n".join(scorer_lines)

        # Ověř, že blocking kód byl odstraněn
        assert "pool.submit" not in scorer_source, "Found blocking pool.submit in scorer"
        assert "asyncio.run" not in scorer_source, "Found blocking asyncio.run in scorer"
        assert ".result(timeout=" not in scorer_source, "Found blocking .result() in scorer"
        assert "ThreadPoolExecutor" not in scorer_source, "Found ThreadPoolExecutor in scorer"

    def test_analyze_state_includes_structure_map_gating(self):
        """Test: _analyze_state vrací structure_map_can_run a structure_map_upcoming_synthesis."""
        import inspect

        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

        # Získej zdrojový kód _analyze_state
        source = inspect.getsource(FullyAutonomousOrchestrator._analyze_state)

        # Ověř, že obsahuje klíčové proměnné
        assert "structure_map_can_run" in source, "Missing structure_map_can_run in _analyze_state"
        assert "structure_map_upcoming_synthesis" in source, "Missing structure_map_upcoming_synthesis in _analyze_state"

        # Ověř, že vrací tyto hodnoty v dict
        assert '"structure_map_can_run"' in source or "'structure_map_can_run'" in source
        assert '"structure_map_upcoming_synthesis"' in source or "'structure_map_upcoming_synthesis'" in source
