"""
compat/pydantic_compat.py — Pydantic v2 Compatibility Layer

ROADMAP-006: Unified API for Pydantic v2 with msgspec.Struct support.

This module provides a single interface for model validation and schema extraction
that works seamlessly across:
- Pydantic v2 BaseModel (primary)
- msgspec.Struct (fallback path in structured generation)

Since pyproject.toml pins pydantic>=2.10.0,<3.0.0, Pydantic v2 is guaranteed.
The defensive hasattr() checks exist only to distinguish Pydantic models from
msgspec.Struct models in the structured generation pipeline.

Usage:
    from compat.pydantic_compat import (
        model_validate,
        model_validate_json,
        model_construct,
        get_schema,
        get_model_fields,
    )

Architecture:
    - model_validate: Unified validation (Pydantic model_validate, msgspec fallback)
    - model_validate_json: Parse JSON string + validate (Pydantic only)
    - model_construct: Skip validation, build from dict (Pydantic only)
    - get_schema: Get JSON schema (Pydantic only, msgspec uses str())
    - get_model_fields: Extract field names from any supported model type

Python 3.14+ compatible.
M1 Air 8GB optimized: minimal overhead, no unnecessary imports.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "model_validate",
    "model_validate_json",
    "model_construct",
    "get_schema",
    "get_model_fields",
    "is_pydantic_model",
    "is_msgspec_struct",
]


def is_pydantic_model(cls: type) -> bool:
    """Check if a class is a Pydantic BaseModel (has v2 model_validate).

    Args:
        cls: The class to check.

    Returns:
        True if Pydantic v2 BaseModel, False otherwise.
    """
    return hasattr(cls, "model_validate")


def is_msgspec_struct(cls: type) -> bool:
    """Check if a class is a msgspec.Struct (has __struct_fields__).

    Args:
        cls: The class to check.

    Returns:
        True if msgspec.Struct, False otherwise.
    """
    return hasattr(cls, "__struct_fields__")


def get_model_fields(cls: type) -> list[str]:
    """Extract field names from any supported model type.

    Priority:
        1. msgspec.Struct: __struct_fields__ (list of field names)
        2. Pydantic v2: model_fields (dict)
        3. Pydantic v1: __fields__ (dict)

    Args:
        cls: The model class.

    Returns:
        List of field names.
    """
    # msgspec.Struct: __struct_fields__ is a tuple of field names
    if hasattr(cls, "__struct_fields__"):
        return list(cls.__struct_fields__)  # type: ignore[union-attr]

    # Pydantic v2: model_fields is a dict of FieldInfo
    if hasattr(cls, "model_fields"):
        fields = getattr(cls, "model_fields", {})
        return list(fields.keys()) if fields else []

    # Pydantic v1 fallback: __fields__ is a dict
    if hasattr(cls, "__fields__"):
        fields = getattr(cls, "__fields__", {})
        return list(fields.keys()) if fields else []

    return []


def model_validate(cls: type, data: dict[str, Any]) -> Any:
    """Unified model validation across Pydantic v2 and msgspec.Struct.

    Args:
        cls: The model class (Pydantic BaseModel or msgspec.Struct).
        data: The data to validate.

    Returns:
        An instance of cls.

    Note:
        For Pydantic models: uses model_validate (full validation).
        For msgspec.Struct: decodes via msgspec (already validated).
    """
    # Pydantic v2: use model_validate
    if hasattr(cls, "model_validate"):
        return cls.model_validate(data)  # type: ignore[union-attr]

    # msgspec.Struct: already validated by msgspec.json.decode()
    # Just construct without validation
    if hasattr(cls, "model_construct"):
        return cls.model_construct(**data)  # type: ignore[union-attr]

    # Last resort: direct construction (may raise)
    return cls(**data)  # type: ignore[operator]


def model_validate_json(cls: type, json_str: str) -> Any:
    """Parse JSON string and validate against model (Pydantic v2 only).

    Args:
        cls: The Pydantic model class.
        json_str: The JSON string to parse and validate.

    Returns:
        An instance of cls.

    Raises:
        AttributeError: If cls is not a Pydantic model.

    Note:
        msgspec.Struct does not have model_validate_json.
        Use msgspec.json.decode() directly for msgspec models.
    """
    if not hasattr(cls, "model_validate_json"):
        raise AttributeError(
            f"{cls.__name__} does not have model_validate_json. This method is only available on Pydantic v2 models."
        )
    return cls.model_validate_json(json_str)  # type: ignore[union-attr]


def get_schema(cls: type) -> str:
    """Get JSON schema string from a model.

    Args:
        cls: The model class (Pydantic BaseModel preferred).

    Returns:
        JSON schema string, or string representation as fallback.

    Note:
        Pydantic v2: Uses model_json_schema() for full JSON schema.
        msgspec.Struct: Falls back to str(cls) since msgspec doesn't
            have a built-in schema generation method.
    """
    try:
        if hasattr(cls, "model_json_schema"):
            # Pydantic v2: Get JSON schema and encode to bytes, then decode to string
            import json

            schema = cls.model_json_schema()
            return json.dumps(schema)
    except Exception:
        pass

    # Fallback: use string representation
    return str(cls)


def model_construct(cls: type, **data: Any) -> Any:
    """Construct model instance without validation (Pydantic v2 only).

    Args:
        cls: The Pydantic model class.
        **data: Field values.

    Returns:
        An instance of cls without field validation.

    Warning:
        model_construct skips all validation. Only use this when you:
        - Are certain the data is valid (e.g., from trusted source)
        - Need maximum performance in hot paths
        - Are handling parse failures gracefully

    Note:
        msgspec.Struct does not have model_construct.
        Use cls(**data) directly for msgspec (validation is separate).
    """
    if hasattr(cls, "model_construct"):
        return cls.model_construct(**data)  # type: ignore[union-attr]

    # Fallback: direct construction (msgspec.Struct)
    return cls(**data)  # type: ignore[operator]


def construct_default(cls: type) -> Any:
    """Construct a model with default values after parse failure.

    Creates an instance with None/default for all fields, useful as
    a last-resort fallback when structured parsing completely fails.

    Args:
        cls: The model class.

    Returns:
        An instance with default field values.
    """
    field_names = get_model_fields(cls)

    # For msgspec.Struct with required fields, we need actual values
    # For Pydantic, model_construct allows omitting fields
    if hasattr(cls, "model_construct"):
        # Pydantic: model_construct allows partial data
        fields = dict.fromkeys(field_names)
        return cls.model_construct(**fields)  # type: ignore[union-attr]

    # msgspec.Struct or other: try with None values
    try:
        return cls(**dict.fromkeys(field_names))  # type: ignore[operator]
    except Exception:
        # Ultimate fallback: empty construction
        try:
            return cls()  # type: ignore[operator]
        except Exception:
            # Cannot construct at all
            return None
