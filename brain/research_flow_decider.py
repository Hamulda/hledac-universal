"""
🔧 HELPER - DecisionEngine pro základní rozhodování
====================================================

Toto je HELPER modul pro základní rozhodovací logiku.

Používá se pouze jako pomocný nástroj pro hermes3_engine.
Pro decision making použijte CANONICAL verzi:
    from hledac.universal.brain.hermes3_engine import Hermes3Engine

Tento modul implementuje různé strategie rozhodování:
- Rule-based (rychlé, deterministic)
- LLM-based (flexibilní, kontextové)
- Hybrid (kombinace obojího)

Pro pokročilé rozhodování použijte Hermes3Engine.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from .hermes3_engine import Hermes3Engine

logger = logging.getLogger(__name__)


class DecisionType(Enum):
    """Typy rozhodnutí"""
    RESEARCH = "research"           # Výzkumná akce
    EXECUTION = "execution"         # Vykonávací akce
    ANALYSIS = "analysis"           # Analytická akce
    PLANNING = "planning"           # Plánovací akce
    SYNTHESIS = "synthesis"         # Syntéza
    ERROR = "error"                 # Chyba
    COMPLETE = "complete"           # Dokončení


@dataclass
class Decision:
    """Rozhodnutí orchestrátoru"""
    decision_type: DecisionType
    action: str
    params: Dict[str, Any]
    reasoning: str
    confidence: float  # 0-1
    complete: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        """Konvertovat na slovník"""
        return {
            "type": self.decision_type.value,
            "action": self.action,
            "params": self.params,
            "reasoning": self.reasoning,
            "confidence": self.confidence,
            "complete": self.complete,
        }


class DecisionEngine:
    """
    Engine pro rozhodování o dalších krocích výzkumu.
    
    Podporuje více strategií:
    - rule_based: Rychlé rozhodnutí podle pravidel
    - llm_based: Rozhodnutí pomocí LLM
    - hybrid: Kombinace (pravidla + LLM pro edge cases)
    """
    
    def __init__(self, strategy: str = "hybrid", hermes: Optional["Hermes3Engine"] = None):
        """
        Inicializace DecisionEngine.

        Args:
            strategy: Strategie rozhodování ("rule_based", "llm_based", "hybrid")
            hermes: Volitelná instance Hermes3Engine pro LLM fallback
        """
        self.strategy = strategy
        self._hermes = hermes

        # Pravidla pro rule-based rozhodování
        self._rules = self._init_rules()
        
    def _init_rules(self) -> List[Dict[str, Any]]:
        """Inicializovat pravidla pro rozhodování"""
        return [
            {
                "name": "first_step_search",
                "condition": lambda ctx: ctx.get("step", 0) == 0,
                "action": "search",
                "params": {"query": "{query}"},
                "reasoning": "Start with broad search",
            },
            {
                "name": "archive_fallback",
                "condition": lambda ctx: ctx.get("consecutive_failures", 0) >= 2,
                "action": "archive_fallback",
                "params": {"url": "{last_url}"},
                "reasoning": "Multiple failures, try archive",
            },
            {
                "name": "fact_check_claims",
                "condition": lambda ctx: len(ctx.get("claims", [])) > 0,
                "action": "fact_check",
                "params": {"claims": "{claims}"},
                "reasoning": "Verify collected claims",
            },
            {
                "name": "deep_research_complex",
                "condition": lambda ctx: self._is_complex_query(ctx.get("query", "")),
                "action": "deep_research",
                "params": {"query": "{query}", "depth": 5},
                "reasoning": "Complex query requires deep research",
            },
            {
                "name": "synthesize_complete",
                "condition": lambda ctx: ctx.get("step", 0) >= ctx.get("max_steps", 20) - 2,
                "action": "synthesize",
                "params": {},
                "reasoning": "Approaching step limit, synthesize",
                "complete": True,
            },
        ]
    
    def _is_complex_query(self, query: str) -> bool:
        """Detekovat komplexní dotaz"""
        complex_indicators = [
            "analyze", "compare", "contrast", "evaluate", "critique",
            "relationship", "impact", "cause", "effect", "synthesize"
        ]
        return any(ind in query.lower() for ind in complex_indicators)
    
    def decide(self, context: Dict[str, Any]) -> Decision:
        """
        Rozhodnout o dalším kroku.
        
        Args:
            context: Kontext výzkumu
            
        Returns:
            Decision objekt
        """
        if self.strategy == "rule_based":
            return self._rule_based_decide(context)
        elif self.strategy == "llm_based":
            return self._llm_based_decide(context)
        else:  # hybrid
            return self._hybrid_decide(context)
    
    def _rule_based_decide(self, context: Dict[str, Any]) -> Decision:
        """Rozhodnout podle pravidel"""
        for rule in self._rules:
            try:
                if rule["condition"](context):
                    # Substitute params
                    params = {}
                    for key, value in rule.get("params", {}).items():
                        if isinstance(value, str) and value.startswith("{") and value.endswith("}"):
                            var_name = value[1:-1]
                            params[key] = context.get(var_name, value)
                        else:
                            params[key] = value
                    
                    return Decision(
                        decision_type=DecisionType.EXECUTION,
                        action=rule["action"],
                        params=params,
                        reasoning=rule["reasoning"],
                        confidence=0.8,
                        complete=rule.get("complete", False),
                    )
            except Exception as e:
                logger.warning(f"Rule {rule.get('name')} failed: {e}")
                continue
        
        # Default: search
        return Decision(
            decision_type=DecisionType.RESEARCH,
            action="search",
            params={"query": context.get("query", "")},
            reasoning="No rule matched, default to search",
            confidence=0.5,
        )
    
    def _call_hermes_decide(self, context: Dict[str, Any]):
        """
        M1-safe helper: call hermes.decide_next_action from sync context.

        Uses loop.run_until_complete() when running inside an event loop,
        or creates a new event loop otherwise.
        """
        coro = self._hermes.decide_next_action(context)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # M1-SAFE: create new event loop in worker thread
            new_loop = asyncio.new_event_loop()
            try:
                return new_loop.run_until_complete(coro)
            finally:
                new_loop.close()
        # M1-SAFE: use run_until_complete on existing loop
        return loop.run_until_complete(coro)

    def _llm_based_decide(self, context: Dict[str, Any]) -> Decision:
        """Rozhodnout pomocí LLM"""
        if self._hermes is None:
            logger.warning("LLM requested but hermes not available, falling back to rules")
            return self._rule_based_decide(context)

        try:
            llm_result = self._call_hermes_decide(context)
            if llm_result and isinstance(llm_result, dict):
                return Decision(
                    decision_type=DecisionType.RESEARCH,
                    action=llm_result.get("action", "search"),
                    params=llm_result.get("params", {}),
                    reasoning=f"[LLM] {llm_result.get('reasoning', 'LLM decision')}",
                    confidence=0.9,
                    complete=llm_result.get("complete", False),
                )
        except Exception as e:
            logger.warning(f"LLM decision failed: {e}, falling back to rules")
        return self._rule_based_decide(context)

    def _hybrid_decide(self, context: Dict[str, Any]) -> Decision:
        """Kombinované rozhodování"""
        rule_decision = self._rule_based_decide(context)

        if rule_decision.confidence < 0.7 and self._hermes is not None:
            try:
                llm_result = self._call_hermes_decide(context)
                if llm_result and isinstance(llm_result, dict):
                    return Decision(
                        decision_type=DecisionType.RESEARCH,
                        action=llm_result.get("action", rule_decision.action),
                        params=llm_result.get("params", rule_decision.params),
                        reasoning=f"[LLM] {llm_result.get('reasoning', rule_decision.reasoning)}",
                        confidence=0.85,
                        complete=llm_result.get("complete", rule_decision.complete),
                    )
            except Exception as e:
                logger.warning(f"LLM fallback failed: {e}, using rule decision")

        return rule_decision
    
    def should_continue(self, context: Dict[str, Any]) -> bool:
        """
        Rozhodnout, zda pokračovat ve výzkumu.
        
        Args:
            context: Aktuální kontext
            
        Returns:
            True pokud pokračovat, False pokud ukončit
        """
        step = context.get("step", 0)
        max_steps = context.get("max_steps", 20)
        
        # Hard limit
        if step >= max_steps:
            return False
        
        # Soft limit - dostatek dat
        if step >= max_steps * 0.8 and len(context.get("collected_data", [])) >= 5:
            return False
        
        # Stagnation detection
        if context.get("stagnation_count", 0) >= 3:
            return False
        
        return True
