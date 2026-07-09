"""
runtime/acquisition/mission.py

Nonfeed mission controller — coordinates lane family expectations.
Extracted from acquisition_strategy.py (original L1138-1250 + L2738-3250).

MODERNIZATION (Issue #18):
  - NonfeedMissionController, MissionIntent, MissionTargetKind in one module
  - infer_mission_intent() isolated here
  - _NONFEED_LANE_FAMILY_MAP kept internal

GHOST_INVARIANTS:
  - No network I/O, no model/MLX load
  - Fail-safe: returns default mission intent on error
"""

from __future__ import annotations

from enum import Enum


# ── Lane family map ─────────────────────────────────────────────────────────────────

_NONFEED_LANE_FAMILY_MAP: dict[str, str] = {
    "PUBLIC": "PUBLIC",
    "CT": "CT",
    "PIVOT_EXECUTOR": "PIVOT_EXECUTOR",
    "WAYBACK": "WAYBACK",
    "PASSIVE_DNS": "PASSIVE_DNS",
}


# ── MissionIntent ───────────────────────────────────────────────────────────────────


class MissionIntent(Enum):
    """F217B: Canonical mission intent taxonomy for nonfeed_diagnostic profile."""

    DOMAIN_RECON = "domain_recon"
    PERSON_RECON = "person_recon"
    INFRA_RECON = "infra_recon"
    CVE_RECON = "cve_recon"
    UNKNOWN = "unknown"


# ── MissionTargetKind ─────────────────────────────────────────────────────────────────


class MissionTargetKind(Enum):
    """F217B: Target kind for mission telemetry."""

    DOMAIN = "domain"
    IP = "ip"
    URL = "url"
    PERSON = "person"
    CRYPTO = "crypto"
    UNKNOWN = "unknown"


# ── NonfeedMissionController ─────────────────────────────────────────────────────────


class NonfeedMissionController:
    """
    F217B: Canonical nonfeed mission contract for nonfeed_diagnostic profile.

    Coordinates lane family expectations without benchmark-owned logic.
    For acquisition_profile=nonfeed_diagnostic:
      - Required lane families: PUBLIC, CT, PIVOT_EXECUTOR
      - Optional lane families: WAYBACK, PASSIVE_DNS
      - FEED is capped until required nonfeed lanes are terminal
      - Mission finishes only when each required family has:
          accepted evidence
          OR explicit terminal state
          OR explicit provider failure
          OR explicit memory skip

    IMPORTANT — what does NOT count as accepted evidence:
      - CT quarantine is NOT accepted evidence (raw hits rejected by bridge criteria)
      - Quality rejection ledger is NOT accepted evidence (quality gate rejection)
      - PUBLIC explicit failure (FETCH_ZERO_SUCCESS, QUALITY_REJECTED, etc.) counts
        as terminal but NOT accepted
      - Feed findings do NOT satisfy nonfeed mission
    """

    __slots__ = ()

    @staticmethod
    def is_mission_profile(acquisition_profile: str | None) -> bool:
        """
        Return True when the profile is any nonfeed_diagnostic variant.

        GHOST_INVARIANTS:
          - No network I/O, no model/MLX load
        """
        if acquisition_profile is None:
            return False
        return acquisition_profile.startswith("nonfeed_diagnostic")

    @staticmethod
    def get_required_families() -> tuple[str, ...]:
        """Required lane families for nonfeed_diagnostic mission."""
        return ("PUBLIC", "CT", "PIVOT_EXECUTOR")

    @staticmethod
    def get_optional_families() -> tuple[str, ...]:
        """Optional lane families for nonfeed_diagnostic mission."""
        return ("WAYBACK", "PASSIVE_DNS")

    @staticmethod
    def _family_to_lane(family: str) -> str:
        """Map lane family string to AcquisitionLane constant."""
        return _NONFEED_LANE_FAMILY_MAP.get(family, family)

    @staticmethod
    def _get_lane_outcome(
        family: str,
        acquisition_lane_outcomes: tuple,
        public_outcome: dict | None,
        ct_quarantine_count: int,
        quality_rejection_ledger: tuple,
    ) -> dict | None:
        """
        Get the outcome dict for a lane family.

        Returns a dict with keys: accepted_findings, terminal_state, error, skipped
        suitable for mission evaluation.

        GHOST_INVARIANTS:
          - No network I/O, no model/MLX load
          - Fail-safe: returns None for unknown families
        """
        # Inline imports to avoid circular deps at module level
        from hledac.universal.runtime.acquisition.plan_builder import (
            normalize_terminal_state,
        )
        from hledac.universal.runtime.acquisition.lane_constants import (
            AcquisitionLane,
        )

        if family == "PUBLIC":
            if public_outcome is None:
                return None
            accepted = public_outcome.get("accepted_count", 0) or 0
            terminal_state = normalize_terminal_state(public_outcome)
            return {
                "accepted_findings": accepted,
                "terminal_state": terminal_state,
                "error": public_outcome.get("error"),
                "skipped": public_outcome.get("skipped", False),
            }
        elif family == "CT":
            lane = AcquisitionLane.CT
            for outcome in acquisition_lane_outcomes:
                if hasattr(outcome, "lane") and outcome.lane == lane:
                    return {
                        "accepted_findings": outcome.accepted_findings,
                        "terminal_state": normalize_terminal_state(outcome.to_dict()),
                        "error": outcome.error,
                        "skipped": False,
                    }
            return None
        elif family == "PIVOT_EXECUTOR":
            lane = AcquisitionLane.PIVOT_EXECUTOR
            for outcome in acquisition_lane_outcomes:
                if hasattr(outcome, "lane") and outcome.lane == lane:
                    return {
                        "accepted_findings": outcome.accepted_findings,
                        "terminal_state": normalize_terminal_state(outcome.to_dict()),
                        "error": outcome.error,
                        "skipped": False,
                    }
            return None
        elif family == "WAYBACK":
            lane = AcquisitionLane.WAYBACK
            for outcome in acquisition_lane_outcomes:
                if hasattr(outcome, "lane") and outcome.lane == lane:
                    return {
                        "accepted_findings": outcome.accepted_findings,
                        "terminal_state": normalize_terminal_state(outcome.to_dict()),
                        "error": outcome.error,
                        "skipped": False,
                    }
            return None
        elif family == "PASSIVE_DNS":
            lane = AcquisitionLane.PASSIVE_DNS
            for outcome in acquisition_lane_outcomes:
                if hasattr(outcome, "lane") and outcome.lane == lane:
                    return {
                        "accepted_findings": outcome.accepted_findings,
                        "terminal_state": normalize_terminal_state(outcome.to_dict()),
                        "error": outcome.error,
                        "skipped": False,
                    }
            return None
        return None

    @staticmethod
    def _evaluate_family_status(outcome: dict | None, memory_skipped: bool = False) -> str:
        """
        Evaluate the status of a lane family for mission completion.

        Returns: "complete" | "terminal_no_evidence" | "skipped" | "unresolved"

        GHOST_INVARIANTS:
          - No network I/O, no model/MLX load
        """
        if outcome is None:
            return "unresolved"
        if memory_skipped:
            return "skipped"
        accepted = outcome.get("accepted_findings", 0) or 0
        terminal_state = outcome.get("terminal_state", "")
        error = outcome.get("error")
        skipped = outcome.get("skipped", False)

        if accepted > 0:
            return "complete"
        if skipped or error:
            return "terminal_no_evidence"
        # Check for explicit terminal states that don't have evidence
        if terminal_state in (
            "FETCH_ZERO_SUCCESS",
            "QUALITY_REJECTED",
            "PROVIDER_ERROR",
            "DISCOVERY_ERROR",
            "TIMEOUT",
        ):
            return "terminal_no_evidence"
        return "unresolved"

    @classmethod
    def build_snapshot(
        cls,
        acquisition_profile: str,
        acquisition_lane_outcomes: tuple,
        public_outcome: dict | None,
        ct_quarantine_count: int,
        quality_rejection_ledger: tuple,
        memory_skipped_families: tuple[str, ...] = (),
    ) -> NonfeedMissionSnapshot:
        """
        Build a NonfeedMissionSnapshot from current lane outcomes.

        GHOST_INVARIANTS:
          - No network I/O, no model/MLX load
          - Fail-safe: returns snapshot with "unknown" intent on error
        """
        # Inline import to avoid circular deps
        # F351: NonfeedMissionSnapshot is defined locally in this module (line 351),
        # not in nonfeed_outcomes.py. The import below is a NO-OP at runtime but
        # would raise ImportError if the fallback exception handler were ever bypassed.
        # Fixed: import the local class directly.
        from runtime.acquisition.mission import NonfeedMissionSnapshot

        try:
            required = cls.get_required_families()
            optional = cls.get_optional_families()

            required_results: dict[str, dict] = {}
            for family in required:
                outcome = cls._get_lane_outcome(
                    family,
                    acquisition_lane_outcomes,
                    public_outcome,
                    ct_quarantine_count,
                    quality_rejection_ledger,
                )
                memory_skipped = family in memory_skipped_families
                status = cls._evaluate_family_status(outcome, memory_skipped)
                required_results[family] = {
                    "status": status,
                    "outcome": outcome,
                    "memory_skipped": memory_skipped,
                }

            optional_results: dict[str, dict] = {}
            for family in optional:
                outcome = cls._get_lane_outcome(
                    family,
                    acquisition_lane_outcomes,
                    public_outcome,
                    ct_quarantine_count,
                    quality_rejection_ledger,
                )
                memory_skipped = family in memory_skipped_families
                status = cls._evaluate_family_status(outcome, memory_skipped)
                optional_results[family] = {
                    "status": status,
                    "outcome": outcome,
                    "memory_skipped": memory_skipped,
                }

            # Determine overall mission completeness
            all_required_complete = all(
                r["status"] in ("complete", "terminal_no_evidence", "skipped")
                for r in required_results.values()
            )

            return NonfeedMissionSnapshot(
                acquisition_profile=acquisition_profile,
                mission_intent="unknown",  # Will be set by caller
                required_families=required,
                optional_families=optional,
                required_results=required_results,
                optional_results=optional_results,
                all_required_complete=all_required_complete,
            )
        except Exception:
            return NonfeedMissionSnapshot(
                acquisition_profile=acquisition_profile,
                mission_intent="unknown",
                required_families=cls.get_required_families(),
                optional_families=cls.get_optional_families(),
                required_results={},
                optional_results={},
                all_required_complete=False,
            )

    @classmethod
    def _derive_exit_reason(
        cls,
        snapshot: NonfeedMissionSnapshot,
        memory_skipped_families: tuple[str, ...],
    ) -> str:
        """
        Derive human-readable exit reason from mission snapshot.

        GHOST_INVARIANTS:
          - No network I/O, no model/MLX load
        """
        if snapshot.all_required_complete:
            return "MISSION_COMPLETE"
        required = snapshot.required_families
        incomplete = [
            f
            for f in required
            if snapshot.required_results.get(f, {}).get("status") == "unresolved"
        ]
        if incomplete:
            return f"INCOMPLETE:{','.join(incomplete)}"
        terminal_no_evidence = [
            f
            for f in required
            if snapshot.required_results.get(f, {}).get("status") == "terminal_no_evidence"
        ]
        if terminal_no_evidence:
            return f"TERMINAL_NO_EVIDENCE:{','.join(terminal_no_evidence)}"
        return "UNKNOWN_EXIT"


# ── Mission snapshot (forward-declared here to avoid circular import) ─────────────────


class NonfeedMissionSnapshot:
    """
    F217B: Snapshot of nonfeed mission state for telemetry.

    Kept here (vs nonfeed_outcomes.py) because it's constructed by
    NonfeedMissionController.build_snapshot().

    GHOST_INVARIANTS:
      - No network I/O, no model/MLX load
    """

    __slots__ = (
        "acquisition_profile",
        "mission_intent",
        "required_families",
        "optional_families",
        "required_results",
        "optional_results",
        "all_required_complete",
    )

    def __init__(
        self,
        acquisition_profile: str,
        mission_intent: str,
        required_families: tuple[str, ...],
        optional_families: tuple[str, ...],
        required_results: dict,
        optional_results: dict,
        all_required_complete: bool,
    ):
        self.acquisition_profile = acquisition_profile
        self.mission_intent = mission_intent
        self.required_families = required_families
        self.optional_families = optional_families
        self.required_results = required_results
        self.optional_results = optional_results
        self.all_required_complete = all_required_complete

    def to_dict(self) -> dict:
        return {
            "acquisition_profile": self.acquisition_profile,
            "mission_intent": self.mission_intent,
            "required_families": self.required_families,
            "optional_families": self.optional_families,
            "required_results": self.required_results,
            "optional_results": self.optional_results,
            "all_required_complete": self.all_required_complete,
        }


# ── Mission intent inference ───────────────────────────────────────────────────────


_INFER_RE: __import__("re").compile(
    r"\b("
    r"domain|domian|domaain|domian"  # typo variants
    r"|person|human|individual|name"
    r"|ip|ipv4|ipv6|host|subnet"
    r"|cve|cve-\d{4}-\d+|vulnerability"
    r"|wallet|bitcoin|eth|ethereum|crypto"
    r"|url|website|web"
    r")\b",
    __import__("re").IGNORECASE,
)


def infer_mission_intent(query: str) -> str:
    """
    F217B: Infer mission intent from query string.

    Returns one of: domain_recon | person_recon | infra_recon | cve_recon | unknown

    GHOST_INVARIANTS:
      - No network I/O, no model/MLX load
      - Bounded: O(len(query)) regex match
      - Fail-safe: returns "unknown" on any error
    """
    if not query:
        return "unknown"
    try:
        match = _INFER_RE.search(query)
        if not match:
            return "unknown"
        indicator = match.group(1).lower()
        if indicator in ("domain", "domian", "domaain", "domian"):
            return "domain_recon"
        elif indicator in ("person", "human", "individual", "name"):
            return "person_recon"
        elif indicator in ("ip", "ipv4", "ipv6", "host", "subnet"):
            return "infra_recon"
        elif indicator in ("cve", "cve-", "vulnerability"):
            return "cve_recon"
        elif indicator in ("wallet", "bitcoin", "eth", "ethereum", "crypto"):
            return "unknown"  # Not yet mapped
        elif indicator in ("url", "website", "web"):
            return "domain_recon"
        return "unknown"
    except Exception:
        return "unknown"


def _mission_target_kind(intent: str) -> str:
    """Map mission intent to target kind."""
    mapping = {
        "domain_recon": "domain",
        "person_recon": "person",
        "infra_recon": "ip",
        "cve_recon": "unknown",
    }
    return mapping.get(intent, "unknown")


def _mission_lanes(
    intent: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """
    Return (required_families, optional_families) for a mission intent.

    GHOST_INVARIANTS:
      - No network I/O, no model/MLX load
    """
    if intent == "domain_recon":
        return (("PUBLIC", "CT"), ("WAYBACK", "PASSIVE_DNS"))
    elif intent == "person_recon":
        return (("PUBLIC",), ("WAYBACK", "PASSIVE_DNS"))
    elif intent == "infra_recon":
        return (("CT", "PIVOT_EXECUTOR"), ("PASSIVE_DNS",))
    elif intent == "cve_recon":
        return (("PUBLIC",), ())
    return (("PUBLIC", "CT", "PIVOT_EXECUTOR"), ("WAYBACK", "PASSIVE_DNS"))


# ── NonfeedMissionExitReason ─────────────────────────────────────────────────────


class NonfeedMissionExitReason(Enum):
    """F217B: Canonical exit reasons for nonfeed mission."""

    MISSION_COMPLETE = "MISSION_COMPLETE"
    INCOMPLETE = "INCOMPLETE"
    TERMINAL_NO_EVIDENCE = "TERMINAL_NO_EVIDENCE"
    UNKNOWN_EXIT = "UNKNOWN_EXIT"
