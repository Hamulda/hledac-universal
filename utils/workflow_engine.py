"""
WorkflowEngine - DAG-based workflow execution z WorkflowOrchestrator

Funkce:
- DAG-based task definition
- Topological ordering
- Parallel/sequential execution
- Conditional and loop tasks
- Retry mechanism s exponential backoff
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

# Sprint 8U: Lazy networkx import to avoid loading 285 modules at cold-start
_nx = None

def _get_nx():
    """Lazy networkx loader - only loads when actually needed."""
    global _nx
    if _nx is None:
        import networkx
        _nx = networkx
    return _nx

logger = logging.getLogger(__name__)


class TaskType(Enum):
    """Typy úkolů"""
    NORMAL = "normal"
    CONDITIONAL = "conditional"  # Podmíněný
    LOOP = "loop"               # Smyčka
    PARALLEL = "parallel"       # Paralelní


class TaskStatus(Enum):
    """Stavy úkolů"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class Task:
    """Úkol ve workflow"""
    id: str
    name: str
    task_type: TaskType = TaskType.NORMAL
    func: Optional[Callable] = None
    params: Dict[str, Any] = field(default_factory=dict)
    condition: Optional[Callable] = None  # Pro CONDITIONAL
    loop_condition: Optional[Callable] = None  # Pro LOOP
    max_retries: int = 3
    retry_delay: float = 1.0
    dependencies: List[str] = field(default_factory=list)
    
    # Runtime state
    status: TaskStatus = TaskStatus.PENDING
    result: Any = None
    error: Optional[str] = None
    attempts: int = 0
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    
    async def execute(self, context: Dict[str, Any]) -> Any:
        """Vykonat úkol"""
        if self.func is None:
            return None
        
        self.start_time = time.time()
        self.status = TaskStatus.RUNNING
        
        try:
            # Check condition for conditional tasks
            if self.task_type == TaskType.CONDITIONAL and self.condition:
                if not self.condition(context):
                    self.status = TaskStatus.SKIPPED
                    return None
            
            # Execute
            if inspect.iscoroutinefunction(self.func):
                result = await self.func(**self.params, context=context)
            else:
                result = self.func(**self.params, context=context)
            
            self.result = result
            self.status = TaskStatus.COMPLETED
            return result
            
        except Exception as e:
            self.error = str(e)
            self.status = TaskStatus.FAILED
            raise
        finally:
            self.end_time = time.time()
    
    def duration(self) -> Optional[float]:
        """Doba trvání"""
        if self.start_time and self.end_time:
            return self.end_time - self.start_time
        return None


@dataclass
class Workflow:
    """Workflow definice"""
    id: str
    name: str
    tasks: Dict[str, Task] = field(default_factory=dict)
    context: Dict[str, Any] = field(default_factory=dict)
    
    def add_task(self, task: Task) -> None:
        """Přidat úkol"""
        self.tasks[task.id] = task
    
    def add_dependency(self, task_id: str, depends_on: str) -> None:
        """Přidat závislost"""
        if task_id in self.tasks:
            self.tasks[task_id].dependencies.append(depends_on)


class WorkflowEngine:
    """
    Engine pro DAG-based workflow execution.
    
    Features:
    - Validace DAG (žádné cykly)
    - Topologické řazení
    - Paralelní vykonávání
    - Retry s exponential backoff
    - Podmíněné a smyčkové úkoly
    """
    
    def __init__(self, max_concurrency: int = 5):
        self.max_concurrency = max_concurrency
        self._execution_history = []
        
    def validate(self, workflow: Workflow) -> bool:
        """
        Validovat workflow.
        
        Args:
            workflow: Workflow k validaci
            
        Returns:
            True pokud validní
        """
        try:
            # Vytvořit DAG
            dag = self._build_dag(workflow)
            
            # Kontrolovat cykly
            if not _get_nx().is_directed_acyclic_graph(dag):
                logger.error("Workflow contains cycles")
                return False
            
            # Kontrolovat existence závislostí
            for task in workflow.tasks.values():
                for dep in task.dependencies:
                    if dep not in workflow.tasks:
                        logger.error(f"Task {task.id} depends on non-existent task {dep}")
                        return False
            
            return True
            
        except Exception as e:
            logger.error(f"Validation failed: {e}")
            return False
    
    def _build_dag(self, workflow: Workflow):
        """Vytvořit DAG z workflow"""
        dag = _get_nx().DiGraph()
        
        # Přidat uzly
        for task_id in workflow.tasks:
            dag.add_node(task_id)
        
        # Přidat hrany (závislosti)
        for task_id, task in workflow.tasks.items():
            for dep in task.dependencies:
                dag.add_edge(dep, task_id)
        
        return dag
    
    async def execute(
        self,
        workflow: Workflow,
        on_task_complete: Optional[Callable] = None
    ) -> Dict[str, Any]:
        """
        Vykonat workflow.
        
        Args:
            workflow: Workflow k vykonání
            on_task_complete: Callback po dokončení úkolu
            
        Returns:
            Výsledky všech úkolů
        """
        if not self.validate(workflow):
            raise ValueError("Invalid workflow")
        
        logger.info(f"Executing workflow: {workflow.name}")
        
        # Topologické řazení
        dag = self._build_dag(workflow)
        execution_order = list(_get_nx().topological_sort(dag))
        
        logger.info(f"Execution order: {execution_order}")
        
        # Seskupit podle úrovní (pro paralelní spuštění)
        levels = self._group_by_levels(dag, execution_order)
        
        # Vykonat
        for level_idx, level_tasks in enumerate(levels):
            logger.info(f"Executing level {level_idx + 1}/{len(levels)}: {len(level_tasks)} tasks")
            
            # Paralelní vykonání v rámci úrovně
            semaphore = asyncio.Semaphore(self.max_concurrency)
            
            async def run_task(task_id: str) -> None:
                async with semaphore:
                    try:
                        await self._execute_task_with_retry(workflow, task_id)
                    except Exception as e:
                        # Store exception in task.error for caller visibility
                        workflow.tasks[task_id].error = str(e)
                        workflow.tasks[task_id].status = TaskStatus.FAILED
                        logger.error(f"Task {task_id} permanently failed: {e}")

            results = await asyncio.gather(*[run_task(tid) for tid in level_tasks], return_exceptions=True)
            # Log any unexpected exceptions (shouldn't happen since run_task catches them)
            for tid, result in zip(level_tasks, results):
                if isinstance(result, Exception):
                    logger.error(f"Task {tid} unexpected exception: {result}")
            
            # Callback
            if on_task_complete:
                for tid in level_tasks:
                    task = workflow.tasks[tid]
                    on_task_complete(task)
        
        # Sběr výsledků
        results = {
            tid: task.result
            for tid, task in workflow.tasks.items()
        }
        
        logger.info(f"Workflow completed: {workflow.name}")
        
        return results
    
    def _group_by_levels(
        self,
        dag,
        execution_order: List[str]
    ) -> List[List[str]]:
        """
        Seskupit úkoly podle úrovní.
        
        Úkoly ve stejné úrovni mohou běžet paralelně.
        """
        levels = []
        completed = set()
        
        remaining = set(execution_order)
        
        while remaining:
            # Najít úkoly s všechny závislostmi splněnými
            ready = []
            for task_id in remaining:
                deps = set(dag.predecessors(task_id))
                if deps <= completed:
                    ready.append(task_id)
            
            if not ready:
                raise ValueError("Cannot resolve dependencies")
            
            levels.append(ready)
            completed.update(ready)
            remaining -= set(ready)
        
        return levels
    
    async def _execute_task_with_retry(
        self,
        workflow: Workflow,
        task_id: str
    ) -> None:
        """Vykonat úkol s retry"""
        task = workflow.tasks[task_id]
        
        while task.attempts < task.max_retries:
            task.attempts += 1
            
            try:
                # Substituovat parametry z kontextu
                params = self._resolve_params(task.params, workflow.context)
                
                # Vykonat
                result = await task.execute(workflow.context)
                
                # Uložit do kontextu
                workflow.context[f"{task_id}_result"] = result
                
                logger.info(f"Task {task_id} completed")
                return
                
            except Exception as e:
                logger.warning(f"Task {task_id} failed (attempt {task.attempts}): {e}")
                
                if task.attempts >= task.max_retries:
                    logger.error(f"Task {task_id} failed after {task.max_retries} attempts")
                    raise
                
                # Exponential backoff
                delay = task.retry_delay * (2 ** (task.attempts - 1))
                logger.info(f"Retrying in {delay}s...")
                await asyncio.sleep(delay)
    
    def _resolve_params(
        self,
        params: Dict[str, Any],
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Substituovat parametry z kontextu.
        
        Podporuje: "${task_id_result.field}"
        """
        resolved = {}
        
        for key, value in params.items():
            if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
                # Resolve reference
                ref = value[2:-1]  # Remove ${ and }
                parts = ref.split(".")
                
                # Get from context
                val = context.get(parts[0])
                
                # Navigate nested
                for part in parts[1:]:
                    if isinstance(val, dict):
                        val = val.get(part)
                    else:
                        val = None
                        break
                
                resolved[key] = val
            else:
                resolved[key] = value
        
        return resolved
    
    def get_execution_history(self) -> List[Dict[str, Any]]:
        """Získat historii vykonávání"""
        return self._execution_history
