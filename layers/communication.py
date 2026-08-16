"""
Communication Layer - Messaging and Content Processing
=====================================================

Consolidated from:
- communication_layer.py: CommunicationLayer + InMemoryMessageBroker
- content_layer.py: ContentCleaner + SimpleHTMLCleaner

Features:
- Agent-to-agent messaging with pub/sub
- LLM routing with caching and batching
- Semantic message routing
- HTML cleaning (Markdown/JSON/Text)
- A2A protocol support

M1 8GB: Uses __slots__ for memory efficiency.
"""
from __future__ import annotations

import asyncio
import hashlib
import heapq
import itertools
import logging
import re
import time
from collections import deque
from enum import Enum
from typing import Any

import msgspec
from compat.msgspec_gc_compat import Struct
from hledac.universal.project_types import CommunicationConfig, MessagePriority
from hledac.universal.utils.asyncx import parallel_ok, safe_create_task

logger = logging.getLogger(__name__)

__all__ = [
    'CommunicationLayer',
    'ContentCleaner',
    'SimpleHTMLCleaner',
    'OutputFormat',
    'CleaningResult',
    'InMemoryMessageBroker',
]

_counter = itertools.count()


# ─── Content Cleaning ────────────────────────────────────────────────────────


class OutputFormat(Enum):
    """Supported output formats."""
    MARKDOWN = 'markdown'
    JSON = 'json'
    TEXT = 'text'


class CleaningResult(Struct, gc=False):
    """HTML cleaning result."""
    success: bool
    content: str
    format: OutputFormat
    metadata: dict[str, Any] | None = None
    error: str | None = None


class SimpleHTMLCleaner:
    """
    HTML cleaner with tiered extraction using nh3 and selectolax.

    Tier-1 TEXT: html_text_fast → nh3.clean → selectolax
    Tier-2 MARKDOWN/JSON: selectolax → regex fallback
    """

    __slots__ = ('_parser_class',)

    def __init__(self) -> None:
        self._parser_class: type | None = None
        self._init_selectolax()

    def _init_selectolax(self) -> None:
        """Initialize selectolax lazily."""
        try:
            from selectolax.parser import HTMLParser
            self._parser_class = HTMLParser
        except ImportError:
            logger.warning('selectolax not available')

    def clean(self, html: str, output_format: OutputFormat = OutputFormat.MARKDOWN) -> CleaningResult:
        """Clean HTML using tiered extraction."""
        try:
            from hledac.universal.utils.html_text_fast import html_to_text_fast
            HTML_TEXT_FAST_AVAILABLE = True
        except ImportError:
            HTML_TEXT_FAST_AVAILABLE = False
            html_to_text_fast = None

        try:
            import nh3 as _nh3
            NH3_AVAILABLE = True
        except ImportError:
            NH3_AVAILABLE = False
            _nh3 = None

        if output_format == OutputFormat.TEXT:
            # Tier-1: html_text_fast
            if HTML_TEXT_FAST_AVAILABLE:
                try:
                    content = html_to_text_fast(html)
                    return CleaningResult(
                        success=True, content=content,
                        format=output_format,
                        metadata={'method': 'html_text_fast'},
                    )
                except Exception as e:
                    logger.warning('html_text_fast failed, falling back to nh3: %s', e)

            # Tier-1.5: nh3 Rust sanitizer
            if NH3_AVAILABLE:
                try:
                    content = _nh3.clean(html, tags=set())
                    content = re.sub(r'\s+', ' ', content).strip()
                    if content:
                        return CleaningResult(
                            success=True, content=content,
                            format=output_format,
                            metadata={'method': 'nh3'},
                        )
                except Exception as e:
                    logger.warning('nh3.clean failed, falling back to selectolax: %s', e)

        # Tier-2: selectolax for MARKDOWN/JSON or fallback for TEXT
        if self._parser_class is None:
            return CleaningResult(
                success=False, content='',
                format=output_format,
                error='selectolax not available',
            )
        try:
            tree = self._parser_class(html)
            if output_format == OutputFormat.TEXT:
                body = tree.css_first('body') or tree
                content = body.text_content(separator=' ', default='')
                content = re.sub(r'\s+', ' ', content).strip()
            elif output_format == OutputFormat.MARKDOWN:
                content = self._to_markdown(tree)
            else:
                content = self._to_json(tree)
            return CleaningResult(
                success=True, content=content,
                format=output_format,
                metadata={'method': 'selectolax'},
            )
        except Exception as e:
            logger.error(f'selectolax cleaning failed: {e}')
            return CleaningResult(
                success=False, content='',
                format=output_format,
                error=str(e),
            )

    def _to_markdown(self, tree: Any) -> str:
        """Convert HTML to Markdown format."""
        lines: list[str] = []
        for tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'ul', 'ol', 'li', 'a', 'strong', 'em'):
            for node in tree.css(tag):
                text = node.text(strip=True)
                if not text:
                    continue
                if tag.startswith('h'):
                    level = int(tag[-1])
                    lines.append(f"{'#' * level} {text}")
                elif tag == 'p':
                    lines.append(text)
                elif tag == 'li':
                    lines.append(f'- {text}')
                elif tag == 'a':
                    href = node.attributes.get('href', '')
                    if href:
                        lines.append(f'[{text}]({href})')
                    else:
                        lines.append(text)
                elif tag == 'strong':
                    lines.append(f'**{text}**')
                elif tag == 'em':
                    lines.append(f'*{text}*')
        return '\n\n'.join(lines)

    def _to_json(self, tree: Any) -> str:
        """Convert HTML to JSON format."""
        from hledac.universal.utils.msgspec_json import dumps_str as _msgspec_dumps_str

        data: dict[str, Any] = {
            'title': '',
            'headings': [],
            'paragraphs': [],
            'links': [],
            'lists': [],
        }
        title_node = tree.css_first('h1')
        if title_node is not None:
            data['title'] = title_node.text(strip=True)
        for h in tree.css('h1,h2,h3,h4,h5,h6'):
            level = int(h.tag[1])
            data['headings'].append({'level': level, 'text': h.text(strip=True)})
        for p in tree.css('p'):
            text = p.text(strip=True)
            if text and len(text) > 20:
                data['paragraphs'].append(text)
        for a in tree.css('a[href]'):
            data['links'].append({
                'text': a.text(strip=True),
                'url': a.attributes['href'],
            })
        for ul in tree.css('ul,ol'):
            items = [li.text(strip=True) for li in ul.css('li') if li.text(strip=True)]
            if items:
                data['lists'].append({'type': ul.tag, 'items': items})
        return _msgspec_dumps_str(data, ensure_ascii=False, indent=2)


class ContentCleaner:
    """
    HTML to Markdown/JSON converter.

    M1 8GB: Uses __slots__ for memory efficiency.
    """
    __slots__ = ('_default_format', '_simple_cleaner')

    def __init__(
        self,
        default_format: OutputFormat = OutputFormat.MARKDOWN,
    ) -> None:
        self._default_format = default_format
        self._simple_cleaner = SimpleHTMLCleaner()

    def clean_html(
        self,
        raw_html: str,
        output_format: OutputFormat | None = None,
    ) -> CleaningResult:
        """Clean HTML to specified format."""
        if output_format is None:
            output_format = self._default_format
        return self._simple_cleaner.clean(raw_html, output_format)

    def clean_html_batch(
        self,
        html_list: list[str],
        output_format: OutputFormat | None = None,
    ) -> list[CleaningResult]:
        """Clean multiple HTML documents."""
        return [self.clean_html(html, output_format) for html in html_list]

    def get_status(self) -> dict[str, Any]:
        """Get cleaner status."""
        return {'default_format': self._default_format.value}


# ─── Message Broker ──────────────────────────────────────────────────────────


class _Subscriber(Struct, gc=False):
    """Single subscriber entry with bounded inbox queue."""
    agent_id: str
    queue: asyncio.Queue[dict[str, Any]]
    channels: set[str]


class InMemoryMessageBroker:
    """
    asyncio.Queue-per-subscriber in-process pub/sub broker.

    M1 8GB: ~256 bytes per idle queue, ~2KB when active. Bounded at 256 subscribers.
    """
    MAX_SUBSCRIBERS: int = 256
    MAX_QUEUE_SIZE: int = 64

    __slots__ = ('_lock', '_subscribers', '_topic_cache')

    def __init__(self) -> None:
        self._subscribers: dict[str, _Subscriber] = {}
        self._lock = asyncio.Lock()
        self._topic_cache: dict[str, set[str]] = {}

    async def subscribe(
        self,
        agent_id: str,
        channels: str | list[str],
    ) -> bool:
        """Subscribe agent to channels."""
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
            ch = {channels} if isinstance(channels, str) else set(channels)
            self._subscribers[agent_id] = _Subscriber(
                agent_id=agent_id,
                queue=asyncio.Queue(maxsize=self.MAX_QUEUE_SIZE),
                channels=ch,
            )
            self._topic_cache.clear()
        logger.debug(f'[BROKER] {agent_id} subscribed to {ch}')
        return True

    async def unsubscribe(
        self,
        agent_id: str,
        channels: str | list[str] | None = None,
    ) -> None:
        """Unsubscribe agent from channels."""
        if agent_id not in self._subscribers:
            return
        async with self._lock:
            if channels is None:
                del self._subscribers[agent_id]
                self._topic_cache.clear()
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

    async def publish(
        self,
        channel: str,
        message: dict[str, Any],
        sender_id: str | None = None,
    ) -> int:
        """Publish message to all subscribers."""
        if not self._subscribers:
            return 0
        envelope = {
            'channel': channel,
            'sender': sender_id,
            'message': message,
            'published_at': time.time(),
        }
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
                logger.warning(f'[BROKER] {sid} queue full, message dropped')
        return delivered

    async def get_message(
        self,
        agent_id: str,
        timeout: float = 5.0,
    ) -> dict[str, Any] | None:
        """Get next message for subscriber."""
        sub = self._subscribers.get(agent_id)
        if sub is None:
            return None
        try:
            async with asyncio.timeout(timeout):
                return await sub.queue.get()
        except TimeoutError:
            return None

    def get_stats(self) -> dict[str, Any]:
        """Broker statistics."""
        return {
            'subscriber_count': len(self._subscribers),
            'topic_count': len(self._topic_cache),
            'max_subscribers': self.MAX_SUBSCRIBERS,
        }


# ─── Communication Layer ─────────────────────────────────────────────────────


class _BatchItem(Struct, gc=False):
    """Batch item with priority for queue ordering."""
    priority: float = 0.0
    counter: int = 0
    timestamp: float = 0.0
    query: dict = {}
    future: asyncio.Future | None = None
    wait_since: float = 0.0

    def __lt__(self, other: _BatchItem) -> bool:
        if not isinstance(other, _BatchItem):
            return NotImplemented
        return (self.priority, self.counter) < (other.priority, other.counter)


class CacheEntry(Struct, frozen=True, gc=False):
    """Cache entry for model responses."""
    key: str
    response: str
    created_at: float
    access_count: int = 0
    last_access: float = 0.0


class CommunicationLayer:
    """
    Unified Communication Layer.

    Features:
    - Agent-to-agent messaging
    - LLM routing with caching and batching
    - Content cleaning
    - A2A protocol support

    M1 8GB: Uses __slots__ for memory efficiency.
    """
    layer_name: str = 'communication'
    _priority: int = 50  # Medium priority

    __slots__ = tuple((
        '_batch_heap',
        '_batch_heap_lock',
        '_batch_shutdown',
        '_batch_task',
        '_cache',
        '_cache_size',
        '_cache_ttl',
        '_content_cleaner',
        '_ctx',
        '_initialized',
        '_max_batch',
        '_metrics',
        '_messaging',
        '_model_bridge',
        '_query_queue',
        '_latency_history',
        'config',
    ))

    def __init__(self, config: CommunicationConfig | None = None) -> None:
        self.config = config or CommunicationConfig()
        self._messaging = InMemoryMessageBroker()
        self._model_bridge = None
        self._cache: dict[str, CacheEntry] = {}
        self._cache_size = getattr(self.config, 'model_cache_size', 100)
        self._cache_ttl = getattr(self.config, 'model_cache_ttl', 300)
        self._query_queue: deque = deque()
        self._batch_heap: list[_BatchItem] = []
        self._batch_heap_lock = asyncio.Lock()
        self._batch_shutdown = asyncio.Event()
        self._batch_task: asyncio.Task | None = None
        self._max_batch = 4
        self._metrics = {
            'total_queries': 0,
            'cache_hits': 0,
            'cache_misses': 0,
            'batched_queries': 0,
            'avg_latency': 0.0,
        }
        self._latency_history: deque = deque(maxlen=100)
        self._initialized = False
        self._ctx: Any | None = None
        self._content_cleaner = ContentCleaner()

    async def mount(self, ctx: Any) -> None:
        """Mount the communication layer."""
        self._ctx = ctx
        await self.initialize()
        ctx.set('communication', self)
        ctx.set('content_cleaner', self._content_cleaner)

    async def unmount(self, ctx: Any) -> None:
        """Unmount the communication layer."""
        await self.shutdown()

    async def process(self, ctx: Any, data: Any) -> Any:
        """Process data through communication layer."""
        return data

    async def rollback(self, ctx: Any, error: Exception) -> None:
        """Rollback on error."""
        logger.warning(f'CommunicationLayer rollback: {error}')

    async def initialize(self) -> bool:
        """Initialize communication subsystems."""
        try:
            self._initialized = True
            logger.info('✅ CommunicationLayer initialized')
            return True
        except Exception as e:
            logger.error(f'Communication layer initialization failed: {e}')
            return False

    async def shutdown(self) -> None:
        """Shutdown communication subsystems."""
        self._batch_shutdown.set()
        if self._batch_task and not self._batch_task.done():
            self._batch_task.cancel()
            try:
                await self._batch_task
            except asyncio.CancelledError:
                pass
        self._initialized = False
        logger.info('Communication layer shutdown complete')

    async def send_message(
        self,
        message: str,
        sender_id: str,
        recipient_id: str | None = None,
    ) -> dict[str, Any]:
        """Send a message."""
        if recipient_id:
            delivered = await self._messaging.publish(
                channel=f'inbox:{recipient_id}',
                message={'type': 'direct', 'content': message},
                sender_id=sender_id,
            )
            return {'success': delivered > 0, 'delivered': delivered}
        return {'success': False, 'error': 'No routing method available'}

    async def broadcast_message(
        self,
        message: str,
        sender_id: str,
        channel: str | None = None,
    ) -> dict[str, Any]:
        """Broadcast message to channel."""
        ch = channel or 'default'
        delivered = await self._messaging.publish(
            channel=ch,
            message={'type': 'broadcast', 'content': message},
            sender_id=sender_id,
        )
        return {'success': delivered > 0, 'delivered': delivered, 'channel': ch}

    def clean_html(
        self,
        raw_html: str,
        output_format: OutputFormat | None = None,
    ) -> CleaningResult:
        """Clean HTML content."""
        return self._content_cleaner.clean_html(raw_html, output_format)

    def clean_html_batch(
        self,
        html_list: list[str],
        output_format: OutputFormat | None = None,
    ) -> list[CleaningResult]:
        """Clean multiple HTML documents."""
        return self._content_cleaner.clean_html_batch(html_list, output_format)

    async def query_model(
        self,
        prompt: str,
        complexity: str = 'medium',
        priority: int = 3,
        use_cache: bool = True,
        max_tokens: int = 500,
        temperature: float = 0.7,
        voi_score: float = 0.5,
    ) -> dict[str, Any]:
        """Query LLM with caching and smart routing."""
        start_time = time.time()
        query_id = hashlib.sha256(f'{prompt}:{time.time()}'.encode()).hexdigest()[:16]

        try:
            if use_cache:
                cached = self._check_cache(prompt, complexity)
                if cached:
                    self._metrics['cache_hits'] += 1
                    return {
                        'success': True,
                        'response': cached,
                        'cached': True,
                        'query_id': query_id,
                        'latency': time.time() - start_time,
                    }
                self._metrics['cache_misses'] += 1

            result = await self._execute_query(prompt, complexity, max_tokens, temperature)

            if use_cache and result.get('success'):
                self._add_to_cache(prompt, complexity, result['response'])

            latency = time.time() - start_time
            self._update_metrics(latency)
            result.update({
                'query_id': query_id,
                'latency': latency,
                'cached': False,
            })
            return result

        except Exception as e:
            logger.error(f'Model query failed: {e}')
            return {'success': False, 'error': str(e), 'query_id': query_id}

    async def _execute_query(
        self,
        prompt: str,
        complexity: str,
        max_tokens: int,
        temperature: float,
    ) -> dict[str, Any]:
        """Execute model query."""
        if self._model_bridge:
            return await self._model_bridge.send_to_model(
                agent_id='communication_layer',
                content=prompt,
                task_type=complexity,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        return {'success': False, 'error': 'model_bridge_unavailable', 'response': None}

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
        from operator import attrgetter

        cache_key = hashlib.sha256(f'{prompt}:{complexity}'.encode()).hexdigest()[:32]
        if len(self._cache) >= self._cache_size:
            oldest = min(self._cache.values(), key=attrgetter('last_access'))
            del self._cache[oldest.key]
        self._cache[cache_key] = CacheEntry(
            key=cache_key,
            response=response,
            created_at=time.time(),
        )

    def _update_metrics(self, latency: float) -> None:
        """Update performance metrics."""
        self._metrics['total_queries'] += 1
        self._latency_history.append(latency)
        if self._latency_history:
            self._metrics['avg_latency'] = sum(self._latency_history) / len(self._latency_history)

    def clear_cache(self) -> int:
        """Clear model response cache."""
        count = len(self._cache)
        self._cache.clear()
        return count

    def get_stats(self) -> dict[str, Any]:
        """Get communication layer statistics."""
        return {
            'initialized': self._initialized,
            'model_metrics': {
                'total_queries': self._metrics['total_queries'],
                'cache_hits': self._metrics['cache_hits'],
                'cache_misses': self._metrics['cache_misses'],
                'cache_hit_rate': self._metrics['cache_hits'] / max(
                    self._metrics['cache_hits'] + self._metrics['cache_misses'], 1
                ),
                'cache_size': len(self._cache),
                'avg_latency_ms': self._metrics['avg_latency'] * 1000,
            },
            'broker': self._messaging.get_stats(),
        }


__all__ = ['CommunicationLayer', 'ContentCleaner', 'InMemoryMessageBroker', 'OutputFormat', 'CleaningResult']
