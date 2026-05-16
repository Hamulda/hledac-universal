"""
Sprint 8TF: Phase Drift Guard — Pure Facts Helper
================================================

Tiny pre-seam scaffold for reading phase/model facts without implicit
cross-layer confusion.

PURPOSE:
  - Provide an authoritative single place to look up phase-layer facts
  - Prevent implicit conflation of workflow-level ↔ coarse-grained phases
  - Help future scheduler/shadow code to read phase facts correctly

WHAT THIS IS:
  - Pure facts helper (no state, no side effects, no new subsystem)
  - Late-import accessor (imported only when needed)
  - Compatibility alias surface

WHAT THIS IS NOT:
  - NOT a new model subsystem
  - NOT a new orchestrating class
  - NOT a cross-plane model API layer
  - NOT a replacement for ModelManager or ModelLifecycleManager

PHASE LAYERS:

  Layer 1 — Workflow-level (ModelManager.PHASE_MODEL_MAP):
    PLAN      → hermes
    DECIDE    → hermes
    GENERATE → hermes
    EMBED     → modernbert
    DEDUP     → modernbert
    ROUTING   → modernbert
    NER       → gliner
    ENTITY    → gliner

  Layer 2 — Coarse-grained (ModelLifecycleManager.enforce_phase_models):
    BRAIN     → hermes loaded, others released
    TOOLS     → hermes released, others on-demand
    SYNTHESIS → hermes loaded, others released
    CLEANUP   → all released

  Layer 3 — Windup-local (windup_engine.SynthesisRunner):
    Qwen/SmolLM isolated from runtime-wide model plane

AUTHORITY:
  This file is a COMPATIBILITY AID — not a new authority.
  The canonical owners remain:
    - ModelManager (acquire/load)
    - ModelLifecycleManager (phase enforcement facade)
    - brain.model_lifecycle (unload cleanup)

USAGE:
  from brain.model_phase_facts import (
      WORKFLOW_PHASES,
      COARSE_GRAINED_PHASES,
      is_workflow_phase,
      is_coarse_grained_phase,
      get_phase_layer,
  )
"""

from __future__ import annotations

# =============================================================================
# Layer 1 — Workflow-level phase strings
# =============================================================================
WORKFLOW_PHASES: frozenset[str] = frozenset({
    "PLAN",
    "DECIDE",
    "GENERATE",
    "EMBED",
    "DEDUP",
    "ROUTING",
    "NER",
    "ENTITY",
})

# =============================================================================
# Layer 2 — Coarse-grained phase strings
# =============================================================================
COARSE_GRAINED_PHASES: frozenset[str] = frozenset({
    "BRAIN",
    "TOOLS",
    "SYNTHESIS",
    "CLEANUP",
})

# =============================================================================
# Phase-layer classification
# =============================================================================
def get_phase_layer(phase: str) -> int:
    """
    Return which phase layer a string belongs to.

    Returns:
        1 = workflow-level (ModelManager.PHASE_MODEL_MAP)
        2 = coarse-grained (ModelLifecycleManager)
        0 = unknown/unclassified

    Note:
        Returns 0 for unknown phases — does NOT raise.
        This is intentional: future phases may be added without updating this file.
    """
    normalized = phase.upper()
    if normalized in WORKFLOW_PHASES:
        return 1
    if normalized in COARSE_GRAINED_PHASES:
        return 2
    return 0


def is_workflow_phase(phase: str) -> bool:
    """Return True if phase is a Layer 1 workflow-level phase string."""
    return phase.upper() in WORKFLOW_PHASES


def is_coarse_grained_phase(phase: str) -> bool:
    """Return True if phase is a Layer 2 coarse-grained phase string."""
    return phase.upper() in COARSE_GRAINED_PHASES


def is_same_layer(phase_a: str, phase_b: str) -> bool:
    """
    Return True if both phases belong to the same layer.

    This is the canonical check BEFORE comparing or mapping phases —
    comparing a Layer 1 phase (e.g., "GENERATE") with a Layer 2 phase
    (e.g., "SYNTHESIS") would be a category error.
    """
    return get_phase_layer(phase_a) == get_phase_layer(phase_b) != 0


# =============================================================================
# Sprint 8TF-R: Phase Drift Guard — Enforce/Assert helpers
# =============================================================================

def assert_no_cross_layer_mapping(phase: str, layer_hint: str = "") -> None:
    """
    Assert that a phase string is NOT being implicitly mapped across layers.

    Call this at the START of any function that takes a phase string and
    would naively compare it to phases from a DIFFERENT layer.

    This is a NO-OP guard — it does NOT raise. It logs a WARNING if a
    cross-layer risk is detected.

    Args:
        phase: The phase string to validate
        layer_hint: Optional string describing which layer the caller expects
                   (e.g., "caller uses Layer 1 workflow phases")

    Example:
        def my_phase_handler(phase_name: str):
            assert_no_cross_layer_mapping(phase_name, "Layer 1")
            # Now safe to compare with ModelManager.PHASE_MODEL_MAP keys
    """
    layer = get_phase_layer(phase)
    if layer == 0:
        # Unknown phase — fail open (future phases may exist)
        return

    # If a Layer 2 coarse-grained phase is being passed to something that
    # expects Layer 1, log a warning
    if layer == 2 and layer_hint.startswith("Layer 1"):
        import logging
        logging.getLogger(__name__).warning(
            f"[PHASE DRIFT GUARD] Cross-layer risk: '{phase}' is Layer 2 "
            f"(coarse-grained) but caller expects Layer 1 (workflow-level). "
            f"This may indicate implicit phase string mapping. "
            f"Use model_phase_facts.is_same_layer() to validate."
        )


def get_phase_layer_strict(phase: str) -> int:
    """
    Return which phase layer a string belongs to — STRICT mode.

    Unlike get_phase_layer(), this returns 0 for ANY phase string that
    appears in BOTH Layer 1 and Layer 2 (e.g., if a future phase name
    is reused across layers). Currently no overlap exists since
    GENERATE ≠ SYNTHESIS, but this function future-proofs the guard.

    Returns:
        1 = workflow-level (ModelManager.PHASE_MODEL_MAP)
        2 = coarse-grained (ModelLifecycleManager)
        0 = unknown / potential cross-layer collision

    Note:
        Returns 0 (unknown) for unrecognized phases — does NOT raise.
    """
    normalized = phase.upper()

    # Check for ambiguous phase names (same string in both layers)
    # Currently none exist: GENERATE ≠ SYNTHESIS
    # This check future-proofs against accidental reuse
    in_layer1 = normalized in WORKFLOW_PHASES
    in_layer2 = normalized in COARSE_GRAINED_PHASES

    if in_layer1 and in_layer2:
        # Collision: phase exists in both layers — cannot determine uniquely
        import logging
        logging.getLogger(__name__).warning(
            f"[PHASE DRIFT GUARD] Phase '{phase}' exists in both Layer 1 and "
            f"Layer 2. This is a cross-layer collision. Treating as unknown."
        )
        return 0

    if in_layer1:
        return 1
    if in_layer2:
        return 2
    return 0
