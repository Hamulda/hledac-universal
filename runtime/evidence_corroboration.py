"""
runtime/evidence_corroboration.py

Evidence Corroboration Graph Scorer — Sprint F223D

Ranks findings/seeds by cross-source corroboration, not feed volume.
No model imports, no network calls, no LLM for scoring.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# --------------------------------------------------------------------------- #
# Datatypes
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class CorroborationScore:
    """One corroboration assessment for an indicator value."""

    value: str = ""
    kind: str = ""
    score: float = 0.0
    source_family_count: int = 0
    independent_source_count: int = 0
    supporting_finding_ids: tuple[str, ...] = field(default_factory=tuple)
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def is_strong(self) -> bool:
        return self.score >= 2.0 and self.source_family_count >= 2

    def is_weak(self) -> bool:
        return self.source_family_count <= 1

    def is_noise(self) -> bool:
        return self.score <= 0.5


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

# Source family groups (families with similar intelligence value)
_FEED_FAMILIES = {"feed"}
_NONFEED_FAMILIES = {"ct", "doh", "wayback", "passive_dns", "leak", "github", "pastebin"}

# Patterns for noisy/example infrastructure — checked before scoring
_NOISE_PATTERNS = [
    re.compile(r"^(https?://)?(www\.)?example\.(com|org|net)$", re.IGNORECASE),
    re.compile(r"^(https?://)?(www\.)?test\.(com|org|net)$", re.IGNORECASE),
    re.compile(r"^(https?://)?(www\.)?localhost\.?(:\d+)?$", re.IGNORECASE),
    re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$"),  # raw IP (no context)
    re.compile(r"^[a-f0-9]{8}\.onion$", re.IGNORECASE),  # generic .onion
    re.compile(r"^(https?://)?(www\.)?github\.com/[^/]+/?$", re.IGNORECASE),  # bare repo
    re.compile(r"^[^@]+@[^@]+\.[^@]+$"),  # bare email with no domain context
]

# Maximum indicators to return in top output
_MAX_RANKED = 100
_MAX_WEAK = 50
_MAX_PIVOTS = 20

# Score weights
_SCORE_FEED_PLUS_CROSS = 3.0
_SCORE_FEED_PLUS_NONFEED = 2.5
_SCORE_FEED_ONLY = 1.5
_SCORE_CROSS_NONFEED = 1.8
_SCORE_SINGLE_NONFEED = 1.0
_SCORE_SINGLE_FEED = 0.8
_SCORE_NOISE = 0.1

# Dedup: when computing source_family_count, only count one finding per source_type
_DUPLICATE_PENALTY = 0.3  # subtracted from score if only duplicates support


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def score_indicators_by_corroboration(findings: list[dict]) -> list[CorroborationScore]:
    """
    Score a list of findings dict by cross-source corroboration.

    Each finding dict must have at least:
        - value: str   — the IOC value (domain, IP, hash, etc.)
        - kind: str    — type of IOC (domain, ip, hash, email, url)
        - source_type: str  — source family (feed, ct, doh, wayback, passive_dns, etc.)

    Optional fields:
        - finding_id: str
        - confidence: float (0-1)
        - feed_rank: int (lower = more important in feed)

    Returns CorroborationScore list, sorted descending by score.
    """
    if not findings:
        return []

    # Group findings by (value, kind) — same indicator
    indicator_groups: dict[tuple[str, str], list[dict]] = {}
    for f in findings:
        value = str(f.get("value", "")).strip()
        kind = str(f.get("kind", "")).strip().lower()
        if not value or not kind:
            continue
        key = (value, kind)
        indicator_groups.setdefault(key, []).append(f)

    scores: list[CorroborationScore] = []

    for (value, kind), group in indicator_groups.items():
        sc = _score_group(value, kind, group)
        scores.append(sc)

    scores.sort(key=lambda s: s.score, reverse=True)
    return scores


def score_seeds_by_corroboration(seeds: list[dict]) -> list[CorroborationScore]:
    """
    Score seeds (from DuckDB nonfeed seed extraction) by corroboration.

    Seed dict must have:
        - value: str
        - kind: str
        - source: str (feed, body, title, url, etc.)
        - quality_decision: str (keep, weak, reject)
        - quality_score: float

    Returns CorroborationScore list.
    """
    if not seeds:
        return []

    # Convert seeds to finding-like dicts for the shared scorer
    findings = []
    for seed in seeds:
        # Skip rejected or very low quality
        qd = seed.get("quality_decision", "keep")
        if qd == "reject":
            continue
        qs = seed.get("quality_score", 0.5)
        if qs < 0.2:
            continue

        source = seed.get("source", "body")
        # Map seed source to source_type family
        source_type = _seed_source_to_family(source)

        findings.append({
            "value": seed.get("value", ""),
            "kind": seed.get("kind", "domain"),
            "source_type": source_type,
            "confidence": seed.get("confidence", 0.5),
            "quality_score": qs,
        })

    return score_indicators_by_corroboration(findings)


# --------------------------------------------------------------------------- #
# Core scoring
# --------------------------------------------------------------------------- #

def _score_group(value: str, kind: str, group: list[dict]) -> CorroborationScore:
    """Score one (value, kind) group of findings."""

    # Dedup: keep unique source_type per group (only one finding per source family)
    seen_source_types: set[str] = set()
    unique_findings: list[dict] = []
    for f in group:
        st = _normalize_source_type(f.get("source_type", "unknown"))
        if st not in seen_source_types:
            seen_source_types.add(st)
            unique_findings.append(f)

    if not unique_findings:
        return CorroborationScore(value=value, kind=kind)

    source_types = [f.get("source_type", "unknown") for f in unique_findings]
    source_families = {_normalize_source_type(st) for st in source_types}

    # Check noise patterns — noise kills score regardless of corroboration
    noise_reason = _check_noise(value, source_families)
    if noise_reason:
        return CorroborationScore(
            value=value,
            kind=kind,
            score=_SCORE_NOISE,
            source_family_count=len(source_families),
            independent_source_count=len(unique_findings),
            supporting_finding_ids=_extract_ids(unique_findings),
            reasons=(noise_reason,),
        )

    # Compute score
    score, reasons = _compute_score(value, kind, source_families, unique_findings)

    # Bonus: recent feed signal (lower feed_rank = more recent/important)
    feed_ranks = [f.get("feed_rank", 999) for f in unique_findings]
    if feed_ranks and min(feed_ranks) < 50:
        score += 0.2

    # Penalty: duplicates-only (same source type repeated)
    if len(unique_findings) == 1 and len(group) > 3:
        # Single unique source but many duplicates — likely duplication noise
        score -= _DUPLICATE_PENALTY

    # Cap score
    score = max(0.0, min(score, 5.0))

    return CorroborationScore(
        value=value,
        kind=kind,
        score=score,
        source_family_count=len(source_families),
        independent_source_count=len(unique_findings),
        supporting_finding_ids=_extract_ids(unique_findings),
        reasons=tuple(reasons),
    )


def _compute_score(
    _value: str, _kind: str, source_families: set[str], findings: list[dict]
) -> tuple[float, list[str]]:
    """Compute corroboration score and reasons."""
    reasons = []

    has_feed = bool(source_families & _FEED_FAMILIES)
    has_nonfeed = bool(source_families & _NONFEED_FAMILIES)
    family_count = len(source_families)

    # Strongest: feed + cross-family (ct/doh/wayback/passive_dns)
    if has_feed and family_count >= 3:
        score = _SCORE_FEED_PLUS_CROSS
        reasons.append(f"feed+{family_count}independent_sources")

    elif has_feed and has_nonfeed and family_count >= 2:
        score = _SCORE_FEED_PLUS_NONFEED
        nonfeed_hits = source_families & _NONFEED_FAMILIES
        reasons.append(f"feed+nonfeed({','.join(sorted(nonfeed_hits))})")

    elif has_feed and family_count == 1:
        score = _SCORE_FEED_ONLY
        reasons.append("feed_only_no_crosssource")

    elif not has_feed and family_count >= 2:
        # Non-feed cross-corroboration is interesting
        score = _SCORE_CROSS_NONFEED
        reasons.append(f"cross_nonfeed({','.join(sorted(source_families))})")

    elif not has_feed and family_count == 1:
        # Single non-feed source
        score = _SCORE_SINGLE_NONFEED
        sf = list(source_families)[0] if source_families else "unknown"
        reasons.append(f"single_source_{sf}")

    else:
        score = _SCORE_SINGLE_FEED
        reasons.append("low_corroboration")

    return score, reasons


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

_SEED_SOURCE_MAP = {
    "feed": "feed",
    "ct": "ct",
    "doh": "doh",
    "wayback": "wayback",
    "passive_dns": "passive_dns",
    "leak": "leak",
    "github": "github",
    "pastebin": "pastebin",
    "body": "nonfeed",
    "title": "nonfeed",
    "url": "nonfeed",
    "header": "nonfeed",
    "unknown": "unknown",
}

def _seed_source_to_family(source: str) -> str:
    return _SEED_SOURCE_MAP.get(source.lower(), "unknown")


def _normalize_source_type(source_type: str) -> str:
    """Normalize source type to a family."""
    if not source_type:
        return "unknown"
    st = source_type.lower().strip()
    if st in _SEED_SOURCE_MAP:
        return _SEED_SOURCE_MAP[st]
    return st


# Platform domains (major CDNs/Cloud providers — high false positive)
_PLATFORM_DOMAINS = frozenset({
    "cloudflare.com", "akamai.com", "fastly.com", "cdn.cloudflare.net",
    "amazonaws.com", "azure.com", "digitalocean.com", "google.com",
    "microsoft.com", "apple.com", "facebook.com", "twitter.com",
    "github.com", "gitlab.com", "bitbucket.org", "jsdelivr.net",
    "unpkg.com", "cdnjs.cloudflare.com", "bootstrapcdn.com",
    "rackcdn.com", "cloudfront.net", "akamaiedge.net", "edgekey.net",
    "mozilla.org", "wordpress.com", "wix.com", "squarespace.com",
})


def _check_noise(value: str, source_families: set[str]) -> str | None:
    """Check if this indicator is noise. Returns reason if noise, else None."""
    v = value.strip().lower()

    # Generic noise patterns
    for pattern in _NOISE_PATTERNS:
        if pattern.match(v):
            return f"noise_pattern:{pattern.pattern[:30]}"

    if v in _PLATFORM_DOMAINS:
        return "major_platform_domain"

    # Feed-only with no cross-source and very high confidence might be noise
    # (generic sinkholed domains common in CT feeds)
    if source_families == {"feed"}:
        return None  # not automatically noise, let feed_rank decide

    return None


def _extract_ids(findings: list[dict]) -> tuple[str, ...]:
    """Extract finding_id or generate from value."""
    ids = []
    for f in findings:
        fid = f.get("finding_id")
        if fid:
            ids.append(str(fid))
    return tuple(ids) if ids else ()


# --------------------------------------------------------------------------- #
# CLI output helpers
# --------------------------------------------------------------------------- #

def build_top_indicators(scores: list[CorroborationScore], limit: int = _MAX_RANKED) -> list[dict]:
    """Build ranked indicators output."""
    strong = [s for s in scores if s.is_strong()][:limit]
    return [_score_to_dict(s) for s in strong]


def build_weak_unverified(scores: list[CorroborationScore], limit: int = _MAX_WEAK) -> list[dict]:
    """Build weak/unverified indicators output."""
    weak = [s for s in scores if s.is_weak() or s.is_noise()][:limit]
    return [_score_to_dict(s) for s in weak]


def build_recommended_pivots(scores: list[CorroborationScore], limit: int = _MAX_PIVOTS) -> list[dict]:
    """Build recommended next pivots from high-corroboration indicators."""
    pivots: list[dict] = []
    seen_kinds: set[str] = set()

    for s in scores:
        if s.is_strong() and s.kind not in seen_kinds and len(pivots) < limit:
            pivots.append({
                "value": s.value,
                "kind": s.kind,
                "score": round(s.score, 2),
                "source_family_count": s.source_family_count,
                "reason": f"{s.source_family_count} independent sources",
                "suggested_action": "pivot",
            })
            seen_kinds.add(s.kind)

    return pivots


def _score_to_dict(s: CorroborationScore) -> dict[str, Any]:
    return {
        "value": s.value,
        "kind": s.kind,
        "score": round(s.score, 2),
        "source_family_count": s.source_family_count,
        "independent_source_count": s.independent_source_count,
        "supporting_finding_count": len(s.supporting_finding_ids),
        "reasons": list(s.reasons),
        "verdict": _verdict(s),
    }


def _verdict(s: CorroborationScore) -> str:
    if s.is_strong():
        return "strong"
    if s.is_weak():
        return "weak"
    return "noise"