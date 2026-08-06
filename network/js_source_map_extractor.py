"""JS Source Maps extractor – retrieves and parses source maps to discover hidden source paths."""

import asyncio
import logging  # noqa: E402


import msgspec.json as _json  # noqa: E402

from hledac.universal.network.session_runtime import async_get_httpx_session  # noqa: E402

logger = logging.getLogger(__name__)

# F4XX: httpx replaces aiohttp — no longer need conditional import
# async_get_httpx_session() always returns httpx.AsyncClient


class _JSSourceMapExtractor:
    """Extracts source paths from JavaScript source maps."""

    MAX_MAP_SIZE = 1024 * 1024  # 1MB
    MAX_PATHS = 50
    # F185D: use session_runtime canonical constants
    _CONNECT_TIMEOUT_S: float = 10.0
    _READ_TIMEOUT_S: float = 10.0

    async def extract_from_bundle(self, bundle_url: str) -> list[str]:
        """Download source map and return extracted source paths."""
        # Construct map URL (common patterns: .map suffix)
        map_url = self._guess_map_url(bundle_url)
        if not map_url:
            return []

        try:
            session = await async_get_httpx_session()
            async with asyncio.timeout(self._READ_TIMEOUT_S):
                resp = await session.get(map_url)
                if resp.status_code != 200:
                    return []
                content = resp.read()
                if len(content) > self.MAX_MAP_SIZE:
                    logger.debug(f"Source map too large: {len(content)} bytes")
                    return []
                data = _json.decode(content)
                sources = data.get("sources", [])
                if not isinstance(sources, list):
                    return []
                # Filter and truncate
                paths = [s for s in sources if isinstance(s, str) and len(s) < 500][: self.MAX_PATHS]
                return paths
        except asyncio.TimeoutError:
            logger.debug(f"Source map timeout for {bundle_url}")
            return []
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug(f"Source map extraction failed for {bundle_url}: {e}")
            return []

    def _guess_map_url(self, bundle_url: str) -> str | None:
        """Guess the source map URL from the bundle URL."""
        if bundle_url.endswith(".js"):
            return bundle_url + ".map"
        return None
