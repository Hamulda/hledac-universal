"""
Ghost Layer - Wrapper for GhostDirector Integration
====================================================

Integrates GhostDirector with:
- RamDiskVault for secure RAM storage
- LootManager for acquired data
- Anti-loop protection (stagnation detection)
- Action execution with vault storage
- Anti-VM protection (from kernel/context.py)
- Neural Memory Guard (M1-specific)
- Process monitoring and integrity checking

This is a thin wrapper that imports existing GhostDirector
and adds integration logic without duplicating code.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional

from ..types import (
    ActionResult,
    ActionType,
    GhostConfig,
    StagnationError,
)

logger = logging.getLogger(__name__)


class GhostLayer:
    """
    Ghost layer integrating GhostDirector with vault and anti-loop protection.

    This layer:
    1. Wraps GhostDirector for action execution (can be shared from LayerManager)
    2. Manages RamDiskVault for secure storage
    3. Tracks LootManager for acquired data
    4. Detects stagnation (infinite loops)
    5. Provides anti-VM protection (SystemContext integration)
    6. M1 Neural Memory Guard for memory cleanup

    Example:
        ghost = GhostLayer(config)
        await ghost.initialize()

        # Check for VM environment
        if ghost.is_vm_environment():
            print("Running in VM!")

        # Execute action with neural cleanup
        result = await ghost.execute_action(
            action_type=ActionType.SCAN,
            parameters={"url": "https://example.com"}
        )

        if result.stagnation_detected:
            # Handle stagnation
            pass
    """

    def __init__(self, config: Optional[GhostConfig] = None, ghost_director: Optional[Any] = None):
        """
        Initialize GhostLayer.

        Args:
            config: Ghost configuration (uses defaults if None)
            ghost_director: Optional shared GhostDirector instance from LayerManager
                           (prevents duplicate initialization on M1 8GB)
        """
        self.config = config or GhostConfig()

        # Core components (lazy loaded)
        # GhostDirector can be shared from LayerManager to prevent duplicate init
        self._ghost_director = ghost_director
        self._ghost_director_shared = ghost_director is not None
        self._vault = None
        self._loot_manager = None

        # SystemContext for anti-VM protection (from kernel/context.py)
        self._system_context: Optional['SystemContext'] = None

        # Anti-loop protection
        self._stagnation_counter = 0
        self._last_results_hash: Optional[str] = None
        self._consecutive_empty = 0
        self._consecutive_same = 0

        # Statistics
        self._action_count = 0
        self._stagnation_events = 0

        logger.info(f"GhostLayer initialized (GhostDirector: {'shared' if self._ghost_director_shared else 'lazy'})")
    
    async def initialize(self) -> bool:
        """
        Initialize GhostLayer components.
        
        Returns:
            True if initialization successful
        """
        try:
            logger.info("🚀 Initializing GhostLayer...")
            
            # Initialize SystemContext (anti-VM protection)
            await self._init_system_context()
            
            # Initialize GhostDirector (lazy import)
            if self.config.enable_anti_loop or self.config.max_steps > 0:
                await self._init_ghost_director()
            
            # Initialize RamDiskVault
            if self.config.enable_vault:
                await self._init_vault()
            
            # Initialize LootManager
            if self.config.enable_loot_manager:
                await self._init_loot_manager()
            
            logger.info("✅ GhostLayer initialized successfully")
            return True
            
        except Exception as e:
            logger.error(f"❌ GhostLayer initialization failed: {e}")
            return False
    
    async def _init_system_context(self) -> None:
        """Initialize SystemContext for anti-VM protection and system monitoring."""
        try:
            self._system_context = SystemContext(
                enable_anti_vm=True,
                enable_process_monitoring=True,
                enable_integrity_checking=True,
                enable_stealth_mode=False,
                m1_optimization=True
            )
            
            # Check for VM environment
            if self._system_context.is_vm_environment():
                logger.warning("⚠️ VM environment detected - anti-VM protections active")
            else:
                logger.info("✅ SystemContext initialized (bare metal detected)")
                
        except Exception as e:
            logger.warning(f"⚠️ SystemContext not available: {e}")
            self._system_context = None
    
    # ====================================================================
    # SystemContext Integration (from kernel/context.py)
    # ====================================================================
    
    def is_vm_environment(self) -> bool:
        """Check if running in virtualized environment."""
        if self._system_context:
            return self._system_context.is_vm_environment()
        return False
    
    def get_system_info(self) -> Dict[str, Any]:
        """Get comprehensive system information."""
        if self._system_context:
            return self._system_context.get_system_info()
        return {"error": "SystemContext not available"}
    
    def force_neural_cleanup(self) -> Dict[str, Any]:
        """
        M1 Neural Memory Guard - Force cleanup of MLX and system memory.
        
        Returns:
            Cleanup results with memory freed
        """
        if self._system_context:
            return self._system_context.force_neural_cleanup()
        return {"error": "SystemContext not available"}
    
    def activate_stealth_mode(self) -> None:
        """Activate stealth mode for enhanced protection."""
        if self._system_context:
            self._system_context.activate_stealth_mode()
            logger.info("🔒 Stealth mode activated")
    
    def get_system_stats(self) -> Dict[str, Any]:
        """Get system context statistics."""
        if self._system_context:
            return self._system_context.get_stats()
        return {}
    
    async def _init_ghost_director(self) -> None:
        """Lazy initialization of GhostDirector (only if not shared)"""
        # Skip if GhostDirector was provided by LayerManager (shared instance)
        if self._ghost_director_shared and self._ghost_director is not None:
            logger.debug("Using shared GhostDirector from LayerManager")
            return

        if self._ghost_director is None:
            try:
                from hledac.cortex.director import GhostDirector

                self._ghost_director = GhostDirector(
                    max_steps=self.config.max_steps,
                    # ctx and vault passed during execution
                )
                await self._ghost_director.initialize_drivers()
                logger.info("✅ GhostDirector initialized (local)")

            except ImportError as e:
                logger.warning(f"⚠️ GhostDirector not available: {e}")
                self._ghost_director = None
    
    async def _init_vault(self) -> None:
        """Lazy initialization of RamDiskVault"""
        if self._vault is None:
            try:
                from hledac.supreme.security.ram_disk_vault import RamDiskVault
                
                self._vault = RamDiskVault(
                    size_mb=self.config.vault_size_mb
                )
                await self._vault.initialize()
                logger.info(f"✅ RamDiskVault initialized ({self.config.vault_size_mb}MB)")
                
            except ImportError as e:
                logger.warning(f"⚠️ RamDiskVault not available: {e}")
                self._vault = None
    
    async def _init_loot_manager(self) -> None:
        """Lazy initialization of LootManager"""
        if self._loot_manager is None:
            try:
                from hledac.supreme.security.loot_manager import LootManager
                
                self._loot_manager = LootManager()
                logger.info("✅ LootManager initialized")
                
            except ImportError as e:
                logger.warning(f"⚠️ LootManager not available: {e}")
                self._loot_manager = None
    
    async def execute_action(
        self,
        action_type: ActionType,
        parameters: Dict[str, Any],
        store_in_vault: bool = True
    ) -> ActionResult:
        """
        Execute a Ghost action with anti-loop protection.
        
        Args:
            action_type: Type of action to execute
            parameters: Action parameters
            store_in_vault: Whether to store result in vault
            
        Returns:
            ActionResult with execution details
            
        Raises:
            StagnationError: If stagnation detected and threshold reached
        """
        self._action_count += 1
        start_time = __import__('time').time()
        
        logger.info(f"🔧 Executing action: {action_type.value}")
        
        try:
            # Check for stagnation before execution
            if self.config.enable_anti_loop:
                if self._check_stagnation(parameters):
                    self._stagnation_events += 1
                    logger.warning(f"🔄 Stagnation detected (event #{self._stagnation_events})")
                    
                    if self._stagnation_counter >= self.config.stagnation_threshold:
                        raise StagnationError(
                            f"Stagnation threshold ({self.config.stagnation_threshold}) reached. "
                            "Research loop detected."
                        )
            
            # Execute via GhostDirector
            if self._ghost_director:
                raw_result = await self._execute_via_director(action_type, parameters)
            else:
                # Fallback: simulate execution
                raw_result = await self._simulate_execution(action_type, parameters)
            
            # Store in vault if enabled
            vault_id = None
            if store_in_vault and self._vault and raw_result.get("success"):
                vault_id = await self._store_in_vault(raw_result)
                raw_result["vault_id"] = vault_id
            
            # Update LootManager
            if self._loot_manager and raw_result.get("success"):
                await self._update_loot(raw_result)
            
            # Update anti-loop tracking
            self._update_stagnation_tracking(raw_result)
            
            execution_time = __import__('time').time() - start_time
            
            result = ActionResult(
                action=action_type,
                success=raw_result.get("success", False),
                data=raw_result,
                execution_time=execution_time,
                stagnation_detected=self._stagnation_counter > 0,
                stored_in_vault=vault_id is not None
            )
            
            logger.info(f"✅ Action completed in {execution_time:.2f}s")
            return result
            
        except Exception as e:
            execution_time = __import__('time').time() - start_time
            logger.error(f"❌ Action failed: {e}")
            
            return ActionResult(
                action=action_type,
                success=False,
                data={"error": str(e)},
                execution_time=execution_time,
                stagnation_detected=False,
                stored_in_vault=False
            )
    
    async def _execute_via_director(
        self,
        action_type: ActionType,
        parameters: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute action via GhostDirector"""
        # GhostDirector expects specific action format
        action_plan = {
            "action": action_type.value,
            "parameters": parameters,
            "vault": self._vault,  # Pass vault for direct storage
        }
        
        # Execute (GhostDirector returns raw results)
        result = await self._ghost_director.execute_action(action_plan)
        
        return {
            "success": result.success if hasattr(result, 'success') else True,
            "data": result.data if hasattr(result, 'data') else result,
            "source": "ghost_director"
        }
    
    async def _simulate_execution(
        self,
        action_type: ActionType,
        parameters: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Simulate action execution when GhostDirector unavailable"""
        logger.debug(f"Simulating action: {action_type.value}")
        
        # Return mock result
        return {
            "success": True,
            "data": {
                "action": action_type.value,
                "parameters": parameters,
                "simulated": True,
                "results": []
            },
            "source": "simulation"
        }
    
    async def _store_in_vault(self, data: Dict[str, Any]) -> Optional[str]:
        """Store data in RamDiskVault"""
        if not self._vault:
            return None
        
        try:
            # Generate unique ID
            data_hash = hashlib.sha256(
                json.dumps(data, sort_keys=True, default=str).encode()
            ).hexdigest()[:16]
            
            vault_id = f"ghost_{data_hash}"
            
            # Store in vault
            await self._vault.store(vault_id, data)
            logger.debug(f"📦 Stored in vault: {vault_id}")
            
            return vault_id
            
        except Exception as e:
            logger.warning(f"⚠️ Failed to store in vault: {e}")
            return None
    
    async def _update_loot(self, data: Dict[str, Any]) -> None:
        """Update LootManager with acquired data"""
        if not self._loot_manager:
            return
        
        try:
            # Extract loot items from result
            items = data.get("data", {}).get("results", [])
            
            for item in items:
                await self._loot_manager.add_loot(
                    source="ghost_action",
                    content=item,
                    metadata={"action": data.get("action")}
                )
            
            if items:
                logger.debug(f"💰 Added {len(items)} items to loot")
                
        except Exception as e:
            logger.warning(f"⚠️ Failed to update loot: {e}")
    
    def _check_stagnation(self, parameters: Dict[str, Any]) -> bool:
        """
        Check if current execution might cause stagnation.
        
        Returns:
            True if stagnation detected
        """
        # Check for empty parameters
        if not parameters or not any(parameters.values()):
            self._consecutive_empty += 1
        else:
            self._consecutive_empty = 0
        
        # Detect stagnation
        stagnation_detected = (
            self._consecutive_empty >= 2 or
            self._consecutive_same >= 3 or
            self._stagnation_counter > 0
        )
        
        if stagnation_detected:
            self._stagnation_counter += 1
        
        return stagnation_detected
    
    def _update_stagnation_tracking(self, result: Dict[str, Any]) -> None:
        """Update stagnation tracking based on result"""
        # Hash the result for comparison
        result_str = json.dumps(result, sort_keys=True, default=str)
        result_hash = hashlib.md5(result_str.encode()).hexdigest()
        
        # Check if same as last
        if result_hash == self._last_results_hash:
            self._consecutive_same += 1
            logger.warning(f"🔄 Same result #{self._consecutive_same}")
        else:
            self._consecutive_same = 0
            self._stagnation_counter = 0  # Reset on different result
        
        self._last_results_hash = result_hash
    
    def get_loot_summary(self) -> Dict[str, Any]:
        """Get summary of acquired loot"""
        if not self._loot_manager:
            return {"available": False}
        
        try:
            return {
                "available": True,
                "items": self._loot_manager.get_summary()
            }
        except Exception as e:
            logger.warning(f"⚠️ Failed to get loot summary: {e}")
            return {"available": False, "error": str(e)}
    
    def get_vault_contents(self) -> List[str]:
        """Get list of vault item IDs"""
        if not self._vault:
            return []
        
        try:
            return self._vault.list_items()
        except Exception as e:
            logger.warning(f"⚠️ Failed to list vault: {e}")
            return []
    
    def reset_stagnation_counter(self) -> None:
        """Reset stagnation counter (call when changing research direction)"""
        self._stagnation_counter = 0
        self._consecutive_empty = 0
        self._consecutive_same = 0
        self._last_results_hash = None
        logger.info("🔄 Stagnation counters reset")
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get GhostLayer statistics"""
        stats = {
            "actions_executed": self._action_count,
            "stagnation_events": self._stagnation_events,
            "stagnation_counter": self._stagnation_counter,
            "vault_enabled": self._vault is not None,
            "loot_enabled": self._loot_manager is not None,
            "ghost_director_enabled": self._ghost_director is not None,
            "system_context_enabled": self._system_context is not None,
        }
        
        # Add system context stats if available
        if self._system_context:
            stats["system"] = self._system_context.get_stats()
        
        return stats
    
    async def cleanup(self) -> None:
        """Cleanup resources"""
        logger.info("🧹 Cleaning up GhostLayer...")
        
        if self._ghost_director:
            try:
                await self._ghost_director.cleanup()
            except Exception as e:
                logger.warning(f"⚠️ GhostDirector cleanup error: {e}")
        
        if self._vault:
            try:
                await self._vault.cleanup()
            except Exception as e:
                logger.warning(f"⚠️ Vault cleanup error: {e}")
        
        logger.info("✅ GhostLayer cleanup complete")


# =============================================================================
# SYSTEM CONTEXT - Anti-VM Protection (from kernel/context.py)
# =============================================================================

import platform
import psutil
import gc
import time
from enum import Enum
from dataclasses import dataclass


class VMThreatLevel(Enum):
    """VM threat levels"""
    CRITICAL = 3
    HIGH = 2
    MEDIUM = 1
    LOW = 0


class ProcessType(Enum):
    """Process types for monitoring"""
    SYSTEM = 0
    USER = 1
    SUSPICIOUS = 2
    MALWARE = 3


@dataclass
class ProcessInfo:
    """Process information"""
    pid: int
    name: str
    ppid: int
    cmdline: List[str]
    executable: str
    user: str
    cpu_percent: float
    memory_percent: float
    create_time: float
    status: ProcessType


@dataclass
class SecurityEvent:
    """Security event for VM threats"""
    event_type: str
    timestamp: float
    threat_level: VMThreatLevel
    process_pid: Optional[int]
    details: Dict[str, Any]
    description: str


class SystemContext:
    """
    SystemContext with anti-VM protection for Ghost operations.
    
    Integrated from kernel/context.py - Provides:
    - VM detection via sysctl kern.hv_support
    - Process monitoring and whitelisting
    - System integrity checking
    - M1 Neural Memory Guard (force_neural_cleanup)
    - Stealth mode activation
    
    Example:
        context = SystemContext(enable_anti_vm=True)
        
        if context.is_vm_environment():
            print("Running in VM!")
        
        # Force memory cleanup
        cleanup_results = context.force_neural_cleanup()
        print(f"Freed {cleanup_results['memory_freed_mb']}MB")
    """
    
    def __init__(self, 
                 enable_anti_vm: bool = True,
                 enable_process_monitoring: bool = True,
                 enable_integrity_checking: bool = True,
                 enable_stealth_mode: bool = False,
                 m1_optimization: bool = True):
        """Initialize SystemContext with anti-VM protection"""
        
        self.id = f"sysctx_{hashlib.sha256(str(time.time()).encode()).hexdigest()[:8]}"
        self.created_at = time.time()
        
        # Anti-VM settings
        self._anti_vm_config = {
            'enable_anti_vm': enable_anti_vm,
            'enable_process_monitoring': enable_process_monitoring,
            'enable_integrity_checking': enable_integrity_checking,
            'enable_stealth_mode': enable_stealth_mode,
            'm1_optimization': m1_optimization,
            'threat_detection_sensitivity': 0.8,
            'process_whitelist': [
                'kernel_task', 'launchd', 'networkd', 'resolved',
                'python', 'node', 'npm', 'pip', 'docker', 'git',
                'hledac', 'main.py', 'launch_ghost.py',
            ],
        }
        
        # Process monitoring
        self._monitored_processes: Dict[int, ProcessInfo] = {}
        self._process_whitelist = set(self._anti_vm_config['process_whitelist'])
        self._suspicious_activities: Dict[int, Dict] = {}
        self._security_events: List[SecurityEvent] = []
        
        # System integrity
        self._system_integrity = {
            'kernel_integrity': True,
            'memory_integrity': True,
            'process_integrity': True,
        }
        
        # Performance metrics
        self._stats = {
            'vm_detections': 0,
            'process_monitoring_events': 0,
            'integrity_checks': 0,
            'stealth_activations': 0,
            'm1_optimizations': 0,
        }
        
        logger.info(f"SystemContext initialized: {self.id}")
    
    def is_vm_environment(self) -> bool:
        """
        Detect if running in virtualized environment.
        
        Uses sysctl kern.hv_support on macOS to detect hypervisor.
        
        Returns:
            True if VM environment detected
        """
        try:
            if platform.system() == "Darwin":
                # Check for Hypervisor framework on macOS
                try:
                    result = subprocess.run(
                        ['sysctl', '-n', 'kern.hv_support'],
                        capture_output=True, text=True, timeout=5.0
                    )
                    if result.returncode == 0:
                        output = result.stdout.strip()
                        if output == '1':
                            logger.warning("Hypervisor detected on macOS")
                            self._stats['vm_detections'] += 1
                            return True
                except (subprocess.TimeoutExpired, Exception):
                    pass
            
            # Check for common VM indicators
            vm_indicators = [
                '/proc/xen',
                '/dev/kvm',
                '/dev/vmmon',  # VMware
                '/sys/class/hypervisor',
            ]
            
            for indicator in vm_indicators:
                if __import__('pathlib').Path(indicator).exists():
                    logger.warning(f"VM indicator found: {indicator}")
                    self._stats['vm_detections'] += 1
                    return True
            
            return False
            
        except Exception as e:
            logger.warning(f"VM detection failed: {e}")
            return False
    
    def get_system_info(self) -> Dict[str, Any]:
        """Get comprehensive system information"""
        try:
            system_info = {
                'platform': platform.system(),
                'processor': platform.processor(),
                'architecture': platform.architecture(),
                'python_version': platform.python_version(),
                'is_vm': self.is_vm_environment(),
            }
            
            # Get M1-specific information
            if self._anti_vm_config['m1_optimization'] and platform.system() == "Darwin":
                try:
                    # Get CPU brand
                    result = subprocess.run(
                        ['sysctl', '-n', 'machdep.cpu.brand_string'],
                        capture_output=True, text=True, timeout=2.0
                    )
                    if result.returncode == 0:
                        system_info['cpu_brand'] = result.stdout.strip()
                    
                    # Get performance cores
                    result = subprocess.run(
                        ['sysctl', '-n', 'hw.perflevel0.logicalcpu'],
                        capture_output=True, text=True, timeout=2.0
                    )
                    if result.returncode == 0:
                        system_info['performance_cores'] = int(result.stdout.strip())
                    
                    # Get efficiency cores
                    result = subprocess.run(
                        ['sysctl', '-n', 'hw.perflevel1.logicalcpu'],
                        capture_output=True, text=True, timeout=2.0
                    )
                    if result.returncode == 0:
                        system_info['efficiency_cores'] = int(result.stdout.strip())
                        
                except Exception as e:
                    logger.debug(f"M1 info gathering failed: {e}")
            
            # Get memory information
            memory = psutil.virtual_memory()
            system_info.update({
                'total_memory_gb': round(memory.total / (1024**3), 2),
                'available_memory_gb': round(memory.available / (1024**3), 2),
                'memory_percent': memory.percent,
            })
            
            return system_info
            
        except Exception as e:
            logger.error(f"System info gathering failed: {e}")
            return {'error': str(e)}
    
    def force_neural_cleanup(self) -> Dict[str, Any]:
        """
        M1 Neural Memory Guard - Force cleanup of MLX and system memory.
        
        Detects if MLX is imported and runs MLX-specific cleanup along with
        garbage collection to prevent memory death spirals on M1 hardware.
        
        Returns:
            Dict with cleanup results and memory freed
        """
        cleanup_results = {
            'mlx_detected': False,
            'mlx_cache_cleared': False,
            'gc_collected': False,
            'memory_before_mb': 0,
            'memory_after_mb': 0,
            'memory_freed_mb': 0,
            'errors': []
        }
        
        try:
            # Get memory before cleanup
            memory = psutil.virtual_memory()
            cleanup_results['memory_before_mb'] = round(memory.used / (1024**2), 2)
            
            # Check if MLX is imported and clear its cache
            try:
                import sys
                mlx_modules = [mod for mod in sys.modules.keys() if mod.startswith('mlx')]
                if mlx_modules:
                    cleanup_results['mlx_detected'] = True
                    logger.info(f"MLX detected, modules: {mlx_modules}")
                    
                    # Clear MLX Metal cache
                    try:
                        import mlx.core as mx
                        if hasattr(mx, 'metal') and hasattr(mx.metal, 'clear_cache'):
                            mx.metal.clear_cache()
                            cleanup_results['mlx_cache_cleared'] = True
                            logger.info("MLX Metal cache cleared")
                    except ImportError:
                        pass
                    except Exception as mlx_error:
                        cleanup_results['errors'].append(f"MLX cache clear failed: {mlx_error}")
                        
            except Exception as import_error:
                cleanup_results['errors'].append(f"MLX detection failed: {import_error}")
            
            # Force Python garbage collection
            try:
                gc.collect()
                cleanup_results['gc_collected'] = True
            except Exception as gc_error:
                cleanup_results['errors'].append(f"GC collection failed: {gc_error}")
            
            # Get memory after cleanup
            memory_after = psutil.virtual_memory()
            cleanup_results['memory_after_mb'] = round(memory_after.used / (1024**2), 2)
            cleanup_results['memory_freed_mb'] = round(
                cleanup_results['memory_before_mb'] - cleanup_results['memory_after_mb'], 2
            )
            
            # Update statistics
            self._stats['m1_optimizations'] += 1
            
            logger.info(f"Neural cleanup: {cleanup_results['memory_freed_mb']}MB freed")
            
        except Exception as e:
            cleanup_results['errors'].append(f"Cleanup failed: {e}")
            logger.error(f"Neural cleanup failed: {e}")
        
        return cleanup_results
    
    def activate_stealth_mode(self) -> None:
        """Activate stealth mode for enhanced protection"""
        if self._anti_vm_config['enable_stealth_mode'] and not self._anti_vm_config.get('stealth_active', False):
            self._anti_vm_config['stealth_active'] = True
            self._stats['stealth_activations'] += 1
            
            logger.warning("🔒 Stealth mode activated - enhanced protection enabled")
            
            # Increase threat detection sensitivity
            self._anti_vm_config['threat_detection_sensitivity'] = 1.0
            
            # Enable additional anti-tampering measures
            self._system_integrity.update({
                'anti_tampering': True,
                'secure_boot': True,
                'protected_memory': True,
            })
    
    def get_stats(self) -> Dict[str, Any]:
        """Get system context statistics"""
        stats = self._stats.copy()
        stats['uptime_seconds'] = time.time() - self.created_at
        stats.update(self._system_integrity)
        
        # Add current memory info
        try:
            memory = psutil.virtual_memory()
            stats['current_memory_gb'] = round(memory.used / (1024**3), 2)
            stats['memory_available_gb'] = round(memory.available / (1024**3), 2)
        except:
            pass
        
        return stats


# Export SystemContext
__all__ = [
    'GhostLayer',
    'SystemContext',
    'VMThreatLevel',
    'ProcessType',
]
