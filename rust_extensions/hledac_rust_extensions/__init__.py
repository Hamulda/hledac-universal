"""
hledac_rust_extensions — Rust extensions for Hledac OSINT platform.

Native Rust code loaded from cdylib at parent directory level.
"""
import os
import sys

# Find cdylib at parent level
_package_dir = os.path.dirname(__file__)
_parent_dir = os.path.dirname(_package_dir)
_cdylib_path = os.path.join(_parent_dir, "hledac_rust_extensions.abi3.so")

if not os.path.exists(_cdylib_path):
    raise ImportError(
        f"Native extension not found at {_cdylib_path}. "
        "Please build with 'cd rust_extensions && maturin develop'."
    )

# Load cdylib as an extension module
import importlib.util
_spec = importlib.util.spec_from_file_location("hledac_rust_extensions", _cdylib_path)
assert _spec is not None, f"Failed to load spec from {_cdylib_path}"
_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None, f"No loader for {_cdylib_path}"
sys.modules["hledac_rust_extensions"] = _mod
_spec.loader.exec_module(_mod)

# Re-export symbols
__version__ = getattr(_mod, "__version__", "0.1.0")
__all__ = [n for n in dir(_mod) if not n.startswith("_")]
globals().update((n, getattr(_mod, n)) for n in __all__)
