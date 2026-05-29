"""
Sprint F195C — test_stealth_session
Stealth layer canonical wiring tests.
"""

import pytest


class TestStealthSessionUA:
    """UA rotation is testable."""

    def test_get_random_ua_returns_from_pool(self):
        """get_random_ua() returns a UA string from the pool."""
        from hledac.universal.stealth.stealth_session import StealthSession

        session = StealthSession()
        ua = session.get_random_ua()
        assert isinstance(ua, str)
        assert ua in session._ua_pool

    def test_ua_pool_size(self):
        """ua_count reflects actual pool size."""
        from hledac.universal.stealth.stealth_session import StealthSession

        session = StealthSession()
        assert session.ua_count == 5

    def test_rotate_ua_round_robin(self):
        """rotate_ua() cycles through pool in order."""
        from hledac.universal.stealth.stealth_session import StealthSession

        session = StealthSession()
        seen = []
        for _ in range(session.ua_count + 1):
            seen.append(session.rotate_ua())
        # Should wrap around
        assert seen[0] == seen[-1]
        assert len(set(seen[: session.ua_count])) == session.ua_count

    def test_get_current_ua_peeks_without_rotating(self):
        """get_current_ua() does not advance the index."""
        from hledac.universal.stealth.stealth_session import StealthSession

        session = StealthSession()
        first = session.get_current_ua()
        second = session.get_current_ua()
        assert first == second
        assert session.rotate_ua() == first

    def test_custom_ua_pool(self):
        """Custom UA pool is respected."""
        from hledac.universal.stealth.stealth_session import StealthSession

        custom_pool = ("ua1", "ua2")
        session = StealthSession(ua_pool=custom_pool)
        assert session.ua_count == 2
        assert session.get_random_ua() in custom_pool


class TestStealthSessionJitter:
    """Request timing has measurable variance."""

    @pytest.mark.asyncio
    async def test_apply_jitter_returns_slept_duration(self):
        """apply_jitter() returns actual delay for variance verification."""
        from hledac.universal.stealth.stealth_session import StealthSession

        session = StealthSession(jitter_min=0.1, jitter_max=0.3)
        delays = []
        for _ in range(30):
            delay = await session.apply_jitter()
            delays.append(delay)
        # Statistical variance: range should span a meaningful portion of [0.1, 0.3]
        delay_range = max(delays) - min(delays)
        assert delay_range > 0.05, f"Expected variance, got range {delay_range}"

    @pytest.mark.asyncio
    async def test_jitter_respects_configured_range(self):
        """Jitter stays within configured bounds."""
        from hledac.universal.stealth.stealth_session import StealthSession

        session = StealthSession(jitter_min=0.05, jitter_max=0.1)
        min_d, max_d = session.get_jitter_range()
        assert min_d == 0.05
        assert max_d == 0.1
        for _ in range(20):
            delay = await session.apply_jitter()
            assert 0.05 <= delay <= 0.1

    def test_get_jitter_range(self):
        """get_jitter_range() returns (min, max) tuple."""
        from hledac.universal.stealth.stealth_session import StealthSession

        session = StealthSession(jitter_min=0.1, jitter_max=0.5)
        rng = session.get_jitter_range()
        assert rng == (0.1, 0.5)


class TestStealthSessionLifecycle:
    """Fetcher closes cleanly."""

    @pytest.mark.asyncio
    async def test_close_is_idempotent(self):
        """close() can be called multiple times without error."""
        from hledac.universal.stealth.stealth_session import StealthSession

        session = StealthSession()
        await session.close()
        await session.close()  # Must not raise

    @pytest.mark.asyncio
    async def test_is_closed_after_close(self):
        """is_closed reflects session state."""
        from hledac.universal.stealth.stealth_session import StealthSession

        session = StealthSession()
        assert not session.is_closed
        await session.close()
        assert session.is_closed

    def test_request_count_starts_at_zero(self):
        """request_count starts at 0."""
        from hledac.universal.stealth.stealth_session import StealthSession

        session = StealthSession()
        assert session.request_count == 0

    def test_repr_shows_state(self):
        """__repr__ includes relevant state."""
        from hledac.universal.stealth.stealth_session import StealthSession

        session = StealthSession(jitter_min=0.05, jitter_max=0.1)
        r = repr(session)
        assert "5" in r  # ua_pool_size
        assert "0.05" in r
        assert "0.1" in r
        assert "closed=False" in r


class TestStealthResponse:
    """StealthResponse DTO."""

    def test_success_2xx(self):
        """success is True for 2xx status."""
        from hledac.universal.stealth.stealth_session import StealthResponse

        resp = StealthResponse(status=200, final_url="", body_bytes=b"")
        assert resp.success is True

    def test_success_false_for_non_2xx(self):
        """success is False for non-2xx status."""
        from hledac.universal.stealth.stealth_session import StealthResponse

        for status in (404, 500, 403):
            resp = StealthResponse(status=status, final_url="", body_bytes=b"")
            assert not resp.success

    def test_truncated_default_false(self):
        """truncated defaults to False."""
        from hledac.universal.stealth.stealth_session import StealthResponse

        resp = StealthResponse(status=200, final_url="", body_bytes=b"test")
        assert resp.truncated is False

    def test_body_bytes_preserved(self):
        """body_bytes stored as-is."""
        from hledac.universal.stealth.stealth_session import StealthResponse

        data = b"\x00\x01\x02"
        resp = StealthResponse(status=200, final_url="", body_bytes=data)
        assert resp.body_bytes == data
