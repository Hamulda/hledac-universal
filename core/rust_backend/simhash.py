# simhash.py — SimHash domain
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hledac_rust_extensions import hledac_rust_extensions


class _RustSimhashDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: hledac_rust_extensions) -> None:
        self._ext = ext

    def compute_simhash(self, text: str) -> int:
        return self._ext.compute_simhash(text)

    def batch_compute_simhash(self, texts: list[str]) -> list[int]:
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
