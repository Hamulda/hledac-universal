# hledac/universal/export/COMPAT_HANDOFF.py
# Sprint 8VX §C: ExportHandoff producer convergence audit
# Sprint 8VY §A: Updated — removal conditions tightened, compat scope explicit
# Sprint 8VZ §B: Updated — from_windup() is now explicitly compat-only
# ⚠️  DEPRECATED THIN ADAPTER — DO NOT EXTEND
"""
Canonical producer-side handoff truth (Sprint 8VZ):

  PRIMARY PATH (canonical post-8VZ): __main__._print_scorecard_report() builds
    ExportHandoff(...) directly, sourcing top_nodes from store.get_top_seed_nodes().
    This is the canonical producer construction point — no dict intermediary.

  COMPAT SEAM (post-8VZ): from_windup(scorecard) is kept only for legacy
    call-sites that pass raw scorecard dicts. It is NO LONGER the canonical
    path in __main__.py.

  SCORECARD ROLE (post-8VZ): scorecard_data dict is retained for:
    - DuckDB persistence (upsert_scorecard)
    - Markdown export (_export_markdown_report)
    - JSON report serialization (export_sprint → eh.scorecard)
    It is NOT the canonical source for top_nodes in the export handoff.

  FUTURE TARGET (post-full-cutover): windup_engine returns typed ExportHandoff
    directly; from_windup(scorecard) disappears entirely;
    ensure_export_handoff() shrinks to None-only path.

  REMOVAL CONDITIONS (updated post-8VZ):
    1. ensure_export_handoff() for dict → SHORTER: only for non-main call-sites
    2. ensure_export_handoff() for None → removal when all callers pass typed ExportHandoff
    3. from_windup(scorecard) → REMOVED from __main__ canonical path (8VZ)
    4. scorecard["top_graph_nodes"] as top_nodes source → REMOVED from canonical path (8VZ)
    5. store.get_top_seed_nodes() fallback → removal when windup always populates top_nodes

  WHAT THIS MODULE IS NOT:
    - NOT a new DTO system — ExportHandoff (types.py) is the only typed handoff
    - NOT an export framework — export plane is sprint_exporter.py
    - NOT a producer factory — __main__ constructs via direct ExportHandoff(), not via this module
    - NOT growing — new features go to windup_engine or types.py, not here
"""

# =============================================================================
# Sprint 8VY §A: ExportHandoff producer convergence
# =============================================================================
# Thin adapter: ExportHandoff | dict | None → ExportHandoff
# PRIMARY role: ensure export_sprint() always receives typed ExportHandoff.
#
# Two compat seams remaining:
#   A. dict → ExportHandoff: via from_windup(scorecard) extracting scorecard["top_graph_nodes"]
#      REMOVAL: when windup_engine returns typed ExportHandoff directly
#   B. None → empty ExportHandoff: for defensive None handling
#      REMOVAL: when __main__ always passes typed ExportHandoff (never None)
#
# Producer side: __main__._print_scorecard_report() → ExportHandoff(...) direct constructor
#                 top_nodes sourced from store.get_top_seed_nodes() (no from_windup in __main__)
# Consumer side: export_sprint() — receives typed ExportHandoff
# =============================================================================

from typing import TYPE_CHECKING, Any, Dict, Union

if TYPE_CHECKING:
    from hledac.universal.types import ExportHandoff  # noqa: F401


def ensure_export_handoff(
    handoff: Union["ExportHandoff", Dict[str, Any], None],  # type: ignore[name-defined]
    default_sprint_id: str = "unknown",
) -> "ExportHandoff":  # type: ignore[name-defined]
    """
    Normalize ExportHandoff | dict | None → ExportHandoff.

    Three dispatch cases:
      1. ExportHandoff instance → returned unchanged (PRIMARY canonical path)
      2. dict (scorecard) → via from_windup(scorecard) — COMPAT SEAM A
      3. None → empty ExportHandoff — COMPAT SEAM B

    Compat seam A (dict path):
      from_windup() extracts scorecard["top_graph_nodes"] → top_nodes.
      Two chained compat seams: windup dict → scorecard dict → ExportHandoff.
      REMOVAL CONDITION: windup_engine returns typed ExportHandoff directly.
      ACTIVE CALLERS: only non-main legacy call-sites (NOT __main__ — __main__ uses direct ctor).

    Compat seam B (None path):
      Returns empty ExportHandoff with default_sprint_id.
      REMOVAL CONDITION: __main__ always passes typed ExportHandoff (never None);
      currently active as defensive seam in export_sprint() consumer boundary.

    Args:
        handoff: ExportHandoff instance, dict (scorecard-style), or None
        default_sprint_id: fallback sprint_id if handoff is None

    Returns:
        ExportHandoff — always typed, never None

    NOTE: Thin compat adapter only. Does NOT create typed objects from raw facts.
          New features go to windup_engine or types.py ExportHandoff.from_windup().
    """
    # Import we need it at runtime
    from hledac.universal.types import ExportHandoff as TypesExportHandoff

    if handoff is None:
        # Compat seam B: defensive None handling
        return TypesExportHandoff(
            sprint_id=default_sprint_id,
            scorecard={},
            top_nodes=[],
        )

    if isinstance(handoff, TypesExportHandoff):
        # PRIMARY canonical path — ExportHandoff passed through unchanged
        return handoff

    if isinstance(handoff, dict):
        # Compat seam A: dict → typed via from_windup()
        # scorecard["top_graph_nodes"] → top_nodes is the current compat extraction
        # ACTIVE CALLERS: non-main legacy call-sites only; __main__ uses direct ctor.
        if not handoff:  # truthfulness guard: empty dict gets same treatment as None
            return TypesExportHandoff(
                sprint_id=default_sprint_id,
                scorecard={},
                top_nodes=[],
            )
        sprint_id = handoff.get("sprint_id", default_sprint_id)
        return TypesExportHandoff.from_windup(
            sprint_id=sprint_id,
            scorecard=handoff,
        )

    # Exhaustive: None handled above, ExportHandoff handled above, dict handled above.
    # Only reachable for truly unexpected types (neither None nor ExportHandoff nor dict).
    raise TypeError(
        f"ensure_export_handoff() got unexpected type: {type(handoff).__name__}. "
        f"Expected ExportHandoff, dict, or None."
    )
