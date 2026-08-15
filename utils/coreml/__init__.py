"""
CoreML microservice — ANE-accelerated inference for Hledac Universal.

Exports:
    CoreMLClient    — async HTTP client (py3.14 compatible)
    CoreMLServiceManager — lifecycle manager (start/stop/health)
    ConvertResult, PredictResult — result models
"""


from .client import CoreMLClient, CoreMLServiceError
from .manager import CoreMLServiceManager





    BatchPredictRequest,
    BatchPredictResult,
    ComputeUnit,
    ConvertRequest,
    ConvertResult,
    EmbedRequest,
    EmbedResult,
    HealthResult,
    ModelInfo,
    ModelsResult,
    PredictRequest,
    PredictResult,
)

__all__ = [
    "CoreMLClient",
    "CoreMLServiceError",

from _core import aclose    "CoreMLServiceManager",
    "ConvertResult",
    "PredictResult",
    "BatchPredictResult",
    "HealthResult",
    "ConvertRequest",
    "PredictRequest",
    "BatchPredictRequest",
    "ModelInfo",
    "ModelsResult",
    "ComputeUnit",
    "EmbedRequest",
    "EmbedResult",
]
