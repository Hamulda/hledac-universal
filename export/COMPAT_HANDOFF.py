# hledac/universal/export/COMPAT_HANDOFF.py
# ⚠️  DEPRECATED THIN ADAPTER — DO NOT EXTEND
# Sprint F186C: Header shrunk; module is a compat seam, not a factory.
"""
Thin adapter: normalize any handoff input to typed ExportHandoff at the
export_sprint() consumer boundary.

Canonical producer: __main__._print_scorecard_report() constructs
ExportHandoff(...) directly — this module is NOT the producer construction point.

Compat seams preserved for backward compat (NOT exercised by canonical producer):
  A. dict → via from_windup(scorecard) — for legacy callers passing scorecard dicts
  B. None → empty ExportHandoff — defensive boundary for missing handoff

ensure_export_handoff() is a consumer-side normalization seam, NOT a factory.
New features go to windup_engine or types.py, not here.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hledac.universal.project_types import ExportHandoff  # noqa: F401


def ensure_export_handoff(
    handoff: ExportHandoff | dict[str, Any] | None,  # type: ignore[name-defined]
    default_sprint_id: str = "unknown",
) -> ExportHandoff:  # type: ignore[name-defined]
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
    from hledac.universal.project_types import ExportHandoff as TypesExportHandoff

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
