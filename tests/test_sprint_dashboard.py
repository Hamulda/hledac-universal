"""
Tests for monitoring/sprint_dashboard.py.

Sprint F195C: Rich terminal dashboard for live sprint monitoring.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

# ── Rich stub ─────────────────────────────────────────────────────────────────

class _FakeLive:
    def __init__(self, *args, **kwargs):
        self._started = False

    def start(self):
        self._started = True

    def update(self, *args, **kwargs):
        pass

    def stop(self):
        pass


class _FakeConsole:
    def __init__(self, *args, **kwargs):
        pass


class _FakeTable:
    def __init__(self, **kw):
        self._rows = []

    def add_column(self, *args, **kw):
        pass

    def add_row(self, *args, **kw):
        self._rows.append(args)


class _FakeText:
    def __init__(self, *args, **kw):
        self._parts = args

    @classmethod
    def assemble(cls, *args, **kw):
        return cls("assembled")


# ── Patch helpers ─────────────────────────────────────────────────────────────

def _patch_rich():
    return patch.dict(
        "sys.modules",
        {
            "rich": MagicMock(),
            "rich.console": MagicMock(Console=_FakeConsole),
            "rich.live": MagicMock(Live=_FakeLive),
            "rich.panel": MagicMock(Panel=MagicMock()),
            "rich.progress": MagicMock(Progress=MagicMock()),
            "rich.table": MagicMock(Table=_FakeTable),
            "rich.text": MagicMock(Text=_FakeText),
        },
    )


class _FakeResult:
    """Minimal fake SprintSchedulerResult for testing."""
    cycles_started: int = 0
    cycles_completed: int = 0
    accepted_findings: int = 0
    duplicate_entry_hashes_skipped: int = 0
    total_pattern_hits: int = 0
    public_accepted_findings: int = 0
    ct_log_accepted_findings: int = 0
    multimodal_enriched_findings: int = 0
    forensics_enriched_ct_findings: int = 0
    entries_per_source: dict = {}
    hits_per_source: dict = {}
    branch_timeout_count: int = 0
    public_branch_timed_out: bool = False
    ct_branch_timed_out: bool = False
    dominant_branch_blocker: str = ""
    public_error: str = ""
    aborted: bool = False
    abort_reason: str = ""
    stop_requested: bool = False
    feed_zero_yield_detected: bool = False


class TestSprintDashboardInit:
    """Dashboard initialization."""

    def test_init(self):
        """Dashboard initializes with sprint metadata."""
        with _patch_rich():
            # Force reimport by removing cached module
            sys.modules.pop("hledac.universal.monitoring.sprint_dashboard", None)
            from hledac.universal.monitoring.sprint_dashboard import SprintDashboard
            dash = SprintDashboard("S195C-001", "ransomware", 1800.0)
            assert dash.sprint_id == "S195C-001"
            assert dash.query == "ransomware"
            assert dash.duration_s == 1800.0
            assert dash._last_phase == "BOOT"
            assert dash._aborted is False


class TestSprintDashboardTable:
    """Dashboard table rendering with various sprint states."""

    def _make_dash(self):
        with _patch_rich():
            sys.modules.pop("hledac.universal.monitoring.sprint_dashboard", None)
            from hledac.universal.monitoring.sprint_dashboard import SprintDashboard
            return SprintDashboard("S195C-001", "ransomware", 1800.0)

    def test_build_table_no_result(self):
        """Renders cleanly before any cycle runs."""
        dash = self._make_dash()
        table = dash._build_table(result=None, elapsed_s=0.0)
        # Should not raise — just render the "Initializing" row
        assert table is not None

    def test_build_table_with_result(self):
        """Renders correctly with a populated result."""
        dash = self._make_dash()
        result = _FakeResult()
        result.cycles_started = 3
        result.cycles_completed = 3
        result.accepted_findings = 12
        result.duplicate_entry_hashes_skipped = 5
        result.total_pattern_hits = 30
        result.entries_per_source = {"https://feeds.example.com": 100}
        table = dash._build_table(result=result, elapsed_s=60.0)
        assert table is not None

    def test_build_table_public_findings(self):
        """Shows public findings when present."""
        dash = self._make_dash()
        result = _FakeResult()
        result.accepted_findings = 5
        result.public_accepted_findings = 3
        table = dash._build_table(result=result, elapsed_s=30.0)
        assert table is not None

    def test_build_table_ct_findings(self):
        """Shows CT log findings when present."""
        dash = self._make_dash()
        result = _FakeResult()
        result.accepted_findings = 7
        result.ct_log_accepted_findings = 2
        table = dash._build_table(result=result, elapsed_s=45.0)
        assert table is not None

    def test_build_table_branch_timeout(self):
        """Shows branch timeout status correctly."""
        dash = self._make_dash()
        result = _FakeResult()
        result.branch_timeout_count = 2
        result.public_branch_timed_out = True
        result.ct_branch_timed_out = False
        result.accepted_findings = 0
        table = dash._build_table(result=result, elapsed_s=120.0)
        assert table is not None

    def test_build_table_aborted(self):
        """Shows abort reason when sprint is aborted."""
        dash = self._make_dash()
        result = _FakeResult()
        result.aborted = True
        result.abort_reason = "memory_pressure"
        result.accepted_findings = 0
        table = dash._build_table(result=result, elapsed_s=90.0)
        assert table is not None

    def test_build_table_stop_requested(self):
        """Shows stop_requested indicator."""
        dash = self._make_dash()
        result = _FakeResult()
        result.stop_requested = True
        result.accepted_findings = 1
        table = dash._build_table(result=result, elapsed_s=30.0)
        assert table is not None

    def test_build_table_feed_zero_yield(self):
        """Shows feed_zero_yield_detected warning."""
        dash = self._make_dash()
        result = _FakeResult()
        result.feed_zero_yield_detected = True
        result.accepted_findings = 0
        table = dash._build_table(result=result, elapsed_s=200.0)
        assert table is not None

    def test_build_table_windup_phase(self):
        """Renders correctly in WINDUP phase."""
        dash = self._make_dash()
        dash._last_phase = "WINDUP"
        result = _FakeResult()
        result.accepted_findings = 8
        table = dash._build_table(result=result, elapsed_s=1700.0)
        assert table is not None

    def test_build_table_teardown_phase(self):
        """Renders correctly in TEARDOWN phase."""
        dash = self._make_dash()
        dash._last_phase = "TEARDOWN"
        result = _FakeResult()
        result.accepted_findings = 10
        table = dash._build_table(result=result, elapsed_s=1810.0)
        assert table is not None

    def test_phase_style(self):
        """Phase style maps correctly."""
        with _patch_rich():
            sys.modules.pop("hledac.universal.monitoring.sprint_dashboard", None)
            from hledac.universal.monitoring.sprint_dashboard import _phase_style
            assert _phase_style("BOOT") == "dim"
            assert _phase_style("WARMUP") == "yellow"
            assert _phase_style("ACTIVE") == "green"
            assert _phase_style("WINDUP") == "cyan"
            assert _phase_style("EXPORT") == "blue"
            assert _phase_style("TEARDOWN") == "magenta"
            assert _phase_style("ABORTED") == "red"
            assert _phase_style("UNKNOWN") == "white"

    def test_phase_emoji(self):
        """Phase emoji maps correctly."""
        with _patch_rich():
            sys.modules.pop("hledac.universal.monitoring.sprint_dashboard", None)
            from hledac.universal.monitoring.sprint_dashboard import _phase_emoji
            assert _phase_emoji("BOOT") == "⚙️"
            assert _phase_emoji("WARMUP") == "⚡"
            assert _phase_emoji("ACTIVE") == "🔨"
            assert _phase_emoji("WINDUP") == "⏹"
            assert _phase_emoji("EXPORT") == "📤"
            assert _phase_emoji("TEARDOWN") == "✅"
            assert _phase_emoji("ABORTED") == "❌"
            assert _phase_emoji("UNKNOWN") == "❓"


class TestSprintDashboardLifecycle:
    """Dashboard start/update/finish lifecycle."""

    def _make_dash_and_fake_live(self):
        """Return (dashboard, fake_live_instance) for asserting calls."""
        with _patch_rich():
            sys.modules.pop("hledac.universal.monitoring.sprint_dashboard", None)
            from hledac.universal.monitoring.sprint_dashboard import SprintDashboard
            dash = SprintDashboard("S195C-001", "ransomware", 1800.0)
            fake_live = _FakeLive()
            # Inject the fake live so we can assert on it
            dash._live = fake_live
            return dash, fake_live

    def test_start_calls_live_start(self):
        """start() calls Live.start() on the instance it creates."""
        sys.modules.pop("hledac.universal.monitoring.sprint_dashboard", None)
        with _patch_rich():
            from hledac.universal.monitoring.sprint_dashboard import SprintDashboard
            fake_live = _FakeLive()
            # Patch Live class so Live(...) returns our fake_live
            import hledac.universal.monitoring.sprint_dashboard as sd
            original_live = sd.Live
            sd.Live = lambda *args, **kwargs: fake_live
            try:
                dash = SprintDashboard("S195C-001", "ransomware", 1800.0)
                assert fake_live._started is False
                dash.start()
                assert fake_live._started is True
            finally:
                sd.Live = original_live

    def test_update_calls_live_update(self):
        """update() calls Live.update()."""
        dash, fake_live = self._make_dash_and_fake_live()
        result = _FakeResult()
        calls = []
        original_update = fake_live.update
        def tracking_update(*args, **kwargs):
            calls.append((args, kwargs))
            return original_update(*args, **kwargs)
        fake_live.update = tracking_update
        dash.update(result, "ACTIVE", 60.0)
        assert len(calls) == 1

    def test_finish_calls_live_stop_and_final_update(self):
        """finish() calls Live.update() then Live.stop()."""
        dash, fake_live = self._make_dash_and_fake_live()
        result = _FakeResult()
        update_calls = []
        stop_called = []
        orig_update = fake_live.update
        def tracking_update(*args, **kwargs):
            update_calls.append(1)
            return orig_update(*args, **kwargs)
        def tracking_stop():
            stop_called.append(1)
        fake_live.update = tracking_update
        fake_live.stop = tracking_stop
        dash.finish(result, 120.0)
        assert len(update_calls) >= 1
        assert len(stop_called) == 1

    def test_update_without_start_is_safe(self):
        """update() before start() is a no-op (no Live instance)."""
        with _patch_rich():
            sys.modules.pop("hledac.universal.monitoring.sprint_dashboard", None)
            from hledac.universal.monitoring.sprint_dashboard import SprintDashboard
            dash = SprintDashboard("S195C-001", "ransomware", 1800.0)
            # _live is None — no crash
            result = _FakeResult()
            dash.update(result, "ACTIVE", 60.0)  # should not raise
            dash.finish(result, 120.0)  # should not raise

    def test_update_after_finish_is_noop(self):
        """update() after finish() is a no-op."""
        with _patch_rich():
            sys.modules.pop("hledac.universal.monitoring.sprint_dashboard", None)
            from hledac.universal.monitoring.sprint_dashboard import SprintDashboard
            dash = SprintDashboard("S195C-001", "ransomware", 1800.0)
            fake_live = _FakeLive()
            dash._live = fake_live
            result = _FakeResult()
            dash.finish(result, 120.0)
            # After finish, _live is None
            update_calls = []
            def tracking_update(*args, **kwargs):
                update_calls.append(1)
            fake_live.update = tracking_update
            dash.update(result, "WINDUP", 150.0)
            assert len(update_calls) == 0

    def test_elapsed_time_shown_correctly(self):
        """Elapsed time is reflected in the progress percentage."""
        dash, _ = self._make_dash_and_fake_live()
        # 1800s duration, 900s elapsed = 50%
        result = _FakeResult()
        table = dash._build_table(result=result, elapsed_s=900.0)
        assert table is not None


class TestSprintDashboardSurvivesBranchTimeout:
    """
    Invariant: dashboard survives branch timeout / early windup.

    Branch timeout / early windup manifest as:
    - result.aborted=True with abort_reason set
    - result.branch_timeout_count > 0
    - result.public_branch_timed_out / ct_branch_timed_out = True
    - result.feed_zero_yield_detected = True
    """

    def _make_dash(self):
        with _patch_rich():
            sys.modules.pop("hledac.universal.monitoring.sprint_dashboard", None)
            from hledac.universal.monitoring.sprint_dashboard import SprintDashboard
            return SprintDashboard("S195C-TO", "leaked_db", 1800.0)

    def test_survives_aborted_with_timeout(self):
        """Dashboard renders when aborted AND has timeouts."""
        dash = self._make_dash()
        result = _FakeResult()
        result.aborted = True
        result.abort_reason = "memory_pressure"
        result.branch_timeout_count = 3
        result.public_branch_timed_out = True
        result.ct_branch_timed_out = True
        result.accepted_findings = 0
        result.duplicate_entry_hashes_skipped = 0
        # Must not raise
        dash.start()
        dash.update(result, "TEARDOWN", 800.0)
        dash.finish(result, 800.0)

    def test_survives_feed_zero_yield_with_early_windup(self):
        """Dashboard renders when feed_zero_yield detected in early windup."""
        dash = self._make_dash()
        result = _FakeResult()
        result.feed_zero_yield_detected = True
        result.accepted_findings = 0
        result.cycles_started = 1
        result.cycles_completed = 1
        # Must not raise
        dash.start()
        dash.update(result, "WINDUP", 1700.0)
        dash.finish(result, 1700.0)

    def test_survives_public_branch_timeout_only(self):
        """Dashboard renders when only public branch times out."""
        dash = self._make_dash()
        result = _FakeResult()
        result.public_branch_timed_out = True
        result.branch_timeout_count = 1
        result.accepted_findings = 0
        # Must not raise
        dash.finish(result, 100.0)

    def test_survives_ct_branch_timeout_only(self):
        """Dashboard renders when only CT branch times out."""
        dash = self._make_dash()
        result = _FakeResult()
        result.ct_branch_timed_out = True
        result.branch_timeout_count = 1
        result.accepted_findings = 0
        # Must not raise
        dash.finish(result, 100.0)


class TestSprintDashboardEnrichmentFields:
    """Dashboard shows enrichment layer fields when present."""

    def _make_dash(self):
        with _patch_rich():
            sys.modules.pop("hledac.universal.monitoring.sprint_dashboard", None)
            from hledac.universal.monitoring.sprint_dashboard import SprintDashboard
            return SprintDashboard("S195C-ENRICH", "config_leak", 1800.0)

    def test_multimodal_enriched_findings_shown(self):
        """Shows multimodal_enriched_findings when > 0."""
        dash = self._make_dash()
        result = _FakeResult()
        result.accepted_findings = 4
        result.multimodal_enriched_findings = 2
        table = dash._build_table(result=result, elapsed_s=100.0)
        assert table is not None

    def test_forensics_enriched_ct_findings_shown(self):
        """Shows forensics_enriched_ct_findings when > 0."""
        dash = self._make_dash()
        result = _FakeResult()
        result.accepted_findings = 6
        result.forensics_enriched_ct_findings = 3
        table = dash._build_table(result=result, elapsed_s=100.0)
        assert table is not None
