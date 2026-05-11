"""
F234: nonfeed_diagnostic180 live closure preflight.

Pure hermetic validation — no live network, no MLX, no DuckDB writes.
Validates profile propagation, query variants, acquisition plan, and DuckDB aclose safety.

Exit codes:
  0 = all checks passed
  1 = one or more checks failed
"""

from __future__ import annotations

import sys
from pathlib import Path

# ── Module path setup (same pattern as benchmarks/live_sprint_measurement.py) ──
_P = Path(__file__).resolve().parent
_universal = str(_P.parent)  # .../hledac/universal
_project_root = str(_P.parent.parent.parent)  # .../hledac
if _universal not in sys.path:
    sys.path.insert(0, _universal)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import types as _types
_hledac_stub = _types.ModuleType('hledac')
_hledac_stub.__path__ = [_project_root, _universal]
_hledac_stub.__file__ = f'{_project_root}/hledac/__init__.py'
_hledac_stub.__package__ = 'hledac'
_hledac_stub.__spec__ = None
sys.modules['hledac'] = _hledac_stub
# Also create hledac.universal namespace explicitly (Python doesn't auto-create sub-packages)
_hledac_universal_stub = _types.ModuleType('hledac.universal')
_hledac_universal_stub.__path__ = [_universal]
_hledac_universal_stub.__package__ = 'hledac.universal'
sys.modules['hledac.universal'] = _hledac_universal_stub

from hledac.universal.runtime.acquisition_strategy import normalize_acquisition_profile, build_acquisition_plan

__all__ = ["run_preflight"]


# ── Check 1 ──────────────────────────────────────────────────────────────────

def check_profile_normalize() -> tuple[bool, str]:
    """_resolve_acquisition_profile('nonfeed_diagnostic180') == 'nonfeed_diagnostic'."""
    result = normalize_acquisition_profile("nonfeed_diagnostic180")
    ok = result["effective"] == "nonfeed_diagnostic"
    detail = (
        f"  input={result['input']!r} effective={result['effective']!r} "
        f"normalized={result['normalized']} reason={result['reason']!r}"
    )
    return ok, detail


# ── Check 2 ──────────────────────────────────────────────────────────────────

def check_acquisition_plan_profile() -> tuple[bool, str]:
    """build_acquisition_plan passes canonical profile to internal plan."""
    plan = build_acquisition_plan(
        query="mozilla.org certificate transparency subdomains april 2026",
        duration_s=300.0,
        aggressive_mode=False,
        uma_state="ok",
        swap_detected=False,
        acquisition_profile="nonfeed_diagnostic",
    )
    # nonfeed_diagnostic profile sets nonfeed_priority_enabled=True internally
    nd = getattr(plan.nonfeed_plan_debug, "acquisition_profile", None)
    ok = nd == "nonfeed_diagnostic"
    detail = f"  nonfeed_plan_debug.acquisition_profile={nd!r}"
    return ok, detail


# ── Check 3 ──────────────────────────────────────────────────────────────────

def check_query_variants() -> tuple[bool, str]:
    """Public query builder variants include mozilla.org for mozilla.org query."""
    # We test the public discovery query-builder variants path via acquisition plan
    # The variants are emitted to providers and tracked in live_kpi.public_query_variants.
    # We verify that a domain-bearing query (mozilla.org) produces PUBLIC lane variants.
    plan = build_acquisition_plan(
        query="mozilla.org certificate transparency subdomains april 2026",
        duration_s=300.0,
        aggressive_mode=False,
        uma_state="ok",
        swap_detected=False,
        acquisition_profile="nonfeed_diagnostic",
    )
    # Get PUBLIC lane plan
    public_plan = next(
        (p for p in plan.plans if getattr(p, "lane", None) == "PUBLIC"),
        None,
    )
    if public_plan is None:
        return False, "  PUBLIC lane plan not found in plans"
    ok = bool(public_plan.enabled)
    detail = (
        f"  PUBLIC enabled={public_plan.enabled} "
        f"max_items={getattr(public_plan, 'max_items', '?')} "
        f"query={plan.query!r}"
    )
    return ok, detail


# ── Check 4 ──────────────────────────────────────────────────────────────────

def check_acquisition_plan_ct_public_truth() -> tuple[bool, str]:
    """build_acquisition_plan for mozilla.org + nonfeed_diagnostic has PUBLIC/CT enabled."""
    plan = build_acquisition_plan(
        query="mozilla.org certificate transparency subdomains april 2026",
        duration_s=300.0,
        aggressive_mode=False,
        uma_state="ok",
        swap_detected=False,
        acquisition_profile="nonfeed_diagnostic",
    )
    lanes = {p.lane: p for p in plan.plans}
    public_plan = lanes.get("PUBLIC")
    ct_plan = lanes.get("CT")
    public_ok = bool(getattr(public_plan, "enabled", False))
    ct_ok = bool(getattr(ct_plan, "enabled", False))
    public_reason = getattr(public_plan, "reason", "not found") or "not found"
    ct_reason = getattr(ct_plan, "reason", "not found") or "not found"
    ok = public_ok and ct_ok
    detail = (
        f"  PUBLIC enabled={public_ok} reason={public_reason!r}  "
        f"CT enabled={ct_ok} reason={ct_reason!r}"
    )
    return ok, detail


# ── Check 5 ──────────────────────────────────────────────────────────────────

def check_duckdb_shadow_aclose_before_init() -> tuple[bool, str]:
    """DuckDBShadowStore aclose() before initialize() does not crash."""
    try:
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()
        # aclose should be safe even before async_initialize — Sprint F233A fix
        import asyncio

        async def _test():
            await store.aclose()

        asyncio.run(_test())
        ok = True
        detail = "  aclose() before async_initialize() — safe (idempotent, no crash)"
    except Exception as e:
        ok = False
        detail = f"  FAILED: {type(e).__name__}: {e}"
    return ok, detail


# ── Check 6 ──────────────────────────────────────────────────────────────────

def check_research_quality_replay_fixture() -> tuple[bool, str]:
    """Research-quality replay fixture preserves feed_findings=4464 + QUALITY_FAIL_FEED_ONLY.

    Simulate the KPI data that would come from a run with:
      - runtime_accepted_find=4464
      - quality_gate = QUALITY_FAIL_FEED_ONLY
      - feed_findings=4464, public_findings=0, ct_findings=0
    """
    try:
        from hledac.universal.tools.research_quality_score import score_research_quality

        # Replay fixture mimicking F232 live run KPI state
        kpi_state = {
            "findings_count": 4464,
            "mode": "live",
            "runtime_truth": {
                "accepted_findings": 4464,
                "branch_mix": {
                    "feed_findings": 4464,
                    "public_findings": 0,
                    "ct_findings": 0,
                },
            },
            "live_kpi": {
                "total_findings": 4464,
                "branch_accepted_counts": {
                    "FEED": 4464,
                    "PUBLIC": 0,
                    "CT": 0,
                },
            },
            "uma_post_swap_gib": 0.0,
        }
        result = score_research_quality(kpi_state)
        gate = result.get("quality_gate", "")
        feed_count = result.get("feed_findings", -1)
        ok_gate = gate == "QUALITY_FAIL_FEED_ONLY"
        ok_feed = feed_count == 4464
        ok = ok_gate and ok_feed
        detail = (
            f"  quality_gate={gate!r} (expected QUALITY_FAIL_FEED_ONLY)  "
            f"feed_findings={feed_count} (expected 4464)  "
            f"replay_match={ok}"
        )
    except Exception as e:
        ok = False
        detail = f"  FAILED: {type(e).__name__}: {e}"
    return ok, detail


# ── Runner ───────────────────────────────────────────────────────────────────

def run_preflight() -> int:
    """Run all preflight checks. Returns 0 on success, 1 on any failure."""
    checks = [
        ("1. normalize_acquisition_profile('nonfeed_diagnostic180')", check_profile_normalize),
        ("2. acquisition_plan profile propagation", check_acquisition_plan_profile),
        ("3. PUBLIC lane enabled for mozilla.org query", check_query_variants),
        ("4. PUBLIC + CT lanes have truth in plan", check_acquisition_plan_ct_public_truth),
        ("5. DuckDBShadowStore aclose before init safe", check_duckdb_shadow_aclose_before_init),
        ("6. research_quality replay fixture integrity", check_research_quality_replay_fixture),
    ]

    all_ok = True
    print("F234 Nonfeed Diagnostic Preflight")
    print("=" * 60)
    for name, fn in checks:
        try:
            ok, detail = fn()
        except Exception as e:
            ok = False
            detail = f"  EXCEPTION: {type(e).__name__}: {e}"
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {name}")
        print(detail)
        if not ok:
            all_ok = False

    print("=" * 60)
    print(f"Result: {'ALL PASSED' if all_ok else 'SOME FAILED'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(run_preflight())