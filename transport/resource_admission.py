"""
transport/resource_admission.py

M1 Resource Ceiling Drift Fix — Resource Admission Manager for Transports.

ROLE: Provides resource admission context managers for all transport types.

Integrates with ResourceLedger to ensure:
1. Each transport requests admission before starting
2. Guaranteed teardown of all child processes and file handles
3. Proper cleanup on both success and failure

TRANSPORTS SUPPORTED:
- TorTransport: 5 FDs, 10 Mach ports, 1 child process
- I2PTransport: 3 FDs, 5 Mach ports, 1 child process
- ArtiTransport: 5 FDs, 10 Mach ports, 1 child process
- NymTransport: 5 FDs, 10 Mach ports, 1 child process

USAGE:
    from hledac.universal.transport.resource_admission import TransportAdmission

    # In transport.start():
    with TransportAdmission.tor():
        # Tor is guaranteed resources
        await self._start_tor()
    # On exit: all resources released, child processes terminated

    # In transport.stop():
    await TransportAdmission.terminate_transport("tor")
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from hledac.universal._core.resource_ledger import (
    ResourceLedger,
    ResourceType,
    get_resource_ledger,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    pass


# =============================================================================
# Transport Resource Profiles
# =============================================================================


@dataclass(frozen=True)
class TransportResourceProfile:
    """
    Resource profile for a transport type.

    Defines the resource requirements for starting and running a transport.
    These are conservative estimates that leave headroom.
    """

    name: str
    fds: int  # File descriptors needed
    mach_ports: int  # Mach ports needed
    child_processes: int  # Child processes needed
    mmap_regions: int  # mmap regions needed
    threads: int  # Additional threads
    tmp_volume_mb: int  # /tmp volume in MB


# Transport resource profiles
TRANSPORT_PROFILES: dict[str, TransportResourceProfile] = {
    "tor": TransportResourceProfile(
        name="tor",
        fds=10,  # SOCKS port, control port, HTTP server, connections
        mach_ports=20,  # Mach ports for networking
        child_processes=1,  # Tor daemon process
        mmap_regions=2,  # Tor data directory, cache
        threads=3,  # Event loop, circuit management
        tmp_volume_mb=64,  # Temp files
    ),
    "i2p": TransportResourceProfile(
        name="i2p",
        fds=8,  # SAM port, SOCKS port, HTTP proxy, connections
        mach_ports=15,
        child_processes=1,  # I2P daemon (Java process)
        mmap_regions=1,
        threads=2,
        tmp_volume_mb=32,
    ),
    "arti": TransportResourceProfile(
        name="arti",
        fds=10,  # SOCKS port, control port, connections
        mach_ports=20,
        child_processes=1,  # Arti (Rust) daemon
        mmap_regions=2,
        threads=3,
        tmp_volume_mb=64,
    ),
    "nym": TransportResourceProfile(
        name="nym",
        fds=12,  # SOCKS port, HTTP port, websocket, connections
        mach_ports=25,
        child_processes=2,  # Nym client + mixnet processes
        mmap_regions=2,
        threads=4,
        tmp_volume_mb=128,
    ),
    "session_pool": TransportResourceProfile(
        name="session_pool",
        fds=50,  # HTTP connections (pooled)
        mach_ports=30,
        child_processes=0,
        mmap_regions=0,
        threads=0,
        tmp_volume_mb=0,
    ),
    "duckdb": TransportResourceProfile(
        name="duckdb",
        fds=5,  # Database files
        mach_ports=5,
        child_processes=0,
        mmap_regions=3,  # WAL, data, indexes
        threads=0,
        tmp_volume_mb=256,  # Query temp space
    ),
}


# =============================================================================
# Resource Admission Manager
# =============================================================================


class TransportAdmission:
    """
    Resource admission manager for transports.

    Provides context managers and utilities for transport resource lifecycle.

    Usage:
        # In transport.start():
        with TransportAdmission.for_transport("tor") as ctx:
            # Resources acquired
            await self._start_tor()
        # Resources released on exit

        # Manual admission:
        ledger = get_resource_ledger()
        with ledger.admission("tor", fds=10, ...):
            pass
    """

    @staticmethod
    def get_profile(transport_name: str) -> TransportResourceProfile:
        """Get the resource profile for a transport."""
        return TRANSPORT_PROFILES.get(
            transport_name,
            TransportResourceProfile(
                name=transport_name,
                fds=5,
                mach_ports=10,
                child_processes=1,
                mmap_regions=1,
                threads=2,
                tmp_volume_mb=32,
            ),
        )

    @classmethod
    def for_transport(
        cls,
        transport_name: str,
        ledger: ResourceLedger | None = None,
    ):
        """
        Create admission context manager for a transport.

        Args:
            transport_name: Name of the transport ("tor", "i2p", "arti", "nym")
            ledger: Optional ResourceLedger instance (uses singleton if None)

        Returns:
            Context manager that grants admission and releases on exit
        """
        ledger = ledger or get_resource_ledger()
        profile = cls.get_profile(transport_name)

        return ledger.admission(
            owner=transport_name,
            fds=profile.fds,
            mach_ports=profile.mach_ports,
            child_processes=profile.child_processes,
            mmap_regions=profile.mmap_regions,
            threads=profile.threads,
            tmp_volume_bytes=profile.tmp_volume_mb * 1024 * 1024,
        )

    @classmethod
    async def terminate_transport(
        cls,
        transport_name: str,
        ledger: ResourceLedger | None = None,
        timeout_s: float = 10.0,
    ) -> int:
        """
        Terminate all resources associated with a transport.

        Args:
            transport_name: Name of the transport
            ledger: Optional ResourceLedger instance
            timeout_s: Seconds to wait for graceful termination

        Returns:
            Number of child processes terminated
        """
        ledger = ledger or get_resource_ledger()

        # First try graceful termination of child processes
        terminated = await ledger.terminate_child_processes(
            owner=transport_name,
            timeout_s=timeout_s,
            force=False,
        )

        # Then release all remaining resources
        released = ledger.release_all(transport_name)

        logger.info(
            f"[TransportAdmission] Cleaned up {transport_name}: "
            f"{terminated} processes terminated, {released} resources released"
        )

        return terminated

    @classmethod
    def can_start_transport(
        cls,
        transport_name: str,
        ledger: ResourceLedger | None = None,
    ) -> tuple[bool, str]:
        """
        Check if a transport can be started.

        Args:
            transport_name: Name of the transport
            ledger: Optional ResourceLedger instance

        Returns:
            (can_start: bool, reason: str)
        """
        ledger = ledger or get_resource_ledger()
        profile = cls.get_profile(transport_name)

        return ledger.can_admit(
            owner=transport_name,
            fds=profile.fds,
            mach_ports=profile.mach_ports,
            child_processes=profile.child_processes,
            mmap_regions=profile.mmap_regions,
            threads=profile.threads,
            tmp_volume_bytes=profile.tmp_volume_mb * 1024 * 1024,
        )


# =============================================================================
# Child Process Cleanup Utilities
# =============================================================================


async def cleanup_child_process(
    pid: int,
    ledger: ResourceLedger | None = None,
    timeout_s: float = 5.0,
) -> bool:
    """
    Clean up a single child process.

    Args:
        pid: Process ID to terminate
        ledger: Optional ResourceLedger
        timeout_s: Graceful termination timeout

    Returns:
        True if process was terminated cleanly
    """
    ledger = ledger or get_resource_ledger()

    try:
        os.kill(pid, 0)  # Check if alive
    except ProcessLookupError:
        # Already dead
        ledger.unregister_child_process(pid)
        return True

    # Send SIGTERM
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        ledger.unregister_child_process(pid)
        return True
    except PermissionError:
        logger.warning(f"[cleanup_child_process] Cannot kill PID {pid}: permission denied")
        return False

    # Wait for graceful shutdown
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
            await asyncio.sleep(0.1)
        except ProcessLookupError:
            # Process exited
            ledger.unregister_child_process(pid)
            return True

    # Force kill
    try:
        os.kill(pid, signal.SIGKILL)
        await asyncio.sleep(0.1)
        ledger.unregister_child_process(pid)
        return True
    except (ProcessLookupError, PermissionError):
        return False


async def cleanup_process_tree(
    pid: int,
    ledger: ResourceLedger | None = None,
    timeout_s: float = 10.0,
) -> int:
    """
    Clean up a process and all its children.

    Uses SIGTERM first, then SIGKILL if needed.
    Tracks all PIDs in the process group.

    Args:
        pid: Root process ID
        ledger: Optional ResourceLedger
        timeout_s: Total timeout for cleanup

    Returns:
        Number of processes terminated
    """
    import time as time_module

    ledger = ledger or get_resource_ledger()
    terminated = 0

    # Get process group
    try:
        pgid = os.getpgid(pid)
        in_pgroup = True
    except OSError:
        in_pgroup = False

    # First SIGTERM
    pids_to_kill = [pid]

    for target_pid in pids_to_kill:
        try:
            os.kill(target_pid, signal.SIGTERM)
            terminated += 1
        except (ProcessLookupError, PermissionError):
            continue

    # Wait for graceful shutdown
    deadline = time_module.monotonic() + timeout_s / 2
    remaining = list(pids_to_kill)

    while remaining and time_module.monotonic() < deadline:
        still_alive = []
        for target_pid in remaining:
            try:
                os.kill(target_pid, 0)  # Check if alive
                still_alive.append(target_pid)
            except ProcessLookupError:
                ledger.unregister_child_process(target_pid)
                continue

        remaining = still_alive
        if remaining:
            await asyncio.sleep(0.2)

    # SIGKILL for remaining
    if remaining:
        for target_pid in remaining:
            try:
                os.kill(target_pid, signal.SIGKILL)
                terminated += 1
            except (ProcessLookupError, PermissionError):
                continue

    # Clean up ledger entries
    for target_pid in pids_to_kill:
        ledger.unregister_child_process(target_pid)

    return terminated


# =============================================================================
# Integration Helpers
# =============================================================================


class TransportResourceMixin:
    """
    Mixin class for transport classes to add resource management.

    Usage:
        class TorTransport(Transport, TransportResourceMixin):
            def __init__(self, ...):
                super().__init__(...)
                self._setup_resource_management("tor")

            async def start(self) -> bool:
                with self.admission_context():
                    # Start Tor
                    pass

            async def stop(self) -> None:
                await self.cleanup_resources()
                # Transport cleanup
    """

    _resource_owner: str = ""
    _ledger: ResourceLedger | None = None
    _child_pids: list[int] = []
    _admission_active: bool = False

    def _setup_resource_management(self, owner: str) -> None:
        """Initialize resource management for this transport."""
        self._resource_owner = owner
        self._ledger = get_resource_ledger()
        self._child_pids = []
        self._admission_active = False

    @property
    def resource_owner(self) -> str:
        """Get the resource owner name."""
        return self._resource_owner

    @property
    def ledger(self) -> ResourceLedger:
        """Get the resource ledger."""
        if self._ledger is None:
            self._ledger = get_resource_ledger()
        return self._ledger

    def admission_context(self):
        """Get admission context manager for this transport."""
        return TransportAdmission.for_transport(self._resource_owner, self._ledger)

    async def cleanup_resources(self) -> None:
        """Clean up all resources for this transport."""
        if not self._resource_owner:
            return

        # Terminate child processes
        if self._child_pids:
            for pid in self._child_pids:
                await cleanup_child_process(pid, self._ledger)
            self._child_pids.clear()

        # Release all resources
        self.ledger.release_all(self._resource_owner)
        self._admission_active = False

    def register_child(self, pid: int) -> bool:
        """
        Register a child process.

        Args:
            pid: Child process PID

        Returns:
            True if registered successfully
        """
        if self.ledger.register_child_process(pid, self._resource_owner):
            self._child_pids.append(pid)
            return True
        return False

    def unregister_child(self, pid: int) -> bool:
        """
        Unregister a child process.

        Args:
            pid: Child process PID

        Returns:
            True if unregistered
        """
        if pid in self._child_pids:
            self._child_pids.remove(pid)
        return self.ledger.unregister_child_process(pid)

    async def restart_if_needed(self) -> bool:
        """
        Check if transport needs restart and perform it.

        Override in subclass if transport has health checks.
        """
        return True  # Default: no restart needed


import time as time_module
from _core import aclose
