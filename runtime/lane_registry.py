"""
runtime/lane_registry.py — F350M-R: A5 Feature-Flag Sprawl Fix
================================================================



Single source of truth for lane enablement.
Replaces scattered os.environ.get("HLEDAC_ENABLE_X") checks for lanes/sidecars.

ARCHITECTURE:
  Three-layer model:
    Layer 1: CapabilityRegistry (core/capabilities.py) — optional deps (MLX, DuckDB, ...)
    Layer 2: LaneRegistry — which lanes/sidecars are enabled for this sprint
    Layer 3: RuntimeConfig (config/settings.py) — resource tuning (memory, cache, ...)

DESIGN RULES:
  - Frozen msgspec structs — lane profiles are set at sprint start
  - O(1) lane membership via frozenset — no env scanning in hot path
  - Fail-safe: is_enabled() returns False for unknown lanes
  - Emergency env override: HLEDAC_FORCE_LANE_<id>=1 bypasses profile check
  - Legacy env fallback: os.environ.get(env_gate) if profile not yet migrated

MIGRATION PATH:
  Before: os.environ.get("HLEDAC_ENABLE_TOR", "") in ("1", "true", "yes", "on")
  After:  LaneRegistry.is_enabled("tor")

GHOST_INVARIANTS:
  - Fail-safe: is_enabled() never raises, returns False for unknown lanes
  - Bounded: no unbounded iteration, no dynamic env scanning
  - No network I/O, no model/MLX load
  - Deterministic: same lane_id + profile always same result
"""

from __future__ import annotations

import logging
import os

from compat.msgspec_gc_compat import Struct
from hledac.universal.compat.msgspec_gc_compat import Struct

__all__ = [
    "LaneRegistry",
    "LaneSpec",
    "LANE_REGISTRY",
]


logger = logging.getLogger(__name__)


class LaneSpec(Struct, frozen=True):
    """
    Canonical specification for one lane / sidecar.

    Attributes:
        lane_id:       Unique identifier used in profile.lanes frozensets
        env_gate:     Legacy HLEDAC_ENABLE_X env var name
        ram_budget_mb: Maximum RSS this lane may use (for memory budgeting)
        priority:      Execution priority 1-10 (higher = runs first)
        description:   Human-readable description for CLI --help
    """

    lane_id: str
    env_gate: str  # legacy HLEDAC_ENABLE_X var, e.g. "HLEDAC_ENABLE_TOR"
    ram_budget_mb: int = 50
    priority: int = 5
    description: str = ""


# ── Lane Specs ─────────────────────────────────────────────────────────────────
# All registered lanes. Add new lanes HERE — one source of truth.

_LANE_SPECS: dict[str, LaneSpec] = {
    # ── Network / OSINT lanes ──────────────────────────────────────────────────
    "tor": LaneSpec(
        lane_id="tor",
        env_gate="HLEDAC_ENABLE_TOR",
        ram_budget_mb=80,
        priority=7,
        description="Tor transport lane",
    ),
    "i2p": LaneSpec(
        lane_id="i2p",
        env_gate="HLEDAC_ENABLE_I2P",
        ram_budget_mb=80,
        priority=7,
        description="I2P anonymous network lane",
    ),
    "nym": LaneSpec(
        lane_id="nym",
        env_gate="HLEDAC_ENABLE_NYM",
        ram_budget_mb=100,
        priority=5,
        description="Nym mixnet transport lane",
    ),
    "dht": LaneSpec(
        lane_id="dht",
        env_gate="HLEDAC_ENABLE_DHT",
        ram_budget_mb=100,
        priority=4,
        description="DHT peer discovery lane",
    ),
    "ipfs": LaneSpec(
        lane_id="ipfs",
        env_gate="HLEDAC_ENABLE_IPFS",
        ram_budget_mb=60,
        priority=5,
        description="IPFS content discovery lane",
    ),
    "federated": LaneSpec(
        lane_id="federated",
        env_gate="HLEDAC_ENABLE_FEDERATED",
        ram_budget_mb=80,
        priority=6,
        description="Federated P2P discovery lane",
    ),
    # ── Intelligence API lanes ─────────────────────────────────────────────────
    "bgp": LaneSpec(
        lane_id="bgp",
        env_gate="HLEDAC_ENABLE_BGP",
        ram_budget_mb=60,
        priority=5,
        description="BGP enrichment sidecar",
    ),
    "bgp_pdns": LaneSpec(
        lane_id="bgp_pdns",
        env_gate="HLEDAC_ENABLE_BGP_PDNS",
        ram_budget_mb=60,
        priority=4,
        description="BGP passive DNS lookup",
    ),
    "shodan": LaneSpec(
        lane_id="shodan",
        env_gate="HLEDAC_ENABLE_SHODAN",
        ram_budget_mb=40,
        priority=5,
        description="Shodan intelligence API lane",
    ),
    "censys": LaneSpec(
        lane_id="censys",
        env_gate="HLEDAC_ENABLE_CENSYS",
        ram_budget_mb=40,
        priority=5,
        description="Censys intelligence API lane",
    ),
    "greynoise": LaneSpec(
        lane_id="greynoise",
        env_gate="HLEDAC_ENABLE_GREYNOISE",
        ram_budget_mb=40,
        priority=5,
        description="GreyNoise threat intelligence lane",
    ),
    "threat_intel": LaneSpec(
        lane_id="threat_intel",
        env_gate="HLEDAC_ENABLE_TI_FEEDS",
        ram_budget_mb=60,
        priority=6,
        description="Threat intelligence feeds sidecar",
    ),
    "whois": LaneSpec(
        lane_id="whois",
        env_gate="HLEDAC_ENABLE_WHOIS",
        ram_budget_mb=30,
        priority=4,
        description="WHOIS lookup sidecar",
    ),
    # ── Content / dark surface lanes ──────────────────────────────────────────
    "dark_pivots": LaneSpec(
        lane_id="dark_pivots",
        env_gate="HLEDAC_ENABLE_DARK_PIVOTS",
        ram_budget_mb=100,
        priority=6,
        description="Tor/I2P/IPFS pivot queries",
    ),
    "common_crawl": LaneSpec(
        lane_id="common_crawl",
        env_gate="HLEDAC_ENABLE_COMMONCRAWL",
        ram_budget_mb=80,
        priority=5,
        description="CommonCrawl search lane",
    ),
    "wayback": LaneSpec(
        lane_id="wayback",
        env_gate="HLEDAC_ENABLE_WAYBACK",
        ram_budget_mb=60,
        priority=4,
        description="Wayback Machine archival search",
    ),
    "providerless_discovery": LaneSpec(
        lane_id="providerless_discovery",
        env_gate="HLEDAC_ENABLE_PROVIDERLESS_DISCOVERY",
        ram_budget_mb=60,
        priority=4,
        description="Cascade: DDG→Historical→Wayback",
    ),
    # ── Sidecar adapters (from SidecarRegistry) ───────────────────────────────
    "fediverse": LaneSpec(
        lane_id="fediverse",
        env_gate="HLEDAC_ENABLE_FEDIVERSE",
        ram_budget_mb=50,
        priority=6,
        description="Fediverse/Mastodon discovery",
    ),
    "academic": LaneSpec(
        lane_id="academic",
        env_gate="HLEDAC_ENABLE_ACADEMIC",
        ram_budget_mb=80,
        priority=5,
        description="Academic/research lane (GLiNER NER)",
    ),
    "alt_protocols": LaneSpec(
        lane_id="alt_protocols",
        env_gate="HLEDAC_ENABLE_ALT_PROTOCOLS",
        ram_budget_mb=60,
        priority=4,
        description="Gopher, Finger, etc.",
    ),
    "leak_sentinel": LaneSpec(
        lane_id="leak_sentinel",
        env_gate="HLEDAC_ENABLE_LEAKSENTINEL",
        ram_budget_mb=30,
        priority=3,
        description="Secret/leak detection sidecar",
    ),
    "social": LaneSpec(
        lane_id="social",
        env_gate="HLEDAC_ENABLE_SOCIAL",
        ram_budget_mb=60,
        priority=5,
        description="Social media discovery",
    ),
    "social_identity_surface": LaneSpec(
        lane_id="social_identity_surface",
        env_gate="HLEDAC_ENABLE_SOCIAL_IDENTITY_SURFACE",
        ram_budget_mb=50,
        priority=5,
        description="Social identity surface mining",
    ),
    "passive_fingerprint": LaneSpec(
        lane_id="passive_fingerprint",
        env_gate="HLEDAC_ENABLE_PASSIVE_FINGERPRINT",
        ram_budget_mb=40,
        priority=5,
        description="Passive TLS fingerprinting",
    ),
    "passive_tech_stack": LaneSpec(
        lane_id="passive_tech_stack",
        env_gate="HLEDAC_ENABLE_PASSIVE_TECH_STACK",
        ram_budget_mb=40,
        priority=4,
        description="Passive technology stack detection",
    ),
    "identity_stitching": LaneSpec(
        lane_id="identity_stitching",
        env_gate="HLEDAC_ENABLE_IDENTITY_STITCHING",
        ram_budget_mb=80,
        priority=5,
        description="Cross-IOC identity stitching",
    ),
    "temporal_archaeology": LaneSpec(
        lane_id="temporal_archaeology",
        env_gate="HLEDAC_ENABLE_TEMPORAL_ARCHAEOLOGY",
        ram_budget_mb=80,
        priority=5,
        description="Historical timeline synthesis",
    ),
    "lancedb_rag": LaneSpec(
        lane_id="lancedb_rag",
        env_gate="HLEDAC_ENABLE_GRAPH_RAG",
        ram_budget_mb=150,
        priority=4,
        description="LanceDB RAG embeddings sidecar",
    ),
    "github_gist": LaneSpec(
        lane_id="github_gist",
        env_gate="HLEDAC_ENABLE_GITHUB_GIST",
        ram_budget_mb=40,
        priority=5,
        description="GitHub GIST discovery",
    ),
    "ja4_collector": LaneSpec(
        lane_id="ja4_collector",
        env_gate="HLEDAC_ENABLE_JA4_COLLECTOR",
        ram_budget_mb=30,
        priority=4,
        description="TLS JA4 fingerprint collector",
    ),
    "tvnews": LaneSpec(
        lane_id="tvnews",
        env_gate="HLEDAC_ENABLE_TV_NEWS",
        ram_budget_mb=60,
        priority=4,
        description="TV news stream analysis",
    ),
    "shadow_walker": LaneSpec(
        lane_id="shadow_walker",
        env_gate="HLEDAC_ENABLE_SHADOW_WALKER",
        ram_budget_mb=100,
        priority=3,
        description="Shadow network walker (experimental)",
    ),
    # ── Forensics lanes ────────────────────────────────────────────────────────
    "image_osint": LaneSpec(
        lane_id="image_osint",
        env_gate="HLEDAC_ENABLE_IMAGE_OSINT",
        ram_budget_mb=100,
        priority=5,
        description="Image forensics and geolocation",
    ),
    "steganography": LaneSpec(
        lane_id="steganography",
        env_gate="HLEDAC_ENABLE_STEGANOGRAPHY",
        ram_budget_mb=80,
        priority=4,
        description="Image steganography detection",
    ),
    "digital_ghost": LaneSpec(
        lane_id="digital_ghost",
        env_gate="HLEDAC_ENABLE_DIGITAL_GHOST",
        ram_budget_mb=80,
        priority=4,
        description="Digital forensics steganography",
    ),
    # ── Research / brain lanes ─────────────────────────────────────────────────
    "hypothesis": LaneSpec(
        lane_id="hypothesis",
        env_gate="HLEDAC_ENABLE_HYPOTHESIS",
        ram_budget_mb=120,
        priority=6,
        description="Hypothesis-driven pivot planner",
    ),
    "dspy": LaneSpec(
        lane_id="dspy",
        env_gate="HLEDAC_ENABLE_DSPY",
        ram_budget_mb=200,
        priority=5,
        description="DSPy compiled hypothesis generation",
    ),
    "hermes_synthesis": LaneSpec(
        lane_id="hermes_synthesis",
        env_gate="HLEDAC_ENABLE_HERMES_SYNTHESIS",
        ram_budget_mb=512,
        priority=5,
        description="Hermes3 synthesis lane",
    ),
    "graph_analysis": LaneSpec(
        lane_id="graph_analysis",
        env_gate="HLEDAC_ENABLE_GRAPH_ANALYSIS",
        ram_budget_mb=150,
        priority=5,
        description="Graph analytics overlay",
    ),
    # ── Browser / automation lanes ─────────────────────────────────────────────
    "nodriver": LaneSpec(
        lane_id="nodriver",
        env_gate="HLEDAC_ENABLE_NODRIVER",
        ram_budget_mb=300,
        priority=4,
        description="Headless Chrome browser automation",
    ),
    "heavy_browser": LaneSpec(
        lane_id="heavy_browser",
        env_gate="HLEDAC_ENABLE_HEAVY_BROWSER",
        ram_budget_mb=500,
        priority=3,
        description="Playwright browser (M1 RAM intensive)",
    ),
    "captcha_detection": LaneSpec(
        lane_id="captcha_detection",
        env_gate="HLEDAC_ENABLE_CAPTCHA_DETECTION",
        ram_budget_mb=60,
        priority=4,
        description="CAPTCHA detection and solving",
    ),
    # ── Network recon lanes ───────────────────────────────────────────────────
    "network_recon": LaneSpec(
        lane_id="network_recon",
        env_gate="HLEDAC_ENABLE_NETWORK_RECON",
        ram_budget_mb=80,
        priority=5,
        description="DNS/WHOIS/SSL reconnaissance",
    ),
    "banner_grab": LaneSpec(
        lane_id="banner_grab",
        env_gate="HLEDAC_ENABLE_BANNER_GRAB",
        ram_budget_mb=60,
        priority=4,
        description="TCP banner enumeration",
    ),
    "blockchain_analyzer": LaneSpec(
        lane_id="blockchain_analyzer",
        env_gate="HLEDAC_ENABLE_BLOCKCHAIN_ANALYZER",
        ram_budget_mb=100,
        priority=5,
        description="Blockchain forensics (BTC/ETH analysis)",
    ),
    # ── Stealth lanes ─────────────────────────────────────────────────────────
    "stealth_layer": LaneSpec(
        lane_id="stealth_layer",
        env_gate="HLEDAC_ENABLE_STEALTH_LAYER",
        ram_budget_mb=40,
        priority=7,
        description="Stealth mode enforcement layer",
    ),
    "privacy_layer": LaneSpec(
        lane_id="privacy_layer",
        env_gate="HLEDAC_ENABLE_PRIVACY_LAYER",
        ram_budget_mb=40,
        priority=6,
        description="Privacy policy enforcement",
    ),
    # ── Layer / orchestration lanes ────────────────────────────────────────────
    "layers": LaneSpec(
        lane_id="layers",
        env_gate="HLEDAC_ENABLE_LAYERS",
        ram_budget_mb=30,
        priority=8,
        description="Security layer manager",
    ),
    "research_layer": LaneSpec(
        lane_id="research_layer",
        env_gate="HLEDAC_ENABLE_RESEARCH_LAYER",
        ram_budget_mb=80,
        priority=5,
        description="Research analysis layer",
    ),
    "content_layer": LaneSpec(
        lane_id="content_layer",
        env_gate="HLEDAC_ENABLE_CONTENT_LAYER",
        ram_budget_mb=60,
        priority=5,
        description="Content analysis layer",
    ),
    # ── TI / feed lanes ───────────────────────────────────────────────────────
    "ti_feeds": LaneSpec(
        lane_id="ti_feeds",
        env_gate="HLEDAC_ENABLE_TI_FEEDS",
        ram_budget_mb=60,
        priority=6,
        description="Threat intelligence feed processing",
    ),
    # ── Other ─────────────────────────────────────────────────────────────────
    "gopher": LaneSpec(
        lane_id="gopher",
        env_gate="HLEDAC_ENABLE_GOPHER",
        ram_budget_mb=40,
        priority=3,
        description="Gopher protocol support",
    ),
    "zkp": LaneSpec(
        lane_id="zkp",
        env_gate="HLEDAC_ENABLE_ZKP",
        ram_budget_mb=60,
        priority=3,
        description="Zero-knowledge proof verification",
    ),
    "neuro": LaneSpec(
        lane_id="neuro",
        env_gate="HLEDAC_ENABLE_NEURO",
        ram_budget_mb=100,
        priority=4,
        description="Neural cryptography lane",
    ),
}


# ── Profile → Lanes mapping ───────────────────────────────────────────────────
# Each AcquisitionProfile maps to a frozenset of enabled lane_ids.
# Order: most common profiles first.

_PROFILE_LANES: dict[str, frozenset[str]] = {
    # Default: clearnet only, minimal footprint
    "default": frozenset(
        {
            "bgp",
            "whois",
            "social",
            "network_recon",
            "providerless_discovery",
            "threat_intel",
        }
    ),
    # Non-feed diagnostic: same as default + research lanes
    "nonfeed_diagnostic": frozenset(
        {
            "bgp",
            "whois",
            "social",
            "network_recon",
            "providerless_discovery",
            "threat_intel",
            "hypothesis",
            "graph_analysis",
        }
    ),
    # Deep OSINT M1: all M1-safe lanes
    "deep_osint_m1": frozenset(
        {
            "tor",
            "i2p",
            "nym",
            "dht",
            "ipfs",
            "bgp",
            "bgp_pdns",
            "shodan",
            "censys",
            "greynoise",
            "dark_pivots",
            "common_crawl",
            "wayback",
            "providerless_discovery",
            "fediverse",
            "academic",
            "alt_protocols",
            "leak_sentinel",
            "social",
            "social_identity_surface",
            "passive_fingerprint",
            "passive_tech_stack",
            "identity_stitching",
            "temporal_archaeology",
            "hypothesis",
            "dspy",
            "hermes_synthesis",
            "graph_analysis",
            "stealth_layer",
            "privacy_layer",
            "layers",
            "research_layer",
            "content_layer",
            "ti_feeds",
            "image_osint",
            "captcha_detection",
            "network_recon",
            "banner_grab",
        }
    ),
    # Research: LLM + graph + research APIs, no dark surface
    "research": frozenset(
        {
            "academic",
            "hypothesis",
            "dspy",
            "hermes_synthesis",
            "graph_analysis",
            "graph_rag",
            "lancedb_rag",
            "common_crawl",
            "wayback",
            "providerless_discovery",
            "bgp",
            "shodan",
            "censys",
            "greynoise",
            "social",
            "fediverse",
            "github_gist",
            "layers",
            "research_layer",
            "content_layer",
            "threat_intel",
            "ti_feeds",
            "whois",
            "network_recon",
            "image_osint",
        }
    ),
    # Academic: research + geopolitical sources
    "academic": frozenset(
        {
            "academic",
            "hypothesis",
            "dspy",
            "hermes_synthesis",
            "graph_analysis",
            "lancedb_rag",
            "common_crawl",
            "wayback",
            "providerless_discovery",
            "bgp",
            "shodan",
            "censys",
            "greynoise",
            "social",
            "fediverse",
            "github_gist",
            "layers",
            "research_layer",
            "content_layer",
            "threat_intel",
            "ti_feeds",
            "whois",
            "network_recon",
        }
    ),
    # Geopolitical: academic + additional intelligence feeds
    "geopolitical": frozenset(
        {
            "academic",
            "hypothesis",
            "dspy",
            "hermes_synthesis",
            "graph_analysis",
            "lancedb_rag",
            "common_crawl",
            "wayback",
            "providerless_discovery",
            "bgp",
            "bgp_pdns",
            "shodan",
            "censys",
            "greynoise",
            "social",
            "fediverse",
            "github_gist",
            "layers",
            "research_layer",
            "content_layer",
            "threat_intel",
            "ti_feeds",
            "whois",
            "network_recon",
            "banner_grab",
            "image_osint",
        }
    ),
    # Threat intelligence: TI-specific lanes
    "threat_intel": frozenset(
        {
            "bgp",
            "bgp_pdns",
            "shodan",
            "censys",
            "greynoise",
            "threat_intel",
            "ti_feeds",
            "hypothesis",
            "dspy",
            "hermes_synthesis",
            "dark_pivots",
            "common_crawl",
            "wayback",
            "leak_sentinel",
            "identity_stitching",
            "layers",
            "research_layer",
            "whois",
            "network_recon",
        }
    ),
}


# ── LaneRegistry ───────────────────────────────────────────────────────────────


class LaneRegistry:
    """
    Central lane enablement checker.

    Priority order for is_enabled():
      1. HLEDAC_FORCE_LANE_<id>=1  — emergency override (operator sets)
      2. lane_id in current_profile.lanes  — profile-based (set at sprint init)
      3. os.environ.get(env_gate)  — legacy env fallback (for not-yet-migrated lanes)

    M1 8GB note: all lookups are O(1) frozenset + dict — no scanning.
    """

    _profile_lanes: frozenset[str] = frozenset()
    _current_profile: str = "default"

    @classmethod
    def set_profile(cls, profile: str) -> None:
        """
        Set the active sprint profile. Call once at sprint start.

        Args:
            profile: Canonical profile name (e.g. "deep_osint_m1", "research")
        """
        cls._current_profile = profile
        cls._profile_lanes = _PROFILE_LANES.get(profile, frozenset())
        logger.debug(
            "LaneRegistry: profile=%s lanes=%s",
            profile,
            sorted(cls._profile_lanes),
        )

    @classmethod
    def get_profile_lanes(cls) -> frozenset[str]:
        """Return the frozenset of lane IDs for the current profile."""
        return cls._profile_lanes

    @classmethod
    def get_current_profile(cls) -> str:
        """Return the name of the current profile."""
        return cls._current_profile

    @classmethod
    def is_enabled(cls, lane_id: str) -> bool:
        """
        Check if a lane is enabled.

        Fails safely: returns False for unknown lane IDs.

        Args:
            lane_id: The lane identifier (e.g. "tor", "dht", "fediverse")

        Returns:
            True if the lane should run for the current sprint
        """
        # 1. Emergency env override
        override_var = f"HLEDAC_FORCE_LANE_{lane_id.upper()}"
        if os.environ.get(override_var, "").lower() in ("1", "true", "yes", "on"):
            logger.debug("LaneRegistry: %s enabled via %s", lane_id, override_var)
            return True

        # 2. Profile-based check (O(1) frozenset)
        if lane_id in cls._profile_lanes:
            return True

        # 3. Legacy env fallback (for lanes not yet in _PROFILE_LANES)
        spec = _LANE_SPECS.get(lane_id)
        if spec is not None and spec.env_gate:
            legacy = os.environ.get(spec.env_gate, "").lower()
            if legacy in ("1", "true", "yes", "on"):
                return True

        return False

    @classmethod
    def get_spec(cls, lane_id: str) -> LaneSpec | None:
        """Get the LaneSpec for a lane ID, or None if unknown."""
        return _LANE_SPECS.get(lane_id)

    @classmethod
    def get_all_lanes(cls) -> list[str]:
        """Return sorted list of all registered lane IDs."""
        return sorted(_LANE_SPECS.keys())

    @classmethod
    def get_lanes_for_profile(cls, profile: str) -> frozenset[str]:
        """Return the lane frozenset for a specific profile name."""
        return _PROFILE_LANES.get(profile, frozenset())

    @classmethod
    def dump(cls) -> dict[str, bool]:
        """Dump all lanes with their enabled status (for debugging/telemetry)."""
        return {lane_id: cls.is_enabled(lane_id) for lane_id in _LANE_SPECS}


# Singleton alias
LANE_REGISTRY = LaneRegistry
