"""
Forensics Enrichment Service
============================

Enriches accepted CanonicalFindings with forensics analysis.
Wraps UniversalMetadataExtractor, steganography_detector, and digital_ghost_detector.

Fail-safe: enrichment failures never crash the caller sprint.
Enrichment is best-effort — absence of forensics data is not an error.

Accepted findings with file-path in payload_text can be enriched with:
- Metadata extraction (EXIF, PDF, DOCX, audio, video, archive)
- Steganography analysis (LSB, histogram, chi-square)
- Digital ghost detection (deleted content, tampering, hidden data)

Integration:
    from forensics.enrichment_service import ForensicsEnricher

    enricher = ForensicsEnricher()
    await enricher.initialize()

    # enrich() returns enrichment dict or None (not a finding object)
    # Callers store the dict themselves (e.g., in LMDB keyed by finding_id)
    enrichment = await enricher.enrich(finding)
    if enrichment:
        await lmdb_store.put(finding.finding_id.encode(), enrichment)

    await enricher.close()

M1 8GB: All heavy dependencies (PIL, pypdf, docx, mutagen) are lazy-loaded
inside enrichment methods. Max 500MB memory per extraction.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import socket
import ssl
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

# Default timeout for external lookups (seconds)
_EXTERNAL_LOOKUP_TIMEOUT: float = 5.0

# Lazy-loaded forensics modules
_MetadataExtractor: Optional[type] = None
_METADATA_EXTRACTOR_AVAILABLE = False

_SteganalysisResult: Optional[type] = None
_STEGANOGRAPHY_AVAILABLE = False

_DigitalGhostResult: Optional[type] = None
_DIGITAL_GHOST_AVAILABLE = False

# Lazily-loaded standard library for WHOIS/SSL/DNS/rDNS


def _lazy_load_modules() -> None:
    """Load forensics modules lazily on first use."""
    global _MetadataExtractor, _METADATA_EXTRACTOR_AVAILABLE
    global _SteganalysisResult, _STEGANOGRAPHY_AVAILABLE
    global _DigitalGhostResult, _DIGITAL_GHOST_AVAILABLE

    if _MetadataExtractor is not None:
        return  # Already loaded

    # UniversalMetadataExtractor
    try:
        from forensics.metadata_extractor import UniversalMetadataExtractor
        _MetadataExtractor = UniversalMetadataExtractor
        _METADATA_EXTRACTOR_AVAILABLE = True
    except ImportError:
        _MetadataExtractor = None
        _METADATA_EXTRACTOR_AVAILABLE = False

    # SteganalysisResult
    try:
        from forensics.steganography_detector import SteganalysisResult
        _SteganalysisResult = SteganalysisResult
        _STEGANOGRAPHY_AVAILABLE = True
    except ImportError:
        _SteganalysisResult = None
        _STEGANOGRAPHY_AVAILABLE = False

    # DigitalGhostResult
    try:
        from forensics.digital_ghost_detector import DigitalGhostResult
        _DigitalGhostResult = DigitalGhostResult
        _DIGITAL_GHOST_AVAILABLE = True
    except ImportError:
        _DigitalGhostResult = None
        _DIGITAL_GHOST_AVAILABLE = False


# ---------------------------------------------------------------------------
# URL / path extraction from payload_text
# ---------------------------------------------------------------------------

# Supported file extensions for forensics enrichment
_SUPPORTED_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp", ".gif", ".webp",
    ".pdf", ".docx", ".doc",
    ".mp3", ".flac", ".ogg", ".m4a", ".wav", ".wma",
    ".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv",
    ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar",
}


def _extract_file_path_from_payload(payload_text: str | None) -> Optional[str]:
    """
    Extract a local file path from payload_text.

    Handles:
    - Direct local paths: /Users/.../file.jpg
    - file:// URLs: file:///tmp/file.pdf
    - Paths with query strings stripped

    Returns None if no suitable file path found or file doesn't exist.
    """
    if not payload_text:
        return None

    # Try file:// URL
    if payload_text.startswith("file://"):
        path_str = payload_text[7:]
        # Strip query/fragment
        path_str = path_str.split("?")[0].split("#")[0]
        path = Path(path_str)
        if path.exists() and path.is_file():
            return str(path)

    # Try direct path
    path = Path(payload_text)
    if not path.is_absolute():
        # Try as relative path from current dir
        path = Path.cwd() / path
    if path.exists() and path.is_file():
        return str(path)

    # Try stripping query strings from URL paths
    clean = payload_text.split("?")[0].split("#")[0]
    if clean != payload_text:
        return _extract_file_path_from_payload(clean)

    return None


def _file_has_forensics_support(file_path: str) -> bool:
    """Check if file extension is supported by forensics enrichment."""
    ext = Path(file_path).suffix.lower()
    return ext in _SUPPORTED_EXTENSIONS


# ---------------------------------------------------------------------------
# Domain extraction from URL payload_text
# ---------------------------------------------------------------------------

def _extract_domain_from_url(url: str | None) -> Optional[str]:
    """
    Extract domain from a URL string.


    Handles:
    - https://example.com/path
    - https://www.example.com/page.html
    - http://sub.domain.example.com:8080/path?query=1

    Returns None if no valid domain found.
    """
    if not url:
        return None
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        if parsed.netloc:
            # Remove port and strip www. prefix for uniformity
            host = parsed.netloc.split(":")[0]
            if host.startswith("www."):
                host = host[4:]
            return host
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# ForensicsResult — typed enrichment result for canonical findings
# ---------------------------------------------------------------------------

@dataclass
class ForensicsResult:
    """
    Sprint F198B: Typed forensics enrichment result.


    Produced by ForensicsEnricher.enrich() and stored in
    finding.metadata["forensics"] on canonical findings.


    Fields:
        finding_id:          Finding identifier
        file_path:           Local file path if enrichable, None otherwise
        whois:               WHOIS lookup result dict or None
        ssl:                 SSL certificate info dict or None
        dns:                 DNS A/AAAA records dict or None
        rdns:                Reverse DNS result dict or None
        enrichment_available: True if any enrichment succeeded

    All lookup fields are None on failure (graceful fallback).
    Never raises — enrichment is best-effort.
    """

    finding_id: str
    file_path: Optional[str] = None
    whois: Optional[dict[str, Any]] = None
    ssl: Optional[dict[str, Any]] = None
    dns: Optional[dict[str, Any]] = None
    rdns: Optional[dict[str, Any]] = None
    enrichment_available: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for storage in finding.metadata."""
        return {
            "finding_id": self.finding_id,
            "file_path": self.file_path,
            "whois": self.whois,
            "ssl": self.ssl,
            "dns": self.dns,
            "rdns": self.rdns,
            "enrichment_available": self.enrichment_available,
        }

# ---------------------------------------------------------------------------
# ForensicsEnricher
# ---------------------------------------------------------------------------

class ForensicsEnricher:
    """
    Forensics enrichment for CanonicalFindings.

    Enriches findings with file-path in payload_text via:
    - UniversalMetadataExtractor: EXIF, PDF, DOCX, audio, video, archive metadata
    - Steganography analysis: LSB, histogram, chi-square for images
    - Digital ghost detection: deleted content, tampering, hidden data

    Fail-safe: all methods are wrapped in try/except.
    Enrichment failures log a warning and return None — never raise.

    M1 8GB: Extractor uses streaming for large files, bounded memory.
    """

    def __init__(
        self,
        cache_path: Optional[str] = None,
        enable_gps: bool = True,
        enable_audio: bool = True,
        enable_video: bool = False,
    ):
        """
        Initialize enricher.

        Args:
            cache_path: Path to SQLite cache for metadata (None = in-memory).
            enable_gps: Extract GPS coordinates from EXIF.
            enable_audio: Extract audio metadata.
            enable_video: Extract video metadata (requires ffmpeg).
        """
        self._extractor: Optional[Any] = None
        self._cache_path = cache_path
        self._enable_gps = enable_gps
        self._enable_audio = enable_audio
        self._enable_video = enable_video
        self._initialized = False
        self._lock = asyncio.Lock()

    async def _ensure_initialized(self) -> None:
        """Ensure extractor is initialized (idempotent)."""
        if self._initialized and self._extractor is not None:
            return
        async with self._lock:
            if self._initialized and self._extractor is not None:
                return
            _lazy_load_modules()
            if _MetadataExtractor is not None:
                self._extractor = _MetadataExtractor(
                    cache_path=self._cache_path,
                    enable_exif=True,
                    enable_gps=self._enable_gps,
                    enable_reverse_geocode=False,
                    enable_audio=self._enable_audio,
                    enable_video=self._enable_video,
                    calculate_hashes=True,
                )
                await self._extractor.initialize()  # type: ignore[optional-member]
            self._initialized = True

    async def initialize(self) -> None:
        """Public initialize — delegates to _ensure_initialized."""
        await self._ensure_initialized()

    async def close(self) -> None:
        """Close extractor and cleanup resources."""
        async with self._lock:
            if self._extractor is not None:
                await self._extractor.close()
                self._extractor = None
            self._initialized = False

    async def enrich(self, finding: Any) -> Optional[dict[str, Any]]:
        """
        Enrich a CanonicalFinding with forensics analysis.

        Extracts file path from finding.payload_text and runs:
        1. Metadata extraction (UniversalMetadataExtractor) — file only
        2. Steganography analysis (images only) — file only
        3. Digital ghost detection — file only
        4. WHOIS/SSL/DNS/rDNS — domain extracted from URL payload_text

        Args:
            finding: A CanonicalFinding (or any object with
                     finding_id, payload_text, source_type attributes).

        Returns:
            Enrichment dict with keys:
            - "forensics": ForensicsResult.to_dict() with all lookup results
            - "file_path": the extracted file path or None
            - "enrichment_available": True if any enrichment succeeded

            Returns None if no enrichable target found or all enrichment failed.
            Never raises — failures return None with a warning log.
        """
        if not self._initialized:
            await self._ensure_initialized()

        # Extract file path from payload_text
        payload_text = getattr(finding, "payload_text", None)
        file_path = _extract_file_path_from_payload(payload_text)
        domain: Optional[str] = None

        if not file_path:
            # Sprint F198B: try extracting domain from URL payload for external lookups
            domain = _extract_domain_from_url(payload_text)

        finding_id = getattr(finding, "finding_id", "unknown")
        enrichment: dict[str, Any] = {
            "finding_id": finding_id,
            "file_path": file_path,
            "metadata": None,
            "steganography": None,
            "ghosts": None,
            "enrichment_available": False,
        }

        # Sprint F198B: Build typed ForensicsResult
        forensics_result = ForensicsResult(
            finding_id=finding_id,
            file_path=file_path,
            enrichment_available=False,
        )

        # 1. Metadata extraction (file only)
        if file_path and self._extractor is not None:
            if _file_has_forensics_support(file_path):
                try:
                    result = await self._extractor.extract(file_path)
                    if result is not None:
                        enrichment["metadata"] = result.to_dict()
                except Exception as exc:
                    log.debug("Forensics metadata extraction failed for %s: %s", finding_id, exc)

        # 2. Steganography analysis (images only)
        if file_path and _STEGANOGRAPHY_AVAILABLE:
            ext = Path(file_path).suffix.lower()
            if ext in {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tiff", ".tif", ".webp"}:
                try:
                    from forensics.steganography_detector import analyze_image_steganography
                    stego_result = analyze_image_steganography(file_path)
                    if stego_result is not None:
                        enrichment["steganography"] = stego_result.to_dict()
                except Exception as exc:
                    log.debug("Steganography analysis failed for %s: %s", finding_id, exc)

        # 3. Digital ghost detection (file only)
        if file_path and _DIGITAL_GHOST_AVAILABLE:
            try:
                from forensics.digital_ghost_detector import analyze_file_ghosts
                ghost_result = analyze_file_ghosts(file_path)
                if ghost_result is not None:
                    enrichment["ghosts"] = ghost_result.to_dict()
            except Exception as exc:
                log.debug("Digital ghost detection failed for %s: %s", finding_id, exc)

        # 4. Sprint F198B: External lookups (domain from URL)
        if domain:
            whois_data = await self._whois_lookup(domain)
            if whois_data:
                forensics_result.whois = whois_data
                forensics_result.enrichment_available = True

            ssl_data = await self._ssl_lookup(domain, 443)
            if ssl_data:
                forensics_result.ssl = ssl_data
                forensics_result.enrichment_available = True

            dns_data = await self._dns_lookup(domain)
            if dns_data:
                forensics_result.dns = dns_data
                forensics_result.enrichment_available = True

            rdns_data = await self._rdns_lookup(domain)
            if rdns_data:
                forensics_result.rdns = rdns_data
                forensics_result.enrichment_available = True

        # Mark enrichment available if any module produced data
        if any(v is not None for k, v in enrichment.items() if k not in ("finding_id", "file_path", "enrichment_available")):
            enrichment["enrichment_available"] = True
            forensics_result.enrichment_available = True

        if not forensics_result.enrichment_available:
            return None

        # Sprint F224F: Compute FOCA confidence modifier from enrichment metadata
        foca_modifier = self._score_foca_findings(enrichment)
        enrichment["foca_confidence_modifier"] = foca_modifier

        # Sprint F198B: inject forensics result into finding.metadata["forensics"]
        enrichment["forensics"] = forensics_result.to_dict()

        # Also inject into the finding object itself if it has a metadata dict
        if hasattr(finding, "metadata") and isinstance(finding.metadata, dict):
            finding.metadata["forensics"] = forensics_result.to_dict()

        return enrichment

    async def enrich_batch(self, findings: list[Any]) -> dict[str, dict[str, Any]]:
        """
        Enrich multiple findings concurrently.

        Args:
            findings: List of CanonicalFinding objects.

        Returns:
            Dict mapping finding_id -> enrichment dict (or empty if failed).
            Failures are silent — only successful enrichments are returned.
        """
        if not findings:
            return {}

        semaphore = asyncio.Semaphore(3)  # Max 3 concurrent enrichments (M1 8GB safe)

        async def enrich_one(finding: Any) -> tuple[str, Optional[dict[str, Any]]]:
            async with semaphore:
                finding_id = getattr(finding, "finding_id", "unknown")
                try:
                    result = await self.enrich(finding)
                    return (finding_id, result)
                except Exception as exc:
                    log.debug("Batch enrichment failed for %s: %s", finding_id, exc)
                    return (finding_id, None)

        tasks = [enrich_one(f) for f in findings]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        out = {}
        for item in results:
            if isinstance(item, Exception):
                continue
            fid, enrich_data = item
            if enrich_data is not None:
                out[fid] = enrich_data

        return out

    def _score_foca_findings(self, enrichment: Optional[dict[str, Any]]) -> float:
        """
        FOCA Step 3: Score FOCA findings for confidence integration.

        Enriches the confidence scoring pipeline with FOCA-specific signals:
        - PPTX: macros, hidden slides, speaker notes, template paths
        - Email: originating IP, attachments, DKIM/SPF results
        - CAD: autocad version, coordinate extents

        This bridges FOCA metadata into the confidence_policy.compute_confidence()
        seam used by the broader pipeline.

        Args:
            enrichment: Enrichment dict from enrich() containing 'metadata' with FOCA data

        Returns:
            FOCA-specific confidence modifier in [0.0, 0.3] to be added to base confidence
        """
        if not enrichment:
            return 0.0

        score = 0.0
        metadata = enrichment.get("metadata")
        if not metadata:
            return 0.0

        # PPTX signals: macro URLs are high-confidence indicators
        pptx = metadata.get("pptx")
        if pptx:
            if pptx.get("macro_urls"):
                score += 0.1
            if pptx.get("has_macros"):
                score += 0.05
            if pptx.get("hidden_slides"):
                score += 0.05  # Hidden content suggests intentional obfuscation
            if pptx.get("template_path"):
                score += 0.05  # Template tracking is forensic signal

        # Email signals: infrastructure indicators
        email = metadata.get("email")
        if email:
            if email.get("originating_ip"):
                score += 0.1  # Traceable infrastructure
            if email.get("dkim_domain") or email.get("spf_result"):
                score += 0.05  # Authentication signals
            if email.get("attachment_count", 0) > 0:
                score += 0.05  # Attachments are IOCs

        # CAD signals: technical drawings are high-value
        cad = metadata.get("cad")
        if cad:
            if cad.get("autocad_version"):
                score += 0.1  # Specific version is identifiable
            if cad.get("coordinate_extents"):
                score += 0.05  # Geolocation possible

        return min(score, 0.3)  # Cap at 0.3 to avoid over-weighting

    # ── Sprint F198B: External lookups (WHOIS/SSL/DNS/rDNS) ─────────────────

    async def _whois_lookup(self, domain: str) -> Optional[dict[str, Any]]:
        """
        Sprint F198B: WHOIS lookup with timeout + graceful fallback.

        Args:
            domain: Domain name to lookup

        Returns:
            WHOIS result dict or None on timeout/failure (fail-soft).
        """
        if not domain:
            return None

        try:
            import whois as _whois_pkg

            def _sync_whois() -> dict[str, Any]:
                try:
                    # python-whois: main function is whois.whois()
                    w = _whois_pkg.whois(domain)
                    if w is None:
                        return {}
                    # Extract key fields
                    return {
                        "registrar": getattr(w, "registrar", None),
                        "creation_date": (
                            str(getattr(w, "creation_date", None)) if hasattr(w, "creation_date") else None
                        ),
                        "expiration_date": (
                            str(getattr(w, "expiration_date", None)) if hasattr(w, "expiration_date") else None
                        ),
                        "name_servers": list(getattr(w, "name_servers", []) or []),
                        "status": getattr(w, "status", None),
                        "dns_sec": getattr(w, "dns_sec", None),
                    }
                except Exception:
                    return {}

            result = await asyncio.wait_for(
                asyncio.to_thread(_sync_whois),
                timeout=_EXTERNAL_LOOKUP_TIMEOUT,
            )
            return result if result else None
        except (asyncio.TimeoutError, Exception):
            return None

    async def _ssl_lookup(self, hostname: str, port: int = 443) -> Optional[dict[str, Any]]:
        """
        Sprint F198B: SSL certificate info with timeout + graceful fallback.

        Args:
            hostname: Hostname to fetch SSL certificate from
            port: Port number (default 443)

        Returns:
            SSL info dict or None on timeout/failure (fail-soft).
        """
        if not hostname:
            return None

        try:
            def _sync_ssl() -> dict[str, Any]:
                try:
                    context = ssl.create_default_context()
                    context.check_hostname = False
                    context.verify_mode = ssl.CERT_NONE
                    with socket.create_connection((hostname, port), timeout=_EXTERNAL_LOOKUP_TIMEOUT) as sock:
                        with context.wrap_socket(sock, server_hostname=hostname) as ssock:
                                                    cert = ssock.getpeercert(binary_form=True)
                                                    digest = hashlib.sha256(cert).hexdigest() if cert else None
                                                    cipher = ssock.cipher()
                                                    return {
                                                        "cipher": cipher[0] if cipher else None,
                                                        "protocol": cipher[2] if cipher else None,
                                                        "sha256_fingerprint": digest,
                                                        "cert_start": ssock.getpeercert() if ssock else None,
                                                    }
                except Exception:
                    return {}

            result = await asyncio.wait_for(
                asyncio.to_thread(_sync_ssl),
                timeout=_EXTERNAL_LOOKUP_TIMEOUT,
            )
            return result if result else None
        except (asyncio.TimeoutError, Exception):
            return None

    async def _dns_lookup(self, domain: str) -> Optional[dict[str, Any]]:
        """
        Sprint F198B: DNS A/AAAA record lookup with timeout + graceful fallback.

        Args:
            domain: Domain name to resolve

        Returns:
            DNS result dict or None on timeout/failure (fail-soft).
        """
        if not domain:
            return None

        try:
            def _sync_dns() -> dict[str, Any]:
                try:
                    import dns.resolver

                    result: dict[str, Any] = {"a": [], "aaaa": [], "mx": [], "ns": []}
                    try:
                        a_records = dns.resolver.resolve(domain, "A", lifetime=_EXTERNAL_LOOKUP_TIMEOUT)
                        result["a"] = [str(r) for r in a_records]
                    except Exception:
                        pass
                    try:
                        aaaa_records = dns.resolver.resolve(domain, "AAAA", lifetime=_EXTERNAL_LOOKUP_TIMEOUT)
                        result["aaaa"] = [str(r) for r in aaaa_records]
                    except Exception:
                        pass
                    try:
                        mx_records = dns.resolver.resolve(domain, "MX", lifetime=_EXTERNAL_LOOKUP_TIMEOUT)
                        result["mx"] = [f"{r.preference} {r.exchange}" for r in mx_records]
                    except Exception:
                        pass
                    try:
                        ns_records = dns.resolver.resolve(domain, "NS", lifetime=_EXTERNAL_LOOKUP_TIMEOUT)
                        result["ns"] = [str(r) for r in ns_records]
                    except Exception:
                        pass
                    return result
                except Exception:
                    return {}

            result = await asyncio.wait_for(
                asyncio.to_thread(_sync_dns),
                timeout=_EXTERNAL_LOOKUP_TIMEOUT,
            )
            return result if result else None
        except (asyncio.TimeoutError, Exception):
            return None

    async def _rdns_lookup(self, ip_address: str) -> Optional[dict[str, Any]]:
        """
        Sprint F198B: Reverse DNS lookup with timeout + graceful fallback.

        Args:
            ip_address: IP address to reverse-lookup

        Returns:
            rDNS result dict {ip: hostname} or None on timeout/failure (fail-soft).
        """
        if not ip_address:
            return None

        try:
            def _sync_rdns() -> dict[str, Any]:
                try:
                    hostname, _, _ = socket.gethostbyaddr(ip_address)
                    return {ip_address: hostname}
                except Exception:
                    return {}

            result = await asyncio.wait_for(
                asyncio.to_thread(_sync_rdns),
                timeout=_EXTERNAL_LOOKUP_TIMEOUT,
            )
            return result if result else None
        except (asyncio.TimeoutError, Exception):
            return None
