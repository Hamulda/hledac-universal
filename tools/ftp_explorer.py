"""
FTP Explorer - strict FTP protocol adapter for bounded directory listing and text file fetching.

This module provides safe FTP operations with:
- Timeouts and concurrency limits
- Bounded depth and entry counts
- Allowed extension filtering
- Graceful fallback when aioftp not available

WARNING: Only use with trusted FTP servers. This is for legitimate research on
public FTP resources (e.g., academic data archives, government data).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# aioftp is optional
try:
    import aioftp
    FTP_AVAILABLE = True
except ImportError:
    FTP_AVAILABLE = False
    aioftp = None

# Constants for bounds
DEFAULT_TIMEOUT = 10  # seconds
MAX_DEPTH = 2
MAX_ENTRIES = 200
MAX_BYTES = 256 * 1024  # 256KB

# Allowed extensions for text file fetching
ALLOWED_EXTENSIONS = {'.txt', '.csv', '.json', '.log', '.md', '.xml', '.yaml', '.yml'}


@dataclass
class FTPListingItem:
    """Represents a single item in FTP directory listing."""
    path: str
    is_dir: bool
    size: int | None = None
    modified: str | None = None


class FTPExplorer:
    """
    Safe FTP explorer with bounded operations.

    WARNING: For authorized research only. Respect FTP server terms of service.
    """

    def __init__(
        self,
        timeout: int = DEFAULT_TIMEOUT,
        max_depth: int = MAX_DEPTH,
        max_entries: int = MAX_ENTRIES,
        max_bytes: int = MAX_BYTES
    ):
        """
        Initialize FTP explorer.

        Args:
            timeout: Connection timeout in seconds
            max_depth: Maximum directory recursion depth
            max_entries: Maximum entries to return per directory
            max_bytes: Maximum bytes to fetch for text files
        """
        self.timeout = timeout
        self.max_depth = max_depth
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self.logger = logging.getLogger(__name__)

    async def list(
        self,
        ftp_url: str,
        max_depth: int | None = None,
        max_entries: int | None = None
    ) -> list[FTPListingItem]:
        """
        List directory contents from FTP server.

        Args:
            ftp_url: FTP URL (e.g., ftp://ftp.example.com/data)
            max_depth: Override max depth (default: self.max_depth)
            max_entries: Override max entries (default: self.max_entries)

        Returns:
            List of FTPListingItem
        """
        if not FTP_AVAILABLE:
            self.logger.warning("aioftp not available, FTP listing disabled")
            return []

        depth = max_depth if max_depth is not None else self.max_depth
        entries_limit = max_entries if max_entries is not None else self.max_entries

        try:
            parsed = urlparse(ftp_url)
            host = parsed.hostname or ""
            port = parsed.port or 21
            path = parsed.path or "/"

            if not host:
                self.logger.error(f"Invalid FTP URL: {ftp_url}")
                return []

            async with aioftp.Client(timeout=self.timeout) as client:
                await client.connect(host, port)
                await client.login()

                items = await self._list_recursive(client, path, depth, entries_limit)
                return items

        except TimeoutError:
            self.logger.warning(f"FTP timeout for {ftp_url}")
            return []
        except Exception as e:
            self.logger.error(f"FTP list failed for {ftp_url}: {e}")
            return []

    async def _list_recursive(
        self,
        client: aioftp.Client,
        path: str,
        remaining_depth: int,
        entries_limit: int
    ) -> list[FTPListingItem]:
        """Recursively list directory contents."""
        if remaining_depth < 0:
            return []

        items = []

        try:
            # List current directory
            async for stat in client.list(path, details=True):
                name = stat.name
                item_path = f"{path.rstrip('/')}/{name}"

                is_dir = 'type' in stat and stat.type == 'd'
                size = stat.size if hasattr(stat, 'size') and not is_dir else None
                modified = stat.modify if hasattr(stat, 'modify') else None

                items.append(FTPListingItem(
                    path=item_path,
                    is_dir=is_dir,
                    size=size,
                    modified=modified
                ))

                # Recurse into subdirectories
                if is_dir and remaining_depth > 0 and len(items) < entries_limit:
                    sub_items = await self._list_recursive(
                        client, item_path, remaining_depth - 1, entries_limit
                    )
                    items.extend(sub_items)

                if len(items) >= entries_limit:
                    break

        except Exception as e:
            self.logger.warning(f"Failed to list {path}: {e}")

        return items[:entries_limit]

    async def fetch_text_file(
        self,
        ftp_url: str,
        max_bytes: int | None = None
    ) -> str:
        """
        Fetch small text file from FTP server.

        Args:
            ftp_url: FTP URL to file (e.g., ftp://ftp.example.com/data/file.txt)
            max_bytes: Override max bytes (default: self.max_bytes)

        Returns:
            File content as string (empty on failure)
        """
        if not FTP_AVAILABLE:
            self.logger.warning("aioftp not available, FTP fetch disabled")
            return ""

        bytes_limit = max_bytes if max_bytes is not None else self.max_bytes

        try:
            parsed = urlparse(ftp_url)
            host = parsed.hostname or ""
            port = parsed.port or 21
            path = parsed.path or "/"

            if not host:
                self.logger.error(f"Invalid FTP URL: {ftp_url}")
                return ""

            # Check extension
            if not self._is_allowed_extension(path):
                self.logger.warning(f"Extension not allowed: {path}")
                return ""

            async with aioftp.Client(timeout=self.timeout) as client:
                await client.connect(host, port)
                await client.login()

                # Get file size first
                stat = await client.stat(path)
                if stat.size and stat.size > bytes_limit:
                    self.logger.warning(f"File too large: {stat.size} > {bytes_limit}")
                    return ""

                # Download file
                async with client.download(path) as stream:
                    content = await stream.read()
                    content = content[:bytes_limit]  # Double-check bounds
                    return content.decode('utf-8', errors='replace')

        except TimeoutError:
            self.logger.warning(f"FTP timeout for {ftp_url}")
            return ""
        except Exception as e:
            self.logger.error(f"FTP fetch failed for {ftp_url}: {e}")
            return ""

    def _is_allowed_extension(self, path: str) -> bool:
        """Check if file extension is allowed."""
        # Extract extension
        match = re.search(r'\.([^./]+)$', path.lower())
        if not match:
            return False
        ext = '.' + match.group(1)
        return ext in ALLOWED_EXTENSIONS


async def list_ftp_directory(
    ftp_url: str,
    max_depth: int = MAX_DEPTH,
    max_entries: int = MAX_ENTRIES
) -> list[FTPListingItem]:
    """
    Convenience function to list FTP directory.

    Args:
        ftp_url: FTP directory URL
        max_depth: Maximum recursion depth
        max_entries: Maximum entries to return

    Returns:
        List of FTPListingItem
    """
    explorer = FTPExplorer(max_depth=max_depth, max_entries=max_entries)
    return await explorer.list(ftp_url)


async def fetch_ftp_text_file(
    ftp_url: str,
    max_bytes: int = MAX_BYTES
) -> str:
    """
    Convenience function to fetch text file from FTP.

    Args:
        ftp_url: FTP file URL
        max_bytes: Maximum bytes to fetch

    Returns:
        File content or empty string
    """
    explorer = FTPExplorer(max_bytes=max_bytes)
    return await explorer.fetch_text_file(ftp_url)


# Import-safe check
def _check_import() -> bool:
    """Verify module imports correctly."""
    return True
