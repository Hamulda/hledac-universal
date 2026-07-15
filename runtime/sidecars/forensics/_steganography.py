"""
runtime/sidecars/forensics/_steganography.py — F-ISSUE-005: SteganographySidecarAdapter
"""
from runtime.sidecar_protocol import SidecarRegistry
from runtime.sidecars._base import SchedulerBackedSidecarAdapter


@SidecarRegistry.register("steganography")
class SteganographySidecarAdapter(SchedulerBackedSidecarAdapter):
    """F3FORENSICS: Steganography detection on image artifacts."""

    sidecar_id: str = "steganography"
    env_gate: str = "HLEDAC_ENABLE_STEGANOGRAPHY"
    ram_budget_mb: int = 100
    priority: int = 2
    scheduler_method_name: str = "_run_steganography_sidecar"
