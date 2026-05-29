"""
Hypothesis Generator — F202G.

Bounded heuristic hypothesis generation from sprint findings.
Fail-soft: always returns >= 1 hypothesis even if DSPy unavailable.

hypothesis/hypothesisgenerator.py
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hledac.universal.knowledge.graph_service import DuckPGQGraph

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------------
# Invariants
# -------------------------------------------------------------------------
MAX_HYPOTHESES = 10
MAX_SEEDS_PER_HYPOTHESIS = 5
MAX_EXTRACTS_PER_TYPE = 1000  # cap per IOC type to avoid unbounded collection
HLEDAC_ENABLE_DSPY = os.environ.get("HLEDAC_ENABLE_DSPY", "").lower() in ("1", "true", "yes")

# -------------------------------------------------------------------------
# Types
# -------------------------------------------------------------------------
@dataclass(frozen=True)
class ResearchHypothesis:
    """Single research hypothesis produced by HypothesisGenerator."""

    hypothesis_text: str
    confidence: float  # 0.0-1.0
    pivot_seeds: tuple[str, ...] = field(default_factory=tuple)
    supporting_findings: tuple[str, ...] = field(default_factory=tuple)  # finding_ids
    hypothesis_type: str = "entity_expansion"  # entity_expansion | temporal | lateral | adversarial


# -------------------------------------------------------------------------
# DSPy wrapper
# -------------------------------------------------------------------------
def _load_dspy_program():
    """Lazy-load DSPy HypothesisGeneratorProgram. Returns (program, error)."""
    try:
        from brain.dspy_programs import get_program

        prog = get_program("hypothesis_generator")
        if prog is None:
            logger.info(
                "DSPy: No compiled HypothesisGenerator program — run:\n"
                "  python scripts/dspy_compile.py hypothesis_generator --train gold_data/hypotheses.jsonl"
            )
            return None
        return prog
    except Exception as e:
        logger.warning("DSPy HypothesisGenerator import failed: %s", e)
        return None


# -------------------------------------------------------------------------
# Entity extraction helpers
# -------------------------------------------------------------------------
_IP_RE = re.compile(
    r"\b(?:(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?))\b"
)
_DOMAIN_RE = re.compile(
    r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}\b"
)
_HASH_RE = re.compile(r"\b[a-fA-F0-9]{32,64}\b")
_EMAIL_RE = re.compile(r"\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b")


def _extract_ips(payload: str) -> list[str]:
    if not payload:
        return []
    return _IP_RE.findall(payload)


def _extract_domains(payload: str) -> list[str]:
    if not payload:
        return []
    domains = _DOMAIN_RE.findall(payload)
    return [d for d in domains if d not in ("example.com", "localhost", "test.com")]


def _extract_hashes(payload: str) -> list[str]:
    if not payload:
        return []
    return _HASH_RE.findall(payload)


def _extract_emails(payload: str) -> list[str]:
    if not payload:
        return []
    return _EMAIL_RE.findall(payload)


# -------------------------------------------------------------------------
# Heuristic hypothesis generation
# -------------------------------------------------------------------------
def _heuristic_generate(
    findings: list[Any], current_seeds: list[str], sprint_depth: int
) -> list[ResearchHypothesis]:
    """Generate hypotheses using simple rule-based heuristic (M1-safe fallback)."""

    by_type: dict[str, list[str]] = {"domain": [], "ip": [], "hash": [], "email": []}
    finding_map: dict[str, list[str]] = {}  # entity_value -> [finding_id, ...]

    for f in findings:
        fid = getattr(f, "finding_id", None) or getattr(f, "id", None) or ""
        payload = getattr(f, "payload_text", "") or ""

        for ip in _extract_ips(payload):
            if len(by_type["ip"]) >= MAX_EXTRACTS_PER_TYPE:
                break
            by_type["ip"].append(ip)
            finding_map.setdefault(ip, []).append(fid)
        for d in _extract_domains(payload):
            if len(by_type["domain"]) >= MAX_EXTRACTS_PER_TYPE:
                break
            by_type["domain"].append(d)
            finding_map.setdefault(d, []).append(fid)
        for h in _extract_hashes(payload):
            if len(by_type["hash"]) >= MAX_EXTRACTS_PER_TYPE:
                break
            by_type["hash"].append(h)
            finding_map.setdefault(h, []).append(fid)
        for e in _extract_emails(payload):
            if len(by_type["email"]) >= MAX_EXTRACTS_PER_TYPE:
                break
            by_type["email"].append(e)
            finding_map.setdefault(e, []).append(fid)

    hypotheses: list[ResearchHypothesis] = []

    # --- Entity expansion: IP /24 subnet --- #
    for ip in by_type["ip"][:5]:
        if len(hypotheses) >= MAX_HYPOTHESES:
            break
        parts = ip.rsplit(".", 1)
        if len(parts) == 2:
            subnet = f"{parts[0]}.{parts[1]}.0.0/16"
            hypotheses.append(
                ResearchHypothesis(
                    hypothesis_text=(
                        f"IP {ip} is a known indicator - explore adjacent {subnet} for related infrastructure"
                    ),
                    confidence=0.65,
                    pivot_seeds=(subnet,),
                    supporting_findings=tuple(finding_map.get(ip, [])),
                    hypothesis_type="entity_expansion",
                )
            )

    # --- Entity expansion: domain parent --- #
    for domain in by_type["domain"][:5]:
        if len(hypotheses) >= MAX_HYPOTHESES:
            break
        parts = domain.split(".")
        if len(parts) >= 2:
            parent = ".".join(parts[1:])
            hypotheses.append(
                ResearchHypothesis(
                    hypothesis_text=(
                        f"Domain {domain} is under investigation - check related domains under {parent}"
                    ),
                    confidence=0.6,
                    pivot_seeds=(parent,),
                    supporting_findings=tuple(finding_map.get(domain, [])),
                    hypothesis_type="entity_expansion",
                )
            )

    # --- Temporal: registration age check --- #
    if sprint_depth > 1:
        for domain in by_type["domain"][:3]:
            if len(hypotheses) >= MAX_HYPOTHESES:
                break
            hypotheses.append(
                ResearchHypothesis(
                    hypothesis_text=(
                        f"Domain {domain} found in current sprint - cross-reference "
                        "WHOIS/registration timeline for age anomalies"
                    ),
                    confidence=0.55,
                    pivot_seeds=(f"whois:{domain}",),
                    supporting_findings=tuple(finding_map.get(domain, [])),
                    hypothesis_type="temporal",
                )
            )

    # --- Lateral: shared hash --- #
    for h in by_type["hash"][:3]:
        if len(hypotheses) >= MAX_HYPOTHESES:
            break
        hypotheses.append(
            ResearchHypothesis(
                hypothesis_text=(
                    f"File hash {h[:16]}... appears in this sprint - "
                    "find other artifacts sharing the same hash for infrastructure mapping"
                ),
                confidence=0.7,
                pivot_seeds=(f"hash:{h}",),
                supporting_findings=tuple(finding_map.get(h, [])),
                hypothesis_type="lateral",
            )
        )

    # --- Adversarial: pastebin / leak signals --- #
    for email in by_type["email"][:3]:
        if len(hypotheses) >= MAX_HYPOTHESES:
            break
        hypotheses.append(
            ResearchHypothesis(
                hypothesis_text=(
                    f"Email {email} appeared in a finding - search paste sites "
                    "and breach feeds for associated credentials or PII"
                ),
                confidence=0.6,
                pivot_seeds=(f"leak:{email}",),
                supporting_findings=tuple(finding_map.get(email, [])),
                hypothesis_type="adversarial",
            )
        )

    # --- Seed expansion --- #
    for seed in current_seeds[:5]:
        if len(hypotheses) >= MAX_HYPOTHESES:
            break
        hypotheses.append(
            ResearchHypothesis(
                hypothesis_text=(
                    f"Seed {seed} is the current anchor - derive related domain/IP patterns "
                    "to expand the investigation scope"
                ),
                confidence=0.5,
                pivot_seeds=(seed,),
                supporting_findings=(),
                hypothesis_type="entity_expansion",
            )
        )

    return hypotheses


# -------------------------------------------------------------------------
# DSPy-augmented generation
# -------------------------------------------------------------------------
def _dspy_generate(
    findings: list[Any],
    current_seeds: list[str],
    sprint_depth: int,
    graph: DuckPGQGraph | None,
) -> list[ResearchHypothesis]:
    """Generate hypotheses using DSPy HypothesisGeneratorProgram (falls back to heuristic)."""
    program = _load_dspy_program()
    if program is None:
        return _heuristic_generate(findings, current_seeds, sprint_depth)

    # Build research query from seeds
    research_query = " ".join(current_seeds[:3])[:200] if current_seeds else "OSINT investigation"

    # Build RAG context from findings payload_text
    rag_lines: list[str] = []
    for f in findings[:20]:
        payload = getattr(f, "payload_text", "") or ""
        if payload:
            rag_lines.append(payload[:500])
    rag_context = " | ".join(rag_lines)[:2000]

    # Build graph summary
    graph_summary = ""
    if graph is not None:
        try:
            stats = graph.graph_stats()
            node_count = stats.get("node_count", 0)
            edge_count = stats.get("edge_count", 0)
            graph_summary = f"Cross-sprint graph: {node_count} nodes, {edge_count} edges"
        except Exception as e:
            logger.debug("graph_stats unavailable: %s", e)
            graph_summary = ""

    try:
        pred = program.forward(
            research_query=research_query,
            rag_context=rag_context,
            graph_summary=graph_summary,
            reward_context="",
            existing_hypotheses=[],
        )
        res = getattr(pred, "answer", "") or ""
    except Exception as e:
        logger.warning("DSPy HypothesisGenerator forward failed: %s", e)
        return _heuristic_generate(findings, current_seeds, sprint_depth)

    hypotheses: list[ResearchHypothesis] = []
    for line in res.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^\d+[.:]\s+(.+)", line)
        text = m.group(1) if m else line
        if len(hypotheses) >= MAX_HYPOTHESES:
            break
        hypotheses.append(
            ResearchHypothesis(
                hypothesis_text=text,
                confidence=0.7,
                pivot_seeds=tuple(current_seeds[:3]),
                supporting_findings=(),
                hypothesis_type="entity_expansion",
            )
        )
    return hypotheses


# -------------------------------------------------------------------------
# HypothesisGenerator
# -------------------------------------------------------------------------
class HypothesisGenerator:
    """
    Generates research hypotheses from sprint findings.

    Args:
        findings: list of CanonicalFinding (or dict-like) from current sprint
        current_seeds: active IOC seeds for this sprint
        sprint_depth: which sprint number (1-indexed) - higher = more aggressive

    Returns:
        list[ResearchHypothesis] - max 10, never empty (fail-soft)
    """

    def __init__(self, graph: DuckPGQGraph | None = None) -> None:
        self._graph = graph

    def generate(
        self,
        findings: list[Any],
        current_seeds: list[str],
        sprint_depth: int = 1,
    ) -> list[ResearchHypothesis]:
        if not findings and not current_seeds:
            return [
                ResearchHypothesis(
                    hypothesis_text="No findings in this sprint - expand query to broader surface area",
                    confidence=0.1,
                    pivot_seeds=("wide-scan",),
                    supporting_findings=(),
                    hypothesis_type="entity_expansion",
                )
            ]

        try:
            if HLEDAC_ENABLE_DSPY and self._graph is not None:
                hypotheses = _dspy_generate(findings, current_seeds, sprint_depth, self._graph)
            else:
                hypotheses = _heuristic_generate(findings, current_seeds, sprint_depth)
        except Exception as e:
            logger.warning("HypothesisGenerator.generate failed: %s - returning heuristic fallback", e)
            hypotheses = _heuristic_generate(findings, current_seeds, sprint_depth)

        if not hypotheses:
            hypotheses = _heuristic_generate(findings, current_seeds, sprint_depth)

        return hypotheses[:MAX_HYPOTHESES]
