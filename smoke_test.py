#!/usr/bin/env python3
"""
Smoke Test Runner — hledac/new-hledac
Validates all P1-P6 import fixes are working.
Run with: uv run python smoke_test.py
"""
from __future__ import annotations

import sys
from typing import NamedTuple

# ── Bootstrap ─────────────────────────────────────────────────────────────────
# The editable installer creates hledac/ as a namespace package whose __path__
# only knows universal/hledac/. Sibling directories are invisible.
#
# Solution: call `ensure_namespace_paths()` from hledac._namespace_bootstrap,
# which is the canonical, idempotent implementation. The first call wires
# the namespace; subsequent calls are no-ops. The function never raises.
# ─────────────────────────────────────────────────────────────────────────────
from hledac._namespace_bootstrap import ensure_namespace_paths

ensure_namespace_paths()


class TestResult(NamedTuple):
    name: str
    passed: bool
    error: str = ""

results: list[TestResult] = []

def test(name: str, code: str) -> None:
    try:
        exec(code, {})  # noqa: S102  # hardcoded test strings, not user input
        results.append(TestResult(name, True))
        print(f"  ✅ {name}")
    except Exception as exc:
        results.append(TestResult(name, False, str(exc)))
        print(f"  ❌ {name}")
        print(f"     {type(exc).__name__}: {exc}")


print("\n=== SMOKE TESTS: P1 — Universal Namespace ===")
test("Transport enum", "from hledac.universal import Transport")
test("GraphRAGOrchestrator", "from hledac.universal.knowledge import GraphRAGOrchestrator")
test("adjust_fetch_workers", "from hledac.universal import adjust_fetch_workers")
test("FullyAutonomousOrchestrator", "from hledac.universal import FullyAutonomousOrchestrator")

print("\n=== SMOKE TESTS: P2 — Security Namespace ===")
test("hledac.security shim", "from hledac.security import StealthEngine, TemporalAnonymizer, ZeroAttributionEngine, KeyManager")  # noqa: E501
test("security_coordinator import", "from hledac.universal.coordinators.security_coordinator import SecurityCoordinator")  # noqa: E501

print("\n=== SMOKE TESTS: P3 — research_coordinator bridges ===")
test("UnifiedAIOrchestrator bridge (import)", "from hledac.universal._shims.core_unified_ai_orchestrator import UnifiedAIOrchestrator")  # noqa: E501
test("UnifiedAIOrchestrator instantiation", "from hledac.universal._shims.core_unified_ai_orchestrator import UnifiedAIOrchestrator; u = UnifiedAIOrchestrator()")  # noqa: E501
test("RAGOrchestrator import", "from hledac.universal.advanced_rag.rag_orchestrator import RAGOrchestrator")
test("RAGOrchestrator instantiation", "from hledac.universal.advanced_rag.rag_orchestrator import RAGOrchestrator; r = RAGOrchestrator()")  # noqa: E501
test("research_coordinator import", "from hledac.universal.coordinators.research_coordinator import ResearchCoordinator")  # noqa: E501
test("ResearchCoordinator instantiation", "from hledac.universal.coordinators.research_coordinator import ResearchCoordinator; rc = ResearchCoordinator()")  # noqa: E501

print("\n=== SMOKE TESTS: P4 — Core redirects ===")
test("mlx_embeddings redirect", "from hledac.core import mlx_embeddings")
test("Watchdog shim", "from hledac.core import watchdog")

print("\n=== SMOKE TESTS: P5 — advanced_web ===")
test("StealthBrowser import", "from hledac.advanced_web.stealth_browser import StealthBrowser")
test("AutomationOrchestrator import", "from hledac.advanced_web.automation_orchestrator import AutomationOrchestrator")
test("StealthBrowser instantiation", "from hledac.advanced_web.stealth_browser import StealthBrowser; sb = StealthBrowser()")  # noqa: E501

print("\n=== SMOKE TESTS: P6 — T3 Strategic stubs ===")
test("ThreatIntelligence import", "from hledac.security import ThreatIntelligence")
test("ZKPResearchEngine import", "from hledac.security import ZKPResearchEngine")
test("QuantumResistantCrypto import", "from hledac.security import QuantumResistantCrypto")

print("\n=== SUMMARY ===")
passed = sum(1 for r in results if r.passed)
total = len(results)
print(f"\n{passed}/{total} tests passing")

if passed < total:
    print("\nFailed tests:")
    for r in results:
        if not r.passed:
            print(f"  ❌ {r.name}: {r.error}")
    sys.exit(1)
else:
    print("\n🎉 ALL SMOKE TESTS PASSED")
    sys.exit(0)
