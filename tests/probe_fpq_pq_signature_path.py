"""
Probe FPQ: Post-Quantum ML-DSA STIX/JSON-LD signature path verification.

GHOST_INVARIANTS:
  - gather(return_exceptions=True) in all async paths
  - No asyncio.run() in async ctx
  - Fail-safe: skip silently when PQ unavailable

Invariant table:
  F0PQ-1  | PQ signing path called for STIX when backend available
  F0PQ-2  | STIX bundle returns unchanged when PQ unavailable (fail-safe)
  F0PQ-3  | PQ signing path called for JSON-LD when backend available
  F0PQ-4  | JSON-LD returns unchanged when PQ unavailable (fail-safe)
  F0PQ-5  | STIX async path uses gather(return_exceptions=True)
  F0PQ-6  | JSON-LD async path uses gather(return_exceptions=True)
  F0PQ-7  | PQAvailability enum has all expected states
  F0PQ-8  | render_stix_bundle is fail-safe (never raises)
  F0PQ-9  | _build_pq_extension fail-safe on signing error
"""
from __future__ import annotations

import asyncio
import inspect
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure hledac.universal is importable from Hledac/ parent
_ROOT = Path(__file__).parent.parent.parent.parent  # tests->universal->hledac->Hledac
sys.path.insert(0, str(_ROOT))

from hledac.universal.export import jsonld_exporter, stix_exporter
from hledac.universal.security.pq_crypto import (
    PQAvailability,
    PQSignature,
)


class FakePQBackend:
    """Fake PQ backend for unit test injection."""

    name_val: str = "test"
    available: bool = True
    sig: PQSignature | None = None

    def name(self) -> str:
        return self.name_val

    def is_available(self) -> bool:
        return self.available

    def ensure_mldsa_key(self, key_id: str, level: int = 65) -> bool:
        return True

    def sign_mldsa_digest(self, key_id: str, digest: str, level: int = 65) -> PQSignature:
        if self.sig is None:
            self.sig = PQSignature(
                algorithm="ml-dsa-65",
                signature=b"FAKE_SIG_BYTES",
                backend_name=self.name_val,
                security_level=65,
            )
        return self.sig

    def verify_mldsa_signature(self, digest, signature, public_key_bytes, *, level=65) -> bool:
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


class FakeUnavailBackend(FakePQBackend):
    """PQ backend reporting unavailability."""
    available = False

    def is_available(self) -> bool:
        return False


def _minimal_report() -> dict:
    """Minimal report dict matching normalize_export_input expectations."""
    return {
        "type": "observed-run",
        "id": "observed-run--test-0001",
        "spec_version": "2.1",
        "objects": [],
        "started_ts": "2026-05-24T00:00:00Z",
        "finished_ts": "2026-05-24T00:01:00Z",
    }


def test_stix_bundle_pq_signing_path_called():
    """STIX bundle receives PQ extension when backend is available."""
    bundle = _minimal_report()
    fake_backend = FakePQBackend()
    fake_status = fake_backend.pq_status()

    async def _fake_get():
        return fake_backend, fake_status

    with patch.object(stix_exporter, "_get_pq_backend_async", _fake_get):
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(
            stix_exporter._maybe_sign_bundle_async(bundle.copy())
        )
        loop.close()

    assert "extension" in result, "PQ extension not added to bundle"
    ext = result["extension"]
    assert ext.get("extension_type") == "hledac:pq-signature"
    assert "ml_dsa_signature" in ext
    assert "ml_dsa_level" in ext
    assert "bundle_sha256" in ext


def test_stix_bundle_skip_when_pq_unavailable():
    """STIX bundle returns unchanged when PQ backend unavailable."""
    bundle = _minimal_report()
    fake_backend = FakeUnavailBackend()
    fake_status = fake_backend.pq_status()

    async def _fake_get():
        return fake_backend, fake_status

    with patch.object(stix_exporter, "_get_pq_backend_async", _fake_get):
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(
            stix_exporter._maybe_sign_bundle_async(bundle.copy())
        )
        loop.close()

    assert "extension" not in result, "extension should not be added when unavailable"


def test_jsonld_pq_signing_path_called():
    """JSON-LD receives PQ extension when backend is available."""
    obj = _minimal_report()
    fake_backend = FakePQBackend()
    fake_status = fake_backend.pq_status()

    async def _fake_get():
        return fake_backend, fake_status

    with patch.object(jsonld_exporter, "_get_pq_backend_async", _fake_get):
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(
            jsonld_exporter._maybe_sign_jsonld_async(obj.copy())
        )
        loop.close()

    assert "ghost:pqSignature" in result, "PQ extension not added to JSON-LD"
    ext = result["ghost:pqSignature"]
    assert ext.get("extension_type") == "hledac:pq-signature"
    assert "ml_dsa_signature" in ext


def test_jsonld_skip_when_pq_unavailable():
    """JSON-LD returns unchanged when PQ backend unavailable."""
    obj = _minimal_report()
    fake_backend = FakeUnavailBackend()
    fake_status = fake_backend.pq_status()

    async def _fake_get():
        return fake_backend, fake_status

    with patch.object(jsonld_exporter, "_get_pq_backend_async", _fake_get):
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(
            jsonld_exporter._maybe_sign_jsonld_async(obj.copy())
        )
        loop.close()

    assert "extension" not in result, "extension should not be added when unavailable"


def test_gather_return_exceptions_in_stix_async():
    """STIX _maybe_sign_bundle_async uses gather(return_exceptions=True)."""
    src = inspect.getsource(stix_exporter._maybe_sign_bundle_async)
    assert "return_exceptions=True" in src, \
        "gather return_exceptions=True not found in STIX async path"


def test_gather_return_exceptions_in_jsonld_async():
    """JSON-LD _maybe_sign_jsonld_async uses gather(return_exceptions=True)."""
    src = inspect.getsource(jsonld_exporter._maybe_sign_jsonld_async)
    assert "return_exceptions=True" in src, \
        "gather return_exceptions=True not found in JSON-LD async path"


def test_pq_availability_enum_complete():
    """PQAvailability has all required states."""
    members = [e.name for e in PQAvailability]
    for expected in ("DISABLED", "UNAVAILABLE", "AVAILABLE", "SIGNED", "FAIL_SOFT"):
        assert expected in members, f"PQAvailability.{expected} missing"


def test_render_stix_bundle_fail_safe():
    """render_stix_bundle returns dict even with bad input (never raises)."""
    result = stix_exporter.render_stix_bundle({})
    assert isinstance(result, dict), "render_stix_bundle must return dict"

    result2 = stix_exporter.render_stix_bundle({"type": "bundle", "objects": []})
    assert isinstance(result2, dict), "render_stix_bundle must return dict"


def test_build_pq_extension_fail_safe():
    """_build_pq_extension returns None when signing fails."""
    fake_backend = FakePQBackend()
    fake_backend.sign_mldsa_digest = MagicMock(side_effect=Exception("sign failed"))

    result = stix_exporter._build_pq_extension(
        _minimal_report(), fake_backend, "com.hledac.pq.signing.v1"
    )
    assert result is None, "_build_pq_extension must return None on signing error"


if __name__ == "__main__":
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
        test_build_pq_extension_fail_safe,
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
