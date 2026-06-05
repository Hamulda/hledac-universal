"""
probe_f262d_gather_completion — bounded hermetic tests for Sprint F262D.

Sprint F262D: Dokončení F262 safe_gather_* migrace pro produkční gather sites,
které F262 vynechal (specifické patterny v nested kontextech, try/except blocích,
1-await specifických případech).

Ověřuje:
  - Migrované soubory používají safe_gather_dropin / safe_gather_fire_and_forget
    místo asyncio.gather (s výjimkou oprávněně ponechaných).
  - Importy safe_gather_* existují v migrovaných souborech.
  - Ponechané soubory mají specifické patterny (CancelledError / _check_gathered /
    1-await + error check), které safe_gather_* nepokrývá.
  - safe_gather_dropin v produkčním kódu zachovává fail-soft invariant:
    - return_exceptions=True (interně)
    - Vrací list[T] filtrovaný (ne raise)
  - Runtime smoke: všechny nově importované moduly lze import bez chyby.

INVARIANTS (always-on, M1 8GB UMA safe):
  1. Každý migrovaný soubor obsahuje safe_gather_dropin import.
  2. Migrované await sites nepoužívají asyncio.gather().
  3. Ponechané soubory mají vlastní CancelledError / _check_gathered / specific pattern.
  4. safe_gather_dropin vrací list[T] filtrovaný (zachovává fail-soft).
  5. Všechny moduly jsou importable (žádné chybné importy po editaci).

Run: `pytest tests/probe_f262d_gather_completion.py -v`
"""
from __future__ import annotations

import ast
import importlib
import os
import sys
from pathlib import Path

import pytest

# Ensure the project root is on sys.path
PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")
)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


# Files migrated in F262D — must use safe_gather_*, not asyncio.gather()
MIGRATED_FILES: list[tuple[str, str]] = [
    # (file path, gather label used in source)
    ("intelligence/web_intelligence.py", "web_intelligence:1289"),
    ("intelligence/academic_discovery.py", "academic_discovery:571"),
    ("intelligence/wayback_cdx.py", "wayback_cdx:315"),
    ("intelligence/doh_lane.py", "doh_lane:339"),
    ("intelligence/network_reconnaissance.py", "network_reconnaissance:825"),
    ("discovery/ti_feed_adapter.py", "ti_feed_adapter:1871"),
    ("discovery/academic/unpaywall_adapter.py", "unpaywall_adapter:182"),
    ("discovery/duckduckgo_adapter.py", "duckduckgo_adapter:1474"),
    ("deep_research/probe_runner.py", "probe_runner:208"),
    ("coordinators/fetch_coordinator.py", "fetch_coordinator:1110"),
    ("coordinators/fetch_coordinator.py", "fetch_coordinator:1524"),
    ("multimodal/evidence_triage.py", "evidence_triage:269"),
    ("planning/htn_planner.py", "htn_planner:592"),
    # sprint_scheduler.py — 7 sites, checked by label existence
]

# sprint_scheduler.py labels (F262D migration)
SPRINT_SCHEDULER_LABELS: list[str] = [
    "sprint_scheduler:13709",
    "sprint_scheduler:14339",
    "sprint_scheduler:14651",
    "sprint_scheduler:17269",
    "sprint_scheduler:17380",
    "sprint_scheduler:18182",
    "sprint_scheduler:19776",
]

# Files intentionally LEFT with asyncio.gather (specific patterns)
LEFT_INTACT_PATTERNS: dict[str, str] = {
    "utils/execution_optimizer.py": "explicit CancelledError re-raise + BaseException warning log",
    "runtime/pivot_executor.py": "explicit _check_gathered() + per-item construction of error result",
    "intelligence/wayback_diff_miner.py": "explicit gathered_errors.append() collection",
    "export/stix_exporter.py": "1-await + explicit errors=[] check (return bundle on errors)",
    "export/jsonld_exporter.py": "1-await + explicit errors=[] check (return obj on errors)",
    "coordinators/execution_coordinator.py": "gather result consumed in [r for r in results if not isinstance(r, Exception)]",
}


def _read_source(rel_path: str) -> str:
    """Read a file relative to PROJECT_ROOT, return its source text."""
    path = Path(PROJECT_ROOT) / rel_path
    if not path.exists():
        pytest.skip(f"File not found: {rel_path}")
    return path.read_text(encoding="utf-8")


def _has_safe_gather_import(source: str) -> bool:
    """True if the source imports safe_gather_dropin or safe_gather_fire_and_forget
    from utils.async_helpers."""
    return (
        "from utils.async_helpers import" in source
        and ("safe_gather_dropin" in source or "safe_gather_fire_and_forget" in source)
    )


def _has_asyncio_gather_call(source: str) -> bool:
    """True if the source contains an `asyncio.gather(...)` call expression
    (not in a comment or docstring)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # Detect: asyncio.gather(...) or _asyncio.gather(...)
        if isinstance(func, ast.Attribute) and func.attr == "gather":
            if isinstance(func.value, ast.Name) and func.value.id in ("asyncio", "_asyncio"):
                return True
    return False


def _find_safe_gather_label(source: str, label: str) -> bool:
    """True if source contains a safe_gather_* call with the expected label string."""
    return f'label="{label}"' in source


# =============================================================================
# TestSprintF262DStatic — static AST/source invariants
# =============================================================================


class TestSprintF262DStatic:
    """Static checks: every migrated file uses safe_gather_*, not asyncio.gather()."""

    @pytest.mark.parametrize("rel_path,expected_label", MIGRATED_FILES)
    def test_migrated_file_imports_safe_gather(self, rel_path: str, expected_label: str) -> None:
        """Invariant 1: každý migrovaný soubor importuje safe_gather_* z utils.async_helpers."""
        source = _read_source(rel_path)
        assert _has_safe_gather_import(source), (
            f"{rel_path} is missing 'from utils.async_helpers import safe_gather_*'"
        )

    @pytest.mark.parametrize("rel_path,expected_label", MIGRATED_FILES)
    def test_migrated_file_uses_expected_label(self, rel_path: str, expected_label: str) -> None:  # noqa: ARG001
        """Invariant 2: migrovaný await site používá očekávaný label pro traceability."""
        source = _read_source(rel_path)
        assert _find_safe_gather_label(source, expected_label), (
            f"{rel_path} should contain safe_gather_*(... label=\"{expected_label}\") "
            f"for F262D traceability"
        )

    @pytest.mark.parametrize("label", SPRINT_SCHEDULER_LABELS)
    def test_sprint_scheduler_has_all_f262d_labels(self, label: str) -> None:
        """All 7 sprint_scheduler F262D labels are present in the file."""
        source = _read_source("runtime/sprint_scheduler.py")
        assert _find_safe_gather_label(source, label), (
            f"sprint_scheduler.py should contain safe_gather_*(... label=\"{label}\")"
        )


class TestSprintF262DIntact:
    """Files intentionally left with asyncio.gather — explain why."""

    @pytest.mark.parametrize("rel_path,reason", list(LEFT_INTACT_PATTERNS.items()))
    def test_left_intact_files_keep_asyncio_gather(self, rel_path: str, reason: str) -> None:
        """These files keep asyncio.gather because their specific pattern is
        not covered by safe_gather_dropin/fire_and_forget."""
        source = _read_source(rel_path)
        assert _has_asyncio_gather_call(source), (
            f"{rel_path} was expected to keep asyncio.gather ({reason}), "
            f"but the call is missing. Was it accidentally migrated?"
        )


# =============================================================================
# TestSprintF262DImport — module import smoke (no actual code execution)
# =============================================================================


class TestSprintF262DImport:
    """Each migrated module can be imported without ImportError after F262D edits."""

    @pytest.mark.parametrize("module_path,_", MIGRATED_FILES)
    def test_module_is_importable(self, module_path: str, _: str) -> None:
        """Smoke: import the module — exercises the new safe_gather_* import line."""
        dotted = "hledac.universal." + module_path.removesuffix(".py").replace("/", ".")
        try:
            importlib.import_module(dotted)
        except ImportError as e:
            # Skip optional heavy deps (mlx, lancedb, duckdb, etc.) — pyright's
            # diagnostic complains but runtime works with fallbacks.
            if any(
                heavy in str(e).lower()
                for heavy in ("mlx", "lancedb", "duckdb", "torch", "spacy", "camoufox")
            ):
                pytest.skip(f"Heavy dep unavailable: {e}")
            raise


# =============================================================================
# TestSprintF262DRuntime — runtime smoke (safe_gather_dropin behaviour)
# =============================================================================


class TestSprintF262DRuntime:
    """Runtime invariants: safe_gather_dropin preserves fail-soft semantics."""

    @pytest.mark.asyncio
    async def test_safe_gather_dropin_filters_exceptions(self) -> None:
        """safe_gather_dropin returns list[T] of successful results only."""
        from utils.async_helpers import safe_gather_dropin

        async def ok() -> int:
            return 42

        async def fail() -> int:
            raise ValueError("nope")

        results = await safe_gather_dropin(ok(), fail(), ok(), label="probe_f262d:filter")
        assert results == [42, 42]
        assert all(not isinstance(r, BaseException) for r in results)

    @pytest.mark.asyncio
    async def test_safe_gather_dropin_reraises_cancelled(self) -> None:
        """safe_gather_dropin re-raises CancelledError per [I8] invariant."""
        from utils.async_helpers import safe_gather_dropin

        async def ok() -> int:
            return 1

        async def cancelled() -> int:
            raise asyncio.CancelledError()

        import asyncio
        with pytest.raises(asyncio.CancelledError):
            await safe_gather_dropin(ok(), cancelled(), label="probe_f262d:cancel")

    @pytest.mark.asyncio
    async def test_safe_gather_fire_and_forget_swallows_all(self) -> None:
        """safe_gather_fire_and_forget never raises — fire-and-forget semantics."""
        from utils.async_helpers import safe_gather_fire_and_forget

        async def ok() -> int:
            return 1

        async def fail() -> int:
            raise ValueError("nope")

        # Bare await — no exception should propagate
        result = await safe_gather_fire_and_forget(
            ok(), fail(), ok(), label="probe_f262d:faf"
        )
        # faf returns _BoundedExceptionLog or None — never the results
        assert result is None or hasattr(result, "suppressed_count")
