"""
F11: Deep research budget map — module-level pure function.

Decoupled from SprintScheduler so the resolution logic is testable in
isolation. SprintScheduler._run_enhanced_research imports this and
calls ``resolve_deep_research_budget_s(extreme_mode=...)``.

GHOST_INVARIANTS: pure function, named except, fail-safe default 60 s.
"""



from typing import Final

# Seconds per ResearchMode tier (M1 8GB UMA ceiling).
# Mapped 1:1 with project_types.ResearchMode strings.
_BUDGET_BY_MODE: Final[dict[str, float]] = {
    "autonomous": 180.0,
    "extreme": 120.0,
    "deep": 60.0,
    "standard": 60.0,
    "quick": 30.0,
}


def resolve_deep_research_budget_s(extreme_mode: bool) -> float:
    """Return F11 deep-research wall-clock budget in seconds.

    Args:
        extreme_mode: True → EXHAUSTIVE depth → 120 s budget. False → ADVANCED → 60 s.

    Returns:
        Wall-clock budget for the deep research call. Conservative defaults
        match the F11 spec; capped by the outer ``_run_enhanced_research_async``
        180 s hard limit.

    Never raises. Pure function (no I/O, no module state) so it is
    testable in isolation.
    """
    try:
        if extreme_mode:
            return _BUDGET_BY_MODE["extreme"]
        return _BUDGET_BY_MODE["deep"]
    except (KeyError, TypeError):
        # Fail-safe: any unexpected input returns DEEP-equivalent (60 s)
        return 60.0


__all__ = ["resolve_deep_research_budget_s"]
