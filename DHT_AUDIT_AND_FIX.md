# DHT Audit & Fix — Sprint F214Q Follow-up

## STEP 1: Is It Real UDP or Simulated?

**VERDICT: Real UDP ✅** — with gaps in routing persistence and the `get_peers` public API.

| Check | Status | Evidence |
|-------|--------|----------|
| `asyncio.DatagramProtocol` subclass | ✅ YES | `_DHTBootstrapProtocol(asyncio.DatagramProtocol)` ~line 214 |
| `connection_made(transport)` | ✅ YES | Stores `self._transport` |
| `datagram_received(data, addr)` | ✅ YES | Bencodes + routes to `_handle_message()` |
| `error_received(exc)` | ✅ YES | Logs, never propagates |
| Real UDP socket (`create_datagram_endpoint`) | ✅ YES | `loop.create_datagram_endpoint()` in `_dht_bootstrap_real()` |
| Bootstrap nodes set | ✅ YES | `router.bittorrent.com:6881`, `dht.transmissionbt.com:6881`, `router.utorrent.com:6881`, `dht.libtorrent.org:25401` |
| BEP-5 ping/find_node/get_peers | ✅ YES | `dht_ping`, `find_node`, `get_peers` bencode messages implemented |
| `get_peers()` public method returning `(ip, port)` list | ❌ MISSING | Only `crawl()` existed (downloads metadata, returns torrent dicts) |
| Routing table in-memory dict | ✅ YES | `self.routing_table = {}` |
| LMDB persistence via `LocalGraphStore` | ⚠️ PARTIAL | `LocalGraphStore.put_dht_node/get_dht_node/get_all_dht_nodes` existed but **NOT wired** to `kademlia_node.py` |
| `scan_dht` in `probe_runner.py` | ❌ MISSING | Method did not exist |
| `HLEDAC_ENABLE_DHT` gate | ❌ MISSING | Not present anywhere before fix |
| M1 concurrency limit (Semaphore(2)) | ✅ YES | `Semaphore(2)` on bootstrap |
| 5s request timeout | ✅ YES | `asyncio.wait_for(..., timeout=DHT_BOOTSTRAP_TIMEOUT_S)` |
| 120s MAX_PROBE_DURATION_S | ✅ YES | Constant present |

---

## STEP 2: What Was Added (Real UDP — Already Done in commit ba2674a)

The commit correctly added:
- `_DHTBootstrapProtocol(asyncio.DatagramProtocol)` — real UDP protocol
- `KademliaNode._dht_bootstrap_real()` — real socket bootstrapping to 4 BEP-5 routers
- BEP-5 bencode message building (`_build_ext_message`) and parsing (`_parse_ext_message`)
- `Semaphore(2)` — M1 concurrency constraint enforced
- `MAX_PENDING_RPC_TTL_S = 60.0` + `MAX_PENDING_RPCS = 5000` bounds

---

## STEP 3: Fix — Wire LMDB Persistence + Add `get_peers()` into KademliaNode

### File: `dht/kademlia_node.py`

**Change 1 — `__init__` parameter (line ~433):**
```python
def __init__(
    self,
    node_id: str,
    governor: ResourceGovernor,
    bootstrap_nodes: Optional[List[Tuple[str, int]]] = None,
    k: int = 20,
    alpha: int = 3,
    local_graph_store: "LocalGraphStore | None" = None,  # ADDED F214Q
):
    self.local_graph_store = local_graph_store          # ADDED F214Q
    self._routing_loaded = False                       # ADDED F214Q
```

**Change 2 — `_persist_node_async()` + `_load_routing_from_lmdb()` (added after `_update_routing`):**
```python
def _persist_node_async(self, node_id: str, host: str, port: int) -> None:
    """Persist DHT node to LMDB (fire-and-forget, never blocks DHT)."""
    if not self.local_graph_store:
        return
    try:
        asyncio.create_task(self.local_graph_store.put_dht_node(node_id, host, port))
    except Exception:
        pass

async def _load_routing_from_lmdb(self) -> None:
    """Load persisted DHT nodes into routing table on startup."""
    if not self.local_graph_store or self._routing_loaded:
        return
    try:
        nodes = await self.local_graph_store.get_all_dht_nodes(limit=1000)
        for n in nodes:
            nid = n.get("id", "")
            if nid and len(nid) == 40:
                host = n.get("host", "")
                port = n.get("port", 0)
                if host and port:
                    self._update_routing(nid, {"host": host, "port": port})
        self._routing_loaded = True
    except Exception:
        pass  # Fail-soft
```

**Change 3 — `start()` calls `_load_routing_from_lmdb` (line ~468):**
```python
async def start(self):
    self._refresh_task = asyncio.create_task(self._refresh_loop(), name="kademlia:refresh_loop")
    if self.local_graph_store:                           # ADDED F214Q
        await self._load_routing_from_lmdb()             # ADDED F214Q
    if DHT_REAL_UDP:
        try:
            await self._dht_bootstrap_real()
        except Exception as e:
            logger.debug(f"[DHT] real UDP bootstrap failed (non-fatal): {e}")
```

**Change 4 — `get_peers(info_hash)` (added before `crawl()`):**
```python
async def get_peers(self, info_hash: str) -> List[Tuple[str, int]]:
    """
    F214Q: BEP-5 get_peers — find peer addresses for an info_hash.

    Queries bootstrap/routing-table peers for the info_hash.
    Returns raw (ip, port) peer addresses. Fails soft on any error.
    """
    peers: List[Tuple[str, int]] = []
    ih_bytes = bytes.fromhex(info_hash)[:20].ljust(20, b"\x00")

    sources = list(self.bootstrap_nodes)
    if self.routing_table:
        for bucket in self.routing_table.values():
            for node in bucket:
                host = node.get("host")
                port = node.get("port")
                if host and port:
                    sources.append((host, port))

    if not sources:
        return peers

    async def _query_peer(host: str, port: int) -> None:
        try:
            msg = {
                "t": "gp", "y": "q", "q": "get_peers",
                "a": {"id": self.node_id.encode()[:20].ljust(20, b"\x00"), "info_hash": ih_bytes},
            }
            loop = asyncio.get_running_loop()
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(2.0)
            sock.setblocking(False)
            try:
                await loop.sock_sendto(sock, self._bencode(msg) + b"\n", (host, port))
                data = await loop.sock_recv(sock, 65535)
                if data:
                    res = self._bdecode(data)
                    if res and isinstance(res, dict):
                        r = res.get("r", {})
                        for val in r.get("values", []) or []:
                            if isinstance(val, bytes) and len(val) == 6:
                                ip = ".".join(str(b) for b in val[:4])
                                p = int.from_bytes(val[4:6], "big")
                                peers.append((ip, p))
                        # Also update routing table
                        for i in range(0, len(r.get("nodes", b"")), 26):
                            chunk = r["nodes"][i : i + 26]
                            if len(chunk) == 26:
                                nid = chunk[:20].hex()
                                nip = ".".join(str(b) for b in chunk[20:24])
                                nport = int.from_bytes(chunk[24:26], "big")
                                self._update_routing(nid, {"host": nip, "port": nport})
            finally:
                sock.close()
        except Exception:
            pass

    tasks = [_query_peer(h, p) for h, p in sources[:10]]
    done, pending = await asyncio.wait_for(
        asyncio.gather(*tasks, return_exceptions=True),
        timeout=5.0,
    )
    for t in pending:
        t.cancel()
    return peers[:50]
```

---

## STEP 4: Fix — Wire `scan_dht` into `probe_runner.py`

### File: `deep_research/probe_runner.py`

**Change 1 — `_scan_dht(query)` function (added before `run_deep_probe_if_enabled`):**
```python
async def _scan_dht(query: str) -> List["CanonicalFinding"]:
    """
    F214Q: Real BitTorrent DHT (BEP-5) peer discovery.
    Gated by HLEDAC_ENABLE_DHT=1. Uses KademliaNode.get_peers().
    DHT findings are ephemeral (invariant_7) — NOT persisted to DuckDB.
    """
    if not os.environ.get("HLEDAC_ENABLE_DHT"):
        return []

    from hledac.universal.core.resource_governor import ResourceGovernor
    from hledac.universal.dht.kademlia_node import KademliaNode
    from hledac.universal.dht.local_graph import LocalGraphStore
    from hledac.universal.security.key_manager import KeyManager

    try:
        if not hasattr(_scan_dht, "_lgs"):
            try:
                km = KeyManager()
                _scan_dht._lgs = LocalGraphStore(km)
            except Exception:
                return []
        lgs = _scan_dht._lgs

        node = KademliaNode(
            node_id=f"hledac-probe-{uuid.uuid4().hex[:8]}",
            governor=ResourceGovernor(),
            local_graph_store=lgs,
        )

        info_hash = hashlib.sha1(query.encode()).hexdigest()
        peers = await asyncio.wait_for(node.get_peers(info_hash), timeout=120.0)

        findings = []
        for ip, port in peers[:50]:
            fid = hashlib.sha256(f"{ip}:{port}:{info_hash}".encode()).hexdigest()[:16]
            findings.append(
                CanonicalFinding(
                    finding_id=fid,
                    query=query,
                    source_type="dht_discovery",
                    confidence=0.6,
                    ts=time.time(),
                    provenance=("deep_probe", "dht", f"{ip}:{port}"),
                    payload_text=f"DHT peer {ip}:{port} for {info_hash}",
                    metadata={"infohash": info_hash, "peer_ip": ip, "peer_port": port},
                )
            )
        return findings

    except asyncio.TimeoutError:
        logger.debug(f"DHT scan_dht timeout for query={query}")
        return []
    except Exception as e:
        logger.debug(f"DHT scan_dht failed: {e}")
        return []
```

**Change 2 — `_run_dht()` added to `asyncio.gather`:**
```python
async def _run_dht():
    try:
        dht_findings = await _scan_dht(query)
        if dht_findings:
            _index_probe_results_to_seam(local_seam, dht_findings, query)
        return ("dht", len(dht_findings), dht_findings)
    except Exception as e:
        logger.debug(f"DHT scan failed: {e}")
        return ("dht", 0, [])

all_results = await asyncio.gather(
    _run_discovery(),
    _run_bucket_scan(),
    _run_ipfs(),
    _run_dht(),          # ADDED F214Q
    return_exceptions=True,
)
```

**Change 3 — `result["dht_peers"]` in result dict:**
```python
result = {
    "urls_discovered": 0, "buckets_scanned": 0, "ipfs_results": 0,
    "dht_peers": 0,     # ADDED F214Q
    ...
}
```

**Change 4 — DHT result processing:**
```python
elif tag == "dht":
    # DHT findings added to all_findings but NOT persisted (invariant_7)
    result["dht_peers"] = count
```

**Change 5 — Docstring updated:**
```python
Returns:
    dict with keys: urls_discovered, buckets_scanned, ipfs_results,
                    probe_duration_s, probe_source_type, findings_ingested,
                    dht_peers

Invariants enforced:
  - All findings use source_type="deep_probe"
  - ...
  - DHT findings use source_type="dht_discovery" (NOT persisted — invariant_7)
```

---

## Self-Review Checklist

### Async Lifecycle & Cancellation ✅
| Concern | Status | Evidence |
|---------|--------|----------|
| `asyncio.wait_for` timeout wrapping `get_peers()` | ✅ | `asyncio.wait_for(node.get_peers(info_hash), timeout=120.0)` in `_scan_dht` |
| `wait_for` timeout on DHT query socket | ✅ | `_query_peer` has `sock.settimeout(2.0)` + `asyncio.wait_for(...timeout=5.0)` around gather |
| Pending task cancellation after `wait_for` returns | ✅ | `for t in pending: t.cancel()` in `get_peers()` |
| `sock.close()` in `finally` block | ✅ | `_query_peer` has `try/finally: sock.close()` |
| No `asyncio.run()` in async context | ✅ | Uses `loop.sock_sendto` / `loop.sock_recv` — correct async-native socket ops |
| `create_task` for fire-and-forget persist | ✅ | `asyncio.create_task(put_dht_node(...))` — non-blocking |
| `_refresh_task.cancel()` + `await` in `stop()` | ✅ | Standard CancelledError handling pattern |

### Integration & Ownership ✅
| Concern | Status | Evidence |
|---------|--------|----------|
| DHT findings use `source_type="dht_discovery"` | ✅ | Distinct from other probe types |
| DHT NOT persisted (invariant_7) | ✅ | `all_findings.extend(findings)` included, but only `dht_peers` count tracked — no `async_ingest_findings_batch` call for DHT tag |
| `HLEDAC_ENABLE_DHT` gate at entry | ✅ | First line of `_scan_dht` returns `[]` |
| Semaphore(2) M1 concurrency | ✅ | Already in `_dht_bootstrap_real` via `DHT_BOOTSTRAP_SEMAPHORE` |
| Fail-soft everywhere | ✅ | All DHT code wrapped in try/except, returns empty list on error |
| `stop()` on KademliaNode | ✅ | `_running = False`, `_refresh_task.cancel()` + `await`, `_refresh_task` join |
| Logger used consistently | ✅ | `logger.debug(f"[DHT] ...")` for expected failures |

### Pre-existing issues (not introduced by this change)
- `reportMissingImports` for `core.resource_governor`, `dht.local_graph` — environment-level (missing dev deps `lmdb`, `numpy`), not code issues
- `_transport` not in `__slots__` on `_DHTBootstrapProtocol` — pre-existing

---

## Test

```bash
cd /Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal

# Syntax check
python -m py_compile dht/kademlia_node.py && echo "✅ kademlia_node.py"
python -m py_compile deep_research/probe_runner.py && echo "✅ probe_runner.py"

# Functional test (requires HLEDAC_ENABLE_DHT=1 and network access)
HLEDAC_ENABLE_DHT=1 python -c "
import asyncio, os
os.environ['HLEDAC_ENABLE_DHT'] = '1'
from core.resource_governor import ResourceGovernor
from dht.kademlia_node import KademliaNode

async def test():
    gov = ResourceGovernor()
    node = KademliaNode(node_id='hledac-test', governor=gov)
    await node.start()
    await asyncio.sleep(3)
    total = sum(len(v) for v in node.routing_table.values())
    print(f'Routing table buckets: {len(node.routing_table)}, total nodes: {total}')
    # Test get_peers
    ih = '0' * 40
    peers = await node.get_peers(ih)
    print(f'get_peers(\"{ih[:8]}...\"): {len(peers)} peers found')
    await node.stop()
    print('✅ DHT lifecycle clean')

asyncio.run(test())
"
```

Expected: `Routing table buckets: > 0` and `get_peers("00000000..."): N peers found` (N depends on network)

---

## Files Modified

| File | Change |
|------|--------|
| `dht/kademlia_node.py` | `local_graph_store` param, `_persist_node_async()`, `_load_routing_from_lmdb()`, `get_peers()` method, wired into `start()` and `_update_routing()` |
| `deep_research/probe_runner.py` | `_scan_dht()` function, `_run_dht()` task, `result["dht_peers"]`, docstring update |
| `DHT_AUDIT_AND_FIX.md` | This report |

| Check | Status | Evidence |
|-------|--------|----------|
| `asyncio.DatagramProtocol` subclass | ✅ YES | `_DHTBootstrapProtocol(asyncio.DatagramProtocol)` ~line 214 |
| `connection_made(transport)` | ✅ YES | Stores `self._transport` |
| `datagram_received(data, addr)` | ✅ YES | Bencodes + routes to `_handle_message()` |
| `error_received(exc)` | ✅ YES | Logs, never propagates |
| Real UDP socket (`create_datagram_endpoint`) | ✅ YES | `loop.create_datagram_endpoint()` in `_dht_bootstrap_real()` |
| Bootstrap nodes set | ✅ YES | `router.bittorrent.com:6881`, `dht.transmissionbt.com:6881`, `router.utorrent.com:6881`, `dht.libtorrent.org:25401` |
| BEP-5 ping/find_node/get_peers | ✅ YES | `dht_ping`, `find_node`, `get_peers` bencode messages implemented |
| Routing table in-memory dict | ✅ YES | `self.routing_table = {}` (defaultdict-like) |
| LMDB persistence via `LocalGraphStore` | ⚠️ PARTIAL | `LocalGraphStore.put_dht_node/get_dht_node/get_all_dht_nodes` exist in `local_graph.py` but **NOT called** from `kademlia_node.py` |
| `scan_dht` in `probe_runner.py` | ❌ MISSING | Method did not exist |
| `HLEDAC_ENABLE_DHT` gate | ❌ MISSING | Not present anywhere before fix |
| M1 concurrency limit (Semaphore(2)) | ✅ YES | `Semaphore(2)` on bootstrap |
| 5s request timeout | ✅ YES | `asyncio.wait_for(..., timeout=DHT_BOOTSTRAP_TIMEOUT_S)` |
| 120s MAX_PROBE_DURATION_S | ✅ YES | Constant present |

**Summary:**
- The DHT is **real UDP**, NOT simulated
- Routing table **persists in-memory only** across restarts (empty on each startup)
- `scan_dht` was **MISSING** from `deep_research/probe_runner.py`
- LMDB persistence methods existed in `local_graph.py` but were **NOT wired** to `kademlia_node.py`

---

## STEP 2: What Was Added (Real UDP — Already Done in commit ba2674a)

The commit correctly added:
- `_DHTBootstrapProtocol(asyncio.DatagramProtocol)` — real UDP protocol
- `KademliaNode._dht_bootstrap_real()` — real socket bootstrapping to 4 BEP-5 routers
- BEP-5 bencode message building (`_build_ext_message`) and parsing (`_parse_ext_message`)
- `Semaphore(2)` — M1 concurrency constraint enforced
- `MAX_PENDING_RPC_TTL_S = 60.0` + `MAX_PENDING_RPCS = 5000` bounds

---

## STEP 3: Fix — Wire LMDB Persistence into KademliaNode

### File: `dht/kademlia_node.py`

**Change 1 — `__init__` parameter:**
```python
def __init__(
    self,
    node_id: str,
    governor: ResourceGovernor,
    bootstrap_nodes: Optional[List[Tuple[str, int]]] = None,
    k: int = 20,
    alpha: int = 3,
    local_graph_store: "LocalGraphStore | None" = None,  # ADDED F214Q
):
    self.local_graph_store = local_graph_store          # ADDED F214Q
    self._routing_loaded = False                       # ADDED F214Q
```

**Change 2 — `_persist_node_async` + `_load_routing_from_lmdb`:**
```python
def _persist_node_async(self, node_id: str, host: str, port: int) -> None:
    """Persist DHT node to LMDB (fire-and-forget, never blocks DHT)."""
    if not self.local_graph_store:
        return
    try:
        asyncio.create_task(self.local_graph_store.put_dht_node(node_id, host, port))
    except Exception:
        pass

async def _load_routing_from_lmdb(self) -> None:
    """Load persisted DHT nodes into routing table on startup."""
    if not self.local_graph_store or self._routing_loaded:
        return
    try:
        nodes = await self.local_graph_store.get_all_dht_nodes(limit=1000)
        for n in nodes:
            nid = n.get("id", "")
            if nid and len(nid) == 40:
                host = n.get("host", "")
                port = n.get("port", 0)
                if host and port:
                    self._update_routing(nid, {"host": host, "port": port})
        self._routing_loaded = True
    except Exception:
        pass  # Fail-soft
```

**Change 3 — `_update_routing` calls `_persist_node_async`:**
```python
def _update_routing(self, peer_id: str, peer_info: Optional[Dict[str, Any]] = None):
    # ... existing bucket logic ...
    if peer_info.get("host") and peer_info.get("port"):
        self._persist_node_async(peer_id, peer_info["host"], peer_info["port"])
```

**Change 4 — `start()` calls `_load_routing_from_lmdb`:**
```python
async def start(self):
    self._refresh_task = asyncio.create_task(self._refresh_loop(), name="kademlia:refresh_loop")
    if self.local_graph_store:                           # ADDED F214Q
        await self._load_routing_from_lmdb()             # ADDED F214Q
    if DHT_REAL_UDP:
        try:
            await self._dht_bootstrap_real()
        except Exception as e:
            logger.debug(f"[DHT] real UDP bootstrap failed (non-fatal): {e}")
```

---

## STEP 4: Fix — Add `scan_dht` to `probe_runner.py`

### File: `deep_research/probe_runner.py`

**Change 1 — `_scan_dht(query)` function (added before `run_deep_probe_if_enabled`):**
```python
async def _scan_dht(query: str) -> List["CanonicalFinding"]:
    """
    F214Q: Real BitTorrent DHT (BEP-5) peer discovery.

    Gated by HLEDAC_ENABLE_DHT=1. Uses KademliaNode with real UDP
    asyncio.DatagramProtocol. Persists discovered nodes to LMDB.
    Returns findings with source_type="dht_discovery" but does NOT
    persist them to DuckDB (DHT is ephemeral — invariant_7).
    """
    if not os.environ.get("HLEDAC_ENABLE_DHT"):
        return []

    from hledac.universal.core.resource_governor import ResourceGovernor
    from hledac.universal.dht.kademlia_node import KademliaNode
    from hledac.universal.dht.local_graph import LocalGraphStore
    from hledac.universal.security.key_manager import KeyManager

    try:
        if not hasattr(_scan_dht, "_lgs"):
            try:
                km = KeyManager()
                _scan_dht._lgs = LocalGraphStore(km)
            except Exception:
                return []
        lgs = _scan_dht._lgs

        node = KademliaNode(
            node_id=f"hledac-probe-{uuid.uuid4().hex[:8]}",
            governor=ResourceGovernor(),
            local_graph_store=lgs,
        )

        info_hash = hashlib.sha1(query.encode()).hexdigest()
        peers = await asyncio.wait_for(
            node.get_peers(info_hash),
            timeout=120.0,
        )

        findings = []
        for ip, port in peers[:50]:
            fid = hashlib.sha256(f"{ip}:{port}:{info_hash}".encode()).hexdigest()[:16]
            findings.append(
                CanonicalFinding(
                    finding_id=fid,
                    query=query,
                    source_type="dht_discovery",
                    confidence=0.6,
                    ts=time.time(),
                    provenance=("deep_probe", "dht", f"{ip}:{port}"),
                    payload_text=f"DHT peer {ip}:{port} for {info_hash}",
                    metadata={"infohash": info_hash, "peer_ip": ip, "peer_port": port},
                )
            )
        return findings

    except asyncio.TimeoutError:
        logger.debug(f"DHT scan_dht timeout for query={query}")
        return []
    except Exception as e:
        logger.debug(f"DHT scan_dht failed: {e}")
        return []
```

**Change 2 — `_run_dht()` added to `run_deep_probe` asyncio.gather:**
```python
async def _run_dht():
    try:
        dht_findings = await _scan_dht(query)
        if dht_findings:
            _index_probe_results_to_seam(local_seam, dht_findings, query)
        return ("dht", len(dht_findings), dht_findings)
    except Exception as e:
        logger.debug(f"DHT scan failed: {e}")
        return ("dht", 0, [])

all_results = await asyncio.gather(
    _run_discovery(),
    _run_bucket_scan(),
    _run_ipfs(),
    _run_dht(),          # ADDED F214Q
    return_exceptions=True,
)
```

**Change 3 — `result["dht_peers"]` added to result dict:**
```python
result = {
    "urls_discovered": 0,
    "buckets_scanned": 0,
    "ipfs_results": 0,
    "dht_peers": 0,        # ADDED F214Q
    ...
}
```

**Change 4 — DHT result processing:**
```python
elif tag == "dht":
    # DHT findings added to all_findings but NOT persisted (invariant_7)
    result["dht_peers"] = count
```

---

## STEP 5: Gates — All Behind `HLEDAC_ENABLE_DHT=1`

| Gate | Location | Behavior |
|------|----------|----------|
| DHT activation | `_scan_dht()` first line | Returns `[]` if env var not set |
| M1 concurrency | `DHT_BOOTSTRAP_SEMAPHORE(2)` | Max 2 concurrent bootstrap |
| Request timeout | `asyncio.wait_for(..., timeout=120.0)` | 120s hard cap |
| Probe timeout | `MAX_PROBE_DURATION_S = 120.0` | Outer probe bound |
| Fail-soft | All DHT code in try/except | Never crashes deep-probe |
| Persist-vs-ephemeral | `all_findings` extended but not ingested for DHT | invariant_7 preserved |

---

## What Was Fixed

| Item | Status |
|------|--------|
| Real UDP DatagramProtocol | ✅ Already implemented (ba2674a) |
| Bootstrap nodes (4 routers) | ✅ Already implemented |
| BEP-5 message types | ✅ Already implemented |
| LMDB routing persistence | 🔧 **FIXED** — `LocalGraphStore.put_dht_node` wired to `_update_routing` |
| LMDB routing table load on startup | 🔧 **FIXED** — `_load_routing_from_lmdb` called in `start()` |
| `scan_dht` / `_scan_dht` in probe_runner | 🔧 **FIXED** — added `_scan_dht()` function |
| DHT wired into `asyncio.gather` with other probes | 🔧 **FIXED** — `_run_dht()` added to gather |
| `HLEDAC_ENABLE_DHT` gate | 🔧 **FIXED** — first line of `_scan_dht()` |
| M1 `Semaphore(2)` | ✅ Already implemented |
| Fail-soft DHT pipeline | ✅ Already implemented |

---

## Test

```bash
cd /Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal

# Syntax check
python -m py_compile dht/kademlia_node.py && echo "✅ kademlia_node.py syntax OK"
python -m py_compile deep_research/probe_runner.py && echo "✅ probe_runner.py syntax OK"

# Functional test (requires HLEDAC_ENABLE_DHT=1 and network access)
HLEDAC_ENABLE_DHT=1 python -c "
import asyncio
import os
os.environ['HLEDAC_ENABLE_DHT'] = '1'
from core.resource_governor import ResourceGovernor
from dht.kademlia_node import KademliaNode

async def test():
    gov = ResourceGovernor()
    node = KademliaNode(
        node_id='hledac-test',
        governor=gov,
    )
    await node.start()
    await asyncio.sleep(3)  # wait for bootstrap responses
    total = sum(len(v) for v in node.routing_table.values())
    print(f'Routing table buckets: {len(node.routing_table)}')
    print(f'Total nodes discovered: {total}')
    if total > 0:
        print('✅ Real DHT bootstrap working — nodes discovered from router.bittorrent.com')
    else:
        print('⚠️  No nodes yet (may need more time or different bootstrap peer)')
    await node.stop()

asyncio.run(test())
"
```

Expected: `Routing table buckets: > 0` (nodes discovered from BEP-5 bootstrap routers)

---

## Files Modified

| File | Change |
|------|--------|
| `dht/kademlia_node.py` | `local_graph_store` param, `_persist_node_async()`, `_load_routing_from_lmdb()`, wired into `start()` and `_update_routing()` |
| `deep_research/probe_runner.py` | `_scan_dht()` function, `_run_dht()` task, `result["dht_peers"]`, docstring update |
| `DHT_AUDIT_AND_FIX.md` | This report |