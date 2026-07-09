"""
intel_seed.py — APT domain seed mappings with runtime config loading.

Home for threat-intel seed data that was previously hardcoded in
runtime/sprint_scheduler.py (_KNOWN_APT_ONION_DOMAINS).

Design principles:
  1. Lazy YAML loading — only reads disk on first access, cached after.
  2. Confidence-tiered — confirmed C2 vs plausible vs unconfirmed.
  3. Fail-safe — falls back to empty dict if YAML missing/corrupt.
  4. Always-on — no feature flag gate; this is a data-only module.
  5. Thread-safe — uses LazySingleton for one-time initialization.

Confidence tiers:
  - confirmed  → seed immediately (real C2 infrastructure)
  - plausible  → seed after confirmed pool exhausted
  - unconfirmed → NOT seeded automatically — requires CT verification

Usage:
  from intel.intel_seed import AptOnionSeeder
  seeder = AptOnionSeeder()
  candidates = seeder.get_candidates_for_query("LockBit BlackCat")
  # Returns list of (domain, confidence) tuples
"""

from __future__ import annotations

import os
import logging
import time
from pathlib import Path
from typing import Any

from utils.lazy_singleton import LazySingleton

log = logging.getLogger(__name__)


def _get_config_path() -> Path:
    """Return path to apt_onion_mapping.yaml.

    Resolution order:
      1. HLEDAC_APT_ONION_MAPPING env var (absolute path)
      2. <repo_root>/config/apt_onion_mapping.yaml
      3. <repo_root>/intel/apt_onion_mapping.yaml (fallback)
    """
    env_path = os.environ.get("HLEDAC_APT_ONION_MAPPING", "").strip()
    if env_path:
        return Path(env_path)

    # Repo root — assumes intel/ is <repo_root>/intel/
    repo_root = Path(__file__).resolve().parent.parent
    config_path = repo_root / "config" / "apt_onion_mapping.yaml"
    if config_path.exists():
        return config_path

    # Fallback: alongside this module
    fallback = Path(__file__).parent / "apt_onion_mapping.yaml"
    return fallback


def _load_yaml() -> dict[str, list[tuple[str, float]]]:
    """Load and parse apt_onion_mapping.yaml.

    Called exactly once via LazySingleton. Fail-safe: returns empty dict on error.
    """
    yaml_path = _get_config_path()

    if not yaml_path.exists():
        log.warning(
            "intel_seed: apt_onion_mapping.yaml not found at %s — using empty mapping",
            yaml_path,
        )
        return {}

    try:
        import yaml as _yaml

        with open(yaml_path, encoding="utf-8") as f:
            raw = _yaml.safe_load(f)
        actors = raw.get("actors", {}) if isinstance(raw, dict) else {}

        result: dict[str, list[tuple[str, float]]] = {}
        for actor_name, actor_data in actors.items():
            if not isinstance(actor_data, dict):
                continue
            domains = actor_data.get("domains", [])
            if not domains:
                continue
            # confidence: confirmed=1.0, plausible=0.7, unconfirmed=0.3
            confidence_str = actor_data.get("confidence", "unconfirmed")
            confidence_map = {"confirmed": 1.0, "plausible": 0.7, "unconfirmed": 0.3}
            confidence = confidence_map.get(confidence_str, 0.3)

            result[actor_name.lower()] = [
                (d, confidence) for d in domains if isinstance(d, str)
            ]

        log.debug(
            "intel_seed: loaded %d APT actors from %s",
            len(result),
            yaml_path,
        )
        return result

    except Exception as e:
        log.warning("intel_seed: failed to load %s — %s", yaml_path, e)
        return {}


# Thread-safe lazy cache — initialized exactly once, even under concurrent access
_cache: LazySingleton[dict[str, list[tuple[str, float]]]] = LazySingleton(_load_yaml)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class AptOnionSeeder:
    """Query APT actor→.onion domain mappings with confidence scoring.

    Instantiate per-sprint (module-level LazySingleton cache is the optimization).
    """

    __slots__ = ("_mapping",)

    def __init__(self) -> None:
        # Bypass __slots__ restriction via object.__setattr__
        object.__setattr__(self, "_mapping", _cache())

    def get_candidates_for_query(
        self,
        query: str,
        min_confidence: float = 0.7,
    ) -> list[tuple[str, float]]:
        """Return (.onion domain, confidence) candidates for a query string.

        Matches actor name as substring (case-insensitive).
        Filters by minimum confidence threshold.

        Args:
            query: Original sprint query (e.g. "LockBit BlackCat AlphV")
            min_confidence: Minimum confidence to return (default 0.7 = plausible+)

        Returns:
            List of (onion_domain, confidence) tuples, deduped, ordered by
            appearance in query.
        """
        if not query or not self._mapping:
            return []

        query_lower = query.lower()
        seen: set[str] = set()
        results: list[tuple[str, float]] = []

        for actor_name, domain_list in self._mapping.items():
            if actor_name in query_lower:
                for domain, confidence in domain_list:
                    if domain not in seen and confidence >= min_confidence:
                        seen.add(domain)
                        results.append((domain, confidence))

        return results

    def get_all_candidates(
        self,
        confidence: str = "confirmed",
    ) -> list[str]:
        """Return all domains for a given confidence tier.

        Args:
            confidence: "confirmed" | "plausible" | "unconfirmed" | "all"

        Returns:
            List of onion domains.
        """
        if confidence == "all":
            out: list[str] = []
            for _, domain_list in self._mapping.items():
                out.extend(d for d, _ in domain_list)
            return out

        threshold_map = {"confirmed": 1.0, "plausible": 0.7, "unconfirmed": 0.3}
        threshold = threshold_map.get(confidence, 0.7)

        out: list[str] = []
        for _, domain_list in self._mapping.items():
            out.extend(d for d, c in domain_list if c >= threshold)
        return out

    @property
    def actor_count(self) -> int:
        """Number of actors in the loaded mapping."""
        return len(self._mapping)

    def reload(self) -> None:
        """Force-reload the YAML from disk (clears cache)."""
        _cache.reset()
        object.__setattr__(self, "_mapping", _cache())


# ---------------------------------------------------------------------------
# Standalone function — drop-in replacement for _ooda_apt_domain_mapping
# ---------------------------------------------------------------------------


def get_apt_onion_candidates(query: str) -> list[str]:
    """Return APT-mapped .onion domain candidates for a query string.

    Replaces hardcoded _ooda_apt_domain_mapping() in sprint_scheduler.py.

    Confidence filter: only returns confirmed (1.0) + plausible (0.7) domains.
    Unconfirmed (0.3) domains are NOT returned — they need CT verification first.

    Args:
        query: Sprint query string.

    Returns:
        List of onion domain strings (no confidence value).
    """
    seeder = AptOnionSeeder()
    candidates = seeder.get_candidates_for_query(query, min_confidence=0.7)
    return [domain for domain, _ in candidates]
