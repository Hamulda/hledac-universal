"""
core/inference_backends/ — Optional inference backends (mlxcel, coreml, mlx_inproc)
================================================================================
These backends are available to InferenceCoordinator:

- mlx_inproc: Default on M1 8GB (in-process mlx-lm via DeepHermes3Engine)
- mlxcel: Opt-in via HLEDAC_INFERENCE_BACKEND=mlxcel (out-of-process Rust, RSS savings)
- coreml: Opt-in via HLEDAC_INFERENCE_BACKEND=coreml (CoreML FastAPI)

MlxcelBackend: Out-of-process mlxcel via MlxcelIpcClient (JSON-RPC over UDS)
CoreMLBackend: CoreML FastAPI microservice via CoreMLClient (http://127.0.0.1:8765)
"""

from __future__ import annotations

# CRITICAL: Import core.inference_coordinator FIRST to set TYPE_CHECKING=True.
# This ensures TYPE_CHECKING=False when backend modules import it,
# avoiding the "Any cannot be instantiated" bug.
from hledac.universal._core.inference_coordinator import (
    IInferenceBackend,
    InferenceBackend,
    InferenceError,
    InferenceRequest,
    InferenceResponse,
    Token,
)
from hledac.universal.utils.optional_imports import lazy_import

# Now import backends (TYPE_CHECKING is now False in these modules,
# but InferenceResponse is the real class from above)
MlxcelBackend = lazy_import("hledac.universal._core.inference_backends.mlxcel_backend:MlxcelBackend", default=None)

CoreMLBackend = lazy_import("hledac.universal._core.inference_backends.coreml_backend:CoreMLBackend", default=None)

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
