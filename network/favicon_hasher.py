"""Favicon hashing using MurmurHash3 for service fingerprinting."""
import hashlib
import logging

logger = logging.getLogger(__name__)

try:
    import mmh3
    MMH3_AVAILABLE = True
except ImportError:
    MMH3_AVAILABLE = False
    logger.warning("[FAVICON] mmh3 not installed, fallback to sha256")


class _FaviconHasher:
    """Compute stable favicon hash (MurmurHash3 preferred, fallback SHA256)."""

    def hash_favicon(self, favicon_bytes: bytes) -> str | None:
        """Return hash string (e.g., 'mmh3:1234567890' or 'sha256:abc123...')."""
        if not favicon_bytes:
            return None

        if MMH3_AVAILABLE and len(favicon_bytes) > 0:
            hash_val = mmh3.hash(favicon_bytes)
            return f"mmh3:{hash_val}"
        else:
            hash_val = hashlib.sha256(favicon_bytes).hexdigest()[:16]
            return f"sha256:{hash_val}"
