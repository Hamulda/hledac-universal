"""
InferenceEngine - Advanced Inference and Reasoning for OSINT
===========================================================

M1 8GB Optimized inference engine providing:
- Abductive reasoning (finding best explanations for observations)
- Evidence chaining (connecting facts through inference chains)
- Probabilistic entity resolution (merging fragmented identities)
- Bayesian belief updating
- Indirect evidence inference

OSINT Inference Rules:
- Co-location rule (same IP → same actor)
- Temporal proximity rule (events close in time → related)
- Communication pattern rule (frequent communication → relationship)
- Writing style similarity (stylometry-based identity linking)
- Behavioral fingerprinting

Memory Optimizations:
- Streaming inference for large datasets
- Memory-efficient graph operations
- Rule-based + lightweight probabilistic (no heavy ML models)
- MLX-accelerated similarity computations
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import logging
import math
import time
import heapq
from collections import defaultdict, deque, OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple, Iterator, Callable
from enum import Enum

import numpy as np

logger = logging.getLogger(__name__)

# MLX availability check (M1 optimization)
try:
    import mlx.core as mx
    MLX_AVAILABLE = True
except ImportError:
    MLX_AVAILABLE = False
    mx = None


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class Evidence:
    """Single piece of evidence with metadata."""
    fact: str
    confidence: float  # 0-1
    source: str
    timestamp: float
    metadata: Dict[str, Any] = field(default_factory=dict)
    evidence_id: str = field(default="")

    def __post_init__(self):
        if not self.evidence_id:
            # Generate deterministic ID from content
            content = f"{self.fact}:{self.source}:{self.timestamp}"
            self.evidence_id = hashlib.md5(content.encode()).hexdigest()[:12]
        # Clamp confidence to valid range
        self.confidence = max(0.0, min(1.0, self.confidence))

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "evidence_id": self.evidence_id,
            "fact": self.fact,
            "confidence": self.confidence,
            "source": self.source,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }


@dataclass
class InferenceStep:
    """Single step in an inference chain."""
    from_statement: str
    to_statement: str
    rule: str
    confidence: float
    step_number: int = 0
    evidence_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "step_number": self.step_number,
            "from": self.from_statement,
            "to": self.to_statement,
            "rule": self.rule,
            "confidence": self.confidence,
            "evidence_ids": self.evidence_ids,
        }


@dataclass
class Hypothesis:
    """Generated hypothesis with probabilistic assessment."""
    statement: str
    prior_probability: float
    posterior_probability: float = field(default=0.0)
    supporting_evidence: List[str] = field(default_factory=list)
    conflicting_evidence: List[str] = field(default_factory=list)
    inference_chain: List[InferenceStep] = field(default_factory=list)
    hypothesis_id: str = field(default="")
    created_at: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.hypothesis_id:
            content = f"{self.statement}:{self.created_at}"
            self.hypothesis_id = hashlib.md5(content.encode()).hexdigest()[:12]
        if self.posterior_probability == 0.0:
            self.posterior_probability = self.prior_probability
        # Clamp probabilities
        self.prior_probability = max(0.0, min(1.0, self.prior_probability))
        self.posterior_probability = max(0.0, min(1.0, self.posterior_probability))

    @property
    def confidence(self) -> float:
        """Alias for posterior probability."""
        return self.posterior_probability

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "hypothesis_id": self.hypothesis_id,
            "statement": self.statement,
            "prior_probability": self.prior_probability,
            "posterior_probability": self.posterior_probability,
            "supporting_evidence": self.supporting_evidence,
            "conflicting_evidence": self.conflicting_evidence,
            "inference_chain": [step.to_dict() for step in self.inference_chain],
            "created_at": self.created_at,
            "metadata": self.metadata,
        }


@dataclass
class ResolvedEntity:
    """Result of probabilistic entity resolution."""
    entity_id: str
    canonical_name: str
    aliases: List[str] = field(default_factory=list)
    fragments: List[Dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.0
    resolution_method: str = ""
    attributes: Dict[str, Any] = field(default_factory=dict)
    source_evidence: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "entity_id": self.entity_id,
            "canonical_name": self.canonical_name,
            "aliases": self.aliases,
            "fragment_count": len(self.fragments),
            "confidence": self.confidence,
            "resolution_method": self.resolution_method,
            "attributes": self.attributes,
            "source_evidence": self.source_evidence,
        }


@dataclass
class InferenceRule:
    """Definition of an inference rule."""
    name: str
    description: str
    condition: Callable[[Dict[str, Any], Dict[str, Any]], bool]
    confidence_multiplier: float
    applies_to: List[str] = field(default_factory=list)

    def evaluate(self, evidence_a: Dict[str, Any], evidence_b: Dict[str, Any]) -> bool:
        """Evaluate if rule applies to given evidence pair."""
        try:
            return self.condition(evidence_a, evidence_b)
        except Exception as e:
            logger.debug(f"Rule {self.name} evaluation failed: {e}")
            return False


class InferenceType(Enum):
    """Types of inference operations."""
    ABDUCTIVE = "abductive"
    DEDUCTIVE = "deductive"
    INDUCTIVE = "inductive"
    ANALOGICAL = "analogical"
    CAUSAL = "causal"
    MULTI_HOP = "multi_hop"


# =============================================================================
# MULTI-HOP REASONING DATA CLASSES
# =============================================================================

@dataclass
class HopStep:
    """Single step in a multi-hop reasoning chain.

    Represents one inference hop from one entity to another,
    including the relationship type, confidence, and supporting evidence.

    Attributes:
        step_number: Position in the hop sequence (1-indexed)
        from_entity: Source entity identifier
        to_entity: Target entity identifier
        relation: Type of relationship connecting the entities
        confidence: Confidence score for this hop (0-1)
        evidence: Supporting evidence for this relationship
    """
    step_number: int
    from_entity: str
    to_entity: str
    relation: str
    confidence: float
    evidence: str

    def __post_init__(self):
        """Validate and normalize hop step data."""
        self.confidence = max(0.0, min(1.0, self.confidence))
        self.step_number = max(1, self.step_number)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "step_number": self.step_number,
            "from_entity": self.from_entity,
            "to_entity": self.to_entity,
            "relation": self.relation,
            "confidence": self.confidence,
            "evidence": self.evidence,
        }


@dataclass
class MultiHopPath:
    """Complete multi-hop reasoning path between entities.

    Represents a full inference chain from a start entity to an end entity,
    with confidence scoring and cycle detection.

    Attributes:
        start_entity: Starting entity identifier
        end_entity: Target entity identifier
        hops: List of HopStep objects forming the path
        total_confidence: Compounded confidence across all hops
        path_length: Number of hops in the path
        is_cyclic: Whether the path contains cycles
    """
    start_entity: str
    end_entity: str
    hops: List[HopStep] = field(default_factory=list)
    total_confidence: float = 0.0
    path_length: int = 0
    is_cyclic: bool = False

    def __post_init__(self):
        """Calculate derived properties after initialization."""
        self.path_length = len(self.hops)
        self.total_confidence = self._calculate_compound_confidence()
        self.is_cyclic = self._detect_cycles()

    def _calculate_compound_confidence(self) -> float:
        """Calculate compounded confidence across all hops.

        Uses product of individual confidences with length penalty:
        compound = prod(hop_confidences) * (0.9 ^ (path_length - 1))
        """
        if not self.hops:
            return 0.0

        # Product of individual confidences
        product_confidence = 1.0
        for hop in self.hops:
            product_confidence *= hop.confidence

        # Apply length penalty for longer paths
        length_penalty = 0.9 ** (self.path_length - 1)

        return product_confidence * length_penalty

    def _detect_cycles(self) -> bool:
        """Detect if the path contains any cycles.

        A cycle occurs when an entity appears more than once in the path.
        """
        seen_entities = set()
        entities = [self.start_entity]
        for hop in self.hops:
            entities.append(hop.to_entity)

        for entity in entities:
            if entity in seen_entities:
                return True
            seen_entities.add(entity)

        return False

    @property
    def final_score(self) -> float:
        """Alias for total_confidence with length penalty applied."""
        return self.total_confidence

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "start_entity": self.start_entity,
            "end_entity": self.end_entity,
            "hops": [hop.to_dict() for hop in self.hops],
            "total_confidence": self.total_confidence,
            "path_length": self.path_length,
            "is_cyclic": self.is_cyclic,
        }

    def get_entities(self) -> List[str]:
        """Get all entities in the path in order."""
        entities = [self.start_entity]
        for hop in self.hops:
            entities.append(hop.to_entity)
        return entities

    def explain(self) -> str:
        """Generate human-readable explanation of the path."""
        if not self.hops:
            return f"Direct connection: {self.start_entity} -> {self.end_entity}"

        lines = [
            f"Multi-hop path from '{self.start_entity}' to '{self.end_entity}':",
            f"  Total confidence: {self.total_confidence:.3f}",
            f"  Path length: {self.path_length} hops",
        ]

        if self.is_cyclic:
            lines.append("  WARNING: Path contains cycles")

        lines.append("")
        lines.append("  Inference chain:")

        for hop in self.hops:
            lines.append(
                f"    {hop.step_number}. {hop.from_entity} --[{hop.relation}]-> {hop.to_entity} "
                f"(confidence: {hop.confidence:.3f})"
            )

        return "\n".join(lines)


# =============================================================================
# INFERENCE ENGINE
# =============================================================================

class InferenceEngine:
    """
    Advanced inference engine for OSINT analysis.

    Provides probabilistic reasoning capabilities optimized for M1 8GB:
    - Streaming processing for large datasets
    - Memory-efficient graph operations
    - MLX-accelerated computations when available
    - Rule-based inference with Bayesian updating
    - Bounded evidence graph and evidence with deterministic LRU eviction

    OSINT-Specific Rules:
    - Co-location: Same IP/network → same actor
    - Temporal proximity: Events close in time → related
    - Communication patterns: Frequent contact → relationship
    - Stylometry: Writing style similarity → identity linking
    - Behavioral fingerprinting: Pattern matching → entity resolution
    """

    # Memory bounds for M1 8GB optimization
    MAX_GRAPH_NODES = 10_000
    MAX_EVIDENCE_ITEMS = 10_000
    MAX_BFS_QUEUE = 1_000
    MAX_BFS_DEPTH = 10

    def __init__(
        self,
        max_chain_depth: int = 5,
        min_confidence_threshold: float = 0.3,
        use_mlx: bool = True,
        streaming_batch_size: int = 1000,
    ):
        """
        Initialize InferenceEngine.

        Args:
            max_chain_depth: Maximum depth for evidence chaining
            min_confidence_threshold: Minimum confidence to consider evidence
            use_mlx: Whether to use MLX acceleration when available
            streaming_batch_size: Batch size for streaming operations
        """
        self.max_chain_depth = max_chain_depth
        self.min_confidence_threshold = min_confidence_threshold
        self.use_mlx = use_mlx and MLX_AVAILABLE
        self.streaming_batch_size = streaming_batch_size

        # Thread pool for safe async execution in sync context
        self._thread_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)

        # Evidence storage (bounded with LRU eviction)
        self._evidence: OrderedDict[str, Evidence] = OrderedDict()
        self._evidence_graph: OrderedDict[str, Set[str]] = OrderedDict()
        self._inference_rules: List[InferenceRule] = []

        # Internal counters for bounded operations
        self._graph_pruned_count = 0
        self._evidence_pruned_count = 0

        # Initialize OSINT-specific inference rules
        self._init_inference_rules()

        logger.info(
            f"InferenceEngine initialized (MLX: {self.use_mlx}, "
            f"max_depth: {max_chain_depth})"
        )

    def _run_coro_sync_safe(self, coro):
        """Run coroutine safely - in thread if loop is already running."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        return self._thread_pool.submit(asyncio.run, coro).result()

    def _shutdown_executor(self) -> None:
        """Shutdown thread pool fail-safe."""
        if hasattr(self, '_thread_pool'):
            try:
                self._thread_pool.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass

    def _init_inference_rules(self) -> None:
        """Initialize OSINT-specific inference rules."""

        # Co-location rule: Same IP/network → same actor
        colocation_rule = InferenceRule(
            name="co_location",
            description="Same IP address or network indicates same actor",
            condition=self._colocation_condition,
            confidence_multiplier=0.75,
            applies_to=["ip_address", "network", "location"],
        )

        # Temporal proximity rule: Events close in time → related
        temporal_rule = InferenceRule(
            name="temporal_proximity",
            description="Events occurring close in time are likely related",
            condition=self._temporal_proximity_condition,
            confidence_multiplier=0.6,
            applies_to=["timestamp", "event"],
        )

        # Communication pattern rule: Frequent communication → relationship
        communication_rule = InferenceRule(
            name="communication_pattern",
            description="Frequent communication indicates relationship",
            condition=self._communication_pattern_condition,
            confidence_multiplier=0.8,
            applies_to=["communication", "message", "contact"],
        )

        # Writing style similarity rule
        stylometry_rule = InferenceRule(
            name="writing_style_similarity",
            description="Similar writing style suggests same author",
            condition=self._stylometry_condition,
            confidence_multiplier=0.7,
            applies_to=["text", "writing", "content"],
        )

        # Behavioral fingerprinting rule
        behavioral_rule = InferenceRule(
            name="behavioral_fingerprinting",
            description="Similar behavioral patterns suggest same entity",
            condition=self._behavioral_condition,
            confidence_multiplier=0.65,
            applies_to=["behavior", "action", "pattern"],
        )

        self._inference_rules = [
            colocation_rule,
            temporal_rule,
            communication_rule,
            stylometry_rule,
            behavioral_rule,
        ]

    # =========================================================================
    # RULE CONDITIONS
    # =========================================================================

    def _colocation_condition(self, a: Dict[str, Any], b: Dict[str, Any]) -> bool:
        """Check if two evidence pieces share IP/network location."""
        ip_a = a.get("ip_address") or a.get("metadata", {}).get("ip")
        ip_b = b.get("ip_address") or b.get("metadata", {}).get("ip")

        if ip_a and ip_b and ip_a == ip_b:
            return True

        # Check network prefix (first 3 octets for /24)
        if ip_a and ip_b:
            try:
                net_a = ".".join(ip_a.split(".")[:3])
                net_b = ".".join(ip_b.split(".")[:3])
                if net_a == net_b:
                    return True
            except (AttributeError, IndexError):
                pass

        # Check geolocation
        loc_a = a.get("location") or a.get("metadata", {}).get("location")
        loc_b = b.get("location") or b.get("metadata", {}).get("location")
        if loc_a and loc_b and loc_a == loc_b:
            return True

        return False

    def _temporal_proximity_condition(self, a: Dict[str, Any], b: Dict[str, Any]) -> bool:
        """Check if two events are temporally close."""
        ts_a = a.get("timestamp") or a.get("metadata", {}).get("timestamp")
        ts_b = b.get("timestamp") or b.get("metadata", {}).get("timestamp")

        if ts_a is None or ts_b is None:
            return False

        # Events within 1 hour are considered related
        time_diff = abs(float(ts_a) - float(ts_b))
        return time_diff < 3600  # 1 hour in seconds

    def _communication_pattern_condition(self, a: Dict[str, Any], b: Dict[str, Any]) -> bool:
        """Check if evidence indicates frequent communication."""
        # Check for communication metadata
        comm_a = a.get("metadata", {}).get("communication_count", 0)
        comm_b = b.get("metadata", {}).get("communication_count", 0)

        if comm_a >= 3 or comm_b >= 3:
            return True

        # Check for bidirectional communication
        if a.get("metadata", {}).get("recipient") == b.get("metadata", {}).get("sender"):
            if b.get("metadata", {}).get("recipient") == a.get("metadata", {}).get("sender"):
                return True

        return False

    def _stylometry_condition(self, a: Dict[str, Any], b: Dict[str, Any]) -> bool:
        """Check if writing styles are similar."""
        text_a = a.get("text") or a.get("fact", "")
        text_b = b.get("text") or b.get("fact", "")

        if not text_a or not text_b or len(text_a) < 50 or len(text_b) < 50:
            return False

        # Simple stylometry: compare character distributions
        similarity = self._calculate_text_similarity(text_a, text_b)
        return similarity > 0.85

    def _behavioral_condition(self, a: Dict[str, Any], b: Dict[str, Any]) -> bool:
        """Check if behavioral patterns match."""
        behavior_a = a.get("metadata", {}).get("behavior_pattern", "")
        behavior_b = b.get("metadata", {}).get("behavior_pattern", "")

        if behavior_a and behavior_b and behavior_a == behavior_b:
            return True

        # Check action sequences
        actions_a = a.get("metadata", {}).get("actions", [])
        actions_b = b.get("metadata", {}).get("actions", [])

        if actions_a and actions_b:
            # Jaccard similarity of action sets
            set_a = set(actions_a)
            set_b = set(actions_b)
            if len(set_a.union(set_b)) > 0:
                similarity = len(set_a.intersection(set_b)) / len(set_a.union(set_b))
                return similarity > 0.7

        return False

    def _mlx_cosine_similarity(self, vec_a: np.ndarray, vec_b: np.ndarray) -> float:
        """GPU-accelerated cosine similarity using MLX with safe zero-check."""
        try:
            a_mx = mx.array(vec_a)
            b_mx = mx.array(vec_b)
            dot = mx.sum(a_mx * b_mx)
            norm_a = mx.sqrt(mx.sum(a_mx * a_mx))
            norm_b = mx.sqrt(mx.sum(b_mx * b_mx))
            # Convert to Python float for safe zero check
            if float(norm_a.item()) == 0.0 or float(norm_b.item()) == 0.0:
                return 0.0
            return (dot / (norm_a * norm_b)).item()
        except Exception as e:
            logger.debug(f"MLX similarity failed: {e}")
            return 0.0

    def _calculate_text_similarity(self, text_a: str, text_b: str) -> float:
        """Calculate stylometric similarity between two texts."""
        # Character frequency comparison
        def get_char_dist(text: str) -> Dict[str, float]:
            text = text.lower()
            total = max(len(text), 1)
            dist = defaultdict(int)
            for char in text:
                if char.isalnum() or char in ".,!?;:":
                    dist[char] += 1
            return {k: v / total for k, v in dist.items()}

        dist_a = get_char_dist(text_a)
        dist_b = get_char_dist(text_b)

        # Cosine similarity
        all_chars = set(dist_a.keys()) | set(dist_b.keys())
        if not all_chars:
            return 0.0

        vec_a = np.array([dist_a.get(c, 0) for c in all_chars])
        vec_b = np.array([dist_b.get(c, 0) for c in all_chars])

        # Use MLX if available for similarity computation
        if self.use_mlx:
            return self._mlx_cosine_similarity(vec_a, vec_b)

        # Fallback to numpy
        dot_product = np.dot(vec_a, vec_b)
        norm_a = np.linalg.norm(vec_a)
        norm_b = np.linalg.norm(vec_b)

        if norm_a > 0 and norm_b > 0:
            return dot_product / (norm_a * norm_b)
        return 0.0

    # =========================================================================
    # CORE INFERENCE METHODS
    # =========================================================================

    # -------------------------------------------------------------------------
    # Bounded evidence and graph with deterministic LRU eviction
    # -------------------------------------------------------------------------

    def _evict_evidence_if_needed(self) -> None:
        """Evict oldest evidence items if over MAX_EVIDENCE_ITEMS cap."""
        while len(self._evidence) > self.MAX_EVIDENCE_ITEMS:
            evicted_id, _ = self._evidence.popitem(last=False)
            # Also remove from graph (edges only, node may still exist)
            if evicted_id in self._evidence_graph:
                del self._evidence_graph[evicted_id]
            self._evidence_pruned_count += 1

    def _evict_graph_node_if_needed(self) -> None:
        """Evict oldest graph nodes if over MAX_GRAPH_NODES cap."""
        while len(self._evidence_graph) > self.MAX_GRAPH_NODES:
            evicted_id, _ = self._evidence_graph.popitem(last=False)
            # Bounded cleanup: iterate up to 200 other nodes to remove references
            cleanup_limit = 200
            cleaned = 0
            for node_id in list(self._evidence_graph.keys()):
                if cleaned >= cleanup_limit:
                    break
                if evicted_id in self._evidence_graph.get(node_id, set()):
                    self._evidence_graph[node_id].discard(evicted_id)
                    cleaned += 1
            self._graph_pruned_count += 1

    def add_evidence(self, evidence: Evidence) -> str:
        """
        Add evidence to the inference engine with bounded storage.

        Args:
            evidence: Evidence to add

        Returns:
            Evidence ID
        """
        # Move to end if exists (update = touch)
        if evidence.evidence_id in self._evidence:
            self._evidence.move_to_end(evidence.evidence_id)
        else:
            self._evidence[evidence.evidence_id] = evidence

        self._evict_evidence_if_needed()

        # Build evidence graph connections
        self._update_evidence_graph(evidence)

        return evidence.evidence_id

    def add_evidence_batch(self, evidence_list: List[Evidence]) -> List[str]:
        """
        Add multiple evidence items efficiently.

        Args:
            evidence_list: List of evidence to add

        Returns:
            List of evidence IDs
        """
        ids = []
        for evidence in evidence_list:
            ids.append(self.add_evidence(evidence))
        return ids

    def _update_evidence_graph(self, new_evidence: Evidence) -> None:
        """Update evidence graph with new connections (bounded)."""
        new_evidence_dict = new_evidence.to_dict()

        # Ensure node exists in graph
        if new_evidence.evidence_id not in self._evidence_graph:
            self._evidence_graph[new_evidence.evidence_id] = set()
        else:
            self._evidence_graph.move_to_end(new_evidence.evidence_id)

        # Check against existing evidence
        for existing_id, existing in self._evidence.items():
            if existing_id == new_evidence.evidence_id:
                continue

            existing_dict = existing.to_dict()

            # Apply inference rules
            for rule in self._inference_rules:
                if rule.evaluate(new_evidence_dict, existing_dict):
                    # Create bidirectional connection
                    if new_evidence.evidence_id not in self._evidence_graph:
                        self._evidence_graph[new_evidence.evidence_id] = set()
                    self._evidence_graph[new_evidence.evidence_id].add(existing_id)

                    if existing_id not in self._evidence_graph:
                        self._evidence_graph[existing_id] = set()
                    self._evidence_graph[existing_id].add(new_evidence.evidence_id)

                    logger.debug(
                        f"Graph connection: {rule.name} between "
                        f"{new_evidence.evidence_id} and {existing_id}"
                    )

        self._evict_graph_node_if_needed()

    def abductive_reasoning(
        self,
        observations: List[Evidence],
        max_hypotheses: int = 10,
    ) -> List[Hypothesis]:
        """
        Perform abductive reasoning to find best explanations for observations.

        Abductive reasoning infers the most likely cause from observed effects.
        Used in OSINT to hypothesize about actor identities, motivations, etc.

        Args:
            observations: List of observed evidence
            max_hypotheses: Maximum number of hypotheses to generate

        Returns:
            List of ranked hypotheses sorted by posterior probability
        """
        if not observations:
            return []

        hypotheses = []

        # Generate candidate explanations based on observation patterns
        candidate_explanations = self._generate_candidate_explanations(observations)

        for explanation in candidate_explanations:
            # Calculate prior probability based on base rates
            prior = self._calculate_prior_probability(explanation, observations)

            # Calculate likelihood of observations given explanation
            likelihood = self._calculate_likelihood(explanation, observations)

            # Apply Bayesian update
            posterior = self.update_beliefs(prior, likelihood, 1.0)

            # Build inference chain
            chain = self._build_inference_chain(explanation, observations)

            # Collect supporting and conflicting evidence
            supporting = []
            conflicting = []
            for obs in observations:
                if self._evidence_supports(obs, explanation):
                    supporting.append(obs.evidence_id)
                else:
                    conflicting.append(obs.evidence_id)

            hypothesis = Hypothesis(
                statement=explanation,
                prior_probability=prior,
                posterior_probability=posterior,
                supporting_evidence=supporting,
                conflicting_evidence=conflicting,
                inference_chain=chain,
            )

            if posterior >= self.min_confidence_threshold:
                hypotheses.append(hypothesis)

        # Sort by posterior probability (descending)
        hypotheses.sort(key=lambda h: h.posterior_probability, reverse=True)

        logger.info(
            f"Abductive reasoning: {len(observations)} observations → "
            f"{len(hypotheses)} hypotheses"
        )

        return hypotheses[:max_hypotheses]

    def _generate_candidate_explanations(self, observations: List[Evidence]) -> List[str]:
        """Generate candidate explanations from observations."""
        explanations = set()

        # Extract entities and patterns from observations
        entities = defaultdict(list)
        for obs in observations:
            # Extract potential actors
            for key in ["actor", "entity", "user", "author", "source"]:
                if key in obs.metadata:
                    entities[key].append(obs.metadata[key])

            # Extract from fact text
            words = obs.fact.split()
            for word in words:
                if word[0].isupper() and len(word) > 3:
                    entities["extracted"].append(word)

        # Generate explanations based on entity co-occurrence
        for entity_type, entity_list in entities.items():
            if len(entity_list) >= 2:
                unique_entities = list(set(entity_list))
                for i, entity_a in enumerate(unique_entities):
                    for entity_b in unique_entities[i+1:]:
                        explanations.add(
                            f"{entity_a} and {entity_b} are the same actor"
                        )
                        explanations.add(
                            f"{entity_a} and {entity_b} are collaborating"
                        )

        # Generate temporal explanations
        timestamps = [obs.timestamp for obs in observations if obs.timestamp > 0]
        if timestamps:
            time_range = max(timestamps) - min(timestamps)
            if time_range < 86400:  # 24 hours
                explanations.add("Events are part of a coordinated campaign")
            elif time_range < 604800:  # 1 week
                explanations.add("Events are part of a sustained operation")

        # Generate location-based explanations
        locations = set()
        for obs in observations:
            loc = obs.metadata.get("location") or obs.metadata.get("country")
            if loc:
                locations.add(loc)

        if len(locations) == 1:
            explanations.add(f"Activity originates from {list(locations)[0]}")
        elif len(locations) > 1:
            explanations.add("Distributed operation across multiple locations")

        return list(explanations)

    def _calculate_prior_probability(
        self,
        explanation: str,
        observations: List[Evidence],
    ) -> float:
        """Calculate prior probability of an explanation."""
        # Base rate from historical data (simplified)
        base_rate = 0.1

        # Adjust based on explanation complexity
        complexity_penalty = 1.0 / (1 + len(explanation.split()) * 0.05)

        # Adjust based on evidence specificity
        specificity_boost = min(len(observations) * 0.05, 0.3)

        prior = base_rate * complexity_penalty + specificity_boost
        return min(prior, 0.95)

    def _calculate_likelihood(
        self,
        explanation: str,
        observations: List[Evidence],
    ) -> float:
        """Calculate likelihood of observations given explanation."""
        if not observations:
            return 0.0

        # Count how many observations are consistent with explanation
        consistent_count = 0
        for obs in observations:
            if self._evidence_supports(obs, explanation):
                consistent_count += 1

        # Likelihood is proportion of consistent observations
        likelihood = consistent_count / len(observations)

        # Weight by average confidence
        avg_confidence = sum(obs.confidence for obs in observations) / len(observations)

        return likelihood * avg_confidence

    def _evidence_supports(self, evidence: Evidence, explanation: str) -> bool:
        """Check if evidence supports an explanation."""
        explanation_lower = explanation.lower()
        fact_lower = evidence.fact.lower()

        # Direct text matching
        if any(word in fact_lower for word in explanation_lower.split()):
            return True

        # Check metadata
        for value in evidence.metadata.values():
            if isinstance(value, str) and value.lower() in explanation_lower:
                return True

        return False

    def _build_inference_chain(
        self,
        explanation: str,
        observations: List[Evidence],
    ) -> List[InferenceStep]:
        """Build inference chain from observations to explanation."""
        chain = []

        for i, obs in enumerate(observations):
            step = InferenceStep(
                from_statement=obs.fact,
                to_statement=explanation if i == len(observations) - 1 else f"Intermediate inference {i+1}",
                rule="abductive_inference",
                confidence=obs.confidence,
                step_number=i + 1,
                evidence_ids=[obs.evidence_id],
            )
            chain.append(step)

        return chain

    def evidence_chaining(
        self,
        start: str,
        target: str,
        max_depth: int = 5,
    ) -> Optional[List[InferenceStep]]:
        """
        Find inference chain connecting start to target through evidence.

        Uses breadth-first search through evidence graph to find
        the strongest chain of inferences connecting two statements.

        Args:
            start: Starting statement or evidence ID
            target: Target statement or evidence ID
            max_depth: Maximum chain depth

        Returns:
            List of inference steps or None if no chain found
        """
        # Find starting evidence
        start_ids = self._find_evidence_by_content(start)
        if not start_ids:
            logger.warning(f"No evidence found for start: {start}")
            return None

        # Find target evidence
        target_ids = self._find_evidence_by_content(target)
        if not target_ids:
            logger.warning(f"No evidence found for target: {target}")
            return None

        # BFS to find shortest path
        for start_id in start_ids:
            for target_id in target_ids:
                chain = self._bfs_chain(start_id, target_id, max_depth)
                if chain:
                    return chain

        return None

    def _find_evidence_by_content(self, content: str) -> List[str]:
        """Find evidence IDs matching content."""
        matching = []
        content_lower = content.lower()

        for evidence_id, evidence in self._evidence.items():
            if content_lower in evidence.fact.lower():
                matching.append(evidence_id)
            elif evidence_id.startswith(content):
                matching.append(evidence_id)

        return matching

    def _bfs_chain(
        self,
        start_id: str,
        target_id: str,
        max_depth: int,
    ) -> Optional[List[InferenceStep]]:
        """Breadth-first search for inference chain."""
        if start_id == target_id:
            return []

        # Queue: (current_id, path, depth)
        queue = deque([(start_id, [start_id], 0)])
        visited = {start_id}

        while queue:
            current_id, path, depth = queue.popleft()

            if depth >= max_depth:
                continue

            # Check neighbors
            neighbors = self._evidence_graph.get(current_id, set())

            for neighbor_id in neighbors:
                if neighbor_id in visited:
                    continue

                new_path = path + [neighbor_id]

                if neighbor_id == target_id:
                    # Build inference chain from path
                    return self._path_to_chain(new_path)

                visited.add(neighbor_id)
                queue.append((neighbor_id, new_path, depth + 1))

        return None

    def _path_to_chain(self, path: List[str]) -> List[InferenceStep]:
        """Convert evidence path to inference chain."""
        chain = []

        for i in range(len(path) - 1):
            from_id = path[i]
            to_id = path[i + 1]

            from_ev = self._evidence.get(from_id)
            to_ev = self._evidence.get(to_id)

            if from_ev and to_ev:
                # Determine which rule connected these
                rule_name = "evidence_connection"
                confidence = min(from_ev.confidence, to_ev.confidence) * 0.9

                step = InferenceStep(
                    from_statement=from_ev.fact,
                    to_statement=to_ev.fact,
                    rule=rule_name,
                    confidence=confidence,
                    step_number=i + 1,
                    evidence_ids=[from_id, to_id],
                )
                chain.append(step)

        return chain

    def probabilistic_entity_resolution(
        self,
        fragments: List[Dict[str, Any]],
        similarity_threshold: float = 0.7,
    ) -> List[ResolvedEntity]:
        """
        Merge fragmented entity identities using probabilistic matching.

        Uses multiple signals (name similarity, attributes, behavioral patterns)
        to cluster fragments into resolved entities.

        Args:
            fragments: List of entity fragments with attributes
            similarity_threshold: Minimum similarity to merge fragments

        Returns:
            List of resolved entities
        """
        if not fragments:
            return []

        # Compute similarity matrix
        n = len(fragments)
        similarity_matrix = np.zeros((n, n))

        for i in range(n):
            for j in range(i + 1, n):
                similarity = self._compute_fragment_similarity(
                    fragments[i], fragments[j]
                )
                similarity_matrix[i, j] = similarity
                similarity_matrix[j, i] = similarity

        # Cluster fragments using greedy merging
        clusters = self._cluster_fragments(similarity_matrix, similarity_threshold)

        # Build resolved entities from clusters
        resolved_entities = []
        for cluster_idx, cluster in enumerate(clusters):
            if not cluster:
                continue

            cluster_fragments = [fragments[i] for i in cluster]

            # Determine canonical name
            names = [f.get("name", "") for f in cluster_fragments if f.get("name")]
            canonical_name = self._select_canonical_name(names) if names else f"Entity_{cluster_idx}"

            # Collect aliases
            all_names = set(names)
            all_names.discard(canonical_name)

            # Merge attributes
            merged_attributes = {}
            for fragment in cluster_fragments:
                for key, value in fragment.items():
                    if key not in ["name", "source", "timestamp"]:
                        if key not in merged_attributes:
                            merged_attributes[key] = set()
                        if isinstance(value, (list, set)):
                            merged_attributes[key].update(value)
                        else:
                            merged_attributes[key].add(value)

            # Convert sets to lists for serialization
            merged_attributes = {
                k: list(v) if len(v) > 1 else list(v)[0]
                for k, v in merged_attributes.items()
            }

            # Calculate cluster confidence
            avg_similarity = np.mean([
                similarity_matrix[i, j]
                for i in cluster for j in cluster if i < j
            ]) if len(cluster) > 1 else 1.0

            # Collect source evidence
            evidence_ids = [
                f.get("evidence_id", "") for f in cluster_fragments
                if f.get("evidence_id")
            ]

            entity = ResolvedEntity(
                entity_id=f"entity_{cluster_idx}_{hashlib.md5(canonical_name.encode()).hexdigest()[:8]}",
                canonical_name=canonical_name,
                aliases=list(all_names),
                fragments=cluster_fragments,
                confidence=avg_similarity,
                resolution_method="probabilistic_clustering",
                attributes=merged_attributes,
                source_evidence=evidence_ids,
            )

            resolved_entities.append(entity)

        logger.info(
            f"Entity resolution: {len(fragments)} fragments → "
            f"{len(resolved_entities)} entities"
        )

        return resolved_entities

    def _compute_fragment_similarity(
        self,
        frag_a: Dict[str, Any],
        frag_b: Dict[str, Any],
    ) -> float:
        """Compute similarity score between two entity fragments."""
        scores = []
        weights = []

        # Name similarity
        name_a = frag_a.get("name", "")
        name_b = frag_b.get("name", "")
        if name_a and name_b:
            name_sim = self._string_similarity(name_a, name_b)
            scores.append(name_sim)
            weights.append(0.4)

        # Attribute similarity
        attrs_a = {k: v for k, v in frag_a.items() if k not in ["name", "source"]}
        attrs_b = {k: v for k, v in frag_b.items() if k not in ["name", "source"]}

        common_keys = set(attrs_a.keys()) & set(attrs_b.keys())
        if common_keys:
            matches = sum(
                1 for k in common_keys
                if attrs_a[k] == attrs_b[k]
            )
            attr_sim = matches / len(common_keys)
            scores.append(attr_sim)
            weights.append(0.35)

        # Behavioral similarity
        behavior_a = frag_a.get("behavior_pattern", "")
        behavior_b = frag_b.get("behavior_pattern", "")
        if behavior_a and behavior_b:
            behavior_sim = 1.0 if behavior_a == behavior_b else 0.0
            scores.append(behavior_sim)
            weights.append(0.25)

        if not scores:
            return 0.0

        # Weighted average
        return sum(s * w for s, w in zip(scores, weights)) / sum(weights)

    def _string_similarity(self, a: str, b: str) -> float:
        """Calculate string similarity using Jaro-Winkler-like approach."""
        a, b = a.lower(), b.lower()

        if a == b:
            return 1.0

        # Use MLX for similarity if available
        if self.use_mlx and len(a) > 0 and len(b) > 0:
            try:
                return self._mlx_string_similarity(a, b)
            except Exception as e:
                logger.debug(f"MLX string similarity failed: {e}")

        # Fallback: simple character-based similarity
        # Longest common subsequence ratio
        m, n = len(a), len(b)
        lcs = [[0] * (n + 1) for _ in range(m + 1)]

        for i in range(1, m + 1):
            for j in range(1, n + 1):
                if a[i - 1] == b[j - 1]:
                    lcs[i][j] = lcs[i - 1][j - 1] + 1
                else:
                    lcs[i][j] = max(lcs[i - 1][j], lcs[i][j - 1])

        lcs_length = lcs[m][n]
        return (2 * lcs_length) / (m + n) if (m + n) > 0 else 0.0

    def _mlx_string_similarity(self, a: str, b: str) -> float:
        """MLX-accelerated string similarity."""
        # Convert to character arrays
        max_len = max(len(a), len(b))
        if max_len == 0:
            return 0.0

        # Pad to same length
        a_padded = a.ljust(max_len, '\0')
        b_padded = b.ljust(max_len, '\0')

        # Create arrays
        a_arr = np.array([ord(c) for c in a_padded], dtype=np.float32)
        b_arr = np.array([ord(c) for c in b_padded], dtype=np.float32)

        # MLX computation
        mx_a = mx.array(a_arr)
        mx_b = mx.array(b_arr)

        # Cosine similarity on character vectors
        dot = mx.sum(mx_a * mx_b).item()
        norm_a = mx.sqrt(mx.sum(mx_a * mx_a)).item()
        norm_b = mx.sqrt(mx.sum(mx_b * mx_b)).item()

        if norm_a > 0 and norm_b > 0:
            return dot / (norm_a * norm_b)
        return 0.0

    def _cluster_fragments(
        self,
        similarity_matrix: np.ndarray,
        threshold: float,
    ) -> List[List[int]]:
        """Cluster fragments based on similarity matrix."""
        n = len(similarity_matrix)
        visited = [False] * n
        clusters = []

        for i in range(n):
            if visited[i]:
                continue

            # Start new cluster
            cluster = [i]
            visited[i] = True

            # Greedy expansion
            queue = deque([i])
            while queue:
                current = queue.popleft()

                for j in range(n):
                    if not visited[j] and similarity_matrix[current, j] >= threshold:
                        visited[j] = True
                        cluster.append(j)
                        queue.append(j)

            clusters.append(cluster)

        return clusters

    def _select_canonical_name(self, names: List[str]) -> str:
        """Select the most canonical name from a list."""
        if not names:
            return ""

        # Prefer longer, more specific names
        scored_names = [
            (name, len(name) + name.count(" ") * 2)
            for name in names
        ]
        scored_names.sort(key=lambda x: x[1], reverse=True)

        return scored_names[0][0]

    def update_beliefs(
        self,
        prior: float,
        likelihood: float,
        evidence_strength: float,
    ) -> float:
        """
        Update beliefs using Bayesian inference.

        P(H|E) = P(E|H) * P(H) / P(E)

        Args:
            prior: Prior probability P(H)
            likelihood: Likelihood P(E|H)
            evidence_strength: Strength of evidence (0-1)

        Returns:
            Posterior probability P(H|E)
        """
        # Clamp inputs
        prior = max(0.001, min(0.999, prior))
        likelihood = max(0.0, min(1.0, likelihood))
        evidence_strength = max(0.0, min(1.0, evidence_strength))

        # Calculate P(E) = P(E|H) * P(H) + P(E|¬H) * P(¬H)
        # Assume P(E|¬H) is low (false positive rate)
        false_positive_rate = 0.1 * (1 - evidence_strength)
        p_evidence = likelihood * prior + false_positive_rate * (1 - prior)

        if p_evidence == 0:
            return prior

        # Bayes' theorem
        posterior = (likelihood * prior) / p_evidence

        # Apply evidence strength as weight
        weighted_posterior = (
            evidence_strength * posterior +
            (1 - evidence_strength) * prior
        )

        return max(0.0, min(1.0, weighted_posterior))

    def calculate_joint_probability(self, hypotheses: List[Hypothesis]) -> float:
        """
        Calculate joint probability of multiple hypotheses.

        Assumes conditional independence for simplicity.
        For dependent hypotheses, use evidence_chaining instead.

        Args:
            hypotheses: List of hypotheses

        Returns:
            Joint probability
        """
        if not hypotheses:
            return 0.0

        # P(H1 ∧ H2 ∧ ... ∧ Hn) = P(H1) * P(H2) * ... * P(Hn)
        # (assuming independence)
        joint_prob = 1.0
        for hypothesis in hypotheses:
            joint_prob *= hypothesis.posterior_probability

        return joint_prob

    def indirect_evidence_inference(
        self,
        target_statement: str,
        max_hops: int = 3,
    ) -> List[InferenceStep]:
        """
        Infer indirect evidence supporting a target statement.

        Finds multi-hop inference chains where direct evidence is scarce
        but indirect connections exist.

        Args:
            target_statement: Statement to find evidence for
            max_hops: Maximum number of inference hops

        Returns:
            List of inference steps from indirect evidence
        """
        target_ids = self._find_evidence_by_content(target_statement)
        if not target_ids:
            return []

        indirect_chains = []

        for target_id in target_ids:
            # Find all paths up to max_hops
            paths = self._find_all_paths(target_id, max_hops)

            for path in paths:
                if len(path) > 1:
                    chain = self._path_to_chain(list(reversed(path)))
                    if chain:
                        indirect_chains.extend(chain)

        # Sort by confidence
        indirect_chains.sort(key=lambda x: x.confidence, reverse=True)

        return indirect_chains

    def _find_all_paths(
        self,
        start_id: str,
        max_depth: int,
    ) -> List[List[str]]:
        """Find all paths from start node up to max_depth."""
        paths = []

        def dfs(current: str, path: List[str], depth: int):
            if depth > max_depth:
                return

            neighbors = self._evidence_graph.get(current, set())
            for neighbor in neighbors:
                if neighbor not in path:
                    new_path = path + [neighbor]
                    paths.append(new_path)
                    dfs(neighbor, new_path, depth + 1)

        dfs(start_id, [start_id], 0)
        return paths

    def streaming_inference(
        self,
        evidence_iterator: Iterator[Evidence],
        callback: Optional[Callable[[Hypothesis], None]] = None,
    ) -> List[Hypothesis]:
        """
        Process evidence in streaming fashion for large datasets.

        Memory-efficient processing that yields hypotheses as evidence
        accumulates.

        Args:
            evidence_iterator: Iterator yielding evidence
            callback: Optional callback for each generated hypothesis

        Returns:
            Final list of ranked hypotheses
        """
        batch = []
        all_hypotheses = []

        for evidence in evidence_iterator:
            batch.append(evidence)
            self.add_evidence(evidence)

            # Process in batches
            if len(batch) >= self.streaming_batch_size:
                hypotheses = self.abductive_reasoning(batch, max_hypotheses=5)
                all_hypotheses.extend(hypotheses)

                if callback:
                    for hyp in hypotheses:
                        callback(hyp)

                batch = []

        # Process remaining evidence
        if batch:
            hypotheses = self.abductive_reasoning(batch, max_hypotheses=5)
            all_hypotheses.extend(hypotheses)

            if callback:
                for hyp in hypotheses:
                    callback(hyp)

        # Deduplicate and rank
        seen_statements = set()
        unique_hypotheses = []
        for hyp in all_hypotheses:
            if hyp.statement not in seen_statements:
                seen_statements.add(hyp.statement)
                unique_hypotheses.append(hyp)

        unique_hypotheses.sort(key=lambda h: h.posterior_probability, reverse=True)

        return unique_hypotheses

    def get_evidence_stats(self) -> Dict[str, Any]:
        """Get statistics about stored evidence."""
        return {
            "total_evidence": len(self._evidence),
            "graph_edges": sum(len(neighbors) for neighbors in self._evidence_graph.values()) // 2,
            "avg_confidence": (
                sum(e.confidence for e in self._evidence.values()) / len(self._evidence)
                if self._evidence else 0.0
            ),
            "inference_rules": len(self._inference_rules),
            "mlx_enabled": self.use_mlx,
        }

    def clear(self) -> None:
        """Clear all evidence and reset state."""
        self._evidence.clear()
        self._evidence_graph.clear()
        logger.info("InferenceEngine state cleared")

    async def cleanup(self) -> None:
        """Clean up resources including thread pool executor."""
        self._shutdown_executor()
        self.clear()
        logger.info("InferenceEngine cleanup completed")

    def export_inference_graph(self) -> Dict[str, Any]:
        """Export evidence graph for visualization."""
        return {
            "nodes": [
                {
                    "id": eid,
                    "fact": ev.fact[:100],  # Truncate for export
                    "confidence": ev.confidence,
                    "source": ev.source,
                }
                for eid, ev in self._evidence.items()
            ],
            "edges": [
                {"source": src, "target": tgt}
                for src, tgts in self._evidence_graph.items()
                for tgt in tgts if src < tgt  # Avoid duplicates
            ],
        }

    # ========================================================================
    # MULTI-HOP REASONING INTEGRATION
    # ========================================================================

    async def multi_hop_inference(
        self,
        start: str,
        end: str,
        max_hops: int = 6,
        min_confidence: float = 0.3,
        max_paths: int = 100,
    ) -> List[MultiHopPath]:
        """
        Perform multi-hop reasoning between entities.

        Finds all inference paths connecting start entity to end entity
        through intermediate entities, with confidence scoring and
        cycle detection.

        OSINT Use Cases:
        - "Is person A connected to criminal organization C through intermediaries?"
        - "What is the chain of shell companies between entity X and Y?"
        - "Find indirect connections between suspects and known actors"

        Args:
            start: Starting entity identifier
            end: Target entity identifier
            max_hops: Maximum number of hops to explore (3-6 recommended)
            min_confidence: Minimum confidence threshold for paths
            max_paths: Maximum number of paths to explore (M1 8GB optimization)

        Returns:
            List of MultiHopPath objects sorted by confidence (highest first)

        Example:
            >>> engine = InferenceEngine()
            >>> # Add evidence...
            >>> paths = await engine.multi_hop_inference(
            ...     start="John Doe",
            ...     end="Criminal Org X",
            ...     max_hops=4,
            ...     min_confidence=0.4
            ... )
            >>> for path in paths[:3]:  # Top 3 paths
            ...     print(path.explain())
        """
        reasoner = MultiHopReasoner(
            inference_engine=self,
            max_hops=max_hops,
            max_paths=max_paths,
            min_confidence=min_confidence,
        )

        return await reasoner.reason(
            start=start,
            end=end,
            min_confidence=min_confidence,
            max_hops=max_hops,
        )

    def multi_hop_reasoning(
        self,
        start: str,
        end: str,
        max_hops: int = 6,
        min_confidence: float = 0.3,
    ) -> Optional[MultiHopPath]:
        """
        Synchronous wrapper for finding the strongest multi-hop path.

        Convenience method for finding the single strongest path between
        entities without async/await syntax.

        Args:
            start: Starting entity identifier
            end: Target entity identifier
            max_hops: Maximum hop depth
            min_confidence: Minimum confidence threshold

        Returns:
            Strongest MultiHopPath or None if no path found
        """
        import asyncio

        reasoner = MultiHopReasoner(
            inference_engine=self,
            max_hops=max_hops,
            min_confidence=min_confidence,
        )

        # Run async method synchronously
        try:
            paths = self._run_coro_sync_safe(
                reasoner.reason(start, end, min_confidence, max_hops)
            )

            if paths:
                return reasoner.rank_paths(paths)[0]
            return None

        except Exception as e:
            logger.error(f"Multi-hop reasoning failed: {e}")
            return None

    def find_indirect_connections(
        self,
        entity: str,
        max_hops: int = 3,
        min_confidence: float = 0.3,
    ) -> Dict[str, List[MultiHopPath]]:
        """
        Find all indirect connections from an entity.

        Discovers entities connected to the start entity through
        multi-hop inference chains.

        Args:
            entity: Starting entity identifier
            max_hops: Maximum hop depth
            min_confidence: Minimum confidence threshold

        Returns:
            Dictionary mapping target entities to their paths
        """
        reasoner = MultiHopReasoner(
            inference_engine=self,
            max_hops=max_hops,
            min_confidence=min_confidence,
        )

        # Get all reachable entities
        reachable = self._get_reachable_entities(entity, max_hops)

        connections = {}
        for target in reachable:
            if target == entity:
                continue

            try:
                paths = self._run_coro_sync_safe(
                    reasoner.reason(entity, target, min_confidence, max_hops)
                )
                if paths:
                    connections[target] = paths
            except Exception as e:
                logger.debug(f"Failed to find path to {target}: {e}")

        return connections

    def _get_reachable_entities(
        self,
        start: str,
        max_hops: int,
    ) -> Set[str]:
        """Get all entities reachable within max_hops from start."""
        reachable = set()
        visited = {start}
        queue = deque([(start, 0)])

        while queue:
            current, depth = queue.popleft()

            if depth >= max_hops:
                continue

            # Find evidence for current entity
            evidence_ids = []
            current_lower = current.lower()
            for evidence_id, evidence in self._evidence.items():
                if current_lower in evidence.fact.lower():
                    evidence_ids.append(evidence_id)

            # Get connected evidence
            for evidence_id in evidence_ids:
                connected = self._evidence_graph.get(evidence_id, set())
                for connected_id in connected:
                    connected_ev = self._evidence.get(connected_id)
                    if connected_ev:
                        # Extract entity from evidence
                        entity = self._extract_entity_from_evidence_sync(connected_ev)
                        if entity and entity not in visited:
                            visited.add(entity)
                            reachable.add(entity)
                            queue.append((entity, depth + 1))

        return reachable

    def _extract_entity_from_evidence_sync(self, evidence: Evidence) -> Optional[str]:
        """Extract primary entity identifier from evidence (sync version)."""
        # Try metadata first
        for key in ["entity", "actor", "subject", "name", "id"]:
            if key in evidence.metadata:
                return str(evidence.metadata[key])

        # Extract from fact (first capitalized word or phrase)
        words = evidence.fact.split()
        for word in words:
            clean_word = word.strip(".,;:!?()[]{}\"'")
            if clean_word and clean_word[0].isupper() and len(clean_word) > 2:
                return clean_word

        return None

    def extended_evidence_chaining(
        self,
        start: str,
        target: str,
        max_depth: int = 5,
    ) -> Optional[List[InferenceStep]]:
        """
        Extended evidence chaining with variable depth.

        Enhanced version of evidence_chaining() that uses the multi-hop
        reasoning system for more robust path finding.

        Args:
            start: Starting statement or evidence ID
            target: Target statement or evidence ID
            max_depth: Maximum chain depth (default 5)

        Returns:
            List of inference steps or None if no chain found
        """
        # First try standard evidence chaining
        result = self.evidence_chaining(start, target, max_depth)
        if result:
            return result

        # Fall back to multi-hop reasoning
        path = self.multi_hop_reasoning(start, target, max_depth)
        if path:
            # Convert MultiHopPath to InferenceStep list
            return self._convert_hop_path_to_inference_steps(path)

        return None

    def _convert_hop_path_to_inference_steps(
        self, path: MultiHopPath
    ) -> List[InferenceStep]:
        """Convert a MultiHopPath to list of InferenceStep objects."""
        steps = []
        for hop in path.hops:
            step = InferenceStep(
                from_statement=hop.from_entity,
                to_statement=hop.to_entity,
                rule=f"multi_hop_{hop.relation}",
                confidence=hop.confidence,
                step_number=hop.step_number,
                evidence_ids=[hop.evidence[:50]],  # Truncated evidence
            )
            steps.append(step)
        return steps

    def calculate_path_confidence(
        self,
        hops: List[HopStep],
        apply_length_penalty: bool = True,
    ) -> float:
        """
        Calculate compounded confidence for a hop sequence.

        Args:
            hops: List of hop steps
            apply_length_penalty: Whether to apply length penalty

        Returns:
            Compounded confidence score
        """
        if not hops:
            return 0.0

        product_confidence = 1.0
        for hop in hops:
            product_confidence *= hop.confidence

        if apply_length_penalty:
            length_penalty = 0.9 ** (len(hops) - 1)
            return product_confidence * length_penalty

        return product_confidence


# =============================================================================
# FACTORY FUNCTION
# =============================================================================

def create_inference_engine(
    max_chain_depth: int = 5,
    min_confidence: float = 0.3,
    use_mlx: bool = True,
) -> InferenceEngine:
    """
    Factory function to create InferenceEngine with standard configuration.

    Args:
        max_chain_depth: Maximum inference chain depth
        min_confidence: Minimum confidence threshold
        use_mlx: Whether to use MLX acceleration

    Returns:
        Configured InferenceEngine instance
    """
    return InferenceEngine(
        max_chain_depth=max_chain_depth,
        min_confidence_threshold=min_confidence,
        use_mlx=use_mlx,
    )


# =============================================================================
# MULTI-HOP REASONER
# =============================================================================

class MultiHopReasoner:
    """Multi-hop reasoning system for n-degree inference chains.

    Implements breadth-first search with depth limits for finding
    inference paths between entities. Optimized for M1 8GB with:
    - Path pruning based on confidence thresholds
    - Early termination when confidence drops too low
    - Memory-efficient BFS with limited queue size
    - Cycle detection to prevent infinite loops

    OSINT Use Cases:
    - "Is person A connected to criminal organization C through intermediaries?"
    - "What is the chain of shell companies between entity X and Y?"
    - "Find all paths from a suspect to known bad actors"

    Attributes:
        inference_engine: Reference to InferenceEngine for evidence access
        max_hops: Maximum number of hops to explore (3-6 recommended)
        max_paths: Maximum number of paths to return (prevents combinatorial explosion)
        min_confidence: Minimum confidence threshold for path inclusion
    """

    def __init__(
        self,
        inference_engine: InferenceEngine,
        max_hops: int = 6,
        max_paths: int = 100,
        min_confidence: float = 0.3,
    ):
        """
        Initialize MultiHopReasoner.

        Args:
            inference_engine: InferenceEngine instance for evidence access
            max_hops: Maximum hop depth (default 6, recommended 3-6)
            max_paths: Maximum paths to explore (M1 8GB optimization)
            min_confidence: Minimum confidence threshold for paths
        """
        self.inference_engine = inference_engine
        self.max_hops = max(3, min(10, max_hops))  # Clamp to reasonable range
        self.max_paths = max_paths
        self.min_confidence = min_confidence

        logger.info(
            f"MultiHopReasoner initialized (max_hops: {max_hops}, "
            f"max_paths: {max_paths}, min_confidence: {min_confidence})"
        )

    async def reason(
        self,
        start: str,
        end: str,
        min_confidence: Optional[float] = None,
        max_hops: Optional[int] = None,
    ) -> List[MultiHopPath]:
        """
        Find all multi-hop paths from start to end entity.

        Uses BFS with depth limiting and confidence-based pruning.
        Returns paths sorted by confidence (highest first).

        Args:
            start: Starting entity identifier
            end: Target entity identifier
            min_confidence: Minimum confidence threshold (overrides default)
            max_hops: Maximum hop depth (overrides default)

        Returns:
            List of MultiHopPath objects sorted by confidence
        """
        min_conf = min_confidence if min_confidence is not None else self.min_confidence
        max_depth = max_hops if max_hops is not None else self.max_hops

        # Use BFS to find all paths
        paths = self._bfs_with_depth(start, end, max_depth, min_conf)

        # Rank paths by confidence
        ranked_paths = self.rank_paths(paths)

        logger.info(
            f"Multi-hop reasoning: '{start}' -> '{end}' found {len(ranked_paths)} paths "
            f"(max_depth: {max_depth}, min_confidence: {min_conf})"
        )

        return ranked_paths

    def _bfs_with_depth(
        self,
        start: str,
        end: str,
        max_depth: int,
        min_confidence: float,
    ) -> List[MultiHopPath]:
        """
        Breadth-first search with depth limiting and confidence pruning.

        Memory-optimized BFS that:
        - Tracks visited nodes per path (not globally)
        - Prunes paths when confidence drops below threshold
        - Limits total paths explored to prevent memory issues
        - Uses early termination when max_paths reached

        Args:
            start: Starting entity
            end: Target entity
            max_depth: Maximum hop depth
            min_confidence: Minimum confidence threshold

        Returns:
            List of MultiHopPath objects
        """
        if start == end:
            # Trivial case: start and end are the same
            return [MultiHopPath(start_entity=start, end_entity=end, hops=[])]

        # Check if entities exist in evidence graph
        start_evidence = self._find_evidence_for_entity(start)
        end_evidence = self._find_evidence_for_entity(end)

        if not start_evidence:
            logger.warning(f"No evidence found for start entity: {start}")
            return []

        if not end_evidence:
            logger.warning(f"No evidence found for end entity: {end}")
            return []

        paths_found = []
        paths_explored = 0

        # Queue items: (current_entity, hops_list, visited_set, current_confidence)
        # Bounded queue to prevent memory issues
        queue = deque([(start, [], {start}, 1.0)], maxlen=self.MAX_BFS_QUEUE)

        # Enforce MAX_BFS_DEPTH
        effective_max_depth = min(max_depth, self.MAX_BFS_DEPTH)

        while queue and paths_explored < self.max_paths:
            current_entity, hops, visited, current_confidence = queue.popleft()

            if len(hops) >= effective_max_depth:
                continue

            # Explore neighbors
            neighbors = self._get_entity_neighbors(current_entity)

            for neighbor_entity, relation, hop_confidence in neighbors:
                # Skip if already visited (cycle detection)
                if neighbor_entity in visited:
                    continue

                # Calculate new confidence
                new_confidence = current_confidence * hop_confidence

                # Prune if confidence too low
                if new_confidence < min_confidence:
                    continue

                # Create hop step
                hop = HopStep(
                    step_number=len(hops) + 1,
                    from_entity=current_entity,
                    to_entity=neighbor_entity,
                    relation=relation,
                    confidence=hop_confidence,
                    evidence=self._get_evidence_for_relation(
                        current_entity, neighbor_entity, relation
                    ),
                )

                new_hops = hops + [hop]

                # Check if we reached the target
                if neighbor_entity == end:
                    path = MultiHopPath(
                        start_entity=start,
                        end_entity=end,
                        hops=new_hops,
                    )
                    paths_found.append(path)
                    paths_explored += 1

                    if paths_explored >= self.max_paths:
                        logger.debug(f"Max paths ({self.max_paths}) reached, terminating search")
                        break

                else:
                    # Continue exploring
                    new_visited = visited | {neighbor_entity}
                    queue.append((neighbor_entity, new_hops, new_visited, new_confidence))

        return paths_found

    def _find_evidence_for_entity(self, entity: str) -> List[str]:
        """Find evidence IDs related to an entity."""
        matching = []
        entity_lower = entity.lower()

        for evidence_id, evidence in self.inference_engine._evidence.items():
            # Check fact content
            if entity_lower in evidence.fact.lower():
                matching.append(evidence_id)
            # Check metadata
            elif any(
                isinstance(v, str) and entity_lower in v.lower()
                for v in evidence.metadata.values()
            ):
                matching.append(evidence_id)

        return matching

    def _get_entity_neighbors(
        self, entity: str
    ) -> List[Tuple[str, str, float]]:
        """
        Get neighboring entities with their relations and confidences.

        Returns list of (neighbor_entity, relation, confidence) tuples.
        """
        neighbors = []
        evidence_ids = self._find_evidence_for_entity(entity)

        for evidence_id in evidence_ids:
            evidence = self.inference_engine._evidence.get(evidence_id)
            if not evidence:
                continue

            # Check graph connections
            connected_ids = self.inference_engine._evidence_graph.get(evidence_id, set())

            for connected_id in connected_ids:
                connected_evidence = self.inference_engine._evidence.get(connected_id)
                if not connected_evidence:
                    continue

                # Extract entity from connected evidence
                neighbor_entity = self._extract_entity_from_evidence(connected_evidence)
                if not neighbor_entity or neighbor_entity == entity:
                    continue

                # Determine relation type
                relation = self._determine_relation_type(evidence, connected_evidence)

                # Calculate confidence
                confidence = min(evidence.confidence, connected_evidence.confidence) * 0.9

                neighbors.append((neighbor_entity, relation, confidence))

        # Remove duplicates, keeping highest confidence
        seen = {}
        for neighbor, relation, confidence in neighbors:
            key = (neighbor, relation)
            if key not in seen or seen[key][2] < confidence:
                seen[key] = (neighbor, relation, confidence)

        return list(seen.values())

    def _extract_entity_from_evidence(self, evidence: Evidence) -> Optional[str]:
        """Extract primary entity identifier from evidence."""
        # Try metadata first
        for key in ["entity", "actor", "subject", "name", "id"]:
            if key in evidence.metadata:
                return str(evidence.metadata[key])

        # Extract from fact (first capitalized word or phrase)
        words = evidence.fact.split()
        for word in words:
            clean_word = word.strip(".,;:!?()[]{}\"'")
            if clean_word and clean_word[0].isupper() and len(clean_word) > 2:
                return clean_word

        # Fallback to evidence ID
        return evidence.evidence_id

    def _determine_relation_type(
        self, evidence_a: Evidence, evidence_b: Evidence
    ) -> str:
        """Determine the type of relationship between two evidence items."""
        # Check for specific relation in metadata
        if "relation" in evidence_a.metadata:
            return str(evidence_a.metadata["relation"])
        if "relation" in evidence_b.metadata:
            return str(evidence_b.metadata["relation"])

        # Infer from evidence content
        fact_a = evidence_a.fact.lower()
        fact_b = evidence_b.fact.lower()

        # Common OSINT relation patterns
        if any(word in fact_a + fact_b for word in ["owns", "owns", "owner"]):
            return "ownership"
        if any(word in fact_a + fact_b for word in ["contact", "communicate", "message"]):
            return "communication"
        if any(word in fact_a + fact_b for word in ["work", "employ", "colleague"]):
            return "employment"
        if any(word in fact_a + fact_b for word in ["family", "relative", "parent", "child"]):
            return "family"
        if any(word in fact_a + fact_b for word in ["location", "located", "address"]):
            return "location"
        if any(word in fact_a + fact_b for word in ["transaction", "payment", "transfer"]):
            return "financial"

        return "association"

    def _get_evidence_for_relation(
        self, entity_a: str, entity_b: str, relation: str
    ) -> str:
        """Get supporting evidence description for a relation."""
        evidence_a = self._find_evidence_for_entity(entity_a)
        evidence_b = self._find_evidence_for_entity(entity_b)

        # Find common evidence or connected evidence
        for ev_id_a in evidence_a:
            for ev_id_b in evidence_b:
                if ev_id_b in self.inference_engine._evidence_graph.get(ev_id_a, set()):
                    ev_a = self.inference_engine._evidence.get(ev_id_a)
                    if ev_a:
                        return ev_a.fact[:200]  # Truncate for brevity

        return f"{relation} relationship inferred from evidence graph"

    def _calculate_compound_confidence(self, hops: List[HopStep]) -> float:
        """
        Calculate compounded confidence across hops.

        Formula: product(hop_confidences) * (0.9 ^ (path_length - 1))

        Args:
            hops: List of hop steps

        Returns:
            Compounded confidence score
        """
        if not hops:
            return 1.0

        product_confidence = 1.0
        for hop in hops:
            product_confidence *= hop.confidence

        # Apply length penalty
        length_penalty = 0.9 ** (len(hops) - 1)

        return product_confidence * length_penalty

    def _detect_cycles(self, path: MultiHopPath) -> bool:
        """
        Detect if a path contains cycles.

        A cycle occurs when an entity appears more than once.

        Args:
            path: MultiHopPath to check

        Returns:
            True if path contains a cycle
        """
        entities = [path.start_entity]
        for hop in path.hops:
            entities.append(hop.to_entity)

        return len(entities) != len(set(entities))

    def rank_paths(self, paths: List[MultiHopPath]) -> List[MultiHopPath]:
        """
        Rank paths by confidence and quality.

        Ranking criteria (in order of priority):
        1. Total confidence (higher is better)
        2. Path length (shorter is better for same confidence)
        3. Non-cyclic paths preferred

        Args:
            paths: List of MultiHopPath objects

        Returns:
            Sorted list of paths (highest confidence first)
        """
        def path_score(path: MultiHopPath) -> Tuple[float, int, bool]:
            # Returns tuple for sorting: (confidence, -length, not_cyclic)
            # Higher confidence, shorter length, non-cyclic = better
            return (
                path.total_confidence,
                -path.path_length,  # Negative so shorter paths rank higher
                not path.is_cyclic,
            )

        return sorted(paths, key=path_score, reverse=True)

    def explain_path(self, path: MultiHopPath) -> str:
        """
        Generate detailed explanation of a reasoning path.

        Args:
            path: MultiHopPath to explain

        Returns:
            Human-readable explanation string
        """
        return path.explain()

    def find_strongest_path(
        self,
        start: str,
        end: str,
        min_confidence: Optional[float] = None,
    ) -> Optional[MultiHopPath]:
        """
        Find the single strongest path between entities.

        Uses A* search with confidence as the optimization metric.

        Args:
            start: Starting entity
            end: Target entity
            min_confidence: Minimum confidence threshold

        Returns:
            Strongest MultiHopPath or None if no path found
        """
        paths = self._bfs_with_depth(
            start,
            end,
            self.max_hops,
            min_confidence if min_confidence is not None else self.min_confidence,
        )

        if not paths:
            return None

        ranked = self.rank_paths(paths)
        return ranked[0] if ranked else None

    def get_path_statistics(self, paths: List[MultiHopPath]) -> Dict[str, Any]:
        """
        Calculate statistics about a set of paths.

        Args:
            paths: List of MultiHopPath objects

        Returns:
            Dictionary with path statistics
        """
        if not paths:
            return {
                "total_paths": 0,
                "avg_confidence": 0.0,
                "avg_path_length": 0.0,
                "cyclic_paths": 0,
                "confidence_range": (0.0, 0.0),
            }

        confidences = [p.total_confidence for p in paths]
        lengths = [p.path_length for p in paths]
        cyclic_count = sum(1 for p in paths if p.is_cyclic)

        return {
            "total_paths": len(paths),
            "avg_confidence": sum(confidences) / len(confidences),
            "avg_path_length": sum(lengths) / len(lengths),
            "cyclic_paths": cyclic_count,
            "confidence_range": (min(confidences), max(confidences)),
            "path_length_range": (min(lengths), max(lengths)),
        }


# =============================================================================
# TOOL REGISTRY INTEGRATION
# =============================================================================

def create_inference_tool(engine: "InferenceEngine", execute_fn=None) -> "Tool":
    """Create a ToolRegistry-compatible Tool from InferenceEngine."""
    from pydantic import BaseModel, Field
    from typing import Optional, List, TYPE_CHECKING, Any, Dict

    if TYPE_CHECKING:
        from ..tool_registry import Tool

    # Define args schema as Pydantic model
    class InferenceArgs(BaseModel):
        mode: str = Field(description="Inference mode: abductive, chain, resolve, indirect")
        query: Optional[str] = Field(default="", description="Query string")
        entities: Optional[List[str]] = Field(default_factory=list, description="List of entities")
        observations: Optional[List[str]] = Field(default_factory=list, description="List of observations")
        hypothesis: Optional[str] = Field(default="", description="Hypothesis for abductive reasoning")
        max_hops: int = Field(default=3, description="Maximum hops for multi-hop inference")

    # Define returns schema as Pydantic model
    class InferenceResult(BaseModel):
        result: Dict[str, Any] = Field(default_factory=dict, description="Inference result")

    # Import here to avoid circular import
    from ..tool_registry import Tool
    return Tool(
        name="infer",
        description="Logical inference: abduction, evidence chaining, multi-hop reasoning, entity resolution",
        args_schema=InferenceArgs,
        returns_schema=InferenceResult,
        memory_mb=50,
        is_network=False,
        handler=execute_fn,
    )