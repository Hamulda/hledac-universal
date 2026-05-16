"""
Sprint 8VX: Lifecycle Convergence Probe Tests

Loads modules via spec_from_file_location to bypass package __init__.py
(which has a syntax error in brain/model_lifecycle.py).
"""

import pytest
import asyncio
from unittest.mock import MagicMock
import importlib.util
import os
import sys


# 5 levels up from tests/probe_8vx/test_lifecycle_convergence.py
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))
assert os.path.exists(os.path.join(_ROOT, "hledac", "universal")), f"_ROOT={_ROOT} is wrong"
_RUNTIME_LIFECYCLE = os.path.join(_ROOT, "hledac", "universal", "runtime", "sprint_lifecycle.py")
_UTILS_LIFECYCLE = os.path.join(_ROOT, "hledac", "universal", "utils", "sprint_lifecycle.py")
_RUNTIME_SCHEDULER = os.path.join(_ROOT, "hledac", "universal", "runtime", "sprint_scheduler.py")
_BRAIN_INIT = os.path.join(_ROOT, "hledac", "universal", "brain", "__init__.py")
_COORDINATORS_INIT = os.path.join(_ROOT, "hledac", "universal", "coordinators", "__init__.py")


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Load modules directly (bypassing package __init__.py)
runtime_lifecycle = _load("hledac.universal.runtime.sprint_lifecycle", _RUNTIME_LIFECYCLE)
utils_lifecycle = _load("hledac.universal.utils.sprint_lifecycle", _UTILS_LIFECYCLE)
runtime_scheduler = _load("hledac.universal.runtime.sprint_scheduler", _RUNTIME_SCHEDULER)

SprintLifecycleManager = runtime_lifecycle.SprintLifecycleManager
SprintPhase = runtime_lifecycle.SprintPhase
InvalidPhaseTransitionError = runtime_lifecycle.InvalidPhaseTransitionError


class TestCanonicalRuntimeSurface:
    """runtime/sprint_lifecycle must have the canonical lifecycle surface."""

    def test_runtime_import_clean(self):
        """Runtime sprint_lifecycle module imports cleanly."""
        assert SprintLifecycleManager is not None
        assert SprintPhase is not None

    def test_sprint_phase_has_six_values(self):
        """SprintPhase enum must have exactly 6 phases."""
        phases = list(SprintPhase)
        assert len(phases) == 6

    def test_start_transitions_boot_to_warmup(self):
        """start() transitions BOOT → WARMUP."""
        mgr = SprintLifecycleManager()
        assert mgr._current_phase == SprintPhase.BOOT
        mgr.start()
        assert mgr._current_phase == SprintPhase.WARMUP

    def test_tick_returns_sprint_phase(self):
        """tick() returns SprintPhase instance."""
        mgr = SprintLifecycleManager()
        mgr.start()
        phase = mgr.tick()
        assert isinstance(phase, SprintPhase)

    def test_transition_to_enforces_monotonic(self):
        """transition_to() rejects non-monotonic transitions."""
        mgr = SprintLifecycleManager()
        mgr.start()
        mgr.transition_to(SprintPhase.ACTIVE)
        with pytest.raises(InvalidPhaseTransitionError):
            mgr.transition_to(SprintPhase.BOOT)

    def test_transition_to_allows_teardown_from_any(self):
        """TEARDOWN is reachable from any phase (abort path)."""
        mgr = SprintLifecycleManager()
        mgr.start()
        mgr.transition_to(SprintPhase.ACTIVE)
        mgr.transition_to(SprintPhase.TEARDOWN)
        assert mgr._current_phase == SprintPhase.TEARDOWN

    def test_remaining_time_callable(self):
        """remaining_time() is a callable method."""
        mgr = SprintLifecycleManager()
        assert callable(mgr.remaining_time)
        rt = mgr.remaining_time()
        assert isinstance(rt, float) and rt >= 0.0

    def test_should_enter_windup_callable(self):
        """should_enter_windup() is a callable method."""
        mgr = SprintLifecycleManager()
        assert callable(mgr.should_enter_windup)
        assert isinstance(mgr.should_enter_windup(), bool)

    def test_recommended_tool_mode_callable(self):
        """recommended_tool_mode() is callable and returns valid values."""
        mgr = SprintLifecycleManager()
        assert callable(mgr.recommended_tool_mode)
        for thermal in ("nominal", "throttled", "critical"):
            result = mgr.recommended_tool_mode(thermal_state=thermal)
            assert result in ("normal", "prune", "panic")

    def test_mark_export_started_exists(self):
        """mark_export_started() exists and transitions WINDUP→EXPORT."""
        mgr = SprintLifecycleManager()
        mgr.start()
        mgr.transition_to(SprintPhase.ACTIVE)
        mgr.transition_to(SprintPhase.WINDUP)
        mgr.mark_export_started()
        assert mgr._current_phase == SprintPhase.EXPORT

    def test_mark_teardown_started_exists(self):
        """mark_teardown_started() exists and transitions to TEARDOWN."""
        mgr = SprintLifecycleManager()
        mgr.start()
        mgr.transition_to(SprintPhase.ACTIVE)
        mgr.transition_to(SprintPhase.WINDUP)
        mgr.mark_teardown_started()
        assert mgr._current_phase == SprintPhase.TEARDOWN

    def test_snapshot_returns_json_safe_dict(self):
        """snapshot() returns dict with all expected keys."""
        mgr = SprintLifecycleManager()
        mgr.start()
        snap = mgr.snapshot()
        assert isinstance(snap, dict)
        for k in (
            "sprint_duration_s", "windup_lead_s", "checkpoint_interval_s",
            "checkpoint_path", "started_at_monotonic", "current_phase",
            "entered_phase_at", "export_started", "teardown_started",
            "abort_requested", "abort_reason", "last_checkpoint_at",
        ):
            assert k in snap, f"snapshot missing key: {k}"
        assert isinstance(snap["current_phase"], str)

    def test_snapshot_abort_fields_work(self):
        """snapshot() captures abort state correctly."""
        mgr = SprintLifecycleManager()
        mgr.start()
        mgr.request_abort("test reason")
        snap = mgr.snapshot()
        assert snap["abort_requested"] is True
        assert snap["abort_reason"] == "test reason"

    def test_is_terminal_callable_bool(self):
        """is_terminal() is callable and returns bool."""
        mgr = SprintLifecycleManager()
        assert callable(mgr.is_terminal)
        assert isinstance(mgr.is_terminal(), bool)

    def test_request_abort_sets_flags(self):
        """request_abort() sets _abort_requested and _abort_reason."""
        mgr = SprintLifecycleManager()
        mgr.start()
        mgr.request_abort("OOM")
        assert mgr._abort_requested is True
        assert mgr._abort_reason == "OOM"


class TestCompatAliasesNeeded:
    """
    These tests assert the DESIRED state after convergence.
    They FAIL now because runtime/sprint_lifecycle is missing these aliases.
    They will PASS once compat aliases are added.
    """

    def test_begin_sprint_alias_exists(self):
        """begin_sprint() must exist as COMPAT ALIAS for start()."""
        mgr = SprintLifecycleManager()
        assert hasattr(mgr, "begin_sprint"), "begin_sprint COMPAT ALIAS missing"
        assert callable(mgr.begin_sprint)
        mgr.begin_sprint()
        assert mgr._current_phase == SprintPhase.WARMUP

    def test_mark_warmup_done_alias_exists(self):
        """mark_warmup_done() must exist as COMPAT ALIAS."""
        mgr = SprintLifecycleManager()
        assert hasattr(mgr, "mark_warmup_done"), "mark_warmup_done COMPAT ALIAS missing"
        assert callable(mgr.mark_warmup_done)
        mgr.start()
        mgr.mark_warmup_done()
        assert mgr._current_phase == SprintPhase.ACTIVE

    def test_request_windup_alias_exists(self):
        """request_windup() must exist as COMPAT ALIAS for transition_to(WINDUP)."""
        mgr = SprintLifecycleManager()
        assert hasattr(mgr, "request_windup"), "request_windup COMPAT ALIAS missing"
        assert callable(mgr.request_windup)
        mgr.start()
        mgr.transition_to(SprintPhase.ACTIVE)
        mgr.request_windup()
        assert mgr._current_phase == SprintPhase.WINDUP

    def test_request_export_alias_exists(self):
        """request_export() must exist as COMPAT ALIAS."""
        mgr = SprintLifecycleManager()
        assert hasattr(mgr, "request_export"), "request_export COMPAT ALIAS missing"
        assert callable(mgr.request_export)

    def test_request_teardown_alias_exists(self):
        """request_teardown() must exist as COMPAT ALIAS."""
        mgr = SprintLifecycleManager()
        assert hasattr(mgr, "request_teardown"), "request_teardown COMPAT ALIAS missing"
        assert callable(mgr.request_teardown)

    def test_is_windup_phase_alias_exists(self):
        """is_windup_phase() must exist as COMPAT ALIAS for should_enter_windup()."""
        mgr = SprintLifecycleManager()
        assert hasattr(mgr, "is_windup_phase"), "is_windup_phase COMPAT ALIAS missing"
        assert callable(mgr.is_windup_phase)

    def test_is_active_property_alias(self):
        """is_active property must exist as COMPAT ALIAS."""
        mgr = SprintLifecycleManager()
        assert hasattr(mgr, "is_active"), "is_active COMPAT PROPERTY missing"

    def test_is_winding_down_property_alias(self):
        """is_winding_down property must exist as COMPAT ALIAS."""
        mgr = SprintLifecycleManager()
        assert hasattr(mgr, "is_winding_down"), "is_winding_down COMPAT PROPERTY missing"


class TestUtilsIsTrueCompatShim:
    """utils/sprint_lifecycle must be a compat shim, not lifecycle authority."""

    def test_utils_exports_sprint_lifecycle_state(self):
        """utils must export SprintLifecycleState enum."""
        assert hasattr(utils_lifecycle, "SprintLifecycleState")
        SLS = utils_lifecycle.SprintLifecycleState
        assert SLS is not None

    def test_utils_maybe_resume_free_function(self):
        """maybe_resume() free function must exist (checkpoint seam)."""
        assert callable(utils_lifecycle.maybe_resume)
        assert utils_lifecycle.maybe_resume(None) is False


class TestWorkflowControlWindupSeparation:
    """workflow/control/windup_local layers must stay separated."""

    def test_no_windup_engine_in_runtime_lifecycle(self):
        """runtime/sprint_lifecycle must NOT reference windup_engine."""
        with open(_RUNTIME_LIFECYCLE) as f:
            content = f.read()
        assert "windup_engine" not in content
        assert "WindupEngine" not in content

    def test_no_scheduler_in_runtime_lifecycle(self):
        """runtime/sprint_lifecycle must NOT reference sprint_scheduler at runtime."""
        with open(_RUNTIME_LIFECYCLE) as f:
            content = f.read()
        # TYPE_CHECKING block is OK (type hints only)
        import re
        # Strip TYPE_CHECKING blocks
        cleaned = re.sub(r'if TYPE_CHECKING:.*?(?=^[^ \t]|\Z)', '', content, flags=re.MULTILINE | re.DOTALL)
        assert "sprint_scheduler" not in cleaned

    def test_windup_local_phase_not_in_lifecycle(self):
        """WindupLocalPhase sub-states must NOT be in SprintPhase enum."""
        names = [p.name for p in SprintPhase]
        assert "WINDUP_LOCAL" not in names
        assert "GATHER" not in names
        assert "GENERATE" not in names

    def test_recommended_tool_mode_not_phase_enum(self):
        """recommended_tool_mode is control surface, not a workflow phase."""
        names = [p.name for p in SprintPhase]
        assert "PANIC" not in names
        assert "PRUNE" not in names
        assert "NORMAL" not in names


class TestLifecycleAdapterBridgesBoth:
    """_LifecycleAdapter must correctly bridge runtime vs utils APIs."""

    def test_lifecycle_adapter_class_exists(self):
        """_LifecycleAdapter must exist in sprint_scheduler.py."""
        assert hasattr(runtime_scheduler, "_LifecycleAdapter")

    def test_lifecycle_adapter_has_required_methods(self):
        """_LifecycleAdapter must have all required bridge methods."""
        Adapter = runtime_scheduler._LifecycleAdapter
        mock_lc = MagicMock()
        adapter = Adapter(mock_lc)
        for attr in ("start", "tick", "remaining_time", "is_terminal",
                     "should_enter_windup", "_current_phase",
                     "recommended_tool_mode", "request_abort"):
            assert hasattr(adapter, attr), f"_LifecycleAdapter missing: {attr}"


class TestNoNewLifecycleOwner:
    """No third lifecycle owner must emerge."""

    def test_no_lifecycle_manager_class_in_brain(self):
        """brain/ must not define SprintLifecycleManager class."""
        if os.path.exists(_BRAIN_INIT):
            with open(_BRAIN_INIT) as f:
                content = f.read()
            assert "class SprintLifecycleManager" not in content

    def test_no_lifecycle_manager_class_in_coordinators(self):
        """coordinators/ must not define SprintLifecycleManager class."""
        if os.path.exists(_COORDINATORS_INIT):
            with open(_COORDINATORS_INIT) as f:
                content = f.read()
            assert "class SprintLifecycleManager" not in content


class TestRunWarmupIsOrchestration:
    """run_warmup() is orchestration, NOT lifecycle authority."""

    def test_run_warmup_is_async(self):
        """run_warmup() must be an async function in __main__.py (orchestration, not lifecycle)."""
        # Sprint 8VX: run_warmup moved from runtime/sprint_lifecycle.py → __main__.py
        _MAIN_PY = os.path.join(_ROOT, "hledac", "universal", "__main__.py")
        main_mod = _load("hledac.universal.__main__", _MAIN_PY)
        assert hasattr(main_mod, "run_warmup"), "run_warmup must be in __main__.py (Sprint 8VX)"
        assert asyncio.iscoroutinefunction(main_mod.run_warmup)

    def test_run_warmup_not_on_manager(self):
        """run_warmup must NOT be a SprintLifecycleManager method."""
        mgr = SprintLifecycleManager()
        assert not hasattr(mgr, "run_warmup")


class TestSnapshotContractStable:
    """Snapshot contract must be stable — no breaking changes."""

    def test_snapshot_current_phase_is_enum_name(self):
        """snapshot['current_phase'] must be SprintPhase.name string."""
        mgr = SprintLifecycleManager()
        mgr.start()
        snap = mgr.snapshot()
        assert snap["current_phase"] == mgr._current_phase.name
        assert snap["current_phase"] in [p.name for p in SprintPhase]

    def test_snapshot_started_at_none_if_not_started(self):
        """snapshot['started_at_monotonic'] must be None before start()."""
        mgr = SprintLifecycleManager()
        snap = mgr.snapshot()
        assert snap["started_at_monotonic"] is None
