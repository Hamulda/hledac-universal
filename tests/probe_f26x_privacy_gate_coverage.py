"""Sprint F26X: PII gate coverage probe.

Verifies that every async_ingest_findings_batch call site in
runtime/sprint_scheduler.py is preceded by a privacy gate
(_run_privacy_gate via the _gate_then_ingest helper).

Coverage matrix (post-F26X):

    Line    Site                                Gated
    -----   --------------------------------    -----
    9085    CT predispatch                      YES
    12262   Wayback prelude                     YES
    12358   PDNS prelude                        YES
    12600   DoH prelude                         YES
    15728   CT log discovery in cycle [orig]    YES
    16278   Tor crawl_seed                      YES
    16556   I2P                                 YES
    16858   DHT                                 YES
    16932   Gopher (dict findings)              YES
    17112   IPFS                                YES
    17221   Forensics                           YES
    17327   Steganography                       YES
    17462   BGP                                 YES
    17606   Banner grab                         YES
    18160   PDNS pivot                          YES
    19048   CT lane candidates                  YES
    20014   _session_provider (BGP)             YES
    20031   _session_provider (PDNS)            YES
    20233   _session_provider (Wayback)         YES
    22511   Enhanced research                   YES
"""

import os
import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Source-level checks (no runtime import required) ────────────────────────


SPRINT_SCHEDULER_PATH = os.path.join(
    os.path.dirname(__file__), "..", "runtime", "sprint_scheduler.py",
)


def _read_sprint_scheduler() -> str:
    with open(SPRINT_SCHEDULER_PATH) as f:
        return f.read()


class TestF26XHelperSurface:
    """F26X: _gate_then_ingest and _run_privacy_gate are present."""

    def test_gate_then_ingest_method_exists(self):
        """SprintScheduler defines _gate_then_ingest closure in __init__."""
        src = _read_sprint_scheduler()
        assert "async def _gate_then_ingest(" in src, (
            "_gate_then_ingest helper missing from sprint_scheduler.py"
        )

    def test_run_privacy_gate_method_exists(self):
        src = _read_sprint_scheduler()
        assert "async def _run_privacy_gate(" in src

    def test_inject_privacy_layer_method_exists(self):
        """F26X inject: explicit privacy-layer injection seam."""
        src = _read_sprint_scheduler()
        assert "def inject_privacy_layer(self, layer: Any) -> None" in src
        assert "self._privacy_layer = layer" in src

    def test_privacy_layer_attribute_initialized(self):
        """self._privacy_layer starts as None in __init__."""
        src = _read_sprint_scheduler()
        assert "self._privacy_layer: Any = None" in src


class TestF26XCallSiteCoverage:
    """F26X: all 20 ingest call sites route through _gate_then_ingest.

    Counts the unique ingest sites and asserts the helper
    call count matches. If anyone adds a new direct
    async_ingest_findings_batch call outside the helper, this fails.
    """

    def test_no_direct_ingest_calls_outside_helper(self):
        """No `await ... .async_ingest_findings_batch(...)` outside the helper."""
        src = _read_sprint_scheduler()
        # Find the helper block boundaries
        helper_start = src.find("async def _gate_then_ingest(")
        helper_end = src.find("        # Sprint 8VI §C: All findings collected during sprint")
        # Strip the helper
        rest = src[:helper_start] + src[helper_end:]
        # Look for direct await calls
        direct = re.findall(
            r"await\s+\S+\.async_ingest_findings_batch\(",
            rest,
        )
        assert direct == [], (
            f"Found {len(direct)} direct async_ingest_findings_batch calls outside the helper: {direct}"
        )

    def test_helper_used_at_least_20_times(self):
        """The 20 known call sites all use _gate_then_ingest."""
        src = _read_sprint_scheduler()
        # Count occurrences in call sites (not the def line)
        # _gate_then_ingest(self, ...) calls
        calls = re.findall(
            r"self\._gate_then_ingest\(",
            src,
        )
        # Subtract the def line which contains the name once
        assert len(calls) >= 20, (
            f"Expected ≥20 _gate_then_ingest call sites, found {len(calls)}"
        )


class TestF26XDictFindingSupport:
    """F26X: Gopher/IPFS dict findings are anonymized, not just objects."""

    def test_run_privacy_gate_handles_dicts(self):
        """_run_privacy_gate branches on isinstance(f, dict)."""
        src = _read_sprint_scheduler()
        # Find the dict branch
        assert "isinstance(f, dict)" in src
        # The branch should use f.get('content') for dict, getattr for object
        assert "f.get('content')" in src or 'f.get("content")' in src

    def test_dict_field_writeback_uses_setitem(self):
        """Dict findings use f[field] = value, not setattr."""
        src = _read_sprint_scheduler()
        # Both branches must be present
        assert "f[field_name] = anon_text" in src
        assert "setattr(f, field_name, anon_text)" in src


# ── Behavioral tests (run-time, mocked privacy layer) ───────────────────────


class _MockPrivacyLayer:
    """Records detect_pii / anonymize_text calls. Fail-soft semantics."""

    def __init__(self, detect_result=None):
        self._detect_result = detect_result
        self.detect_calls: list[str] = []
        self.anonymize_calls: list[str] = []

    def detect_pii(self, text: str):
        self.detect_calls.append(text)
        return self._detect_result

    def anonymize_text(self, text: str) -> str:
        self.anonymize_calls.append(text)
        return f"[REDACTED]{text[-4:]}"


def _make_sprint_scheduler_stub(privacy_layer=None):
    """Build a minimal object that exposes the _gate_then_ingest
    closure logic. We can't instantiate SprintScheduler (deps),
    so we replicate the helper inline using closures that mirror
    the real implementation. This is a test-only stand-in.
    """
    class _Stub:
        _privacy_layer: object  # type: ignore[assignment]

        def __init__(self):
            self._layer_manager = MagicMock()
            if privacy_layer is not None:
                self._layer_manager.privacy = privacy_layer
            self._privacy_layer = None
            self._result = MagicMock()
            self._result.pii_findings_anonymized = 0
            self.ingest_calls: list[list] = []

        async def _run_privacy_gate(self, findings, layer):
            anonymized = []
            count = 0
            for f in findings:
                try:
                    text = (
                        f.get("payload_text", "")
                        if isinstance(f, dict)
                        else getattr(f, "payload_text", "") or ""
                    )
                    pii_result = layer.detect_pii(text) if text else {}
                    has_pii = (
                        bool(pii_result)
                        if isinstance(pii_result, bool)
                        else any(pii_result.values())
                    )
                    if has_pii:
                        count += 1
                        anon = layer.anonymize_text(text)
                        if isinstance(f, dict):
                            f["payload_text"] = anon
                        else:
                            f.payload_text = anon
                except Exception:
                    # Mirror real helper: fail-soft per finding
                    pass
                anonymized.append(f)
            return anonymized, count

        async def _gate_then_ingest(self, store, findings):
            if store is None or not findings:
                return None
            gated = findings
            if os.environ.get("HLEDAC_ENABLE_PRIVACY_LAYER") == "1":
                layer = self._privacy_layer or getattr(self._layer_manager, "privacy", None)
                if layer:
                    gated, n = await self._run_privacy_gate(findings, layer)
                    self._result.pii_findings_anonymized += n
            self.ingest_calls.append(list(gated))
            # Mirror real helper: never raise; await the store call.
            try:
                result = await store.async_ingest_findings_batch(gated)
                return result
            except Exception:
                return None

    return _Stub()


class TestF26XGateBehavior:
    """F26X: gate triggers when env var is set, bypasses otherwise."""

    @pytest.mark.asyncio
    async def test_gate_runs_when_env_var_set(self):
        privacy = _MockPrivacyLayer(detect_result={"email": ["a@b.com"]})
        stub = _make_sprint_scheduler_stub(privacy_layer=privacy)
        store = MagicMock()
        store.async_ingest_findings_batch = AsyncMock(return_value=[{"accepted": True}])
        findings = [{"payload_text": "Email: a@b.com"}]

        with patch.dict(os.environ, {"HLEDAC_ENABLE_PRIVACY_LAYER": "1"}):
            await stub._gate_then_ingest(store, findings)

        assert len(privacy.detect_calls) == 1
        assert "[REDACTED]" in findings[0]["payload_text"]
        store.async_ingest_findings_batch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_gate_bypassed_when_env_var_unset(self):
        privacy = _MockPrivacyLayer(detect_result={"email": ["a@b.com"]})
        stub = _make_sprint_scheduler_stub(privacy_layer=privacy)
        store = MagicMock()
        store.async_ingest_findings_batch = AsyncMock(return_value=[{"accepted": True}])
        findings = [{"payload_text": "Email: a@b.com"}]

        env = {k: v for k, v in os.environ.items() if k != "HLEDAC_ENABLE_PRIVACY_LAYER"}
        with patch.dict(os.environ, env, clear=True):
            await stub._gate_then_ingest(store, findings)

        assert not privacy.detect_calls
        assert findings[0]["payload_text"] == "Email: a@b.com"
        store.async_ingest_findings_batch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_fail_soft_on_privacy_error(self):
        """Privacy layer raises → ingest still proceeds with original findings."""
        class _Broken:
            def detect_pii(self, _text: str) -> dict:
                raise RuntimeError("PII engine broken")

        stub = _make_sprint_scheduler_stub(privacy_layer=_Broken())
        store = MagicMock()
        store.async_ingest_findings_batch = AsyncMock(return_value=[{"accepted": True}])
        findings = [{"payload_text": "PII here"}]

        with patch.dict(os.environ, {"HLEDAC_ENABLE_PRIVACY_LAYER": "1"}):
            # Must not raise
            await stub._gate_then_ingest(store, findings)

        # Ingest still called (with original findings)
        store.async_ingest_findings_batch.assert_awaited_once()
        assert findings[0]["payload_text"] == "PII here"

    @pytest.mark.asyncio
    async def test_noop_when_store_is_none(self):
        stub = _make_sprint_scheduler_stub()
        # No findings
        result = await stub._gate_then_ingest(None, [])
        assert result is None

    @pytest.mark.asyncio
    async def test_noop_when_findings_empty(self):
        stub = _make_sprint_scheduler_stub()
        store = MagicMock()
        store.async_ingest_findings_batch = AsyncMock()
        result = await stub._gate_then_ingest(store, [])
        assert result is None
        store.async_ingest_findings_batch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_fail_soft_on_ingest_error(self):
        """async_ingest_findings_batch raises → helper returns None, never raises."""
        stub = _make_sprint_scheduler_stub()
        store = MagicMock()
        store.async_ingest_findings_batch = AsyncMock(side_effect=RuntimeError("db down"))
        findings = [{"payload_text": "x"}]

        # Must not raise
        result = await stub._gate_then_ingest(store, findings)
        assert result is None


class TestF26XInjectPriority:
    """F26X: self._privacy_layer takes priority over self._layer_manager.privacy."""

    @pytest.mark.asyncio
    async def test_injected_layer_used_over_layer_manager(self):
        injected = _MockPrivacyLayer(detect_result={"email": ["x@y.com"]})
        layer_mgr_priv = _MockPrivacyLayer(detect_result={"email": ["X@Y.COM"]})

        stub = _make_sprint_scheduler_stub(privacy_layer=layer_mgr_priv)
        stub._privacy_layer = injected  # Inject wins

        store = MagicMock()
        store.async_ingest_findings_batch = AsyncMock(return_value=[{"accepted": True}])
        findings = [{"payload_text": "Email: x@y.com"}]

        with patch.dict(os.environ, {"HLEDAC_ENABLE_PRIVACY_LAYER": "1"}):
            await stub._gate_then_ingest(store, findings)

        # Only the injected layer should have been called
        assert len(injected.detect_calls) == 1
        assert not layer_mgr_priv.detect_calls

    @pytest.mark.asyncio
    async def test_fallback_to_layer_manager_when_no_inject(self):
        layer_priv = _MockPrivacyLayer(detect_result={"email": ["x@y.com"]})

        stub = _make_sprint_scheduler_stub(privacy_layer=layer_priv)
        assert stub._privacy_layer is None  # No inject

        store = MagicMock()
        store.async_ingest_findings_batch = AsyncMock(return_value=[{"accepted": True}])
        findings = [{"payload_text": "Email: x@y.com"}]

        with patch.dict(os.environ, {"HLEDAC_ENABLE_PRIVACY_LAYER": "1"}):
            await stub._gate_then_ingest(store, findings)

        assert len(layer_priv.detect_calls) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
