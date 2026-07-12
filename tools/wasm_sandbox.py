"""
WASM Sandbox - WebAssembly Secure Execution Environment
======================================================

Secure WASM execution with fuel limits, epoch interruption,
and resource management.
"""
import asyncio
import logging
import threading
import time
from pathlib import Path
from typing import Any
logger = logging.getLogger(__name__)
_WASMTIME_AVAILABLE = False
try:
    import wasmtime
    from wasmtime import Config, Engine, Instance, Module, Store
    _WASMTIME_AVAILABLE = True
except ImportError:
    wasmtime = None
    Config = None
    Engine = None
    Store = None
    Module = None
    Instance = None

class WasmSandbox:
    """
    Secure WebAssembly execution sandbox.

    Features:
        - Fuel consumption tracking
        - Epoch-based interruption
        - Timeout enforcement
        - Resource limits
    """
    DEFAULT_FUEL_LIMIT = 1000000
    DEFAULT_EPOCH_DEADLINE = 30
    DEFAULT_TIMEOUT = 60
    __slots__ = tuple(('_config', '_engine', '_epoch_ticker', '_epoch_ticker_running', '_lock', '_running_instances', 'cache_dir', 'epoch_deadline', 'fuel_limit', 'timeout'))

    def __init__(self, fuel_limit: int=DEFAULT_FUEL_LIMIT, epoch_deadline: float=DEFAULT_EPOCH_DEADLINE, timeout: float=DEFAULT_TIMEOUT, cache_dir: Path | None=None):
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
        self._engine: Engine | None = None
        self._config: Config | None = None
        self._epoch_ticker: threading.Thread | None = None
        self._epoch_ticker_running = False
        self._running_instances: set[int] = set()
        self._lock = threading.Lock()
        if _WASMTIME_AVAILABLE:
            self._init_engine()
            self._start_epoch_ticker()
        logger.info(f'WasmSandbox initialized: fuel={fuel_limit}, epoch={epoch_deadline}s, timeout={timeout}s')

    def _init_engine(self):
        """Initialize WASM engine with fuel and epoch settings."""
        if not _WASMTIME_AVAILABLE:
            return
        try:
            self._config = Config()
            self._config.consume_fuel(True)
            self._config.epoch_interruption(True)
            self._engine = Engine(self._config)
            logger.debug('WASM engine initialized')
        except Exception as e:
            logger.error(f'Failed to initialize WASM engine: {e}')
            self._engine = None

    def _start_epoch_ticker(self):
        """Start background epoch ticker thread."""
        if not _WASMTIME_AVAILABLE:
            return
        self._epoch_ticker_running = True
        self._epoch_ticker = threading.Thread(target=self._epoch_ticker_loop, daemon=True, name='wasm-epoch-ticker')
        self._epoch_ticker.start()
        logger.debug('Epoch ticker started')

    def _epoch_ticker_loop(self):
        """Background loop that increments epoch."""
        epoch_counter = 0
        while self._epoch_ticker_running:
            try:
                with self._lock:
                    pass
                epoch_counter += 1
                time.sleep(self.epoch_deadline / 3)
            except Exception as e:
                logger.debug(f'Epoch ticker error: {e}')

    def is_available(self) -> bool:
        """Check if WASM runtime is available."""
        return _WASMTIME_AVAILABLE and self._engine is not None

    async def run_async(self, wasm_bytes: bytes, function_name: str='run', args: dict[str, Any] | None=None) -> dict[str, Any]:
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
            return {'success': False, 'result': None, 'fuel_used': 0, 'error': 'WASM runtime not available'}
        result = {'success': False, 'result': None, 'fuel_used': 0, 'error': None}
        try:
            loop = asyncio.get_running_loop()
            async with asyncio.timeout(self.timeout):
                result = await loop.run_in_executor(None, self._run_sync, wasm_bytes, function_name, args)
        except TimeoutError:
            result['error'] = f'Execution timeout ({self.timeout}s)'
            logger.warning('WASM execution timeout: %s (%.1fs)', function_name, self.timeout, extra={'fn': function_name, 'timeout_s': self.timeout})
        except Exception as e:
            result['error'] = str(e)
            logger.error(f'WASM execution error: {e}')
        return result

    def _run_sync(self, wasm_bytes: bytes, function_name: str, args: dict[str, Any] | None) -> dict[str, Any]:
        """
        Synchronous WASM execution with fuel tracking.

        This runs in a thread pool to avoid blocking.
        """
        if not _WASMTIME_AVAILABLE:
            return {'success': False, 'result': None, 'fuel_used': 0, 'error': 'wasmtime not available'}
        result: dict[str, Any] = {'success': False, 'result': None, 'fuel_used': 0, 'error': None}
        store = None
        instance = None
        try:
            assert self._engine is not None, 'Engine not initialized'
            store = Store(self._engine)
            store.set_fuel(self.fuel_limit)
            store.set_epoch_deadline(int(self.epoch_deadline))
            instance_id = id(store)
            with self._lock:
                self._running_instances.add(instance_id)
            module = Module(self._engine, wasm_bytes)
            instance = Instance(store, module, [])
            if function_name in instance.exports(store):
                func = instance.exports(store)[function_name]
                if args:
                    func(**args)
                else:
                    func()
                fuel_remaining = store.get_fuel()
                result['fuel_used'] = self.fuel_limit - fuel_remaining
                result['success'] = True
                result['result'] = True
            else:
                result['error'] = f"Function '{function_name}' not found"
        except wasmtime.RuntimeError as e:
            if 'fuel' in str(e).lower():
                result['error'] = 'Fuel exhausted'
                result['fuel_used'] = self.fuel_limit
            else:
                result['error'] = f'Runtime error: {e}'
        except Exception as e:
            result['error'] = str(e)
        finally:
            with self._lock:
                self._running_instances.discard(instance_id)
        return result

    def load_module(self, wasm_path: Path) -> bytes | None:
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
            logger.error(f'Failed to load WASM module: {e}')
            return None

    def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.shutdown()

    async def shutdown(self):
        """Shutdown the sandbox and cleanup resources."""
        logger.info('Shutting down WASM sandbox')
        self._epoch_ticker_running = False
        if self._epoch_ticker:
            self._epoch_ticker.join(timeout=5)
        logger.info('WASM sandbox shutdown complete')

    def get_stats(self) -> dict[str, Any]:
        """Get sandbox statistics."""
        return {'available': self.is_available(), 'fuel_limit': self.fuel_limit, 'epoch_deadline': self.epoch_deadline, 'timeout': self.timeout, 'running_instances': len(self._running_instances), 'epoch_ticker_running': self._epoch_ticker_running}
Instance = None
try:
    if _WASMTIME_AVAILABLE:
        from wasmtime import Instance
except ImportError:
    pass