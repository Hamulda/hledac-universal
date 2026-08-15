"""
F350M-FED-P: InMemoryPeerNodeTransport — in-process peer bridge for tests.

Sprint: F350M-FED-P / P2P Transport Activation 2026-06-04

Target: federated/transports/inmemory_peer.py

PURPOSE
=======
In-process peer bridge that satisfies the NodeTransport Protocol
without any sockets, mDNS, or Noise XX crypto. It exists for:

  1. Hermetic tests (no network, no event loop coupling).
  2. Single-process multi-virtual-node simulation — two transports
     can be wired as "peers" in the same process for testing the
     federated pattern end-to-end.
  3. Smoke runs on CI / sandboxes where mDNS / UDP are blocked.

The bridge exposes the SAME async `run(lane, query) -> list[dict]`
contract as LaneDispatchTransport and PeerNodeTransport. It is
swappable via `NodeTransportFactory.create("inmemory_peer")`.

USAGE IN TESTS
==============
    from hledac.universal.federated.transports.inmemory_peer import (
        InMemoryPeerNodeTransport,
    )

    async def test_pair():
        t1 = InMemoryPeerNodeTransport(node_id="alice")
        t2 = InMemoryPeerNodeTransport(node_id="bob")
        t1.add_peer(t2)  # bidirectional bridge
        t2.add_peer(t1)
        t1.set_seed({"surface": [{"ioc_type": "domain", "ioc_value": "x.com"}]})
        findings = await t1.run("surface", "test query")
        assert findings[0]["ioc_value"] == "x.com"

The transport also supports a deterministic "seed" mode: pre-load
findings per lane, and the run() call returns them. This is the
closest analogue to the legacy _LocalNodeTransport but useful.

M1 8GB SAFETY
=============
- INMEMORY_PEER_MAX_PEERS = 4 (matches PEER_NODE_MAX_PEERS)
- All methods are pure async (no I/O).
- No sockets, no threads, no cryptography imports.
- close() is a no-op (no resources to release).
- Always fail-soft (return [] on any error).
"""



import asyncio
import logging
import time
from typing import Any

from .protocol import NodeTransportFactory, set_sprint_id_attr
from core import aclose

logger = logging.getLogger(__name__)


# --- M1 BOUNDS --------------------------------------------------------------

INMEMORY_PEER_MAX_PEERS: int = 4
"""Hard cap on paired in-memory peers."""

INMEMORY_PEER_MAX_SEEDS_PER_LANE: int = 25
"""Hard cap on pre-loaded findings per lane."""

INMEMORY_PEER_MSG_TIMEOUT_S: float = 0.5
"""Per-peer in-process message timeout. Bounded for test determinism."""


@NodeTransportFactory.register("inmemory_peer")
class InMemoryPeerNodeTransport:
    """
    In-process peer bridge for tests.

    Satisfies the NodeTransport Protocol. Each `run(lane, query)`:

      1. If a seed is registered for the lane, return a copy of the
         seed (deterministic, fast — no peer roundtrip).
      2. Otherwise, round-robin to a paired peer and call its
         `run(lane, query)` via an in-process Future. Bounded by
         INMEMORY_PEER_MSG_TIMEOUT_S.
      3. Aggregate the response and return.

    This is the simplest "real" transport: it actually exercises the
    full per-node contract (construct, run, find findings) but
    without any I/O.
    """

    __slots__ = (
        "node_id",
        "_peers",
        "_seeds",
        "_sprint_id",
        "_round_robin_idx",
        "_calls",
        "_closed",
    )

    def __init__(self, node_id: str = "node-local") -> None:
        self.node_id: str = str(node_id or "node-local")[:64]
        # Paired peers (InMemoryPeerNodeTransport instances).
        self._peers: dict[str, InMemoryPeerNodeTransport] = {}
        # Per-lane seed findings (deterministic fallback).
        self._seeds: dict[str, list[dict[str, Any]]] = {}
        self._sprint_id: str = ""
        self._round_robin_idx: int = 0
        # Diagnostic counters.
        self._calls: int = 0
        self._closed: bool = False

    def set_sprint_id(self, sprint_id: str) -> None:
        """Set sprint id for traceability."""
        set_sprint_id_attr(self, sprint_id)

    def set_seed(self, lane_to_findings: dict[str, list[dict[str, Any]]]) -> None:
        """
        Pre-load deterministic findings per lane. Bounded by
        INMEMORY_PEER_MAX_SEEDS_PER_LANE per lane.

        Replaces any existing seed for the same lane.
        """
        for lane, findings in lane_to_findings.items():
            if not isinstance(findings, list):
                continue
            bounded: list[dict[str, Any]] = []
            for f in findings:
                if not isinstance(f, dict):
                    continue
                bounded.append(dict(f))
                if len(bounded) >= INMEMORY_PEER_MAX_SEEDS_PER_LANE:
                    break
            self._seeds[str(lane)] = bounded

    def add_peer(self, peer: InMemoryPeerNodeTransport) -> None:
        """
        Pair this transport with another in-process peer (bidirectional).
        Bounded by INMEMORY_PEER_MAX_PEERS.
        """
        if len(self._peers) >= INMEMORY_PEER_MAX_PEERS:
            logger.debug(
                "[FED-IMM] max peers reached (%d), cannot add %s",
                INMEMORY_PEER_MAX_PEERS, peer.node_id,
            )
            return
        if peer is self:
            return  # no self-pair
        self._peers[peer.node_id] = peer
        # Bidirectional — but only if the peer has room.
        if len(peer._peers) < INMEMORY_PEER_MAX_PEERS and self.node_id not in peer._peers:
            peer._peers[self.node_id] = self

    def remove_peer(self, peer_id: str) -> None:
        """Remove a paired peer (one-directional removal)."""
        self._peers.pop(peer_id, None)

    @property
    def peer_count(self) -> int:
        return len(self._peers)

    @property
    def call_count(self) -> int:
        """Number of run() invocations (diagnostic)."""
        return self._calls

    async def run(self, lane: str, query: str) -> list[dict[str, Any]]:
        """
        Return seeded findings (if any) for the lane, otherwise
        round-robin to a paired peer.

        Always fail-soft. Returns [] on any error.
        """
        started = time.monotonic()
        try:
            if self._closed:
                return []
            self._calls += 1
            lane_key = str(lane)[:32]
            # 1. Seed path — deterministic.
            seed = self._seeds.get(lane_key)
            if seed:
                out: list[dict[str, Any]] = []
                for f in seed[:INMEMORY_PEER_MAX_SEEDS_PER_LANE]:
                    if not isinstance(f, dict):
                        continue
                    norm = _normalize_inmem_finding(f, lane_key, self.node_id, self._sprint_id)
                    if norm is not None:
                        out.append(norm)
                return out
            # 2. Peer round-robin path.
            if not self._peers:
                return []
            peer_ids = sorted(self._peers.keys())
            self._round_robin_idx = (self._round_robin_idx + 1) % len(peer_ids)
            peer_id = peer_ids[self._round_robin_idx]
            peer = self._peers[peer_id]
            try:
                async with asyncio.timeout(INMEMORY_PEER_MSG_TIMEOUT_S):
                    peer_findings = await peer._serve(lane_key, query)
            except TimeoutError:
                logger.debug("[FED-IMM] peer %s serve timeout lane=%r", peer_id, lane)
                return []
            out2: list[dict[str, Any]] = []
            for f in (peer_findings or [])[:INMEMORY_PEER_MAX_SEEDS_PER_LANE]:
                if not isinstance(f, dict):
                    continue
                norm = _normalize_inmem_finding(f, lane_key, self.node_id, self._sprint_id)
                if norm is not None:
                    out2.append(norm)
            elapsed = time.monotonic() - started
            logger.debug(
                "[FED-IMM] node=%s lane=%r peer=%s findings=%d dur=%.4fs",
                self.node_id, lane, peer_id, len(out2), elapsed,
            )
            return out2
        except asyncio.CancelledError:
            raise
        except Exception as e:  # GHOST_INVARIANT: fail-soft
            elapsed = time.monotonic() - started
            logger.warning(
                "[FED-IMM] run fail-soft lane=%r %s: %s dur=%.4fs",
                lane, type(e).__name__, e, elapsed,
            )
            return []

    async def _serve(self, lane: str, query: str) -> list[dict[str, Any]]:
        """
        Internal: serve a peer's run() call. Returns the peer's seed
        findings for the lane (no recursive peer hop). Mirrors the
        run() method but is the "server-side" entry point.
        """
        seed = self._seeds.get(str(lane)[:32])
        if not seed:
            return []
        # Return a defensive copy so the caller can't mutate our seed.
        return [dict(f) for f in seed[:INMEMORY_PEER_MAX_SEEDS_PER_LANE] if isinstance(f, dict)]

    async def close(self) -> None:
        """Idempotent no-op. No resources to release."""
        self._closed = True
        self._peers.clear()
        self._seeds.clear()


def _normalize_inmem_finding(
    raw: dict[str, Any],
    lane: str,
    node_id: str,
    sprint_id: str,
) -> dict[str, Any] | None:
    """Normalize an in-memory finding into the federated contract."""
    if not isinstance(raw, dict):
        return None
    ioc_type = raw.get("ioc_type") or raw.get("type") or "observation"
    ioc_value = raw.get("ioc_value") or raw.get("value") or ""
    if not ioc_value:
        return None
    try:
        confidence = float(raw.get("confidence", 0.5) or 0.5)
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))
    finding: dict[str, Any] = {
        "ioc_type": str(ioc_type)[:64],
        "ioc_value": str(ioc_value)[:512],
        "confidence": confidence,
        "source_lane": lane,
        "source_type": "federated_inmemory_peer",
        "provenance": ("federated_inmemory_peer", f"node={node_id}"),
    }
    payload = raw.get("payload_text") or raw.get("payload")
    if payload is not None and isinstance(payload, str):
        finding["payload_text"] = payload[:1024]
    if sprint_id:
        finding["sprint_id"] = str(sprint_id)[:64]
    return finding


__all__ = [
    "InMemoryPeerNodeTransport",
    "INMEMORY_PEER_MAX_PEERS",
    "INMEMORY_PEER_MAX_SEEDS_PER_LANE",
    "INMEMORY_PEER_MSG_TIMEOUT_S",
]
