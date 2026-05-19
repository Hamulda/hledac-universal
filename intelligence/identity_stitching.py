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

M1 8GB CEILING (ADVISORY):
  - max_memory_mb=512 recommended for M1 8GB UMA; default is correct
  - _similarity_cache: unbounded Dict — call optimize_memory() after large batches
  - _match_cache: unbounded Dict — call optimize_memory() after large batches
  - optimize_memory() clears both caches and forces gc.collect()

PROMOTION GATE: requires production call site evidence beyond legacy path.
"""

from __future__ import annotations

import gc
import logging
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import numpy as np

# Optional imports with fallbacks
# networkx is lazy — imported only when first graph operation is needed
NETWORKX_AVAILABLE = True  # assume available, defer actual import to _get_nx()
_nx = None


def _get_nx():
    """Lazy networkx importer — imported only when first graph method is called."""
    global _nx
    if _nx is None:
        import networkx as _nx_mod
        _nx = _nx_mod
    return _nx

try:
    from rapidfuzz import fuzz, distance
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False
    fuzz = None
    distance = None

# NOTE: sklearn imports moved to function scope to avoid loading sklearn+pandas at import time
# Use _check_sklearn_available() or check inside methods
SKLEARN_AVAILABLE = True  # Will be checked lazily

# Import from relationship_discovery for integration
try:
    from .relationship_discovery import Entity, Relationship, RelationshipType
    RELATIONSHIP_AVAILABLE = True
except ImportError:
    RELATIONSHIP_AVAILABLE = False
    Entity = None
    Relationship = None
    RelationshipType = None

logger = logging.getLogger(__name__)


@dataclass
class UsernameEntry:
    """Represents a username on a specific platform."""
    platform: str
    username: str
    verified: bool = False
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.first_seen is None:
            self.first_seen = datetime.now()
        if self.last_seen is None:
            self.last_seen = datetime.now()

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "platform": self.platform,
            "username": self.username,
            "verified": self.verified,
            "first_seen": self.first_seen.isoformat() if self.first_seen else None,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "metadata": self.metadata,
        }


@dataclass
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
    aliases: List[str] = field(default_factory=list)
    emails: List[str] = field(default_factory=list)
    usernames: List[UsernameEntry] = field(default_factory=list)
    confidence: float = 0.5
    evidence: List[str] = field(default_factory=list)
    attributes: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: Optional[datetime] = None

    def __post_init__(self):
        if self.updated_at is None:
            self.updated_at = datetime.now()

    def add_username(self, platform: str, username: str, **kwargs) -> UsernameEntry:
        """Add a username entry for a platform."""
        entry = UsernameEntry(platform=platform, username=username, **kwargs)
        self.usernames.append(entry)
        self.updated_at = datetime.now()
        return entry

    def get_username(self, platform: str) -> Optional[str]:
        """Get username for a specific platform."""
        for entry in self.usernames:
            if entry.platform.lower() == platform.lower():
                return entry.username
        return None

    def get_all_usernames(self) -> List[str]:
        """Get all usernames across platforms."""
        return [entry.username for entry in self.usernames]

    def get_platforms(self) -> Set[str]:
        """Get set of platforms where this identity appears."""
        return {entry.platform for entry in self.usernames}

    def to_dict(self) -> Dict[str, Any]:
        """Convert profile to dictionary."""
        return {
            "id": self.id,
            "primary_name": self.primary_name,
            "aliases": self.aliases,
            "emails": self.emails,
            "usernames": [u.to_dict() for u in self.usernames],
            "confidence": self.confidence,
            "evidence": self.evidence,
            "attributes": self.attributes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


@dataclass
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
    match_signals: Dict[str, float] = field(default_factory=dict)
    confidence: float = 0.35  # numeric [0.0, 1.0]: high=0.85, medium=0.60, low=0.35
    evidence: List[str] = field(default_factory=list)

    def __post_init__(self):
        # Determine confidence level based on score
        if self.match_score >= 0.85:
            self.confidence = 0.85
        elif self.match_score >= 0.6:
            self.confidence = 0.60
        else:
            self.confidence = 0.35

    def to_dict(self) -> Dict[str, Any]:
        """Convert match to dictionary."""
        return {
            "profile_a": self.profile_a,
            "profile_b": self.profile_b,
            "match_score": self.match_score,
            "match_signals": self.match_signals,
            "confidence": self.confidence,
            "evidence": self.evidence,
        }


@dataclass
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
    profile_ids: List[str]
    primary_profile: str
    merged_names: List[str]
    merged_emails: List[str]
    merged_usernames: List[UsernameEntry]
    stitch_confidence: float
    match_evidence: List[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        """Convert stitched identity to dictionary."""
        return {
            "id": self.id,
            "profile_ids": self.profile_ids,
            "primary_profile": self.primary_profile,
            "merged_names": self.merged_names,
            "merged_emails": self.merged_emails,
            "usernames": [u.to_dict() for u in self.merged_usernames],
            "stitch_confidence": self.stitch_confidence,
            "match_evidence": self.match_evidence,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


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

    # Default weights for match signals
    DEFAULT_SIGNAL_WEIGHTS = {
        "username_exact": 1.0,
        "username_similarity": 0.7,
        "email_exact": 1.0,
        "email_domain": 0.3,
        "alias_match": 0.8,
        "style_similarity": 0.5,
        "temporal_overlap": 0.4,
        "network_overlap": 0.6,
    }

    def __init__(
        self,
        similarity_threshold: float = 0.7,
        signal_weights: Optional[Dict[str, float]] = None,
        max_memory_mb: int = 512,
        enable_fuzzy: bool = True,
    ):
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

        # Core data structures
        self._profiles: Dict[str, IdentityProfile] = {}
        self._username_index: Dict[str, Set[str]] = defaultdict(set)  # username -> profile_ids
        self._email_index: Dict[str, Set[str]] = defaultdict(set)  # email -> profile_ids
        self._alias_index: Dict[str, Set[str]] = defaultdict(set)  # alias -> profile_ids
        self._platform_index: Dict[str, Set[str]] = defaultdict(set)  # platform -> profile_ids

        # Graph structure (lazy initialized)
        self._identity_graph: Optional[Any] = None

        # Cached computations
        self._similarity_cache: Dict[Tuple[str, str], float] = {}
        self._match_cache: Dict[Tuple[str, str], IdentityMatch] = {}

        # Statistics
        self._stats = {
            "profiles_added": 0,
            "matches_computed": 0,
            "identities_stitched": 0,
            "graphs_built": 0,
        }

        logger.info(
            f"IdentityStitchingEngine initialized "
            f"(threshold={similarity_threshold}, fuzzy={self.enable_fuzzy})"
        )

    # ========================================================================
    # Profile Management
    # ========================================================================

    def add_profile(self, profile: IdentityProfile) -> bool:
        """
        Add an identity profile to the engine.

        Args:
            profile: IdentityProfile to add

        Returns:
            True if added, False if already exists
        """
        if profile.id in self._profiles:
            logger.debug(f"Profile {profile.id} already exists, updating")
            self._update_profile(profile)
            return False

        self._profiles[profile.id] = profile
        self._stats["profiles_added"] += 1

        # Update indexes
        self._index_profile(profile)

        # Invalidate caches
        self._invalidate_caches()

        logger.debug(f"Added profile: {profile.id} ({profile.primary_name})")
        return True

    def _update_profile(self, profile: IdentityProfile):
        """Update an existing profile."""
        existing = self._profiles[profile.id]
        existing.primary_name = profile.primary_name
        existing.aliases = list(set(existing.aliases + profile.aliases))
        existing.emails = list(set(existing.emails + profile.emails))
        existing.usernames.extend(profile.usernames)
        existing.attributes.update(profile.attributes)
        existing.updated_at = datetime.now()

        # Re-index
        self._index_profile(existing)

    def _index_profile(self, profile: IdentityProfile):
        """Index a profile for fast lookup."""
        # Index usernames
        for entry in profile.usernames:
            normalized = self._normalize_username(entry.username)
            self._username_index[normalized].add(profile.id)
            self._platform_index[entry.platform.lower()].add(profile.id)

        # Index emails
        for email in profile.emails:
            normalized = self._normalize_email(email)
            self._email_index[normalized].add(profile.id)

        # Index aliases
        for alias in profile.aliases:
            normalized = self._normalize_text(alias)
            self._alias_index[normalized].add(profile.id)

        # Also index primary name
        normalized_name = self._normalize_text(profile.primary_name)
        self._alias_index[normalized_name].add(profile.id)

    def get_profile(self, profile_id: str) -> Optional[IdentityProfile]:
        """Get a profile by ID."""
        return self._profiles.get(profile_id)

    def remove_profile(self, profile_id: str) -> bool:
        """Remove a profile and all its indexes."""
        if profile_id not in self._profiles:
            return False

        profile = self._profiles[profile_id]

        # Remove from indexes
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

        # Remove profile
        del self._profiles[profile_id]

        # Invalidate caches
        self._invalidate_caches()

        return True

    def _invalidate_caches(self):
        """Invalidate all cached computations."""
        self._identity_graph = None
        self._similarity_cache.clear()
        self._match_cache.clear()

    # ========================================================================
    # Normalization Utilities
    # ========================================================================

    @staticmethod
    def _normalize_username(username: str) -> str:
        """Normalize username for comparison."""
        # Remove leading @, convert to lowercase, remove common separators
        normalized = username.lower().strip().lstrip("@")
        # Remove common separators for comparison
        normalized = re.sub(r'[._-]', '', normalized)
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
        parts = email.split("@")
        return parts[-1] if len(parts) > 1 else ""

    # ========================================================================
    # Similarity Computation
    # ========================================================================

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
        if cache_key in self._similarity_cache:
            return self._similarity_cache[cache_key]

        norm1 = self._normalize_username(user1)
        norm2 = self._normalize_username(user2)

        # Exact match
        if norm1 == norm2:
            return 1.0

        # Fuzzy matching with rapidfuzz
        if self.enable_fuzzy and RAPIDFUZZ_AVAILABLE:
            # Use ratio for overall similarity
            similarity = fuzz.ratio(norm1, norm2) / 100.0

            # Boost for token set ratio (handles reordering)
            token_sim = fuzz.token_set_ratio(norm1, norm2) / 100.0

            # Use the maximum of both
            result = max(similarity, token_sim)
        else:
            # Fallback: simple character-level similarity
            result = self._simple_similarity(norm1, norm2)

        self._similarity_cache[cache_key] = result
        return result

    def _simple_similarity(self, s1: str, s2: str) -> float:
        """Simple similarity metric when rapidfuzz is not available."""
        if not s1 and not s2:
            return 1.0
        if not s1 or not s2:
            return 0.0

        # Levenshtein distance approximation
        len_sum = len(s1) + len(s2)
        if len_sum == 0:
            return 1.0

        # Simple character overlap
        common = sum((c in s2) for c in s1)
        return (2 * common) / len_sum

    def compute_style_similarity(self, texts1: List[str], texts2: List[str]) -> float:
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

        # Combine texts
        all_texts = texts1 + texts2

        # Use sklearn TF-IDF if available
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.metrics.pairwise import cosine_similarity
        except ImportError:
            return self._lexical_similarity(texts1, texts2)

        if len(all_texts) >= 2:
            try:
                vectorizer = TfidfVectorizer(
                    max_features=1000,  # Limit for memory efficiency
                    stop_words="english",
                    ngram_range=(1, 2),
                    min_df=1,
                )
                tfidf_matrix = vectorizer.fit_transform(all_texts)

                # Compute pairwise similarities
                similarities = cosine_similarity(tfidf_matrix[:len(texts1)], tfidf_matrix[len(texts1):])

                # Return maximum similarity
                return float(np.max(similarities))
            except Exception as e:
                logger.warning(f"TF-IDF similarity failed: {e}, falling back")

        # Fallback: simple word overlap
        return self._lexical_similarity(texts1, texts2)

    def _lexical_similarity(self, texts1: List[str], texts2: List[str]) -> float:
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
    def _extract_words(text: str) -> Set[str]:
        """Extract words from text."""
        # Simple word extraction
        words = re.findall(r'\b[a-zA-Z]{3,}\b', text.lower())
        return set(words)

    def compute_temporal_overlap(
        self,
        activity1: List[datetime],
        activity2: List[datetime],
        window_days: int = 30,
    ) -> float:
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

        # Sort timestamps
        times1 = sorted(activity1)
        times2 = sorted(activity2)

        # Find overlapping periods
        overlap_count = 0
        window = timedelta(days=window_days)

        for t1 in times1:
            for t2 in times2:
                if abs((t1 - t2).total_seconds()) <= window.total_seconds():
                    overlap_count += 1
                    break

        # Normalize by the smaller activity set
        min_activity = min(len(times1), len(times2))
        return min(1.0, overlap_count / min_activity) if min_activity > 0 else 0.0

    def compute_network_overlap(self, network1: Set[str], network2: Set[str]) -> float:
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

        # Jaccard similarity
        jaccard = len(intersection) / len(union) if union else 0.0

        # Also consider overlap ratio relative to smaller network
        min_size = min(len(network1), len(network2))
        overlap_ratio = len(intersection) / min_size if min_size > 0 else 0.0

        # Combine both metrics
        return (jaccard + overlap_ratio) / 2

    # ========================================================================
    # Match Computation
    # ========================================================================

    def compute_match(
        self,
        profile_a: IdentityProfile,
        profile_b: IdentityProfile,
    ) -> IdentityMatch:
        """
        Compute match between two profiles.

        Args:
            profile_a: First profile
            profile_b: Second profile

        Returns:
            IdentityMatch with scores and signals
        """
        cache_key = (profile_a.id, profile_b.id)
        if cache_key in self._match_cache:
            return self._match_cache[cache_key]

        signals: Dict[str, float] = {}
        evidence: List[str] = []

        # Username similarity
        usernames_a = profile_a.get_all_usernames()
        usernames_b = profile_b.get_all_usernames()

        if usernames_a and usernames_b:
            max_username_sim = 0.0
            for u1 in usernames_a:
                for u2 in usernames_b:
                    sim = self.compute_username_similarity(u1, u2)
                    max_username_sim = max(max_username_sim, sim)
                    if sim == 1.0:
                        evidence.append(f"Exact username match: {u1}")
                    elif sim >= 0.8:
                        evidence.append(f"Similar usernames: {u1} ~ {u2} ({sim:.2f})")

            signals["username_similarity"] = max_username_sim

        # Email matching
        emails_a = set(profile_a.emails)
        emails_b = set(profile_b.emails)

        if emails_a & emails_b:
            signals["email_exact"] = 1.0
            evidence.append(f"Shared emails: {emails_a & emails_b}")
        elif emails_a and emails_b:
            # Check for domain similarity
            domains_a = {self._extract_email_domain(e) for e in emails_a}
            domains_b = {self._extract_email_domain(e) for e in emails_b}
            if domains_a & domains_b:
                signals["email_domain"] = 0.5
                evidence.append(f"Shared email domains: {domains_a & domains_b}")

        # Alias matching
        aliases_a = set(profile_a.aliases + [profile_a.primary_name])
        aliases_b = set(profile_b.aliases + [profile_b.primary_name])

        if aliases_a & aliases_b:
            signals["alias_match"] = 1.0
            evidence.append(f"Shared aliases: {aliases_a & aliases_b}")
        else:
            # Check for similar aliases
            max_alias_sim = 0.0
            for a1 in aliases_a:
                for a2 in aliases_b:
                    sim = self.compute_username_similarity(a1, a2)
                    max_alias_sim = max(max_alias_sim, sim)
            if max_alias_sim > 0.7:
                signals["alias_match"] = max_alias_sim

        # Platform overlap (different usernames on same platform is negative)
        platforms_a = profile_a.get_platforms()
        platforms_b = profile_b.get_platforms()
        shared_platforms = platforms_a & platforms_b

        if shared_platforms:
            # Check if usernames differ on shared platforms
            for platform in shared_platforms:
                u1 = profile_a.get_username(platform)
                u2 = profile_b.get_username(platform)
                if u1 and u2 and u1.lower() != u2.lower():
                    # Different usernames on same platform - reduce confidence
                    signals["username_similarity"] = signals.get("username_similarity", 0) * 0.5
                    evidence.append(f"Different usernames on {platform}: {u1} vs {u2}")

        # Compute weighted score
        total_weight = 0.0
        weighted_score = 0.0

        for signal, score in signals.items():
            weight = self.signal_weights.get(signal, 0.5)
            weighted_score += score * weight
            total_weight += weight

        final_score = weighted_score / total_weight if total_weight > 0 else 0.0

        match = IdentityMatch(
            profile_a=profile_a.id,
            profile_b=profile_b.id,
            match_score=final_score,
            match_signals=signals,
            evidence=evidence,
        )

        self._match_cache[cache_key] = match
        self._stats["matches_computed"] += 1

        return match

    def find_matches(self, profile_id: str, min_score: Optional[float] = None) -> List[IdentityMatch]:
        """
        Find potential matches for a profile.

        Args:
            profile_id: Profile ID to find matches for
            min_score: Minimum match score (uses similarity_threshold if None)

        Returns:
            List of IdentityMatch objects sorted by score
        """
        if profile_id not in self._profiles:
            logger.warning(f"Profile {profile_id} not found")
            return []

        threshold = min_score if min_score is not None else self.similarity_threshold
        profile = self._profiles[profile_id]
        matches: List[IdentityMatch] = []

        # Quick candidate selection using indexes
        candidates: Set[str] = set()

        # Add profiles with similar usernames
        for username in profile.get_all_usernames():
            normalized = self._normalize_username(username)
            candidates.update(self._username_index.get(normalized, set()))

        # Add profiles with shared emails
        for email in profile.emails:
            normalized = self._normalize_email(email)
            candidates.update(self._email_index.get(normalized, set()))

        # Add profiles with similar aliases
        for alias in profile.aliases + [profile.primary_name]:
            normalized = self._normalize_text(alias)
            candidates.update(self._alias_index.get(normalized, set()))

        # Remove self
        candidates.discard(profile_id)

        # Compute matches for candidates
        for candidate_id in candidates:
            candidate = self._profiles.get(candidate_id)
            if not candidate:
                continue

            match = self.compute_match(profile, candidate)
            if match.match_score >= threshold:
                matches.append(match)

        # Sort by score descending
        matches.sort(key=lambda m: m.match_score, reverse=True)

        return matches

    def find_all_matches(self, min_score: Optional[float] = None) -> List[IdentityMatch]:
        """
        Find all matches across all profiles.

        Args:
            min_score: Minimum match score

        Returns:
            List of IdentityMatch objects
        """
        threshold = min_score if min_score is not None else self.similarity_threshold
        matches: List[IdentityMatch] = []
        seen_pairs: Set[Tuple[str, str]] = set()

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

    # ========================================================================
    # Identity Stitching
    # ========================================================================

    def stitch_identities(
        self,
        match_threshold: float = 0.8,
        transitive_threshold: float = 0.6,
    ) -> List[StitchedIdentity]:
        """
        Stitch identities based on matches.

        Args:
            match_threshold: Threshold for direct stitching
            transitive_threshold: Threshold for transitive stitching

        Returns:
            List of StitchedIdentity objects
        """
        if not NETWORKX_AVAILABLE:
            raise ImportError("NetworkX is required for identity stitching")

        start_time = time.time()
        nx = _get_nx()

        # Build match graph
        graph = nx.Graph()

        # Add all profiles as nodes
        for profile_id in self._profiles:
            graph.add_node(profile_id)

        # Add edges for matches above threshold
        matches = self.find_all_matches(min_score=match_threshold)
        for match in matches:
            graph.add_edge(
                match.profile_a,
                match.profile_b,
                weight=match.match_score,
                match=match,
            )

        # Find connected components (stitched identities)
        stitched: List[StitchedIdentity] = []

        for component in nx.connected_components(graph):
            if len(component) == 1:
                # Single profile - not stitched
                continue

            profile_ids = list(component)
            primary_id = profile_ids[0]

            # Collect all data from constituent profiles
            all_names: Set[str] = set()
            all_emails: Set[str] = set()
            all_usernames: List[UsernameEntry] = []
            all_evidence: List[str] = []
            total_confidence = 0.0

            for pid in profile_ids:
                profile = self._profiles[pid]
                all_names.add(profile.primary_name)
                all_names.update(profile.aliases)
                all_emails.update(profile.emails)
                all_usernames.extend(profile.usernames)

            # Collect evidence from edges
            for i, pid_a in enumerate(profile_ids):
                for pid_b in profile_ids[i + 1:]:
                    if graph.has_edge(pid_a, pid_b):
                        match = graph[pid_a][pid_b].get("match")
                        if match:
                            total_confidence += match.match_score
                            all_evidence.extend(match.evidence)

            # Average confidence
            edge_count = sum(1 for _ in nx.edges(graph.subgraph(component)))
            avg_confidence = total_confidence / edge_count if edge_count > 0 else 0.0

            stitched_identity = StitchedIdentity(
                id=f"stitched_{primary_id}",
                profile_ids=profile_ids,
                primary_profile=primary_id,
                merged_names=list(all_names),
                merged_emails=list(all_emails),
                merged_usernames=all_usernames,
                stitch_confidence=avg_confidence,
                match_evidence=list(set(all_evidence)),
            )

            stitched.append(stitched_identity)

        self._stats["identities_stitched"] += len(stitched)

        logger.info(f"Stitched {len(stitched)} identities in {time.time() - start_time:.3f}s")
        return stitched

    # ========================================================================
    # Graph Operations
    # ========================================================================

    def get_identity_graph(self) -> Any:
        """
        Get the identity graph with all profiles and matches.

        Returns:
            NetworkX Graph with identity data
        """
        if not NETWORKX_AVAILABLE:
            raise ImportError("NetworkX is required for graph operations")

        if self._identity_graph is not None:
            return self._identity_graph

        nx = _get_nx()
        graph = nx.Graph()

        # Add nodes for all profiles
        for profile_id, profile in self._profiles.items():
            graph.add_node(
                profile_id,
                primary_name=profile.primary_name,
                aliases=profile.aliases,
                emails=profile.emails,
                platforms=list(profile.get_platforms()),
                confidence=profile.confidence,
            )

        # Add edges for all matches above threshold
        matches = self.find_all_matches()
        for match in matches:
            graph.add_edge(
                match.profile_a,
                match.profile_b,
                weight=match.match_score,
                confidence=match.confidence,
                signals=match.match_signals,
            )

        self._identity_graph = graph
        self._stats["graphs_built"] += 1

        return graph

    def get_identity_communities(self) -> List[Set[str]]:
        """
        Detect communities in the identity graph.

        Returns:
            List of communities (sets of profile IDs)
        """
        if not NETWORKX_AVAILABLE:
            raise ImportError("NetworkX is required for community detection")

        graph = self.get_identity_graph()

        if graph.number_of_nodes() == 0:
            return []

        nx = _get_nx()
        # Use connected components as communities
        return list(nx.connected_components(graph))

    # ========================================================================
    # Integration with RelationshipDiscoveryEngine
    # ========================================================================

    def to_entities_and_relationships(
        self,
        stitched_identities: Optional[List[StitchedIdentity]] = None,
    ) -> Tuple[List[Any], List[Any]]:
        """
        Convert stitched identities to Entity and Relationship objects.

        Args:
            stitched_identities: Pre-computed stitched identities (optional)

        Returns:
            Tuple of (entities, relationships) for RelationshipDiscoveryEngine
        """
        if not RELATIONSHIP_AVAILABLE:
            raise ImportError("relationship_discovery module not available")

        if stitched_identities is None:
            stitched_identities = self.stitch_identities()

        entities: List[Entity] = []
        relationships: List[Relationship] = []

        for stitched in stitched_identities:
            # Create entity for stitched identity
            entity = Entity(
                id=stitched.id,
                type=EntityType.DIGITAL_IDENTITY,
                attributes={
                    "merged_names": stitched.merged_names,
                    "merged_emails": stitched.merged_emails,
                    "profile_count": len(stitched.profile_ids),
                    "stitch_confidence": stitched.stitch_confidence,
                },
                sources=stitched.profile_ids,
            )
            entities.append(entity)

            # Create relationships between constituent profiles
            for i, pid_a in enumerate(stitched.profile_ids):
                for pid_b in stitched.profile_ids[i + 1:]:
                    rel = Relationship(
                        source=pid_a,
                        target=pid_b,
                        type=RelationshipType.RELATED_TO,
                        strength=stitched.stitch_confidence,
                        confidence=stitched.stitch_confidence,
                        evidence=stitched.match_evidence,
                    )
                    relationships.append(rel)

        return entities, relationships

    # ========================================================================
    # Export and Serialization
    # ========================================================================

    def to_dict(self) -> Dict[str, Any]:
        """Export engine state as dictionary."""
        return {
            "profiles": {k: v.to_dict() for k, v in self._profiles.items()},
            "stats": self._stats,
            "similarity_threshold": self.similarity_threshold,
            "signal_weights": self.signal_weights,
        }

    def export_matches(self) -> List[Dict[str, Any]]:
        """Export all matches as list of dictionaries."""
        matches = self.find_all_matches()
        return [m.to_dict() for m in matches]

    def export_stitched(self) -> List[Dict[str, Any]]:
        """Export stitched identities as list of dictionaries."""
        stitched = self.stitch_identities()
        return [s.to_dict() for s in stitched]

    def get_stats(self) -> Dict[str, Any]:
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

        logger.info("IdentityStitchingEngine cleared")

    # ========================================================================
    # Memory Management (M1 8GB Optimized)
    # ========================================================================

    def optimize_memory(self):
        """Optimize memory usage by clearing caches and forcing GC."""
        self._identity_graph = None
        self._similarity_cache.clear()
        self._match_cache.clear()

        gc.collect()

        logger.debug("Memory optimization completed")

    def get_memory_usage(self) -> Dict[str, int]:
        """Estimate memory usage of key data structures."""
        import sys

        profile_size = sum(sys.getsizeof(p) for p in self._profiles.values())

        index_size = (
            sum(sys.getsizeof(s) for s in self._username_index.values()) +
            sum(sys.getsizeof(s) for s in self._email_index.values()) +
            sum(sys.getsizeof(s) for s in self._alias_index.values())
        )

        return {
            "profiles_bytes": profile_size,
            "indexes_bytes": index_size,
            "total_bytes": profile_size + index_size,
            "profile_count": len(self._profiles),
            "cache_entries": len(self._similarity_cache) + len(self._match_cache),
        }


# Factory function
def create_identity_stitching_engine(
    similarity_threshold: float = 0.7,
    signal_weights: Optional[Dict[str, float]] = None,
    max_memory_mb: int = 512,
    enable_fuzzy: bool = True,
) -> IdentityStitchingEngine:
    """Factory function to create an IdentityStitchingEngine."""
    return IdentityStitchingEngine(
        similarity_threshold=similarity_threshold,
        signal_weights=signal_weights,
        max_memory_mb=max_memory_mb,
        enable_fuzzy=enable_fuzzy,
    )


# Example usage
async def example_usage():
    """Example usage of the IdentityStitchingEngine."""
    engine = create_identity_stitching_engine(similarity_threshold=0.6)

    # Create profiles
    profiles = [
        IdentityProfile(
            id="alice_twitter",
            primary_name="Alice Smith",
            emails=["alice@example.com"],
            aliases=["alice_s"],
        ),
        IdentityProfile(
            id="alice_github",
            primary_name="Alice Smith",
            emails=["alice@example.com"],
            aliases=["alicecodes"],
        ),
        IdentityProfile(
            id="bob_twitter",
            primary_name="Bob Jones",
            emails=["bob@example.com"],
        ),
        IdentityProfile(
            id="alice_alt",
            primary_name="Alice S.",
            emails=["alice.smith@example.com"],
            aliases=["alice_smith"],
        ),
    ]

    # Add usernames
    profiles[0].add_username("twitter", "alice_smith", verified=True)
    profiles[1].add_username("github", "alicecodes")
    profiles[2].add_username("twitter", "bobjones")
    profiles[3].add_username("reddit", "alice_s")

    # Add profiles to engine
    for profile in profiles:
        engine.add_profile(profile)

    print("=== Finding Matches ===")
    for profile in profiles:
        matches = engine.find_matches(profile.id)
        if matches:
            print(f"\n{profile.primary_name} ({profile.id}):")
            for match in matches[:3]:
                print(f"  -> {match.profile_b}: {match.match_score:.2f} ({match.confidence})")
                print(f"     Signals: {match.match_signals}")

    print("\n=== Stitching Identities ===")
    stitched = engine.stitch_identities(match_threshold=0.7)
    for identity in stitched:
        print(f"\nStitched Identity: {identity.id}")
        print(f"  Profiles: {identity.profile_ids}")
        print(f"  Names: {identity.merged_names}")
        print(f"  Emails: {identity.merged_emails}")
        print(f"  Confidence: {identity.stitch_confidence:.2f}")

    print("\n=== Identity Graph Stats ===")
    graph = engine.get_identity_graph()
    print(f"  Nodes: {graph.number_of_nodes()}")
    print(f"  Edges: {graph.number_of_edges()}")

    # Export for RelationshipDiscoveryEngine
    if RELATIONSHIP_AVAILABLE:
        print("\n=== Export for RelationshipDiscoveryEngine ===")
        entities, relationships = engine.to_entities_and_relationships(stitched)
        print(f"  Entities: {len(entities)}")
        print(f"  Relationships: {len(relationships)}")

    # Cleanup
    engine.clear()


if __name__ == "__main__":
    import asyncio
    asyncio.run(example_usage())
