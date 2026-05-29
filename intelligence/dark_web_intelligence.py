"""
Dark Web Intelligence Module
==============================

Tor/I2P crawling and hidden service analysis for deep OSINT research.
Self-hosted on M1 8GB with stealth capabilities.

Features:
- Tor hidden service crawling (.onion)
- I2P eepsite crawling (.i2p)
- Marketplace monitoring
- Forum intelligence gathering
- PGP key extraction
- Cryptocurrency address detection
- Stealth request routing through Tor
- Automatic captcha detection and handling

M1 Optimized: Streaming processing, lazy loading, minimal memory footprint
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import os
import re
import time
from collections import OrderedDict
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from urllib.parse import urljoin, urlparse

import aiohttp
import numpy as np

# Try to import socks for Tor support
try:
    import aiohttp_socks
    TOR_AVAILABLE = True
except ImportError:
    TOR_AVAILABLE = False

try:
    from selectolax.parser import HTMLParser as _SelectolaxHTMLParser
    SELECTOLAX_AVAILABLE = True
except ImportError:
    SELECTOLAX_AVAILABLE = False

from ..types import RiskLevel

logger = logging.getLogger(__name__)

# Optional deps for image extraction
try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    import numpy as np
    NP_AVAILABLE = True
except ImportError:
    NP_AVAILABLE = False


class DarkWebSource(Enum):
    """Types of dark web sources."""
    TOR_ONION = "tor_onion"
    I2P_EEPSITE = "i2p_eepsite"
    TORRENT_TRACKER = "torrent_tracker"
    PASTE_SITE = "paste_site"
    FORUM = "forum"
    MARKETPLACE = "marketplace"
    WHISTLEBLOWER = "whistleblower"


class OnionType(Enum):
    """Types of onion services."""
    V2 = "v2"  # 16 chars (deprecated)
    V3 = "v3"  # 56 chars (current)
    UNKNOWN = "unknown"


@dataclass
class HiddenService:
    """Represents a discovered hidden service."""
    address: str
    onion_type: OnionType
    source: DarkWebSource
    title: str | None = None
    description: str | None = None
    last_seen: float = field(default_factory=time.time)
    first_seen: float = field(default_factory=time.time)
    is_online: bool = False
    response_time_ms: float = 0.0
    server_signature: str | None = None
    bitcoin_addresses: list[str] = field(default_factory=list)
    monero_addresses: list[str] = field(default_factory=list)
    pgp_keys: list[str] = field(default_factory=list)
    linked_onions: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.MEDIUM


@dataclass
class DarkWebContent:
    """Content extracted from dark web."""
    url: str
    content_hash: str
    content_type: str
    title: str | None
    text_content: str
    extracted_at: float
    metadata: dict[str, Any] = field(default_factory=dict)
    cryptocurrency_addresses: dict[str, list[str]] = field(default_factory=dict)
    emails: list[str] = field(default_factory=list)
    pgp_blocks: list[str] = field(default_factory=list)
    magnet_links: list[str] = field(default_factory=list)
    raw_html: str = ""  # F216R: raw HTML for image extraction


@dataclass
class PGPKeyInfo:
    """Extracted PGP key information."""
    key_id: str
    fingerprint: str
    user_ids: list[str]
    creation_date: datetime | None
    key_type: str
    key_size: int
    raw_key: str


class TorProxyManager:
    """
    Manages Tor proxy connections for stealth crawling.

    Requires Tor to be running locally (brew install tor)
    """

    def __init__(
        self,
        proxy_host: str = "127.0.0.1",
        proxy_port: int = 9050,
        control_port: int = 9051,
        control_password: str | None = None
    ):
        self.proxy_host = proxy_host
        self.proxy_port = proxy_port
        self.control_port = control_port
        self.control_password = control_password
        self._session: aiohttp.ClientSession | None = None
        self._connector = None

    async def initialize(self) -> bool:
        """Initialize Tor proxy connection."""
        if not TOR_AVAILABLE:
            logger.error("aiohttp-socks not installed. Run: pip install aiohttp-socks")
            return False

        try:
            # Test if Tor is running
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.proxy_host, self.proxy_port),
                timeout=5.0
            )
            writer.close()
            await writer.wait_closed()

            # Create SOCKS5 connector
            self._connector = aiohttp_socks.ProxyConnector.from_url(
                f"socks5://{self.proxy_host}:{self.proxy_port}"
            )

            # Create session with extended timeout for Tor
            timeout = aiohttp.ClientTimeout(total=120, connect=60)
            self._session = aiohttp.ClientSession(
                connector=self._connector,
                timeout=timeout,
                headers={
                    "User-Agent": self._get_tor_browser_ua()
                }
            )

            logger.info(f"Tor proxy initialized: {self.proxy_host}:{self.proxy_port}")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize Tor proxy: {e}")
            return False

    def _get_tor_browser_ua(self) -> str:
        """Get Tor Browser User-Agent."""
        return "Mozilla/5.0 (Windows NT 10.0; rv:102.0) Gecko/20100101 Firefox/102.0"

    async def new_identity(self) -> bool:
        """Request new Tor identity (new exit node)."""
        if not self.control_password:
            logger.warning("No control password set, cannot request new identity")
            return False

        try:
            reader, writer = await asyncio.open_connection(
                self.proxy_host, self.control_port
            )

            # Authenticate
            writer.write(f'AUTHENTICATE "{self.control_password}"\r\n'.encode())
            await writer.drain()

            response = await reader.readline()
            if b"250" not in response:
                logger.error(f"Tor authentication failed: {response}")
                return False

            # Request new identity
            writer.write(b"SIGNAL NEWNYM\r\n")
            await writer.drain()

            response = await reader.readline()
            writer.close()
            await writer.wait_closed()

            if b"250" in response:
                logger.info("New Tor identity requested")
                # Wait for circuit to build
                await asyncio.sleep(5)
                return True

            return False

        except Exception as e:
            logger.error(f"Failed to get new Tor identity: {e}")
            return False

    def get_session(self) -> aiohttp.ClientSession | None:
        """Get aiohttp session configured for Tor."""
        return self._session

    async def close(self):
        """Close Tor connections."""
        if self._session:
            await self._session.close()
        if self._connector:
            await self._connector.close()

    async def __aenter__(self) -> TorProxyManager:
        """Async context manager entry - initializes Tor connection."""
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit - closes Tor connection."""
        await self.close()


class DarkWebCrawler:
    """
    Advanced dark web crawler for OSINT research.

    Crawls Tor hidden services and extracts intelligence:
    - Hidden service enumeration
    - Content extraction and indexing
    - Cryptocurrency address harvesting
    - PGP key discovery
    - Link graph analysis
    """

    # Bounded memory constants (M1 8GB)
    MAX_CONTENT_CACHE: int = 200
    MAX_VISITED_URLS: int = 5000
    MAX_DISCOVERED_SERVICES: int = 1000
    MAX_URL_QUEUE: int = 200  # bounded queue for discovered URLs

    # Regex patterns
    ONION_V2_PATTERN = re.compile(r"[a-z2-7]{16}\.onion")
    ONION_V3_PATTERN = re.compile(r"[a-z2-7]{56}\.onion")
    I2P_PATTERN = re.compile(r"[a-zA-Z0-9\-\.]+\.i2p")
    BTC_ADDRESS_PATTERN = re.compile(r"(bc1|[13])[a-zA-HJ-NP-Z0-9]{25,62}")
    XMR_ADDRESS_PATTERN = re.compile(r"4[0-9AB][1-9A-HJ-NP-Za-km-z]{93}")
    EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
    MAGNET_PATTERN = re.compile(r"magnet:\?xt=urn:btih:[a-fA-F0-9]{40}")
    PGP_BLOCK_PATTERN = re.compile(
        r"-----BEGIN PGP (PUBLIC|PRIVATE) KEY BLOCK-----.*?-----END PGP \1 KEY BLOCK-----",
        re.DOTALL
    )

    def __init__(
        self,
        tor_proxy: TorProxyManager | None = None,
        max_depth: int = 3,
        max_pages_per_site: int = 100,
        request_delay: float = 2.0,
        respect_robots_txt: bool = False  # Many dark sites don't have it
    ):
        self.tor_proxy = tor_proxy or TorProxyManager()
        self.max_depth = max_depth
        self.max_pages_per_site = max_pages_per_site
        self.request_delay = request_delay
        self.respect_robots_txt = respect_robots_txt

        # Bounded session state (M1 8GB)
        # OrderedDict provides FIFO LRU eviction on insert beyond limit
        self.discovered_services: Ordereddict[str, HiddenService] = OrderedDict()
        self.visited_urls: Ordereddict[str, bool] = OrderedDict()
        self.content_cache: Ordereddict[str, DarkWebContent] = OrderedDict()
        self.url_queue: asyncio.Queue = asyncio.Queue(maxsize=self.MAX_URL_QUEUE)

        # Statistics
        self.stats = {
            "pages_crawled": 0,
            "services_discovered": 0,
            "bitcoin_addresses": 0,
            "monero_addresses": 0,
            "pgp_keys_found": 0,
            "errors": 0
        }

    async def initialize(self) -> bool:
        """Initialize the crawler."""
        return await self.tor_proxy.initialize()

    async def crawl_onion(
        self,
        onion_address: str,
        depth: int = 0
    ) -> AsyncIterator[DarkWebContent]:
        """
        Crawl a Tor hidden service.

        Args:
            onion_address: .onion address (with or without .onion suffix)
            depth: Current crawl depth

        Yields:
            DarkWebContent objects
        """
        # Normalize address
        if not onion_address.endswith(".onion"):
            onion_address = f"{onion_address}.onion"

        url = f"http://{onion_address}"

        if url in self.visited_urls or depth > self.max_depth:
            return

        self._bounded_insert_visited_url(url)

        try:
            content = await self._fetch_page(url)
            if content:
                yield content

                # Extract and queue linked pages
                if depth < self.max_depth:
                    links = self._extract_links(content.text_content, onion_address)
                    for link in links[:10]:  # Limit breadth
                        if link not in self.visited_urls:
                            async for subcontent in self.crawl_onion(link, depth + 1):
                                yield subcontent

        except Exception as e:
            logger.error(f"Error crawling {url}: {e}")
            self.stats["errors"] += 1

    async def _fetch_page(self, url: str) -> DarkWebContent | None:
        """Fetch a single page through Tor."""
        session = self.tor_proxy.get_session()
        if not session:
            logger.error("No Tor session available")
            return None

        try:
            start_time = time.time()

            async with session.get(url, allow_redirects=True) as response:
                response_time = (time.time() - start_time) * 1000

                if response.status != 200:
                    logger.warning(f"HTTP {response.status} for {url}")
                    return None

                html = await response.text()

                # Extract content
                content = self._parse_content(url, html)
                content.response_time_ms = response_time

                # Update statistics
                self.stats["pages_crawled"] += 1
                self.stats["bitcoin_addresses"] += len(content.cryptocurrency_addresses.get("bitcoin", []))
                self.stats["monero_addresses"] += len(content.cryptocurrency_addresses.get("monero", []))
                self.stats["pgp_keys_found"] += len(content.pgp_blocks)

                self._bounded_insert_discovered_service(
                    url,
                    HiddenService(
                        address=url,
                        onion_type=OnionType.V3,
                        source=DarkWebSource.TOR_ONION,
                        is_online=True,
                        response_time_ms=response_time,
                    )
                )

                self._bounded_insert_content_cache(url, content)

                # Respect rate limiting
                await asyncio.sleep(self.request_delay)

                return content

        except TimeoutError:
            logger.warning(f"Timeout fetching {url}")
            return None
        except Exception as e:
            logger.error(f"Error fetching {url}: {e}")
            return None

    def _parse_content(self, url: str, html: str) -> DarkWebContent:
        """Parse HTML content and extract intelligence."""
        # F214OPT-A: selectolax-first + lxml fallback (same parser used historically)
        if SELECTOLAX_AVAILABLE:
            try:
                tree = _SelectolaxHTMLParser(html)
                for tag in tree.css("script, style"):
                    tag.decompose()
                text = tree.body.text(separator=" ", strip=True) if tree.body else ""
                title_tag = tree.css_first("title")
                title = title_tag.text(strip=True) if title_tag else None
                desc_tag = tree.css_first("meta[name='description']")
                meta_description = desc_tag.get("content", "") if desc_tag else ""
            except Exception:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(html, "lxml")
                for script in soup(["script", "style"]):
                    script.decompose()
                text = soup.get_text(separator=" ", strip=True)
                title_tag = soup.find("title")
                title = title_tag.get_text(strip=True) if title_tag else None
                desc_tag = soup.find("meta", attrs={"name": "description"})
                meta_description = desc_tag.get("content", "") if desc_tag else ""
        else:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "lxml")
            for script in soup(["script", "style"]):
                script.decompose()
            text = soup.get_text(separator=" ", strip=True)
            title_tag = soup.find("title")
            title = title_tag.get_text(strip=True) if title_tag else None
            desc_tag = soup.find("meta", attrs={"name": "description"})
            meta_description = desc_tag.get("content", "") if desc_tag else ""

        # Extract cryptocurrency addresses
        crypto_addresses = {
            "bitcoin": self.BTC_ADDRESS_PATTERN.findall(text),
            "monero": self.XMR_ADDRESS_PATTERN.findall(text)
        }

        # Extract emails
        emails = self.EMAIL_PATTERN.findall(text)

        # Extract PGP blocks
        pgp_blocks = self.PGP_BLOCK_PATTERN.findall(html)

        # Extract magnet links
        magnet_links = self.MAGNET_PATTERN.findall(text)

        # Extract metadata
        metadata = {
            "meta_description": meta_description,
            "meta_keywords": "",
            "server": ""
        }

        return DarkWebContent(
            url=url,
            content_hash=hashlib.sha256(html.encode()).hexdigest(),
            content_type="text/html",
            title=title,
            text_content=text,
            extracted_at=time.time(),
            metadata=metadata,
            cryptocurrency_addresses=crypto_addresses,
            emails=emails,
            pgp_blocks=[p[0] for p in pgp_blocks],
            magnet_links=magnet_links,
            raw_html=html,  # F216R: raw HTML preserved for image extraction
        )

    async def extract_and_encode_images(
        self,
        html: str,
        page_url: str,
        sprint_id: str,
        fetch_coordinator,
        vision_encoder,
        vector_store,
    ) -> list[dict]:
        """
        Sprint F214R: Extract images from crawled HTML and store VisionEncoder embeddings.

        Gate: HLEDAC_ENABLE_IMAGE_OSINT=1 (default: off).
        Bounded: max 3 images per page, 512KB per image, 8s timeout.
        Fail-soft: any exception → log warning, return [].
        """
        if not os.getenv("HLEDAC_ENABLE_IMAGE_OSINT"):
            return []

        if not PIL_AVAILABLE or not NP_AVAILABLE:
            logger.warning("PIL or numpy not available, skipping image extraction")
            return []

        try:
            if SELECTOLAX_AVAILABLE:
                tree = _SelectolaxHTMLParser(html)
                img_tags_raw = tree.css("img[src]")
            else:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(html, "html.parser")
                img_tags_raw = soup.find_all("img", src=True)
        except Exception as exc:
            logger.warning("HTML parse failed for %s: %s", page_url, exc)
            return []

        # Filter: skip data URIs, #, empty, tracking pixels (width+height < 20px)
        candidates: list[str] = []
        seen_srcs: set[str] = set()
        for img in img_tags_raw[:10]:
            src = (img.attributes.get("src") if hasattr(img, "attributes") else img.get("src", "")).strip()
            if not src or src.startswith("data:") or src.startswith("#") or src in seen_srcs:
                continue
            seen_srcs.add(src)
            w = img.attributes.get("width") if hasattr(img, "attributes") else img.get("width")
            h = img.attributes.get("height") if hasattr(img, "attributes") else img.get("height")
            try:
                if w and h and int(w) < 20 and int(h) < 20:
                    continue
            except (ValueError, TypeError):
                pass
            candidates.append(urljoin(page_url, src))
            if len(candidates) >= 3:
                break

        if not candidates:
            return []

        results: list[dict] = []
        for img_url in candidates:
            try:
                # Download via FetchCoordinator —JA3 stealth transport
                # Content-Length check (>512KB) handled by fetch_coordinator
                resp = await fetch_coordinator.fetch(img_url, timeout=8.0)
                if resp is None:
                    continue
                body = resp.get("body") if isinstance(resp, dict) else None
                if body is None:
                    continue
                if isinstance(body, str):
                    body = body.encode()
                if len(body) > 512 * 1024:
                    logger.debug("Image exceeds 512KB limit: %s", img_url)
                    continue

                # Validate PIL-openable
                try:
                    pil_img = Image.open(io.BytesIO(body))
                    pil_img = pil_img.convert("RGB")
                except Exception:
                    logger.debug("Not a valid image: %s", img_url)
                    continue

                # F216R: Steganography check via security/stego_detector.py (canonical)
                stego_result: dict = {"stego_detected": False, "confidence": 0.0}
                try:
                    import tempfile
                    from pathlib import Path

                    from hledac.universal.security.stego_detector import quick_stego_check

                    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                        tmp_path = Path(tmp.name)
                        try:
                            pil_img.save(tmp_path, format="JPEG", quality=85)
                            # quick_stego_check is async def — call directly with await
                            raw_result = await quick_stego_check(tmp_path)
                            stego_result = {
                                "stego_detected": raw_result.get("is_suspicious", False),
                                "confidence": raw_result.get("confidence", 0.0),
                            }
                        finally:
                            try:
                                tmp_path.unlink(missing_ok=True)
                            except Exception:
                                pass
                except Exception as exc:
                    logger.debug("Stego check failed for %s: %s", img_url, exc)
                    stego_result = {"stego_detected": False, "confidence": 0.0}

                # Encode batch (VisionEncoder handles CoreML/ANE internally)
                embeddings = vision_encoder.encode_batch([body])
                if not embeddings or embeddings[0] is None:
                    logger.warning("VisionEncoder returned None for: %s", img_url)
                    continue

                emb = embeddings[0]
                if hasattr(emb, "tolist"):
                    emb = emb.tolist()

                # Store via vector_store.add_vectors — table="image"
                # np already imported at module level (NP_AVAILABLE guard passed above)
                try:
                    vec_id = f"img_{sprint_id}_{hashlib.md5(img_url.encode()).hexdigest()[:12]}"
                    vector_store.add_vectors(
                        ids=[vec_id],
                        vectors=np.array([emb], dtype=np.float32),
                        index_type="image",
                    )
                    stored = True
                except Exception as exc:
                    logger.warning("Vector store write failed for %s: %s", img_url, exc)
                    stored = False

                results.append(
                    {
                        "img_url": img_url,
                        "embedding_dim": len(emb),
                        "stored": stored,
                        "stego_detected": stego_result.get("stego_detected", False),
                        "stego_confidence": stego_result.get("confidence", 0.0),
                        "stego_signals": stego_result.get("signals", []),
                    }
                )
            except Exception as exc:
                logger.warning("Image extract/encode failed for %s: %s", img_url, exc)
                continue

        logger.debug(
            "Image extraction: %d/%d images processed for %s",
            len(results),
            len(candidates),
            page_url,
        )
        return results

    def _extract_links(self, html: str, base_domain: str) -> list[str]:
        """Extract .onion links from content."""
        links: list[str] = []
        seen: set[str] = set()
        if SELECTOLAX_AVAILABLE:
            try:
                tree = _SelectolaxHTMLParser(html)
                for anchor in tree.css("a[href]"):
                    href = anchor.attributes.get("href", "")
                    parsed = urlparse(href)
                    if not parsed.netloc:
                        href = urljoin(f"http://{base_domain}", href)
                        parsed = urlparse(href)
                    if ".onion" in parsed.netloc and parsed.netloc not in seen:
                        seen.add(parsed.netloc)
                        links.append(parsed.netloc)
                return links
            except Exception:
                pass
        # Fallback
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        for link in soup.find_all("a", href=True):
            href = link["href"]
            parsed = urlparse(href)
            if not parsed.netloc:
                href = urljoin(f"http://{base_domain}", href)
                parsed = urlparse(href)
            if ".onion" in parsed.netloc and parsed.netloc not in seen:
                seen.add(parsed.netloc)
                links.append(parsed.netloc)
        return links

    def search_onion_addresses(self, text: str) -> list[tuple[str, OnionType]]:
        """
        Search text for onion addresses.

        Returns:
            List of (address, type) tuples
        """
        addresses = []

        # Find v3 addresses
        for match in self.ONION_V3_PATTERN.findall(text):
            addresses.append((match, OnionType.V3))

        # Find v2 addresses (deprecated but still exist)
        for match in self.ONION_V2_PATTERN.findall(text):
            addresses.append((match, OnionType.V2))

        return addresses

    async def monitor_service(self, onion_address: str, interval_minutes: int = 60) -> AsyncIterator[dict[str, Any]]:
        """
        Continuously monitor a hidden service for changes.

        Args:
            onion_address: .onion address to monitor
            interval_minutes: Check interval in minutes

        Yields:
            Change notifications
        """
        last_hash = None

        while True:
            try:
                url = f"http://{onion_address}.onion"
                content = await self._fetch_page(url)

                if content:
                    current_hash = content.content_hash

                    if last_hash and current_hash != last_hash:
                        yield {
                            "type": "content_change",
                            "address": onion_address,
                            "timestamp": time.time(),
                            "old_hash": last_hash,
                            "new_hash": current_hash,
                            "title": content.title
                        }

                    last_hash = current_hash
                else:
                    yield {
                        "type": "offline",
                        "address": onion_address,
                        "timestamp": time.time()
                    }

                await asyncio.sleep(interval_minutes * 60)

            except Exception as e:
                logger.error(f"Monitor error for {onion_address}: {e}")
                await asyncio.sleep(interval_minutes * 60)

    def get_statistics(self) -> dict[str, Any]:
        """Get crawling statistics with bounded truth."""
        return {
            **self.stats,
            "discovered_services_size": len(self.discovered_services),
            "discovered_services_limit": self.MAX_DISCOVERED_SERVICES,
            "visited_urls_size": len(self.visited_urls),
            "visited_urls_limit": self.MAX_VISITED_URLS,
            "content_cache_size": len(self.content_cache),
            "content_cache_limit": self.MAX_CONTENT_CACHE,
        }

    # ------------------------------------------------------------------
    # Bounded helpers (M1 8GB — prevent unbounded memory growth)
    # ------------------------------------------------------------------

    def _bounded_insert_content_cache(self, url: str, content: DarkWebContent) -> None:
        """Insert into content_cache with FIFO LRU eviction at limit."""
        if url in self.content_cache:
            self.content_cache.move_to_end(url)
        else:
            if len(self.content_cache) >= self.MAX_CONTENT_CACHE:
                self.content_cache.popitem(last=False)
        self.content_cache[url] = content

    def _bounded_insert_visited_url(self, url: str) -> None:
        """Insert into visited_urls with FIFO LRU eviction at limit."""
        if url in self.visited_urls:
            self.visited_urls.move_to_end(url)
        else:
            if len(self.visited_urls) >= self.MAX_VISITED_URLS:
                self.visited_urls.popitem(last=False)
        self.visited_urls[url] = True

    def _bounded_insert_discovered_service(self, url: str, service: HiddenService) -> None:
        """Insert into discovered_services with FIFO eviction at limit."""
        if url in self.discovered_services:
            self.discovered_services.move_to_end(url)
        else:
            if len(self.discovered_services) >= self.MAX_DISCOVERED_SERVICES:
                self.discovered_services.popitem(last=False)
        self.discovered_services[url] = service

    def reset_session(self) -> None:
        """Clear all session state (bounded structures + queues)."""
        self.discovered_services.clear()
        self.visited_urls.clear()
        self.content_cache.clear()
        while not self.url_queue.empty():
            try:
                self.url_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        self.stats = {
            "pages_crawled": 0,
            "services_discovered": 0,
            "bitcoin_addresses": 0,
            "monero_addresses": 0,
            "pgp_keys_found": 0,
            "errors": 0
        }

    async def close(self):
        """Close crawler and cleanup session state."""
        self.reset_session()
        await self.tor_proxy.close()


class CryptocurrencyAnalyzer:
    """
    Analyzes cryptocurrency addresses found in dark web content.

    Tracks transactions, balances (where possible), and relationships.
    """

    def __init__(self):
        self.address_cache: dict[str, dict[str, Any]] = {}

    def analyze_bitcoin_address(self, address: str) -> dict[str, Any]:
        """
        Analyze Bitcoin address.

        Note: Without external APIs, we can only do basic validation.
        For full analysis, would need blockchain.info or similar API.
        """
        # Basic validation
        is_valid = self._validate_bitcoin_address(address)

        analysis = {
            "address": address,
            "type": self._get_bitcoin_address_type(address),
            "is_valid": is_valid,
            "possible_type": "segwit" if address.startswith("bc1") else "legacy/p2sh"
        }

        return analysis

    def _validate_bitcoin_address(self, address: str) -> bool:
        """Basic Bitcoin address validation."""
        if address.startswith("bc1"):
            # Bech32 validation would require bech32 library
            return len(address) in [42, 62]
        elif address.startswith("1") or address.startswith("3"):
            # Base58Check - would require base58 library for full validation
            return 25 <= len(address) <= 35
        return False

    def _get_bitcoin_address_type(self, address: str) -> str:
        """Get Bitcoin address type."""
        if address.startswith("bc1q"):
            return "P2WPKH" if len(address) == 42 else "P2WSH"
        elif address.startswith("bc1p"):
            return "P2TR"  # Taproot
        elif address.startswith("1"):
            return "P2PKH"
        elif address.startswith("3"):
            return "P2SH"
        return "unknown"

    def cluster_addresses(self, addresses: list[str]) -> dict[str, list[str]]:
        """
        Cluster addresses that might belong to the same entity.

        Uses heuristics like:
        - Common input ownership
        - Change address patterns
        """
        # This would require transaction graph analysis
        # Placeholder for clustering logic
        clusters = {"unknown": addresses}
        return clusters


# Export
__all__ = [
    "TorProxyManager",
    "DarkWebCrawler",
    "HiddenService",
    "DarkWebContent",
    "PGPKeyInfo",
    "CryptocurrencyAnalyzer",
    "DarkWebSource",
    "OnionType",
    "darkweb_content_to_canonical",
    "DHTFinding",
    "dht_content_to_canonical",
]


def darkweb_content_to_canonical(content: DarkWebContent, query: str) -> CanonicalFinding:
    """
    Sprint F251: Map DarkWebCrawler output → CanonicalFinding for sprint ingestion.

    Bounded: payload_text truncated to 3000 chars, fail-safe if title is None.
    """
    import hashlib

    from hledac.universal.knowledge.duckdb_store import CanonicalFinding

    title = content.title or "onion"
    body = content.text_content or ""
    payload = f"{title}\n{body[:3000]}"

    # relevance_score stored in metadata by DarkWebCrawler enrichors
    meta = content.metadata or {}
    confidence = float(meta.get("relevance_score", 0.5))
    confidence = max(0.0, min(1.0, confidence))  # clamp to [0.0, 1.0]

    finding_id = f"dw_{hashlib.md5(content.url.encode()).hexdigest()[:16]}"

    return CanonicalFinding(
        finding_id=finding_id,
        query=query,
        source_type="onion_discovery",
        confidence=confidence,
        ts=content.extracted_at,
        provenance=(content.url,),
        payload_text=payload,
    )


# =============================================================================
# DHT Discovery Adapter — Sprint F214Q
# =============================================================================
@dataclass
class DHTFinding:
    """Structured output from DHT crawl operations."""
    info_hash: str
    name: str = ""
    files: list[dict] = field(default_factory=list)
    size_bytes: int = 0
    peers: int = 0
    source: str = "dht"


def dht_content_to_canonical(dht_result: DHTFinding, query: str) -> CanonicalFinding:
    """
    Sprint F214Q: Map DHT crawl result → CanonicalFinding for sprint ingestion.

    Bounded: payload_text truncated to 3000 chars, fail-safe.
    INVARIANT: DHT queries NEVER go over Tor — clearnet UDP only.
    """
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding

    name = dht_result.name or "dht_torrent"
    # Build magnet URI from info_hash
    magnet = f"magnet:?xt=urn:btih:{dht_result.info_hash}"
    if dht_result.name:
        magnet += f"&dn={dht_result.name}"

    body = f"info_hash={dht_result.info_hash} peers={dht_result.peers} size={dht_result.size_bytes}"
    if dht_result.files:
        file_names = ", ".join(f.get("name", "") for f in dht_result.files[:10])
        body += f"\nfiles: {file_names}"
    payload = f"{name}\n{magnet}\n{body[:3000]}"

    # confidence based on peer count (more peers = more confirmed)
    confidence = min(0.9, 0.3 + (dht_result.peers / 100))
    confidence = max(0.0, min(1.0, confidence))

    finding_id = f"dht_{hashlib.md5(dht_result.info_hash.encode()).hexdigest()[:16]}"

    return CanonicalFinding(
        finding_id=finding_id,
        query=query,
        source_type="dht_discovery",
        confidence=confidence,
        ts=time.time(),
        provenance=(f"info_hash:{dht_result.info_hash}",),
        payload_text=payload,
    )
