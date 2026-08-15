#!/usr/bin/env python3
"""
R0: Nonfeed Reality Lock Audit Probe
====================================



Hermetický read-only audit, který validuje R0 invariants (Q1-Q9) z
`tests/test_r0_nonfeed_reality_lock.py` a generuje deterministické
artifacts:

    archive/probe_r/probe_r0_nonfeed_reality_lock/
        REPORT_NONFEED_REALITY_LOCK.md     # Lidsky čitelný audit report
        nonfeed_reality_lock.json          # Strojově čitelný JSON summary

Invarianty (z test_r0_nonfeed_reality_lock.py):
    Q1   core.__main__.run_sprint je canonical sprint owner
    Q2-Q3 sprint_scheduler importuje acquisition_strategy + source_finding_bridge
         a volá run_enabled_acquisition_lanes
    Q4   crtsh_adapter, passive_dns, WaybackDiffMiner existují
    Q5   source_finding_bridge.exposes CT/Wayback/PassiveDNS converters
    Q9   NonfeedCandidateLedger (rodiny + stadia + MAX_LEDGER_SIZE=500)

DESIGN:
- Read-only, zero side effects kromě vlastního výstupního adresáře
- M1-kompatibilní: pouze ast.parse + importlib (žádný MLX, curl_cffi, network)
- Idempotentní: opakovaný běh generuje identické výstupy (až na timestamp)
- Lazy: fixtures v conftest.py volají tento runner on-demand

Usage:
    # Manuální spuštění
    PYTHONPATH=. python tools/probe_r0_nonfeed_reality_lock.py

    # V testech (automaticky přes fixture)
    pytest tests/test_r0_nonfeed_reality_lock.py

Sprint F26X refactor: cutting-edge Python 3.14 (PEP 695 type aliases, PEP 604
type hints, @dataclass(frozen=True, slots=True)).
"""


import ast
import json
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from core import aclose

# ── Configuration ────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent.parent
PROBE_DIR = REPO_ROOT / "archive/probe_r/probe_r0_nonfeed_reality_lock"
REPORT_MD = PROBE_DIR / "REPORT_NONFEED_REALITY_LOCK.md"
SUMMARY_JSON = PROBE_DIR / "nonfeed_reality_lock.json"

# Testy, které očekávají artifacts:
#   tests/test_r0_nonfeed_reality_lock.py::TestNoProductionEdits
EXPECTED_ARTIFACTS = (REPORT_MD, SUMMARY_JSON)


# ── Result types (cutting-edge: frozen + slots) ─────────────────────────────


@dataclass(frozen=True, slots=True)
class CheckResult:
    """One invariant verification outcome."""

    question: str           # "Q1", "Q2-Q3", ...
    name: str               # krátký název testu
    passed: bool
    detail: str             # lidsky čitelný detail


@dataclass(frozen=True, slots=True)
class AuditReport:
    """Celkový výsledek R0 auditu."""

    timestamp: str
    total_checks: int
    passed: int
    failed: int
    checks: tuple[CheckResult, ...]

    @property
    def verdict(self) -> str:
        return "R0_LOCK_PASS" if self.failed == 0 else "R0_LOCK_FAIL"


# ── Helpers ─────────────────────────────────────────────────────────────────


def _read_source(rel_path: str) -> str:
    """Safe file read with explicit encoding."""
    path = REPO_ROOT / rel_path
    with path.open(encoding="utf-8") as f:
        return f.read()


def _ast_imports_modules(source: str) -> set[str]:
    """Return set of module paths imported via `from X import Y`."""
    tree = ast.parse(source)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _ast_called_names(source: str) -> set[str]:
    """Return set of bare-name function call targets (e.g. `run_enabled_acquisition_lanes(...)`)."""
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            names.add(node.func.id)
    return names


# ── Q1: canonical owner ─────────────────────────────────────────────────────


def check_q1_canonical_owner() -> CheckResult:
    """core.__main__.run_sprint je canonical sprint owner."""
    try:
        sys.path.insert(0, str(REPO_ROOT))
        from runtime_authority_manifest import CANONICAL_SPRINT_OWNER  # noqa: PLC0415

        expected = "hledac.universal.core.__main__.run_sprint"
        passed = CANONICAL_SPRINT_OWNER == expected
        return CheckResult(
            question="Q1",
            name="canonical_sprint_owner",
            passed=passed,
            detail=f"CANONICAL_SPRINT_OWNER={CANONICAL_SPRINT_OWNER!r} expected={expected!r}",
        )
    except Exception as e:
        return CheckResult(
            question="Q1", name="canonical_sprint_owner", passed=False, detail=f"import failed: {e}"
        )


# ── Q2-Q3: sprint_scheduler wiring ──────────────────────────────────────────


def check_q2_q3_scheduler_wiring() -> list[CheckResult]:
    """sprint_scheduler importuje acquisition_strategy + source_finding_bridge a volá run_enabled_acquisition_lanes."""
    results: list[CheckResult] = []
    rel = "runtime/sprint_scheduler.py"
    try:
        source = _read_source(rel)
    except FileNotFoundError:
        return [
            CheckResult("Q2-Q3", "scheduler_source_readable", False, f"{rel} not found"),
        ]

    imports = _ast_imports_modules(source)
    calls = _ast_called_names(source)

    has_acq = any("acquisition_strategy" in m for m in imports)
    results.append(
        CheckResult(
            "Q2-Q3",
            "scheduler_imports_acquisition_strategy",
            has_acq,
            f"imported={sorted(m for m in imports if 'acquisition' in m)}",
        )
    )

    has_run_enabled = "run_enabled_acquisition_lanes" in calls
    results.append(
        CheckResult(
            "Q2-Q3",
            "scheduler_calls_run_enabled_acquisition_lanes",
            has_run_enabled,
            f"present={has_run_enabled}",
        )
    )

    has_bridge = any("source_finding_bridge" in m for m in imports)
    results.append(
        CheckResult(
            "Q2-Q3",
            "scheduler_imports_source_finding_bridge",
            has_bridge,
            f"imported={sorted(m for m in imports if 'source_finding' in m)}",
        )
    )

    return results


# ── Q4: adapters exist ──────────────────────────────────────────────────────


def check_q4_adapters() -> list[CheckResult]:
    """crtsh_adapter, passive_dns, wayback_diff_miner exist."""
    checks: list[Callable[[], CheckResult]] = [
        lambda: CheckResult(
            "Q4",
            "crtsh_adapter_call_crtsh_callable",
            _check_callable("discovery.crtsh_adapter", "call_crtsh"),
            "from discovery.crtsh_adapter import call_crtsh",
        ),
        lambda: CheckResult(
            "Q4",
            "passive_dns_call_lookup_callable",
            _check_callable("security.passive_dns", "call_lookup_passive_dns"),
            "from security.passive_dns import call_lookup_passive_dns",
        ),
        lambda: CheckResult(
            "Q4",
            "wayback_diff_miner_class_exists",
            _check_callable("intelligence.wayback_diff_miner", "WaybackDiffMiner"),
            "from recon.wayback_diff_miner import WaybackDiffMiner",
        ),
    ]
    return [c() for c in checks]


def _check_callable(module: str, name: str) -> bool:
    try:
        mod = __import__(module, fromlist=[name])
        return callable(getattr(mod, name, None))
    except Exception:
        return False


# ── Q5: source_finding_bridge converters ────────────────────────────────────


def check_q5_bridge() -> list[CheckResult]:
    """source_finding_bridge.exposes CT/Wayback/PassiveDNS converters."""
    converters = [
        ("ct_results_to_findings", "CT"),
        ("wayback_results_to_findings", "Wayback"),
        ("passive_dns_results_to_findings", "PassiveDNS"),
    ]
    results: list[CheckResult] = []
    for fn_name, family in converters:
        results.append(
            CheckResult(
                "Q5",
                f"source_finding_bridge.{fn_name}_callable",
                _check_callable("runtime.source_finding_bridge", fn_name),
                f"family={family}",
            )
        )
    return results


# ── Q9: NonfeedCandidateLedger ──────────────────────────────────────────────


def check_q9_ledger() -> list[CheckResult]:
    """NonfeedCandidateLedger exists with families, stages, MAX_LEDGER_SIZE=500."""
    results: list[CheckResult] = []

    try:
        sys.path.insert(0, str(REPO_ROOT))
        from runtime.nonfeed_candidate_ledger import (  # noqa: PLC0415
            FAMILY_CT,
            FAMILY_PASSIVE_DNS,
            FAMILY_PIVOT,
            FAMILY_PUBLIC,
            FAMILY_WAYBACK,
            MAX_LEDGER_SIZE,
            STAGE_ACCEPTED,
            STAGE_DISCOVERED,
            STAGE_PROVIDER_FAILED,
            STAGE_QUARANTINED,
            STAGE_REJECTED,
            STAGE_STORED,
        )

        families_ok = (
            FAMILY_PUBLIC == "PUBLIC"
            and FAMILY_CT == "CT"
            and FAMILY_WAYBACK == "WAYBACK"
            and FAMILY_PASSIVE_DNS == "PASSIVE_DNS"
            and FAMILY_PIVOT == "PIVOT"
        )
        results.append(
            CheckResult(
                "Q9",
                "ledger_family_constants",
                families_ok,
                "PUBLIC/CT/WAYBACK/PASSIVE_DNS/PIVOT defined",
            )
        )

        stages_ok = (
            STAGE_DISCOVERED == "discovered"
            and STAGE_QUARANTINED == "quarantined"
            and STAGE_REJECTED == "rejected"
            and STAGE_STORED == "stored"
            and STAGE_ACCEPTED == "accepted"
            and STAGE_PROVIDER_FAILED == "provider_failed"
        )
        results.append(
            CheckResult(
                "Q9",
                "ledger_stage_constants",
                stages_ok,
                "6 stages defined",
            )
        )

        results.append(
            CheckResult(
                "Q9",
                "ledger_max_size_bound",
                MAX_LEDGER_SIZE == 500,
                f"MAX_LEDGER_SIZE={MAX_LEDGER_SIZE}",
            )
        )
    except Exception as e:
        results.append(
            CheckResult("Q9", "ledger_imports", False, f"import failed: {e}")
        )

    return results


# ── Audit runner ────────────────────────────────────────────────────────────


def run_audit() -> AuditReport:
    """Execute all R0 invariant checks and return immutable AuditReport."""
    checks: list[CheckResult] = []
    checks.append(check_q1_canonical_owner())
    checks.extend(check_q2_q3_scheduler_wiring())
    checks.extend(check_q4_adapters())
    checks.extend(check_q5_bridge())
    checks.extend(check_q9_ledger())

    passed = sum(1 for c in checks if c.passed)
    failed = sum(1 for c in checks if not c.passed)
    return AuditReport(
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime()),
        total_checks=len(checks),
        passed=passed,
        failed=failed,
        checks=tuple(checks),
    )


# ── Output: markdown report ─────────────────────────────────────────────────


def render_markdown(report: AuditReport) -> str:
    lines: list[str] = [
        "# R0: Nonfeed Reality Lock Audit Report",
        "",
        f"**Generated:** {report.timestamp}  ",
        f"**Total checks:** {report.total_checks}  ",
        f"**Passed:** {report.passed}  ",
        f"**Failed:** {report.failed}  ",
        f"**Verdict:** `{report.verdict}`",
        "",
        "## R0 Invariants",
        "",
        "| Q | Check | Result | Detail |",
        "|---|-------|--------|--------|",
    ]
    for c in report.checks:
        status = "✅ PASS" if c.passed else "❌ FAIL"
        lines.append(f"| {c.question} | `{c.name}` | {status} | {c.detail} |")

    lines.extend(
        [
            "",
            "## Probe Methodology",
            "",
            "Hermetický read-only audit, který:",
            "1. Parsuje `runtime/sprint_scheduler.py` AST a hledá importy + volání",
            "2. Importuje `runtime_authority_manifest` a ověřuje canonical owner",
            "3. Importuje discovery/security/intelligence adaptéry (read-only)",
            "4. Ověřuje `runtime.source_finding_bridge` konvertory",
            "5. Importuje `runtime.nonfeed_candidate_ledger` a validuje konstanty",
            "",
            "## Re-run",
            "",
            "```bash",
            "PYTHONPATH=. python tools/probe_r0_nonfeed_reality_lock.py",
            "```",
        ]
    )
    return "\n".join(lines) + "\n"


# ── Output: JSON summary ────────────────────────────────────────────────────


def render_json(report: AuditReport) -> str:
    payload = {
        "verdict": report.verdict,
        "timestamp": report.timestamp,
        "total_checks": report.total_checks,
        "passed": report.passed,
        "failed": report.failed,
        "checks": [
            {
                "question": c.question,
                "name": c.name,
                "passed": c.passed,
                "detail": c.detail,
            }
            for c in report.checks
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


# ── Main entrypoint ─────────────────────────────────────────────────────────


def main() -> int:
    PROBE_DIR.mkdir(parents=True, exist_ok=True)
    report = run_audit()

    REPORT_MD.write_text(render_markdown(report), encoding="utf-8")
    SUMMARY_JSON.write_text(render_json(report), encoding="utf-8")

    print(f"R0 audit: {report.passed}/{report.total_checks} passed, verdict={report.verdict}")
    print(f"  → {REPORT_MD.relative_to(REPO_ROOT)}")
    print(f"  → {SUMMARY_JSON.relative_to(REPO_ROOT)}")
    return 0 if report.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
