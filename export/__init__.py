from __future__ import annotations

# export/ — Backward-compat re-exports from report/
# Issue 12.1: export/ redirects to report/ package
# Legacy callers import from export/* — these re-exports maintain compat
# Re-export canonical classes from report/ for backward compat
from report.engine import ReportEngine, get_report_engine, ReportOutput
from report.renderers import (
    JSONRenderer,
    MarkdownRenderer,
    HTMLRenderer,
    SVGRenderer,
    PDFRenderer,
)

# Keep existing legacy exports (unchanged — they still work)
from hledac.universal.export.export_manager import (
    ExportManager,
    get_export_manager,
)
from hledac.universal.export.jsonld_exporter import (
    normalize_export_input as normalize_export_input,
)
from hledac.universal.export.jsonld_exporter import (
    render_jsonld,
    render_jsonld_str,
    render_jsonld_to_path,
)
from hledac.universal.export.markdown_reporter import (
    normalize_report_input,
    render_diagnostic_markdown,
    render_diagnostic_markdown_to_path,
)
from hledac.universal.export.stix_exporter import (
    render_cti_stix_bundle_to_path,
    render_stix_bundle,
    render_stix_bundle_json,
    render_stix_bundle_to_path,
)
from hledac.universal.export.parquet_writer import (
    ParquetExporter,
    export_findings_parquet,
    export_parquet_to_path,
)

__all__ = [
    # New unified engine (Issue 12.1)
    "ReportEngine",
    "ReportOutput",
    "get_report_engine",
    # Renderers (Issue 12.1)
    "JSONRenderer",
    "MarkdownRenderer",
    "HTMLRenderer",
    "SVGRenderer",
    "PDFRenderer",
    # Legacy exports (unchanged)
    "normalize_export_input",
    "normalize_report_input",
    "render_diagnostic_markdown",
    "render_diagnostic_markdown_to_path",
    "render_jsonld",
    "render_jsonld_str",
    "render_jsonld_to_path",
    "render_stix_bundle",
    "render_stix_bundle_json",
    "render_stix_bundle_to_path",
    "render_cti_stix_bundle_to_path",
    "ExportManager",
    "get_export_manager",
    # Parquet zero-copy export (F320-EXT)
    "ParquetExporter",
    "export_findings_parquet",
    "export_parquet_to_path",
]
