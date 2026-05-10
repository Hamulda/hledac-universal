"""
intelligence/social_identity_miner.py — F204I: Social Identity Surface Miner
============================================================================

Deterministic social identity facet miner. Extracts usernames, display names,
profile URLs, bio links, PGP/email hints from accepted findings without
invasive scraping.

GHOST_INVARIANTS enforced:
- asyncio.gather always with return_exceptions=True
- gather(return_exceptions=True) results are filtered inline and CancelledError is re-raised
- asyncio.CancelledError re-raised
- No blocking calls in event loop
- Canonical write path: async_ingest_findings_batch()
- Model lifecycle: NOT USED
- RAM guard: skip if RSS > high_water
- Bounds: MAX_SOCIAL_PROFILES, MAX_LINKS_PER_PROFILE, MAX_SOCIAL_TEXT_BYTES
- Fail-soft: malformed HTML/payload silently skipped
"""

from __future__ import annotations

import asyncio
import json
import re
import time as _time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from typing import TYPE_CHECKING

from .confidence_policy import compute_confidence as _compute_confidence

if TYPE_CHECKING:
    from ..knowledge.duckdb_store import DuckDBShadowStore
    from ..project_types import CanonicalFinding

# ── Bounds ────────────────────────────────────────────────────────────────────
MAX_SOCIAL_PROFILES: int = 200
MAX_LINKS_PER_PROFILE: int = 20
MAX_SOCIAL_TEXT_BYTES: int = 4096
SOCIAL_MIN_CONFIDENCE: float = 0.35

# Platform patterns: (platform_name, url_pattern_regex, username_extract_regex, is_invite_only)
# is_invite_only: True = discord-style invite links that are not person identities
_PLATFORM_PATTERNS: list[tuple[str, re.Pattern[str], re.Pattern[str], bool]] = [
    (
        "github",
        re.compile(r"https?://(?:www\.)?github\.com/([^/]+)?"),
        re.compile(r"(?:github\.com/|@)([a-zA-Z0-9][a-zA-Z0-9_-]{0,38})"),
        False,
    ),
    (
        "twitter",
        re.compile(r"https?://(?:www\.)?(?:twitter\.com|x\.com)/([^/]+)?"),
        re.compile(r"(?:twitter\.com/|@)([a-zA-Z0-9_]{1,15})"),
        False,
    ),
    (
        "linkedin",
        re.compile(r"https?://(?:www\.)?linkedin\.com/in/([^/]+)?"),
        re.compile(r"linkedin\.com/in/([a-zA-Z0-9_-]{3,100})"),
        False,
    ),
    (
        "mastodon",
        re.compile(r"https?://(?:www\.)?mastodon\.social/@([^/]+)?"),
        re.compile(r"@(?:[a-zA-Z0-9_]+@)?([a-zA-Z0-9_]{1,30})"),
        False,
    ),
    (
        "keybase",
        re.compile(r"https?://(?:www\.)?keybase\.io/([^/]+)?"),
        re.compile(r"(?:keybase\.io/|@)([a-zA-Z0-9][a-zA-Z0-9_-]{0,38})"),
        False,
    ),
    (
        "gitlab",
        re.compile(r"https?://(?:www\.)?gitlab\.com/([^/]+)?"),
        re.compile(r"(?:gitlab\.com/|@)([a-zA-Z0-9][a-zA-Z0-9_-]{0,38})"),
        False,
    ),
    (
        "hackernews",
        re.compile(r"https?://news\.ycombinator\.com/user\?id=([^&]+)?"),
        re.compile(r"(?:news\.ycombinator\.com/user\?id=|@)([a-zA-Z0-9_-]{1,30})"),
        False,
    ),
    (
        "reddit",
        re.compile(r"https?://(?:www\.)?reddit\.com/user/([^/]+)?"),
        re.compile(r"(?:reddit\.com/user/|u/)([a-zA-Z0-9_-]{3,20})"),
        False,
    ),
    (
        "youtube",
        re.compile(r"https?://(?:www\.)?youtube\.com/@([^/]+)?"),
        re.compile(r"(?:youtube\.com/@|@)([a-zA-Z0-9_-]{3,30})"),
        False,
    ),
    (
        "facebook",
        re.compile(r"https?://(?:www\.)?facebook\.com/([^/]+)?"),
        re.compile(r"(?:facebook\.com/|@)([a-zA-Z0-9\.]{5,50})"),
        False,
    ),
    # R7: new platforms
    (
        "telegram",
        re.compile(r"https?://(?:www\.)?(?:t\.me|telegram\.me)/([^/]+)?"),
        re.compile(r"(?:t\.me|telegram\.me)/([a-zA-Z0-9_-]{3,50})"),
        False,
    ),
    (
        "matrix",
        re.compile(r"https?://(?:www\.)?matrix\.to/#[^/]+/?$"),
        re.compile(r"matrix\.to/#@?([^/]+)"),
        False,
    ),
    (
        "medium",
        re.compile(r"https?://(?:www\.)?medium\.com/@([^/]+)?"),
        re.compile(r"medium\.com/@([a-zA-Z0-9_-]{3,50})"),
        False,
    ),
    (
        "substack",
        re.compile(r"https?://(?:www\.)?([a-zA-Z0-9][a-zA-Z0-9_-]{0,48})\.substack\.com/?"),
        re.compile(r"substack\.com/@([a-zA-Z0-9_-]{3,50})"),
        False,
    ),
    (
        "npmjs",
        re.compile(r"https?://(?:www\.)?npmjs\.com/~([^/]+)?"),
        re.compile(r"npmjs\.com/~([a-zA-Z0-9_-]{3,50})"),
        False,
    ),
    (
        "pypi",
        re.compile(r"https?://(?:www\.)?pypi\.org/user/([^/]+)?"),
        re.compile(r"pypi\.org/user/([a-zA-Z0-9_-]{3,50})"),
        False,
    ),
    (
        "huggingface",
        re.compile(r"https?://(?:www\.)?huggingface\.co/([^/]+)?"),
        re.compile(r"huggingface\.co/([a-zA-Z0-9_-]{3,50})"),
        False,
    ),
    (
        "github_gist",
        re.compile(r"https?://(?:www\.)?gist\.github\.com/([^/]+)?"),
        re.compile(r"gist\.github\.com/([a-zA-Z0-9_-]{3,50})"),
        False,
    ),
    # Self-hosted GitLab instances (path-based, e.g. /u/admin)
    (
        "gitlab_selfhosted",
        re.compile(r"https?://[^/]+/u/([^/]+)?"),
        re.compile(r"/u/([a-zA-Z0-9][a-zA-Z0-9_-]{0,38})"),
        False,
    ),
    # Discord invite — classified as invite NOT person identity
    (
        "discord_invite",
        re.compile(r"https?://(?:www\.)?discord(?:(?:app)?\.com/invite|\.gg)/([^/]+)?"),
        re.compile(r"discord(?:app)?\.com/invite/([a-zA-Z0-9_-]{3,20})"),
        True,
    ),
]

# Bio link patterns (domain mentions in text)
_BIO_LINK_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?:https?://)?(?:www\.)?([a-zA-Z0-9-]+\.[a-zA-Z]{2,})/[~@]?[a-zA-Z0-9_-]+", re.IGNORECASE),
    re.compile(r"@([a-zA-Z0-9_-]{1,30})\.(?:io|dev|com|org|net)", re.IGNORECASE),
]

# Email patterns in text
_EMAIL_PATTERNS: re.Pattern[str] = re.compile(
    r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
)

# PGP fingerprint patterns — defined for future use (PGP extraction not yet wired)
_PGP_PATTERNS: re.Pattern[str] = re.compile(
    r"\b(?:PGP|GPG)[:\s]*(?:0x)?([A-F0-9]{8,40})\b",
    re.IGNORECASE,
)


# ── Dataclasses ────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class SocialIdentityFacet:
    """A single social identity profile extracted from findings."""

    finding_id: str
    platform: str
    username: str
    display_name: str
    profile_url: str
    linked_domains: tuple[str, ...]
    linked_emails: tuple[str, ...]
    confidence: float
    # R7: evidence_kind tracks how this facet was derived
    evidence_kind: str = "url_in_payload"  # url_in_payload | ioc_value | provenance | text_pattern


@dataclass(frozen=True)
class SocialIdentityResult:
    """Outcome of a social identity mining scan."""

    facets: tuple[SocialIdentityFacet, ...]
    scanned_count: int
    skipped_count: int
    elapsed_ms: float


def _is_url(text: str) -> bool:
    """Check if text looks like a URL."""
    if not text or len(text) > 200:
        return False
    return bool(re.match(r"https?://", text, re.IGNORECASE))



class SocialIdentityMiner:
    """
    Deterministic social identity facet miner.

    Extracts social profile facets (GitHub, Twitter, LinkedIn, etc.) from
    accepted findings by scanning URLs, text content, and bio links.
    No invasive scraping — only surface-level extraction from existing data.

    Fail-soft: malformed input silently skipped, partial results returned.
    """

    __slots__ = ("_seen_profiles", "_semaphore", "_stats")

    def __init__(self) -> None:
        self._seen_profiles: dict[str, str] = {}  # url -> finding_id (dedup)
        self._semaphore: asyncio.Semaphore = asyncio.Semaphore(4)
        self._stats: dict[str, int] = {
            "scanned": 0,
            "skipped": 0,
            "facets_found": 0,
        }

    def reset(self) -> None:
        """Reset state between sprints."""
        self._seen_profiles.clear()
        self._stats = {"scanned": 0, "skipped": 0, "facets_found": 0}

    # ── Public API ─────────────────────────────────────────────────────────────

    async def mine(
        self,
        findings: list[Any],
        store: Any,
        query: str,
    ) -> SocialIdentityResult:
        """
        Scan accepted findings for social identity facets.

        Args:
            findings: Accepted CanonicalFinding list from sprint
            store: DuckDBShadowStore for canonical write
            query: Sprint query (used for context)

        Returns:
            SocialIdentityResult with extracted facets and stats
        """
        start_ms = _time.monotonic() * 1000
        facets: list[SocialIdentityFacet] = []

        # RAM guard: skip if high pressure
        try:
            from ..utils.uma_budget import get_uma_snapshot
            snap = get_uma_snapshot()
            if snap.get("high_water") and snap.get("rss_mb", 0) > snap["high_water"] * 0.85:
                return SocialIdentityResult(
                    facets=(),
                    scanned_count=0,
                    skipped_count=len(findings),
                    elapsed_ms=(_time.monotonic() * 1000 - start_ms),
                )
        except Exception:
            pass  # fail-soft: continue without RAM guard

        # Collect URLs from all findings
        all_urls: list[tuple[str, str, str, str]] = []  # (url, finding_id, text_sample, evidence_kind)
        for finding in findings:
            if len(all_urls) >= MAX_SOCIAL_PROFILES:
                break
            self._stats["scanned"] += 1

            # Extract from payload_text
            urls_from_payload = self._extract_urls_from_payload(finding)
            for url in urls_from_payload[:MAX_LINKS_PER_PROFILE]:
                all_urls.append((url, getattr(finding, "finding_id", "unknown"), "", "url_in_payload"))

            # Extract from ioc_value (often a URL or domain)
            ioc_val = getattr(finding, "ioc_value", "")
            if ioc_val and isinstance(ioc_val, str) and len(ioc_val) < 2048:
                if _is_url(ioc_val):
                    all_urls.append((ioc_val, getattr(finding, "finding_id", "unknown"), "", "ioc_value"))

            # Extract from source_type or other text fields
            source_type = getattr(finding, "source_type", "")
            if source_type in ("ct", "certificate_transparency"):
                # Parse certificate sanitized domains
                domains = self._extract_domains_from_cert_text(getattr(finding, "payload_text", "") or "")
                for domain in domains[:5]:
                    all_urls.append((f"https://{domain}", getattr(finding, "finding_id", "unknown"), "", "provenance"))

        self._stats["scanned"] = len(findings)

        if not all_urls:
            return SocialIdentityResult(
                facets=(),
                scanned_count=self._stats["scanned"],
                skipped_count=self._stats["skipped"],
                elapsed_ms=_time.monotonic() * 1000 - start_ms,
            )

        # Process URLs concurrently (bounded)
        tasks = [
            self._process_url(url, finding_id, text_sample, evidence_kind)
            for url, finding_id, text_sample, evidence_kind in all_urls
        ]

        gathered: list[Any] = []
        try:
            gathered = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=30.0,
            )
        except asyncio.TimeoutError:
            for t in tasks:
                try:
                    t.close()
                except Exception:
                    pass
            gathered = []

        # Collect valid facets — re-raise CancelledError per GHOST_INVARIANTS
        for result in gathered:
            if isinstance(result, asyncio.CancelledError):
                raise result
            if isinstance(result, Exception):
                self._stats["skipped"] += 1
                continue
            if isinstance(result, SocialIdentityFacet):
                facets.append(result)
                self._stats["facets_found"] += 1

        # Deduplicate by profile URL
        unique_facets = self._deduplicate_facets(facets)

        # Canonical write
        if unique_facets:
            await self._write_findings(unique_facets, store, query)

        return SocialIdentityResult(
            facets=tuple(unique_facets),
            scanned_count=self._stats["scanned"],
            skipped_count=self._stats["skipped"],
            elapsed_ms=_time.monotonic() * 1000 - start_ms,
        )

    # ── URL Processing ─────────────────────────────────────────────────────────

    async def _process_url(
        self,
        url: str,
        finding_id: str,
        text_sample: str,
        source: str = "url_in_payload",  # R7: evidence_kind source
    ) -> SocialIdentityFacet | None:
        """Extract social identity from a single URL."""
        async with self._semaphore:
            try:
                # Parse URL
                parsed = urlparse(url)
                path = parsed.path.strip("/")

                # Check against known platform patterns
                for platform, url_re, username_re, is_invite_only in _PLATFORM_PATTERNS:
                    url_match = url_re.match(url)
                    if not url_match:
                        # Try host + path matching
                        host_match = re.match(
                            r"https?://(?:www\.)?" + re.escape(parsed.netloc) + r"/?",
                            url,
                        )
                        if host_match and platform in parsed.netloc:
                            url_match = True

                    if not url_match and platform not in parsed.netloc:
                        continue

                    # R7: discord_invite is NOT a person identity — skip
                    if is_invite_only:
                        return None

                    # Extract username
                    username = ""
                    if path:
                        username = path.split("/")[0]
                        if username_re.search(url):
                            m = username_re.search(url)
                            if m and m.group(1):
                                username = m.group(1)

                    if not username or len(username) < 2:
                        continue

                    # Build profile URL
                    profile_url = self._build_profile_url(platform, username, parsed.netloc)

                    # Linked domains/emails from text_sample
                    linked_domains = self._extract_linked_domains(text_sample)
                    linked_emails = self._extract_linked_emails(text_sample)

                    # Confidence scoring
                    confidence = self._compute_confidence(platform, username, linked_domains, linked_emails)

                    if confidence < SOCIAL_MIN_CONFIDENCE:
                        continue

                    return SocialIdentityFacet(
                        finding_id=finding_id,
                        platform=platform,
                        username=username,
                        display_name=username,  # display name unknown without scraping
                        profile_url=profile_url,
                        linked_domains=tuple(linked_domains),
                        linked_emails=tuple(linked_emails),
                        confidence=confidence,
                        evidence_kind=source,
                    )

                return None

            except Exception:
                return None

    # ── Extraction Helpers ─────────────────────────────────────────────────────

    def _extract_urls_from_payload(self, finding: Any) -> list[str]:
        """Extract URLs from finding payload_text."""
        urls: list[str] = []
        try:
            payload = getattr(finding, "payload_text", "") or ""
            if not payload:
                return []

            # Try JSON envelope
            try:
                env = json.loads(payload)
                for key in ("urls", "links", "extracted_urls", "url_list"):
                    if key in env and isinstance(env[key], list):
                        urls.extend(str(u) for u in env[key] if isinstance(u, str))
                # Also scan raw text in envelope
                if "raw_text" in env:
                    urls.extend(self._scan_text_for_urls(env["raw_text"]))
                elif "text" in env:
                    urls.extend(self._scan_text_for_urls(env["text"]))
            except (json.JSONDecodeError, TypeError):
                # Plain text — scan for URLs
                urls.extend(self._scan_text_for_urls(payload))

            # Also check raw str representation
            finding_str = str(finding)
            urls.extend(self._scan_text_for_urls(finding_str))

        except Exception:
            pass

        return urls[:MAX_LINKS_PER_PROFILE]

    def _scan_text_for_urls(self, text: str) -> list[str]:
        """Scan text for URL patterns."""
        if not text or len(text) > MAX_SOCIAL_TEXT_BYTES:
            return []

        urls: list[str] = []
        # HTTP(S) URLs
        url_re = re.compile(
            r"https?://[a-zA-Z0-9][a-zA-Z0-9-]*(?:\.[a-zA-Z]{2,})+(?:/[^\s<>\"')\]]*)?",
            re.IGNORECASE,
        )
        for m in url_re.finditer(text):
            url = m.group(0)
            if len(url) < 200:  # Sanity bound
                urls.append(url)

        return urls[:MAX_LINKS_PER_PROFILE]

    def _extract_domains_from_cert_text(self, text: str) -> list[str]:
        """Extract domains from certificate transparency text."""
        if not text:
            return []
        # Common domain extraction patterns in CT data
        domain_re = re.compile(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?\.(?:[a-zA-Z]{2,})")
        domains = []
        for m in domain_re.finditer(text):
            d = m.group(0)
            if len(d) > 4 and "." in d and d.count(".") < 4:
                domains.append(d)
        return domains[:10]

    def _extract_linked_domains(self, text: str) -> list[str]:
        """Extract domain mentions from text (bio links)."""
        if not text:
            return []
        domains: list[str] = []
        for pattern in _BIO_LINK_PATTERNS:
            for m in pattern.finditer(text):
                if m.group(1):
                    domains.append(m.group(1).lower())
        return list(set(domains))[:5]

    def _extract_linked_emails(self, text: str) -> list[str]:
        """Extract email addresses from text."""
        if not text:
            return []
        emails = _EMAIL_PATTERNS.findall(text)
        return list(set(emails))[:5]

    # ── Confidence Scoring ─────────────────────────────────────────────────────

    def _compute_confidence(
        self,
        _platform: str,
        _username: str,
        linked_domains: list[str],
        linked_emails: list[str],
    ) -> float:
        """Compute confidence using canonical confidence policy."""
        # has_provenance: social profiles are derived from evidence
        has_provenance = True
        # has_ioc: email or domain links count as IOC
        has_ioc = bool(linked_emails or linked_domains)
        # corroboration_count: linked domains and emails provide corroboration
        corroboration_count = min(len(linked_domains) + len(linked_emails), 4)

        # Compute via policy
        confidence = _compute_confidence(
            source_family="SOCIAL",
            has_provenance=has_provenance,
            has_ioc=has_ioc,
            corroboration_count=corroboration_count,
            model_score=None,
        )

        # Preserve minimum threshold
        return max(confidence, SOCIAL_MIN_CONFIDENCE)

    # ── Deduplication & Write ───────────────────────────────────────────────────

    def _deduplicate_facets(
        self,
        facets: list[SocialIdentityFacet],
    ) -> list[SocialIdentityFacet]:
        """Deduplicate facets by profile URL."""
        seen: dict[str, SocialIdentityFacet] = {}
        for facet in facets:
            key = f"{facet.platform}:{facet.username}"
            if key not in seen:
                seen[key] = facet

        return list(seen.values())[:MAX_SOCIAL_PROFILES]

    async def _write_findings(
        self,
        facets: list[SocialIdentityFacet],
        store: Any,
        query: str,
    ) -> None:
        """Write social identity facets via canonical path."""
        try:
            from hledac.universal.knowledge.duckdb_store import CanonicalFinding

            # Build CanonicalFindings from facets
            findings: list[CanonicalFinding] = []
            for facet in facets:
                payload = json.dumps({
                    "platform": facet.platform,
                    "username": facet.username,
                    "display_name": facet.display_name,
                    "profile_url": facet.profile_url,
                    "linked_domains": list(facet.linked_domains),
                    "linked_emails": list(facet.linked_emails),
                    "confidence": facet.confidence,
                    "source_finding_id": facet.finding_id,
                    # R7: evidence_kind tracks derivation path
                    "evidence_kind": facet.evidence_kind if hasattr(facet, "evidence_kind") else "url_in_payload",
                })

                finding = CanonicalFinding(
                    finding_id=f"social:{facet.platform}:{facet.username[:32]}",
                    source_type="social_identity_surface",
                    query=query,
                    confidence=facet.confidence,
                    ts=_time.time(),
                    provenance=("social_identity_miner", facet.platform),
                    payload_text=payload,
                )
                findings.append(finding)

            # Canonical write path
            if hasattr(store, "async_ingest_findings_batch"):
                await store.async_ingest_findings_batch(findings)
            elif hasattr(store, "ingest_findings"):
                await store.ingest_findings(findings)

        except Exception:
            pass  # fail-soft: non-critical advisory

    # ── Utility ─────────────────────────────────────────────────────────────────

    def _build_profile_url(self, platform: str, username: str, platform_host: str = "") -> str:
        """Build canonical profile URL for a platform."""
        platform_url_map = {
            "github": f"https://github.com/{username}",
            "twitter": f"https://twitter.com/{username}",
            "linkedin": f"https://linkedin.com/in/{username}",
            "mastodon": f"https://mastodon.social/@{username}",
            "keybase": f"https://keybase.io/{username}",
            "gitlab": f"https://gitlab.com/{username}",
            "hackernews": f"https://news.ycombinator.com/user?id={username}",
            "reddit": f"https://www.reddit.com/user/{username}",
            "youtube": f"https://youtube.com/@{username}",
            "facebook": f"https://www.facebook.com/{username}",
            # R7: new platforms
            "telegram": f"https://t.me/{username}",
            "matrix": f"https://matrix.to/#/{username}",
            "medium": f"https://medium.com/@{username}",
            "substack": f"https://{username}.substack.com/",
            "npmjs": f"https://www.npmjs.com/~{username}",
            "pypi": f"https://pypi.org/user/{username}",
            "huggingface": f"https://huggingface.co/{username}",
            "github_gist": f"https://gist.github.com/{username}",
            # discord_invite is NOT a person identity — no profile URL
        }
        if platform == "gitlab_selfhosted" and platform_host:
            return f"https://{platform_host}/u/{username}"
        if platform == "discord_invite":
            return ""  # invite links are not person identities
        return platform_url_map.get(platform, f"https://{platform}.com/{username}")

    def get_stats(self) -> dict[str, int]:
        """Return current mining statistics."""
        return dict(self._stats)


# ── Factory ────────────────────────────────────────────────────────────────────
def create_social_identity_miner_adapter() -> SocialIdentityMiner:
    """Create a SocialIdentityMiner instance."""
    return SocialIdentityMiner()