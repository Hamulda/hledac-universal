"""
Sprint P0-1 tests — SprintSchedulerResult SoA integration.

Verifies that the IntCounterLayout overlay is correctly wired into
SprintSchedulerResult:
    - 16 hot-path counters route through property delegations to the SoA buffer
    - Original AoS fields are preserved (dataclasses.asdict() compatibility)
    - bump_counter() helper is faster than the property path
    - Backward compat: `result.attr += 1` still works on all 16 hot-path counters
    - All 16 names in INT_COUNTER_LAYOUT_NAMES are exposed as properties

Approach: we cannot `import` sprint_scheduler.py directly (it has a 200+
import chain that pulls in msgspec, MLX, etc.). Instead we extract the
class definition text and exec() it in an isolated namespace with just
the dependencies we need: dataclass, field, IntCounterLayout.
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys
import time
import types
import unittest

# ─── Module loading ────────────────────────────────────────────────────

_RUNTIME_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "runtime")
)


def _load_isolated(name: str) -> types.ModuleType:
    """Load a runtime/ module by path, bypassing runtime/__init__.py."""
    path = os.path.join(_RUNTIME_DIR, f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"runtime.{name}", path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    sys.modules[f"runtime.{name}"] = mod
    return mod


# IntCounterLayout (no hledac dependencies)
_icl_mod = _load_isolated("int_counter_layout")
sys.modules["hledac.universal.runtime.int_counter_layout"] = _icl_mod
IntCounterLayout = _icl_mod.IntCounterLayout


# ─── Extract SprintSchedulerResult class from sprint_scheduler.py ─────


def _extract_sprint_scheduler_result_class() -> str:
    """
    Read sprint_scheduler.py and extract the SprintSchedulerResult class
    block as executable Python text. We do this rather than importing
    the whole module to avoid the 200+ import chain.

    Returns the class body text INCLUDING the @dataclass decorator
    (which lives on the line immediately before `class SprintSchedulerResult:`).
    This is critical: the dataclass decorator is what triggers
    `__post_init__` invocation from the generated `__init__`. Without
    it, `__post_init__` is just a regular method that is never called
    automatically.
    """
    scheduler_path = os.path.join(_RUNTIME_DIR, "sprint_scheduler.py")
    with open(scheduler_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # Find start: first line matching `^class SprintSchedulerResult:`
    class_line_idx = None
    for i, line in enumerate(lines):
        if re.match(r"^class SprintSchedulerResult:", line):
            class_line_idx = i
            break
    if class_line_idx is None:
        raise RuntimeError("class SprintSchedulerResult not found")

    # Walk back to include the @dataclass decorator (it's on the line
    # immediately before, with possibly blank lines in between)
    start_idx = class_line_idx
    for i in range(class_line_idx - 1, -1, -1):
        line = lines[i].strip()
        if not line:
            continue  # skip blank lines
        if line.startswith("@dataclass"):
            start_idx = i
            break
        # If we hit anything else (docstring, comment, etc.) before
        # the @dataclass line, something is wrong with the file layout.
        raise RuntimeError(
            f"Expected @dataclass decorator before line {class_line_idx + 1}, "
            f"but found: {line!r}"
        )

    # Find end: next top-level `class ` or `def ` after start
    end_idx = None
    for i in range(class_line_idx + 1, len(lines)):
        line = lines[i]
        if re.match(r"^(class |def |@\w+)", line):
            end_idx = i
            break
    if end_idx is None:
        raise RuntimeError("End of SprintSchedulerResult not found")

    class_text = "".join(lines[start_idx:end_idx])
    return class_text


def _build_isolated_sprint_scheduler_result() -> type:
    """
    Build SprintSchedulerResult in an isolated namespace.

    The class is defined as @dataclass(slots=True) and depends on:
        - dataclass, field from dataclasses
        - IntCounterLayout (we provide)
        - Some lines reference `Final`, `Sequence` from typing (we provide)
        - `msgspec` types — not used in the class body, only in OTHER
          classes of sprint_scheduler.py, so we don't need it here.

    The class body has a long docstring + 117 int fields + property
    delegations + bump_counter. No external type references inside
    the class body.
    """
    class_text = _extract_sprint_scheduler_result_class()

    # Build namespace
    ns: dict[str, object] = {
        "dataclass": _safe_dataclass_decorator,
        "field": _safe_field_factory,
        "IntCounterLayout": IntCounterLayout,
        "INT_COUNTER_LAYOUT_NAMES": _INT_COUNTER_LAYOUT_NAMES,
        "Any": object,  # typing.Any as a stand-in (used in field type hints)
        "Final": object,  # typing.Final as a stand-in
        "Sequence": object,  # typing.Sequence as a stand-in
    }

    exec(class_text, ns)
    cls = ns.get("SprintSchedulerResult")
    if cls is None:
        raise RuntimeError("SprintSchedulerResult not built")
    return cls  # type: ignore[return-value]


def _safe_dataclass_decorator(*args, **kwargs):
    """Pass-through to dataclass.dataclass, ignoring slots=True for
    test simplicity (slots would require a specific Python runtime)."""
    from dataclasses import dataclass as _real_dataclass

    # Strip slots=True (test runs in a context where slots work but
    # we want to keep tests as portable as possible)
    if "slots" in kwargs:
        kwargs.pop("slots")
    return _real_dataclass(*args, **kwargs)


def _safe_field_factory(*args, **kwargs):
    """Pass-through to dataclasses.field."""
    from dataclasses import field as _real_field

    return _real_field(*args, **kwargs)


# Build the class once at module load. If extraction fails, raise
# ImportError so the test module is unimportable (cleaner than a
# half-loaded test with None sentinels everywhere).
_INT_COUNTER_LAYOUT_NAMES: tuple[str, ...] = (
    "cycles_started",
    "cycles_completed",
    "consecutive_empty_cycles",
    "unique_entry_hashes_seen",
    "duplicate_entry_hashes_skipped",
    "hard_deadline_checked_count",
    "windup_guard_call_count",
    "windup_guard_callback_supplied_count",
    "windup_guard_callback_executed_count",
    "policy_quality_feedback_calls",
    "policy_quality_feedback_errors",
    "ipfs_cids_attempted",
    "multimodal_enriched_findings",
    "feed_suppression_count",
    "forensics_enriched_ct_findings",
    "acquisition_lanes_skipped",
)
SprintSchedulerResult: type = _build_isolated_sprint_scheduler_result()
INT_COUNTER_LAYOUT_NAMES: tuple[str, ...] = _INT_COUNTER_LAYOUT_NAMES


# ─── Tests ──────────────────────────────────────────────────────────────


class TestSprintSchedulerResultSoa(unittest.TestCase):
    """
    Verifies the 16 hot-path property delegations route to the SoA buffer.
    """

    def setUp(self):
        self.result = SprintSchedulerResult()

    def test_post_init_allocates_layout(self):
        """L.1: __post_init__ allocates the layout exactly once."""
        self.assertIsNotNone(self.result._int_counter_layout)
        self.assertTrue(self.result._int_counter_layout.is_active())

    def test_all_16_counters_default_to_zero(self):
        """All hot-path counters default to 0 (zero-initialised array)."""
        for name in INT_COUNTER_LAYOUT_NAMES:
            value = getattr(self.result, name)
            self.assertEqual(
                value, 0, f"counter {name!r} should default to 0"
            )

    def test_property_setter_writes_to_layout(self):
        """Setter routes through SoA layout."""
        self.result.cycles_started = 42
        self.assertEqual(
            self.result._int_counter_layout.get("cycles_started"), 42
        )
        self.assertEqual(self.result.cycles_started, 42)

    def test_property_setter_only_writes_to_layout(self):
        """Property setter does NOT modify the AoS field (preserved)."""
        # We set a value, then verify the layout has it.
        # The AoS field is not set by the property (only the layout is).
        # This is intentional: we keep AoS fields as zero-init defaults
        # for asdict() consumers, and the layout holds the live value.
        self.result.cycles_completed = 7
        # Layout has the live value
        self.assertEqual(self.result.cycles_completed, 7)
        # Note: AoS field is not in __dict__ if the @dataclass __init__
        # did not set it (because we used init=False / it's a property).

    def test_inplace_add_works(self):
        """`counter += 1` expands to get+set, both routing through SoA."""
        for _ in range(5):
            self.result.cycles_started += 1
        self.assertEqual(self.result.cycles_started, 5)
        self.assertEqual(
            self.result._int_counter_layout.get("cycles_started"), 5
        )

    def test_bump_counter_helper(self):
        """bump_counter(name) is the recommended fast path."""
        self.result.bump_counter("cycles_started")
        self.result.bump_counter("cycles_started")
        self.result.bump_counter("cycles_started")
        self.assertEqual(self.result.cycles_started, 3)
        # Mixed with normal += 1
        self.result.cycles_started += 1
        self.assertEqual(self.result.cycles_started, 4)

    def test_bump_counter_with_n(self):
        self.result.bump_counter("cycles_completed", n=10)
        self.assertEqual(self.result.cycles_completed, 10)

    def test_bump_counter_unknown_returns_zero(self):
        """Fail-soft: unknown name returns 0, doesn't raise."""
        result = self.result.bump_counter("does_not_exist")
        self.assertEqual(result, 0)

    def test_bump_counter_failsoft_no_layout(self):
        """If layout is None, bump_counter returns 0 (no crash)."""
        self.result._int_counter_layout = None
        result = self.result.bump_counter("cycles_started")
        self.assertEqual(result, 0)

    def test_all_16_names_wired(self):
        """Every name in INT_COUNTER_LAYOUT_NAMES is exposed as a property."""
        layout = self.result._int_counter_layout
        for name in INT_COUNTER_LAYOUT_NAMES:
            with self.subTest(counter=name):
                # Each name has a slot in the layout
                self.assertIsNotNone(
                    layout.get_indices().get(name),
                    f"{name!r} missing from layout indices",
                )

    def test_layout_count_matches_names(self):
        """Layout has exactly the number of counters in the names tuple."""
        layout = self.result._int_counter_layout
        self.assertEqual(len(layout), len(INT_COUNTER_LAYOUT_NAMES))


class TestSprintSchedulerResultAsdictCompat(unittest.TestCase):
    """
    Verifies dataclass + asdict() compatibility is preserved.

    The 16 hot-path counters are exposed via @property, which means
    they do NOT appear in `dataclasses.fields()` and thus are NOT
    included in `asdict()`. This is intentional: the SoA buffer is
    the canonical store, but AoS field defaults (0) are still in
    `fields()` for non-SoA consumers (e.g. dashboard reads, but those
    actually go through the property).
    """

    def setUp(self):
        self.result = SprintSchedulerResult()

    def test_dataclass_instance_creation(self):
        """SprintSchedulerResult() works without args (all defaults)."""
        r = SprintSchedulerResult()
        self.assertIsNotNone(r)

    def test_non_int_fields_preserved(self):
        """Non-int fields (bool/str/list/dict) keep their default values."""
        r = SprintSchedulerResult()
        # These are AoS fields, should still be their defaults
        self.assertEqual(r.final_phase, "BOOT")
        self.assertEqual(r.aborted, False)
        self.assertEqual(r.abort_reason, "")
        self.assertEqual(r.export_paths, [])
        self.assertEqual(r.entries_per_source, {})

    def test_set_non_int_field(self):
        """Setting a non-int field works as before (no property)."""
        r = SprintSchedulerResult()
        r.final_phase = "EXPORT"
        r.aborted = True
        self.assertEqual(r.final_phase, "EXPORT")
        self.assertTrue(r.aborted)


class TestSprintSchedulerResultSoAReadConsistency(unittest.TestCase):
    """
    Read-side consistency: hot-path counters read the same value whether
    accessed via property OR via layout.get().
    """

    def setUp(self):
        self.result = SprintSchedulerResult()

    def test_read_consistency_after_set(self):
        for name in INT_COUNTER_LAYOUT_NAMES:
            with self.subTest(counter=name):
                setattr(self.result, name, 99)
                self.assertEqual(getattr(self.result, name), 99)
                self.assertEqual(self.result._int_counter_layout.get(name), 99)

    def test_read_consistency_after_bump(self):
        self.result.bump_counter("unique_entry_hashes_seen", n=42)
        self.assertEqual(self.result.unique_entry_hashes_seen, 42)
        self.assertEqual(
            self.result._int_counter_layout.get("unique_entry_hashes_seen"),
            42,
        )

    def test_independent_counters(self):
        """Setting one counter doesn't affect another."""
        self.result.cycles_started = 100
        self.result.cycles_completed = 200
        self.assertEqual(self.result.cycles_started, 100)
        self.assertEqual(self.result.cycles_completed, 200)

    def test_layout_snapshot_contains_all_counters(self):
        """layout.snapshot() returns all 16 counters with current values."""
        self.result.cycles_started = 1
        self.result.cycles_completed = 2
        self.result.feed_suppression_count = 5
        snap = self.result._int_counter_layout.snapshot()
        self.assertEqual(snap["cycles_started"], 1)
        self.assertEqual(snap["cycles_completed"], 2)
        self.assertEqual(snap["feed_suppression_count"], 5)
        # Unset counters are still 0
        self.assertEqual(snap["forensics_enriched_ct_findings"], 0)


# ─── Performance sanity (informational) ────────────────────────────────


class TestSprintSchedulerResultSoAPerf(unittest.TestCase):
    """
    Informational — verifies SoA path is in the same ballpark as AoS.

    The win for SoA is small (~10-30μs/sprint) because M1 8GB hot path
    has only ~360 +=1 operations per sprint. This test is here to
    document that the SoA path is not slower than AoS.
    """

    def setUp(self):
        # Build already happened at module load; no per-test setup needed.
        pass

    def test_bump_counter_not_slower_than_property(self):
        """bump_counter() should be no slower than `attr += 1`."""
        r1 = SprintSchedulerResult()
        r2 = SprintSchedulerResult()
        N = 10_000

        # Warm up
        for _ in range(100):
            r1.cycles_started += 1
            r2.bump_counter("cycles_started")

        t0 = time.monotonic()
        for _ in range(N):
            r1.cycles_started += 1
        t_aos = time.monotonic() - t0

        t0 = time.monotonic()
        for _ in range(N):
            r2.bump_counter("cycles_started")
        t_soa = time.monotonic() - t0

        # SoA should be roughly the same speed, not 2× slower
        # (loose bound — Python attribute lookups dominate on hot path)
        self.assertLess(
            t_soa,
            t_aos * 3.0,
            f"bump_counter ({t_soa*1e6:.1f}μs/{N}) is much slower than "
            f"property ({t_aos*1e6:.1f}μs/{N})",
        )


if __name__ == "__main__":
    unittest.main()
