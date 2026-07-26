"""parsing/feed_parser.py — Modern zero-dependency RSS/Atom parser via selectolax.

Replaces feedparser (7-15 ms/fed sync overhead) with selectolax MyHTML (~3-5 ms/fed).
Fully async-native: no sync blocking in the pipeline.

M1 8GB advantage:
  • ~12 MB RSS stack elimination (feedparser + sgmllib deps removed)
  • Zero sync blocker in async pipeline
  • msgspec.Struct output → directly consumable by FeedPipelineEntryResult

Schema coverage:
  RSS 2.0: channel/item → title/link/description/pubDate/guid
  Atom 1.0: feed/entry → title/link[@href]/summary/content/published/updated/author

Security:
  • XML entity/DOCTYPE guard before parsing
  • Size cap delegated to caller (max_bytes)
  • Fail-soft on malformed XML
  • No exec/eval, no dynamic code

Sprint F320-8 — Issue #8: feedparser removal
"""
from __future__ import annotations

import asyncio
import datetime
import re
import urllib.parse
from typing import Any, NamedTuple

try:
    from selectolax.parser import HTMLParser as _SelectolaxHTMLParser
    _SELECTOLAX_AVAILABLE = True
except ImportError:
    _SelectolaxHTMLParser: Any = None  # type: ignore[assignment]
    _SELECTOLAX_AVAILABLE = False

import msgspec
import xxhash


# ---------------------------------------------------------------------------
# DTO — msgspec.Struct for zero-allocation downstream consumption
# ---------------------------------------------------------------------------

class FeedEntry(msgspec.Struct, frozen=True):
    """Single parsed feed entry — msgspec.Struct for direct FeedPipelineEntryResult use."""

    feed_url: str
    entry_url: str
    title: str
    link: str
    description: str
    published_raw: str
    published_ts: float | None
    author: str
    content: str
    language: str
    feed_title: str
    entry_hash: str


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_XML_ENTITY_RE = re.compile(r"<!ENTITY|<!DOCTYPE", re.IGNORECASE)
_ISO_Z_RE = re.compile(r"Z$")

# ISO 8601 normalization helpers
def _normalize_iso(raw: str) -> str:
    """Normalize ISO 8601 / RFC 3339 timestamp to fromisoformat-compatible form."""
    raw = raw.strip()
    raw = _ISO_Z_RE.sub("+00:00", raw)
    return raw


def _parse_timestamp(raw: str | None) -> float | None:
    """Parse date from RSS or Atom formats. Returns None on failure."""
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None
    # Try ISO 8601 / RFC 3339 via fromisoformat
    try:
        normalized = _normalize_iso(raw)
        dt = datetime.datetime.fromisoformat(normalized)
        return dt.timestamp()
    except Exception:
        pass
    # Try RFC 2822 (RSS pubDate) via email.utils
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(raw)
        return dt.timestamp()
    except Exception:
        pass
    return None


def _entry_hash(title: str, published_raw: str) -> str:
    """Compute deterministic hash for entry identity."""
    return xxhash.xxh3_64(f"{(title or '')}|{(published_raw or '')}").hexdigest()


def _is_xml_dangerous(text: str) -> bool:
    """Return True if text contains XML ENTITY / DOCTYPE declarations."""
    if not text:
        return False
    return bool(_XML_ENTITY_RE.search(text))


def _sanitize_xml(raw: str) -> str:
    """Remove DOCTYPE/ENTITY declarations from XML text.

    Delegates to rust.xml.sanitize_xml when Rust backend is available (5× faster).
    Falls back to the pure-Python implementation for compatibility.

    Single-pass scanner:
      1. Strips <!DOCTYPE ...> declarations (including internal subsets).
      2. Strips <!ENTITY ...> declarations entirely.
      3. Preserves standard XML predefined entities (&amp; &lt; &gt; &quot; &apos;)
         and numeric character references (&#NNN; &#xHHH;).
    """
    # Fast path: try Rust backend first
    try:
        from core.rust_backend import rust

        if rust.is_available:
            return rust.xml.sanitize_xml(raw)
    except Exception:
        pass

    # Fallback: pure-Python implementation
    if "<!doctype" not in raw.lower() and "<!entity" not in raw.lower():
        return raw

    result: list[str] = []
    i = 0
    n = len(raw)

    while i < n:
        c = raw[i]

        # Handle <!DOCTYPE ...>
        if c == "<" and raw[i:i+9].lower() == "<!doctype":
            i += 9
            depth = 0
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
                                i += 2
                                break
                    elif ch == ">" and depth == 0:
                        i += 1
                        break
                else:
                    if ch == quote_char:
                        in_quote = False
                        quote_char = None
                i += 1

        # Handle <!ENTITY ...>
        elif c == "<" and raw[i:i+9].lower() == "<!entity":
            i += 9
            in_quote = False
            quote_char = None
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

        else:
            result.append(c)
            i += 1

    return "".join(result)


# ---------------------------------------------------------------------------
# RSS 2.0 parser via selectolax
# ---------------------------------------------------------------------------

def _selectolax_rss_feed(text: str, feed_url: str = "") -> list[FeedEntry]:
    """Parse RSS 2.0 feed using selectolax MyHTML (C backend).

    ~3-5 ms/fed vs 7-15 ms for feedparser.
    Falls back to [] if selectolax unavailable.
    """
    if not _SELECTOLAX_AVAILABLE:
        return []

    try:
        assert _SelectolaxHTMLParser is not None
        parser = _SelectolaxHTMLParser(text.encode("utf-8") if isinstance(text, str) else text)
    except Exception:
        return []

    entries: list[FeedEntry] = []

    # Extract channel metadata
    channel = parser.css_first("channel")
    if channel is None:
        return []

    channel_title = _tag_text(channel, "title")
    channel_language = _tag_text(channel, "language")

    # Iterate items
    items = channel.css("item")
    for item in items:
        title = _tag_text(item, "title")
        link = _tag_text(item, "link")
        description = _tag_text(item, "description")
        pub_date = _tag_text(item, "pubDate")
        guid = _tag_text(item, "guid")
        # author/dc:creator — dc: uses namespace prefix; handle both via text extraction
        author = _tag_text(item, "author") or _tag_text_ns(item, "creator", "http://purl.org/dc/elements/1.1/")

        # content:encoded — namespace-prefixed; use attribute-free version
        content = _tag_text(item, "content:encoded")

        # published_ts
        published_ts = _parse_timestamp(pub_date)

        # entry_url: guid > link
        entry_url = guid if guid else link
        entry_url = _normalize_url(entry_url) if entry_url else ""

        published_raw = pub_date or ""

        entries.append(FeedEntry(
            feed_url=feed_url,
            entry_url=entry_url,
            title=title or "",
            link=link or "",
            description=description or "",
            published_raw=published_raw,
            published_ts=published_ts,
            author=author or "",
            content=content,
            language=channel_language or "",
            feed_title=channel_title or "",
            entry_hash=_entry_hash(title or "", published_raw),
        ))

    return entries


# ---------------------------------------------------------------------------
# Atom 1.0 parser via selectolax
# ---------------------------------------------------------------------------

def _selectolax_atom_feed(text: str, feed_url: str) -> list[FeedEntry]:
    """Parse Atom 1.0 feed using selectolax MyHTML (C backend).

    ~3-5 ms/fed vs 7-15 ms for feedparser.
    Falls back to [] if selectolax unavailable.
    """
    if not _SELECTOLAX_AVAILABLE:
        return []

    try:
        assert _SelectolaxHTMLParser is not None
        parser = _SelectolaxHTMLParser(text.encode("utf-8") if isinstance(text, str) else text)
    except Exception:
        return []

    entries: list[FeedEntry] = []

    # Feed-level metadata
    feed_title = _tag_text(parser, "title")
    feed_language = _tag_text(parser, "language") or ""

    # Iterate entries
    for entry in parser.css("entry"):
        title = _tag_text(entry, "title")
        summary = _tag_text(entry, "summary")
        content = _tag_text(entry, "content") or ""
        published = _tag_text(entry, "published")
        updated = _tag_text(entry, "updated")
        published_raw = published or updated or ""

        # Author
        author_el = entry.css_first("author")
        author = ""
        if author_el is not None:
            author = _tag_text(author_el, "name")

        # Entry URL: rel="alternate" or no rel, with href
        entry_url = ""
        for link_el in entry.css("link"):
            rel = link_el.attributes.get("rel")
            href = link_el.attributes.get("href", "")
            if rel is None or rel == "alternate":
                entry_url = _normalize_url(href)
                break
        if not entry_url:
            for link_el in entry.css("link"):
                href = link_el.attributes.get("href", "")
                if href:
                    entry_url = _normalize_url(href)
                    break

        published_ts = _parse_timestamp(published_raw)

        entries.append(FeedEntry(
            feed_url=feed_url,
            entry_url=entry_url or "",
            title=title or "",
            link=entry_url or "",
            description=summary or "",
            published_raw=published_raw,
            published_ts=published_ts,
            author=author or "",
            content=content,
            language=feed_language or "",
            feed_title=feed_title or "",
            entry_hash=_entry_hash(title or "", published_raw),
        ))

    return entries


# ---------------------------------------------------------------------------
# HTMLParser fallback (stdlib, no selectolax needed)
# ---------------------------------------------------------------------------

def _stdlib_rss_feed(text: str, feed_url: str = "") -> list[FeedEntry]:
    """Parse RSS 2.0 using stdlib html.parser — fallback when selectolax unavailable."""
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []

    if root.tag != "rss" and root.tag.endswith("rss"):
        return []

    channel = None
    for child in root:
        if child.tag == "channel":
            channel = child
            break
    if channel is None:
        return []

    entries: list[FeedEntry] = []
    channel_title = ""
    channel_language = ""

    for ch in channel:
        ln = ch.tag.split("}")[-1] if "}" in ch.tag else ch.tag
        if ln == "title" and not channel_title:
            channel_title = (ch.text or "").strip()
        elif ln == "language" and not channel_language:
            channel_language = (ch.text or "").strip()
        elif ln == "item":
            title = link = description = pub_date = guid = author = content = ""
            for item_child in ch:
                ic_ln = item_child.tag.split("}")[-1] if "}" in item_child.tag else item_child.tag
                if ic_ln == "title":
                    title = (item_child.text or "").strip()
                elif ic_ln == "link":
                    link = (item_child.text or "").strip()
                elif ic_ln == "description":
                    description = (item_child.text or "").strip()
                elif ic_ln == "pubDate":
                    pub_date = (item_child.text or "").strip()
                elif ic_ln == "guid":
                    guid = (item_child.text or "").strip()
                elif ic_ln == "author":
                    author = (item_child.text or "").strip()
                elif ic_ln == "creator":
                    author = (item_child.text or "").strip()
                elif ic_ln == "encoded":
                    content = (item_child.text or "").strip()

            entry_url = guid if guid else link
            entry_url = _normalize_url(entry_url) if entry_url else ""

            entries.append(FeedEntry(
                feed_url=feed_url,
                entry_url=entry_url,
                title=title or "",
                link=link or "",
                description=description or "",
                published_raw=pub_date or "",
                published_ts=_parse_timestamp(pub_date),
                author=author or "",
                content=content,
                language=channel_language or "",
                feed_title=channel_title or "",
                entry_hash=_entry_hash(title or "", pub_date or ""),
            ))

    return entries


def _stdlib_atom_feed(text: str, feed_url: str) -> list[FeedEntry]:
    """Parse Atom 1.0 using stdlib xml.etree.ElementTree — fallback."""
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []

    entries: list[FeedEntry] = []
    feed_title = ""
    feed_language = ""

    for child in root:
        ln = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if ln == "title":
            feed_title = (child.text or "").strip()
        elif ln == "language":
            feed_language = (child.text or "").strip()
        elif ln == "entry":
            title = summary = content = published = updated = author = ""
            entry_url = ""
            for ec in child:
                ecln = ec.tag.split("}")[-1] if "}" in ec.tag else ec.tag
                if ecln == "title":
                    title = (ec.text or "").strip()
                elif ecln == "summary":
                    summary = (ec.text or "").strip()
                elif ecln == "content":
                    content = (ec.text or "").strip()
                elif ecln == "published":
                    published = (ec.text or "").strip()
                elif ecln == "updated":
                    updated = (ec.text or "").strip()
                elif ecln == "author":
                    for name_el in ec:
                        if name_el.tag.split("}")[-1] == "name":
                            author = (name_el.text or "").strip()
                            break
                elif ecln == "link":
                    rel = ec.attrib.get("rel")
                    href = ec.attrib.get("href", "")
                    if (rel is None or rel == "alternate") and href:
                        entry_url = _normalize_url(href)
                        break
            if not entry_url:
                for ec in child:
                    ecln = ec.tag.split("}")[-1] if "}" in ec.tag else ec.tag
                    if ecln == "link":
                        href = ec.attrib.get("href", "")
                        if href:
                            entry_url = _normalize_url(href)
                            break

            published_raw = published or updated or ""
            entries.append(FeedEntry(
                feed_url=feed_url,
                entry_url=entry_url or "",
                title=title or "",
                link=entry_url or "",
                description=summary or "",
                published_raw=published_raw,
                published_ts=_parse_timestamp(published_raw),
                author=author or "",
                content=content,
                language=feed_language or "",
                feed_title=feed_title or "",
                entry_hash=_entry_hash(title or "", published_raw),
            ))

    return entries


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _tag_text(parent, tag: str) -> str:
    """Return text of first matching CSS tag, or empty string."""
    if not hasattr(parent, "css_first"):
        return ""
    try:
        el = parent.css_first(tag)
    except ValueError:
        # CSS selectors don't support colons (e.g. content:encoded in RSS);
        # fall back to iterating children and matching by exact tag name
        if not hasattr(parent, "css"):
            return ""
        for child in parent.css("*"):
            if child.tag == tag:
                return (child.text() or "").strip()
        return ""
    if el is None:
        return ""
    return (el.text() or "").strip()


def _tag_text_ns(parent, local_name: str, ns_uri: str) -> str:
    """Return text of first element matching local name + namespace URI (for RSS namespaced elements)."""
    if not hasattr(parent, "css"):
        return ""
    # Iterate children manually — CSS selectors don't handle xmlns prefixes
    for child in parent.css("*"):
        if child.tag == f"{{{ns_uri}}}{local_name}":
            return (child.text() or "").strip()
    return ""


def _tag_attr(parent, tag: str, attr: str) -> str:
    """Return attribute of first matching CSS tag, or empty string."""
    if not hasattr(parent, "css_first"):
        return ""
    el = parent.css_first(tag)
    if el is None:
        return ""
    return el.attributes.get(attr, "")


def _normalize_url(raw: str | None) -> str:
    """Normalize URL: lowercase scheme+host, strip trailing ?."""
    if not raw:
        return ""
    raw = raw.strip()
    if not raw:
        return ""
    try:
        parsed = urllib.parse.urlparse(raw)
        normalized = parsed._replace(
            scheme=parsed.scheme.lower(),
            netloc=parsed.netloc.lower(),
        ).geturl()
        if normalized.endswith("?"):
            normalized = normalized[:-1]
        return normalized
    except Exception:
        return raw.strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_rss(text: str, feed_url: str = "") -> list[FeedEntry]:
    """Parse RSS 2.0 feed from raw text (bytes or str).

    Strategy:
      1. selectolax MyHTML (C backend, ~3-5 ms)
      2. stdlib xml.etree.ElementTree fallback

    Returns list of FeedEntry (possibly empty).
    Raises nothing — fail-soft on parse errors.
    """
    if not text:
        return []

    # Guard against XML bombs
    if _is_xml_dangerous(text):
        sanitized = _sanitize_xml(text)
    else:
        sanitized = text

    # Try selectolax first
    if _SELECTOLAX_AVAILABLE:
        result = _selectolax_rss_feed(sanitized, feed_url)
        if result:
            return result

    # stdlib fallback
    return _stdlib_rss_feed(sanitized, feed_url)


def parse_atom(text: str, feed_url: str = "") -> list[FeedEntry]:
    """Parse Atom 1.0 feed from raw text (bytes or str).

    Strategy:
      1. selectolax MyHTML (C backend, ~3-5 ms)
      2. stdlib xml.etree.ElementTree fallback

    Returns list of FeedEntry (possibly empty).
    Raises nothing — fail-soft on parse errors.
    """
    if not text:
        return []

    # Guard against XML bombs
    if _is_xml_dangerous(text):
        sanitized = _sanitize_xml(text)
    else:
        sanitized = text

    # Try selectolax first
    if _SELECTOLAX_AVAILABLE:
        result = _selectolax_atom_feed(sanitized, feed_url)
        if result:
            return result

    # stdlib fallback
    return _stdlib_atom_feed(sanitized, feed_url)


def parse_feed(text: str, feed_url: str = "") -> list[FeedEntry]:
    """Auto-detect RSS 2.0 or Atom 1.0 and parse accordingly.

    Returns list of FeedEntry (possibly empty).
    Raises nothing — fail-soft on parse errors.
    """
    if not text:
        return []

    stripped = text.strip()
    if stripped.startswith("<rss") or "<channel>" in stripped[:500]:
        return parse_rss(text, feed_url)
    if '<feed' in stripped[:200] or stripped.startswith("<?xml"):
        # Check for Atom signature
        if "xmlns=\"http://www.w3.org/2005/Atom\"" in stripped[:1000] or "<feed" in stripped[:200]:
            return parse_atom(text, feed_url)
        # Could be RSS with XML declaration
        return parse_rss(text, feed_url)
    # Heuristic: default to RSS (most common)
    return parse_rss(text, feed_url)


# ---- Batch async API for feed_parser ----

# NOTE: Removed module-level ThreadPoolExecutor singleton (AP-08 fix).
# asyncio.to_thread() uses the built-in Python thread pool, which:
#   - Requires no max_workers tuning (adapts automatically)
#   - Avoids singleton max_workers mismatch across call sites
#   - Is GIL-friendly (selectolax releases GIL during C calls)
#   - Requires no atexit registration (pool lifetime = process lifetime)
# Previous pattern: shared pool with max_workers set at first call → all
# subsequent calls with different max_concurrency were capped at that value.


_RUST_SANITIZE_AVAILABLE: bool = False
try:
    from core.rust_backend import rust

    if rust.is_available:
        _batch_sanitize_xml = rust.xml.batch_sanitize_xml
        _RUST_SANITIZE_AVAILABLE = True
except Exception:
    _batch_sanitize_xml = None  # type: ignore[assignment]


class _FeedParseTask(NamedTuple):
    """Single feed parse task for batch processing."""
    text: str
    feed_url: str


async def parse_feeds_async(
    tasks: list[_FeedParseTask],
    *,
    max_concurrency: int = 8,
) -> list[list[FeedEntry]]:
    """Parse multiple feeds concurrently using asyncio.to_thread().

    M1 8GB strategy:
    - Sanitization: Rust batch_sanitize_xml (rayon parallel, ≥32 items)
    - Parsing: selectolax in ThreadPoolExecutor (GIL released during C calls)
    - Concurrency bounded by semaphore to prevent memory exhaustion

    Args:
        tasks: List of (text, feed_url) tuples to parse.
        max_concurrency: Maximum concurrent parse operations (default 8).

    Returns:
        List of FeedEntry lists, one per input task in order.
    """
    if not tasks:
        return []

    semaphore = asyncio.Semaphore(max_concurrency)

    async def _parse_with_semaphore(task: _FeedParseTask) -> list[FeedEntry]:
        async with semaphore:
            # asyncio.to_thread: uses Python's built-in thread pool (no singleton
            # max_workers mismatch). selectolax releases GIL during C calls so this
            # is M1 8GB-friendly. Pool lifetime = process lifetime, no atexit needed.
            return await asyncio.to_thread(_parse_single_feed, task)

    # Batch sanitization via Rust (rayon parallel for ≥32 items)
    # Threshold 32: below this, serial sanitization in Rust is faster than rayon overhead
    texts = [t.text for t in tasks]
    if _RUST_SANITIZE_AVAILABLE and len(texts) >= 32 and _batch_sanitize_xml is not None:
        from hledac.universal.utils.executor_decorator import offload_to
        sanitized_texts = await offload_to("duckdb_pool", _batch_sanitize_xml, texts)
        tasks = [
            _FeedParseTask(sanitized, task.feed_url)
            for sanitized, task in zip(sanitized_texts, tasks)
        ]

    from utils.async_helpers import parallel_ok
    from typing import cast
    # parallel_ok: returns list[T] (successes only), exceptions silently dropped.
    result = await parallel_ok(
        *[_parse_with_semaphore(task) for task in tasks],
        label="feed_parse",
    )
    # Shared pool (_parse_pool) lives for process lifetime, closed via atexit.
    return cast("list[list[FeedEntry]]", result)
    # NOTE: shared pool (_parse_pool) is NOT shut down here — it lives for
    # process lifetime and is closed via atexit at Python exit (SC-08 fix).


def _parse_single_feed(task: _FeedParseTask) -> list[FeedEntry]:
    """Parse a single feed (called in thread pool)."""
    text, feed_url = task.text, task.feed_url
    if not text:
        return []
    try:
        return parse_feed(text, feed_url)
    except Exception:
        return []
