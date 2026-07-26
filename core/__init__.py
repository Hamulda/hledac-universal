"""core — F350M-R A-04"""
from hledac.universal.core.locks import LockCategory, LockInfo, register_lock, acquire_in_order, get_registered_locks, get_locks_by_category, AsyncLockDCLP, make_counter
from hledac.universal.core.embeddings.legacy import MLXEmbeddingManager, EmbeddingTask, apply_task_prefix, should_normalize
from hledac.universal.core.resource_governor import Priority
from hledac.universal.core.system_detector import SystemDetector, get_system_detector, get_hardware_capabilities, HardwareCapabilities
from utils.uma_budget import Watchdog
_rb = None


def __getattr__(name: str):
    global _rb
    if name == "rust_backend":
        if _rb is None:
            from hledac.universal.core.rust_backend import rust as _r
            _rb = _r
        return _rb
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["Priority", "MLXEmbeddingManager", "EmbeddingTask", "apply_task_prefix", "should_normalize", "SystemDetector", "get_system_detector", "get_hardware_capabilities", "HardwareCapabilities", "LockCategory", "LockInfo", "register_lock", "acquire_in_order", "get_registered_locks", "get_locks_by_category", "AsyncLockDCLP", "make_counter", "Watchdog", "rust_backend"]
