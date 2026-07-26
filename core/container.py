"""core/container.py — ServiceContainer: unified service registry for Hledac Universal.

F350M-R / A3: Nahrazuje 5 izolovaných registry/singletonů jedním konzistentním
kontraktem.

Registry které migrujeme:
  1. core/inference_coordinator  — _COORDINATOR singleton → container.get('inference.coordinator')
  2. core/isolated_executors     — 3 pool singletony     → container.get('executor.duckdb')
  3. core/rust_backend/_prober   — probe() cached       → container.get('rust.probe')
  4. core/capabilities.py        — CAPS.require()        → container.get('cap.<name>')

Scope hodnoty:
  'singleton' — jedna instance per container (default)
  'factory'   — nova instance pri kazdem get()

usage:
    from core.container import ServiceContainer, get_global_container

    # Registrace (typicky v bootstrapu)
    container = ServiceContainer()
    container.register('inference.coordinator', factory=create_coordinator, scope='singleton')
    container.register('executor.duckdb', factory=create_duckdb_pool, scope='singleton')

    # Získání (kdekoliv v kódu)
    coordinator = container.get('inference.coordinator')
    pool = container.try_get('executor.duckdb')  # None pokud není registrován

    # Global container pro kompatibilitu (existující kód)
    g = get_global_container()
    g.register('inference.coordinator', factory=create_coordinator, scope='singleton')

Python 3.14 note:
    - TYPE_CHECKING guard pro type hints
    - Ziadne dataclass/msgpack — plain class + __slots__ (výkon)
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any, Callable, Generic, TypeVar

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

__all__ = [
    "ServiceContainer",
    "get_global_container",
    "reset_global_container",
]


class ServiceContainer:
    """
    Lightweight service registry s podporou singleton a factory scope.

    Invarianty:
    - register() je idempotent pro stejny name+scope; duplicitni volani je no-op
    - get() na neregistrovany service haze KeyError
    - try_get() vraci None pro neregistrovany service
    - Ziadny service neni lazy-loaded automaticky — factory se vola pri prvnim get()
    - Thread-safe pro konkureni access (RWLock pres _lock)

    M1 8GB: ziadna alokace navic — plain Python objects, __slots__ kde možno.
    """

    __slots__ = (
        "_services",
        "_instances",
        "_lock",
        "_parent",
    )

    def __init__(
        self,
        parent: ServiceContainer | None = None,
    ) -> None:
        """
        Parameters
        ----------
        parent : ServiceContainer | None
            Parent container for hierarchical scoping (volitelne).
            try_get() deleguje na parent pokud local lookup failuje.
        """
        self._services: dict[str, _ServiceDesc] = {}
        self._instances: dict[str, Any] = {}  # name → resolved instance
        self._lock = threading.RLock()
        self._parent = parent

    # ── Public API ────────────────────────────────────────────────────────────

    def register(
        self,
        name: str,
        factory: Callable[..., Any],
        *,
        scope: str = "singleton",
        override: bool = False,
    ) -> None:
        """
        Registruj service.

        Parameters
        ----------
        name : str
            Unikatni identifikator (napr. 'inference.coordinator',
            'executor.duckdb', 'rust.probe').
        factory : Callable[..., Any]
            Zero-argument callable — muze byt async callable.
            Volana prave jednou per 'singleton' scope.
        scope : str
            'singleton' (default) nebo 'factory'.
        override : bool
            True = nahrad existujici registraci.
            False (default) = no-op pokud uz existuje.

        Raises
        ------
        ValueError
            scope neni 'singleton' ani 'factory'.
        """
        if scope not in ("singleton", "factory"):
            raise ValueError(f"scope must be 'singleton' or 'factory', got {scope!r}")

        with self._lock:
            if name in self._services and not override:
                # Idempotent no-op
                return
            self._services[name] = _ServiceDesc(factory=factory, scope=scope)
            # Clear cached instance if re-registering (override case)
            if override and name in self._instances:
                del self._instances[name]

    def get(self, name: str) -> Any:
        """
        Vrat instanci service (scope='singleton' = cached, scope='factory' = fresh).

        Raises
        ------
        KeyError
            Service neni registrován.
        """
        # Fast path: cached singleton (no lock needed for read of immutable dict)
        if name in self._instances:
            return self._instances[name]

        with self._lock:
            # Double-check po acquire
            if name in self._instances:
                return self._instances[name]

            desc = self._resolve_desc(name)
            if desc is None:
                raise KeyError(f"Service not registered: {name!r}")

            if desc.scope == "singleton":
                instance = desc.factory()
                self._instances[name] = instance
                return instance
            else:
                # factory scope — kazde volani vytvari novou instanci
                return desc.factory()

    def try_get(self, name: str) -> Any | None:
        """
        Vrat instanci nebo None pokud neni registrován.

        Preferujte try_get() pred get() pokud absence service je legální stav.
        """
        try:
            return self.get(name)
        except KeyError:
            return None

    def is_registered(self, name: str) -> bool:
        """True pokud service je registrován (lokalne nebo v parent)."""
        if name in self._services:
            return True
        if self._parent is not None:
            return self._parent.is_registered(name)
        return False

    def registered_names(self) -> list[str]:
        """Seznam vsech lokalne registrovanych jmen (ne rekurzivne do parent)."""
        with self._lock:
            return list(self._services.keys())

    def clear(self) -> None:
        """Smaze vsechny lokalni registrace a instance (ne parent)."""
        with self._lock:
            self._services.clear()
            self._instances.clear()

    # ── Internal ─────────────────────────────────────────────────────────────

    def _resolve_desc(self, name: str) -> _ServiceDesc | None:
        """Najdi _ServiceDesc — lokalne nebo v parent chainu."""
        if name in self._services:
            return self._services[name]
        if self._parent is not None:
            return self._parent._resolve_desc(name)
        return None

    def get_or_create_in_child(self, name: str, child: ServiceContainer) -> Any:
        """
        Vrat existujici instanci z parent containeru nebo vytvor v child.

        Použitelné pro per-sprint scope, kde chceme sdilet singleton
        z hlavního containeru ale mit vlastni cache pro sprint-specific data.

        Parameters
        ----------
        name : str
            Jmeno service z parent containeru.
        child : ServiceContainer
            Child container, ktery dostane local copy pokud je to singleton.

        Returns
        -------
        Any
            Existujici instance z parent (singleton) nebo nova z child factory.
        """
        with self._lock:
            desc = self._resolve_desc(name)
            if desc is None:
                raise KeyError(f"Service not registered: {name!r}")

            if desc.scope == "singleton":
                # Singleton z parent — vrat z child cache nebo vytvor
                if name in child._instances:
                    return child._instances[name]
                instance = desc.factory()
                child._instances[name] = instance
                child._services[name] = desc
                return instance
            else:
                return desc.factory()


# ── _ServiceDesc — internal descriptor ────────────────────────────────────────


class _ServiceDesc:
    """Internal descriptor pro jednu service registraci."""

    __slots__ = ("factory", "scope")

    def __init__(self, factory: Callable[..., Any], scope: str) -> None:
        self.factory = factory
        self.scope = scope


# ── Global container (backward-compat pro existujici kod) ─────────────────────


_GLOBAL_CONTAINER: ServiceContainer | None = None
_GLOBAL_CONTAINER_LOCK = threading.Lock()


def get_global_container() -> ServiceContainer:
    """
    Vrat globalni ServiceContainer singleton (process-wide).

    Lazy init — container se vytvari na prvnim volani.
    Použijte pro backward-compat nebo pro jednoduche pripady.
    Pro sprint-specific scoped container pouzijte ServiceContainer(parent=...).
    """
    global _GLOBAL_CONTAINER
    if _GLOBAL_CONTAINER is None:
        with _GLOBAL_CONTAINER_LOCK:
            if _GLOBAL_CONTAINER is None:
                _GLOBAL_CONTAINER = ServiceContainer()
                # A3: Seed global container with capabilities so ctx.container.get('cap.<name>')
                # works alongside CAPS.require(). Safe to call multiple times (idempotent).
                _seed_global_container(_GLOBAL_CONTAINER)
    return _GLOBAL_CONTAINER


def _seed_global_container(container: ServiceContainer) -> None:
    """Seed container with capabilities (A3). Called once on first global container creation."""
    try:
        import core.capabilities as cap_module

        # Iterate module globals — capabilities are defined as module-level variables (ZSTD, MLX, ...)
        for cap_name, cap_obj in vars(cap_module).items():
            if not isinstance(cap_obj, type) and hasattr(cap_obj, "name") and hasattr(cap_obj, "import_path"):
                try:

                    def _factory(c: Any = cap_obj) -> Any:
                        from core.capabilities import CAPS as _caps

                        return _caps.require(c)

                    container.register(f"cap.{cap_name}", factory=_factory, scope="singleton")
                except Exception:
                    pass
    except Exception:
        pass


def reset_global_container() -> None:
    """
    Reset global container — for testing only.

    Neni thread-safe pokud jine vlakno drzi referenci na stary container.
    Použijte pouze v testech.
    """
    global _GLOBAL_CONTAINER
    with _GLOBAL_CONTAINER_LOCK:
        _GLOBAL_CONTAINER = None
