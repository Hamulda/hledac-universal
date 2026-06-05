"""
hledac — root namespace package.

This file makes `hledac` a real (non-namespace) package so that
`from hledac._namespace_bootstrap import ensure_namespace_paths` is a
valid import. Calling the bootstrap here at import time means that any
caller of `import hledac.universal` (or any other `hledac.X` subpackage)
will see the extended namespace, with sibling top-level directories
wired in as `hledac.security`, `hledac.core`, `hledac.advanced_web`,
`hledac.advanced_rag`, `hledac.research`, `hledac.advanced_reasoning`,
`hledac.discovery`, etc.

The actual wire-up logic lives in `hledac._namespace_bootstrap` and is
**idempotent** — safe to call from anywhere, any number of times.
"""
from __future__ import annotations

# Bootstrap FIRST so subsequent `from hledac.X import Y` calls work even
# when only the root `hledac` package is being imported.
from hledac._namespace_bootstrap import ensure_namespace_paths  # noqa: E402

ensure_namespace_paths()

__all__ = ["ensure_namespace_paths"]
