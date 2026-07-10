

# report/ — Unified Reporting Pipeline
# Issue 12.1: Consolidated export package for {json, md, html, svg}
#
# Design principles:
# - msgspec.json.encode for JSON (faster than orjson for msgspec.Struct types)
# - jinja2 for Markdown templates (replaces string concatenation)
# - graphviz system binary for SVG (subprocess, not Python bindings)
# - weasyprint for PDF (cross-platform, no extra browser binary)
# - Streaming writes for M1 8GB disk bottleneck
#
# Architecture:
#   ReportEngine (entry point)
#   ├── JSONRenderer    → msgspec.json.encode
#   ├── MarkdownRenderer → jinja2 templates
#   ├── HTMLRenderer    → Markdown → HTML conversion
#   ├── SVGRenderer     → graphviz dot -Tsvg subprocess
#   └── PDFRenderer     → weasyprint HTML → PDF
#
# Export compatibility: export/ re-exports from report/ for backward compat

from report.engine import ReportEngine, get_report_engine

__all__ = [
    "ReportEngine",
    "get_report_engine",
]
