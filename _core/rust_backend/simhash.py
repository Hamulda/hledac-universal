# simhash.py — SimHash domain
"""
[SAFE-3] FFI Circuit Breaker integration for simhash module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from _core._util import aclose

if TYPE_CHECKING:
    from hledac_rust_extensions import hledac_rust_extensions

# [SAFE-3] FFI Circuit Breaker
try:
    from hledac.universal._core.ffi_circuit_breaker import (
        FFI_MODULE_SIMHASH,
        get_ffi_circuit_breaker,
    )
    _FFI_CB_AVAILABLE = True
except ImportError:
    _FFI_CB_AVAILABLE = False
    FFI_MODULE_SIMHASH = "simhash"


class _RustSimhashDomain:
    """[SAFE-3] Rust SIMD domain with FFI circuit breaker."""
    __slots__ = ("_ext", "_ffi_cb")

    def __init__(self, ext: hledac_rust_extensions) -> None:
        self._ext = ext
        # [SAFE-3] Initialize FFI circuit breaker
        self._ffi_cb = get_ffi_circuit_breaker() if _FFI_CB_AVAILABLE else None

    def compute_simhash(self, text: str) -> int:
        """[SAFE-3] Compute simhash with circuit breaker."""
        if self._ffi_cb is not None:
            def rust_call() -> int:
                return self._ext.compute_simhash(text)
            result = self._ffi_cb.call_or_fallback(
                FFI_MODULE_SIMHASH, rust_call, text
            )
            if result.success:
                return result.value  # type: ignore[return-value]
            return _python_compute_simhash(text)
        return self._ext.compute_simhash(text)

    def batch_compute_simhash(self, texts: list[str]) -> list[int]:
        """[SAFE-3] Batch compute simhash with circuit breaker."""
        if self._ffi_cb is not None:
            def rust_call() -> list[int]:
                return self._ext.batch_compute_simhash(texts)
            result = self._ffi_cb.call_or_fallback(
                FFI_MODULE_SIMHASH, rust_call, texts
            )
            if result.success:
                return result.value  # type: ignore[return-value]
            return [_python_compute_simhash(t) for t in texts]
        return self._ext.batch_compute_simhash(texts)


class _PythonSimhashDomain:
    """Pure-Python simhash fallback."""

    __slots__ = ()

    @staticmethod
    def compute_simhash(text: str) -> int:
        return _python_compute_simhash(text)

    @staticmethod
    def batch_compute_simhash(texts: list[str]) -> list[int]:
        return [_python_compute_simhash(t) for t in texts]


def _python_compute_simhash(text: str) -> int:
    """
    Pure-Python SimHash approximation.

    Splits text into tokens, hashes each with MD5, accumulates
    64-bit feature vectors, and returns the final 64-bit hash.
    """
    import hashlib

    if not text:
        return 0

    tokens = text.lower().split()
    if not tokens:
        return 0

    v = [0] * 64
    for token in tokens:
        # MD5 gives 128-bit hash; take first 8 bytes as 64-bit
        h = hashlib.md5(token.encode()).digest()
        h64 = int.from_bytes(h[:8], byteorder="big")
        for i in range(64):
            bit = (h64 >> i) & 1
            v[i] += 1 if bit else -1

    result = 0
    for i in range(64):
        if v[i] > 0:
            result |= 1 << i
    return result


def get_domain(ext: object | None) -> _RustSimhashDomain | _PythonSimhashDomain:
    if ext is not None:
        return _RustSimhashDomain(ext)
    return _PythonSimhashDomain()
