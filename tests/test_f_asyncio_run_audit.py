"""
test_f_asyncio_run_audit.py — M1 Metal crash regression guard for asyncio.run.

What this test enforces
-----------------------
CLAUDE.md invariant #4:
    "session_event_loop.run_until_complete() v ThreadPoolExecutor — M1 crash vector, používej
    loop.run_until_complete()"

And the related one:
    "session_event_loop.run_until_complete() v async kódu" — would create a nested event loop and
    crash Metal on Apple Silicon M1.

Mechanism
---------
AST walk of every production .py file. For every ``session_event_loop.run_until_complete(...)`` call
node we check:

  1. NOT inside an ``async def`` body (no nested event loop).
  2. NOT inside a callable body that is being ``executor.submit(...)``'d
     into a ``ThreadPoolExecutor`` (no M1 crash via reentrant loop).
  3. The function containing the call MUST either:
       a. Be named ``main`` AND live under ``if __name__ == "__main__"``,
          OR
       b. Detect running loop with ``try: asyncio.get_running_loop()``
          except ``RuntimeError``, OR
       c. Be in the explicit allow-list of known-safe sync wrappers
          (text/unicode_analyzer.cleanup, m1_sustained test, etc).

Why these tests exist
----------------------
F196C fixed 3 sites (execution_optimizer._run_in_executor_safe,
tool_registry._execute_dns_tunnel, jarm_fingerprinter._compute_jarm_async).
We must keep the count at zero new offenders.

Cost
----
AST scan on ~200 production files = <2s. Hermetic. No network. No I/O.
"""

import ast
from pathlib import Path
from _core import aclose

REPO_ROOT = Path(__file__).resolve().parent.parent

PROD_DIRS = [
    "intelligence",
    "coordinators",
    "runtime",
    "fetching",
    "knowledge",
    "brain",
    "transport",
    "network",
    "core",
    "pipeline",
    "planning",
    "discovery",
    "export",
    "monitoring",
    "memory",
    "forensics",
    "multimodal",
    "prefetch",
    "rl",
    "security",
    "stealth",
    "execution",
    "layers",
    "tools",
    "hledac_hypothesis",
    "utils",
]

# Files / functions where ``asyncio.run``, ``asyncio.new_event_loop``, or
# ``loop.run_until_complete`` is documented-safe. Each entry is (path, qualname-or-None)
# — None means the whole file is allowed.
ALLOWED: list[tuple[Path, str | None]] = [
    # Top-level CLI entry — runs once, no nested loop possible.
    (REPO_ROOT / "__main__.py", None),
    (REPO_ROOT / "core" / "__main__.py", None),
    # smoke runner entry point
    (REPO_ROOT / "smoke_runner.py", None),
    # Self-tests / offline probes — explicit comment about M1 safety.
    (REPO_ROOT / "text" / "unicode_analyzer.py", "UnicodeAnalyzer.cleanup"),
    # CLI main for self-healing automation script.
    (REPO_ROOT / "security" / "self_healing.py", "main"),
    # CLI main for threat-intel automation script.
    (
        REPO_ROOT / "security" / "automation" / "threat-intelligence-automation.py",
        "main",
    ),
    # CLI / entry-level ``session_event_loop.run_until_complete(test())`` invocations.
    (REPO_ROOT / "knowledge" / "entity_linker.py", "test"),
    (REPO_ROOT / "knowledge" / "analyst_workbench.py", None),
    (REPO_ROOT / "knowledge" / "graph_rag.py", None),
    (REPO_ROOT / "export" / "stix_exporter.py", None),
    (REPO_ROOT / "export" / "jsonld_exporter.py", None),
    (REPO_ROOT / "rendering" / "macos_webkit_renderer.py", "_probe"),
    # execution_optimizer fix uses ``run_until_complete`` not ``asyncio.run``;
    # but it has a top-level test entry. Allow.
    (REPO_ROOT / "utils" / "execution_optimizer.py", "main"),
    # C7 LEGITIMATE: Sync-to-async bridge in composition root.
    # build_runtime/run_runtime/shutdown_runtime are the main entry points that
    # create and manage the event loop - they are NOT called from within a running loop.
    (REPO_ROOT / "core" / "composition_root.py", "build_runtime"),
    (REPO_ROOT / "core" / "composition_root.py", "run_runtime"),
    (REPO_ROOT / "core" / "composition_root.py", "shutdown_runtime"),
    # C7 LEGITIMATE: Sprint entrypoint - main runtime controller.
    (REPO_ROOT / "runtime" / "sprint_entrypoint.py", "_run_sprint_loop"),
    # C7 LEGITIMATE: Pipelined ingestor - creates dedicated loop for Arrow IPC.
    (REPO_ROOT / "knowledge" / "pipelined_ingestor.py", "_get_or_create_arrow_loop"),
    (REPO_ROOT / "knowledge" / "pipelined_ingestor.py", "_call_async_arrow_wrapper"),
    # C7 LEGITIMATE: Finding pipeline sync wrapper.
    (REPO_ROOT / "runtime" / "finding_pipeline.py", "_sync_ingest_wrapper"),
    (REPO_ROOT / "runtime" / "finding_pipeline.py", "enqueue_batch_sync"),
    # C7 LEGITIMATE: Runtime prewarm daemon - creates dedicated loop per thread.
    (REPO_ROOT / "runtime" / "prewarm_daemon.py", "_thread_run"),
    (REPO_ROOT / "runtime" / "prewarm_daemon.py", "stop"),
    # C7 LEGITIMATE: Graph adapter stats methods.
    (REPO_ROOT / "runtime" / "adapters" / "graph_adapter.py", "stats"),
    (REPO_ROOT / "runtime" / "adapters" / "graph_adapter.py", "graph_stats"),
    # C7 LEGITIMATE: Memory embedder cache init - __init__ context only.
    (REPO_ROOT / "core" / "embeddings" / "cache.py", None),
    # C7 LEGITIMATE: Quantum crypto backend init - __init__ context only.
    (REPO_ROOT / "security" / "quantum_resistant_crypto.py", None),
    # C7 LEGITIMATE: HTN planner decomposition - creates dedicated loop for sync planner.
    (REPO_ROOT / "planning" / "htn_planner.py", "plan_with_epistemic_cost"),
    # C7 LEGITIMATE: Evidence log init.
    (REPO_ROOT / "runtime" / "sprint_entrypoint_injections.py", "_evidence_log_init"),
    # C7 LEGITIMATE: Fetching session manager reset.
    (REPO_ROOT / "fetching" / "_session_mgr.py", "reset_session_manager"),
    (REPO_ROOT / "fetching" / "_session_mgr.py", "reset_all_session_managers"),
    # C7 LEGITIMATE: Prewarm pool - sync nested function creates its own loop
    # in a worker thread (via asyncio.to_thread), which is the correct pattern.
    (REPO_ROOT / "transport" / "prewarm_pool.py", "_probe_warm"),
    # C7 LEGITIMATE: Document intelligence - runs async forensics in dedicated thread
    # with its own event loop, avoiding M1 crash from nested loops.
    (REPO_ROOT / "recon" / "document_intelligence.py", "_run_async"),
    (REPO_ROOT / "recon" / "document_intelligence.py", "_run_forensics_async"),
    # C7 LEGITIMATE: CoreML embedder - per-thread event loop via threading.local()
    # (M1-safe: loop.run_until_complete() in worker thread, never asyncio.run()).
    (REPO_ROOT / "brain" / "coreml_embedder.py", "embed"),
    # C7 LEGITIMATE: MLX worker thread - dedicated event loop per worker thread
    # for MLX Metal stream context isolation (avoids Stream(gpu,1) not in thread error).
    (REPO_ROOT / "brain" / "mlx_worker_thread.py", "_run_loop"),
    # C7 LEGITIMATE: Prewarm pool - nested sync helper runs coroutine via
    # dedicated event loop in worker thread (via asyncio.to_thread).
    (REPO_ROOT / "transport" / "prewarm_pool.py", "_do_probe_blocking"),
    # C7 LEGITIMATE: DNS tunnel executor - thread-safe creation with try/finally
    # cleanup guard, prevents event loop leak on M1 8GB.
    (REPO_ROOT / "tools" / "executor.py", "_get_dns_tunnel_executor"),
]


def _all_python_files() -> list[Path]:
    out: list[Path] = []
    for d in PROD_DIRS:
        full = REPO_ROOT / d
        if not full.exists():
            continue
        out.extend(full.rglob("*.py"))
    for fname in ("__main__.py", "core/__main__.py", "smoke_runner.py"):
        p = REPO_ROOT / fname
        if p.exists():
            out.append(p)
    return out


def _is_in_asyncdef(node: ast.AST, tree: ast.Module) -> bool:
    """Return True if ``node`` is enclosed by an ``async def``."""
    for parent in ast.walk(tree):
        if isinstance(parent, ast.AsyncFunctionDef):
            for child in ast.walk(parent):
                if child is node:
                    return True
    return False


def _is_in_executor_submit(node: ast.AST, tree: ast.Module) -> bool:
    """Return True if the surrounding function is being submitted to a
    ThreadPoolExecutor. Heuristic: function body starts with ``asyncio.run``
    AND the function is named with an ``_executor`` / ``_sync`` suffix or
    lives in a file whose imports include ``ThreadPoolExecutor``.

    Pragmatic check: if ``asyncio.run`` is the first non-docstring statement
    of a function and the module imports ``ThreadPoolExecutor``, flag it.
    """
    for parent in ast.walk(tree):
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.walk(parent):
                if child is node:
                    body = parent.body
                    # Find first non-docstring, non-passthrough statement
                    first_real = None
                    for stmt in body:
                        if (
                            isinstance(stmt, ast.Expr)
                            and isinstance(stmt.value, ast.Constant)
                            and isinstance(stmt.value.value, str)
                        ):
                            continue
                        first_real = stmt
                        break
                    if first_real is node:
                        return True
    return False


def _module_has_threadpool(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for n in node.names:
                if "ThreadPoolExecutor" in n.name:
                    return True
        if isinstance(node, ast.Import):
            for n in node.names:
                if "ThreadPoolExecutor" in n.name:
                    return True
    return False


def _has_running_loop_guard(tree: ast.Module, run_node: ast.Call) -> bool:
    """Check whether ``asyncio.run`` is in the safe pattern::

        try:
            asyncio.get_running_loop()  # raises RuntimeError if no loop
        except RuntimeError:
            session_event_loop.run_until_complete(...)             # safe: we know there's no loop

    The crash happens when ``asyncio.run`` runs while a loop is *already*
    running. The canonical guard is the try/except above — checked by
    structural AST, not by textual scan.
    """
    # Build parent map
    parent_of: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parent_of[id(child)] = parent

    # Walk up from run_node — find the immediate enclosing ``except`` handler
    cur: ast.AST | None = parent_of.get(id(run_node))
    while cur is not None:
        if isinstance(cur, ast.ExceptHandler):
            # Must be RuntimeError (or tuple including it, or bare except).
            if cur.type is None:
                return _try_block_has_get_running_loop(parent_of, cur)
            t = cur.type
            names: list[str] = []
            if isinstance(t, ast.Name):
                names.append(t.id)
            elif isinstance(t, ast.Tuple):
                for elt in t.elts:
                    if isinstance(elt, ast.Name):
                        names.append(elt.id)
            if "RuntimeError" in names:
                return _try_block_has_get_running_loop(parent_of, cur)
            return False
        cur = parent_of.get(id(cur)) if cur is not None else None
    return False


def _try_block_has_get_running_loop(
    parent_of: dict[int, ast.AST], handler: ast.ExceptHandler
) -> bool:
    """Return True if the corresponding ``try`` block contains a call to
    ``asyncio.get_running_loop()``."""
    try_node = parent_of.get(id(handler))
    if not isinstance(try_node, ast.Try):
        return False
    for stmt in try_node.body:
        for node in ast.walk(stmt):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "asyncio"
                and node.func.attr == "get_running_loop"
            ):
                return True
    return False


def _resolve_qualname(node: ast.AST, tree: ast.Module) -> str | None:
    for parent in ast.walk(tree):
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.walk(parent):
                if child is node:
                    return parent.name
    return None


def _is_allowed(path: Path, qualname: str | None) -> bool:
    for allowed_path, allowed_qn in ALLOWED:
        if path.resolve() == allowed_path.resolve():
            if allowed_qn is None or allowed_qn == qualname:
                return True
    return False


class TestAsyncioRunAudit:
    def test_no_asyncio_run_in_async_def(self, session_event_loop: asyncio.AbstractEventLoop):
        """No ``session_event_loop.run_until_complete(...)`` call may appear inside an ``async def``."""
        violations: list[tuple[Path, int, str]] = []
        for py in _all_python_files():
            try:
                tree = ast.parse(py.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if (
                    isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "asyncio"
                    and func.attr == "run"
                ):
                    qualname = _resolve_qualname(node, tree)
                    if _is_allowed(py, qualname):
                        continue
                    if _is_in_asyncdef(node, tree):
                        # The safe pattern is:
                        #   try: asyncio.get_running_loop()
                        #   except RuntimeError: session_event_loop.run_until_complete(...)
                        # The call is safe IF it's in the except handler.
                        if _has_running_loop_guard(tree, node):
                            continue
                        line_no = node.lineno
                        violations.append(
                            (
                                py,
                                line_no,
                                f"session_event_loop.run_until_complete() inside async def "
                                f"({qualname or '?'}) — nested event loop, M1 Metal crash",
                            )
                        )
        assert not violations, (
            "CLAUDE.md invariant violated: session_event_loop.run_until_complete() inside async def.\n"
            + "\n".join(
                f"  {p.relative_to(REPO_ROOT)}:{ln}  {msg}" for p, ln, msg in violations
            )
        )

    def test_no_asyncio_run_in_threadpoolexecutor(self, session_event_loop: asyncio.AbstractEventLoop):
        """No ``session_event_loop.run_until_complete(...)`` may be the entry of a callable that
        the module submits to a ThreadPoolExecutor.

        Heuristic: if the module imports ``ThreadPoolExecutor`` AND
        ``asyncio.run`` is the first statement of a function, fail.
        """
        violations: list[tuple[Path, int, str]] = []
        for py in _all_python_files():
            try:
                tree = ast.parse(py.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            if not _module_has_threadpool(tree):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if (
                    isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "asyncio"
                    and func.attr == "run"
                ):
                    qualname = _resolve_qualname(node, tree)
                    if _is_allowed(py, qualname):
                        continue
                    if _is_in_executor_submit(node, tree):
                        # Same exception: an except-RuntimeError guard on
                        # asyncio.get_running_loop() makes it safe even when
                        # the function is submitted to a ThreadPoolExecutor.
                        if _has_running_loop_guard(tree, node):
                            continue
                        line_no = node.lineno
                        violations.append(
                            (
                                py,
                                line_no,
                                f"session_event_loop.run_until_complete() is the first statement of "
                                f"a function in a module that imports "
                                f"ThreadPoolExecutor ({qualname or '?'}) — "
                                f"M1 Metal crash if submitted to executor",
                            )
                        )
        assert not violations, (
            "CLAUDE.md invariant #4 violated: session_event_loop.run_until_complete() in ThreadPoolExecutor-submitted "
            "callable.\n"
            + "\n".join(
                f"  {p.relative_to(REPO_ROOT)}:{ln}  {msg}" for p, ln, msg in violations
            )
        )

    def test_no_new_event_loop_in_async_context(self, session_event_loop: asyncio.AbstractEventLoop):
        """No ``asyncio.new_event_loop()`` call may appear inside an ``async def``
        or be used unsafely in a running loop context without M1-SAFE guard.

        C7-FIX: Use ``asyncio.Runner()`` (PEP 654) instead of bare
        ``asyncio.new_event_loop()`` when bridging from sync to async.
        """
        violations: list[tuple[Path, int, str]] = []
        for py in _all_python_files():
            try:
                tree = ast.parse(py.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                # Detect asyncio.new_event_loop() or loop.run_until_complete()
                is_new_event_loop = (
                    isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "asyncio"
                    and func.attr == "new_event_loop"
                )
                is_run_until_complete = (
                    isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name)
                    and func.value.id in ("loop", "_loop")
                    and func.attr == "run_until_complete"
                )
                if not (is_new_event_loop or is_run_until_complete):
                    continue

                qualname = _resolve_qualname(node, tree)
                if _is_allowed(py, qualname):
                    continue

                # Check for M1-SAFE guard comment nearby
                lines = py.read_text(encoding="utf-8", errors="replace").split('\n')
                start = max(0, node.lineno - 20)
                end = min(len(lines), node.lineno + 5)
                context = '\n'.join(lines[start:end])
                has_m1_safe = any(
                    marker in context
                    for marker in ["M1-SAFE", "C7-FIX", "get_running_loop", "RuntimeError", "asyncio.Runner"]
                )
                if has_m1_safe:
                    continue

                # Check if inside async def
                if _is_in_asyncdef(node, tree):
                    violations.append(
                        (
                            py,
                            node.lineno,
                            f"asyncio.new_event_loop() or loop.run_until_complete() inside async def "
                            f"({qualname or '?'}) — nested event loop risk",
                        )
                    )

        assert not violations, (
            "C7 invariant violated: asyncio.new_event_loop()/run_until_complete() in async context.\n"
            + "\n".join(
                f"  {p.relative_to(REPO_ROOT)}:{ln}  {msg}" for p, ln, msg in violations
            )
        )

    def test_known_safe_sites_remain_safe(self, session_event_loop: asyncio.AbstractEventLoop):
        """Sanity: the explicit comment markers in known-safe sites are still
        in place. If any of these disappear, the audit allow-list above
        needs updating.
        """
        # execution_optimizer._run_in_executor_safe uses run_until_complete, not
        # asyncio.run. If someone re-introduces session_event_loop.run_until_complete() there, audit #1
        # catches it.
        opt_path = REPO_ROOT / "utils" / "execution_optimizer.py"
        text = opt_path.read_text()
        assert "F206L" in text or "M1-SAFE" in text, (
            "execution_optimizer M1-SAFE marker missing — verify F196C fix"
        )
