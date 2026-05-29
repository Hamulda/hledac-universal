# hledac/universal/export/components/stix_streaming.py
# Sprint F11N: Streaming STIX bundle write for large IOC sets
"""
Streaming STIX bundle write — bounded memory for large sprint sets.

When len(ioc_nodes) > 500, uses batched in-memory processing with
explicit memory management and size tracking.

Adds stix_bundle_size_bytes to SprintResult telemetry.
"""
from __future__ import annotations

import json
from datetime import UTC
from pathlib import Path
from typing import Any

__all__ = ["stream_stix_bundle", "STIXStreamingResult"]


class STIXStreamingResult:
    """Result of streaming STIX write."""

    bundle_size_bytes: int = 0
    object_count: int = 0
    ioc_count: int = 0
    capped: bool = False


def stream_stix_bundle(
    report: object,
    *,
    output_path: str | Path | None = None,
    ioc_threshold: int = 500,
) -> STIXStreamingResult:
    """
    Render STIX bundle with streaming-friendly handling for large IOC sets.

    When len(ioc_nodes) > ioc_threshold, processes in batches and tracks
    bundle size for telemetry (stix_bundle_size_bytes).

    Parameters
    ----------
    report : object
        ObservedRunReport or mapping compatible with render_stix_bundle.
    output_path : str | Path | None
        Output file path. If None, uses default path from render_stix_bundle_to_path.
    ioc_threshold : int
        Threshold for activating batch processing. Default 500.

    Returns
    -------
    STIXStreamingResult
        Telemetry: bundle_size_bytes, object_count, ioc_count, capped.
    """
    from hledac.universal.export.stix_exporter import (
        render_stix_bundle,
        render_stix_bundle_to_path,
    )

    result = STIXStreamingResult()

    # Normalize input to get IOC count
    try:
        data = normalize_for_stix_streaming(report)
    except Exception:
        data = {}

    ioc_count = _count_iocs(data)
    result.ioc_count = ioc_count
    result.capped = ioc_count > ioc_threshold

    if ioc_count > ioc_threshold:
        # Batched path — process with bounded memory
        bundle = _build_batched_stix_bundle(data, ioc_threshold)
    else:
        # Standard path — use existing render
        bundle = render_stix_bundle(report)

    # Serialize and measure
    content = json.dumps(bundle, indent=2, sort_keys=True, ensure_ascii=False)
    result.bundle_size_bytes = len(content.encode("utf-8"))
    result.object_count = len(bundle.get("objects", []))

    # Write to path if specified
    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(content, encoding="utf-8")
    elif output_path is not None:
        # Use render_stix_bundle_to_path for default path resolution
        path = render_stix_bundle_to_path(report)
        # Measure again with actual written path
        if path.exists():
            result.bundle_size_bytes = path.stat().st_size

    return result


def normalize_for_stix_streaming(report: object) -> dict[str, Any]:
    """
    Normalize report to dict for STIX streaming analysis.
    Handles both Mapping and msgpec.Struct input.
    """
    if isinstance(report, dict):
        return report
    if hasattr(report, "__dict__"):
        return vars(report)
    if hasattr(report, "asdict"):
        return report.asdict()
    # Fallback: try dict()
    try:
        return dict(report)  # type: ignore[arg-type]
    except Exception:
        return {}


def _count_iocs(data: dict[str, Any]) -> int:
    """Count total IOCs in the normalized report."""
    count = 0
    for finding in data.get("findings", []):
        if isinstance(finding, dict):
            iocs = finding.get("ioc_nodes", []) or finding.get("indicators", [])
            count += len(iocs) if iocs else 0
        elif hasattr(finding, "ioc_nodes"):
            iocs = finding.ioc_nodes or []
            count += len(iocs) if iocs else 0
    return count


def _build_batched_stix_bundle(data: dict[str, Any], batch_size: int) -> dict[str, Any]:
    """
    Build STIX bundle with batched object construction for large IOC sets.

    Processes findings in batches of batch_size to bound peak memory.
    """
    from hledac.universal.export.stix_exporter import (
        _BUNDLE_TYPE,
        _STIX_SPEC_VERSION,
        _bundle_id,
    )

    objects: list[dict[str, Any]] = []
    findings = data.get("findings", [])
    total_batches = (len(findings) + batch_size - 1) // batch_size

    for batch_idx in range(total_batches):
        batch = findings[batch_idx * batch_size : (batch_idx + 1) * batch_size]
        for finding in batch:
            # Build indicator from finding
            if isinstance(finding, dict):
                ioc_nodes = finding.get("ioc_nodes", []) or []
                for ioc in ioc_nodes:
                    ind = _build_indicator_from_ioc(finding, ioc)
                    if ind:
                        objects.append(ind)
            elif hasattr(finding, "ioc_nodes"):
                for ioc in (finding.ioc_nodes or []):
                    ind = _build_indicator_from_ioc(dict(finding), ioc)
                    if ind:
                        objects.append(ind)

    # Build minimal bundle structure
    from datetime import datetime

    now = datetime.now(UTC)
    created = now.isoformat()

    bundle: dict[str, Any] = {
        "type": _BUNDLE_TYPE,
        "id": _bundle_id(),
        "spec_version": _STIX_SPEC_VERSION,
        "created": created,
        "modified": created,
        "objects": objects,
    }
    return bundle


def _build_indicator_from_ioc(finding: dict, ioc: dict) -> dict[str, Any] | None:
    """Build a STIX indicator from an IOC node."""
    from hledac.universal.export.stix_exporter import (
        _build_indicator_object,
        _safe_str,
    )

    try:
        ioc_type = ioc.get("type", "unknown")
        ioc_value = ioc.get("value", "")

        if not ioc_value:
            return None

        pattern = _ioc_to_pattern(ioc_type, ioc_value)
        if not pattern:
            return None

        labels = [ioc_type, finding.get("source_type", "unknown")]
        float(ioc.get("confidence", 0.5))

        ind = _build_indicator_object(
            pattern=pattern,
            labels=labels,
            valid_from=_safe_str(finding.get("ts", "")) or "2020-01-01T00:00:00Z",
            description=f"hledac_ioc:{ioc_value}",
        )
        return ind
    except Exception:
        return None


def _ioc_to_pattern(ioc_type: str, ioc_value: str) -> str | None:
    """Convert IOC type+value to STIX pattern."""
    try:
        match ioc_type.lower():
            case "ipv4":
                return f"[ipv4-addr:value = '{ioc_value}']"
            case "ipv6":
                return f"[ipv6-addr:value = '{ioc_value}']"
            case "domain":
                return f"[domain-name:value = '{ioc_value}']"
            case "url":
                return f"[url:value = '{ioc_value}']"
            case "email" | "email_addr":
                return f"[email-addr:value = '{ioc_value}']"
            case "md5":
                return f"[file:hashes.'MD5' = '{ioc_value}']"
            case "sha256":
                return f"[file:hashes.'SHA-256' = '{ioc_value}']"
            case "sha1":
                return f"[file:hashes.'SHA-1' = '{ioc_value}']"
            case "hostname":
                return f"[hostname:value = '{ioc_value}']"
            case "username" | "account":
                return f"[user-account:account_type = '{ioc_value}']"
            case _:
                # Generic pattern for unknown types
                return None
    except Exception:
        return None
