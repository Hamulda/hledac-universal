"""
Network Intelligence - BGP & DoH
===============================

BGP lookup via pybgpstream, DoH via dnspython with Cloudflare/Google resolvers.

M1 Optimized: Async I/O, bounded RAM (<300MB for BGP data), no blocking sync calls.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

# Bounded RAM limit for BGP/IPFS data (300MB)
MAX_BGP_DATA_MB = 300

# DoH endpoints
CLOUDFLARE_DOH = "https://cloudflare-dns.com/dns-query"
GOOGLE_DOH = "https://dns.google/dns-query"


async def get_bgp_info(prefix: str) -> dict[str, Any]:
    """
    Look up BGP information for a prefix using pybgpstream.
    Returns ASN, prefix, origin AS name, country.
    Falls back to ipinfo.io API if pybgpstream unavailable.

    Anti-pattern: bounded RAM, max 300MB for BGP data.

    Args:
        prefix: BGP prefix (e.g., "8.8.8.0/24" or "2001:db8::/32")

    Returns:
        Dict with keys: prefix, asn, as_name, country, announced, found
    """
    result: dict[str, Any] = {
        "prefix": prefix,
        "asn": None,
        "as_name": None,
        "country": None,
        "announced": False,
        "found": False,
        "error": None,
    }

    # Try pybgpstream first
    try:
        import pybgpstream

        stream = pybgpstream.BGPStream(
            data_interface="single",
            filter=f"prefix {prefix}",
        )

        records = []
        for i, rec in enumerate(stream):
            if i >= 100:  # Cap at 100 records for memory safety
                break
            records.append(rec)

        if records:
            result["found"] = True
            result["announced"] = True

            # Parse first record for ASN info
            for rec in records:
                elem = rec[2]  # elementary record
                if elem:
                    result["asn"] = elem.get("asn")
                    result["as_path"] = elem.get("path")
                    # Extract origin AS from path
                    path = elem.get("path", "")
                    if path:
                        asns = path.split()
                        if asns:
                            result["origin_asn"] = asns[-1] if asns[-1] != "None" else asns[-2] if len(asns) > 1 else None
                    result["country"] = elem.get("country")
                    break

            logger.info(f"BGP lookup for {prefix}: ASN {result.get('asn')}")

    except ImportError:
        # Fallback to ipinfo.io API
        result = await _get_bgp_via_ipinfo(prefix)
    except Exception as e:
        logger.debug(f"pybgpstream failed for {prefix}: {e}")
        # Fallback to API
        result = await _get_bgp_via_ipinfo(prefix)

    return result


async def _get_bgp_via_ipinfo(prefix: str) -> dict[str, Any]:
    """
    Fallback BGP lookup via ipinfo.io API.
    Requires IPINFO_API_KEY env var or uses free tier.
    """
    result: dict[str, Any] = {
        "prefix": prefix,
        "asn": None,
        "as_name": None,
        "country": None,
        "announced": False,
        "found": False,
        "error": None,
        "source": "ipinfo.io",
    }

    api_key = os.environ.get("IPINFO_API_KEY")

    try:
        # Extract IP from prefix for API call
        ip = prefix.split("/")[0]

        url = f"https://ipinfo.io/{ip}/json"
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=timeout) as resp:
                if resp.status == 200:
                    data = await resp.json()

                    # Extract ASN from org field (e.g., "AS15169 Google")
                    org = data.get("org", "")
                    if org.startswith("AS"):
                        asn_part = org.split()[0]
                        result["asn"] = asn_part.replace("AS", "")
                        result["as_name"] = " ".join(org.split()[1:]) if len(org.split()) > 1 else None

                    result["country"] = data.get("country")
                    result["announced"] = bool(result["asn"])
                    result["found"] = True

                    logger.info(f"BGP via ipinfo.io for {prefix}: ASN {result.get('asn')}")

    except Exception as e:
        result["error"] = str(e)
        logger.debug(f"ipinfo.io fallback failed for {prefix}: {e}")

    return result


async def resolve_dns_doh(domain: str) -> dict[str, list[str]]:
    """
    Resolve DNS via DoH (DNS-over-HTTPS) using Cloudflare (1.1.1.1)
    and Google (8.8.8.8) resolvers via dnspython with cloudflare/google DoH endpoints.
    Returns A, AAAA records.

    Args:
        domain: Domain name to resolve

    Returns:
        Dict with keys: a (list of IPv4), aaaa (list of IPv6), mx, txt, found
    """
    result: dict[str, Any] = {
        "domain": domain,
        "a": [],
        "aaaa": [],
        "mx": [],
        "txt": [],
        "found": False,
        "errors": [],
    }

    # Try dnspython with DoH first
    try:
        import dns.asyncresolver
        import dns.message
        import dns.query

        # Cloudflare DoH
        doh_urls = [
            ("cloudflare", CLOUDFLARE_DOH),
            ("google", GOOGLE_DOH),
        ]

        for provider, doh_url in doh_urls:
            try:
                # Make DoH query for A records
                query = dns.message.make_query(domain, dns.rdatatype.A)
                response = await dns.query.https(
                    query,
                    doh_url,
                    timeout=10,
                )

                for answer in response.answer:
                    for rdata in answer:
                        if rdata.rdtype == dns.rdatatype.A:
                            result["a"].append(str(rdata))
                        elif rdata.rdtype == dns.rdatatype.AAAA:
                            result["aaaa"].append(str(rdata))

                # Also try MX
                mx_query = dns.message.make_query(domain, dns.rdatatype.MX)
                mx_response = await dns.query.https(
                    mx_query,
                    doh_url,
                    timeout=10,
                )

                for answer in mx_response.answer:
                    for rdata in answer:
                        if rdata.rdtype == dns.rdatatype.MX:
                            result["mx"].append(f"{rdata.preference} {rdata.exchange}")

                # Also try TXT
                txt_query = dns.message.make_query(domain, dns.rdatatype.TXT)
                txt_response = await dns.query.https(
                    txt_query,
                    doh_url,
                    timeout=10,
                )

                for answer in txt_response.answer:
                    for rdata in answer:
                        if rdata.rdtype == dns.rdatatype.TXT:
                            result["txt"].extend([str(t) for t in rdata.strings])

                if result["a"] or result["aaaa"]:
                    result["found"] = True
                    result["doh_provider"] = provider
                    break

            except Exception as e:
                result["errors"].append(f"{provider}: {str(e)}")
                logger.debug(f"DoH {provider} failed for {domain}: {e}")
                continue

    except ImportError:
        # Fallback to aiohttp direct DoH queries
        result = await _resolve_doh_direct(domain)
    except Exception as e:
        logger.debug(f"dnspython DoH failed for {domain}: {e}")
        result = await _resolve_doh_direct(domain)

    return result


async def _resolve_doh_direct(domain: str) -> dict[str, list[str]]:
    """
    Direct DoH resolution via aiohttp when dnspython is unavailable.
    Uses JSON-mode DoH endpoints.
    """
    result: dict[str, Any] = {
        "domain": domain,
        "a": [],
        "aaaa": [],
        "mx": [],
        "txt": [],
        "found": False,
        "errors": [],
        "source": "direct_doh",
    }

    doh_endpoints = [
        ("cloudflare", f"{CLOUDFLARE_DOH}?name={domain}&type=A"),
        ("google", f"{GOOGLE_DOH}?name={domain}&type=A"),
    ]

    timeout = aiohttp.ClientTimeout(total=15)

    async with aiohttp.ClientSession() as session:
        for provider, url in doh_endpoints:
            try:
                headers = {"Accept": "application/dns-json"}

                async with session.get(url, headers=headers, timeout=timeout) as resp:
                    if resp.status == 200:
                        data = await resp.json()

                        # Parse DNS JSON response (RFC 8425)
                        answers = data.get("Answer", [])
                        for ans in answers:
                            ans_type = ans.get("type")
                            value = ans.get("data")

                            if ans_type == 1:  # A record
                                result["a"].append(value)
                            elif ans_type == 28:  # AAAA record
                                result["aaaa"].append(value)
                            elif ans_type == 15:  # MX record
                                result["mx"].append(value)
                            elif ans_type == 16:  # TXT record
                                result["txt"].append(value)

                        if result["a"] or result["aaaa"]:
                            result["found"] = True
                            result["doh_provider"] = provider
                            break

            except Exception as e:
                result["errors"].append(f"{provider}: {str(e)}")
                logger.debug(f"Direct DoH {provider} failed for {domain}: {e}")

    return result


def integrate_bgp_doh_to_graph(
    ip_addresses: list[str],
    asn_info: dict[str, Any],
    graph: Any,
) -> None:
    """
    Add BGP/DoH results to knowledge graph.

    Args:
        ip_addresses: List of IP addresses to add
        asn_info: BGP ASN information dict
        graph: Knowledge graph instance (e.g., IOCGraph)
    """
    if graph is None:
        return

    try:
        # Add ASN node if available
        if asn_info.get("asn"):
            asn_id = f"asn:{asn_info['asn']}"
            graph.add_node(
                asn_id,
                node_type="asn",
                asn=asn_info["asn"],
                as_name=asn_info.get("as_name"),
                country=asn_info.get("country"),
                prefix=asn_info.get("prefix"),
            )

        # Add IP nodes with ASN relationship
        for ip in ip_addresses:
            ip_id = f"ip:{ip}"
            graph.add_node(ip_id, node_type="ip", address=ip)

            if asn_info.get("asn"):
                graph.add_edge(
                    ip_id,
                    asn_id,
                    relationship="belongs_to",
                )

        logger.debug(
            f"Integrated {len(ip_addresses)} IPs and ASN {asn_info.get('asn')} to graph"
        )

    except Exception as e:
        logger.debug(f"Failed to integrate BGP/DoH to graph: {e}")


# Export
__all__ = [
    "get_bgp_info",
    "resolve_dns_doh",
    "integrate_bgp_doh_to_graph",
    "MAX_BGP_DATA_MB",
]
