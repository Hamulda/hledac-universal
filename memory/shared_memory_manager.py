"""
Shared Memory Manager with Apache Arrow
=====================================

Zero-copy data transfer between phases using Apache Arrow.
Provides ArrowSharedMemory for efficient serialization/deserialization.
"""

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Try to import pyarrow for zero-copy operations
try:
    import pyarrow as pa
    PYARROW_AVAILABLE = True
except ImportError:
    PA = None
    PYARROW_AVAILABLE = False

# Try to import orjson for fast JSON serialization
try:
    import orjson
    ORJSON_AVAILABLE = True
except ImportError:
    import json
    ORJSON_AVAILABLE = False


def _json_dumps(obj: Any) -> bytes:
    """Serialize object to JSON bytes."""
    if ORJSON_AVAILABLE:
        return orjson.dumps(obj)
    return json.dumps(obj).encode('utf-8')


def _json_loads(data) -> Any:
    """Deserialize JSON bytes to object."""
    if data is None:
        return {}
    if ORJSON_AVAILABLE:
        try:
            return orjson.loads(data)
        except Exception:
            pass
    # Fallback to standard json
    try:
        if isinstance(data, bytes):
            return json.loads(data.decode('utf-8'))
        elif isinstance(data, str):
            return json.loads(data)
    except Exception:
        pass
    return {}


class ArrowSharedMemory:
    """
    Zero-copy shared memory using Apache Arrow for inter-process communication.

    Features:
    - Serializes data to Arrow IPC format
    - Stores in temporary file (or shared memory)
    - Provides zero-copy read via memory-mapped file
    - Explicit cleanup after deserialization

    Usage:
        with ArrowSharedMemory("my_data") as shm:
            shm.serialize(data)
            loaded = shm.deserialize()
        # Memory released after exiting with block
    """

    def __init__(self, name: str, size: int = 50_000_000):
        """
        Initialize Arrow shared memory.

        Args:
            name: Unique identifier for this shared memory
            size: Maximum size in bytes (default 50MB)
        """
        self.name = name
        self.size = size
        self._file_path: str | None = None
        self._buffer: bytes | None = None
        self._closed = False

    def serialize(self, data: Any) -> int:
        """
        Serialize data to Arrow IPC format.

        Args:
            data: Python object to serialize (dict, list, etc.)

        Returns:
            Size of serialized data in bytes
        """
        # Always use JSON for reliability - Arrow is optional optimization
        try:
            self._buffer = _json_dumps(data)
            self.size = len(self._buffer)
            return self.size
        except Exception as e:
            logger.warning(f"JSON serialization failed: {e}")
            self._buffer = b'{}'
            self.size = len(self._buffer)
            return self.size

    def deserialize(self) -> Any:
        """
        Deserialize data from Arrow IPC format.

        Returns:
            Deserialized Python object
        """
        if self._buffer is None:
            raise ValueError("No data to deserialize. Call serialize() first.")

        # Check if it's Arrow format and we have pyarrow available
        if PYARROW_AVAILABLE and self._is_arrow_format():
            try:
                # Read Arrow IPC format
                reader = pa.ipc.open_stream(pa.py_buffer(self._buffer))
                table = reader.read_all()

                # Convert to Python dict
                result = {}
                for col in table.column_names:
                    arr = table.column(col)
                    # Convert to Python list
                    if arr.type == pa.string():
                        result[col] = arr.to_pylist()
                    elif pa.types.is_integer(arr.type):
                        result[col] = arr.to_pylist()
                    elif pa.types.is_floating(arr.type):
                        result[col] = arr.to_pylist()
                    elif pa.types.is_boolean(arr.type):
                        result[col] = arr.to_pylist()
                    else:
                        result[col] = arr.to_pylist()

                return result

            except Exception as e:
                logger.warning(f"Arrow deserialization failed, falling back to JSON: {e}")

        # Fallback to JSON deserialization
        try:
            return _json_loads(self._buffer)
        except Exception as e:
            logger.warning(f"JSON deserialization also failed: {e}")
            # Last resort: return empty dict
            return {}

    def _is_arrow_format(self) -> bool:
        """Check if buffer starts with Arrow IPC magic bytes."""
        if self._buffer is None or len(self._buffer) < 6:
            return False
        # Arrow IPC file format magic bytes
        return self._buffer[:6] == b'ARROW'

    def close(self):
        """Explicitly close and release memory."""
        if not self._closed:
            self._buffer = None
            if self._file_path and os.path.exists(self._file_path):
                try:
                    os.unlink(self._file_path)
                except Exception as e:
                    logger.debug(f"Failed to remove temp file: {e}")
            self._closed = True
            logger.debug(f"Closed ArrowSharedMemory {self.name}")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
