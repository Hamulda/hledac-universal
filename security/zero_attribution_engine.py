"""
Zero-Attribution Engine — query fingerprinting anonymization.

Threat model: researcher's query pattern must not be distinguishable
from background noise. Adversary monitors timing, headers, cover traffic.

M1 constraint: all operations < 5ms per finding. No heavy crypto.
"""
from __future__ import annotations

import io
import os
import re
import secrets
from typing import Optional

# EXIF stripping — optional dep, fail-safe
try:
    from PIL import Image

    _PIL_AVAILABLE = True
except Exception:
    _PIL_AVAILABLE = False

# PDF metadata stripping — optional dep
try:
    import pypdf

    _PYPDF_AVAILABLE = True
except Exception:
    _PYPDF_AVAILABLE = False

import logging

logger = logging.getLogger(__name__)

_ZAT_ENABLED = os.getenv("HLEDAC_ENABLE_ZERO_ATTRIBUTION", "0") == "1"

# ------------------------------------------------------------------
# User-Agent pool — 50 real browser strings (no internet needed)
# ------------------------------------------------------------------
_UA_POOL = [
    # Chrome 124 macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 OPR/109.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5_0) AppleWebKit/605.1.15 (KHTML, like Goken Gecko) Version/17.4 Safari/605.1.15",
    # Firefox 125/126 macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13.5; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.5; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:124.0) Gecko/20100101 Firefox/124.0",
    # Safari 17 macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    # Chrome Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 11.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    # Chrome Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0",
    # Chrome Android
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Mobile Safari/537.36",
    # Safari iOS 17
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPad; CPU OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
    # Chrome Android tablet
    "Mozilla/5.0 (Linux; Android 14; Pixel Tablet) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Edge Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    # Opera
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 OPR/110.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 OPR/110.0.0.0",
    # Yandex
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 YaBrowser/24.4.0.0",
    # Brave
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Brave/1.66.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Brave/1.66.0",
    # Vivaldi
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Vivaldi/6.7.0.0",
    # Firefox Android
    "Mozilla/5.0 (Android 14; Mobile; rv:125.0) Gecko/125.0 Firefox/125.0",
    "Mozilla/5.0 (Android 14; Tablet; rv:125.0) Gecko/125.0 Firefox/125.0",
    # Chrome iOS
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/124.0.0.0 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPad; CPU OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/124.0.0.0 Mobile/15E148 Safari/604.1",
]

# Accept-Language distributions — plausible browser locales
_ACCEPT_LANGUAGE_POOL = [
    "en-US,en;q=0.9",
    "en-US,en;q=0.9,es;q=0.8",
    "en-GB,en;q=0.9",
    "en-US,en;q=0.9,fr;q=0.8",
    "en-US,en;q=0.9,de;q=0.8",
    "en-US,en;q=0.9,ja;q=0.8",
    "en-US,en;q=0.9,zh-CN;q=0.8",
    "en-US,en;q=0.9,ru;q=0.8",
    "en,en-US;q=0.9",
    "en-US,en;q=0.9,nl;q=0.8",
    "en-AU,en;q=0.9",
    "en-CA,en;q=0.9,fr;q=0.8",
    "de-DE,de;q=0.9,en;q=0.8",
    "fr-FR,fr;q=0.9,en;q=0.8",
    "es-ES,es;q=0.9,en;q=0.8",
    "ja-JP,ja;q=0.9,en;q=0.8",
]

# Accept-Encoding
_ACCEPT_ENCODING_POOL = [
    " gzip, deflate, br",
    " gzip, deflate",
    " deflate, gzip, br",
]

# DNT
_DNT_POOL = ["1", "0", None]

# ------------------------------------------------------------------
# Cover traffic word pool — semantic field terms for decoy generation
# ------------------------------------------------------------------
_COVER_WORD_PAIRS = [
    ("election", "transparency"),
    ("market", "forecast"),
    ("climate", "sensor"),
    ("shipping", "route"),
    ("healthcare", "provider"),
    ("academic", "journal"),
    ("weather", "radar"),
    ("traffic", "congestion"),
    ("energy", "grid"),
    ("agriculture", "yield"),
    ("real estate", "appraisal"),
    ("financial", "derivatives"),
    ("supply chain", "logistics"),
    ("entertainment", "streaming"),
    ("sports", "analytics"),
    ("technology", "startup"),
    ("education", "platform"),
    ("travel", "booking"),
    ("retail", "inventory"),
    ("manufacturing", "output"),
    ("telecommunication", "infrastructure"),
    ("government", "procurement"),
    ("nonprofit", "donation"),
    ("environmental", "monitoring"),
    ("public safety", "response"),
]

# Server-identifying HTML patterns to strip
_HTML_SERVER_TAGS = re.compile(
    rb"<!--\s*(?:server|generator|apache|nginx|iis|powered.by)[^>]*-->|"
    rb"<(?:meta|comment)\s+[^>]*(?:server|generator|apache|nginx|iis)[^>]*>|"
    rb"<(?:meta|comment)\s+[^>]*(?:powered.by)[^>]*>",
    re.IGNORECASE,
)
_HTML_VERSION_RE = re.compile(rb'<!DOCTYPE[^>]*html[^>]*>', re.IGNORECASE)


class ZeroAttributionEngine:
    """Query pattern anonymization engine.

    All operations fail-safe — on any exception, returns identity
    (no jitter, original headers, original content). Feature gate:
    HLEDAC_ENABLE_ZERO_ATTRIBUTION=1.
    """

    __slots__ = ("_enabled", "_ua_idx")

    def __init__(self, enabled: bool | None = None) -> None:
        self._enabled = enabled if enabled is not None else _ZAT_ENABLED
        self._ua_idx = secrets.randbelow(len(_UA_POOL)) if _UA_POOL else 0
        logger.debug("ZeroAttributionEngine enabled=%s", self._enabled)

    # ------------------------------------------------------------------
    # 1. Query timing jitter
    # ------------------------------------------------------------------
    def query_timing_jitter(self, base_delay: float) -> float:
        """Add non-deterministic jitter to query delay.

        Returns base_delay + N(0, base_delay * 0.3), clamped to [0.5, 30.0].

        M1 constraint: < 0.1ms — pure Python arithmetic + secrets.
        """
        if not self._enabled:
            return base_delay
        try:
            jitter = secrets.randbelow(2**32) / (2**32 - 1)  # [0, 1)
            gauss = 1 - (2 * jitter)  # rough gauss approximation
            delta = base_delay * 0.3 * gauss
            return max(0.5, min(30.0, base_delay + delta))
        except Exception:
            return base_delay

    # ------------------------------------------------------------------
    # 2. Cover traffic generation
    # ------------------------------------------------------------------
    def generate_cover_traffic(
        self, n_decoys: int = 3, topic_hints: list[str] | None = None
    ) -> list[str]:
        """Generate n_decoy plausible but irrelevant query strings.

        Uses lightweight word-association (no embedding model needed).
        topic_hints, if provided, bias decoy domain selection.

        M1 constraint: < 2ms for n=3 — linear word pair selection.
        """
        if not self._enabled or n_decoys <= 0:
            return []
        try:
            decoys: list[str] = []
            pool = _COVER_WORD_PAIRS
            # Bias selection by topic hints using simple string overlap
            if topic_hints:
                scored = []
                for a, b in pool:
                    score = sum(1 for h in topic_hints if h.lower() in (a + b).lower())
                    scored.append((score, a, b))
                scored.sort(reverse=True)
                pool = [(a, b) for _, a, b in scored[:20]]
            for _ in range(n_decoys):
                a, b = secrets.choice(pool)
                # Add a random modifier to make each decoy unique
                modifiers = [
                    "recent",
                    "latest",
                    "statistics",
                    "report",
                    "overview",
                    "trends",
                ]
                mod = secrets.choice(modifiers)
                decoys.append(f"{mod} {a} {b}")
            return decoys
        except Exception:
            return []

    # ------------------------------------------------------------------
    # 2b. Cover traffic URL generation (transport-aware)
    # ------------------------------------------------------------------
    def generate_cover_traffic_urls(
        self, n_decoys: int = 3, transport: str = "clearnet"
    ) -> list[str]:
        """Generate n_decoy Wikipedia/archive URLs for cover traffic.

        Transport-aware mapping:
        - clearnet / unknown → Wikipedia article + Archive.org fallback
        - tor → Ahmia .onion search (no clearnet leak)
        - i2p → legwork.i2p search

        M1 constraint: < 2ms — pure sync URL construction, no I/O.
        """
        from urllib.parse import quote

        if not self._enabled or n_decoys <= 0:
            return []
        try:
            # Get decoy query strings from existing method
            decoy_queries = self.generate_cover_traffic(n_decoys=n_decoys)
            if not decoy_queries:
                return []

            urls: list[str] = []
            transport_lower = transport.lower()

            if transport_lower in ("clearnet", "unknown"):
                # Map each query to Wikipedia article URL
                for query in decoy_queries[:n_decoys]:
                    safe_topic = query.replace(" ", "_").replace('"', "")
                    urls.append(f"https://en.wikipedia.org/wiki/{quote(safe_topic, safe='')}")
                # Archive.org fallback for extra decoys beyond Wikipedia
                for query in decoy_queries[n_decoys:]:
                    safe_topic = query.replace(" ", "+").replace('"', "")
                    urls.append(f"https://archive.org/search?query={quote(safe_topic)}")

            elif transport_lower == "tor":
                # Ahmia .onion search — OPSEC: no clearnet URLs for Tor cover traffic
                onion_base = "http://juhanurmihxlp77nkq76byazcldy2hlmovfu2epvl5ankdibsot4csyd.onion/search/"
                for query in decoy_queries[:n_decoys]:
                    urls.append(f"{onion_base}?q={quote(query)}")
                # If no queries (pool exhausted), fail-soft → empty list

            elif transport_lower == "i2p":
                # legwork.i2p search
                i2p_base = "http://legwork.i2p/search?q="
                for query in decoy_queries[:n_decoys]:
                    urls.append(f"{i2p_base}{quote(query)}")

            return urls
        except Exception:
            return []

    # ------------------------------------------------------------------
    # 3. Header fingerprint randomization
    # ------------------------------------------------------------------
    def fingerprint_rotate_headers(self, headers: dict | None = None) -> dict:
        """Rotate Accept-Language, Accept-Encoding, DNT, User-Agent.

        Creates non-deterministic browser profile from curated pools.
        Preserves any existing headers not in the randomized set.

        M1 constraint: < 1ms — dict construction + secrets.choice.
        """
        if not self._enabled:
            return headers.copy() if headers else {}
        try:
            result = dict(headers) if headers else {}
            # Rotate User-Agent (round-robin across pool)
            self._ua_idx = (self._ua_idx + 1) % len(_UA_POOL)
            result["User-Agent"] = _UA_POOL[self._ua_idx]
            # Randomize Accept-Language
            result["Accept-Language"] = secrets.choice(_ACCEPT_LANGUAGE_POOL)
            # Randomize Accept-Encoding
            result["Accept-Encoding"] = secrets.choice(_ACCEPT_ENCODING_POOL)
            # Randomize DNT
            dnt = secrets.choice(_DNT_POOL)
            if dnt is not None:
                result["DNT"] = dnt
            elif "DNT" in result:
                del result["DNT"]
            # Remove any server-identifying headers
            for key in list(result.keys()):
                if key.lower() in ("server", "x-powered-by", "x-aspnet-version"):
                    del result[key]
            return result
        except Exception as e:
            logger.warning("fingerprint_rotate_headers failed: %s", e)
            return headers.copy() if headers else {}

    # ------------------------------------------------------------------
    # 4. Metadata stripping
    # ------------------------------------------------------------------
    def strip_metadata(self, content: bytes, content_type: str) -> bytes:
        """Strip identifying metadata from content.

        - JPEG/PNG: EXIF via Pillow
        - PDF: author/creator metadata via pypdf
        - HTML: server comments, version strings

        Returns original content on any failure (fail-safe).

        M1 constraint: < 5ms for typical finding (image < 1MB).
        """
        if not self._enabled or not content:
            return content
        ct = content_type.lower()
        try:
            if ct.startswith("image/jpeg") or ct.startswith("image/jpg"):
                return self._strip_image_exif(content)
            elif ct.startswith("image/png"):
                return self._strip_image_exif(content)
            elif ct.startswith("application/pdf"):
                return self._strip_pdf_metadata(content)
            elif ct.startswith("text/html") or ct.startswith("application/xhtml"):
                return self._strip_html_metadata(content)
            return content
        except Exception as e:
            logger.debug("strip_metadata(%s) failed: %s", content_type, e)
            return content

    def _strip_image_exif(self, content: bytes) -> bytes:
        """Remove EXIF from JPEG/PNG via Pillow."""
        if not _PIL_AVAILABLE:
            return content
        try:
            img = Image.open(io.BytesIO(content))
            out_img = Image.new(img.mode, img.size)
            out_img.paste(img)
            out = io.BytesIO()
            out_img.save(out, format=img.format or "JPEG")
            return out.getvalue()
        except Exception:
            return content

    def _strip_pdf_metadata(self, content: bytes) -> bytes:
        """Strip PDF author/creator metadata via pypdf."""
        if not _PYPDF_AVAILABLE:
            return content
        try:
            reader = pypdf.PdfReader(io.BytesIO(content))
            writer = pypdf.PdfWriter()
            for page in reader.pages:
                writer.add_page(page)
            # Clear document info
            writer.add_metadata({})
            out = io.BytesIO()
            writer.write(out)
            return out.getvalue()
        except Exception:
            return content

    def _strip_html_metadata(self, content: bytes) -> bytes:
        """Strip server-identifying HTML comments and meta tags."""
        result = _HTML_SERVER_TAGS.sub(b"", content)
        result = _HTML_VERSION_RE.sub(b"", result)
        return result


__all__ = ["ZeroAttributionEngine"]