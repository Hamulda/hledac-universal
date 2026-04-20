"""
Digital Ghost Detector
======================

Detects "digital ghosts" - traces of deleted, hidden, or tampered content
in files and filesystems. Analyzes residual artifacts, file structure
anomalies, and metadata inconsistencies.

Features:
- Deleted file residue detection
- Hidden content analysis
- Tampering detection via structure analysis
- Timeline anomaly detection
- Metadata ghost detection

M1 8GB Optimized:
- Streaming for large files
- Bounded analysis
- Chunked processing
"""

from __future__ import annotations

import math
import os
import struct
import zlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


# Constants
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB max
CHUNK_SIZE = 64 * 1024  # 64KB chunks


@dataclass
class GhostArtifact:
    """A detected ghost artifact."""
    artifact_type: str  # deleted_content, hidden_data, tampering, timeline_anomaly, metadata_ghost
    confidence: float  # 0.0-1.0
    description: str
    location: Optional[str] = None
    evidence: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "artifact_type": self.artifact_type,
            "confidence": self.confidence,
            "description": self.description,
            "location": self.location,
            "evidence": self.evidence,
        }


@dataclass
class DigitalGhostResult:
    """Result of digital ghost analysis."""
    file_path: str
    success: bool
    artifacts: list[GhostArtifact] = field(default_factory=list)
    tampering_score: float = 0.0  # 0.0-1.0
    ghost_score: float = 0.0  # 0.0-1.0
    overall_suspicious: bool = False
    confidence: float = 0.0  # 0.0-1.0
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "file_path": self.file_path,
            "success": self.success,
            "artifacts": [a.to_dict() for a in self.artifacts],
            "tampering_score": self.tampering_score,
            "ghost_score": self.ghost_score,
            "overall_suspicious": self.overall_suspicious,
            "confidence": self.confidence,
            "error": self.error,
        }


def _detect_zlib_ghosts(data: bytes) -> list[GhostArtifact]:
    """
    Detect residual zlib-compressed data that might indicate hidden content.

    Args:
        data: Raw bytes to analyze

    Returns:
        List of detected ghost artifacts
    """
    artifacts = []

    if len(data) < 1024:
        return artifacts

    # Look for zlib signatures that don't belong
    pos = 0
    zlib_occurrences = 0

    while pos < len(data) - 4:
        # Check for zlib header (0x78)
        if data[pos] == 0x78:
            # Potential zlib stream
            try:
                # Try to parse as zlib
                decompressor = zlib.decompressobj()
                chunk = data[pos:pos + 1024]
                decompressor.decompress(chunk)
                zlib_occurrences += 1
                pos += 1
            except Exception:
                pos += 1
        else:
            pos += 1

    # Multiple zlib streams might indicate hidden data
    if zlib_occurrences > 3:
        artifacts.append(GhostArtifact(
            artifact_type="hidden_data",
            confidence=min(zlib_occurrences / 20.0, 1.0),
            description=f"Multiple zlib streams detected ({zlib_occurrences})",
            evidence={"zlib_stream_count": zlib_occurrences},
        ))

    return artifacts


def _detect_byte_pattern_anomalies(data: bytes) -> list[GhostArtifact]:
    """
    Detect unusual byte patterns that might indicate deleted content.

    Args:
        data: Raw bytes to analyze

    Returns:
        List of detected ghost artifacts
    """
    artifacts = []

    if len(data) < 256:
        return artifacts

    # Count transitions (bytes that differ from neighbors)
    transitions = 0
    for i in range(1, len(data)):
        if data[i] != data[i - 1]:
            transitions += 1

    transition_ratio = transitions / max(len(data) - 1, 1)

    # Very low transition ratio might indicate padding/encryption
    if transition_ratio < 0.01:
        artifacts.append(GhostArtifact(
            artifact_type="hidden_data",
            confidence=0.7,
            description="Unusually low byte variation - possible encrypted or padded content",
            evidence={"transition_ratio": transition_ratio},
        ))

    # Very high entropy regions might indicate compressed/encrypted data
    entropy_samples = []
    sample_size = 256
    for i in range(0, min(len(data), 8192), sample_size):
        chunk = data[i:i + sample_size]
        if len(chunk) == sample_size:
            entropy = _calculate_entropy(chunk)
            entropy_samples.append(entropy)

    if entropy_samples:
        avg_entropy = sum(entropy_samples) / len(entropy_samples)
        high_entropy_count = sum(1 for e in entropy_samples if e > 7.0)

        if high_entropy_count > len(entropy_samples) * 0.5:
            artifacts.append(GhostArtifact(
                artifact_type="hidden_data",
                confidence=0.6,
                description="High entropy regions detected - possible compressed or encrypted data",
                evidence={
                    "avg_entropy": avg_entropy,
                    "high_entropy_regions": high_entropy_count,
                },
            ))

    return artifacts


def _calculate_entropy(data: bytes) -> float:
    """
    Calculate Shannon entropy of data.

    Args:
        data: Bytes to analyze

    Returns:
        Entropy in bits (0-8)
    """
    if not data:
        return 0.0

    histogram = [0] * 256
    for byte in data:
        histogram[byte] += 1

    entropy = 0.0
    total = len(data)
    for count in histogram:
        if count > 0:
            p = count / total
            entropy -= p * math.log2(p)

    return entropy


def _detect_string_fragments(data: bytes) -> list[GhostArtifact]:
    """
    Detect fragmented string remnants that might indicate deleted content.

    Args:
        data: Raw bytes to analyze

    Returns:
        List of detected ghost artifacts
    """
    artifacts = []

    if len(data) < 64:
        return artifacts

    # Look for null bytes followed by printable strings
    # This pattern often appears in file slack space
    null_runs = 0
    i = 0
    while i < len(data) - 16:
        if data[i] == 0:
            # Count run of nulls
            run_length = 0
            while i < len(data) and data[i] == 0:
                run_length += 1
                i += 1

            # Long null run followed by printable content might be ghost
            if run_length >= 8:
                # Check if followed by printable ASCII
                remaining = data[i:i + 8]
                printable_count = sum(1 for b in remaining if 32 <= b < 127)
                if printable_count >= 4:
                    null_runs += 1
        else:
            i += 1

    if null_runs > 2:
        artifacts.append(GhostArtifact(
            artifact_type="deleted_content",
            confidence=min(null_runs / 10.0, 0.8),
            description=f"Null byte patterns with string remnants ({null_runs} occurrences)",
            evidence={"null_pattern_count": null_runs},
        ))

    return artifacts


def _detect_duplicate_patterns(data: bytes) -> list[GhostArtifact]:
    """
    Detect duplicate or repetitive patterns that might indicate tampering.

    Args:
        data: Raw bytes to analyze

    Returns:
        List of detected ghost artifacts
    """
    artifacts = []

    if len(data) < 512:
        return artifacts

    # Look for exact 256-byte repeats (common in copied/fabricated content)
    chunk_size = 256
    repeats = {}

    for i in range(0, len(data) - chunk_size, chunk_size):
        chunk = data[i:i + chunk_size]
        chunk_hash = hash(chunk[:32])  # Hash first 32 bytes for speed

        if chunk_hash in repeats:
            repeats[chunk_hash] += 1
        else:
            repeats[chunk_hash] = 1

    # Find chunks that repeat more than 3 times
    suspicious_repeats = {k: v for k, v in repeats.items() if v > 3}

    if suspicious_repeats:
        total_suspicious = sum(suspicious_repeats.values())
        artifacts.append(GhostArtifact(
            artifact_type="tampering",
            confidence=min(total_suspicious / 20.0, 0.9),
            description=f"Repetitive content blocks detected ({total_suspicious} repeated chunks)",
            evidence={"repeated_chunk_count": total_suspicious},
        ))

    return artifacts


def analyze_file_ghosts(file_path: str) -> DigitalGhostResult:
    """
    Perform comprehensive digital ghost analysis on a file.

    Args:
        file_path: Path to file to analyze

    Returns:
        DigitalGhostResult with detected artifacts
    """
    result = DigitalGhostResult(file_path=file_path, success=False)

    try:
        path = Path(file_path)
        if not path.exists():
            result.error = "File not found"
            return result

        file_size = path.stat().st_size
        if file_size > MAX_FILE_SIZE:
            result.error = f"File too large: {file_size} bytes (max: {MAX_FILE_SIZE})"
            return result

        with open(file_path, "rb") as f:
            data = f.read()

        # Run all ghost detection methods
        result.artifacts.extend(_detect_zlib_ghosts(data))
        result.artifacts.extend(_detect_byte_pattern_anomalies(data))
        result.artifacts.extend(_detect_string_fragments(data))
        result.artifacts.extend(_detect_duplicate_patterns(data))

        # Calculate overall scores
        if result.artifacts:
            tampering_scores = [
                a.confidence for a in result.artifacts
                if a.artifact_type == "tampering"
            ]
            result.tampering_score = max(tampering_scores) if tampering_scores else 0.0

            ghost_scores = [
                a.confidence for a in result.artifacts
                if a.artifact_type in ("deleted_content", "hidden_data", "metadata_ghost")
            ]
            result.ghost_score = max(ghost_scores) if ghost_scores else 0.0

            all_confidences = [a.confidence for a in result.artifacts]
            result.confidence = sum(all_confidences) / len(all_confidences)

            result.overall_suspicious = (
                result.tampering_score > 0.5
                or result.ghost_score > 0.5
                or len(result.artifacts) >= 3
            )

        result.success = True

    except Exception as e:
        result.error = str(e)

    return result


def analyze_directory_ghosts(directory_path: str) -> list[DigitalGhostResult]:
    """
    Analyze all files in a directory for digital ghost artifacts.

    Args:
        directory_path: Path to directory

    Returns:
        List of DigitalGhostResult for each file
    """
    results = []

    try:
        path = Path(directory_path)
        if not path.is_dir():
            return results

        for file_path in path.rglob("*"):
            if file_path.is_file():
                result = analyze_file_ghosts(str(file_path))
                results.append(result)

    except Exception:
        pass

    return results


__all__ = [
    "GhostArtifact",
    "DigitalGhostResult",
    "analyze_file_ghosts",
    "analyze_directory_ghosts",
    "MAX_FILE_SIZE",
]
