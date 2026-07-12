"""
Identity Stitching Engine
=========================

Advanced cross-platform identity linking and probabilistic identity matching system.

Features:
- Cross-platform identity linking (usernames, emails, aliases)
- Probabilistic identity matching with weighted scoring
- Username similarity (Levenshtein, Jaro-Winkler via rapidfuzz)
- Writing style similarity using lightweight embeddings
- Temporal overlap analysis
- Network overlap analysis (shared connections)
- Identity graph construction and analysis

STATUS: DORMANT + HELPER
  - Zero production call sites (grep audit: legacy autonomous_orchestrator.py only)
  - Imports Entity/Relationship from relationship_discovery.py (helper dependency)
  - identity_stitching.py is called BY relationship_discovery via to_entities_and_relationships()
  - NOT on canonical sprint/autonomous_orchestrator.py hot path
  - Re-exported via intelligence/__init__.py (lazy try/except)

ROLE: HELPER-ONLY — provides conversion method to RelationshipDiscoveryEngine
  but is not called in production paths itself.

M1 8GB CEILING (HARD):
  - max_memory_mb=512 hard limit for M1 8GB UMA
  - _similarity_cache: bounded LRU (max 4096 entries) with O(1) eviction
  - _match_cache: bounded LRU (max 2048 entries) with O(1) eviction
  - optimize_memory() clears both caches and forces gc.collect()
  - Memory-pressure auto-eviction triggers when RSS > 80% of max_memory_mb

PROMOTION GATE: requires production call site evidence beyond legacy path.
"""
import gc
import logging
import re
import time
from collections import defaultdict, OrderedDict
from dataclasses import dataclass, field
import msgspec
from datetime import datetime, timedelta
from typing import Any, Generic, TypeVar
T = TypeVar('T', default=object)

class _BoundedCache[T]:
    """
    Bounded LRU cache with O(1) eviction and optional memory-pressure trigger.

    Features:
    - OrderedDict-based LRU with O(1) access/eviction
    - Symmetric key normalization: (A,B) and (B,A) map to same slot
    - Optional memory-pressure auto-eviction (configurable threshold)
    - Explicit max_size enforcement on every put
    - Thread-safe for single-threaded use (no locks)

    M1 8GB: Uses psutil to monitor RSS and evicts when pressure exceeds
    memory_pressure_threshold (default 0.8 = 80% of max_memory_mb).
    """
    __slots__ = tuple(('_cache', '_max_memory_bytes', '_max_size', '_memory_pressure_threshold', '_process'))

    def __init__(self, max_size: int, max_memory_mb: float=512.0, memory_pressure_threshold: float=0.8):
        self._max_size = max_size
        self._max_memory_bytes = max_memory_mb * 1024 * 1024
        self._memory_pressure_threshold = memory_pressure_threshold
        self._cache: OrderedDict[str, T] = OrderedDict()
        self._process: Any = None

    def _normalize_key(self, key: tuple[str, str]) -> str:
        """Normalize key so (A,B) and (B,A) map to same slot."""
        return tuple(sorted(key))

    def get(self, key: tuple[str, str]) -> T | None:
        """Get item, moving it to end (most recently used). Returns None if not found."""
        norm = self._normalize_key(key)
        if norm not in self._cache:
            return None
        self._cache.move_to_end(norm)
        return self._cache[norm]

    def put(self, key: tuple[str, str], value: T) -> None:
        """Put item, evicting oldest if at capacity. Also checks memory pressure."""
        norm = self._normalize_key(key)
        if norm in self._cache:
            self._cache.move_to_end(norm)
            self._cache[norm] = value
            return
        while len(self._cache) >= self._max_size:
            self._cache.popitem(last=False)
        self._cache[norm] = value
        self._maybe_evict_on_pressure()

    def _maybe_evict_on_pressure(self) -> None:
        """Evict 50% of cache if RSS exceeds memory pressure threshold."""
        try:
            if self._process is None:
                import psutil
                self._process = psutil.Process()
            rss = self._process.memory_info().rss
            if rss > self._max_memory_bytes * self._memory_pressure_threshold:
                evict_count = max(1, len(self._cache) // 2)
                for _ in range(evict_count):
                    if self._cache:
                        self._cache.popitem(last=False)
                logger.debug(f'Cache pressure eviction: evicted {evict_count} entries (RSS={rss / 1024 / 1024:.1f}MB)')
        except Exception:
            pass

    def clear(self) -> None:
        """Clear all entries."""
        self._cache.clear()

    def __len__(self) -> int:
        return len(self._cache)

    def stats(self) -> dict[str, Any]:
        """Return cache statistics."""
        return {'entries': len(self._cache), 'max_size': self._max_size, 'utilization': len(self._cache) / self._max_size if self._max_size > 0 else 0}
import numpy as np
NETWORKX_AVAILABLE = True
_nx = None
IGRAPH_AVAILABLE = True
_ig = None

def _get_nx():
    """Lazy networkx importer — imported only when first graph method is called."""
    global _nx
    if _nx is None:
        import networkx as _nx_mod
        _nx = _nx_mod
    return _nx

def _get_ig():
    """Lazy igraph importer — M1-optimized C-core, preferred over networkx."""
    global _ig
    if _ig is None:
        import igraph as _ig_mod
        _ig = _ig_mod
    return _ig
try:
    from rapidfuzz import distance, fuzz
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False
    fuzz = None
    distance = None
SKLEARN_AVAILABLE = True
try:
    from .relationship_discovery import Entity, Relationship, RelationshipType
    RELATIONSHIP_AVAILABLE = True
except ImportError:
    RELATIONSHIP_AVAILABLE = False
    Entity = None
    Relationship = None
    RelationshipType = None
logger = logging.getLogger(__name__)

@dataclass(True)
class UsernameEntry:
    """Represents a username on a specific platform."""
    platform: str
    username: str
    verified: bool = False
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.first_seen is None:
            self.first_seen = datetime.now(UTC)
        if self.last_seen is None:
            self.last_seen = datetime.now(UTC)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {'platform': self.platform, 'username': self.username, 'verified': self.verified, 'first_seen': self.first_seen.isoformat() if self.first_seen else None, 'last_seen': self.last_seen.isoformat() if self.last_seen else None, 'metadata': self.metadata}

@dataclass(frozen=True, slots=True)
class IdentityProfile:
    """
    Represents a unified identity profile across platforms.

    Attributes:
        id: Unique identifier for this profile
        primary_name: Primary display name
        aliases: List of known aliases/alternate names
        emails: List of associated email addresses
        usernames: List of platform-specific usernames
        confidence: Overall confidence score (0-1)
        evidence: List of evidence strings supporting this profile
        attributes: Additional metadata
        created_at: Profile creation timestamp
        updated_at: Last update timestamp
    """
    id: str
    primary_name: str
    aliases: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    usernames: list[UsernameEntry] = field(default_factory=list)
    confidence: float = 0.5
    evidence: list[str] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.updated_at is None:
            self.updated_at = datetime.now(UTC)

    def add_username(self, platform: str, username: str, **kwargs) -> UsernameEntry:
        """Add a username entry for a platform."""
        entry = UsernameEntry(platform=platform, username=username, **kwargs)
        self.usernames.append(entry)
        self.updated_at = datetime.now(UTC)
        return entry

    def get_username(self, platform: str) -> str | None:
        """Get username for a specific platform."""
        for entry in self.usernames:
            if entry.platform.lower() == platform.lower():
                return entry.username
        return None

    def get_all_usernames(self) -> list[str]:
        """Get all usernames across platforms."""
        return [entry.username for entry in self.usernames]

    def get_platforms(self) -> set[str]:
        """Get set of platforms where this identity appears."""
        return {entry.platform for entry in self.usernames}

    def to_dict(self) -> dict[str, Any]:
        """Convert profile to dictionary."""
        return {'id': self.id, 'primary_name': self.primary_name, 'aliases': self.aliases, 'emails': self.emails, 'usernames': [u.to_dict() for u in self.usernames], 'confidence': self.confidence, 'evidence': self.evidence, 'attributes': self.attributes, 'created_at': self.created_at.isoformat() if self.created_at else None, 'updated_at': self.updated_at.isoformat() if self.updated_at else None}

@dataclass(frozen=True, slots=True)
class IdentityMatch:
    """
    Represents a match between two identity profiles.

    Attributes:
        profile_a: ID of first profile
        profile_b: ID of second profile
        match_score: Overall match score (0-1)
        match_signals: Dictionary of individual signal scores
        confidence: Confidence level (high, medium, low)
        evidence: List of evidence supporting the match
    """
    profile_a: str
    profile_b: str
    match_score: float
    match_signals: dict[str, float] = field(default_factory=dict)
    confidence: float = 0.35
    evidence: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.match_score >= 0.85:
            self.confidence = 0.85
        elif self.match_score >= 0.6:
            self.confidence = 0.6
        else:
            self.confidence = 0.35

    def to_dict(self) -> dict[str, Any]:
        """Convert match to dictionary."""
        return {'profile_a': self.profile_a, 'profile_b': self.profile_b, 'match_score': self.match_score, 'match_signals': self.match_signals, 'confidence': self.confidence, 'evidence': self.evidence}

@dataclass(frozen=True, slots=True)
class StitchedIdentity:
    """
    Represents a stitched identity combining multiple profiles.

    Attributes:
        id: Unique identifier for stitched identity
        profile_ids: IDs of constituent profiles
        primary_profile: ID of primary profile
        merged_names: All names from constituent profiles
        merged_emails: All emails from constituent profiles
        merged_usernames: All usernames from constituent profiles
        stitch_confidence: Confidence in the stitching (0-1)
        match_evidence: Evidence supporting the stitch
    """
    id: str
    profile_ids: list[str]
    primary_profile: str
    merged_names: list[str]
    merged_emails: list[str]
    merged_usernames: list[UsernameEntry]
    stitch_confidence: float
    match_evidence: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        """Convert stitched identity to dictionary."""
        return {'id': self.id, 'profile_ids': self.profile_ids, 'primary_profile': self.primary_profile, 'merged_names': self.merged_names, 'merged_emails': self.merged_emails, 'usernames': [u.to_dict() for u in self.merged_usernames], 'stitch_confidence': self.stitch_confidence, 'match_evidence': self.match_evidence, 'created_at': self.created_at.isoformat() if self.created_at else None}

class IdentityStitchingEngine:
    """
    Advanced identity stitching engine for cross-platform identity linking.

    This engine provides comprehensive capabilities for:
    - Linking identities across platforms using usernames, emails, and aliases
    - Probabilistic identity matching with multiple signals
    - Username similarity using fuzzy string matching
    - Writing style similarity using lightweight text analysis
    - Temporal overlap analysis
    - Network overlap analysis
    - Identity graph construction and community detection

    M1 8GB Optimizations:
    - Uses rapidfuzz for fast C-based string matching
    - No heavy ML models - only lightweight sklearn TF-IDF if available
    - Memory-efficient graph operations with NetworkX
    - Streaming processing for large datasets
    - Lazy evaluation for expensive operations

    Example:
        engine = IdentityStitchingEngine(similarity_threshold=0.7)

        # Add profiles
        profile = IdentityProfile(
            id="user1",
            primary_name="Alice Smith",
            emails=["alice@example.com"],
        )
        profile.add_username("twitter", "alice_smith")
        profile.add_username("github", "alicecodes")
        engine.add_profile(profile)

        # Find matches
        matches = engine.find_matches("user1")

        # Stitch identities
        stitched = engine.stitch_identities(match_threshold=0.8)
    """
    DEFAULT_SIGNAL_WEIGHTS = {'username_exact': 1.0, 'username_similarity': 0.7, 'email_exact': 1.0, 'email_domain': 0.3, 'alias_match': 0.8, 'style_similarity': 0.5, 'temporal_overlap': 0.4, 'network_overlap': 0.6}
    __slots__ = tuple(('_alias_index', '_email_index', '_identity_graph', '_match_cache', '_platform_index', '_profiles', '_similarity_cache', '_stats', '_username_index', 'enable_fuzzy', 'max_memory_mb', 'signal_weights', 'similarity_threshold'))

    def __init__(self, similarity_threshold: float=0.7, signal_weights: dict[str, float] | None=None, max_memory_mb: int=512, enable_fuzzy: bool=True):
        """
        Initialize the Identity Stitching Engine.

        Args:
            similarity_threshold: Minimum similarity score for matching
            signal_weights: Custom weights for match signals (uses defaults if None)
            max_memory_mb: ADVISORY ceiling in MB — not hard-enforced.
                           Default 512MB is appropriate for M1 8GB UMA.
            enable_fuzzy: Enable fuzzy string matching (requires rapidfuzz)
        """
        self.similarity_threshold = similarity_threshold
        self.signal_weights = signal_weights or self.DEFAULT_SIGNAL_WEIGHTS.copy()
        self.max_memory_mb = max_memory_mb
        self.enable_fuzzy = enable_fuzzy and RAPIDFUZZ_AVAILABLE
        self._profiles: dict[str, IdentityProfile] = {}
        self._username_index: dict[str, set[str]] = defaultdict(set)
        self._email_index: dict[str, set[str]] = defaultdict(set)
        self._alias_index: dict[str, set[str]] = defaultdict(set)
        self._platform_index: dict[str, set[str]] = defaultdict(set)
        self._identity_graph: Any | None = None
        self._similarity_cache = _BoundedCache[float](max_size=4096, max_memory_mb=max_memory_mb, memory_pressure_threshold=0.8)
        self._match_cache = _BoundedCache[IdentityMatch](max_size=2048, max_memory_mb=max_memory_mb, memory_pressure_threshold=0.8)
        self._stats = {'profiles_added': 0, 'matches_computed': 0, 'identities_stitched': 0, 'graphs_built': 0}
        logger.info(f'IdentityStitchingEngine initialized (threshold={similarity_threshold}, fuzzy={self.enable_fuzzy})')

    def add_profile(self, profile: IdentityProfile) -> bool:
        """
        Add an identity profile to the engine.

        Args:
            profile: IdentityProfile to add

        Returns:
            True if added, False if already exists
        """
        if profile.id in self._profiles:
            logger.debug(f'Profile {profile.id} already exists, updating')
            self._update_profile(profile)
            return False
        self._profiles[profile.id] = profile
        self._stats['profiles_added'] += 1
        self._index_profile(profile)
        self._invalidate_caches()
        logger.debug(f'Added profile: {profile.id} ({profile.primary_name})')
        return True

    def _update_profile(self, profile: IdentityProfile):
        """Update an existing profile."""
        existing = self._profiles[profile.id]
        existing.primary_name = profile.primary_name
        existing.aliases = list(set(existing.aliases + profile.aliases))
        existing.emails = list(set(existing.emails + profile.emails))
        existing.usernames.extend(profile.usernames)
        existing.attributes.update(profile.attributes)
        existing.updated_at = datetime.now(UTC)
        self._index_profile(existing)

    def _index_profile(self, profile: IdentityProfile):
        """Index a profile for fast lookup."""
        for entry in profile.usernames:
            normalized = self._normalize_username(entry.username)
            self._username_index[normalized].add(profile.id)
            self._platform_index[entry.platform.lower()].add(profile.id)
        for email in profile.emails:
            normalized = self._normalize_email(email)
            self._email_index[normalized].add(profile.id)
        for alias in profile.aliases:
            normalized = self._normalize_text(alias)
            self._alias_index[normalized].add(profile.id)
        normalized_name = self._normalize_text(profile.primary_name)
        self._alias_index[normalized_name].add(profile.id)

    def get_profile(self, profile_id: str) -> IdentityProfile | None:
        """Get a profile by ID."""
        return self._profiles.get(profile_id)

    def remove_profile(self, profile_id: str) -> bool:
        """Remove a profile and all its indexes."""
        if profile_id not in self._profiles:
            return False
        profile = self._profiles[profile_id]
        for entry in profile.usernames:
            normalized = self._normalize_username(entry.username)
            self._username_index[normalized].discard(profile_id)
            self._platform_index[entry.platform.lower()].discard(profile_id)
        for email in profile.emails:
            normalized = self._normalize_email(email)
            self._email_index[normalized].discard(profile_id)
        for alias in profile.aliases:
            normalized = self._normalize_text(alias)
            self._alias_index[normalized].discard(profile_id)
        normalized_name = self._normalize_text(profile.primary_name)
        self._alias_index[normalized_name].discard(profile_id)
        del self._profiles[profile_id]
        self._invalidate_caches()
        return True

    def _invalidate_caches(self):
        """Invalidate all cached computations."""
        self._identity_graph = None
        self._similarity_cache.clear()
        self._match_cache.clear()

    @staticmethod
    def _normalize_username(username: str) -> str:
        """Normalize username for comparison."""
        normalized = username.lower().strip().lstrip('@')
        normalized = re.sub('[._-]', '', normalized)
        return normalized

    @staticmethod
    def _normalize_email(email: str) -> str:
        """Normalize email for comparison."""
        return email.lower().strip()

    @staticmethod
    def _normalize_text(text: str) -> str:
        """Normalize text for comparison."""
        return text.lower().strip()

    @staticmethod
    def _extract_email_domain(email: str) -> str:
        """Extract domain from email address."""
        parts = email.split('@')
        return parts[-1] if len(parts) > 1 else ''

    def compute_username_similarity(self, user1: str, user2: str) -> float:
        """
        Compute similarity between two usernames.

        Uses rapidfuzz for fast fuzzy matching if available,
        falls back to simple normalized comparison.

        Args:
            user1: First username
            user2: Second username

        Returns:
            Similarity score (0-1)
        """
        cache_key = (user1, user2)
        cached = self._similarity_cache.get(cache_key)
        if cached is not None:
            return cached
        norm1 = self._normalize_username(user1)
        norm2 = self._normalize_username(user2)
        if norm1 == norm2:
            return 1.0
        if self.enable_fuzzy and RAPIDFUZZ_AVAILABLE:
            similarity = fuzz.ratio(norm1, norm2) / 100.0
            token_sim = fuzz.token_set_ratio(norm1, norm2) / 100.0
            result = max(similarity, token_sim)
        else:
            result = self._simple_similarity(norm1, norm2)
        self._similarity_cache.put(cache_key, result)
        return result

    def _simple_similarity(self, s1: str, s2: str) -> float:
        """Simple similarity metric when rapidfuzz is not available."""
        if not s1 and (not s2):
            return 1.0
        if not s1 or not s2:
            return 0.0
        len_sum = len(s1) + len(s2)
        if len_sum == 0:
            return 1.0
        common = sum((c in s2 for c in s1))
        return 2 * common / len_sum

    def compute_style_similarity(self, texts1: list[str], texts2: list[str]) -> float:
        """
        Compute writing style similarity between two sets of texts.

        Uses TF-IDF cosine similarity if sklearn is available,
        falls back to simple lexical similarity.

        Args:
            texts1: First set of texts
            texts2: Second set of texts

        Returns:
            Similarity score (0-1)
        """
        if not texts1 or not texts2:
            return 0.0
        all_texts = texts1 + texts2
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.metrics.pairwise import cosine_similarity
        except ImportError:
            return self._lexical_similarity(texts1, texts2)
        if len(all_texts) >= 2:
            try:
                vectorizer = TfidfVectorizer(max_features=1000, stop_words='english', ngram_range=(1, 2), min_df=1)
                tfidf_matrix = vectorizer.fit_transform(all_texts)
                similarities = cosine_similarity(tfidf_matrix[:len(texts1)], tfidf_matrix[len(texts1):])
                return float(np.max(similarities))
            except Exception as e:
                logger.warning(f'TF-IDF similarity failed: {e}, falling back')
        return self._lexical_similarity(texts1, texts2)

    def _lexical_similarity(self, texts1: list[str], texts2: list[str]) -> float:
        """Compute lexical similarity based on word overlap."""
        words1 = set()
        words2 = set()
        for text in texts1:
            words1.update(self._extract_words(text))
        for text in texts2:
            words2.update(self._extract_words(text))
        if not words1 or not words2:
            return 0.0
        intersection = words1 & words2
        union = words1 | words2
        return len(intersection) / len(union) if union else 0.0

    @staticmethod
    def _extract_words(text: str) -> set[str]:
        """Extract words from text."""
        words = re.findall('\\b[a-zA-Z]{3,}\\b', text.lower())
        return set(words)

    def compute_temporal_overlap(self, activity1: list[datetime], activity2: list[datetime], window_days: int=30) -> float:
        """
        Compute temporal overlap between two activity timelines.

        Args:
            activity1: First activity timeline
            activity2: Second activity timeline
            window_days: Time window for considering overlap

        Returns:
            Overlap score (0-1)
        """
        if not activity1 or not activity2:
            return 0.0
        times1 = sorted(activity1)
        times2 = sorted(activity2)
        overlap_count = 0
        window = timedelta(days=window_days)
        for t1 in times1:
            for t2 in times2:
                if abs((t1 - t2).total_seconds()) <= window.total_seconds():
                    overlap_count += 1
                    break
        min_activity = min(len(times1), len(times2))
        return min(1.0, overlap_count / min_activity) if min_activity > 0 else 0.0

    def compute_network_overlap(self, network1: set[str], network2: set[str]) -> float:
        """
        Compute network overlap (shared connections).

        Args:
            network1: First network (set of connection IDs)
            network2: Second network (set of connection IDs)

        Returns:
            Overlap score (0-1)
        """
        if not network1 or not network2:
            return 0.0
        intersection = network1 & network2
        union = network1 | network2
        jaccard = len(intersection) / len(union) if union else 0.0
        min_size = min(len(network1), len(network2))
        overlap_ratio = len(intersection) / min_size if min_size > 0 else 0.0
        return (jaccard + overlap_ratio) / 2

    def compute_match(self, profile_a: IdentityProfile, profile_b: IdentityProfile) -> IdentityMatch:
        """
        Compute match between two profiles.

        Args:
            profile_a: First profile
            profile_b: Second profile

        Returns:
            IdentityMatch with scores and signals
        """
        cache_key = (profile_a.id, profile_b.id)
        cached = self._match_cache.get(cache_key)
        if cached is not None:
            return cached
        signals: dict[str, float] = {}
        evidence: list[str] = []
        usernames_a = profile_a.get_all_usernames()
        usernames_b = profile_b.get_all_usernames()
        if usernames_a and usernames_b:
            max_username_sim = 0.0
            for u1 in usernames_a:
                for u2 in usernames_b:
                    sim = self.compute_username_similarity(u1, u2)
                    max_username_sim = max(max_username_sim, sim)
                    if sim == 1.0:
                        evidence.append(f'Exact username match: {u1}')
                    elif sim >= 0.8:
                        evidence.append(f'Similar usernames: {u1} ~ {u2} ({sim:.2f})')
            signals['username_similarity'] = max_username_sim
        emails_a = set(profile_a.emails)
        emails_b = set(profile_b.emails)
        if emails_a & emails_b:
            signals['email_exact'] = 1.0
            evidence.append(f'Shared emails: {emails_a & emails_b}')
        elif emails_a and emails_b:
            domains_a = {self._extract_email_domain(e) for e in emails_a}
            domains_b = {self._extract_email_domain(e) for e in emails_b}
            if domains_a & domains_b:
                signals['email_domain'] = 0.5
                evidence.append(f'Shared email domains: {domains_a & domains_b}')
        aliases_a = set(profile_a.aliases + [profile_a.primary_name])
        aliases_b = set(profile_b.aliases + [profile_b.primary_name])
        if aliases_a & aliases_b:
            signals['alias_match'] = 1.0
            evidence.append(f'Shared aliases: {aliases_a & aliases_b}')
        else:
            max_alias_sim = 0.0
            for a1 in aliases_a:
                for a2 in aliases_b:
                    sim = self.compute_username_similarity(a1, a2)
                    max_alias_sim = max(max_alias_sim, sim)
            if max_alias_sim > 0.7:
                signals['alias_match'] = max_alias_sim
        platforms_a = profile_a.get_platforms()
        platforms_b = profile_b.get_platforms()
        shared_platforms = platforms_a & platforms_b
        if shared_platforms:
            for platform in shared_platforms:
                u1 = profile_a.get_username(platform)
                u2 = profile_b.get_username(platform)
                if u1 and u2 and (u1.lower() != u2.lower()):
                    signals['username_similarity'] = signals.get('username_similarity', 0) * 0.5
                    evidence.append(f'Different usernames on {platform}: {u1} vs {u2}')
        total_weight = 0.0
        weighted_score = 0.0
        for signal, score in signals.items():
            weight = self.signal_weights.get(signal, 0.5)
            weighted_score += score * weight
            total_weight += weight
        final_score = weighted_score / total_weight if total_weight > 0 else 0.0
        match = IdentityMatch(profile_a=profile_a.id, profile_b=profile_b.id, match_score=final_score, match_signals=signals, evidence=evidence)
        self._match_cache.put(cache_key, match)
        self._stats['matches_computed'] += 1
        return match

    def find_matches(self, profile_id: str, min_score: float | None=None) -> list[IdentityMatch]:
        """
        Find potential matches for a profile.

        Args:
            profile_id: Profile ID to find matches for
            min_score: Minimum match score (uses similarity_threshold if None)

        Returns:
            List of IdentityMatch objects sorted by score
        """
        if profile_id not in self._profiles:
            logger.warning(f'Profile {profile_id} not found')
            return []
        threshold = min_score if min_score is not None else self.similarity_threshold
        profile = self._profiles[profile_id]
        matches: list[IdentityMatch] = []
        candidates: set[str] = set()
        for username in profile.get_all_usernames():
            normalized = self._normalize_username(username)
            candidates.update(self._username_index.get(normalized, set()))
        for email in profile.emails:
            normalized = self._normalize_email(email)
            candidates.update(self._email_index.get(normalized, set()))
        for alias in profile.aliases + [profile.primary_name]:
            normalized = self._normalize_text(alias)
            candidates.update(self._alias_index.get(normalized, set()))
        candidates.discard(profile_id)
        for candidate_id in candidates:
            candidate = self._profiles.get(candidate_id)
            if not candidate:
                continue
            match = self.compute_match(profile, candidate)
            if match.match_score >= threshold:
                matches.append(match)
        matches.sort(key=lambda m: m.match_score, reverse=True)
        return matches

    def find_all_matches(self, min_score: float | None=None) -> list[IdentityMatch]:
        """
        Find all matches across all profiles.

        Args:
            min_score: Minimum match score

        Returns:
            List of IdentityMatch objects
        """
        threshold = min_score if min_score is not None else self.similarity_threshold
        matches: list[IdentityMatch] = []
        seen_pairs: set[tuple[str, str]] = set()
        profile_ids = list(self._profiles.keys())
        for i, id_a in enumerate(profile_ids):
            for id_b in profile_ids[i + 1:]:
                pair = tuple(sorted([id_a, id_b]))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                profile_a = self._profiles[id_a]
                profile_b = self._profiles[id_b]
                match = self.compute_match(profile_a, profile_b)
                if match.match_score >= threshold:
                    matches.append(match)
        matches.sort(key=lambda m: m.match_score, reverse=True)
        return matches

    def stitch_identities(self, match_threshold: float=0.8, transitive_threshold: float=0.6) -> list[StitchedIdentity]:
        """
        Stitch identities based on matches.

        Args:
            match_threshold: Threshold for direct stitching
            transitive_threshold: Threshold for transitive stitching

        Returns:
            List of StitchedIdentity objects
        """
        if not NETWORKX_AVAILABLE:
            raise ImportError('NetworkX is required for identity stitching')
        start_time = time.time()
        ig = _get_ig()
        graph = ig.Graph()
        profile_ids_list = list(self._profiles.keys())
        graph.add_vertices(len(profile_ids_list))
        for i, profile_id in enumerate(profile_ids_list):
            graph.vs[i]['name'] = profile_id
        id_to_idx = {pid: i for i, pid in enumerate(profile_ids_list)}
        matches = self.find_all_matches(min_score=match_threshold)
        for match in matches:
            a_idx = id_to_idx.get(match.profile_a)
            b_idx = id_to_idx.get(match.profile_b)
            if a_idx is not None and b_idx is not None:
                try:
                    graph.add_edge(a_idx, b_idx, weight=match.match_score, match=match)
                except Exception:
                    pass
        stitched: list[StitchedIdentity] = []
        for component in graph.connected_components():
            if len(component) == 1:
                continue
            comp_profile_ids = [profile_ids_list[i] for i in component]
            primary_id = comp_profile_ids[0]
            all_names: set[str] = set()
            all_emails: set[str] = set()
            all_usernames: list[UsernameEntry] = []
            all_evidence: list[str] = []
            total_confidence = 0.0
            for pid in comp_profile_ids:
                profile = self._profiles[pid]
                all_names.add(profile.primary_name)
                all_names.update(profile.aliases)
                all_emails.update(profile.emails)
                all_usernames.extend(profile.usernames)
            for i, pid_a in enumerate(comp_profile_ids):
                for pid_b in comp_profile_ids[i + 1:]:
                    a_idx = id_to_idx.get(pid_a)
                    b_idx = id_to_idx.get(pid_b)
                    if a_idx is not None and b_idx is not None:
                        try:
                            edge_id = graph.get_eid(a_idx, b_idx, error=False)
                            if edge_id >= 0:
                                match = graph.es[edge_id].get('match')
                                if match:
                                    total_confidence += match.match_score
                                    all_evidence.extend(match.evidence)
                        except Exception:
                            pass
            edge_count = sum((1 for _ in graph.es))
            avg_confidence = total_confidence / edge_count if edge_count > 0 else 0.0
            stitched_identity = StitchedIdentity(id=f'stitched_{primary_id}', profile_ids=profile_ids, primary_profile=primary_id, merged_names=list(all_names), merged_emails=list(all_emails), merged_usernames=all_usernames, stitch_confidence=avg_confidence, match_evidence=list(set(all_evidence)))
            stitched.append(stitched_identity)
        self._stats['identities_stitched'] += len(stitched)
        logger.info(f'Stitched {len(stitched)} identities in {time.time() - start_time:.3f}s')
        return stitched

    def get_identity_graph(self) -> Any:
        """
        Get the identity graph with all profiles and matches.

        Returns:
            igraph Graph with identity data (M1-optimized C-core)
        """
        if not NETWORKX_AVAILABLE:
            raise ImportError('NetworkX is required for graph operations')
        if self._identity_graph is not None:
            return self._identity_graph
        ig = _get_ig()
        graph = ig.Graph()
        profile_ids_list = list(self._profiles.keys())
        graph.add_vertices(len(profile_ids_list))
        for i, profile_id in enumerate(profile_ids_list):
            profile = self._profiles[profile_id]
            graph.vs[i]['name'] = profile_id
            graph.vs[i]['primary_name'] = profile.primary_name
            graph.vs[i]['aliases'] = profile.aliases
            graph.vs[i]['emails'] = profile.emails
            graph.vs[i]['platforms'] = list(profile.get_platforms())
            graph.vs[i]['confidence'] = profile.confidence
        id_to_idx = {pid: i for i, pid in enumerate(profile_ids_list)}
        matches = self.find_all_matches()
        for match in matches:
            a_idx = id_to_idx.get(match.profile_a)
            b_idx = id_to_idx.get(match.profile_b)
            if a_idx is not None and b_idx is not None:
                try:
                    graph.add_edge(a_idx, b_idx, weight=match.match_score, confidence=match.confidence, signals=match.match_signals)
                except Exception:
                    pass
        self._identity_graph = graph
        self._stats['graphs_built'] += 1
        return graph

    def get_identity_communities(self) -> list[set[str]]:
        """
        Detect communities in the identity graph.

        Returns:
            List of communities (sets of profile IDs) using igraph C-core
        """
        if not NETWORKX_AVAILABLE:
            raise ImportError('NetworkX is required for community detection')
        graph = self.get_identity_graph()
        if graph.vcount() == 0:
            return []
        profile_ids_list = list(self._profiles.keys())
        communities = []
        for component in graph.connected_components():
            comm_set = {profile_ids_list[i] for i in component}
            communities.append(comm_set)
        return communities

    def to_entities_and_relationships(self, stitched_identities: list[StitchedIdentity] | None=None) -> tuple[list[Any], list[Any]]:
        """
        Convert stitched identities to Entity and Relationship objects.

        Args:
            stitched_identities: Pre-computed stitched identities (optional)

        Returns:
            Tuple of (entities, relationships) for RelationshipDiscoveryEngine
        """
        if not RELATIONSHIP_AVAILABLE:
            raise ImportError('relationship_discovery module not available')
        if stitched_identities is None:
            stitched_identities = self.stitch_identities()
        entities: list[Entity] = []
        relationships: list[Relationship] = []
        for stitched in stitched_identities:
            entity = Entity(id=stitched.id, type=EntityType.DIGITAL_IDENTITY, attributes={'merged_names': stitched.merged_names, 'merged_emails': stitched.merged_emails, 'profile_count': len(stitched.profile_ids), 'stitch_confidence': stitched.stitch_confidence}, sources=stitched.profile_ids)
            entities.append(entity)
            for i, pid_a in enumerate(stitched.profile_ids):
                for pid_b in stitched.profile_ids[i + 1:]:
                    rel = Relationship(source=pid_a, target=pid_b, type=RelationshipType.RELATED_TO, strength=stitched.stitch_confidence, confidence=stitched.stitch_confidence, evidence=stitched.match_evidence)
                    relationships.append(rel)
        return (entities, relationships)

    def to_dict(self) -> dict[str, Any]:
        """Export engine state as dictionary."""
        return {'profiles': {k: v.to_dict() for k, v in self._profiles.items()}, 'stats': self._stats, 'similarity_threshold': self.similarity_threshold, 'signal_weights': self.signal_weights}

    def export_matches(self) -> list[dict[str, Any]]:
        """Export all matches as list of dictionaries."""
        matches = self.find_all_matches()
        return [m.to_dict() for m in matches]

    def export_stitched(self) -> list[dict[str, Any]]:
        """Export stitched identities as list of dictionaries."""
        stitched = self.stitch_identities()
        return [s.to_dict() for s in stitched]

    def get_stats(self) -> dict[str, Any]:
        """Get engine statistics."""
        return self._stats.copy()

    def clear(self):
        """Clear all data from the engine."""
        self._profiles.clear()
        self._username_index.clear()
        self._email_index.clear()
        self._alias_index.clear()
        self._platform_index.clear()
        self._invalidate_caches()
        gc.collect()
        logger.info('IdentityStitchingEngine cleared')

    def optimize_memory(self):
        """Optimize memory usage by clearing caches and forcing GC."""
        self._identity_graph = None
        self._similarity_cache.clear()
        self._match_cache.clear()
        gc.collect()
        logger.debug('Memory optimization completed')

    def get_memory_usage(self) -> dict[str, int]:
        """Estimate memory usage of key data structures."""
        import sys
        profile_size = sum((sys.getsizeof(p) for p in self._profiles.values()))
        index_size = sum((sys.getsizeof(s) for s in self._username_index.values())) + sum((sys.getsizeof(s) for s in self._email_index.values())) + sum((sys.getsizeof(s) for s in self._alias_index.values()))
        return {'profiles_bytes': profile_size, 'indexes_bytes': index_size, 'total_bytes': profile_size + index_size, 'profile_count': len(self._profiles), 'similarity_cache': self._similarity_cache.stats(), 'match_cache': self._match_cache.stats()}

def create_identity_stitching_engine(similarity_threshold: float=0.7, signal_weights: dict[str, float] | None=None, max_memory_mb: int=512, enable_fuzzy: bool=True) -> IdentityStitchingEngine:
    """Factory function to create an IdentityStitchingEngine."""
    return IdentityStitchingEngine(similarity_threshold=similarity_threshold, signal_weights=signal_weights, max_memory_mb=max_memory_mb, enable_fuzzy=enable_fuzzy)

async def example_usage():
    """Example usage of the IdentityStitchingEngine."""
    engine = create_identity_stitching_engine(similarity_threshold=0.6)
    profiles = [IdentityProfile(id='alice_twitter', primary_name='Alice Smith', emails=['alice@example.com'], aliases=['alice_s']), IdentityProfile(id='alice_github', primary_name='Alice Smith', emails=['alice@example.com'], aliases=['alicecodes']), IdentityProfile(id='bob_twitter', primary_name='Bob Jones', emails=['bob@example.com']), IdentityProfile(id='alice_alt', primary_name='Alice S.', emails=['alice.smith@example.com'], aliases=['alice_smith'])]
    profiles[0].add_username('twitter', 'alice_smith', verified=True)
    profiles[1].add_username('github', 'alicecodes')
    profiles[2].add_username('twitter', 'bobjones')
    profiles[3].add_username('reddit', 'alice_s')
    for profile in profiles:
        engine.add_profile(profile)
    print('=== Finding Matches ===')
    for profile in profiles:
        matches = engine.find_matches(profile.id)
        if matches:
            print(f'\n{profile.primary_name} ({profile.id}):')
            for match in matches[:3]:
                print(f'  -> {match.profile_b}: {match.match_score:.2f} ({match.confidence})')
                print(f'     Signals: {match.match_signals}')
    print('\n=== Stitching Identities ===')
    stitched = engine.stitch_identities(match_threshold=0.7)
    for identity in stitched:
        print(f'\nStitched Identity: {identity.id}')
        print(f'  Profiles: {identity.profile_ids}')
        print(f'  Names: {identity.merged_names}')
        print(f'  Emails: {identity.merged_emails}')
        print(f'  Confidence: {identity.stitch_confidence:.2f}')
    print('\n=== Identity Graph Stats ===')
    graph = engine.get_identity_graph()
    print(f'  Nodes: {graph.number_of_nodes()}')
    print(f'  Edges: {graph.number_of_edges()}')
    if RELATIONSHIP_AVAILABLE:
        print('\n=== Export for RelationshipDiscoveryEngine ===')
        entities, relationships = engine.to_entities_and_relationships(stitched)
        print(f'  Entities: {len(entities)}')
        print(f'  Relationships: {len(relationships)}')
    engine.clear()
if __name__ == '__main__':
    import asyncio
    asyncio.run(example_usage())