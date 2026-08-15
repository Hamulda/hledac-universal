"""
export/_shared.py — Consolidated shared helpers for export/ modules.

F4.3/F320: Deduplicated helpers previously duplicated across
- export/jsonld_exporter.py
- export/stix_exporter.py
- export/export_manager.py

Single-source-of-truth for these helpers:
- _safe_str()
- _iso_timestamp()
- normalize_export_input()
"""
import msgspec

from datetime import UTC, datetime
from typing import Any, cast
from collections.abc import Mapping
from core import aclose


# ─────────────────────────────────────────────────────────────────────────────
# Safe string conversion
# ─────────────────────────────────────────────────────────────────────────────

def _safe_str(val: Any) -> str:
    """Safe str conversion — None becomes empty string, everything else str()."""
    if val is None:
        return ""
    return str(val)


# ─────────────────────────────────────────────────────────────────────────────
# Timestamp normalization
# ─────────────────────────────────────────────────────────────────────────────

def _iso_timestamp(ts: Any, *, fmt: str = "iso") -> str:
    """
    Convert unix timestamp or datetime to string.

    Parameters
    ----------
    ts : Any
        Unix timestamp (int/float), datetime, or None.
    fmt : str
        - "iso"  : ISO format via .isoformat()  (JSON-LD / general use)
        - "rfc3339": RFC3339 "Z" suffix format (STIX / threat-intel use)

    Returns
    -------
    str
        Formatted timestamp string, or "unknown" (iso) / _utc_now() (rfc3339) fallback.
    """
    if ts is None:
        return _utc_now() if fmt == "rfc3339" else "unknown"
    try:
        if isinstance(ts, datetime):
            dt = ts
        else:
            dt = datetime.fromtimestamp(float(ts), tz=UTC)
    except (TypeError, ValueError):
        return _utc_now() if fmt == "rfc3339" else "unknown"

    if fmt == "rfc3339":
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return dt.isoformat()


def _utc_now() -> str:
    """Return current UTC time as RFC3339 string."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ─────────────────────────────────────────────────────────────────────────────
# Input normalization
# ─────────────────────────────────────────────────────────────────────────────

def normalize_export_input(report) -> dict[str, Any]:
    """
    Convert ObservedRunReport (msgspec.Struct) or Mapping → plain dict.

    msgspec.Structs use ``__struct_fields__`` for field-order preservation.
    For Mapping objects, dict(report) is safe.
    """
    # hasattr guard is intentional — bypasses type-checker false-positive on `object`
    if hasattr(report, "__struct_fields__"):  # type: ignore[has-type]
        return {f: getattr(report, f) for f in report.__struct_fields__}
    if isinstance(report, dict):
        return dict(report)
    if hasattr(report, "keys"):  # type: ignore[has-type]
        return dict(cast(Mapping, report))
    raise TypeError(
        f"report must be msgspec.Struct or Mapping, got {type(report).__name__}"
    )
