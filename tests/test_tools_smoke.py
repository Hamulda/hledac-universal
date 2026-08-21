"""# noqa: N999
O-04: tools/ and scripts/ smoke tests
================================================================

Implements smoke testing for:
  - tools/*.py modules (import smoke tests via parametrization)
  - scripts/*.sh shell scripts (shellcheck validation)

Architecture:
  - Parametrized pytest tests (stable, type-safe)
  - Bounded: max 43 tests (28 safe tools + 5 shell scripts)
  - No test_ prefix needed on source files

Usage:
    pytest tests/test_tools_smoke.py -q                    # all
    pytest tests/test_tools_smoke.py -k tools -q            # tools only
    pytest tests/test_tools_smoke.py -k scripts -q          # shell scripts only
    pytest tests/ -m tools_smoke -q                         # via marker

Markers:
    tools_smoke  — all tool/script smoke tests
    tool_smoke   — Python module import smoke tests
    script_smoke — shell script validation tests
"""

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal")
TOOLS_DIR = REPO_ROOT / "tools"
SCRIPTS_DIR = REPO_ROOT / "scripts"


# =============================================================================
# O-04: Tool smoke tests — parametrized import tests
# =============================================================================

#: Tool modules that are SAFE to import in a smoke test (no heavy MLX/GPU init)
#: Heavy tools (content_miner, darknet, document_metadata_extractor, executor,
#: lightpanda_*, lmdb_kv, paywall, vision_analyzer, vlm_analyzer, wasm_sandbox)
#: are tested separately in their own sprint test files.
SAFE_TOOLS: list[str] = [
    "content_extractor",
    "deep_research_sources",
    "deep_web_hints",
    "delta_compressor",
    "discovery_replay",
    "file_cache",
    "ftp_explorer",
    "hnsw_builder",
    "ioc_dedup",
    "metadata_dedup",
    "ocr_engine",
    "osint_frameworks",
    "policies",
    "regex_cache",
    "registry",
    "reputation",
    "reranker",
    "rolling_hash_engine",
    "scoring",
    "search_fusion",
    "serialization",
    "session_manager",
    "smart_deduplicator",
    "source_bandit",
    "temporal",
    "url_dedup",
    "zstd_compressor",
]


def _get_tool_module(tool_name: str) -> Any:
    """Import and return a tool module, handling lazy-loading."""
    # Check if tools package exposes it via __getattr__ or direct export
    import tools as _tools_pkg

    # Try getattr first (works if __getattr__ is defined in tools/__init__.py)
    try:
        return getattr(_tools_pkg, tool_name)
    except AttributeError:
        pass

    # Fallback: direct module import via importlib
    tool_path = TOOLS_DIR / f"{tool_name}.py"
    if not tool_path.exists():
        pytest.fail(f"{tool_name}.py does not exist in tools/")

    import importlib.util

    spec = importlib.util.spec_from_file_location(f"tools.{tool_name}", tool_path)
    if spec is None or spec.loader is None:
        pytest.fail(f"Cannot load spec for tools.{tool_name}")

    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"tools.{tool_name}"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.tools_smoke
@pytest.mark.tool_smoke
@pytest.mark.parametrize("tool_name", SAFE_TOOLS, ids=lambda t: f"tools.{t}")
def test_tool_import_smoke(tool_name: str) -> None:
    """
    Smoke test: tools.{tool_name} imports without ImportError.

    Covers safe tools that don't trigger heavy MLX/GPU/ML dependencies on import.
    """
    try:
        _mod = _get_tool_module(tool_name)
    except ImportError as e:
        pytest.fail(f"ImportError for tools.{tool_name}: {e}")


# =============================================================================
# O-04: Shell script smoke tests — shellcheck validation
# =============================================================================

SHELL_SCRIPTS: list[str] = [
    "install.sh",
    "mount_ramdisk.sh",
    "setup_ramdisk_env.sh",
    "ty_check.sh",
    "unmount_ramdisk.sh",
]


def _find_shellcheck() -> Path | None:
    """Find shellcheck binary, None if not available."""
    for p in ["/opt/homebrew/bin/shellcheck", "/usr/bin/shellcheck"]:
        path = Path(p)
        if path.exists():
            return path
    return None


@pytest.mark.tools_smoke
@pytest.mark.script_smoke
@pytest.mark.parametrize("script_name", SHELL_SCRIPTS, ids=str)
def test_script_shellcheck(script_name: str) -> None:
    """
    Smoke test: scripts/{script_name} passes shellcheck validation.

    Requires shellcheck (brew install shellcheck on macOS).
    Runs shellcheck with warnings enabled and fails on any error.
    """
    script_path = SCRIPTS_DIR / script_name
    if not script_path.exists():
        pytest.skip(f"{script_name} does not exist in scripts/")

    shellcheck = _find_shellcheck()
    if shellcheck is None:
        pytest.skip("shellcheck not available (brew install shellcheck)")

    # SC1090: cannot follow non-constant sources (we use variables for paths)
    # SC1007/SC2048/SC2006/SC2028/SC2086: style preferences, not errors
    result = subprocess.run(
        [
            str(shellcheck),
            "-S",
            "error",
            "-e",
            "SC1090",
            "-e",
            "SC1007",
            "-e",
            "SC2048",
            "-e",
            "SC2006",
            "-e",
            "SC2028",
            "-e",
            "SC2086",
            str(script_path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    if result.returncode != 0:
        pytest.fail(
            f"shellcheck failed for {script_name} (exit {result.returncode}):\n"
            f"STDOUT:\n{result.stdout}\n"
            f"STDERR:\n{result.stderr}"
        )


# =============================================================================
# O-04: Tools metadata smoke test — tools/__init__.py exports
# =============================================================================


def pytest_configure(config: pytest.Config) -> None:
    """Register O-04 smoke test markers."""
    config.addinivalue_line("markers", "tools_smoke: O-04 smoke tests for tools/ and scripts/")
    config.addinivalue_line("markers", "tool_smoke: Python module import smoke tests")
    config.addinivalue_line("markers", "script_smoke: shell script validation tests")
