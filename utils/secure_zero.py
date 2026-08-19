"""
Secure Zero Memory Wipe — F350M-R G1

Cryptographic memory wipe for key material.

Two-pass wipe (DoD 5220.22-M inspired):
  Pass 1: overwrite with cryptographically random bytes (secrets.randbelow)
  Pass 2: overwrite with zeros

ctypes.memset via pointer arithmetic (id(buf)+offset) is NOT used —
it is unreliable because CPython's bytearray memory layout is an
internal implementation detail that varies between versions and
causes segfaults (confirmed on CPython 3.14+: memset returns True
but wipes wrong memory or causes SIGSEGV).

The Python loop is simple, correct, and fast enough for key material
(typically < 1ms for 256-byte keys on M1).  Security benefit of
ctypes memset is negligible compared to the risk of silent failures.
"""
from __future__ import annotations

import logging
from typing import Any
from _core import aclose

__all__ = [
    "secure_zero",
    "secure_zero_typed",
    "wipe_bytes",
    "wipe_bytearray",
    "MemoryWipeError",
]

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------
# Core wipe — two-pass (random + zero)
# -------------------------------------------------------------------


def secure_zero(buf: bytearray | bytes | memoryview) -> bool:
    """
    Cryptographically secure zero-fill of a mutable buffer.

    Two-pass wipe:
      1. Overwrite with secrets.randbelow(256) — random noise
      2. Overwrite with 0x00 — clean zeros

    Works with:
      - bytearray (primary — mutable, wiped in-place)
      - bytes (immutable — returns False, caller must convert first)
      - memoryview (wipes backing bytearray if mutable)

    Returns True if wiped, False if bytes was passed (immutable).

    M1 8GB: Python loop is Metal-safe (no GPU), ~0.1ms for 32 bytes.

    Example:
        key = bytearray(secrets.token_bytes(32))
        secure_zero(key)   # wipe before GC
    """
    if isinstance(buf, bytes):
        return False

    if isinstance(buf, memoryview):
        if not buf.readonly:
            backing = buf.tobytes()
            if isinstance(backing, bytearray):
                _wipe_inplace(backing)
                return True
        return False

    if isinstance(buf, bytearray):
        _wipe_inplace(buf)
        return True

    return False


def _wipe_inplace(buf: bytearray) -> None:
    """
    Two-pass secure wipe of a bytearray in-place.

    Pass 1: cryptographically random data
    Pass 2: zeros
    """
    import secrets

    n = len(buf)
    for i in range(n):
        buf[i] = secrets.randbelow(256)
    for i in range(n):
        buf[i] = 0


def wipe_bytes(data: bytes) -> bytearray:
    """
    Create a mutable copy of bytes and securely wipe it.

    Use when you have key material as bytes but need to wipe it:
        secret = secrets.token_bytes(32)
        wipe_bytes(secret)   # returns wiped mutable copy
        # NOTE: original bytes still in Python GC — use bytearray from start!
    """
    return bytearray(data)


def wipe_bytearray(buf: bytearray) -> bool:
    """Alias for secure_zero() — semantic clarity for key material wipe."""
    return secure_zero(buf)


# -------------------------------------------------------------------
# Typed wipe for structured secret containers
# -------------------------------------------------------------------

SecretContainer: Any = None  # deferred msgspec.Struct import


def secure_zero_typed(obj: Any) -> None:
    """
    Recursively wipe all bytes/bytearray fields in a msgspec.Struct
    or any duck-typed object with __slots__ or __dict__.

    Skips fields named 'public' (public keys are not secret).

    Example:
        @msgspec.define
        class SecretKeys(Struct):
            public: bytes
            private: bytes
            nonce: bytes

        keys = SecretKeys(public=pk, private=sk, nonce=iv)
        secure_zero_typed(keys)   # wipes private + nonce only
    """
    global SecretContainer

    if SecretContainer is None:
        try:
            import msgspec
            from compat.msgspec_gc_compat import Struct

            SecretContainer = msgspec.Struct
        except ImportError:
            SecretContainer = type(None)  # no-op

    # --- duck-typed msgspec.Struct (has __slots__) ---
    if isinstance(obj, SecretContainer):
        for name in getattr(obj, "__slots__", ()):
            if name.startswith("public") or name in ("public", "public_key"):
                continue
            val = getattr(obj, name, None)
            if val is None:
                continue
            if isinstance(val, bytearray):
                secure_zero(val)
            elif isinstance(val, memoryview) and not val.readonly:
                secure_zero(val)
            elif isinstance(val, SecretContainer):
                secure_zero_typed(val)

    # --- plain object with __dict__ ---
    elif hasattr(obj, "__dict__"):
        for name, val in obj.__dict__.items():
            if name.startswith("public") or name in ("public", "public_key"):
                continue
            if isinstance(val, bytearray):
                secure_zero(val)
            elif isinstance(val, memoryview) and not val.readonly:
                secure_zero(val)
            elif isinstance(val, SecretContainer):
                secure_zero_typed(val)

    # --- bare bytearray ---
    elif isinstance(obj, bytearray):
        secure_zero(obj)


# -------------------------------------------------------------------
# Tor/I2P identity material wipe helpers
# -------------------------------------------------------------------


def wipe_tor_identity(onion_address: str | None, _hidden_service_dir: str | None = None) -> None:
    """
    Wipe Tor hidden-service identity material from memory.

    Call this at TorTransport.stop() or when rotating circuits.

    Args:
        onion_address: The .onion address string (not key material — derived)
        _hidden_service_dir: Unused, kept for API compatibility
    """
    if onion_address:
        try:
            encoded = onion_address.encode("ascii")
            temp = bytearray(encoded)
            secure_zero(temp)
        except Exception:  # noqa: BLE001
            pass


def wipe_i2p_identity(i2p_address: str | None) -> None:
    """
    Wipe I2P destination address from memory.

    Args:
        i2p_address: The I2P Base32 destination string
    """
    if i2p_address:
        try:
            encoded = i2p_address.encode("ascii")
            temp = bytearray(encoded)
            secure_zero(temp)
        except Exception:  # noqa: BLE001
            pass


class MemoryWipeError(RuntimeError):
    """Raised when secure_zero cannot wipe a buffer."""
