"""
runtime/sidecar_runner_decorator.py — F27: Sidecar Runner Base + Factories

Eliminated ~160 LOC of duplicate try/except boilerplate from sidecar_bus.py.
Provides:
- BaseSidecarRunner: ABC for runners with complex inline _run_impl logic
- sidecar_runner(): factory for simple correlate()-based runners
- sidecar_runner_await(): factory for async async_correlate()-based runners

Pattern (before → after):
  BEFORE (20+ LOC per runner):
    async def _xxx_runner(findings, store, query):
        if not findings or store is None: return
        try:
            from hledac.universal.recon.xxx import create_xxx_adapter
        except Exception: return
        try:
            adapter = create_xxx_adapter()
            derived_findings = adapter.do_something(findings, query)
            if not derived_findings: return
            results = await store.async_ingest_findings_batch(derived_findings)
            return sum(1 for r in results if isinstance(r, dict) and r.get("accepted"))
        except Exception: pass

  AFTER (8-12 LOC per runner):
    _XxxRunner = sidecar_runner(
        name="xxx",
        module_path="hledac.universal.recon.xxx",
        factory_name="create_xxx_adapter",
        correlate_method="do_something",
    )

GHOST_INVARIANTS enforced:
- asyncio.gather always with return_exceptions=True
- Fail-soft: sidecar error never crashes the sprint
- Lazy import: module loaded only on first use
- RAM guard: skipped by bus if governor reports critical/emergency
"""

import importlib
import logging
from abc import abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

_sidecarlogger = logging.getLogger(__name__)


# ── Canonical ingest helper ───────────────────────────────────────────────────


async def _store_ingest_and_count(
    store: DuckDBShadowStore,
    derived_findings: list,
) -> int:
    """
    Canonical ingest helper — stores derived findings, returns accepted count.

    Used by all runners that produce derived findings.
    """
    if not derived_findings:
        return 0
    results = await store.async_ingest_findings_batch(derived_findings)
    return sum(1 for r in results if isinstance(r, dict) and r.get("accepted"))


# ── Composable runner factories (F27) ─────────────────────────────────────────
# Plain callable objects — no dataclass inheritance, no abstractmethod issues.


def sidecar_runner(
    *,
    name: str,
    module_path: str,
    factory_name: str,
    correlate_method: str = "correlate",
) -> Any:
    """
    Factory: create a simple sidecar runner callable.

    The returned object is awaitable and callable — compatible with
    SidecarRunner = Callable[[list, DuckDBShadowStore, str], Any].

    Usage:
        _ExposureCorrelatorRunner = sidecar_runner(
            name="exposure_correlator",
            module_path="hledac.universal.recon.exposure_correlator",
            factory_name="create_exposure_correlator_adapter",
            correlate_method="correlate",
    )

    Then in DEFAULT_SIDECAR_RUNNERS:
        ("exposure_correlator", _ExposureCorrelatorRunner()),
    """
    _module_path = module_path
    _factory_name = factory_name
    _correlate_method = correlate_method
    _name = name
    _cached_adapter: Any | None = None
    _adapter_initialized: bool = False

    class _SidecarRunner:
        """Simple sidecar runner: lazy import + correlate + ingest."""

        __slots__ = ()

        def _get_adapter(self) -> Any | None:
            nonlocal _cached_adapter, _adapter_initialized
            if _adapter_initialized:
                return _cached_adapter
            _adapter_initialized = True
            try:
                module = importlib.import_module(_module_path)
                factory = getattr(module, _factory_name)
                _cached_adapter = factory()
                return _cached_adapter
            except Exception:
                _sidecarlogger.debug(
                    "[%s] factory '%s' from '%s' failed",
                    _name,
                    _factory_name,
                    _module_path,
                )
                return None

        async def __call__(
            self,
            findings: list,
            store: DuckDBShadowStore | None,
            query: str,
        ) -> Any:
            if not findings or store is None:
                return None
            adapter = self._get_adapter()
            if adapter is None:
                return None
            try:
                method = getattr(adapter, _correlate_method)
                derived_findings = method(findings, query)
                return await _store_ingest_and_count(store, derived_findings)
            except Exception as e:
                _sidecarlogger.debug(
                    "[%s] %s failed: %s: %s",
                    _name,
                    _correlate_method,
                    type(e).__name__,
                    e,
                )
                return None

    return _SidecarRunner


def sidecar_runner_await(
    *,
    name: str,
    module_path: str,
    factory_name: str,
    correlate_method: str = "async_correlate",
) -> Any:
    """
    Like sidecar_runner but for async adapter.correlate() methods.

    Returns a callable compatible with SidecarRunner.
    """
    _module_path = module_path
    _factory_name = factory_name
    _correlate_method = correlate_method
    _name = name
    _cached_adapter: Any | None = None
    _adapter_initialized: bool = False

    class _SidecarRunnerAwait:
        __slots__ = ()

        def _get_adapter(self) -> Any | None:
            nonlocal _cached_adapter, _adapter_initialized
            if _adapter_initialized:
                return _cached_adapter
            _adapter_initialized = True
            try:
                module = importlib.import_module(_module_path)
                factory = getattr(module, _factory_name)
                _cached_adapter = factory()
                return _cached_adapter
            except Exception:
                _sidecarlogger.debug(
                    "[%s] factory '%s' from '%s' failed",
                    _name,
                    _factory_name,
                    _module_path,
                )
                return None

        async def __call__(
            self,
            findings: list,
            store: DuckDBShadowStore | None,
            query: str,
        ) -> Any:
            if not findings or store is None:
                return None
            adapter = self._get_adapter()
            if adapter is None:
                return None
            try:
                method = getattr(adapter, _correlate_method)
                derived_findings = await method(findings, query)
                return await _store_ingest_and_count(store, derived_findings)
            except Exception as e:
                _sidecarlogger.debug(
                    "[%s] %s failed: %s: %s",
                    _name,
                    _correlate_method,
                    type(e).__name__,
                    e,
                )
                return None

    return _SidecarRunnerAwait


# ── BaseSidecarRunner ABC (for complex runners with inline _run_impl) ─────────


class BaseSidecarRunner:
    """
    ABC for sidecar runners with complex inline logic.

    Subclasses implement _run_impl(adapter, findings, store, query).
    Base handles:
      - Early return if findings empty or store None
      - Lazy import of module_path via importlib
      - Adapter creation via factory_name
      - Fail-soft: any exception → debug log → return None
    """

    __slots__ = ()

    @property
    @abstractmethod
    def _module_path(self) -> str | None:
        """Dotted module path for the adapter (e.g. 'hledac.universal.recon.xxx')."""
        raise NotImplementedError

    @property
    @abstractmethod
    def _factory_name(self) -> str:
        """Name of the factory function on the module (e.g. 'create_xxx_adapter')."""
        raise NotImplementedError

    @property
    @abstractmethod
    def _name(self) -> str:
        """Sidecar name used in log messages."""
        raise NotImplementedError

    @property
    def _cached_adapter(self) -> Any | None:
        """Lazily-created adapter instance. Override for custom adapter lifecycle."""
        return None

    def _import_module(self) -> Any | None:
        """Lazy import of _module_path. Returns module or None on failure."""
        mp = self._module_path
        if mp is None:
            return None
        try:
            return importlib.import_module(mp)
        except Exception:
            _sidecarlogger.debug(
                "[%s] import module '%s' failed",
                self._name,
                mp,
            )
            return None

    def _create_adapter(self) -> Any | None:
        """Lazy import + factory call. Returns adapter or None on failure."""
        module = self._import_module()
        if module is None:
            return None
        try:
            factory = getattr(module, self._factory_name)
            return factory()
        except Exception:
            _sidecarlogger.debug(
                "[%s] factory '%s' call failed",
                self._name,
                self._factory_name,
            )
            return None

    async def run_async(
        self,
        findings: list,
        store: DuckDBShadowStore | None,
        query: str,
    ) -> Any:
        """
        Canonical runner entry point used by FindingSidecarBus.

        Handles early-exit guards and fail-soft wrapper around _run_impl.
        """
        if not findings or store is None:
            return None
        adapter = self._create_adapter()
        if adapter is None:
            return None
        try:
            return await self._run_impl(adapter, findings, store, query)
        except Exception as e:
            _sidecarlogger.debug(
                "[%s] _run_impl failed: %s: %s",
                self._name,
                type(e).__name__,
                e,
            )
            return None

    async def __call__(
        self,
        findings: list,
        store: DuckDBShadowStore | None,
        query: str,
    ) -> Any:
        """Callable entry point — SidecarRunner protocol compatibility."""
        return await self.run_async(findings, store, query)

    @abstractmethod
    async def _run_impl(
        self,
        adapter: Any,
        findings: list,
        store: DuckDBShadowStore,
        query: str,
    ) -> Any:
        """
        Subclass implements the actual sidecar logic here.

        Args:
            adapter: Created by _factory_name from _module_path.
            findings: Accepted findings from current sprint.
            store: DuckDBShadowStore for canonical writes.
            query: Original sprint query.

        Returns:
            Stored count (int), or None on no-op.
            Any exception propagates to fail-soft handler in run_async.
        """
        raise NotImplementedError  # type: ignore[not_covered,unused-parameters]
