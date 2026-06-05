# advanced_web — browser automation within universal
from .stealth_browser import StealthBrowser
from .automation_orchestrator import AutomationOrchestrator
from .structured_extractor import (
    StructuredExtractor,
    StructuredExtraction,
    ExtractedEntity,
    ExtractedRelation,
)

__all__ = [
    "StealthBrowser",
    "AutomationOrchestrator",
    "StructuredExtractor",
    "StructuredExtraction",
    "ExtractedEntity",
    "ExtractedRelation",
]
