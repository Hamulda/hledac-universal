# DHT Real UDP Implementation — Sprint F214

## Overview

Real BitTorrent DHT (BEP-5) crawling over native `asyncio.DatagramProtocol`.
Replaces simulated in-memory DHT with actual UDP network traffic.

## Protocol — BEP-5 BitTorrent DHT

### Message Types (bencode)

| Type | Direction | Format |
|------|-----------|--------|
| PING | query->router | `{"y":"q","q":"ping","a":{"id":"<20-byte>"}}` |
| PONG | response->router | `{"y":"r","r":{"id":"<20-byte>"}}` |
| FIND_NODE | query->router | `{"y":"q","q":"find_node","a":{"id":"<20-byte>","target":"<20-byte>"}}` |
| FIND_NODE_R | response->router | `{"y":"r","r":{"id":"<20-byte>","nodes":"<compact>"}}` |
| GET_PEERS | query->router | `{"y":"q","q":"get_peers","a":{"id":"<20-byte>","info_hash":"<20-byte>"}}` |
| GET_PEERS_R | response->router | `{"y":"r","r":{"id":"<20-byte>","token":"<str>","nodes":"<compact>","values":[<ip,port>]}}` |
| ANNOUNCE_PEER | query->router | `{"y":"q","q":"announce_peer","a":{"id":"<20-byte>","info_hash":"<20-byte>","port":6881,"token":"<str>"}}` |

Compact node info: 26 bytes = 20-byte node_id + 4-byte IP + 2-byte port (big-endian).

### Bootstrap Nodes

```
router.bittorrent.com:6881
dht.transmissionbt.com:6881
router.utorrent.com:6881
dht.libtorrent.org:25401
```

## Architecture

### Gate: `HLEDAC_ENABLE_DHT=1`

```python
DHT_REAL_UDP = bool(os.getenv("HLEDAC_ENABLE_DHT", "0") == "1")
```

All real UDP code runs only when `DHT_REAL_UDP=True`. Default: simulated mode.

### DatagramProtocol — `_DHTBootstrapProtocol`

Located in `dht/kademlia_node.py`, replaces `_transport=None` stub with real UDP.

```python
class _DHTBootstrapProtocol(asyncio.DatagramProtocol):
    slots = ("_loop", "_node_id", "_nodes_found", "_error", "_transport")
    # connection_made(transport)
    # send(data, addr) -> (transport, None)
    # datagram_received(data, addr)
    # error_received(exc)
```

Key design: `send()` method returns `(transport, None)` so caller can use `asyncio.wait_for(protocol.send(...), timeout=DHT_BOOTSTRAP_TIMEOUT_S)`.

### Bootstrap Flow

```
start() -> _dht_bootstrap_real() (when DHT_REAL_UDP)
  ├── DHT_BOOTSTRAP_SEMAPHORE.acquire() — max 2 concurrent
  ├── loop.create_datagram_endpoint(_DHTBootstrapProtocol, local_addr=("0.0.0.0", 0))
  ├── Build FIND_NODE message (our own node_id as target)
  ├── protocol.send(bencoded, (host, port)) × 4 peers (each wait_for 5s)
  ├── asyncio.sleep(3.0) — collect routing responses
  └── Parse compact nodes -> _update_routing(node_id_hex, node_info)
```

### LMDB Routing Table Persistence

`LocalGraphStore` stores DHT routing nodes under `dht_nodes:` prefix.

```python
async def put_dht_node(self, node_id: str, host: str, port: int) -> None: ...
async def get_dht_node(self, node_id: str) -> Optional[Dict[str, Any]]: ...
async def get_all_dht_nodes(self, limit: int = 1000) -> List[Dict[str, Any]]: ...
async def clear_dht_nodes(self) -> None: ...
```

Persistence triggered at:
- Post-bootstrap: new nodes from FIND_NODE responses
- Post-crawl: accumulated routing table

## M1 Constraints Applied

| Constraint | Value | Location |
|-----------|-------|----------|
| Concurrent bootstrap limit | Semaphore(2) | `DHT_BOOTSTRAP_SEMAPHORE = asyncio.Semaphore(2)` |
| DHT request timeout | 5s | `DHT_BOOTSTRAP_TIMEOUT_S = 5.0` |
| Max probe duration | 120s | `MAX_DHT_PROBE_DURATION_S = 120` |
| Max pending RPCs | 5000 (existing) | `MAX_PENDING_RPCS = 5000` |
| RPC TTL eviction | 60s (existing) | `MAX_PENDING_RPC_TTL_S = 60.0` |

## Fail-Soft Guarantees

- `_dht_bootstrap_real()` wrapped in try/except — never propagates
- `_DHTBootstrapProtocol.datagram_received()` — all parse errors caught silently
- `crawl()` still returns partial results on DHT failure
- `probe_runner.scan_dht()` — any exception logged, returns empty list

## Backward Compatibility

- `BOOTSTRAP_PEERS` alias points to `DHT_BOOTSTRAP_PEERS` — existing code unchanged
- `is_dht_production_ready()` now returns `DHT_REAL_UDP` (was always `False`)
- `crawl_dht_for_keyword()` unchanged (simulated path still works when `DHT_REAL_UDP=False`)

## `scan_dht(infohash)` — DeepProbeScanner Integration

In `deep_research/probe_runner.py`:

```python
async def scan_dht(
    self,
    infohash: str,
    store: "DuckDBShadowStore",
    timeout_s: float = 120.0,
) -> List[CanonicalFinding]:
    """
    F214: Real DHT peer discovery for a specific infohash.

    Uses real UDP bootstrapped KademliaNode to send GET_PEERS,
    collect peer addresses from responses, and return as
    CanonicalFinding objects with source_type="dht_discovery".

    Bounded: 120s max, fail-soft on errors.
    Returns: List[CanonicalFinding] (may be empty on errors).
    """
    if not DHT_REAL_UDP:
        return []  # Simulated mode — no real DHT
```

## Files Changed

| File | Change |
|------|--------|
| `dht/kademlia_node.py` | Real UDP bootstrap, `_DHTBootstrapProtocol`, LMDB persistence |
| `dht/local_graph.py` | `put_dht_node/get_dht_node/get_all_dht_nodes/clear_dht_nodes` |
| `deep_research/probe_runner.py` | `scan_dht(infohash)` method on DeepProbeScanner |
| `DHT_REAL_UDP_IMPLEMENTATION.md` | This document |