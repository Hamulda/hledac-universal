from __future__ import annotations

# advanced_web — browser automation within universal
from .automation_orchestrator import AutomationOrchestrator
from .stealth_browser import StealthBrowser
from .structured_extractor import (
    ExtractedEntity,
    ExtractedRelation,
    StructuredExtraction,
    StructuredExtractor,
)

__all__ = [
    "StealthBrowser",
    "AutomationOrchestrator",
    "StructuredExtractor",
    "StructuredExtraction",
    "ExtractedEntity",
    "ExtractedRelation",
]
