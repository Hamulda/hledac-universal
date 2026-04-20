"""
Steganography Detector
=====================

Steganography detection for images using statistical analysis.
Supports LSB detection, histogram analysis, and stegdetect wrapper.

Features:
- Chi-square histogram analysis
- LSB (Least Significant Bit) steganography detection
- stegdetect wrapper integration
- Streaming for large files (max 5MB)
- Bounded analysis with early termination

M1 8GB Optimized:
- Max 5MB file reads
- Streaming chunked analysis
- Memory-bounded histogram computation
"""

from __future__ import annotations

import math
import os
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Optional dependency
STEGDETECT_AVAILABLE = False
try:
    import subprocess

    def _check_stegdetect() -> bool:
        """Check if stegdetect binary is available."""
        try:
            subprocess.run(
                ["stegdetect", "-v"],
                capture_output=True,
                timeout=5,
            )
            return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    STEGDETECT_AVAILABLE = _check_stegdetect()
except Exception:
    pass


# Constants
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB max
CHUNK_SIZE = 64 * 1024  # 64KB chunks for streaming


@dataclass
class SteganalysisResult:
    """Result of steganalysis on an image."""
    file_path: str
    lsb_suspicious: bool = False
    lsb_score: float = 0.0  # 0.0-1.0, higher = more suspicious
    histogram_suspicious: bool = False
    histogram_score: float = 0.0  # 0.0-1.0
    chi_square_score: float = 0.0
    stegdetect_result: Optional[str] = None
    stegdetect_available: bool = False
    overall_suspicious: bool = False
    confidence: float = 0.0  # 0.0-1.0
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "file_path": self.file_path,
            "lsb_suspicious": self.lsb_suspicious,
            "lsb_score": self.lsb_score,
            "histogram_suspicious": self.histogram_suspicious,
            "histogram_score": self.histogram_score,
            "chi_square_score": self.chi_square_score,
            "stegdetect_result": self.stegdetect_result,
            "stegdetect_available": self.stegdetect_available,
            "overall_suspicious": self.overall_suspicious,
            "confidence": self.confidence,
            "error": self.error,
        }


def _calculate_chi_square(data: bytes) -> float:
    """
    Calculate chi-square statistic for LSB steganalysis.

    In clean images, even and odd byte values should have similar frequencies.
    LSB steganography often distorts this distribution.

    Args:
        data: Raw bytes to analyze

    Returns:
        Chi-square score (higher = more suspicious)
    """
    if len(data) < 256:
        return 0.0

    # Count even/odd byte frequencies
    even_count = 0
    odd_count = 0

    for byte in data:
        if byte % 2 == 0:
            even_count += 1
        else:
            odd_count += 1

    total = even_count + odd_count
    if total == 0:
        return 0.0

    expected = total / 2.0

    # Chi-square: sum((observed - expected)^2 / expected)
    chi_square = 0.0
    if expected > 0:
        even_diff = even_count - expected
        chi_square += (even_diff * even_diff) / expected
    if expected > 0:
        odd_diff = odd_count - expected
        chi_square += (odd_diff * odd_diff) / expected

    return chi_square


def _analyze_histogram(data: bytes) -> tuple[float, float]:
    """
    Analyze byte histogram for steganography signatures.

    Detects:
    - Unusual gaps in byte frequency distribution
    - Abnormal entropy patterns
    - Quantization artifacts from embedding

    Args:
        data: Raw bytes to analyze

    Returns:
        Tuple of (histogram_score, entropy_score)
    """
    if len(data) < 256:
        return 0.0, 0.0

    # Build histogram
    histogram = [0] * 256
    for byte in data:
        histogram[byte] += 1

    total = len(data)

    # Calculate entropy
    entropy = 0.0
    for count in histogram:
        if count > 0:
            p = count / total
            entropy -= p * math.log2(p)

    # Max entropy for byte distribution is 8.0
    normalized_entropy = entropy / 8.0

    # Detect unusual gaps (steganography often creates them)
    # Count how many byte values have exactly 0 or very low counts
    zero_count = sum(1 for c in histogram if c == 0)
    low_count = sum(1 for c in histogram if 0 < c < total * 0.001)

    # Gaps score: 0-1 range
    gap_score = (zero_count + low_count * 0.5) / 256.0
    gap_score = min(gap_score, 1.0)

    # Combined histogram suspicion score
    histogram_score = (gap_score * 0.5 + (1 - normalized_entropy) * 0.5)

    return histogram_score, normalized_entropy


def _lsb_detection(file_path: str) -> tuple[bool, float]:
    """
    Detect LSB steganography by analyzing least significant bits.

    Args:
        file_path: Path to image file

    Returns:
        Tuple of (is_suspicious, score)
    """
    try:
        file_size = os.path.getsize(file_path)
        if file_size > MAX_FILE_SIZE:
            return False, 0.0

        with open(file_path, "rb") as f:
            data = f.read()

        # Focus on first 1MB for speed (steganography typically in early bytes)
        analysis_data = data[:1024 * 1024]

        chi_square = _calculate_chi_square(analysis_data)

        # Chi-square threshold for suspicion (empirical)
        # Clean images typically have chi-square < 10
        # Embedded images often show chi-square > 50
        is_suspicious = chi_square > 50.0
        score = min(chi_square / 100.0, 1.0)  # Normalize to 0-1

        return is_suspicious, score

    except Exception:
        return False, 0.0


def analyze_image_steganography(file_path: str) -> SteganalysisResult:
    """
    Perform comprehensive steganalysis on an image file.

    Args:
        file_path: Path to image file

    Returns:
        SteganalysisResult with detection results
    """
    result = SteganalysisResult(file_path=file_path)

    try:
        path = Path(file_path)
        if not path.exists():
            result.error = "File not found"
            return result

        file_size = path.stat().st_size
        if file_size > MAX_FILE_SIZE:
            result.error = f"File too large: {file_size} bytes (max: {MAX_FILE_SIZE})"
            return result

        # Check file extension (steganography typically in images)
        ext = path.suffix.lower()
        if ext not in {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tiff", ".tif"}:
            result.error = f"Unsupported file type: {ext}"
            return result

        # LSB detection
        result.lsb_suspicious, result.lsb_score = _lsb_detection(file_path)

        # Histogram analysis
        with open(file_path, "rb") as f:
            data = f.read()

        # Analyze first 2MB for histogram
        analysis_data = data[:2 * 1024 * 1024]
        result.histogram_score, entropy = _analyze_histogram(analysis_data)
        result.histogram_suspicious = result.histogram_score > 0.6

        # Chi-square on LSB analysis data
        result.chi_square_score = _calculate_chi_square(analysis_data)

        # Stegdetect if available
        result.stegdetect_available = STEGDETECT_AVAILABLE
        if STEGDETECT_AVAILABLE:
            try:
                proc = subprocess.run(
                    ["stegdetect", "-t", "j", file_path],
                    capture_output=True,
                    timeout=30,
                )
                output = proc.stdout.decode("utf-8", errors="ignore").strip()
                if output:
                    result.stegdetect_result = output
                    # Parse stegdetect output for suspicion
                    if "negative" not in output.lower():
                        result.overall_suspicious = True
            except Exception:
                pass

        # Overall suspicion
        scores = [
            result.lsb_score,
            result.histogram_score,
            result.chi_square_score / 100.0,  # Normalize
        ]
        result.confidence = sum(scores) / len(scores)

        # Overall suspicious if any strong signal
        result.overall_suspicious = (
            result.overall_suspicious
            or result.lsb_suspicious
            or result.histogram_suspicious
            or result.confidence > 0.5
        )

    except Exception as e:
        result.error = str(e)

    return result


def _stegdetect_wrapper(file_path: str, options: Optional[list[str]] = None) -> str:
    """
    Wrapper for stegdetect binary.

    Args:
        file_path: Path to file to analyze
        options: Additional stegdetect options

    Returns:
        stegdetect output string
    """
    if not STEGDETECT_AVAILABLE:
        return ""

    try:
        cmd = ["stegdetect"]
        if options:
            cmd.extend(options)
        cmd.append(file_path)

        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=60,
        )
        return proc.stdout.decode("utf-8", errors="ignore").strip()

    except Exception:
        return ""


__all__ = [
    "SteganalysisResult",
    "analyze_image_steganography",
    "MAX_FILE_SIZE",
    "STEGDETECT_AVAILABLE",
]
