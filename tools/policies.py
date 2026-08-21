"""
URL scoring policies. No action generation, just scoring.
Each policy has a stable `.name` and a `.score` that can be updated.
"""

import logging
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)
AUTHORITY_MARKERS = (".gov", ".edu", ".mil", "wikipedia.org", "reuters.com", "apnews.com", "bbc.com")
ARCHIVE_MARKERS = ("web.archive.org", "archive.today", "archive.org")
DISCOURSE_MARKERS = ("reddit.com", "news.ycombinator.com", "github.com", "stackoverflow.com", "x.com", "twitter.com")


class BasePolicy:
    """Base URL scoring policy with name and score attributes."""

    __slots__ = ("name", "score")

    def __init__(self, name: str) -> None:
        self.name = name
        self.score = 0.0

    def score_url(self, url: str, state: Any) -> float:
        """Override in subclass to implement scoring logic."""
        raise NotImplementedError


class AuthorityPolicy(BasePolicy):
    def __init__(self) -> None:
        super().__init__(name="authority")

    def score_url(self, url: str, state: Any) -> float:
        domain = urlparse(url).netloc.lower()
        if any(m in domain for m in AUTHORITY_MARKERS):
            return 1.0
        return 0.3


class TemporalPolicy(BasePolicy):
    def __init__(self) -> None:
        super().__init__(name="temporal")

    def score_url(self, url: str, state: Any) -> float:
        domain = urlparse(url).netloc.lower()
        if any(m in domain for m in ARCHIVE_MARKERS):
            return 0.9
        return 0.4


class DiscoursePolicy(BasePolicy):
    def __init__(self) -> None:
        super().__init__(name="discourse")

    def score_url(self, url: str, state: Any) -> float:
        domain = urlparse(url).netloc.lower()
        if any(m in domain for m in DISCOURSE_MARKERS):
            return 1.0
        return 0.2
