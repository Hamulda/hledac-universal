"""
Sprint F206X: Secure Enclave Reality Lock — Hermetic Tests

Tests the new internal Secure Enclave abstraction:
- NullSecureEnclaveBackend returns unavailable
- Missing external import does not crash
- _secure_process returns original chunks unchanged
- Fake backend signs exactly one batch digest, not one digest per chunk
- Backend exception does not fail the sprint path
- Telemetry distinguishes disabled/unavailable/signed/fail_soft
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

# Import the new abstraction
from hledac.universal.security.secure_enclave import (
    EnclaveAvailability,
    EnclaveStatus,
    BatchManifest,
    SecureEnclaveBackend,
    SecureEnclaveError,
    NullSecureEnclaveBackend,
    build_batch_manifest,
    create_secure_enclave_backend,
)


class TestBuildBatchManifest:
    """Test canonical batch manifest building."""

    def test_empty_chunks(self):
        manifest = build_batch_manifest([])
        assert manifest.chunk_count == 0
        assert manifest.chunk_hashes == []
        assert manifest.batch_digest is not None

    def test_single_chunk(self):
        chunks = ["hello world"]
        manifest = build_batch_manifest(chunks)
        assert manifest.chunk_count == 1
        assert len(manifest.chunk_hashes) == 1
        assert manifest.batch_digest is not None

    def test_multiple_chunks(self):
        chunks = ["chunk1", "chunk2", "chunk3"]
        manifest = build_batch_manifest(chunks)
        assert manifest.chunk_count == 3
        assert len(manifest.chunk_hashes) == 3

    def test_deterministic_digest(self):
        """Same chunks always produce same batch_digest."""
        chunks = ["alpha", "beta", "gamma"]
        m1 = build_batch_manifest(chunks)
        m2 = build_batch_manifest(chunks)
        assert m1.batch_digest == m2.batch_digest

    def test_order_independent_digest(self):
        """Batch digest is order-independent (sorted hashes)."""
        chunks_a = ["a", "b", "c"]
        chunks_b = ["c", "b", "a"]
        m1 = build_batch_manifest(chunks_a)
        m2 = build_batch_manifest(chunks_b)
        # Batch digest must be same regardless of chunk order
        # (we sort hashes before computing batch_digest)
        assert m1.batch_digest == m2.batch_digest
        # But chunk_hashes list follows input order, so differs
        assert m1.chunk_hashes != m2.chunk_hashes


class TestNullSecureEnclaveBackend:
    """Test NullSecureEnclaveBackend behavior."""

    def test_is_available_returns_false(self):
        backend = NullSecureEnclaveBackend()
        assert backend.is_available() is False

    def test_name_is_null(self):
        backend = NullSecureEnclaveBackend()
        assert backend.name == "null"

    @pytest.mark.asyncio
    async def test_sign_batch_digest_raises(self):
        backend = NullSecureEnclaveBackend()
        manifest = build_batch_manifest(["test"])
        with pytest.raises(SecureEnclaveError):
            await backend.sign_batch_digest(manifest)


class TestCreateSecureEnclaveBackend:
    """Test backend factory function."""

    @pytest.mark.asyncio
    async def test_disabled_returns_null(self):
        """When enabled=False, returns null backend with DISABLED status."""
        backend, status = await create_secure_enclave_backend(enabled=False)
        assert isinstance(backend, NullSecureEnclaveBackend)
        assert status.availability == EnclaveAvailability.DISABLED

    @pytest.mark.asyncio
    async def test_returns_valid_tuple(self):
        """When enabled=True, returns (backend, status) tuple with valid status."""
        backend, status = await create_secure_enclave_backend(enabled=True)
        # Returns a tuple
        assert backend is not None
        assert status is not None
        # Status has valid availability
        assert status.availability in (
            EnclaveAvailability.AVAILABLE,
            EnclaveAvailability.UNAVAILABLE,
        )


class FakeSecureEnclaveBackend:
    """Fake backend for testing that signs exactly one batch digest."""

    name: str = "fake"

    def __init__(self):
        self._available = True
        self.signatures: list[bytes] = []

    def is_available(self) -> bool:
        return self._available

    async def sign_batch_digest(self, manifest: BatchManifest) -> "SignedDigest":
        """Sign exactly ONE batch digest for the entire batch."""
        from hledac.universal.security.secure_enclave import SignedDigest

        # Produce ONE signature for the batch digest
        sig = f"fake_sig_for_{manifest.batch_digest}".encode()
        self.signatures.append(sig)

        return SignedDigest(
            batch_digest=manifest.batch_digest,
            signature=sig,
            backend_name=self.name,
            chunk_count=manifest.chunk_count,
        )


class TestFakeBackendSignsBatchDigest:
    """Test that fake backend signs exactly one digest per batch."""

    @pytest.mark.asyncio
    async def test_signs_one_digest_per_batch(self):
        """One call to sign_batch_digest produces exactly one signature."""
        from hledac.universal.security.secure_enclave import build_batch_manifest

        fake = FakeSecureEnclaveBackend()
        chunks = ["a", "b", "c", "d", "e"]

        manifest = build_batch_manifest(chunks)
        result = await fake.sign_batch_digest(manifest)

        # Exactly one signature for the batch
        assert len(fake.signatures) == 1
        assert result.batch_digest == manifest.batch_digest
        assert result.chunk_count == 5

    @pytest.mark.asyncio
    async def test_not_one_per_chunk(self):
        """Multiple chunks produce one signature, not one per chunk."""
        from hledac.universal.security.secure_enclave import build_batch_manifest

        fake = FakeSecureEnclaveBackend()
        chunks = ["chunk1", "chunk2", "chunk3", "chunk4", "chunk5"]

        manifest = build_batch_manifest(chunks)
        await fake.sign_batch_digest(manifest)

        # Still exactly one signature, not one per chunk
        assert len(fake.signatures) == 1


class TestEnclaveStatusTelemetry:
    """Test that EnclaveStatus correctly reflects all availability states."""

    def test_disabled_has_correct_availability(self):
        status = EnclaveStatus(availability=EnclaveAvailability.DISABLED)
        assert status.availability == EnclaveAvailability.DISABLED
        assert status.signed_batch_digest is None
        assert status.chunk_count == 0

    def test_unavailable_has_error_message(self):
        status = EnclaveStatus(
            availability=EnclaveAvailability.UNAVAILABLE,
            error_message="Import failed: No module named 'hledac.ultra_context'"
        )
        assert status.availability == EnclaveAvailability.UNAVAILABLE
        assert "Import failed" in status.error_message

    def test_signed_has_digest_and_count(self):
        status = EnclaveStatus(
            availability=EnclaveAvailability.SIGNED,
            signed_batch_digest="abc123def456",
            chunk_count=42
        )
        assert status.availability == EnclaveAvailability.SIGNED
        assert status.signed_batch_digest == "abc123def456"
        assert status.chunk_count == 42

    def test_fail_soft_has_error(self):
        status = EnclaveStatus(
            availability=EnclaveAvailability.FAIL_SOFT,
            error_message="Signing operation failed"
        )
        assert status.availability == EnclaveAvailability.FAIL_SOFT
        assert "Signing operation failed" in status.error_message


class TestSecureEnclaveProtocol:
    """Test that SecureEnclaveBackend Protocol is properly defined."""

    def test_null_satisfies_protocol(self):
        """NullSecureEnclaveBackend satisfies the SecureEnclaveBackend Protocol."""
        backend = NullSecureEnclaveBackend()
        # Protocol check via runtime_checkable
        assert isinstance(backend, SecureEnclaveBackend)

    def test_fake_satisfies_protocol(self):
        """FakeSecureEnclaveBackend satisfies the SecureEnclaveBackend Protocol."""
        fake = FakeSecureEnclaveBackend()
        assert isinstance(fake, SecureEnclaveBackend)


# ── RAGEngine Integration Tests ────────────────────────────────────────────────

@pytest.fixture
def mock_rag_engine(monkeypatch):
    """Create a minimal RAGEngine for integration testing."""
    from knowledge.rag_engine import RAGEngine, RAGConfig

    # Patch out all external dependencies
    monkeypatch.setattr("knowledge.rag_engine.create_secure_enclave_backend", AsyncMock(
        return_value=(NullSecureEnclaveBackend(), EnclaveStatus(availability=EnclaveAvailability.DISABLED))
    ))

    config = RAGConfig(enable_secure_enclave=True)
    engine = RAGEngine(config)
    return engine


class TestRAGEngineSecureEnclaveIntegration:
    """Integration tests for _secure_process in RAGEngine context."""

    @pytest.mark.asyncio
    async def test_secure_process_returns_chunks_unchanged(self, mock_rag_engine):
        """_secure_process must return original chunks, never mutated."""
        await mock_rag_engine._init_secure_enclave()

        original = ["chunk1", "chunk2", "chunk3"]
        result = await mock_rag_engine._secure_process(original)

        assert result == original
        assert result is original  # Same list object

    @pytest.mark.asyncio
    async def test_secure_process_fails_soft_on_exception(self, mock_rag_engine):
        """Backend exception must not raise — fail-soft."""

        class FailingBackend:
            name = "failing"
            def is_available(self): return True
            async def sign_batch_digest(self, manifest):
                raise SecureEnclaveError("Signing failed")

        mock_rag_engine._secure_enclave = FailingBackend()
        mock_rag_engine._enclave_status = EnclaveStatus(availability=EnclaveAvailability.AVAILABLE)

        original = ["test", "chunks"]
        result = await mock_rag_engine._secure_process(original)

        # Still returns chunks unchanged despite exception
        assert result == original
        # Status reflects failure
        assert mock_rag_engine._enclave_status.availability == EnclaveAvailability.FAIL_SOFT

    @pytest.mark.asyncio
    async def test_secure_process_with_fake_backend(self, mock_rag_engine):
        """With a working fake backend, _secure_process returns chunks and records signature."""
        fake = FakeSecureEnclaveBackend()
        mock_rag_engine._secure_enclave = fake
        mock_rag_engine._enclave_status = EnclaveStatus(availability=EnclaveAvailability.AVAILABLE)

        chunks = ["a", "b", "c"]
        result = await mock_rag_engine._secure_process(chunks)

        assert result == chunks  # Chunks returned unchanged
        assert len(fake.signatures) == 1  # One batch signature produced
        assert mock_rag_engine._enclave_status.availability == EnclaveAvailability.SIGNED

    @pytest.mark.asyncio
    async def test_secure_process_when_disabled(self, mock_rag_engine):
        """When backend not available, returns chunks unchanged immediately."""
        mock_rag_engine._secure_enclave = None
        mock_rag_engine._enclave_status = None

        chunks = ["test", "data"]
        result = await mock_rag_engine._secure_process(chunks)

        assert result == chunks

    @pytest.mark.asyncio
    async def test_query_with_secure_true_uses_secure_process(self, mock_rag_engine):
        """query(secure=True) path correctly invokes _secure_process."""
        # Setup fake backend
        fake = FakeSecureEnclaveBackend()
        mock_rag_engine._secure_enclave = fake
        mock_rag_engine._enclave_status = EnclaveStatus(availability=EnclaveAvailability.AVAILABLE)

        chunks = ["chunk1", "chunk2"]

        # Call query with secure=True
        result = await mock_rag_engine.query(
            query="test query",
            context_chunks=list(chunks),  # copy to avoid mutation
            secure=True
        )

        # Chunks should be returned unchanged
        assert result["chunks_used"] == len(chunks)
        assert result["secure"] is True

        # One batch signature recorded
        assert len(fake.signatures) == 1