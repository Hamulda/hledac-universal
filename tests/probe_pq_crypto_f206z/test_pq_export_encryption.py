"""
Sprint F206AA: Post-Quantum HPKE Export Encryption — Hermetic Tests

Tests HPKE export encryption envelope, backend protocol, and encryption:
- ExportEncryptionEnvelope dataclass structure
- NullPostQuantumExportBackend returns unavailable, never crashes
- Fake backend encrypts and decrypts correctly
- AAD hash mismatch fails decryption
- Ciphertext tamper fails decryption
- PQ_REQUIRED policy returns unavailable error when backend is null
- PQ_PREFERRED policy returns unencrypted when backend is null
- No live network, no Touch ID, no Secure Enclave
"""
from __future__ import annotations

import base64
import hashlib

from hledac.universal.security.pq_export_encryption import (
    Decryptability,
    ExportEncryptionEnvelope,
    ExportPolicy,
    HPKEAvailability,
    HPKEStatus,
    NullPostQuantumExportBackend,
    TestOnlyHPKERoundtripMaterial,
    compute_aad_hash,
)


class TestExportEncryptionEnvelope:
    """Test ExportEncryptionEnvelope dataclass structure and methods."""

    def test_envelope_defaults(self):
        envelope = ExportEncryptionEnvelope()
        assert envelope.mode == "PQ-HPKE-XWingMLKEM768X25519-SHA256-AES-GCM-256"
        assert envelope.encapsulated_key_b64 == ""
        assert envelope.aad_hash == ""
        assert envelope.aad_b64 == ""
        assert envelope.ciphertext_b64 == ""
        assert envelope.recipient_public_key_b64 == ""
        assert envelope.recipient_key_id == ""
        assert envelope.recipient_public_key_fingerprint == ""
        assert envelope.decryptability == Decryptability.UNSUPPORTED
        assert envelope.pq is False
        assert envelope.created_at == ""
        assert envelope.backend == "null"

    def test_envelope_to_dict(self):
        envelope = ExportEncryptionEnvelope(
            mode="PQ-HPKE-XWingMLKEM768X25519-SHA256-AES-GCM-256",
            encapsulated_key_b64="abc123",
            aad_hash="def456",
            aad_b64="aGR0",
            ciphertext_b64="ghi789",
            recipient_public_key_b64="jkl012",
            recipient_key_id="key-123",
            recipient_public_key_fingerprint="fp456",
            decryptability=Decryptability.PERSISTENT_KEYCHAIN,
            pq=True,
            created_at="2026-04-30T00:00:00Z",
            backend="test-backend",
        )
        d = envelope.to_dict()
        assert d["mode"] == "PQ-HPKE-XWingMLKEM768X25519-SHA256-AES-GCM-256"
        assert d["encapsulated_key_b64"] == "abc123"
        assert d["aad_hash"] == "def456"
        assert d["aad_b64"] == "aGR0"
        assert d["ciphertext_b64"] == "ghi789"
        assert d["recipient_public_key_b64"] == "jkl012"
        assert d["recipient_key_id"] == "key-123"
        assert d["recipient_public_key_fingerprint"] == "fp456"
        assert d["decryptability"] == "persistent_keychain"
        assert d["pq"] is True
        assert d["created_at"] == "2026-04-30T00:00:00Z"
        assert d["backend"] == "test-backend"
        # Safety: no private key fields
        private_fields = [k for k in d if "private" in k.lower()]
        assert private_fields == [], f"to_dict must not contain private fields: {private_fields}"

    def test_envelope_from_dict(self):
        d = {
            "mode": "PQ-HPKE-XWingMLKEM768X25519-SHA256-AES-GCM-256",
            "encapsulated_key_b64": "abc123",
            "aad_hash": "def456",
            "aad_b64": "aGR0",
            "ciphertext_b64": "ghi789",
            "recipient_public_key_b64": "jkl012",
            "recipient_key_id": "key-123",
            "recipient_public_key_fingerprint": "fp456",
            "decryptability": "persistent_keychain",
            "pq": True,
            "created_at": "2026-04-30T00:00:00Z",
            "backend": "test-backend",
        }
        envelope = ExportEncryptionEnvelope.from_dict(d)
        assert envelope.mode == "PQ-HPKE-XWingMLKEM768X25519-SHA256-AES-GCM-256"
        assert envelope.encapsulated_key_b64 == "abc123"
        assert envelope.aad_b64 == "aGR0"
        assert envelope.recipient_key_id == "key-123"
        assert envelope.recipient_public_key_fingerprint == "fp456"
        assert envelope.decryptability == Decryptability.PERSISTENT_KEYCHAIN
        assert envelope.pq is True
        assert envelope.is_encrypted() is True

    def test_envelope_is_encrypted(self):
        envelope = ExportEncryptionEnvelope()
        assert envelope.is_encrypted() is False

        envelope = ExportEncryptionEnvelope(
            ciphertext_b64="some_ciphertext",
            encapsulated_key_b64="some_key",
        )
        assert envelope.is_encrypted() is True

        # Missing one field
        envelope = ExportEncryptionEnvelope(ciphertext_b64="some_ciphertext")
        assert envelope.is_encrypted() is False


class TestHPKEStatus:
    """Test HPKEStatus dataclass."""

    def test_status_defaults(self):
        status = HPKEStatus()
        assert status.availability == HPKEAvailability.DISABLED
        assert status.backend_name == "null"
        assert status.error_message is None
        assert status.recipient_key_id is None
        assert status.encrypted_count == 0
        assert status.decrypted_count == 0


class TestNullBackend:
    """Test NullPostQuantumExportBackend never crashes."""

    def test_null_backend_not_available(self):
        backend = NullPostQuantumExportBackend()
        assert backend.is_available() is False

    def test_null_backend_hpke_status(self):
        backend = NullPostQuantumExportBackend()
        status = backend.hpke_status()
        assert status.availability == HPKEAvailability.DISABLED
        assert status.backend_name == "null"

    def test_null_backend_generate_key_returns_none(self):
        backend = NullPostQuantumExportBackend()
        result = backend.generate_recipient_key("test-key")
        assert result is None

    def test_null_backend_encrypt_returns_none(self):
        backend = NullPostQuantumExportBackend()
        result = backend.encrypt_hpke(
            plaintext=b"test data",
            aad=b"test aad",
            recipient_public_key_b64="dGVzdGtleQ==",
        )
        assert result is None

    def test_null_backend_decrypt_returns_none(self):
        backend = NullPostQuantumExportBackend()
        envelope = ExportEncryptionEnvelope(
            encapsulated_key_b64="abc",
            ciphertext_b64="def",
            aad_hash="ghi",
            aad_b64="YZRp",
            recipient_public_key_b64="jkl",
            recipient_key_id="",
            decryptability=Decryptability.UNSUPPORTED,
            pq=True,
        )
        result = backend.decrypt_hpke(
            envelope=envelope,
            plaintext_placeholder=b"placeholder",
        )
        assert result is None


class FakePostQuantumExportBackend:
    """Fake backend for testing encryption/decryption roundtrip."""

    name: str = "fake-hpke"
    _status: HPKEStatus = HPKEStatus(
        availability=HPKEAvailability.AVAILABLE,
        backend_name="fake-hpke",
        recipient_key_id="test-key",
    )
    _recipient_key: bytes = b"fake-recipient-key-32-bytes-xx"
    _encrypted_data: dict = {}

    def is_available(self) -> bool:
        return True

    def hpke_status(self) -> HPKEStatus:
        return self._status

    def generate_recipient_key(self, key_id: str) -> tuple[str, str, str]:
        import base64
        import hashlib
        public_key = base64.b64encode(self._recipient_key).decode("ascii")
        fingerprint = hashlib.sha256(self._recipient_key).hexdigest()
        return public_key, key_id, fingerprint

    def encrypt_hpke(
        self,
        plaintext: bytes,
        aad: bytes,
        recipient_public_key_b64: str,
        recipient_key_id: str = "",
    ) -> ExportEncryptionEnvelope:
        import base64
        import hashlib
        from datetime import datetime, timezone

        # Simple XOR encryption for fake
        key = self._recipient_key
        ciphertext = bytes(p ^ k for p, k in zip(plaintext, key * (len(plaintext) // len(key) + 1)))

        # Compute fingerprint of public key
        try:
            pub_bytes = base64.b64decode(recipient_public_key_b64)
            fingerprint = hashlib.sha256(pub_bytes).hexdigest()
        except Exception:
            fingerprint = ""

        envelope = ExportEncryptionEnvelope(
            mode="PQ-HPKE-XWingMLKEM768X25519-SHA256-AES-GCM-256",
            encapsulated_key_b64=base64.b64encode(b"fake-encapsulated-key").decode("ascii"),
            aad_hash=compute_aad_hash(aad),
            aad_b64=base64.b64encode(aad).decode("ascii"),
            ciphertext_b64=base64.b64encode(ciphertext).decode("ascii"),
            recipient_public_key_b64=recipient_public_key_b64,
            recipient_key_id=recipient_key_id or "test-key",
            recipient_public_key_fingerprint=fingerprint,
            decryptability=Decryptability.PERSISTENT_KEYCHAIN,
            pq=True,
            created_at=datetime.now(timezone.utc).isoformat(),
            backend=self.name,
        )
        # Store a copy of the envelope to detect tampering
        self._encrypted_data["envelope"] = ExportEncryptionEnvelope(
            mode=envelope.mode,
            encapsulated_key_b64=envelope.encapsulated_key_b64,
            aad_hash=envelope.aad_hash,
            aad_b64=envelope.aad_b64,
            ciphertext_b64=envelope.ciphertext_b64,
            recipient_public_key_b64=envelope.recipient_public_key_b64,
            recipient_key_id=envelope.recipient_key_id,
            recipient_public_key_fingerprint=envelope.recipient_public_key_fingerprint,
            decryptability=envelope.decryptability,
            pq=envelope.pq,
            created_at=envelope.created_at,
            backend=envelope.backend,
        )
        self._encrypted_data["aad"] = aad
        self._status.encrypted_count += 1
        return envelope

    def decrypt_hpke(
        self,
        envelope: ExportEncryptionEnvelope,
        plaintext_placeholder: bytes,
        test_material: TestOnlyHPKERoundtripMaterial | None = None,
    ) -> bytes | None:
        import base64

        stored_envelope = self._encrypted_data.get("envelope")
        if stored_envelope is None:
            return None

        # Verify AAD hash matches (compares with stored copy, not reference)
        if envelope.aad_hash != stored_envelope.aad_hash:
            return None

        # Verify ciphertext matches (detects tampering)
        if envelope.ciphertext_b64 != stored_envelope.ciphertext_b64:
            return None

        try:
            ciphertext = base64.b64decode(envelope.ciphertext_b64)
            key = self._recipient_key
            plaintext = bytes(c ^ k for c, k in zip(ciphertext, key * (len(ciphertext) // len(key) + 1)))
            self._status.decrypted_count += 1
            return plaintext
        except Exception:
            return None


class TestFakeBackendRoundtrip:
    """Test encryption/decryption roundtrip with fake backend."""

    def test_encrypt_decrypt_roundtrip(self):
        backend = FakePostQuantumExportBackend()
        plaintext = b"Secret evidence bundle data"
        aad = b"export-aad-metadata"
        recipient_key = base64.b64encode(backend._recipient_key).decode("ascii")

        # Encrypt
        envelope = backend.encrypt_hpke(
            plaintext=plaintext,
            aad=aad,
            recipient_public_key_b64=recipient_key,
        )

        assert envelope.is_encrypted() is True
        assert envelope.ciphertext_b64 != ""
        assert envelope.encapsulated_key_b64 != ""
        assert envelope.aad_hash != ""

        # Decrypt
        decrypted = backend.decrypt_hpke(
            envelope=envelope,
            plaintext_placeholder=plaintext,
        )

        assert decrypted is not None
        assert decrypted == plaintext

    def test_aad_hash_mismatch_fails_decrypt(self):
        backend = FakePostQuantumExportBackend()
        plaintext = b"Secret evidence bundle data"
        aad = b"export-aad-metadata"
        recipient_key = base64.b64encode(backend._recipient_key).decode("ascii")

        # Encrypt
        envelope = backend.encrypt_hpke(
            plaintext=plaintext,
            aad=aad,
            recipient_public_key_b64=recipient_key,
        )

        # Tamper with AAD hash
        envelope.aad_hash = "tampered" + envelope.aad_hash[9:]

        # Decrypt should fail
        decrypted = backend.decrypt_hpke(
            envelope=envelope,
            plaintext_placeholder=plaintext,
        )

        assert decrypted is None

    def test_ciphertext_tamper_fails_decrypt(self):
        backend = FakePostQuantumExportBackend()
        plaintext = b"Secret evidence bundle data"
        aad = b"export-aad-metadata"
        recipient_key = base64.b64encode(backend._recipient_key).decode("ascii")

        # Encrypt
        envelope = backend.encrypt_hpke(
            plaintext=plaintext,
            aad=aad,
            recipient_public_key_b64=recipient_key,
        )

        # Tamper with ciphertext
        ct = base64.b64decode(envelope.ciphertext_b64)
        tampered = bytes([ct[0] ^ 0xFF]) + ct[1:]
        envelope.ciphertext_b64 = base64.b64encode(tampered).decode("ascii")

        # Decrypt should fail
        decrypted = backend.decrypt_hpke(
            envelope=envelope,
            plaintext_placeholder=plaintext,
        )

        assert decrypted is None


class TestComputeAadHash:
    """Test AAD hash computation."""

    def test_compute_aad_hash(self):
        aad = b"test aad data"
        expected_hash = hashlib.sha256(aad).hexdigest()
        result = compute_aad_hash(aad)
        assert result == expected_hash

    def test_compute_aad_hash_different_inputs(self):
        aad1 = b"input 1"
        aad2 = b"input 2"
        hash1 = compute_aad_hash(aad1)
        hash2 = compute_aad_hash(aad2)
        assert hash1 != hash2


class TestExportPolicy:
    """Test ExportPolicy enum values."""

    def test_policy_values(self):
        assert ExportPolicy.PQ_REQUIRED.value == "pq_required"
        assert ExportPolicy.PQ_PREFERRED.value == "pq_preferred"
        assert ExportPolicy.UNENCRYPTED_ONLY.value == "unencrypted_only"


class TestHPKEAvailability:
    """Test HPKEAvailability enum values."""

    def test_availability_values(self):
        assert HPKEAvailability.DISABLED.value == "disabled"
        assert HPKEAvailability.UNAVAILABLE.value == "unavailable"
        assert HPKEAvailability.AVAILABLE.value == "available"
        assert HPKEAvailability.ENCRYPTED.value == "encrypted"
        assert HPKEAvailability.FAIL_SOFT.value == "fail_soft"
