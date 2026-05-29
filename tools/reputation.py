"""
Reputation scoring for domains based on corroboration/contradiction.
"""

import logging
from collections import defaultdict

logger = logging.getLogger(__name__)

# In‑memory storage for reputation counts (per domain)
_reputation_counts: dict[str, dict[str, int]] = defaultdict(lambda: {"confirmed": 0, "refuted": 0})

# Boundedness constant
MAX_REPUTATION_DOMAINS = 1000


def _enforce_reputation_cap() -> None:
    """Ensure _reputation_counts stays within MAX_REPUTATION_DOMAINS limit."""
    if len(_reputation_counts) <= MAX_REPUTATION_DOMAINS:
        return

    try:
        # Find domain with lowest total count (confirmed + refuted)
        min_domain = None
        min_total = float('inf')
        for domain, counts in _reputation_counts.items():
            total = counts.get('confirmed', 0) + counts.get('refuted', 0)
            if total < min_total:
                min_total = total
                min_domain = domain
        if min_domain:
            del _reputation_counts[min_domain]
    except Exception:
        # Fail-safe: evict arbitrary domain if calculation fails
        try:
            if _reputation_counts:
                del _reputation_counts[next(iter(_reputation_counts))]
        except Exception:
            pass


def update_reputation(domain: str, confirmed: bool = False, refuted: bool = False) -> None:
    """Update reputation counts for a domain."""
    if confirmed:
        _reputation_counts[domain]["confirmed"] += 1
    if refuted:
        _reputation_counts[domain]["refuted"] += 1
    # Enforce boundedness after each update
    _enforce_reputation_cap()


def get_reputation_score(domain: str) -> float:
    """
    Return reputation score in [0,1].
    - 1.0 = fully trusted (all confirmed, no refuted)
    - 0.0 = fully untrusted (all refuted, no confirmed)
    - 0.5 = neutral (no data or equal)
    """
    counts = _reputation_counts.get(domain, {"confirmed": 0, "refuted": 0})
    total = counts["confirmed"] + counts["refuted"]
    if total == 0:
        return 0.5
    score = counts["confirmed"] / total
    return max(0.0, min(1.0, score))


def reset_reputation() -> None:
    """Clear all reputation data (for testing)."""
    _reputation_counts.clear()
