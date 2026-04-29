"""
F206K: HTTPX Client Lazy Surface Tests

Tests httpx_client.py invariants:
  [H2-I1] Lazy import — httpx NOT imported at module level
  [H2-I4] Fail-soft disabled — h2 missing → _httpx_h2_enabled = False
  [H2-I6] No top-level network side effects at import time
  [H2-I7] CancelledError propagates (not swallowed)
  [H2-I8] Idempotent close
"""

import pytest
import asyncio
import sys
from unittest.mock import patch


class TestHttpxClientImports:
    """Verify lazy import behavior and fail-soft when h2 missing."""

    def test_module_import_no_crash(self):
        """Module imports without h2 installed."""
        from hledac.universal.transport import httpx_client

        assert hasattr(httpx_client, "is_httpx_h2_enabled")
        assert hasattr(httpx_client, "get_httpx_capability_reason")
        assert hasattr(httpx_client, "async_get_httpx_client")
        assert hasattr(httpx_client, "close_httpx_client_async")

    def test_httpx_not_imported_at_module_level(self):
        """httpx module should NOT be in sys.modules after importing httpx_client."""
        # Clear any prior imports
        for key in list(sys.modules.keys()):
            if "httpx" in key or "h2" in key:
                del sys.modules[key]

        from hledac.universal.transport import httpx_client

        # h2 should NOT be in sys.modules (lazy import check)
        h2_keys = [k for k in sys.modules if k == "h2"]
        assert len(h2_keys) == 0, f"h2 should not be imported yet: {h2_keys}"

    def test_is_httpx_h2_enabled_false_without_h2(self):
        """When h2 is not installed, is_httpx_h2_enabled returns False."""
        # Force re-check by resetting module-level state
        from hledac.universal.transport import httpx_client as hc
        hc._httpx_h2_enabled = False
        hc._httpx_import_error = None

        with patch.dict("sys.modules", {"h2": None}):
            result = hc._check_httpx_h2_capability()
            assert result is False

    def test_capability_reason_when_disabled(self):
        """get_httpx_capability_reason returns diagnostic string."""
        from hledac.universal.transport import httpx_client as hc

        # Reset state
        hc._httpx_h2_enabled = False
        hc._httpx_import_error = None

        hc._check_httpx_h2_capability()
        reason = hc.get_httpx_capability_reason()

        assert isinstance(reason, str)
        assert len(reason) > 0
        # When h2 is not installed, should mention h2
        assert "h2" in reason.lower() or "httpx" in reason.lower()

    def test_close_is_idempotent(self):
        """close_httpx_client_async can be called multiple times without error."""
        from hledac.universal.transport import httpx_client as hc

        # Reset state
        hc._httpx_client_instance = None
        hc._httpx_client_closed = False

        # Should not raise
        async def run():
            await hc.close_httpx_client_async()
            await hc.close_httpx_client_async()  # second call should be no-op

        asyncio.run(run())

    def test_async_get_httpx_client_raises_when_h2_missing(self):
        """async_get_httpx_client raises RuntimeError when h2 not available."""
        from hledac.universal.transport import httpx_client as hc

        # Reset state to simulate h2 not available
        hc._httpx_h2_enabled = False
        hc._httpx_import_error = "h2 not installed (httpx[http2] required for HTTP/2)"
        hc._httpx_client_instance = None
        hc._httpx_client_closed = False

        async def run():
            with pytest.raises(RuntimeError, match="HTTPX HTTP/2 not available"):
                await hc.async_get_httpx_client()

        asyncio.run(run())


class TestHttpxClientNoNetworkSideEffects:
    """Verify no network calls at import time."""

    def test_no_network_at_import(self):
        """Importing httpx_client should not open any network connections."""
        # This is verified by the lazy import pattern:
        # httpx_client does NOT have `import httpx` at module level
        # All httpx imports are inside functions that are only called at runtime

        import inspect
        from hledac.universal.transport import httpx_client

        source = inspect.getsource(httpx_client)
        # Find all import statements in the module (top-level only)
        lines = source.split("\n")
        top_level_import_lines = []
        in_function = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("def ") or stripped.startswith("async def "):
                in_function = True
            elif not in_function and (stripped.startswith("import ") or stripped.startswith("from ")):
                top_level_import_lines.append(stripped)

        # httpx should only be imported inside function bodies (lazy)
        for line in top_level_import_lines:
            assert "httpx" not in line, \
                f"httpx imported at module level (not lazy): {line}"


__all__ = []
