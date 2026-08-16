# export/ — Backward-compat re-exports from report/
# Issue 12.1: export/ redirects to report/ package
# Legacy callers import from export/* — these re-exports maintain compat
# Re-export canonical classes from report/ for backward compat
from hledac.universal.report.engine import ReportEngine, get_report_engine, ReportOutput
from hledac.universal.report.renderers import (
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

# Re-export sprint export functions (moved from formatters.py to break circular import)
from hledac.universal.export.sprint_exporter import (
    export_partial_sprint,
    export_sprint,
    )

# ISSUE [META]-009: WASMDashboardBuilder — standalone investigator dashboard
from hledac.universal.export.dashboard_builder import (
    WASMDashboardBuilder,
    build_wasm_dashboard,
    )

# ISSUE [APEX]-1010: Sprint bundle format
from hledac.universal.export.sprint_bundler import (
    bundle_sprint,
    bundle_and_index_sprint,
    verify_bundle,
    BUNDLE_FORMAT_VERSION,
    )
from hledac.universal.export.sprint_viewer import (
    view_bundle,
    )
from _core import aclose

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
    # Sprint bundle format [APEX]-1010
    "bundle_sprint",
    "bundle_and_index_sprint",
    "verify_bundle",
    "BUNDLE_FORMAT_VERSION",
    "view_bundle",
    # [META]-009
    "WASMDashboardBuilder",
    "build_wasm_dashboard",
]
