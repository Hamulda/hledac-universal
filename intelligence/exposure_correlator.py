"""
AssetExposureCorrelator — F202C: Correlates asset exposure signals into explainable findings.

Signal sources consumed:
  - ct_log findings: cert→SAN mappings, issuers, timestamps
  - open_storage findings: exposed S3/Firebase/Elasticsearch/MongoDB buckets
  - jarm fingerprints: TLS fingerprint hashes (infrastructure clustering)
  - passive_dns findings: domain→IP mappings

Correlation types produced:
  - exposed_host: host with open bucket + cert-domain relation
  - cert_domain_relation: CT cert SAN matches query domain
  - open_bucket: confirmed exposed cloud storage bucket
  - suspicious_service_fingerprint: JARM fingerprint matching known-suspicious pattern
  - infra_cluster: multiple hosts sharing same JARM hash (co-located infra)

Bounds:
  - MAX_ASSETS = 1000          max unique assets per sprint
  - MAX_SIGNALS_PER_ASSET = 3  max signals correlated per asset
  - MAX_FINDINGS = 500         max exposure findings produced

All methods fail-soft: sprint continues on any error.
Findings persist via async_ingest_findings_batch (canonical write path).
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding

logger = logging.getLogger(__name__)

# ── Bounds ────────────────────────────────────────────────────────────────────

MAX_ASSETS: int = 1000
MAX_SIGNALS_PER_ASSET: int = 3
MAX_FINDINGS: int = 500

# ── Signal Types ───────────────────────────────────────────────────────────────

SIGNAL_TYPE_CT_CERT = "ct_cert"
SIGNAL_TYPE_OPEN_BUCKET = "open_bucket"
SIGNAL_TYPE_JARM = "jarm_fp"
SIGNAL_TYPE_PASSIVE_DNS = "passive_dns"
SIGNAL_TYPE_PASSIVE_FINGERPRINT = "passive_fingerprint"

# ── Correlation Types ──────────────────────────────────────────────────────────

CORR_EXPOSED_HOST = "exposed_host"
CORR_CERT_DOMAIN = "cert_domain_relation"
CORR_OPEN_BUCKET = "open_bucket"
CORR_SUSPICIOUS_FP = "suspicious_service_fingerprint"
CORR_INFRA_CLUSTER = "infra_cluster"

# ── JARM Known-Suspicious Prefixes ───────────────────────────────────────────

# Servers that are rarely used for legitimate infrastructure
_SUSPICIOUS_JARM_PREFIXES: tuple[str, ...] = (
    "2a2a2a2a2a2a",  # GREASE placeholder
    "000000000000",  # No cipher accepted
)

# ── Stats ─────────────────────────────────────────────────────────────────────

_stats: dict[str, int] = {
    "assets_registered": 0,
    "signals_extracted": 0,
    "correlations_run": 0,
    "findings_produced": 0,
    "exposed_hosts_found": 0,
    "open_buckets_found": 0,
    "infra_clusters_found": 0,
}


def get_correlator_stats() -> dict[str, int]:
    """Return copy of correlator stats (for probe verification)."""
    return dict(_stats)


def reset_correlator_stats() -> None:
    """Reset all stats to zero (for probe test isolation)."""
    _stats.clear()
    _stats.update({
        "assets_registered": 0,
        "signals_extracted": 0,
        "correlations_run": 0,
        "findings_produced": 0,
        "exposed_hosts_found": 0,
        "open_buckets_found": 0,
        "infra_clusters_found": 0,
    })


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class AssetSignal:
    """A single signal associated with an asset."""
    signal_type: str           # SIGNAL_TYPE_*
    asset_key: str             # normalized asset identifier
    confidence: float
    metadata: dict             # signal-specific payload
    finding_id: str            # source finding that produced this signal


@dataclass
class Asset:
    """An asset (host, domain, IP) with collected signals."""
    key: str
    signals: list[AssetSignal] = field(default_factory=list)

    @property
    def has_bucket(self) -> bool:
        return any(s.signal_type == SIGNAL_TYPE_OPEN_BUCKET for s in self.signals)

    @property
    def has_cert(self) -> bool:
        return any(s.signal_type == SIGNAL_TYPE_CT_CERT for s in self.signals)

    @property
    def has_jarm(self) -> bool:
        return any(s.signal_type == SIGNAL_TYPE_JARM for s in self.signals)

    @property
    def has_dns(self) -> bool:
        return any(s.signal_type == SIGNAL_TYPE_PASSIVE_DNS for s in self.signals)


@dataclass
class ExposureFinding:
    """A correlated exposure finding with evidence."""
    corr_type: str             # CORR_* constant
    asset_key: str
    confidence: float
    summary: str               # human-readable one-line summary
    evidence_pointers: list[str]  # list of source finding_ids
    signal_facets: dict[str, float]  # per-signal-type confidence contribution
    suggested_pivots: list[dict]  # recommended follow-up queries
    payload: dict              # full correlation data


# ── Normalization Helpers ──────────────────────────────────────────────────────

def _normalize_host(asset_key: str) -> str:
    """Strip port, scheme, and normalize to lowercase."""
    key = asset_key.lower().strip()
    for prefix in ("https://", "http://"):
        if key.startswith(prefix):
            key = key[len(prefix):]
    if ":" in key:
        key = key.rsplit(":", 1)[0]
    return key


def _normalize_url(asset_key: str) -> str:
    """Normalize bucket URL to base key."""
    key = asset_key.lower().strip()
    for prefix in ("https://", "http://"):
        if key.startswith(prefix):
            key = key[len(prefix):]
    return key.rstrip("/")


def _extract_jarm_from_payload(payload_text: str | None) -> str | None:
    """Extract JARM hash from payload_text."""
    if not payload_text:
        return None
    try:
        import json
        data = json.loads(payload_text) if isinstance(payload_text, str) else payload_text
        h = data.get("jarm_hash") or data.get("jarm") or data.get("hash")
        if h and len(h) == 62:
            return h
    except Exception:
        pass
    return None


# ── Open Storage Scanner DTO ──────────────────────────────────────────────────

@dataclass
class OpenStorageResult:
    """Normalized DTO for open storage scan results."""
    url: str
    status: int
    bucket_type: str   # 's3' | 'firebase' | 'elasticsearch' | 'mongodb'
    headers: dict


def scan_open_storage(domains: list[str]) -> list[OpenStorageResult]:
    """
    Scan domains for open storage buckets.

    Returns list of OpenStorageResult for buckets returning HTTP 200.
    Fail-soft: returns [] on any error.
    """
    try:
        from hledac.universal.network.open_storage_scanner import _OpenStorageScanner
    except Exception:
        return []

    results: list[OpenStorageResult] = []
    scanner = _OpenStorageScanner()

    async def _scan_all():
        import asyncio
        tasks = [scanner.scan_domain(d) for d in domains]
        return await asyncio.gather(*tasks, return_exceptions=True)

    try:
        loop = asyncio.new_event_loop()
        try:
            scan_results = loop.run_until_complete(_scan_all())
        finally:
            loop.close()
    except Exception:
        return []

    for scan_result in scan_results:
        if isinstance(scan_result, Exception):
            continue
        for item in scan_result:
            results.append(OpenStorageResult(
                url=item.get("url", ""),
                status=item.get("status", 0),
                bucket_type=item.get("type", "unknown"),
                headers=item.get("headers", {}),
            ))

    return results


# ── Signal Extraction ─────────────────────────────────────────────────────────

def extract_signals(findings: list["CanonicalFinding"]) -> list[AssetSignal]:
    """
    Extract asset signals from a list of CanonicalFindings.

    Signal types extracted:
      - ct_cert: from ct_log findings (san = finding_id)
      - open_bucket: from open_storage findings
      - jarm_fp: from jarm fingerprint findings
      - passive_dns: from passive_dns findings

    Returns:
        List of AssetSignal objects (unbounded within a sprint, but bounded
        per-call via MAX_SIGNALS_PER_ASSET during correlation).
    """
    signals: list[AssetSignal] = []

    for finding in findings:
        src = getattr(finding, "source_type", "") or ""
        fid = getattr(finding, "finding_id", "")
        confidence = getattr(finding, "confidence", 0.5) or 0.5
        payload = getattr(finding, "payload_text", None) or "{}"

        try:
            import json
            data = json.loads(payload) if isinstance(payload, str) else payload
        except Exception:
            data = {}

        if src == "ct_log":
            # finding_id is ct_{sha1(san)[:16]}, asset key is the SAN
            san = fid.replace("ct_", "") if fid.startswith("ct_") else fid
            asset_key = _normalize_host(san)
            issuer = data.get("issuer", "")
            cert_count = data.get("cert_count", 0)
            domain = data.get("domain", "")
            signals.append(AssetSignal(
                signal_type=SIGNAL_TYPE_CT_CERT,
                asset_key=asset_key,
                confidence=confidence,
                metadata={"issuer": issuer, "cert_count": cert_count, "domain": domain, "san": san},
                finding_id=fid,
            ))

        elif src == "open_storage":
            url = data.get("url", "")
            bucket_type = data.get("type", "unknown")
            status = data.get("status", 0)
            if url:
                signals.append(AssetSignal(
                    signal_type=SIGNAL_TYPE_OPEN_BUCKET,
                    asset_key=_normalize_url(url),
                    confidence=confidence,
                    metadata={"url": url, "bucket_type": bucket_type, "status": status},
                    finding_id=fid,
                ))

        elif src == "jarm":
            jarm_hash = _extract_jarm_from_payload(payload)
            if jarm_hash:
                # asset_key is the domain/IP from finding_id
                asset_key = _normalize_host(fid.replace("jarm_", "")) if fid.startswith("jarm_") else _normalize_host(fid)
                signals.append(AssetSignal(
                    signal_type=SIGNAL_TYPE_JARM,
                    asset_key=asset_key,
                    confidence=confidence,
                    metadata={"jarm_hash": jarm_hash},
                    finding_id=fid,
                ))

        elif src == "passive_dns":
            # passive_dns findings have domain and ip in payload
            domain = data.get("domain", "")
            ip = data.get("ip", "") or data.get("ip_address", "")
            if domain:
                asset_key = _normalize_host(domain)
                signals.append(AssetSignal(
                    signal_type=SIGNAL_TYPE_PASSIVE_DNS,
                    asset_key=asset_key,
                    confidence=confidence,
                    metadata={"domain": domain, "ip": ip, "record_type": data.get("record_type", "A")},
                    finding_id=fid,
                ))

        elif src == "passive_fingerprint":
            # passive_fingerprint findings have service_name and product in payload
            service_name = data.get("service_name", "")
            product = data.get("product", "")
            version = data.get("version", "")
            facets = data.get("facets", {})
            if service_name:
                # Use the finding's ioc_value as asset_key if available, else use service_name
                asset_key = getattr(finding, "ioc_value", "") or service_name
                signals.append(AssetSignal(
                    signal_type=SIGNAL_TYPE_PASSIVE_FINGERPRINT,
                    asset_key=asset_key,
                    confidence=confidence,
                    metadata={
                        "service_name": service_name,
                        "product": product,
                        "version": version,
                        "facets": facets,
                    },
                    finding_id=fid,
                ))

    _stats["signals_extracted"] = len(signals)
    return signals


# ── Correlation Engine ────────────────────────────────────────────────────────

def _correlate_signals(signals: list[AssetSignal]) -> list[ExposureFinding]:
    """
    Correlate signals into exposure findings.

    Algorithm:
      1. Group signals by asset_key (bounded to MAX_ASSETS)
      2. For each asset with multiple signal types, attempt correlation
      3. For JARM fingerprints, cluster assets by hash (infra_cluster)
      4. For each successful correlation, produce an ExposureFinding

    Bounded:
      - MAX_ASSETS=1000: skip assets beyond this cap
      - MAX_SIGNALS_PER_ASSET=3: only keep first 3 signals per asset
      - MAX_FINDINGS=500: cap total findings produced
    """
    findings: list[ExposureFinding] = []

    # Group signals by asset
    asset_map: dict[str, Asset] = {}
    for sig in signals:
        if len(asset_map) >= MAX_ASSETS:
            break
        if sig.asset_key not in asset_map:
            asset_map[sig.asset_key] = Asset(key=sig.asset_key)
        asset = asset_map[sig.asset_key]
        if len(asset.signals) < MAX_SIGNALS_PER_ASSET:
            asset.signals.append(sig)

    _stats["assets_registered"] = len(asset_map)

    # ── Correlate per-asset ───────────────────────────────────────────────────

    for asset_key, asset in asset_map.items():
        if len(findings) >= MAX_FINDINGS:
            break

        # open_bucket: single signal type is sufficient
        if asset.has_bucket:
            finding = _make_open_bucket_finding(asset)
            if finding:
                findings.append(finding)
                _stats["open_buckets_found"] += 1

        # exposed_host: bucket + cert or bucket + DNS
        if asset.has_bucket and (asset.has_cert or asset.has_dns):
            finding = _make_exposed_host_finding(asset)
            if finding:
                findings.append(finding)
                _stats["exposed_hosts_found"] += 1

        # cert_domain_relation: cert signal
        if asset.has_cert:
            finding = _make_cert_domain_finding(asset)
            if finding:
                findings.append(finding)

        # suspicious JARM: known-bad fingerprint prefix
        for sig in asset.signals:
            if sig.signal_type == SIGNAL_TYPE_JARM:
                jarm_hash = sig.metadata.get("jarm_hash", "")
                if any(jarm_hash.startswith(p) for p in _SUSPICIOUS_JARM_PREFIXES):
                    finding = _make_suspicious_fp_finding(asset, sig)
                    if finding:
                        findings.append(finding)
                        break

    # ── JARM infra clustering ────────────────────────────────────────────────
    # Group assets by JARM hash to find co-located infrastructure
    jarm_groups: dict[str, list[str]] = {}
    for asset_key, asset in asset_map.items():
        for sig in asset.signals:
            if sig.signal_type == SIGNAL_TYPE_JARM:
                jarm_hash = sig.metadata.get("jarm_hash", "")
                if jarm_hash and not any(jarm_hash.startswith(p) for p in _SUSPICIOUS_JARM_PREFIXES):
                    if jarm_hash not in jarm_groups:
                        jarm_groups[jarm_hash] = []
                    jarm_groups[jarm_hash].append(asset_key)

    for jarm_hash, hosts in jarm_groups.items():
        if len(hosts) < 2:  # need at least 2 hosts for a cluster
            continue
        if len(findings) >= MAX_FINDINGS:
            break
        # Only report one infra_cluster per JARM hash
        evidence = []
        for host in hosts:
            for sig in asset_map[host].signals:
                if sig.signal_type == SIGNAL_TYPE_JARM:
                    evidence.append(sig.finding_id)
        findings.append(ExposureFinding(
            corr_type=CORR_INFRA_CLUSTER,
            asset_key=f"cluster:{jarm_hash[:16]}",
            confidence=0.85,
            summary=f"Infra cluster: {len(hosts)} hosts sharing JARM hash {jarm_hash[:16]}...",
            evidence_pointers=evidence[:10],
            signal_facets={SIGNAL_TYPE_JARM: 0.85},
            suggested_pivots=[
                {"type": "reverse_whois", "query": jarm_hash[:16]},
                {"type": "jarm_lookup", "query": jarm_hash},
            ],
            payload={
                "jarm_hash": jarm_hash,
                "host_count": len(hosts),
                "hosts": hosts[:20],
            },
        ))
        _stats["infra_clusters_found"] += 1

    _stats["correlations_run"] = len(asset_map)
    _stats["findings_produced"] = len(findings)
    return findings


# ── Finding Factory Methods ───────────────────────────────────────────────────

def _make_open_bucket_finding(asset: Asset) -> ExposureFinding | None:
    """Produce an open_bucket finding from an asset with bucket signal."""
    bucket_sig = next((s for s in asset.signals if s.signal_type == SIGNAL_TYPE_OPEN_BUCKET), None)
    if not bucket_sig:
        return None

    url = bucket_sig.metadata.get("url", "")
    bucket_type = bucket_sig.metadata.get("bucket_type", "unknown")

    # Confidence: bucket type matters
    bucket_confidence: dict[str, float] = {
        "s3": 0.95,
        "firebase": 0.90,
        "elasticsearch": 0.85,
        "mongodb": 0.80,
    }
    conf = bucket_confidence.get(bucket_type, 0.70)

    return ExposureFinding(
        corr_type=CORR_OPEN_BUCKET,
        asset_key=asset.key,
        confidence=conf,
        summary=f"Open {bucket_type} bucket: {url}",
        evidence_pointers=[bucket_sig.finding_id],
        signal_facets={SIGNAL_TYPE_OPEN_BUCKET: conf},
        suggested_pivots=[
            {"type": "bucket_enum", "query": url},
            {"type": "passive_dns", "query": url},
        ],
        payload={
            "url": url,
            "bucket_type": bucket_type,
            "status": bucket_sig.metadata.get("status", 0),
        },
    )


def _make_exposed_host_finding(asset: Asset) -> ExposureFinding | None:
    """Produce an exposed_host finding from an asset with bucket + cert/DNS."""
    bucket_sig = next((s for s in asset.signals if s.signal_type == SIGNAL_TYPE_OPEN_BUCKET), None)
    cert_sig = next((s for s in asset.signals if s.signal_type == SIGNAL_TYPE_CT_CERT), None)
    dns_sig = next((s for s in asset.signals if s.signal_type == SIGNAL_TYPE_PASSIVE_DNS), None)

    evidence: list[str] = []
    if bucket_sig:
        evidence.append(bucket_sig.finding_id)
    if cert_sig:
        evidence.append(cert_sig.finding_id)
    if dns_sig:
        evidence.append(dns_sig.finding_id)

    # Combine confidences
    conf = 0.5
    facets: dict[str, float] = {}
    if bucket_sig:
        facets[SIGNAL_TYPE_OPEN_BUCKET] = 0.95
        conf = max(conf, 0.8)
    if cert_sig:
        facets[SIGNAL_TYPE_CT_CERT] = cert_sig.confidence
        conf = max(conf, 0.85)
    if dns_sig:
        facets[SIGNAL_TYPE_PASSIVE_DNS] = dns_sig.confidence
        conf = max(conf, 0.75)

    url = bucket_sig.metadata.get("url", "") if bucket_sig else asset.key
    domain = cert_sig.metadata.get("domain", "") if cert_sig else ""
    ip = dns_sig.metadata.get("ip", "") if dns_sig else ""

    pivots: list[dict] = []
    if domain:
        pivots.append({"type": "ct_log", "query": domain})
    if ip:
        pivots.append({"type": "passive_dns", "query": ip})
    pivots.append({"type": "jarm_fingerprint", "query": asset.key})

    return ExposureFinding(
        corr_type=CORR_EXPOSED_HOST,
        asset_key=asset.key,
        confidence=conf,
        summary=f"Exposed host: {url} (bucket + cert/DNS correlation)",
        evidence_pointers=evidence,
        signal_facets=facets,
        suggested_pivots=pivots,
        payload={
            "url": url,
            "domain": domain,
            "ip": ip,
            "has_bucket": bool(bucket_sig),
            "has_cert": bool(cert_sig),
            "has_dns": bool(dns_sig),
        },
    )


def _make_cert_domain_finding(asset: Asset) -> ExposureFinding | None:
    """Produce a cert_domain_relation finding."""
    cert_sig = next((s for s in asset.signals if s.signal_type == SIGNAL_TYPE_CT_CERT), None)
    if not cert_sig:
        return None

    issuer = cert_sig.metadata.get("issuer", "")
    domain = cert_sig.metadata.get("domain", "")
    san = cert_sig.metadata.get("san", "")

    return ExposureFinding(
        corr_type=CORR_CERT_DOMAIN,
        asset_key=asset.key,
        confidence=cert_sig.confidence,
        summary=f"CT cert: {san[:40]}... issued by {issuer[:30]}",
        evidence_pointers=[cert_sig.finding_id],
        signal_facets={SIGNAL_TYPE_CT_CERT: cert_sig.confidence},
        suggested_pivots=[
            {"type": "ct_log", "query": domain},
            {"type": "passive_dns", "query": domain},
        ],
        payload={
            "issuer": issuer,
            "domain": domain,
            "san": san,
            "cert_count": cert_sig.metadata.get("cert_count", 0),
        },
    )


def _make_suspicious_fp_finding(asset: Asset, sig: AssetSignal) -> ExposureFinding | None:
    """Produce a suspicious_service_fingerprint finding."""
    jarm_hash = sig.metadata.get("jarm_hash", "")

    return ExposureFinding(
        corr_type=CORR_SUSPICIOUS_FP,
        asset_key=asset.key,
        confidence=0.6,  # lower confidence — suspicious prefix doesn't mean malicious
        summary=f"Suspicious JARM fingerprint on {asset.key}: {jarm_hash[:20]}...",
        evidence_pointers=[sig.finding_id],
        signal_facets={SIGNAL_TYPE_JARM: 0.6},
        suggested_pivots=[
            {"type": "jarm_lookup", "query": jarm_hash},
            {"type": "threatintel", "query": asset.key},
        ],
        payload={
            "jarm_hash": jarm_hash,
            "suspicious_reason": "known_suspicious_prefix",
        },
    )


# ── CanonicalFinding Conversion ───────────────────────────────────────────────

def to_canonical_findings(
    findings: list[ExposureFinding],
    query: str,
) -> list["CanonicalFinding"]:
    """
    Convert ExposureFinding list to CanonicalFinding list.

    Each CanonicalFinding:
      - source_type = "exposure_correlation"
      - finding_id = "exp_{hash}"
      - payload_text = JSON with correlation data + evidence envelope fields
    """
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding

    canonical: list[CanonicalFinding] = []
    ts = time.time()

    for finding in findings[:MAX_FINDINGS]:
        # Build a stable finding_id from asset_key + corr_type + ts
        id_input = f"{finding.asset_key}:{finding.corr_type}:{int(ts)}"
        fid = f"exp_{hashlib.sha1(id_input.encode()).hexdigest()[:24]}"

        # Build evidence envelope payload
        import json
        payload = {
            "corr_type": finding.corr_type,
            "asset_key": finding.asset_key,
            "summary": finding.summary,
            "evidence_pointers": finding.evidence_pointers,
            "signal_facets": finding.signal_facets,
            "suggested_pivots": finding.suggested_pivots,
            "correlation_payload": finding.payload,
            "_f202c": True,
        }

        canonical.append(CanonicalFinding(
            finding_id=fid,
            query=query,
            source_type="exposure_correlation",
            confidence=finding.confidence,
            ts=ts,
            provenance=("exposure_correlator", finding.corr_type),
            payload_text=json.dumps(payload, ensure_ascii=False),
        ))

    return canonical


# ── Public API ─────────────────────────────────────────────────────────────────

def correlate_exposure_signals(
    findings: list["CanonicalFinding"],
    query: str,
) -> list["CanonicalFinding"]:
    """
    F202C: Correlate asset exposure signals from sprint findings.

    Entry point for the exposure correlation sidecar.

    Pipeline:
      1. Extract signals from findings (ct_log, open_storage, jarm, passive_dns)
      2. Correlate signals into ExposureFinding objects
      3. Convert to CanonicalFinding list
      4. Return for async_ingest_findings_batch ingestion

    Bounds enforced:
      - MAX_ASSETS=1000
      - MAX_SIGNALS_PER_ASSET=3
      - MAX_FINDINGS=500

    Fail-soft: returns [] on any error.

    Returns:
        List of CanonicalFinding with source_type="exposure_correlation".
    """
    try:
        if not findings:
            return []

        # 1. Extract signals from current sprint findings
        signals = extract_signals(findings)
        if not signals:
            return []

        # 2. Correlate signals into exposure findings
        exp_findings = _correlate_signals(signals)
        if not exp_findings:
            return []

        # 3. Convert to canonical findings
        canonical = to_canonical_findings(exp_findings, query)
        return canonical

    except Exception as e:
        logger.debug(f"[ExposureCorrelator] correlation failed: {e}")
        return []


# ── Adapter ───────────────────────────────────────────────────────────────────

class ExposureCorrelatorAdapter:
    """
    F202C: Bounded exposure correlation adapter.

    Wraps the correlation pipeline with M1-safe bounds and fail-soft guarantees.
    """

    def __init__(self) -> None:
        self._stats_snapshot: dict[str, int] = {}

    def correlate(self, findings: list["CanonicalFinding"], query: str) -> list["CanonicalFinding"]:
        """
        Correlate exposure signals from findings.

        Returns:
            List of CanonicalFinding (source_type="exposure_correlation").
        """
        result = correlate_exposure_signals(findings, query)
        self._stats_snapshot = get_correlator_stats()
        return result

    def get_stats(self) -> dict[str, int]:
        """Return latest correlation stats."""
        return self._stats_snapshot

    def reset(self) -> None:
        """Reset internal state and stats."""
        reset_correlator_stats()
        self._stats_snapshot = {}


def create_exposure_correlator_adapter() -> ExposureCorrelatorAdapter:
    """Factory for ExposureCorrelatorAdapter."""
    return ExposureCorrelatorAdapter()
