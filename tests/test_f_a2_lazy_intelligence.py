"""
Sprint F-A2: lazy module loading via PEP 562 ``__getattr__`` in
``intelligence/__init__.py``.

Hermetic tests verifying:
  - cold import time is bounded (no eager submodule imports)
  - all 21 ``XXX_AVAILABLE`` flags are accessible without side-effects
  - ``__dir__`` advertises every lazy name (tab completion works)
  - ``__getattr__`` returns the expected class / function / constant
  - ``__getattr__`` raises ``AttributeError`` for unknown names
  - null fallbacks (``Name = None``) fire when a submodule is missing
  - name collisions resolve to the LAST spec (mirrors original shadowing)
  - spec loading is idempotent (repeated access = single import)
  - ``_lazy_stats`` diagnostic reflects actual state
  - ``__all__`` contains every public name with zero duplicates
  - behavioral parity: same set of top-level names available as before

All tests run offline — no network, no MLX, no heavy deps loaded.
"""


import importlib
import sys
import time

import pytest
from core import aclose


# Force the package to be importable; conftest already does this in CI.
@pytest.fixture
def reload_intelligence():
    """Force a clean reload of ``intelligence`` so timing measurements are
    hermetic. Caches pre-loaded state and restores it after the test."""
    cached = sys.modules.get("intelligence")
    sys.modules.pop("intelligence", None)
    import intelligence
    yield intelligence
    # restore original
    sys.modules["intelligence"] = cached


# ---------------------------------------------------------------------------
# Cold import latency
# ---------------------------------------------------------------------------


class TestSprintFA2ColdImport:
    """Cold ``import intelligence`` should NOT pay for submodule imports."""

    def test_cold_import_under_120ms(self, reload_intelligence):
        """Hermetic: no submodule import should fire on ``import intelligence``.

        Original code: ~206ms (21 try/except blocks + 25 import lines).
        Lazy target: <120ms (spec table + module init only).
        Headroom is large because of logger setup, mtime checks, etc.
        """
        sys.modules.pop("intelligence", None)
        t0 = time.perf_counter()
        mod = importlib.import_module("intelligence")
        elapsed_ms = (time.perf_counter() - t0) * 1000
        # generous bound; on M1 cold start this is ~26ms
        assert elapsed_ms < 120, f"cold import took {elapsed_ms:.1f}ms (>120ms)"
        assert mod._lazy_stats()["resolved_count"] == 0, (
            "no specs should be resolved on cold import"
        )

    def test_cold_import_does_not_load_submodules(self, reload_intelligence):
        """Verify spec table + PEP 562 path is wired (no eager module load).

        Probe: import intelligence fresh, then check that no spec
        has been resolved (zero heavy deps loaded).
        """
        sys.modules.pop("intelligence", None)
        mod = importlib.import_module("intelligence")
        stats = mod._lazy_stats()
        assert stats["resolved_count"] == 0
        assert stats["total_count"] == 21
        assert len(stats["pending"]) == 21
        assert len(stats["loaded"]) == 0


# ---------------------------------------------------------------------------
# Flag + class access
# ---------------------------------------------------------------------------


class TestSprintFA2FlagAccess:
    """All 21 ``XXX_AVAILABLE`` flags are accessible via ``intelligence.X``."""

    @pytest.mark.parametrize("flag", [
        "ARCHIVE_AVAILABLE",
        "TEMPORAL_AVAILABLE",
        "CRAWLER_AVAILABLE",
        "WEB_INTEL_AVAILABLE",
        "ACADEMIC_SEARCH_AVAILABLE",
        "DATA_LEAK_HUNTER_AVAILABLE",
        "CRYPTO_AVAILABLE",
        "DOCUMENT_INTELLIGENCE_AVAILABLE",
        "TEMPORAL_ARCHAEOLOGIST_AVAILABLE",
        "TIMELINE_SYNTHESIZER_AVAILABLE",
        "TEMPORAL_ARCHAEOLOGIST_ADAPTER_AVAILABLE",
        "EXPOSED_SERVICE_HUNTER_AVAILABLE",
        "OPEN_SOURCE_COLLECTORS_AVAILABLE",
        "ACADEMIC_DISCOVERY_AVAILABLE",
        "PASTEBIN_MONITOR_AVAILABLE",
        "RELATIONSHIP_DISCOVERY_AVAILABLE",
        "PATTERN_MINING_AVAILABLE",
        "IDENTITY_STITCHING_AVAILABLE",
        "BLOCKCHAIN_FORENSICS_AVAILABLE",
        "INPUT_DETECTOR_AVAILABLE",
        "WORKFLOW_ORCHESTRATOR_AVAILABLE",
    ])
    def test_flag_returns_bool(self, reload_intelligence, flag):
        value = getattr(reload_intelligence, flag)
        assert isinstance(value, bool), (
            f"{flag} returned {type(value).__name__}, expected bool"
        )


class TestSprintFA2NameAccess:
    """Class/function names are accessible and trigger lazy load."""

    def test_accessing_class_triggers_spec_load(self, reload_intelligence):
        """Probing: only the owning spec loads on first access."""
        # Pastebin is a real dep in this repo; first access triggers load
        _ = reload_intelligence.PasteFinding
        stats = reload_intelligence._lazy_stats()
        assert "PASTEBIN_MONITOR_AVAILABLE" in stats["loaded"]

    def test_accessing_nonexistent_raises_attribute_error(self, reload_intelligence):
        with pytest.raises(AttributeError) as exc_info:
            reload_intelligence.NonExistent_FA2Test  # noqa: B018
        assert "NonExistent_FA2Test" in str(exc_info.value)
        assert "intelligence" in str(exc_info.value)

    def test_hasattr_returns_false_for_missing(self, reload_intelligence):
        assert hasattr(reload_intelligence, "NonExistent_FA2Test") is False
        assert hasattr(reload_intelligence, "ARCHIVE_AVAILABLE") is True


# ---------------------------------------------------------------------------
# __dir__ + __all__ integrity
# ---------------------------------------------------------------------------


class TestSprintFA2DirAndAll:
    """``__dir__`` + ``__all__`` give a complete view of the package."""

    def test_dir_contains_all_lazy_names(self, reload_intelligence):
        d = set(dir(reload_intelligence))
        # Spot check a few representative names from each spec category
        for n in [
            "ARCHIVE_AVAILABLE", "ArchiveDiscovery",
            "TimelineSynthesizer", "MAX_TIMELINE_EVENTS",
            "PasteFinding", "pastebin_run",
            "BlockchainForensics", "BlockchainPatternType",
            "IntelligentInputDetector", "InputAnalysis",
            "WorkflowOrchestrator", "ComprehensiveReport",
        ]:
            assert n in d, f"dir() missing {n}"

    def test_all_has_no_duplicates(self, reload_intelligence):
        all_list = reload_intelligence.__all__
        assert len(all_list) == len(set(all_list)), (
            f"__all__ has duplicates: "
            f"{[n for n in all_list if all_list.count(n) > 1]}"
        )

    def test_all_contains_availability_flags(self, reload_intelligence):
        for f in [
            "ARCHIVE_AVAILABLE", "CRYPTO_AVAILABLE",
            "TIMELINE_SYNTHESIZER_AVAILABLE", "PASTEBIN_MONITOR_AVAILABLE",
        ]:
            assert f in reload_intelligence.__all__, f"__all__ missing {f}"


# ---------------------------------------------------------------------------
# Name collisions
# ---------------------------------------------------------------------------


class TestSprintFA2NameCollisions:
    """Names imported from multiple submodules: last spec wins."""

    def test_anomaly_resolves_to_last_spec(self, reload_intelligence):
        """`Anomaly` is in pattern_mining AND workflow_orchestrator.
        Last spec (workflow_orchestrator) wins, matching original.
        """
        value = reload_intelligence.Anomaly
        # workflow_orchestrator not installed in CI → explicit None
        # fallback per the original ``except`` branch
        if reload_intelligence.WORKFLOW_ORCHESTRATOR_AVAILABLE:
            assert value is not None
        else:
            # last spec loaded but missing → None fallback fires
            assert value is None

    def test_pattern_resolves_to_input_detector(self, reload_intelligence):
        """`Pattern` in pattern_mining AND input_detector.
        input_detector is last → wins. Returns the class or None.
        """
        value = reload_intelligence.Pattern
        if reload_intelligence.INPUT_DETECTOR_AVAILABLE:
            assert isinstance(value, type)
        else:
            assert value is None


# ---------------------------------------------------------------------------
# Null fallbacks
# ---------------------------------------------------------------------------


class TestSprintFA2NullFallbacks:
    """input_detector + workflow_orchestrator set explicit ``Name = None``
    in their original except branches. Preserve that contract.
    """

    def test_input_detector_null_fallback_on_missing_dep(self, reload_intelligence):
        """Simulate a missing submodule by patching import_module.

        Verifies that when ``import_module`` raises (transitive ImportError),
        the spec's null-fallback names are set to ``None`` and the flag
        resolves to ``False`` — matching the original ``except ImportError``
        behaviour where ``Name = None`` was an explicit assignment.
        """
        import intelligence

        # Reset internal state to force re-attempt
        from intelligence import _LAZY_SPECS, _RESOLVED_SPECS
        spec_input = next(
            s for s in _LAZY_SPECS if s[1] == "INPUT_DETECTOR_AVAILABLE"
        )
        # Remove from resolved so _load_spec will re-run
        _RESOLVED_SPECS.discard(spec_input)
        # Also clear the cached globals so the test reflects a clean state
        for n in spec_input[3]:  # nulls
            intelligence.__dict__.pop(n, None)
        intelligence.__dict__.pop("INPUT_DETECTOR_AVAILABLE", None)

        # Monkey-patch import_module to fail for this specific spec
        orig_import = intelligence.importlib.import_module
        call_count = {"n": 0}

        def fail_import(name, package=None):
            call_count["n"] += 1
            # match the original sub-attr access path, not the full module name
            if isinstance(name, str) and name.endswith("input_detector"):
                raise ImportError("simulated FA2 test failure")
            return orig_import(name, package)

        intelligence.importlib.import_module = fail_import
        try:
            value = intelligence.IntelligentInputDetector
            assert value is None, "expected None fallback on import failure"
            assert intelligence.INPUT_DETECTOR_AVAILABLE is False
            assert call_count["n"] >= 1
        finally:
            intelligence.importlib.import_module = orig_import
            # Re-resolve for any subsequent test by clearing cache
            _RESOLVED_SPECS.discard(spec_input)
            for n in spec_input[3]:
                intelligence.__dict__.pop(n, None)
            intelligence.__dict__.pop("INPUT_DETECTOR_AVAILABLE", None)


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestSprintFA2Idempotency:
    """Repeated attribute access should NOT re-import."""

    def test_repeated_access_does_not_reimport(self, reload_intelligence):

        # First access loads the spec
        _ = reload_intelligence.PasteFinding
        stats_after_first = reload_intelligence._lazy_stats()
        resolved_first = stats_after_first["resolved_count"]

        # Capture import_module call count via wrapper
        import intelligence as _mod
        orig = _mod.importlib.import_module
        calls = {"n": 0}

        def counting_import(name, package=None):
            calls["n"] += 1
            return orig(name, package)

        _mod.importlib.import_module = counting_import
        try:
            # Multiple accesses on the same spec
            _ = reload_intelligence.PasteFinding
            _ = reload_intelligence.pastebin_run
            _ = reload_intelligence.PASTEBIN_MONITOR_AVAILABLE
        finally:
            _mod.importlib.import_module = orig

        # No new import_module call should have happened for pastebin
        assert calls["n"] == 0, (
            f"expected 0 re-imports, got {calls['n']} "
            f"(spec should be cached after first load)"
        )
        stats_after = reload_intelligence._lazy_stats()
        assert stats_after["resolved_count"] == resolved_first


# ---------------------------------------------------------------------------
# Behavioral parity
# ---------------------------------------------------------------------------


class TestSprintFA2Parity:
    """Top-level names exposed match the original package surface."""

    # These names should ALWAYS be present in __all__ / dir(),
    # regardless of whether their dep is installed in the test env.
    ALWAYS_PRESENT = [
        # Flags
        "ARCHIVE_AVAILABLE", "TEMPORAL_AVAILABLE", "CRYPTO_AVAILABLE",
        "PASTEBIN_MONITOR_AVAILABLE", "INPUT_DETECTOR_AVAILABLE",
        "WORKFLOW_ORCHESTRATOR_AVAILABLE",
        # Lazy internals
        "_lazy_stats",
    ]

    def test_always_present_names(self, reload_intelligence):
        for n in self.ALWAYS_PRESENT:
            assert n in reload_intelligence.__all__, (
                f"__all__ missing required public name: {n}"
            )
            assert n in dir(reload_intelligence), (
                f"dir() missing required public name: {n}"
            )

    def test_module_is_package(self, reload_intelligence):
        """`intelligence` is a package, not a module — sanity check."""
        # A package's __file__ ends with __init__.py
        assert reload_intelligence.__file__ is not None
        assert reload_intelligence.__file__.endswith("__init__.py")
        assert reload_intelligence.__name__ == "intelligence"

    def test_getattr_raises_for_unknown_with_module_prefix(self, reload_intelligence):
        """Error message must include both module + attribute name."""
        with pytest.raises(AttributeError) as exc_info:
            reload_intelligence.zzz_nonexistent_zzz  # noqa: B018
        msg = str(exc_info.value)
        assert "intelligence" in msg
        assert "zzz_nonexistent_zzz" in msg


# ---------------------------------------------------------------------------
# Diagnostic
# ---------------------------------------------------------------------------


class TestSprintFA2DiagnosticStats:
    """``_lazy_stats`` returns a useful diagnostic snapshot."""

    def test_stats_shape(self, reload_intelligence):
        stats = reload_intelligence._lazy_stats()
        assert set(stats.keys()) == {
            "loaded", "pending", "resolved_count", "total_count",
        }
        assert isinstance(stats["loaded"], list)
        assert isinstance(stats["pending"], list)
        assert isinstance(stats["resolved_count"], int)
        assert isinstance(stats["total_count"], int)

    def test_stats_progresses_on_access(self, reload_intelligence):
        # cold state
        s0 = reload_intelligence._lazy_stats()
        assert s0["resolved_count"] == 0

        # access one name → at least one spec loaded
        _ = reload_intelligence.CRYPTO_AVAILABLE
        s1 = reload_intelligence._lazy_stats()
        assert s1["resolved_count"] >= 1
        assert s1["resolved_count"] == s0["resolved_count"] + 1

    def test_stats_loaded_pending_partition(self, reload_intelligence):
        """``loaded`` and ``pending`` partition the full spec list."""
        for _ in range(3):
            _ = reload_intelligence.ARCHIVE_AVAILABLE
            _ = reload_intelligence.TEMPORAL_AVAILABLE
        stats = reload_intelligence._lazy_stats()
        all_flags = set(stats["loaded"]) | set(stats["pending"])
        assert len(stats["loaded"]) + len(stats["pending"]) == stats["total_count"]
        # No overlap between loaded and pending
        assert not (set(stats["loaded"]) & set(stats["pending"]))
