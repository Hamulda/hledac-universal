from __future__ import annotations

"""JS Source Maps extractor – retrieves and parses source maps to discover hidden source paths."""
import json
import logging

from hledac.universal.network.session_runtime import async_get_aiohttp_session

logger = logging.getLogger(__name__)

# Try to import aiohttp with fail-safe
try:
    import aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:
    aiohttp = None
    AIOHTTP_AVAILABLE = False


class _JSSourceMapExtractor:
    """Extracts source paths from JavaScript source maps."""

    MAX_MAP_SIZE = 1024 * 1024  # 1MB
    MAX_PATHS = 50
    # F185D: use session_runtime canonical constants
    _CONNECT_TIMEOUT_S: float = 10.0
    _READ_TIMEOUT_S: float = 10.0

    async def extract_from_bundle(self, bundle_url: str) -> list[str]:
        """Download source map and return extracted source paths."""
        if not AIOHTTP_AVAILABLE:
            return []

        # Construct map URL (common patterns: .map suffix)
        map_url = self._guess_map_url(bundle_url)
        if not map_url:
            return []

        try:
            session = await async_get_aiohttp_session()
            async with session.get(
                map_url,
                timeout=aiohttp.ClientTimeout(
                    connect=self._CONNECT_TIMEOUT_S,
                    sock_read=self._READ_TIMEOUT_S,
                ),
            ) as resp:
                if resp.status != 200:
                    return []
                content = await resp.read()
                if len(content) > self.MAX_MAP_SIZE:
                    logger.debug(f"Source map too large: {len(content)} bytes")
                    return []
                data = json.loads(content)
                sources = data.get('sources', [])
                if not isinstance(sources, list):
                    return []
                # Filter and truncate
                paths = [s for s in sources if isinstance(s, str) and len(s) < 500][:self.MAX_PATHS]
                return paths
        except Exception as e:
            logger.debug(f"Source map extraction failed for {bundle_url}: {e}")
            return []

    def _guess_map_url(self, bundle_url: str) -> str | None:
        """Guess the source map URL from the bundle URL."""
        if bundle_url.endswith('.js'):
            return bundle_url + '.map'
        return None
