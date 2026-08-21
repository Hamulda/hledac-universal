"""
runtime/acquisition/threat_dictionary.py

Threat actor + malware family dictionary with O(1) lookup.
Extracted from acquisition_strategy.py (original L183-311).

MODERNIZATION (Issue #18):
  - Module-level _THREAT_DICTIONARY stays read-only (no changes needed)
  - lookup_threat_entity() unchanged — already optimal
  - No lazy import needed: this module has no heavy deps
"""

# Bounded LRU cache for threat actors and malware families (M1 8GB safe)
# format: "name": ("type", ["alias1", "alias2", ...])
# max 500 entries total — bounded per invariant
_THREAT_DICTIONARY: dict[str, tuple[str, list[str]]] = {
    # Malware families — ransomware
    "lockbit": ("malware_family", ["lockbit 2.0", "lockbit3", "ldx"]),
    "lockbit 2.0": ("malware_family", ["lockbit", "lockbit3", "ldx"]),
    "lockbit3": ("malware_family", ["lockbit", "lockbit 2.0", "ldx"]),
    "conti": ("malware_family", ["conti ransomware", "wizard spider"]),
    "conti ransomware": ("malware_family", ["conti", "wizard spider"]),
    "wizard spider": ("malware_family", ["conti"]),
    "revil": ("malware_family", ["revil ransomware", "sodinokibi"]),
    "sodinokibi": ("malware_family", ["revil", "revil ransomware"]),
    "revil ransomware": ("malware_family", ["revil", "sodinokibi"]),
    "blackcat": ("malware_family", ["alphv", "blackcat ransomware"]),
    "alphv": ("malware_family", ["blackcat", "blackcat ransomware"]),
    "blackcat ransomware": ("malware_family", ["blackcat", "alphv"]),
    "clop": ("malware_family", ["clop ransomware", "clopv2"]),
    "clop ransomware": ("malware_family", ["clop"]),
    "hive": ("malware_family", ["hive ransomware"]),
    "hive ransomware": ("malware_family", ["hive"]),
    "ryuk": ("malware_family", ["ryuk ransomware"]),
    "ryuk ransomware": ("malware_family", ["ryuk"]),
    "ransomexx": ("malware_family", ["ransomexx", "nexway"]),
    "nexway": ("malware_family", ["ransomexx"]),
    "malware_family": ("malware_family", ["malware family"]),
    # Malware families — banking trojans / loaders
    "emotet": ("malware_family", ["emotet trojan", "heodo"]),
    "emotet trojan": ("malware_family", ["emotet"]),
    "heodo": ("malware_family", ["emotet"]),
    "qakbot": ("malware_family", ["qakbot trojan", "qbot"]),
    "qbot": ("malware_family", ["qakbot"]),
    "qakbot trojan": ("malware_family", ["qakbot"]),
    "icedid": ("malware_family", ["icedid trojan", "bokbot"]),
    "bokbot": ("malware_family", ["icedid"]),
    "icedid trojan": ("malware_family", ["icedid"]),
    "dridex": ("malware_family", ["dridex trojan", "bugat"]),
    "bugat": ("malware_family", ["dridex"]),
    "dridex trojan": ("malware_family", ["dridex"]),
    "trickbot": ("malware_family", ["trickbot trojan", "trickster"]),
    "trickbot trojan": ("malware_family", ["trickbot"]),
    "trickster": ("malware_family", ["trickbot"]),
    "raccoon stealer": ("malware_family", ["raccoon", "raccoon malware"]),
    "raccoon malware": ("malware_family", ["raccoon", "raccoon stealer"]),
    # Malware families — info stealers
    "raccoon": ("malware_family", ["raccoon stealer"]),
    "stealer": ("malware_family", ["stealer malware", "infostealer"]),
    "infostealer": ("malware_family", ["stealer", "infostealer malware"]),
    "vidar": ("malware_family", ["vidar stealer"]),
    "vidar stealer": ("malware_family", ["vidar"]),
    "aurora": ("malware_family", ["aurora stealer"]),
    "aurora stealer": ("malware_family", ["aurora"]),
    "redline": ("malware_family", ["redline stealer"]),
    "redline stealer": ("malware_family", ["redline"]),
    # Malware families — RATs
    "rat": ("malware_family", ["remote access trojan", "rat malware"]),
    "remote access trojan": ("malware_family", ["rat"]),
    "rat malware": ("malware_family", ["rat"]),
    "cobalt strike": ("malware_family", ["cobaltstrike", "cs"]),
    "cobaltstrike": ("malware_family", ["cobalt strike"]),
    "cs": ("malware_family", ["cobalt strike"]),
    "metasploit": ("malware_family", ["metasploit framework", "msf"]),
    "metasploit framework": ("malware_family", ["metasploit"]),
    "msf": ("malware_family", ["metasploit"]),
    # Threat actors — APT groups
    "apt29": ("threat_actor", ["cozy bear", "the dukens", "midnight blizzard"]),
    "cozy bear": ("threat_actor", ["apt29", "cozyduke", "midnight blizzard"]),
    "cozyduke": ("threat_actor", ["apt29", "cozy bear"]),
    "the dukens": ("threat_actor", ["apt29"]),
    "midnight blizzard": ("threat_actor", ["apt29", "cozy bear"]),
    "apt41": ("threat_actor", ["barium", "wicked panda", "zinc"]),
    "barium": ("threat_actor", ["apt41", "wicked panda"]),
    "wicked panda": ("threat_actor", ["apt41", "barium"]),
    "zinc": ("threat_actor", ["apt41", "lazarus group"]),
    "apt28": ("threat_actor", ["fancy bear", "sofacy", "sandworm"]),
    "fancy bear": ("threat_actor", ["apt28", "sofacy", "pawn storm"]),
    "sofacy": ("threat_actor", ["apt28", "fancy bear"]),
    "pawn storm": ("threat_actor", ["apt28", "fancy bear"]),
    "sandworm": ("threat_actor", ["apt28", "voodoo bear", "electrum"]),
    "voodoo bear": ("threat_actor", ["sandworm"]),
    "electrum": ("threat_actor", ["sandworm"]),
    "lazarus": ("threat_actor", ["lazarus group", "hidden cobra", "zinc"]),
    "lazarus group": ("threat_actor", ["lazarus", "hidden cobra"]),
    "hidden cobra": ("threat_actor", ["lazarus", "lazarus group"]),
    "fin7": ("threat_actor", ["carbanak", "fin7", "carbanak gang"]),
    "carbanak": ("threat_actor", ["fin7", "carbanak gang", "anunak"]),
    "carbanak gang": ("threat_actor", ["fin7", "carbanak"]),
    "anunak": ("threat_actor", ["carbanak", "fin7"]),
    "fin8": ("threat_actor", ["fin8", "punkey"]),
    "punkey": ("threat_actor", ["fin8"]),
    "apt17": ("threat_actor", ["apt17", "tailgater team"]),
    "tailgater team": ("threat_actor", ["apt17"]),
    "apt19": ("threat_actor", ["apt19", "joe team"]),
    "joe team": ("threat_actor", ["apt19"]),
    "apt32": ("threat_actor", ["apt32", "ocean lot"]),
    "ocean lot": ("threat_actor", ["apt32"]),
    "apt37": ("threat_actor", ["apt37", "reaper group", "geumseong"]),
    "reaper group": ("threat_actor", ["apt37"]),
    "geumseong": ("threat_actor", ["apt37"]),
    "apt38": ("threat_actor", ["apt38", "zinc", "lazarus group"]),
    "menu": ("malware_family", ["menu pass", "menupass"]),
    "menu pass": ("malware_family", ["menu", "menupass"]),
    "menupass": ("malware_family", ["menu", "menu pass"]),
    " FINSP": ("threat_actor", ["fin6", "fin6"]),
    "fin6": ("threat_actor", ["finspy", "finsp"]),
    "finspy": ("threat_actor", ["fin6", "finsp"]),
    "leaf": ("threat_actor", ["luminous", "leaf"]),
    "luminous": ("threat_actor", ["leaf", "luminous"]),
    "thrip": ("threat_actor", ["thrip", "thrip"]),
    "tick": ("threat_actor", ["tick", "tick"]),
    "磷": ("threat_actor", ["tick", "tick"]),
    "temp.jockey": ("threat_actor", ["temp.jockey", "jockey"]),
    "jockey": ("threat_actor", ["temp.jockey", "jockey"]),
    # Nation-state / espionage
    "stuxnet": ("malware_family", ["stuxnet worm"]),
    "stuxnet worm": ("malware_family", ["stuxnet"]),
    "duqu": ("malware_family", ["duqu trojan"]),
    "duqu trojan": ("malware_family", ["duqu"]),
    "flame": ("malware_family", ["flame trojan", "flamer"]),
    "flame trojan": ("malware_family", ["flame"]),
    "flamer": ("malware_family", ["flame"]),
    "wannacry": ("malware_family", ["wannacry ransomware", "wanna cry"]),
    "wannacry ransomware": ("malware_family", ["wannacry", "wanna cry"]),
    "wanna cry": ("malware_family", ["wannacry", "wannacry ransomware"]),
    "notpetya": ("malware_family", ["notpetya", "petya"]),
    "petya": ("malware_family", ["notpetya", "petya"]),
    "bad rabbit": ("malware_family", ["badrabbit", "bad rabbit ransomware"]),
    "badrabbit": ("malware_family", ["bad rabbit"]),
    # Nation-state actors
    "menu": ("threat_actor", ["menu pass", "menupass"]),
    "temp.jockey": ("threat_actor", ["jockey", "temp jockey"]),
    "darkhotel": ("threat_actor", ["darkhotel", "dark hotel"]),
    "dark hotel": ("threat_actor", ["darkhotel"]),
    "patchwork": ("threat_actor", ["patchwork", "dropping elephant"]),
    "dropping elephant": ("threat_actor", ["patchwork"]),
    "tick": ("threat_actor", ["tick", "thrip"]),
    "thrip": ("threat_actor", ["tick", "thrip"]),
    "lazarus": ("threat_actor", ["lazarus group", "hidden cobra"]),
    # Additional aliases / expansions
    "wizard spider": ("threat_actor", ["conti", "wizard spider"]),
}


def lookup_threat_entity(name: str) -> tuple[str, str] | None:
    """
    O(1) dict lookup for threat entity type + canonical name.

    Returns (entity_type, canonical_name) if found, None otherwise.

    GHOST_INVARIANTS:
      - No network I/O, no model/MLX load
      - Fail-safe: returns None for unknown entities
      - Deterministic: same input always same output
    """
    if not name:
        return None
    entry = _THREAT_DICTIONARY.get(name.lower())
    if entry is None:
        return None
    entity_type, aliases = entry
    return entity_type, name.lower()
