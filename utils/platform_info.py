"""
Platform Information Helper
============================
Lightweight platform/dependency truth without heavy imports.
Provides actionable status for optional acceleration dependencies.

Designed to be:
- Importable without triggering MLX/torch heavy runtime
- Side-effect free
- Fast (<200ms for 1000 calls)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DepCategory(Enum):
    """Dependency category/status."""
    BASELINE_REQUIRED = "baseline_required"       # Required for core function
    OPTIONAL_AVAILABLE = "optional_available"     # Installed and available
    OPTIONAL_MISSING = "optional_missing"         # Not installed
    PLATFORM_GUARDED = "platform_guarded"         # Depends on platform (e.g. MPS)


@dataclass(frozen=True, slots=True)
class AccelerationStatus:
    """Status for a single acceleration dependency."""
    name: str
    available: bool
    category: DepCategory
    version: str | None
    install_hint: str | None
    platform_note: str | None


@dataclass(frozen=True, slots=True)
class PlatformReport:
    """Full platform acceleration report."""
    statuses: dict[str, AccelerationStatus]
    summary: str
    missing_optional: list[str]
    install_command: str


# Named fallback constant for non-MLX RAM estimation
# Conservative: 500MB default prediction when MLX unavailable
FALLBACK_RAM_ESTIMATE_MB: float = 500.0
FALLBACK_RAM_ESTIMATE_GB: float = 0.5


# ---------------------------------------------------------------------------
# Individual probe functions (lazy — import only when needed)
# ---------------------------------------------------------------------------

def _probe_mlx() -> AccelerationStatus:
    try:
        import mlx.core as mx
        return AccelerationStatus(
            name="mlx",
            available=True,
            category=DepCategory.OPTIONAL_AVAILABLE,
            version=getattr(mx, "__version__", "unknown"),
            install_hint=None,
            platform_note="Apple Silicon MLX accelerator",
        )
    except ImportError:
        return AccelerationStatus(
            name="mlx",
            available=False,
            category=DepCategory.OPTIONAL_MISSING,
            version=None,
            install_hint="pip install mlx",
            platform_note="Apple Silicon MLX (mlx community package)",
        )


def _probe_torch() -> AccelerationStatus:
    try:
        import torch
        mps_available = False
        mps_note = None
        try:
            if hasattr(torch.backends, "mps"):
                mps_available = torch.backends.mps.is_available()
                if not mps_available:
                    mps_note = "MPS available=False on this hardware"
        except Exception as e:
            mps_note = f"MPS probe failed: {e}"

        return AccelerationStatus(
            name="torch",
            available=True,
            category=DepCategory.OPTIONAL_AVAILABLE,
            version=torch.__version__,
            install_hint=None,
            platform_note=f"torch MPS available: {mps_available}" + (f" ({mps_note})" if mps_note else ""),
        )
    except ImportError:
        return AccelerationStatus(
            name="torch",
            available=False,
            category=DepCategory.OPTIONAL_MISSING,
            version=None,
            install_hint="pip install torch --index-url https://download.pytorch.org/whl/cpu",
            platform_note="CPU-only PyTorch; MPS (Metal) requires Apple Silicon",
        )


def _probe_torch_mps() -> AccelerationStatus:
    try:
        import torch
        if not hasattr(torch.backends, "mps"):
            return AccelerationStatus(
                name="torch_mps",
                available=False,
                category=DepCategory.PLATFORM_GUARDED,
                version=None,
                install_hint="torch is required for MPS",
                platform_note="torch.backends.mps not available",
            )
        available = torch.backends.mps.is_available()
        return AccelerationStatus(
            name="torch_mps",
            available=available,
            category=DepCategory.PLATFORM_GUARDED if not available else DepCategory.OPTIONAL_AVAILABLE,
            version=None,
            install_hint=None if available else "Requires Apple Silicon Mac",
            platform_note="Metal Performance Shaders for torch on Apple Silicon",
        )
    except ImportError:
        return AccelerationStatus(
            name="torch_mps",
            available=False,
            category=DepCategory.OPTIONAL_MISSING,
            version=None,
            install_hint="pip install torch",
            platform_note="torch required for MPS probe",
        )


def _probe_fast_langdetect() -> AccelerationStatus:
    try:
        import fast_langdetect
        return AccelerationStatus(
            name="fast_langdetect",
            available=True,
            category=DepCategory.OPTIONAL_AVAILABLE,
            version=getattr(fast_langdetect, "__version__", "unknown"),
            install_hint=None,
            platform_note="FTZ-format language detection (10x faster than langdetect)",
        )
    except ImportError:
        return AccelerationStatus(
            name="fast_langdetect",
            available=False,
            category=DepCategory.OPTIONAL_MISSING,
            version=None,
            install_hint="pip install fast-langdetect",
            platform_note="Language detection (optional acceleration)",
        )


def _probe_datasketch() -> AccelerationStatus:
    try:
        import datasketch
        ver = getattr(datasketch, "__version__", "unknown")
        return AccelerationStatus(
            name="datasketch",
            available=True,
            category=DepCategory.OPTIONAL_AVAILABLE,
            version=ver,
            install_hint=None,
            platform_note="MinHash LSH for near-duplicate detection",
        )
    except ImportError:
        return AccelerationStatus(
            name="datasketch",
            available=False,
            category=DepCategory.OPTIONAL_MISSING,
            version=None,
            install_hint="pip install datasketch",
            platform_note="MinHash LSH (optional for relationship discovery)",
        )


def _probe_rapidfuzz() -> AccelerationStatus:
    try:
        import rapidfuzz
        return AccelerationStatus(
            name="rapidfuzz",
            available=True,
            category=DepCategory.OPTIONAL_AVAILABLE,
            version=getattr(rapidfuzz, "__version__", "unknown"),
            install_hint=None,
            platform_note="C-based Levenshtein/Jaro-Winkler string matching",
        )
    except ImportError:
        return AccelerationStatus(
            name="rapidfuzz",
            available=False,
            category=DepCategory.OPTIONAL_MISSING,
            version=None,
            install_hint="pip install rapidfuzz",
            platform_note="Fuzzy string matching (optional for identity stitching)",
        )


# ---------------------------------------------------------------------------
# Main API
# ---------------------------------------------------------------------------

def get_optional_acceleration_status() -> PlatformReport:
    """
    Probe all optional acceleration dependencies.

    Returns a PlatformReport with per-package status and install hints.
    Lightweight — all probes are lazy (import on first use only).
    """
    statuses: dict[str, AccelerationStatus] = {}

    for probe_fn in (
        _probe_mlx,
        _probe_torch,
        _probe_torch_mps,
        _probe_fast_langdetect,
        _probe_datasketch,
        _probe_rapidfuzz,
    ):
        s = probe_fn()
        statuses[s.name] = s

    missing = [
        s.name for s in statuses.values()
        if s.category in (DepCategory.OPTIONAL_MISSING, DepCategory.PLATFORM_GUARDED) and s.install_hint
    ]

    if missing:
        cmd = f"pip install {' '.join(_get_install_hint(m) for m in missing if m in statuses)}"
    else:
        cmd = "# All optional accelerators installed"

    summary_parts = []
    if statuses.get("mlx", AccelerationStatus("", False, DepCategory.OPTIONAL_MISSING, None, None, None)).available:
        summary_parts.append("MLX")
    if statuses.get("torch_mps", AccelerationStatus("", False, DepCategory.PLATFORM_GUARDED, None, None, None)).available:
        summary_parts.append("torch.MPS")
    if missing:
        summary_parts.append(f"+{len(missing)} optional")
    summary = ", ".join(summary_parts) if summary_parts else "baseline only"

    return PlatformReport(
        statuses=statuses,
        summary=summary,
        missing_optional=missing,
        install_command=cmd,
    )


def _get_install_hint(name: str) -> str:
    """Get install hint for a dependency name."""
    hints = {
        "mlx": "mlx",
        "torch": "torch --index-url https://download.pytorch.org/whl/cpu",
        "torch_mps": "torch",
        "fast_langdetect": "fast-langdetect",
        "datasketch": "datasketch",
        "rapidfuzz": "rapidfuzz",
    }
    return hints.get(name, name)


def format_platform_summary() -> str:
    """Formatter-friendly one-line platform summary."""
    r = get_optional_acceleration_status()
    parts = [f"{k}:{v.available}" for k, v in r.statuses.items()]
    return f"[Platform] {' | '.join(parts)} | missing={r.missing_optional}"
