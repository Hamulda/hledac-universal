# metal.py — Metal/GPU domain
"""
Metal GPU detection and batch keyword/IOC scanning.
Pure Python fallback for environments without Metal GPU.

"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

class _PythonMetalDomainInner:
    """Python fallback for Metal/GPU scanning operations."""

    __slots__ = ("_ipv4_re", "_ipv6_re", "_url_re", "_email_re", "_hash_re")

    def __init__(self) -> None:
        self._ipv4_re = re.compile(
            r"(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}"
            r"(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)"
    )
        self._ipv6_re = re.compile(
            r"(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}|"
            r"(?:[0-9a-fA-F]{1,4}:){1,7}:|"
            r"(?:[0-9a-fA-F]{1,4}:){1,6}:[0-9a-fA-F]{1,4}|"
            r"(?:[0-9a-fA-F]{1,4}:){1,5}(?::[0-9a-fA-F]{1,4}){1,2}|"
            r"(?:[0-9a-fA-F]{1,4}:){1,4}(?::[0-9a-fA-F]{1,4}){1,3}|"
            r"(?:[0-9a-fA-F]{1,4}:){1,3}(?::[0-9a-fA-F]{1,4}){1,4}|"
            r"(?:[0-9a-fA-F]{1,4}:){1,2}(?::[0-9a-fA-F]{1,4}){1,5}|"
            r"[0-9a-fA-F]{1,4}:(?::[0-9a-fA-F]{1,4}){1,6}|"
            r":(?:(?::[0-9a-fA-F]{1,4}){1,7}|:)|"
            r"::(?:[fF]{4}:)?(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}"
            r"(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)"
    )
        self._url_re = re.compile(r"https?://[^\s<>\"\']+")
        self._email_re = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
        # MD5, SHA1, SHA256, SHA512
        self._hash_re = re.compile(
            r"\b[a-fA-F0-9]{32}\b|\b[a-fA-F0-9]{40}\b|"
            r"\b[a-fA-F0-9]{64}\b|\b[a-fA-F0-9]{128}\b"
    )

    def batch_keyword_scan(self, texts: list[str], keywords: list[str]) -> list[tuple[int, int, int, int]]:
        """Batch keyword scan - returns (text_idx, start, end, keyword_idx) for each match."""
        results: list[tuple[int, int, int, int]] = []
        for ti, text in enumerate(texts):
            for ki, kw in enumerate(keywords):
                start = 0
                while True:
                    idx = text.find(kw, start)
                    if idx == -1:
                        break
                    results.append((ti, idx, idx + len(kw), ki))
                    start = idx + 1
        return results

    def batch_ioc_scan(self, texts: list[str]) -> list[tuple[int, int, int, int, str]]:
        """IoC scan: IP (IPv4=0, IPv6=1), URL=2, email=3, hash=4."""
        results: list[tuple[int, int, int, int, str]] = []
        for ti, text in enumerate(texts):
            for m in self._ipv4_re.finditer(text):
                results.append((ti, 0, m.start(), m.end(), m.group()))
            for m in self._ipv6_re.finditer(text):
                results.append((ti, 1, m.start(), m.end(), m.group()))
            for m in self._url_re.finditer(text):
                results.append((ti, 2, m.start(), m.end(), m.group()))
            for m in self._email_re.finditer(text):
                results.append((ti, 3, m.start(), m.end(), m.group()))
            for m in self._hash_re.finditer(text):
                results.append((ti, 4, m.start(), m.end(), m.group()))
        return results


def _python_check_metal_availability() -> dict[str, Any]:
    """Check Metal/GPU availability - always returns False for Python fallback."""
    return {
        "metal_available": False,
        "device_name": "python_fallback",
        "device_count": 0,
        "gpu_name": "Python fallback",
        "memory_total": 0,
    }


def _python_get_pattern_stats(
    results: list[tuple[int, int, int, int]],
    num_texts: int,
    bytes_scanned: int,
) -> dict[str, Any]:
    """Get pattern scan statistics."""
    return {
        "total_matches": len(results),
        "texts_with_matches": len({r[0] for r in results}),
        "bytes_scanned": bytes_scanned,
    }


def check_metal_availability() -> dict[str, Any]:
    """Public API: check Metal/GPU availability."""
    result = _python_check_metal_availability()
    # R-4: Cross-process Metal busy check before dispatch
    try:
        from hledac.universal.brain.ane_embedder import _MLXFamilyMutex
        mutex = _MLXFamilyMutex()
        result["metal_busy_with_other_process"] = mutex.is_metal_busy_with_other_process
    except Exception:
        result["metal_busy_with_other_process"] = False
    return result
