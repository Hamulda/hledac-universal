"""Type stubs for `hledac_rust_extensions.content_hasher`.

Mirrors the public surface of `content_hasher.rs` for type-checkers
and IDEs. The actual implementation is in the compiled extension;
this stub only describes the Python-facing contract.

Verze: synchronizes s `content_hasher.rs` 0.1.0+content-hasher-0.1
"""

__version__: str

class ContentHasher:
    """Stateless namespace for content hashing primitives.

    No instantiation — all methods are static. Used by:
    - `public_fetcher._extract_tls_metadata_from_response` — TLS cert
      SHA-256 (drop-in for `hashlib.sha256`).
    - `public_fetcher._compute_body_hash` — response body BLAKE3-64
      fingerprint for cross-URL dedup.

    BLAKE3 path uses ARM NEON SIMD on aarch64 (Apple Silicon M1+)
    for ~3-5x throughput vs scalar fallback. SHA-256 path uses the
    portable `sha2` crate (no platform-specific intrinsics).
    """

    @staticmethod
    def sha256_hex(data: bytes) -> str:
        """Compute SHA-256 and return as 64-char lowercase hex.

        Drop-in replacement for `hashlib.sha256(data).hexdigest()`.

        Args:
            data: Byte slice (e.g. DER-encoded TLS cert).

        Returns:
            64-character lowercase hex digest.
        """
        ...

    @staticmethod
    def blake3_64(body: bytes) -> str:
        """Compute 64-bit BLAKE3 fingerprint as 16-char hex.

        Truncates the 256-bit BLAKE3 output to its first 8 bytes
        (uniformly distributed per BLAKE3 spec). Used as a fast
        body-hash key for RotatingBloomFilter dedup and LMDB
        metadata values.

        Args:
            body: Response body bytes.

        Returns:
            16-character lowercase hex fingerprint.
        """
        ...

    @staticmethod
    def blake3_hex(body: bytes) -> str:
        """Compute full 256-bit BLAKE3 hash as 64-char hex.

        Used for content-aware dedup where 64-bit collision risk is
        unacceptable (e.g. evidence chain, long-tail archival).

        Args:
            body: Response body bytes.

        Returns:
            64-character lowercase hex digest.
        """
        ...

    @staticmethod
    def batch_blake3_64(bodies: list[bytes]) -> list[str]:
        """Parallel BLAKE3-64 over many bodies via rayon.

        On M1 (8-core) with NEON, expect ~5 GB/s aggregate throughput.
        Used to backfill body hashes after a bulk fetch.

        Args:
            bodies: List of response body byte slices.

        Returns:
            List of 16-character hex strings, same length as `bodies`.
        """
        ...
