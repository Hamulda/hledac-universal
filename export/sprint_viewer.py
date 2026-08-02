# export/sprint_viewer.py
# ISSUE [APEX]-1010: Offline sprint bundle viewer
"""
Sprint bundle viewer — extract and render .hledac-sprint bundles.

Usage:
    python -m hledac.universal.export.sprint_viewer <bundle_path> [--extract <dir>]

Extracts bundle contents and renders markdown report for offline inspection.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import tarfile
from datetime import datetime
from pathlib import Path
from typing import Any


def _decompress_bundle(bundle_path: Path) -> bytes:
    """Decompress .hledac-sprint bundle (tar.zst or tar)."""
    bundle_bytes = bundle_path.read_bytes()

    # Try zstd decompression
    try:
        import compression.zstd
        return compression.zstd.decompress(bundle_bytes)
    except ImportError:
        # Fallback: assume uncompressed tar
        return bundle_bytes
    except Exception:
        # Not zstd compressed, return as-is
        return bundle_bytes


def _extract_tar(tar_bytes: bytes, output_dir: Path) -> dict[str, Any]:
    """Extract tar archive to directory."""
    tar_buffer = io.BytesIO(tar_bytes)
    extracted_files = {}

    with tarfile.open(fileobj=tar_buffer, mode="r") as tar:
        for member in tar.getmembers():
            if member.isfile():
                file_obj = tar.extractfile(member)
                if file_obj:
                    file_bytes = file_obj.read()
                    output_path = output_dir / member.name
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    output_path.write_bytes(file_bytes)
                    extracted_files[member.name] = {
                        "size": len(file_bytes),
                        "path": str(output_path),
                    }

    return extracted_files


def _render_markdown_report(report_data: dict[str, Any]) -> str:
    """Render JSON report as markdown."""
    lines = []
    lines.append("# Sprint Report")
    lines.append("")

    # Executive summary
    if "summary" in report_data:
        lines.append("## Executive Summary")
        lines.append("")
        summary = report_data["summary"]
        if isinstance(summary, str):
            lines.append(summary)
        elif isinstance(summary, dict):
            for key, value in summary.items():
                lines.append(f"- **{key}**: {value}")
        lines.append("")

    # Metrics
    if "metrics" in report_data:
        lines.append("## Metrics")
        lines.append("")
        metrics = report_data["metrics"]
        if isinstance(metrics, dict):
            for key, value in metrics.items():
                lines.append(f"- **{key}**: {value}")
        lines.append("")

    # Findings
    if "findings" in report_data:
        lines.append("## Findings")
        lines.append("")
        findings = report_data["findings"]
        if isinstance(findings, list):
            for i, finding in enumerate(findings[:20], 1):
                if isinstance(finding, dict):
                    title = finding.get("title", "Untitled")
                    lines.append(f"{i}. **{title}**")
                    if "description" in finding:
                        lines.append(f"   {finding['description'][:200]}")
        lines.append("")

    # Product value summary
    if "product_value_summary" in report_data:
        lines.append("## Product Value Summary")
        lines.append("")
        pvs = report_data["product_value_summary"]
        if isinstance(pvs, dict):
            for key, value in pvs.items():
                lines.append(f"- **{key}**: {value}")
        lines.append("")

    # Capability synthesis
    if "capability_synthesis" in report_data:
        lines.append("## Capability Synthesis")
        lines.append("")
        synthesis = report_data["capability_synthesis"]
        if isinstance(synthesis, str):
            lines.append(synthesis)
        elif isinstance(synthesis, dict):
            for key, value in synthesis.items():
                lines.append(f"### {key}")
                lines.append(str(value))
                lines.append("")

    return "\n".join(lines)


def view_bundle(bundle_path: Path, extract_dir: Path | None = None) -> dict[str, Any]:
    """
    View .hledac-sprint bundle contents.

    Args:
        bundle_path: Path to .hledac-sprint bundle
        extract_dir: Optional directory to extract files (default: temp dir)

    Returns:
        Dict with bundle contents and metadata
    """
    import tempfile

    if not bundle_path.exists():
        raise FileNotFoundError(f"Bundle not found: {bundle_path}")

    result = {
        "bundle_path": str(bundle_path),
        "bundle_size": bundle_path.stat().st_size,
        "files": {},
        "metadata": {},
        "markdown_report": None,
    }

    # Decompress bundle
    tar_bytes = _decompress_bundle(bundle_path)

    # Extract to temp dir or specified dir
    if extract_dir is None:
        extract_dir = Path(tempfile.mkdtemp(prefix="hledac-sprint-"))
    else:
        extract_dir.mkdir(parents=True, exist_ok=True)

    # Extract tar
    result["files"] = _extract_tar(tar_bytes, extract_dir)

    # Read metadata
    metadata_path = extract_dir / "metadata.json"
    if metadata_path.exists():
        result["metadata"] = json.loads(metadata_path.read_text())

    # Read and render report
    report_path = extract_dir / "report.json"
    if report_path.exists():
        try:
            report_data = json.loads(report_path.read_text())
            result["markdown_report"] = _render_markdown_report(report_data)

            # Write markdown report
            md_path = extract_dir / "report.md"
            md_path.write_text(result["markdown_report"])
            result["files"]["report.md"] = {
                "size": len(result["markdown_report"]),
                "path": str(md_path),
            }
        except Exception as e:
            result["error"] = f"Failed to render report: {e}"

    # Read manifest
    manifest_path = extract_dir / "manifest.sha256"
    if manifest_path.exists():
        result["manifest"] = manifest_path.read_text()

    result["extract_dir"] = str(extract_dir)

    return result


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="View .hledac-sprint bundle contents",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m hledac.universal.export.sprint_viewer ~/.hledac/bundles/sprint-123.hledac-sprint
  python -m hledac.universal.export.sprint_viewer bundle.hledac-sprint --extract ./output
        """,
    )
    parser.add_argument("bundle", type=Path, help="Path to .hledac-sprint bundle")
    parser.add_argument(
        "--extract",
        type=Path,
        metavar="DIR",
        help="Extract bundle to directory (default: temp dir)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON",
    )

    args = parser.parse_args()

    try:
        result = view_bundle(args.bundle, args.extract)

        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Bundle: {result['bundle_path']}")
            print(f"Size: {result['bundle_size']:,} bytes")
            print(f"Extracted to: {result['extract_dir']}")
            print()

            if result.get("metadata"):
                print("Metadata:")
                for key, value in result["metadata"].items():
                    print(f"  {key}: {value}")
                print()

            print("Files:")
            for filename, info in result["files"].items():
                print(f"  {filename}: {info['size']:,} bytes → {info['path']}")
            print()

            if result.get("markdown_report"):
                print("=" * 80)
                print(result["markdown_report"])

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()


__all__ = ["view_bundle"]
