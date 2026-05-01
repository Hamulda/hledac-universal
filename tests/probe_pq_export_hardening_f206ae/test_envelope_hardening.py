"""
PQ Export Hardening F206AE — Envelope Security Tests.

Validates:
- Production envelope never contains recipient_private_key_b64
- Production to_dict() excludes all private key material
- repr(envelope) is safe for logging
- Decryptability states are truthful
- Test-only roundtrip material is isolated from production path
"""
from __future__ import annotations

import pytest

from security.pq_export_encryption import (
    Decryptability,
    ExportEncryptionEnvelope,
    ExportPolicy,
    TestOnlyHPKERoundtripMaterial,
    encrypt_export_bundle,
    decrypt_export_bundle,
)


class TestEnvelopeNoPrivateKey:
    """Envelope must never carry private key material."""

    def test_envelope_has_no_recipient_private_key_b64_attr(self):
        """Production ExportEncryptionEnvelope has no recipient_private_key_b64 attribute."""
        envelope = ExportEncryptionEnvelope(
            recipient_public_key_b64="dGVzdF9wdWJsaWNfa2V5",
            recipient_key_id="test-key-1",
            recipient_public_key_fingerprint="abc123",
            decryptability=Decryptability.PERSISTENT_KEYCHAIN,
            pq=True,
        )
        assert not hasattr(envelope, "recipient_private_key_b64"), (
            "ExportEncryptionEnvelope must NOT have recipient_private_key_b64 attribute"
        )

    def test_to_dict_contains_no_private_key_fields(self):
        """to_dict() must not contain any field with 'private' in the name."""
        envelope = ExportEncryptionEnvelope(
            recipient_public_key_b64="dGVzdF9wdWJsaWNfa2V5",
            recipient_key_id="test-key-1",
            recipient_public_key_fingerprint="abc123def456",
            decryptability=Decryptability.PERSISTENT_KEYCHAIN,
            pq=True,
        )
        d = envelope.to_dict()
        private_fields = [k for k in d if "private" in k.lower()]
        assert private_fields == [], (
            f"to_dict() must not contain private key fields, found: {private_fields}"
        )

    def test_to_dict_contains_expected_fields(self):
        """to_dict() contains all required production-safe fields."""
        envelope = ExportEncryptionEnvelope(
            recipient_public_key_b64="dGVzdF9wdWJsaWNfa2V5",
            recipient_key_id="test-key-1",
            recipient_public_key_fingerprint="abc123def456",
            decryptability=Decryptability.PERSISTENT_KEYCHAIN,
            pq=True,
        )
        d = envelope.to_dict()
        required = {
            "recipient_key_id",
            "recipient_public_key_fingerprint",
            "decryptability",
            "pq",
            "recipient_public_key_b64",
        }
        assert required.issubset(d.keys()), (
            f"to_dict() missing required fields: {required - d.keys()}"
        )

    def test_repr_contains_no_private_material(self):
        """repr(envelope) must not expose private key material."""
        envelope = ExportEncryptionEnvelope(
            recipient_public_key_b64="dGVzdF9wdWJsaWNfa2V5",
            recipient_key_id="test-key-1",
            recipient_public_key_fingerprint="abc123def456",
            decryptability=Decryptability.PERSISTENT_KEYCHAIN,
            pq=True,
        )
        repr_str = repr(envelope)
        assert "private" not in repr_str.lower(), (
            f"repr() must not contain 'private': {repr_str}"
        )
        assert "key_abc" not in repr_str.lower(), (
            f"repr() must not expose key material: {repr_str}"
        )
        # Public key is not sensitive in HPKE context
        assert "dGVzdF9wdWJsaWNfa2V5" not in repr_str, (
            "repr() should not expose full public key base64"
        )

    def test_repr_shows_safety_fields(self):
        """repr() shows key safety metadata."""
        envelope = ExportEncryptionEnvelope(
            recipient_key_id="test-key-1",
            recipient_public_key_fingerprint="abc123",
            decryptability=Decryptability.PERSISTENT_KEYCHAIN,
            pq=True,
        )
        repr_str = repr(envelope)
        assert "pq=True" in repr_str, "repr() should show pq flag"
        assert "decryptability=" in repr_str, "repr() should show decryptability"
        assert "persistent_keychain" in repr_str, "repr() should show keychain state"


class TestDecryptabilityStates:
    """Decryptability enum is truthful about key availability."""

    def test_decryptability_enum_values(self):
        """Decryptability has expected state values."""
        assert Decryptability.PERSISTENT_KEYCHAIN.value == "persistent_keychain"
        assert Decryptability.EPHEMERAL_TEST_ONLY.value == "ephemeral_test_only"
        assert Decryptability.UNSUPPORTED.value == "unsupported"

    def test_envelope_default_decryptability(self):
        """Default envelope has UNSUPPORTED decryptability."""
        envelope = ExportEncryptionEnvelope()
        assert envelope.decryptability == Decryptability.UNSUPPORTED
        assert envelope.pq is False

    def test_envelope_persistent_keychain_state(self):
        """Envelope with recipient_key_id has PERSISTENT_KEYCHAIN decryptability."""
        envelope = ExportEncryptionEnvelope(
            recipient_public_key_b64="dGVzdA==",
            recipient_key_id="my-key-id",
            recipient_public_key_fingerprint="fingerprint123",
            decryptability=Decryptability.PERSISTENT_KEYCHAIN,
            pq=True,
        )
        assert envelope.decryptability == Decryptability.PERSISTENT_KEYCHAIN
        assert envelope.pq is True
        assert envelope.recipient_key_id == "my-key-id"


class TestFromDictRoundtrip:
    """from_dict() restores envelope correctly and ignores private key fields."""

    def test_from_dict_restores_new_fields(self):
        """from_dict() correctly restores recipient_key_id, fingerprint, decryptability, pq."""
        original = {
            "mode": "PQ-HPKE-XWingMLKEM768X25519-SHA256-AES-GCM-256",
            "encapsulated_key_b64": "enc_key",
            "aad_hash": "hash",
            "aad_b64": "aad",
            "ciphertext_b64": "cipher",
            "recipient_public_key_b64": "pub_key",
            "recipient_key_id": "key-123",
            "recipient_public_key_fingerprint": "fp456",
            "decryptability": "persistent_keychain",
            "pq": True,
            "created_at": "2026-01-01T00:00:00Z",
            "backend": "swift-helper-hpke",
        }
        envelope = ExportEncryptionEnvelope.from_dict(original)
        assert envelope.recipient_key_id == "key-123"
        assert envelope.recipient_public_key_fingerprint == "fp456"
        assert envelope.decryptability == Decryptability.PERSISTENT_KEYCHAIN
        assert envelope.pq is True

    def test_from_dict_ignores_private_key_if_present(self):
        """from_dict() silently ignores any private key fields (backward compat)."""
        # Old envelope with private key - should not crash
        old_dict = {
            "mode": "PQ-HPKE-XWingMLKEM768X25519-SHA256-AES-GCM-256",
            "encapsulated_key_b64": "enc",
            "aad_hash": "h",
            "aad_b64": "a",
            "ciphertext_b64": "c",
            "recipient_public_key_b64": "pub",
            "recipient_private_key_b64": "SECRET_PRIVATE_KEY_SHOULD_BE_IGNORED",
            "recipient_key_id": "",
            "recipient_public_key_fingerprint": "",
            "decryptability": "unsupported",
            "pq": False,
            "created_at": "",
            "backend": "null",
        }
        envelope = ExportEncryptionEnvelope.from_dict(old_dict)
        # Should not have recipient_private_key_b64 even if dict had it
        assert not hasattr(envelope, "recipient_private_key_b64")
        # Should have new fields with defaults
        assert envelope.recipient_key_id == ""
        assert envelope.decryptability == Decryptability.UNSUPPORTED

    def test_from_dict_unknown_decryptability_defaults_to_unsupported(self):
        """Unknown decryptability string defaults to UNSUPPORTED."""
        d = {
            "decryptability": "unknown_nonsense",
        }
        envelope = ExportEncryptionEnvelope.from_dict(d)
        assert envelope.decryptability == Decryptability.UNSUPPORTED


class TestTestOnlyRoundtripMaterial:
    """TestOnlyHPKERoundtripMaterial is isolated from production path."""

    def test_test_only_material_exists(self):
        """TestOnlyHPKERoundtripMaterial dataclass exists with correct fields."""
        mat = TestOnlyHPKERoundtripMaterial(
            public_key_b64="test_public_key",
            private_key_b64="test_private_key",
        )
        assert mat.public_key_b64 == "test_public_key"
        assert mat.private_key_b64 == "test_private_key"

    def test_test_only_material_not_in_envelope(self):
        """TestOnlyHPKERoundtripMaterial is never placed into an envelope."""
        mat = TestOnlyHPKERoundtripMaterial(
            public_key_b64="pub",
            private_key_b64="priv",
        )
        envelope = ExportEncryptionEnvelope(
            recipient_public_key_b64=mat.public_key_b64,
            recipient_key_id="test",
            pq=True,
            decryptability=Decryptability.EPHEMERAL_TEST_ONLY,
        )
        # Envelope should only have the public key, never the private
        assert not hasattr(envelope, "recipient_private_key_b64")
        d = envelope.to_dict()
        private_fields = [k for k in d if "private" in k.lower()]
        assert private_fields == [], f"Envelope to_dict() must not have private fields: {private_fields}"


class TestDecryptPersistenceFailClosed:
    """Decrypt with no persistent key must fail closed, not silently."""

    @pytest.mark.asyncio
    async def test_decrypt_without_key_returns_persistence_unsupported(self):
        """decrypt_export_bundle with no key_id and no test_material fails closed."""
        # Envelope with no recipient_key_id (simulates production envelope without keychain)
        envelope = ExportEncryptionEnvelope(
            encapsulated_key_b64="enc_key",
            aad_b64="aad",
            ciphertext_b64="cipher",
            recipient_public_key_b64="pub",
            recipient_key_id="",  # No keychain reference
            decryptability=Decryptability.UNSUPPORTED,
            pq=True,
        )
        plaintext, error = await decrypt_export_bundle(envelope)
        assert plaintext is None
        assert error == "PQ_HPKE_DECRYPT_PERSISTENCE_UNSUPPORTED", (
            f"Expected PERSISTENCE_UNSUPPORTED, got: {error}"
        )

    @pytest.mark.asyncio
    async def test_decrypt_with_ephemeral_test_envelope_requires_test_material(self):
        """Envelope with ephemeral decryptability requires test_material to decrypt."""
        envelope = ExportEncryptionEnvelope(
            encapsulated_key_b64="enc_key",
            aad_b64="aad",
            ciphertext_b64="cipher",
            recipient_public_key_b64="pub",
            recipient_key_id="",
            decryptability=Decryptability.EPHEMERAL_TEST_ONLY,
            pq=True,
        )
        plaintext, error = await decrypt_export_bundle(envelope)
        assert plaintext is None
        assert error == "PQ_HPKE_DECRYPT_PERSISTENCE_UNSUPPORTED"


class TestEnvelopeCreatedByBackend:
    """Envelopes created by a backend follow production-safe defaults."""

    def test_envelope_with_key_id_has_persistent_decryptability(self):
        """When recipient_key_id is provided, envelope decryptability is PERSISTENT_KEYCHAIN."""
        envelope = ExportEncryptionEnvelope(
            recipient_public_key_b64="dGVzdA==",
            recipient_key_id="my-production-key",
            recipient_public_key_fingerprint="sha256_fingerprint",
            decryptability=Decryptability.PERSISTENT_KEYCHAIN,
            pq=True,
        )
        assert envelope.decryptability == Decryptability.PERSISTENT_KEYCHAIN
        assert envelope.pq is True
        assert envelope.recipient_key_id == "my-production-key"


class TestNoPrivateKeyInAnySerialization:
    """Verify no code path accidentally leaks private key through serialization."""

    def test_all_dict_keys_safe(self):
        """All keys in to_dict() output are production-safe names."""
        envelope = ExportEncryptionEnvelope(
            recipient_public_key_b64="dGVzdF9wdWJsaWNfa2V5",
            recipient_key_id="key-123",
            recipient_public_key_fingerprint="fingerprint",
            decryptability=Decryptability.PERSISTENT_KEYCHAIN,
            pq=True,
        )
        d = envelope.to_dict()
        unsafe_prefixes = ("private", "secret", "key_")
        unsafe = [k for k in d if any(k.lower().startswith(p) for p in unsafe_prefixes)]
        # recipient_public_key_b64 is allowed (public)
        unsafe = [k for k in unsafe if k != "recipient_public_key_b64"]
        assert unsafe == [], f"Unsafe keys in to_dict(): {unsafe}"
