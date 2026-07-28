"""
stream_handler.py — Stream Token Handler
=========================================

PEP 698: Extracted from DeepHermes3Engine streaming methods.
Handles token-by-token streaming with cancellation support.

Extracted from:
- generate_stream()
- _stream_tokens()
- _sync_stream_prep()

M1 8GB: Streaming provides better perceived latency for long generations.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, AsyncIterator

if TYPE_CHECKING:
    from typing import Callable

logger = logging.getLogger(__name__)


@dataclass
class StreamConfig:
    """Configuration for streaming."""
    queue_size: int = 1024  # Token queue size
    cancel_timeout: float = 2.0  # Seconds to wait for cancellation


@dataclass
class StreamStats:
    """Streaming statistics."""
    tokens_yielded: int = 0
    tokens_cancelled: int = 0
    stream_errors: int = 0


class StreamHandler:
    """
    Handles token streaming with cancellation support.

    Extracted from DeepHermes3Engine to:
    1. Isolate streaming logic for testing
    2. Provide reusable streaming interface
    3. Eliminate scattered _stream_tokens implementations

    M1 8GB: Uses queue-based streaming to prevent blocking.
    """

    def __init__(self, config: StreamConfig | None = None) -> None:
        self._config = config or StreamConfig()
        self._queue: asyncio.Queue[str] | None = None
        self._cancelled = False
        self._stats = StreamStats()
        self._cancel_event: asyncio.Event | None = None

    def get_stats(self) -> StreamStats:
        """Get streaming statistics."""
        return self._stats

    async def stream_tokens(
        self,
        generate_fn: Callable[..., AsyncIterator[str]],
        *args: Any,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """
        Stream tokens from a generator function.

        Args:
            generate_fn: Async generator function that yields tokens
            *args, **kwargs: Arguments to pass to generate_fn

        Yields:
            Token strings
        """
        self._queue = asyncio.Queue(maxsize=self._config.queue_size)
        self._cancelled = False
        self._cancel_event = asyncio.Event()

        async def producer() -> None:
            """Producer coroutine that runs the generator."""
            try:
                async for token in generate_fn(*args, **kwargs):
                    if self._cancelled:
                        break
                    async with asyncio.timeout(self._config.cancel_timeout):
                        await self._queue.put(token)
                # Signal end of stream
                await self._queue.put(None)  # type: ignore
            except asyncio.CancelledError:
                self._stats.tokens_cancelled += 1
            except Exception as e:
                logger.warning(f'[StreamHandler] Producer error: {e}')
                self._stats.stream_errors += 1
                await self._queue.put(None)  # type: ignore

        # Start producer
        producer_task = asyncio.create_task(producer())

        try:
            while True:
                try:
                    async with asyncio.timeout(0.1):
                        token = await self._queue.get()
                except asyncio.TimeoutError:
                    # Check for cancellation
                    if self._cancelled:
                        break
                    continue
                if token is None:  # End of stream
                    break
                self._stats.tokens_yielded += 1
                yield token
        finally:
            producer_task.cancel()
            try:
                await producer_task
            except asyncio.CancelledError:
                pass

    async def cancel(self) -> None:
        """
        Cancel current streaming operation.

        M1 8GB: Cancellation should be responsive (<2s).
        """
        self._cancelled = True
        if self._cancel_event:
            self._cancel_event.set()

        # Drain queue
        if self._queue:
            while not self._queue.empty():
                try:
                    self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

    def format_stream_delta(
        self,
        token: str,
        decoded: str,
    ) -> str:
        """
        Format streaming delta for display.

        Override for custom formatting.

        Args:
            token: Raw token
            decoded: Decoded text

        Returns:
            Formatted delta string
        """
        # Default: return decoded (assumes partial decoding)
        return decoded


class SyncStreamPrep:
    """
    Synchronous streaming preparation.

    Extracted from DeepHermes3Engine._sync_stream_prep().
    Provides ChatML formatting for streaming prompts.
    """

    @staticmethod
    def format_chatml(
        system_msg: str,
        user_msg: str,
        history: list[dict[str, str]] | None = None,
    ) -> str:
        """
        Format messages in ChatML format.

        Args:
            system_msg: System message
            user_msg: User message
            history: Conversation history

        Returns:
            Formatted ChatML string
        """
        parts = [f'<|im_start|>system\n{system_msg}<|im_end|>']

        if history:
            for msg in history[-4:]:  # Last 4 messages
                role = msg.get('role', 'user')
                content = msg.get('content', '')
                parts.append(f'<|im_start|>{role}\n{content}<|im_end|>')

        parts.append(f'<|im_start|>user\n{user_msg}<|im_end|>')
        return '\n'.join(parts)

    @staticmethod
    def prepare_streaming_kwargs(
        prompt: str,
        max_tokens: int = 512,
        temperature: float = 0.7,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        Prepare kwargs for streaming generate.

        Args:
            prompt: Formatted prompt
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature
            **kwargs: Additional kwargs

        Returns:
            kwargs dict for mlx_lm.generate
        """
        from mlx_lm.sample_utils import make_sampler

        return {
            'prompt': prompt,
            'max_tokens': max_tokens,
            'sampler': make_sampler(temp=temperature),
            **kwargs,
        }