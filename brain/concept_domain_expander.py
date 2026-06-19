# Concept→Domain Expansion Pre-Phase (P0-2)
# ===========================================
# Problem: CT/DOH/WAYBACK lanes need domain/IP/URL seeds.
# Concept queries ("ransomware leak dark web") produce no literal domains.
# Fix: Before build_acquisition_plan(), run MLX inference to generate
# synthetic domain candidates, enabling CT/DOH/WAYBACK for concept queries.
#
# ARCHITECTURE:
#   sprint_scheduler.run_internal() [~line 6685]
#     └── expand_concept_domains(query, hermes_engine)
#           ├── MLX path: hermes_engine.generate(prompt) → domain list
#           └── Fast heuristic fallback: pattern matching on query
#
# INVARIANTS:
#   • Always-on: no feature flag, no toggle
#   • Bounded: max 5 synthetic domains, max 512 tokens prompt, 15s timeout
#   • Fail-safe: returns [] on any error, never raises
#   • M1 8GB: single inference call, no batch, memory guard checked
#   • No new public APIs beyond expand_concept_domains()
#   • Result fed to NonfeedSeedContext.domains prepending (line ~12904)

from __future__ import annotations

import re
from typing import Any

logger = __import__("logging").getLogger(__name__)

# ── Domain pattern heuristics ──────────────────────────────────────────────────

# TLDs commonly associated with OSINT-relevant topics
_OSINT_RELEVANT_TLDS = frozenset({
    "com", "org", "net", "io", "co", "ai", "app", "dev",
    "onion",        # dark web (kept for reference, excluded from CT/DOH)
    "lib", "page", "site", "blog", "news", "alert",
})

# Known brand/entity patterns that suggest domain generation candidates
_BRAND_TLD_RE = re.compile(
    r"(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}",
    re.ASCII,
)

# Suspicious/new TLDs often used in OSINT
_SUSPICIOUS_TLDS = frozenset({
    # Truly problematic TLDs (free registrars, high spam)
    "tk", "ml", "ga", "cf", "gq",  # free country-code TLDs
    "work", "date", "racing", "download", "stream",  # high-abuse
})

# Context-aware TLD allowlist for OSINT (less restrictive than before)
_OSINT_TLD_ALLOWLIST = frozenset({
    "com", "org", "net", "io", "co", "ai", "app", "dev",
    "lib", "page", "site", "blog", "news", "alert", "info",
    "biz", "cc", "tv", "me", "us", "uk", "eu", "ru", "cn",
})

# ── Prompt template ────────────────────────────────────────────────────────────

_CONCEPT_DOMAIN_PROMPT = """You are an OSINT domain expansion specialist. Given a research query, generate exactly 5 domain name candidates relevant to the topic.

Research query: {query}

Rules:
- Only output domain names, one per line, nothing else
- Use real, plausible domain patterns (e.g., threatblog.com, ransomware-tracker.io, leakwatch.org)
- Include both broad (e.g., securityblog.com) and specific (e.g., ransomware-hunt.net) patterns
- Do NOT invent fake TLDs — use: com, org, net, io, co, app, dev, lib, site, blog, news, alert
- Do NOT include .onion domains

Output exactly 5 domains:"""


# ── Synthetic domain candidate ─────────────────────────────────────────────────

class SyntheticDomainCandidate:
    """A domain candidate generated from a concept query."""

    __slots__ = ("domain", "confidence", "source", "reason")

    def __init__(
        self,
        domain: str,
        confidence: float,
        source: str,
        reason: str,
    ) -> None:
        self.domain = domain
        self.confidence = confidence  # 0.0–1.0
        self.source = source  # "mlx" | "heuristic"
        self.reason = reason  # human-readable why this domain was generated


# ── Heuristic domain expansion ─────────────────────────────────────────────────

# OSINT-relevant keyword mappings → domain templates
# Expanded coverage for threat intelligence queries (P2-2)
_KEYWORD_DOMAIN_TEMPLATES: tuple[tuple[frozenset[str], list[str]], ...] = (
    # Ransomware / Malware
    (
        frozenset({
            "ransomware", "ransom", "malware", "lockbit", "alphv", "blackcat",
            "conti", "revil", "sodinokibi", "wannacry", "wannacryptor",
            "petya", "notpetya", "badrabbit", "emotet", "trickbot", "qakbot",
        }),
        [
            "{kw}blog.com", "{kw}tracker.com", "{kw}leak.com",
            "{kw}-tracker.io", "{kw}alert.com", "{kw}watch.org",
            "{kw}intel.io", "{kw}news.com", "ransomware-{kw}.com",
            "{kw}monitor.net", "{kw}report.com",
        ],
    ),
    # Data Breach / Leak
    (
        frozenset({
            "breach", "leak", "exposed", "dump", "data breach",
            "credential", "password dump", "account leak", "database leak",
            "sensitive data", "个人信息泄露", "数据泄露",
        }),
        [
            "{kw}leak.com", "{kw}watch.org", "{kw}tracker.io",
            "{kw}alert.com", "{kw}monitor.com", "{kw}intel.com",
            "breach{kw}.com", "{kw}db.com", "{kw}exposed.com",
            "{kw}alert.io", "leak{kw}.com",
        ],
    ),
    # Dark Web / Underground
    (
        frozenset({
            "dark web", "darkweb", "tor", "onion", "underground",
            "illegal market", "carding", "hacking forum", "cyber crime forum",
        }),
        [
            "dark{kw}.com", "{kw}leak.com", "hidden{kw}.io",
            "underground{kw}.net", "{kw}alert.com", "{kw}market.io",
            "cyber{kw}.net", "{kw}underground.com",
        ],
    ),
    # APT / Nation-State
    (
        frozenset({
            "apt", "nation-state", "state-sponsored", "cyber espionage",
            "lazarus", "fancybear", "cozybear", "apt29", "apt41",
            "lazarus group", "comment crew", "equation group",
        }),
        [
            "{kw}tracker.com", "{kw}intel.org", "{kw}report.com",
            "{kw}alert.net", "{kw}monitor.io", "{kw}research.org",
            "{kw}group.com", "apt{kw}.io",
        ],
    ),
    # Phishing / Fraud
    (
        frozenset({
            "phishing", "phish", "spam", "scam", "fraud", "social engineering",
            "spear phishing", " BEC", "business email compromise",
        }),
        [
            "{kw}alert.com", "{kw}tracker.io", "{kw}report.org",
            "{kw}watch.net", "{kw}db.com", "phish{kw}.com",
            "{kw}lookup.com", "{kw}verify.com",
        ],
    ),
    # Vulnerability / CVE
    (
        frozenset({
            "cve", "vulnerability", "exploit", "零day", "0day", "unpatched",
            "nday", "pill", "cve-", "security flaw", "remote code execution",
        }),
        [
            "{kw}tracker.com", "{kw}db.com", "{kw}alert.org",
            "{kw}intel.net", "{kw}monitor.io", "vuln{kw}.com",
            "{kw}exploit.com", "{kw}patch.com",
        ],
    ),
    # OSINT / Reconnaissance
    (
        frozenset({
            "osint", "recon", "footprint", "surface", "mapping",
            "reconnaissance", "information gathering", "asset discovery",
        }),
        [
            "{kw}intel.com", "{kw}probe.io", "{kw}scan.net",
            "{kw}map.org", "{kw}tracker.dev", "{kw}search.com",
            "{kw}gather.com", "{kw}discover.io",
        ],
    ),
    # Threat Intelligence / CTI
    (
        frozenset({
            "cti", "threat intel", "threat intelligence", "ti feed",
            "stix", "taxii", "threat actor", "threat group", "indicator",
        }),
        [
            "{kw}feed.com", "{kw}intel.org", "{kw}platform.io",
            "{kw}portal.net", "{kw}api.dev", "{kw}hub.io",
            "{kw}intel.io", "{kw}threat.com",
        ],
    ),
    # Security / Infosec
    (
        frozenset({
            "infosec", "security", "cyber", "hacking", "sec",
            "infosec news", "cybersecurity", "cyber security",
        }),
        [
            "{kw}blog.com", "{kw}news.com", "{kw}alert.org",
            "{kw}report.net", "{kw}intel.io", "{kw}portal.dev",
            "{kw}watch.com", "{kw}cyber.com",
        ],
    ),
    # ICS / OT / IoT
    (
        frozenset({
            "iot", "ics", "scada", "industrial", "operational technology",
            "Operational Technology", "ics security", "ot security",
        }),
        [
            "{kw}monitor.io", "{kw}alert.com", "{kw}tracker.org",
            "{kw}intel.net", "{kw}scan.dev", "{kw}security.io",
            "{kw}ot.com", "{kw}ics.io",
        ],
    ),
    # Social Media / Social OSINT
    (
        frozenset({
            "social media", "social network", "osint social", "social",
            "twitter osint", "linkedin osint", "facebook osint",
            "instagram osint", "social engineering",
        }),
        [
            "{kw}social.com", "{kw}intel.io", "{kw}track.net",
            "{kw}monitor.org", "{kw}alert.dev", "{kw}lookup.com",
            "{kw}profile.io", "{kw}account.com",
        ],
    ),
    # Botnet / C2
    (
        frozenset({
            "botnet", "c2", "command and control", "command & control",
            "c&c", "bot", "zombie network", "ddos",
        }),
        [
            "{kw}tracker.com", "{kw}intel.io", "{kw}monitor.net",
            "{kw}alert.org", "{kw}block.com", "{kw}c2.io",
        ],
    ),
    # Data Theft / Exfiltration
    (
        frozenset({
            "data theft", "exfiltration", "data leak", "insider threat",
            "corporate espionage", "intellectual property theft",
        }),
        [
            "{kw}leak.com", "{kw}watch.org", "{kw}alert.io",
            "{kw}monitor.com", "{kw}detect.net", "{kw}insider.com",
        ],
    ),
    # Threat Group / Hacker Group
    (
        frozenset({
            "threat group", "hacker group", "cyber criminal", "cybercriminal",
            "organized crime", "ransomware group", "attack group",
        }),
        [
            "{kw}group.com", "{kw}intel.io", "{kw}alert.com",
            "{kw}tracker.net", "{kw}monitor.org", "{kw}gang.com",
        ],
    ),
    # Supply Chain Attack
    (
        frozenset({
            "supply chain", "supplychain", "third party", "vendor breach",
            "software supply chain", "dependency confusion",
        }),
        [
            "{kw}supply.com", "{kw}chain.io", "{kw}vendor.com",
            "{kw}thirdparty.com", "{kw}dependency.com", "{kw}trust.io",
        ],
    ),
    # Cryptocurrency / Blockchain
    (
        frozenset({
            "cryptocurrency", "crypto", "bitcoin", "ethereum", "wallet",
            "blockchain", "crypto scam", "mixer", "tumbler",
        }),
        [
            "{kw}chain.com", "{kw}wallet.io", "{kw}tracker.net",
            "{kw}crypto.com", "{kw}block.io", "{kw}coin.com",
        ],
    ),
    # Identity / Credential
    (
        frozenset({
            "identity theft", "credential stuffing", "account takeover",
            "password reuse", " credential", "identity fraud",
        }),
        [
            "{kw}identity.com", "{kw}credential.com", "{kw}tracker.io",
            "{kw}monitor.com", "{kw}alert.org", "{kw}theft.com",
        ],
    ),
    # Web Shell / Backdoor
    (
        frozenset({
            "web shell", "webshell", "backdoor", "rootkit", "persistence", "lateral movement", "post-exploitation",
        }),
        [
            "{kw}shell.com", "{kw}backdoor.io", "{kw}tracker.net",
            "{kw}monitor.org", "{kw}alert.com", "{kw}root.com",
        ],
    ),
)


def _heuristic_expand_concept(query: str) -> list[SyntheticDomainCandidate]:
    """
    Fast heuristic domain expansion without MLX.

    Extracts domain-like tokens from the query and generates plausible
    OSINT-relevant domain patterns.

    Algorithm:
      1. Tokenize query into n-grams (1-3 words)
      2. For each n-gram, generate domain patterns with OSINT TLDs
      3. Score by relevance heuristics (keyword matching, suspicious TLDs penalized)
      4. Return top 5

    Returns:
        List of SyntheticDomainCandidate (may be empty).
    """
    if not query or not isinstance(query, str):
        return []

    candidates: list[SyntheticDomainCandidate] = []
    seen: set[str] = set()

    query_lower = query.lower()

    # Extract key terms from query for n-gram generation
    words = re.findall(r"[a-zA-Z0-9]{2,}", query_lower)
    ngrams: list[str] = []
    for n in (3, 2, 1):  # prefer longer n-grams
        for i in range(len(words) - n + 1):
            ngrams.append("".join(words[i:i + n]))

    # Score keyword matches
    keyword_scores: dict[str, float] = {}
    for kw_set, _ in _KEYWORD_DOMAIN_TEMPLATES:
        for word in words:
            if word in kw_set:
                keyword_scores[word] = keyword_scores.get(word, 0) + 1.0

    # Generate candidates from templates
    for kw_set, templates in _KEYWORD_DOMAIN_TEMPLATES:
        matched_words = [w for w in words if w in kw_set]
        if not matched_words:
            continue
        best_word = max(matched_words, key=lambda w: keyword_scores.get(w, 0))
        for template in templates:
            domain = template.replace("{kw}", best_word)
            if domain in seen:
                continue
            seen.add(domain)
            # Penalize suspicious TLDs
            tld = domain.rsplit(".", 1)[-1] if "." in domain else ""
            tld_penalty = 0.2 if tld in _SUSPICIOUS_TLDS else 0.0
            confidence = 0.75 - tld_penalty
            candidates.append(
                SyntheticDomainCandidate(
                    domain=domain,
                    confidence=confidence,
                    source="heuristic",
                    reason=f"keyword_template:{best_word}",
                )
            )

    # Also add pure n-gram based candidates if we have few results
    if len(candidates) < 3:
        for ngram in ngrams[:10]:
            if len(ngram) < 4:
                continue
            for tld in ("com", "org", "net", "io"):
                domain = f"{ngram}.{tld}"
                if domain in seen:
                    continue
                seen.add(domain)
                candidates.append(
                    SyntheticDomainCandidate(
                        domain=domain,
                        confidence=0.5,
                        source="heuristic",
                        reason=f"ngram:{ngram}",
                    )
                )

    # Sort by confidence, return top 5
    candidates.sort(key=lambda c: c.confidence, reverse=True)
    return candidates[:5]


# ── MLX-based expansion ────────────────────────────────────────────────────────

async def _mlx_expand_concept(
    query: str,
    hermes_engine: Any,
    timeout_s: float = 15.0,
) -> list[SyntheticDomainCandidate]:
    """
    MLX-based concept→domain expansion using Hermes-3.

    Sends a structured prompt to Hermes-3 requesting 5 domain candidates
    relevant to the research query.

    Args:
        query: Original research query string
        hermes_engine: DeepHermes3Engine instance (must be loaded)
        timeout_s: Max time to wait for inference

    Returns:
        List of SyntheticDomainCandidate from MLX generation.
        Falls back to [] on any error (fail-safe).
    """
    if hermes_engine is None:
        return []

    try:
        import asyncio

        prompt = _CONCEPT_DOMAIN_PROMPT.format(query=query[:500])  # cap query length

        # Run inference with timeout
        result = await asyncio.wait_for(
            hermes_engine.generate(
                prompt,
                temperature=0.3,  # low temp for deterministic domain names
                max_tokens=128,   # 5 domains × ~20 chars + newlines
                system_msg="You are an OSINT domain analyst. Output only domain names.",
                thinking=False,   # fast path, no deep thinking needed
            ),
            timeout=timeout_s,
        )

        candidates: list[SyntheticDomainCandidate] = []
        seen: set[str] = set()

        for line in result.splitlines():
            line = line.strip().rstrip(".")
            if not line or len(line) < 4 or len(line) > 63:
                continue
            # Validate domain-like structure
            if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9.-]*\.[a-zA-Z]{2,}$", line):
                continue
            domain = line.lower()
            if domain in seen:
                continue
            # Reject .onion (can't CT/DOH-query TOR)
            if domain.endswith(".onion"):
                continue
            # Reject suspicious TLDs
            tld = domain.rsplit(".", 1)[-1]
            if tld in _SUSPICIOUS_TLDS:
                continue
            seen.add(domain)
            candidates.append(
                SyntheticDomainCandidate(
                    domain=domain,
                    confidence=0.8,
                    source="mlx",
                    reason="llm_generated",
                )
            )

        return candidates[:5]

    except TimeoutError:
        logger.debug("[P0-2] MLX domain expansion timed out after %ss", timeout_s)
        return []
    except Exception as exc:
        logger.debug("[P0-2] MLX domain expansion failed: %s", exc)
        return []


# ── Main entry point ───────────────────────────────────────────────────────────

async def expand_concept_domains(
    query: str,
    hermes_engine: Any | None = None,
    timeout_s: float = 15.0,
) -> list[SyntheticDomainCandidate]:
    """
    Expand a concept query into synthetic domain candidates.

    Tries MLX (Hermes-3) first if hermes_engine is available and loaded,
    otherwise falls back to fast heuristic expansion.

    This function is called BEFORE build_acquisition_plan() in the sprint
    scheduler so that the resulting domains can enable CT/DOH/WAYBACK lanes.

    Args:
        query: The research query string (e.g., "ransomware leak dark web")
        hermes_engine: Optional DeepHermes3Engine instance. If None or not
                       loaded, heuristic expansion is used.
        timeout_s: Max inference time for MLX path (default 15s).

    Returns:
        List of SyntheticDomainCandidate (max 5), sorted by confidence desc.
        Never returns None. Empty list if no candidates could be generated.

    Invariants:
        • Always-on: no feature flag
        • Bounded: max 5 candidates, 128 token generation
        • Fail-safe: returns [] on any error, never raises
        • M1 8GB safe: single inference call, no batching
        • Deterministic order: sorted by confidence descending
    """
    if not query or not isinstance(query, str) or not query.strip():
        return []

    # Check if hermes_engine is actually loaded (not None and has model)
    _engine_loaded = (
        hermes_engine is not None
        and hasattr(hermes_engine, "_model")
        and hermes_engine._model is not None
    )

    if _engine_loaded:
        # Try MLX path first
        mlx_candidates = await _mlx_expand_concept(query, hermes_engine, timeout_s)
        if mlx_candidates:
            logger.debug(
                "[P0-2] MLX expanded %d domain candidates for query '%s...'",
                len(mlx_candidates),
                query[:30],
            )
            return mlx_candidates

    # Fall back to fast heuristic expansion
    heuristic_candidates = _heuristic_expand_concept(query)
    if heuristic_candidates:
        logger.debug(
            "[P0-2] Heuristic expanded %d domain candidates for query '%s...'",
            len(heuristic_candidates),
            query[:30],
        )
    return heuristic_candidates


# ── Convenience: extract domain strings from candidates ────────────────────────

def extract_domain_strings(
    candidates: list[SyntheticDomainCandidate],
    min_confidence: float = 0.25,
) -> list[str]:
    """
    Extract plain domain strings from SyntheticDomainCandidate list.

    Args:
        candidates: List of SyntheticDomainCandidate.
        min_confidence: Minimum confidence threshold (default 0.25, lowered from 0.3
            for broader coverage of heuristic candidates).
    """
    return [c.domain for c in candidates if c.confidence >= min_confidence]
