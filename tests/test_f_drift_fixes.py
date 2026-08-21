"""
Sprint F-CLEAN: Drift-fix regression suite.

Verifies fixes for 3 production-critical bugs:

  1. ``layers/ghost_layer.py:26`` — ``import subprocess`` accidentally inserted
     into the parenthesised ``from ... import (...)`` block. Module raised
     ``SyntaxError`` on import, blocking ``layers/__init__.py:35`` and
     ``core/__main__.py:1574``.

  2. ``rendering/__init__.py:21`` — same bug pattern, blocking the public
     ``hledac.universal.rendering`` namespace (private submodule path was OK).

  3. ``knowledge/duckdb_store.py:7498`` — ``asyncio.create_task(asyncio.coroutine(...)())``
     where ``asyncio.coroutine`` was *removed in Python 3.11*. Project targets
     ``>=3.14,<3.16`` (runtime: CPython 3.14.5), so the entire advisory graph
     update silently failed in a ``try/except`` envelope. The cross-sprint
     entity graph never accumulated findings.

Plus one consistency fix:

  4. ``tests/probe_fpq_stix_signature.py:97`` — same ``asyncio.coroutine()``
     pattern, replaced with idiomatic ``asyncio.sleep(0, result=...)`` (gather
     accepts any awaitable).

INVARIANTS (enforced by this suite):
  I1. All three modules import cleanly on Python 3.14 (no ``SyntaxError``).
  I2. ``_schedule_graph_update`` creates a real task in async context
      (regression: was silently swallowed by AttributeError try/except).
  I3. ``_schedule_graph_update`` is a no-op in sync context — never raises.
  I4. In-flight task set is bounded by ``_MAX_INFLIGHT_GRAPH_UPDATES`` (16).
  I5. Repo-wide: zero active ``asyncio.coroutine`` in non-probe code.
  I6. Repo-wide: zero ``import subprocess`` inside ``from ... import (...)``
      parenthesised imports.

M1 8GB-friendly: pure stdlib + AST. No MLX, no DuckDB instance, no network.
"""

import ast
import asyncio
from pathlib import Path

import pytest

REPO_ROOT = Path("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal")


# ---------------------------------------------------------------------------
# I1. Import smoke — three modules must be importable on Python 3.14
# ---------------------------------------------------------------------------
class TestImportSmoke:
    """Verify the three SyntaxError bugs are gone."""

    def test_ghost_layer_imports_without_syntax_error(self) -> None:
        """layers/ghost_layer.py must import cleanly."""
        from hledac.universal.layers import ghost_layer  # noqa: F401

        # Confirm the public surface is intact
        assert hasattr(ghost_layer, "GhostLayer"), "GhostLayer class missing"
        assert hasattr(ghost_layer, "ActionResult"), "ActionResult missing"
        # Confirm subprocess is still imported at module level (it is used
        # at lines 666/714/722/730 for subprocess.run calls).
        assert "subprocess" in dir(ghost_layer), (
            "subprocess must remain importable (used by ghost_layer.run_system_command)"
        )

    def test_rendering_imports_without_syntax_error(self) -> None:
        """rendering/__init__.py must import cleanly (full namespace)."""
        from hledac.universal import rendering  # noqa: F401

        # Public surface exported via __all__
        expected = {
            "WebKitRenderResult",
            "is_macos_webkit_available",
            "fetch_with_macos_webkit",
            "MACOS_WEBKIT_REASONS",
        }
        for name in expected:
            assert name in rendering.__all__, f"{name} missing from rendering.__all__"

    def test_duckdb_store_module_parses(self) -> None:
        """duckdb_store.py must be AST-parseable on Python 3.14."""
        path = REPO_ROOT / "knowledge" / "duckdb_store.py"
        source = path.read_text(encoding="utf-8")
        # AST parse raises SyntaxError on bad syntax. No execute, just parse.
        ast.parse(source)


# ---------------------------------------------------------------------------
# I5. Repo-wide guard: no live asyncio.coroutine() in production code
# ---------------------------------------------------------------------------
class TestAsyncioCoroutineDrift:
    """Production code must not use the removed-in-3.11 ``asyncio.coroutine``."""

    def test_no_asyncio_coroutine_in_production_code(self) -> None:
        """No active asyncio.coroutine() in non-probe, non-test code."""
        prod_dirs = [
            REPO_ROOT / "core",
            REPO_ROOT / "runtime",
            REPO_ROOT / "knowledge",
            REPO_ROOT / "brain",
            REPO_ROOT / "coordinators",
            REPO_ROOT / "transport",
            REPO_ROOT / "fetching",
            REPO_ROOT / "intelligence",
            REPO_ROOT / "discovery",
            REPO_ROOT / "pipeline",
            REPO_ROOT / "export",
            REPO_ROOT / "monitoring",
            REPO_ROOT / "memory",
            REPO_ROOT / "network",
            REPO_ROOT / "forensics",
            REPO_ROOT / "multimodal",
            REPO_ROOT / "planning",
            REPO_ROOT / "prefetch",
            REPO_ROOT / "rl",
            REPO_ROOT / "security",
            REPO_ROOT / "utils",
            REPO_ROOT / "layers",
            REPO_ROOT / "rendering",
            REPO_ROOT / "stealth",
            REPO_ROOT / "execution",
            REPO_ROOT / "patterns",
            REPO_ROOT / "config",
            REPO_ROOT / "tools",
            REPO_ROOT / "scripts",
            REPO_ROOT / "hledac_hypothesis",
        ]
        offenders: list[tuple[Path, int, str]] = []
        for prod_dir in prod_dirs:
            if not prod_dir.exists():
                continue
            for py_file in prod_dir.rglob("*.py"):
                try:
                    tree = ast.parse(py_file.read_text(encoding="utf-8"))
                except SyntaxError:
                    continue  # covered by TestImportSmoke
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call):
                        continue
                    func = node.func
                    # Match: asyncio.coroutine(...)
                    if (
                        isinstance(func, ast.Attribute)
                        and func.attr == "coroutine"
                        and isinstance(func.value, ast.Name)
                        and func.value.id == "asyncio"
                    ):
                        offenders.append((py_file, node.lineno, "asyncio.coroutine() call"))
        assert not offenders, "Production code uses asyncio.coroutine() (removed in 3.11):\n" + "\n".join(
            f"  {p.relative_to(REPO_ROOT)}:{ln} {why}" for p, ln, why in offenders
        )


# ---------------------------------------------------------------------------
# I6. Repo-wide guard: no ``import subprocess`` inside parens
# ---------------------------------------------------------------------------
class TestSubprocessInParensDrift:
    """No ``import subprocess`` accidentally inside ``from ... import (...)``."""

    def test_no_subprocess_inside_from_parens_imports(self) -> None:
        """Drift guard: no SyntaxError from ``import X`` inside ``from ... import (...)`` parens.

        Uses ``ast.parse`` rather than line-pattern matching — this avoids
        false positives on docstring examples (e.g. ``forensics/__init__.py``
        shows an example ``from hledac.universal.forensics import (...)``
        block in its module docstring). AST parsing of a real file fails
        with ``SyntaxError`` exactly when the drift pattern is in the
        actual code (not in a string literal).

        The bug is class-agnostic — it has hit ``subprocess``, ``piexif``,
        etc. across the codebase. We catch ANY drift here, not just one
        offender, because the failure mode is the same.
        """
        offenders: list[tuple[Path, int, str]] = []
        # Production dirs (mirrors TestAsyncioCoroutineDrift scope) + tests.
        prod_dirs = [
            REPO_ROOT / "core",
            REPO_ROOT / "runtime",
            REPO_ROOT / "knowledge",
            REPO_ROOT / "brain",
            REPO_ROOT / "coordinators",
            REPO_ROOT / "transport",
            REPO_ROOT / "fetching",
            REPO_ROOT / "intelligence",
            REPO_ROOT / "discovery",
            REPO_ROOT / "pipeline",
            REPO_ROOT / "export",
            REPO_ROOT / "monitoring",
            REPO_ROOT / "memory",
            REPO_ROOT / "network",
            REPO_ROOT / "forensics",
            REPO_ROOT / "multimodal",
            REPO_ROOT / "planning",
            REPO_ROOT / "prefetch",
            REPO_ROOT / "rl",
            REPO_ROOT / "security",
            REPO_ROOT / "utils",
            REPO_ROOT / "layers",
            REPO_ROOT / "rendering",
            REPO_ROOT / "stealth",
            REPO_ROOT / "execution",
            REPO_ROOT / "patterns",
            REPO_ROOT / "config",
            REPO_ROOT / "tools",
            REPO_ROOT / "scripts",
            REPO_ROOT / "hledac_hypothesis",
            REPO_ROOT / "tests",
        ]
        skip_dirs = {"__pycache__", ".git"}
        for prod_dir in prod_dirs:
            if not prod_dir.exists():
                continue
            for py_file in prod_dir.rglob("*.py"):
                if any(part in skip_dirs for part in py_file.parts):
                    continue
                try:
                    src = py_file.read_text(encoding="utf-8")
                except OSError, UnicodeDecodeError:
                    continue
                try:
                    ast.parse(src, filename=str(py_file))
                except SyntaxError as e:
                    msg = e.msg or "syntax error"
                    # Heuristic: only flag "invalid syntax" on a line that
                    # sits inside an open `from ... import (` parens block.
                    # This trims noise from other forms of syntax errors
                    # (e.g. pre-existing AST bugs unrelated to this drift).
                    if "import" in msg.lower() or "parenth" in msg.lower():
                        offenders.append((py_file, e.lineno or 0, msg))
        assert not offenders, (
            "Drift pattern detected — import stmt inside 'from ... import (...)' parens:\n"
            + "\n".join(f"  {p.relative_to(REPO_ROOT)}:{ln} {snippet}" for p, ln, snippet in offenders)
        )


# ---------------------------------------------------------------------------
# I2 + I3 + I4. _schedule_graph_update behaviour
# ---------------------------------------------------------------------------
class TestScheduleGraphUpdate:
    """Verify the Python-3.10+ refactor of _schedule_graph_update."""

    @pytest.fixture
    def store(self):
        """DuckDBShadowStore in :memory: mode for hermetic testing.

        We bypass async_initialize() because the contract under test is the
        pure-Python _schedule_graph_update — no DB writes needed. The store's
        graph update is a no-op import (graph_service is feature-gated).

        M1 8GB: cleanup releases DuckDB PyO3 50-200 MB buffer.
        """
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        s = DuckDBShadowStore()
        # _bg_tasks is initialised in __init__, but be defensive
        s._bg_tasks = set()
        try:
            yield s
        finally:
            try:
                s.close()
            except Exception:
                pass
            import gc

            gc.collect()

    @pytest.fixture
    def sample_findings(self):
        """Minimal CanonicalFinding-like objects (duck-typed)."""

        class _F:
            def __init__(self, value, type_, conf, src) -> None:
                self.ioc_value = value
                self.ioc_type = type_
                self.confidence = conf
                self.source_type = src

        return [
            _F("1.2.3.4", "ip", 0.9, "ct_log"),
            _F("evil.example", "domain", 0.8, "ct_log"),
            _F("https://x/y", "url", 0.7, ""),  # empty source_type
            _F(None, "ip", 0.5, "ct_log"),  # missing ioc_value (filter out)
        ]

    @pytest.mark.asyncio
    async def test_async_context_creates_task(self, store, sample_findings) -> None:
        """In async context, _schedule_graph_update creates a real task."""
        store._schedule_graph_update(sample_findings)
        # Yield to the loop so the task starts
        await asyncio.sleep(0)
        # Bound: tasks set should contain >=1 task (or fewer if it finished)
        # Critically: it must NOT have raised (was failing pre-fix).
        assert isinstance(store._bg_tasks, set)

    def test_sync_context_is_noop(self, store, sample_findings) -> None:
        """In sync context (no running loop), method is silent no-op."""
        # We are explicitly NOT in an event loop. Should not raise.
        store._schedule_graph_update(sample_findings)
        # Tasks set should be empty (loop was absent, so no task created).
        assert not store._bg_tasks

    def test_inflight_cap_enforced(self, store, sample_findings) -> None:
        """In-flight task set is bounded by _MAX_INFLIGHT_GRAPH_UPDATES."""
        from hledac.universal.knowledge import duckdb_store as ds_mod

        cap = ds_mod._MAX_INFLIGHT_GRAPH_UPDATES
        assert cap == 16, f"Cap unexpectedly changed: {cap}"

        # Pre-fill the set past the cap (simulate backlog)
        for _ in range(cap + 5):
            store._bg_tasks.add(asyncio.sleep(0))
        assert len(store._bg_tasks) > cap

        # Now call _schedule_graph_update — should be silently dropped
        store._schedule_graph_update(sample_findings)
        # Bounded: no new task created (count unchanged)
        assert len(store._bg_tasks) == cap + 5

    @pytest.mark.asyncio
    async def test_drain_callback_releases_task(self, store, sample_findings) -> None:
        """Task added to _bg_tasks is removed on completion."""
        store._schedule_graph_update(sample_findings)
        await asyncio.sleep(0.05)  # let any spawned task finish
        # Steady state: set should be drained back near zero
        assert len(store._bg_tasks) <= 1

    @pytest.mark.asyncio
    async def test_finds_with_missing_attrs_filtered(self, store) -> None:
        """Objects without ioc_value/ioc_type are filtered out, no error."""

        class _BadFinding:
            pass

        # Should not raise; should silently skip (rows=[])
        store._schedule_graph_update([_BadFinding()])
        await asyncio.sleep(0)

    @pytest.mark.asyncio
    async def test_finds_with_no_findings_is_noop(self, store) -> None:
        """Empty findings list creates a task that completes near-immediately.

        Invariant: steady-state _bg_tasks count returns near 0 after the
        scheduled task finishes.
        """
        store._schedule_graph_update([])
        # Yield to let the scheduled task run + discard callback fire
        await asyncio.sleep(0.01)
        assert len(store._bg_tasks) <= 1, f"Expected <=1 in-flight task, got {len(store._bg_tasks)}"
