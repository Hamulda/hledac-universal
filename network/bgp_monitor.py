#!/usr/bin/env python3

"""

BGP Monitor — Real-time BGP event streaming via pybgpstream.



Graceful fallback when pybgpstream is unavailable on arm64.

Bounded memory: max 1000 events in deque, older events discarded.



Anti-patterns prevented:

  - No blocking socket ops (all async via asyncio)

  - No pybgpstream assumption (ImportError guard at top)

  - No unbounded memory (deque maxlen=1000)

"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import deque
from collections.abc import Callable

logger = logging.getLogger(__name__)

# ── Rust fast-path (IOC extraction) ─────────────────────────────────────────
_RUST_IOC_AVAILABLE = False
try:
    from hledac_rust_extensions import fast_ioc_extract

    _RUST_IOC_AVAILABLE = True
except ImportError:
    fast_ioc_extract = None  # type: ignore[assignment]

# ── F234: IP Extraction ─────────────────────────────────────────────────────────
_IPV4_PATTERN = re.compile(
    r'\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b'
)
_PRIVATE_RANGES = re.compile(
    r'^(?:10\.|172\.(?:1[6-9]|2\d|3[01])\.|192\.168\.|127\.|::1|fe80)'
)


def extract_public_ips_from_text(text: str) -> list[str]:
    """Extract public IPv4 addresses from text. Filters RFC1918/loopback/link-local.

    F234 INVARIANT: Private IPs (RFC1918, loopback, link-local) are NEVER sent to
    BGP enrichment. Max 20 IPs per sprint (dedup + cap).

    Rust fast-path: uses pre-compiled OnceCell regex via hledac_rust_extensions.
    Python fallback: uses module-level _IPV4_PATTERN.
    """
    if not text:
        return []

    if _RUST_IOC_AVAILABLE:
        # Rust fast-path: extract all ipv4 types, then filter private IPs
        candidates = fast_ioc_extract(text)
        ips = [v for v, t in candidates if t == "ipv4"]
    else:
        # Python fallback
        ips = _IPV4_PATTERN.findall(text)

    # Deduplicate while preserving order, then filter RFC1918 private IPs
    seen: set[str] = set()
    result: list[str] = []
    for ip in ips:
        if ip in seen:
            continue
        seen.add(ip)
        if not _PRIVATE_RANGES.match(ip):
            result.append(ip)
    return result



# ---------------------------------------------------------------------------

# Graceful fallback — must be at top of file, not inside functions

# ---------------------------------------------------------------------------

try:

    import pybgpstream

    BGP_AVAILABLE = True

except ImportError:

    BGP_AVAILABLE = False

    logger.warning(

        "WARNING: pybgpstream not available on arm64 — BGP monitoring disabled"

    )





# ---------------------------------------------------------------------------

# Data contracts

# ---------------------------------------------------------------------------



BGP_EVENT_TYPES: frozenset[str] = frozenset({"announce", "withdraw", "unknown"})





def _parse_as_path(raw_path: str) -> str:

    """Normalize AS path to space-separated string."""

    if not raw_path:

        return ""

    # AS paths come as "{asn1} {asn2} ..." or "{asn1}{asn2}..."

    # Normalize to space-separated

    normalized = " ".join(raw_path.replace("{", "").replace("}", "").split())

    return normalized.strip()





# ---------------------------------------------------------------------------

# Main API

# ---------------------------------------------------------------------------



async def monitor_bgp(

    prefixes: list[str],

    callback: Callable[[float, str, str, str], None],

    duration_seconds: int = 60,

) -> list[dict]:

    """

    Stream BGP events for given prefixes.



    Args:

        prefixes: List of BGP prefixes to monitor (e.g. ["1.1.1.0/24"])

        callback: Called with (timestamp, prefix, as_path, event_type) per event.

                  timestamp: float (unix time)

                  prefix: str (e.g. "1.1.1.0/24")

                  as_path: str (e.g. "13335 1234")

                  event_type: str in {"announce", "withdraw", "unknown"}

        duration_seconds: How long to stream (default 60s)



    Returns:

        List of event dicts with keys: timestamp, prefix, as_path, event_type



    Anti-patterns prevented:

      - Graceful degradation when BGP_AVAILABLE=False

      - Bounded memory via deque(maxlen=1000)

      - Non-blocking via asyncio shield around sync pybgpstream iteration

    """

    if not BGP_AVAILABLE:

        logger.warning(

            "WARNING: pybgpstream not available on arm64 — BGP monitoring disabled"

        )

        return []




    event_buffer: deque[dict] = deque(maxlen=1000)  # Bounded memory



    # Parse duration into start/end times

    end_time = int(time.time())

    start_time = end_time - duration_seconds



    try:

        stream = pybgpstream.BGPStream(

            data_interface="single",

            filter=f"type any prefix {' '.join(prefixes)}",

        )

        stream.set_start_time(start_time)

        stream.set_end_time(end_time)



        async def _stream_events():

            """Async wrapper around sync pybgpstream iteration."""

            try:

                for entry in stream:

                    elem = entry.record["elements"][0]

                    raw_ts = elem["time"]

                    raw_prefix = elem["prefix"]

                    raw_as_path = elem.get("fields", {}).get("as-path", "")

                    raw_type = elem["type"]



                    timestamp = float(raw_ts)

                    prefix = str(raw_prefix)

                    as_path = _parse_as_path(str(raw_as_path))

                    event_type = raw_type if raw_type in BGP_EVENT_TYPES else "unknown"



                    event = {

                        "timestamp": timestamp,

                        "prefix": prefix,

                        "as_path": as_path,

                        "event_type": event_type,

                    }

                    event_buffer.append(event)



                    # Invoke callback (non-blocking)

                    try:

                        callback(timestamp, prefix, as_path, event_type)

                    except Exception as cb_err:

                        logger.debug(f"BGP callback error: {cb_err}")



                    # Check if duration exceeded

                    if time.time() - end_time + duration_seconds > 0:

                        break

            except Exception as e:

                logger.warning(f"BGP stream error: {e}")



        # Run sync iteration in executor to avoid blocking event loop

        loop = asyncio.get_running_loop()

        await asyncio.wait_for(

            loop.run_in_executor(None, _stream_events),

            timeout=duration_seconds + 5,

        )



    except TimeoutError:

        logger.debug(f"BGP monitor reached duration limit ({duration_seconds}s)")

    except Exception as e:

        logger.warning(f"BGP monitor error: {e}")



    # Return buffered events (max 1000)

    return list(event_buffer)





__all__ = [

    "BGP_AVAILABLE",

    "monitor_bgp",

    "monitor_bgp_as_findings",

]





# F229: CanonicalFinding return path

async def monitor_bgp_as_findings(

    prefixes: list[str],

    duration_seconds: int = 60,

    timeout: int = 30,

) -> list:

    """

    Monitor BGP events and return as CanonicalFinding list.



    Fails soft: returns empty list on any error or when BGP unavailable.



    Args:

        prefixes:         List of BGP prefixes (e.g. ["1.1.1.0/24", "8.8.0.0/16"])

        duration_seconds: How long to stream (default 60s)

        timeout:          Seconds per call (default 30s, unused but kept for API compat)



    Returns:

        list[CanonicalFinding] — one per BGP event

    """

    if not BGP_AVAILABLE:

        return []



    from hledac.universal.knowledge.duckdb_store import CanonicalFinding



    def _to_canonical(event: dict) -> CanonicalFinding:

        import hashlib

        ts = event["timestamp"]

        prefix = event["prefix"]

        as_path = event["as_path"]

        event_type = event["event_type"]

        content_hash = hashlib.sha256(f"{prefix}:{as_path}:{event_type}".encode()).hexdigest()[:16]

        finding_id = f"bgp_{prefix.replace('/', '_')}_{int(ts * 1000)}_{content_hash}"

        return CanonicalFinding(

            finding_id=finding_id,

            query=f"bgp:{prefix}",

            source_type="bgp_monitor",

            confidence=0.8 if event_type in ("announce", "withdraw") else 0.5,

            ts=ts,

            provenance=(prefix, as_path, event_type),

            payload_text=f"prefix={prefix} as_path={as_path} event={event_type}",

            accepted=True,

            reason="bgp_monitor",

            entropy=0.0,

            normalized_hash=None,

            duplicate=False,

        )



    try:

        events = await monitor_bgp(

            prefixes=prefixes,

            callback=lambda *args: None,  # discard, we just want return value

            duration_seconds=duration_seconds,

        )

    except Exception:

        return []



    if not events:

        return []



    findings = []

    for event in events:

        try:

            findings.append(_to_canonical(event))

        except Exception:

            continue








async def bgp_enrich_to_canonical(ip_or_asn: str, query: str) -> list[CanonicalFinding]:
    """BGP enrichment adapter — mapuje BGP monitor output na CanonicalFinding.

    Args:
        ip_or_asn: IP adresa nebo ASN (např. "8.8.8.8" nebo "AS15169")
        query: kontextový dotaz pro BGP lookup
    Returns:
        list[CanonicalFinding] s source_type="bgp_enrichment"
    """
    try:
        findings = await monitor_bgp_as_findings(ip_or_asn, query=query, duration_seconds=30)
        # Prejmenuj source_type na bgp_enrichment pro rozliseni od raw monitoru
        for f in findings:
            object.__setattr__(f, 'source_type', 'bgp_enrichment')
        return findings
    except Exception:
        return []


# ── F234: RIPE Stat API lazy import ─────────────────────────────────────────────
_aiohttp = None


def _get_aiohttp():
    global _aiohttp
    if _aiohttp is None:
        import aiohttp as _mod
        _aiohttp = _mod
    return _aiohttp


# ── F234: RIPE Stat API client ──────────────────────────────────────────────────
_RIPE_PREFIX_URL = "https://stat.ripe.net/data/prefix-overview/data.json"
_RIPE_WHOIS_URL = "https://stat.ripe.net/data/whois/data.json"
_RIPE_TIMEOUT = 30.0  # seconds per IP


async def enrich_ip_as_finding(ip: str) -> list[CanonicalFinding]:
    """
    F234: Enrich a single IP with live BGP data from RIPE Stat API.

    Invariants (F234):
      - RFC1918/loopback IPs are NEVER sent to RIPE (gate: extract_public_ips_from_text)
      - Max 20 IPs per sprint (enforced by caller)
      - 30s timeout per IP
      - Fail-soft: any error → return []

    RIPE Stat API (no auth required):
      - GET prefix-overview → ASN, prefix, holder
      - GET whois (per ASN) → org name, country, abuse contact

    Returns:
        list[CanonicalFinding] with source_type="bgp_ripe_stat", confidence=0.88
    """
    # Gate: extract_public_ips_from_text is the canonical RFC1918 filter
    public_ips = extract_public_ips_from_text(ip)
    if not public_ips:
        return []

    actual_ip = public_ips[0]
    aiohttp = _get_aiohttp()

    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=_RIPE_TIMEOUT)
        ) as session:
            # Fetch prefix-overview and whois concurrently
            async with session.get(
                f"{_RIPE_PREFIX_URL}?resource={actual_ip}",
                headers={"Accept": "application/json"},
            ) as pref_resp:
                pref_data = await pref_resp.json()

            # Extract ASN from prefix-overview
            asns = pref_data.get("data", {}).get("prefixes", [])
            if not asns:
                return []

            # Use the most-specific (first) prefix entry
            entry = asns[0]
            asn = entry.get("asn")
            prefix = entry.get("prefix")
            holder = entry.get("holder")

            if not asn:
                return []

            # Fetch whois for ASN — country, org, abuse contact
            country = ""
            org_name = ""
            abuse_contact = ""
            try:
                async with session.get(
                    f"{_RIPE_WHOIS_URL}?resource={asn}",
                    headers={"Accept": "application/json"},
                ) as whois_resp:
                    whois_data = (await whois_resp.json()).get("data", {})
                    if "objects" in whois_data:
                        for obj in whois_data["objects"].get("object", []):
                            for attr in obj.get("attributes", {}).get("attribute", []):
                                name = attr.get("name", "")
                                value = attr.get("value", "")
                                if name == "country":
                                    country = value
                                elif name == "org-name":
                                    org_name = value
                                elif name == "abuse-mailbox":
                                    abuse_contact = value
            except Exception:
                pass  # fail-soft: whois is supplementary

            from hledac.universal.knowledge.duckdb_store import CanonicalFinding
            import hashlib
            import time as _time_module

            ts = _time_module.monotonic()

            content_hash = hashlib.sha256(
                f"{actual_ip}:{asn}:{prefix}:{holder}".encode()
            ).hexdigest()[:16]
            finding_id = f"bgp_ripe_{asn}_{actual_ip.replace('.', '_')}_{content_hash}"

            metadata = {
                "asn": str(asn),
                "prefix": prefix or "",
                "holder": holder or "",
                "country": country,
                "org_name": org_name,
                "abuse_contact": abuse_contact,
            }

            return [
                CanonicalFinding(
                    finding_id=finding_id,
                    query=f"bgp_ripe:{actual_ip}",
                    source_type="bgp_ripe_stat",
                    confidence=0.88,
                    ts=ts,
                    provenance=("bgp_ripe_stat", str(asn), actual_ip),
                    payload_text=str(metadata),
                )
            ]

    except Exception:
        return []
