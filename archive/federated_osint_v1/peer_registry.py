from typing import Dict, Optional, Any
import time


class PeerRegistry:
    def __init__(self):
        self._peers: Dict[str, Dict[str, Any]] = {}

    def add_peer(self, peer_id: str, tor_endpoint: Optional[str] = None,
                 nym_endpoint: Optional[str] = None, public_key: Optional[bytes] = None):
        entry = self._peers.setdefault(peer_id, {})
        if tor_endpoint:
            entry['tor'] = tor_endpoint
        if nym_endpoint:
            entry['nym'] = nym_endpoint
        if public_key:
            entry['public_key'] = public_key
        entry['last_seen'] = time.time()

    def get_peer(self, peer_id: str) -> Optional[Dict[str, Any]]:
        return self._peers.get(peer_id)

    def get_endpoint(self, peer_id: str, transport_type: str) -> Optional[str]:
        peer = self._peers.get(peer_id)
        if peer:
            return peer.get(transport_type)
        return None

    def remove_peer(self, peer_id: str):
        self._peers.pop(peer_id, None)

    def get_all_peers(self) -> Dict[str, Dict[str, Any]]:
        return self._peers.copy()
