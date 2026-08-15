#!/usr/bin/env python3
"""
Audit script: find MODULE-LEVEL (top-level) imports of heavy modules.
Functions/classes may use the heavy module freely inside their body,
but it must NOT be imported at the module level.

Heavy modules: duckdb, lancedb, mlx.core, mlx_embeddings, torch,
               transformers, lxml, selectolax, cryptography

CI behavior: FAIL if any heavy module is imported at module level
             (outside of TYPE_CHECKING block or inside a function/class).

Usage:
    python tools/audit_eager_imports.py [--verbose] [--exclude-probe] [--exclude-tests]
"""

import ast
import sys
from pathlib import Path

from operator import attrgetter, itemgetter
from core import aclose
HEAVY_MODULES = {
    "duckdb",
    "lancedb",
    "mlx",
    "mlx.core",
    "mlx_embeddings",
    "mlx_vlm",
    "torch",
    "transformers",
    "torchvision",
    "lxml",
    "selectolax",
    "cryptography",
    "aioquic",
    "playwright",
    "pillow",
    "opencv-python",
}

# Components that ARE the MLX/brain/embeddings layer — they may use mlx/torch at module level
# These are feature-gated (HLEDAC_ENABLE_* flags) or optional extras ([mlx-embed], [http3])
ALLOWED = {
    # Brain MLX layer
    "brain/inference_engine.py",
    "brain/deephermes3_engine.py",
    "brain/mlx_batched_executor.py",
    "brain/mlx_embedder.py",
    "brain/_lazy.py",
    "brain/ane_embedder.py",
    "brain/modernbert_engine.py",
    "brain/coreml_embedder.py",
    "brain/ner_engine.py",
    "brain/moe_router.py",
    "brain/gnn_predictor.py",
    "brain/model_lifecycle.py",
    "brain/prompt_bandit.py",
    "brain/_hermes_cache.py",
    "brain/synthesis_runner.py",
    "brain/dspy_service.py",
    # Core MLX/embeddings
    "core/mlx_embeddings.py",
    "core/embeddings/manager.py",
    "embeddings/modernbert_embedder.py",
    # Knowledge LanceDB
    "knowledge/lancedb_store.py",
    "knowledge/semantic_store.py",
    "knowledge/stores/lancedb_vector_store.py",
    "knowledge/ann_index.py",
    "knowledge/pq_index.py",
    "knowledge/explainer/deep.py",
    # Utils MLX
    "utils/mlx_lazy.py",
    "utils/mlx_memory.py",
    "utils/mlx_cache.py",
    "utils/mlx_prompt_cache.py",
    "utils/metal_embedder_buffers.py",
    "utils/metal_slab_pool.py",
    "utils/mps_graph.py",
    "utils/shared_tensor.py",
    "utils/sketches.py",
    "utils/ane_pipelines.py",
    "utils/deduplication.py",
    "utils/intelligent_cache.py",
    "utils/capability_prober.py",
    "utils/uma_budget.py",
    "utils/memory_dashboard.py",
    "utils/platform_info.py",
    # Intelligence modules
    "intelligence/advanced_image_osint.py",
    "intelligence/document_intelligence.py",
    "intelligence/pattern_mining.py",
    "intelligence/relationship_discovery.py",
    "intel/dns_tunnel_detector.py",
    # Network/DHT
    "network/dns_tunnel_detector.py",
    "dht/local_graph.py",
    # Prefetch
    "prefetch/prefetch_oracle.py",
    "prefetch/ssm_reranker.py",
    # RL
    "rl/qmix.py",
    "rl/state_extractor.py",
    # Selectolax — parsing layer, feature-gated
    "intelligence/dark_web_intelligence.py",
    "intelligence/open_source_collectors.py",
    "intelligence/pastebin_monitor.py",
    "intelligence/stealth_crawler.py",
    "discovery/ti_feed_adapter.py",
    "parsing/feed_parser.py",
    "tools/content_miner.py",
    "tools/content_extractor.py",
    "tools/bench_m1_runtime_gates.py",
    "utils/html_text_fast.py",
    "utils/html_parse_pool.py",
    "coordinators/validation_coordinator.py",
    # DELETED: advanced_web/structured_extractor.py
    # Cryptography — security layer, feature-gated
    "intelligence/cryptographic_intelligence.py",
    "security/encryption.py",
    "security/vault_manager.py",
    "secrets_vault/vault.py",
    # Recon modules — feature-gated heavy dependencies
    "recon/dark_web_intelligence.py",
    "recon/cryptographic_intelligence.py",
    "recon/dns/dns_tunnel_detector.py",
    # Core Rust backend — selectolax fallback for HTML parsing
    "core/rust_backend/html.py",
    "core/rust_backend/misc.py",
    # MLX embeddings server — requires mlx_embeddings
    "mlx_server.py",
    # Knowledge vector index — mlx-accelerated similarity
    "knowledge/vector_index_base.py",
    # Optional extras
    "transport/http3_lane.py",  # aioquic via [http3] extra
}

EXCLUDE_DIRS = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".venv",
    "build",
    "dist",
    "archive",
    ".git",
    "tests/",
    "benchmarks/",
    "benchmarks_shadow/",
    "scripts/",
    "tools/",
    "probe_",
}


def _get_parent_map(tree: ast.AST) -> dict:
    """Build a dict mapping each node to its parent node."""
    parent_map = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parent_map[child] = parent
    return parent_map


def _is_in_type_checking_block(node: ast.AST, parent_map: dict) -> bool:
    """Check if node is inside an `if TYPE_CHECKING:` block."""
    ancestor: ast.AST | None = node
    while ancestor is not None:
        if isinstance(ancestor, ast.If):
            test = ancestor.test
            # Check if test is `TYPE_CHECKING` or `sys.modules.get("TYPE_CHECKING")`
            if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
                return True
            if isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING":
                return True
        ancestor = parent_map.get(ancestor)
    return False


def _is_inside_function_or_class(node: ast.AST, parent_map: dict) -> bool:
    """Check if node is nested inside a FunctionDef/AsyncFunctionDef/ClassDef."""
    ancestor: ast.AST | None = parent_map.get(node)
    while ancestor is not None:
        if isinstance(ancestor, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return True
        ancestor = parent_map.get(ancestor)
    return False


def find_module_level_imports(py_file: Path) -> list[dict]:
    """Find heavy module imports that are at module level (not inside a function/class)."""
    try:
        content = py_file.read_text()
    except (OSError, UnicodeDecodeError):
        return []

    try:
        tree = ast.parse(content, filename=str(py_file))
    except SyntaxError:
        return []

    parent_map = _get_parent_map(tree)
    violations = []

    for node in ast.walk(tree):
        is_module_level = not _is_inside_function_or_class(node, parent_map)
        in_type_checking = _is_in_type_checking_block(node, parent_map)

        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name.split(".")[0]
                if name in HEAVY_MODULES:
                    violations.append({
                        "file": str(py_file),
                        "line": node.lineno,
                        "module": alias.name,
                        "kind": "Import",
                        "module_level": is_module_level,
                        "type_checking": in_type_checking,
                    })

        elif isinstance(node, ast.ImportFrom):
            if node.module:
                name = node.module.split(".")[0]
                if name in HEAVY_MODULES:
                    violations.append({
                        "file": str(py_file),
                        "line": node.lineno,
                        "module": node.module,
                        "kind": "ImportFrom",
                        "module_level": is_module_level,
                        "type_checking": in_type_checking,
                    })

    return violations


def main() -> None:
    verbose = "--verbose" in sys.argv
    exclude_tests = "--exclude-tests" in sys.argv
    exclude_probe = "--exclude-probe" in sys.argv

    # BUG-FIX: Script is at tools/audit/audit_eager_imports.py
    # Path(__file__).parent = tools/audit, .parent.parent = tools (WRONG!)
    # Correct: tools/audit -> tools -> project_root (3 levels up)
    root = Path(__file__).resolve().parent.parent.parent
    violations = []

    for py_file in root.rglob("*.py"):
        path_str = str(py_file)

        if any(ex in path_str for ex in EXCLUDE_DIRS):
            continue
        if exclude_tests and "/tests/" in path_str:
            continue
        if exclude_probe and "probe_" in path_str:
            continue

        viols = find_module_level_imports(py_file)
        violations.extend(viols)

    # Only module-level violations outside TYPE_CHECKING
    module_level_violations = [
        v for v in violations
        if v["module_level"] and not v["type_checking"]
    ]

    # Filter allowed files
    real_violations = [
        v for v in module_level_violations
        if v["file"].replace(str(root) + "/", "") not in ALLOWED
    ]

    if verbose:
        print(f"Audit root: {root}")
        print(f"Total module-level violations: {len(real_violations)}")

    if real_violations:
        print(f"\n[FAIL] Eager top-level imports found: {len(real_violations)}")
        for v in sorted(real_violations, key=itemgetter("line")):
            print(f"  {v['file'].replace(str(root)+'/','')}:{v['line']}: {v['kind']} {v['module']}")
        print("\nFix by moving the import inside the function/class that uses it,")
        print("or add the file to ALLOWED if it's a core MLX/brain component.")
        sys.exit(1)
    else:
        print("[PASS] No eager top-level imports found.")


if __name__ == "__main__":
    main()
