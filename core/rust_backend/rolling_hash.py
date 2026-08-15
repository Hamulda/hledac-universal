# rolling_hash.py — Rolling hash domain

from typing import TYPE_CHECKING, Any
from core._util import aclose



if TYPE_CHECKING:
    from hledac_rust_extensions import hledac_rust_extensions


class _RustRollingHashDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: hledac_rust_extensions) -> None:
        self._ext = ext

    def RollingHashEngine(self, base: int = 257) -> Any:
        return self._ext.RollingHashEngine(base)


class _PythonRollingHashDomain:
    """Pure-Python rolling hash fallback."""

    __slots__ = ()

    def RollingHashEngine(self, base: int = 257) -> _PythonRollingHashEngine:
        return _PythonRollingHashEngine(base)


def get_domain(ext: object | None) -> _RustRollingHashDomain | _PythonRollingHashDomain:
    if ext is not None:
        return _RustRollingHashDomain(ext)
    return _PythonRollingHashDomain()


# ------------------------------------------------------------------
# Pure-Python rolling hash (moved from top of rust_backend.py)
# ------------------------------------------------------------------


class _PythonRollingHashEngine:
    """Pure-Python rolling hash engine fallback."""

    __slots__ = ("_base", "_modulus", "_window_size")

    def __init__(self, base: int = 257, modulus: int = 1_000_000_007, window_size: int = 8) -> None:
        self._base = base
        self._modulus = modulus
        self._window_size = window_size

    def _compute_power(self, window_size: int) -> int:
        return pow(self._base, window_size, self._modulus)

    def hash(self, data: bytes) -> int:
        h = 0
        for i, b in enumerate(data[: self._window_size]):
            h = (h * self._base + b) % self._modulus
        return h

    def roll(self, old_hash: int, old_char: int, new_char: int, window_size: int) -> int:
        power = self._compute_power(window_size)
        h = (old_hash - old_char * power) % self._modulus
        h = (h * self._base + new_char) % self._modulus
        return h

    def hashes(self, data: bytes, window_size: int = 8) -> list[int]:
        if len(data) < window_size:
            return []
        results = []
        current = self.hash(data[:window_size])
        results.append(current)
        for i in range(window_size, len(data)):
            current = self.roll(current, data[i - window_size], data[i], window_size)
            results.append(current)
        return results
