"""
F196A PROBE: Canonical Baseline and Ghost Verdict.

Verifies:
1. Ghost modules are deleted from the filesystem
2. No production code imports ghost modules (real imports, not TYPE_CHECKING)
3. sprint_scheduler.py has no ghost references
4. rl/__init__.py no longer exports MARLCoordinator
5. runtime/telemetry.py remains importable (ACTIVE module)
6. _entry_to_pattern_findings returns exactly 15-element tuple (tuple contract frozen)
"""

import re
from pathlib import Path

import pytest

UNIVERSAL_DIR = Path(__file__).parent.parent.parent.resolve()
# The hledac/universal package root (where runtime/, rl/, pipeline/ live)
SRC_DIR = UNIVERSAL_DIR
RUNTIME_DIR = SRC_DIR / "runtime"
RL_DIR = SRC_DIR / "rl"

# Ghost module names (deleted in F196A)
GHOST_MODULES = {
    "intelligence_dispatcher": RUNTIME_DIR / "intelligence_dispatcher.py",
    "memory_watchdog": RUNTIME_DIR / "memory_watchdog.py",
    "session_authority": RUNTIME_DIR / "session_authority.py",
}

GHOST_BACKUPS = {
    "intelligence_dispatcher": RUNTIME_DIR / "intelligence_dispatcher.py.bak_F180F",
    "memory_watchdog": RUNTIME_DIR / "memory_watchdog.py.bak_F180F",
    "session_authority": RUNTIME_DIR / "session_authority.py.bak_F180F",
}

GHOST_SYMBOLS = ["IntelligenceDispatcher", "MemoryWatchdog", "SessionAuthority", "MARLCoordinator"]


# ─────────────────────────────────────────────────────────────────────────────
# F196A-1: Ghost module files are deleted
# ─────────────────────────────────────────────────────────────────────────────


class TestF196A1_GhostModulesDeleted:
    """Ghost modules must not exist on filesystem."""

    @pytest.mark.parametrize("name,path", GHOST_MODULES.items())
    def test_ghost_module_deleted(self, name: str, path: Path) -> None:
        assert not path.exists(), (
            f"F196A: {name} still exists at {path} — must be deleted. "
            f"Zero canonical call-sites confirmed."
        )

    @pytest.mark.parametrize("name,path", GHOST_BACKUPS.items())
    def test_ghost_backup_deleted(self, name: str, path: Path) -> None:
        assert not path.exists(), (
            f"F196A: {name} backup still exists at {path} — must be deleted."
        )


# ─────────────────────────────────────────────────────────────────────────────
# F196A-2: rl/marl_coordinator.py deleted, rl/__init__.py cleaned
# ─────────────────────────────────────────────────────────────────────────────


class TestF196A2_MARLCoordinatorDeleted:
    """rl/marl_coordinator.py is deleted (zero production call-sites)."""

    def test_marl_coordinator_file_deleted(self) -> None:
        path = RL_DIR / "marl_coordinator.py"
        assert not path.exists(), (
            f"F196A: rl/marl_coordinator.py still exists at {path} — must be deleted."
        )

    def test_rl_init_no_marl_coordinator_export(self) -> None:
        """rl/__init__.py must not export MARLCoordinator."""
        init_path = RL_DIR / "__init__.py"
        content = init_path.read_text()
        assert "MARLCoordinator" not in content, (
            "F196A: rl/__init__.py still references MARLCoordinator — "
            "must be removed from exports."
        )
        assert "marl_coordinator" not in content, (
            "F196A: rl/__init__.py still imports marl_coordinator — must be removed."
        )


# ─────────────────────────────────────────────────────────────────────────────
# F196A-3: No real (non-TYPE_CHECKING) imports of ghost modules
# ─────────────────────────────────────────────────────────────────────────────


class TestF196A3_NoRealGhostImports:
    """Production code must not import ghost modules outside TYPE_CHECKING blocks."""

    TC_BLOCK_RE = re.compile(r"if\s+TYPE_CHECKING\s*:", re.MULTILINE)

    def _in_type_checking_block(self, content: str, lineno: int) -> bool:
        """Return True if lineno (1-indexed) falls within a TYPE_CHECKING block."""
        for m in self.TC_BLOCK_RE.finditer(content):
            tc_start = content[: m.start()].count("\n") + 1
            rest = content[m.end() :]
            # Find next non-indented line to estimate TC block end
            tc_end = tc_start
            for i, line in enumerate(rest.splitlines()):
                if line.strip() and not line.startswith(" " * 8) and not line.startswith("\t"):
                    tc_end = tc_start + i
                    break
            else:
                tc_end = tc_start + rest.count("\n") + 1
            if tc_start <= lineno <= tc_end:
                return True
        return False

    def _find_real_imports(self, file_path: Path, symbol: str) -> list[str]:
        """Find non-TYPE_CHECKING imports of symbol in file."""
        try:
            content = file_path.read_text()
        except (OSError, UnicodeDecodeError):
            return []
        results: list[str] = []
        import_re = re.compile(
            rf"^\s*(?:from|import)\s+(?:\S+\s+)?(?:import\s+)?.*?\b{symbol}\b",
            re.MULTILINE,
        )
        for m in import_re.finditer(content):
            lineno = content[: m.start()].count("\n") + 1
            if not self._in_type_checking_block(content, lineno):
                results.append(f"{file_path.name}:{lineno}")
        return results

    @pytest.mark.parametrize("symbol", GHOST_SYMBOLS)
    def test_no_real_imports_in_production_code(self, symbol: str) -> None:
        """
        Ghost symbols must not appear in real (non-TYPE_CHECKING) imports.

        Excludes:
        - TYPE_CHECKING blocks (forward references only)
        - Test files (tests/ directory)
        - Documentation files (*.md)
        """
        violations: list[str] = []
        for py_file in SRC_DIR.rglob("*.py"):
            if "/tests/" in str(py_file):
                continue
            real_imports = self._find_real_imports(py_file, symbol)
            if real_imports:
                violations.extend(real_imports)

        assert not violations, (
            f"F196A: {symbol} found in real production imports: {violations}. "
            f"Ghost modules must not be imported outside TYPE_CHECKING blocks."
        )


# ─────────────────────────────────────────────────────────────────────────────
# F196A-4: sprint_scheduler.py is clean of ghost references
# ─────────────────────────────────────────────────────────────────────────────


class TestF196A4_SprintSchedulerClean:
    """sprint_scheduler.py must not reference deleted ghost modules."""

    GHOST_PATTERNS = [
        "intelligence_dispatcher",
        "memory_watchdog",
        "session_authority",
        "attach_dispatcher",
        "_dispatcher",
        "_watchdog",
        "_SprintSchedulerWatchdogCallbacks",
        "suspend_intelligence",
        "resume_intelligence",
        "get_dispatcher",
        "is_intelligence_attached",
    ]

    def test_no_ghost_references_in_sprint_scheduler(self) -> None:
        """sprint_scheduler.py must not contain any ghost module references."""
        scheduler_path = RUNTIME_DIR / "sprint_scheduler.py"
        content = scheduler_path.read_text()

        violations: list[str] = []
        for pattern in self.GHOST_PATTERNS:
            if pattern in content:
                lineno = content.split(pattern)[0].count("\n") + 1
                violations.append(f"{pattern} at line ~{lineno}")

        assert not violations, (
            f"F196A: sprint_scheduler.py still contains ghost references: {violations}. "
            f"These must be removed after ghost module deletion."
        )


# ─────────────────────────────────────────────────────────────────────────────
# F196A-5: runtime/telemetry.py is ACTIVE (not ghost)
# ─────────────────────────────────────────────────────────────────────────────


class TestF196A5_TelemetryIsActive:
    """runtime/telemetry.py is an active module — verify it remains importable."""

    def test_telemetry_file_exists(self) -> None:
        """telemetry.py must exist (ACTIVE, not deleted)."""
        telemetry_path = RUNTIME_DIR / "telemetry.py"
        assert telemetry_path.exists(), (
            "F196A: runtime/telemetry.py does not exist — "
            "it is an ACTIVE module and must not be deleted."
        )

    def test_telemetry_importable(self) -> None:
        """runtime/telemetry must be importable."""
        try:
            from hledac.universal.runtime import telemetry
            assert hasattr(telemetry, "TelemetryLogger")
            assert hasattr(telemetry, "SprintMetrics")
        except ImportError as e:
            pytest.fail(f"F196A: Cannot import runtime.telemetry: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# F196A-6: Tuple contract frozen — _entry_to_pattern_findings returns 15 elements
# ─────────────────────────────────────────────────────────────────────────────


class TestF196A6_TupleContractFrozen:
    """Verify _entry_to_pattern_findings returns exactly 15-element tuple."""

    def test_entry_to_pattern_findings_returns_15_elements(self) -> None:
        """
        _entry_to_pattern_findings must return a 15-element tuple.

        The return type annotation is the authoritative contract:
          tuple[list[dict], int, int, int, str, str, bool, bool,
                EntryQualitySignal, FallbackDecision, str, int, int, int, int]
        """
        # _entry_to_pattern_findings is verified via source inspection below

        # Read the source to verify the return type annotation
        source_path = SRC_DIR / "pipeline" / "live_feed_pipeline.py"
        content = source_path.read_text()

        # Find the function definition and extract its return type
        func_match = re.search(
            r"async def _entry_to_pattern_findings\([^)]*\)\s*->\s*tuple\[(.*?)\]:",
            content,
            re.DOTALL,
        )
        assert func_match, (
            "F196A: Could not find _entry_to_pattern_findings return type annotation"
        )

        # Count elements in the tuple annotation
        type_str = func_match.group(1)
        # Remove newlines and count commas + 1
        type_str_clean = re.sub(r"\s+", "", type_str)
        elements = type_str_clean.split(",")
        # Handle multiline entries that might be split
        actual_elements: list[str] = []
        for el in elements:
            el = el.strip()
            if not el:
                continue
            # If element contains nested tuple markers that weren't split
            actual_elements.append(el)

        assert len(actual_elements) == 15, (
            f"F196A: _entry_to_pattern_findings return type has {len(actual_elements)} elements, "
            f"expected 15. Tuple contract is not frozen."
        )

    def test_tuple_assignment_unpacks_15_vars(self) -> None:
        """
        The caller of _entry_to_pattern_findings must unpack exactly 15 values.
        This guards against accidental API drift.
        """
        source_path = SRC_DIR / "pipeline" / "live_feed_pipeline.py"
        content = source_path.read_text()

        # Find the assignment that unpacks _entry_to_pattern_findings
        # Pattern: (...variable names...) = await _entry_to_pattern_findings
        unpack_pattern = re.compile(
            r"\(\s*findings[^)]*\)\s*=\s*await\s+_entry_to_pattern_findings",
            re.DOTALL,
        )
        matches = list(unpack_pattern.finditer(content))
        assert matches, (
            "F196A: Could not find _entry_to_pattern_findings await assignment"
        )

        # Extract the variable names from the first match
        match = matches[0]
        # Get the full tuple expression
        start = match.start()
        # Find opening paren
        open_paren = content.index("(", start)
        close_paren = open_paren
        depth = 0
        for i, c in enumerate(content[open_paren:], open_paren):
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    close_paren = i
                    break
        tuple_expr = content[open_paren : close_paren + 1]

        # Count variables (names followed by comma or closing paren)
        # Remove assignment target context, extract variable names
        var_pattern = re.compile(r"^\s*\((.*)\)\s*$", re.DOTALL)
        var_match = var_pattern.match(tuple_expr)
        assert var_match, f"F196A: Could not parse tuple expression: {tuple_expr}"

        vars_str = var_match.group(1)
        # Split on commas but be careful with function calls that contain commas
        # Count the top-level commas
        vars_list: list[str] = []
        depth = 0
        current = ""
        for c in vars_str:
            if c in "([{":
                depth += 1
            elif c in ")]}":
                depth -= 1
            elif c == "," and depth == 0:
                var = current.strip()
                if var:
                    vars_list.append(var)
                current = ""
                continue
            current += c
        var = current.strip()
        if var:
            vars_list.append(var)

        assert len(vars_list) == 15, (
            f"F196A: _entry_to_pattern_findings caller unpacks {len(vars_list)} variables, "
            f"expected 15. Tuple contract is not frozen. Vars: {vars_list}"
        )
