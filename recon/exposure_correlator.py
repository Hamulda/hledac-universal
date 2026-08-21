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
import asyncio
import hashlib
from hledac.universal.utils.asyncx import safe_create_task
import logging
import time
from collections.abc import Generator
from dataclasses import field
import msgspec
from compat.msgspec_gc_compat import Struct
from typing import TYPE_CHECKING
from hledac.universal.utils.msgspec_json import loads as _msgspec_loads, dumps_str as _msgspec_dumps_str
from hledac.universal.utils.asyncx import parallel_ok
if TYPE_CHECKING:
    import httpx
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding
    from hledac.universal.network.passive_dns import PassiveDNSResolver
logger = logging.getLogger(__name__)
MAX_ASSETS: int = 1000
MAX_SIGNALS_PER_ASSET: int = 3
MAX_FINDINGS: int = 500
MAX_BUCKET_CANDIDATES_PER_ENTITY: int = 30
MAX_BUCKET_CHECKS_PARALLEL: int = 10
MAX_SUBDOMAIN_TAKEOVER_SUBDOMAINS: int = 50
MAX_CLOUD_FINDINGS: int = 100
SIGNAL_TYPE_CT_CERT = 'ct_cert'
SIGNAL_TYPE_OPEN_BUCKET = 'open_bucket'
SIGNAL_TYPE_JARM = 'jarm_fp'
SIGNAL_TYPE_PASSIVE_DNS = 'passive_dns'
SIGNAL_TYPE_PASSIVE_FINGERPRINT = 'passive_fingerprint'
CORR_EXPOSED_HOST = 'exposed_host'
CORR_CERT_DOMAIN = 'cert_domain_relation'
CORR_OPEN_BUCKET = 'open_bucket'
CORR_SUSPICIOUS_FP = 'suspicious_service_fingerprint'
CORR_INFRA_CLUSTER = 'infra_cluster'
CORR_SUBDOMAIN_TAKEOVER = 'subdomain_takeover_possible'
_SUSPICIOUS_JARM_PREFIXES: tuple[str, ...] = ('2a2a2a2a2a2a', '000000000000')
_stats: dict[str, int] = {'assets_registered': 0, 'signals_extracted': 0, 'correlations_run': 0, 'findings_produced': 0, 'exposed_hosts_found': 0, 'open_buckets_found': 0, 'infra_clusters_found': 0, 'subdomain_takeovers_found': 0}
_S3_SUFFIXES: tuple[str, ...] = ('', '-prod', '-dev', '-staging', '-backup', '-data', '-assets', '-media', '-static', '-files', '-documents', '-private', '-public', '-logs', '-config', '-database', '-storage', '-usercontent')
_CLOUD_BUCKET_TEMPLATES: tuple[tuple[str, str, str], ...] = (('s3', 's3', 'https://{bucket}.s3.amazonaws.com'), ('s3', 's3', 'https://{bucket}.s3.{region}.amazonaws.com'), ('gcs', 'gcs', 'https://storage.googleapis.com/{bucket}'), ('gcs', 'gcs', 'https://{bucket}.storage.googleapis.com'), ('azure', 'azure', 'https://{bucket}.blob.core.windows.net'), ('azure', 'azure', 'https://{bucket}.blob.core.windows.net/{bucket}'))
_SUBDOMAIN_TAKEOVER_PROVIDERS: tuple[tuple[str, str, tuple[str, ...]], ...] = (('github_io', 'github', ('.github.io',)), ('azurewebsites', 'azure', ('.azurewebsites.net',)), ('netlify', 'netlify', ('.netlify.app', '.netlify.com')), ('heroku', 'heroku', ('.herokuapp.com',)), ('shopify', 'shopify', ('.myshopify.com', '.shopify.com')), ('ghost', 'ghost', ('.ghost.io',)), ('wordpress', 'wordpress', ('.wordpress.com',)), ('firebase', 'firebase', ('.firebaseapp.com', '.firebase.io')), ('appspot', 'gcp', ('.appspot.com',)), ('cloudfunctions', 'gcp', ('.cloudfunctions.net',)), ('surge', 'surge', ('.surge.sh',)), ('vercel', 'vercel', ('.vercel.app',)), ('render', 'render', ('.onrender.com',)), ('gitlab', 'gitlab', ('.gitlab.io',)), ('bitbucket', 'bitbucket', ('.bitbucket.io',)))
_GENERIC_HOSTING_JARM_PREFIXES: tuple[str, ...] = ('2a2a2a2a2a2a', '000000000000', '07e14f8e7e7e7e')

def get_correlator_stats() -> dict[str, int]:
    """Return copy of correlator stats (for probe verification)."""
    return dict(_stats)

def reset_correlator_stats() -> None:
    """Reset all stats to zero (for probe test isolation)."""
    _stats.clear()
    _stats.update({'assets_registered': 0, 'signals_extracted': 0, 'correlations_run': 0, 'findings_produced': 0, 'exposed_hosts_found': 0, 'open_buckets_found': 0, 'infra_clusters_found': 0, 'subdomain_takeovers_found': 0})

class AssetSignal(Struct):
    """A single signal associated with an asset."""
    signal_type: str
    asset_key: str
    confidence: float
    metadata: dict
    finding_id: str

class Asset(Struct):
    """An asset (host, domain, IP) with collected signals."""
    key: str
    signals: list[AssetSignal] = field(default_factory=list)

    @property
    def has_bucket(self) -> bool:
        return any((s.signal_type == SIGNAL_TYPE_OPEN_BUCKET for s in self.signals))

    @property
    def has_cert(self) -> bool:
        return any((s.signal_type == SIGNAL_TYPE_CT_CERT for s in self.signals))

    @property
    def has_jarm(self) -> bool:
        return any((s.signal_type == SIGNAL_TYPE_JARM for s in self.signals))

    @property
    def has_dns(self) -> bool:
        return any((s.signal_type == SIGNAL_TYPE_PASSIVE_DNS for s in self.signals))

class ExposureFinding(Struct):
    """A correlated exposure finding with evidence."""
    corr_type: str
    asset_key: str
    confidence: float
    summary: str
    evidence_pointers: list[str]
    signal_facets: dict[str, float]
    suggested_pivots: list[dict]
    payload: dict

def _normalize_host(asset_key: str) -> str:
    """Strip port, scheme, and normalize to lowercase."""
    key = asset_key.lower().strip()
    for prefix in ('https://', 'http://'):
        if key.startswith(prefix):
            key = key[len(prefix):]
    if ':' in key:
        key = key.rsplit(':', 1)[0]
    return key

def _normalize_url(asset_key: str) -> str:
    """Normalize bucket URL to base key."""
    key = asset_key.lower().strip()
    for prefix in ('https://', 'http://'):
        if key.startswith(prefix):
            key = key[len(prefix):]
    return key.rstrip('/')

def _extract_jarm_from_payload(payload_text: str | None) -> str | None:
    """Extract JARM hash from payload_text."""
    if not payload_text:
        return None
    try:
        data = _msgspec_loads(payload_text) if isinstance(payload_text, str) else payload_text
        h = data.get('jarm_hash') or data.get('jarm') or data.get('hash')
        if h and len(h) == 62:
            return h
    except Exception:  # noqa: BLE001
        pass
    return None

def _generate_bucket_candidates(entity_name: str) -> Generator[tuple[str, str, str]]:
    """
    Generate lazy bucket name candidates for an entity.

    Yields suffix-augmented names for S3-style buckets.
    Generator pattern: yields tuples of (candidate_name, provider, url_template).
    """
    name = entity_name.lower().split('://')[-1].split('/')[0].split(':')[0]
    parts = name.split('.')
    base_name = parts[0] if parts else name
    for suffix in _S3_SUFFIXES:
        bucket_name = f'{base_name}{suffix}'
        for _, provider, template in _CLOUD_BUCKET_TEMPLATES:
            yield (bucket_name, provider, template)

async def _check_bucket_head(session: httpx.AsyncClient, bucket_name: str, provider: str, url_template: str) -> dict | None:
    """
    Perform HEAD check on a single bucket URL.

    Returns dict with bucket info if accessible (200/403), None if unreachable.
    """
    try:
        import httpx
        url = url_template.format(bucket=bucket_name)
        async with session.head(url, timeout=httpx.Timeout(total=10.0), follow_redirects=True) as resp:
            status = resp.status
            if status in (200, 403):
                return {'url': url, 'bucket_name': bucket_name, 'provider': provider, 'status': status, 'is_open': status == 200, 'headers': dict(resp.headers)}
    except Exception:  # noqa: BLE001
        pass
    return None

async def _detect_open_buckets_async(entity_name: str) -> list[dict]:
    """
    Async bucket enumeration for a single entity.

    Uses lazy generator + semaphore(10) for parallel checks.
    Returns list of accessible bucket dicts.
    """
    import asyncio
    try:
    except Exception:
        return []
    candidates = _generate_bucket_candidates(entity_name)
    session = await httpx.AsyncClient()
    semaphore = asyncio.Semaphore(MAX_BUCKET_CHECKS_PARALLEL)

    async def _check_with_sem(candidate: tuple[str, str, str]) -> dict | None:
        async with semaphore:
            return await _check_bucket_head(session, *candidate)
    tasks = []
    async for candidate in _async_candidate_gen(candidates, MAX_BUCKET_CANDIDATES_PER_ENTITY):
        tasks.append(safe_create_task(_check_with_sem(candidate)))
    if not tasks:
        return []
    results = await parallel_ok(*tasks, label='exposure_correlator:353')
    findings = []
    for r in results:
        if isinstance(r, Exception):
            continue
        if r is not None:
            findings.append(r)
    return findings

async def _async_candidate_gen(candidates, max_items: int):
    """
    Async generator that yields from an iterator with a cap.
    
    Cleanup:
        - Properly handles early exit via try/finally
        - Ensures no resource leaks on cancellation
    """
    count = 0
    try:
        for candidate in candidates:
            if count >= max_items:
                break
            yield candidate
            count += 1
    finally:
        # Cleanup: clear reference to candidates on early exit
        # This helps garbage collection when generator is abandoned mid-iteration
        del candidates

def _detect_open_buckets(entity_name: str) -> list[dict]:
    """
    Sync wrapper for bucket enumeration.

    Returns list of bucket findings (sync, for integration with existing pipeline).
    SCAVENGER-FIX: Uses run_sync_async() instead of get_running_loop + run_until_complete.
    run_sync_async() uses asyncio.Runner() (PEP 654) for Python 3.11+ and handles
    both running and non-running event loop cases.
    """
    from hledac.universal.utils.sync_bridge import run_sync_async
    return run_sync_async(_detect_open_buckets_async(entity_name))

async def _resolve_cname_chain(resolver: PassiveDNSResolver, subdomain: str, max_depth: int=3) -> list[str]:
    """
    Resolve CNAME chain for a subdomain.

    Returns list of CNAME targets in chain order.
    """
    chain = []
    current = subdomain
    seen = set()
    for _ in range(max_depth):
        if current in seen:
            break
        seen.add(current)
        try:
            cnames = await resolver.resolve(current, rdtype='CNAME')
            if not cnames:
                break
            chain.append(cnames[0])
            current = cnames[0]
        except Exception:
            break
    return chain

def _check_takeover_provider(cname_chain: list[str]) -> tuple[str, str] | None:
    """
    Check if CNAME chain matches a takeover-vulnerable provider.

    Returns (provider_name, target_pattern) if matched, None otherwise.
    """
    for provider_name, _, patterns in _SUBDOMAIN_TAKEOVER_PROVIDERS:
        for cname in cname_chain:
            for pattern in patterns:
                if cname.endswith(pattern) or pattern.endswith(cname):
                    return (provider_name, pattern)
    return None

async def _detect_subdomain_takeover_async(subdomains: list[str]) -> list[dict]:
    """
    Async subdomain takeover detection.

    Uses PassiveDNSResolver to follow CNAME chains and checks for
    takeover-vulnerable providers.
    """
    from hledac.universal.network.passive_dns import PassiveDNSResolver
    findings = []
    resolver = PassiveDNSResolver()
    for subdomain in subdomains[:MAX_SUBDOMAIN_TAKEOVER_SUBDOMAINS]:
        try:
            cname_chain = await _resolve_cname_chain(resolver, subdomain)
            if not cname_chain:
                continue
            takeover_info = _check_takeover_provider(cname_chain)
            if takeover_info:
                provider, pattern = takeover_info
                findings.append({'subdomain': subdomain, 'cname_chain': cname_chain, 'provider': provider, 'target_pattern': pattern, 'severity': 'CRITICAL'})
        except Exception:
            continue
    return findings

def _detect_subdomain_takeover(subdomains: list[str]) -> list[dict]:
    """
    Sync wrapper for subdomain takeover detection.

    Returns list of takeover findings.
    SCAVENGER-FIX: Uses run_sync_async() instead of get_running_loop + run_until_complete.
    run_sync_async() uses asyncio.Runner() (PEP 654) for Python 3.11+ and handles
    both running and non-running event loop cases.
    """
    from hledac.universal.utils.sync_bridge import run_sync_async
    return run_sync_async(_detect_subdomain_takeover_async(subdomains))

def _is_generic_hosting_jarm(jarm_hash: str) -> bool:
    """
    Check if JARM hash indicates generic hosting infrastructure.

    Generic hosting pages return similar JARM regardless of content.
    Real services have distinct fingerprints.
    """
    if not jarm_hash or len(jarm_hash) != 62:
        return False
    if any((jarm_hash.startswith(p) for p in _GENERIC_HOSTING_JARM_PREFIXES)):
        return True
    return False

def _classify_jarm_hosting(jarm_hash: str, http_status: int) -> str:
    """
    Classify if a JARM + HTTP response indicates hosting vs real content.

    Returns: "generic_hosting" | "real_content" | "unknown"
    """
    if _is_generic_hosting_jarm(jarm_hash):
        return 'generic_hosting'
    if http_status == 404 and jarm_hash:
        return 'possible_takeover'
    if http_status == 200 and jarm_hash and (len(jarm_hash) == 62):
        return 'real_content'
    return 'unknown'

class OpenStorageResult(Struct):
    """Normalized DTO for open storage scan results."""
    url: str
    status: int
    bucket_type: str
    headers: dict

def scan_open_storage(domains: list[str]) -> list[OpenStorageResult]:
    """
    Scan domains for open storage buckets.

    Returns list of OpenStorageResult for buckets returning HTTP 200.
    Fail-soft: returns [] on any error.
    SCAVENGER-FIX: Uses run_sync_async() instead of new_event_loop/run_until_complete.
    run_sync_async() uses asyncio.Runner() (PEP 654) for Python 3.11+ and handles
    both running and non-running event loop cases.
    """
    try:
        from hledac.universal.network.open_storage_scanner import _OpenStorageScanner
    except Exception:
        return []
    results: list[OpenStorageResult] = []
    scanner = _OpenStorageScanner()

    async def _scan_all():
        tasks = [scanner.scan_domain(d) for d in domains]
        return await parallel_ok(*tasks, label='exposure_correlator:562')
    try:
        from hledac.universal.utils.sync_bridge import run_sync_async
        scan_results = run_sync_async(_scan_all())
    except Exception:
        return []
    for scan_result in scan_results:
        if isinstance(scan_result, Exception):
            continue
        if isinstance(scan_result, (list, tuple, set)):
            for item in scan_result:
                results.append(OpenStorageResult(url=item.get('url', ''), status=item.get('status', 0), bucket_type=item.get('type', 'unknown'), headers=item.get('headers', {})))
    return results

def extract_signals(findings: list[CanonicalFinding]) -> list[AssetSignal]:
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
        src = getattr(finding, 'source_type', '') or ''
        fid = getattr(finding, 'finding_id', '')
        confidence = getattr(finding, 'confidence', 0.5) or 0.5
        payload = getattr(finding, 'payload_text', None) or '{}'
        try:
            data = _msgspec_loads(payload) if isinstance(payload, str) else payload
        except Exception:
            data = {}
        if src == 'ct_log':
            san = fid.replace('ct_', '') if fid.startswith('ct_') else fid
            asset_key = _normalize_host(san)
            issuer = data.get('issuer', '')
            cert_count = data.get('cert_count', 0)
            domain = data.get('domain', '')
            signals.append(AssetSignal(signal_type=SIGNAL_TYPE_CT_CERT, asset_key=asset_key, confidence=confidence, metadata={'issuer': issuer, 'cert_count': cert_count, 'domain': domain, 'san': san}, finding_id=fid))
        elif src == 'open_storage':
            url = data.get('url', '')
            bucket_type = data.get('type', 'unknown')
            status = data.get('status', 0)
            if url:
                signals.append(AssetSignal(signal_type=SIGNAL_TYPE_OPEN_BUCKET, asset_key=_normalize_url(url), confidence=confidence, metadata={'url': url, 'bucket_type': bucket_type, 'status': status}, finding_id=fid))
        elif src == 'jarm':
            jarm_hash = _extract_jarm_from_payload(payload)
            if jarm_hash:
                asset_key = _normalize_host(fid.replace('jarm_', '')) if fid.startswith('jarm_') else _normalize_host(fid)
                signals.append(AssetSignal(signal_type=SIGNAL_TYPE_JARM, asset_key=asset_key, confidence=confidence, metadata={'jarm_hash': jarm_hash}, finding_id=fid))
        elif src == 'passive_dns':
            domain = data.get('domain', '')
            ip = data.get('ip', '') or data.get('ip_address', '')
            if domain:
                asset_key = _normalize_host(domain)
                signals.append(AssetSignal(signal_type=SIGNAL_TYPE_PASSIVE_DNS, asset_key=asset_key, confidence=confidence, metadata={'domain': domain, 'ip': ip, 'record_type': data.get('record_type', 'A')}, finding_id=fid))
        elif src == 'passive_fingerprint':
            service_name = data.get('service_name', '')
            product = data.get('product', '')
            version = data.get('version', '')
            facets = data.get('facets', {})
            if service_name:
                asset_key = getattr(finding, 'ioc_value', '') or service_name
                signals.append(AssetSignal(signal_type=SIGNAL_TYPE_PASSIVE_FINGERPRINT, asset_key=asset_key, confidence=confidence, metadata={'service_name': service_name, 'product': product, 'version': version, 'facets': facets}, finding_id=fid))
    _stats['signals_extracted'] = len(signals)
    return signals

# F240C: Correlation helpers
def _group_signals_by_asset(signals: list[AssetSignal]) -> dict[str, Asset]:
    """Group signals by asset_key with bounded capacity."""
    asset_map: dict[str, Asset] = {}
    for sig in signals:
        if len(asset_map) >= MAX_ASSETS:
            break
        if sig.asset_key not in asset_map:
            asset_map[sig.asset_key] = Asset(key=sig.asset_key)
        asset = asset_map[sig.asset_key]
        if len(asset.signals) < MAX_SIGNALS_PER_ASSET:
            asset.signals.append(sig)
    return asset_map

def _correlate_asset_signals(asset: Asset) -> list[ExposureFinding]:
    """Correlate signals for a single asset."""
    findings: list[ExposureFinding] = []
    if len(findings) >= MAX_FINDINGS:
        return findings
    if asset.has_bucket:
        if finding := _make_open_bucket_finding(asset):
            findings.append(finding)
            _stats['open_buckets_found'] += 1
    if asset.has_bucket and (asset.has_cert or asset.has_dns):
        if finding := _make_exposed_host_finding(asset):
            findings.append(finding)
            _stats['exposed_hosts_found'] += 1
    if asset.has_cert:
        if finding := _make_cert_domain_finding(asset):
            findings.append(finding)
    for sig in asset.signals:
        if sig.signal_type == SIGNAL_TYPE_JARM:
            jarm_hash = sig.metadata.get('jarm_hash', '')
            if any(jarm_hash.startswith(p) for p in _SUSPICIOUS_JARM_PREFIXES):
                if finding := _make_suspicious_fp_finding(asset, sig):
                    findings.append(finding)
                    break
    return findings

def _build_jarm_clusters(asset_map: dict[str, Asset]) -> dict[str, list[str]]:
    """Build JARM hash clusters for infrastructure correlation."""
    jarm_groups: dict[str, list[str]] = {}
    for asset_key, asset in asset_map.items():
        for sig in asset.signals:
            if sig.signal_type == SIGNAL_TYPE_JARM:
                jarm_hash = sig.metadata.get('jarm_hash', '')
                if jarm_hash and not any(jarm_hash.startswith(p) for p in _SUSPICIOUS_JARM_PREFIXES):
                    jarm_groups.setdefault(jarm_hash, []).append(asset_key)
    return jarm_groups

def _emit_infra_clusters(jarm_groups: dict[str, list[str]], asset_map: dict[str, Asset]) -> list[ExposureFinding]:
    """Emit infrastructure cluster findings from JARM groups."""
    findings: list[ExposureFinding] = []
    for jarm_hash, hosts in jarm_groups.items():
        if len(hosts) < 2 or len(findings) >= MAX_FINDINGS:
            continue
        evidence = [sig.finding_id for host in hosts for sig in asset_map[host].signals if sig.signal_type == SIGNAL_TYPE_JARM]
        findings.append(ExposureFinding(
            corr_type=CORR_INFRA_CLUSTER,
            asset_key=f'cluster:{jarm_hash[:16]}',
            confidence=0.85,
            summary=f'Infra cluster: {len(hosts)} hosts sharing JARM hash {jarm_hash[:16]}...',
            evidence_pointers=evidence[:10],
            signal_facets={SIGNAL_TYPE_JARM: 0.85},
            suggested_pivots=[{'type': 'reverse_whois', 'query': jarm_hash[:16]}, {'type': 'jarm_lookup', 'query': jarm_hash}],
            payload={'jarm_hash': jarm_hash, 'host_count': len(hosts), 'hosts': hosts[:20]}
        ))
        _stats['infra_clusters_found'] += 1
    return findings

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
    asset_map = _group_signals_by_asset(signals)
    _stats['assets_registered'] = len(asset_map)

    findings: list[ExposureFinding] = []
    for asset in asset_map.values():
        if len(findings) >= MAX_FINDINGS:
            break
        findings.extend(_correlate_asset_signals(asset))

    # JARM infrastructure clustering
    jarm_groups = _build_jarm_clusters(asset_map)
    findings.extend(_emit_infra_clusters(jarm_groups, asset_map))

    _stats['correlations_run'] = len(asset_map)
    _stats['findings_produced'] = len(findings)
    return findings

def _make_open_bucket_finding(asset: Asset) -> ExposureFinding | None:
    """Produce an open_bucket finding from an asset with bucket signal."""
    bucket_sig = next((s for s in asset.signals if s.signal_type == SIGNAL_TYPE_OPEN_BUCKET), None)
    if not bucket_sig:
        return None
    url = bucket_sig.metadata.get('url', '')
    bucket_type = bucket_sig.metadata.get('bucket_type', 'unknown')
    bucket_confidence: dict[str, float] = {'s3': 0.95, 'firebase': 0.9, 'elasticsearch': 0.85, 'mongodb': 0.8}
    conf = bucket_confidence.get(bucket_type, 0.7)
    return ExposureFinding(corr_type=CORR_OPEN_BUCKET, asset_key=asset.key, confidence=conf, summary=f'Open {bucket_type} bucket: {url}', evidence_pointers=[bucket_sig.finding_id], signal_facets={SIGNAL_TYPE_OPEN_BUCKET: conf}, suggested_pivots=[{'type': 'bucket_enum', 'query': url}, {'type': 'passive_dns', 'query': url}], payload={'url': url, 'bucket_type': bucket_type, 'status': bucket_sig.metadata.get('status', 0)})

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
    url = bucket_sig.metadata.get('url', '') if bucket_sig else asset.key
    domain = cert_sig.metadata.get('domain', '') if cert_sig else ''
    ip = dns_sig.metadata.get('ip', '') if dns_sig else ''
    pivots: list[dict] = []
    if domain:
        pivots.append({'type': 'ct_log', 'query': domain})
    if ip:
        pivots.append({'type': 'passive_dns', 'query': ip})
    pivots.append({'type': 'jarm_fingerprint', 'query': asset.key})
    return ExposureFinding(corr_type=CORR_EXPOSED_HOST, asset_key=asset.key, confidence=conf, summary=f'Exposed host: {url} (bucket + cert/DNS correlation)', evidence_pointers=evidence, signal_facets=facets, suggested_pivots=pivots, payload={'url': url, 'domain': domain, 'ip': ip, 'has_bucket': bool(bucket_sig), 'has_cert': bool(cert_sig), 'has_dns': bool(dns_sig)})

def _make_cert_domain_finding(asset: Asset) -> ExposureFinding | None:
    """Produce a cert_domain_relation finding."""
    cert_sig = next((s for s in asset.signals if s.signal_type == SIGNAL_TYPE_CT_CERT), None)
    if not cert_sig:
        return None
    issuer = cert_sig.metadata.get('issuer', '')
    domain = cert_sig.metadata.get('domain', '')
    san = cert_sig.metadata.get('san', '')
    return ExposureFinding(corr_type=CORR_CERT_DOMAIN, asset_key=asset.key, confidence=cert_sig.confidence, summary=f'CT cert: {san[:40]}... issued by {issuer[:30]}', evidence_pointers=[cert_sig.finding_id], signal_facets={SIGNAL_TYPE_CT_CERT: cert_sig.confidence}, suggested_pivots=[{'type': 'ct_log', 'query': domain}, {'type': 'passive_dns', 'query': domain}], payload={'issuer': issuer, 'domain': domain, 'san': san, 'cert_count': cert_sig.metadata.get('cert_count', 0)})

def _make_suspicious_fp_finding(asset: Asset, sig: AssetSignal) -> ExposureFinding | None:
    """Produce a suspicious_service_fingerprint finding."""
    jarm_hash = sig.metadata.get('jarm_hash', '')
    return ExposureFinding(corr_type=CORR_SUSPICIOUS_FP, asset_key=asset.key, confidence=0.6, summary=f'Suspicious JARM fingerprint on {asset.key}: {jarm_hash[:20]}...', evidence_pointers=[sig.finding_id], signal_facets={SIGNAL_TYPE_JARM: 0.6}, suggested_pivots=[{'type': 'jarm_lookup', 'query': jarm_hash}, {'type': 'threatintel', 'query': asset.key}], payload={'jarm_hash': jarm_hash, 'suspicious_reason': 'known_suspicious_prefix'})

def to_canonical_findings(findings: list[ExposureFinding], query: str) -> list[CanonicalFinding]:
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
        id_input = f'{finding.asset_key}:{finding.corr_type}:{int(ts)}'
        fid = f'exp_{hashlib.sha256(id_input.encode()).hexdigest()[:24]}'
        payload = {'corr_type': finding.corr_type, 'asset_key': finding.asset_key, 'summary': finding.summary, 'evidence_pointers': finding.evidence_pointers, 'signal_facets': finding.signal_facets, 'suggested_pivots': finding.suggested_pivots, 'correlation_payload': finding.payload, '_f202c': True}
        canonical.append(CanonicalFinding(finding_id=fid, query=query, source_type='exposure_correlation', confidence=finding.confidence, ts=ts, provenance=('exposure_correlator', finding.corr_type), payload_text=_msgspec_dumps_str(payload, ensure_ascii=False)))
    return canonical

def correlate_exposure_signals(findings: list[CanonicalFinding], query: str) -> list[CanonicalFinding]:
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
        signals = extract_signals(findings)
        if not signals:
            return []
        exp_findings = _correlate_signals(signals)
        if not exp_findings:
            return []
        canonical = to_canonical_findings(exp_findings, query)
        return canonical
    except Exception as e:
        logger.debug(f'[ExposureCorrelator] correlation failed: {e}')
        return []

class ExposureCorrelatorAdapter:
    """
    F202C: Bounded exposure correlation adapter.

    Wraps the correlation pipeline with M1-safe bounds and fail-soft guarantees.
    """
    __slots__ = tuple(('_stats_snapshot',))

    def __init__(self) -> None:
        self._stats_snapshot: dict[str, int] = {}

    def correlate(self, findings: list[CanonicalFinding], query: str) -> list[CanonicalFinding]:
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

    def enumerate_cloud_buckets(self, entity_name: str) -> list[dict]:
        """
        Enumerate S3/GCP/Azure buckets for an entity name.

        Uses lazy generator with semaphore(10) for parallel HEAD checks.
        Returns list of bucket findings with provider, status, and severity.

        Bounds:
            - MAX_BUCKET_CANDIDATES_PER_ENTITY=30 candidates max
            - MAX_BUCKET_CHECKS_PARALLEL=10 parallel checks
            - 200 = OPEN BUCKET (HIGH severity), 403 = bucket exists (MEDIUM)
        """
        findings = _detect_open_buckets(entity_name)
        _stats['open_buckets_found'] += len(findings)
        return findings

    def detect_subdomain_takeovers(self, subdomains: list[str]) -> list[dict]:
        """
        Detect subdomain takeover vulnerabilities.

        Uses PassiveDNSResolver to follow CNAME chains and identifies
        subdomains pointing to takeover-vulnerable providers.

        Returns list of takeover findings with severity=CRITICAL.

        Bounds:
            - MAX_SUBDOMAIN_TAKEOVER_SUBDOMAINS=50 subdomains per entity
        """
        findings = _detect_subdomain_takeover(subdomains)
        _stats['subdomain_takeovers_found'] += len(findings)
        return findings

def create_exposure_correlator_adapter() -> ExposureCorrelatorAdapter:
    """Factory for ExposureCorrelatorAdapter."""
    return ExposureCorrelatorAdapter()