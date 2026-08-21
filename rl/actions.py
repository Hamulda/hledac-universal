"""
Konstanty pro akce agentů – sdílené napříč komponentami.
"""

ACTION_CONTINUE = 0
ACTION_FETCH_MORE = 1
ACTION_DEEP_DIVE = 2
ACTION_BRANCH = 3
ACTION_YIELD = 4
ACTION_LANE_SELECT = 10  # F265LANE: meta-action for lane selection mode

ACTION_NAMES = {
    ACTION_CONTINUE: "continue",
    ACTION_FETCH_MORE: "fetch_more",
    ACTION_DEEP_DIVE: "deep_dive",
    ACTION_BRANCH: "branch",
    ACTION_YIELD: "yield",
    ACTION_LANE_SELECT: "lane_select",
}

ACTION_DIM = 11  # 5 base actions + 6 lane selection combos (10-15)

# F265LANE: Bounded lane combination space (max 6 combos for M1 8GB)
# Each combo is a frozenset of lane names to enable
LANE_COMBINATIONS = [
    frozenset(["PUBLIC", "CT", "WAYBACK"]),  # baseline
    frozenset(["PUBLIC", "CT"]),  # drop WAYBACK
    frozenset(["CT", "WAYBACK", "DOH"]),  # drop PUBLIC (low yield)
    frozenset(["PUBLIC", "CT", "DOH"]),  # drop WAYBACK, add DOH
    frozenset(["CT", "PASSIVE_DNS", "WAYBACK"]),  # CT-focused
    frozenset(["PUBLIC", "CT", "PASSIVE_DNS"]),  # no WAYBACK
]

LANE_COMBINATION_NAMES = [
    "BASELINE",  # PUBLIC + CT + WAYBACK
    "NO_WAYBACK",  # PUBLIC + CT
    "NO_PUBLIC",  # CT + WAYBACK + DOH
    "DOH_REPLACE",  # PUBLIC + CT + DOH
    "CT_FOCUSED",  # CT + PASSIVE_DNS + WAYBACK
    "NO_WAYBACK_PDNS",  # PUBLIC + CT + PASSIVE_DNS
]


# Mapping: action index → LANE_COMBINATIONS index
# Actions 10-15 map to LANE_COMBINATIONS[0-5]
def lane_combo_from_action(action: int) -> int | None:
    """Map action index to lane combo index. Returns None if not a lane select action."""
    if 10 <= action <= 15:
        return action - 10
    return None


def action_from_lane_combo(combo_idx: int) -> int:
    """Map lane combo index to action index."""
    return 10 + combo_idx
