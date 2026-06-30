"""
F350M-FED: Federated Sidecar Adapter — wires FederatedResearchCoordinator
into the existing SidecarOrchestrator / SidecarRegistry pattern.

Sprint: F350M-FED / Federated Activation 2026-06-04
Target: federated/sidecar_adapter.py

PURPOSE
=======
This module adapts the federated multi-virtual-node coordinator
(FederatedResearchCoordinator) to the canonical SidecarAdapterProtocol so
that it slots into the existing advisory pipeline of SprintScheduler.

LIFECYCLE
=========
1. SprintScheduler.run_advisory_runner() calls SidecarOrchestrator
2. SidecarOrchestrator iterates over SidecarRegistry.get_available()
3. For each sidecar whose env_gate is set AND RAM budget fits, it
   instantiates and calls sidecar.run(ctx)
4. BaseSidecarAdapter.run() is a fail-soft wrapper around run_async()
5. FederatedSidecarAdapter.run_async() builds a coordinator and
   converts the merged findings into CanonicalFinding objects

M1 8GB SAFETY
=============
- env-gated: HLEDAC_ENABLE_FEDERATED=1
- ram_budget_mb: 30  (matches: 3 nodes × ~5MB + Q-tables + tasks)
- priority: 5 (medium, runs after stealth but before forensics)
- Adaptive node count: 1-2 nodes depending on memory_pressure
- M1 hard skip if memory_pressure > 0.85 (delegated to governor)
- All exceptions caught (BaseSidecarAdapter.run() is the wrapper)

CANONICAL OUTPUT
================
Returns list[CanonicalFinding] with:
    source_type = "federated_research"
    provenance  = ("federated_research", f"lane={lane}")
    confidence  = from finding.confidence (default 0.5)
    payload_text = finding payload (if any)
"""


import logging
import os
import time
import uuid
from typing import Any

from .coordinator import (
    AGGREGATION_MAX_FINDINGS,
    FederatedResearchCoordinator,
    NodeLane,
    is_federated_enabled,
)

logger = logging.getLogger(__name__)

__all__ = ["FederatedSidecarAdapter"]


# --- M1 BOUNDS (different from coordinator.py — tighter for sidecar) --------

SIDECAR_MAX_NODES: int = 2
"""Max virtual nodes in sidecar mode (1 default, 2 max)."""

SIDECAR_MEMORY_SKIP_THRESHOLD: float = 0.85
"""Skip the sidecar entirely if memory_pressure is above this ratio."""

SIDECAR_MEMORY_REDUCED_THRESHOLD: float = 0.70
"""Reduce to 1 node if memory_pressure is above this ratio."""

SIDECAR_TIMEOUT_S: float = 12.0
"""Hard timeout for the whole sidecar run. Tighter than coordinator default."""

# --- SOURCE TYPE -------------------------------------------------------------

SOURCE_TYPE: str = "federated_research"
"""source_type field on produced CanonicalFinding objects."""


# --- ADAPTER -----------------------------------------------------------------


class FederatedSidecarAdapter:
    """
    Sidecar adapter that wires FederatedResearchCoordinator into the
    sprint advisory pipeline.

    Implements the duck-typed subset of SidecarAdapterProtocol needed by
    BaseSidecarAdapter (and explicitly satisfies the Protocol at runtime
    via @runtime_checkable in sidecar_protocol.py).

    Class-level attributes (read by SidecarRegistry.get_available):
        sidecar_id:    "federated_research"
        env_gate:      "HLEDAC_ENABLE_FEDERATED"
        ram_budget_mb: 30
        priority:      5

    The base run() method is the fail-soft wrapper. The actual work
    happens in run_async().
    """

    # --- Class-level sidecar protocol attributes ---
    sidecar_id: str = "federated_research"
    env_gate: str = "HLEDAC_ENABLE_FEDERATED"
    ram_budget_mb: int = 30
    priority: int = 5

    def is_available(self) -> bool:
        """
        Check both env-gate and module availability.

        Mirrors BaseSidecarAdapter.is_available() but also gates on
        the module-level is_federated_enabled() check (which checks
        the exact env-var token set) so the registration is consistent
        with capabilities.py.
        """
        if not is_federated_enabled():
            return False
        return os.getenv(self.env_gate, "").strip().lower() in ("1", "true", "yes", "on")

    async def run(self, ctx: Any) -> list[Any]:
        """
        Fail-soft wrapper. Subclasses/base would call self.run_async(ctx).

        We implement this directly (rather than via BaseSidecarAdapter
        inheritance) to keep the federated layer zero-dependency on
        `runtime.sidecar_protocol` (which is a sibling runtime module,
        not a federated concern). The behavior is identical.
        """
        try:
            return await self.run_async(ctx)
        except Exception as e:
            logger.warning(
                "[FED-SIDECAR] run: fail-soft exception: %s: %s",
                type(e).__name__, e,
            )
            return []

    @staticmethod
    def _select_transport_name() -> str:
        """
        Pick the transport name for this sidecar run.

        Selection ladder (F350M-FED-P):
          1. HLEDAC_ENABLE_FEDERATED_P2P=1 → "peer_node" (Tier 2 P2P)
             ONLY if the optional deps (zeroconf, cryptography) are
             importable. We probe lazily here so a missing dep is a
             soft fallback, not a hard crash.
          2. Otherwise → "lane_dispatch" (Tier 1: real per-lane backends).
             This is the new default — it actually produces findings.
          3. If both fail (e.g. during a partial install), the
             coordinator's factory will fall back to the legacy stub.

        Returns:
            The transport name to pass to NodeTransportFactory.create().
        """
        # Tier 2 — P2P over UDP + Noise XX + mDNS.
        # Gate check is duplicated from peer_node.is_peer_node_enabled()
        # to keep the import surface minimal in the sidecar path.
        p2p_env = os.environ.get("HLEDAC_ENABLE_FEDERATED_P2P", "").strip().lower()
        if p2p_env in ("1", "true", "yes", "on"):
            try:
                import zeroconf  # noqa: F401
                from cryptography.hazmat.primitives.asymmetric.x25519 import (  # noqa: F401
                    X25519PrivateKey,
                )
                return "peer_node"
            except Exception as e:
                logger.info(
                    "[FED-SIDECAR] HLEDAC_ENABLE_FEDERATED_P2P=1 but "
                    "P2P deps missing (%s: %s) — falling back to lane_dispatch",
                    type(e).__name__, e,
                )
        return "lane_dispatch"

    async def run_async(self, ctx: Any) -> list[Any]:
        """
        Execute federated research on the current sprint context.

        Args:
            ctx: SidecarContext with .query, .sprint_id, .findings,
                 .sprint_mode, .memory_pressure

        Returns:
            list[CanonicalFinding] (may be empty). Never raises.

        Behavior:
            1. M1 safety check: skip if memory_pressure > 0.85
            2. Adaptive node count based on memory_pressure:
               - ≤ 0.70: 2 nodes
               - > 0.70: 1 node
            3. Build FederatedResearchCoordinator (bounded timeout)
            4. Run distribute_research(ctx.query)
            5. Convert merged_findings to CanonicalFinding
            6. Hard cap output at AGGREGATION_MAX_FINDINGS
        """
        started = time.monotonic()
        query = getattr(ctx, "query", "") or ""
        sprint_id = getattr(ctx, "sprint_id", "unknown") or "unknown"
        memory_pressure = float(getattr(ctx, "memory_pressure", 0.0) or 0.0)
        sprint_mode = str(getattr(ctx, "sprint_mode", "active") or "active")

        # Step 1: M1 safety check
        if memory_pressure > SIDECAR_MEMORY_SKIP_THRESHOLD:
            logger.info(
                "[FED-SIDECAR] skipping (memory_pressure=%.2f > %.2f)",
                memory_pressure, SIDECAR_MEMORY_SKIP_THRESHOLD,
            )
            return []

        # Step 2: adaptive node count
        if memory_pressure > SIDECAR_MEMORY_REDUCED_THRESHOLD:
            max_nodes = 1
        else:
            max_nodes = SIDECAR_MAX_NODES  # 2

        # Lane selection: pick 2 lanes based on sprint mode
        # (surface always; second depends on mode)
        lanes = [NodeLane.SURFACE]
        if sprint_mode in ("aggressive", "deep", "extreme", "exhaustive"):
            lanes.append(NodeLane.DARK)
        else:
            lanes.append(NodeLane.ARCHIVE)
        # Clamp to max_nodes
        lanes = lanes[:max_nodes]

        # Step 3: build the coordinator with a tightened total timeout.
        # We import the module here to mutate the constant temporarily —
        # this is safe because the constant is read at call time.
        from . import coordinator as _coord_mod
        original_total = _coord_mod.DISTRIBUTE_TOTAL_TIMEOUT_S
        _coord_mod.DISTRIBUTE_TOTAL_TIMEOUT_S = SIDECAR_TIMEOUT_S
        # F350M-FED-P: Transport selection. The sidecar now picks the
        # transport by name. The selection ladder is:
        #   1. HLEDAC_ENABLE_FEDERATED_P2P=1 → "peer_node" (Tier 2 P2P).
        #      Falls back to "lane_dispatch" if the env-gate is on but
        #      PeerNodeTransport cannot import (no zeroconf/cryptography).
        #   2. Otherwise → "lane_dispatch" (Tier 1: real per-lane backends).
        #      This is the new default — it actually produces findings.
        #   3. Backward compat: if both factories fail, the coordinator
        #      falls back to the legacy _LocalNodeTransport (returns []).
        transport_name = self._select_transport_name()
        try:
            coord = FederatedResearchCoordinator(
                max_nodes=max_nodes,
                transport_name=transport_name,
            )
            # Best-effort: forward the sprint id to the transport for
            # finding traceability (LaneDispatchTransport and
            # PeerNodeTransport both implement set_sprint_id).
            try:
                if hasattr(coord._transport, "set_sprint_id"):
                    coord._transport.set_sprint_id(sprint_id)
            except Exception:  # GHOST_INVARIANT: never raise  # noqa: BLE001
                pass
            result = await coord.distribute_research(query, lanes=lanes)
        except Exception as e:  # last-resort fail-soft
            logger.warning(
                "[FED-SIDECAR] coordinator raised: %s: %s",
                type(e).__name__, e,
            )
            return []
        finally:
            _coord_mod.DISTRIBUTE_TOTAL_TIMEOUT_S = original_total
            # Best-effort transport close — releases any sockets / mDNS.
            try:
                closer = getattr(coord._transport, "close", None)
                if callable(closer):
                    await closer()
            except Exception:  # GHOST_INVARIANT: never raise  # noqa: BLE001
                pass

        # Step 4: convert merged_findings → CanonicalFinding
        findings = self._to_canonical_findings(
            result.merged_findings, sprint_id, query,
        )
        # Step 5: hard cap
        findings = findings[:AGGREGATION_MAX_FINDINGS]

        elapsed = time.monotonic() - started
        logger.info(
            "[FED-SIDECAR] done: query_len=%d nodes=%d failed=%d "
            "merged=%d canonical=%d dur=%.3fs",
            len(query), result.total_nodes, result.failed_nodes,
            len(result.merged_findings), len(findings), elapsed,
        )
        return findings

    @staticmethod
    def _to_canonical_findings(
        merged: list[dict[str, Any]],
        sprint_id: str,
        query: str,
    ) -> list[Any]:
        """
        Convert federated merged findings into CanonicalFinding objects.

        Tolerates two cases:
            1. knowledge.duckdb_store.CanonicalFinding is importable → use it
            2. Not importable (rare, e.g. sidecar run before main import)
               → return plain dicts with source_type=federated_research
               and let the downstream dispatch_fail path convert.

        The plain-dict fallback is intentional fail-soft: the sidecar
        never aborts the sprint even if CanonicalFinding is unavailable.
        """
        canonical_cls: type | None = None
        try:
            from knowledge.duckdb_store import CanonicalFinding
            canonical_cls = CanonicalFinding
        except Exception:
            try:
                from hledac.universal.knowledge.duckdb_store import CanonicalFinding
                canonical_cls = CanonicalFinding
            except Exception:
                canonical_cls = None

        out: list[Any] = []
        for finding in merged:
            if not isinstance(finding, dict):
                continue
            ioc_type = (
                finding.get("ioc_type")
                or finding.get("type")
                or finding.get("indicator_type")
                or "unknown"
            )
            ioc_value = (
                finding.get("ioc_value")
                or finding.get("value")
                or finding.get("indicator")
                or ""
            )
            confidence = float(finding.get("confidence", 0.5) or 0.5)
            # Clamp confidence into [0, 1]
            confidence = max(0.0, min(1.0, confidence))
            lane = str(finding.get("source_lane", "surface") or "surface")
            finding_id = (
                finding.get("finding_id")
                or f"fed-{sprint_id}-{uuid.uuid4().hex[:12]}"
            )
            payload_text = (
                finding.get("payload_text")
                or f"federated_lane={lane} ioc_type={ioc_type} ioc_value={ioc_value}"
            )

            if canonical_cls is not None:
                try:
                    cf = canonical_cls(
                        finding_id=finding_id,
                        query=query,
                        source_type=SOURCE_TYPE,
                        confidence=confidence,
                        ts=time.time(),
                        provenance=("federated_research", f"lane={lane}"),
                        payload_text=payload_text,
                    )
                    out.append(cf)
                    continue
                except Exception as e:
                    logger.debug(
                        "[FED-SIDECAR] CanonicalFinding construction "
                        "failed: %s — falling back to dict",
                        e,
                    )

            # Fallback: plain dict with canonical-ish shape
            out.append({
                "finding_id": finding_id,
                "query": query,
                "source_type": SOURCE_TYPE,
                "confidence": confidence,
                "ts": time.time(),
                "provenance": ("federated_research", f"lane={lane}"),
                "payload_text": payload_text,
            })
        return out
