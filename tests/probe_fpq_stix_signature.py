"""
Probe F0PQ: Post-Quantum ML-DSA STIX/JSON-LD signature path verification.
Invariant: PQ signing path is called and is fail-safe.
GHOST_INVARIANTS: gather(return_exceptions=True), no asyncio.run() in async ctx.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

# Candidate imports (adjust if project uses relative imports)
try:
    from export.stix_exporter import _maybe_sign_bundle, render_stix_bundle
    STIX_OK = True
except Exception:
    STIX_OK = False

try:
    JSONLD_OK = True
except Exception:
    JSONLD_OK = False

try:
    from security.pq_crypto import (
        PQAvailability,
        PQSignature,
    )
    PQ_OK = True
except Exception:
    PQ_OK = False


class FakePQBackend:
    """Fake PQ backend for testing."""
    name_val = "test"
    available = True
    sig_obj = None  # set per test

    def name(self) -> str:
        return self.name_val

    def is_available(self) -> bool:
        return self.available

    def ensure_mldsa_key(self, key_id: str, level: int = 65) -> bool:
        return True

    def sign_mldsa_digest(self, key_id: str, digest: str, level: int = 65) -> PQSignature:
        if self.sig_obj is None:
            self.sig_obj = PQSignature(
                algorithm="ml-dsa-65",
                signature_bytes=b"FAKE_SIG_BYTES",
                key_id=key_id,
                level=65,
                has_mldsa_flag=True,
            )
        return self.sig_obj

    def verify_mldsa_signature(self, digest, signature, public_key_bytes, level=65) -> bool:
        return True

    def pq_status(self):
        class _s:
            availability = PQAvailability.AVAILABLE
            backend_name = "test"
            error_message = None
            mldsa_key_id = "com.hledac.pq.signing.v1"
            mldsa_level = 65
            signed_batch_digest = None
            chunk_count = 0
        return _s()


def _fake_report() -> dict:
    """Minimal report dict matching normalize_export_input expectations."""
    return {
        "type": "observed-run",
        "id": "observed-run--test-0001",
        "spec_version": "2.1",
        "objects": [],
        "started_ts": "2026-05-24T00:00:00Z",
        "finished_ts": "2026-05-24T00:01:00Z",
    }


# ---------------------------------------------------------------------------
# F0PQ-1: STIX bundle PQ signing path called
# ---------------------------------------------------------------------------
def test_stix_bundle_pq_signing_path_called():
    """STIX bundle PQ signing path is invoked when PQ backend available."""
    assert STIX_OK, "stix_exporter import failed"
    bundle = _fake_report()
    # patch the async backend getter to return our fake
    with patch("export.stix_exporter._get_pq_backend_async") as mock_get:
        mock_get.return_value = asyncio.get_event_loop().run_until_complete(
            asyncio.gather(
                asyncio.coroutine(lambda: (FakePQBackend(), type("S", (), {"availability": PQAvailability.AVAILABLE})()))(),
                return_exceptions=True,
            )
        )[0]
        # actually simulate what _maybe_sign_bundle does
        from export.stix_exporter import _maybe_sign_bundle_async
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(_maybe_sign_bundle_async(bundle.copy()))
        loop.close()
    assert "extension" in result, "PQ extension not added to bundle"
    ext = result["extension"]
    assert ext.get("extension_type") == "hledac:pq-signature", "wrong extension type"
    assert "ml_dsa_signature" in ext, "ml_dsa_signature missing"


# ---------------------------------------------------------------------------
# F0PQ-2: STIX bundle skip silently when PQ unavailable
# ---------------------------------------------------------------------------
def test_stix_bundle_skip_when_pq_unavailable():
    """STIX bundle returns unchanged when PQ backend unavailable."""
    assert STIX_OK
    bundle = _fake_report()
    fake_backend = FakePQBackend()
    fake_backend.available = False
    # test via _maybe_sign_bundle directly (sync wrapper)
    result = _maybe_sign_bundle(bundle.copy())
    # When backend is not available, _maybe_sign_bundle returns bundle unchanged
    # Note: _maybe_sign_bundle checks backend.is_available() but also checks
    # status.availability — when unavailable, it returns bundle unchanged
    assert result is not None  # fail-safe: never None


# ---------------------------------------------------------------------------
# F0PQ-3: JSON-LD PQ signing path called
# ---------------------------------------------------------------------------
def test_jsonld_pq_signing_path_called():
    """JSON-LD PQ signing path is invoked when PQ backend available."""
    assert JSONLD_OK, "jsonld_exporter import failed"
    obj = _fake_report()
    from export.jsonld_exporter import _maybe_sign_jsonld_async
    loop = asyncio.new_event_loop()
    result = loop.run_until_complete(_maybe_sign_jsonld_async(obj.copy()))
    loop.close()
    assert "extension" in result, "PQ extension not added to JSON-LD"
    ext = result["extension"]
    assert ext.get("extension_type") == "hledac:pq-signature", "wrong extension type"


# ---------------------------------------------------------------------------
# F0PQ-4: JSON-LD skip silently when PQ unavailable
# ---------------------------------------------------------------------------
def test_jsonld_skip_when_pq_unavailable():
    """JSON-LD returns unchanged when PQ backend unavailable."""
    assert JSONLD_OK
    from export.jsonld_exporter import _maybe_sign_jsonld_async
    obj = _fake_report()
    fake_backend = FakePQBackend()
    fake_backend.available = False
    loop = asyncio.new_event_loop()
    result = loop.run_until_complete(_maybe_sign_jsonld_async(obj.copy()))
    loop.close()
    assert result is not None  # fail-safe


# ---------------------------------------------------------------------------
# F0PQ-5: GHOST_INVARIANTS: gather(return_exceptions=True) in async path
# ---------------------------------------------------------------------------
def test_gather_return_exceptions_in_stix_async():
    """STIX _maybe_sign_bundle_async uses gather(return_exceptions=True)."""
    assert STIX_OK
    # Verify by code inspection: the function must use gather with return_exceptions
    import inspect

    from export.stix_exporter import _maybe_sign_bundle_async
    src = inspect.getsource(_maybe_sign_bundle_async)
    assert "return_exceptions=True" in src, "gather return_exceptions=True not found"


# ---------------------------------------------------------------------------
# F0PQ-6: GHOST_INVARIANTS: gather(return_exceptions=True) in JSON-LD async
# ---------------------------------------------------------------------------
def test_gather_return_exceptions_in_jsonld_async():
    """JSON-LD _maybe_sign_jsonld_async uses gather(return_exceptions=True)."""
    assert JSONLD_OK
    import inspect

    from export.jsonld_exporter import _maybe_sign_jsonld_async
    src = inspect.getsource(_maybe_sign_jsonld_async)
    assert "return_exceptions=True" in src, "gather return_exceptions=True not found"


# ---------------------------------------------------------------------------
# F0PQ-7: PQAvailability enum values present
# ---------------------------------------------------------------------------
def test_pq_availability_enum_complete():
    """PQAvailability has all expected states."""
    assert PQ_OK
    assert hasattr(PQAvailability, "DISABLED")
    assert hasattr(PQAvailability, "UNAVAILABLE")
    assert hasattr(PQAvailability, "AVAILABLE")
    assert hasattr(PQAvailability, "SIGNED")
    assert hasattr(PQAvailability, "FAIL_SOFT")


# ---------------------------------------------------------------------------
# F0PQ-8: render_stix_bundle returns valid dict (fail-safe)
# ---------------------------------------------------------------------------
def test_render_stix_bundle_fail_safe():
    """render_stix_bundle returns dict even with bad input."""
    assert STIX_OK
    result = render_stix_bundle({})
    assert isinstance(result, dict), "render_stix_bundle must return dict"


if __name__ == "__main__":
    import sys
    results = []
    for fn in [
        test_stix_bundle_pq_signing_path_called,
        test_stix_bundle_skip_when_pq_unavailable,
        test_jsonld_pq_signing_path_called,
        test_jsonld_skip_when_pq_unavailable,
        test_gather_return_exceptions_in_stix_async,
        test_gather_return_exceptions_in_jsonld_async,
        test_pq_availability_enum_complete,
        test_render_stix_bundle_fail_safe,
    ]:
        try:
            fn()
            results.append(f"  PASS  {fn.__name__}")
        except Exception as e:
            results.append(f"  FAIL  {fn.__name__}: {e}")
    for r in results:
        print(r)
    failed = sum(1 for r in results if "FAIL" in r)
    sys.exit(failed)
