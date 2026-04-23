import subprocess
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


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
        self.device_path: Optional[str] = None
        self.mount_point: Optional[str] = None
        self._block_size = 512

    def mount(self) -> Optional[str]:
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
                    return True
                
                logger.error(f"Failed to unmount RAM disk: {result.stderr}")
                return False
            
            logger.info("RAM disk unmounted successfully")
            self.device_path = None
            self.mount_point = None
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
            except Exception:
                pass
            self.device_path = None
            self.mount_point = None

    def __enter__(self):
        self.mount()
        return self

    def __exit__(self, _exc_type, _exc_val, _exc_tb):
        self.unmount()

    def __del__(self):
        self.unmount()
