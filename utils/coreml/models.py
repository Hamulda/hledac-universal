"""
Pydantic v2 models shared between CoreML service and client.
"""


from enum import Enum, StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ComputeUnit(StrEnum):
    """CoreML compute unit selection."""

    CPU = "cpu"
    GPU = "gpu"
    ANE = "ane"  # Apple Neural Engine
    ALL = "all"  # CPU + GPU + ANE


# ── Convert ────────────────────────────────────────────────────────────────────


class ConvertRequest(BaseModel):
    """Request to convert a model to CoreML format."""

    src: str = Field(description="Path to source model file (.pt, .onnx, . SavedModel)")
    dst: str = Field(description="Path to destination .mlpackage")
    model_type: str = Field(
        default="torch",
        description="Converter type: torch, onnx, or tensorflow",
    )
    compute_unit: ComputeUnit = Field(
        default=ComputeUnit.ALL,
        description="Target compute unit for the converted model",
    )


class ConvertResult(BaseModel):
    """Result of a model conversion."""

    success: bool
    dst: str | None = None
    error: str | None = None
    latency_ms: float =0.0


# ── Predict ────────────────────────────────────────────────────────────────────────


class PredictRequest(BaseModel):
    """Request for single inference."""

    model_name: str = Field(description="Name of the cached model")
    inputs: dict[str, Any] = Field(description="Input tensors as dict")
    compute_unit: ComputeUnit = Field(
        default=ComputeUnit.ALL,
        description="Override compute unit for this prediction",
    )


class PredictResult(BaseModel):
    """Result of a single inference."""

    outputs: dict[str, Any]
    latency_ms: float
    compute_unit_used: str


# ── Batch Predict ──────────────────────────────────────────────────────────────


class BatchPredictRequest(BaseModel):
    """Request for batch inference."""

    model_name: str = Field(description="Name of the cached model")
    inputs: list[dict[str, Any]] = Field(description="List of input dicts")
    compute_unit: ComputeUnit = Field(
        default=ComputeUnit.ALL,
        description="Override compute unit for batch",
    )


class BatchPredictResult(BaseModel):
    """Result of a batch inference."""

    results: list[PredictResult]
    total_latency_ms: float
    avg_latency_ms: float
    compute_unit_used: str


# ── Health ──────────────────────────────────────────────────────────────────────


class HealthResult(BaseModel):
    """Health check result."""

    status: str = Field(description="'ok' when healthy")
    version: str = Field(description="coremltools version")
    ane: bool = Field(description="Apple Neural Engine available")
    models_loaded: int = Field(default=0, description="Number of cached models")
    cache_max: int = Field(default=2, description="Max models in cache")


# ── Model Management ──────────────────────────────────────────────────────────


class ModelInfo(BaseModel):
    """Information about a loaded model."""

    name: str
    loaded_at: float  # monotonic timestamp
    compute_unit: str
    input_shapes: dict[str, str] # name -> shape string
    output_shapes: dict[str, str]


class ModelsResult(BaseModel):
    """List of currently loaded models."""

    models: list[ModelInfo]
    cache_max: int = 2
    cache_used: int = 0
