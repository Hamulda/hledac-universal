"""
SourceBandit – UCB1 bandit pro adaptivní výběr zdrojů.
Perzistentní v LMDB, učí se napříč běhy.
Sprint 42: LinUCB contextual bandit přidán.
Sprint 43: Geo + Language context features (14 dim).
Sprint 3D: Uses open_lmdb() from paths.py for env-driven discipline.
"""

import json
import logging
import math
from pathlib import Path
from typing import Dict, List, Optional, Any

import numpy as np

# Sprint 8AR: Use LMDB_ROOT from paths.py for OPSEC compliance (lazy to avoid import regression)
# Sprint 3D: Also import open_lmdb for env-driven discipline
_LMDB_ROOT = None
_open_lmdb = None  # lazy import

logger = logging.getLogger(__name__)

# Sprint 42: LinUCB constants
# Sprint 43: Extended to 14 features (8 base + 6 geo/lang)
N_FEATURES = 14
MIN_LINUCB_SAMPLES = 5

# Sprint 45: MessagePack for faster serialization
try:
    from .serialization import pack, unpack
    MSGPACK_AVAILABLE = True
except ImportError:
    MSGPACK_AVAILABLE = False
    logger.warning("[MSGPACK] not available, using JSON")


class LinUCBArm:
    """Linear UCB arm with online update."""

    def __init__(self, n_features: int, alpha: float = 0.5):
        self.A = np.eye(n_features)  # n×n matrix, invertible
        self.b = np.zeros(n_features)  # reward vector
        self.alpha = alpha
        self.n_features = n_features

    def select(self, context: np.ndarray) -> float:
        """Compute UCB score for this arm given context."""
        A_inv = np.linalg.inv(self.A)
        theta = A_inv @ self.b
        ucb = theta @ context + self.alpha * np.sqrt(context @ A_inv @ context)
        return float(ucb)

    def update(self, context: np.ndarray, reward: float) -> None:
        """Online update via linear regression."""
        self.A += np.outer(context, context)
        self.b += reward * context

    def to_dict(self) -> dict:
        return {'A': self.A.tolist(), 'b': self.b.tolist(), 'alpha': self.alpha}

    @classmethod
    def from_dict(cls, d: dict, n_features: int) -> 'LinUCBArm':
        arm = cls(n_features, d['alpha'])
        arm.A = np.array(d['A'])
        arm.b = np.array(d['b'])
        return arm


def _extract_base_features(analysis: Dict[str, Any]) -> list:
    """Sprint 42 features (8 dims)."""
    intent = analysis.get('intent', 'other').lower()
    query = analysis.get('query', '')
    return [
        1.0 if 'factual' in intent else 0.0,
        1.0 if 'investigat' in intent else 0.0,
        1.0 if 'tech' in intent else 0.0,
        1.0 if 'monitor' in intent else 0.0,
        1.0 if all(k not in intent for k in ['factual', 'investigat', 'tech', 'monitor']) else 0.0,
        min(len(query), 200) / 200.0,
        1.0 if analysis.get('entities') else 0.0,
        1.0 if analysis.get('temporal_scope') else 0.0,
    ]


def extract_context_features(analysis: Optional[Dict[str, Any]]) -> np.ndarray:
    """
    Returns 14-dim feature vector:
    [0-7] base features (intent, query_length, entities, temporal)
    [8] geo_eu (EU/European keywords)
    [9] geo_us (US/American keywords)
    [10] geo_ru (Russian keywords)
    [11] lang_cz (Czech diacritics)
    [12] lang_ru (Cyrillic)
    [13] lang_de (German umlauts)
    """
    if analysis is None:
        return np.ones(N_FEATURES, dtype=np.float64) * 0.5

    base = _extract_base_features(analysis)

    query_lower = analysis.get('query', '').lower()
    # Geo keywords [8-10]
    geo_eu = 1.0 if any(w in query_lower for w in ['eu', 'euro', 'brusel', 'praha', 'berlin', 'paris', 'london', 'warsaw']) else 0.0
    geo_us = 1.0 if any(w in query_lower for w in ['usa', 'washington', 'ny', 'california', 'texas', 'new york']) else 0.0
    geo_ru = 1.0 if any(w in query_lower for w in ['moscow', 'kreml', 'putin', 'россия', 'kremlin']) else 0.0

    # Language flags [11-13]
    lang_cz = 1.0 if any(ord('á') <= ord(c) <= ord('ů') for c in query_lower) else 0.0
    lang_ru = 1.0 if any(0x0400 <= ord(c) <= 0x04FF for c in query_lower) else 0.0
    lang_de = 1.0 if any(c in 'äöüß' for c in query_lower) else 0.0

    geo_lang = [geo_eu, geo_us, geo_ru, lang_cz, lang_ru, lang_de]
    return np.array(base + geo_lang, dtype=np.float64)


class SourceBandit:
    """UCB1 bandit pro source selection s LMDB persistence."""

    SOURCES = ['web', 'academic', 'darkweb', 'archive', 'blockchain', 'osint']
    LMDB_MAP_SIZE = 10 * 1024 * 1024  # 10 MB

    def __init__(self, lmdb_path: Optional[Path] = None):
        # Sprint 8AR: Lazy import to avoid cold-start regression.
        # Sprint 3D: Also lazily import open_lmdb
        global _LMDB_ROOT, _open_lmdb
        if _LMDB_ROOT is None:
            try:
                from ..paths import LMDB_ROOT as _LMDB_ROOT, open_lmdb as _open_lmdb
            except ImportError:
                _LMDB_ROOT = None
                _open_lmdb = None
        if lmdb_path is None:
            if _LMDB_ROOT is not None:
                lmdb_path = _LMDB_ROOT / 'bandit.lmdb'
            else:
                # Fallback: legacy path (will be outside declared root in OPSEC-degraded mode)
                lmdb_path = Path.home() / '.hledac' / 'bandit.lmdb'
        lmdb_path.parent.mkdir(parents=True, exist_ok=True)

        # Sprint 3D: use open_lmdb() for env-driven discipline + lock recovery
        if _open_lmdb is not None:
            self._env = _open_lmdb(
                lmdb_path,
                map_size=self.LMDB_MAP_SIZE,
                max_dbs=1,
                writemap=False,
                metasync=True
            )
        else:
            # Sprint 3D fallback: direct lmdb.open for backward compat
            import lmdb
            self._env = lmdb.open(
                str(lmdb_path),
                map_size=self.LMDB_MAP_SIZE,
                max_dbs=1,
                writemap=False,
                metasync=True
            )
        self._stats = self._load()
        # Sprint 42: LinUCB arms
        self._linucb_arms: Dict[str, LinUCBArm] = {}
        self._counts: Dict[str, int] = {}
        self._rewards: Dict[str, float] = {}
        self._load_linucb()

    def _load(self) -> Dict[str, Dict[str, float]]:
        """Načte statistiky z LMDB."""
        stats = {s: {'pulls': 0, 'rewards': 0.0} for s in self.SOURCES}
        try:
            with self._env.begin() as txn:
                for src in self.SOURCES:
                    val = txn.get(src.encode())
                    if val:
                        loaded = json.loads(val.decode())
                        # Ošetření starších formátů
                        stats[src]['pulls'] = loaded.get('pulls', 0)
                        stats[src]['rewards'] = loaded.get('rewards', 0.0)
        except Exception as e:
            logger.warning(f"[BANDIT] Failed to load stats: {e}")
        return stats

    def _save(self, source: str):
        """Uloží statistiku pro jeden zdroj do LMDB."""
        try:
            with self._env.begin(write=True) as txn:
                txn.put(
                    source.encode(),
                    json.dumps(self._stats[source]).encode()
                )
        except Exception as e:
            logger.warning(f"[BANDIT] Failed to save {source}: {e}")

    def select(self, n: int = 3) -> List[str]:
        """
        UCB1 selection – vrací top-n zdrojů.
        Pokud některý zdroj nemá pulls, má nekonečný score (explore).
        """
        total_pulls = sum(s['pulls'] for s in self._stats.values()) + 1  # +1 pro log

        scored = []
        for src, s in self._stats.items():
            if s['pulls'] == 0:
                # Nevyzkoušený zdroj → maximální score (explore)
                score = float('inf')
            else:
                mean_reward = s['rewards'] / s['pulls']
                # UCB1 vzorec: mean + sqrt(2 * ln(total) / n_pulls)
                exploration = math.sqrt(2 * math.log(total_pulls) / s['pulls'])
                score = mean_reward + exploration
            scored.append((score, src))

        # Sestupně podle score
        scored.sort(reverse=True)
        selected = [src for _, src in scored[:n]]
        logger.debug(f"[BANDIT] selected={selected}")
        return selected

    def update(self, source: str, reward: float):
        """
        Aktualizuje statistiku pro zdroj a uloží do LMDB.
        reward = 0..1 (např. poměr úspěšných findings / celkem)
        """
        if source not in self._stats:
            logger.warning(f"[BANDIT] Unknown source: {source}")
            return

        self._stats[source]['pulls'] += 1
        self._stats[source]['rewards'] += reward
        self._save(source)

    def get_credibility(self, source: str) -> float:
        """Get credibility score for a source (0-1)."""
        if source not in self._stats:
            return 0.5
        pulls = self._stats[source]['pulls']
        if pulls == 0:
            return 0.5
        return min(1.0, self._stats[source]['rewards'] / pulls)

    # Sprint 42: LinUCB methods
    # Sprint 43: Added migration from 8-dim to 14-dim
    # Sprint 45: MessagePack for faster serialization
    def _load_linucb(self):
        """Load LinUCB arms from LMDB if available."""
        if not hasattr(self, '_env') or self._env is None:
            return
        try:
            with self._env.begin() as txn:
                raw = txn.get(b'linucb_arms')
                if raw:
                    # Sprint 45: Try MessagePack first, fallback to JSON
                    if MSGPACK_AVAILABLE:
                        try:
                            data = unpack(raw)
                        except Exception:
                            data = json.loads(raw.decode())
                    else:
                        data = json.loads(raw.decode())

                    for src, d in data.items():
                        # Sprint 43: Check if stored model has fewer features (migration)
                        if len(d['A'][0]) == 8:
                            # Pad A with identity and b with zeros
                            oldA = np.array(d['A'])
                            oldb = np.array(d['b'])
                            newA = np.eye(N_FEATURES)
                            newA[:8, :8] = oldA
                            newb = np.zeros(N_FEATURES)
                            newb[:8] = oldb
                            d['A'] = newA.tolist()
                            d['b'] = newb.tolist()
                            logger.info(f"[LINUCB] Migrated {src} from 8-dim to 14-dim")
                        self._linucb_arms[src] = LinUCBArm.from_dict(d, N_FEATURES)
                    logger.info(f"[LINUCB] Loaded {len(self._linucb_arms)} arms")
        except Exception as e:
            logger.warning(f"[LINUCB] Load failed: {e}")

    def _save_linucb(self):
        """Save LinUCB arms to LMDB if available."""
        if not hasattr(self, '_env') or self._env is None:
            return
        try:
            data = {src: arm.to_dict() for src, arm in self._linucb_arms.items()}
            # Sprint 45: Use MessagePack if available
            if MSGPACK_AVAILABLE:
                packed = pack(data)
            else:
                packed = json.dumps(data).encode()
            with self._env.begin(write=True) as txn:
                txn.put(b'linucb_arms', packed)
        except Exception as e:
            logger.warning(f"[LINUCB] Save failed: {e}")

    def _ucb1_score(self, source: str) -> float:
        """Classic UCB1 score (used for fallback)."""
        total = sum(self._counts.values()) + 1
        if self._counts.get(source, 0) == 0:
            return float('inf')
        mean = self._rewards.get(source, 0.0) / max(1, self._counts[source])
        exploration = 2.0 * np.sqrt(np.log(total) / max(1, self._counts[source]))
        return mean + exploration

    def select_with_context(self, sources: List[str], analysis: Optional[Dict[str, Any]],
                          n: int = 3) -> List[str]:
        """Select sources using LinUCB if enough data, else fallback to UCB1."""
        try:
            context = extract_context_features(analysis)
            scored = []
            for src in sources:
                if src not in self._linucb_arms:
                    self._linucb_arms[src] = LinUCBArm(N_FEATURES)
                arm = self._linucb_arms[src]
                if self._counts.get(src, 0) < MIN_LINUCB_SAMPLES:
                    # Fallback to classic UCB1 score
                    score = self._ucb1_score(src)
                else:
                    score = arm.select(context)
                scored.append((score, src))
            scored.sort(reverse=True)
            return [s for _, s in scored[:n]]
        except Exception as e:
            logger.warning(f"[LINUCB] Error: {e}, falling back to UCB1")
            return self._select_ucb1(sources, n)

    def _select_ucb1(self, sources: List[str], n: int) -> List[str]:
        """Fallback UCB1 selection."""
        scored = []
        for src in sources:
            self._counts.setdefault(src, 0)
            self._rewards.setdefault(src, 0.0)
            score = self._ucb1_score(src)
            scored.append((score, src))
        scored.sort(reverse=True)
        return [s for _, s in scored[:n]]

    def update_with_context(self, source: str, reward: float, analysis: Optional[Dict[str, Any]]) -> None:
        """Update both UCB1 counts and LinUCB arm."""
        # Classic UCB1 update
        self._counts[source] = self._counts.get(source, 0) + 1
        self._rewards[source] = self._rewards.get(source, 0.0) + reward

        # LinUCB update
        try:
            context = extract_context_features(analysis)
            if source not in self._linucb_arms:
                self._linucb_arms[source] = LinUCBArm(N_FEATURES)
            self._linucb_arms[source].update(context, reward)
            # Periodically save (every 10 updates)
            if sum(self._counts.values()) % 10 == 0:
                self._save_linucb()
        except Exception as e:
            logger.warning(f"[LINUCB] Update error: {e}")

    def close(self):
        """Uzavře LMDB environment."""
        if hasattr(self, '_env') and self._env:
            self._env.close()

    def __del__(self):
        self.close()
