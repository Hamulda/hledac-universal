"""
Smoke tests for transport/http_cache.py
========================================

Validates the two operating modes of ``build_cache_transport()``:

1. test_cache_transport_builds
   - With hishel + httpx installed: returns a non-None transport object
     (either ``hishel.AsyncCacheTransport`` or — if optional deps are
     missing in this environment — the base transport).

2. test_cache_transport_fail_soft
   - With hishel temporarily masked: returns the base_transport unchanged.

All tests run with HLEDAC_HTTP_CACHE behaviour bypassed — the gate lives
in FetchCoordinator, not in build_cache_transport itself.
"""


import sys
from unittest.mock import patch

import pytest
from _core import aclose

# Ensure the universal package is importable when pytest is invoked from
# the repo root (matches the project's existing test bootstrap).
_HERE = __file__.rsplit("/", 2)[0]  # .../universal
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


@pytest.mark.asyncio
async def test_cache_transport_builds() -> None:
    """
    build_cache_transport() returns either a wrapped hishel transport
    (when hishel is installed) or the base_transport unchanged (fail-soft).
    In both cases the return value MUST be something the caller can use.
    """
    from transport.http_cache import build_cache_transport

    sentinel_base = object()  # opaque base transport stand-in
    result = await build_cache_transport(sentinel_base)

    # Fail-soft contract: when hishel/httpx are missing, the function
    # returns the sentinel unchanged. When present, it returns a hishel
    # transport. Either way the result must not be None.
    assert result is not None, "build_cache_transport must never return None"

    # If hishel is available, the wrapper must be a different object than the
    # sentinel; if not, it must be exactly the sentinel.
    import importlib.util

    hishel_present = importlib.util.find_spec("hishel") is not None
    httpx_present = importlib.util.find_spec("httpx") is not None

    if hishel_present and httpx_present:
        # hishel present → wrapped object expected (not the bare sentinel)
        assert result is not sentinel_base, (
            "hishel is installed but build_cache_transport returned the bare "
            "base — wrap did not happen"
        )
        # Heuristic: the wrapped object should expose handle_async_request
        # (the httpx AsyncBaseTransport contract hishel implements).
        assert hasattr(result, "handle_async_request") or hasattr(
            result, "_transport"
        ), "wrapped transport missing expected hishel/httpx surface"
    else:
        # hishel not installed in this env → sentinel returned unchanged
        assert result is sentinel_base, (
            "hishel not installed; build_cache_transport must pass through "
            "base_transport unchanged"
        )


@pytest.mark.asyncio
async def test_cache_transport_fail_soft() -> None:
    """
    With hishel masked as ImportError, build_cache_transport() must return
    the base_transport unchanged — no exception, no None, no partial state.
    """
    from transport.http_cache import build_cache_transport

    sentinel_base = object()

    # Mask hishel: force the import inside build_cache_transport() to fail.
    saved_hishel = sys.modules.pop("hishel", None)

    class _ImportBlocker:
        def find_module(self, name, *args):  # pragma: no cover
            del args
            if name == "hishel":
                return self
            return None

        def load_module(self, *args):  # pragma: no cover
            del args
            raise ImportError("hishel deliberately masked for fail-soft test")

    blocker = _ImportBlocker()
    sys.meta_path.insert(0, blocker)  # type: ignore[arg-type]

    try:
        with patch.dict(sys.modules, {"hishel": None}):
            result = await build_cache_transport(sentinel_base)
        assert result is sentinel_base, (
            "fail-soft contract broken: build_cache_transport did not return "
            "base_transport when hishel import failed"
        )
    finally:
        # Cleanup: remove blocker + restore hishel if it was loaded before.
        try:
            sys.meta_path.remove(blocker)  # type: ignore[arg-type]
        except ValueError:
            pass
        if saved_hishel is not None:
            sys.modules["hishel"] = saved_hishel


@pytest.mark.asyncio
async def test_cache_transport_constants_bounded() -> None:
    """
    Invariant guard: max size 256 MB, 7-day TTL, expected cacheable codes.
    Prevents silent drift of bounded constants.
    """
    from transport.http_cache import (
        CACHEABLE_STATUS_CODES,
        DEFAULT_TTL_SECONDS,
        MAX_CACHE_SIZE_BYTES,
    )

    assert MAX_CACHE_SIZE_BYTES == 256 * 1024 * 1024, "size cap must stay 256MB"
    assert DEFAULT_TTL_SECONDS == 7 * 24 * 3600, "TTL must stay 7 days"
    assert CACHEABLE_STATUS_CODES == [
        200, 203, 204, 300, 301, 404, 405, 410, 414, 501,
    ], "cacheable status codes drifted from spec"
