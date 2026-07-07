"""
ZeroAttributionEngine — functional implementation.

Provides zero-attribution metadata stripping and header fingerprinting.
Used by fetch_coordinator.py, stealth_layer.py, and intelligence modules.

Real implementation provides:
- strip_metadata(): Remove identifying metadata from content
- fingerprint_rotate_headers(): Rotate headers to reduce fingerprinting
- generate_cover_traffic_urls(): Generate decoy URLs for cover traffic
"""
from __future__ import annotations


import io
import logging
import random
import string
import zipfile

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# MIME type constants
# ---------------------------------------------------------------------------
_JPEG_MIME_TYPES = {"image/jpeg", "image/jpg"}
_PNG_MIME_TYPES = {"image/png"}
_PDF_MIME_TYPES = {"application/pdf"}
_DOCX_MIME_TYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


# ---------------------------------------------------------------------------
# strip_metadata()
# ---------------------------------------------------------------------------

def strip_metadata(content: bytes, mime_type: str | None = None) -> bytes:
    """
    Strip identifying metadata from content.

    Supports JPEG (via PIL), PNG (via PIL), PDF (pass-through — requires heavy deps),
    DOCX (via zipfile/ZIP manipulation). All other types returned unchanged.

    Args:
        content: Raw content bytes.
        mime_type: MIME type string (e.g. "image/jpeg", "application/pdf").
                   If None only heuristic detection is attempted.

    Returns:
        bytes: Content with metadata stripped.
    """
    if not content:
        return content

    mt = (mime_type or "").lower().strip()

    # JPEG — strip EXIF via PIL
    if mt in _JPEG_MIME_TYPES:
        return _strip_jpeg(content)

    # PNG — strip metadata via PIL
    if mt in _PNG_MIME_TYPES:
        return _strip_png(content)

    # DOCX — strip ZIP-embedded docProps/core.xml
    if mt in _DOCX_MIME_TYPES:
        return _strip_docx(content)

    # PDF — pass-through (heavy dep; caller can pre-strip externally)
    if mt in _PDF_MIME_TYPES:
        return content

    # Fallback: try heuristic detection via magic bytes
    return _strip_by_magic(content)


def _strip_jpeg(content: bytes) -> bytes:
    """Remove EXIF metadata from JPEG content via PIL."""
    try:
        from PIL import Image
    except Exception:
        return content

    try:
        img = Image.open(io.BytesIO(content))
        data = img.getdata()
        img_without_exif = Image.new(data.mode, data.size)
        img_without_exif.putdata(data)
        # Clear EXIF flag
        if hasattr(img_without_exif, "_getexif"):
            try:
                del img_without_exif._getexif
            except Exception:  # noqa: BLE001
                pass
        # Rebuild: strip all APP markers by saving to fresh buffer
        buf = io.BytesIO()
        img_without_exif.save(buf, format=img.format or "JPEG", exif=b"")
        return buf.getvalue()
    except Exception:
        return content


def _strip_png(content: bytes) -> bytes:
    """Remove metadata (tEXt, iTXt, zTXt, eXIf) from PNG via PIL."""
    try:
        from PIL import Image
    except Exception:
        return content

    try:
        img = Image.open(io.BytesIO(content))
        data = img.getdata()
        img_clean = Image.new(data.mode, data.size)
        img_clean.putdata(data)
        buf = io.BytesIO()
        img_clean.save(buf, format=img.format or "PNG")
        return buf.getvalue()
    except Exception:
        return content


def _strip_docx(content: bytes) -> bytes:
    """Remove docProps/core.xml (author, title, dates) from DOCX ZIP."""
    try:
        buf_in = io.BytesIO(content)
        buf_out = io.BytesIO()
        with zipfile.ZipFile(buf_in, "r") as zin, zipfile.ZipFile(buf_out, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                if item.filename == "docProps/core.xml":
                    continue  # skip metadata
                zout.writestr(item, zin.read(item.filename))
        return buf_out.getvalue()
    except Exception:
        return content


def _strip_by_magic(content: bytes) -> bytes:
    """Heuristic strip based on magic bytes when mime_type unavailable."""
    if len(content) < 12:
        return content
    # JPEG SOI marker: FF D8
    if content[0] == 0xFF and content[1] == 0xD8:
        return _strip_jpeg(content)
    # PNG signature: 89 50 4E 47 0D 0A 1A 0A
    if content[:8] == b"\x89PNG\r\n\x1a\n":
        return _strip_png(content)
    # DOCX/ZIP signature: PK (50 4B)
    if content[:2] == b"PK":
        return _strip_docx(content)
    return content


# ---------------------------------------------------------------------------
# fingerprint_rotate_headers()
# ---------------------------------------------------------------------------

# Headers that reveal server/version/fingerprint — removed from responses
_FINGERPRINT_HEADERS_TO_REMOVE: set[str] = {
    # Server identification
    "server",
    "x-powered-by",
    "x-aspnet-version",
    "x-aspnetmvc-version",
    # Caching / content identity
    "etag",
    "last-modified",
    "if-match",
    "if-none-match",
    "if-modified-since",
    "if-unmodified-since",
    # CDN / proxy fingerprinting
    "cf-ray",
    "x-cache",
    "x-cache-hit",
    "x-cdn",
    "x-edge-location",
    "cf-cache-status",
    "cf-request-id",
    # WAF / security headers that reveal fingerprint
    "x-served-by",
    "x-backend-server",
    "x-host",
    # Generic request fingerprints
    "x-request-id",
    "x-correlation-id",
    "x-trace-id",
    # Content generation markers
    "content-length",  # may mismatch after strip_metadata
    "content-md5",
    # Timing headers
    "x-response-time",
    "x-runtime",
    "x-req-time",
    # Strict-Transport-Security preload mark (reveals browser history)
    "strict-transport-security",  # keep in client req
    # Alternative / forwarded headers (client-side, rotate those)
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-server",
    "x-real-ip",
    "x-client-ip",
    "x-requested-with",
    "x-originating-ip",
    "true-client-ip",
    "via",
    "forwarded",
    # Custom analytics / usage tracking
    "x-amz-cf-id",
    "x-amz-id-2",
    "x-db-server-info",
    "x-middleware-id",
    "x-ratelimit-remaining",
    "x-ratelimit-limit",
    "x-ratelimit-reset",
    "retry-after",
    "x-rate-limit-remaining",
    "x-rate-limit-limit",
    "x-rate-limit-reset",
    "x-robots-tag",
    "x-sitemap-type",
    "link",  # preconnect hints reveal infrastructure
}

# Headers we allow through (pass-through from origin)
_ALLOWED_HEADERS: set[str] = {
    "content-type",
    "content-encoding",
    "content-language",
    "vary",
    "cache-control",
    "expires",
    "access-control-allow-origin",
    "access-control-allow-credentials",
    "access-control-allow-headers",
    "access-control-allow-methods",
    "access-control-expose-headers",
    "access-control-max-age",
    "timing-allow-origin",
    "accept-ranges",
    "connection",
    "keep-alive",
    "transfer-encoding",
    "upgrade",
    "sec-websocket-accept",
    "sec-websocket-key",
    "sec-websocket-version",
    "sec-websocket-extensions",
    "sec-websocket-protocol",
}


def fingerprint_rotate_headers(headers: dict) -> dict:
    """
    Rotate headers to reduce fingerprinting.

    Removes headers that reveal server version, CDN fingerprint, caching
    identity, and infrastructure details. Normalizes remaining headers.

    Args:
        headers: Original headers dict (lowercase keys preferred).

    Returns:
        dict: Headers with fingerprintable values removed/normalized.
    """
    if not headers:
        return {}

    # Normalize keys to lowercase for consistent matching
    result: dict[str, str] = {}
    for key, value in headers.items():
        k = key.lower().strip()
        if k in _FINGERPRINT_HEADERS_TO_REMOVE:
            continue
        if k in _ALLOWED_HEADERS:
            result[key] = value
        elif k.startswith("x-") and not k.startswith("x-request-id"):
            # Strip non-standard headers except safe ones
            continue
        else:
            result[key] = value

    return result


# ---------------------------------------------------------------------------
# generate_cover_traffic_urls()
# ---------------------------------------------------------------------------

# Pre-defined popular decoy path segments — low metadata emission
_DECOY_PATHS = [
    "/stylesheets/main.css",
    "/static/js/vendor/jquery.min.js",
    "/images/placeholder.png",
    "/favicon.ico",
    "/robots.txt",
    "/sitemap.xml",
    "/apple-touch-icon.png",
    "/browserconfig.xml",
    "/sw.js",
    "/manifest.json",
    "/.well-known/security.txt",
    "/humans.txt",
    "/ads.txt",
]

# Common popular domains for decoy referrers / targets
_DECOY_CLEARNET_DOMAINS = [
    "example.com",
    "www.example.org",
    "example.net",
    "example.edu",
    "example.gov",
    "www.wikipedia.org",
    "www.cloudflare.com",
    "www.google.com",
    "www.microsoft.com",
    "www.apple.com",
    "cdn.jsdelivr.net",
    "cdnjs.cloudflare.com",
    "fonts.googleapis.com",
    "maxcdn.bootstrapcdn.com",
    "stackpath.bootstrapcdn.com",
    "ajax.googleapis.com",
    "www.wikipedia.org",
    "www.reddit.com",
    "www.facebook.com",
    "www.twitter.com",
    "www.instagram.com",
    "www.linkedin.com",
    "www.github.com",
    "www.stackoverflow.com",
    "www.nytimes.com",
    "www.bbc.com",
    "www.cnn.com",
    "www.theguardian.com",
    "www.forbes.com",
    "www.reuters.com",
    "www.amazon.com",
    "www.ebay.com",
    "www.wikipedia.org",
    "www.imdb.com",
    "www.weather.com",
    "www.msn.com",
    "www.foxnews.com",
    "www.npr.org",
    "www.pbs.org",
    "www.youtube.com",
    "vimeo.com",
    "www.dailymotion.com",
    "www.flickr.com",
    "www.pinterest.com",
    "www.tumblr.com",
    "www.wordpress.com",
    "www.blogger.com",
    "www.medium.com",
    "www.quora.com",
    "www.reddit.com",
    "www.wikipedia.org",
]

_TOR_DECOY_PATHS = [
    "/",
    "/search",
    "/about",
    "/contact",
    "/privacy",
    "/terms",
    "/faq",
    "/help",
    "/blog",
    "/news",
]

_I2P_DECOY_PATHS = [
    "/",
    "/stats",
    "/info",
    "/network",
    "/peers",
    "/status",
    "/api",
    "/docs",
    "/help",
]


def _random_base32(length: int) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def _random_hex(length: int) -> str:
    return "".join(random.choices("0123456789abcdef", k=length))


def generate_cover_traffic_urls(
    n_decoys: int = 1, transport: str = "clearnet"
) -> list[str]:
    """
    Generate decoy URLs for cover traffic.

    Creates plausible-looking URLs that blend with normal traffic patterns.
    Uses popular well-known domains and paths to minimize metadata emission.

    Args:
        n_decoys: Number of decoy URLs to generate.
        transport: Transport type — "clearnet" | "tor" | "i2p".

    Returns:
        list[str]: Decoy URLs (empty list if transport unknown or n_decoys <= 0).
    """
    if n_decoys <= 0:
        return []

    t = transport.lower()
    urls: list[str] = []

    if t == "clearnet":
        for _ in range(n_decoys):
            domain = random.choice(_DECOY_CLEARNET_DOMAINS)
            path = random.choice(_DECOY_PATHS)
            urls.append(f"https://{domain}{path}")

    elif t == "tor":
        for _ in range(n_decoys):
            # Random .onion address (valid format only, not real)
            prefix = _random_hex(16)
            path = random.choice(_TOR_DECOY_PATHS)
            urls.append(f"http://{prefix}.onion{path}")

    elif t == "i2p":
        for _ in range(n_decoys):
            # Random I2P destination (Base32 over 516 chars)
            dest = _random_base32(52)
            path = random.choice(_I2P_DECOY_PATHS)
            urls.append(f"http://{dest}.b32.i2p{path}")

    else:
        # Unknown transport — generate generic HTTPS decoys
        for _ in range(n_decoys):
            domain = random.choice(_DECOY_CLEARNET_DOMAINS)
            path = random.choice(_DECOY_PATHS)
            urls.append(f"https://{domain}{path}")

    return urls


# ---------------------------------------------------------------------------
# ZeroAttributionEngine — wrapper class (interface compat for callers)
# ---------------------------------------------------------------------------

class ZeroAttributionEngine:
    """
    Zero-attribution engine for metadata stripping and header fingerprinting.

    Provides fingerprint rotation and cover traffic generation.
    Wraps the module-level functions for callers expecting an object interface.
    """

    def __init__(self, **kwargs) -> None:
        """Initialize with optional configuration."""
        self._enabled = kwargs.get("enabled", True)
        logger.debug(f"ZeroAttributionEngine: enabled={self._enabled}")

    def strip_metadata(self, data: bytes, mime_type: str = "") -> bytes:
        """Strip EXIF and metadata. Fail-safe — returns original on any error."""
        try:
            mime = mime_type.lower()
            if mime in ("image/jpeg", "image/jpg") or data[:3] == b"\xff\xd8\xff":
                import io

                from PIL import Image
                img = Image.open(io.BytesIO(data))
                clean = io.BytesIO()
                img_copy = Image.new(img.mode, img.size)
                img_copy.putdata(img.getdata())
                img_copy.save(clean, format="JPEG", quality=95)
                return clean.getvalue()
            elif mime == "image/png" or data[:8] == b"\x89PNG\r\n\x1a\n":
                import io

                from PIL import Image
                img = Image.open(io.BytesIO(data))
                clean = io.BytesIO()
                img_copy = Image.new(img.mode, img.size)
                img_copy.putdata(img.getdata())
                img_copy.save(clean, format="PNG")
                return clean.getvalue()
            elif mime in ("application/vnd.openxmlformats-officedocument"
                          ".wordprocessingml.document",) or data[:4] == b"PK\x03\x04":
                import io
                import zipfile
                src = zipfile.ZipFile(io.BytesIO(data))
                out = io.BytesIO()
                dst = zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED)
                skip = {"docProps/core.xml", "docProps/app.xml"}
                for item in src.infolist():
                    if item.filename not in skip:
                        dst.writestr(item, src.read(item.filename))
                dst.close()
                return out.getvalue()
        except Exception:  # noqa: BLE001
            pass
        return data  # fail-safe

    def fingerprint_rotate_headers(self, headers: dict) -> dict:
        """Remove fingerprinting headers. Returns cleaned dict."""
        return {k: v for k, v in headers.items() if k.lower() not in _FINGERPRINT_HEADERS_TO_REMOVE}

    def generate_cover_traffic_urls(self, count: int = 5) -> list[str]:
        """Generate plausible cover traffic URLs."""
        return random.sample(_DECOY_CLEARNET_DOMAINS, min(count, len(_DECOY_CLEARNET_DOMAINS)))


__all__ = ["ZeroAttributionEngine", "strip_metadata", "fingerprint_rotate_headers", "generate_cover_traffic_urls"]
