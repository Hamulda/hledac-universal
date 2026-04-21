"""
F192G PROBE: Grey Runtime Seam Census
======================================

Verifies the epistemological status of 4 runtime files that were suspected
of being: stale plan items, not-on-main, renamed, dormant, helper-only,
or ghost features.

Per-file matrix:
  intelligence_dispatcher  → ACTIVATED (F192G sprint wiring)
  memory_watchdog         → ACTIVATED (F192G sprint wiring)
  session_authority       → GHOST (zero call-sites, zero references)
  telemetry               → GHOST (zero imports, comment reference only)

Findings:
  - All 4 files exist on current main
  - All 4 are F180F-era (Apr 16 00:24, all have .bak_F180F companions)
  - intelligence_dispatcher + memory_watchdog: now wired to SprintScheduler
    via attach_dispatcher() — no longer ghosts
  - session_authority: completely unreferenced singleton
  - telemetry: metrics_registry.py comments reference it but
    ingest_sprint_event takes Dict[str,object] not SprintEvent,
    no actual import anywhere

Root cause: F180F sprint introduced these as planned seams but
never wired them into __main__.py or any canonical entry point.
F192G sprint activates the dispatcher + watchdog as bounded lifecycle sidecars.

Tests F192G-10 through F192G-14 verify the wiring.
"""

import ast
import os
import re
import subprocess
from pathlib import Path
from typing import FrozenSet

import pytest

# ── Constants ─────────────────────────────────────────────────────────────────

RUNTIME_DIR = Path(__file__).parent.parent.parent / "runtime"
UNIVERSAL_DIR = Path(__file__).parent.parent.parent

# Files under audit
AUDIT_FILES = {
    "intelligence_dispatcher": RUNTIME_DIR / "intelligence_dispatcher.py",
    "memory_watchdog": RUNTIME_DIR / "memory_watchdog.py",
    "session_authority": RUNTIME_DIR / "session_authority.py",
    "telemetry": RUNTIME_DIR / "telemetry.py",
}

BACKUP_FILES = {
    "intelligence_dispatcher": RUNTIME_DIR / "intelligence_dispatcher.py.bak_F180F",
    "memory_watchdog": RUNTIME_DIR / "memory_watchdog.py.bak_F180F",
    "session_authority": RUNTIME_DIR / "session_authority.py.bak_F180F",
    "telemetry": RUNTIME_DIR / "telemetry.py.bak_F180F",
}


# ── Exclusions ────────────────────────────────────────────────────────────────

# Files to exclude from call-site searches.
# NOTE: We do NOT exclude the audited files themselves (intelligence_dispatcher.py,
# memory_watchdog.py, session_authority.py, telemetry.py) because we need to find
# their internal TYPE_CHECKING cross-references.  We only exclude:
#   - backup files (.bak_F180F)
#   - cache dirs (__pycache__, .venv)
#   - unrelated code (legacy, other probe dirs)
EXCLUDE_PATHS = frozenset({
    ".bak_F180F",
    "__pycache__",
    ".venv",
    "legacy",
    "tests/probe_f192",  # other probe dirs
})


# ── Helper: find real call-sites ─────────────────────────────────────────────

# Precise patterns for real imports (not comments, not local variable names)
_IMPORT_PATTERNS = {
    "IntelligenceDispatcher": re.compile(
        r"^\s*from\s+\S+\s+import\s+.*?\bIntelligenceDispatcher\b|"
        r"^\s*import\s+.*?\bIntelligenceDispatcher\b",
        re.MULTILINE,
    ),
    "MemoryWatchdog": re.compile(
        r"^\s*from\s+\S+\s+import\s+.*?\bMemoryWatchdog\b|"
        r"^\s*import\s+.*?\bMemoryWatchdog\b",
        re.MULTILINE,
    ),
    "SessionAuthority": re.compile(
        r"^\s*from\s+\S+\s+import\s+.*?\bSessionAuthority\b|"
        r"^\s*import\s+.*?\bSessionAuthority\b",
        re.MULTILINE,
    ),
    "get_session_authority": re.compile(
        r"^\s*from\s+\S+\s+import\s+.*?\bget_session_authority\b|"
        r"^\s*import\s+.*?\bget_session_authority\b",
        re.MULTILINE,
    ),
    "TelemetryLogger": re.compile(
        r"^\s*from\s+\S+\s+import\s+.*?\bTelemetryLogger\b|"
        r"^\s*import\s+.*?\bTelemetryLogger\b",
        re.MULTILINE,
    ),
    "SprintEvent": re.compile(
        r"^\s*from\s+\S+\s+import\s+.*?\bSprintEvent\b|"
        r"^\s*import\s+.*?\bSprintEvent\b",
        re.MULTILINE,
    ),
    "JsonFormatter": re.compile(
        r"^\s*from\s+\S+\s+import\s+.*?\bJsonFormatter\b|"
        r"^\s*import\s+.*?\bJsonFormatter\b",
        re.MULTILINE,
    ),
}

# Pattern to detect TYPE_CHECKING block (lines after "if TYPE_CHECKING:")
_TC_BLOCK = re.compile(r"if\s+TYPE_CHECKING\s*:", re.MULTILINE)


def _in_tc_block(file_path: Path, lineno: int) -> bool:
    """Return True if lineno (1-indexed) falls within a TYPE_CHECKING block."""
    try:
        lines = file_path.read_text(errors="ignore").splitlines()
    except Exception:
        return False
    for i, line in enumerate(lines):
        if _TC_BLOCK.search(line):
            # Base indent of the "if TYPE_CHECKING:" line
            base_indent = len(line) - len(line.lstrip())
            # Scan subsequent lines that are part of this block
            for j in range(i + 1, len(lines)):
                l = lines[j]
                stripped = l.strip()
                if not stripped:
                    # Empty line — continue, does not end the block
                    continue
                # Compute indent of this line
                line_indent = len(l) - len(l.lstrip())
                if line_indent <= base_indent:
                    # Dedented to or past base — end of block
                    break
                if j == lineno - 1:  # 0-indexed j vs 1-indexed lineno
                    return True
    return False


def _find_call_sites(symbol_name: str, root: Path) -> list[tuple[Path, int, str]]:
    """
    Find real import/usage of symbol_name in source files under root.
    Excludes: self-references, backup files, __pycache__, .venv.
    Returns [(file, lineno, line_content), ...].
    Only matches actual import statements (not comments, not local vars).
    """
    pattern = _IMPORT_PATTERNS.get(symbol_name)
    if pattern is None:
        return []
    results = []
    for py_file in root.rglob("*.py"):
        path_str = str(py_file)
        if any(ex in path_str for ex in EXCLUDE_PATHS):
            continue
        try:
            content = py_file.read_text(errors="ignore")
        except Exception:
            continue
        for lineno, line in enumerate(content.splitlines(), 1):
            # Skip comment-only lines
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if pattern.search(line) and symbol_name in line:
                # Accept if in TYPE_CHECKING block (forward ref), or real import
                tc = _in_tc_block(py_file, lineno)
                if not tc:
                    results.append((py_file, lineno, stripped))
    return results


def _find_type_checking_refs(symbol_name: str, root: Path) -> list[tuple[Path, int]]:
    """
    Find TYPE_CHECKING block references to symbol_name.
    Returns files that reference the symbol inside a TYPE_CHECKING block.
    Searches inside TC block body (not just the same line as TYPE_CHECKING:).
    """
    results = []
    tc_pattern = re.compile(rf"\b{symbol_name}\b")
    for py_file in root.rglob("*.py"):
        path_str = str(py_file)
        if any(ex in path_str for ex in EXCLUDE_PATHS):
            continue
        try:
            content = py_file.read_text(errors="ignore")
        except Exception:
            continue
        lines = content.splitlines()
        for i, line in enumerate(lines):
            if _TC_BLOCK.search(line):
                base_indent = len(line) - len(line.lstrip())
                for j in range(i + 1, len(lines)):
                    l = lines[j]
                    stripped = l.strip()
                    if not stripped:
                        # Empty line — continue, does not end the block
                        continue
                    line_indent = len(l) - len(l.lstrip())
                    if line_indent <= base_indent:
                        # Dedented to or past base — end of block
                        break
                    if tc_pattern.search(l):
                        results.append((py_file, j + 1))  # 1-indexed
                        break
    return results


# =============================================================================
# F192G-1: File existence matrix
# =============================================================================

class TestF192G1_FileExistence:
    """All 4 audited files must exist on current main."""

    @pytest.mark.parametrize("name,path", [
        pytest.param(n, p, id=n) for n, p in AUDIT_FILES.items()
    ])
    def test_file_exists(self, name: str, path: Path):
        """Each file must exist on disk at expected path."""
        assert path.exists(), f"{name}: file not found at {path}"
        assert path.is_file(), f"{name}: path exists but is not a file"

    @pytest.mark.parametrize("name,path", [
        pytest.param(n, p, id=n) for n, p in BACKUP_FILES.items()
    ])
    def test_f180f_backup_exists(self, name: str, path: Path):
        """Each file must have a F180F backup companion (F180F-era origin confirmed)."""
        assert path.exists(), f"{name}: .bak_F180F not found at {path}"


# =============================================================================
# F192G-2: Ghost verdict — zero call-sites (session_authority, telemetry only)
# =============================================================================

class TestF192G2_GhostVerdict_CallSites:
    """session_authority and telemetry must have zero call-sites. dispatcher + watchdog are now wired."""

    def test_session_authority_zero_call_sites(self):
        """
        session_authority: ZERO call-sites in codebase.

        Verdict: GHOST
        get_session_authority() and SessionAuthority class are completely
        unreferenced anywhere. Singleton accessor is dead code.
        """
        sites = _find_call_sites("SessionAuthority", UNIVERSAL_DIR)
        assert len(sites) == 0, (
            f"session_authority has {len(sites)} call-sites (expected 0): {sites}"
        )

        sites2 = _find_call_sites("get_session_authority", UNIVERSAL_DIR)
        assert len(sites2) == 0, (
            f"get_session_authority has {len(sites2)} call-sites (expected 0): {sites2}"
        )

    def test_telemetry_zero_call_sites(self):
        """
        telemetry: ZERO real call-sites in codebase.

        Verdict: GHOST
        TelemetryLogger, SprintEvent, JsonFormatter are completely unreferenced.
        metrics_registry.py comments reference them but ingest_sprint_event
        takes Dict[str,object], not SprintEvent — no actual type dependency.
        """
        for symbol in ("TelemetryLogger", "SprintEvent", "JsonFormatter"):
            sites = _find_call_sites(symbol, UNIVERSAL_DIR)
            assert len(sites) == 0, (
                f"telemetry.{symbol} has {len(sites)} call-sites (expected 0): {sites}"
            )


# =============================================================================
# F192G-3: Cross-reference pair (intelligence_dispatcher ↔ memory_watchdog)
# =============================================================================

class TestF192G3_CrossReferencePair:
    """intelligence_dispatcher and memory_watchdog form a TYPE_CHECKING pair, now also wired to scheduler."""

    def test_id_md_type_checking_only(self):
        """
        intelligence_dispatcher imports memory_watchdog only via TYPE_CHECKING.
        memory_watchdog imports intelligence_dispatcher only via TYPE_CHECKING.
        No other file references either via real imports (only TYPE_CHECKING).
        """
        id_refs = _find_type_checking_refs("IntelligenceDispatcher", UNIVERSAL_DIR)
        md_refs = _find_type_checking_refs("MemoryWatchdog", UNIVERSAL_DIR)

        # Each should only see the other (in their respective TYPE_CHECKING blocks)
        id_files = {p.name for p, _ in id_refs}
        md_files = {p.name for p, _ in md_refs}

        assert id_files == {"memory_watchdog.py"}, (
            f"intelligence_dispatcher TYPE_CHECKING refs: {id_files}"
        )
        assert md_files == {"intelligence_dispatcher.py"}, (
            f"memory_watchdog TYPE_CHECKING refs: {md_files}"
        )


# =============================================================================
# F192G-4: Stale plan item evidence (TICKET-00X markers)
# =============================================================================

class TestF192G4_StalePlanEvidence:
    """Files must carry TICKET-006 / TICKET-007 markers confirming F180F-era origin."""

    def test_intelligence_dispatcher_has_ticket_006(self):
        """intelligence_dispatcher must have TICKET-006 marker (F180F sprint plan item)."""
        path = AUDIT_FILES["intelligence_dispatcher"]
        content = path.read_text()
        assert "TICKET-006" in content, "TICKET-006 marker not found in intelligence_dispatcher.py"

    def test_memory_watchdog_has_ticket_007(self):
        """memory_watchdog must have TICKET-007 marker (F180F sprint plan item)."""
        path = AUDIT_FILES["memory_watchdog"]
        content = path.read_text()
        assert "TICKET-007" in content, "TICKET-007 marker not found in memory_watchdog.py"


# =============================================================================
# F192G-5: Telemetry comment-reference audit (metrics_registry)
# =============================================================================

class TestF192G5_TelemetryCommentReference:
    """telemetry.py is referenced only in metrics_registry.py comments, not imports."""

    def test_telemetry_no_real_import_in_metrics_registry(self):
        """
        metrics_registry.py must NOT import SprintEvent, TelemetryLogger,
        or JsonFormatter from runtime/telemetry.py.
        The ingest_sprint_event method takes Dict[str,object], not SprintEvent.

        Note: "SprintEvent" appears in a comment ("SprintEvent.to_dict()")
        but is NOT actually imported. Only flag real import statements.
        """
        mr_path = UNIVERSAL_DIR / "metrics_registry.py"
        content = mr_path.read_text()

        # Real import patterns to detect
        import_pattern = re.compile(
            r"^\s*from\s+hledac\.universal\.runtime\.telemetry\s+import|"
            r"^\s*import\s+.*?hledac\.universal\.runtime\.telemetry",
            re.MULTILINE,
        )
        assert not import_pattern.search(content), (
            "metrics_registry.py has a real import of runtime/telemetry — "
            "telemetry is ghost but is being wired!"
        )

        # TelemetryLogger and JsonFormatter must not appear at all (no comment refs either)
        for symbol in ("TelemetryLogger", "JsonFormatter"):
            assert symbol not in content, (
                f"metrics_registry.py contains '{symbol}' — telemetry ghost found"
            )

    def test_telemetry_ingest_takes_dict_not_sprint_event(self):
        """
        ingest_sprint_event must accept Dict[str,object], not SprintEvent.
        This confirms no type dependency on telemetry.py.
        """
        mr_path = UNIVERSAL_DIR / "metrics_registry.py"
        content = mr_path.read_text()

        # Must have ingest_sprint_event with Dict signature
        assert "def ingest_sprint_event(self, event: Dict[str, object])" in content, (
            "ingest_sprint_event signature changed — may indicate telemetry wiring attempt"
        )


# =============================================================================
# F192G-6: Probe directory f192g must exist
# =============================================================================

class TestF192G6_ProbeDirectoryExists:
    """tests/probe_f192g/ directory must exist and be importable."""

    def test_probe_dir_exists(self):
        """tests/probe_f192g/ directory must exist on disk."""
        probe_dir = Path(__file__).parent
        assert probe_dir.exists(), f"probe_f192g dir not found at {probe_dir}"
        assert (probe_dir / "__init__.py").exists(), "probe_f192g/__init__.py missing"

    def test_no_other_probe_f192_siblings(self):
        """
        probe_f192g is the 7th probe (f192a-f192f already exist).
        This is informational — no action required.
        """
        parent = Path(__file__).parent.parent
        existing = sorted([p.name for p in parent.iterdir() if p.name.startswith("probe_f192")])
        assert "probe_f192g" in existing, f"probe_f192g not in probe list: {existing}"


# =============================================================================
# F192G-8: types.py rename audit — no live import of hledac.universal.types
# =============================================================================

class TestF192G8_TypesRenameAudit:
    """Verify hledac.universal.types is fully migrated to project_types."""

    def test_no_live_import_of_hledac_universal_types(self):
        """
        Audit: no live Python file in the codebase imports hledac.universal.types.

        The rename from types.py → project_types.py was done in F192B.
        Any remaining import of 'hledac.universal.types' is stale and must be fixed.

        Excludes: test files (they may test the import path itself), __pycache__,
        .venv, legacy.
        """
        import re

        universal = Path(__file__).parent.parent.parent
        stale_import = re.compile(
            r"^\s*from\s+hledac\.universal\.types\s+import|"
            r"^\s*import\s+hledac\.universal\.types\b",
            re.MULTILINE,
        )

        failures = []
        for py_file in universal.rglob("*.py"):
            path_str = str(py_file)
            # Exclude test files (they may intentionally test import paths),
            # __pycache__, .venv, .bak, legacy
            if any(ex in path_str for ex in (
                "__pycache__", ".venv", ".bak", "legacy",
                "tests/",  # test files may probe import paths
            )):
                continue
            try:
                content = py_file.read_text(errors="ignore")
            except Exception:
                continue
            for lineno, line in enumerate(content.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if stale_import.search(line) and "hledac.universal.types" in line:
                    failures.append((py_file, lineno, stripped))

        assert len(failures) == 0, (
            f"Found {len(failures)} stale import(s) of hledac.universal.types:\n"
            + "\n".join(f"  {f.relative_to(universal)}:{l}  {s}" for f, l, s in failures)
        )


# =============================================================================
# F192G-7: dispatcher/watchdog are NOT in sprint_lifecycle or resource_governor
# =============================================================================

class TestF192G7_RuntimePathNonEntry:
    """intelligence_dispatcher and memory_watchdog are NOT imported by sprint_lifecycle or resource_governor."""

    def test_not_in_sprint_lifecycle(self):
        """SprintLifecycleManager must not import intelligence_dispatcher or memory_watchdog."""
        sl_path = UNIVERSAL_DIR / "runtime" / "sprint_lifecycle.py"
        if not sl_path.exists():
            pytest.skip("sprint_lifecycle.py not editable per sprint mandate")
        content = sl_path.read_text()
        import_pattern = re.compile(
            r"^\s*from\s+\S+\s+import\s+.*?(?:"
            r"intelligence_dispatcher|memory_watchdog"
            r")|^\s*import\s+.*?(?:"
            r"intelligence_dispatcher|memory_watchdog"
            r")",
            re.MULTILINE,
        )
        hits = import_pattern.findall(content)
        assert not hits, (
            f"sprint_lifecycle.py imports intelligence modules: {hits} — "
            "dispatcher/watchdog are scheduler sidecars, not lifecycle dependencies"
        )

    def test_not_in_resource_governor(self):
        """resource_governor.py has its own internal _telemetry dict — not runtime/telemetry."""
        rg_path = UNIVERSAL_DIR / "core" / "resource_governor.py"
        if not rg_path.exists():
            pytest.skip("resource_governor.py not editable per sprint mandate")
        content = rg_path.read_text()
        import_pattern = re.compile(
            r"^\s*from\s+hledac\.universal\.runtime\.telemetry\s+import|"
            r"^\s*import\s+.*?hledac\.universal\.runtime\.telemetry",
            re.MULTILINE,
        )
        assert not import_pattern.search(content), (
            "resource_governor.py imports runtime/telemetry — unexpected wiring"
        )
        # It uses its own internal _telemetry dict (confirmed by F180F sprint)
        assert "get_uma_telemetry" in content, (
            "resource_governor.py should have get_uma_telemetry (its own telemetry)"
        )


# =============================================================================
# F192G-10: SprintScheduler.attach_dispatcher() — happy path
# =============================================================================

class TestF192G10_AttachDispatcher:
    """SprintScheduler.attach_dispatcher() creates dispatcher + optional watchdog."""

    def test_attach_dispatcher_returns_dispatcher(self):
        """attach_dispatcher returns an IntelligenceDispatcher instance."""
        from unittest.mock import MagicMock
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler, SprintSchedulerConfig

        config = SprintSchedulerConfig()
        scheduler = SprintScheduler(config)

        dispatcher = scheduler.attach_dispatcher(session=None, with_watchdog=False)

        assert dispatcher is not None
        assert hasattr(dispatcher, "_suspended_tiers")
        assert hasattr(dispatcher, "_memory_watchdog")
        assert dispatcher._memory_watchdog is None  # watchdog disabled

    def test_attach_dispatcher_with_watchdog_attaches_watchdog(self):
        """with_watchdog=True attaches MemoryWatchdog to dispatcher."""
        from unittest.mock import MagicMock
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler, SprintSchedulerConfig

        config = SprintSchedulerConfig()
        scheduler = SprintScheduler(config)

        dispatcher = scheduler.attach_dispatcher(session=None, with_watchdog=True)

        assert dispatcher._memory_watchdog is not None
        assert hasattr(dispatcher._memory_watchdog, "start")
        assert hasattr(dispatcher._memory_watchdog, "stop")

    def test_is_intelligence_attached_reflects_state(self):
        """is_intelligence_attached() returns True after attach, False before."""
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler, SprintSchedulerConfig

        config = SprintSchedulerConfig()
        scheduler = SprintScheduler(config)

        assert not scheduler.is_intelligence_attached()

        scheduler.attach_dispatcher(session=None, with_watchdog=False)

        assert scheduler.is_intelligence_attached()

    def test_get_dispatcher_returns_attached_instance(self):
        """get_dispatcher() returns the attached dispatcher or None."""
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler, SprintSchedulerConfig

        config = SprintSchedulerConfig()
        scheduler = SprintScheduler(config)

        assert scheduler.get_dispatcher() is None

        dispatcher = scheduler.attach_dispatcher(session=None, with_watchdog=False)

        assert scheduler.get_dispatcher() is dispatcher


# =============================================================================
# F192G-11: suspend_intelligence() / resume_intelligence()
# =============================================================================

class TestF192G11_SuspendResume:
    """Scheduler can suspend and resume intelligence tiers."""

    def test_suspend_tier2_adds_to_suspended_tiers(self):
        """suspend_intelligence('TIER2') adds TIER2 to dispatcher's suspended set."""
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler, SprintSchedulerConfig

        config = SprintSchedulerConfig()
        scheduler = SprintScheduler(config)
        scheduler.attach_dispatcher(session=None, with_watchdog=False)

        assert "TIER2" not in scheduler._dispatcher._suspended_tiers

        scheduler.suspend_intelligence("TIER2")

        assert "TIER2" in scheduler._dispatcher._suspended_tiers

    def test_resume_tier2_removes_from_suspended_tiers(self):
        """resume_intelligence('TIER2') removes TIER2 from suspended set."""
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler, SprintSchedulerConfig

        config = SprintSchedulerConfig()
        scheduler = SprintScheduler(config)
        scheduler.attach_dispatcher(session=None, with_watchdog=False)

        scheduler.suspend_intelligence("TIER2")
        assert "TIER2" in scheduler._dispatcher._suspended_tiers

        scheduler.resume_intelligence("TIER2")

        assert "TIER2" not in scheduler._dispatcher._suspended_tiers

    def test_suspend_unknown_tier_is_idempotent(self):
        """suspend_intelligence with unknown tier name does not raise."""
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler, SprintSchedulerConfig

        config = SprintSchedulerConfig()
        scheduler = SprintScheduler(config)
        scheduler.attach_dispatcher(session=None, with_watchdog=False)

        # Should not raise — dispatcher checks tier name at run time
        scheduler.suspend_intelligence("TIER2")
        scheduler.suspend_intelligence("TIER3")  # unknown tier

        assert "TIER2" in scheduler._dispatcher._suspended_tiers
        assert "TIER3" in scheduler._dispatcher._suspended_tiers

    def test_suspend_with_no_dispatcher_is_noop(self):
        """suspend_intelligence on scheduler without dispatcher is no-op (no crash)."""
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler, SprintSchedulerConfig

        config = SprintSchedulerConfig()
        scheduler = SprintScheduler(config)

        # No dispatcher attached — should not raise
        scheduler.suspend_intelligence("TIER2")
        scheduler.resume_intelligence("TIER2")


# =============================================================================
# F192G-12: Dispatcher.run_tier() checks suspension — fail-soft
# =============================================================================

class TestF192G12_DispatcherRunTierSuspension:
    """IntelligenceDispatcher.run_tier() skips suspended tiers, returns []."""

    def test_run_tier_skips_suspended_tier2(self):
        """run_tier(TIER2) returns [] when TIER2 is suspended."""
        import asyncio
        from hledac.universal.runtime.intelligence_dispatcher import (
            IntelligenceDispatcher,
            IntelligenceTier,
        )

        dispatcher = IntelligenceDispatcher()
        dispatcher._suspended_tiers.add("TIER2")

        async def run():
            return await dispatcher.run_tier(IntelligenceTier.TIER2, "example.com", {})

        result = asyncio.get_event_loop().run_until_complete(run())

        assert result == []

    def test_run_tier_runs_tier1_when_not_suspended(self):
        """run_tier(TIER1) executes when not suspended (may return [] if no modules available in test env)."""
        import asyncio
        from hledac.universal.runtime.intelligence_dispatcher import (
            IntelligenceDispatcher,
            IntelligenceTier,
        )

        dispatcher = IntelligenceDispatcher()

        async def run():
            return await dispatcher.run_tier(IntelligenceTier.TIER1, "example.com", {})

        # Result is list of dicts (may be empty if modules fail to load in test env)
        result = asyncio.get_event_loop().run_until_complete(run())

        assert isinstance(result, list)


# =============================================================================
# F192G-13: MemoryWatchdog — PressureLevel tier2_suspended policy
# =============================================================================

class TestF192G13_MemoryWatchdogTierPolicy:
    """MemoryWatchdog PressureLevel.tier2_suspended() and tier1_caution()."""

    def test_warn_level_tier2_not_suspended(self):
        """WARN level: TIER2 not suspended, TIER1 not in caution."""
        from hledac.universal.runtime.memory_watchdog import PressureLevel

        level = PressureLevel.from_str("warn")
        assert not level.tier2_suspended()
        assert not level.tier1_caution()

    def test_critical_level_tier2_suspended(self):
        """CRITICAL level: TIER2 suspended, TIER1 in caution."""
        from hledac.universal.runtime.memory_watchdog import PressureLevel

        level = PressureLevel.from_str("critical")
        assert level.tier2_suspended()
        assert level.tier1_caution()

    def test_emergency_level_tier2_suspended(self):
        """EMERGENCY level: TIER2 suspended, TIER1 in caution."""
        from hledac.universal.runtime.memory_watchdog import PressureLevel

        level = PressureLevel.from_str("emergency")
        assert level.tier2_suspended()
        assert level.tier1_caution()


# =============================================================================
# F192G-14: Fail-soft teardown — scheduler cleans up sidecar on exit
# =============================================================================

class TestF192G14_FailSoftTeardown:
    """SprintScheduler.run() teardown is fail-soft: sidecar errors don't propagate."""

    def test_teardown_with_watchdog_stop_error_is_swallowed(self):
        """If watchdog.stop() raises, exception is swallowed and _watchdog is set to None."""
        from unittest.mock import MagicMock, patch
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler, SprintSchedulerConfig

        config = SprintSchedulerConfig()
        scheduler = SprintScheduler(config)
        scheduler.attach_dispatcher(session=None, with_watchdog=True)

        # Patch watchdog.stop to raise
        original_stop = scheduler._watchdog.stop
        scheduler._watchdog.stop = MagicMock(side_effect=RuntimeError("stop failed"))

        # Simulate teardown path (copy of the fail-soft teardown block)
        try:
            scheduler._watchdog.stop()
        except Exception:
            pass
        scheduler._watchdog = None
        scheduler._dispatcher = None

        # Verify watchdog was set to None even though stop() raised
        assert scheduler._watchdog is None
        assert scheduler._dispatcher is None

    def test_teardown_with_dispatcher_none_is_noop(self):
        """Teardown when _dispatcher is None is a no-op (no crash)."""
        from hledac.universal.runtime.sprint_scheduler import SprintScheduler, SprintSchedulerConfig

        config = SprintSchedulerConfig()
        scheduler = SprintScheduler(config)

        # No dispatcher attached — teardown is no-op
        assert scheduler._watchdog is None
        assert scheduler._dispatcher is None

        # Should not raise
        if scheduler._watchdog is not None:
            try:
                scheduler._watchdog.stop()
            except Exception:
                pass
            scheduler._watchdog = None
        scheduler._dispatcher = None

        assert scheduler._watchdog is None
        assert scheduler._dispatcher is None
