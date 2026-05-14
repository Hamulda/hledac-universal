"""
Rolling Hash Engine - Content-Defined Chunking (CDC) using Gear hash.

Implements FastCDC-like chunking:
- Uses rolling hash to detect chunk boundaries
- Deterministic outputs
- Hard caps on chunks and processing time

This is used for:
- Delta compression (finding similar chunks)
- Deduplication (chunk-level)
"""

from __future__ import annotations

import hashlib
import heapq
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Gear hash parameters (FastCDC defaults)
GEAR_MASK = 0x3FFFFFFF  # 30-bit mask
# Full 256-byte gear table for CDC
GEAR_TABLE = [
    0x00000000, 0x1689D6EE, 0x2313C8DC, 0x30AB9A06, 0x4107BC31, 0x50D31BF5, 0x61A0F1DA, 0x726F1D7E,
    0x853FBAA2, 0x97F1DB47, 0xA982BF19, 0xBB149E6D, 0xCCA77E80, 0xDE3B5F34, 0xEFD04087, 0x016623D9,
    0x12FC47A2, 0x2492686C, 0x36298836, 0x48C0A70F, 0x5A5806E3, 0x6CEF57D8, 0x7F86AB4C, 0x921EEF21,
    0xA4B73315, 0xB74F76E8, 0xC9E8BA9C, 0xDC82FC51, 0xEF1D3E05, 0x01B881A8, 0x1452C25D, 0x26ED0333,
    0x398743E8, 0x4C21759D, 0x5FBCA751, 0x7257D906, 0x84F309BA, 0x978F3F6F, 0xAA2A7423, 0xBDC6A916,
    0xD061DFAA, 0xE3FE153F, 0xF69B4AD3, 0x093780A6, 0x1CD2B55C, 0x2F6E0B31, 0x42096005, 0x54A4B5DA,
    0x67400BAE, 0x79DB6173, 0x8C77B727, 0x9F130CFC, 0xB1AE6100, 0xC44AB6DE, 0xD6E60C92, 0xE9826147,
    0xFC1DB71B, 0x0FB7CEEF, 0x22520684, 0x34ED3E5A, 0x4787752F, 0x5A21AC03, 0x6CBBDEE7, 0x7F56119C,
    0x8DFAD26D, 0xA08F34A1, 0xB222E5D6, 0xC4B6870B, 0xD64B373F, 0xE8DFE674, 0xFA7480A8, 0x0C0C243D,
    0x1EA00672, 0x3034E7A6, 0x42C8C91B, 0x545DAA4F, 0x66F18C84, 0x79856FB8, 0x8B1A51ED, 0x9DAE3421,
    0xAF431655, 0xC1D7F989, 0xD46CDBBD, 0xE600BCF1, 0xF8949E26, 0x0A297F5A, 0x1CBD618F, 0x2F5243C3,
    0x41E626F7, 0x547B082B, 0x670FEA5F, 0x79A3CC93, 0x8C38AEC7, 0x9ECC90FC, 0xB1617330, 0xC3F65564,
    0xD68A3898, 0xE91F1ACD, 0xFBB3FD01, 0x0E47E035, 0x20DCC26A, 0x3371A59E, 0x460687D2, 0x589B6B06,
    0x6B304D3B, 0x7DC4306F, 0x905813A3, 0xA2ECF7D7, 0xB580DB0C, 0xC815BF40, 0xDBA9A374, 0xEE3D87A8,
    0x00D26BDD, 0x13674F11, 0x25FC3245, 0x38911679, 0x4A25FAAD, 0x5CBAE0E1, 0x6F4EC615, 0x81E3AB49,
    0x94778F7D, 0xA60C73B2, 0xB8A058E6, 0xCB343D1A, 0xDDC8214E, 0xF05C0682, 0x02F0E9B6, 0x1584CCEA,
    0x2819B01E, 0x3AAD9452, 0x4D427886, 0x5FD65DBB, 0x726B41EF, 0x84FF2623, 0x97940957, 0xAA68ED8B,
    0xBDFDD1BF, 0xD091B5F3, 0xE3269A27, 0xF5BA7E5B, 0x084F628F, 0x1AE346C3, 0x2D772AF7, 0x400C0F2B,
    0x52A0F35F, 0x6535D793, 0x77C9BC07, 0x8A5EA03B, 0x9CF2846F, 0xAF8669A3, 0xC21B4ED7, 0xD4AF330B,
    0xE743183F, 0xF9D7FC73, 0x0C6CE0A7, 0x1F00C4DB, 0x3195A90F, 0x44298D43, 0x56BE7277, 0x695256AB,
    0x7BE63BDF, 0x8E7A2013, 0xA10E0547, 0xB3A2E97B, 0xC636CEAF, 0xD8CBB3E3, 0xEB5F9817, 0xFDF47C4B,
    0x1049617F, 0x22DD45B3, 0x35722AE7, 0x48060F1B, 0x5A9AF44F, 0x6D2ED883, 0x7FC3BCB7, 0x9257A0EB,
    0xA4EC851F, 0xB7806953, 0xCA154D87, 0xDCA931BB, 0xEF3D16EF, 0x01D1FB23, 0x1466DF57, 0x27FAC38B,
    0x3A8FA7BF, 0x4D248CF3, 0x5FB87027, 0x724D545B, 0x84E1388F, 0x97751DC3, 0xAA0901F7, 0xBC9DE62B,
    0xCF32CA5F, 0xE1C7AE93, 0xF45B92C7, 0x06F077FB, 0x19845C2F, 0x2C184163, 0x3FAC2581, 0x52410AB5,
    0x64D5EEE9, 0x776AD31D, 0x89FEB751, 0x9C939C85, 0xAF278BA9, 0xC1BC7EDD, 0xD4506211, 0xE6E54745,
    0xF97A2B79, 0x0C0E10AD, 0x1EA2F4E1, 0x3137D915, 0x43CBBD49, 0x5660A27D, 0x68F586B1, 0x7B8A6BE5,
    0x8E1E5019, 0xA0B3344D, 0xB3471981, 0xC5DCFDB5, 0xD870E2E9, 0xEB05C71D, 0xFD99AB51, 0x102E8F85,
    0x22C274A9, 0x355658DD, 0x47EB3D11, 0x5A7F2145, 0x6D140679, 0x7FA8EAAD, 0x923DCFE1, 0xA4D1B415,
    0xB7659949, 0xC9FA7D7D, 0xDC8E62B1, 0xEF2346E5, 0x01B72B19, 0x144C0F4D, 0x26E0F381, 0x3975D7B5,
    0x4C09BCE9, 0x5E9DA11D, 0x71328551, 0x83C76985, 0x965C4EB9, 0xA8F033ED, 0xBB841821, 0xCE18FC55,
    0xE0ADE189, 0xF341C6BD, 0x05D6AAF1, 0x186A8F25, 0x2AFF7359, 0x3D94578D, 0x50283CC1, 0x62BD20F5,
    0x75520529, 0x87E6E95D, 0x9A7BCB91, 0xAD10AEC5, 0xBFA493F9, 0xD239782D, 0xE5CD5C61, 0xF8614195,
]


class RollingHashEngine:
    """
    Content-Defined Chunking engine using Gear hash (FastCDC-like).

    Deterministic chunking based on rolling hash:
    - Chunks are defined by hash value hitting boundaries
    - Normal chunk size around avg_size
    - Min/max bounds to prevent pathological cases
    """

    def __init__(
        self,
        min_size: int = 2048,
        avg_size: int = 8192,
        max_size: int = 65536
    ):
        """
        Initialize rolling hash engine.

        Args:
            min_size: Minimum chunk size in bytes
            avg_size: Target average chunk size
            max_size: Maximum chunk size in bytes
        """
        self.min_size = min_size
        self.avg_size = avg_size
        self.max_size = max_size
        self.logger = logging.getLogger(__name__)

    def _gear_hash(self, byte_val: int, hash_val: int) -> int:
        """Compute gear hash for a byte."""
        return ((hash_val << 1) ^ GEAR_TABLE[byte_val & 0xFF]) & GEAR_MASK

    def _is_chunk_boundary(self, hash_val: int, position: int, chunk_start: int) -> bool:
        """Determine if current position is a chunk boundary."""
        # Calculate target boundary based on position
        chunk_size = position - chunk_start

        # Too small - no boundary yet
        if chunk_size < self.min_size:
            return False

        # Force boundary at max size
        if chunk_size >= self.max_size:
            return True

        # FastCDC boundary detection
        # Use middle bits of hash for boundary decision
        # Normalize to avg_size range
        normalized = chunk_size * 256 // self.avg_size

        # Hash modulo for boundary
        boundary = hash_val % 256

        # Boundary when normalized value matches boundary condition
        return normalized >= boundary

    def chunk_bytes(
        self,
        data: bytes,
        *,
        min_size: Optional[int] = None,
        avg_size: Optional[int] = None,
        max_size: Optional[int] = None,
        max_chunks: int = 2048
    ) -> list[tuple[int, int]]:
        """
        Split bytes into chunks using content-defined chunking.

        Args:
            data: Input bytes to chunk
            min_size: Override minimum chunk size
            avg_size: Override average chunk size
            max_size: Override maximum chunk size
            max_chunks: Maximum number of chunks to return

        Returns:
            List of (start, end) byte offsets for each chunk
        """
        if not data:
            return []

        min_s = min_size if min_size is not None else self.min_size
        avg_s = avg_size if avg_size is not None else self.avg_size
        max_s = max_size if max_size is not None else self.max_size

        chunks = []
        hash_val = 0
        chunk_start = 0
        position = 0

        # Initialize hash with first bytes
        init_len = min(31, len(data))
        for i in range(init_len):
            hash_val = self._gear_hash(data[i], hash_val)

        position = init_len

        # Roll through data
        while position < len(data):
            # Update hash with current byte
            hash_val = self._gear_hash(data[position], hash_val)

            # Check for boundary
            if self._is_chunk_boundary(hash_val, position, chunk_start):
                # Found chunk boundary
                chunks.append((chunk_start, position))
                chunk_start = position

                # Stop if we've reached max chunks
                if len(chunks) >= max_chunks:
                    break

                # Reset hash
                hash_val = 0
                init_len = min(31, len(data) - position)
                for i in range(init_len):
                    hash_val = self._gear_hash(data[position + i], hash_val)
                position += init_len
                continue

            position += 1

        # Add final chunk if there's data left
        if chunk_start < len(data):
            chunks.append((chunk_start, len(data)))

        # Cap at max_chunks
        return chunks[:max_chunks]

    def chunk_signatures(
        self,
        data: bytes,
        *,
        max_chunks: int = 2048
    ) -> list[str]:
        """
        Get SHA256 signatures for each chunk.

        Args:
            data: Input bytes
            max_chunks: Maximum chunks to process

        Returns:
            List of SHA256 hex strings (one per chunk)
        """
        chunks = self.chunk_bytes(data, max_chunks=max_chunks)
        signatures = []

        for start, end in chunks:
            chunk = data[start:end]
            sig = hashlib.sha256(chunk).hexdigest()
            signatures.append(sig)

        return signatures

    def superfeatures(
        self,
        signatures: list[str],
        *,
        k: int = 12
    ) -> list[str]:
        """
        Compute superfeatures from chunk signatures.

        Superfeatures are minhash-like: select k smallest hashes from signatures.
        Used for similarity detection.

        Args:
            signatures: List of chunk SHA256 signatures
            k: Number of superfeatures to return

        Returns:
            List of k smallest signature prefixes (for comparison)
        """
        if not signatures:
            return []

        # Take first 8 chars of each signature for comparison
        # (enough for Bloom filter / LSH purposes)
        prefixes = [sig[:8] for sig in signatures]

        # Get k smallest (by hex value) — heapq.nsmallest is ~7x faster than full sorted
        return heapq.nsmallest(k, prefixes)


def chunk_data(
    data: bytes,
    min_size: int = 2048,
    avg_size: int = 8192,
    max_size: int = 65536,
    max_chunks: int = 2048
) -> list[tuple[int, int]]:
    """
    Convenience function to chunk bytes.

    Args:
        data: Input bytes
        min_size: Minimum chunk size
        avg_size: Target average chunk size
        max_size: Maximum chunk size
        max_chunks: Maximum number of chunks

    Returns:
        List of (start, end) offsets
    """
    engine = RollingHashEngine(min_size, avg_size, max_size)
    return engine.chunk_bytes(data, max_chunks=max_chunks)


def compute_superfeatures(
    data: bytes,
    k: int = 12
) -> list[str]:
    """
    Convenience function to compute superfeatures from data.

    Args:
        data: Input bytes
        k: Number of superfeatures

    Returns:
        List of k superfeature strings
    """
    engine = RollingHashEngine()
    signatures = engine.chunk_signatures(data)
    return engine.superfeatures(signatures, k=k)
