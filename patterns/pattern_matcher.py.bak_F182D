"""
PatternMatcher singleton with pyahocorasick backend.

Pattern intelligence baseline — §8 first sprint.
Scope: ONLY this module and tests/probe_8x/.
No AO imports, no transport imports, no network access.
"""

from __future__ import annotations

import logging
import logging
import re
import sys
import time
from typing import NamedTuple

import ahocorasick

logger = logging.getLogger(__name__)

__all__ = [
    "PatternHit",
    "get_pattern_matcher",
    "configure_patterns",
    "match_text",
    "reset_pattern_matcher",
    "get_backend_info",
    "configure_default_bootstrap_patterns_if_empty",
    "get_default_bootstrap_patterns",
    "extract_high_precision_entities",
]

# -----------------------------------------------------------------------------
# Backend truth
# -----------------------------------------------------------------------------
BACKEND_AVAILABLE = True
BACKEND_VERSION = getattr(ahocorasick, "__version__", "unknown")


def get_backend_info() -> dict:
    return {
        "backend": "pyahocorasick",
        "version": BACKEND_VERSION,
        "available": BACKEND_AVAILABLE,
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
_RE_ONION_V3 = re.compile(
    r"[a-z2-7]{56}\.onion", re.IGNORECASE
)
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
_RE_BTC_BECH32 = re.compile(r"\bbc1[ac-hj-np-z02-9]{11,71}\b", re.IGNORECASE)
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
# Identical regex to _RE_ETH_ADDR — labeled distinctly for contract vs EOA context
_RE_ETH_CONTRACT = re.compile(r"\b0x[a-fA-F0-9]{40}\b")



class ExtractedEntity(NamedTuple):
    """High-precision entity extracted via regex post-processing."""
    entity_type: str
    value: str
    start: int
    end: int


def extract_high_precision_entities(text: str) -> list[ExtractedEntity]:
    """Extract high-precision structured entities via regex.

    Covers: CVE, GHSA, onion v3, SHA256, MD5, SHA1, ETH.
    Returns ExtractedEntity list sorted by start offset.
    """
    entities: list[ExtractedEntity] = []

    for pattern, entity_type in [
        (_RE_CVE, "cve_identifier"),
        (_RE_GHSA, "ghsa_identifier"),
        (_RE_ONION_V3, "onion_v3_address"),
        (_RE_SHA256, "sha256_hash"),
        (_RE_MD5, "md5_hash"),
        (_RE_SHA1, "sha1_hash"),
        (_RE_ETH_ADDR, "eth_address"),
        # Sprint F165A — new structured IOC coverage
        (_RE_USDT_TRC20, "usdt_trc20"),
        (_RE_LTC_ADDR, "ltc_address"),
        (_RE_DOGE_ADDR, "doge_address"),
        (_RE_ETH_CONTRACT, "eth_contract"),
    ]:
        for m in pattern.finditer(text):
            entities.append(ExtractedEntity(
                entity_type=entity_type,
                value=m.group(),
                start=m.start(),
                end=m.end(),
            ))

    # Hash validation: reject if value looks ambiguous (all zeros, repeating, etc.)
    validated: list[ExtractedEntity] = []
    for e in entities:
        if e.entity_type in ("sha256_hash", "md5_hash", "sha1_hash"):
            v = e.value.lower()
            # Reject trivial hashes: all same char, all zeros, sequential
            if len(set(v)) < 4:
                continue  # too trivial to be real
        validated.append(e)

    # Sort by start offset
    validated.sort(key=lambda x: x.start)
    return validated


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
# Singleton state
# -----------------------------------------------------------------------------
class _PatternMatcherState:
    """Holds the singleton PatternMatcher instance and its lifecycle state."""

    __slots__ = ("_automaton", "_pattern_version", "_registry_snapshot", "_dirty", "_bootstrap_applied")

    def __init__(self) -> None:
        self._automaton: ahocorasick.Automaton | None = None
        self._pattern_version: int = 0
        self._registry_snapshot: frozenset[tuple[str, str]] = frozenset()
        self._dirty: bool = True  # needs rebuild on first match
        self._bootstrap_applied: bool = False

    def pattern_count(self) -> int:
        """Return number of configured patterns. O(1)."""
        return len(self._registry_snapshot)

    def get_status(self) -> dict:
        """Return current matcher status. O(1), side-effect free."""
        return {
            "configured_count": len(self._registry_snapshot),
            "bootstrap_default_configured": self._bootstrap_applied,
            "dirty": self._dirty,
            "pattern_version": self._pattern_version,
            "bootstrap_pack_version": _BOOTSTRAP_PACK_VERSION,
            "default_bootstrap_count": len(_BOOTSTRAP_PATTERNS),
        }


_matcher_state = _PatternMatcherState()


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------


def get_pattern_matcher() -> _PatternMatcherState:
    """Return the singleton PatternMatcher state.

    Does NOT trigger a build — build is deferred to first match_text() call.
    """
    return _matcher_state


def configure_patterns(registry: tuple[tuple[str, str], ...]) -> None:
    """Update the active pattern registry and mark matcher for lazy rebuild.

    Args:
        registry: Tuple of (pattern, label) pairs.
                  Pass _SEED_REGISTRY for test seeding.
                  Pass () to clear all patterns.
    """
    new_snapshot = frozenset(registry)
    if new_snapshot == _matcher_state._registry_snapshot:
        return  # no-op on identical registry
    _matcher_state._registry_snapshot = new_snapshot
    _matcher_state._pattern_version += 1
    _matcher_state._dirty = True


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
    if not _matcher_state._registry_snapshot or not text:
        return []

    # Lazy build
    if _matcher_state._dirty:
        _build_automaton()

    automaton = _matcher_state._automaton
    assert automaton is not None

    hits: list[PatternHit] = []

    # Case-insensitive search: normalize text once
    text_lower = text.lower()

    for end_idx, (pattern, label) in automaton.iter(text_lower):
        start_idx = end_idx - len(pattern) + 1
        value = text[start_idx:end_idx + 1]

        # Boundary post-check
        if boundary_policy == "word":
            before_ok = start_idx == 0 or not text[start_idx - 1].isalnum()
            after_ok = (end_idx + 1) >= len(text) or not text[end_idx + 1].isalnum()
            if not (before_ok and after_ok):
                continue

        hits.append(
            PatternHit(
                pattern=sys.intern(pattern),
                start=start_idx,
                end=end_idx + 1,
                value=value,
                label=sys.intern(label) if label else None,
            )
        )

    # Sprint 8QB V4 + Sprint 8SC V5: regex post-pass for structured patterns
    # Run after AC scan so both literal+regex hits are returned
    text_lower = text.lower()
    for _pattern, _label in [
        (_RE_CVE, "cve_identifier"),
        (_RE_GHSA, "ghsa_identifier"),
        (_RE_BTC_LEGACY, "btc_address"),
        (_RE_BTC_BECH32, "btc_address"),
        (_RE_TELEGRAM, "telegram_link"),
        (_RE_MISP_UUID, "misp_uuid"),
        (_RE_ONION_V3, "onion_v3"),
        # Sprint 8SC V5
        (_RE_XMR_ADDR, "xmr_address"),
        (_RE_I2P_ADDR, "i2p_address"),
        (_RE_PGP_FP, "pgp_fingerprint"),
        (_RE_IPFS_CID, "ipfs_cid"),
        # Sprint F160B — structured IOC hot-path wiring
        (_RE_SHA256, "sha256_hash"),
        (_RE_MD5, "md5_hash"),
        (_RE_SHA1, "sha1_hash"),
        (_RE_ETH_ADDR, "eth_address"),
        # Sprint F165A — new structured IOC coverage
        (_RE_USDT_TRC20, "usdt_trc20"),
        (_RE_LTC_ADDR, "ltc_address"),
        (_RE_DOGE_ADDR, "doge_address"),
        (_RE_ETH_CONTRACT, "eth_contract"),
    ]:
        for m in _pattern.finditer(text_lower):
            hits.append(PatternHit(
                pattern=sys.intern(m.group()),
                start=m.start(),
                end=m.end(),
                value=m.group(),
                label=sys.intern(_label),
            ))

    # Sort by start offset
    hits.sort(key=lambda h: h.start)
    return hits


def reset_pattern_matcher() -> None:
    """Reset singleton to pristine state. FOR TEST USE ONLY.

    Clears automaton, resets version, marks dirty.
    After reset, get_pattern_matcher() returns the same state object
    but in un-built (dirty) condition.
    """
    _matcher_state._automaton = None
    _matcher_state._pattern_version = 0
    _matcher_state._registry_snapshot = frozenset()
    _matcher_state._dirty = True
    _matcher_state._bootstrap_applied = False


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


def _build_automaton() -> None:
    """Build or rebuild the pyahocorasick automaton from current registry snapshot."""
    automaton = ahocorasick.Automaton()

    # Normalize patterns to lowercase for case-insensitive matching
    for pattern, label in _matcher_state._registry_snapshot:
        pattern_lower = pattern.lower()
        automaton.add_word(pattern_lower, (pattern_lower, label))

    automaton.make_automaton()
    _matcher_state._automaton = automaton
    _matcher_state._dirty = False


# -----------------------------------------------------------------------------
# Benchmark helpers (importable for offline measurement)
# -----------------------------------------------------------------------------


def benchmark_build(registry: tuple[tuple[str, str], ...]) -> dict:
    """Measure automaton build time for a given registry."""
    configure_patterns(registry)
    t0 = time.perf_counter()
    _build_automaton()
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
