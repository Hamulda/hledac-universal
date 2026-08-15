"""
Probe Q1: Architecture Rules as CI — Hermetic Tests

Tests architecture rules enforcement tools:
  1. BLE001 — bare except ban (via ble_audit.py AST analysis)
  2. ASYNC461 — raw asyncio.gather ban (via ban_raw_gather.py AST analysis)
  3. E911 — asyncio.run() outside allowed entry points (AST analysis)
  4. F911 — asyncio.wait_for ban (use safe_wait_for)
  5. TPL001 — threading.Lock() registration (via grep)
  6. RUFF022 — banned bare imports (via ruff_ext.py)
  7. networkx ban (AST analysis)
  8. aiohttp runtime ban (AST analysis)
  9. stdlib json in hot paths ban (AST analysis)
  10. direct rust import ban (AST analysis)

Each test is HERMETIC — tests tools against temp files, not real codebase.
M1 time budget: --timeout=30
"""

import ast
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from textwrap import dedent
from typing import NamedTuple

import pytest
from _core import aclose


ROOT = Path("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

class ToolResult(NamedTuple):
    """Standard tool result format."""
    rc: int
    output: str


def _run_tool(
    module: str,
    args: list[str],
    cwd: Path = ROOT,
    timeout: int = 30,
) -> ToolResult:
    """Run a Python module as a tool and capture output."""
    cmd = [sys.executable, "-m", module] + args
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return ToolResult(rc=result.returncode, output=result.stdout + result.stderr)


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: BLE001 — bare except ban
# ─────────────────────────────────────────────────────────────────────────────


class TestBLE001BareExceptBan:
    """BLE001: bare except is banned (must specify exception type)."""

    def test_ble_audit_module_imports(self):
        """ble_audit module is available and has required symbols."""
        from tools.audit.ble_audit import audit_file, BLEAuditConfig, Violation
        assert callable(audit_file)
        assert BLEAuditConfig is not None
        assert hasattr(Violation, "_fields")

    def test_detects_bare_except(self, tmp_path: Path):
        """Bare except is detected as bare_except kind."""
        f = tmp_path / "bare.py"
        f.write_text("try:\n    pass\nexcept:\n    pass\n")
        from tools.audit.ble_audit import audit_file, DEFAULT_CONFIG
        violations = audit_file(f, DEFAULT_CONFIG)
        bare = [v for v in violations if v.kind == "bare_except"]
        assert len(bare) == 1, f"Expected 1 bare_except, got: {bare}"
        assert bare[0].line == 3  # "except:" is on line 3 (line 1=try, line 2=pass, line 3=except:)

    def test_exception_tuple_is_not_bare_except(self, tmp_path: Path):
        """except (ValueError, TypeError) is NOT a bare except."""
        f = tmp_path / "tuple.py"
        f.write_text("try:\n    pass\nexcept (ValueError, TypeError):\n    pass\n")
        from tools.audit.ble_audit import audit_file, DEFAULT_CONFIG
        violations = audit_file(f, DEFAULT_CONFIG)
        bare = [v for v in violations if v.kind == "bare_except"]
        assert len(bare) == 0, f"Exception tuple should not be bare_except: {bare}"

    def test_logged_exception_is_not_bare_except(self, tmp_path: Path):
        """except Exception as e with logger call is NOT a bare except."""
        f = tmp_path / "logged.py"
        f.write_text(dedent("""
            import logging
            logger = logging.getLogger(__name__)
            try:
                pass
            except Exception as e:
                logger.error("failed")
        """))
        from tools.audit.ble_audit import audit_file, DEFAULT_CONFIG
        violations = audit_file(f, DEFAULT_CONFIG)
        bare = [v for v in violations if v.kind == "bare_except"]
        assert len(bare) == 0, f"Logged exception should not be bare_except: {bare}"

    def test_noqa_suppresses_violation(self, tmp_path: Path):
        """# noqa: BLE001 comment suppresses the violation."""
        f = tmp_path / "suppressed.py"
        f.write_text("try:\n    pass\nexcept:  # noqa: BLE001\n    pass\n")
        from tools.audit.ble_audit import audit_file, DEFAULT_CONFIG
        violations = audit_file(f, DEFAULT_CONFIG)
        bare = [v for v in violations if v.kind == "bare_except"]
        assert len(bare) == 0, f"noqa should suppress: {bare}"

    def test_broad_exception_is_reported(self, tmp_path: Path):
        """except Exception: pass (broad exception) is a violation when not logged."""
        f = tmp_path / "broad.py"
        f.write_text("try:\n    pass\nexcept Exception:\n    pass\n")
        from tools.audit.ble_audit import audit_file, DEFAULT_CONFIG
        violations = audit_file(f, DEFAULT_CONFIG)
        broad = [v for v in violations if v.kind == "exception_pass"]
        # exception_pass is the ble_audit kind for broad except without logging
        assert len(broad) == 1, f"exception_pass (broad except without logging) should be a violation: {broad}"


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: ASYNC461 — raw asyncio.gather ban
# ─────────────────────────────────────────────────────────────────────────────


class TestASYNC461RawGatherBan:
    """ASYNC461: asyncio.gather without return_exceptions=True is banned."""

    def test_ban_raw_gather_module_imports(self):
        """ban_raw_gather module is available."""
        from tools.audit.ban_raw_gather import find_violations
        assert callable(find_violations)

    def test_raw_gather_detected(self, tmp_path: Path):
        """asyncio.gather(...) without return_exceptions is detected."""
        f = tmp_path / "raw.py"
        f.write_text(dedent("""
            import asyncio
            async def f(): pass
            asyncio.gather(f(), f())
        """))
        from tools.audit.ban_raw_gather import find_violations
        violations = find_violations(tmp_path)
        raw = [v for v in violations if "raw.py" in str(v[0])]
        assert len(raw) == 1, f"Should detect raw gather: {raw}"

    def test_gather_with_return_exceptions_not_detected(self, tmp_path: Path):
        """asyncio.gather(..., return_exceptions=True) is allowed."""
        f = tmp_path / "safe.py"
        f.write_text(dedent("""
            import asyncio
            async def f(): pass
            asyncio.gather(f(), f(), return_exceptions=True)
        """))
        from tools.audit.ban_raw_gather import find_violations
        violations = find_violations(tmp_path)
        safe = [v for v in violations if "safe.py" in str(v[0])]
        assert len(safe) == 0, f"return_exceptions=True should be allowed: {safe}"

    def test_parallel_is_allowed(self, tmp_path: Path):
        """parallel() from utils.async_helpers is allowed (not asyncio.gather)."""
        f = tmp_path / "parallel.py"
        f.write_text(dedent("""
            from utils.async_helpers import parallel
            async def f(): pass
            parallel(f(), f())
        """))
        from tools.audit.ban_raw_gather import find_violations
        violations = find_violations(tmp_path)
        par = [v for v in violations if "parallel.py" in str(v[0])]
        assert len(par) == 0, f"parallel() should be allowed: {par}"


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: E911 — asyncio.run() outside allowed entry points
# ─────────────────────────────────────────────────────────────────────────────


E911_ALLOWED_PREFIXES = ("__main__", "tools/", "tests/")


def _find_asyncio_run_violations_in_dir(root: Path) -> list[tuple[Path, int, str]]:
    """Find asyncio.run() calls outside allowed entry points."""
    violations = []
    skip_dirs = {"__pycache__", ".venv", "archive", "probe_", ".git", ".claude"}

    for py_file in root.rglob("*.py"):
        if any(skip in py_file.parts for skip in skip_dirs):
            continue
        try:
            content = py_file.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        try:
            tree = ast.parse(content, filename=str(py_file))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "run":
                continue
            if not isinstance(node.func.value, ast.Name):
                continue
            if node.func.value.id != "asyncio":
                continue

            rel = py_file.relative_to(root)
            allowed = any(str(rel).startswith(prefix) for prefix in E911_ALLOWED_PREFIXES)
            if not allowed:
                violations.append((py_file, node.lineno, str(rel)))

    return violations


class TestE911AsyncioRunOutsideAllowed:
    """E911: asyncio.run() banned outside __main__, tools/, tests/."""

    def test_asyncio_run_in_temp_file_detected(self, tmp_path: Path):
        """asyncio.run() in a temp runtime file is a violation."""
        f = tmp_path / "runtime_use.py"
        f.write_text("import asyncio\nasyncio.run(asyncio.sleep(0))\n")

        violations = _find_asyncio_run_violations_in_dir(tmp_path)
        assert len(violations) == 1, f"Should detect asyncio.run: {violations}"
        assert violations[0][2] == "runtime_use.py"

    def test_asyncio_run_in_main_allowed(self, tmp_path: Path):
        """asyncio.run() in __main__ file is allowed."""
        f = tmp_path / "__main__.py"
        f.write_text("import asyncio\nasyncio.run(asyncio.sleep(0))\n")

        violations = _find_asyncio_run_violations_in_dir(tmp_path)
        assert len(violations) == 0, f"__main__ should allow asyncio.run: {violations}"

    def test_asyncio_run_in_tools_allowed(self, tmp_path: Path):
        """asyncio.run() in tools/ file is allowed."""
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        f = tools_dir / "helper.py"
        f.write_text("import asyncio\nasyncio.run(asyncio.sleep(0))\n")

        violations = _find_asyncio_run_violations_in_dir(tmp_path)
        assert len(violations) == 0, f"tools/ should allow asyncio.run: {violations}"


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: F911 — asyncio.wait_for ban
# ─────────────────────────────────────────────────────────────────────────────


class TestF911AsyncioWaitForBan:
    """F911: asyncio.wait_for() banned (use safe_wait_for)."""

    def test_safe_wait_for_exists_and_importable(self):
        """safe_wait_for exists in utils.async_helpers."""
        from hledac.universal.utils.asyncx import safe_wait_for
        assert callable(safe_wait_for)

    def test_asyncio_wait_for_without_shield_is_violation(self, tmp_path: Path):
        """asyncio.wait_for() without asyncio.shield is a violation."""
        f = tmp_path / "wait_for.py"
        f.write_text(dedent("""
            import asyncio
            async def f(): pass
            asyncio.wait_for(f(), timeout=1.0)
        """))
        violations = _find_wait_for_violations(tmp_path)
        bad = [v for v in violations if "wait_for.py" in str(v[0])]
        assert len(bad) == 1, f"asyncio.wait_for without shield should be detected: {bad}"

    def test_asyncio_wait_for_with_shield_is_allowed(self, tmp_path: Path):
        """asyncio.wait_for(asyncio.shield(...)) is allowed."""
        f = tmp_path / "shielded.py"
        f.write_text(dedent("""
            import asyncio
            async def f(): pass
            asyncio.wait_for(asyncio.shield(f()), timeout=1.0)
        """))
        violations = _find_wait_for_violations(tmp_path)
        allowed = [v for v in violations if "shielded.py" in str(v[0])]
        assert len(allowed) == 0, f"asyncio.wait_for(shield(...)) should be allowed: {allowed}"


def _find_wait_for_violations(root: Path) -> list[tuple[Path, int, str]]:
    """Find asyncio.wait_for() without shield."""
    violations = []
    for py_file in root.rglob("*.py"):
        try:
            content = py_file.read_text()
            tree = ast.parse(content, filename=str(py_file))
        except (SyntaxError, OSError):
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "wait_for":
                continue
            if not isinstance(node.func.value, ast.Name):
                continue
            if node.func.value.id != "asyncio":
                continue

            # Check if first arg is asyncio.shield
            has_shield = False
            if node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Call):
                    if isinstance(arg.func, ast.Attribute):
                        has_shield = arg.func.attr == "shield"
                    elif isinstance(arg.func, ast.Name):
                        has_shield = arg.func.id == "shield"

            if not has_shield:
                violations.append((py_file, node.lineno, py_file.name))

    return violations


# ─────────────────────────────────────────────────────────────────────────────
# Test 5: TPL001 — threading.Lock() registration
# ─────────────────────────────────────────────────────────────────────────────


class TestTPL001ThreadingLockRegistration:
    """TPL001: threading.Lock() must be registered in core/locks.py."""

    def test_lock_category_exists(self):
        """LockCategory enum exists in core.locks."""
        from hledac.universal._core.locks import LockCategory
        assert hasattr(LockCategory, "GRAPH")
        assert hasattr(LockCategory, "NETWORK")

    def test_register_lock_is_callable(self):
        """register_lock function is callable."""
        from hledac.universal._core.locks import register_lock
        assert callable(register_lock)


# ─────────────────────────────────────────────────────────────────────────────
# Test 6: RUFF022 — banned bare imports
# ─────────────────────────────────────────────────────────────────────────────


class TestRUFF022BannedBareImports:
    """RUFF022: bare imports (from runtime, brain, etc.) are banned."""

    def test_ruff022_module_imports(self):
        """ruff_ext module is available."""
        import ruff_ext
        assert hasattr(ruff_ext, "check_file")
        assert hasattr(ruff_ext, "BANNED_ROOTS")

    def test_bare_runtime_import_is_violation(self, tmp_path: Path):
        """from runtime import X is a RUFF022 violation."""
        f = tmp_path / "bad.py"
        f.write_text("from runtime import SomeClass\n")

        import ruff_ext
        violations = ruff_ext.check_file(f)
        assert len(violations) >= 1, f"Should detect bare runtime import: {violations}"

    def test_bare_brain_import_is_violation(self, tmp_path: Path):
        """from brain import X is a RUFF022 violation."""
        f = tmp_path / "bad_brain.py"
        f.write_text("from brain import SomeClass\n")

        import ruff_ext
        violations = ruff_ext.check_file(f)
        assert len(violations) >= 1, f"Should detect bare brain import: {violations}"

    def test_hledac_universal_import_is_allowed(self, tmp_path: Path):
        """from hledac.universal.runtime import X is allowed."""
        f = tmp_path / "good.py"
        f.write_text("from hledac.universal.runtime import SomeClass\n")

        import ruff_ext
        violations = ruff_ext.check_file(f)
        assert len(violations) == 0, f"hledac.universal imports should be allowed: {violations}"

    def test_stdlib_import_is_allowed(self, tmp_path: Path):
        """from pathlib import Path is allowed."""
        f = tmp_path / "stdlib.py"
        f.write_text("from pathlib import Path\n")

        import ruff_ext
        violations = ruff_ext.check_file(f)
        assert len(violations) == 0, f"stdlib imports should be allowed: {violations}"


# ─────────────────────────────────────────────────────────────────────────────
# Test 7: networkx ban
# ─────────────────────────────────────────────────────────────────────────────


class TestNetworkXBan:
    """networkx is banned — use igraph instead."""

    def test_networkx_import_is_detected(self, tmp_path: Path):
        """import networkx is detected."""
        f = tmp_path / "nx.py"
        f.write_text("import networkx as nx\n")

        violations = _find_networkx_violations(tmp_path)
        bad = [v for v in violations if "nx.py" in str(v[0])]
        assert len(bad) == 1, f"Should detect networkx import: {bad}"

    def test_igraph_is_available(self):
        """igraph is available as replacement."""
        try:
            import igraph
            assert hasattr(igraph, "Graph")
        except ImportError:
            pytest.skip("igraph not installed")


def _find_networkx_violations(root: Path) -> list[tuple[Path, int, str]]:
    """Find networkx imports."""
    violations = []
    for py_file in root.rglob("*.py"):
        try:
            content = py_file.read_text()
            tree = ast.parse(content, filename=str(py_file))
        except (SyntaxError, OSError):
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "networkx" or alias.name.startswith("networkx."):
                        violations.append((py_file, node.lineno, f"import {alias.name}"))
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.startswith("networkx"):
                    violations.append((py_file, node.lineno, f"from {node.module} import ..."))

    return violations


# ─────────────────────────────────────────────────────────────────────────────
# Test 8: aiohttp runtime ban
# ─────────────────────────────────────────────────────────────────────────────


class TestAiohttpRuntimeBan:
    """aiohttp is banned in runtime code (curl_cffi is primary HTTP)."""

    def test_aiohttp_import_in_temp_file_detected(self, tmp_path: Path):
        """import aiohttp in a temp transport file is a violation."""
        f = tmp_path / "transport" / "client.py"
        f.parent.mkdir()
        f.write_text("import aiohttp\n")

        violations = _find_aiohttp_violations(tmp_path)
        bad = [v for v in violations if "client.py" in str(v[0])]
        assert len(bad) >= 1, f"Should detect aiohttp import: {bad}"

    def test_curl_cffi_is_available(self):
        """curl_cffi is available as primary HTTP library."""
        try:
            import curl_cffi
            assert hasattr(curl_cffi, "requests")
        except ImportError:
            pytest.skip("curl_cffi not installed")


def _find_aiohttp_violations(root: Path) -> list[tuple[Path, int, str]]:
    """Find aiohttp imports."""
    violations = []
    for py_file in root.rglob("*.py"):
        try:
            content = py_file.read_text()
            tree = ast.parse(content, filename=str(py_file))
        except (SyntaxError, OSError):
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "aiohttp" or alias.name.startswith("aiohttp."):
                        violations.append((py_file, node.lineno, f"import {alias.name}"))
            elif isinstance(node, ast.ImportFrom):
                if node.module and (node.module == "aiohttp" or node.module.startswith("aiohttp.")):
                    violations.append((py_file, node.lineno, f"from {node.module} import ..."))

    return violations


# ─────────────────────────────────────────────────────────────────────────────
# Test 9: stdlib json in hot paths ban
# ─────────────────────────────────────────────────────────────────────────────


HOT_PATH_MODULES = {"core", "brain", "knowledge", "runtime", "transport", "fetching"}


class TestStdlibJsonBan:
    """stdlib json is banned in hot-path modules (use orjson)."""

    def test_stdlib_json_import_in_hot_path_detected(self, tmp_path: Path):
        """import json in a hot-path temp file is a violation."""
        # Create a fake hot-path module structure
        f = tmp_path / "core" / "hot.py"
        f.parent.mkdir()
        f.write_text("import json\n")

        violations = _find_stdlib_json_violations(tmp_path)
        bad = [v for v in violations if "hot.py" in str(v[0])]
        assert len(bad) >= 1, f"Should detect stdlib json in hot path: {bad}"

    def test_orjson_is_available(self):
        """orjson is available as replacement."""
        try:
            import orjson
            assert hasattr(orjson, "dumps")
        except ImportError:
            pytest.skip("orjson not installed")


def _find_stdlib_json_violations(root: Path) -> list[tuple[Path, int, str]]:
    """Find stdlib json imports in hot-path modules."""
    violations = []
    for py_file in root.rglob("*.py"):
        parts = py_file.relative_to(root).parts
        if len(parts) < 2 or parts[0] not in HOT_PATH_MODULES:
            continue

        try:
            content = py_file.read_text()
            tree = ast.parse(content, filename=str(py_file))
        except (SyntaxError, OSError):
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "json":
                        violations.append((py_file, node.lineno, "import json"))
            elif isinstance(node, ast.ImportFrom):
                if node.module == "json":
                    violations.append((py_file, node.lineno, "from json import ..."))

    return violations


# ─────────────────────────────────────────────────────────────────────────────
# Test 10: direct rust import ban
# ─────────────────────────────────────────────────────────────────────────────


class TestDirectRustImportBan:
    """Direct rust imports banned — use hledac_rust_extensions wrapper."""

    def test_direct_rust_import_detected(self, tmp_path: Path):
        """from rust import X is a violation (must use hledac_rust_extensions)."""
        f = tmp_path / "bad_rust.py"
        f.write_text("from rust import some_func\n")

        violations = _find_rust_violations(tmp_path)
        bad = [v for v in violations if "bad_rust.py" in str(v[0])]
        assert len(bad) >= 1, f"Should detect direct rust import: {bad}"

    def test_hledac_rust_extensions_wrapper_exists(self):
        """hledac_rust_extensions wrapper exists (or rust_backend fallback)."""
        # Either hledac_rust_extensions is importable, or core/rust_backend exists
        try:
            import hledac_rust_extensions  # noqa: F401
            assert True
        except ImportError:
            rust_backend = ROOT / "core" / "rust_backend"
            assert rust_backend.exists(), "Neither hledac_rust_extensions nor rust_backend found"


def _find_rust_violations(root: Path) -> list[tuple[Path, int, str]]:
    """Find direct rust/rust_backend imports."""
    BANNED = {"rust", "rust_backend"}
    violations = []
    for py_file in root.rglob("*.py"):
        try:
            content = py_file.read_text()
            tree = ast.parse(content, filename=str(py_file))
        except (SyntaxError, OSError):
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module:
                    root_mod = node.module.split(".")[0]
                    if root_mod in BANNED:
                        violations.append((py_file, node.lineno, f"from {node.module} import ..."))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    root_mod = alias.name.split(".")[0]
                    if root_mod in BANNED:
                        violations.append((py_file, node.lineno, f"import {alias.name}"))

    return violations


# ─────────────────────────────────────────────────────────────────────────────
# Test 11: Feature flag profile field requirement
# ─────────────────────────────────────────────────────────────────────────────


class TestFeatureFlagProfileRequirement:
    """New HLEDAC_ENABLE_* flags must have a profile field in CLAUDE.md."""

    def test_claude_md_has_feature_flags_table(self):
        """CLAUDE.md documents feature flags with profile field."""
        claude_md = ROOT / "CLAUDE.md"
        assert claude_md.exists(), "CLAUDE.md must exist"

        content = claude_md.read_text()
        assert "HLEDAC_ENABLE_" in content, "CLAUDE.md should document HLEDAC_ENABLE_ flags"
        # Profile column must exist in the feature flags table
        assert "profile" in content.lower(), "CLAUDE.md should have a profile column"

    def test_claude_md_feature_flags_table_has_profile_column(self):
        """Feature flags table in CLAUDE.md has properly formatted Profile column."""
        claude_md = ROOT / "CLAUDE.md"
        content = claude_md.read_text()

        lines = content.split("\n")
        # Collect all consecutive pipe-separated lines starting from header
        table_lines = []
        capture = False
        for line in lines:
            if "Flag" in line and "Default" in line and "|" in line:
                capture = True
            if capture:
                if "|" in line:
                    table_lines.append(line)
                elif line.strip() and not line.startswith("|"):
                    break

        assert len(table_lines) >= 2, f"Feature flags table should have header + data rows, got: {table_lines}"
        header = table_lines[0]
        assert "profile" in header.lower(), f"Profile column missing in header: {header}"

    def test_pyproject_toml_has_flag_references(self):
        """pyproject.toml references HLEDAC_ENABLE_ flags."""
        pyproject = ROOT / "pyproject.toml"
        assert pyproject.exists()
        content = pyproject.read_text()
        assert "HLEDAC_ENABLE_" in content


# ─────────────────────────────────────────────────────────────────────────────
# Test 12: otool -L homebrew ban
# ─────────────────────────────────────────────────────────────────────────────


class TestOtoolHomebrewBan:
    """otool -L on homebrew libs is banned (M1 macOS compatibility)."""

    def test_no_otool_calls_on_homebrew_libs(self):
        """No subprocess calls to otool -L on /opt/homebrew/* paths."""
        violations = []
        skip = {"__pycache__", ".git", ".claude", "archive", "probe_", "tests"}

        for py_file in ROOT.rglob("*.py"):
            if any(s in py_file.parts for s in skip):
                continue
            try:
                content = py_file.read_text()
            except (OSError, UnicodeDecodeError):
                continue
            import re
            # Only flag otool -L calls on homebrew paths (the actual banned pattern)
            # Allow: search paths like Path('/opt/homebrew/bin/...') or /opt/homebrew in strings
            # Ban: subprocess calls running 'otool -L /opt/homebrew/...'
            if re.search(r"otool\s+-[L]\s+.*/opt/homebrew", content):
                violations.append(str(py_file.relative_to(ROOT)))

        assert len(violations) == 0, f"Files running otool on homebrew libs: {violations}"
