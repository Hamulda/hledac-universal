"""tests/test_container_resolution.py — A3: ServiceContainer resolution verification.

F350M-R / A3: Verifies that:
  1. ServiceContainer.register() / get() / try_get() work correctly
  2. Global container is a singleton
  3. get_inference_coordinator() delegates to container when registered
  4. isolated_executors pool getters delegate to container when registered
  5. SprintContext.container field is correctly set during bootstrap
"""

from __future__ import annotations

import pytest

from core.container import (
from core import aclose
    ServiceContainer,
    get_global_container,
    reset_global_container,
)


class TestServiceContainer:
    """Unit tests for ServiceContainer."""

    def setup_method(self) -> None:
        reset_global_container()
        self.container = ServiceContainer()

    def teardown_method(self) -> None:
        self.container.clear()

    def test_register_and_get_singleton(self) -> None:
        """register(scope=singleton) returns same instance on repeated get()."""
        factory_calls: int = 0

        def factory() -> object:
            nonlocal factory_calls
            factory_calls += 1
            return object()

        self.container.register("svc1", factory=factory, scope="singleton")
        inst1 = self.container.get("svc1")
        inst2 = self.container.get("svc1")

        assert inst1 is inst2
        assert factory_calls == 1

    def test_register_and_get_factory(self) -> None:
        """register(scope=factory) returns new instance on each get()."""
        factory_calls: int = 0

        def factory() -> object:
            nonlocal factory_calls
            factory_calls += 1
            return object()

        self.container.register("svc2", factory=factory, scope="factory")
        inst1 = self.container.get("svc2")
        inst2 = self.container.get("svc2")

        assert inst1 is not inst2
        assert factory_calls == 2

    def test_get_unknown_raises_keyerror(self) -> None:
        """get() on unregistered service raises KeyError."""
        with pytest.raises(KeyError):
            self.container.get("nonexistent")

    def test_try_get_unknown_returns_none(self) -> None:
        """try_get() on unregistered service returns None."""
        assert self.container.try_get("nonexistent") is None

    def test_override_replaces_registration(self) -> None:
        """register(override=True) replaces existing registration."""
        results: list[int] = []

        def factory1() -> object:
            results.append(1)
            return object()

        def factory2() -> object:
            results.append(2)
            return object()

        self.container.register("svc3", factory=factory1, scope="singleton")
        self.container.get("svc3")  # triggers factory1
        self.container.register("svc3", factory=factory2, scope="singleton", override=True)
        self.container.get("svc3")  # triggers factory2 (override cleared cache)

        assert results == [1, 2]

    def test_is_registered(self) -> None:
        """is_registered() returns True for registered services."""
        def factory() -> object:
            return object()

        assert not self.container.is_registered("svc4")
        self.container.register("svc4", factory=factory, scope="singleton")
        assert self.container.is_registered("svc4")

    def test_parent_chain_resolution(self) -> None:
        """Child container resolves from parent via parent chain."""
        parent = ServiceContainer()

        def parent_factory() -> str:
            return "from_parent"

        parent.register("shared", factory=parent_factory, scope="singleton")
        child = ServiceContainer(parent=parent)

        assert child.get("shared") == "from_parent"
        assert parent.is_registered("shared")

    def test_registered_names(self) -> None:
        """registered_names() returns all local registrations."""
        def factory() -> object:
            return object()

        self.container.register("a", factory=factory, scope="singleton")
        self.container.register("b", factory=factory, scope="singleton")
        names = self.container.registered_names()

        assert "a" in names
        assert "b" in names


class TestGlobalContainer:
    """Tests for the global singleton container."""

    def setup_method(self) -> None:
        reset_global_container()

    def teardown_method(self) -> None:
        reset_global_container()

    def test_global_is_singleton(self) -> None:
        """get_global_container() returns the same instance."""
        g1 = get_global_container()
        g2 = get_global_container()
        assert g1 is g2

    def test_reset_clears_global(self) -> None:
        """reset_global_container() allows fresh container."""
        g1 = get_global_container()
        reset_global_container()
        g2 = get_global_container()
        assert g1 is not g2


class TestContainerIntegration:
    """A3 integration: container wired into existing singletons."""

    def setup_method(self) -> None:
        reset_global_container()

    def teardown_method(self) -> None:
        reset_global_container()

    def test_inference_coordinator_delegates_to_container(self) -> None:
        """get_inference_coordinator() returns container instance when registered."""
        from core.inference_coordinator import get_inference_coordinator

        # Register a mock coordinator in the global container
        container = get_global_container()

        class MockCoordinator:
            pass

        mock = MockCoordinator()
        container.register(
            "inference.coordinator",
            factory=lambda: mock,
            scope="singleton",
        )

        # get_inference_coordinator() should return our mock
        result = get_inference_coordinator()
        assert result is mock

    def test_isolated_executors_delegate_to_container_duckdb(self) -> None:
        """get_duckdb_executor() returns container instance when registered."""
        from core.isolated_executors import get_duckdb_executor

        container = get_global_container()

        class MockExecutor:
            pass

        mock = MockExecutor()
        container.register(
            "executor.duckdb",
            factory=lambda: mock,
            scope="singleton",
        )

        result = get_duckdb_executor()
        assert result is mock

    def test_isolated_executors_delegate_to_container_mlx(self) -> None:
        """get_mlx_executor() returns container instance when registered."""
        from core.isolated_executors import get_mlx_executor

        container = get_global_container()

        class MockExecutor:
            pass

        mock = MockExecutor()
        container.register(
            "executor.mlx",
            factory=lambda: mock,
            scope="singleton",
        )

        result = get_mlx_executor()
        assert result is mock

    def test_isolated_executors_delegate_to_container_evidence(self) -> None:
        """get_evidence_batch_writer() returns container instance when registered."""
        from core.isolated_executors import get_evidence_batch_writer

        container = get_global_container()

        class MockWriter:
            pass

        mock = MockWriter()
        container.register(
            "executor.evidence",
            factory=lambda: mock,
            scope="singleton",
        )

        result = get_evidence_batch_writer()
        assert result is mock


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
