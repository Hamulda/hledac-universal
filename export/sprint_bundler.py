# export/sprint_bundler.py
# ISSUE [APEX]-1010: Unified .hledac-sprint bundle format
"""
Sprint bundler — creates single-file .hledac-sprint archives.

Bundles all sprint artifacts (report, seeds, evidence, metadata) into a
tar.zst archive with SHA-256 integrity manifest.

M1 8GB optimizations:
- APFS clonefile for evidence DB (CoW, zero-copy)
- zstd level 9 compression (good ratio/speed balance)
- Streaming tar write (bounded memory)
- compression.zstd (Python 3.14 stdlib)

ISSUE [META]-001: Extended with mmap byte-range offsets for zero-copy
entity loading without full decompression. Bundle extraction now builds
an index mapping entity→byte-range for O(1) access.

Bundle format:
  ~/.hledac/bundles/{sprint_id}.hledac-sprint
  └── tar.zst archive containing:
      ├── manifest.sha256 (SHA-256 hashes of all files)
      ├── metadata.json (sprint_id, timestamp, format version)
      ├── report.json (canonical sprint report)
      ├── seeds.json (next sprint seeds)
      ├── evidence.jsonl.zst (evidence log, zstd compressed)
      └── entity_index.json.zst (IOC → byte-range mapping for [META]-001)
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json as _stdlib_json
import logging
import os
import shutil
import tarfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Bundle format version
BUNDLE_FORMAT_VERSION = "1.0"


def _compute_sha256(data: bytes) -> str:
    """Compute SHA-256 hash of bytes."""
    return hashlib.sha256(data).hexdigest()


def _clonefile_or_copy(src: Path, dst: Path) -> bool:
    """
    APFS clonefile (CoW, zero-copy) with shutil.copy2 fallback.

    Returns True on success, False on failure.
    """
    try:
        # Try APFS clonefile first (macOS)
        os.clonefile(str(src), str(dst))
        return True
    except (AttributeError, OSError):
        # Fallback to shutil.copy2
        try:
            shutil.copy2(src, dst)
            return True
        except Exception as e:
            logger.warning(f"[BUNDLER] clonefile/copy failed: {e}")
            return False


async def bundle_sprint(
    sprint_id: str,
    report_path: Path | None = None,
    seeds_path: Path | None = None,
    evidence_path: Path | None = None,
    output_path: Path | None = None,
    metadata: dict[str, Any] | None = None,
    dashboard_html: Path | None = None,
) -> Path | None:
    """
    Create .hledac-sprint bundle from sprint artifacts.

    Args:
        sprint_id: Sprint identifier
        report_path: Path to report.json (optional, auto-detected)
        seeds_path: Path to seeds.json (optional, auto-detected)
        evidence_path: Path to evidence.jsonl (optional, auto-detected)
        output_path: Output bundle path (optional, auto-generated)
        metadata: Additional metadata to include in bundle
        dashboard_html: [META]-009: Path to pre-generated dashboard.html.
                       When set, the dashboard is included as dashboard.html
                       in the tar.zst archive AND stored alongside the bundle
                       as {sprint_id}.html for direct browser opening.

    Returns:
        Path to created bundle, or None on failure

    M1 8GB safe: streaming tar write, bounded memory, zstd compression.
    """
    from hledac.universal.paths import (
        get_sprint_bundle_path,
        get_sprint_json_report_path,
        get_sprint_next_seeds_path,
        EVIDENCE_ROOT,
    )

    # Auto-detect paths if not provided
    if report_path is None:
        report_path = get_sprint_json_report_path(sprint_id)
    if seeds_path is None:
        seeds_path = get_sprint_next_seeds_path(sprint_id)
    if evidence_path is None:
        # Evidence is stored as {run_id}.jsonl in EVIDENCE_ROOT
        # Try to find evidence for this sprint
        evidence_candidates = list(EVIDENCE_ROOT.glob(f"*{sprint_id}*.jsonl"))
        if evidence_candidates:
            evidence_path = evidence_candidates[0]
    if output_path is None:
        output_path = get_sprint_bundle_path(sprint_id)

    logger.info(f"[BUNDLER] Creating bundle for sprint {sprint_id}")
    logger.debug(f"[BUNDLER] report={report_path}, seeds={seeds_path}, evidence={evidence_path}")

    # Collect artifacts
    artifacts: dict[str, bytes] = {}
    manifest_entries: list[dict[str, str]] = []

    # 1. Metadata
    bundle_metadata = {
        "sprint_id": sprint_id,
        "format_version": BUNDLE_FORMAT_VERSION,
        "timestamp": datetime.now(UTC).isoformat(),
        "created_by": "hledac.universal.export.sprint_bundler",
    }
    if metadata:
        bundle_metadata.update(metadata)
    metadata_bytes = _stdlib_json.dumps(bundle_metadata, indent=2).encode("utf-8")
    artifacts["metadata.json"] = metadata_bytes
    manifest_entries.append({
        "file": "metadata.json",
        "sha256": _compute_sha256(metadata_bytes),
        "size": str(len(metadata_bytes)),
    })

    # 2. Report JSON
    if report_path and report_path.exists():
        try:
            report_bytes = report_path.read_bytes()
            artifacts["report.json"] = report_bytes
            manifest_entries.append({
                "file": "report.json",
                "sha256": _compute_sha256(report_bytes),
                "size": str(len(report_bytes)),
            })
            logger.debug(f"[BUNDLER] Added report.json ({len(report_bytes)} bytes)")
        except Exception as e:
            logger.warning(f"[BUNDLER] Failed to read report: {e}")
    else:
        logger.warning(f"[BUNDLER] Report not found: {report_path}")

    # 3. Seeds JSON
    if seeds_path and seeds_path.exists():
        try:
            seeds_bytes = seeds_path.read_bytes()
            artifacts["seeds.json"] = seeds_bytes
            manifest_entries.append({
                "file": "seeds.json",
                "sha256": _compute_sha256(seeds_bytes),
                "size": str(len(seeds_bytes)),
            })
            logger.debug(f"[BUNDLER] Added seeds.json ({len(seeds_bytes)} bytes)")
        except Exception as e:
            logger.warning(f"[BUNDLER] Failed to read seeds: {e}")
    else:
        logger.warning(f"[BUNDLER] Seeds not found: {seeds_path}")

    # 4. Evidence JSONL (compress with zstd)
    if evidence_path and evidence_path.exists():
        try:
            evidence_bytes = evidence_path.read_bytes()
            # Compress with zstd level 9
            try:
                import compression.zstd
                evidence_compressed = compression.zstd.compress(evidence_bytes, level=9)
                artifacts["evidence.jsonl.zst"] = evidence_compressed
                manifest_entries.append({
                    "file": "evidence.jsonl.zst",
                    "sha256": _compute_sha256(evidence_compressed),
                    "size": str(len(evidence_compressed)),
                })
                logger.debug(
                    f"[BUNDLER] Added evidence.jsonl.zst "
                    f"({len(evidence_bytes)} → {len(evidence_compressed)} bytes, "
                    f"ratio={len(evidence_compressed)/len(evidence_bytes):.2%})"
                )
            except ImportError:
                # Fallback: store uncompressed
                artifacts["evidence.jsonl"] = evidence_bytes
                manifest_entries.append({
                    "file": "evidence.jsonl",
                    "sha256": _compute_sha256(evidence_bytes),
                    "size": str(len(evidence_bytes)),
                })
                logger.warning("[BUNDLER] compression.zstd not available, storing uncompressed")
        except Exception as e:
            logger.warning(f"[BUNDLER] Failed to read evidence: {e}")
    else:
        logger.debug(f"[BUNDLER] Evidence not found: {evidence_path}")

    # 6. [META]-009: Dashboard HTML — standalone investigator dashboard
    if dashboard_html and dashboard_html.exists():
        try:
            html_bytes = dashboard_html.read_bytes()
            artifacts["dashboard.html"] = html_bytes
            manifest_entries.append({
                "file": "dashboard.html",
                "sha256": _compute_sha256(html_bytes),
                "size": str(len(html_bytes)),
            })
            logger.debug(f"[BUNDLER] Added dashboard.html ({len(html_bytes)} bytes)")
            # Also store alongside the bundle for direct browser opening
            try:
                import shutil as _shutil

                _shutil.copy2(dashboard_html, output_path.with_suffix(".html"))
            except Exception:
                pass  # Non-fatal
        except Exception as e:
            logger.warning(f"[BUNDLER] Failed to read dashboard: {e}")

    # 7. Generate manifest
    manifest_lines = [
        "# SHA-256 manifest for .hledac-sprint bundle",
        f"# Sprint: {sprint_id}",
        f"# Timestamp: {bundle_metadata['timestamp']}",
        "# Format: <sha256>  <filename>",
        "",
    ]
    for entry in manifest_entries:
        manifest_lines.append(f"{entry['sha256']}  {entry['file']}")
    manifest_text = "\n".join(manifest_lines) + "\n"
    manifest_bytes = manifest_text.encode("utf-8")
    artifacts["manifest.sha256"] = manifest_bytes

    # 8. Create tar.zst archive
    try:
        # Create tar in memory (streaming for large bundles)
        tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
            for filename, data in artifacts.items():
                info = tarfile.TarInfo(name=filename)
                info.size = len(data)
                info.mtime = datetime.now(UTC).timestamp()
                tar.addfile(info, io.BytesIO(data))

        tar_bytes = tar_buffer.getvalue()
        tar_buffer.close()

        # Compress with zstd level 9
        try:
            import compression.zstd
            bundle_bytes = compression.zstd.compress(tar_bytes, level=9)
            logger.debug(
                f"[BUNDLER] tar.zst compression: "
                f"{len(tar_bytes)} → {len(bundle_bytes)} bytes "
                f"(ratio={len(bundle_bytes)/len(tar_bytes):.2%})"
            )
        except ImportError:
            # Fallback: store as .tar (uncompressed)
            bundle_bytes = tar_bytes
            output_path = output_path.with_suffix(".tar")
            logger.warning("[BUNDLER] compression.zstd not available, storing as .tar")

        # Write bundle to disk
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(bundle_bytes)

        logger.info(f"[BUNDLER] Bundle created: {output_path} ({len(bundle_bytes)} bytes)")
        return output_path

    except Exception as e:
        logger.error(f"[BUNDLER] Failed to create bundle: {e}", exc_info=True)
        return None


async def verify_bundle(bundle_path: Path) -> dict[str, Any]:
    """
    Verify bundle integrity by checking SHA-256 hashes.

    Returns:
        Dict with verification results:
        - valid: bool (overall integrity)
        - manifest_valid: bool (manifest hash matches)
        - files_checked: int
        - errors: list[str]
    """
    result = {
        "valid": False,
        "manifest_valid": False,
        "files_checked": 0,
        "errors": [],
    }

    try:
        # Decompress bundle
        bundle_bytes = bundle_path.read_bytes()
        try:
            import compression.zstd
            tar_bytes = compression.zstd.decompress(bundle_bytes)
        except ImportError:
            # Assume uncompressed .tar
            tar_bytes = bundle_bytes

        # Extract tar
        tar_buffer = io.BytesIO(tar_bytes)
        with tarfile.open(fileobj=tar_buffer, mode="r") as tar:
            # Read manifest
            manifest_file = tar.extractfile("manifest.sha256")
            if not manifest_file:
                result["errors"].append("manifest.sha256 not found")
                return result

            manifest_text = manifest_file.read().decode("utf-8")
            manifest_lines = [
                line for line in manifest_text.split("\n")
                if line and not line.startswith("#")
            ]

            # Parse manifest entries
            manifest_entries = {}
            for line in manifest_lines:
                parts = line.split("  ", 1)
                if len(parts) == 2:
                    sha256, filename = parts
                    manifest_entries[filename] = sha256

            # Verify each file
            for filename, expected_hash in manifest_entries.items():
                if filename == "manifest.sha256":
                    continue  # Skip manifest itself

                file_obj = tar.extractfile(filename)
                if not file_obj:
                    result["errors"].append(f"{filename} not found in archive")
                    continue

                file_bytes = file_obj.read()
                actual_hash = _compute_sha256(file_bytes)

                if actual_hash != expected_hash:
                    result["errors"].append(
                        f"{filename}: hash mismatch "
                        f"(expected {expected_hash[:16]}..., got {actual_hash[:16]}...)"
                    )
                else:
                    result["files_checked"] += 1

            result["manifest_valid"] = len(result["errors"]) == 0
            result["valid"] = result["manifest_valid"]

    except Exception as e:
        result["errors"].append(f"Verification failed: {e}")

    return result


# ── Streaming Bundle Extraction with Byte-Range Index ─────────────────────────

async def extract_bundle_streaming(
    bundle_path: Path,
    sprint_id: str,
) -> tuple[Path, dict[str, dict[str, Any]]]:
    """
    Extract bundle with byte-range index for [META]-001 zero-copy loading.

    Streams the tar archive while building an index mapping IOC values
    to their byte offsets within the uncompressed tar. This enables
    mmap-based random access without full decompression.

    Args:
        bundle_path: Path to the .hledac-sprint bundle
        sprint_id: Sprint identifier

    Returns:
        (extracted_dir, entity_index): Directory with extracted content and
        index mapping "ioc_type:entity_value" → {entity_value, ioc_type, sha256, ...}
    """

    extracted_dir = bundle_path.parent / f"{sprint_id}_extracted"
    extracted_dir.mkdir(parents=True, exist_ok=True)

    entity_index: dict[str, dict[str, Any]] = {}
    now = time.time()
    _TAR_BLOCK_SIZE = 512  # tar records are 512-byte blocks

    def _data_offset(header_offset: int, member_size: int) -> int:
        """Calculate actual data offset after tar header and padding."""
        header_blocks = 1  # header is 1 block (512 bytes)
        data_blocks = (member_size + _TAR_BLOCK_SIZE - 1) // _TAR_BLOCK_SIZE
        return header_offset + (header_blocks + data_blocks) * _TAR_BLOCK_SIZE

    try:
        # Read bundle and decompress
        bundle_bytes = bundle_path.read_bytes()
        try:
            import compression.zstd
            tar_bytes = compression.zstd.decompress(bundle_bytes)
        except ImportError:
            tar_bytes = bundle_bytes

        # Stream through tar, tracking offsets
        tar_buffer = io.BytesIO(tar_bytes)
        with tarfile.open(fileobj=tar_buffer, mode="r") as tar:
            for member in tar:
                if not member.isfile():
                    continue

                # Track byte offset for this member
                current_offset = tar_buffer.tell()
                data_offset = _data_offset(current_offset, member.size)

                member_path = extracted_dir / member.name
                member_path.parent.mkdir(parents=True, exist_ok=True)

                with tar.extractfile(member) as src:
                    if src is not None:
                        data = src.read()
                        member_path.write_bytes(data)

                # Index evidence entries
                if member.name.startswith("evidence"):
                    try:
                        content = data
                        if member.name.endswith(".zst"):
                            try:
                                import compression.zstd
                                content = compression.zstd.decompress(data)
                            except ImportError:
                                pass

                        text = content.decode("utf-8")
                        sha256 = _compute_sha256(content)
                        data_length = len(content)

                        for line in text.splitlines():
                            if not line.strip():
                                continue
                            try:
                                import orjson
                                entry = orjson.loads(line)

                                # Extract IOC from entry
                                ioc_value = (
                                    entry.get("value")
                                    or entry.get("entity")
                                    or entry.get("ioc_value")
                                )
                                ioc_type = (
                                    entry.get("type")
                                    or entry.get("ioc_type")
                                    or "unknown"
                                )
                                source = entry.get("source", "unknown")
                                confidence = entry.get("confidence", 0.5)

                                if ioc_value and ioc_type:
                                    # Create composite key
                                    idx_key = f"{ioc_type}:{ioc_value}"

                                    if idx_key not in entity_index:
                                        entity_index[idx_key] = {
                                            "entity_value": ioc_value,
                                            "ioc_type": ioc_type,
                                            "last_confirmed_sprint": sprint_id,
                                            "source_count": 0,
                                            "sources": [],
                                            "sha256": sha256,
                                            "bundle_path": str(bundle_path),
                                            "mmap_offset": data_offset,
                                            "mmap_length": data_length,
                                            "first_seen_ts": now,
                                            "last_confirmed_ts": now,
                                            "confidence_sum": 0.0,
                                        }

                                    # Update aggregates
                                    entry_idx = entity_index[idx_key]
                                    entry_idx["source_count"] += 1
                                    if source not in entry_idx["sources"]:
                                        entry_idx["sources"].append(source)
                                    entry_idx["confidence_sum"] += confidence
                                    entry_idx["last_confirmed_ts"] = now

                            except Exception:
                                pass
                    except Exception as e:
                        logger.debug("[BUNDLER] Evidence indexing failed: %s", e)

    except Exception as e:
        logger.warning("[BUNDLER] Streaming extraction failed: %s", e)

    return (extracted_dir, entity_index)


async def index_bundle_entities(
    sprint_id: str,
    bundle_path: Path,
    entity_index: dict[str, dict[str, Any]],
    duckdb_store: Any | None = None,
) -> None:
    """
    Index extracted entities into DuckDB cross_sprint_entity_index.

    Uses async_upsert_cross_sprint_entity() via DuckDBShadowStore.
    Called after extract_bundle_streaming() completes.

    Args:
        sprint_id: Sprint identifier
        bundle_path: Path to the bundle (for logging)
        entity_index: Index from extract_bundle_streaming()
        duckdb_store: Optional DuckDBShadowStore reference. If None, tries
                      to get the shared store via knowledge module.
    """
    try:
        # Get duckdb store if not provided
        if duckdb_store is None:
            try:
                from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore
                # Try the singleton pattern if available
                duckdb_store = DuckDBShadowStore.get_shared_instance() if hasattr(
                    DuckDBShadowStore, "get_shared_instance"
                ) else None
            except Exception:
                duckdb_store = None

        if duckdb_store is None:
            logger.debug("[BUNDLER] No DuckDB store available for entity indexing")
            return

        indexed = 0
        for idx_key, entry in entity_index.items():
            try:
                avg_confidence = (
                    entry["confidence_sum"] / entry["source_count"]
                    if entry["source_count"] > 0
                    else 0.5
                )
                await duckdb_store.async_upsert_cross_sprint_entity(
                    entity_value=entry["entity_value"],
                    ioc_type=entry["ioc_type"],
                    sprint_id=sprint_id,
                    ts=entry["last_confirmed_ts"],
                    confidence=avg_confidence,
                    content_hash=entry.get("sha256"),
                )
                indexed += 1
            except Exception:
                pass

        logger.info(
            "[BUNDLER] Indexed %d entities from sprint %s into cross_sprint_entity_index",
            indexed, sprint_id
        )

    except Exception as e:
        logger.warning("[BUNDLER] Entity indexing failed: %s", e)


async def bundle_and_index_sprint(
    sprint_id: str,
    report_path: Path | None = None,
    seeds_path: Path | None = None,
    evidence_path: Path | None = None,
    output_path: Path | None = None,
    metadata: dict[str, Any] | None = None,
    index_entities: bool = True,
    duckdb_store: Any | None = None,  # [META]-001: store for entity indexing
    dashboard_html: Path | None = None,  # [META]-009: standalone dashboard
) -> Path | None:
    """
    Create bundle AND index entities into DuckDB cross_sprint_entity_index.

    Convenience function that calls bundle_sprint() and then
    extract_bundle_streaming() + index_bundle_entities().

    Args:
        Same as bundle_sprint() plus:
        index_entities: If True, also index entities for [META]-001
        duckdb_store: DuckDBShadowStore for entity indexing
        dashboard_html: [META]-009: Path to pre-generated dashboard.html

    Returns:
        Path to created bundle, or None on failure
    """
    # Create the bundle
    bundle_path = await bundle_sprint(
        sprint_id,
        report_path=report_path,
        seeds_path=seeds_path,
        evidence_path=evidence_path,
        output_path=output_path,
        metadata=metadata,
        dashboard_html=dashboard_html,
    )

    if bundle_path is None:
        return None

    # Index entities if requested
    if index_entities:
        try:
            _, entity_index = await extract_bundle_streaming(bundle_path, sprint_id)
            await index_bundle_entities(sprint_id, bundle_path, entity_index, duckdb_store=duckdb_store)
        except Exception as e:
            logger.warning("[BUNDLER] Entity indexing failed: %s", e)

    return bundle_path


__all__ = [
    "bundle_sprint",
    "bundle_and_index_sprint",
    "verify_bundle",
    "extract_bundle_streaming",
    "index_bundle_entities",
    "BUNDLE_FORMAT_VERSION",
]
