"""
TASYNC001: Detect time.sleep() inside async def without executor context.

Blocking time.sleep() inside async test functions blocks the pytest-asyncio
event loop. Use await asyncio.sleep() instead.

This rule is CONSERVATIVE - it only flags time.sleep() that is NOT inside
an executor-wrapped context:
- time.sleep() inside nested sync functions passed to executors: OK
- time.sleep() inside lambdas passed to executor patterns: OK
- time.sleep() directly in async function body: FLAGGED

Correct patterns:
- await asyncio.sleep(duration)  ← RECOMMENDED
- time.sleep() inside nested function/lambda passed to executor  ← OK

Incorrect patterns:
- time.sleep(duration) directly inside async def test_*  ← FLAGGED

Rule ID: TASYNC001
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import NamedTuple


class Violation(NamedTuple):
    file: Path
    line: int
    col: int
    name: str
    message: str


# Executor patterns - functions that wrap blocking calls in threads
EXECUTOR_PATTERNS = frozenset({
    "to_thread_with_timeout",
    "to_thread",
    "run_in_executor",
    "submit",
    "rayon_submit",
    "run_lmdb",
    "mlx_inference_lock_aio",
})


def _is_time_sleep_call(node: ast.expr) -> bool:
    """Check if node is time.sleep() call."""
    if not isinstance(node, ast.Call):
        return False
    if not isinstance(node.func, ast.Attribute):
        return False
    if node.func.attr != "sleep":
        return False
    # Check if it's time.sleep()
    if isinstance(node.func.value, ast.Name) and node.func.value.id == "time":
        return True
    return False


def _is_executor_call(node: ast.Call) -> bool:
    """Check if a Call node is an executor pattern call."""
    if isinstance(node.func, ast.Name):
        return node.func.id in EXECUTOR_PATTERNS
    if isinstance(node.func, ast.Attribute):
        return node.func.attr in EXECUTOR_PATTERNS
    return False


def _is_lambda_passed_to_executor(time_sleep_node: ast.Call, async_func: ast.AsyncFunctionDef) -> bool:
    """Check if time.sleep is inside a lambda that's passed to an executor pattern."""
    # Walk up to find if we're inside a Lambda
    for ancestor in ast.walk(async_func):
        if isinstance(ancestor, ast.Lambda):
            # Check if this Lambda contains our time.sleep call
            for child in ast.walk(ancestor):
                if child is time_sleep_node:
                    # Found it - now check if the Lambda is passed to an executor
                    # Look for the parent Call that uses this Lambda
                    for parent in ast.walk(async_func):
                        if isinstance(parent, ast.Call) and _is_executor_call(parent):
                            # Check if any argument is this Lambda
                            for arg in parent.args:
                                for arg_child in ast.walk(arg):
                                    if arg_child is ancestor:
                                        return True
                    return True
    return False


def _is_in_nested_sync_function(time_sleep_node: ast.Call, async_func: ast.AsyncFunctionDef) -> bool:
    """Check if time.sleep is inside a nested sync function that's passed to an executor."""
    for ancestor in ast.walk(async_func):
        if isinstance(ancestor, ast.FunctionDef) and not isinstance(ancestor, ast.AsyncFunctionDef):
            # This is a nested sync function
            for child in ast.walk(ancestor):
                if child is time_sleep_node:
                    # Found time.sleep in this nested function
                    # Check if the function is passed to an executor
                    for parent in ast.walk(async_func):
                        if isinstance(parent, ast.Call) and _is_executor_call(parent):
                            for arg in parent.args:
                                for arg_child in ast.walk(arg):
                                    if isinstance(arg_child, ast.Name) and arg_child.id == ancestor.name:
                                        return True
                    return True
    return False


def _check_async_function(node: ast.AsyncFunctionDef, file_path: Path) -> list[Violation]:
    """Check an async function for time.sleep() violations."""
    violations = []
    
    is_test = node.name.startswith('test_')
    if not is_test:
        return violations
    
    for child in ast.walk(node):
        if isinstance(child, ast.Call) and _is_time_sleep_call(child):
            # Check if time.sleep is in an executor-wrapped context
            is_executor_wrapped = (
                _is_lambda_passed_to_executor(child, node) or
                _is_in_nested_sync_function(child, node)
            )
            
            if not is_executor_wrapped:
                violations.append(Violation(
                    file=file_path,
                    line=child.lineno or 0,
                    col=child.col_offset or 0,
                    name="TASYNC001",
                    message=f"TASYNC001: time.sleep() in async def {node.name} "
                            f"is not in executor context. Use 'await asyncio.sleep(duration)' instead.",
                ))
    
    return violations


def check_file(file_path: Path) -> list[Violation]:
    """Check a single file for TASYNC001 violations."""
    violations = []
    
    # Only check test files
    if not str(file_path).startswith('tests/') or not file_path.name.startswith('test_'):
        return violations
    
    try:
        with open(file_path) as f:
            content = f.read()
        tree = ast.parse(content, filename=str(file_path))
    except (SyntaxError, OSError):
        return violations
    
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef):
            violations.extend(_check_async_function(node, file_path))
    
    return violations


def check_directory(base_path: Path = Path(".")) -> list[Violation]:
    """Check all test files in directory for TASYNC001 violations."""
    violations = []
    
    tests_dir = base_path / "tests"
    if not tests_dir.exists():
        return violations
    
    for py_file in tests_dir.glob("test_*.py"):
        violations.extend(check_file(py_file))
    
    return violations


def main() -> int:
    """CLI entry point."""
    violations = check_directory(Path("."))
    
    if violations:
        print("TASYNC001 violations found:")
        for v in violations:
            print(f"  {v.file}:{v.line}:{v.col} {v.name}: {v.message}")
        return 1
    
    print("No TASYNC001 violations found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
