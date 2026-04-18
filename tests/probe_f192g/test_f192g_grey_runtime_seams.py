"""
F192G PROBE: Grey Runtime Seam Census
======================================

Verifies the epistemological status of 4 runtime files that were suspected
of being: stale plan items, not-on-main, renamed, dormant, helper-only,
or ghost features.

Per-file matrix:
  intelligence_dispatcher  → GHOST (zero call-sites, self-reference only)
  memory_watchdog         → GHOST (zero call-sites, self-reference only)
  session_authority       → GHOST (zero call-sites, zero references)
  telemetry               → GHOST (zero imports, comment reference only)

Findings:
  - All 4 files exist on current main
  - All 4 are F180F-era (Apr 16 00:24, all have .bak_F180F companions)
  - All 4 have zero call-sites in canonical sprint path
  - All 4 are orphaned pair: intelligence_dispatcher ↔ memory_watchdog
    (TYPE_CHECKING cross-reference only, no real wiring)
  - session_authority: completely unreferenced singleton
  - telemetry: metrics_registry.py comments reference it but
    ingest_sprint_event takes Dict[str,object] not SprintEvent,
    no actual import anywhere

Root cause: F180F sprint introduced these as planned seams but
never wired them into __main__.py or any canonical entry point.
They remain as "potential future" infrastructure with zero runtime presence.

Deferred: No action taken (per sprint mandate — no promotion, no wiring).
These remain as documented ghost seams for future audit.
"""

import ast
import os
import re
import subprocess
from pathlib import Path
from typing import FrozenSet

import pytest

# ── Constants ─────────────────────────────────────────────────────────────────

RUNTIME_DIR = Path(__file__).parent.parent.parent.parent / "runtime"
UNIVERSAL_DIR = Path(__file__).parent.parent.parent.parent

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
# F192G-2: Ghost verdict — zero call-sites
# =============================================================================

class TestF192G2_GhostVerdict_CallSites:
    """All 4 files must have zero call-sites in canonical sprint path."""

    def test_intelligence_dispatcher_zero_call_sites(self):
        """
        intelligence_dispatcher: ZERO real call-sites in codebase.

        Verdict: GHOST
        Only self-reference (TYPE_CHECKING in memory_watchdog) exists.
        No entry point, no wiring, no canonical path usage.
        """
        sites = _find_call_sites("IntelligenceDispatcher", UNIVERSAL_DIR)
        assert len(sites) == 0, (
            f"intelligence_dispatcher has {len(sites)} call-sites (expected 0): {sites}"
        )

    def test_memory_watchdog_zero_call_sites(self):
        """
        memory_watchdog: ZERO real call-sites in codebase.

        Verdict: GHOST
        Only self-reference (TYPE_CHECKING in intelligence_dispatcher) exists.
        No entry point, no wiring, no canonical path usage.
        """
        sites = _find_call_sites("MemoryWatchdog", UNIVERSAL_DIR)
        assert len(sites) == 0, (
            f"memory_watchdog has {len(sites)} call-sites (expected 0): {sites}"
        )

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
    """intelligence_dispatcher and memory_watchdog form an orphaned TYPE_CHECKING pair."""

    def test_id_md_only_reference_each_other(self):
        """
        intelligence_dispatcher imports memory_watchdog only via TYPE_CHECKING.
        memory_watchdog imports intelligence_dispatcher only via TYPE_CHECKING.
        No other file references either.
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

    def test_no_real_import_wiring(self):
        """
        Neither intelligence_dispatcher nor memory_watchdog is imported
        via a real (non-TYPE_CHECKING) import anywhere.
        """
        for symbol in ("IntelligenceDispatcher", "MemoryWatchdog"):
            sites = _find_call_sites(symbol, UNIVERSAL_DIR)
            assert len(sites) == 0, f"{symbol} has unexpected real import: {sites}"


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
# F192G-7: Runtime path non-entry verification
# =============================================================================

class TestF192G7_RuntimePathNonEntry:
    """None of the 4 ghost files are imported or invoked in any canonical entry point."""

    def test_not_in_sprint_scheduler(self):
        """SprintScheduler must not import any of the 4 ghost modules."""
        ss_path = UNIVERSAL_DIR / "runtime" / "sprint_scheduler.py"
        if not ss_path.exists():
            pytest.skip("sprint_scheduler.py not editable per sprint mandate")
        content = ss_path.read_text()
        # Only flag actual imports, not comments or local variable names
        import_pattern = re.compile(
            r"^\s*from\s+\S+\s+import\s+.*?(?:"
            r"intelligence_dispatcher|memory_watchdog|session_authority|telemetry"
            r")|^\s*import\s+.*?(?:"
            r"intelligence_dispatcher|memory_watchdog|session_authority|telemetry"
            r")",
            re.MULTILINE,
        )
        hits = import_pattern.findall(content)
        assert not hits, (
            f"sprint_scheduler.py imports ghost modules: {hits} — "
            "these are runtime ghosts, not wired to canonical path"
        )

    def test_not_in_sprint_lifecycle(self):
        """SprintLifecycleManager must not import any of the 4 ghost modules."""
        sl_path = UNIVERSAL_DIR / "runtime" / "sprint_lifecycle.py"
        if not sl_path.exists():
            pytest.skip("sprint_lifecycle.py not editable per sprint mandate")
        content = sl_path.read_text()
        import_pattern = re.compile(
            r"^\s*from\s+\S+\s+import\s+.*?(?:"
            r"intelligence_dispatcher|memory_watchdog|session_authority|telemetry"
            r")|^\s*import\s+.*?(?:"
            r"intelligence_dispatcher|memory_watchdog|session_authority|telemetry"
            r")",
            re.MULTILINE,
        )
        hits = import_pattern.findall(content)
        assert not hits, (
            f"sprint_lifecycle.py imports ghost modules: {hits} — "
            "these are runtime ghosts, not wired to canonical path"
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
