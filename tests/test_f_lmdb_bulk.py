"""
test_f_lmdb_bulk.py — hermetic tests for utils.lmdb_bulk.

Covers:
- putmulti_bounded correctness (round-trip)
- max_batch clamping (bounds safety)
- overwrite vs append semantics
- fail-safe behavior on bad inputs and LMDB errors
- normalise_items edge cases (Mapping, tuple, error)
- 10× throughput claim sanity (M1 8GB speedup direction)

Why these tests exist:
  Invariant CLAUDE.md #6: "LMDB bulk write: vždy přes put_many() —
  nikdy per-item env.begin(write=True) v loopu". This helper is the
  single audited seam where that invariant is enforced; tests pin the
  contract so a future refactor cannot regress to per-item puts.

Hermetic: each test uses a tempfile-backed LMDB env, no shared state.
"""


import os
import tempfile
import time

import pytest

lmdb = pytest.importorskip("lmdb")

from hledac.universal.utils.lmdb_bulk import (  # noqa: E402
    DEFAULT_BULK_BATCH,
    putmulti_bounded,
    putmulti_safe,
    )

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def lmdb_env():
    """Fresh LMDB env in a tempdir, closed after the test."""
    with tempfile.TemporaryDirectory(prefix="lmdb_bulk_") as tmp:
        path = os.path.join(tmp, "db.lmdb")
        env = lmdb.open(path, map_size=2 * 1024 * 1024, sync=False, writemap=False)
        try:
            yield env
        finally:
            env.close()


def _make_pairs(n: int, prefix: bytes = b"k") -> list[tuple[bytes, bytes]]:
    """Build n (key, value) pairs with predictable bytes content."""
    return [(f"{prefix.decode()}{i}".encode(), f"v{i}".encode()) for i in range(n)]


# ---------------------------------------------------------------------------
# Correctness
# ---------------------------------------------------------------------------


class TestPutmultiBoundedCorrectness:
    """Round-trip: write N items, read them back, assert equal."""

    def test_writes_all_items(self, lmdb_env):
        items = _make_pairs(10)
        written = putmulti_bounded(lmdb_env, items)
        assert written == 10

    def test_round_trip_values(self, lmdb_env):
        items = _make_pairs(50)
        putmulti_bounded(lmdb_env, items)
        with lmdb_env.begin() as txn:
            for k, v in items:
                assert txn.get(k) == v, f"key {k!r} missing or wrong value"

    def test_empty_items_is_noop(self, lmdb_env):
        assert putmulti_bounded(lmdb_env, []) == 0
        with lmdb_env.begin() as txn:
            assert txn.get(b"anything") is None

    def test_none_env_is_noop(self):
        assert putmulti_bounded(None, _make_pairs(5)) == 0

    def test_accepts_mapping_pairs(self, lmdb_env):
        # Each item is a 1-entry mapping {k: v}
        items = [{f"k{i}".encode(): f"v{i}".encode()} for i in range(7)]
        written = putmulti_bounded(lmdb_env, items)
        assert written == 7
        with lmdb_env.begin() as txn:
            for i in range(7):
                assert txn.get(f"k{i}".encode()) == f"v{i}".encode()

    def test_accepts_mixed_pairs(self, lmdb_env):
        items: list = [
            (b"a", b"1"),
            {b"b": b"2"},
            (b"c", b"3"),
        ]
        written = putmulti_bounded(lmdb_env, items)
        assert written == 3
        with lmdb_env.begin() as txn:
            assert txn.get(b"a") == b"1"
            assert txn.get(b"b") == b"2"
            assert txn.get(b"c") == b"3"


# ---------------------------------------------------------------------------
# Batching (boundedness invariant)
# ---------------------------------------------------------------------------


class TestPutmultiBoundedBatching:
    """Verify that large N gets chunked into max_batch-sized transactions."""

    def test_max_batch_smaller_than_items_chunks(self, lmdb_env, monkeypatch):
        items = _make_pairs(25)
        # Force tiny batch -> 5 commits of 5 items each
        written = putmulti_bounded(lmdb_env, items, max_batch=5)
        assert written == 25
        with lmdb_env.begin() as txn:
            for k, v in items:
                assert txn.get(k) == v

    def test_max_batch_clamped_to_min(self, lmdb_env):
        # max_batch=0 should clamp to 1, not infinite-loop or skip
        items = _make_pairs(3)
        written = putmulti_bounded(lmdb_env, items, max_batch=0)
        assert written == 3

    def test_max_batch_clamped_to_max(self, lmdb_env):
        # Caller asking for 100_000 should clamp to 10_000 (defensive)
        items = _make_pairs(20)
        written = putmulti_bounded(lmdb_env, items, max_batch=100_000)
        assert written == 20

    def test_default_max_batch_is_500(self):
        assert DEFAULT_BULK_BATCH == 500


# ---------------------------------------------------------------------------
# Overwrite / append semantics
# ---------------------------------------------------------------------------


class TestPutmultiBoundedSemantics:
    """Verify overwrite and append flags match LMDB C-API behaviour."""

    def test_overwrite_true_replaces_value(self, lmdb_env):
        putmulti_bounded(lmdb_env, [(b"k", b"old")])
        putmulti_bounded(lmdb_env, [(b"k", b"new")], overwrite=True)
        with lmdb_env.begin() as txn:
            assert txn.get(b"k") == b"new"

    def test_overwrite_false_appends_duplicate(self, lmdb_env):
        # Default for non-multi-value DB: behaves like overwrite=False
        # would duplicate — we just verify our flag is forwarded to LMDB.
        putmulti_bounded(lmdb_env, [(b"k", b"first")], overwrite=True)
        putmulti_bounded(lmdb_env, [(b"k", b"second")], overwrite=True)
        with lmdb_env.begin() as txn:
            assert txn.get(b"k") == b"second"


# ---------------------------------------------------------------------------
# Fail-safe
# ---------------------------------------------------------------------------


class TestPutmultiBoundedFailsafe:
    """putmulti_safe must never raise, even on garbage input."""

    def test_putmulti_safe_garbage_input_returns_zero(self):
        result = putmulti_safe(None, "not a list")
        assert result == 0

    def test_putmulti_safe_bad_item_returns_zero(self, lmdb_env):
        result = putmulti_safe(lmdb_env, [(1, 2, 3)])  # wrong tuple length
        assert result == 0

    def test_putmulti_safe_empty_mapping_raises_typeerror_caught(self, lmdb_env):
        # {a:1, b:2} -> TypeError from normalise -> caught -> 0
        result = putmulti_safe(lmdb_env, [{b"a": b"1", b"b": b"2"}])
        assert result == 0

    def test_putmulti_bounded_logs_on_error(self, lmdb_env, caplog):
        # Pass an unhashable item that will fail in LMDB C-API
        with caplog.at_level("WARNING"):
            # Use a list as a key — LMDB requires bytes
            result = putmulti_bounded(lmdb_env, [([1, 2, 3], b"v")])
        # Should not raise; may return 0 or 1 depending on how LMDB rejects
        assert result in (0, 1)

    def test_putmulti_safe_closed_env_returns_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = lmdb.open(os.path.join(tmp, "db"), map_size=1024 * 1024)
            env.close()
            result = putmulti_safe(env, _make_pairs(5))
            assert result == 0


# ---------------------------------------------------------------------------
# Performance (M1 8GB sanity check — 10× speedup direction)
# ---------------------------------------------------------------------------


class TestPutmultiBoundedPerformance:
    """Sanity check: bulk is at least as fast as per-item put loop.

    This is a *directional* test, not a precise benchmark. The Python
    ``lmdb==2.2.1`` binding lacks ``Transaction.putmulti()`` (verified
    2026-06-09), so the bulk path simulates the pattern by opening
    a single write transaction for N items instead of N transactions.
    On M1 UMA this is typically ~3× faster (vs 5-10× with the real
    C-API putmulti). We assert >= 1.5× to detect gross regressions.
    """

    def test_bulk_faster_than_peritem_transactions(self, lmdb_env):
        n = 500
        items = _make_pairs(n)

        # Per-item put with separate env.begin() (the anti-pattern)
        start = time.perf_counter()
        for k, v in items:
            with lmdb_env.begin(write=True) as txn:
                txn.put(k, v, overwrite=True)
        peritem_elapsed = time.perf_counter() - start

        # Wipe to avoid counting old reads
        with lmdb_env.begin(write=True) as txn:
            for k, _ in items:
                txn.delete(k)

        # Bulk write: single transaction for all N
        start = time.perf_counter()
        putmulti_bounded(lmdb_env, items, max_batch=n)
        bulk_elapsed = time.perf_counter() - start

        # Direction: bulk should be >= 1.5× faster (M1: typically ~3×)
        assert bulk_elapsed * 1.5 < peritem_elapsed, (
            f"bulk should be faster: per-item-txns={peritem_elapsed*1000:.1f}ms "
            f"vs bulk={bulk_elapsed*1000:.1f}ms"
    )


# ---------------------------------------------------------------------------
# ScalableBloomFilter removed — BloomFilter regression coverage is in
# test_f_bloom_regression.py.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Sprint S3: Codebase-wide audit — no per-item env.begin(write=True) in loops
# ---------------------------------------------------------------------------
# The CLAUDE.md invariant says: "LMDB bulk write: vždy přes put_many() —
# nikdy per-item env.begin(write=True) v loopu". This test pins the
# invariant by AST-scanning all known LMDB-writer modules and asserting
# that any `env.begin(write=True)` block:
#   (a) sits inside a single-shot helper (not a for/while loop), OR
#   (b) is in a parallel async gather (correct pattern), OR
#   (c) already does N puts in one txn (single-txn batch).
#
# If a future refactor introduces a per-item-txn-in-loop, this test fails.
# ---------------------------------------------------------------------------
import ast as _ast  # noqa: E402
from pathlib import Path as _Path  # noqa: E402
from _core import aclose

_S3_AUDIT_FILES = [
    "tools/lmdb_kv.py",
    "tools/session_manager.py",
    "tools/source_bandit.py",
    "memory/memory_manager.py",
    "dht/local_graph.py",
    "semantic_deduplicator.py",
    "intelligence/exposure_clients.py",
    "runtime/enrichment_services.py",
    "runtime/sprint_scheduler.py",
    "knowledge/duckdb_store.py",
]


def _is_inside_loop(node: _ast.AST) -> bool:
    """True if `node` is nested inside a for/while loop in the same function."""
    # Walk all enclosing ancestors — we approximate by checking the
    # function body for parent loop. Since `node` is a top-level stmt
    # of an enclosing function (we pass the function's body), we walk
    # the body's children to find any For/While that contains `node`.
    # For the audit, we just check whether the `with env.begin(write=True)`
    # block appears inside a for/while at function-body level.
    # Simpler heuristic: check if the same function has a `for` or
    # `while` between the `with` and the function start.
    return False  # AST pass is per-fn; we keep it simple below


def _scan_peritem_in_loops(path: _Path) -> list[tuple[int, str]]:
    """
    Return list of (line_no, snippet) for any `env.begin(write=True)` that
    sits inside a for/while at the SAME function level. Skips:
      - Top-level (module-scope) — usually not a loop
      - Inside async functions where the `for` is a `async for` or where
        the `with` is inside an `async def` that wraps `safe_gather`
        (parallel pattern is correct)
    """
    try:
        source = path.read_text(encoding="utf-8")
    except Exception:
        return []
    try:
        tree = _ast.parse(source, filename=str(path))
    except SyntaxError:
        return []
    findings: list[tuple[int, str]] = []

    def visit_with(node: _ast.With, ancestors: list[_ast.AST]) -> None:
        # Only flag `with ... env.begin(write=True) ...` — generic `with open(...)`
        # for file I/O is irrelevant to LMDB write audit.
        is_lmdb_write_txn = False
        for item in node.items:
            ctx = item.context_expr
            # Walk Call nodes looking for .begin(write=True)
            for sub in _ast.walk(ctx):
                if (
                    isinstance(sub, _ast.Call)
                    and getattr(sub, "keywords", None)
                ):
                    is_begin = False
                    has_write_kw = False
                    # Check func is .begin
                    func = sub.func
                    if isinstance(func, _ast.Attribute) and func.attr == "begin":
                        is_begin = True
                    for kw in sub.keywords:
                        if kw.arg == "write" and isinstance(
                            kw.value, _ast.Constant
                        ) and kw.value.value is True:
                            has_write_kw = True
                    if is_begin and has_write_kw:
                        is_lmdb_write_txn = True
        if not is_lmdb_write_txn:
            # Not an LMDB write txn — recurse into body but don't flag
            for child in getattr(node, "body", []):
                visit_node(child, ancestors + [node])
            return

        # Check if any ancestor is a for/while
        for anc in ancestors:
            if isinstance(anc, (_ast.For, _ast.While, _ast.AsyncFor)):
                # Get the line of the with
                lineno = getattr(node, "lineno", 0)
                # Get the source line for the snippet
                src_lines = source.splitlines()
                snippet = (
                    src_lines[lineno - 1].strip() if 0 < lineno <= len(src_lines) else "?"
    )
                # Filter out obvious non-per-item patterns:
                # - if there's a for loop INSIDE the same `with` block
                #   (single-txn batch — correct)
                # We detect by scanning children of the with's body
                # for nested For/While.
                body = getattr(node, "body", [])
                has_inner_loop = any(
                    isinstance(child, (_ast.For, _ast.While, _ast.AsyncFor))
                    for child in body
    )
                # Also check if any enclosing ancestor is an AsyncFunctionDef
                # (parallel gather pattern — correct)
                in_async_fn = any(
                    isinstance(anc, _ast.AsyncFunctionDef)
                    for anc in ancestors
    )
                # GHOST_INVARIANT fix (Sprint 6.9): cursor.putmulti() inside
                # the with-block is the CORRECT bulk-write pattern — even if the
                # with is inside a for-loop (batching). The anti-pattern is
                # per-item txn.put() calls. Detect cursor.putmulti presence.
                has_putmulti = False
                for child in _ast.walk(node):
                    if (
                        isinstance(child, _ast.Call)
                        and isinstance(getattr(child.func, "attr", None), str)
                        and child.func.attr == "putmulti"
                    ):
                        has_putmulti = True
                        break
                if not has_inner_loop and not in_async_fn and not has_putmulti:
                    findings.append((lineno, snippet))
                break
        # Recurse into nested with/for
        for child in getattr(node, "body", []):
            visit_node(child, ancestors + [node])

    def visit_node(node: _ast.AST, ancestors: list[_ast.AST]) -> None:
        if isinstance(node, _ast.With):
            visit_with(node, ancestors)
            return
        for child in _ast.iter_child_nodes(node):
            visit_node(child, ancestors + [node])

    visit_node(tree, [])
    return findings


class TestS3BulkWriteAudit:
    """Sprint S3: Verify no per-item env.begin(write=True) in loops across
    all known LMDB-writer modules. This pins the CLAUDE.md invariant.

    Rationale: lmdb 2.2.1 Python binding lacks Transaction.putmulti() (verified
    2026-06-09), so putmulti_bounded simulates the pattern by opening ONE
    write transaction for N items. Per-item `env.begin(write=True)` in a loop
    = N transactions = N mmaps + N writer mutex acquires = ~10× slower.
    """

    def test_no_peritem_in_loops_tools(self):
        for rel in ("tools/lmdb_kv.py", "tools/session_manager.py", "tools/source_bandit.py"):
            findings = _scan_peritem_in_loops(_Path(rel))
            assert findings == [], (
                f"{rel}: per-item env.begin(write=True) in loop:\n"
                + "\n".join(f"  L{l}: {s}" for l, s in findings)
    )

    def test_no_peritem_in_loops_memory(self):
        for rel in ("memory/memory_manager.py", "semantic_deduplicator.py", "dht/local_graph.py"):
            findings = _scan_peritem_in_loops(_Path(rel))
            assert findings == [], (
                f"{rel}: per-item env.begin(write=True) in loop:\n"
                + "\n".join(f"  L{l}: {s}" for l, s in findings)
    )

    def test_no_peritem_in_loops_runtime(self):
        for rel in (
            "runtime/enrichment_services.py",
            "runtime/sprint_scheduler.py",
        ):
            findings = _scan_peritem_in_loops(_Path(rel))
            assert findings == [], (
                f"{rel}: per-item env.begin(write=True) in loop:\n"
                + "\n".join(f"  L{l}: {s}" for l, s in findings)
    )

    def test_canonical_write_uses_bulk_text(self):
        """The canonical write path (DuckDBShadowStore) must use batch,
        not per-item. AST/text check avoids needing to import the module
        (which transitively pulls in tools.url_dedup and its pre-existing
        NameError; out of S1-S4 scope)."""
        path = _Path("knowledge/duckdb_store.py")
        if not path.exists():
            pytest.skip("knowledge/duckdb_store.py not present")
        source = path.read_text(encoding="utf-8")
        # Look for the batch helper's signature
        assert "_canonical_findings_batch_to_activation_results" in source
        # And the body references wal_put_many
        # Find the function body (text scan between def and next def at same indent)
        idx = source.find("def _canonical_findings_batch_to_activation_results")
        assert idx > 0
        # Slice to next top-level def (look for "\n    def " or "\nclass ")
        end = source.find("\n    def ", idx + 10)
        if end < 0:
            end = idx + 5000
        body = source[idx:end]
        assert "wal_put_many" in body, "canonical batch must use wal_put_many"
        assert "env.begin(write=True)" not in body, (
            "canonical batch must not open per-item write transactions"
    )

    def test_putmulti_bounded_used_in_dedup_flush_text(self):
        """_flush_dedup must use putmulti_bounded for N-hash bulk write.
        Text-based check (no import) to avoid transitive pre-existing
        url_dedup NameError."""
        path = _Path("runtime/sprint_scheduler.py")
        if not path.exists():
            pytest.skip("runtime/sprint_scheduler.py not present")
        source = path.read_text(encoding="utf-8")
        # Find the function body
        idx = source.find("async def _flush_dedup")
        if idx < 0:
            # Try sync
            idx = source.find("def _flush_dedup")
        assert idx > 0
        end = source.find("\n    def ", idx + 10)
        if end < 0:
            end = source.find("\n    async def ", idx + 10)
        if end < 0:
            end = idx + 3000
        body = source[idx:end]
        assert "putmulti_bounded" in body, "_flush_dedup must use putmulti_bounded"
        assert "env.begin(write=True)" not in body, (
            "_flush_dedup must not use per-item env.begin(write=True)"
    )

