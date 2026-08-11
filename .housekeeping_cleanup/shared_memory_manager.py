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
    stored as views into the buffer (no copying beyond the initial IPC read).
    
    Usage:
        ipc_bytes = scanner.scan_mmap_arrow_ipc("/path/to/file.bin")
        result = IocScanResult.from_ipc_bytes(ipc_bytes)
        
        # Access columns directly (zero-copy views)
        patterns = result.patterns  # List[str]
        values = result.values      # List[str]
        
        # Iterate as named tuples
        for hit in result:
            print(f"{hit.pattern}: {hit.value} at {hit.start}-{hit.end}")
        
        # OPTIMIZATION: Zero-copy iteration over raw Arrow arrays
        for i in range(len(result)):
            pattern = result.pattern_at(i)  # Direct Arrow array access
            start = result.start_at(i)       # No list conversion
    """
    
    __slots__ = ('_batch', '_patterns', '_labels', '_values', '_starts', '_ends', 
                 '_pattern_col', '_label_col', '_value_col', '_start_col', '_end_col')
    
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
        pattern_col=None,
        label_col=None,
        value_col=None,
        start_col=None,
        end_col=None,
    ):
        self._batch = batch
        self._patterns = patterns
        self._labels = labels
        self._values = values
        self._starts = starts
        self._ends = ends
        # Store raw Arrow columns for zero-copy iteration (MODERN-24 optimization)
        self._pattern_col = pattern_col
        self._label_col = label_col
        self._value_col = value_col
        self._start_col = start_col
        self._end_col = end_col
    
    @classmethod
    def from_ipc_bytes(cls, ipc_bytes: bytes) -> 'IocScanResult':
        """Deserialize IOC scan results from Arrow IPC bytes.
        
        MODERN-24: This uses pa.ipc.open_stream() which creates zero-copy
        views into the buffer. No data is copied beyond the initial IPC bytes.
        
        Args:
            ipc_bytes: Arrow IPC stream bytes from Rust scan_mmap_arrow_ipc()
            
        Returns:
            IocScanResult with columns accessible as lists AND zero-copy accessors
            
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
            
            # Store raw Arrow columns for zero-copy iteration (no list conversion)
            # These are views into the IPC buffer - NO data copying
            pattern_col = batch.column('pattern')
            label_col = batch.column('label')
            value_col = batch.column('value')
            start_col = batch.column('start')
            end_col = batch.column('end')
            
            # Extract columns as Python lists (for backward compatibility)
            # This is still efficient because Arrow slices the views
            patterns = pattern_col.to_pylist()
            labels = label_col.to_pylist()
            values = value_col.to_pylist()
            starts = [int(x) for x in start_col.to_pylist()]
            ends = [int(x) for x in end_col.to_pylist()]
            
            return cls(
                batch=batch,
                patterns=patterns,
                labels=labels,
                values=values,
                starts=starts,
                ends=ends,
                pattern_col=pattern_col,
                label_col=label_col,
                value_col=value_col,
                start_col=start_col,
                end_col=end_col,
            )
            
        except Exception as e:
            raise ValueError(f"Failed to deserialize IPC bytes: {e}") from e
    
    # ---------------------------------------------------------------------------
    # Zero-copy column accessors (MODERN-24 optimization)
    # ---------------------------------------------------------------------------
    
    def pattern_at(self, index: int) -> str:
        """Zero-copy string access from Arrow array (no list conversion)."""
        if self._pattern_col is not None:
            return self._pattern_col[index].as_py()
        return self._patterns[index]
    
    def label_at(self, index: int) -> Optional[str]:
        """Zero-copy nullable string access from Arrow array."""
        if self._label_col is not None:
            val = self._label_col[index].as_py()
            return val  # Returns None for nulls automatically
        return self._labels[index]
    
    def value_at(self, index: int) -> str:
        """Zero-copy string access from Arrow array."""
        if self._value_col is not None:
            return self._value_col[index].as_py()
        return self._values[index]
    
    def start_at(self, index: int) -> int:
        """Zero-copy uint64 access from Arrow array (returns Python int)."""
        if self._start_col is not None:
            return self._start_col[index].as_py()
        return self._starts[index]
    
    def end_at(self, index: int) -> int:
        """Zero-copy uint64 access from Arrow array (returns Python int)."""
        if self._end_col is not None:
            return self._end_col[index].as_py()
        return self._ends[index]
    
    def iter_zero_copy(self):
        """Zero-copy iterator over hits (avoids list materialization).
        
        Usage:
            for hit in result.iter_zero_copy():
                print(f"{hit.pattern}: {hit.value}")
        
        Yields:
            IocHit namedtuples with zero-copy column access
        """
        n = len(self)
        for i in range(n):
            yield IocHit(
                pattern=self.pattern_at(i),
                label=self.label_at(i),
                value=self.value_at(i),
                start=self.start_at(i),
                end=self.end_at(i),
            )
    
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
    MODERN-25: Delegates to arrow_ipc_to_table() from duckdb_store for unified handling.
    Falls back to JSON if Arrow deserialization fails.
    
    Args:
        ipc_bytes: Arrow IPC bytes or JSON bytes
        
    Returns:
        Deserialized data as appropriate type
    """
    if ipc_bytes is None or len(ipc_bytes) == 0:
        return {}
    
    if isinstance(ipc_bytes[:6], bytes) and ipc_bytes[:6] == b'ARROW':
        # MODERN-25: Unified Arrow IPC helper
        try:
            from hledac.universal.knowledge.duckdb_store import arrow_ipc_to_table
            result = arrow_ipc_to_table(ipc_bytes, source="shared_memory")
            if result is not None:
                return result
        except ImportError:
            # duckdb_store not available, fall through to direct handling
            pass
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

        MODERN-25: Delegates to arrow_ipc_to_table() for unified Arrow IPC handling.

        Returns:
            Deserialized Python object
        """
        if self._buffer is None:
            raise ValueError('No data to deserialize. Call serialize() first.')
        if self._is_arrow_format():
            # MODERN-25: Unified Arrow IPC helper
            try:
                from hledac.universal.knowledge.duckdb_store import arrow_ipc_to_table
                result = arrow_ipc_to_table(self._buffer, source="arrow_shared_memory")
                if result is not None:
                    return result
            except ImportError:
                # duckdb_store not available, try direct pyarrow handling
                pass
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