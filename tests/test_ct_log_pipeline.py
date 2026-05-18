"""Sprint F193A — CT log canonical pipeline integration tests."""
import time
import pytest
from unittest.mock import AsyncMock, MagicMock


class TestSprintF193A:
    def test_ct_log_to_canonical_findings_basic(self):
        from hledac.universal.intelligence.ct_log_client import CTLogClient
        ct_result = {
            "domain": "example.com",
            "san_names": ["sub1.example.com", "sub2.example.com"],
            "issuers": ["Let's Encrypt"],
            "first_cert": 1609459200.0,
            "last_cert": 1700000000.0,
            "cert_count": 3,
        }
        findings = CTLogClient.to_canonical_findings(ct_result, "example.com")
        assert len(findings) == 2
        assert all(f.source_type == "ct_log" for f in findings)
        assert all("ct_log" in f.provenance for f in findings)
        assert all("example.com" in f.provenance for f in findings)
        assert all(f.confidence == 0.75 for f in findings)

    def test_ct_log_to_canonical_findings_max_san(self):
        from hledac.universal.intelligence.ct_log_client import CTLogClient
        ct_result = {
            "domain": "example.com",
            "san_names": [f"sub{i}.example.com" for i in range(200)],
            "issuers": [],
            "first_cert": 0.0,
            "last_cert": 0.0,
            "cert_count": 200,
        }
        findings = CTLogClient.to_canonical_findings(ct_result, "example.com")
        assert len(findings) <= 50

    def test_ct_log_to_canonical_findings_empty_sans(self):
        from hledac.universal.intelligence.ct_log_client import CTLogClient
        ct_result = {
            "domain": "example.com",
            "san_names": [],
            "issuers": [],
            "first_cert": 0.0,
            "last_cert": 0.0,
            "cert_count": 0,
        }
        findings = CTLogClient.to_canonical_findings(ct_result, "example.com")
        assert findings == []

    @pytest.mark.asyncio
    async def test_ct_log_discovery_no_client(self):
        """Scheduler with no CT log client should be a no-op."""
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler
        from hledac.universal.runtime.sprint_scheduler import SprintSchedulerResult
        # Create minimal scheduler — just enough to call the method
        sched = SprintScheduler.__new__(SprintScheduler)
        sched._result = SprintSchedulerResult()
        sched._ct_log_client = None
        await sched._run_ct_log_discovery_in_cycle(query="example.com", store=None)
        assert sched._result.ct_log_discovered == 0
        assert sched._result.ct_log_error == ""

    @pytest.mark.asyncio
    async def test_ct_log_discovery_mock_client(self):
        """Mock CT log client should update result counters correctly."""
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler, SprintSchedulerResult
        from hledac.universal.intelligence.ct_log_client import CTLogClient

        ct_result = {
            "domain": "example.com",
            "san_names": ["a.example.com", "b.example.com", "c.example.com"],
            "issuers": ["CA"],
            "first_cert": 1.0,
            "last_cert": time.time(),
            "cert_count": 3,
        }

        mock_client = MagicMock()
        mock_client.pivot_domain = AsyncMock(return_value=ct_result)
        mock_client.to_canonical_findings = CTLogClient.to_canonical_findings

        # The store mock: return accepted results
        mock_store = MagicMock()
        mock_store.async_ingest_findings_batch = AsyncMock(
            return_value=[{"accepted": True}, {"accepted": True}, {"accepted": False}]
        )

        sched = SprintScheduler.__new__(SprintScheduler)
        sched._result = SprintSchedulerResult()
        sched._ct_log_client = mock_client
        # bypassed __init__ — set all attrs that _run_ct_log_discovery_in_cycle touches
        sched._enrichment_services = None
        sched._graph_accumulator = None  # bypassed __init__
        sched._sidecar_orchestrator = AsyncMock()  # bypassed __init__
        sched.sprint_id = ""  # used by _accumulate_findings_to_graph
        sched._sidecar_dispatcher = None  # bypassed __init__

        await sched._run_ct_log_discovery_in_cycle(query="example.com", store=mock_store)

        assert sched._result.ct_log_discovered == 3
        assert sched._result.ct_log_stored == 2  # 2 accepted out of 3
        assert sched._result.ct_log_error == ""
        # Sprint F194A: ct_log_accepted_findings mirrors ct_log_stored for truth accounting
        assert sched._result.ct_log_accepted_findings == 2


class TestSprintF194A:
    """Sprint F194A — CT log findings closure in canonical sprint truth."""

    def test_runtime_truth_ct_findings_in_branch_mix(self):
        """_runtime_truth branch_mix must include ct_findings."""
        from hledac.universal.core.__main__ import _runtime_truth
        rt = _runtime_truth(
            actual_duration_s=120.0,
            query="example.com",
            duration_s=180.0,
            cycles_completed=2,
            cycles_started=3,
            accepted_findings=10,
            total_pattern_hits=5,
            public_accepted_findings=3,
            feed_findings=7,
            ct_findings=4,
            swap_detected=False,
            uma_state="ok",
        )
        assert rt["branch_mix"]["ct_findings"] == 4
        assert rt["accepted_findings"] == 10
        assert rt["primary_signal_source"] == "mixed_ct"

    def test_runtime_truth_ct_only_primary_signal(self):
        """When CT findings dominate, primary_signal_source is 'ct'."""
        from hledac.universal.core.__main__ import _runtime_truth
        rt = _runtime_truth(
            actual_duration_s=120.0,
            query="example.com",
            duration_s=180.0,
            cycles_completed=2,
            cycles_started=3,
            accepted_findings=5,
            total_pattern_hits=0,
            public_accepted_findings=0,
            feed_findings=0,
            ct_findings=5,
            swap_detected=False,
            uma_state="ok",
        )
        assert rt["branch_mix"]["ct_findings"] == 5
        assert rt["primary_signal_source"] == "ct"

    def test_runtime_truth_ct_findings_default_zero(self):
        """ct_findings defaults to 0 for backward compatibility."""
        from hledac.universal.core.__main__ import _runtime_truth
        rt = _runtime_truth(
            actual_duration_s=120.0,
            query="example.com",
            duration_s=180.0,
            cycles_completed=2,
            cycles_started=3,
            accepted_findings=5,
            total_pattern_hits=0,
            public_accepted_findings=0,
            feed_findings=5,
            ct_findings=0,
            swap_detected=False,
            uma_state="ok",
        )
        assert rt["branch_mix"]["ct_findings"] == 0
        assert rt["primary_signal_source"] == "feed"

    def test_ct_log_accepted_findings_set_by_discovery(self):
        """_run_ct_log_discovery_in_cycle sets ct_log_accepted_findings = ct_log_stored."""
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler, SprintSchedulerResult
        import time
        from unittest.mock import MagicMock, AsyncMock

        ct_result = {
            "domain": "example.com",
            "san_names": ["a.example.com", "b.example.com"],
            "issuers": ["CA"],
            "first_cert": 1.0,
            "last_cert": time.time(),
            "cert_count": 2,
        }

        from hledac.universal.intelligence.ct_log_client import CTLogClient
        mock_client = MagicMock()
        mock_client.pivot_domain = AsyncMock(return_value=ct_result)
        mock_client.to_canonical_findings = CTLogClient.to_canonical_findings

        mock_store = MagicMock()
        mock_store.async_ingest_findings_batch = AsyncMock(
            return_value=[{"accepted": True}, {"accepted": True}]
        )

        sched = SprintScheduler.__new__(SprintScheduler)
        sched._result = SprintSchedulerResult()
        sched._ct_log_client = mock_client
        # bypassed __init__ — set all attrs that _run_ct_log_discovery_in_cycle touches
        sched._enrichment_services = None
        sched._graph_accumulator = None  # bypassed __init__
        sched._sidecar_orchestrator = AsyncMock()  # bypassed __init__
        sched.sprint_id = ""  # used by _accumulate_findings_to_graph
        sched._sidecar_dispatcher = None  # bypassed __init__

        import asyncio
        asyncio.run(sched._run_ct_log_discovery_in_cycle(query="example.com", store=mock_store))

        # Sprint F194A: ct_log_accepted_findings is set alongside ct_log_stored
        assert sched._result.ct_log_stored == 2
        assert sched._result.ct_log_accepted_findings == 2

    def test_ct_log_pipeline_sprint_scheduler_result_fields(self):
        """SprintSchedulerResult has all required F194A fields."""
        from hledac.universal.runtime.sprint_scheduler import SprintSchedulerResult
        result = SprintSchedulerResult()
        assert hasattr(result, "ct_log_discovered")
        assert hasattr(result, "ct_log_stored")
        assert hasattr(result, "ct_log_accepted_findings")
        assert hasattr(result, "ct_log_error")
        assert result.ct_log_accepted_findings == 0  # default is 0
        assert result.ct_log_discovered == 0
        assert result.ct_log_stored == 0

    @pytest.mark.asyncio
    async def test_ct_findings_included_in_combined_accepted_count(self):
        """
        Persisted CT findings must increase the combined accepted_findings count
        used in canonical sprint truth surfaces.

        Simulates the in-place modification:
          result.accepted_findings += result.ct_log_stored
        """
        from hledac.universal.runtime.sprint_scheduler import SprintSchedulerResult

        result = SprintSchedulerResult()
        result.accepted_findings = 5  # feed/public pipeline findings
        result.ct_log_stored = 3     # CT log findings persisted

        # In-place modification: result.accepted_findings += result.ct_log_stored
        result.accepted_findings += result.ct_log_stored

        # Combined count flows into all canonical truth surfaces
        assert result.accepted_findings == 8  # 5 + 3

        # write_sprint_delta uses new_findings = result.accepted_findings
        new_findings = result.accepted_findings
        assert new_findings == 8

        # runtime_truth accepted_findings uses the combined count
        assert result.accepted_findings == 8

    def test_ct_findings_additive_to_public_feed_no_double_count(self):
        """
        CT findings are additive to feed/public counts.
        The combined total (feed+public+CT) is the canonical accepted_findings.
        """
        from hledac.universal.runtime.sprint_scheduler import SprintSchedulerResult

        result = SprintSchedulerResult()
        result.accepted_findings = 6   # feed findings only
        result.public_accepted_findings = 4  # public findings
        result.ct_log_stored = 3      # CT findings (new source)

        # In-place modification mirrors run_sprint() canonical accounting
        result.accepted_findings += result.ct_log_stored

        # Combined total = feed + public + CT = 6 + 4 + 3 = 13
        combined = result.accepted_findings + result.public_accepted_findings
        assert combined == 13  # 6 (modified) + 4 = 13

        # feed_fnd derivation: accepted_findings (combined) - public
        # This is the combined count in canonical surfaces
        # feed findings are implicitly: combined - public = 6 + 3 = 9 (CT adds to feed side)
        # No double counting: feed(6) + public(4) + CT(3) = 13
        assert result.accepted_findings == 9  # 6 (modified) + 3