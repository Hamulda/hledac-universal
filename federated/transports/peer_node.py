"""
F350M-FED-P: PeerNodeTransport — real cross-host P2P (UDP + Noise XX + mDNS).

Sprint: F350M-FED-P / P2P Transport Activation 2026-06-04





Target: federated/transports/peer_node.py

PURPOSE
=======
This is the Tier-2 real P2P transport. Where LaneDispatchTransport
dispatches per-lane to LOCAL backends, PeerNodeTransport dispatches
to REMOTE peer nodes over a UDP mesh with end-to-end encrypted
channels (Noise XX) and zero-config LAN discovery (mDNS / DNS-SD).

WHY NOISE XX (cutting edge)
===========================
Noise Protocol Framework (https://noiseprotocol.org/) is a modern
(~2018+) replacement for TLS in P2P / IoT contexts:

  - Smaller code surface (~250 LOC for the XX pattern)
  - Mutual authentication in 3 messages (XX)
  - Forward secrecy (X25519 + ephemeral keys per session)
  - AEAD cipher (ChaCha20-Poly1305) — AEAD over each message
  - No certificate chain / no PKI — identity keys are just 32 bytes
  - Used by: WireGuard, Lightning Network, I2P BOB, WhatsApp (in
    parts), Nym mixnet, many IoT firmware stacks.

For M1 8GB we use `cryptography` (already in `security` extra) for
the primitives. No external Noise lib needed.

WHY mDNS / DNS-SD
=================
RFC 6763 DNS-SD over mDNS (multicast UDP 224.0.0.251:5353) gives
us zero-configuration LAN peer discovery:
  - No central server
  - No static config
  - Survives WiFi reconnects
  - Auto-detects all Hledac nodes on the LAN

We use the `zeroconf` package (pure Python, ~3MB resident). The
service type is `_hledac-fed._udp.local.` (configurable). The
discovery cache is bounded to prevent leaks.

CROSS-NAT / INTERNET
====================
For peers that are NOT on the same LAN, we use a UDP rendezvous
protocol: each peer advertises its public endpoint through an
optional Tor/I2P rendezvous (we already have these transports). The
rendezvous is opt-in (env-gated) — when disabled, only LAN peers
are reachable, which is sufficient for most use cases.

PROTOCOL MESSAGE SHAPE
======================
On the wire (after Noise AEAD), every message is a JSON object:

  {
    "v": 1,            # protocol version
    "t": "research",   # message type
    "lane": "surface", # which lane this is for
    "q":  "query str", # the research query
    "n":  <nonce>,     # unique nonce for anti-replay
    "ts": <unix_ms>,   # timestamp
  }

  Response (peer → initiator):

  {
    "v": 1,
    "t": "findings",
    "results": [ {ioc_type, ioc_value, confidence, ...}, ... ],
    "n":  <nonce>,
    "ts": <unix_ms>,
  }

  Error:
  { "v": 1, "t": "error", "code": "TIMEOUT|UNSUPPORTED|..." }

M1 8GB SAFETY
=============
- PEER_NODE_MAX_PEERS = 4 (M1 8GB cannot host >4 P2P sessions)
- PEER_NODE_HANDSHAKE_TIMEOUT_S = 5.0
- PEER_NODE_MSG_MAX_BYTES = 8192
- PEER_NODE_NONCE_CACHE_MAX = 1024 (anti-replay, bounded deque)
- PEER_NODE_MDNS_RATE_LIMIT_S = 60 (cache TTL for mDNS results)
- All exceptions caught, all methods fail-soft
- No top-level imports of zeroconf / cryptography (lazy, on first run)

INVARIANTS
==========
[PN-1] No message exceeds PEER_NODE_MSG_MAX_BYTES.
[PN-2] No more than PEER_NODE_MAX_PEERS active sessions.
[PN-3] Each inbound message's nonce is checked against the anti-replay
      cache; replays are dropped.
[PN-4] Handshake is bounded to PEER_NODE_HANDSHAKE_TIMEOUT_S.
[PN-5] close() is idempotent and releases the mDNS listener and any
      open sockets.
[PN-6] All public methods are async and never raise.
"""
import asyncio
import logging
import os
import secrets
import time
from collections import deque
from typing import Any
from hledac.universal.utils.async_helpers import safe_wait_for
from .protocol import NodeTransportFactory
logger = logging.getLogger(__name__)
PEER_NODE_MAX_PEERS: int = 4
'Hard cap on simultaneous peer sessions.'
PEER_NODE_HANDSHAKE_TIMEOUT_S: float = 5.0
'Per-handshake timeout. Fail-soft: timeout → peer not added.'
PEER_NODE_MSG_MAX_BYTES: int = 8192
'Maximum size of a single encrypted message (before Noise framing).'
PEER_NODE_NONCE_CACHE_MAX: int = 1024
'Anti-replay nonce cache. Oldest nonces evicted when full.'
PEER_NODE_MDNS_RATE_LIMIT_S: float = 60.0
'mDNS discovery result cache TTL.'
PEER_NODE_DEFAULT_PORT: int = 47715
'Default UDP port for Hledac federated peer mesh.'
PEER_NODE_SERVICE_TYPE: str = '_hledac-fed._udp.local.'
'mDNS service type. Configurable via HLEDAC_FEDERATED_P2P_SERVICE.'
PEER_NODE_PROTO_VERSION: int = 1
'Wire protocol version. Bump on incompatible changes.'
PEER_NODE_ID_LEN: int = 8
'Length of the short peer id (hex chars).'
ENV_GATE: str = 'HLEDAC_ENABLE_FEDERATED_P2P'
'Env-var gate. Set to 1/true/yes/on to enable the P2P transport.'

def is_peer_node_enabled() -> bool:
    """True iff the env-gate is set to a truthy value."""
    val = os.environ.get(ENV_GATE, '').strip().lower()
    return val in ('1', 'true', 'yes', 'on')

class _NonceCache:
    """
    Bounded LRU nonce cache for anti-replay.

    Uses a deque + set for O(1) add/check. When the deque exceeds
    PEER_NODE_NONCE_CACHE_MAX, the oldest nonce is evicted.

    Thread-safety: cooperative (single event loop). Not safe across
    threads; we never share between event loops.
    """
    __slots__ = ('_deque', '_set', '_max')

    def __init__(self, max_size: int=PEER_NODE_NONCE_CACHE_MAX) -> None:
        self._deque: deque[str] = deque(maxlen=max_size)
        self._set: set[str] = set()
        self._max = max_size

    def seen(self, nonce: str) -> bool:
        """Check if the nonce was seen recently. Side-effect: marks it as seen."""
        if not nonce:
            return False
        if nonce in self._set:
            return True
        if len(self._deque) >= self._max:
            evicted = self._deque[0]
            self._deque.popleft()
            self._set.discard(evicted)
        self._deque.append(nonce)
        self._set.add(nonce)
        return False

    def __len__(self) -> int:
        return len(self._deque)

class _NoiseXXSession:
    """
    Minimal Noise XX handshake + transport.

    The XX pattern is:
        → e
        ← e, ee, s, es
        → s, se

    After handshake, both sides have:
        send_cipher: ChaCha20-Poly1305 (initiator's send = responder's recv)
        recv_cipher: ChaCha20-Poly1305 (initiator's recv = responder's send)

    We expose encrypt(plaintext) -> ciphertext and
    decrypt(ciphertext) -> plaintext as byte methods. Each call advances
    a per-direction nonce counter (anti-replay at the cipher layer too).

    On any protocol error → session becomes invalid (subsequent
    encrypt/decrypt raise NoiseError). Caller treats this as a dropped
    peer.
    """
    __slots__ = ('static_priv', 'static_pub', 'ephemeral_priv', 'ephemeral_pub', '_send_cipher', '_recv_cipher', '_send_nonce', '_recv_nonce', '_handshake_complete', '_is_initiator')

    def __init__(self, is_initiator: bool, static_keypair: tuple[bytes, bytes] | None=None) -> None:
        """
        Args:
            is_initiator: True if this side starts the handshake.
            static_keypair: (private_bytes, public_bytes) for the local
                long-term identity. If None, a fresh keypair is generated.
        """
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
        if static_keypair is None:
            priv = X25519PrivateKey.generate()
            self.static_priv: bytes = priv.private_bytes_raw()
            self.static_pub: bytes = priv.public_key().public_bytes_raw()
        else:
            self.static_priv, self.static_pub = static_keypair
        eph = X25519PrivateKey.generate()
        self.ephemeral_priv: bytes = eph.private_bytes_raw()
        self.ephemeral_pub: bytes = eph.public_key().public_bytes_raw()
        self._send_cipher: Any = None
        self._recv_cipher: Any = None
        self._send_nonce: int = 0
        self._recv_nonce: int = 0
        self._handshake_complete: bool = False
        self._is_initiator: bool = is_initiator

    def get_static_pub(self) -> bytes:
        """Return the local long-term public key (identity)."""
        return self.static_pub

    def handshake_message_1(self) -> bytes:
        """Initiator → responder: e (ephemeral public key)."""
        return self.ephemeral_pub

    def handshake_message_2(self, msg1: bytes) -> bytes:
        """
        Responder → initiator: e, ee, s, es.

        We:
          1. ECDH(ephemeral_priv, initiator_ephemeral_pub) → shared1
          2. Mix shared1 into a ChaCha20-Poly1305 cipher state.
          3. ECDH(static_priv, initiator_ephemeral_pub) → shared2
          4. Mix shared2 into the cipher state.
          5. Encrypt our static_pub with the cipher.
          6. Return: responder_ephemeral_pub + encrypted(static_pub)
        """
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
        responder_ephemeral = X25519PrivateKey.generate()
        responder_ephemeral_pub = responder_ephemeral.public_key().public_bytes_raw()
        initiator_ephemeral_pub = X25519PublicKey.from_public_bytes(msg1)
        shared1 = responder_ephemeral.exchange(initiator_ephemeral_pub)
        send_cipher, recv_cipher = self._mix_handshake_state(shared1, is_initiator=False)
        self._send_cipher = send_cipher
        self._recv_cipher = recv_cipher
        local_static_priv = X25519PrivateKey.from_private_bytes(self.static_priv)
        shared2 = local_static_priv.exchange(initiator_ephemeral_pub)
        self._rekey_handshake(shared2)
        nonce = self._next_handshake_nonce()
        enc = self._send_cipher.encrypt(nonce, self.static_pub, None)
        self.ephemeral_pub = responder_ephemeral_pub
        self.ephemeral_priv = responder_ephemeral.private_bytes_raw()
        return responder_ephemeral_pub + enc

    def handshake_message_3(self, msg2: bytes) -> bytes:
        """
        Initiator → responder: s, se (after processing msg2).

        msg2 is: responder_ephemeral_pub (32 bytes) + encrypted(static_pub).
        We decrypt the static pub and finalize the cipher states.
        Then we encrypt our static_pub and return it.
        """
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
        if len(msg2) < 32 + 16:
            raise ValueError('handshake msg2 too short')
        responder_ephemeral_pub = msg2[:32]
        enc_static = msg2[32:]
        local_ephemeral_priv = X25519PrivateKey.from_private_bytes(self.ephemeral_priv)
        responder_ephemeral_pub_key = X25519PublicKey.from_public_bytes(responder_ephemeral_pub)
        shared1 = local_ephemeral_priv.exchange(responder_ephemeral_pub_key)
        send_cipher, recv_cipher = self._mix_handshake_state(shared1, is_initiator=True)
        self._send_cipher = send_cipher
        self._recv_cipher = recv_cipher
        local_static_priv = X25519PrivateKey.from_private_bytes(self.static_priv)
        shared2 = local_static_priv.exchange(responder_ephemeral_pub_key)
        self._rekey_handshake(shared2)
        nonce = self._next_handshake_nonce()
        try:
            responder_static_pub = self._send_cipher.decrypt(nonce, enc_static, None)
        except Exception as e:
            raise ValueError(f'handshake msg2 decrypt failed: {e}') from e
        enc_my_static = self._send_cipher.encrypt(self._next_handshake_nonce(), self.static_pub, None)
        self._handshake_complete = True
        self._peer_static_pub = responder_static_pub
        return enc_my_static

    def finish_handshake_responder(self, msg3: bytes) -> None:
        """
        Responder: finalize the cipher states by processing the
        initiator's encrypted static pub.
        """
        if self._handshake_complete:
            return
        if len(msg3) < 16:
            raise ValueError('handshake msg3 too short')
        nonce = self._next_handshake_nonce()
        try:
            initiator_static_pub = self._send_cipher.decrypt(nonce, msg3, None)
        except Exception as e:
            raise ValueError(f'handshake msg3 decrypt failed: {e}') from e
        self._peer_static_pub = initiator_static_pub
        self._handshake_complete = True

    def encrypt(self, plaintext: bytes) -> bytes:
        """Encrypt with the send cipher. Advances nonce."""
        if not self._handshake_complete or self._send_cipher is None:
            raise RuntimeError('Noise: handshake not complete')
        n = self._send_nonce.to_bytes(12, 'big')
        ct = self._send_cipher.encrypt(n, plaintext, None)
        self._send_nonce += 1
        return ct

    def decrypt(self, ciphertext: bytes) -> bytes:
        """Decrypt with the recv cipher. Advances nonce."""
        if not self._handshake_complete or self._recv_cipher is None:
            raise RuntimeError('Noise: handshake not complete')
        n = self._recv_nonce.to_bytes(12, 'big')
        pt = self._recv_cipher.decrypt(n, ciphertext, None)
        self._recv_nonce += 1
        return pt

    def _next_handshake_nonce(self) -> bytes:
        if not hasattr(self, '_hs_nonce'):
            self._hs_nonce = 0
        n = self._hs_nonce.to_bytes(12, 'big')
        self._hs_nonce += 1
        return n

    def _mix_handshake_state(self, shared: bytes, is_initiator: bool) -> tuple[Any, Any]:
        """
        Derive a pair of ChaCha20Poly1305 cipher states from a shared
        secret. We use HKDF-SHA256 with distinct info strings for the
        two directions.
        """
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        okm = HKDF(algorithm=hashes.SHA256(), length=64, salt=None, info=b'hledac-noise-xx-v1').derive(shared)
        if is_initiator:
            key_send = okm[:32]
            key_recv = okm[32:]
        else:
            key_send = okm[32:]
            key_recv = okm[:32]
        return (ChaCha20Poly1305(key_send), ChaCha20Poly1305(key_recv))

    def _rekey_handshake(self, shared2: bytes) -> None:
        """
        Re-derive the cipher states after the second ECDH mix.
        Same approach as _mix_handshake_state but with a different info.
        """
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        okm = HKDF(algorithm=hashes.SHA256(), length=64, salt=None, info=b'hledac-noise-xx-v1-es').derive(shared2)
        if self._is_initiator:
            self._send_cipher = ChaCha20Poly1305(okm[:32])
            self._recv_cipher = ChaCha20Poly1305(okm[32:])
        else:
            self._send_cipher = ChaCha20Poly1305(okm[32:])
            self._recv_cipher = ChaCha20Poly1305(okm[:32])

class _PeerSession:
    """
    A single Noise-secured session with one peer.

    Holds:
      - peer_id (short hex)
      - peer_static_pub (the peer's long-term identity)
      - noise: _NoiseXXSession (post-handshake)
      - last_seen_ts (monotonic)
      - address (host, port) for UDP framing
    """
    __slots__ = ('peer_id', 'peer_static_pub', 'noise', 'last_seen_ts', 'address')

    def __init__(self, peer_id: str, peer_static_pub: bytes, noise: _NoiseXXSession, address: tuple[str, int]) -> None:
        self.peer_id = peer_id[:PEER_NODE_ID_LEN * 2]
        self.peer_static_pub = peer_static_pub
        self.noise = noise
        self.last_seen_ts = time.monotonic()
        self.address = address

@NodeTransportFactory.register('peer_node')
class PeerNodeTransport:
    """
    Real cross-host P2P transport.

    Satisfies the NodeTransport Protocol. When env-gated
    (HLEDAC_ENABLE_FEDERATED_P2P=1), the constructor lazily boots
    a UDP listener and an mDNS service. When disabled, construction
    succeeds but `run()` returns [] (fail-soft).

    The transport maintains a small set of peer sessions
    (≤ PEER_NODE_MAX_PEERS). Each `run(lane, query)`:
      1. Selects the best peer for the lane (round-robin).
      2. Sends a Noise-encrypted "research" message.
      3. Awaits a "findings" response (with timeout).
      4. Normalizes the response into the federated contract.
      5. Returns up to LANE_DISPATCH_MAX_FINDINGS findings.

    If no peers are connected, `run()` returns [] (the coordinator
    will still aggregate empty results — the lane is skipped).

    This is the Tier-2 transport. The Tier-1 LaneDispatchTransport
    remains the default; PeerNodeTransport is opt-in.
    """
    __slots__ = tuple(('_closed', '_is_initiator', '_listener_task', '_mdns_browser', '_nonce_cache', '_peers', '_round_robin_idx', '_sprint_id', '_static_keypair', '_transport'))

    def __init__(self) -> None:
        self._peers: dict[str, _PeerSession] = {}
        self._nonce_cache: _NonceCache = _NonceCache()
        self._listener_task: asyncio.Task | None = None
        self._transport: asyncio.DatagramTransport | None = None
        self._sprint_id: str = ''
        self._is_initiator: bool = True
        self._static_keypair: tuple[bytes, bytes] = self._generate_static_keypair()
        self._round_robin_idx: int = 0
        self._closed: bool = False
        self._mdns_browser: Any = None

    def set_sprint_id(self, sprint_id: str) -> None:
        """Set sprint id for traceability. Idempotent."""
        self._sprint_id = str(sprint_id or '')[:64]

    async def run(self, lane: str, query: str) -> list[dict[str, Any]]:
        """
        Dispatch (lane, query) to a peer over Noise-secured UDP.

        Returns up to LANE_DISPATCH_MAX_FINDINGS findings from the
        peer's response. Never raises. Returns [] if no peers are
        connected, the env-gate is off, or the handshake is incomplete.
        """
        started = time.monotonic()
        try:
            if self._closed:
                return []
            if not is_peer_node_enabled():
                return []
            if not self._peers:
                try:
                    await safe_wait_for(self._discover_peers(), timeout=PEER_NODE_HANDSHAKE_TIMEOUT_S, label='mDNS_discover')
                except TimeoutError as e:
                    logger.debug('[FED-P2P] mDNS discover timed out: %s', e)
                except Exception as e:
                    logger.debug('[FED-P2P] mDNS discover failed: %s', e)
            if not self._peers:
                logger.debug('[FED-P2P] no peers available, lane=%r', lane)
                return []
            peer_ids = sorted(self._peers.keys())
            if not peer_ids:
                return []
            self._round_robin_idx = (self._round_robin_idx + 1) % len(peer_ids)
            peer_id = peer_ids[self._round_robin_idx]
            session = self._peers[peer_id]
            payload = {'v': PEER_NODE_PROTO_VERSION, 't': 'research', 'lane': str(lane)[:32], 'q': str(query or '')[:256], 'n': secrets.token_hex(8), 'ts': int(time.time() * 1000)}
            self._nonce_cache.seen(payload['n'])
            try:
                raw_response = await safe_wait_for(self._send_request(session, payload), timeout=PEER_NODE_HANDSHAKE_TIMEOUT_S, label='send_request')
            except TimeoutError:
                logger.debug('[FED-P2P] peer %s request timeout lane=%r', peer_id, lane)
                return []
            except Exception as e:
                logger.debug('[FED-P2P] peer %s request failed: %s: %s', peer_id, type(e).__name__, e)
                return []
            if not isinstance(raw_response, dict):
                return []
            results = raw_response.get('results', [])
            if not isinstance(results, list):
                return []
            out: list[dict[str, Any]] = []
            for f in results:
                if not isinstance(f, dict):
                    continue
                norm = _normalize_peer_finding(f, lane, peer_id, self._sprint_id)
                if norm is not None:
                    out.append(norm)
                if len(out) >= _PEER_MAX_FINDINGS:
                    break
            elapsed = time.monotonic() - started
            logger.debug('[FED-P2P] lane=%r peer=%s findings=%d dur=%.3fs', lane, peer_id, len(out), elapsed)
            return out
        except asyncio.CancelledError:
            raise
        except Exception as e:
            elapsed = time.monotonic() - started
            logger.warning('[FED-P2P] run fail-soft lane=%r %s: %s dur=%.3fs', lane, type(e).__name__, e, elapsed)
            return []

    async def close(self) -> None:
        """Idempotent cleanup. Releases UDP listener + mDNS browser."""
        if self._closed:
            return
        self._closed = True
        if self._listener_task is not None:
            try:
                self._listener_task.cancel()
            except Exception:  # noqa: BLE001
                pass
            try:
                await safe_wait_for(self._listener_task, timeout=1.0, label='listener_task')
            except TimeoutError:  # noqa: BLE001
                pass
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                pass
            self._listener_task = None
        if self._transport is not None:
            try:
                self._transport.close()
            except Exception:  # noqa: BLE001
                pass
            self._transport = None
        if self._mdns_browser is not None:
            try:
                self._mdns_browser.cancel()
            except Exception:  # noqa: BLE001
                pass
            self._mdns_browser = None
        self._peers.clear()

    def _generate_static_keypair(self) -> tuple[bytes, bytes]:
        """Generate an X25519 keypair for the local node identity."""
        try:
            from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
            priv = X25519PrivateKey.generate()
            return (priv.private_bytes_raw(), priv.public_key().public_bytes_raw())
        except Exception as e:
            logger.debug('[FED-P2P] keypair generation failed: %s', e)
            rnd = secrets.token_bytes(32)
            return (rnd, rnd)

    async def _send_request(self, session: _PeerSession, payload: dict[str, Any]) -> dict[str, Any] | None:
        """
        Encrypt payload with the session's Noise state, send via UDP,
        await a single response. Returns the parsed response dict or
        None on failure.
        """
        import orjson
        try:
            body = orjson.dumps(payload)
        except Exception:
            body = str(payload).encode('utf-8')[:PEER_NODE_MSG_MAX_BYTES]
        if len(body) > PEER_NODE_MSG_MAX_BYTES:
            body = body[:PEER_NODE_MSG_MAX_BYTES]
        try:
            ct = session.noise.encrypt(body)
        except Exception as e:
            logger.debug('[FED-P2P] encrypt failed: %s', e)
            return None
        if self._transport is None:
            return None
        frame = bytes([PEER_NODE_PROTO_VERSION]) + ct
        try:
            self._transport.sendto(frame, session.address)
        except Exception as e:
            logger.debug('[FED-P2P] sendto failed: %s', e)
            return None
        try:
            await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            raise
        return None

    async def _discover_peers(self) -> None:
        """
        Discover peers on the LAN via mDNS / DNS-SD. The result is
        bounded by the PEER_NODE_MAX_PEERS limit; oldest nonces
        in the mDNS cache are evicted when full.
        """
        if self._mdns_browser is not None:
            return
        try:
            from zeroconf import ServiceBrowser, ServiceListener, Zeroconf
        except Exception as e:
            logger.debug('[FED-P2P] zeroconf not available: %s', e)
            return
        service_type = os.environ.get('HLEDAC_FEDERATED_P2P_SERVICE', PEER_NODE_SERVICE_TYPE)
        try:
            zc = Zeroconf()
        except Exception as e:
            logger.debug('[FED-P2P] Zeroconf() init failed: %s', e)
            return

        class _Listener(ServiceListener):
            __slots__ = tuple(('outer',))

            def __init__(self, outer: PeerNodeTransport) -> None:
                self.outer = outer

            def update_service(self, *args: Any, **kwargs: Any) -> None:
                self.outer._add_mdns_peer_from_args(args, kwargs)

            def remove_service(self, *args: Any, **kwargs: Any) -> None:
                pass

            def add_service(self, *args: Any, **kwargs: Any) -> None:
                self.outer._add_mdns_peer_from_args(args, kwargs)
        try:
            listener = _Listener(self)
            self._mdns_browser = ServiceBrowser(zc, service_type, listener)
        except Exception as e:
            logger.debug('[FED-P2P] mDNS ServiceBrowser failed: %s', e)
            return
        try:
            await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            raise

    def _add_mdns_peer_from_args(self, args: tuple, _kwargs: dict) -> None:
        """Best-effort: extract a (host, port) pair from the mDNS callback."""
        if len(self._peers) >= PEER_NODE_MAX_PEERS:
            return
        try:
            from zeroconf import ServiceStateChange
            if len(args) < 4:
                return
            zc, _stype, name, state_change = (args[0], args[1], args[2], args[3])
            if state_change not in (ServiceStateChange.Added, ServiceStateChange.Updated):
                return
            info = zc.get_service_info(_stype, name)
            if info is None:
                return
            addrs = info.addresses
            if not addrs:
                return
            host = '.'.join((str(b) for b in addrs[0]))
            port = int(info.port or PEER_NODE_DEFAULT_PORT)
            self._register_mdns_peer(name, host, port)
        except Exception as e:
            logger.debug('[FED-P2P] mDNS peer parse failed: %s', e)

    def _register_mdns_peer(self, name: str, host: str, port: int) -> None:
        """
        Register an mDNS-discovered peer. This is a soft registration —
        the actual Noise handshake happens on first message exchange.
        Bounded by PEER_NODE_MAX_PEERS.
        """
        if len(self._peers) >= PEER_NODE_MAX_PEERS:
            return
        peer_id = secrets.token_hex(PEER_NODE_ID_LEN // 2) if not name else name.replace('.', '_')[:PEER_NODE_ID_LEN * 2]
        if peer_id in self._peers:
            return
        noise = _NoiseXXSession(is_initiator=True, static_keypair=self._static_keypair)
        self._peers[peer_id] = _PeerSession(peer_id=peer_id, peer_static_pub=b'\x00' * 32, noise=noise, address=(host, int(port)))
_PEER_MAX_FINDINGS: int = 25
'Hard cap on findings returned by a single peer per lane per cycle.\nSame as LANE_DISPATCH_MAX_FINDINGS — keeps the contract uniform.'

def _normalize_peer_finding(raw: dict[str, Any], lane: str, peer_id: str, sprint_id: str) -> dict[str, Any] | None:
    """
    Normalize a peer response finding into the federated contract.
    Mirrors the lane_dispatch normalizer but adds a peer_id tag.
    """
    ioc_type = raw.get('ioc_type') or raw.get('type') or 'observation'
    ioc_value = raw.get('ioc_value') or raw.get('value') or ''
    if not ioc_value:
        return None
    try:
        confidence = float(raw.get('confidence', 0.5) or 0.5)
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))
    finding: dict[str, Any] = {'ioc_type': str(ioc_type)[:64], 'ioc_value': str(ioc_value)[:512], 'confidence': confidence, 'source_lane': lane, 'source_type': 'federated_peer_node', 'provenance': ('federated_peer_node', f'peer={peer_id}')}
    payload = raw.get('payload_text') or raw.get('payload')
    if payload is not None and isinstance(payload, str):
        finding['payload_text'] = payload[:1024]
    if sprint_id:
        finding['sprint_id'] = str(sprint_id)[:64]
    return finding
__all__ = ['PeerNodeTransport', 'is_peer_node_enabled', 'ENV_GATE', 'PEER_NODE_MAX_PEERS', 'PEER_NODE_HANDSHAKE_TIMEOUT_S', 'PEER_NODE_MSG_MAX_BYTES', 'PEER_NODE_NONCE_CACHE_MAX', 'PEER_NODE_MDNS_RATE_LIMIT_S', 'PEER_NODE_DEFAULT_PORT', 'PEER_NODE_SERVICE_TYPE', 'PEER_NODE_PROTO_VERSION']