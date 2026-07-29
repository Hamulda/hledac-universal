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
  - routing_table: dict[bucket_index → list of peers], k=20 peers per bucket
  - _pending_rpcs: dict[rpc_id → Future], bounded on MAX_PENDING_RPCS (5000), TTL 60s
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
DHT_PROMOTION_STATUS: str = 'simulated_no_persist'

def is_dht_production_ready() -> bool:
    """
    Returns DHT_REAL_UDP — real UDP DHT is production-ready for persistence.
    Simulated DHT (HLEDAC_ENABLE_DHT != "1") is NOT production-ready.

    F206F: This gate prevents accidental promotion of simulated DHT
    results to production OSINT sources.

    Returns:
        DHT_REAL_UDP — True only when HLEDAC_ENABLE_DHT=1 (real UDP active).
    """
    return DHT_REAL_UDP
import asyncio
import hashlib
import logging
import os
import secrets
import socket
import time
import uuid
from collections import OrderedDict
from typing import Any
from hledac.universal.core.resource_governor import ResourceGovernor
from hledac.universal.dht.local_graph import LocalGraphStore
from hledac.universal.utils.async_helpers import parallel_ok, safe_gather_fire_and_forget, safe_wait_for
logger = logging.getLogger(__name__)
_RNG = secrets.SystemRandom()
MAX_ITEM_BYTES = 256 * 1024
MAX_PENDING_RPCS = 5000
MAX_PENDING_RPC_TTL_S = 60.0
MAXCRAWLDEPTH = 3
DHT_SNAPSHOT_EVERY_N = 50
DHT_SNAPSHOT_KEY = b'routing_table_v1'
DHT_REAL_UDP = os.getenv('HLEDAC_ENABLE_DHT', '').lower() in ('1', 'true', 'yes', 'on')
MAX_DHT_PROBE_DURATION_S = 120
DHT_BOOTSTRAP_TIMEOUT_S = 8.0
from hledac.universal.core.concurrency_registry import ConcurrencyCategory, get_semaphore_for_testing
DHT_BOOTSTRAP_SEMAPHORE = get_semaphore_for_testing(ConcurrencyCategory.DHT_BOOTSTRAP)
DHT_REQUEST_SEMAPHORE = get_semaphore_for_testing(ConcurrencyCategory.DHT_REQUEST)
DHT_REQUEST_TIMEOUT_S = 5.0
if DHT_REAL_UDP:
    STATUS_LINE = 'STATUS: REAL UDP DHT — HLEDAC_ENABLE_DHT=1 ACTIVE'
else:
    STATUS_LINE = 'STATUS: DHT SIMULATED — set HLEDAC_ENABLE_DHT=1 to activate real UDP'
DHT_BOOTSTRAP_PEERS = [('router.bittorrent.com', 6881), ('dht.transmissionbt.com', 6881), ('router.utorrent.com', 6881), ('dht.libtorrent.org', 25401)]
BOOTSTRAP_PEERS = DHT_BOOTSTRAP_PEERS

class _DHTBootstrapProtocol(asyncio.DatagramProtocol):
    """
    F214: asyncio.DatagramProtocol for real BitTorrent DHT (BEP-5) bootstrapping.

    Handles FIND_NODE responses from bootstrap peers to populate the routing table.
    Not connection-oriented — stateless query/response over UDP.

    M1 Constraints applied:
      - 5s timeout per request (handled by caller via wait_for)
      - 3s collection window after sending
      - Fail-soft: no exceptions propagated to caller
    """
    __slots__ = ('_loop', '_node_id', '_nodes_found', '_error', '_transport')

    def __init__(self, loop, node_id: str):
        self._loop = loop
        self._node_id = node_id
        self._nodes_found: dict[str, dict[str, Any]] = {}
        self._error: Exception | None = None
        self._transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:
        self._transport = transport

    def send(self, data: bytes, addr: tuple[str, int]) -> tuple:
        """Send datagram. Call via asyncio.wait_for for timeout."""
        if self._transport:
            self._transport.sendto(data, addr)
        return (self._transport, None)

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        """Parse DHT FIND_NODE response, extract compact node info."""
        try:
            msg = self._bdecode(data)
            if not msg or not isinstance(msg, dict):
                return
            r = msg.get('r', {})
            if not isinstance(r, dict):
                return
            node_id = r.get('id', b'').hex() if isinstance(r.get('id'), bytes) else ''
            if not node_id or len(node_id) != 40:
                return
            compact = r.get('nodes', b'')
            if not compact or len(compact) < 26:
                return
            for i in range(0, len(compact) - 25, 26):
                chunk = compact[i:i + 26]
                if len(chunk) < 26:
                    break
                nid = chunk[:20]
                ip_bytes = chunk[20:24]
                raw_port = chunk[24:26]
                ip = '.'.join((str(b) for b in ip_bytes))
                port = int.from_bytes(raw_port, 'big')
                self._nodes_found[nid.hex()] = {'id': nid.hex(), 'host': ip, 'port': port}
        except Exception as e:
            logger.debug(f'[DHT] node parse failed: {e}')

    def error_received(self, exc: Exception) -> None:
        self._error = exc

    @staticmethod
    def _bdecode(data: bytes) -> dict[str, Any] | None:
        """Minimal bencode decoder for DHT responses."""
        return safe_bdecode(data)
MAX_PENDING_FUTURES = 5000

class BEP5UDPProtocol(asyncio.DatagramProtocol):
    """
    F214: Real BEP-5 asyncio.DatagramProtocol with future-based pending map.
    D-23 FIX: _pending dict now bounded via TTL cleanup + size cap.

    Bound to local UDP socket on construction. Caller invokes send_and_wait()
    which encodes a bencode message, registers a Future keyed by transaction id,
    and awaits response matched by transaction id. Datagram responses with
    unknown tids are silently dropped (malformed packets).
    """
    __slots__ = ('_handler', '_transport', '_pending', '_pending_created', '_loop')

    def __init__(self, message_handler):
        self._handler = message_handler
        self._transport: asyncio.DatagramTransport | None = None
        self._pending: dict[bytes, asyncio.Future] = {}
        self._pending_created: dict[bytes, float] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:
        self._transport = transport
        self._loop = asyncio.get_running_loop()

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        try:
            msg = bdecode(data)
            tid = msg.get(b't') if isinstance(msg, dict) else None
            if tid and tid in self._pending:
                fut = self._pending.pop(tid)
                self._pending_created.pop(tid, None)
                if not fut.done():
                    fut.set_result((msg, addr))
        except Exception:
            pass

    def error_received(self, exc: Exception) -> None:
        logger.debug(f'[DHT] UDP transport error: {exc}')

    def _cleanup_pending(self) -> None:
        """
        D-23: TTL + size-based cleanup for _pending dict.

        Evicts:
          1. Already-done / cancelled futures
          2. Entries older than DHT_REQUEST_TIMEOUT_S (5s TTL)
          3. If still over MAX_PENDING_FUTURES, evicts oldest by creation time (FIFO)
        """
        now = time.time()
        expired_tids = [tid for tid, ts in list(self._pending_created.items()) if tid not in self._pending or self._pending[tid].done() or self._pending[tid].cancelled() or (now - ts > DHT_REQUEST_TIMEOUT_S)]
        for tid in expired_tids:
            self._pending.pop(tid, None)
            self._pending_created.pop(tid, None)
        if len(self._pending) > MAX_PENDING_FUTURES:
            excess = len(self._pending) - MAX_PENDING_FUTURES
            sorted_tids = sorted(self._pending_created, key=lambda t: self._pending_created[t])
            for tid in sorted_tids[:excess]:
                self._pending.pop(tid, None)
                self._pending_created.pop(tid, None)

    async def send_and_wait(self, addr: tuple[str, int], msg_dict: dict, timeout: float=5.0) -> tuple[dict, tuple] | None:
        """
        Bencode msg_dict, send via UDP, await response matched by transaction id.

        Returns:
            (decoded_response_dict, source_addr) on success, or None on timeout.
        """
        self._cleanup_pending()
        tid = os.urandom(4)
        msg_dict[b't'] = tid
        data = bencode(msg_dict)
        loop = self._loop or asyncio.get_running_loop()
        fut = loop.create_future()
        self._pending[tid] = fut
        self._pending_created[tid] = time.time()
        try:
            if self._transport:
                self._transport.sendto(data, addr)
            async with asyncio.timeout(timeout):
                return await fut
        except TimeoutError:
            return None
        finally:
            self._pending.pop(tid, None)
            self._pending_created.pop(tid, None)

def bencode(obj: Any) -> bytes:
    """
    Standard BitTorrent bencode encoder (BEP-3).

    Dicts MUST have bytes keys (BEP-5 requirement). Strings and bytes are
    encoded as byte strings.
    """
    if isinstance(obj, dict):
        items = []
        for k, v in obj.items():
            if isinstance(k, str):
                k = k.encode('utf-8')
            items.append(bencode(k))
            items.append(bencode(v))
        return b'd' + b''.join(items) + b'e'
    if isinstance(obj, list):
        return b'l' + b''.join((bencode(i) for i in obj)) + b'e'
    if isinstance(obj, bool):
        return f'i{(1 if obj else 0)}e'.encode()
    if isinstance(obj, int):
        return f'i{obj}e'.encode()
    if isinstance(obj, (bytes, bytearray)):
        return f'{len(obj)}:'.encode() + bytes(obj)
    if isinstance(obj, str):
        b = obj.encode('utf-8')
        return f'{len(b)}:'.encode() + b
    raise TypeError(f'bencode: unsupported type {type(obj).__name__}')

def bdecode(data: bytes) -> Any:
    """
    Standard BitTorrent bencode decoder (BEP-3).
    """

    def _rec(d: bytes, p: int) -> tuple[Any, int]:
        if p >= len(d):
            raise ValueError('bdecode: unexpected end of data')
        ch = d[p:p + 1]
        if ch == b'd':
            res: dict = {}
            p += 1
            while p < len(d) and d[p:p + 1] != b'e':
                k, p = _rec(d, p)
                v, p = _rec(d, p)
                res[k] = v
            return (res, p + 1)
        if ch == b'l':
            lst: list = []
            p += 1
            while p < len(d) and d[p:p + 1] != b'e':
                itm, p = _rec(d, p)
                lst.append(itm)
            return (lst, p + 1)
        if ch == b'i':
            p += 1
            end = d.index(b'e', p)
            return (int(d[p:end]), end + 1)
        if ch.isdigit():
            colon = d.index(b':', p)
            ln = int(d[p:colon])
            start = colon + 1
            return (d[start:start + ln], start + ln)
        raise ValueError(f'bdecode: unexpected byte {ch!r} at {p}')
    result, _ = _rec(data, 0)
    return result

def safe_bdecode(data: bytes) -> dict[str, Any] | None:
    """
    Single safe bencode decoder — wraps bdecode for protocol loop safety.

    Replaces the former _bdecode_fixed. Used in datagram_received paths
    where one malformed packet must never crash the protocol loop.

    Returns None on any decoding error (ValueError, IndexError, KeyError).
    Only returns a dict; lists/scalars are filtered out as invalid for
    DHT message payloads.
    """
    try:
        result = bdecode(data)
        return result if isinstance(result, dict) else None
    except (ValueError, IndexError, KeyError):
        return None

async def crawl_dht_for_keyword(keyword: str, duration_s: int=120, max_results: int=100) -> list[dict]:
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
    node = KademliaNode(node_id=f'hledac-crawl-{uuid.uuid4().hex[:8]}', governor=governor, bootstrap_nodes=BOOTSTRAP_PEERS)
    try:
        loop = asyncio.get_running_loop()
        for host, port in BOOTSTRAP_PEERS:
            sock = None
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.setblocking(False)
                await safe_wait_for(loop.sock_connect(sock, (host, port)), timeout=2.0, label='dht_bootstrap')
                logger.debug(f'[DHT] Bootstrap peer {host}:{port} reachable')
            except (TimeoutError, OSError) as e:
                logger.debug(f'[DHT] Bootstrap peer {host}:{port} unreachable: {e}')
            finally:
                if sock:
                    sock.close()
        keyword_lower = keyword.lower()
        searched_tokens: set[str] = set()
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_QUERIES)

        async def search_token(token: str) -> dict | None:
            async with semaphore:
                dht_key = f'urn:btih:{hashlib.sha256(token.encode()).hexdigest()[:40]}'
                try:
                    value = await node.find_value(dht_key)
                    if value and isinstance(value, dict):
                        name = value.get('name', '')
                        if keyword_lower in name.lower():
                            return {'info_hash': dht_key, 'name': name, 'files': value.get('files', []), 'size_bytes': value.get('size_bytes', 0), 'peers': value.get('peers', 0), 'source': 'dht'}
                except Exception as e:
                    logger.debug(f'[DHT] result collection failed: {e}')
                return None
        while time.monotonic() - start_time < duration_s and len(results) < max_results:
            tokens = keyword_lower.split()
            new_tokens = [t for t in tokens if t not in searched_tokens]
            if not new_tokens:
                break
            for token in new_tokens:
                searched_tokens.add(token)
            tasks = [search_token(t) for t in new_tokens]
            found = await parallel_ok(*tasks, label='kademlia_node:506')
            for item in found:
                if isinstance(item, dict) and item:
                    results.append(item)
            if not results:
                for key, (val, _ts) in list(node.data_store.items())[:50]:
                    if isinstance(val, dict) and 'name' in val:
                        if keyword_lower in str(val.get('name', '')).lower():
                            results.append({'info_hash': key, 'name': val.get('name', ''), 'files': val.get('files', []), 'size_bytes': val.get('size_bytes', 0), 'peers': val.get('peers', 0), 'source': 'dht'})
                            if len(results) >= max_results:
                                break
            await asyncio.sleep(0.5)
    except Exception as e:
        logger.warning(f'[DHT] crawl error: {e}')
    finally:
        await node.stop()
    logger.info(f"[DHT] crawl '{keyword}': {len(results)} results in {time.monotonic() - start_time:.1f}s")
    return results[:max_results]

async def lookup_info_hash_metadata(info_hash: str, timeout_s: float=15.0) -> dict:
    """
    Lookup konkrétního info_hash přes DHT get_peers + ut_metadata.
    Vrátí: {info_hash, name, files, size_bytes, peers, source}
    Prázdný dict při timeoutu nebo chybě (nikdy nevyhodí výjimku).
    """
    governor = ResourceGovernor()
    node = KademliaNode(node_id=f'hledac-lookup-{info_hash[:8]}', governor=governor)
    try:
        async with asyncio.timeout(timeout_s):
            value = await node.find_value(info_hash)
        if value and isinstance(value, dict):
            return {'info_hash': info_hash, 'name': value.get('name', ''), 'files': value.get('files', []), 'size_bytes': value.get('size_bytes', 0), 'peers': value.get('peers', 0), 'source': 'dht'}
        return {}
    except (TimeoutError, Exception):
        return {}
    finally:
        await node.stop()

class KademliaNode:
    __slots__ = tuple(('_bep5_protocol', '_bep5_transport', '_nodes_since_snapshot', '_pending_rpcs', '_pending_rpcs_created', '_refresh_task', '_routing_loaded', '_running', '_transport', 'alpha', 'bootstrap_nodes', 'data_store', 'data_store_max', 'data_store_ttl', 'governor', 'k', 'local_graph_store', 'node_id', 'routing_table'))

    def __init__(self, node_id: str, governor: ResourceGovernor, bootstrap_nodes: list[tuple[str, int]] | None=None, k: int=20, alpha: int=3, local_graph_store: LocalGraphStore | None=None):
        self.node_id = node_id
        self.governor = governor
        self.bootstrap_nodes = bootstrap_nodes or []
        self.k = k
        self.alpha = alpha
        self.local_graph_store = local_graph_store
        self._routing_loaded = False
        self.routing_table: dict[int, list[dict[str, Any]]] = {}
        self.data_store: OrderedDict[str, tuple[Any, float]] = OrderedDict()
        self.data_store_max = 10000
        self.data_store_ttl = 3600
        self._running = True
        self._refresh_task: asyncio.Task | None = None
        self._transport = None
        self._bep5_transport: asyncio.DatagramTransport | None = None
        self._bep5_protocol: BEP5UDPProtocol | None = None
        self._nodes_since_snapshot = 0
        self._pending_rpcs: dict[str, asyncio.Future] = {}
        self._pending_rpcs_created: dict[str, float] = {}

    def set_transport(self, transport):
        self._transport = transport
        transport.register_handler('dht_ping', self._handle_ping)
        transport.register_handler('dht_pong', self._handle_pong)
        transport.register_handler('dht_store', self._handle_store)
        transport.register_handler('dht_find_value', self._handle_find_value)
        transport.register_handler('dht_find_value_resp', self._handle_find_value_resp)

    async def start_udp(self, port: int=0) -> bool:
        """
        F214: Create persistent UDP socket via asyncio.DatagramProtocol.

        Binds to 0.0.0.0:<port> (port=0 = ephemeral). Stores transport and
        BEP5UDPProtocol as self._bep5_transport / self._bep5_protocol for
        future send_and_wait() calls. Returns True on success, False on error.
        Gated by HLEDAC_ENABLE_DHT=1 — no-op when disabled.

        Idempotent: if start_udp was already called and the transport is open,
        returns True without creating a new endpoint.
        """
        if not DHT_REAL_UDP:
            return False
        if self._bep5_transport is not None and (not self._bep5_transport.is_closing()):
            return True
        try:
            loop = asyncio.get_running_loop()
            transport, protocol = await loop.create_datagram_endpoint(lambda: BEP5UDPProtocol(self._handle_message), local_addr=('0.0.0.0', port))
            self._bep5_transport = transport
            self._bep5_protocol = protocol
            return True
        except Exception as e:
            logger.debug(f'[DHT] start_udp failed: {e}')
            return False

    async def _dht_bootstrap_real(self) -> None:
        """
        F214: Real DHT bootstrap via persistent BEP-5 UDP protocol.

        Sends FIND_NODE to all bootstrap peers using send_and_wait (future
        pattern). Each request is bounded by DHT_BOOTSTRAP_SEMAPHORE (M1: max 2
        concurrent), with 5s timeout per request. K-closest nodes from each
        response are added to the routing table. Fail-soft: logs but never
        propagates.
        """
        if not DHT_REAL_UDP:
            return
        if not self._bep5_protocol:
            ok = await self.start_udp()
            if not ok:
                return
        our_id = self.node_id.encode()[:20].ljust(20, b'\x00')

        async def _query_one(host: str, port: int) -> None:
            async with DHT_BOOTSTRAP_SEMAPHORE:
                try:
                    assert self._bep5_protocol is not None, 'DHT bootstrap requires start_udp() to be called first'
                    msg = {b'y': b'q', b'q': b'find_node', b'a': {b'id': our_id, b'target': our_id}}
                    result = await self._bep5_protocol.send_and_wait((host, port), msg, timeout=DHT_BOOTSTRAP_TIMEOUT_S)
                    if not result:
                        return
                    resp, _src = result
                    r = resp.get(b'r') if isinstance(resp, dict) else None
                    if not isinstance(r, dict):
                        return
                    compact = r.get(b'nodes', b'')
                    if not isinstance(compact, bytes) or len(compact) < 26:
                        return
                    for i in range(0, len(compact) - 25, 26):
                        chunk = compact[i:i + 26]
                        if len(chunk) < 26:
                            break
                        nid = chunk[:20]
                        ip_bytes = chunk[20:24]
                        raw_port = chunk[24:26]
                        ip = '.'.join((str(b) for b in ip_bytes))
                        nport = int.from_bytes(raw_port, 'big')
                        self._update_routing(nid.hex(), {'host': ip, 'port': nport})
                except Exception:
                    pass
        tasks = [_query_one(h, p) for h, p in self.bootstrap_nodes]
        if tasks:
            await safe_gather_fire_and_forget(*tasks, label='kademlia_node:706')
        logger.debug(f'[DHT] bootstrap done: routing_table_size={sum((len(b) for b in self.routing_table.values()))}')
        if not self.routing_table:
            logger.debug('[DHT] persistent protocol got 0 nodes, fallback to per-query socket')
            await self._dht_bootstrap_fallback()

    async def stop(self):
        self._running = False
        if self._refresh_task:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
        if self.local_graph_store:
            try:
                await self._save_routing_snapshot_to_lmdb()
            except Exception as e:
                logger.debug(f'[DHT] routing snapshot save failed: {e}')
        if self._bep5_transport is not None and (not self._bep5_transport.is_closing()):
            try:
                self._bep5_transport.close()
            except Exception:
                pass
            self._bep5_transport = None
            self._bep5_protocol = None

    async def _handle_message(self, msg: dict, addr: tuple) -> None:
        """
        F214: Generic message handler for BEP5UDPProtocol.

        For unsolicited inbound responses (no pending RPC), we use this to
        opportunistically update the routing table with the sender's node id
        and observed transport-level metadata. Fail-soft: any error swallowed.
        """
        try:
            if not isinstance(msg, dict):
                return
            r = msg.get(b'r')
            if isinstance(r, dict):
                nid = r.get(b'id')
                if isinstance(nid, bytes) and len(nid) == 20:
                    self._update_routing(nid.hex(), {'host': addr[0], 'port': addr[1]})
        except Exception:
            pass

    async def _dht_bootstrap_fallback(self) -> None:
        """
        F214: Fallback bootstrap using per-query UDP socket (used when persistent
        BEP5UDPProtocol got 0 nodes — some NAT/firewall setups don't deliver
        responses to long-lived sockets).
        """
        our_id = self.node_id.encode()[:20].ljust(20, b'\x00')

        async def _query(host: str, port: int) -> None:
            async with DHT_BOOTSTRAP_SEMAPHORE:
                sock = None
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    sock.settimeout(DHT_BOOTSTRAP_TIMEOUT_S)
                    sock.setblocking(False)
                    msg = {b'y': b'q', b'q': b'find_node', b'a': {b'id': our_id, b'target': our_id}}
                    loop = asyncio.get_running_loop()
                    await loop.sock_sendto(sock, bencode(msg), (host, port))
                    data = await loop.sock_recv(sock, 65535)
                    if not data:
                        return
                    resp = bdecode(data)
                    if not isinstance(resp, dict):
                        return
                    r = resp.get(b'r')
                    if not isinstance(r, dict):
                        return
                    compact = r.get(b'nodes', b'')
                    if not isinstance(compact, bytes) or len(compact) < 26:
                        return
                    for i in range(0, len(compact) - 25, 26):
                        chunk = compact[i:i + 26]
                        if len(chunk) < 26:
                            break
                        nid = chunk[:20]
                        nip = '.'.join((str(b) for b in chunk[20:24]))
                        nport = int.from_bytes(chunk[24:26], 'big')
                        self._update_routing(nid.hex(), {'host': nip, 'port': nport})
                except Exception:
                    pass
                finally:
                    if sock:
                        sock.close()
        tasks = [_query(h, p) for h, p in self.bootstrap_nodes]
        if tasks:
            await safe_gather_fire_and_forget(*tasks, label='kademlia_node:809')

    def _distance(self, key1: str, key2: str) -> int:
        h1 = int(hashlib.sha256(key1.encode()).hexdigest(), 16)
        h2 = int(hashlib.sha256(key2.encode()).hexdigest(), 16)
        return h1 ^ h2

    def _bucket_index(self, key: str) -> int:
        dist = self._distance(key, self.node_id)
        if dist == 0:
            return 0
        return min(dist.bit_length() - 1, 255)

    def _update_routing(self, peer_id: str, peer_info: dict[str, Any] | None=None):
        if peer_id == self.node_id:
            return
        peer_info = peer_info or {}
        b = self._bucket_index(peer_id)
        bucket = self.routing_table.setdefault(b, [])
        bucket = [p for p in bucket if p.get('id') != peer_id]
        bucket.append({'id': peer_id, **peer_info, 'last_seen': time.time()})
        if len(bucket) > self.k:
            bucket = bucket[:self.k]
        self.routing_table[b] = bucket
        if peer_info.get('host') and peer_info.get('port'):
            self._persist_node_async(peer_id, peer_info['host'], peer_info['port'])
            self._nodes_since_snapshot += 1
            self._maybe_persist_snapshot()

    def _persist_node_async(self, node_id: str, host: str, port: int) -> None:
        """Persist a DHT node to LMDB via LocalGraphStore (fire-and-forget)."""
        if not self.local_graph_store:
            return
        try:
            safe_create_task(self.local_graph_store.put_dht_node(node_id, host, port))
        except Exception:
            pass

    async def _load_routing_from_lmdb(self) -> None:
        """Load persisted DHT nodes from LMDB into routing table on startup."""
        if not self.local_graph_store or self._routing_loaded:
            return
        try:
            snapshot_nodes = await self.local_graph_store.load_routing_snapshot()
            if snapshot_nodes:
                for n in snapshot_nodes:
                    if not isinstance(n, dict):
                        continue
                    nid = n.get('node_id', '')
                    host = n.get('host', '')
                    port = n.get('port', 0)
                    if nid and len(nid) == 40 and host and port:
                        self._update_routing(nid, {'host': host, 'port': port})
            else:
                nodes = await self.local_graph_store.get_all_dht_nodes(limit=1000)
                for n in nodes:
                    nid = n.get('id', '')
                    if nid and len(nid) == 40:
                        host = n.get('host', '')
                        port = n.get('port', 0)
                        if host and port:
                            self._update_routing(nid, {'host': host, 'port': port})
            self._routing_loaded = True
        except Exception:
            pass

    def _flatten_routing_table(self) -> list[dict[str, Any]]:
        """
        F214: Flatten routing table into a list of {node_id, host, port, last_seen}
        dicts suitable for LMDB snapshot persistence.
        """
        out: list[dict[str, Any]] = []
        for bucket in self.routing_table.values():
            for n in bucket:
                nid = n.get('id')
                host = n.get('host')
                port = n.get('port')
                if nid and host and port:
                    out.append({'node_id': nid, 'host': host, 'port': int(port), 'last_seen': float(n.get('last_seen', time.time()))})
        return out

    async def _save_routing_snapshot_to_lmdb(self) -> None:
        """
        F214: Persist full routing table snapshot to LMDB. Fail-soft.
        """
        if not self.local_graph_store:
            return
        nodes = self._flatten_routing_table()
        if not nodes:
            return
        try:
            await self.local_graph_store.save_routing_snapshot(nodes)
            self._nodes_since_snapshot = 0
        except Exception:
            pass

    def _maybe_persist_snapshot(self) -> None:
        """
        F214: Fire-and-forget snapshot persistence when 50+ new nodes have been
        discovered since the last snapshot. Never blocks the caller.
        """
        if not self.local_graph_store:
            return
        if self._nodes_since_snapshot < DHT_SNAPSHOT_EVERY_N:
            return
        try:
            safe_create_task(self._save_routing_snapshot_to_lmdb())
        except Exception:
            pass

    def _find_closest_nodes(self, key: str, count: int) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        b = self._bucket_index(key)
        for i in range(max(0, b - 5), min(256, b + 6)):
            candidates.extend(self.routing_table.get(i, []))
        candidates.sort(key=lambda n: self._distance(n['id'], key))
        return candidates[:count]

    def _local_put(self, key: str, value: Any):
        self.data_store[key] = (value, time.time())
        self.data_store.move_to_end(key)
        if len(self.data_store) > self.data_store_max:
            self.data_store.popitem(last=False)

    def _local_get(self, key: str) -> Any | None:
        if key not in self.data_store:
            return None
        value, ts = self.data_store[key]
        if time.time() - ts > self.data_store_ttl:
            del self.data_store[key]
            return None
        self.data_store.move_to_end(key)
        return value

    def _cleanup_pending_rpcs(self):
        """
        F185E: TTL + size-based cleanup for _pending_rpcs.

        Evicts:
        1. Completed or cancelled futures
        2. Entries older than MAX_PENDING_RPC_TTL_S
        3. If still over MAX_PENDING_RPCS, evicts oldest by creation time (FIFO)
        """
        now = time.time()
        expired_rpc_ids = [rid for rid, fut in list(self._pending_rpcs.items()) if fut.done() or fut.cancelled() or (rid in self._pending_rpcs_created and now - self._pending_rpcs_created[rid] > MAX_PENDING_RPC_TTL_S)]
        for rid in expired_rpc_ids:
            self._pending_rpcs.pop(rid, None)
            self._pending_rpcs_created.pop(rid, None)
        if len(self._pending_rpcs) > MAX_PENDING_RPCS:
            excess = len(self._pending_rpcs) - MAX_PENDING_RPCS
            sorted_ids = sorted(self._pending_rpcs_created, key=lambda rid: self._pending_rpcs_created[rid])
            for rid in sorted_ids[:excess]:
                self._pending_rpcs.pop(rid, None)
                self._pending_rpcs_created.pop(rid, None)

    async def store(self, key: str, value: Any):
        self._local_put(key, value)
        closest = self._find_closest_nodes(key, self.k)
        tasks = [self._send_store(p['id'], key, value) for p in closest if p['id'] != self.node_id]
        if tasks:
            await safe_gather_fire_and_forget(*tasks, label='kademlia_node:991')

    async def find_value(self, key: str) -> Any | None:
        self._cleanup_pending_rpcs()
        local = self._local_get(key)
        if local is not None:
            return local
        queried = set()
        shortlist = self._find_closest_nodes(key, self.alpha)
        while shortlist:
            rpc_ids: list[str] = []
            send_tasks: list[asyncio.Task] = []
            for peer in shortlist[:self.alpha]:
                pid = peer['id']
                if pid in queried or pid == self.node_id:
                    continue
                queried.add(pid)
                rpc_id = str(uuid.uuid4())
                rpc_ids.append(rpc_id)
                fut = asyncio.get_running_loop().create_future()
                self._pending_rpcs[rpc_id] = fut
                self._pending_rpcs_created[rpc_id] = time.time()
                send_tasks.append(safe_create_task(self._send_find_value(pid, key, rpc_id), name=f'kademlia:send_find_value:{pid[:8]}'))
            if not rpc_ids:
                break
            futures = [self._pending_rpcs[rid] for rid in rpc_ids if rid in self._pending_rpcs]
            if not futures:
                break
            async with asyncio.timeout(3.0):
                results = await parallel_ok(*futures, label='kademlia_node:1028')
            for rid in rpc_ids:
                self._pending_rpcs.pop(rid, None)
                self._pending_rpcs_created.pop(rid, None)
            for res in results:
                if isinstance(res, BaseException):
                    continue
                if isinstance(res, dict) and 'value' in res:
                    self._local_put(key, res['value'])
                    return res['value']
                if isinstance(res, dict) and 'nodes' in res:
                    for n in res['nodes']:
                        if n.get('id') and n['id'] not in queried:
                            shortlist.append(n)
            shortlist.sort(key=lambda n: self._distance(n['id'], key))
            shortlist = shortlist[:self.k]
        return None

    async def _ping(self, peer_id: str) -> bool:
        if not self._transport:
            return False
        rpc_id = str(uuid.uuid4())
        fut = asyncio.get_running_loop().create_future()
        self._pending_rpcs[rpc_id] = fut
        self._pending_rpcs_created[rpc_id] = time.time()
        await self._transport.send_message(peer_id, 'dht_ping', {'rpc_id': rpc_id}, '')
        try:
            async with asyncio.timeout(2.0):
                ok = await fut
            self._update_routing(peer_id)
            return bool(ok)
        except TimeoutError:
            return False
        finally:
            self._pending_rpcs.pop(rpc_id, None)
            self._pending_rpcs_created.pop(rpc_id, None)

    async def _send_store(self, peer_id: str, key: str, value: Any):
        if not self._transport:
            return
        try:
            import orjson
            approx = len(orjson.dumps(value))
            if approx > MAX_ITEM_BYTES:
                logger.warning('DHT store skipped: value too large')
                return
        except Exception:
            pass
        await self._transport.send_message(peer_id, 'dht_store', {'key': key, 'value': value}, '')
        self._update_routing(peer_id)

    async def _send_find_value(self, peer_id: str, key: str, rpc_id: str):
        if not self._transport:
            return
        await self._transport.send_message(peer_id, 'dht_find_value', {'key': key, 'rpc_id': rpc_id}, '')
        self._update_routing(peer_id)

    async def _handle_ping(self, data: dict[str, Any]):
        sender = data.get('sender')
        payload = data.get('payload', {})
        rpc_id = payload.get('rpc_id')
        if sender and rpc_id and self._transport:
            self._update_routing(sender)
            await self._transport.send_message(sender, 'dht_pong', {'rpc_id': rpc_id}, '')

    async def _handle_pong(self, data: dict[str, Any]):
        sender = data.get('sender')
        payload = data.get('payload', {})
        rpc_id = payload.get('rpc_id')
        if sender:
            self._update_routing(sender)
        fut = self._pending_rpcs.get(rpc_id)
        if fut and (not fut.done()):
            fut.set_result(True)
            self._pending_rpcs_created.pop(rpc_id, None)

    async def _handle_store(self, data: dict[str, Any]):
        sender = data.get('sender')
        payload = data.get('payload', {})
        if sender:
            self._update_routing(sender)
        key = payload.get('key')
        value = payload.get('value')
        if key is None:
            return
        self._local_put(key, value)

    async def _handle_find_value(self, data: dict[str, Any]):
        sender = data.get('sender')
        payload = data.get('payload', {})
        key = payload.get('key')
        rpc_id = payload.get('rpc_id')
        if not (sender and key and rpc_id and self._transport):
            return
        self._update_routing(sender)
        value = self._local_get(key)
        if value is not None:
            await self._transport.send_message(sender, 'dht_find_value_resp', {'rpc_id': rpc_id, 'value': value}, '')
            return
        closest = self._find_closest_nodes(key, self.k)
        await self._transport.send_message(sender, 'dht_find_value_resp', {'rpc_id': rpc_id, 'nodes': closest}, '')

    async def _handle_find_value_resp(self, data: dict[str, Any]):
        sender = data.get('sender')
        payload = data.get('payload', {})
        rpc_id = payload.get('rpc_id')
        if sender:
            self._update_routing(sender)
        fut = self._pending_rpcs.get(rpc_id)
        if fut and (not fut.done()):
            fut.set_result(payload)
            self._pending_rpcs_created.pop(rpc_id, None)

    async def _refresh_loop(self):
        while self._running:
            await asyncio.sleep(300)
            self._cleanup_pending_rpcs()
            bucket_idx = _RNG.randint(0, 255)
            bucket = list(self.routing_table.get(bucket_idx, []))
            for peer in bucket:
                pid = peer.get('id')
                if pid:
                    ok = await self._ping(pid)
                    if not ok:
                        self.routing_table[bucket_idx] = [p for p in self.routing_table.get(bucket_idx, []) if p.get('id') != pid]

    async def get_peers(self, info_hash: str) -> list[tuple[str, int]]:
        """
        F214Q: BEP-5 get_peers — iterative Kademlia lookup for peer addresses.

        Performs up to MAXCRAWLDEPTH (3) iterations:
          1. Pick K-closest nodes from routing table (or bootstrap peers on iter 0)
          2. Send GET_PEERS in parallel (bounded by DHT_REQUEST_SEMAPHORE=50)
          3. Collect `values` (peer addresses) and refresh routing table from
             `nodes` field of responses
          4. Stop when no closer nodes are found, or MAXCRAWLDEPTH reached
          5. Return up to 50 unique (ip, port) tuples

        Args:
            info_hash: 40-char hex torrent info hash

        Returns:
            List of (ip_address, port) tuples. Empty on timeout/error (fail-soft).
        """
        peers: list[tuple[str, int]] = []
        try:
            ih_bytes = bytes.fromhex(info_hash)
        except ValueError:
            logger.debug(f'[DHT] invalid info_hash hex: {info_hash!r}')
            return peers
        ih_bytes = ih_bytes[:20].ljust(20, b'\x00')
        our_id = self.node_id.encode()[:20].ljust(20, b'\x00')
        queried: set[tuple[str, int]] = set()
        seen_peers: set[tuple[str, int]] = set()

        async def _query_peer(host: str, port: int) -> dict | None:
            """Single GET_PEERS query. Returns decoded response dict or None."""
            async with DHT_REQUEST_SEMAPHORE:
                try:
                    msg = {b'y': b'q', b'q': b'get_peers', b'a': {b'id': our_id, b'info_hash': ih_bytes}}
                    if self._bep5_protocol is not None:
                        result = await self._bep5_protocol.send_and_wait((host, port), msg, timeout=DHT_REQUEST_TIMEOUT_S)
                        if not result:
                            return None
                        resp, _src = result
                    else:
                        loop = asyncio.get_running_loop()
                        try:
                            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                            sock.settimeout(DHT_REQUEST_TIMEOUT_S)
                            sock.setblocking(False)
                            await loop.sock_sendto(sock, self._bencode(msg), (host, port))
                            data = await loop.sock_recv(sock, 65535)
                            resp = self._bdecode(data) if data else None
                        finally:
                            if sock is not None:
                                sock.close()
                    if not isinstance(resp, dict):
                        return None
                    r = resp.get(b'r')
                    if not isinstance(r, dict):
                        r = resp.get('r', {})
                        if not isinstance(r, dict):
                            return None
                    r_norm: dict = {}
                    for k, v in r.items():
                        r_norm[k if isinstance(k, bytes) else k.encode()] = v
                    return r_norm
                except Exception:
                    return None

        # ── routing helpers ───────────────────────────────────────────────────────

        def _collect_candidate_peers(depth: int, info_hash: str) -> list[tuple[str, int]]:
            """Collect candidate (host, port) peers for a crawl depth.

            Deduplicates bootstrap_nodes against routing_table entries on depth=0
            to avoid sending redundant queries to the same peer.
            """
            if depth == 0 or not self.routing_table:
                # Build seen set from routing table first (avoids O(n²) dedup below)
                seen: set[tuple[str, int]] = set()
                for bucket in self.routing_table.values():
                    for n in bucket:
                        h, p = n.get('host'), n.get('port')
                        if h and p:
                            seen.add((h, int(p)))
                candidates: list[tuple[str, int]] = []
                for h, p in self.bootstrap_nodes:
                    if (h, p) not in seen:
                        candidates.append((h, p))
            else:
                closest = self._find_closest_nodes(info_hash, self.alpha)
                candidates = [(n['host'], int(n['port'])) for n in closest if n.get('host') and n.get('port')]
            return candidates

        def _peers_from_response(r_norm: dict) -> list[tuple[str, int]]:
            """Extract (ip, port) peer addresses from a get_peers response."""
            out: list[tuple[str, int]] = []
            values = r_norm.get(b'values') or r_norm.get('values') or []
            if not isinstance(values, list):
                return out
            for val in values:
                if isinstance(val, bytes) and len(val) == 6:
                    ip = '.'.join((str(b) for b in val[:4]))
                    p = int.from_bytes(val[4:6], 'big')
                    out.append((ip, p))
            return out

        def _nodes_from_response(r_norm: dict) -> list[tuple[str, str, int]]:
            """Extract (nid, ip, port) nodes from a get_peers response."""
            out: list[tuple[str, str, int]] = []
            compact = r_norm.get(b'nodes') or r_norm.get('nodes') or b''
            if not isinstance(compact, (bytes, bytearray)):
                return out
            for i in range(0, len(compact) - 25, 26):
                chunk = compact[i:i + 26]
                if len(chunk) < 26:
                    break
                nid = chunk[:20].hex()
                nip = '.'.join((str(b) for b in chunk[20:24]))
                nport = int.from_bytes(chunk[24:26], 'big')
                out.append((nid, nip, nport))
            return out

        def _update_routing_batch(results: list[dict | None]) -> bool:
            """Update routing table from response results. Returns True if new peers found."""
            got_new_peers = False
            for res in results:
                if not isinstance(res, dict):
                    continue
                for ip, p in _peers_from_response(res):
                    if (ip, p) not in seen_peers:
                        seen_peers.add((ip, p))
                        peers.append((ip, p))
                        got_new_peers = True
                for nid, nip, nport in _nodes_from_response(res):
                    self._update_routing(nid, {'host': nip, 'port': nport})
            return got_new_peers
        # ── iterative crawl loop ──────────────────────────────────────────────────
        for depth in range(MAXCRAWLDEPTH):
            candidates = _collect_candidate_peers(depth, info_hash)
            new_sources = [(h, p) for h, p in candidates if (h, p) not in queried]
            if not new_sources:
                break
            for h, p in new_sources[:10]:
                queried.add((h, p))
            tasks = [_query_peer(h, p) for h, p in new_sources[:10]]
            if not tasks:
                break
            results = await parallel_ok(*tasks, label='kademlia_node:get_peers')
            got_new_peers = _update_routing_batch(results)
            if not got_new_peers and depth > 0:
                break
        return peers[:50]

    async def crawl(self, keyword: str, duration_s: int=120, max_results: int=50) -> list[dict]:
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
        sock: socket.socket | None = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(2.0)
            sock.setblocking(False)
            asyncio.get_running_loop()
            for peer_host, peer_port in BOOTSTRAP_PEERS:
                try:
                    await self._dht_send_ping(sock, peer_host, peer_port)
                except Exception as e:
                    logger.debug(f'DHT bootstrap ping to {peer_host}:{peer_port} failed: {e}')
            keyword_lower = keyword.lower()
            tokens = keyword_lower.split()
            info_hashes = []
            for token in tokens[:5]:
                hash_input = token.encode()
                btih_hash = hashlib.sha256(hash_input).hexdigest()[:40]
                info_hash = bytes.fromhex(btih_hash)
                info_hashes.append((token, info_hash))
            combined = keyword_lower.replace(' ', '_')[:50]
            combined_hash = hashlib.sha256(combined.encode()).hexdigest()[:40]
            info_hashes.append((combined, bytes.fromhex(combined_hash)))
            while time.monotonic() - start_time < duration_s and len(results) < max_results:
                for token, info_hash in info_hashes:
                    if len(results) >= max_results:
                        break
                    for _ in range(3):
                        peer_host = _RNG.choice([p[0] for p in BOOTSTRAP_PEERS])
                        peer_port = _RNG.choice([p[1] for p in BOOTSTRAP_PEERS])
                        try:
                            peers_response = await self._dht_send_get_peers(sock, peer_host, peer_port, info_hash)
                            if peers_response:
                                await self._handle_get_peers_response(peers_response, info_hash, token, results, seen_hashes)
                        except Exception as e:
                            logger.debug(f'get_peers query failed: {e}')
                        await asyncio.sleep(0.1)
                self._refresh_routing_from_results()
                await asyncio.sleep(1.0)
        finally:
            if sock:
                sock.close()
        if results:
            try:
                await self._store_dht_results(keyword, results)
            except Exception as e:
                logger.debug(f'DHT results storage failed: {e}')
        elapsed = time.monotonic() - start_time
        logger.info(f"DHT crawl '{keyword}': {len(results)} results in {elapsed:.1f}s")
        return results[:max_results]

    async def _dht_send_ping(self, sock: socket.socket, host: str, port: int) -> dict | None:
        """Send DHT ping and receive response."""
        ping_msg = {'t': 'aa', 'y': 'q', 'q': 'ping', 'a': {'id': self.node_id.encode()[:20].ljust(20, b'\x00')}}
        try:
            loop = asyncio.get_running_loop()
            await loop.sock_sendto(sock, self._bencode(ping_msg), (host, port))
            async with asyncio.timeout(2.0):
                data = await loop.sock_recv(sock, 65535)
            if data:
                return self._bdecode(data)
        except Exception:
            pass
        return None

    async def _dht_send_get_peers(self, sock: socket.socket, host: str, port: int, info_hash: bytes) -> dict | None:
        """Send get_peers query for info_hash."""
        msg = {'t': 'bb', 'y': 'q', 'q': 'get_peers', 'a': {'id': self.node_id.encode()[:20].ljust(20, b'\x00'), 'info_hash': info_hash[:20].ljust(20, b'\x00')}}
        try:
            loop = asyncio.get_running_loop()
            await loop.sock_sendto(sock, self._bencode(msg), (host, port))
            async with asyncio.timeout(2.0):
                data = await loop.sock_recv(sock, 65535)
            if data:
                return self._bdecode(data)
        except Exception:
            pass
        return None

    async def _handle_get_peers_response(self, response: dict, info_hash: bytes, keyword: str, results: list, seen_hashes: set):
        """Handle get_peers response and extract peer/torrent info."""
        try:
            r = response.get('r', {})
            if not r:
                return
            nodes = r.get('nodes', '')
            if nodes and len(nodes) >= 26:
                num_peers = len(nodes) // 26
                for i in range(num_peers):
                    node_data = nodes[i * 26:(i + 1) * 26]
                    peer_id = node_data[:20]
                    peer_host = '.'.join((str(b) for b in node_data[20:24]))
                    peer_port = int.from_bytes(node_data[24:26], 'big')
                    self._update_routing(peer_id.hex(), {'host': peer_host, 'port': peer_port})
            values = r.get('values', [])
            if isinstance(values, list):
                for value in values[:5]:
                    if len(value) == 6:
                        peer_host = '.'.join((str(b) for b in value[:4]))
                        peer_port = int.from_bytes(value[4:6], 'big')
                        info_hash_str = info_hash.hex()
                        if info_hash_str not in seen_hashes:
                            seen_hashes.add(info_hash_str)
                            metadata = await self._fetch_torrent_metadata(peer_host, peer_port, info_hash)
                            if metadata and keyword.lower() in metadata.get('name', '').lower():
                                results.append({'info_hash': info_hash_str, 'name': metadata.get('name', ''), 'files': metadata.get('files', []), 'size_bytes': metadata.get('length', 0), 'peers': len(values), 'source': 'dht'})
        except Exception as e:
            logger.debug(f'handle_get_peers_response failed: {e}')

    async def _fetch_torrent_metadata(self, peer_host: str, peer_port: int, info_hash: bytes) -> dict | None:
        """
        P10: Fetch torrent metadata from peer using BEP-9 (ut_metadata).

        Connects to peer via TCP and performs BitTorrent handshake + extension
        handshake to download metadata (info dict) without downloading content.
        """
        try:
            async with asyncio.timeout(5.0):
                reader, writer = await asyncio.open_connection(peer_host, peer_port)
            protocol = bytes([19]) + b'BitTorrent protocol'
            handshake = protocol + bytes(8) + info_hash[:20].ljust(20, b'\x00') + self.node_id.encode()[:20].ljust(20, b'\x00')
            writer.write(handshake)
            await writer.drain()
            async with asyncio.timeout(5.0):
                response = await reader.read(68)
            if len(response) < 68:
                writer.close()
                await writer.wait_closed()
                return None
            if response[25] & 16 == 0:
                writer.close()
                await writer.wait_closed()
                return None
            ext_handshake = {'m': {'ut_metadata': 1}, 'ut_metadata': 1}
            ext_msg = self._build_ext_message(20, ext_handshake)
            writer.write(ext_msg)
            await writer.drain()
            async with asyncio.timeout(5.0):
                ext_response = await reader.read(65535)
            if not ext_response:
                writer.close()
                await writer.wait_closed()
                return None
            metadata_size = 0
            metadata_parts = {}
            piece_index = 0
            while True:
                request = {'msg_type': 2, 'piece': piece_index}
                req_msg = self._build_ext_message(3, request)
                writer.write(req_msg)
                await writer.drain()
                try:
                    async with asyncio.timeout(5.0):
                        data = await reader.read(65535)
                    if not data:
                        break
                    msg = self._parse_ext_message(data)
                    if msg and msg.get('msg_type') == 1:
                        piece = msg.get('piece', 0)
                        total_size = msg.get('total_size', 0)
                        if total_size > 0 and metadata_size == 0:
                            metadata_size = total_size
                        if 'metadata' in msg:
                            metadata_parts[piece] = msg['metadata']
                        if len(metadata_parts) * 16384 >= metadata_size:
                            break
                except TimeoutError:
                    break
                piece_index += 1
                if piece_index > 1000:
                    break
            writer.close()
            await writer.wait_closed()
            if metadata_parts and metadata_size > 0:
                full_metadata = b''.join((metadata_parts.get(i, b'') for i in range(len(metadata_parts))))
                return self._bdecode(full_metadata)
        except Exception as e:
            logger.debug(f'_fetch_torrent_metadata failed: {e}')
        return None

    def _build_ext_message(self, msg_id: int, payload: dict) -> bytes:
        """Build BEP-10 extension protocol message."""
        bencoded = self._bencode(payload)
        length = len(bencoded) + 1
        return length.to_bytes(4, 'big') + bytes([msg_id]) + bencoded

    def _parse_ext_message(self, data: bytes) -> dict | None:
        """Parse BEP-10 extension protocol message."""
        try:
            if len(data) < 5:
                return None
            length = int.from_bytes(data[:4], 'big')
            msg_id = data[4]
            payload = self._bdecode(data[5:5 + length])
            return {'msg_id': msg_id, **payload}
        except Exception:
            return None

    def _bencode(self, obj: Any) -> bytes:
        """Simple bencode encoder for DHT messages."""
        if isinstance(obj, dict):
            items = []
            for k in sorted(obj.keys()):
                items.append(self._bencode(k))
                items.append(self._bencode(obj[k]))
            return b'd' + b''.join(items) + b'e'
        elif isinstance(obj, list):
            return b'l' + b''.join((self._bencode(i) for i in obj)) + b'e'
        elif isinstance(obj, int):
            return f'i{obj}e'.encode()
        elif isinstance(obj, bytes):
            return f'{len(obj)}:'.encode() + obj
        elif isinstance(obj, str):
            return f'{len(obj.encode())}:'.encode() + obj.encode()
        return b''

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
        if data[pos:pos + 1] == b'd':
            result = {}
            pos += 1
            while pos < len(data) and data[pos:pos + 1] != b'e':
                key, pos = self._bdecode_recursive(data, pos)
                value, pos = self._bdecode_recursive(data, pos)
                if key is not None:
                    result[key] = value
            return (result, pos + 1)
        elif data[pos:pos + 1] == b'l':
            result = []
            pos += 1
            while pos < len(data) and data[pos:pos + 1] != b'e':
                item, pos = self._bdecode_recursive(data, pos)
                result.append(item)
            return (result, pos + 1)
        elif data[pos:pos + 1] == b'i':
            pos += 1
            end = data.index(b'e', pos)
            return (int(data[pos:end]), end + 1)
        elif data[pos:pos + 1].isdigit():
            colon = data.index(b':', pos)
            length = int(data[pos:colon])
            start = colon + 1
            return (data[start:start + length], start + length)
        return (None, pos + 1)

    async def _store_dht_results(self, keyword: str, results: list):
        """Sprint F192B: DHT crawl is EXPERIMENTAL — no longer persists findings.

        Findings from DHT crawl are returned to caller but NOT written to
        DuckDBShadowStore. Canonical sprint path handles persistence if needed.
        Kept as no-op to avoid breaking callers that reference this method.
        """
        pass

    def _refresh_routing_from_results(self):
        """Refresh routing table - called periodically during crawl."""
        pass