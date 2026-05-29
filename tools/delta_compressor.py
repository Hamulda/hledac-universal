"""
Delta Compressor - Text delta using unified_diff + zlib compression.

Implements:
- make_text_delta: Create delta from base and newer text
- apply_text_delta: Apply delta to reconstruct newer text

Uses unified_diff for text differences and zlib for compression.
"""

from __future__ import annotations

import difflib
import logging
import re
import struct
import zlib

logger = logging.getLogger(__name__)

# Magic number for delta format
DELTA_MAGIC = b'DELT'
VERSION = 1

# Constants
MAX_CHARS = 200_000
MAX_LINES = 20_000
MAX_OUTPUT_CHARS = 300_000


class DeltaCompressor:
    """
    Text delta compressor using unified_diff + zlib.

    Format:
    - Magic (4 bytes): b'DELT'
    - Version (1 byte)
    - Flags (1 byte): bit 0 = compressed
    - Original length (4 bytes)
    - Delta length (4 bytes)
    - Delta data (variable)
    """

    def __init__(self, compress: bool = True):
        """
        Initialize delta compressor.

        Args:
            compress: Whether to compress delta data with zlib
        """
        self.compress = compress
        self.logger = logging.getLogger(__name__)

    def make_text_delta(
        self,
        base: str,
        newer: str,
        *,
        max_chars: int = MAX_CHARS,
        max_lines: int = MAX_LINES
    ) -> bytes:
        """
        Create delta from base to newer text.

        Args:
            base: Original/base text
            newer: Newer version text
            max_chars: Maximum input characters to process
            max_lines: Maximum lines to diff

        Returns:
            Delta bytes, or full compressed newer text if delta too large
        """
        # Bound inputs
        base = base[:max_chars]
        newer = newer[:max_chars]

        base_lines = base.splitlines(keepends=True)[:max_lines]
        newer_lines = newer.splitlines(keepends=True)[:max_lines]

        # Generate unified diff
        diff = list(difflib.unified_diff(
            base_lines,
            newer_lines,
            fromfile='base',
            tofile='newer',
            lineterm=''
        ))

        diff_text = ''.join(diff)

        # If diff is larger than original, store full newer text
        if len(diff_text) >= len(newer):
            return self._encode_full(newer)

        # Try to compress
        if self.compress:
            try:
                compressed = zlib.compress(diff_text.encode('utf-8'), level=6)
                # If compression helps, use it
                if len(compressed) < len(diff_text):
                    return self._encode_delta(diff_text.encode('utf-8'), len(base), compressed=True)
            except Exception as e:
                self.logger.warning(f"Compression failed: {e}")

        # Store uncompressed delta
        return self._encode_delta(diff_text.encode('utf-8'), len(base), compressed=False)

    def _encode_delta(self, diff_bytes: bytes, original_len: int, compressed: bool) -> bytes:
        """Encode delta with header."""
        flags = 1 if compressed else 0
        header = struct.pack('>4sBBII', DELTA_MAGIC, VERSION, flags, original_len, len(diff_bytes))
        return header + diff_bytes

    def _encode_full(self, text: str) -> bytes:
        """Encode full text (no delta)."""
        text_bytes = text.encode('utf-8')
        compressed = zlib.compress(text_bytes, level=6)
        # Use special flag to indicate full text
        header = struct.pack('>4sBBII', DELTA_MAGIC, VERSION, 0x80, len(text_bytes), len(compressed))
        return header + compressed

    def apply_text_delta(
        self,
        base: str,
        delta: bytes,
        *,
        max_output_chars: int = MAX_OUTPUT_CHARS
    ) -> str:
        """
        Apply delta to reconstruct newer text.

        Args:
            base: Original/base text
            delta: Delta bytes from make_text_delta
            max_output_chars: Maximum output characters

        Returns:
            Reconstructed newer text, or original if delta is invalid
        """
        if not delta or len(delta) < 14:
            return base

        # Parse header
        try:
            header = delta[:14]
            magic, version, flags, original_len, delta_len = struct.unpack('>4sBBII', header)

            if magic != DELTA_MAGIC:
                self.logger.warning("Invalid delta magic")
                return base

            if version != VERSION:
                self.logger.warning(f"Unsupported delta version: {version}")
                return base

            delta_data = delta[14:14 + delta_len]

        except Exception as e:
            self.logger.warning(f"Failed to parse delta header: {e}")
            return base

        # Check if this is full text (no delta)
        if flags & 0x80:
            # Full text stored
            try:
                text = zlib.decompress(delta_data)
                return text.decode('utf-8')[:max_output_chars]
            except Exception as e:
                self.logger.warning(f"Failed to decompress full text: {e}")
                return base

        # Decompress if needed
        if flags & 1:
            try:
                diff_text = zlib.decompress(delta_data).decode('utf-8')
            except Exception as e:
                self.logger.warning(f"Failed to decompress delta: {e}")
                return base
        else:
            try:
                diff_text = delta_data.decode('utf-8')
            except Exception as e:
                self.logger.warning(f"Failed to decode delta: {e}")
                return base

        # ===== Apply unified diff with correct multi-hunk handling =====
        try:
            base_lines = base.splitlines(keepends=True)
            result_lines = list(base_lines)  # start with copy of base

            diff_lines = diff_text.splitlines(keepends=True)
            offset = 0  # tracks how inserted/removed lines shift positions

            i = 0
            while i < len(diff_lines):
                line = diff_lines[i]
                # Parse hunk header – use the pattern verified above
                if line.startswith('@@'):
                    hunk_match = re.match(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@', line)
                    if not hunk_match:
                        i += 1
                        continue
                    base_start = int(hunk_match.group(1)) - 1  # 0-indexed
                    base_count = int(hunk_match.group(2) or 1)

                    # Collect hunk lines
                    i += 1
                    hunk_remove = []
                    hunk_add = []
                    while i < len(diff_lines) and not diff_lines[i].startswith('@@'):
                        hunk_line = diff_lines[i]
                        if hunk_line.startswith('-'):
                            hunk_remove.append(hunk_line[1:])
                        elif hunk_line.startswith('+'):
                            hunk_add.append(hunk_line[1:])
                        # context lines (space prefix): skip
                        i += 1

                    # Apply hunk at offset-adjusted position
                    insert_pos = base_start + offset
                    insert_pos = max(0, min(insert_pos, len(result_lines)))

                    # Remove lines from result
                    for _ in range(min(base_count, len(result_lines) - insert_pos)):
                        if insert_pos < len(result_lines):
                            result_lines.pop(insert_pos)

                    # Insert new lines
                    for j, new_line in enumerate(hunk_add):
                        result_lines.insert(insert_pos + j, new_line)

                    # Update offset
                    offset += len(hunk_add) - base_count
                else:
                    i += 1

            result = ''.join(result_lines)
            return result[:max_output_chars]
        except Exception as e:
            self.logger.warning(f"Failed to apply delta: {e}")
            return base


def make_delta(base: str, newer: str) -> bytes:
    """
    Convenience function to create delta.

    Args:
        base: Original text
        newer: Newer text

    Returns:
        Delta bytes
    """
    compressor = DeltaCompressor()
    return compressor.make_text_delta(base, newer)


def apply_delta(base: str, delta: bytes) -> str:
    """
    Convenience function to apply delta.

    Args:
        base: Original text
        delta: Delta bytes

    Returns:
        Reconstructed newer text
    """
    compressor = DeltaCompressor()
    return compressor.apply_text_delta(base, delta)
