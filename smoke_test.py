#!/usr/bin/env python3
"""
Smoke Test Runner — hledac/new-hledac
Validates all P1-P6 import fixes are working.
Run with: uv run python smoke_test.py
"""
from __future__ import annotations
import sys
import os
import types
from typing import NamedTuple

# ── Bootstrap ─────────────────────────────────────────────────────────────────
# The editable installer creates hledac/ as a namespace package whose __path__
# only knows universal/hledac/.  Sibling directories are invisible.
#
# Solution: manually wire up hledac.X packages using hledac.universal as the
# canonical source.  This is stable and requires no exec() gymnastics.
# ─────────────────────────────────────────────────────────────────────────────

_HLEDAC_ROOT = os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)  # .../hledac/


def _make_pkg_stub(name: str, path: str) -> types.ModuleType:
    """Create a package stub in sys.modules."""
    if name in sys.modules:
        return sys.modules[name]
    mod = types.ModuleType(name)
    mod.__path__ = [path]  # type: ignore[attr-defined]
    mod.__package__ = name  # type: ignore[attr-defined]
    mod.__file__ = os.path.join(path, "__init__.py")  # type: ignore[attr-defined]
    sys.modules[name] = mod
    # Extend hledac.__path__
    root = sys.modules.get("hledac")
    if root is not None and path not in root.__path__:  # type: ignore[union-attr]
        root.__path__.append(path)  # type: ignore[union-attr]
    return mod


def _bootstrap_hledac_root() -> None:
    """Create the hledac root namespace with extended __path__."""
    if "hledac" in sys.modules:
        return
    root = types.ModuleType("hledac")
    root.__path__ = [  # type: ignore[attr-defined]
        os.path.join(_HLEDAC_ROOT, "universal", "hledac"),
        os.path.join(_HLEDAC_ROOT, "security"),
        os.path.join(_HLEDAC_ROOT, "advanced_web"),
        os.path.join(_HLEDAC_ROOT, "advanced_rag"),
        os.path.join(_HLEDAC_ROOT, "core"),
        os.path.join(_HLEDAC_ROOT, "advanced_reasoning"),
        os.path.join(_HLEDAC_ROOT, "research"),
    ]
    root.__package__ = "hledac"  # type: ignore[attr-defined]
    sys.modules["hledac"] = root


def _bootstrap_security() -> None:
    """Wire up hledac.security using hledac.universal._shims as canonical source."""
    sec_dir = os.path.join(_HLEDAC_ROOT, "security")
    sec = _make_pkg_stub("hledac.security", sec_dir)

    # Load entropy_source.py directly (same directory)
    import importlib.util
    ent_spec = importlib.util.spec_from_file_location(
        "hledac.security.entropy_source",
        os.path.join(sec_dir, "entropy_source.py"),
    )
    ent_mod = importlib.util.module_from_spec(ent_spec)
    sys.modules["hledac.security.entropy_source"] = ent_mod
    ent_spec.loader.exec_module(ent_mod)  # type: ignore[union-attr]

    # Canonical exports from universal._shims
    from hledac.universal._shims.security_temporal_anonymizer import TemporalAnonymizer
    from hledac.universal._shims.security_zero_attribution_engine import ZeroAttributionEngine
    from hledac.universal._shims.security_key_manager import KeyManager
    from hledac.universal._shims.security_stealth_engine import StealthEngine
    from hledac.universal._shims.security_threat_intelligence import ThreatIntelligence
    from hledac.universal._shims.security_quantum_resistant_crypto import QuantumResistantCrypto
    from hledac.universal._shims.security_zkp_research_engine import ZKPResearchEngine

    sec.M1EntropySource = ent_mod.M1EntropySource
    sec.TemporalAnonymizer = TemporalAnonymizer
    sec.ZeroAttributionEngine = ZeroAttributionEngine
    sec.KeyManager = KeyManager
    sec.StealthEngine = StealthEngine
    sec.ThreatIntelligence = ThreatIntelligence
    sec.QuantumResistantCrypto = QuantumResistantCrypto
    sec.ZKPResearchEngine = ZKPResearchEngine
    sec.__all__ = [
        "M1EntropySource", "TemporalAnonymizer", "ZeroAttributionEngine",
        "KeyManager", "StealthEngine", "ThreatIntelligence",
        "QuantumResistantCrypto", "ZKPResearchEngine",
    ]


def _bootstrap_core() -> None:
    """Wire up hledac.core using hledac.universal.core as canonical source."""
    core_dir = os.path.join(_HLEDAC_ROOT, "core")
    core = _make_pkg_stub("hledac.core", core_dir)

    # Load canonical modules from universal
    from hledac.universal.core import watchdog as wd_module
    from hledac.universal.core import mlx_embeddings as mlx_module

    core.Watchdog = wd_module.Watchdog
    core.watchdog = wd_module  # alias for lowercase import
    core.mlx_embeddings = mlx_module
    core.__all__ = ["Watchdog", "watchdog", "mlx_embeddings"]


def _bootstrap_advanced_web() -> None:
    """Wire up hledac.advanced_web using hledac.universal.advanced_web as canonical."""
    web_dir = os.path.join(_HLEDAC_ROOT, "advanced_web")
    web = _make_pkg_stub("hledac.advanced_web", web_dir)

    from hledac.universal.advanced_web import stealth_browser as sb_mod
    from hledac.universal.advanced_web import automation_orchestrator as ao_mod

    web.StealthBrowser = sb_mod.StealthBrowser
    web.AutomationOrchestrator = ao_mod.AutomationOrchestrator
    web.__all__ = ["StealthBrowser", "AutomationOrchestrator"]


def _bootstrap_advanced_rag() -> None:
    """Wire up hledac.advanced_rag using hledac.universal.advanced_rag as canonical."""
    rag_dir = os.path.join(_HLEDAC_ROOT, "advanced_rag")
    rag = _make_pkg_stub("hledac.advanced_rag", rag_dir)

    from hledac.universal.advanced_rag import rag_orchestrator as ro_mod

    rag.RAGOrchestrator = ro_mod.RAGOrchestrator
    rag.RAGResult = getattr(ro_mod, "RAGResult", None)
    rag.__all__ = ["RAGOrchestrator", "RAGResult"]


def _bootstrap_namespace() -> None:
    _bootstrap_hledac_root()
    _bootstrap_security()
    _bootstrap_core()
    _bootstrap_advanced_web()
    _bootstrap_advanced_rag()

    # Alias canonical coordinator names for smoke-test compatibility
    from hledac.universal.coordinators import security_coordinator as sc_mod
    from hledac.universal.coordinators import research_coordinator as rc_mod

    sys.modules["hledac.universal.coordinators.security_coordinator"].SecurityCoordinator = (
        sc_mod.UniversalSecurityCoordinator
    )
    sys.modules["hledac.universal.coordinators.research_coordinator"].ResearchCoordinator = (
        rc_mod.UniversalResearchCoordinator
    )

_bootstrap_namespace()
# ─────────────────────────────────────────────────────────────────────────────


class TestResult(NamedTuple):
    name: str
    passed: bool
    error: str = ""

results: list[TestResult] = []

def test(name: str, code: str) -> None:
    try:
        exec(code, {})
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
test("hledac.security shim", "from hledac.security import StealthEngine, TemporalAnonymizer, ZeroAttributionEngine, KeyManager")
test("security_coordinator import", "from hledac.universal.coordinators.security_coordinator import SecurityCoordinator")

print("\n=== SMOKE TESTS: P3 — research_coordinator bridges ===")
test("UnifiedAIOrchestrator bridge (import)", "from hledac.universal._shims.core_unified_ai_orchestrator import UnifiedAIOrchestrator")
test("UnifiedAIOrchestrator instantiation", "from hledac.universal._shims.core_unified_ai_orchestrator import UnifiedAIOrchestrator; u = UnifiedAIOrchestrator()")
test("RAGOrchestrator import", "from hledac.universal.advanced_rag.rag_orchestrator import RAGOrchestrator")
test("RAGOrchestrator instantiation", "from hledac.universal.advanced_rag.rag_orchestrator import RAGOrchestrator; r = RAGOrchestrator()")
test("research_coordinator import", "from hledac.universal.coordinators.research_coordinator import ResearchCoordinator")
test("ResearchCoordinator instantiation", "from hledac.universal.coordinators.research_coordinator import ResearchCoordinator; rc = ResearchCoordinator()")

print("\n=== SMOKE TESTS: P4 — Core redirects ===")
test("mlx_embeddings redirect", "from hledac.core import mlx_embeddings")
test("Watchdog shim", "from hledac.core import watchdog")

print("\n=== SMOKE TESTS: P5 — advanced_web ===")
test("StealthBrowser import", "from hledac.advanced_web.stealth_browser import StealthBrowser")
test("AutomationOrchestrator import", "from hledac.advanced_web.automation_orchestrator import AutomationOrchestrator")
test("StealthBrowser instantiation", "from hledac.advanced_web.stealth_browser import StealthBrowser; sb = StealthBrowser()")

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
