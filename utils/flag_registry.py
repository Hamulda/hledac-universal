"""
F11 + Phase 2: Declarative FlagSpec registry.

Single source of truth for HLEDAC_* feature flag metadata (group,
implications, mutual-exclusion, daemon requirements, RAM budget).
Backed by ``FLAG_REGISTRY`` dict that callers can query for
documentation, validation (Phase 3) and discovery (Phase 4).

Design rules (GHOST_INVARIANTS):
- ``utils/flag_registry.py`` MUST NOT import any hledac module.
  Importing ``hledac.*`` from a low-level utility creates circular
  import risk during package init and is forbidden.
- Stdlib only (``dataclasses``, ``os``, ``typing``).
- Frozen dataclass — once a spec is registered it cannot be mutated.
- Fail-soft: helpers never raise on missing flag; they return
  ``False`` (off) for unknown names.

Source: ``docs/flag_analysis/FLAGS_TAXONOMY_AND_VALIDATION.md`` —
Section 3.1 (implication rules), 3.2 (mutual exclusion),
3.3 (resource gates), 2.1 (8-group taxonomy).
"""
import os
from dataclasses import dataclass, field, replace
from typing import Literal
FlagGroup = Literal['network', 'brain', 'storage', 'dark_surface', 'intelligence_apis', 'forensics', 'stealth', 'system']
VALID_GROUPS: frozenset[str] = frozenset({'network', 'brain', 'storage', 'dark_surface', 'intelligence_apis', 'forensics', 'stealth', 'system'})

@dataclass(frozen=True, slots=True)
class FlagSpec:
    """Canonical specification of a single HLEDAC_* feature flag.

    Attributes:
        name: Environment variable name, e.g. ``"HLEDAC_ENABLE_TOR"``.
        group: One of the 8 taxonomy groups. Validated at registration
            time via :data:`VALID_GROUPS`.
        default: Default env value as a string. ``"0"`` (off) unless
            a flag is shipped on by default (none in current taxonomy).
        implies: Other flag names that MUST be enabled when this flag
            is on. Tuples are one-directional — register the reverse
            in the depended-upon flag's spec if the relationship is
            symmetric.
        conflicts_with: Other flag names that MUST NOT be enabled
            alongside this flag. Stored symmetrically in registry —
            if A conflicts with B, B's spec also lists A.
        requires_daemon: Optional daemon name that must be running
            (``"tor"``, ``"i2p"``, ``"nym"``). Used by Phase 3
            validation; not enforced here.
        min_ram_mb: Minimum RSS headroom (MiB) required on M1 8GB UMA.
            Phase 3 budget gate uses compound sum of active flags.
        description: Human-readable doc string. Single line.
    """
    name: str
    group: FlagGroup
    default: str = '0'
    implies: tuple[str, ...] = field(default_factory=tuple)
    conflicts_with: tuple[str, ...] = field(default_factory=tuple)
    requires_daemon: str | None = None
    min_ram_mb: int = 0
    description: str = ''
FLAG_REGISTRY: dict[str, FlagSpec] = {}

class FlagRegistryError(ValueError):
    """Raised by :func:`register` on invalid group or duplicate name."""

def register(spec: FlagSpec) -> FlagSpec:
    """Register a :class:`FlagSpec` in the global registry.

    Args:
        spec: Frozen FlagSpec instance.

    Returns:
        The same ``spec`` (unchanged), so calls can be chained or
        used as expressions.

    Raises:
        FlagRegistryError: If ``spec.group`` is not in
            :data:`VALID_GROUPS` or ``spec.name`` is already
            registered.
    """
    if spec.group not in VALID_GROUPS:
        raise FlagRegistryError(f'FlagSpec {spec.name!r}: invalid group {spec.group!r}; expected one of {sorted(VALID_GROUPS)}')
    if spec.name in FLAG_REGISTRY:
        existing = FLAG_REGISTRY[spec.name]
        raise FlagRegistryError(f'FlagSpec {spec.name!r} already registered (group={existing.group!r}); duplicate registration')
    FLAG_REGISTRY[spec.name] = spec
    return spec

def _register_symmetric_conflict(a: FlagSpec, b: FlagSpec) -> tuple[FlagSpec, FlagSpec]:
    """Register two specs with bidirectional ``conflicts_with``.

    Used for mutual-exclusion pairs (HEAVY_BROWSER ↔ NODRIVER,
    CURL_CFFI ↔ HTTPX_H2, FEDERATED_HYBRID ↔ FEDERATED_P2P,
    SYNTHESIS ↔ HERMES_SYNTHESIS). The original dataclasses are
    frozen, so we rebuild them via :func:`dataclasses.replace`.

    Both specs are written to :data:`FLAG_REGISTRY`; existing
    ``conflicts_with`` tuples are preserved and extended.
    """
    a = replace(a, conflicts_with=tuple(dict.fromkeys((*a.conflicts_with, b.name))))
    b = replace(b, conflicts_with=tuple(dict.fromkeys((*b.conflicts_with, a.name))))
    FLAG_REGISTRY[a.name] = a
    FLAG_REGISTRY[b.name] = b
    return (a, b)

def list_flags(group: str | None=None) -> list[FlagSpec]:
    """Return all registered flag specs, optionally filtered by group.

    Args:
        group: Group name (see :data:`VALID_GROUPS`). ``None`` returns
            every spec in registration order.
    """
    if group is None:
        return list(FLAG_REGISTRY.values())
    return [s for s in FLAG_REGISTRY.values() if s.group == group]

def get_spec(name: str) -> FlagSpec | None:
    """Look up a flag spec by env-var name. ``None`` if unknown."""
    return FLAG_REGISTRY.get(name)
register(FlagSpec(name='HLEDAC_ENABLE_TOR', group='network', requires_daemon='tor', min_ram_mb=50, description='Tor SOCKS5 transport (requires running tor daemon).'))
register(FlagSpec(name='HLEDAC_ENABLE_I2P', group='network', requires_daemon='i2p', min_ram_mb=50, description='I2P SAM bridge transport (requires i2pd daemon).'))
register(FlagSpec(name='HLEDAC_ENABLE_NYM', group='network', requires_daemon='nym', min_ram_mb=80, description='Nym mixnet transport (requires nym-client daemon).'))
register(FlagSpec(name='HLEDAC_ENABLE_IPFS', group='network', min_ram_mb=30, description='IPFS gateway content fetch (sprint sidecar).'))
_register_symmetric_conflict(FlagSpec(name='HLEDAC_ENABLE_CURL_CFFI', group='network', min_ram_mb=50, description='curl_cffi HTTP transport (JA3 fingerprinting).'), FlagSpec(name='HLEDAC_ENABLE_HTTPX_H2', group='network', min_ram_mb=10, description='httpx HTTP/2 backend (conflicts with curl_cffi).'))
_register_symmetric_conflict(FlagSpec(name='HLEDAC_ENABLE_NODRIVER', group='network', min_ram_mb=400, description='nodriver headless browser (Chrome binary required).'), FlagSpec(name='HLEDAC_ENABLE_HEAVY_BROWSER', group='network', min_ram_mb=1500, description='Playwright (M1 RAM critical, 1.5GB+ headroom).'))
register(FlagSpec(name='HLEDAC_ENABLE_LLM', group='brain', min_ram_mb=2200, description='MLX/Hermes3 4bit LLM inference (M1 2.2GB RSS).'))
register(FlagSpec(name='HLEDAC_ENABLE_DSPY', group='brain', implies=('HLEDAC_ENABLE_LLM',), min_ram_mb=200, description='DSPy compiled hypothesis programs (requires LLM).'))
register(FlagSpec(name='HLEDAC_ENABLE_HYPOTHESIS', group='brain', implies=('HLEDAC_ENABLE_LLM',), min_ram_mb=200, description='Hypothesis-driven pivot planner (requires LLM).'))
register(FlagSpec(name='HLEDAC_ENABLE_GRAPH_RAG', group='brain', implies=('HLEDAC_ENABLE_LLM', 'HLEDAC_ENABLE_GRAPH_ANALYSIS'), min_ram_mb=300, description='Graph RAG embeddings (requires LLM + graph analysis).'))
register(FlagSpec(name='HLEDAC_ENABLE_GRAPH_ANALYSIS', group='storage', min_ram_mb=200, description='Leiden community detection (DuckPGQ graph).'))
register(FlagSpec(name='HLEDAC_ENABLE_GRAPH_PATHS', group='storage', implies=('HLEDAC_ENABLE_GRAPH_ANALYSIS',), min_ram_mb=150, description='Quantum pathfinder (requires graph analysis).'))
register(FlagSpec(name='HLEDAC_HTTP_CACHE', group='storage', min_ram_mb=20, description='HTTP response cache (enabled by default in .env.example).'))
register(FlagSpec(name='HLEDAC_ENABLE_DARK_PIVOTS', group='dark_surface', min_ram_mb=80, description='Orchestrated Tor/I2P/IPFS pivots in hypothesis engine.'))
register(FlagSpec(name='HLEDAC_ENABLE_DHT', group='dark_surface', min_ram_mb=100, description='DHT discovery sidecar (used for dark surface content).'))
register(FlagSpec(name='HLEDAC_ENABLE_FEDERATED', group='dark_surface', min_ram_mb=150, description='Federated sidecar activation (gateway).'))
_register_symmetric_conflict(FlagSpec(name='HLEDAC_ENABLE_FEDERATED_HYBRID', group='dark_surface', implies=('HLEDAC_ENABLE_FEDERATED',), min_ram_mb=200, description='Federated hybrid (P2P + bridge; requires FEDERATED).'), FlagSpec(name='HLEDAC_ENABLE_FEDERATED_P2P', group='dark_surface', min_ram_mb=200, description='Federated pure P2P mode (conflicts with hybrid).'))
register(FlagSpec(name='HLEDAC_ENABLE_BGP', group='intelligence_apis', min_ram_mb=40, description='BGP enrichment sidecar (F234).'))
register(FlagSpec(name='HLEDAC_ENABLE_BGP_PDNS', group='intelligence_apis', implies=('HLEDAC_ENABLE_BGP',), min_ram_mb=60, description='Passive DNS via BGP (requires BGP parent).'))
register(FlagSpec(name='HLEDAC_ENABLE_ACADEMIC', group='intelligence_apis', min_ram_mb=80, description='Academic research lane (arXiv, PubMed).'))
register(FlagSpec(name='HLEDAC_ENABLE_LEAKSENTINEL', group='intelligence_apis', min_ram_mb=30, description='Paste/GitHub/breach signal scanning.'))
register(FlagSpec(name='HLEDAC_CONTENT_HASHER', group='system', min_ram_mb=10, description='Content hash registry (Rust extension).'))
register(FlagSpec(name='HLEDAC_ENABLE_STEALTH_LAYER', group='stealth', min_ram_mb=40, description='Stealth mode (UA rotation, jitter variance).'))
_register_symmetric_conflict(FlagSpec(name='HLEDAC_ENABLE_SYNTHESIS', group='system', min_ram_mb=0, description='DEPRECATED alias — use HLEDAC_ENABLE_HERMES_SYNTHESIS.'), FlagSpec(name='HLEDAC_ENABLE_HERMES_SYNTHESIS', group='brain', implies=('HLEDAC_ENABLE_LLM',), min_ram_mb=200, description='Hermes3 synthesis lane (preferred over SYNTHESIS).'))
register(FlagSpec(name='HLEDAC_BENCHMARK', group='system', min_ram_mb=0, description='Benchmark mode (short-circuit sprint loop for measurement).'))
register(FlagSpec(name='HLEDAC_OFFLINE', group='system', min_ram_mb=0, description='Offline mode (skip all network egress).'))
register(FlagSpec(name='HLEDAC_RL_SKIP_RAM_GATE', group='system', min_ram_mb=0, description='Bypass RL policy RAM gate (advisory only, do not crash on low RAM).'))
register(FlagSpec(name='HLEDAC_DISABLE_GC_FREEZE', group='system', min_ram_mb=0, description='Disable gc.freeze() / gc.unfreeze() cycles (M1 latency tests).'))
register(FlagSpec(name='HLEDAC_DISABLE_RL', group='system', min_ram_mb=0, description='Disable RL/SprintPolicyManager even when the module is importable.'))
register(FlagSpec(name='HLEDAC_TRACEMALLOC', group='system', min_ram_mb=50, description='Enable tracemalloc (debug allocator; ~50MB overhead).'))
register(FlagSpec(name='HLEDAC_ENABLE_BANNER_GRAB', group='network', min_ram_mb=0, description='TCP banner grab (enum sidecar; SYN/ACK probe).'))
register(FlagSpec(name='HLEDAC_ENABLE_COMMONCRAWL', group='network', min_ram_mb=50, description='CommonCrawl index search (CDX + WARC sampling).'))
register(FlagSpec(name='HLEDAC_ENABLE_GOPHER', group='network', min_ram_mb=0, description='Gopher protocol fetcher (RFC 1436; fallback for alt-protocol lane).'))
register(FlagSpec(name='HLEDAC_ENABLE_PROVIDERLESS_DISCOVERY', group='network', min_ram_mb=0, description='Providerless discovery cascade (DDG → Historical → Wayback).'))
register(FlagSpec(name='HLEDAC_HTTP3', group='network', min_ram_mb=0, description='HTTP/3 (QUIC) experimental transport.'))
register(FlagSpec(name='HLEDAC_IPFS_CLEARNET', group='network', min_ram_mb=0, description='IPFS clearnet gateway (public pinning/CDN, no local daemon).'))
register(FlagSpec(name='HLEDAC_DEEP_RESEARCH', group='brain', implies=('HLEDAC_ENABLE_LLM',), min_ram_mb=500, description='Deep research lane (multi-step LLM planning, requires LLM).'))
register(FlagSpec(name='HLEDAC_ENABLE_CONTENT_LAYER', group='brain', min_ram_mb=200, description='Content analysis layer (entity/relation extraction, not LLM).'))
register(FlagSpec(name='HLEDAC_ENABLE_LAYERS', group='brain', min_ram_mb=0, description='Security layer manager (composable validation pipeline).'))
register(FlagSpec(name='HLEDAC_ENABLE_RESEARCH_LAYER', group='brain', min_ram_mb=200, description='Research analysis layer (corpus aggregation + ranking).'))
register(FlagSpec(name='HLEDAC_ENABLE_TEMPORAL_STORE', group='storage', min_ram_mb=100, description='Temporal data store (time-series findings, versioned CT logs).'))
register(FlagSpec(name='HLEDAC_LANCEDB_QUANTIZE', group='storage', min_ram_mb=0, description='LanceDB IVF-PQ vector quantization (M1 8GB RAM friendly, opt-in).'))
register(FlagSpec(name='HLEDAC_LANCEDB_AUTO_TUNE', group='storage', implies=('HLEDAC_LANCEDB_QUANTIZE',), min_ram_mb=0, description='Adaptive IVF-PQ auto-tuning — measure recall@K, grow/shrink num_partitions (M1 8GB friendly, opt-in, sprint F264E).'))
register(FlagSpec(name='HLEDAC_LANCEDB_AUTO_TUNE_THRESHOLD', group='storage', min_ram_mb=0, description='Insert count threshold for auto-tune evaluation (default 5000, sprint F264E).'))
register(FlagSpec(name='HLEDAC_LANCEDB_AUTO_TUNE_COOLDOWN_S', group='storage', min_ram_mb=0, description='Cooldown in seconds between consecutive IVF-PQ auto-tune attempts (default 3600, sprint F264E).'))
register(FlagSpec(name='HLEDAC_ENABLE_CENSYS', group='intelligence_apis', min_ram_mb=0, description='Censys intelligence API (cert/host enrichment; needs API key).'))
register(FlagSpec(name='HLEDAC_ENABLE_SHODAN', group='intelligence_apis', min_ram_mb=0, description='Shodan intelligence API (host/port enrichment; needs API key).'))
register(FlagSpec(name='HLEDAC_ENABLE_TI_FEEDS', group='intelligence_apis', min_ram_mb=50, description='Threat intelligence feeds aggregator (RSS/TAXII/JSON).'))
register(FlagSpec(name='HLEDAC_ENABLE_DIGITAL_GHOST', group='stealth', min_ram_mb=0, description='Digital forensics ghost mode (anti-attribution transport).'))
register(FlagSpec(name='HLEDAC_ENABLE_PRIVACY_LAYER', group='stealth', min_ram_mb=0, description='Privacy policy enforcement (PII redaction, OP-leakage guard).'))
register(FlagSpec(name='HLEDAC_ENABLE_ZKP', group='stealth', min_ram_mb=0, description='Zero-knowledge proof envelope (claim provenance without content).'))
register(FlagSpec(name='HLEDAC_EXPERIMENTAL_NEURO_CRYPTO', group='stealth', min_ram_mb=0, description='Experimental neuro-cryptographic sidecar (research, not for prod).'))
register(FlagSpec(name='HLEDAC_ENABLE_STEGANOGRAPHY', group='forensics', min_ram_mb=0, description='Image steganography detection (LSB + transform-domain).'))
_TRUTHY: frozenset[str] = frozenset({'1', 'true', 'yes', 'on'})

def is_flag_active(name: str) -> bool:
    """Resolve a flag to ``True`` iff its env var is a truthy token.

    Mirror of :func:`utils.feature_flags._env_truthy` exposed at the
    registry level so callers that already import the registry don't
    need a second import. Never raises.
    """
    try:
        raw = os.environ.get(name, '0')
    except (AttributeError, TypeError):
        return False
    try:
        return raw.strip().lower() in _TRUTHY
    except (AttributeError, TypeError):
        return False

def is_enabled(name: str, default: str='0') -> bool:
    """Resolve a flag to bool using its :class:`FlagSpec.default` as fallback.

    Args:
        name: Env var name (e.g. ``"HLEDAC_ENABLE_TOR"``).
        default: Default token (``"0"``/``"1"``); used when the env
            var is absent. Truthy tokens: ``{"1","true","yes","on"}``.

    Mirrors :func:`is_flag_active` but allows the caller to pass a
    spec-level default token (which is a string in :class:`FlagSpec`).
    Never raises.
    """
    try:
        raw = os.environ.get(name, default)
    except (AttributeError, TypeError):
        return False
    try:
        return raw.strip().lower() in _TRUTHY
    except (AttributeError, TypeError):
        return False
_RAM_WARN_MB: int = 5500
_RAM_FATAL_MB: int = 7000

def validate_flag_combo() -> tuple[list[str], list[str]]:
    """Fail-fast combo validation for the current process env.

    Returns:
        ``(errors, warnings)`` — two independent lists.
        ``errors`` are hard conflicts (callers should exit 2).
        ``warnings`` are soft hints (log and proceed).

    Rules:
        - For each active flag, every entry in ``spec.implies`` that
          is itself a known flag AND is currently disabled emits a
          soft warning (implication not satisfied).
        - For each active flag, every entry in ``spec.conflicts_with``
          that is also active emits a hard error (mutual exclusion).
        - Sum of ``min_ram_mb`` for all active flags is checked
          against M1 thresholds (>7000MB → fatal, >5500MB → warn).

    Stdlib-only by design — must not import MLX / DuckDB / anything
    in :mod:`hledac` (utility is hot-loaded by :mod:`__main__` before
    runtime init). Never raises.
    """
    errors: list[str] = []
    warnings: list[str] = []
    try:
        active: set[str] = {name for name, spec in FLAG_REGISTRY.items() if is_enabled(name, spec.default)}
        for name in sorted(active):
            spec = FLAG_REGISTRY.get(name)
            if spec is None:
                continue
            for implied in spec.implies:
                if implied in FLAG_REGISTRY and (not is_enabled(implied, FLAG_REGISTRY[implied].default)):
                    warnings.append(f'{name} implies {implied} but it is disabled')
            for conflict in spec.conflicts_with:
                if conflict in active:
                    errors.append(f'CONFLICT: {name} ↔ {conflict} cannot both be active')
        total_ram = sum((FLAG_REGISTRY[f].min_ram_mb for f in active if f in FLAG_REGISTRY))
        if total_ram > _RAM_FATAL_MB:
            errors.append(f'FATAL: estimated RAM {total_ram}MB exceeds M1 8GB budget')
        elif total_ram > _RAM_WARN_MB:
            warnings.append(f'WARNING: estimated RAM {total_ram}MB approaching M1 limit')
    except (AttributeError, TypeError, KeyError) as exc:
        errors.append(f'validate_flag_combo internal error: {exc!r}')
    return (errors, warnings)
__all__ = ['FlagGroup', 'FlagSpec', 'FlagRegistryError', 'FLAG_REGISTRY', 'VALID_GROUPS', 'register', 'list_flags', 'get_spec', 'is_flag_active', 'is_enabled', 'validate_flag_combo']