"""F229A: Nonfeed Corroboration Score v1 — lane-outcome-level scoring.

Pure scoring from SprintSchedulerResult.src_family_outcomes.
No LLM. No network. No raw evidence text storage.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

# ----------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------
_FEED = "feed"
_PUBLIC = "public"
_CT = "ct"
_DOH = "doh"
_WAYBACK = "wayback"
_PASSIVE_DNS = "passive_dns"

_NONFEED_FAMILIES = {_CT, _DOH, _WAYBACK, _PASSIVE_DNS}

_TERMINAL_COMPLETED = "COMPLETED"
_TERMINAL_NO_RESULTS = "ATTEMPTED_NO_RESULTS"

_MAX_SCORE = 1.0
_MIN_SCORE = 0.0


# ----------------------------------------------------------------------
# Dataclass
# ----------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class LaneCorroborationScore:
    """Lane-level corroboration score for a sprint result.

    Attributes
    ----------
    corroboration_score : float
        0.0–1.0, capped.
    corroborating_families : tuple[str, ...]
        Families that contributed positively to the score.
    corroboration_reason : str
        Human-readable summary of why the score is what it is.
    """

    corroboration_score: float
    corroborating_families: tuple[str, ...]
    corroboration_reason: str


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _terminal_ok(state: str | None) -> bool:
    """True when the lane terminal state counts as a successful outcome."""
    return state in (_TERMINAL_COMPLETED, _TERMINAL_NO_RESULTS)


def _nonfeed_terminal(family: str, outcomes: dict) -> bool:
    """True when a nonfeed family reached a terminal state (completed/no-results)."""
    fam_data = outcomes.get(family, {})
    ts = fam_data.get("terminal_state")
    return _terminal_ok(ts)


# ----------------------------------------------------------------------
# Core scorer
# ----------------------------------------------------------------------
def score_lane_outcomes(
    *,
    feed_present: bool,
    public_present: bool,
    ct_terminal: bool,
    doh_terminal: bool,
    wayback_terminal: bool,
    passive_dns_terminal: bool,
    seed_context_available: bool,
    nonfeed_terminal_count: int,
    public_discovery_zero_results: bool = False,
) -> LaneCorroborationScore:
    """Compute corroboration score from lane terminal states.

    Parameters
    ----------
    feed_present :
        Feed lane produced at least one finding.
    public_present :
        Public lane produced at least one finding.
    ct_terminal :
        CT lane reached terminal (completed / no-results).
    doh_terminal :
        DOH lane reached terminal.
    wayback_terminal :
        Wayback lane reached terminal.
    passive_dns_terminal :
        PassiveDNS lane reached terminal.
    seed_context_available :
        Seed context was available for this sprint.
    nonfeed_terminal_count :
        Number of nonfeed families that are terminal (for +0.10 bonus).
    public_discovery_zero_results :
        Public lane returned DISCOVERY_ZERO_RESULTS.
    """
    score = 0.0
    families: list[str] = []

    # Additive components
    if feed_present:
        score += 0.20
        families.append(_FEED)

    if public_present:
        score += 0.20
        families.append(_PUBLIC)

    if ct_terminal:
        score += 0.25
        families.append(_CT)

    if doh_terminal:
        score += 0.25
        families.append(_DOH)

    if wayback_terminal:
        score += 0.20
        families.append(_WAYBACK)

    if passive_dns_terminal:
        score += 0.20
        families.append(_PASSIVE_DNS)

    if seed_context_available:
        score += 0.10
        families.append("seed_context")

    if nonfeed_terminal_count >= 2:
        score += 0.10
        families.append("multi_nonfeed_confirm")

    # Feed-only penalty
    if feed_present and not (public_present or ct_terminal or doh_terminal):
        score -= 0.25

    # Nonfeed expected but all missing penalty
    nonfeed_missed = (
        ct_terminal == False
        and doh_terminal == False
        and wayback_terminal == False
        and passive_dns_terminal == False
    )
    if not feed_present and nonfeed_missed and nonfeed_terminal_count == 0:
        score -= 0.20

    # Zero-results penalty on public
    if public_discovery_zero_results:
        score -= 0.10

    # Clamp
    score = max(_MIN_SCORE, min(_MAX_SCORE, score))

    # Build reason string
    if not feed_present and nonfeed_terminal_count == 0:
        reason = "no corroborating sources; score near zero"
    elif nonfeed_terminal_count >= 3:
        reason = f"strong corroboration across {nonfeed_terminal_count} nonfeed families"
    elif nonfeed_terminal_count >= 1:
        reason = f"{nonfeed_terminal_count} nonfeed family confirmed the seed"
    elif feed_present and public_present:
        reason = "feed + public only; low corroboration depth"
    elif feed_present:
        reason = "feed-only; minimal corroboration"
    else:
        reason = f"corroboration score {score:.2f}"

    return LaneCorroborationScore(
        corroboration_score=round(score, 2),
        corroborating_families=tuple(families),
        corroboration_reason=reason,
    )


# ----------------------------------------------------------------------
# Passthrough from SprintSchedulerResult fields
# ----------------------------------------------------------------------
def score_from_result(result: object) -> LaneCorroborationScore:
    """Score corroboration given a SprintSchedulerResult instance.

    Reads lane terminal states from ``result.src_family_outcomes``.
    """
    outcomes: dict = getattr(result, "src_family_outcomes", {}) or {}

    def _present(fam: str) -> bool:
        fd = outcomes.get(fam, {})
        return bool(fd.get("accepted_count", 0) > 0)

    def _terminal(fam: str) -> bool:
        return _nonfeed_terminal(fam, outcomes)

    # Count nonfeed terminals
    nonfeed_terminals = sum(
        1 for f in _NONFEED_FAMILIES if _terminal(f)
    )

    # Detect zero-results flag from public lane
    public_zero = outcomes.get(_PUBLIC, {}).get("terminal_state") == _TERMINAL_NO_RESULTS

    return score_lane_outcomes(
        feed_present=_present(_FEED),
        public_present=_present(_PUBLIC),
        ct_terminal=_terminal(_CT),
        doh_terminal=_terminal(_DOH),
        wayback_terminal=_terminal(_WAYBACK),
        passive_dns_terminal=_terminal(_PASSIVE_DNS),
        seed_context_available=getattr(result, "seed_context_available", False),
        nonfeed_terminal_count=nonfeed_terminals,
        public_discovery_zero_results=bool(public_zero),
    )