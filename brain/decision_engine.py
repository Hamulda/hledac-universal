"""
🔧 HELPER - DecisionEngine pro základní rozhodování
====================================================

DEPRECATED: This module is deprecated. Use brain/research_flow_decider.py instead.

This module is kept for backward compatibility. All new code should import from:
    from hledac.universal.brain.research_flow_decider import DecisionEngine, DecisionType, Decision
"""
import logging
import math
from dataclasses import dataclass
import msgspec
from enum import Enum
from typing import TYPE_CHECKING, Any
if TYPE_CHECKING:
    pass
logger = logging.getLogger(__name__)

class DecisionType(Enum):
    """Typy rozhodnutí"""
    RESEARCH = 'research'
    EXECUTION = 'execution'
    ANALYSIS = 'analysis'
    PLANNING = 'planning'
    SYNTHESIS = 'synthesis'
    ERROR = 'error'
    COMPLETE = 'complete'

class Decision(msgspec.Struct, gc=False):
    """Rozhodnutí orchestrátoru"""
    decision_type: DecisionType
    action: str
    params: dict[str, Any]
    reasoning: str
    confidence: float
    complete: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Konvertovat na slovník"""
        return {'type': self.decision_type.value, 'action': self.action, 'params': self.params, 'reasoning': self.reasoning, 'confidence': self.confidence, 'complete': self.complete}

class DecisionEngine:
    """
    Engine pro rozhodování o dalších krocích výzkumu.

    Podporuje více strategií:
    - rule_based: Rychlé rozhodnutí podle pravidel
    - llm_based: Rozhodnutí pomocí LLM
    - hybrid: Kombinace (pravidla + LLM pro edge cases)
    """
    __slots__ = tuple(('_bandit_counts', '_bandit_rewards', '_bandit_total_trials', '_rules', 'strategy'))

    def __init__(self, strategy: str='hybrid'):
        """
        Inicializace DecisionEngine.

        Args:
            strategy: Strategie rozhodování ("rule_based", "llm_based", "hybrid")
        """
        self.strategy = strategy
        self._rules = self._init_rules()
        self._bandit_counts: dict[tuple[str, str], int] = {}
        self._bandit_rewards: dict[tuple[str, str], float] = {}
        self._bandit_total_trials: dict[str, int] = {}

    def _init_rules(self) -> list[dict[str, Any]]:
        """Inicializovat pravidla pro rozhodování"""
        return [{'name': 'first_step_search', 'condition': lambda ctx: ctx.get('step', 0) == 0, 'action': 'search', 'params': {'query': '{query}'}, 'reasoning': 'Start with broad search'}, {'name': 'archive_fallback', 'condition': lambda ctx: ctx.get('consecutive_failures', 0) >= 2, 'action': 'archive_fallback', 'params': {'url': '{last_url}'}, 'reasoning': 'Multiple failures, try archive'}, {'name': 'fact_check_claims', 'condition': lambda ctx: len(ctx.get('claims', [])) > 0, 'action': 'fact_check', 'params': {'claims': '{claims}'}, 'reasoning': 'Verify collected claims'}, {'name': 'deep_research_complex', 'condition': lambda ctx: self._is_complex_query(ctx.get('query', '')), 'action': 'deep_research', 'params': {'query': '{query}', 'depth': 5}, 'reasoning': 'Complex query requires deep research'}, {'name': 'synthesize_complete', 'condition': lambda ctx: ctx.get('step', 0) >= ctx.get('max_steps', 20) - 2, 'action': 'synthesize', 'params': {}, 'reasoning': 'Approaching step limit, synthesize', 'complete': True}]

    def _is_complex_query(self, query: str) -> bool:
        """Detekovat komplexní dotaz"""
        complex_indicators = ['analyze', 'compare', 'contrast', 'evaluate', 'critique', 'relationship', 'impact', 'cause', 'effect', 'synthesize']
        return any((ind in query.lower() for ind in complex_indicators))

    def decide(self, context: dict[str, Any]) -> Decision:
        """
        Rozhodnout o dalším kroku.

        Args:
            context: Kontext výzkumu

        Returns:
            Decision objekt
        """
        return self._rule_based_decide(context)

    def _rule_based_decide(self, context: dict[str, Any]) -> Decision:
        """Rozhodnout podle pravidel"""
        for rule in self._rules:
            try:
                if rule['condition'](context):
                    params = {}
                    for key, value in rule.get('params', {}).items():
                        if isinstance(value, str) and value.startswith('{') and value.endswith('}'):
                            var_name = value[1:-1]
                            params[key] = context.get(var_name, value)
                        else:
                            params[key] = value
                    return Decision(decision_type=DecisionType.EXECUTION, action=rule['action'], params=params, reasoning=rule['reasoning'], confidence=0.8, complete=rule.get('complete', False))
            except Exception as e:
                logger.warning(f"Rule {rule.get('name')} failed: {e}")
                continue
        return Decision(decision_type=DecisionType.RESEARCH, action='search', params={'query': context.get('query', '')}, reasoning='No rule matched, default to search', confidence=0.5)

    def _select_bandit_action(self, input_type: str, candidates: list[str]) -> str:
        """Select module using UCB1 multi-armed bandit."""
        total = self._bandit_total_trials.get(input_type, 0) + 1
        best_score = -float('inf')
        best_action = None
        for module in candidates:
            key = (input_type, module)
            n = self._bandit_counts.get(key, 0)
            if n == 0:
                return module
            r = self._bandit_rewards.get(key, 0) / n
            exploration = math.sqrt(2 * math.log(total) / n)
            score = r + exploration
            if score > best_score:
                best_score = score
                best_action = module
        return best_action or candidates[0]

    def _update_bandit(self, input_type: str, module: str, reward: float):
        """Update bandit statistics with reward."""
        key = (input_type, module)
        self._bandit_counts[key] = self._bandit_counts.get(key, 0) + 1
        self._bandit_rewards[key] = self._bandit_rewards.get(key, 0) + reward
        self._bandit_total_trials[input_type] = self._bandit_total_trials.get(input_type, 0) + 1

    def should_continue(self, context: dict[str, Any]) -> bool:
        """
        Rozhodnout, zda pokračovat ve výzkumu.

        Args:
            context: Aktuální kontext

        Returns:
            True pokud pokračovat, False pokud ukončit
        """
        step = context.get('step', 0)
        max_steps = context.get('max_steps', 20)
        if step >= max_steps:
            return False
        if step >= max_steps * 0.8 and len(context.get('collected_data', [])) >= 5:
            return False
        if context.get('stagnation_count', 0) >= 3:
            return False
        return True