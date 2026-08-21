"""Public pipeline acceptance — CanonicalFinding construction from pattern hits and public-surface pages.

Extracted from live_public_pipeline.py.
Handles: _build_public_finding(), _extract_live_public_findings_from_page().

Depends on:
- public_patterns: _make_finding_id, _pattern_context, MAX_EXTRACTED_TEXT_CHARS
- duckdb_store: CanonicalFinding
- public_constants: _SOURCE_TYPE, _PUBLIC_SOURCE_TYPE, _DEFAULT_CONFIDENCE
"""

import time

_SOURCE_TYPE: str = "live_public_pipeline"
_PUBLIC_SOURCE_TYPE: str = "public"
_DEFAULT_CONFIDENCE: float = 0.8
MAX_EXTRACTED_TEXT_CHARS: int = 200_000


async def _build_public_finding(
    *,
    query: str,
    url: str,
    page_text: str,
    hit_title: str,
    hit_snippet: str,
    discovery_score: float | None,
    discovery_reason: str | None,
    http_status_code: int = 0,
) -> tuple:
    """F226B: Build a public-surface CanonicalFinding from a non-pattern-matching page.

    Called when a page fetches successfully, extracts text, but has zero pattern
    matches AND is NOT skipped by quality gate (SKIP_WEAK) — i.e. a "content-only" page
    that provides public surface evidence.

    Also called for bootstrap pages (robots.txt, security.txt, sitemap.xml) that
    have meaningful content even without pattern matches.

    Does NOT bypass quality gate — SKIP_WEAK pages still return empty tuple.

    Returns:
        Tuple of (CanonicalFinding,) or () if page provides no actionable signal.

    """
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding

    from .public_patterns import _make_finding_id

    # P0-FIX (F290): Accept title+snippet even without body text.
    if not page_text or not page_text.strip():
        if not hit_title and not hit_snippet:
            return ()
        # Fall through with empty page_text — title/snippet will still be used

    # Bounded payload from title + snippet + first chars of body + status
    payload_parts: list[str] = []
    if hit_title:
        payload_parts.append(f"title: {hit_title[:200]}")
    if hit_snippet:
        payload_parts.append(f"snippet: {hit_snippet[:300]}")
    body_preview = page_text[:500].strip() if page_text else ""
    if body_preview:
        payload_parts.append(f"body: {body_preview}")
    if http_status_code > 0:
        payload_parts.append(f"status: {http_status_code}")
    if not payload_parts:
        return ()

    payload_text = "\n".join(payload_parts)
    if len(payload_text) > 2000:
        payload_text = payload_text[:2000]

    provenance_parts = [
        "source_family:public",
        f"url:{url[:300]}",
        "label:public_surface",
    ]
    if discovery_score is not None:
        provenance_parts.append(f"score:{discovery_score:.2f}")
    if discovery_reason:
        provenance_parts.append(f"reason:{discovery_reason[:100]}")
    provenance: tuple[str, ...] = tuple(provenance_parts)

    finding_id = _make_finding_id(
        query=query,
        url=url,
        label="public_surface",
        pattern="content_only",
        value=payload_text[:100],
    )

    try:
        finding = CanonicalFinding(
            finding_id=finding_id,
            query=query[:500],
            source_type=_PUBLIC_SOURCE_TYPE,
            confidence=0.65,
            ts=time.time(),
            provenance=provenance,
            payload_text=payload_text,
        )
        return (finding,)
    except Exception:
        return ()


async def _extract_live_public_findings_from_page(
    *,
    query: str,
    url: str,
    hit_label: str,
    hit_pattern: str,
    hit_value: str,
    hit_start: int,
    hit_end: int,
    page_text: str,
    discovery_score: float | None = None,
) -> tuple:
    """Construct CanonicalFinding for a single PatternHit.

    All heavy work (context extraction) offloaded to thread executor.
    """
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding
    from hledac.universal.runtime.worker_pool import get_rust_pool

    from .public_patterns import _make_finding_id, _pattern_context

    # Extract context in rayon pool — ISSUE 3.1 FIX: was run_in_cpu_pool_async
    pool = get_rust_pool("cpu")
    context: str = await pool.submit(_pattern_context, page_text, hit_start, hit_end)

    # Truncate to hard cap
    if len(context) > MAX_EXTRACTED_TEXT_CHARS:
        context = context[:MAX_EXTRACTED_TEXT_CHARS]

    finding_id = _make_finding_id(query, url, hit_label, hit_pattern, hit_value)

    provenance: tuple[str, ...] = (
        "source_family:public",
        "duckduckgo",
        url,
        hit_label or "",
        hit_pattern,
    )

    if discovery_score is not None:
        confidence = float(max(0.0, min(1.0, discovery_score)))
    else:
        confidence = _DEFAULT_CONFIDENCE

    finding = CanonicalFinding(
        finding_id=finding_id,
        query=query,
        source_type=_SOURCE_TYPE,
        confidence=confidence,
        ts=time.time(),
        provenance=provenance,
        payload_text=context,
    )
    return (finding,)
