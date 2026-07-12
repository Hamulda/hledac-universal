"""
Communication Layer - Universal Orchestrator Integration

Unified communication system integrating:
- Agent Messaging (pub/sub channels)
- Agent Model Bridge (LLM routing)
- Emergent Communication (semantic routing, vocabulary)
- A2A Protocol Adapter (Google A2A compatibility)

Provides unified API for agent-to-agent and agent-to-model communication.
"""
import asyncio
import hashlib
import heapq
import logging
import time
from collections import deque
from dataclasses import dataclass, field
import msgspec
from typing import Any
from collections.abc import Callable, Coroutine
from hledac.universal.project_types import CommunicationConfig, MessagePriority
logger = logging.getLogger(__name__)
import itertools
from hledac.universal.utils.async_helpers import safe_create_task, safe_gather_ok
_counter = itertools.count()

@dataclass(slots=True)
class _Subscriber:
    """Single subscriber entry with bounded inbox queue."""
    agent_id: str
    queue: asyncio.Queue[dict[str, Any]]
    channels: set[str]

class InMemoryMessageBroker:
    """
    asyncio.Queue-per-subscriber in-process pub/sub broker.

    Replaces dict[topic, list[callback]] synchronous pattern with:
    - One asyncio.Queue per subscriber (bounded, maxsize=64)
    - Topic → routing to all matching subscriber queues
    - Async consumer: `await queue.get()` per subscriber

    M1 8GB: ~256 bytes per idle queue, ~2KB when active. Bounded at 256 subscribers.

    Not cross-process — all in one asyncio event loop. For cross-process, use NATS
    (py-nats>0.15) or Redis Streams if already deployed.
    """
    MAX_SUBSCRIBERS: int = 256
    MAX_QUEUE_SIZE: int = 64
    __slots__ = tuple(('_lock', '_subscribers', '_topic_cache'))

    def __init__(self) -> None:
        self._subscribers: dict[str, _Subscriber] = {}
        self._lock = asyncio.Lock()
        self._topic_cache: dict[str, set[str]] = {}

    async def subscribe(self, agent_id: str, channels: str | list[str]) -> bool:
        """
        Subscribe agent to one or more channels.

        Args:
            agent_id: Unique agent identifier
            channels: Single channel name or list; "*" means all channels

        Returns:
            True if subscribed, False if limit reached
        """
        if agent_id in self._subscribers:
            sub = self._subscribers[agent_id]
            async with self._lock:
                if isinstance(channels, str):
                    sub.channels.add(channels)
                else:
                    sub.channels.update(channels)
                self._topic_cache.clear()
            return True
        if len(self._subscribers) >= self.MAX_SUBSCRIBERS:
            logger.warning(f'[BROKER] subscriber limit reached, rejecting {agent_id}')
            return False
        async with self._lock:
            if isinstance(channels, str):
                ch = {channels}
            else:
                ch = set(channels)
            self._subscribers[agent_id] = _Subscriber(agent_id=agent_id, queue=asyncio.Queue(maxsize=self.MAX_QUEUE_SIZE), channels=ch)
            self._topic_cache.clear()
        logger.debug(f'[BROKER] {agent_id} subscribed to {ch}')
        return True

    async def unsubscribe(self, agent_id: str, channels: str | list[str] | None=None) -> None:
        """
        Unsubscribe agent from channels, or fully remove if channels is None.

        Args:
            agent_id: Agent to unsubscribe
            channels: Specific channels, list of channels, or None (full removal)
        """
        if agent_id not in self._subscribers:
            return
        async with self._lock:
            if channels is None:
                del self._subscribers[agent_id]
                self._topic_cache.clear()
                logger.debug(f'[BROKER] {agent_id} fully unsubscribed')
                return
            sub = self._subscribers[agent_id]
            if isinstance(channels, str):
                sub.channels.discard(channels)
            else:
                for ch in channels:
                    sub.channels.discard(ch)
            if not sub.channels:
                del self._subscribers[agent_id]
                self._topic_cache.clear()
                logger.debug(f'[BROKER] {agent_id} fully unsubscribed (no channels left)')
            else:
                self._topic_cache.clear()

    async def publish(self, channel: str, message: dict[str, Any], sender_id: str | None=None) -> int:
        """
        Publish message to all subscribers of the given channel.

        Args:
            channel: Channel name (topic routing key)
            message: Message payload
            sender_id: Optional sender ID (excluded from delivery)

        Returns:
            Number of subscribers that received the message
        """
        if not self._subscribers:
            return 0
        envelope = {'channel': channel, 'sender': sender_id, 'message': message, 'published_at': time.time()}
        delivered = 0
        async with self._lock:
            if not self._topic_cache:
                for sid, sub in self._subscribers.items():
                    for ch in sub.channels:
                        if ch not in self._topic_cache:
                            self._topic_cache[ch] = set()
                        self._topic_cache[ch].add(sid)
            recipient_ids: set[str] = set()
            if '*' in self._topic_cache:
                recipient_ids.update(self._topic_cache['*'])
            if channel in self._topic_cache:
                recipient_ids.update(self._topic_cache[channel])
            if sender_id:
                recipient_ids.discard(sender_id)
        for sid in recipient_ids:
            sub = self._subscribers.get(sid)
            if sub is None:
                continue
            try:
                sub.queue.put_nowait(envelope)
                delivered += 1
            except asyncio.QueueFull:
                logger.warning(f'[BROKER] {sid} queue full, message dropped on {channel}')
        return delivered

    async def get_message(self, agent_id: str, timeout: float=5.0) -> dict[str, Any] | None:
        """
        Get next message for subscriber (async queue get).

        Args:
            agent_id: Subscriber ID
            timeout: Seconds to wait (default 5.0)

        Returns:
            Message envelope or None on timeout
        """
        sub = self._subscribers.get(agent_id)
        if sub is None:
            return None
        try:
            async with asyncio.timeout(timeout):
                return await sub.queue.get()
        except TimeoutError:
            return None

    def get_queue_size(self, agent_id: str) -> int:
        """Return current inbox queue size for monitoring."""
        sub = self._subscribers.get(agent_id)
        return sub.queue.qsize() if sub else 0

    def get_stats(self) -> dict[str, Any]:
        """Broker statistics for monitoring."""
        return {'subscriber_count': len(self._subscribers), 'topic_count': len(self._topic_cache), 'max_subscribers': self.MAX_SUBSCRIBERS, 'queue_capacity': self.MAX_QUEUE_SIZE, 'subscribers': {sid: {'channels': list(sub.channels), 'queue_size': sub.queue.qsize(), 'queue_capacity': sub.queue.maxsize} for sid, sub in self._subscribers.items()}}

class _InMemoryMessaging:
    """
    Synchronous wrapper over InMemoryMessageBroker implementing AgentMessagingSystem-like API.

    Used as fallback when communication/agent_messaging.py is not available.
    Provides broadcast() and send_message() that delegate to the broker.
    """
    __slots__ = tuple(('_broker',))

    def __init__(self, broker: InMemoryMessageBroker) -> None:
        self._broker = broker

    async def initialize(self) -> None:
        """No-op initialization."""
        pass

    async def send_message(self, sender_id: str, recipient_id: str, content: str) -> dict[str, Any]:
        """Send direct message to recipient via inbox queue."""
        delivered = await self._broker.publish(channel=f'inbox:{recipient_id}', message={'type': 'direct', 'content': content}, sender_id=sender_id)
        return {'success': delivered > 0, 'delivered': delivered}

    async def broadcast(self, sender_id: str, content: str, channel: str | None=None) -> dict[str, Any]:
        """Broadcast to channel (or default channel)."""
        ch = channel or 'default'
        delivered = await self._broker.publish(channel=ch, message={'type': 'broadcast', 'content': content}, sender_id=sender_id)
        return {'success': delivered > 0, 'delivered': delivered, 'channel': ch}

    def register_agent(self, agent_id: str, metadata: dict[str, Any]) -> None:
        """Register agent (subscribe to default channel)."""
        safe_create_task(self._broker.subscribe(agent_id, ['default', f'inbox:{agent_id}']), name='comm_layer:register_agent')

    def unregister_agent(self, agent_id: str) -> None:
        """Unregister agent."""
        safe_create_task(self._broker.unsubscribe(agent_id), name='comm_layer:unregister_agent')

@dataclass(order=True, slots=True)
class _BatchItem:
    """Batch item with priority for queue ordering."""
    priority: float = field(default=0.0)
    counter: int = field(default=0, compare=True)
    timestamp: float = field(default=0.0, compare=False)
    query: dict = field(default_factory=dict, compare=False)
    future: asyncio.Future = field(default=None, compare=False)
    wait_since: float = field(default_factory=time.time, compare=False)

@dataclass(slots=True)
class ModelQuery:
    """Model query with metadata."""
    query_id: str
    prompt: str
    complexity: str
    priority: int
    use_cache: bool
    timestamp: float

@dataclass(frozen=True, slots=True)
class CacheEntry:
    """Cache entry for model responses."""
    key: str
    response: str
    created_at: float
    access_count: int = 0
    last_access: float = field(default_factory=time.time)
HAS_COMM_MODULES = False
try:
    from ...communication.agent_messaging import AgentMessagingSystem
    from ...communication.agent_model_bridge import AgentModelBridge
    HAS_COMM_MODULES = True
except ImportError:
    pass
try:
    from ...emergent_communication.a2a_protocol_adapter import A2AAgentCard, A2AProtocolAdapter
    from ...emergent_communication.agent_relevance_scorer import AgentRelevanceScorer
    from ...emergent_communication.communication_optimizer import CommunicationOptimizer, OptimizationMode
    from ...emergent_communication.semantic_message_router import IntentType, RoutingDecision, SemanticMessageRouter
    from ...emergent_communication.topic_channel_organizer import TopicChannelOrganizer
    from ...emergent_communication.vocabulary_manager import EncodingResult, VocabularyManager
    HAS_EMERGENT = True
except ImportError:
    HAS_EMERGENT = False

@dataclass(frozen=True, slots=True)
class MessageContext:
    """Message context for routing."""
    sender_id: str
    priority: MessagePriority
    channel: str | None = None
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
    __slots__ = tuple(('_a2a_adapter', '_batch_heap', '_batch_heap_lock', '_batch_queue', '_batch_shutdown', '_batch_size', '_batch_task', '_batch_threshold', '_batch_timeout', '_batch_timeout_new', '_broker', '_cache', '_cache_size', '_cache_ttl', '_ctx', '_initialized', '_latency_history', '_max_batch', '_messaging', '_metrics', '_model_bridge', '_optimizer', '_query_queue', '_relevance_scorer', '_semantic_router', '_topic_organizer', '_vocabulary', 'config', 'layer_name'))

    def __init__(self, config: CommunicationConfig):
        self.config = config
        self._messaging: Any | None = None
        self._model_bridge: Any | None = None
        self._semantic_router: Any | None = None
        self._vocabulary: Any | None = None
        self._topic_organizer: Any | None = None
        self._relevance_scorer: Any | None = None
        self._optimizer: Any | None = None
        self._a2a_adapter: Any | None = None
        self._broker = InMemoryMessageBroker()
        self._cache: dict[str, CacheEntry] = {}
        self._cache_size = config.model_cache_size if hasattr(config, 'model_cache_size') else 100
        self._cache_ttl = config.model_cache_ttl if hasattr(config, 'model_cache_ttl') else 300
        self._query_queue: deque = deque()
        self._batch_size = config.model_batch_size if hasattr(config, 'model_batch_size') else 5
        self._batch_timeout = config.model_batch_timeout if hasattr(config, 'model_batch_timeout') else 0.05
        self._batch_queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._batch_threshold = 10
        self._batch_timeout_new = 0.02
        self._batch_task: asyncio.Task | None = None
        self._batch_shutdown = asyncio.Event()
        self._batch_heap: list[_BatchItem] = []
        self._batch_heap_lock = asyncio.Lock()
        self._max_batch = 4
        self._metrics = {'total_queries': 0, 'cache_hits': 0, 'cache_misses': 0, 'batched_queries': 0, 'avg_latency': 0.0}
        self._latency_history: deque = deque(maxlen=100)
        self._initialized = False
        self.layer_name: str = 'communication'
        self._ctx: Any | None = None

    async def mount(self, ctx: Any) -> None:
        """Layer Protocol: mount."""
        self._ctx = ctx
        await self.initialize()
        ctx.set('communication', self)

    async def unmount(self, ctx: Any) -> None:
        """Layer Protocol: unmount."""
        await self.shutdown()

    async def on_event(self, ctx: Any, event: Any) -> Any:
        """Layer Protocol: handle communication events."""
        return event

    async def initialize(self) -> bool:
        """Initialize all communication subsystems."""
        try:
            if HAS_EMERGENT:
                from ...emergent_communication.communication_optimizer import CommunicationOptimizer
                self._optimizer = CommunicationOptimizer(mode=OptimizationMode.BALANCED, enable_batching=self.config.enable_batching, enable_compression=self.config.enable_compression)
                await self._optimizer.start()
                logger.info('CommunicationOptimizer initialized')
            if HAS_COMM_MODULES and self.config.enable_agent_messaging:
                from ...communication.agent_messaging import AgentMessagingSystem
                self._messaging = AgentMessagingSystem()
                await self._messaging.initialize()
                logger.info('AgentMessagingSystem initialized')
            elif self.config.enable_agent_messaging:
                self._messaging = _InMemoryMessaging(self._broker)
                await self._messaging.initialize()
                logger.info('InMemoryMessaging initialized (fallback)')
            if HAS_COMM_MODULES and self.config.enable_model_bridge:
                from ...communication.agent_model_bridge import AgentModelBridge
                self._model_bridge = AgentModelBridge()
                await self._model_bridge.start()
                logger.info('AgentModelBridge initialized')
            if HAS_EMERGENT and self.config.enable_emergent_comm:
                from ...emergent_communication.agent_relevance_scorer import AgentRelevanceScorer
                from ...emergent_communication.semantic_message_router import SemanticMessageRouter
                from ...emergent_communication.topic_channel_organizer import TopicChannelOrganizer
                from ...emergent_communication.vocabulary_manager import VocabularyManager
                self._semantic_router = SemanticMessageRouter()
                self._vocabulary = VocabularyManager()
                self._topic_organizer = TopicChannelOrganizer()
                self._relevance_scorer = AgentRelevanceScorer()
                logger.info('Emergent communication components initialized')
            if HAS_EMERGENT and self.config.enable_a2a_protocol:
                from ...emergent_communication.a2a_protocol_adapter import A2AProtocolAdapter
                self._a2a_adapter = A2AProtocolAdapter()
                logger.info('A2AProtocolAdapter initialized')
            self._initialized = True
            return True
        except Exception as e:
            logger.error(f'Communication layer initialization failed: {e}')
            return False

    async def shutdown(self) -> None:
        """Shutdown all communication subsystems."""
        self._batch_shutdown.set()
        if self._batch_task and (not self._batch_task.done()):
            self._batch_task.cancel()
            try:
                await self._batch_task
            except asyncio.CancelledError:
                pass
        if self._optimizer:
            await self._optimizer.stop()
        if self._model_bridge:
            await self._model_bridge.stop()
        self._initialized = False
        logger.info('Communication layer shutdown complete')

    def register_agent(self, agent_id: str, capabilities: set[str], specializations: set[str] | None=None) -> None:
        """Register an agent with the communication system."""
        if self._semantic_router:
            self._semantic_router.register_agent(agent_id, capabilities, specializations)
        if self._relevance_scorer:
            cap_dict = dict.fromkeys(capabilities, 1.0)
            self._relevance_scorer.register_agent(agent_id, cap_dict, specializations or set())
        if self._messaging:
            self._messaging.register_agent(agent_id, {'capabilities': list(capabilities)})

    def unregister_agent(self, agent_id: str) -> None:
        """Unregister an agent."""
        if self._semantic_router:
            self._semantic_router.unregister_agent(agent_id)
        if self._relevance_scorer:
            self._relevance_scorer.unregister_agent(agent_id)
        if self._messaging:
            self._messaging.unregister_agent(agent_id)

    async def send_message(self, message: str, sender_id: str, recipient_id: str | None=None, context: MessageContext | None=None) -> dict[str, Any]:
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
            return {'success': False, 'error': 'Not initialized'}
        if recipient_id and self._messaging:
            return await self._messaging.send_message(sender_id=sender_id, recipient_id=recipient_id, content=message)
        if self._semantic_router:
            routing = await self._semantic_router.route_message(message=message, sender_id=sender_id)
            return {'success': True, 'method': 'semantic_routing', 'recipients': routing.recipients, 'confidence': routing.confidence}
        return {'success': False, 'error': 'No routing method available'}

    async def broadcast_message(self, message: str, sender_id: str, channel: str | None=None) -> dict[str, Any]:
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
            return await self._messaging.broadcast(sender_id=sender_id, content=message, channel=channel)
        return {'success': False, 'error': 'Messaging not available'}

    async def query_model(self, prompt: str, complexity: str='medium', priority: int=3, use_cache: bool=True, max_tokens: int=500, temperature: float=0.7, voi_score: float=0.5) -> dict[str, Any]:
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
        query_id = hashlib.sha256(f'{prompt}:{time.time()}'.encode()).hexdigest()[:16]
        try:
            if use_cache:
                cached = self._check_cache(prompt, complexity)
                if cached:
                    self._metrics['cache_hits'] += 1
                    return {'success': True, 'response': cached, 'cached': True, 'query_id': query_id, 'latency': time.time() - start_time}
                self._metrics['cache_misses'] += 1
            if self.config.enable_batching and priority > 2:
                return await self._queue_query(query_id, prompt, complexity, priority, max_tokens, temperature, voi_score)
            result = await self._execute_query(prompt, complexity, max_tokens, temperature)
            if use_cache and result.get('success'):
                self._add_to_cache(prompt, complexity, result['response'])
            latency = time.time() - start_time
            self._update_metrics(latency)
            result.update({'query_id': query_id, 'latency': latency, 'cached': False})
            return result
        except Exception as e:
            logger.error(f'Model query failed: {e}')
            return {'success': False, 'error': str(e), 'query_id': query_id}

    async def _execute_query(self, prompt: str, complexity: str, max_tokens: int, temperature: float) -> dict[str, Any]:
        """Execute model query with smart routing."""
        if complexity in ('complex', 'very_complex'):
            model = 'hermes-3-4b'
        else:
            model = 'hermes-3-1.7b'
        if self._model_bridge:
            return await self._model_bridge.send_to_model(agent_id='communication_layer', content=prompt, task_type=complexity, max_tokens=max_tokens, temperature=temperature)
        return {'success': False, 'error': 'model_bridge_unavailable', 'model': model, 'response': None}

    def _update_max_batch(self) -> None:
        """Update max_batch based on available RAM."""
        try:
            import psutil
            free_gb = psutil.virtual_memory().available / 1024 ** 3
            self._max_batch = 8 if free_gb > 4.0 else 4
        except Exception:
            pass

    async def _queue_query(self, query_id: str, prompt: str, complexity: str, priority: int, max_tokens: int, temperature: float, voi_score: float=0.5) -> dict[str, Any] | None:
        """Add query to batch queue with priority based on voi_score."""
        future = asyncio.Future()
        query = ModelQuery(query_id=query_id, prompt=prompt, complexity=complexity, priority=priority, use_cache=True, timestamp=time.time())
        trace_id = getattr(self, '_current_trace_id', None)
        item = _BatchItem(priority=-voi_score, counter=next(_counter), timestamp=time.time(), query={'query': query, 'max_tokens': max_tokens, 'temperature': temperature, 'trace_id': trace_id}, future=future)
        async with self._batch_heap_lock:
            import heapq
            heapq.heappush(self._batch_heap, item)
        if not self._batch_task or self._batch_task.done():
            self._batch_task = safe_create_task(self._batch_processor(), name='communication_layer:batch_processor')
        try:
            async with asyncio.timeout(10.0):
                await future
        except TimeoutError:
            return {'success': False, 'error': 'batch_timeout', 'response': None}

    async def _batch_processor(self) -> None:
        """Process batched queries using priority heap and dynamic max_batch (Sprint 41).
        Sprint 42: Added aging for anti-starvation."""
        AGING_RATE = 0.01
        MAX_PRIORITY_CAP = -0.01
        while True:
            try:
                async with asyncio.timeout(0):
                    await self._batch_shutdown.wait()
                break
            except TimeoutError:
                pass
            try:
                self._update_max_batch()
                now = time.time()
                async with self._batch_heap_lock:
                    if self._batch_heap:
                        aged_items = []
                        for item in self._batch_heap:
                            wait_seconds = now - item.wait_since
                            if wait_seconds > 0.2:
                                boosted = min(item.priority + AGING_RATE * wait_seconds, MAX_PRIORITY_CAP)
                                aged_items.append(_BatchItem(priority=boosted, counter=item.counter, timestamp=item.timestamp, wait_since=item.wait_since, query=item.query, future=item.future))
                            else:
                                aged_items.append(item)
                        self._batch_heap = aged_items
                        heapq.heapify(self._batch_heap)
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
                results = await self._process_batch_parallel([item.query for item in batch])
                for item, res in zip(batch, results, strict=False):
                    if isinstance(res, Exception):
                        res = {'success': False, 'error': str(res), 'response': None}
                    if not item.future.done():
                        item.future.set_result(res)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f'[BATCH] Processor error: {e}')
                await asyncio.sleep(0.1)

    async def _process_batch_parallel(self, queries: list[dict]) -> list[dict]:
        """Run batch of prompts with fallback per item (Sprint 41)."""

        async def run_one(q):
            try:
                query = q.get('query')
                return await self._execute_query(query.prompt, query.complexity, q.get('max_tokens', 1024), q.get('temperature', 0.7))
            except Exception as e:
                return {'success': False, 'error': str(e), 'response': None}
        return await safe_gather_ok(*[run_one(q) for q in queries], label='communication_layer:596')

    async def _process_batch(self, batch: list[dict]) -> None:
        """Process a batch of queries (Sprint 26)."""
        if not batch:
            return
        self._metrics['batched_queries'] += len(batch)
        for item in batch:
            query = item.get('query')
            future = item.get('future')
            if not query or not future:
                continue
            try:
                result = await self._execute_query(query.prompt, query.complexity, item.get('max_tokens', 1024), item.get('temperature', 0.7))
                if query.use_cache and result.get('success'):
                    self._add_to_cache(query.prompt, query.complexity, result['response'])
                if not future.done():
                    future.set_result(result)
            except Exception as e:
                if not future.done():
                    future.set_result({'success': False, 'error': str(e)})

    def _check_cache(self, prompt: str, complexity: str) -> str | None:
        """Check if response is cached."""
        cache_key = hashlib.sha256(f'{prompt}:{complexity}'.encode()).hexdigest()[:32]
        if cache_key in self._cache:
            entry = self._cache[cache_key]
            if time.time() - entry.created_at < self._cache_ttl:
                entry.access_count += 1
                entry.last_access = time.time()
                return entry.response
            else:
                del self._cache[cache_key]
        return None

    def _add_to_cache(self, prompt: str, complexity: str, response: str) -> None:
        """Add response to cache."""
        cache_key = hashlib.sha256(f'{prompt}:{complexity}'.encode()).hexdigest()[:32]
        if len(self._cache) >= self._cache_size:
            oldest = min(self._cache.values(), key=lambda e: e.last_access)
            del self._cache[oldest.key]
        self._cache[cache_key] = CacheEntry(key=cache_key, response=response, created_at=time.time())

    def _update_metrics(self, latency: float) -> None:
        """Update performance metrics."""
        self._metrics['total_queries'] += 1
        self._latency_history.append(latency)
        if self._latency_history:
            self._metrics['avg_latency'] = sum(self._latency_history) / len(self._latency_history)

    def clear_cache(self) -> int:
        """Clear model response cache.

        Returns:
            Number of entries cleared
        """
        count = len(self._cache)
        self._cache.clear()
        return count

    async def route_semantically(self, message: str, sender_id: str) -> RoutingDecision | None:
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

    def encode_message(self, message: str) -> dict[str, Any]:
        """
        Encode message using vocabulary compression.

        Args:
            message: Original message

        Returns:
            Encoding result
        """
        if not self._vocabulary:
            return {'original': message, 'encoded': message, 'compression': 1.0}
        result = self._vocabulary.encode_message(message)
        return {'original': message, 'encoded': result.encoded_message, 'compression': result.compression_ratio, 'codes_used': result.codes_used}

    def decode_message(self, encoded: str) -> str:
        """Decode vocabulary-compressed message."""
        if not self._vocabulary:
            return encoded
        return self._vocabulary.decode_message(encoded)

    def subscribe_to_channel(self, agent_id: str, channel: str) -> bool:
        """Subscribe agent to a topic channel."""
        if not self._topic_organizer:
            return False
        return self._topic_organizer.subscribe_agent(agent_id, channel)

    def unsubscribe_from_channel(self, agent_id: str, channel: str) -> bool:
        """Unsubscribe agent from a topic channel."""
        if not self._topic_organizer:
            return False
        return self._topic_organizer.unsubscribe_agent(agent_id, channel)

    def set_agent_card(self, card: dict[str, Any]) -> None:
        """Set A2A agent card."""
        if self._a2a_adapter:
            from ...emergent_communication.a2a_protocol_adapter import A2AAgentCard
            agent_card = A2AAgentCard(**card)
            self._a2a_adapter.set_agent_card(agent_card)

    def create_a2a_task(self, message: dict[str, Any], session_id: str | None=None) -> str | None:
        """Create A2A protocol task."""
        if not self._a2a_adapter:
            return None
        task = self._a2a_adapter.create_task(message, session_id)
        return task.id

    def get_a2a_task(self, task_id: str) -> dict[str, Any] | None:
        """Get A2A task status."""
        if not self._a2a_adapter:
            return None
        return self._a2a_adapter.get_task(task_id)

    def get_stats(self) -> dict[str, Any]:
        """Get communication layer statistics."""
        stats = {'initialized': self._initialized, 'subsystems': {'messaging': self._messaging is not None, 'model_bridge': self._model_bridge is not None, 'semantic_router': self._semantic_router is not None, 'vocabulary': self._vocabulary is not None, 'topic_organizer': self._topic_organizer is not None, 'a2a_adapter': self._a2a_adapter is not None}, 'model_metrics': {'total_queries': self._metrics['total_queries'], 'cache_hits': self._metrics['cache_hits'], 'cache_misses': self._metrics['cache_misses'], 'cache_hit_rate': self._metrics['cache_hits'] / max(self._metrics['cache_hits'] + self._metrics['cache_misses'], 1), 'cache_size': len(self._cache), 'batched_queries': self._metrics['batched_queries'], 'avg_latency_ms': self._metrics['avg_latency'] * 1000}}
        if self._optimizer:
            stats['optimizer'] = self._optimizer.get_metrics()
        if self._a2a_adapter:
            stats['a2a'] = self._a2a_adapter.get_stats()
        stats['broker'] = self._broker.get_stats()
        return stats

    async def health_check(self) -> tuple[bool, list[str]]:
        """Check communication layer health."""
        issues = []
        if not self._initialized:
            issues.append('Not initialized')
        return (not issues, issues)

async def create_communication_layer(config: CommunicationConfig) -> CommunicationLayer:
    """Create and initialize communication layer."""
    layer = CommunicationLayer(config)
    await layer.initialize()
    return layer