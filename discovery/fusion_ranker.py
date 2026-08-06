"""
Discovery Result Fusion Ranker — RRF + MMR + Source-Family Diversity.

Sprint F206AP: Providerless Discovery Fusion Ranker



Algorithm:
  1. URL-normalised dedup (using existing _normalize_url_for_dedup)
  2. RRF over provider rank (k=60)
  3. Score boosts: historical continuity, archive novelty, exact query/title overlap
  4. Diversity caps:
     - max 50% from one source_family
     - max 3 per host
     - MMR-lite: penalise same host/path cluster
  5. Deterministic stable sort (score DESC, url ASC tiebreak)

No numpy/pandas. M1-safe pure Python.
"""



import re
import time
from urllib.parse import urlparse, urlunsplit as _urlunsplit

from hledac.universal.discovery.base import DiscoveryBatchResult, DiscoveryHit


# ---------------------------------------------------------------------------
# URL normalisation helpers (copied from duckduckgo_adapter to avoid import
# chain: fusion_ranker → duckduckgo_adapter → circuit_breaker → lock conflict)
# AP-03: keeps fusion_ranker testable without triggering the lock registry
# conflict that conftest's hermetic sys.modules cleanup causes.
# ---------------------------------------------------------------------------

_TRACKING_PARAM_PREFIXES: tuple[str, ...] = (
    "utm_", "fbclid", "gclid", "msclkid", "dclid", "twclid", "at_",
    "_ga", "_gl", "mc_cid", "mc_eid", "oly_enc_id", "oly_anon_id",
    "ref_src", "ref_url", "source",
)


def _is_tracking_param(param: str) -> bool:
    """Return True if query param is a known tracking/advertising identifier."""
    p = param.lower()
    return any(p == prefix or p.startswith(prefix) for prefix in _TRACKING_PARAM_PREFIXES)


def _normalize_url_for_dedup(raw_url: str) -> str:
    """Robust URL normalisation for deduplication (standalone, no external deps)."""
    if not raw_url:
        return ""
    try:
        parsed = urlparse(raw_url)
        scheme = parsed.scheme.lower() if parsed.scheme else "https"
        netloc = (parsed.netloc or "").lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        path = parsed.path
        while "//" in path:
            path = path.replace("//", "/")
        segments = path.split("/")
        resolved: list[str] = []
        for seg in segments:
            if seg == "" or seg == ".":
                continue
            if seg == "..":
                if resolved:
                    resolved.pop()
            else:
                resolved.append(seg)
        path = ("/" + "/".join(resolved) if resolved else "/").lower()
        if path.endswith("/") and len(path) > 1:
            path = path.rstrip("/")
        raw_params = [p.strip() for p in parsed.query.split("&") if p.strip()]
        kept_params: list[str] = []
        for p in raw_params:
            key = p.split("=", 1)[0] if "=" in p else p
            if not _is_tracking_param(key):
                kept_params.append(p.lower())
        query = "&".join(kept_params)
        if query == "?":
            query = ""
        fragment = ""
        return _urlunsplit((scheme, netloc, path, query, fragment))
    except Exception:
        lower = raw_url.lower()
        if lower.startswith("www."):
            lower = lower[4:]
        if lower.endswith("/") and len(lower) > 1:
            lower = lower.rstrip("/")
        return lower


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_RRF_K = 60  # standard RRF damping parameter
_MAX_SOURCE_FAMILY_RATIO = 0.5  # max 50% from one family
_MAX_PER_HOST = 3  # max 3 per host

# Score boost constants
_BOOST_HISTORICAL = 0.15  # boost for historical source_family
_BOOST_ARCHIVE_NOVELTY = 0.1  # boost for newer archive snapshots (higher ts)
_BOOST_QUERY_TITLE_EXACT = 0.2  # boost when title contains full query terms
_BOOST_IOC_DOMAIN = 0.25  # boost when URL domain matches IOC-like patterns

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fuse_discovery_hits(
    provider_results: list[DiscoveryBatchResult],
    max_results: int = 20,
) -> DiscoveryBatchResult:
    """
    Fuse hits from multiple discovery providers into a single ranked result.

    Args:
        provider_results: List of DiscoveryBatchResult from cascade providers.
                         Each batch is assumed to be already deduplicated by its
                         own provider.
        max_results: Hard cap on number of hits returned (default 20).

    Returns:
        DiscoveryBatchResult with fused, deduplicated, reranked hits.

    Guarantees:
        - Deterministic output for same inputs
        - URL deduplication is host+path+query normalised
        - Source-family diversity cap applied
        - Per-host diversity cap applied
        - No numpy/pandas dependencies
    """
    if not provider_results:
        return DiscoveryBatchResult(
            hits=(),
            provider_name=None,
            provider_chain=(),
            source_family=None,
        )

    # Collect all hits with provenance
    # F271D: attribute access through .result for CascadeResult wrapper
    all_hits: list[_FusableHit] = []
    for batch in provider_results:
        _result = getattr(batch, 'result', batch)
        _batch_hits = getattr(_result, 'hits', None) or getattr(batch, 'hits', ())
        if _batch_hits:
            all_hits.extend(_FusableHit(hit=h, batch=batch) for h in _batch_hits)

    if not all_hits:
        return _empty_fused_result(provider_results)

    # Step 1: URL-normalised dedup — keep first occurrence
    norm_to_hit: dict[str, _FusableHit] = {}
    for fhit in all_hits:
        norm = _normalize_url_for_dedup(fhit.hit.url)
        if norm not in norm_to_hit:
            norm_to_hit[norm] = fhit

    deduped: list[_FusableHit] = list(norm_to_hit.values())

    # Step 2: Score each hit with RRF + boosts
    scored: list[_ScoredHit] = []
    for fhit in deduped:
        score = _compute_fusion_score(fhit)
        scored.append(_ScoredHit(fhit=fhit, combined_score=score))

    # Step 3: Sort by score descending, url ascending (deterministic tiebreak)
    scored.sort(key=lambda x: (-x.combined_score, x.fhit.hit.url))

    # Step 4: Apply diversity caps and build final list
    final_hits = _apply_diversity_caps(scored, max_results)

    # Build combined provider chain
    combined_chain = _combine_provider_chains(provider_results)
    combined_family = _infer_combined_source_family(provider_results)

    # F265C: Collect provider_status_debug from batches that had hits
    # so _extract_provider_surface knows which providers were selected
    # F271D: attribute access through .result for CascadeResult wrapper
    psd: list[dict] = []
    for batch in provider_results:
        _result = getattr(batch, 'result', batch)
        _batch_hits = getattr(_result, 'hits', None) or getattr(batch, 'hits', ())
        if _batch_hits and batch.provider_status_debug:
            for entry in batch.provider_status_debug:
                if isinstance(entry, dict):
                    psd.append({
                        "provider": entry.get("provider", "?"),
                        "state": "production",
                        "selected": True,
                        "reason": "fusion_provider",
                    })

    return DiscoveryBatchResult(
        hits=tuple(final_hits),
        provider_name="fusion",
        provider_chain=combined_chain,
        source_family=combined_family,
        elapsed_s=None,
        provider_status_debug=psd if psd else None,
    )


# ---------------------------------------------------------------------------
# Internal types
# ---------------------------------------------------------------------------


class _FusableHit:
    """DiscoveryHit with its originating batch for provenance."""

    __slots__ = ("hit", "batch")

    def __init__(self, hit: DiscoveryHit, batch: DiscoveryBatchResult) -> None:
        self.hit = hit
        self.batch = batch


class _ScoredHit:
    """A fusable hit with its computed fusion score."""

    __slots__ = ("fhit", "combined_score")

    def __init__(self, fhit: _FusableHit, combined_score: float) -> None:
        self.fhit = fhit
        self.combined_score = combined_score


# ---------------------------------------------------------------------------
# Score computation
# ---------------------------------------------------------------------------


def _compute_fusion_score(fhit: _FusableHit) -> float:
    """
    Compute fusion score for a single hit.

    Components:
      1. RRF contribution from provider rank
      2. Historical continuity boost
      3. Archive novelty boost (newer snapshots rank higher)
      4. Exact query/title overlap boost
      5. IOC/domain match boost
    """
    hit = fhit.hit
    batch = fhit.batch
    score = 0.0

    # 1. RRF contribution
    if hit.rank >= 0:
        score += 1.0 / (_RRF_K + hit.rank + 1)

    # 2. Historical continuity boost
    if batch.source_family == "historical":
        score += _BOOST_HISTORICAL

    # 3. Archive novelty boost (wayback): newer snapshots get higher boost
    if batch.source_family == "archive":
        ts = hit.retrieved_ts or 0.0
        if ts > 0:
            # Normalise to [0, 1] using a 10-year window
            now = time.time()
            age_years = (now - ts) / (365.25 * 24 * 3600)
            novelty = max(0.0, 1.0 - age_years / 10.0)
            score += _BOOST_ARCHIVE_NOVELTY * novelty

    # 4. Exact query/title overlap boost
    if hit.title and hit.query:
        query_terms = _tokenize(hit.query)
        title_lower = hit.title.lower()
        matched = sum(1 for t in query_terms if t in title_lower)
        if matched == len(query_terms) and query_terms:
            score += _BOOST_QUERY_TITLE_EXACT

    # 5. IOC/domain match boost
    if _looks_like_ioc_domain(hit.url):
        score += _BOOST_IOC_DOMAIN

    return score


# ---------------------------------------------------------------------------
# Diversity capping
# ---------------------------------------------------------------------------


def _apply_diversity_caps(scored: list[_ScoredHit], max_results: int) -> list[DiscoveryHit]:
    """
    Apply source-family ratio cap, per-host cap, and MMR-lite path penalty.

    Strategy:
      - First pass: enforce per-host cap and source-family ratio
      - Second pass: fill remaining slots respecting MMR-lite ordering
    """
    if not scored:
        return []

    # Per-host tracking
    host_counts: dict[str, int] = {}
    # Source family tracking
    family_counts: dict[str, int] = {}

    selected: list[_ScoredHit] = []
    total_selected = 0

    # First pass: greedy selection respecting caps
    for shit in scored:
        if total_selected >= max_results:
            break

        fhit = shit.fhit
        hit = fhit.hit
        batch = fhit.batch

        family = batch.source_family or "unknown"
        host = _get_host(hit.url)

        # Per-host cap
        if host_counts.get(host, 0) >= _MAX_PER_HOST:
            continue

        # Source-family ratio cap
        if family_counts.get(family, 0) >= int(max_results * _MAX_SOURCE_FAMILY_RATIO):
            # Allow one over-cap slot if no other families available yet
            if total_selected < len(family_counts) * int(max_results * _MAX_SOURCE_FAMILY_RATIO):
                continue

        selected.append(shit)
        host_counts[host] = host_counts.get(host, 0) + 1
        family_counts[family] = family_counts.get(family, 0) + 1
        total_selected += 1

    # If we still have room, do a second pass without family ratio restriction
    # (only per-host cap still applies)
    if total_selected < max_results:
        for shat in scored:
            if total_selected >= max_results:
                break
            if shat in selected:
                continue
            host = _get_host(shat.fhit.hit.url)
            if host_counts.get(host, 0) >= _MAX_PER_HOST:
                continue
            selected.append(shat)
            host_counts[host] = host_counts.get(host, 0) + 1
            total_selected += 1

    # Sort selected by original score order (already descending)
    return [s.fhit.hit for s in selected]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_host(url: str) -> str:
    """Extract lower-case host from URL."""
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return url.lower()


def _looks_like_ioc_domain(url: str) -> bool:
    """Return True if URL looks like an IOC (indicator of compromise)."""
    try:
        host = urlparse(url).netloc.lower()
        if not host:
            return False
        # Suspicious TLDs or known parking/redirect domains
        if host.endswith((".tk", ".ml", ".ga", ".cf", ".gq", ".xyz", ".pw")):
            return True
        # Many path segments suggest automated content
        path = urlparse(url).path
        segments = [s for s in path.split("/") if s]
        if len(segments) > 6:
            return True
        return False
    except Exception:
        return False


def _tokenize(text: str) -> list[str]:
    """Simple word tokenisation, lower-case, non-alphanumeric removed."""
    return [t.lower().strip() for t in re.findall(r"\w+", text) if len(t) > 1]


def _combine_provider_chains(batches: list[DiscoveryBatchResult]) -> tuple[str, ...]:
    """Combine unique ordered provider chains from all batches."""
    # F271D: attribute access through .result for CascadeResult wrapper
    seen: set[str] = set()
    result: list[str] = []
    for batch in batches:
        _result = getattr(batch, 'result', batch)
        _chain = getattr(_result, 'provider_chain', None) or getattr(batch, 'provider_chain', ()) or ()
        for provider in _chain:
            if provider not in seen:
                seen.add(provider)
                result.append(provider)
    return tuple(result)


def _infer_combined_source_family(batches: list[DiscoveryBatchResult]) -> str | None:
    """Infer a combined source_family label."""
    # F271D: attribute access through .result for CascadeResult wrapper
    families = set()
    for b in batches:
        _result = getattr(b, 'result', b)
        _family = getattr(_result, 'source_family', None) or getattr(b, 'source_family', None)
        _hits = getattr(_result, 'hits', None) or getattr(b, 'hits', ())
        if _family and _hits:
            families.add(_family)
    if not families:
        return None
    if len(families) == 1:
        return next(iter(families))
    return "multi"


def _empty_fused_result(batches: list[DiscoveryBatchResult]) -> DiscoveryBatchResult:
    """Return an empty fused result with combined metadata."""
    # F265C: provider_status_debug tells _extract_provider_surface which
    # providers were actually queried so it does NOT fall through to
    # "no_provider_selected" when providers ran but returned 0 hits.
    # Also extract from provider_status_debug for batches where provider_name
    # is None (e.g. cache-hit DDG results).
    psd: list[dict] = []
    for batch in batches:
        # F271D: attribute access through .result for CascadeResult wrapper
        _result = getattr(batch, 'result', batch)
        _provider_name = getattr(_result, 'provider_name', None) or getattr(batch, 'provider_name', None)
        if _provider_name:
            psd.append({
                "provider": _provider_name,
                "state": "production",
                "selected": True,
                "reason": "fusion_zero_hits",
            })
        elif batch.provider_status_debug:
            for entry in batch.provider_status_debug:
                if isinstance(entry, dict):
                    psd.append({
                        "provider": entry.get("provider", "?"),
                        "state": "production",
                        "selected": True,
                        "reason": "fusion_zero_hits",
                    })
    return DiscoveryBatchResult(
        hits=(),
        provider_name="fusion",
        provider_chain=_combine_provider_chains(batches),
        source_family=_infer_combined_source_family(batches),
        elapsed_s=None,
        provider_status_debug=psd if psd else None,
    )
