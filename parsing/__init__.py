"""parsing — zero-dependency feed parsing (selectolax + orjson)."""
from __future__ import annotations

from hledac.universal.parsing.feed_parser import (
    FeedEntry,
    parse_atom,
    parse_feed,
    parse_rss,
)

__all__ = ["FeedEntry", "parse_atom", "parse_rss", "parse_feed"]
