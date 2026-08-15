"""
Hypothesis Generator — F202G.

Bounded heuristic hypothesis generation from sprint findings.


Fail-soft: always returns >= 1 hypothesis even if DSPy unavailable.

hledac_hypothesis/hypothesisgenerator.py
"""
import logging
import os
import re
from dataclasses import dataclass, field
import msgspec
from typing import TYPE_CHECKING, Any
from core import aclose
if TYPE_CHECKING:
    from hledac.universal.knowledge.graph_service import DuckPGQGraph
logger = logging.getLogger(__name__)
MAX_HYPOTHESES = 10
MAX_SEEDS_PER_HYPOTHESIS = 5
MAX_EXTRACTS_PER_TYPE = 1000
HLEDAC_ENABLE_DSPY = os.environ.get('HLEDAC_ENABLE_DSPY', '').lower() in ('1', 'true', 'yes')

class ResearchHypothesis(msgspec.Struct, frozen=True, gc=False):
    """Single research hypothesis produced by HypothesisGenerator."""
    hypothesis_text: str
    confidence: float
    pivot_seeds: tuple[str, ...] = field(default_factory=tuple)
    supporting_findings: tuple[str, ...] = field(default_factory=tuple)
    hypothesis_type: str = 'entity_expansion'

def _load_dspy_program():
    """Lazy-load DSPy HypothesisGeneratorProgram. Returns (program, error)."""
    try:
        from hledac.universal.brain.dspy_programs import get_program
        prog = get_program('hypothesis_generator')
        if prog is None:
            logger.info('DSPy: No compiled HypothesisGenerator program — run:\n  python scripts/dspy_compile.py hypothesis_generator --train gold_data/hypotheses.jsonl')
            return None
        return prog
    except Exception as e:
        logger.warning('DSPy HypothesisGenerator import failed: %s', e)
        return None
_IP_RE = re.compile('\\b(?:(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?))\\b')
_DOMAIN_RE = re.compile('\\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9\\-]{0,61}[a-zA-Z0-9])?\\.)+[a-zA-Z]{2,}\\b')
_HASH_RE = re.compile('\\b[a-fA-F0-9]{32,64}\\b')
_EMAIL_RE = re.compile('\\b[a-zA-Z0-9._%+\\-]+@[a-zA-Z0-9.\\-]+\\.[a-zA-Z]{2,}\\b')

def _extract_ips(payload: str) -> list[str]:
    if not payload:
        return []
    return _IP_RE.findall(payload)

def _extract_domains(payload: str) -> list[str]:
    if not payload:
        return []
    domains = _DOMAIN_RE.findall(payload)
    return [d for d in domains if d not in ('example.com', 'localhost', 'test.com')]

def _extract_hashes(payload: str) -> list[str]:
    if not payload:
        return []
    return _HASH_RE.findall(payload)

def _extract_emails(payload: str) -> list[str]:
    if not payload:
        return []
    return _EMAIL_RE.findall(payload)

def _extract_findings_by_type(findings: list[Any]) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Extract findings organized by type (IP, domain, hash, email) with finding IDs."""
    by_type: dict[str, list[str]] = {'domain': [], 'ip': [], 'hash': [], 'email': []}
    finding_map: dict[str, list[str]] = {}
    extractors = {
        'ip': _extract_ips,
        'domain': _extract_domains,
        'hash': _extract_hashes,
        'email': _extract_emails,
    }
    for f in findings:
        fid = getattr(f, 'finding_id', None) or getattr(f, 'id', None) or ''
        payload = getattr(f, 'payload_text', '') or ''
        for ioc_type, extractor in extractors.items():
            for value in extractor(payload):
                if len(by_type[ioc_type]) >= MAX_EXTRACTS_PER_TYPE:
                    break
                by_type[ioc_type].append(value)
                finding_map.setdefault(value, []).append(fid)
    return by_type, finding_map


def _build_ip_hypotheses(ip_list: list[str], finding_map: dict[str, list[str]]) -> list[ResearchHypothesis]:
    """Build hypotheses for IP addresses - explore adjacent subnets."""
    hypotheses: list[ResearchHypothesis] = []
    for ip in ip_list[:5]:
        if len(hypotheses) >= MAX_HYPOTHESES:
            break
        parts = ip.rsplit('.', 1)
        if len(parts) == 2:
            subnet = f'{parts[0]}.{parts[1]}.0.0/16'
            hypotheses.append(ResearchHypothesis(
                hypothesis_text=f'IP {ip} is a known indicator - explore adjacent {subnet} for related infrastructure',
                confidence=0.65, pivot_seeds=(subnet,), supporting_findings=tuple(finding_map.get(ip, [])),
                hypothesis_type='entity_expansion'))
    return hypotheses


def _build_domain_hypotheses(domain_list: list[str], finding_map: dict[str, list[str]]) -> list[ResearchHypothesis]:
    """Build hypotheses for domains - check parent domains."""
    hypotheses: list[ResearchHypothesis] = []
    for domain in domain_list[:5]:
        if len(hypotheses) >= MAX_HYPOTHESES:
            break
        parts = domain.split('.')
        if len(parts) >= 2:
            parent = '.'.join(parts[1:])
            hypotheses.append(ResearchHypothesis(
                hypothesis_text=f'Domain {domain} is under investigation - check related domains under {parent}',
                confidence=0.6, pivot_seeds=(parent,), supporting_findings=tuple(finding_map.get(domain, [])),
                hypothesis_type='entity_expansion'))
    return hypotheses


def _build_temporal_domain_hypotheses(domain_list: list[str], finding_map: dict[str, list[str]]) -> list[ResearchHypothesis]:
    """Build temporal hypotheses for domains - WHOIS age anomalies."""
    hypotheses: list[ResearchHypothesis] = []
    for domain in domain_list[:3]:
        if len(hypotheses) >= MAX_HYPOTHESES:
            break
        hypotheses.append(ResearchHypothesis(
            hypothesis_text=f'Domain {domain} found in current sprint - cross-reference WHOIS/registration timeline for age anomalies',
            confidence=0.55, pivot_seeds=(f'whois:{domain}',), supporting_findings=tuple(finding_map.get(domain, [])),
            hypothesis_type='temporal'))
    return hypotheses


def _build_hash_hypotheses(hash_list: list[str], finding_map: dict[str, list[str]]) -> list[ResearchHypothesis]:
    """Build hypotheses for file hashes - find related artifacts."""
    hypotheses: list[ResearchHypothesis] = []
    for h in hash_list[:3]:
        if len(hypotheses) >= MAX_HYPOTHESES:
            break
        hypotheses.append(ResearchHypothesis(
            hypothesis_text=f'File hash {h[:16]}... appears in this sprint - find other artifacts sharing the same hash for infrastructure mapping',
            confidence=0.7, pivot_seeds=(f'hash:{h}',), supporting_findings=tuple(finding_map.get(h, [])),
            hypothesis_type='lateral'))
    return hypotheses


def _build_email_hypotheses(email_list: list[str], finding_map: dict[str, list[str]]) -> list[ResearchHypothesis]:
    """Build hypotheses for emails - search for credentials/PII leaks."""
    hypotheses: list[ResearchHypothesis] = []
    for email in email_list[:3]:
        if len(hypotheses) >= MAX_HYPOTHESES:
            break
        hypotheses.append(ResearchHypothesis(
            hypothesis_text=f'Email {email} appeared in a finding - search paste sites and breach feeds for associated credentials or PII',
            confidence=0.6, pivot_seeds=(f'leak:{email}',), supporting_findings=tuple(finding_map.get(email, [])),
            hypothesis_type='adversarial'))
    return hypotheses


def _build_seed_hypotheses(seeds: list[str]) -> list[ResearchHypothesis]:
    """Build hypotheses from current seed anchors."""
    hypotheses: list[ResearchHypothesis] = []
    for seed in seeds[:5]:
        if len(hypotheses) >= MAX_HYPOTHESES:
            break
        hypotheses.append(ResearchHypothesis(
            hypothesis_text=f'Seed {seed} is the current anchor - derive related domain/IP patterns to expand the investigation scope',
            confidence=0.5, pivot_seeds=(seed,), supporting_findings=(), hypothesis_type='entity_expansion'))
    return hypotheses


def _heuristic_generate(findings: list[Any], current_seeds: list[str], sprint_depth: int) -> list[ResearchHypothesis]:
    """Generate hypotheses using simple rule-based heuristic (M1-safe fallback)."""
    by_type, finding_map = _extract_findings_by_type(findings)
    hypotheses: list[ResearchHypothesis] = []
    hypotheses.extend(_build_ip_hypotheses(by_type['ip'], finding_map))
    hypotheses.extend(_build_domain_hypotheses(by_type['domain'], finding_map))
    if sprint_depth > 1:
        hypotheses.extend(_build_temporal_domain_hypotheses(by_type['domain'], finding_map))
    hypotheses.extend(_build_hash_hypotheses(by_type['hash'], finding_map))
    hypotheses.extend(_build_email_hypotheses(by_type['email'], finding_map))
    hypotheses.extend(_build_seed_hypotheses(current_seeds))
    return hypotheses[:MAX_HYPOTHESES]


def _dspy_generate(findings: list[Any], current_seeds: list[str], sprint_depth: int, graph: DuckPGQGraph | None) -> list[ResearchHypothesis]:
    """Generate hypotheses using DSPy HypothesisGeneratorProgram (falls back to heuristic)."""
    program = _load_dspy_program()
    if program is None:
        return _heuristic_generate(findings, current_seeds, sprint_depth)
    research_query = ' '.join(current_seeds[:3])[:200] if current_seeds else 'OSINT investigation'
    rag_lines: list[str] = []
    for f in findings[:20]:
        payload = getattr(f, 'payload_text', '') or ''
        if payload:
            rag_lines.append(payload[:500])
    rag_context = ' | '.join(rag_lines)[:2000]
    graph_summary = ''
    if graph is not None:
        try:
            stats = graph.graph_stats()
            node_count = stats.get('node_count', 0)
            edge_count = stats.get('edge_count', 0)
            graph_summary = f'Cross-sprint graph: {node_count} nodes, {edge_count} edges'
        except Exception as e:
            logger.debug('graph_stats unavailable: %s', e)
            graph_summary = ''
    try:
        pred = program.forward(research_query=research_query, rag_context=rag_context, graph_summary=graph_summary, reward_context='', existing_hypotheses=[])
        res = getattr(pred, 'answer', '') or ''
    except Exception as e:
        logger.warning('DSPy HypothesisGenerator forward failed: %s', e)
        return _heuristic_generate(findings, current_seeds, sprint_depth)
    hypotheses: list[ResearchHypothesis] = []
    for line in res.strip().split('\n'):
        line = line.strip()
        if not line:
            continue
        m = re.match('^\\d+[.:]\\s+(.+)', line)
        text = m.group(1) if m else line
        if len(hypotheses) >= MAX_HYPOTHESES:
            break
        hypotheses.append(ResearchHypothesis(hypothesis_text=text, confidence=0.7, pivot_seeds=tuple(current_seeds[:3]), supporting_findings=(), hypothesis_type='entity_expansion'))
    return hypotheses

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
    __slots__ = tuple(('_graph',))

    def __init__(self, graph: DuckPGQGraph | None=None) -> None:
        self._graph = graph

    def generate(self, findings: list[Any], current_seeds: list[str], sprint_depth: int=1) -> list[ResearchHypothesis]:
        if not findings and (not current_seeds):
            return [ResearchHypothesis(hypothesis_text='No findings in this sprint - expand query to broader surface area', confidence=0.1, pivot_seeds=('wide-scan',), supporting_findings=(), hypothesis_type='entity_expansion')]
        try:
            if HLEDAC_ENABLE_DSPY and self._graph is not None:
                hypotheses = _dspy_generate(findings, current_seeds, sprint_depth, self._graph)
            else:
                hypotheses = _heuristic_generate(findings, current_seeds, sprint_depth)
        except Exception as e:
            logger.warning('HypothesisGenerator.generate failed: %s - returning heuristic fallback', e)
            hypotheses = _heuristic_generate(findings, current_seeds, sprint_depth)
        if not hypotheses:
            hypotheses = _heuristic_generate(findings, current_seeds, sprint_depth)
        return hypotheses[:MAX_HYPOTHESES]