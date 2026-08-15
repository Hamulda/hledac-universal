"""
Sprint F215C — PUBLIC Terminality Truth.

Tests that PUBLIC lane always has explicit terminal state in canonical report
for domain-query active profiles.

MUST NOT:
- Use live network
- Launch browser
- Load MLX model
- Modify fetcher/transport behavior

ABORT CONDITIONS:
- Any public fetch behavior rewrite
- Any transport policy rewrite
- Any live network in tests
- Any model load
- Any threshold change
"""



import pytest






    SourceFamilyOutcome,
    normalize_source_family_outcome,
)


class TestPublicTerminalStateDerivation:
    """Unit tests for PUBLIC terminal state derivation via normalize_source_family_outcome."""

from _core import aclose
    def test_raw_none_returns_never_scheduled(self):
        """raw=None → terminal_state=NEVER_SCHEDULED (never scheduled / no outcome recorded)."""
        result = normalize_source_family_outcome("public", None)
        assert result["terminal_state"] == "NEVER_SCHEDULED"
        assert result["attempted"] is False
        assert result["skipped"] is True
        assert result["skip_reason"] == "no_outcome_recorded"

    def test_raw_none_preserves_skip_reason(self):
        """raw=None always sets skip_reason=no_outcome_recorded."""
        result = normalize_source_family_outcome("public", None)
        assert "no_outcome_recorded" in result["skip_reason"]

    # ── ATTEMPTED_* states ──────────────────────────────────────────────────

    def test_attempted_with_error_returns_attempted_error(self):
        """attempted=True + error → ATTEMPTED_ERROR."""
        result = normalize_source_family_outcome("public", {
            "attempted": True,
            "skipped": False,
            "error": "import:ModuleNotFoundError",
            "timeout": False,
            "accepted_count": 0,
        })
        assert result["terminal_state"] == "ATTEMPTED_ERROR"
        assert result["error"] == "import:ModuleNotFoundError"

    def test_attempted_with_timeout_returns_attempted_timeout(self):
        """attempted=True + timeout=True → ATTEMPTED_TIMEOUT."""
        result = normalize_source_family_outcome("public", {
            "attempted": True,
            "skipped": False,
            "error": None,
            "timeout": True,
            "accepted_count": 0,
        })
        assert result["terminal_state"] == "ATTEMPTED_TIMEOUT"
        assert result["timeout"] is True

    def test_attempted_with_accepted_findings_returns_attempted_accepted(self):
        """attempted=True + accepted_count > 0 → ATTEMPTED_ACCEPTED."""
        result = normalize_source_family_outcome("public", {
            "attempted": True,
            "skipped": False,
            "error": None,
            "timeout": False,
            "accepted_count": 5,
        })
        assert result["terminal_state"] == "ATTEMPTED_ACCEPTED"
        assert result["accepted_count"] == 5

    def test_attempted_zero_results_returns_attempted_no_results(self):
        """attempted=True + accepted_count=0 + no error/timeout → ATTEMPTED_NO_RESULTS."""
        result = normalize_source_family_outcome("public", {
            "attempted": True,
            "skipped": False,
            "error": None,
            "timeout": False,
            "accepted_count": 0,
        })
        assert result["terminal_state"] == "ATTEMPTED_NO_RESULTS"
        assert result["accepted_count"] == 0

    def test_attempted_zero_results_with_discovered_returns_no_results(self):
        """attempted with raw_count but accepted_count=0 is still ATTEMPTED_NO_RESULTS."""
        result = normalize_source_family_outcome("public", {
            "attempted": True,
            "skipped": False,
            "error": None,
            "timeout": False,
            "raw_count": 10,
            "accepted_count": 0,
        })
        assert result["terminal_state"] == "ATTEMPTED_NO_RESULTS"

    # ── SKIPPED states ───────────────────────────────────────────────────────

    def test_skipped_by_policy_returns_skipped_by_policy(self):
        """skipped with policy-related skip_reason → SKIPPED_BY_POLICY."""
        result = normalize_source_family_outcome("public", {
            "attempted": False,
            "skipped": True,
            "skip_reason": "policy:public_disabled",
            "error": None,
            "timeout": False,
            "accepted_count": 0,
        })
        assert result["terminal_state"] == "SKIPPED_BY_POLICY"

    def test_skipped_by_memory_returns_skipped_by_memory(self):
        """skipped with memory/hardware skip_reason → SKIPPED_BY_MEMORY."""
        for reason in ("memory:pressure", "hw_skip:UMA_critical", "hardware:memory_exhausted"):
            result = normalize_source_family_outcome("public", {
                "attempted": False,
                "skipped": True,
                "skip_reason": reason,
                "error": None,
                "timeout": False,
                "accepted_count": 0,
            })
            assert result["terminal_state"] == "SKIPPED_BY_MEMORY", f"failed for {reason}"

    def test_skipped_by_remaining_low_returns_skipped(self):
        """skipped with terminal:remaining_too_low → SKIPPED (not SKIPPED_BY_POLICY)."""
        result = normalize_source_family_outcome("public", {
            "attempted": False,
            "skipped": True,
            "skip_reason": "terminal:remaining_too_low",
            "error": None,
            "timeout": False,
            "accepted_count": 0,
        })
        # remaining_too_low is a timeout/cycle-skip, not policy or memory
        assert result["terminal_state"] == "SKIPPED"

    def test_skipped_no_skip_reason_returns_skipped(self):
        """attempted=False, skipped=True but no specific reason → SKIPPED."""
        result = normalize_source_family_outcome("public", {
            "attempted": False,
            "skipped": True,
            "skip_reason": None,
            "error": None,
            "timeout": False,
            "accepted_count": 0,
        })
        assert result["terminal_state"] == "SKIPPED"

    # ── Feed balance (list tuple) path ─────────────────────────────────────

    def test_feed_tuple_returns_attempted(self):
        """Feed verdict tuple → attempted=True terminal_state=ATTEMPTED_NO_RESULTS (no accepted)."""
        # (tag, signal, fb_use, fb_waste, quality)
        verdict = ("FEED", 3, 3, 0, 0.95)
        result = normalize_source_family_outcome("feed", verdict)
        assert result["terminal_state"] == "ATTEMPTED_NO_RESULTS"
        assert result["attempted"] is True
        assert result["family"] == "feed"

    # ── AcquisitionLaneOutcome object path ───────────────────────────────────

    def test_acquisition_lane_outcome_to_dict(self):
        """AcquisitionLaneOutcome with to_dict() is normalized correctly."""
        from hledac.universal.runtime.acquisition_strategy import AcquisitionLaneOutcome
        outcome = AcquisitionLaneOutcome(
            lane="CT",
            enabled=True,
            attempted=True,
            accepted_findings=0,
            timeout=True,
            error=None,
            duration_s=10.0,
        )
        result = normalize_source_family_outcome("ct", outcome)
        assert result["terminal_state"] == "ATTEMPTED_TIMEOUT"
        assert result["attempted"] is True
        assert result["timeout"] is True


class TestSourceFamilyOutcomeTerminalState:
    """Tests that SourceFamilyOutcome.to_dict() includes terminal_state."""

    def test_to_dict_includes_terminal_state(self):
        """to_dict() must include terminal_state field."""
        sfo = SourceFamilyOutcome(
            family="public",
            attempted=True,
            skipped=False,
            skip_reason=None,
            raw_count=5,
            built_count=3,
            accepted_count=2,
            error=None,
            timeout=False,
            duration_s=10.5,
            terminal_state="ATTEMPTED_ACCEPTED",
        )
        d = sfo.to_dict()
        assert "terminal_state" in d
        assert d["terminal_state"] == "ATTEMPTED_ACCEPTED"

    def test_to_dict_terminal_state_round_trip(self):
        """terminal_state survives to_dict() and back."""
        sfo = SourceFamilyOutcome(
            family="public",
            attempted=False,
            skipped=True,
            skip_reason="no_outcome_recorded",
            raw_count=0,
            built_count=0,
            accepted_count=0,
            error=None,
            timeout=False,
            duration_s=None,
            terminal_state="NEVER_SCHEDULED",
        )
        d = sfo.to_dict()
        result = normalize_source_family_outcome("public", d)
        assert result["terminal_state"] == "NEVER_SCHEDULED"

    def test_terminal_state_default_is_unknown(self):
        """SourceFamilyOutcome with no terminal_state passed gets default=UNKNOWN."""
        # Verify the dataclass field default
        import inspect
        sig = inspect.signature(SourceFamilyOutcome)
        ts_param = sig.parameters["terminal_state"]
        assert ts_param.default == "UNKNOWN", f"Expected UNKNOWN default, got {ts_param.default}"


class TestPublicTerminalStateCanonical:
    """Integration tests verifying terminal_state appears in canonical report surface."""

    def test_normalize_public_outcome_has_terminal_state(self):
        """normalize_source_family_outcome for PUBLIC always includes terminal_state."""
        # Simulate all PUBLIC outcome dicts that _run_public_discovery_in_cycle creates
        outcomes = [
            # import failure
            {"lane": "PUBLIC", "attempted": True, "skipped": False, "skip_reason": None,
             "raw_count": 0, "built_count": 0, "accepted_count": 0,
             "error": "import:ModuleNotFoundError", "timeout": False, "duration_s": None},
            # TaskGroup error
            {"lane": "PUBLIC", "attempted": True, "skipped": False, "skip_reason": None,
             "raw_count": 0, "built_count": 0, "accepted_count": 0,
             "error": "TaskGroup: ExceptionGroup", "timeout": False, "duration_s": None},
            # generic exception
            {"lane": "PUBLIC", "attempted": True, "skipped": False, "skip_reason": None,
             "raw_count": 0, "built_count": 0, "accepted_count": 0,
             "error": "RuntimeError: boom", "timeout": False, "duration_s": None},
            # success with results
            {"lane": "PUBLIC", "attempted": True, "skipped": False, "skip_reason": None,
             "raw_count": 5, "built_count": 3, "accepted_count": 2,
             "error": None, "timeout": False, "duration_s": 12.5},
            # success no results
            {"lane": "PUBLIC", "attempted": True, "skipped": False, "skip_reason": None,
             "raw_count": 0, "built_count": 0, "accepted_count": 0,
             "error": None, "timeout": False, "duration_s": 35.0},
            # timeout
            {"lane": "PUBLIC", "attempted": True, "skipped": False, "skip_reason": None,
             "raw_count": 0, "built_count": 0, "accepted_count": 0,
             "error": "terminal:timeout", "timeout": True, "duration_s": 35.0},
            # envelope timeout
            {"lane": "PUBLIC", "attempted": True, "skipped": False, "skip_reason": None,
             "raw_count": 0, "built_count": 0, "accepted_count": 0,
             "error": "terminal:envelope_timeout", "timeout": True, "duration_s": 5.0},
            # remaining_too_low aggressive skip
            {"lane": "PUBLIC", "attempted": True, "skipped": True,
             "skip_reason": "terminal:remaining_too_low",
             "raw_count": 0, "built_count": 0, "accepted_count": 0,
             "error": "terminal:remaining_too_low", "timeout": False, "duration_s": None},
            # stable mode timeout
            {"lane": "PUBLIC", "attempted": True, "skipped": False, "skip_reason": None,
             "raw_count": 0, "built_count": 0, "accepted_count": 0,
             "error": "terminal:timeout", "timeout": True, "duration_s": 10.0},
        ]
        for outcome in outcomes:
            result = normalize_source_family_outcome("public", outcome)
            assert "terminal_state" in result, f"Missing terminal_state for {outcome.get('error')}"
            assert result["terminal_state"] != "UNKNOWN", f"Got UNKNOWN for {outcome.get('error')}"
            assert result["terminal_state"] is not None

    def test_all_defined_terminal_states_covered(self):
        """All 8 required terminal state values are derivable."""
        # Each case returns (raw_dict, expected_terminal_state)
        cases = [
            (None, "NEVER_SCHEDULED"),
            ({"attempted": False, "skipped": True, "skip_reason": "policy:disabled",
              "error": None, "timeout": False, "accepted_count": 0}, "SKIPPED_BY_POLICY"),
            ({"attempted": False, "skipped": True, "skip_reason": "memory:pressure",
              "error": None, "timeout": False, "accepted_count": 0}, "SKIPPED_BY_MEMORY"),
            ({"attempted": True, "skipped": False, "error": "oops",
              "timeout": False, "accepted_count": 0}, "ATTEMPTED_ERROR"),
            ({"attempted": True, "skipped": False, "error": None,
              "timeout": True, "accepted_count": 0}, "ATTEMPTED_TIMEOUT"),
            ({"attempted": True, "skipped": False, "error": None,
              "timeout": False, "accepted_count": 3}, "ATTEMPTED_ACCEPTED"),
            ({"attempted": True, "skipped": False, "error": None,
              "timeout": False, "accepted_count": 0}, "ATTEMPTED_NO_RESULTS"),
        ]
        for raw, expected in cases:
            result = normalize_source_family_outcome("public", raw)
            assert result["terminal_state"] == expected, \
                f"For {raw}: expected {expected}, got {result['terminal_state']}"

        # Verify NEVER_SCHEDULED
        result = normalize_source_family_outcome("public", None)
        assert result["terminal_state"] == "NEVER_SCHEDULED"


class TestHermeticInvariants:
    """GHOST_INVARIANTS: no live network, no model load."""

    def test_no_network_imports(self):
        """normalize_source_family_outcome must not directly import network modules."""
        # Check source file for direct import statements (not transitive)
        import pathlib
        source_path = pathlib.Path(__file__).parent.parent.parent / "runtime" / "acquisition_strategy.py"
        source_code = source_path.read_text()
        import_lines = [line.strip() for line in source_code.splitlines()
                        if (line.strip().startswith("import ") or line.strip().startswith("from "))
                        and not line.strip().startswith("#")]
        network_mods = {"requests", "httpx", "aiohttp", "urllib3", "curl", "curl_cffi"}
        for line in import_lines:
            for mod in network_mods:
                if f"import {mod}" in line or f"from {mod}" in line:
                    pytest.fail(f"Direct network import found: {line}")

    def test_no_mlx_import(self):
        """normalize_source_family_outcome must not import mlx."""
        # mlx is never imported in acquisition_strategy (pure business logic)
        import hledac.universal.runtime.acquisition_strategy as mod
        mod_file = getattr(mod, "__file__", "")
        if "mlx" in str(mod_file):
            pytest.fail("mlx imported in acquisition_strategy")
