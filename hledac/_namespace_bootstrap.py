"""
hledac namespace bootstrap.

The editable installer exposes only `hledac.universal` as a real subpackage.
Sibling top-level packages (security, core, advanced_web, advanced_rag,
advanced_reasoning, research, discovery, …) live as bare directories next
to `universal/`, so `import hledac.security` raises `ModuleNotFoundError`
under the stock namespace package.

This module extends the `hledac` namespace's `__path__` so those siblings
become importable as `hledac.X` (and the canonical `hledac.universal.X`
re-exports under `hledac.security`, `hledac.core`, etc.). The function
`ensure_namespace_paths()` is **idempotent** and fail-safe: it may be
called any number of times without duplicating paths or raising.

Usage:
    from hledac._namespace_bootstrap import ensure_namespace_paths
    ensure_namespace_paths()        # typically invoked from hledac/__init__.py

INVARIANTS:
- No top-level imports of `hledac.*` subpackages here (chicken-and-egg).
- All cross-package reads go through `importlib.import_module` and are
  wrapped in try/except — never raises.
- Module-level flag `_BOOTSTRAPPED` guarantees at-most-once path extension.
- Path entries are deduplicated via `_extend_path` (set membership on list).
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import sys
import types
from typing import List, Optional

# Resolve the project root: two levels up from this file.
#   /…/hledac/universal/hledac/_namespace_bootstrap.py
#   dirname(file)                       -> /…/hledac/universal/hledac
#   dirname(dirname(file))              -> /…/hledac/universal  ← ROOT
#
# All sibling top-level packages (security/, core/, brain/, fetching/,
# transport/, coordinators/, knowledge/, runtime/, utils/, advanced_web/,
# advanced_rag/, advanced_reasoning/, research/, discovery/) live as
# direct subdirectories of the project root.
_HLEDAC_ROOT: str = os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)


# Sibling top-level package directories of hledac.universal. Each entry is
# joined onto _HLEDAC_ROOT. Only directories that actually exist on disk
# are appended to `hledac.__path__`.
_SIBLING_PACKAGE_DIRS: tuple[str, ...] = (
    # ── Core spec paths (must be reachable as hledac.X imports) ─────
    "security",
    "advanced_web",
    "core",
    "fetching",
    "transport",
    "coordinators",
    "brain",
    "knowledge",
    "runtime",
    "utils",
    # ── Additional siblings registered by the legacy bootstrap ────
    "advanced_rag",
    "advanced_reasoning",
    "research",
    "discovery",
)


# Guard flag — True once ensure_namespace_paths has successfully extended
# the namespace on this interpreter. Lets the function be called any
# number of times without re-extending or re-importing shims.
_BOOTSTRAPPED: bool = False


def _extend_path(root: types.ModuleType, path: str) -> None:
    """Append `path` to `root.__path__` if not already present (idempotent)."""
    if not hasattr(root, "__path__") or path is None:
        return
    plist = root.__path__  # type: ignore[attr-defined]
    if path not in plist:
        plist.append(path)


def _make_pkg_stub(name: str, path: str) -> Optional[types.ModuleType]:
    """
    Create a package stub in `sys.modules[name]` if not already present.
    Returns the (existing or new) module. Idempotent.
    """
    if not os.path.isdir(path):
        return None

    if name in sys.modules:
        mod = sys.modules[name]
        _extend_path(mod, path)
        return mod

    mod = types.ModuleType(name)
    mod.__path__ = [path]  # type: ignore[attr-defined]
    mod.__package__ = name  # type: ignore[attr-defined]
    init_py = os.path.join(path, "__init__.py")
    mod.__file__ = init_py if os.path.isfile(init_py) else None  # type: ignore[attr-defined]
    sys.modules[name] = mod

    # If the hledac root is already in sys.modules, extend its __path__ too.
    root = sys.modules.get("hledac")
    if root is not None:
        _extend_path(root, path)
    return mod


def _ensure_hledac_root() -> types.ModuleType:
    """
    Ensure the `hledac` root module is in `sys.modules`.

    If `hledac` is already loaded (as a real package, e.g. via
    `hledac/__init__.py`), return it. Otherwise create a synthetic root
    and populate its `__path__` with the existing sibling project
    directories so that `import hledac.security` etc. succeed.
    """
    if "hledac" in sys.modules:
        root = sys.modules["hledac"]
        if not hasattr(root, "__path__") or root.__path__ is None:  # type: ignore[attr-defined]
            root.__path__ = []  # type: ignore[attr-defined]
        for sub in _SIBLING_PACKAGE_DIRS:
            full = os.path.join(_HLEDAC_ROOT, sub)
            if os.path.isdir(full):
                _extend_path(root, full)
        return root

    # Synthesize a fresh namespace root (legacy smoke_test path).
    root = types.ModuleType("hledac")
    initial: List[str] = []
    for sub in _SIBLING_PACKAGE_DIRS:
        full = os.path.join(_HLEDAC_ROOT, sub)
        if os.path.isdir(full):
            initial.append(full)
    root.__path__ = initial  # type: ignore[attr-defined]
    root.__package__ = "hledac"  # type: ignore[attr-defined]
    sys.modules["hledac"] = root
    return root


# ---------------------------------------------------------------------------
# Spec-mandated namespace extensions
# ---------------------------------------------------------------------------


def _inject_sys_path() -> None:
    """
    Idempotently inject `_HLEDAC_ROOT` and every spec sibling into
    `sys.path` so the canonical Python import machinery can resolve
    `import security`, `import hledac.security`, `import hledac.universal`,
    etc. without confusion.

    Project layout (real, not assumed):
        /…/hledac/universal/                  ← _HLEDAC_ROOT, project root
        /…/hledac/universal/hledac/           ← namespace root (this file)
        /…/hledac/universal/hledac/__init__.py
        /…/hledac/universal/{security,core,advanced_web,fetching,transport,
                              coordinators,brain,knowledge,runtime,utils}

    Each sibling directory is prepended to `sys.path` **only if it exists
    on disk** and is **not already present** (idempotent: 3× calls produce
    a single entry per dir).
    """
    targets: tuple[str, ...] = (_HLEDAC_ROOT,) + tuple(
        os.path.join(_HLEDAC_ROOT, sub) for sub in _SIBLING_PACKAGE_DIRS
    )
    for path in targets:
        if os.path.isdir(path) and path not in sys.path:
            sys.path.insert(0, path)


def _bootstrap_universal() -> None:
    """
    Create the virtual `hledac.universal` package — points to the project
    root, allowing `import hledac.universal` and downstream imports
    (`hledac.universal.brain`, `hledac.universal.coordinators`, …) to
    resolve.

    Without this, Python cannot find `hledac.universal` because the
    `universal/` directory is the project root itself, not a sub-package
    of `hledac/`.

    Idempotent: if `hledac.universal` is already in `sys.modules`, this is
    a no-op. Re-registration is allowed (e.g. after `importlib.reload` of
    the namespace root) but will not duplicate `__path__` entries.
    """
    if not os.path.isdir(_HLEDAC_ROOT):
        return

    root_init = os.path.join(_HLEDAC_ROOT, "__init__.py")
    if "hledac.universal" in sys.modules:
        mod = sys.modules["hledac.universal"]
        if _HLEDAC_ROOT not in getattr(mod, "__path__", []):
            mod.__path__ = [_HLEDAC_ROOT]  # type: ignore[attr-defined]
        if not getattr(mod, "__file__", None) and os.path.isfile(root_init):
            mod.__file__ = root_init  # type: ignore[attr-defined]
        return

    mod = types.ModuleType("hledac.universal")
    mod.__path__ = [_HLEDAC_ROOT]  # type: ignore[attr-defined]
    mod.__file__ = root_init if os.path.isfile(root_init) else None  # type: ignore[attr-defined]
    mod.__package__ = "hledac.universal"  # type: ignore[attr-defined]
    sys.modules["hledac.universal"] = mod


def _bootstrap_extended_siblings() -> None:
    """
    Belt-and-suspenders wiring for the spec sibling packages. Two
    mechanisms are applied per directory:

    1. Append the directory to `hledac.__path__` (so sub-package search
       can reach it via the standard namespace path machinery).
    2. Register an explicit stub in `sys.modules` as `hledac.<sub>`.
       This is REQUIRED when the parent `hledac` is a regular package
       (has its own ``__init__.py``), because regular packages cannot
       host implicit namespace sub-portions in their ``__path__`` — the
       standard import machinery would fail to find even regular
       sub-packages whose physical location sits outside the parent's
       own directory.

    Idempotent: `_extend_path` deduplicates on `not in plist`,
    `_make_pkg_stub` short-circuits if the name is already in
    `sys.modules`. Both are no-ops on repeated invocations.

    Fail-safe: each per-directory step is wrapped in try/except so a
    single broken sibling cannot abort the rest of the bootstrap.
    """
    hledac_mod = sys.modules.get("hledac")
    for sub in _SIBLING_PACKAGE_DIRS:
        full = os.path.join(_HLEDAC_ROOT, sub)
        if not os.path.isdir(full):
            continue
        try:
            if hledac_mod is not None and hasattr(hledac_mod, "__path__"):
                _extend_path(hledac_mod, full)
        except Exception:
            pass  # fail-soft
        try:
            _make_pkg_stub(f"hledac.{sub}", full)
        except Exception:
            pass  # fail-soft


# ---------------------------------------------------------------------------
# Per-sibling re-export shims
# ---------------------------------------------------------------------------


def _bootstrap_security() -> None:
    """
    Wire up `hledac.security` using `hledac.universal._shims` as the
    canonical source of the heavy classes. The local entropy_source.py
    is loaded directly (it's a sibling module, not a package).
    """
    sec_dir = os.path.join(_HLEDAC_ROOT, "security")
    if not os.path.isdir(sec_dir):
        return
    sec = _make_pkg_stub("hledac.security", sec_dir)
    if sec is None:
        return

    ent_path = os.path.join(sec_dir, "entropy_source.py")
    if os.path.isfile(ent_path) and "hledac.security.entropy_source" not in sys.modules:
        try:
            spec = importlib.util.spec_from_file_location(
                "hledac.security.entropy_source", ent_path
            )
            if spec is not None and spec.loader is not None:
                ent_mod = importlib.util.module_from_spec(spec)
                sys.modules["hledac.security.entropy_source"] = ent_mod
                spec.loader.exec_module(ent_mod)
                if hasattr(ent_mod, "M1EntropySource"):
                    setattr(sec, "M1EntropySource", ent_mod.M1EntropySource)
        except Exception:
            pass  # fail-soft

    for name, shim_path in (
        ("TemporalAnonymizer", "hledac.universal._shims.security_temporal_anonymizer"),
        ("ZeroAttributionEngine", "hledac.universal._shims.security_zero_attribution_engine"),
        ("KeyManager", "hledac.universal._shims.security_key_manager"),
        ("StealthEngine", "hledac.universal._shims.security_stealth_engine"),
        ("ThreatIntelligence", "hledac.universal._shims.security_threat_intelligence"),
        ("QuantumResistantCrypto", "hledac.universal._shims.security_quantum_resistant_crypto"),
        ("ZKPResearchEngine", "hledac.universal._shims.security_zkp_research_engine"),
    ):
        try:
            shim_mod = importlib.import_module(shim_path)
            if hasattr(shim_mod, name):
                setattr(sec, name, getattr(shim_mod, name))
        except Exception:
            pass  # fail-soft

    sec.__all__ = [  # type: ignore[attr-defined]
        "M1EntropySource", "TemporalAnonymizer", "ZeroAttributionEngine",
        "KeyManager", "StealthEngine", "ThreatIntelligence",
        "QuantumResistantCrypto", "ZKPResearchEngine",
    ]


def _bootstrap_core() -> None:
    """
    Wire up `hledac.core` using `hledac.universal.core` as canonical.
    """
    core_dir = os.path.join(_HLEDAC_ROOT, "core")
    if not os.path.isdir(core_dir):
        return
    core = _make_pkg_stub("hledac.core", core_dir)
    if core is None:
        return

    try:
        wd_module = importlib.import_module("hledac.universal.core.watchdog")
        core.Watchdog = wd_module.Watchdog  # type: ignore[attr-defined]
        core.watchdog = wd_module  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        mlx_module = importlib.import_module("hledac.universal.core.mlx_embeddings")
        core.mlx_embeddings = mlx_module  # type: ignore[attr-defined]
    except Exception:
        pass

    core.__all__ = ["Watchdog", "watchdog", "mlx_embeddings"]  # type: ignore[attr-defined]


def _bootstrap_advanced_web() -> None:
    """
    Wire up `hledac.advanced_web` using `hledac.universal.advanced_web`.
    """
    web_dir = os.path.join(_HLEDAC_ROOT, "advanced_web")
    if not os.path.isdir(web_dir):
        return
    web = _make_pkg_stub("hledac.advanced_web", web_dir)
    if web is None:
        return

    try:
        sb_mod = importlib.import_module(
            "hledac.universal.advanced_web.stealth_browser"
        )
        web.StealthBrowser = sb_mod.StealthBrowser  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        ao_mod = importlib.import_module(
            "hledac.universal.advanced_web.automation_orchestrator"
        )
        web.AutomationOrchestrator = ao_mod.AutomationOrchestrator  # type: ignore[attr-defined]
    except Exception:
        pass

    web.__all__ = ["StealthBrowser", "AutomationOrchestrator"]  # type: ignore[attr-defined]


def _bootstrap_advanced_rag() -> None:
    """
    Wire up `hledac.advanced_rag` using `hledac.universal.advanced_rag`.
    """
    rag_dir = os.path.join(_HLEDAC_ROOT, "advanced_rag")
    if not os.path.isdir(rag_dir):
        return
    rag = _make_pkg_stub("hledac.advanced_rag", rag_dir)
    if rag is None:
        return

    try:
        ro_mod = importlib.import_module(
            "hledac.universal.advanced_rag.rag_orchestrator"
        )
        rag.RAGOrchestrator = ro_mod.RAGOrchestrator  # type: ignore[attr-defined]
        rag.RAGResult = getattr(ro_mod, "RAGResult", None)  # type: ignore[attr-defined]
    except Exception:
        pass

    rag.__all__ = ["RAGOrchestrator", "RAGResult"]  # type: ignore[attr-defined]


def _bootstrap_coordinator_aliases() -> None:
    """
    Alias canonical coordinator class names onto the legacy names that
    some tests import by (`SecurityCoordinator` -> `UniversalSecurityCoordinator`,
    `ResearchCoordinator` -> `UniversalResearchCoordinator`).
    """
    try:
        sc_mod = importlib.import_module(
            "hledac.universal.coordinators.security_coordinator"
        )
        if hasattr(sc_mod, "UniversalSecurityCoordinator"):
            sc_mod.SecurityCoordinator = sc_mod.UniversalSecurityCoordinator  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        rc_mod = importlib.import_module(
            "hledac.universal.coordinators.research_coordinator"
        )
        if hasattr(rc_mod, "UniversalResearchCoordinator"):
            rc_mod.ResearchCoordinator = rc_mod.UniversalResearchCoordinator  # type: ignore[attr-defined]
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def ensure_namespace_paths() -> bool:
    """
    Idempotently extend the `hledac` namespace so sibling top-level
    packages are importable as `hledac.X` and the canonical shims
    under `hledac.universal._shims.*` are reachable as
    `hledac.security.*` / `hledac.core.*` / etc.

    Returns:
        True  -- this call performed the bootstrap (first invocation).
        False -- bootstrap already done; this was a no-op.

    The function is **fail-safe**: any failure inside the per-sibling
    bootstrap helpers is swallowed (those helpers never raise) and the
    rest of the bootstrap still runs.
    """
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return False

    _ensure_hledac_root()
    _inject_sys_path()
    _bootstrap_universal()
    _bootstrap_extended_siblings()
    _bootstrap_security()
    _bootstrap_core()
    _bootstrap_advanced_web()
    _bootstrap_advanced_rag()
    _bootstrap_coordinator_aliases()

    _BOOTSTRAPPED = True
    return True


__all__ = ["ensure_namespace_paths", "_HLEDAC_ROOT"]
