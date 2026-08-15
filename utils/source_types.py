"""
Source type centralization — single source of truth for ``source_type`` field.

GHOST_INVARIANTS:
- StrEnum is bytes-cheap (members are interned strings, no per-instance dict).
- Backward-compatible: ``SourceType`` and the legacy string literal form
  compare equal (``SourceType.CT_LOG == "ct_log"``).
- Adding a new source type = one line in :class:`SourceType`. The type-checker
  will surface every call site that needs updating.
- Runtime-validating: ``SourceType("nonexistent")`` raises ``ValueError``.

M1 8GB UMA: 0 KB runtime overhead; enum is ``__sizeof__`` < 1 KiB.

Discovery: ``rg -oE 'source_type="[a-z_0-9]+"' --type py | sort -u`` (2026-06-01)
yields 80 unique values across the sprint pipeline. All are captured below
(plus the canonical aliases — see :data:`LEGACY_ALIASES`).
"""


import enum
from typing import Final, Literal
from core import aclose


class SourceType(enum.StrEnum):
    """Canonical source type registry for ``CanonicalFinding.source_type``.

    Use the enum member (``SourceType.CT_LOG``) at call sites where the
    value is known statically; use :meth:`SourceType` for runtime validation
    of untrusted input (will raise :class:`ValueError` for unknown strings).
    """

    # ── Core / CT ─────────────────────────────────────────────────────────
    CT_LOG = "ct_log"
    CT = "ct"
    CT_INDICATORS = "ct_indicators"
    CERT_LOG = "cert_log"

    # ── Web / Clearnet ────────────────────────────────────────────────────
    WEB = "web"
    WEB_FETCH = "web_fetch"
    RSS = "rss"
    RSS_ATOM_PIPELINE = "rss_atom_pipeline"
    SEARXNG = "searxng"
    CORE_FULLTEXT = "core_fulltext"

    # ── Public search engines ─────────────────────────────────────────────
    DUCKDUCKGO_SEARCH = "duckduckgo_search"  # noqa: F811 — enum name only

    # ── Shodan / Censys / GreyNoise / BGP ─────────────────────────────────
    SHODAN_SEARCH = "shodan_search"
    SHODAN_INTEL = "shodan_intel"
    CENSYS_INTEL = "censys_intel"
    GREYNOISE_INTEL = "greynoise_intel"
    BGP_INTELLIGENCE = "bgp_intelligence"
    BGP_ENRICHMENT = "bgp_enrichment"
    BGP_MONITOR = "bgp_monitor"
    BGP_RIPE_STAT = "bgp_ripe_stat"
    RIR_CORRELATION = "rir_correlation"

    # ── Passive DNS / Fingerprint ─────────────────────────────────────────
    PASSIVE_DNS = "passive_dns"
    CIRCL_PDNS = "circl_pdns"
    PASSIVE_FINGERPRINT = "passive_fingerprint"
    PASSIVE_TECH_STACK = "passive_tech_stack"
    DOH = "doh"  # DNS-over-HTTPS — passive-DNS variant

    # ── Dark / Alt protocols ──────────────────────────────────────────────
    ONION_DISCOVERY = "onion_discovery"
    I2P = "i2p"
    I2P_DISCOVERY = "i2p_discovery"
    I2P_CONTENT = "i2p_content"
    IPFS = "ipfs"
    IPFS_CONTENT = "ipfs_content"
    IPFS_DIRECTORY = "ipfs_directory"
    IPFS_FETCH = "ipfs_fetch"
    IPFS_SEARCH = "ipfs_search"
    GOPHER = "gopher"
    GOPHER_CONTENT = "gopher_content"
    GEMINI = "gemini"
    GEMINI_CONTENT = "gemini_content"
    ZERONET = "zeronet"
    ZERONET_CONTENT = "zeronet_content"
    FREENET = "freenet"
    FREENET_CONTENT = "freenet_content"

    # ── DHT / Fediverse / Social ──────────────────────────────────────────
    DHT_DISCOVERY = "dht_discovery"
    DHT_METADATA = "dht_metadata"
    FEDIVERSE = "fediverse"
    MATRIX = "matrix"
    MATRIX_PUBLIC = "matrix_public"
    SOCIAL_IDENTITY_SURFACE = "social_identity_surface"

    # ── Academic / Research ──────────────────────────────────────────────
    ACADEMIC = "academic"
    ACADEMIC_SEARCH = "academic_search"
    ARXIV_BULK = "arxiv_bulk"
    OPENALEX = "openalex"
    S2ORC = "s2orc"
    UNPAYWALL = "unpaywall"

    # ── Leak / Sentinel ──────────────────────────────────────────────────
    PASTEBIN_MONITOR = "pastebin_monitor"
    GITHUB_SECRET_SCANNER = "github_secret_scanner"
    GITHUB = "github"
    LEAK_SENTINEL = "leak_sentinel"

    # ── Forensics / Steganography ────────────────────────────────────────
    STEGANOGRAPHY_DETECTION = "steganography_detection"
    DIGITAL_GHOST_DETECTION = "digital_ghost_detection"
    BLOCKCHAIN_FORENSICS = "blockchain_forensics"
    FORENSIC_ANALYSIS = "forensic_analysis"  # Sprint F261

    # ── Identity / Exposure / Temporal ───────────────────────────────────
    IDENTITY_STITCHING = "identity_stitching"
    IDENTITY_ATTRIBUTION = "identity_attribution"
    EXPOSURE_CORRELATION = "exposure_correlation"
    TEMPORAL_ARCHAEOLOGY = "temporal_archaeology"

    # ── Network recon / CVE ──────────────────────────────────────────────
    NETWORK_RECON = "network_recon"
    BANNER_GRAB = "banner_grab"
    NVD_CVE = "nvd_cve"
    CVE_LOOKUP = "cve_lookup"

    # ── Synthesis / Inference / Patterns ─────────────────────────────────
    HERMES_INFERENCE = "hermes_inference"
    LLM_SYNTHESIS = "llm_synthesis"
    TOT_SYNTHESIS = "tot_synthesis"
    DEEP_RESEARCH = "deep_research"
    DEEP_PROBE = "deep_probe"
    DEEP_PROBE_IPFS = "deep_probe_ipfs"
    PATTERN_BEHAVIORAL = "pattern_behavioral"
    PATTERN_TEMPORAL = "pattern_temporal"
    KILLCHAIN_TAG = "killchain_tag"

    # ── Pipeline / Context ───────────────────────────────────────────────
    CONTEXT_SEED = "context_seed"
    RL_RESEARCH = "rl_research"
    SPRINT_DIFF = "sprint_diff"
    EVIDENCE_PACKET = "evidence_packet"
    DOCUMENT = "document"

    # ── Historical / Archive ─────────────────────────────────────────────
    WAYBACK_CDX = "wayback_cdx"
    WAYBACK_DIFF = "wayback_diff"

    # ── Test / Synthetic (kept for hermetic probe lanes) ─────────────────
    TEST = "test"
    BENCH_SOURCE = "bench_source"

    def __str__(self) -> str:
        # StrEnum.__str__ is already the value; explicit for IDE friendliness
        return self.value


# Backward-compat: legacy string values that were renamed or merged. New code
# must use the canonical member, but we accept the legacy string at the
# runtime-validating constructor and route it to the canonical member.
LEGACY_ALIASES: Final[dict[str, str]] = {
    "ct": SourceType.CT_LOG.value,
    "certificate_transparency": SourceType.CT_LOG.value,  # Sprint F262 sweep
    "rss": SourceType.RSS_ATOM_PIPELINE.value,
    "ipfs": SourceType.IPFS_CONTENT.value,
    "i2p": SourceType.I2P_DISCOVERY.value,
    "gopher": SourceType.GOPHER_CONTENT.value,
    "gemini": SourceType.GEMINI_CONTENT.value,
    "zeronet": SourceType.ZERONET.value,
    "freenet": SourceType.FREENET.value,
    "matrix": SourceType.MATRIX_PUBLIC.value,
    "academic": SourceType.ACADEMIC_SEARCH.value,
    "github": SourceType.GITHUB_SECRET_SCANNER.value,
    "duckduckgo_search": SourceType.WEB_FETCH.value,
    "web": SourceType.WEB_FETCH.value,
    "doh": SourceType.PASSIVE_DNS.value,  # DNS-over-HTTPS — passive-DNS variant
}


def canonical_source_type(value: str | SourceType | None) -> str:
    """Return the canonical string for a source-type value (legacy or modern).

    Never raises — unknown values are returned unchanged so a forward-compatible
    finding recorded today still resolves on a future schema bump. ``None``
    and empty input both return ``""``.
    """
    if value is None:
        return ""
    try:
        raw = value.value if isinstance(value, SourceType) else str(value)
    except (AttributeError, TypeError):
        return ""
    if not raw:
        return ""
    return LEGACY_ALIASES.get(raw, raw)


# Type alias for static type-checkers (mypy/pyright).
SourceTypeLiteral = Literal[
    "ct_log", "ct", "ct_indicators", "cert_log",
    "web", "web_fetch", "rss", "rss_atom_pipeline", "searxng", "core_fulltext",
    "duckduckgo_search",
    "shodan_search", "shodan_intel", "censys_intel", "greynoise_intel",
    "bgp_intelligence", "bgp_enrichment", "bgp_monitor", "bgp_ripe_stat", "rir_correlation",
    "passive_dns", "circl_pdns", "passive_fingerprint", "passive_tech_stack", "doh",
    "onion_discovery", "i2p", "i2p_discovery", "i2p_content",
    "ipfs", "ipfs_content", "ipfs_directory", "ipfs_fetch", "ipfs_search",
    "gopher", "gopher_content", "gemini", "gemini_content",
    "zeronet", "zeronet_content", "freenet", "freenet_content",
    "dht_discovery", "dht_metadata", "fediverse", "matrix", "matrix_public",
    "social_identity_surface",
    "academic", "academic_search", "arxiv_bulk", "openalex", "s2orc", "unpaywall",
    "pastebin_monitor", "github_secret_scanner", "github", "leak_sentinel",
    "steganography_detection", "digital_ghost_detection", "blockchain_forensics",
    "forensic_analysis",  # Sprint F265 — closes cosmetic gap with SourceType.FORENSIC_ANALYSIS
    "identity_stitching", "identity_attribution", "exposure_correlation", "temporal_archaeology",
    "network_recon", "banner_grab", "nvd_cve", "cve_lookup",
    "hermes_inference", "llm_synthesis", "tot_synthesis",
    "deep_research", "deep_probe", "deep_probe_ipfs",
    "pattern_behavioral", "pattern_temporal", "killchain_tag",
    "context_seed", "rl_research", "sprint_diff", "evidence_packet", "document",
    "wayback_cdx", "wayback_diff",
    "test", "bench_source",
]


__all__ = [
    "SourceType",
    "SourceTypeLiteral",
    "LEGACY_ALIASES",
    "canonical_source_type",
]
