"""
Orchestrator Module - Modular organization for autonomous orchestrator components
===============================================================================

CONTAINMENT METADATA (Sprint F13)
=================================
role: SECONDARY_THIN_FACADE
    Tento modul je THIN SECONDARY RE-EXPORT layer. Není canonical owner implementace.
    Chová se jako organizční vrstva pro backward compatibility.

chain_of_authority:
    orchestrator/__init__.py (SECONDARY FACADE)
        → autonomous_orchestrator.py (ROOT RE-EXPORT FACADE, NON_CANONICAL_FACADE)
            → legacy/autonomous_orchestrator.py (IMPLEMENTATION TRUTH, 13k+ lines)

NOT a refactor - behavior remains unchanged.

Wording note: "implementation remains in autonomous_orchestrator.py" je zavádějící.
autonomous_orchestrator.py sám je re-export facade, ne implementation owner.
Canonical implementation: legacy/autonomous_orchestrator.py
"""

# Re-export the main orchestrator class for backward compatibility
# Implementation truth: legacy/autonomous_orchestrator.py (via root facade chain)
from ..autonomous_orchestrator import FullyAutonomousOrchestrator

# Re-export extracted managers (behavior identical to original)
from .research_manager import _ResearchManager
from .security_manager import _SecurityManager

__all__ = [
    "FullyAutonomousOrchestrator",
    "_ResearchManager",
    "_SecurityManager",
]

# Sprint F13: Explicit containment seal
# This module is a SECONDARY FACADE only.
# It does NOT hold canonical implementation.
# Production consumers should import from legacy/autonomous_orchestrator.py directly
# once migration blockers (smoke_runner.py, research_manager, security_manager) are resolved.
