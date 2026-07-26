"""
Deprecated: use ``brain.unified_research_bridge`` directly.

Moved to brain/unified_research_bridge.py (F350M-R A-01).
This stub exists only for backward compatibility during migration.
"""
import warnings

__all__ = ["UnifiedAIOrchestrator"]

warnings.warn(
    "compat.core_unified_ai_orchestrator is deprecated. Use brain.unified_research_bridge directly. "
    "This shim will be removed in a future sprint.",
    DeprecationWarning,
    stacklevel=2,
)

from brain.unified_research_bridge import UnifiedAIOrchestrator
