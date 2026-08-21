"""
brain/hermes/structured.py — Structured Output Generation
====================================================

PEP 698: Extracted from brain/deephermes3_engine.py.

Handles:
- Outlines-based structured generation
- Pydantic/msgspec model validation
- Retry with correction logic
- Parse error recovery

M1 8GB: Uses outlines for grammar-constrained decoding when available.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Outlines availability check
OUTLINES_AVAILABLE = False
_outlines_module = None

try:
    import outlines

    _outlines_module = outlines
    OUTLINES_AVAILABLE = True
except ImportError:
    logger.warning("outlines not installed — grammar-constrained decoding disabled")


def _get_outlines_generator(model, tokenizer, response_model):
    """
    Get or create Outlines generator for a response model.

    Args:
        model: MLX model
        tokenizer: Tokenizer
        response_model: Pydantic or msgspec model

    Returns:
        Outlines generator or None
    """
    if not OUTLINES_AVAILABLE or _outlines_module is None:
        return None

    try:
        from outlines import generate

        key = id(response_model)

        if key not in _get_outlines_generator.cache:
            _get_outlines_generator.cache[key] = generate.json(
                model,
                response_model,
            )

        return _get_outlines_generator.cache[key]
    except Exception as e:
        logger.debug(f"[OUTLINES] Generator creation failed: {e}")
        return None


_get_outlines_generator.cache = {}


async def generate_structured[T](
    engine,
    prompt: str,
    response_model: type[T],
    temperature: float | None = None,
    max_tokens: int | None = None,
    system_msg: str | None = None,
    max_retries: int = 2,
    priority: float = 1.0,
) -> T:
    """
    Generate structured output using Pydantic/msgspec models.

    Args:
        engine: DeepHermes3Engine instance
        prompt: Input prompt
        response_model: Pydantic or msgspec model class
        temperature: Sampling temperature
        max_tokens: Maximum tokens to generate
        system_msg: Optional system message
        max_retries: Maximum retry attempts on parse failure
        priority: Batch priority

    Returns:
        Instance of response_model

    Raises:
        Exception: If all retries fail
    """
    temperature = temperature if temperature is not None else 0.1
    max_tokens = max_tokens if max_tokens is not None else 1024
    system_msg = system_msg or engine.config.system_prompt

    last_error: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            # Try outlines generation if available
            if OUTLINES_AVAILABLE and engine._outlines_model is not None:
                result = await _structured_with_outlines(
                    engine,
                    prompt,
                    response_model,
                    temperature,
                    max_tokens,
                    system_msg,
                )
                if result is not None:
                    return result

            # Fallback to regular generation with JSON parsing
            return await _structured_with_parse(
                engine,
                prompt,
                response_model,
                temperature,
                max_tokens,
                system_msg,
            )

        except Exception as e:
            last_error = e
            logger.debug(f"[STRUCTURED] Attempt {attempt + 1} failed: {e}")

            if attempt < max_retries:
                # Wait before retry
                await asyncio.sleep(0.1 * (attempt + 1))
                continue

    # All retries exhausted
    raise last_error or RuntimeError(f"generate_structured failed after {max_retries + 1} attempts")


async def _structured_with_outlines[T](
    engine,
    prompt: str,
    response_model: type[T],
    temperature: float,
    max_tokens: int,
    system_msg: str,
) -> T | None:
    """
    Generate using Outlines grammar-constrained decoding.

    Args:
        engine: DeepHermes3Engine instance
        prompt: Input prompt
        response_model: Response model class
        temperature: Temperature
        max_tokens: Max tokens
        system_msg: System message

    Returns:
        Structured result or None on failure
    """
    try:
        generator = _get_outlines_generator(
            engine._outlines_model,
            engine.tokenizer,
            response_model,
        )

        if generator is None:
            return None

        # Format prompt
        from .chatml import format_chatml

        formatted = format_chatml(system_msg, prompt)

        # Generate with outlines

        tokenized = engine.tokenizer
        tokens = tokenized.encode(formatted)

        # Use outlines to generate
        result = generator(tokens, max_tokens=max_tokens)

        # Decode and parse
        text = tokenized.decode(result)
        return _parse_json_response(text, response_model)

    except Exception as e:
        logger.debug(f"[OUTLINES] Generation failed: {e}")
        return None


async def _structured_with_parse[T](
    engine,
    prompt: str,
    response_model: type[T],
    temperature: float,
    max_tokens: int,
    system_msg: str,
) -> T:
    """
    Generate with JSON parsing fallback.

    Args:
        engine: DeepHermes3Engine instance
        prompt: Input prompt
        response_model: Response model class
        temperature: Temperature
        max_tokens: Max tokens
        system_msg: System message

    Returns:
        Structured result
    """
    # Generate text
    text = await engine.generate(
        prompt,
        system_msg=system_msg,
        temperature=temperature,
        max_tokens=max_tokens,
    )

    return _parse_json_response(text, response_model)


def _parse_json_response[T](text: str, response_model: type[T]) -> T:
    """
    Parse JSON response into structured model.

    Args:
        text: Generated text (may contain JSON in various formats)
        response_model: Model class to parse into

    Returns:
        Parsed model instance
    """
    # FIXED: Use absolute import for compat module (project root)
    # Try to extract JSON
    import json
    import re

    from hledac.universal.compat.pydantic_compat import (
        model_validate as _pydantic_validate,
    )

    json_match = re.search(
        r"\{[^{}]*(?:\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}[^{}]*)*\}",
        text,
        re.DOTALL,
    )

    if json_match:
        json_str = json_match.group()
    else:
        # Try parsing entire text as JSON
        json_str = text.strip()

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        raise ValueError(f"Failed to parse JSON: {json_str[:100]}")

    # Convert to model
    return _pydantic_validate(response_model, data)


def extract_parse_error(exc: Exception, raw_text: str, schema_cls: type) -> str:
    """
    Extract helpful error message from parse failure.

    Args:
        exc: The exception that occurred
        raw_text: The raw generated text
        schema_cls: The schema class

    Returns:
        Error message string
    """
    from hledac.universal.compat.pydantic_compat import get_model_fields

    try:
        fields = get_model_fields(schema_cls)
        field_names = list(fields.keys())
    except Exception:
        field_names = ["<unknown>"]

    error_parts = [
        f"Parse error: {exc}",
        f"Schema fields: {', '.join(field_names[:5])}",
    ]

    if len(raw_text) > 100:
        error_parts.append(f"Raw text (truncated): {raw_text[:100]}...")
    else:
        error_parts.append(f"Raw text: {raw_text}")

    return " | ".join(error_parts)


async def structured_with_correction[T](
    engine,
    prompt: str,
    response_model: type[T],
    temperature: float,
    max_tokens: int,
    system_msg: str,
    max_corrections: int = 1,
) -> T:
    """
    Generate with LLM-guided correction on parse failure.

    Args:
        engine: DeepHermes3Engine instance
        prompt: Input prompt
        response_model: Response model
        temperature: Temperature
        max_tokens: Max tokens
        system_msg: System message
        max_corrections: Max correction iterations

    Returns:
        Corrected structured result
    """
    last_result: T | None = None

    for correction_attempt in range(max_corrections + 1):
        try:
            return await generate_structured(
                engine,
                prompt,
                response_model,
                temperature,
                max_tokens,
                system_msg,
                max_retries=1,
            )
        except Exception as e:
            error_msg = extract_parse_error(e, str(e), response_model)

            if correction_attempt < max_corrections:
                # Ask LLM to correct
                correction_prompt = f"""Previous output failed validation:
{error_msg}

Original prompt: {prompt}

Generate corrected output:"""

                try:
                    prompt = await engine.generate(
                        correction_prompt,
                        system_msg="You are a helpful assistant. Fix the previous output.",
                        temperature=temperature,
                        max_tokens=max_tokens,
                    )
                except Exception:
                    # Give up on correction
                    pass

    # Return last result if available, otherwise raise
    if last_result is not None:
        return last_result

    raise RuntimeError(f"structured_with_correction failed after {max_corrections + 1} attempts")
