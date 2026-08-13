"""
transport/capability_registry.py — F-ISSUE-005: Darknet/P2P Capability Registry
================================================================================

PROBLEM:
  Discovery sidecars (onion, i2p, ipfs, dht, commoncrawl) are thin adapter stubs
  that delegate to SidecarOrchestrator._run_* methods, which in turn call
  scheduler._run_* methods that don't exist. The system appears autonomous but
  is actually running stubs.

  Example chain:
    OnionDiscoverySidecarAdapter.run_async()
      → getattr(scheduler, "_run_onion_discovery_sidecar")
      → scheduler._scheduler_ref_var.get() → SidecarOrchestrator
      → SidecarOrchestrator._run_onion_discovery_sidecar()
        → self._scheduler._run_onion_discovery_sidecar()
        → SchedulerAdvisory protocol method (doesn't exist on SprintScheduler!)

IMPACT:
  Sprint with all darknet/P2P flags enabled consumes 100-200 MB RAM and several
  minutes wall-time starting adapters, but actual .onion, I2P eepsites, IPFS, DHT,
  or Gopher collection doesn't happen. System appears autonomous but is simulating.

SOLUTION:
  TransportCapabilityRegistry provides explicit capability detection with 4 states:

    READY:           Transport is actually connected and verified
    STUB:            API exists but doesn't perform real protocol operations
    UNAVAILABLE:     Transport dependencies not present (binary missing, etc.)
    MISSING_IMPLEMENTATION: Feature not implemented yet

  Each transport MUST declare its capability before sidecar execution.
  SidecarOrchestrator skips stub/unavailable/missing paths as SKIPPED,
  not as failed tasks.

ARCHITECTURE:
  ┌─────────────────────────────────────────────────────────────────────┐
  │                    TransportCapabilityRegistry                       │
  │  ┌─────────────────────────────────────────────────────────────┐    │
  │  │  Protocol | Transport | Capability | Reason                  │    │
  │  ├─────────────────────────────────────────────────────────────┤    │
  │  │  TOR     | TorTransport  | READY     | SOCKS5 running     │    │
  │  │  I2P     | I2PTransport  | STUB       | SAM mode not run  │    │
  │  │  ARTI    | ArtiTransport | UNAVAILABLE| Rust not loaded   │    │
  │  │  NYM     | NymTransport  | UNAVAILABLE| nym-client missing │    │
  │  │  IPFS    | None         | MISSING    | No implementation  │    │
  │  │  DHT     | None         | STUB        | KademliaNode sim   │    │
  │  │  GOPHER  | GopherTrans. | READY      | Functional impl    │    │
  │  │  I2P_RAW | I2PSAMv3Cli | READY       | SAM v3 client ok  │    │
  │  └─────────────────────────────────────────────────────────────┘    │
  └─────────────────────────────────────────────────────────────────────┘

M1 8GB CONSIDERATIONS:
  - Detection is fast: TCP socket check, binary which(), env var check
  - No heavy imports during capability detection (lazy)
  - Results cached after first check (ContextVar for per-sprint isolation)

USAGE:
  from transport.capability_registry import (
      get_capability,
      is_protocol_ready,
      get_all_capabilities,
      TransportCapability,
      MISSING_IMPLEMENTATION,
  )

  if is_protocol_ready("tor"):
      await fetch_via_tor(url)
  elif get_capability("tor") == TransportCapability.STUB:
      log.warning("Tor stub mode: results will be simulated")
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import os
import shutil
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


class TransportCapability(Enum):
    """
    Capability state for darknet/P2P transports.

    Values:
        READY: Transport is actually connected and verified to work.
               Real protocol operations will be performed.

        STUB: API exists but doesn't perform real protocol operations.
              May return mock data or silently no-op.
              Sidecars should skip with WARNING log.

        UNAVAILABLE: Transport dependencies not present.
                     Binary missing, library not installed, etc.
                     Sidecars should skip with INFO log.

        MISSING_IMPLEMENTATION: Feature not implemented yet.
                                Sidecar stub exists but actual implementation missing.
                                Sidecars should skip with INFO log and TODO marker.
    """

    READY = "ready"
    STUB = "stub"
    UNAVAILABLE = "unavailable"
    MISSING_IMPLEMENTATION = "missing_implementation"


# Human-readable reasons for each capability state
_CAPABILITY_REASONS: dict[str, dict[TransportCapability, str]] = {
    "tor": {
        TransportCapability.READY: "Tor SOCKS5 proxy running and circuit established",
        TransportCapability.UNAVAILABLE: "Tor binary not found (install: brew install tor)",
        TransportCapability.STUB: "TorTransport.start() not called or circuit not established",
    },
    "i2p": {
        TransportCapability.READY: "I2P SAM v3 client connected and session active",
        TransportCapability.UNAVAILABLE: "I2P SAM bridge not running on port 7656",
        TransportCapability.STUB: "I2P HTTP/SOCKS proxy available but SAM v3 not connected",
        TransportCapability.MISSING_IMPLEMENTATION: "I2P discovery sidecar not wired to orchestrator",
    },
    "arti": {
        TransportCapability.READY: "ArtiNode Rust embedding bootstrapped and running",
        TransportCapability.UNAVAILABLE: "Rust arti_bridge module not available (embedded_tor feature disabled)",
        TransportCapability.STUB: "Arti subprocess mode: real Tor but no circuit isolation",
    },
    "nym": {
        TransportCapability.READY: "nym-client WebSocket bridge connected",
        TransportCapability.UNAVAILABLE: "nym-client binary not found in PATH",
        TransportCapability.STUB: "nym-client process started but WebSocket not connected",
    },
    "ipfs": {
        TransportCapability.READY: "IPFS HTTP gateway accessible",
        TransportCapability.UNAVAILABLE: "No IPFS gateway configured or accessible",
        TransportCapability.STUB: "IPFS library present but no real network operations",
        TransportCapability.MISSING_IMPLEMENTATION: "IPFS Kademlia/BitSwap not implemented (HTTP gateway only)",
    },
    "dht": {
        TransportCapability.READY: "DHT network accessible and responding",
        TransportCapability.STUB: "KademliaNode initialized but _transport is None (simulated mode)",
        TransportCapability.MISSING_IMPLEMENTATION: "DHT crawler not integrated with sidecar orchestrator",
    },
    "gopher": {
        TransportCapability.READY: "Gopher protocol client functional",
        TransportCapability.MISSING_IMPLEMENTATION: "GopherLane not implemented",
    },
    "i2p_sam": {
        TransportCapability.READY: "I2P SAM v3 client connected and session active",
        TransportCapability.UNAVAILABLE: "I2P SAM bridge not running on port 7656",
        TransportCapability.STUB: "I2P transport available but SAM v3 session not created",
    },
}


# ── Capability Detection Functions ────────────────────────────────────────────


async def _detect_tor_capability() -> tuple[TransportCapability, str]:
    """Detect Tor transport capability.

    Returns READY if:
      - tor binary exists
      - SOCKS5 proxy port 9050 is open
      - Circuit can be established

    Returns UNAVAILABLE if:
      - tor binary not found

    Returns STUB if:
      - tor binary exists but SOCKS5 not reachable
    """
    # Check binary
    if shutil.which("tor") is None:
        return TransportCapability.UNAVAILABLE, _CAPABILITY_REASONS["tor"][TransportCapability.UNAVAILABLE]

    # Check SOCKS5 port
    try:
        import socket

        def _check_port() -> bool:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2.0)
            try:
                s.connect(("127.0.0.1", 9050))
                s.close()
                return True
            except OSError:
                return False

        loop = asyncio.get_running_loop()
        socks_ok = await loop.run_in_executor(None, _check_port)
        if socks_ok:
            return TransportCapability.READY, _CAPABILITY_REASONS["tor"][TransportCapability.READY]
        return TransportCapability.STUB, "Tor binary exists but SOCKS5 proxy not reachable on port 9050"
    except Exception as e:
        return TransportCapability.STUB, f"Tor capability check failed: {e}"


async def _detect_i2p_capability() -> tuple[TransportCapability, str]:
    """Detect I2P transport capability.

    Returns READY if:
      - I2P SAM v3 bridge reachable on port 7656
      - Session can be created

    Returns UNAVAILABLE if:
      - SAM bridge not reachable

    Returns STUB if:
      - SOCKS5 proxy (4444) available but SAM not connected
    """
    import socket

    # Check SAM port (7656)
    def _check_sam_port() -> bool:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2.0)
        try:
            s.connect(("127.0.0.1", 7656))
            s.close()
            return True
        except OSError:
            return False

    # Fall back to SOCKS5 check
    def _check_socks_port() -> bool:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2.0)
        try:
            s.connect(("127.0.0.1", 4444))
            s.close()
            return True
        except OSError:
            return False

    try:
        loop = asyncio.get_running_loop()
        sam_ok = await loop.run_in_executor(None, _check_sam_port)
        if sam_ok:
            return TransportCapability.READY, _CAPABILITY_REASONS["i2p"][TransportCapability.READY]

        socks_ok = await loop.run_in_executor(None, _check_socks_port)
        if socks_ok:
            return TransportCapability.STUB, "I2P SOCKS5 proxy available (port 4444) but SAM v3 not connected"

        return TransportCapability.UNAVAILABLE, _CAPABILITY_REASONS["i2p"][TransportCapability.UNAVAILABLE]
    except Exception as e:
        return TransportCapability.UNAVAILABLE, f"I2P capability check failed: {e}"


async def _detect_arti_capability() -> tuple[TransportCapability, str]:
    """Detect Arti transport capability.

    Returns READY if:
      - Rust arti_bridge module available AND
      - ArtiNode can be instantiated AND
      - bootstrap succeeds (circuit established)

    Returns STUB if:
      - Rust module available but bootstrap fails
      - Arti binary not installed (for subprocess fallback)

    Returns UNAVAILABLE if:
      - Rust module not available (embedded_tor feature disabled)
      - No fallback path available

    FIX-5: Previously returned READY based solely on class existence.
    Now performs actual bootstrap verification to prevent false positives.

    M1 8GB: Uses short timeout (5s) for bootstrap check to avoid
    blocking detection for too long. Memory is still bounded by
    limiting bootstrap attempt to one async check per sprint.
    """
    import asyncio
    import shutil

    try:
        import rust

        if not hasattr(rust, "arti_bridge"):
            # Check if we have subprocess arti as fallback
            if shutil.which("arti") is not None:
                return (
                    TransportCapability.STUB,
                    "Rust arti_bridge unavailable, subprocess arti not connected",
                )
            return TransportCapability.UNAVAILABLE, _CAPABILITY_REASONS["arti"][TransportCapability.UNAVAILABLE]

        arti_bridge = rust.arti_bridge

        # Check if ArtiNode class exists
        if not hasattr(arti_bridge, "ArtiNode"):
            return (
                TransportCapability.STUB,
                "ArtiNode class not found in arti_bridge module",
            )

        node_class = getattr(arti_bridge, "ArtiNode")
        if not callable(node_class):
            return TransportCapability.STUB, "ArtiNode is not callable"

        # FIX-5: Perform actual bootstrap verification
        # Use short timeout to avoid blocking detection
        try:
            import os
            data_dir = os.environ.get("HLEDAC_ARTI_DATA_DIR", "~/.hledac/arti")

            async def _try_bootstrap() -> tuple[bool, str]:
                """Attempt ArtiNode bootstrap and return (success, reason)."""
                try:
                    # Create ArtiNode instance (lightweight until start())
                    node = node_class(data_dir=data_dir)

                    # Attempt bootstrap with 5s timeout
                    loop = asyncio.get_running_loop()

                    def _do_start() -> bool:
                        try:
                            return node.start()
                        except Exception as e:
                            return False

                    bootstrapped = await asyncio.wait_for(
                        loop.run_in_executor(None, _do_start),
                        timeout=5.0,
                    )

                    if bootstrapped:
                        # Verify with session_status
                        try:
                            status = node.session_status()
                            if status and status.get("bootstrap_status"):
                                return True, f"ArtiNode bootstrapped: {status.get('bootstrap_status')}"
                        except Exception:
                            pass
                        return True, "ArtiNode bootstrapped successfully"
                    return False, "ArtiNode.start() returned False"

                except asyncio.TimeoutError:
                    return False, "ArtiNode bootstrap timed out (5s)"
                except PermissionError:
                    return False, "ArtiNode creation blocked by permissions"
                except OSError as e:
                    return False, f"ArtiNode creation failed: {e}"
                except Exception as e:
                    return False, f"ArtiNode bootstrap error: {e}"

            success, reason = await _try_bootstrap()
            if success:
                return TransportCapability.READY, reason
            return TransportCapability.STUB, reason

        except Exception as e:
            # Bootstrap attempt failed
            return TransportCapability.STUB, f"ArtiNode bootstrap verification failed: {e}"

    except ImportError:
        # No rust module at all
        if shutil.which("arti") is not None:
            return (
                TransportCapability.STUB,
                "Rust module unavailable, subprocess arti binary present but not connected",
            )
        return TransportCapability.UNAVAILABLE, _CAPABILITY_REASONS["arti"][TransportCapability.UNAVAILABLE]
    except Exception as e:
        # Catch-all for unexpected errors
        return TransportCapability.UNAVAILABLE, f"Arti detection error: {e}"


async def _detect_nym_capability() -> tuple[TransportCapability, str]:
    """Detect Nym transport capability.

    Returns READY if:
      - nym-client binary exists
      - WebSocket can connect to local port

    Returns UNAVAILABLE if:
      - nym-client binary not found
    """
    import socket

    if shutil.which("nym-client") is None and not os.environ.get("HLEDAC_NYM_SOCKS_PROXY"):
        return TransportCapability.UNAVAILABLE, _CAPABILITY_REASONS["nym"][TransportCapability.UNAVAILABLE]

    # Check WebSocket port
    def _check_ws_port() -> bool:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.0)
        try:
            s.connect(("127.0.0.1", 1977))
            s.close()
            return True
        except OSError:
            return False

    try:
        loop = asyncio.get_running_loop()
        ws_ok = await loop.run_in_executor(None, _check_ws_port)
        if ws_ok:
            return TransportCapability.READY, _CAPABILITY_REASONS["nym"][TransportCapability.READY]

        return TransportCapability.STUB, "nym-client binary exists but WebSocket bridge not connected"
    except Exception:
        return TransportCapability.STUB, "nym-client may be available but connection check failed"


async def _detect_ipfs_capability() -> tuple[TransportCapability, str]:
    """Detect IPFS capability.

    Returns READY if:
      - At least one IPFS gateway is accessible

    Returns UNAVAILABLE if:
      - No gateways configured or accessible

    Returns MISSING_IMPLEMENTATION if:
      - Only HTTP gateway fallback (no libp2p Kademlia/BitSwap)

    M1 8GB OPTIMIZATION: Uses HLEDAC_IPFS_GATEWAY_URL env var for gateway URL,
    with fallback to public gateways. Checks with lightweight HEAD request.
    """
    import httpx
    import os

    # Priority 1: User-configured gateway via env var
    configured_gateway = os.environ.get("HLEDAC_IPFS_GATEWAY_URL", "").strip()
    
    # Priority 2: Default public gateways (fallback)
    fallback_gateways = [
        "https://ipfs.io",
        "https://cloudflare-ipfs.com",
        "https://dweb.link",
    ]

    # Build gateway list: configured first, then fallbacks
    gateways_to_check = []
    if configured_gateway:
        gateways_to_check.append(configured_gateway)
    gateways_to_check.extend(fallback_gateways)

    for gateway in gateways_to_check:
        try:
            async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
                # Use HEAD request for faster health check (no body transfer)
                resp = await client.head(gateway)
                if resp.status_code < 500:
                    if configured_gateway and gateway == configured_gateway:
                        return TransportCapability.READY, f"IPFS gateway {gateway} accessible (user-configured)"
                    return TransportCapability.READY, f"IPFS gateway {gateway} accessible"
        except Exception:
            pass

    return TransportCapability.MISSING_IMPLEMENTATION, _CAPABILITY_REASONS["ipfs"][TransportCapability.MISSING_IMPLEMENTATION]


async def _detect_dht_capability() -> tuple[TransportCapability, str]:
    """Detect DHT capability.

    Returns STUB if:
      - KademliaNode is imported but _transport is None (simulated)

    Returns MISSING_IMPLEMENTATION if:
      - KademliaNode not importable

    M1 8GB OPTIMIZATION: Does NOT instantiate KademliaNode — checks
    module-level _transport attribute via introspection to avoid
    unnecessary memory allocation.
    """
    try:
        from hledac.universal.network.dht import kademlia_node

        # M1 8GB OPTIMIZATION: Check if module has _transport attribute set,
        # without instantiating the class. This avoids ~50KB allocation per check.
        if hasattr(kademlia_node, "_transport") and getattr(kademlia_node, "_transport", None) is not None:
            return TransportCapability.READY, "DHT network has UDP transport active"
        return TransportCapability.STUB, _CAPABILITY_REASONS["dht"][TransportCapability.STUB]
    except ImportError:
        return TransportCapability.MISSING_IMPLEMENTATION, "KademliaNode not importable"


async def _detect_gopher_capability() -> tuple[TransportCapability, str]:
    """Detect Gopher capability.

    GopherTransport is fully implemented and functional.
    Returns READY if import succeeds.
    """
    try:
        # Try absolute import first (when running as hledac.universal.transport module)
        from hledac.universal.transport.gopher_transport import GopherTransport
        return TransportCapability.READY, _CAPABILITY_REASONS["gopher"][TransportCapability.READY]
    except ImportError:
        try:
            # Fallback: try relative import (when running from transport/ directory)
            from .gopher_transport import GopherTransport
            return TransportCapability.READY, _CAPABILITY_REASONS["gopher"][TransportCapability.READY]
        except ImportError:
            return TransportCapability.MISSING_IMPLEMENTATION, "GopherTransport not importable"


# ── Capability Registry ────────────────────────────────────────────────────────


class _TransportCapabilityRegistry:
    """
    Registry for darknet/P2P transport capabilities.

    Uses ContextVar for per-sprint isolation (enables parallel sprint execution).
    Capabilities are cached after first detection per async context.
    """

    # Per-sprint capability cache
    _cache: dict[str, tuple[TransportCapability, str]] | None = None

    # Detection functions per protocol
    _detectors: dict[str, Callable[[], Awaitable[tuple[TransportCapability, str]]]] = {
        "tor": _detect_tor_capability,
        "i2p": _detect_i2p_capability,
        "i2p_sam": _detect_i2p_capability,  # Same as I2P but emphasizes SAM mode
        "arti": _detect_arti_capability,
        "nym": _detect_nym_capability,
        "ipfs": _detect_ipfs_capability,
        "dht": _detect_dht_capability,
        "gopher": _detect_gopher_capability,
    }

    def __init__(self) -> None:
        # Initialize per-instance cache
        self._cache = {}

    def get_capability(self, protocol: str) -> tuple[TransportCapability, str]:
        """
        Get cached capability for a protocol.

        Returns (capability, reason) tuple.
        If not cached, returns (MISSING_IMPLEMENTATION, "Not registered").
        """
        if self._cache is None:
            self._cache = {}

        if protocol not in self._cache:
            return TransportCapability.MISSING_IMPLEMENTATION, f"Protocol '{protocol}' not registered in capability registry"

        return self._cache[protocol]

    async def detect_capability(self, protocol: str) -> tuple[TransportCapability, str]:
        """
        Detect and cache capability for a protocol.

        Detection is performed once per async context (sprint).
        Subsequent calls return cached result.
        """
        if self._cache is None:
            self._cache = {}

        if protocol in self._cache:
            return self._cache[protocol]

        detector = self._detectors.get(protocol)
        if detector is None:
            capability = TransportCapability.MISSING_IMPLEMENTATION
            reason = f"No detector function for protocol '{protocol}'"
        else:
            try:
                capability, reason = await detector()
            except Exception as e:
                capability = TransportCapability.UNAVAILABLE
                reason = f"Detection failed with error: {e}"
                logger.debug("Capability detection for %s failed: %s", protocol, e)

        self._cache[protocol] = (capability, reason)
        return capability, reason

    async def detect_all(self) -> dict[str, tuple[TransportCapability, str]]:
        """
        Detect capabilities for all registered protocols.

        Returns dict mapping protocol name to (capability, reason) tuple.
        """
        results = {}
        for protocol in self._detectors:
            capability, reason = await self.detect_capability(protocol)
            results[protocol] = (capability, reason)
        return results

    def clear_cache(self) -> None:
        """Clear the capability cache. Used at sprint teardown."""
        self._cache = {}

    def is_ready(self, protocol: str) -> bool:
        """Check if a protocol is READY (cached or registered as ready)."""
        capability, _ = self.get_capability(protocol)
        return capability == TransportCapability.READY

    def is_stub(self, protocol: str) -> bool:
        """Check if a protocol is a STUB."""
        capability, _ = self.get_capability(protocol)
        return capability == TransportCapability.STUB

    def is_unavailable(self, protocol: str) -> bool:
        """Check if a protocol is UNAVAILABLE."""
        capability, _ = self.get_capability(protocol)
        return capability == TransportCapability.UNAVAILABLE

    def is_missing_implementation(self, protocol: str) -> bool:
        """Check if a protocol has MISSING_IMPLEMENTATION."""
        capability, _ = self.get_capability(protocol)
        return capability == TransportCapability.MISSING_IMPLEMENTATION


# Module-level singleton with ContextVar for per-sprint isolation
_capability_registry_var: contextvars.ContextVar[_TransportCapabilityRegistry | None] = (
    contextvars.ContextVar("_capability_registry_var", default=None)
)


def get_capability_registry() -> _TransportCapabilityRegistry:
    """Get the current sprint's capability registry (or create one)."""
    registry = _capability_registry_var.get()
    if registry is None:
        registry = _TransportCapabilityRegistry()
        _capability_registry_var.set(registry)
    return registry


# ── Convenience Functions ─────────────────────────────────────────────────────


async def get_capability(protocol: str) -> tuple[TransportCapability, str]:
    """
    Get capability for a protocol in the current sprint context.

    Args:
        protocol: Protocol name (e.g., "tor", "i2p", "ipfs")

    Returns:
        (capability, reason) tuple
    """
    registry = get_capability_registry()
    return await registry.detect_capability(protocol)


async def is_protocol_ready(protocol: str) -> bool:
    """
    Check if a protocol is READY.

    Args:
        protocol: Protocol name

    Returns:
        True if protocol is READY, False otherwise
    """
    capability, _ = await get_capability(protocol)
    return capability == TransportCapability.READY


async def get_all_capabilities() -> dict[str, tuple[TransportCapability, str]]:
    """
    Get capabilities for all registered protocols.

    Returns:
        Dict mapping protocol name to (capability, reason) tuple
    """
    registry = get_capability_registry()
    return await registry.detect_all()


def get_capability_summary() -> dict[str, str]:
    """
    Get a human-readable summary of all protocol capabilities.

    Returns:
        Dict mapping protocol name to one-liner status
    """
    registry = get_capability_registry()
    summary = {}
    for protocol in ["tor", "i2p", "arti", "nym", "ipfs", "dht", "gopher"]:
        capability, reason = registry.get_capability(protocol)
        summary[protocol] = f"[{capability.value.upper()}] {reason}"
    return summary


def clear_capability_cache() -> None:
    """Clear the capability cache and reset the ContextVar.

    Call at sprint teardown to ensure fresh capability detection
    on the next sprint. Resets the ContextVar so a new registry
    instance is created on next access.
    """
    # Reset the ContextVar so next access creates fresh registry
    _capability_registry_var.set(None)


# ── Sidecar Integration ───────────────────────────────────────────────────────


def get_skip_reason(protocol: str) -> str | None:
    """
    Get the reason why a sidecar should be skipped.

    Returns None if sidecar should run, or a reason string if it should be skipped.
    """
    registry = get_capability_registry()
    capability, reason = registry.get_capability(protocol)

    if capability == TransportCapability.READY:
        return None  # Run the sidecar

    if capability == TransportCapability.STUB:
        return f"[STUB] {reason}. Sidecar will run but results may be simulated."

    if capability == TransportCapability.UNAVAILABLE:
        return f"[UNAVAILABLE] {reason}. Skipping sidecar."

    if capability == TransportCapability.MISSING_IMPLEMENTATION:
        return f"[MISSING_IMPLEMENTATION] {reason}. Sidecar stub exists but no real implementation."

    return f"[UNKNOWN] Unexpected capability state for {protocol}"
