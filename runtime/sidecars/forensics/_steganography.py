"""
runtime/sidecars/forensics/_steganography.py — F-ISSUE-005: SteganographySidecarAdapter
================================================================================

ARCHITECTURE NOTE (FIX-5):
  This adapter is NOT registered in SidecarRegistry to avoid dual-path execution.
  It is executed exclusively via SidecarOrchestrator._run_steganography_sidecar()
  (Branch D in run_advisory_runner()).

  FIX-5: Removed @SidecarRegistry.register() decorator that caused dual execution.
  FIX-5: Added protocol = "" for explicit clearnet classification.

CAPABILITY CHECK:
  - Protocol: "" (empty = clearnet/forensics, no transport capability check)
  - Always runs if lane is enabled (HLEDAC_ENABLE_STEGANOGRAPHY=1)

NOTE:
  Steganography detection analyzes image files for hidden data using
  statistical analysis, chi-square tests, and LSB manipulation detection.
"""
from hledac.universal.runtime.sidecars._darknet_base import DarknetSidecarAdapter


# FIX-5: Inherit from DarknetSidecarAdapter for proper capability handling
# but set protocol="" to indicate clearnet (no transport dependency)
class SteganographySidecarAdapter(DarknetSidecarAdapter):
    """F3FORENSICS: Steganography detection on image artifacts.

    FIX-5: Now uses DarknetSidecarAdapter with explicit protocol="" for
    clearnet classification. Not registered in SidecarRegistry - executed
    via orchestrator Branch D only.
    """

    sidecar_id: str = "steganography"
    env_gate: str = "HLEDAC_ENABLE_STEGANOGRAPHY"
    ram_budget_mb: int = 100
    priority: int = 2
    scheduler_method_name: str = "_run_steganography_sidecar"
    protocol: str = ""  # FIX-5: Empty = clearnet, no transport capability check
    capability_check_enabled: bool = False  # FIX-5: No transport dependency
