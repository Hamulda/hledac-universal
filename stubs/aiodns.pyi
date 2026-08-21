"""Stub for aiodns — c-ares backed async DNS resolver."""

import asyncio

class DNSResolver:
    loop: asyncio.AbstractEventLoop | None
    def __init__(self, loop: asyncio.AbstractEventLoop | None = None) -> None: ...
    async def gethostbyname(self, hostname: str, family: int) -> HostResult: ...

class HostResult:
    name: str
    addresses: list[HostAddr]
    __slots__: tuple[str, ...]

class HostAddr:
    host: str
    __slots__: tuple[str, ...]
