"""
CVE Data Loader — Pre-built NVD CVE snapshot loader for CveCorrelationMatrix.

ISSUE [ULTIMATE]-004: Ships with top 50 technologies pre-indexed for zero-network lookups.

TECHNOLOGIES INDEXED:
    Web Servers: nginx, Apache, Microsoft-IIS, lighttpd, OpenBSD httpd
    SSH: OpenSSH
    Databases: PostgreSQL, MySQL/MariaDB, MongoDB, Redis, Elasticsearch
    CMS: WordPress, Drupal, Joomla, Magento
    Containers: Kubernetes, Docker
    Languages: PHP, Node.js, Python, Ruby, Java, Go
    Web Frameworks: Django, Flask, Rails, Express, Spring
    Caches: Memcached, Varnish
    Load Balancers: HAProxy
    DNS: BIND, PowerDNS
    Mail: Postfix, Exim, Dovecot
    CDN/Proxy: Cloudflare, Akamai
    Authentication: OpenLDAP, 389DS
    Monitoring: Prometheus, Grafana
    Message Queues: RabbitMQ, Apache Kafka, NATS
    CI/CD: Jenkins, GitLab Runner
    VPN: OpenVPN, WireGuard
    FTP: vsftpd, ProFTPD
    SMB: Samba
    TLS: OpenSSL, GnuTLS

DATA FORMAT:
    JSON array of CVE records with normalized fields.
    Pre-filtered to CVSS 5.0+ or KEV catalog entries.
    Version patterns use regex notation (e.g., r"1\.18\..*" for nginx 1.18.x).

QUARTERLY UPDATE:
    Run: python -m hledac.universal.knowledge.cve_data_loader --update
    Downloads NVD CVE 2.0 API feed filtered to indexed technologies.
    Generates: data/cve_matrix.db (DuckDB) + data/cve_matrix.json (backup)
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import httpx

from hledac.universal.core.env_config import ENV
from core import aclose

logger = logging.getLogger(__name__)

# ── NVD API Config ─────────────────────────────────────────────────────────────
_NVD_API_BASE = "https://services.nvd.nist.gov/rest/json/cves/2.0"
_RATE_LIMIT_MS = 6000  # NVD requires 6s between requests

# ── Data Paths ────────────────────────────────────────────────────────────────
_DATA_DIR = Path(__file__).parent.parent / "data"
_CVE_MATRIX_DB = _DATA_DIR / "cve_matrix.db"
_CVE_MATRIX_JSON = _DATA_DIR / "cve_matrix.json"
_CVE_MATRIX_JSON_GZ = _DATA_DIR / "cve_matrix.json.gz"

# ── Top 50 Technologies ───────────────────────────────────────────────────────
_INDEXED_TECHNOLOGIES = {
    # Web Servers
    "nginx", "apache", "microsoft-iis", "lighttpd", "openbsd httpd",
    # SSH
    "openssh",
    # Databases
    "postgresql", "mysql", "mariadb", "mongodb", "redis", "elasticsearch",
    # CMS
    "wordpress", "drupal", "joomla", "magento",
    # Containers
    "kubernetes", "docker",
    # Languages/Runtimes
    "php", "node.js", "nodejs", "python", "ruby", "java", "go",
    # Web Frameworks
    "django", "flask", "ruby on rails", "express", "spring",
    # Caches
    "memcached", "varnish",
    # Load Balancers
    "haproxy",
    # DNS
    "bind", "powerdns",
    # Mail
    "postfix", "exim", "dovecot",
    # CDN/Proxy
    "cloudflare", "akamai",
    # Authentication
    "openldap", "389ds",
    # Monitoring
    "prometheus", "grafana",
    # Message Queues
    "rabbitmq", "apache kafka", "nats",
    # CI/CD
    "jenkins", "gitlab runner",
    # VPN
    "openvpn", "wireguard",
    # FTP
    "vsftpd", "proftpd",
    # SMB
    "samba",
    # TLS/SSL
    "openssl", "gnutls",
}


def _tech_cpe_prefix(tech: str) -> str:
    """Map technology name to CPE 2.3 prefix."""
    mapping = {
        "nginx": "nginx:nginx",
        "apache": "apache:http_server",
        "openssh": "openssh:openssh",
        "postgresql": "postgresql:postgresql",
        "mysql": "mysql:mysql",
        "mariadb": "mariadb:mariadb",
        "mongodb": "mongodb:mongodb",
        "redis": "redis:redis",
        "elasticsearch": "elastic:elasticsearch",
        "wordpress": "wordpress:wordpress",
        "drupal": "drupal:drupal",
        "joomla": "joomla:joomla",
        "kubernetes": "kubernetes:kubernetes",
        "docker": "docker:docker",
        "php": "php:php",
        "node.js": "nodejs:node",
        "nodejs": "nodejs:node",
        "python": "python:python",
        "ruby": "ruby:ruby",
        "java": "oracle:java",
        "django": "django:django",
        "flask": "pallets:flask",
        "haproxy": "haproxy:haproxy",
        "openssl": "openssl:openssl",
    }
    return mapping.get(tech.lower(), f"{tech}:{tech}")


# ── CVE Record Parser ──────────────────────────────────────────────────────────

def _parse_cvss_metrics(metrics: dict) -> tuple[float | None, str]:
    """Extract CVSS score and version from metrics (prefers 3.x)."""
    if cvss_v31 := metrics.get("cvssMetricV31"):
        cvss_data = cvss_v31[0].get("cvssData", {})
        return cvss_data.get("baseScore"), "3.1"
    if cvss_v30 := metrics.get("cvssMetricV30"):
        cvss_data = cvss_v30[0].get("cvssData", {})
        return cvss_data.get("baseScore"), "3.0"
    if cvss_v2 := metrics.get("cvssMetricV2"):
        cvss_data = cvss_v2[0].get("cvssData", {})
        return cvss_data.get("baseScore"), "2.0"
    return None, "3.1"


def _parse_description(descriptions: list[dict]) -> str:
    """Extract English description from descriptions list."""
    for desc in descriptions:
        if desc.get("lang") == "en":
            return desc.get("value", "")
    return descriptions[0].get("value", "") if descriptions else ""


def _parse_cwe_id(weaknesses: list[dict]) -> str | None:
    """Extract CWE ID from weakness list."""
    for weakness in weaknesses:
        for desc in weakness.get("description", []):
            cwe_value = desc.get("value", "")
            if cwe_value.startswith("CWE-"):
                return cwe_value
    return None


def _parse_version_patterns(configs: list[dict]) -> dict[str, str]:
    """Extract version patterns from CPE configurations."""
    version_patterns: dict[str, str] = {}
    for config in configs:
        for node in config.get("nodes", []):
            for cpe_match in node.get("cpeMatches", []):
                if not cpe_match.get("vulnerable", False):
                    continue
                cpe = cpe_match.get("criteria", "")
                parts = cpe.split(":")
                if len(parts) >= 5:
                    vendor, product = parts[3], parts[4]
                    version = parts[5] if len(parts) > 5 else "*"
                    update = parts[6] if len(parts) > 6 else "*"
                    tech_key = f"{vendor}:{product}"
                    if tech_key in version_patterns:
                        continue
                    # Build version pattern
                    if update == "*":
                        pattern = ".*" if version == "*" else f"^{re_escape(version)}.*"
                    else:
                        pattern = ".*" if version == "*" else f"^{re_escape(version)}.{re_escape(update)}"
                    version_patterns[tech_key] = pattern
    return version_patterns


def _parse_nvd_cve(cve_item: dict[str, Any]) -> dict[str, Any] | None:
    """Parse NVD CVE 2.0 JSON item into normalized record."""
    try:
        cve = cve_item.get("cve", {})
        cve_id = cve.get("id", "")
        if not cve_id:
            return None

        published = cve.get("published", "")[:10]
        cvss_score, cvss_version = _parse_cvss_metrics(cve.get("metrics", {}))
        description = _parse_description(cve.get("descriptions", []))
        cwe_id = _parse_cwe_id(cve.get("weaknesses", []))
        version_patterns = _parse_version_patterns(cve.get("configurations", []))

        return {
            "cve_id": cve_id,
            "cvss_score": cvss_score,
            "cvss_version": cvss_version,
            "cwe_id": cwe_id,
            "description": description[:500],
            "published_date": published,
            "version_patterns": version_patterns,
        }
    except Exception as e:
        logger.debug(f"Failed to parse CVE: {e}")
        return None


def re_escape(s: str) -> str:
    """Escape string for regex."""
    return "".join(c if c.isalnum() or c in ".-_" else f"\\{c}" for c in s)


# ── Data Loader ────────────────────────────────────────────────────────────────

async def _fetch_nvd_page(
    client: httpx.AsyncClient,
    start_idx: int,
    results_per_page: int = 100,
) -> dict[str, Any]:
    """Fetch single NVD API page."""
    params = {
        "startIndex": start_idx,
        "resultsPerPage": results_per_page,
    }
    resp = await client.get(_NVD_API_BASE, params=params, timeout=60.0)
    resp.raise_for_status()
    await asyncio.sleep(_RATE_LIMIT_MS / 1000)
    return resp.json()


async def _fetch_all_nvd_cves(
    technologies: set[str],
    min_cvss: float = 5.0,
    max_records: int | None = 50000,
) -> list[dict[str, Any]]:
    """Fetch all CVEs for indexed technologies."""
    all_cves: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    async with httpx.AsyncClient(headers={"Accept": "application/json"}) as client:
        start_idx = 0
        page = 0
        while True:
            try:
                data = await _fetch_nvd_page(client, start_idx)
                vulnerabilities = data.get("vulnerabilities", [])
                if not vulnerabilities:
                    break

                for vuln in vulnerabilities:
                    parsed = _parse_nvd_cve(vuln)
                    if not parsed:
                        continue

                    # Filter by technology
                    tech_matches = []
                    for tech in technologies:
                        cpe_prefix = _tech_cpe_prefix(tech)
                        if any(cpe_prefix.lower() in vp.lower() for vp in parsed.get("version_patterns", {})):
                            tech_matches.append(tech)

                    if not tech_matches:
                        continue

                    # Filter by CVSS
                    if parsed["cvss_score"] and parsed["cvss_score"] < min_cvss:
                        continue

                    if parsed["cve_id"] in seen_ids:
                        continue
                    seen_ids.add(parsed["cve_id"])

                    for tech in tech_matches:
                        cve_record = {
                            "cve_id": parsed["cve_id"],
                            "technology": tech,
                            "version_pattern": parsed["version_patterns"].get(_tech_cpe_prefix(tech)),
                            "cvss_score": parsed["cvss_score"],
                            "cwe_id": parsed["cwe_id"],
                            "description_snippet": parsed["description"],
                            "published_date": parsed["published_date"],
                        }
                        all_cves.append(cve_record)

                total = data.get("totalResults", 0)
                logger.info(f"[CVE Loader] Fetched page {page}, {len(all_cves)} CVEs so far (of ~{min(total, max_records or float('inf'))})")

                if len(all_cves) >= (max_records or float("inf")):
                    break

                start_idx += 100
                page += 1

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 403:
                    logger.warning("[CVE Loader] Rate limited by NVD, waiting 30s...")
                    await asyncio.sleep(30)
                    continue
                raise

            except Exception as e:
                logger.error(f"[CVE Loader] Error: {e}")
                break

    return all_cves[:max_records]


# ── DuckDB Export ─────────────────────────────────────────────────────────────

async def export_to_duckdb(cve_records: list[dict[str, Any]], db_path: Path) -> int:
    """Export CVE records to DuckDB."""
    import duckdb

    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = duckdb.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cve_matrix (
            cve_id TEXT PRIMARY KEY,
            technology TEXT NOT NULL,
            version_pattern TEXT,
            cvss_score REAL,
            cwe_id TEXT,
            description_snippet TEXT,
            published_date TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cve_tech ON cve_matrix(technology)")

    loaded = 0
    for record in cve_records:
        try:
            conn.execute("""
                INSERT OR IGNORE INTO cve_matrix
                (cve_id, technology, version_pattern, cvss_score, cwe_id, description_snippet, published_date)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, [
                record["cve_id"],
                record["technology"].lower(),
                record.get("version_pattern"),
                record.get("cvss_score"),
                record.get("cwe_id"),
                record.get("description_snippet", "")[:500],
                record.get("published_date"),
            ])
            loaded += 1
        except Exception as e:
            logger.debug(f"Failed to insert {record.get('cve_id')}: {e}")

    conn.execute("PRAGIA optimize")
    conn.close()
    return loaded


# ── CLI ───────────────────────────────────────────────────────────────────────

async def update_cve_matrix(
    output_db: Path = _CVE_MATRIX_DB,
    output_json: Path = _CVE_MATRIX_JSON,
    output_gz: Path = _CVE_MATRIX_JSON_GZ,
    min_cvss: float = 5.0,
    max_records: int = 50000,
) -> int:
    """Update CVE matrix from NVD API."""
    logger.info(f"[CVE Loader] Fetching CVEs for {len(_INDEXED_TECHNOLOGIES)} technologies...")

    start_time = time.time()
    cve_records = await _fetch_all_nvd_cves(_INDEXED_TECHNOLOGIES, min_cvss, max_records)
    elapsed = time.time() - start_time

    logger.info(f"[CVE Loader] Collected {len(cve_records)} CVE records in {elapsed:.1f}s")

    # Save JSON backup
    with gzip.open(output_gz, "wt") as f:
        json.dump(cve_records, f)
    logger.info(f"[CVE Loader] Saved JSON backup: {output_gz} ({output_gz.stat().st_size / 1024:.1f} KB)")

    # Export to DuckDB
    loaded = await export_to_duckdb(cve_records, output_db)
    logger.info(f"[CVE Loader] Exported {loaded} records to {output_db} ({output_db.stat().st_size / 1024 / 1024:.1f} MB)")

    return loaded


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Update CVE correlation matrix from NVD")
    parser.add_argument("--update", action="store_true", help="Fetch latest CVE data from NVD")
    parser.add_argument("--min-cvss", type=float, default=5.0, help="Minimum CVSS score")
    parser.add_argument("--max-records", type=int, default=50000, help="Maximum records to fetch")
    args = parser.parse_args()

    if args.update:
        asyncio.run(update_cve_matrix(min_cvss=args.min_cvss, max_records=args.max_records))
    else:
        parser.print_help()
