"""
Bitcoin UTXO Graph Analyzer
===========================







ISSUE [UNINDEXED]-009: Native UTXO graph analysis for Bitcoin forensics.

Provides:
- UTXO graph construction from raw Bitcoin transaction data (txid -> inputs -> outputs)
- Change address detection via heuristics (one-time use, round-number outputs)
- Multi-input clustering via connected components on the UTXO graph
- Local analysis mode — no API dependency, works on raw transaction data

M1 8GB Memory Ceiling:
- MAX_NODES = 100_000 — hard bound to prevent OOM
- igraph C-core operations — 5-10x faster than NetworkX
- __slots__ on UTXOGraph — per-instance overhead ~200 bytes
- Estimated RAM: ~20MB for 10K transaction UTXO graph

Architecture:
- igraph.Graph as the primary data structure (directed: txid -> inputs/outputs)
- Connected components algorithm for multi-input clustering
- Change address heuristics based on established Bitcoin forensics literature

References:
- Meiklejohn et al. (2013) "A Fistful of Bitcoins"
- Androulaki et al. (2013) "Evaluating User Privacy in Bitcoin"
- Ron & Shamir (2013) "Quantitative Analysis of the Full Bitcoin Transaction Graph"
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# --- Lazy igraph import (M1-optimized C-core, same pattern as utils/graph_utils.py) ---
_igraph: Any = None
_IGRAPH_AVAILABLE: bool = False
try:
    import igraph as _igraph
    _IGRAPH_AVAILABLE = True
except ImportError:
    _igraph = None
    logger.debug("igraph not available — UTXO graph analysis disabled (fallback to heuristics)")


# ---------------------------------------------------------------------------
# Constants (M1 8GB bounded)
# ---------------------------------------------------------------------------

# Hard cap on number of nodes in the UTXO graph to prevent OOM.
# Each igraph node uses ~100-200 bytes, so 100K nodes ≈ 20MB.
MAX_UTXO_NODES: int = 100_000

# Maximum number of transactions to ingest.
MAX_TRANSACTIONS: int = 10_000

# Change address detection thresholds.
CHANGE_ADDRESS_MIN_SATOSHI: int = 546  # dust limit
ROUND_AMOUNT_TOLERANCE: float = 1e-8   # for detecting near-integer BTC amounts


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class UTXONode:
    """A node in the UTXO graph — either a transaction or an address."""

    node_id: str
    node_type: str  # 'tx' or 'address'
    value_satoshis: int = 0
    timestamp: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class UTXOEdge:
    """A directed edge in the UTXO graph (tx -> address or address -> tx)."""

    source_id: str
    target_id: str
    value_satoshis: int = 0
    is_coinbase: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ChangeAddressResult:
    """Result of change address detection."""

    address: str
    is_change: bool
    confidence: float
    heuristic: str  # 'fresh_address', 'round_output', 'one_input', 'address_reuse'
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class UTXOCluster:
    """A cluster of addresses identified via UTXO graph analysis."""

    cluster_id: str
    addresses: list[str]
    transactions: list[str]
    confidence: float
    cluster_type: str  # 'common_input', 'change_address_pair', 'peel_chain'
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class UTXOGraphAnalysis:
    """Complete UTXO graph analysis result."""

    graph_node_count: int
    graph_edge_count: int
    clusters: list[UTXOCluster]
    change_addresses: list[ChangeAddressResult]
    community_count: int = 0
    largest_community_size: int = 0
    processing_time_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# UTXOGraph — Core UTXO Graph Analyzer
# ---------------------------------------------------------------------------


class UTXOGraph:
    """
    Native Bitcoin UTXO graph analyzer using igraph.

    Builds a directed UTXO graph from raw Bitcoin transaction data and performs:
    - Multi-input clustering via connected components (co-spending heuristic)
    - Change address detection (fresh addresses, round-number outputs)
    - Peel chain detection
    - Community structure analysis

    M1 8GB Optimized:
    - igraph C-core for all graph operations (50-100x faster than pure Python)
    - Bounded by MAX_UTXO_NODES and MAX_TRANSACTIONS
    - __slots__ minimizes per-instance memory (~200 bytes)
    """

    __slots__ = (
        "_address_to_id",
        "_change_addresses",
        "_clusters",
        "_graph",
        "_id_to_address",
        "_id_to_tx",
        "_initialized",
        "_tx_counter",
        "_tx_to_id",
    )

    def __init__(self) -> None:
        self._graph: Any = None  # igraph.Graph
        self._initialized: bool = False
        self._tx_to_id: dict[str, int] = {}
        self._id_to_tx: dict[int, str] = {}
        self._address_to_id: dict[str, int] = {}
        self._id_to_address: dict[int, str] = {}
        self._tx_counter: int = 0
        self._clusters: list[UTXOCluster] = []
        self._change_addresses: list[ChangeAddressResult] = []

    # ------------------------------------------------------------------
    # Graph Construction
    # ------------------------------------------------------------------

    def _build_utxo_graph(self, transactions: list[dict[str, Any]]) -> bool:
        """
        Build a directed UTXO graph from raw Bitcoin transactions.

        Graph structure:
        - Nodes: 'tx:<txid>' and 'addr:<address>'
        - Edges: 'addr:<addr>' -> 'tx:<txid>' (input side)
                 'tx:<txid>' -> 'addr:<addr>' (output side)

        Memory bound: MAX_UTXO_NODES cap prevents OOM on large datasets.

        Returns True if graph was built successfully.
        """
        if not _IGRAPH_AVAILABLE:
            logger.warning("igraph not available — cannot build UTXO graph")
            return False

        start_time = time.monotonic()
        self._tx_to_id.clear()
        self._id_to_tx.clear()
        self._address_to_id.clear()
        self._id_to_address.clear()
        self._tx_counter = 0

        tx_count = min(len(transactions), MAX_TRANSACTIONS)

        # First pass: count unique addresses to pre-allocate
        addr_set: set[str] = set()
        valid_txs: list[dict[str, Any]] = []

        for tx in transactions[:tx_count]:
            tx_data = tx.get("transaction", tx)
            inputs = tx_data.get("inputs", [])
            outputs = tx_data.get("outputs", [])
            if not inputs or not outputs:
                continue
            valid_txs.append(tx_data)
            for inp in inputs:
                addr = inp.get("recipient", inp.get("address", ""))
                if addr:
                    addr_set.add(addr)
            for out in outputs:
                addr = out.get("recipient", out.get("address", ""))
                if addr:
                    addr_set.add(addr)

            if len(addr_set) > MAX_UTXO_NODES:
                logger.warning(
                    f"UTXO graph: address count ({len(addr_set)}) exceeds MAX_UTXO_NODES "
                    f"({MAX_UTXO_NODES}), truncating"
                )
                break

        # Cap at MAX_UTXO_NODES
        max_nodes = min(len(addr_set) + len(valid_txs), MAX_UTXO_NODES)

        # Build node vectors
        nodes: list[UTXONode] = []
        node_id_counter: dict[str, int] = {}

        for tx_data in valid_txs:
            txid = tx_data.get("hash", tx_data.get("txid", ""))
            if not txid:
                continue
            if len(nodes) >= max_nodes:
                break

            tx_node_id = f"tx:{txid}"
            if tx_node_id not in node_id_counter:
                node_id_counter[tx_node_id] = len(nodes)
                nodes.append(
                    UTXONode(
                        node_id=tx_node_id,
                        node_type="tx",
                        timestamp=tx_data.get("time", tx_data.get("block_time", 0)),
                        metadata={"txid": txid},
                    )
                )
            self._tx_to_id[txid] = node_id_counter[tx_node_id]
            self._id_to_tx[node_id_counter[tx_node_id]] = txid
            self._tx_counter += 1

            # Add output address nodes
            for out in tx_data.get("outputs", []):
                addr = out.get("recipient", out.get("address", ""))
                if not addr:
                    continue
                addr_node_id = f"addr:{addr}"
                if addr_node_id not in node_id_counter:
                    node_id_counter[addr_node_id] = len(nodes)
                    nodes.append(
                        UTXONode(
                            node_id=addr_node_id,
                            node_type="address",
                            value_satoshis=int(out.get("value", 0)),
                            metadata={"address": addr},
                        )
                    )
                    self._address_to_id[addr] = node_id_counter[addr_node_id]
                    self._id_to_address[node_id_counter[addr_node_id]] = addr

            # Also add input address nodes (may not appear in outputs)
            for inp in tx_data.get("inputs", []):
                addr = inp.get("recipient", inp.get("address", ""))
                if not addr:
                    continue
                addr_node_id = f"addr:{addr}"
                if addr_node_id not in node_id_counter:
                    node_id_counter[addr_node_id] = len(nodes)
                    nodes.append(
                        UTXONode(
                            node_id=addr_node_id,
                            node_type="address",
                            value_satoshis=int(inp.get("value", 0)),
                            metadata={"address": addr},
                        )
                    )
                    self._address_to_id[addr] = node_id_counter[addr_node_id]
                    self._id_to_address[node_id_counter[addr_node_id]] = addr

        logger.info(
            f"UTXO graph: {len(nodes)} nodes ({self._tx_counter} tx, "
            f"{len(self._address_to_id)} unique addresses)"
        )

        # Build edges
        edges: list[UTXOEdge] = []
        edge_list: list[tuple[int, int]] = []

        for tx_data in valid_txs:
            txid = tx_data.get("hash", tx_data.get("txid", ""))
            if txid not in self._tx_to_id:
                continue
            tx_vid = self._tx_to_id[txid]

            is_coinbase = tx_data.get("is_coinbase", False)

            # Input edges: address -> tx
            for inp in tx_data.get("inputs", []):
                addr = inp.get("recipient", inp.get("address", ""))
                if not addr:
                    continue
                addr_vid = self._address_to_id.get(addr)
                if addr_vid is not None:
                    edge_list.append((addr_vid, tx_vid))
                    edges.append(
                        UTXOEdge(
                            source_id=f"addr:{addr}",
                            target_id=f"tx:{txid}",
                            value_satoshis=int(inp.get("value", 0)),
                            is_coinbase=is_coinbase,
                        )
                    )

            # Output edges: tx -> address
            for out in tx_data.get("outputs", []):
                addr = out.get("recipient", out.get("address", ""))
                if not addr:
                    continue
                addr_vid = self._address_to_id.get(addr)
                if addr_vid is not None:
                    edge_list.append((tx_vid, addr_vid))
                    edges.append(
                        UTXOEdge(
                            source_id=f"tx:{txid}",
                            target_id=f"addr:{addr}",
                            value_satoshis=int(out.get("value", 0)),
                            is_coinbase=is_coinbase,
                        )
                    )

        # Build igraph
        try:
            self._graph = _igraph.Graph(
                n=len(nodes),
                edges=edge_list,
                directed=True,
            )
            # Attach vertex attributes
            self._graph.vs["node_id"] = [n.node_id for n in nodes]
            self._graph.vs["node_type"] = [n.node_type for n in nodes]
            self._graph.vs["value_satoshis"] = [n.value_satoshis for n in nodes]
            self._graph.vs["timestamp"] = [n.timestamp for n in nodes]
            self._initialized = True
        except Exception as e:
            logger.error(f"Failed to build igraph UTXO graph: {e}")
            return False

        elapsed = (time.monotonic() - start_time) * 1000
        logger.info(
            f"UTXO graph built: {self._graph.vcount()} nodes, "
            f"{self._graph.ecount()} edges in {elapsed:.1f}ms"
        )

        return True

    # ------------------------------------------------------------------
    # Change Address Detection
    # ------------------------------------------------------------------

    def _detect_change_addresses(self) -> list[ChangeAddressResult]:
        """
        Detect likely change addresses using heuristics from Bitcoin forensics literature.

        Heuristics applied (Meiklejohn et al. 2013, Androulaki et al. 2013):
        1. **Fresh address**: Address appears only once in the graph (one-time use)
           — Bitcoin wallets generate new addresses for change.
        2. **Round-number output**: The non-change output is often a round number
           (e.g., 1.0 BTC), while change is an irregular amount.
        3. **One input, two outputs**: Common payment pattern where one output
           is the payment (round amount) and the other is change.
        4. **Address reuse**: If an address appears as input in a subsequent tx
           AND also appears as output in an earlier tx, it's unlikely to be change.

        Returns list of ChangeAddressResult per address.
        """
        if not self._initialized or self._graph is None:
            return []

        results: list[ChangeAddressResult] = []
        addr_indices = [
            v.index
            for v in self._graph.vs
            if v["node_type"] == "address"
        ]
        if not addr_indices:
            return results

        # Pre-compute address degree (frequency) using igraph C-core
        degrees = self._graph.degree(addr_indices, mode=_igraph.ALL)

        # Build adjacency: for each tx node, collect its input and output addresses
        # We use igraph neighborhood for fast lookups
        for v_idx in addr_indices:
            addr = self._graph.vs[v_idx]["node_id"]
            # Remove 'addr:' prefix
            addr_value = addr[5:] if addr.startswith("addr:") else addr
            deg = degrees[addr_indices.index(v_idx)] if addr_indices else 0

            confidence = 0.0
            heuristics: list[str] = []

            # Heuristic 1: Fresh address (appears only once — degree = 1 or 2)
            # In- and out-degree each count separately in directed graph
            in_deg = self._graph.degree(v_idx, mode=_igraph.IN)
            out_deg = self._graph.degree(v_idx, mode=_igraph.OUT)
            if in_deg <= 1 and out_deg <= 1:
                confidence += 0.3
                heuristics.append("fresh_address")

            # Heuristic 2: Check for round-number neighbors
            # If this address receives an amount that is NOT a round number
            # while its sibling output (same tx) IS a round number, it's likely change
            value = self._graph.vs[v_idx]["value_satoshis"]
            if value > CHANGE_ADDRESS_MIN_SATOSHI:
                btc_value = value / 100_000_000.0
                if not _is_round_btc(btc_value, ROUND_AMOUNT_TOLERANCE):
                    # Check if any sibling output IS round
                    predecessors = self._graph.neighbors(v_idx, mode=_igraph.IN)
                    for pred in predecessors:
                        if self._graph.vs[pred]["node_type"] == "tx":
                            siblings = self._graph.neighbors(pred, mode=_igraph.OUT)
                            for sib in siblings:
                                if sib != v_idx:
                                    sib_val = self._graph.vs[sib]["value_satoshis"] / 100_000_000.0
                                    if _is_round_btc(sib_val, ROUND_AMOUNT_TOLERANCE):
                                        confidence += 0.25
                                        heuristics.append("round_output")
                                        break
                            if "round_output" in heuristics:
                                break

            # Heuristic 3: One-input pattern
            if in_deg == 1:
                predecessors = self._graph.neighbors(v_idx, mode=_igraph.IN)
                if len(predecessors) == 1:
                    tx_node = predecessors[0]
                    input_count = len(self._graph.neighbors(tx_node, mode=_igraph.IN))
                    if input_count == 1:
                        confidence += 0.1
                        heuristics.append("one_input")

            # Heuristic 4: Address reuse (penalty)
            if deg > 2:
                confidence -= 0.2
                heuristics.append("address_reuse")

            # Normalize and clamp confidence
            confidence = max(0.0, min(1.0, confidence))

            is_change = confidence >= 0.3

            results.append(
                ChangeAddressResult(
                    address=addr_value,
                    is_change=is_change,
                    confidence=confidence,
                    heuristic="+".join(heuristics) if heuristics else "none",
                    metadata={
                        "in_degree": in_deg,
                        "out_degree": out_deg,
                        "total_degree": deg,
                    },
                )
            )

        self._change_addresses = results
        logger.info(
            f"Change address detection: {sum(1 for r in results if r.is_change)}/{len(results)} "
            f"addresses classified as change"
        )
        return results

    # ------------------------------------------------------------------
    # Multi-Input Clustering (Connected Components)
    # ------------------------------------------------------------------

    def _cluster_by_common_input_graph(self) -> list[UTXOCluster]:
        """
        Cluster addresses using multi-input heuristic via connected components.

        The multi-input heuristic (Meiklejohn et al. 2013): if two addresses appear
        as inputs to the same transaction, they are controlled by the same entity.

        We build an undirected projection of the UTXO graph:
          - Address nodes are connected if they share a common transaction as input.
          - Connected components in this graph form clusters.

        Returns list of UTXOCluster objects.
        """
        if not self._initialized or self._graph is None:
            return []

        start_time = time.monotonic()

        # Step 1: Build address co-input projection
        # For each tx node, collect all addresses that are inputs to it
        tx_to_inputs: dict[int, list[int]] = {}
        tx_indices = [
            v.index for v in self._graph.vs if v["node_type"] == "tx"
        ]

        for tx_idx in tx_indices:
            inputs: list[int] = []
            # Outgoing from tx are outputs, incoming to tx are inputs
            in_neighbors = self._graph.neighbors(tx_idx, mode=_igraph.IN)
            for nb in in_neighbors:
                if self._graph.vs[nb]["node_type"] == "address":
                    inputs.append(nb)
            if len(inputs) >= 2:
                tx_to_inputs[tx_idx] = inputs

        if not tx_to_inputs:
            logger.info("No multi-input transactions found for clustering")
            return []

        # Step 2: Build the address-address projection graph
        # Create edges between addresses that co-occur as inputs
        projection_edges: list[tuple[int, int]] = []
        addr_to_pid: dict[int, int] = {}
        pid_to_addr: dict[int, int] = {}

        for inputs in tx_to_inputs.values():
            for i, addr_a in enumerate(inputs):
                if addr_a not in addr_to_pid:
                    pid = len(addr_to_pid)
                    addr_to_pid[addr_a] = pid
                    pid_to_addr[pid] = addr_a
                for addr_b in inputs[i + 1 :]:
                    if addr_b not in addr_to_pid:
                        pid = len(addr_to_pid)
                        addr_to_pid[addr_b] = pid
                        pid_to_addr[pid] = addr_b
                    projection_edges.append(
                        (addr_to_pid[addr_a], addr_to_pid[addr_b])
                    )

        if not projection_edges:
            logger.info("No projection edges created — addresses do not share inputs")
            return []

        # Step 3: Build igraph projection and run connected components
        try:
            projection_graph = _igraph.Graph(
                n=len(addr_to_pid),
                edges=projection_edges,
                directed=False,
            )
        except Exception as e:
            logger.error(f"Failed to build projection graph: {e}")
            return []

        # Connected components via igraph C-core
        components = projection_graph.components(mode=_igraph.WEAK)
        logger.info(f"Connected components: {len(components)}")

        # Step 4: Build cluster objects
        clusters: list[UTXOCluster] = []
        for comp_idx, component in enumerate(components):
            member_count = len(component)
            if member_count < 2:
                continue  # Skip singletons

            addresses: list[str] = []
            txids: set[str] = set()

            for pid in component:
                orig_addr_idx = pid_to_addr[pid]
                addr_str = self._graph.vs[orig_addr_idx]["node_id"]
                addr_value = addr_str[5:] if addr_str.startswith("addr:") else addr_str
                addresses.append(addr_value)

                # Collect transactions that used this address as input
                out_neighbors = self._graph.neighbors(orig_addr_idx, mode=_igraph.OUT)
                for tx_nb in out_neighbors:
                    if self._graph.vs[tx_nb]["node_type"] == "tx":
                        tx_node_id = self._graph.vs[tx_nb]["node_id"]
                        txid = tx_node_id[3:] if tx_node_id.startswith("tx:") else tx_node_id
                        txids.add(txid)

            # Confidence: higher for larger clusters with more shared transactions
            confidence = min(0.95, 0.5 + member_count * 0.05)
            if len(txids) >= 3:
                confidence = min(0.95, confidence + 0.1)

            cluster_id = _generate_cluster_id(addresses)

            clusters.append(
                UTXOCluster(
                    cluster_id=cluster_id,
                    addresses=addresses,
                    transactions=sorted(txids),
                    confidence=confidence,
                    cluster_type="common_input",
                    metadata={
                        "member_count": member_count,
                        "shared_tx_count": len(txids),
                        "component_index": comp_idx,
                    },
                )
            )

        elapsed = (time.monotonic() - start_time) * 1000

        # Sort by size (largest first)
        clusters.sort(key=lambda c: len(c.addresses), reverse=True)
        self._clusters = clusters

        logger.info(
            f"UTXO clustering: {len(clusters)} clusters found "
            f"({sum(len(c.addresses) for c in clusters)} addresses) in {elapsed:.1f}ms"
        )
        return clusters

    # ------------------------------------------------------------------
    # Peel Chain Detection
    # ------------------------------------------------------------------

    def _detect_peel_chains(self) -> list[UTXOCluster]:
        """
        Detect peel chain patterns in the UTXO graph.

        A peel chain occurs when:
        1. A large UTXO is spent
        2. A small amount goes to the intended recipient
        3. The change (large portion) goes to a new address under same control
        4. Process repeats

        We detect this by analyzing transactions where one output is much larger
        than the other output (typical ratio > 10:1) and the larger output is
        subsequently spent in a similar pattern.
        """
        if not self._initialized or self._graph is None:
            return []

        peel_chains: list[UTXOCluster] = []
        tx_indices = [
            v.index for v in self._graph.vs if v["node_type"] == "tx"
        ]

        processed_txs: set[str] = set()
        chain_id = 0

        for tx_idx in tx_indices:
            tx_node_id = self._graph.vs[tx_idx]["node_id"]
            txid = tx_node_id[3:] if tx_node_id.startswith("tx:") else tx_node_id
            if txid in processed_txs:
                continue

            # Get outputs of this transaction
            out_neighbors = self._graph.neighbors(tx_idx, mode=_igraph.OUT)
            addr_outputs = [
                nb for nb in out_neighbors
                if self._graph.vs[nb]["node_type"] == "address"
            ]

            if len(addr_outputs) < 2:
                continue

            # Get output values
            output_values: list[tuple[int, int]] = []
            for out in addr_outputs:
                val = self._graph.vs[out]["value_satoshis"]
                output_values.append((out, val))

            # Check for peel chain: one output >> another
            output_values.sort(key=lambda x: x[1], reverse=True)
            if len(output_values) < 2:
                continue

            large_out, large_val = output_values[0]
            small_out, small_val = output_values[-1]

            if small_val == 0:
                continue

            ratio = large_val / small_val
            if ratio > 5.0:
                # Potential peel chain — track the large output
                large_addr = self._graph.vs[large_out]["node_id"]
                large_addr_value = (
                    large_addr[5:] if large_addr.startswith("addr:") else large_addr
                )

                # Follow the large output to its spending transaction
                spend_txs = self._graph.neighbors(large_out, mode=_igraph.OUT)
                chain_addresses = [large_addr_value]
                chain_txs = [txid]

                for spend_tx in spend_txs:
                    if self._graph.vs[spend_tx]["node_type"] == "tx":
                        spend_txid_raw = self._graph.vs[spend_tx]["node_id"]
                        spend_txid = (
                            spend_txid_raw[3:]
                            if spend_txid_raw.startswith("tx:")
                            else spend_txid_raw
                        )
                        if spend_txid not in processed_txs:
                            chain_txs.append(spend_txid)
                            processed_txs.add(spend_txid)

                if len(chain_txs) >= 2:
                    chain_id += 1
                    peel_chains.append(
                        UTXOCluster(
                            cluster_id=f"peel_chain_{chain_id}",
                            addresses=chain_addresses,
                            transactions=chain_txs,
                            confidence=min(0.85, 0.4 + len(chain_txs) * 0.1),
                            cluster_type="peel_chain",
                            metadata={
                                "value_ratio": ratio,
                                "chain_length": len(chain_txs),
                                "large_value_btc": large_val / 100_000_000.0,
                                "small_value_btc": small_val / 100_000_000.0,
                            },
                        )
                    )

            processed_txs.add(txid)

        logger.info(f"Peel chain detection: {len(peel_chains)} chains found")
        self._clusters.extend(peel_chains)
        return peel_chains

    # ------------------------------------------------------------------
    # Main Analysis Entry Point
    # ------------------------------------------------------------------

    def analyze_utxo_graph(
        self,
        transactions: list[dict[str, Any]],
        detect_change: bool = True,
        detect_peel: bool = True,
    ) -> UTXOGraphAnalysis:
        """
        Run full UTXO graph analysis on raw Bitcoin transaction data.

        Args:
            transactions: List of raw BTC transaction dicts.
                Each dict must have 'hash'/'txid', 'inputs' (list with 'recipient'/'address'),
                and 'outputs' (list with 'recipient'/'address' and 'value' in satoshis).
            detect_change: Enable change address detection (default True).
            detect_peel: Enable peel chain detection (default True).

        Returns:
            UTXOGraphAnalysis with clusters, change addresses, and graph metrics.
        """
        start_time = time.monotonic()

        if not _IGRAPH_AVAILABLE:
            return UTXOGraphAnalysis(
                graph_node_count=0,
                graph_edge_count=0,
                clusters=[],
                change_addresses=[],
                processing_time_ms=(time.monotonic() - start_time) * 1000,
                metadata={"error": "igraph not available"},
            )

        # Build graph
        success = self._build_utxo_graph(transactions)
        if not success:
            return UTXOGraphAnalysis(
                graph_node_count=0,
                graph_edge_count=0,
                clusters=[],
                change_addresses=[],
                processing_time_ms=(time.monotonic() - start_time) * 1000,
                metadata={"error": "graph construction failed"},
            )

        # Detect change addresses
        change_results: list[ChangeAddressResult] = []
        if detect_change:
            change_results = self._detect_change_addresses()

        # Cluster via multi-input heuristic
        clusters: list[UTXOCluster] = self._cluster_by_common_input_graph()

        # Detect peel chains
        if detect_peel:
            self._detect_peel_chains()

        # Community structure via igraph label propagation (if graph is large enough)
        community_count = 0
        largest_community_size = 0
        if self._graph.vcount() >= 10:
            try:
                # Fast label propagation on a simplified undirected version
                undirected = self._graph.as_undirected()
                membership = undirected.community_label_propagation()
                communities_set = set(membership.membership)
                community_count = len(communities_set)
                largest_community_size = max(
                    (sum(1 for m in membership.membership if m == c))
                    for c in communities_set
                )
            except Exception as e:
                logger.debug(f"Community detection skipped: {e}")

        elapsed = (time.monotonic() - start_time) * 1000

        return UTXOGraphAnalysis(
            graph_node_count=self._graph.vcount(),
            graph_edge_count=self._graph.ecount(),
            clusters=clusters,
            change_addresses=change_results,
            community_count=community_count,
            largest_community_size=largest_community_size,
            processing_time_ms=elapsed,
            metadata={
                "transaction_count": self._tx_counter,
                "unique_addresses": len(self._address_to_id),
                "algorithm": "igraph_c_core",
            },
        )

    # ------------------------------------------------------------------
    # Convenience: cluster addresses via graph traversal (API-free)
    # ------------------------------------------------------------------

    def cluster_addresses_graph(
        self,
        addresses: list[str],
        transactions: list[dict[str, Any]],
    ) -> list[UTXOCluster]:
        """
        Cluster addresses using UTXO graph analysis (no API required).

        This is the local-mode alternative to BlockchainForensics.cluster_addresses()
        which requires Etherscan/Blockchair API access.

        Args:
            addresses: List of Bitcoin addresses to cluster.
            transactions: Raw BTC transactions containing these addresses
                in inputs/outputs.

        Returns:
            List of UTXOCluster objects where each cluster groups addresses
            likely controlled by the same entity.
        """
        if not _IGRAPH_AVAILABLE:
            logger.warning("igraph not available — returning empty clusters")
            return []

        if len(addresses) < 2:
            return []

        # Filter transactions to those involving our addresses
        addr_set = set(addresses)
        relevant_txs: list[dict[str, Any]] = []

        for tx in transactions:
            tx_data = tx.get("transaction", tx)
            tx_addrs: set[str] = set()

            for inp in tx_data.get("inputs", []):
                a = inp.get("recipient", inp.get("address", ""))
                if a:
                    tx_addrs.add(a)
            for out in tx_data.get("outputs", []):
                a = out.get("recipient", out.get("address", ""))
                if a:
                    tx_addrs.add(a)

            if tx_addrs & addr_set:
                relevant_txs.append(tx_data)

        if not relevant_txs:
            logger.info("No transactions found containing the specified addresses")
            return []

        # Analyze and filter clusters to only those containing our addresses
        analysis = self.analyze_utxo_graph(relevant_txs, detect_change=False, detect_peel=False)

        # Filter clusters to only those containing at least one of our target addresses
        result_clusters: list[UTXOCluster] = []
        for cluster in analysis.clusters:
            if any(a in addr_set for a in cluster.addresses):
                result_clusters.append(cluster)

        # Add multi-address clusters where our addresses were grouped together
        return result_clusters


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_round_btc(value: float, tolerance: float = ROUND_AMOUNT_TOLERANCE) -> bool:
    """Check if a BTC amount is a 'round number' (integer, 0.1, 0.5, etc.)."""
    if value <= 0:
        return False
    # Check common round values
    for rv in [0.001, 0.01, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 100.0]:
        if abs(value - rv) < tolerance:
            return True
    # Check integer values
    if abs(value - round(value)) < tolerance:
        return True
    return False


def _generate_cluster_id(addresses: list[str]) -> str:
    """Generate a deterministic cluster ID from addresses."""
    sorted_addrs = sorted(addresses)
    hash_input = "".join(sorted_addrs).encode()
    return hashlib.sha256(hash_input).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Module-level Convenience Functions
# ---------------------------------------------------------------------------


def analyze_bitcoin_transactions(
    transactions: list[dict[str, Any]],
) -> UTXOGraphAnalysis:
    """
    Convenience function for quick UTXO graph analysis.

    Args:
        transactions: List of raw Bitcoin transaction dictionaries.

    Returns:
        UTXOGraphAnalysis with clustering results.
    """
    analyzer = UTXOGraph()
    return analyzer.analyze_utxo_graph(transactions)


def cluster_bitcoin_addresses(
    addresses: list[str],
    transactions: list[dict[str, Any]],
) -> list[UTXOCluster]:
    """
    Convenience function for address clustering via UTXO graph.

    Args:
        addresses: Bitcoin addresses to cluster.
        transactions: Raw transactions containing these addresses.

    Returns:
        List of UTXOCluster objects.
    """
    analyzer = UTXOGraph()
    return analyzer.cluster_addresses_graph(addresses, transactions)
