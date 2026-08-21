"""DEPRECATED shim — original `hledac.services.content_expander` namespace was retired.

The canonical content expansion surface lives in `utils/hydration_extractor.py`
and `pipeline/live_public_pipeline.py`. This file remains as a fail-soft re-export
shell so any stale `from hledac.universal.utils.content_expander import …`
imports keep working at runtime; type checker sees an empty module.
"""

__all__: list[str] = []
