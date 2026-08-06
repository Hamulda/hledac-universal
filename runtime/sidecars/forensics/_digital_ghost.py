"""
runtime/sidecars/forensics/_digital_ghost.py — F-ISSUE-005: DigitalGhostSidecarAdapter
"""
from hledac.universal.runtime.sidecar_protocol import SidecarRegistry

from hledac.universal.runtime.sidecars._base import SchedulerBackedSidecarAdapter


@SidecarRegistry.register("digital_ghost")
class DigitalGhostSidecarAdapter(SchedulerBackedSidecarAdapter):
    """F3FORENSICS: Digital ghost detection on file artifacts."""

    sidecar_id: str = "digital_ghost"
    env_gate: str = "HLEDAC_ENABLE_DIGITAL_GHOST"
    ram_budget_mb: int = 100
    priority: int = 2
    scheduler_method_name: str = "_run_digital_ghost_sidecar"
