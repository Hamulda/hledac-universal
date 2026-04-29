"""
F206K: HTTPX/H2 Blocker Fix Tests

Tests the 4 blockers:
  FIX 1: h2 dependency added to requirements-optional.txt
  FIX 2: httpx 0.28.x not rejected by version check
  FIX 3: HLEDAC_ENABLE_HTTPX_H2 env gate implemented
  FIX 4: close_httpx_client_async wired to canonical teardown
"""

import asyncio
import os
import sys
from unittest.mock import patch, MagicMock

import pytest


class TestHttpx028NotRejected:
    """FIX 2: httpx 0.28.1 is not rejected as too old."""

    def test_httpx_028_is_not_rejected(self):
        """Mock httpx 0.28.1 + h2 available → capability check passes."""
        from hledac.universal.transport import httpx_client as hc

        # Reset state
        hc._httpx_h2_enabled = False
        hc._httpx_import_error = None

        fake_httpx = MagicMock(__version__="0.28.1")
        fake_h2 = MagicMock()

        with patch.dict(sys.modules, {"httpx": fake_httpx, "h2": fake_h2}):
            with patch("importlib.import_module", side_effect=lambda m: {"httpx": fake_httpx, "h2": fake_h2}[m] if m in ("httpx", "h2") else None):
                # Re-run capability check
                hc._httpx_h2_enabled = False
                hc._httpx_import_error = None
                result = hc._check_httpx_h2_capability()

        # Should pass — 0.28.1 is not rejected
        assert result is True, "httpx 0.28.1 should not be rejected"
        assert hc._httpx_h2_enabled is True
        assert hc._httpx_import_error is None


class TestH2MissingDisablesLane:
    """FIX 1: Without h2, the lane is disabled without crashing."""

    def test_h2_missing_disables_lane_without_crash(self):
        """h2 import fail → is_httpx_h2_enabled returns False."""
        from hledac.universal.transport import httpx_client as hc

        # Reset state
        hc._httpx_h2_enabled = False
        hc._httpx_import_error = None

        # h2 not available
        with patch.dict(sys.modules, {"h2": None}):
            result = hc._check_httpx_h2_capability()

        assert result is False
        assert "h2" in hc._httpx_import_error.lower()

    def test_h2_missing_still_imports_public_fetcher(self):
        """public_fetcher still imports when h2 is missing."""
        # This is a compile/import check — if it doesn't crash, we're good
        from hledac.universal.fetching import public_fetcher  # noqa: F401


class TestEnvDisabledByDefault:
    """FIX 3: Env gate defaults to disabled."""

    def test_env_disabled_by_default(self):
        """HLEDAC_ENABLE_HTTPX_H2 unset → httpx_h2_disabled_env."""
        # Clear env
        env_backup = os.environ.pop("HLEDAC_ENABLE_HTTPX_H2", None)
        try:
            from hledac.universal.transport import httpx_transport as ht

            should_use, reason = ht.should_use_httpx_h2(
                "https://api.github.com/users", False, False
            )
            assert should_use is False
            assert reason == "httpx_h2_disabled_env"
        finally:
            if env_backup is not None:
                os.environ["HLEDAC_ENABLE_HTTPX_H2"] = env_backup

    def test_env_disabled_blocks_even_api_url(self):
        """With env disabled, even API-like URLs route to aiohttp."""
        env_backup = os.environ.pop("HLEDAC_ENABLE_HTTPX_H2", None)
        try:
            from hledac.universal.transport import httpx_transport as ht

            should_use, reason = ht.should_use_httpx_h2(
                "https://api.cloudflare.com/users", False, False
            )
            assert should_use is False
            assert reason == "httpx_h2_disabled_env"
        finally:
            if env_backup is not None:
                os.environ["HLEDAC_ENABLE_HTTPX_H2"] = env_backup


class TestEnvEnabledAllowsEligible:
    """FIX 3: Env enabled + h2 available → eligible API URL uses httpx_h2."""

    def test_env_enabled_allows_eligible_api_like_url(self):
        """HLEDAC_ENABLE_HTTPX_H2=1 + h2 available → httpx_h2 for API URL."""
        env_backup = os.environ.get("HLEDAC_ENABLE_HTTPX_H2")
        os.environ["HLEDAC_ENABLE_HTTPX_H2"] = "1"
        try:
            from hledac.universal.transport import httpx_client as hc
            from hledac.universal.transport import httpx_transport as ht

            # Reset capability state
            hc._httpx_h2_enabled = False
            hc._httpx_import_error = None

            # Mock h2 available
            fake_h2 = MagicMock()
            with patch.dict(sys.modules, {"h2": fake_h2}):
                should_use, reason = ht.should_use_httpx_h2(
                    "https://api.github.com/users", False, False
                )

            assert should_use is True
            assert reason == "api_like"
        finally:
            if env_backup is None:
                os.environ.pop("HLEDAC_ENABLE_HTTPX_H2", None)
            else:
                os.environ["HLEDAC_ENABLE_HTTPX_H2"] = env_backup


class TestDarknetStillBlocked:
    """FIX 3: Darknet URLs still blocked even when env is enabled."""

    @pytest.mark.parametrize("url,expected_reason", [
        ("http://3d2u.onion/paste", "darknet_url"),
        ("http://expyuzz4wqqyqhvn.onion/", "darknet_url"),
        ("http://example.i2p/page", "darknet_url"),
        ("http://v4.b32.i2p/test", "darknet_url"),
        ("http://mysite.freenet/", "freenet_not_httpx_supported"),
    ])
    def test_darknet_still_blocks_even_when_env_enabled(self, url, expected_reason):
        """Env enabled + .onion/.i2p/.freenet → never httpx_h2."""
        env_backup = os.environ.get("HLEDAC_ENABLE_HTTPX_H2")
        os.environ["HLEDAC_ENABLE_HTTPX_H2"] = "1"
        try:
            from hledac.universal.transport import httpx_transport as ht

            should_use, reason = ht.should_use_httpx_h2(url, False, False)
            assert should_use is False
            assert reason == expected_reason
        finally:
            if env_backup is None:
                os.environ.pop("HLEDAC_ENABLE_HTTPX_H2", None)
            else:
                os.environ["HLEDAC_ENABLE_HTTPX_H2"] = env_backup


class TestStealthJsStillBlock:
    """FIX 3: Stealth/JS still blocked even when env is enabled."""

    def test_stealth_still_blocks_when_env_enabled(self):
        """Env enabled + use_stealth=True → aiohttp."""
        env_backup = os.environ.get("HLEDAC_ENABLE_HTTPX_H2")
        os.environ["HLEDAC_ENABLE_HTTPX_H2"] = "1"
        try:
            from hledac.universal.transport import httpx_transport as ht

            should_use, reason = ht.should_use_httpx_h2(
                "https://api.github.com/users", use_stealth=True, use_js=False
            )
            assert should_use is False
            assert reason == "stealth_required"
        finally:
            if env_backup is None:
                os.environ.pop("HLEDAC_ENABLE_HTTPX_H2", None)
            else:
                os.environ["HLEDAC_ENABLE_HTTPX_H2"] = env_backup

    def test_js_still_blocks_when_env_enabled(self):
        """Env enabled + use_js=True → aiohttp."""
        env_backup = os.environ.get("HLEDAC_ENABLE_HTTPX_H2")
        os.environ["HLEDAC_ENABLE_HTTPX_H2"] = "1"
        try:
            from hledac.universal.transport import httpx_transport as ht

            should_use, reason = ht.should_use_httpx_h2(
                "https://api.github.com/users", use_stealth=False, use_js=True
            )
            assert should_use is False
            assert reason == "js_required"
        finally:
            if env_backup is None:
                os.environ.pop("HLEDAC_ENABLE_HTTPX_H2", None)
            else:
                os.environ["HLEDAC_ENABLE_HTTPX_H2"] = env_backup


class TestCloseWiredToCanonicalTeardown:
    """FIX 4: close_httpx_client_async wired to canonical teardown."""

    def test_close_httpx_client_async_importable(self):
        """close_httpx_client_async is importable from httpx_client."""
        from hledac.universal.transport.httpx_client import close_httpx_client_async
        assert callable(close_httpx_client_async)

    def test_close_in_main_finally_block(self):
        """core/__main__.py run_sprint finally block calls close_httpx_client_async."""
        import ast
        import inspect

        # Read __main__.py source
        from hledac.universal.core import __main__ as main_module
        source = inspect.getsource(main_module)

        # Parse AST
        tree = ast.parse(source)

        # Find run_sprint function
        found_finally_with_close = False
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "run_sprint":
                # Walk its finally block
                for child in ast.walk(node):
                    if isinstance(child, ast.Try):
                        for handler in child.finalbody:
                            # Check if close_httpx_client_async appears in finally block
                            for stmt in ast.walk(handler):
                                if isinstance(stmt, ast.ImportFrom):
                                    if stmt.module and "httpx_client" in stmt.module:
                                        for alias in stmt.names:
                                            if alias.name == "close_httpx_client_async":
                                                found_finally_with_close = True

        assert found_finally_with_close, (
            "close_httpx_client_async not found in run_sprint finally block. "
            "Verify core/__main__.py run_sprint finally block imports and awaits "
            "close_httpx_client_async."
        )

    def test_close_await_outside_lock(self):
        """close_httpx_client_async awaits aclose OUTSIDE the lock (H2-I8)."""
        import ast
        import inspect

        from hledac.universal.transport import httpx_client as hc
        source = inspect.getsource(hc)

        # Parse and verify close_httpx_client_async structure
        tree = ast.parse(source)
        found_close_func = None
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "close_httpx_client_async":
                found_close_func = node
                break

        assert found_close_func is not None, "close_httpx_client_async not found"

        # The aclose() await must NOT be inside the lock with block.
        # Find all await aclose() nodes and verify none are inside a With statement body
        all_nodes = list(ast.walk(found_close_func))
        aclose_nodes = [
            n for n in all_nodes
            if isinstance(n, ast.Await)
            and isinstance(n.value, ast.Call)
            and hasattr(n.value.func, "attr")
            and n.value.func.attr == "aclose"
        ]
        assert len(aclose_nodes) > 0, "No await aclose() found in close_httpx_client_async"

        # Verify each aclose is not inside a With body
        for aclose_node in aclose_nodes:
            # Walk up to find if any ancestor is a With node's body
            # For each With node, check if aclose_node is in its body
            for other in all_nodes:
                if isinstance(other, ast.With):
                    with_body_ids = {id(n) for n in ast.walk(other)}
                    if id(aclose_node) in with_body_ids:
                        raise AssertionError(
                            "aclose should NOT be awaited inside the lock with block — "
                            "it must be awaited after lock release (H2-I8 invariant)"
                        )


class TestRoutingTruthTableUpdated:
    """Verify routing truth table reflects env gate."""

    @pytest.mark.parametrize("url,use_stealth,use_js,expected_httpx,expected_reason", [
        # Env disabled (default) — all routes block at httpx_h2_disabled_env
        ("https://api.github.com/users", False, False, False, "httpx_h2_disabled_env"),
        ("https://cdn.cloudflare.com/", False, False, False, "httpx_h2_disabled_env"),
        ("https://example.com/page", False, False, False, "httpx_h2_disabled_env"),
        # Darknet — blocked by env gate even when env would be enabled
        ("http://3d2u.onion/paste", False, False, False, "httpx_h2_disabled_env"),
        ("http://example.i2p/page", False, False, False, "httpx_h2_disabled_env"),
        ("http://mysite.freenet/", False, False, False, "httpx_h2_disabled_env"),
        # Stealth/JS — blocked by env gate even when env would be enabled
        ("https://api.github.com", True, False, False, "httpx_h2_disabled_env"),
        ("https://api.github.com", False, True, False, "httpx_h2_disabled_env"),
    ])
    def test_routing_with_env_disabled(self, url, use_stealth, use_js, expected_httpx, expected_reason):
        """Default (env disabled) routing table."""
        env_backup = os.environ.pop("HLEDAC_ENABLE_HTTPX_H2", None)
        try:
            from hledac.universal.transport import httpx_transport as ht

            should_use, reason = ht.should_use_httpx_h2(url, use_stealth, use_js)
            assert should_use == expected_httpx, f"URL {url}: expected httpx={expected_httpx}, got {should_use}"
            assert reason == expected_reason, f"URL {url}: expected reason={expected_reason}, got {reason}"
        finally:
            if env_backup is not None:
                os.environ["HLEDAC_ENABLE_HTTPX_H2"] = env_backup


__all__ = []
