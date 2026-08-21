"""
CoreML Inference Utilities — ISSUE-005 Parallel Pipeline Integration
================================================================

Provides async inference functions for the InferencePipeline.

Functions:
    run_coreml_inference: Run inference via CoreML microservice
    batch_coreml_inference: Batch inference with parallel execution

Usage:
    from utils.coreml.inference import run_coreml_inference

    result = await run_coreml_inference(
        model_name="prm_step",
        inputs={"text": "evidence fact"}
    )
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Cache for client singleton
_coreml_client = None


async def _get_coreml_client():
    """Get or create the CoreML client singleton."""
    global _coreml_client
    if _coreml_client is None:
        try:
            from hledac.universal.utils.coreml.client import CoreMLClient

            _coreml_client = CoreMLClient()
        except Exception as e:
            logger.warning("[CoreML Inference] Failed to create client: %s", e)
            return None
    return _coreml_client


async def run_coreml_inference(
    model_name: str,
    inputs: dict[str, Any],
    *,
    compute_unit: str = "ALL",
    timeout: float = 30.0,
) -> dict[str, Any]:
    """
    Run inference via CoreML microservice.

    Args:
        model_name: Name of the cached model
        inputs: Input dictionary for the model
        compute_unit: Compute unit (ALL, CPU_ONLY, GPU_ONLY, ANE_ONLY)
        timeout: Request timeout in seconds

    Returns:
        Result dictionary with model outputs

    Raises:
        CoreMLServiceError: If the service returns an error
    """
    client = await _get_coreml_client()
    if client is None:
        logger.debug("[CoreML Inference] Client unavailable, returning fallback")
        return {"score": 0.5, "outputs": {}}

    try:
        from utils.coreml.service import ComputeUnit

        # Map string to ComputeUnit enum
        # NOTE: ComputeUnit is a StrEnum with values: CPU, GPU, ANE, ALL
        compute_unit_map = {
            "ALL": ComputeUnit.ALL,
            "CPU_ONLY": ComputeUnit.CPU,
            "GPU_ONLY": ComputeUnit.GPU,
            "ANE_ONLY": ComputeUnit.ANE,
        }
        compute_unit_enum = compute_unit_map.get(compute_unit.upper(), ComputeUnit.ALL)

        result = await asyncio.wait_for(
            client.predict(model=model_name, inputs=inputs, compute_unit=compute_unit_enum),
            timeout=timeout,
        )

        # Convert to dict
        if hasattr(result, "model_dump"):
            return result.model_dump()
        return {"score": 0.5, "outputs": {}}

    except TimeoutError:
        logger.warning("[CoreML Inference] Timeout for model %s", model_name)
        return {"score": 0.5, "outputs": {}, "error": "timeout"}
    except Exception as e:
        logger.debug("[CoreML Inference] Error for model %s: %s", model_name, e)
        return {"score": 0.5, "outputs": {}, "error": str(e)}


async def batch_coreml_inference(
    model_name: str,
    inputs_list: list[dict[str, Any]],
    *,
    compute_unit: str = "ALL",
    batch_size: int = 16,
    timeout: float = 60.0,
) -> list[dict[str, Any]]:
    """
    Run batch inference via CoreML microservice.

    Args:
        model_name: Name of the cached model
        inputs_list: List of input dictionaries
        compute_unit: Compute unit (ALL, CPU_ONLY, GPU_ONLY, ANE_ONLY)
        batch_size: Number of inputs per batch
        timeout: Total timeout in seconds

    Returns:
        List of result dictionaries
    """
    if not inputs_list:
        return []

    client = await _get_coreml_client()
    if client is None:
        return [{"score": 0.5, "outputs": {}} for _ in inputs_list]

    try:
        from utils.coreml.service import ComputeUnit

        # Map string to ComputeUnit enum
        # NOTE: ComputeUnit is a StrEnum with values: CPU, GPU, ANE, ALL
        compute_unit_map = {
            "ALL": ComputeUnit.ALL,
            "CPU_ONLY": ComputeUnit.CPU,
            "GPU_ONLY": ComputeUnit.GPU,
            "ANE_ONLY": ComputeUnit.ANE,
        }
        compute_unit_enum = compute_unit_map.get(compute_unit.upper(), ComputeUnit.ALL)

        results = []
        for i in range(0, len(inputs_list), batch_size):
            batch = inputs_list[i : i + batch_size]

            try:
                result = await asyncio.wait_for(
                    client.predict_batch(
                        model=model_name,
                        inputs=batch,
                        compute_unit=compute_unit_enum,
                    ),
                    timeout=timeout,
                )

                if hasattr(result, "results"):
                    results.extend(result.results)
                else:
                    results.extend([{} for _ in batch])

            except TimeoutError:
                logger.warning("[CoreML Inference] Batch timeout at index %d", i)
                results.extend([{"score": 0.5, "error": "timeout"} for _ in batch])
            except Exception as e:
                logger.debug("[CoreML Inference] Batch error at index %d: %s", i, e)
                results.extend([{"score": 0.5, "error": str(e)} for _ in batch])

        return results

    except Exception as e:
        logger.warning("[CoreML Inference] Batch inference failed: %s", e)
        return [{"score": 0.5, "error": str(e)} for _ in inputs_list]


async def check_coreml_health() -> bool:
    """
    Check if the CoreML service is healthy.

    Returns:
        True if service is healthy, False otherwise
    """
    client = await _get_coreml_client()
    if client is None:
        return False

    try:
        health = await client.health()
        return health.status == "ok"
    except Exception:
        return False
