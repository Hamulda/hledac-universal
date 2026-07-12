"""
Plugin registry for capability adapters.

Provides CapabilityPluginRegistry — a lightweight registration layer for
capability plugins that validates module presence via importlib.util.find_spec()
without triggering any network I/O or full module imports.
"""
import importlib.util
import os
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class CapabilityRegistration:
    """Immutable registration record for a single capability."""
    module_spec: str
    env_gate: str | None
    api_key: str | None
    env_enabled: bool
    module_found: bool

class CapabilityPluginRegistry:
    """
    Lightweight plugin registry for capability adapters.

    Validates module presence via ``importlib.util.find_spec()`` only —
    no network I/O, no full module imports at registration time.

    The registry is purely declarative: ``register_capability()`` records
    the metadata; actual capability loading is handled by
    ``CapabilityRegistry.load()`` in ``capabilities.py``.
    """
    __slots__ = tuple(('_registrations',))

    def __init__(self) -> None:
        self._registrations: dict[str, CapabilityRegistration] = {}

    def register_capability(self, cap: str, *, module_spec: str, env_gate: str | None=None, api_key: str | None=None) -> None:
        """
        Register a capability adapter.

        Args:
            cap: Capability identifier (e.g. "graph_rag", "bgp").
            module_spec: Fully-qualified module path to validate
                (e.g. "hledac.universal.knowledge.rag_engine").
            env_gate: Environment variable name that gates availability
                (e.g. "HLEDAC_ENABLE_BGP"). If None, capability is always
                considered env-enabled.
            api_key: Environment variable name containing the required API key.
                If None, no API key is required. If set, the capability is
                only available when the variable is non-empty.

        Raises:
            ValueError: If ``module_spec`` is not a valid Python identifier
                or ``cap`` is empty.
        """
        if not cap:
            raise ValueError('cap must be non-empty')
        if not module_spec or not module_spec.replace('.', '_').isidentifier():
            raise ValueError(f'module_spec must be a valid Python identifier, got {module_spec!r}')
        env_enabled = True
        if env_gate:
            env_enabled = os.environ.get(env_gate, '').lower() in ('1', 'true', 'yes', 'on')
        spec = importlib.util.find_spec(module_spec)
        module_found = spec is not None
        api_key_present = True
        if api_key:
            api_key_present = bool(os.environ.get(api_key, '').strip())
        final_env_enabled = env_enabled and api_key_present and module_found
        self._registrations[cap] = CapabilityRegistration(module_spec=module_spec, env_gate=env_gate, api_key=api_key, env_enabled=final_env_enabled, module_found=module_found)

    def is_registered(self, cap: str) -> bool:
        """Check whether a capability has been registered."""
        return cap in self._registrations

    def get(self, cap: str) -> CapabilityRegistration | None:
        """Return the registration record for a capability, or None if not registered."""
        return self._registrations.get(cap)

    def registrations(self) -> dict[str, CapabilityRegistration]:
        """Return a copy of all registrations."""
        return dict(self._registrations)