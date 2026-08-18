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
import datetime
import logging
import re
import time
import urllib.parse
from asyncio import CancelledError
from html.parser import HTMLParser
from typing import TYPE_CHECKING, Any
import msgspec
from compat.msgspec_gc_compat import Struct
import xxhash
import xml.etree.ElementTree as _ET

# Use canonical timestamp parser from feed_parser (eliminates duplicate _parse_timestamp logic)
from hledac.universal.parsing.feed_parser import _parse_timestamp as _parse_published_ts

def _entry_hash(title: str, published_raw: str) -> str:
    """Compute deterministic xxhash of title|published_raw for entry identity."""
    return xxhash.xxh3_64(f"{title or ''}|{published_raw or ''}").hexdigest()
try:
    import defusedxml.ElementTree as _DET
except ImportError:
    import xml.etree.ElementTree as _DET
if TYPE_CHECKING:
    from hledac.universal.fetching.public_fetcher import FetchResult
logger = logging.getLogger(__name__)

class FeedEntryHit(Struct, frozen=True):
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
    entry_hash: str = ''
    rich_content: str = ''
    entry_author: str = ''
    feed_title: str = ''
    feed_language: str = ''
    freshness_score: float = 0.0
    quality_score: float = 0.0
    freshness_tier: str = ''
    selection_reason: str = ''
    source_priority_bias: float = 0.0
    time_signal_reason: str = ''

class FeedBatchResult(Struct, frozen=True):
    """Result of fetching and parsing one feed."""
    feed_url: str
    entries: tuple[FeedEntryHit, ...]
    error: str | None = None
    source_accessibility_error: str | None = None
    raw_xml: str | None = None

class FeedDiscoveryHit(Struct, frozen=True):
    """Single feed URL discovered from an HTML page."""
    page_url: str
    feed_url: str
    title: str
    feed_type: str
    confidence: float
    source: str
    discovered_ts: float

class FeedDiscoveryBatchResult(Struct, frozen=True):
    """Result of discovering feed URLs from an HTML page."""
    page_url: str
    hits: tuple[FeedDiscoveryHit, ...]
    error: str | None = None

class FeedSeed(Struct, frozen=True):
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

class MergedFeedSource(Struct, frozen=True):
    """A feed source after merging discovered and seeded sources."""
    feed_url: str
    label: str
    origin: str
    priority: int
_SOURCE: str = 'rss_atom'
_MAX_ENTRIES_HARD: int = 200
_XML_ENTITY_RE: re.Pattern[str] = re.compile('<!ENTITY|<!DOCTYPE', re.IGNORECASE)
_ISO_Z_RE: re.Pattern[str] = re.compile('Z$')
_FEED_TYPES_HIGH: tuple[str, ...] = ('application/rss+xml', 'application/atom+xml')
_FEED_TYPES_LOW: tuple[str, ...] = ('application/xml', 'text/xml')
_MAX_CANDIDATES_DEFAULT: int = 10
_MAX_CANDIDATES_HARD: int = 20

def _local_name(tag: str) -> str:
    """Strip namespace prefix, return local name."""
    if tag is None:
        return ''
    idx = tag.rfind('}')
    if idx >= 0:
        return tag[idx + 1:]
    return tag

def _find_first_child(parent, localname: str) -> Any | None:
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

def _normalize_url(raw: str | None) -> str:
    """Normalize URL for dedup: lowercase scheme+host, strip lone ?."""
    if not raw:
        return ''
    raw = raw.strip()
    if not raw:
        return ''
    try:
        parsed = urllib.parse.urlparse(raw)
        normalized = parsed._replace(scheme=parsed.scheme.lower(), netloc=parsed.netloc.lower()).geturl()
        if normalized.endswith('?'):
            normalized = normalized[:-1]
        return normalized
    except Exception as e:
        logger.debug(f"[RSS/Atom] URL normalization failed for '{raw}': {e}")
        return raw.strip()

def _text_of(element) -> str:
    """Return element text or empty string."""
    if element is None:
        return ''
    text = element.text
    if text is None:
        return ''
    return text.strip()

_TIER_RECENT_MAX: float = 3 * 86400
_TIER_FRESH_MAX: float = 14 * 86400
_TIER_AGED_MAX: float = 60 * 86400
_TIER_STALE_MAX: float = 365 * 86400
_FUTURE_GAP_MAX: float = 3600 * 6

def _compute_freshness(published_ts: float | None, retrieved_ts: float) -> tuple[float, str]:
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
        return (0.05, 'unknown')
    age = retrieved_ts - published_ts
    if age < 0:
        future_gap = abs(age)
        if future_gap > _FUTURE_GAP_MAX:
            penalty = max(0.05, 0.3 * (1 - future_gap / (86400 * 7)))
            return (penalty, 'future')
        else:
            return (0.95, 'recent')
    if age <= _TIER_RECENT_MAX:
        return (1.0, 'recent')
    if age <= _TIER_FRESH_MAX:
        return (0.85, 'fresh')
    if age <= _TIER_AGED_MAX:
        return (0.6, 'aged')
    if age <= _TIER_STALE_MAX:
        return (0.3, 'stale')
    return (0.1, 'unknown')
_SPAM_DOMAIN_PATTERNS: tuple[str, ...] = ('blogspot.com', 'wordpress.com', 'livejournal.com', 'tumblr.com', 'blogspot.ru', 'wp.ru', 'site90.net', '000webhost.com', '110mb.com', 'freesitehost.com', 'blogcindi.com', 'bloggen.ru', 'blogrund.com', 'wordpress.org.ru')

def _is_spam_domain(url: str) -> bool:
    """Return True for known low-quality / parked / placeholder domains."""
    if not url:
        return False
    try:
        parsed = urllib.parse.urlparse(url)
        netloc = parsed.netloc.lower()
        return any((p in netloc for p in _SPAM_DOMAIN_PATTERNS))
    except Exception as e:
        logger.debug(f"[RSS/Atom] Spam domain check failed for '{url}': {e}")
        return False
_SEO_SPAM_TITLE_RE = re.compile('(?:\\b\\w+\\b\\s*){30,}', re.IGNORECASE)
_REPEATED_DOTS_RE = re.compile('^\\.{3,}$')
_TEMPLATE_NOISE_RE = re.compile('^\\s*(?:title|untitled|article|post|page)\\s*$', re.IGNORECASE)

def _is_seo_spam_title(title: str) -> bool:
    """Return True for SEO-optimized / template noise titles."""
    t = title.strip()
    if not t or len(t) < 3:
        return True
    if _TEMPLATE_NOISE_RE.match(t):
        return True
    if _REPEATED_DOTS_RE.match(t):
        return True
    if _SEO_SPAM_TITLE_RE.match(t):
        return True
    return False
_SNIPPET_TEMPLATE_RE = re.compile('^\\s*[\\w\\s\\-]+\\s*[•·|\\-\\"\']\\s*[\\w\\s\\-]+\\s*$')

def _is_template_snippet(snippet: str, title: str) -> bool:
    """Return True for generic template snippet that adds no value."""
    s = snippet.strip()
    if not s:
        return False
    if _SNIPPET_TEMPLATE_RE.match(s) and len(s) < 80:
        return True
    return False

# ── Score accumulator helpers for _compute_quality ────────────────────────────


def _score_url_quality(entry_url: str | None) -> float:
    """Score URL structure quality (scheme + path depth)."""
    if not entry_url:
        return 0.0
    try:
        parsed = urllib.parse.urlparse(entry_url)
        scheme = parsed.scheme.lower()
        path = parsed.path.rstrip('/')
        if scheme and scheme not in ('http', 'https'):
            return -0.15
        if path.count('/') >= 2:
            return 0.08
        if path.count('/') == 1 and len(path) > 1:
            return 0.04
    except Exception as e:
        logger.debug(f"[RSS/Atom] URL quality parse failed for '{entry_url}': {e}")
    return 0.0


def _score_content_length(rich_content: Any) -> float:
    """Score rich_content length (more content = higher quality)."""
    if not rich_content:
        return 0.0
    rc_len = len(rich_content)
    if rc_len > 500:
        return 0.35
    if rc_len > 100:
        return 0.2
    return 0.1


def _score_summary_quality(summary: str, title: str) -> float:
    """Score summary word count if not a template snippet."""
    if _is_template_snippet(summary, title):
        return 0.0
    words = len(summary.split())
    if words >= 30:
        return 0.15
    if words >= 10:
        return 0.08
    if words > 0:
        return 0.03
    return 0.0


def _score_title_length(title: str) -> float:
    """Score title length (optimal range 30-120 chars)."""
    title_len = len(title.strip())
    if 30 <= title_len <= 120:
        return 0.12
    if title_len > 0:
        return 0.04
    return 0.0


def _compute_quality(entry: FeedEntryHit) -> float:
    """
    Compute quality_score (0.0-1.0) from entry metadata.
    Uses score accumulator pattern with dimension-specific helpers.

    F178E additions:
    - Spam domain penalty (max -0.25)
    - SEO spam title penalty (-0.3)
    - Template snippet penalty (-0.1)
    """
    score = 0.0
    # Penalty: spam signals
    if _is_spam_domain(entry.entry_url):
        score -= 0.25
    if _is_seo_spam_title(entry.title):
        score -= 0.3
    if _is_template_snippet(entry.summary, entry.title):
        score -= 0.1
    # Content quality scoring (dimension helpers)
    score += _score_content_length(entry.rich_content)
    score += _score_summary_quality(entry.summary, entry.title)
    score += _score_title_length(entry.title)
    # Metadata presence scoring
    if entry.entry_author.strip():
        score += 0.12
    if entry.feed_language.strip():
        score += 0.08
    if entry.feed_title.strip():
        score += 0.05
    # URL structure scoring
    score += _score_url_quality(entry.entry_url)
    return max(min(score, 1.0), 0.0)

def _is_xml_entity_dangerous(text: str) -> bool:
    """Check for ENTITY / DOCTYPE declarations that could be XML bombs."""
    if not text:
        return False
    return bool(_XML_ENTITY_RE.search(text))

def _entry_dedup_key(entry_url: str, title: str, published_raw: str, guid_raw: str | None, is_permalink: bool | None) -> str:
    """
    Build stable dedup key following RSS identity priority:
    guid (if permalink or no attribute) > link > fallback(title|published_raw).
    """
    if guid_raw:
        if is_permalink or is_permalink is None:
            return f'g:{guid_raw}'
        return f'gf:{guid_raw}'
    if entry_url:
        return f'u:{entry_url}'
    return f'f:{title.lower().strip()}|{published_raw}'
class _ParseMode:
    """Parse-mode observability labels for internal/tracking use."""
    RAW_DEFUSEDXML = 'raw_defusedxml'
    SANITIZED_DEFUSEDXML = 'sanitized_defusedxml'
    SANITIZED_STDLIB_FALLBACK = 'sanitized_stdlib_fallback'
    FINAL_FAIL = 'final_fail'
_BENIGN_HTML_ENTITIES: tuple[tuple[str, str], ...] = (('nbsp', '\xa0'), ('ndash', '–'), ('mdash', '—'), ('ldquo', '“'), ('rdquo', '”'), ('lsquo', '‘'), ('rsquo', '’'), ('hellip', '…'))

# Module-level constants for XML sanitization (computed once, reduces memory churn)
_SANITIZE_PREDEFINED: frozenset[str] = frozenset({'amp', 'lt', 'gt', 'quot', 'apos'})
_SANITIZE_BENIGN_NAMES: frozenset[str] = frozenset((name for name, _ in _BENIGN_HTML_ENTITIES))
_SANITIZE_BENIGN_PATTERNS: tuple[tuple[str, str], ...] = tuple(_BENIGN_HTML_ENTITIES)


def _skip_doctype(raw: str, i: int) -> int:
    """Skip DOCTYPE declaration including nested brackets."""
    i += 9  # skip '<!doctype'
    depth = 0
    quote_char: str | None = None
    n = len(raw)
    while i < n:
        ch = raw[i]
        if quote_char is not None:
            if ch == quote_char:
                quote_char = None
        elif ch in ('"', "'"):
            quote_char = ch
        elif ch == '[':
            depth += 1
        elif ch == ']':
            if depth > 0:
                depth -= 1
                if depth == 0 and i + 1 < n and raw[i + 1] == '>':
                    return i + 2
        elif ch == '>' and depth == 0:
            return i + 1
        i += 1
    return i


def _skip_entity_decl(raw: str, i: int) -> int:
    """Skip ENTITY declaration."""
    i += 9  # skip '<!entity'
    in_quote = False
    quote_char: str | None = None
    n = len(raw)
    while i < n:
        ch = raw[i]
        if not in_quote:
            if ch in ('"', "'"):
                in_quote = True
                quote_char = ch
            elif ch == '>' and not in_quote:
                i += 1
                return i
        elif ch == quote_char:
            in_quote = False
            quote_char = None
        i += 1
    return i


def _handle_entity_ref(raw: str, i: int, result: list[str]) -> int:
    """Handle entity reference &name; and return new position."""
    sem_idx = raw.find(';', i + 1)
    if sem_idx != -1 and sem_idx - i < 20:
        name = raw[i + 1:sem_idx]
        name_is_valid = name and name.isidentifier() and (name.lower() not in _SANITIZE_PREDEFINED)
        if name_is_valid:
            if name.lower() in _SANITIZE_BENIGN_NAMES:
                replacement = next((repl for n_, repl in _SANITIZE_BENIGN_PATTERNS if n_.lower() == name.lower()))
                result.append(replacement)
                return sem_idx + 1
            result.append(' ')
            return sem_idx + 1
    result.append(raw[i])
    return i + 1


def _is_doctype_start(raw: str, i: int, n: int) -> bool:
    """Check if position i starts a DOCTYPE declaration."""
    return n - i >= 9 and raw[i] == '<' and raw[i:i + 9].lower() == '<!doctype'


def _is_entity_decl_start(raw: str, i: int, n: int) -> bool:
    """Check if position i starts an ENTITY declaration."""
    return n - i >= 9 and raw[i] == '<' and raw[i:i + 9].lower() == '<!entity'


def _is_entity_ref(raw: str, i: int, n: int) -> bool:
    """Check if position i is an entity reference (not numeric)."""
    return n - i > 1 and raw[i] == '&' and raw[i + 1] != '#'


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
    if '<!doctype' not in raw.lower() and '<!entity' not in raw.lower() and '&' not in raw:
        return raw
    result: list[str] = []
    i = 0
    n = len(raw)
    while i < n:
        if _is_doctype_start(raw, i, n):
            i = _skip_doctype(raw, i)
        elif _is_entity_decl_start(raw, i, n):
            i = _skip_entity_decl(raw, i)
        elif _is_entity_ref(raw, i, n):
            i = _handle_entity_ref(raw, i, result)
        else:
            result.append(raw[i])
            i += 1
    return ''.join(result)

def _child_by_name(parent, localname):
    """Find first child by local name using list(parent) snapshot."""
    children = list(parent)
    for child in children:
        if child.tag == localname:
            return child
    return None


def _parse_rss_title(item_children: list) -> str:
    """Extract title from RSS item children."""
    for ic in item_children:
        if _local_name(ic.tag) == 'title':
            return (ic.text or '').strip()
    return ''


def _parse_rss_link(item_children: list) -> str:
    """Extract link from RSS item children."""
    for ic in item_children:
        if _local_name(ic.tag) == 'link':
            return (ic.text or '').strip()
    return ''


def _parse_rss_description(item_children: list) -> tuple[str, str]:
    """Extract description and content:encoded from RSS item children."""
    description = ''
    content_encoded = ''
    for ic in item_children:
        local = _local_name(ic.tag)
        if local == 'description':
            description = (ic.text or '').strip()
        elif local == 'encoded' and 'content' in ic.tag.lower():
            content_encoded = (ic.text or '').strip()
    return description, content_encoded


def _parse_rss_pubdate_and_guid(item_children: list) -> tuple[str, str, bool | None]:
    """Extract pubDate, guid and isPermaLink from RSS item children."""
    pub_date_raw = ''
    guid_raw = ''
    is_permalink = None
    for ic in item_children:
        local = _local_name(ic.tag)
        if local == 'pubDate':
            pub_date_raw = (ic.text or '').strip()
        elif local == 'guid':
            guid_raw = (ic.text or '').strip()
            is_permalink = ic.get('isPermaLink')
            if is_permalink is not None:
                is_permalink = is_permalink.lower() == 'true'
    return pub_date_raw, guid_raw, is_permalink


def _parse_rss_author(item_children: list) -> str:
    """Extract author/creator from RSS item children."""
    for ic in item_children:
        local = _local_name(ic.tag)
        if local in ('author', 'creator'):
            return (ic.text or '').strip()
    return ''


def _dedup_and_create_entry(
    title: str,
    link: str,
    description: str,
    pub_date_raw: str,
    guid_raw: str,
    entry_author: str,
    content_encoded: str,
    feed_url: str,
    seen_keys: set[str],
    retrieved_ts: float,
) -> FeedEntryHit | None:
    """Build FeedEntryHit from RSS field values with deduplication."""
    published_ts = _parse_published_ts(pub_date_raw)
    # Normalize values to avoid repeated 'or' expressions
    entry_url = link or guid_raw or ''
    summary = content_encoded or description or ''
    dedup_key = _entry_dedup_key(entry_url, title, pub_date_raw, None, None)
    if dedup_key in seen_keys:
        return None
    seen_keys.add(dedup_key)
    return FeedEntryHit(
        feed_url=feed_url,
        entry_url=entry_url,
        title=title or '',
        summary=summary,
        published_raw=pub_date_raw or '',
        published_ts=published_ts,
        source=_SOURCE,
        rank=0,
        retrieved_ts=retrieved_ts,
        entry_hash=_entry_hash(title or '', pub_date_raw or ''),
        rich_content=content_encoded or '',
        entry_author=entry_author,
    )


# Keep old name as alias for compatibility
_build_entry_from_rss_fields = _dedup_and_create_entry


def _parse_rss_item(item: Any, feed_url: str, seen_keys: set[str], retrieved_ts: float) -> FeedEntryHit | None:
    """Parse a single RSS item element."""
    item_children = list(item)
    title = _parse_rss_title(item_children)
    link = _parse_rss_link(item_children)
    description, content_encoded = _parse_rss_description(item_children)
    pub_date_raw, guid_raw, _ = _parse_rss_pubdate_and_guid(item_children)
    entry_author = _parse_rss_author(item_children)
    return _build_entry_from_rss_fields(
        title, link, description, pub_date_raw, guid_raw,
        None, entry_author, content_encoded, feed_url, seen_keys, retrieved_ts
    )

def _extract_channel_metadata(channel) -> tuple[str, str]:
    """Extract title and language from RSS channel element."""
    channel_title = ''
    channel_language = ''
    for ch in channel:
        local = _local_name(ch.tag)
        if local == 'title' and not channel_title:
            channel_title = (ch.text or '').strip()
        elif local == 'language' and not channel_language:
            channel_language = (ch.text or '').strip()
    return channel_title, channel_language


def _process_rss_items(channel, feed_url, retrieved_ts):
    """Process all RSS items and return entries with seen_keys set."""
    entries: list[FeedEntryHit] = []
    seen_keys: set[str] = set()
    for child in channel:
        if child.tag != 'item':
            continue
        entry = _parse_rss_item(child, feed_url, seen_keys, retrieved_ts)
        if entry is None:
            continue
        if not entry.title:
            item_children = list(child)
            if item_children:
                entry.title = (item_children[0].text or '').strip()
        entry.rank = len(entries)
        entries.append(entry)
    return entries, seen_keys


def _parse_rss(root, feed_url: str, retrieved_ts: float) -> list[FeedEntryHit]:
    """
    Parse RSS 2.0 feed.

    RSS 2.0 structure:
      rss/channel/item/title/link/description/pubDate/guid[@isPermaLink]
    """
    channel = _child_by_name(root, 'channel')
    if channel is None:
        return []
    channel_children = list(channel)
    channel_title, channel_language = _extract_channel_metadata(channel_children)
    entries, _ = _process_rss_items(channel_children, feed_url, retrieved_ts)
    for entry in entries:
        entry.feed_title = channel_title
        entry.feed_language = channel_language
    return entries

def _parse_atom_entry(entry, feed_url: str, retrieved_ts: float, feed_title: str, feed_language: str, seen_keys: set[str], rank: int) -> FeedEntryHit | None:
    """Parse a single Atom entry element and return FeedEntryHit or None if duplicate."""
    title = _text_of(_find_first_child(entry, 'title'))
    summary = _text_of(_find_first_child(entry, 'summary'))
    content_el = _find_first_child(entry, 'content')
    rich_content = _text_of(content_el) or ''
    published_raw = _text_of(_find_first_child(entry, 'published')) or _text_of(_find_first_child(entry, 'updated'))
    published_ts = _parse_published_ts(published_raw)
    author_el = _find_first_child(entry, 'author')
    entry_author = _text_of(_find_first_child(author_el, 'name')) if author_el is not None else ''
    entry_url = _extract_atom_entry_url(entry)
    dedup_key = _entry_dedup_key(entry_url, title, published_raw, None, None)
    if dedup_key in seen_keys:
        return None
    seen_keys.add(dedup_key)
    return FeedEntryHit(feed_url=feed_url, entry_url=entry_url or '', title=title or '', summary=summary or '', published_raw=published_raw or '', published_ts=published_ts, source=_SOURCE, rank=rank, retrieved_ts=retrieved_ts, entry_hash=_entry_hash(title or '', published_raw or ''), rich_content=rich_content or '', entry_author=entry_author, feed_title=feed_title, feed_language=feed_language)


def _extract_atom_entry_url(entry) -> str:
    """Extract the best URL from Atom entry link elements."""
    for link_el in _iter_children(entry, 'link'):
        rel = link_el.get('rel')
        href = link_el.get('href') or ''
        if rel is None or rel == 'alternate':
            return _normalize_url(href)
    for link_el in _iter_children(entry, 'link'):
        href = link_el.get('href') or ''
        if href:
            return _normalize_url(href)
    return ''


def _parse_atom(root, feed_url: str, retrieved_ts: float) -> list[FeedEntryHit]:
    """
    Parse Atom 1.0 feed.

    Atom 1.0 structure:
      feed/entry/title/link[@href][@rel=alternate or no rel]/summary/published/updated
    """
    feed_title = _text_of(_find_first_child(root, 'title'))
    feed_language = _text_of(_find_first_child(root, 'language')) or ''
    entries: list[FeedEntryHit] = []
    seen_keys: set[str] = set()
    for rank, entry in enumerate(_iter_children(root, 'entry')):
        entry_hit = _parse_atom_entry(entry, feed_url, retrieved_ts, feed_title, feed_language, seen_keys, rank)
        if entry_hit is not None:
            entries.append(entry_hit)
    return entries

def _report_parse_mode(out_list: list[str] | None, mode: str) -> None:
    """Append parse mode label to the out list if provided. Never raises."""
    if out_list is not None:
        out_list.append(mode)

def _parse_feed_xml(xml_text: str, feed_url: str, retrieved_ts: float, _parse_mode_out: list[str] | None=None, _feed_type_out: list[str] | None=None) -> list[FeedEntryHit]:
    """Detect feed type and parse accordingly. Returns list of FeedEntryHit or empty list on failure."""
    # Recovery order: raw defusedxml -> sanitized defusedxml -> sanitized stdlib -> fail-soft
    sanitized = _safe_sanitize_xml(xml_text)
    parse_strategies = [
        (xml_text, _DET.fromstring, _ParseMode.RAW_DEFUSEDXML),
        (sanitized, _DET.fromstring, _ParseMode.SANITIZED_DEFUSEDXML),
        (sanitized, _ET.fromstring, _ParseMode.SANITIZED_STDLIB_FALLBACK),
    ]
    for strategy_xml, parser_fn, mode in parse_strategies:
        entries, _ = _try_parse_with_mode(
            strategy_xml, feed_url, retrieved_ts, parser_fn,
            mode, _parse_mode_out, _feed_type_out
    )
        if entries is not None:
            return entries
    _report_parse_mode(_parse_mode_out, _ParseMode.FINAL_FAIL)
    return []

_FAILURE_STAGE_MAP = {
    ('connection', 'dns_error'): 'source_dns_failure',
    ('connection', 'connect_error'): 'source_connect_failure',
    ('connection', 'timeout'): 'source_timeout',
    ('connection', None): 'source_connect_failure',
    ('tls', None): 'source_tls_failure',
    ('http', None): 'source_http_unreachable',
    ('validation', None): None,
    ('body', None): None,
    ('size', None): None,
}

def _map_fetch_result_to_source_accessibility(result: FetchResult) -> str | None:
    """Return canonical source_accessibility_error from fetch-layer truth (F170B)."""
    failure_stage: str | None = getattr(result, 'failure_stage', None)
    network_error_kind: str | None = getattr(result, 'network_error_kind', None)
    
    # Check structured failure_stage + network_error_kind first
    key = (failure_stage, network_error_kind)
    if key in _FAILURE_STAGE_MAP:
        return _FAILURE_STAGE_MAP[key]
    
    # Fallback to raw error string parsing
    err: str = result.error or ''
    if err == 'timeout':
        return 'source_timeout'
    if err.startswith('fetch_error:'):
        return 'source_connect_failure'
    if err.startswith('retryable:') or err.startswith('content_type_rejected:'):
        return 'source_http_unreachable'
    if err.startswith('url_'):
        return None
    return err or None


def _fetch_feed_content(feed_url: str, timeout_s: float, max_bytes: int) -> tuple:
    """
    Fetch feed content and return result along with error information.

    Returns tuple of (result, error_tag, src_err).
    """
    from hledac.universal.fetching.public_fetcher import async_fetch_public_text

    # ISSUE-10 FIX: Use asyncio.Runner() instead of deprecated get_event_loop().run_until_complete()
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop - use asyncio.Runner() (Python 3.11+)
            with asyncio.Runner() as runner:
                result = runner.run(
                    async_fetch_public_text(feed_url, timeout_s=timeout_s, max_bytes=max_bytes, bypass_circuit_breaker=True)
                )
        else:
            # Running loop detected - schedule on running loop
            result = asyncio.run_coroutine_threadsafe(
                async_fetch_public_text(feed_url, timeout_s=timeout_s, max_bytes=max_bytes, bypass_circuit_breaker=True),
                loop
            ).result()
    except Exception:
        # Return None result with error on exception
        return (None, 'fetch_exception', None)

    if result.error or result.text is None:
        fetch_err = result.error or 'fetch_returned_none'
        src_accessibility = _map_fetch_result_to_source_accessibility(result)
        return (result, fetch_err, src_accessibility)

    return (result, None, None)


def _categorize_parse_failure(result, had_valid_feed_type: bool) -> tuple[str, str | None]:
    """
    Categorize parse failure and return error_tag and src_err.

    Returns tuple of (error_tag, src_err).
    """
    stripped = result.text.strip()
    starts_html = stripped.startswith(('<!DOCTYPE', '<html'))
    final_url = getattr(result, 'final_url', None) or None
    redirected_flag = getattr(result, 'redirected', False) or False
    is_redirect = redirected_flag or (final_url and final_url != result.url and starts_html)

    if is_redirect and starts_html:
        error_tag = 'redirected_non_feed_endpoint'
        src_err = 'source_redirected_to_non_feed'
    elif starts_html:
        error_tag = 'fetch_returned_html_not_xml'
        src_err = None
    elif had_valid_feed_type:
        error_tag = 'valid_empty_feed'
        src_err = None
    else:
        error_tag = 'xml_parse_error'
        src_err = None

    return (error_tag, src_err)


def _parse_and_validate_feed(result, feed_url: str, retrieved_ts: float) -> tuple:
    """
    Parse feed XML and validate results.

    Returns tuple of (parsed, had_valid_feed_type, error_tag, src_err, raw_xml).
    """
    _feed_type: list[str] = []
    parsed = _parse_feed_xml(result.text, feed_url, retrieved_ts, _feed_type_out=_feed_type)
    had_valid_feed_type: bool = bool(_feed_type)

    if not parsed and result.text.strip():
        error_tag, src_err = _categorize_parse_failure(result, had_valid_feed_type)
        return (parsed, had_valid_feed_type, error_tag, src_err, result.text)

    return (parsed, had_valid_feed_type, None, None, result.text)


def _detect_parse_error_type(parsed, had_valid_feed_type: bool) -> str | None:
    """
    Determine if there was a parse error that should be reported.

    Returns error_tag or None.
    """
    if not parsed and had_valid_feed_type:
        return 'valid_empty_feed'
    return None


def _determine_time_relevance(published_ts: float | None, freshness_score: float, freshness_tier: str) -> float:
    """Compute time relevance score (ts_rel) based on freshness metrics."""
    if published_ts is None:
        return 0.1
    if freshness_tier == 'future':
        return 0.15
    if freshness_tier == 'unknown':
        return 0.2
    if freshness_score >= 1.0:
        return 1.0
    if freshness_score >= 0.85:
        return 0.9
    if freshness_score >= 0.6:
        return 0.6
    return 0.3


def _compute_richness(entry: FeedEntryHit, summary_len: int, rc_len: int, title_len: int) -> tuple[int, str]:
    """
    Compute richness score and band for an entry.

    Returns tuple of (richness, richness_band).
    """
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

    richness_band: str = 'low' if richness <= 2 else 'medium' if richness <= 4 else 'high'

    return (richness, richness_band)


def _determine_source_bias(feed_url_lower: str) -> float:
    """
    Determine source priority bias (spb) based on known OSINT sources.

    Returns spb value between 0.0 and 0.15.
    """
    if 'cisa.gov' in feed_url_lower or 'nvd.nist.gov' in feed_url_lower:
        return 0.15
    if 'krebs' in feed_url_lower or 'sans.edu' in feed_url_lower:
        return 0.12
    if 'abuse.ch' in feed_url_lower or 'urlhaus' in feed_url_lower:
        return 0.1
    if 'welivesecurity' in feed_url_lower:
        return 0.08
    if 'bleepingcomputer' in feed_url_lower or 'thehackersnews' in feed_url_lower:
        return 0.06
    return 0.0


def _determine_time_signal(entry: FeedEntryHit, ts_rel: float, freshness_tier: str) -> str:
    """
    Determine the time signal classification for an entry.

    Returns time_signal string.
    """
    if entry.published_ts is None:
        return 'no_timestamp'
    if freshness_tier == 'future':
        return 'future_ts_penalized'
    if freshness_tier == 'unknown':
        return 'unparseable_ts'
    if ts_rel >= 1.0:
        return 'ts_recent_high_conf'
    if ts_rel >= 0.9:
        return 'ts_fresh_high_conf'
    return 'ts_aged_low_conf'


def _build_scored_entry(entry: FeedEntryHit, retrieved_ts: float) -> tuple:
    """
    Build a scored entry tuple with all scoring components.

    Returns tuple: (entry, freshness_score, quality_score, freshness_tier, combined, ts_rel, richness_band, usefulness_band, spb, time_signal)
    """
    title_len = len(entry.title.strip())
    summary_len = len(entry.summary.strip())
    rc_len = len(entry.rich_content) if entry.rich_content else 0

    freshness_score, freshness_tier = _compute_freshness(entry.published_ts, retrieved_ts)
    quality_score = _compute_quality(entry)
    combined = freshness_score * 0.55 + quality_score * 0.45

    ts_rel = _determine_time_relevance(entry.published_ts, freshness_score, freshness_tier)
    richness, richness_band = _compute_richness(entry, summary_len, rc_len, title_len)

    usefulness_band: str = 'high' if combined >= 0.8 else 'medium' if combined >= 0.55 else 'low' if combined >= 0.3 else 'noise'

    feed_url_lower = entry.feed_url.lower()
    spb = _determine_source_bias(feed_url_lower)

    time_signal = _determine_time_signal(entry, ts_rel, freshness_tier)

    return (entry, freshness_score, quality_score, freshness_tier, combined, ts_rel, richness_band, usefulness_band, spb, time_signal)


def _score_entries(deduped: list[FeedEntryHit], retrieved_ts: float) -> list[tuple]:
    """
    Score all entries and return sorted list of scored tuples.

    Returns list of scored tuples sorted by combined score descending.
    """
    scored: list[tuple] = []
    all_filtered_out: bool = True

    for entry in deduped:
        title_len = len(entry.title.strip())
        summary_len = len(entry.summary.strip())
        rc_len = len(entry.rich_content) if entry.rich_content else 0

        if title_len == 0 and summary_len == 0 and rc_len == 0:
            continue

        all_filtered_out = False
        scored.append(_build_scored_entry(entry, retrieved_ts))

    scored.sort(key=lambda x: -(x[4] + x[8]))  # Sort by combined + spb

    return scored


def _get_base_reason(freshness_tier: str, freshness_score: float, quality_score: float) -> str:
    """Get base reason string based on freshness and quality scores."""
    tier_reason_map = {
        'future': 'future_timestamp',
        'unknown': 'missing_timestamp',
    }
    if freshness_tier in tier_reason_map:
        return tier_reason_map[freshness_tier]
    if freshness_score >= 1.0:
        return 'recent_high_quality' if quality_score >= 0.5 else 'recent'
    if freshness_score >= 0.85:
        return 'fresh_high_quality' if quality_score >= 0.5 else 'fresh'
    if quality_score >= 0.6:
        return 'quality_signal'
    if quality_score >= 0.3:
        return 'moderate_quality'
    return 'aged_low_quality'


def _is_enhanced_entry(entry: FeedEntryHit, richness_band: str) -> bool:
    """Check if entry has enhanced metadata (author + language or high richness)."""
    has_author = bool(entry.entry_author.strip())
    has_lang = bool(entry.feed_language.strip())
    return (has_author and has_lang) or richness_band == 'high'


def _build_selection_reason(
    entry: FeedEntryHit,
    freshness_score: float,
    quality_score: float,
    freshness_tier: str,
    ts_rel: float,
    richness_band: str,
    usefulness_band: str,
    spb: float,
    time_signal: str
) -> str:
    """
    Build the selection reason string for an entry.

    Returns formatted reason string with all metadata.
    """
    reason = _get_base_reason(freshness_tier, freshness_score, quality_score)
    if _is_enhanced_entry(entry, richness_band) and not reason.startswith('enhanced_'):
        reason = 'enhanced_' + reason
    return f'{reason}|ts_rel={ts_rel:.2f}|richness={richness_band}|usefulness={usefulness_band}|src_bias={spb:.2f}|ts_signal={time_signal}'


def _build_final_entries(scored: list[tuple], max_entries: int) -> tuple[list[FeedEntryHit], bool]:
    """
    Build final list of FeedEntryHit from scored entries.

    Returns tuple of (entries list, all_filtered_out flag).
    """
    entries: list[FeedEntryHit] = []
    all_filtered_out: bool = True

    for rank, scored_entry in enumerate(scored[:max_entries]):
        (entry, freshness_score, quality_score, freshness_tier, _, ts_rel, richness_band, usefulness_band, spb, time_signal) = scored_entry

        reason = _build_selection_reason(
            entry, freshness_score, quality_score, freshness_tier,
            ts_rel, richness_band, usefulness_band, spb, time_signal
    )

        entries.append(FeedEntryHit(
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
            rich_content=getattr(entry, 'rich_content', '') or '',
            entry_author=getattr(entry, 'entry_author', '') or '',
            feed_title=getattr(entry, 'feed_title', '') or '',
            feed_language=getattr(entry, 'feed_language', '') or '',
            freshness_score=freshness_score,
            quality_score=quality_score,
            freshness_tier=freshness_tier,
            selection_reason=reason,
            source_priority_bias=spb,
            time_signal_reason=time_signal
        ))

        if rank < len(scored):
            all_filtered_out = False

    return (entries, all_filtered_out)


async def async_fetch_feed_entries(feed_url: str, max_entries: int=50, timeout_s: float=35.0, max_bytes: int=2000000) -> FeedBatchResult:
    max_entries = min(max(max_entries, 1), _MAX_ENTRIES_HARD)
    retrieved_ts = time.time()

    # Step 1: Fetch feed content
    result, fetch_error, src_accessibility = await _fetch_feed_content_async(feed_url, timeout_s, max_bytes)
    if fetch_error:
        return _error_result(feed_url, fetch_error, src_accessibility, result)

    # Step 2: Parse and validate feed
    parsed, had_valid_feed_type, parse_error, src_err, raw_xml = _parse_and_validate_feed(result, feed_url, retrieved_ts)
    if parse_error:
        return _error_result(feed_url, parse_error, src_err, None, raw_xml)

    # Step 3: Deduplicate, score, and build final entries
    deduped = _deduplicate_entries(parsed)
    scored = _score_entries(deduped, retrieved_ts)
    entries, all_filtered_out = _build_final_entries(scored, max_entries)

    # Step 4: Determine final error tag
    error_tag = _determine_final_error(entries, all_filtered_out, had_valid_feed_type)

    return FeedBatchResult(
        feed_url=feed_url,
        entries=tuple(entries),
        error=error_tag,
        raw_xml=raw_xml
    )


async def _fetch_feed_content_async(feed_url: str, timeout_s: float, max_bytes: int) -> tuple:
    """
    Async helper to fetch feed content.

    Returns tuple of (result, error_tag, src_err).
    """
    from hledac.universal.fetching.public_fetcher import async_fetch_public_text

    try:
        result = await async_fetch_public_text(feed_url, timeout_s=timeout_s, max_bytes=max_bytes, bypass_circuit_breaker=True)
    except CancelledError:
        raise  # Re-raise CancelledError to properly cancel the task
    if result.error or result.text is None:
        fetch_err = result.error or 'fetch_returned_none'
        src_accessibility = _map_fetch_result_to_source_accessibility(result)
        return (result, fetch_err, src_accessibility)

    return (result, None, None)

class _FeedLinkParser(HTMLParser):
    """
    Lightweight HTMLParser that extracts <link rel="alternate"> feed candidates.

    Fail-soft: collects partial hits even if parsing fails mid-document.
    """
    __slots__ = tuple(('_base_href', '_error', '_hits'))

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
        if self._error is None:
            self._error = message

    def _parse_base_tag(self, attrs: list[tuple[str, str | None]]) -> None:
        """Extract base href from <base> tag."""
        if self._base_href is not None:
            return
        for name, value in attrs:
            if name == 'href' and value and value.strip():
                self._base_href = value.strip()
                break

    def _is_valid_feed_link(self, href: str, rel: str) -> bool:
        """Check if href is a valid feed link candidate."""
        if 'alternate' not in rel.lower():
            return False
        if not href or not href.strip():
            return False
        if href.strip().startswith('#'):
            return False
        scheme = urllib.parse.urlparse(href.strip()).scheme.lower()
        if scheme and scheme not in ('http', 'https'):
            return False
        return True

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == 'base':
            self._parse_base_tag(attrs)
            return
        if tag != 'link':
            return
        attr_dict: dict[str, str] = {name.lower(): value for name, value in attrs if name and value}
        href = attr_dict.get('href', '')
        if not self._is_valid_feed_link(href, attr_dict.get('rel', '')):
            return
        self._hits.append({
            'href': href,
            'type': attr_dict.get('type', '').lower(),
            'title': attr_dict.get('title', '') or ''
        })

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

def _resolve_feed_href(raw_href: str, base_url: str) -> str:
    """
    Resolve a raw href against a base URL.

    If raw_href is already absolute (has scheme), return as-is after
    basic normalization. Otherwise use urllib.parse.urljoin.
    Finally strip any fragment and normalize scheme+host to lowercase.
    """
    raw_href = raw_href.strip()
    if not raw_href:
        return ''
    parsed = urllib.parse.urlparse(raw_href)
    if parsed.scheme in ('http', 'https'):
        resolved = parsed._replace(fragment='').geturl()
        return _normalize_url(resolved)
    resolved = urllib.parse.urljoin(base_url, raw_href)
    resolved_parsed = urllib.parse.urlparse(resolved)
    resolved = resolved_parsed._replace(fragment='').geturl()
    return _normalize_url(resolved)


def _compute_feed_confidence(feed_type: str) -> float | None:
    """Compute confidence score based on feed MIME type."""
    if feed_type in _FEED_TYPES_HIGH:
        return 1.0
    if feed_type in _FEED_TYPES_LOW:
        return 0.5
    return None


def _is_valid_resolved_url(resolved: str) -> bool:
    """Check if resolved URL has valid http/https scheme."""
    if not resolved:
        return False
    parsed = urllib.parse.urlparse(resolved)
    return parsed.scheme in ('http', 'https')


def _build_discovery_hit(hit_dict: dict, page_url: str, base_url: str, seen_urls: set[str], max_candidates: int, hits: list[FeedDiscoveryHit], discovered_ts: float) -> bool:
    """Process a single hit dict and add to hits if valid. Returns True if max reached."""
    raw_href = hit_dict['href']
    feed_type = hit_dict['type']
    title = hit_dict['title']
    confidence = _compute_feed_confidence(feed_type)
    if confidence is None:
        return False
    resolved = _resolve_feed_href(raw_href, base_url)
    if not _is_valid_resolved_url(resolved):
        return False
    if resolved in seen_urls:
        return False
    seen_urls.add(resolved)
    hits.append(FeedDiscoveryHit(page_url=page_url, feed_url=resolved, title=title or '', feed_type=feed_type, confidence=confidence, source='link_tag', discovered_ts=discovered_ts))
    return len(hits) >= max_candidates


def discover_feed_urls_from_html(page_url: str, html_text: str, max_candidates: int=_MAX_CANDIDATES_DEFAULT) -> FeedDiscoveryBatchResult:
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
    max_candidates = max(1, min(max_candidates, _MAX_CANDIDATES_HARD))
    parser = _FeedLinkParser()
    parse_error: str | None = None
    try:
        parser.feed(html_text)
    except Exception as e:
        logger.debug(f'[RSS/Atom] HTML feed discovery parse failed for {page_url}: {e}')
        parse_error = str(e)
    base_href = parser.base_href
    base_url = base_href if base_href else page_url
    seen_urls: set[str] = set()
    hits: list[FeedDiscoveryHit] = []
    discovered_ts = time.time()
    for hit_dict in parser.hits:
        if _build_discovery_hit(hit_dict, page_url, base_url, seen_urls, max_candidates, hits, discovered_ts):
            break
    if hits:
        parse_error = None
    elif parse_error:
        return FeedDiscoveryBatchResult(page_url=page_url, hits=(), error=f'html_parse_error:{parse_error}')
    hits.sort(key=lambda h: -h.confidence)
    return FeedDiscoveryBatchResult(page_url=page_url, hits=tuple(hits), error=None)

async def async_discover_feed_urls(page_url: str, timeout_s: float=35.0, max_bytes: int=2000000, max_candidates: int=_MAX_CANDIDATES_DEFAULT) -> FeedDiscoveryBatchResult:
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
    from hledac.universal.fetching.public_fetcher import async_fetch_public_text
    try:
        result = await async_fetch_public_text(page_url, timeout_s=timeout_s, max_bytes=max_bytes, bypass_circuit_breaker=True)
    except CancelledError:
        raise
    if result.error or result.text is None:
        return FeedDiscoveryBatchResult(page_url=page_url, hits=(), error=result.error or 'fetch_returned_none')
    content_type = result.content_type.lower()
    if content_type and (not ('text/html' in content_type or 'application/xhtml+xml' in content_type)):
        return FeedDiscoveryBatchResult(page_url=page_url, hits=(), error=f'content_type_rejected:{content_type}')
    batch: FeedDiscoveryBatchResult = await asyncio.to_thread(discover_feed_urls_from_html, page_url, result.text, max_candidates)
    return batch

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
    return (FeedSeed(feed_url='https://www.cisa.gov/feeds/hns.xml', label='CISA HNS', source='curated_seed', priority=10), FeedSeed(feed_url='https://nvd.nist.gov/feeds/xml/cve/misc/nvd-rss.xml', label='NVD CVE RSS', source='curated_seed', priority=10), FeedSeed(feed_url='https://feeds.feedburner.com/TheHackersNews', label='The Hacker News', source='curated_seed', priority=4), FeedSeed(feed_url='https://krebsonsecurity.com/feed/', label='Krebs on Security', source='curated_seed', priority=7), FeedSeed(feed_url='https://abuse.ch/feeds/urlhaus/', label='URLhaus', source='curated_seed', priority=10), FeedSeed(feed_url='https://www.welivesecurity.com/feed/', label='WeLiveSecurity', source='curated_seed', priority=3), FeedSeed(feed_url='https://www.bleepingcomputer.com/feed/', label='BleepingComputer', source='curated_seed', priority=4), FeedSeed(feed_url='https://isc.sans.edu/rssfeed.xml', label='SANS ISC', source='curated_seed', priority=6), FeedSeed(feed_url='https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json', label='CISA KEV', source='topology_candidate', priority=10), FeedSeed(feed_url='https://services.nvd.nist.gov/rest/json/cves/2.0?pubStartDate=2025-01-01T00:00:00.000&pubEndDate=2025-12-31T23:59:59.999', label='NVD CVE JSON', source='topology_candidate', priority=8), FeedSeed(feed_url='https://web.archive.org/cdx/search/cdx?url=*.com&output=json&limit=20', label='Wayback CDX', source='topology_candidate', priority=1), FeedSeed(feed_url='https://index.commoncrawl.org/CC-MAIN-2024-51-index', label='CommonCrawl CDX', source='topology_candidate', priority=1))

def normalize_seed_identity(seed: FeedSeed) -> str:
    """
    Return a canonical identity string for a FeedSeed.

    Uses the URL host + path (no query/fragment) for stable identification.
    No network calls.
    """
    try:
        parsed = urllib.parse.urlparse(seed.feed_url)
        return f'{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path}'
    except Exception as e:
        logger.debug(f"[RSS/Atom] Seed identity normalize failed for '{seed.feed_url}': {e}")
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
    runtime = [s for s in seeds if s.source == 'curated_seed']
    topology = [s for s in seeds if s.source == 'topology_candidate']
    return {'count': len(seeds), 'runtime_rss_atom_count': len(runtime), 'topology_candidate_count': len(topology), 'runtime_rss_atom_identities': sorted((normalize_seed_identity(s) for s in runtime)), 'topology_candidate_identities': sorted((normalize_seed_identity(s) for s in topology)), 'runtime_rss_atom_urls': sorted((s.feed_url for s in runtime)), 'topology_candidate_urls': sorted((s.feed_url for s in topology)), 'all_urls': sorted((s.feed_url for s in seeds)), 'has_authenticated_reuters': any(('reuters.com' in s.feed_url.lower() for s in seeds))}

def get_runtime_feed_seeds() -> tuple[FeedSeed, ...]:
    """
    Return the runtime RSS/Atom feed seed surface.

    Contains ONLY ``source=curated_seed`` entries, sorted by priority descending.
    Topology candidates (JSON/WARC/CDX endpoints) are EXCLUDED.

    This is the surface that belongs in hot-path feed fetching.
    """
    seeds = get_default_feed_seeds()
    runtime = [s for s in seeds if s.source == 'curated_seed']
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
    return tuple((s for s in seeds if s.source == 'topology_candidate'))
VIABILITY_HIGH_PRIORITY_THRESHOLD: int = 7
VIABILITY_MEDIUM_PRIORITY_THRESHOLD: int = 4

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
    if not runtime:
        tier = 'unknown'
    else:
        max_priority = max((s.priority for s in runtime))
        if max_priority >= VIABILITY_HIGH_PRIORITY_THRESHOLD:
            tier = 'high'
        elif max_priority >= VIABILITY_MEDIUM_PRIORITY_THRESHOLD:
            tier = 'medium'
        elif max_priority > 0:
            tier = 'low'
        else:
            tier = 'degraded'
    runtime_feeds: list[dict[str, Any]] = [{'feed_url': s.feed_url, 'label': s.label, 'priority': s.priority, 'source': s.source, 'identity': normalize_seed_identity(s)} for s in runtime]
    canonical_labels = [s.label for s in runtime if s.priority >= VIABILITY_HIGH_PRIORITY_THRESHOLD]
    return {'viability_tier': tier, 'runtime_feed_count': len(runtime), 'topology_candidate_count': len(topology), 'runtime_feeds': runtime_feeds, 'top_priority': max((s.priority for s in runtime), default=0), 'canonical_osint_sources': canonical_labels}

def _normalize_for_dedup(url: str) -> str:
    """Normalize URL for deterministic merge dedup."""
    return _normalize_url(url)

def merge_feed_sources(discovered: tuple[FeedDiscoveryHit, ...], seeds: tuple[FeedSeed, ...]) -> tuple[MergedFeedSource, ...]:
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
    for seed in seeds:
        norm = _normalize_for_dedup(seed.feed_url)
        if norm and norm not in seen_urls:
            seen_urls[norm] = {'feed_url': seed.feed_url, 'label': seed.label, 'origin': 'seed', 'priority': seed.priority}
    for hit in discovered:
        norm = _normalize_for_dedup(hit.feed_url)
        if norm and norm not in seen_urls:
            label = hit.title if hit.title else norm
            seen_urls[norm] = {'feed_url': hit.feed_url, 'label': label, 'origin': 'discovered', 'priority': 0}
    sorted_items = sorted(seen_urls.values(), key=lambda x: -x['priority'])
    return tuple((MergedFeedSource(feed_url=item['feed_url'], label=item['label'], origin=item['origin'], priority=item['priority']) for item in sorted_items))
# AP-07: Fan-out orchestrator for runtime RSS/Atom feeds using parallel()
async def async_fetch_all_runtime_feeds(
    max_concurrent: int = 5,
    max_entries_per_feed: int = 20,
    timeout_s: float = 35.0,
    max_bytes: int = 2_000_000,
) -> tuple[FeedBatchResult, ...]:
    """
    AP-07 FIX: Fan-out orchestrator for runtime RSS/Atom feeds.

    Fetches all curated_seed feeds from get_runtime_feed_seeds() concurrently
    using parallel() with max_concurrent limit (default 5).

    Args:
        max_concurrent: Max concurrent feed fetches (default 5, M1 8GB safe).
        max_entries_per_feed: Max entries per feed (default 20).
        timeout_s: Per-feed timeout in seconds (default 35.0).
        max_bytes: Max bytes per feed (default 2_000_000).

    Returns:
        Tuple of FeedBatchResult for each feed that was fetched.
    """
    from hledac.universal.utils.asyncx import parallel

    runtime_seeds = get_runtime_feed_seeds()

    async def _fetch_one(seed: FeedSeed) -> FeedBatchResult:
        try:
            result = await async_fetch_feed_entries(
                feed_url=seed.feed_url,
                max_entries=max_entries_per_feed,
                timeout_s=timeout_s,
                max_bytes=max_bytes,
    )
            return result
        except asyncio.CancelledError:
            raise
        except Exception:
            # Fail-safe: return empty result on error
            return FeedBatchResult(feed_url=seed.feed_url, entries=(), error="fetch_failed")

    # Build coroutine list
    coros = [_fetch_one(seed) for seed in runtime_seeds]

    # Run with bounded concurrency via parallel()
    try:
        build = await parallel(
            coros,
            concurrency=max_concurrent,
            policy="collect",
            taskgroup=True,
            ctx="rss_atom:fetch_all_runtime",
    )
        ok_results = build.ok
    except asyncio.CancelledError:
        raise
    except Exception:
        ok_results = []

    return tuple(ok_results)


from hledac.universal.utils.html_parse_pool import parse_html_links as _parse_html_links
from _core import aclose

async def parse_html_async(html: str) -> list[dict]:
    """Async wrapper — uses centralized M1-safe ThreadPoolExecutor from html_parse_pool."""
    return await _parse_html_links(html)
def _deduplicate_entries(entries: list[FeedEntryHit]) -> list[FeedEntryHit]:
    """Deduplicate entries by URL + title + published_raw."""
    seen_keys: set[str] = set()
    deduped: list[FeedEntryHit] = []
    for entry in entries:
        key = _entry_dedup_key(entry.entry_url, entry.title, entry.published_raw, None, None)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(entry)
    return deduped


def _determine_final_error(entries: list, all_filtered_out: bool, had_valid_feed_type: bool) -> str | None:
    """Determine final error tag based on entry state."""
    if not entries and all_filtered_out:
        return 'parsed_but_filtered'
    if not entries and had_valid_feed_type:
        return 'valid_empty_feed'
    return None


def _error_result(
    feed_url: str,
    error_tag: str,
    src_accessibility: str | None,
    result,
    raw_xml: str | None = None
) -> FeedBatchResult:
    """Build an error FeedBatchResult."""
    return FeedBatchResult(
        feed_url=feed_url,
        entries=(),
        error=error_tag,
        source_accessibility_error=src_accessibility,
        raw_xml=raw_xml if raw_xml is not None else (result.text if result and result.text else None)
    )
def _try_parse_with_mode(
    xml_text: str,
    feed_url: str,
    retrieved_ts: float,
    parser_func,
    mode: str,
    _parse_mode_out: list[str] | None,
    _feed_type_out: list[str] | None
) -> tuple[list[FeedEntryHit] | None, str]:
    """
    Attempt to parse XML with a given parser function.

    Returns (entries or None, mode_label) on success, or (None, mode_label) on failure.
    """
    try:
        root = parser_func(xml_text)
        if root is not None:
            _report_parse_mode(_parse_mode_out, mode)
            local_root = _local_name(root.tag)
            if local_root == 'rss':
                if _feed_type_out is not None:
                    _feed_type_out.append('rss')
                return (_parse_rss(root, feed_url, retrieved_ts), mode)
            elif local_root == 'feed':
                if _feed_type_out is not None:
                    _feed_type_out.append('feed')
                return (_parse_atom(root, feed_url, retrieved_ts), mode)
            else:
                return ([], mode)
    except Exception as e:
        logger.debug(f'[RSS/Atom] {mode} parse failed for {feed_url}: {e}')
    return (None, mode)


def _parse_xml_result(
    entries: list[FeedEntryHit] | None,
    mode: str,
    _parse_mode_out: list[str] | None
) -> list[FeedEntryHit]:
    """Handle the result of a parse attempt, returning entries or empty list."""
    if entries is not None:
        _report_parse_mode(_parse_mode_out, mode)
        return entries
    return []
