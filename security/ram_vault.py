import atexit
import asyncio
import logging
import os
import re
import subprocess
import weakref

logger = logging.getLogger(__name__)

# ── Vault registry for atexit cleanup ─────────────────────────────────────────
#
# All RamDiskVault instances that have successfully mounted register their
# device_path here. An atexit handler detaches every tracked device on clean
# process exit. This is the PRIMARY cleanup path; weakref.finalize is the
# secondary fallback when an instance is garbage-collected before shutdown.
_vault_registry: dict[str, "RamDiskVault"] = {}
_atexit_registered: bool = False


def _vault_atexit_cleanup() -> None:
    """
    Atexit handler that detaches all registered RAM disks.

    Runs at interpreter shutdown (after all other atexit handlers).
    Each vault is detached with -force; "not found" errors are ignored.
    """
    global _vault_registry
    for device_path in list(_vault_registry):
        try:
            result = subprocess.run(
                ["hdiutil", "detach", device_path, "-force"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                logger.debug(f"atexit cleanup: detached {device_path}")
            else:
                err = result.stderr.lower()
                if "not found" not in err and "no such" not in err:
                    logger.warning(f"atexit cleanup warning: {result.stderr.strip()}")
        except Exception as e:
            logger.debug(f"atexit cleanup error: {e}")
    _vault_registry.clear()


def _register_vault(vault: "RamDiskVault") -> None:
    """Register a vault for atexit cleanup; registers handler on first call."""
    global _atexit_registered
    if vault.device_path and vault.device_path not in _vault_registry:
        _vault_registry[vault.device_path] = vault
        if not _atexit_registered:
            atexit.register(_vault_atexit_cleanup)
            _atexit_registered = True


# ── weakref finalizer registry ─────────────────────────────────────────────────

# Keeps finalizer objects alive so they can fire at GC time or atexit.
# Without this set, the weakref.finalize callback object itself could be
# garbage-collected before the interpreter shuts down.
_finalized_vaults: weakref.WeakSet = weakref.WeakSet()


def _finalize_vault(weak_self: weakref.ref) -> None:
    """
    Secondary finalizer callback for RamDiskVault.

    Called by weakref.finalize when the RamDiskVault instance is garbage
    collected (or during interpreter shutdown as final fallback). Runs
    detached from the RamDiskVault instance so it can safely clean up even
    if the object is in a broken state.

    Also removes the vault from the atexit registry to prevent double-detach.

    Args:
        weak_self: Weak reference to the RamDiskVault instance
    """
    vault = weak_self()
    if vault is None:
        return

    # Remove from atexit registry first (prevents double-detach)
    if vault.device_path and vault.device_path in _vault_registry:
        del _vault_registry[vault.device_path]

    # Guard: skip if already unmounted
    if vault.device_path is None and vault.mount_point is None:
        return

    try:
        result = subprocess.run(
            ["hdiutil", "detach", vault.device_path, "-force"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            logger.debug(f"WeakRef finalizer: unmounted {vault.device_path}")
        else:
            err_lower = result.stderr.lower()
            if "not found" not in err_lower and "no such" not in err_lower:
                logger.warning(
                    f"WeakRef finalizer: hdiutil warning: {result.stderr.strip()}"
                )
    except subprocess.TimeoutExpired:
        logger.warning(f"WeakRef finalizer: timeout unmounting {vault.device_path}")
    except Exception as e:
        logger.debug(f"WeakRef finalizer: unmount error: {e}")
    finally:
        vault.device_path = None
        vault.mount_point = None


class RamDiskVault:
    # Valid name pattern: alphanumeric with spaces, underscores, hyphens only
    _VALID_NAME_RE = re.compile(r'^[A-Za-z0-9 _-]+$')

    def __init__(self, size_mb: int = 256, name: str = "GhostVault"):
        # Validate inputs before storing
        if not isinstance(size_mb, int) or size_mb <= 0 or size_mb > 4096:
            raise ValueError("size_mb must be a positive integer <= 4096")
        if not self._VALID_NAME_RE.match(name):
            raise ValueError(
                "name must contain only alphanumeric characters, spaces, "
                "underscores, and hyphens"
            )
        self.size_mb = size_mb
        self.name = name
        self.device_path: str | None = None
        self.mount_point: str | None = None
        self._block_size = 512

        # Primary safety net: weakref.finalize guarantees this callback runs
        # when the object is garbage collected OR at interpreter shutdown.
        # weakref.finalize registers an atexit handler internally, so this
        # survives even if __del__ is never called or is blocked.
        self._finalizer = weakref.finalize(self, _finalize_vault, weakref.ref(self))
        _finalized_vaults.add(self._finalizer)

        # Track mount state — used by _register_vault and atexit registry
        self._mounted: bool = False

    def mount(self) -> str | None:
        try:
            block_count = (self.size_mb * 1024 * 1024) // self._block_size

            logger.info(f"Creating RAM disk: {self.size_mb}MB ({block_count} blocks)")

            create_result = subprocess.run(
                ["hdiutil", "attach", "-nomount", f"ram://{block_count}"],
                capture_output=True,
                text=True,
                timeout=30
            )

            if create_result.returncode != 0:
                logger.error(f"Failed to create RAM disk: {create_result.stderr}")
                return None

            self.device_path = create_result.stdout.strip()
            logger.info(f"RAM disk device created: {self.device_path}")

            logger.info(f"Formatting device with HFS+ filesystem: {self.name}")
            format_result = subprocess.run(
                ["diskutil", "erasevolume", "HFS+", self.name, self.device_path],
                capture_output=True,
                text=True,
                timeout=30
            )

            if format_result.returncode != 0:
                logger.error(f"Failed to format RAM disk: {format_result.stderr}")
                self._cleanup_device()
                return None

            mount_output = format_result.stdout
            mount_match = re.search(r'/Volumes/([^\s]+)', mount_output)
            if mount_match:
                self.mount_point = f"/Volumes/{mount_match.group(1)}"
            else:
                self.mount_point = f"/Volumes/{self.name}"

            logger.info(f"RAM disk mounted at: {self.mount_point}")
            self._mounted = True
            _register_vault(self)
            return self.mount_point

        except subprocess.TimeoutExpired:
            logger.error("Timeout while mounting RAM disk")
            self._cleanup_device()
            return None
        except Exception as e:
            logger.error(f"Unexpected error mounting RAM disk: {e}")
            self._cleanup_device()
            return None

    def unmount(self) -> bool:
        # Deregister from atexit registry first — prevents atexit from
        # attempting to detach after we already did it here.
        if self.device_path and self.device_path in _vault_registry:
            del _vault_registry[self.device_path]

        if not self.device_path:
            logger.warning("No device to unmount")
            return True

        try:
            logger.info(f"Unmounting RAM disk: {self.device_path}")

            result = subprocess.run(
                ["hdiutil", "detach", self.device_path, "-force"],
                capture_output=True,
                text=True,
                timeout=15
            )

            if result.returncode != 0:
                if "not found" in result.stderr.lower() or "no such" in result.stderr.lower():
                    logger.warning("Device already detached or not found")
                    self.device_path = None
                    self.mount_point = None
                    self._mounted = False
                    return True

                logger.error(f"Failed to unmount RAM disk: {result.stderr}")
                return False

            logger.info("RAM disk unmounted successfully")
            self.device_path = None
            self.mount_point = None
            self._mounted = False
            return True

        except subprocess.TimeoutExpired:
            logger.error("Timeout while unmounting RAM disk")
            return False
        except Exception as e:
            logger.error(f"Unexpected error unmounting RAM disk: {e}")
            return False

    def is_mounted(self) -> bool:
        if not self.mount_point:
            return False

        try:
            result = subprocess.run(
                ["df", self.mount_point],
                capture_output=True,
                text=True,
                timeout=5
            )
            return result.returncode == 0
        except Exception:
            return False

    def _cleanup_device(self):
        if self.device_path:
            try:
                subprocess.run(
                    ["hdiutil", "detach", self.device_path, "-force"],
                    capture_output=True,
                    timeout=10
                )
            except Exception:  # noqa: BLE001
                pass
            self.device_path = None
            self.mount_point = None

    def __enter__(self):
        self.mount()
        return self

    def __exit__(self, _exc_type, _exc_val, _exc_tb):
        self.unmount()

    # ── async context manager (Python 3.11+) ────────────────────────────────

    async def __aenter__(self) -> "RamDiskVault":
        await self.ainitialize()
        return self

    async def __aexit__(self, _exc_type, _exc_val, _exc_tb) -> None:
        await self.acleanup()

    # ── async API (for ghost_layer compatibility) ────────────────────────────

    async def amount(self) -> str | None:
        """Async mount — runs mount() in thread pool to avoid blocking event loop."""
        return await asyncio.to_thread(self.mount)

    async def ainitialize(self) -> bool:
        """Async alias for amount()."""
        result = await self.amount()
        return result is not None

    async def aunmount(self) -> bool:
        """Async unmount — runs unmount() in thread pool."""
        return await asyncio.to_thread(self.unmount)

    async def acleanup(self) -> bool:
        """Async cleanup — alias for aunmount()."""
        return await self.aunmount()

    # ── ghost_layer compatibility shims ───────────────────────────────────────

    def initialize(self) -> bool:
        """Alias for mount() — returns True if mount succeeded."""
        return self.mount() is not None

    def store(self, key: str, data: dict) -> bool:
        """Write data as JSON under mount_point/<key>.json. Returns True on success."""
        if not self.mount_point:
            return False
        try:
            import json as _json
            path = os.path.join(self.mount_point, f"{key}.json")
            with open(path, "w") as f:
                _json.dump(data, f, default=str)
            return True
        except Exception:
            return False

    def list_items(self) -> list[str]:
        """Return list of stored key names (filename stems, without .json)."""
        if not self.mount_point:
            return []
        try:
            import glob as _glob
            import os as _os
            return [
                _os.path.splitext(_os.path.basename(p))[0]
                for p in _glob.glob(_os.path.join(self.mount_point, "*.json"))
            ]
        except Exception:
            return []

    def cleanup(self) -> bool:
        """Alias for unmount()."""
        return self.unmount()
