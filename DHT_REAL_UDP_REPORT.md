# DHT Real UDP Implementation Report (BEP-5)

**Date:** 2026-06-01  
**Sprint:** F214Q  
**Status:** IMPLEMENTED

---

## BEP-5 Message Format Implemented

### Message Types (Bencode)

| Message | Format | Status |
|---------|--------|--------|
| PING | `{"t": tid, "y": "q", "q": "ping", "a": {"id": node_id}}` | ✅ Implemented |
| PONG | `{"t": tid, "y": "r", "r": {"id": node_id}}` | ✅ Implemented |
| FIND_NODE | `{"t": tid, "y": "q", "q": "find_node", "a": {"id": node_id, "target": target_id}}` | ✅ Implemented |
| GET_PEERS | `{"t": tid, "y": "q", "q": "get_peers", "a": {"id": node_id, "info_hash": ih_bytes}}` | ✅ Implemented |
| ANNOUNCE_PEER | `{"t": tid, "y": "q", "q": "announce_peer", "a": {...}}` | ❌ Not implemented (optional) |

### Bencode Implementation
- **Location:** `dht/kademlia_node.py` lines 1263+
- **Encoder:** `_bencode(obj)` - recursive, dict keys sorted
- **Decoder:** `_bdecode(data)` - handles bytes dict keys (BEP-5 compliant)
- **Spec coverage:** integers, byte strings, lists, dicts

---

## Routing Table Persistence Schema

### LMDB Storage

**Database:** `local_graph.lmdb` (encrypted, AES-GCM)

**Key format:** `dht_node:<node_id_hex>`  
**Value format:** Encrypted JSON `{"host": ip, "port": port, "node_id": id}`

### Persistence Methods

| Method | Purpose |
|--------|---------|
| `LocalGraphStore.put_dht_node(node_id, host, port)` | Persist discovered node |
| `LocalGraphStore.get_dht_node(node_id)` | Retrieve by node_id |
| `LocalGraphStore.get_all_dht_nodes(limit=1000)` | Scan persisted nodes |
| `LocalGraphStore.count_dht_nodes()` | Count total persisted nodes |
| `LocalGraphStore.clear_dht_nodes()` | Clear on startup reset |

### Load/Save Lifecycle
1. **Startup:** `KademliaNode._load_routing_from_lmdb()` reads up to 1000 nodes
2. **Discovery:** `_update_routing()` fires `put_dht_node()` async
3. **Shutdown:** Graceful close via `KademliaNode.stop()`

---

## M1 Constraints Applied

| Constraint | Value | Location |
|------------|-------|----------|
| Bootstrap concurrency | `Semaphore(2)` | `kademlia_node.py:123` |
| Request concurrency | `Semaphore(50)` | `kademlia_node.py:124` |
| Request timeout | `5.0s` | `kademlia_node.py:125` |
| Probe duration | `120s` (MAX_DHT_PROBE_DURATION_S) | `kademlia_node.py:120` |
| Memory bounds | data_store max 10K, TTL 3600s | `kademlia_node.py:1-50` |

### Concurrency Control

```python
# M1: Acquire semaphore before network call (max 50 concurrent)
async def _query_peer(host: str, port: int):
    async with DHT_REQUEST_SEMAPHORE:
        # UDP socket operations with DHT_REQUEST_TIMEOUT_S timeout
```

---

## Bootstrap Nodes

| Node | Address | Port |
|------|---------|------|
| BitTorrent Mainline | `router.bittorrent.com` | 6881 |
| uTorrent | `router.utorrent.com` | 6881 |
| Transmission | `dht.transmissionbt.com` | 6881 |
| libtorrent | `dht.libtorrent.org` | 25401 |

---

## Known Limitations vs Full BEP-5 Spec

### Implemented
- ✅ FIND_NODE for routing table population
- ✅ GET_PEERS for peer discovery
- ✅ Bencode encoding/decoding
- ✅ Compact node info (26 bytes: 20 ID + 4 IP + 2 port)
- ✅ Peer address extraction from `values` field

### Not Implemented
- ❌ ANNOUNCE_PEER handling (optional in BEP-5)
- ❌ BEP-9 `ut_metadata` extension (torrent metadata download)
- ❌ BEP-10 extension protocol handshake for metadata
- ❌ Token validation for announce_peer

### Notes
- `metadata_fetcher.py` has partial BEP-9/BEP-10 implementation but not wired to crawl()
- DHT findings are ephemeral (NOT persisted to DuckDB) per invariant_7
- `DHT_PROMOTION_STATUS = "simulated_no_persist"` — not production-promoted

---

## Integration Points

| File | Method | Purpose |
|------|--------|---------|
| `probe_runner.py` | `_scan_dht(query)` | Entry point for DHT scan |
| `probe_runner.py` | `run_deep_probe()` | Orchestrates DHT alongside other probes |
| `kademlia_node.py` | `get_peers(info_hash)` | Query DHT for peers |
| `kademlia_node.py` | `crawl(keyword)` | Full crawl with metadata |
| `local_graph.py` | `LocalGraphStore` | LMDB persistence layer |

---

## Gate

```bash
export HLEDAC_ENABLE_DHT=1  # Enable real UDP DHT
```

When not set: `_scan_dht()` returns `[]` immediately (fail-soft).

---

## Test Coverage

| Test Class | Tests | Status |
|------------|-------|--------|
| TestDHTGate | 2 | ✅ PASS |
| TestDHTM1Constraints | 3 | ✅ PASS |
| TestDHTInfohashGeneration | 2 | ✅ PASS |
| TestDHTFindingsStructure | 2 | ✅ PASS |
| TestDHTFailSoft | 2 | ✅ PASS |
| TestDHTLocalGraphStore | 2 | ✅ PASS |
| TestDHTBencode | 2 | ✅ PASS |
| **Total** | **15** | **✅ ALL PASS** |

---

## Invariants (Enforced)

| ID | Invariant | Enforcement |
|----|-----------|-------------|
| 1 | DHT disabled returns [] | Gate check in `_scan_dht()` |
| 2 | Findings use source_type="dht_discovery" | CanonicalFinding construction |
| 3 | DHT findings NOT persisted | No call to `async_ingest_findings_batch()` |
| 4 | Max 50 concurrent requests | `DHT_REQUEST_SEMAPHORE(50)` |
| 5 | 5s timeout per request | `DHT_REQUEST_TIMEOUT_S` |
| 6 | All methods fail-soft | try/except everywhere |
| 7 | info_hash from SHA256(query) | `hashlib.sha256().hexdigest()[:40]` |
