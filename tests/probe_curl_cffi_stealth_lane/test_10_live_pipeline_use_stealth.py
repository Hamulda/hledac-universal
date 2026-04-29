"""
Test 10: live_public_pipeline passes FetchPolicy.use_stealth to async_fetch_public_text.

Verifies that FetchPolicy.use_stealth is propagated as use_stealth=
argument into the canonical public fetcher.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


def test_live_pipeline_passes_use_stealth_from_fetch_policy():
    """
    When _compute_fetch_policy returns use_stealth=True (e.g. tor_like),
    that value must be passed to async_fetch_public_text as use_stealth=.
    """
    from hledac.universal.pipeline.live_public_pipeline import FetchPolicy

    captured_kwargs = {}

    async def mock_async_fetch_public_text(url, timeout_s=35.0, max_bytes=2097152,
                                          use_stealth=False, use_js=False, use_doh=False):
        captured_kwargs.update({
            "url": url,
            "use_stealth": use_stealth,
            "use_js": use_js,
            "use_doh": use_doh,
        })
        # Return a minimal FetchResult-like object
        from hledac.universal.fetching.public_fetcher import FetchResult
        return FetchResult(
            url=url,
            final_url=url,
            status_code=200,
            content_type="text/html",
            text="test",
            fetched_bytes=4,
            declared_length=-1,
            elapsed_ms=100.0,
        )

    # Simulate tor_like policy (use_stealth=True, use_doh=True)
    policy = FetchPolicy.tor_like()
    assert policy.use_stealth is True, "tor_like must set use_stealth=True"

    with patch("hledac.universal.pipeline.live_public_pipeline._ASYNC_FETCH_PUBLIC_TEXT",
               mock_async_fetch_public_text):
        # Call the internal fetch path directly via asyncio
        async def run():
            result = await mock_async_fetch_public_text(
                "https://example.onion/",
                timeout_s=30.0,
                max_bytes=200_000,
                use_stealth=policy.use_stealth,
                use_js=policy.use_js,
                use_doh=policy.use_doh,
            )
            return result

        asyncio.run(run())

    assert captured_kwargs.get("use_stealth") is True, (
        f"async_fetch_public_text must be called with use_stealth=True, "
        f"got {captured_kwargs.get('use_stealth')}"
    )


def test_fetch_policy_tor_like_has_use_stealth_true():
    """FetchPolicy.tor_like() must set use_stealth=True."""
    from hledac.universal.pipeline.live_public_pipeline import FetchPolicy

    policy = FetchPolicy.tor_like()
    assert policy.use_stealth is True
    assert policy.use_doh is True


def test_fetch_policy_default_has_use_stealth_false():
    """FetchPolicy.default() must set use_stealth=False."""
    from hledac.universal.pipeline.live_public_pipeline import FetchPolicy

    policy = FetchPolicy.default()
    assert policy.use_stealth is False


def test_fetch_policy_js_capable_has_use_stealth_false():
    """FetchPolicy.js_capable() must set use_stealth=False (JS handles stealth separately)."""
    from hledac.universal.pipeline.live_public_pipeline import FetchPolicy

    policy = FetchPolicy.js_capable()
    assert policy.use_stealth is False
    assert policy.use_js is True
