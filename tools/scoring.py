"""
Lead scoring and contradiction utilities (no dependencies on internal storage).
"""

import re
import time


def normalize_text(text: str) -> str:
    """Normalize text for contradiction detection (lowercase, trim, remove punctuation)."""
    text = text.lower().strip()
    text = re.sub(r'[^\w\s]', '', text)
    return re.sub(r'\s+', ' ', text).strip()


def has_contradiction(
    object_variants: list[str],
    predicate: str,
    whitelist: set[str],
    domain_sets: list[set[str]]
) -> bool:
    """
    Determine if a set of object variants represents a significant contradiction.
    - predicate must be whitelisted
    - at least 2 variants must have independent domains (union of their domains >= 2)
    - after normalization, at least two variants must differ
    """
    if predicate not in whitelist:
        return False
    if len(object_variants) < 2 or len(domain_sets) < 2:
        return False

    # Normalize all variants
    norm_variants = [normalize_text(v) for v in object_variants]

    # Check if any two variants differ
    differing = False
    for i in range(len(norm_variants)):
        for j in range(i + 1, len(norm_variants)):
            if norm_variants[i] != norm_variants[j]:
                # Check domain independence for this pair
                domains_i = domain_sets[i] if i < len(domain_sets) else set()
                domains_j = domain_sets[j] if j < len(domain_sets) else set()
                if len(domains_i | domains_j) >= 2:
                    differing = True
                    break
        if differing:
            break

    return differing


class LeadScore:
    """Lead scoring for entities/claims (stateless)."""

    @staticmethod
    def compute_score(centrality: int, created_at: float, current_time: float | None = None) -> float:
        """Calculate lead score = centrality * (1 - min(1.0, age_hours / 24))."""
        if current_time is None:
            current_time = time.time()
        age_hours = max(0.0, (current_time - created_at) / 3600.0)
        recency_factor = min(1.0, age_hours / 72.0)
        return float(centrality) * (1.0 - recency_factor)
