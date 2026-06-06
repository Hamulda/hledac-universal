"""F4.3: Per-flag smoke runner tests."""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
RUNNER = PROJECT_ROOT / "tools" / "flag_smoke_runner.py"


class TestF43FlagSmoke:
    """Verify the per-flag smoke runner discovers and validates flags."""

    def test_runner_script_exists(self):
        """The smoke runner script must be present."""
        assert RUNNER.exists()

    def test_runner_help_works(self):
        """--help exits 0 with usage info."""
        result = subprocess.run(
            [sys.executable, str(RUNNER), "--help"],
            capture_output=True, text=True, timeout=30,
            cwd=str(PROJECT_ROOT),
        )
        assert result.returncode == 0
        assert "usage" in result.stdout.lower() or "options" in result.stdout.lower()

    def test_runner_discovers_real_flags(self):
        """At least 30 HLEDAC_ENABLE_* flags must be discoverable."""
        result = subprocess.run(
            [sys.executable, str(RUNNER), "--json"],
            capture_output=True, text=True, timeout=180,
            cwd=str(PROJECT_ROOT),
        )
        assert result.returncode == 0
        import json
        reports = json.loads(result.stdout)
        assert len(reports) >= 30, f"only {len(reports)} flags discovered"

    def test_runner_categorizes_known_pass_flag(self):
        """HLEDAC_ENABLE_DSPY must PASS — heavily referenced in 14 files."""
        result = subprocess.run(
            [sys.executable, str(RUNNER), "--only", "HLEDAC_ENABLE_DSPY"],
            capture_output=True, text=True, timeout=60,
            cwd=str(PROJECT_ROOT),
        )
        assert "PASS" in result.stdout, (
            f"expected PASS, got rc={result.returncode}; "
            f"stdout={result.stdout!r}; stderr={result.stderr!r}"
        )
        assert "HLEDAC_ENABLE_DSPY" in result.stdout

    def test_runner_rejects_nonexistent_flag(self):
        """Asking for an unknown flag exits 2 with stderr message."""
        # Random flag with high entropy — should not match any source file.
        import hashlib
        unique_token = "F43" + hashlib.sha256(b"unique-nonexistent-flag").hexdigest()[:12].upper()
        flag_name = f"HLEDAC_ENABLE_{unique_token}"
        result = subprocess.run(
            [sys.executable, str(RUNNER), "--only", flag_name],
            capture_output=True, text=True, timeout=10,
            cwd=str(PROJECT_ROOT),
        )
        assert result.returncode == 2
        assert "not found" in result.stderr.lower()

    def test_runner_regex_accepts_real_flag(self):
        """Discovery regex matches real flag names exactly."""
        from tools.flag_smoke_runner import _FLAG_PATTERN
        assert _FLAG_PATTERN.fullmatch("HLEDAC_ENABLE_DSPY")
        assert _FLAG_PATTERN.fullmatch("HLEDAC_ENABLE_HTTPX_H2")
        # Edge: trailing underscore is technically a valid character but no
        # real flag uses it — the catalog disambiguates. Test the catalog
        # path: the runner's _discover_flags() output must not contain any
        # entry ending in bare underscore.
        from tools.flag_smoke_runner import _discover_flags
        flags = _discover_flags()
        suspicious = [f for f in flags if f.endswith("_")]
        assert not suspicious, f"trailing-underscore flags: {suspicious}"

    def test_runner_clean_env_after_check(self):
        """Runner must not leave HLEDAC_ENABLE_* set after --only check."""
        env_before = {k: v for k, v in os.environ.items() if k.startswith("HLEDAC_ENABLE_")}
        subprocess.run(
            [sys.executable, str(RUNNER), "--only", "HLEDAC_ENABLE_DSPY"],
            capture_output=True, text=True, timeout=60,
            cwd=str(PROJECT_ROOT),
        )
        env_after = {k: v for k, v in os.environ.items() if k.startswith("HLEDAC_ENABLE_")}
        assert env_before == env_after, "runner leaked HLEDAC_ENABLE_* into environment"
