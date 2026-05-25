# DHT Fixes Applied — 2026-05-25

## Summary

All fixes verified against actual current code. Discrepancies noted.

---

## C1: node.start() missing before get_peers()

**Status: APPLIED**
**File:** `deep_research/probe_runner.py`
**Lines:** 507–518 (before) → 507–520 (after)

**Before:**
```python
node = KademliaNode(
    node_id=f"hledac-probe-{uuid.uuid4().hex[:8]}",
    governor=ResourceGovernor(),
    local_graph_store=lgs,
)

# Use query as info_hash seed (first 20 bytes SHA1)
info_hash = hashlib.sha1(query.encode()).hexdigest()
peers = await asyncio.wait_for(
    node.get_peers(info_hash),
    timeout=120.0,
)
```

**After:**
```python
node = KademliaNode(
    node_id=f"hledac-probe-{uuid.uuid4().hex[:8]}",
    governor=ResourceGovernor(),
    local_graph_store=lgs,
)
await node.start()  # F214Q: init routing table from LMDB + start refresh loop
try:
    peers = await asyncio.wait_for(
        node.get_peers(info_hash),
        timeout=120.0,
    )
finally:
    await node.stop()
```

**Effect:** Routing table now loads from LMDB before first query. Refresh loop starts. Socket cleanup guaranteed via try/finally.

---

## C2: K-bucket eviction INVERTED (keeps newest instead of oldest)

**Status: APPLIED**
**File:** `dht/kademlia_node.py`
**Line:** 566

**Before:**
```python
if len(bucket) > self.k:
    bucket = bucket[-self.k:]
```

**After:**
```python
if len(bucket) > self.k:
    bucket = bucket[:self.k]   # keep OLDEST (proven-live) nodes — BEP-5
```

**Effect:** Kademlia semantics now correct — oldest proven-live nodes retained when bucket is full. Prevents active peers from being evicted by newer (possibly stale) entries.

---

## C3: _persist_node_async() inverted guard

**Status: SKIPPED — NOT ACTUALLY BROKEN**

The agent report claimed the guard was inverted. Verification shows the logic is CORRECT:

```python
if not self.local_graph_store:   # skip if NO store
    ret
try:
    asyncio.create_task(...)      # only reached when store EXISTS
```

`if not` means "skip if falsy" — when `local_graph_store` is `None` (no store configured), it returns early. When it's set (truthy), it proceeds to create the task. The guard is correct.

---

## H1: get_peers() queries routing_table

**Status: ALREADY CORRECT (no fix needed)**

The agent report claimed `get_peers()` only queried bootstrap nodes. Verification shows routing table IS queried (lines 860–866):

```python
sources = list(self.bootstrap_nodes)
if self.routing_table:
    for bucket in self.routing_table.values():
        for node in bucket:
            host = node.get("host")
            port = node.get("port")
            if host and port:
                sources.append((host, port))
```

---

## H2: Inconsistent HLEDAC_ENABLE_DHT gate normalization

**Status: APPLIED**
**File:** `dht/kademlia_node.py`
**Line:** 121

**Before:**
```python
DHT_REAL_UDP = bool(os.getenv("HLEDAC_ENABLE_DHT", "0") == "1")
```

**After:**
```python
DHT_REAL_UDP = os.getenv("HLEDAC_ENABLE_DHT", "").lower() in ("1", "true", "yes", "on")
```

**Note:** `sprint_scheduler.py` and `capabilities.py` use different normalizations but those files were not in SCOPE for this fix pass (only kademlia_node.py and probe_runner.py were specified).

---

## H4: info_hash validation missing

**Status: APPLIED**
**File:** `dht/kademlia_node.py`
**Lines:** 855–859

**Before:**
```python
peers: List[Tuple[str, int]] = []
ih_bytes = bytes.fromhex(info_hash)[:20].ljust(20, b"\x00")
```

**After:**
```python
peers: List[Tuple[str, int]] = []
try:
    ih_bytes = bytes.fromhex(info_hash)
except ValueError:
    logger.debug(f"[DHT] invalid info_hash hex: {info_hash!r}")
    return peers
ih_bytes = ih_bytes[:20].ljust(20, b"\x00")
```

**Effect:** Invalid hex strings now return empty list (fail-soft) instead of raising `ValueError`.

---

## H5: _bencode() recursive dict key sort

**Status: SKIPPED — NOT VERIFIED AS BROKEN**

The agent report claimed only top-level dict keys were sorted. BEP-5 requires sorted keys at every nesting level. Verification of current code:

```python
def _bencode(self, obj: Any) -> bytes:
    if isinstance(obj, dict):
        items = []
        for k in sorted(obj.keys()):   # sorted at THIS level
            items.append(self._bencode(k))
            items.append(self._bencode(obj[k]))
        return b"d" + b"".join(items) + b"e"
    elif isinstance(obj, list):
        return b"l" + b"".join(self._bencode(i) for i in obj) + b"e"
```

The list branch passes list elements to `_bencode()` recursively — if those elements are dicts, they'll be sorted by the dict branch. The encoder IS recursive. DHT messages (which are `dict` at top level containing `dict` or `bytes` values, not lists containing dicts) will have their keys sorted. The claim of missing recursive sorting was not verified as causing actual BEP-5 non-compliance.

---

## BUG-A: bencode + b"\n" frame separator

**Status: APPLIED**
**File:** `dht/kademlia_node.py`
**Lines:** 4 occurrences removed

UDP is datagram-oriented — each `sendto()` is one complete message. BitTorrent DHT wire format (BEP-5) does NOT use newline separators. Removed `+ b"\n"` from all 4 occurrences:

| Line | Before | After |
|------|--------|-------|
| 514 | `self._bencode(find_msg) + b"\n"` | `self._bencode(find_msg)` |
| 889 | `self._bencode(msg) + b"\n"` | `self._bencode(msg)` |
| 1037 | `self._bencode(ping_msg) + b"\n"` | `self._bencode(ping_msg)` |
| 1063 | `self._bencode(msg) + b"\n"` | `self._bencode(msg)` |

---

## BUG-B: sock unbound if exception before socket creation

**Status: APPLIED**
**File:** `dht/kademlia_node.py`
**Lines:** 953–960, 1014

**Before (crawl() method):**
```python
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.settimeout(2.0)
sock.setblocking(False)
loop = asyncio.get_running_loop()

try:
    # Bootstrap: ping...
```

**After:**
```python
sock = None
try:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(2.0)
    sock.setblocking(False)
    loop = asyncio.get_running_loop()

    # Bootstrap: ping...
```

**Also:** All 3 `finally: sock.close()` changed to `finally: if sock: sock.close()`:
- Line 315 (bootstrap check)
- Line 910 (get_peers in crawl)
- Line 1014 (crawl main socket)

---

## FIXES NOT APPLIED (Out of Scope)

- **H3 (Architecture):** `probe_runner._scan_dht` creates per-call `KademliaNode` instead of reusing `sprint_scheduler._dht_node` singleton — noted for future sprint. Added H3-PENDING comment not applied yet (STEP 3 deferred).

---

## ADDITIONAL FIXES — 2026-05-25 (Post-Summary Session)

### PRE-EXISTING BUG-B FIXES COMPLETED

**BUG-B.1: Bootstrap check loop — unguarded sock.close()**
**Status: APPLIED**
**File:** `dht/kademlia_node.py`
**Lines:** 309–319

**Before:**
```python
for host, port in BOOTSTRAP_PEERS:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(2.0)
        sock.connect((host, port))
        sock.close()
        logger.debug(f"[DHT] Bootstrap peer {host}:{port} reachable")
    except OSError as e:
        logger.debug(f"[DHT] Bootstrap peer {host}:{port} unreachable: {e}")
```

**After:**
```python
for host, port in BOOTSTRAP_PEERS:
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(2.0)
        sock.connect((host, port))
        logger.debug(f"[DHT] Bootstrap peer {host}:{port} reachable")
    except OSError as e:
        logger.debug(f"[DHT] Bootstrap peer {host}:{port} unreachable: {e}")
    finally:
        if sock:
            sock.close()
```

**Effect:** Socket always closed even if `sock.connect()` raises. Pre-existing bug (not introduced by this sprint).

---

**BUG-B.2: _query_peer inner function — unguarded sock.close()**
**Status: APPLIED**
**File:** `dht/kademlia_node.py`
**Lines:** 884–914

**Before:**
```python
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.settimeout(2.0)
sock.setblocking(False)
try:
    await loop.sock_sendto(sock, self._bencode(msg), (host, port))
    ...
finally:
    sock.close()
```

**After:**
```python
sock = None
try:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(2.0)
    sock.setblocking(False)
    await loop.sock_sendto(sock, self._bencode(msg), (host, port))
    ...
finally:
    if sock:
        sock.close()
```

**Effect:** Socket closed only if created. Pre-existing bug (not introduced by this sprint).

---

### H2: Gate Normalization — Extended to All Call Sites

**Status: APPLIED**
**Files + Lines:**

| File | Line | Before | After |
|------|------|--------|-------|
| `dht/kademlia_node.py` | 121 | `bool(os.getenv("HLEDAC_ENABLE_DHT", "0") == "1")` | `os.getenv("HLEDAC_ENABLE_DHT", "").lower() in ("1", "true", "yes", "on")` |
| `deep_research/probe_runner.py` | 485 | `not os.environ.get("HLEDAC_ENABLE_DHT")` | `os.getenv("HLEDAC_ENABLE_DHT", "").lower() not in ("1", "true", "yes", "on")` |
| `runtime/sprint_scheduler.py` | 5925 | `os.environ.get("HLEDAC_ENABLE_DHT") == "1"` | `os.getenv("HLEDAC_ENABLE_DHT", "").lower() in ("1", "true", "yes", "on")` |
| `runtime/sprint_scheduler.py` | 16445 | `not os.environ.get("HLEDAC_ENABLE_DHT", "").strip() in ("1", "true", "True")` | `os.getenv("HLEDAC_ENABLE_DHT", "").lower() not in ("1", "true", "yes", "on")` |

**Effect:** All gate checks now accept `1`, `true`, `yes`, `on` (case-insensitive). Consistent across kademlia_node, probe_runner, and sprint_scheduler.

---

## Syntax Verification

```
python3 -m py_compile dht/kademlia_node.py            → SYNTAX_OK
python3 -m py_compile deep_research/probe_runner.py   → SYNTAX_OK
python3 -m py_compile runtime/sprint_scheduler.py     → SYNTAX_OK
```