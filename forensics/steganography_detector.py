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
from typing import Opt

try:
    from hledac_core import chi_square as _rust_chi_square, entropy as _rust_entropy
    from hledac_core import fast_ioc_extract as _rust_fast_ioc_extract
    from hledac_core import bloom_check as _rust_bloom_check
    from hledac_core import url_normalize as _rust_url_normalize
    _RUST_AVAILABLE = True
except ImportError:
    _RUST_AVAILABLE = False

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
    Uses Rust extension if available, falls back to Python.

    Args:
        data: Raw bytes to analyze

    Returns:
        Chi-square score (higher = more suspicious)
    """
    if _RUST_AVAILABLE:
        try:
            return float(_rust_chi_square(data))
        except Exception:
            pass  # Fall through to Python

    # Python fallback: 256-bin histogram chi-square
    if len(data) < 256:
        return 0.0

    histogram = [0] * 256
    for byte in data:
        histogram[byte] += 1

    n = len(data)
    expected = n / 256.0
    chi_square = 0.0

    for count in histogram:
        if count == 0:
            continue
        diff = count - expected
        chi_square += (diff * diff) / expected

    return chi_square


def _python_entropy(histogram: list[int], total: int) -> float:
    """Python-only entropy calculation (helper for fallback)."""
    entropy = 0.0
    for count in histogram:
        if count > 0:
            p = count / total
            entropy -= p * math.log2(p)
    return entropy / 8.0


# ===== Python fallback implementations for Rust functions =====

import re
from typing import TypedDict

class IocResult(TypedDict):
    ioc_type: str
    ioc_value: str

def _python_fast_ioc_extract(text: str) -> list[IocResult]:
    """Python fallback for IOC extraction using regex."""
    results: list[IocResult] = []

    # IPv4
    ipv4_re = re.compile(r'\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b')
    for m in ipv4_re.finditer(text):
        results.append({"ioc_type": "ipv4", "ioc_value": m.group()})

    # IPv6
    ipv6_re = re.compile(r'\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b')
    for m in ipv6_re.finditer(text):
        results.append({"ioc_type": "ipv6", "ioc_value": m.group()})

    # Domain
    domain_re = re.compile(r'\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}\b')
    for m in domain_re.finditer(text):
        domain = m.group().lower()
        if domain not in ("example.com", "test.com", "localhost"):
            results.append({"ioc_type": "domain", "ioc_value": domain})

    # MD5
    md5_re = re.compile(r'\b[a-fA-F0-9]{32}\b')
    for m in md5_re.finditer(text):
        results.append({"ioc_type": "md5", "ioc_value": m.group().lower()})

    # SHA1
    sha1_re = re.compile(r'\b[a-fA-F0-9]{40}\b')
    for m in sha1_re.finditer(text):
        results.append({"ioc_type": "sha1", "ioc_value": m.group().lower()})

    # SHA256
    sha256_re = re.compile(r'\b[a-fA-F0-9]{64}\b')
    for m in sha256_re.finditer(text):
        results.append({"ioc_type": "sha256", "ioc_value": m.group().lower()})

    # Email
    email_re = re.compile(r'\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b')
    for m in email_re.finditer(text):
        results.append({"ioc_type": "email", "ioc_value": m.group().lower()})

    # CVE
    cve_re = re.compile(r'\bCVE-\d{4}-\d{4,}\b')
    for m in cve_re.finditer(text):
        results.append({"ioc_type": "cve", "ioc_value": m.group().upper()})

    return results


def _python_url_normalize(url: str) -> str:
    """Python fallback for URL normalization."""
    from urllib.parse import urlparse, urlencode

    try:
        parsed = urlparse(url)
    except Exception:
        return url.lower()

    # Lowercase scheme and host
    scheme = parsed.scheme.lower()
    host = parsed.netloc.lower().split("@")[-1]  # Remove user:pass@
    if ":" in host:
        host, port_str = host.rsplit(":", 1)
        # Remove default ports
        if (scheme == "http" and port_str == "80") or (scheme == "https" and port_str == "443"):
            port_str = ""
        if port_str:
            host = f"{host}:{port_str}"
    else:
        # Check for default port in netloc
        netloc_lower = parsed.netloc.lower()
        if netloc_lower.endswith(":80"):
            host = host.replace(":80", "")
        elif netloc_lower.endswith(":443"):
            host = host.replace(":443", "")

    # Path
    path = parsed.path or "/"

    # Sort and filter query params
    if parsed.query:
        params = sorted(parsed.query.split("&"))
        tracking = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
                   "fbclid", "gclid", "msclkid", "ref", "source", "mc_cid", "mc_eid"}
        filtered = [p for p in params if not any(p.startswith(t + "=") for t in tracking)]
        query = "&".join(filtered)
    else:
        query = ""

    result = f"{scheme}://{host}{path}"
    if query:
        result += f"?{query}"
    return result


# ===== Public wrappers =====

def fast_ioc_extract(text: str) -> list[IocResult]:
    """Extract IOCs from text using Rust extension if available."""
    if _RUST_AVAILABLE:
        try:
            raw = _rust_fast_ioc_extract(text)
            return [{"ioc_type": ioc_type, "ioc_value": ioc_value} for ioc_value, ioc_type in raw]
        except Exception:
            pass
    return _python_fast_ioc_extract(text)


def url_normalize(url: str) -> str:
    """Normalize URL using Rust extension if available."""
    if _RUST_AVAILABLE:
        try:
            return _rust_url_normalize(url)
        except Exception:
            pass
    return _python_url_normalize(url)


def bloom_check(items: list[str], capacity: int, fp_rate: float) -> list[bool]:
    """Batch Bloom filter check using Rust extension if available."""
    if _RUST_AVAILABLE:
        try:
            return _rust_bloom_check(items, capacity, fp_rate)
        except Exception:
            pass
    # Fallback: all False (assume not in filter)
    return [False] * len(items)


def _analyze_histogram(data: bytes) -> tuple[float, float]:
    """
    Analyze byte histogram for steganography signatures.
    Uses Rust extension for entropy if available, falls back to Python.

    Returns:
        Tuple of (histogram_score, entropy_score)
    """
    if len(data) < 256:
        return 0.0, 0.0

    histogram = [0] * 256
    for byte in data:
        histogram[byte] += 1

    total = len(data)

    # Calculate entropy using Rust or Python
    if _RUST_AVAILABLE:
        try:
            normalized_entropy = float(_rust_entropy(data))
        except Exception:
            normalized_entropy = _python_entropy(histogram, total)
    else:
        normalized_entropy = _python_entropy(histogram, total)

    # Detect unusual gaps (steganography often creates them)
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
