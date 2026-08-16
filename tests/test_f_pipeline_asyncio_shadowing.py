"""
Regression test for pre-existing UnboundLocalError: asyncio bug.

Bug: `import asyncio` inside `async_run_live_public_pipeline` (was at line 4890)
shadowed the module-level `import asyncio`. Python scoping makes `asyncio` a
local name throughout the entire function body, so every earlier `asyncio.X`
reference (e.g. `asyncio.Semaphore(...)` at the old line 3770) raised
`UnboundLocalError: local variable 'asyncio' referenced before assignment`.

This file locks down two invariants:

1. STATIC: AST scan of the function body must contain ZERO `import asyncio`
   statements inside `async_run_live_public_pipeline`. Catches the class of bug
   deterministically (stdlib-only, M1-friendly, no network, no MLX).

2. RUNTIME: `async_run_live_public_pipeline(...)` must not raise
   `UnboundLocalError: asyncio` even when called with DI seams that make
   execution reach the `asyncio.Semaphore(...)` construction site.

Author: Sprint F26xA — asyncio-scoping-shield regression.
"""

import ast
import inspect
import sys
import tempfile
from unittest.mock import MagicMock

import pytest
from _core import aclose

# Make the package importable when pytest is launched from a worktree.
_HERE = "/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal"
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


class TestSprintFAsyncioShadowing:
    """Lock down asyncio-shadowing regression for live_public_pipeline."""

    # ------------------------------------------------------------------ #
    # 1. Static AST check — no local `import asyncio` inside the function
    # ------------------------------------------------------------------ #
    def test_no_local_import_asyncio_in_async_run_live_public_pipeline(self):
        """AST scan: zero local `import asyncio` inside the function body.

        Why: any `import asyncio` anywhere in the function body makes the name
        `asyncio` local to the entire function (CPython 3.x scoping rule). All
        earlier `asyncio.X` references then raise UnboundLocalError. This test
        is the canary that catches the entire class of bug at static-analysis
        time, no runtime needed.
        """
        from hledac.universal.pipeline import live_public_pipeline

        source = inspect.getsource(live_public_pipeline.async_run_live_public_pipeline)
        tree = ast.parse(source)

        # Locate the top-level FunctionDef (sync or async) for async_run_live_public_pipeline
        func_def: ast.FunctionDef | ast.AsyncFunctionDef | None = None
        for node in tree.body:
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == "async_run_live_public_pipeline"
            ):
                func_def = node
                break

        assert func_def is not None, (
            "async_run_live_public_pipeline FunctionDef/AsyncFunctionDef not found via AST"
    )

        shadowing: list[tuple[int, str]] = []
        for node in ast.walk(func_def):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "asyncio":
                        # ast lineno is 1-based within the source slice — report it
                        shadowing.append((node.lineno, alias.name))
            elif isinstance(node, ast.ImportFrom):
                if node.module == "asyncio":
                    shadowing.append((node.lineno, node.module or ""))

        assert shadowing == [], (
            f"Found {len(shadowing)} local import(s) of 'asyncio' inside "
            f"async_run_live_public_pipeline at AST line(s) "
            f"{[ln for ln, _ in shadowing]}. "
            "Python scoping makes `asyncio` a local name throughout the "
            "function body, causing UnboundLocalError for every earlier "
            "asyncio.X reference (e.g. asyncio.Semaphore). Use the "
            "module-level import (line 14) — do NOT add a local `import asyncio`."
    )

    def test_no_local_import_asyncio_in_other_public_pipeline_functions(self):
        """AST scan: zero local `import asyncio` in any other pipeline fn.

        The fix targets the specific function, but the same Python scoping
        bug can affect any async function. Scan top-level async functions
        in live_public_pipeline as a wider safety net.
        """
        from hledac.universal.pipeline import live_public_pipeline

        source = inspect.getsource(live_public_pipeline)
        tree = ast.parse(source)

        offenders: list[tuple[str, int]] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Skip nested defs/classes — only check function bodies at this level
                if node.col_offset == 0:
                    for child in ast.walk(node):
                        if isinstance(child, ast.Import):
                            for alias in child.names:
                                if alias.name == "asyncio":
                                    offenders.append((node.name, child.lineno))
                        elif isinstance(child, ast.ImportFrom):
                            if child.module == "asyncio":
                                offenders.append((node.name, child.lineno))

        assert offenders == [], (
            f"Found local `import asyncio` inside top-level functions: "
            f"{offenders}. Same scoping bug class — fix by removing the "
            f"redundant import (asyncio is module-scoped at line 14)."
    )

    # ------------------------------------------------------------------ #
    # 2. Runtime smoke — actually call the function and assert no ULE
    # ------------------------------------------------------------------ #
    @pytest.mark.asyncio
    async def test_async_run_live_public_pipeline_reaches_semaphore_construction_without_ule(self):
        """Call the function with DI seams. Must NOT raise UnboundLocalError: asyncio.

        This is the runtime mirror of the static AST check. The DI seam path
        (fetch_fn / match_fn / discovery_fn) lets the function run far enough
        to reach `asyncio.Semaphore(effective_concurrency)` (old line 3770).
        Pre-fix, this site raised UnboundLocalError on `asyncio`.
        """
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore
        from hledac.universal.pipeline.live_public_pipeline import async_run_live_public_pipeline

        # Build a canned discovery result with one hit
        canned_discovery = MagicMock()
        canned_discovery.hits = (
            MagicMock(
                url="https://example.com",
                title="Example",
                snippet="Example",
                rank=0,
                score=0.9,
                reason="test",
            ),
    )
        canned_discovery.cache_hit = False

        async def canned_fetch(url, timeout, max_bytes, use_stealth=False, use_js=False, use_doh=False):
            result = MagicMock()
            result.fetched_text = "<html>test</html>"
            result.elapsed_s = 0.05
            result.status_code = 200
            result.used_stealth = False
            result.used_js = False
            result.used_doh = False
            return result

        async def canned_discovery_fn(query, max_results=10, timeout_s=30.0):
            return canned_discovery

        async def canned_match(text):
            return []  # no pattern matches

        with tempfile.TemporaryDirectory() as tmp:
            db_path = tmp + "/f26xa_shadowing.ddb"
            store = DuckDBShadowStore(db_path=db_path)
            store._init_persistent_dedup_lmdb = lambda: None
            await store.async_initialize()

            # Catch the specific exception class so a regression message is sharp.
            try:
                await async_run_live_public_pipeline(
                    query="example.com",
                    store=store,
                    max_results=5,
                    fetch_timeout_s=2.0,
                    fetch_max_bytes=10_000,
                    fetch_concurrency=1,  # exercises asyncio.Semaphore(1) on line 3770
                    fetch_fn=canned_fetch,
                    match_fn=canned_match,
                    discovery_fn=canned_discovery_fn,
    )
            except UnboundLocalError as exc:
                if "asyncio" in str(exc):
                    pytest.fail(
                        f"REGRESSION: UnboundLocalError: asyncio fired — "
                        f"the local `import asyncio` shadowing bug has returned. "
                        f"Original: {exc}"
    )
                # Any other UnboundLocalError is a different bug — let it propagate
                raise
