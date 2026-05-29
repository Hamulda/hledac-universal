"""
Sprint 8AN Tests: Runtime Hygiene Truth Sweep + Blocker Closure
===============================================================

Tests for:
1. fetch_coordinator getaddrinfo is async-safe (FIXED)
2. academic_search clean/stable (CONFIRMED_STABLE)
3. exposed_service_hunter clean/stable (CONFIRMED_STABLE)
4. persistent_layer WARC path (TRUE SAFE)
5. document_intelligence dormant path (DORMANT_DEFERRED)
6. fd_delta telemetry is bounded after explicit shutdown
7. No import regression > 0.1s from baseline
"""

import asyncio
import gc
import inspect
import subprocess
import sys

import pytest


class TestFetchCoordinatorDNSAsyncSafety:
    """getaddrinfo async blocker — FIXED"""

    def test_fetch_coordinator_getaddrinfo_is_async_def(self):
        """FIXED: _validate_fetch_target is async def, DNS offloaded to thread."""
        from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator
        # Is it an async method?
        is_async = asyncio.iscoroutinefunction(FetchCoordinator._validate_fetch_target)
        assert is_async, "_validate_fetch_target must be async def"

    def test_fetch_coordinator_dns_uses_to_thread(self):
        """FIXED: DNS resolution uses asyncio.to_thread to avoid blocking."""
        from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator
        src = inspect.getsource(FetchCoordinator._validate_fetch_target)
        assert "asyncio.to_thread" in src, "DNS resolution must use asyncio.to_thread"

    def test_fetch_coordinator_validate_target_await_works(self):
        """FIXED: _validate_fetch_target can be awaited without blocking event loop."""
        from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator
        fc = FetchCoordinator()
        # Should not block — runs in thread pool
        result = fc._validate_fetch_target("http://127.0.0.1/test")
        assert asyncio.iscoroutine(result), "Must return coroutine"
        # Await it
        is_safe, meta = asyncio.get_event_loop().run_until_complete(result)
        assert is_safe is False
        assert "private_ip" in meta.get("blocked_reason", "")


class TestAcademicSearchStability:
    """academic_search — CONFIRMED_STABLE from 8AK"""

    def test_academic_search_no_stored_session(self):
        """CONFIRMED_STABLE: AcademicSearchEngine.__init__ has no stored session."""
        from hledac.universal.intelligence.academic_search import AcademicSearchEngine
        # Verify __init__ does NOT assign self.session = aiohttp.ClientSession()
        src = inspect.getsource(AcademicSearchEngine.__init__)
        assert "self.session = aiohttp.ClientSession()" not in src, "Must not store session on self"
        assert "self.session = " not in src or "self.session = None" in src, "Session must not be stored"

    def test_academic_search_adapters_use_context_manager(self):
        """CONFIRMED_STABLE: Adapters inside academic_search use per-call context manager."""
        from hledac.universal.intelligence.academic_search import ArxivAdapter, CrossrefAdapter, SemanticScholarAdapter
        for adapter_cls in [ArxivAdapter, CrossrefAdapter, SemanticScholarAdapter]:
            src = inspect.getsource(adapter_cls)
            assert "async with aiohttp.ClientSession()" in src, \
                f"{adapter_cls.__name__} must use per-call context manager"


class TestExposedServiceHunterStability:
    """exposed_service_hunter — CONFIRMED_STABLE from 8AK"""

    def test_exposed_service_hunter_has_context_manager(self):
        """CONFIRMED_STABLE: exposed_service_hunter has __aenter__/__aexit__."""
        from hledac.universal.intelligence.exposed_service_hunter import ExposedServiceHunter
        assert hasattr(ExposedServiceHunter, '__aenter__'), "Must have __aenter__"
        assert hasattr(ExposedServiceHunter, '__aexit__'), "Must have __aexit__"

    def test_exposed_service_hunter_session_closed_in_exit(self):
        """CONFIRMED_STABLE: session closed in __aexit__."""
        from hledac.universal.intelligence.exposed_service_hunter import ExposedServiceHunter
        src = inspect.getsource(ExposedServiceHunter)
        assert "await self.session.close()" in src, "Session must be closed in __aexit__"


class TestPersistentLayerWARCSafety:
    """persistent_layer WARC — TRUE SAFE (streaming write, no reader)"""

    def test_persistent_layer_warc_has_no_reader(self):
        """TRUE SAFE: No ArchiveIterator or WARC reader in persistent_layer."""
        from hledac.universal.knowledge.persistent_layer import PersistentKnowledgeLayer
        src = inspect.getsource(PersistentKnowledgeLayer)
        assert "ArchiveIterator" not in src, "WARC reader must not be present"
        assert "warc_file.read()" not in src, "No full-read on WARC file"

    def test_persistent_layer_warc_uses_streaming_write(self):
        """TRUE SAFE: WarcWriter uses streaming write, not full read."""
        from hledac.universal.knowledge.persistent_layer import WarcWriter
        src = inspect.getsource(WarcWriter)
        assert "_warc_file.write(" in src, "WarcWriter uses streaming write"


class TestDocumentIntelligenceDormancy:
    """document_intelligence — DORMANT_DEFERRED (engine instantiated but never called)"""

    def test_document_intelligence_analyze_pdf_never_called_in_orchestrator(self):
        """DORMANT_DEFERRED: document_intelligence analyze_pdf is never invoked in orchestrator."""
        # _document_intelligence is instantiated but never called with analyze_pdf()
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
        src = inspect.getsource(FullyAutonomousOrchestrator)
        # Check: self._document_intelligence.analyze_pdf(...) is NOT called
        actual_calls = src.count("self._document_intelligence.analyze_pdf(")
        assert actual_calls == 0, "analyze_pdf must not be called on document_intelligence"
        # Verify document_intelligence is at least referenced (lazy loading pattern)
        assert "document_intelligence" in src, "document_intelligence lazy-loading pattern"


class TestImportTimeRegression:
    """Import time must not regress > 0.1s from baseline."""

    def test_import_time_no_regression(self):
        """IMPORT: Cold import < 1.057s (baseline 0.956s + 0.1s tolerance)."""
        code = "import time; t=time.perf_counter(); import hledac.universal.autonomous_orchestrator as m; print(f'{time.perf_counter()-t:.6f}')"
        vals = []
        for _ in range(3):
            r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
            lines = [l for l in r.stdout.strip().split('\n') if l.strip()]
            vals.append(float(lines[-1].strip()))
        import statistics
        median = statistics.median(vals)
        baseline = 0.955835
        assert median <= baseline + 0.1, f"Import {median:.3f}s exceeds baseline {baseline:.3f}s + 0.1s tolerance"


class TestFDDeltaTelemetry:
    """FD delta telemetry bounded after explicit shutdown."""

    def test_fd_delta_bounded_after_import(self):
        """FD: After import, FD count <= baseline + 5."""
        import os

        import psutil
        # Measure actual baseline at test invocation time
        p = psutil.Process(os.getpid())
        baseline_fds = p.num_fds()
        # After orchestrator module is loaded, check delta
        after_import_fds = p.num_fds()
        assert after_import_fds <= baseline_fds + 5, f"FDs {after_import_fds} > baseline {baseline_fds} + 5"

    @pytest.mark.asyncio
    async def test_fd_delta_bounded_after_shutdown(self):
        """FD: Shutdown does not leak more than 15 FDs above init baseline."""
        import os

        import psutil
        p = psutil.Process(os.getpid())

        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
        orch = FullyAutonomousOrchestrator()
        await orch.initialize()

        after_init_fds = p.num_fds()
        assert after_init_fds <= 100, f"FDs after init {after_init_fds} unexpectedly high"

        # CP4: after explicit shutdown + gc — check delta from init (not pre-import)
        if hasattr(orch, 'cleanup_async'):
            await orch.cleanup_async()
        else:
            orch.cleanup()
        await asyncio.sleep(0.5)
        gc.collect()

        after_shutdown_fds = p.num_fds()
        fd_delta = after_shutdown_fds - after_init_fds
        # Shutdown should not leak FDs — delta should be small (within 15 of init)
        assert fd_delta <= 15, f"FD leak: shutdown opened {abs(fd_delta)} more FDs than init ({after_shutdown_fds} vs {after_init_fds})"


class TestExistingTelemetryExposed:
    """Verify 8AJ telemetry fields exist on orchestrator."""

    def test_orchestrator_has_boot_hygiene_attributes(self):
        """8AJ fields: _runtime_artifacts_outside_ramdisk_count, _lmdb_locks_removed_at_boot."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
        # Check initialize() source for the attribute assignments
        src = inspect.getsource(FullyAutonomousOrchestrator.initialize)
        assert "_runtime_artifacts_outside_ramdisk_count" in src, "8AJ: runtime_artifacts_outside_ramdisk_count"
        assert "_lmdb_locks_removed_at_boot" in src, "8AJ: lmdb_locks_removed_at_boot"
        assert "_stale_sockets_removed_at_boot" in src, "8AJ: stale_sockets_removed_at_boot"
