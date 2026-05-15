"""Sprint F217: Evidence Grounding Validator.

Deterministic string matching validator — verifies IOC/claim strings
appear in the original evidence text. No ML, no LLM, no embeddings.

Bounded:
- max 64 claimed items
- evidence capped at 200k chars
- case-insensitive default
- exact substring match v1
"""

from __future__ import annotations

from dataclasses import dataclass


REASON_ALL_ITEMS_GROUNDED = "all_items_grounded"
REASON_MISSING_ITEMS = "missing_items"
REASON_NO_CLAIMED_ITEMS = "no_claimed_items"
REASON_EMPTY_EVIDENCE = "empty_evidence"


@dataclass(frozen=True)
class GroundingValidationResult:
    """Result of grounding validation.

    Attributes:
        grounded: True if all claimed items found in evidence.
        checked_items: Number of items checked (may be less than claimed if cap hit).
        matched_items: Number of items matched in evidence.
        missing_items: Tuple of items NOT found in evidence.
        reason: Short reason code.
    """

    grounded: bool
    checked_items: int
    matched_items: int
    missing_items: tuple[str, ...]
    reason: str


def validate_strings_grounded(
    claimed_items: list[str] | tuple[str, ...],
    evidence_text: str,
    *,
    case_sensitive: bool = False,
    max_items: int = 64,
    max_evidence_chars: int = 200_000,
) -> GroundingValidationResult:
    """Validate that claimed IOC/claim strings appear in evidence text.

    Args:
        claimed_items: List of strings to check (IOCs, domains, IPs, etc.).
        evidence_text: The source evidence text to check against.
        case_sensitive: If True, do case-sensitive matching. Default False.
        max_items: Maximum number of items to check. Default 64.
        max_evidence_chars: Maximum evidence chars to process. Default 200_000.

    Returns:
        GroundingValidationResult with grounded flag and details.

    Reasons:
        - all_items_grounded: all checked items found in evidence
        - missing_items: one or more items not found
        - no_claimed_items: claimed_items was empty
        - empty_evidence: evidence_text was empty
    """
    evidence_stripped = evidence_text.strip()
    if not evidence_stripped:
        return GroundingValidationResult(
            grounded=False,
            checked_items=0,
            matched_items=0,
            missing_items=(),
            reason=REASON_EMPTY_EVIDENCE,
        )

    if not claimed_items:
        return GroundingValidationResult(
            grounded=False,
            checked_items=0,
            matched_items=0,
            missing_items=(),
            reason=REASON_NO_CLAIMED_ITEMS,
        )

    items_to_check = list(claimed_items[:max_items])
    evidence = evidence_stripped[:max_evidence_chars]

    missing = []
    matched = 0

    for item in items_to_check:
        if not item:
            continue
        search_item = item if case_sensitive else item.lower()
        search_evidence = evidence if case_sensitive else evidence.lower()
        if search_item in search_evidence:
            matched += 1
        else:
            missing.append(item)

    checked = len(items_to_check)
    all_grounded = len(missing) == 0

    reason = REASON_ALL_ITEMS_GROUNDED if all_grounded else REASON_MISSING_ITEMS

    return GroundingValidationResult(
        grounded=all_grounded,
        checked_items=checked,
        matched_items=matched,
        missing_items=tuple(missing),
        reason=reason,
    )