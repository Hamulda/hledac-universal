#!/usr/bin/env python3
"""
CoreML FastAPI microservice — fully standalone (no hledac imports).
Runs in coremltools py3.12 venv with full ANE support.
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time
from collections import OrderedDict
from enum import Enum
from pathlib import Path
from typing import Any

import coremltools as ct
from coremltools.models import MLModel
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# ── ComputeUnit (local, not from models.py) ─────────────────────────────────


class ComputeUnit(str, Enum):
    CPU = "cpu"
    GPU = "gpu"
    ANE = "ane"
    ALL = "all"


_COMPUTE_UNIT_MAP = {
    ComputeUnit.CPU: ct.ComputeUnit.CPU_ONLY,
    ComputeUnit.GPU: ct.ComputeUnit.CPU_AND_GPU,
    ComputeUnit.ANE: ct.ComputeUnit.CPU_AND_NE,
    ComputeUnit.ALL: ct.ComputeUnit.ALL,
}


def _cu_from_str(cu: ComputeUnit) -> ct.ComputeUnit:
    return _COMPUTE_UNIT_MAP.get(cu, ct.ComputeUnit.ALL)


def _cu_label(cu: ct.ComputeUnit) -> str:
    return {
        ct.ComputeUnit.CPU_ONLY: "cpu",
        ct.ComputeUnit.CPU_AND_GPU: "gpu",
        ct.ComputeUnit.CPU_AND_NE: "ane",
        ct.ComputeUnit.ALL: "all",
    }.get(cu, "unknown")


# ── Pydantic models (local, standalone) ───────────────────────────────────────


class ConvertRequest(BaseModel):
    src: str
    dst: str
    model_type: str = "torch"
    compute_unit: ComputeUnit = ComputeUnit.ALL


class ConvertResult(BaseModel):
    success: bool
    dst: str | None = None
    error: str | None = None
    latency_ms: float = 0.0


class PredictRequest(BaseModel):
    model_name: str
    inputs: dict[str, Any]
    compute_unit: ComputeUnit = ComputeUnit.ALL


class PredictResult(BaseModel):
    outputs: dict[str, Any]
    latency_ms: float
    compute_unit_used: str


class BatchPredictRequest(BaseModel):
    model_name: str
    inputs: list[dict[str, Any]]
    compute_unit: ComputeUnit = ComputeUnit.ALL


class BatchPredictResult(BaseModel):
    results: list[PredictResult]
    total_latency_ms: float
    avg_latency_ms: float
    compute_unit_used: str


class HealthResult(BaseModel):
    status: str
    version: str
    ane: bool
    models_loaded: int = 0
    cache_max: int = 2


class ModelInfo(BaseModel):
    name: str
    loaded_at: float
    compute_unit: str
    input_shapes: dict[str, str]
    output_shapes: dict[str, str]


class ModelsResult(BaseModel):
    models: list[ModelInfo]
    cache_max: int = 2
    cache_used: int = 0


# ── Logging ────────────────────────────────────────────────────────────────────

logger = logging.getLogger("coreml-service")
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(
    logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S")
)
logger.addHandler(_handler)
logger.setLevel(logging.INFO)


def _log(level: str, msg: str, **kwargs: Any) -> None:
    lat = kwargs.pop("latency_ms", None)
    parts = [f'level="{level}"', f'msg="{msg}"']
    for k, v in kwargs.items():
        parts.append(f'{k}="{v}"')
    if lat is not None:
        parts.append(f'latency_ms="{lat:.2f}"')
    logger.info(" ".join(parts))


# ── Model Cache ────────────────────────────────────────────────────────────────

_MAX_CACHE = 2


def _shapes(model: MLModel, outputs: bool = False) -> dict[str, str]:
    spec = model._spec
    items = spec.description.output if outputs else spec.description.input
    return {item.name: str(item.type) for item in items}


class _ModelCache:
    def __init__(self, max_size: int = _MAX_CACHE) -> None:
        self._max_size = max_size
        self._cache: OrderedDict[str, MLModel] = OrderedDict()
        self._meta: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    def _evict_lru(self) -> None:
        if len(self._cache) >= self._max_size:
            name = self._cache.popitem(last=False)[0]
            _log("INFO", "evicted_model", model_name=name)

    async def load(
        self,
        name: str,
        path: str | Path,
        compute_unit: ComputeUnit = ComputeUnit.ALL,
    ) -> MLModel:
        async with self._lock:
            if name in self._cache:
                self._cache.move_to_end(name)
                return self._cache[name]

            self._evict_lru()
            t0 = time.perf_counter()
            cu = _cu_from_str(compute_unit)
            model = ct.models.MLModel(str(path), compute_units=cu)
            self._cache[name] = model
            self._meta[name] = {
                "loaded_at": time.monotonic(),
                "compute_unit": compute_unit.value,
                "input_shapes": _shapes(model),
                "output_shapes": _shapes(model, outputs=True),
            }
            latency = (time.perf_counter() - t0) * 1000
            _log("INFO", "model_loaded", model_name=name, latency_ms=latency)
            return model

    async def predict(
        self,
        name: str,
        inputs: dict[str, Any],
        compute_unit: ComputeUnit = ComputeUnit.ALL,
    ) -> tuple[dict[str, Any], float, str]:
        async with self._lock:
            if name not in self._cache:
                raise HTTPException(404, f"Model '{name}' not loaded")
            model = self._cache[name]
            self._cache.move_to_end(name)

        cu = _cu_from_str(compute_unit)
        t0 = time.perf_counter()
        raw = model.predict(inputs)
        outputs = {}
        for k, v in raw.items():
            if hasattr(v, "tolist"):
                outputs[k] = v.tolist()
            else:
                outputs[k] = v
        latency = (time.perf_counter() - t0) * 1000
        return outputs, latency, _cu_label(cu)

    async def predict_batch(
        self,
        name: str,
        inputs_list: list[dict[str, Any]],
        compute_unit: ComputeUnit = ComputeUnit.ALL,
    ) -> tuple[list[PredictResult], float, str]:
        results: list[PredictResult] = []
        t0 = time.perf_counter()
        for inputs in inputs_list:
            outs, lat, cu_used = await self.predict(name, inputs, compute_unit)
            results.append(PredictResult(outputs=outs, latency_ms=lat, compute_unit_used=cu_used))
        total = (time.perf_counter() - t0) * 1000
        avg = total / len(results) if results else 0.0
        return results, total, cu_used

    async def list_models(self) -> list[ModelInfo]:
        async with self._lock:
            return [
                ModelInfo(
                    name=name,
                    loaded_at=self._meta[name]["loaded_at"],
                    compute_unit=self._meta[name]["compute_unit"],
                    input_shapes=self._meta[name]["input_shapes"],
                    output_shapes=self._meta[name]["output_shapes"],
                )
                for name in self._cache
            ]

    async def unload(self, name: str) -> bool:
        async with self._lock:
            if name in self._cache:
                del self._cache[name]
                del self._meta[name]
                _log("INFO", "model_unloaded", model_name=name)
                return True
            return False

    async def clear(self) -> None:
        async with self._lock:
            self._cache.clear()
            self._meta.clear()
            _log("INFO", "cache_cleared")


# ── FastAPI App ──────────────────────────────────────────────────────────────

app = FastAPI(title="CoreML Service", version="9.0")
_cache = _ModelCache(max_size=_MAX_CACHE)


@app.get("/health", response_model=HealthResult)
async def health() -> HealthResult:
    try:
        import coremltools.libcoremlpython as _mlp  # noqa: F401
        ane_available = True
    except Exception:
        ane_available = False
    models = await _cache.list_models()
    return HealthResult(
        status="ok",
        version=ct.__version__,
        ane=ane_available,
        models_loaded=len(models),
        cache_max=_MAX_CACHE,
    )


@app.get("/models", response_model=ModelsResult)
async def list_models() -> ModelsResult:
    models = await _cache.list_models()
    return ModelsResult(models=models, cache_max=_MAX_CACHE, cache_used=len(models))


@app.delete("/models/{name}")
async def delete_model(name: str) -> JSONResponse:
    ok = await _cache.unload(name)
    if not ok:
        raise HTTPException(404, f"Model '{name}' not found")
    return JSONResponse({"ok": True, "model_name": name})


@app.post("/convert", response_model=ConvertResult)
async def convert(req: ConvertRequest) -> ConvertResult:
    t0 = time.perf_counter()
    try:
        src = Path(req.src)
        dst = Path(req.dst)
        cu = _cu_from_str(req.compute_unit)

        if not src.exists():
            return ConvertResult(success=False, error=f"Source not found: {src}")

        if req.model_type == "torch":
            import torch
            model = torch.jit.load(str(src))
            mlmodel = ct.convert(
                model,
                inputs=[
                    ct.TensorType(name=k, shape=v.shape)
                    for k, v in dict(model.state_dict()).items()
                ],
                compute_units=cu,
            )
        elif req.model_type == "onnx":
            try:
                import coremltools.converters.onnx as onnx_c
                mlmodel = onnx_c.convert(str(src), compute_units=cu)
            except ModuleNotFoundError:
                return ConvertResult(
                    success=False,
                    error="onnx converter not available. Install: pip install onnx",
                )
        else:
            return ConvertResult(success=False, error=f"Unsupported model_type: {req.model_type}")

        mlmodel.save(str(dst))
        latency = (time.perf_counter() - t0) * 1000
        _log("INFO", "model_converted", src=str(src), dst=str(dst), latency_ms=latency)
        return ConvertResult(success=True, dst=str(dst), latency_ms=latency)

    except Exception as e:
        latency = (time.perf_counter() - t0) * 1000
        _log("ERROR", "convert_failed", error=str(e), latency_ms=latency)
        return ConvertResult(success=False, error=str(e), latency_ms=latency)


@app.post("/predict", response_model=PredictResult)
async def predict(req: PredictRequest) -> PredictResult:
    name = req.model_name
    inputs = req.inputs
    cu = req.compute_unit

    cached = [m.name for m in await _cache.list_models()]
    if name not in cached:
        raise HTTPException(404, f"Model '{name}' not found. Load it first via POST /models/{{name}}/load")

    try:
        outputs, latency, cu_used = await _cache.predict(name, inputs, cu)
        return PredictResult(outputs=outputs, latency_ms=latency, compute_unit_used=cu_used)
    except HTTPException:
        raise
    except Exception as e:
        _log("ERROR", "predict_failed", model_name=name, error=str(e))
        raise HTTPException(500, str(e))


@app.post("/predict/batch", response_model=BatchPredictResult)
async def predict_batch(req: BatchPredictRequest) -> BatchPredictResult:
    name = req.model_name
    inputs_list = req.inputs
    cu = req.compute_unit

    try:
        results, total, cu_used = await _cache.predict_batch(name, inputs_list, cu)
        avg = total / len(results) if results else 0.0
        return BatchPredictResult(
            results=results,
            total_latency_ms=total,
            avg_latency_ms=avg,
            compute_unit_used=cu_used,
        )
    except HTTPException:
        raise
    except Exception as e:
        _log("ERROR", "batch_predict_failed", model_name=name, error=str(e))
        raise HTTPException(500, str(e))


@app.post("/models/{name}/load")
async def load_model(name: str, path: str, compute_unit: ComputeUnit = ComputeUnit.ALL) -> JSONResponse:
    try:
        await _cache.load(name, path, compute_unit)
        return JSONResponse({"ok": True, "model_name": name})
    except Exception as e:
        raise HTTPException(500, str(e))


@app.on_event("shutdown")
async def shutdown() -> None:
    await _cache.clear()
    _log("INFO", "service_shutdown")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")
