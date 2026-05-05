#!/usr/bin/env python3
"""
Communication Layer - Universal Orchestrator Integration

Unified communication system integrating:
- Agent Messaging (pub/sub channels)
- Agent Model Bridge (LLM routing)
- Emergent Communication (semantic routing, vocabulary)
- A2A Protocol Adapter (Google A2A compatibility)

Provides unified API for agent-to-agent and agent-to-model communication.
"""

from __future__ import annotations

import asyncio
import heapq
import hashlib
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from hledac.universal.project_types import CommunicationConfig, MessagePriority

logger = logging.getLogger(__name__)


# Sprint 41: Priority batch item for dynamic batching
# Sprint 42: Added wait_since for aging (anti-starvation)
# Sprint 47: Added counter for tie-breaking when VoI is equal
import itertools

_counter = itertools.count()

@dataclass(order=True)
class _BatchItem:
    """Batch item with priority for queue ordering."""
    priority: float = field(default=0.0)  # lower = higher priority (heapq is min-heap)
    counter: int = field(default=0, compare=True)  # Sprint 47: tie-breaker for equal VoI
    timestamp: float = field(default=0.0, compare=False)
    query: dict = field(default_factory=dict, compare=False)
    future: asyncio.Future = field(default=None, compare=False)
    wait_since: float = field(default_factory=time.time, compare=False)  # Sprint 42


@dataclass
class ModelQuery:
    """Model query with metadata."""
    query_id: str
    prompt: str
    complexity: str
    priority: int
    use_cache: bool
    timestamp: float


@dataclass
class CacheEntry:
    """Cache entry for model responses."""
    key: str
    response: str
    created_at: float
    access_count: int = 0
    last_access: float = field(default_factory=time.time)

# Lazy imports
HAS_COMM_MODULES = False
try:
    from ...communication.agent_messaging import AgentMessagingSystem
    from ...communication.agent_model_bridge import AgentModelBridge
    HAS_COMM_MODULES = True
except ImportError:
    pass

try:
    from ...emergent_communication.semantic_message_router import (
        SemanticMessageRouter, IntentType, RoutingDecision
    )
    from ...emergent_communication.vocabulary_manager import (
        VocabularyManager, EncodingResult
    )
    from ...emergent_communication.topic_channel_organizer import (
        TopicChannelOrganizer
    )
    from ...emergent_communication.agent_relevance_scorer import (
        AgentRelevanceScorer
    )
    from ...emergent_communication.communication_optimizer import (
        CommunicationOptimizer, OptimizationMode
    )
    from ...emergent_communication.a2a_protocol_adapter import (
        A2AProtocolAdapter, A2AAgentCard
    )
    HAS_EMERGENT = True
except ImportError:
    HAS_EMERGENT = False


@dataclass
class MessageContext:
    """Message context for routing."""
    sender_id: str
    priority: MessagePriority
    channel: Optional[str] = None
    requires_response: bool = False
    timeout: float = 30.0


class CommunicationLayer:
    """
    Unified Communication Layer.
    
    Integrates all communication subsystems:
    - Agent-to-agent messaging
    - Agent-to-model routing with caching and batching
    - Semantic message routing
    - Vocabulary compression
    - Topic channels
    - A2A protocol support
    
    Features from AgentModelBridge:
    - Smart model routing (complexity-based)
    - Shared context cache
    - Request batching
    - Priority queuing
    - Performance metrics
    """
    
    def __init__(self, config: CommunicationConfig):
        self.config = config
        
        # Subsystems
        self._messaging: Optional[Any] = None
        self._model_bridge: Optional[Any] = None
        self._semantic_router: Optional[Any] = None
        self._vocabulary: Optional[Any] = None
        self._topic_organizer: Optional[Any] = None
        self._relevance_scorer: Optional[Any] = None
        self._optimizer: Optional[Any] = None
        self._a2a_adapter: Optional[Any] = None
        
        # Model bridge features (from agent_model_bridge.py)
        self._cache: Dict[str, CacheEntry] = {}
        self._cache_size = config.model_cache_size if hasattr(config, 'model_cache_size') else 100
        self._cache_ttl = config.model_cache_ttl if hasattr(config, 'model_cache_ttl') else 300
        
        # Batching (legacy)
        self._query_queue: deque = deque()
        self._batch_size = config.model_batch_size if hasattr(config, 'model_batch_size') else 5
        self._batch_timeout = config.model_batch_timeout if hasattr(config, 'model_batch_timeout') else 0.05

        # Sprint 26: Adaptive batching with asyncio.Queue (F207N-D: bounded for M1 8GB)
        # maxsize=256: control-path queue for model queries, bounded to prevent OOM
        self._batch_queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._batch_threshold = 10  # process immediately if queue >= this
        self._batch_timeout_new = 0.02  # 20ms max wait
        self._batch_task: Optional[asyncio.Task] = None

        # Sprint 41: Dynamic batching with priority queue
        self._batch_heap: List[_BatchItem] = []
        self._batch_heap_lock = asyncio.Lock()
        self._max_batch = 4  # default, updated dynamically
        
        # Metrics
        self._metrics = {
            "total_queries": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "batched_queries": 0,
            "avg_latency": 0.0
        }
        self._latency_history: deque = deque(maxlen=100)
        
        self._initialized = False
        
    async def initialize(self) -> bool:
        """Initialize all communication subsystems."""
        try:
            # Initialize optimizer first (used by others)
            if HAS_EMERGENT:
                from ...emergent_communication.communication_optimizer import CommunicationOptimizer
                self._optimizer = CommunicationOptimizer(
                    mode=OptimizationMode.BALANCED,
                    enable_batching=self.config.enable_batching,
                    enable_compression=self.config.enable_compression
                )
                await self._optimizer.start()
                logger.info("CommunicationOptimizer initialized")
            
            # Initialize agent messaging
            if HAS_COMM_MODULES and self.config.enable_agent_messaging:
                from ...communication.agent_messaging import AgentMessagingSystem
                self._messaging = AgentMessagingSystem()
                await self._messaging.initialize()
                logger.info("AgentMessagingSystem initialized")
            
            # Initialize model bridge
            if HAS_COMM_MODULES and self.config.enable_model_bridge:
                from ...communication.agent_model_bridge import AgentModelBridge
                self._model_bridge = AgentModelBridge()
                await self._model_bridge.start()
                logger.info("AgentModelBridge initialized")
            
            # Initialize emergent communication
            if HAS_EMERGENT and self.config.enable_emergent_comm:
                from ...emergent_communication.semantic_message_router import SemanticMessageRouter
                from ...emergent_communication.vocabulary_manager import VocabularyManager
                from ...emergent_communication.topic_channel_organizer import TopicChannelOrganizer
                from ...emergent_communication.agent_relevance_scorer import AgentRelevanceScorer
                
                self._semantic_router = SemanticMessageRouter()
                self._vocabulary = VocabularyManager()
                self._topic_organizer = TopicChannelOrganizer()
                self._relevance_scorer = AgentRelevanceScorer()
                logger.info("Emergent communication components initialized")
            
            # Initialize A2A adapter
            if HAS_EMERGENT and self.config.enable_a2a_protocol:
                from ...emergent_communication.a2a_protocol_adapter import A2AProtocolAdapter
                self._a2a_adapter = A2AProtocolAdapter()
                logger.info("A2AProtocolAdapter initialized")
            
            self._initialized = True
            return True
            
        except Exception as e:
            logger.error(f"Communication layer initialization failed: {e}")
            return False
    
    async def shutdown(self) -> None:
        """Shutdown all communication subsystems."""
        if self._optimizer:
            await self._optimizer.stop()
        
        if self._model_bridge:
            await self._model_bridge.stop()
        
        self._initialized = False
        logger.info("Communication layer shutdown complete")
    
    # ============== Agent Registration ==============
    
    def register_agent(
        self,
        agent_id: str,
        capabilities: Set[str],
        specializations: Optional[Set[str]] = None
    ) -> None:
        """Register an agent with the communication system."""
        if self._semantic_router:
            self._semantic_router.register_agent(agent_id, capabilities, specializations)
        
        if self._relevance_scorer:
            cap_dict = {cap: 1.0 for cap in capabilities}
            self._relevance_scorer.register_agent(agent_id, cap_dict, specializations or set())
        
        if self._messaging:
            self._messaging.register_agent(agent_id, {"capabilities": list(capabilities)})
    
    def unregister_agent(self, agent_id: str) -> None:
        """Unregister an agent."""
        if self._semantic_router:
            self._semantic_router.unregister_agent(agent_id)
        
        if self._relevance_scorer:
            self._relevance_scorer.unregister_agent(agent_id)
        
        if self._messaging:
            self._messaging.unregister_agent(agent_id)
    
    # ============== Message Sending ==============
    
    async def send_message(
        self,
        message: str,
        sender_id: str,
        recipient_id: Optional[str] = None,
        context: Optional[MessageContext] = None
    ) -> Dict[str, Any]:
        """
        Send a message using the best available method.
        
        Args:
            message: Message content
            sender_id: Sender agent ID
            recipient_id: Optional specific recipient
            context: Optional message context
        
        Returns:
            Delivery result
        """
        if not self._initialized:
            return {"success": False, "error": "Not initialized"}
        
        # If specific recipient provided, send directly
        if recipient_id and self._messaging:
            return await self._messaging.send_message(
                sender_id=sender_id,
                recipient_id=recipient_id,
                content=message
            )
        
        # Otherwise use semantic routing
        if self._semantic_router:
            routing = await self._semantic_router.route_message(
                message=message,
                sender_id=sender_id
            )
            
            return {
                "success": True,
                "method": "semantic_routing",
                "recipients": routing.recipients,
                "confidence": routing.confidence
            }
        
        return {"success": False, "error": "No routing method available"}
    
    async def broadcast_message(
        self,
        message: str,
        sender_id: str,
        channel: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Broadcast message to multiple agents.
        
        Args:
            message: Message content
            sender_id: Sender agent ID
            channel: Optional channel name
        
        Returns:
            Broadcast result
        """
        if self._messaging:
            return await self._messaging.broadcast(
                sender_id=sender_id,
                content=message,
                channel=channel
            )
        
        return {"success": False, "error": "Messaging not available"}
    
    # ============== Model Communication ==============
    
    async def query_model(
        self,
        prompt: str,
        complexity: str = "medium",
        priority: int = 3,
        use_cache: bool = True,
        max_tokens: int = 500,
        temperature: float = 0.7,
        voi_score: float = 0.5
    ) -> Dict[str, Any]:
        """
        Query LLM with caching and smart routing.

        Args:
            prompt: Query prompt
            complexity: Complexity level (simple/medium/complex/very_complex)
            priority: Priority level (1-5, 1 is highest)
            use_cache: Whether to use response cache
            voi_score: Value of Information score (higher = process first)
            max_tokens: Maximum tokens in response
            temperature: Response temperature
        
        Returns:
            Model response with metadata
        """
        start_time = time.time()
        query_id = hashlib.sha256(f"{prompt}:{time.time()}".encode()).hexdigest()[:16]
        
        try:
            # Check cache first
            if use_cache:
                cached = self._check_cache(prompt, complexity)
                if cached:
                    self._metrics["cache_hits"] += 1
                    return {
                        "success": True,
                        "response": cached,
                        "cached": True,
                        "query_id": query_id,
                        "latency": time.time() - start_time
                    }
                self._metrics["cache_misses"] += 1
            
            # If batching enabled, add to queue
            if self.config.enable_batching and priority > 2:  # Don't batch high priority
                return await self._queue_query(
                    query_id, prompt, complexity, priority, max_tokens, temperature, voi_score
                )
            
            # Direct query
            result = await self._execute_query(
                prompt, complexity, max_tokens, temperature
            )
            
            # Update cache
            if use_cache and result.get("success"):
                self._add_to_cache(prompt, complexity, result["response"])
            
            # Update metrics
            latency = time.time() - start_time
            self._update_metrics(latency)
            
            result.update({
                "query_id": query_id,
                "latency": latency,
                "cached": False
            })
            
            return result
            
        except Exception as e:
            logger.error(f"Model query failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "query_id": query_id
            }
    
    async def _execute_query(
        self,
        prompt: str,
        complexity: str,
        max_tokens: int,
        temperature: float
    ) -> Dict[str, Any]:
        """Execute model query with smart routing."""
        # Determine model based on complexity
        if complexity in ("complex", "very_complex"):
            model = "hermes-3-4b"  # Use larger model
        else:
            model = "hermes-3-1.7b"  # Use faster model
        
        if self._model_bridge:
            # Use existing bridge
            return await self._model_bridge.send_to_model(
                agent_id="communication_layer",
                content=prompt,
                task_type=complexity,
                max_tokens=max_tokens,
                temperature=temperature
            )
        
        # Fallback - return failure (not fake success)
        return {
            "success": False,
            "error": "model_bridge_unavailable",
            "model": model,
            "response": None
        }
    
    # Sprint 41: Helper to update max_batch based on available RAM
    def _update_max_batch(self) -> None:
        """Update max_batch based on available RAM."""
        try:
            import psutil
            free_gb = psutil.virtual_memory().available / (1024**3)
            self._max_batch = 8 if free_gb > 4.0 else 4
        except Exception:
            # fail-safe: keep current value
            pass

    async def _queue_query(
        self,
        query_id: str,
        prompt: str,
        complexity: str,
        priority: int,
        max_tokens: int,
        temperature: float,
        voi_score: float = 0.5
    ) -> Dict[str, Any]:
        """Add query to batch queue with priority based on voi_score."""
        future = asyncio.Future()

        query = ModelQuery(
            query_id=query_id,
            prompt=prompt,
            complexity=complexity,
            priority=priority,
            use_cache=True,
            timestamp=time.time()
        )

        # Sprint 41: Use priority heap instead of queue
        # Sprint 43: Propagate trace_id for distributed tracing
        # Sprint 47: Add counter for tie-breaking when VoI is equal
        trace_id = getattr(self, '_current_trace_id', None)
        item = _BatchItem(
            priority=-voi_score,  # heapq is min-heap, so negative = higher score first
            counter=next(_counter),  # Sprint 47: stable tie-breaking
            timestamp=time.time(),
            query={
                'query': query,
                'max_tokens': max_tokens,
                'temperature': temperature,
                'trace_id': trace_id,
            },
            future=future
        )

        async with self._batch_heap_lock:
            import heapq
            heapq.heappush(self._batch_heap, item)

        # Start batch processor if not running
        if not self._batch_task or self._batch_task.done():
            self._batch_task = asyncio.create_task(self._batch_processor(), name="communication_layer:batch_processor")

        try:
            return await asyncio.wait_for(future, timeout=10.0)
        except asyncio.TimeoutError:
            return {"success": False, "error": "batch_timeout", "response": None}
    
    # Sprint 41: Dynamic batching with priority queue
    async def _batch_processor(self) -> None:
        """Process batched queries using priority heap and dynamic max_batch (Sprint 41).
        Sprint 42: Added aging for anti-starvation."""
        AGING_RATE = 0.01  # priority boost per second of waiting
        MAX_PRIORITY_CAP = -0.01  # ensure low-VoI never surpasses high-VoI with VoI=1.0

        while True:
            try:
                # Update max_batch based on free RAM
                self._update_max_batch()
                now = time.time()

                # Sprint 42: Aging - boost priority based on wait time
                async with self._batch_heap_lock:
                    if self._batch_heap:
                        aged_items = []
                        for item in self._batch_heap:
                            wait_seconds = now - item.wait_since
                            if wait_seconds > 0.2:  # >200ms
                                # Boost: original priority + AGING_RATE * wait_seconds
                                # but cap so it never exceeds MAX_PRIORITY_CAP
                                boosted = min(item.priority + AGING_RATE * wait_seconds,
                                              MAX_PRIORITY_CAP)
                                # Create new item with updated priority (keep original counter)
                                aged_items.append(_BatchItem(
                                    priority=boosted,
                                    counter=item.counter,  # Sprint 47: preserve tie-breaker
                                    timestamp=item.timestamp,
                                    wait_since=item.wait_since,  # keep original wait start
                                    query=item.query,
                                    future=item.future
                                ))
                            else:
                                aged_items.append(item)
                        # Replace heap with aged items and re-heapify
                        self._batch_heap = aged_items
                        heapq.heapify(self._batch_heap)

                # Wait for first item – without holding lock during sleep
                is_empty = False
                batch = []
                async with self._batch_heap_lock:
                    if not self._batch_heap:
                        is_empty = True
                    else:
                        batch = []
                        for _ in range(min(self._max_batch, len(self._batch_heap))):
                            item = heapq.heappop(self._batch_heap)
                            batch.append(item)

                if is_empty:
                    await asyncio.sleep(0.01)
                    continue

                if not batch:
                    continue

                # Process batch – one failure doesn't kill others
                results = await self._process_batch_parallel([item.query for item in batch])
                for item, res in zip(batch, results):
                    # Handle Exception objects from gather with return_exceptions=True
                    if isinstance(res, Exception):
                        res = {"success": False, "error": str(res), "response": None}
                    if not item.future.done():
                        item.future.set_result(res)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"[BATCH] Processor error: {e}")
                await asyncio.sleep(0.1)

    async def _process_batch_parallel(self, queries: List[dict]) -> List[dict]:
        """Run batch of prompts with fallback per item (Sprint 41)."""
        async def run_one(q):
            try:
                query = q.get('query')
                return await self._execute_query(
                    query.prompt, query.complexity,
                    q.get('max_tokens', 1024), q.get('temperature', 0.7)
                )
            except Exception as e:
                return {"success": False, "error": str(e), "response": None}

        return await asyncio.gather(*[run_one(q) for q in queries], return_exceptions=True)

    async def _process_batch(self, batch: List[dict]) -> None:
        """Process a batch of queries (Sprint 26)."""
        if not batch:
            return

        self._metrics["batched_queries"] += len(batch)

        for item in batch:
            query = item.get("query")
            future = item.get("future")
            if not query or not future:
                continue

            try:
                result = await self._execute_query(
                    query.prompt,
                    query.complexity,
                    item.get("max_tokens", 1024),
                    item.get("temperature", 0.7)
                )

                # Cache result
                if query.use_cache and result.get("success"):
                    self._add_to_cache(
                        query.prompt, query.complexity, result["response"]
                    )

                if not future.done():
                    future.set_result(result)

            except Exception as e:
                if not future.done():
                    future.set_result({"success": False, "error": str(e)})

    def _check_cache(self, prompt: str, complexity: str) -> Optional[str]:
        """Check if response is cached."""
        cache_key = hashlib.sha256(f"{prompt}:{complexity}".encode()).hexdigest()[:32]
        
        if cache_key in self._cache:
            entry = self._cache[cache_key]
            
            # Check TTL
            if time.time() - entry.created_at < self._cache_ttl:
                entry.access_count += 1
                entry.last_access = time.time()
                return entry.response
            else:
                # Expired
                del self._cache[cache_key]
        
        return None
    
    def _add_to_cache(self, prompt: str, complexity: str, response: str) -> None:
        """Add response to cache."""
        cache_key = hashlib.sha256(f"{prompt}:{complexity}".encode()).hexdigest()[:32]
        
        # Evict oldest if cache full
        if len(self._cache) >= self._cache_size:
            oldest = min(self._cache.values(), key=lambda e: e.last_access)
            del self._cache[oldest.key]
        
        self._cache[cache_key] = CacheEntry(
            key=cache_key,
            response=response,
            created_at=time.time()
        )
    
    def _update_metrics(self, latency: float) -> None:
        """Update performance metrics."""
        self._metrics["total_queries"] += 1
        self._latency_history.append(latency)
        
        # Update average
        if self._latency_history:
            self._metrics["avg_latency"] = sum(self._latency_history) / len(self._latency_history)
    
    def clear_cache(self) -> int:
        """Clear model response cache.
        
        Returns:
            Number of entries cleared
        """
        count = len(self._cache)
        self._cache.clear()
        return count
    
    # ============== Semantic Routing ==============
    
    async def route_semantically(
        self,
        message: str,
        sender_id: str
    ) -> Optional[RoutingDecision]:
        """
        Route message using semantic analysis.
        
        Args:
            message: Message content
            sender_id: Sender agent ID
        
        Returns:
            Routing decision
        """
        if not self._semantic_router:
            return None
        
        return await self._semantic_router.route_message(message, sender_id)
    
    # ============== Vocabulary Management ==============
    
    def encode_message(self, message: str) -> Dict[str, Any]:
        """
        Encode message using vocabulary compression.
        
        Args:
            message: Original message
        
        Returns:
            Encoding result
        """
        if not self._vocabulary:
            return {"original": message, "encoded": message, "compression": 1.0}
        
        result = self._vocabulary.encode_message(message)
        return {
            "original": message,
            "encoded": result.encoded_message,
            "compression": result.compression_ratio,
            "codes_used": result.codes_used
        }
    
    def decode_message(self, encoded: str) -> str:
        """Decode vocabulary-compressed message."""
        if not self._vocabulary:
            return encoded
        
        return self._vocabulary.decode_message(encoded)
    
    # ============== Topic Channels ==============
    
    def subscribe_to_channel(
        self,
        agent_id: str,
        channel: str
    ) -> bool:
        """Subscribe agent to a topic channel."""
        if not self._topic_organizer:
            return False
        
        return self._topic_organizer.subscribe_agent(agent_id, channel)
    
    def unsubscribe_from_channel(
        self,
        agent_id: str,
        channel: str
    ) -> bool:
        """Unsubscribe agent from a topic channel."""
        if not self._topic_organizer:
            return False
        
        return self._topic_organizer.unsubscribe_agent(agent_id, channel)
    
    # ============== A2A Protocol ==============
    
    def set_agent_card(self, card: Dict[str, Any]) -> None:
        """Set A2A agent card."""
        if self._a2a_adapter:
            from ...emergent_communication.a2a_protocol_adapter import A2AAgentCard
            agent_card = A2AAgentCard(**card)
            self._a2a_adapter.set_agent_card(agent_card)
    
    def create_a2a_task(
        self,
        message: Dict[str, Any],
        session_id: Optional[str] = None
    ) -> Optional[str]:
        """Create A2A protocol task."""
        if not self._a2a_adapter:
            return None
        
        task = self._a2a_adapter.create_task(message, session_id)
        return task.id
    
    def get_a2a_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get A2A task status."""
        if not self._a2a_adapter:
            return None
        
        return self._a2a_adapter.get_task(task_id)
    
    # ============== Utility Methods ==============
    
    def get_stats(self) -> Dict[str, Any]:
        """Get communication layer statistics."""
        stats = {
            "initialized": self._initialized,
            "subsystems": {
                "messaging": self._messaging is not None,
                "model_bridge": self._model_bridge is not None,
                "semantic_router": self._semantic_router is not None,
                "vocabulary": self._vocabulary is not None,
                "topic_organizer": self._topic_organizer is not None,
                "a2a_adapter": self._a2a_adapter is not None
            },
            "model_metrics": {
                "total_queries": self._metrics["total_queries"],
                "cache_hits": self._metrics["cache_hits"],
                "cache_misses": self._metrics["cache_misses"],
                "cache_hit_rate": (
                    self._metrics["cache_hits"] / 
                    max(self._metrics["cache_hits"] + self._metrics["cache_misses"], 1)
                ),
                "cache_size": len(self._cache),
                "batched_queries": self._metrics["batched_queries"],
                "avg_latency_ms": self._metrics["avg_latency"] * 1000
            }
        }
        
        if self._optimizer:
            stats["optimizer"] = self._optimizer.get_metrics()
        
        if self._a2a_adapter:
            stats["a2a"] = self._a2a_adapter.get_stats()
        
        return stats
    
    async def health_check(self) -> Tuple[bool, List[str]]:
        """Check communication layer health."""
        issues = []
        
        if not self._initialized:
            issues.append("Not initialized")
        
        return len(issues) == 0, issues


# Factory function
async def create_communication_layer(
    config: CommunicationConfig
) -> CommunicationLayer:
    """Create and initialize communication layer."""
    layer = CommunicationLayer(config)
    await layer.initialize()
    return layer
