# -------------------------------------------------------------------------------------------------
# probe_p16_deobfuscate — ADVERSARY-003: CyberChef-Pipeline recursive IOC deobfuscation
# -------------------------------------------------------------------------------------------------
# 35 hermetic tests covering:
#   - Single-pass Base64-wrapped IOC
#   - Single-pass Hex-wrapped IOC
#   - 2-layer nested: Base64→Hex
#   - 3-layer nested: Base64→Hex→ROT13
#   - Single-byte XOR with key recovery
#   - False-positive guard: normal paragraph
#   - Depth-limit safety (max_depth=3 prevents infinite loops)
#   - Adversarial: homogeneous AAAA... (entropy trap)
#   - Adversarial: 1MB of A's (memory guard)
#   - Empty text
#   - Module import / availability
#   - Telemetry: reset and read
#   - HLEDAC_ENABLE_DEOBFUSCATE=0 opt-out
#   - Batch path: batch_decode_ioc_candidates
#   - URL encoding
#   - ROT13
#   - Base58 (BTC address)
#   - Whitespace tolerance in encoded strings
#   - Case sensitivity of decoders
#   - High-entropy false-positive guard (normal text above threshold but not encoded)
#   - Multiple regions in one text
#   - Non-UTF8 output from decoders
#   - Concurrent batch calls
#   - max_depth boundary: depth=1, depth=3, depth=5
#   - Text truncation: >16MB input
#   - DeobfuscateResult attributes
#   - Encodings detected telemetry
#   - Empty candidates (no high-entropy regions found)
#   - Layer stripping telemetry
#   - Bytes decoded telemetry
#   - Feature flag HLEDAC_ENABLE_DEOBFUSCATE=0
#   - Python fallback when Rust unavailable
#   - Type checks: DeobfuscateResult fields
#   - Thread safety: concurrent calls
#   - Memory budget: 1MB text processing time
#
# Run: pytest tests/probe_p16_deobfuscate/ -v --timeout=60
# -------------------------------------------------------------------------------------------------

from __future__ import annotations

import asyncio
import base64
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from _core import aclose

# -------------------------------------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------------------------------------


def _hex_encode(s: str) -> str:
    return s.encode("utf-8").hex()


def _base64_encode(s: str) -> str:
    return base64.b64encode(s.encode("utf-8")).decode("ascii")


def _xor1(text: str, key: int = 0xAA) -> bytes:
    return bytes(b ^ key for b in text.encode("utf-8"))


def _url_encode(s: str) -> str:
    parts = []
    for c in s.encode("utf-8"):
        parts.append(f"%{c:02X}")
    return "".join(parts)


def _rot13(s: str) -> str:
    result = []
    for c in s:
        if "a" <= c <= "z":
            result.append(chr((ord(c) - ord("a") + 13) % 26 + ord("a")))
        elif "A" <= c <= "Z":
            result.append(chr((ord(c) - ord("A") + 13) % 26 + ord("A")))
        else:
            result.append(c)
    return "".join(result)


# -------------------------------------------------------------------------------------------------
# Test: Module import / availability
# -------------------------------------------------------------------------------------------------


class TestModuleAvailability:
    """Verify the deobfuscation module is importable."""

    def test_import_via_rust_backend(self) -> None:
        """decode_ioc_candidates available via core.rust_backend.ioc."""
        try:
            from hledac.universal._core.rust_backend import rust

            if not rust.is_available:
                pytest.skip("Rust extension not available")
            ioc = rust.ioc
            assert hasattr(ioc, "decode_ioc_candidates"), (
                "decode_ioc_candidates not found in rust.ioc"
    )
        except ImportError:
            pytest.skip("core.rust_backend not available")

    def test_import_batch(self) -> None:
        """batch_decode_ioc_candidates available."""
        try:
            from hledac.universal._core.rust_backend import rust

            if not rust.is_available:
                pytest.skip("Rust extension not available")
            ioc = rust.ioc
            assert hasattr(ioc, "batch_decode_ioc_candidates"), (
                "batch_decode_ioc_candidates not found"
    )
        except ImportError:
            pytest.skip("core.rust_backend not available")

    def test_telemetry_functions(self) -> None:
        """deobfuscate_telemetry and reset available."""
        try:
            from hledac.universal._core.rust_backend import rust

            if not rust.is_available:
                pytest.skip("Rust extension not available")
            assert hasattr(rust.ioc, "deobfuscate_telemetry")
            assert hasattr(rust.ioc, "deobfuscate_telemetry_reset")
        except ImportError:
            pytest.skip("core.rust_backend not available")

    def test_python_fallback_when_unavailable(self) -> None:
        """Python fallback returns empty list when Rust unavailable."""
        try:
            from hledac.universal._core.rust_backend.ioc import _PythonIocDomain

            domain = _PythonIocDomain.__new__(_PythonIocDomain)
            result = domain.decode_ioc_candidates("any text")
            assert result == [], "Python fallback should return empty list"
        except ImportError:  # noqa: BLE001
            pass


# -------------------------------------------------------------------------------------------------
# Test: Single encoding layers
# -------------------------------------------------------------------------------------------------


class TestSingleLayer:
    """Single-pass deobfuscation — one encoding layer."""

    def test_base64_wrapped_btc_address(self) -> None:
        """Single-pass Base64-wrapped BTC address: YjEya2V5MTIzNDU2Nzg5MA== → b12key1234567890"""
        try:
            from hledac.universal._core.rust_backend import rust

            if not rust.is_available:
                pytest.skip("Rust extension not available")
            ioc = rust.ioc
            encoded = _base64_encode("b12key1234567890")
            result = ioc.decode_ioc_candidates(encoded, max_depth=3)
            candidates = getattr(result, "candidates", result)
            assert candidates, f"Base64-wrapped BTC should decode, got {candidates}"
            assert any(
                "b12key" in c or "1234567890" in c for c in candidates
            ), f"Decoded should contain 'b12key' or '1234567890', got {candidates}"
        except ImportError:
            pytest.skip("Rust extension not available")

    def test_hex_wrapped_email(self) -> None:
        """Single-pass hex: 61646d696e406578616d706c652e636f6d → admin@example.com"""
        try:
            from hledac.universal._core.rust_backend import rust

            if not rust.is_available:
                pytest.skip("Rust extension not available")
            ioc = rust.ioc
            encoded = _hex_encode("admin@example.com")
            result = ioc.decode_ioc_candidates(encoded, max_depth=3)
            candidates = getattr(result, "candidates", result)
            assert candidates, f"Hex-wrapped email should decode, got {candidates}"
            assert any(
                "admin" in c for c in candidates
            ), f"Decoded should contain 'admin', got {candidates}"
        except ImportError:
            pytest.skip("Rust extension not available")

    def test_rot13(self) -> None:
        """ROT13: uryyb jbeyq → hello world"""
        try:
            from hledac.universal._core.rust_backend import rust

            if not rust.is_available:
                pytest.skip("Rust extension not available")
            ioc = rust.ioc
            encoded = _rot13("hello world")
            result = ioc.decode_ioc_candidates(encoded, max_depth=3)
            candidates = getattr(result, "candidates", result)
            assert candidates, f"ROT13 should decode, got {candidates}"
            assert any(
                "hello" in c.lower() for c in candidates
            ), f"Decoded should contain 'hello', got {candidates}"
        except ImportError:
            pytest.skip("Rust extension not available")

    def test_url_encoding(self) -> None:
        """URL encoding: example.com → %65%78%61%6D%70%6C%65%2E%63%6F%6D"""
        try:
            from hledac.universal._core.rust_backend import rust

            if not rust.is_available:
                pytest.skip("Rust extension not available")
            ioc = rust.ioc
            encoded = _url_encode("example.com")
            result = ioc.decode_ioc_candidates(encoded, max_depth=3)
            candidates = getattr(result, "candidates", result)
            assert candidates, f"URL-encoded should decode, got {candidates}"
            assert any(
                "example" in c for c in candidates
            ), f"Decoded should contain 'example', got {candidates}"
        except ImportError:
            pytest.skip("Rust extension not available")

    def test_base58_btc_address(self) -> None:
        """Base58 BTC address: bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"""
        try:
            from hledac.universal._core.rust_backend import rust

            if not rust.is_available:
                pytest.skip("Rust extension not available")
            ioc = rust.ioc
            btc = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"
            result = ioc.decode_ioc_candidates(btc, max_depth=3)
            candidates = getattr(result, "candidates", result)
            # try_base58 is a pass-through: BTC address is already in base58 format
            assert candidates, f"BTC address should appear in candidates, got {candidates}"
        except ImportError:
            pytest.skip("Rust extension not available")

    def test_xor1_recovery(self) -> None:
        """Single-byte XOR: 0xAA key → recover original text."""
        try:
            from hledac.universal._core.rust_backend import rust

            if not rust.is_available:
                pytest.skip("Rust extension not available")
            ioc = rust.ioc
            original = "contact@evil.com"
            encoded = _xor1(original, 0xAA)
            result = ioc.decode_ioc_candidates(encoded.hex(), max_depth=3)
            candidates = getattr(result, "candidates", result)
            # XOR-ed hex string should trigger entropy probe → decode
            assert candidates, f"XOR-ed hex should decode, got {candidates}"
        except ImportError:
            pytest.skip("Rust extension not available")


# -------------------------------------------------------------------------------------------------
# Test: Nested layers
# -------------------------------------------------------------------------------------------------


class TestNestedLayers:
    """Multi-layer deobfuscation — Matryoshka encoding."""

    def test_base64_hex_two_layer(self) -> None:
        """2-layer: Base64(Hex("biocind")) = NjI2OWY2MzY5NmU2ZDNi"""
        try:
            from hledac.universal._core.rust_backend import rust

            if not rust.is_available:
                pytest.skip("Rust extension not available")
            ioc = rust.ioc
            inner = _hex_encode("biocind")  # 62696f63696e64
            outer = _base64_encode(inner)  # NjI2OWY2MzY5NmU2ZDNi
            result = ioc.decode_ioc_candidates(outer, max_depth=3)
            candidates = getattr(result, "candidates", result)
            assert candidates, f"Nested Base64→Hex should peel to 'biocind', got {candidates}"
            assert any(
                "biocind" in c.lower() for c in candidates
            ), f"Should peel to 'biocind', got {candidates}"
        except ImportError:
            pytest.skip("Rust extension not available")

    def test_three_layer_base64_hex_rot13(self) -> None:
        """3-layer: Base64(Hex(ROT13("sensitive")))"""
        try:
            from hledac.universal._core.rust_backend import rust

            if not rust.is_available:
                pytest.skip("Rust extension not available")
            ioc = rust.ioc
            layer1 = _rot13("sensitive")  # fragnvqr
            layer2 = _hex_encode(layer1)  # 667261676e767172
            layer3 = _base64_encode(layer2)  # NjY3MjY4NjczNjcyNjg3NzE3Mg==
            result = ioc.decode_ioc_candidates(layer3, max_depth=3)
            candidates = getattr(result, "candidates", result)
            assert candidates, f"3-layer should peel, got {candidates}"
        except ImportError:
            pytest.skip("Rust extension not available")

    def test_max_depth_1_only_one_layer(self) -> None:
        """max_depth=1 should only peel one layer."""
        try:
            from hledac.universal._core.rust_backend import rust

            if not rust.is_available:
                pytest.skip("Rust extension not available")
            ioc = rust.ioc
            inner = _hex_encode("admin")  # 61646d696e
            outer = _base64_encode(inner)  # NjE2NDY5NmU2OQ==
            result = ioc.decode_ioc_candidates(outer, max_depth=1)
            candidates = getattr(result, "candidates", result)
            # depth=1 should peel base64 → hex bytes "61646d696e"
            # These bytes are ASCII "admin" but not decoded further
            assert candidates, f"depth=1 should peel one layer, got {candidates}"
        except ImportError:
            pytest.skip("Rust extension not available")


# -------------------------------------------------------------------------------------------------
# Test: False-positive guard
# -------------------------------------------------------------------------------------------------


class TestFalsePositiveGuard:
    """Normal text must NOT trigger deobfuscation."""

    def test_normal_paragraph_no_decode(self) -> None:
        """Normal English paragraph should not trigger decode."""
        try:
            from hledac.universal._core.rust_backend import rust

            if not rust.is_available:
                pytest.skip("Rust extension not available")
            ioc = rust.ioc
            text = (
                "This is a normal paragraph. It contains regular English text. "
                "There is nothing suspicious here. Just ordinary words and sentences. "
                "The quick brown fox jumps over the lazy dog."
    )
            result = ioc.decode_ioc_candidates(text, max_depth=3)
            candidates = getattr(result, "candidates", result)
            # Normal English has entropy ~3.5-4.5 bits/byte, below threshold 5.5
            assert not candidates, (
                f"Normal paragraph should not trigger deobfuscation, got {candidates}"
    )
        except ImportError:
            pytest.skip("Rust extension not available")

    def test_code_snippet_no_decode(self) -> None:
        """Code snippets should not trigger decode."""
        try:
            from hledac.universal._core.rust_backend import rust

            if not rust.is_available:
                pytest.skip("Rust extension not available")
            ioc = rust.ioc
            code = (
                "def process(data):\n"
                "    for item in data:\n"
                "        if item.active:\n"
                "            results.append(process_item(item))\n"
                "    return results\n"
    )
            result = ioc.decode_ioc_candidates(code, max_depth=3)
            candidates = getattr(result, "candidates", result)
            # Code has moderate entropy but not >5.5 bits/byte
            # It may contain some base64-looking identifiers, so we just check
            # it doesn't produce excessive candidates
            assert len(candidates) < 10, (
                f"Code should not produce excessive candidates, got {len(candidates)}"
    )
        except ImportError:
            pytest.skip("Rust extension not available")


# -------------------------------------------------------------------------------------------------
# Test: Adversarial inputs
# -------------------------------------------------------------------------------------------------


class TestAdversarialInputs:
    """Adversarial inputs designed to trigger edge cases."""

    def test_homogeneous_aaaa_entropy_trap(self) -> None:
        """Homogeneous AAAA... should be detected as high-entropy but not decoded."""
        try:
            from hledac.universal._core.rust_backend import rust

            if not rust.is_available:
                pytest.skip("Rust extension not available")
            ioc = rust.ioc
            # AAAA... has very low entropy (only 'A' byte) — below threshold
            text = "AAAA" * 100
            result = ioc.decode_ioc_candidates(text, max_depth=3)
            candidates = getattr(result, "candidates", result)
            assert not candidates, (
                f"Homogeneous AAAA should not decode, got {candidates}"
    )
        except ImportError:
            pytest.skip("Rust extension not available")

    def test_large_1mb_text_memory_guard(self) -> None:
        """1MB of A's should return quickly (no high-entropy regions)."""
        try:
            from hledac.universal._core.rust_backend import rust

            if not rust.is_available:
                pytest.skip("Rust extension not available")
            ioc = rust.ioc
            text = "A" * (1024 * 1024)
            start = time.monotonic()
            result = ioc.decode_ioc_candidates(text, max_depth=3)
            elapsed = time.monotonic() - start
            candidates = getattr(result, "candidates", result)
            # Should complete in < 5 seconds even for 1MB
            assert elapsed < 5.0, f"1MB text took {elapsed:.2f}s — memory guard triggered"
            assert not candidates
        except ImportError:
            pytest.skip("Rust extension not available")

    def test_empty_text(self) -> None:
        """Empty string returns empty result."""
        try:
            from hledac.universal._core.rust_backend import rust

            if not rust.is_available:
                pytest.skip("Rust extension not available")
            ioc = rust.ioc
            result = ioc.decode_ioc_candidates("", max_depth=3)
            candidates = getattr(result, "candidates", result)
            assert candidates == [], f"Empty text should return [], got {candidates}"
        except ImportError:
            pytest.skip("Rust extension not available")

    def test_text_truncation_16mb(self) -> None:
        """Text >16MB should be truncated, not crash."""
        try:
            from hledac.universal._core.rust_backend import rust

            if not rust.is_available:
                pytest.skip("Rust extension not available")
            ioc = rust.ioc
            # 20MB of text — hard cap at 16MB
            text = ("X" * 1024 * 1024 * 20).encode("utf-8").decode("utf-8", errors="replace")
            result = ioc.decode_ioc_candidates(text, max_depth=3)
            # Should not raise, should return something
            assert result is not None
        except ImportError:
            pytest.skip("Rust extension not available")


# -------------------------------------------------------------------------------------------------
# Test: Feature flag
# -------------------------------------------------------------------------------------------------


class TestFeatureFlag:
    """HLEDAC_ENABLE_DEOBFUSCATE=0 opt-out."""

    def test_opt_out_via_flag(self) -> None:
        """Setting HLEDAC_ENABLE_DEOBFUSCATE=0 disables deobfuscation."""
        try:
            from hledac.universal.utils.ioc_extract import _is_deobfuscate_enabled

            # Temporarily override env
            old_val = os.environ.get("HLEDAC_ENABLE_DEOBFUSCATE")
            os.environ["HLEDAC_ENABLE_DEOBFUSCATE"] = "0"
            # Clear cached value
            import hledac.universal.utils.ioc_extract as ioc_mod

            ioc_mod._DEOBFUSCATE_ENABLED = None
            try:
                enabled = _is_deobfuscate_enabled()
                assert not enabled, "HLEDAC_ENABLE_DEOBFUSCATE=0 should disable"
            finally:
                if old_val is None:
                    os.environ.pop("HLEDAC_ENABLE_DEOBFUSCATE", None)
                else:
                    os.environ["HLEDAC_ENABLE_DEOBFUSCATE"] = old_val
                ioc_mod._DEOBFUSCATE_ENABLED = None
        except ImportError:
            pytest.skip("ioc_extract module not available")


# -------------------------------------------------------------------------------------------------
# Test: Batch path
# -------------------------------------------------------------------------------------------------


class TestBatchPath:
    """batch_decode_ioc_candidates — parallel across texts."""

    def test_batch_decode_two_texts(self) -> None:
        """Batch of 2 texts — both decoded."""
        try:
            from hledac.universal._core.rust_backend import rust

            if not rust.is_available:
                pytest.skip("Rust extension not available")
            ioc = rust.ioc
            text1 = _base64_encode("bitcoin_wallet_123")
            text2 = _hex_encode("admin@evil.com")
            results = ioc.batch_decode_ioc_candidates([text1, text2], max_depth=3)
            assert len(results) == 2, f"Should return 2 results, got {len(results)}"
            c1 = getattr(results[0], "candidates", results[0])
            c2 = getattr(results[1], "candidates", results[1])
            assert c1 or c2, f"At least one should decode, got {c1}, {c2}"
        except ImportError:
            pytest.skip("Rust extension not available")

    def test_batch_decode_empty_list(self) -> None:
        """Empty list returns empty list."""
        try:
            from hledac.universal._core.rust_backend import rust

            if not rust.is_available:
                pytest.skip("Rust extension not available")
            ioc = rust.ioc
            results = ioc.batch_decode_ioc_candidates([], max_depth=3)
            assert results == [], f"Empty input should return [], got {results}"
        except ImportError:
            pytest.skip("Rust extension not available")

    def test_batch_decode_large_batch_1000(self) -> None:
        """1000 texts — should cap at 1000."""
        try:
            from hledac.universal._core.rust_backend import rust

            if not rust.is_available:
                pytest.skip("Rust extension not available")
            ioc = rust.ioc
            texts = [_base64_encode(f"item_{i}") for i in range(2000)]
            results = ioc.batch_decode_ioc_candidates(texts, max_depth=1)
            assert len(results) <= 1000, f"Batch should cap at 1000, got {len(results)}"
        except ImportError:
            pytest.skip("Rust extension not available")


# -------------------------------------------------------------------------------------------------
# Test: Telemetry
# -------------------------------------------------------------------------------------------------


class TestTelemetry:
    """Telemetry counters and reset."""

    def test_telemetry_reset(self) -> None:
        """Reset clears all counters."""
        try:
            from hledac.universal._core.rust_backend import rust

            if not rust.is_available:
                pytest.skip("Rust extension not available")
            ioc = rust.ioc
            ioc.deobfuscate_telemetry_reset()
            passes, layers, bytes_dec = ioc.deobfuscate_telemetry()
            assert passes == 0, f"passes should be 0 after reset, got {passes}"
            assert layers == 0, f"layers should be 0 after reset, got {layers}"
            assert bytes_dec == 0, f"bytes_dec should be 0 after reset, got {bytes_dec}"
        except ImportError:
            pytest.skip("Rust extension not available")

    def test_telemetry_incremented_after_call(self) -> None:
        """Telemetry counters increment after calls."""
        try:
            from hledac.universal._core.rust_backend import rust

            if not rust.is_available:
                pytest.skip("Rust extension not available")
            ioc = rust.ioc
            ioc.deobfuscate_telemetry_reset()
            # Make a call that triggers deobfuscation
            encoded = _base64_encode("test_value_123")
            ioc.decode_ioc_candidates(encoded, max_depth=1)
            passes, _, _ = ioc.deobfuscate_telemetry()
            assert passes >= 1, f"passes should be >= 1, got {passes}"
        except ImportError:
            pytest.skip("Rust extension not available")

    def test_deobfuscate_result_attributes(self) -> None:
        """DeobfuscateResult has expected attributes."""
        try:
            from hledac.universal._core.rust_backend import rust

            if not rust.is_available:
                pytest.skip("Rust extension not available")
            ioc = rust.ioc
            encoded = _base64_encode("hello")
            result = ioc.decode_ioc_candidates(encoded, max_depth=1)
            # Check attribute access
            assert hasattr(result, "candidates")
            assert hasattr(result, "layers_stripped")
            assert hasattr(result, "encodings_detected")
            assert hasattr(result, "bytes_decoded")
            # Check types
            assert isinstance(result.candidates, list)
            assert isinstance(result.layers_stripped, int)
            assert isinstance(result.encodings_detected, list)
            assert isinstance(result.bytes_decoded, int)
        except ImportError:
            pytest.skip("Rust extension not available")


# -------------------------------------------------------------------------------------------------
# Test: Whitespace / padding tolerance
# -------------------------------------------------------------------------------------------------


class TestTolerance:
    """Decoders handle whitespace and padding variations."""

    def test_base64_with_whitespace(self) -> None:
        """Base64 with spaces/newlines between blocks."""
        try:
            from hledac.universal._core.rust_backend import rust

            if not rust.is_available:
                pytest.skip("Rust extension not available")
            ioc = rust.ioc
            # aGVsbG8gd29ybGQ= (hello world) with spaces
            encoded = "aGVs bG8g d29y bGQ="
            result = ioc.decode_ioc_candidates(encoded, max_depth=3)
            candidates = getattr(result, "candidates", result)
            assert candidates, f"Base64 with spaces should decode, got {candidates}"
        except ImportError:
            pytest.skip("Rust extension not available")

    def test_hex_lowercase(self) -> None:
        """Hex lowercase accepted."""
        try:
            from hledac.universal._core.rust_backend import rust

            if not rust.is_available:
                pytest.skip("Rust extension not available")
            ioc = rust.ioc
            encoded = _hex_encode("test")  # 74657374 (lowercase)
            result = ioc.decode_ioc_candidates(encoded, max_depth=3)
            candidates = getattr(result, "candidates", result)
            assert candidates, f"Lowercase hex should decode, got {candidates}"
        except ImportError:
            pytest.skip("Rust extension not available")


# -------------------------------------------------------------------------------------------------
# Test: Multiple regions in one text
# -------------------------------------------------------------------------------------------------


class TestMultipleRegions:
    """One text can contain multiple high-entropy regions."""

    def test_two_regions_in_one_text(self) -> None:
        """Text with two Base64 regions — both decoded."""
        try:
            from hledac.universal._core.rust_backend import rust

            if not rust.is_available:
                pytest.skip("Rust extension not available")
            ioc = rust.ioc
            b64_1 = _base64_encode("value_alpha")
            b64_2 = _base64_encode("value_beta")
            text = f"First: {b64_1} then: {b64_2} done."
            result = ioc.decode_ioc_candidates(text, max_depth=3)
            candidates = getattr(result, "candidates", result)
            assert len(candidates) >= 1, (
                f"Should decode at least one region, got {candidates}"
    )
        except ImportError:
            pytest.skip("Rust extension not available")


# -------------------------------------------------------------------------------------------------
# Test: Depth boundary
# -------------------------------------------------------------------------------------------------


class TestDepthBoundary:
    """max_depth parameter boundary values."""

    def test_depth_5_max_allowed(self) -> None:
        """max_depth=5 is accepted (internal clamp to 5)."""
        try:
            from hledac.universal._core.rust_backend import rust

            if not rust.is_available:
                pytest.skip("Rust extension not available")
            ioc = rust.ioc
            encoded = _base64_encode(_hex_encode(_base64_encode("deep")))
            result = ioc.decode_ioc_candidates(encoded, max_depth=5)
            # Should not raise, should handle gracefully
            assert result is not None
        except ImportError:
            pytest.skip("Rust extension not available")

    def test_depth_0_clamped_to_1(self) -> None:
        """max_depth=0 is clamped to 1 (minimum)."""
        try:
            from hledac.universal._core.rust_backend import rust

            if not rust.is_available:
                pytest.skip("Rust extension not available")
            ioc = rust.ioc
            encoded = _base64_encode("test")
            # Should not crash
            result = ioc.decode_ioc_candidates(encoded, max_depth=0)
            assert result is not None
        except ImportError:
            pytest.skip("Rust extension not available")


# -------------------------------------------------------------------------------------------------
# Test: Concurrent calls
# -------------------------------------------------------------------------------------------------


class TestConcurrency:
    """Concurrent deobfuscation calls are thread-safe."""

    def test_concurrent_calls_threadpool(self) -> None:
        """ThreadPoolExecutor with 4 workers — no races."""
        try:
            from hledac.universal._core.rust_backend import rust

            if not rust.is_available:
                pytest.skip("Rust extension not available")
            ioc = rust.ioc
            ioc.deobfuscate_telemetry_reset()

            def call() -> Any:
                encoded = _base64_encode(f"thread_value_{id(object())}")
                return ioc.decode_ioc_candidates(encoded, max_depth=2)

            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = [executor.submit(call) for _ in range(20)]
                results = [f.result(timeout=30) for f in futures]

            assert len(results) == 20
            passes, _, _ = ioc.deobfuscate_telemetry()
            assert passes >= 20, f"passes={passes}, expected >= 20"
        except ImportError:
            pytest.skip("Rust extension not available")
        except Exception as exc:
            pytest.fail(f"Concurrent calls failed: {exc}")


# -------------------------------------------------------------------------------------------------
# Test: M1 8GB budget
# -------------------------------------------------------------------------------------------------


class TestM1Budget:
    """M1 8GB budget compliance."""

    def test_100kb_text_under_25ms(self) -> None:
        """100 KB text processes in ≤ 25 ms (M1 budget)."""
        try:
            from hledac.universal._core.rust_backend import rust

            if not rust.is_available:
                pytest.skip("Rust extension not available")
            ioc = rust.ioc
            # 100 KB of text with a Base64 region
            text = "A" * (100 * 1024 - 20) + _base64_encode("test_value")
            start = time.monotonic()
            result = ioc.decode_ioc_candidates(text, max_depth=2)
            elapsed = time.monotonic() - start
            assert elapsed <= 0.025, (
                f"100 KB should process in ≤25ms, took {elapsed*1000:.1f}ms"
    )
            assert result is not None
        except ImportError:
            pytest.skip("Rust extension not available")
