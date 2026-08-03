"""IOC extraction domain — Rust backend bridge + pure Python fallback.

Canonical module (F350M-R). Wraps Rust extension or pure Python regex for:
  - extract_iocs(text) → dict[str, list[str]]
  - extract_iocs_flat(text) → list[(ioc_type, value)]
  - extract_iocs_simd(text) → list[(ioc_type, value)]
  - batch_extract_iocs(texts) → list[dict[...]]
  - batch_extract_iocs_simd(texts) → list[list[(ioc_type, value)]]
  - batch_extract_iocs_simd_indexed(texts) → list[(text_idx, value, ioc_type)]

F1.2 root fix: Rust IOC_META_REGEX fails to init (pattern error) → all SIMD
methods return []. Fixed by delegating to forensics/ioc_extractor Python fallback
which uses the same combined regex but bypasses broken Rust path.
"""


import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hledac_rust_extensions import hledac_rust_extensions

# M1-OPT: Use shared domain executor instead of per-module TPE
# crypto preset = 1 worker (CPU-bound: yara-python, Pycryptodome)
from concurrent.futures import ThreadPoolExecutor

from hledac.universal.utils.domain_executors import get_or_create


def _get_executor() -> ThreadPoolExecutor:
    """Return shared 'crypto' domain executor for CPU-bound IOC extraction."""
    return get_or_create("crypto")

_PY_IPV4_RE = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b")
_PY_DOMAIN_RE = re.compile(r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}\b")
_PY_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_PY_URL_RE = re.compile(r"https?://[^\s<>\"]+")
_PY_HASH_RE = re.compile(r"\b[a-fA-F0-9]{32}\b|\b[a-fA-F0-9]{40}\b|\b[a-fA-F0-9]{64}\b")


# ISSUE-4 FIX: Shared batch helper to eliminate duplicate `if not texts` guards.
# Both _RustIocDomain and _PythonIocDomain use the same pattern for parallel batch ops.
from typing import Any, Callable


def _batch_extract_iocs_helper(
    texts: list[str], func: Callable[[str], Any]
) -> list[Any]:
    """Shared batch extraction helper with early-exit guard."""
    if not texts:
        return []
    ex = _get_executor()
    return list(ex.map(func, texts))


def _batch_extract_iocs_indexed_helper(
    texts: list[str], func: Callable[[tuple[int, str]], list[tuple[int, str, str]]]
) -> list[tuple[int, str, str]]:
    """Shared indexed batch extraction helper with early-exit guard."""
    if not texts:
        return []
    indexed: list[tuple[int, str]] = list(enumerate(texts))
    ex = _get_executor()
    return [row for rows in ex.map(func, indexed) for row in rows]


class _RustIocDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: hledac_rust_extensions) -> None:
        self._ext = ext

    def extract_iocs(self, text: str) -> dict[str, list[str]]:
        return self._ext.extract_iocs(text)

    def batch_extract_iocs(self, texts: list[str]) -> list[dict[str, list[str]]]:
        # F1.2 root fix: Rust batch_extract_iocs_simd is broken (IOC_META_REGEX init fails).
        # Use parallel Python fallback via shared executor.
        return _batch_extract_iocs_helper(texts, _python_extract_iocs)

    def nfc_normalize(self, text: str) -> str:
        return self._ext.nfc_normalize(text)

    def extract_iocs_flat(self, text: str) -> list[tuple[str, str]]:
        return self._ext.extract_iocs_flat(text)

    def batch_nfc_normalize_fast(self, texts: list[str]) -> list[str]:
        return self._ext.batch_nfc_normalize_fast(texts)

    def batch_strip_diacritics_fast(self, texts: list[str]) -> list[str]:
        return self._ext.batch_strip_diacritics_fast(texts)

    def extract_iocs_simd(self, text: str) -> list[tuple[str, str]]:
        # F1.2 root fix: Rust extract_iocs_simd broken (IOC_META_REGEX init fails).
        # Delegate to forensics/ioc_extractor Python fallback.
        return _python_extract_iocs_simd_single(text)

    def batch_extract_iocs_simd(self, texts: list[str]) -> list[list[tuple[str, str]]]:
        # F1.2 root fix: Rust batch broken. Use parallel Python fallback.
        return _batch_extract_iocs_helper(texts, _python_extract_iocs_simd_single)

    def batch_extract_iocs_simd_indexed(self, texts: list[str]) -> list[tuple[int, str, str]]:
        # F1.2 root fix: Rust indexed batch broken. Use parallel Python fallback.
        return _batch_extract_iocs_indexed_helper(texts, _python_extract_iocs_flat_indexed)

    def batch_dedup_urls(self, urls: list[str]) -> list[str]:
        """Deduplicate URLs — delegates to Rust standalone function in ioc_extract module."""
        return self._ext.batch_dedup_urls(urls)

    # ADVERSARY-003: CyberChef-Pipeline — recursive IOC deobfuscation before SIMD scan.
    # decode_ioc_candidates peels encoding layers (Base64/Hex/Base58/URL/ROT13/XOR)
    # from high-entropy regions in text, exposing hidden IOCs to the regex engine.
    def decode_ioc_candidates(self, text: str, max_depth: int | None = None) -> list[str]:
        result = self._ext.deobfuscate.decode_ioc_candidates(text, max_depth)
        return result.candidates if hasattr(result, "candidates") else []

    def batch_decode_ioc_candidates(
        self, texts: list[str], max_depth: int | None = None
    ) -> list[list[str]]:
        results = self._ext.deobfuscate.batch_decode_ioc_candidates(texts, max_depth)
        return [r.candidates if hasattr(r, "candidates") else [] for r in results]


class _PythonIocDomain:
    __slots__ = ()

    @staticmethod
    def extract_iocs(text: str) -> dict[str, list[str]]:
        return _python_extract_iocs(text)

    @staticmethod
    def batch_extract_iocs(texts: list[str]) -> list[dict[str, list[str]]]:
        return [_python_extract_iocs(t) for t in texts]

    @staticmethod
    def nfc_normalize(text: str) -> str:
        return _python_nfc_normalize(text)

    @staticmethod
    def extract_iocs_flat(text: str) -> list[tuple[str, str]]:
        return _python_extract_iocs_flat(text)

    @staticmethod
    def batch_nfc_normalize_fast(texts: list[str]) -> list[str]:
        return [_python_nfc_normalize(t) for t in texts]

    @staticmethod
    def batch_strip_diacritics_fast(texts: list[str]) -> list[str]:
        return [_python_strip_diacritics(t) for t in texts]

    @staticmethod
    def extract_iocs_simd(text: str) -> list[tuple[str, str]]:
        return _python_extract_iocs_simd_single(text)

    @staticmethod
    def batch_extract_iocs_simd(texts: list[str]) -> list[list[tuple[str, str]]]:
        # B2 fix: parallel via ThreadPoolExecutor — shared helper eliminates duplicate guard.
        return _batch_extract_iocs_helper(texts, _python_extract_iocs_simd_single)

    @staticmethod
    def batch_extract_iocs_simd_indexed(texts: list[str]) -> list[tuple[int, str, str]]:
        # B2 fix: parallel via ThreadPoolExecutor — shared helper eliminates duplicate guard.
        return _batch_extract_iocs_indexed_helper(texts, _python_extract_iocs_flat_indexed)

    @staticmethod
    def batch_dedup_urls(urls: list[str]) -> list[str]:
        """Deduplicate URLs — pure Python fallback."""
        return _python_batch_dedup_urls(urls)

    # ADVERSARY-003: Python fallback — deobfuscation not available in Python path.
    # Returns empty list (deobfuscation requires Rust rayon pool + SIMD).
    @staticmethod
    def decode_ioc_candidates(text: str, max_depth: int | None = None) -> list[str]:
        return []

    @staticmethod
    def batch_decode_ioc_candidates(
        texts: list[str], max_depth: int | None = None
    ) -> list[list[str]]:
        return [[] for _ in texts]

    # ADVERSARY-003: module-level telemetry helpers (work for both Rust and Python paths).
    def deobfuscate_telemetry() -> tuple[int, int, int]:
        """Return (passes, layers_stripped, bytes_decoded) counters.
        Returns (0,0,0) when Rust extension is unavailable."""
        try:
            from hledac_rust_extensions import hledac_rust_extensions
            return hledac_rust_extensions.deobfuscate_telemetry()  # type: ignore[attr-defined]
        except Exception:
            return (0, 0, 0)

    def deobfuscate_telemetry_reset() -> None:
        """Reset telemetry counters. Call at sprint boundary."""
        try:
            from hledac_rust_extensions import hledac_rust_extensions
            hledac_rust_extensions.deobfuscate_telemetry_reset()  # type: ignore[attr-defined]
        except Exception:
            pass


_PY_IPV6_RE: re.Pattern | None = None
_PY_MD5_RE: re.Pattern | None = None
_PY_SHA1_RE: re.Pattern | None = None
_PY_SHA256_RE: re.Pattern | None = None
try:
    _PY_IPV6_RE = re.compile(
        r"(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}"
        r"|(?:[0-9a-fA-F]{1,4}:){1,7}:"
        r"|(?:[0-9a-fA-F]{1,4}:){1,6}:[0-9a-fA-F]{1,4}"
        r"|(?:[0-9a-fA-F]{1,4}:){1,5}(?::[0-9a-fA-F]{1,4}){1,2}"
        r"|(?:[0-9a-fA-F]{1,4}:){1,4}(?::[0-9a-fA-F]{1,4}){1,3}"
        r"|(?:[0-9a-fA-F]{1,4}:){1,3}(?::[0-9a-fA-F]{1,4}){1,4}"
        r"|(?:[0-9a-fA-F]{1,4}:){1,2}(?::[0-9a-fA-F]{1,4}){1,5}"
        r"|[0-9a-fA-F]{1,4}:(?:(?::[0-9a-fA-F]{1,4}){1,6})"
        r"|:(?:(?::[0-9a-fA-F]{1,4}){1,7}|:)"
        r"|fe80:(?::[0-9a-fA-F]{0,4}){0,4}%[0-9a-zA-Z]+"
        r"|::(?:ffff(?::0{1,4})?:)?(?:(?:25[0-5]|(?:2[0-4]|1?\d)?\d)(?:\.(?:25[0-5]|(?:2[0-4]|1?\d)?\d)){3})"
        r"|(?:[0-9a-fA-F]{1,4}:){1,4}:(?:(?:25[0-5]|(?:2[0-4]|1?\d)?\d)(?:\.(?:25[0-5]|(?:2[0-4]|1?\d)?\d)){3})"
    )
    _PY_MD5_RE = re.compile(r"\b[a-fA-F0-9]{32}\b")
    _PY_SHA1_RE = re.compile(r"\b[a-fA-F0-9]{40}\b")
    _PY_SHA256_RE = re.compile(r"\b[a-fA-F0-9]{64}\b")
except Exception:
    pass


def _python_extract_iocs_flat_indexed(text_with_idx: tuple[int, str]) -> list[tuple[int, str, str]]:
    """Extract IOCs from text, returning (text_idx, ioc_value, ioc_type).

    Accepts (text_idx, text) tuple — matches ThreadPoolExecutor.map() signature.
    B2 fix: all 8 IOC types extracted (URL/EMAIL/IPv4/IPv6/DOMAIN/MD5/SHA1/SHA256).
    """
    idx, text = text_with_idx
    result: list[tuple[int, str, str]] = []
    if not text:
        return result
    try:
        for value in _PY_URL_RE.findall(text):
            result.append((idx, value, "url"))
        for value in _PY_EMAIL_RE.findall(text):
            result.append((idx, value.lower(), "email"))
        for value in _PY_IPV4_RE.findall(text):
            result.append((idx, value, "ipv4"))
        if _PY_IPV6_RE is not None:
            for value in _PY_IPV6_RE.findall(text):
                result.append((idx, value.lower(), "ipv6"))
        for value in _PY_DOMAIN_RE.findall(text):
            result.append((idx, value.lower(), "domain"))
        for value in _PY_MD5_RE.findall(text):
            result.append((idx, value.lower(), "md5"))
        for value in _PY_SHA1_RE.findall(text):
            result.append((idx, value.lower(), "sha1"))
        for value in _PY_SHA256_RE.findall(text):
            result.append((idx, value.lower(), "sha256"))
    except Exception:
        pass
    return result


def _python_extract_iocs_simd_single(text: str) -> list[tuple[str, str]]:
    """Extract flat IOC list [(ioc_type, value), ...] using forensics/ioc_extractor.

    F1.2 root fix: Uses forensics/ioc_extractor._IOC_COMBINED (named-group
    single-pass regex) so hash types are correctly classified by length.
    """
    try:
        from forensics.ioc_extractor import _IOC_COMBINED

        results: list[tuple[str, str]] = []
        seen: set[str] = set()
        for m in _IOC_COMBINED.finditer(text):
            name = m.lastgroup
            if name is None:
                continue
            value = m.group()
            key = f"{name}:{value}"
            if key in seen:
                continue
            seen.add(key)
            if name.startswith("ipv6"):
                results.append((value.lower(), "ipv6"))
            elif name == "ipv4":
                results.append((value, "ipv4"))
            elif name in ("md5", "sha1", "sha256"):
                results.append((value.lower(), name))
            elif name == "email":
                results.append((value.lower(), name))
            else:
                results.append((value, name))
        return results
    except Exception:
        return []


def _python_extract_iocs(text: str) -> dict[str, list[str]]:
    """Pure-Python IOC extraction — uses forensics/ioc_extractor combined regex.

    F1.2 root fix: Uses forensics/ioc_extractor._IOC_COMBINED (named-group
    single-pass regex) so hash types are correctly classified by length.
    Pre-compiled at module load — no per-call re-compilation.
    """
    if not text:
        return {"urls": [], "domains": [], "emails": [], "ipv4s": [], "md5s": [], "sha1s": [], "sha256s": []}
    try:
        from forensics.ioc_extractor import _IOC_COMBINED

        seen: dict[str, set[str]] = {"urls": set(), "domains": set(), "emails": set(), "ipv4s": set()}
        all_hashes: set[str] = set()
        md5s: list[str] = []
        sha1s: list[str] = []
        sha256s: list[str] = []
        for m in _IOC_COMBINED.finditer(text):
            name = m.lastgroup
            if name is None:
                continue
            value = m.group()
            if name == "url":
                if value not in seen["urls"]:
                    seen["urls"].add(value)
            elif name in ("ipv4", "ipv6_full"):
                if value not in seen["ipv4s"]:
                    seen["ipv4s"].add(value)
            elif name == "domain":
                if value not in seen["domains"]:
                    seen["domains"].add(value)
            elif name == "email":
                if value not in seen["emails"]:
                    seen["emails"].add(value)
            elif name in ("md5", "sha1", "sha256"):
                lower_h = value.lower()
                if lower_h in all_hashes:
                    continue
                all_hashes.add(lower_h)
                length = len(lower_h)
                if length == 32:
                    md5s.append(lower_h)
                elif length == 40:
                    sha1s.append(lower_h)
                elif length == 64:
                    sha256s.append(lower_h)
        return {
            "urls": list(seen["urls"]),
            "domains": list(seen["domains"]),
            "emails": list(seen["emails"]),
            "ipv4s": list(seen["ipv4s"]),
            "md5s": md5s,
            "sha1s": sha1s,
            "sha256s": sha256s,
        }
    except Exception:
        return {"urls": [], "domains": [], "emails": [], "ipv4s": [], "md5s": [], "sha1s": [], "sha256s": []}


def _python_extract_iocs_flat(text: str) -> list[tuple[str, str]]:
    iocs = _python_extract_iocs(text)
    result: list[tuple[str, str]] = []
    for ioc_type, values in iocs.items():
        for v in values:
            result.append((ioc_type, v))
    return result


def _python_nfc_normalize(text: str) -> str:
    import unicodedata

    try:
        return unicodedata.normalize("NFC", text)
    except Exception:
        return text


def _python_batch_dedup_urls(urls: list[str]) -> list[str]:
    """Pure-Python URL dedup with normalization — mirrors Rust batch_dedup_urls."""
    from urllib.parse import parse_qsl, urlencode, urlparse
    seen: set[str] = set()
    result: list[str] = []
    for url in urls:
        try:
            trimmed = url.strip()
            if not trimmed:
                continue
            synthetic = trimmed if "://" in trimmed else f"http://{trimmed.lstrip('/')}"
            parsed = urlparse(synthetic)
            scheme = parsed.scheme.lower()
            host = parsed.hostname or ""
            port = parsed.port
            path = parsed.path or "/"
            if port == 80 and scheme == "http":
                port = None
            elif port == 443 and scheme == "https":
                port = None
            norm = f"{scheme}://{host}"
            if port:
                norm += f":{port}"
            norm += path
            params = [(k, v) for k, v in parse_qsl(parsed.query) if k not in {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid", "gclid", "ref", "source"}]
            params.sort()
            if params:
                norm += "?" + urlencode(params)
            if norm not in seen:
                seen.add(norm)
                result.append(norm)
        except Exception:
            if url not in seen:
                seen.add(url)
                result.append(url)
    return result


def _python_strip_diacritics(text: str) -> str:
    import unicodedata

    try:
        nfkd = unicodedata.normalize("NFKD", text)
        return "".join(c for c in nfkd if not unicodedata.combining(c))
    except Exception:
        return text


def get_domain(ext: object | None) -> _RustIocDomain | _PythonIocDomain:
    if ext is not None:
        return _RustIocDomain(ext)
    return _PythonIocDomain()
