"""
core/inference_backends/ — Optional inference backends (mlxcel, coreml)
======================================================================
These backends are maintained for testing and future use but are NOT
wired into the default InferenceCoordinator path.

To use: set HLEDAC_INFERENCE_BACKEND=mlxcel or HLEDAC_INFERENCE_BACKEND=coreml
before importing, or pass backend=InferenceBackend.MLXCEL explicitly.

MlxcelBackend: Out-of-process mlxcel via MlxcelIpcClient (JSON-RPC over UDS)
CoreMLBackend: CoreML FastAPI microservice via CoreMLClient (http://127.0.0.1:8765)
"""
from __future__ import annotations

# CRITICAL: Import core.inference_coordinator FIRST to set TYPE_CHECKING=True.
# This ensures TYPE_CHECKING=False when backend modules import it,
# avoiding the "Any cannot be instantiated" bug.
from core.inference_coordinator import (
    InferenceBackend,
    InferenceError,
    InferenceRequest,
    InferenceResponse,
    IInferenceBackend,
    Token,
)

# Now import backends (TYPE_CHECKING is now False in these modules,
# but InferenceResponse is the real class from above)
try:
    from core.inference_backends.mlxcel_backend import MlxcelBackend
except ImportError:
    MlxcelBackend = None  # type: ignore[assignment]

try:
    from core.inference_backends.coreml_backend import CoreMLBackend
except ImportError:
    CoreMLBackend = None  # type: ignore[assignment]

__all__ = [
    # Optional backends
    "MlxcelBackend",
    "CoreMLBackend",
    # Re-export DTOs for convenience
    "InferenceBackend",
    "InferenceError",
    "InferenceRequest",
    "InferenceResponse",
    "IInferenceBackend",
    "Token",
]
