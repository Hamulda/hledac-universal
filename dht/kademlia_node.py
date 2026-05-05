"""
Kademlia DHT Node pro distributed storage a lookup.

PROMOTION GATE — EXPERIMENTAL / SIMULATED / NOT PROMOTED
==========================================================
Kademlia-based distributed hash table node s BEP-9/BEP-10 extension support.

STATUS: EXPERIMENTAL / SIMULATED
  - crawl_dht_for_keyword(): "Simulovaný crawl" — reálný DHT vyžaduje BEP-10/BEP-9 implementaci
  - BEP-9 metadata extension (ut_metadata) NENÍ IMPLEMENTOVÁNA — pouze comments
  - Transport layer: register_handler / send_message API existuje, ale _transport je vždy None
  - find_value(): lokální data_store + simulované RPC — žádný reálný síťový provoz
  - BOOTSTRAP_PEERS: 4 public BT DHT routery, ale pouze socket.connect() test (ping bez Kademlia ping)

M1 8GB MEMORY CEILING:
  - data_store: OrderedDict, max 10_000 položek, TTL 3600s — BOUNDED ✓
  - routing_table: Dict[bucket_index → list of peers], k=20 peers per bucket
  - _pending_rpcs: Dict[rpc_id → Future], bounded on MAX_PENDING_RPCS (5000), TTL 60s
  - F185E: MAX_PENDING_RPCS hard cap + TTL eviction prevents unbounded growth
  - MAX_ITEM_BYTES = 256KB hard cap na store — BOUNDED ✓
  - Žádné MLX/alokace mimo síťové operace

ALLOWED PURPOSE: BT DHT crawler pro info_hash discovery
  - Primární use case: hledání torrent content přes DHT síť
  - NENÍ součástí OSINT canonical pipeline (web fetching, RSS, feed discovery)
  - Koreluje s blockchain_analyzer? NE — zcela nezávislé moduly

PROMOTION ELIGIBILITY: NO
  - SIMULATED label = not production-ready
  - Žádné production call sites (grep: 0 volání crawl_dht_for_keyword/lookup_info_hash_metadata)
  - Transport layer je stub — _transport je vždy None → _ping/_send_* jsou no-ops
  - BEP-9/BEP-10 neimplementováno = reálný BT content discovery nefunguje
  - Problém: autrual DHT crawler by generoval M1 síťovou stopu bez užitku pro OSINT

SECURITY: Žádná.
  - socket.AF_INET pouze (IPv4-only bootstrap)
  - Žádná autentifikace v DHT zprávách
STEALTH: Žádná.
  - DHT provoz je plně identifikovatelný jako BitTorrent traffic
  - Není to "stealth" — DHT routery vědí že jsme BT klient

DŮLEŽITÉ: Tento modul je paper-compliant Kademlia implementation,
ALE bez reálného síťového transportu je to pouze local DHT simulation.
"""

# =============================================================================
# DHT PROMOTION GATE — F206F
# =============================================================================
# Explicit promotion status for DHT module.
# DHT crawl returns data but MUST NEVER call async_ingest_findings_batch.
# This gate prevents simulated DHT from being confused with production OSINT.
DHT_PROMOTION_STATUS: str = "simulated_no_persist"


def is_dht_production_ready() -> bool:
    """
    Returns False — DHT is SIMULATED and must not persist findings.

    F206F: This gate exists to prevent accidental promotion of simulated
    DHT results to production OSINT sources. DHT crawl results are
    returned for potential future enrichment but are never written to
    DuckDB via async_ingest_findings_batch.

    Returns:
        False always — DHT is not production-ready for persistence.
    """
    return False


# =============================================================================
# END DHT PROMOTION GATE
# =============================================================================

import asyncio
import hashlib
import logging
import random
import socket
import time
import uuid
from collections import OrderedDict
from typing import Any, Dict, List, Optional

from hledac.universal.core.resource_governor import ResourceGovernor, Priority

logger = logging.getLogger(__name__)

MAX_ITEM_BYTES = 256 * 1024  # 256KB hard cap

# F185E: MAX_PENDING_RPCS — hard upper bound na pending RPC count
# TTL 60s — RPCs older than this are evicted on next cleanup
MAX_PENDING_RPCS = 5000
MAX_PENDING_RPC_TTL_S = 60.0

# Sprint 8VE A.2: Bootstrap peers for DHT crawl (IPv4-only)
BOOTSTRAP_PEERS = [
    ("router.bittorrent.com",  6881),
    ("dht.transmissionbt.com", 6881),
    ("router.utorrent.com",    6881),
    ("dht.libtorrent.org",    25401),
]


async def crawl_dht_for_keyword(
    keyword: str,
    duration_s: int = 120,
    max_results: int = 100,
    store_results: bool = True,
) -> list[dict]:
    """
    Pasivní DHT crawl — zachytí info_hashes cirkulující sítí.

    FÁZE P5: Přidán limit 50 souběžných dotazů a DuckDB storage.

    Implementační požadavky:
      1. Bootstrap přes BOOTSTRAP_PEERS s socket.AF_INET force
         (M1 preferuje IPv6, DHT sítě jsou primárně IPv4)
      2. BEP-9 metadata extension (ut_metadata) přes BEP-10
         Extension Protocol — pro každý zachycený info_hash:
           a) připoj se k peerům z announce_peer zpráv
           b) pošli extension handshake s ut_metadata podporou
           c) stáhni POUZE torrent metadata (název, file list, size)
           d) NESTAHUJ obsah torrentu
      3. Filtruj výsledky: keyword.lower() in name.lower()
      4. Respektuj duration_s — ukonči crawl po uplynutí času
      5. Používá KademliaNode pro routing table management
      6. MAX_CONCURRENT_QUERIES = 50 — bounded semaphore

    Vrací: [{"info_hash": str, "name": str, "files": list,
             "size_bytes": int, "peers": int, "source": "dht"}]
    """
    MAX_CONCURRENT_QUERIES = 50
    results: list[dict] = []
    start_time = time.monotonic()

    governor = ResourceGovernor()
    node = KademliaNode(
        node_id=f"hledac-crawl-{uuid.uuid4().hex[:8]}",
        governor=governor,
        bootstrap_nodes=[f"{h}:{p}" for h, p in BOOTSTRAP_PEERS],
    )

    duckdb_store = None
    # Sprint F192B: DHT crawl is EXPERIMENTAL/SIMULATED — write-side persistence
    # bypass removed. Findings from DHT are returned but NOT stored to DuckDBShadowStore.
    # They may be incorporated via the canonical sprint path if needed.

    try:
        for host, port in BOOTSTRAP_PEERS:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.settimeout(2.0)
                sock.connect((host, port))
                sock.close()
                logger.debug(f"[DHT] Bootstrap peer {host}:{port} reachable")
            except OSError as e:
                logger.debug(f"[DHT] Bootstrap peer {host}:{port} unreachable: {e}")

        keyword_lower = keyword.lower()
        searched_tokens: set[str] = set()
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_QUERIES)

        async def search_token(token: str) -> Optional[dict]:
            async with semaphore:
                dht_key = f"urn:btih:{hashlib.sha256(token.encode()).hexdigest()[:40]}"
                try:
                    value = await node.find_value(dht_key)
                    if value and isinstance(value, dict):
                        name = value.get("name", "")
                        if keyword_lower in name.lower():
                            return {
                                "info_hash": dht_key,
                                "name": name,
                                "files": value.get("files", []),
                                "size_bytes": value.get("size_bytes", 0),
                                "peers": value.get("peers", 0),
                                "source": "dht",
                            }
                except Exception:
                    pass
                return None

        while (time.monotonic() - start_time) < duration_s and len(results) < max_results:
            tokens = keyword_lower.split()
            new_tokens = [t for t in tokens if t not in searched_tokens]
            if not new_tokens:
                break

            for token in new_tokens:
                searched_tokens.add(token)

            tasks = [search_token(t) for t in new_tokens]
            found = await asyncio.gather(*tasks, return_exceptions=True)

            for item in found:
                if isinstance(item, dict) and item:
                    results.append(item)

            if not results:
                for key, (val, _ts) in list(node.data_store.items())[:50]:
                    if isinstance(val, dict) and "name" in val:
                        if keyword_lower in str(val.get("name", "")).lower():
                            results.append({
                                "info_hash": key,
                                "name": val.get("name", ""),
                                "files": val.get("files", []),
                                "size_bytes": val.get("size_bytes", 0),
                                "peers": val.get("peers", 0),
                                "source": "dht",
                            })
                            if len(results) >= max_results:
                                break

            await asyncio.sleep(0.5)

        # Sprint F192B: DHT findings are returned but NOT persisted to DuckDBShadowStore.
        # DHT crawl is EXPERIMENTAL/SIMULATED — persistence via canonical sprint path only.

    except Exception as e:
        logger.warning(f"[DHT] crawl error: {e}")
    finally:
        await node.stop()

    logger.info(f"[DHT] crawl '{keyword}': {len(results)} results in {time.monotonic() - start_time:.1f}s")
    return results[:max_results]


async def lookup_info_hash_metadata(
    info_hash: str,
    timeout_s: float = 15.0,
) -> dict:
    """
    Lookup konkrétního info_hash přes DHT get_peers + ut_metadata.
    Vrátí: {info_hash, name, files, size_bytes, peers, source}
    Prázdný dict při timeoutu nebo chybě (nikdy nevyhodí výjimku).
    """
    governor = ResourceGovernor()
    node = KademliaNode(
        node_id=f"hledac-lookup-{info_hash[:8]}",
        governor=governor,
    )

    try:
        # Použij existující find_value API
        value = await asyncio.wait_for(
            node.find_value(info_hash),
            timeout=timeout_s,
        )
        if value and isinstance(value, dict):
            return {
                "info_hash": info_hash,
                "name": value.get("name", ""),
                "files": value.get("files", []),
                "size_bytes": value.get("size_bytes", 0),
                "peers": value.get("peers", 0),
                "source": "dht",
            }
        return {}
    except (asyncio.TimeoutError, Exception):
        return {}
    finally:
        await node.stop()


class KademliaNode:
    def __init__(
        self,
        node_id: str,
        governor: ResourceGovernor,
        bootstrap_nodes: Optional[List[str]] = None,
        k: int = 20,
        alpha: int = 3,
    ):
        self.node_id = node_id
        self.governor = governor
        self.bootstrap_nodes = bootstrap_nodes or []
        self.k = k
        self.alpha = alpha

        self.routing_table: Dict[int, List[Dict[str, Any]]] = {}
        self.data_store: "OrderedDict[str, tuple[Any, float]]" = OrderedDict()
        self.data_store_max = 10_000
        self.data_store_ttl = 3600

        self._running = True
        self._refresh_task: Optional[asyncio.Task] = None
        self._transport = None

        self._pending_rpcs: Dict[str, asyncio.Future] = {}
        # F185E: track creation time for TTL-based eviction
        self._pending_rpcs_created: Dict[str, float] = {}

    def set_transport(self, transport):
        self._transport = transport
        transport.register_handler("dht_ping", self._handle_ping)
        transport.register_handler("dht_pong", self._handle_pong)
        transport.register_handler("dht_store", self._handle_store)
        transport.register_handler("dht_find_value", self._handle_find_value)
        transport.register_handler("dht_find_value_resp", self._handle_find_value_resp)

    async def start(self):
        self._refresh_task = asyncio.create_task(self._refresh_loop(), name="kademlia:refresh_loop")
        for peer in self.bootstrap_nodes:
            if peer == self.node_id:
                continue
            await self._ping(peer)

    async def stop(self):
        self._running = False
        if self._refresh_task:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass

    def _distance(self, key1: str, key2: str) -> int:
        h1 = int(hashlib.sha256(key1.encode()).hexdigest(), 16)
        h2 = int(hashlib.sha256(key2.encode()).hexdigest(), 16)
        return h1 ^ h2

    def _bucket_index(self, key: str) -> int:
        dist = self._distance(key, self.node_id)
        if dist == 0:
            return 0
        return min(dist.bit_length() - 1, 255)

    def _update_routing(self, peer_id: str, peer_info: Optional[Dict[str, Any]] = None):
        if peer_id == self.node_id:
            return
        peer_info = peer_info or {}
        b = self._bucket_index(peer_id)
        bucket = self.routing_table.setdefault(b, [])
        bucket = [p for p in bucket if p.get("id") != peer_id]
        bucket.append({"id": peer_id, **peer_info, "last_seen": time.time()})
        if len(bucket) > self.k:
            bucket = bucket[-self.k:]
        self.routing_table[b] = bucket

    def _find_closest_nodes(self, key: str, count: int) -> List[Dict[str, Any]]:
        candidates: List[Dict[str, Any]] = []
        b = self._bucket_index(key)
        for i in range(max(0, b - 5), min(256, b + 6)):
            candidates.extend(self.routing_table.get(i, []))
        candidates.sort(key=lambda n: self._distance(n["id"], key))
        return candidates[:count]

    def _local_put(self, key: str, value: Any):
        self.data_store[key] = (value, time.time())
        self.data_store.move_to_end(key)
        if len(self.data_store) > self.data_store_max:
            self.data_store.popitem(last=False)

    def _local_get(self, key: str) -> Optional[Any]:
        if key not in self.data_store:
            return None
        value, ts = self.data_store[key]
        if time.time() - ts > self.data_store_ttl:
            del self.data_store[key]
            return None
        self.data_store.move_to_end(key)
        return value

    # ---- F185E: pending RPC TTL eviction ----

    def _cleanup_pending_rpcs(self):
        """
        F185E: TTL + size-based cleanup for _pending_rpcs.

        Evicts:
        1. Completed or cancelled futures
        2. Entries older than MAX_PENDING_RPC_TTL_S
        3. If still over MAX_PENDING_RPCS, evicts oldest by creation time (FIFO)
        """
        now = time.time()
        # Remove done/cancelled and expired
        expired_rpc_ids = [
            rid for rid, fut in list(self._pending_rpcs.items())
            if fut.done() or fut.cancelled()
            or (rid in self._pending_rpcs_created and now - self._pending_rpcs_created[rid] > MAX_PENDING_RPC_TTL_S)
        ]
        for rid in expired_rpc_ids:
            self._pending_rpcs.pop(rid, None)
            self._pending_rpcs_created.pop(rid, None)

        # If still over limit, evict oldest by creation time (FIFO)
        if len(self._pending_rpcs) > MAX_PENDING_RPCS:
            excess = len(self._pending_rpcs) - MAX_PENDING_RPCS
            # Sort by creation time (oldest first)
            sorted_ids = sorted(self._pending_rpcs_created, key=lambda rid: self._pending_rpcs_created[rid])
            for rid in sorted_ids[:excess]:
                self._pending_rpcs.pop(rid, None)
                self._pending_rpcs_created.pop(rid, None)

    async def store(self, key: str, value: Any):
        self._local_put(key, value)

        closest = self._find_closest_nodes(key, self.k)
        tasks = [self._send_store(p["id"], key, value) for p in closest if p["id"] != self.node_id]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def find_value(self, key: str) -> Optional[Any]:
        self._cleanup_pending_rpcs()
        local = self._local_get(key)
        if local is not None:
            return local

        queried = set()
        shortlist = self._find_closest_nodes(key, self.alpha)

        while shortlist:
            rpc_ids: List[str] = []
            send_tasks: List[asyncio.Task] = []

            for peer in shortlist[: self.alpha]:
                pid = peer["id"]
                if pid in queried or pid == self.node_id:
                    continue
                queried.add(pid)

                rpc_id = str(uuid.uuid4())
                rpc_ids.append(rpc_id)
                fut = asyncio.get_running_loop().create_future()
                self._pending_rpcs[rpc_id] = fut
                self._pending_rpcs_created[rpc_id] = time.time()
                send_tasks.append(asyncio.create_task(self._send_find_value(pid, key, rpc_id), name=f"kademlia:send_find_value:{pid[:8]}"))

            if not rpc_ids:
                break

            # wait for responses (futures)
            futures = [self._pending_rpcs[rid] for rid in rpc_ids if rid in self._pending_rpcs]
            if not futures:
                break

            results = await asyncio.wait_for(
                asyncio.gather(*futures, return_exceptions=True),
                timeout=3.0,
            )
            # remove all rpcs
            for rid in rpc_ids:
                self._pending_rpcs.pop(rid, None)
                self._pending_rpcs_created.pop(rid, None)

            for res in results:
                if isinstance(res, BaseException):
                    continue
                if isinstance(res, dict) and "value" in res:
                    self._local_put(key, res["value"])
                    return res["value"]
                if isinstance(res, dict) and "nodes" in res:
                    for n in res["nodes"]:
                        if n.get("id") and n["id"] not in queried:
                            shortlist.append(n)

            shortlist.sort(key=lambda n: self._distance(n["id"], key))
            shortlist = shortlist[: self.k]

        return None

    async def _ping(self, peer_id: str) -> bool:
        if not self._transport:
            return False
        rpc_id = str(uuid.uuid4())
        fut = asyncio.get_running_loop().create_future()
        self._pending_rpcs[rpc_id] = fut
        self._pending_rpcs_created[rpc_id] = time.time()
        await self._transport.send_message(peer_id, "dht_ping", {"rpc_id": rpc_id}, "")
        try:
            ok = await asyncio.wait_for(fut, timeout=2.0)
            self._update_routing(peer_id)
            return bool(ok)
        except asyncio.TimeoutError:
            return False
        finally:
            self._pending_rpcs.pop(rpc_id, None)
            self._pending_rpcs_created.pop(rpc_id, None)

    async def _send_store(self, peer_id: str, key: str, value: Any):
        if not self._transport:
            return
        # hard cap (best-effort): odmítnout příliš velké hodnoty
        try:
            import orjson
            approx = len(orjson.dumps(value))
            if approx > MAX_ITEM_BYTES:
                logger.warning("DHT store skipped: value too large")
                return
        except Exception:
            pass

        await self._transport.send_message(peer_id, "dht_store", {"key": key, "value": value}, "")
        self._update_routing(peer_id)

    async def _send_find_value(self, peer_id: str, key: str, rpc_id: str):
        if not self._transport:
            return
        await self._transport.send_message(peer_id, "dht_find_value", {"key": key, "rpc_id": rpc_id}, "")
        self._update_routing(peer_id)

    # Handlers
    async def _handle_ping(self, data: Dict[str, Any]):
        sender = data.get("sender")
        payload = data.get("payload", {})
        rpc_id = payload.get("rpc_id")
        if sender and rpc_id and self._transport:
            self._update_routing(sender)
            await self._transport.send_message(sender, "dht_pong", {"rpc_id": rpc_id}, "")

    async def _handle_pong(self, data: Dict[str, Any]):
        sender = data.get("sender")
        payload = data.get("payload", {})
        rpc_id = payload.get("rpc_id")
        if sender:
            self._update_routing(sender)
        fut = self._pending_rpcs.get(rpc_id)
        if fut and not fut.done():
            fut.set_result(True)
            self._pending_rpcs_created.pop(rpc_id, None)

    async def _handle_store(self, data: Dict[str, Any]):
        sender = data.get("sender")
        payload = data.get("payload", {})
        if sender:
            self._update_routing(sender)
        key = payload.get("key")
        value = payload.get("value")
        if key is None:
            return
        self._local_put(key, value)

    async def _handle_find_value(self, data: Dict[str, Any]):
        sender = data.get("sender")
        payload = data.get("payload", {})
        key = payload.get("key")
        rpc_id = payload.get("rpc_id")
        if not (sender and key and rpc_id and self._transport):
            return

        self._update_routing(sender)

        value = self._local_get(key)
        if value is not None:
            await self._transport.send_message(sender, "dht_find_value_resp", {"rpc_id": rpc_id, "value": value}, "")
            return

        closest = self._find_closest_nodes(key, self.k)
        await self._transport.send_message(sender, "dht_find_value_resp", {"rpc_id": rpc_id, "nodes": closest}, "")

    async def _handle_find_value_resp(self, data: Dict[str, Any]):
        sender = data.get("sender")
        payload = data.get("payload", {})
        rpc_id = payload.get("rpc_id")
        if sender:
            self._update_routing(sender)
        fut = self._pending_rpcs.get(rpc_id)
        if fut and not fut.done():
            fut.set_result(payload)
            self._pending_rpcs_created.pop(rpc_id, None)

    async def _refresh_loop(self):
        while self._running:
            await asyncio.sleep(300)
            # F185E: periodic pending RPC cleanup
            self._cleanup_pending_rpcs()
            bucket_idx = random.randint(0, 255)
            bucket = list(self.routing_table.get(bucket_idx, []))
            for peer in bucket:
                pid = peer.get("id")
                if pid:
                    ok = await self._ping(pid)
                    if not ok:
                        self.routing_table[bucket_idx] = [p for p in self.routing_table.get(bucket_idx, []) if p.get("id") != pid]

    # -------------------------------------------------------------------------
    # P10: Real BEP-9/10 DHT Implementation
    # -------------------------------------------------------------------------

    async def crawl(self, keyword: str, duration_s: int = 120, max_results: int = 50) -> list[dict]:
        """
        P10: Real DHT crawl for keyword-based torrent discovery.

        Implements BEP-9 (Extension for Peers Exchange) and BEP-10 (Extension
        Protocol Handshake) for downloading torrent metadata.

        Flow:
          1. Bootstrap to DHT network via BOOTSTRAP_PEERS
          2. Generate info_hash candidates from keyword (BTIH hash)
          3. Send get_peers queries to DHT network
          4. Handle announce_peer responses (get peer info)
          5. Download metadata via ut_metadata extension (BEP-9)
          6. Filter results by keyword match
          7. Store to knowledge store and graph

        Args:
            keyword: Search keyword for torrent discovery
            duration_s: Maximum crawl duration in seconds
            max_results: Maximum number of results to return

        Returns:
            List of dicts with keys: info_hash, name, files, size_bytes, peers, source
        """
        results: list[dict] = []
        start_time = time.monotonic()
        seen_hashes: set[str] = set()

        # P10: Real UDP socket for DHT communication
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(2.0)
        sock.setblocking(False)
        loop = asyncio.get_running_loop()

        try:
            # Bootstrap: ping known peers and populate routing table
            for peer_host, peer_port in BOOTSTRAP_PEERS:
                try:
                    await self._dht_send_ping(sock, peer_host, peer_port)
                except Exception as e:
                    logger.debug(f"DHT bootstrap ping to {peer_host}:{peer_port} failed: {e}")

            # Generate info_hash candidates from keyword
            keyword_lower = keyword.lower()
            tokens = keyword_lower.split()
            info_hashes = []

            # Generate multiple hash variants for better coverage
            for token in tokens[:5]:  # Limit tokens
                hash_input = token.encode()
                btih_hash = hashlib.sha256(hash_input).hexdigest()[:40]
                info_hash = bytes.fromhex(btih_hash)
                info_hashes.append((token, info_hash))

            # Also try combined keyword
            combined = keyword_lower.replace(" ", "_")[:50]
            combined_hash = hashlib.sha256(combined.encode()).hexdigest()[:40]
            info_hashes.append((combined, bytes.fromhex(combined_hash)))

            # Send get_peers queries and collect responses
            while (time.monotonic() - start_time) < duration_s and len(results) < max_results:
                for token, info_hash in info_hashes:
                    if len(results) >= max_results:
                        break

                    # Query random peers for this info_hash
                    for _ in range(3):  # Multiple queries per hash
                        peer_host = random.choice([p[0] for p in BOOTSTRAP_PEERS])
                        peer_port = random.choice([p[1] for p in BOOTSTRAP_PEERS])
                        try:
                            peers_response = await self._dht_send_get_peers(
                                sock, peer_host, peer_port, info_hash
                            )
                            if peers_response:
                                await self._handle_get_peers_response(
                                    peers_response, info_hash, token, results, seen_hashes
                                )
                        except Exception as e:
                            logger.debug(f"get_peers query failed: {e}")

                        await asyncio.sleep(0.1)  # Rate limiting

                # Also refresh routing table from responses
                self._refresh_routing_from_results()

                await asyncio.sleep(1.0)

        finally:
            sock.close()

        # Store results to knowledge if available
        if results:
            try:
                await self._store_dht_results(keyword, results)
            except Exception as e:
                logger.debug(f"DHT results storage failed: {e}")

        elapsed = time.monotonic() - start_time
        logger.info(f"DHT crawl '{keyword}': {len(results)} results in {elapsed:.1f}s")
        return results[:max_results]

    async def _dht_send_ping(self, sock: socket.socket, host: str, port: int) -> Optional[dict]:
        """Send DHT ping and receive response."""
        # Bencode format for DHT messages
        ping_msg = {
            "t": "aa",
            "y": "q",
            "q": "ping",
            "a": {"id": self.node_id.encode()[:20].ljust(20, b'\x00')}
        }
        try:
            await asyncio.get_running_loop().sock_sendall(
                sock, self._bencode(ping_msg) + b"\n"
            )
            data = await asyncio.wait_for(
                asyncio.get_running_loop().sock_recv(sock, 65535),
                timeout=2.0
            )
            if data:
                return self._bdecode(data)
        except Exception:
            pass
        return None

    async def _dht_send_get_peers(
        self, sock: socket.socket, host: str, port: int, info_hash: bytes
    ) -> Optional[dict]:
        """Send get_peers query for info_hash."""
        msg = {
            "t": "bb",
            "y": "q",
            "q": "get_peers",
            "a": {
                "id": self.node_id.encode()[:20].ljust(20, b'\x00'),
                "info_hash": info_hash[:20].ljust(20, b'\x00'),
            }
        }
        try:
            await asyncio.get_running_loop().sock_sendall(
                sock, self._bencode(msg) + b"\n"
            )
            data = await asyncio.wait_for(
                asyncio.get_running_loop().sock_recv(sock, 65535),
                timeout=2.0
            )
            if data:
                return self._bdecode(data)
        except Exception:
            pass
        return None

    async def _handle_get_peers_response(
        self,
        response: dict,
        info_hash: bytes,
        keyword: str,
        results: list,
        seen_hashes: set,
    ):
        """Handle get_peers response and extract peer/torrent info."""
        try:
            r = response.get("r", {})
            if not r:
                return

            nodes = r.get("nodes", "")
            if nodes and len(nodes) >= 26:
                # Extract peer info from nodes field
                num_peers = len(nodes) // 26
                for i in range(num_peers):
                    node_data = nodes[i*26:(i+1)*26]
                    peer_id = node_data[:20]
                    peer_host = ".".join(str(b) for b in node_data[20:24])
                    peer_port = int.from_bytes(node_data[24:26], "big")

                    # Update routing table
                    self._update_routing(peer_id.hex(), {
                        "host": peer_host,
                        "port": peer_port,
                    })

            # Also check for values (peers list)
            values = r.get("values", [])
            if isinstance(values, list):
                for value in values[:5]:  # Limit peers per response
                    if len(value) == 6:
                        peer_host = ".".join(str(b) for b in value[:4])
                        peer_port = int.from_bytes(value[4:6], "big")

                        # We have a peer for this info_hash - try to get metadata
                        info_hash_str = info_hash.hex()
                        if info_hash_str not in seen_hashes:
                            seen_hashes.add(info_hash_str)
                            metadata = await self._fetch_torrent_metadata(
                                peer_host, peer_port, info_hash
                            )
                            if metadata and keyword.lower() in metadata.get("name", "").lower():
                                results.append({
                                    "info_hash": info_hash_str,
                                    "name": metadata.get("name", ""),
                                    "files": metadata.get("files", []),
                                    "size_bytes": metadata.get("length", 0),
                                    "peers": len(values),
                                    "source": "dht",
                                })
        except Exception as e:
            logger.debug(f"handle_get_peers_response failed: {e}")

    async def _fetch_torrent_metadata(
        self, peer_host: str, peer_port: int, info_hash: bytes
    ) -> Optional[dict]:
        """
        P10: Fetch torrent metadata from peer using BEP-9 (ut_metadata).

        Connects to peer via TCP and performs BitTorrent handshake + extension
        handshake to download metadata (info dict) without downloading content.
        """
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(peer_host, peer_port),
                timeout=5.0
            )

            # BitTorrent handshake
            protocol = b"BitTorrent protocol"
            handshake = (
                protocol +
                bytes(8) +  # reserved bytes (extensions)
                info_hash[:20].ljust(20, b'\x00') +  # info_hash
                self.node_id.encode()[:20].ljust(20, b'\x00')  # peer_id
            )
            writer.write(handshake)
            await writer.drain()

            # Read handshake response
            response = await asyncio.wait_for(reader.read(68), timeout=5.0)
            if len(response) < 68:
                writer.close()
                await writer.wait_closed()
                return None

            # Check extension support (byte 25 = 0x10)
            if response[25] & 0x10 == 0:
                writer.close()
                await writer.wait_closed()
                return None

            # Extension handshake (BEP-10)
            ext_handshake = {
                "m": {
                    "ut_metadata": 1,  # Metadata extension
                },
                "ut_metadata": 1,
            }
            ext_msg = self._build_ext_message(20, ext_handshake)  # msg_id 20 = handshake
            writer.write(ext_msg)
            await writer.drain()

            # Read extension handshake response
            ext_response = await asyncio.wait_for(reader.read(65535), timeout=5.0)
            if not ext_response:
                writer.close()
                await writer.wait_closed()
                return None

            # Request metadata pieces
            metadata_size = 0
            metadata_parts = {}
            piece_index = 0

            while True:
                # Request next piece
                request = {"msg_type": 2, "piece": piece_index}  # 2 = request
                req_msg = self._build_ext_message(3, request)  # msg_id 3 = ut_metadata
                writer.write(req_msg)
                await writer.drain()

                try:
                    data = await asyncio.wait_for(reader.read(65535), timeout=5.0)
                    if not data:
                        break

                    # Parse extension message
                    msg = self._parse_ext_message(data)
                    if msg and msg.get("msg_type") == 1:  # 1 = data
                        piece = msg.get("piece", 0)
                        total_size = msg.get("total_size", 0)
                        if total_size > 0 and metadata_size == 0:
                            metadata_size = total_size
                        if "metadata" in msg:
                            metadata_parts[piece] = msg["metadata"]

                        if len(metadata_parts) * 16384 >= metadata_size:
                            break
                except asyncio.TimeoutError:
                    break

                piece_index += 1
                if piece_index > 1000:  # Sanity limit
                    break

            writer.close()
            await writer.wait_closed()

            # Reassemble metadata
            if metadata_parts and metadata_size > 0:
                full_metadata = b"".join(
                    metadata_parts.get(i, b"") for i in range(len(metadata_parts))
                )
                return self._bdecode(full_metadata)

        except Exception as e:
            logger.debug(f"_fetch_torrent_metadata failed: {e}")
        return None

    def _build_ext_message(self, msg_id: int, payload: dict) -> bytes:
        """Build BEP-10 extension protocol message."""
        bencoded = self._bencode(payload)
        # Extension message format: length (4 bytes) + msg_id (1 byte) + payload
        length = len(bencoded) + 1
        return length.to_bytes(4, "big") + bytes([msg_id]) + bencoded

    def _parse_ext_message(self, data: bytes) -> Optional[dict]:
        """Parse BEP-10 extension protocol message."""
        try:
            if len(data) < 5:
                return None
            length = int.from_bytes(data[:4], "big")
            msg_id = data[4]
            payload = self._bdecode(data[5:5+length])
            return {"msg_id": msg_id, **payload}
        except Exception:
            return None

    def _bencode(self, obj: Any) -> bytes:
        """Simple bencode encoder for DHT messages."""
        if isinstance(obj, dict):
            items = []
            for k in sorted(obj.keys()):
                items.append(self._bencode(k))
                items.append(self._bencode(obj[k]))
            return b"d" + b"".join(items) + b"e"
        elif isinstance(obj, list):
            return b"l" + b"".join(self._bencode(i) for i in obj) + b"e"
        elif isinstance(obj, int):
            return f"i{obj}e".encode()
        elif isinstance(obj, bytes):
            return f"{len(obj)}:".encode() + obj
        elif isinstance(obj, str):
            return f"{len(obj.encode())}:".encode() + obj.encode()
        return b""

    def _bdecode(self, data: bytes) -> Any:
        """Simple bencode decoder for DHT responses."""
        try:
            return self._bdecode_recursive(data, 0)[0]
        except Exception:
            return {}

    def _bdecode_recursive(self, data: bytes, pos: int) -> tuple[Any, int]:
        """Recursive bencode decoder."""
        if pos >= len(data):
            return (None, pos)

        if data[pos:pos+1] == b"d":
            result = {}
            pos += 1
            while pos < len(data) and data[pos:pos+1] != b"e":
                key, pos = self._bdecode_recursive(data, pos)
                value, pos = self._bdecode_recursive(data, pos)
                if key is not None:
                    result[key] = value
            return (result, pos + 1)
        elif data[pos:pos+1] == b"l":
            result = []
            pos += 1
            while pos < len(data) and data[pos:pos+1] != b"e":
                item, pos = self._bdecode_recursive(data, pos)
                result.append(item)
            return (result, pos + 1)
        elif data[pos:pos+1] == b"i":
            pos += 1
            end = data.index(b"e", pos)
            return (int(data[pos:end]), end + 1)
        elif data[pos:pos+1].isdigit():
            colon = data.index(b":", pos)
            length = int(data[pos:colon])
            start = colon + 1
            return (data[start:start+length], start + length)
        return (None, pos + 1)

    async def _store_dht_results(self, keyword: str, results: list):
        """Sprint F192B: DHT crawl is EXPERIMENTAL — no longer persists findings.

        Findings from DHT crawl are returned to caller but NOT written to
        DuckDBShadowStore. Canonical sprint path handles persistence if needed.
        Kept as no-op to avoid breaking callers that reference this method.
        """
        # DHT crawl is EXPERIMENTAL/SIMULATED — write-side bypass removed.
        pass

    def _refresh_routing_from_results(self):
        """Refresh routing table - called periodically during crawl."""
        # This is handled by _update_routing calls in response handlers
        pass

