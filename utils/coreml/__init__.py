"""
CoreML microservice — ANE-accelerated inference for Hledac Universal.

Exports:
    CoreMLClient    — async HTTP client (py3.14 compatible)
    CoreMLServiceManager — lifecycle manager (start/stop/health)
    ConvertResult, PredictResult — result models
"""
from __future__ import annotations

from .client import CoreMLClient, CoreMLServiceError
from .manager import CoreMLServiceManager
from .models import (
    BatchPredictRequest,
    BatchPredictResult,
    ConvertRequest,
    ConvertResult,
    HealthResult,
    ModelInfo,
    ModelsResult,
    PredictRequest,
    PredictResult,
)

__all__ = [
    "CoreMLClient",
    "CoreMLServiceError",
    "CoreMLServiceManager",
    "ConvertResult",
    "PredictResult",
    "BatchPredictResult",
    "HealthResult",
    "ConvertRequest",
    "PredictRequest",
    "BatchPredictRequest",
    "ModelInfo",
    "ModelsResult",
]
