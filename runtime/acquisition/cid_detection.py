"""
runtime/acquisition/cid_detection.py

IPFS CID (Content Identifier) detection — CIDv0 and CIDv1 base32.
Extracted from acquisition_strategy.py (original L514-548).

MODERNIZATION (Issue #18):
  - Module-level compiled regex (no per-call recompilation)
  - No heavy imports — fully self-contained
"""


import re

# R10: CID detection regex — bounded, no catastrophic backtracking
# CIDv0: Qm + 44 base58 chars = 46 chars total
_CIDV0_RE = re.compile(r"^Qm[A-Za-z2-7]{44}$")

# CIDv1 base32: bafy + 50-59 base32 chars
_CIDV1_BASE32_RE = re.compile(r"^bafy[a-z2-7]{50,59}$")


def _has_explicit_ipfs_cid(value: str) -> bool:
    """
    Return True if value is an explicit IPFS CID (CIDv0 or CIDv1 base32).

    GHOST_INVARIANTS:
      - No network I/O, no model/MLX load
      - Bounded: O(1) length check before regex
      - Fail-safe: returns False for malformed input
    """
    if not value or len(value) < 46 or len(value) > 70:
        return False
    if value.startswith("Qm") and len(value) == 46:
        return bool(_CIDV0_RE.match(value))
    if value.startswith("bafy"):
        return bool(_CIDV1_BASE32_RE.match(value))
    return False


def _extract_cids_from_text(text: str) -> list[str]:
    """
    Extract unique explicit CIDs from arbitrary text. Bounded dedup.

    GHOST_INVARIANTS:
      - No network I/O, no model/MLX load
      - Bounded: O(n) where n = word count, max ~1000 chars
      - Fail-safe: returns [] on any error
    """
    if not text:
        return []
    cids_seen: set[str] = set()
    cids: list[str] = []
    for word in text.split():
        word = word.strip().rstrip("/").rstrip(")")
        if _has_explicit_ipfs_cid(word) and word not in cids_seen:
            cids_seen.add(word)
            cids.append(word)
        # Also check for CID embedded in URL/path
        if "/" in word or ":" in word:
            for part in word.replace(":", "/").split("/"):
                part = part.strip()
                if _has_explicit_ipfs_cid(part) and part not in cids_seen:
                    cids_seen.add(part)
                    cids.append(part)
    return cids
