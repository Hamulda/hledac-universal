"""
Secure aggregation s dvěma režimy:
- MASKING: rychlé párové masky (algebraické zrušení)
- SHAMIR: Shamir secret sharing v mod p s kvantizací pro toleranci výpadků
"""

import numpy as np
import mlx.core as mx
from typing import Dict, List, Optional
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes

# Prvočíslo pro modulární aritmetiku (2^31 - 1, prime)
P = 2**31 - 1


class SecureAggregator:
    """Secure aggregation s masking a Shamir režimy."""

    def __init__(self, node_id: str, peer_ids: List[str], mode: str = 'masking', threshold: int = 2):
        self.node_id = node_id
        self.peer_ids = sorted(peer_ids)
        self.mode = mode
        self.threshold = threshold
        self.mask_seeds: Dict[tuple, bytes] = {}
        self.session_keys: Dict[str, bytes] = {}

    def set_peer_ids(self, peer_ids: List[str]):
        self.peer_ids = sorted(peer_ids)

    def set_session_key(self, peer_id: str, key: bytes):
        self.session_keys[peer_id] = key
        key_pair = tuple(sorted([self.node_id, peer_id]))
        self.mask_seeds[key_pair] = key

    # ===== PRG (HKDF counter mode) =====
    def _hkdf_expand(self, prk: bytes, info: bytes, length: int) -> bytes:
        """HKDF expand v counter módu pro libovolné délky."""
        from cryptography.hazmat.primitives.hmac import HMAC
        from cryptography.hazmat.primitives import hashes

        hash_len = hashes.SHA256().digest_size  # 32
        output = b''
        counter = 1
        while len(output) < length:
            h = HMAC(prk, hashes.SHA256())
            h.update(output[-hash_len:] if counter > 1 else info + bytes([counter]))
            output += h.finalize()
            counter += 1
        return output[:length]

    def _prg_bytes(self, seed: bytes, info: bytes, length: int) -> bytes:
        """HKDF expand v counter módu (HMAC-SHA256 jako stream)."""
        hkdf = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=info)
        prk = hkdf.derive(seed)  # pseudorandom key
        return self._hkdf_expand(prk, info, length)

    def _derive_mask_tensor(self, seed: bytes, tensor_name: str, shape: tuple, round: int) -> np.ndarray:
        """Generuje masku jako numpy array (float32)."""
        info = f"{tensor_name}:{round}".encode()
        num_floats = int(np.prod(shape))
        num_bytes = num_floats * 4
        random_bytes = self._prg_bytes(seed, info, num_bytes)
        u32 = np.frombuffer(random_bytes, dtype=np.uint32)
        u = u32 / 2**32  # [0,1)
        scale = 100.0
        mask_np = (u * 2 * scale - scale).reshape(shape).astype(np.float32)
        return mask_np

    # ===== MASKING režim =====
    def create_masked_update(self, update: Dict[str, mx.array], round: int) -> Dict[str, mx.array]:
        """Vytvoří maskovaný update pro masking režim."""
        # Vyhodnotíme tensory
        mx.eval(*update.values())
        update_np = {k: np.array(v) for k, v in update.items()}
        masked = {k: v.copy() for k, v in update_np.items()}

        for peer in self.peer_ids:
            if peer == self.node_id:
                continue
            sign = 1 if self.node_id < peer else -1
            key_pair = tuple(sorted([self.node_id, peer]))
            seed = self.mask_seeds.get(key_pair)
            if seed is None:
                continue
            for tname, tensor in update_np.items():
                mask = self._derive_mask_tensor(seed, tname, tensor.shape, round)
                masked[tname] = masked[tname] + sign * mask

        return {k: mx.array(v) for k, v in masked.items()}

    # ===== SHAMIR režim (mod p) =====
    def _quantize(self, arr: np.ndarray, scale: int = 2**20) -> np.ndarray:
        """Kvantizace float32 na int64 s daným scaling faktorem."""
        return np.clip(np.round(arr * scale), -scale, scale - 1).astype(np.int64)

    def _dequantize(self, arr: np.ndarray, scale: int = 2**20) -> np.ndarray:
        """Dekvantizace int64 zpět na float32."""
        return (arr / scale).astype(np.float32)

    def _mod_inv(self, a: int) -> int:
        """Modulární inverze vzhledem k P (Fermatův teorém)."""
        return pow(a, P - 2, P)

    def create_shamir_shares(self, update: Dict[str, mx.array], round: int) -> Dict[str, Dict]:
        """
        Kvantizace update, rozdělení na threshold shareů v mod p.
        Vrací {peer_id: {tensor_name: share (np.int64)}}
        """
        mx.eval(*update.values())
        update_np = {k: np.array(v) for k, v in update.items()}
        quantized = {k: self._quantize(v) for k, v in update_np.items()}

        shares = {peer: {} for peer in self.peer_ids}

        for tname, tensor in quantized.items():
            # Koeficienty polynomu: prvním je samotný tensor, ostatní náhodné z rozsahu 0..P-1
            coeffs = [tensor] + [
                np.random.randint(0, P, size=tensor.shape, dtype=np.int64)
                for _ in range(self.threshold - 1)
            ]
            for idx, peer in enumerate(self.peer_ids):
                x = idx + 1
                share = np.zeros_like(tensor, dtype=np.int64)
                x_pow = 1
                for c in coeffs:
                    share = (share + c * x_pow) % P
                    x_pow = (x_pow * x) % P
                shares[peer][tname] = share

        return shares

    def aggregate_shamir_shares(self, received_shares: Dict[str, Dict]) -> Optional[Dict[str, mx.array]]:
        """
        Agregace shareů, Lagrange interpolace v bodě 0 pomocí modulární aritmetiky.
        """
        if len(received_shares) < self.threshold:
            return None

        sample = next(iter(received_shares.values()))
        result = {}

        for tname in sample.keys():
            total = np.zeros_like(sample[tname], dtype=np.int64)
            # Seznam indexů peerů, kteří přispěli
            peer_idxs = [self.peer_ids.index(pid) + 1 for pid in received_shares.keys()]

            for pid, tensors in received_shares.items():
                idx = self.peer_ids.index(pid) + 1
                # Lagrangeův koeficient pro bod 0
                num, den = 1, 1
                for j in peer_idxs:
                    if j == idx:
                        continue
                    num = (num * (0 - j)) % P
                    den = (den * (idx - j)) % P
                lagrange = (num * self._mod_inv(den)) % P
                # Přispěvek tohoto peera
                contrib = (tensors[tname].astype(np.int64) * lagrange) % P
                total = (total + contrib) % P

            # Dekvantizace
            float_val = self._dequantize(total)
            result[tname] = mx.array(float_val)

        return result
