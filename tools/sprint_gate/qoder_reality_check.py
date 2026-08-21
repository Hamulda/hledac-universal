"""
SPRINT F206AG-v2: Qoder Repowiki Reality Matrix

Scans the Qoder repowiki documentation and compares claims against



actual repository wiring. Produces JSON reality matrix + Markdown report.

HERMETIC: No MLX imports, no network calls, no live sprint execution,
no helper subprocess spawn, no production code modification.
"""

import json
import re
from collections import defaultdict
from dataclasses import asdict, field
from operator import attrgetter
from pathlib import Path

from compat.msgspec_gc_compat import Struct

REPO_ROOT = Path("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal")
QODER_ROOT_DEFAULT = REPO_ROOT / ".qoder/repowiki/en/content"
OUTPUT_JSON_DEFAULT = REPO_ROOT / "probe_qoder_reality/qoder_reality_matrix.json"
OUTPUT_MD_DEFAULT = REPO_ROOT / "probe_qoder_reality/REPORT_QODER_REALITY_MATRIX.md"
VERDICT_CANONICAL_OWNER = "CANONICAL_OWNER"
VERDICT_ACTIVE_RUNTIME = "ACTIVE_RUNTIME"
VERDICT_ACTIVE_PIPELINE = "ACTIVE_PIPELINE"
VERDICT_ACTIVE_SIDECAR = "ACTIVE_SIDECAR"
VERDICT_ACTIVE_DIAGNOSTIC = "ACTIVE_DIAGNOSTIC"
VERDICT_ACTIVE_SUPPORT = "ACTIVE_SUPPORT"
VERDICT_ACTIVE_CAPABILITY = "ACTIVE_CAPABILITY"
VERDICT_ACTIVE_ENTRYPOINT = "ACTIVE_ENTRYPOINT"
VERDICT_PATH_AUTHORITY = "PATH_AUTHORITY"
VERDICT_SECURITY_CRITICAL = "SECURITY_CRITICAL"
VERDICT_STORAGE_AUTHORITY = "STORAGE_AUTHORITY"
VERDICT_TRANSPORT_AUTHORITY = "TRANSPORT_AUTHORITY"
VERDICT_DONOR = "DONOR"
VERDICT_DONOR_OR_OPTIONAL = "DONOR_OR_OPTIONAL"
VERDICT_LEGACY = "LEGACY"
VERDICT_DEPRECATED = "DEPRECATED"
VERDICT_TEST_ONLY = "TEST_ONLY"
VERDICT_DEAD_OR_UNWIRED = "DEAD_OR_UNWIRED"
VERDICT_MISSING_DOC_TARGET = "MISSING_DOC_TARGET"
VERDICT_UNKNOWN_NEEDS_REVIEW = "UNKNOWN_NEEDS_REVIEW"
CANONICAL_OWNER_PATHS = {"core/__main__.py"}
ACTIVE_SUPPORT_PATHS = {"core/resource_governor.py", "core/mlx_embeddings.py", "project_types.py", "config.py"}
ACTIVE_ENTRYPOINT_PATHS = {"__main__.py"}
PATH_AUTHORITY_PATHS = {"paths.py"}
ACTIVE_CAPABILITY_PATHS = {"tools/qoder_reality_check.py", "graph/quantum_pathfinder.py"}
ACTIVE_RUNTIME_PATHS = {
    "runtime/sprint_scheduler.py",
    "runtime/sprint_lifecycle.py",
    "runtime/sprint_lifecycle_runner.py",
}
ACTIVE_PIPELINE_PATHS = {"pipeline/live_public_pipeline.py", "pipeline/live_feed_pipeline.py"}
ACTIVE_SIDECAR_PATHS = {"runtime/sidecar_bus.py", "runtime/sidecar_dispatcher.py", "runtime/sprint_advisory_runner.py"}
STORAGE_AUTHORITY_PATHS = {"knowledge/duckdb_store.py", "knowledge/semantic_store.py"}
TRANSPORT_AUTHORITY_PATHS = {
    "transport/circuit_breaker.py",
    "transport/transport_resolver.py",
    "transport/tor_transport.py",
}
SECURITY_CRITICAL_PATHS = {"security/pq_export_encryption.py", "security/pq_export_encryption_swift.py"}
ACTIVE_DIAGNOSTIC_PATHS = {
    "runtime/shadow_inputs.py",
    "runtime/shadow_parity.py",
    "runtime/shadow_pre_decision.py",
    "runtime/shadow_outputs.py",
    "runtime/shadow_scores.py",
}
LEGACY_PATHS = {"legacy/autonomous_orchestrator.py", "legacy/persistent_layer.py"}
DEPRECATED_PATHS = {"autonomous_orchestrator.py"}
TEST_ONLY_PATHS = {"tests/", "probe_", "benchmarks/"}
DEAD_OR_UNWIRED_PATHS = {"layers/layer_manager.py", "orchestrator_integration.py", "runtime/windup_engine.py"}
DONOR_OR_OPTIONAL_PATHS = {
    "graph/quantum_pathfinder.py",
    "layers/",
    "planning/",
    "policy/",
    "loops/",
    "infrastructure/",
    "orchestrator_integration.py",
}
HAS_CANONICAL_WRITE = {"knowledge/duckdb_store.py", "knowledge/graph_service.py", "export/sprint_exporter.py"}
HAS_NETWORK_PATH = {
    "fetching/public_fetcher.py",
    "transport/tor_transport.py",
    "transport/httpx_client.py",
    "intelligence/ct_log_client.py",
    "coordinators/fetch_coordinator.py",
}
HAS_MLX_IMPORT = {
    "brain/hermes3_engine.py",
    "brain/model_lifecycle.py",
    "brain/model_manager.py",
    "utils/mlx_cache.py",
    "utils/mlx_memory.py",
}
HAS_SUBPROCESS_SPAWN = {"tools/secure_enclave_helper/__init__.py"}
HAS_SECRET_EXPORT_RISK = {
    "security/pq_export_encryption.py",
    "security/pq_export_encryption_swift.py",
    "security/key_manager.py",
}
PRIVATE_HELPER_PATHS = {}
PRODUCTION_CALL_PATHS = {
    "runtime/sprint_scheduler.py": {"core/__main__.py", "core/__main__.py:run_sprint"},
    "runtime/sprint_lifecycle.py": {"core/__main__.py"},
    "runtime/sprint_lifecycle_runner.py": {"runtime/sprint_scheduler.py"},
    "runtime/sprint_advisory_runner.py": {"runtime/sprint_scheduler.py"},
    "runtime/sidecar_bus.py": {"runtime/sprint_scheduler.py"},
    "runtime/sidecar_dispatcher.py": {"runtime/sprint_scheduler.py"},
    "knowledge/duckdb_store.py": {"core/__main__.py"},
    "knowledge/semantic_store.py": {"core/__main__.py"},
    "export/sprint_exporter.py": {"core/__main__.py"},
    "transport/circuit_breaker.py": {"runtime/sprint_scheduler.py", "coordinators/fetch_coordinator.py"},
    "transport/transport_resolver.py": {"pipeline/live_public_pipeline.py"},
    "transport/tor_transport.py": {"core/__main__.py"},
}


class ModuleReality(Struct):
    path: str
    exists: bool
    verdict: str
    qoder_docs: list[str] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)
    risks: list[str] = field(default_factory=list)
    recommended_action: str = ""


class Overclaim(Struct, frozen=True):
    doc_path: str
    claim: str
    referenced_path: str
    actual_verdict: str
    severity: str
    affected_modules_count: int = 1
    examples: list[str] = field(default_factory=list)
    group_key: str = ""


class HighRiskGap(Struct, frozen=True):
    gap_type: str
    description: str
    affected_paths: list[str]
    recommended_sprint: str
    severity: str


def extract_file_refs(md_path: Path) -> dict[str, list[str]]:
    """Extract all file references from a markdown document.

    Returns:
        Dict with keys 'file_links', 'md_links', 'code_paths', 'module_names'
    """
    try:
        content = md_path.read_text()
    except Exception:
        return {"file_links": [], "md_links": [], "code_paths": [], "module_names": []}
    raw_file_links = re.findall("file://([^\\)]+)", content)
    KNOWN_ROOT_FILES = {
        "__main__.py",
        "config.py",
        "paths.py",
        "requirements.txt",
        "requirements-optional.txt",
        "pytest.ini",
        "project_types.py",
        "autonomous_orchestrator.py",
    }
    file_links = []
    for ref in raw_file_links:
        stripped = ref.split("#")[0].strip()
        if "/" in stripped or stripped.endswith((".py", ".md", ".txt", ".json", ".stix")):
            file_links.append(ref)
        elif any(stripped.startswith(k) or stripped == k for k in KNOWN_ROOT_FILES):
            file_links.append(ref)
    md_links = [m.group(2) for m in re.finditer("\\[([^\\]]+)\\]\\(([^)]+\\.py)(?:#[^\\)]+)?\\)", content)]
    code_path_patterns = [
        "(?:^|\\s)(runtime/[\\w_/\\-\\.]+)",
        "(?:^|\\s)(pipeline/[\\w_/\\-\\.]+)",
        "(?:^|\\s)(brain/[\\w_/\\-\\.]+)",
        "(?:^|\\s)(knowledge/[\\w_/\\-\\.]+)",
        "(?:^|\\s)(security/[\\w_/\\-\\.]+)",
        "(?:^|\\s)(transport/[\\w_/\\-\\.]+)",
        "(?:^|\\s)(coordinators/[\\w_/\\-\\.]+)",
        "(?:^|\\s)(utils/[\\w_/\\-\\.]+)",
        "(?:^|\\s)(export/[\\w_/\\-\\.]+)",
        "(?:^|\\s)(multimodal/[\\w_/\\-\\.]+)",
        "(?:^|\\s)(intelligence/[\\w_/\\-\\.]+)",
        "(?:^|\\s)(network/[\\w_/\\-\\.]+)",
        "(?:^|\\s)(discovery/[\\w_/\\-\\.]+)",
        "(?:^|\\s)(fetching/[\\w_/\\-\\.]+)",
        "(?:^|\\s)(graph/[\\w_/\\-\\.]+)",
        "(?:^|\\s)(legacy/[\\w_/\\-\\.]+)",
        "(?:^|\\s)(stealth/[\\w_/\\-\\.]+)",
        "(?:^|\\s)(patterns/[\\w_/\\-\\.]+)",
        "(?:^|\\s)(layers/[\\w_/\\-\\.]+)",
        "(?:^|\\s)(tools/[\\w_/\\-\\.]+)",
        "(?:^|\\s)(core/[\\w_/\\-\\.]+)",
        "(?:^|\\s)(monitoring/[\\w_/\\-\\.]+)",
        "(?:^|\\s)(runtime\\.shadow_[\\w_]+)",
        "(?:^|\\s)(memory/[\\w_/\\-\\.]+)",
    ]
    code_paths = []
    for pat in code_path_patterns:
        code_paths.extend(re.findall(pat, content))
    module_names = re.findall(
        "\\b([A-Z][a-zA-Z0-9]+(?:Engine|Manager|Store|Service|Adapter|Coordinator|Runner|Helper|Bus|Dispatcher|Exporter|Client|Layer|Resolver|Breaker))\\b",
        content,
    )
    normalized_file_links = []
    for ref in file_links:
        ref = re.sub("#L\\d+-L\\d+$", "", ref)
        ref = re.sub("#L\\d+$", "", ref)
        ref = re.sub("^hledac/universal/", "", ref)
        ref = ref.lstrip("/")
        if ref.startswith("file://"):
            ref = ref[7:]
        normalized_file_links.append(ref)
    return {
        "file_links": normalized_file_links,
        "md_links": md_links,
        "code_paths": list(set(code_paths)),
        "module_names": list(set(module_names)),
    }


def normalize_path(ref: str) -> str:
    """Normalize a reference string to a repo-relative path."""
    ref = re.sub("#L\\d+-L\\d+$", "", ref)
    ref = re.sub("#L\\d+$", "", ref)
    ref = re.sub("^hledac/universal/", "", ref)
    ref = ref.lstrip("/")
    if ref.startswith("file://"):
        ref = ref[7:]
    return ref


# Exact-match verdicts: path → verdict (checked first)
_EXACT_PATH_VERDICTS: dict[str, str] = {
    # Priority exact matches (exact paths only, not prefixes)
    **dict.fromkeys(CANONICAL_OWNER_PATHS, VERDICT_CANONICAL_OWNER),
    **dict.fromkeys(ACTIVE_SUPPORT_PATHS, VERDICT_ACTIVE_SUPPORT),
    **dict.fromkeys(ACTIVE_CAPABILITY_PATHS, VERDICT_ACTIVE_CAPABILITY),
    **dict.fromkeys(ACTIVE_ENTRYPOINT_PATHS, VERDICT_ACTIVE_ENTRYPOINT),
    **dict.fromkeys(PATH_AUTHORITY_PATHS, VERDICT_PATH_AUTHORITY),
    **dict.fromkeys(ACTIVE_RUNTIME_PATHS, VERDICT_ACTIVE_RUNTIME),
    **dict.fromkeys(ACTIVE_PIPELINE_PATHS, VERDICT_ACTIVE_PIPELINE),
    **dict.fromkeys(ACTIVE_SIDECAR_PATHS, VERDICT_ACTIVE_SIDECAR),
    **dict.fromkeys(ACTIVE_DIAGNOSTIC_PATHS, VERDICT_ACTIVE_DIAGNOSTIC),
    **dict.fromkeys(STORAGE_AUTHORITY_PATHS, VERDICT_STORAGE_AUTHORITY),
    **dict.fromkeys(TRANSPORT_AUTHORITY_PATHS, VERDICT_TRANSPORT_AUTHORITY),
    **dict.fromkeys(SECURITY_CRITICAL_PATHS, VERDICT_SECURITY_CRITICAL),
    **dict.fromkeys(PRIVATE_HELPER_PATHS, VERDICT_SECURITY_CRITICAL),
    **dict.fromkeys(LEGACY_PATHS, VERDICT_LEGACY),
    **dict.fromkeys(DEPRECATED_PATHS, VERDICT_DEPRECATED),
    **dict.fromkeys(DEAD_OR_UNWIRED_PATHS, VERDICT_DEAD_OR_UNWIRED),
    # Special test files (exact matches)
    "run_baseline.py": VERDICT_TEST_ONLY,
    "run_comprehensive_tests.py": VERDICT_TEST_ONLY,
    "__main__.py": VERDICT_ACTIVE_ENTRYPOINT,
    # Deprecated project files
    "config.py": VERDICT_DEPRECATED,
    "requirements.txt": VERDICT_DEPRECATED,
    "requirements-optional.txt": VERDICT_DEPRECATED,
    "pytest.ini": VERDICT_DEPRECATED,
    "project_types.py": VERDICT_DEPRECATED,
    "capabilities.py": VERDICT_DEPRECATED,
    "smoke_runner.py": VERDICT_DEPRECATED,
    "tool_registry.py": VERDICT_DEPRECATED,
    "metrics_registry.py": VERDICT_DEPRECATED,
    "embedding_pipeline.py": VERDICT_DEPRECATED,
    "semantic_deduplicator.py": VERDICT_DEPRECATED,
    "deep_probe.py": VERDICT_DEPRECATED,
    "enhanced_research.py": VERDICT_DEPRECATED,
    "research_context.py": VERDICT_DEPRECATED,
    "tot_integration.py": VERDICT_DEPRECATED,
    "GHOST_INVARIANTS.md": VERDICT_DEPRECATED,
    "LONGTERM_PLAN.md": VERDICT_DEPRECATED,
    "REAL_ARCHITECTURE.md": VERDICT_DEPRECATED,
}

# Prefix-based verdicts for test paths (checked before other prefixes)
_TEST_PREFIX_VERDICTS: tuple[str, ...] = tuple(TEST_ONLY_PATHS)

# Prefix-based verdicts: (prefixes_tuple, verdict) — checked in order
_PREFIX_VERDICTS: list[tuple[tuple[str, ...], str]] = [
    (("brain/.",), VERDICT_TEST_ONLY),  # dotfile in brain
    (("coordinators/",), VERDICT_ACTIVE_SIDECAR),
    (("intelligence/",), VERDICT_ACTIVE_CAPABILITY),
    (("knowledge/",), VERDICT_STORAGE_AUTHORITY),
    (("transport/",), VERDICT_TRANSPORT_AUTHORITY),
    (("export/",), VERDICT_STORAGE_AUTHORITY),
    (("utils/",), VERDICT_ACTIVE_CAPABILITY),
    (("discovery/", "network/"), VERDICT_ACTIVE_CAPABILITY),
    (("fetching/",), VERDICT_ACTIVE_CAPABILITY),
    (("multimodal/",), VERDICT_ACTIVE_CAPABILITY),
    (("stealth/",), VERDICT_SECURITY_CRITICAL),
    (("forensics/",), VERDICT_ACTIVE_CAPABILITY),
    (("monitoring/",), VERDICT_ACTIVE_SIDECAR),
    (("patterns/",), VERDICT_ACTIVE_CAPABILITY),
    (("graph/",), VERDICT_STORAGE_AUTHORITY),
    (("cache/",), VERDICT_ACTIVE_RUNTIME),
    (("research/", "deep_research/"), VERDICT_ACTIVE_RUNTIME),
    (("orchestrator/", "execution/"), VERDICT_LEGACY),
    (("loops/",), VERDICT_DONOR_OR_OPTIONAL),
    (("memory/",), VERDICT_ACTIVE_RUNTIME),
    (("planning/", "policy/"), VERDICT_DONOR),
    (("legacy/",), VERDICT_LEGACY),
    (("infrastructure/",), VERDICT_DONOR_OR_OPTIONAL),
    (("tools/",), VERDICT_ACTIVE_SIDECAR),  # special: needs _classify_tools helper
    (("brain/",), VERDICT_ACTIVE_CAPABILITY),  # brain/* falls through here
]


def _classify_test_only(path: str) -> str | None:
    """Check special test-only patterns."""
    if "brain/." in path or path.endswith("/."):
        return VERDICT_TEST_ONLY
    if "benchmark_results" in path or "benchmark_results/" in path:
        return VERDICT_TEST_ONLY
    if ".full-review" in path:
        return VERDICT_TEST_ONLY
    if path.endswith(".stix.json"):
        return VERDICT_TEST_ONLY
    return None


def _classify_runtime(path: str) -> str | None:
    """Classify runtime/ prefixed paths."""
    if path.startswith("runtime/shadow_"):
        return VERDICT_ACTIVE_DIAGNOSTIC
    if path in ACTIVE_RUNTIME_PATHS:
        return VERDICT_ACTIVE_RUNTIME
    return VERDICT_ACTIVE_SIDECAR


def _classify_pipeline(path: str) -> str:
    """Classify pipeline/ prefixed paths."""
    if path in ACTIVE_PIPELINE_PATHS:
        return VERDICT_ACTIVE_PIPELINE
    return VERDICT_ACTIVE_CAPABILITY


def _classify_tools(path: str) -> str:
    """Classify tools/ prefixed paths."""
    if "secure_enclave" in path:
        return VERDICT_SECURITY_CRITICAL
    return VERDICT_ACTIVE_SIDECAR


def _classify_core(path: str) -> str | None:
    """Classify core/ prefixed paths (excluding __main__.py)."""
    if path != "core/__main__.py":
        return VERDICT_UNKNOWN_NEEDS_REVIEW
    return None


def _classify_init_file(path: str) -> str | None:
    """Classify __init__.py files by directory."""
    if path.endswith("/__init__.py"):
        dir_name = path.split("/")[0]
        if dir_name in ("knowledge", "storage"):
            return VERDICT_STORAGE_AUTHORITY
        if dir_name in ("security", "stealth"):
            return VERDICT_SECURITY_CRITICAL
        if dir_name in ("transport",):
            return VERDICT_TRANSPORT_AUTHORITY
        return VERDICT_ACTIVE_CAPABILITY
    return None


def classify_path(path: str) -> str:
    """Classify a path based on known patterns and wiring evidence.

    Dispatcher pattern: exact match → test prefix → prefix match → special cases → default.
    """
    # 1. Exact path matches
    if path in _EXACT_PATH_VERDICTS:
        return _EXACT_PATH_VERDICTS[path]

    # 2. Test prefix patterns (tests/, probe_, benchmarks/)
    for prefix in _TEST_PREFIX_VERDICTS:
        if path.startswith(prefix):
            return VERDICT_TEST_ONLY

    # 3. Special test-only patterns
    test_verdict = _classify_test_only(path)
    if test_verdict:
        return test_verdict

    # 4. Prefix-based classification
    for prefixes, verdict in _PREFIX_VERDICTS:
        if path.startswith(prefixes):
            # Special handlers for complex prefix rules
            if verdict == VERDICT_ACTIVE_RUNTIME and path.startswith("runtime/"):
                return _classify_runtime(path)
            if verdict == VERDICT_ACTIVE_CAPABILITY and path.startswith("pipeline/"):
                return _classify_pipeline(path)
            if verdict == VERDICT_ACTIVE_SIDECAR and path.startswith("tools/"):
                return _classify_tools(path)
            if verdict == VERDICT_UNKNOWN_NEEDS_REVIEW and path.startswith("core/"):
                return _classify_core(path)
            return verdict

    # 5. Donor/optional prefix check
    for prefix in DONOR_OR_OPTIONAL_PATHS:
        if path.startswith(prefix):
            return VERDICT_DONOR_OR_OPTIONAL

    # 5. Special __init__.py classification
    init_verdict = _classify_init_file(path)
    if init_verdict:
        return init_verdict

    # 6. Default
    return VERDICT_UNKNOWN_NEEDS_REVIEW


def build_evidence(path: str, qoder_docs: list[str]) -> dict:
    """Build evidence dict for a module."""
    evidence = {
        "referenced_in_docs": len(qoder_docs),
        "has_canonical_write": path in HAS_CANONICAL_WRITE,
        "has_network_path": path in HAS_NETWORK_PATH,
        "has_mlx_import": path in HAS_MLX_IMPORT,
        "has_subprocess_spawn": path in HAS_SUBPROCESS_SPAWN,
        "has_secret_export_risk": path in HAS_SECRET_EXPORT_RISK,
        "is_production_call_target": path in PRODUCTION_CALL_PATHS,
    }
    return evidence


def build_risks(path: str, verdict: str, exists: bool) -> list[str]:
    """Build risk list for a module."""
    risks = []
    if not exists:
        risks.append("MISSING_DOC_TARGET: file referenced in docs but does not exist in repo")
    if verdict == VERDICT_UNKNOWN_NEEDS_REVIEW and exists:
        risks.append("UNKNOWN_NEEDS_REVIEW: path exists but not classified, needs manual wiring audit")
    if verdict == VERDICT_DEPRECATED:
        risks.append("DEPRECATED: deprecated facade, do not use for new work")
    if verdict == VERDICT_LEGACY:
        risks.append("LEGACY: historical code, not in canonical production path")
    if verdict == VERDICT_DONOR:
        risks.append("DONOR: referenced by docs but not in canonical runtime call chain")
    if verdict == VERDICT_DONOR_OR_OPTIONAL:
        risks.append("DONOR_OR_OPTIONAL: documented by Qoder but not reachable from canonical sprint")
    if verdict == VERDICT_DEAD_OR_UNWIRED:
        risks.append("DEAD_OR_UNWIRED: confirmed dead or unwired from production call chain")
    if path in HAS_SECRET_EXPORT_RISK:
        risks.append("PRIVATE_KEY_OR_SECRET_EXPORT_RISK: handles sensitive material in export envelopes")
    if path in PRIVATE_HELPER_PATHS:
        risks.append("HARDCODED_ABSOLUTE_HELPER_PATH: absolute path to helper binary in code")
    if path in HAS_SUBPROCESS_SPAWN:
        risks.append("HELPER_SUBPROCESS_SPAWN: spawns subprocess at import time")
    if path in HAS_MLX_IMPORT:
        risks.append("MLX_MODEL_LOAD: imports MLX at module level")
    if verdict in (VERDICT_DONOR, VERDICT_LEGACY, VERDICT_DEPRECATED, VERDICT_DEAD_OR_UNWIRED):
        risks.append("DOCUMENTATION_OVERCLAIM: docs may overstate runtime role")
    return risks


OVERCLAIM_KEYWORDS = [
    ("integrates with runtime", VERDICT_DONOR),
    ("canonical", VERDICT_DONOR),
    ("production", VERDICT_DONOR),
    ("active", VERDICT_LEGACY),
    ("primary", VERDICT_DONOR),
    ("wired", VERDICT_DONOR),
]


def detect_overclaims(doc_path: str, content: str, refs_to_paths: dict[str, list[str]]) -> list[Overclaim]:
    """Detect documentation overclaims in a single doc."""
    overclaims = []
    for ref, doc_list in refs_to_paths.items():
        if not doc_list:
            continue
        normalized = normalize_path(ref)
        verdict = classify_path(normalized)
        for keyword, _ in OVERCLAIM_KEYWORDS:
            if keyword.lower() in content.lower():
                if verdict in (VERDICT_DONOR, VERDICT_LEGACY, VERDICT_DEPRECATED, VERDICT_DEAD_OR_UNWIRED):
                    overclaims.append(
                        Overclaim(
                            doc_path=doc_path,
                            claim=f"Document claims '{keyword}' but actual verdict is {verdict}",
                            referenced_path=normalized,
                            actual_verdict=verdict,
                            severity="MEDIUM" if verdict == VERDICT_DONOR else "HIGH",
                        )
                    )
    return overclaims


def detect_high_risk_gaps(all_modules: dict[str, ModuleReality]) -> list[HighRiskGap]:
    """Detect high-risk architectural gaps."""
    gaps = []
    secret_risk = [
        m for m in all_modules.values() if m.path in HAS_SECRET_EXPORT_RISK and m.verdict != VERDICT_SECURITY_CRITICAL
    ]
    if secret_risk:
        gaps.append(
            HighRiskGap(
                gap_type="PRIVATE_KEY_IN_EXPORT",
                description="Security-critical paths not properly classified",
                affected_paths=[m.path for m in secret_risk],
                recommended_sprint="F206AH",
                severity="CRITICAL",
            )
        )
    hardcoded = [m for m in all_modules.values() if m.path in PRIVATE_HELPER_PATHS]
    if hardcoded:
        gaps.append(
            HighRiskGap(
                gap_type="HARDCODED_HELPER_PATH",
                description="Absolute paths to helper binaries hardcoded",
                affected_paths=[m.path for m in hardcoded],
                recommended_sprint="F206AH",
                severity="HIGH",
            )
        )
    subprocess_risk = [m for m in all_modules.values() if m.path in HAS_SUBPROCESS_SPAWN]
    if subprocess_risk:
        gaps.append(
            HighRiskGap(
                gap_type="SUBPROCESS_AT_IMPORT",
                description="Helper subprocess spawned at module import time",
                affected_paths=[m.path for m in subprocess_risk],
                recommended_sprint="F206AH",
                severity="HIGH",
            )
        )
    memory_paths = [m for m in all_modules.values() if "memory" in m.path.lower() or "duckdb" in m.path.lower()]
    if len(memory_paths) > 5:
        gaps.append(
            HighRiskGap(
                gap_type="MULTIPLE_MEMORY_AUTHORITIES",
                description=f"{len(memory_paths)} memory-related paths found, potential authority fragmentation",
                affected_paths=[m.path for m in memory_paths[:10]],
                recommended_sprint="F206AI",
                severity="MEDIUM",
            )
        )
    missing_write = [
        m
        for m in all_modules.values()
        if m.verdict in (VERDICT_UNKNOWN_NEEDS_REVIEW, VERDICT_DONOR, VERDICT_DONOR_OR_OPTIONAL)
        and m.evidence.get("has_canonical_write", False) is False
        and (m.path not in HAS_CANONICAL_WRITE)
    ]
    if missing_write:
        gaps.append(
            HighRiskGap(
                gap_type="UNDOCUMENTED_WRITE_PATH",
                description="Paths with runtime activity lack canonical write documentation",
                affected_paths=[m.path for m in missing_write[:5]],
                recommended_sprint="F206AI",
                severity="MEDIUM",
            )
        )
    return gaps


def scan_qoder_wiki(qoder_root: Path) -> tuple[dict[str, ModuleReality], list[Overclaim], list[HighRiskGap], dict]:
    """Scan entire Qoder wiki tree and build reality matrix."""
    all_refs: dict[str, list[str]] = defaultdict(list)
    doc_count = 0
    md_files = list(qoder_root.rglob("*.md"))
    for md_path in sorted(md_files):
        doc_count += 1
        rel_doc = str(md_path.relative_to(qoder_root))
        refs = extract_file_refs(md_path)
        for ref_list in [refs["file_links"], refs["md_links"], refs["code_paths"]]:
            for ref in ref_list:
                normalized = normalize_path(ref)
                if normalized:
                    all_refs[normalized].append(rel_doc)
    modules: dict[str, ModuleReality] = {}
    for ref, doc_list in sorted(all_refs.items()):
        normalized = normalize_path(ref)
        if not normalized:
            continue
        exists = (REPO_ROOT / normalized).exists()
        verdict = classify_path(normalized)
        evidence = build_evidence(normalized, doc_list)
        risks = build_risks(normalized, verdict, exists)
        if verdict == VERDICT_UNKNOWN_NEEDS_REVIEW and exists:
            recommended = "Audit wiring: exists but not in known production call chain"
        elif not exists:
            recommended = "Create missing file or fix documentation reference"
        elif verdict in (VERDICT_DEPRECATED, VERDICT_LEGACY):
            recommended = "Consider removing or archiving dead code"
        elif verdict == VERDICT_DONOR:
            recommended = "Audit if donor module should be wired into production"
        elif verdict == VERDICT_SECURITY_CRITICAL:
            recommended = "Ensure security-critical path is properly audited"
        else:
            recommended = "No action needed"
        modules[normalized] = ModuleReality(
            path=normalized,
            exists=exists,
            verdict=verdict,
            qoder_docs=sorted(set(doc_list)),
            evidence=evidence,
            risks=risks,
            recommended_action=recommended,
        )
    for sec_path in sorted(SECURITY_CRITICAL_PATHS):
        if sec_path not in modules and (REPO_ROOT / sec_path).exists():
            modules[sec_path] = ModuleReality(
                path=sec_path,
                exists=True,
                verdict=VERDICT_SECURITY_CRITICAL,
                qoder_docs=[],
                evidence=build_evidence(sec_path, []),
                risks=build_risks(sec_path, VERDICT_SECURITY_CRITICAL, True),
                recommended_action="Ensure security-critical path is properly audited and documented",
            )
    total_refs = len(all_refs)
    existing = sum(1 for m in modules.values() if m.exists)
    missing = sum(1 for m in modules.values() if not m.exists)
    verdict_counts = defaultdict(int)
    for m in modules.values():
        verdict_counts[m.verdict] += 1
    summary = {
        "documents_scanned": doc_count,
        "references_extracted": total_refs,
        "modules_total": len(modules),
        "modules_exist": existing,
        "modules_missing": missing,
        "verdict_breakdown": dict(verdict_counts),
    }
    raw_overclaims: list[tuple] = []
    for md_path in sorted(qoder_root.rglob("*.md")):
        rel_doc = str(md_path.relative_to(qoder_root))
        try:
            content = md_path.read_text()
        except Exception:
            continue
        for keyword in ["canonical", "production", "wired", "active runtime"]:
            if keyword.lower() in content.lower():
                refs = extract_file_refs(md_path)
                for ref_list in [refs["file_links"], refs["md_links"], refs["code_paths"]]:
                    for ref in ref_list:
                        normalized = normalize_path(ref)
                        if normalized in modules:
                            m = modules[normalized]
                            if m.verdict in (
                                VERDICT_DONOR,
                                VERDICT_DONOR_OR_OPTIONAL,
                                VERDICT_LEGACY,
                                VERDICT_DEPRECATED,
                                VERDICT_DEAD_OR_UNWIRED,
                                VERDICT_TEST_ONLY,
                            ):
                                raw_overclaims.append((rel_doc, keyword, normalized, m.verdict))
    grouped: dict[tuple, dict] = defaultdict(lambda: {"affected_modules": set(), "examples": []})
    for doc_path, keyword, ref_path, verdict in raw_overclaims:
        key = (doc_path, keyword, verdict)
        grouped[key]["affected_modules"].add(ref_path)
        if len(grouped[key]["examples"]) < 5:
            grouped[key]["examples"].append(ref_path)
    overclaims: list[Overclaim] = []
    for (doc_path, keyword, verdict), data in grouped.items():
        affected_count = len(data["affected_modules"])
        severity = "HIGH" if affected_count > 10 else "MEDIUM" if affected_count > 3 else "LOW"
        overclaims.append(
            Overclaim(
                doc_path=doc_path,
                claim=f"Uses '{keyword}' language but module is {verdict}",
                referenced_path=data["examples"][0] if data["examples"] else "",
                actual_verdict=verdict,
                severity=severity,
                affected_modules_count=affected_count,
                examples=data["examples"],
                group_key=f"{doc_path}|{keyword}|{verdict}",
            )
        )
    gaps = detect_high_risk_gaps(modules)
    return (modules, overclaims, gaps, summary)


def write_json(
    modules: dict[str, ModuleReality],
    overclaims: list[Overclaim],
    gaps: list[HighRiskGap],
    summary: dict,
    output_path: Path,
) -> None:
    """Write JSON reality matrix."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "qoder_root": str(REPO_ROOT / ".qoder/repowiki/en/content"),
        "documents_scanned": summary["documents_scanned"],
        "references_extracted": summary["references_extracted"],
        "modules": [asdict(m) for m in sorted(modules.values(), key=attrgetter("path"))],
        "overclaims": [asdict(o) for o in overclaims],
        "high_risk_gaps": [asdict(g) for g in gaps],
        "summary": summary,
    }
    output_path.write_text(json.dumps(data, indent=2))
    print(f"JSON written: {output_path} ({len(data['modules'])} modules)")


def write_markdown(
    modules: dict[str, ModuleReality],
    overclaims: list[Overclaim],
    gaps: list[HighRiskGap],
    summary: dict,
    output_path: Path,
) -> None:
    """Write Markdown reality matrix report."""
    by_verdict: dict[str, list[ModuleReality]] = defaultdict(list)
    for m in modules.values():
        by_verdict[m.verdict].append(m)
    lines = [
        "# Qoder Repowiki Reality Matrix",
        "",
        f"**Scanned**: {summary['documents_scanned']} documents, {summary['references_extracted']} references, {summary['modules_total']} unique modules ({summary['modules_exist']} exist, {summary['modules_missing']} missing)",
        "",
        "---",
        "",
        "## Executive Summary",
        "",
        f"- **CANONICAL_OWNER**: {len(by_verdict.get(VERDICT_CANONICAL_OWNER, []))} modules",
        f"- **ACTIVE_RUNTIME**: {len(by_verdict.get(VERDICT_ACTIVE_RUNTIME, []))} modules",
        f"- **ACTIVE_PIPELINE**: {len(by_verdict.get(VERDICT_ACTIVE_PIPELINE, []))} modules",
        f"- **ACTIVE_SIDECAR**: {len(by_verdict.get(VERDICT_ACTIVE_SIDECAR, []))} modules",
        f"- **ACTIVE_DIAGNOSTIC**: {len(by_verdict.get(VERDICT_ACTIVE_DIAGNOSTIC, []))} modules",
        f"- **ACTIVE_SUPPORT**: {len(by_verdict.get(VERDICT_ACTIVE_SUPPORT, []))} modules",
        f"- **ACTIVE_CAPABILITY**: {len(by_verdict.get(VERDICT_ACTIVE_CAPABILITY, []))} modules",
        f"- **ACTIVE_ENTRYPOINT**: {len(by_verdict.get(VERDICT_ACTIVE_ENTRYPOINT, []))} modules",
        f"- **PATH_AUTHORITY**: {len(by_verdict.get(VERDICT_PATH_AUTHORITY, []))} modules",
        f"- **DONOR_OR_OPTIONAL**: {len(by_verdict.get(VERDICT_DONOR_OR_OPTIONAL, []))} modules",
        f"- **SECURITY_CRITICAL**: {len(by_verdict.get(VERDICT_SECURITY_CRITICAL, []))} modules",
        f"- **STORAGE_AUTHORITY**: {len(by_verdict.get(VERDICT_STORAGE_AUTHORITY, []))} modules",
        f"- **TRANSPORT_AUTHORITY**: {len(by_verdict.get(VERDICT_TRANSPORT_AUTHORITY, []))} modules",
        f"- **DONOR**: {len(by_verdict.get(VERDICT_DONOR, []))} modules",
        f"- **LEGACY**: {len(by_verdict.get(VERDICT_LEGACY, []))} modules",
        f"- **DEPRECATED**: {len(by_verdict.get(VERDICT_DEPRECATED, []))} modules",
        f"- **TEST_ONLY**: {len(by_verdict.get(VERDICT_TEST_ONLY, []))} modules",
        f"- **DEAD_OR_UNWIRED**: {len(by_verdict.get(VERDICT_DEAD_OR_UNWIRED, []))} modules",
        f"- **MISSING_DOC_TARGET**: {len(by_verdict.get(VERDICT_MISSING_DOC_TARGET, []))} modules",
        f"- **UNKNOWN_NEEDS_REVIEW**: {len(by_verdict.get(VERDICT_UNKNOWN_NEEDS_REVIEW, []))} modules",
        "",
        "---",
        "",
        "## Canonical Hot Path Map",
        "",
        "```",
        "core/__main__.py (CANONICAL_OWNER)",
        "  └── run_sprint()",
        "        ├── runtime/sprint_scheduler.py (ACTIVE_RUNTIME)",
        "        │     ├── runtime/sprint_lifecycle.py",
        "        │     ├── runtime/sprint_lifecycle_runner.py",
        "        │     ├── runtime/sprint_advisory_runner.py (ACTIVE_SIDECAR)",
        "        │     ├── runtime/sidecar_bus.py (ACTIVE_SIDECAR)",
        "        │     ├── runtime/sidecar_dispatcher.py (ACTIVE_SIDECAR)",
        "        │     └── runtime/shadow_*.py (ACTIVE_DIAGNOSTIC)",
        "        ├── knowledge/duckdb_store.py (STORAGE_AUTHORITY)",
        "        ├── knowledge/semantic_store.py (STORAGE_AUTHORITY)",
        "        ├── export/sprint_exporter.py",
        "        └── pipeline/live_public_pipeline.py (ACTIVE_PIPELINE)",
        "              └── pipeline/live_feed_pipeline.py (ACTIVE_PIPELINE)",
        "```",
        "",
        "---",
        "",
        "## Active Runtime Modules",
        "",
    ]
    for m in sorted(by_verdict.get(VERDICT_ACTIVE_RUNTIME, []), key=attrgetter("path")):
        lines.append(f"- `{m.path}` — {(m.qoder_docs[0] if m.qoder_docs else 'no docs')}")
    lines.extend(["", "## Active Pipeline Modules", ""])
    for m in sorted(by_verdict.get(VERDICT_ACTIVE_PIPELINE, []), key=attrgetter("path")):
        lines.append(f"- `{m.path}` — {len(m.qoder_docs)} doc(s)")
    lines.extend(["", "## Active Sidecar Modules", ""])
    for m in sorted(by_verdict.get(VERDICT_ACTIVE_SIDECAR, []), key=attrgetter("path")):
        lines.append(f"- `{m.path}`")
    lines.extend(["", "## Active Diagnostic Modules", ""])
    for m in sorted(by_verdict.get(VERDICT_ACTIVE_DIAGNOSTIC, []), key=attrgetter("path")):
        lines.append(f"- `{m.path}`")
    lines.extend(["", "## Security-Critical Modules", ""])
    for m in sorted(by_verdict.get(VERDICT_SECURITY_CRITICAL, []), key=attrgetter("path")):
        lines.append(f"- `{m.path}` — {(m.risks[0] if m.risks else 'no risks noted')}")
    lines.extend(["", "## Storage Authority Modules", ""])
    for m in sorted(by_verdict.get(VERDICT_STORAGE_AUTHORITY, []), key=attrgetter("path")):
        lines.append(f"- `{m.path}`")
    lines.extend(["", "## Transport Authority Modules", ""])
    for m in sorted(by_verdict.get(VERDICT_TRANSPORT_AUTHORITY, []), key=attrgetter("path")):
        lines.append(f"- `{m.path}`")
    lines.extend(["", "## Donor / Legacy / Deprecated Modules", ""])
    for verdict in [VERDICT_DONOR, VERDICT_LEGACY, VERDICT_DEPRECATED]:
        if by_verdict.get(verdict):
            lines.append(f"\n### {verdict} ({len(by_verdict[verdict])})")
            for m in sorted(by_verdict[verdict], key=attrgetter("path"))[:20]:
                lines.append(f"- `{m.path}` — {len(m.qoder_docs)} doc(s)")
            if len(by_verdict[verdict]) > 20:
                lines.append(f"  ... and {len(by_verdict[verdict]) - 20} more")
    lines.extend(["", "## Missing Documentation Targets", ""])
    missing = [m for m in modules.values() if not m.exists]
    if missing:
        for m in sorted(missing, key=attrgetter("path"))[:30]:
            lines.append(f"- `{m.path}` — referenced by {len(m.qoder_docs)} doc(s)")
    else:
        lines.append("None — all referenced files exist.")
    lines.extend(["", "## Unknown / Needs Review", ""])
    unknown = by_verdict.get(VERDICT_UNKNOWN_NEEDS_REVIEW, [])
    for m in sorted(unknown, key=attrgetter("path"))[:20]:
        lines.append(f"- `{m.path}` — {m.recommended_action}")
    lines.extend(["", "## Overclaims (Grouped)", ""])
    if overclaims:
        total_affected = sum(o.affected_modules_count for o in overclaims)
        lines.append(f"**Total overclaims**: {len(overclaims)} groups affecting ~{total_affected} module references")
        lines.append("")
        for o in sorted(overclaims, key=lambda x: -x.affected_modules_count)[:30]:
            lines.append(f"- **[{o.severity}]** `{o.doc_path}`: {o.claim}")
            lines.append(
                f"  → `{o.affected_modules_count}` affected modules, examples: {', '.join(f'`{e}`' for e in o.examples[:3])}"
            )
    else:
        lines.append("No significant overclaims detected.")
    lines.extend(["", "## High-Risk Gaps", ""])
    if gaps:
        for g in gaps:
            lines.append(f"\n### [{g.severity}] {g.gap_type}")
            lines.append(f"**Recommended Sprint**: {g.recommended_sprint}")
            lines.append("**Affected paths**:")
            for p in g.affected_paths:
                lines.append(f"  - `{p}`")
    else:
        lines.append("No high-risk gaps detected.")
    lines.extend(["", "## Verdict Breakdown", ""])
    for verdict, count in sorted(summary["verdict_breakdown"].items(), key=lambda x: -x[1]):
        lines.append(f"- **{verdict}**: {count}")
    lines.extend(["", "---", "", "## Recommended Sprint Queue", ""])
    sprint_queue = [
        ("F206AH", "Security-critical gaps: private key export, hardcoded helper paths, subprocess spawn"),
        ("F206AI", "Memory authority audit, undocumented write paths"),
        ("F206AJ", "Legacy/deprecated cleanup, donor module wiring decisions"),
    ]
    for sprint, desc in sprint_queue:
        lines.append(f"- **{sprint}**: {desc}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines))
    print(f"Markdown written: {output_path}")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Qoder Repowiki Reality Matrix Scanner")
    parser.add_argument("--qoder-root", type=Path, default=QODER_ROOT_DEFAULT, help="Path to Qoder wiki root")
    parser.add_argument("--json", type=Path, default=OUTPUT_JSON_DEFAULT, help="Output JSON path")
    parser.add_argument("--markdown", type=Path, default=OUTPUT_MD_DEFAULT, help="Output Markdown path")
    args = parser.parse_args()
    print(f"Scanning Qoder wiki: {args.qoder_root}")
    modules, overclaims, gaps, summary = scan_qoder_wiki(args.qoder_root)
    print(f"Found {len(modules)} unique module references across {summary['documents_scanned']} docs")
    write_json(modules, overclaims, gaps, summary, args.json)
    write_markdown(modules, overclaims, gaps, summary, args.markdown)
    print("\nSummary:")
    for verdict, count in sorted(summary["verdict_breakdown"].items(), key=lambda x: -x[1]):
        print(f"  {verdict}: {count}")
    print(f"\nOverclaims: {len(overclaims)}")
    print(f"High-risk gaps: {len(gaps)}")


if __name__ == "__main__":
    main()
