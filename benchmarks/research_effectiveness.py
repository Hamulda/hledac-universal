"""
Research Effectiveness Score Aggregation Helper

Computes top-level research scorecards from benchmark/evidence data:
- ResearchBreadthIndex
- ResearchDepthIndex
- ResearchQualityIndex
- ResearchFrictionIndex
- DeepResearchPowerScore

All functions are fail-open, offline-friendly, and deterministic-friendly.
Returns UNAVAILABLE_WITH_REASON when data is missing or insufficient.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

# ---------------------------------------------------------------------------
# Canonical normalization helpers
# ---------------------------------------------------------------------------

SOURCE_FAMILY_MAP = {
    "ct": "certificate_transparency",
    "certificate_transparency": "certificate_transparency",
    "wayback": "archive",
    "commoncrawl": "commoncrawl",
    "necromancer": "archive",
    "onion": "darknet",
    "tor": "darknet",
    "i2p": "darknet",
    "direct": "clearnet",
    "bing": "search",
    "duckduckgo": "search",
    "google": "search",
    "searxng": "search",
    "exalead": "search",
    "shodan": "iot",
    "censys": "iot",
    "passivedns": "dns",
    "dnsdb": "dns",
    "virustotal": "threat",
    "alienvault": "threat",
}


def normalize_source_family(source: str) -> str:
    """Map any source string to canonical family."""
    if not source:
        return "unknown"
    lower = source.lower()
    for key, family in SOURCE_FAMILY_MAP.items():
        if key.lower() in lower:
            return family
    return "other"


def normalize_acquisition_mode(mode: str) -> str:
    """Normalize acquisition mode to canonical form."""
    if not mode:
        return "unknown"
    lower = mode.lower()
    if "passive" in lower or "dns" in lower:
        return "passive"
    if "archive" in lower or "wayback" in lower or "necromancer" in lower:
        return "archive"
    if "onion" in lower or "i2p" in lower or "tor" in lower:
        return "hidden_service"
    if "ct" in lower or "certificate" in lower:
        return "certificate_transparency"
    if "cc" in lower or "commoncrawl" in lower:
        return "commoncrawl"
    if "deep" in lower or "probe" in lower:
        return "deep_crawl"
    return "direct"


def normalize_confidence_bucket(confidence: float) -> str:
    """Bucket confidence into qualitative tiers."""
    if confidence >= 0.9:
        return "high"
    elif confidence >= 0.7:
        return "medium"
    elif confidence >= 0.4:
        return "low"
    return "unknown"


def normalize_severity(severity: str) -> str:
    """Normalize severity strings."""
    if not severity:
        return "unknown"
    lower = severity.lower()
    if any(x in lower for x in ("critical", "crit")):
        return "critical"
    if any(x in lower for x in ("high", "severe")):
        return "high"
    if any(x in lower for x in ("medium", "moderate", "med")):
        return "medium"
    if any(x in lower for x in ("low", "info", "informational")):
        return "low"
    return "unknown"


# ---------------------------------------------------------------------------
# HHI computation
# ---------------------------------------------------------------------------

def _hhi(fraction_map: dict[str, int]) -> float:
    """
    Herfindahl-Hirschman Index for source concentration.
    Returns 0.0 (uniform) to 1.0 (monopoly).
    """
    total = sum(fraction_map.values())
    if total == 0:
        return 0.0
    return sum((count / total) ** 2 for count in fraction_map.values())


# ---------------------------------------------------------------------------
# Scorecard calculators
# ---------------------------------------------------------------------------


def compute_research_breadth_index(data: dict[str, Any]) -> dict[str, Any]:
    """
    Compute ResearchBreadthIndex from aggregated run data.

    Dimensions:
    - source_family_count: number of distinct source families used
    - source_family_hhi: HHI of source family distribution (lower = more diverse)
    - unique_domains: count of unique domains encountered
    - unique_tlds: count of unique TLDs
    - unique_content_types: count of content types (html/json/pdf/etc)
    - unique_source_hosts: count of unique hosts
    """
    try:
        acquisition = data.get("acquisition", {})
        sources = acquisition.get("sources", [])

        if not sources and not acquisition:
            return _unavailable("No acquisition data available")

        # Extract source families
        families: dict[str, int] = defaultdict(int)
        domains: dict[str, bool] = {}
        tlds: dict[str, bool] = {}
        content_types: dict[str, bool] = {}
        hosts: dict[str, bool] = {}

        for src in sources:
            src_name = str(src.get("source", src.get("host", src.get("url", ""))))
            family = normalize_source_family(src_name)
            families[family] += 1

            if "domain" in src:
                domains[src["domain"]] = True
            if "tld" in src:
                tlds[src["tld"]] = True
            if "content_type" in src:
                content_types[src["content_type"]] = True
            if "host" in src:
                hosts[src["host"]] = True
            if "url" in src:
                url = src["url"]
                try:
                    host = url.split("/")[2] if "//" in url else url
                    hosts[host] = True
                    if "." in host:
                        tlds[host.rsplit(".", 1)[-1]] = True
                except Exception:
                    pass

        # Fallback: extract from acquisition counters if no sources list
        if not sources:
            counters = acquisition.get("ct_attempts", 0)
            if counters > 0:
                families["certificate_transparency"] = counters
            wayback_attempts = acquisition.get("wayback_quick_attempts", 0)
            if wayback_attempts > 0:
                families["archive"] += wayback_attempts
            cc_attempts = acquisition.get("commoncrawl_attempts", 0)
            if cc_attempts > 0:
                families["commoncrawl"] = cc_attempts

        family_hhi = _hhi(families)
        source_family_count = len(families)

        # Normalize HHI: 1.0 monopoly = 0.0 breadth, 0.0 uniform = 1.0 breadth
        hhi_normalized = max(0.0, 1.0 - family_hhi)

        # Composite breadth score (0-100)
        breadth_score = (
            min(1.0, source_family_count / 8.0) * 30 +
            hhi_normalized * 30 +
            min(1.0, len(domains) / 100.0) * 20 +
            min(1.0, len(tlds) / 10.0) * 10 +
            min(1.0, len(content_types) / 5.0) * 5 +
            min(1.0, len(hosts) / 50.0) * 5
        )

        return {
            "status": "READY",
            "source_family_count": source_family_count,
            "source_family_hhi": round(family_hhi, 4),
            "source_family_hhi_normalized": round(hhi_normalized, 4),
            "unique_domains": len(domains),
            "unique_tlds": len(tlds),
            "unique_content_types": len(content_types),
            "unique_source_hosts": len(hosts),
            "breadth_score": round(breadth_score, 2),
            "families_breakdown": dict(families),
        }
    except Exception as e:
        return _unavailable(f"Breadth computation failed: {e}")


def compute_research_depth_index(data: dict[str, Any]) -> dict[str, Any]:
    """
    Compute ResearchDepthIndex from aggregated run data.

    Dimensions:
    - unindexed_source_hits: CT / DNS / passive sources accessed
    - archive_resurrection_hits: Wayback / necromancer successes
    - passive_source_hits: passive DNS, OTX, etc.
    - hidden_service_hits: Tor / I2P accesses
    - decentralized_source_hits: IPFS, dat, etc.
    - max_frontier_depth: deepest crawl depth reached
    - median_frontier_depth: median crawl depth
    """
    try:
        acquisition = data.get("acquisition", {})
        gating = data.get("gating", {})

        # Archive resurrection
        wayback_quick_successes = acquisition.get("wayback_quick_successes", 0)
        necromancer_rescues = acquisition.get("necromancer_rescues", 0)
        archive_resurrection_hits = wayback_quick_successes + necromancer_rescues

        # Unindexed / passive sources
        ct_successes = acquisition.get("ct_successes", 0)
        unindexed_source_hits = ct_successes

        # Onion / hidden services
        onion_available = acquisition.get("onion_available", 0)
        hidden_service_hits = onion_available

        # Passive sources (from PRF / expansion)
        prf_invocations = acquisition.get("prf_invocations", 0)

        # Deepening candidates from gating
        deepening_candidates = gating.get("deepening_gate_candidates", 0)

        # Estimate depth from evidence chains
        evidence_depths: list[int] = []
        synthesis = data.get("synthesis", {})
        if "evidence_depth" in synthesis:
            evidence_depths = synthesis["evidence_depth"]

        max_frontier_depth = max(evidence_depths) if evidence_depths else 0
        median_frontier_depth = (
            sorted(evidence_depths)[len(evidence_depths) // 2]
            if evidence_depths else 0
        )

        # Depth signals
        depth_signals = {
            "archive_resurrection": archive_resurrection_hits > 0,
            "unindexed_access": ct_successes > 0,
            "hidden_service_access": onion_available > 0,
            "passive_expansion": prf_invocations > 0,
            "deepening_candidates": deepening_candidates > 0,
        }

        depth_score = (
            (1.0 if depth_signals["archive_resurrection"] else 0.0) * 20 +
            (1.0 if depth_signals["unindexed_access"] else 0.0) * 20 +
            (1.0 if depth_signals["hidden_service_access"] else 0.0) * 20 +
            (1.0 if depth_signals["passive_expansion"] else 0.0) * 15 +
            min(1.0, deepening_candidates / 10.0) * 15 +
            min(1.0, max_frontier_depth / 5.0) * 10
        )

        return {
            "status": "READY",
            "unindexed_source_hits": unindexed_source_hits,
            "archive_resurrection_hits": archive_resurrection_hits,
            "passive_source_hits": prf_invocations,
            "hidden_service_hits": hidden_service_hits,
            "decentralized_source_hits": 0,
            "max_frontier_depth": max_frontier_depth,
            "median_frontier_depth": median_frontier_depth,
            "depth_score": round(depth_score, 2),
            "depth_signals": depth_signals,
            "wayback_quick_successes": wayback_quick_successes,
            "necromancer_rescues": necromancer_rescues,
            "ct_successes": ct_successes,
            "onion_available": onion_available,
        }
    except Exception as e:
        return _unavailable(f"Depth computation failed: {e}")


def compute_research_quality_index(data: dict[str, Any]) -> dict[str, Any]:
    """
    Compute ResearchQualityIndex from aggregated run data.

    Dimensions:
    - high_conf_findings_per_minute
    - novel_findings_per_100_sources
    - corroborated_findings_ratio
    - single_source_claim_ratio
    - evidence_completeness_rate
    - quality_weighted_findings_per_minute
    """
    try:
        synthesis = data.get("synthesis", {})
        gating = data.get("gating", {})
        timing = data.get("timing", {})
        sources = data.get("sources", [])

        wall_clock = timing.get("total_wall_clock_seconds", 0.0)
        research_runtime = timing.get("research_runtime_seconds", wall_clock)

        # Findings metrics
        findings_count = data.get("findings_count", synthesis.get("claims_emitted", 0))
        contested_claims = synthesis.get("contested_claims", 0)
        contradictions = synthesis.get("contradictions_surfaced", 0)
        _winner_only = synthesis.get("winner_only_evidence_count", 0)
        _ = _winner_only  # reserved for future evidence quality analysis

        # Confidence buckets
        high_conf = data.get("high_confidence_findings", 0)
        medium_conf = data.get("medium_confidence_findings", 0)
        low_conf = data.get("low_confidence_findings", 0)
        _ = medium_conf, low_conf  # reserved for future bucketing

        # Source corroboration
        corroborated = data.get("corroborated_findings", 0)
        single_source = data.get("single_source_claims", 0)

        # Evidence completeness
        evidence_completeness = data.get("evidence_completeness_rate", 0.0)

        # Admissions / throughput
        admits = gating.get("admits", 0)
        backlog_promotions = gating.get("backlog_promotions", 0)
        backlog_pushes = gating.get("backlog_pushes", 0)
        _ = backlog_promotions, backlog_pushes  # reserved for future gating analysis

        # Per-minute rates
        minutes = max(research_runtime / 60.0, 0.001)
        high_conf_per_min = high_conf / minutes if high_conf else 0.0
        findings_per_min = findings_count / minutes if findings_count else 0.0

        # Novelty (sources with new info vs total)
        novel_findings = data.get("novel_findings", findings_count)
        source_count = len(sources) if sources else max(admits, 1)
        novel_per_100_sources = (novel_findings / source_count * 100) if source_count else 0.0

        # Corroboration ratio
        total_claims = findings_count + contested_claims + contradictions
        corroborated_ratio = corroborated / total_claims if total_claims else 0.0
        single_source_ratio = single_source / total_claims if total_claims else 0.0

        # Quality score (0-100)
        quality_score = (
            min(1.0, high_conf_per_min / 10.0) * 25 +
            min(1.0, novel_per_100_sources / 50.0) * 20 +
            corroborated_ratio * 25 +
            max(0.0, 1.0 - single_source_ratio) * 15 +
            evidence_completeness * 15
        )

        return {
            "status": "READY",
            "high_conf_findings_per_minute": round(high_conf_per_min, 2),
            "novel_findings_per_100_sources": round(novel_per_100_sources, 2),
            "corroborated_findings_ratio": round(corroborated_ratio, 4),
            "single_source_claim_ratio": round(single_source_ratio, 4),
            "evidence_completeness_rate": round(evidence_completeness, 4),
            "quality_weighted_findings_per_minute": round(findings_per_min, 2),
            "total_findings": findings_count,
            "high_confidence_findings": high_conf,
            "corroborated_findings": corroborated,
            "contested_claims": contested_claims,
            "contradictions": contradictions,
            "quality_score": round(quality_score, 2),
        }
    except Exception as e:
        return _unavailable(f"Quality computation failed: {e}")


def compute_research_friction_index(data: dict[str, Any]) -> dict[str, Any]:
    """
    Compute ResearchFrictionIndex from aggregated run data.

    Dimensions:
    - challenge_issued_rate
    - challenge_solve_rate
    - challenge_loop_rate
    - challenge_solve_latency_ms
    - fallback_rate_after_403
    - fallback_rate_after_429
    """
    try:
        acquisition = data.get("acquisition", {})
        fetch = data.get("fetch", {})
        del fetch  # noqa: F841  # reserved for future fetch-level friction analysis

        # Challenge / bot detection metrics
        challenges_issued = data.get("challenges_issued",
                                     acquisition.get("captcha_issued", 0))
        challenges_solved = data.get("challenges_solved",
                                      acquisition.get("captcha_solved", 0))
        challenges_loop = data.get("challenges_loop",
                                    acquisition.get("captcha_loop", 0))
        challenge_latency_ms = data.get("challenge_solve_latency_ms", 0.0)

        # HTTP error fallbacks
        http_403_fallbacks = data.get("fallback_after_403",
                                       acquisition.get("fallback_after_403", 0))
        http_429_fallbacks = data.get("fallback_after_429",
                                       acquisition.get("fallback_after_429", 0))
        http_403_total = data.get("total_403",
                                   acquisition.get("http_403_count", 0))
        http_429_total = data.get("total_429",
                                   acquisition.get("http_429_count", 0))

        # Wayback fallbacks
        wayback_quick_attempts = acquisition.get("wayback_quick_attempts", 0)
        wayback_quick_successes = acquisition.get("wayback_quick_successes", 0)

        # Source fallback (CDX fallback)
        wayback_cdx_attempts = acquisition.get("wayback_cdx_attempts", 0)
        wayback_cdx_lines = acquisition.get("wayback_cdx_lines", 0)

        # Acquisition totals
        total_fetch_attempts = (
            acquisition.get("ct_attempts", 0) +
            acquisition.get("wayback_quick_attempts", 0) +
            acquisition.get("commoncrawl_attempts", 0) +
            acquisition.get("necromancer_attempts", 0) +
            1  # avoid div by zero
        )

        # Rates
        challenge_issued_rate = challenges_issued / total_fetch_attempts if total_fetch_attempts else 0.0
        challenge_solve_rate = challenges_solved / max(challenges_issued, 1)
        challenge_loop_rate = challenges_loop / max(challenges_issued, 1)
        fallback_rate_403 = http_403_fallbacks / max(http_403_total, 1)
        fallback_rate_429 = http_429_fallbacks / max(http_429_total, 1)

        # Wayback fallback rate (CDX coverage)
        wayback_fallback_rate = wayback_cdx_lines / max(wayback_cdx_attempts, 1) if wayback_cdx_attempts else 0.0

        # Friction score (0-100, lower = less friction)
        friction_score = (
            challenge_issued_rate * 100 * 0.20 +
            (1.0 - challenge_solve_rate) * 100 * 0.20 +
            challenge_loop_rate * 100 * 0.15 +
            min(1.0, challenge_latency_ms / 10000.0) * 100 * 0.10 +
            fallback_rate_403 * 100 * 0.15 +
            fallback_rate_429 * 100 * 0.20
        )

        return {
            "status": "READY",
            "challenge_issued_rate": round(challenge_issued_rate, 4),
            "challenge_solve_rate": round(challenge_solve_rate, 4),
            "challenge_loop_rate": round(challenge_loop_rate, 4),
            "challenge_solve_latency_ms": round(challenge_latency_ms, 2),
            "fallback_rate_after_403": round(fallback_rate_403, 4),
            "fallback_rate_after_429": round(fallback_rate_429, 4),
            "wayback_fallback_rate": round(wayback_fallback_rate, 4),
            "wayback_quick_attempts": wayback_quick_attempts,
            "wayback_quick_successes": wayback_quick_successes,
            "wayback_cdx_attempts": wayback_cdx_attempts,
            "wayback_cdx_lines": wayback_cdx_lines,
            "challenges_issued": challenges_issued,
            "challenges_solved": challenges_solved,
            "challenges_loop": challenges_loop,
            "http_403_fallbacks": http_403_fallbacks,
            "http_429_fallbacks": http_429_fallbacks,
            "friction_score": round(friction_score, 2),
        }
    except Exception as e:
        return _unavailable(f"Friction computation failed: {e}")


def compute_deep_research_power_score(
    breadth: dict[str, Any],
    depth: dict[str, Any],
    quality: dict[str, Any],
    friction: dict[str, Any],
) -> dict[str, Any]:
    """
    Compute DeepResearchPowerScore = weighted composite of all 4 indexes.

    Formula:
    DeepResearchPowerScore = (
        breadth_score * 0.25 +
        depth_score * 0.30 +
        quality_score * 0.30 +
        (100 - friction_score) * 0.15
    )

    Returns score 0-100.
    """
    try:
        bs = breadth.get("breadth_score", 0.0) if breadth.get("status") == "READY" else 0.0
        ds = depth.get("depth_score", 0.0) if depth.get("status") == "READY" else 0.0
        qs = quality.get("quality_score", 0.0) if quality.get("status") == "READY" else 0.0
        fs = friction.get("friction_score", 0.0) if friction.get("status") == "READY" else 0.0

        power_score = bs * 0.25 + ds * 0.30 + qs * 0.30 + (100 - fs) * 0.15

        # Determine tier
        if power_score >= 80:
            tier = "excellent"
        elif power_score >= 60:
            tier = "good"
        elif power_score >= 40:
            tier = "average"
        elif power_score >= 20:
            tier = "poor"
        else:
            tier = "minimal"

        return {
            "status": "READY",
            "deep_research_power_score": round(power_score, 2),
            "tier": tier,
            "components": {
                "breadth_score": bs,
                "depth_score": ds,
                "quality_score": qs,
                "friction_inverse_score": round(100 - fs, 2),
            },
            "component_weights": {
                "breadth": 0.25,
                "depth": 0.30,
                "quality": 0.30,
                "friction_inverse": 0.15,
            },
        }
    except Exception as e:
        return _unavailable(f"Power score computation failed: {e}")


# ---------------------------------------------------------------------------
# JSON/JSONL file aggregation
# ---------------------------------------------------------------------------

def load_benchmark_json(filepath: str) -> dict[str, Any]:
    """Load a single benchmark JSON file, fail-open."""
    try:
        with open(filepath, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def aggregate_benchmark_jsons(
    pattern: str,
    max_files: int = 100,
) -> dict[str, Any]:
    """
    Load and aggregate benchmark JSON files matching pattern.
    Returns merged dataset with averaged counters.
    """
    import glob as _glob

    files = sorted(_glob.glob(pattern))[:max_files]
    if not files:
        return {}

    # Aggregate counters
    merged: dict[str, Any] = defaultdict(int)
    timing_vals: list[float] = []
    findings_vals: list[int] = []
    sources_count = 0

    for fp in files:
        data = load_benchmark_json(fp)
        for key, val in data.items():
            if isinstance(val, (int, float)):
                merged[key] += val
            elif key == "acquisition":
                if "acquisition" not in merged:
                    merged["acquisition"] = defaultdict(lambda: 0.0)
                for ak, av in val.items():
                    if isinstance(av, (int, float)):
                        merged["acquisition"][ak] += av
            elif key == "synthesis":
                if "synthesis" not in merged:
                    merged["synthesis"] = defaultdict(lambda: 0.0)
                for sk, sv in val.items():
                    if isinstance(sv, (int, float)):
                        merged["synthesis"][sk] += sv
            elif key == "gating":
                if "gating" not in merged:
                    merged["gating"] = defaultdict(lambda: 0.0)
                for gk, gv in val.items():
                    if isinstance(gk, (int, float)):
                        merged["gating"][gk] += gv

        if "total_wall_clock_seconds" in data:
            timing_vals.append(data["total_wall_clock_seconds"])
        if "findings_count" in data:
            findings_vals.append(data["findings_count"])
        if "sources_count" in data:
            sources_count += data.get("sources_count", 0)

    merged["timing"] = {
        "total_wall_clock_seconds": sum(timing_vals) if timing_vals else 0.0,
        "research_runtime_seconds": 0.0,
        "average_wall_clock_per_run": sum(timing_vals) / len(timing_vals) if timing_vals else 0.0,
    }
    merged["findings_count"] = sum(findings_vals)
    merged["sources_count"] = sources_count
    merged["_aggregated_from"] = len(files)

    # Convert defaultdicts
    result = dict(merged)
    if "acquisition" in result:
        result["acquisition"] = dict(result["acquisition"])
    if "synthesis" in result:
        result["synthesis"] = dict(result["synthesis"])
    if "gating" in result:
        result["gating"] = dict(result["gating"])

    return result


def compute_all_scorecards(
    benchmark_pattern: str,
    max_files: int = 100,
) -> dict[str, Any]:
    """
    Compute all 5 scorecards from benchmark JSON files matching pattern.

    Returns full scorecard dict with all 5 top-level scores.
    """
    data = aggregate_benchmark_jsons(benchmark_pattern, max_files)

    breadth = compute_research_breadth_index(data)
    depth = compute_research_depth_index(data)
    quality = compute_research_quality_index(data)
    friction = compute_research_friction_index(data)
    power = compute_deep_research_power_score(breadth, depth, quality, friction)

    return {
        "research_breadth_index": breadth,
        "research_depth_index": depth,
        "research_quality_index": quality,
        "research_friction_index": friction,
        "deep_research_power_score": power,
        "_meta": {
            "aggregated_from_files": data.get("_aggregated_from", 0),
            "total_wall_clock_seconds": data.get("timing", {}).get("total_wall_clock_seconds", 0.0),
            "total_findings": data.get("findings_count", 0),
            "computed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        },
    }


def generate_scorecard_markdown(scorecard: dict[str, Any]) -> str:
    """Generate human-readable markdown report from scorecard."""
    lines = [
        "# Research Effectiveness Scorecard",
        "",
        f"_Generated: {scorecard.get('_meta', {}).get('computed_at', 'N/A')}_",
        f"_Aggregated from: {scorecard.get('_meta', {}).get('aggregated_from_files', 0)} runs_",
        "",
        "## DeepResearchPowerScore",
        "",
    ]

    power = scorecard.get("deep_research_power_score", {})
    if power.get("status") == "READY":
        lines.extend([
            f"- **Score**: {power.get('deep_research_power_score', 'N/A')} / 100",
            f"- **Tier**: {power.get('tier', 'N/A')}",
            "",
            "### Component Breakdown",
            "",
        ])
        components = power.get("components", {})
        weights = power.get("component_weights", {})
        for key, weight in weights.items():
            score = components.get(key, 0)
            lines.append(f"- {key}: {score} × {weight} = {round(score * weight, 2)}")
    else:
        lines.append(f"- **Status**: {power.get('status', 'UNKNOWN')}")
        if power.get("reason"):
            lines.append(f"- **Reason**: {power['reason']}")

    for name, idx in [
        ("ResearchBreadthIndex", "research_breadth_index"),
        ("ResearchDepthIndex", "research_depth_index"),
        ("ResearchQualityIndex", "research_quality_index"),
        ("ResearchFrictionIndex", "research_friction_index"),
    ]:
        lines.extend(["", f"## {name}", ""])
        data = scorecard.get(idx, {})
        if data.get("status") == "READY":
            # Print key metrics
            exclude = {"status", "depth_signals", "families_breakdown"}
            for k, v in data.items():
                if k not in exclude:
                    lines.append(f"- {k}: {v}")
        else:
            lines.append(f"- **Status**: {data.get('status', 'UNKNOWN')}")
            if data.get("reason"):
                lines.append(f"- **Reason**: {data['reason']}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _unavailable(reason: str) -> dict[str, Any]:
    return {"status": "UNAVAILABLE_WITH_REASON", "reason": reason}


# ---------------------------------------------------------------------------
# Standalone CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python research_effectiveness.py <benchmark_json_pattern>")
        print("Example: python research_effectiveness.py benchmark_results/benchmark_*.json")
        sys.exit(1)

    pattern = sys.argv[1]
    scorecard = compute_all_scorecards(pattern)

    # Print markdown
    print(generate_scorecard_markdown(scorecard))

    # Write JSON
    output_json = pattern.replace("*", "research_scorecard").replace("?", "_scorecard")
    if ".json" not in output_json:
        output_json = "research_scorecard.json"
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(scorecard, f, indent=2, ensure_ascii=False)
    print(f"\n[OK] Scorecard written to {output_json}")
