"""
Matrix Public Rooms Intelligence Adapter.

Search Matrix public rooms for intelligence signals.
Uses matrix.org homeserver for public room directory.

M1 constraint: Max 50 messages per room, 10s timeout per request.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from aiohttp import ClientSession

logger = logging.getLogger(__name__)

# Matrix constants
MATRIX_HOMESERVER = "https://matrix-client.matrix.org"
MATRIX_TIMEOUT = 10.0
MAX_ROOM_MESSAGES = 50
MAX_ROOMS_TO_SEARCH = 20
MAX_GUEST_TOKEN_AGE = 3600  # 1 hour

# Rate limiting
MATRIX_RATE_LIMIT_DELAY = 2.0  # seconds between requests


@dataclass
class MatrixRoom:
    """Represents a Matrix public room."""
    room_id: str
    name: Optional[str]
    topic: Optional[str]
    canonical_alias: Optional[str]
    num_joined_members: int
    world_readable: bool
    guest_can_join: bool


@dataclass
class MatrixPublicAdapter:
    """Search Matrix public rooms for intelligence signals.

    Matrix.org has 80M+ users, many security/research communities.
    Public rooms can be searched via matrix.org's public directory.
    Requires guest access token for reading room messages.
    """
    _homeserver: str = field(default=MATRIX_HOMESERVER)
    _access_token: Optional[str] = field(default=None, repr=False)
    _token_acquired_at: float = field(default=0.0, repr=False)
    _session: Optional[ClientSession] = field(default=None, repr=False)
    _last_request_time: float = field(default=0.0, repr=False)

    @property
    def session(self) -> ClientSession:
        """Lazy session getter."""
        if self._session is None or self._session.closed:
            self._session = ClientSession()
        return self._session

    async def close(self) -> None:
        """Close HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()

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
        # Reuse token if still valid
        if self._access_token and (time.time() - self._token_acquired_at) < MAX_GUEST_TOKEN_AGE:
            return True

        await self._rate_limit()

        try:
            api_url = f"{self._homeserver}/_matrix/client/v3/register"
            data = {
                "kind": "guest"
            }

            async with self.session.post(
                api_url,
                json=data,
                timeout=MATRIX_TIMEOUT
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    self._access_token = result.get("access_token")
                    self._token_acquired_at = time.time()
                    return bool(self._access_token)
                return False
        except Exception as e:
            logger.debug(f"Guest registration failed: {e}")
            return False

    async def search_public_rooms(
        self,
        search_term: str,
        limit: int = MAX_ROOMS_TO_SEARCH
    ) -> list[MatrixRoom]:
        """Search public rooms by term.

        Args:
            search_term: Search term for room name/topic
            limit: Maximum rooms to return

        Returns:
            List of MatrixRoom objects
        """
        await self._rate_limit()

        try:
            api_url = f"{self._homeserver}/_matrix/client/v3/publicRooms"
            params: dict[str, Any] = {"limit": limit}
            if search_term:
                import json
                params["filter"] = json.dumps({"generic_search_term": search_term})

            async with self.session.get(
                api_url,
                params=params,
                timeout=MATRIX_TIMEOUT
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    chunk = data.get("chunk", [])
                    return [
                        MatrixRoom(
                            room_id=r.get("room_id", ""),
                            name=r.get("name"),
                            topic=r.get("topic"),
                            canonical_alias=r.get("canonical_alias"),
                            num_joined_members=r.get("num_joined_members", 0),
                            world_readable=r.get("world_readable", False),
                            guest_can_join=r.get("guest_can_join", False)
                        )
                        for r in chunk
                    ]
                return []
        except Exception as e:
            logger.debug(f"Public rooms search failed: {e}")
            return []

    async def get_room_messages(
        self,
        room_id: str,
        limit: int = MAX_ROOM_MESSAGES
    ) -> list[dict]:
        """Get recent messages from a public room.

        Requires guest access token.

        Args:
            room_id: Matrix room ID (e.g., "!roomid:matrix.org")
            limit: Maximum messages to fetch

        Returns:
            List of message dictionaries
        """
        if not await self._ensure_guest_token():
            logger.debug("No guest token available")
            return []

        await self._rate_limit()

        try:
            api_url = f"{self._homeserver}/_matrix/client/v3/rooms/{room_id}/messages"
            params = {
                "dir": "b",  # backwards (older messages first)
                "limit": min(limit, MAX_ROOM_MESSAGES)
            }
            headers = {
                "Authorization": f"Bearer {self._access_token}"
            }

            async with self.session.get(
                api_url,
                params=params,
                headers=headers,
                timeout=MATRIX_TIMEOUT
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("chunk", [])
                elif resp.status == 401:
                    # Token expired, refresh
                    self._access_token = None
                return []
        except Exception as e:
            logger.debug(f"Room messages fetch failed for {room_id}: {e}")
            return []

    async def get_room_info(self, room_id: str) -> Optional[dict]:
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
            api_url = f"{self._homeserver}/_matrix/client/v3/rooms/{room_id}"
            headers = {
                "Authorization": f"Bearer {self._access_token}"
            }

            async with self.session.get(
                api_url,
                headers=headers,
                timeout=MATRIX_TIMEOUT
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                return None
        except Exception as e:
            logger.debug(f"Room info fetch failed for {room_id}: {e}")
            return None

    async def search_and_fetch_rooms(
        self,
        search_term: str,
        max_messages: int = 30
    ) -> list[dict]:
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

        all_messages = []
        for room in rooms[:3]:  # Limit to top 3 rooms
            messages = await self.get_room_messages(room.room_id, max_messages)
            if messages:
                all_messages.extend(messages)

        return all_messages

    def is_enabled(self) -> bool:
        """Check if Matrix adapter is enabled."""
        return os.getenv("HLEDAC_ENABLE_SOCIAL", "").strip() == "1"
