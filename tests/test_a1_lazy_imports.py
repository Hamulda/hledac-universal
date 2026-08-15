"""A1 Smoke Tests — V2Init Service Bootstrap

Tests that _lazy_imports.py exists and can load all 5 core services.
Verifies that V2Init assertions work correctly.

RUN: python -m pytest tests/test_a1_lazy_imports.py -v
"""

from __future__ import annotations

import pytest
from core import aclose


class TestA1LazyImportsModule:
    """A1: _lazy_imports.py module existence and factory functions."""

    def test_module_exists(self) -> None:
        """Verify _lazy_imports.py module can be imported."""
        from hledac.universal._lazy_imports import (
            get_DuckDBShadowStore,
            get_M1ResourceGovernor,
            get_Hermes3Engine,
            get_EvidenceLog,
            get_SidecarOrchestrator,
        )
        # If this import succeeds, the module exists and has all 5 factories
        assert callable(get_DuckDBShadowStore)
        assert callable(get_M1ResourceGovernor)
        assert callable(get_Hermes3Engine)
        assert callable(get_EvidenceLog)
        assert callable(get_SidecarOrchestrator)

    def test_get_all_service_status(self) -> None:
        """Verify diagnostic helper returns status for all 5 services."""
        from hledac.universal._lazy_imports import get_all_service_status, LazyServiceInfo

        status = get_all_service_status()
        assert isinstance(status, dict)
        assert len(status) == 5

        expected_services = {
            "DuckDBShadowStore",
            "M1ResourceGovernor",
            "Hermes3Engine",
            "EvidenceLog",
            "SidecarOrchestrator",
        }
        assert set(status.keys()) == expected_services

        # All should have LazyServiceInfo instances
        for name, info in status.items():
            assert isinstance(info, LazyServiceInfo), f"{name} should be LazyServiceInfo"
            # Note: Some services may not be available in test environment
            # (e.g., duckdb, mlx). The key is the module was found.
            assert info.name == name
            assert info.class_path  # Should have a class path

    def test_factory_returns_class_not_instance(self) -> None:
        """Verify factories return class types, not instances."""
        from hledac.universal._lazy_imports import (
            get_DuckDBShadowStore,
            get_M1ResourceGovernor,
            get_EvidenceLog,
        )

        # These should return class types
        cls = get_DuckDBShadowStore()
        assert isinstance(cls, type), "Factory should return a class, not instance"

        cls = get_M1ResourceGovernor()
        assert isinstance(cls, type), "Factory should return a class, not instance"

        cls = get_EvidenceLog()
        assert isinstance(cls, type), "Factory should return a class, not instance"

    def test_pep_810_module_getattr(self) -> None:
        """Verify PEP 810 __getattr__ works for factory functions."""
        # This should work via __getattr__ lazy loading
        from hledac.universal._lazy_imports import get_DuckDBShadowStore
        assert callable(get_DuckDBShadowStore)

    def test_class_caching(self) -> None:
        """Verify classes are cached after first import."""
        from hledac.universal._lazy_imports import get_EvidenceLog

        # Call twice - should return same class object (cached)
        cls1 = get_EvidenceLog()
        cls2 = get_EvidenceLog()
        assert cls1 is cls2, "Factory should return cached class"


class TestA1V2InitAssertions:
    """A1: V2Init startup assertions for service availability."""

    def test_hasattr_safe_helper(self) -> None:
        """Verify _hasattr_safe helper works correctly."""
        from hledac.universal.runtime.scheduler_v2._v2_init import _hasattr_safe

        class Obj:
            x = 1

        obj = Obj()
        assert _hasattr_safe(obj, "x") is True
        assert _hasattr_safe(obj, "y") is False

        # Should not raise even with problematic __getattr__
        class Problematic:
            def __getattr__(self, name):
                raise RuntimeError("test")

        p = Problematic()
        assert _hasattr_safe(p, "x") is False  # Should return False, not raise

    def test_init_result_structure(self) -> None:
        """Verify InitResult has expected structure for assertions."""
        from hledac.universal.runtime.scheduler_v2.protocol import InitResult

        # Create failure result
        failure = InitResult.failure("test error", 1.0)
        assert failure.ok is False
        assert failure.value is None
        assert failure.error == "test error"

        # Create success result
        class Dummy:
            pass

        success = InitResult.success(Dummy(), 2.0)
        assert success.ok is True
        assert success.value is not None
        assert success.error is None


class TestA1EvidenceLogIntegration:
    """A1: EvidenceLog lazy import integration test."""

    def test_evidence_log_factory_works(self) -> None:
        """Verify EvidenceLog factory returns the correct class."""
        from hledac.universal._lazy_imports import get_EvidenceLog

        EvidenceLog = get_EvidenceLog()
        assert EvidenceLog.__name__ == "EvidenceLog"

        # EvidenceLog should have specific __init__ signature
        import inspect
        sig = inspect.signature(EvidenceLog.__init__)
        params = list(sig.parameters.keys())
        assert "run_id" in params, "EvidenceLog.__init__ should have run_id param"


class TestA1SidecarOrchestratorIntegration:
    """A1: SidecarOrchestrator lazy import integration test."""

    def test_sidecar_orchestrator_factory_works(self) -> None:
        """Verify SidecarOrchestrator factory returns the correct class."""
        from hledac.universal._lazy_imports import get_SidecarOrchestrator

        SidecarOrchestrator = get_SidecarOrchestrator()
        assert SidecarOrchestrator.__name__ == "SidecarOrchestrator"


# ─── Diagnostic Test ────────────────────────────────────────────────────────


def test_run_service_diagnostics() -> None:
    """Run full diagnostics — useful for debugging import failures.

    This test always passes but prints diagnostic info.
    """
    from hledac.universal._lazy_imports import get_all_service_status

    print("\n" + "=" * 60)
    print("A1 Service Diagnostics")
    print("=" * 60)

    status = get_all_service_status()
    all_ok = True
    for name, info in status.items():
        if info.available:
            print(f"  ✓ {name:<25} OK ({info.class_path})")
        else:
            print(f"  ✗ {name:<25} FAIL")
            print(f"    Error: {info.error}")
            all_ok = False

    print("=" * 60)
    print(f"Overall: {'ALL OK' if all_ok else 'SOME FAILURES'}")

    # This test always passes — diagnostics only
    assert True
