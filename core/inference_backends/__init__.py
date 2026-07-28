"""
core/inference_backends/ — Optional inference backends (mlxcel, coreml, mlx_inproc)
================================================================================
These backends are available to InferenceCoordinator:

- mlxcel: Default on M1 8GB (RSS savings ~2GB via subprocess isolation)
- mlx_inproc: Opt-in via HLEDAC_INFERENCE_BACKEND=mlx_inproc (in-process, dev/debug)
- coreml: Opt-in via HLEDAC_INFERENCE_BACKEND=coreml (CoreML FastAPI)

MlxcelBackend: Out-of-process mlxcel via MlxcelIpcClient (JSON-RPC over UDS)
CoreMLBackend: CoreML FastAPI microservice via CoreMLClient (http://127.0.0.1:8765)
"""
from __future__ import annotations

# CRITICAL: Import core.inference_coordinator FIRST to set TYPE_CHECKING=True.
# This ensures TYPE_CHECKING=False when backend modules import it,
# avoiding the "Any cannot be instantiated" bug.
from hledac.universal.core.inference_coordinator import (
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
    from hledac.universal.core.inference_backends.mlxcel_backend import MlxcelBackend
except ImportError:
    MlxcelBackend = None  # type: ignore[assignment]

try:
    from hledac.universal.core.inference_backends.coreml_backend import CoreMLBackend
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
