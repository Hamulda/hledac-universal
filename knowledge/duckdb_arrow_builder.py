"""DuckDB Arrow Builder — Arrow batch building for DuckDBShadowStore.

F360 Phase 4: Extracted Arrow batch building from DuckDBShadowStore.

ARCHITECTURE:
    duckdb_arrow_builder.py — Arrow IPC batch building with fallbacks
    duckdb_store.py         — DuckDBShadowStore (delegates here)

ARROW BUILDING STRATEGY (M1 8GB):
    1. Rust batch: Fastest, uses hledac_rust_extensions
    2. PyArrow fallback: Standard PyArrow Table construction
    3. Manual fallback: Row-by-row INSERT (slowest, last resort)

Memory: Bounded batch sizes, early exit on memory pressure.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any
from _core import aclose

if TYPE_CHECKING:
    from .duckdb_store import CanonicalFinding

__all__ = ["DuckDBArrowBuilder", "ArrowBuildConfig", "ArrowBuildStatus"]

logger = logging.getLogger(__name__)


# ─── Enums ───────────────────────────────────────────────────────────────────

class ArrowBuildStatus(Enum):
    """Arrow build path status."""
    SUCCESS = "success"
    FALLBACK_LEGACY = "fallback_legacy"
    FALLBACK_PYARROW = "fallback_pyarrow"
    FALLBACK_INIT = "fallback_init"
    FALLBACK_RUST = "fallback_rust"


# ─── Configuration ────────────────────────────────────────────────────────────

@dataclass(slots=True)
class ArrowBuildConfig:
    """M1 8GB bounded configuration for Arrow building."""
    
    min_batch_size: int = 5
    max_batch_size: int = 1024
    rust_batch_threshold: int = 50
    enable_fallback: bool = True
    arrow_metrics_enabled: bool = True


# ─── Arrow Builder ───────────────────────────────────────────────────────────

class DuckDBArrowBuilder:
    """
    Arrow batch builder for DuckDB findings.
    
    F360 Phase 4: Extracted from DuckDBShadowStore._build_arrow_batch_* methods.
    
    Builds Arrow IPC batches from CanonicalFinding objects with multiple fallbacks:
    1. Rust batch (fastest): Uses hledac_rust_extensions
    2. PyArrow: Standard PyArrow Table construction
    3. Manual: Row-by-row (slowest, fallback)
    
    M1 8GB: Uses __slots__ for minimal memory footprint.
    Bounded: Max 1024 findings per batch.
    """
    
    __slots__ = (
        "_config",
        "_metrics",
        "_rust_batch_func",
        "_pyarrow_module",
    )
    
    def __init__(
        self,
        config: ArrowBuildConfig | None = None,
        *,
        metrics: dict[str, int] | None = None,
    ) -> None:
        self._config = config or ArrowBuildConfig()
        self._metrics = metrics or {
            "arrow_selected": 0,
            "arrow_fallback_rust": 0,
            "arrow_fallback_pyarrow": 0,
            "arrow_fallback_legacy": 0,
            "arrow_success_count": 0,
            "arrow_error_count": 0,
        }
        self._rust_batch_func: Any | None = None
        self._pyarrow_module: Any | None = None
        self._try_init_rust()
        self._try_init_pyarrow()
    
    def _try_init_rust(self) -> None:
        """Try to initialize Rust batch function."""
        try:
            from hledac_rust_extensions import batch_findings_to_arrow_ipc
            self._rust_batch_func = batch_findings_to_arrow_ipc
        except ImportError:
            logger.debug("[ArrowBuilder] Rust batch not available")
            self._rust_batch_func = None
    
    def _try_init_pyarrow(self) -> None:
        """Try to initialize PyArrow module."""
        try:
            import pyarrow as pa
            self._pyarrow_module = pa
        except ImportError:
            logger.debug("[ArrowBuilder] PyArrow not available")
            self._pyarrow_module = None
    
    def get_metrics(self) -> dict[str, int]:
        """Return arrow metrics."""
        return dict(self._metrics)
    
    def build_arrow_batch(
        self,
        findings: list["CanonicalFinding"],
    ) -> tuple[Any | None, ArrowBuildStatus]:
        """
        Build Arrow IPC batch from findings.
        
        Args:
            findings: List of CanonicalFinding objects
            
        Returns:
            Tuple of (arrow_bytes_or_table, status)
        """
        if not findings:
            return None, ArrowBuildStatus.SUCCESS
        
        n = len(findings)
        
        # Check batch size bounds
        if n < self._config.min_batch_size:
            return None, ArrowBuildStatus.FALLBACK_LEGACY
        
        if n > self._config.max_batch_size:
            findings = findings[: self._config.max_batch_size]
        
        # Try Rust path first (fastest)
        if n >= self._config.rust_batch_threshold and self._rust_batch_func:
            result = self._try_rust_batch(findings)
            if result is not None:
                self._metrics["arrow_success_count"] += 1
                return result, ArrowBuildStatus.SUCCESS
        
        # PyArrow fallback
        if self._pyarrow_module:
            result = self._try_pyarrow_batch(findings)
            if result is not None:
                self._metrics["arrow_fallback_pyarrow"] += 1
                return result, ArrowBuildStatus.FALLBACK_PYARROW
        
        # Legacy fallback (row-by-row)
        self._metrics["arrow_fallback_legacy"] += 1
        return None, ArrowBuildStatus.FALLBACK_LEGACY
    
    def _try_rust_batch(
        self,
        findings: list["CanonicalFinding"],
    ) -> Any | None:
        """Try Rust batch building."""
        try:
            import orjson
            
            # Serialize findings to JSON for Rust
            serialized = [
                {
                    "id": f.finding_id,
                    "query": f.query,
                    "source_type": f.source_type,
                    "confidence": f.confidence,
                    "ts": f.ts,
                    "provenance": f.provenance,
                    "payload_text": f.payload_text,
                }
                for f in findings
            ]
            json_bytes = orjson.dumps(serialized)
            
            result = self._rust_batch_func(json_bytes)
            self._metrics["arrow_selected"] += 1
            return result
        except Exception as e:
            logger.debug(f"[ArrowBuilder] Rust batch failed: {e}")
            self._metrics["arrow_fallback_rust"] += 1
            return None
    
    def _try_pyarrow_batch(
        self,
        findings: list["CanonicalFinding"],
    ) -> Any | None:
        """Try PyArrow batch building."""
        if self._pyarrow_module is None:
            return None
        
        try:
            pa = self._pyarrow_module
            
            # Build column arrays
            ids = [f.finding_id for f in findings]
            queries = [f.query or "" for f in findings]
            source_types = [f.source_type or "" for f in findings]
            confidences = [f.confidence for f in findings]
            timestamps = [f.ts for f in findings]
            provenances = [f.provenance or "" for f in findings]
            payloads = [f.payload_text or "" for f in findings]
            
            # Create PyArrow table
            table = pa.table(
                {
                    "id": pa.array(ids, type=pa.string()),
                    "query": pa.array(queries, type=pa.string()),
                    "source_type": pa.array(source_types, type=pa.string()),
                    "confidence": pa.array(confidences, type=pa.float64()),
                    "ts": pa.array(timestamps, type=pa.float64()),
                    "provenance_json": pa.array(provenances, type=pa.string()),
                    "payload_text": pa.array(payloads, type=pa.string()),
                }
    )
            
            return table
        except Exception as e:
            logger.debug(f"[ArrowBuilder] PyArrow batch failed: {e}")
            self._metrics["arrow_error_count"] += 1
            return None
    
    def build_arrow_ipc_bytes(
        self,
        findings: list["CanonicalFinding"],
    ) -> tuple[bytes | None, ArrowBuildStatus]:
        """Build Arrow IPC bytes from findings."""
        result, status = self.build_arrow_batch(findings)
        
        if result is None:
            return None, status
        
        # If we got a PyArrow table, convert to IPC bytes
        if self._pyarrow_module and hasattr(result, "to_pybytes"):
            try:
                import io
                buffer = io.BytesIO()
                with self._pyarrow_module.ipc.new_file(buffer) as writer:
                    writer.write_table(result)
                return buffer.getvalue(), status
            except Exception as e:
                logger.debug(f"[ArrowBuilder] IPC conversion failed: {e}")
                return None, ArrowBuildStatus.FALLBACK_LEGACY
        
        # If result is already bytes, return as-is
        if isinstance(result, bytes):
            return result, status
        
        return None, ArrowBuildStatus.FALLBACK_LEGACY


# ─── Arrow Utilities ─────────────────────────────────────────────────────────

def arrow_ipc_to_record_batch(
    ipc_bytes: bytes,
    *,
    source: str = "unknown",
) -> Any | None:
    """
    Convert Arrow IPC bytes to record batch.
    
    Args:
        ipc_bytes: Arrow IPC format bytes
        source: Human-readable origin for debugging
        
    Returns:
        PyArrow RecordBatch or None on failure
    """
    try:
        import pyarrow as pa
        import io
        
        reader = pa.ipc.open_file(io.BytesIO(ipc_bytes))
        return reader.read_batch(0)
    except Exception as e:
        logger.debug(f"[ArrowBuilder] IPC to record batch failed ({source}): {e}")
        return None


def arrow_ipc_to_table(
    ipc_bytes: bytes,
    *,
    source: str = "unknown",
) -> Any | None:
    """
    Convert Arrow IPC bytes to table.
    
    Args:
        ipc_bytes: Arrow IPC format bytes
        source: Human-readable origin for debugging
        
    Returns:
        PyArrow Table or None on failure
    """
    try:
        import pyarrow as pa
        import io
        
        reader = pa.ipc.open_file(io.BytesIO(ipc_bytes))
        return reader.read_all()
    except Exception as e:
        logger.debug(f"[ArrowBuilder] IPC to table failed ({source}): {e}")
        return None
