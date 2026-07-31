"""
tools/preserved_logic — preserved external integrations.

This package contains integration stubs for features that were removed or
migrated from the codebase. All imports are fail-safe: they either return
a functional stub or raise ImportError so callers can handle gracefully.

Stub modules:
- fast_filter: URL filtering (FastFilter)
- fast_lang: Language detection (LanguageDetector)
- engine_core.data_validator: Data validation (DataValidator)
- content_cleaner: HTML cleaning (ContentCleaner)
- monitoring.diagnostics_engine: System diagnostics (DiagnosticsEngine)
"""
