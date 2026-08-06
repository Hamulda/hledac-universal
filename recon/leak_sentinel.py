"""
LeakSecretSentinel — F202D: Bounded leak and secret sentinel optional branch.

Optional sidecar that converts paste/GitHub/breach signals into redacted



CanonicalFinding objects with evidence pointers. Runs after CT findings are
accepted — does NOT block finding acceptance.

Signal sources:
  - data_leak_hunter: breach API results (HaveIBeenPwned, DeHashed, etc.)
  - pastebin_monitor: paste site scraping (pastebin, paste.gg, rentry)
  - github_secret_scanner: GitHub code search for leaked secrets

Constraints:
  - No raw secrets in report/export — all masked via pii_gate.fallback_sanitize
  - External calls timeout + fail-soft
  - No background monitoring loop — single-shot bounded execution
  - Persist only via async_ingest_findings_batch()

Bounds:
  - MAX_LEAK_SOURCES = 3          paste, github, breach
  - MAX_FINDINGS_PER_SOURCE = 50  max findings per source
  - MAX_TOTAL_FINDINGS = 100     max findings across all sources
  - TIMEOUT_PER_SOURCE = 30.0    seconds per source fetch

Evidence envelope (stored in payload_text):
  - audit_reason: str
  - evidence_pointers: list[str]
  - signal_facets: dict[str, float]
  - suggested_pivots: list[dict]
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field
import msgspec
from enum import StrEnum
from typing import TYPE_CHECKING, Any
from hledac.universal.utils.async_helpers import safe_create_task, safe_gather_fire_and_forget

class LeakSentinelError(StrEnum):
    """String-based error codes for fail-soft error reporting."""
    PASTEBIN_NOT_AVAILABLE = 'pastebin_monitor not available'
    PASTEBIN_TIMEOUT = 'pastebin_monitor timeout'
    PASTEBIN_ERROR = 'pastebin_monitor error: {reason}'
    PASTEBIN_ADAPTER_ERROR = 'pastebin adapter error: {reason}'
    GITHUB_NOT_AVAILABLE = 'github_secret_scanner not available'
    GITHUB_FORMAT_ERROR = "github scan requires 'owner/repo' format"
    GITHUB_TIMEOUT = 'github_secret_scanner timeout'
    GITHUB_ERROR = 'github_secret_scanner error: {reason}'
    GITHUB_ADAPTER_ERROR = 'github adapter error: {reason}'
    DATA_LEAK_NOT_AVAILABLE = 'data_leak_hunter not available'
    DATA_LEAK_INIT_ERROR = 'data_leak_hunter init failed'
    DATA_LEAK_TIMEOUT = 'data_leak_hunter timeout'
    DATA_LEAK_ERROR = 'data_leak_hunter error: {reason}'
    BREACH_ADAPTER_ERROR = 'breach adapter error: {reason}'
try:
    import orjson
    _ORJSON_AVAILABLE = True
except ImportError:
    _ORJSON_AVAILABLE = False
    import json as _json

def _leak_json_dumps(obj: Any) -> str:
    """Compact JSON string. orjson is 2-3× faster, default output is compact."""
    if _ORJSON_AVAILABLE:
        return orjson.dumps(obj).decode('utf-8')
    return _json.dumps(obj)
_ZSTD_COMPRESS: Any | None = None

def _get_zstd_compress():
    """Lazily import zstd.compress, or return None if unavailable."""
    global _ZSTD_COMPRESS
    if _ZSTD_COMPRESS is None:
        try:
            import zstd
            _ZSTD_COMPRESS = zstd.compress
        except Exception:
            _ZSTD_COMPRESS = False
    if _ZSTD_COMPRESS is False:
        return None
    return _ZSTD_COMPRESS

def _maybe_compress_payload(payload_bytes: bytes) -> bytes:
    """Compress payload if zstd is available and savings are ≥50%."""
    compress = _get_zstd_compress()
    if compress is None:
        return payload_bytes
    if len(payload_bytes) < 64:
        return payload_bytes
    compressed = compress(payload_bytes)
    if len(compressed) < len(payload_bytes) * 0.8:
        return b'\x00' + compressed
    return payload_bytes
if TYPE_CHECKING:
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding
logger = logging.getLogger(__name__)
MAX_LEAK_SOURCES: int = 3
MAX_FINDINGS_PER_SOURCE: int = 50
MAX_TOTAL_FINDINGS: int = 100
TIMEOUT_PER_SOURCE: float = 30.0
SOURCE_TYPE_LEAK = 'leak_sentinel'
SOURCE_TYPE_PASTE = 'paste_leak'
SOURCE_TYPE_GITHUB_SECRET = 'github_secret'
_MAX_ENVELOPE_SIZE: int = 4096
_MAX_CONTEXT_LEN: int = 200
_SECRET_PATTERNS: list[tuple[str, str]] = [('\\bAKIA[0-9A-Z]{16}\\b', 'AKIA[REDACTED]'), ('\\bsk_live_[0-9a-zA-Z]{24}\\b', 'sk_live_[REDACTED]'), ('Bearer\\s+[A-Za-z0-9_\\.\\-]{20,}', 'Bearer [REDACTED]'), ('-----BEGIN[^\\n]+-----', '[REDACTED:PRIVATE KEY]'), ('(?i)(?:api[_-]?key|secret|password|passwd|token)\\s*[=:]\\s*["\\\']?[A-Za-z0-9_\\.\\-]{8,32}["\\\']?', '[REDACTED:CREDENTIAL]'), ('\\bAIza[0-9A-Za-z\\-_]{35}\\b', 'AIza[REDACTED]')]

def _redact_text(text: str) -> str:
    """Redact PII and secrets from text.

    Secret patterns are applied FIRST (before fallback_sanitize) to prevent
    partial masking by PII patterns. Then fallback_sanitize handles standard PII.
    """
    import re
    result = text
    for pat, repl in _SECRET_PATTERNS:
        result = re.sub(pat, repl, result)
    try:
        from hledac.universal.security.pii_gate import fallback_sanitize
        result = fallback_sanitize(result)
    except Exception:
        pass
    return result

class LeakSourceResult(msgspec.Struct, gc=False):
    """Result from one leak source."""
    source: str
    findings: list[dict]
    errors: list[str]
    elapsed_s: float = 0.0

class LeakSentinelStats(msgspec.Struct, gc=False):
    """Statistics from a leak sentinel run."""
    sources_run: int = 0
    sources_succeeded: int = 0
    findings_produced: int = 0
    findings_stored: int = 0
    elapsed_s: float = 0.0
    errors: list[str] = field(default_factory=list)

async def _fetch_paste_findings(query: str, semaphore: asyncio.Semaphore) -> LeakSourceResult:
    """
    Bounded adapter for pastebin_monitor.

    Converts PasteFinding objects to dicts with redacted secrets.
    Timeout: TIMEOUT_PER_SOURCE seconds.
    Max findings: MAX_FINDINGS_PER_SOURCE.
    """
    result = LeakSourceResult(source='pastebin', findings=[], errors=[])
    try:
        async with semaphore:
            start = time.monotonic()
            try:
                from hledac.universal.recon.pastebin_monitor import PasteFinding
                from hledac.universal.recon.pastebin_monitor import run as run_pastebin
            except ImportError:
                result.errors.append(LeakSentinelError.PASTEBIN_NOT_AVAILABLE)
                return result
            pastes: list[PasteFinding] = []
            try:
                async with asyncio.timeout(TIMEOUT_PER_SOURCE):
                    pastes = await run_pastebin(query)
            except TimeoutError:
                result.errors.append(LeakSentinelError.PASTEBIN_TIMEOUT)
                return result
            except Exception as e:
                result.errors.append(LeakSentinelError.PASTEBIN_ERROR.format(reason=str(e)))
                return result
            result.elapsed_s = time.monotonic() - start
            for paste in pastes[:MAX_FINDINGS_PER_SOURCE]:
                masked_secrets = [s[-4:] + '****' if len(s) > 4 else '****' for s in paste.extracted_secrets]
                redacted_snippet = _redact_text(paste.context_snippet[:_MAX_CONTEXT_LEN])
                finding_dict = {'uri': paste.uri, 'source_site': paste.source, 'secrets_count': len(paste.extracted_secrets), 'secrets_masked': masked_secrets, 'emails_count': len(paste.emails), 'ip_count': len(paste.ip_addresses), 'context_snippet': redacted_snippet, 'signal_type': 'paste_leak'}
                result.findings.append(finding_dict)
            logger.debug(f'LeakSentinel pastebin: {len(result.findings)} findings, {result.elapsed_s:.1f}s elapsed')
    except Exception as e:
        result.errors.append(LeakSentinelError.PASTEBIN_ADAPTER_ERROR.format(reason=str(e)))
    return result

async def _fetch_github_secret_findings(query: str, semaphore: asyncio.Semaphore) -> LeakSourceResult:
    """
    Bounded adapter for github_secret_scanner.

    Converts SecretFinding objects to dicts with masked secrets.
    Timeout: TIMEOUT_PER_SOURCE seconds.
    Max findings: MAX_FINDINGS_PER_SOURCE.
    """
    result = LeakSourceResult(source='github', findings=[], errors=[])
    try:
        async with semaphore:
            start = time.monotonic()
            try:
                from hledac.universal.recon.github_secret_scanner import SecretFinding, scan_repo
            except ImportError:
                result.errors.append(LeakSentinelError.GITHUB_NOT_AVAILABLE)
                return result
            repo_name = query
            if '/' not in query:
                result.errors.append(LeakSentinelError.GITHUB_FORMAT_ERROR)
                return result
            secrets: list[SecretFinding] = []
            try:
                async with asyncio.timeout(TIMEOUT_PER_SOURCE):
                    secrets = await scan_repo(repo_name)
            except TimeoutError:
                result.errors.append(LeakSentinelError.GITHUB_TIMEOUT)
                return result
            except Exception as e:
                result.errors.append(LeakSentinelError.GITHUB_ERROR.format(reason=str(e)))
                return result
            result.elapsed_s = time.monotonic() - start
            for secret in secrets[:MAX_FINDINGS_PER_SOURCE]:
                finding_dict = {'file_path': secret.file_path, 'line': secret.line, 'pattern': secret.pattern, 'context_masked': secret.masked_context(), 'signal_type': 'github_secret'}
                result.findings.append(finding_dict)
            logger.debug(f'LeakSentinel github: {len(result.findings)} findings, {result.elapsed_s:.1f}s elapsed')
    except Exception as e:
        result.errors.append(LeakSentinelError.GITHUB_ADAPTER_ERROR.format(reason=str(e)))
    return result

async def _fetch_breach_findings(query: str, semaphore: asyncio.Semaphore) -> LeakSourceResult:
    """
    Bounded adapter for data_leak_hunter.

    Converts LeakAlert objects to dicts with redacted PII.
    Timeout: TIMEOUT_PER_SOURCE seconds.
    Max findings: MAX_FINDINGS_PER_SOURCE.

    Note: DataLeakHunter uses long-running monitoring loops, so we
    call check_target() for a single-shot bounded check.
    """
    result = LeakSourceResult(source='breach', findings=[], errors=[])
    try:
        async with semaphore:
            start = time.monotonic()
            try:
                from hledac.universal.recon.data_leak_hunter import BreachAPIConfig, DataLeakHunter
            except ImportError:
                result.errors.append(LeakSentinelError.DATA_LEAK_NOT_AVAILABLE)
                return result
            hunter = DataLeakHunter(api_config=BreachAPIConfig())
            initialized = await hunter.initialize()
            if not initialized:
                result.errors.append(LeakSentinelError.DATA_LEAK_INIT_ERROR)
                return result
            target_type = 'email'
            if '@' not in query:
                if '/' in query:
                    target_type = 'username'
                else:
                    target_type = 'domain'
            alerts = []
            try:
                async with asyncio.timeout(TIMEOUT_PER_SOURCE):
                    alerts = await hunter.check_target(query, target_type)
            except TimeoutError:
                result.errors.append(LeakSentinelError.DATA_LEAK_TIMEOUT)
                return result
            except Exception as e:
                result.errors.append(LeakSentinelError.DATA_LEAK_ERROR.format(reason=str(e)))
                return result
            finally:
                await hunter.cleanup()
            result.elapsed_s = time.monotonic() - start
            for alert in alerts[:MAX_FINDINGS_PER_SOURCE]:
                redacted_data: dict[str, Any] = {}
                raw_data = alert.leaked_data or {}
                for k, v in raw_data.items():
                    if isinstance(v, str):
                        redacted_data[k] = _redact_text(v)
                    else:
                        redacted_data[k] = v
                finding_dict = {'alert_id': alert.alert_id, 'target': _redact_text(alert.target), 'target_type': alert.target_type, 'breach_name': alert.breach_name, 'severity': alert.severity.value, 'source': alert.source.value, 'leaked_data_classes': list(redacted_data.keys()), 'url': alert.url or '', 'signal_type': 'breach_leak'}
                result.findings.append(finding_dict)
            logger.debug(f'LeakSentinel breach: {len(result.findings)} findings, {result.elapsed_s:.1f}s elapsed')
    except Exception as e:
        result.errors.append(LeakSentinelError.BREACH_ADAPTER_ERROR.format(reason=str(e)))
    return result

def _build_evidence_envelope(source: str, evidence_pointers: list[str], signal_facets: dict[str, float], audit_reason: str) -> str:
    """Build JSON evidence envelope for payload_text."""
    envelope = {'audit_reason': audit_reason, 'evidence_pointers': evidence_pointers, 'signal_facets': signal_facets, 'suggested_pivots': _build_pivots(source)}
    try:
        text = _leak_json_dumps(envelope)
        if len(text) > _MAX_ENVELOPE_SIZE:
            signal_facets = dict(list(signal_facets.items())[:5])
            envelope['signal_facets'] = signal_facets
            text = _leak_json_dumps(envelope)
        return text
    except Exception:
        return '{"audit_reason":"serialization_error"}'

def _build_pivots(source: str) -> list[dict]:
    """Build suggested pivots for a finding."""
    if source == 'pastebin':
        return [{'type': 'paste_leak', 'query': 'paste content keywords'}]
    elif source == 'github':
        return [{'type': 'github_secret', 'query': 'repo commits history'}]
    else:
        return [{'type': 'breach_lookup', 'query': 'haveibeenpwned'}]

def _dict_to_canonical(finding: dict, query: str, source_type: str, index: int) -> CanonicalFinding:
    """
    Convert a leak finding dict to a CanonicalFinding.

    Args:
        finding: Source-specific finding dict
        query: Original sprint query
        source_type: SOURCE_TYPE_PASTE | SOURCE_TYPE_GITHUB_SECRET | SOURCE_TYPE_LEAK
        index: Finding index for stable finding_id
    """
    import hashlib
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding
    raw_id = f'{source_type}:{query}:{index}'
    finding_id = hashlib.sha256(raw_id.encode()).hexdigest()[:16]
    pointers = []
    if 'uri' in finding:
        pointers.append(finding['uri'])
    elif 'file_path' in finding:
        pointers.append(f"{finding['file_path']}:{finding.get('line', 0)}")
    elif 'alert_id' in finding:
        pointers.append(finding['alert_id'])
    facets: dict[str, float] = {}
    if 'secrets_count' in finding:
        facets['secrets_count'] = float(finding['secrets_count'])
    if 'emails_count' in finding:
        facets['emails_count'] = float(finding['emails_count'])
    if 'severity' in finding:
        sev_map = {'info': 0.1, 'low': 0.3, 'medium': 0.5, 'high': 0.7, 'critical': 0.9}
        facets['severity_score'] = sev_map.get(finding['severity'], 0.5)
    if 'pattern' in finding:
        facets['pattern_match'] = 0.8
    payload = {'leak_source': finding.get('source_site', finding.get('source', 'unknown')), 'signal_type': finding.get('signal_type', source_type)}
    if 'context_masked' in finding:
        payload['context'] = finding['context_masked']
    elif 'context_snippet' in finding:
        payload['context'] = finding['context_snippet']
    if 'secrets_masked' in finding:
        payload['secrets'] = finding['secrets_masked']
    payload_text = _build_evidence_envelope(source=finding.get('source_site', finding.get('source', 'unknown')), evidence_pointers=pointers, signal_facets=facets, audit_reason=f'LeakSentinel {source_type} finding')
    try:
        full_payload = _leak_json_dumps(payload)
        if len(full_payload) + len(payload_text) < 8000:
            payload_text = payload_text + '|' + full_payload
    except Exception:
        pass
    return CanonicalFinding(finding_id=finding_id, query=query, source_type=source_type, confidence=0.6, ts=time.time(), provenance=('leak_sentinel',), payload_text=payload_text)

class LeakSentinelAdapter:
    """
    Canonical adapter for leak/secret detection in the sprint pipeline.

    Bounded sidecar — runs after CT findings are accepted.
    Does NOT block finding acceptance (fail-soft throughout).

    Usage:
        adapter = LeakSentinelAdapter()
        findings = await adapter.scan(query)
        results = await store.async_ingest_findings_batch(findings)
    """
    __slots__ = tuple(('_semaphore', '_stats'))

    def __init__(self) -> None:
        self._stats = LeakSentinelStats()
        from hledac.universal.core.concurrency import ConcurrencyCategory, get_semaphore
        self._semaphore = get_semaphore(ConcurrencyCategory.SCRAPE_GENERAL)

    def get_stats(self) -> LeakSentinelStats:
        """Return statistics from the last run."""
        return self._stats

    async def scan(self, query: str) -> list[CanonicalFinding]:
        """
        Run bounded leak scans across all available sources.

        Args:
            query: Sprint query (domain, email, username, or 'owner/repo')

        Returns:
            List of CanonicalFinding (redacted, bounded to MAX_TOTAL_FINDINGS)
        """
        self._stats = LeakSentinelStats()
        start = time.monotonic()
        if not query or len(query) < 2:
            return []
        sources_to_run: list[tuple[str, asyncio.Task]] = []
        t = safe_create_task(_fetch_paste_findings(query, self._semaphore), name='leak_sentinel:paste_findings')
        sources_to_run.append(('pastebin', t))
        if '/' in query and len(query) > 3:
            t = safe_create_task(_fetch_github_secret_findings(query, self._semaphore), name='leak_sentinel:github_findings')
            sources_to_run.append(('github', t))
        if '@' in query or ('.' in query and '/' not in query):
            t = safe_create_task(_fetch_breach_findings(query, self._semaphore), name='leak_sentinel:breach_findings')
            sources_to_run.append(('breach', t))
        self._stats.sources_run = len(sources_to_run)
        try:
            async with asyncio.timeout(TIMEOUT_PER_SOURCE * 2):
                await safe_gather_fire_and_forget(*[t for _, t in sources_to_run], label='leak_sentinel:546')
        except TimeoutError:
            for _, t in sources_to_run:
                if not t.done():
                    t.cancel()
            self._stats.errors.append('overall timeout — partial results may be missing')
            results = []
        all_findings: list[CanonicalFinding] = []
        source_type_map = {'pastebin': SOURCE_TYPE_PASTE, 'github': SOURCE_TYPE_GITHUB_SECRET, 'breach': SOURCE_TYPE_LEAK}
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                src_name = sources_to_run[i][0] if i < len(sources_to_run) else 'unknown'
                self._stats.errors.append(f'{src_name} exception: {result}')
                continue
            if not isinstance(result, LeakSourceResult):
                continue
            self._stats.sources_succeeded += 1
            if result.errors:
                self._stats.errors.extend(result.errors)
            src_type = source_type_map.get(result.source, SOURCE_TYPE_LEAK)
            for j, finding_dict in enumerate(result.findings):
                canonical = _dict_to_canonical(finding_dict, query, src_type, j)
                all_findings.append(canonical)
        if len(all_findings) > MAX_TOTAL_FINDINGS:
            all_findings = all_findings[:MAX_TOTAL_FINDINGS]
        self._stats.findings_produced = len(all_findings)
        self._stats.elapsed_s = time.monotonic() - start
        logger.debug(f'LeakSentinel: {self._stats.findings_produced} findings from {self._stats.sources_succeeded}/{self._stats.sources_run} sources in {self._stats.elapsed_s:.1f}s')
        return all_findings

def create_leak_sentinel_adapter() -> LeakSentinelAdapter:
    """Create a LeakSentinelAdapter instance."""
    return LeakSentinelAdapter()