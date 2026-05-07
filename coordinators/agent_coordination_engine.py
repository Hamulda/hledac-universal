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

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


class AgentType(Enum):
    """Types of specialized research agents."""
    ACADEMIC = "academic"           # Academic database search
    DARK_WEB = "dark_web"          # Dark web/deep web search
    HIDDEN_DB = "hidden_database"  # Hidden database access
    DATA_RECON = "data_reconstruction"  # Deleted data reconstruction
    PRIVACY = "privacy_enhancer"   # Privacy-focused research
    ARCHIVE = "archive"            # Archive/library access
    GENERAL = "general"            # General web search


class TaskPriority(Enum):
    """Task priority levels."""
    CRITICAL = 1
    HIGH = 2
    NORMAL = 3
    LOW = 4
    BACKGROUND = 5


@dataclass
class AgentCapability:
    """Capability definition for an agent."""
    agent_type: AgentType
    name: str
    description: str
    max_concurrent: int = 3
    supported_operations: List[str] = field(default_factory=list)
    priority_boost: float = 1.0  # Multiplier for agent selection


@dataclass
class AgentPerformance:
    """Performance metrics for an agent."""
    agent_type: AgentType
    total_tasks: int = 0
    successful_tasks: int = 0
    failed_tasks: int = 0
    avg_duration: float = 0.0
    last_used: Optional[float] = None
    reliability_score: float = 1.0  # 0.0 - 1.0
    
    @property
    def success_rate(self) -> float:
        if self.total_tasks == 0:
            return 1.0
        return self.successful_tasks / self.total_tasks


@dataclass
class TaskRequest:
    """Request for agent execution."""
    id: str
    operation: str
    query: str
    priority: TaskPriority = TaskPriority.NORMAL
    agent_preferences: List[AgentType] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)
    timeout: float = 60.0
    max_retries: int = 2


@dataclass
class TaskResult:
    """Result from agent execution."""
    task_id: str
    agent_type: AgentType
    success: bool
    data: Any = None
    error: Optional[str] = None
    duration: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CoordinationStrategy:
    """Strategy for task coordination."""
    parallel_execution: bool = True
    max_parallel_agents: int = 3
    aggregate_results: bool = True
    deduplicate: bool = True
    fail_fast: bool = False  # If True, fail on first error
    min_success_rate: float = 0.5  # Minimum success rate for agent selection


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
    
    def __init__(self, strategy: Optional[CoordinationStrategy] = None):
        self.strategy = strategy or CoordinationStrategy()
        
        # Agent registry
        self._capabilities: Dict[AgentType, AgentCapability] = {}
        self._performance: Dict[AgentType, AgentPerformance] = {}
        
        # Agent executors (type -> callable)
        self._executors: Dict[AgentType, Callable] = {}
        
        # Active tasks tracking
        self._active_tasks: Dict[str, asyncio.Task] = {}
        self._task_semaphores: Dict[AgentType, asyncio.Semaphore] = {}
        
        # Operation history
        self._operation_history: List[Dict[str, Any]] = []
        self._max_history = 1000
        
        logger.info("AgentCoordinationEngine initialized")
    
    def register_agent(
        self,
        capability: AgentCapability,
        executor: Callable[[TaskRequest], Any]
    ) -> None:
        """
        Register an agent with its capability and executor function.
        
        Args:
            capability: Agent capability definition
            executor: Async function that executes tasks
        """
        self._capabilities[capability.agent_type] = capability
        self._performance[capability.agent_type] = AgentPerformance(
            agent_type=capability.agent_type
        )
        self._executors[capability.agent_type] = executor
        self._task_semaphores[capability.agent_type] = asyncio.Semaphore(
            capability.max_concurrent
        )
        
        logger.info(f"Registered agent: {capability.name} ({capability.agent_type.value})")
    
    def unregister_agent(self, agent_type: AgentType) -> None:
        """Unregister an agent."""
        self._capabilities.pop(agent_type, None)
        self._performance.pop(agent_type, None)
        self._executors.pop(agent_type, None)
        self._task_semaphores.pop(agent_type, None)
        logger.info(f"Unregistered agent: {agent_type.value}")
    
    async def execute_task(
        self,
        request: TaskRequest,
        strategy: Optional[CoordinationStrategy] = None
    ) -> TaskResult:
        """
        Execute a single task with the best available agent.
        
        Args:
            request: Task request
            strategy: Optional override strategy
            
        Returns:
            Task execution result
        """
        strategy = strategy or self.strategy
        
        # Select best agent
        selected_agent = self._select_agent(request)
        if not selected_agent:
            return TaskResult(
                task_id=request.id,
                agent_type=AgentType.GENERAL,
                success=False,
                error="No suitable agent found"
            )
        
        # Execute with retry logic
        for attempt in range(request.max_retries + 1):
            try:
                result = await self._execute_with_agent(request, selected_agent)
                self._update_performance(selected_agent, result)
                self._record_operation(request, result)
                return result
            except Exception as e:
                logger.warning(f"Task {request.id} failed (attempt {attempt + 1}): {e}")
                if attempt == request.max_retries:
                    error_result = TaskResult(
                        task_id=request.id,
                        agent_type=selected_agent,
                        success=False,
                        error=str(e),
                        duration=0.0
                    )
                    self._update_performance(selected_agent, error_result)
                    return error_result
                await asyncio.sleep(0.5 * (attempt + 1))  # Exponential backoff
        
        # Should never reach here
        return TaskResult(
            task_id=request.id,
            agent_type=selected_agent,
            success=False,
            error="Max retries exceeded"
        )
    
    async def execute_parallel(
        self,
        requests: List[TaskRequest],
        strategy: Optional[CoordinationStrategy] = None
    ) -> List[TaskResult]:
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
            # Sequential execution
            results = []
            for request in requests:
                result = await self.execute_task(request, strategy)
                results.append(result)
                if strategy.fail_fast and not result.success:
                    break
            return results
        
        # Parallel execution with semaphore control
        sem = asyncio.Semaphore(strategy.max_parallel_agents)
        
        async def execute_with_limit(request: TaskRequest) -> TaskResult:
            async with sem:
                return await self.execute_task(request, strategy)
        
        tasks = [execute_with_limit(req) for req in requests]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Convert exceptions to error results
        processed_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                processed_results.append(TaskResult(
                    task_id=requests[i].id,
                    agent_type=AgentType.GENERAL,
                    success=False,
                    error=str(result)
                ))
            else:
                processed_results.append(result)
        
        return processed_results
    
    def _select_agent(self, request: TaskRequest) -> Optional[AgentType]:
        """Select the best agent for a task based on capabilities and performance."""
        candidates = []
        
        # Check preferred agents first
        for agent_type in request.agent_preferences:
            if agent_type in self._capabilities:
                candidates.append(agent_type)
        
        # If no preferences or none available, find by operation support
        if not candidates:
            for agent_type, capability in self._capabilities.items():
                if request.operation in capability.supported_operations:
                    candidates.append(agent_type)
        
        # If still no candidates, use any available agent
        if not candidates and self._capabilities:
            candidates = list(self._capabilities.keys())
        
        if not candidates:
            return None
        
        # Score candidates by performance and priority
        best_agent = None
        best_score = -1.0
        
        for agent_type in candidates:
            perf = self._performance[agent_type]
            cap = self._capabilities[agent_type]
            
            # Skip unreliable agents
            if perf.reliability_score < self.strategy.min_success_rate:
                continue
            
            # Calculate score
            score = (
                perf.success_rate * 0.4 +
                perf.reliability_score * 0.3 +
                (1.0 / (perf.avg_duration + 1)) * 0.2 +  # Faster is better
                cap.priority_boost * 0.1
            )
            
            if score > best_score:
                best_score = score
                best_agent = agent_type
        
        return best_agent or (candidates[0] if candidates else None)
    
    async def _execute_with_agent(
        self,
        request: TaskRequest,
        agent_type: AgentType
    ) -> TaskResult:
        """Execute task with specific agent."""
        executor = self._executors.get(agent_type)
        if not executor:
            raise RuntimeError(f"No executor for agent {agent_type}")
        
        sem = self._task_semaphores[agent_type]
        
        start_time = time.time()
        async with sem:
            try:
                # Execute with timeout (C4: use asyncio.timeout for structured concurrency)
                async with asyncio.timeout(request.timeout):
                    data = await executor(request)

                duration = time.time() - start_time
                return TaskResult(
                    task_id=request.id,
                    agent_type=agent_type,
                    success=True,
                    data=data,
                    duration=duration
                )
            except asyncio.TimeoutError:  # C4: TimeoutError is alias in 3.11+, catches both
                duration = time.time() - start_time
                return TaskResult(
                    task_id=request.id,
                    agent_type=agent_type,
                    success=False,
                    error=f"Timeout after {request.timeout}s",
                    duration=duration
                )
    
    def _update_performance(self, agent_type: AgentType, result: TaskResult) -> None:
        """Update performance metrics for an agent."""
        perf = self._performance[agent_type]
        perf.total_tasks += 1
        perf.last_used = time.time()
        
        if result.success:
            perf.successful_tasks += 1
        else:
            perf.failed_tasks += 1
        
        # Update average duration
        perf.avg_duration = (
            (perf.avg_duration * (perf.total_tasks - 1) + result.duration)
            / perf.total_tasks
        )
        
        # Update reliability score (exponential moving average)
        success = 1.0 if result.success else 0.0
        perf.reliability_score = 0.9 * perf.reliability_score + 0.1 * success
    
    def _record_operation(self, request: TaskRequest, result: TaskResult) -> None:
        """Record operation in history."""
        record = {
            "timestamp": time.time(),
            "task_id": request.id,
            "operation": request.operation,
            "agent_type": result.agent_type.value,
            "success": result.success,
            "duration": result.duration,
        }
        
        self._operation_history.append(record)
        
        # Trim history
        if len(self._operation_history) > self._max_history:
            self._operation_history = self._operation_history[-self._max_history:]
    
    def get_agent_stats(self) -> Dict[str, Any]:
        """Get statistics for all registered agents."""
        return {
            agent_type.value: {
                "total_tasks": perf.total_tasks,
                "success_rate": perf.success_rate,
                "avg_duration": perf.avg_duration,
                "reliability": perf.reliability_score,
                "capabilities": self._capabilities[agent_type].supported_operations,
            }
            for agent_type, perf in self._performance.items()
        }
    
    def get_operation_history(
        self,
        agent_type: Optional[AgentType] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Get operation history with optional filtering."""
        history = self._operation_history
        
        if agent_type:
            history = [
                h for h in history
                if h["agent_type"] == agent_type.value
            ]
        
        return history[-limit:]


# Convenience function for quick coordination
async def coordinated_search(
    query: str,
    agents: List[AgentType],
    engine: Optional[AgentCoordinationEngine] = None
) -> List[TaskResult]:
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
    
    requests = [
        TaskRequest(
            id=f"search_{agent.value}_{int(time.time() * 1000)}",
            operation="search",
            query=query,
            agent_preferences=[agent]
        )
        for agent in agents
    ]
    
    return await engine.execute_parallel(requests)
