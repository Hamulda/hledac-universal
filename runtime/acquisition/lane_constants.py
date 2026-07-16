"""
runtime/acquisition/lane_constants.py

Lane constants and risk levels.
Extracted from acquisition_strategy.py (original L491-570 + L806-817).

MODERNIZATION (Issue #18):
  - AcquisitionLane stays as-is (class with constants — simple, no msgspec needed)
  - RiskLevel stays as StrEnum — already optimal
  - TERMINAL_STATES / NON_TERMINAL_STATES kept here (were inline in acquisition_strategy.py)
"""


from enum import StrEnum


class AcquisitionLane:
    """
    Canonical acquisition lane identifiers.

    NOTE: FEED and PUBLIC lanes are NOT run via run_enabled_acquisition_lanes().
    They are run by SprintScheduler via its own pipeline calls.
    STEALTH lane is NOT run here — caller must explicitly enable it.
    """

    FEED = "FEED"
    PUBLIC = "PUBLIC"
    CT = "CT"
    WAYBACK = "WAYBACK"
    PASSIVE_DNS = "PASSIVE_DNS"
    BLOCKCHAIN = "BLOCKCHAIN"
    STEALTH = "STEALTH"
    PIVOT_EXECUTOR = "PIVOT_EXECUTOR"
    ACADEMIC = "ACADEMIC"
    IPFS = "IPFS"
    DOH = "DOH"
    OPEN_SOURCE = "OPEN_SOURCE"
    SHODAN = "SHODAN"
    CENSYS = "CENSYS"
    GREYNOISE = "GREYNOISE"

    @classmethod
    def values(cls) -> list[str]:
        return [
            cls.FEED,
            cls.PUBLIC,
            cls.CT,
            cls.WAYBACK,
            cls.PASSIVE_DNS,
            cls.BLOCKCHAIN,
            cls.STEALTH,
            cls.PIVOT_EXECUTOR,
            cls.ACADEMIC,
            cls.IPFS,
            cls.DOH,
            cls.OPEN_SOURCE,
            cls.SHODAN,
            cls.CENSYS,
            cls.GREYNOISE,
        ]


class RiskLevel(StrEnum):
    """Risk level for acquisition lanes."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# P1-03: Per-lane RAM budget (MB) for M1 8GB resource-aware scheduling.
# These are conservative estimates used by ResourceGovernor for informed
# throttling decisions. High-cost lanes trigger stricter concurrency limits.
# NOTE: Lane IDs are string values — not all lanes are in AcquisitionLane enum
# (e.g. "BGP", "NETWORK_RECON") so we use string literals directly.
LANE_RAM_BUDGETS: dict[str, int] = {
    # Critical cost lanes (heavy I/O + parsing)
    "BLOCKCHAIN": 120,  # ETH/BTC address analysis, Web3 RpcClient
    "SHODAN": 100,  # shodan API calls + response parsing
    "CENSYS": 100,  # censys API + certificate parsing
    "GREYNOISE": 80,  # greynoise API + threat intel parsing
    "NETWORK_RECON": 80,  # DNS/WHOIS/SSL enumeration
    # Medium cost lanes
    "BGP": 60,  # BGP AS paths, route views
    "PASSIVE_DNS": 60,  # passive DNS lookups
    "WAYBACK": 60,  # Wayback Machine CDX API
    "CT": 50,  # Certificate Transparency logs
    "DOH": 50,  # DNS-over-HTTPS lookups
    # Low cost lanes
    "ACADEMIC": 40,  # academic search API
    "OPEN_SOURCE": 40,  # OSINT collectors
    "STEALTH": 40,  # Tor/I2P transport overhead
    "PIVOT_EXECUTOR": 40,  # pivot planning + scheduling
    # Minimal cost lanes
    "IPFS": 20,  # IPFS gateway pings
    "FEED": 15,  # RSS/Atom feed parsing
    "PUBLIC": 10,  # SERP/public fetches (shared FetchCoordinator)
}
"""Per-lane RAM budgets in MB (M1 8GB calibrated, conservative estimates).

Used by ResourceGovernor for informed lane throttling decisions.
Lane costs are additive when multiple lanes run concurrently.
"""


def get_lane_ram_budget(lane_id: str) -> int:
    """
    Get RAM budget for a lane by its identifier.

    P1-03: Surfaces per-lane memory cost to ResourceGovernor so it can
    make informed throttling decisions based on which lanes are active.

    Returns:
        RAM budget in MB, defaults to 30MB for unknown lanes.
    """
    return LANE_RAM_BUDGETS.get(lane_id, 30)


# Terminal states — lanes that have reached a terminal condition
TERMINAL_STATES = frozenset({
    "COMPLETE",
    "COMPLETE_ZERO",
    "FETCH_ZERO_SUCCESS",
    "QUALITY_REJECTED",
    "DUPLICATE_REJECTED",
    "LOW_INFORMATION",
    "PROVIDER_ERROR",
    "DISCOVERY_ERROR",
    "TIMEOUT",
    "CIRCUIT_BREAKER_OPEN",
    "QUARANTINED",
    "SKIPPED",
    "MISSING_REQUIREMENT",
})

# Non-terminal states — lanes still in progress or soft terminal
NON_TERMINAL_STATES = frozenset({
    "PENDING",
    "RUNNING",
    "DISABLED",
    "ADVISORY",
    "UNMET_GATE",
})
