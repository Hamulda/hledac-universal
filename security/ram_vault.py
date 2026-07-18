import atexit
import asyncio
import logging
import os
import re
import subprocess
import threading
import weakref
logger = logging.getLogger(__name__)
_vault_registry: dict[str, RamDiskVault] = {}
_atexit_registered: bool = False

# Global RAM disk size tracker — prevents M1 8GB oversubscription
# Lock ensures thread-safe updates across RamDiskVault instances
_total_ramdisk_mb: int = 0
_total_ramdisk_lock: threading.Lock = threading.Lock()
MAX_TOTAL_RAMDISK_MB: int = 512  # Conservative: 512MB on 8GB machine

def _vault_atexit_cleanup() -> None:
    """
    Atexit handler that detaches all registered RAM disks.

    Runs at interpreter shutdown (after all other atexit handlers).
    Each vault is detached with -force; "not found" errors are ignored.
    """
    global _vault_registry
    for device_path in list(_vault_registry):
        try:
            result = subprocess.run(['hdiutil', 'detach', device_path, '-force'], capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                logger.debug(f'atexit cleanup: detached {device_path}')
            else:
                err = result.stderr.lower()
                if 'not found' not in err and 'no such' not in err:
                    logger.warning(f'atexit cleanup warning: {result.stderr.strip()}')
        except Exception as e:
            logger.debug(f'atexit cleanup error: {e}')
    _vault_registry.clear()

def _register_vault(vault: RamDiskVault) -> None:
    """Register a vault for atexit cleanup; registers handler on first call."""
    global _atexit_registered
    if vault.device_path and vault.device_path not in _vault_registry:
        _vault_registry[vault.device_path] = vault
        if not _atexit_registered:
            atexit.register(_vault_atexit_cleanup)
            _atexit_registered = True
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
    if vault.device_path and vault.device_path in _vault_registry:
        del _vault_registry[vault.device_path]
    if vault.device_path is None and vault.mount_point is None:
        return
    try:
        result = subprocess.run(['hdiutil', 'detach', vault.device_path, '-force'], capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            logger.debug(f'WeakRef finalizer: unmounted {vault.device_path}')
        else:
            err_lower = result.stderr.lower()
            if 'not found' not in err_lower and 'no such' not in err_lower:
                logger.warning(f'WeakRef finalizer: hdiutil warning: {result.stderr.strip()}')
    except subprocess.TimeoutExpired:
        logger.warning(f'WeakRef finalizer: timeout unmounting {vault.device_path}')
    except Exception as e:
        logger.debug(f'WeakRef finalizer: unmount error: {e}')
    finally:
        vault.device_path = None
        vault.mount_point = None

class RamDiskVault:
    _VALID_NAME_RE = re.compile('^[A-Za-z0-9 _-]+$')
    __slots__ = tuple(('_block_size', '_finalizer', '_mounted', 'device_path', 'mount_point', 'name', 'size_mb'))

    def __init__(self, size_mb: int=256, name: str='GhostVault'):
        if not isinstance(size_mb, int) or size_mb <= 0 or size_mb > 4096:
            raise ValueError('size_mb must be a positive integer <= 4096')
        if not self._VALID_NAME_RE.match(name):
            raise ValueError('name must contain only alphanumeric characters, spaces, underscores, and hyphens')
        self.size_mb = size_mb
        self.name = name
        self.device_path: str | None = None
        self.mount_point: str | None = None
        self._block_size = 512
        self._finalizer = weakref.finalize(self, _finalize_vault, weakref.ref(self))
        _finalized_vaults.add(self._finalizer)
        self._mounted: bool = False

    def mount(self) -> str | None:
        # Check global RAM budget before allocating
        global _total_ramdisk_mb, _total_ramdisk_lock
        with _total_ramdisk_lock:
            if _total_ramdisk_mb + self.size_mb > MAX_TOTAL_RAMDISK_MB:
                logger.error(
                    f'RAM disk size limit exceeded: {self.size_mb}MB requested, '
                    f'currently allocated: {_total_ramdisk_mb}MB, max: {MAX_TOTAL_RAMDISK_MB}MB'
                )
                return None
        try:
            block_count = self.size_mb * 1024 * 1024 // self._block_size
            logger.info(f'Creating RAM disk: {self.size_mb}MB ({block_count} blocks)')
            create_result = subprocess.run(['hdiutil', 'attach', '-nomount', f'ram://{block_count}'], capture_output=True, text=True, timeout=30)
            if create_result.returncode != 0:
                logger.error(f'Failed to create RAM disk: {create_result.stderr}')
                return None
            self.device_path = create_result.stdout.strip()
            logger.info(f'RAM disk device created: {self.device_path}')
            logger.info(f'Formatting device with HFS+ filesystem: {self.name}')
            format_result = subprocess.run(['diskutil', 'erasevolume', 'HFS+', self.name, self.device_path], capture_output=True, text=True, timeout=30)
            if format_result.returncode != 0:
                logger.error(f'Failed to format RAM disk: {format_result.stderr}')
                self._cleanup_device()
                return None
            mount_output = format_result.stdout
            mount_match = re.search('/Volumes/([^\\s]+)', mount_output)
            if mount_match:
                self.mount_point = f'/Volumes/{mount_match.group(1)}'
            else:
                self.mount_point = f'/Volumes/{self.name}'
            logger.info(f'RAM disk mounted at: {self.mount_point}')
            self._mounted = True
            _register_vault(self)
            # Update global RAM budget tracker
            with _total_ramdisk_lock:
                _total_ramdisk_mb += self.size_mb
                logger.debug(f'Global RAM disk budget: {_total_ramdisk_mb}/{MAX_TOTAL_RAMDISK_MB}MB')
            return self.mount_point
        except subprocess.TimeoutExpired:
            logger.error('Timeout while mounting RAM disk')
            self._cleanup_device()
            return None
        except Exception as e:
            logger.error(f'Unexpected error mounting RAM disk: {e}')
            self._cleanup_device()
            return None

    def unmount(self) -> bool:
        if self.device_path and self.device_path in _vault_registry:
            del _vault_registry[self.device_path]
        if not self.device_path:
            logger.warning('No device to unmount')
            return True
        try:
            logger.info(f'Unmounting RAM disk: {self.device_path}')
            result = subprocess.run(['hdiutil', 'detach', self.device_path, '-force'], capture_output=True, text=True, timeout=15)
            if result.returncode != 0:
                if 'not found' in result.stderr.lower() or 'no such' in result.stderr.lower():
                    logger.warning('Device already detached or not found')
                    self.device_path = None
                    self.mount_point = None
                    self._mounted = False
                    return True
                logger.error(f'Failed to unmount RAM disk: {result.stderr}')
                return False
            logger.info('RAM disk unmounted successfully')
            # Decrement global RAM budget tracker
            global _total_ramdisk_mb
            with _total_ramdisk_lock:
                _total_ramdisk_mb -= self.size_mb
            self.device_path = None
            self.mount_point = None
            self._mounted = False
            return True
        except subprocess.TimeoutExpired:
            logger.error('Timeout while unmounting RAM disk')
            return False
        except Exception as e:
            logger.error(f'Unexpected error unmounting RAM disk: {e}')
            return False

    def is_mounted(self) -> bool:
        if not self.mount_point:
            return False
        try:
            result = subprocess.run(['df', self.mount_point], capture_output=True, text=True, timeout=5)
            return result.returncode == 0
        except Exception:
            return False

    def _cleanup_device(self):
        # Decrement global RAM budget tracker before cleanup
        if self._mounted or self.device_path:
            global _total_ramdisk_mb
            with _total_ramdisk_lock:
                _total_ramdisk_mb -= self.size_mb
        if self.device_path:
            try:
                subprocess.run(['hdiutil', 'detach', self.device_path, '-force'], capture_output=True, timeout=10)
            except Exception:
                pass
            self.device_path = None
            self.mount_point = None
            self._mounted = False

    def __enter__(self):
        self.mount()
        return self

    def __exit__(self, _exc_type, _exc_val, _exc_tb):
        self.unmount()

    async def __aenter__(self) -> RamDiskVault:
        await self.ainitialize()
        return self

    async def __aexit__(self, _exc_type, _exc_val, _exc_tb) -> None:
        await self.acleanup()

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

    def initialize(self) -> bool:
        """Alias for mount() — returns True if mount succeeded."""
        return self.mount() is not None

    def store(self, key: str, data: dict) -> bool:
        """Write data as JSON under mount_point/<key>.json. Returns True on success."""
        if not self.mount_point:
            return False
        try:
            import json as _json
            path = os.path.join(self.mount_point, f'{key}.json')
            with open(path, 'w') as f:
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
            return [_os.path.splitext(_os.path.basename(p))[0] for p in _glob.glob(_os.path.join(self.mount_point, '*.json'))]
        except Exception:
            return []

    def cleanup(self) -> bool:
        """Alias for unmount()."""
        return self.unmount()