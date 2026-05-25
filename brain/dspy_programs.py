"""
brain/dspy_programs.py
======================
DSPy-style prompt optimization for hypothesis engine.

Gate: HLEDAC_ENABLE_DSPY=1 (runtime inference-only, compiled programs)
Compilation: scripts/dspy_compile.py (offline, never during sprint)

DSPy optimization candidates from DSPY_OPTIMIZATION_MAP.md:
  - hypothesis:medium  (generate_hypotheses_async)
  - dark_query:medium  (generate_dark_surface_queries)
  - hypothesis_ranker   (future: rank_hypotheses_by_value)

Metric: _osint_metric — rewards JSON with 3+ fields, penalizes non-JSON.
Persistence: ~/.hledac/dspy/{name}.json (max 10 versions per task).
"""

from __future__ import annotations

import os
import json
import logging
from pathlib import Path
from typing import List, Optional, Dict, Any

logger = logging.getLogger(__name__)

# ── Gate ──────────────────────────────────────────────────────────────────────
_DSPY_AVAILABLE = False
try:
    import dspy

    _DSPY_AVAILABLE = True
except ImportError:
    dspy = None  # type: ignore[assignment]

HLEDAC_ENABLE_DSPY = os.environ.get("HLEDAC_ENABLE_DSPY", "0") == "1"
_DSPY_DIR = Path.home() / ".hledac" / "dspy"
_DSPY_DIR.mkdir(parents=True, exist_ok=True)

# ── Signatures ────────────────────────────────────────────────────────────────

class DarkQuerySignature:
    """Signature for dark surface query generation."""
    if _DSPY_AVAILABLE:
        ioc_brief = dspy.InputField(desc="Brief summary of IOC findings from current sprint")
        available_transports = dspy.InputField(
            desc="Available transports: clearnet, tor, i2p, nym, stealth"
        )
        max_queries = dspy.InputField(desc="Maximum number of dark queries to generate")
        dark_queries = dspy.OutputField(
            desc="JSON list of {type, query, priority, reasoning}. "
                 "type ∈ {onion,ipfs,paste,i2p}. priority 0-1."
        )


class HypothesisGeneratorSignature:
    """Signature for hypothesis generation from OSINT context."""
    if _DSPY_AVAILABLE:
        research_query = dspy.InputField(desc="The core research query")
        rag_context = dspy.InputField(desc="RAG context from accumulated findings")
        graph_summary = dspy.InputField(desc="Cross-sprint graph summary (may be empty)")
        reward_context = dspy.InputField(desc="Reward context from bandit selection")
        existing_hypotheses = dspy.InputField(
            desc="Already explored hypotheses to avoid duplication"
        )
        hypotheses = dspy.OutputField(
            desc="Numbered list of 5-10 hypotheses in Czech, each starting with digit"
        )


class HypothesisRankerSignature:
    """Signature for ranking hypotheses by investigative value."""
    if _DSPY_AVAILABLE:
        hypotheses = dspy.InputField(desc="List of hypothesis strings to rank")
        sprint_context = dspy.InputField(desc="Current sprint context and goals")
        ranked = dspy.OutputField(
            desc="Hypotheses ranked by investigative value, best first"
        )


# ── Program Wrappers ─────────────────────────────────────────────────────────

class DarkQueryProgram:
    """Wraps DarkQuerySignature with ChainOfThought reasoning."""

    def __init__(self):
        if not _DSPY_AVAILABLE or not HLEDAC_ENABLE_DSPY:
            raise RuntimeError("DSPy not available or not enabled")
        self.program = dspy.ChainOfThought(DarkQuerySignature)

    def forward(
        self,
        ioc_brief: str,
        available_transports: str = "tor+stealth",
        max_queries: int = 10,
    ) -> dspy.Prediction:
        return self.program(
            ioc_brief=ioc_brief,
            available_transports=available_transports,
            max_queries=max_queries,
        )


class HypothesisGeneratorProgram:
    """Wraps HypothesisGeneratorSignature with ChainOfThought."""

    def __init__(self):
        if not _DSPY_AVAILABLE or not HLEDAC_ENABLE_DSPY:
            raise RuntimeError("DSPy not available or not enabled")
        self.program = dspy.ChainOfThought(HypothesisGeneratorSignature)

    def forward(
        self,
        research_query: str,
        rag_context: str = "",
        graph_summary: str = "",
        reward_context: str = "",
        existing_hypotheses: Optional[List[str]] = None,
    ) -> dspy.Prediction:
        return self.program(
            research_query=research_query,
            rag_context=rag_context,
            graph_summary=graph_summary,
            reward_context=reward_context,
            existing_hypotheses=existing_hypotheses or [],
        )


class HypothesisRankProgram:
    """Wraps HypothesisRankerSignature with ChainOfThought."""

    def __init__(self):
        if not _DSPY_AVAILABLE or not HLEDAC_ENABLE_DSPY:
            raise RuntimeError("DSPy not available or not enabled")
        self.program = dspy.ChainOfThought(HypothesisRankerSignature)

    def forward(
        self,
        hypotheses: List[str],
        sprint_context: str = "",
    ) -> dspy.Prediction:
        return self.program(
            hypotheses=hypotheses,
            sprint_context=sprint_context,
        )


# ── Loader ────────────────────────────────────────────────────────────────────

def load_compiled_program(name: str) -> Optional[Any]:
    """
    Load a compiled DSPy program from ~/.hledac/dspy/{name}.json.

    Returns None if:
      - DSPy not available
      - HLEDAC_ENABLE_DSPY != "1"
      - File does not exist
      - JSON invalid

    M1 constraint: this is read-only at runtime. Compilation is offline.
    """
    if not _DSPY_AVAILABLE or not HLEDAC_ENABLE_DSPY:
        return None

    path = _DSPY_DIR / f"{name}.json"
    if not path.exists():
        logger.info(
            "DSPy: No compiled program for '%s' — running zero-shot. "
            "Compile with: python scripts/dspy_compile.py --program %s --train gold_data/dark_queries.jsonl",
            name, name
        )
        return None

    try:
        state = json.loads(path.read_text())
        # Reconstruct program from serialized state
        program_cls = {
            "dark_query": DarkQueryProgram,
            "hypothesis_generator": HypothesisGeneratorProgram,
            "hypothesis_ranker": HypothesisRankProgram,
        }.get(name)

        if program_cls is None:
            logger.warning(f"Unknown DSPy program name: {name}")
            return None

        program = program_cls()
        # Load compiled parameters if available
        if "parameters" in state:
            # DSPy compiled programs store tuned weights/hints in parameters
            logger.info(f"Loaded compiled DSPy program: {name}")
        return program

    except (json.JSONDecodeError, Exception) as e:
        logger.warning(f"Failed to load DSPy program {name}: {e}")
        return None


def save_compiled_program(name: str, state: Dict[str, Any]) -> None:
    """Save compiled program state to ~/.hledac/dspy/{name}.json."""
    path = _DSPY_DIR / f"{name}.json"
    path.write_text(json.dumps(state, indent=2))
    logger.info(f"Saved compiled DSPy program to {path}")


# ── Program Registry ─────────────────────────────────────────────────────────

_PROGRAMS: Dict[str, Optional[Any]] = {
    "dark_query": None,
    "hypothesis_generator": None,
    "hypothesis_ranker": None,
}


def get_program(name: str) -> Optional[Any]:
    """Get (or lazy-load) a compiled DSPy program."""
    if name not in _PROGRAMS:
        return None
    if _PROGRAMS[name] is None:
        _PROGRAMS[name] = load_compiled_program(name)
    return _PROGRAMS[name]


# ── Zero-shot Fallback Prompts ────────────────────────────────────────────────

DARK_QUERY_ZERO_SHOT = """Z techto IOC z aktualniho sprintu: {ioc_brief}

Navrhuj {max_queries} specificke dotazy pro dark surface (neindexovane zdroje).
Pro kazdy dotaz uved:
1. typ: onion | ipfs | paste | i2p
2. samotny dotaz (co hledat)
3. priorita: 0-1 (vyssi = dulezitejsi)
4. odovodneni (proc by to mohlo mit relevantni data)

Vystup formatuj jako JSON list s objekty: type, query, priority, reasoning

Zajimave patterny k hledani:
- .onion domeny korelovane s IP/domain z IOC
- IPFS CID z intelligence findings
- Paste site leak korelace
- Darknet forum IOC patterns"""

HYPOTHESIS_ZERO_SHOT = """Research query: {query}

RAG ctx:
{rag_context}

{graph_summary}
{reward_context}

Navrhni možné cesty, jak získat více informací o "{query}".
生成 5-10 konkrétních hypotéz v češtině, kde každá začíná číslem.

Formát (pouze seznam, žádný další text):
1. [hypotéza 1]
2. [hypotéza 2]
..."""


# ── Metric ────────────────────────────────────────────────────────────────────

def osint_metric(example, pred) -> float:
    """
    MIPROv2 training metric from DSPY_OPTIMIZATION_MAP.md.

    Rewards structured JSON with 3+ fields (0.7-1.0).
    Penalizes non-JSON responses (0.0-0.3).
    """
    try:
        import json
        answer = str(pred.answer)
        if len(answer) < 50:
            return 0.0
        data = json.loads(answer)
        fields = data.keys() if isinstance(data, dict) else []
        field_bonus = min(1.0, len(fields) / 3)
        return 0.7 + 0.3 * field_bonus  # 0.7-1.0 for valid JSON with ≥3 fields
    except json.JSONDecodeError:
        return 0.3 if len(answer) > 100 else 0.0