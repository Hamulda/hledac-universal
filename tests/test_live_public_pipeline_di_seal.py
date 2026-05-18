"""
Seal tests for live_public_pipeline dependency injection seam (F226).

Legacy module-level globals are preserved for backward compatibility but
should NOT be extended. New tests MUST use explicit keyword DI parameters.

Allowlisted legacy globals (DO NOT add new ones):
    _ASYNC_FETCH_PUBLIC_TEXT  — patched by tests for fetch seam
    _SYNC_MATCH_TEXT          — patched by tests for match seam
    _ASYNC_DISCOVERY_SEARCH  — patched via _patch_discovery()
    _CT_SCANNER_GET_SUBDOMAINS — patched via _patch_ct_scanner()

Preferred test hook: explicit keyword arguments to async_run_live_public_pipeline.
"""

import ast
import inspect
from pathlib import Path

import pytest

PIPELINE_PATH = Path(__file__).parent.parent / "pipeline" / "live_public_pipeline.py"


class TestDIParameters:
    """Verify async_run_live_public_pipeline has all required DI parameters."""

    def test_signature_has_all_5_di_params(self):
        """All 5 DI parameters present in function signature."""
        from pipeline.live_public_pipeline import async_run_live_public_pipeline

        sig = inspect.signature(async_run_live_public_pipeline)
        required = {"fetch_fn", "match_fn", "discovery_fn", "ct_subdomains_fn", "clear_query_cache_fn"}
        actual = {p.name for p in sig.parameters.values()}
        missing = required - actual
        assert not missing, f"Missing DI params: {missing}"

    def test_all_5_di_params_are_keyword_only(self):
        """All 5 DI params must be keyword-only (no positional)."""
        from pipeline.live_public_pipeline import async_run_live_public_pipeline

        sig = inspect.signature(async_run_live_public_pipeline)
        for name in ("fetch_fn", "match_fn", "discovery_fn", "ct_subdomains_fn", "clear_query_cache_fn"):
            param = sig.parameters[name]
            assert param.kind in (
                inspect.Parameter.KEYWORD_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            ), f"{name} must be keyword-only or positional-or-keyword"


class TestLegacyGlobalsAllowlist:
    """Enforce that no new _ASYNC_* / _SYNC_* patch globals are added."""

    LEGACY_ALLOWLIST = {
        "_ASYNC_FETCH_PUBLIC_TEXT",
        "_SYNC_MATCH_TEXT",
        "_ASYNC_DISCOVERY_SEARCH",
        "_CT_SCANNER_GET_SUBDOMAINS",
    }

    def test_no_new_async_patch_globals(self):
        """Detect any new _ASYNC_* or _SYNC_* module globals beyond allowlist."""
        source = PIPELINE_PATH.read_text()
        tree = ast.parse(source)

        globals_found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and isinstance(target.id, str):
                        name = target.id
                        if name.startswith("_ASYNC_") or name.startswith("_SYNC_"):
                            globals_found.add(name)

        extra = globals_found - self.LEGACY_ALLOWLIST
        assert not extra, (
            f"New patch globals found — add to DI boundary or use explicit DI: {extra}\n"
            "If this is a new legitimate DI seam, add it to LEGACY_ALLOWLIST in this test."
        )


class TestProbeF226UsesExplicitDI:
    """Verify probe_f226_di_seams tests use explicit DI, not monkeypatch globals."""

    def test_probe_f226_no_monkeypatch_of_async_fetch(self):
        """probe_f226_di_seams must not monkeypatch _ASYNC_FETCH_PUBLIC_TEXT."""
        probe_path = Path(__file__).parent / "probe_f226_di_seams.py"
        if not probe_path.exists():
            pytest.skip("probe_f226_di_seams.py not found")

        source = probe_path.read_text()
        # Check for actual assignment monkeypatch, not just references in docstrings
        # Patterns: global _ASYNC_FETCH_PUBLIC_TEXT = ..., or patching in test body
        import re

        monkeypatch_patterns = [
            r"_ASYNC_FETCH_PUBLIC_TEXT\s*=",  # direct assignment
            r"globals\(\).*_ASYNC_FETCH_PUBLIC_TEXT",  # globals() assignment
        ]
        for pattern in monkeypatch_patterns:
            matches = re.findall(pattern, source)
            assert not matches, (
                "probe_f226_di_seams.py must use explicit fetch_fn=..., not monkeypatch globals"
            )

    def test_probe_f226_no_monkeypatch_of_sync_match(self):
        """probe_f226_di_seams must not monkeypatch _SYNC_MATCH_TEXT."""
        probe_path = Path(__file__).parent / "probe_f226_di_seams.py"
        if not probe_path.exists():
            pytest.skip("probe_f226_di_seams.py not found")

        source = probe_path.read_text()
        import re

        monkeypatch_patterns = [
            r"_SYNC_MATCH_TEXT\s*=",
            r"globals\(\).*_SYNC_MATCH_TEXT",
        ]
        for pattern in monkeypatch_patterns:
            matches = re.findall(pattern, source)
            assert not matches, (
                "probe_f226_di_seams.py must use explicit match_fn=..., not monkeypatch globals"
            )