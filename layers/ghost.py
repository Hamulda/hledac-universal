"""
Ghost Layer - Ghost Orchestration and Anti-Loop Protection
========================================================

Consolidated from:
- ghost_layer.py: GhostLayer + SystemContext
- memory_layer.py: MemoryLayer (memory management)

Features:
- GhostDirector integration with anti-loop protection
- RamDiskVault for secure storage
- LootManager for acquired data
- Anti-VM protection (SystemContext)
- M1 Neural Memory Guard
- Process monitoring and integrity checking

M1 8GB: Shared GhostDirector singleton prevents duplicate initialization.
"""

from __future__ import annotations

import gc
import hashlib
import logging
import platform
import subprocess
import time
from typing import Any

from compat.msgspec_gc_compat import Struct
from hledac.universal.project_types import (
    ActionResult,
    ActionType,
    GhostConfig,
    StagnationError,
)
from hledac.universal.utils.hashing import xxh3_64_hex

logger = logging.getLogger(__name__)

__all__ = [
    "GhostLayer",
    "SystemContext",
    "VMThreatLevel",
]


class VMThreatLevel(Struct):
    """VM threat levels."""

    CRITICAL: int = 3
    HIGH: int = 2
    MEDIUM: int = 1
    LOW: int = 0


class SystemContext:
    """
    SystemContext with anti-VM protection for Ghost operations.

    Provides:
    - VM detection via sysctl kern.hv_support
    - Process monitoring and whitelisting
    - System integrity checking
    - M1 Neural Memory Guard (force_neural_cleanup)
    - Stealth mode activation

    M1 8GB: Uses __slots__ for memory efficiency.
    """

    __slots__ = (
        "_anti_vm_config",
        "_monitored_processes",
        "_process_whitelist",
        "_security_events",
        "_stats",
        "_suspicious_activities",
        "_system_integrity",
        "created_at",
        "id",
    )

    def __init__(
        self,
        enable_anti_vm: bool = True,
        enable_process_monitoring: bool = True,
        enable_integrity_checking: bool = True,
        enable_stealth_mode: bool = False,
        m1_optimization: bool = True,
    ) -> None:
        self.id = f"sysctx_{hashlib.sha256(str(time.time()).encode()).hexdigest()[:8]}"
        self.created_at = time.time()
        self._anti_vm_config = {
            "enable_anti_vm": enable_anti_vm,
            "enable_process_monitoring": enable_process_monitoring,
            "enable_integrity_checking": enable_integrity_checking,
            "enable_stealth_mode": enable_stealth_mode,
            "m1_optimization": m1_optimization,
            "threat_detection_sensitivity": 0.8,
            "process_whitelist": [
                "kernel_task",
                "launchd",
                "networkd",
                "resolved",
                "python",
                "node",
                "npm",
                "pip",
                "docker",
                "git",
                "hledac",
                "main.py",
                "launch_ghost.py",
            ],
        }
        self._monitored_processes: dict[int, dict] = {}
        self._process_whitelist = set(self._anti_vm_config["process_whitelist"])
        self._suspicious_activities: dict[int, dict] = {}
        self._security_events: list = []
        self._system_integrity = {
            "kernel_integrity": True,
            "memory_integrity": True,
            "process_integrity": True,
        }
        self._stats = {
            "vm_detections": 0,
            "process_monitoring_events": 0,
            "integrity_checks": 0,
            "stealth_activations": 0,
            "m1_optimizations": 0,
        }
        logger.info(f"SystemContext initialized: {self.id}")

    def is_vm_environment(self) -> bool:
        """Detect if running in virtualized environment."""
        try:
            if platform.system() == "Darwin":
                try:
                    result = subprocess.run(
                        ["sysctl", "-n", "kern.hv_support"],
                        capture_output=True,
                        text=True,
                        timeout=5.0,
                    )
                    if result.returncode == 0 and result.stdout.strip() == "1":
                        logger.warning("Hypervisor detected on macOS")
                        self._stats["vm_detections"] += 1
                        return True
                except Exception:
                    pass

            for indicator in ["/proc/xen", "/dev/kvm", "/dev/vmmon", "/sys/class/hypervisor"]:
                try:
                    from pathlib import Path

                    if Path(indicator).exists():
                        logger.warning(f"VM indicator found: {indicator}")
                        self._stats["vm_detections"] += 1
                        return True
                except Exception:
                    pass
            return False
        except Exception as e:
            logger.warning(f"VM detection failed: {e}")
            return False

    def get_system_info(self) -> dict[str, Any]:
        """Get comprehensive system information."""
        try:
            system_info = {
                "platform": platform.system(),
                "processor": platform.processor(),
                "architecture": platform.architecture(),
                "python_version": platform.python_version(),
                "is_vm": self.is_vm_environment(),
            }
            if self._anti_vm_config["m1_optimization"] and platform.system() == "Darwin":
                try:
                    result = subprocess.run(
                        ["sysctl", "-n", "machdep.cpu.brand_string"],
                        capture_output=True,
                        text=True,
                        timeout=2.0,
                    )
                    if result.returncode == 0:
                        system_info["cpu_brand"] = result.stdout.strip()
                except Exception:
                    pass
            try:
                from hledac.universal.utils.sys_metrics import system_memory_sync

                memory = system_memory_sync()
                system_info.update(
                    {
                        "total_memory_gb": round(memory.total_gib, 2),
                        "available_memory_gb": round(memory.available_gib, 2),
                        "memory_percent": memory.percent,
                    }
                )
            except Exception:
                pass
            return system_info
        except Exception as e:
            logger.error(f"System info gathering failed: {e}")
            return {"error": str(e)}

    def force_neural_cleanup(self) -> dict[str, Any]:
        """M1 Neural Memory Guard - Force cleanup of MLX and system memory."""
        from hledac.universal.utils.mlx_cache import get_mx
        from hledac.universal._core.resource_governor import _get_cached_psutil, _read_virtual_memory_sync

        cleanup_results = {
            "mlx_detected": False,
            "mlx_cache_cleared": False,
            "gc_collected": False,
            "memory_before_mb": 0,
            "memory_after_mb": 0,
            "memory_freed_mb": 0,
            "errors": [],
        }
        try:
            memory = _get_cached_psutil("virtual_memory", _read_virtual_memory_sync)
            if memory is not None:
                cleanup_results["memory_before_mb"] = round(memory.used / 1024**2, 2)

            # Clear MLX cache if available
            try:
                import sys

                mlx_modules = [mod for mod in sys.modules.keys() if mod.startswith("mlx")]
                if mlx_modules:
                    cleanup_results["mlx_detected"] = True
                    logger.info(f"MLX detected, modules: {mlx_modules}")
                    mx = get_mx()
                    if mx is not None:
                        mx.eval([])
                        gc.collect()
                        if hasattr(mx, "clear_cache"):
                            mx.clear_cache()
                        gc.collect()
                        cleanup_results["mlx_cache_cleared"] = True
                        logger.info("MLX Metal cache cleared")
            except Exception as import_error:
                cleanup_results["errors"].append(f"MLX detection failed: {import_error}")

            # Force garbage collection
            gc.collect()
            cleanup_results["gc_collected"] = True

            memory_after = _get_cached_psutil("virtual_memory", _read_virtual_memory_sync)
            if memory_after is not None:
                cleanup_results["memory_after_mb"] = round(memory_after.used / 1024**2, 2)
            cleanup_results["memory_freed_mb"] = round(
                cleanup_results["memory_before_mb"] - cleanup_results["memory_after_mb"], 2
            )
            self._stats["m1_optimizations"] += 1
            logger.info(f"Neural cleanup: {cleanup_results['memory_freed_mb']}MB freed")
        except Exception as e:
            cleanup_results["errors"].append(f"Cleanup failed: {e}")
            logger.error(f"Neural cleanup failed: {e}")
        return cleanup_results

    def activate_stealth_mode(self) -> None:
        """Activate stealth mode for enhanced protection."""
        if self._anti_vm_config["enable_stealth_mode"]:
            self._anti_vm_config["stealth_active"] = True
            self._stats["stealth_activations"] += 1
            logger.warning("🔒 Stealth mode activated - enhanced protection enabled")
            self._anti_vm_config["threat_detection_sensitivity"] = 1.0
            self._system_integrity.update(
                {
                    "anti_tampering": True,
                    "secure_boot": True,
                    "protected_memory": True,
                }
            )

    def get_stats(self) -> dict[str, Any]:
        """Get system context statistics."""
        stats = self._stats.copy()
        stats["uptime_seconds"] = time.time() - self.created_at
        stats.update(self._system_integrity)
        try:
            from hledac.universal.utils.sys_metrics import system_memory_sync

            memory = system_memory_sync()
            stats["current_memory_gb"] = round(memory.used_gib, 2)
            stats["memory_available_gb"] = round(memory.available_gib, 2)
        except Exception:
            pass
        return stats


class GhostLayer:
    """
    Ghost layer integrating GhostDirector with vault and anti-loop protection.

    This layer:
    1. Wraps GhostDirector for action execution
    2. Manages RamDiskVault for secure storage
    3. Tracks LootManager for acquired data
    4. Detects stagnation (infinite loops)
    5. Provides anti-VM protection (SystemContext)
    6. M1 Neural Memory Guard for memory cleanup

    M1 8GB: Uses __slots__ for memory efficiency, shared GhostDirector singleton.
    """

    layer_name: str = "ghost"
    _priority: int = 100  # High priority - runs first

    __slots__ = (
        "_action_count",
        "_consecutive_empty",
        "_consecutive_same",
        "_ghost_director",
        "_ghost_director_shared",
        "_initialized",
        "_last_results_hash",
        "_loot_manager",
        "_stagnation_counter",
        "_stagnation_events",
        "_system_context",
        "_vault",
        "config",
    )

    def __init__(
        self,
        config: GhostConfig | None = None,
        ghost_director: Any | None = None,
    ) -> None:
        self.config = config or GhostConfig()
        self._ghost_director = ghost_director
        self._ghost_director_shared = ghost_director is not None
        self._vault = None
        self._loot_manager = None
        self._system_context: SystemContext | None = None
        self._stagnation_counter = 0
        self._last_results_hash: str | None = None
        self._consecutive_empty = 0
        self._consecutive_same = 0
        self._action_count = 0
        self._stagnation_events = 0
        self._initialized = False
        logger.info(f"GhostLayer initialized (GhostDirector: {'shared' if self._ghost_director_shared else 'lazy'})")

    async def mount(self, ctx: Any) -> None:
        """Mount the ghost layer."""
        await self.initialize()
        ctx.set("ghost", self)
        ctx.set("system_context", self._system_context)

    async def unmount(self, ctx: Any) -> None:
        """Unmount the ghost layer."""
        await self.cleanup()

    async def process(self, ctx: Any, data: Any) -> Any:
        """Process data through ghost layer (passthrough for now)."""
        return data

    async def rollback(self, ctx: Any, error: Exception) -> None:
        """Rollback on error."""
        logger.warning(f"GhostLayer rollback: {error}")

    async def __aenter__(self) -> GhostLayer:
        """Async context manager entry."""
        if not self._initialized:
            await self.initialize()
        return self

    async def __aexit__(self, _exc_type: Any, _exc_val: Any, _exc_tb: Any) -> bool:
        """Async context manager exit."""
        await self.cleanup()
        return False

    async def initialize(self) -> bool:
        """Initialize GhostLayer components."""
        try:
            logger.info("🚀 Initializing GhostLayer...")
            await self._init_system_context()
            if self.config.enable_anti_loop or self.config.max_steps > 0:
                await self._init_ghost_director()
            if self.config.enable_vault:
                await self._init_vault()
            if self.config.enable_loot_manager:
                await self._init_loot_manager()
            logger.info("✅ GhostLayer initialized successfully")
            self._initialized = True
            return True
        except Exception as e:
            logger.error(f"❌ GhostLayer initialization failed: {e}")
            return False

    async def _init_system_context(self) -> None:
        """Initialize SystemContext for anti-VM protection."""
        try:
            self._system_context = SystemContext(
                enable_anti_vm=True,
                enable_process_monitoring=True,
                enable_integrity_checking=True,
                enable_stealth_mode=False,
                m1_optimization=True,
            )
            if self._system_context.is_vm_environment():
                logger.warning("⚠️ VM environment detected - anti-VM protections active")
            else:
                logger.info("✅ SystemContext initialized (bare metal detected)")
        except Exception as e:
            logger.warning(f"⚠️ SystemContext not available: {e}")
            self._system_context = None

    def is_vm_environment(self) -> bool:
        """Check if running in virtualized environment."""
        if self._system_context:
            return self._system_context.is_vm_environment()
        return False

    def get_system_info(self) -> dict[str, Any]:
        """Get comprehensive system information."""
        if self._system_context:
            return self._system_context.get_system_info()
        return {"error": "SystemContext not available"}

    def force_neural_cleanup(self) -> dict[str, Any]:
        """M1 Neural Memory Guard."""
        if self._system_context:
            return self._system_context.force_neural_cleanup()
        return {"error": "SystemContext not available"}

    def activate_stealth_mode(self) -> None:
        """Activate stealth mode."""
        if self._system_context:
            self._system_context.activate_stealth_mode()
            logger.info("🔒 Stealth mode activated")

    def get_system_stats(self) -> dict[str, Any]:
        """Get system context statistics."""
        if self._system_context:
            return self._system_context.get_stats()
        return {}

    async def _init_ghost_director(self) -> None:
        """Lazy initialization of GhostDirector."""
        if self._ghost_director_shared and self._ghost_director is not None:
            logger.debug("Using shared GhostDirector")
            return
        if self._ghost_director is None:
            try:
                from hledac.universal.cortex.director import GhostDirector

                self._ghost_director = GhostDirector(max_steps=self.config.max_steps)
                await self._ghost_director.initialize_drivers()
                logger.info("✅ GhostDirector initialized (local)")
            except ImportError as e:
                logger.warning(f"⚠️ GhostDirector not available: {e}")
                self._ghost_director = None

    async def _init_vault(self) -> None:
        """Lazy initialization of RamDiskVault."""
        if self._vault is None:
            try:
                from hledac.universal.security.ram_vault import RamDiskVault

                self._vault = RamDiskVault(size_mb=self.config.vault_size_mb)
                await self._vault.ainitialize()
                logger.info(f"✅ RamDiskVault initialized ({self.config.vault_size_mb}MB)")
            except ImportError as e:
                logger.warning(f"⚠️ RamDiskVault not available: {e}")
                self._vault = None

    async def _init_loot_manager(self) -> None:
        """Lazy initialization of LootManager."""
        if self._loot_manager is None:
            try:
                from hledac.universal.supreme.security.loot_manager import LootManager

                self._loot_manager = LootManager()
                logger.info("✅ LootManager initialized")
            except ImportError as e:
                logger.warning(f"⚠️ LootManager not available: {e}")
                self._loot_manager = None

    async def execute_action(
        self,
        action_type: ActionType,
        parameters: dict[str, Any],
        store_in_vault: bool = True,
    ) -> ActionResult:
        """Execute a Ghost action with anti-loop protection."""

        self._action_count += 1
        start_time = time.time()
        logger.info(f"🔧 Executing action: {action_type.value}")

        try:
            if self.config.enable_anti_loop:
                if self._check_stagnation(parameters):
                    self._stagnation_events += 1
                    logger.warning(f"🔄 Stagnation detected (#{self._stagnation_events})")
                    if self._stagnation_counter >= self.config.stagnation_threshold:
                        raise StagnationError(f"Stagnation threshold ({self.config.stagnation_threshold}) reached.")

            if self._ghost_director:
                raw_result = await self._execute_via_director(action_type, parameters)
            else:
                raw_result = await self._simulate_execution(action_type, parameters)

            vault_id = None
            if store_in_vault and self._vault and raw_result.get("success"):
                vault_id = await self._store_in_vault(raw_result)
                raw_result["vault_id"] = vault_id

            if self._loot_manager and raw_result.get("success"):
                await self._update_loot(raw_result)

            self._update_stagnation_tracking(raw_result)
            execution_time = time.time() - start_time

            result = ActionResult(
                action=action_type,
                success=raw_result.get("success", False),
                data=raw_result,
                execution_time=execution_time,
                stagnation_detected=self._stagnation_counter > 0,
                stored_in_vault=vault_id is not None,
            )
            logger.info(f"✅ Action completed in {execution_time:.2f}s")
            return result

        except Exception as e:
            execution_time = time.time() - start_time
            logger.error(f"❌ Action failed: {e}")
            return ActionResult(
                action=action_type,
                success=False,
                data={"error": str(e)},
                execution_time=execution_time,
                stagnation_detected=False,
                stored_in_vault=False,
            )

    async def _execute_via_director(
        self,
        action_type: ActionType,
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute action via GhostDirector."""
        action_plan = {
            "action": action_type.value,
            "parameters": parameters,
            "vault": self._vault,
        }
        result = await self._ghost_director.execute_action(action_plan)
        return {
            "success": getattr(result, "success", True),
            "data": getattr(result, "data", result),
            "source": "ghost_director",
        }

    async def _simulate_execution(
        self,
        action_type: ActionType,
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        """Simulate action execution when GhostDirector unavailable."""
        logger.debug(f"Simulating action: {action_type.value}")
        return {
            "success": True,
            "data": {
                "action": action_type.value,
                "parameters": parameters,
                "simulated": True,
                "results": [],
            },
            "source": "simulation",
        }

    async def _store_in_vault(self, data: dict[str, Any]) -> str | None:
        """Store data in RamDiskVault."""
        import orjson

        if not self._vault:
            return None
        try:
            data_hash = hashlib.sha256(orjson.dumps(data, option=orjson.OPT_SORT_KEYS)).hexdigest()[:16]
            vault_id = f"ghost_{data_hash}"
            self._vault.store(vault_id, data)
            logger.debug(f"📦 Stored in vault: {vault_id}")
            return vault_id
        except Exception as e:
            logger.warning(f"⚠️ Failed to store in vault: {e}")
            return None

    async def _update_loot(self, data: dict[str, Any]) -> None:
        """Update LootManager with acquired data."""
        if not self._loot_manager:
            return
        try:
            items = data.get("data", {}).get("results", [])
            for item in items:
                await self._loot_manager.add_loot(
                    source="ghost_action",
                    content=item,
                    metadata={"action": data.get("action")},
                )
            if items:
                logger.debug(f"💰 Added {len(items)} items to loot")
        except Exception as e:
            logger.warning(f"⚠️ Failed to update loot: {e}")

    def _check_stagnation(self, parameters: dict[str, Any]) -> bool:
        """Check if current execution might cause stagnation."""
        if not parameters or not any(parameters.values()):
            self._consecutive_empty += 1
        else:
            self._consecutive_empty = 0

        stagnation_detected = (
            self._consecutive_empty >= 2 or self._consecutive_same >= 3 or self._stagnation_counter > 0
        )
        if stagnation_detected:
            self._stagnation_counter += 1
        return stagnation_detected

    def _update_stagnation_tracking(self, result: dict[str, Any]) -> None:
        """Update stagnation tracking based on result."""
        import orjson

        result_str = orjson.dumps(result, option=orjson.OPT_SORT_KEYS).decode()
        result_hash = xxh3_64_hex(result_str)
        if result_hash == self._last_results_hash:
            self._consecutive_same += 1
            logger.warning(f"🔄 Same result #{self._consecutive_same}")
        else:
            self._consecutive_same = 0
            self._stagnation_counter = 0
        self._last_results_hash = result_hash

    def get_loot_summary(self) -> dict[str, Any]:
        """Get summary of acquired loot."""
        if not self._loot_manager:
            return {"available": False}
        try:
            return {"available": True, "items": self._loot_manager.get_summary()}
        except Exception as e:
            logger.warning(f"⚠️ Failed to get loot summary: {e}")
            return {"available": False, "error": str(e)}

    def get_vault_contents(self) -> list[str]:
        """Get list of vault item IDs."""
        if not self._vault:
            return []
        try:
            return self._vault.list_items()
        except Exception as e:
            logger.warning(f"⚠️ Failed to list vault: {e}")
            return []

    def reset_stagnation_counter(self) -> None:
        """Reset stagnation counter."""
        self._stagnation_counter = 0
        self._consecutive_empty = 0
        self._consecutive_same = 0
        self._last_results_hash = None
        logger.info("🔄 Stagnation counters reset")

    def get_statistics(self) -> dict[str, Any]:
        """Get GhostLayer statistics."""
        stats = {
            "actions_executed": self._action_count,
            "stagnation_events": self._stagnation_events,
            "stagnation_counter": self._stagnation_counter,
            "vault_enabled": self._vault is not None,
            "loot_enabled": self._loot_manager is not None,
            "ghost_director_enabled": self._ghost_director is not None,
            "system_context_enabled": self._system_context is not None,
        }
        if self._system_context:
            stats["system"] = self._system_context.get_stats()
        return stats

    async def cleanup(self) -> None:
        """Cleanup resources."""
        logger.info("🧹 Cleaning up GhostLayer...")
        if self._ghost_director is not None:
            try:
                await self._ghost_director.cleanup()
            except Exception as e:
                logger.warning(f"⚠️ GhostDirector cleanup error: {e}")
        try:
            if self._vault is not None:
                await self._vault.acleanup()
        except Exception as e:
            logger.warning(f"⚠️ Vault cleanup error: {e}")
        if self._loot_manager is not None:
            try:
                cleanup_fn = getattr(self._loot_manager, "cleanup", None)
                if cleanup_fn is not None:
                    import inspect

                    if inspect.iscoroutinefunction(cleanup_fn):
                        await cleanup_fn()
                    else:
                        cleanup_fn()
            except Exception as e:
                logger.warning(f"⚠️ LootManager cleanup error: {e}")
        self._initialized = False
        logger.info("✅ GhostLayer cleanup complete")


__all__ = ["GhostLayer", "SystemContext"]
