"""
Universal Execution Coordinator
===============================

Integrated execution coordination combining:
- DeepSeek R1: GhostDirector + Parallel + Ray Cluster
- Hermes3: Simplified initialization patterns
- M1 Master: Memory-aware task scheduling

Unique Features Integrated:
1. Multi-backend execution (GhostDirector → Parallel → Ray)
2. Dynamic task generation based on confidence
3. Mission-based execution (GhostDirector missions)
4. Distributed task distribution (Ray cluster)
5. Parallel task optimization with priorities
6. Execution result aggregation
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from .base import DecisionResponse, OperationResult, OperationType, UniversalCoordinator

logger = logging.getLogger(__name__)


@dataclass
class ExecutionTask:
    """Definition of an execution task."""
    task_id: str
    description: str
    priority: str  # 'critical', 'high', 'medium', 'low'
    executor: str  # 'ghost', 'parallel', 'ray'
    payload: dict[str, Any] = field(default_factory=dict)
    timeout: float = 60.0
    retries: int = 0


@dataclass
class ExecutionResult:
    """
    Result of task execution.

    NON-CANONICAL LOCAL SCAFFOLD (Sprint 8VF):
    ════════════════════════════════════════════════
    This dataclass is a LOCAL scaffold — it does NOT belong to the
    canonical types.py scaffold surface.

    RELATIONSHIP TO CANONICAL:
    - Canonical ExecutionResult is types.py:1441 (ExecutionResult with slots=True)
    - This local version exists because execution_coordinator owns its own
      task result model independently of the canonical handoff pipeline
    - The two are NOT aligned — field names differ

    BOUNDARY RULE (Sprint 8VF):
    - execution_coordinator may use this local type internally
    - Any CROSS-COMPONENT handoff that carries execution state to another
      component (e.g. analyzer→router, windup→export) MUST use the canonical
      types.py ExecutionResult, not this one

    MIGRATION CONDITION:
    When execution_coordinator is refactored to pass typed handoffs through
    the canonical pipeline, this local ExecutionResult becomes redundant.

    See: types.py CANONICAL SCAFFOLD header (line 1269)
    """
    task_id: str
    success: bool
    summary: str
    executor: str
    execution_time: float
    result_data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class UniversalExecutionCoordinator(UniversalCoordinator):
    """
    Universal coordinator for execution operations.

    Integrates three execution backends:
    1. GhostDirector - Mission-based execution
    2. ParallelExecutionOptimizer - Parallel task processing
    3. RayClusterManager - Distributed cluster execution

    Routing Strategy:
    - 'ghost'/'director'/'mission' → GhostDirector
    - 'parallel'/'distributed' → Parallel Execution
    - 'ray'/'cluster' → Ray Cluster
    - Default → GhostDirector (with fallback chain)

    Task Generation:
    - Dynamic task count based on decision.confidence
    - Priority based on confidence threshold
    """

    def __init__(self, max_concurrent: int = 10):
        super().__init__(
            name="universal_execution_coordinator",
            max_concurrent=max_concurrent,
            memory_aware=True
        )

        # Execution subsystems (lazy initialization)
        self._ghost_director: Any | None = None
        self._parallel_executor: Any | None = None
        self._ray_cluster: Any | None = None

        # Availability flags
        self._ghost_available = False
        self._parallel_available = False  # Orphaned: hledac.tools.preserved_logic.parallel_execution_optimizer does not exist
        self._ray_available = False

        # Configuration
        self._ghost_max_steps = 10
        self._parallel_max_tasks = 5
        self._ray_max_tasks = 10

        # Task tracking
        self._pending_tasks: dict[str, ExecutionTask] = {}
        self._completed_tasks: dict[str, ExecutionResult] = {}
        self._max_completed_history = 100

        # Execution metrics
        self._ghost_executions = 0
        self._parallel_executions = 0
        self._ray_executions = 0

        # Hermes3: Action history
        self._action_history: deque = deque()
        self._max_history = 100

    # ========================================================================
    # Initialization
    # ========================================================================

    async def _do_initialize(self) -> bool:
        """Initialize execution subsystems with graceful degradation."""
        initialized_any = False

        # Try GhostDirector
        try:
            from hledac.cortex.director import GhostDirector
            self._ghost_director = GhostDirector(max_steps=self._ghost_max_steps)
            self._ghost_available = True
            initialized_any = True
            logger.info("ExecutionCoordinator: GhostDirector initialized")
        except (ImportError, IndentationError):
            logger.warning("ExecutionCoordinator: GhostDirector not available")
        except Exception as e:
            logger.warning(f"ExecutionCoordinator: GhostDirector init failed: {e}")

        # Try ParallelExecutionOptimizer
        try:
            from hledac.tools.preserved_logic.parallel_execution_optimizer import ParallelExecutionOptimizer
            self._parallel_executor = ParallelExecutionOptimizer()
            if hasattr(self._parallel_executor, 'initialize'):
                await self._parallel_executor.initialize()
            self._parallel_available = True
            initialized_any = True
            logger.info("ExecutionCoordinator: ParallelExecutionOptimizer initialized")
        except ImportError:
            logger.warning("ExecutionCoordinator: ParallelExecutionOptimizer not available")
        except Exception as e:
            logger.warning(f"ExecutionCoordinator: ParallelExecutor init failed: {e}")

        # Try RayClusterManager
        try:
            from hledac.distributed_computing.ray_cluster import RayClusterManager
            self._ray_cluster = RayClusterManager()
            if hasattr(self._ray_cluster, 'initialize'):
                await self._ray_cluster.initialize()
            self._ray_available = True
            initialized_any = True
            logger.info("ExecutionCoordinator: RayClusterManager initialized")
        except ImportError:
            logger.warning("ExecutionCoordinator: RayClusterManager not available")
        except Exception as e:
            logger.warning(f"ExecutionCoordinator: RayCluster init failed: {e}")

        return initialized_any

    async def _do_cleanup(self) -> None:
        """Cleanup execution subsystems."""
        if self._ghost_director and hasattr(self._ghost_director, 'cleanup'):
            try:
                await self._ghost_director.cleanup()
            except Exception as e:
                logger.error(f"Error cleaning up GhostDirector: {e}")

        if self._parallel_executor and hasattr(self._parallel_executor, 'cleanup'):
            try:
                await self._parallel_executor.cleanup()
            except Exception as e:
                logger.error(f"Error cleaning up ParallelExecutor: {e}")

        if self._ray_cluster and hasattr(self._ray_cluster, 'cleanup'):
            try:
                await self._ray_cluster.cleanup()
            except Exception as e:
                logger.error(f"Error cleaning up RayCluster: {e}")

        self._pending_tasks.clear()
        self._completed_tasks.clear()

    # ========================================================================
    # Core Operations
    # ========================================================================

    def get_supported_operations(self) -> list[OperationType]:
        """Return supported operation types."""
        return [OperationType.EXECUTION]

    async def handle_request(
        self,
        operation_ref: str,
        decision: DecisionResponse
    ) -> OperationResult:
        """
        Handle execution request with intelligent routing.

        Args:
            operation_ref: Unique operation reference
            decision: Execution decision with routing info

        Returns:
            OperationResult with execution outcome
        """
        start_time = time.time()
        operation_id = self.generate_operation_id()

        try:
            # Track operation
            self.track_operation(operation_id, {
                'operation_ref': operation_ref,
                'decision': decision,
                'type': 'execution'
            })

            # Route to appropriate execution method
            result = await self._execute_decision(decision)

            # Create operation result
            operation_result = OperationResult(
                operation_id=operation_id,
                status="completed" if result.success else "failed",
                result_summary=result.summary,
                execution_time=time.time() - start_time,
                success=result.success,
                metadata={
                    'executor': result.executor,
                    'tasks_executed': 1 if result.executor == 'ghost' else result.result_data.get('task_count', 1),
                }
            )

        except Exception as e:
            operation_result = OperationResult(
                operation_id=operation_id,
                status="failed",
                result_summary=f"Execution failed: {str(e)}",
                execution_time=time.time() - start_time,
                success=False,
                error_message=str(e)
            )
        finally:
            self.untrack_operation(operation_id)

        # Record metrics
        self.record_operation_result(operation_result)
        return operation_result

    # ========================================================================
    # Execution Routing
    # ========================================================================

    async def _execute_decision(self, decision: DecisionResponse) -> ExecutionResult:
        """
        Route execution decision to appropriate backend.

        Routing logic:
        1. Parse chosen_option for routing hints
        2. Try primary backend
        3. Fallback to alternatives if needed
        """
        chosen = decision.chosen_option.lower()

        # Determine primary executor
        if 'ghost' in chosen or 'director' in chosen or 'mission' in chosen:
            primary = 'ghost'
        elif 'parallel' in chosen or 'concurrent' in chosen:
            primary = 'parallel'
        elif 'ray' in chosen or 'cluster' in chosen or 'distributed' in chosen:
            primary = 'ray'
        else:
            primary = 'ghost'  # Default

        # Build fallback chain
        fallback_chain = [primary] + [e for e in ['ghost', 'parallel', 'ray'] if e != primary]

        # Try each executor in order
        last_error = None
        for executor in fallback_chain:
            try:
                if executor == 'ghost' and self._ghost_available:
                    return await self._execute_ghost_director(decision)
                elif executor == 'parallel' and self._parallel_available:
                    return await self._execute_parallel_processing(decision)
                elif executor == 'ray' and self._ray_available:
                    return await self._execute_ray_cluster(decision)
            except Exception as e:
                last_error = e
                logger.warning(f"Execution backend '{executor}' failed: {e}")
                continue

        # All backends failed
        return ExecutionResult(
            task_id='none',
            success=False,
            summary=f'All execution backends failed. Last error: {last_error}',
            executor='none',
            execution_time=0.0,
            error=str(last_error)
        )

    async def _execute_ghost_director(
        self,
        decision: DecisionResponse
    ) -> ExecutionResult:
        """Execute using GhostDirector (mission-based)."""
        start_time = time.time()

        if not self._ghost_director:
            raise RuntimeError("GhostDirector not available")

        # Create mission from decision
        mission = {
            'objective': decision.reasoning,
            'confidence': decision.confidence,
            'estimated_duration': decision.estimated_duration,
            'decision_id': decision.decision_id,
            'priority': decision.priority,
            'metadata': decision.metadata
        }

        # Execute mission
        result = await self._ghost_director.execute_mission(mission)

        execution_time = time.time() - start_time
        self._ghost_executions += 1

        return ExecutionResult(
            task_id=decision.decision_id,
            success=result.get('success', False),
            summary=result.get('summary', 'Ghost mission completed'),
            executor='ghost',
            execution_time=execution_time,
            result_data=result
        )

    async def _execute_parallel_processing(
        self,
        decision: DecisionResponse
    ) -> ExecutionResult:
        """Execute using ParallelExecutionOptimizer."""
        start_time = time.time()

        if not self._parallel_executor:
            raise RuntimeError("ParallelExecutionOptimizer not available")

        # Generate tasks based on confidence
        # Higher confidence = more tasks
        num_tasks = self._calculate_task_count(decision.confidence, self._parallel_max_tasks)

        tasks = self._generate_tasks(decision, num_tasks)

        # Execute in parallel
        results = await self._parallel_executor.execute_parallel(tasks)

        execution_time = time.time() - start_time
        self._parallel_executions += 1

        # Aggregate results
        success_count = sum(1 for r in results if r.get('success', False))

        return ExecutionResult(
            task_id=f"parallel_{decision.decision_id}",
            success=success_count > 0,
            summary=f'Parallel execution: {success_count}/{len(results)} tasks succeeded',
            executor='parallel',
            execution_time=execution_time,
            result_data={
                'task_count': len(results),
                'success_count': success_count,
                'tasks': results
            }
        )

    async def _execute_ray_cluster(
        self,
        decision: DecisionResponse
    ) -> ExecutionResult:
        """Execute using RayClusterManager."""
        start_time = time.time()

        if not self._ray_cluster:
            raise RuntimeError("RayClusterManager not available")

        # Generate distributed tasks
        num_tasks = self._calculate_task_count(decision.confidence, self._ray_max_tasks)

        tasks = [f'{decision.decision_id}_task_{i}' for i in range(num_tasks)]

        # Distribute across cluster
        results = await self._ray_cluster.distribute_tasks(tasks)

        execution_time = time.time() - start_time
        self._ray_executions += 1

        return ExecutionResult(
            task_id=f"ray_{decision.decision_id}",
            success=len(results) > 0,
            summary=f'Distributed execution: {len(results)} tasks across cluster',
            executor='ray',
            execution_time=execution_time,
            result_data={
                'task_count': len(results),
                'distributed': True,
                'tasks': results
            }
        )

    # ========================================================================
    # Task Generation
    # ========================================================================

    def _calculate_task_count(self, confidence: float, max_tasks: int) -> int:
        """
        Calculate number of tasks based on confidence.

        Higher confidence = more parallelization opportunity
        """
        # Linear scaling: confidence 0.5 → 50% of max, 1.0 → 100%
        base_count = max(1, int(confidence * max_tasks))
        return min(base_count, max_tasks)

    def _generate_tasks(
        self,
        decision: DecisionResponse,
        count: int
    ) -> list[dict[str, Any]]:
        """
        Generate task definitions for parallel execution.

        Assigns priorities based on confidence:
        - confidence > 0.8: high priority
        - confidence > 0.5: medium priority
        - otherwise: low priority
        """
        priority = 'high' if decision.confidence > 0.8 else ('medium' if decision.confidence > 0.5 else 'low')

        tasks = []
        for i in range(count):
            task = {
                'id': f'{decision.decision_id}_task_{i}',
                'description': f"Execute {decision.chosen_option} (part {i+1}/{count})",
                'priority': priority,
                'parent_decision': decision.decision_id,
                'metadata': decision.metadata
            }
            tasks.append(task)

        return tasks

    # ========================================================================
    # Advanced Execution Features
    # ========================================================================

    async def execute_with_fallback(
        self,
        task: ExecutionTask,
        fallback_chain: list[str] | None = None
    ) -> ExecutionResult:
        """
        Execute task with automatic fallback between backends.

        Unique feature: Tries primary executor, falls back to others on failure.

        Args:
            task: Task to execute
            fallback_chain: Ordered list of executors to try

        Returns:
            Execution result from first successful executor
        """
        if fallback_chain is None:
            fallback_chain = ['ghost', 'parallel', 'ray']

        # Ensure primary executor is first
        if task.executor in fallback_chain:
            fallback_chain.remove(task.executor)
        fallback_chain = [task.executor] + fallback_chain

        last_error = None
        for executor in fallback_chain:
            try:
                # Create minimal decision for task
                decision = DecisionResponse(
                    decision_id=task.task_id,
                    chosen_option=executor,
                    confidence=0.7,
                    reasoning=task.description,
                    estimated_duration=task.timeout
                )

                if executor == 'ghost' and self._ghost_available:
                    result = await self._execute_ghost_director(decision)
                    if result.success:
                        return result
                elif executor == 'parallel' and self._parallel_available:
                    result = await self._execute_parallel_processing(decision)
                    if result.success:
                        return result
                elif executor == 'ray' and self._ray_available:
                    result = await self._execute_ray_cluster(decision)
                    if result.success:
                        return result

            except Exception as e:
                last_error = e
                logger.warning(f"Fallback execution failed for '{executor}': {e}")
                continue

        return ExecutionResult(
            task_id=task.task_id,
            success=False,
            summary=f'All fallback executors failed. Last error: {last_error}',
            executor='none',
            execution_time=0.0,
            error=str(last_error)
        )

    async def execute_batch(
        self,
        tasks: list[ExecutionTask],
        max_parallel: int = 5
    ) -> list[ExecutionResult]:
        """
        Execute batch of tasks with controlled parallelism.

        Args:
            tasks: List of tasks to execute
            max_parallel: Maximum concurrent executions

        Returns:
            List of execution results
        """
        semaphore = asyncio.Semaphore(max_parallel)

        async def execute_with_limit(task: ExecutionTask) -> ExecutionResult:
            async with semaphore:
                decision = DecisionResponse(
                    decision_id=task.task_id,
                    chosen_option=task.executor,
                    confidence=0.7 if task.priority == 'high' else 0.5,
                    reasoning=task.description,
                    estimated_duration=task.timeout
                )
                return await self._execute_decision(decision)

        # Execute all tasks with parallelism limit
        # F206K: Added return_exceptions=True — one failure shouldn't cancel other tasks
        results = await asyncio.gather(*[
            execute_with_limit(task) for task in tasks
        ], return_exceptions=True)

        # Filter exceptions — return only successful ExecutionResult
        return [r for r in results if not isinstance(r, Exception)]

    # ========================================================================
    # Task Tracking
    # ========================================================================

    def register_task(self, task: ExecutionTask) -> str:
        """Register task for tracking."""
        self._pending_tasks[task.task_id] = task
        return task.task_id

    def complete_task(self, result: ExecutionResult) -> None:
        """Mark task as completed."""
        if result.task_id in self._pending_tasks:
            del self._pending_tasks[result.task_id]

        self._completed_tasks[result.task_id] = result

        # Trim history
        while len(self._completed_tasks) > self._max_completed_history:
            oldest = next(iter(self._completed_tasks))
            del self._completed_tasks[oldest]

    def get_task_status(self, task_id: str) -> dict[str, Any] | None:
        """Get status of specific task."""
        if task_id in self._pending_tasks:
            return {'status': 'pending', 'task': self._pending_tasks[task_id]}
        if task_id in self._completed_tasks:
            result = self._completed_tasks[task_id]
            return {'status': 'completed', 'result': result}
        return None

    def get_pending_tasks(self) -> list[str]:
        """Get list of pending task IDs."""
        return list(self._pending_tasks.keys())

    def get_completed_tasks(self, limit: int = 10) -> list[ExecutionResult]:
        """Get recently completed tasks."""
        return list(self._completed_tasks.values())[-limit:]

    # ========================================================================
    # Reporting
    # ========================================================================

    def _get_feature_list(self) -> list[str]:
        """Report available features."""
        features = ["Multi-backend execution routing"]

        if self._ghost_available:
            features.append("GhostDirector Mission Execution")
        if self._parallel_available:
            features.append("Parallel Task Processing")
        if self._ray_available:
            features.append("Ray Cluster Distribution")

        features.extend([
            "Dynamic task generation",
            "Automatic fallback chain",
            "Batch execution with parallelism control",
            "Task tracking and history",
            "Confidence-based routing"
        ])

        return features

    def get_execution_stats(self) -> dict[str, Any]:
        """Get execution statistics."""
        return {
            'ghost_executions': self._ghost_executions,
            'parallel_executions': self._parallel_executions,
            'ray_executions': self._ray_executions,
            'total_executions': self._ghost_executions + self._parallel_executions + self._ray_executions,
            'pending_tasks': len(self._pending_tasks),
            'completed_history': len(self._completed_tasks),
        }

    def get_available_executors(self) -> dict[str, bool]:
        """Get availability status of all executors."""
        return {
            'ghost': self._ghost_available,
            'parallel': self._parallel_available,
            'ray': self._ray_available
        }

    # ========================================================================
    # Hermes3 Integration - Action History and Plan Execution
    # ========================================================================

    async def execute_action(
        self,
        action_type: str,
        payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """
        Execute a single action via GhostDirector (from Hermes3).

        Args:
            action_type: Type of action to execute
            payload: Action payload

        Returns:
            Action execution result
        """
        if not self._ghost_available or not self._ghost_director:
            return {
                'success': False,
                'error': 'GhostDirector not available',
                'action': action_type
            }

        try:
            from hledac.cortex.director import DirectorAction

            # Initialize director if needed
            if hasattr(self._ghost_director, 'initialize_drivers'):
                await self._ghost_director.initialize_drivers()

            # Execute action
            action = DirectorAction(action_type.upper())
            result = await self._ghost_director._act(action, payload or {}, {})

            # Track action in history
            self._action_history.append({
                'timestamp': time.time(),
                'action': action_type,
                'payload': payload,
                'result': result,
                'success': True
            })

            # Trim history if needed
            while len(self._action_history) > self._max_history:
                self._action_history.popleft()

            return {
                'success': True,
                'action': action_type,
                'result': result
            }

        except Exception as e:
            logger.error(f"Action execution failed: {e}")

            # Track failed action
            self._action_history.append({
                'timestamp': time.time(),
                'action': action_type,
                'payload': payload,
                'error': str(e),
                'success': False
            })

            return {
                'success': False,
                'error': str(e),
                'action': action_type
            }

    # ========================================================================
    # Speculative Decoding Integration (from speculative_decoding/)
    # ========================================================================

    async def generate_with_speculative_decoding(
        self,
        prompt: str,
        max_tokens: int = 100,
        mode: str = "balanced",
        draft_model_path: str | None = None
    ) -> dict[str, Any]:
        """
        Generate text using speculative decoding for faster inference.

        Integrated from: speculative_decoding/speculative_engine.py

        Features:
        - Draft-then-verify approach using smaller draft model
        - MLX integration for M1 optimization
        - Adaptive K based on acceptance rate
        - Multiple decoding modes (FAST, QUALITY, BALANCED)

        Args:
            prompt: Input prompt
            max_tokens: Maximum tokens to generate
            mode: Decoding mode ('fast', 'quality', 'balanced')
            draft_model_path: Path to draft model (optional)

        Returns:
            Generation result with text and metrics
        """
        try:
            from hledac.speculative_decoding.speculative_engine import (
                DecodingMode,
                SpeculationConfig,
                SpeculativeEngine,
            )

            # Map mode string to enum
            mode_map = {
                'fast': DecodingMode.FAST,
                'quality': DecodingMode.QUALITY,
                'balanced': DecodingMode.BALANCED
            }
            decoding_mode = mode_map.get(mode, DecodingMode.BALANCED)

            # Initialize engine
            config = SpeculationConfig()
            engine = SpeculativeEngine(
                config=config,
                draft_model_path=draft_model_path
            )

            # Check availability
            if not engine.is_available():
                logger.warning("Speculative decoding not available, using fallback")
                return {
                    'success': False,
                    'error': 'Speculative decoding not available (MLX not installed)',
                    'text': prompt,
                    'fallback': True
                }

            # Generate
            result = await engine.generate(
                prompt=prompt,
                max_tokens=max_tokens,
                mode=decoding_mode
            )

            return {
                'success': True,
                'text': result.final_text,
                'total_tokens': result.total_tokens,
                'accepted_tokens': result.accepted_tokens,
                'rejected_tokens': result.rejected_tokens,
                'acceptance_rate': result.acceptance_rate,
                'speedup_factor': result.speedup_factor,
                'total_time': result.total_time,
                'draft_model_calls': result.draft_model_calls,
                'target_model_calls': result.target_model_calls,
                'mode': mode
            }

        except ImportError:
            logger.warning("SpeculativeEngine not available")
            return {
                'success': False,
                'error': 'SpeculativeEngine not available',
                'text': prompt,
                'fallback': True
            }
        except Exception as e:
            logger.error(f"Speculative decoding failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'text': prompt,
                'fallback': True
            }

    async def get_speculative_decoding_stats(self) -> dict[str, Any]:
        """
        Get speculative decoding performance statistics.

        Returns:
            Statistics about speculative decoding performance
        """
        try:
            from hledac.speculative_decoding.speculative_engine import SpeculativeEngine

            engine = SpeculativeEngine()
            if hasattr(engine, 'metrics'):
                metrics = engine.metrics
                return {
                    'available': engine.is_available(),
                    'total_tokens_generated': metrics.total_tokens_generated,
                    'accepted_tokens': metrics.accepted_tokens,
                    'rejected_tokens': metrics.rejected_tokens,
                    'average_acceptance_rate': metrics.average_acceptance_rate,
                    'average_speedup': metrics.average_speedup,
                    'total_generation_time': metrics.total_generation_time
                }
            return {'available': engine.is_available(), 'metrics': None}
        except ImportError:
            return {'available': False, 'error': 'SpeculativeEngine not available'}
        except Exception as e:
            return {'available': False, 'error': str(e)}

    async def generate_with_adaptive_speculation(
        self,
        prompt: str,
        max_tokens: int = 100,
        initial_k: int = 5,
        target_acceptance_rate: float = 0.7,
        draft_model_path: str | None = None
    ) -> dict[str, Any]:
        """
        Generate text with adaptive K speculative decoding.

        Features:
        - Adaptive K adjustment based on acceptance rate
        - Real-time performance optimization
        - Detailed generation metrics

        Args:
            prompt: Input prompt
            max_tokens: Maximum tokens to generate
            initial_k: Initial draft tokens to generate
            target_acceptance_rate: Target acceptance rate for adaptation
            draft_model_path: Path to draft model

        Returns:
            Generation result with adaptive metrics
        """
        try:
            from hledac.speculative_decoding.speculative_engine import (
                DecodingMode,
                SpeculationConfig,
                SpeculativeEngine,
            )

            # Configure with adaptive K
            config = SpeculationConfig()
            config.adaptive_k = True
            config.min_k = 1
            config.max_k = 10
            config.target_acceptance_rate = target_acceptance_rate

            engine = SpeculativeEngine(
                config=config,
                draft_model_path=draft_model_path
            )

            if not engine.is_available():
                return {
                    'success': False,
                    'error': 'Speculative decoding not available',
                    'text': prompt,
                    'fallback': True
                }

            # Track K adaptations

            # Generate with adaptive tracking
            result = await engine.generate(
                prompt=prompt,
                max_tokens=max_tokens,
                mode=DecodingMode.BALANCED
            )

            return {
                'success': True,
                'text': result.final_text,
                'total_tokens': result.total_tokens,
                'accepted_tokens': result.accepted_tokens,
                'rejected_tokens': result.rejected_tokens,
                'acceptance_rate': result.acceptance_rate,
                'speedup_factor': result.speedup_factor,
                'total_time': result.total_time,
                'draft_model_calls': result.draft_model_calls,
                'target_model_calls': result.target_model_calls,
                'initial_k': initial_k,
                'target_acceptance': target_acceptance_rate,
                'adaptive_enabled': True
            }

        except ImportError:
            return {'success': False, 'error': 'SpeculativeEngine not available', 'fallback': True}
        except Exception as e:
            logger.error(f"Adaptive speculative decoding failed: {e}")
            return {'success': False, 'error': str(e), 'fallback': True}

    def get_action_history(
        self,
        action_type: str | None = None,
        limit: int = 50
    ) -> list[dict[str, Any]]:
        """
        Get action execution history (from Hermes3).

        Args:
            action_type: Filter by action type (None = all)
            limit: Maximum number of entries

        Returns:
            List of action history entries
        """
        history = self._action_history

        if action_type:
            history = [
                h for h in history
                if h.get('action') == action_type
            ]

        return history[-limit:]

    def clear_action_history(self) -> int:
        """
        Clear action history.

        Returns:
            Number of entries cleared
        """
        count = len(self._action_history)
        self._action_history.clear()
        return count

    async def execute_plan(self, plan: list[dict[str, Any]]) -> dict[str, Any]:
        """
        Execute a plan of actions (from Hermes3).

        Args:
            plan: List of action steps

        Returns:
            Plan execution results
        """
        results = []

        for step in plan:
            action_type = step.get('action', 'search')
            payload = step.get('payload', {})

            result = await self.execute_action(action_type, payload)
            results.append(result)

            # Update load factor based on progress
            self._load_factor = min(1.0, len(results) / 10)

        return {
            'success': all(r.get('success', False) for r in results),
            'steps_executed': len(results),
            'successful_steps': sum(1 for r in results if r.get('success')),
            'failed_steps': sum(1 for r in results if not r.get('success')),
            'results': results
        }
