from dataclasses import dataclass
from pathlib import Path
from typing import Opt

try:
    from hledac_rust_extensions import chi_square as _rust_chi_square, entropy as _rust_entropy
    try:
        from hledac_rust_extensions import fast_ioc_extract_batch as _rust_extract_iocs
    except ImportError:
        _rust_extract_iocs = None
    try:
        from hledac_rust_extensions import url_normalize_batch as _rust_normalize_url
    except ImportError:
        _rust_normalize_url = None
    try:
        from hledac_rust_extensions import bloom_check_batch as _rust_bloom_check
    except ImportError:
        _rust_bloom_check = None
    try:
        from hledac_rust_extensions import batch_sha256 as _rust_batch_sha256
    except ImportError:
        _rust_batch_sha256 = None
    _RUST_AVAILABLE = True
except ImportError:
    _RUST_AVAILABLE = False
    _rust_extract_iocs = None
    _rust_normalize_url = None
    _rust_bloom_check = None
    _rust_batch_sha256 = None

# opt dep
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


import hashlib
import math
import re
import urllib.parse
from dataclasses import dataclass
from typing import Opt

__all__ = [
    "SteganalysisResult",
    "analyze_image_steganography",
    "MAX_FILE_SIZE",
    "STEGDETECT_AVAILABLE",
]


# Python pure-fallback implementations (used when Rust unavailable)

def _python_extract_iocs(text: str) -> list[tuple[str, str]]:
    """Pure-Python IOC extraction via regex."""
    patterns = {
        "ipv4": r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b",
        "ipv6": r"(?i)\b(?:[0-9a-f]{1,4}:){7}[0-9a-f]{1,4}\b",
        "onion": r"\b[a-z2-7]{56}\.onion\b",
        "domain": r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}\b",
        "md5": r"\b[a-fA-F0-9]{32}\b",
        "sha1": r"\b[a-fA-F0-9]{40}\b",
        "sha256": r"\b[a-fA-F0-9]{64}\b",
        "email": r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b",
        "cve": r"\bCVE-\d{4}-\d{4,}\b",
    }
    results = []
    seen = set()
    for ioc_type, pattern in patterns.items():
        for m in re.finditer(pattern, text):
            val = m.group()
            if val not in seen:
                seen.add(val)
                results.append((val, ioc_type))
    return results


def _python_normalize_url(url: str) -> str:
    """Pure-Python URL normalization."""
    try:
        parsed = urllib.parse.urlparse(url)
        scheme = parsed.scheme.lower()
        host = parsed.hostname or ""
        port = parsed.port
        strip_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
        out = f"{scheme}://{host}" + (f":{port}" if port and not strip_port else "")

        params = urllib.parse.parse_qsl(parsed.query)
        params = [(k, v) for k, v in params if not k.startswith("utm_") and not k.startswith("fb_") and not k.startswith("mc_")]
        params.sort(key=lambda x: x[0])
        if params:
            qs = urllib.parse.urlencode(params)
            out += f"?{qs}"
        return out
    except Exception:
        return url


def _python_batch_sha256(items: list[str]) -> list[str]:
    """Pure-Python SHA256 batch."""
    return [hashlib.sha256(s.encode()).hexdigest() for s in items]


# Wrappers — use Rust if available, pure-Python fallback otherwise

def extract_iocs(text: str) -> list[tuple[str, str]]:
    """Extract IOCs from text: IPv4/IPv6/.onion/domain/MD5/SHA1/SHA256/email/CVE."""
    if _RUST_AVAILABLE and _rust_extract_iocs is not None:
        return _rust_extract_iocs(text)
    return _python_extract_iocs(text)


def normalize_url(url: str) -> str:
    """Canonicalize URL: lowercase scheme+host, strip default ports, sort params, remove utm_*."""
    if _RUST_AVAILABLE and _rust_normalize_url is not None:
        return _rust_normalize_url(url)
    return _python_normalize_url(url)


def bloom_check(items: list[str], capacity: int = 100_000, fp_rate: float = 0.01) -> list[bool]:
    """Batch Bloom filter check for URL dedup pre-screening."""
    if _RUST_AVAILABLE and _rust_bloom_check is not None:
        try:
            return _rust_bloom_check(items, capacity)
        except Exception:
            pass
    return [False] * len(items)


def batch_sha256(items: list[str]) -> list[str]:
    """SHA256 hash each string — for fast dedup fingerprinting."""
    if _RUST_AVAILABLE and _rust_batch_sha256 is not None:
        return _rust_batch_sha256(items)
    return _python_batch_sha256(items)


@dataclass
class SteganalysisResult:
    """Result of steganalysis on an image."""
    file_path: str
    lsb_suspicious: bool = False
    lsb_score: float = 0.0  # 0.0-1.0, higher = more suspicious
    histogram_suspicious: bool = False
    histogram_score: float = 0.0  # 0.0-1.0
    chi_square_score: float = 0.0
    stegdetect_result: Opt[str] = None
    stegdetect_available: bool = False
    overall_suspicious: bool = False
    confidence: float = 0.0  # 0.0-1.0
    err: Opt[str] = None


MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB


def analyze_image_steganography(file_path: str) -> SteganalysisResult:
    """Analyze image for steganographic content using multiple techniques."""
    result = SteganalysisResult(file_path=file_path)
    try:
        path = Path(file_path)
        if path.stat().st_size > MAX_FILE_SIZE:
            result.err = "file_too_large"
            return result
        with open(path, "rb") as f:
            data = f.read()
    except Exception as e:
        result.err = str(e)
        return result

    try:
        result.chi_square_score = chi_square(data)
    except Exception:
        pass

    try:
        result.overall_suspicious = result.chi_square_score > 0.47
        result.confidence = min(result.chi_square_score * 2, 1.0)
    except Exception:
        pass

    return result


def chi_square(data: bytes) -> float:
    """Compute chi-square statistic on byte histogram. Rust-accelerated."""
    if _RUST_AVAILABLE:
        try:
            return float(_rust_chi_square(data))
        except Exception:
            pass
    # Pure-Python fallback
    if not data:
        return 0.0
    hist = [0] * 256
    for b in data:
        hist[b] += 1
    n = len(data)
    expected = n / 256.0
    chi = sum((obs - expected) ** 2 / expected for obs in hist)
    return chi / 256.0


def entropy(data: bytes) -> float:
    """Compute Shannon entropy. Rust-accelerated."""
    if _RUST_AVAILABLE:
        try:
            return float(_rust_entropy(data))
        except Exception:
            pass
    if not data:
        return 0.0
    hist = [0] * 256
    for b in data:
        hist[b] += 1
    n = len(data)
    ent = 0.0
    for count in hist:
        if count > 0:
            p = count / n
            ent -= p * math.log2(p)
    return ent