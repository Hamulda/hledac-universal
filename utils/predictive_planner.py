"""
PredictivePlanner - Prediktivní plánování z PredictiveOrchestrator

Funkce:




- Speculative execution (spekulativní vykonávání)
- Prediction accuracy tracking
- Rollback management
- Parallel plan evaluation
"""
import asyncio
from collections import deque
from hledac.universal.utils.asyncx import safe_create_task
import logging
import time
from dataclasses import dataclass, field
import msgspec
from compat.msgspec_gc_compat import Struct
from typing import Any
logger = logging.getLogger(__name__)

class Prediction(Struct):
    """Predikce kroku"""
    action: str
    params: dict[str, Any]
    confidence: float
    predicted_at: float = field(default_factory=time.time)
    executed: bool = False
    correct: bool | None = None

class PredictionMetrics(Struct, frozen=True):
    """Metriky predikcí"""
    total_predictions: int = 0
    correct_predictions: int = 0
    incorrect_predictions: int = 0
    not_executed: int = 0

    def accuracy(self) -> float:
        """Vypočítat přesnost"""
        executed = self.correct_predictions + self.incorrect_predictions
        if executed == 0:
            return 0.0
        return self.correct_predictions / executed

    def record(self, prediction: Prediction) -> None:
        """Zaznamenat predikci"""
        self.total_predictions += 1
        if not prediction.executed:
            self.not_executed += 1
        elif prediction.correct:
            self.correct_predictions += 1
        else:
            self.incorrect_predictions += 1

class RollbackManager:
    """
    Manager pro rollback operace.

    Umožňuje vrátit změny při špatné predikci.
    """
    __slots__ = tuple(('_checkpoints',))

    def __init__(self):
        self._checkpoints: deque[dict[str, Any]] = deque(maxlen=128)

    def create_checkpoint(self, state: dict[str, Any]) -> int:
        """
        Vytvořit checkpoint.

        Args:
            state: Aktuální stav

        Returns:
            ID checkpointu
        """
        checkpoint_id = len(self._checkpoints)
        self._checkpoints.append(state.copy())
        return checkpoint_id

    def rollback(self, checkpoint_id: int) -> dict[str, Any] | None:
        """
        Rollback na checkpoint.

        Args:
            checkpoint_id: ID checkpointu

        Returns:
            Stav z checkpointu nebo None
        """
        if 0 <= checkpoint_id < len(self._checkpoints):
            return self._checkpoints[checkpoint_id]
        return None

    def clear(self) -> None:
        """Vymazat všechny checkpointy"""
        self._checkpoints.clear()

class PredictivePlanner:
    """
    Prediktivní plánovač.

    Features:
    - Předpovídá další kroky během plánování
    - Spekulativně je vykonává paralelně
    - Rollback při špatné predikci
    - Učí se z přesnosti predikcí
    """
    __slots__ = tuple(('_prediction_history', 'metrics', 'min_confidence', 'rollback_manager'))

    def __init__(self, min_confidence: float=0.7):
        self.min_confidence = min_confidence
        self.metrics = PredictionMetrics()
        self.rollback_manager = RollbackManager()
        self._prediction_history: deque[dict[str, Any]] = deque(maxlen=512)

    async def plan_with_prediction(self, planner_func, executor_func, context: dict[str, Any], max_speculative_steps: int=3) -> dict[str, Any]:
        """
        Plánovat s prediktivním vykonáváním.

        Pipeline:
        1. Start planning in background
        2. While planning, predict first steps
        3. Speculatively execute predictions
        4. Wait for planning
        5. Validate speculations
        6. Execute remaining steps

        Args:
            planner_func: Funkce pro plánování
            executor_func: Funkce pro vykonání
            context: Kontext
            max_speculative_steps: Max spekulativních kroků

        Returns:
            Výsledky
        """
        logger.info('Starting predictive planning...')
        checkpoint_id = self.rollback_manager.create_checkpoint(context)
        start_time = time.time()
        planning_task = safe_create_task(planner_func(context), name='predictive:planning')
        predictions = await self._predict_steps(context, max_speculative_steps)
        speculative_results = await self._execute_speculative(predictions, executor_func, context)
        plan = await planning_task
        validated, remaining = self._validate_predictions(predictions, plan, speculative_results)
        for pred in predictions:
            self.metrics.record(pred)
        if validated:
            logger.info('Predictions validated, continuing...')
            results = await self._execute_remaining(remaining, executor_func, context)
        else:
            logger.warning('Predictions incorrect, rolling back...')
            context = self.rollback_manager.rollback(checkpoint_id) or context
            results = await self._execute_full_plan(plan, executor_func, context)
        total_time = time.time() - start_time
        logger.info(f'Predictive planning completed in {total_time:.2f}s')
        logger.info(f'Prediction accuracy: {self.metrics.accuracy():.2%}')
        return {'results': results, 'predictions': predictions, 'accuracy': self.metrics.accuracy(), 'duration': total_time}

    async def _predict_steps(self, context: dict[str, Any], max_steps: int) -> list[Prediction]:
        """
        Predikovat další kroky.

        Args:
            context: Kontext
            max_steps: Max počet predikcí

        Returns:
            Seznam predikcí
        """
        predictions = []
        context.get('current_step', 0)
        history: list[dict[str, Any]] = context.get('history', [])
        action_counts: dict[str, int] = {}
        for h in history:
            a = h.get('action') or h.get('predicted_action', 'search')
            action_counts[a] = action_counts.get(a, 0) + 1
        preferred_action = max(action_counts, key=lambda a: action_counts[a]) if action_counts else 'search'
        for i in range(max_steps):
            prediction = Prediction(action=preferred_action, params={'query': context.get('query', '')}, confidence=0.7 - i * 0.1)
            predictions.append(prediction)
        return predictions

    async def _execute_speculative(self, predictions: list[Prediction], executor_func, context: dict[str, Any]) -> list[Any]:
        """
        Spekulativně vykonat predikce.

        Args:
            predictions: Predikce k vykonání
            executor_func: Funkce pro vykonání
            context: Kontext

        Returns:
            Výsledky
        """
        results = []
        for pred in predictions:
            if pred.confidence >= self.min_confidence:
                try:
                    result = await executor_func(pred.action, pred.params, context)
                    results.append(result)
                    pred.executed = True
                except Exception as e:
                    logger.warning(f'Speculative execution failed: {e}')
                    results.append(None)
            else:
                results.append(None)
        return results

    def _validate_predictions(self, predictions: list[Prediction], actual_plan: list[dict[str, Any]], _speculative_results: list[Any]) -> tuple:
        """
        Validovat predikce oproti skutečnému plánu.

        Args:
            predictions: Predikované kroky
            actual_plan: Skutečný plán
            speculative_results: Spekulativní výsledky

        Returns:
            (all_correct, remaining_steps)
        """
        all_correct = True
        for i, pred in enumerate(predictions):
            if i >= len(actual_plan):
                break
            actual_step = actual_plan[i]
            if pred.action == actual_step.get('action'):
                pred.correct = True
            else:
                pred.correct = False
                all_correct = False
        remaining = actual_plan[len(predictions):]
        return (all_correct, remaining)

    async def _execute_remaining(self, remaining: list[dict[str, Any]], executor_func, context: dict[str, Any]) -> list[Any]:
        """Vykonat zbývající kroky"""
        results = []
        for step in remaining:
            result = await executor_func(step.get('action'), step.get('params', {}), context)
            results.append(result)
        return results

    async def _execute_full_plan(self, plan: list[dict[str, Any]], executor_func, context: dict[str, Any]) -> list[Any]:
        """Vykonat celý plán od začátku"""
        results = []
        for step in plan:
            result = await executor_func(step.get('action'), step.get('params', {}), context)
            results.append(result)
        return results

    def get_stats(self) -> dict[str, Any]:
        """Získat statistiky predikcí"""
        return {'total_predictions': self.metrics.total_predictions, 'correct': self.metrics.correct_predictions, 'incorrect': self.metrics.incorrect_predictions, 'not_executed': self.metrics.not_executed, 'accuracy': self.metrics.accuracy()}
import asyncio
from _core import aclose