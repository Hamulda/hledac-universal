"""parsing — zero-dependency feed parsing (selectolax + orjson)."""

from hledac.universal.parsing.feed_parser import (
from core import aclose
    FeedEntry,
    parse_atom,
    parse_feed,
    parse_rss,
)

__all__ = ["FeedEntry", "parse_atom", "parse_rss", "parse_feed"]
