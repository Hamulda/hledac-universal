"""
WorkflowEngine - DAG-based workflow execution z WorkflowOrchestrator

Funkce:



- DAG-based task definition
- Topological ordering (native Python - no networkx dependency)
- Parallel/sequential execution
- Conditional and loop tasks
- Retry mechanism s exponential backoff

Migrated from networkx to native Python DAG (Issue #28).
"""
import asyncio
import inspect
import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
import msgspec
from compat.msgspec_gc_compat import Struct
from enum import Enum
from typing import Any
from .async_helpers import parallel_ok
from _core import aclose
logger = logging.getLogger(__name__)

class TaskType(Enum):
    """Typy úkolů"""
    NORMAL = 'normal'
    CONDITIONAL = 'conditional'
    LOOP = 'loop'
    PARALLEL = 'parallel'

class TaskStatus(Enum):
    """Stavy úkolů"""
    PENDING = 'pending'
    RUNNING = 'running'
    COMPLETED = 'completed'
    FAILED = 'failed'
    SKIPPED = 'skipped'

class Task(Struct):
    """Úkol ve workflow"""
    id: str
    name: str
    task_type: TaskType = TaskType.NORMAL
    func: Callable | None = None
    params: dict[str, Any] = field(default_factory=dict)
    condition: Callable | None = None
    loop_condition: Callable | None = None
    max_retries: int = 3
    retry_delay: float = 1.0
    dependencies: list[str] = field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    result: Any = None
    error: str | None = None
    attempts: int = 0
    start_time: float | None = None
    end_time: float | None = None

    async def execute(self, context: dict[str, Any]) -> Any:
        """Vykonat úkol"""
        if self.func is None:
            return None
        self.start_time = time.time()
        self.status = TaskStatus.RUNNING
        try:
            if self.task_type == TaskType.CONDITIONAL and self.condition:
                if not self.condition(context):
                    self.status = TaskStatus.SKIPPED
                    return None
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

    def duration(self) -> float | None:
        """Doba trvání"""
        if self.start_time and self.end_time:
            return self.end_time - self.start_time
        return None

class Workflow(Struct, frozen=True):
    """Workflow definice"""
    id: str
    name: str
    tasks: dict[str, Task] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)

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
    __slots__ = tuple(('_execution_history', 'max_concurrency'))

    def __init__(self, max_concurrency: int=5):
        self.max_concurrency = max_concurrency
        self._execution_history: deque[dict[str, Any]] = deque(maxlen=512)

    def validate(self, workflow: Workflow) -> bool:
        """
        Validovat workflow.

        Args:
            workflow: Workflow k validaci

        Returns:
            True pokud validní
        """
        try:
            dag = self._build_dag(workflow)
            if not self._is_dag(dag):
                logger.error('Workflow contains cycles')
                return False
            for task in workflow.tasks.values():
                for dep in task.dependencies:
                    if dep not in workflow.tasks:
                        logger.error(f'Task {task.id} depends on non-existent task {dep}')
                        return False
            return True
        except Exception as e:
            logger.error(f'Validation failed: {e}')
            return False

    def _build_dag(self, workflow: Workflow) -> dict[str, list[str]]:
        """Vytvořit DAG z workflow jako adjacency dict {task_id: [dependencies]}"""
        dag: dict[str, list[str]] = {task_id: [] for task_id in workflow.tasks}
        for task_id, task in workflow.tasks.items():
            dag[task_id] = list(task.dependencies)
        return dag

    def _is_dag(self, dag: dict[str, list[str]]) -> bool:
        """Kontrolovat cykly pomocí Kahn's algorithm."""
        in_degree: dict[str, int] = {n: 0 for n in dag}
        for node in dag:
            for dep in dag[node]:
                in_degree[dep] += 1
        queue = [n for n, d in in_degree.items() if d == 0]
        count = 0
        while queue:
            node = queue.pop(0)
            count += 1
            for dep in dag.get(node, []):
                in_degree[dep] -= 1
                if in_degree[dep] == 0:
                    queue.append(dep)
        return count == len(dag)

    def _topological_sort(self, dag: dict[str, list[str]]) -> list[str]:
        """Topologické řazení pomocí Kahn's algorithm."""
        in_degree: dict[str, int] = {n: 0 for n in dag}
        for node in dag:
            for dep in dag[node]:
                in_degree[dep] += 1
        queue = [n for n, d in in_degree.items() if d == 0]
        result = []
        while queue:
            node = queue.pop(0)
            result.append(node)
            for dep in dag.get(node, []):
                in_degree[dep] -= 1
                if in_degree[dep] == 0:
                    queue.append(dep)
        return result

    async def execute(self, workflow: Workflow, on_task_complete: Callable | None=None) -> dict[str, Any]:
        """
        Vykonat workflow.

        Args:
            workflow: Workflow k vykonání
            on_task_complete: Callback po dokončení úkolu

        Returns:
            Výsledky všech úkolů
        """
        if not self.validate(workflow):
            raise ValueError('Invalid workflow')
        logger.info(f'Executing workflow: {workflow.name}')
        dag = self._build_dag(workflow)
        execution_order = self._topological_sort(dag)
        logger.info(f'Execution order: {execution_order}')
        levels = self._group_by_levels(dag, execution_order)
        for level_idx, level_tasks in enumerate(levels):
            logger.info(f'Executing level {level_idx + 1}/{len(levels)}: {len(level_tasks)} tasks')
            semaphore = asyncio.Semaphore(self.max_concurrency)

            async def run_task(task_id: str) -> None:
                async with semaphore:
                    try:
                        await self._execute_task_with_retry(workflow, task_id)
                    except Exception as e:
                        workflow.tasks[task_id].error = str(e)
                        workflow.tasks[task_id].status = TaskStatus.FAILED
                        logger.error(f'Task {task_id} permanently failed: {e}')
            results = await parallel_ok(*[run_task(tid) for tid in level_tasks], label='workflow_engine:242')
            for tid, result in zip(level_tasks, results, strict=False):
                if isinstance(result, Exception):
                    logger.error(f'Task {tid} unexpected exception: {result}')
            if on_task_complete:
                for tid in level_tasks:
                    task = workflow.tasks[tid]
                    on_task_complete(task)
        results = {tid: task.result for tid, task in workflow.tasks.items()}
        logger.info(f'Workflow completed: {workflow.name}')
        return results

    def _group_by_levels(self, dag: dict[str, list[str]], execution_order: list[str]) -> list[list[str]]:
        """
        Seskupit úkoly podle úrovní.

        Úkoly ve stejné úrovni mohou běžet paralelně.
        DAG je dict {task_id: [dependencies]}
        """
        levels = []
        completed: set[str] = set()
        remaining = set(execution_order)
        while remaining:
            ready = []
            for task_id in remaining:
                deps = set(dag.get(task_id, []))
                if deps <= completed:
                    ready.append(task_id)
            if not ready:
                raise ValueError('Cannot resolve dependencies')
            levels.append(ready)
            completed.update(ready)
            remaining -= set(ready)
        return levels

    async def _execute_task_with_retry(self, workflow: Workflow, task_id: str) -> None:
        """Vykonat úkol s retry"""
        task = workflow.tasks[task_id]
        while task.attempts < task.max_retries:
            task.attempts += 1
            try:
                self._resolve_params(task.params, workflow.context)
                result = await task.execute(workflow.context)
                workflow.context[f'{task_id}_result'] = result
                logger.info(f'Task {task_id} completed')
                return
            except Exception as e:
                logger.warning(f'Task {task_id} failed (attempt {task.attempts}): {e}')
                if task.attempts >= task.max_retries:
                    logger.error(f'Task {task_id} failed after {task.max_retries} attempts')
                    raise
                delay = task.retry_delay * 2 ** (task.attempts - 1)
                logger.info(f'Retrying in {delay}s...')
                await asyncio.sleep(delay)

    def _resolve_params(self, params: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        """
        Substituovat parametry z kontextu.

        Podporuje: "${task_id_result.field}"
        """
        resolved = {}
        for key, value in params.items():
            if isinstance(value, str) and value.startswith('${') and value.endswith('}'):
                ref = value[2:-1]
                parts = ref.split('.')
                val = context.get(parts[0])
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

    def get_execution_history(self) -> deque[dict[str, Any]]:
        """Získat historii vykonávání"""
        return self._execution_history