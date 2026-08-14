"""
Agent Coordination Engine - Multi-Agent Orchestration System

Coordinates multiple research agents with intelligent task distribution,






capability-based routing, and result aggregation.

Based on advanced_crypto_integration.py concept.

Features:
- Capability-based agent selection
- Intelligent task distribution
- Parallel execution across agents
- Result aggregation and deduplication
- Performance tracking per agent
- Automatic fallback chains
"""
import asyncio
import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import field
from enum import Enum
from typing import Any

import msgspec
from hledac.universal.compat.msgspec_gc_compat import Struct
from hledac.universal.compat.msgspec_gc_compat import Struct
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from hledac.universal.utils.asyncx import parallel

logger = logging.getLogger(__name__)

class AgentType(Enum):
    """Types of specialized research agents."""
    ACADEMIC = 'academic'
    DARK_WEB = 'dark_web'
    HIDDEN_DB = 'hidden_database'
    DATA_RECON = 'data_reconstruction'
    PRIVACY = 'privacy_enhancer'
    ARCHIVE = 'archive'
    GENERAL = 'general'

class TaskPriority(Enum):
    """Task priority levels."""
    CRITICAL = 1
    HIGH = 2
    NORMAL = 3
    LOW = 4
    BACKGROUND = 5

class AgentCapability(Struct):
    """Capability definition for an agent."""
    agent_type: AgentType
    name: str
    description: str
    max_concurrent: int = 3
    supported_operations: list[str] = field(default_factory=list)
    priority_boost: float = 1.0

class AgentPerformance(Struct):
    """Performance metrics for an agent."""
    agent_type: AgentType
    total_tasks: int = 0
    successful_tasks: int = 0
    failed_tasks: int = 0
    avg_duration: float = 0.0
    last_used: float | None = None
    reliability_score: float = 1.0

    @property
    def success_rate(self) -> float:
        if self.total_tasks == 0:
            return 1.0
        return self.successful_tasks / self.total_tasks

class TaskRequest(Struct, frozen=True):
    """Request for agent execution."""
    id: str
    operation: str
    query: str
    priority: TaskPriority = TaskPriority.NORMAL
    agent_preferences: list[AgentType] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    timeout: float = 60.0
    max_retries: int = 2

class TaskResult(Struct, frozen=True):
    """Result from agent execution."""
    task_id: str
    agent_type: AgentType
    success: bool
    data: Any = None
    error: str | None = None
    duration: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

class CoordinationStrategy(Struct, frozen=True):
    """Strategy for task coordination."""
    parallel_execution: bool = True
    max_parallel_agents: int = 3
    aggregate_results: bool = True
    deduplicate: bool = True
    fail_fast: bool = False
    min_success_rate: float = 0.5

class AgentCoordinationEngine:
    """
    Multi-agent coordination engine with intelligent task distribution.

    Example:
        >>> engine = AgentCoordinationEngine()
        >>>
        >>> # Register agents
        >>> engine.register_agent(AgentCapability(
        ...     agent_type=AgentType.ACADEMIC,
        ...     name="AcademicSearch",
        ...     supported_operations=["search", "citation_analysis"]
        ... ))
        >>>
        >>> # Execute task
        >>> result = await engine.execute_task(TaskRequest(
        ...     id="task_001",
        ...     operation="search",
        ...     query="machine learning",
        ...     agent_preferences=[AgentType.ACADEMIC]
        ... ))
    """
    __slots__ = ('_active_tasks', '_capabilities', '_executors', '_max_history', '_operation_history', '_performance', '_task_semaphores', 'strategy')

    def __init__(self, strategy: CoordinationStrategy | None=None) -> None:
        self.strategy = strategy or CoordinationStrategy()
        self._capabilities: dict[AgentType, AgentCapability] = {}
        self._performance: dict[AgentType, AgentPerformance] = {}
        self._executors: dict[AgentType, Callable] = {}
        self._active_tasks: dict[str, asyncio.Task] = {}
        self._task_semaphores: dict[AgentType, asyncio.Semaphore] = {}
        self._operation_history: deque = deque(maxlen=self._max_history)
        self._max_history = 1000
        logger.info('AgentCoordinationEngine initialized')

    def register_agent(self, capability: AgentCapability, executor: Callable[[TaskRequest], Any]) -> None:
        """
        Register an agent with its capability and executor function.

        Args:
            capability: Agent capability definition
            executor: Async function that executes tasks
        """
        self._capabilities[capability.agent_type] = capability
        self._performance[capability.agent_type] = AgentPerformance(agent_type=capability.agent_type)
        self._executors[capability.agent_type] = executor
        self._task_semaphores[capability.agent_type] = asyncio.Semaphore(capability.max_concurrent)
        logger.info(f'Registered agent: {capability.name} ({capability.agent_type.value})')

    def unregister_agent(self, agent_type: AgentType) -> None:
        """Unregister an agent."""
        self._capabilities.pop(agent_type, None)
        self._performance.pop(agent_type, None)
        self._executors.pop(agent_type, None)
        self._task_semaphores.pop(agent_type, None)
        logger.info(f'Unregistered agent: {agent_type.value}')

    async def execute_task(self, request: TaskRequest) -> TaskResult:
        """
        Execute a single task with the best available agent.

        Args:
            request: Task request
            strategy: Optional override strategy

        Returns:
            Task execution result
        G6: Uses tenacity @retry with exponential backoff + jitter.
        """
        selected_agent = self._select_agent(request)
        if not selected_agent:
            return TaskResult(task_id=request.id, agent_type=AgentType.GENERAL, success=False, error='No suitable agent found')

        # G6: Use tenacity for retry with exponential backoff + jitter
        _task_retry = retry(
            wait=wait_exponential_jitter(base=0.5, max=8.0),
            stop=stop_after_attempt(request.max_retries + 1),
            retry=retry_if_exception_type(Exception),
            reraise=True,
        )

        async def _execute_once() -> TaskResult:
            result = await self._execute_with_agent(request, selected_agent)
            self._update_performance(selected_agent, result)
            self._record_operation(request, result)
            return result

        try:
            result = await _task_retry(_execute_once)
            return result
        except Exception as e:
            logger.warning(f'Task {request.id} failed after all retries: {e}')
            error_result = TaskResult(task_id=request.id, agent_type=selected_agent, success=False, error=str(e), duration=0.0)
            self._update_performance(selected_agent, error_result)
            return error_result

    async def execute_parallel(self, requests: list[TaskRequest], strategy: CoordinationStrategy | None=None) -> list[TaskResult]:
        """
        Execute multiple tasks in parallel across agents.

        Args:
            requests: List of task requests
            strategy: Optional override strategy

        Returns:
            List of task results
        """
        strategy = strategy or self.strategy
        if not strategy.parallel_execution:
            results = []
            for request in requests:
                result = await self.execute_task(request)
                results.append(result)
                if strategy.fail_fast and (not result.success):
                    break
            return results
        sem = asyncio.Semaphore(strategy.max_parallel_agents)

        async def execute_with_limit(request: TaskRequest) -> TaskResult:
            async with sem:
                return await self.execute_task(request)
        tasks: list = [execute_with_limit(req) for req in requests]
        gathered = await parallel(tasks, policy="collect", ctx="agent_coordination_engine:219")
        return list(gathered.ok)

    def _select_agent(self, request: TaskRequest) -> AgentType | None:
        """Select the best agent for a task based on capabilities and performance."""
        candidates = []
        for agent_type in request.agent_preferences:
            if agent_type in self._capabilities:
                candidates.append(agent_type)
        if not candidates:
            for agent_type, capability in self._capabilities.items():
                if request.operation in capability.supported_operations:
                    candidates.append(agent_type)
        if not candidates and self._capabilities:
            candidates = list(self._capabilities.keys())
        if not candidates:
            return None
        best_agent = None
        best_score = -1.0
        for agent_type in candidates:
            perf = self._performance[agent_type]
            cap = self._capabilities[agent_type]
            if perf.reliability_score < self.strategy.min_success_rate:
                continue
            score = perf.success_rate * 0.4 + perf.reliability_score * 0.3 + 1.0 / (perf.avg_duration + 1) * 0.2 + cap.priority_boost * 0.1
            if score > best_score:
                best_score = score
                best_agent = agent_type
        return best_agent or (candidates[0] if candidates else None)

    async def _execute_with_agent(self, request: TaskRequest, agent_type: AgentType) -> TaskResult:
        """Execute task with specific agent."""
        executor = self._executors.get(agent_type)
        if not executor:
            raise RuntimeError(f'No executor for agent {agent_type}')
        sem = self._task_semaphores[agent_type]
        start_time = time.time()
        async with sem:
            try:
                async with asyncio.timeout(request.timeout):
                    data = await executor(request)
                duration = time.time() - start_time
                return TaskResult(task_id=request.id, agent_type=agent_type, success=True, data=data, duration=duration)
            except TimeoutError:
                duration = time.time() - start_time
                return TaskResult(task_id=request.id, agent_type=agent_type, success=False, error=f'Timeout after {request.timeout}s', duration=duration)

    def _update_performance(self, agent_type: AgentType, result: TaskResult) -> None:
        """Update performance metrics for an agent."""
        perf = self._performance[agent_type]
        perf.total_tasks += 1
        perf.last_used = time.time()
        if result.success:
            perf.successful_tasks += 1
        else:
            perf.failed_tasks += 1
        perf.avg_duration = (perf.avg_duration * (perf.total_tasks - 1) + result.duration) / perf.total_tasks
        success = 1.0 if result.success else 0.0
        perf.reliability_score = 0.9 * perf.reliability_score + 0.1 * success

    def _record_operation(self, request: TaskRequest, result: TaskResult) -> None:
        """Record operation in history."""
        record = {'timestamp': time.time(), 'task_id': request.id, 'operation': request.operation, 'agent_type': result.agent_type.value, 'success': result.success, 'duration': result.duration}
        self._operation_history.append(record)

    def get_agent_stats(self) -> dict[str, Any]:
        """Get statistics for all registered agents."""
        return {agent_type.value: {'total_tasks': perf.total_tasks, 'success_rate': perf.success_rate, 'avg_duration': perf.avg_duration, 'reliability': perf.reliability_score, 'capabilities': self._capabilities[agent_type].supported_operations} for agent_type, perf in self._performance.items()}

    def get_operation_history(self, agent_type: AgentType | None=None, limit: int=100) -> list[dict[str, Any]]:
        """Get operation history with optional filtering."""
        history = self._operation_history
        if agent_type:
            history = [h for h in history if h['agent_type'] == agent_type.value]
        return list(history)[-limit:]

async def coordinated_search(query: str, agents: list[AgentType], engine: AgentCoordinationEngine | None=None) -> list[TaskResult]:
    """
    Perform coordinated search across multiple agents.

    Args:
        query: Search query
        agents: List of agent types to use
        engine: Optional coordination engine (creates new if None)

    Returns:
        Results from all agents
    """
    if engine is None:
        engine = AgentCoordinationEngine()
    requests = [TaskRequest(id=f'search_{agent.value}_{int(time.time() * 1000)}', operation='search', query=query, agent_preferences=[agent]) for agent in agents]
    return await engine.execute_parallel(requests)
