"""
evidence/_archiver.py — WARC Archiver for HTTP response archival.

ISO 28500 compliant WARC writer for raw HTTP response persistence.
Court-admissible forensic evidence storage with compressed offsets.

Architecture (Sprint Split-Brain):
- WARCArchiver: WARC writing, snippet extraction, global path registry
- WarcWriteResult: ISO 28500 WARC record metadata

ISSUE-003 FIX: Module-level locks registered via @auto_register decorator.
"""

from __future__ import annotations

import gzip
import hashlib
import logging
import threading
import uuid
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import msgspec
from compat.msgspec_gc_compat import Struct
from _core.lock_registry import LockCategory, auto_register

logger = logging.getLogger(__name__)

# Global WARC paths for sprint_exporter discovery
_warc_paths_global: list[str] = []
_warc_snippets_global: list[dict[str, Any]] = []


@auto_register(LockCategory.GRAPH)
def _warc_paths_lock():
    """Module-level lock for WARC paths and snippets global state."""
    return threading.Lock()


def _append_warc_snippet(snippet: dict[str, Any]) -> None:
    """Append a WARC snippet to the global singleton (thread-safe)."""
    global _warc_snippets_global
    with _warc_paths_lock():
        _warc_snippets_global.append(snippet)
        if len(_warc_snippets_global) > 500:
            _warc_snippets_global.pop(0)


def get_warc_paths() -> list[str]:
    """Return copy of globally registered WARC file paths."""
    with _warc_paths_lock():
        return list(_warc_paths_global)


def get_warc_snippets() -> list[dict[str, Any]]:
    """Return copy of globally registered WARC snippets."""
    with _warc_paths_lock():
        return list(_warc_snippets_global)


def _clear_warc_globals() -> None:
    """Clear global WARC state between sprints."""
    global _warc_paths_global, _warc_snippets_global
    with _warc_paths_lock():
        _warc_paths_global.clear()
        _warc_snippets_global.clear()


class WarcWriteResult(Struct, frozen=True, kw_only=True):
    """ISO 28500 WARC record metadata."""
    record_id: str = ""
    byte_offset: int = 0
    byte_length: int = 0
    warc_path: str = ""
    success: bool = False
    url: str = ""
    timestamp: str = ""
    warc_type: str = "response"
    payload_digest: str = ""
    status: int = 0
    compressed_offset: int = 0
    compressed_size: int = 0


class WARCWriter:
    """
    ISO 28500 compliant WARC/1.1 writer for raw HTTP response persistence.

    M1 8GB bounds:
    - MAX_WARC_SIZE = 10 GB per file (bounded rotation)
    - gzip compression (native C, Metal-compatible)

    Fail-safe invariants:
    - Any write error → logs and continues (never blocks sprint)
    - Rotation is transparent — callers just write, rotation is automatic
    """
    __slots__ = ('_path', '_file', '_current_size', '_max_size', '_record_counter', '_lock', '_logger', '_path_history')
    WARC_VERSION = "WARC/1.1"
    MAX_WARC_SIZE = 10 * 1024 * 1024 * 1024  # 10 GB
    _MIN_PAYLOAD_SIZE = 50

    def __init__(self, path: Path, max_size_gb: float = 10.0) -> None:
        self._path = Path(str(path).replace('.jsonl', '').replace('.enc', ''))
        if not str(self._path).endswith('.warc.gz'):
            self._path = self._path.parent / f"{self._path.stem}.warc.gz"
        self._file = gzip.GzipFile(self._path, 'ab', compresslevel=6)
        self._current_size = self._path.stat().st_size if self._path.exists() else 0
        self._max_size = int(max_size_gb * 1024 * 1024 * 1024)
        self._record_counter = 0
        self._lock = threading.Lock()
        self._logger = logger
        self._path_history: list[Path] = [self._path]

    def write_response(
        self,
        url: str,
        timestamp: datetime,
        http_request: bytes,
        http_response: bytes,
    ) -> WarcWriteResult | None:
        """Write a WARC response record (ISO 28500 Section 7.2)."""
        if len(http_response) < self._MIN_PAYLOAD_SIZE:
            return WarcWriteResult(success=False)

        try:
            with self._lock:
                self._record_counter += 1
                record_id = f"<urn:uuid:{uuid.uuid4()}>"
                payload_digest = f"sha1:{hashlib.sha1(http_response).hexdigest()}"
                byte_offset = self._current_size
                _current_warc_path = str(self._path)
                self._file.flush()
                compressed_offset = self._path.stat().st_size if self._path.exists() else 0

                header = (
                    f"{self.WARC_VERSION}\r\n"
                    f"WARC-Type: response\r\n"
                    f"WARC-Target-URI: {url}\r\n"
                    f"WARC-Date: {timestamp.isoformat()}Z\r\n"
                    f"WARC-Record-ID: {record_id}\r\n"
                    f"Content-Length: {len(http_response)}\r\n"
                    f"Content-Type: application/http;msgtype=response\r\n"
                    f"WARC-Payload-Digest: {payload_digest}\r\n"
                    f"WARC-Identified-Payload-Type: application/http;msgtype=response\r\n"
                    f"WARC-Filename: {self._path.name}\r\n"
                    f"\r\n"
    )

                request_header = (
                    f"{self.WARC_VERSION}\r\n"
                    f"WARC-Type: request\r\n"
                    f"WARC-Target-URI: {url}\r\n"
                    f"WARC-Date: {timestamp.isoformat()}Z\r\n"
                    f"WARC-Record-ID: <urn:uuid:{uuid.uuid4()}>\r\n"
                    f"Content-Length: {len(http_request)}\r\n"
                    f"Content-Type: application/http;msgtype=request\r\n"
                    f"\r\n"
    )

                request_block = request_header.encode('utf-8', errors='replace') + http_request + b"\r\n"
                self._file.write(request_block)
                self._current_size += len(request_block)

                response_block = header.encode('utf-8', errors='replace') + http_response + b"\r\n"
                self._file.write(response_block)
                self._current_size += len(response_block)

                byte_length = len(request_block) + len(response_block)
                self._file.flush()
                compressed_size = self._path.stat().st_size - compressed_offset

                if self._current_size > self._max_size:
                    self._rotate_unlocked()

                _http_status = self._parse_http_status(http_response)

                return WarcWriteResult(
                    url=url, timestamp=timestamp.isoformat() + 'Z', record_id=record_id,
                    byte_offset=byte_offset, byte_length=byte_length, warc_path=_current_warc_path,
                    warc_type='response', payload_digest=payload_digest, status=_http_status,
                    success=True, compressed_offset=compressed_offset, compressed_size=compressed_size,
    )
        except Exception as e:
            self._logger.debug("warc_write_failed", url=url, error=str(e))
            return None


    def write_raw(
        self,
        url: str,
        timestamp: datetime,
        http_response: bytes,
        content_type: str = "application/http;msgtype=response",
    ) -> WarcWriteResult | None:
        """
        Write a raw HTTP response (no request record).

        Use when only response bytes are available (e.g., from cache).
        """
        if len(http_response) < self._MIN_PAYLOAD_SIZE:
            return WarcWriteResult(success=False)

        try:
            with self._lock:
                self._record_counter += 1
                record_id = f"<urn:uuid:{uuid.uuid4()}>"
                payload_digest = f"sha1:{hashlib.sha1(http_response).hexdigest()}"
                byte_offset = self._current_size
                _current_warc_path = str(self._path)
                self._file.flush()
                compressed_offset = self._path.stat().st_size if self._path.exists() else 0

                header = (
                    f"{self.WARC_VERSION}\r\n"
                    f"WARC-Type: response\r\n"
                    f"WARC-Target-URI: {url}\r\n"
                    f"WARC-Date: {timestamp.isoformat()}Z\r\n"
                    f"WARC-Record-ID: {record_id}\r\n"
                    f"Content-Length: {len(http_response)}\r\n"
                    f"Content-Type: {content_type}\r\n"
                    f"WARC-Payload-Digest: {payload_digest}\r\n"
                    f"WARC-Filename: {self._path.name}\r\n"
                    f"\r\n"
    )

                block = header.encode('utf-8', errors='replace') + http_response + b"\r\n"
                self._file.write(block)
                self._current_size += len(block)
                self._file.flush()
                compressed_size = self._path.stat().st_size - compressed_offset

                if self._current_size > self._max_size:
                    self._rotate_unlocked()

                return WarcWriteResult(
                    url=url, timestamp=timestamp.isoformat() + 'Z', record_id=record_id,
                    byte_offset=byte_offset, byte_length=len(block), warc_path=_current_warc_path,
                    warc_type='response', payload_digest=payload_digest, status=0,
                    success=True, compressed_offset=compressed_offset, compressed_size=compressed_size,
    )
        except Exception as e:
            self._logger.debug("warc_write_raw_failed", url=url, error=str(e))
            return None

    def _parse_http_status(self, http_response: bytes) -> int:
        """Extract HTTP status code from response."""
        try:
            first_line = http_response[:http_response.find(b'\r\n')].decode('utf-8', errors='replace')
            if first_line.startswith('HTTP/'):
                parts = first_line.split(' ', 2)
                if len(parts) >= 2:
                    return int(parts[1])
        except Exception:
            pass
        return 0

    def _rotate_unlocked(self) -> None:
        """Rotate to new WARC file."""
        try:
            self._file.close()
            self._current_size = 0
            idx = len(self._path_history)
            new_path = self._path.parent / f"{self._path.stem}_{idx}.warc.gz"
            self._path = new_path
            self._path_history.append(new_path)
            self._file = gzip.GzipFile(self._path, 'ab', compresslevel=6)
        except Exception:
            self._file = gzip.GzipFile(self._path, 'ab', compresslevel=6)

    def close(self) -> None:
        """Close the WARC writer."""
        with self._lock:
            try:
                self._file.close()
            except Exception:
                pass

    @property
    def path(self) -> Path:
        return self._path

    @property
    def path_history(self) -> list[Path]:
        return self._path_history

    @property
    def record_count(self) -> int:
        return self._record_counter

    @property
    def current_size(self) -> int:
        return self._current_size


class WARCArchiver:
    """
    WARC archival for HTTP responses.

    Sprint Split-Brain: Extracted from EvidenceLog to isolate
    HTTP archival from event storage. Enables independent testing
    and WARC-only workflows.
    """

    __slots__ = (
        '_enabled', '_warc_writer', '_warc_path', '_warc_paths',
        '_warc_data_lock', '_warc_snippets',
    )

    def __init__(self, enabled: bool = True) -> None:
        self._enabled = enabled
        self._warc_writer: WARCWriter | None = None
        self._warc_path: Path | None = None
        self._warc_paths: list[str] = []
        self._warc_data_lock = threading.Lock()
        self._warc_snippets: list[dict[str, Any]] = []

    def init_writer(self, path: Path) -> None:
        """Initialize WARC writer with path."""
        if not self._enabled:
            return
        self._warc_path = path
        self._warc_writer = WARCWriter(path)

    def archive_raw(
        self,
        url: str,
        timestamp: datetime,
        http_response: bytes,
        content_type: str = "application/http;msgtype=response",
    ) -> WarcWriteResult | None:
        """Archive raw HTTP response (no request record)."""
        if not self._enabled:
            return None
        if self._warc_writer is None:
            return None
        result = self._warc_writer.write_raw(url, timestamp, http_response, content_type)
        if result:
            self._build_warc_snippet(result, http_response)
        return result


    def archive_http_response(
        self,
        url: str,
        timestamp: datetime,
        http_request: bytes,
        http_response: bytes,
    ) -> WarcWriteResult | None:
        """Archive HTTP response to WARC file."""
        if not self._enabled:
            return None

        if self._warc_writer is None:
            return None

        result = self._warc_writer.write_response(url, timestamp, http_request, http_response)
        if result:
            self._build_warc_snippet(result, http_response)
        return result

    def _build_warc_snippet(self, prov: WarcWriteResult, http_response: bytes) -> None:
        """Build dashboard-ready snippet from WARC record."""
        try:
            _body_start = http_response.find(b'\r\n\r\n')
            _raw_headers = http_response[:_body_start] if _body_start >= 0 else b''
            _body_bytes = http_response[_body_start + 4:] if _body_start >= 0 else http_response

            _status = prov.status or 0
            if _status == 0:
                _header_text = _raw_headers.decode('utf-8', errors='replace')
                _first_line = _header_text.split('\r\n')[0] if _header_text else ''
                if _first_line.startswith('HTTP/'):
                    _parts = _first_line.split(' ', 2)
                    if len(_parts) >= 2:
                        _status = int(_parts[1])

            _text_content = _body_bytes.decode('utf-8', errors='replace')

            _html_content: str | None = None
            if _text_content.strip().startswith(('<', '<?', '<!')):
                try:
                    class _TextExtractor(HTMLParser):
                        def __init__(self) -> None:
                            super().__init__()
                            self._lines: list[str] = []

                        def handle_data(self, data: str) -> None:
                            self._lines.append(data.strip())

                    _parser = _TextExtractor()
                    _parser.feed(_text_content)
                    _html_content = '\n'.join(_parser._lines)[:5000]
                except Exception:
                    _html_content = _text_content[:5000]

            _snippet = {
                'url': prov.url,
                'timestamp': prov.timestamp,
                'status': _status,
                'html': _html_content or '',
                'text': _text_content[:5000],
                'record_id': prov.record_id,
                'byte_offset': prov.byte_offset,
                'byte_length': prov.byte_length,
                'warc_path': prov.warc_path,
                'payload_digest': prov.payload_digest,
                'compressed_offset': prov.compressed_offset,
                'compressed_size': prov.compressed_size,
            }

            with self._warc_data_lock:
                self._warc_snippets.append(_snippet)
                if len(self._warc_snippets) > 500:
                    self._warc_snippets.pop(0)

            _append_warc_snippet(_snippet)
        except Exception:
            pass

    @property
    def warc_snippets(self) -> list[dict[str, Any]]:
        """Return WARC snippets for dashboard."""
        with self._warc_data_lock:
            return list(self._warc_snippets)

    def _register_warc_paths_global(self) -> None:
        """Register WARC paths in the global singleton."""
        if self._warc_writer:
            for _p in self._warc_writer.path_history:
                _path_str = str(_p)
                if _path_str not in self._warc_paths:
                    self._warc_paths.append(_path_str)
                    with _warc_paths_lock:
                        if _path_str not in _warc_paths_global:
                            _warc_paths_global.append(_path_str)

    def close(self) -> None:
        """Close WARC writer and register paths."""
        if self._warc_writer:
            try:
                self._warc_writer.close()
                self._register_warc_paths_global()
            except Exception:
                pass
            self._warc_writer = None
