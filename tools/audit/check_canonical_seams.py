"""
CANONICAL SEAMS verifier (Issue #23).

Single machine-readable source of truth for the four canonical entry points
enumerated in AGENTS.md > CANONICAL ENTRY POINTS. This script validates that
each seam:

  1. is importable (module + attribute resolve),
  2. is callable,
  3. has the correct async/sync shape,
  4. carries a non-empty return annotation (i.e. is "correctly typed").

Divergence (rename, retype, async/sync flip, dropped annotation) is a HARD
FAILURE so the CI gate `pytest tests/test_canonical_seams.py -x` breaks the
build instead of silently drifting.

Design notes — M1 8GB UMA / Python 3.14 best practices:
- NO top-level MLX/torch imports. Every seam module is imported lazily inside
  its checker via importlib, and we NEVER instantiate DeepHermes3Engine
  (instantiation loads the MLX model → M1 crash vector). We only introspect
  the class + method shape.
- Pure stdlib (inspect / typing) for seam validation. No network, no browser,
  no model load.
- Declarative SEAMS registry == single source of truth, shared by the test.

Usage:
    uv run python tools/audit/check_canonical_seams.py
    uv run python tools/audit/check_canonical_seams.py --json
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path


def _ensure_repo_on_path() -> None:
    """Make every seam module importable (idempotent, M1/CI safe).

    The script lives at hledac/universal/tools/audit/. Invoked standalone (not
    under pytest's `pythonpath = .`), neither root is on sys.path, so the
    fully-qualified `hledac.universal...` imports AND the project's top-level
    absolute imports (`compat`, `_core`, `brain`, ...) both fail. We walk up
    from __file__ and prepend every directory that is a valid root:

      * a directory directly containing the `hledac` package  -> `import hledac`
      * a directory directly containing `compat/` AND `brain/` -> `import compat`

    No-op under pytest (those roots are already provided by pythonpath/.).
    """
    here = Path(__file__).resolve()
    seen: set[str] = set()
    for parent in (here, *here.parents):
        # (a) directory that directly contains the `hledac` package
        if (parent / "hledac" / "__init__.py").exists():
            _add_root(str(parent), seen)
        # (b) directory that directly contains the top-level `compat`/`brain`
        #     packages used as absolute imports across the project
        if (parent / "compat" / "__init__.py").exists() and (
            parent / "brain" / "__init__.py"
        ).exists():
            _add_root(str(parent), seen)


def _add_root(root: str, seen: set[str]) -> None:
    if root in seen:
        return
    seen.add(root)
    if root not in sys.path:
        sys.path.insert(0, root)


@dataclass
class SeamSpec:
    """One canonical entry point that must never drift."""

    name: str
    module: str
    # dotted attribute path relative to `module`, e.g. "FetchCoordinator.fetch"
    attr: str
    must_be_async: bool
    note: str = ""


@dataclass
class SeamResult:
    name: str
    ok: bool
    detail: str
    spec: SeamSpec = field(repr=False)


# Canonical seams — mirrors AGENTS.md > CANONICAL ENTRY POINTS.
# These are the ONLY sanctioned entry points; any drift fails CI.
SEAMS: list[SeamSpec] = [
    SeamSpec(
        name="canonical_fetch",
        module="hledac.universal.coordinators.fetch_coordinator",
        attr="FetchCoordinator.fetch",
        must_be_async=True,
        note="Issue #1 fix: thin fetch() wrapper routing clearnet->public_fetcher, "
        "onion/i2p->FetchCoordinatorFacade. Do NOT call the adapter/facade directly.",
    ),
    SeamSpec(
        name="canonical_write",
        module="hledac.universal.knowledge.duckdb_store",
        attr="DuckDBShadowStore.async_ingest_findings_batch",
        must_be_async=True,
        note="Single canonical DuckDB write path (Invariant #5).",
    ),
    SeamSpec(
        name="canonical_ioc_graph",
        module="hledac.universal.knowledge.graph_service",
        attr="upsert_ioc",
        must_be_async=False,
        note="Issue #4: the single canonical IOC upsert (the 3x upsert_ioc impls "
        "collapse here).",
    ),
    SeamSpec(
        name="canonical_mlx",
        module="hledac.universal.brain.deephermes3_engine",
        attr="DeepHermes3Engine.generate",
        must_be_async=True,
        note="Canonical MLX inference entry (replaces the non-existent Hermes3Engine).",
    ),
]


def _resolve(spec: SeamSpec):
    """Import `module` lazily and walk the dotted `attr` path."""
    _ensure_repo_on_path()
    mod = importlib.import_module(spec.module)
    parts = spec.attr.split(".")
    obj = getattr(mod, parts[0])
    for part in parts[1:]:
        obj = getattr(obj, part)
    return obj


def _annotation_str(obj, raw) -> str:
    """Resolve a return annotation to a stable string (forward-ref safe)."""
    try:
        hints = inspect.get_annotations(obj, eval_str=True)
        if "return" in hints and hints["return"] is not None:
            return str(hints["return"])
    except Exception:
        pass
    try:
        return str(raw)
    except Exception:
        return ""


def _check_one(spec: SeamSpec) -> SeamResult:
    try:
        obj = _resolve(spec)
    except Exception as exc:  # noqa: BLE001 - any import/resolve failure is a fail
        return SeamResult(spec.name, False, f"import/resolve failed: {type(exc).__name__}: {exc}", spec)

    if not callable(obj):
        return SeamResult(spec.name, False, f"{spec.attr} is not callable", spec)

    is_async = inspect.iscoroutinefunction(obj)
    if is_async != spec.must_be_async:
        want = "async" if spec.must_be_async else "sync"
        got = "async" if is_async else "sync"
        return SeamResult(spec.name, False, f"expected {want} but got {got}", spec)

    try:
        sig = inspect.signature(obj)
        raw = sig.return_annotation
        if raw is inspect.Signature.empty:
            return SeamResult(spec.name, False, "missing return annotation", spec)
        ann = _annotation_str(obj, raw)
        if not ann or ann == "<empty>":
            return SeamResult(spec.name, False, "empty return annotation", spec)
    except Exception as exc:  # noqa: BLE001 - introspection failure is a fail
        return SeamResult(spec.name, False, f"signature introspection failed: {exc}", spec)

    shape = "async" if is_async else "sync"
    return SeamResult(spec.name, True, f"ok ({shape}, -> {ann})", spec)


def check_all_seams() -> list[SeamResult]:
    """Validate every canonical seam. Pure function — safe to call from tests."""
    return [_check_one(spec) for spec in SEAMS]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Verify canonical seams exist and are correctly typed.")
    ap.add_argument("--json", action="store_true", help="emit a JSON report instead of text")
    args = ap.parse_args(argv)

    results = check_all_seams()
    failed = [r for r in results if not r.ok]

    if args.json:
        payload = {
            "ok": not failed,
            "seams": [
                {"name": r.name, "ok": r.ok, "detail": r.detail, "note": r.spec.note}
                for r in results
            ],
        }
        print(json.dumps(payload, indent=2))
    else:
        width = max((len(r.name) for r in results), default=0)
        for r in results:
            mark = "PASS" if r.ok else "FAIL"
            print(f"[{mark}] {r.name:<{width}}  {r.detail}")
        if failed:
            print(f"\n{len(failed)}/{len(results)} canonical seam(s) FAILED")
        else:
            print(f"\nAll {len(results)} canonical seams OK")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
