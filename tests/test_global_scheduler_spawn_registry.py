"""
Spawn Registry Reality Test (Standalone)
========================================

Hermetic test: verifies whether a module-level _TASK_REGISTRY dict
is visible in a child process when using 'spawn' context.

No project imports needed — this tests the fundamental behavior of
multiprocessing spawn + module-level registry.

Run:
    python3 tests/test_global_scheduler_spawn_registry.py
"""

import multiprocessing as mp
import os
import queue
import sys

# ---------------------------------------------------------------------------
# Standalone module that simulates global_scheduler._TASK_REGISTRY
# ---------------------------------------------------------------------------

_TASK_REGISTRY = {}
_MAX_TASK_REGISTRY = 1000


def register_task(name, func):
    global _TASK_REGISTRY
    if name in _TASK_REGISTRY:
        del _TASK_REGISTRY[name]
    _TASK_REGISTRY[name] = func
    while len(_TASK_REGISTRY) > _MAX_TASK_REGISTRY:
        oldest = next(iter(_TASK_REGISTRY))
        del _TASK_REGISTRY[oldest]


def get_task(name):
    return _TASK_REGISTRY.get(name)


# ---------------------------------------------------------------------------
# Child worker functions (must be picklable / importable at top level)
# ---------------------------------------------------------------------------

def _child_check_registry(child_conn, task_name_check):
    """
    Child process entry point via spawn.
    Reloads this module's state to see if _TASK_REGISTRY was inherited.
    """
    pid = os.getpid()
    # With spawn, child starts from blank Python state.
    # It only inherits what is passed via arguments/pipes, NOT parent's memory.
    # So _TASK_REGISTRY will be empty ({}).
    import __main__
    main_dict = dir(__main__)
    '_TASK_REGISTRY' in main_dict and len(main_dict) > 0

    # Re-import THIS module in child to check state
    import importlib

    import test_global_scheduler_spawn_registry as self_module
    importlib.reload(self_module)

    registry_keys = list(self_module._TASK_REGISTRY.keys())
    get_result = self_module.get_task(task_name_check)

    child_conn.send({
        "registry_keys": registry_keys,
        "get_task_result": get_result,
        "child_pid": pid,
        "success": True,
    })


def _child_simulate_worker(signal_queue, result_queue, task_name_check):
    """
    Simulate GlobalPriorityScheduler._worker_loop behavior:
    receive job from queue, look up task from _TASK_REGISTRY.
    """
    pid = os.getpid()
    try:
        item = signal_queue.get(timeout=5.0)
        if item is None:
            return

        job_id = item[7] if len(item) > 7 else "unknown"
        task_name = item[3] if len(item) > 3 else task_name_check

        # Reload self to get current registry state in this process
        import importlib

        import test_global_scheduler_spawn_registry as self_module
        importlib.reload(self_module)

        func = self_module.get_task(task_name)
        result_queue.put({
            "job_id": job_id,
            "task_found": func is not None,
            "registry_keys": list(self_module._TASK_REGISTRY.keys()),
            "child_pid": pid,
            "success": True,
        })
    except Exception as e:
        result_queue.put({
            "error": str(e),
            "child_pid": pid,
            "success": False,
        })


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_spawn_registry_empty():
    """Core test: spawn child, check if _TASK_REGISTRY is empty."""
    print("\n=== Test: SPAWN child _TASK_REGISTRY state ===")

    # Register something in parent
    def parent_func():
        return 42

    register_task("parent_task", parent_func)
    print(f"  Parent PID {os.getpid()}: registered 'parent_task'")
    print(f"  Parent _TASK_REGISTRY: {list(_TASK_REGISTRY.keys())}")

    ctx = mp.get_context('spawn')
    parent_conn, child_conn = mp.Pipe()

    child = ctx.Process(target=_child_check_registry, args=(child_conn, "parent_task"))
    child.start()

    if parent_conn.poll(timeout=15):
        result = parent_conn.recv()
    else:
        result = {"error": "timeout waiting for child", "success": False}
        child.terminate()

    child.join(timeout=5)

    print(f"  Child PID {result.get('child_pid', '?')}: registry_keys={result.get('registry_keys', [])}")
    print(f"  Child get_task('parent_task'): {result.get('get_task_result', 'N/A')}")
    print(f"  Success: {result.get('success', False)}")

    if not result.get("success"):
        return "ERROR", result

    if result["registry_keys"]:
        return "PASS (registry inherited)", result
    else:
        return "FAIL (registry EMPTY in spawn child)", result


def test_spawn_worker_behavioral():
    """Behavioral: put job in queue, see if spawn worker finds task."""
    print("\n=== Test: SPAWN worker behavioral (job dispatch) ===")

    def test_task(x):
        return x * 2

    register_task("behavioral_task", test_task)
    print(f"  Parent PID {os.getpid()}: registered 'behavioral_task'")
    print(f"  Parent _TASK_REGISTRY: {list(_TASK_REGISTRY.keys())}")

    ctx = mp.get_context('spawn')
    signal_q = ctx.Queue()
    result_q = ctx.Queue()

    # Put job item: (priority, timestamp, seq, task_name, args, kwargs, affinity_key, job_id, max_retries)
    job_item = (1, 0.0, 0, "behavioral_task", (21,), {}, "affinity", "job_xyz", 0)
    signal_q.put(job_item)

    worker = ctx.Process(target=_child_simulate_worker, args=(signal_q, result_q, "behavioral_task"))
    worker.start()

    try:
        result = result_q.get(timeout=10)
        worker.join(timeout=2)
    except queue.Empty:
        result = {"error": "result queue timeout", "success": False}
        worker.terminate()
        worker.join(timeout=2)

    print(f"  Result: {result}")

    if not result.get("success"):
        return f"ERROR: {result.get('error', 'unknown')}", result
    if result.get("task_found"):
        return "PASS (task found)", result
    return "FAIL (task NOT found in worker)", result


def test_fork_registry_inherited():
    """Fork context: child should inherit parent's _TASK_REGISTRY."""
    print("\n=== Test: FORK child _TASK_REGISTRY state ===")

    def parent_func():
        return 42

    register_task("fork_task", parent_func)
    print(f"  Parent PID {os.getpid()}: registered 'fork_task'")
    print(f"  Parent _TASK_REGISTRY: {list(_TASK_REGISTRY.keys())}")

    ctx = mp.get_context('fork')
    parent_conn, child_conn = mp.Pipe()

    child = ctx.Process(target=_child_check_registry, args=(child_conn, "fork_task"))
    child.start()

    if parent_conn.poll(timeout=15):
        result = parent_conn.recv()
    else:
        result = {"error": "timeout waiting for child", "success": False}
        child.terminate()

    child.join(timeout=5)

    print(f"  Child PID {result.get('child_pid', '?')}: registry_keys={result.get('registry_keys', [])}")
    print(f"  Child get_task('fork_task'): {result.get('get_task_result', 'N/A')}")
    print(f"  Success: {result.get('success', False)}")

    if not result.get("success"):
        return "ERROR", result

    if result["registry_keys"]:
        return "PASS (registry inherited via fork)", result
    else:
        return "FAIL (registry empty even with fork)", result


if __name__ == "__main__":
    print("=" * 60)
    print("SPAWN REGISTRY REALITY TEST")
    print("=" * 60)
    print(f"Python: {sys.version}")
    print(f"Default start method: {mp.get_start_method()}")
    print(f"Available: {mp.get_all_start_methods()}")

    results = []

    status, detail = test_spawn_registry_empty()
    results.append(("spawn", status, detail))

    status, detail = test_spawn_worker_behavioral()
    results.append(("spawn_worker", status, detail))

    try:
        status, detail = test_fork_registry_inherited()
        results.append(("fork", status, detail))
    except Exception as e:
        results.append(("fork", f"ERROR: {e}", None))

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for method, status, _ in results:
        print(f"  {method}: {status}")

    spawn_fail = any(r[0] in ("spawn", "spawn_worker") and "FAIL" in r[1] for r in results)

    print("\n" + "=" * 60)
    print("IMPLICATIONS FOR GlobalPriorityScheduler")
    print("=" * 60)

    if spawn_fail:
        print("  WARNING: spawn makes _TASK_REGISTRY EMPTY in workers!")
        print("  macOS default = spawn → async tasks FAIL in ProcessPool workers")
        print("")
        print("  MINIMAL POLICY OPTIONS:")
        print("  1. Use fork context: mp.set_start_method('fork') on Mac")
        print("     Risk: fork can cause deadlocks with locks in parent")
        print("  2. Route async callables to ThreadPoolExecutor instead")
        print("  3. Pass registry via Manager().dict() — but slower")
        print("  4. Spawn workers with pre-populated registry via initializer")
        print("")
        print("  RECOMMENDATION: ThreadPool for async, ProcessPool for CPU-bound sync only")
        print("")
        print("  Creating report: reports/F_GLOBAL_SCHEDULER_SPAWN_REGISTRY_REALITY.md")
    else:
        print("  Registry works in spawn context (registry re-created in child)")
        print("  No action needed.")
