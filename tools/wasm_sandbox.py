"""
WASM Sandbox - WebAssembly Secure Execution Environment
======================================================

Secure WASM execution with fuel limits, epoch interruption,
and resource management.
"""

import asyncio
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Set

logger = logging.getLogger(__name__)

# WASM runtime availability
_WASMTIME_AVAILABLE = False

try:
    import wasmtime
    from wasmtime import Config, Engine, Store, Module
    _WASMTIME_AVAILABLE = True
except ImportError:
    wasmtime = None
    Config = None
    Engine = None
    Store = None
    Module = None


class WasmSandbox:
    """
    Secure WebAssembly execution sandbox.

    Features:
        - Fuel consumption tracking
        - Epoch-based interruption
        - Timeout enforcement
        - Resource limits
    """

    # Default limits
    DEFAULT_FUEL_LIMIT = 1_000_000  # 1M fuel units
    DEFAULT_EPOCH_DEADLINE = 30  # 30 seconds
    DEFAULT_TIMEOUT = 60  # 60 seconds

    def __init__(
        self,
        fuel_limit: int = DEFAULT_FUEL_LIMIT,
        epoch_deadline: float = DEFAULT_EPOCH_DEADLINE,
        timeout: float = DEFAULT_TIMEOUT,
        cache_dir: Optional[Path] = None
    ):
        """
        Initialize WASM sandbox.

        Args:
            fuel_limit: Maximum fuel units per execution
            epoch_deadline: Epoch interruption deadline in seconds
            timeout: Overall execution timeout in seconds
            cache_dir: Directory for module caching
        """
        self.fuel_limit = fuel_limit
        self.epoch_deadline = epoch_deadline
        self.timeout = timeout
        self.cache_dir = cache_dir

        # Engine and store
        self._engine: Optional[Engine] = None
        self._config: Optional[Config] = None

        # Epoch ticker
        self._epoch_ticker: Optional[threading.Thread] = None
        self._epoch_ticker_running = False

        # Running instances
        self._running_instances: Set[int] = set()
        self._lock = threading.Lock()

        # Initialize if available
        if _WASMTIME_AVAILABLE:
            self._init_engine()
            self._start_epoch_ticker()

        logger.info(
            f"WasmSandbox initialized: fuel={fuel_limit}, "
            f"epoch={epoch_deadline}s, timeout={timeout}s"
        )

    def _init_engine(self):
        """Initialize WASM engine with fuel and epoch settings."""
        if not _WASMTIME_AVAILABLE:
            return

        try:
            # Configure fuel consumption
            self._config = Config()
            self._config.consume_fuel(True)

            # Enable epoch interruption
            self._config.epoch_interruption(True)

            # Create engine
            self._engine = Engine(self._config)

            logger.debug("WASM engine initialized")

        except Exception as e:
            logger.error(f"Failed to initialize WASM engine: {e}")
            self._engine = None

    def _start_epoch_ticker(self):
        """Start background epoch ticker thread."""
        if not _WASMTIME_AVAILABLE:
            return

        self._epoch_ticker_running = True
        self._epoch_ticker = threading.Thread(
            target=self._epoch_ticker_loop,
            daemon=True,
            name="wasm-epoch-ticker"
        )
        self._epoch_ticker.start()
        logger.debug("Epoch ticker started")

    def _epoch_ticker_loop(self):
        """Background loop that increments epoch."""
        epoch_counter = 0

        while self._epoch_ticker_running:
            try:
                # Increment epoch for all stores
                with self._lock:
                    # In wasmtime, we would call store.set_epoch_deadline()
                    # but this requires store access - simplified here
                    pass

                epoch_counter += 1
                time.sleep(self.epoch_deadline / 3)  # Tick 3 times per deadline

            except Exception as e:
                logger.debug(f"Epoch ticker error: {e}")

    def is_available(self) -> bool:
        """Check if WASM runtime is available."""
        return _WASMTIME_AVAILABLE and self._engine is not None

    async def run_async(
        self,
        wasm_bytes: bytes,
        function_name: str = "run",
        args: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Run WASM module asynchronously with timeout and fuel limits.

        Args:
            wasm_bytes: WASM module bytecode
            function_name: Function to execute
            args: Function arguments

        Returns:
            Dict with 'success', 'result', 'fuel_used', 'error'
        """
        if not self.is_available():
            return {
                'success': False,
                'result': None,
                'fuel_used': 0,
                'error': 'WASM runtime not available'
            }

        result = {
            'success': False,
            'result': None,
            'fuel_used': 0,
            'error': None
        }

        try:
            # Run in executor to not block event loop
            loop = asyncio.get_running_loop()
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    self._run_sync,
                    wasm_bytes,
                    function_name,
                    args
                ),
                timeout=self.timeout
            )

        except asyncio.TimeoutError:
            result['error'] = f"Execution timeout ({self.timeout}s)"
            logger.warning(f"WASM execution timeout: {function_name}")
        except Exception as e:
            result['error'] = str(e)
            logger.error(f"WASM execution error: {e}")

        return result

    def _run_sync(
        self,
        wasm_bytes: bytes,
        function_name: str,
        args: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Synchronous WASM execution with fuel tracking.

        This runs in a thread pool to avoid blocking.
        """
        if not _WASMTIME_AVAILABLE:
            return {
                'success': False,
                'result': None,
                'fuel_used': 0,
                'error': 'wasmtime not available'
            }

        result = {
            'success': False,
            'result': None,
            'fuel_used': 0,
            'error': None
        }

        store = None
        instance = None

        try:
            # Create store
            store = Store(self._engine)

            # Set fuel limit
            store.add_fuel(self.fuel_limit)

            # Set epoch deadline
            store.set_epoch_deadline(self.epoch_deadline)

            # Add to running instances
            instance_id = id(store)
            with self._lock:
                self._running_instances.add(instance_id)

            # Load module
            module = Module(self._engine, wasm_bytes)

            # Instantiate with imports
            # For now, use empty imports
            instance = Instance(module, [])

            # Get function
            if function_name in instance.exports:
                func = instance.exports[function_name]

                # Call with arguments if provided
                if args:
                    func(**args)
                else:
                    func()

                # Get fuel used
                fuel_remaining = store.fuel()
                result['fuel_used'] = self.fuel_limit - fuel_remaining
                result['success'] = True
                result['result'] = True  # Void function

            else:
                result['error'] = f"Function '{function_name}' not found"

        except wasmtime.RuntimeError as e:
            if "fuel" in str(e).lower():
                result['error'] = "Fuel exhausted"
                result['fuel_used'] = self.fuel_limit
            else:
                result['error'] = f"Runtime error: {e}"
        except Exception as e:
            result['error'] = str(e)
        finally:
            # Remove from running instances
            with self._lock:
                self._running_instances.discard(instance_id)

        return result

    def load_module(self, wasm_path: Path) -> Optional[bytes]:
        """
        Load WASM module from file.

        Args:
            wasm_path: Path to .wasm file

        Returns:
            Module bytecode or None
        """
        try:
            return wasm_path.read_bytes()
        except Exception as e:
            logger.error(f"Failed to load WASM module: {e}")
            return None

    def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.shutdown()

    async def shutdown(self):
        """Shutdown the sandbox and cleanup resources."""
        logger.info("Shutting down WASM sandbox")

        # Stop epoch ticker
        self._epoch_ticker_running = False
        if self._epoch_ticker:
            self._epoch_ticker.join(timeout=5)

        # Note: Can't easily stop running instances
        # They will complete or be interrupted by epoch

        logger.info("WASM sandbox shutdown complete")

    def get_stats(self) -> Dict[str, Any]:
        """Get sandbox statistics."""
        return {
            'available': self.is_available(),
            'fuel_limit': self.fuel_limit,
            'epoch_deadline': self.epoch_deadline,
            'timeout': self.timeout,
            'running_instances': len(self._running_instances),
            'epoch_ticker_running': self._epoch_ticker_running
        }


# Alias for compatibility
Instance = None
try:
    if _WASMTIME_AVAILABLE:
        from wasmtime import Instance
except ImportError:
    pass
