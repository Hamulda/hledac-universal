# NON_CANONICAL: Secondary facade for legacy/autonomous_orchestrator.py
# See autonomous_orchestrator.py (root) and LEGACY_ORCHESTRATOR_DETACH_AUDIT.md
# Do not add new functionality here.

"""
Orchestrator Module — SECONDARY THIN FACADE (Sprint F181A)
=========================================================

.. role::
    SECONDARY_THIN_FACADE: Thin re-export layer, NOT canonical owner.
    This module organizes backward-compat exports from the facade chain.

.. canonical_owner::
    legacy/autonomous_orchestrator.py — holds all real implementation (~31k lines)

.. authority_chain::
    orchestrator/__init__.py (SECONDARY FACADE)
        → autonomous_orchestrator.py (ROOT RE-EXPORT FACADE, NON_CANONICAL_FACADE)
            → legacy/autonomous_orchestrator.py (IMPLEMENTATION TRUTH)

.. what_this_is_not::
    - NOT production entrypoint
    - NOT canonical sprint owner
    - NOT implementation authority
    - NOT recommended import path for new code

.. migration_blockers::
    - smoke_runner.py imports from here
    - test probes import FullyAutonomousOrchestrator from here
    - orchestrator/research_manager.py and security_manager.py are also re-exports

.. action_required::
    New code should import directly from legacy/autonomous_orchestrator.py
    or from specific submodules (runtime/, brain/, etc.) as appropriate.
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

# Sprint F181A: Explicit containment seal — SECONDARY FACADE only.
# This module does NOT hold canonical implementation.
# Production consumers should import from legacy/autonomous_orchestrator.py directly.
