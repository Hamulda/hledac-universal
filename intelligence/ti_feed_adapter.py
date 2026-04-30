"""
Sprint 8PB: Local TI Feed Mirrors + TIFeedAdapter

Mirrors-first princip: lokální mirror se zkouší PŘED jakýmkoli HTTP fetch.
Mirror miss → HTTP fallback s log INFO.

Struktura:
- MirrorManager: stahuje a spravuje lokální mirror soubory
- TIFeedAdapter: unified TI feed adapter s local_mirror tier (priority=95)
- Parsery: parse_cisa_kev, parse_urlhaus_csv, parse_threatfox_json, parse_feodo_json, parse_openphish_txt
"""

from __future__ import annotations

import warnings
warnings.warn(
    "intelligence.ti_feed_adapter je deprecated. "
    "Používej discovery.ti_feed_adapter.",
    DeprecationWarning, stacklevel=2
)

import asyncio
import csv
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp

logger = logging.getLogger(__name__)

# =============================================================================
# Constants
# =============================================================================

MIRRORS_ROOT = Path.home() / ".hledac" / "mirrors"
_MAX_MIRROR_AGE_SECONDS = 24 * 60 * 60  # 24 hours
_MAX_MIRROR_BYTES = 50_000_000  # 50MB cap

# Mirror source definitions
MIRROR_SOURCES: Dict[str, Dict[str, Any]] = {
    "cisa_kev": {
        "url": "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json",
        "dest": "cisa_kev.json",
        "method": "GET",
        "parser": "parse_cisa_kev",
    },
    "urlhaus_recent": {
        "url": "https://urlhaus.abuse.ch/downloads/csv_recent/",
        "dest": "urlhaus_recent.csv",
        "method": "GET",
        "parser": "parse_urlhaus_csv",
    },
    "threatfox_ioc": {
        "url": "https://threatfox-api.abuse.ch/api/v1/",
        "dest": "threatfox_ioc.json",
        "method": "POST",
        "body": {"query": "get_iocs", "days": 7},
        "parser": "parse_threatfox_json",
    },
    "feodo_ip": {
        "url": "https://feodotracker.abuse.ch/downloads/ipblocklist.json",
        "dest": "feodo_ip.json",
        "method": "GET",
        "parser": "parse_feodo_json",
    },
    "openphish_feed": {
        "url": "https://openphish.com/feed.txt",
        "dest": "openphish_feed.txt",
        "method": "GET",
        "parser": "parse_openphish_txt",
    },
    "nvd_recent": {
        "url": "https://services.nvd.nist.gov/rest/json/cves/2.0/",
        "dest": "nvd_recent.json",
        "method": "GET",
        "params": {"resultsPerPage": 200, "startIndex": 0},
        "parser": "parse_nvd_recent",
    },
}

# =============================================================================
# MirrorManager
# =============================================================================


class MirrorManager:
    """
    Stahuje a spravuje lokální TI feed mirrors.
    Mirror files jsou staženy pouze pokud:
    1. Neexistují
    2. Nebo jsou starší 24 hodin
    """

    def __init__(self, mirrors_root: Path = MIRRORS_ROOT) -> None:
        self._mirrors_root = mirrors_root
        self._mirrors_root.mkdir(parents=True, exist_ok=True)
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=120, connect=30),
                headers={"User-Agent": "curl/7.0"},
            )
        return self._session

    def _mirror_path(self, name: str) -> Path:
        source = MIRROR_SOURCES.get(name)
        if source is None:
            raise ValueError(f"Unknown mirror source: {name}")
        return self._mirrors_root / source["dest"]

    def _is_fresh(self, path: Path) -> bool:
        """Kontrola mtime - fresh pokud mladší 24h."""
        if not path.exists():
            return False
        try:
            mtime = path.stat().st_mtime
            return (time.monotonic() - mtime) < _MAX_MIRROR_AGE_SECONDS
        except OSError:
            return False

    async def download_mirror(
        self,
        name: str,
        max_bytes: int = _MAX_MIRROR_BYTES,
    ) -> Optional[Path]:
        """
        Stáhne mirror file pokud není fresh.

        Returns:
            Path to downloaded file, or None if skipped/failed.
        """
        source = MIRROR_SOURCES.get(name)
        if source is None:
            logger.warning(f"Unknown mirror source: {name}")
            return None

        dest_path = self._mirror_path(name)

        # Skip pokud fresh
        if self._is_fresh(dest_path):
            logger.debug(f"Mirror {name} is fresh, skipping download")
            return dest_path

        url = source["url"]
        method = source.get("method", "GET")
        body = source.get("body")
        params = source.get("params")

        logger.info(f"Downloading mirror {name} from {url}")

        tmp_path = dest_path.with_suffix(dest_path.suffix + ".tmp")
        bytes_downloaded = 0

        try:
            session = await self._get_session()

            kwargs: Dict[str, Any] = {}
            if params:
                kwargs["params"] = params
            if body:
                kwargs["data"] = json.dumps(body)
                kwargs["headers"] = {"Content-Type": "application/json"}

            async with session.request(method, url, **kwargs) as resp:
                if resp.status != 200:
                    logger.warning(
                        f"Mirror {name} download failed: HTTP {resp.status}"
                    )
                    # Fail-open: pokud mirror selže, pokračuj bez něj
                    if tmp_path.exists():
                        try:
                            tmp_path.unlink()
                        except OSError:
                            pass
                    return None

                bytes_downloaded = 0
                with open(tmp_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(8192):
                        bytes_downloaded += len(chunk)
                        if bytes_downloaded > max_bytes:
                            logger.warning(
                                f"Mirror {name} exceeds {max_bytes} bytes, truncating"
                            )
                            break
                        f.write(chunk)

                # Atomic rename
                tmp_path.rename(dest_path)
                logger.info(
                    f"Mirror {name} downloaded: {bytes_downloaded} bytes → {dest_path}"
                )
                return dest_path

        except asyncio.CancelledError:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            raise
        except Exception as e:
            logger.warning(f"Mirror {name} download error: {e}")
            # Fail-open: pokud mirror selže, pokračuj bez něj
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            return None

    async def init_mirrors(self) -> Dict[str, Optional[Path]]:
        """
        Inicializuje všechny mirrors (download pokud nejsou fresh).
        Vrací dict name → Path (nebo None pro neúspěšné).
        """
        results = {}
        for name in MIRROR_SOURCES:
            results[name] = await self.download_mirror(name)
        return results

    def get_mirror_path(self, name: str) -> Optional[Path]:
        """Vrátí path k mirror file pokud existuje."""
        path = self._mirror_path(name)
        return path if path.exists() else None

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()


# =============================================================================
# Parser Functions
# =============================================================================


def parse_cisa_kev(path: Path) -> List[Dict[str, Any]]:
    """
    Parse CISA KEV JSON.
    Vrací list dicts s cveID.
    """
    results = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.loads(f.read())

        # CISA KEV má strukturu: {"vulnerabilities": [...]}
        vulns = data.get("vulnerabilities", [])
        for v in vulns:
            cve_id = v.get("cveID") or v.get("cve_id")
            if cve_id:
                results.append({
                    "cve_id": cve_id,
                    "vendor_project": v.get("vendorProject", ""),
                    "product": v.get("product", ""),
                    "date_added": v.get("dateAdded", ""),
                    "short_description": v.get("shortDescription", ""),
                    "known_ransomware_campaign_use": v.get("knownRansomwareCampaignUse", ""),
                    "notes": v.get("notes", ""),
                })
    except Exception as e:
        logger.warning(f"Failed to parse CISA KEV: {e}")
    return results


def parse_urlhaus_csv(path: Path) -> List[Dict[str, Any]]:
    """
    Parse URLhaus CSV format.
    Vrací list dicts s url a tags.
    """
    results = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                url = row.get("url", "").strip()
                if url:
                    results.append({
                        "url": url,
                        "tags": row.get("tags", "").strip(),
                        "threat": row.get("threat", "").strip(),
                        "date_added": row.get("date_added", "").strip(),
                        "status": row.get("status", "").strip(),
                    })
    except Exception as e:
        logger.warning(f"Failed to parse URLhaus CSV: {e}")
    return results


def parse_threatfox_json(path: Path) -> List[Dict[str, Any]]:
    """
    Parse ThreatFox JSON.
    Vrací list dicts s IOC.
    """
    results = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.loads(f.read())

        # ThreatFox API response: {"data": [...]}
        for item in data.get("data", []):
            ioc = item.get("ioc", "")
            ioc_type = item.get("ioc_type", "")
            malware = item.get("malware", "")
            if ioc:
                results.append({
                    "ioc": ioc,
                    "ioc_type": ioc_type,
                    "malware": malware,
                    "confidence": item.get("confidence", ""),
                    "date_added": item.get("date_added", ""),
                })
    except Exception as e:
        logger.warning(f"Failed to parse ThreatFox JSON: {e}")
    return results


def parse_feodo_json(path: Path) -> List[Dict[str, Any]]:
    """
    Parse Feodo IP blocklist JSON.
    Vrací list dicts s IP/malware/port.
    """
    results = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.loads(f.read())

        # Feodo má strukturu: [{malware: "...", ip: "...", port: ..., ...}]
        if isinstance(data, list):
            for item in data:
                ip = item.get("ip", "")
                if ip:
                    results.append({
                        "ip": ip,
                        "port": item.get("port", ""),
                        "malware": item.get("malware", ""),
                        "status": item.get("status", ""),
                        "date_added": item.get("date_added", ""),
                    })
    except Exception as e:
        logger.warning(f"Failed to parse Feodo JSON: {e}")
    return results


def parse_openphish_txt(path: Path) -> List[Dict[str, Any]]:
    """
    Parse OpenPhish feed.txt (plain text, one URL per line).
    Vrací list dicts s url.
    """
    results = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                url = line.strip()
                if url and not url.startswith("#"):
                    results.append({
                        "url": url,
                        "source": "openphish",
                        "date_added": "",
                    })
    except Exception as e:
        logger.warning(f"Failed to parse OpenPhish feed: {e}")
    return results


def parse_nvd_recent(path: Path) -> List[Dict[str, Any]]:
    """
    Parse NVD 2.0 JSON.
    Vrací list dicts s CVE IDs.
    """
    results = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.loads(f.read())

        # NVD 2.0: {"vulnerabilities": [{cve: {id: ..., descriptions: [...]}}]}
        for vuln in data.get("vulnerabilities", []):
            cve = vuln.get("cve", {})
            cve_id = cve.get("id", "")
            if cve_id:
                descs = cve.get("descriptions", [])
                desc_en = next(
                    (d["value"] for d in descs if d.get("lang") == "en"),
                    "",
                )
                results.append({
                    "cve_id": cve_id,
                    "description": desc_en,
                    "published": cve.get("published", ""),
                    "last_modified": cve.get("lastModified", ""),
                })
    except Exception as e:
        logger.warning(f"Failed to parse NVD JSON: {e}")
    return results


# =============================================================================
# Sprint 8RA — Anonymous Bulk Feeds & NVD Historical
# =============================================================================

THREATFOX_BULK_URL = "https://threatfox.abuse.ch/export/json/recent/"
THREATFOX_MIRROR = "threatfox_recent.json"
MAX_THREATFOX_AGE_HOURS = 4.0

NVD_YEARS = [2022, 2023, 2024, 2025]
NVD_MODIFIED_URL = "https://nvd.nist.gov/feeds/json/cve/1.1/nvdcve-1.1-modified.json.gz"
NVD_YEAR_URL = "https://nvd.nist.gov/feeds/json/cve/1.1/nvdcve-1.1-{year}.json.gz"
SIZE_GATE_BYTES = 80 * 1024 * 1024  # 80MB gzip max per year
MAX_NVD_AGE_HOURS = 12.0


async def fetch_threatfox_recent(
    mirrors_dir: Path,
    session: aiohttp.ClientSession,
    max_age_hours: float = MAX_THREATFOX_AGE_HOURS,
) -> List[Dict[str, Any]]:
    """
    Sprint 8RA: ThreatFox anonymous bulk export — GET bez API klíče.

    Anonymní bulk URL: https://threatfox.abuse.ch/export/json/recent/
    Timeout: 30s. Rate limit: max 1 request per run.
    Fail-open: HTTP error → stale mirror zůstane, pipeline pokračuje.
    """
    out_path = mirrors_dir / THREATFOX_MIRROR
    now = time.time()

    # Stale check
    if out_path.exists():
        age_h = (now - out_path.stat().st_mtime) / 3600
        if age_h < max_age_hours:
            logger.debug(f"ThreatFox mirror fresh ({age_h:.1f}h old)")
            try:
                return await asyncio.to_thread(lambda: json.loads(out_path.read_text()))
            except Exception:
                pass

    # Download
    try:
        async with session.get(
            THREATFOX_BULK_URL,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status == 200:
                data = await resp.json(content_type=None)
                if isinstance(data, list):
                    await asyncio.to_thread(out_path.write_text, json.dumps(data))
                    logger.info(f"ThreatFox mirror updated: {len(data)} entries")
                    return data
                logger.warning(f"ThreatFox unexpected response type: {type(data)}")
            else:
                logger.warning(f"ThreatFox HTTP {resp.status} — using stale mirror")
    except Exception as e:
        logger.warning(f"ThreatFox fetch failed: {e} — using stale mirror")

    # Fallback to stale
    if out_path.exists():
        try:
            return await asyncio.to_thread(lambda: json.loads(out_path.read_text()))
        except Exception:
            pass
    return []


def parse_threatfox_recent(data: List[Dict[str, Any]]) -> List[str]:
    """
    Sprint 8RA: Extract IOC values from ThreatFox bulk data.

    Supported ioc_types: md5_hash, sha256_hash, domain, ip:port, url.
    Returns list of IOC value strings.
    """
    SUPPORTED_TYPES = {"md5_hash", "sha256_hash", "domain", "ip:port", "url"}
    results: List[str] = []
    for item in data:
        ioc_type = item.get("ioc_type", "")
        ioc = item.get("ioc", "")
        if ioc_type in SUPPORTED_TYPES and ioc:
            results.append(ioc)
    return results


async def fetch_nvd_historical(
    mirrors_dir: Path,
    session: aiohttp.ClientSession,
    years: List[int] = NVD_YEARS,
) -> Dict[str, int]:
    """
    Sprint 8RA: Stáhne per-year NVD feeds (2022–2025 gzip).

    Size gate: přeskočí rok pokud gzip > 80MB.
    Uloží do mirrors_dir / nvd_{year}.json.gz (compressed on disk).
    Returns {year: cve_count} for downloaded years, -1 for cached.
    """
    import gzip

    results: Dict[str, int] = {}
    for year in years:
        out_gz = mirrors_dir / f"nvd_{year}.json.gz"
        if out_gz.exists():
            logger.debug(f"NVD {year} mirror exists ({out_gz.stat().st_size/1e6:.1f}MB)")
            results[str(year)] = -1  # already cached
            continue

        url = NVD_YEAR_URL.format(year=year)
        try:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"NVD {year}: HTTP {resp.status}")
                    results[str(year)] = 0
                    continue

                content_length = int(resp.headers.get("content-length", 0))
                if content_length > SIZE_GATE_BYTES:
                    logger.warning(f"NVD {year}: {content_length/1e6:.0f}MB > 80MB gate, skip")
                    results[str(year)] = 0
                    continue

                data = await resp.read()
                if len(data) > SIZE_GATE_BYTES:
                    logger.warning(f"NVD {year}: downloaded {len(data)/1e6:.0f}MB > 80MB gate, skip")
                    results[str(year)] = 0
                    continue

                out_gz.write_bytes(data)

                # Parse CVE count from gzip
                try:
                    parsed = json.loads(gzip.decompress(data))
                    count = len(parsed.get("CVE_Items", []))
                    results[str(year)] = count
                    logger.info(f"NVD {year}: {count} CVEs ({len(data)/1e6:.1f}MB gzip)")
                except Exception as parse_err:
                    logger.warning(f"NVD {year} parse failed: {parse_err}")
                    results[str(year)] = 0

        except Exception as e:
            logger.warning(f"NVD {year} fetch failed: {e}")
            results[str(year)] = 0

    return results


async def refresh_if_stale(
    mirrors_dir: Path,
    session: aiohttp.ClientSession,
) -> None:
    """
    Sprint 8RA: Refresh stale mirrors v WARMUP fázi sprintu.

    ThreatFox: max 4h stale → fetch_threatfox_recent
    NVD modified: max 12h stale → nvdcve-1.1-modified.json.gz
    """
    now = time.time()

    # ThreatFox: max 4h
    await fetch_threatfox_recent(mirrors_dir, session, max_age_hours=4.0)

    # NVD modified feed: max 12h
    modified_gz = mirrors_dir / "nvd_modified.json.gz"
    if not modified_gz.exists() or (now - modified_gz.stat().st_mtime) / 3600 > MAX_NVD_AGE_HOURS:
        try:
            async with session.get(
                NVD_MODIFIED_URL,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                if resp.status == 200:
                    modified_gz.write_bytes(await resp.read())
                    logger.info("NVD modified feed updated")
                else:
                    logger.warning(f"NVD modified HTTP {resp.status}")
        except Exception as e:
            logger.warning(f"NVD modified refresh failed: {e}")


# =============================================================================
# MirrorParser registry
# =============================================================================

_MIRROR_PARSERS = {
    "parse_cisa_kev": parse_cisa_kev,
    "parse_urlhaus_csv": parse_urlhaus_csv,
    "parse_threatfox_json": parse_threatfox_json,
    "parse_feodo_json": parse_feodo_json,
    "parse_openphish_txt": parse_openphish_txt,
    "parse_nvd_recent": parse_nvd_recent,
}


# =============================================================================
# TIFeedAdapter
# =============================================================================


class TIFeedAdapter:
    """
    Unified TI feed adapter s local_mirror tier (priority=95).

    Mirrors-first princip:
    1. Zkus lokální mirror
    2. Pokud mirror miss, fall back na HTTP
    3. local_mirror tier má priority=95 (vyšší než structured_ti=90)
    """

    def __init__(self, mirrors_root: Path = MIRRORS_ROOT) -> None:
        self._mirror_mgr = MirrorManager(mirrors_root=mirrors_root)
        self._mirror_cache: Dict[str, List[Dict[str, Any]]] = {}
        self._cache_time: Dict[str, float] = {}
        self._cache_ttl = 300.0  # 5 minut in-memory cache

    def _is_cache_valid(self, source: str) -> bool:
        """Kontrola in-memory cache validity."""
        if source not in self._cache_time:
            return False
        return (time.monotonic() - self._cache_time[source]) < self._cache_ttl

    async def _load_mirror_data(self, name: str) -> List[Dict[str, Any]]:
        """Load data z lokálního mirror file."""
        if self._is_cache_valid(name) and name in self._mirror_cache:
            return self._mirror_cache[name]

        source = MIRROR_SOURCES.get(name)
        if source is None:
            return []

        parser_name = source.get("parser", "")
        parser_fn = _MIRROR_PARSERS.get(parser_name)
        if parser_fn is None:
            logger.warning(f"No parser for mirror {name}")
            return []

        path = self._mirror_mgr.get_mirror_path(name)
        if path is None:
            logger.info(f"Mirror {name} not available locally")
            return []

        data = parser_fn(path)

        self._mirror_cache[name] = data
        self._cache_time[name] = time.monotonic()

        return data

    async def get_iocs(
        self,
        indicator: str,
        ioc_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Získej IOC data pro daný indikátor.

        Vrací list findings s tier='local_mirror', priority=95.
        """
        findings = []
        indicator_lower = indicator.lower()

        # Pro CVE indikátory: zkus CISA KEV a NVD mirrors
        if indicator_lower.startswith("cve-"):
            cisa_data = await self._load_mirror_data("cisa_kev")
            for item in cisa_data:
                if item.get("cve_id", "").lower() == indicator_lower:
                    findings.append({
                        "type": "cisa_kev",
                        "indicator": indicator,
                        "data": item,
                        "tier": "local_mirror",
                        "priority": 95,
                        "source": "CISA KEV",
                    })

            nvd_data = await self._load_mirror_data("nvd_recent")
            for item in nvd_data:
                if item.get("cve_id", "").lower() == indicator_lower:
                    findings.append({
                        "type": "nvd",
                        "indicator": indicator,
                        "data": item,
                        "tier": "local_mirror",
                        "priority": 95,
                        "source": "NVD",
                    })

        # Pro URL indikátory: zkus URLhaus a OpenPhish
        if indicator_lower.startswith("http://") or indicator_lower.startswith("https://"):
            urlhaus_data = await self._load_mirror_data("urlhaus_recent")
            for item in urlhaus_data:
                if item.get("url", "").lower() == indicator_lower:
                    findings.append({
                        "type": "urlhaus",
                        "indicator": indicator,
                        "data": item,
                        "tier": "local_mirror",
                        "priority": 95,
                        "source": "URLhaus",
                    })

            openphish_data = await self._load_mirror_data("openphish_feed")
            for item in openphish_data:
                if item.get("url", "").lower() == indicator_lower:
                    findings.append({
                        "type": "openphish",
                        "indicator": indicator,
                        "data": item,
                        "tier": "local_mirror",
                        "priority": 95,
                        "source": "OpenPhish",
                    })

        # Pro IP indikátory: zkus Feodo
        ip_pattern = indicator_lower.split("/")[0]
        if self._looks_like_ip(ip_pattern):
            feodo_data = await self._load_mirror_data("feodo_ip")
            for item in feodo_data:
                if item.get("ip", "").lower() == ip_pattern:
                    findings.append({
                        "type": "feodo",
                        "indicator": indicator,
                        "data": item,
                        "tier": "local_mirror",
                        "priority": 95,
                        "source": "Feodo Tracker",
                    })

        # Pro malware IOC: zkus ThreatFox
        if ioc_type == "malware" or "malware" in indicator_lower:
            threatfox_data = await self._load_mirror_data("threatfox_ioc")
            for item in threatfox_data:
                if item.get("malware", "").lower() in indicator_lower:
                    findings.append({
                        "type": "threatfox",
                        "indicator": indicator,
                        "data": item,
                        "tier": "local_mirror",
                        "priority": 95,
                        "source": "ThreatFox",
                    })

        return findings

    def _looks_like_ip(self, text: str) -> bool:
        """Jednoduchá IP kontrola."""
        parts = text.split(".")
        if len(parts) != 4:
            return False
        try:
            return all(0 <= int(p) <= 255 for p in parts)
        except ValueError:
            return False

    async def init_mirrors(self) -> Dict[str, bool]:
        """Inicializuje všechny mirrors. Vrací success dict."""
        results = {}
        mirror_results = await self._mirror_mgr.init_mirrors()
        for name, path in mirror_results.items():
            results[name] = path is not None
        return results

    async def close(self) -> None:
        await self._mirror_mgr.close()
        self._mirror_cache.clear()
        self._cache_time.clear()
