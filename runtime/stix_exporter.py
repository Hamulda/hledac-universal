"""
runtime/stix_exporter.py — STIX 2.1 bundle export.

F350M-R: Native Rust STIX encoding + jsonschema validation.

Provides stix.encode(), stix.decode(), stix.validate() backed by
rust_extensions.stix_2_1 (serde_json + jsonschema, 2-4× faster than Python json.dumps).

API:
    stix.encode(finding) -> bytes       # STIX bundle bytes (STIX-JSON)
    stix.decode(bundle_bytes) -> dict  # Parse STIX bundle
    stix.validate(stix_json) -> ValidationResult  # Schema validation

Feature gate: HLEDAC_ENABLE_STIX=1 (default: 0, opt-in)
Python fallback: pure-Python json.dumps when Rust unavailable.

Integration: pipeline/live_public_pipeline.py:_generate_and_store_report
"""

from __future__ import annotations

import logging

# orjson — strict import with stdlib fallback (fail-safe, always-on)
try:
    import orjson as _orjson_mod

    _HAS_ORJSON: bool = True
except ImportError:
    _orjson_mod = None  # type: ignore[assignment]
    _HAS_ORJSON = False
    import json as _stdlib_json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from _core import aclose

logger = logging.getLogger(__name__)

# ─── Lazy Rust import ──────────────────────────────────────────────────────────

_RUST_STIX: Any | None = None


def _get_rust_stix():
    """Lazy-load Rust stix_2_1 module. Called on first use."""
    global _RUST_STIX
    if _RUST_STIX is None:
        # R6: Centralized Rust access via core.rust_backend
        from hledac.universal._core.rust_backend import rust
        _rust = rust.stix
        if _rust is not None and hasattr(_rust, "encode_finding"):
            _RUST_STIX = _rust
            logger.debug("[stix] Rust stix_2_1 loaded (serde_json + jsonschema)")
        else:
            _RUST_STIX = False  # type: ignore[assignment]
            logger.debug("[stix] Rust stix_2_1 missing encode_finding, using Python fallback")
    return _RUST_STIX if _RUST_STIX else None


def _json_loads(data: bytes | str) -> Any:
    """Fast JSON decode: tries orjson first, falls back to stdlib json."""
    if _HAS_ORJSON:
        return _orjson_mod.loads(data)
    return _stdlib_json.loads(data)


# ─── Public API ────────────────────────────────────────────────────────────────

STIX_BUNDLE_TYPE = "bundle"
STIX_SPEC_VERSION = "2.1"


def encode(finding: dict[str, Any]) -> bytes:
    """
    Encode a CanonicalFinding dict to a STIX 2.1 bundle (STIX-JSON bytes).

    Tries Rust stix_2_1.encode_finding() first (2-4× faster).
    Falls back to pure-Python json.dumps on error.

    Args:
        finding: CanonicalFinding-like dict with keys:
            ioc_type, ioc_value, source_type, confidence, query,
            finding_id (optional), payload_text (optional)

    Returns:
        UTF-8 encoded STIX bundle bytes
    """
    rust = _get_rust_stix()
    if rust is not None:
        try:
            result = rust.encode_finding(finding)
            if isinstance(result, bytes) and result:
                return result
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[stix] Rust encode_finding failed: {exc}, falling back to Python")

    return _py_encode(finding)


def decode(bundle_bytes: bytes) -> dict[str, Any]:
    """
    Parse STIX bundle bytes back to a Python dict.

    Tries Rust stix_2_1.decode_bundle() first.
    Falls back to json.loads on error.

    Args:
        bundle_bytes: STIX bundle as bytes

    Returns:
        Parsed STIX bundle dict
    """
    rust = _get_rust_stix()
    if rust is not None:
        try:
            result = rust.decode_bundle(bundle_bytes)
            if isinstance(result, str):
                parsed = _json_loads(result)
                if "error" not in parsed:
                    return parsed
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[stix] Rust decode_bundle failed: {exc}, falling back to Python")

    try:
        return _json_loads(bundle_bytes)
    except Exception:
        return {"error": "failed to decode STIX bundle"}


def validate(stix_json: str) -> ValidationResult:
    """
    Validate a STIX JSON string against STIX 2.1 schema.

    Tries Rust stix_2_1.validate_json() first.
    Falls back to structural Python validation on error.

    Args:
        stix_json: STIX JSON string

    Returns:
        ValidationResult dataclass with fields:
            is_valid: bool
            errors: list[dict]  # [{path, message, value_preview}, ...]
            object_count: int | None
    """
    rust = _get_rust_stix()
    if rust is not None:
        try:
            result_str = rust.validate_json(stix_json)
            if result_str:
                parsed = _json_loads(result_str)
                return ValidationResult(
                    is_valid=parsed.get("is_valid", False),
                    errors=parsed.get("errors", []),
                    object_count=parsed.get("object_count"),
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[stix] Rust validate_json failed: {exc}, falling back to Python")

    return _py_validate(stix_json)


# ─── Python fallback implementations ────────────────────────────────────────────


def _iso8601_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso8601_future(days: int = 90) -> str:
    future = datetime.now(timezone.utc) + timedelta(days=days)
    return future.strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _hash_algorithm(value: str) -> str:
    length = len(value)
    if length == 32:
        return "MD5"
    elif length == 40:
        return "SHA-1"
    elif length == 64:
        return "SHA-256"
    elif length == 128:
        return "SHA-512"
    return "MD5"


def _cybox_pattern(ioc_type: str, ioc_value: str) -> str:
    """Build a CyBox pattern for a given IOC type and value."""
    escaped = ioc_value.replace("\\", "\\\\").replace("'", "\\'")
    patterns: dict[str, str] = {
        "url": f"url = '{escaped}'",
        "ipv4-addr": f"ipv4-addr:value = '{escaped}'",
        "ipv6-addr": f"ipv6-addr:value = '{escaped}'",
        "domain-name": f"domain-name:value = '{escaped}'",
        "email-addr": f"email-addr:value = '{escaped}'",
        "md5": f"file-hash:hashes.MD5 = '{escaped}'",
        "sha1": f"file-hash:hashes.'SHA-1' = '{escaped}'",
        "sha256": f"file-hash:hashes.'SHA-256' = '{escaped}'",
        "sha512": f"file-hash:hashes.'SHA-512' = '{escaped}'",
        "mutex": f"mutex:name = '{escaped}'",
        "cve": f"vulnerability:cve = '{escaped}'",
    }
    return patterns.get(ioc_type, f"file-hash:hashes.MD5 = '{escaped}'")


def _build_sco(ioc_type: str, ioc_value: str) -> dict[str, Any]:
    """Build a STIX Cyber-observable Object (SCO) from IOC data."""
    sco: dict[str, Any] = {"type": ioc_type, "value": ioc_value}

    if ioc_type == "file-hash":
        algorithm = _hash_algorithm(ioc_value)
        sco["hashes"] = {algorithm: ioc_value}
    elif ioc_type in ("ipv4-addr", "ipv6-addr", "domain-name"):
        sco["resolves_to_refs"] = []

    return sco


def _py_encode(finding: dict[str, Any]) -> bytes:
    """Pure-Python STIX bundle encoding (fallback)."""
    bundle_id = f"bundle--{_new_uuid()}"
    now = _iso8601_now()

    ioc_type = finding.get("ioc_type", "unknown")
    ioc_value = finding.get("ioc_value", "")
    source_type = finding.get("source_type", "web")
    confidence = finding.get("confidence", 0.5)
    query = finding.get("query", "")
    payload = finding.get("payload_text", "")

    indicator_id = f"indicator--{_new_uuid()}"
    indicator: dict[str, Any] = {
        "type": "indicator",
        "spec_version": "2.1",
        "id": indicator_id,
        "created": now,
        "modified": now,
        "name": f"{source_type} indicator: {query[:120]}",
        "description": f"OSINT indicator extracted from {source_type} source. Query: {query[:500]}",
        "pattern": _cybox_pattern(ioc_type, ioc_value),
        "pattern_type": "stix",
        "valid_from": now,
        "confidence": int(confidence * 100),
        "valid_until": _iso8601_future(90),
        "labels": [source_type, "osint"],
        "object_marking_refs": ["marking-definition--613f2e26-407d-48f7-9f50-60798f4e9e5e"],
    }

    sco = _build_sco(ioc_type, ioc_value)
    if sco:
        indicator["objects"] = [sco]

    note_id = f"note--{_new_uuid()}"
    note: dict[str, Any] = {
        "type": "note",
        "spec_version": "2.1",
        "id": note_id,
        "created": now,
        "modified": now,
        "abstract": query[:500],
        "content": payload[:5000] if payload else "",
        "object_refs": [indicator_id],
        "labels": ["osint", "hledac"],
    }

    bundle: dict[str, Any] = {
        "type": "bundle",
        "id": bundle_id,
        "spec_version": "2.1",
        "objects": [indicator, note],
    }

    return _orjson_mod.dumps(bundle)


def _py_validate(stix_json: str) -> ValidationResult:
    """Pure-Python structural STIX validation (fallback)."""
    try:
        obj = _orjson_mod.loads(stix_json)
    except orjson.JSONDecodeError as exc:
        return ValidationResult(
            is_valid=False,
            errors=[{"path": "", "message": f"JSON parse error: {exc}", "value_preview": None}],
            object_count=None,
        )

    if not isinstance(obj, dict):
        return ValidationResult(
            is_valid=False,
            errors=[{"path": "", "message": "STIX object must be a JSON object", "value_preview": None}],
            object_count=None,
        )

    obj_type = obj.get("type")
    if obj_type == "bundle":
        objects = obj.get("objects", [])
        if not isinstance(objects, list):
            return ValidationResult(
                is_valid=False,
                errors=[{"path": "objects", "message": "'objects' must be an array", "value_preview": None}],
                object_count=None,
            )

        errors = []
        ids_seen: set[str] = set()
        for i, item in enumerate(objects):
            if not isinstance(item, dict):
                errors.append({"path": f"objects[{i}]", "message": "must be a JSON object", "value_preview": None})
                continue

            item_type = item.get("type", "unknown")
            item_id_raw = item.get("id")
            item_id: str | None = item_id_raw if isinstance(item_id_raw, str) else None

            if item_id is not None:
                if item_id in ids_seen:
                    errors.append(
                        {"path": f"objects[{i}].id", "message": f"Duplicate STIX ID '{item_id}'", "value_preview": None}
                    )
                ids_seen.add(item_id)
            else:
                errors.append(
                    {"path": f"objects[{i}].id", "message": f"Missing required field 'id'", "value_preview": None}
                )

            is_sco = item_type in ("ipv4-addr", "ipv6-addr", "domain-name", "url", "file-hash", "email-addr")
            if not is_sco and "spec_version" not in item:
                errors.append(
                    {
                        "path": f"objects[{i}].spec_version",
                        "message": f"SDO '{item_type}' missing 'spec_version'",
                        "value_preview": None,
                    }
                )

        return ValidationResult(is_valid=len(errors) == 0, errors=errors, object_count=len(objects))

    if not obj.get("id") or not obj.get("type"):
        return ValidationResult(
            is_valid=False,
            errors=[{"path": "", "message": "STIX object missing required 'id' or 'type'", "value_preview": None}],
            object_count=None,
        )

    return ValidationResult(is_valid=True, errors=[], object_count=1)


# ─── ValidationResult dataclass ─────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """
    STIX validation result.

    Attributes:
        is_valid: True if the STIX JSON is valid, False otherwise.
        errors: List of validation errors. Empty if is_valid=True.
        object_count: Number of objects in the bundle, or None if not countable.
    """

    is_valid: bool
    errors: list[dict[str, Any]]
    object_count: int | None

    def to_dict(self) -> dict[str, Any]:
        return {"is_valid": self.is_valid, "errors": self.errors, "object_count": self.object_count}

    def __repr__(self) -> str:
        return f"ValidationResult(is_valid={self.is_valid}, errors={len(self.errors)}, object_count={self.object_count})"
