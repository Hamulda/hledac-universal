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

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding

logger = logging.getLogger(__name__)

# ── Bounds ────────────────────────────────────────────────────────────────────

MAX_LEAK_SOURCES: int = 3
MAX_FINDINGS_PER_SOURCE: int = 50
MAX_TOTAL_FINDINGS: int = 100
TIMEOUT_PER_SOURCE: float = 30.0

# Source type identifiers
SOURCE_TYPE_LEAK = "leak_sentinel"
SOURCE_TYPE_PASTE = "paste_leak"
SOURCE_TYPE_GITHUB_SECRET = "github_secret"

# ── Evidence envelope constants ────────────────────────────────────────────────

_MAX_ENVELOPE_SIZE: int = 4096
_MAX_CONTEXT_LEN: int = 200


# ── PII redaction ─────────────────────────────────────────────────────────────

_SECRET_PATTERNS: list[tuple[str, str]] = [
    # AWS keys: AKIA... (20 chars)
    (r'\bAKIA[0-9A-Z]{16}\b', 'AKIA[REDACTED]'),
    # Stripe live keys
    (r'\bsk_live_[0-9a-zA-Z]{24}\b', 'sk_live_[REDACTED]'),
    # Bearer token in Authorization header (full line replacement)
    (r'Bearer\s+[A-Za-z0-9_\.\-]{20,}', 'Bearer [REDACTED]'),
    # Private key headers
    (r'-----BEGIN[^\n]+-----', '[REDACTED:PRIVATE KEY]'),
    # Generic secret assignments (api_key=, password=, secret=, token=)
    # Use word boundary + optional quotes around value
    (r'(?i)(?:api[_-]?key|secret|password|passwd|token)\s*[=:]\s*["\']?[A-Za-z0-9_\.\-]{8,32}["\']?',
     '[REDACTED:CREDENTIAL]'),
    # Google API keys
    (r'\bAIza[0-9A-Za-z\-_]{35}\b', 'AIza[REDACTED]'),
]


def _redact_text(text: str) -> str:
    """Redact PII and secrets from text.

    Secret patterns are applied FIRST (before fallback_sanitize) to prevent
    partial masking by PII patterns. Then fallback_sanitize handles standard PII.
    """
    import re

    result = text

    # 1. Apply secret patterns first (before PII sanitization)
    for pat, repl in _SECRET_PATTERNS:
        result = re.sub(pat, repl, result)

    # 2. Apply fallback_sanitize for standard PII (email, phone, SSN, etc.)
    try:
        from hledac.universal.security.pii_gate import fallback_sanitize
        result = fallback_sanitize(result)
    except Exception:
        pass

    return result


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class LeakSourceResult:
    """Result from one leak source."""
    source: str           # "pastebin" | "github" | "breach"
    findings: list[dict]   # raw findings (already redacted)
    errors: list[str]     # error messages (fail-soft)
    elapsed_s: float = 0.0


@dataclass
class LeakSentinelStats:
    """Statistics from a leak sentinel run."""
    sources_run: int = 0
    sources_succeeded: int = 0
    findings_produced: int = 0
    findings_stored: int = 0
    elapsed_s: float = 0.0
    errors: list[str] = field(default_factory=list)


# ── Bounded adapters for each source ─────────────────────────────────────────

async def _fetch_paste_findings(
    query: str,
    semaphore: asyncio.Semaphore,
) -> LeakSourceResult:
    """
    Bounded adapter for pastebin_monitor.

    Converts PasteFinding objects to dicts with redacted secrets.
    Timeout: TIMEOUT_PER_SOURCE seconds.
    Max findings: MAX_FINDINGS_PER_SOURCE.
    """
    import json
    result = LeakSourceResult(source="pastebin", findings=[], errors=[])

    try:
        async with semaphore:
            start = time.monotonic()
            # Import here to avoid circular dependencies and enable fail-soft
            try:
                from hledac.universal.intelligence.pastebin_monitor import (
                    PasteFinding,
                    run as run_pastebin,
                )
            except ImportError:
                result.errors.append("pastebin_monitor not available")
                return result

            # Run with timeout
            pastes: list[PasteFinding] = []
            try:
                pastes = await asyncio.wait_for(
                    run_pastebin(query),
                    timeout=TIMEOUT_PER_SOURCE,
                )
            except asyncio.TimeoutError:
                result.errors.append("pastebin_monitor timeout")
                return result
            except Exception as e:
                result.errors.append(f"pastebin_monitor error: {e}")
                return result

            result.elapsed_s = time.monotonic() - start

            for paste in pastes[:MAX_FINDINGS_PER_SOURCE]:
                # Mask secrets in extracted_secrets
                masked_secrets = [
                    s[-4:] + "****" if len(s) > 4 else "****"
                    for s in paste.extracted_secrets
                ]
                # Redact context_snippet via pii_gate
                redacted_snippet = _redact_text(paste.context_snippet[:_MAX_CONTEXT_LEN])

                finding_dict = {
                    "uri": paste.uri,
                    "source_site": paste.source,
                    "secrets_count": len(paste.extracted_secrets),
                    "secrets_masked": masked_secrets,
                    "emails_count": len(paste.emails),
                    "ip_count": len(paste.ip_addresses),
                    "context_snippet": redacted_snippet,
                    "signal_type": "paste_leak",
                }
                result.findings.append(finding_dict)

            logger.debug(
                f"LeakSentinel pastebin: {len(result.findings)} findings, "
                f"{result.elapsed_s:.1f}s elapsed"
            )

    except Exception as e:
        result.errors.append(f"pastebin adapter error: {e}")

    return result


async def _fetch_github_secret_findings(
    query: str,
    semaphore: asyncio.Semaphore,
) -> LeakSourceResult:
    """
    Bounded adapter for github_secret_scanner.

    Converts SecretFinding objects to dicts with masked secrets.
    Timeout: TIMEOUT_PER_SOURCE seconds.
    Max findings: MAX_FINDINGS_PER_SOURCE.
    """
    result = LeakSourceResult(source="github", findings=[], errors=[])

    try:
        async with semaphore:
            start = time.monotonic()
            try:
                from hledac.universal.intelligence.github_secret_scanner import (
                    SecretFinding,
                    scan_repo,
                )
            except ImportError:
                result.errors.append("github_secret_scanner not available")
                return result

            # Parse query as repo name if it looks like one
            repo_name = query
            if "/" not in query:
                # Query is not a repo — skip GitHub scan
                result.errors.append("github scan requires 'owner/repo' format")
                return result

            secrets: list[SecretFinding] = []
            try:
                secrets = await asyncio.wait_for(
                    scan_repo(repo_name),
                    timeout=TIMEOUT_PER_SOURCE,
                )
            except asyncio.TimeoutError:
                result.errors.append("github_secret_scanner timeout")
                return result
            except Exception as e:
                result.errors.append(f"github_secret_scanner error: {e}")
                return result

            result.elapsed_s = time.monotonic() - start

            for secret in secrets[:MAX_FINDINGS_PER_SOURCE]:
                # context is already masked by _mask_secret in the scanner
                finding_dict = {
                    "file_path": secret.file_path,
                    "line": secret.line,
                    "pattern": secret.pattern,
                    "context_masked": secret.masked_context(),
                    "signal_type": "github_secret",
                }
                result.findings.append(finding_dict)

            logger.debug(
                f"LeakSentinel github: {len(result.findings)} findings, "
                f"{result.elapsed_s:.1f}s elapsed"
            )

    except Exception as e:
        result.errors.append(f"github adapter error: {e}")

    return result


async def _fetch_breach_findings(
    query: str,
    semaphore: asyncio.Semaphore,
) -> LeakSourceResult:
    """
    Bounded adapter for data_leak_hunter.

    Converts LeakAlert objects to dicts with redacted PII.
    Timeout: TIMEOUT_PER_SOURCE seconds.
    Max findings: MAX_FINDINGS_PER_SOURCE.

    Note: DataLeakHunter uses long-running monitoring loops, so we
    call check_target() for a single-shot bounded check.
    """
    result = LeakSourceResult(source="breach", findings=[], errors=[])

    try:
        async with semaphore:
            start = time.monotonic()
            try:
                from hledac.universal.intelligence.data_leak_hunter import (
                    DataLeakHunter,
                    BreachAPIConfig,
                )
            except ImportError:
                result.errors.append("data_leak_hunter not available")
                return result

            hunter = DataLeakHunter(api_config=BreachAPIConfig())
            initialized = await hunter.initialize()
            if not initialized:
                result.errors.append("data_leak_hunter init failed")
                return result

            # Determine target type from query
            target_type = "email"
            if "@" not in query:
                if "/" in query:
                    target_type = "username"
                else:
                    target_type = "domain"

            alerts = []
            try:
                alerts = await asyncio.wait_for(
                    hunter.check_target(query, target_type),
                    timeout=TIMEOUT_PER_SOURCE,
                )
            except asyncio.TimeoutError:
                result.errors.append("data_leak_hunter timeout")
                return result
            except Exception as e:
                result.errors.append(f"data_leak_hunter error: {e}")
                return result
            finally:
                await hunter.cleanup()

            result.elapsed_s = time.monotonic() - start

            for alert in alerts[:MAX_FINDINGS_PER_SOURCE]:
                # Redact any leaked_data values that might contain secrets
                redacted_data: Dict[str, Any] = {}
                raw_data = alert.leaked_data or {}
                for k, v in raw_data.items():
                    if isinstance(v, str):
                        redacted_data[k] = _redact_text(v)
                    else:
                        redacted_data[k] = v

                finding_dict = {
                    "alert_id": alert.alert_id,
                    "target": _redact_text(alert.target),
                    "target_type": alert.target_type,
                    "breach_name": alert.breach_name,
                    "severity": alert.severity.value,
                    "source": alert.source.value,
                    "leaked_data_classes": list(redacted_data.keys()),
                    "url": alert.url or "",
                    "signal_type": "breach_leak",
                }
                result.findings.append(finding_dict)

            logger.debug(
                f"LeakSentinel breach: {len(result.findings)} findings, "
                f"{result.elapsed_s:.1f}s elapsed"
            )

    except Exception as e:
        result.errors.append(f"breach adapter error: {e}")

    return result


# ── Conversion to CanonicalFinding ────────────────────────────────────────────

def _build_evidence_envelope(
    source: str,
    evidence_pointers: list[str],
    signal_facets: dict[str, float],
    audit_reason: str,
) -> str:
    """Build JSON evidence envelope for payload_text."""
    import json
    envelope = {
        "audit_reason": audit_reason,
        "evidence_pointers": evidence_pointers,
        "signal_facets": signal_facets,
        "suggested_pivots": _build_pivots(source),
    }
    try:
        text = json.dumps(envelope, separators=(",", ":"))
        if len(text) > _MAX_ENVELOPE_SIZE:
            # Truncate signal_facets if needed
            signal_facets = {k: v for k, v in list(signal_facets.items())[:5]}
            envelope["signal_facets"] = signal_facets
            text = json.dumps(envelope, separators=(",", ":"))
        return text
    except Exception:
        return '{"audit_reason":"serialization_error"}'


def _build_pivots(source: str) -> list[dict]:
    """Build suggested pivots for a finding."""
    if source == "pastebin":
        return [{"type": "paste_leak", "query": "paste content keywords"}]
    elif source == "github":
        return [{"type": "github_secret", "query": "repo commits history"}]
    else:
        return [{"type": "breach_lookup", "query": "haveibeenpwned"}]


def _dict_to_canonical(
    finding: dict,
    query: str,
    source_type: str,
    index: int,
) -> "CanonicalFinding":
    """
    Convert a leak finding dict to a CanonicalFinding.

    Args:
        finding: Source-specific finding dict
        query: Original sprint query
        source_type: SOURCE_TYPE_PASTE | SOURCE_TYPE_GITHUB_SECRET | SOURCE_TYPE_LEAK
        index: Finding index for stable finding_id
    """
    import hashlib
    import json
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding

    # Build finding_id from source + query hash
    raw_id = f"{source_type}:{query}:{index}"
    finding_id = hashlib.sha256(raw_id.encode()).hexdigest()[:16]

    # Build evidence pointers
    pointers = []
    if "uri" in finding:
        pointers.append(finding["uri"])
    elif "file_path" in finding:
        pointers.append(f"{finding['file_path']}:{finding.get('line', 0)}")
    elif "alert_id" in finding:
        pointers.append(finding["alert_id"])

    # Build signal facets
    facets: Dict[str, float] = {}
    if "secrets_count" in finding:
        facets["secrets_count"] = float(finding["secrets_count"])
    if "emails_count" in finding:
        facets["emails_count"] = float(finding["emails_count"])
    if "severity" in finding:
        sev_map = {"info": 0.1, "low": 0.3, "medium": 0.5, "high": 0.7, "critical": 0.9}
        facets["severity_score"] = sev_map.get(finding["severity"], 0.5)
    if "pattern" in finding:
        facets["pattern_match"] = 0.8

    # Build payload
    payload = {
        "leak_source": finding.get("source_site", finding.get("source", "unknown")),
        "signal_type": finding.get("signal_type", source_type),
    }
    if "context_masked" in finding:
        payload["context"] = finding["context_masked"]
    elif "context_snippet" in finding:
        payload["context"] = finding["context_snippet"]
    if "secrets_masked" in finding:
        payload["secrets"] = finding["secrets_masked"]

    payload_text = _build_evidence_envelope(
        source=finding.get("source_site", finding.get("source", "unknown")),
        evidence_pointers=pointers,
        signal_facets=facets,
        audit_reason=f"LeakSentinel {source_type} finding",
    )

    # Serialize full payload alongside envelope
    try:
        full_payload = json.dumps(payload, separators=(",", ":"))
        if len(full_payload) + len(payload_text) < 8000:
            payload_text = payload_text + "|" + full_payload
    except Exception:
        pass

    return CanonicalFinding(
        finding_id=finding_id,
        query=query,
        source_type=source_type,
        confidence=0.6,  # Leaked data has inherent uncertainty
        ts=time.time(),
        provenance=("leak_sentinel",),
        payload_text=payload_text,
    )


# ── Main coordinator ───────────────────────────────────────────────────────────

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

    def __init__(self) -> None:
        self._stats = LeakSentinelStats()
        self._semaphore = asyncio.Semaphore(MAX_LEAK_SOURCES)

    def get_stats(self) -> LeakSentinelStats:
        """Return statistics from the last run."""
        return self._stats

    async def scan(self, query: str) -> list["CanonicalFinding"]:
        """
        Run bounded leak scans across all available sources.

        Args:
            query: Sprint query (domain, email, username, or 'owner/repo')

        Returns:
            List of CanonicalFinding (redacted, bounded to MAX_TOTAL_FINDINGS)
        """
        import json
        from hledac.universal.knowledge.duckdb_store import CanonicalFinding

        self._stats = LeakSentinelStats()
        start = time.monotonic()

        if not query or len(query) < 2:
            return []

        # Determine which sources to run based on query format
        sources_to_run: list[tuple[str, asyncio.Task]] = []

        # Always try pastebin (works with any query)
        t = asyncio.create_task(_fetch_paste_findings(query, self._semaphore))
        sources_to_run.append(("pastebin", t))

        # Try github if query looks like 'owner/repo'
        if "/" in query and len(query) > 3:
            t = asyncio.create_task(_fetch_github_secret_findings(query, self._semaphore))
            sources_to_run.append(("github", t))

        # Try breach for email/domain/username queries
        if "@" in query or ("." in query and "/" not in query):
            t = asyncio.create_task(_fetch_breach_findings(query, self._semaphore))
            sources_to_run.append(("breach", t))

        self._stats.sources_run = len(sources_to_run)

        # Wait for all sources with timeout
        try:
            results: list[LeakSourceResult] = await asyncio.wait_for(
                asyncio.gather(*[t for _, t in sources_to_run], return_exceptions=True),
                timeout=TIMEOUT_PER_SOURCE * 2,
            )
        except asyncio.TimeoutError:
            # Cancel pending tasks
            for _, t in sources_to_run:
                if not t.done():
                    t.cancel()
            self._stats.errors.append("overall timeout — partial results may be missing")
            results = []

        # Process results
        all_findings: list[CanonicalFinding] = []
        source_type_map = {
            "pastebin": SOURCE_TYPE_PASTE,
            "github": SOURCE_TYPE_GITHUB_SECRET,
            "breach": SOURCE_TYPE_LEAK,
        }

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                src_name = sources_to_run[i][0] if i < len(sources_to_run) else "unknown"
                self._stats.errors.append(f"{src_name} exception: {result}")
                continue

            if not isinstance(result, LeakSourceResult):
                continue

            self._stats.sources_succeeded += 1
            if result.errors:
                self._stats.errors.extend(result.errors)

            # Determine source_type for these findings
            src_type = source_type_map.get(result.source, SOURCE_TYPE_LEAK)

            for j, finding_dict in enumerate(result.findings):
                canonical = _dict_to_canonical(finding_dict, query, src_type, j)
                all_findings.append(canonical)

        # Cap at MAX_TOTAL_FINDINGS
        if len(all_findings) > MAX_TOTAL_FINDINGS:
            all_findings = all_findings[:MAX_TOTAL_FINDINGS]

        self._stats.findings_produced = len(all_findings)
        self._stats.elapsed_s = time.monotonic() - start

        logger.debug(
            f"LeakSentinel: {self._stats.findings_produced} findings from "
            f"{self._stats.sources_succeeded}/{self._stats.sources_run} sources "
            f"in {self._stats.elapsed_s:.1f}s"
        )

        return all_findings


# ── Factory ───────────────────────────────────────────────────────────────────

def create_leak_sentinel_adapter() -> LeakSentinelAdapter:
    """Create a LeakSentinelAdapter instance."""
    return LeakSentinelAdapter()
