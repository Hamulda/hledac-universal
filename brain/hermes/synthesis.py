"""
brain/hermes/synthesis.py — Synthesis Methods
============================================

PEP 698: Extracted from brain/deephermes3_engine.py.

Handles:
- Research report generation
- Sprint plan generation
- Findings synthesis
- Multi-turn synthesis orchestration

M1 8GB: Bounded prompts to respect memory constraints.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# Constants for bounded synthesis (M1 8GB constraints)
SYNTH_MAX_QUERY_CHARS = 1024
SYNTH_MAX_FINDINGS = 50
SYNTH_MAX_FINDING_CHARS = 800
SYNTH_MAX_HYPOTHESES = 10
SYNTH_MAX_OUTPUT_CHARS = 8192

REPORT_MAX_CONTEXT_CHARS = 4096 * 4
REPORT_MAX_ITEM_CHARS = 500
REPORT_MAX_ITEMS = 20

REPORT_SYSTEM_PROMPT = """Jsi OSINT research agent. Analyzuj poskytnuté podklady a vytvoř strukturovaný report v češtině. Na konci své odpovědi VŽDY vlož blok <IOC_JSON> s extrahovanými entitami ve formátu JSON. Formát: <IOC_JSON>{"iocs": ["ioc1", "ioc2", ...], "entities": ["entity1", "entity2", ...]}</IOC_JSON>"""

SPRINT_PLAN_SYSTEM_PROMPT = """You are a research sprint planning assistant. Create actionable sprint plans based on the research query and context. Respond in Czech with structured JSON output."""

FINDINGS_SYSTEM_PROMPT = """You are a research synthesis assistant. Analyze findings and create coherent hypotheses. Respond in Czech with structured JSON output."""


class SynthesisOutput:
    """
    Synthesis output container.
    
    PEP 698: Extracted from _SynthesisOutput in deephermes3_engine.py.
    """
    
    __slots__ = ("findings", "hypotheses", "confidence")
    
    def __init__(
        self,
        findings: list[str] | None = None,
        hypotheses: list[str] | None = None,
        confidence: float = 0.5,
    ):
        self.findings = findings or []
        self.hypotheses = hypotheses or []
        self.confidence = confidence


async def generate_report(
    engine,
    query: str,
    context: list[str],
) -> str:
    """
    Generate OSINT research report from query and context.
    
    Fail-soft: returns empty string if model not loaded.
    
    Args:
        engine: DeepHermes3Engine instance
        query: Research query
        context: List of context strings
        
    Returns:
        Generated report text, or empty string if unavailable
    """
    if engine._model is None:
        logger.warning("[GENERATE_REPORT] Model not loaded, skipping report generation")
        return ""
    
    # Bound query length
    bounded_query = str(query)[:SYNTH_MAX_QUERY_CHARS]
    
    # Sanitize context items
    _sanitize_fn = engine._sanitize_for_llm
    truncated_contexts: list[str] = []
    total_len = 0
    
    for item in context[:REPORT_MAX_ITEMS]:
        # Sanitize web content
        if _sanitize_fn is not None:
            sanitized_item = _sanitize_fn(str(item))
        else:
            sanitized_item = str(item)
        
        truncated = sanitized_item[:REPORT_MAX_ITEM_CHARS]
        
        if total_len + len(truncated) > REPORT_MAX_CONTEXT_CHARS:
            remaining = REPORT_MAX_CONTEXT_CHARS - total_len
            if remaining > 100:
                truncated_contexts.append(truncated[:remaining])
            break
        
        truncated_contexts.append(truncated)
        total_len += len(truncated)
    
    context_str = "\n---\n".join(truncated_contexts)
    
    # Apply bandit modifier if available
    bandit = engine._get_prompt_bandit()
    arm_used = ""
    modifier = ""
    if bandit is not None:
        try:
            arm_used = bandit.select_arm()
            modifier = bandit.get_prompt_modifier(arm_used)
            engine._last_bandit_arm = arm_used
            logger.debug(f"[GENERATE_REPORT] Bandit arm: {arm_used}")
        except Exception as e:
            logger.debug(f"[GENERATE_REPORT] Bandit select failed: {e}")
    
    # Format prompt
    modifier_str = f"\n\n{modifier}" if modifier else ""
    prompt = f"""{bounded_query}{modifier_str}

Context:
{context_str}

Generate a comprehensive report based on the above context."""
    
    try:
        result = await engine.generate(prompt, system_msg=REPORT_SYSTEM_PROMPT, max_tokens=2048)
        
        # Record bandit reward
        if bandit is not None and arm_used:
            try:
                bandit.update(arm_used, 1.0)
            except Exception as e:
                logger.debug(f"[GENERATE_REPORT] Bandit update failed: {e}")
        
        return result
    except Exception as e:
        logger.error(f"[GENERATE_REPORT] Generation failed: {e}")
        return ""


async def generate_sprint_plan(
    engine,
    query: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Generate research sprint plan.
    
    Args:
        engine: DeepHermes3Engine instance
        query: Research query
        context: Optional context dictionary
        
    Returns:
        Sprint plan dictionary with tasks and milestones
    """
    context = context or {}
    bounded_query = str(query)[:SYNTH_MAX_QUERY_CHARS]
    
    # Bound history
    history = context.get("history", [])[:5]
    from hledac.universal.utils.msgspec_json import encode_fast as _msgspec_encode_fast
    history_str = _msgspec_encode_fast(history).decode() if history else "No previous context"
    
    prompt = f"""Research Query: {bounded_query}

Previous Context:
{history_str}

Create a sprint plan for this research."""

    try:
        result = await engine.generate(prompt, system_msg=SPRINT_PLAN_SYSTEM_PROMPT, max_tokens=1024)
        return {
            "query": bounded_query,
            "plan": result,
            "status": "generated",
        }
    except Exception as e:
        logger.error(f"[SPRINT_PLAN] Generation failed: {e}")
        return {
            "query": bounded_query,
            "plan": "",
            "status": "failed",
            "error": str(e),
        }


async def synthesize_findings(
    engine,
    query: str,
    findings: list[Any],
    hypotheses: list[str] | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Synthesize research findings into coherent output.
    
    Args:
        engine: DeepHermes3Engine instance
        query: Research query
        findings: List of findings to synthesize
        hypotheses: Optional existing hypotheses
        context: Optional context dictionary
        
    Returns:
        Synthesized findings dictionary
    """
    context = context or {}
    bounded_query = str(query)[:SYNTH_MAX_QUERY_CHARS]
    
    # Bound findings
    bounded_findings = []
    for f in findings[:SYNTH_MAX_FINDINGS]:
        finding_str = str(f)[:SYNTH_MAX_FINDING_CHARS]
        bounded_findings.append(finding_str)
    
    bounded_hypotheses = []
    if hypotheses:
        for h in hypotheses[:SYNTH_MAX_HYPOTHESES]:
            bounded_hypotheses.append(str(h)[:SYNTH_MAX_FINDING_CHARS])
    
    findings_str = "\n".join(f"- {f}" for f in bounded_findings)
    hypotheses_str = "\n".join(f"- {h}" for h in bounded_hypotheses) if bounded_hypotheses else "None"
    
    prompt = f"""Research Query: {bounded_query}

Findings:
{findings_str}

Existing Hypotheses:
{hypotheses_str}

Synthesize the findings into a coherent analysis."""

    try:
        result = await engine.generate(prompt, system_msg=FINDINGS_SYSTEM_PROMPT, max_tokens=SYNTH_MAX_OUTPUT_CHARS)
        
        return {
            "query": bounded_query,
            "synthesis": result,
            "findings_count": len(bounded_findings),
            "hypotheses_count": len(bounded_hypotheses),
            "status": "synthesized",
        }
    except Exception as e:
        logger.error(f"[SYNTHESIZE] Synthesis failed: {e}")
        return {
            "query": bounded_query,
            "synthesis": "",
            "status": "failed",
            "error": str(e),
        }


async def triage_findings_if_needed(
    engine,
    findings: list[Any],
    threshold: float = 0.3,
) -> list[Any]:
    """
    Triage findings by relevance if triage mode is enabled.
    
    Args:
        engine: DeepHermes3Engine instance
        findings: List of findings to triage
        threshold: Relevance threshold
        
    Returns:
        Filtered findings list
    """
    if not engine._triage_mode:
        return findings
    
    from .decisions import TriageMode
    
    filtered = []
    for finding in findings:
        if isinstance(finding, dict):
            is_relevant = await TriageMode.score_relevance(
                engine, finding, threshold
            )
            if is_relevant:
                filtered.append(finding)
        else:
            filtered.append(finding)
    
    dropped = len(findings) - len(filtered)
    if dropped > 0:
        logger.debug(f"[TRIAGE] Dropped {dropped} low-relevance findings")
    
    return filtered


async def synthesize(
    engine,
    context: dict[str, Any],
) -> str:
    """
    Simple synthesis of context into a response.
    
    Args:
        engine: DeepHermes3Engine instance
        context: Context dictionary
        
    Returns:
        Synthesized text
    """
    query = context.get("query", "")
    findings = context.get("findings", [])
    
    findings_str = "\n".join(f"- {str(f)[:SYNTH_MAX_FINDING_CHARS]}" for f in findings[:SYNTH_MAX_FINDINGS])
    
    prompt = f"""Based on the following findings:

{findings_str}

Provide a comprehensive synthesis."""
    
    try:
        return await engine.generate(prompt, max_tokens=SYNTH_MAX_OUTPUT_CHARS)
    except Exception as e:
        logger.error(f"[SYNTHESIZE] Simple synthesis failed: {e}")
        return ""
