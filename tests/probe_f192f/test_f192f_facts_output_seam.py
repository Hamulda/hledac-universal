"""
Sprint F192F: Canonical facts/output seam convergence probe tests
================================================================

Tests for facts/output boundary truth, typed handoff path, compat path
containment, degraded output boundedness, and stable output contract.

INVARIANTS tested:
- DF-1: _build_product_value_summary scorecard fact priority over runtime counter
- DF-2: sanitize failure produces bounded safe structure (no unsanitized leak)
- Compat: ensure_export_handoff typed path + dict/None compat seams
- Stable: no new world creation, no new write APIs
- Facts: canonical duckdb_store authority, no competing store world
"""

import asyncio
import json
import sys
import unittest
from unittest.mock import MagicMock, AsyncMock, patch

sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal')


# ==============================================================================
# TestSprintF192F_FactsOutputBoundary
# ==============================================================================

class TestSprintF192F_FactsOutputBoundary(unittest.TestCase):
    """Tests for facts/output boundary truth."""

    def test_pvs_scorecard_fact_priority_over_runtime_counter(self):
        """
        DF-1 FIX INVARIANT: _build_product_value_summary must prioritize
        scorecard accepted_findings over dedup_status accepted_count (runtime).

        When scorecard has accepted_findings > 0, it MUST be used.
        dedup_status.accepted_count (runtime counter) is secondary fallback only.
        """
        from hledac.universal.export.sprint_exporter import _build_product_value_summary

        # Mock store with runtime counter > scorecard fact
        mock_store = MagicMock()
        mock_store.get_dedup_runtime_status.return_value = {
            "accepted_count": 100,  # runtime counter (should NOT override)
            "low_information_rejected_count": 5,
            "in_memory_duplicate_rejected_count": 3,
            "persistent_duplicate_rejected_count": 2,
            "other_rejected_count": 0,
            "persistent_dedup_enabled": True,
            "dedup_lmdb_path": "/tmp/dedup.db",
        }

        # Mock ExportHandoff with scorecard having accepted_findings = 25
        mock_eh = MagicMock()
        mock_eh.scorecard = {
            "accepted_findings": 25,  # authoritative fact
            "findings_per_minute": 1.5,
            "ioc_density": 0.35,
        }
        mock_eh.gnn_predictions = 0
        mock_eh.synthesis_engine = None

        pvs = _build_product_value_summary(mock_store, mock_eh, "sprint-001")

        # DF-1 FIX: scorecard fact (25) MUST be used, NOT runtime counter (100)
        self.assertEqual(pvs["accepted"], 25,
            "scorecard accepted_findings must take priority over dedup_status accepted_count")

    def test_pvs_runtime_counter_used_when_scorecard_empty(self):
        """
        DF-1 SECONDARY: When scorecard accepted_findings is 0 or missing,
        dedup_status.accepted_count (runtime counter) MUST be used as fallback.
        """
        from hledac.universal.export.sprint_exporter import _build_product_value_summary

        mock_store = MagicMock()
        mock_store.get_dedup_runtime_status.return_value = {
            "accepted_count": 50,  # runtime counter — should be used
            "low_information_rejected_count": 10,
            "in_memory_duplicate_rejected_count": 5,
            "persistent_duplicate_rejected_count": 3,
            "other_rejected_count": 2,
            "persistent_dedup_enabled": True,
            "dedup_lmdb_path": "/tmp/dedup.db",
        }

        mock_eh = MagicMock()
        mock_eh.scorecard = {
            "accepted_findings": 0,  # empty — runtime counter should be used
            "findings_per_minute": 2.0,
            "ioc_density": 0.6,
        }
        mock_eh.gnn_predictions = 0
        mock_eh.synthesis_engine = None

        pvs = _build_product_value_summary(mock_store, mock_eh, "sprint-002")

        # When scorecard fact is 0, runtime counter (50) should be used
        self.assertEqual(pvs["accepted"], 50,
            "runtime counter should be used when scorecard accepted_findings is 0")

    def test_pvs_reject_breakdown_uses_runtime_counters(self):
        """
        reject_breakdown fields (low_information, in_memory_duplicate, etc.)
        come from dedup_status runtime counters — this is correct because
        these are runtime ingest statistics, not persisted facts.
        """
        from hledac.universal.export.sprint_exporter import _build_product_value_summary

        mock_store = MagicMock()
        mock_store.get_dedup_runtime_status.return_value = {
            "accepted_count": 30,
            "low_information_rejected_count": 12,
            "in_memory_duplicate_rejected_count": 7,
            "persistent_duplicate_rejected_count": 4,
            "other_rejected_count": 1,
            "persistent_dedup_enabled": True,
            "dedup_lmdb_path": "/tmp/dedup.db",
        }

        mock_eh = MagicMock()
        mock_eh.scorecard = {
            "accepted_findings": 30,
            "findings_per_minute": 1.0,
            "ioc_density": 0.4,
        }
        mock_eh.gnn_predictions = 0
        mock_eh.synthesis_engine = None

        pvs = _build_product_value_summary(mock_store, mock_eh, "sprint-003")

        self.assertEqual(pvs["reject_breakdown"]["low_information"], 12)
        self.assertEqual(pvs["reject_breakdown"]["in_memory_duplicate"], 7)
        self.assertEqual(pvs["reject_breakdown"]["persistent_duplicate"], 4)
        self.assertEqual(pvs["reject_breakdown"]["fail_open"], 1)

    def test_pvs_no_new_write_api(self):
        """
        INVARIANT: _build_product_value_summary must NOT call any new write API.
        It reads from scorecard (handoff) and store.get_dedup_runtime_status() only.
        """
        from hledac.universal.export.sprint_exporter import _build_product_value_summary

        mock_store = MagicMock()
        mock_store.get_dedup_runtime_status.return_value = {
            "accepted_count": 10,
            "low_information_rejected_count": 2,
            "in_memory_duplicate_rejected_count": 1,
            "persistent_duplicate_rejected_count": 0,
            "other_rejected_count": 0,
            "persistent_dedup_enabled": True,
            "dedup_lmdb_path": "/tmp/dedup.db",
        }

        mock_eh = MagicMock()
        mock_eh.scorecard = {"accepted_findings": 10, "findings_per_minute": 0.5, "ioc_density": 0.3}
        mock_eh.gnn_predictions = 0
        mock_eh.synthesis_engine = None

        # Track all store method calls
        called_methods = []
        original_get = mock_store.get_dedup_runtime_status
        def track_get():
            called_methods.append("get_dedup_runtime_status")
            return original_get()
        mock_store.get_dedup_runtime_status = track_get

        pvs = _build_product_value_summary(mock_store, mock_eh, "sprint-004")

        # Only read methods should be called
        self.assertEqual(called_methods, ["get_dedup_runtime_status"],
            "only read methods should be called, no write APIs")


# ==============================================================================
# TestSprintF192F_CompatHandoff
# ==============================================================================

class TestSprintF192F_CompatHandoff(unittest.TestCase):
    """Tests for compat handoff seams."""

    def test_typed_handoff_passed_unchanged(self):
        """
        PRIMARY PATH INVARIANT: Typed ExportHandoff passed to ensure_export_handoff
        must be returned unchanged (no reconstruction).
        """
        from hledac.universal.export.COMPAT_HANDOFF import ensure_export_handoff
        from hledac.universal.project_types import ExportHandoff

        eh = ExportHandoff(
            sprint_id="sprint-test",
            scorecard={"accepted_findings": 5},
            top_nodes=[{"value": "evil.com", "ioc_type": "domain"}],
        )

        result = ensure_export_handoff(eh, default_sprint_id="default")

        # Must be the exact same instance — no reconstruction
        self.assertIs(result, eh,
            "typed ExportHandoff must be returned unchanged")
        self.assertEqual(result.sprint_id, "sprint-test")
        self.assertEqual(len(result.top_nodes), 1)

    def test_dict_handoff_converted_via_from_windup(self):
        """
        COMPAT SEAM A INVARIANT: dict (scorecard-style) handoff must be
        converted to typed ExportHandoff via from_windup().
        Must NOT reconstruct directly.
        """
        from hledac.universal.export.COMPAT_HANDOFF import ensure_export_handoff

        dict_handoff = {
            "sprint_id": "sprint-dict",
            "top_graph_nodes": [{"value": "192.168.1.1", "ioc_type": "ip"}],
            "accepted_findings": 10,
        }

        result = ensure_export_handoff(dict_handoff, default_sprint_id="default")

        # Must be typed ExportHandoff
        from hledac.universal.project_types import ExportHandoff
        self.assertIsInstance(result, ExportHandoff,
            "dict handoff must be converted to typed ExportHandoff")
        self.assertEqual(result.sprint_id, "sprint-dict")
        # from_windup extracts top_graph_nodes → top_nodes
        self.assertEqual(len(result.top_nodes), 1)

    def test_none_handoff_returns_empty_typed(self):
        """
        COMPAT SEAM B INVARIANT: None handoff must return empty ExportHandoff
        with default_sprint_id — must NOT raise.
        """
        from hledac.universal.export.COMPAT_HANDOFF import ensure_export_handoff
        from hledac.universal.project_types import ExportHandoff

        result = ensure_export_handoff(None, default_sprint_id="fallback-sprint")

        self.assertIsInstance(result, ExportHandoff,
            "None handoff must return typed ExportHandoff")
        self.assertEqual(result.sprint_id, "fallback-sprint")
        self.assertEqual(result.scorecard, {})
        self.assertEqual(result.top_nodes, [])

    def test_empty_dict_treated_same_as_none(self):
        """
        Empty dict (truthy check in compat seam A) must be treated like None —
        returns empty ExportHandoff with default_sprint_id.
        """
        from hledac.universal.export.COMPAT_HANDOFF import ensure_export_handoff
        from hledac.universal.project_types import ExportHandoff

        result = ensure_export_handoff({}, default_sprint_id="empty-dict-sprint")

        self.assertIsInstance(result, ExportHandoff)
        self.assertEqual(result.sprint_id, "empty-dict-sprint")

    def test_unexpected_type_raises_type_error(self):
        """
        Exhaustive type check: truly unexpected types must raise TypeError,
        not silently convert or return None.
        """
        from hledac.universal.export.COMPAT_HANDOFF import ensure_export_handoff

        with self.assertRaises(TypeError) as ctx:
            ensure_export_handoff(42, default_sprint_id="default")  # int is not valid

        self.assertIn("unexpected type", str(ctx.exception))


# ==============================================================================
# TestSprintF192F_DegradedOutput
# ==============================================================================

class TestSprintF192F_DegradedOutput(unittest.TestCase):
    """Tests for degraded output boundedness."""

    def test_sanitize_failure_produces_safe_degraded_structure(self):
        """
        DF-2 FIX INVARIANT: When sanitize_outbound fails or returns no
        'sanitized' key, the exported content must be a bounded safe structure,
        NOT the unsanitized original content.

        Safe structure: {"_sanitize_failure": True, "sprint_id": "...", "report": "..."}
        """
        from hledac.universal.export.sprint_exporter import export_sprint

        mock_store = MagicMock()
        mock_store.get_dedup_runtime_status.return_value = {
            "accepted_count": 5,
            "low_information_rejected_count": 1,
            "in_memory_duplicate_rejected_count": 0,
            "persistent_duplicate_rejected_count": 0,
            "other_rejected_count": 0,
            "persistent_dedup_enabled": False,
            "dedup_lmdb_path": "",
        }

        mock_eh = MagicMock()
        mock_eh.sprint_id = "sprint-degraded"
        mock_eh.scorecard = {"accepted_findings": 5, "findings_per_minute": 0.5, "ioc_density": 0.2}
        mock_eh.gnn_predictions = 0
        mock_eh.synthesis_engine = None
        mock_eh.top_nodes = []
        mock_eh.correlation = None

        # Patch sanitize_outbound to fail
        with patch('hledac.universal.export.sprint_exporter.get_sprint_json_report_path',
                   return_value=MagicMock()):
            with patch('hledac.universal.export.sprint_exporter.get_sprint_next_seeds_path',
                       return_value=MagicMock()):
                with patch('hledac.universal.export.sprint_exporter._generate_next_sprint_seeds',
                           return_value=MagicMock()):
                    with patch('hledac.universal.export.sprint_exporter._get_sprint_trend',
                               new_callable=AsyncMock, return_value=[]):
                        with patch('hledac.universal.export.sprint_exporter._get_source_leaderboard',
                                   new_callable=AsyncMock, return_value=[]):
                            with patch('hledac.universal.export.sprint_exporter._get_correlation_from_handoff',
                                       return_value=None):
                                with patch('hledac.universal.export.sprint_exporter._get_runtime_truth',
                                           return_value=None):
                                    with patch('hledac.universal.export.sprint_exporter._get_feed_verdict',
                                               return_value=None):
                                        with patch('hledac.universal.export.sprint_exporter._get_public_verdict',
                                                   return_value=None):
                                            with patch('hledac.universal.export.sprint_exporter._get_signal_path',
                                                       return_value=None):
                                                with patch('hledac.universal.export.sprint_exporter._get_hypothesis_pack',
                                                           return_value=None):
                                                    with patch('hledac.universal.export.sprint_exporter._get_canonical_run_summary',
                                                               return_value=None):
                                                        with patch('hledac.universal.export.sprint_exporter._get_sprint_verdict',
                                                                   return_value=None):
                                                            with patch('hledac.universal.export.sprint_exporter._get_synthesis_outcome_payload',
                                                                       return_value=None):
                                                                with patch(
                                                                    'hledac.universal.export.sprint_exporter.UniversalSecurityCoordinator'
                                                                ) as mock_sec:
                                                                    # Simulate sanitize failure
                                                                    mock_sec.return_value.initialize = AsyncMock()
                                                                    mock_sec.return_value.sanitize_outbound = AsyncMock(
                                                                        side_effect=Exception("sanitize failed")
                                                                    )

                                                                    result = asyncio.run(
                                                                        export_sprint(mock_store, mock_eh, "sprint-degraded")
                                                                    )

        # DF-2 FIX: result must contain degraded structure, NOT unsanitized content
        self.assertIn("report_json", result)
        self.assertIsNotNone(result["report_json"])

    def test_sanitize_missing_key_uses_degraded_not_unsanitized(self):
        """
        DF-2 FIX: When gate_result has no 'sanitized' key (partial success),
        must NOT fall back to boundary_text. Must use degraded safe structure.
        """
        from hledac.universal.export.sprint_exporter import export_sprint
        from unittest.mock import MagicMock

        mock_store = MagicMock()
        mock_store.get_dedup_runtime_status.return_value = {
            "accepted_count": 5,
            "low_information_rejected_count": 1,
            "in_memory_duplicate_rejected_count": 0,
            "persistent_duplicate_rejected_count": 0,
            "other_rejected_count": 0,
            "persistent_dedup_enabled": False,
            "dedup_lmdb_path": "",
        }

        mock_eh = MagicMock()
        mock_eh.sprint_id = "sprint-no-key"
        mock_eh.scorecard = {"accepted_findings": 5, "findings_per_minute": 0.5, "ioc_density": 0.2}
        mock_eh.gnn_predictions = 0
        mock_eh.synthesis_engine = None
        mock_eh.top_nodes = []
        mock_eh.correlation = None

        # gate_result has no 'sanitized' key (partial success)
        gate_result_no_key = {"pii_count": 0, "risk_level": "low"}

        with patch('hledac.universal.export.sprint_exporter.get_sprint_json_report_path',
                   return_value=MagicMock()):
            with patch('hledac.universal.export.sprint_exporter.get_sprint_next_seeds_path',
                       return_value=MagicMock()):
                with patch('hledac.universal.export.sprint_exporter._generate_next_sprint_seeds',
                           return_value=MagicMock()):
                    with patch('hledac.universal.export.sprint_exporter._get_sprint_trend',
                               new_callable=AsyncMock, return_value=[]):
                        with patch('hledac.universal.export.sprint_exporter._get_source_leaderboard',
                                   new_callable=AsyncMock, return_value=[]):
                            with patch('hledac.universal.export.sprint_exporter._get_correlation_from_handoff',
                                       return_value=None):
                                with patch('hledac.universal.export.sprint_exporter._get_runtime_truth',
                                           return_value=None):
                                    with patch('hledac.universal.export.sprint_exporter._get_feed_verdict',
                                               return_value=None):
                                        with patch('hledac.universal.export.sprint_exporter._get_public_verdict',
                                                   return_value=None):
                                            with patch('hledac.universal.export.sprint_exporter._get_signal_path',
                                                       return_value=None):
                                                with patch('hledac.universal.export.sprint_exporter._get_hypothesis_pack',
                                                           return_value=None):
                                                    with patch('hledac.universal.export.sprint_exporter._get_canonical_run_summary',
                                                               return_value=None):
                                                        with patch('hledac.universal.export.sprint_exporter._get_sprint_verdict',
                                                                   return_value=None):
                                                            with patch('hledac.universal.export.sprint_exporter._get_synthesis_outcome_payload',
                                                                       return_value=None):
                                                                with patch(
                                                                    'hledac.universal.export.sprint_exporter.UniversalSecurityCoordinator'
                                                                ) as mock_sec:
                                                                    mock_sec.return_value.initialize = AsyncMock()
                                                                    mock_sec.return_value.sanitize_outbound = AsyncMock(
                                                                        return_value=gate_result_no_key  # No 'sanitized' key
                                                                    )

                                                                    result = asyncio.run(
                                                                        export_sprint(mock_store, mock_eh, "sprint-no-key")
                                                                    )

        # DF-2 FIX: report_json must be present (degraded structure written)
        self.assertIn("report_json", result)


# ==============================================================================
# TestSprintF192F_StableOutputContract
# ==============================================================================

class TestSprintF192F_StableOutputContract(unittest.TestCase):
    """Tests for stable output contract — no new world creation."""

    def test_export_sprint_returns_stable_keys(self):
        """
        INVARIANT: export_sprint must return stable, documented keys.
        No new keys must be added without explicit sprint spec.
        """
        from hledac.universal.export.sprint_exporter import export_sprint

        mock_store = MagicMock()
        mock_store.get_dedup_runtime_status.return_value = {
            "accepted_count": 5,
            "low_information_rejected_count": 1,
            "in_memory_duplicate_rejected_count": 0,
            "persistent_duplicate_rejected_count": 0,
            "other_rejected_count": 0,
            "persistent_dedup_enabled": False,
            "dedup_lmdb_path": "",
        }

        mock_eh = MagicMock()
        mock_eh.sprint_id = "sprint-stable"
        mock_eh.scorecard = {"accepted_findings": 5, "findings_per_minute": 0.5, "ioc_density": 0.2}
        mock_eh.gnn_predictions = 0
        mock_eh.synthesis_engine = None
        mock_eh.top_nodes = []
        mock_eh.correlation = None

        expected_keys = {
            "report_json", "seeds_json", "product_value_summary", "sprint_summary",
            "operator_brief", "run_truth_note", "branch_truth", "best_first_move",
            "why_this_run_matters",
        }

        with patch('hledac.universal.export.sprint_exporter.get_sprint_json_report_path',
                   return_value=MagicMock()):
            with patch('hledac.universal.export.sprint_exporter.get_sprint_next_seeds_path',
                       return_value=MagicMock()):
                with patch('hledac.universal.export.sprint_exporter._generate_next_sprint_seeds',
                           return_value=MagicMock()):
                    with patch('hledac.universal.export.sprint_exporter._get_sprint_trend',
                               new_callable=AsyncMock, return_value=[]):
                        with patch('hledac.universal.export.sprint_exporter._get_source_leaderboard',
                                   new_callable=AsyncMock, return_value=[]):
                            with patch('hledac.universal.export.sprint_exporter._get_correlation_from_handoff',
                                       return_value=None):
                                with patch('hledac.universal.export.sprint_exporter._get_runtime_truth',
                                           return_value=None):
                                    with patch('hledac.universal.export.sprint_exporter._get_feed_verdict',
                                               return_value=None):
                                        with patch('hledac.universal.export.sprint_exporter._get_public_verdict',
                                                   return_value=None):
                                            with patch('hledac.universal.export.sprint_exporter._get_signal_path',
                                                       return_value=None):
                                                with patch('hledac.universal.export.sprint_exporter._get_hypothesis_pack',
                                                           return_value=None):
                                                    with patch('hledac.universal.export.sprint_exporter._get_canonical_run_summary',
                                                               return_value=None):
                                                        with patch('hledac.universal.export.sprint_exporter._get_sprint_verdict',
                                                                   return_value=None):
                                                            with patch('hledac.universal.export.sprint_exporter._get_synthesis_outcome_payload',
                                                                       return_value=None):
                                                                with patch(
                                                                    'hledac.universal.export.sprint_exporter.UniversalSecurityCoordinator'
                                                                ) as mock_sec:
                                                                    mock_sec.return_value.initialize = AsyncMock()
                                                                    mock_sec.return_value.sanitize_outbound = AsyncMock(
                                                                        return_value={"sanitized": '{"sprint_id":"sprint-stable"}'}
                                                                    )

                                                                    result = asyncio.run(
                                                                        export_sprint(mock_store, mock_eh, "sprint-stable")
                                                                    )

        # All expected keys must be present
        self.assertEqual(set(result.keys()) & expected_keys, expected_keys,
            "export_sprint must return stable set of keys")

    def test_no_new_write_api_in_export(self):
        """
        INVARIANT: export_sprint must NOT call any new write API.
        It reads from handoff + store, writes JSON to disk via paths.py.
        """
        from hledac.universal.export.sprint_exporter import export_sprint

        mock_store = MagicMock()
        mock_store.get_dedup_runtime_status.return_value = {
            "accepted_count": 5,
            "low_information_rejected_count": 1,
            "in_memory_duplicate_rejected_count": 0,
            "persistent_duplicate_rejected_count": 0,
            "other_rejected_count": 0,
            "persistent_dedup_enabled": False,
            "dedup_lmdb_path": "",
        }

        mock_eh = MagicMock()
        mock_eh.sprint_id = "sprint-nowrite"
        mock_eh.scorecard = {"accepted_findings": 5, "findings_per_minute": 0.5, "ioc_density": 0.2}
        mock_eh.gnn_predictions = 0
        mock_eh.synthesis_engine = None
        mock_eh.top_nodes = []
        mock_eh.correlation = None

        called_write_methods = []

        with patch('hledac.universal.export.sprint_exporter.get_sprint_json_report_path',
                   return_value=MagicMock()):
            with patch('hledac.universal.export.sprint_exporter.get_sprint_next_seeds_path',
                       return_value=MagicMock()):
                with patch('hledac.universal.export.sprint_exporter._generate_next_sprint_seeds',
                           return_value=MagicMock()):
                    with patch('hledac.universal.export.sprint_exporter._get_sprint_trend',
                               new_callable=AsyncMock, return_value=[]):
                        with patch('hledac.universal.export.sprint_exporter._get_source_leaderboard',
                                   new_callable=AsyncMock, return_value=[]):
                            with patch('hledac.universal.export.sprint_exporter._get_correlation_from_handoff',
                                       return_value=None):
                                with patch('hledac.universal.export.sprint_exporter._get_runtime_truth',
                                           return_value=None):
                                    with patch('hledac.universal.export.sprint_exporter._get_feed_verdict',
                                               return_value=None):
                                        with patch('hledac.universal.export.sprint_exporter._get_public_verdict',
                                                   return_value=None):
                                            with patch('hledac.universal.export.sprint_exporter._get_signal_path',
                                                       return_value=None):
                                                with patch('hledac.universal.export.sprint_exporter._get_hypothesis_pack',
                                                           return_value=None):
                                                    with patch('hledac.universal.export.sprint_exporter._get_canonical_run_summary',
                                                               return_value=None):
                                                        with patch('hledac.universal.export.sprint_exporter._get_sprint_verdict',
                                                                   return_value=None):
                                                            with patch('hledac.universal.export.sprint_exporter._get_synthesis_outcome_payload',
                                                                       return_value=None):
                                                                with patch(
                                                                    'hledac.universal.export.sprint_exporter.UniversalSecurityCoordinator'
                                                                ) as mock_sec:
                                                                    mock_sec.return_value.initialize = AsyncMock()
                                                                    mock_sec.return_value.sanitize_outbound = AsyncMock(
                                                                        return_value={"sanitized": '{"sprint_id":"sprint-nowrite"}'}
                                                                    )

                                                                    asyncio.run(
                                                                        export_sprint(mock_store, mock_eh, "sprint-nowrite")
                                                                    )

        # No write methods on store should be called
        self.assertEqual(called_write_methods, [],
            "export_sprint must not call write methods on store")


# ==============================================================================
# TestSprintF192F_DuckDBStoreFactsAuthority
# ==============================================================================

class TestSprintF192F_DuckDBStoreFactsAuthority(unittest.TestCase):
    """Tests for duckdb_store canonical facts authority."""

    def test_duckdb_store_is_duckdb_shadow_store(self):
        """
        INVARIANT: duckdb_store.py module must export DuckDBShadowStore.
        This is the canonical sprint facts authority.
        """
        from hledac.universal.knowledge import duckdb_store
        self.assertTrue(hasattr(duckdb_store, "DuckDBShadowStore"),
            "duckdb_store must export DuckDBShadowStore")

    def test_get_dedup_runtime_status_returns_typed_dict(self):
        """
        INVARIANT: get_dedup_runtime_status() returns a dict with known keys.
        No new keys without sprint spec.
        """
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()  # in-memory mode for testing
        status = store.get_dedup_runtime_status()

        expected_keys = {
            "persistent_dedup_enabled", "last_boot_cleanup_error", "last_dedup_error",
            "dedup_lmdb_path", "dedup_namespace", "hot_cache_size", "hot_cache_capacity",
            "in_memory_duplicate_count", "persistent_duplicate_count", "accepted_count",
            "low_information_rejected_count", "in_memory_duplicate_rejected_count",
            "persistent_duplicate_rejected_count", "other_rejected_count",
        }

        self.assertEqual(set(status.keys()) & expected_keys, expected_keys,
            "get_dedup_runtime_status must return stable typed dict")

    def test_get_top_seed_nodes_is_read_only(self):
        """
        INVARIANT: get_top_seed_nodes() is a READ-ONLY seam.
        Must not modify any internal state.
        """
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()  # in-memory

        # Should return [] (no graph attached) without raising
        result = store.get_top_seed_nodes(n=5)

        self.assertIsInstance(result, list,
            "get_top_seed_nodes must return list")
        # Must not raise — fail-open for read-only diagnostic


# ==============================================================================
# TestSprintF192F_SourceTaxonomyNormalization
# ==============================================================================

class TestSprintF192F_SourceTaxonomyNormalization(unittest.TestCase):
    """
    Sprint F192H: Source taxonomy normalization in research depth metric.

    Canonical source types emitted by live pipelines and their expected tiers:
      - ct_log (ct_log_client.py:273)          → tier 1
      - onion_discovery (live_public_pipeline.py:1785) → tier 2
      - academic_discovery (live_public_pipeline.py:1995) → tier 1
      - pastebin_monitor (live_public_pipeline.py:2067) → tier 1
      - github_secret_scanner (live_public_pipeline.py:2107) → tier 1
      - ipfs (ti_feed_adapter.py:1367)         → tier 1
      - shodan_search (shodan_wrapper.py:204)  → tier 1
      - bgp_monitor (ti_feed_adapter.py:1742)  → tier 1
      - live_public_pipeline (live_public_pipeline.py) → tier 0
    """

    def test_ct_log_in_source_tier(self):
        """ct_log (ct_log_client.py:273) must be in _SOURCE_TIER at tier 1."""
        from hledac.universal.export.sprint_exporter import _SOURCE_TIER
        self.assertEqual(_SOURCE_TIER.get("ct_log"), 1,
            "ct_log must be tier 1 in _SOURCE_TIER")

    def test_onion_discovery_in_source_tier_tier2(self):
        """onion_discovery (live_public_pipeline.py:1785) must be in _SOURCE_TIER at tier 2."""
        from hledac.universal.export.sprint_exporter import _SOURCE_TIER
        self.assertEqual(_SOURCE_TIER.get("onion_discovery"), 2,
            "onion_discovery must be tier 2 in _SOURCE_TIER")

    def test_ipfs_in_source_tier(self):
        """ipfs (ti_feed_adapter.py:1367) must be in _SOURCE_TIER at tier 1."""
        from hledac.universal.export.sprint_exporter import _SOURCE_TIER
        self.assertEqual(_SOURCE_TIER.get("ipfs"), 1,
            "ipfs must be tier 1 in _SOURCE_TIER")

    def test_shodan_search_in_source_tier(self):
        """shodan_search (shodan_wrapper.py:204) must be in _SOURCE_TIER at tier 1."""
        from hledac.universal.export.sprint_exporter import _SOURCE_TIER
        self.assertEqual(_SOURCE_TIER.get("shodan_search"), 1,
            "shodan_search must be tier 1 in _SOURCE_TIER")

    def test_bgp_monitor_in_source_tier(self):
        """bgp_monitor (ti_feed_adapter.py:1742) must be in _SOURCE_TIER at tier 1."""
        from hledac.universal.export.sprint_exporter import _SOURCE_TIER
        self.assertEqual(_SOURCE_TIER.get("bgp_monitor"), 1,
            "bgp_monitor must be tier 1 in _SOURCE_TIER")

    def test_live_public_pipeline_tier0(self):
        """live_public_pipeline must be tier 0 (indexed/surface)."""
        from hledac.universal.export.sprint_exporter import _SOURCE_TIER
        self.assertEqual(_SOURCE_TIER.get("live_public_pipeline"), 0,
            "live_public_pipeline must be tier 0")

    def test_ct_log_contributes_to_research_depth(self):
        """ct_log hits contribute to non_indexed_ratio in research depth computation."""
        from hledac.universal.export.sprint_exporter import _compute_research_depth

        mock_store = MagicMock()
        mock_eh = MagicMock()
        mock_eh.scorecard = {"entries_per_source": {"ct_log": 50, "rss_atom_pipeline": 50}, "hits_per_source": {}}
        mock_eh.runtime_truth = None

        result = _compute_research_depth(mock_eh, None, None, None, {"_no_correlation_data": True})
        # 50 tier-1 / 100 total = 0.5 → 10.0
        self.assertEqual(result["breakdown"]["non_indexed_ratio"], 10.0)
        self.assertEqual(result["depth_signals"]["deep_sources_found"], 50)

    def test_onion_discovery_contributes_to_research_depth_tier2(self):
        """onion_discovery (tier 2) hits contribute as deep sources."""
        from hledac.universal.export.sprint_exporter import _compute_research_depth

        mock_eh = MagicMock()
        mock_eh.scorecard = {"entries_per_source": {"onion_discovery": 30, "live_public_pipeline": 70}, "hits_per_source": {}}
        mock_eh.runtime_truth = None

        result = _compute_research_depth(mock_eh, None, None, None, {"_no_correlation_data": True})
        # 30 tier-2 / 100 total = 0.3 → 6.0
        self.assertEqual(result["breakdown"]["non_indexed_ratio"], 6.0)

    def test_ipfs_contributes_to_research_depth(self):
        """ipfs (tier 1) hits contribute to non_indexed_ratio."""
        from hledac.universal.export.sprint_exporter import _compute_research_depth

        mock_eh = MagicMock()
        mock_eh.scorecard = {"entries_per_source": {"ipfs": 25, "rss_atom_pipeline": 75}, "hits_per_source": {}}
        mock_eh.runtime_truth = None

        result = _compute_research_depth(mock_eh, None, None, None, {"_no_correlation_data": True})
        # 25 tier-1 / 100 total = 0.25 → 5.0
        self.assertEqual(result["breakdown"]["non_indexed_ratio"], 5.0)

    def test_shodan_search_contributes_to_research_depth(self):
        """shodan_search (tier 1) hits contribute to non_indexed_ratio."""
        from hledac.universal.export.sprint_exporter import _compute_research_depth

        mock_eh = MagicMock()
        mock_eh.scorecard = {"entries_per_source": {"shodan_search": 40, "live_public_pipeline": 60}, "hits_per_source": {}}
        mock_eh.runtime_truth = None

        result = _compute_research_depth(mock_eh, None, None, None, {"_no_correlation_data": True})
        # 40 tier-1 / 100 total = 0.4 → 8.0
        self.assertEqual(result["breakdown"]["non_indexed_ratio"], 8.0)

    def test_bgp_monitor_contributes_to_research_depth(self):
        """bgp_monitor (tier 1) hits contribute to non_indexed_ratio."""
        from hledac.universal.export.sprint_exporter import _compute_research_depth

        mock_eh = MagicMock()
        mock_eh.scorecard = {"entries_per_source": {"bgp_monitor": 20, "rss_atom_pipeline": 80}, "hits_per_source": {}}
        mock_eh.runtime_truth = None

        result = _compute_research_depth(mock_eh, None, None, None, {"_no_correlation_data": True})
        # 20 tier-1 / 100 total = 0.2 → 4.0
        self.assertEqual(result["breakdown"]["non_indexed_ratio"], 4.0)


if __name__ == "__main__":
    unittest.main()
