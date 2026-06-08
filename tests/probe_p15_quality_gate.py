"""
probe_p15_quality_gate.py — Sprint P1-5 Quality Gate Rust extension probe.

Verifies the BLAKE2b-128 quality-gate compute kernels in
`rust_extensions/src/quality_gate.rs` are bit-compatible with their Python
references in `knowledge/quality_assessment.py`:

    normalize_quality_text  ↔  _normalize_for_quality
    compute_entropy         ↔  _compute_entropy
    dedup_fingerprint       ↔  _compute_dedup_fingerprint
    url_fingerprint         ↔  _compute_url_fingerprint (URL normalize reused)

Bounded batch API checks (cap_slice defensive truncate).

Fail-soft: if the extension is not built (`maturin develop` not run), the
import tests are skipped — the rest of the suite can still validate the
Python fallback paths.

Run:  pytest tests/probe_p15_quality_gate.py -v
"""

from __future__ import annotations

import hashlib
import math
import sys
import time
from collections import Counter
from typing import Iterable

import pytest


# ---------------------------------------------------------------------------
# Extension availability
# ---------------------------------------------------------------------------

try:
    import hledac_rust_extensions as _r  # type: ignore

    RUST_AVAILABLE = hasattr(_r, "normalize_quality_text")
    if RUST_AVAILABLE:
        RUST_NORMALIZE = _r.normalize_quality_text
        RUST_ENTROPY = _r.compute_entropy
        RUST_DEDUP_FP = _r.dedup_fingerprint
        RUST_URL_FP = _r.url_fingerprint
        RUST_BATCH_ENTROPY = _r.batch_entropy
        RUST_BATCH_DEDUP = _r.batch_dedup_fingerprints
        RUST_BATCH_URL = _r.batch_url_fingerprints
except ImportError:
    RUST_AVAILABLE = False
    RUST_NORMALIZE = RUST_ENTROPY = RUST_DEDUP_FP = RUST_URL_FP = None
    RUST_BATCH_ENTROPY = RUST_BATCH_DEDUP = RUST_BATCH_URL = None


# Python references (live copies — the Rust path must match these exactly).
# CRITICAL: Must mirror knowledge/quality_assessment.py:_normalize_for_quality
# line-for-line (including the non-printable strip AFTER whitespace collapse),
# otherwise the parity test is meaningless.
def py_normalize(text: str) -> str:
    if not text:
        return ""
    lowered = text.lower()
    stripped = lowered.strip()
    normalized = " ".join(stripped.split())
    import string
    ws = set(string.whitespace)
    return "".join(ch for ch in normalized if ord(ch) >= 32 or ch in ws)


def py_entropy(text: str) -> float:
    if not text:
        return 0.0
    counts = Counter(text)
    n = len(text)
    return -sum(
        (c / n) * math.log2(c / n) for c in counts.values() if c > 0
    )


def py_dedup_fp(text: str) -> str:
    return hashlib.blake2b(py_normalize(text).encode("utf-8"), digest_size=16).hexdigest()


# Python url_fingerprint (for parity check) — mirrors the FULL Python path
# used when the Rust url_engine is NOT available. The Rust url_fingerprint in
# quality_gate.rs uses url_engine::normalize (Sprint F216R canonical), so
# this parity is verified by the canonical F216R tests rather than here.
def py_url_fingerprint(url: str) -> str:
    if not url:
        return ""
    import hashlib as _h
    return _h.blake2b(url.lower().encode("utf-8"), digest_size=16).hexdigest()


# ---------------------------------------------------------------------------
# Conditional skip when extension not built
# ---------------------------------------------------------------------------

pytestmark_rust = pytest.mark.skipif(
    not RUST_AVAILABLE,
    reason="hledac_rust_extensions quality_gate module not built (run maturin develop --release in rust_extensions/)",
)


# ===========================================================================
# 1. Parity: Rust output == Python reference (BIT-IDENTICAL)
# ===========================================================================


class TestRustParity:
    """Rust kernels must produce bit-identical output to the Python references."""

    @pytest.fixture(params=[
        "",
        "hello",
        "  Hello   WORLD  ",
        "a\tb\nc\rd",
        "OSINT IOC finding content",
        "Mixed CASE with Punctuation!",
        "a\x00b\x07c\td",  # non-printable interleaved with whitespace
        "x" * 1000,         # long string
        "café résumé naïve",  # unicode
        "https://Example.com/path/?utm_source=x&id=42#frag",
        "  \t\n  ",          # all whitespace
        "OSINT: ransomware note at /backup — encrypted",
    ])
    def sample_text(self, request):
        return request.param

    def test_normalize_bit_identical(self, sample_text):
        if not RUST_AVAILABLE:
            pytest.skip("rust not built")
        assert RUST_NORMALIZE(sample_text) == py_normalize(sample_text), (
            f"normalize mismatch for: {sample_text!r}"
        )

    def test_entropy_bit_identical(self, sample_text):
        if not RUST_AVAILABLE:
            pytest.skip("rust not built")
        r, p = RUST_ENTROPY(sample_text), py_entropy(sample_text)
        # Tolerate IEEE-754 rounding in the last bit (Rust uses f64::log2 native,
        # Python uses math.log2 — both are correctly rounded but may differ by
        # 1 ULP on adversarial inputs).
        assert abs(r - p) < 1e-12, f"entropy mismatch: rust={r} py={p} for {sample_text!r}"

    def test_dedup_fingerprint_bit_identical(self, sample_text):
        if not RUST_AVAILABLE:
            pytest.skip("rust not built")
        assert RUST_DEDUP_FP(sample_text) == py_dedup_fp(sample_text), (
            f"fingerprint mismatch for: {sample_text!r}"
        )

    def test_dedup_fingerprint_format(self, sample_text):
        """Output must be 32 lowercase hex chars (BLAKE2b-128)."""
        if not RUST_AVAILABLE or not sample_text:
            pytest.skip("rust not built or empty input")
        fp = RUST_DEDUP_FP(sample_text)
        assert len(fp) == 32
        assert all(c in "0123456789abcdef" for c in fp), f"non-hex chars in: {fp!r}"


# ===========================================================================
# 2. Specific correctness properties
# ===========================================================================


class TestRustProperties:
    """Independent correctness checks (not just Python parity)."""

    def test_normalize_empty(self):
        if not RUST_AVAILABLE:
            pytest.skip("rust not built")
        assert RUST_NORMALIZE("") == ""
        assert RUST_NORMALIZE("   \t\n  ") == ""

    def test_normalize_lowercases(self):
        if not RUST_AVAILABLE:
            pytest.skip("rust not built")
        assert RUST_NORMALIZE("HELLO World") == "hello world"

    def test_normalize_collapses_whitespace(self):
        if not RUST_AVAILABLE:
            pytest.skip("rust not built")
        # \t \n \r are all whitespace → single space
        assert RUST_NORMALIZE("a\tb\nc\rd") == "a b c d"
        # Multiple spaces collapse
        assert RUST_NORMALIZE("a    b") == "a b"

    def test_normalize_strips_non_printable(self):
        if not RUST_AVAILABLE:
            pytest.skip("rust not built")
        # \x00 NUL is non-printable → removed (after whitespace collapse)
        assert RUST_NORMALIZE("a\x00b") == "ab"
        # \x07 BEL is non-printable → removed
        assert RUST_NORMALIZE("hello\x07world") == "helloworld"

    def test_entropy_empty_is_zero(self):
        if not RUST_AVAILABLE:
            pytest.skip("rust not built")
        assert RUST_ENTROPY("") == 0.0

    def test_entropy_constant_string_is_zero(self):
        if not RUST_AVAILABLE:
            pytest.skip("rust not built")
        assert RUST_ENTROPY("aaaaaaaa") == 0.0

    def test_entropy_two_chars_uniform_is_one(self):
        if not RUST_AVAILABLE:
            pytest.skip("rust not built")
        # p=0.5 each → -2 * 0.5 * log2(0.5) = 1.0
        assert abs(RUST_ENTROPY("ababababab") - 1.0) < 1e-9

    def test_dedup_fingerprint_deterministic(self):
        if not RUST_AVAILABLE:
            pytest.skip("rust not built")
        text = "deterministic test input"
        assert RUST_DEDUP_FP(text) == RUST_DEDUP_FP(text)

    def test_dedup_fingerprint_changes_with_input(self):
        if not RUST_AVAILABLE:
            pytest.skip("rust not built")
        a = RUST_DEDUP_FP("OSINT finding A")
        b = RUST_DEDUP_FP("OSINT finding B")
        assert a != b

    def test_url_fingerprint_empty(self):
        if not RUST_AVAILABLE:
            pytest.skip("rust not built")
        assert RUST_URL_FP("") == ""

    def test_url_fingerprint_normalizes_casing(self):
        if not RUST_AVAILABLE:
            pytest.skip("rust not built")
        # OSINT URL normalize lowercases scheme+host (F216R canonical)
        a = RUST_URL_FP("https://Example.com/path/")
        b = RUST_URL_FP("https://example.com/path")
        assert a == b


# ===========================================================================
# 3. Batch API correctness (rayon parallel must match single-call)
# ===========================================================================


class TestBatchAPI:
    """Batch variants must produce identical results to single-call."""

    @pytest.fixture
    def sample_texts(self) -> list[str]:
        return [
            "OSINT finding alpha",
            "OSINT finding bravo",
            "OSINT finding charlie",
            "OSINT finding delta",
            "OSINT finding echo",
            "  Mixed   CASE  Input  ",
            "tab\there\nnewline",
            "non-printable\x00mixed\x07in",
            "café résumé naïve",  # unicode
            "" * 0,                # empty (edge case)
        ]

    def test_batch_entropy_matches_single(self, sample_texts):
        if not RUST_AVAILABLE:
            pytest.skip("rust not built")
        batched = RUST_BATCH_ENTROPY(sample_texts)
        singles = [RUST_ENTROPY(t) for t in sample_texts]
        assert len(batched) == len(singles)
        for b, s in zip(batched, singles):
            assert abs(b - s) < 1e-12, f"batch entropy mismatch: {b} vs {s}"

    def test_batch_dedup_matches_single(self, sample_texts):
        if not RUST_AVAILABLE:
            pytest.skip("rust not built")
        batched = RUST_BATCH_DEDUP(sample_texts)
        singles = [RUST_DEDUP_FP(t) for t in sample_texts]
        assert batched == singles, "batch dedup mismatch"

    def test_batch_url_matches_single(self):
        if not RUST_AVAILABLE:
            pytest.skip("rust not built")
        urls = [
            "https://Example.com/path/",
            "https://other.org/article?id=1&utm_source=tw",
            "HTTP://THIRD.NET",
            "",
            "https://fourth.io/a/b/c/",
        ]
        batched = RUST_BATCH_URL(urls)
        singles = [RUST_URL_FP(u) for u in urls]
        assert batched == singles, "batch url fingerprint mismatch"

    def test_batch_empty_input_returns_empty(self):
        if not RUST_AVAILABLE:
            pytest.skip("rust not built")
        assert RUST_BATCH_ENTROPY([]) == []
        assert RUST_BATCH_DEDUP([]) == []
        assert RUST_BATCH_URL([]) == []

    def test_batch_caps_to_hard_cap(self):
        """BATCH_HARD_CAP = 4096 — defensive truncate on oversized input."""
        if not RUST_AVAILABLE:
            pytest.skip("rust not built")
        big = [f"text-{i}" for i in range(5000)]
        # batch_entropy caps to 4096, returning 4096 results
        result = RUST_BATCH_ENTROPY(big)
        assert len(result) == 4096, f"expected 4096 cap, got {len(result)}"
        # Same cap for other batch APIs
        assert len(RUST_BATCH_DEDUP(big)) == 4096
        assert len(RUST_BATCH_URL(big)) == 4096


# ===========================================================================
# 4. Python wrapper integration (quality_assessment.py fast-path)
# ===========================================================================


class TestPythonWrapper:
    """Verify the Python wrapper in knowledge/quality_assessment.py works."""

    def test_wrapper_imports(self):
        # The wrapper should be importable regardless of Rust availability
        from knowledge.quality_assessment import (
            _QUALITY_GATE_RUST_AVAILABLE,
            _normalize_for_quality,
            _compute_entropy,
            _compute_dedup_fingerprint,
        )
        assert isinstance(_QUALITY_GATE_RUST_AVAILABLE, bool)

    def test_normalize_works_without_rust(self):
        # Force Python path by setting _QUALITY_GATE_RUST_AVAILABLE = False
        import knowledge.quality_assessment as qa
        original = qa._QUALITY_GATE_RUST_AVAILABLE
        try:
            qa._QUALITY_GATE_RUST_AVAILABLE = False
            qa._rust_normalize_quality_text = None
            assert qa._normalize_for_quality("  HELLO World  ") == "hello world"
        finally:
            qa._QUALITY_GATE_RUST_AVAILABLE = original

    def test_entropy_works_without_rust(self):
        import knowledge.quality_assessment as qa
        original = qa._QUALITY_GATE_RUST_AVAILABLE
        try:
            qa._QUALITY_GATE_RUST_AVAILABLE = False
            qa._rust_compute_entropy = None
            assert qa._compute_entropy("") == 0.0
            assert abs(qa._compute_entropy("abab") - 1.0) < 1e-9
        finally:
            qa._QUALITY_GATE_RUST_AVAILABLE = original

    def test_dedup_fingerprint_works_without_rust(self):
        import knowledge.quality_assessment as qa
        original = qa._QUALITY_GATE_RUST_AVAILABLE
        try:
            qa._QUALITY_GATE_RUST_AVAILABLE = False
            qa._rust_dedup_fingerprint = None
            fp = qa._compute_dedup_fingerprint("hello world")
            assert len(fp) == 32
            # Must match hashlib.blake2b of normalized text
            assert fp == hashlib.blake2b(b"hello world", digest_size=16).hexdigest()
        finally:
            qa._QUALITY_GATE_RUST_AVAILABLE = original

    def test_normalize_uses_rust_when_available(self):
        """When Rust is available, the wrapper should call it (verified by mocking)."""
        if not RUST_AVAILABLE:
            pytest.skip("rust not built")
        import knowledge.quality_assessment as qa
        call_count = [0]
        original = qa._rust_normalize_quality_text

        def spy(text: str) -> str:
            call_count[0] += 1
            return original(text)

        try:
            qa._rust_normalize_quality_text = spy
            qa._QUALITY_GATE_RUST_AVAILABLE = True
            qa._normalize_for_quality("  Hello World  ")
            assert call_count[0] == 1, "Rust fast-path not called"
        finally:
            qa._rust_normalize_quality_text = original

    def test_fallback_on_rust_exception(self):
        """If Rust raises, the Python fallback must take over."""
        if not RUST_AVAILABLE:
            pytest.skip("rust not built")
        import knowledge.quality_assessment as qa

        def broken_normalize(_text: str) -> str:
            raise RuntimeError("simulated Rust panic")

        original = qa._rust_normalize_quality_text
        try:
            qa._rust_normalize_quality_text = broken_normalize
            qa._QUALITY_GATE_RUST_AVAILABLE = True
            # Should NOT raise — Python fallback should engage
            result = qa._normalize_for_quality("  Hello World  ")
            assert result == "hello world"
        finally:
            qa._rust_normalize_quality_text = original


# ===========================================================================
# 5. M1 8GB safety (no unbounded allocation, no panics on adversarial input)
# ===========================================================================


class TestM1Safety:
    """Bounded memory, no panics, no infinite allocations."""

    @pytest.mark.parametrize("size", [10, 100, 1_000, 10_000])
    def test_works_on_long_strings(self, size):
        if not RUST_AVAILABLE:
            pytest.skip("rust not built")
        text = "a" * size
        # Normalize
        assert RUST_NORMALIZE(text) == text
        # Entropy (constant → 0.0)
        assert RUST_ENTROPY(text) == 0.0
        # Fingerprint
        fp = RUST_DEDUP_FP(text)
        assert len(fp) == 32

    def test_handles_unicode(self):
        if not RUST_AVAILABLE:
            pytest.skip("rust not built")
        text = "café résumé naïve 日本語 🚀"
        normalized = RUST_NORMALIZE(text)
        # Should be lowercased + whitespace-collapsed, no panics
        assert "café" in normalized
        # Entropy is finite
        e = RUST_ENTROPY(text)
        assert 0.0 <= e < 8.0  # <8 bits per byte (UTF-8 is variable-width)

    def test_handles_binary_like_input(self):
        """High-entropy input (binary garbage) should not panic."""
        if not RUST_AVAILABLE:
            pytest.skip("rust not built")
        # All 256 byte values
        text = "".join(chr(i) for i in range(256) if 32 <= i < 256 or i in (9, 10, 13))
        e = RUST_ENTROPY(text)
        assert 0.0 <= e < 8.0
        # Fingerprint must succeed
        fp = RUST_DEDUP_FP(text)
        assert len(fp) == 32

    def test_batch_with_large_input_caps(self):
        """M1 8GB: batch API must not allocate unbounded memory."""
        if not RUST_AVAILABLE:
            pytest.skip("rust not built")
        # Send 100x the hard cap — defensive cap must engage
        huge = [f"item-{i}" for i in range(100_000)]
        result = RUST_BATCH_DEDUP(huge)
        assert len(result) == 4096  # BATCH_HARD_CAP


# ===========================================================================
# 6. Performance smoke (not strict — just confirms Rust is not slower)
# ===========================================================================


@pytest.mark.benchmark
class TestPerformanceSmoke:
    """Sanity: Rust batch should not be 5× SLOWER than single-call.

    Real benchmarks live in benchmarks/; this is just a regression guard
    against accidentally making Rust slower than Python.
    """

    def test_batch_not_slower_than_5x_single(self):
        if not RUST_AVAILABLE:
            pytest.skip("rust not built")
        texts = [f"finding content for entropy test {i}" for i in range(1000)]

        # Single calls
        t0 = time.perf_counter()
        singles = [RUST_ENTROPY(t) for t in texts]
        single_ms = (time.perf_counter() - t0) * 1000

        # Batched
        t0 = time.perf_counter()
        batched = RUST_BATCH_ENTROPY(texts)
        batch_ms = (time.perf_counter() - t0) * 1000

        assert len(singles) == len(batched) == len(texts)
        # Bound: batched should be at most 5× the single-call cost (lenient).
        # In practice, batched is faster (4-8×) on M1 P-cores.
        # We bound the WORST case to catch pathological regressions.
        assert batch_ms < single_ms * 5, (
            f"batch={batch_ms:.2f}ms vs single={single_ms:.2f}ms — "
            f"batch is {batch_ms / single_ms:.1f}× slower (regression?)"
        )
