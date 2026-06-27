"""
F350M-FED: Federated Research Coordinator (virtual-node model).

Sprint: F350M-FED / Federated Activation 2026-06-04
Target: federated/coordinator.py

PURPOSE
=======
This module activates the `federated/` capability as a bounded, single-host,
multi-virtual-node research coordinator. It is NOT real P2P — true mesh
networking would require a transport layer (Tor/I2P/HTTPS rendezvous) that is
out of scope for M1 8GB UMA. Instead, we model the federated pattern locally:

- N independent "virtual nodes" share the SAME host (M1) and SAME query.
- Each virtual node maintains its OWN in-memory Q-table (RL policy slice)
  and explores a DIFFERENT strategy lane.
- The coordinator runs nodes in parallel via asyncio.gather(return_exceptions=True)
  (GHOST_INVARIANT), collects their findings, deduplicates them by
  (ioc_type, ioc_value) tuple, and returns a unified FederatedResult.

This is the canonical seam for "federated research" semantics on a
single host. The same interface can be swapped to a real P2P transport
later by replacing the local `_LocalNodeTransport` with a remote transport,
WITHOUT touching the caller surface.

DESIGN BOUNDS (HARD INVARIANTS — M1 8GB)
=======================================
- MAX_VIRTUAL_NODES = 3  (bounded: M1 8GB cannot host >3 RL slices)
- PER_NODE_MAX_FINDINGS = 100  (bounded per-node yield)
- AGGREGATION_MAX_FINDINGS = 500  (bounded merged output)
- DEDUP_KEY: (ioc_type, ioc_value) tuple, str-normalized
- NO_MLX, NO_BROWSER, NO_STEALTH  (read-only data plane, no heavy engines)
- NO_LMDB_PERSISTENCE  (in-memory only; LMDB QTable persistence would
  require 3 separate paths + memory_manager — out of scope for activation)

FAIL-SOFT (per GHOST_INVARIANT #10)
===================================
Every public method is wrapped in try/except Exception. The coordinator
returns a safe empty FederatedResult on any error — it never raises into
the sprint lifecycle. The caller is expected to log + continue.

INTEGRATION
===========
1. CapabilityRegistry registers FEDERATED when HLEDAC_ENABLE_FEDERATED=1
2. sprint_scheduler (or any sidecar orchestrator) imports
   `FederatedResearchCoordinator` lazily and invokes
   `await coordinator.distribute_research(query)` to obtain a merged
   finding set for the current sprint.
3. The caller is responsible for downstream ingest (e.g. async_ingest_findings_batch).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

from hledac.universal.utils.async_helpers import safe_gather_dropin

# F350M-FED: In-memory QTable — local, not the same as loops.QTable.
# We deliberately do NOT import loops.ResearchLoop because its __init__
# requires hypothesis_engine + graph, both of which are heavy and would
# create 3× memory pressure. The federated QTable is a lightweight RL
# slice that tracks per-node action reward for the federated lanes.
from .qtable import FederatedQTable

logger = logging.getLogger(__name__)

__all__ = [
    "FederatedResearchCoordinator",
    "FederatedResult",
    "NodeResult",
    "NodeLane",
    "MAX_VIRTUAL_NODES",
]


# --- BOUNDS (module-level, immutable) ---------------------------------------

MAX_VIRTUAL_NODES: int = 3
"""Hard cap on simultaneous virtual nodes per coordinator instance."""

PER_NODE_MAX_FINDINGS: int = 100
"""Hard cap on findings a single node may produce in one distribute cycle."""

AGGREGATION_MAX_FINDINGS: int = 500
"""Hard cap on the merged/deduplicated output of a single distribute cycle."""

PER_NODE_TIMEOUT_S: float = 10.0
"""Hard timeout for a single node's run_lane() coroutine. Fail-soft enforced."""

DISTRIBUTE_TOTAL_TIMEOUT_S: float = 30.0
"""Hard timeout for the entire distribute_research() call. Fail-soft enforced."""


# --- TYPES ------------------------------------------------------------------


class NodeLane:
    """
    Lane identifier for a virtual node's research strategy.

    Each lane corresponds to a different angle of investigation on the
    same query. The choice is deliberately minimal (3 lanes) — adding
    more lanes requires a corresponding QTable that the M1 can afford.
    """

    SURFACE = "surface"
    DARK = "dark"
    ARCHIVE = "archive"

    ALL: tuple[str, ...] = (SURFACE, DARK, ARCHIVE)
    """The default 3-lane partitioning (matches MAX_VIRTUAL_NODES)."""


@dataclass
class NodeResult:
    """Result of a single virtual node's research cycle."""

    lane: str
    findings: list[dict[str, Any]] = field(default_factory=list)
    reward: float = 0.0
    error: str | None = None
    duration_s: float = 0.0

    def is_ok(self) -> bool:
        return self.error is None


@dataclass
class FederatedResult:
    """
    Aggregated output of distribute_research().

    This is the canonical return type for the federated capability. It
    contains the merged (deduplicated) findings plus per-node diagnostics
    so the caller can reason about which lanes contributed what.
    """

    query: str
    merged_findings: list[dict[str, Any]] = field(default_factory=list)
    node_results: list[NodeResult] = field(default_factory=list)
    dedup_count: int = 0
    total_nodes: int = 0
    failed_nodes: int = 0
    duration_s: float = 0.0

    def summary(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "total_nodes": self.total_nodes,
            "failed_nodes": self.failed_nodes,
            "merged_finding_count": len(self.merged_findings),
            "dedup_count": self.dedup_count,
            "duration_s": round(self.duration_s, 3),
            "lanes": [n.lane for n in self.node_results],
        }


# --- NODE TRANSPORT (LOCAL VIRTUAL) ----------------------------------------


class _LocalNodeTransport:
    """
    The "transport" for a virtual node on the local M1 host.

    In a real federated deployment, this would be a remote RPC, Tor/I2P
    socket, or libp2p stream. Here, it is a local async callable that
    yields synthesized findings for the lane+query.

    The contract is intentionally minimal:
        async def run(self, lane: str, query: str) -> list[dict]
        -> returns up to PER_NODE_MAX_FINDINGS findings

    This is the swap point for a real remote transport.
    """

    async def run(self, lane: str, query: str) -> list[dict[str, Any]]:
        """
        Default lane runner. Produces up to PER_NODE_MAX_FINDINGS
        bounded synthetic findings for the (lane, query) pair.

        This is the FALLBACK runner — in production the orchestrator
        would inject a richer transport (e.g. one that calls
        ResearchLoop.run_once or GhostExecutor.execute).
        """
        # The lane/query parameters are part of the transport contract;
        # the default implementation just signals which lane executed.
        # A real transport would dispatch on (lane, query) to a remote node.
        logger.debug(f"[FED-TRANSport] local node run: lane={lane} query_len={len(query or '')}")
        return []


# --- COORDINATOR ------------------------------------------------------------


class FederatedResearchCoordinator:
    """
    Coordinates N virtual research nodes for a single query and aggregates
    their findings.

    Lifecycle:
        coord = FederatedResearchCoordinator()
        result = await coord.distribute_research(query)
        # result.merged_findings -> list[dict], deduplicated

    Thread-safety: each instance is single-use per distribute cycle.
    Multiple concurrent distribute_research() calls on the SAME instance
    are NOT supported (use separate instances).
    """

    def __init__(
        self,
        max_nodes: int = MAX_VIRTUAL_NODES,
        transport: _LocalNodeTransport | None = None,
        bridge: Any | None = None,
        use_bridge: bool = False,
        transport_name: str | None = None,
    ) -> None:
        # Clamp max_nodes to the global hard cap (defense in depth)
        self._max_nodes: int = max(1, min(int(max_nodes), MAX_VIRTUAL_NODES))
        # Resolve the transport. Resolution order (F350M-FED-P):
        #   1. `transport` arg (direct injection, highest priority —
        #      preserves the original back-compat signature).
        #   2. `transport_name` arg → NodeTransportFactory.create(name).
        #   3. Default fallback: _LocalNodeTransport() (legacy stub,
        #      returns [] — back-compat with the pre-sprint behavior).
        #
        # The factory import is LAZY (inside the except branch) so the
        # coordinator still imports cleanly when the federated/
        # transport subpackage is unavailable (e.g. partial install).
        if transport is not None:
            self._transport: Any = transport
        elif transport_name is not None:
            try:
                from .transports import NodeTransportFactory
                self._transport: Any = NodeTransportFactory.create(transport_name)
            except Exception as e:
                logger.debug(
                    "[FED] transport_name=%r factory failed (%s: %s) — "
                    "falling back to _LocalNodeTransport",
                    transport_name, type(e).__name__, e,
                )
                self._transport: Any = _LocalNodeTransport()
        else:
            # Default: keep the original stub behavior (backward compat).
            # Production callers (e.g. FederatedSidecarAdapter) explicitly
            # inject LaneDispatchTransport via `transport_name="lane_dispatch"`.
            self._transport: Any = _LocalNodeTransport()
        # Per-lane QTable slice (federated learning aspect)
        # When `use_bridge=True` and a `bridge` is provided, all lanes
        # share a single FederatedBridge (lane-prefixed) — this enables
        # cross-sprint LMDB persistence + optional ResearchLoop hybrid
        # access. When `use_bridge=False` (the default), we keep the
        # original per-lane FederatedQTable design for full backward
        # compatibility and zero overhead.
        if use_bridge and bridge is not None:
            self._bridge: Any | None = bridge
            self._qtables: dict[str, FederatedQTable] = {}
        else:
            self._bridge = None
            self._qtables: dict[str, FederatedQTable] = {
                lane: FederatedQTable() for lane in NodeLane.ALL[: self._max_nodes]
            }

    @property
    def max_nodes(self) -> int:
        return self._max_nodes

    async def distribute_research(
        self,
        query: str,
        lanes: list[str] | None = None,
    ) -> FederatedResult:
        """
        Distribute the query across up to max_nodes virtual nodes and
        aggregate their findings.

        Args:
            query: Research query to distribute.
            lanes: Optional list of NodeLane.* values. If None, uses the
                   first max_nodes lanes from NodeLane.ALL.

        Returns:
            FederatedResult with merged findings, per-node diagnostics,
            dedup count, and timing. NEVER raises — returns a safe
            empty result on any internal error.
        """
        started = time.monotonic()
        result = FederatedResult(query=query, total_nodes=0)

        try:
            # Resolve lane partition
            chosen_lanes = self._resolve_lanes(lanes)
            result.total_nodes = len(chosen_lanes)

            if not chosen_lanes:
                logger.warning("[FED] No lanes selected, returning empty result")
                return result

            # Launch nodes in parallel with gather(return_exceptions=True)
            # GHOST_INVARIANT: every gather must use return_exceptions
            node_coros: list[asyncio.Task[NodeResult]] = []
            for lane in chosen_lanes:
                node_coros.append(
                    asyncio.create_task(self._run_node(lane, query))
                )
            try:
                async with asyncio.timeout(DISTRIBUTE_TOTAL_TIMEOUT_S):
                    gathered = await safe_gather_dropin(*node_coros, label="coordinator:311")
            except TimeoutError:
                logger.error(
                    f"[FED] distribute_research total timeout "
                    f"{DISTRIBUTE_TOTAL_TIMEOUT_S}s exceeded"
                )
                result.duration_s = time.monotonic() - started
                return result

            # Collect per-node results, fail-soft on exceptions
            for lane, outcome in zip(chosen_lanes, gathered, strict=False):
                if isinstance(outcome, BaseException):
                    result.failed_nodes += 1
                    result.node_results.append(
                        NodeResult(
                            lane=lane,
                            error=f"{type(outcome).__name__}: {outcome}",
                        )
                    )
                    logger.warning(
                        f"[FED] node lane={lane} raised: "
                        f"{type(outcome).__name__}: {outcome}"
                    )
                    continue
                if isinstance(outcome, NodeResult):
                    if not outcome.is_ok():
                        result.failed_nodes += 1
                    result.node_results.append(outcome)

            # Aggregate + dedup
            merged, dedup_count = self._aggregate_and_dedup(
                [n for n in result.node_results if n.is_ok()]
            )
            result.merged_findings = merged[:AGGREGATION_MAX_FINDINGS]
            result.dedup_count = dedup_count

        except Exception as e:  # GHOST_INVARIANT: fail-soft everywhere
            logger.error(
                f"[FED] distribute_research unexpected error: "
                f"{type(e).__name__}: {e}"
            )
            result.failed_nodes = max(result.failed_nodes, 1)

        # Optional bridge-side persist (debounced, fail-soft).
        # Only fires when the coordinator was built with `use_bridge=True`
        # AND the bridge has a real LMDB path AND the debounce window
        # has elapsed. NEVER raises.
        if self._bridge is not None:
            try:
                persisted = await self._bridge.persist_if_due()
                if persisted:
                    logger.debug(
                        f"[FED] bridge persisted: "
                        f"updates={self._bridge.update_count} "
                        f"persists={self._bridge.persist_count}"
                    )
            except Exception as pe:
                logger.debug(f"[FED] bridge persist skipped: {pe}")

        result.duration_s = time.monotonic() - started
        logger.info(
            f"[FED] distribute_research done: nodes={result.total_nodes} "
            f"failed={result.failed_nodes} merged={len(result.merged_findings)} "
            f"dedup={result.dedup_count} dur={result.duration_s:.3f}s"
        )
        return result

    async def _run_node(self, lane: str, query: str) -> NodeResult:
        """
        Run a single virtual node. Always returns a NodeResult — never
        raises. The QTable is updated with the observed reward (even if 0).
        """
        started = time.monotonic()
        node = NodeResult(lane=lane)
        # Best-effort: if the transport supports set_sprint_id, give it
        # the current sprint id for finding traceability. This is a
        # no-op for the legacy _LocalNodeTransport.
        try:
            set_id = getattr(self._transport, "set_sprint_id", None)
            if callable(set_id):
                set_id(getattr(self, "_sprint_id_for_transport", "") or "")
        except Exception as sid_e:  # GHOST_INVARIANT: never raise
            logger.debug("[FED] set_sprint_id skipped: %s", sid_e)
        try:
            # Per-node timeout (GHOST_INVARIANT: bounded execution)
            async with asyncio.timeout(PER_NODE_TIMEOUT_S):
                raw_findings = await self._transport.run(lane, query)
            # Bound the per-node yield
            if not isinstance(raw_findings, list):
                raw_findings = []
            node.findings = raw_findings[:PER_NODE_MAX_FINDINGS]

            # Compute reward (1.0 = found something, scaled by yield)
            node.reward = min(1.0, len(node.findings) / 10.0)

            # Q-table update (RL slice: per-lane)
            # Bridge path: route through FederatedBridge (lane-prefixed,
            # cross-sprint LMDB persistence, optional hybrid).
            # Per-lane QTable path: original lightweight design.
            state = (lane, len(node.findings))
            try:
                if self._bridge is not None:
                    # Bridge handles lane-prefixing internally
                    self._bridge.update(
                        lane=lane,
                        state=state,
                        action=lane,
                        reward=node.reward,
                        next_state=state,
                    )
                else:
                    qtable = self._qtables.get(lane)
                    if qtable is not None:
                        qtable.update(
                            state=state,
                            action=lane,
                            reward=node.reward,
                            next_state=state,
                        )
            except Exception as qe:  # Q-table update is best-effort
                logger.debug(
                    f"[FED] qtable update lane={lane} skipped: {qe}"
                )

        except TimeoutError:
            node.error = f"timeout after {PER_NODE_TIMEOUT_S}s"
            logger.warning(f"[FED] node lane={lane} timed out")
        except Exception as e:
            node.error = f"{type(e).__name__}: {e}"
            logger.warning(
                f"[FED] node lane={lane} failed: {node.error}"
            )
        finally:
            node.duration_s = time.monotonic() - started
        return node

    def _resolve_lanes(self, requested: list[str] | None) -> list[str]:
        """
        Validate and bound the requested lane list. Drops unknown lanes
        and clamps to max_nodes.
        """
        if not requested:
            return list(NodeLane.ALL[: self._max_nodes])
        valid = [l for l in requested if l in NodeLane.ALL]  # noqa: E741
        # Deduplicate while preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for l in valid:  # noqa: E741
            if l not in seen:
                seen.add(l)
                unique.append(l)
        return unique[: self._max_nodes]

    def _aggregate_and_dedup(
        self,
        node_results: list[NodeResult],
    ) -> tuple[list[dict[str, Any]], int]:
        """
        Merge findings across nodes, deduplicate by (ioc_type, ioc_value).
        When duplicates collide, keep the entry with the highest
        'confidence' field (default 0.0).
        """
        merged: dict[tuple[str, str], dict[str, Any]] = {}
        raw_count = 0
        for node in node_results:
            for finding in node.findings:
                raw_count += 1
                if not isinstance(finding, dict):
                    continue
                key = self._dedup_key(finding)
                if key is None:
                    # No dedup key — keep the finding with a synthetic key
                    # so it does not collide with any other
                    key = (
                        "_unkeyed_",
                        f"{id(finding)}-{raw_count}",
                    )
                existing = merged.get(key)
                if existing is None:
                    # First sighting — annotate with source lane
                    enriched = dict(finding)
                    enriched.setdefault("source_lane", node.lane)
                    merged[key] = enriched
                else:
                    # Duplicate — keep higher confidence
                    new_conf = float(finding.get("confidence", 0.0) or 0.0)
                    old_conf = float(existing.get("confidence", 0.0) or 0.0)
                    if new_conf > old_conf:
                        enriched = dict(finding)
                        enriched.setdefault("source_lane", node.lane)
                        merged[key] = enriched
        dedup_count = max(0, raw_count - len(merged))
        return list(merged.values()), dedup_count

    @staticmethod
    def _dedup_key(finding: dict[str, Any]) -> tuple[str, str] | None:
        """
        Extract a (ioc_type, ioc_value) tuple from a finding.

        Tolerates several common shapes:
            {"ioc_type": "domain", "ioc_value": "example.com"}
            {"type": "domain", "value": "example.com"}
            {"indicator_type": ..., "indicator": ...}
        Returns None if no recognizable key can be extracted — in that
        case the caller will synthesize a unique key for this finding.
        """
        ioc_type = (
            finding.get("ioc_type")
            or finding.get("type")
            or finding.get("indicator_type")
        )
        ioc_value = (
            finding.get("ioc_value")
            or finding.get("value")
            or finding.get("indicator")
        )
        if not ioc_type or not ioc_value:
            return None
        return (str(ioc_type).strip().lower(), str(ioc_value).strip().lower())


# --- MODULE-LEVEL GATE CHECK ------------------------------------------------


def is_federated_enabled() -> bool:
    """
    Module-level env-var gate check, used by capability registration.

    Centralizes the HLEDAC_ENABLE_FEDERATED semantics so that the
    capabilities.py registration and any direct callers agree on the
    exact token set ("1", "true", "yes", "on", case-insensitive).
    """
    raw = os.environ.get("HLEDAC_ENABLE_FEDERATED", "").strip().lower()
    return raw in ("1", "true", "yes", "on")
