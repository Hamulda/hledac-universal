"""
OnionSeedManager — curated .onion seed list management + Ahmia discovery.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.parse
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiohttp

logger = logging.getLogger(__name__)

# Onion link extractors (precompiled — module level, never inside functions)
_RE_ONION_V3 = re.compile(r"\b[a-z2-7]{56}\.onion\b")
_RE_ONION_V2 = re.compile(r"\b[a-z2-7]{16}\.onion\b")


class OnionSeedManager:
    """
    Spravuje .onion seed list pro dark web crawling.

    B4: CURATED_SEEDS — hardcoded, veřejné read-only indexované zdroje.
    """

    CURATED_SEEDS: list[str] = [
        # Hidden Wiki — veřejný read-only index
        "http://zqktlwiuavvvqqt4ybvgvi7tyo4hjl5xgfuvpdf6otjiycgwqbym2qad.onion/wiki/",
        # Ahmia .onion — FiMX index
        "http://juhanurmihxlp77nkq76byazcldy2hlmovfu2epvl5ankdibsot4csyd.onion/",
    ]

    def __init__(self, seeds_path: Path | None = None) -> None:
        if seeds_path is None:
            from hledac.universal.paths import TOR_ROOT
            seeds_path = TOR_ROOT / "onion_seeds.json"
        self._path: Path = seeds_path
        self._seeds: set[str] = set(self.CURATED_SEEDS)

    # -------------------------------------------------------------------------
    # Persistence
    # -------------------------------------------------------------------------

    async def load(self) -> None:
        """Načíst persistované seeds z disku."""
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text())
            loaded = set(data.get("seeds", []))
            self._seeds |= loaded
            logger.debug(f"Loaded {len(loaded)} seeds from disk (total: {len(self._seeds)})")
        except Exception as e:
            logger.warning(f"Seed load failed: {e}")

    async def save(self) -> None:
        """Persistovat seeds na disk."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._path.write_text(json.dumps({
                "seeds": list(self._seeds),
                "ts": time.time()
            }))
        except Exception as e:
            logger.warning(f"Seed save failed: {e}")

    # -------------------------------------------------------------------------
    # Seed management
    # -------------------------------------------------------------------------

    def add_seed(self, url: str) -> None:
        """
        Přidat .onion URL jako nový seed.

        B4 invariant: přijímáme pouze http(s) URLs obsahující .onion.
        """
        if ".onion" in url and url.startswith("http"):
            self._seeds.add(url)

    def get_seeds(self, limit: int = 10) -> list[str]:
        """
        Vrátit seeds pro crawling — curated seeds first.

        Returns up to *limit* seeds: all curated first, then rest.
        """
        curated = [s for s in self.CURATED_SEEDS if s in self._seeds]
        rest = [s for s in self._seeds if s not in self.CURATED_SEEDS]
        return (curated + rest)[:limit]

    # -------------------------------------------------------------------------
    # Ahmia discovery
    # -------------------------------------------------------------------------

    async def discover_from_ahmia(
        self,
        query: str,
        session: object | None = None,
    ) -> list[str]:
        """
        Přidat nové onion seeds z Ahmia clearnet search.

        Uses the provided aiohttp.ClientSession if given,
        otherwise creates a temporary one.
        B6: 15s timeout per Ahmia request.
        """
        import aiohttp

        ahmia_url = f"https://ahmia.fi/search/?q={urllib.parse.quote(query)}"
        close_session = session is None

        try:
            if session is None:
                session = aiohttp.ClientSession()

            async with session.get(
                ahmia_url,
                timeout=aiohttp.ClientTimeout(total=15),
                headers={"User-Agent": "Hledac/1.0 OSINT research tool"},
            ) as resp:
                if resp.status != 200:
                    return []
                html = await resp.text()

            # Extract .onion V3 and V2 links
            new_seeds: set[str] = set()
            for pattern in (_RE_ONION_V3, _RE_ONION_V2):
                new_seeds.update(pattern.findall(html))

            discovered: list[str] = []
            for seed in new_seeds:
                url = f"http://{seed}/"
                if url not in self._seeds:
                    self.add_seed(url)
                    discovered.append(url)

            if discovered:
                logger.info(f"Ahmia discovered {len(discovered)} new seeds for '{query}'")

            return discovered

        except Exception as e:
            logger.warning(f"Ahmia discovery failed: {e}")
            return []
        finally:
            if close_session and session is not None:
                await session.close()

    # -------------------------------------------------------------------------
    # Sprint 8SC: Ahmia live discovery via Tor
    # -------------------------------------------------------------------------

    async def discover_via_tor(
        self,
        query: str,
        tor_session: aiohttp.ClientSession,
    ) -> list[str]:
        """Ahmia .onion discovery přes Tor.
        Fallback na clearnet Ahmia pokud Tor nedostupný."""
        import aiohttp

        AHMIA_ONION = (
            "juhanurmihxlp77nkq76byazcldy2hmbbj3j3jbcrpvzmntbxnjbxqd.onion"
        )
        q_enc = urllib.parse.quote_plus(query)

        async def _fetch(url: str, sess: aiohttp.ClientSession) -> str:
            async with sess.get(
                url, timeout=aiohttp.ClientTimeout(total=30)
            ) as r:
                r.raise_for_status()
                return await r.text()

        html = ""
        try:
            html = await _fetch(
                f"http://{AHMIA_ONION}/search/?q={q_enc}", tor_session
            )
            logger.info(f"Ahmia .onion discovery: got {len(html)} chars")
        except Exception as e:
            logger.warning(f"Ahmia .onion failed: {e} — trying clearnet fallback")
            try:
                async with aiohttp.ClientSession() as s:
                    html = await _fetch(
                        f"https://ahmia.fi/search/?q={q_enc}", s
                    )
            except Exception as e2:
                logger.warning(f"Ahmia clearnet also failed: {e2}")
                return []

        # Parse .onion V3 addresses z HTML (56 base32 chars)
        onion_re = re.compile(
            r"([a-z2-7]{56}\.onion)", re.IGNORECASE
        )
        found = list(set(onion_re.findall(html)))
        logger.info(
            f"Ahmia discovery '{query}': found {len(found)} .onion addresses"
        )

        # Přidat do seed manageru a uložit
        for addr in found:
            self._seeds.add(addr)
        if found:
            await self.save()
        return found
