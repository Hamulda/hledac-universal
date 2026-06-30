"""
Phase 3: Fail-fast flag validation tests.

Covers:
- Preset sanity (MINIMAL, OSINT, RECON, RESEARCH, FULL)
- Conflict detection (HEAVY_BROWSER ↔ NODRIVER, CURL_CFFI ↔ HTTPX_H2)
- Implication rules (DSPY → LLM, GRAPH_RAG → LLM+GRAPH_ANALYSIS)
- RAM budget gates (FULL warns, MINIMAL safe)
- Preset application semantics (no overwrite, env preserved)
- CLI surface (--list-presets exits 0, conflict exits 2)
- Empty-env baseline (no errors when nothing is enabled)

All tests isolate process env via a pytest fixture that snapshots
and restores the HLEDAC_ENABLE_* namespace around each test.
"""


import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
ENTRYPOINT = PROJECT_ROOT / "__main__.py"

# Env namespace we own (avoid clobbering unrelated CI vars).
_PREFIX = "HLEDAC_"


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Snapshot HLEDAC_* env, restore after each test."""
    saved = {k: v for k, v in os.environ.items() if k.startswith(_PREFIX)}
    # Clear all HLEDAC_* in test scope.
    for k in list(os.environ):
        if k.startswith(_PREFIX):
            monkeypatch.delenv(k, raising=False)
    yield
    # monkeypatch restores automatically; explicit clear for safety.
    for k in list(os.environ):
        if k.startswith(_PREFIX) and k not in saved:
            monkeypatch.delenv(k, raising=False)


# ---------------------------------------------------------------------------
# 1-2. Preset sanity
# ---------------------------------------------------------------------------


class TestPresetSanity:
    def test_no_conflicts_in_minimal_preset(self) -> None:
        """MINIMAL preset is empty — no conflicts possible."""
        from utils.flag_presets import apply_preset
        from utils.flag_registry import validate_flag_combo

        applied = apply_preset("minimal", overwrite=True)
        assert applied == {}
        errors, _ = validate_flag_combo()
        assert errors == [], f"MINIMAL produced errors: {errors}"

    def test_no_conflicts_in_osint_preset(self) -> None:
        """OSINT preset uses public APIs only — no browser/transport conflicts."""
        from utils.flag_presets import apply_preset
        from utils.flag_registry import validate_flag_combo

        apply_preset("osint", overwrite=True)
        errors, _ = validate_flag_combo()
        assert errors == [], f"OSINT produced errors: {errors}"


# ---------------------------------------------------------------------------
# 3-4. Conflict pairs
# ---------------------------------------------------------------------------


class TestConflicts:
    def test_conflict_heavy_browser_vs_nodriver(self) -> None:
        """HEAVY_BROWSER and NODRIVER are mutually exclusive."""
        from utils.flag_registry import validate_flag_combo

        os.environ["HLEDAC_ENABLE_HEAVY_BROWSER"] = "1"
        os.environ["HLEDAC_ENABLE_NODRIVER"] = "1"
        errors, _ = validate_flag_combo()
        assert any(
            "HEAVY_BROWSER" in e and "NODRIVER" in e for e in errors
        ), f"missing conflict error in: {errors}"

    def test_conflict_curl_cffi_vs_httpx_h2(self) -> None:
        """CURL_CFFI and HTTPX_H2 are mutually exclusive HTTP backends."""
        from utils.flag_registry import validate_flag_combo

        os.environ["HLEDAC_ENABLE_CURL_CFFI"] = "1"
        os.environ["HLEDAC_ENABLE_HTTPX_H2"] = "1"
        errors, _ = validate_flag_combo()
        assert any(
            "CURL_CFFI" in e and "HTTPX_H2" in e for e in errors
        ), f"missing conflict error in: {errors}"


# ---------------------------------------------------------------------------
# 5-6. Implication rules
# ---------------------------------------------------------------------------


class TestImplications:
    def test_implication_dspy_requires_llm(self) -> None:
        """DSPY implies LLM — soft warning if LLM is disabled."""
        from utils.flag_registry import validate_flag_combo

        os.environ["HLEDAC_ENABLE_DSPY"] = "1"
        # Intentionally NOT enabling LLM.
        errors, warnings = validate_flag_combo()
        assert not any("HEAVY_BROWSER" in e for e in errors)
        assert any(
            "DSPY" in w and "LLM" in w for w in warnings
        ), f"missing DSPY→LLM implication warning: {warnings}"

    def test_implication_graph_rag_requires_llm(self) -> None:
        """GRAPH_RAG implies LLM — soft warning if LLM is disabled."""
        from utils.flag_registry import validate_flag_combo

        os.environ["HLEDAC_ENABLE_GRAPH_RAG"] = "1"
        # Intentionally NOT enabling LLM (also missing GRAPH_ANALYSIS).
        errors, warnings = validate_flag_combo()
        assert not any("FATAL" in e for e in errors)
        assert any(
            "GRAPH_RAG" in w and "LLM" in w for w in warnings
        ), f"missing GRAPH_RAG→LLM warning: {warnings}"


# ---------------------------------------------------------------------------
# 7-8. RAM budget gates
# ---------------------------------------------------------------------------


class TestRamBudget:
    def test_ram_budget_full_preset_warns(self) -> None:
        """FULL preset exceeds 5500MB soft ceiling → warning emitted."""
        from utils.flag_presets import apply_preset
        from utils.flag_registry import validate_flag_combo

        apply_preset("full", overwrite=True)
        errors, warnings = validate_flag_combo()
        # FULL is RAM-fatal (>7000MB) AND warning (>5500MB) — at minimum
        # one of them must fire.
        ram_problems = [m for m in (errors + warnings) if "RAM" in m.upper()]
        assert ram_problems, (
            f"FULL preset produced no RAM diagnostics; "
            f"errors={errors}, warnings={warnings}"
        )

    def test_ram_budget_minimal_safe(self) -> None:
        """MINIMAL preset is empty → no RAM diagnostics."""
        from utils.flag_presets import apply_preset
        from utils.flag_registry import validate_flag_combo

        apply_preset("minimal", overwrite=True)
        errors, warnings = validate_flag_combo()
        assert errors == []
        assert not any("RAM" in w.upper() for w in warnings), (
            f"MINIMAL emitted RAM warnings: {warnings}"
        )


# ---------------------------------------------------------------------------
# 9. Preset application semantics
# ---------------------------------------------------------------------------


class TestPresetLoading:
    def test_preset_loading_sets_environ(self) -> None:
        """apply_preset('osint', overwrite=True) writes all keys to env."""
        from utils.flag_presets import OSINT, apply_preset

        applied = apply_preset("osint", overwrite=True)
        # Every key the OSINT preset declares must be in os.environ.
        for flag in OSINT:
            assert os.environ.get(flag) == "1", (
                f"{flag} not set after apply_preset('osint')"
            )
        # And the returned dict echoes what was actually written.
        assert set(applied.keys()) == set(OSINT.keys())


# ---------------------------------------------------------------------------
# 10-11. CLI surface (subprocess tests against __main__.py)
# ---------------------------------------------------------------------------


class TestCli:
    def test_list_presets_exit_zero(self) -> None:
        """`--list-presets` prints the table and exits 0."""
        result = subprocess.run(
            [sys.executable, str(ENTRYPOINT), "--list-presets"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(PROJECT_ROOT),
        )
        assert result.returncode == 0, (
            f"--list-presets exited {result.returncode}; "
            f"stderr={result.stderr!r}"
        )
        # Table must mention all 5 preset names.
        for name in ("minimal", "osint", "recon", "research", "full"):
            assert name in result.stdout, (
                f"preset {name!r} missing from --list-presets output"
            )

    def test_conflict_exits_with_code_2(self) -> None:
        """Process env with HEAVY_BROWSER=1 + NODRIVER=1 → exit 2."""
        env = os.environ.copy()
        env["HLEDAC_ENABLE_HEAVY_BROWSER"] = "1"
        env["HLEDAC_ENABLE_NODRIVER"] = "1"
        # --list-presets short-circuits before validation. To exercise
        # the conflict path we run main() without it; Phase 0 validation
        # fires before _run_public_passive_once, exiting 2.
        result = subprocess.run(
            [sys.executable, str(ENTRYPOINT)],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(PROJECT_ROOT),
            env=env,
        )
        assert result.returncode == 2, (
            f"expected exit 2 on conflict, got {result.returncode}; "
            f"stderr={result.stderr!r}"
        )


# ---------------------------------------------------------------------------
# 12. Empty-env baseline
# ---------------------------------------------------------------------------


class TestEmptyEnv:
    def test_validate_empty_env_no_errors(self) -> None:
        """With no HLEDAC_* set, validation passes cleanly."""
        from utils.flag_registry import validate_flag_combo

        # Fixture already cleared HLEDAC_* — assert baseline.
        for k in list(os.environ):
            assert not k.startswith(_PREFIX), (
                f"fixture leak: {k} still set"
            )
        errors, warnings = validate_flag_combo()
        assert errors == [], f"empty env produced errors: {errors}"
        # No flags active → no RAM warnings either.
        assert not any("RAM" in w.upper() for w in warnings)
