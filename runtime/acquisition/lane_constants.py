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
