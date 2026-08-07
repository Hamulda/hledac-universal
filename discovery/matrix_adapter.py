"""
Matrix Public Rooms Intelligence Adapter.

Search Matrix public rooms for intelligence signals.


Uses matrix.org homeserver for public room directory.

M1 constraint: Max 50 messages per room, 10s timeout per request.
"""
import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
import msgspec
from typing import Any
import httpx
logger = logging.getLogger(__name__)
MATRIX_HOMESERVER = 'https://matrix-client.matrix.org'
MATRIX_TIMEOUT = 10.0
MAX_ROOM_MESSAGES = 50
MAX_ROOMS_TO_SEARCH = 20
MAX_GUEST_TOKEN_AGE = 3600
MATRIX_RATE_LIMIT_DELAY = 2.0

class MatrixRoom(msgspec.Struct, gc=False):
    """Represents a Matrix public room."""
    room_id: str
    name: str | None
    topic: str | None
    canonical_alias: str | None
    num_joined_members: int
    world_readable: bool
    guest_can_join: bool

class MatrixPublicAdapter(msgspec.Struct, frozen=True, gc=False):
    """Search Matrix public rooms for intelligence signals.

    Matrix.org has 80M+ users, many security/research communities.
    Public rooms can be searched via matrix.org's public directory.
    Requires guest access token for reading room messages.
    """
    _homeserver: str = field(default=MATRIX_HOMESERVER)
    _last_request_time: float = field(default=0.0)
    _access_token: str | None = field(default=None, repr=False)
    _token_acquired_at: float = field(default=0.0, repr=False)
    _session: httpx.AsyncClient | None = field(default=None, repr=False)

    @property
    def session(self) -> httpx.AsyncClient:
        """Lazy session getter."""
        if self._session is None or self._session.is_closed:
            self._session = httpx.AsyncClient()
        return self._session

    async def close(self) -> None:
        """Close HTTP session."""
        if self._session and (not self._session.is_closed):
            await self._session.aclose()

    async def _rate_limit(self) -> None:
        """Enforce rate limiting.

        F259: Made async with asyncio.sleep to avoid blocking the event loop.
        """
        now = time.monotonic()
        if now - self._last_request_time < MATRIX_RATE_LIMIT_DELAY:
            await asyncio.sleep(MATRIX_RATE_LIMIT_DELAY - (now - self._last_request_time))
        self._last_request_time = time.monotonic()

    async def _ensure_guest_token(self) -> bool:
        """Ensure we have a valid guest access token."""
        if self._access_token and time.time() - self._token_acquired_at < MAX_GUEST_TOKEN_AGE:
            return True
        await self._rate_limit()
        try:
            api_url = f'{self._homeserver}/_matrix/client/v3/register'
            data = {'kind': 'guest'}
            async with self.session.post(api_url, json=data, timeout=httpx.Timeout(MATRIX_TIMEOUT)) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    self._access_token = result.get('access_token')
                    self._token_acquired_at = time.time()
                    return bool(self._access_token)
                return False
        except Exception as e:
            logger.debug(f'Guest registration failed: {e}')
            return False

    async def search_public_rooms(self, search_term: str, limit: int=MAX_ROOMS_TO_SEARCH) -> list[MatrixRoom]:
        """Search public rooms by term.

        Args:
            search_term: Search term for room name/topic
            limit: Maximum rooms to return

        Returns:
            List of MatrixRoom objects
        """
        await self._rate_limit()
        try:
            api_url = f'{self._homeserver}/_matrix/client/v3/publicRooms'
            params: dict[str, Any] = {'limit': limit}
            if search_term:
                import json
                params['filter'] = json.dumps({'generic_search_term': search_term})
            async with self.session.get(api_url, params=params, timeout=httpx.Timeout(MATRIX_TIMEOUT)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    chunk = data.get('chunk', [])
                    from hledac.universal.transport.circuit_breaker import get_breaker
                    try:
                        from urllib.parse import urlparse as _urlparse
                        get_breaker(_urlparse(api_url).netloc).record_success()
                    except Exception:  # noqa: BLE001
                        pass
                    return [MatrixRoom(room_id=r.get('room_id', ''), name=r.get('name'), topic=r.get('topic'), canonical_alias=r.get('canonical_alias'), num_joined_members=r.get('num_joined_members', 0), world_readable=r.get('world_readable', False), guest_can_join=r.get('guest_can_join', False)) for r in chunk]
                elif resp.status == 429:
                    from hledac.universal.transport.circuit_breaker import get_breaker
                    try:
                        from urllib.parse import urlparse as _urlparse
                        get_breaker(_urlparse(api_url).netloc).record_failure(failure_kind='matrix_search:429')
                    except Exception:  # noqa: BLE001
                        pass
                return []
        except Exception as e:
            logger.debug(f'Public rooms search failed: {e}')
            return []

    async def get_room_messages(self, room_id: str, limit: int=MAX_ROOM_MESSAGES) -> list[dict]:
        """Get recent messages from a public room.

        Requires guest access token.

        Args:
            room_id: Matrix room ID (e.g., "!roomid:matrix.org")
            limit: Maximum messages to fetch

        Returns:
            List of message dictionaries
        """
        if not await self._ensure_guest_token():
            logger.debug('No guest token available')
            return []
        await self._rate_limit()
        try:
            api_url = f'{self._homeserver}/_matrix/client/v3/rooms/{room_id}/messages'
            params = {'dir': 'b', 'limit': min(limit, MAX_ROOM_MESSAGES)}
            headers = {'Authorization': f'Bearer {self._access_token}'}
            async with self.session.get(api_url, params=params, headers=headers, timeout=httpx.Timeout(MATRIX_TIMEOUT)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get('chunk', [])
                elif resp.status == 401:
                    self._access_token = None
                return []
        except Exception as e:
            logger.debug(f'Room messages fetch failed for {room_id}: {e}')
            return []

    async def get_room_info(self, room_id: str) -> dict | None:
        """Get room state information.

        Args:
            room_id: Matrix room ID

        Returns:
            Room state dictionary or None
        """
        if not await self._ensure_guest_token():
            return None
        await self._rate_limit()
        try:
            api_url = f'{self._homeserver}/_matrix/client/v3/rooms/{room_id}'
            headers = {'Authorization': f'Bearer {self._access_token}'}
            async with self.session.get(api_url, headers=headers, timeout=httpx.Timeout(MATRIX_TIMEOUT)) as resp:
                if resp.status == 200:
                    return await resp.json()
                return None
        except Exception as e:
            logger.debug(f'Room info fetch failed for {room_id}: {e}')
            return None

    async def search_and_fetch_rooms(self, search_term: str, max_messages: int=30) -> list[dict]:
        """Convenience method: search rooms and fetch messages from top matches.

        Args:
            search_term: Search term for room name/topic
            max_messages: Messages to fetch per room

        Returns:
            List of message dictionaries from matching rooms
        """
        rooms = await self.search_public_rooms(search_term, limit=5)
        if not rooms:
            return []

        # P1-02: Parallelizace — room message fetching paralelně místo sekvenčně
        from hledac.universal.utils.async_helpers import parallel

        async def _get_messages(room: Any) -> list:
            msgs = await self.get_room_messages(room.room_id, max_messages)
            return msgs if msgs else []

        results = await parallel([_get_messages(r) for r in rooms[:3]], policy="log", ctx="matrix:get_messages")
        all_messages = []
        for msgs in results:
            all_messages.extend(msgs)
        return all_messages

    def is_enabled(self) -> bool:
        """Check if Matrix adapter is enabled."""
        return os.getenv('HLEDAC_ENABLE_SOCIAL', '').strip() == '1'