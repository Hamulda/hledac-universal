"""
hledac_rust_extensions — Rust extensions for Hledac OSINT platform.

Runtime-loaded PyO3 cdylib. Supports three loading strategies in order of preference:


1. Installed wheel  (standard, no path manipulation)
   → __file__ points into site-packages, .so loaded by Python's import machinery.
2. Editable develop  (uv run maturin develop — in-place .so in rust_extensions/)
   → detected by checking if __file__ is inside the workspace rust_extensions/ dir.
3. Workspace .so    (cargo build --release manually, maturin build + uv pip install)
   → fallback when neither wheel nor editable install is available.

The module is registered in sys.modules before exec_module to avoid a race:
two concurrent "import hledac_rust_extensions" calls must not try to exec the
same _spec twice.  We use an import lock (threading.local) so that only the
first caller performs the load; later callers block on that lock and receive
the already-initialised module from sys.modules.

Acceptance: python -c "import hledac_rust_extensions; print(hledac_rust_extensions.__file__)"
shows a path INSIDE site-packages when installed as a wheel.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import threading
from typing import ClassVar

__version__ = "0.1.0"


def _is_editable_install() -> bool:
    """
    True when running from 'uv run maturin develop' (in-place .so).

    maturin develop creates a .pth file or symlink inside site-packages
    that points back to the workspace rust_extensions/ directory.
    We detect this by checking whether our own __file__ lives inside
    a rust_extensions/ workspace dir (as opposed to site-packages).
    """
    own_file: str = __file__
    # Normalise: follow symlinks so we get the real path
    own_file = os.path.realpath(own_file)
    # __file__ is rust_extensions/hledac_rust_extensions/__init__.py
    # The parent of the package dir is the rust_extensions workspace root
    pkg_dir = os.path.dirname(own_file)  # → rust_extensions/hledac_rust_extensions/
    parent_dir = os.path.dirname(pkg_dir)  # → rust_extensions/
    # ISSUE-014: Non-abi3 native wheel — check for cp314-specific .so first,
    # then abi3.so for legacy compatibility, then .dylib for older builds.
    # maturin python-source="." places the .so inside the package dir.
    import sys

    py_ver = f"cpython-{sys.version_info.major}{sys.version_info.minor}-darwin"
    candidates_pkg = [
        f"hledac_rust_extensions.{py_ver}.so",
        "hledac_rust_extensions.abi3.so",
    ]
    for c in candidates_pkg:
        if os.path.isfile(os.path.join(pkg_dir, c)):
            return True
    # Also check parent dir for legacy .dylib/.so artifacts
    if sys.platform == "darwin":
        candidates_parent = ["hledac_rust_extensions.dylib", "hledac_rust_extensions.cdylib.so"]
    else:
        candidates_parent = ["hledac_rust_extensions.so", "hledac_rust_extensions.cdylib.so"]
    return any(os.path.isfile(os.path.join(parent_dir, c)) for c in candidates_parent)


def _find_workspace_so() -> str | None:
    """
    Return the .so path for the editable / workspace fallback.

    Called only when NOT installed as a wheel.
    """
    # __file__ = rust_extensions/hledac_rust_extensions/__init__.py
    pkg_dir = os.path.dirname(os.path.abspath(__file__))
    # one level up = rust_extensions/
    parent_dir = os.path.dirname(pkg_dir)
    # ISSUE-014: Non-abi3 native wheel — check cp314-specific .so first,
    # then abi3.so for legacy, then .dylib for older builds.
    import sys

    py_ver = f"cpython-{sys.version_info.major}{sys.version_info.minor}-darwin"
    candidates_pkg = [
        f"hledac_rust_extensions.{py_ver}.so",
        "hledac_rust_extensions.abi3.so",
    ]
    for c in candidates_pkg:
        so_path = os.path.join(pkg_dir, c)
        if os.path.isfile(so_path):
            return so_path
    # Then check parent dir for .dylib/.so (legacy/bug-compatible)
    if sys.platform == "darwin":
        candidates_parent = ["hledac_rust_extensions.dylib", "hledac_rust_extensions.cdylib.so"]
    else:
        candidates_parent = ["hledac_rust_extensions.so", "hledac_rust_extensions.cdylib.so"]
    for c in candidates_parent:
        so_path = os.path.join(parent_dir, c)
        if os.path.isfile(so_path):
            return so_path
    return None


class _ImportLock:
    """
    Per-module threading lock.  Only the first thread to acquire it performs
    the actual import; all others wait and then receive the pre-registered
    module from sys.modules.

    Using a class-level dict keyed by full module name means every
    concurrent import of hledac_rust_extensions in the same process shares
    the same lock — no matter which code path triggered the import.
    """

    _locks: ClassVar[dict[str, threading.Lock]] = {}

    __slots__ = ("_key",)

    def __init__(self, key: str) -> None:
        self._key = key
        if key not in _ImportLock._locks:
            _ImportLock._locks[key] = threading.Lock()

    def __enter__(self) -> None:
        _ImportLock._locks[self._key].acquire()

    def __exit__(self, _exc_type: object, _exc_val: object, _exc_tb: object) -> None:
        _ImportLock._locks[self._key].release()


def _load() -> None:
    """
    Thread-safe, idempotent module initialiser.

    Handles all three installation scenarios:
      1. Wheel-installed (standard)    → Python import machinery already loaded us
      2. Editable develop             → load workspace .so in-place
      3. Workspace .so fallback        → load manually from parent dir
    """
    # Case 1: wheel-installed — Python already executed this file as part of
    #         importlib.invalidate_caches() + import machinery.
    #         The symbols are already in sys.modules.  Nothing to do.
    if not _is_editable_install() and _find_workspace_so() is None:
        # Registered by the Python import machinery; just pull symbols.
        _mod = sys.modules.get("hledac_rust_extensions")
        if _mod is not None:
            _reexport(_mod)
        return

    # Cases 2 & 3: we need to load the .so ourselves.
    so_path = _find_workspace_so()
    if so_path is None:
        raise ImportError(
            "Native extension not found. "
            "Run: cd rust_extensions && maturin develop  (dev)  or  "
            "cd rust_extensions && maturin build --release && uv pip install dist/*.whl  (prod)"
        )

    _spec = importlib.util.spec_from_file_location("hledac_rust_extensions", so_path)
    if _spec is None:
        raise ImportError(f"importlib.util.spec_from_file_location returned None for {so_path}")
    if _spec.loader is None:
        raise ImportError(f"No loader available for {so_path}")

    # Register BEFORE exec_module so concurrent callers get the in-progress
    # module object, not a second attempt to exec the same .so.
    sys.modules["hledac_rust_extensions"] = importlib.util.module_from_spec(_spec)

    # exec_module raises on error; we let it propagate.
    _spec.loader.exec_module(sys.modules["hledac_rust_extensions"])  # type: ignore[arg-type]

    _reexport(sys.modules["hledac_rust_extensions"])


def _reexport(mod: object) -> None:
    """Re-export all public symbols from the loaded Rust module."""
    global __version__
    __version__ = getattr(mod, "__version__", __version__)
    _all = [n for n in dir(mod) if not n.startswith("_")]
    globals()["__all__"] = _all
    globals().update((n, getattr(mod, n)) for n in _all)


_LOCK = _ImportLock("hledac_rust_extensions")

with _LOCK:
    _load()
