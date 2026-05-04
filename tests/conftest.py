"""
Sprint F208-D: pytest configuration — deterministic sys.path bootstrap.

Replaces fake-namespace mocking from F208-C with clean sys.path insertion.
Python resolves hledac.universal.* via real filesystem paths, no monkeypatching.
"""

import os
import sys
import asyncio
from pathlib import Path

import pytest


def _bootstrap_project_root() -> Path:
    """
    Find the repo root that contains hledac/universal and insert it into sys.path.

    Walk upward from this conftest.py until we find:
        ancestor / "hledac" / "universal" / "__init__.py"

    Returns the resolved Path to that ancestor.
    """
    current = Path(__file__).resolve().parent  # .../hledac/universal/tests
    for ancestor in current.parents:
        candidate = ancestor / "hledac" / "universal"
        if candidate.is_dir():
            ancestor_str = str(ancestor)
            if ancestor_str not in sys.path:
                sys.path.insert(0, ancestor_str)
            return ancestor
    # Fallback: insert project root (parent of hledac/universal/tests)
    # This handles the case where we run pytest from inside hledac/universal/
    project_root = current.parent  # should be hledac/universal
    root_str = str(project_root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
    return project_root


def pytest_configure(config=None) -> None:  # noqa: ARG001
    """
    Called before any test module is imported.

    Replaces F208-C namespace mocking with deterministic sys.path bootstrap:
      1. Walk upward from this file to find repo root containing hledac/universal
      2. Insert that root into sys.path so imports resolve via real filesystem

    Also sets HF cache env vars to declared runtime root.
    """
    # config is unused but required by pytest hook signature
    project_root = _bootstrap_project_root()
    # (project_root is intentionally not referenced — bootstrap handles sys.path side effect)
    _ramdisk_env = os.environ.get("GHOST_RAMDISK", "")
    if _ramdisk_env:
        _selected = _ramdisk_env
    else:
        _selected = os.environ.get("HLEDAC_RUNTIME_ROOT", "")

    _cache_root = os.environ.get("HLEDAC_CACHE_ROOT", "")
    if not _cache_root:
        if _selected:
            os.environ["HLEDAC_CACHE_ROOT"] = _selected
        else:
            from pathlib import Path
            os.environ["HLEDAC_CACHE_ROOT"] = str(Path.home() / ".hledac_fallback_ramdisk")

    _fallback_cache = os.environ["HLEDAC_CACHE_ROOT"]
    for _env_var in [
        "HF_HOME", "HF_HUB_CACHE", "HF_DATASETS_CACHE",
        "TRANSFORMERS_CACHE", "PYTORCH_TRANSFORMERS_CACHE",
        "PYTORCH_PRETRAINED_BERT_CACHE", "TORCH_HOME",
        "XDG_CACHE_HOME", "SENTENCE_TRANSFORMERS_HOME",
    ]:
        if not os.environ.get(_env_var):
            os.environ[_env_var] = os.path.join(_fallback_cache, "hf_cache")

    os.makedirs(os.environ["HLEDAC_CACHE_ROOT"], exist_ok=True)
    os.makedirs(os.path.join(_fallback_cache, "hf_cache"), exist_ok=True)


@pytest.fixture(autouse=True)
def _restore_event_loop():
    """
    Restore a fresh event loop after every test.

    Problem: asyncio.run() calls loop.close() and does NOT restore the
    previous event loop. This leaves MainThread with no registered loop,
    causing subsequent tests that call asyncio.get_event_loop() to raise:
        RuntimeError: There is no current event loop in thread 'MainThread'.
    """
    old_loop = None
    try:
        old_loop = asyncio.get_event_loop()
    except RuntimeError:
        pass

    yield

    try:
        asyncio.get_event_loop()
    except RuntimeError:
        new_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(new_loop)