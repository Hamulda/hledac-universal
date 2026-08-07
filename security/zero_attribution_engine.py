"""
ZeroAttributionEngine — functional implementation.

Provides zero-attribution metadata stripping and header fingerprinting.

Used by fetch_coordinator.py, stealth_layer.py, and intelligence modules.

Real implementation provides:
- strip_metadata(): Remove identifying metadata from content
- fingerprint_rotate_headers(): Rotate headers to reduce fingerprinting
- generate_cover_traffic_urls(): Generate decoy URLs for cover traffic
"""
import io
import logging
import secrets
import string
import zipfile
logger = logging.getLogger(__name__)

# Crypto-safe RNG — F350M-R
_RNG = secrets.SystemRandom()
_JPEG_MIME_TYPES = {'image/jpeg', 'image/jpg'}
_PNG_MIME_TYPES = {'image/png'}
_PDF_MIME_TYPES = {'application/pdf'}
_DOCX_MIME_TYPES = {'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}

def strip_metadata(content: bytes, mime_type: str | None=None) -> bytes:
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
    mt = (mime_type or '').lower().strip()
    if mt in _JPEG_MIME_TYPES:
        return _strip_jpeg(content)
    if mt in _PNG_MIME_TYPES:
        return _strip_png(content)
    if mt in _DOCX_MIME_TYPES:
        return _strip_docx(content)
    if mt in _PDF_MIME_TYPES:
        return content
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
        if hasattr(img_without_exif, '_getexif'):
            try:
                del img_without_exif._getexif
            except Exception:  # noqa: BLE001
                pass
        buf = io.BytesIO()
        img_without_exif.save(buf, format=img.format or 'JPEG', exif=b'')
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
        img_clean.save(buf, format=img.format or 'PNG')
        return buf.getvalue()
    except Exception:
        return content

def _strip_docx(content: bytes) -> bytes:
    """Remove docProps/core.xml (author, title, dates) from DOCX ZIP."""
    try:
        buf_in = io.BytesIO(content)
        buf_out = io.BytesIO()
        with zipfile.ZipFile(buf_in, 'r') as zin, zipfile.ZipFile(buf_out, 'w', zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                if item.filename == 'docProps/core.xml':
                    continue
                zout.writestr(item, zin.read(item.filename))
        return buf_out.getvalue()
    except Exception:
        return content

def _strip_by_magic(content: bytes) -> bytes:
    """Heuristic strip based on magic bytes when mime_type unavailable."""
    if len(content) < 12:
        return content
    if content[0] == 255 and content[1] == 216:
        return _strip_jpeg(content)
    if content[:8] == b'\x89PNG\r\n\x1a\n':
        return _strip_png(content)
    if content[:2] == b'PK':
        return _strip_docx(content)
    return content
_FINGERPRINT_HEADERS_TO_REMOVE: set[str] = {'server', 'x-powered-by', 'x-aspnet-version', 'x-aspnetmvc-version', 'etag', 'last-modified', 'if-match', 'if-none-match', 'if-modified-since', 'if-unmodified-since', 'cf-ray', 'x-cache', 'x-cache-hit', 'x-cdn', 'x-edge-location', 'cf-cache-status', 'cf-request-id', 'x-served-by', 'x-backend-server', 'x-host', 'x-request-id', 'x-correlation-id', 'x-trace-id', 'content-length', 'content-md5', 'x-response-time', 'x-runtime', 'x-req-time', 'strict-transport-security', 'x-forwarded-for', 'x-forwarded-host', 'x-forwarded-server', 'x-real-ip', 'x-client-ip', 'x-requested-with', 'x-originating-ip', 'true-client-ip', 'via', 'forwarded', 'x-amz-cf-id', 'x-amz-id-2', 'x-db-server-info', 'x-middleware-id', 'x-ratelimit-remaining', 'x-ratelimit-limit', 'x-ratelimit-reset', 'retry-after', 'x-rate-limit-remaining', 'x-rate-limit-limit', 'x-rate-limit-reset', 'x-robots-tag', 'x-sitemap-type', 'link'}
_ALLOWED_HEADERS: set[str] = {'content-type', 'content-encoding', 'content-language', 'vary', 'cache-control', 'expires', 'access-control-allow-origin', 'access-control-allow-credentials', 'access-control-allow-headers', 'access-control-allow-methods', 'access-control-expose-headers', 'access-control-max-age', 'timing-allow-origin', 'accept-ranges', 'connection', 'keep-alive', 'transfer-encoding', 'upgrade', 'sec-websocket-accept', 'sec-websocket-key', 'sec-websocket-version', 'sec-websocket-extensions', 'sec-websocket-protocol'}

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
    result: dict[str, str] = {}
    for key, value in headers.items():
        k = key.lower().strip()
        if k in _FINGERPRINT_HEADERS_TO_REMOVE:
            continue
        if k in _ALLOWED_HEADERS:
            result[key] = value
        elif k.startswith('x-') and (not k.startswith('x-request-id')):
            continue
        else:
            result[key] = value
    return result
_DECOY_PATHS = ['/stylesheets/main.css', '/static/js/vendor/jquery.min.js', '/images/placeholder.png', '/favicon.ico', '/robots.txt', '/sitemap.xml', '/apple-touch-icon.png', '/browserconfig.xml', '/sw.js', '/manifest.json', '/.well-known/security.txt', '/humans.txt', '/ads.txt']
_DECOY_CLEARNET_DOMAINS = ['example.com', 'www.example.org', 'example.net', 'example.edu', 'example.gov', 'www.wikipedia.org', 'www.cloudflare.com', 'www.google.com', 'www.microsoft.com', 'www.apple.com', 'cdn.jsdelivr.net', 'cdnjs.cloudflare.com', 'fonts.googleapis.com', 'maxcdn.bootstrapcdn.com', 'stackpath.bootstrapcdn.com', 'ajax.googleapis.com', 'www.wikipedia.org', 'www.reddit.com', 'www.facebook.com', 'www.twitter.com', 'www.instagram.com', 'www.linkedin.com', 'www.github.com', 'www.stackoverflow.com', 'www.nytimes.com', 'www.bbc.com', 'www.cnn.com', 'www.theguardian.com', 'www.forbes.com', 'www.reuters.com', 'www.amazon.com', 'www.ebay.com', 'www.wikipedia.org', 'www.imdb.com', 'www.weather.com', 'www.msn.com', 'www.foxnews.com', 'www.npr.org', 'www.pbs.org', 'www.youtube.com', 'vimeo.com', 'www.dailymotion.com', 'www.flickr.com', 'www.pinterest.com', 'www.tumblr.com', 'www.wordpress.com', 'www.blogger.com', 'www.medium.com', 'www.quora.com', 'www.reddit.com', 'www.wikipedia.org']
_TOR_DECOY_PATHS = ['/', '/search', '/about', '/contact', '/privacy', '/terms', '/faq', '/help', '/blog', '/news']
_I2P_DECOY_PATHS = ['/', '/stats', '/info', '/network', '/peers', '/status', '/api', '/docs', '/help']

def _random_base32(length: int) -> str:
    return ''.join(_RNG.choices(string.ascii_lowercase + string.digits, k=length))

def _random_hex(length: int) -> str:
    return ''.join(_RNG.choices('0123456789abcdef', k=length))

def generate_cover_traffic_urls(n_decoys: int=1, transport: str='clearnet') -> list[str]:
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
    if t == 'clearnet':
        for _ in range(n_decoys):
            domain = _RNG.choice(_DECOY_CLEARNET_DOMAINS)
            path = _RNG.choice(_DECOY_PATHS)
            urls.append(f'https://{domain}{path}')
    elif t == 'tor':
        for _ in range(n_decoys):
            prefix = _random_hex(16)
            path = _RNG.choice(_TOR_DECOY_PATHS)
            urls.append(f'http://{prefix}.onion{path}')
    elif t == 'i2p':
        for _ in range(n_decoys):
            dest = _random_base32(52)
            path = _RNG.choice(_I2P_DECOY_PATHS)
            urls.append(f'http://{dest}.b32.i2p{path}')
    else:
        for _ in range(n_decoys):
            domain = _RNG.choice(_DECOY_CLEARNET_DOMAINS)
            path = _RNG.choice(_DECOY_PATHS)
            urls.append(f'https://{domain}{path}')
    return urls

class ZeroAttributionEngine:
    """
    Zero-attribution engine for metadata stripping and header fingerprinting.

    Provides fingerprint rotation and cover traffic generation.
    Wraps the module-level functions for callers expecting an object interface.
    """
    __slots__ = tuple(('_enabled',))

    def __init__(self, **kwargs) -> None:
        """Initialize with optional configuration."""
        self._enabled = kwargs.get('enabled', True)
        logger.debug(f'ZeroAttributionEngine: enabled={self._enabled}')

    def strip_metadata(self, data: bytes, mime_type: str='') -> bytes:
        """Strip EXIF and metadata. Fail-safe — returns original on any error."""
        try:
            mime = mime_type.lower()
            if mime in ('image/jpeg', 'image/jpg') or data[:3] == b'\xff\xd8\xff':
                import io
                from PIL import Image
                img = Image.open(io.BytesIO(data))
                clean = io.BytesIO()
                img_copy = Image.new(img.mode, img.size)
                img_copy.putdata(img.getdata())
                img_copy.save(clean, format='JPEG', quality=95)
                return clean.getvalue()
            elif mime == 'image/png' or data[:8] == b'\x89PNG\r\n\x1a\n':
                import io
                from PIL import Image
                img = Image.open(io.BytesIO(data))
                clean = io.BytesIO()
                img_copy = Image.new(img.mode, img.size)
                img_copy.putdata(img.getdata())
                img_copy.save(clean, format='PNG')
                return clean.getvalue()
            elif mime in ('application/vnd.openxmlformats-officedocument.wordprocessingml.document',) or data[:4] == b'PK\x03\x04':
                import io
                import zipfile
                src = zipfile.ZipFile(io.BytesIO(data))
                out = io.BytesIO()
                dst = zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED)
                skip = {'docProps/core.xml', 'docProps/app.xml'}
                for item in src.infolist():
                    if item.filename not in skip:
                        dst.writestr(item, src.read(item.filename))
                dst.close()
                return out.getvalue()
        except Exception:  # noqa: BLE001
            pass
        return data

    def fingerprint_rotate_headers(self, headers: dict) -> dict:
        """Remove fingerprinting headers. Returns cleaned dict."""
        return {k: v for k, v in headers.items() if k.lower() not in _FINGERPRINT_HEADERS_TO_REMOVE}

    def generate_cover_traffic_urls(self, count: int=5) -> list[str]:
        """Generate plausible cover traffic URLs."""
        return _RNG.sample(_DECOY_CLEARNET_DOMAINS, min(count, len(_DECOY_CLEARNET_DOMAINS)))
__all__ = ['ZeroAttributionEngine', 'strip_metadata', 'fingerprint_rotate_headers', 'generate_cover_traffic_urls']