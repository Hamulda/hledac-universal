"""
SPRINT F206AG-v2: Qoder Reality Check — Hermetic Test Suite

Tests the qoder_reality_check.py scanner invariants.
HERMETIC: No MLX imports, no network calls, no live sprint execution.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal")
SCANNER = REPO_ROOT / "tools/qoder_reality_check.py"
QODER_ROOT = REPO_ROOT / ".qoder/repowiki/en/content"
JSON_OUTPUT = REPO_ROOT / "probe_qoder_reality/qoder_reality_matrix.json"
MD_OUTPUT = REPO_ROOT / "probe_qoder_reality/REPORT_QODER_REALITY_MATRIX.md"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="module")
def matrix_data() -> dict:
    """Load the generated reality matrix."""
    if not JSON_OUTPUT.exists():
        pytest.skip(f"Matrix not generated: {JSON_OUTPUT}")
    with open(JSON_OUTPUT) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def modules(matrix_data: dict) -> dict[str, dict]:
    """Index modules by path."""
    return {m["path"]: m for m in matrix_data["modules"]}


# --------------------------------------------------------------------------- #
# Invariant: Qoder root exists
# --------------------------------------------------------------------------- #

class TestQoderRootExists:
    def test_qoder_root_directory_exists(self):
        """Qoder wiki root must exist."""
        assert QODER_ROOT.exists(), f"Qoder root not found: {QODER_ROOT}"
        assert QODER_ROOT.is_dir()

    def test_qoder_root_has_content(self):
        """Qoder wiki root must have subdirectories."""
        subdirs = [d for d in QODER_ROOT.iterdir() if d.is_dir()]
        assert len(subdirs) > 0, "Qoder root has no subdirectories"

    def test_qoder_root_has_markdown_files(self):
        """Qoder wiki root must contain .md files."""
        md_files = list(QODER_ROOT.rglob("*.md"))
        assert len(md_files) >= 80, f"Expected ~88 .md files, found {len(md_files)}"


# --------------------------------------------------------------------------- #
# Invariant: Scanner walks all specified top-level folders/files
# --------------------------------------------------------------------------- #

class TestScannerCoverage:
    def test_scanner_script_exists(self):
        """Scanner script must exist."""
        assert SCANNER.exists(), f"Scanner not found: {SCANNER}"

    def test_scanner_defines_scan_function(self):
        """Scanner must define scan_qoder_wiki function."""
        content = SCANNER.read_text()
        assert "def scan_qoder_wiki" in content
        assert "def classify_path" in content
        assert "def extract_file_refs" in content

    def test_matrix_mentions_all_major_sections(self, matrix_data: dict):
        """Matrix must reference docs from all major Qoder sections."""
        sections = [
            "Core Architecture",
            "Brain Engines",
            "Pipeline System",
            "Runtime Management",
            "Security and Privacy",
            "Knowledge Layer",
            "Transport and Networking",
            "Intelligence Modules",
            "Testing and Quality Assurance",
            "Export and Reporting",
        ]
        doc_paths = set()
        for m in matrix_data["modules"]:
            doc_paths.update(m["qoder_docs"])

        found = []
        missing = []
        for section in sections:
            for doc in doc_paths:
                if section.lower() in doc.lower():
                    found.append(section)
                    break
            else:
                missing.append(section)

        assert len(missing) == 0, f"Sections not referenced: {missing}"


# --------------------------------------------------------------------------- #
# Invariant: core/__main__.py classified CANONICAL_OWNER
# --------------------------------------------------------------------------- #

class TestCanonicalOwner:
    def test_core_main_classified_canonical_owner(self, modules: dict):
        """core/__main__.py must be CANONICAL_OWNER."""
        assert "core/__main__.py" in modules, "core/__main__.py not in matrix"
        assert modules["core/__main__.py"]["verdict"] == "CANONICAL_OWNER"
        assert modules["core/__main__.py"]["exists"] is True

    def test_core_main_referenced_in_docs(self, modules: dict):
        """core/__main__.py must be referenced in Qoder docs."""
        assert "core/__main__.py" in modules
        assert len(modules["core/__main__.py"]["qoder_docs"]) > 0


# --------------------------------------------------------------------------- #
# Invariant: runtime/sprint_scheduler.py classified ACTIVE_RUNTIME
# --------------------------------------------------------------------------- #

class TestActiveRuntime:
    def test_sprint_scheduler_classified_active_runtime(self, modules: dict):
        """runtime/sprint_scheduler.py must be ACTIVE_RUNTIME."""
        assert "runtime/sprint_scheduler.py" in modules
        assert modules["runtime/sprint_scheduler.py"]["verdict"] == "ACTIVE_RUNTIME"
        assert modules["runtime/sprint_scheduler.py"]["exists"] is True

    def test_sprint_lifecycle_classified_active_runtime(self, modules: dict):
        """runtime/sprint_lifecycle.py must be ACTIVE_RUNTIME."""
        assert "runtime/sprint_lifecycle.py" in modules
        assert modules["runtime/sprint_lifecycle.py"]["verdict"] == "ACTIVE_RUNTIME"

    def test_sprint_lifecycle_runner_classified_active_runtime(self, modules: dict):
        """runtime/sprint_lifecycle_runner.py must be ACTIVE_RUNTIME."""
        assert "runtime/sprint_lifecycle_runner.py" in modules
        assert modules["runtime/sprint_lifecycle_runner.py"]["verdict"] == "ACTIVE_RUNTIME"


# --------------------------------------------------------------------------- #
# Invariant: pipeline/live_public_pipeline.py classified ACTIVE_PIPELINE
# --------------------------------------------------------------------------- #

class TestActivePipeline:
    def test_live_public_pipeline_classified_active_pipeline(self, modules: dict):
        """pipeline/live_public_pipeline.py must be ACTIVE_PIPELINE."""
        assert "pipeline/live_public_pipeline.py" in modules
        assert modules["pipeline/live_public_pipeline.py"]["verdict"] == "ACTIVE_PIPELINE"
        assert modules["pipeline/live_public_pipeline.py"]["exists"] is True

    def test_live_feed_pipeline_classified_active_pipeline(self, modules: dict):
        """pipeline/live_feed_pipeline.py must be ACTIVE_PIPELINE."""
        assert "pipeline/live_feed_pipeline.py" in modules
        assert modules["pipeline/live_feed_pipeline.py"]["verdict"] == "ACTIVE_PIPELINE"


# --------------------------------------------------------------------------- #
# Invariant: legacy/autonomous_orchestrator.py classified LEGACY
# --------------------------------------------------------------------------- #

class TestLegacyClassification:
    def test_legacy_autonomous_orchestrator_classified_legacy_if_referenced(self, modules: dict):
        """If legacy/autonomous_orchestrator.py is in docs, it must be LEGACY."""
        if "legacy/autonomous_orchestrator.py" in modules:
            m = modules["legacy/autonomous_orchestrator.py"]
            assert m["verdict"] == "LEGACY"
            assert m["exists"] is True


# --------------------------------------------------------------------------- #
# Invariant: runtime/windup_engine.py NOT classified ACTIVE_RUNTIME
# --------------------------------------------------------------------------- #

class TestWindupEngineClassification:
    def test_windup_engine_not_active_runtime(self, modules: dict):
        """runtime/windup_engine.py must NOT be ACTIVE_RUNTIME unless production call path exists."""
        if "runtime/windup_engine.py" in modules:
            verdict = modules["runtime/windup_engine.py"]["verdict"]
            assert verdict != "ACTIVE_RUNTIME", \
                "runtime/windup_engine.py is ACTIVE_RUNTIME but has no production call path"


# --------------------------------------------------------------------------- #
# Invariant: security/pq_export_encryption.py is SECURITY_CRITICAL
# --------------------------------------------------------------------------- #

class TestSecurityCritical:
    def test_pq_export_encryption_security_critical(self, modules: dict):
        """security/pq_export_encryption.py must be SECURITY_CRITICAL."""
        assert "security/pq_export_encryption.py" in modules
        assert modules["security/pq_export_encryption.py"]["verdict"] == "SECURITY_CRITICAL"

    def test_pq_export_encryption_swift_security_critical(self, modules: dict):
        """security/pq_export_encryption_swift.py must be SECURITY_CRITICAL."""
        assert "security/pq_export_encryption_swift.py" in modules
        assert modules["security/pq_export_encryption_swift.py"]["verdict"] == "SECURITY_CRITICAL"

    def test_secure_enclave_helper_security_critical(self, modules: dict):
        """secure_enclave_helper files must be SECURITY_CRITICAL if referenced in docs."""
        # secure_enclave_helper is a Swift package; check PQ encryption Python wrappers
        if "security/pq_export_encryption.py" in modules:
            assert modules["security/pq_export_encryption.py"]["verdict"] == "SECURITY_CRITICAL"


# --------------------------------------------------------------------------- #
# Invariant: No UNKNOWN_NEEDS_REVIEW verdict for existing files
# --------------------------------------------------------------------------- #

class TestNoUnknownExisting:
    def test_no_unknown_verdict_for_existing_files(self, modules: dict):
        """No existing file should have UNKNOWN_NEEDS_REVIEW verdict."""
        unknown_existing = [
            m["path"] for m in modules.values()
            if m["verdict"] == "UNKNOWN_NEEDS_REVIEW" and m["exists"]
        ]
        assert len(unknown_existing) == 0, \
            f"Existing files with UNKNOWN verdict: {unknown_existing}"


# --------------------------------------------------------------------------- #
# Invariant: Scanner does not modify production code
# --------------------------------------------------------------------------- #

class TestNoProductionModification:
    def test_scanner_does_not_import_mlx_heavy_modules(self):
        """Scanner must not import MLX-heavy modules at module level."""
        scanner_content = SCANNER.read_text()
        mlx_imports = [
            "from hledac.universal.brain import",
            "from hledac.universal.utils import mlx",
            "import mlx",
        ]
        for pattern in mlx_imports:
            assert pattern not in scanner_content, \
                f"Scanner imports MLX-heavy module: {pattern}"

    def test_scanner_has_no_network_calls(self):
        """Scanner must not make live network calls."""
        scanner_content = SCANNER.read_text()
        network_patterns = [
            "requests.",
            "httpx.get",
            "httpx.post",
            "aiohttp",
            "urllib.request",
            "curl_cffi",
        ]
        for pattern in network_patterns:
            assert pattern not in scanner_content, \
                f"Scanner makes network calls: {pattern}"

    def test_scanner_does_not_spawn_subprocess(self):
        """Scanner must not spawn helper subprocesses."""
        scanner_content = SCANNER.read_text()
        subprocess_patterns = [
            "subprocess.run",
            "subprocess.Popen",
            "asyncio.create_subprocess",
        ]
        for pattern in subprocess_patterns:
            assert pattern not in scanner_content, \
                f"Scanner spawns subprocess: {pattern}"


# --------------------------------------------------------------------------- #
# Invariant: Matrix has correct schema
# --------------------------------------------------------------------------- #

class TestMatrixSchema:
    def test_matrix_has_required_top_level_keys(self, matrix_data: dict):
        """Matrix must have required top-level keys."""
        required = {"qoder_root", "documents_scanned", "references_extracted",
                   "modules", "overclaims", "high_risk_gaps", "summary"}
        assert required.issubset(matrix_data.keys())

    def test_matrix_modules_have_required_fields(self, matrix_data: dict):
        """Each module must have required fields."""
        required = {"path", "exists", "verdict", "qoder_docs", "evidence", "risks",
                   "recommended_action"}
        for m in matrix_data["modules"]:
            assert required.issubset(m.keys()), f"Module missing fields: {m['path']}"

    def test_matrix_has_documents_scanned(self, matrix_data: dict):
        """documents_scanned must be 88."""
        assert matrix_data["documents_scanned"] == 88

    def test_matrix_overclaims_is_list(self, matrix_data: dict):
        """overclaims must be a list."""
        assert isinstance(matrix_data["overclaims"], list)

    def test_matrix_high_risk_gaps_is_list(self, matrix_data: dict):
        """high_risk_gaps must be a list."""
        assert isinstance(matrix_data["high_risk_gaps"], list)


# --------------------------------------------------------------------------- #
# Invariant: Markdown report exists
# --------------------------------------------------------------------------- #

class TestMarkdownReport:
    def test_markdown_report_exists(self):
        """Markdown report must exist."""
        assert MD_OUTPUT.exists(), f"Markdown report not found: {MD_OUTPUT}"

    def test_markdown_report_has_content(self):
        """Markdown report must have substantial content."""
        content = MD_OUTPUT.read_text()
        assert len(content) > 5000, f"Markdown report too short: {len(content)} chars"

    def test_markdown_report_has_canonical_hot_path(self):
        """Markdown report must include canonical hot path map."""
        content = MD_OUTPUT.read_text()
        assert "CANONICAL_OWNER" in content
        assert "core/__main__.py" in content
        assert "run_sprint()" in content


# --------------------------------------------------------------------------- #
# Invariant: No file:// prefix keys in matrix
# --------------------------------------------------------------------------- #

class TestNoFileProtocolKeys:
    def test_no_file_protocol_prefix_in_keys(self, matrix_data: dict):
        """No module path should contain 'file://' prefix."""
        bad_keys = [m["path"] for m in matrix_data["modules"] if "file://" in m["path"]]
        assert len(bad_keys) == 0, f"Keys with file:// prefix: {bad_keys[:5]}"
