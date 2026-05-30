"""
BEP-9: Extension protocol for fetching torrent metadata without downloading torrent file.

Protocol: TCP connection to peer → handshake with extension protocol →
 request ut_metadata → receive metadata pieces → parse bencode info dict

M1 constraint: Max 5 concurrent metadata fetches.
"""
from __future__ import annotations

import asyncio
import hashlib
import struct
from dataclasses import dataclass, field
from typing import Any, Optional
import logging

logger = logging.getLogger(__name__)

# BEP-9 Constants
UT_METADATA_ID = 1
METADATA_PIECE_SIZE = 16384  # 16KB per piece
BEP_9_TIMEOUT = 30.0
MAX_CONCURRENT_FETCHES = 5
MAX_PEERS_TO_TRY = 3

# BitTorrent protocol constants
BT_HEADER_SIZE = 68
PROTOCOL_STRING = b"BitTorrent protocol"
BT_EXTENDED_FLAG =0x10


@dataclass
class TorrentInfo:
    """Parsed torrent metadata."""
    name: str
    files: list[dict]  # [{path, length}]
    total_size: int
    piece_length: int
    pieces: bytes
    trackers: list[str]
    creation_date: Optional[int] = None
    created_by: Optional[str] = None
    comment: Optional[str] = None


@dataclass
class TorrentMetadataFetcher:
    """BEP-9: Fetch torrent metadata via extended BitTorrent handshake.

    Uses TCP connections to peers that support the ut_metadata extension.
    Reassembles metadata pieces and verifies SHA1 hash matches infohash.
    """
    _semaphore: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(MAX_CONCURRENT_FETCHES))
    _cache: dict = field(default_factory=dict)

    async def fetch_metadata(
        self,
        infohash: bytes,
        peers: list[tuple[str, int]],
        timeout: float = BEP_9_TIMEOUT
    ) -> Optional[TorrentInfo]:
        """Fetch torrent metadata from available peers.

        Args:
            infohash: 20-byte torrent info hash
            peers: List of (ip, port) tuples from BEP-5 get_peers()
            timeout: Request timeout in seconds

        Returns:
            TorrentInfo if successfully fetched and verified, None otherwise
        """
        if not infohash or len(infohash) != 20:
            logger.warning(f"Invalid infohash length: {len(infohash) if infohash else 0}")
            return None

        # Check cache
        cache_key = infohash.hex()
        if cache_key in self._cache:
            return self._cache[cache_key]

        async with self._semaphore:
            # Try up to MAX_PEERS_TO_TRY peers
            for ip, port in peers[:MAX_PEERS_TO_TRY]:
                try:
                    info = await self._try_peer(ip, port, infohash, timeout)
                    if info:
                        self._cache[cache_key] = info
                        return info
                except Exception as e:
                    logger.debug(f"Peer {ip}:{port} failed: {e}")
                    continue

        return None

    async def _try_peer(
        self,
        ip: str,
        port: int,
        infohash: bytes,
        timeout: float
    ) -> Optional[TorrentInfo]:
        """Attempt to fetch metadata from a single peer."""
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(ip, port),
                timeout=min(10.0, timeout)
            )
        except Exception:
            return None

        try:
            # Step 1: BitTorrent handshake with extension protocol
            reserved = bytearray(8)
            reserved[5] = BT_EXTENDED_FLAG  # Set extended protocol bit
            handshake = (
                PROTOCOL_STRING +
                bytes(reserved) +
                infohash +
                b"\x00" * 20  # peer_id
            )
            writer.write(handshake)

            # Step 2: Read handshake response
            response = await asyncio.wait_for(reader.readexactly(BT_HEADER_SIZE), timeout=10.0)
            if len(response) < BT_HEADER_SIZE:
                return None

            # Check if peer supports extension protocol
            if not (response[25] & BT_EXTENDED_FLAG):
                logger.debug(f"Peer {ip} does not support extension protocol")
                return None

            peer_reserved = response[5:13]
            extended_id = peer_reserved[7] if len(peer_reserved) > 7 else 0

            # Step 3: Send extended handshake
            import json
            ext_handshake = {
                b"m": {
                    b"ut_metadata": UT_METADATA_ID
                }
            }
            ext_msg = self._build_extended_message(20, ext_handshake)  # 20 = handshake
            writer.write(ext_msg)

            # Step 4: Read extended handshake response
            metadata_size = None
            ut_metadata_id = None

            while True:
                try:
                    msg = await asyncio.wait_for(reader.readexactly(6), timeout=10.0)
                    if len(msg) < 4:
                        break
                    msg_len = struct.unpack(">I", msg[:4])[0]
                    if msg_len > 1024 * 1024:  # Sanity check
                        break

                    msg_data = await asyncio.wait_for(reader.readexactly(msg_len), timeout=5.0)
                    msg_id = msg_data[0] if msg_data else 0

                    if msg_id == 20:  # Extended message
                        metadata_size, ut_metadata_id = self._parse_extended_handshake(msg_data[1:])
                        if metadata_size and ut_metadata_id:
                            break
                except asyncio.TimeoutError:
                    break

            if not metadata_size or not ut_metadata_id:
                return None

            # Step 5: Request metadata pieces
            num_pieces = (metadata_size + METADATA_PIECE_SIZE - 1) // METADATA_PIECE_SIZE
            metadata_pieces: list[bytes] = [b""] * num_pieces
            received_count = 0

            for piece_idx in range(num_pieces):
                request = {
                    b"msg_type": 0,  # request
                    b"piece": piece_idx
                }
                req_msg = self._build_extended_message(ut_metadata_id, request)
                writer.write(req_msg)

            # Step 6: Receive metadata pieces
            deadline = asyncio.get_event_loop().time() + timeout
            remaining = num_pieces

            while remaining > 0:
                remaining_time = max(1.0, deadline - asyncio.get_event_loop().time())
                try:
                    header = await asyncio.wait_for(reader.readexactly(6), timeout=remaining_time)
                    msg_len = struct.unpack(">I", header[:4])[0]
                    if msg_len > METADATA_PIECE_SIZE + 100:
                        break

                    msg_data = await asyncio.wait_for(reader.readexactly(msg_len), timeout=5.0)
                    msg_id = msg_data[0] if msg_data else 0

                    if msg_id == ut_metadata_id:
                        piece_idx, piece_data = self._parse_metadata_piece(msg_data[1:])
                        if 0 <= piece_idx < num_pieces and metadata_pieces[piece_idx] == b"":
                            metadata_pieces[piece_idx] = piece_data
                            remaining -= 1
                            received_count += 1
                except asyncio.TimeoutError:
                    break

            # Reassemble and verify
            full_metadata = b"".join(metadata_pieces)
            if not full_metadata:
                return None

            # Verify SHA1
            computed_hash = hashlib.sha1(full_metadata).digest()
            if computed_hash != infohash:
                logger.warning(f"SHA1 mismatch for {infohash.hex()[:8]}")
                return None

            # Bencode decode
            info = self._decode_bencode(full_metadata)
            if not info:
                return None

            return self._parse_torrent_info(info, full_metadata)

        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    def _build_extended_message(self, msg_id: int, payload: dict) -> bytes:
        """Build a bencode-encoded extended message."""
        bencoded = self._bencode(payload)
        msg = bytes([msg_id]) + bencoded
        return struct.pack(">I", len(msg)) + msg

    def _parse_extended_handshake(self, data: bytes) -> tuple[Optional[int], Optional[int]]:
        """Parse extended handshake to extract metadata_size and ut_metadata_id."""
        try:
            decoded = self._decode_bencode(data)
            if not isinstance(decoded, dict):
                return None, None

            m = decoded.get(b"m", decoded.get("m", {}))
            ut_id = m.get(b"ut_metadata", m.get("ut_metadata"))
            if ut_id is None:
                return None, None

            metadata_size = decoded.get(b"metadata_size", decoded.get("metadata_size"))
            return metadata_size, ut_id
        except Exception:
            return None, None

    def _parse_metadata_piece(self, data: bytes) -> tuple[int, bytes]:
        """Parse a metadata piece message."""
        try:
            decoded = self._decode_bencode(data)
            if isinstance(decoded, dict):
                msg_type = decoded.get(b"msg_type", decoded.get("msg_type", 1))
                piece = decoded.get(b"piece", decoded.get("piece", 0))
                if msg_type == 1:  # data message
                    piece_data = decoded.get(b"metadata", decoded.get("metadata", b""))
                    return piece, piece_data
            return 0, b""
        except Exception:
            return 0, b""

    def _bencode(self, obj) -> bytes:
        """Simple bencode encoder."""
        if isinstance(obj, int):
            return b"i" + str(obj).encode() + b"e"
        elif isinstance(obj, bytes):
            return str(len(obj)).encode() + b":" + obj
        elif isinstance(obj, str):
            return self._bencode(obj.encode())
        elif isinstance(obj, list):
            result = b"l"
            for item in obj:
                result += self._bencode(item)
            return result + b"e"
        elif isinstance(obj, dict):
            result = b"d"
            for key in sorted(obj.keys()):
                result += self._bencode(key) + self._bencode(obj[key])
            return result + b"e"
        else:
            return self._bencode(str(obj))

    def _decode_bencode(self, data: bytes) -> Any:
        """Decode bencoded data."""
        return self._decode_bencode_iter(data, 0)[0]

    def _decode_bencode_iter(self, data: bytes, pos: int) -> tuple[Any, int]:
        """Iterative bencode decoder."""
        if pos >= len(data):
            return None, pos

        char = chr(data[pos])

        if char == "i":
            end = data.index(b"e", pos)
            return int(data[pos + 1:end]), end + 1
        elif char == "l":
            pos += 1
            result = []
            while data[pos] != ord("e"):
                item, pos = self._decode_bencode_iter(data, pos)
                result.append(item)
            return result, pos + 1
        elif char == "d":
            pos += 1
            result = {}
            while data[pos] != ord("e"):
                key, pos = self._decode_bencode_iter(data, pos)
                value, pos = self._decode_bencode_iter(data, pos)
                result[key] = value
            return result, pos + 1
        elif char.isdigit():
            colon = data.index(b":", pos)
            length = int(data[pos:colon])
            return data[colon + 1:colon + 1 + length], colon + 1 + length
        else:
            return None, pos + 1

    def _parse_torrent_info(self, info: dict, _raw_info: bytes) -> TorrentInfo:  # noqa: ARG002
        """Parse bencoded info dict into TorrentInfo."""
        name = info.get(b"name", info.get("name", "unknown"))
        if isinstance(name, bytes):
            name = name.decode("utf-8", errors="replace")

        piece_length = info.get(b"piece length", info.get("piece_length", 0))
        pieces = info.get(b"pieces", info.get("pieces", b""))
        if isinstance(pieces, str):
            pieces = pieces.encode()

        # Parse files
        files = []
        total_size = 0

        if b"files" in info:
            for f in info[b"files"]:
                path_parts = [p.decode("utf-8", errors="replace") for p in f.get(b"path", f.get("path", []))]
                length = f.get(b"length", f.get("length", 0))
                files.append({
                    "path": "/".join(path_parts) if path_parts else "unknown",
                    "length": length
                })
                total_size += length
        else:
            length = info.get(b"length", info.get("length", 0))
            files.append({"path": name, "length": length})
            total_size = length

        # Trackers from top-level (if present)
        trackers = []
        if b"announce" in info:
            tracker = info[b"announce"]
            if isinstance(tracker, bytes):
                tracker = tracker.decode("utf-8", errors="replace")
            trackers.append(tracker)

        return TorrentInfo(
            name=name,
            files=files,
            total_size=total_size,
            piece_length=piece_length,
            pieces=pieces,
            trackers=trackers
        )

    def clear_cache(self) -> None:
        """Clear metadata cache."""
        self._cache.clear()

    def extract_intel_from_torrent(
        self,
        info: TorrentInfo,
        infohash: str
    ) -> list[dict]:
        """Convert torrent metadata to OSINT findings.

        Args:
            info: Parsed torrent metadata
            infohash: Hex info hash string

        Returns:
            List of dict findings with source_type="dht_metadata"
        """
        findings = []

        # File names → potential leaked data indicators
        for f in info.files:
            path = f.get("path", "")
            if len(path) > 3:  # Skip very short names
                findings.append({
                    "source_type": "dht_metadata",
                    "type": "file_name",
                    "value": path,
                    "size_bytes": f.get("length", 0),
                    "infohash": infohash,
                    "torrent_name": info.name,
                    "confidence": "medium"
                })

        # Directory structure → organizational pattern signals
        if len(info.files) > 1:
            dir_parts = []
            for f in info.files[:5]:
                path = f.get("path", "")
                if isinstance(path, str):
                    parts = path.split("/")[:-1]
                    if parts:
                        dir_parts.append("/".join(parts))
            findings.append({
                "source_type": "dht_metadata",
                "type": "directory_structure",
                "value": "; ".join(dir_parts) if dir_parts else "",
                "file_count": len(info.files),
                "infohash": infohash,
                "torrent_name": info.name,
                "confidence": "low"
            })

        # Total size → data exfiltration scale estimate
        findings.append({
            "source_type": "dht_metadata",
            "type": "total_size",
            "value": info.total_size,
            "human_readable": self._format_size(info.total_size),
            "infohash": infohash,
            "torrent_name": info.name,
            "confidence": "medium"
        })

        # Tracker list → infrastructure indicators
        for tracker in info.trackers:
            findings.append({
                "source_type": "dht_metadata",
                "type": "tracker",
                "value": tracker,
                "infohash": infohash,
                "torrent_name": info.name,
                "confidence": "high"
            })

        return findings

    def _format_size(self, size_bytes: float) -> str:
        """Format bytes to human readable string."""
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if abs(size_bytes) < 1024:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024
        return f"{size_bytes:.1f} PB"
