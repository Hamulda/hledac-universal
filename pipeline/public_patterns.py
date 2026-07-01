"""
Public pattern matching and quality scoring.

Extracted from live_public_pipeline.py.
Handles: IOC extraction (rust backend), pattern context, quality scoring,
         page usability computation, and HTML→text conversion.

Pure functions, no I/O, no async. Heavy I/O (rust backend) is fail-safe.
"""

import hashlib
import html.parser
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

# ----------------------------------------------------------------------
# Quality tier constants (re-exported from public_constants for convenience)
# ----------------------------------------------------------------------
_QUALITY_TIER_VERY_GOOD = "very_good"
_QUALITY_TIER_GOOD = "good"
_QUALITY_TIER_OK = "ok"
_QUALITY_TIER_WEAK = "weak_low_signal"
_QUALITY_TIER_SKIP = "SKIP_WEAK"

# ----------------------------------------------------------------------
# Fetch budget tiers
# ----------------------------------------------------------------------
_FETCH_BUDGET_STRONG: float = 1.25
_FETCH_BUDGET_NORMAL: float = 1.0
_FETCH_BUDGET_WEAK: float = 0.65
_FETCH_BUDGET_SKIP: float = 0.0

# ----------------------------------------------------------------------
# Discovery signal thresholds
# ----------------------------------------------------------------------
_DISCOVERY_SIGNAL_SCORE_THRESHOLD: float = 0.3
_DISCOVERY_FALSE_POSITIVE_THRESHOLD: float = 0.5
_DISCOVERY_SKIP_THRESHOLD: float = 0.15

# ----------------------------------------------------------------------
# Text processing
# ----------------------------------------------------------------------
MAX_EXTRACTED_TEXT_CHARS: int = 200_000
MAX_METADATA_PREPEND_CHARS: int = 500
_FINDING_ID_CONTEXT_RADIUS: int = 100
_LOW_ENTROPY_UNIQUE_WORD_RATIO: float = 0.25

# ----------------------------------------------------------------------
# Pattern hit sentinel
# ----------------------------------------------------------------------
_NO_HIT_START = object()


# ----------------------------------------------------------------------
# HTML extraction
# ----------------------------------------------------------------------


class _HTMLTextExtractor(html.parser.HTMLParser):
    """
    Lightweight HTMLParser that collects only text from body-level tags.
    Fail-soft: never raises on malformed HTML.
    """
    __slots__ = ("_in_body", "_chunks", "_last_end")

    def __init__(self) -> None:
        super().__init__()
        self._in_body = False
        self._chunks: list[str] = []
        self._last_end = 0

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag in ("body", "div", "p", "tr", "li", "article", "section", "main"):
            if not self._chunks or self._chunks[-1] != " ":
                self._chunks.append(" ")
        elif tag in ("br", "hr"):
            if self._chunks and self._chunks[-1] != " ":
                self._chunks.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in (
            "body", "div", "p", "tr", "li", "article", "section", "main", "h1",
            "h2", "h3", "h4", "h5", "h6", "ul", "ol",
        ):
            if self._chunks and self._chunks[-1] != " ":
                self._chunks.append(" ")

    def handle_data(self, data: str) -> None:
        stripped = data.strip()
        if stripped:
            self._chunks.append(stripped)
            if self._chunks[-1] != " ":
                self._chunks.append(" ")

    def get_text(self) -> str:
        result = "".join(self._chunks)
        result = re.sub(r"\s+", " ", result).strip()
        return result


def _html_to_text(html_content: str) -> str:
    """
    Convert HTML to plain text using stdlib HTMLParser.
    Runs in calling thread (caller is responsible for asyncio.to_thread).
    """
    try:
        parser = _HTMLTextExtractor()
        parser.feed(html_content)
        text = parser.get_text()
    except Exception:
        text = re.sub(r"<[^>]+>", " ", html_content)
        text = re.sub(r"\s+", " ", text).strip()
    return text


# ----------------------------------------------------------------------
# Finding ID
# ----------------------------------------------------------------------


def _make_finding_id(
    query: str, url: str, label: str, pattern: str, value: str
) -> str:
    """
    Deterministic finding ID via SHA-256 hash of pipeline inputs.
    Uses rust backend xxhash if available (10-20x faster), falls back to sha256.
    """
    key = f"{query}\x00{url}\x00{label}\x00{pattern}\x00{value}"
    try:
        from core.rust_backend import rust as _rust_backend

        if _rust_backend.is_available and _rust_backend.hash is not None:
            return _rust_backend.hash.content_hash_hex(key)
        raise ImportError("Rust hash not available")
    except Exception:
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


# ----------------------------------------------------------------------
# Pattern context window
# ----------------------------------------------------------------------


def _pattern_context(
    text: str,
    start: int,
    end: int,
    radius: int = _FINDING_ID_CONTEXT_RADIUS,
) -> str:
    """
    Extract a context window around a pattern hit.
    Runs in calling thread (caller is responsible for asyncio.to_thread).
    """
    lo = max(0, start - radius)
    hi = min(len(text), end + radius)
    return text[lo:hi]


# ----------------------------------------------------------------------
# JS confidence
# ----------------------------------------------------------------------


def _js_confidence_from_verdict(
    verdict: str,
    status_code: int | None = None,
    content_length: int | None = None,
) -> float:
    """Derive js_confidence from verdict string and response signals."""
    if "RETRY_JS:thin_text_strong_signal" in verdict:
        return 0.85
    if "RETRY_JS" in verdict:
        return 0.70
    if status_code in (403, 429):
        return 0.45
    if content_length is not None and content_length < 500:
        return 0.55
    return 0.30


# ----------------------------------------------------------------------
# Text enrichment
# ----------------------------------------------------------------------


def _enrich_text_with_metadata(
    title: str,
    snippet: str,
    extracted_text: str,
) -> str:
    """
    Build a bounded scan text from: [title] [snippet] [extracted_content].

    FIX F300: Strip HTML from title and snippet before concatenation.
    """
    try:
        from hledac.universal.pipeline.scoring import _strip_html_tags_from_text
    except ImportError:
        def _strip_html_tags_from_text(text: str) -> str:
            if not text:
                return ""
            return re.sub(r'<[^>]+>', ' ', text).strip()

    title_clean = _strip_html_tags_from_text(title) if title else ""
    snippet_clean = _strip_html_tags_from_text(snippet) if snippet else ""

    meta_parts: list[str] = []
    remaining_meta = MAX_METADATA_PREPEND_CHARS

    if title_clean:
        title_trunc = title_clean[:remaining_meta]
        meta_parts.append(title_trunc)
        remaining_meta -= len(title_trunc)

    if snippet_clean and remaining_meta > 20:
        snippet_trunc = snippet_clean[:remaining_meta]
        meta_parts.append(snippet_trunc)

    meta_prefix = "\n".join(meta_parts) + "\n---\n"

    max_content = MAX_EXTRACTED_TEXT_CHARS - len(meta_prefix)
    if max_content < 0:
        max_content = 0

    content = extracted_text[:max_content] if extracted_text else ""
    return meta_prefix + content


# ----------------------------------------------------------------------
# Quality scoring
# ----------------------------------------------------------------------


def _score_page_quality(
    *,
    hit_url: str,
    hit_title: str,
    hit_snippet: str,
    hit_rank: int,
    query: str,
    extracted_text: str,
    discovery_score: float | None = None,
    discovery_reason: str | None = None,
) -> str:
    """
    Score page quality based on text content and discovery metadata.

    Returns: quality tier string (_QUALITY_TIER_*)
    """
    extracted_len = len(extracted_text) if extracted_text else 0

    # ---- Tier 1: Discovery signal check ----
    if discovery_score is not None and discovery_score >= 0.7:
        if discovery_reason in (
            "rescue_candidate", "bootstrap_security_txt",
            "seed_context_domain", "seed_context_url"
        ):
            return _QUALITY_TIER_GOOD
        if discovery_score >= 0.85:
            return _QUALITY_TIER_VERY_GOOD

    # ---- Tier 2: Pre-fetch text length gate (F275) ----
    if extracted_len < 80:
        return _QUALITY_TIER_SKIP

    # ---- Tier 3: Low entropy detection (F163B) ----
    if extracted_text:
        words = extracted_text.split()
        if words:
            unique_ratio = len(set(w.lower() for w in words)) / len(words)
            if unique_ratio < _LOW_ENTROPY_UNIQUE_WORD_RATIO:
                return _QUALITY_TIER_WEAK

    # ---- Tier 4: URL pattern quality ----
    url_lower = hit_url.lower()
    if any(p in url_lower for p in (
        "/.well-known/security.txt", "/robots.txt", "/sitemap.xml",
        "cisa.gov", "github.com", "abuse.ch"
    )):
        return _QUALITY_TIER_VERY_GOOD

    # ---- Tier 5: Title/snippet signal ----
    title_len = len(hit_title) if hit_title else 0
    snippet_len = len(hit_snippet) if hit_snippet else 0
    if title_len > 30 and snippet_len > 50:
        return _QUALITY_TIER_GOOD
    if title_len > 10 or snippet_len > 30:
        return _QUALITY_TIER_OK

    # ---- Tier 6: Default ----
    if extracted_len > 200:
        return _QUALITY_TIER_OK
    return _QUALITY_TIER_WEAK


# ----------------------------------------------------------------------
# Page usability computation
# ----------------------------------------------------------------------


def _compute_page_usable_fields(
    *,
    fetched: bool,
    matched_patterns: int,
    stored_findings: int,
    quality_reason: str | None,
    discovery_signal: bool,
    discovery_score: float | None,
    error: str | None,
    extracted_text_len: int = 0,
) -> tuple[bool, str, str, bool, str, str]:
    """
    Compute usable/quality fields for a page result.

    Returns: (is_usable, quality_tier, quality_reason, is_strong_signal,
              strong_signal_reason, waste_category)
    """
    # ---- Error path ----
    if error:
        if "404" in error or "NOT FOUND" in error.upper():
            return (False, "SKIP_WEAK", "http_404", False, "", "http_error")
        if "timeout" in error.lower():
            return (False, "SKIP_WEAK", "fetch_timeout", False, "", "timeout")
        return (False, "SKIP_WEAK", "fetch_error", False, "", "http_error")

    # ---- Not fetched ----
    if not fetched:
        return (False, "SKIP_WEAK", "not_fetched", False, "", "not_fetched")

    # ---- No extracted text ----
    if extracted_text_len == 0:
        return (False, "SKIP_WEAK", "empty_text", False, "", "empty_text")

    # ---- Quality tier mapping ----
    tier = quality_reason or _QUALITY_TIER_OK

    if tier == _QUALITY_TIER_SKIP or tier == "SKIP_WEAK":
        return (False, _QUALITY_TIER_SKIP, quality_reason or "skip", False, "", "weak_signal")

    # ---- Strong discovery signal ----
    strong_signal = False
    strong_signal_reason = ""
    if discovery_signal and discovery_score is not None and discovery_score >= _DISCOVERY_SIGNAL_SCORE_THRESHOLD:
        if matched_patterns > 0:
            strong_signal = True
            strong_signal_reason = f"discovery_score={discovery_score:.2f}, patterns={matched_patterns}"
        elif discovery_score >= _DISCOVERY_FALSE_POSITIVE_THRESHOLD:
            strong_signal = True
            strong_signal_reason = f"discovery_fp_bypass={discovery_score:.2f}"

    # ---- Usable ----
    is_usable = tier in (_QUALITY_TIER_VERY_GOOD, _QUALITY_TIER_GOOD, _QUALITY_TIER_OK)

    waste_category = ""
    if matched_patterns == 0 and stored_findings == 0:
        if discovery_score is not None and discovery_score < _DISCOVERY_FALSE_POSITIVE_THRESHOLD:
            waste_category = "waste_no_patterns"
        else:
            waste_category = "discovery_false_positive"

    return (
        is_usable,
        tier,
        quality_reason or "ok",
        strong_signal,
        strong_signal_reason,
        waste_category,
    )


# ----------------------------------------------------------------------
# IOC extraction (rust backend)
# ----------------------------------------------------------------------


def extract_iocs_from_text(text: str) -> list[Any]:
    """
    Extract IOCs from text using rust backend regex engine.

    P3 optimization: Routes to SIMD variant for text > 1KB (bulk content)
    since Teddy/NEON acceleration provides significant speedup for large texts.

    Fail-safe: returns empty list on any error.
    """
    try:
        from core.rust_backend import rust as _rust_backend
        if _rust_backend.is_available and hasattr(_rust_backend, "ioc"):
            # P3: Use SIMD for bulk text (>1KB) — Teddy/NEON accelerates regex on M1
            if len(text) > 1024 and hasattr(_rust_backend.ioc, "extract_iocs_simd"):
                return _rust_backend.ioc.extract_iocs_simd(text)
            return _rust_backend.ioc.extract_iocs_flat(text)
    except Exception:  # noqa: BLE001
        pass
    return []


# ----------------------------------------------------------------------
# Threat actor / malware family extraction
# ----------------------------------------------------------------------


# Bounded compiled patterns — max 500 entries in dictionary, O(1) lookup
_THREAT_ACTOR_RE = re.compile(
    r"\b(?:APT\d{2,3}|UAT|NCSC|GREY)\d*\b|"
    r"\b(?:Cozy Bear|CozyDuke|CozyDuke|Midnight Blizzard|The Dukens)\b|"
    r"\b(?:Fancy Bear|Sofacy|APT28|Sandworm|Voodoo Bear|Electrum)\b|"
    r"\b(?:Lazarus Group|Lazarus|Hidden Cobra)\b|"
    r"\b(?:FIN7|FIN8|Carbanak|Carbanak Gang|Anunak)\b|"
    r"\b(?:Barium|Wicked Panda|Zinc)\b|"
    r"\b(?:UNC\d{3,6})\b|"
    r"\b(?:Ocean Lot|Reaper Group|Geumseong|APT32|APT37|APT38)\b|"
    r"\b(?:TA428|MenuPass|Tailgater Team|Joe Team)\b",
    re.IGNORECASE,
)

_RANSOMWARE_FAMILY_RE = re.compile(
    r"\b(?:LockBit|LockBit\s*2(?:\.0)?|LockBit\s*3|LDX)\b|"
    r"\b(?:Conti|Wizard Spider|Ryuk)\b|"
    r"\b(?:REvil|Sodinokibi|RansomEXX|Nexway)\b|"
    r"\b(?:BlackCat|ALPHV|Hive|Clop)\b|"
    r"\b(?:Emotet|Heodo|Qakbot|Qbot)\b|"
    r"\b(?:IcedId|Bokbot|Dridex|Bugat)\b|"
    r"\b(?:TrickBot|Trickster|Raccoon Stealer)\b|"
    r"\b(?:VidAR|Aurora|RedLine)\b|"
    r"\b(?:Cobalt Strike|CobaltStrike|CS)\b|"
    r"\b(?:Metasploit|Metasploit Framework|MSF)\b",
    re.IGNORECASE,
)


def extract_threat_entities(text: str) -> list[tuple[str, str]]:
    """
    Extract threat actors and malware families from text.

    Returns list of (entity_name, entity_type) tuples.
    entity_type is "threat_actor" or "malware_family".

    GHOST_INVARIANTS:
      - Bounded: O(1) regex match, bounded result list
      - Fail-safe: returns [] on any error
      - No MLX/model loading
    """
    try:
        if not text:
            return []

        results: list[tuple[str, str]] = []
        seen: set[str] = set()

        # Extract threat actors
        for match in _THREAT_ACTOR_RE.finditer(text):
            name = match.group().strip()
            if name and name not in seen:
                seen.add(name)
                results.append((name, "threat_actor"))

        # Extract malware families
        for match in _RANSOMWARE_FAMILY_RE.finditer(text):
            name = match.group().strip()
            if name and name not in seen:
                seen.add(name)
                results.append((name, "malware_family"))

        return results
    except Exception:
        return []


# ----------------------------------------------------------------------
# UMA state helper
# ----------------------------------------------------------------------


def _get_uma_state() -> tuple[str, bool]:
    """
    Read UMA status via resource_governor surface.
    Returns (state_str, io_only_hint).
    """
    from hledac.universal.core.resource_governor import (
        evaluate_uma_state,
        sample_uma_status,
    )
    status = sample_uma_status()
    state = evaluate_uma_state(status.system_used_gib)
    io_only = status.io_only
    return state, io_only


# ----------------------------------------------------------------------
# Patterns configured count
# ----------------------------------------------------------------------


def _get_patterns_configured_count() -> int:
    """Return count of configured pattern matchers from singleton registry."""
    try:
        import sys
        state = sys.modules["hledac.universal.patterns.pattern_matcher"]._matcher_state
        return len(state._registry_snapshot) if state._registry_snapshot else 0
    except Exception:
        return 0
