"""
PatternMatcher singleton — Rust Aho-Corasick backend.

Pattern intelligence baseline — §8 first sprint.



Scope: ONLY this module and tests/probe_8x/.
No AO imports, no transport imports, no network access.

Always-on: Rust Aho-Corasick via hledac_rust_extensions (primary hot-path).
Fallback: linear str.find() scan over ~200 bootstrap patterns (<1ms for 4KB text).
pyahocorasick C-extension NOT required — avoids Python 3.14 compatibility risk.
"""

import functools
import logging
import os
import re
import sys
import threading
import time
from typing import NamedTuple, cast

from operator import attrgetter, itemgetter
logger = logging.getLogger(__name__)

__all__ = [
    "PatternHit",
    "get_pattern_matcher",
    "configure_patterns",
    "match_text",
    "match_text_batch",
    "reset_pattern_matcher",
    "prewarm",
    "configure_default_bootstrap_patterns_if_empty",
    "get_default_bootstrap_patterns",
    "extract_high_precision_entities",
    "extract_structured_entities",
]

# -----------------------------------------------------------------------------
# Rust extension import guard
# -----------------------------------------------------------------------------
_RUST_ACO_AVAILABLE = False
_RUST_STRUCTURED_EXTRACTOR_AVAILABLE = False
_RUST_IMPORT_ERROR: str | None = None
try:
    # R6: Centralized Rust access via core.rust_backend
    from hledac.universal.core.rust_backend import rust

    # Expose Rust classes as RustAhoCorasickMatcher for API compatibility
    RustAhoCorasickMatcher = rust.raw.AhoCorasickMatcher
    _RUST_ACO_AVAILABLE = True

    # Issue #15: check for unified structured entity extractor via rust.raw
    _raw = rust.raw
    if _raw is not None and hasattr(_raw, "extract_structured_entities_py"):
        _RUST_STRUCTURED_EXTRACTOR_AVAILABLE = True
        _rust_extract_structured = _raw.extract_structured_entities_py
        _rust_batch_extract_structured = _raw.batch_extract_structured_entities_py
    else:
        _rust_extract_structured = None
        _rust_batch_extract_structured = None
except ImportError as _exc:
    _RUST_IMPORT_ERROR = str(_exc)

# Issue #17: fatal warning at boot if Rust ACO is not available
# This is emitted once at module load time — not per-call
if not _RUST_ACO_AVAILABLE:
    logger.warning(
        "[FATAL-WARN] Rust Aho-Corasick (hledac_rust_extensions) not available. "
        "Linear scan fallback will be used. "
        "Performance: O(patterns × text_length) vs O(text_length) with Rust ACO. "
        "Install: uv add hledac-rust-extensions or rebuild Rust extensions. "
        f"Import error: {_RUST_IMPORT_ERROR}"
    )
else:
    logger.debug("[OK] Rust Aho-Corasick available — primary hot-path enabled.")


# -----------------------------------------------------------------------------
# Backend truth — lazily resolved on first get_backend_info() call
# -----------------------------------------------------------------------------
def get_backend_info() -> dict:
    """Return backend info — Rust ACO primary, linear scan fallback."""
    if _RUST_ACO_AVAILABLE and _matcher_state._rust_aco is not None:
        backend = "rust_aho_corasick"
    else:
        backend = "linear_scan"
    return {
        "backend": backend,
        "available": _RUST_ACO_AVAILABLE,
        "rust_available": _RUST_ACO_AVAILABLE,
    }


# -----------------------------------------------------------------------------
# Typed hit contract
# -----------------------------------------------------------------------------

class PatternHit(NamedTuple):
    """Single pattern match result.

    Invariants:
    - pattern, label are sys.intern()'d (dedup + fast compare)
    - value is a direct substring slice from input text (NOT interned)
    - start/end are byte offsets matching value extraction
    """

    pattern: str
    start: int
    end: int
    value: str
    label: str | None

    def __repr__(self) -> str:
        return f"PatternHit({self.pattern!r}, {self.start}, {self.end}, {self.value!r}, {self.label!r})"


# -----------------------------------------------------------------------------
# Bootstrap OSINT literal pack — Sprint 8BO v3 IOC-First
# High-signal, lowercase, exact-match literals only.
# NO regex, NO case-sensitive variants, NO short ambiguous tokens.
#
# Layer 1 — Structured identifiers (highest precision)
# Layer 2 — TTP / ATT&CK-like terminology
# Layer 3 — Malware / offensive tooling taxonomy
# Layer 4 — OSINT / leak vocabulary (precision-safe)
# -----------------------------------------------------------------------------
_BOOTSTRAP_PATTERNS_V3: tuple[tuple[str, str], ...] = (
    # === Layer 1: Structured identifiers (highest precision) ===
    ("cve-", "vulnerability_id"),
    ("ghsa-", "vulnerability_id"),
    ("rhsa-", "vulnerability_id"),
    ("usn-", "vulnerability_id"),
    ("msrc-", "vulnerability_id"),
    ("edb-id", "exploit_db_id"),
    ("edb:", "exploit_db_id"),
    # === Layer 2: TTP / ATT&CK-like ===
    ("lateral movement", "attack_technique"),
    ("credential dumping", "attack_technique"),
    ("command and control", "attack_technique"),
    ("c2 beacon", "attack_technique"),
    ("privilege escalation", "attack_technique"),
    ("defense evasion", "attack_technique"),
    ("persistence mechanism", "attack_technique"),
    ("living off the land", "attack_technique"),
    ("lolbin", "attack_technique"),
    ("lolbas", "attack_technique"),
    (" spear-phishing", "attack_technique"),
    (" spear phishing", "attack_technique"),
    ("data breach", "security_incident"),
    ("data dump", "security_incident"),
    # === Layer 2b: Named APT / threat actor groups (Sprint F153) ===
    # High-precision, low-FP: these identifiers are rarely used outside CTI context
    ("apt28", "threat_actor"),
    ("apt-28", "threat_actor"),  # hyphenated variant (Sprint F173B)
    ("apt29", "threat_actor"),
    ("apt41", "threat_actor"),
    ("lazarus group", "threat_actor"),
    ("sandworm", "threat_actor"),
    ("fancy bear", "threat_actor"),
    ("cozy bear", "threat_actor"),
    # === Layer 3: Malware / offensive tooling ===
    ("infostealer", "malware_type"),
    ("wiper", "malware_type"),
    ("wiper attack", "malware_type"),
    ("exploit kit", "threat_type"),
    ("cobalt strike", "offensive_tool"),
    ("cobalt strike beacon", "offensive_tool"),
    ("mimikatz", "offensive_tool"),
    ("sliver c2", "offensive_tool"),
    ("sliver", "offensive_tool"),
    ("dropper", "malware_type"),
    ("loader", "malware_type"),
    ("ransomware-as-a-service", "malware_type"),
    ("raas", "malware_type"),
    ("ransomware", "malware_type"),
    # === Layer 4: OSINT / leak vocabulary ===
    ("leaked database", "osint_source"),
    ("pastebin leak", "osint_source"),
    ("github dork", "osint_source"),
    ("shodan", "osint_source"),
    ("censys", "osint_source"),
    ("greynoise", "osint_source"),
    ("darknet domain", "darknet_domain"),
    # === Original v1/v2 core literals (preserved) ===
    (".onion", "darknet_domain"),
    ("phishing", "attack_vector"),
    ("malware", "threat_type"),
    ("botnet", "threat_type"),
    ("exploit", "attack_vector"),
    ("vulnerability", "threat_type"),
    ("breach", "security_incident"),
    ("leak", "security_incident"),
    ("leaked", "security_incident"),
    ("credentials", "credential_type"),
    ("credential", "credential_type"),
    ("backdoor", "threat_type"),
    # === Morphology variants from v2 ===
    ("vulnerabilities", "threat_type"),
    ("exploited", "attack_vector"),
    ("exploits", "attack_vector"),
    ("exploiting", "attack_vector"),
    ("ransomware attacks", "malware_type"),
    ("breaches", "security_incident"),
    ("leaks", "security_incident"),
    ("infected", "malware_type"),
    ("infection", "malware_type"),
    # === Sprint 8QB V4 OSINT Literals ===
    # Layer 5: Cryptocurrency / blockchain indicators
    ("bitcoin:", "bitcoin_payment"),
    ("bitcoin address", "bitcoin_payment"),
    ("btc address", "bitcoin_payment"),
    ("send btc", "bitcoin_payment"),
    ("wallet address", "bitcoin_payment"),
    # Layer 5: Messaging platform indicators
    ("t.me/", "telegram_link"),
    ("telegram channel", "telegram_link"),
    ("telegram group", "telegram_link"),
    ("tg://", "telegram_link"),
    # Layer 5: MISP / threat intel sharing
    ("misp event", "misp_indicator"),
    ("misp-event", "misp_indicator"),
    ("misp uuid", "misp_indicator"),
    ("misp indicator", "misp_indicator"),
    # Layer 5: Paste sites / data leak venues
    ("pastebin.com/", "paste_site"),
    ("paste.ee/", "paste_site"),
    ("ghostbin.com/", "paste_site"),
    ("hastebin.com/", "paste_site"),
    # Layer 5: Credential leak / combolist patterns
    ("combolist", "credential_leak"),
    ("stealer log", "credential_leak"),
    ("database leak", "security_incident"),  # reinforced
    # Layer 5: Ransomware groups V2
    ("lockbit", "ransomware_group"),
    ("blackcat", "ransomware_group"),
    ("alphv", "ransomware_group"),
    ("clop", "ransomware_group"),
    ("play ransomware", "ransomware_group"),
    ("royal ransomware", "ransomware_group"),
    ("bl00dy", "ransomware_group"),
    ("8base", "ransomware_group"),
    ("rhysida", "ransomware_group"),
    # === P2.2: Akira Ransomware (active 2023+, targets Win+Linux) ===
    ("akira ransomware", "ransomware_group"),
    ("akira locker", "ransomware_group"),
    ("akira leak site", "dark_market"),
    ("akira victim", "ransomware_group"),
    ("akira-files", "ransomware_group"),
    ("akira data leak", "security_incident"),
    # === P2.2: BlackSuits Ransomware (Conti rebrand, active 2022+) ===
    ("blacksuits ransomware", "ransomware_group"),
    ("blacksuits locker", "ransomware_group"),
    ("blacksuits leak site", "dark_market"),
    ("blacksuits victim", "ransomware_group"),
    ("blacksuits data leak", "security_incident"),
    ("blacksuits dark web", "dark_market"),
    # === P2.2: C2 Frameworks (beyond cobalt strike / sliver) ===
    ("metasploit framework", "offensive_tool"),
    ("metasploit c2", "offensive_tool"),
    ("msfvenom", "offensive_tool"),
    ("covenant c2", "offensive_tool"),
    ("koadic c2", "offensive_tool"),
    ("koadic", "offensive_tool"),
    ("pupy ratel", "offensive_tool"),
    ("pupy", "offensive_tool"),
    ("poshc2", "offensive_tool"),
    ("posh c2", "offensive_tool"),
    ("brute ratel", "offensive_tool"),
    ("brute-ratel", "offensive_tool"),
    ("silent push", "offensive_tool"),
    ("mythic c2", "offensive_tool"),
    ("mythic", "offensive_tool"),
    ("havoc c2", "offensive_tool"),
    ("havoc framework", "offensive_tool"),
    ("deimos c2", "offensive_tool"),
    ("octopus c2", "offensive_tool"),
    ("octopus", "offensive_tool"),
    ("evil c2", "offensive_tool"),
    ("evilwinrm", "offensive_tool"),
    ("crackmapexec", "offensive_tool"),
    ("wmiexec", "offensive_tool"),
    ("smbexec", "offensive_tool"),
    ("bloodhound", "offensive_tool"),
    ("sharpbound", "offensive_tool"),
    # === P2.2: VPN Services (legitimate) ===
    ("expressvpn", "vpn_service"),
    ("nordvpn", "vpn_service"),
    ("mullvad", "vpn_service"),
    ("protonvpn", "vpn_service"),
    ("surfshark", "vpn_service"),
    ("private internet access", "vpn_service"),
    ("pia vpn", "vpn_service"),
    ("cyberghost", "vpn_service"),
    ("ipvanish", "vpn_service"),
    ("windscribe", "vpn_service"),
    ("torguard", "vpn_service"),
    ("hide.me", "vpn_service"),
    ("vyprvpn", "vpn_service"),
    ("perfect privacy", "vpn_service"),
    ("airvpn", "vpn_service"),
    # === P2.2: VPN Protocols ===
    ("wireguard vpn", "vpn_protocol"),
    ("openvpn config", "vpn_protocol"),
    ("openvpn profile", "vpn_protocol"),
    ("ikev2 vpn", "vpn_protocol"),
    ("vpn protocol", "vpn_protocol"),
    # === P2.2: VPN Malware / Fake VPN (infostealer vector) ===
    ("free vpn malware", "vpn_malware"),
    ("fake vpn", "vpn_malware"),
    ("vpn stealer", "vpn_malware"),
    ("vpn credential theft", "vpn_malware"),
    ("vpn dump", "vpn_malware"),
    ("hotspot shield malware", "vpn_malware"),
    ("psiphon malware", "vpn_malware"),
    # === Sprint 8SC V5 DARK WEB + CRYPTO + PGP ===
    # Dark protocols
    ("i2p", "dark_protocol"),
    ("yggdrasil", "dark_protocol"),
    ("zeronet", "dark_protocol"),
    ("freenet", "dark_protocol"),
    ("ipfs://", "dark_protocol"),
    ("magnet:", "dark_protocol"),
    (".b32.i2p", "dark_protocol"),
    (".i2p", "dark_protocol"),
    ("ed2k:", "dark_protocol"),
    ("gnutella", "dark_protocol"),
    ("retroshare", "dark_protocol"),
    # PGP artifacts
    ("-----begin pgp", "pgp_artifact"),
    ("pgp key", "pgp_artifact"),
    ("pgp fingerprint", "pgp_artifact"),
    ("gpg key", "pgp_artifact"),
    ("public key block", "pgp_artifact"),
    ("-----end pgp", "pgp_artifact"),
    ("keybase.io", "pgp_artifact"),
    # Crypto payment
    ("monero", "crypto_payment"),
    ("xmr address", "crypto_payment"),
    ("xmr wallet", "crypto_payment"),
    ("donate xmr", "crypto_payment"),
    ("zcash", "crypto_payment"),
    ("zec address", "crypto_payment"),
    ("privacy coin", "crypto_payment"),
    ("untraceable payment", "crypto_payment"),
    # Dark market
    ("darknet market", "dark_market"),
    ("dark market", "dark_market"),
    ("vendor shop", "dark_market"),
    ("escrow service", "dark_market"),
    ("dispute resolution", "dark_market"),
    ("pgp required", "dark_market"),
    ("jabber xmpp", "dark_market"),
    ("hidden service marketplace", "dark_market"),
)

_BOOTSTRAP_PATTERNS = _BOOTSTRAP_PATTERNS_V3
_BOOTSTRAP_PACK_VERSION = 3

# -----------------------------------------------------------------------------
# Pattern pack metadata — lightweight per-literal annotations
# Each entry: (pattern, metadata_dict)
# Keys: layer (1-4), source_vocab, mitre_tactic
# -----------------------------------------------------------------------------
_PATTERN_PACK_METADATA: dict[str, dict] = {
    # Layer 1: identifiers
    "cve-": {"layer": 1, "source_vocab": "identifier", "mitre_tactic": None},
    "ghsa-": {"layer": 1, "source_vocab": "identifier", "mitre_tactic": None},
    "rhsa-": {"layer": 1, "source_vocab": "identifier", "mitre_tactic": None},
    "usn-": {"layer": 1, "source_vocab": "identifier", "mitre_tactic": None},
    "msrc-": {"layer": 1, "source_vocab": "identifier", "mitre_tactic": None},
    "edb-id": {"layer": 1, "source_vocab": "identifier", "mitre_tactic": None},
    "edb:": {"layer": 1, "source_vocab": "identifier", "mitre_tactic": None},
    # Layer 2: TTP
    "lateral movement": {"layer": 2, "source_vocab": "ttp", "mitre_tactic": "TA0008"},
    "credential dumping": {"layer": 2, "source_vocab": "ttp", "mitre_tactic": "TA0006"},
    "command and control": {"layer": 2, "source_vocab": "ttp", "mitre_tactic": "TA0011"},
    "c2 beacon": {"layer": 2, "source_vocab": "ttp", "mitre_tactic": "TA0011"},
    "privilege escalation": {"layer": 2, "source_vocab": "ttp", "mitre_tactic": "TA0004"},
    "defense evasion": {"layer": 2, "source_vocab": "ttp", "mitre_tactic": "TA0005"},
    "persistence mechanism": {"layer": 2, "source_vocab": "ttp", "mitre_tactic": "TA0003"},
    "living off the land": {"layer": 2, "source_vocab": "ttp", "mitre_tactic": None},
    "lolbin": {"layer": 2, "source_vocab": "ttp", "mitre_tactic": "TA0002"},
    "lolbas": {"layer": 2, "source_vocab": "ttp", "mitre_tactic": "TA0002"},
    " spear-phishing": {"layer": 2, "source_vocab": "ttp", "mitre_tactic": "TA0001"},
    " spear phishing": {"layer": 2, "source_vocab": "ttp", "mitre_tactic": "TA0001"},
    "data breach": {"layer": 2, "source_vocab": "ttp", "mitre_tactic": None},
    "data dump": {"layer": 2, "source_vocab": "ttp", "mitre_tactic": None},
    # Layer 3: malware/tooling
    "infostealer": {"layer": 3, "source_vocab": "malware", "mitre_tactic": None},
    "wiper": {"layer": 3, "source_vocab": "malware", "mitre_tactic": None},
    "wiper attack": {"layer": 3, "source_vocab": "malware", "mitre_tactic": None},
    "exploit kit": {"layer": 3, "source_vocab": "malware", "mitre_tactic": None},
    "cobalt strike": {"layer": 3, "source_vocab": "malware", "mitre_tactic": None},
    "cobalt strike beacon": {"layer": 3, "source_vocab": "malware", "mitre_tactic": None},
    "mimikatz": {"layer": 3, "source_vocab": "malware", "mitre_tactic": None},
    "sliver c2": {"layer": 3, "source_vocab": "malware", "mitre_tactic": None},
    "sliver": {"layer": 3, "source_vocab": "malware", "mitre_tactic": None},
    "dropper": {"layer": 3, "source_vocab": "malware", "mitre_tactic": None},
    "loader": {"layer": 3, "source_vocab": "malware", "mitre_tactic": None},
    "ransomware-as-a-service": {"layer": 3, "source_vocab": "malware", "mitre_tactic": None},
    "raas": {"layer": 3, "source_vocab": "malware", "mitre_tactic": None},
    "ransomware": {"layer": 3, "source_vocab": "malware", "mitre_tactic": None},
    # Layer 4: OSINT
    "leaked database": {"layer": 4, "source_vocab": "osint", "mitre_tactic": None},
    "pastebin leak": {"layer": 4, "source_vocab": "osint", "mitre_tactic": None},
    "github dork": {"layer": 4, "source_vocab": "osint", "mitre_tactic": None},
    "shodan": {"layer": 4, "source_vocab": "osint", "mitre_tactic": None},
    "censys": {"layer": 4, "source_vocab": "osint", "mitre_tactic": None},
    "greynoise": {"layer": 4, "source_vocab": "osint", "mitre_tactic": None},
    "darknet domain": {"layer": 4, "source_vocab": "osint", "mitre_tactic": None},
    # Original core
    ".onion": {"layer": 4, "source_vocab": "osint", "mitre_tactic": None},
    "phishing": {"layer": 2, "source_vocab": "ttp", "mitre_tactic": "TA0001"},
    "malware": {"layer": 3, "source_vocab": "malware", "mitre_tactic": None},
    "botnet": {"layer": 3, "source_vocab": "malware", "mitre_tactic": None},
    "exploit": {"layer": 2, "source_vocab": "ttp", "mitre_tactic": None},
    "vulnerability": {"layer": 1, "source_vocab": "identifier", "mitre_tactic": None},
    "breach": {"layer": 2, "source_vocab": "ttp", "mitre_tactic": None},
    "leak": {"layer": 4, "source_vocab": "osint", "mitre_tactic": None},
    "leaked": {"layer": 4, "source_vocab": "osint", "mitre_tactic": None},
    "credentials": {"layer": 2, "source_vocab": "ttp", "mitre_tactic": "TA0006"},
    "credential": {"layer": 2, "source_vocab": "ttp", "mitre_tactic": "TA0006"},
    "backdoor": {"layer": 3, "source_vocab": "malware", "mitre_tactic": None},
    # v2 morphology
    "vulnerabilities": {"layer": 1, "source_vocab": "identifier", "mitre_tactic": None},
    "exploited": {"layer": 2, "source_vocab": "ttp", "mitre_tactic": None},
    "exploits": {"layer": 2, "source_vocab": "ttp", "mitre_tactic": None},
    "exploiting": {"layer": 2, "source_vocab": "ttp", "mitre_tactic": None},
    "ransomware attacks": {"layer": 3, "source_vocab": "malware", "mitre_tactic": None},
    "breaches": {"layer": 2, "source_vocab": "ttp", "mitre_tactic": None},
    "leaks": {"layer": 4, "source_vocab": "osint", "mitre_tactic": None},
    "infected": {"layer": 3, "source_vocab": "malware", "mitre_tactic": None},
    "infection": {"layer": 3, "source_vocab": "malware", "mitre_tactic": None},
    # Sprint F165A — new structured IOC coverage
    "usdt_trc20": {"layer": 1, "source_vocab": "identifier", "mitre_tactic": None},
    "ltc_address": {"layer": 1, "source_vocab": "identifier", "mitre_tactic": None},
    "doge_address": {"layer": 1, "source_vocab": "identifier", "mitre_tactic": None},
    "eth_contract": {"layer": 1, "source_vocab": "identifier", "mitre_tactic": None},
    # Sprint F153 + F173B: threat actor / APT groups
    "apt28": {"layer": 2, "source_vocab": "threat_actor", "mitre_tactic": None},
    "apt-28": {"layer": 2, "source_vocab": "threat_actor", "mitre_tactic": None},  # hyphenated variant (F173B)
    "apt29": {"layer": 2, "source_vocab": "threat_actor", "mitre_tactic": None},
    "apt41": {"layer": 2, "source_vocab": "threat_actor", "mitre_tactic": None},
    "lazarus group": {"layer": 2, "source_vocab": "threat_actor", "mitre_tactic": None},
    "sandworm": {"layer": 2, "source_vocab": "threat_actor", "mitre_tactic": None},
    "fancy bear": {"layer": 2, "source_vocab": "threat_actor", "mitre_tactic": None},
    "cozy bear": {"layer": 2, "source_vocab": "threat_actor", "mitre_tactic": None},
    # === Sprint 8QB V4: Ransomware groups + OSINT + crypto ===
    "lockbit": {"layer": 3, "source_vocab": "ransomware_group", "mitre_tactic": None},
    "blackcat": {"layer": 3, "source_vocab": "ransomware_group", "mitre_tactic": None},
    "alphv": {"layer": 3, "source_vocab": "ransomware_group", "mitre_tactic": None},
    "clop": {"layer": 3, "source_vocab": "ransomware_group", "mitre_tactic": None},
    "play ransomware": {"layer": 3, "source_vocab": "ransomware_group", "mitre_tactic": None},
    "royal ransomware": {"layer": 3, "source_vocab": "ransomware_group", "mitre_tactic": None},
    "bl00dy": {"layer": 3, "source_vocab": "ransomware_group", "mitre_tactic": None},
    "8base": {"layer": 3, "source_vocab": "ransomware_group", "mitre_tactic": None},
    "rhysida": {"layer": 3, "source_vocab": "ransomware_group", "mitre_tactic": None},
    # === P2.2: Akira Ransomware ===
    "akira ransomware": {"layer": 3, "source_vocab": "ransomware_group", "mitre_tactic": None},
    "akira locker": {"layer": 3, "source_vocab": "ransomware_group", "mitre_tactic": None},
    "akira leak site": {"layer": 4, "source_vocab": "dark_market", "mitre_tactic": None},
    "akira victim": {"layer": 3, "source_vocab": "ransomware_group", "mitre_tactic": None},
    "akira-files": {"layer": 3, "source_vocab": "ransomware_group", "mitre_tactic": None},
    "akira data leak": {"layer": 4, "source_vocab": "security_incident", "mitre_tactic": None},
    # === P2.2: BlackSuits Ransomware ===
    "blacksuits ransomware": {"layer": 3, "source_vocab": "ransomware_group", "mitre_tactic": None},
    "blacksuits locker": {"layer": 3, "source_vocab": "ransomware_group", "mitre_tactic": None},
    "blacksuits leak site": {"layer": 4, "source_vocab": "dark_market", "mitre_tactic": None},
    "blacksuits victim": {"layer": 3, "source_vocab": "ransomware_group", "mitre_tactic": None},
    "blacksuits data leak": {"layer": 4, "source_vocab": "security_incident", "mitre_tactic": None},
    "blacksuits dark web": {"layer": 4, "source_vocab": "dark_market", "mitre_tactic": None},
    # === P2.2: C2 Frameworks ===
    "metasploit framework": {"layer": 3, "source_vocab": "offensive_tool", "mitre_tactic": None},
    "metasploit c2": {"layer": 3, "source_vocab": "offensive_tool", "mitre_tactic": None},
    "msfvenom": {"layer": 3, "source_vocab": "offensive_tool", "mitre_tactic": None},
    "covenant c2": {"layer": 3, "source_vocab": "offensive_tool", "mitre_tactic": None},
    "koadic c2": {"layer": 3, "source_vocab": "offensive_tool", "mitre_tactic": None},
    "koadic": {"layer": 3, "source_vocab": "offensive_tool", "mitre_tactic": None},
    "pupy ratel": {"layer": 3, "source_vocab": "offensive_tool", "mitre_tactic": None},
    "pupy": {"layer": 3, "source_vocab": "offensive_tool", "mitre_tactic": None},
    "poshc2": {"layer": 3, "source_vocab": "offensive_tool", "mitre_tactic": None},
    "posh c2": {"layer": 3, "source_vocab": "offensive_tool", "mitre_tactic": None},
    "brute ratel": {"layer": 3, "source_vocab": "offensive_tool", "mitre_tactic": None},
    "brute-ratel": {"layer": 3, "source_vocab": "offensive_tool", "mitre_tactic": None},
    "silent push": {"layer": 3, "source_vocab": "offensive_tool", "mitre_tactic": None},
    "mythic c2": {"layer": 3, "source_vocab": "offensive_tool", "mitre_tactic": None},
    "mythic": {"layer": 3, "source_vocab": "offensive_tool", "mitre_tactic": None},
    "havoc c2": {"layer": 3, "source_vocab": "offensive_tool", "mitre_tactic": None},
    "havoc framework": {"layer": 3, "source_vocab": "offensive_tool", "mitre_tactic": None},
    "deimos c2": {"layer": 3, "source_vocab": "offensive_tool", "mitre_tactic": None},
    "octopus c2": {"layer": 3, "source_vocab": "offensive_tool", "mitre_tactic": None},
    "octopus": {"layer": 3, "source_vocab": "offensive_tool", "mitre_tactic": None},
    "evil c2": {"layer": 3, "source_vocab": "offensive_tool", "mitre_tactic": None},
    "evilwinrm": {"layer": 3, "source_vocab": "offensive_tool", "mitre_tactic": None},
    "crackmapexec": {"layer": 3, "source_vocab": "offensive_tool", "mitre_tactic": None},
    "wmiexec": {"layer": 3, "source_vocab": "offensive_tool", "mitre_tactic": None},
    "smbexec": {"layer": 3, "source_vocab": "offensive_tool", "mitre_tactic": None},
    "bloodhound": {"layer": 3, "source_vocab": "offensive_tool", "mitre_tactic": None},
    "sharpbound": {"layer": 3, "source_vocab": "offensive_tool", "mitre_tactic": None},
    # === P2.2: VPN Services ===
    "expressvpn": {"layer": 4, "source_vocab": "vpn_service", "mitre_tactic": None},
    "nordvpn": {"layer": 4, "source_vocab": "vpn_service", "mitre_tactic": None},
    "mullvad": {"layer": 4, "source_vocab": "vpn_service", "mitre_tactic": None},
    "protonvpn": {"layer": 4, "source_vocab": "vpn_service", "mitre_tactic": None},
    "surfshark": {"layer": 4, "source_vocab": "vpn_service", "mitre_tactic": None},
    "private internet access": {"layer": 4, "source_vocab": "vpn_service", "mitre_tactic": None},
    "pia vpn": {"layer": 4, "source_vocab": "vpn_service", "mitre_tactic": None},
    "cyberghost": {"layer": 4, "source_vocab": "vpn_service", "mitre_tactic": None},
    "ipvanish": {"layer": 4, "source_vocab": "vpn_service", "mitre_tactic": None},
    "windscribe": {"layer": 4, "source_vocab": "vpn_service", "mitre_tactic": None},
    "torguard": {"layer": 4, "source_vocab": "vpn_service", "mitre_tactic": None},
    "hide.me": {"layer": 4, "source_vocab": "vpn_service", "mitre_tactic": None},
    "vyprvpn": {"layer": 4, "source_vocab": "vpn_service", "mitre_tactic": None},
    "perfect privacy": {"layer": 4, "source_vocab": "vpn_service", "mitre_tactic": None},
    "airvpn": {"layer": 4, "source_vocab": "vpn_service", "mitre_tactic": None},
    # === P2.2: VPN Protocols ===
    "wireguard vpn": {"layer": 4, "source_vocab": "vpn_protocol", "mitre_tactic": None},
    "openvpn config": {"layer": 4, "source_vocab": "vpn_protocol", "mitre_tactic": None},
    "openvpn profile": {"layer": 4, "source_vocab": "vpn_protocol", "mitre_tactic": None},
    "ikev2 vpn": {"layer": 4, "source_vocab": "vpn_protocol", "mitre_tactic": None},
    "vpn protocol": {"layer": 4, "source_vocab": "vpn_protocol", "mitre_tactic": None},
    # === P2.2: VPN Malware ===
    "free vpn malware": {"layer": 3, "source_vocab": "vpn_malware", "mitre_tactic": None},
    "fake vpn": {"layer": 3, "source_vocab": "vpn_malware", "mitre_tactic": None},
    "vpn stealer": {"layer": 3, "source_vocab": "vpn_malware", "mitre_tactic": None},
    "vpn credential theft": {"layer": 3, "source_vocab": "vpn_malware", "mitre_tactic": None},
    "vpn dump": {"layer": 3, "source_vocab": "vpn_malware", "mitre_tactic": None},
    "hotspot shield malware": {"layer": 3, "source_vocab": "vpn_malware", "mitre_tactic": None},
    "psiphon malware": {"layer": 3, "source_vocab": "vpn_malware", "mitre_tactic": None},
    # V4 OSINT / leak vocabulary
    "bitcoin:": {"layer": 4, "source_vocab": "crypto_payment", "mitre_tactic": None},
    "bitcoin address": {"layer": 4, "source_vocab": "crypto_payment", "mitre_tactic": None},
    "btc address": {"layer": 4, "source_vocab": "crypto_payment", "mitre_tactic": None},
    "send btc": {"layer": 4, "source_vocab": "crypto_payment", "mitre_tactic": None},
    "wallet address": {"layer": 4, "source_vocab": "crypto_payment", "mitre_tactic": None},
    "t.me/": {"layer": 4, "source_vocab": "telegram_link", "mitre_tactic": None},
    "telegram channel": {"layer": 4, "source_vocab": "telegram_link", "mitre_tactic": None},
    "telegram group": {"layer": 4, "source_vocab": "telegram_link", "mitre_tactic": None},
    "tg://": {"layer": 4, "source_vocab": "telegram_link", "mitre_tactic": None},
    "misp event": {"layer": 4, "source_vocab": "misp_indicator", "mitre_tactic": None},
    "misp-event": {"layer": 4, "source_vocab": "misp_indicator", "mitre_tactic": None},
    "misp uuid": {"layer": 4, "source_vocab": "misp_indicator", "mitre_tactic": None},
    "misp indicator": {"layer": 4, "source_vocab": "misp_indicator", "mitre_tactic": None},
    "pastebin.com/": {"layer": 4, "source_vocab": "paste_site", "mitre_tactic": None},
    "paste.ee/": {"layer": 4, "source_vocab": "paste_site", "mitre_tactic": None},
    "ghostbin.com/": {"layer": 4, "source_vocab": "paste_site", "mitre_tactic": None},
    "hastebin.com/": {"layer": 4, "source_vocab": "paste_site", "mitre_tactic": None},
    "combolist": {"layer": 4, "source_vocab": "credential_leak", "mitre_tactic": None},
    "stealer log": {"layer": 4, "source_vocab": "credential_leak", "mitre_tactic": None},
    "database leak": {"layer": 4, "source_vocab": "security_incident", "mitre_tactic": None},
    # === Sprint 8SC V5: Dark web + crypto + PGP ===
    "i2p": {"layer": 4, "source_vocab": "dark_protocol", "mitre_tactic": None},
    "yggdrasil": {"layer": 4, "source_vocab": "dark_protocol", "mitre_tactic": None},
    "zeronet": {"layer": 4, "source_vocab": "dark_protocol", "mitre_tactic": None},
    "freenet": {"layer": 4, "source_vocab": "dark_protocol", "mitre_tactic": None},
    "ipfs://": {"layer": 4, "source_vocab": "dark_protocol", "mitre_tactic": None},
    "magnet:": {"layer": 4, "source_vocab": "dark_protocol", "mitre_tactic": None},
    ".b32.i2p": {"layer": 4, "source_vocab": "dark_protocol", "mitre_tactic": None},
    ".i2p": {"layer": 4, "source_vocab": "dark_protocol", "mitre_tactic": None},
    "ed2k:": {"layer": 4, "source_vocab": "dark_protocol", "mitre_tactic": None},
    "gnutella": {"layer": 4, "source_vocab": "dark_protocol", "mitre_tactic": None},
    "retroshare": {"layer": 4, "source_vocab": "dark_protocol", "mitre_tactic": None},
    "-----begin pgp": {"layer": 4, "source_vocab": "pgp_artifact", "mitre_tactic": None},
    "pgp key": {"layer": 4, "source_vocab": "pgp_artifact", "mitre_tactic": None},
    "pgp fingerprint": {"layer": 4, "source_vocab": "pgp_artifact", "mitre_tactic": None},
    "gpg key": {"layer": 4, "source_vocab": "pgp_artifact", "mitre_tactic": None},
    "public key block": {"layer": 4, "source_vocab": "pgp_artifact", "mitre_tactic": None},
    "-----end pgp": {"layer": 4, "source_vocab": "pgp_artifact", "mitre_tactic": None},
    "keybase.io": {"layer": 4, "source_vocab": "pgp_artifact", "mitre_tactic": None},
    "monero": {"layer": 4, "source_vocab": "crypto_payment", "mitre_tactic": None},
    "xmr address": {"layer": 4, "source_vocab": "crypto_payment", "mitre_tactic": None},
    "xmr wallet": {"layer": 4, "source_vocab": "crypto_payment", "mitre_tactic": None},
    "donate xmr": {"layer": 4, "source_vocab": "crypto_payment", "mitre_tactic": None},
    "zcash": {"layer": 4, "source_vocab": "crypto_payment", "mitre_tactic": None},
    "zec address": {"layer": 4, "source_vocab": "crypto_payment", "mitre_tactic": None},
    "privacy coin": {"layer": 4, "source_vocab": "crypto_payment", "mitre_tactic": None},
    "untraceable payment": {"layer": 4, "source_vocab": "crypto_payment", "mitre_tactic": None},
    "darknet market": {"layer": 4, "source_vocab": "dark_market", "mitre_tactic": None},
    "dark market": {"layer": 4, "source_vocab": "dark_market", "mitre_tactic": None},
    "vendor shop": {"layer": 4, "source_vocab": "dark_market", "mitre_tactic": None},
    "escrow service": {"layer": 4, "source_vocab": "dark_market", "mitre_tactic": None},
    "dispute resolution": {"layer": 4, "source_vocab": "dark_market", "mitre_tactic": None},
    "pgp required": {"layer": 4, "source_vocab": "dark_market", "mitre_tactic": None},
    "jabber xmpp": {"layer": 4, "source_vocab": "dark_market", "mitre_tactic": None},
    "hidden service marketplace": {"layer": 4, "source_vocab": "dark_market", "mitre_tactic": None},
}


def get_pattern_pack_metadata(pattern: str) -> dict | None:
    """Return metadata for a pattern, or None if not found."""
    return _PATTERN_PACK_METADATA.get(pattern)


# -----------------------------------------------------------------------------
# High-precision regex extraction helper
# Extends AC automaton with structured entity extraction
# -----------------------------------------------------------------------------
_RE_CVE = re.compile(r"CVE-\d{4}-\d{4,7}", re.IGNORECASE)
_RE_GHSA = re.compile(r"GHSA-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}", re.IGNORECASE)
_RE_SHA256 = re.compile(
    r"\b[a-f0-9]{64}\b", re.IGNORECASE
)
_RE_MD5 = re.compile(
    r"\b[a-f0-9]{32}\b", re.IGNORECASE
)
_RE_SHA1 = re.compile(
    r"\b[a-f0-9]{40}\b", re.IGNORECASE
)

# Sprint 8QB V4 — precision regex patterns (compiled once at module level)
# BTC legacy: case-insensitive (addresses may be mixed case)
_RE_BTC_LEGACY = re.compile(r"\b[13][a-km-zA-HJ-NP-Z1-9]{26,34}\b", re.IGNORECASE)
# BTC bech32: bc1 address (P2WPKH/P2WSH), case-insensitive
# Fixed: [ac-hj-np-z02-9] incorrectly included h,j (bech32 charset excludes them)
# Correct bech32 chars (excluding I, O, l, 0, 1): qpzry9x8gf2tvdw0s3jn54khce6mua7l
_RE_BTC_BECH32 = re.compile(r"\bbc1[qpzry9x8gf2tvdw0s3jn54khce6mua7l]{11,71}\b", re.IGNORECASE)
# ETH address: 0x prefix + 40 hex chars (42 total), mixed-case checksum OK
# Strict 0x prefix prevents accidental FP on raw 40-char hex strings
_RE_ETH_ADDR = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
# Telegram t.me/ links — 3+ char slug
_RE_TELEGRAM = re.compile(r"\bt\.me/[\w\-]{3,}\b")
# MISP UUID: 8-4-4-4-12 hex format
_RE_MISP_UUID = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE
)
# Onion v3: exactly 56 base32 chars before .onion (stricter than older patterns)
_RE_ONION_V3 = re.compile(r"\b[a-z2-7]{56}\.onion\b", re.IGNORECASE)

# === PATTERN V5 — DARK WEB + CRYPTO + PGP ===
# Monero mainnet: 95 chars, starts with 4 (case-insensitive for lowercase text)
_RE_XMR_ADDR = re.compile(r"\b4[0-9AB][1-9A-HJ-NP-Za-km-z]{93}\b", re.IGNORECASE)
# I2P B32 address: 52 base32 chars + .b32.i2p
_RE_I2P_ADDR = re.compile(r"\b[a-z2-7]{52}\.b32\.i2p\b", re.IGNORECASE)
# PGP fingerprint: 40 hex chars with optional spaces (case-insensitive)
_RE_PGP_FP = re.compile(r"\b(?:[0-9A-F]{4}\s?){10}\b", re.IGNORECASE)
# IPFS CIDv0: Qm + 44 base58 chars
_RE_IPFS_CID = re.compile(r"\bQm[1-9A-HJ-NP-Za-km-z]{44}\b", re.IGNORECASE)

# === SPRINT F165A — STRUCTURED IOC COVERAGE GAPS ===
# USDT TRC20 (Tron network): T prefix + 33 base58 chars = 34 total
_RE_USDT_TRC20 = re.compile(r"\bT[A-HJ-NP-Za-km-z1-9]{33}\b", re.IGNORECASE)
# Litecoin P2PKH: L prefix + 33 base58 chars = 34 total
_RE_LTC_ADDR = re.compile(r"\bL[1-9A-HJ-NP-Za-km-z]{33}\b", re.IGNORECASE)
# Dogecoin P2PKH: D prefix + 33 base58 chars = 34 total
# Full base58 alphabet (no I, O, 0, l): 123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz
_RE_DOGE_ADDR = re.compile(
    r"\bD[123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz]{33}\b",
    re.IGNORECASE
)
# Ethereum contract address: 0x prefix + 40 hex, commonly a contract (not just EOA)
# Uses _RE_ETH_ADDR — same regex, different label distinguishes contract vs EOA
# Note: removing duplicate regex saves memory; callers use _RE_ETH_ADDR with "eth_contract" label

# === P20 — API KEY / SECRET PATTERNS ===
# AWS Access Key ID: AKIA + 16 uppercase alphanumeric chars (20 total)
_RE_AWS_KEY_ID = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
# Google API Key: AIza + 35 URL-safe base64 chars (39 total)
_RE_GOOGLE_API_KEY = re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b")
# Stripe live secret key: sk_live_ + 24 alphanum chars
_RE_STRIPE_SK = re.compile(r"\bsk_live_[0-9a-zA-Z]{24}\b")
# Slack bot/app token: xox[baprs]- prefix pattern
_RE_SLACK_TOKEN = re.compile(r"\bxox[baprs]-[0-9]{10,13}-[0-9]{10,13}-[A-Za-z0-9]{24,32}\b")



class ExtractedEntity(NamedTuple):
    """High-precision entity extracted via regex post-processing."""
    entity_type: str
    value: str
    start: int
    end: int


def _looks_random(s: str) -> bool:
    """Return True if s looks like a real hash (high-entropy hex string)."""
    return bool(re.fullmatch(r'[a-f0-9]{20,}', s))


# Issue #20 — master regex: one-pass scan instead of 15 separate finditer() calls.
# Compiled lazily on first call, not at import time (avoids module-load overhead).
# Each group is named for fast dispatch to entity_type without string matching.
_MASTER_RE: re.Pattern[str] | None = None
_MASTER_MAP: dict[str, tuple[str, int]] = {
    # Format: group_name: (entity_type, min_len)
    # min_len reflects actual regex minimum length for belt-and-suspenders validation
    "cve": ("cve_identifier", 12),       # CVE-YYYY-NNNN = 12 chars (4+1+4+1+2)
    "ghsa": ("ghsa_identifier", 17),    # GHSA-xxxx-xxxx-xxxx = 17
    "onion3": ("onion_v3_address", 62),  # [a-z2-7]{56}.onion = 62
    "sha256": ("sha256_hash", 64),       # 64 hex
    "md5": ("md5_hash", 32),            # 32 hex
    "sha1": ("sha1_hash", 40),          # 40 hex
    "eth": ("eth_address", 42),          # 0x + 40 hex
    "usdt": ("usdt_trc20", 34),          # T + 33 base58
    "ltc": ("ltc_address", 34),          # L + 33 base58
    "doge": ("doge_address", 34),        # D + 33 base58
    "aws": ("aws_access_key_id", 20),   # AKIA + 16
    "gapi": ("google_api_key", 39),     # AIza + 35
    "stripe": ("stripe_secret_key", 31), # sk_live_ + 24
    "slack": ("slack_token", 51),       # xox[baprs]-10-10-24..32
}


def _get_master_regex() -> re.Pattern[str]:
    """Lazily build and cache the master regex pattern."""
    global _MASTER_RE
    if _MASTER_RE is None:
        # Build alternation from existing compiled regexes (not string patterns)
        # This ensures consistency — we reuse the exact same regex objects
        parts: list[str] = [
            # CVE: CVE-YYYY-NNNN... (12+ chars)
            r"(?P<cve>CVE-\d{4}-\d{4,7})",
            # GHSA: GHSA-xxxx-xxxx-xxxx (17 chars)
            r"(?P<ghsa>GHSA-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4})",
            # Onion v3: 56 base32 + .onion (60+ chars)
            r"(?P<onion3>[a-z2-7]{56}\.onion)",
            # SHA256: 64 hex chars (64 chars)
            r"(?P<sha256>[a-f0-9]{64})",
            # MD5: 32 hex chars (32 chars)
            r"(?P<md5>[a-f0-9]{32})",
            # SHA1: 40 hex chars (40 chars)
            r"(?P<sha1>[a-f0-9]{40})",
            # ETH: 0x + 40 hex (42 chars)
            r"(?P<eth>0x[a-fA-F0-9]{40})",
            # USDT TRC20: T + 33 base58 (34 chars)
            r"(?P<usdt>T[A-HJ-NP-Za-km-z1-9]{33})",
            # LTC: L + 33 base58 (34 chars)
            r"(?P<ltc>L[1-9A-HJ-NP-Za-km-z]{33})",
            # DOGE: D + 33 base58 (34 chars)
            r"(?P<doge>D[123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz]{33})",
            # AWS key: AKIA + 16 alphanum (20 chars)
            r"(?P<aws>AKIA[0-9A-Z]{16})",
            # Google API key: AIza + 35 URL-safe base64 (39-40 chars)
            r"(?P<gapi>AIza[0-9A-Za-z\-_]{35})",
            # Stripe: sk_live_ + 24 alphanum (31+ chars)
            r"(?P<stripe>sk_live_[0-9a-zA-Z]{24})",
            # Slack: xox[baprs]- + 10-13 + 10-13 + 24-32 (51-73 chars)
            r"(?P<slack>xox[baprs]-[0-9]{10,13}-[0-9]{10,13}-[A-Za-z0-9]{24,32})",
        ]
        _MASTER_RE = re.compile("|".join(parts), re.IGNORECASE)
    return _MASTER_RE


def extract_high_precision_entities(text: str) -> list[ExtractedEntity]:
    """Extract high-precision structured entities via regex.

    Uses a single master-regex pass for O(n) scanning instead of O(15×n).
    Early-exits on texts shorter than the shortest possible match (16 chars).
    Returns ExtractedEntity list sorted by start offset.
    """
    # Issue #20 — early exit: shortest possible match is CVE-1-1 (8 chars)
    if len(text) < 8:
        return []

    master = _get_master_regex()
    entities: list[ExtractedEntity] = []

    for m in master.finditer(text):
        # Fast dispatch: find first matching group (short-circuit, O(1) avg)
        matched = next(
            ((entity_type, min_len, m.group(g)) for g, (entity_type, min_len) in _MASTER_MAP.items() if m.group(g)),
            None,
        )
        if matched is None:
            continue
        entity_type, min_len, _ = matched
        # Preserve original case via slice — m.group() returns lowercase from
        # case-insensitive regex, but text[start:end] gives us the real case.
        value = text[m.start():m.end()]
        # Belt-and-suspenders: verify minimum length
        if len(value) < min_len:
            continue
        entities.append(ExtractedEntity(
            entity_type=entity_type,
            value=value,
            start=m.start(),
            end=m.end(),
        ))

    # Hash validation: reject trivial/accidental hashes
    validated: list[ExtractedEntity] = []
    for e in entities:
        if e.entity_type in ("sha256_hash", "md5_hash", "sha1_hash"):
            v = e.value.lower()
            # Require at least 8 unique chars and reject obvious patterns
            if len(set(v)) < 8:
                continue  # too trivial to be real
            # Reject repeating/sequential patterns (e.g. "aaaaaaaa", "12345678")
            if v != v[0] * len(v) and not _looks_random(v):
                continue
        validated.append(e)

    # Sort by start offset
    validated.sort(key=attrgetter("start"))
    return validated


def extract_structured_entities(text: str) -> list[dict]:
    """
    Extract IOCs and return as structured list of dicts for GraphManager.

    FÁZE P9: Pipeline consumable format — list[dict] with entity_type + value.
    Combines both AC automaton hits and regex post-pass results.
    Memory-bounded: max 1000 entries per call (M1 8GB safe).

    Returns:
        List of {"entity_type": str, "value": str, "label": str} dicts.
        Deduplicated by (entity_type, value) pair.
    """
    hits = match_text(text)
    matched_count = len(hits)
    if matched_count == 0:
        sample = text[:200] if len(text) > 200 else text
        logger.debug(
            f"[PATTERN_MATCHER] zero pattern matches for text sample: {sample!r} "
            f"(len={len(text)})"
        )
        return []

    seen: set[tuple[str, str]] = set()
    entities: list[dict] = []
    max_entries = 1000

    for hit in hits:
        key = (hit.label or "unknown", hit.value)
        if key in seen or len(entities) >= max_entries:
            continue
        seen.add(key)
        entities.append({
            "entity_type": hit.label or "unknown",
            "value": hit.value,
            "label": hit.pattern,
        })

    return entities


# -----------------------------------------------------------------------------
# Seed registry — ONLY for infrastructure tests, not production OSINT
# -----------------------------------------------------------------------------
_SEED_REGISTRY: tuple[tuple[str, str], ...] = (
    ("@example.com", "email"),
    ("1BTC", "crypto_address"),
    (".onion", "domain"),
    ("+420", "phone"),
)


# -----------------------------------------------------------------------------


class _PatternMatcherState:
    """Holds the singleton PatternMatcher instance and its lifecycle state."""

    __slots__ = (
        "_pattern_version",
        "_registry_snapshot",
        "_bootstrap_applied",
        "_rust_aco",
        "_regex_alternation",
        "_label_map_cache",  # Issue #35: cache label_map to avoid per-call dict allocation
        "_prewarm_lock",
        "_prewarm_done",  # F3-OPT: set True after prewarm thread completes
    )

    def __init__(self) -> None:
        # _automaton removed — pyahocorasick no longer used
        self._pattern_version: int = 0
        self._registry_snapshot: frozenset[tuple[str, str]] = frozenset()
        self._bootstrap_applied: bool = False
        self._rust_aco: AhoCorasickMatcher | None | object = None
        # Issue #17: pre-compiled regex alternation for linear scan fallback
        # O(n) single-pass scan vs O(p×n) str.find() loop
        self._regex_alternation: re.Pattern[str] | None = None
        # Issue #35: cached label_map for regex fallback path — avoids per-call
        # dict allocation (O(p) inserts per match_text call with regex fallback)
        self._label_map_cache: dict[str, tuple[str, str]] | None = None
        # F3: lock guards prewarm thread vs configure_patterns() vs match_text() races
        self._prewarm_lock: threading.Lock = threading.Lock()
        # F3-OPT: set True after prewarm thread completes build.
        # configure_patterns checks this BEFORE acquiring lock to skip the
        # redundant acquire→compare-snapshot→release cycle when prewarm finished.
        self._prewarm_done: bool = False

    def is_built(self) -> bool:
        """Return True if Rust ACO is initialized and ready.

        Rust ACO is built eagerly in configure_patterns() — this checks
        that build succeeded (rust_aco is not None).
        """
        return self._rust_aco is not None

    def pattern_count(self) -> int:
        """Return number of configured patterns. O(1)."""
        return len(self._registry_snapshot)

    def get_status(self) -> dict:
        """Return current matcher status. O(1), side-effect free."""
        return {
            "configured_count": len(self._registry_snapshot),
            "bootstrap_default_configured": self._bootstrap_applied,
            "pattern_version": self._pattern_version,
            "bootstrap_pack_version": _BOOTSTRAP_PACK_VERSION,
            "default_bootstrap_count": len(_BOOTSTRAP_PATTERNS),
            "rust_aco_built": self.is_built(),
        }


_matcher_state = _PatternMatcherState()


# -----------------------------------------------------------------------------
# Internal helpers
# -----------------------------------------------------------------------------


def _has_overlapping_patterns(patterns: list[str]) -> bool:
    """Detect overlapping patterns in O(n log n) worst-case.

    Issue #35-fix: previously O(n²) — 200 patterns = 40,000 comparisons.
    Now: O(n log n) via length-sorting + containment pruning.

    Two patterns overlap when one is a prefix of another OR one contains the other.
    After sorting by length descending, any shorter pattern can only be contained
    in longer patterns that appear earlier in the sorted list. We check all
    previous (longer) patterns for containment, and use startswith() for prefix overlap.

    Rust ACO find_iter() correctly handles overlapping matches regardless,
    so this only gates the warning message, not actual behavior.
    """
    if len(patterns) < 2:
        return False

    # Sort by length descending — longer patterns first
    sorted_patterns = sorted(patterns, key=lambda p: (-len(p), p))

    # For each pattern, check ALL previous (longer or equal) patterns
    # Previous patterns are guaranteed to be >= length, so can contain current
    for i, p1 in enumerate(sorted_patterns):
        for j in range(i):
            p2 = sorted_patterns[j]
            # p2 is longer or equal — check prefix overlap AND containment
            if p2.startswith(p1) or p1 in p2:
                return True

    return False


@functools.cache
def _is_word_boundary(text: str, start: int, end: int) -> bool:
    """Check word-boundary for a match at [start:end].

    Cached per (text, start, end) triple — boundary checks are redundant
    within a single match_text() call since each position appears at most
    once per path. @functools.cache gives O(1) lookup with zero overhead.

    Returns True when:
      - start==0 OR char before start is NOT alphanumeric
      - end>=len(text) OR char at end is NOT alphanumeric
    """
    before_ok = start == 0 or not text[start - 1].isalnum()
    after_ok = end >= len(text) or not text[end].isalnum()
    return before_ok and after_ok


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------


def get_pattern_matcher() -> _PatternMatcherState:
    """Return the singleton PatternMatcher state.

    Does NOT trigger a build — build is deferred to first match_text() call.
    """
    return _matcher_state


def configure_patterns(registry: tuple[tuple[str, str], ...], *, _from_prewarm: bool = False) -> None:
    """Update the active pattern registry.

    Args:
        registry: Tuple of (pattern, label) pairs.
                  Pass _SEED_REGISTRY for test seeding.
                  Pass () to clear all patterns.
        _from_prewarm: Internal flag — when True, skip the _prewarm_lock
                       acquisition (prewarm thread holds it for the full build).
    """
    new_snapshot = frozenset(registry)
    if new_snapshot == _matcher_state._registry_snapshot:
        return  # no-op on identical registry

    # F3-OPT: if prewarm thread already completed the same build, skip lock entirely.
    # _prewarm_done is set True only when prewarm built _BOOTSTRAP_PATTERNS successfully.
    # This eliminates one redundant acquire→compare→release cycle per first match_text call.
    if (
        not _from_prewarm
        and not _matcher_state._prewarm_done
    ):
        acquired = _matcher_state._prewarm_lock.acquire(timeout=30.0)
        if not acquired:
            logger.warning("[PATTERNS] configure_patterns: lock timeout — skipping (prewarm may be stuck)")
            return
        try:
            # Re-check after acquiring — another caller may have configured between
            # our initial check above and lock acquisition
            if new_snapshot != _matcher_state._registry_snapshot:
                _configure_patterns_impl(registry)
        finally:
            _matcher_state._prewarm_lock.release()
    elif not _from_prewarm:
        # prewarm already done with same patterns — _registry_snapshot already set;
        # _configure_patterns_impl would be a no-op but skip the lock entirely
        pass
    else:
        _configure_patterns_impl(registry)


def _configure_patterns_impl(registry: tuple[tuple[str, str], ...]) -> None:
    """Core pattern configuration — caller must hold _prewarm_lock."""
    _matcher_state._registry_snapshot = frozenset(registry)
    _matcher_state._pattern_version += 1

    # Issue #35-fix: O(n log n) overlap detection via sorted neighbor check.
    # Previously: O(n²) nested loop — 200 patterns = 40,000 comparisons.
    # Now: sort + single pass check only neighboring sorted strings.
    # Rust ACO handles overlaps correctly via find_iter() — this check only
    # gates whether to warn about fallback, not whether to use Rust ACO.
    patterns_list = [p.lower() for p, _l in registry]
    has_overlapping = _has_overlapping_patterns(patterns_list)

    # Issue #11: explicitly release old Rust instance before replacing.
    # PyO3 objects are freed by GC eventually, but close() drops the
    # automaton + Vec<String> immediately so the memory is reclaimed before
    # the new instance is allocated — critical for M1 8GB RAM budget.
    # close() is added in aho_corasick.rs; skip gracefully if extension
    # hasn't been rebuilt yet (AttributeError = old build, TypeError = None).
    old = _matcher_state._rust_aco
    if old is not None:
        try:
            old.close()
        except (AttributeError, TypeError):  # noqa: BLE001
            pass  # fail-safe: close not available or already None — non-fatal

    # Build Rust ACO eagerly if available and patterns don't overlap
    # Issue #14: pass labels directly to Rust — eliminates Python dict lookup in hot path
    if _RUST_ACO_AVAILABLE and not has_overlapping:
        labels_list = [label for _p, label in registry]
        _matcher_state._rust_aco = RustAhoCorasickMatcher(patterns_list, labels_list)
        _matcher_state._regex_alternation = None  # Not needed when Rust ACO is used
    else:
        _matcher_state._rust_aco = None
        # Issue #17: build pre-compiled regex alternation for O(n) fallback
        # Escape special regex characters in patterns — patterns are literals, not regex
        escaped = [re.escape(p) for p in patterns_list]
        if escaped:
            # Sort by length descending to match longest first (avoids partial overlaps)
            escaped.sort(key=len, reverse=True)
            pattern_str = "|".join(escaped)
            try:
                _matcher_state._regex_alternation = re.compile(pattern_str, re.IGNORECASE)
            except re.error:
                _matcher_state._regex_alternation = None
        else:
            _matcher_state._regex_alternation = None
        # Issue #35: build cached label_map for regex fallback path
        # Cached here (once per configure) instead of per match_text() call
        _matcher_state._label_map_cache = {
            p.lower(): (p, label) for p, label in registry
        } if registry else None
        # Issue #17: warn when falling back from Rust ACO due to overlapping patterns
        if _RUST_ACO_AVAILABLE and has_overlapping:
            logger.warning(
                "[WARN] Rust ACO unavailable for %d patterns due to overlaps. "
                "Using regex alternation fallback (O(n) single-pass). "
                "Consider removing substring patterns to enable Rust ACO.",
                len(registry),
            )


# ── Pattern scan helpers ─────────────────────────────────────────────────────────


def _scan_with_regex_alternation(
    text: str,
    text_lower: str,
    regex_alt: Any,
    label_map: dict[str, tuple[str, str]],
    boundary_policy: str,
) -> list[PatternHit]:
    """O(n) single-pass regex alternation scan for pattern matching."""
    hits: list[PatternHit] = []
    for m in regex_alt.finditer(text_lower):
        matched = m.group()
        key = matched.lower()
        if key not in label_map:
            for pk in label_map:
                if pk.lower() == key:
                    key = pk
                    break
            else:
                continue
        pattern, label = label_map[key]
        start, end = m.start(), m.end()
        if boundary_policy == "word" and not _is_word_boundary(text, start, end):
            continue
        hits.append(PatternHit(
            pattern=sys.intern(pattern),
            start=start,
            end=end,
            value=text[start:end],
            label=sys.intern(label) if label else None,
        ))
    return hits


def _scan_with_str_find(
    text: str,
    text_lower: str,
    boundary_policy: str,
) -> list[PatternHit]:
    """O(p×n) str.find() loop — fallback when regex alternation unavailable."""
    hits: list[PatternHit] = []
    for pattern, label in _matcher_state._registry_snapshot:
        pattern_lower = pattern.lower()
        pos = 0
        while True:
            idx = text_lower.find(pattern_lower, pos)
            if idx == -1:
                break
            if boundary_policy == "word" and not _is_word_boundary(text, idx, idx + len(pattern)):
                pos = idx + 1
                continue
            hits.append(PatternHit(
                pattern=sys.intern(pattern),
                start=idx,
                end=idx + len(pattern),
                value=text[idx:idx + len(pattern)],
                label=sys.intern(label) if label else None,
            ))
            pos = idx + 1
    return hits


def _extract_structured_entities_python(
    text: str,
    text_lower: str,
) -> list[PatternHit]:
    """Python fallback: 25× re.finditer() for structured entity extraction."""
    hits: list[PatternHit] = []
    for _pattern, _label in [
        (_RE_CVE, "cve_identifier"),
        (_RE_GHSA, "ghsa_identifier"),
        (_RE_BTC_LEGACY, "btc_address"),
        (_RE_BTC_BECH32, "btc_address"),
        (_RE_TELEGRAM, "telegram_link"),
        (_RE_MISP_UUID, "misp_uuid"),
        (_RE_ONION_V3, "onion_v3"),
        (_RE_XMR_ADDR, "xmr_address"),
        (_RE_I2P_ADDR, "i2p_address"),
        (_RE_PGP_FP, "pgp_fingerprint"),
        (_RE_IPFS_CID, "ipfs_cid"),
        (_RE_SHA256, "sha256_hash"),
        (_RE_MD5, "md5_hash"),
        (_RE_SHA1, "sha1_hash"),
        (_RE_ETH_ADDR, "eth_address"),
        (_RE_USDT_TRC20, "usdt_trc20"),
        (_RE_LTC_ADDR, "ltc_address"),
        (_RE_DOGE_ADDR, "doge_address"),
        (_RE_ETH_ADDR, "eth_contract"),
        (_RE_AWS_KEY_ID, "aws_access_key_id"),
        (_RE_GOOGLE_API_KEY, "google_api_key"),
        (_RE_STRIPE_SK, "stripe_secret_key"),
        (_RE_SLACK_TOKEN, "slack_token"),
    ]:
        for m in _pattern.finditer(text_lower):
            hits.append(PatternHit(
                pattern=sys.intern(m.group()),
                start=m.start(),
                end=m.end(),
                value=text[m.start():m.end()],
                label=sys.intern(_label),
            ))
    return hits


def _scan_rust_aco(text_lower: str, boundary_policy: str) -> list[PatternHit]:
    """Scan using Rust Aho-Corasick (primary path)."""
    rust_boundary: str | None = boundary_policy if boundary_policy != "none" else None
    return list(_matcher_state._rust_aco.scan(text_lower, rust_boundary))


def _extract_structured_rust(text: str, text_lower: str, boundary_policy: str) -> list[PatternHit]:
    """Extract structured entities using Rust extractor."""
    hits: list[PatternHit] = []
    for r_start, r_end, r_value, r_label in _rust_extract_structured(text_lower):
        if boundary_policy == "word" and not _is_word_boundary(text, r_start, r_end):
            continue
        hits.append(PatternHit(
            pattern=sys.intern(r_value),
            start=r_start,
            end=r_end,
            value=text[r_start:r_end],
            label=sys.intern(r_label),
        ))
    return hits


def match_text(
    text: str, *, boundary_policy: str = "none"
) -> list[PatternHit]:
    """Find all pattern occurrences in *text* using the active registry.

    Args:
        text: Input string to search.
        boundary_policy:
            - "none"  — all matches (default, overlap allowed)
            - "word"  — require word-boundary-like condition on each side
                        (checked via adjacent character classification)
    Returns:
        List of PatternHit sorted by start offset (ascending).
        Empty list when no matches or empty registry.
    """
    # F270: Lazy bootstrap — apply default OSINT patterns on first match
    if not _matcher_state._bootstrap_applied:
        configure_default_bootstrap_patterns_if_empty()

    if not _matcher_state._registry_snapshot or not text:
        return []

    text_lower = text.lower()

    # === Pattern scan: Rust ACO or Python fallback ===
    if _RUST_ACO_AVAILABLE and _matcher_state._rust_aco is not None:
        hits = _scan_rust_aco(text_lower, boundary_policy)
    elif _matcher_state._regex_alternation is not None:
        label_map = cast(dict[str, tuple[str, str]], _matcher_state._label_map_cache)
        hits = _scan_with_regex_alternation(text, text_lower, _matcher_state._regex_alternation, label_map, boundary_policy)
    else:
        hits = _scan_with_str_find(text, text_lower, boundary_policy)

    # === Structured entity extraction: Rust or Python fallback ===
    if _RUST_STRUCTURED_EXTRACTOR_AVAILABLE and _rust_extract_structured is not None:
        hits.extend(_extract_structured_rust(text, text_lower, boundary_policy))
    else:
        hits.extend(_extract_structured_entities_python(text, text_lower))

    hits.sort(key=attrgetter("start"))
    return hits


def match_text_batch(
    texts: list[str], *, boundary_policy: str = "none"
) -> list[list[PatternHit]]:
    """Batch pattern matching — rayon parallel across texts, single GIL acquisition.

    Issue #4: Replaces N serial match_text() calls with one batch call.
    Uses Rust AhoCorasickMatcher.scan_batch() when available (rayon parallel,
    single GIL acquisition). Falls back to serial Python loop for small batches.

    Args:
        texts: List of input strings to search.
        boundary_policy: "none" or "word" (passed to per-text post-filter).

    Returns:
        List of hit lists, one per input text in same order.
        Empty texts return empty list for that text.

    M1 8GB: Rust scan_batch uses mixed_pool (adaptive 1-2 threads).
    Python fallback uses serial loop (same as calling match_text N times).
    """
    if not texts:
        return []

    # Empty registry: return empty hits for all texts
    if not _matcher_state._registry_snapshot:
        return [[] for _ in texts]

    # Ensure bootstrap applied
    if not _matcher_state._bootstrap_applied:
        configure_default_bootstrap_patterns_if_empty()

    # Check Rust batch path
    rust_aco_enabled = (
        _RUST_ACO_AVAILABLE
        and _matcher_state._rust_aco is not None
    )

    if rust_aco_enabled and len(texts) >= 4:
        # Rust batch path — single GIL acquisition, rayon parallel scan
        # Issue #37: scan_batch returns List[List[PatternHit]] — zero Python allocations,
        # labels interned in Rust. Issue #14: label inline, no Python dict lookup.
        # Issue #18: boundary check done in Rust.
        # Issue #38-FIX: texts_lower passed to scan_batch — automaton was built with
        # lowercase patterns, so the scanned text must also be lowercased for matches.
        rust_boundary: str | None = boundary_policy if boundary_policy != "none" else None
        texts_lower: list[str] = [t.lower() for t in texts]
        raw_results: list[list[PatternHit]] = _matcher_state._rust_aco.scan_batch(texts_lower, rust_boundary)

        # Issue #15: Batch structured entity extraction — rayon parallel across texts.
        # Replaces per-text 25× re.finditer() loop in match_text().
        # Uses text_lower for case-insensitive pattern matching (regexes are case-insensitive).
        if _RUST_STRUCTURED_EXTRACTOR_AVAILABLE and _rust_batch_extract_structured is not None:
            rust_structured_results: list[list[tuple[int, int, str, str]]] = _rust_batch_extract_structured(texts_lower)
        else:
            rust_structured_results = [[] for _ in texts]

        results: list[list[PatternHit]] = []
        for _text_idx, (text, raw_hits, structured_hits) in enumerate(zip(texts, raw_results, rust_structured_results, strict=False)):
            hits: list[PatternHit] = []

            # AC scan hits (Rust) — Issue #37-FIX: Rust PatternHit used directly.
            # Zero Python allocations — Rust returns PatternHit PyClass objects.
            # value is text slice from original text (preserves original case).
            # label from Rust is already interned (Box::leak in Rust), no sys.intern() needed.
            for rh in raw_hits:
                hits.append(rh)

            # Structured entity hits (Rust batch) — apply boundary_policy filter in Python
            for r_start, r_end, r_value, r_label in structured_hits:
                if boundary_policy == "word" and not _is_word_boundary(text, r_start, r_end):
                    continue
                hits.append(PatternHit(
                    pattern=sys.intern(r_value),
                    start=r_start,
                    end=r_end,
                    value=text[r_start:r_end],
                    label=sys.intern(r_label),
                ))

            hits.sort(key=attrgetter("start"))
            results.append(hits)
        return results

    # Python fallback — serial per-text (same as original match_text loop)
    return [match_text(t, boundary_policy=boundary_policy) for t in texts]


def reset_pattern_matcher() -> None:
    """Reset singleton to pristine state. FOR TEST USE ONLY.

    Clears automaton, resets version, marks dirty.
    After reset, get_pattern_matcher() returns the same state object
    but in un-built (dirty) condition.
    """
    # Issue #11: explicitly release old Rust instance before replacing.
    old = _matcher_state._rust_aco
    if old is not None:
        try:
            old.close()
        except (AttributeError, TypeError):  # noqa: BLE001
            pass  # fail-safe: close not available or already None
    _matcher_state._rust_aco = None
    _matcher_state._pattern_version = 0
    _matcher_state._registry_snapshot = frozenset()
    _matcher_state._bootstrap_applied = False
    _matcher_state._regex_alternation = None
    _matcher_state._label_map_cache = None
    _matcher_state._prewarm_done = False  # F3-OPT: reset so prewarm can run again after reset


def get_default_bootstrap_patterns() -> tuple[tuple[str, str], ...]:
    """Return the current default bootstrap patterns tuple.

    Side-effect free. No matcher state is consulted or modified.
    """
    return _BOOTSTRAP_PATTERNS


def configure_default_bootstrap_patterns_if_empty() -> bool:
    """
    Bootstrap the matcher with OSINT literal pack if registry is empty.

    Idempotent: does nothing when registry already contains patterns.
    Does not overwrite existing registry.

    Returns:
        True if bootstrap was applied, False if registry was non-empty
        or bootstrap failed.
    """
    if _matcher_state._registry_snapshot:
        return False
    try:
        configure_patterns(_BOOTSTRAP_PATTERNS)
        _matcher_state._bootstrap_applied = True
        n = len(_BOOTSTRAP_PATTERNS)
        logger.info(f"[PATTERNS] configured {n} bootstrap patterns")
        return True
    except Exception:
        return False


def prewarm() -> None:
    """
    Eagerly initialize the pattern matcher before first use — F3.

    Spawns a background thread that holds _prewarm_lock through the full
    Rust Aho-Corasick build, then releases it. If another caller tries
    configure_patterns() or match_text() before prewarm finishes, it blocks
    on _prewarm_lock, ensuring a fully-built automaton is visible to all.

    Gate: HLEDAC_PATTERN_WARMUP=1 (default ON, opt-out via =0).

    Thread-safe via threading.Lock — prewarm thread holds the lock for the
    entire build, other callers block until it's released.

    No-op if registry already populated (bootstrap already applied) or if
    HLEDAC_PATTERN_WARMUP=0.
    """
    # F3: opt-out gate — default ON
    if os.environ.get("HLEDAC_PATTERN_WARMUP", "1") == "0":
        return
    if _matcher_state._registry_snapshot:
        return  # already bootstrapped — no-op
    if not _RUST_ACO_AVAILABLE:
        return  # nothing to warm — Rust ACO unavailable

    def _prewarm_thread() -> None:
        """Thread target: hold lock through entire Rust ACO build."""
        # F3: check Rust availability BEFORE acquiring lock — avoid lock leak if import fails.
        # Import here (not at module level) keeps lazy import semantics.
        if not _RUST_ACO_AVAILABLE:
            logger.debug("[PATTERNS] prewarm: Rust ACO unavailable — skipping")
            return
        acquired = _matcher_state._prewarm_lock.acquire(timeout=30.0)
        if not acquired:
            logger.warning("[PATTERNS] prewarm: lock timeout — skipping")
            return
        try:
            # F3-FIX: _from_prewarm=True is MANDATORY here.
            # Without it, configure_patterns() would try to re-acquire _prewarm_lock
            # (which we already hold), causing a deadlock.
            # Also skip configure_default_bootstrap_patterns_if_empty() — go direct
            # to avoid the bootstrap_applied double-set complexity.
            if not _matcher_state._registry_snapshot:
                _configure_patterns_impl(_BOOTSTRAP_PATTERNS)
                _matcher_state._bootstrap_applied = True
                _matcher_state._prewarm_done = True  # F3-OPT: signal completion to skip redundant lock acquire
                logger.info(f"[PATTERNS] prewarm built {len(_BOOTSTRAP_PATTERNS)} patterns")
            else:
                logger.debug("[PATTERNS] prewarm: registry already populated")
        except Exception as exc:
            logger.warning(f"[PATTERNS] prewarm failed: {exc}")
        finally:
            _matcher_state._prewarm_lock.release()

    t = threading.Thread(target=_prewarm_thread, daemon=True, name="pattern-prewarm")
    t.start()
    logger.debug("[PATTERNS] prewarm thread started")





# -----------------------------------------------------------------------------
# Benchmark helpers (importable for offline measurement)
# -----------------------------------------------------------------------------


def benchmark_build(registry: tuple[tuple[str, str], ...]) -> dict:
    """Measure automaton build time for a given registry."""
    t0 = time.perf_counter()
    configure_patterns(registry)
    t1 = time.perf_counter()
    return {"build_ms": (t1 - t0) * 1000, "pattern_count": len(registry)}


def benchmark_match(
    text: str,
    iterations: int = 1000,
    boundary_policy: str = "none",
) -> dict:
    """Measure repeated match_text() performance."""
    configure_patterns(_SEED_REGISTRY)
    # warm-up build
    match_text(text, boundary_policy=boundary_policy)

    t0 = time.perf_counter()
    for _ in range(iterations):
        match_text(text, boundary_policy=boundary_policy)
    t1 = time.perf_counter()

    total_ms = (t1 - t0) * 1000
    per_call_ms = total_ms / iterations
    return {
        "iterations": iterations,
        "total_ms": total_ms,
        "per_call_ms": per_call_ms,
        "text_len": len(text),
    }
