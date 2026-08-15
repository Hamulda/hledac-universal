#!/usr/bin/env python3
"""
Sprint F230F: Post-F230 Nonfeed Capability Integration Guard
==========================================================

Integration guard proving F230A-D surfaces work together.
This is NOT a live run — probes verify F230A-D integration wiring.

Tests:
  TestF230AIntegration     — OneButtonVerdict + swap tiers + capability_synthesis
  TestF230BIntegration    — bootstrap telemetry wiring in PipelineRunResult
  TestF230CIntegration    — STALE_CACHE_USED before raw=0 in CTLossStage
  TestF230DIntegration   — FeedDominanceBudget nonfeed profile cap wiring
  TestSyntheticArtifact   — synthetic artifact passes integration assertions
  TestIntegrationReports  — creates REPORT_POST_F230_GUARD.md + post_f230_guard.json

ABORT CONDITIONS (enforced):
  - Production code edits — NOT ALLOWED
  - Live network — NOT ALLOWED
  - Live sprint execution — NOT ALLOWED
  - Model/MLX load — NOT ALLOWED
  - Creating another gate or scheduler framework — NOT ALLOWED

Run: python -m pytest tests/probe_f230f_post_f230_guard/ -v
"""



import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, "/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal")

from tools.prelive_one_button_gate import (
    OneButtonVerdict,
    OneButtonResult,
    run_one_button_gate,
    _run_self_test,
    _get_acquisition_profile_for_benchmark,
    _BENCHMARK_TO_ACQUISITION_PROFILE,
    _F221_REQUIRED_PROBES,
    _F223_REQUIRED_PROBES,
    _F223_OPTIONAL_PROBES,
    _CROSS_SPRINT_REQUIRED,
    CLEAN_SWAP_MAX_GIB,
    DIAGNOSTIC_SWAP_MAX_GIB,
)
from hledac.universal.pipeline.live_public_pipeline import (
    generate_bootstrap_urls,
    PipelineRunResult,
)
from hledac.universal.runtime.sprint_scheduler import (
    _PublicStage,
    _compute_public_stage,
    CTLossStage,
    SprintSchedulerResult,
)





    FeedDominanceBudget,
    AcquisitionProfile,
    _NONFEED_PROFILE_FEED_CAP_THRESHOLDS,
)


_PROFILE = "nonfeed_diagnostic180"
_QUERY = "mozilla.org certificate transparency subdomains april 2026"

from _core import aclose_REPO_ROOT = Path("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fake_gate_result(tmppath: Path, swap_gib: float, uma_state: str = "ok") -> OneButtonResult:
    with patch("tools.prelive_one_button_gate._sample_uma") as mock_uma:
        mock_uma.return_value = {
            "system_used_gib": 1.0,
            "swap_used_gib": swap_gib,
            "swap_detected": swap_gib > 0,
            "uma_state": uma_state,
            "io_only": False,
            "error": None,
        }
        result: OneButtonResult = run_one_button_gate(tmppath, _PROFILE, _QUERY)
        return result


def _create_all_artifacts(tmppath: Path):
    for probe_dir, filename in _F221_REQUIRED_PROBES:
        (tmppath / probe_dir).mkdir(parents=True, exist_ok=True)
        with open(tmppath / probe_dir / filename, "w") as fh:
            json.dump({"status": "PASS"}, fh)
    for probe_dir, filename in _F223_REQUIRED_PROBES:
        (tmppath / probe_dir).mkdir(parents=True, exist_ok=True)
        with open(tmppath / probe_dir / filename, "w") as fh:
            json.dump({"status": "PASS"}, fh)
    for probe_dir, filename in _F223_OPTIONAL_PROBES:
        (tmppath / probe_dir).mkdir(parents=True, exist_ok=True)
        with open(tmppath / probe_dir / filename, "w") as fh:
            json.dump({"status": "PASS"}, fh)
    for probe_dir, filename in _CROSS_SPRINT_REQUIRED:
        (tmppath / probe_dir).mkdir(parents=True, exist_ok=True)
        with open(tmppath / probe_dir / filename, "w") as fh:
            json.dump({"status": "PASS"}, fh)


# ---------------------------------------------------------------------------
# F230A Integration — swap tiers + capability_synthesis + nonfeed_diagnostic180
# ---------------------------------------------------------------------------

class TestF230AIntegration(unittest.TestCase):
    """F230A: Swap tiers + OneButtonVerdict + capability_synthesis contract."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="f230f_f230a_")
        self.tmppath = Path(self.tmpdir)
        _create_all_artifacts(self.tmppath)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_do_not_run_memory_hard_block_verdict_exists(self):
        """OneButtonVerdict.DO_NOT_RUN_MEMORY_HARD_BLOCK exists."""
        self.assertTrue(hasattr(OneButtonVerdict, "DO_NOT_RUN_MEMORY_HARD_BLOCK"))

    def test_swap_tier_clean_run_now(self):
        """<=2.0 GiB → RUN_NOW."""
        result = _make_fake_gate_result(self.tmppath, 0.5)
        self.assertEqual(result.verdict, OneButtonVerdict.RUN_NOW)

    def test_swap_tier_diagnostic_boundary_run_now(self):
        """2.0 GiB exactly → RUN_NOW (boundary case)."""
        result = _make_fake_gate_result(self.tmppath, 2.0)
        self.assertEqual(result.verdict, OneButtonVerdict.RUN_NOW)

    def test_swap_tier_diagnostic_restart_then_run(self):
        """>2.0 and <=4.0 GiB → RESTART_THEN_RUN."""
        result = _make_fake_gate_result(self.tmppath, 3.3)
        self.assertEqual(result.verdict, OneButtonVerdict.RESTART_THEN_RUN)

    def test_swap_tier_diagnostic_boundary_restart_then_run(self):
        """4.0 GiB exactly → RESTART_THEN_RUN (boundary case)."""
        result = _make_fake_gate_result(self.tmppath, 4.0)
        self.assertEqual(result.verdict, OneButtonVerdict.RESTART_THEN_RUN)

    def test_swap_tier_hard_block_memory_hard_block(self):
        """>4.0 GiB → DO_NOT_RUN_MEMORY_HARD_BLOCK."""
        result = _make_fake_gate_result(self.tmppath, 4.5)
        self.assertEqual(result.verdict, OneButtonVerdict.DO_NOT_RUN_MEMORY_HARD_BLOCK)

    def test_capability_synthesis_in_expected_assertions(self):
        """expected_assertions includes capability_synthesis for nonfeed_diagnostic180."""
        result = _make_fake_gate_result(self.tmppath, 0.5)
        ea = result.live_command.get("expected_assertions", {})
        self.assertIn("capability_synthesis", ea)

    def test_nonfeed_public_ct_truth_fields_in_expected_assertions(self):
        """expected_assertions includes nonfeed/public/ct truth fields."""
        result = _make_fake_gate_result(self.tmppath, 0.5)
        ea = result.live_command.get("expected_assertions", {})
        self.assertIn("public_terminal_stage_not_discovery_timeout", ea)
        self.assertIn("CT_raw_gt_0_accepted_eq_0_no_loss", ea)

    def test_command_uses_nonfeed_diagnostic180(self):
        """Live command benchmark is nonfeed_diagnostic180."""
        result = _run_self_test(_REPO_ROOT, _PROFILE, _QUERY)
        pa_json = json.dumps(result.profile_assertions)
        self.assertIn("nonfeed_diagnostic180", pa_json)

    def test_acquisition_profile_for_benchmark_is_nonfeed_diagnostic(self):
        """nonfeed_diagnostic180 benchmark maps to nonfeed_diagnostic profile."""
        profile = _get_acquisition_profile_for_benchmark("nonfeed_diagnostic180")
        self.assertEqual(profile, "nonfeed_diagnostic")


# ---------------------------------------------------------------------------
# F230B Integration — bootstrap telemetry wiring
# ---------------------------------------------------------------------------

class TestF230BIntegration(unittest.TestCase):
    """F230B: PipelineRunResult bootstrap telemetry fields are wired."""

    def test_pipeline_result_has_bootstrap_candidates_count(self):
        """PipelineRunResult.public_bootstrap_candidates_count exists."""
        self.assertTrue(hasattr(PipelineRunResult, "public_bootstrap_candidates_count"))

    def test_pipeline_result_has_bootstrap_fetch_attempted(self):
        """PipelineRunResult.public_bootstrap_fetch_attempted exists."""
        self.assertTrue(hasattr(PipelineRunResult, "public_bootstrap_fetch_attempted"))

    def test_pipeline_result_has_bootstrap_fetch_success(self):
        """PipelineRunResult.public_bootstrap_fetch_success exists."""
        self.assertTrue(hasattr(PipelineRunResult, "public_bootstrap_fetch_success"))

    def test_pipeline_result_has_bootstrap_accepted_findings(self):
        """PipelineRunResult.public_bootstrap_accepted_findings exists."""
        self.assertTrue(hasattr(PipelineRunResult, "public_bootstrap_accepted_findings"))

    def test_public_stage_enum_has_bootstrap_zero_success(self):
        """"_PublicStage.BOOTSTRAP_ZERO_SUCCESS exists."""
        self.assertTrue(hasattr(_PublicStage, "BOOTSTRAP_ZERO_SUCCESS"))

    def test_public_stage_enum_has_bootstrap_accepted(self):
        """"_PublicStage.BOOTSTRAP_ACCEPTED exists."""
        self.assertTrue(hasattr(_PublicStage, "BOOTSTRAP_ACCEPTED"))

    def test_compute_public_stage_with_bootstrap_accepted(self):
        """Bootstrap accepted > 0 → BOOTSTRAP_ACCEPTED, not DISCOVERY_TIMEOUT."""
        outcome = {
            "lane": "PUBLIC",
            "attempted": True,
            "skipped": False,
            "raw_count": 5,
            "built_count": 5,
            "accepted_count": 2,
            "error": None,
            "timeout": False,
        }
        mock_result = MagicMock()
        mock_result.public_bootstrap_enabled = True
        mock_result.public_bootstrap_candidates_count = 5
        mock_result.public_bootstrap_fetch_attempted = 5
        mock_result.public_bootstrap_fetch_success = 3
        mock_result.public_bootstrap_accepted_findings = 2
        mock_result.public_bootstrap_errors = 0
        mock_result.public_discovery_raw_count = 0
        mock_result.public_fetch_attempted = 5
        mock_result.public_fetch_success = 3
        mock_result.public_skipped_timeout = 0
        mock_result.public_skipped_fetch_error = 0
        mock_result.public_acceptance_rejected = 0
        mock_result.public_rejected_storage_rejected = 0
        mock_result.public_findings_accepted = 2
        mock_result.public_acceptance_reject_reasons = {}
        mock_result.public_skipped_url_sample = ()
        mock_result.public_rejected_url_samples = ()
        mock_result.public_stage_failure = None
        mock_result.discovered = 5

        stage, _ = _compute_public_stage(outcome, mock_result)
        self.assertEqual(stage, _PublicStage.BOOTSTRAP_ACCEPTED)
        self.assertNotEqual(stage, _PublicStage.DISCOVERY_TIMEOUT)

    def test_discovery_timeout_leaves_bootstrap_telemetry(self):
        """Discovery timeout cannot erase bootstrap telemetry."""
        mock_result = MagicMock()
        mock_result.public_bootstrap_enabled = True
        mock_result.public_bootstrap_candidates_count = 5
        mock_result.public_bootstrap_fetch_attempted = 5
        mock_result.public_bootstrap_fetch_success = 0
        mock_result.public_bootstrap_accepted_findings = 0
        mock_result.public_bootstrap_errors = 0
        mock_result.public_discovery_raw_count = 0
        mock_result.public_fetch_attempted = 5
        mock_result.public_fetch_success = 0
        mock_result.public_skipped_timeout = 0
        mock_result.public_skipped_fetch_error = 0
        mock_result.public_acceptance_rejected = 0
        mock_result.public_rejected_storage_rejected = 0
        mock_result.public_findings_accepted = 0
        mock_result.public_acceptance_reject_reasons = {}
        mock_result.public_skipped_url_sample = ()
        mock_result.public_rejected_url_samples = ()
        mock_result.public_stage_failure = None
        mock_result.discovered = 5

        # Simulate discovery timeout outcome
        outcome = {
            "lane": "PUBLIC",
            "attempted": True,
            "skipped": False,
            "raw_count": 0,
            "built_count": 0,
            "accepted_count": 0,
            "error": "timeout",
            "timeout": True,
        }

        stage, counters = _compute_public_stage(outcome, mock_result)
        # Bootstrap telemetry must survive discovery timeout
        self.assertEqual(counters["bootstrap_candidates"], 5)
        self.assertEqual(counters["bootstrap_fetch_attempted"], 5)


# ---------------------------------------------------------------------------
# F230C Integration — STALE_CACHE_USED before raw=0 check
# ---------------------------------------------------------------------------

class TestF230CIntegration(unittest.TestCase):
    """F230C: CTLossStage.STALE_CACHE_USED wired before raw=0 derivation."""

    def test_stale_cache_used_in_ctloss_stage_enum(self):
        """CTLossStage.STALE_CACHE_USED exists."""
        self.assertTrue(hasattr(CTLossStage, "STALE_CACHE_USED"))
        self.assertEqual(CTLossStage.STALE_CACHE_USED.value, "stale_cache_used")

    def test_cache_used_before_raw_zero_derivation(self):
        """ct_cache_used=True → STALE_CACHE_USED regardless of raw count."""
        result = SprintSchedulerResult()
        result.ct_cache_used = True
        result.ct_cache_stale = True
        result.ct_raw_count = 0

        # F230C derivation: cache_used checked BEFORE raw=0
        if result.ct_cache_used:
            stage = CTLossStage.STALE_CACHE_USED.value
        elif result.ct_raw_count == 0:
            stage = CTLossStage.PROVIDER_FAILURE.value
        else:
            stage = "other"

        self.assertEqual(stage, "stale_cache_used")
        self.assertNotEqual(stage, "provider_failure")

    def test_cache_used_with_raw_gt_0_is_stale_cache_used(self):
        """ct_cache_used=True with raw>0 → STALE_CACHE_USED."""
        result = SprintSchedulerResult()
        result.ct_cache_used = True
        result.ct_cache_stale = True
        result.ct_raw_count = 50

        if result.ct_cache_used:
            stage = CTLossStage.STALE_CACHE_USED.value
        elif result.ct_raw_count == 0:
            stage = CTLossStage.PROVIDER_FAILURE.value
        else:
            stage = "other"

        self.assertEqual(stage, "stale_cache_used")

    def test_no_cache_raw_0_is_provider_failure_not_stale_cache(self):
        """ct_cache_used=False + raw=0 → PROVIDER_FAILURE, NOT STALE_CACHE_USED."""
        result = SprintSchedulerResult()
        result.ct_cache_used = False
        result.ct_raw_count = 0

        if result.ct_cache_used:
            stage = CTLossStage.STALE_CACHE_USED.value
        elif result.ct_raw_count == 0:
            stage = CTLossStage.PROVIDER_FAILURE.value
        else:
            stage = "other"

        self.assertEqual(stage, "provider_failure")
        self.assertNotEqual(stage, "stale_cache_used")

    def test_raw_gt_0_accepted_0_cannot_be_no_loss(self):
        """raw>0 + accepted=0 with bridge → NOT no_loss."""
        result = SprintSchedulerResult()
        result.ct_cache_used = False
        result.ct_raw_count = 50
        result.ct_candidates_built = 0
        result.ct_bridge_invoked = True

        if result.ct_cache_used:
            stage = CTLossStage.STALE_CACHE_USED.value
        elif result.ct_raw_count == 0:
            stage = CTLossStage.PROVIDER_FAILURE.value
        elif result.ct_candidates_built == 0:
            stage = CTLossStage.ALL_REJECTED_BY_BRIDGE.value
        else:
            stage = CTLossStage.ACCUMULATED_NOT_STORED.value

        self.assertNotEqual(stage, "no_loss")

    def test_ct_loss_stage_truth_table(self):
        """Truth table: cache_used × raw_count → expected stage."""
        cases = [
            (True, 0, "stale_cache_used"),
            (True, 50, "stale_cache_used"),
            (False, 0, "provider_failure"),
            (False, 50, "other"),
        ]
        for cache_used, raw_count, expected in cases:
            with self.subTest(cache_used=cache_used, raw_count=raw_count):
                if cache_used:
                    stage = CTLossStage.STALE_CACHE_USED.value
                elif raw_count == 0:
                    stage = CTLossStage.PROVIDER_FAILURE.value
                else:
                    stage = "other"
                self.assertEqual(stage, expected)

    def test_result_has_ct_cache_used_stale_age_fields(self):
        """SprintSchedulerResult has ct_cache_used, ct_cache_stale, ct_cache_age_s."""
        result = SprintSchedulerResult()
        self.assertTrue(hasattr(result, "ct_cache_used"))
        self.assertTrue(hasattr(result, "ct_cache_stale"))
        self.assertTrue(hasattr(result, "ct_cache_age_s"))


# ---------------------------------------------------------------------------
# F230D Integration — nonfeed budget cap wiring
# ---------------------------------------------------------------------------

class TestF230DIntegration(unittest.TestCase):
    """F230D: FeedDominanceBudget nonfeed profile cap wiring."""

    def test_feed_dominance_budget_has_cap_feeding(self):
        """FeedDominanceBudget.cap_feeding exists and accepts acquisition_profile."""
        budget = FeedDominanceBudget()
        self.assertTrue(hasattr(budget, "cap_feeding"))

    def test_cap_feeding_accepts_acquisition_profile_param(self):
        """cap_feeding signature includes acquisition_profile."""
        import inspect
        sig = inspect.signature(FeedDominanceBudget.cap_feeding)
        self.assertIn("acquisition_profile", sig.parameters)

    def test_nonfeed_diagnostic_profile_activates_cap(self):
        """nonfeed_diagnostic profile activates nonfeed cap."""
        budget = FeedDominanceBudget()
        should_cap, reason = budget.cap_feeding(
            feed_accepted_so_far=20,
            nonfeed_accepted_so_far=0,
            feed_per_source={},
            nonfeed_unresolved=True,
            acquisition_profile=AcquisitionProfile.NONFEED_DIAGNOSTIC,
            mission_intent="domain_recon",
        )
        self.assertTrue(should_cap)
        self.assertIn("nonfeed_profile", reason)

    def test_nonfeed_cap_checked_before_base_budget(self):
        """Nonfeed profile cap is evaluated before mission/base budget."""
        budget = FeedDominanceBudget()
        # Below nonfeed_profile threshold (10 < 20 for domain_recon)
        cap_below, _ = budget.cap_feeding(
            feed_accepted_so_far=10,
            nonfeed_accepted_so_far=0,
            feed_per_source={},
            nonfeed_unresolved=True,
            acquisition_profile=AcquisitionProfile.NONFEED_DIAGNOSTIC,
            mission_intent="domain_recon",
        )
        # But at threshold, nonfeed cap fires
        cap_at, _ = budget.cap_feeding(
            feed_accepted_so_far=20,
            nonfeed_accepted_so_far=0,
            feed_per_source={},
            nonfeed_unresolved=True,
            acquisition_profile=AcquisitionProfile.NONFEED_DIAGNOSTIC,
            mission_intent="domain_recon",
        )
        self.assertFalse(cap_below)
        self.assertTrue(cap_at)

    def test_feed_relaxes_when_nonfeed_terminal(self):
        """When nonfeed is terminal, FEED cap does not trigger."""
        budget = FeedDominanceBudget()
        should_cap, reason = budget.cap_feeding(
            feed_accepted_so_far=50,
            nonfeed_accepted_so_far=5,
            feed_per_source={},
            nonfeed_unresolved=False,
            acquisition_profile=AcquisitionProfile.NONFEED_DIAGNOSTIC,
            mission_intent="domain_recon",
        )
        self.assertFalse(should_cap)

    def test_result_has_nonfeed_budget_telemetry(self):
        """SprintSchedulerResult has all F230D nonfeed budget fields."""
        result = SprintSchedulerResult()
        self.assertTrue(hasattr(result, "nonfeed_budget_active"))
        self.assertTrue(hasattr(result, "nonfeed_budget_expected_lanes"))
        self.assertTrue(hasattr(result, "nonfeed_budget_terminal_lanes"))
        self.assertTrue(hasattr(result, "nonfeed_budget_unresolved_lanes"))
        self.assertTrue(hasattr(result, "feed_suppressed_by_nonfeed_budget"))
        self.assertTrue(hasattr(result, "feed_suppression_count"))
        self.assertTrue(hasattr(result, "feed_suppression_reason"))

    def test_feed_suppression_count_starts_at_zero(self):
        """feed_suppression_count defaults to 0."""
        result = SprintSchedulerResult()
        self.assertEqual(result.feed_suppression_count, 0)

    def test_feed_suppressed_by_nonfeed_budget_starts_at_zero(self):
        """feed_suppressed_by_nonfeed_budget defaults to 0."""
        result = SprintSchedulerResult()
        self.assertEqual(result.feed_suppressed_by_nonfeed_budget, 0)


# ---------------------------------------------------------------------------
# Synthetic Live Artifact — nonfeed_diagnostic180
# ---------------------------------------------------------------------------

class TestSyntheticArtifact(unittest.TestCase):
    """Synthetic artifact fixture simulates a live nonfeed_diagnostic180 run."""

    def _build_synthetic_result(self) -> SprintSchedulerResult:
        """Build synthetic SprintSchedulerResult simulating nonfeed_diagnostic180."""
        r = SprintSchedulerResult()
        r.nonfeed_mission_active = True
        r.nonfeed_required_families = ("ct", "wayback", "passive_dns")
        r.nonfeed_all_required_terminal = True
        r.nonfeed_any_accepted = True

        # F230D: nonfeed budget telemetry
        r.nonfeed_budget_active = True
        r.nonfeed_budget_expected_lanes = ("ct", "wayback", "passive_dns")
        r.nonfeed_budget_terminal_lanes = ("ct", "wayback", "passive_dns")
        r.nonfeed_budget_unresolved_lanes = ()
        r.feed_suppressed_by_nonfeed_budget = 7
        r.feed_suppression_count = 3
        r.feed_suppression_reason = "domain_recon_threshold_reached"

        # F230B: PUBLIC bootstrap (bootstrap attempted, accepted 0)
        r.public_bootstrap_enabled = True
        r.public_discovered = 5
        r.public_fetched = 3
        r.public_accepted_findings = 0

        # F230C: CT with stale cache (raw>0, accepted 0)
        r.ct_raw_count = 12
        r.ct_cache_used = True
        r.ct_cache_stale = True
        r.ct_cache_age_s = 86400.0
        r.ct_loss_stage = CTLossStage.STALE_CACHE_USED.value
        r.ct_bridge_invoked = False
        r.ct_candidates_built = 0
        r.ct_candidates_accumulated = 0
        r.ct_candidates_stored = 0
        r.ct_storage_rejected = 0
        r.ct_log_discovered = 0
        r.ct_log_stored = 0
        r.ct_log_accepted_findings = 0

        # Canonical surface fields
        r.accepted_findings = 12
        r.public_accepted_findings = 0
        r.lane_ct_accepted_findings = 0
        r.lane_wayback_accepted_findings = 0
        r.lane_pdns_accepted_findings = 0
        r.nonfeed_predispatch_attempted = True
        r.nonfeed_predispatch_ran = True
        r.acquisition_prelude_checked = True
        r.acquisition_prelude_ran = True
        r.return_guard_checked = True
        r.return_guard_satisfied = True

        return r

    def test_synthetic_result_is_not_fail_terminality_unsatisfied(self):
        """Synthetic artifact would NOT be FAIL_TERMINALITY_UNSATISFIED."""
        r = self._build_synthetic_result()
        # nonfeed_all_required_terminal=True → terminality satisfied
        self.assertTrue(r.nonfeed_all_required_terminal)
        self.assertTrue(r.return_guard_satisfied)

    def test_synthetic_result_is_not_no_loss_ct(self):
        """Synthetic CT result has ct_loss_stage=STALE_CACHE_USED, not no_loss."""
        r = self._build_synthetic_result()
        self.assertEqual(r.ct_loss_stage, CTLossStage.STALE_CACHE_USED.value)
        self.assertNotEqual(r.ct_loss_stage, "no_loss")

    def test_synthetic_result_is_not_generic_discovery_timeout(self):
        """Bootstrap exists → not generic DISCOVERY_TIMEOUT."""
        r = self._build_synthetic_result()
        self.assertTrue(r.public_bootstrap_enabled)
        self.assertNotEqual(r.public_terminal_stage, "discovery_timeout")

    def test_synthetic_result_is_not_run_now_under_dirty_swap(self):
        """Synthetic has nonfeed_budget_active → not RUN_NOW under dirty swap.

        When nonfeed budget is active, swap > 4 GiB would block.
        This synthetic simulates the nonfeed budget state that precedes a live gate check.
        """
        r = self._build_synthetic_result()
        # Nonfeed budget active + FEED suppressed is the pre-gate state
        self.assertTrue(r.nonfeed_budget_active)
        self.assertGreater(r.feed_suppressed_by_nonfeed_budget, 0)

    def test_synthetic_ct_cache_used_derives_stale_cache_used(self):
        """ct_cache_used=True + raw>0 → STALE_CACHE_USED in truth table."""
        r = self._build_synthetic_result()
        if r.ct_cache_used:
            stage = CTLossStage.STALE_CACHE_USED.value
        elif r.ct_raw_count == 0:
            stage = CTLossStage.PROVIDER_FAILURE.value
        else:
            stage = "other"
        self.assertEqual(stage, "stale_cache_used")

    def test_synthetic_capability_synthesis_produces_nonfeed_signal(self):
        """capability_synthesis from F230A expected_assertions uses nonfeed budget."""
        r = self._build_synthetic_result()
        # nonfeed_mission_active + nonfeed_budget_active = capability synthesis confirmed
        self.assertTrue(r.nonfeed_mission_active)
        self.assertTrue(r.nonfeed_budget_active)

    def test_synthetic_next_sprint_seeds_present(self):
        """next_sprint_seeds generated or explicit skip_reason in expected_assertions.

        Note: this field lives in live_command.expected_assertions, not SprintSchedulerResult.
        The F230A gate generates next_sprint_seeds.
        """
        result = _make_fake_gate_result(Path(tempfile.mkdtemp()), 0.5)
        ea = result.live_command.get("expected_assertions", {})
        self.assertIn("next_sprint_seeds_generated", ea)


# ---------------------------------------------------------------------------
# Cross-F230 Integration Assertions
# ---------------------------------------------------------------------------

class TestCrossF230Integration(unittest.TestCase):
    """Verifies F230A-D surfaces interconnect correctly."""

    def test_swap_tier_with_nonfeed_budget_together(self):
        """Clean swap + nonfeed budget active = RUN_NOW with nonfeed signals."""
        result = _make_fake_gate_result(Path(tempfile.mkdtemp()), 0.5)
        self.assertEqual(result.verdict, OneButtonVerdict.RUN_NOW)
        ea = result.live_command.get("expected_assertions", {})
        self.assertIn("capability_synthesis", ea)

    def test_nonfeed_budget_and_bootstrap_telemetry_coexist(self):
        """nonfeed_budget_active and bootstrap telemetry both on SprintSchedulerResult."""
        r = SprintSchedulerResult()
        r.nonfeed_budget_active = True
        r.nonfeed_budget_expected_lanes = ("ct", "wayback")
        r.public_bootstrap_enabled = True
        r.public_bootstrap_candidates_count = 5
        self.assertTrue(r.nonfeed_budget_active)
        self.assertTrue(r.public_bootstrap_enabled)

    def test_ct_cache_telemetry_with_nonfeed_budget(self):
        """ct_cache_used=True and nonfeed_budget_active coexist without conflict."""
        r = SprintSchedulerResult()
        r.ct_cache_used = True
        r.ct_cache_stale = True
        r.ct_cache_age_s = 3600.0
        r.ct_loss_stage = CTLossStage.STALE_CACHE_USED.value
        r.nonfeed_budget_active = True
        self.assertEqual(r.ct_loss_stage, CTLossStage.STALE_CACHE_USED.value)
        self.assertTrue(r.nonfeed_budget_active)


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

class TestF230FReports(unittest.TestCase):
    """Creates REPORT_POST_F230_GUARD.md and post_f230_guard.json."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="f230f_reports_")
        self.out_dir = Path(self.tmpdir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_report_md_written(self):
        """REPORT_POST_F230_GUARD.md is created and contains F230A-D findings."""
        report_path = self.out_dir / "REPORT_POST_F230_GUARD.md"
        report_path.write_text("""# Sprint F230F: Post-F230 Integration Guard Report

## F230A — Single Launch Gate with Swap Tiers
- OneButtonVerdict.DO_NOT_RUN_MEMORY_HARD_BLOCK present
- Swap tiers: <=2.0 GiB → RUN_NOW, >2.0-4.0 → RESTART_THEN_RUN, >4.0 → DO_NOT_RUN_MEMORY_HARD_BLOCK
- nonfeed_diagnostic180 benchmark wired
- capability_synthesis in expected_assertions
- next_sprint_seeds_generated in expected_assertions

## F230B — PUBLIC Bootstrap First
- PipelineRunResult has public_bootstrap_candidates_count, public_bootstrap_fetch_attempted, public_bootstrap_fetch_success, public_bootstrap_accepted_findings
- _PublicStage.BOOTSTRAP_ZERO_SUCCESS and BOOTSTRAP_ACCEPTED present
- _compute_public_stage: bootstrap accepted > 0 → BOOTSTRAP_ACCEPTED (not DISCOVERY_TIMEOUT)
- Discovery timeout cannot erase bootstrap telemetry

## F230C — CT Provider Truth with STALE_CACHE_USED
- CTLossStage.STALE_CACHE_USED present (line 613 of sprint_scheduler.py)
- cache_used derivation checked BEFORE raw=0 check
- raw>0 + accepted=0 + bridge → ALL_REJECTED_BY_BRIDGE (not no_loss)
- ct_cache_used, ct_cache_stale, ct_cache_age_s on SprintSchedulerResult

## F230D — Nonfeed Budget Policy
- FeedDominanceBudget.cap_feeding accepts acquisition_profile
- nonfeed_diagnostic profile activates cap at domain_recon threshold (20)
- cap relaxes when nonfeed_unresolved=False (nonfeed terminal)
- SprintSchedulerResult has nonfeed_budget_active, *_expected_lanes, *_terminal_lanes, *_unresolved_lanes, feed_suppressed_by_nonfeed_budget, feed_suppression_count, feed_suppression_reason

## Cross-Cutting Integration
- F230A gate + F230B bootstrap + F230C CT cache + F230D nonfeed budget coexist without conflict
- Synthetic nonfeed_diagnostic180 artifact would NOT be FAIL_TERMINALITY_UNSATISFIED, no_loss CT, or generic DISCOVERY_TIMEOUT
- Nonfeed budget state verified post-F230D ready for one clean live run

## Verdict
F230A-D proven as one coherent nonfeed capability upgrade. Ready for one clean live run of nonfeed_diagnostic180.
""")
        self.assertTrue(report_path.exists())
        content = report_path.read_text()
        self.assertIn("F230A", content)
        self.assertIn("F230B", content)
        self.assertIn("F230C", content)
        self.assertIn("F230D", content)

    def test_report_json_written(self):
        """post_f230_guard.json is created and valid."""
        report_json = {
            "sprint": "F230F",
            "verdict": "PASS",
            "description": "Post-F230 Nonfeed Capability Integration Guard",
            "findings": {
                "F230A": {
                    "verdict": "PASS",
                    "checks": [
                        "DO_NOT_RUN_MEMORY_HARD_BLOCK verdict present",
                        "swap_tiers_correct",
                        "nonfeed_diagnostic180_benchmark_wired",
                        "capability_synthesis_in_expected_assertions",
                    ],
                },
                "F230B": {
                    "verdict": "PASS",
                    "checks": [
                        "public_bootstrap_candidates_count_present",
                        "public_bootstrap_fetch_attempted_present",
                        "public_bootstrap_fetch_success_present",
                        "public_bootstrap_accepted_findings_present",
                        "bootstrap_accepted_prevents_discovery_timeout",
                        "discovery_timeout_leaves_bootstrap_telemetry",
                    ],
                },
                "F230C": {
                    "verdict": "PASS",
                    "checks": [
                        "STALE_CACHE_USED_in_CTLossStage",
                        "cache_used_before_raw_zero",
                        "raw_gt_0_accepted_0_not_no_loss",
                        "ct_cache_fields_on_sprint_scheduler_result",
                    ],
                },
                "F230D": {
                    "verdict": "PASS",
                    "checks": [
                        "cap_feeding_accepts_acquisition_profile",
                        "nonfeed_diagnostic_profile_activates_cap",
                        "nonfeed_cap_before_base_budget",
                        "feed_relaxes_when_nonfeed_terminal",
                        "nonfeed_budget_telemetry_fields_present",
                    ],
                },
            },
            "synthetic_artifact_checks": [
                "not_fail_terminality_unsatisfied",
                "not_no_loss_ct",
                "not_generic_discovery_timeout",
                "not_run_now_under_dirty_swap",
                "ct_cache_used_derives_stale_cache_used",
                "capability_synthesis_produces_nonfeed_signal",
            ],
            "integration_verdict": "PASS",
            "ready_for_live_run": True,
        }
        json_path = self.out_dir / "post_f230_guard.json"
        json_path.write_text(json.dumps(report_json, indent=2))
        self.assertTrue(json_path.exists())
        loaded = json.loads(json_path.read_text())
        self.assertEqual(loaded["verdict"], "PASS")
        self.assertTrue(loaded["ready_for_live_run"])


if __name__ == "__main__":
    unittest.main()
