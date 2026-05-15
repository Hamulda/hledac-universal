"""
F220B: Pivot Lane Planner for DOH/CT/Wayback/Nonfeed Lanes

Determines which nonfeed lanes should run for each pivot seed type.
Pure, no network — returns a plan only.

Seed type → lane mapping:
  domain → DOH + CT + WAYBACK + PASSIVE_DNS
  url    → WAYBACK + PUBLIC
  ip     → BGP + PASSIVE_DNS (+ DOH reverse if supported)
  hash   → no-op (malware lookup not supported in this scope)
  entity → PUBLIC (public provider rescue)
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


# ----------------------------------------------------------------------
# Output DTOs
# ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LanePlanItem:
    """A single planned lane invocation for a pivot seed."""

    lane: str  # e.g. "DOH", "CT", "WAYBACK"
    seed_value: str
    seed_type: str
    priority: float
    reason: str


@dataclass(frozen=True, slots=True)
class PivotLanePlan:
    """Complete lane plan for a set of pivot seeds."""

    items: tuple[LanePlanItem, ...]
    skipped: tuple[str, ...]  # seed values that were skipped (e.g. unsupported hash)
    reason: str


# ----------------------------------------------------------------------
# Priority constants
# ----------------------------------------------------------------------

_PRIORITY_DOMAIN_DOH: float = 0.80
_PRIORITY_DOMAIN_CT: float = 0.85
_PRIORITY_DOMAIN_WAYBACK: float = 0.70
_PRIORITY_DOMAIN_PASSIVE_DNS: float = 0.75

_PRIORITY_URL_WAYBACK: float = 0.80
_PRIORITY_URL_PUBLIC: float = 0.75

_PRIORITY_IP_BGP: float = 0.85
_PRIORITY_IP_PASSIVE_DNS: float = 0.75
_PRIORITY_IP_DOH_REVERSE: float = 0.60  # lower — conditional

_PRIORITY_ENTITY_PUBLIC: float = 0.70


# ----------------------------------------------------------------------
# Core planner
# ----------------------------------------------------------------------


def plan_lanes_for_pivot_seeds(
    seeds: Sequence[dict],
    *,
    max_items: int = 128,
    enable_doh: bool = True,
    enable_ct: bool = True,
    enable_wayback: bool = True,
    enable_passive_dns: bool = True,
    enable_bgp: bool = True,
) -> PivotLanePlan:
    """
    Plan which nonfeed lanes to run for each pivot seed.

    Args:
        seeds: PivotSeed objects (or duck-typed objects with value, seed_type).
        max_items: Hard cap on total plan items (default 128).
        enable_*: Feature flags per lane family.

    Returns:
        PivotLanePlan with items, skipped list, and reason string.
    """
    if not seeds:
        return PivotLanePlan(
            items=(),
            skipped=(),
            reason="no_seeds",
        )

    items: list[LanePlanItem] = []
    skipped: list[str] = []
    seen_pairs: set[tuple[str, str]] = set()  # (lane, seed_value) dedupe

    for seed in seeds:
        seed_value = getattr(seed, "value", None) or ""
        seed_type = getattr(seed, "seed_type", None) or ""

        if not seed_value or not seed_type:
            continue

        # --- domain → DOH + CT + WAYBACK + PASSIVE_DNS ---
        if seed_type == "domain":
            _plan_domain(
                seed_value,
                seed_type,
                items,
                seen_pairs,
                enable_doh=enable_doh,
                enable_ct=enable_ct,
                enable_wayback=enable_wayback,
                enable_passive_dns=enable_passive_dns,
            )

        # --- url → WAYBACK + PUBLIC ---
        elif seed_type == "url":
            _plan_url(
                seed_value,
                seed_type,
                items,
                seen_pairs,
                enable_wayback=enable_wayback,
            )

        # --- ip → BGP + PASSIVE_DNS + DOH reverse ---
        elif seed_type == "ip":
            _plan_ip(
                seed_value,
                seed_type,
                items,
                seen_pairs,
                enable_doh=enable_doh,
                enable_passive_dns=enable_passive_dns,
                enable_bgp=enable_bgp,
            )

        # --- hash → no-op (unsupported in this scope) ---
        elif seed_type in ("hash", "md5", "sha1", "sha256"):
            skipped.append(seed_value)

        # --- entity → PUBLIC rescue ---
        elif seed_type == "entity":
            _plan_entity(seed_value, seed_type, items, seen_pairs)

        # Unknown types: skip silently

    # Enforce max_items bound
    if len(items) > max_items:
        items = items[:max_items]

    return PivotLanePlan(
        items=tuple(items),
        skipped=tuple(skipped),
        reason=_build_reason(items, skipped, len(seeds)),
    )


# ----------------------------------------------------------------------
# Per-type planners
# ----------------------------------------------------------------------


def _plan_domain(
    seed_value: str,
    seed_type: str,
    items: list[LanePlanItem],
    seen_pairs: set[tuple[str, str]],
    *,
    enable_doh: bool,
    enable_ct: bool,
    enable_wayback: bool,
    enable_passive_dns: bool,
) -> None:
    """domain → DOH + CT + WAYBACK + PASSIVE_DNS"""
    pair = ("DOH", seed_value)
    if enable_doh and pair not in seen_pairs:
        seen_pairs.add(pair)
        items.append(
            LanePlanItem(
                lane="DOH",
                seed_value=seed_value,
                seed_type=seed_type,
                priority=_PRIORITY_DOMAIN_DOH,
                reason="domain_doh_lookup",
            )
        )

    pair = ("CT", seed_value)
    if enable_ct and pair not in seen_pairs:
        seen_pairs.add(pair)
        items.append(
            LanePlanItem(
                lane="CT",
                seed_value=seed_value,
                seed_type=seed_type,
                priority=_PRIORITY_DOMAIN_CT,
                reason="domain_ct_lookup",
            )
        )

    pair = ("WAYBACK", seed_value)
    if enable_wayback and pair not in seen_pairs:
        seen_pairs.add(pair)
        items.append(
            LanePlanItem(
                lane="WAYBACK",
                seed_value=seed_value,
                seed_type=seed_type,
                priority=_PRIORITY_DOMAIN_WAYBACK,
                reason="domain_wayback_archive",
            )
        )

    pair = ("PASSIVE_DNS", seed_value)
    if enable_passive_dns and pair not in seen_pairs:
        seen_pairs.add(pair)
        items.append(
            LanePlanItem(
                lane="PASSIVE_DNS",
                seed_value=seed_value,
                seed_type=seed_type,
                priority=_PRIORITY_DOMAIN_PASSIVE_DNS,
                reason="domain_passive_dns",
            )
        )


def _plan_url(
    seed_value: str,
    seed_type: str,
    items: list[LanePlanItem],
    seen_pairs: set[tuple[str, str]],
    *,
    enable_wayback: bool,
) -> None:
    """url → WAYBACK + PUBLIC"""
    pair = ("WAYBACK", seed_value)
    if enable_wayback and pair not in seen_pairs:
        seen_pairs.add(pair)
        items.append(
            LanePlanItem(
                lane="WAYBACK",
                seed_value=seed_value,
                seed_type=seed_type,
                priority=_PRIORITY_URL_WAYBACK,
                reason="url_wayback_archive",
            )
        )

    pair = ("PUBLIC", seed_value)
    if pair not in seen_pairs:
        seen_pairs.add(pair)
        items.append(
            LanePlanItem(
                lane="PUBLIC",
                seed_value=seed_value,
                seed_type=seed_type,
                priority=_PRIORITY_URL_PUBLIC,
                reason="url_public_fetch",
            )
        )


def _plan_ip(
    seed_value: str,
    seed_type: str,
    items: list[LanePlanItem],
    seen_pairs: set[tuple[str, str]],
    *,
    enable_doh: bool,
    enable_passive_dns: bool,
    enable_bgp: bool,
) -> None:
    """ip → BGP + PASSIVE_DNS + DOH reverse (lower priority)"""
    pair = ("BGP", seed_value)
    if enable_bgp and pair not in seen_pairs:
        seen_pairs.add(pair)
        items.append(
            LanePlanItem(
                lane="BGP",
                seed_value=seed_value,
                seed_type=seed_type,
                priority=_PRIORITY_IP_BGP,
                reason="ip_bgp_lookup",
            )
        )

    pair = ("PASSIVE_DNS", seed_value)
    if enable_passive_dns and pair not in seen_pairs:
        seen_pairs.add(pair)
        items.append(
            LanePlanItem(
                lane="PASSIVE_DNS",
                seed_value=seed_value,
                seed_type=seed_type,
                priority=_PRIORITY_IP_PASSIVE_DNS,
                reason="ip_passive_dns",
            )
        )

    # DOH reverse lookup for IPs (lower priority — conditional)
    pair = ("DOH", seed_value)
    if enable_doh and pair not in seen_pairs:
        seen_pairs.add(pair)
        items.append(
            LanePlanItem(
                lane="DOH",
                seed_value=seed_value,
                seed_type=seed_type,
                priority=_PRIORITY_IP_DOH_REVERSE,
                reason="ip_doh_reverse",
            )
        )


def _plan_entity(
    seed_value: str,
    seed_type: str,
    items: list[LanePlanItem],
    seen_pairs: set[tuple[str, str]],
) -> None:
    """entity → PUBLIC (public provider rescue)"""
    pair = ("PUBLIC", seed_value)
    if pair not in seen_pairs:
        seen_pairs.add(pair)
        items.append(
            LanePlanItem(
                lane="PUBLIC",
                seed_value=seed_value,
                seed_type=seed_type,
                priority=_PRIORITY_ENTITY_PUBLIC,
                reason="entity_public_rescue",
            )
        )


def _build_reason(items: list[LanePlanItem], skipped: list[str], total_seeds: int) -> str:
    """Build a human-readable reason string for the plan."""
    lanes_planned = sorted({item.lane for item in items})
    if not lanes_planned:
        reason = "no_viable_lanes"
    elif skipped:
        reason = f"planned_{len(items)}_items_{len(skipped)}_skipped_from_{total_seeds}_seeds"
    else:
        reason = f"planned_{len(items)}_items_from_{total_seeds}_seeds_lanes_{','.join(lanes_planned)}"
    return reason