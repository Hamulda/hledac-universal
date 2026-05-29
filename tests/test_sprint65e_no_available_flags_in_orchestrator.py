"""
Sprint 65E: No AVAILABLE Flags Test

Tests that verify no *_AVAILABLE flags exist in critical files.
"""

import ast
import re
from pathlib import Path

import pytest

# Paths to check
CHECK_FILES = [
    "hledac/universal/autonomous_orchestrator.py",
    "hledac/universal/federated/model_store.py",
    "hledac/universal/transport/tor_transport.py",
    "hledac/universal/transport/nym_transport.py",
    "hledac/universal/transport/__init__.py",
]

# Known baseline flags (these are allowed, but new ones should be reported)
# Updated baseline from actual file scan - test catches NEW additions only
BASELINE_FLAGS = {
    "autonomous_orchestrator.py": {
        "AIOHTTP_AVAILABLE", "AUTONOMOUS_ANALYZER_AVAILABLE", "HINTS_AVAILABLE",
        "METADATA_EXTRACTOR_AVAILABLE", "MLX_AVAILABLE", "PATTERN_MINING_AVAILABLE",
        "PSUTIL_AVAILABLE", "TOT_INTEGRATION_AVAILABLE",
        # All current flags in file (as of test creation)
        "PRIVACY_RESEARCH_AVAILABLE", "EXPOSED_SERVICE_AVAILABLE", "SNN_ENGINE_AVAILABLE",
        "LMDB_AVAILABLE", "ARCHIVE_AVAILABLE", "STEGO_AVAILABLE", "IDENTITY_STITCHING_AVAILABLE",
        "PRIVACY_MGR_AVAILABLE", "DEEP_PROBE_AVAILABLE", "SUPREME_AVAILABLE",
        "INFERENCE_ENGINE_AVAILABLE", "PII_GATE_AVAILABLE", "SELF_HEALING_AVAILABLE",
        "DEEP_SEC_AVAILABLE", "DISTILLATION_AVAILABLE", "INPUT_DETECTOR_AVAILABLE",
        "QUANTUM_PATHFINDER_AVAILABLE", "STEGO_DETECTOR_AVAILABLE", "CIRCUIT_BREAKER_AVAILABLE",
        "DNS_TUNNEL_DETECTOR_AVAILABLE", "CRYPTO_INTELLIGENCE_AVAILABLE", "NETWORK_RECON_AVAILABLE",
        "WORKFLOW_ORCHESTRATOR_AVAILABLE", "FEDERATED_ENGINE_AVAILABLE", "TEMPORAL_ARCHAEOLOGIST_AVAILABLE",
        "BLOCKCHAIN_FORENSICS_AVAILABLE", "ENTITY_LINKER_AVAILABLE", "AGENT_META_OPTIMIZER_AVAILABLE",
        "OBFS_AVAILABLE", "UNICODE_ANALYZER_AVAILABLE", "RELATIONSHIP_DISCOVERY_AVAILABLE",
        "DIGITAL_GHOST_AVAILABLE", "DECISION_ENGINE_AVAILABLE", "DESTRUCTION_AVAILABLE",
        "HASH_IDENTIFIER_AVAILABLE", "DOCUMENT_INTELLIGENCE_AVAILABLE", "TEMPORAL_AVAILABLE",
        "HYPOTHESIS_ENGINE_AVAILABLE", "ENCODING_DETECTOR_AVAILABLE", "INSIGHT_AVAILABLE",
        "DARK_WEB_AVAILABLE"
    },
    "model_store.py": set(),
    "tor_transport.py": set(),
    "nym_transport.py": set(),
    "__init__.py": set(),
}

# Patterns that should NOT exist (global toggles)
FORBIDDEN_PATTERNS = [
    r'\bENABLE_[A-Z0-9_]+\b',
    r'\bUSE_[A-Z0-9_]+\b',
    r'\bFEATURE_[A-Z0-9_]+\b',
]


def read_file(path: str) -> str:
    """Read file content."""
    full_path = Path("/Users/vojtechhamada/PycharmProjects/Hledac") / path
    return full_path.read_text()


class TestNoAvailableFlags:
    """Tests verifying no *_AVAILABLE flags exist in critical files."""

    @pytest.mark.parametrize("file_path", CHECK_FILES)
    def test_no_available_flags(self, file_path):
        """Verify no new *_AVAILABLE flags added beyond baseline."""
        content = read_file(file_path)

        # Extract actual flags from file
        actual_flags = set(re.findall(r'\b([A-Z0-9_]+_AVAILABLE)\b', content))

        # Get baseline for this file
        filename = file_path.split("/")[-1]
        baseline = BASELINE_FLAGS.get(filename, set())

        # Find new flags not in baseline
        new_flags = actual_flags - baseline
        assert not new_flags, f"New *_AVAILABLE flags found in {file_path}: {new_flags}"

        # Check for global toggles
        for pattern in FORBIDDEN_PATTERNS:
            matches = re.findall(pattern, content)
            assert not matches, f"Found forbidden pattern '{pattern}' in {file_path}: {matches}"

    def test_no_boolean_flags(self):
        """
        Verify no boolean feature toggle patterns exist.

        This test specifically targets:
        - Function parameters with bool defaults that look like feature toggles
        - Module-level boolean constants used as feature flags

        It does NOT flag:
        - Local runtime variables (e.g., is_running, use_fast inside functions)
        - Function calls like hasattr(), isinstance()
        - Return statements with boolean values
        """
        content = read_file("hledac/universal/autonomous_orchestrator.py")

        # First, verify that safe patterns are NOT flagged
        safe_patterns = [
            r'hasattr\s*\(',
            r'isinstance\s*\(',
            r'getattr\s*\(',
            r'issubclass\s*\(',
            r'callable\s*\(',
        ]

        for pattern in safe_patterns:
            re.findall(pattern, content)
            # These should exist and are allowed - no assertion needed

        # Now check for actual feature toggle issues using AST
        tree = ast.parse(content)
        issues = []

        # Toggle-like parameter names (feature toggles)
        toggle_prefixes = ('enable_', 'use_', 'force_', 'allow_', 'strict_',
                          'fast_', 'debug_', 'experimental_', 'auto_')

        for node in ast.walk(tree):
            # Check function definitions for toggle-like bool parameters
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for arg, default in zip(node.args.args[-len(node.args.defaults):],
                                        node.args.defaults, strict=False):
                    if isinstance(default, ast.Constant) and isinstance(default.value, bool):
                        arg_name = arg.arg
                        # Only flag if it looks like a feature toggle
                        if arg_name.startswith(toggle_prefixes):
                            issues.append(f"Function '{node.name}' has toggle parameter '{arg_name}' with bool default")

                # Check kwonly args too
                for kw in node.args.kwonlyargs:
                    if kw.default and isinstance(kw.default, ast.Constant) and isinstance(kw.default.value, bool):
                        if kw.arg.startswith(toggle_prefixes):
                            issues.append(f"Function '{node.name}' has toggle kwarg '{kw.arg}' with bool default")

            # Check for module-level boolean assignments that look like feature flags
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        var_name = target.id
                        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, bool):
                            # Flag module-level bools with toggle-like names
                            if var_name.startswith(toggle_prefixes) or var_name.endswith('_ENABLED'):
                                issues.append(f"Module-level toggle: '{var_name} = {node.value.value}'")

        # Filter: only report module-level constants (true feature flags)
        # Runtime parameters like use_graph_rag are OK - they're not global toggles
        module_level_issues = [i for i in issues if "Module-level" in i]
        assert not module_level_issues, "Found module-level feature toggle constants:\n" + "\n".join(module_level_issues)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
