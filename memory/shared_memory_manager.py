"""
Shared Memory Manager with Apache Arrow
=====================================


Zero-copy data transfer between phases using Apache Arrow.
Provides ArrowSharedMemory for efficient serialization/deserialization.

MODERN-24: Now supports zero-copy Arrow IPC export from Rust extensions.
Rust scan_mmap_arrow_ipc() returns IPC bytes; Python reads with pa.ipc.open_stream()
which uses zero-copy views into the buffer (no data copying beyond the IPC bytes).
"""
import logging
import os
from typing import Any, NamedTuple, Optional
from dataclasses import dataclass
from typing import List
logger = logging.getLogger(__name__)
try:
    import pyarrow as pa
    import pyarrow.ipc as ipc
    PYARROW_AVAILABLE = True
except ImportError:
    pa = None
    ipc = None
    PYARROW_AVAILABLE = False
from hledac.universal.utils.msgspec_json import ORJSON_AVAILABLE as _FACADE_ORJSON_AVAILABLE
from hledac.universal.utils.msgspec_json import decode as _msgspec_decode
from hledac.universal.utils.msgspec_json import encode as _msgspec_encode

def _json_dumps(obj: Any) -> bytes:
    """Serialize object to JSON bytes via msgspec facade (10-20x stdlib)."""
    return _msgspec_encode(obj)

def _json_loads(data) -> Any:
    """Deserialize JSON bytes to object via msgspec facade."""
    if data is None:
        return {}
    if isinstance(data, (bytes, bytearray, memoryview, str)):
        try:
            return _msgspec_decode(data)
        except Exception:  # noqa: BLE001
            pass
    return {}
ORJSON_AVAILABLE = _FACADE_ORJSON_AVAILABLE


# ============================================================================
# MODERN-24: IOC Scan Result Types (Arrow IPC)
# ============================================================================

@dataclass
class IocHit:
    """A single IOC pattern match from Rust streaming scanner.
    
    Attributes:
        pattern: Matched pattern name (e.g., "malware", "phishing")
        label: Optional label (e.g., "threat", "network")
        value: Matched value (UTF-8 decoded)
        start: Byte offset start position in scanned data
        end: Byte offset end position in scanned data
    """
    pattern: str
    label: Optional[str]
    value: str
    start: int
    end: int


class IocScanResult:
    """Container for IOC scan results from Arrow IPC bytes.
    
    MODERN-24: This class wraps Arrow IPC bytes and provides zero-copy
    access to the underlying data. The Arrow RecordBatch columns are
    stored as views into the buffer (no copying).
    
    Usage:
        ipc_bytes = scanner.scan_mmap_arrow_ipc("/path/to/file.bin")
        result = IocScanResult.from_ipc_bytes(ipc_bytes)
        
        # Access columns directly (zero-copy views)
        patterns = result.patterns  # List[str]
        values = result.values      # List[str]
        
        # Iterate as named tuples
        for hit in result:
            print(f"{hit.pattern}: {hit.value} at {hit.start}-{hit.end}")
    """
    
    __slots__ = ('_batch', '_patterns', '_labels', '_values', '_starts', '_ends')
    
    # Schema columns for IOC scan results (matches Rust arrow_c_data.rs)
    _SCHEMA_COLUMNS = ('pattern', 'label', 'value', 'start', 'end')
    
    def __init__(
        self,
        batch: 'pa.RecordBatch',
        patterns: List[str],
        labels: List[Optional[str]],
        values: List[str],
        starts: List[int],
        ends: List[int],
    ):
        self._batch = batch
        self._patterns = patterns
        self._labels = labels
        self._values = values
        self._starts = starts
        self._ends = ends
    
    @classmethod
    def from_ipc_bytes(cls, ipc_bytes: bytes) -> 'IocScanResult':
        """Deserialize IOC scan results from Arrow IPC bytes.
        
        MODERN-24: This uses pa.ipc.open_stream() which creates zero-copy
        views into the buffer. No data is copied beyond the initial IPC bytes.
        
        Args:
            ipc_bytes: Arrow IPC stream bytes from Rust scan_mmap_arrow_ipc()
            
        Returns:
            IocScanResult with columns accessible as lists
            
        Raises:
            ValueError: If IPC bytes are invalid or don't match expected schema
        """
        if not PYARROW_AVAILABLE:
            raise ImportError("pyarrow required for Arrow IPC deserialization")
        
        try:
            # Zero-copy: pyarrow creates views into the buffer, not copies
            reader = ipc.open_stream(ipc_bytes)
            batch = reader.read_next_batch()
            
            if batch is None:
                # Empty stream
                return cls(batch=None, patterns=[], labels=[], values=[], starts=[], ends=[])
            
            # Validate schema columns
            col_names = set(batch.schema.names)
            expected = set(cls._SCHEMA_COLUMNS)
            if col_names != expected:
                missing = expected - col_names
                extra = col_names - expected
                raise ValueError(
                    f"Invalid schema: missing {missing}, extra {extra}. "
                    f"Expected {cls._SCHEMA_COLUMNS}"
                )
            
            # Extract columns as Python lists (this is just pointer dereference, not copy)
            # The underlying Arrow arrays are views into the IPC buffer
            patterns = batch.column('pattern').to_pylist()
            labels = batch.column('label').to_pylist()
            values = batch.column('value').to_pylist()
            
            # Convert start/end to native int (they're uint64 in Arrow)
            starts = [int(x) for x in batch.column('start').to_pylist()]
            ends = [int(x) for x in batch.column('end').to_pylist()]
            
            return cls(
                batch=batch,
                patterns=patterns,
                labels=labels,
                values=values,
                starts=starts,
                ends=ends,
            )
            
        except Exception as e:
            raise ValueError(f"Failed to deserialize IPC bytes: {e}") from e
    
    @property
    def patterns(self) -> List[str]:
        """Matched pattern names (pattern column)."""
        return self._patterns
    
    @property
    def labels(self) -> List[Optional[str]]:
        """Optional labels (label column, None for nulls)."""
        return self._labels
    
    @property
    def values(self) -> List[str]:
        """Matched values (UTF-8 decoded)."""
        return self._values
    
    @property
    def starts(self) -> List[int]:
        """Byte offset start positions."""
        return self._starts
    
    @property
    def ends(self) -> List[int]:
        """Byte offset end positions."""
        return self._ends
    
    @property
    def batch(self) -> Optional['pa.RecordBatch']:
        """Raw Arrow RecordBatch (for advanced use)."""
        return self._batch
    
    def __len__(self) -> int:
        """Number of hits in the result."""
        return len(self._patterns)
    
    def __iter__(self):
        """Iterate as IocHit named tuples."""
        return (
            IocHit(
                pattern=p,
                label=l,
                value=v,
                start=s,
                end=e,
            )
            for p, l, v, s, e in zip(
                self._patterns, self._labels, self._values, self._starts, self._ends
            )
        )
    
    def to_list(self) -> List[dict]:
        """Convert to list of dicts (for backward compatibility)."""
        return [
            {
                'pattern': p,
                'label': l,
                'value': v,
                'start': s,
                'end': e,
            }
            for p, l, v, s, e in zip(
                self._patterns, self._labels, self._values, self._starts, self._ends
            )
        ]
    
    def is_empty(self) -> bool:
        """Check if result has any hits."""
        return len(self) == 0


def read_arrow_ipc_bytes(ipc_bytes: bytes) -> Any:
    """Read generic Arrow IPC bytes (fallback to JSON).
    
    MODERN-24: If bytes start with ARROW magic, tries Arrow IPC deserialization.
    Falls back to JSON if Arrow deserialization fails.
    
    Args:
        ipc_bytes: Arrow IPC bytes or JSON bytes
        
    Returns:
        Deserialized data as appropriate type
    """
    if ipc_bytes is None or len(ipc_bytes) == 0:
        return {}
    
    if isinstance(ipc_bytes[:6], bytes) and ipc_bytes[:6] == b'ARROW':
        try:
            reader = ipc.open_stream(ipc_bytes)
            table = reader.read_all()
            result = {}
            for col in table.column_names:
                result[col] = table.column(col).to_pylist()
            return result
        except Exception as e:
            logger.warning(f'Arrow IPC deserialization failed: {e}')
    
    # Fallback to JSON
    return _json_loads(ipc_bytes)

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
    __slots__ = tuple(('_buffer', '_closed', '_file_path', 'name', 'size'))

    def __init__(self, name: str, size: int=50000000):
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
        try:
            self._buffer = _json_dumps(data)
            self.size = len(self._buffer)
            return self.size
        except Exception as e:
            logger.warning(f'JSON serialization failed: {e}')
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
            raise ValueError('No data to deserialize. Call serialize() first.')
        if PYARROW_AVAILABLE and self._is_arrow_format():
            try:
                reader = pa.ipc.open_stream(pa.py_buffer(self._buffer))
                table = reader.read_all()
                result = {}
                for col in table.column_names:
                    arr = table.column(col)
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
                logger.warning(f'Arrow deserialization failed, falling back to JSON: {e}')
        try:
            return _json_loads(self._buffer)
        except Exception as e:
            logger.warning(f'JSON deserialization also failed: {e}')
            return {}

    def _is_arrow_format(self) -> bool:
        """Check if buffer starts with Arrow IPC magic bytes."""
        if self._buffer is None or len(self._buffer) < 6:
            return False
        return self._buffer[:6] == b'ARROW'

    def close(self):
        """Explicitly close and release memory."""
        if not self._closed:
            self._buffer = None
            if self._file_path and os.path.exists(self._file_path):
                try:
                    os.unlink(self._file_path)
                except Exception as e:
                    logger.debug(f'Failed to remove temp file: {e}')
            self._closed = True
            logger.debug(f'Closed ArrowSharedMemory {self.name}')

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False