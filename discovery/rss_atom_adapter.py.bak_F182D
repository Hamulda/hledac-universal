"""
RSS 2.0 and Atom 1.0 passive feed adapter.

Public-passive only: uses async_fetch_public_text() from 8AD as sole network input.
No storage writes. No LLM calls.

Parsing strategy:
- Namespace-safe via local-name helpers.
- Primary parser: defusedxml.ElementTree (available in env).
- Fallback: stdlib xml.etree.ElementTree.
- RSS 2.0: channel/item → title/link/description/pubDate/guid.
- Atom 1.0: feed/entry → title/link[@href]/summary/published/updated.

Security:
- XML entity/DOCTYPE guard before parsing.
- Size cap delegated to 8AD (max_bytes).
- Fail-soft on malformed XML.

Deduplication (preserve-first within a single feed):
- RSS: guid > link > fallback(title|published_raw).
- Atom: link[@rel=alternate/@href] > link[@href] > fallback(title|published_raw).

Sprint 8AJ — Feed Source Discovery + Curated Seeds:
- HTML <link rel="alternate"> discovery from downloaded HTML.
- <base href> awareness for relative URL resolution.
- Typed curated seed surface (OSINT-relevant feeds).
- Deterministic merge of discovered + seeded sources.
"""

from __future__ import annotations

import asyncio
import re
import time
import urllib.parse
from asyncio import CancelledError
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import TYPE_CHECKING, Any

import msgspec
import xxhash


def _entry_hash(title: str, published_raw: str) -> str:
    """Compute deterministic xxhash of title|published_raw for entry identity."""
    return xxhash.xxh64(f"{(title or '')}|{(published_raw or '')}").hexdigest()

# Sprint 8AH: defusedxml is primary parser when available.
# stdlib xml.etree.ElementTree is fallback.
try:
    import defusedxml.ElementTree as _DET
except ImportError:
    import xml.etree.ElementTree as _DET

if TYPE_CHECKING:
    from hledac.universal.fetching.public_fetcher import FetchResult  # noqa: F401


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------


class FeedEntryHit(msgspec.Struct, frozen=True, gc=False):
    """Single parsed feed entry."""

    feed_url: str
    entry_url: str
    title: str
    summary: str
    published_raw: str
    published_ts: float | None
    source: str
    rank: int
    retrieved_ts: float
    entry_hash: str = ""  # Sprint 8AN: xxhash of title|published_raw for dedup
    # Sprint 8BE: rich feed content field (content:encoded / entry.content[0].value)
    rich_content: str = ""
    # Sprint F150H: author field — key for downstream relevance/quality signal
    entry_author: str = ""
    # Sprint F150H: feed-level title extracted at parse time (not a network call)
    feed_title: str = ""
    # Sprint F150H: language hint from feed metadata (ISO 639-1 or similar)
    feed_language: str = ""
    # F150J: freshness score 0.0-1.0 (recent=1.0, stale=0.1, future=penalized)
    freshness_score: float = 0.0
    # F150J: quality score 0.0-1.0 (rich_content, author, title/summary length)
    quality_score: float = 0.0
    # F150J: freshness tier label — recent|fresh|aged|stale|future|unknown
    freshness_tier: str = ""
    # F150J: human-readable reason for selection/rank
    selection_reason: str = ""
    # F150K: source-side credibility signal — derived from curated seed priority
    source_priority_bias: float = 0.0
    # F150K: lightweight timestamp quality label
    time_signal_reason: str = ""


class FeedBatchResult(msgspec.Struct, frozen=True, gc=False):
    """Result of fetching and parsing one feed."""

    feed_url: str
    entries: tuple[FeedEntryHit, ...]
    error: str | None = None
    # F169C: source-level accessibility error — feed ingress root cause
    # beyond parse-level error. Carries the fetch-layer signal when the
    # feed was inaccessible at the source level.
    source_accessibility_error: str | None = None


# Sprint 8AJ — Feed Discovery DTOs


class FeedDiscoveryHit(msgspec.Struct, frozen=True, gc=False):
    """Single feed URL discovered from an HTML page."""

    page_url: str
    feed_url: str
    title: str
    feed_type: str
    confidence: float
    source: str
    discovered_ts: float


class FeedDiscoveryBatchResult(msgspec.Struct, frozen=True, gc=False):
    """Result of discovering feed URLs from an HTML page."""

    page_url: str
    hits: tuple[FeedDiscoveryHit, ...]
    error: str | None = None


class FeedSeed(msgspec.Struct, frozen=True, gc=False):
    """
    Single curated OSINT-relevant RSS/Atom feed seed.

    ``source`` field values:
    - ``"curated_seed"`` — runtime-usable RSS/Atom feed (primary surface)
    - ``"topology_candidate"`` — non-feed endpoint (intelligence/topology candidate only)

    Only ``curated_seed`` sources belong in the runtime RSS/Atom feed surface.
    ``topology_candidate`` sources are excluded from feed-surface processing.
    """

    feed_url: str
    label: str
    source: str
    priority: int = 0


class MergedFeedSource(msgspec.Struct, frozen=True, gc=False):
    """A feed source after merging discovered and seeded sources."""

    feed_url: str
    label: str
    origin: str  # "seed" | "discovered"
    priority: int


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_SOURCE: str = "rss_atom"
_MAX_ENTRIES_HARD: int = 100
_XML_ENTITY_RE: re.Pattern[str] = re.compile(
    r"<!ENTITY|<!DOCTYPE", re.IGNORECASE
)
# ISO 8601 / RFC 3339 normalization
_ISO_Z_RE: re.Pattern[str] = re.compile(r"Z$")

# Sprint 8AJ — Feed Discovery constants
_FEED_TYPES_HIGH: tuple[str, ...] = (
    "application/rss+xml",
    "application/atom+xml",
)
_FEED_TYPES_LOW: tuple[str, ...] = (
    "application/xml",
    "text/xml",
)
_MAX_CANDIDATES_DEFAULT: int = 10
_MAX_CANDIDATES_HARD: int = 20


# ---------------------------------------------------------------------------
# Namespace-safe local-name helpers
# ---------------------------------------------------------------------------


def _local_name(tag: str) -> str:
    """Strip namespace prefix, return local name."""
    if tag is None:
        return ""
    idx = tag.rfind("}")
    if idx >= 0:
        return tag[idx + 1 :]
    return tag


def _find_first_child(
    parent, localname: str
) -> Any | None:
    """Find first direct child element by local name (namespace-safe)."""
    children = list(parent)
    for child in children:
        if _local_name(child.tag) == localname:
            return child
    return None


def _iter_children(parent, localname: str):
    """Yield all direct child elements matching local name."""
    children = list(parent)
    for child in children:
        if _local_name(child.tag) == localname:
            yield child


# ---------------------------------------------------------------------------
# URL normalization (conservative, matching 8AC style)
# ---------------------------------------------------------------------------


def _normalize_url(raw: str | None) -> str:
    """Normalize URL for dedup: lowercase scheme+host, strip lone ?."""
    if not raw:
        return ""
    raw = raw.strip()
    if not raw:
        return ""
    try:
        parsed = urllib.parse.urlparse(raw)
        # lowercase scheme and host only
        normalized = parsed._replace(
            scheme=parsed.scheme.lower(),
            netloc=parsed.netloc.lower(),
        ).geturl()
        # strip lone trailing "?"
        if normalized.endswith("?"):
            normalized = normalized[:-1]
        return normalized
    except Exception:
        return raw.strip()


# ---------------------------------------------------------------------------
# Text extraction helpers
# ---------------------------------------------------------------------------


def _text_of(element) -> str:
    """Return element text or empty string."""
    if element is None:
        return ""
    text = element.text
    if text is None:
        return ""
    return text.strip()



# ---------------------------------------------------------------------------
# Date parsing (fail-soft, no locale dependency)
# ---------------------------------------------------------------------------


def _parse_published_ts(raw: str | None) -> float | None:
    """Parse date from RSS or Atom formats. Returns None on failure."""
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None
    # Try RFC 3339 / ISO 8601 via fromisoformat
    try:
        normalized = _ISO_Z_RE.sub("+00:00", raw)
        dt = __import__("datetime").datetime.fromisoformat(normalized)
        return dt.timestamp()
    except Exception:
        pass
    # Try RSS pubDate via email.utils
    try:
        dt = parsedate_to_datetime(raw)
        return dt.timestamp()
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# F150J: Freshness + Quality scoring (pure local, no new deps)
# ---------------------------------------------------------------------------

# Freshness tiers: age in seconds from retrieved_ts
# F178E: relaxed from 180→365 days — OSINT content (CVE disclosures, malware campaigns)
# remains valuable long after publication; 6-month-old IoCs are still actionable
_TIER_RECENT_MAX: float = 3 * 86400       # ≤3 days  → "recent"
_TIER_FRESH_MAX: float = 14 * 86400       # ≤14 days → "fresh"
_TIER_AGED_MAX: float = 60 * 86400        # ≤60 days → "aged"
_TIER_STALE_MAX: float = 365 * 86400     # ≤365 days → "stale"
# >365 days or future → "unknown"

# Future penalty: entries with published_ts > retrieved_ts + this gap are penalized
_FUTURE_GAP_MAX: float = 3600 * 6        # 6 hours ahead = tolerated noise


def _compute_freshness(
    published_ts: float | None,
    retrieved_ts: float,
) -> tuple[float, str]:
    """
    Compute freshness_score (0.0-1.0) and freshness_tier.

    Scoring:
    - recent (≤3d):   score 1.0
    - fresh (≤14d):   score 0.85
    - aged (≤60d):   score 0.6
    - stale (≤180d): score 0.3
    - very old:       score 0.1
    - future (>retrieved_ts+6h): penalize by gap ratio, min 0.05
    - None/unparseable: 0.05 (treat as very stale, not discarded)
    """
    if published_ts is None:
        return 0.05, "unknown"

    age = retrieved_ts - published_ts

    if age < 0:
        # Future timestamp
        future_gap = abs(age)
        if future_gap > _FUTURE_GAP_MAX:
            # More than 6h in the future → heavy penalty
            penalty = max(0.05, 0.3 * (1 - future_gap / (86400 * 7)))
            return penalty, "future"
        else:
            # Within tolerance — near-present, small bonus
            return 0.95, "recent"

    if age <= _TIER_RECENT_MAX:
        return 1.0, "recent"
    if age <= _TIER_FRESH_MAX:
        return 0.85, "fresh"
    if age <= _TIER_AGED_MAX:
        return 0.6, "aged"
    if age <= _TIER_STALE_MAX:
        return 0.3, "stale"
    return 0.1, "unknown"


# F178E: spam domain patterns — known low-quality / parked / placeholder domains
_SPAM_DOMAIN_PATTERNS: tuple[str, ...] = (
    "blogspot.com",
    "wordpress.com",
    "livejournal.com",
    "tumblr.com",
    "blogspot.ru",
    "wp.ru",
    "site90.net",
    "000webhost.com",
    "110mb.com",
    "freesitehost.com",
    "blogcindi.com",
    "bloggen.ru",
    "blogrund.com",
    "wordpress.org.ru",
)


def _is_spam_domain(url: str) -> bool:
    """Return True for known low-quality / parked / placeholder domains."""
    if not url:
        return False
    try:
        parsed = urllib.parse.urlparse(url)
        netloc = parsed.netloc.lower()
        return any(p in netloc for p in _SPAM_DOMAIN_PATTERNS)
    except Exception:
        return False


# F178E: SEO spam / title-manipulation patterns
_SEO_SPAM_TITLE_RE = __import__("re").compile(
    r"(?:\b\w+\b\s*){30,}", re.IGNORECASE  # title with 30+ words = likely keyword-stuffed
)
_REPEATED_DOTS_RE = __import__("re").compile(r"^\.{3,}$")  # "..." only title
_TEMPLATE_NOISE_RE = __import__("re").compile(
    r"^\s*(?:title|untitled|article|post|page)\s*$", re.IGNORECASE
)


def _is_seo_spam_title(title: str) -> bool:
    """Return True for SEO-optimized / template noise titles."""
    t = title.strip()
    if not t or len(t) < 3:
        return True
    # Template noise
    if _TEMPLATE_NOISE_RE.match(t):
        return True
    # Repeated dots
    if _REPEATED_DOTS_RE.match(t):
        return True
    # 30+ words = keyword stuffing
    if _SEO_SPAM_TITLE_RE.match(t):
        return True
    return False


# F178E: snippet noise patterns — "title • description" template
_SNIPPET_TEMPLATE_RE = __import__("re").compile(
    r"^\s*[\w\s\-]+\s*[•·|\-\"']\s*[\w\s\-]+\s*$"
)


def _is_template_snippet(snippet: str, title: str) -> bool:
    """Return True for generic template snippet that adds no value."""
    s = snippet.strip()
    if not s:
        return False
    # "title • description" pattern noise
    if _SNIPPET_TEMPLATE_RE.match(s) and len(s) < 80:
        return True
    return False


def _compute_quality(entry: FeedEntryHit) -> float:
    """
    Compute quality_score (0.0-1.0) from entry metadata.
    Factors: rich_content, summary length, title length,
    entry_author presence, feed_language presence, URL structure.

    F178E additions:
    - Spam domain penalty (max -0.25)
    - SEO spam title penalty (-0.3)
    - Template snippet penalty (-0.1)
    - Windows-1252 charset hint bonus (+0.05)
    """
    score = 0.0

    # F178E: spam domain penalty
    if _is_spam_domain(entry.entry_url):
        score -= 0.25

    # F178E: SEO spam title detection
    if _is_seo_spam_title(entry.title):
        score -= 0.30

    # F178E: template snippet detection
    if _is_template_snippet(entry.summary, entry.title):
        score -= 0.10

    # rich_content — most important signal
    rc = entry.rich_content
    if rc:
        rc_len = len(rc)
        if rc_len > 500:
            score += 0.35
        elif rc_len > 100:
            score += 0.2
        else:
            score += 0.1

    # summary length (words) — only count if not template noise
    if not _is_template_snippet(entry.summary, entry.title):
        summary_words = len(entry.summary.split())
        if summary_words >= 30:
            score += 0.15
        elif summary_words >= 10:
            score += 0.08
        elif summary_words > 0:
            score += 0.03

    # title length (chars, excluding whitespace)
    title_len = len(entry.title.strip())
    if 30 <= title_len <= 120:
        score += 0.12
    elif title_len > 0:
        score += 0.04

    # entry_author presence
    if entry.entry_author.strip():
        score += 0.12

    # feed_language presence
    if entry.feed_language.strip():
        score += 0.08

    # feed_title presence (metadata richness)
    if entry.feed_title.strip():
        score += 0.05

    # URL structure — has path beyond TLD
    eu = entry.entry_url
    if eu:
        try:
            parsed = urllib.parse.urlparse(eu)
            scheme = parsed.scheme.lower()
            path = parsed.path.rstrip("/")
            # Penalize non-http schemes
            if scheme and scheme not in ("http", "https"):
                score -= 0.15
            elif path.count("/") >= 2:
                score += 0.08  # structured URL = article
            elif path.count("/") == 1 and len(path) > 1:
                score += 0.04
        except Exception:
            pass

    return max(min(score, 1.0), 0.0)


# ---------------------------------------------------------------------------
# XML security guard
# ---------------------------------------------------------------------------


def _is_xml_entity_dangerous(text: str) -> bool:
    """Check for ENTITY / DOCTYPE declarations that could be XML bombs."""
    if not text:
        return False
    return bool(_XML_ENTITY_RE.search(text))


# ---------------------------------------------------------------------------
# Entry identity / dedup key
# ---------------------------------------------------------------------------


def _entry_dedup_key(
    entry_url: str,
    title: str,
    published_raw: str,
    guid_raw: str | None,
    is_permalink: bool | None,
) -> str:
    """
    Build stable dedup key following RSS identity priority:
    guid (if permalink or no attribute) > link > fallback(title|published_raw).
    """
    if guid_raw:
        # guid with isPermaLink="true" or no attribute can serve as URL
        if is_permalink or is_permalink is None:
            return f"g:{guid_raw}"
        # isPermaLink="false" → use as dedup key only
        return f"gf:{guid_raw}"
    if entry_url:
        return f"u:{entry_url}"
    return f"f:{title.lower().strip()}|{published_raw}"


# ---------------------------------------------------------------------------
# Sprint 8AR — Safe XML Recovery
# ---------------------------------------------------------------------------

import xml.etree.ElementTree as _ET  # stdlib fallback only


class _ParseMode:
    """Parse-mode observability labels for internal/tracking use."""

    RAW_DEFUSEDXML = "raw_defusedxml"
    SANITIZED_DEFUSEDXML = "sanitized_defusedxml"
    SANITIZED_STDLIB_FALLBACK = "sanitized_stdlib_fallback"
    FINAL_FAIL = "final_fail"


# Explicit allowlist of benign HTML named entities that appear in real feeds.
# Each entry is (entity_name, unicode_replacement).
# Covers: dashes, quotes, apostrophes, ellipsis,nbsp.
_BENIGN_HTML_ENTITIES: tuple[tuple[str, str], ...] = (
    ("nbsp", "\u00a0"),  # non-breaking space
    ("ndash", "\u2013"),  # en dash
    ("mdash", "\u2014"),  # em dash
    ("ldquo", "\u201c"),  # left double quotation mark
    ("rdquo", "\u201d"),  # right double quotation mark
    ("lsquo", "\u2018"),  # left single quotation mark
    ("rsquo", "\u2019"),  # right single quotation mark
    ("hellip", "\u2026"),  # horizontal ellipsis
)


def _safe_sanitize_xml(raw: str) -> str:
    """
    Produce a sanitized copy of XML text safe for re-parsing.

    Single-pass scanner that:
    1. Strips <!DOCTYPE ...> declarations (including internal subsets).
    2. Strips <!ENTITY ...> declarations entirely.
    3. Removes ``&name;`` references for stripped custom entities.
    4. Replaces benign HTML named-entity references with Unicode equivalents.

    Standard XML predefined entities (&amp; &lt; &gt; &quot; &apos;) and numeric
    character references (&#NNN; &#xHHH;) are left untouched.

    Unknown custom entity references NOT on the allowlist remain and will
    cause a parse failure (fail-soft behaviour).

    Returns the original input unchanged if no DOCTYPE/ENTITY declarations
    are present (fast path).
    """
    # Fast path: no dangerous declarations AND no entity references to process
    # Note: we still process the input even with benign entity refs
    # (predefined entities like &amp; are left untouched by the scanner)
    if (
        "<!doctype" not in raw.lower()
        and "<!entity" not in raw.lower()
        and "&" not in raw
    ):
        return raw

    import re as _re

    # ---- Precompute sets ----
    _predefined: frozenset[str] = frozenset(
        {"amp", "lt", "gt", "quot", "apos"}
    )
    # apos is included because defusedxml resolves it to "'" natively in XML 1.0.
    _benign_names: frozenset[str] = frozenset(
        name for name, _ in _BENIGN_HTML_ENTITIES
    )
    _benign_patterns: tuple[tuple[str, str], ...] = tuple(_BENIGN_HTML_ENTITIES)

    result: list[str] = []
    i = 0
    n = len(raw)

    while i < n:
        c = raw[i]

        # ---- Handle <!DOCTYPE ...> ----
        if c == "<" and raw[i:i+9].lower() == "<!doctype":
            # Skip the entire DOCTYPE block using bracket-depth tracker
            i += 9
            depth = 0  # inside [...]:
            in_quote = False
            quote_char: str | None = None
            while i < n:
                ch = raw[i]
                if not in_quote:
                    if ch in ('"', "'"):
                        in_quote = True
                        quote_char = ch
                    elif ch == "[":
                        depth += 1
                    elif ch == "]":
                        if depth > 0:
                            depth -= 1
                            if depth == 0 and i + 1 < n and raw[i + 1] == ">":
                                i += 2  # consume ']>'
                                break
                    elif ch == ">" and depth == 0:
                        i += 1  # consume bare '>'
                        break
                else:
                    if ch == quote_char:
                        in_quote = False
                        quote_char = None
                i += 1

        # ---- Handle <!ENTITY ...> ----
        elif c == "<" and raw[i:i+9].lower() == "<!entity":
            # Skip the entire ENTITY declaration without copying to output
            i += 9
            in_quote = False
            quote_char: str | None = None
            while i < n:
                ch = raw[i]
                if not in_quote:
                    if ch in ('"', "'"):
                        in_quote = True
                        quote_char = ch
                    elif ch == ">" and not in_quote:
                        i += 1
                        break
                else:
                    if ch == quote_char:
                        in_quote = False
                        quote_char = None
                i += 1

        # ---- Handle &name; entity reference ----
        elif c == "&" and i + 1 < n and raw[i + 1] != "#":
            # Potential named entity reference
            sem_idx = raw.find(";", i + 1)
            if sem_idx != -1 and sem_idx - i < 20:  # sanity limit on name length
                name = raw[i + 1:sem_idx]
                name_is_valid = (
                    name
                    and name.isidentifier()
                    and name.lower() not in _predefined
                )
                if name_is_valid:
                    if name.lower() in _benign_names:
                        # Replace with Unicode equivalent
                        replacement = next(
                            repl
                            for n_, repl in _benign_patterns
                            if n_.lower() == name.lower()
                        )
                        result.append(replacement)
                        i = sem_idx + 1
                        continue
                    # else: custom entity reference not on allowlist.
                    # Replace with space to prevent undefined-entity parse errors
                    # while preserving document structure.
                    result.append(" ")
                    i = sem_idx + 1
                    continue
                # Not a valid identifier: treat as literal '&'
                result.append(c)
                i += 1
            else:
                result.append(c)
                i += 1

        # ---- Default: copy character ----
        else:
            result.append(c)
            i += 1

    return "".join(result)


# ---------------------------------------------------------------------------
# Feed parsers
# ---------------------------------------------------------------------------


def _child_by_name(parent, localname):
    """Find first child by local name using list(parent) snapshot."""
    children = list(parent)
    for child in children:
        if child.tag == localname:
            return child
    return None


def _parse_rss(root, feed_url: str, retrieved_ts: float) -> list[FeedEntryHit]:
    """
    Parse RSS 2.0 feed.

    RSS 2.0 structure:
      rss/channel/item/title/link/description/pubDate/guid[@isPermaLink]
    """
    channel = _child_by_name(root, "channel")
    if channel is None:
        return []

    # ---- Extract channel-level metadata once ----
    channel_children = list(channel)
    channel_title = ""
    channel_language = ""
    for ch in channel_children:
        local = _local_name(ch.tag)
        if local == "title" and not channel_title:
            channel_title = (ch.text or "").strip()
        elif local == "language" and not channel_language:
            channel_language = (ch.text or "").strip()

    entries: list[FeedEntryHit] = []
    seen_keys: set[str] = set()

    for child in channel_children:
        if child.tag != "item":
            continue
        item = child
        # Extract fields using list(item) snapshot
        item_children = list(item)
        # Sprint 8BE: content:encoded is first-choice rich content (use first child only as fallback for title)
        title = ""
        content_encoded = ""
        link = ""
        description = ""
        pub_date_raw = ""
        guid_raw = ""
        is_permalink = None
        entry_author = ""
        for ic in item_children:
            ln = ic.tag
            # Sprint 8BE: use _local_name for namespace-safe matching
            local = _local_name(ln)
            if local == "title":
                title = (ic.text or "").strip()
            # content:encoded uses namespace in RSS; _local_name strips namespace
            # so "{http://purl.org/rss/1.0/modules/content/}encoded" -> "encoded"
            # We distinguish from other "encoded"-named elements via original tag
            elif local == "encoded" and "content" in ln.lower():
                content_encoded = (ic.text or "").strip()
            elif local == "link":
                link = (ic.text or "").strip()
            elif local == "description":
                description = (ic.text or "").strip()
            elif local == "pubDate":
                pub_date_raw = (ic.text or "").strip()
            elif local == "guid":
                guid_raw = (ic.text or "").strip()
                attr = ic.get("isPermaLink")
                if attr is not None:
                    is_permalink = attr.lower() == "true"
            # Sprint F150H: author/dc:creator — key quality signal for downstream
            elif local == "author" and not entry_author:
                entry_author = (ic.text or "").strip()
            elif local == "creator" and not entry_author:
                entry_author = (ic.text or "").strip()
        # Sprint 8BE: fallback for title if it was never set (item had no <title> element)
        if not title and item_children:
            title = (item_children[0].text or "").strip()

        published_ts = _parse_published_ts(pub_date_raw)

        # Determine entry_url
        guid_is_url = bool(guid_raw) and (is_permalink is True or is_permalink is None)
        if guid_is_url:
            entry_url = _normalize_url(guid_raw)
        else:
            entry_url = _normalize_url(link)

        if guid_raw:
            dedup_key = _entry_dedup_key(entry_url, title, pub_date_raw, guid_raw, is_permalink)
        else:
            dedup_key = _entry_dedup_key(entry_url, title, pub_date_raw, None, None)

        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)

        entries.append(
            FeedEntryHit(
                feed_url=feed_url,
                entry_url=entry_url or "",
                title=title or "",
                summary=description or "",
                published_raw=pub_date_raw or "",
                published_ts=published_ts,
                source=_SOURCE,
                rank=len(entries),
                retrieved_ts=retrieved_ts,
                entry_hash=_entry_hash(title or "", pub_date_raw or ""),
                # Sprint 8BE: rich_content = content:encoded if present, else ""
                rich_content=content_encoded or "",
                # Sprint F150H: feed-level + author metadata
                entry_author=entry_author,
                feed_title=channel_title,
                feed_language=channel_language,
            )
        )

    return entries


def _parse_atom(root, feed_url: str, retrieved_ts: float) -> list[FeedEntryHit]:
    """
    Parse Atom 1.0 feed.

    Atom 1.0 structure:
      feed/entry/title/link[@href][@rel=alternate or no rel]/summary/published/updated
    """
    # ---- Extract feed-level metadata once ----
    feed_title = _text_of(_find_first_child(root, "title"))
    feed_language = _text_of(_find_first_child(root, "language")) or ""

    entries: list[FeedEntryHit] = []
    seen_keys: set[str] = set()

    for entry in _iter_children(root, "entry"):
        title = _text_of(_find_first_child(entry, "title"))
        summary = _text_of(_find_first_child(entry, "summary"))
        # Sprint 8BE: extract content element for rich HTML content
        content_el = _find_first_child(entry, "content")
        rich_content = _text_of(content_el) or ""
        published_raw = _text_of(
            _find_first_child(entry, "published")
        ) or _text_of(_find_first_child(entry, "updated"))
        published_ts = _parse_published_ts(published_raw)
        # Sprint F150H: author — key quality signal for downstream
        author_el = _find_first_child(entry, "author")
        entry_author = ""
        if author_el is not None:
            entry_author = _text_of(_find_first_child(author_el, "name")) or ""

        # Find entry URL: rel="alternate" or no rel attribute, with href
        entry_url = ""
        for link_el in _iter_children(entry, "link"):
            rel = link_el.get("rel")
            href = link_el.get("href") or ""
            if rel is None or rel == "alternate":
                entry_url = _normalize_url(href)
                break
        if not entry_url:
            # Fallback: any link with href
            for link_el in _iter_children(entry, "link"):
                href = link_el.get("href") or ""
                if href:
                    entry_url = _normalize_url(href)
                    break

        dedup_key = _entry_dedup_key(entry_url, title, published_raw, None, None)

        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)

        entries.append(
            FeedEntryHit(
                feed_url=feed_url,
                entry_url=entry_url or "",
                title=title or "",
                summary=summary or "",
                published_raw=published_raw or "",
                published_ts=published_ts,
                source=_SOURCE,
                rank=len(entries),
                retrieved_ts=retrieved_ts,
                entry_hash=_entry_hash(title or "", published_raw or ""),
                # Sprint 8BE: rich_content = content element if present, else ""
                rich_content=rich_content or "",
                # Sprint F150H: feed-level + author metadata
                entry_author=entry_author,
                feed_title=feed_title,
                feed_language=feed_language,
            )
        )

    return entries


def _report_parse_mode(out_list: list[str] | None, mode: str) -> None:
    """Append parse mode label to the out list if provided. Never raises."""
    if out_list is not None:
        out_list.append(mode)


def _parse_feed_xml(
    xml_text: str,
    feed_url: str,
    retrieved_ts: float,
    _parse_mode_out: list[str] | None = None,
    _feed_type_out: list[str] | None = None,
) -> list[FeedEntryHit]:
    """
    Detect feed type and parse accordingly.
    Returns list of FeedEntryHit or empty list on failure.

    Recovery order (Sprint 8AR):
    1. Primary defusedxml on raw input.
    2. Sanitized copy retry via defusedxml (removes DOCTYPE/ENTITY,
       replaces benign HTML named entities).
    3. Sanitized copy via stdlib ET fallback.
    4. Fail-soft.

    ``_parse_mode_out``, if provided, is appended with the parse mode
    label for observability (never raises, never affects results).

    ``_feed_type_out``, if provided, is appended with the matched feed type
    ("rss" | "feed") when a recognized root tag is found, even if the resulting
    entry list is empty. This distinguishes a valid-but-empty feed from an
    unrecognized root tag. Never raises.
    """
    # ---- Step 1: primary defusedxml on raw input ----
    try:
        root = _DET.fromstring(xml_text)
        if root is not None:
            _report_parse_mode(_parse_mode_out, _ParseMode.RAW_DEFUSEDXML)
            local_root = _local_name(root.tag)
            if local_root == "rss":
                if _feed_type_out is not None:
                    _feed_type_out.append("rss")
                return _parse_rss(root, feed_url, retrieved_ts)
            elif local_root == "feed":
                if _feed_type_out is not None:
                    _feed_type_out.append("feed")
                return _parse_atom(root, feed_url, retrieved_ts)
            else:
                return []
    except Exception:
        pass

    # ---- Step 2: sanitized defusedxml retry ----
    sanitized = _safe_sanitize_xml(xml_text)
    try:
        root = _DET.fromstring(sanitized)
        if root is not None:
            _report_parse_mode(_parse_mode_out, _ParseMode.SANITIZED_DEFUSEDXML)
            local_root = _local_name(root.tag)
            if local_root == "rss":
                if _feed_type_out is not None:
                    _feed_type_out.append("rss")
                return _parse_rss(root, feed_url, retrieved_ts)
            elif local_root == "feed":
                if _feed_type_out is not None:
                    _feed_type_out.append("feed")
                return _parse_atom(root, feed_url, retrieved_ts)
            else:
                return []
    except Exception:
        pass

    # ---- Step 3: sanitized stdlib ET fallback ----
    try:
        root = _ET.fromstring(sanitized)
        if root is not None:
            _report_parse_mode(_parse_mode_out, _ParseMode.SANITIZED_STDLIB_FALLBACK)
            local_root = _local_name(root.tag)
            if local_root == "rss":
                if _feed_type_out is not None:
                    _feed_type_out.append("rss")
                return _parse_rss(root, feed_url, retrieved_ts)
            elif local_root == "feed":
                if _feed_type_out is not None:
                    _feed_type_out.append("feed")
                return _parse_atom(root, feed_url, retrieved_ts)
            else:
                return []
    except Exception:
        pass

    # ---- Step 4: fail-soft ----
    _report_parse_mode(_parse_mode_out, _ParseMode.FINAL_FAIL)
    return []


# ---------------------------------------------------------------------------
# F170B — Canonical source-accessibility mapping from fetch-layer truth
# ---------------------------------------------------------------------------


def _map_fetch_result_to_source_accessibility(result: "FetchResult") -> str | None:
    """Return canonical source_accessibility_error from fetch-layer truth (F170B).

    Priority: failure_stage + network_error_kind (F169B structured fields) over
    raw error string parsing.  Returns None when the fetch error is a local
    configuration problem (URL validation) or when the source body was reached
    (body/size failures) — those are not source accessibility issues.

    Canonical values produced:
      source_dns_failure        — DNS resolution failure
      source_connect_failure    — TCP/connection refused or reset
      source_tls_failure        — TLS handshake failure
      source_timeout            — request timed out before body
      source_http_unreachable   — HTTP-level error (4xx / 5xx / content-type rejection)
    """
    failure_stage: str | None = getattr(result, "failure_stage", None)
    network_error_kind: str | None = getattr(result, "network_error_kind", None)

    # --- Structured path (F169B fields present) ---
    if failure_stage == "connection":
        if network_error_kind == "dns_error":
            return "source_dns_failure"
        if network_error_kind == "connect_error":
            return "source_connect_failure"
        if network_error_kind == "timeout":
            return "source_timeout"
        return "source_connect_failure"  # kind unknown, but connection-level

    if failure_stage == "tls":
        return "source_tls_failure"

    if failure_stage == "http":
        return "source_http_unreachable"

    if failure_stage in ("validation", "body", "size"):
        # validation = bad URL (config error, not source inaccessible)
        # body/size   = body started arriving (source was reachable)
        return None

    # --- Legacy / unstructured path (no failure_stage set) ---
    err: str = result.error or ""
    if err == "timeout":
        return "source_timeout"
    if err.startswith("fetch_error:"):
        return "source_connect_failure"
    if err.startswith("retryable:") or err.startswith("content_type_rejected:"):
        return "source_http_unreachable"
    if err.startswith("url_"):
        return None  # URL validation error — not a source accessibility issue

    # Unknown / edge-case — pass raw string through as degraded truth
    return err or None


# ---------------------------------------------------------------------------
# Public API — Feed Fetching
# ---------------------------------------------------------------------------


async def async_fetch_feed_entries(
    feed_url: str,
    max_entries: int = 25,  # F150H: 20→25: more signal per fetch without extra network cost
    timeout_s: float = 35.0,
    max_bytes: int = 2_000_000,
) -> FeedBatchResult:
    """
    Fetch and parse a RSS 2.0 or Atom 1.0 feed.

    Parameters
    ----------
    feed_url:
        URL of the feed.
    max_entries:
        Maximum entries to return (default 20, hard cap 100).
    timeout_s:
        Fetch timeout passed to 8AD async_fetch_public_text.
    max_bytes:
        Maximum bytes to accept from 8AD fetch.

    Returns
    -------
    FeedBatchResult
        entries tuple (possibly empty) on success,
        or entries=() with error string on failure.
    """
    # Clamp hard cap
    max_entries = min(max(max_entries, 1), _MAX_ENTRIES_HARD)

    # Import here to keep this module import-light
    from hledac.universal.fetching.public_fetcher import async_fetch_public_text

    retrieved_ts = time.time()

    # Network call via 8AD
    try:
        result = await async_fetch_public_text(
            feed_url, timeout_s=timeout_s, max_bytes=max_bytes
        )
    except CancelledError:
        raise  # never swallow

    # Handle fetch-level errors fail-soft
    if result.error or result.text is None:
        # F170B: canonical source accessibility mapping — use failure_stage/network_error_kind
        # instead of passing raw error string. error= keeps raw bucket for routing;
        # source_accessibility_error= carries the canonical accessibility truth.
        fetch_err = result.error or "fetch_returned_none"
        src_accessibility = _map_fetch_result_to_source_accessibility(result)
        return FeedBatchResult(
            feed_url=feed_url,
            entries=(),
            error=fetch_err,
            source_accessibility_error=src_accessibility,
        )

    # Guard removed by Sprint 8AR: DOCTYPE/ENTITY handling is now done
    # via sanitized recovery inside _parse_feed_xml.

    # Parse — track feed type so we can distinguish valid-but-empty from unrecognized root
    _feed_type: list[str] = []
    parsed = _parse_feed_xml(result.text, feed_url, retrieved_ts, _feed_type_out=_feed_type)
    had_valid_feed_type: bool = bool(_feed_type)

    if not parsed and result.text.strip():
        stripped = result.text.strip()
        starts_html = stripped.startswith(("<!DOCTYPE", "<html"))

        # F169C: redirect drift fix — fetch layer carries final_url + redirected flag,
        # not redirected_to. Use final_url != url AND redirected flag for truth.
        final_url = getattr(result, "final_url", None) or None
        redirected_flag = getattr(result, "redirected", False) or False
        is_redirect = redirected_flag or (final_url and final_url != result.url and starts_html)
        if is_redirect and starts_html:
            error_tag = "redirected_non_feed_endpoint"
        elif starts_html:
            error_tag = "fetch_returned_html_not_xml"
        elif had_valid_feed_type:
            # F164B: valid RSS/Atom root found but zero entries
            error_tag = "valid_empty_feed"
        else:
            error_tag = "xml_parse_error"

        # F169C: propagate source-accessibility error when redirect detected
        src_err: str | None = None
        if error_tag == "redirected_non_feed_endpoint":
            src_err = "source_redirected_to_non_feed"

        return FeedBatchResult(
            feed_url=feed_url,
            entries=(),
            error=error_tag,
            source_accessibility_error=src_err,
        )

    # Deduplicate (preserve-first within this feed)
    seen_keys: set[str] = set()
    deduped: list[FeedEntryHit] = []
    for entry in parsed:
        key = _entry_dedup_key(
            entry.entry_url,
            entry.title,
            entry.published_raw,
            None,
            None,
        )
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(entry)

    # ---- F150J: Score + pre-filter + deterministic rerank ----
    scored: list[tuple[FeedEntryHit, float, float, str, str, float, float, str, str, float, str]] = []
    all_filtered_out: bool = len(deduped) > 0  # True if we had entries but all got pre-filtered

    for entry in deduped:
        # Light pre-filter: skip obvious noise
        title_len = len(entry.title.strip())
        summary_len = len(entry.summary.strip())
        rc_len = len(entry.rich_content) if entry.rich_content else 0

        # Noise: no title AND no summary AND no rich_content → skip
        if title_len == 0 and summary_len == 0 and rc_len == 0:
            continue

        # We got at least one entry that passed the pre-filter
        all_filtered_out = False
        freshness_score, freshness_tier = _compute_freshness(
            entry.published_ts, retrieved_ts
        )
        quality_score = _compute_quality(entry)

        # Combined score: freshness权重0.55, quality权重0.45
        combined = freshness_score * 0.55 + quality_score * 0.45

        # timestamp_reliability (additive signal #1): how trustworthy is this entry's timestamp?
        # penalize future/missing, reward bounded-age recent entries
        if entry.published_ts is None:
            ts_rel = 0.1
        elif freshness_tier == "future":
            ts_rel = 0.15
        elif freshness_tier == "unknown":
            ts_rel = 0.2
        elif freshness_score >= 1.0:
            ts_rel = 1.0
        elif freshness_score >= 0.85:
            ts_rel = 0.9
        elif freshness_score >= 0.6:
            ts_rel = 0.6
        else:
            ts_rel = 0.3

        # metadata_richness_band (additive signal #2): structured metadata density
        richness = 0
        if entry.entry_author.strip():
            richness += 1
        if entry.feed_title.strip():
            richness += 1
        if entry.feed_language.strip():
            richness += 1
        if rc_len > 500:
            richness += 2
        elif rc_len > 100:
            richness += 1
        if summary_len > 50:
            richness += 1
        if title_len > 0:
            richness += 1
        richness_band: str = "low" if richness <= 2 else ("medium" if richness <= 4 else "high")

        # entry_usefulness_band (additive signal #3): derived from combined score quintiles
        usefulness_band: str = (
            "high" if combined >= 0.8
            else ("medium" if combined >= 0.55
                  else ("low" if combined >= 0.3 else "noise"))
        )

        # F150K: source_priority_bias — credibility lift from curated seed domain hints.
        # Fail-soft: 0.0 when no signal. Range [0.0, 0.15].
        # High-priority OSINT domains (CISA, NVD, Krebs, SANS ISC) get small positive bias.
        # This is additive with combined so recent+high+trustworthy rises above stale+high.
        spb = 0.0
        feed_url_lower = entry.feed_url.lower()
        if "cisa.gov" in feed_url_lower or "nvd.nist.gov" in feed_url_lower:
            spb = 0.15
        elif "krebs" in feed_url_lower or "sans.edu" in feed_url_lower:
            spb = 0.12
        elif "abuse.ch" in feed_url_lower or "urlhaus" in feed_url_lower:
            spb = 0.10
        elif "welivesecurity" in feed_url_lower:
            spb = 0.08
        elif "bleepingcomputer" in feed_url_lower or "thehackersnews" in feed_url_lower:
            spb = 0.06

        # F150K: time_signal_reason — lightweight timestamp quality label
        if entry.published_ts is None:
            time_signal = "no_timestamp"
        elif freshness_tier == "future":
            time_signal = "future_ts_penalized"
        elif freshness_tier == "unknown":
            time_signal = "unparseable_ts"
        elif ts_rel >= 1.0:
            time_signal = "ts_recent_high_conf"
        elif ts_rel >= 0.9:
            time_signal = "ts_fresh_high_conf"
        else:
            time_signal = "ts_aged_low_conf"

        scored.append((
            entry, freshness_score, quality_score, freshness_tier, "", combined,
            ts_rel, richness_band, usefulness_band, spb, time_signal,
        ))

    # Sort: highest combined score desc; preserve-first tie-break via stable sort
    # F150K: source_priority_bias is additive bias — fold into sort key.
    # Python sort is stable so equal keys preserve insertion order (preserve-first).
    scored.sort(key=lambda x: -(x[5] + x[9]))

    # Clamp to max_entries and rebuild with scoring metadata
    entries: list[FeedEntryHit] = []
    for rank, (entry, freshness_score, quality_score, freshness_tier, _,
               combined, ts_rel, richness_band, usefulness_band,
               spb, time_signal) in enumerate(scored[:max_entries]):

        # Determine selection_reason
        if freshness_tier == "future":
            reason = "future_timestamp"
        elif freshness_tier == "unknown":
            reason = "missing_timestamp"
        elif freshness_score >= 1.0:
            reason = "recent_high_quality" if quality_score >= 0.5 else "recent"
        elif freshness_score >= 0.85:
            reason = "fresh_high_quality" if quality_score >= 0.5 else "fresh"
        elif quality_score >= 0.6:
            reason = "quality_signal"
        elif quality_score >= 0.3:
            reason = "moderate_quality"
        else:
            reason = "aged_low_quality"

        # Embed additive signals into selection_reason (downstream-readable)
        # source_quality_hint baked into reason: rich metadata → "enhanced_" prefix
        has_author = bool(entry.entry_author.strip())
        has_lang = bool(entry.feed_language.strip())
        is_enhanced = has_author and has_lang or richness_band == "high"
        if is_enhanced and not reason.startswith("enhanced_"):
            reason = "enhanced_" + reason

        # Append downstream-significant bands to reason (F150K adds time_signal)
        reason = f"{reason}|ts_rel={ts_rel:.2f}|richness={richness_band}|usefulness={usefulness_band}|src_bias={spb:.2f}|ts_signal={time_signal}"

        # Build final entry with additive metadata (F150J + F150K delta)
        entries.append(
            FeedEntryHit(
                feed_url=entry.feed_url,
                entry_url=entry.entry_url,
                title=entry.title,
                summary=entry.summary,
                published_raw=entry.published_raw,
                published_ts=entry.published_ts,
                source=entry.source,
                rank=rank,
                retrieved_ts=entry.retrieved_ts,
                entry_hash=entry.entry_hash,
                rich_content=getattr(entry, "rich_content", "") or "",
                entry_author=getattr(entry, "entry_author", "") or "",
                feed_title=getattr(entry, "feed_title", "") or "",
                feed_language=getattr(entry, "feed_language", "") or "",
                freshness_score=freshness_score,
                quality_score=quality_score,
                freshness_tier=freshness_tier,
                selection_reason=reason,
                source_priority_bias=spb,
                time_signal_reason=time_signal,
            )
        )

    # ---- F164B: propagate parsed-but-filtered signal ----
    if all_filtered_out and entries:
        # This is a logic guard: scored entries that passed pre-filter
        # should never result in zero entries here, but track it anyway.
        pass
    error_tag: str | None = None
    if not entries and all_filtered_out:
        error_tag = "parsed_but_filtered"
    elif not entries and had_valid_feed_type:
        # F164B: valid RSS/Atom root found but zero entries after parsing
        error_tag = "valid_empty_feed"
    # else: entries found → no error, normal success

    return FeedBatchResult(feed_url=feed_url, entries=tuple(entries), error=error_tag)


# ---------------------------------------------------------------------------
# Sprint 8AJ — HTML Feed Discovery
# ---------------------------------------------------------------------------


class _FeedLinkParser(HTMLParser):
    """
    Lightweight HTMLParser that extracts <link rel="alternate"> feed candidates.

    Fail-soft: collects partial hits even if parsing fails mid-document.
    """

    def __init__(self) -> None:
        super().__init__()
        self._hits: list[dict[str, str]] = []
        self._base_href: str | None = None
        self._error: str | None = None

    @property
    def hits(self) -> list[dict[str, str]]:
        return self._hits

    @property
    def base_href(self) -> str | None:
        return self._base_href

    @property
    def parse_error(self) -> str | None:
        """Return the first parse error message, if any."""
        return self._error

    def error(self, message: str) -> None:
        # HTMLParser.error() is called on parse failures.
        # Store only the first error for diagnostics.
        if self._error is None:
            self._error = message

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag == "base":
            # <base href="..."> — take the first valid one
            if self._base_href is None:
                for name, value in attrs:
                    if name == "href" and value and value.strip():
                        self._base_href = value.strip()
                        break
            return

        if tag != "link":
            return

        # Build attr dict (lowercase keys, preserve case of values)
        attr_dict: dict[str, str] = {}
        for name, value in attrs:
            if name is not None and value is not None:
                attr_dict[name.lower()] = value

        rel = attr_dict.get("rel", "")
        # Normalize rel to lowercase for matching
        rel_lower = rel.lower()
        if "alternate" not in rel_lower:
            return

        href = attr_dict.get("href", "")
        if not href or not href.strip():
            return

        # Reject fragment-only href before urljoin
        href_stripped = href.strip()
        if href_stripped.startswith("#"):
            return

        # Reject non-http schemes
        scheme = urllib.parse.urlparse(href_stripped).scheme.lower()
        if scheme and scheme not in ("http", "https"):
            return

        feed_type = attr_dict.get("type", "").lower()
        title = attr_dict.get("title", "") or ""

        self._hits.append(
            {
                "href": href,
                "type": feed_type,
                "title": title,
            }
        )

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        # e.g. <link ... /> — same logic as starttag
        self.handle_starttag(tag, attrs)


def _resolve_feed_href(
    raw_href: str,
    base_url: str,
) -> str:
    """
    Resolve a raw href against a base URL.

    If raw_href is already absolute (has scheme), return as-is after
    basic normalization. Otherwise use urllib.parse.urljoin.
    Finally strip any fragment and normalize scheme+host to lowercase.
    """
    raw_href = raw_href.strip()
    if not raw_href:
        return ""

    # Fast path: already absolute
    parsed = urllib.parse.urlparse(raw_href)
    if parsed.scheme in ("http", "https"):
        # Strip fragment
        resolved = parsed._replace(fragment="").geturl()
        return _normalize_url(resolved)

    # Resolve relative against base_url
    resolved = urllib.parse.urljoin(base_url, raw_href)
    # Strip fragment
    resolved_parsed = urllib.parse.urlparse(resolved)
    resolved = resolved_parsed._replace(fragment="").geturl()
    return _normalize_url(resolved)


def discover_feed_urls_from_html(
    page_url: str,
    html_text: str,
    max_candidates: int = _MAX_CANDIDATES_DEFAULT,
) -> FeedDiscoveryBatchResult:
    """
    Discover RSS/Atom feed URLs from an HTML page's <link> tags.

    Only considers ``<link rel="alternate">`` tags with a feed-compatible
    MIME type. Relative hrefs are resolved using the page's ``<base href>``
    if present, otherwise against ``page_url``.

    Parameters
    ----------
    page_url:
        URL of the HTML page (used as base for relative href resolution).
    html_text:
        Raw HTML content of the page.
    max_candidates:
        Maximum number of feed candidates to return (hard cap 20).

    Returns
    -------
    FeedDiscoveryBatchResult
        ``hits`` tuple of ``FeedDiscoveryHit`` ordered by confidence (high
        first), then preserve-first. ``error`` is set only on parse failure
        that prevents any extraction.
    """
    # Clamp max_candidates
    max_candidates = max(1, min(max_candidates, _MAX_CANDIDATES_HARD))

    parser = _FeedLinkParser()
    parse_error: str | None = None

    try:
        parser.feed(html_text)
    except Exception as e:
        parse_error = str(e)

    base_href = parser.base_href
    base_url = base_href if base_href else page_url

    seen_urls: set[str] = set()
    hits: list[FeedDiscoveryHit] = []
    discovered_ts = time.time()

    for hit_dict in parser.hits:
        raw_href = hit_dict["href"]
        feed_type = hit_dict["type"]
        title = hit_dict["title"]

        # Determine confidence
        if feed_type in _FEED_TYPES_HIGH:
            confidence = 1.0
        elif feed_type in _FEED_TYPES_LOW:
            confidence = 0.5
        else:
            # Unknown or missing type — skip
            continue

        resolved = _resolve_feed_href(raw_href, base_url)
        if not resolved:
            continue

        # Scheme guard: only http/https
        parsed_resolved = urllib.parse.urlparse(resolved)
        if parsed_resolved.scheme not in ("http", "https"):
            continue

        # Dedup preserve-first
        if resolved in seen_urls:
            continue
        seen_urls.add(resolved)

        hits.append(
            FeedDiscoveryHit(
                page_url=page_url,
                feed_url=resolved,
                title=title or "",
                feed_type=feed_type,
                confidence=confidence,
                source="link_tag",
                discovered_ts=discovered_ts,
            )
        )

        if len(hits) >= max_candidates:
            break

    # If we found hits, clear parse error (partial is OK)
    if hits:
        parse_error = None
    elif parse_error:
        # No hits and had a parse error — signal complete failure
        return FeedDiscoveryBatchResult(
            page_url=page_url,
            hits=(),
            error=f"html_parse_error:{parse_error}",
        )

    # Sort: high confidence first, preserve-first for same confidence
    hits.sort(key=lambda h: -h.confidence)

    return FeedDiscoveryBatchResult(
        page_url=page_url,
        hits=tuple(hits),
        error=None,
    )


async def async_discover_feed_urls(
    page_url: str,
    timeout_s: float = 35.0,
    max_bytes: int = 2_000_000,
    max_candidates: int = _MAX_CANDIDATES_DEFAULT,
) -> FeedDiscoveryBatchResult:
    """
    Thin async wrapper: fetch an HTML page via 8AD and discover feed URLs.

    The CPU-bound HTML parsing is offloaded to a thread pool so it never
    blocks the event loop.

    Fail-soft behaviour:
    - Fetch error → empty hits + error string.
    - Non-HTML content type → empty hits + error string.
    - ``CancelledError`` is re-raised and never swallowed.

    Parameters
    ----------
    page_url:
        URL of the HTML page to fetch and analyse.
    timeout_s:
        Fetch timeout passed to 8AD.
    max_bytes:
        Maximum bytes to accept from 8AD.
    max_candidates:
        Passed through to ``discover_feed_urls_from_html``.
    """
    # Import here to keep import surface clean
    from hledac.universal.fetching.public_fetcher import async_fetch_public_text

    try:
        result = await async_fetch_public_text(
            page_url, timeout_s=timeout_s, max_bytes=max_bytes
        )
    except CancelledError:
        raise  # never swallow

    # Fail-soft on fetch errors
    if result.error or result.text is None:
        return FeedDiscoveryBatchResult(
            page_url=page_url,
            hits=(),
            error=result.error or "fetch_returned_none",
        )

    # Reject non-HTML content types
    content_type = result.content_type.lower()
    if content_type and not (
        "text/html" in content_type
        or "application/xhtml+xml" in content_type
    ):
        return FeedDiscoveryBatchResult(
            page_url=page_url,
            hits=(),
            error=f"content_type_rejected:{content_type}",
        )

    # Offload pure-Python HTML parsing to thread pool
    batch: FeedDiscoveryBatchResult = await asyncio.to_thread(
        discover_feed_urls_from_html,
        page_url,
        result.text,
        max_candidates,
    )

    return batch


# ---------------------------------------------------------------------------
# Sprint 8AJ — Curated Seed Surface
# ---------------------------------------------------------------------------


def get_default_feed_seeds() -> tuple[FeedSeed, ...]:
    """
    Return a typed set of OSINT-relevant curated feed seeds.

    No network calls are made at import time. Priority is non-zero only
    for feeds that are primary OSINT sources; supporting feeds get 0.

    Source values:
    - ``curated_seed`` — runtime RSS/Atom feed (belongs in feed-surface processing)
    - ``topology_candidate`` — non-feed endpoint (intelligence/topology candidate only,
      excluded from RSS/Atom feed-surface processing but kept in this surface
      for source completeness and auditability)

    Runtime RSS/Atom surface: CISA HNS, NVD CVE RSS, The Hacker News, URLhaus,
    WeLiveSecurity, BleepingComputer.
    Topology/intelligence candidates: CISA KEV JSON, NVD CVE JSON, Wayback CDX,
    CommonCrawl CDX.
    """
    return (
        # ---- Runtime RSS/Atom feed surface ----
        # CISA — critical infrastructure advisories (RSS)
        FeedSeed(
            feed_url="https://www.cisa.gov/feeds/hns.xml",
            label="CISA HNS",
            source="curated_seed",
            priority=10,
        ),
        # NVD — CVE database (RSS)
        FeedSeed(
            feed_url="https://nvd.nist.gov/feeds/xml/cve/misc/nvd-rss.xml",
            label="NVD CVE RSS",
            source="curated_seed",
            priority=10,
        ),
        # The Hacker News — direct feedburner (F150H: already optimal, keep)
        FeedSeed(
            feed_url="https://feeds.feedburner.com/TheHackersNews",
            label="The Hacker News",
            source="curated_seed",
            priority=4,  # F150H: 5→4: general-sec news, not primary OSINT
        ),
        # Krebs on Security — independent investigative security journalism (F150H: new)
        FeedSeed(
            feed_url="https://krebsonsecurity.com/feed/",
            label="Krebs on Security",
            source="curated_seed",
            priority=7,  # F150H: high-quality independent voice
        ),
        # Abuse.ch URLhaus — malware URL blocklist (RSS)
        FeedSeed(
            feed_url="https://abuse.ch/feeds/urlhaus/",
            label="URLhaus",
            source="curated_seed",
            priority=10,
        ),
        # WeLiveSecurity — ESET security research (RSS)
        FeedSeed(
            feed_url="https://www.welivesecurity.com/feed/",
            label="WeLiveSecurity",
            source="curated_seed",
            priority=3,
        ),
        # BleepingComputer — security news (RSS)
        FeedSeed(
            feed_url="https://www.bleepingcomputer.com/feed/",
            label="BleepingComputer",
            source="curated_seed",
            priority=4,
        ),
        # SANS Internet Storm Center — threat intel diary (F150H: new)
        FeedSeed(
            feed_url="https://isc.sans.edu/rssfeed.xml",
            label="SANS ISC",
            source="curated_seed",
            priority=6,  # F150H: solid community-driven threat intel
        ),
        # ---- Topology/intelligence candidates (non-feed endpoints) ----
        # CISA KEV — Known Exploited Vulnerabilities catalog (JSON, not RSS/Atom)
        FeedSeed(
            feed_url="https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json",
            label="CISA KEV",
            source="topology_candidate",
            priority=10,
        ),
        # NVD CVE JSON — NVD REST API for CVEs (JSON, not RSS/Atom)
        FeedSeed(
            feed_url="https://services.nvd.nist.gov/rest/json/cves/2.0?pubStartDate=2025-01-01T00:00:00.000&pubEndDate=2025-12-31T23:59:59.999",
            label="NVD CVE JSON",
            source="topology_candidate",
            priority=8,
        ),
        # Wayback CDX — Wayback Machine CDX API (JSON, not RSS/Atom)
        FeedSeed(
            feed_url="https://web.archive.org/cdx/search/cdx?url=*.com&output=json&limit=20",
            label="Wayback CDX",
            source="topology_candidate",
            priority=1,
        ),
        # CommonCrawl CDX — CommonCrawl index endpoint (WARC index, not RSS/Atom)
        FeedSeed(
            feed_url="https://index.commoncrawl.org/CC-MAIN-2024-51-index",
            label="CommonCrawl CDX",
            source="topology_candidate",
            priority=1,
        ),
    )


# ---------------------------------------------------------------------------
# Sprint 8AT — Seed Health Truth Surface
# ---------------------------------------------------------------------------


def normalize_seed_identity(seed: FeedSeed) -> str:
    """
    Return a canonical identity string for a FeedSeed.

    Uses the URL host + path (no query/fragment) for stable identification.
    No network calls.
    """
    try:
        parsed = urllib.parse.urlparse(seed.feed_url)
        return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path}"
    except Exception:
        return seed.feed_url.lower().strip()


def get_default_feed_seed_truth() -> dict[str, Any]:
    """
    Return a truth-surface dict describing the current curated seed state.

    Intended for test and audit use. Side-effect free.

    Truth surface fields:
    - ``count`` — total number of curated seeds (runtime + topology)
    - ``runtime_rss_atom_count`` — seeds with source=curated_seed (runtime RSS/Atom feeds)
    - ``topology_candidate_count`` — seeds with source=topology_candidate
    - ``runtime_rss_atom_urls`` — sorted list of runtime RSS/Atom feed URLs
    - ``topology_candidate_urls`` — sorted list of non-feed endpoint URLs
    - ``all_urls`` — sorted list of all seed URLs
    - ``has_authenticated_reuters`` — True if Reuters feed is present (should be absent)
    """
    seeds = get_default_feed_seeds()
    runtime = [s for s in seeds if s.source == "curated_seed"]
    topology = [s for s in seeds if s.source == "topology_candidate"]
    return {
        "count": len(seeds),
        "runtime_rss_atom_count": len(runtime),
        "topology_candidate_count": len(topology),
        "runtime_rss_atom_identities": sorted(normalize_seed_identity(s) for s in runtime),
        "topology_candidate_identities": sorted(normalize_seed_identity(s) for s in topology),
        "runtime_rss_atom_urls": sorted(s.feed_url for s in runtime),
        "topology_candidate_urls": sorted(s.feed_url for s in topology),
        "all_urls": sorted(s.feed_url for s in seeds),
        "has_authenticated_reuters": any(
            "reuters.com" in s.feed_url.lower() for s in seeds
        ),
    }


# ---------------------------------------------------------------------------
# Sprint F160D — Runtime Feed Seed Surface Accessors
# ---------------------------------------------------------------------------


def get_runtime_feed_seeds() -> tuple[FeedSeed, ...]:
    """
    Return the runtime RSS/Atom feed seed surface.

    Contains ONLY ``source=curated_seed`` entries, sorted by priority descending.
    Topology candidates (JSON/WARC/CDX endpoints) are EXCLUDED.

    This is the surface that belongs in hot-path feed fetching.
    """
    seeds = get_default_feed_seeds()
    runtime = [s for s in seeds if s.source == "curated_seed"]
    runtime.sort(key=lambda s: -s.priority)
    return tuple(runtime)


def get_topology_candidates() -> tuple[FeedSeed, ...]:
    """
    Return the topology/intelligence candidate surface.

    Contains ONLY ``source=topology_candidate`` entries.
    These are non-feed endpoints (JSON APIs, CDX indexes) used for
    intelligence/topology purposes — excluded from RSS/Atom hot-path fetching.
    """
    seeds = get_default_feed_seeds()
    return tuple(s for s in seeds if s.source == "topology_candidate")


# Viability tier thresholds — derived from FeedSeed.priority field.
# These drive get_feed_viability_posture() and are part of its public contract.
# DO NOT use these values in any new scoring system — they are for tier derivation only.
VIABILITY_HIGH_PRIORITY_THRESHOLD: int = 7   # min priority for "high" tier
VIABILITY_MEDIUM_PRIORITY_THRESHOLD: int = 4  # min priority for "medium" tier


def get_feed_viability_posture() -> dict[str, Any]:
    """
    Return a derived viability posture for the runtime feed seed surface.

    Derived ONLY from existing truth fields (priority, source, label) —
    NO new parallel scoring world. No network calls.

    Posture fields:
    - ``viability_tier`` — "high" | "medium" | "low" | "degraded" | "unknown"
    - ``runtime_feed_count`` — number of runtime RSS/Atom feeds
    - ``topology_candidate_count`` — number of topology candidates
    - ``runtime_feeds`` — list of runtime (curated_seed) entries with identity, label, priority, source
      — NOTE: topology_candidates are excluded from this list; use get_topology_candidates() separately
    - ``top_priority`` — highest priority value in the runtime surface
    - ``canonical_osint_sources`` — list of labels that are primary OSINT sources
    """
    runtime = get_runtime_feed_seeds()
    topology = get_topology_candidates()

    # Compute viability_tier from priority distribution
    if not runtime:
        tier = "unknown"
    else:
        max_priority = max(s.priority for s in runtime)
        if max_priority >= VIABILITY_HIGH_PRIORITY_THRESHOLD:
            tier = "high"
        elif max_priority >= VIABILITY_MEDIUM_PRIORITY_THRESHOLD:
            tier = "medium"
        elif max_priority > 0:
            tier = "low"
        else:
            tier = "degraded"

    # Build runtime_feeds list — only from existing FeedSeed fields (no new data).
    # NOTE: this list contains ONLY curated_seed entries; topology_candidates
    # are NOT included — use get_topology_candidates() separately.
    runtime_feeds: list[dict[str, Any]] = [
        {
            "feed_url": s.feed_url,
            "label": s.label,
            "priority": s.priority,
            "source": s.source,
            "identity": normalize_seed_identity(s),
        }
        for s in runtime
    ]

    # Canonical OSINT sources: priority >= VIABILITY_HIGH_PRIORITY_THRESHOLD
    canonical_labels = [s.label for s in runtime if s.priority >= VIABILITY_HIGH_PRIORITY_THRESHOLD]

    return {
        "viability_tier": tier,
        "runtime_feed_count": len(runtime),
        "topology_candidate_count": len(topology),
        "runtime_feeds": runtime_feeds,
        "top_priority": max((s.priority for s in runtime), default=0),
        "canonical_osint_sources": canonical_labels,
    }


# ---------------------------------------------------------------------------
# Sprint 8AJ — Merge Discovered + Seeded Sources
# ---------------------------------------------------------------------------


def _normalize_for_dedup(url: str) -> str:
    """Normalize URL for deterministic merge dedup."""
    return _normalize_url(url)


def merge_feed_sources(
    discovered: tuple[FeedDiscoveryHit, ...],
    seeds: tuple[FeedSeed, ...],
) -> tuple[MergedFeedSource, ...]:
    """
    Merge discovered feed hits with curated seeds.

    Rules:
    1. Seeds have their own priority; discovered hits get priority 0.
    2. Dedup by normalized feed URL — seed URL wins over discovered URL
       when they resolve to the same normalized URL.
    3. Result is sorted: higher priority first; preserve-first for ties.
    4. All metadata (label, origin, priority) is preserved — never returns
       only a tuple of URLs.
    """
    seen_urls: dict[str, dict[str, Any]] = {}

    # Process seeds first so they take precedence on collision
    for seed in seeds:
        norm = _normalize_for_dedup(seed.feed_url)
        if norm and norm not in seen_urls:
            seen_urls[norm] = {
                "feed_url": seed.feed_url,
                "label": seed.label,
                "origin": "seed",
                "priority": seed.priority,
            }

    # Process discovered hits (seed wins on collision)
    for hit in discovered:
        norm = _normalize_for_dedup(hit.feed_url)
        if norm and norm not in seen_urls:
            label = hit.title if hit.title else norm
            seen_urls[norm] = {
                "feed_url": hit.feed_url,
                "label": label,
                "origin": "discovered",
                "priority": 0,
            }

    # Sort: higher priority first, preserve-first (dict insertion order) for ties
    sorted_items = sorted(seen_urls.values(), key=lambda x: -x["priority"])

    return tuple(
        MergedFeedSource(
            feed_url=item["feed_url"],
            label=item["label"],
            origin=item["origin"],
            priority=item["priority"],
        )
        for item in sorted_items
    )


# =============================================================================
# Sprint 8VE D.1: CPU-heavy HTML parsing via ProcessPoolExecutor (GIL bypass)
# =============================================================================

import atexit as _atexit
import concurrent.futures as _cf

_PARSE_POOL: _cf.ProcessPoolExecutor | None = None


def _get_parse_pool() -> _cf.ProcessPoolExecutor:
    global _PARSE_POOL
    if _PARSE_POOL is None:
        _PARSE_POOL = _cf.ProcessPoolExecutor(max_workers=3)
        _atexit.register(_PARSE_POOL.shutdown, wait=False)  # cisty cleanup
    return _PARSE_POOL


def _parse_html_sync(html: str) -> list[dict]:
    """
    CPU-bound HTML parse — spouští se v process pool (GIL bypass).
    Primárně: selectolax (Rust-based, ARM64 native, 10-50× rychlejší než BS4).
    Fallback: BeautifulSoup pokud selectolax není dostupný.
    """
    results = []
    try:
        from selectolax.parser import HTMLParser
        tree = HTMLParser(html)
        for node in tree.css("a[href]"):
            href  = node.attributes.get("href", "")
            text  = node.text(strip=True)
            if href.startswith("http"):
                results.append({"url": href, "title": text[:200]})
        return results
    except ImportError:
        pass
    # Fallback: BeautifulSoup
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        if a["href"].startswith("http"):
            results.append({"url": a["href"], "title": a.get_text(strip=True)[:200]})
    return results


async def parse_html_async(html: str) -> list[dict]:
    """Async wrapper kolem _parse_html_sync — spoustí v process pool."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_get_parse_pool(), _parse_html_sync, html)
