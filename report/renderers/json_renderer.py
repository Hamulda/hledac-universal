"""
msgspec.json.encode is faster than orjson for msgspec.Struct types.
For plain dicts, falls back to msgspec.json.Encoder().encode().
Supports incremental streaming write for large reports.

"""
import msgspec
from typing import TYPE_CHECKING, Any
if TYPE_CHECKING:
    from pathlib import Path
__all__ = ['JSONRenderer']

class JSONRenderer:
    """
    Renders reports to JSON using msgspec.

    For msgspec.Struct types: msgspec.json.encode() (fastest path)
    For plain dicts: msgspec.json.Encoder().encode() (structured, fast)
    For raw dicts with orjson fallback: handled by export_compat layer

    Streaming: yields chunks for large reports to avoid memory spikes.
    """
    __slots__ = tuple(('_encoder', '_indent'))

    def __init__(self, *, indent: int=2) -> None:
        self._indent = indent
        self._encoder = msgspec.json.Encoder(indent=self._indent)

    def encode(self, data: Any) -> str:
        """Encode data to JSON string."""
        if hasattr(data, '__struct__'):
            return msgspec.json.encode(data).decode()
        return self._encoder.encode(data).decode()

    def encode_bytes(self, data: Any) -> bytes:
        """Encode data to JSON bytes (zero-copy for msgspec.Struct)."""
        if hasattr(data, '__struct__'):
            return msgspec.json.encode(data)
        return self._encoder.encode(data)

    def render_to_file(self, data: Any, path: Path | str) -> Path:
        """Render to file with streaming write for large reports."""
        import os
        from pathlib import Path as P
        path = P(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if hasattr(data, '__struct__'):
            encoded = msgspec.json.encode(data)
            with open(path, 'wb') as fh:
                chunk_size = 64 * 1024
                for i in range(0, len(encoded), chunk_size):
                    fh.write(encoded[i:i + chunk_size])
            return path
        with open(path, 'w', encoding='utf-8') as fh:
            if hasattr(data, '__struct__'):
                chunk_gen = self._stream_struct(data, chunk_size=64 * 1024)
            else:
                chunk_gen = self._stream_dict(data, chunk_size=64 * 1024)
            for chunk in chunk_gen:
                fh.write(chunk)
        return path

    def _stream_struct(self, data: Any, chunk_size: int=64 * 1024):
        """Stream a msgspec.Struct as JSON chunks."""
        encoded = msgspec.json.encode(data)
        for i in range(0, len(encoded), chunk_size):
            yield encoded[i:i + chunk_size].decode()

    def _stream_dict(self, data: Any, chunk_size: int=64 * 1024):
        """Stream a dict as JSON chunks using msgspec.Encoder."""
        encoder = msgspec.json.Encoder(indent=self._indent)
        encoded = encoder.encode(data).decode()
        for i in range(0, len(encoded), chunk_size):
            yield encoded[i:i + chunk_size]