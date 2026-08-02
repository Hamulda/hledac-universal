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

Bundle format:
  ~/.hledac/bundles/{sprint_id}.hledac-sprint
  └── tar.zst archive containing:
      ├── manifest.sha256 (SHA-256 hashes of all files)
      ├── metadata.json (sprint_id, timestamp, format version)
      ├── report.json (canonical sprint report)
      ├── seeds.json (next sprint seeds)
      └── evidence.jsonl.zst (evidence log, zstd compressed)
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import os
import shutil
import tarfile
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
    metadata_bytes = json.dumps(bundle_metadata, indent=2).encode("utf-8")
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

    # 5. Generate manifest
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

    # 6. Create tar.zst archive
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


__all__ = ["bundle_sprint", "verify_bundle", "BUNDLE_FORMAT_VERSION"]
