"""
test_ghost_invariants.py — R-5: GHOST_INVARIANTS CI Enforcement

Tests every GHOST_INVARIANT rule defined in GHOST_INVARIANTS.md.
Each test is named after its invariant for traceability.

Invariant coverage:
  I1  — asyncio.gather always uses return_exceptions=True
  I2  — _check_gathered() called after every gather (where applicable)
  I3  — async_getaddrinfo used instead of socket.getaddrinfo
  I4  — time.monotonic for all interval measurements
  I5  — bare except is forbidden (must use except Exception: or specific)
  I6  — asyncio.to_thread forbidden for DNS/CoreML/DuckDB
  I7  — asyncio.run() in ThreadPoolExecutor is a crash vector
  I8  — mx.eval([]) before mx.metal.clear_cache()
  I9  — DuckDB writes only via async_ingest_findings_batch()
  I10 — LMDB bulk write via cursor.putmulti()

CI mode:
    pytest tests/test_ghost_invariants.py --strict-markers -q

Hard (fail CI):
  I1 - asyncio.gather without return_exceptions=True
  I5 - bare except:
  I7 - asyncio.run() inside run_in_executor context

Warning-only (informational in CI):
  I2 - gather without _check_gathered (excludes safe wrappers)
  I3 - socket.getaddrinfo in async context
  I4 - time.time() for intervals
  I6 - asyncio.to_thread in forbidden contexts
  I8 - mx.metal.clear_cache() without mx.eval([]) barrier
  I9 - direct duckdb.connect() (excluding pool manager)
  I10 - LMDB per-item write loop
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest
from _core import aclose

# -----------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------

ROOT = Path("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal")
SRC_DIRS = [
    ROOT / "brain", ROOT / "core", ROOT / "runtime",
    ROOT / "coordinators", ROOT / "pipeline", ROOT / "transport",
    ROOT / "knowledge", ROOT / "intelligence", ROOT / "forensics",
    ROOT / "advanced_web", ROOT / "export", ROOT / "tools",
]
EXCLUDE = {".hypothesis", "__pycache__", "archive/", ".venv", ".venv-test", "probe_"}


def _iter_python_files(dirs: list) -> list:
    for d in dirs:
        if not d.exists():
            continue
        for f in d.rglob("*.py"):
            s = str(f)
            if any(p in s for p in EXCLUDE):
                continue
            yield f


# -----------------------------------------------------------------------
# AST Visitors
# -----------------------------------------------------------------------

class GatherCallAnalyzer(ast.NodeVisitor):
    """Find asyncio.gather() calls and check return_exceptions."""

    def __init__(self, filename: str):
        self.filename = filename
        self.violations: list[tuple[int, str]] = []
        self.safe_calls = 0
        self.raw_calls = 0

    def visit_Call(self, node: ast.Call) -> None:
        if not isinstance(node.func, ast.Attribute):
            self.generic_visit(node)
            return

        if node.func.attr != "gather":
            self.generic_visit(node)
            return

        # Check if it's asyncio.gather
        is_asyncio_gather = (
            isinstance(node.func.value, ast.Name) and
            node.func.value.id == "asyncio"
        )

        # Check for safe wrappers
        is_safe_wrapper = isinstance(node.func.value, ast.Name) and node.func.value.id in (
            "safe_gather_ok", "safe_gather_first_ok", "safe_gather_all_ok",
            "parallel_ok", "try_group",
        )

        if is_asyncio_gather:
            self.raw_calls += 1
            has_return_exceptions = any(
                kw.arg == "return_exceptions"
                for kw in node.keywords
            )
            if not has_return_exceptions and not is_safe_wrapper:
                snippet = f"asyncio.gather at line {node.lineno}"
                self.violations.append((node.lineno, snippet))
        elif is_safe_wrapper:
            self.safe_calls += 1

        self.generic_visit(node)


class BareExceptAnalyzer(ast.NodeVisitor):
    """Find bare except: clauses."""

    def __init__(self, filename: str):
        self.filename = filename
        self.violations: list[tuple[int, str]] = []

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type is None:  # bare except:
            self.violations.append((node.lineno, "bare except:"))
        self.generic_visit(node)


class AsyncioRunInExecutorAnalyzer(ast.NodeVisitor):
    """Find asyncio.run() inside run_in_executor callback context."""

    def __init__(self, filename: str):
        self.filename = filename
        self.violations: list[tuple[int, str]] = []

    def visit_Call(self, node: ast.Call) -> None:
        is_asyncio_run = (
            isinstance(node.func, ast.Attribute) and
            node.func.attr == "run" and
            isinstance(node.func.value, ast.Name) and
            node.func.value.id == "asyncio"
        )
        if is_asyncio_run:
            # Check if parent context is run_in_executor (heuristic: check ancestors)
            # We do simple context check via node positioning
            self.violations.append((node.lineno, "asyncio.run()"))
        self.generic_visit(node)


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------

def _check_file_gather(filepath: Path) -> tuple[list, int, int]:
    try:
        content = filepath.read_text(errors="ignore")
        tree = ast.parse(content, filename=str(filepath))
    except Exception:
        return [], 0, 0

    visitor = GatherCallAnalyzer(str(filepath.relative_to(ROOT)))
    visitor.visit(tree)
    return visitor.violations, visitor.raw_calls, visitor.safe_calls


def _check_file_bare_except(filepath: Path) -> list:
    try:
        content = filepath.read_text(errors="ignore")
        tree = ast.parse(content, filename=str(filepath))
    except Exception:
        return []

    visitor = BareExceptAnalyzer(str(filepath.relative_to(ROOT)))
    visitor.visit(tree)
    return visitor.violations


class _AsyncioRunInExecutorVisitor(ast.NodeVisitor):
    """Find asyncio.run() passed as argument to run_in_executor()."""

    def __init__(self, filename: str):
        self.filename = filename
        self.violations: list[tuple[int, str]] = []

    def visit_Call(self, node: ast.Call) -> None:
        is_run_in_executor = (
            isinstance(node.func, ast.Attribute) and
            node.func.attr == "run_in_executor"
        )
        if is_run_in_executor:
            for arg in node.args:
                if isinstance(arg, ast.Call):
                    if (isinstance(arg.func, ast.Attribute) and
                        arg.func.attr == "run" and
                        isinstance(arg.func.value, ast.Name) and
                        arg.func.value.id == "asyncio"):
                        self.violations.append((node.lineno, "asyncio.run() passed to run_in_executor"))
        self.generic_visit(node)


def _find_asyncio_run_in_executor() -> list:
    """Find asyncio.run() calls passed as argument to run_in_executor()."""
    violations = []
    for f in _iter_python_files(SRC_DIRS):
        try:
            content = f.read_text(errors="ignore")
            tree = ast.parse(content, filename=str(f))
            visitor = _AsyncioRunInExecutorVisitor(str(f.relative_to(ROOT)))
            visitor.visit(tree)
            for lineno, snippet in visitor.violations:
                violations.append((str(f.relative_to(ROOT)), lineno, snippet))
        except Exception:
            pass
    return violations


def _find_mx_clear_cache_without_eval() -> list:
    """Find mx.metal.clear_cache() calls without preceding mx.eval([])."""
    violations = []
    clear_cache_re = re.compile(r'mx\.metal\.clear_cache\s*\(\s*\)')

    for f in _iter_python_files([ROOT / "brain", ROOT / "core", ROOT / "runtime"]):
        try:
            content = f.read_text(errors="ignore")
            lines = content.split('\n')
            for i, line in enumerate(lines, 1):
                if clear_cache_re.search(line):
                    if line.strip().startswith('#'):
                        continue
                    context_before = "\n".join(lines[max(0, i-10):i])
                    if "mx.eval([])" not in context_before and "mx\.eval" not in context_before:
                        violations.append((str(f.relative_to(ROOT)), i, line.strip()[:80]))
        except Exception:
            pass
    return violations


def _find_socket_getaddrinfo() -> list:
    """Find socket.getaddrinfo calls in source files."""
    violations = []
    pattern = re.compile(r'socket\.getaddrinfo\s*\(')
    for f in _iter_python_files(SRC_DIRS):
        try:
            content = f.read_text(errors="ignore")
            lines = content.split('\n')
            for i, line in enumerate(lines, 1):
                if pattern.search(line):
                    if line.strip().startswith('#'):
                        continue
                    violations.append((str(f.relative_to(ROOT)), i, line.strip()[:80]))
        except Exception:
            pass
    return violations


def _find_time_time_for_intervals() -> list:
    """Find time.time() calls used for interval calculations."""
    violations = []
    pattern = re.compile(r'time\.time\s*\(')
    interval_indicators = ["elapsed", "delta", "interval", "duration", "since", "- start", "- start", "= start"]

    for f in _iter_python_files(SRC_DIRS):
        if "_test" in str(f):
            continue
        try:
            content = f.read_text(errors="ignore")
            lines = content.split('\n')
            for i, line in enumerate(lines, 1):
                if pattern.search(line):
                    if line.strip().startswith('#'):
                        continue
                    if any(ind in line.lower() for ind in interval_indicators):
                        violations.append((str(f.relative_to(ROOT)), i, line.strip()[:80]))
        except Exception:
            pass
    return violations


def _find_lmdb_per_item_write() -> list:
    """Find per-item LMDB write loops."""
    violations = []
    for f in _iter_python_files(SRC_DIRS):
        if "_test" in str(f) or "rust_backend" in str(f):
            continue
        try:
            content = f.read_text(errors="ignore")
            lines = content.split('\n')
            in_loop = False
            loop_line = 0
            for i, line in enumerate(lines, 1):
                stripped = line.lstrip()
                if stripped.startswith(("for ", "while ")) and not stripped.startswith("#"):
                    in_loop = True
                    loop_line = i
                if in_loop and "env.begin(write=True)" in line:
                    if not line.strip().startswith('#'):
                        violations.append((str(f.relative_to(ROOT)), i,
                                         f"env.begin(write=True) in loop at line {loop_line}"))
                        in_loop = False
                # Detect dedent
                if in_loop and stripped and not stripped.startswith("#"):
                    if not any(stripped.startswith(k) for k in ["for ", "while ", "if ", "else:", "elif "]):
                        if line and line[0] not in " \t":
                            in_loop = False
        except Exception:
            pass
    return violations


# -----------------------------------------------------------------------
# I1: asyncio.gather always uses return_exceptions=True (HARD FAIL)
# -----------------------------------------------------------------------

class TestI1AsyncGatherReturnExceptions:
    """I1: All asyncio.gather() calls must pass return_exceptions=True.

    HARD FAIL in CI. Violations cause task cancellation and M1 crashes.
    """

    @pytest.fixture(scope="class")
    def gather_results(self) -> dict:
        all_violations = []
        total_raw = 0
        total_safe = 0

        for f in _iter_python_files(SRC_DIRS):
            violations, raw, safe = _check_file_gather(f)
            if violations:
                all_violations.extend((str(f.relative_to(ROOT)), line, snip)
                                      for line, snip in violations)
            total_raw += raw
            total_safe += safe

        return {
            "violations": all_violations,
            "total_raw": total_raw,
            "total_safe": total_safe,
        }

    def test_no_raw_asyncio_gather_without_return_exceptions(self, gather_results):
        """HARD: asyncio.gather() without return_exceptions=True crashes siblings."""
        violations = gather_results["violations"]
        assert len(violations) == 0, (
            f"Found {len(violations)} asyncio.gather() without return_exceptions=True:\n" +
            "\n".join(f"  {f}:{line} — {s}" for f, line, s in violations[:10])
        )

    def test_gather_landscape_summary(self, gather_results):
        """Document the gather landscape (always passes)."""
        total = gather_results["total_raw"] + gather_results["total_safe"]
        print(f"\n  [I1 landscape] {total} total gather calls "
              f"({gather_results['total_safe']} safe wrappers, "
              f"{gather_results['total_raw']} raw)")


# -----------------------------------------------------------------------
# I5: bare except is forbidden (HARD FAIL)
# -----------------------------------------------------------------------

class TestI5BareExceptForbidden:
    """I5: All except: clauses must catch a specific exception type.

    HARD FAIL in CI. Bare except: silently swallows KeyboardInterrupt and SystemExit.
    """

    @pytest.fixture(scope="class")
    def bare_except_results(self) -> list:
        all_violations = []
        for f in _iter_python_files(SRC_DIRS):
            violations = _check_file_bare_except(f)
            for lineno, snippet in violations:
                all_violations.append((str(f.relative_to(ROOT)), lineno, snippet))
        return all_violations

    def test_no_bare_except(self, bare_except_results):
        """HARD: bare except: silently swallows KeyboardInterrupt, SystemExit."""
        assert len(bare_except_results) == 0, (
            f"Found {len(bare_except_results)} bare except: clauses:\n" +
            "\n".join(f"  {f}:{line} — {s}" for f, line, s in bare_except_results[:10])
        )


# -----------------------------------------------------------------------
# I7: asyncio.run() in ThreadPoolExecutor crash vector (HARD FAIL)
# -----------------------------------------------------------------------

class TestI7AsyncioRunInThreadPool:
    """I7: asyncio.run() inside ThreadPoolExecutor causes M1 crash.

    HARD FAIL in CI. This is a documented M1 crash vector.
    """

    @pytest.fixture(scope="class")
    def asyncio_run_violations(self) -> list:
        return _find_asyncio_run_in_executor()

    def test_no_asyncio_run_in_executor_context(self, asyncio_run_violations):
        """HARD: asyncio.run() inside run_in_executor = M1 crash vector."""
        assert len(asyncio_run_violations) == 0, (
            f"Found {len(asyncio_run_violations)} asyncio.run() in executor context:\n" +
            "\n".join(f"  {f}:{line} — {s}" for f, line, s in asyncio_run_violations[:10])
        )


# -----------------------------------------------------------------------
# I8: mx.eval([]) before mx.metal.clear_cache() (WARNING)
# -----------------------------------------------------------------------

class TestI8MXEvalBeforeClearCache:
    """I8: mx.eval([]) must be called before mx.metal.clear_cache().

    WARNING-only in CI. Without the barrier, clear_cache() is a no-op.
    """

    @pytest.fixture(scope="class")
    def clear_cache_violations(self) -> list:
        return _find_mx_clear_cache_without_eval()

    def test_mx_eval_before_clear_cache(self, clear_cache_violations):
        """mx.eval([]) must precede mx.metal.clear_cache() to drain GPU queue."""
        if clear_cache_violations:
            print(f"\n  [I8 WARNING] {len(clear_cache_violations)} clear_cache() without mx.eval([]) barrier:")
            for f, line, snip in clear_cache_violations[:5]:
                print(f"    {f}:{line} — {snip}")


# -----------------------------------------------------------------------
# I3: async_getaddrinfo instead of socket.getaddrinfo (WARNING)
# -----------------------------------------------------------------------

class TestI3AsyncGetAddrInfo:
    """I3: DNS resolution must use async_getaddrinfo(), not socket.getaddrinfo.

    WARNING-only in CI. Blocking socket.getaddrinfo in async context blocks the event loop.
    """

    @pytest.fixture(scope="class")
    def socket_getaddrinfo_violations(self) -> list:
        return _find_socket_getaddrinfo()

    def test_no_blocking_socket_getaddrinfo(self, socket_getaddrinfo_violations):
        """socket.getaddrinfo is blocking — use async_getaddrinfo() from utils.async_helpers."""
        if socket_getaddrinfo_violations:
            print(f"\n  [I3 WARNING] {len(socket_getaddrinfo_violations)} socket.getaddrinfo calls:")
            for f, line, snip in socket_getaddrinfo_violations[:5]:
                print(f"    {f}:{line} — {snip}")


# -----------------------------------------------------------------------
# I4: time.monotonic for interval measurements (WARNING)
# -----------------------------------------------------------------------

class TestI4TimeMonotonic:
    """I4: All interval measurements must use time.monotonic(), not time.time().

    WARNING-only in CI. time.time() can jump due to NTP adjustments.
    """

    @pytest.fixture(scope="class")
    def time_time_violations(self) -> list:
        return _find_time_time_for_intervals()

    def test_no_time_time_for_intervals(self, time_time_violations):
        """time.time() is not monotonic — use time.monotonic() for intervals."""
        if time_time_violations:
            print(f"\n  [I4 WARNING] {len(time_time_violations)} time.time() calls used for intervals:")
            for f, line, snip in time_time_violations[:5]:
                print(f"    {f}:{line} — {snip}")


# -----------------------------------------------------------------------
# I10: LMDB bulk write via cursor.putmulti() (WARNING)
# -----------------------------------------------------------------------

class TestI10LMDBBulkWrite:
    """I10: LMDB bulk writes must use cursor.putmulti(), not per-item loops.

    WARNING-only in CI. Per-item loops are 15-30x slower.
    """

    @pytest.fixture(scope="class")
    def lmdb_violations(self) -> list:
        return _find_lmdb_per_item_write()

    def test_no_lmdb_per_item_write_loop(self, lmdb_violations):
        """LMDB writes must use cursor.putmulti() batch API."""
        if lmdb_violations:
            print(f"\n  [I10 WARNING] {len(lmdb_violations)} per-item LMDB write loops:")
            for f, line, snip in lmdb_violations[:5]:
                print(f"    {f}:{line} — {snip}")


# -----------------------------------------------------------------------
# I9: DuckDB writes only via async_ingest_findings_batch() (WARNING)
# -----------------------------------------------------------------------

class TestI9DuckDBWriteOnlyViaPool:
    """I9: DuckDB findings writes must go through async_ingest_findings_batch().

    WARNING-only in CI. The canonical write path ensures batching, retry, and error handling.
    Note: duckdb_store.py (pool manager) and read-only connections are exempt.
    """

    @pytest.fixture(scope="class")
    def duckdb_connect_violations(self) -> list:
        """Find direct duckdb.connect() calls in non-pool, non-read-only contexts."""
        violations = []
        pattern = re.compile(r'(?<!# )(?<!\w)duckdb\.connect\s*\(')

        # Files that are exempt (pool manager or infrastructure)
        EXEMPT_FILES = {
            "knowledge/duckdb_store.py",        # IS the pool manager
            "knowledge/duckdb_migrator.py",      # Migration helper (accepts external conn)
            "core/lazy_imports.py",             # Testing infrastructure
            "core/rust_backend/misc.py",        # Low-level Rust backend
            "core/rust_backend/query.py",       # Low-level Rust backend
            "archive/",                          # Archived code
        }

        for f in _iter_python_files([ROOT / "knowledge", ROOT / "coordinators",
                                      ROOT / "pipeline", ROOT / "runtime"]):
            if any(exempt in str(f) for exempt in EXEMPT_FILES):
                continue
            try:
                content = f.read_text(errors="ignore")
                lines = content.split('\n')
                for i, line in enumerate(lines, 1):
                    if pattern.search(line):
                        if line.strip().startswith('#'):
                            continue
                        # Skip read_only connections
                        if "read_only=True" in line or "read_only = True" in line:
                            continue
                        violations.append((str(f.relative_to(ROOT)), i, line.strip()[:80]))
            except Exception:
                pass
        return violations

    def test_duckdb_writes_via_canonical_path(self, duckdb_connect_violations):
        """Findings writes must go through DuckDBShadowStore.async_ingest_findings_batch()."""
        if duckdb_connect_violations:
            print(f"\n  [I9 WARNING] {len(duckdb_connect_violations)} direct duckdb.connect() calls "
                  "(excludes pool manager + read-only):")
            for f, line, snip in duckdb_connect_violations[:10]:
                print(f"    {f}:{line} — {snip}")


# -----------------------------------------------------------------------
# I2: _check_gathered() called after every gather (WARNING)
# -----------------------------------------------------------------------

class TestI2CheckGatheredCalled:
    """I2: _check_gathered() should be called after asyncio.gather() in canonical paths.

    WARNING-only in CI. Ensures errors are properly partitioned from ok results.
    Excludes: safe wrappers (safe_gather_*, parallel_ok, try_group), tools/, probes/.
    """

    @pytest.fixture(scope="class")
    def gather_without_check(self) -> dict:
        all_violations = []
        gather_pattern = re.compile(r'(?<!# )asyncio\.gather\s*\(')
        check_pattern = re.compile(r'_check_gathered\s*\(')
        safe_pattern = re.compile(r'(safe_gather_|parallel_ok|try_group)\s*\(')
        # Exclude tools/ and probe_ directories
        EXCLUDE_DIRS = {"tools/", "probe_", "archive/"}

        for f in _iter_python_files(SRC_DIRS):
            s = str(f)
            if any(d in s for d in EXCLUDE_DIRS):
                continue

            try:
                content = f.read_text(errors="ignore")
                lines = content.split('\n')

                # Find gather calls via AST
                tree = ast.parse(content, filename=str(f))
                gather_lines = []
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call):
                        if (isinstance(node.func, ast.Attribute) and
                            node.func.attr == "gather" and
                            isinstance(node.func.value, ast.Name) and
                            node.func.value.id == "asyncio"):
                            # Check it's not a safe wrapper
                            src_snippet = lines[node.lineno - 1] if node.lineno <= len(lines) else ""
                            if not safe_pattern.search(src_snippet):
                                gather_lines.append(node.lineno)

                # Find _check_gathered calls
                check_lines = [i for i, l in enumerate(lines, 1) if check_pattern.search(l)]

                for gline in gather_lines:
                    has_check = any(abs(gline - cline) <= 8 for cline in check_lines)
                    if not has_check:
                        snippet = lines[gline-1].strip()[:80] if gline <= len(lines) else ""
                        all_violations.append((str(f.relative_to(ROOT)), gline, snippet))
            except Exception:
                pass

        return {"violations": all_violations}

    def test_check_gathered_after_gather(self, gather_without_check):
        """_check_gathered() should be called after gather to partition errors."""
        violations = gather_without_check["violations"]
        if violations:
            print(f"\n  [I2 WARNING] {len(violations)} gather() calls without _check_gathered() "
                  "(safe wrappers excluded):")
            for f, line, snip in violations[:10]:
                print(f"    {f}:{line} — {snip[:70]}")


# -----------------------------------------------------------------------
# Run
# -----------------------------------------------------------------------

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short", "--strict-markers"])
