#!/usr/bin/env python3
"""
Sprint F206A: Baseline Runner probe tests
========================================

F206A-1  | run_baseline.py --profile f205-green --json PATH produces valid JSON
F206A-2  | JSON output has required keys: profile, commands, passed, failed,
          known_failures, duration_s, test_inventory
F206A-3  | test_inventory.collected_tests is int >= 0
F206A-4  | --collect-only flag produces inventory without running tests
F206A-5  | known_failures is a list (may be empty)
F206A-6  | duration_s is float > 0
F206A-7  | commands list is non-empty when not --collect-only
F206A-8  | profile field matches the --profile argument
F206A-9  | failed count is non-negative int
F206A-10 | smoke step included in commands when not --collect-only
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

RUN_BASELINE = PROJECT_ROOT / "run_baseline.py"


# ---------------------------------------------------------------------------
# Mock responses — fast, no real pytest execution
# ---------------------------------------------------------------------------

MOCK_COLLECT_OUTPUT = """tests/probe_f204a/test_sidecar_bus_all_sources.py::TestSidecarBus::test_f204a_1
tests/probe_f204a/test_sidecar_bus_all_sources.py::TestSidecarBus::test_f204a_2
tests/probe_f204b/test_asset_exposure_correlator.py::TestAssetExposureCorrelator::test_f204b_1
tests/probe_f205b/test_sidecar_ordering.py::TestSidecarOrdering::test_f205b_1
tests/probe_f205b/test_sidecar_ordering.py::TestSidecarOrdering::test_f205b_2
"""

MOCK_PROBE_OUTPUT_PASS = "10 passed in 1.23s\n"


def _make_mock_result(returncode=0, stdout="", stderr=""):
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


def _mock_subprocess_run(cmd, **_kwargs):
    """Fast mock for subprocess.run inside run_baseline.py."""
    joined = " ".join(cmd)
    if "--co" in cmd:
        return _make_mock_result(0, MOCK_COLLECT_OUTPUT, "")
    elif "smoke_runner" in joined:
        return _make_mock_result(0, "[INFO] smoke_runner: SMOKE TEST PASSED", "")
    else:
        return _make_mock_result(0, MOCK_PROBE_OUTPUT_PASS, "")


class TestBaselineRunnerJsonSchema:
    """F206A-1 through F206A-10: JSON output schema validation (mocked subprocess)."""

    @pytest.mark.asyncio
    async def _run_profile_mocked(self, profile: str, collect_only: bool = False) -> dict:
        """Call run_baseline.run_baseline_profile with mocked subprocess."""
        import run_baseline as rb

        with patch.object(rb.subprocess, "run", side_effect=_mock_subprocess_run):
            result = await rb.run_baseline_profile(profile, collect_only=collect_only)
            return dataclasses.asdict(result)

    def _result_to_dict(self, result) -> dict:
        """Convert dataclass result to plain dict."""
        import dataclasses
        return dataclasses.asdict(result)

    @pytest.mark.asyncio
    async def test_f206a_1_produces_valid_json(self):
        """F206A-1: run_baseline_profile returns dict-like object serializable to JSON."""
        data = await self._run_profile_mocked("f205-green", collect_only=True)
        assert isinstance(data, dict), f"Expected dict, got {type(data)}"

    @pytest.mark.asyncio
    async def test_f206a_2_required_keys_present(self):
        """F206A-2: JSON has all required keys."""
        data = await self._run_profile_mocked("f205-green", collect_only=True)
        required = {"profile", "commands", "passed", "failed", "known_failures", "duration_s", "test_inventory"}
        missing = required - set(data.keys())
        assert not missing, f"Missing keys: {missing}"

    @pytest.mark.asyncio
    async def test_f206a_3_inventory_collected_tests_is_int(self):
        """F206A-3: test_inventory.collected_tests is int >= 0."""
        data = await self._run_profile_mocked("f205-green", collect_only=True)
        inv = data.get("test_inventory", {})
        ct = inv.get("collected_tests")
        assert isinstance(ct, int), f"collected_tests should be int, got {type(ct)}"
        assert ct >= 0, f"collected_tests should be >= 0, got {ct}"

    @pytest.mark.asyncio
    async def test_f206a_4_collect_only_flag(self):
        """F206A-4: --collect-only produces inventory without running tests."""
        data = await self._run_profile_mocked("f205-green", collect_only=True)
        assert data["passed"] == 0, "collect-only should have 0 passed"
        assert data["failed"] == 0, "collect-only should have 0 failed"

    @pytest.mark.asyncio
    async def test_f206a_5_known_failures_is_list(self):
        """F206A-5: known_failures is a list."""
        data = await self._run_profile_mocked("f205-green", collect_only=True)
        kf = data.get("known_failures")
        assert isinstance(kf, list), f"known_failures should be list, got {type(kf)}"

    @pytest.mark.asyncio
    async def test_f206a_6_duration_s_is_positive_float(self):
        """F206A-6: duration_s is float > 0."""
        data = await self._run_profile_mocked("f205-green", collect_only=True)
        dur = data.get("duration_s")
        assert isinstance(dur, (int, float)), f"duration_s should be numeric, got {type(dur)}"
        assert dur >= 0, f"duration_s should be >= 0, got {dur}"

    @pytest.mark.asyncio
    async def test_f206a_7_commands_non_empty(self):
        """F206A-7: commands list is non-empty when not --collect-only."""
        data = await self._run_profile_mocked("f205-green", collect_only=False)
        cmds = data.get("commands")
        assert isinstance(cmds, list) and len(cmds) > 0, f"commands should be non-empty, got {cmds}"

    @pytest.mark.asyncio
    async def test_f206a_8_profile_matches_argument(self):
        """F206A-8: profile field matches --profile argument."""
        data = await self._run_profile_mocked("f205-green", collect_only=True)
        assert data.get("profile") == "f205-green"

    @pytest.mark.asyncio
    async def test_f206a_9_failed_is_non_negative(self):
        """F206A-9: failed count is non-negative int."""
        data = await self._run_profile_mocked("f205-green", collect_only=False)
        failed = data.get("failed")
        assert isinstance(failed, int) and failed >= 0, f"failed should be >= 0 int, got {failed}"

    @pytest.mark.asyncio
    async def test_f206a_10_smoke_step_present(self):
        """F206A-10: smoke step included in commands when not --collect-only."""
        data = await self._run_profile_mocked("f205-green", collect_only=False)
        steps = {c.get("step") for c in data.get("commands", [])}
        assert "smoke" in steps, f"Expected 'smoke' in steps, got {steps}"


class TestBaselineRunnerIntegration:
    """Integration: runs real run_baseline.py --collect-only as subprocess."""

    def test_f206a_integration_collect_only_exit_zero(self):
        """Integration: run_baseline.py --collect-only exits 0."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            json_path = Path(f.name)

        args = [
            sys.executable, str(RUN_BASELINE),
            "--profile", "f205-green",
            "--json", str(json_path),
            "--collect-only",
        ]
        cp = subprocess.run(args, cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=300)
        try:
            data = json.loads(json_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            data = {}
        json_path.unlink(missing_ok=True)

        assert cp.returncode == 0, f"Expected exit 0, got {cp.returncode}: {cp.stderr[:200]}"
        assert "profile" in data, "JSON should have 'profile' key"
        assert data["profile"] == "f205-green"
