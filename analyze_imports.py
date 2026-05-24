#!/usr/bin/env python3
"""Categorize broken imports into 4 groups: real-missing-pip, wrong-internal-path, permanently-shimmed, genuine-dead."""
import json
import os
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal")

def load_broken_imports():
    with open(ROOT / "broken_imports.json") as f:
        return json.load(f)

def load_shims():
    shims = {}
    shims_dir = ROOT / "_shims"
    if shims_dir.exists():
        for p in shims_dir.glob("*.py"):
            content = p.read_text()
            shims[p.stem] = content
    return shims

def load_pyproject():
    with open(ROOT / "pyproject.toml") as f:
        return f.read()

def categorize_item(item, shims, pyproject_content):
    missing = item["missing_module"]
    import_stmt = item["import_statement"]
    file_path = item.get("file", "")
    suggestion = item.get("suggestion", "")
    note = item.get("note", "")
    status = item.get("status", "")

    # Category 1: hledac.core.* -> should be hledac.universal.*
    if re.match(r"^hledac\.core\.", missing):
        return "wrong_internal_path", "hledac.core.* -> hledac.universal.*"

    # Category 2: hledac.cortex.*, hledac.speculative.*, hledac.tools.preserved_logic.* - these don't exist
    if re.match(r"^hledac\.(cortex|speculative_decoding|tools\.preserved_logic)\.", missing):
        return "permanently_shimmed", "module never existed - legacy/removed"

    # Category 3: hledac.universal.LAYERS.* - removed in F196A
    if "layers.build_temporal_priority_hints" in missing:
        return "permanently_shimmed", "removed in F196A sprint"

    # Category 4: hledac.universal.rl.marl_coordinator - deleted in F196A
    if "marl_coordinator" in missing:
        return "permanently_shimmed", "deleted in F196A sprint"

    # Category 5: hledac.universal.runtime.memory_watchdog - deleted in F196A
    if "memory_watchdog" in missing:
        return "permanently_shimmed", "deleted in F196A sprint"

    # Category 6: hledac.universal.AdaptiveSemaphore - pre-F195C dead code
    if missing == "hledac.universal.AdaptiveSemaphore":
        return "permanently_shimmed", "deleted before F195C"

    # Category 7: hledac.universal.FETCH_SEMAPHORE - removed in F196A
    if "FETCH_SEMAPHORE" in missing:
        return "permanently_shimmed", "removed in F196A sprint"

    # Category 8: hledac.universal.Orchestrator types - different names
    if "FullyAutonomousOrchestrator" in missing:
        return "wrong_internal_path", "now in autonomous_orchestrator.py"

    # Category 9: hledac.security.temporal_anonymizer -> hledac.universal.security.temporal_anonymizer
    if re.match(r"^hledac\.security\.", missing):
        return "wrong_internal_path", "now in hledac.universal.security.*"

    # Category 10: hledac.universal.utils.ActionResult - check if exists
    if "ActionResult" in missing:
        # Check if it exists elsewhere
        if (ROOT / "utils" / "async_helpers.py").exists():
            content = (ROOT / "utils" / "async_helpers.py").read_text()
            if "class ActionResult" in content or "ActionResult" in content:
                return "wrong_internal_path", "moved to utils.async_helpers"
        return "permanently_shimmed", "module removed"

    # Category 11: hledac.universal.TransportContext - check if exists
    if "TransportContext" in missing:
        if (ROOT / "transport" / "context.py").exists():
            return "wrong_internal_path", "moved to transport.context"
        return "permanently_shimmed", "module removed"

    # Category 12: hledac.universal.TransportResolver - check if exists
    if "TransportResolver" in missing:
        if (ROOT / "transport" / "resolver.py").exists():
            return "wrong_internal_path", "check transport/resolver.py"
        return "permanently_shimmed", "module removed"

    # Category 13: hledac.universal.adjust_fetch_workers - check if exists
    if "adjust_fetch_workers" in missing:
        if (ROOT / "utils" / "concurrency.py").exists():
            content = (ROOT / "utils" / "concurrency.py").read_text()
            if "adjust_fetch_workers" in content:
                return "wrong_internal_path", "moved to utils.concurrency"
        return "permanently_shimmed", "function removed"

    # Category 14: hledac.universal.get_uuid7_compat_status - check if exists
    if "get_uuid7_compat_status" in missing:
        if (ROOT / "utils" / "time_utils.py").exists():
            content = (ROOT / "utils" / "time_utils.py").read_text()
            if "uuid7" in content.lower():
                return "wrong_internal_path", "moved to utils.time_utils"
        return "permanently_shimmed", "function removed"

    # Category 15: hledac.universal.Orchestrator or similar - check autonomous_orchestrator
    if "Orchestrator" in missing:
        if (ROOT / "autonomous_orchestrator.py").exists():
            return "wrong_internal_path", "now in autonomous_orchestrator.py"

    # hledac.universal without module = top-level import
    if missing == "hledac.universal":
        return "permanently_shimmed", "top-level universal import - no such module"

    # hledac.universal.layers.* - removed in F196A
    if re.match(r"^hledac\.universal\.layers\.", missing):
        return "permanently_shimmed", "removed in F196A sprint"

    # hledac.universal.export.render_* functions - removed/renamed
    if re.match(r"^hledac\.universal\.export\.render_", missing):
        return "permanently_shimmed", "export functions renamed/removed"

    # hledac.universal.budget_manager - removed in F196A
    if "budget_manager" in missing:
        return "permanently_shimmed", "removed in F196A sprint"

    # hledac.universal.context_cache - removed in F196A
    if "context_cache" in missing:
        return "permanently_shimmed", "removed in F196A sprint"

    # hledac.universal.probe_f207j_* - sprint-specific, can be removed
    if re.match(r"^hledac\.universal\.probe_f207j_", missing):
        return "permanently_shimmed", "F207J sprint probe - no longer needed"

    # hledac.universal.transport.Transport - check if exists
    if missing == "hledac.universal.transport.Transport":
        if (ROOT / "transport" / "__init__.py").exists():
            return "wrong_internal_path", "Transport class in transport/__init__.py"
        return "permanently_shimmed", "Transport class removed"

    # hledac.universal.export.__all__ - not a real import
    if "__all__" in missing:
        return "permanently_shimmed", "__all__ is not an importable symbol"

    # hledac.common.*, hledac.neuromorphic.*, hledac.advanced_web.*, hledac.stealth_osint.*, etc. - don't exist
    non_universal_prefixes = ["common", "neuromorphic", "advanced_web", "stealth_osint",
                               "stealth_web_v2", "supreme", "ultra_context", "config",
                               "msqes", "runtime"]
    prefix = missing.split('.')[1] if '.' in missing else ""
    if prefix in non_universal_prefixes:
        return "permanently_shimmed", f"module never existed - {prefix}"

    # hledac.outdated.* - module never existed
    if re.match(r"^hledac\.outdated\.", missing):
        return "permanently_shimmed", "module never existed - outdated/"
    # hledac.advanced_rag.* - module never existed
    if re.match(r"^hledac\.advanced_rag\.", missing):
        return "permanently_shimmed", "module never existed - advanced_rag"
    # hledac.core.http.* -> fetching/public_fetcher or doesn't exist
    if re.match(r"^hledac\.core\.http\.", missing):
        # These reference hledac/core/http.py which is outside universal/
        return "permanently_shimmed", "hledac.core.http is outside universal/"
    # hledac.core.resilience.* -> these exist in hledac/core/resilience.py (outside universal/)
    if re.match(r"^hledac\.core\.resilience\.", missing):
        return "permanently_shimmed", "hledac.core.resilience is outside universal/"
    # hledac.core.unified_ai_orchestrator.* - never existed
    if re.match(r"^hledac\.core\.unified_ai_orchestrator\.", missing):
        return "permanently_shimmed", "module never existed"
    # hledac.core.mlx_embeddings.* -> these exist in hledac/core/mlx_embeddings.py (outside universal/)
    if re.match(r"^hledac\.core\.mlx_embeddings\.", missing):
        return "permanently_shimmed", "hledac.core.mlx_embeddings is outside universal/"
    # hledac.core.unified_ai_orchestrator.* - never existed
    if re.match(r"^hledac\.core\.unified_ai_orchestrator\.", missing):
        return "permanently_shimmed", "module never existed"
    # hledac.security.stealth_engine - in _shims already
    if "stealth_engine" in missing and missing.startswith("hledac.security."):
        return "permanently_shimmed", "in _shims/security_stealth_engine.py"
    # hledac.security.threat_intelligence - in _shims already
    if "threat_intelligence" in missing and missing.startswith("hledac.security."):
        return "permanently_shimmed", "in _shims/security_threat_intelligence.py"
    # hledac.security.zkp_research_engine - in _shims already
    if "zkp_research_engine" in missing and missing.startswith("hledac.security."):
        return "permanently_shimmed", "in _shims/security_zkp_research_engine.py"
    # knowledge/duckdb_store.py importing from old paths
    if "duckdb_store" in item.get("file", "") and re.match(r"^hledac\.(core|universal)\.knowledge\.", missing):
        return "wrong_internal_path", "knowledge/duckdb_store.py has old import paths"
    # tests/test_sprint_f193a_legacy_boundary.py - legacy test file
    if "test_sprint_f193a_legacy_boundary" in item.get("file", ""):
        return "permanently_shimmed", "legacy boundary test - can be deleted"
    # text module-level availability vars
    if re.match(r"^hledac\.universal\.text\.(UNICODE_ANALYZER_AVAILABLE|ENCODING_DETECTOR_AVAILABLE|HASH_IDENTIFIER_AVAILABLE)$", missing):
        return "permanently_shimmed", "module-level bool removed/renamed"
    # orchestrator._ResearchManager, _SecurityManager - removed
    if re.match(r"^hledac\.universal\.orchestrator\._(Research|Security)Manager$", missing):
        return "permanently_shimmed", "removed in F196A sprint"
    # knowledge.ContextGraph, knowledge.RAGEngine - in _shims or don't exist
    if missing in ["hledac.universal.knowledge.ContextGraph", "hledac.universal.knowledge.RAGEngine"]:
        return "permanently_shimmed", "knowledge graph classes not in this version"

    # test files importing non-existent coordinators
    if missing in ["hledac.universal.coordinators.UniversalExecutionCoordinator",
                   "hledac.universal.coordinators.UniversalSecurityCoordinator",
                   "hledac.universal.coordinators.UniversalResearchCoordinator"]:
        return "permanently_shimmed", "coordinators never existed - legacy test"

    # fetch_loop.SELECTOLAX_AVAILABLE - removed
    if "SELECTOLAX_AVAILABLE" in missing:
        return "permanently_shimmed", "removed - no longer using selectolax"

    # hypothesis.BetaBinomial - moved to scipy
    if missing == "hledac.universal.hypothesis.BetaBinomial":
        return "permanently_shimmed", "use scipy.stats.beta instead"

    # transport.InMemoryTransport - test helper, doesn't exist
    if missing == "hledac.universal.transport.InMemoryTransport":
        return "permanently_shimmed", "test transport never implemented"

    # autonomy.agent_meta_optimizer - never existed
    if "agent_meta_optimizer" in missing:
        return "permanently_shimmed", "module never existed"

    # security.encrypt_aes_gcm / decrypt_aes_gcm - check if in quantum_safe
    if missing in ["hledac.universal.security.encrypt_aes_gcm", "hledac.universal.security.decrypt_aes_gcm"]:
        if (ROOT / "security" / "quantum_safe.py").exists():
            content = (ROOT / "security" / "quantum_safe.py").read_text()
            if "aes_gcm" in content.lower():
                return "wrong_internal_path", "check security/quantum_safe.py"
        return "permanently_shimmed", "function removed"

    # intelligence.path_discovery.ShadowWalkerAlgorithm - never existed
    if "ShadowWalkerAlgorithm" in missing:
        return "permanently_shimmed", "algorithm never implemented"

    # brain.modernbert_engine.ModernBertEngine - we use Hermes/MLX
    if "ModernBertEngine" in missing:
        return "permanently_shimmed", "we use Hermes3 via MLX, not ModernBERT"

    # runtime.intelligence_dispatcher - deleted in F196A
    if "intelligence_dispatcher" in missing:
        return "permanently_shimmed", "deleted in F196A sprint"

    # runtime.runtime_authority_manifest - never existed
    if "runtime_authority_manifest" in missing:
        return "permanently_shimmed", "manifest never existed"

    # knowledge.evidence_log.EvidencePacketStorage - never existed
    if "EvidencePacketStorage" in missing:
        return "permanently_shimmed", "storage never implemented"

    # Default: permanently shimmed (status=optional with note)
    if status == "optional" and note:
        return "permanently_shimmed", note

    return "unknown", "needs manual review"

def analyze():
    data = load_broken_imports()
    shims = load_shims()
    pyproject_content = load_pyproject()

    bi = data["broken_imports"]
    categories = defaultdict(list)

    for item in bi:
        cat, reason = categorize_item(item, shims, pyproject_content)
        categories[cat].append({**item, "category_reason": reason})

    # Summary
    print("=" * 60)
    print("IMPORT CATEGORIZATION REPORT")
    print("=" * 60)
    print(f"Total broken imports: {len(bi)}")
    print(f"Previous size: 79KB -> Current: 91KB (GROWTH: +12KB)")
    print()
    print("BY CATEGORY:")
    for cat, items in sorted(categories.items(), key=lambda x: -len(x[1])):
        print(f"  {cat}: {len(items)}")

    print("\n" + "=" * 60)
    print("CATEGORY DETAILS")
    print("=" * 60)

    for cat, items in sorted(categories.items(), key=lambda x: -len(x[1])):
        print(f"\n### {cat} ({len(items)} items) ###")
        # Group by missing_module
        by_module = defaultdict(list)
        for item in items:
            by_module[item["missing_module"]].append(item)

        for mod, mod_items in sorted(by_module.items(), key=lambda x: -len(x[1]))[:15]:
            print(f"  {len(mod_items)}x {mod}")

    # Generate JSON output
    output = {
        "categories": {cat: items for cat, items in categories.items()},
        "summary": {cat: len(items) for cat, items in categories.items()}
    }

    with open(ROOT / "import_categorization.json", "w") as f:
        json.dump(output, f, indent=2)

    print(f"\nFull categorization saved to import_categorization.json")

    # Top missing pip packages (hledac.* that should be real deps)
    print("\n" + "=" * 60)
    print("TOP MISSING INTERNAL IMPORTS (wrong paths)")
    print("=" * 60)
    wrong_path = categories.get("wrong_internal_path", [])
    for item in wrong_path[:20]:
        print(f"  {item['file']}:{item['line']} -> {item['missing_module']}")

if __name__ == "__main__":
    analyze()