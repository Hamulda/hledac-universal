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
import concurrent.futures
import hashlib
import logging
import socket
import ssl
from dataclasses import dataclass
import msgspec
from pathlib import Path
from typing import Any

from hledac.universal.utils.async_helpers import safe_gather_ok

log = logging.getLogger(__name__)

# Default timeout for external lookups (seconds)
_EXTERNAL_LOOKUP_TIMEOUT: float = 5.0

# Lazy-loaded forensics modules
_MetadataExtractor: type | None = None
_METADATA_EXTRACTOR_AVAILABLE = False

_SteganalysisResult: type | None = None
_STEGANOGRAPHY_AVAILABLE = False

_DigitalGhostResult: type | None = None
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

    # StegoResult (from forensics.stego_detector — canonical)
    try:
        from forensics.stego_detector import StegoResult
        _StegoResult = StegoResult
        _STEGANOGRAPHY_AVAILABLE = True
    except ImportError:
        _StegoResult = None
        _STEGANOGRAPHY_AVAILABLE = False

    # DigitalGhostAnalysis (from forensics.digital_ghost_detector — canonical)
    try:
        from forensics.digital_ghost_detector import DigitalGhostAnalysis
        _DigitalGhostResult = DigitalGhostAnalysis
        _DIGITAL_GHOST_AVAILABLE = True
    except ImportError:
        _DigitalGhostResult = None
        _DIGITAL_GHOST_AVAILABLE = False


# ---------------------------------------------------------------------------
# Adapters: security module -> forensics enrichment contract
# security/stego_detector uses async StatisticalStegoDetector.analyze_image()
# security/digital_ghost_detector uses sync DigitalGhostDetector.analyze_file()
# forensics wrapper results have .to_dict() — adapters bridge the gap
# ---------------------------------------------------------------------------

def _stego_result_to_dict(result: Any) -> dict[str, Any]:
    """Bridge security.stego_detector.StegoResult -> enrichment dict."""
    if result is None:
        return {}
    return {
        "has_stego": result.has_stego,
        "confidence": result.confidence,
        "method_used": result.method_used,
        "message_length_estimate": result.message_length_estimate,
        "chi_square": (
            {
                "p_value": result.chi_square.p_value,
                "chi_square_stat": result.chi_square.chi_square_stat,
                "embedded_bytes_estimate": result.chi_square.embedded_bytes_estimate,
                "is_significant": result.chi_square.is_significant,
            }
            if result.chi_square else None
        ),
        "rs_analysis": (
            {
                "message_length": result.rs_analysis.message_length,
                "confidence": result.rs_analysis.confidence,
            }
            if result.rs_analysis else None
        ),
        "dct_analysis": (
            {
                "anomaly_score": result.dct_analysis.anomaly_score,
                "histogram_deviation": result.dct_analysis.histogram_deviation,
            }
            if result.dct_analysis else None
        ),
        "details": result.details,
    }


async def _run_stego_analysis(file_path: str) -> dict[str, Any]:
    """Async wrapper for async StatisticalStegoDetector.analyze_image()."""
    try:
        from forensics.stego_detector import StatisticalStegoDetector, StegoConfig

        detector = StatisticalStegoDetector(StegoConfig())
        result = await detector.analyze_image(file_path)
        return _stego_result_to_dict(result)
    except Exception as exc:
        log.debug("Forensics stego analysis failed for %s: %s", file_path, exc)
        return {}


def _run_ghost_analysis(file_path: str) -> dict[str, Any]:
    """Bridge security.digital_ghost_detector.DigitalGhostAnalysis -> enrichment dict."""
    if _DigitalGhostResult is None:
        return {}
    try:
        detector = _DigitalGhostResult()
        result = detector.analyze_file(file_path)
        if result is None:
            return {}
        return {
            "target": result.target,
            "ghost_signals": [
                {
                    "signal_type": s.signal_type,
                    "location": s.location,
                    "confidence": s.confidence,
                    "content_snippet": s.content_snippet,
                    "indicators": s.indicators,
                }
                for s in result.ghost_signals
            ],
            "recovered_content": [
                {
                    "original_location": c.original_location,
                    "recovered_text": c.recovered_text,
                    "confidence": c.confidence,
                    "recovery_method": c.recovery_method,
                }
                for c in result.recovered_content
            ],
            "overall_confidence": result.overall_confidence,
            "recommendations": result.recommendations,
        }
    except Exception as exc:
        log.debug("Forensics ghost analysis failed for %s: %s", file_path, exc)
        return {}


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


def _extract_file_path_from_payload(payload_text: str | None) -> str | None:
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

def _extract_domain_from_url(url: str | None) -> str | None:
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
    except Exception:  # noqa: BLE001
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
    file_path: str | None = None
    whois: dict[str, Any] | None = None
    ssl: dict[str, Any] | None = None
    dns: dict[str, Any] | None = None
    rdns: dict[str, Any] | None = None
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
        cache_path: str | None = None,
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
        self._extractor: Any | None = None
        self._cache_path = cache_path
        self._enable_gps = enable_gps
        self._enable_audio = enable_audio
        self._enable_video = enable_video
        self._initialized = False
        self._lock = asyncio.Lock()
        # Issue #13: bounded executor for sync ghost analysis (M1 8GB: 2 workers)
        self._ghost_executor: concurrent.futures.ThreadPoolExecutor | None = None

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
            if self._ghost_executor is not None:
                self._ghost_executor.shutdown(wait=False)
                self._ghost_executor = None
            self._initialized = False

    async def _run_ghost_analysis_async(self, file_path: str) -> dict[str, Any]:
        """Async wrapper for sync _run_ghost_analysis via ThreadPoolExecutor.

        Issue #13: runs in executor to avoid blocking the event loop.
        M1 8GB: max_workers=2 keeps CPU utilization bounded.
        """
        if self._ghost_executor is None:
            self._ghost_executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=2, thread_name_prefix="ghost_"
            )
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._ghost_executor, _run_ghost_analysis, file_path)

    async def enrich(self, finding: Any) -> dict[str, Any] | None:
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
        domain: str | None = None

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
                    stego_data = await _run_stego_analysis(file_path)
                    if stego_data:
                        enrichment["steganography"] = stego_data
                except Exception as exc:
                    log.debug("Steganography analysis failed for %s: %s", finding_id, exc)

        # 3. Digital ghost detection (file only) — Issue #13: async via executor
        if file_path and _DIGITAL_GHOST_AVAILABLE:
            try:
                ghost_data = await self._run_ghost_analysis_async(file_path)
                if ghost_data:
                    enrichment["ghosts"] = ghost_data
            except Exception as exc:
                log.debug("Digital ghost detection failed for %s: %s", finding_id, exc)

        # 4. Issue #13: Parallel WHOIS/SSL/DNS/rDNS via TaskGroup
        # Speedup: 4× serial (8-20s) → parallel (~max) ≈ 5s
        if domain:
            try:
                async with asyncio.TaskGroup() as tg:
                    t_whois = tg.create_task(self._whois_lookup(domain), name="forensics:whois")
                    t_ssl = tg.create_task(self._ssl_lookup(domain, 443), name="forensics:ssl")
                    t_dns = tg.create_task(self._dns_lookup(domain), name="forensics:dns")
                    t_rdns = tg.create_task(self._rdns_lookup(domain), name="forensics:rdns")
                whois_data = t_whois.result()
                ssl_data = t_ssl.result()
                dns_data = t_dns.result()
                rdns_data = t_rdns.result()
            except* (asyncio.TimeoutError, OSError, socket.gaierror) as eg:
                log.debug("[FORENSICS] parallel domain lookup timeout/DNS error: %s", eg)
                whois_data = ssl_data = dns_data = rdns_data = None
            except* Exception as eg:
                # Unexpected error: surface it but fail-soft
                first_exc = eg.exceptions[0] if eg.exceptions else eg
                log.debug("[FORENSICS] parallel domain lookup unexpected error: %s", first_exc)
                whois_data = ssl_data = dns_data = rdns_data = None

            if whois_data:
                forensics_result.whois = whois_data
                forensics_result.enrichment_available = True
            if ssl_data:
                forensics_result.ssl = ssl_data
                forensics_result.enrichment_available = True
            if dns_data:
                forensics_result.dns = dns_data
                forensics_result.enrichment_available = True
            if rdns_data:
                forensics_result.rdns = rdns_data
                forensics_result.enrichment_available = True

        # F3FORENSICS: FOCA x_originating_ip bridge — Issue #13: parallel WHOIS + rDNS
        if hasattr(finding, 'payload'):
            payload = finding.payload or {}
            email_meta = payload.get('email_metadata', {}) or payload.get('email', {})
            x_originating_ip = email_meta.get('originating_ip') or email_meta.get('x_originating_ip')
            if x_originating_ip:
                try:
                    import ipaddress
                    ip = ipaddress.ip_address(x_originating_ip)
                    if not ip.is_private and not ip.is_loopback and not ip.is_reserved:
                        # Issue #13: parallel WHOIS + rDNS for x_originating_ip
                        try:
                            async with asyncio.TaskGroup() as tg:
                                t_whois_ip = tg.create_task(self._whois_lookup(x_originating_ip), name="forensics:xip_whois")
                                t_rdns_ip = tg.create_task(self._rdns_lookup(x_originating_ip), name="forensics:xip_rdns")
                            whois_ip_data = t_whois_ip.result()
                            rdns_ip_data = t_rdns_ip.result()
                        except* (asyncio.TimeoutError, OSError, socket.gaierror) as eg:
                            log.debug("[FORENSICS] x_originating_ip lookup timeout/DNS error: %s", eg)
                            whois_ip_data = rdns_ip_data = None
                        except* Exception as eg:
                            first_exc = eg.exceptions[0] if eg.exceptions else eg
                            log.debug("[FORENSICS] x_originating_ip lookup unexpected error: %s", first_exc)
                            whois_ip_data = rdns_ip_data = None

                        if whois_ip_data or rdns_ip_data:
                            enrichment['x_originating_ip_enrichment'] = {
                                'ip': x_originating_ip,
                                'whois': whois_ip_data,
                                'rdns': rdns_ip_data,
                            }
                            forensics_result.enrichment_available = True
                except Exception:  # noqa: BLE001
                    pass  # noqa: BLE001  # Fail-soft: invalid IP or lookup failed

        # Sprint F262: Sub-step 6 — IOC extraction from payload_text + email IP
        # Emits per-IOC CanonicalFinding objects into enrichment["_ioc_canonical_findings"]
        # (consumed downstream by EnrichmentServices.enrich_one for batched
        # async_ingest_findings_batch(parent + IOC children)).
        # Fail-soft, bounded by IOC_FINDINGS_MAX (per-finding) + global budget.
        try:
            from forensics.ioc_extractor import ioc_extract_to_canonical_findings

            ioc_text_parts: list[str] = []
            if payload_text:
                ioc_text_parts.append(str(payload_text)[:8192])  # bound scan length
            if x_originating_ip:
                ioc_text_parts.append(str(x_originating_ip))
            ioc_text = "\n".join(ioc_text_parts) if ioc_text_parts else ""

            if ioc_text:
                finding_id = getattr(finding, "finding_id", None) or "unknown"
                finding_query = getattr(finding, "query", "") or ""
                # budget_remaining is provided by EnrichmentServices when
                # SprintResult.ioc_findings_total is available; else defaults.
                ioc_findings = ioc_extract_to_canonical_findings(
                    text=ioc_text,
                    source_finding_id=str(finding_id)[:128],
                    query=str(finding_query)[:512],
                )
                if ioc_findings:
                    # Stash raw CanonicalFinding list for downstream batched write
                    enrichment["_ioc_canonical_findings"] = ioc_findings
                    # Bounded dict summary for LMDB payload (no CanonicalFinding
                    # serialization, just the IOC content for replay/inspect)
                    enrichment["ioc_findings"] = [
                        {
                            "finding_id": getattr(cf, "finding_id", ""),
                            "ioc_type": (getattr(cf, "payload_text", "") or "")
                            .split(";", 1)[0]
                            .replace("ioc_type=", "")
                            .strip(),
                            "value": (getattr(cf, "payload_text", "") or "")
                            .split(";", 1)[1]
                            .replace("value=", "")
                            .strip()
                            if ";" in (getattr(cf, "payload_text", "") or "")
                            else "",
                        }
                        for cf in ioc_findings
                    ]
                    forensics_result.enrichment_available = True
        except Exception as exc:
            log.debug("Forensics IOC sub-step failed for %s: %s", finding_id, exc)

        # Mark enrichment available if any module produced data
        if any(v is not None for k, v in enrichment.items() if k not in ("finding_id", "file_path", "enrichment_available")):  # noqa: E501
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

        from hledac.universal.core.concurrency_registry import ConcurrencyCategory, get_semaphore_for_testing
        semaphore = get_semaphore_for_testing(ConcurrencyCategory.GRAPH_RAG)

        async def enrich_one(finding: Any) -> tuple[str, dict[str, Any] | None]:
            async with semaphore:
                finding_id = getattr(finding, "finding_id", "unknown")
                try:
                    result = await self.enrich(finding)
                    return (finding_id, result)
                except Exception as exc:
                    log.debug("Batch enrichment failed for %s: %s", finding_id, exc)
                    return (finding_id, None)

        tasks = [enrich_one(f) for f in findings]
        results = await safe_gather_ok(*tasks, label="enrichment_service:540")

        out = {}
        for item in results:
            if isinstance(item, Exception):
                continue
            fid, enrich_data = item
            if enrich_data is not None:
                out[fid] = enrich_data

        return out

    def _score_foca_findings(self, enrichment: dict[str, Any] | None) -> float:
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

    async def _whois_lookup(self, domain: str) -> dict[str, Any] | None:
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

            async with asyncio.timeout(_EXTERNAL_LOOKUP_TIMEOUT):
                from hledac.universal.runtime.worker_pool import io_bound
                result = await io_bound(_sync_whois)
            return result if result else None
        except (TimeoutError, Exception):
            return None

    async def _ssl_lookup(self, hostname: str, port: int = 443) -> dict[str, Any] | None:
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

            async with asyncio.timeout(_EXTERNAL_LOOKUP_TIMEOUT):
                from hledac.universal.runtime.worker_pool import io_bound
                result = await io_bound(_sync_ssl)
            return result if result else None
        except (TimeoutError, Exception):
            return None

    async def _dns_lookup(self, domain: str) -> dict[str, Any] | None:
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
            # Sprint F207N-C: prefer dns.asyncresolver (non-blocking on M1, no thread slot).
            # Fallback to sync dns.resolver via asyncio.to_thread if async resolver unavailable.
            async def _async_dns() -> dict[str, Any]:
                try:
                    import dns.asyncresolver
                    resolver = dns.asyncresolver.Resolver()
                    resolver.lifetime = _EXTERNAL_LOOKUP_TIMEOUT
                    resolver.timeout = _EXTERNAL_LOOKUP_TIMEOUT
                    result: dict[str, Any] = {"a": [], "aaaa": [], "mx": [], "ns": []}
                    try:
                        ans = await resolver.resolve(domain, "A")
                        result["a"] = [str(r) for r in ans]
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        ans = await resolver.resolve(domain, "AAAA")
                        result["aaaa"] = [str(r) for r in ans]
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        ans = await resolver.resolve(domain, "MX")
                        result["mx"] = [f"{r.preference} {r.exchange}" for r in ans]
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        ans = await resolver.resolve(domain, "NS")
                        result["ns"] = [str(r) for r in ans]
                    except Exception:  # noqa: BLE001
                        pass
                    return result
                except ImportError:
                    # Fallback: sync resolver in thread (slightly heavier, still bounded)
                    import dns.resolver
                    res: dict[str, Any] = {"a": [], "aaaa": [], "mx": [], "ns": []}
                    for rtype, key, fmt in (
                        ("A", "a", lambda r: str(r)),
                        ("AAAA", "aaaa", lambda r: str(r)),
                        ("MX", "mx", lambda r: f"{r.preference} {r.exchange}"),
                        ("NS", "ns", lambda r: str(r)),
                    ):
                        try:
                            from hledac.universal.runtime.worker_pool import io_bound
                            ans = await io_bound(
                                dns.resolver.resolve, domain, rtype, lifetime=_EXTERNAL_LOOKUP_TIMEOUT
                            )
                            res[key] = [fmt(rec) for rec in ans]
                        except Exception:  # noqa: BLE001
                            pass
                    return res

            async with asyncio.timeout(_EXTERNAL_LOOKUP_TIMEOUT):
                result = await _async_dns()
            return result if result else None
        except (TimeoutError, Exception):
            return None

    async def _rdns_lookup(self, ip_address: str) -> dict[str, Any] | None:
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

            async with asyncio.timeout(_EXTERNAL_LOOKUP_TIMEOUT):
                from hledac.universal.runtime.worker_pool import io_bound
                result = await io_bound(_sync_rdns)
            return result if result else None
        except (TimeoutError, Exception):
            return None


# ---------------------------------------------------------------------------
# Sprint F261: CanonicalFinding wiring for forensic analysis
# ---------------------------------------------------------------------------
# Bridges ForensicsEnricher.enrich() output to the canonical DuckDB write
# path (DuckDBShadowStore.async_ingest_findings_batch). Forensic results
# become first-class CanonicalFindings with source_type="forensic_analysis"
# and are bounded to prevent payload blowup. All functions are fail-safe
# — return None / [] on any error, never raise.

# Canonical source_type string for the forensic analysis ingest path
FORENSIC_SOURCE_TYPE: str = "forensic_analysis"

# Hard limits for bounded payload_text serialization
_FORENSIC_PAYLOAD_MAX_BYTES = 4096
_FORENSIC_PAYLOAD_KEYS_MAX = 25
_FORENSIC_PAYLOAD_STR_MAX = 512
_FORENSIC_PAYLOAD_LIST_MAX = 10
_FORENSIC_PAYLOAD_LIST_ITEM_STR_MAX = 200


# ── Sprint F264: provenance_json forensics facet ──────────────────────────
# A structured facet co-located in payload_text so downstream DuckDB queries
# (and STIX export) can distinguish forensic-analysis-derived findings from
# raw CT/web findings without joining LMDB. The facet is a bounded JSON dict
# with a fixed schema (capability, parent_source, signal_flags) so it stays
# SQL queryable via ``payload_text LIKE '%"facet":"forensic_analysis"%'``.
#
# Bounded: max 5 keys, 128 chars per string, 1 KB total — well under the
# parent _FORENSIC_PAYLOAD_MAX_BYTES envelope. The bounded dict is merged
# into the existing payload_text at the top level (no schema change).
_FORENSIC_FACET_STR_MAX = 128


def _build_forensic_facet(
    enrichment: dict[str, Any],
    parent_source_type: str,
    finding_id_suffix: str,
) -> dict[str, Any]:
    """Build a bounded ``provenance_json``-style facet for a forensic finding.

    Sprint F264: returns a dict with a fixed key schema (5 keys, ≤128 chars
    each, ≤1 KB total) suitable for embedding in ``payload_text`` JSON. The
    facet is a **read-side helper** for downstream consumers (DuckDB
    ``payload_text LIKE '%"facet":"forensic_analysis"%'``, STIX exporter,
    markdown reporter) — it does not add a new DB column.

    Capability is inferred from which enrichment sub-dicts are non-empty:
    ``whois/ssl/dns/rdns`` → ``"network"``, ``steganography`` → ``"steg"``,
    ``ghosts`` → ``"ghost"``, ``metadata`` → ``"meta"``, otherwise ``"mixed"``.

    Returns empty dict on any error (fail-soft, never raises).
    """
    if not isinstance(enrichment, dict):
        return {}
    try:
        # Signal flags bitmask
        flags = 0
        capability_kinds: list[str] = []
        if enrichment.get("whois") and isinstance(enrichment["whois"], dict):
            flags |= 0x01
            capability_kinds.append("whois")
        if enrichment.get("ssl") and isinstance(enrichment["ssl"], dict):
            flags |= 0x02
            capability_kinds.append("ssl")
        if enrichment.get("dns") and isinstance(enrichment["dns"], dict):
            flags |= 0x04
            capability_kinds.append("dns")
        if enrichment.get("rdns") and isinstance(enrichment["rdns"], dict):
            flags |= 0x08
            capability_kinds.append("rdns")
        if enrichment.get("steganography") and isinstance(
            enrichment["steganography"], dict
        ):
            flags |= 0x10
            capability_kinds.append("steg")
        if enrichment.get("ghosts") and isinstance(enrichment["ghosts"], dict):
            flags |= 0x20
            capability_kinds.append("ghost")
        if enrichment.get("metadata") and isinstance(enrichment["metadata"], dict):
            flags |= 0x40
            capability_kinds.append("meta")

        if not capability_kinds:
            capability = "mixed"
        elif len(capability_kinds) == 1:
            capability = capability_kinds[0]
        else:
            capability = "network" if all(
                k in ("whois", "ssl", "dns", "rdns") for k in capability_kinds
            ) else "mixed"

        return {
            "facet": FORENSIC_SOURCE_TYPE,
            "capability": capability[:_FORENSIC_FACET_STR_MAX],
            "parent_source": str(parent_source_type or "")[:_FORENSIC_FACET_STR_MAX],
            "finding_id_suffix": str(finding_id_suffix or "_forensic")[:32],
            "signal_flags": int(flags) & 0x7F,
        }
    except Exception:
        return {}


def _merge_facet_into_enrichment(
    enrichment: dict[str, Any],
    facet: dict[str, Any],
) -> dict[str, Any]:
    """Merge bounded facet dict into enrichment so it lands in payload_text JSON.

    Sprint F264: prepends the facet dict under the key ``_forensic_facet``
    so it appears as a top-level field in the bounded payload_text. Total
    cost is ≤ 1 KB (5 keys × 128 chars) — well under the 4 KB envelope.
    Fail-soft: returns enrichment unchanged on any error.
    """
    if not isinstance(enrichment, dict) or not isinstance(facet, dict) or not facet:
        return enrichment
    try:
        # Use a fresh dict to avoid mutating caller state.
        merged = dict(enrichment)
        merged["_forensic_facet"] = dict(facet)
        return merged
    except Exception:
        return enrichment


def _bound_enrichment_for_payload(enrichment: dict[str, Any]) -> str:
    """Bound the enrichment dict to a JSON string for CanonicalFinding.payload_text.

    F261 invariant: payload_text must be bounded to prevent write-blowup in
    DuckDBShadowStore. Truncates per-key strings, caps key count, and
    caps list/dict nesting depth. Returns "" on any serialization error.
    """
    if not isinstance(enrichment, dict):
        return ""
    try:
        import orjson
        def _dumps(v):
            return orjson.dumps(v).decode("utf-8", errors="replace")
    except ImportError:
        import json
        def _dumps(v):
            return json.dumps(v, ensure_ascii=False)

    bounded: dict[str, Any] = {}
    keys = list(enrichment.keys())
    for i, k in enumerate(keys):
        if i >= _FORENSIC_PAYLOAD_KEYS_MAX:
            bounded["_truncated_keys"] = keys[i:][:5]
            break
        v = enrichment[k]
        bk = str(k)[:64]
        if isinstance(v, str):
            bounded[bk] = v[:_FORENSIC_PAYLOAD_STR_MAX]
        elif isinstance(v, (list, tuple)):
            bounded[bk] = [
                str(x)[:_FORENSIC_PAYLOAD_LIST_ITEM_STR_MAX]
                for x in list(v)[:_FORENSIC_PAYLOAD_LIST_MAX]
            ]
        elif isinstance(v, dict):
            if len(v) <= 8 and all(
                isinstance(x, (str, int, float, bool, type(None))) for x in v.values()
            ):
                bounded[bk] = {str(kk)[:32]: vv for kk, vv in v.items()}
            else:
                bounded[bk] = _dumps(v)[:_FORENSIC_PAYLOAD_STR_MAX]
        elif isinstance(v, (int, float, bool)) or v is None:
            bounded[bk] = v
        else:
            bounded[bk] = str(v)[:_FORENSIC_PAYLOAD_STR_MAX]
    return _dumps(bounded)[:_FORENSIC_PAYLOAD_MAX_BYTES]


def make_canonical_finding_from_enrichment(
    original_finding: Any,
    enrichment: dict[str, Any],
    *,
    source_type: str | None = None,
    finding_id_suffix: str = "_forensic",
) -> Any:
    """
    Convert a ForensicsEnricher.enrich() result into a CanonicalFinding.

    Sprint F261: Forensic capabilities → CanonicalFinding wiring.
    The new finding is a *derived* finding: it points back to the
    original via its parent finding_id (encoded in the new finding_id
    as ``<parent_finding_id>_forensic``) and via the bounded payload_text.

    source_type defaults to :data:`FORENSIC_SOURCE_TYPE` but can be
    overridden for sub-capabilities (e.g., ``"steganography_detection"``,
    ``"digital_ghost_detection"``).

    Fail-safe: returns None on any error — never raises. Caller decides
    whether to skip the finding or fall back to legacy storage.

    Args:
        original_finding: The parent finding (any object with
            ``finding_id``, ``query``, ``source_type``, ``confidence``).
        enrichment: Dict from ForensicsEnricher.enrich() or compatible.
        source_type: Override for the new finding's source_type.
        finding_id_suffix: Suffix appended to parent finding_id.

    Returns:
        A :class:`knowledge.duckdb_store.CanonicalFinding` instance,
        or None on validation / construction failure.
    """
    if not original_finding or not isinstance(enrichment, dict):
        return None
    try:
        # Lazy import — duckdb_store is heavy; forensics loads early.
        from knowledge.duckdb_store import CanonicalFinding

        parent_id = getattr(original_finding, "finding_id", None)
        if not parent_id:
            return None

        try:
            import time as _time
            ts = float(_time.time())
        except Exception:
            ts = 0.0

        try:
            confidence = float(getattr(original_finding, "confidence", 0.7) or 0.7)
        except (TypeError, ValueError):
            confidence = 0.7
        confidence = max(0.0, min(1.0, confidence))

        query_raw = getattr(original_finding, "query", "") or ""
        query = str(query_raw)[:512]

        parent_source_type = str(
            getattr(original_finding, "source_type", "") or ""
        )[:64]
        new_source_type = str(source_type or FORENSIC_SOURCE_TYPE)[:64]

        payload_text = _bound_enrichment_for_payload(enrichment)

        finding_id = f"{parent_id}{finding_id_suffix}"[:128]

        # Sprint F264: embed structured provenance_json facet in payload_text
        # so downstream DuckDB queries can filter by forensic capability
        # (e.g. ``payload_text LIKE '%"facet":"forensic_analysis"%'``).
        # The facet is built from the enrichment sub-dicts and is bounded
        # to 5 keys × 128 chars — well under the 4 KB envelope. Fail-soft:
        # an empty/None enrichment simply produces an empty facet, which is
        # filtered out by ``_merge_facet_into_enrichment``.
        try:
            facet = _build_forensic_facet(
                enrichment, parent_source_type, finding_id_suffix
            )
            if facet:
                enrichment_with_facet = _merge_facet_into_enrichment(enrichment, facet)
                payload_text = _bound_enrichment_for_payload(enrichment_with_facet)
        except Exception:  # noqa: BLE001
            # Facet is advisory — payload_text already set above is fine
            pass

        return CanonicalFinding(
            finding_id=finding_id,
            query=query,
            source_type=new_source_type,
            confidence=confidence,
            ts=ts,
            provenance=("forensic_analysis", parent_source_type),
            payload_text=payload_text,
        )
    except Exception as exc:
        log.debug(
            "make_canonical_finding_from_enrichment failed for parent=%s: %s",
            getattr(original_finding, "finding_id", "?"),
            exc,
        )
        return None
