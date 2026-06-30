"""
Phase 3: Flag presets — named bundles of HLEDAC_ENABLE_* values.

Five built-in profiles derived from
``docs/flag_analysis/FLAGS_TAXONOMY_AND_VALIDATION.md`` §5.1:

    MINIMAL  — CI / unit tests, no network, no LLM
    OSINT    — default operator profile, public OSINT APIs
    RECON    — darksurface + stealth, no LLM
    RESEARCH — LLM + graph + research APIs, no darksurface
    FULL     — all flags enabled, M1 unsafe (dev workstation only)

Each preset is a ``dict[str, str]`` mapping flag env-var name to
``"0"``/``"1"``. ``apply_preset(name, *, overwrite=False)`` writes
those values into ``os.environ`` without overwriting flags that the
operator has already set on the command line (preset is the default,
explicit env is the override — invariant from the sprint spec).

Source: docs/flag_analysis/FLAGS_TAXONOMY_AND_VALIDATION.md §5.1.
"""


import os
from typing import Final

# ---------------------------------------------------------------------------
# Preset definitions (Phase 3 spec)
# ---------------------------------------------------------------------------

MINIMAL: Final[dict[str, str]] = {
    # CI / unit tests — no network, no LLM, no sidecars.
}

OSINT: Final[dict[str, str]] = {
    # Default operator profile — public OSINT APIs only.
    "HLEDAC_ENABLE_TI_FEEDS": "1",
    "HLEDAC_ENABLE_IMAGE_OSINT": "1",
    "HLEDAC_ENABLE_STEGANOGRAPHY": "1",
    "HLEDAC_ENABLE_LEAKSENTINEL": "1",
    "HLEDAC_ENABLE_CENSYS": "1",
    "HLEDAC_ENABLE_SHODAN": "1",
    "HLEDAC_ENABLE_GREYNOISE": "1",
    "HLEDAC_ENABLE_COMMONCRAWL": "1",
    "HLEDAC_ENABLE_PROVIDERLESS_DISCOVERY": "1",
    "HLEDAC_ENABLE_TEMPORAL_STORE": "1",
}

RECON: Final[dict[str, str]] = {
    # Darksurface + stealth, no LLM.
    "HLEDAC_ENABLE_DARK_PIVOTS": "1",
    "HLEDAC_ENABLE_TOR": "1",
    "HLEDAC_ENABLE_I2P": "1",
    "HLEDAC_ENABLE_NYM": "1",
    "HLEDAC_ENABLE_IPFS": "1",
    "HLEDAC_ENABLE_DHT": "1",
    "HLEDAC_ENABLE_FEDIVERSE": "1",
    "HLEDAC_ENABLE_GOPHER": "1",
    "HLEDAC_ENABLE_ALT_PROTOCOLS": "1",
    "HLEDAC_ENABLE_STEALTH_LAYER": "1",
    "HLEDAC_ENABLE_NODRIVER": "1",
    "HLEDAC_ENABLE_ZERO_ATTRIBUTION": "1",
    "HLEDAC_ENABLE_PRIVACY_LAYER": "1",
    "HLEDAC_ENABLE_BGP": "1",
    "HLEDAC_ENABLE_BGP_PDNS": "1",
    "HLEDAC_ENABLE_BANNER_GRAB": "1",
}

RESEARCH: Final[dict[str, str]] = {
    # LLM + graph + research APIs, no darksurface.
    "HLEDAC_ENABLE_LLM": "1",
    "HLEDAC_ENABLE_DSPY": "1",
    "HLEDAC_ENABLE_HYPOTHESIS": "1",
    "HLEDAC_ENABLE_HERMES_SYNTHESIS": "1",
    "HLEDAC_ENABLE_GRAPH_RAG": "1",
    "HLEDAC_ENABLE_GRAPH_ANALYSIS": "1",
    "HLEDAC_ENABLE_GRAPH_PATHS": "1",
    "HLEDAC_ENABLE_CONTENT_LAYER": "1",
    "HLEDAC_ENABLE_EVIDENCE_ANALYZER": "1",
    "HLEDAC_ENABLE_ACADEMIC": "1",
    "HLEDAC_ENABLE_RESEARCH_LAYER": "1",
    "HLEDAC_ENABLE_DIGITAL_GHOST": "1",
    "HLEDAC_ENABLE_DEEP_RESEARCH": "1",
}

# FULL is built dynamically from FLAG_REGISTRY at import-time of this
# module so it stays in sync with newly registered flags. The dict
# has to be materialised eagerly because consumers (CLI --preset)
# expect a concrete dict, not a generator.
def _build_full() -> dict[str, str]:
    # Local import to avoid hard dependency on registry import order
    # (utils.flag_presets is independent of utils.flag_registry).
    from .flag_registry import FLAG_REGISTRY
    return dict.fromkeys(FLAG_REGISTRY, "1")


FULL: Final[dict[str, str]] = _build_full()


PRESETS: Final[dict[str, dict[str, str]]] = {
    "minimal": MINIMAL,
    "osint": OSINT,
    "recon": RECON,
    "research": RESEARCH,
    "full": FULL,
}


# ---------------------------------------------------------------------------
# Preset application — never overwrites explicit env vars
# ---------------------------------------------------------------------------

def apply_preset(
    name: str,
    *,
    overwrite: bool = False,
) -> dict[str, str]:
    """Apply preset ``name`` to ``os.environ``.

    Args:
        name: Preset key (one of ``MINIMAL``/``OSINT``/``RECON``/
            ``RESEARCH``/``FULL``).
        overwrite: If ``False`` (default), flags already set in
            ``os.environ`` are kept as-is — preset values fill in
            only the unset keys (explicit env = override invariant).
            If ``True``, every preset entry is written, replacing
            any existing value.

    Returns:
        The dict that was actually written (preset subset whose
        keys were applied). Empty dict if preset name is unknown.

    Raises:
        KeyError: if ``name`` is not in :data:`PRESETS`.
    """
    if name not in PRESETS:
        raise KeyError(
            f"Unknown preset {name!r}. Available: {sorted(PRESETS)}"
        )
    preset = PRESETS[name]
    applied: dict[str, str] = {}
    for flag, value in preset.items():
        if not overwrite and os.environ.get(flag) is not None:
            continue
        os.environ[flag] = value
        applied[flag] = value
    return applied


def estimate_preset_ram_mb(name: str) -> int:
    """Estimate compound M1 RAM cost of preset ``name``.

    Uses :data:`utils.flag_registry.FLAG_REGISTRY` for ``min_ram_mb``
    per flag. Unknown flags contribute 0. Used by ``--list-presets``
    CLI and by Phase 3 RAM budget check.
    """
    if name not in PRESETS:
        return 0
    try:
        from .flag_registry import FLAG_REGISTRY
    except ImportError:
        return 0
    total = 0
    for flag in PRESETS[name]:
        spec = FLAG_REGISTRY.get(flag)
        if spec is not None:
            total += spec.min_ram_mb
    return total


def list_presets_table() -> str:
    """Render a markdown-ish text table of presets with RAM estimate.

    Used by ``--list-presets`` CLI handler.
    """
    from .flag_registry import _RAM_FATAL_MB, _RAM_WARN_MB

    rows: list[tuple[str, str, str, str]] = []
    for name in ("minimal", "osint", "recon", "research", "full"):
        ram = estimate_preset_ram_mb(name)
        nflags = len(PRESETS.get(name, {}))
        if ram > _RAM_FATAL_MB:
            status = "M1 UNSAFE"
        elif ram > _RAM_WARN_MB:
            status = "warn"
        else:
            status = "M1 safe"
        rows.append((name, str(nflags), f"{ram}MB", status))

    header = ("PRESET", "FLAGS", "RAM", "STATUS")
    widths = [max(len(r[i]) for r in rows + [header]) for i in range(4)]
    line = "  ".join(h.ljust(w) for h, w in zip(header, widths, strict=False))
    sep = "  ".join("-" * w for w in widths)
    body = "\n".join(
        "  ".join(c.ljust(w) for c, w in zip(row, widths, strict=False)) for row in rows
    )
    return f"{line}\n{sep}\n{body}"


__all__ = [
    "MINIMAL",
    "OSINT",
    "RECON",
    "RESEARCH",
    "FULL",
    "PRESETS",
    "apply_preset",
    "estimate_preset_ram_mb",
    "list_presets_table",
]
