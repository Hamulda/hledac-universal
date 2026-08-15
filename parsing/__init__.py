"""parsing — zero-dependency feed parsing (selectolax + orjson)."""






    FeedEntry,
    parse_atom,
    parse_feed,
    parse_rss,
)

__all__ = ["FeedEntry", "parse_atom", "parse_rss", "parse_feed"]

from _core import aclose