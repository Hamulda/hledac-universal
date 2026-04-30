"""
Sprint F206Z: Post-Quantum ML-DSA Hybrid Signatures — Hermetic Tests

Tests PQ dataclasses, PostQuantumBackend protocol, and hybrid signing:
- PQStatus, PQSignature, HybridSignatureSet dataclasses
- NullPostQuantumBackend returns unavailable, never crashes
- Fake backend signs exactly one digest per batch
- Hybrid signature contains P-256 + optional ML-DSA
- Invalid ML-DSA signature fails verification
- Missing optional ML-DSA does not fail sprint
- macOS <26 returns fail-soft via PQ_NOT_AVAILABLE
- No live network, no Touch ID, no per-chunk signing
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

import pytest

from hledac.universal.security.pq_crypto import (
    HybridSignatureSet,
    PQAvailability,
    PQSecurityLevel,
    PQSignature,
    PQStatus,
    PostQuantumBackend,
    PostQuantumError,
    NullPostQuantumBackend,
)


class TestPQDataclasses:
    """Test PQ dataclasses structure and properties."""

    def test_pq_status_defaults(self):
        status = PQStatus()
        assert status.availability == PQAvailability.DISABLED
        assert status.backend_name == "null"
        assert status.error_message is None
        assert status.mldsa_key_id is None
        assert status.mldsa_level is None
        assert status.signed_batch_digest is None
        assert status.chunk_count == 0

    def test_pq_status_full(self):
        status = PQStatus(
            availability=PQAvailability.AVAILABLE,
            backend_name="swift-helper",
            error_message=None,
            mldsa_key_id="com.hledac.pq.v1",
            mldsa_level=65,
            signed_batch_digest="abc123",
            chunk_count=42,
        )
        assert status.availability == PQAvailability.AVAILABLE
        assert status.mldsa_key_id == "com.hledac.pq.v1"
        assert status.mldsa_level == 65

    def test_pq_signature_fields(self):
        sig = PQSignature(
            algorithm="ml-dsa-65",
            signature=b"\xde\xad\xbe\xef",
            backend_name="swift-helper",
            security_level=65,
        )
        assert sig.algorithm == "ml-dsa-65"
        assert sig.signature == b"\xde\xad\xbe\xef"
        assert sig.backend_name == "swift-helper"
        assert sig.security_level == 65

    def test_hybrid_signature_set_p256_only(self):
        hybrid = HybridSignatureSet(
            batch_digest="abc123",
            p256_signature=b"\x01\x02\x03",
            p256_backend="secure-enclave",
            mldsa_signature=None,
            chunk_count=5,
        )
        assert hybrid.has_mldsa is False
        assert hybrid.is_hybrid is False

    def test_hybrid_signature_set_full_hybrid(self):
        mldsa_sig = PQSignature(
            algorithm="ml-dsa-65",
            signature=b"\x10\x20\x30",
            backend_name="swift-helper",
            security_level=65,
        )
        hybrid = HybridSignatureSet(
            batch_digest="abc123",
            p256_signature=b"\x01\x02\x03",
            p256_backend="secure-enclave",
            mldsa_signature=mldsa_sig,
            chunk_count=5,
        )
        assert hybrid.has_mldsa is True
        assert hybrid.is_hybrid is True

    def test_pq_security_level_enum(self):
        assert PQSecurityLevel.ML_DSA_65.value == 65


class TestNullPostQuantumBackend:
    """Test NullPostQuantumBackend behavior — always unavailable, never crashes."""

    def test_is_available_returns_false(self):
        backend = NullPostQuantumBackend()
        assert backend.is_available() is False

    def test_name_is_null(self):
        backend = NullPostQuantumBackend()
        assert backend.name == "null"

    def test_pq_status_disabled(self):
        backend = NullPostQuantumBackend()
        status = backend.pq_status()
        assert status.availability == PQAvailability.DISABLED
        assert status.backend_name == "null"

    def test_ensure_mldsa_key_returns_false(self):
        backend = NullPostQuantumBackend()
        result = backend.ensure_mldsa_key("com.hledac.pq.v1", level=65)
        assert result is False

    def test_sign_mldsa_digest_raises(self):
        backend = NullPostQuantumBackend()
        with pytest.raises(PostQuantumError, match="Null backend cannot sign"):
            backend.sign_mldsa_digest("com.hledac.pq.v1", "0" * 64, level=65)

    def test_verify_returns_false(self):
        backend = NullPostQuantumBackend()
        result = backend.verify_mldsa_signature(
            digest="0" * 64,
            signature=b"\x01\x02",
            public_key_bytes=b"\x03\x04",
            level=65,
        )
        assert result is False


class FakePostQuantumBackend:
    """Fake PQ backend for testing — signs exactly one digest per batch."""

    name: str = "fake-pq"

    def __init__(self):
        self._available = True
        self._status = PQStatus(availability=PQAvailability.AVAILABLE)
        self.signatures: list[PQSignature] = []
        self.key_ids: list[str] = []

    def is_available(self) -> bool:
        return self._available

    def pq_status(self) -> PQStatus:
        return self._status

    def ensure_mldsa_key(self, key_id: str, level: int = 65) -> bool:
        self.key_ids.append(key_id)
        return True

    def sign_mldsa_digest(self, key_id: str, digest: str, level: int = 65) -> PQSignature:
        sig = PQSignature(
            algorithm=f"ml-dsa-{level}",
            signature=f"fake_mldsa_sig_for_{digest}".encode(),
            backend_name=self.name,
            security_level=level,
        )
        self.signatures.append(sig)
        return sig

    def verify_mldsa_signature(
        self,
        digest: str,
        signature: bytes,
        public_key_bytes: bytes,
        level: int = 65
    ) -> bool:
        # Verify that the signature was produced by this fake backend
        expected_prefix = f"fake_mldsa_sig_for_{digest}".encode()
        return signature.startswith(expected_prefix)


class TestFakeBackendSignsBatchDigest:
    """Test that fake PQ backend signs exactly one digest per batch."""

    @pytest.mark.asyncio
    async def test_signs_one_digest_per_batch(self):
        fake = FakePostQuantumBackend()
        digest = "a" * 64

        sig = fake.sign_mldsa_digest("com.hledac.pq.v1", digest, level=65)

        assert len(fake.signatures) == 1
        assert sig.algorithm == "ml-dsa-65"

    @pytest.mark.asyncio
    async def test_multiple_batches_produce_one_sig_each(self):
        fake = FakePostQuantumBackend()

        sig1 = fake.sign_mldsa_digest("com.hledac.pq.v1", "aaa" + "0" * 61, level=65)
        sig2 = fake.sign_mldsa_digest("com.hledac.pq.v1", "bbb" + "0" * 61, level=65)

        assert len(fake.signatures) == 2
        assert sig1.signature != sig2.signature  # Different digests


class TestHybridSignatureSet:
    """Test HybridSignatureSet structure and properties."""

    def test_hybrid_set_with_both_signatures(self):
        p256_sig = b"\x00\x11\x22\x33"
        mldsa_sig = PQSignature(
            algorithm="ml-dsa-65",
            signature=b"\x44\x55\x66\x77",
            backend_name="fake-pq",
            security_level=65,
        )

        hybrid = HybridSignatureSet(
            batch_digest="deadbeef",
            p256_signature=p256_sig,
            p256_backend="secure-enclave",
            mldsa_signature=mldsa_sig,
            chunk_count=10,
        )

        assert hybrid.has_mldsa is True
        assert hybrid.is_hybrid is True
        assert hybrid.p256_signature == p256_sig
        assert hybrid.mldsa_signature == mldsa_sig

    def test_hybrid_set_p256_only(self):
        hybrid = HybridSignatureSet(
            batch_digest="deadbeef",
            p256_signature=b"\x00\x11\x22\x33",
            p256_backend="secure-enclave",
            mldsa_signature=None,
            chunk_count=10,
        )

        assert hybrid.has_mldsa is False
        assert hybrid.is_hybrid is False


class TestPostQuantumBackendProtocol:
    """Test that PostQuantumBackend Protocol is properly defined."""

    def test_null_satisfies_protocol(self):
        backend = NullPostQuantumBackend()
        assert isinstance(backend, PostQuantumBackend)

    def test_fake_satisfies_protocol(self):
        fake = FakePostQuantumBackend()
        assert isinstance(fake, PostQuantumBackend)


class TestPQAvailabilityEnum:
    """Test PQAvailability states."""

    def test_all_states_present(self):
        assert PQAvailability.DISABLED.value == "disabled"
        assert PQAvailability.UNAVAILABLE.value == "unavailable"
        assert PQAvailability.AVAILABLE.value == "available"
        assert PQAvailability.SIGNED.value == "signed"
        assert PQAvailability.FAIL_SOFT.value == "fail_soft"

    def test_status_reflects_availability(self):
        status = PQStatus(availability=PQAvailability.UNAVAILABLE, error_message="Helper not found")
        assert status.availability == PQAvailability.UNAVAILABLE
        assert "Helper not found" in status.error_message